"""
scripts/auto_refresh_token.py -- Trading System v2  FIX-135 Item 49 / FIX-187

Purpose:
    Fully headless Zerodha token refresh via TOTP (no browser, no manual OTP).
    Runs daily at 08:00 IST Mon-Fri via cron (after the 05:00 token cleanup),
    eliminating the daily manual OTP step.

Flow (FIX-187 -- all automated):
    1. Resolve the primary account from config/accounts.csv (which names the
       env vars that hold each credential).
    2. POST kite.zerodha.com/api/login  (user_id + password)   -> request_id
    3. Generate TOTP (pyotp) and POST /api/twofa               -> auth'd session
    4. GET  connect/login?v=3&api_key=...  -> follow redirect  -> request_token
    5. Exchange request_token -> access_token
       (reuses scripts.zerodha_login.exchange_request_token)
    6. Save token JSON in the CANONICAL format
       (reuses scripts.zerodha_login.save_token) so that
       zerodha_login.is_token_valid() accepts it (main.py live startup) AND the
       paper-mode quote provider finds the api_key field. Same token file feeds
       paper and live -- parity by construction.

Credentials (in .env, NEVER committed). Each is resolved by trying the
account-specific env var named in accounts.csv FIRST, then a generic fallback:
    user_id    : ZERODHA_USER_ID_<acct>      | ZERODHA_USER_ID
    password   : ZERODHA_PASSWORD_<acct>     | ZERODHA_PASSWORD
    totp_secret: <account.totp_secret_env>   | ZERODHA_TOTP_SECRET
                 (e.g. ZERODHA_TOTP_LFL836)
    api_key    : <account.api_key_env>       | ZERODHA_API_KEY
    api_secret : <account.api_secret_env>    | ZERODHA_API_SECRET

Exit codes:
    0 -- success (Telegram INFO sent, heartbeat recorded SUCCESS)
    1 -- error / missing credentials (Telegram CRITICAL sent, heartbeat FAILED)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Load .env so a manual run works without `. .env` first (matches
# scripts/check_cron_drift.py). Cron also sources .env -- double-load is a no-op.
from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from core.account_registry import AccountRegistry
from core.logger import get_logger

_LOGIN_URL = "https://kite.zerodha.com/api/login"
_TWOFA_URL = "https://kite.zerodha.com/api/twofa"
_CONNECT_LOGIN_URL = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
_DEFAULT_TOKEN_PATH = _ROOT / "data_store" / "session" / "zerodha_token.json"
_DEFAULT_ACCOUNTS_CSV = _ROOT / "config" / "accounts.csv"

# FIX-187 / FIX-184 lesson: a default python-requests User-Agent can be 403'd by
# upstream WAFs. Present a browser-like UA for the Kite login endpoints.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_NETWORK_RETRIES = 3
_REQUEST_TIMEOUT = 10.0


class _AuthError(RuntimeError):
    """Definitive authentication failure -- do NOT network-retry (e.g. wrong
    password, TOTP rejected after retry, request_token never returned)."""


# ─────────────────────────────────────────────────────────────────────────────
# TOTP
# ─────────────────────────────────────────────────────────────────────────────

def generate_totp(secret: str) -> str:
    """Generate the current 6-digit TOTP from a base32 secret key."""
    try:
        import pyotp
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError("pyotp not installed. Run: pip install pyotp") from exc
    return pyotp.TOTP(secret).now()


# ─────────────────────────────────────────────────────────────────────────────
# Credential resolution (account-specific name, generic fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_env(*names: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Return (value, name_used) for the first env var in *names that is set and
    non-empty, else (None, None). Never returns the value's content in logs.
    """
    for name in names:
        if not name:
            continue
        val = os.environ.get(name)
        if val:
            return val, name
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# request_token acquisition (FIX-187 P0-B fix)
# ─────────────────────────────────────────────────────────────────────────────

def _request_token_from_url(url: str) -> Optional[str]:
    """Extract a request_token query param from a URL/Location header, if any."""
    if not url:
        return None
    vals = parse_qs(urlparse(url).query).get("request_token")
    return vals[0] if vals else None


