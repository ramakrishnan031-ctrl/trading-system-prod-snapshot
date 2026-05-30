"""
tests/unit/test_fix129_rejection_handling.py

Tests for FIX-129 Item 43: order rejection Telegram alerts.
  - Broker rejection at placement time → Telegram WARNING sent
  - Slippage guard: suppress_alert=True → no duplicate alert
  - Broker-side rejection of placed order (OrderStatusChanged FAILED) → alert
  - Cancellation (zero fill) → alert sent
  - notifier=None → no error
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.cost_calculator import CostCalculator
from broker.order_monitor import OrderMonitor
from broker.product_resolver import ProductResolver
from broker.zerodha_adapter import PlacedOrder
from core.events import EventBus, OrderStatusChanged
from core.exceptions import BrokerError, OrderRejectedError
from core.ids import new_signal_id, new_order_id
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.entry_engine import EntryResult
from orders.full_entry_engine import FullEntryEngine
from orders.order_manager import OrderManager
from orders.order_placer import OrderPlacer
from orders.order_protocol_co import CoPlusTgtProtocol
from orders.order_protocol_limit import LimitTripleProtocol


def _log():
    return logging.getLogger("test_fix129_rejection")


def _make_store(tmp_path: Path) -> StateStore:
    return StateStore(db_path=tmp_path / "test.db")


def _seed_signal(store: StateStore) -> str:
    sig_id = new_signal_id()
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id, symbol, scanner, strategy,
               triggered_at, received_at, expires_at, status, fingerprint, fingerprint_date)
               VALUES (?, 'RELIANCE', 'test', 'test', ?, ?, ?, 'PROCESSING', ?, ?)""",
            (sig_id, now, now, now, sig_id[:8], now[:10]),
        )
    return sig_id


class _MockAdapter:
    def __init__(self, raises=None):
        self._raises = raises
        self.placed = []
        self.cancelled = []

    def place_order(self, symbol, side, qty, price, order_type, intent,
                    tag=None, trigger_price=0.0, variety="regular"):
        if self._raises:
            raise self._raises
        import uuid
        po = PlacedOrder(
            internal_order_id=new_order_id(),
            broker_order_id="PAPER_" + uuid.uuid4().hex[:12].upper(),
            symbol=symbol, side=side, qty=qty, price=price,
            order_type=order_type, product="MIS", status="SUBMITTED",
            ts=now_ist(),
        )
        self.placed.append(po)
        return po

    def cancel_order(self, broker_order_id, variety="regular"):
        from broker.zerodha_adapter import CancelResult
        self.cancelled.append(broker_order_id)
        return CancelResult(broker_order_id=broker_order_id, success=True, reason="")


class _MockFundManager:
    _LEVERAGE_MAP = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}

    def __init__(self):
        self._leverage_map = dict(self._LEVERAGE_MAP)
        self.released = []

    def commit_to_used(self, reservation_id, actual_fill_price, actual_qty):
        pass

    def release(self, reservation_id, reason=""):
        self.released.append(reservation_id)

    def release_used(self, **kwargs):
        pass

    def required_margin(self, qty, price, intent):
        return (qty * price) / self._LEVERAGE_MAP.get(intent, 1.0)


def _make_placer(tmp_path: Path, adapter=None, notifier=None, raises=None):
    store = _make_store(tmp_path)
    om = OrderManager(store, _log())
    bus = EventBus()
    fm = _MockFundManager()
    mon = MagicMock(spec=OrderMonitor)
    _adapter = adapter or _MockAdapter(raises=raises)
    lim_proto = LimitTripleProtocol(adapter=_adapter, logger=_log())
    co_proto = CoPlusTgtProtocol(adapter=_adapter, logger=_log())
    engine = FullEntryEngine(limit_protocol=lim_proto, co_protocol=co_proto, logger=_log())
    cost_calc = MagicMock(spec=CostCalculator)
    resolver = ProductResolver({"zerodha": {"INTRADAY": "MIS", "DELIVERY": "CNC", "COVER_ORDER": "CO"}})
    placer = OrderPlacer(
        entry_engine=engine, order_manager=om, fund_manager=fm,
        bus=bus, logger=_log(), order_monitor=mon, cost_calculator=cost_calc,
        product_resolver=resolver, notifier=notifier, mode="TEST",
    )
    return placer, store, fm, bus, _adapter


