"""
app.py — Vigilant Video — Main Flask Application
"""
import os
import uuid
import json
from datetime import datetime, timedelta
from functools import wraps

import boto3
from botocore.client import Config  # Required for R2 signature version
import redis
from flask import (
    Flask, render_template, request, current_app,
    jsonify, session, redirect, url_for
)
from werkzeug.security import generate_password_hash, check_password_hash

from config import config
from models import (
    db, User, ProtectionJob, Subscription,
    DownloadLog, UsageLimit, WatermarkActivation
)

def _seed_usage_limits():
    """
    Insert default plan rows into usage_limits if they don't exist yet.
    Safe to call on every startup — skips rows that are already present.
    This ensures the app works even if init_db.py was never run manually.
    """
    plans = [
        {
            'plan':                      'free',
            'max_videos_per_month':      3,
            'max_video_length_secs':     120,            # 2 minutes
            'max_file_size_bytes':       209_715_200,    # 200 MB
            'adversarial_enabled':       False,
            'freq_perturbation_enabled': False,
            'processing_priority':       'low',
        },
        {
            'plan':                      'pro',
            'max_videos_per_month':      50,
            'max_video_length_secs':     1_800,           # 30 minutes
            'max_file_size_bytes':       1_073_741_824,   # 1 GB
            'adversarial_enabled':       True,
            'freq_perturbation_enabled': True,
            'processing_priority':       'medium',
        },
        {
            'plan':                      'business',
            'max_videos_per_month':      -1,              # unlimited
            'max_video_length_secs':     7_200,           # 2 hours
            'max_file_size_bytes':       5_368_709_120,   # 5 GB
            'adversarial_enabled':       True,
            'freq_perturbation_enabled': True,
            'processing_priority':       'high',
        },
    ]
    inserted = 0
    for p in plans:
        if not UsageLimit.query.get(p['plan']):
            db.session.add(UsageLimit(**p))
            inserted += 1
    if inserted:
        db.session.commit()


def create_app(env=None):
    app = Flask(__name__)
    env = env or os.environ.get('FLASK_ENV', 'default')
    app.config.from_object(config[env])
    db.init_app(app)
    with app.app_context():
        db.create_all()          # Create tables on startup if they don't exist
        _seed_usage_limits()     # Ensure plan rows exist — safe if already seeded
    return app

app = create_app()

# ══════════════════════════════════════════════════════════════════════
# CLOUD PROVIDER HELPERS
# ══════════════════════════════════════════════════════════════════════
def get_s3_client():
    return boto3.client(
        's3',
        endpoint_url=f"https://{current_app.config['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=current_app.config['R2_ACCESS_KEY'],
        aws_secret_access_key=current_app.config['R2_SECRET_KEY'],
        config=Config(signature_version='s3v4')  # Critical for R2 presigned URLs
    )

def get_redis_client():
    return redis.Redis.from_url(current_app.config['REDIS_URL'], decode_responses=True)

# ══════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════════════════════════════════════
def get_current_user():
    uid = session.get('user_id')
    return User.query.get(uid) if uid else None

def login_required_api(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({'message': 'Authentication required.'}), 401
        return f(*args, **kwargs)
    return decorated

def check_upload_limit(user):
    if not user.subscription:
        return False, 'No active subscription found.'
    sub = user.subscription

    # ── Lazy monthly reset ──────────────────────────────────────────
    now = datetime.utcnow()
    period_start = sub.started_at or now
    if now.year > period_start.year or now.month > period_start.month:
        sub.monthly_uploads_used = 0
        sub.started_at = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        db.session.commit()

    limit = UsageLimit.query.get(sub.plan)
    if not limit: return False, 'Plan not found.'
    if limit.max_videos_per_month == -1: return True, 'ok'
    if sub.monthly_uploads_used >= limit.max_videos_per_month:
        return False, 'Monthly upload limit reached.'
    return True, 'ok'

# ══════════════════════════════════════════════════════════════════════
# DASHBOARD / FRONTEND ROUTES
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login')
def login():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/register')
def register():
    if session.get('user_id'):
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    if not session.get('user_id'):
        return redirect(url_for('login'))
    return render_template('userDashboard.html')

# ══════════════════════════════════════════════════════════════════════
# API — AUTH (LOGIN / REGISTER / LOGOUT / ME)
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data     = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return jsonify({'message': 'Username and password are required.'}), 400
    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'message': 'Invalid username or password.'}), 401
    session['user_id'] = user.id
    return jsonify({'message': 'Logged in.', 'user': user.to_dict()}), 200

