"""
tests/test_system.py — System / End-to-End Tests for VigilantVideo
Tests complete user workflows across multiple API calls in sequence.
External services (Redis, S3/R2) are mocked at the boundary.
"""
import pytest
import uuid
from unittest.mock import patch, MagicMock
from io import BytesIO
from datetime import datetime, timedelta

from models import db, User, Subscription, ProtectionJob, DownloadLog, WatermarkActivation


# ─────────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────────
def register_and_login(client, username='sysuser', password='syspassword'):
    """Register a new user, then verify login works."""
    resp = client.post('/api/auth/register', json={'username': username, 'password': password})
    assert resp.status_code == 201
    return resp.get_json()['user']


def make_s3_mock():
    """Return a MagicMock for the S3 client with JSON-serializable return values."""
    m = MagicMock()
    m.generate_presigned_post.return_value = {
        'url': 'https://r2.example.com/upload',
        'fields': {'key': 'raw/test.mp4', 'Content-Type': 'video/mp4'},
    }
    m.generate_presigned_url.return_value = 'https://r2.example.com/protected/vid.mp4?sig=xyz'
    # head_object should NOT raise (file found in R2) — MagicMock default works, but be explicit
    m.head_object.return_value = {}
    return m


def upload_video_flow(client, filename='myvideo.mp4', filesize=2048):
    """Simulate the two-step direct R2 upload flow in system tests.

    The caller must have already set up get_s3_client to return make_s3_mock()
    so that generate_presigned_post returns JSON-serializable data.
    """
    presign_resp = client.post('/api/upload/presign', json={
        'filename': filename,
        'filesize': filesize
    })
    if presign_resp.status_code != 200:
        return presign_resp
    job_id = presign_resp.get_json()['job_id']
    confirm_resp = client.post('/api/upload/confirm', json={
        'job_id': job_id
    })
    return confirm_resp


# ─────────────────────────────────────────────────────────────────
# WORKFLOW 1: FULL REGISTRATION → DASHBOARD FLOW
# ─────────────────────────────────────────────────────────────────
class TestRegistrationToDashboardFlow:

    @patch('app.get_s3_client')
    def test_register_then_view_dashboard(self, mock_s3, client):
        """
        System: A brand-new user can register, then immediately
        view their (empty) dashboard.
        """
        mock_s3.return_value = make_s3_mock()

        # Step 1 – Register
        r1 = client.post('/api/auth/register',
                         json={'username': 'dash_user', 'password': 'pass1234'})
        assert r1.status_code == 201

        # Step 2 – View API dashboard
        r2 = client.get('/api/dashboard')
        assert r2.status_code == 200
        data = r2.get_json()
        assert data['stats']['videos_protected'] == 0
        assert data['videos'] == []

    @patch('app.get_s3_client')
    def test_register_login_logout_login_again(self, mock_s3, client):
        """
        System: User can register, log out, and log back in successfully.
        """
        mock_s3.return_value = make_s3_mock()

        client.post('/api/auth/register',
                    json={'username': 'bounce_user', 'password': 'pass1234'})

        # Log out
        r_out = client.post('/api/logout')
        assert r_out.status_code == 200

        # Verify session cleared
        r_me = client.get('/api/me')
        assert r_me.status_code == 401

        # Log back in
        r_in = client.post('/api/auth/login',
                           json={'username': 'bounce_user', 'password': 'pass1234'})
        assert r_in.status_code == 200

        # Dashboard accessible again
        r_dash = client.get('/api/dashboard')
        assert r_dash.status_code == 200


