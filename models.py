"""
models.py — SQLAlchemy models for Vigilant Video
All 7 tables from the Database Plan, ready for SQLite (dev) and Supabase (prod).
"""

import json
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# ══════════════════════════════════════════════════════════════════
# TABLE 1: users
# Replaces the in-memory `users = {}` dict in app.py
# ══════════════════════════════════════════════════════════════════
class User(db.Model):
    __tablename__ = 'users'

    id            = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    username      = db.Column(db.String(80),  nullable=False, unique=True)
    password_hash = db.Column(db.String(256), nullable=False)
    plan_tier     = db.Column(db.String(20),  default='free', nullable=False)
    is_active     = db.Column(db.Boolean,     default=True,  nullable=False)
    created_at    = db.Column(db.DateTime,    default=datetime.utcnow, nullable=False)

    # ── Relationships ──────────────────────────────────────────────
    jobs          = db.relationship('ProtectionJob',  backref='user', lazy='dynamic')
    downloads     = db.relationship('DownloadLog',    backref='user', lazy='dynamic')
    feedback      = db.relationship('UserFeedback',   backref='user', lazy='dynamic')
    subscription  = db.relationship('Subscription',   backref='user', uselist=False)

    # ── Helper methods ─────────────────────────────────────────────
    def to_dict(self):
        return {
            'id':         self.id,
            'username':   self.username,
            'plan_tier':  self.subscription.plan if self.subscription else 'free',  # ← single source
            'is_active':  self.is_active,
            'created_at': self.created_at.isoformat(),
            'initials':   self.username[:2].upper(),
    }

    def get_monthly_uploads_used(self):
        """How many uploads this user has done this calendar month."""
        sub = self.subscription
        return sub.monthly_uploads_used if sub else 0

    def get_upload_limit(self):
        if not self.subscription:
            return 3  # fallback to free
        limit = UsageLimit.query.get(self.subscription.plan)
        return limit.max_videos_per_month if limit else 3

    def __repr__(self):
        plan = self.subscription.plan if self.subscription else 'free'
        return f'<User {self.username} [{plan}]>'


# ══════════════════════════════════════════════════════════════════
# TABLE 2: protection_jobs
# One row per video upload. Powers the Video Library dashboard.
# ══════════════════════════════════════════════════════════════════
class ProtectionJob(db.Model):
    __tablename__ = 'protection_jobs'

    id                  = db.Column(db.Integer,    primary_key=True, autoincrement=True)
    job_id              = db.Column(db.String(36), unique=True, nullable=False, index=True)
    user_id             = db.Column(db.Integer,    db.ForeignKey('users.id'), nullable=False)
    status              = db.Column(db.String(20), nullable=False, default='pending')
    # status values: pending | processing | done | error

    error_message       = db.Column(db.Text,       nullable=True)
    original_filename   = db.Column(db.String(255), nullable=False)
    original_size_bytes = db.Column(db.BigInteger,  nullable=True)
    input_path          = db.Column(db.String(500), nullable=True)   # upload path / Cloudinary URL
    output_path         = db.Column(db.String(500), nullable=True)   # protected video path / URL

    created_at          = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at        = db.Column(db.DateTime, nullable=True)

    # ── Relationships ──────────────────────────────────────────────
    watermark      = db.relationship('WatermarkActivation', backref='job', uselist=False)
    download_logs  = db.relationship('DownloadLog',         backref='job', lazy='dynamic')
    feedback       = db.relationship('UserFeedback',        backref='job', lazy='dynamic')

    # ── Helper methods ─────────────────────────────────────────────
    def size_display(self):
        """Returns human-readable file size e.g. '148 MB'."""
        if not self.original_size_bytes:
            return '—'
        b = self.original_size_bytes
        for unit in ['B', 'KB', 'MB', 'GB']:
            if b < 1024:
                return f'{b:.1f} {unit}'
            b /= 1024
        return f'{b:.1f} GB'

    def to_dict(self):
        return {
            'job_id':            self.job_id,
            'status':            self.status,
            'original_filename': self.original_filename,
            'size':              self.size_display(),
            'created_at':        self.created_at.isoformat(),
            'completed_at':      self.completed_at.isoformat() if self.completed_at else None,
            'output_path':       self.output_path,
            'error_message':     self.error_message,
        }

    def __repr__(self):
        return f'<ProtectionJob {self.job_id} [{self.status}]>'


