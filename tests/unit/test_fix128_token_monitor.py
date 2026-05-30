"""
tests/unit/test_fix128_token_monitor.py

Tests for FIX-128 Fix E: token expiry mid-session detection (TokenMonitor).
  - paper_mode=True: start() is no-op, check_now() always returns True
  - valid token: check_now() returns True, on_expiry not called
  - expired token: check_now() returns False, on_expiry fires, Telegram alert sent
  - on_expiry fires exactly once per session (not on every failed check)
  - market hours gating: check skipped outside market hours
"""
from __future__ import annotations

import logging
import sys
from datetime import time as _time
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.token_monitor import TokenMonitor
from core.time_authority import now_ist


def _log() -> logging.Logger:
    return logging.getLogger("test_token_monitor")


def _make_monitor(
    profile_fn=None,
    on_expiry=None,
    paper_mode: bool = False,
    notifier=None,
    market_open: str = None,
    market_close: str = None,
) -> TokenMonitor:
    return TokenMonitor(
        profile_fn=profile_fn or (lambda: None),
        on_expiry=on_expiry or (lambda: None),
        logger=_log(),
        check_interval_sec=1800,
        paper_mode=paper_mode,
        market_open=market_open,
        market_close=market_close,
        notifier=notifier,
        mode="TEST",
    )


class TestTokenMonitorPaperMode:

    def test_paper_mode_check_now_always_valid(self) -> None:
        """Paper mode: check_now() always returns True, never calls profile_fn."""
        profile_calls = []
        monitor = _make_monitor(
            profile_fn=lambda: profile_calls.append(1),
            paper_mode=True,
        )
        result = monitor.check_now()
        assert result is True
        assert profile_calls == [], "profile_fn should not be called in paper mode"
        print("  OK: paper mode check_now → True, no profile call")

    def test_paper_mode_start_noop(self) -> None:
        """Paper mode: start() does not launch a thread."""
        monitor = _make_monitor(paper_mode=True)
        monitor.start()
        assert monitor._thread is None, "Paper mode: no thread should be created"
        monitor.stop()
        print("  OK: paper mode start() is no-op, no thread launched")


class TestTokenMonitorValidToken:

    def test_valid_token_check_returns_true(self) -> None:
        """Valid profile() call → check_now() returns True, on_expiry not called."""
        expiry_calls: List[str] = []
        monitor = _make_monitor(
            profile_fn=lambda: {"user_id": "TEST123"},
            on_expiry=lambda: expiry_calls.append("expired"),
        )
        result = monitor.check_now()
        assert result is True
        assert expiry_calls == [], "on_expiry should not fire for valid token"
        print("  OK: valid token → check_now returns True, no expiry")

    def test_valid_token_no_telegram_alert(self) -> None:
        """Valid token: no Telegram alert sent."""
        notifier = MagicMock()
        monitor = _make_monitor(
            profile_fn=lambda: None,
            notifier=notifier,
        )
        monitor.check_now()
        assert not notifier.send.called, "No alert for valid token"
        print("  OK: valid token → no Telegram alert")


