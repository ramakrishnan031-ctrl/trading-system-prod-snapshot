"""
tests/unit/test_fix075_price_drift.py

FIX-075: Price drift check before placement with margin top-up

Prevents broker 16388 rejection when price drifts after reservation.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from broker.cost_calculator import CostCalculator
from broker.order_monitor import OrderMonitor
from broker.product_resolver import ProductResolver
from broker.zerodha_adapter import PlacedOrder
from capital.fund_manager import FundManager, ReservationResult
from core.events import EventBus
from core.exceptions import OrderRejectedError
from core.ids import new_signal_id, new_order_id
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
        self.placed_orders.append({
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "order_id": broker_order_id,
        })
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


class _FakeEngine:
    """Entry engine that delegates to adapter."""

    def __init__(self, adapter):
        self._adapter = adapter

    def execute(self, symbol, side, qty, entry_price, sl_price, tgt_price, intent, trade_id, order_protocol):
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


class _FakeLiveFeed:
    """Live feed with configurable quote responses."""

    def __init__(self, ltp=None, success=True):
        self._ltp = ltp
        self._success = success
        self.quote_calls = []

    def quote(self, symbol):
        """Return QuoteResult-like object."""
        self.quote_calls.append(symbol)
        result = SimpleNamespace()
        result.success = self._success
        result.ltp = self._ltp
        result.error = None if self._success else "Quote fetch failed"
        return result

    def set_ltp(self, ltp):
        """Update LTP for testing drift scenarios."""
        self._ltp = ltp


class _FakeMonitor:
    """Minimal order monitor stub."""

    def track(self, internal_order_id, broker_order_id, symbol, side, qty, expected_price, placed_at, leg):
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
                      'PROCESSING', 'fp_test_drift', ?)
            """,
            (sig_id, now, now, now, now[:10]),
        )
    return sig_id


def _make_placer(tmp_path: Path, fund_manager, live_feed=None, price_drift_threshold=0.005):
    """Build OrderPlacer with FIX-075 live_feed wiring."""
    store = StateStore(tmp_path / "test.db")
    bus = EventBus()
    adapter = _FakeAdapter()
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

    # Use a real logger to capture warnings
    import logging
    logger = logging.getLogger("test_placer")
    logger.setLevel(logging.DEBUG)

    placer = OrderPlacer(
        entry_engine=full_engine,
        order_manager=om,
        fund_manager=fund_manager,
        bus=bus,
        logger=logger,
        order_monitor=monitor,
        cost_calculator=cost_calc,
        product_resolver=product_resolver,
        live_feed=live_feed,  # FIX-075: wire live_feed for drift check
        price_drift_threshold=price_drift_threshold,  # FIX-075: configurable threshold
    )
    return placer, store, adapter