def _fetch_request_token(session, api_key: str, log, max_hops: int = 6) -> str:
    """
    After a successful login + 2FA, the request_token is delivered by the
    Kite Connect OAuth endpoint as a redirect query param -- it is NOT in the
    /api/twofa response. Walk the connect/login redirect chain and return the
    request_token as soon as it appears in a Location header (which is before
    we would actually fetch the app's registered -- possibly unreachable --
    redirect URL).
    """
    url = _CONNECT_LOGIN_URL.format(api_key=api_key)
    for hop in range(max_hops):
        resp = session.get(url, allow_redirects=False, timeout=_REQUEST_TIMEOUT)
        location = resp.headers.get("Location", "")
        token = _request_token_from_url(location) or _request_token_from_url(resp.url)
        if token:
            return token
        if resp.is_redirect and location:
            url = urljoin(url, location)
            continue
        raise _AuthError(
            f"request_token not found from connect/login "
            f"(HTTP {resp.status_code}, hop {hop})"
        )
    raise _AuthError("request_token not found: connect/login redirect chain too long")


# ─────────────────────────────────────────────────────────────────────────────
# Headless login
# ─────────────────────────────────────────────────────────────────────────────

def login_and_get_request_token(
    session,
    user_id: str,
    password: str,
    totp_secret: str,
    api_key: str,
    log,
) -> str:
    """
    Perform the headless Zerodha login (password + TOTP) on `session` and return
    the request_token. Retries the 2FA step once on a rejected TOTP (clock skew).

    Raises:
        _AuthError: definitive auth failure (no point retrying).
        requests.RequestException: transient network/HTTP error (caller retries).
    """
    # Step 1 -- password login.
    resp = session.post(
        _LOGIN_URL,
        data={"user_id": user_id, "password": password},
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise _AuthError(f"Login failed: {data.get('message', 'unknown error')}")
    request_id = data["data"]["request_id"]

    # Step 2 -- TOTP 2FA, with one retry on rejection (allows the next 30s
    # window / a small clock skew to pass).
    for attempt in range(2):
        twofa = session.post(
            _TWOFA_URL,
            data={
                "user_id": user_id,
                "request_id": request_id,
                "twofa_value": generate_totp(totp_secret),
                "twofa_type": "totp",
            },
            timeout=_REQUEST_TIMEOUT,
        )
        ok = False
        message = ""
        if twofa.status_code == 200:
            try:
                ok = twofa.json().get("status") == "success"
                message = twofa.json().get("message", "")
            except ValueError:
                message = twofa.text[:200]
        else:
            message = f"HTTP {twofa.status_code}: {twofa.text[:200]}"
        if ok:
            break
        if attempt == 0:
            log.warning("auto_refresh_token.twofa_retry: %s", message)
            time.sleep(1.0)
            continue
        raise _AuthError(f"2FA failed: {message}")

    # Step 3 -- request_token via the connect/login redirect.
    return _fetch_request_token(session, api_key, log)


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────

def _refresh_once(account, creds: dict, token_path: Path, log) -> str:
    """One full attempt: login -> exchange -> save. Returns access_token."""
    import requests  # local import: keeps module import light for unit tests

    # Reuse the canonical exchange + save so the token JSON is byte-compatible
    # with zerodha_login.is_token_valid() and the paper quote provider.
    from scripts.zerodha_login import exchange_request_token, save_token

    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})

    request_token = login_and_get_request_token(
        session, creds["user_id"], creds["password"],
        creds["totp_secret"], creds["api_key"], log,
    )
    access_token = exchange_request_token(
        creds["api_key"], creds["api_secret"], request_token
    )
    save_token(
        account_id=account.account_id,
        broker=account.broker,
        api_key=creds["api_key"],
        access_token=access_token,
        token_path=token_path,
    )
    return access_token


def run_refresh(account, creds: dict, token_path: Path, log) -> str:
    """
    Refresh the token with retry. Transient network/HTTP errors are retried up
    to `_NETWORK_RETRIES` times with exponential backoff; _AuthError is fatal.
    Returns the access_token or raises.
    """
    import requests

    backoff = 2.0
    last_exc: Optional[Exception] = None
    for attempt in range(1, _NETWORK_RETRIES + 1):
        try:
            return _refresh_once(account, creds, token_path, log)
        except _AuthError:
            raise
        except requests.RequestException as exc:
            last_exc = exc
            log.warning(
                "auto_refresh_token.network_retry attempt=%d/%d error=%s",
                attempt, _NETWORK_RETRIES, exc,
            )
            if attempt < _NETWORK_RETRIES:
                time.sleep(backoff)
                backoff *= 2
    assert last_exc is not None
    raise last_exc


