"""
tests/test_integration.py — Integration Tests for VigilantVideo
Tests all API endpoints with a real in-memory SQLite database.
External services (Redis, R2) are mocked.
"""
import pytest
import json
import uuid
from unittest.mock import patch, MagicMock
from io import BytesIO
from datetime import datetime, timedelta

from models import db, User, Subscription, ProtectionJob, UsageLimit, DownloadLog, WatermarkActivation
from werkzeug.security import generate_password_hash


# ─────────────────────────────────────────────────────────────────
# 1. AUTH — REGISTER
# ─────────────────────────────────────────────────────────────────
class TestRegisterAPI:

    def test_register_success(self, client):
        """POST /api/auth/register with valid data returns 201."""
        resp = client.post('/api/auth/register',
                           json={'username': 'newuser', 'password': 'securepass'})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data['user']['username'] == 'newuser'

    def test_register_creates_free_subscription(self, client, app):
        """Registration auto-creates a free Subscription record."""
        client.post('/api/auth/register',
                    json={'username': 'subuser', 'password': 'pass123'})
        with app.app_context():
            user = User.query.filter_by(username='subuser').first()
            assert user.subscription is not None
            assert user.subscription.plan == 'free'

    def test_register_missing_username(self, client):
        """POST with no username returns 400."""
        resp = client.post('/api/auth/register',
                           json={'username': '', 'password': 'pass123'})
        assert resp.status_code == 400

    def test_register_missing_password(self, client):
        """POST with no password returns 400."""
        resp = client.post('/api/auth/register',
                           json={'username': 'user', 'password': ''})
        assert resp.status_code == 400

    def test_register_short_password(self, client):
        """Password shorter than 6 chars returns 400."""
        resp = client.post('/api/auth/register',
                           json={'username': 'user2', 'password': '123'})
        assert resp.status_code == 400

    def test_register_duplicate_username(self, client):
        """Registering a taken username returns 409 Conflict."""
        client.post('/api/auth/register',
                    json={'username': 'dupuser', 'password': 'pass123'})
        resp = client.post('/api/auth/register',
                           json={'username': 'dupuser', 'password': 'anotherpass'})
        assert resp.status_code == 409

    def test_register_sets_session(self, client):
        """Successful registration must establish a session."""
        with client.session_transaction() as sess:
            sess.clear()
        client.post('/api/auth/register',
                    json={'username': 'sessionuser', 'password': 'pass123'})
        resp = client.get('/api/me')
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────
# 2. AUTH — LOGIN
# ─────────────────────────────────────────────────────────────────
class TestLoginAPI:

    def test_login_success(self, client, sample_user):
        """POST /api/auth/login with correct credentials returns 200."""
        resp = client.post('/api/auth/login',
                           json={'username': 'testuser', 'password': 'password123'})
        assert resp.status_code == 200
        assert resp.get_json()['user']['username'] == 'testuser'

    def test_login_wrong_password(self, client, sample_user):
        """Wrong password returns 401."""
        resp = client.post('/api/auth/login',
                           json={'username': 'testuser', 'password': 'wrongpass'})
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        """Non-existent username returns 401."""
        resp = client.post('/api/auth/login',
                           json={'username': 'ghost', 'password': 'whatever'})
        assert resp.status_code == 401

    def test_login_missing_fields(self, client):
        """Missing username or password returns 400."""
        resp = client.post('/api/auth/login', json={'username': 'testuser'})
        assert resp.status_code == 400

    def test_login_sets_session(self, client, sample_user):
        """Login must set user_id in session."""
        client.post('/api/auth/login',
                    json={'username': 'testuser', 'password': 'password123'})
        resp = client.get('/api/me')
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────
# 3. AUTH — LOGOUT & ME
# ─────────────────────────────────────────────────────────────────
class TestLogoutAndMeAPI:

    def test_logout_success(self, auth_client):
        """POST /api/logout returns 200 and clears session."""
        resp = auth_client.post('/api/logout')
        assert resp.status_code == 200
        follow = auth_client.get('/api/me')
        assert follow.status_code == 401

    def test_me_authenticated(self, auth_client, sample_user):
        """GET /api/me when logged in returns user data."""
        resp = auth_client.get('/api/me')
        assert resp.status_code == 200
        assert resp.get_json()['user']['username'] == 'testuser'

    def test_me_unauthenticated(self, client):
        """GET /api/me without session returns 401."""
        resp = client.get('/api/me')
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────
# 4. DASHBOARD ROUTES (PAGES)
# ─────────────────────────────────────────────────────────────────
class TestPageRoutes:

    def test_index_page_returns_200(self, client):
        """GET / must return 200."""
        resp = client.get('/')
        assert resp.status_code == 200

    def test_login_page_returns_200(self, client):
        """GET /login must return 200 for anonymous user."""
        resp = client.get('/login')
        assert resp.status_code == 200

    def test_register_page_returns_200(self, client):
        """GET /register must return 200 for anonymous user."""
        resp = client.get('/register')
        assert resp.status_code == 200

    def test_dashboard_redirects_unauthenticated(self, client):
        """GET /dashboard without session must redirect to /login."""
        resp = client.get('/dashboard', follow_redirects=False)
        assert resp.status_code in (301, 302)
        assert 'login' in resp.headers.get('Location', '').lower()

    def test_dashboard_accessible_when_logged_in(self, auth_client):
        """GET /dashboard with active session must return 200."""
        resp = auth_client.get('/dashboard')
        assert resp.status_code == 200

    def test_login_page_redirects_authenticated(self, auth_client):
        """GET /login while already logged in must redirect to dashboard."""
        resp = auth_client.get('/login', follow_redirects=False)
        assert resp.status_code in (301, 302)


