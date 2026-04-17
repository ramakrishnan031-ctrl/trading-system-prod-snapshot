"""
scripts/zerodha_login.py -- Trading System v2

Purpose:
    Browser-assisted Zerodha login flow.  Generates a session access token
    and saves it to data_store/session/zerodha_token.json.

    Can be run standalone:
        python scripts/zerodha_login.py --account LFL836

    Also imported by main.py for the interactive startup flow (SU16).

Locked Design Decisions:
    SU10 -- Login flow: open browser, accept request_token, exchange for
            access_token via POST /session/token, save JSON.
    SU16 -- Standalone + importable.  main.py calls this for DRY.
    SU17 -- is_token_valid() and load_token() helpers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

_IST = timezone(timedelta(hours=5, minutes=30))
_KITE_LOGIN_URL = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
_KITE_SESSION_URL = "https://api.kite.trade/session/token"
_DEFAULT_TOKEN_PATH = Path("data_store/session/zerodha_token.json")


# ─────────────────────────────────────────────────────────────────────────────
# Token helpers (SU17)
# ─────────────────────────────────────────────────────────────────────────────

def load_token(token_path: Path) -> Optional[dict]:
    """
    Parse zerodha_token.json; return dict or None on any error (SU17).
    """
    try:
        with open(token_path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def is_token_valid(account_id: str, token_path: Path) -> bool:
    """
    Return True iff:
      - token file exists
      - account_id matches
      - token date == today (IST)
      - access_token is non-empty
    (SU17)
    """
    token = load_token(token_path)
    if token is None:
        return False
    today_str = datetime.now(_IST).date().isoformat()
    return (
        token.get("account_id") == account_id
        and token.get("date") == today_str
        and bool(token.get("access_token", "").strip())
    )


# ─────────────────────────────────────────────────────────────────────────────
# Token exchange (SU10e)
# ─────────────────────────────────────────────────────────────────────────────

def exchange_request_token(
    api_key: str,
    api_secret: str,
    request_token: str,
    timeout_sec: float = 10.0,
) -> str:
    """
    Exchange request_token for access_token via Kite API (SU10e).

    Returns:
        access_token string on success.

    Raises:
        RuntimeError: on non-200 response or missing access_token in response.
        requests.RequestException: on network failure.
    """
    checksum = hashlib.sha256(
        (api_key + request_token + api_secret).encode()
    ).hexdigest()

    resp = requests.post(
        _KITE_SESSION_URL,
        data={
            "api_key": api_key,
            "request_token": request_token,
            "checksum": checksum,
        },
        timeout=timeout_sec,
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Kite session exchange failed: HTTP {resp.status_code} -- {resp.text[:200]}"
        )

    payload = resp.json()
    access_token = payload.get("data", {}).get("access_token") or payload.get("access_token")
    if not access_token:
        raise RuntimeError(
            f"access_token missing from Kite response: {str(payload)[:200]}"
        )
    return access_token


# ─────────────────────────────────────────────────────────────────────────────
# Token save (SU10f)
# ─────────────────────────────────────────────────────────────────────────────

def save_token(
    account_id: str,
    broker: str,
    api_key: str,
    access_token: str,
    token_path: Path = _DEFAULT_TOKEN_PATH,
) -> None:
    """
    Write token JSON to token_path (SU10f).

    Creates parent directories if needed.
    """
    token_path.parent.mkdir(parents=True, exist_ok=True)
    now_ist = datetime.now(_IST)
    expires_at = now_ist.replace(hour=5, minute=0, second=0, microsecond=0)
    if expires_at <= now_ist:
        expires_at = expires_at + timedelta(days=1)

    record = {
        "account_id": account_id,
        "broker": broker,
        "access_token": access_token,
        "api_key": api_key,
        "date": now_ist.date().isoformat(),
        "saved_at": now_ist.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    with open(token_path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Interactive login flow (SU10)
# ─────────────────────────────────────────────────────────────────────────────

def run_login_flow(
    account_id: str,
    broker: str,
    api_key: str,
    api_secret: str,
    token_path: Path = _DEFAULT_TOKEN_PATH,
    input_fn=input,
    open_browser: bool = True,
) -> str:
    """
    Interactive Zerodha login: open browser, accept request_token, exchange,
    save, and return access_token (SU10).

    Args:
        account_id:    e.g. "LFL836"
        broker:        e.g. "zerodha"
        api_key:       resolved API key string
        api_secret:    resolved API secret string
        token_path:    path to save the token JSON
        input_fn:      injectable input() for testing
        open_browser:  if False, skip webbrowser.open (for tests)

    Returns:
        access_token string.

    Raises:
        SystemExit(1): on exchange failure.
    """
    login_url = _KITE_LOGIN_URL.format(api_key=api_key)

    if open_browser:
        webbrowser.open(login_url)

    print("  Browser opened. Complete login:")
    print("    User ID + Password + TOTP from Kite app")
    print()
    print("  After login, browser redirects to localhost URL.")
    print("  Copy the request_token value from the URL.")
    print()
    request_token = input_fn("  Paste request_token here: ").strip()

    try:
        access_token = exchange_request_token(api_key, api_secret, request_token)
    except Exception as exc:
        print(f"  Login failed: {exc}")
        sys.exit(1)

    save_token(account_id, broker, api_key, access_token, token_path)
    print(f"  Login successful! Token saved for {account_id}.")
    return access_token


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point (SU16)
# ─────────────────────────────────────────────────────────────────────────────

def _cli_main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="zerodha_login.py",
        description="Zerodha browser-assisted login -- generates session token",
    )
    parser.add_argument(
        "--account", required=True,
        help="Account ID from accounts.csv (e.g. LFL836)",
    )
    parser.add_argument(
        "--token-path", default=str(_DEFAULT_TOKEN_PATH),
        help="Path to save zerodha_token.json",
    )
    args = parser.parse_args(argv)

    # Load accounts.csv to get credentials env var names
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from core.account_registry import AccountRegistry
    try:
        registry = AccountRegistry.load(Path("config/accounts.csv"))
        account = registry.get(args.account)
    except KeyError:
        print(f"  Error: Account {args.account!r} not found in accounts.csv")
        return 5
    except Exception as exc:
        print(f"  Error loading accounts.csv: {exc}")
        return 5

    api_key = os.environ.get(account.api_key_env)
    api_secret = os.environ.get(account.api_secret_env)

    if not api_key:
        print(f"  Error: env var {account.api_key_env!r} not set")
        return 5
    if not api_secret:
        print(f"  Error: env var {account.api_secret_env!r} not set")
        return 5

    run_login_flow(
        account_id=account.account_id,
        broker=account.broker,
        api_key=api_key,
        api_secret=api_secret,
        token_path=Path(args.token_path),
    )
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
