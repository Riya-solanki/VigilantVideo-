"""
config.py — Vigilant Video — App Configuration
Supports SQLite (dev) → Supabase PostgreSQL (prod) with one env var change.
Cloudinary handles storage ONLY for the final protected video outputs.
"""

import os
import cloudinary


class Config:
    # ── Security ──────────────────────────────────────────────────────
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

    # ── Database ───────────────────────────────────────────────────────
    # Dev:  SQLite file created automatically — zero setup needed
    # Prod: export DATABASE_URL=postgresql://... (Supabase connection string)
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'sqlite:///vigilant.db'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,    # auto-reconnect on stale connections
        'pool_recycle':  300,     # recycle connections every 5 min
    }

    # ── Cloudinary — Video File Storage ───────────────────────────────
    # Get these 3 values from: cloudinary.com → Dashboard → API Keys
    # Set as environment variables — NEVER hardcode them in source code
    #
    #   export CLOUDINARY_CLOUD_NAME=your_cloud_name
    #   export CLOUDINARY_API_KEY=your_api_key
    #   export CLOUDINARY_API_SECRET=your_api_secret
    #
    CLOUDINARY_CLOUD_NAME = os.environ.get('XXX', '')
    CLOUDINARY_API_KEY    = os.environ.get('YYYY',    '')
    CLOUDINARY_API_SECRET = os.environ.get('ZZZZ', '')

    # Cloudinary folder structure inside your account:
    #   vigilant_video/
    #       protected/   ← ONLY processed + protected outputs are stored in the cloud
    CLOUDINARY_FOLDER_PROTECTED = 'vigilant_video/protected'

    # ── File Uploads ───────────────────────────────────────────────────
    # The new pipeline: Flask accepts the raw file locally → AI Worker processes it → 
    # Uploads the shielded output to Cloudinary → Deletes all local copies instantly.
    MAX_CONTENT_LENGTH = 200 * 1024 * 1024   # 200 MB — matches free plan limit
    UPLOAD_FOLDER      = os.environ.get('UPLOAD_FOLDER', 'uploads')  # strict temp directory
    ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv'}

    # ── Session ────────────────────────────────────────────────────────
    SESSION_COOKIE_HTTPONLY    = True
    SESSION_COOKIE_SAMESITE    = 'Lax'
    PERMANENT_SESSION_LIFETIME = 86400   # 24 hours in seconds


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True         # HTTPS only in production
    MAX_CONTENT_LENGTH    = 5 * 1024 * 1024 * 1024   # 5 GB for business plan


# ── Cloudinary global initialiser ─────────────────────────────────────
def init_cloudinary():
    """
    Configures the Cloudinary Python SDK globally.
    Called once inside create_app() after the Flask config is loaded.
    """
    cloudinary.config(
        cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME'),
        api_key    = os.environ.get('CLOUDINARY_API_KEY'),
        api_secret = os.environ.get('CLOUDINARY_API_SECRET'),
        secure     = True,    # always return https:// URLs
    )


# ── Config picker ──────────────────────────────────────────────────────
config = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'default':     DevelopmentConfig,
}