# ══════════════════════════════════════════════════════════════════
# TABLE 3: watermark_activations
# Stores the full output of protect_video() from protection.py
# ═════════════════════════════════════════════════════════
# 
# ═════════
class WatermarkActivation(db.Model):
    __tablename__ = 'watermark_activations'

    id                        = db.Column(db.Integer, primary_key=True, autoincrement=True)
    job_id                    = db.Column(db.Integer, db.ForeignKey('protection_jobs.id'), nullable=False)

    watermark_text            = db.Column(db.String(256), nullable=True)
    watermark_strength        = db.Column(db.Float,  default=0.1)
    noise_strength            = db.Column(db.Float,  default=0.03)
    freq_perturbation_strength= db.Column(db.Float,  default=0.02)
    frames_processed          = db.Column(db.Integer, nullable=True)
    processing_duration_secs  = db.Column(db.Float,   nullable=True)

    # Stored as JSON strings — use the helper methods to read/write
    _protections_applied      = db.Column('protections_applied', db.Text, nullable=True)
    _models_used              = db.Column('models_used',          db.Text, nullable=True)

    created_at                = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # ── JSON helpers ───────────────────────────────────────────────
    @property
    def protections_applied(self):
        return json.loads(self._protections_applied) if self._protections_applied else []

    @protections_applied.setter
    def protections_applied(self, value):
        self._protections_applied = json.dumps(value)

    @property
    def models_used(self):
        return json.loads(self._models_used) if self._models_used else []

    @models_used.setter
    def models_used(self, value):
        self._models_used = json.dumps(value)

    @classmethod
    def from_result(cls, job_db_id, result: dict):
        """
        Create a WatermarkActivation directly from the protect_video() result dict.

        Usage in app.py:
            activation = WatermarkActivation.from_result(job.id, result)
            db.session.add(activation)
            db.session.commit()
        """
        w = cls(job_id=job_db_id)
        w.watermark_text             = result.get('watermark_text')
        w.watermark_strength         = result.get('watermark_strength', 0.1)
        w.noise_strength             = result.get('noise_strength', 0.03)
        w.freq_perturbation_strength = result.get('freq_perturbation_strength', 0.02)
        w.frames_processed           = result.get('frames_processed')
        w.processing_duration_secs   = result.get('duration_seconds')
        w.protections_applied        = result.get('protections_applied', [])
        w.models_used                = result.get('models_used', [])
        return w

    def __repr__(self):
        return f'<WatermarkActivation job_id={self.job_id} frames={self.frames_processed}>'


# ══════════════════════════════════════════════════════════════════
# TABLE 4: download_logs
# Powers the Activity Feed in the dashboard
# ══════════════════════════════════════════════════════════════════
class DownloadLog(db.Model):
    __tablename__ = 'download_logs'

    id            = db.Column(db.Integer,    primary_key=True, autoincrement=True)
    job_id        = db.Column(db.Integer,    db.ForeignKey('protection_jobs.id'), nullable=False)
    user_id       = db.Column(db.Integer,    db.ForeignKey('users.id'),           nullable=False)
    ip_address    = db.Column(db.String(45), nullable=True)
    downloaded_at = db.Column(db.DateTime,   default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'job_id':       self.job_id,
            'user_id':      self.user_id,
            'ip_address':   self.ip_address,
            'downloaded_at': self.downloaded_at.isoformat(),
        }

    def __repr__(self):
        return f'<DownloadLog job_id={self.job_id} user_id={self.user_id}>'


