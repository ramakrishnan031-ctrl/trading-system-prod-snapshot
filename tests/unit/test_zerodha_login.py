"""
tests/unit/test_zerodha_login.py -- Trading System v2

Tests for scripts/zerodha_login.py (SU10, SU16, SU17, SU20).

Run: python -m pytest tests/unit/test_zerodha_login.py -v
Or:  python tests/unit/test_zerodha_login.py  (standalone)
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.zerodha_login import (
    _KITE_LOGIN_URL,
    exchange_request_token,
    is_token_valid,
    load_token,
    save_token,
)

_IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_token(path: Path, **overrides) -> dict:
    today_str = datetime.now(_IST).date().isoformat()
    record = {
        "account_id": "LFL836",
        "broker": "zerodha",
        "access_token": "valid_access_token_abc",
        "api_key": "test_api_key",
        "date": today_str,
        "saved_at": datetime.now(_IST).isoformat(),
        "expires_at": (datetime.now(_IST) + timedelta(hours=10)).isoformat(),
    }
    record.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    return record


# ─────────────────────────────────────────────────────────────────────────────
# SU17: is_token_valid
# ─────────────────────────────────────────────────────────────────────────────

def test_is_token_valid_valid_token(tmp_path: Path) -> None:
    """Valid token for correct account and today returns True."""
    p = tmp_path / "token.json"
    _write_token(p, account_id="LFL836")
    assert is_token_valid("LFL836", p) is True


def test_is_token_valid_wrong_account(tmp_path: Path) -> None:
    """Token with mismatched account_id returns False."""
    p = tmp_path / "token.json"
    _write_token(p, account_id="DR6114")
    assert is_token_valid("LFL836", p) is False


def test_is_token_valid_old_date(tmp_path: Path) -> None:
    """Token dated yesterday returns False."""
    p = tmp_path / "token.json"
    yesterday = (datetime.now(_IST) - timedelta(days=1)).date().isoformat()
    _write_token(p, date=yesterday)
    assert is_token_valid("LFL836", p) is False


def test_is_token_valid_missing_file(tmp_path: Path) -> None:
    """Missing token file returns False (no exception)."""
    assert is_token_valid("LFL836", tmp_path / "no_such_file.json") is False


def test_is_token_valid_corrupt_json(tmp_path: Path) -> None:
    """Corrupt JSON in token file returns False."""
    p = tmp_path / "token.json"
    p.write_text("not valid json {{", encoding="utf-8")
    assert is_token_valid("LFL836", p) is False


# ─────────────────────────────────────────────────────────────────────────────
# SU17: load_token
# ─────────────────────────────────────────────────────────────────────────────

def test_load_token_returns_dict(tmp_path: Path) -> None:
    """load_token returns dict for valid file."""
    p = tmp_path / "token.json"
    _write_token(p)
    result = load_token(p)
    assert isinstance(result, dict)
    assert result["account_id"] == "LFL836"


def test_load_token_missing_file_returns_none(tmp_path: Path) -> None:
    """load_token returns None for missing file."""
    assert load_token(tmp_path / "ghost.json") is None


# ─────────────────────────────────────────────────────────────────────────────
# SU10e: exchange_request_token
# ─────────────────────────────────────────────────────────────────────────────

def test_exchange_request_token_success() -> None:
    """Successful exchange returns the access_token string."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"access_token": "live_token_xyz"}}

    with patch("scripts.zerodha_login.requests.post", return_value=mock_resp):
        token = exchange_request_token("api_key", "api_secret", "req_token_abc")

    assert token == "live_token_xyz"


def test_exchange_request_token_api_error_raises() -> None:
    """Non-200 response raises RuntimeError."""
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.text = "Invalid checksum"

    with patch("scripts.zerodha_login.requests.post", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="403"):
            exchange_request_token("api_key", "api_secret", "bad_token")


# ─────────────────────────────────────────────────────────────────────────────
# SU10f: save_token
# ─────────────────────────────────────────────────────────────────────────────

def test_save_token_creates_correct_json(tmp_path: Path) -> None:
    """save_token writes a valid JSON with all required fields."""
    p = tmp_path / "session" / "zerodha_token.json"
    save_token("LFL836", "zerodha", "my_api_key", "my_access_token", p)

    assert p.exists()
    with open(p, encoding="utf-8") as fh:
        record = json.load(fh)

    assert record["account_id"] == "LFL836"
    assert record["broker"] == "zerodha"
    assert record["access_token"] == "my_access_token"
    assert record["api_key"] == "my_api_key"
    today_str = datetime.now(_IST).date().isoformat()
    assert record["date"] == today_str
    assert "saved_at" in record
    assert "expires_at" in record


# ─────────────────────────────────────────────────────────────────────────────
# Login URL construction
# ─────────────────────────────────────────────────────────────────────────────

def test_login_url_contains_api_key() -> None:
    """Login URL template correctly embeds api_key."""
    url = _KITE_LOGIN_URL.format(api_key="MY_KEY_123")
    assert "MY_KEY_123" in url
    assert "kite.zerodha.com" in url


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback

    tests_fn = [
        test_is_token_valid_valid_token,
        test_is_token_valid_wrong_account,
        test_is_token_valid_old_date,
        test_is_token_valid_missing_file,
        test_is_token_valid_corrupt_json,
        test_load_token_returns_dict,
        test_load_token_missing_file_returns_none,
        test_exchange_request_token_success,
        test_exchange_request_token_api_error_raises,
        test_save_token_creates_correct_json,
        test_login_url_contains_api_key,
    ]

    passed = failed = 0
    for fn in tests_fn:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                # Only call with tmp_path if the function needs it
                import inspect
                sig = inspect.signature(fn)
                if sig.parameters:
                    fn(Path(tmp))
                else:
                    fn()
                print(f"  OK  {fn.__name__}")
                passed += 1
            except Exception:
                print(f"  FAIL  {fn.__name__}")
                traceback.print_exc()
                failed += 1

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)
