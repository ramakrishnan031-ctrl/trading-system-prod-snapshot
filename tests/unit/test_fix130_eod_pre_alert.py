"""
tests/unit/test_fix130_eod_pre_alert.py

FIX-130 Item 7: EOD pre-alert at 14:45 IST.
  - _fire_eod_pre_alert sends Telegram when open positions exist
  - No alert sent when no open positions
  - Alert body includes symbol, direction, qty, entry_price
"""
from __future__ import annotations

import sys
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.state_store import StateStore
from core.ids import new_trade_id, new_signal_id
from core.time_authority import now_ist


def _log():
    return logging.getLogger("test_fix130_eod_pre_alert")


def _make_store(tmp: Path) -> StateStore:
    return StateStore(tmp / "test.db")


def _seed_open_trade(store: StateStore, symbol: str, direction: str,
                     qty: int = 10, price: float = 1000.0) -> str:
    trade_id = new_trade_id()
    sig_id = new_signal_id()
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id, symbol, scanner, strategy,
               triggered_at, received_at, expires_at, status, fingerprint, fingerprint_date)
               VALUES (?, ?, 'test', 'test', ?, ?, ?, 'TRADED', ?, ?)""",
            (sig_id, symbol, now, now, now, sig_id[:8], now[:10]),
        )
        cur.execute(
            """INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy,
               status, qty_planned, entry_target_price, sl_initial, tgt_initial,
               order_protocol, margin_reserved, risk_amount,
               qty_filled, entry_actual_price, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'test', 'OPEN',
               ?, ?, 0.0, 0.0, 'LIMIT_TRIPLE', 200.0, 50.0,
               ?, ?, ?, ?)""",
            (trade_id, sig_id, symbol, direction, qty, price, qty, price, now, now),
        )
    return trade_id


class TestEodPreAlert:

    def test_alert_sent_when_open_positions(self) -> None:
        """_fire_eod_pre_alert sends Telegram when there are open positions."""
        import importlib
        main_mod = importlib.import_module("main")
        notifier = MagicMock()
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store, "RELIANCE", "LONG", qty=10, price=2500.0)
            _seed_open_trade(store, "TCS", "SHORT", qty=5, price=3000.0)

            main_mod._fire_eod_pre_alert(store, notifier, "PAPER", _log())

            notifier.send.assert_called_once()
            call_kwargs = notifier.send.call_args
            body = call_kwargs[1].get("body", "")
            assert "RELIANCE" in body
            assert "TCS" in body
            assert "LONG" in body
            assert "SHORT" in body
            store.close()
        print("  OK: alert sent with open positions content")

    def test_no_alert_when_no_open_positions(self) -> None:
        """_fire_eod_pre_alert does not send Telegram when no open positions."""
        import importlib
        main_mod = importlib.import_module("main")
        notifier = MagicMock()
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            # No open trades

            main_mod._fire_eod_pre_alert(store, notifier, "PAPER", _log())

            notifier.send.assert_not_called()
            store.close()
        print("  OK: no alert when no open positions")

    def test_no_alert_when_notifier_is_none(self) -> None:
        """_fire_eod_pre_alert gracefully handles notifier=None."""
        import importlib
        main_mod = importlib.import_module("main")
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store, "RELIANCE", "LONG")

            # Should not raise even without notifier
            main_mod._fire_eod_pre_alert(store, None, "LIVE", _log())
            store.close()
        print("  OK: no raise when notifier=None")

    def test_alert_body_includes_count(self) -> None:
        """Alert body starts with count of open positions."""
        import importlib
        main_mod = importlib.import_module("main")
        notifier = MagicMock()
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store, "RELIANCE", "LONG")
            _seed_open_trade(store, "TCS", "SHORT")
            _seed_open_trade(store, "INFY", "LONG")

            main_mod._fire_eod_pre_alert(store, notifier, "LIVE", _log())

            body = notifier.send.call_args[1]["body"]
            assert "3 position" in body
            store.close()
        print("  OK: alert body includes position count")


if __name__ == "__main__":
    tests = [
        TestEodPreAlert().test_alert_sent_when_open_positions,
        TestEodPreAlert().test_no_alert_when_no_open_positions,
        TestEodPreAlert().test_no_alert_when_notifier_is_none,
        TestEodPreAlert().test_alert_body_includes_count,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