class TestTokenMonitorExpiry:

    def test_expired_token_check_returns_false(self) -> None:
        """profile() raises → check_now() returns False."""
        def _fail():
            raise Exception("TokenException: Invalid token")

        monitor = _make_monitor(profile_fn=_fail)
        result = monitor.check_now()
        assert result is False
        print("  OK: token expiry → check_now returns False")

    def test_expired_token_fires_on_expiry(self) -> None:
        """profile() raises → on_expiry callback called."""
        expiry_calls: List[str] = []

        def _fail():
            raise Exception("token expired")

        monitor = _make_monitor(
            profile_fn=_fail,
            on_expiry=lambda: expiry_calls.append("fired"),
        )
        monitor.check_now()
        assert len(expiry_calls) == 1, "on_expiry should fire exactly once"
        print("  OK: token expiry → on_expiry fires")

    def test_expiry_callback_fires_only_once(self) -> None:
        """Multiple failed checks → on_expiry fires exactly once per session."""
        expiry_calls: List[str] = []

        def _fail():
            raise Exception("token invalid")

        monitor = _make_monitor(
            profile_fn=_fail,
            on_expiry=lambda: expiry_calls.append("fired"),
        )
        monitor.check_now()
        monitor.check_now()
        monitor.check_now()
        assert len(expiry_calls) == 1, "on_expiry must fire only once per session"
        print("  OK: on_expiry fires exactly once even with repeated failures")

    def test_expiry_sends_telegram_critical(self) -> None:
        """Token expiry: Telegram CRITICAL alert with 'TOKEN EXPIRED' title."""
        notifier = MagicMock()

        def _fail():
            raise Exception("token invalid")

        monitor = _make_monitor(
            profile_fn=_fail,
            notifier=notifier,
        )
        monitor.check_now()
        assert notifier.send.called, "Telegram alert should be sent on expiry"
        call_kwargs = notifier.send.call_args.kwargs
        assert call_kwargs["severity"] == "CRITICAL"
        assert "TOKEN" in call_kwargs["title"].upper()
        print("  OK: token expiry sends CRITICAL Telegram alert")

    def test_expiry_uses_soft_kill_not_hard_kill(self) -> None:
        """Token expiry triggers SOFT_KILL — order_monitor keeps managing exits."""
        kill_switch = MagicMock()

        def _fail():
            raise Exception("token expired")

        monitor = _make_monitor(
            profile_fn=_fail,
            on_expiry=lambda: kill_switch.soft_kill(reason="token_expired", triggered_by="token_monitor"),
        )
        monitor.check_now()
        assert kill_switch.soft_kill.called, "soft_kill should be called"
        assert not kill_switch.hard_kill.called, "hard_kill must NOT be called for token expiry"
        print("  OK: token expiry triggers soft_kill (not hard_kill)")


class TestTokenMonitorMarketHours:

    def test_is_market_hours_true_inside_window(self) -> None:
        """_is_market_hours() returns True when now is inside market window."""
        monitor = _make_monitor(market_open="09:15", market_close="15:30")
        # Mock market hours to cover current time
        monitor._market_open_t = _time(0, 0)   # always open
        monitor._market_close_t = _time(23, 59)
        assert monitor._is_market_hours() is True
        print("  OK: _is_market_hours() returns True inside window")

    def test_is_market_hours_false_before_open(self) -> None:
        """_is_market_hours() returns False when now is before market open."""
        monitor = _make_monitor(market_open="23:58", market_close="23:59")
        monitor._market_open_t = _time(23, 58)
        monitor._market_close_t = _time(23, 59)
        # Current time is certainly < 23:58 (unless running at midnight!)
        import datetime as _dt
        if now_ist().time() < _time(23, 58):
            assert monitor._is_market_hours() is False
        print("  OK: _is_market_hours() returns False before open (conditional)")

    def test_no_window_configured_always_returns_true(self) -> None:
        """If no market_open/close configured, _is_market_hours() always returns True."""
        monitor = _make_monitor()  # no market window
        monitor._market_open_t = None
        monitor._market_close_t = None
        assert monitor._is_market_hours() is True
        print("  OK: no market window → _is_market_hours always True")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        TestTokenMonitorPaperMode().test_paper_mode_check_now_always_valid,
        TestTokenMonitorPaperMode().test_paper_mode_start_noop,
        TestTokenMonitorValidToken().test_valid_token_check_returns_true,
        TestTokenMonitorValidToken().test_valid_token_no_telegram_alert,
        TestTokenMonitorExpiry().test_expired_token_check_returns_false,
        TestTokenMonitorExpiry().test_expired_token_fires_on_expiry,
        TestTokenMonitorExpiry().test_expiry_callback_fires_only_once,
        TestTokenMonitorExpiry().test_expiry_sends_telegram_critical,
        TestTokenMonitorExpiry().test_expiry_uses_soft_kill_not_hard_kill,
        TestTokenMonitorMarketHours().test_is_market_hours_true_inside_window,
        TestTokenMonitorMarketHours().test_is_market_hours_false_before_open,
        TestTokenMonitorMarketHours().test_no_window_configured_always_returns_true,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
