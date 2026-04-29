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
def register(): return redirect(url_for('dashboard')) if session.get('user_id') else render_template('register.html')

@app.route('/dashboard')
def dashboard(): return redirect(url_for('login')) if not session.get('user_id') else render_template('userDashboard.html')

# ══════════════════════════════════════════════════════════════════════
# API — DASHBOARD DATA (REAL DATA FROM DB)
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/dashboard', methods=['GET'])
@login_required_api
def api_dashboard():
    user = get_current_user()
    
    # Fetch all user's jobs
    jobs = ProtectionJob.query.filter_by(user_id=user.id).order_by(ProtectionJob.created_at.desc()).all()
    
    # Build video list for frontend
    videos = []
    for job in jobs:
        videos.append({
            'name': job.original_filename.rsplit('.', 1)[0] if '.' in job.original_filename else job.original_filename,
            'ext': job.original_filename.rsplit('.', 1)[-1].lower() if '.' in job.original_filename else 'mp4',
            'date': job.created_at.strftime('%b %d, %Y') if job.created_at else 'Unknown',
            'size': format_bytes(job.original_size_bytes) if job.original_size_bytes else '0 B',
            'status': job.status,
            'progress': 100 if job.status in ['done', 'protected'] else (50 if job.status == 'processing' else 0),
        })
    
    # Calculate stats
    protected_count = len([v for v in videos if v['status'] in ['done', 'protected']])
    processing_count = len([v for v in videos if v['status'] in ['processing', 'pending', 'queued']])
    
    # Get download logs for activity feed
    download_logs = DownloadLog.query.filter_by(user_id=user.id).order_by(DownloadLog.timestamp.desc()).limit(5).all()
    
    # Get completed jobs for activity feed
    completed_jobs = ProtectionJob.query.filter_by(user_id=user.id, status='done').order_by(ProtectionJob.completed_at.desc()).limit(5).all()
    
    # Build activity feed
    feed = []
    
    # Add completed jobs to feed
    for job in completed_jobs:
        if job.completed_at:
            time_ago = get_time_ago(job.completed_at)
            feed.append({
                'dot': 'green',
                'text': f"<strong>{job.original_filename}</strong> successfully protected",
                'time': time_ago
            })
    
    # Add download logs to feed
    for log in download_logs:
        time_ago = get_time_ago(log.downloaded_at) if hasattr(log, 'downloaded_at') else 'Unknown'
        job = ProtectionJob.query.get(log.job_id)
        filename = job.original_filename if job else 'Video'
        feed.append({
            'dot': 'cyan',
            'text': f"<strong>{filename}</strong> downloaded",
            'time': time_ago
        })
    
    # Calculate storage
    total_storage_bytes = sum(job.original_size_bytes or 0 for job in jobs)
    storage_limit_bytes = 10 * 1024 * 1024 * 1024  # 10 GB default
    
    return jsonify({
        'user': {
            'username': user.username or 'User',
            'initials': user.username[0].upper() if user.username else 'U'
        },
        'stats': {
            'videos_protected': protected_count,
            'processing_now': processing_count,
            'scrape_attempts_blocked': 0,  # TODO: Implement scrape tracking
            'storage_used_bytes': total_storage_bytes,
            'storage_limit_bytes': storage_limit_bytes,
        },
        'videos': videos,
        'feed': feed[:10]  # Limit feed to 10 items
    }), 200

# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════
def format_bytes(bytes_val):
    if not bytes_val or bytes_val == 0:
        return '0 B'
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    i = 0
    while bytes_val >= 1024 and i < len(units) - 1:
        bytes_val /= 1024.0
        i += 1
    return f'{bytes_val:.1f} {units[i]}'

def get_time_ago(dt):
    if not dt:
        return 'Unknown'
    from datetime import datetime as dt_class
    now = dt_class.utcnow()
    diff = now - dt
    
    seconds = diff.total_seconds()
    if seconds < 60:
        return 'Just now'
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f'{minutes}m ago'
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f'{hours}h ago'
    else:
        days = int(seconds / 86400)
        return f'{days}d ago'

# ══════════════════════════════════════════════════════════════════════
# API — ASYNC VIDEO UPLOAD (REDIS QUEUE)
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    
    if not username or not password:
        return jsonify({'message': 'Username and password required.'}), 400
    
    if User.query.filter_by(username=username).first():
        return jsonify({'message': 'Username already exists.'}), 409
    
    user = User(username=username, password_hash=generate_password_hash(password))
    subscription = Subscription(plan='free')
    user.subscription = subscription
    
    db.session.add(user)
    db.session.add(subscription)
    db.session.commit()
    
    session['user_id'] = user.id
    return jsonify({'message': 'Account created successfully.', 'user_id': user.id}), 201

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    
    if not username or not password:
        return jsonify({'message': 'Username and password required.'}), 400
    
    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'message': 'Invalid credentials.'}), 401
    
    session['user_id'] = user.id
    return jsonify({'message': 'Login successful.', 'user_id': user.id}), 200

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'message': 'Logout successful.'}), 200

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
            "webhook_url": f"{request.url_root}api/internal/webhook",
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