"""Unit tests for authentication, fail-closed production mode, HMAC signed URLs, and rate limiting."""

import os
import pathlib
import sys
import time
import unittest
from unittest.mock import MagicMock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT))

from app import auth
from app.config import settings
from fastapi import HTTPException


class TestAuth(unittest.TestCase):
    def setUp(self):
        self.orig_token = settings.access_token
        settings.access_token = "secret-test-token-123"

    def tearDown(self):
        settings.access_token = self.orig_token

    def test_signed_url_success(self):
        path = "/api/clips/42/download"
        query = auth.sign_url(path, expires_in_seconds=300)
        self.assertIn("exp=", query)
        self.assertIn("sig=", query)

        params = dict(item.split("=") for item in query.split("&"))
        exp = int(params["exp"])
        sig = params["sig"]

        self.assertTrue(auth.verify_signature(path, exp, sig))

    def test_signed_url_expired_rejected(self):
        path = "/api/clips/42/download"
        # Signed 10 seconds in the past
        query = auth.sign_url(path, expires_in_seconds=-10)
        params = dict(item.split("=") for item in query.split("&"))
        exp = int(params["exp"])
        sig = params["sig"]

        self.assertFalse(auth.verify_signature(path, exp, sig))

    def test_signed_url_tampered_path_rejected(self):
        path = "/api/clips/42/download"
        query = auth.sign_url(path, expires_in_seconds=300)
        params = dict(item.split("=") for item in query.split("&"))
        exp = int(params["exp"])
        sig = params["sig"]

        # Different path must fail
        self.assertFalse(auth.verify_signature("/api/clips/99/download", exp, sig))

    def test_require_token_with_valid_bearer(self):
        req = MagicMock()
        req.url.path = "/api/stats"
        req.client.host = "127.0.0.1"

        # Valid Bearer token does not raise
        auth.require_token(req, authorization="Bearer secret-test-token-123")

    def test_require_token_with_invalid_token(self):
        req = MagicMock()
        req.url.path = "/api/stats"
        req.client.host = "127.0.0.1"

        with self.assertRaises(HTTPException) as ctx:
            auth.require_token(req, authorization="Bearer wrong-token")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_require_token_with_valid_hmac(self):
        req = MagicMock()
        path = "/api/clips/10/thumb"
        req.url.path = path
        req.client.host = "127.0.0.1"

        query = auth.sign_url(path, expires_in_seconds=300)
        params = dict(item.split("=") for item in query.split("&"))

        # Valid HMAC signature passes without needing raw access token
        auth.require_token(req, exp=int(params["exp"]), sig=params["sig"])

    def test_rate_limiting_triggers_429(self):
        req = MagicMock()
        req.client.host = "10.0.0.99"
        # Clear rate limit state for this IP
        auth._rate_limits.pop("10.0.0.99", None)

        # Send 5 requests under limit of 5
        for _ in range(5):
            auth.check_rate_limit(req, max_per_minute=5)

        # 6th request should trigger 429
        with self.assertRaises(HTTPException) as ctx:
            auth.check_rate_limit(req, max_per_minute=5)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Retry-After", ctx.exception.headers)

    def test_fail_closed_in_production_when_token_unset(self):
        settings.access_token = ""
        req = MagicMock()
        req.url.path = "/api/stats"
        req.client.host = "127.0.0.1"

        # Simulate production environment
        orig_env = os.environ.get("RAILWAY_ENVIRONMENT")
        try:
            os.environ["RAILWAY_ENVIRONMENT"] = "production"
            with self.assertRaises(HTTPException) as ctx:
                auth.require_token(req)
            self.assertEqual(ctx.exception.status_code, 500)
        finally:
            if orig_env is None:
                os.environ.pop("RAILWAY_ENVIRONMENT", None)
            else:
                os.environ["RAILWAY_ENVIRONMENT"] = orig_env


if __name__ == "__main__":
    unittest.main()


def test_wrong_tokens_lock_out_that_address_only():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import auth
    auth._bad_tokens.clear()
    c = TestClient(app)
    bad = {"Authorization": "Bearer nope", "X-Forwarded-For": "203.0.113.9"}
    codes = [c.get("/api/jobs", headers=bad).status_code for _ in range(21)]
    assert codes[:20] == [401] * 20 and codes[20] == 429
    ok = {"Authorization": "Bearer test-token", "X-Forwarded-For": "198.51.100.7"}
    assert c.get("/api/jobs", headers=ok).status_code == 200       # other visitors unaffected
    auth._bad_tokens.clear()
