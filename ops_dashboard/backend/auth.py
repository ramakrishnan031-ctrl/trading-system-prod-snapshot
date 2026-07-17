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
import logging
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

# S3: the login-throttle audit trail (addendum §2).
_log = logging.getLogger("ops_dashboard.auth")


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
    """S3 (2026-07-17): a THROTTLE, not a lockout.

    WHAT WAS WRONG — a denial-of-service on the only operator. The old tracker
    locked an ACCOUNT for `lockout_minutes` after `max_failures`, keyed by the
    SUBMITTED username. So anyone who knew Rama's username could send 5 bad
    passwords and lock HIM out for 15 minutes, repeatably and indefinitely. The
    control meant to stop an attacker handed one a reliable way to shut the
    operator out of his own trading dashboard.

    WHY NOT THE AUDIT'S "PER-IP LOCKOUT" — it is strictly WORSE here. The GUI is
    Waitress on 127.0.0.1:8500 behind the tailscaled proxy, so `remote_addr` is
    ALWAYS 127.0.0.1: per-IP keying collapses to ONE bucket shared by every
    caller, and any failed attempt would lock out everyone, including Rama.
    X-Forwarded-For is not trusted either (it is caller-settable, so a lockout
    keyed on it would be trivially spoofable in both directions).

    THE PROPERTY THIS GUARANTEES — *** correct credentials ALWAYS authenticate. ***
    There is no state in which a valid login is refused, so an attacker cannot
    deny Rama access no matter how many failures they generate. Brute force is
    slowed instead: each FAILED attempt sleeps an exponentially growing, CAPPED
    delay before its response, which throttles guess throughput without ever
    standing between the operator and a correct password.

    Why a delay and not a fast rejection: telling a correct password from a wrong
    one REQUIRES verifying it. Any rule that refuses attempts without checking
    would refuse Rama's correct password too — i.e. it would be a lockout again.
    So the only mechanism that slows guessing while keeping the operator's access
    unconditional is to verify always and delay the failures.

    Residual (accepted, documented): a flood of failed attempts occupies Waitress
    worker threads for up to `throttle_max_seconds` each. The cap keeps that
    bounded, and the dashboard is reachable only from Rama's tailnet — not the
    public internet — so this is proportionate. It is a slowdown, never a lockout.

    In-memory and per-process by design (single operator, single Waitress
    process): a restart clears the counters, which can only ever GRANT access
    sooner, never deny it.
    """

    def __init__(self, max_failures: int = 5, lockout_seconds: int = 900,
                 throttle_base_seconds: float = 1.0,
                 throttle_max_seconds: float = 8.0):
        # max_failures: consecutive failures BEFORE throttling starts — a few
        # honest typos stay free. lockout_seconds: retained only so existing
        # config (`lockout_minutes`) still loads; NOTHING locks any more. It sets
        # how long a quiet period clears the failure streak.
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self.throttle_base_seconds = throttle_base_seconds
        self.throttle_max_seconds = throttle_max_seconds
        self._failures: dict = {}       # username -> consecutive failure count
        self._last_failure: dict = {}   # username -> epoch of the most recent failure

    def _decay(self, username: str, now: float) -> None:
        """A quiet period clears the streak, so yesterday's typos don't throttle
        today's login."""
        last = self._last_failure.get(username)
        if last is not None and now - last >= self.lockout_seconds:
            self._failures.pop(username, None)
            self._last_failure.pop(username, None)

    def failure_count(self, username: str, *, now: Optional[float] = None) -> int:
        now = now if now is not None else time.time()
        self._decay(username, now)
        return self._failures.get(username, 0)

    def throttle_delay(self, username: str, *, now: Optional[float] = None) -> float:
        """Seconds to delay the NEXT failed response for `username`.

        0.0 until max_failures consecutive failures, then exponential, capped.
        Never gates a SUCCESSFUL login — see authenticate().
        """
        n = self.failure_count(username, now=now)
        if n < self.max_failures:
            return 0.0
        exp = n - self.max_failures
        return float(min(self.throttle_base_seconds * (2 ** exp),
                         self.throttle_max_seconds))

    def record_failure(self, username: str, *, now: Optional[float] = None) -> int:
        now = now if now is not None else time.time()
        self._decay(username, now)
        self._failures[username] = self._failures.get(username, 0) + 1
        self._last_failure[username] = now
        return self._failures[username]

    def record_success(self, username: str) -> None:
        self._failures.pop(username, None)
        self._last_failure.pop(username, None)

    # Back-compat shims: the lockout is GONE. Kept so any caller/test still
    # referring to them gets the truth (never locked) rather than an
    # AttributeError that could mask a stale call site.
    def is_locked(self, username: str, *, now: Optional[float] = None) -> bool:
        return False

    def seconds_remaining(self, username: str, *, now: Optional[float] = None) -> int:
        return 0


