"""
app.py — Vigilant Video — Main Flask Application
"""
import os
import uuid
import json
from datetime import datetime
from functools import wraps

import boto3
from botocore.client import Config  # Added for R2 signature version
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

def create_app(env=None):
    app = Flask(__name__)
    env = env or os.environ.get('FLASK_ENV', 'default')
    app.config.from_object(config[env])
    db.init_app(app)
    with app.app_context():
        db.create_all()   # Create tables on startup if they don't exist
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
    limit = UsageLimit.query.get(user.subscription.plan)
    if not limit: return False, 'Plan not found.'
    if limit.max_videos_per_month == -1: return True, 'ok'
    used = user.subscription.monthly_uploads_used if user.subscription else 0
    if used >= limit.max_videos_per_month:
        return False, 'Monthly upload limit reached.'
    return True, 'ok'

# ══════════════════════════════════════════════════════════════════════
# DASHBOARD / FRONTEND ROUTES
# ══════════════════════════════════════════════════════════════════════
@app.route('/')
def home(): return render_template('index.html')

@app.route('/login')
def login(): return redirect(url_for('dashboard')) if session.get('user_id') else render_template('login.html')

@app.route('/register')
def register(): return redirect(url_for('dashboard')) if session.get('user_id') else render_template('login.html')

@app.route('/dashboard')
def dashboard(): return redirect(url_for('login')) if not session.get('user_id') else render_template('userDashboard.html')

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
    db.session.flush()  # get user.id before commit
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
# API — DASHBOARD DATA (single endpoint, everything in one call)
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/dashboard', methods=['GET'])
@login_required_api
def api_dashboard():
    user = get_current_user()

    # ── All jobs for this user (newest first) ──────────────────────
    jobs = ProtectionJob.query.filter_by(user_id=user.id)\
               .order_by(ProtectionJob.created_at.desc()).all()

    # ── Stats ──────────────────────────────────────────────────────
    videos_protected  = sum(1 for j in jobs if j.status == 'done')
    processing_now    = sum(1 for j in jobs if j.status in ('pending', 'processing'))
    storage_used      = sum(j.original_size_bytes or 0 for j in jobs)

    # Storage limit from UsageLimit table (default 10 GB for free)
    limit_row = UsageLimit.query.get(user.plan_tier)
    storage_limit = (limit_row.max_file_size_bytes
                     if limit_row and limit_row.max_file_size_bytes != -1
                     else 10 * 1024 ** 3)  # 10 GB default
    uploads_limit = (limit_row.max_videos_per_month
                     if limit_row else 3)
    uploads_used  = user.subscription.monthly_uploads_used if user.subscription else 0

    # ── Video rows for the library table ──────────────────────────
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
            'status': 'done' if j.status == 'done' else j.status,  # done→download ready
        }

    videos = [_job_to_video(j) for j in jobs]

    # ── Activity feed (recent completions + downloads) ─────────────
    feed = []
    for j in jobs[:10]:  # cap at 10 entries
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
            'scrape_attempts_blocked': 0,  # placeholder — no detection table yet
            'storage_used_bytes':      storage_used,
            'storage_limit_bytes':     storage_limit,
            'uploads_used':            uploads_used,
            'uploads_limit':           uploads_limit,
        },
        'videos': videos,
        'feed':   feed,
    }), 200

def _time_ago(dt):
    """Return a human-readable 'X ago' string for a datetime."""
    if not dt:
        return ''
    diff = datetime.utcnow() - dt
    secs = int(diff.total_seconds())
    if secs < 60:    return 'Just now'
    if secs < 3600:  return f'{secs // 60} min ago'
    if secs < 86400: return f'{secs // 3600} hr ago'
    return f'{secs // 86400} days ago'

