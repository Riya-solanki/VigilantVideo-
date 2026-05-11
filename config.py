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
    # MAX_CONTENT_LENGTH is intentionally kept small — Render only receives
    # JSON metadata now (presign step), never raw video bytes.
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024   # 1 MB (JSON requests only)

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


class ProductionConfig(Config):
    DEBUG                 = False
    SESSION_COOKIE_SECURE = True


config = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'default':     DevelopmentConfig,
}