# ─────────────────────────────────────────────────────────────────
# 5. API DASHBOARD
# ─────────────────────────────────────────────────────────────────
class TestAPIDashboard:

    def test_dashboard_api_requires_auth(self, client):
        """GET /api/dashboard without session returns 401."""
        resp = client.get('/api/dashboard')
        assert resp.status_code == 401

    @patch('app.get_s3_client')
    def test_dashboard_api_returns_stats(self, mock_s3, auth_client):
        """GET /api/dashboard returns stats, videos, and feed."""
        mock_s3.return_value = MagicMock()
        resp = auth_client.get('/api/dashboard')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'stats' in data
        assert 'videos' in data
        assert 'feed' in data

    @patch('app.get_s3_client')
    def test_dashboard_stats_keys(self, mock_s3, auth_client):
        """Dashboard stats must include videos_protected and storage fields."""
        mock_s3.return_value = MagicMock()
        resp = auth_client.get('/api/dashboard')
        stats = resp.get_json()['stats']
        for key in ('videos_protected', 'storage_used_bytes', 'uploads_used', 'uploads_limit'):
            assert key in stats

    @patch('app.get_s3_client')
    def test_dashboard_videos_list(self, mock_s3, auth_client, app, sample_user, sample_job):
        """Dashboard must list user's protection jobs."""
        mock_s3.return_value = MagicMock()
        with app.app_context():
            resp = auth_client.get('/api/dashboard')
        data = resp.get_json()
        assert isinstance(data['videos'], list)


