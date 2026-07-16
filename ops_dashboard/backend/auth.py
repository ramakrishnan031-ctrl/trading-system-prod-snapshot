"""
ops_dashboard/backend/auth.py

Single-user authentication: salted PBKDF2-HMAC-SHA256 password (hash stored in
gui_config.yaml — NEVER plaintext) + TOTP second factor (pyotp) + a 5-failure /
15-minute lockout. Provides the auth blueprint (login/logout) and a setup CLI:

    python -m backend.auth --setup --username <u> --password <pw>

which writes the auth block into gui_config.yaml and prints the otpauth:// URI
(scan into an authenticator app) plus the TOTP secret.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional, Tuple

import pyotp
import yaml
from flask import (
    Blueprint, current_app, jsonify, redirect, render_template,
    request, session, url_for,
)

_PBKDF2_ITERATIONS = 240_000
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config", "gui_config.yaml")


# ─────────────────────────────────────────────────────────────────────────────
# Password hashing (stdlib hashlib; no external crypto dependency)
# ─────────────────────────────────────────────────────────────────────────────
def hash_password(password: str, *, iterations: int = _PBKDF2_ITERATIONS,
                  salt: Optional[bytes] = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or stored.count("$") != 3:
        return False
    algo, iters, salt_hex, hash_hex = stored.split("$")
    if algo != "pbkdf2_sha256":
        return False
    try:
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            bytes.fromhex(salt_hex), int(iters),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def verify_totp(secret: str, code: str, *, totp_disabled: bool = False) -> bool:
    """Verify a TOTP code. FAILS CLOSED on a missing secret (AB-910 §1.3).

    An absent/empty `totp_secret` used to return True — i.e. losing the secret silently
    downgraded the dashboard from two factors to one, and the weaker state was the
    *default*. A security control must never be disabled by the absence of its own config.

    Skipping TOTP is now something you can only ask for OUT LOUD, via an explicit
    `auth.totp_disabled: true` in the GUI config. That is the dev escape hatch and the
    only way to log in without a second factor.
    """
    if not secret:
        return bool(totp_disabled)  # fail CLOSED unless explicitly, deliberately disabled
    if not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
    except Exception:  # pyotp raises on malformed input
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Lockout tracker (in-memory; single user, single process)
# ─────────────────────────────────────────────────────────────────────────────
class LoginAttemptTracker:
    def __init__(self, max_failures: int = 5, lockout_seconds: int = 900):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures: dict = {}       # username -> count
        self._locked_until: dict = {}   # username -> epoch

    def is_locked(self, username: str, *, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        until = self._locked_until.get(username, 0.0)
        if until and now < until:
            return True
        if until and now >= until:
            # cooldown elapsed → reset
            self._locked_until.pop(username, None)
            self._failures.pop(username, None)
        return False

    def seconds_remaining(self, username: str, *, now: Optional[float] = None) -> int:
        now = now if now is not None else time.time()
        return max(0, int(self._locked_until.get(username, 0.0) - now))

    def record_failure(self, username: str, *, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        self._failures[username] = self._failures.get(username, 0) + 1
        if self._failures[username] >= self.max_failures:
            self._locked_until[username] = now + self.lockout_seconds

    def record_success(self, username: str) -> None:
        self._failures.pop(username, None)
        self._locked_until.pop(username, None)


def authenticate(auth_cfg: dict, tracker: LoginAttemptTracker,
                 username: str, password: str, totp_code: str,
                 *, now: Optional[float] = None) -> Tuple[bool, Optional[str]]:
    """Return (ok, error_message). Applies lockout before checking credentials."""
    expected_user = auth_cfg.get("username", "")
    if not expected_user or not auth_cfg.get("password_hash"):
        return False, "Auth not configured. Run: python -m backend.auth --setup"
    if tracker.is_locked(username, now=now):
        return False, f"Locked out. Try again in {tracker.seconds_remaining(username, now=now)}s."
    ok_user = hmac.compare_digest(username or "", expected_user)
    ok_pw = verify_password(password or "", auth_cfg.get("password_hash", ""))
    # AB-910 §1.3: an empty totp_secret refuses unless `totp_disabled: true` is explicit.
    ok_totp = verify_totp(
        auth_cfg.get("totp_secret", ""), totp_code,
        totp_disabled=bool(auth_cfg.get("totp_disabled", False)),
    )
    if ok_user and ok_pw and ok_totp:
        tracker.record_success(username)
        return True, None
    tracker.record_failure(username, now=now)
    return False, "Invalid credentials."


def is_authenticated() -> bool:
    return bool(session.get("user"))


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication required"}), 401
            return redirect(url_for("auth.login_get"))
        return view(*args, **kwargs)
    return wrapped


# ─────────────────────────────────────────────────────────────────────────────
# Blueprint
# ─────────────────────────────────────────────────────────────────────────────
auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET"])
def login_get():
    if is_authenticated():
        return redirect(url_for("dashboard_page"))
    return render_template("login.html", error=None)


@auth_bp.route("/login", methods=["POST"])
def login_post():
    cfg = current_app.config["GUI_CONFIG"]
    tracker: LoginAttemptTracker = current_app.config["LOGIN_TRACKER"]
    auth_cfg = cfg.get("auth", {})
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    totp_code = request.form.get("totp", "")
    ok, err = authenticate(auth_cfg, tracker, username, password, totp_code)
    if ok:
        session.clear()
        session["user"] = username
        session.permanent = True
        return redirect(url_for("dashboard_page"))
    return render_template("login.html", error=err), 401


@auth_bp.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("auth.login_get"))


# ─────────────────────────────────────────────────────────────────────────────
# Setup CLI
# ─────────────────────────────────────────────────────────────────────────────
def _write_auth_block(config_path: str, username: str, password_hash: str,
                      totp_secret: str) -> None:
    # Missing file is fine — on the VM, --setup targets the git-ignored
    # gui_config.local.yaml overlay (created here on first run; chmod 600
    # is the operator's step per deployment/INSTALL.md).
    if os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    else:
        data = {}
    auth = data.get("auth", {}) or {}
    auth.update({
        "username": username,
        "password_hash": password_hash,
        "totp_secret": totp_secret,
        "max_failures": auth.get("max_failures", 5),
        "lockout_minutes": auth.get("lockout_minutes", 15),
        "session_lifetime_minutes": auth.get("session_lifetime_minutes", 60),
    })
    data["auth"] = auth
    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Ops dashboard auth setup.")
    p.add_argument("--setup", action="store_true", help="Generate credentials and write gui_config.yaml.")
    p.add_argument("--username", required=False, help="Login username.")
    p.add_argument("--password", required=False, help="Login password (or omit to be prompted).")
    p.add_argument("--config", default=_CONFIG_PATH, help="Path to gui_config.yaml.")
    args = p.parse_args(argv)

    if not args.setup:
        p.print_help()
        return 0

    username = args.username
    if not username:
        print("ERROR: --username is required for --setup")
        return 1
    password = args.password
    if not password:
        import getpass
        password = getpass.getpass("New password: ")
    if not password:
        print("ERROR: empty password")
        return 1

    pw_hash = hash_password(password)
    totp_secret = pyotp.random_base32()
    _write_auth_block(args.config, username, pw_hash, totp_secret)
    uri = pyotp.TOTP(totp_secret).provisioning_uri(name=username, issuer_name="OpsDashboard")
    print("Auth configured in:", args.config)
    print("Username         :", username)
    print("TOTP secret      :", totp_secret)
    print("otpauth URI (QR) :", uri)
    print("Scan the URI into an authenticator app; keep the secret private.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
