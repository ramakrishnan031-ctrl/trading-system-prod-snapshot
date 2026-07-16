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


def test_totp_empty_secret_fails_closed():
    """AB-910 §1.3: a missing secret must REFUSE, not wave the user through.

    This assertion is the exact inverse of what it was before 17-Jul, deliberately:
    the old contract ("empty secret disables TOTP") meant losing the secret silently
    downgraded the dashboard to a single factor, with the weaker state as the default.
    """
    assert auth.verify_totp("", "anything") is False
    assert auth.verify_totp("", "") is False


def test_totp_disabled_flag_is_the_only_escape_hatch():
    """Skipping TOTP must be asked for explicitly — and must still work, so a dev
    (or a locked-out operator) has a documented way through."""
    assert auth.verify_totp("", "anything", totp_disabled=True) is True
    # An explicit disable does NOT override a real secret: a wrong code still fails.
    secret = pyotp.random_base32()
    assert auth.verify_totp(secret, "000000", totp_disabled=True) is False
    assert auth.verify_totp(secret, pyotp.TOTP(secret).now(), totp_disabled=True) is True


def test_authenticate_empty_secret_refuses_but_flag_allows():
    """End-to-end through authenticate(), which is what the login route calls."""
    cfg = {"username": "tester", "password_hash": auth.hash_password("pw"),
           "totp_secret": ""}
    ok, err = auth.authenticate(cfg, auth.LoginAttemptTracker(), "tester", "pw", "")
    assert ok is False, "empty totp_secret must not authenticate by default"

    ok, err = auth.authenticate({**cfg, "totp_disabled": True},
                                auth.LoginAttemptTracker(), "tester", "pw", "")
    assert ok is True and err is None


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


def test_local_overlay_deep_merge(tmp_path):
    """G2c: gui_config.local.yaml deep-merges over the base (checkout-f-safe
    VM secrets/paths). Overlay wins; untouched base keys survive."""
    import yaml as _yaml
    base = {"paths": {"main_db": "pc.db", "logs_dir": "pc-logs"},
            "server": {"bind_host": "127.0.0.1", "session_cookie_secure": False},
            "reports_download_enabled": False}
    overlay = {"paths": {"main_db": "/vm/trading_system.db"},
               "server": {"session_cookie_secure": True},
               "auth": {"username": "rama", "password_hash": "x", "totp_secret": "y"}}
    (tmp_path / "gui_config.yaml").write_text(_yaml.safe_dump(base), encoding="utf-8")
    (tmp_path / "gui_config.local.yaml").write_text(_yaml.safe_dump(overlay), encoding="utf-8")
    cfg = app_module.load_gui_config(str(tmp_path / "gui_config.yaml"))
    assert cfg["paths"]["main_db"] == "/vm/trading_system.db"   # overlay wins
    assert cfg["paths"]["logs_dir"] == "pc-logs"                # base survives
    assert cfg["server"]["session_cookie_secure"] is True
    assert cfg["server"]["bind_host"] == "127.0.0.1"
    assert cfg["auth"]["username"] == "rama"


def test_auth_setup_creates_missing_local_file(tmp_path):
    """--setup targets the (possibly absent) local overlay on the VM."""
    from backend.auth import _write_auth_block
    path = str(tmp_path / "gui_config.local.yaml")
    _write_auth_block(path, "rama", "pbkdf2_sha256$1$aa$bb", "SECRET")
    import yaml as _yaml
    data = _yaml.safe_load(open(path, encoding="utf-8"))
    assert data["auth"]["username"] == "rama"
    assert data["auth"]["totp_secret"] == "SECRET"