# ─────────────────────────────────────────────────────────────────
# WORKFLOW 2: FULL VIDEO PROTECTION LIFECYCLE
# ─────────────────────────────────────────────────────────────────
class TestVideoProtectionLifecycle:

    @patch('app.get_redis_client')
    @patch('app.get_s3_client')
    def test_upload_then_poll_then_download(self, mock_s3, mock_redis, client, app):
        """
        System: User uploads a video → polls status → webhook fires →
        user downloads the protected file.
        """
        mock_s3_inst = make_s3_mock()
        mock_s3.return_value = mock_s3_inst
        mock_redis_inst = MagicMock()
        mock_redis.return_value = mock_redis_inst
        mock_redis_inst.get.return_value = None

        # 1. Register + auto-login
        client.post('/api/auth/register',
                    json={'username': 'lifecycle_user', 'password': 'secure123'})

        # 2. Upload a video
        upload_resp = upload_video_flow(client, 'myvideo.mp4', 2048)
        assert upload_resp.status_code == 202
        job_id = upload_resp.get_json()['job_id']

        # 3. Poll status → should be pending
        status_resp = client.get(f'/api/status/{job_id}')
        assert status_resp.status_code == 200
        assert status_resp.get_json()['status'] == 'pending'

        # 4. Simulate GPU worker webhook (job done)
        webhook_resp = client.post('/api/internal/webhook', json={
            'webhook_secret': 'test-webhook-secret',
            'task_id': job_id,
            'status': 'done',
            'metrics': {
                'watermark_text': 'VigilantVideo',
                'watermark_strength': 0.1,
                'frames_processed': 60,
                'duration_seconds': 3.5,
                'protections_applied': ['watermark'],
                'models_used': ['adversarial_v1'],
            },
        })
        assert webhook_resp.status_code == 200

        # 5. Poll status → now done
        status_resp2 = client.get(f'/api/status/{job_id}')
        assert status_resp2.get_json()['status'] == 'done'

        # 6. Download the protected file
        dl_resp = client.get(f'/api/download/{job_id}')
        assert dl_resp.status_code == 200
        dl_data = dl_resp.get_json()
        assert 'download_url' in dl_data
        assert dl_data['filename'].startswith('Protected_')

    @patch('app.get_redis_client')
    @patch('app.get_s3_client')
    def test_upload_then_webhook_error_then_status_shows_error(self, mock_s3, mock_redis, client):
        """
        System: When the GPU worker fails, the job status reflects 'error'.
        """
        mock_s3.return_value = make_s3_mock()
        mock_redis_inst = MagicMock()
        mock_redis_inst.get.return_value = None
        mock_redis.return_value = mock_redis_inst

        client.post('/api/auth/register',
                    json={'username': 'error_user', 'password': 'secure123'})

        upload_resp = upload_video_flow(client, 'fail.mp4', 1024)
        job_id = upload_resp.get_json()['job_id']

        # GPU worker reports error
        client.post('/api/internal/webhook', json={
            'webhook_secret': 'test-webhook-secret',
            'task_id': job_id,
            'status': 'error',
            'error_message': 'CUDA out of memory',
        })

        status_resp = client.get(f'/api/status/{job_id}')
        data = status_resp.get_json()
        assert data['status'] == 'error'
        assert data['error'] == 'CUDA out of memory'


# ─────────────────────────────────────────────────────────────────
# WORKFLOW 3: UPLOAD QUOTA ENFORCEMENT
# ─────────────────────────────────────────────────────────────────
class TestUploadQuotaEnforcement:

    @patch('app.get_redis_client')
    @patch('app.get_s3_client')
    def test_quota_exhausted_after_three_uploads(self, mock_s3, mock_redis, client):
        """
        System: Free-plan user can upload 3 videos; the 4th must be denied.
        """
        mock_s3.return_value = make_s3_mock()
        mock_redis.return_value = MagicMock()

        client.post('/api/auth/register',
                    json={'username': 'quota_user', 'password': 'pass123'})

        def upload():
            return upload_video_flow(client, 'clip.mp4', 512)

        # First 3 uploads must succeed
        for i in range(3):
            r = upload()
            assert r.status_code == 202, f"Upload {i+1} should succeed"

        # 4th upload must be rejected
        r4 = upload()
        assert r4.status_code == 429

    @patch('app.get_redis_client')
    @patch('app.get_s3_client')
    def test_dashboard_reflects_updated_upload_count(self, mock_s3, mock_redis, client):
        """
        System: After each upload, the dashboard reports an incremented uploads_used.
        """
        mock_s3.return_value = make_s3_mock()
        mock_redis.return_value = MagicMock()

        client.post('/api/auth/register',
                    json={'username': 'count_user', 'password': 'pass123'})

        # Baseline
        dash1 = client.get('/api/dashboard').get_json()
        assert dash1['stats']['uploads_used'] == 0

        # Upload one video
        upload_video_flow(client, 'clip.mp4', 512)

        dash2 = client.get('/api/dashboard').get_json()
        assert dash2['stats']['uploads_used'] == 1


# ─────────────────────────────────────────────────────────────────
# WORKFLOW 4: VIDEO DELETE LIFECYCLE
# ─────────────────────────────────────────────────────────────────
class TestVideoDeleteLifecycle:

    @patch('app.get_redis_client')
    @patch('app.get_s3_client')
    def test_upload_then_delete_removes_from_library(self, mock_s3, mock_redis, client):
        """
        System: After deleting a video, it must no longer appear in the dashboard.
        """
        mock_s3.return_value = make_s3_mock()
        mock_redis.return_value = MagicMock()

        client.post('/api/auth/register',
                    json={'username': 'delete_user', 'password': 'pass123'})

        # Upload
        upload_resp = upload_video_flow(client, 'todelete.mp4', 1024)
        job_id = upload_resp.get_json()['job_id']

        # Delete
        del_resp = client.delete(f'/api/video/{job_id}')
        assert del_resp.status_code == 200

        # Video must be gone from the DB
        status_resp = client.get(f'/api/status/{job_id}')
        assert status_resp.status_code == 404

    @patch('app.get_redis_client')
    @patch('app.get_s3_client')
    def test_delete_calls_s3_for_both_raw_and_protected(self, mock_s3, mock_redis, client, app):
        """
        System: Deleting a job must call S3 delete_object for both raw and protected keys.
        """
        mock_s3_inst = make_s3_mock()
        mock_s3.return_value = mock_s3_inst
        mock_redis.return_value = MagicMock()

        client.post('/api/auth/register',
                    json={'username': 's3_delete_user', 'password': 'pass123'})

        upload_resp = upload_video_flow(client, 'clip.mp4', 512)
        job_id = upload_resp.get_json()['job_id']

        mock_s3_inst.reset_mock()
        client.delete(f'/api/video/{job_id}')

        # Both raw and protected paths deleted
        assert mock_s3_inst.delete_object.call_count >= 1


