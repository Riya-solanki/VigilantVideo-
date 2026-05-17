"""
config.py — Vigilant Video — App Configuration
"""
import os

class Config:
    # ── Security ──────────────────────────────────────────────────────
    SECRET_KEY     = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', 'super-secret-webhook-key')

    # WEBHOOK_BASE_URL: the public URL of this Render service.
    # Set this in Render's environment variables to your actual service URL,
    # e.g.  https://vigilant-video.onrender.com
    # The worker will POST to  <WEBHOOK_BASE_URL>/api/internal/webhook
    WEBHOOK_BASE_URL = os.environ.get('WEBHOOK_BASE_URL', '')

    # ── Database ───────────────────────────────────────────────────────
    SQLALCHEMY_DATABASE_URI    = os.environ.get('DATABASE_URL', 'sqlite:///vigilant.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS  = {
        'pool_pre_ping': True,
        'pool_recycle':  300,
    }

    # ── Cloudflare R2 ──────────────────────────────────────────────────
    R2_ACCOUNT_ID  = os.environ.get('R2_ACCOUNT_ID')
    R2_ACCESS_KEY  = os.environ.get('R2_ACCESS_KEY')
    R2_SECRET_KEY  = os.environ.get('R2_SECRET_KEY')
    R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME', 'vigilant-video-bucket')

    # ── Redis ──────────────────────────────────────────────────────────
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')

    # ── File Uploads ───────────────────────────────────────────────────
    # MAX_CONTENT_LENGTH caps the total request body Flask will accept.
    # For the presigned-POST flow this was 1 MB (JSON only).
    # For the direct stream-upload fallback (/api/upload/stream) it must
    # be at least as large as the biggest allowed file.
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024   # 1 MB default (JSON requests)

    # MAX_UPLOAD_BYTES is the limit enforced inside the presigned POST
    # policy sent to R2.  R2 will reject uploads larger than this.
    MAX_UPLOAD_BYTES   = int(os.environ.get('MAX_UPLOAD_BYTES', 5 * 1024 ** 3))  # 5 GB

    ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv'}

    # ── Session ────────────────────────────────────────────────────────
    SESSION_COOKIE_HTTPONLY    = True
    SESSION_COOKIE_SAMESITE    = 'Lax'
    PERMANENT_SESSION_LIFETIME = 86400


class DevelopmentConfig(Config):
    DEBUG = True
    # Override to accept full video files via /api/upload/stream
    # (avoids R2 CORS issues in local dev — browser uploads to Flask instead).
    MAX_CONTENT_LENGTH = 210 * 1024 * 1024  # 210 MB — covers free-plan 200 MB limit
    # Fallback so the Kaggle worker webhook always has a valid base URL locally.
    # Override in .env with your ngrok URL when Kaggle needs to reach this server.
    WEBHOOK_BASE_URL = os.environ.get('WEBHOOK_BASE_URL', 'http://127.0.0.1:5000')


class ProductionConfig(Config):
    DEBUG                 = False
    SESSION_COOKIE_SECURE = True


config = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'default':     DevelopmentConfig,
}