# ══════════════════════════════════════════════════════════════════
# TABLE 5: user_feedback
# Optional — add when you want ratings/bug reports
# ══════════════════════════════════════════════════════════════════
class UserFeedback(db.Model):
    __tablename__ = 'user_feedback'

    id         = db.Column(db.Integer,    primary_key=True, autoincrement=True)
    user_id    = db.Column(db.Integer,    db.ForeignKey('users.id'),            nullable=False)
    job_id     = db.Column(db.Integer,    db.ForeignKey('protection_jobs.id'),  nullable=True)
    rating     = db.Column(db.Integer,    nullable=True)     # 1–5
    category   = db.Column(db.String(50), nullable=True)     # quality / speed / bug_report / etc.
    comment    = db.Column(db.Text,       nullable=True)
    created_at = db.Column(db.DateTime,   default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<UserFeedback user_id={self.user_id} rating={self.rating}>'


# ══════════════════════════════════════════════════════════════════
# TABLE 6: subscriptions
# Powers plan badge + upload counter in dashboard
# ══════════════════════════════════════════════════════════════════
class Subscription(db.Model):
    __tablename__ = 'subscriptions'

    id                   = db.Column(db.Integer,    primary_key=True, autoincrement=True)
    user_id              = db.Column(db.Integer,    db.ForeignKey('users.id'), nullable=False, unique=True)
    plan                 = db.Column(db.String(20), nullable=False, default='free')
    is_active            = db.Column(db.Boolean,    default=True,  nullable=False)
    payment_method       = db.Column(db.String(50), nullable=True)   # stripe / razorpay / etc.
    monthly_uploads_used = db.Column(db.Integer,    default=0,     nullable=False)
    started_at           = db.Column(db.DateTime,   default=datetime.utcnow, nullable=False)
    expires_at           = db.Column(db.DateTime,   nullable=True)


    def reset_monthly_counter(self):
        """Call this on a monthly cron job to reset upload counts."""
        self.monthly_uploads_used = 0
        db.session.commit()

    def to_dict(self):
        return {
            'plan':                 self.plan,
            'is_active':            self.is_active,
            'monthly_uploads_used': self.monthly_uploads_used,
            'expires_at':           self.expires_at.isoformat() if self.expires_at else None,
        }

    def __repr__(self):
        return f'<Subscription user_id={self.user_id} plan={self.plan}>'


# ══════════════════════════════════════════════════════════════════
# TABLE 7: usage_limits  (pre-seeded config — do not edit via API)
# ══════════════════════════════════════════════════════════════════
class UsageLimit(db.Model):
    __tablename__ = 'usage_limits'

    plan                       = db.Column(db.String(20),  primary_key=True)
    max_videos_per_month       = db.Column(db.Integer,     nullable=False)   # -1 = unlimited
    max_video_length_secs      = db.Column(db.Integer,     nullable=False)   # -1 = unlimited
    max_file_size_bytes        = db.Column(db.BigInteger,  nullable=False)   # -1 = unlimited
    adversarial_enabled        = db.Column(db.Boolean,     default=False)
    freq_perturbation_enabled  = db.Column(db.Boolean,     default=False)
    processing_priority        = db.Column(db.String(20),  default='low')    # low / medium / high

    def to_dict(self):
        return {
            'plan':                      self.plan,
            'max_videos_per_month':      self.max_videos_per_month,
            'max_video_length_secs':     self.max_video_length_secs,
            'max_file_size_bytes':       self.max_file_size_bytes,
            'adversarial_enabled':       self.adversarial_enabled,
            'freq_perturbation_enabled': self.freq_perturbation_enabled,
            'processing_priority':       self.processing_priority,
        }

    def __repr__(self):
        return f'<UsageLimit plan={self.plan}>'