# ─────────────────────────────────────────────────────────────────────────────
# Telegram + heartbeat (defensive: never affect exit code)
# ─────────────────────────────────────────────────────────────────────────────

def _alert(message: str, level: str, log) -> None:
    try:
        from alerts.telegram_notifier import TelegramNotifier

        notifier = TelegramNotifier.from_env()
        if notifier is None:
            log.info("auto_refresh_token.telegram_unconfigured")
            return
        notifier.send_alert(message, level=level)
    except Exception as exc:  # pragma: no cover - alerting must never crash refresh
        log.warning("auto_refresh_token.telegram_failed: %s", exc)


def _heartbeat(status: str, duration_sec: float, message: Optional[str], log) -> None:
    try:
        from utils.cron_heartbeat import record_heartbeat

        record_heartbeat(
            "auto_refresh_token",
            status=status,
            duration_sec=duration_sec,
            message=message,
        )
    except Exception as exc:  # pragma: no cover
        log.warning("auto_refresh_token.heartbeat_failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="auto_refresh_token",
        description="FIX-187: headless Zerodha TOTP login + token refresh.",
    )
    parser.add_argument(
        "--account", default=None,
        help="Account ID from accounts.csv (default: the primary account).",
    )
    parser.add_argument(
        "--token-path", metavar="PATH", default=str(_DEFAULT_TOKEN_PATH),
        help="Path to save zerodha_token.json",
    )
    parser.add_argument(
        "--accounts-csv", metavar="PATH", default=str(_DEFAULT_ACCOUNTS_CSV),
        help="Path to accounts.csv (default: config/accounts.csv).",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("auto_refresh_token")
    start = time.time()

    # Resolve the account (primary by default).
    try:
        registry = AccountRegistry.load(Path(args.accounts_csv))
        account = registry.get(args.account) if args.account else registry.primary()
    except Exception as exc:
        log.error("auto_refresh_token.account_resolve_failed: %s", exc)
        _alert(f"Token refresh FAILED: cannot resolve account ({exc})", "CRITICAL", log)
        _heartbeat("FAILED", time.time() - start, f"account resolve: {exc}", log)
        return 1

    acct = account.account_id
    # Resolve each credential: account-specific env name first, generic fallback.
    user_id, _ = _resolve_env(f"ZERODHA_USER_ID_{acct}", "ZERODHA_USER_ID")
    password, _ = _resolve_env(f"ZERODHA_PASSWORD_{acct}", "ZERODHA_PASSWORD")
    totp_secret, _ = _resolve_env(account.totp_secret_env, "ZERODHA_TOTP_SECRET")
    api_key, _ = _resolve_env(account.api_key_env, "ZERODHA_API_KEY")
    api_secret, _ = _resolve_env(account.api_secret_env, "ZERODHA_API_SECRET")

    creds = {
        "user_id": user_id, "password": password, "totp_secret": totp_secret,
        "api_key": api_key, "api_secret": api_secret,
    }
    missing = [k for k, v in creds.items() if not v]
    if missing:
        log.error("auto_refresh_token.missing_creds account=%s missing=%s", acct, missing)
        _alert(
            f"Token refresh FAILED for {acct}: missing credentials {missing}",
            "CRITICAL", log,
        )
        _heartbeat("FAILED", time.time() - start, f"missing creds: {missing}", log)
        return 1

    if args.dry_run:
        totp = generate_totp(totp_secret)
        log.info("auto_refresh_token.dry_run account=%s totp=%s*** (no login)", acct, totp[:3])
        print(f"[DRY-RUN] account={acct} TOTP generated OK (not logging in)")
        return 0

    try:
        run_refresh(account, creds, Path(args.token_path), log)
    except Exception as exc:
        log.error("auto_refresh_token.failed account=%s: %s", acct, exc, exc_info=True)
        _alert(f"Token refresh FAILED for {acct}: {exc}", "CRITICAL", log)
        _heartbeat("FAILED", time.time() - start, str(exc), log)
        return 1

    duration = time.time() - start
    log.info("auto_refresh_token.success account=%s duration=%.1fs", acct, duration)
    _alert(f"Token refreshed successfully for {acct}", "INFO", log)
    _heartbeat("SUCCESS", duration, None, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
