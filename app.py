"""
app.py — Vigilant Video — Main Flask Application
Database:      SQLite (dev) → Supabase PostgreSQL (prod)
File Storage:  Cloudinary — all videos stored permanently in the cloud
"""

import os
import uuid
import tempfile
from datetime import datetime
from functools import wraps

import cloudinary
import cloudinary.uploader
import cloudinary.api

from flask import (
    Flask, render_template, request,
    jsonify, session, redirect, url_for
)
from werkzeug.security import generate_password_hash, check_password_hash
from protection import protect_video

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
# CLOUDINARY HELPERS
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

    Args:
        file_path    : local path to the file
        folder       : Cloudinary folder (e.g. 'vigilant_video/originals')
        public_id    : optional custom ID — defaults to filename without extension
        resource_type: 'video' for mp4/mov/avi/mkv

    Returns:
        dict with keys: url, public_id, size_bytes, duration_secs
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
    """
    Delete a video from Cloudinary by its public_id.
    Called to clean up original after protection is applied (optional).
    """
    try:
        cloudinary.uploader.destroy(public_id, resource_type=resource_type)
    except Exception as e:
        app.logger.warning(f'Cloudinary delete failed for {public_id}: {e}')


def cleanup_local_file(path):
    """Delete a local temp file after uploading to Cloudinary."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        app.logger.warning(f'Could not delete local temp file {path}: {e}')


# ══════════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════════════════════════════════════
def get_current_user():
    """Return the logged-in User object, or None."""
    uid = session.get('user_id')
    if not uid:
        return None
    return User.query.get(uid)


def login_required_api(f):
    """Decorator: returns 401 JSON if the user is not logged in."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({'message': 'Authentication required.'}), 401
        return f(*args, **kwargs)
    return decorated


def check_upload_limit(user):
    """
    Check whether the user is allowed to upload another video this month.
    Returns (allowed: bool, error_message: str).
    """
    limit = UsageLimit.query.get(user.plan_tier)
    if not limit:
        return False, 'Plan configuration not found. Please contact support.'

    if limit.max_videos_per_month == -1:
        return True, 'ok'   # unlimited

    sub  = user.subscription
    used = sub.monthly_uploads_used if sub else 0

    if used >= limit.max_videos_per_month:
        return False, (
            f'Monthly upload limit reached ({limit.max_videos_per_month} '
            f'videos/month on the {user.plan_tier} plan). '
            f'Please upgrade to continue uploading.'
        )
    return True, 'ok'


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
    return render_template('dashboard.html')


# ══════════════════════════════════════════════════════════════════════
# API — AUTH
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/register', methods=['POST'])
def api_register():
    data     = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username:
        return jsonify({'message': 'Username is required.'}), 400
    if len(username) < 3:
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
        'name':     user.username,
        'initials': user.username[:2].upper(),
        'plan':     user.plan_tier,
        'redirect': url_for('dashboard'),
    }), 201


