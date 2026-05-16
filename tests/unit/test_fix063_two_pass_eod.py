"""
FIX-063: Two-pass EOD squareoff with 2s sleep.

Tests that EOD squareoff does Pass 1 (cancel), sleeps 2s, then Pass 2 (exit).
"""
from __future__ import annotations

import logging
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.order_state_machine import OrderStateMachine
from broker.zerodha_adapter import ZerodhaAdapter
from capital.fund_manager import FundManager
from capital.kill_switch import KillSwitch
from core.events import EventBus
from core.market_windows import MarketWindows
from core.state_store import StateStore
from orders.eod_squareoff import EodSquareoff

_IST = timezone(offset=__import__("datetime").timedelta(hours=5, minutes=30))
_SCHEMA_PATH = Path(__file__).parent.parent.parent / "core" / "schema.sql"


def _make_store(tmp_dir: Path) -> StateStore:
    return StateStore(tmp_dir / "test.db", _SCHEMA_PATH)


def test_two_pass_with_2s_sleep() -> None:
    """FIX-063: _fire() executes Pass 1 (cancel), sleeps 2s, then Pass 2 (exit)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))

        # Mocks
        mock_adapter = MagicMock(spec=ZerodhaAdapter)
        mock_adapter.get_positions.return_value = []  # No positions
        mock_adapter.cancel_order.return_value = MagicMock(success=True)

        mock_fm = MagicMock(spec=FundManager)
        mock_fm.release.return_value = True

        mock_sm = MagicMock(spec=OrderStateMachine)
        mock_bus = MagicMock(spec=EventBus)
        mock_ks = MagicMock(spec=KillSwitch)
        mock_ks.is_active.return_value = False

        mock_mw = MagicMock(spec=MarketWindows)
        mock_ta = MagicMock()  # time_authority module

        eod = EodSquareoff(
            adapter=mock_adapter,
            state_store=store,
            fund_manager=mock_fm,
            state_machine=mock_sm,
            bus=mock_bus,
            market_windows=mock_mw,
            time_authority=mock_ta,
            kill_switch=mock_ks,
            logger=logging.getLogger("test"),
        )

        # Patch time.sleep to track calls
        with patch("time.sleep") as mock_sleep:
            now = datetime(2026, 5, 15, 15, 17, 0, tzinfo=_IST)
            eod._fire(now, recovery_fire=False)

            # Assert time.sleep(2) was called exactly once
            mock_sleep.assert_called_once_with(2)

            print("  OK _fire() calls time.sleep(2) between passes")

        # Close store to release DB lock
        store.close()


def test_phantom_fill_caught_in_pass2() -> None:
    """FIX-063: Phantom fill that appears after Pass 1 is caught in Pass 2."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))

        # Mocks
        mock_adapter = MagicMock(spec=ZerodhaAdapter)
        mock_adapter.cancel_order.return_value = MagicMock(success=True)

        # Mock DB queries to simulate the phantom fill scenario
        # Pass 1: get_pending_intraday_orders returns 1 order to cancel
        # get_pending_exit_legs returns empty (no exit legs)
        # Pass 2: get_open_intraday_positions returns 1 position (the phantom fill)

        call_count = {"pending": 0, "exit_legs": 0, "open": 0}

        def mock_get_pending_intraday_orders():
            call_count["pending"] += 1
            if call_count["pending"] == 1:
                # First call (Pass 1): return 1 order to cancel
                return [{
                    "trade_id": "trade1",
                    "signal_id": "sig1",
                    "broker_order_id": "ord1",
                    "symbol": "RELIANCE",
                }]
            return []

        def mock_get_pending_exit_legs():
            return []  # No exit legs to cancel

        def mock_get_open_intraday_positions():
            call_count["open"] += 1
            # First call (Pass 2): return the phantom fill position
            if call_count["open"] == 1:
                return [{
                    "trade_id": "trade1",
                    "symbol": "RELIANCE",
                    "qty_filled": 1,
                    "side": "BUY",
                    "order_protocol": "LIMIT_TRIPLE",
                    "direction": "LONG",
                    "product": "MIS",
                    "exit_protocol": "LIMIT_THEN_MARKET",
                    "entry_broker_order_id": "entry_ord1",
                    "entry_variety": "regular",
                }]
            return []

        store.get_pending_intraday_orders = mock_get_pending_intraday_orders
        store.get_pending_exit_legs = mock_get_pending_exit_legs
        store.get_open_intraday_positions = mock_get_open_intraday_positions
        store.get_reservation_id_for_signal = MagicMock(return_value="res1")

        mock_adapter.get_positions.return_value = [
            MagicMock(symbol="RELIANCE", qty=1, product="MIS", average_price=105.0),
        ]
        mock_adapter.place_order.return_value = ("broker_exit1", "internal_exit1")

        mock_fm = MagicMock(spec=FundManager)
        mock_fm.release.return_value = True

        mock_sm = MagicMock()
        mock_sm.new_order_id = MagicMock(return_value="internal_exit1")

        mock_bus = MagicMock(spec=EventBus)
        mock_ks = MagicMock(spec=KillSwitch)
        mock_ks.is_active.return_value = False
        mock_mw = MagicMock(spec=MarketWindows)
        mock_ta = MagicMock()

        eod = EodSquareoff(
            adapter=mock_adapter,
            state_store=store,
            fund_manager=mock_fm,
            state_machine=mock_sm,
            bus=mock_bus,
            market_windows=mock_mw,
            time_authority=mock_ta,
            kill_switch=mock_ks,
            logger=logging.getLogger("test"),
            inter_order_delay_ms=0,
        )

        with patch("time.sleep") as mock_sleep:
            now = datetime(2026, 5, 15, 15, 17, 0, tzinfo=_IST)
            result = eod._fire(now, recovery_fire=False)

            # Assert: 2s sleep happened between passes
            mock_sleep.assert_called_once_with(2)

            # Assert: Pass 1 cancelled 1 order
            assert result.cancels_attempted == 1

            # Assert: Pass 2 exited 1 position (the phantom fill)
            assert result.positions_attempted == 1

            print("  OK phantom fill caught in Pass 2 after 2s sleep")

        # Close store to release DB lock
        store.close()


