"""
tests/test_security.py — Security Tests for VigilantVideo
"""
import pytest
import hmac
from unittest.mock import patch
from app import verify_redis_url, limiter
from flask import Flask

class TestSecurityAndRateLimits:

    def test_login_rate_limiting(self, client, sample_user):
        """POST /api/auth/login is rate limited after 5 requests per minute."""
        # Reset the limiter for this test
        limiter.reset()
        
        # Make 5 successful/failed requests (all within the limit)
        for i in range(5):
            resp = client.post('/api/auth/login', json={
                'username': 'testuser',
                'password': 'wrongpassword'  # 401 is fine, just hitting the route
            })
            assert resp.status_code == 401

        # The 6th request must trigger a 429 Too Many Requests
        resp = client.post('/api/auth/login', json={
            'username': 'testuser',
            'password': 'password123'
        })
        assert resp.status_code == 429
        assert resp.get_json()['message'] == "Too many requests. Please try again later."
        limiter.reset()

    def test_register_rate_limiting(self, client):
        """POST /api/auth/register is rate limited after 5 requests per minute."""
        limiter.reset()
        
        # Make 5 register attempts
        for i in range(5):
            resp = client.post('/api/auth/register', json={
                'username': f'limuser{i}',
                'password': 'password123'
            })
            assert resp.status_code == 201

        # 6th request must trigger a 429
        resp = client.post('/api/auth/register', json={
            'username': 'limuser_exceeded',
            'password': 'password123'
        })
        assert resp.status_code == 429
        limiter.reset()

    def test_webhook_unauthorized_with_wrong_secret(self, client):
        """POST /api/internal/webhook with invalid webhook_secret returns 403."""
        resp = client.post('/api/internal/webhook', json={
            'task_id': 'some-job-id',
            'status': 'done',
            'webhook_secret': 'wrong-secret'
        })
        assert resp.status_code == 403
        assert resp.get_json() == {"error": "Unauthorized"}

    def test_webhook_authorized_with_correct_secret(self, client, app):
        """POST /api/internal/webhook with correct webhook_secret doesn't return 403."""
        # Correct secret but job doesn't exist, should return 404 (Unauthorized bypass verified)
        resp = client.post('/api/internal/webhook', json={
            'task_id': 'nonexistent-job-id',
            'status': 'done',
            'webhook_secret': 'test-webhook-secret'
        })
        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Job not found"}

    def test_verify_redis_url_localhost_no_password(self, app):
        """verify_redis_url accepts localhost/127.0.0.1 without a password."""
        with app.app_context():
            # Mock configuration
            app.config['REDIS_URL'] = 'redis://127.0.0.1:6379'
            assert verify_redis_url(app) is True

    def test_verify_redis_url_remote_no_password_fails(self, app):
        """verify_redis_url rejects remote hosts without a password."""
        with app.app_context():
            app.config['REDIS_URL'] = 'redis://optimal-pangolin.upstash.io:6379'
            assert verify_redis_url(app) is False

    def test_verify_redis_url_remote_with_password_passes(self, app):
        """verify_redis_url accepts remote hosts with a password."""
        with app.app_context():
            app.config['REDIS_URL'] = 'rediss://default:password123@optimal-pangolin.upstash.io:6379'
            assert verify_redis_url(app) is True