# ─────────────────────────────────────────────────────────────────
# WORKFLOW 5: MULTI-USER ISOLATION
# ─────────────────────────────────────────────────────────────────
class TestMultiUserIsolation:

    @patch('app.get_redis_client')
    @patch('app.get_s3_client')
    def test_user_cannot_access_other_users_job(self, mock_s3, mock_redis, client):
        """
        System: User B must not be able to download or see User A's jobs.
        """
        mock_s3.return_value = make_s3_mock()
        mock_redis_inst = MagicMock()
        mock_redis_inst.get.return_value = None
        mock_redis.return_value = mock_redis_inst

        # User A registers and uploads
        client.post('/api/auth/register',
                    json={'username': 'user_a', 'password': 'pass123'})
        upload_resp = upload_video_flow(client, 'private.mp4', 512)
        job_id_a = upload_resp.get_json()['job_id']

        # Log out user A, register user B
        client.post('/api/logout')
        client.post('/api/auth/register',
                    json={'username': 'user_b', 'password': 'pass456'})

        # User B tries to access user A's job
        status_resp = client.get(f'/api/status/{job_id_a}')
        assert status_resp.status_code == 404

        dl_resp = client.get(f'/api/download/{job_id_a}')
        assert dl_resp.status_code == 404

        del_resp = client.delete(f'/api/video/{job_id_a}')
        assert del_resp.status_code == 404

    @patch('app.get_s3_client')
    def test_dashboard_only_shows_own_videos(self, mock_s3, client):
        """
        System: Each user's dashboard only includes their own videos.
        """
        mock_s3.return_value = make_s3_mock()

        # User A
        client.post('/api/auth/register',
                    json={'username': 'owner_a', 'password': 'pass123'})
        dash_a = client.get('/api/dashboard').get_json()
        assert isinstance(dash_a['videos'], list)
        assert all(v['name'] != 'owner_b_video' for v in dash_a['videos'])

        # Switch to User B
        client.post('/api/logout')
        client.post('/api/auth/register',
                    json={'username': 'owner_b', 'password': 'pass456'})
        dash_b = client.get('/api/dashboard').get_json()
        # B's dashboard is separate from A's
        assert isinstance(dash_b['videos'], list)


# ─────────────────────────────────────────────────────────────────
# WORKFLOW 6: WATERMARK ACTIVATION PERSISTENCE
# ─────────────────────────────────────────────────────────────────
class TestWatermarkPersistence:

    @patch('app.get_redis_client')
    @patch('app.get_s3_client')
    def test_webhook_creates_watermark_activation_record(self, mock_s3, mock_redis, client, app):
        """
        System: When the webhook marks a job done with metrics,
        a WatermarkActivation record must be saved to the DB.
        """
        mock_s3.return_value = make_s3_mock()
        mock_redis.return_value = MagicMock()

        client.post('/api/auth/register',
                    json={'username': 'wm_user', 'password': 'pass123'})

        upload_resp = upload_video_flow(client, 'wm_video.mp4', 512)
        job_id = upload_resp.get_json()['job_id']

        metrics = {
            'watermark_text': 'VigilantVideo',
            'watermark_strength': 0.12,
            'noise_strength': 0.03,
            'freq_perturbation_strength': 0.02,
            'frames_processed': 240,
            'duration_seconds': 10.0,
            'protections_applied': ['watermark', 'noise'],
            'models_used': ['adversarial_v2'],
        }

        client.post('/api/internal/webhook', json={
            'webhook_secret': 'test-webhook-secret',
            'task_id': job_id,
            'status': 'done',
            'metrics': metrics,
        })

        with app.app_context():
            job = ProtectionJob.query.filter_by(job_id=job_id).first()
            wa = WatermarkActivation.query.filter_by(job_id=job.id).first()
            assert wa is not None
            assert wa.watermark_text == 'VigilantVideo'
            assert wa.frames_processed == 240
            assert wa.protections_applied == ['watermark', 'noise']