def _throttle_source() -> str:
    """S3 / addendum §2: a best-effort caller label for the throttle audit line.

    LOGGED, NEVER TRUSTED. Behind the tailscaled loopback proxy `remote_addr` is
    always 127.0.0.1, and X-Forwarded-For is caller-settable — so neither is a
    sound basis for a security DECISION (which is exactly why the throttle is
    keyed on the username and gates nothing). Both are recorded so repeated-
    failure activity is auditable rather than silent.
    """
    try:
        addr = request.remote_addr or "?"
        xff = request.headers.get("X-Forwarded-For")
        return f"remote_addr={addr} xff={xff or '-'}(untrusted)"
    except Exception:
        return "remote_addr=? xff=-(no request context)"


def authenticate(auth_cfg: dict, tracker: LoginAttemptTracker,
                 username: str, password: str, totp_code: str,
                 *, now: Optional[float] = None,
                 sleep_fn=time.sleep) -> Tuple[bool, Optional[str]]:
    """Return (ok, error_message).

    S3 (2026-07-17): THROTTLES failures; never locks the account.

    *** Credentials are verified FIRST and unconditionally. *** There is no state
    in which a correct username+password+TOTP is refused, so an attacker cannot
    deny Rama access however many failures they generate. The old code checked
    `tracker.is_locked()` BEFORE verifying — that check was the DoS.

    Only the FAILURE path is delayed (exponential, capped) — which is what slows
    brute force. `sleep_fn` is injectable so tests assert the delay without
    actually sleeping.
    """
    expected_user = auth_cfg.get("username", "")
    if not expected_user or not auth_cfg.get("password_hash"):
        return False, "Auth not configured. Run: python -m backend.auth --setup"
    ok_user = hmac.compare_digest(username or "", expected_user)
    ok_pw = verify_password(password or "", auth_cfg.get("password_hash", ""))
    # AB-910 §1.3: an empty totp_secret refuses unless `totp_disabled: true` is explicit.
    ok_totp = verify_totp(
        auth_cfg.get("totp_secret", ""), totp_code,
        totp_disabled=bool(auth_cfg.get("totp_disabled", False)),
    )
    if ok_user and ok_pw and ok_totp:
        # THE property: a correct login always succeeds, whatever the streak.
        tracker.record_success(username)
        return True, None

    # Failure: record it FIRST, then delay this response by the backoff the new
    # streak has earned — so the Nth consecutive failure serves the Nth delay and
    # the audit line's count matches the delay it reports.
    count = tracker.record_failure(username, now=now)
    delay = tracker.throttle_delay(username, now=now)
    if delay > 0:
        # addendum §2: every throttle activation is auditable.
        _log.warning(
            "auth.login_throttled: consecutive_failures=%d applied_delay=%.1fs "
            "username=%r %s",
            count, delay, username, _throttle_source(),
        )
        sleep_fn(delay)
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
