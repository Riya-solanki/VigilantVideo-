"""
tests/test_unit.py — Unit Tests for VigilantVideo
Tests individual functions and model methods in isolation.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from werkzeug.security import generate_password_hash, check_password_hash

from models import (
    db, User, Subscription, ProtectionJob,
    UsageLimit, WatermarkActivation, DownloadLog
)


# ─────────────────────────────────────────────────────────────────
# 1. USER MODEL
# ─────────────────────────────────────────────────────────────────
class TestUserModel:

    def test_user_to_dict_contains_required_keys(self, app, sample_user):
        """User.to_dict() must expose id, username, plan_tier, is_active, initials."""
        with app.app_context():
            user = db.session.merge(sample_user)
            d = user.to_dict()
        assert 'id' in d
        assert 'username' in d
        assert 'plan_tier' in d
        assert 'is_active' in d
        assert 'initials' in d

    def test_user_initials_are_uppercase(self, app, sample_user):
        """Initials must be the first 2 chars of username in upper-case."""
        with app.app_context():
            user = db.session.merge(sample_user)
            assert user.to_dict()['initials'] == 'TE'

    def test_user_password_hash_is_not_plaintext(self, app, sample_user):
        """password_hash must never equal the raw password."""
        with app.app_context():
            user = db.session.merge(sample_user)
            assert user.password_hash != 'password123'

    def test_check_password_hash_correct(self, app, sample_user):
        """Correct password must pass check_password_hash."""
        with app.app_context():
            user = db.session.merge(sample_user)
            assert check_password_hash(user.password_hash, 'password123')

    def test_check_password_hash_wrong(self, app, sample_user):
        """Wrong password must fail check_password_hash."""
        with app.app_context():
            user = db.session.merge(sample_user)
            assert not check_password_hash(user.password_hash, 'wrongpassword')

    def test_user_default_plan_tier_is_free(self, app, sample_user):
        """Newly registered user must be on the free plan."""
        with app.app_context():
            user = db.session.merge(sample_user)
            assert user.to_dict()['plan_tier'] == 'free'

    def test_user_is_active_by_default(self, app, sample_user):
        """New users must be active by default."""
        with app.app_context():
            user = db.session.merge(sample_user)
            assert user.is_active is True

    def test_get_monthly_uploads_used(self, app, sample_user):
        """get_monthly_uploads_used should reflect subscription counter."""
        with app.app_context():
            user = db.session.merge(sample_user)
            assert user.get_monthly_uploads_used() == 0

    def test_get_upload_limit_free(self, app, sample_user):
        """Free plan upload limit must be 3."""
        with app.app_context():
            user = db.session.merge(sample_user)
            assert user.get_upload_limit() == 3

    def test_user_repr(self, app, sample_user):
        """User __repr__ must include the username."""
        with app.app_context():
            user = db.session.merge(sample_user)
            assert 'testuser' in repr(user)


# ─────────────────────────────────────────────────────────────────
# 2. PROTECTION JOB MODEL
# ─────────────────────────────────────────────────────────────────
class TestProtectionJobModel:

    def test_size_display_bytes(self, app, sample_user):
        """size_display should return bytes for small files."""
        with app.app_context():
            job = ProtectionJob(
                job_id='test-job-1', user_id=sample_user.id,
                status='pending', original_filename='video.mp4',
                original_size_bytes=512,
            )
            db.session.add(job)
            db.session.commit()
            assert 'B' in job.size_display()

    def test_size_display_megabytes(self, app, sample_job):
        """size_display should show MB for a 1 MB file."""
        with app.app_context():
            job = db.session.merge(sample_job)
            assert 'MB' in job.size_display()

    def test_size_display_none(self, app, sample_user):
        """size_display returns '—' when size is not set."""
        with app.app_context():
            job = ProtectionJob(
                job_id='test-job-2', user_id=sample_user.id,
                status='pending', original_filename='v.mp4',
            )
            db.session.add(job)
            db.session.commit()
            assert job.size_display() == '—'

    def test_protection_job_to_dict_keys(self, app, sample_job):
        """to_dict must contain job_id, status, original_filename, size."""
        with app.app_context():
            job = db.session.merge(sample_job)
            d = job.to_dict()
        for key in ('job_id', 'status', 'original_filename', 'size'):
            assert key in d

    def test_protection_job_repr(self, app, sample_job):
        """ProtectionJob repr must include job_id."""
        with app.app_context():
            job = db.session.merge(sample_job)
            assert job.job_id in repr(job)

    def test_job_status_default_pending(self, app, sample_user):
        """A new ProtectionJob must default to 'pending' status."""
        with app.app_context():
            job = ProtectionJob(
                job_id='test-job-3', user_id=sample_user.id,
                original_filename='clip.mp4',
            )
            db.session.add(job)
            db.session.commit()
            assert job.status == 'pending'


# ─────────────────────────────────────────────────────────────────
# 3. WATERMARK ACTIVATION MODEL
# ─────────────────────────────────────────────────────────────────
class TestWatermarkActivationModel:

    def test_from_result_creates_activation(self, app, sample_job):
        """WatermarkActivation.from_result must map result dict to model fields."""
        with app.app_context():
            job = db.session.merge(sample_job)
            result = {
                'watermark_text': 'VigilantVideo',
                'watermark_strength': 0.15,
                'noise_strength': 0.04,
                'freq_perturbation_strength': 0.03,
                'frames_processed': 120,
                'duration_seconds': 5.2,
                'protections_applied': ['watermark', 'noise'],
                'models_used': ['adversarial_v2'],
            }
            activation = WatermarkActivation.from_result(job.id, result)
            db.session.add(activation)
            db.session.commit()

            assert activation.watermark_text == 'VigilantVideo'
            assert activation.frames_processed == 120
            assert activation.watermark_strength == 0.15

    def test_protections_applied_json_roundtrip(self, app, sample_job):
        """protections_applied property must serialize/deserialize via JSON."""
        with app.app_context():
            job = db.session.merge(sample_job)
            activation = WatermarkActivation(job_id=job.id)
            activation.protections_applied = ['watermark', 'noise', 'freq']
            db.session.add(activation)
            db.session.commit()
            assert activation.protections_applied == ['watermark', 'noise', 'freq']

    def test_models_used_json_roundtrip(self, app, sample_job):
        """models_used property must serialize/deserialize via JSON."""
        with app.app_context():
            job = db.session.merge(sample_job)
            activation = WatermarkActivation(job_id=job.id)
            activation.models_used = ['adversarial_v2', 'noise_net']
            db.session.add(activation)
            db.session.commit()
            assert activation.models_used == ['adversarial_v2', 'noise_net']

    def test_empty_protections_returns_list(self, app, sample_job):
        """protections_applied on a new activation must return empty list."""
        with app.app_context():
            job = db.session.merge(sample_job)
            activation = WatermarkActivation(job_id=job.id)
            assert activation.protections_applied == []


# ─────────────────────────────────────────────────────────────────
# 4. SUBSCRIPTION MODEL
# ─────────────────────────────────────────────────────────────────
class TestSubscriptionModel:

    def test_subscription_defaults(self, app, sample_user):
        """New subscription must default to free plan, 0 uploads."""
        with app.app_context():
            user = db.session.merge(sample_user)
            sub = user.subscription
            assert sub.plan == 'free'
            assert sub.monthly_uploads_used == 0
            assert sub.is_active is True

    def test_subscription_to_dict(self, app, sample_user):
        """Subscription.to_dict must contain plan and monthly_uploads_used."""
        with app.app_context():
            user = db.session.merge(sample_user)
            d = user.subscription.to_dict()
            assert 'plan' in d
            assert 'monthly_uploads_used' in d

    def test_subscription_repr(self, app, sample_user):
        """Subscription repr must include user_id."""
        with app.app_context():
            user = db.session.merge(sample_user)
            assert str(user.id) in repr(user.subscription)


# ─────────────────────────────────────────────────────────────────
# 5. USAGE LIMIT MODEL
# ─────────────────────────────────────────────────────────────────
class TestUsageLimitModel:

    def test_free_plan_exists(self, app):
        """Free plan must be seeded in usage_limits."""
        with app.app_context():
            limit = UsageLimit.query.get('free')
            assert limit is not None
            assert limit.max_videos_per_month == 3

    def test_pro_plan_exists(self, app):
        """Pro plan must be seeded in usage_limits."""
        with app.app_context():
            limit = UsageLimit.query.get('pro')
            assert limit is not None
            assert limit.max_videos_per_month == 50

    def test_usage_limit_to_dict(self, app):
        """UsageLimit.to_dict must include plan and limits."""
        with app.app_context():
            limit = UsageLimit.query.get('free')
            d = limit.to_dict()
            assert d['plan'] == 'free'
            assert 'max_videos_per_month' in d
            assert 'max_file_size_bytes' in d

    def test_usage_limit_repr(self, app):
        """UsageLimit repr must mention plan name."""
        with app.app_context():
            limit = UsageLimit.query.get('free')
            assert 'free' in repr(limit)


# ─────────────────────────────────────────────────────────────────
# 6. _time_ago HELPER
# ─────────────────────────────────────────────────────────────────
class TestTimeAgoHelper:

    def test_time_ago_just_now(self, app):
        """Less than 60 seconds ago must return 'Just now'."""
        from app import _time_ago
        with app.app_context():
            result = _time_ago(datetime.utcnow() - timedelta(seconds=30))
            assert result == 'Just now'

    def test_time_ago_minutes(self, app):
        """Between 1–59 minutes ago must return 'X min ago'."""
        from app import _time_ago
        with app.app_context():
            result = _time_ago(datetime.utcnow() - timedelta(minutes=5))
            assert 'min ago' in result

    def test_time_ago_hours(self, app):
        """Between 1–23 hours ago must return 'X hr ago'."""
        from app import _time_ago
        with app.app_context():
            result = _time_ago(datetime.utcnow() - timedelta(hours=3))
            assert 'hr ago' in result

    def test_time_ago_days(self, app):
        """More than 1 day ago must return 'X days ago'."""
        from app import _time_ago
        with app.app_context():
            result = _time_ago(datetime.utcnow() - timedelta(days=2))
            assert 'days ago' in result

    def test_time_ago_none_returns_empty(self, app):
        """None input must return an empty string."""
        from app import _time_ago
        with app.app_context():
            assert _time_ago(None) == ''


# ─────────────────────────────────────────────────────────────────
# 7. check_upload_limit HELPER
# ─────────────────────────────────────────────────────────────────
class TestCheckUploadLimit:

    def test_allow_when_under_limit(self, app, sample_user):
        """Upload allowed when monthly_uploads_used < limit."""
        from app import check_upload_limit
        with app.app_context():
            user = db.session.merge(sample_user)
            allowed, msg = check_upload_limit(user)
            assert allowed is True

    def test_deny_when_at_limit(self, app, sample_user):
        """Upload denied when monthly_uploads_used == max_videos_per_month."""
        from app import check_upload_limit
        with app.app_context():
            user = db.session.merge(sample_user)
            user.subscription.monthly_uploads_used = 3
            db.session.commit()
            allowed, msg = check_upload_limit(user)
            assert allowed is False
            assert 'limit' in msg.lower()

    def test_deny_when_no_subscription(self, app, sample_user):
        """Upload denied if user has no subscription."""
        from app import check_upload_limit
        with app.app_context():
            user = db.session.merge(sample_user)
            db.session.delete(user.subscription)
            db.session.commit()
            user2 = db.session.get(User, user.id)
            allowed, msg = check_upload_limit(user2)
            assert allowed is False

    def test_pro_plan_higher_limit(self, app, sample_user):
        """Pro plan user should have a higher monthly limit."""
        from app import check_upload_limit
        with app.app_context():
            user = db.session.merge(sample_user)
            user.subscription.plan = 'pro'
            user.subscription.monthly_uploads_used = 49
            db.session.commit()
            allowed, msg = check_upload_limit(user)
            assert allowed is True