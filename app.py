"""
app.py — Vigilant Video — Main Flask Application
Database:      SQLite (dev) → Supabase PostgreSQL (prod)
File Storage:  Cloudinary — ONLY shielded videos are stored in the cloud
"""

import os
import uuid
import threading
from datetime import datetime
from functools import wraps

import cloudinary
import cloudinary.uploader
import cloudinary.api

from flask import (
    Flask, render_template, request, current_app,
    jsonify, session, redirect, url_for
)
from werkzeug.security import generate_password_hash, check_password_hash

# Note: Ensure you have a protection_gpu_v2.py file with this function defined
from protection_gpu_v2 import protect_video

from config import config, init_cloudinary
from models import (
    db, User, ProtectionJob, Subscription,
    DownloadLog, UsageLimit, WatermarkActivation
)


# ══════════════════════════════════════════════════════════════════════
# APP FACTORY
# ══════════════════════════════════════════════════════════════════════
def create_app(env=None):
    app = Flask(__name__)

    env = env or os.environ.get('FLASK_ENV', 'default')
    app.config.from_object(config[env])

    # Init database
    db.init_app(app)

    # Init Cloudinary — reads env vars set in config
    init_cloudinary()

    return app

app = create_app()


# ══════════════════════════════════════════════════════════════════════
# CLOUDINARY & FILE HELPERS
# ══════════════════════════════════════════════════════════════════════
def cloudinary_configured():
    """Returns True if all 3 Cloudinary credentials are set."""
    return all([
        os.environ.get('CLOUDINARY_CLOUD_NAME'),
        os.environ.get('CLOUDINARY_API_KEY'),
        os.environ.get('CLOUDINARY_API_SECRET'),
    ])

def upload_to_cloudinary(file_path, folder, public_id=None, resource_type='video'):
    """
    Upload a local file to Cloudinary.
    Returns the secure URL and public_id on success.
    """
    upload_options = {
        'folder':        folder,
        'resource_type': resource_type,
        'use_filename':  True,
        'unique_filename': True,
    }
    if public_id:
        upload_options['public_id'] = public_id

    result = cloudinary.uploader.upload(file_path, **upload_options)

    return {
        'url':          result['secure_url'],
        'public_id':    result['public_id'],
        'size_bytes':   result.get('bytes', 0),
        'duration_secs': result.get('duration', 0),
    }

def delete_from_cloudinary(public_id, resource_type='video'):
    """Delete a video from Cloudinary by its public_id."""
    try:
        cloudinary.uploader.destroy(public_id, resource_type=resource_type)
    except Exception as e:
        app.logger.warning(f'Cloudinary delete failed for {public_id}: {e}')