# ══════════════════════════════════════════════════════════════════════
# API — ASYNC VIDEO UPLOAD (REDIS QUEUE)
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/upload', methods=['POST'])
@login_required_api
def api_upload():
    user = get_current_user()
    allowed, msg = check_upload_limit(user)
    if not allowed: return jsonify({'message': msg}), 429

    if 'video' not in request.files: return jsonify({'message': 'No file uploaded.'}), 400
    f = request.files['video']
    name = (f.filename or '').strip()
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    
    if ext not in app.config.get('ALLOWED_EXTENSIONS', {'mp4', 'mov', 'avi', 'mkv'}):
        return jsonify({'message': f'Unsupported format: .{ext}'}), 415

    job_id = str(uuid.uuid4())
    upload_dir = app.config.get('UPLOAD_FOLDER', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    local_path = os.path.join(upload_dir, f'{job_id}.{ext}')
    f.save(local_path)
    file_size = os.path.getsize(local_path)

    # 1. Upload Raw File to Cloudflare R2
    s3 = get_s3_client()
    bucket = current_app.config['R2_BUCKET_NAME']
    raw_r2_key = f"raw/{job_id}.{ext}"
    protected_r2_key = f"protected/{job_id}.{ext}"
    
    s3.upload_file(local_path, bucket, raw_r2_key)
    os.remove(local_path) # Scorched earth: delete local file immediately

    # 2. Save Pending Job in Database
    job = ProtectionJob(
        job_id=job_id,
        user_id=user.id,
        status='pending',
        original_filename=name,
        original_size_bytes=file_size,
        input_path=raw_r2_key, 
        output_path=protected_r2_key
    )
    db.session.add(job)
    user.subscription.monthly_uploads_used += 1
    db.session.commit()

    # 3. Push to Upstash Redis with Failure Handling
    try:
        r = get_redis_client()
        task_data = {
            "task_id": job_id,
            "raw_object": raw_r2_key,
            "protected_object": protected_r2_key,
            #"webhook_url": f"{request.url_root}api/internal/webhook",
            # Replace 'a1b2-c3d4' with your actual Ngrok forwarding address
            "webhook_url": " https://4cb5-61-12-82-214.ngrok-free.app/api/internal/webhook",
            "webhook_secret": current_app.config['WEBHOOK_SECRET']
        }
        r.rpush("vigilant_video_queue", json.dumps(task_data))
        r.set(f"status:{job_id}", "queued")
    except Exception as e:
        app.logger.error(f"Redis dispatch failed: {e}")
        # Rollback: delete from R2 and DB so we don't have orphaned files/jobs
        s3.delete_object(Bucket=bucket, Key=raw_r2_key)
        db.session.delete(job)
        db.session.commit()
        return jsonify({'message': 'Our processing queue is temporarily unavailable. Please try again in a few minutes.'}), 503

    return jsonify({
        'job_id': job_id,
        'status': 'pending',
        'message': 'Upload received. Dispatched to GPU worker queue.',
    }), 202

# ══════════════════════════════════════════════════════════════════════
# API — WEBHOOK (CALLED BY EXTERNAL WORKERS)
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/internal/webhook', methods=['POST'])
def webhook_job_complete():
    data = request.get_json(silent=True) or {}
    
    # Verify the secret to ensure users can't fake a completed job
    if data.get('webhook_secret') != current_app.config['WEBHOOK_SECRET']:
        return jsonify({"error": "Unauthorized"}), 403

    job_id = data.get('task_id')
    status = data.get('status') # 'done' or 'error'
    metrics = data.get('metrics', {})

    job = ProtectionJob.query.filter_by(job_id=job_id).first()
    if not job:
        return jsonify({"error": "Job not found"}), 404

    job.status = status
    job.completed_at = datetime.utcnow()
    if status == 'error':
        job.error_message = data.get('error_message', 'Unknown GPU error')
    else:
        # Save the processing metrics to the DB
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
    return jsonify({'job_id': job.job_id, 'status': job.status, 'error': job.error_message}), 200

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

    # Generate Secure, Expiring Cloudflare R2 Download Link
    s3 = get_s3_client()
    try:
        presigned_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': current_app.config['R2_BUCKET_NAME'], 'Key': job.output_path},
            ExpiresIn=3600 # Link expires in 1 hour
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
        # Delete protected output
        if job.output_path:
            s3.delete_object(Bucket=bucket, Key=job.output_path)
            
        # Delete raw input (prevents storage leaks from unprocessed or failed jobs)
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
    os.makedirs(app.config.get('UPLOAD_FOLDER', 'uploads'), exist_ok=True)
    app.run(debug=True)