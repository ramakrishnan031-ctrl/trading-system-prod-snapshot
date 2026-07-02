"""auth — password hashing, TOTP, lockout, authenticate, session flags."""
from __future__ import annotations

import pyotp

from backend import auth
from backend import app as app_module


def test_password_hash_roundtrip():
    stored = auth.hash_password("secret123")
    assert stored.startswith("pbkdf2_sha256$")
    assert auth.verify_password("secret123", stored) is True
    assert auth.verify_password("wrong", stored) is False
    assert auth.verify_password("secret123", "garbage") is False


def test_totp():
    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).now()
    assert auth.verify_totp(secret, code) is True
    assert auth.verify_totp(secret, "000000") is False
    assert auth.verify_totp("", "anything") is True   # empty secret disables TOTP


def test_lockout():
    tr = auth.LoginAttemptTracker(max_failures=5, lockout_seconds=900)
    t0 = 1000.0
    for _ in range(5):
        assert tr.is_locked("u", now=t0) is False
        tr.record_failure("u", now=t0)
    assert tr.is_locked("u", now=t0) is True
    assert tr.seconds_remaining("u", now=t0) == 900
    # cooldown elapsed → unlocked and reset
    assert tr.is_locked("u", now=t0 + 901) is False
    tr.record_success("u")
    assert tr.is_locked("u", now=t0) is False


def test_authenticate_flow():
    tr = auth.LoginAttemptTracker(max_failures=5, lockout_seconds=900)
    secret = pyotp.random_base32()
    auth_cfg = {
        "username": "tester",
        "password_hash": auth.hash_password("secret123"),
        "totp_secret": secret,
    }
    ok, err = auth.authenticate(auth_cfg, tr, "tester", "secret123",
                                pyotp.TOTP(secret).now())
    assert ok is True and err is None

    ok, err = auth.authenticate(auth_cfg, tr, "tester", "wrong",
                                pyotp.TOTP(secret).now())
    assert ok is False and "Invalid" in err


def test_authenticate_unconfigured():
    tr = auth.LoginAttemptTracker()
    ok, err = auth.authenticate({}, tr, "x", "y", "z")
    assert ok is False and "not configured" in err.lower()


def test_session_cookie_flags(gui_config):
    app = app_module.create_app(gui_config=gui_config)
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Strict"
    # Secure is config-gated (off for loopback-HTTP dev; on under TLS in G2c)
    assert app.config["SESSION_COOKIE_SECURE"] is False