@app.route('/api/auth/register', methods=['POST'])
def api_register():
    data     = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return jsonify({'message': 'Username and password are required.'}), 400
    if len(password) < 6:
        return jsonify({'message': 'Password must be at least 6 characters.'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'message': 'Username already taken.'}), 409
    user = User(username=username, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.flush()
    sub  = Subscription(user_id=user.id, plan='free')
    db.session.add(sub)
    db.session.commit()
    session['user_id'] = user.id
    return jsonify({'message': 'Account created.', 'user': user.to_dict()}), 201

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'message': 'Logged out.'}), 200

@app.route('/api/me', methods=['GET'])
def api_me():
    user = get_current_user()
    if not user:
        return jsonify({'message': 'Not authenticated.'}), 401
    return jsonify({'user': user.to_dict()}), 200

# ══════════════════════════════════════════════════════════════════════
# API — DASHBOARD DATA
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/dashboard', methods=['GET'])
@login_required_api
def api_dashboard():
    user = get_current_user()

    jobs = ProtectionJob.query.filter_by(user_id=user.id)\
               .order_by(ProtectionJob.created_at.desc()).all()

    videos_protected  = sum(1 for j in jobs if j.status == 'done')
    processing_now    = sum(1 for j in jobs if j.status in ('pending', 'processing'))
    storage_used      = sum(j.original_size_bytes or 0 for j in jobs)

    limit_row = UsageLimit.query.get(user.plan_tier)
    storage_limit = (limit_row.max_file_size_bytes
                     if limit_row and limit_row.max_file_size_bytes != -1
                     else 10 * 1024 ** 3)
    uploads_limit = (limit_row.max_videos_per_month if limit_row else 3)
    uploads_used  = user.subscription.monthly_uploads_used if user.subscription else 0

    _s3 = get_s3_client()
    _bucket = current_app.config['R2_BUCKET_NAME']
    _changed = False
    for j in jobs:
        if (
            j.status == 'done'
            and j.completed_at
            and datetime.utcnow() - j.completed_at > timedelta(days=3)
        ):
            try:
                if j.output_path:
                    _s3.delete_object(Bucket=_bucket, Key=j.output_path)
            except Exception as _e:
                app.logger.warning(f"Expiry: failed to delete {j.output_path} from R2: {_e}")
            j.status = 'expired'
            _changed = True
    if _changed:
        db.session.commit()

    def _job_to_video(j):
        fname = j.original_filename or ''
        parts = fname.rsplit('.', 1)
        name  = parts[0] if len(parts) == 2 else fname
        ext   = parts[1].lower() if len(parts) == 2 else ''
        return {
            'job_id': j.job_id,
            'name':   name,
            'ext':    ext,
            'date':   j.created_at.strftime('%d %b %Y') if hasattr(j.created_at, 'strftime') else str(j.created_at)[:10],
            'size':   j.size_display(),
            'status': j.status,
        }

    videos = [_job_to_video(j) for j in jobs]

    feed = []
    for j in jobs[:10]:
        if j.status == 'done' and j.completed_at:
            feed.append({
                'dot':  'green',
                'text': f'<strong>{j.original_filename}</strong> successfully protected',
                'time': _time_ago(j.completed_at),
            })
        elif j.status == 'error':
            feed.append({
                'dot':  'red',
                'text': f'<strong>{j.original_filename}</strong> protection failed',
                'time': _time_ago(j.created_at),
            })
        elif j.status in ('pending', 'processing'):
            feed.append({
                'dot':  'amber',
                'text': f'<strong>{j.original_filename}</strong> is being processed',
                'time': _time_ago(j.created_at),
            })

    return jsonify({
        'user': user.to_dict(),
        'stats': {
            'videos_protected':        videos_protected,
            'processing_now':          processing_now,
            'scrape_attempts_blocked': 0,
            'storage_used_bytes':      storage_used,
            'storage_limit_bytes':     storage_limit,
            'uploads_used':            uploads_used,
            'uploads_limit':           uploads_limit,
        },
        'videos': videos,
        'feed':   feed,
    }), 200

def _time_ago(dt):
    if not dt:
        return ''
    diff = datetime.utcnow() - dt
    secs = int(diff.total_seconds())
    if secs < 60:    return 'Just now'
    if secs < 3600:  return f'{secs // 60} min ago'
    if secs < 86400: return f'{secs // 3600} hr ago'
    return f'{secs // 86400} days ago'