def test_no_fills_during_gap_normal_flow() -> None:
    """FIX-063: Clean EOD with no fills during 2s gap works normally."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))

        # Mock DB queries for clean EOD: no pending orders, one OPEN position
        store.get_pending_intraday_orders = MagicMock(return_value=[])
        store.get_pending_exit_legs = MagicMock(return_value=[])
        store.get_open_intraday_positions = MagicMock(return_value=[{
            "trade_id": "trade1",
            "symbol": "RELIANCE",
            "qty_filled": 1,
            "side": "BUY",
            "order_protocol": "LIMIT_TRIPLE",
            "direction": "LONG",
            "product": "MIS",
            "exit_protocol": "LIMIT_THEN_MARKET",
            "entry_broker_order_id": "entry_ord1",
            "entry_variety": "regular",
        }])

        mock_adapter = MagicMock(spec=ZerodhaAdapter)
        mock_adapter.get_positions.return_value = [
            MagicMock(symbol="RELIANCE", qty=1, product="MIS", average_price=105.0),
        ]
        mock_adapter.place_order.return_value = ("broker_exit1", "internal_exit1")

        mock_fm = MagicMock(spec=FundManager)
        mock_sm = MagicMock()
        mock_sm.new_order_id = MagicMock(return_value="internal_exit1")
        mock_bus = MagicMock(spec=EventBus)
        mock_ks = MagicMock(spec=KillSwitch)
        mock_ks.is_active.return_value = False
        mock_mw = MagicMock(spec=MarketWindows)
        mock_ta = MagicMock()

        eod = EodSquareoff(
            adapter=mock_adapter,
            state_store=store,
            fund_manager=mock_fm,
            state_machine=mock_sm,
            bus=mock_bus,
            market_windows=mock_mw,
            time_authority=mock_ta,
            kill_switch=mock_ks,
            logger=logging.getLogger("test"),
            inter_order_delay_ms=0,
        )

        with patch("time.sleep") as mock_sleep:
            now = datetime(2026, 5, 15, 15, 17, 0, tzinfo=_IST)
            result = eod._fire(now, recovery_fire=False)

            # Assert: 2s sleep happened
            mock_sleep.assert_called_once_with(2)

            # Assert: Pass 2 exited the existing position
            assert result.positions_attempted == 1

            print("  OK normal EOD flow with 2s sleep works")

        # Close store to release DB lock
        store.close()


if __name__ == "__main__":
    test_two_pass_with_2s_sleep()
    test_phantom_fill_caught_in_pass2()
    test_no_fills_during_gap_normal_flow()
    print("\nAll FIX-063 tests passed!")