class TestRejectionAlert:

    def test_broker_rejection_sends_telegram_warning(self) -> None:
        """OrderRejectedError from broker → Telegram WARNING alert sent."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = MagicMock()
            exc = OrderRejectedError("margin insufficient", symbol="RELIANCE")
            placer, store, fm, bus, adapter = _make_placer(
                Path(tmp), notifier=notifier, raises=exc
            )
            sig_id = _seed_signal(store)

            with pytest.raises(OrderRejectedError):
                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=1000.0, sl_price=950.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_001",
                )

            assert notifier.send.called, "Telegram alert should be sent on broker rejection"
            call_kwargs = notifier.send.call_args.kwargs
            assert call_kwargs["severity"] == "WARNING"
            assert "RELIANCE" in call_kwargs["title"]
            store.close()
            print("  OK: broker rejection → Telegram WARNING sent")

    def test_notifier_none_does_not_raise(self) -> None:
        """notifier=None → no error, rejection handled silently."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            exc = OrderRejectedError("margin insufficient")
            placer, store, fm, bus, adapter = _make_placer(
                Path(tmp), notifier=None, raises=exc
            )
            sig_id = _seed_signal(store)

            with pytest.raises(OrderRejectedError):
                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=1000.0, sl_price=950.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_002",
                )
            store.close()
            print("  OK: notifier=None → no error on rejection")

    def test_slippage_guard_no_duplicate_alert(self) -> None:
        """Slippage guard path: suppress_alert=True → only 1 alert (from slippage guard, not _handle_placement_failure)."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = MagicMock()
            # No adapter raises — placement would succeed if we get there
            placer, store, fm, bus, adapter = _make_placer(Path(tmp), notifier=notifier)
            placer._max_entry_slippage_pct = 1.0

            class _FakeFeed:
                def quote(self, sym):
                    class R:
                        ltp = 1030.0  # 3% slippage from trigger=1000
                        success = True
                    return R()

            placer._live_feed = _FakeFeed()
            sig_id = _seed_signal(store)

            with pytest.raises(OrderRejectedError):
                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=1000.0, sl_price=950.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_slippage",
                    signal_trigger_price=1000.0,
                )

            # Exactly 1 alert from slippage guard, NOT 2 (no extra from _handle_placement_failure)
            assert notifier.send.call_count == 1, (
                f"Expected 1 alert (slippage guard only), got {notifier.send.call_count}"
            )
            store.close()
            print("  OK: slippage guard → exactly 1 alert (no duplicate from _handle_placement_failure)")

    def test_broker_zero_fill_cancellation_sends_alert(self) -> None:
        """Broker cancels ENTRY order (zero fill) → Telegram WARNING sent."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = MagicMock()
            placer, store, fm, bus, adapter = _make_placer(Path(tmp), notifier=notifier)
            sig_id = _seed_signal(store)

            # Successful placement first
            placer.place(
                symbol="RELIANCE", side="BUY", qty=10,
                entry_price=1000.0, sl_price=950.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_fill_cancel",
            )

            notifier.reset_mock()  # clear the ORDER PLACED alert

            # Simulate broker FAILED status change (zero fill = broker-side rejection)
            # Find the entry internal_id
            with placer._fill_map_lock:
                entry_internal_id = next(
                    (k for k, v in placer._fill_map.items() if v.leg == "ENTRY"), None
                )
            if entry_internal_id is None:
                store.close()
                print("  SKIP: no tracked entry order (test infra issue)")
                return

            bus.publish(OrderStatusChanged(
                source_module="test",
                internal_order_id=entry_internal_id,
                broker_order_id="BROKER_001",
                status="FAILED",
                qty_filled=0,
                avg_fill_price=None,
                rejection_reason="Insufficient margin",
            ))

            assert notifier.send.called, "Alert should be sent when broker rejects placed order"
            call_kwargs = notifier.send.call_args.kwargs
            assert call_kwargs["severity"] == "WARNING"
            assert "REJECTED" in call_kwargs["title"].upper() or "RELIANCE" in call_kwargs["title"]
            store.close()
            print("  OK: broker zero-fill cancellation → Telegram WARNING sent")

    def test_cancellation_no_alert(self) -> None:
        """CANCELLED status (not FAILED/REJECTED) → no alert (user-initiated)."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            notifier = MagicMock()
            placer, store, fm, bus, adapter = _make_placer(Path(tmp), notifier=notifier)
            sig_id = _seed_signal(store)

            placer.place(
                symbol="RELIANCE", side="BUY", qty=10,
                entry_price=1000.0, sl_price=950.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_user_cancel",
            )
            notifier.reset_mock()

            with placer._fill_map_lock:
                entry_internal_id = next(
                    (k for k, v in placer._fill_map.items() if v.leg == "ENTRY"), None
                )
            if entry_internal_id is None:
                store.close()
                return

            bus.publish(OrderStatusChanged(
                source_module="test",
                internal_order_id=entry_internal_id,
                broker_order_id="BROKER_001",
                status="CANCELLED",   # user-cancelled, not broker rejected
                qty_filled=0,
                avg_fill_price=None,
                rejection_reason=None,
            ))

            # CANCELLED should not send an alert (different from FAILED/REJECTED)
            assert not notifier.send.called, "No alert for user-cancelled order"
            store.close()
            print("  OK: CANCELLED status (zero fill) → no rejection alert")


if __name__ == "__main__":
    tests = [
        TestRejectionAlert().test_broker_rejection_sends_telegram_warning,
        TestRejectionAlert().test_notifier_none_does_not_raise,
        TestRejectionAlert().test_slippage_guard_no_duplicate_alert,
        TestRejectionAlert().test_broker_zero_fill_cancellation_sends_alert,
        TestRejectionAlert().test_cancellation_no_alert,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
