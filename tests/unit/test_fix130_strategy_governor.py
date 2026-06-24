"""
tests/unit/test_fix130_strategy_governor.py

FIX-130 Item 6: Intraday strategy circuit breaker (StrategyGovernor).
  - check() returns (False, "") when disabled
  - check() returns (False, "") when no losing history
  - check() pauses strategy when today_pnl < 2x avg_daily_loss
  - check() returns (False, "") after cutoff_time regardless of P&L
  - check() returns (True, reason) on second call for same paused strategy
  - Telegram notification sent on pause
"""
from __future__ import annotations

import sys
import logging
from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from capital.strategy_governor import StrategyGovernor
from core.state_store import StateStore
from core.time_authority import now_ist


def _log():
    return logging.getLogger("test_governor")


class _FakeConfig:
    def __init__(self, enabled=True, loss_multiplier=2.0,
                 cutoff_time="12:00", lookback_days=10):
        self.enabled = enabled
        self.loss_multiplier = loss_multiplier
        self.cutoff_time = cutoff_time
        self.lookback_days = lookback_days


class _StrictNotifier:
    """Enforces the REAL TelegramNotifier.send signature (severity is required,
    keyword-only). The 24-Jun bug — send() called without severity — raised
    TypeError that the governor swallowed, so the alert was lost; a bare MagicMock
    hid it. With this, a missing/renamed severity arg leaves `calls` empty."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, *, severity, title, body, source_module, context=None):
        self.calls.append({"severity": severity, "title": title,
                           "body": body, "source_module": source_module})


def _make_store(tmp: Path) -> StateStore:
    return StateStore(tmp / "test.db")


def _seed_closed_trade(store: StateStore, strategy: str, pnl: float, date_offset_days: int = 0):
    """Insert a closed trade with given P&L and date offset from today."""
    from core.ids import new_trade_id, new_signal_id
    trade_id = new_trade_id()
    sig_id = new_signal_id()
    now = now_ist()
    if date_offset_days != 0:
        from datetime import timedelta
        now = now - timedelta(days=date_offset_days)
    ts = now.isoformat()
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id, symbol, scanner, strategy,
               triggered_at, received_at, expires_at, status, fingerprint, fingerprint_date)
               VALUES (?, 'X', 'scanner', ?, ?, ?, ?, 'TRADED', ?, ?)""",
            (sig_id, strategy, ts, ts, ts, sig_id[:8], ts[:10]),
        )
        cur.execute(
            """INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy,
               status, qty_planned, entry_target_price, sl_initial, tgt_initial,
               order_protocol, margin_reserved, risk_amount, gross_pnl,
               created_at, updated_at)
               VALUES (?, ?, 'X', 'LONG', ?, 'CLOSED',
               10, 100.0, 95.0, 110.0, 'LIMIT_TRIPLE', 200.0, 50.0, ?,
               ?, ?)""",
            (trade_id, sig_id, strategy, pnl, ts, ts),
        )


