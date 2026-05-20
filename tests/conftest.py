"""
conftest.py — Shared pytest fixtures for VigilantVideo test suite
"""
import pytest
import sys
import os

# Make sure the project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import werkzeug.security

# Speed up password hashing for tests
_orig_generate_password_hash = werkzeug.security.generate_password_hash
_orig_check_password_hash = werkzeug.security.check_password_hash

def fast_generate_password_hash(password, method=None, salt_length=None):
    return f"plain:{password}"

def fast_check_password_hash(pwhash, password):
    if pwhash.startswith("plain:"):
        return pwhash == f"plain:{password}"
    try:
        return _orig_check_password_hash(pwhash, password)
    except Exception:
        return pwhash == password

werkzeug.security.generate_password_hash = fast_generate_password_hash
werkzeug.security.check_password_hash = fast_check_password_hash

# Import the module-level app (which has ALL routes registered via decorators)
import app as app_module
from models import db, User, Subscription, ProtectionJob, UsageLimit, WatermarkActivation, DownloadLog
from werkzeug.security import generate_password_hash
import uuid
from datetime import datetime


@pytest.fixture(scope='session')
def app():
    """Configure the existing Flask app for testing with in-memory SQLite once per session."""
    flask_app = app_module.app

    flask_app.config.update({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'SQLALCHEMY_TRACK_MODIFICATIONS': False,
        'SECRET_KEY': 'test-secret-key',
        'WEBHOOK_SECRET': 'test-webhook-secret',
        'WTF_CSRF_ENABLED': False,
        'R2_ACCOUNT_ID': 'test-account',
        'R2_ACCESS_KEY': 'test-access',
        'R2_SECRET_KEY': 'test-secret',
        'R2_BUCKET_NAME': 'test-bucket',
        'REDIS_URL': 'redis://localhost:6379',
        'UPLOAD_FOLDER': '/tmp/test_uploads',
        'ALLOWED_EXTENSIONS': {'mp4', 'mov', 'avi', 'mkv'},
        'MAX_CONTENT_LENGTH': 200 * 1024 * 1024,
    })

    with flask_app.app_context():
        db.drop_all()
        db.create_all()
        _seed_usage_limits()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture(scope='function', autouse=True)
def cleanup_database(app):
    """Delete all database rows between tests to ensure isolation, preserving seed data."""
    yield
    with app.app_context():
        db.session.rollback()
        for table in reversed(db.metadata.sorted_tables):
            if table.name != 'usage_limits':
                db.session.execute(table.delete())
        db.session.commit()


@pytest.fixture(scope='function', autouse=True)
def reset_rate_limiter():
    """Reset the rate limiter between tests to prevent rate limits leaking across tests."""
    from app import limiter
    limiter.reset()


def _seed_usage_limits():
    """Seed the usage_limits table required by auth/upload logic."""
    if not UsageLimit.query.get('free'):
        db.session.add(UsageLimit(
            plan='free',
            max_videos_per_month=3,
            max_video_length_secs=120,
            max_file_size_bytes=200 * 1024 * 1024,
            adversarial_enabled=False,
            freq_perturbation_enabled=False,
            processing_priority='low',
        ))
    if not UsageLimit.query.get('pro'):
        db.session.add(UsageLimit(
            plan='pro',
            max_videos_per_month=50,
            max_video_length_secs=3600,
            max_file_size_bytes=-1,
            adversarial_enabled=True,
            freq_perturbation_enabled=True,
            processing_priority='high',
        ))
    db.session.commit()


@pytest.fixture(scope='function')
def client(app):
    """Return a Flask test client."""
    return app.test_client()


@pytest.fixture(scope='function')
def runner(app):
    """Return a Flask CLI test runner."""
    return app.test_cli_runner()


@pytest.fixture(scope='function')
def db_session(app):
    """Provide the SQLAlchemy db session."""
    with app.app_context():
        yield db.session


@pytest.fixture(scope='function')
def sample_user(app):
    """Create and return a real User + Subscription in the DB."""
    with app.app_context():
        user = User(
            username='testuser',
            password_hash=generate_password_hash('password123'),
            plan_tier='free',
        )
        db.session.add(user)
        db.session.flush()
        sub = Subscription(user_id=user.id, plan='free', monthly_uploads_used=0)
        db.session.add(sub)
        db.session.commit()
        yield user


@pytest.fixture(scope='function')
def auth_client(client, sample_user, app):
    """A test client already logged in as sample_user."""
    with app.app_context():
        with client.session_transaction() as sess:
            sess['user_id'] = sample_user.id
    yield client


@pytest.fixture(scope='function')
def sample_job(app, sample_user):
    """Create a done ProtectionJob for sample_user."""
    with app.app_context():
        job = ProtectionJob(
            job_id=str(uuid.uuid4()),
            user_id=sample_user.id,
            status='done',
            original_filename='test_video.mp4',
            original_size_bytes=1024 * 1024,
            input_path='raw/test.mp4',
            output_path='protected/test.mp4',
            completed_at=datetime.utcnow(),
        )
        db.session.add(job)
        db.session.commit()
        yield job