# ─────────────────────────────────────────────────────────────────
# 6. API UPLOAD
# ─────────────────────────────────────────────────────────────────
class TestAPIUpload:

    def test_upload_requires_auth(self, client):
        """POST /api/upload without session returns 401."""
        resp = client.post('/api/upload')
        assert resp.status_code == 401

    @patch('app.get_redis_client')
    @patch('app.get_s3_client')
    def test_upload_success(self, mock_s3, mock_redis, auth_client, tmp_path):
        """Valid video upload dispatches job and returns 202."""
        mock_s3_inst = MagicMock()
        mock_s3.return_value = mock_s3_inst
        mock_redis_inst = MagicMock()
        mock_redis.return_value = mock_redis_inst

        resp = auth_client.post(
            '/api/upload',
            data={'video': (BytesIO(b'\x00' * 1024), 'clip.mp4', 'video/mp4')},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 202
        data = resp.get_json()
        assert 'job_id' in data
        assert data['status'] == 'pending'

    @patch('app.get_redis_client')
    @patch('app.get_s3_client')
    def test_upload_increments_counter(self, mock_s3, mock_redis, auth_client, app, sample_user):
        """Successful upload must increment monthly_uploads_used."""
        mock_s3.return_value = MagicMock()
        mock_redis.return_value = MagicMock()

        with app.app_context():
            user = db.session.merge(sample_user)
            before = user.subscription.monthly_uploads_used

        auth_client.post(
            '/api/upload',
            data={'video': (BytesIO(b'\x00' * 512), 'test.mp4', 'video/mp4')},
            content_type='multipart/form-data',
        )

        with app.app_context():
            user = db.session.merge(sample_user)
            after = user.subscription.monthly_uploads_used
        assert after == before + 1

    def test_upload_no_file_returns_400(self, auth_client):
        """Upload with no file attached returns 400."""
        resp = auth_client.post('/api/upload', data={}, content_type='multipart/form-data')
        assert resp.status_code == 400

    def test_upload_unsupported_extension_returns_415(self, auth_client):
        """Uploading a .txt file returns 415 Unsupported Media."""
        resp = auth_client.post(
            '/api/upload',
            data={'video': (BytesIO(b'data'), 'file.txt', 'text/plain')},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 415

    @patch('app.get_redis_client')
    @patch('app.get_s3_client')
    def test_upload_denied_at_limit(self, mock_s3, mock_redis, auth_client, app, sample_user):
        """Upload returns 429 when monthly limit is reached."""
        mock_s3.return_value = MagicMock()
        mock_redis.return_value = MagicMock()

        with app.app_context():
            user = db.session.merge(sample_user)
            user.subscription.monthly_uploads_used = 3
            db.session.commit()

        resp = auth_client.post(
            '/api/upload',
            data={'video': (BytesIO(b'\x00' * 512), 'x.mp4', 'video/mp4')},
            content_type='multipart/form-data',
        )
        assert resp.status_code == 429


# ─────────────────────────────────────────────────────────────────
# 7. API STATUS
# ─────────────────────────────────────────────────────────────────
class TestAPIStatus:

    def test_status_requires_auth(self, client):
        """GET /api/status/<id> without session returns 401."""
        resp = client.get(f'/api/status/{uuid.uuid4()}')
        assert resp.status_code == 401

    @patch('app.get_redis_client')
    def test_status_returns_job_info(self, mock_redis, auth_client, app, sample_user, sample_job):
        """GET /api/status/<job_id> returns job_id, status, progress."""
        mock_redis.return_value.get.return_value = None
        with app.app_context():
            job = db.session.merge(sample_job)
            job_id = job.job_id
        resp = auth_client.get(f'/api/status/{job_id}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'job_id' in data
        assert 'status' in data
        assert 'progress' in data

    def test_status_unknown_job_returns_404(self, auth_client):
        """GET /api/status for a non-existent job_id returns 404."""
        resp = auth_client.get(f'/api/status/non-existent-id')
        assert resp.status_code == 404


# ─────────────────────────────────────────────────────────────────
# 8. API DOWNLOAD
# ─────────────────────────────────────────────────────────────────
class TestAPIDownload:

    def test_download_requires_auth(self, client):
        """GET /api/download/<id> without session returns 401."""
        resp = client.get(f'/api/download/{uuid.uuid4()}')
        assert resp.status_code == 401

    @patch('app.get_s3_client')
    def test_download_done_job(self, mock_s3, auth_client, app, sample_user, sample_job):
        """GET /api/download for a 'done' job returns presigned URL."""
        mock_s3.return_value.generate_presigned_url.return_value = 'https://r2.example.com/protected/test.mp4?sig=abc'
        with app.app_context():
            job = db.session.merge(sample_job)
            job_id = job.job_id
        resp = auth_client.get(f'/api/download/{job_id}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'download_url' in data
        assert 'filename' in data

    def test_download_pending_job_returns_404(self, auth_client, app, sample_user):
        """GET /api/download for a pending job returns 404."""
        with app.app_context():
            job = ProtectionJob(
                job_id=str(uuid.uuid4()), user_id=sample_user.id,
                status='pending', original_filename='vid.mp4',
            )
            db.session.add(job)
            db.session.commit()
            job_id = job.job_id
        resp = auth_client.get(f'/api/download/{job_id}')
        assert resp.status_code == 404

    def test_download_nonexistent_job_returns_404(self, auth_client):
        """GET /api/download for a completely unknown job returns 404."""
        resp = auth_client.get(f'/api/download/fake-job-id')
        assert resp.status_code == 404

    @patch('app.get_s3_client')
    def test_download_logs_entry(self, mock_s3, auth_client, app, sample_user, sample_job):
        """Successful download must create a DownloadLog record."""
        mock_s3.return_value.generate_presigned_url.return_value = 'https://r2.example.com/file'
        with app.app_context():
            job = db.session.merge(sample_job)
            job_id = job.job_id
        auth_client.get(f'/api/download/{job_id}')
        with app.app_context():
            job = ProtectionJob.query.filter_by(job_id=job_id).first()
            assert DownloadLog.query.filter_by(job_id=job.id).count() == 1


# ─────────────────────────────────────────────────────────────────
# 9. API DELETE
# ─────────────────────────────────────────────────────────────────
class TestAPIDeleteVideo:

    @patch('app.get_s3_client')
    def test_delete_video_success(self, mock_s3, auth_client, app, sample_user, sample_job):
        """DELETE /api/video/<job_id> removes job from DB and returns 200."""
        mock_s3.return_value = MagicMock()
        with app.app_context():
            job = db.session.merge(sample_job)
            job_id = job.job_id
        resp = auth_client.delete(f'/api/video/{job_id}')
        assert resp.status_code == 200
        with app.app_context():
            assert ProtectionJob.query.filter_by(job_id=job_id).first() is None

    def test_delete_requires_auth(self, client):
        """DELETE /api/video without session returns 401."""
        resp = client.delete(f'/api/video/{uuid.uuid4()}')
        assert resp.status_code == 401

    def test_delete_nonexistent_returns_404(self, auth_client):
        """DELETE /api/video for unknown job returns 404."""
        resp = auth_client.delete(f'/api/video/does-not-exist')
        assert resp.status_code == 404

    @patch('app.get_s3_client')
    def test_delete_also_removes_download_logs(self, mock_s3, auth_client, app, sample_user, sample_job):
        """Deleting a job must cascade-delete associated DownloadLog rows."""
        mock_s3.return_value = MagicMock()
        with app.app_context():
            job = db.session.merge(sample_job)
            log = DownloadLog(job_id=job.id, user_id=sample_user.id)
            db.session.add(log)
            db.session.commit()
            job_id = job.job_id

        auth_client.delete(f'/api/video/{job_id}')

        with app.app_context():
            assert DownloadLog.query.count() == 0


# ─────────────────────────────────────────────────────────────────
# 10. WEBHOOK
# ─────────────────────────────────────────────────────────────────
class TestWebhook:

    def test_webhook_updates_job_to_done(self, client, app, sample_user, sample_job):
        """Valid webhook with secret must update job status to 'done'."""
        with app.app_context():
            job = db.session.merge(sample_job)
            job.status = 'processing'
            db.session.commit()
            job_id = job.job_id

        resp = client.post('/api/internal/webhook', json={
            'webhook_secret': 'test-webhook-secret',
            'task_id': job_id,
            'status': 'done',
            'metrics': {},
        })
        assert resp.status_code == 200
        with app.app_context():
            updated = ProtectionJob.query.filter_by(job_id=job_id).first()
            assert updated.status == 'done'

    def test_webhook_wrong_secret_returns_403(self, client, sample_job, app):
        """Webhook with wrong secret must return 403."""
        with app.app_context():
            job = db.session.merge(sample_job)
            job_id = job.job_id
        resp = client.post('/api/internal/webhook', json={
            'webhook_secret': 'WRONG-SECRET',
            'task_id': job_id,
            'status': 'done',
            'metrics': {},
        })
        assert resp.status_code == 403

    def test_webhook_unknown_job_returns_404(self, client):
        """Webhook for an unknown job_id must return 404."""
        resp = client.post('/api/internal/webhook', json={
            'webhook_secret': 'test-webhook-secret',
            'task_id': 'non-existent-job',
            'status': 'done',
            'metrics': {},
        })
        assert resp.status_code == 404

    def test_webhook_error_status(self, client, app, sample_user, sample_job):
        """Webhook with status='error' must update job and store error message."""
        with app.app_context():
            job = db.session.merge(sample_job)
            job.status = 'processing'
            db.session.commit()
            job_id = job.job_id

        resp = client.post('/api/internal/webhook', json={
            'webhook_secret': 'test-webhook-secret',
            'task_id': job_id,
            'status': 'error',
            'error_message': 'GPU out of memory',
            'metrics': {},
        })
        assert resp.status_code == 200
        with app.app_context():
            updated = ProtectionJob.query.filter_by(job_id=job_id).first()
            assert updated.status == 'error'
            assert updated.error_message == 'GPU out of memory'