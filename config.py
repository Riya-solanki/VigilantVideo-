"""
config.py — Vigilant Video — App Configuration
"""
import os

class Config:
    # ── Security ──────────────────────────────────────────────────────
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', 'super-secret-webhook-key')

    # ── Database ───────────────────────────────────────────────────────
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///vigilant.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle':  300,
    }

    # ── Cloudflare R2 & Redis (The Distributed Stack) ──────────────────
    R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID')
    R2_ACCESS_KEY = os.environ.get('R2_ACCESS_KEY')
    R2_SECRET_KEY = os.environ.get('R2_SECRET_KEY')
    R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME', 'vigilant-video-bucket')
    
    REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')

    # ── File Uploads ───────────────────────────────────────────────────
    MAX_CONTENT_LENGTH = 200 * 1024 * 1024   # 200 MB
    UPLOAD_FOLDER      = os.environ.get('UPLOAD_FOLDER', 'uploads')
    ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv'}

    # ── Session ────────────────────────────────────────────────────────
    SESSION_COOKIE_HTTPONLY    = True
    SESSION_COOKIE_SAMESITE    = 'Lax'
    PERMANENT_SESSION_LIFETIME = 86400

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    MAX_CONTENT_LENGTH    = 5 * 1024 * 1024 * 1024

config = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'default':     DevelopmentConfig,
}