# ══════════════════════════════════════════════════════════════════════
# API — STEP 1 OF 2: GENERATE PRESIGNED POST URL
# The browser calls this FIRST to get a short-lived upload token.
# Render only does auth + DB work — no file bytes touch this server.
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/upload/presign', methods=['POST'])
@login_required_api
def api_presign_upload():
    user = get_current_user()
    allowed, msg = check_upload_limit(user)
    if not allowed:
        return jsonify({'message': msg}), 429

    data = request.get_json(silent=True) or {}
    filename = (data.get('filename') or '').strip()
    filesize = data.get('filesize', 0)

    if not filename:
        return jsonify({'message': 'filename is required.'}), 400

    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in app.config.get('ALLOWED_EXTENSIONS', {'mp4', 'mov', 'avi', 'mkv'}):
        return jsonify({'message': f'Unsupported format: .{ext}'}), 415

    # Check per-plan file size limit
    limit_row = UsageLimit.query.get(user.plan_tier)
    if limit_row and limit_row.max_file_size_bytes != -1:
        if filesize > limit_row.max_file_size_bytes:
            return jsonify({'message': 'File exceeds your plan\'s size limit.'}), 413

    job_id = str(uuid.uuid4())
    raw_r2_key       = f"raw/{job_id}.{ext}"
    protected_r2_key = f"protected/{job_id}.{ext}"

    # Generate a presigned POST — valid for 15 minutes.
    # The browser will POST the file directly to R2 using these credentials.
    s3 = get_s3_client()
    bucket = current_app.config['R2_BUCKET_NAME']

    try:
        presigned = s3.generate_presigned_post(
            Bucket=bucket,
            Key=raw_r2_key,
            Fields={
                'Content-Type': f'video/{ext}',
            },
            Conditions=[
                ['content-length-range', 1, app.config.get('MAX_UPLOAD_BYTES', 5 * 1024 ** 3)],
                {'Content-Type': f'video/{ext}'},
            ],
            ExpiresIn=900  # 15 minutes
        )
    except Exception as e:
        app.logger.error(f"Failed to generate presigned POST: {e}")
        return jsonify({'message': 'Could not generate upload URL. Please try again.'}), 500

    # Create a 'pending_presign' job so we can validate the confirm call later.
    job = ProtectionJob(
        job_id=job_id,
        user_id=user.id,
        status='pending_presign',       # special transient status
        original_filename=filename,
        original_size_bytes=filesize,
        input_path=raw_r2_key,
        output_path=protected_r2_key
    )
    db.session.add(job)
    # Increment quota now — rolled back in /confirm if upload never completes.
    user.subscription.monthly_uploads_used += 1
    db.session.commit()

    return jsonify({
        'job_id':     job_id,
        'upload_url': presigned['url'],    # R2 endpoint
        'fields':     presigned['fields'], # Hidden form fields the browser must POST
    }), 200


# ══════════════════════════════════════════════════════════════════════
# API — STEP 2 OF 2: CONFIRM UPLOAD & DISPATCH TO WORKER
# Browser calls this AFTER the direct-to-R2 upload succeeds.
# Render only does DB work + Redis push — still no file bytes here.
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/upload/confirm', methods=['POST'])
@login_required_api
def api_confirm_upload():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    job_id = (data.get('job_id') or '').strip()

    if not job_id:
        return jsonify({'message': 'job_id is required.'}), 400

    job = ProtectionJob.query.filter_by(job_id=job_id, user_id=user.id).first()
    if not job:
        return jsonify({'message': 'Job not found.'}), 404
    if job.status != 'pending_presign':
        return jsonify({'message': 'Job already confirmed or invalid state.'}), 409

    # Optionally verify the object actually landed in R2 before queuing.
    # This prevents someone calling /confirm without actually uploading.
    s3 = get_s3_client()
    bucket = current_app.config['R2_BUCKET_NAME']
    try:
        s3.head_object(Bucket=bucket, Key=job.input_path)
    except Exception:
        # Object not found — roll back the quota increment and delete the phantom job.
        user.subscription.monthly_uploads_used = max(0, user.subscription.monthly_uploads_used - 1)
        db.session.delete(job)
        db.session.commit()
        return jsonify({'message': 'Upload not found in storage. Please try again.'}), 404

    # Transition to proper 'pending' and push to the GPU worker queue.
    job.status = 'pending'
    db.session.commit()

    try:
        r = get_redis_client()
        task_data = {
            "task_id":        job_id,
            "raw_object":     job.input_path,
            "protected_object": job.output_path,
            "webhook_url":    current_app.config.get('WEBHOOK_BASE_URL', '') + '/api/internal/webhook',
            "webhook_secret": current_app.config['WEBHOOK_SECRET']
        }
        r.rpush("vigilant_video_queue", json.dumps(task_data))
        r.set(f"status:{job_id}", "queued")
    except Exception as e:
        app.logger.error(f"Redis dispatch failed: {e}")
        # Don't delete the job — an admin can re-queue manually.
        # But do signal the client so it can retry.
        return jsonify({'message': 'Processing queue temporarily unavailable. Please try again in a few minutes.'}), 503

    return jsonify({
        'job_id':  job_id,
        'status':  'pending',
        'message': 'Upload confirmed. Dispatched to GPU worker queue.',
    }), 202