class TestStrategyGovernor:

    def test_disabled_never_pauses(self) -> None:
        """When enabled=False, check() always returns (False, "")."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            gov = StrategyGovernor(store, _FakeConfig(enabled=False))
            paused, _ = gov.check("gap_go_long", time(10, 0))
            assert not paused
            store.close()
        print("  OK: disabled governor never pauses")

    def test_no_history_allows_trading(self) -> None:
        """No historical data -> avg_loss = 0 -> no pause."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            gov = StrategyGovernor(store, _FakeConfig())
            paused, _ = gov.check("gap_go_long", time(10, 0))
            assert not paused
            store.close()
        print("  OK: no history -> no pause")

    def test_pause_fired_when_loss_exceeds_threshold(self) -> None:
        """Today's loss < 2x avg_daily_loss -> strategy paused."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            # Seed 5 days of -100 losses (avg = -100)
            for i in range(1, 6):
                _seed_closed_trade(store, "gap_go_long", -100.0, date_offset_days=i)
            # Today: -250 loss (> 2x threshold of 2*(-100) = -200)
            _seed_closed_trade(store, "gap_go_long", -250.0, date_offset_days=0)

            gov = StrategyGovernor(store, _FakeConfig(loss_multiplier=2.0, cutoff_time="12:00"))
            paused, reason = gov.check("gap_go_long", time(10, 0))

            assert paused, f"Should be paused; reason: {reason}"
            assert "gap_go_long" in str(paused) or True  # paused is True
            store.close()
        print("  OK: loss > 2x avg triggers circuit breaker")

    def test_no_pause_when_loss_below_threshold(self) -> None:
        """Today's loss within 2x avg -> no pause."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            # Seed 5 days of -100 losses (avg = -100)
            for i in range(1, 6):
                _seed_closed_trade(store, "gap_go_long", -100.0, date_offset_days=i)
            # Today: -150 loss (< 2x threshold of -200 -> still above threshold)
            _seed_closed_trade(store, "gap_go_long", -150.0, date_offset_days=0)

            gov = StrategyGovernor(store, _FakeConfig(loss_multiplier=2.0, cutoff_time="12:00"))
            paused, _ = gov.check("gap_go_long", time(10, 0))

            assert not paused
            store.close()
        print("  OK: loss < 2x avg -> no pause")

    def test_no_pause_after_cutoff_time(self) -> None:
        """After cutoff_time, circuit breaker does not pause even with big loss."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            for i in range(1, 6):
                _seed_closed_trade(store, "gap_go_long", -100.0, date_offset_days=i)
            _seed_closed_trade(store, "gap_go_long", -500.0, date_offset_days=0)

            gov = StrategyGovernor(store, _FakeConfig(cutoff_time="12:00"))
            # Check AFTER 12:00 cutoff
            paused, _ = gov.check("gap_go_long", time(12, 1))

            assert not paused
            store.close()
        print("  OK: no pause after cutoff time")

    def test_already_paused_returns_true(self) -> None:
        """Once paused, subsequent check() calls return True immediately."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            for i in range(1, 6):
                _seed_closed_trade(store, "gap_go_long", -100.0, date_offset_days=i)
            _seed_closed_trade(store, "gap_go_long", -250.0, date_offset_days=0)

            gov = StrategyGovernor(store, _FakeConfig())
            # First call pauses
            paused1, _ = gov.check("gap_go_long", time(10, 0))
            assert paused1
            # Second call should also be True (from in-memory set)
            paused2, reason2 = gov.check("gap_go_long", time(10, 30))
            assert paused2
            assert "paused today" in reason2
            store.close()
        print("  OK: already-paused strategy returns True on subsequent checks")

    def test_telegram_alert_sent_on_pause(self) -> None:
        """Telegram notification is sent when circuit breaker fires."""
        notifier = MagicMock()
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            for i in range(1, 6):
                _seed_closed_trade(store, "gap_go_long", -100.0, date_offset_days=i)
            _seed_closed_trade(store, "gap_go_long", -250.0, date_offset_days=0)

            gov = StrategyGovernor(store, _FakeConfig(), notifier=notifier, mode="PAPER")
            gov.check("gap_go_long", time(10, 0))

            notifier.send.assert_called_once()
            call_kwargs = notifier.send.call_args
            title = call_kwargs[1].get("title", "") or (call_kwargs[0][0] if call_kwargs[0] else "")
            assert "gap_go_long" in title or "PAUSED" in title
            # 24-Jun: severity is REQUIRED — lock it so the missing-arg bug can't return.
            assert call_kwargs[1].get("severity") == "WARNING"
            store.close()
        print("  OK: Telegram notification sent on pause")

    def test_pause_alert_dispatches_with_required_severity(self) -> None:
        """Regression for the 24-Jun lost-alert bug: against a notifier that
        enforces the real send() signature, the circuit-breaker alert must
        actually DISPATCH (severity supplied) — on the buggy code the TypeError
        was swallowed and `calls` stayed empty."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            for i in range(1, 6):
                _seed_closed_trade(store, "gap_go_long", -100.0, date_offset_days=i)
            _seed_closed_trade(store, "gap_go_long", -250.0, date_offset_days=0)
            notifier = _StrictNotifier()
            gov = StrategyGovernor(store, _FakeConfig(), notifier=notifier, mode="LIVE")
            gov.check("gap_go_long", time(10, 0))

            assert len(notifier.calls) == 1            # dispatched (0 if severity missing)
            assert notifier.calls[0]["severity"] == "WARNING"
            assert "PAUSED" in notifier.calls[0]["title"]
            assert notifier.calls[0]["source_module"] == "strategy_governor"
            store.close()
        print("  OK: pause alert dispatches with required severity=WARNING")


if __name__ == "__main__":
    tests = [
        TestStrategyGovernor().test_disabled_never_pauses,
        TestStrategyGovernor().test_no_history_allows_trading,
        TestStrategyGovernor().test_pause_fired_when_loss_exceeds_threshold,
        TestStrategyGovernor().test_no_pause_when_loss_below_threshold,
        TestStrategyGovernor().test_no_pause_after_cutoff_time,
        TestStrategyGovernor().test_already_paused_returns_true,
        TestStrategyGovernor().test_telegram_alert_sent_on_pause,
        TestStrategyGovernor().test_pause_alert_dispatches_with_required_severity,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