class TestFix075PriceDrift:
    """FIX-075: Price drift check with margin top-up."""

    def test_drift_above_threshold_top_up_succeeds(self):
        """Price drifts 2% (above 0.5% threshold) → margin topped up, order placed at new price."""
        with TemporaryDirectory() as tmp:
            # Initial capital: ₹100k
            fm_store = StateStore(Path(tmp) / "fm.db")
            fm_bus = EventBus()
            fm = FundManager(
                state_store=fm_store,
                bus=fm_bus,
                logger=MagicMock(),
                intraday_bucket_pct=0.7,
                positional_bucket_pct=0.3,
                leverage_map={"INTRADAY": 5.0, "DELIVERY": 1.0, "COVER_ORDER": 6.0, "BRACKET_ORDER": 5.0},
                kill_switch=None,
            )
            fm.initialize(broker_balance=100000.0)

            # Original price: ₹100, drifts to ₹102 (2% drift)
            live_feed = _FakeLiveFeed(ltp=102.0, success=True)
            placer, store, adapter = _make_placer(Path(tmp), fm, live_feed=live_feed)

            try:
                sig_id = _seed_signal(store)

                # Reserve margin at original price ₹100
                res = fm.reserve(symbol="RELIANCE", qty=100, price=100.0, intent="INTRADAY", signal_id=sig_id)
                assert res.success
                original_margin = res.margin

                # Place order - should detect drift and top up
                placer.place(
                    symbol="RELIANCE",
                    side="BUY",
                    qty=100,
                    entry_price=100.0,
                    sl_price=95.0,
                    intent="INTRADAY",
                    signal_id=sig_id,
                    reservation_id=res.reservation_id,
                )

                # Order placed successfully
                assert len(adapter.placed_orders) == 1
                placed = adapter.placed_orders[0]

                # Verify margin was topped up
                snap = fm.get_snapshot()
                # Original margin: 100 * 100 / 5 = 2000
                # After top-up: 100 * 102 / 5 = 2040
                # Margin is still 'reserved' (not yet filled), so check reserved amount
                expected_reserved = 100 * 102 / 5  # 2040
                assert abs(snap.intraday_reserved - expected_reserved) < 1.0

                # Price should be updated to drifted LTP
                assert placed["price"] == 102.0
            finally:
                store.close()
                fm_store.close()
                fm_store.close()

    def test_drift_below_threshold_no_top_up(self):
        """Price drifts 0.3% (below 0.5% threshold) → no top-up, proceeds normally."""
        with TemporaryDirectory() as tmp:
            fm_store = StateStore(Path(tmp) / "fm.db")
            fm_bus = EventBus()
            fm = FundManager(
                state_store=fm_store,
                bus=fm_bus,
                logger=MagicMock(),
                intraday_bucket_pct=0.7,
                positional_bucket_pct=0.3,
                leverage_map={"INTRADAY": 5.0, "DELIVERY": 1.0, "COVER_ORDER": 6.0, "BRACKET_ORDER": 5.0},
                kill_switch=None,
            )
            fm.initialize(broker_balance=100000.0)

            # Original price: ₹100, drifts to ₹100.30 (0.3% drift)
            live_feed = _FakeLiveFeed(ltp=100.30, success=True)
            placer, store, adapter = _make_placer(Path(tmp), fm, live_feed=live_feed)

            try:
                sig_id = _seed_signal(store)

                res = fm.reserve(symbol="RELIANCE", qty=100, price=100.0, intent="INTRADAY", signal_id=sig_id)
                assert res.success
                original_reserved = fm.get_snapshot().intraday_reserved

                placer.place(
                    symbol="RELIANCE",
                    side="BUY",
                    qty=100,
                    entry_price=100.0,
                    sl_price=95.0,
                    intent="INTRADAY",
                    signal_id=sig_id,
                    reservation_id=res.reservation_id,
                )

                # Order placed, no top-up (reserved unchanged)
                assert len(adapter.placed_orders) == 1
                # Should still use original price (drift too small)
                assert adapter.placed_orders[0]["price"] == 100.0

                # Reserved margin unchanged (no top-up)
                assert fm.get_snapshot().intraday_reserved == original_reserved
            finally:
                store.close()
                fm_store.close()

    def test_drift_requires_top_up_insufficient_capital(self):
        """Drift requires top-up but insufficient capital → REJECTED_PRICE_DRIFT."""
        with TemporaryDirectory() as tmp:
            # Low capital: ₹3000 (just enough for original reservation)
            fm_store = StateStore(Path(tmp) / "fm.db")
            fm_bus = EventBus()
            fm = FundManager(
                state_store=fm_store,
                bus=fm_bus,
                logger=MagicMock(),
                intraday_bucket_pct=0.7,
                positional_bucket_pct=0.3,
                leverage_map={"INTRADAY": 5.0, "DELIVERY": 1.0, "COVER_ORDER": 6.0, "BRACKET_ORDER": 5.0},
                kill_switch=None,
            )
            fm.initialize(broker_balance=3000.0)

            # Original price: ₹100, drifts to ₹110 (10% drift)
            live_feed = _FakeLiveFeed(ltp=110.0, success=True)
            placer, store, adapter = _make_placer(Path(tmp), fm, live_feed=live_feed)

            try:
                sig_id = _seed_signal(store)

                # Reserve at ₹100: margin = 100*100/5 = 2000
                res = fm.reserve(symbol="RELIANCE", qty=100, price=100.0, intent="INTRADAY", signal_id=sig_id)
                assert res.success

                # Attempt placement - should reject due to insufficient capital for top-up
                with pytest.raises(OrderRejectedError, match="price drift.*insufficient capital"):
                    placer.place(
                        symbol="RELIANCE",
                        side="BUY",
                        qty=100,
                        entry_price=100.0,
                        sl_price=95.0,
                        intent="INTRADAY",
                        signal_id=sig_id,
                        reservation_id=res.reservation_id,
                    )

                # No order placed
                assert len(adapter.placed_orders) == 0

                # Reservation should be released (cleanup on failure)
                assert fm.get_snapshot().intraday_reserved == 0.0
            finally:
                store.close()
                fm_store.close()

    def test_quote_fn_fails_proceeds_with_original(self):
        """quote_fn fails → WARNING logged, order proceeds with original price."""
        with TemporaryDirectory() as tmp:
            fm_store = StateStore(Path(tmp) / "fm.db")
            fm_bus = EventBus()
            fm = FundManager(
                state_store=fm_store,
                bus=fm_bus,
                logger=MagicMock(),
                intraday_bucket_pct=0.7,
                positional_bucket_pct=0.3,
                leverage_map={"INTRADAY": 5.0, "DELIVERY": 1.0, "COVER_ORDER": 6.0, "BRACKET_ORDER": 5.0},
                kill_switch=None,
            )
            fm.initialize(broker_balance=100000.0)

            # Quote fails (success=False)
            live_feed = _FakeLiveFeed(ltp=None, success=False)
            placer, store, adapter = _make_placer(Path(tmp), fm, live_feed=live_feed)

            try:
                sig_id = _seed_signal(store)

                res = fm.reserve(symbol="RELIANCE", qty=100, price=100.0, intent="INTRADAY", signal_id=sig_id)
                assert res.success

                # Place order - should succeed despite quote failure
                placer.place(
                    symbol="RELIANCE",
                    side="BUY",
                    qty=100,
                    entry_price=100.0,
                    sl_price=95.0,
                    intent="INTRADAY",
                    signal_id=sig_id,
                    reservation_id=res.reservation_id,
                )

                # Order placed with original price (quote failed)
                assert len(adapter.placed_orders) == 1
                assert adapter.placed_orders[0]["price"] == 100.0
            finally:
                store.close()
                fm_store.close()

    def test_configurable_threshold(self):
        """Drift threshold is configurable - changing config changes behavior."""
        with TemporaryDirectory() as tmp:
            fm_store = StateStore(Path(tmp) / "fm.db")
            fm_bus = EventBus()
            fm = FundManager(
                state_store=fm_store,
                bus=fm_bus,
                logger=MagicMock(),
                intraday_bucket_pct=0.7,
                positional_bucket_pct=0.3,
                leverage_map={"INTRADAY": 5.0, "DELIVERY": 1.0, "COVER_ORDER": 6.0, "BRACKET_ORDER": 5.0},
                kill_switch=None,
            )
            fm.initialize(broker_balance=100000.0)

            # Custom threshold: 0.01 (1%)
            # Price drifts 0.8% (below 1% threshold)
            live_feed = _FakeLiveFeed(ltp=100.80, success=True)
            placer, store, adapter = _make_placer(Path(tmp), fm, live_feed=live_feed, price_drift_threshold=0.01)

            try:
                sig_id = _seed_signal(store)

                res = fm.reserve(symbol="RELIANCE", qty=100, price=100.0, intent="INTRADAY", signal_id=sig_id)
                assert res.success
                original_reserved = fm.get_snapshot().intraday_reserved

                placer.place(
                    symbol="RELIANCE",
                    side="BUY",
                    qty=100,
                    entry_price=100.0,
                    sl_price=95.0,
                    intent="INTRADAY",
                    signal_id=sig_id,
                    reservation_id=res.reservation_id,
                )

                # No top-up (0.8% < 1%)
                assert fm.get_snapshot().intraday_reserved == original_reserved
                assert len(adapter.placed_orders) == 1
            finally:
                store.close()
                fm_store.close()

    def test_no_live_feed_skips_drift_check(self):
        """If live_feed=None, drift check is skipped (backward compat)."""
        with TemporaryDirectory() as tmp:
            fm_store = StateStore(Path(tmp) / "fm.db")
            fm_bus = EventBus()
            fm = FundManager(
                state_store=fm_store,
                bus=fm_bus,
                logger=MagicMock(),
                intraday_bucket_pct=0.7,
                positional_bucket_pct=0.3,
                leverage_map={"INTRADAY": 5.0, "DELIVERY": 1.0, "COVER_ORDER": 6.0, "BRACKET_ORDER": 5.0},
                kill_switch=None,
            )
            fm.initialize(broker_balance=100000.0)

            # No live_feed provided
            placer, store, adapter = _make_placer(Path(tmp), fm, live_feed=None)

            try:
                sig_id = _seed_signal(store)

                res = fm.reserve(symbol="RELIANCE", qty=100, price=100.0, intent="INTRADAY", signal_id=sig_id)
                assert res.success

                # Place order - should proceed without drift check
                placer.place(
                    symbol="RELIANCE",
                    side="BUY",
                    qty=100,
                    entry_price=100.0,
                    sl_price=95.0,
                    intent="INTRADAY",
                    signal_id=sig_id,
                    reservation_id=res.reservation_id,
                )

                # Order placed normally
                assert len(adapter.placed_orders) == 1
            finally:
                store.close()
                fm_store.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
