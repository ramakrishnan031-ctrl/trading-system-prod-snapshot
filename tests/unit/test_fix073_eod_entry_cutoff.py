"""
tests/unit/test_fix073_eod_entry_cutoff.py

FIX-073: EOD entry cutoff check before adapter.place_order()

Prevents signals delayed in rate limiter from opening positions after
EOD squareoff time (broker RMS penalty risk).
"""
from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from broker.cost_calculator import CostCalculator
from broker.order_monitor import OrderMonitor
from broker.product_resolver import ProductResolver
from broker.zerodha_adapter import PlacedOrder
from capital.fund_manager import FundManager
from core.events import EventBus
from core.exceptions import OrderRejectedError
from core.ids import new_signal_id, new_order_id
from core.market_windows import MarketWindows
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.entry_engine import EntryResult
from orders.full_entry_engine import FullEntryEngine
from orders.order_manager import OrderManager
from orders.order_placer import OrderPlacer


IST = ZoneInfo("Asia/Kolkata")


class _FakeAdapter:
    """Minimal adapter that records place_order calls."""

    def __init__(self):
        self.placed_orders = []
        self.cancelled = []

    def place_order(
        self,
        symbol,
        side,
        qty,
        price=None,
        trigger_price=None,
        order_type="MARKET",
        product="MIS",
        variety="regular",
    ):
        broker_order_id = f"fake_{len(self.placed_orders)}"
        internal_order_id = new_order_id()
        self.placed_orders.append(
            {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "order_id": broker_order_id,
            }
        )
        return PlacedOrder(
            internal_order_id=internal_order_id,
            broker_order_id=broker_order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price or 100.0,
            order_type=order_type,
            product=product,
            status="SUBMITTED",
            ts=datetime.now(tz=IST),
        )

    def cancel_order(self, broker_order_id, order_type="regular"):
        self.cancelled.append(broker_order_id)
        return {"success": True}


class _FakeEngine:
    """Entry engine that delegates to adapter."""

    def __init__(self, adapter):
        self._adapter = adapter

    def execute(self, symbol, side, qty, entry_price, sl_price, tgt_price, intent, trade_id, order_protocol, entry_order_type="LIMIT"):
        placed = self._adapter.place_order(
            symbol=symbol,
            side=side,
            qty=qty,
            price=entry_price,
            product="MIS",
        )
        return EntryResult(
            success=True,
            entry_broker_order_id=placed.broker_order_id,
            sl_broker_order_id=f"sl_{placed.broker_order_id}",
            tgt_broker_order_id=f"tgt_{placed.broker_order_id}",
            entry_internal_id=f"int_entry_{trade_id}",
            sl_internal_id=f"int_sl_{trade_id}",
            tgt_internal_id=f"int_tgt_{trade_id}",
            order_protocol=order_protocol or "LIMIT_TRIPLE",
        )


class _FakeFundManager:
    """Minimal fund manager that tracks reservations."""

    def __init__(self, initial_capital=100000.0):
        self.reserved = {}
        self.released = set()
        self.initial = initial_capital

    def reserve(self, amount, intent, notes=""):
        res_id = f"res_{len(self.reserved)}"
        self.reserved[res_id] = amount
        return res_id

    def release(self, reservation_id, notes=""):
        self.released.add(reservation_id)
        if reservation_id in self.reserved:
            del self.reserved[reservation_id]

    def required_margin(self, qty, price, intent):
        # Assume 20% margin for simplicity
        return qty * price * 0.2


class _FakeMonitor:
    """Minimal order monitor stub."""

    def track(self, internal_order_id, broker_order_id, symbol, side, qty, expected_price, placed_at, leg, **kwargs):
        pass


