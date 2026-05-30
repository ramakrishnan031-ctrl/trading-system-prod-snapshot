"""
tests/unit/test_fix128_daily_loss_sequence.py

Tests for FIX-128 Fix D: daily loss limit kill sequence.
Tests the _make_daily_loss_cb callback sequence in isolation:
  - Telegram alert fires BEFORE soft_kill
  - EodSquareoff.fire_now() fires BEFORE soft_kill
  - Sequence works without EodSquareoff (eod_ref={})
  - soft_kill always fires even if eod.fire_now() raises
  - Paper mode: same sequence (paper adapter simulates closes)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def _log() -> logging.Logger:
    return logging.getLogger("test_fix128_daily_loss")


# ─────────────────────────────────────────────────────────────────────────────
# Replicate _make_daily_loss_cb logic for isolated testing
# (avoids importing all of main.py with its heavy dependencies)
# ─────────────────────────────────────────────────────────────────────────────

def _make_daily_loss_cb_isolated(
    kill_switch,
    notifier,
    mode: str = "LIVE",
    eod_ref: Optional[dict] = None,
    log=None,
):
    """Isolated version of main._make_daily_loss_cb for unit testing."""
    _lg = log or logging.getLogger("daily_loss_test")

    def _on_daily_loss_breach() -> None:
        _lg.critical("daily_loss_limit.breach_sequence_start")

        if notifier is not None:
            try:
                notifier.send(
                    severity="CRITICAL",
                    title=f"[{mode}] DAILY LOSS LIMIT HIT",
                    body="Closing all positions and halting new trades.",
                    source_module="main",
                )
            except Exception as ne:
                _lg.error("notifier.send failed: %s", ne)

        eod_instance = (eod_ref or {}).get("eod")
        if eod_instance is not None:
            try:
                eod_instance.fire_now(
                    reason="daily_loss_limit_breached",
                    triggered_by="fund_manager",
                )
            except Exception as exc:
                _lg.error("eod_fire_now_failed: %s", exc)

        kill_switch.soft_kill(
            reason="daily_loss_limit_breached",
            triggered_by="fund_manager",
        )

    return _on_daily_loss_breach


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDailyLossSequence:

    def test_sequence_order_alert_then_eod_then_kill(self) -> None:
        """Correct sequence: Telegram alert → eod.fire_now() → soft_kill."""
        sequence: List[str] = []
        kill_switch = MagicMock()
        kill_switch.soft_kill.side_effect = lambda **kw: sequence.append("soft_kill")
        notifier = MagicMock()
        notifier.send.side_effect = lambda **kw: sequence.append("telegram_alert")
        eod = MagicMock()
        eod.fire_now.side_effect = lambda **kw: sequence.append("eod_fire_now")

        cb = _make_daily_loss_cb_isolated(
            kill_switch=kill_switch,
            notifier=notifier,
            mode="LIVE",
            eod_ref={"eod": eod},
        )
        cb()

        assert sequence == ["telegram_alert", "eod_fire_now", "soft_kill"], (
            f"Wrong sequence: {sequence}. Expected: alert → eod → kill"
        )
        print("  OK: sequence = alert → eod.fire_now() → soft_kill")

    def test_telegram_alert_severity_critical(self) -> None:
        """Alert must be CRITICAL severity."""
        kill_switch = MagicMock()
        notifier = MagicMock()
        cb = _make_daily_loss_cb_isolated(
            kill_switch=kill_switch, notifier=notifier,
        )
        cb()
        call_kwargs = notifier.send.call_args.kwargs
        assert call_kwargs["severity"] == "CRITICAL"
        assert "LOSS LIMIT HIT" in call_kwargs["title"].upper() or "DAILY LOSS" in call_kwargs["title"].upper()
        print("  OK: Telegram alert is CRITICAL severity")

    def test_soft_kill_always_fires_even_if_eod_raises(self) -> None:
        """soft_kill fires even when eod.fire_now() raises an exception."""
        kill_switch = MagicMock()
        eod = MagicMock()
        eod.fire_now.side_effect = RuntimeError("EOD failed")

        cb = _make_daily_loss_cb_isolated(
            kill_switch=kill_switch, notifier=None,
            eod_ref={"eod": eod},
        )
        cb()  # should not raise

        assert kill_switch.soft_kill.called, "soft_kill must fire even if eod.fire_now raises"
        print("  OK: soft_kill fires even if eod.fire_now() raises")

    def test_soft_kill_fires_without_eod(self) -> None:
        """Without EodSquareoff wired, soft_kill still fires."""
        kill_switch = MagicMock()
        cb = _make_daily_loss_cb_isolated(
            kill_switch=kill_switch, notifier=None,
            eod_ref=None,  # not wired
        )
        cb()
        assert kill_switch.soft_kill.called
        print("  OK: soft_kill fires without EodSquareoff wired")

    def test_soft_kill_fires_without_eod_ref_eod_none(self) -> None:
        """eod_ref={"eod": None} → eod.fire_now() skipped, soft_kill still fires."""
        kill_switch = MagicMock()
        cb = _make_daily_loss_cb_isolated(
            kill_switch=kill_switch, notifier=None,
            eod_ref={"eod": None},
        )
        cb()
        assert kill_switch.soft_kill.called
        print("  OK: eod=None in eod_ref → soft_kill still fires")

    def test_no_hard_kill_only_soft_kill(self) -> None:
        """Daily loss limit uses SOFT_KILL (not HARD_KILL) — positions will be closed first."""
        kill_switch = MagicMock()
        cb = _make_daily_loss_cb_isolated(
            kill_switch=kill_switch, notifier=None,
        )
        cb()
        assert kill_switch.soft_kill.called, "soft_kill must be called"
        assert not kill_switch.hard_kill.called, "hard_kill must NOT be called for daily loss limit"
        print("  OK: SOFT_KILL used (not hard_kill) for daily loss limit")

    def test_eod_fire_now_called_with_correct_args(self) -> None:
        """eod.fire_now() is called with reason=daily_loss_limit_breached."""
        kill_switch = MagicMock()
        eod = MagicMock()
        cb = _make_daily_loss_cb_isolated(
            kill_switch=kill_switch, notifier=None,
            eod_ref={"eod": eod},
        )
        cb()
        assert eod.fire_now.called
        call_kwargs = eod.fire_now.call_args.kwargs
        assert "daily_loss" in call_kwargs.get("reason", "").lower()
        print("  OK: eod.fire_now() called with daily_loss reason")

    def test_notifier_failure_does_not_abort_sequence(self) -> None:
        """If Telegram alert fails, eod.fire_now() and soft_kill still run."""
        sequence: List[str] = []
        kill_switch = MagicMock()
        kill_switch.soft_kill.side_effect = lambda **kw: sequence.append("soft_kill")
        notifier = MagicMock()
        notifier.send.side_effect = RuntimeError("Telegram down")
        eod = MagicMock()
        eod.fire_now.side_effect = lambda **kw: sequence.append("eod_fire_now")

        cb = _make_daily_loss_cb_isolated(
            kill_switch=kill_switch, notifier=notifier,
            eod_ref={"eod": eod},
        )
        cb()

        assert "eod_fire_now" in sequence, "eod.fire_now should still run after alert failure"
        assert "soft_kill" in sequence, "soft_kill should still run after alert failure"
        print("  OK: Telegram failure does not abort eod.fire_now or soft_kill")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        TestDailyLossSequence().test_sequence_order_alert_then_eod_then_kill,
        TestDailyLossSequence().test_telegram_alert_severity_critical,
        TestDailyLossSequence().test_soft_kill_always_fires_even_if_eod_raises,
        TestDailyLossSequence().test_soft_kill_fires_without_eod,
        TestDailyLossSequence().test_soft_kill_fires_without_eod_ref_eod_none,
        TestDailyLossSequence().test_no_hard_kill_only_soft_kill,
        TestDailyLossSequence().test_eod_fire_now_called_with_correct_args,
        TestDailyLossSequence().test_notifier_failure_does_not_abort_sequence,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
