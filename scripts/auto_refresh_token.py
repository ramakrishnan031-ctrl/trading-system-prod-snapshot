"""
scripts/auto_refresh_token.py -- Trading System v2  FIX-135 Item 49

Purpose:
    Automated Zerodha token refresh using TOTP (no browser required).
    1. POST login with user_id + password
    2. Generate TOTP from secret key
    3. POST two-factor auth
    4. Exchange request_token for access_token
    5. Save to zerodha_token.json

    Run at 08:00 IST (before market open, after token cleanup at 05:00).

Credentials (in .env, NEVER committed):
    ZERODHA_USER_ID, ZERODHA_PASSWORD, ZERODHA_TOTP_SECRET,
    ZERODHA_API_KEY, ZERODHA_API_SECRET

Exit codes:
    0 -- success
    1 -- error / missing credentials
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.time_authority import now_ist

_LOGIN_URL = "https://kite.zerodha.com/api/login"
_TWOFA_URL = "https://kite.zerodha.com/api/twofa"
_DEFAULT_TOKEN_PATH = _ROOT / "data_store" / "session" / "zerodha_token.json"


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="auto_refresh_token",
        description="FIX-135: Automated Zerodha TOTP login + token refresh.",
    )
    parser.add_argument(
        "--token-path", metavar="PATH", default=str(_DEFAULT_TOKEN_PATH),
        help="Path to save zerodha_token.json",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def generate_totp(secret: str) -> str:
    """Generate current TOTP from secret key."""
    try:
        import pyotp
    except ImportError:
        raise RuntimeError(
            "pyotp not installed. Run: pip install pyotp"
        )
    return pyotp.TOTP(secret).now()


def login_and_get_request_token(
    user_id: str,
    password: str,
    totp_secret: str,
    log: logging.Logger,
) -> str:
    """
    Perform Zerodha login + TOTP 2FA.
    Returns the request_token from the redirect URL.
    """
    import requests

    session = requests.Session()

    log.info("auto_refresh_token: step 1 - POST login")
    login_resp = session.post(_LOGIN_URL, data={
        "user_id": user_id,
        "password": password,
    })
    login_resp.raise_for_status()
    login_data = login_resp.json()

    if login_data.get("status") != "success":
        raise RuntimeError(
            f"Login failed: {login_data.get('message', 'unknown error')}"
        )

    request_id = login_data["data"]["request_id"]

    log.info("auto_refresh_token: step 2 - generate TOTP and POST 2FA")
    totp_value = generate_totp(totp_secret)

    twofa_resp = session.post(_TWOFA_URL, data={
        "user_id": user_id,
        "request_id": request_id,
        "twofa_value": totp_value,
        "twofa_type": "totp",
    })
    twofa_resp.raise_for_status()
    twofa_data = twofa_resp.json()

    if twofa_data.get("status") != "success":
        raise RuntimeError(
            f"2FA failed: {twofa_data.get('message', 'unknown error')}"
        )

    request_token = twofa_data["data"].get("request_token")
    if not request_token:
        raise RuntimeError("No request_token in 2FA response")

    return request_token


def exchange_for_access_token(
    api_key: str,
    api_secret: str,
    request_token: str,
    log: logging.Logger,
) -> dict:
    """
    Exchange request_token for access_token via kiteconnect.
    Returns dict with access_token and other session data.
    """
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        raise RuntimeError("kiteconnect not installed")

    kite = KiteConnect(api_key=api_key)
    session_data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = session_data.get("access_token")
    if not access_token:
        raise RuntimeError("No access_token in session response")

    log.info("auto_refresh_token: step 3 - access_token obtained")
    return {
        "access_token": access_token,
        "user_id": session_data.get("user_id", ""),
        "login_time": now_ist().isoformat(),
    }


def save_token(token_data: dict, path: Path, log: logging.Logger) -> None:
    """Atomically save token data to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    try:
        tmp_path.write_text(
            json.dumps(token_data, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)
        log.info("auto_refresh_token: token saved to %s", path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("auto_refresh_token")

    user_id = os.environ.get("ZERODHA_USER_ID", "")
    password = os.environ.get("ZERODHA_PASSWORD", "")
    totp_secret = os.environ.get("ZERODHA_TOTP_SECRET", "")
    api_key = os.environ.get("ZERODHA_API_KEY", "")
    api_secret = os.environ.get("ZERODHA_API_SECRET", "")

    missing = []
    if not user_id:
        missing.append("ZERODHA_USER_ID")
    if not password:
        missing.append("ZERODHA_PASSWORD")
    if not totp_secret:
        missing.append("ZERODHA_TOTP_SECRET")
    if not api_key:
        missing.append("ZERODHA_API_KEY")
    if not api_secret:
        missing.append("ZERODHA_API_SECRET")

    if missing:
        log.error("auto_refresh_token: missing env vars: %s", missing)
        return 1

    if args.dry_run:
        totp = generate_totp(totp_secret)
        log.info("auto_refresh_token: dry-run TOTP=%s (not logging in)", totp[:3] + "***")
        return 0

    try:
        request_token = login_and_get_request_token(
            user_id, password, totp_secret, log
        )
        token_data = exchange_for_access_token(
            api_key, api_secret, request_token, log
        )
        save_token(token_data, Path(args.token_path), log)
    except Exception as exc:
        log.error("auto_refresh_token.failed: %s", exc, exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