# ══════════════════════════════════════════════════════════════════════
# API — WEBHOOK (CALLED BY EXTERNAL WORKERS)
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/internal/webhook', methods=['POST'])
def webhook_job_complete():
    data = request.get_json(silent=True) or {}

    if data.get('webhook_secret') != current_app.config['WEBHOOK_SECRET']:
        return jsonify({"error": "Unauthorized"}), 403

    job_id  = data.get('task_id')
    status  = data.get('status')   # 'done' or 'error'
    metrics = data.get('metrics', {})

    job = ProtectionJob.query.filter_by(job_id=job_id).first()
    if not job:
        return jsonify({"error": "Job not found"}), 404

    job.status       = status
    job.completed_at = datetime.utcnow()
    if status == 'error':
        job.error_message = data.get('error_message', 'Unknown GPU error')
    else:
        activation = WatermarkActivation.from_result(job.id, metrics)
        db.session.add(activation)

    db.session.commit()
    return jsonify({"message": "Database updated successfully"}), 200

# ══════════════════════════════════════════════════════════════════════
# API — STATUS POLLING
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/status/<job_id>', methods=['GET'])
@login_required_api
def api_status(job_id):
    user = get_current_user()
    job  = ProtectionJob.query.filter_by(job_id=job_id, user_id=user.id).first()
    if not job: return jsonify({'message': 'Job not found.'}), 404

    try:
        r = get_redis_client()
        progress_str = r.get(f"progress:{job_id}")
        progress = float(progress_str) if progress_str else (100.0 if job.status == 'done' else 0.0)
    except Exception:
        progress = 100.0 if job.status == 'done' else 0.0

    return jsonify({'job_id': job.job_id, 'status': job.status, 'progress': progress, 'error': job.error_message}), 200

# ══════════════════════════════════════════════════════════════════════
# API — SECURE DOWNLOAD (PRE-SIGNED URLS) & DELETE
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/download/<job_id>', methods=['GET'])
@login_required_api
def api_download(job_id):
    user = get_current_user()
    job  = ProtectionJob.query.filter_by(job_id=job_id, user_id=user.id).first()

    if not job or job.status != 'done':
        return jsonify({'message': 'Video not ready.'}), 404

    s3 = get_s3_client()
    try:
        presigned_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': current_app.config['R2_BUCKET_NAME'], 'Key': job.output_path},
            ExpiresIn=3600
        )
    except Exception as e:
        app.logger.error(f"Failed to generate download link: {e}")
        return jsonify({'message': 'Failed to generate download link.'}), 500

    log = DownloadLog(job_id=job.id, user_id=user.id, ip_address=request.remote_addr)
    db.session.add(log)
    db.session.commit()

    return jsonify({
        'download_url': presigned_url,
        'filename': f"Protected_{job.original_filename}"
    }), 200

@app.route('/api/video/<job_id>', methods=['DELETE'])
@login_required_api
def api_delete_video(job_id):
    user = get_current_user()
    job  = ProtectionJob.query.filter_by(job_id=job_id, user_id=user.id).first()
    if not job: return jsonify({'message': 'Job not found.'}), 404

    s3 = get_s3_client()
    bucket = current_app.config['R2_BUCKET_NAME']

    try:
        if job.output_path:
            s3.delete_object(Bucket=bucket, Key=job.output_path)
        if job.input_path:
            s3.delete_object(Bucket=bucket, Key=job.input_path)
    except Exception as e:
        app.logger.warning(f"Failed to delete {job.output_path} or {job.input_path} from R2: {e}")

    DownloadLog.query.filter_by(job_id=job.id).delete()
    WatermarkActivation.query.filter_by(job_id=job.id).delete()
    db.session.delete(job)
    db.session.commit()

    return jsonify({'message': 'Video deleted successfully.'}), 200

if __name__ == '__main__':
    app.run(debug=True)