def _seed_signal(store: StateStore) -> str:
    """Create a minimal signal row for linking."""
    sig_id = new_signal_id()
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals (
                signal_id, symbol, scanner, strategy,
                triggered_at, received_at, expires_at,
                status, fingerprint, fingerprint_date
            ) VALUES (?, 'RELIANCE', 'test_scanner', 'test_strategy',
                      ?, ?, ?,
                      'PROCESSING', 'fp_test_eod', ?)
            """,
            (sig_id, now, now, now, now[:10]),
        )
    return sig_id


def _make_placer(
    tmp_path: Path,
    market_windows: MarketWindows,
    adapter=None,
):
    """Build OrderPlacer with FIX-073 market_windows wiring."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    fm = _FakeFundManager()
    adapter = adapter or _FakeAdapter()
    engine = _FakeEngine(adapter)
    full_engine = FullEntryEngine(
        limit_protocol=MagicMock(),
        co_protocol=MagicMock(),
        logger=MagicMock(),
        default_protocol="LIMIT_TRIPLE",
    )
    full_engine.execute = engine.execute  # Bypass protocol routing
    om = OrderManager(store, MagicMock(), bus)
    cost_calc = CostCalculator(MagicMock())
    monitor = _FakeMonitor()
    product_resolver = ProductResolver({"zerodha": {"INTRADAY": "MIS"}})

    placer = OrderPlacer(
        entry_engine=full_engine,
        order_manager=om,
        fund_manager=fm,
        bus=bus,
        logger=MagicMock(),
        order_monitor=monitor,
        cost_calculator=cost_calc,
        product_resolver=product_resolver,
        market_windows=market_windows,  # FIX-073
    )
    return placer, store, fm, adapter


