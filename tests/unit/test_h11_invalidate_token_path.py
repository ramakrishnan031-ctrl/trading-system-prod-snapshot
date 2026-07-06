"""
tests/unit/test_h11_invalidate_token_path.py

Wave-3 / H-11 — main._invalidate_token() must operate on the REAL broker-token
path (data_store/session/zerodha_token.json), the same file the loader reads at
startup — not the process CWD.

Root cause (pre-fix): _invalidate_token used Path("zerodha_token.json") (CWD),
while the loader (scripts.zerodha_login.is_token_valid/load_token, called from
main.py live startup) reads data_store/session/zerodha_token.json. On a
BrokerAuthError, _shutdown -> _invalidate_token was supposed to rename the dead
token so the next non-interactive live boot fails fast at the is_token_valid()
gate (return 6) and the systemd restart loop breaks. The wrong path meant it
logged "not found, skipping" and left the dead token in place — the FIX-062
auth-restart-loop guard was inert (the service could loop on a revoked token).

These tests drive the REAL _invalidate_token / _shutdown against a genuine token
file at the loader's real relative path (via chdir into a temp dir), and verify
with the REAL loader (zerodha_login.is_token_valid / load_token).

Real collaborators:  main._invalidate_token, main._shutdown (real function),
                     the _TOKEN_PATH constant, zerodha_login.is_token_valid /
                     load_token reading a REAL file on a temp path.
Simulated:           _shutdown's component collaborators (MagicMock .stop/etc.);
                     chdir to a temp dir so the file op is genuine, not mocked.

RED/GREEN: test_h11_invalidate_acts_on_real_token and
test_h11_shutdown_breaks_restart_loop FAIL against the unfixed CWD path (the real
token survives -> loader still loads it) and PASS after the fix.

Run: python -m pytest tests/unit/test_h11_invalidate_token_path.py -v
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.time_authority import now_ist
from scripts.zerodha_login import is_token_valid, load_token

_REAL_TOKEN_REL = Path("data_store/session/zerodha_token.json")
_ACCOUNT = "TESTACC"


def _write_valid_token() -> Path:
    """Write a genuinely VALID token (account+today's IST date+non-empty token) at
    the loader's real relative path under the current (temp) CWD."""
    p = _REAL_TOKEN_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "account_id": _ACCOUNT,
        "date": now_ist().date().isoformat(),
        "access_token": "live_abc123",
        "api_key": "k",
    }))
    return p


def _shutdown_mocks() -> dict:
    return {
        "signal_proc": MagicMock(stop=MagicMock()),
        "entry_gate": MagicMock(stop=MagicMock()),
        "smart_tgt": MagicMock(stop=MagicMock()),
        "order_reconciler": MagicMock(stop=MagicMock()),
        "order_monitor": MagicMock(stop=MagicMock()),
        "live_feed": MagicMock(disconnect=MagicMock()),
        "candle_store": MagicMock(stop=MagicMock()),
        "notifier": MagicMock(send=MagicMock()),
        "store": MagicMock(close=MagicMock()),
        "webhook_receiver": MagicMock(stop=MagicMock()),
        "clock_skew_probe": MagicMock(stop=MagicMock()),
    }


# ── Test 1 — invalidation acts on the REAL token (core bug; RED before fix) ───

def test_h11_invalidate_acts_on_real_token() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        try:
            os.chdir(tmp)
            _write_valid_token()

            import main as main_module
            main_module._log = logging.getLogger("test_h11")

            # Loader sees a VALID token before invalidation.
            assert is_token_valid(_ACCOUNT, _REAL_TOKEN_REL) is True

            main_module._invalidate_token()

            # Unfixed adapter renamed the CWD path -> the REAL token survives and
            # is_token_valid stays True -> this FAILS (RED). Fixed: gone/invalid.
            assert load_token(_REAL_TOKEN_REL) is None
            assert is_token_valid(_ACCOUNT, _REAL_TOKEN_REL) is False
            assert Path("data_store/session/zerodha_token.invalid").exists()
            print("  OK H-11 T1: invalidate renames the REAL token "
                  "(loader can no longer load it)")
        finally:
            os.chdir(cwd)


# ── Test 2 — restart-loop guard breaks the loop (RED before fix) ──────────────

def test_h11_shutdown_breaks_restart_loop() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        try:
            os.chdir(tmp)
            _write_valid_token()

            import main as main_module
            main_module._log = logging.getLogger("test_h11")
            main_module._broker_auth_failed = True   # BrokerAuthError happened

            main_module._shutdown(**_shutdown_mocks())

            # Next non-interactive live boot gates on is_token_valid() (main.py
            # :1800 -> "Token missing or expired" -> return 6). After a correct
            # invalidation that gate is False, so the dead token is NOT reused.
            # RED before fix: the real token survived -> is_token_valid True.
            assert is_token_valid(_ACCOUNT, _REAL_TOKEN_REL) is False
            assert load_token(_REAL_TOKEN_REL) is None
            print("  OK H-11 T2: auth-fail shutdown invalidates the real token "
                  "-> next boot fails fast (loop broken)")
        finally:
            os.chdir(cwd)
            import main as main_module
            main_module._broker_auth_failed = False


# ── Test 3 — no clash: a valid token round-trips when NOT invalidated ─────────

def test_h11_valid_token_survives_when_flag_not_set() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        try:
            os.chdir(tmp)
            _write_valid_token()

            import main as main_module
            main_module._log = logging.getLogger("test_h11")
            main_module._broker_auth_failed = False   # no auth failure

            main_module._shutdown(**_shutdown_mocks())

            # Invalidation not triggered -> the loader still loads the valid token
            # (writer/loader path convention intact).
            assert is_token_valid(_ACCOUNT, _REAL_TOKEN_REL) is True
            assert not Path("data_store/session/zerodha_token.invalid").exists()
            print("  OK H-11 T3: valid token round-trips when flag not set "
                  "(no clash with loader)")
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    test_h11_invalidate_acts_on_real_token()
    test_h11_shutdown_breaks_restart_loop()
    test_h11_valid_token_survives_when_flag_not_set()
    print("\nAll H-11 tests passed.")