def cleanup_local_file(path):
    """Delete a local temp file to keep the server clean."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        pass # Optional: log to your server logs


# ══════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════════════════════════════════════
def get_current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    return User.query.get(uid)

def login_required_api(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({'message': 'Authentication required.'}), 401
        return f(*args, **kwargs)
    return decorated

def check_upload_limit(user):
    limit = UsageLimit.query.get(user.subscription.plan)
    if not limit:
        return False, 'Plan configuration not found. Please contact support.'

    if limit.max_videos_per_month == -1:
        return True, 'ok'

    sub  = user.subscription
    used = sub.monthly_uploads_used if sub else 0

    if used >= limit.max_videos_per_month:
        return False, f'Monthly upload limit reached on the {user.plan_tier} plan.'
    return True, 'ok'


# ══════════════════════════════════════════════════════════════════════
# BACKGROUND WORKER
# ══════════════════════════════════════════════════════════════════════
def process_video_background(app_context, job_id, local_input_path, ext):
    """
    Runs in a separate thread. Applies the AI shield, uploads to Cloudinary,
    updates the database, and deletes local raw files.
    """
    with app_context:
        job = ProtectionJob.query.filter_by(job_id=job_id).first()
        if not job:
            return

        protected_local = None
        try:
            job.status = 'processing'
            db.session.commit()

            # 1. Apply AI Shield (Local -> Local)
            protected_local = f'uploads/{job_id}_protected.{ext}'
            
            # Simulated return dictionary from your AI model
            result = protect_video(local_input_path, protected_local)

            # 2. Upload ONLY the protected output to Cloudinary
            if cloudinary_configured():
                out = upload_to_cloudinary(
                    file_path = protected_local,
                    folder    = current_app.config['CLOUDINARY_FOLDER_PROTECTED'],
                    public_id = f'{job_id}_protected',
                )
                final_url = out['url']
            else:
                # Fallback for dev without Cloudinary
                final_url = protected_local

            # 3. Save watermark/fingerprint details
            activation = WatermarkActivation.from_result(job.id, result)
            db.session.add(activation)

            # 4. Mark Complete & Save URL
            job.status       = 'done'
            job.output_path  = final_url
            job.completed_at = datetime.utcnow()
            db.session.commit()

        except Exception as e:
            current_app.logger.error(f"Processing failed for {job_id}: {str(e)}")
            job.status = 'error'
            job.error_message = 'Failed to apply AI shield.'
            db.session.commit()

        finally:
            # 5. Scorched Earth: Delete all local raw and protected files
            cleanup_local_file(local_input_path)
            if cloudinary_configured() and protected_local:
                cleanup_local_file(protected_local)


# ══════════════════════════════════════════════════════════════════════
# PAGE ROUTES
# ══════════════════════════════════════════════════════════════════════
@app.route('/')
def home():
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
# API — AUTH
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/register', methods=['POST'])
def api_register():
    data     = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or len(username) < 3:
        return jsonify({'message': 'Username must be at least 3 characters.'}), 400
    if not password or len(password) < 8:
        return jsonify({'message': 'Password must be at least 8 characters.'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'message': 'Username already taken.'}), 409

    user = User(
        username      = username,
        password_hash = generate_password_hash(password),
        plan_tier     = 'free',
        is_active     = True,
    )
    db.session.add(user)
    db.session.flush()

    sub = Subscription(
        user_id              = user.id,
        plan                 = 'free',
        is_active            = True,
        monthly_uploads_used = 0,
    )
    db.session.add(sub)
    db.session.commit()

    session.permanent    = True
    session['user_id']   = user.id
    session['username']  = user.username

    return jsonify({
        'message':  'Account created successfully.',
        'redirect': url_for('dashboard'),
    }), 201

@app.route('/api/login', methods=['POST'])
def api_login():
    data     = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'message': 'Invalid username or password.'}), 401

    session.permanent    = True
    session['user_id']   = user.id
    session['username']  = user.username

    return jsonify({
        'message':  'Login successful.',
        'redirect': url_for('dashboard'),
    }), 200

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'redirect': url_for('login')}), 200


# ══════════════════════════════════════════════════════════════════════
# API — DASHBOARD DATA
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/dashboard', methods=['GET'])
@login_required_api
def api_dashboard():
    user = get_current_user()
    all_jobs   = ProtectionJob.query.filter_by(user_id=user.id).all()
    protected  = [j for j in all_jobs if j.status == 'done']
    processing = [j for j in all_jobs if j.status in ('pending', 'processing')]

    recent_jobs = (
        ProtectionJob.query
        .filter_by(user_id=user.id)
        .order_by(ProtectionJob.created_at.desc())
        .limit(20).all()
    )

    videos = [{
        'job_id':        j.job_id,
        'name':          j.original_filename,
        'date':          j.created_at.strftime('%b %d, %Y'),
        'status':        j.status,
        'download_url':  j.output_path or '', 
    } for j in recent_jobs]

    return jsonify({
        'stats': {
            'videos_protected': len(protected),
            'processing_now':   len(processing),
        },
        'videos': videos,
    }), 200


# ══════════════════════════════════════════════════════════════════════
# API — ASYNC VIDEO UPLOAD
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/upload', methods=['POST'])
@login_required_api
def api_upload():
    user = get_current_user()

    # 1. Plan limits & Validation
    allowed, msg = check_upload_limit(user)
    if not allowed:
        return jsonify({'message': msg}), 429

    if 'video' not in request.files:
        return jsonify({'message': 'No file uploaded.'}), 400

    f = request.files['video']
    name = (f.filename or '').strip()
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    
    if ext not in app.config.get('ALLOWED_EXTENSIONS', {'mp4', 'mov', 'avi', 'mkv'}):
        return jsonify({'message': f'Unsupported format: .{ext}'}), 415

    # 2. Save purely local temp file
    job_id = str(uuid.uuid4())
    upload_dir = app.config.get('UPLOAD_FOLDER', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)

    local_filename = f'{job_id}.{ext}'
    local_path = os.path.join(upload_dir, local_filename)
    f.save(local_path)

    # 3. Create Pending Job in DB
    job = ProtectionJob(
        job_id              = job_id,
        user_id             = user.id,
        status              = 'pending',
        original_filename   = name,
        original_size_bytes = os.path.getsize(local_path),
        input_path          = 'local_processing_queue', 
        output_path         = None, 
    )
    db.session.add(job)
    
    if user.subscription:
        user.subscription.monthly_uploads_used += 1
    db.session.commit()

    # 4. Trigger Background Thread
    thread = threading.Thread(
        target=process_video_background,
        args=(app.app_context(), job_id, local_path, ext)
    )
    thread.start()

    # 5. Return Success Instantly
    return jsonify({
        'job_id':  job_id,
        'status':  'pending',
        'message': 'Upload received. AI Shield processing has started.',
    }), 202


# ══════════════════════════════════════════════════════════════════════
# API — STATUS POLLING
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/status/<job_id>', methods=['GET'])
@login_required_api
def api_status(job_id):
    user = get_current_user()
    job  = ProtectionJob.query.filter_by(job_id=job_id, user_id=user.id).first()

    if not job:
        return jsonify({'message': 'Job not found.'}), 404

    return jsonify({
        'job_id':       job.job_id,
        'status':       job.status,           
        'error':        job.error_message,
        'download_url': job.output_path,      
    }), 200


# ══════════════════════════════════════════════════════════════════════
# API — DOWNLOAD & DELETE
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/download/<job_id>', methods=['GET'])
@login_required_api
def api_download(job_id):
    user = get_current_user()
    job  = ProtectionJob.query.filter_by(job_id=job_id, user_id=user.id).first()

    if not job or job.status != 'done':
        return jsonify({'message': 'Video not ready.'}), 404

    log = DownloadLog(job_id=job.id, user_id=user.id, ip_address=request.remote_addr)
    db.session.add(log)
    db.session.commit()

    return jsonify({
        'download_url': job.output_path,
        'filename': f"Protected_{job.original_filename}"
    }), 200

@app.route('/api/video/<job_id>', methods=['DELETE'])
@login_required_api
def api_delete_video(job_id):
    user = get_current_user()
    job  = ProtectionJob.query.filter_by(job_id=job_id, user_id=user.id).first()

    if not job:
        return jsonify({'message': 'Job not found.'}), 404

    if cloudinary_configured():
        delete_from_cloudinary(f'{job_id}_protected')

    DownloadLog.query.filter_by(job_id=job.id).delete()
    WatermarkActivation.query.filter_by(job_id=job.id).delete()
    db.session.delete(job)
    db.session.commit()

    return jsonify({'message': 'Video deleted successfully.'}), 200

# ══════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    # Ensure the upload directory exists on startup
    os.makedirs(app.config.get('UPLOAD_FOLDER', 'uploads'), exist_ok=True)
    app.run(debug=True)