class TestFix073EodEntryCutoff:
    """FIX-073: EOD entry cutoff prevents late orders."""

    def test_order_rejected_past_eod_cutoff_15_16(self):
        """Order at 15:16 (past 15:15 cutoff) -> rejected, capital released."""
        fake_time = datetime(2026, 5, 18, 15, 16, 0, tzinfo=IST)
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            mw = MarketWindows(eod_entry_cutoff=time(15, 15))
            placer, store, fm, adapter = _make_placer(Path(tmp), mw)
            sig_id = _seed_signal(store)
            res_id = fm.reserve(5000.0, "INTRADAY")

            with patch('core.time_authority.now_ist', return_value=fake_time):
                with patch('orders.order_placer.now_ist', return_value=fake_time):
                    with pytest.raises(OrderRejectedError, match="past EOD entry cutoff"):
                        placer.place(
                            symbol="RELIANCE",
                            side="BUY",
                            qty=10,
                            entry_price=2500.0,
                            sl_price=2450.0,
                            intent="INTRADAY",
                            signal_id=sig_id,
                            reservation_id=res_id,
                        )

                    # Capital must be released
                    assert res_id in fm.released
                    # No broker order placed
                    assert len(adapter.placed_orders) == 0
            store.close()

    def test_order_proceeds_before_eod_cutoff_15_14(self):
        """Order at 15:14 (before 15:15 cutoff) -> proceeds normally."""
        fake_time = datetime(2026, 5, 18, 15, 14, 0, tzinfo=IST)
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            mw = MarketWindows(eod_entry_cutoff=time(15, 15))
            placer, store, fm, adapter = _make_placer(Path(tmp), mw)

            try:
                with patch('core.time_authority.now_ist', return_value=fake_time):
                    with patch('orders.order_placer.now_ist', return_value=fake_time):
                        sig_id = _seed_signal(store)
                        res_id = fm.reserve(5000.0, "INTRADAY")

                        placer.place(
                            symbol="RELIANCE",
                            side="BUY",
                            qty=10,
                            entry_price=2500.0,
                            sl_price=2450.0,
                            intent="INTRADAY",
                            signal_id=sig_id,
                            reservation_id=res_id,
                        )

                        # Order placed successfully
                        assert len(adapter.placed_orders) == 1
                        assert adapter.placed_orders[0]["symbol"] == "RELIANCE"
                        # Capital NOT released
                        assert res_id not in fm.released
            finally:
                store.close()

    def test_order_rejected_exactly_at_cutoff_15_15_00(self):
        """Order at 15:15:00 exactly (boundary inclusive) -> rejected."""
        fake_time = datetime(2026, 5, 18, 15, 15, 0, tzinfo=IST)
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            mw = MarketWindows(eod_entry_cutoff=time(15, 15))
            placer, store, fm, adapter = _make_placer(Path(tmp), mw)
            sig_id = _seed_signal(store)
            res_id = fm.reserve(5000.0, "INTRADAY")

            with patch('core.time_authority.now_ist', return_value=fake_time):
                with patch('orders.order_placer.now_ist', return_value=fake_time):
                    with pytest.raises(OrderRejectedError, match="past EOD entry cutoff"):
                        placer.place(
                            symbol="RELIANCE",
                            side="BUY",
                            qty=10,
                            entry_price=2500.0,
                            sl_price=2450.0,
                            intent="INTRADAY",
                            signal_id=sig_id,
                            reservation_id=res_id,
                        )

                    assert res_id in fm.released
                    assert len(adapter.placed_orders) == 0
            store.close()

    def test_configurable_cutoff_time(self):
        """Changing eod_entry_cutoff changes behavior."""
        fake_time = datetime(2026, 5, 18, 15, 12, 0, tzinfo=IST)
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            # Test with 15:10 cutoff
            mw_early = MarketWindows(eod_entry_cutoff=time(15, 10))
            placer, store, fm, adapter = _make_placer(Path(tmp), mw_early)
            sig_id = _seed_signal(store)
            res_id = fm.reserve(5000.0, "INTRADAY")

            with patch('core.time_authority.now_ist', return_value=fake_time):
                with patch('orders.order_placer.now_ist', return_value=fake_time):
                    # At 15:12 with 15:10 cutoff -> rejected
                    with pytest.raises(OrderRejectedError, match="past EOD entry cutoff"):
                        placer.place(
                            symbol="RELIANCE",
                            side="BUY",
                            qty=10,
                            entry_price=2500.0,
                            sl_price=2450.0,
                            intent="INTRADAY",
                            signal_id=sig_id,
                            reservation_id=res_id,
                        )

                    assert res_id in fm.released
                    assert len(adapter.placed_orders) == 0
            store.close()

    def test_no_market_windows_skips_check(self):
        """If market_windows=None, no EOD check (backward compat)."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            # Create placer without market_windows
            store = StateStore(Path(tmp) / "test.db")
            bus = EventBus()
            fm = _FakeFundManager()
            adapter = _FakeAdapter()
            engine = _FakeEngine(adapter)
            full_engine = FullEntryEngine(
                limit_protocol=MagicMock(),
                co_protocol=MagicMock(),
                logger=MagicMock(),
                default_protocol="LIMIT_TRIPLE",
            )
            full_engine.execute = engine.execute
            om = OrderManager(store, MagicMock(), bus)

            placer = OrderPlacer(
                entry_engine=full_engine,
                order_manager=om,
                fund_manager=fm,
                bus=bus,
                logger=MagicMock(),
                order_monitor=_FakeMonitor(),
                cost_calculator=CostCalculator(MagicMock()),
                product_resolver=ProductResolver({"zerodha": {"INTRADAY": "MIS"}}),
                market_windows=None,  # No EOD check
            )

            fake_time = datetime(2026, 5, 18, 15, 20, 0, tzinfo=IST)
            with patch('core.time_authority.now_ist', return_value=fake_time):
                with patch('orders.order_placer.now_ist', return_value=fake_time):
                    sig_id = _seed_signal(store)
                    res_id = fm.reserve(5000.0, "INTRADAY")

                    # Even at 15:20, order proceeds (no check)
                    placer.place(
                        symbol="RELIANCE",
                        side="BUY",
                        qty=10,
                        entry_price=2500.0,
                        sl_price=2450.0,
                        intent="INTRADAY",
                        signal_id=sig_id,
                        reservation_id=res_id,
                    )

                    assert len(adapter.placed_orders) == 1
            store.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