@app.route('/api/login', methods=['POST'])
def api_login():
    data     = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'message': 'Username and password are required.'}), 400

    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'message': 'Invalid username or password.'}), 401
    if not user.is_active:
        return jsonify({'message': 'Account is disabled. Contact support.'}), 403

    session.permanent    = True
    session['user_id']   = user.id
    session['username']  = user.username

    return jsonify({
        'message':  'Login successful.',
        'name':     user.username,
        'initials': user.username[:2].upper(),
        'plan':     user.plan_tier,
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
    """Returns all data needed to populate the dashboard UI from real DB rows."""
    user = get_current_user()
    if not user:
        return jsonify({'message': 'User not found.'}), 404

    all_jobs   = ProtectionJob.query.filter_by(user_id=user.id).all()
    protected  = [j for j in all_jobs if j.status == 'done']
    processing = [j for j in all_jobs if j.status in ('pending', 'processing')]

    # Video library — most recent 20
    recent_jobs = (
        ProtectionJob.query
        .filter_by(user_id=user.id)
        .order_by(ProtectionJob.created_at.desc())
        .limit(20).all()
    )

    videos = []
    for j in recent_jobs:
        parts = j.original_filename.rsplit('.', 1)
        videos.append({
            'job_id':        j.job_id,
            'name':          parts[0] if len(parts) > 1 else j.original_filename,
            'ext':           parts[1].lower() if len(parts) > 1 else '',
            'date':          j.created_at.strftime('%b %d, %Y'),
            'size':          j.size_display(),
            'status':        j.status,
            'download_url':  j.output_path or '',   # Cloudinary URL — empty if not done
        })

    # Activity feed
    downloads = (
        DownloadLog.query
        .filter_by(user_id=user.id)
        .order_by(DownloadLog.downloaded_at.desc())
        .limit(10).all()
    )
    feed = []
    for d in downloads:
        job = ProtectionJob.query.get(d.job_id)
        feed.append({
            'dot':  'blue',
            'text': f'Download of <strong>{job.original_filename if job else "video"}</strong>',
            'time': d.downloaded_at.strftime('%b %d, %I:%M %p'),
        })

    completed = (
        ProtectionJob.query
        .filter_by(user_id=user.id, status='done')
        .order_by(ProtectionJob.completed_at.desc())
        .limit(5).all()
    )
    for j in completed:
        feed.append({
            'dot':  'green',
            'text': f'<strong>{j.original_filename}</strong> successfully protected',
            'time': j.completed_at.strftime('%b %d, %I:%M %p') if j.completed_at else '',
        })

    feed = feed[:10]

    sub   = user.subscription
    limit = UsageLimit.query.get(user.plan_tier)

    return jsonify({
        'user':  user.to_dict(),
        'stats': {
            'videos_protected':        len(protected),
            'processing_now':          len(processing),
            'scrape_attempts_blocked': 0,
            'storage_used_bytes':      sum(j.original_size_bytes or 0 for j in all_jobs),
            'storage_limit_bytes':     limit.max_file_size_bytes if limit else 209_715_200,
        },
        'subscription': sub.to_dict() if sub else {'plan': 'free', 'monthly_uploads_used': 0},
        'videos':       videos,
        'feed':         feed,
    }), 200


# ══════════════════════════════════════════════════════════════════════
# API — VIDEO UPLOAD  (with Cloudinary)
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/upload', methods=['POST'])
@login_required_api
def api_upload():
    """
    Full upload flow:
    1. Validate the file and check plan limits
    2. Save temporarily to local disk
    3. Upload original to Cloudinary  →  vigilant_video/originals/
    4. Create ProtectionJob row in DB with Cloudinary URL
    5. Delete local temp file
    6. Return job_id to frontend for status polling

    When protection.py is integrated, step 4.5 will be:
        run protect_video() → upload output → save output URL → mark done
    """
    user = get_current_user()
    if not user:
        return jsonify({'message': 'User not found.'}), 404

    # ── Plan limit check ────────────────────────────────────────────────
    allowed, msg = check_upload_limit(user)
    if not allowed:
        return jsonify({'message': msg}), 429

    # ── File validation ─────────────────────────────────────────────────
    if 'video' not in request.files:
        return jsonify({'message': 'No file uploaded. Use field name "video".'}), 400

    f    = request.files['video']
    name = (f.filename or '').strip()
    if not name:
        return jsonify({'message': 'Filename is empty.'}), 400

    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext not in app.config.get('ALLOWED_EXTENSIONS', {'mp4', 'mov', 'avi', 'mkv'}):
        return jsonify({'message': f'Unsupported format: .{ext}. Use MP4, MOV, AVI, or MKV.'}), 415

    # ── Save to local temp file ─────────────────────────────────────────
    job_id     = str(uuid.uuid4())
    upload_dir = app.config.get('UPLOAD_FOLDER', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)

    local_filename = f'{job_id}.{ext}'
    local_path     = os.path.join(upload_dir, local_filename)
    f.save(local_path)
    file_size = os.path.getsize(local_path)

    # ── Upload original to Cloudinary ───────────────────────────────────
    input_url        = None
    cloudinary_input_id = None

    if cloudinary_configured():
        try:
            result = upload_to_cloudinary(
                file_path = local_path,
                folder    = app.config.get('CLOUDINARY_FOLDER_ORIGINALS', 'vigilant_video/originals'),
                public_id = job_id,       # use job_id as Cloudinary public_id for easy lookup
            )
            input_url           = result['url']
            cloudinary_input_id = result['public_id']
            app.logger.info(f'Uploaded original to Cloudinary: {input_url}')
        except Exception as e:
            app.logger.error(f'Cloudinary upload failed: {e}')
            cleanup_local_file(local_path)
            return jsonify({'message': 'File upload to storage failed. Please try again.'}), 500
    else:
        # Cloudinary not configured — keep local file for development
        input_url = local_path
        app.logger.warning('Cloudinary not configured — storing file locally (dev mode only).')

    # ── Create ProtectionJob row ────────────────────────────────────────
    job = ProtectionJob(
        job_id              = job_id,
        user_id             = user.id,
        status              = 'pending',
        original_filename   = name,
        original_size_bytes = file_size,
        input_path          = input_url,    # Cloudinary URL (or local path in dev)
        output_path         = None,         # filled in after protection completes
    )
    db.session.add(job)

    # ── Increment monthly upload counter ────────────────────────────────
    sub = user.subscription
    if sub:
        sub.monthly_uploads_used += 1

    db.session.commit()

    # ── Clean up local temp file (already uploaded to Cloudinary) ───────
    if cloudinary_configured():
        cleanup_local_file(local_path)

    # ── After uploading original to Cloudinary ──
    # Run your protection algorithm on the local file
    protected_local = f'uploads/{job_id}_protected.{ext}'
    result = protect_video(local_path, protected_local)

    # Upload the protected output to Cloudinary
    out = upload_to_cloudinary(
        file_path = protected_local,
        folder    = app.config['CLOUDINARY_FOLDER_PROTECTED'],
        public_id = f'{job_id}_protected',
    )

    # Save watermark details to DB
    activation = WatermarkActivation.from_result(job.id, result)
    db.session.add(activation)

    # Mark the job as done with the Cloudinary download URL
    job.status       = 'done'
    job.output_path  = out['url']        # ← permanent Cloudinary URL
    job.completed_at = datetime.utcnow()
    db.session.commit()

    # Clean up both local temp files
    cleanup_local_file(local_path)
    cleanup_local_file(protected_local)

    return jsonify({
        'job_id':  job_id,
        'status':  'pending',
        'message': 'Upload successful. Protection has been queued.',
    }), 202


# ══════════════════════════════════════════════════════════════════════
# API — STATUS POLLING
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/status/<job_id>', methods=['GET'])
@login_required_api
def api_status(job_id):
    """
    Frontend polls this every 2 seconds to check processing progress.
    Returns the current status and the download URL once done.
    """
    user = get_current_user()
    job  = ProtectionJob.query.filter_by(job_id=job_id, user_id=user.id).first()

    if not job:
        return jsonify({'message': 'Job not found.'}), 404

    return jsonify({
        'job_id':       job.job_id,
        'status':       job.status,           # pending / processing / done / error
        'error':        job.error_message,
        'download_url': job.output_path,      # Cloudinary URL — None until done
        'completed_at': job.completed_at.isoformat() if job.completed_at else None,
    }), 200


# ══════════════════════════════════════════════════════════════════════
# API — DOWNLOAD
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/download/<job_id>', methods=['GET'])
@login_required_api
def api_download(job_id):
    """
    Called when the user clicks "Download Protected Video".

    Returns the Cloudinary download URL so the browser can fetch the file
    directly from Cloudinary — no proxying through Flask needed.
    Also logs the download in the download_logs table for the activity feed.
    """
    user = get_current_user()
    job  = ProtectionJob.query.filter_by(job_id=job_id, user_id=user.id).first()

    if not job:
        return jsonify({'message': 'Job not found.'}), 404

    if job.status != 'done':
        return jsonify({'message': 'Video is not ready yet. Check status first.'}), 409

    if not job.output_path:
        return jsonify({'message': 'Protected video URL is missing. Contact support.'}), 500

    # ── Log the download ──────────────────────────────────────────────
    log = DownloadLog(
        job_id     = job.id,
        user_id    = user.id,
        ip_address = request.remote_addr,
    )
    db.session.add(log)
    db.session.commit()

    # ── Return the Cloudinary URL ─────────────────────────────────────
    # The frontend uses this URL to trigger a browser download.
    # The file is served directly by Cloudinary — not through your server.
    return jsonify({
        'download_url':    job.output_path,       # permanent Cloudinary HTTPS URL
        'filename':        job.original_filename,  # suggested save-as filename
    }), 200


# ══════════════════════════════════════════════════════════════════════
# API — USER INFO
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/me', methods=['GET'])
@login_required_api
def api_me():
    """Returns the current user's profile info for the dashboard."""
    user = get_current_user()
    if not user:
        return jsonify({'message': 'User not found.'}), 404
    return jsonify(user.to_dict()), 200


# ══════════════════════════════════════════════════════════════════════
# API — DELETE VIDEO (optional)
# ══════════════════════════════════════════════════════════════════════
@app.route('/api/video/<job_id>', methods=['DELETE'])
@login_required_api
def api_delete_video(job_id):
    """
    Delete a video from both Cloudinary and the database.
    Only the owner can delete their own videos.
    """
    user = get_current_user()
    job  = ProtectionJob.query.filter_by(job_id=job_id, user_id=user.id).first()

    if not job:
        return jsonify({'message': 'Job not found.'}), 404

    # ── Delete from Cloudinary ────────────────────────────────────────
    if cloudinary_configured():
        # Delete original (public_id = job_id)
        delete_from_cloudinary(job_id)
        # Delete protected output (public_id = job_id_protected)
        delete_from_cloudinary(f'{job_id}_protected')

    # ── Delete from DB ────────────────────────────────────────────────
    # Remove related records first (foreign key constraints)
    DownloadLog.query.filter_by(job_id=job.id).delete()
    WatermarkActivation.query.filter_by(job_id=job.id).delete()
    db.session.delete(job)
    db.session.commit()

    return jsonify({'message': 'Video deleted successfully.'}), 200


# ══════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    app.run(debug=True)