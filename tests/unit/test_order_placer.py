"""
tests/unit/test_order_placer.py

Tests for:
  - orders/order_manager.py  (OMgr1–OMgr9)
  - orders/entry_engine.py   (EntryResult, EntryEngine ABC)
  - orders/order_protocol_limit.py (OPL1–OPL6)
  - orders/order_protocol_co.py    (OPC1–OPC7)
  - orders/full_entry_engine.py    (FEE1–FEE5)
  - orders/order_placer.py         (OP1–OP11)

Coverage goals:
  - OrderManager CRUD: create_trade, insert_order, record_entry_fill,
    update_trade_status, link_signal_trade, get_trade, get_orders_for_trade
  - LimitTripleProtocol: happy path, entry fail, SL fail, TGT fail
  - CoPlusTgtProtocol: happy path, CO fail, TGT fail (partial success)
  - FullEntryEngine: protocol routing, unknown protocol
  - OrderPlacer: happy path, broker failure rollback, fill event handling
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, List, Optional
from unittest.mock import MagicMock, call

import pytest

from broker.cost_calculator import CostCalculator
from broker.order_monitor import OrderMonitor
from broker.product_resolver import ProductResolver
from broker.zerodha_adapter import PlacedOrder
from core.events import EventBus, OrderFilled, OrderStatusChanged
from core.exceptions import (
    BrokerAuthError,
    BrokerError,
    BrokerRateLimit429Error,
    BrokerTimeoutError,
    OrderRejectedError,
)
from core.ids import new_signal_id
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.entry_engine import EntryResult
from orders.full_entry_engine import FullEntryEngine
from orders.order_manager import OrderManager
from orders.order_placer import (
    OrderPlacer,
    _FillEntry,
    _LEG_ENTRY,
    _LEG_SL,
    _LEG_TGT,
    _LEG_EOD,
    _VALID_LEGS,
)
from orders.order_protocol_co import CoPlusTgtProtocol
from orders.order_protocol_limit import LimitTripleProtocol


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _log() -> logging.Logger:
    return logging.getLogger("test_order_placer")


def _make_store(tmp_path: Path) -> StateStore:
    db = tmp_path / "test.db"
    schema = Path("core/schema.sql")
    store = StateStore(db_path=db, schema_path=schema)
    return store


def _seed_signal(store: StateStore) -> str:
    """Insert a minimal signal row so FK constraints pass."""
    sig_id = new_signal_id()
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals (
                signal_id, symbol, scanner, strategy,
                triggered_at, received_at, expires_at,
                status, fingerprint, fingerprint_date
            ) VALUES (?, 'RELIANCE', 'gap_go_long', 'gap_go_long',
                      ?, ?, ?,
                      'PROCESSING', 'fp_test_001', ?)
            """,
            (sig_id, now, now, now, now[:10]),
        )
    return sig_id


def _placed_order(symbol="RELIANCE", side="BUY", internal_id=None, broker_id=None):
    """Create a minimal PlacedOrder for testing."""
    import uuid as _uuid
    from core.ids import new_order_id
    from datetime import datetime
    return PlacedOrder(
        internal_order_id=internal_id or new_order_id(),
        broker_order_id=broker_id or ("PAPER_" + _uuid.uuid4().hex[:12].upper()),
        symbol=symbol,
        side=side,
        qty=10,
        price=100.0,
        order_type="LIMIT",
        product="MIS",
        status="SUBMITTED",
        ts=datetime.now(),
    )


class _MockAdapter:
    """Mock ZerodhaAdapter that returns PlacedOrder or raises on demand."""

    def __init__(self, raises: Optional[Exception] = None,
                 fail_on_call: int = -1) -> None:
        self._raises = raises
        self._fail_on = fail_on_call
        self._call_count = 0
        self.placed: List[dict] = []
        self.cancelled: List[str] = []

    def place_order(self, symbol, side, qty, price, order_type, intent,
                    tag=None, trigger_price=0.0, variety="regular"):
        self._call_count += 1
        if self._raises is not None and (
            self._fail_on < 0 or self._call_count == self._fail_on
        ):
            raise self._raises
        po = _placed_order(symbol=symbol, side=side)
        self.placed.append({
            "symbol": symbol, "side": side, "qty": qty,
            "price": price, "order_type": order_type,
            "trigger_price": trigger_price, "variety": variety,
            "broker_order_id": po.broker_order_id,
            "internal_order_id": po.internal_order_id,
        })
        return po

    def cancel_order(self, broker_order_id: str):
        from broker.zerodha_adapter import CancelResult
        self.cancelled.append(broker_order_id)
        return CancelResult(broker_order_id=broker_order_id, success=True, reason="")


class _MockFundManager:
    """Minimal FundManager mock."""

    # Mirrors the default leverage map used by FundManager so required_margin
    # stays behavior-compatible when order_placer migrates off the 0.20
    # hardcode (E.2 / H-3).
    _LEVERAGE_MAP = {
        "INTRADAY": 5.0,
        "COVER_ORDER": 6.0,
        "DELIVERY": 1.0,
        "BRACKET_ORDER": 5.0,
    }

    def __init__(self) -> None:
        self.committed: List[dict] = []
        self.released: List[str] = []
        self.released_used: List[dict] = []  # BL-10a

    def commit_to_used(self, reservation_id, actual_fill_price, actual_qty):
        self.committed.append({
            "reservation_id": reservation_id,
            "actual_fill_price": actual_fill_price,
            "actual_qty": actual_qty,
        })

    def release(self, reservation_id, reason=""):
        self.released.append(reservation_id)

    def release_used(self, *, symbol, exit_price, exit_qty, intent,
                     entry_price, direction, costs=0.0):
        self.released_used.append({
            "symbol": symbol, "exit_price": exit_price, "exit_qty": exit_qty,
            "intent": intent, "entry_price": entry_price,
            "direction": direction, "costs": costs,
        })

    def required_margin(self, qty: int, price: float, intent: str) -> float:
        """H-3: mirror FundManager.required_margin signature for order_placer."""
        leverage = self._LEVERAGE_MAP.get(intent, 1.0)
        return (qty * price) / leverage


class _MockKillSwitch:
    """Minimal KillSwitch mock with configurable is_active return."""

    def __init__(self, active: bool = False) -> None:
        self._active = active

    def is_active(self, intent: str = "entry") -> bool:
        return self._active


# ─────────────────────────────────────────────────────────────────────────────
# OrderManager tests (OMgr1–OMgr9)
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderManager:

    def test_create_trade_returns_trd_id(self) -> None:
        """create_trade returns a valid trd_ prefixed ID. (OMgr2)"""
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            sig_id = _seed_signal(store)
            om = OrderManager(store, _log())
            trade_id = om.create_trade(
                signal_id=sig_id, symbol="RELIANCE", direction="LONG",
                strategy="gap_go_long", sector=None, qty=10,
                entry_target_price=2500.0, sl_initial=2450.0, tgt_initial=2600.0,
                order_protocol="LIMIT_TRIPLE", margin_reserved=5000.0,
                risk_amount=500.0,
            )
            assert trade_id.startswith("trd_"), f"Expected trd_ prefix, got {trade_id!r}"
            store.close()
            print("  OK create_trade returns trd_ ID (OMgr2)")

    def test_create_trade_inserts_pending_fill_row(self) -> None:
        """create_trade inserts row with status=PENDING_FILL. (OMgr2)"""
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            sig_id = _seed_signal(store)
            om = OrderManager(store, _log())
            trade_id = om.create_trade(
                signal_id=sig_id, symbol="INFY", direction="SHORT",
                strategy="gap_fade_short", sector="IT", qty=5,
                entry_target_price=1800.0, sl_initial=1830.0, tgt_initial=1740.0,
                order_protocol="CO_PLUS_TGT", margin_reserved=1800.0,
                risk_amount=150.0,
            )
            row = om.get_trade(trade_id)
            assert row is not None
            assert row["status"] == "PENDING_FILL"
            assert row["symbol"] == "INFY"
            assert row["direction"] == "SHORT"
            assert row["qty_planned"] == 5
            assert row["order_protocol"] == "CO_PLUS_TGT"
            store.close()
            print("  OK create_trade inserts PENDING_FILL row (OMgr2)")

    def test_insert_order_creates_order_row(self) -> None:
        """insert_order creates a row with status=PENDING. (OMgr3)"""
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            sig_id = _seed_signal(store)
            om = OrderManager(store, _log())
            trade_id = om.create_trade(
                signal_id=sig_id, symbol="SBIN", direction="LONG",
                strategy="test", sector=None, qty=20,
                entry_target_price=600.0, sl_initial=590.0, tgt_initial=620.0,
                order_protocol="LIMIT_TRIPLE", margin_reserved=2400.0,
                risk_amount=200.0,
            )
            om.insert_order(
                trade_id=trade_id, broker_order_id="BROKER123",
                leg="ENTRY", transaction_type="BUY",
                order_type="LIMIT", product="MIS", variety="regular",
                qty_requested=20, price=600.0,
            )
            orders = om.get_orders_for_trade(trade_id)
            assert len(orders) == 1
            assert orders[0]["order_id"] == "BROKER123"
            assert orders[0]["leg"] == "ENTRY"
            assert orders[0]["status"] == "PENDING"
            store.close()
            print("  OK insert_order creates PENDING order row (OMgr3)")

    def test_record_entry_fill_sets_open(self) -> None:
        """record_entry_fill sets status=OPEN and fill data. (OMgr4)"""
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            sig_id = _seed_signal(store)
            om = OrderManager(store, _log())
            trade_id = om.create_trade(
                signal_id=sig_id, symbol="HDFC", direction="LONG",
                strategy="vwap_bounce", sector=None, qty=5,
                entry_target_price=1600.0, sl_initial=1580.0, tgt_initial=1640.0,
                order_protocol="LIMIT_TRIPLE", margin_reserved=1600.0,
                risk_amount=100.0,
            )
            fill_ts = now_ist().isoformat()
            om.record_entry_fill(
                trade_id=trade_id,
                avg_fill_price=1601.50,
                qty_filled=5,
                filled_at=fill_ts,
            )
            row = om.get_trade(trade_id)
            assert row["status"] == "OPEN"
            assert row["entry_actual_price"] == pytest.approx(1601.50)
            assert row["qty_filled"] == 5
            assert row["entry_time"] == fill_ts
            store.close()
            print("  OK record_entry_fill sets OPEN + fill data (OMgr4)")

    def test_update_trade_status(self) -> None:
        """update_trade_status changes status column. (OMgr5)"""
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            sig_id = _seed_signal(store)
            om = OrderManager(store, _log())
            trade_id = om.create_trade(
                signal_id=sig_id, symbol="TCS", direction="LONG",
                strategy="test", sector=None, qty=2,
                entry_target_price=4000.0, sl_initial=3950.0, tgt_initial=4100.0,
                order_protocol="LIMIT_TRIPLE", margin_reserved=1600.0,
                risk_amount=100.0,
            )
            om.update_trade_status(trade_id, "FAILED")
            row = om.get_trade(trade_id)
            assert row["status"] == "FAILED"
            store.close()
            print("  OK update_trade_status changes status (OMgr5)")

    def test_link_signal_trade(self) -> None:
        """link_signal_trade sets signals.trade_id. (OMgr6)"""
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            sig_id = _seed_signal(store)
            om = OrderManager(store, _log())
            trade_id = om.create_trade(
                signal_id=sig_id, symbol="WIPRO", direction="LONG",
                strategy="test", sector=None, qty=3,
                entry_target_price=500.0, sl_initial=490.0, tgt_initial=520.0,
                order_protocol="LIMIT_TRIPLE", margin_reserved=300.0,
                risk_amount=30.0,
            )
            om.link_signal_trade(sig_id, trade_id)
            row = store.fetch_one(
                "SELECT trade_id FROM signals WHERE signal_id = ?", (sig_id,)
            )
            assert row is not None
            assert row["trade_id"] == trade_id
            store.close()
            print("  OK link_signal_trade sets signals.trade_id (OMgr6)")

    def test_get_trade_returns_none_for_missing(self) -> None:
        """get_trade returns None for unknown trade_id. (OMgr7)"""
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            om = OrderManager(store, _log())
            result = om.get_trade("trd_" + "0" * 32)
            assert result is None
            store.close()
            print("  OK get_trade returns None for unknown (OMgr7)")

    def test_get_orders_for_trade_multiple(self) -> None:
        """get_orders_for_trade returns all orders sorted by placed_at. (OMgr8)"""
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            sig_id = _seed_signal(store)
            om = OrderManager(store, _log())
            trade_id = om.create_trade(
                signal_id=sig_id, symbol="AXISBANK", direction="LONG",
                strategy="test", sector=None, qty=10,
                entry_target_price=1000.0, sl_initial=980.0, tgt_initial=1040.0,
                order_protocol="LIMIT_TRIPLE", margin_reserved=2000.0,
                risk_amount=200.0,
            )
            om.insert_order(
                trade_id=trade_id, broker_order_id="ENTRY001",
                leg="ENTRY", transaction_type="BUY", order_type="LIMIT",
                product="MIS", variety="regular", qty_requested=10, price=1000.0,
            )
            om.insert_order(
                trade_id=trade_id, broker_order_id="SL001",
                leg="SL", transaction_type="SELL", order_type="SL-M",
                product="MIS", variety="regular", qty_requested=10, price=0.0,
                trigger_price=980.0,
            )
            om.insert_order(
                trade_id=trade_id, broker_order_id="TGT001",
                leg="TGT", transaction_type="SELL", order_type="LIMIT",
                product="MIS", variety="regular", qty_requested=10, price=1040.0,
            )
            orders = om.get_orders_for_trade(trade_id)
            assert len(orders) == 3
            legs = [o["leg"] for o in orders]
            assert "ENTRY" in legs and "SL" in legs and "TGT" in legs
            store.close()
            print("  OK get_orders_for_trade returns all 3 orders (OMgr8)")


# ─────────────────────────────────────────────────────────────────────────────
# LimitTripleProtocol tests (OPL1–OPL6)
# ─────────────────────────────────────────────────────────────────────────────

class TestLimitTripleProtocol:

    def _make_proto(self, adapter=None):
        if adapter is None:
            adapter = _MockAdapter()
        return LimitTripleProtocol(adapter=adapter, logger=_log()), adapter

    def test_happy_path_places_three_orders(self) -> None:
        """Happy path: 3 orders placed (ENTRY LIMIT, SL SL-M, TGT LIMIT). (OPL1)"""
        proto, adapter = self._make_proto()
        result = proto.execute(
            symbol="RELIANCE", side="BUY", qty=10,
            entry_price=2500.0, sl_price=2450.0, tgt_price=2600.0,
            intent="INTRADAY", trade_id="trd_abc",
        )
        assert result.success
        assert result.order_protocol == "LIMIT_TRIPLE"
        assert result.entry_broker_order_id
        assert result.sl_broker_order_id
        assert result.tgt_broker_order_id
        assert len(adapter.placed) == 3
        # ENTRY: LIMIT BUY
        assert adapter.placed[0]["order_type"] == "LIMIT"
        assert adapter.placed[0]["side"] == "BUY"
        # SL: SL-M SELL, trigger=sl_price
        assert adapter.placed[1]["order_type"] == "SL-M"
        assert adapter.placed[1]["side"] == "SELL"
        assert adapter.placed[1]["trigger_price"] == pytest.approx(2450.0)
        # TGT: LIMIT SELL
        assert adapter.placed[2]["order_type"] == "LIMIT"
        assert adapter.placed[2]["side"] == "SELL"
        print("  OK LIMIT_TRIPLE happy path: 3 orders, correct types/sides (OPL1)")

    def test_short_trade_uses_correct_exit_sides(self) -> None:
        """SHORT trade: SL and TGT are BUY orders (closing side). (OPL2)"""
        proto, adapter = self._make_proto()
        result = proto.execute(
            symbol="NIFTY", side="SELL", qty=5,
            entry_price=22000.0, sl_price=22200.0, tgt_price=21600.0,
            intent="INTRADAY", trade_id="trd_short",
        )
        assert result.success
        assert adapter.placed[0]["side"] == "SELL"  # entry
        assert adapter.placed[1]["side"] == "BUY"   # SL
        assert adapter.placed[2]["side"] == "BUY"   # TGT
        print("  OK LIMIT_TRIPLE short trade: exit orders are BUY (OPL2)")

    def test_entry_failure_raises_no_sl_placed(self) -> None:
        """Entry failure -> BrokerError raised; SL/TGT not placed. (OPL3)"""
        adapter = _MockAdapter(raises=OrderRejectedError("rejected"))
        proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        with pytest.raises(BrokerError):
            proto.execute(
                symbol="FAIL", side="BUY", qty=1,
                entry_price=100.0, sl_price=95.0, tgt_price=110.0,
                intent="INTRADAY", trade_id="trd_fail",
            )
        assert len(adapter.placed) == 0
        print("  OK entry failure -> BrokerError, 0 orders placed (OPL3)")

    def test_sl_failure_cancels_entry(self) -> None:
        """SL failure -> entry is cancelled best-effort; BrokerError raised. (OPL3)"""
        # First call (ENTRY) succeeds; second call (SL) fails
        adapter = _MockAdapter(raises=OrderRejectedError("sl rejected"),
                               fail_on_call=2)
        proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        with pytest.raises(BrokerError):
            proto.execute(
                symbol="SBIN", side="BUY", qty=5,
                entry_price=600.0, sl_price=585.0, tgt_price=630.0,
                intent="INTRADAY", trade_id="trd_sl_fail",
            )
        assert len(adapter.placed) == 1   # only ENTRY was placed
        assert len(adapter.cancelled) == 1  # ENTRY was cancelled
        print("  OK SL failure -> entry cancelled (OPL3)")

    def test_tgt_failure_raises_sl_still_standing(self) -> None:
        """TGT failure -> raises BrokerError; SL still standing. (OPL3)"""
        # Third call (TGT) fails
        adapter = _MockAdapter(raises=OrderRejectedError("tgt rejected"),
                               fail_on_call=3)
        proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        with pytest.raises(BrokerError):
            proto.execute(
                symbol="HDFC", side="BUY", qty=3,
                entry_price=1600.0, sl_price=1580.0, tgt_price=1640.0,
                intent="INTRADAY", trade_id="trd_tgt_fail",
            )
        assert len(adapter.placed) == 2   # ENTRY + SL placed, TGT failed
        assert len(adapter.cancelled) == 0  # SL stands
        print("  OK TGT failure -> SL still standing (OPL3)")


# ─────────────────────────────────────────────────────────────────────────────
# CoPlusTgtProtocol tests (OPC1–OPC7)
# ─────────────────────────────────────────────────────────────────────────────

class TestCoPlusTgtProtocol:

    def _make_proto(self, adapter=None):
        if adapter is None:
            adapter = _MockAdapter()
        return CoPlusTgtProtocol(adapter=adapter, logger=_log()), adapter

    def test_happy_path_places_co_and_tgt(self) -> None:
        """CO_PLUS_TGT: places CO order + LIMIT TGT; sl_broker_order_id is empty. (OPC1)"""
        proto, adapter = self._make_proto()
        result = proto.execute(
            symbol="RELIANCE", side="BUY", qty=10,
            entry_price=2500.0, sl_price=2450.0, tgt_price=2600.0,
            intent="INTRADAY", trade_id="trd_co",
        )
        assert result.success
        assert result.order_protocol == "CO_PLUS_TGT"
        assert result.entry_broker_order_id
        assert result.sl_broker_order_id == ""      # OPC5: SL inside CO bracket
        assert result.tgt_broker_order_id
        assert len(adapter.placed) == 2
        # CO order: variety=co, order_type=SL, trigger_price=sl_price
        assert adapter.placed[0]["variety"] == "co"
        assert adapter.placed[0]["order_type"] == "SL"
        assert adapter.placed[0]["trigger_price"] == pytest.approx(2450.0)
        # TGT: variety=regular, order_type=LIMIT
        assert adapter.placed[1]["variety"] == "regular"
        assert adapter.placed[1]["order_type"] == "LIMIT"
        print("  OK CO_PLUS_TGT happy path: CO + TGT placed (OPC1, OPC2, OPC5)")

    def test_co_failure_raises(self) -> None:
        """CO failure -> BrokerError raised; TGT not placed. (OPC4)"""
        adapter = _MockAdapter(raises=OrderRejectedError("co blocked"))
        proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
        with pytest.raises(BrokerError):
            proto.execute(
                symbol="FAIL", side="BUY", qty=1,
                entry_price=100.0, sl_price=95.0, tgt_price=110.0,
                intent="INTRADAY", trade_id="trd_co_fail",
            )
        assert len(adapter.placed) == 0
        print("  OK CO failure -> BrokerError, nothing placed (OPC4)")

    def test_tgt_failure_returns_failure(self) -> None:
        """MED #13: TGT failure after CO placed -> success=False (contract honesty).
        CO broker_order_id is preserved in entry_broker_order_id for reconciliation."""
        adapter = _MockAdapter(raises=BrokerAuthError("auth"), fail_on_call=2)
        proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
        result = proto.execute(
            symbol="SBIN", side="BUY", qty=5,
            entry_price=600.0, sl_price=590.0, tgt_price=620.0,
            intent="INTRADAY", trade_id="trd_co_partial",
        )
        assert not result.success, "MED #13: TGT leg failure must return success=False"
        assert result.entry_broker_order_id, "CO broker_order_id preserved for reconciliation"
        assert result.tgt_broker_order_id == ""
        assert result.rejection_reason, "rejection_reason names which leg failed"
        assert "TGT" in result.rejection_reason
        print("  OK CO TGT failure -> success=False; CO broker_order_id preserved (MED #13)")

    def test_short_co_trade_correct_sides(self) -> None:
        """SHORT CO trade: CO SELL + TGT BUY. (OPC2, OPC3)"""
        proto, adapter = self._make_proto()
        result = proto.execute(
            symbol="NIFTY", side="SELL", qty=5,
            entry_price=22000.0, sl_price=22200.0, tgt_price=21600.0,
            intent="INTRADAY", trade_id="trd_co_short",
        )
        assert result.success
        assert adapter.placed[0]["side"] == "SELL"   # CO entry
        assert adapter.placed[1]["side"] == "BUY"    # TGT exit
        print("  OK CO short trade: SELL CO + BUY TGT (OPC2, OPC3)")


# ─────────────────────────────────────────────────────────────────────────────
# FullEntryEngine tests (FEE1–FEE5)
# ─────────────────────────────────────────────────────────────────────────────

class TestFullEntryEngine:

    def _make_engine(self, default="LIMIT_TRIPLE"):
        adapter = _MockAdapter()
        co_proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
        limit_proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        engine = FullEntryEngine(
            co_protocol=co_proto, limit_protocol=limit_proto,
            logger=_log(), default_protocol=default,
        )
        return engine, adapter

    def test_routes_limit_triple(self) -> None:
        """order_protocol='LIMIT_TRIPLE' routes to LimitTripleProtocol. (FEE2)"""
        engine, adapter = self._make_engine()
        result = engine.execute(
            symbol="RELIANCE", side="BUY", qty=5,
            entry_price=2500.0, sl_price=2450.0, tgt_price=2600.0,
            intent="INTRADAY", trade_id="trd_fee_limit",
            order_protocol="LIMIT_TRIPLE",
        )
        assert result.success
        assert result.order_protocol == "LIMIT_TRIPLE"
        assert len(adapter.placed) == 3
        print("  OK routes LIMIT_TRIPLE -> 3 orders (FEE2)")

    def test_routes_co_plus_tgt(self) -> None:
        """order_protocol='CO_PLUS_TGT' routes to CoPlusTgtProtocol. (FEE2)"""
        engine, adapter = self._make_engine()
        result = engine.execute(
            symbol="INFY", side="BUY", qty=5,
            entry_price=1800.0, sl_price=1770.0, tgt_price=1860.0,
            intent="INTRADAY", trade_id="trd_fee_co",
            order_protocol="CO_PLUS_TGT",
        )
        assert result.success
        assert result.order_protocol == "CO_PLUS_TGT"
        assert len(adapter.placed) == 2
        print("  OK routes CO_PLUS_TGT -> 2 orders (FEE2)")

    def test_unknown_protocol_raises(self) -> None:
        """Unknown protocol -> ValueError. (FEE2)"""
        engine, _ = self._make_engine()
        with pytest.raises(ValueError, match="Unknown order_protocol"):
            engine.execute(
                symbol="TCS", side="BUY", qty=1,
                entry_price=4000.0, sl_price=3950.0, tgt_price=4100.0,
                intent="INTRADAY", trade_id="trd_bad",
                order_protocol="BRACKET_MAGIC",
            )
        print("  OK unknown protocol -> ValueError (FEE2)")

    def test_default_protocol_used_when_not_provided(self) -> None:
        """Empty order_protocol uses default_protocol. (FEE2)"""
        engine, adapter = self._make_engine(default="LIMIT_TRIPLE")
        result = engine.execute(
            symbol="WIPRO", side="BUY", qty=3,
            entry_price=500.0, sl_price=490.0, tgt_price=520.0,
            intent="INTRADAY", trade_id="trd_default",
            order_protocol="",   # uses default
        )
        assert result.order_protocol == "LIMIT_TRIPLE"
        assert len(adapter.placed) == 3
        print("  OK empty protocol uses default (FEE2)")


# ─────────────────────────────────────────────────────────────────────────────
# OrderPlacer tests (OP1–OP11)
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderPlacer:

    def _make_placer(self, tmp_path, adapter=None, default_protocol="LIMIT_TRIPLE"):
        store = _make_store(tmp_path)
        if adapter is None:
            adapter = _MockAdapter()
        co_proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
        limit_proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        engine = FullEntryEngine(
            co_protocol=co_proto, limit_protocol=limit_proto,
            logger=_log(), default_protocol=default_protocol,
        )
        om = OrderManager(store, _log())
        fm = _MockFundManager()
        bus = EventBus()
        placer = OrderPlacer(
            entry_engine=engine,
            order_manager=om,
            fund_manager=fm,
            bus=bus,
            logger=_log(),
            order_monitor=MagicMock(spec=OrderMonitor),  # BL-7b
            cost_calculator=MagicMock(spec=CostCalculator),  # BL-10a
            rr_ratio=2.0,
            default_order_protocol=default_protocol,
        )
        return placer, store, fm, bus, adapter, om

    def test_happy_path_creates_trade_and_places_orders(self) -> None:
        """Happy path: trade row created, 3 orders placed (LIMIT_TRIPLE). (OP1-OP4)"""
        with TemporaryDirectory() as tmp:
            placer, store, fm, bus, adapter, om = self._make_placer(Path(tmp))
            sig_id = _seed_signal(store)

            placer.place(
                symbol="RELIANCE", side="BUY", qty=10,
                entry_price=2500.0, sl_price=2450.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_abc123",
            )

            # 3 broker orders placed
            assert len(adapter.placed) == 3

            # Trade row in DB
            rows = store.fetch_all("SELECT * FROM trades WHERE signal_id = ?", (sig_id,))
            assert len(rows) == 1
            trade = dict(rows[0])
            assert trade["status"] == "PENDING_FILL"
            assert trade["symbol"] == "RELIANCE"
            assert trade["direction"] == "LONG"
            assert trade["order_protocol"] == "LIMIT_TRIPLE"
            store.close()
            print("  OK happy path: trade + 3 orders placed (OP1-OP4)")

    def test_tgt_computed_from_rr_ratio(self) -> None:
        """tgt_price = entry + (entry - sl) * rr_ratio for LONG. (OP3)"""
        with TemporaryDirectory() as tmp:
            placer, store, fm, bus, adapter, om = self._make_placer(Path(tmp))
            sig_id = _seed_signal(store)

            placer.place(
                symbol="TCS", side="BUY", qty=5,
                entry_price=4000.0, sl_price=3950.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_tgt",
            )
            # rr=2.0: tgt = 4000 + (4000-3950)*2 = 4000 + 100 = 4100
            row = store.fetch_one("SELECT tgt_initial FROM trades WHERE signal_id = ?", (sig_id,))
            assert row["tgt_initial"] == pytest.approx(4100.0)
            store.close()
            print("  OK tgt_price = entry + risk * rr_ratio (OP3)")

    def test_short_tgt_computed_correctly(self) -> None:
        """tgt_price = entry - (sl - entry) * rr for SHORT. (OP3)"""
        with TemporaryDirectory() as tmp:
            placer, store, fm, bus, adapter, om = self._make_placer(Path(tmp))
            sig_id = _seed_signal(store)

            placer.place(
                symbol="NIFTY", side="SELL", qty=5,
                entry_price=22000.0, sl_price=22200.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_short_tgt",
            )
            # rr=2.0: tgt = 22000 - (22200-22000)*2 = 22000 - 400 = 21600
            row = store.fetch_one("SELECT tgt_initial FROM trades WHERE signal_id = ?", (sig_id,))
            assert row["tgt_initial"] == pytest.approx(21600.0)
            store.close()
            print("  OK SHORT tgt_price = entry - risk * rr (OP3)")

    def test_caller_supplied_tgt_price_overrides_internal(self) -> None:
        """Caller-supplied tgt_price overrides OP3 internal computation. (SPW6)"""
        with TemporaryDirectory() as tmp:
            placer, store, fm, bus, adapter, om = self._make_placer(Path(tmp))
            sig_id = _seed_signal(store)

            # Internal would compute: 2500 + (2500-2450)*2 = 2600
            # We supply 2700 explicitly
            placer.place(
                symbol="RELIANCE", side="BUY", qty=10,
                entry_price=2500.0, sl_price=2450.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_spw6",
                tgt_price=2700.0,
            )
            row = store.fetch_one("SELECT tgt_initial FROM trades WHERE signal_id = ?", (sig_id,))
            assert row["tgt_initial"] == pytest.approx(2700.0), \
                f"Expected 2700.0, got {row['tgt_initial']}"
            store.close()
            print("  OK caller-supplied tgt_price=2700 stored (SPW6)")

    def test_none_tgt_price_uses_internal_computation(self) -> None:
        """tgt_price=None (default) -> OP3 internal computation used. (SPW6)"""
        with TemporaryDirectory() as tmp:
            placer, store, fm, bus, adapter, om = self._make_placer(Path(tmp))
            sig_id = _seed_signal(store)

            # rr=2.0: 4000 + (4000-3980)*2 = 4000 + 40 = 4040
            placer.place(
                symbol="TCS", side="BUY", qty=5,
                entry_price=4000.0, sl_price=3980.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_spw6_none",
                tgt_price=None,
            )
            row = store.fetch_one("SELECT tgt_initial FROM trades WHERE signal_id = ?", (sig_id,))
            assert row["tgt_initial"] == pytest.approx(4040.0), \
                f"Expected 4040.0, got {row['tgt_initial']}"
            store.close()
            print("  OK tgt_price=None -> internal OP3 computation used (SPW6)")

    def test_broker_failure_sets_trade_failed_releases_capital(self) -> None:
        """Broker failure -> trade=FAILED, reservation released. (OP7)"""
        with TemporaryDirectory() as tmp:
            adapter = _MockAdapter(raises=OrderRejectedError("rejected"))
            placer, store, fm, bus, _, om = self._make_placer(Path(tmp), adapter=adapter)
            sig_id = _seed_signal(store)

            with pytest.raises(BrokerError):
                placer.place(
                    symbol="FAIL", side="BUY", qty=1,
                    entry_price=100.0, sl_price=95.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_fail",
                )

            # Trade row must exist and be FAILED
            rows = store.fetch_all("SELECT * FROM trades WHERE signal_id = ?", (sig_id,))
            assert len(rows) == 1
            assert dict(rows[0])["status"] == "FAILED"
            # Capital released
            assert "res_fail" in fm.released
            store.close()
            print("  OK broker failure -> trade FAILED + capital released (OP7)")

    def test_fill_event_commits_capital_and_updates_trade(self) -> None:
        """OrderFilled event -> capital committed + trade status = OPEN. (OP6)"""
        with TemporaryDirectory() as tmp:
            adapter = _MockAdapter()
            placer, store, fm, bus, _, om = self._make_placer(Path(tmp), adapter=adapter)
            sig_id = _seed_signal(store)

            placer.place(
                symbol="HDFC", side="BUY", qty=5,
                entry_price=1600.0, sl_price=1580.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_hdfc",
            )

            # Get the internal_order_id of the ENTRY order
            entry_internal = adapter.placed[0]["internal_order_id"]

            # Simulate OrderFilled event
            filled_at = now_ist().isoformat()
            bus.publish(OrderFilled(
                source_module="order_monitor",
                payload={},
                internal_order_id=entry_internal,
                broker_order_id=adapter.placed[0]["broker_order_id"],
                symbol="HDFC",
                side="BUY",
                filled_qty=5,
                avg_fill_price=1601.0,
                filled_at=filled_at,
            ))

            # Capital committed
            assert len(fm.committed) == 1
            assert fm.committed[0]["reservation_id"] == "res_hdfc"
            assert fm.committed[0]["actual_fill_price"] == pytest.approx(1601.0)
            assert fm.committed[0]["actual_qty"] == 5

            # Trade status updated to OPEN
            row = store.fetch_one("SELECT status FROM trades WHERE signal_id = ?", (sig_id,))
            assert row["status"] == "OPEN"
            store.close()
            print("  OK fill event -> capital committed + trade OPEN (OP6)")

    def test_fill_for_unknown_internal_id_is_ignored(self) -> None:
        """OrderFilled for unknown internal_order_id does nothing. (OP5)"""
        with TemporaryDirectory() as tmp:
            adapter = _MockAdapter()
            placer, store, fm, bus, _, om = self._make_placer(Path(tmp), adapter=adapter)

            # Dispatch fill for order we never placed
            bus.publish(OrderFilled(
                source_module="order_monitor",
                payload={},
                internal_order_id="ord_" + "9" * 32,
                broker_order_id="UNKNOWN",
                symbol="WHATEVER", side="BUY",
                filled_qty=1, avg_fill_price=100.0, filled_at="2026-04-15T10:00:00",
            ))
            assert len(fm.committed) == 0  # nothing committed
            store.close()
            print("  OK unknown fill event silently ignored (OP5)")

    def test_fill_map_entry_removed_after_fill(self) -> None:
        """Fill entry removed from _fill_map after OrderFilled. (OP5)"""
        with TemporaryDirectory() as tmp:
            adapter = _MockAdapter()
            placer, store, fm, bus, _, om = self._make_placer(Path(tmp), adapter=adapter)
            sig_id = _seed_signal(store)

            placer.place(
                symbol="SBIN", side="BUY", qty=2,
                entry_price=600.0, sl_price=590.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_sbin",
            )
            entry_internal = adapter.placed[0]["internal_order_id"]

            # Before fill
            with placer._fill_map_lock:
                assert entry_internal in placer._fill_map

            # Fire fill
            bus.publish(OrderFilled(
                source_module="test",
                payload={},
                internal_order_id=entry_internal,
                broker_order_id="BROKER1",
                symbol="SBIN", side="BUY",
                filled_qty=2, avg_fill_price=601.0,
                filled_at=now_ist().isoformat(),
            ))

            # After fill, entry removed from map
            with placer._fill_map_lock:
                assert entry_internal not in placer._fill_map
            store.close()
            print("  OK fill_map entry removed after fill (OP5)")

    def test_place_co_protocol_places_two_orders(self) -> None:
        """CO_PLUS_TGT protocol places 2 orders (CO + TGT). (OP9)"""
        with TemporaryDirectory() as tmp:
            adapter = _MockAdapter()
            placer, store, fm, bus, _, om = self._make_placer(
                Path(tmp), adapter=adapter, default_protocol="CO_PLUS_TGT"
            )
            sig_id = _seed_signal(store)

            placer.place(
                symbol="INFY", side="BUY", qty=5,
                entry_price=1800.0, sl_price=1770.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_co",
            )
            # CO_PLUS_TGT: CO entry + LIMIT TGT = 2 orders
            assert len(adapter.placed) == 2
            assert adapter.placed[0]["variety"] == "co"
            store.close()
            print("  OK CO_PLUS_TGT places 2 orders (OP9)")


# ─────────────────────────────────────────────────────────────────────────────
# OP-LM1: kill_switch last-mile check
# ─────────────────────────────────────────────────────────────────────────────

class TestKillSwitchLastMile:

    def test_kill_switch_active_aborts_no_adapter_call(self) -> None:
        """Active kill_switch -> no adapter call, trade=FAILED, reservation released. (OP-LM1)"""
        with TemporaryDirectory() as tmp:
            adapter = _MockAdapter()
            store = _make_store(Path(tmp))
            sig_id = _seed_signal(store)
            co_proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
            limit_proto = LimitTripleProtocol(adapter=adapter, logger=_log())
            engine = FullEntryEngine(
                co_protocol=co_proto, limit_protocol=limit_proto,
                logger=_log(), default_protocol="LIMIT_TRIPLE",
            )
            om = OrderManager(store, _log())
            fm = _MockFundManager()
            bus = EventBus()
            ks = _MockKillSwitch(active=True)

            placer = OrderPlacer(
                entry_engine=engine,
                order_manager=om,
                fund_manager=fm,
                bus=bus,
                logger=_log(),
                order_monitor=MagicMock(spec=OrderMonitor),  # BL-7b
            cost_calculator=MagicMock(spec=CostCalculator),  # BL-10a
                kill_switch=ks,
            )

            with pytest.raises(OrderRejectedError, match="kill_switch_active_last_mile"):
                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=2500.0, sl_price=2450.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_ks",
                )

            # Adapter must NOT have been called
            assert len(adapter.placed) == 0

            # Trade row must exist and be FAILED
            rows = store.fetch_all("SELECT * FROM trades WHERE signal_id = ?", (sig_id,))
            assert len(rows) == 1
            assert dict(rows[0])["status"] == "FAILED"

            # Reservation released
            assert "res_ks" in fm.released

            store.close()
            print("  OK kill_switch active -> no adapter call, trade FAILED, capital released (OP-LM1)")

    def test_kill_switch_inactive_does_not_block(self) -> None:
        """Inactive kill_switch -> placement proceeds normally. (OP-LM1)"""
        with TemporaryDirectory() as tmp:
            adapter = _MockAdapter()
            store = _make_store(Path(tmp))
            sig_id = _seed_signal(store)
            co_proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
            limit_proto = LimitTripleProtocol(adapter=adapter, logger=_log())
            engine = FullEntryEngine(
                co_protocol=co_proto, limit_protocol=limit_proto,
                logger=_log(), default_protocol="LIMIT_TRIPLE",
            )
            om = OrderManager(store, _log())
            fm = _MockFundManager()
            bus = EventBus()
            ks = _MockKillSwitch(active=False)

            placer = OrderPlacer(
                entry_engine=engine, order_manager=om,
                fund_manager=fm, bus=bus, logger=_log(),
                order_monitor=MagicMock(spec=OrderMonitor),  # BL-7b
            cost_calculator=MagicMock(spec=CostCalculator),  # BL-10a
                kill_switch=ks,
            )

            placer.place(
                symbol="TCS", side="BUY", qty=5,
                entry_price=4000.0, sl_price=3950.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_ks_ok",
            )

            assert len(adapter.placed) == 3  # LIMIT_TRIPLE: all 3 placed
            store.close()
            print("  OK kill_switch inactive -> placement proceeds (OP-LM1 negative)")


# ─────────────────────────────────────────────────────────────────────────────
# OP-LM2: reservation release on placement failure (explicit verification)
# ─────────────────────────────────────────────────────────────────────────────

class TestReservationRelease:

    def test_reservation_released_on_broker_error(self) -> None:
        """Every BrokerError path releases the capital reservation. (OP-LM2)"""
        with TemporaryDirectory() as tmp:
            adapter = _MockAdapter(raises=OrderRejectedError("rejected hard"))
            store = _make_store(Path(tmp))
            sig_id = _seed_signal(store)
            co_proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
            limit_proto = LimitTripleProtocol(adapter=adapter, logger=_log())
            engine = FullEntryEngine(
                co_protocol=co_proto, limit_protocol=limit_proto,
                logger=_log(), default_protocol="LIMIT_TRIPLE",
            )
            om = OrderManager(store, _log())
            fm = _MockFundManager()
            bus = EventBus()
            placer = OrderPlacer(
                entry_engine=engine, order_manager=om,
                fund_manager=fm, bus=bus, logger=_log(),
                order_monitor=MagicMock(spec=OrderMonitor),  # BL-7b
            cost_calculator=MagicMock(spec=CostCalculator),  # BL-10a
            )

            with pytest.raises(BrokerError):
                placer.place(
                    symbol="FAIL", side="BUY", qty=1,
                    entry_price=100.0, sl_price=95.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_lm2",
                )

            assert "res_lm2" in fm.released, "Reservation not released on broker failure"
            store.close()
            print("  OK BrokerError path releases reservation (OP-LM2)")


# ─────────────────────────────────────────────────────────────────────────────
# OP-LM3: empty broker_order_id treated as failure
# ─────────────────────────────────────────────────────────────────────────────

class _MockAdapterEmptyBrokerId:
    """Adapter that returns PlacedOrder with empty broker_order_id on the Nth call."""

    def __init__(self, empty_on_call: int = 1) -> None:
        self._empty_on = empty_on_call
        self._call_count = 0
        self.placed: List[dict] = []
        self.cancelled: List[str] = []

    def place_order(self, symbol, side, qty, price, order_type, intent,
                    tag=None, trigger_price=0.0, variety="regular"):
        self._call_count += 1
        from core.ids import new_order_id
        broker_id = "" if self._call_count == self._empty_on else (
            "PAPER_" + __import__("uuid").uuid4().hex[:12].upper()
        )
        from broker.zerodha_adapter import PlacedOrder
        from datetime import datetime
        po = PlacedOrder(
            internal_order_id=new_order_id(),
            broker_order_id=broker_id,
            symbol=symbol, side=side, qty=qty, price=price,
            order_type=order_type, product="MIS",
            status="SUBMITTED", ts=datetime.now(),
        )
        self.placed.append({"broker_order_id": broker_id, "symbol": symbol})
        return po

    def cancel_order(self, broker_order_id: str):
        from broker.zerodha_adapter import CancelResult
        self.cancelled.append(broker_order_id)
        return CancelResult(broker_order_id=broker_order_id, success=True, reason="")


class TestEmptyBrokerOrderId:

    def test_limit_triple_empty_entry_id_raises(self) -> None:
        """LIMIT_TRIPLE: empty broker_order_id on ENTRY -> OrderRejectedError. (OP-LM3)"""
        adapter = _MockAdapterEmptyBrokerId(empty_on_call=1)
        proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        with pytest.raises(OrderRejectedError, match="empty broker_order_id"):
            proto.execute(
                symbol="RELIANCE", side="BUY", qty=10,
                entry_price=2500.0, sl_price=2450.0, tgt_price=2600.0,
                intent="INTRADAY", trade_id="trd_lm3_entry",
            )
        print("  OK empty entry broker_order_id -> OrderRejectedError (OP-LM3 ENTRY)")

    def test_limit_triple_empty_sl_id_raises(self) -> None:
        """LIMIT_TRIPLE: empty broker_order_id on SL -> OrderRejectedError + entry cancelled. (OP-LM3)"""
        adapter = _MockAdapterEmptyBrokerId(empty_on_call=2)
        proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        with pytest.raises(OrderRejectedError, match="empty broker_order_id"):
            proto.execute(
                symbol="SBIN", side="BUY", qty=5,
                entry_price=600.0, sl_price=585.0, tgt_price=630.0,
                intent="INTRADAY", trade_id="trd_lm3_sl",
            )
        # Entry was placed (call 1) and should have been cancelled
        assert len(adapter.cancelled) == 1
        print("  OK empty SL broker_order_id -> OrderRejectedError + entry cancelled (OP-LM3 SL)")

    def test_co_empty_co_id_raises(self) -> None:
        """CO_PLUS_TGT: empty broker_order_id on CO -> OrderRejectedError. (OP-LM3)"""
        adapter = _MockAdapterEmptyBrokerId(empty_on_call=1)
        proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
        with pytest.raises(OrderRejectedError, match="empty broker_order_id"):
            proto.execute(
                symbol="INFY", side="BUY", qty=5,
                entry_price=1800.0, sl_price=1770.0, tgt_price=1860.0,
                intent="INTRADAY", trade_id="trd_lm3_co",
            )
        print("  OK empty CO broker_order_id -> OrderRejectedError (OP-LM3 CO)")

    def test_co_empty_tgt_id_is_failure(self) -> None:
        """MED #13: CO_PLUS_TGT empty TGT broker_order_id -> success=False. (OP-LM3 + MED #13)"""
        adapter = _MockAdapterEmptyBrokerId(empty_on_call=2)
        proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
        result = proto.execute(
            symbol="HDFC", side="BUY", qty=3,
            entry_price=1600.0, sl_price=1580.0, tgt_price=1640.0,
            intent="INTRADAY", trade_id="trd_lm3_co_tgt",
        )
        assert not result.success, "MED #13: empty TGT broker_order_id must return success=False"
        assert result.entry_broker_order_id, "CO broker_order_id preserved for reconciliation"
        assert result.tgt_broker_order_id == ""
        assert result.rejection_reason
        print("  OK empty TGT broker_order_id -> success=False; CO preserved (MED #13 + OP-LM3)")


class TestProductResolverWiring:
    """HIGH #7: product_resolver injected into OrderPlacer yields correct product code."""

    _PRODUCT_MAP = {"zerodha": {"INTRADAY": "MIS", "DELIVERY": "CNC", "COVER_ORDER": "CO"}}

    def _make_placer_with_resolver(self, tmp_path):
        store = _make_store(tmp_path)
        adapter = _MockAdapter()
        co_proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
        limit_proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        engine = FullEntryEngine(
            co_protocol=co_proto, limit_protocol=limit_proto,
            logger=_log(), default_protocol="LIMIT_TRIPLE",
        )
        om = OrderManager(store, _log())
        fm = _MockFundManager()
        bus = EventBus()
        resolver = ProductResolver(self._PRODUCT_MAP)
        placer = OrderPlacer(
            entry_engine=engine,
            order_manager=om,
            fund_manager=fm,
            bus=bus,
            logger=_log(),
            order_monitor=MagicMock(spec=OrderMonitor),  # BL-7b
            cost_calculator=MagicMock(spec=CostCalculator),  # BL-10a
            rr_ratio=2.0,
            product_resolver=resolver,
        )
        return placer, store, om

    def test_intraday_uses_mis_from_resolver(self) -> None:
        """HIGH #7: INTRADAY intent -> product_resolver.resolve('INTRADAY') = 'MIS'."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            placer, store, om = self._make_placer_with_resolver(Path(tmp))
            sig_id = _seed_signal(store)
            placer.place(
                symbol="RELIANCE", side="BUY", qty=5,
                entry_price=2500.0, sl_price=2450.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_test",
            )
            # Check that the ENTRY order row in DB has product="MIS"
            trade_row = store.fetch_one(
                "SELECT trade_id FROM trades WHERE signal_id = ?", (sig_id,)
            )
            assert trade_row is not None
            entry_orders = store.fetch_all(
                "SELECT product FROM orders WHERE trade_id = ? AND leg = 'ENTRY'",
                (trade_row["trade_id"],),
            )
            assert entry_orders, "Expected ENTRY order row"
            assert entry_orders[0]["product"] == "MIS", (
                f"Expected MIS from resolver, got {entry_orders[0]['product']}"
            )
            print("  OK HIGH #7: product_resolver.resolve('INTRADAY') = 'MIS' used in DB")


# ─────────────────────────────────────────────────────────────────────────────
# BL-7a — _FillEntry leg taxonomy lockdown
# ─────────────────────────────────────────────────────────────────────────────

class TestBl7aFillEntryLegTaxonomy:
    """
    BL-7a: _FillEntry validates leg ∈ {ENTRY, SL, TGT, EOD} and exposes
    order_protocol + direction for branching in the split fill handler (BL-7d).
    """

    def test_fill_entry_rejects_invalid_leg(self) -> None:
        """Constructor must reject leg values outside _VALID_LEGS."""
        try:
            _FillEntry(
                trade_id="trd_x", reservation_id="res_x", symbol="RELIANCE",
                qty=1, leg="BOGUS",
                order_protocol="LIMIT_TRIPLE", direction="LONG",
            )
        except ValueError as exc:
            assert "BOGUS" in str(exc)
            assert "ENTRY" in str(exc)  # lists valid set
            print("  OK _FillEntry rejects invalid leg (BL-7a)")
            return
        raise AssertionError("Expected ValueError on invalid leg")

    def test_fill_entry_accepts_all_valid_legs(self) -> None:
        """All four valid legs construct cleanly."""
        for leg in (_LEG_ENTRY, _LEG_SL, _LEG_TGT, _LEG_EOD):
            fe = _FillEntry(
                trade_id="trd_x", reservation_id="res_x", symbol="RELIANCE",
                qty=1, leg=leg,
                order_protocol="LIMIT_TRIPLE", direction="LONG",
            )
            assert fe.leg == leg
        assert _VALID_LEGS == frozenset({"ENTRY", "SL", "TGT", "EOD"})
        print("  OK _FillEntry accepts all 4 valid legs (BL-7a)")

    def test_fill_entry_stores_order_protocol_and_direction(self) -> None:
        """New fields order_protocol + direction persist on the entry."""
        fe = _FillEntry(
            trade_id="trd_abc", reservation_id="res_123", symbol="HDFC",
            qty=5, leg=_LEG_ENTRY,
            order_protocol="CO_PLUS_TGT", direction="SHORT",
        )
        assert fe.trade_id == "trd_abc"
        assert fe.reservation_id == "res_123"
        assert fe.symbol == "HDFC"
        assert fe.qty == 5
        assert fe.leg == "ENTRY"
        assert fe.order_protocol == "CO_PLUS_TGT"
        assert fe.direction == "SHORT"
        print("  OK _FillEntry stores order_protocol + direction (BL-7a)")


# ─────────────────────────────────────────────────────────────────────────────
# BL-12 — OrderStatusChanged event pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestBl12OrderStatusEventPipeline:
    """
    End-to-end tests for BL-12: OrderMonitor publishes OrderStatusChanged on
    every successful OSM transition; OrderManager (subscribed to the bus)
    updates the orders table. Covers COMPLETE/CANCELLED/REJECTED/PARTIAL.
    Uses real DB, real OrderManager, real OrderMonitor, real EventBus.
    """

    @staticmethod
    def _seed_trade_and_order(
        store: StateStore,
        broker_order_id: str,
        qty: int = 10,
        entry_price: float = 2500.0,
    ) -> str:
        """Seed a trade + one ENTRY order row so update_order_status has a row to hit."""
        sig_id = _seed_signal(store)
        om = OrderManager(store, _log())  # bus=None; seed-only helper
        trade_id = om.create_trade(
            signal_id=sig_id, symbol="RELIANCE", direction="LONG",
            strategy="gap_go_long", sector=None, qty=qty,
            entry_target_price=entry_price,
            sl_initial=entry_price - 50.0,
            tgt_initial=entry_price + 100.0,
            order_protocol="LIMIT_TRIPLE", margin_reserved=entry_price * qty,
            risk_amount=50.0 * qty,
        )
        om.insert_order(
            trade_id=trade_id, broker_order_id=broker_order_id,
            leg="ENTRY", transaction_type="BUY",
            order_type="LIMIT", product="MIS", variety="regular",
            qty_requested=qty, price=entry_price,
        )
        return trade_id

    @staticmethod
    def _make_pipeline(store: StateStore) -> tuple[OrderManager, Any, Any, EventBus]:
        """Build real OM (subscribed) + OSM + Monitor on a shared bus."""
        from broker.order_monitor import OrderMonitor
        from broker.order_state_machine import OrderStateMachine

        bus = EventBus()
        om = OrderManager(store, _log(), bus=bus)   # bus-wired: subscribes
        osm = OrderStateMachine(bus=bus)

        class _Adapter:
            def get_order_history(self, _): return []
            def cancel_order(self, _): ...

        monitor = OrderMonitor(
            adapter=_Adapter(),
            state_machine=osm,
            bus=bus,
            logger=_log(),
            poll_interval_sec=1,
            fill_timeout_sec=60,
        )
        return om, osm, monitor, bus

    @staticmethod
    def _track(monitor: Any, osm: Any, internal_id: str, broker_id: str,
               qty: int = 10, filled_qty: int = 0, avg_price: float = 0.0) -> None:
        osm.register(internal_id)
        osm.transition(internal_id, "SUBMITTED")
        monitor.track(
            internal_order_id=internal_id, broker_order_id=broker_id,
            symbol="RELIANCE", side="BUY", qty=qty,
            expected_price=2500.0, placed_at=now_ist(),
        )
        # Pre-populate fill data on the watch entry (monitor's poll would do this)
        entry = monitor._watched[internal_id]
        entry.filled_qty = filled_qty
        entry.avg_fill_price = avg_price

    def test_order_monitor_complete_updates_orders_table_status(self) -> None:
        """COMPLETE transition → orders.status='COMPLETE' with qty/avg_price persisted."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            self._seed_trade_and_order(store, "KITE001")
            om, osm, monitor, _ = self._make_pipeline(store)
            # Re-subscribe: _make_pipeline's om isn't the seeding one — verify here
            self._track(monitor, osm, "ord_c1", "KITE001",
                        qty=10, filled_qty=10, avg_price=2510.0)
            # Walk OSM: SUBMITTED -> OPEN -> COMPLETE
            monitor._safe_transition("ord_c1", "OPEN",
                                     entry=monitor._watched["ord_c1"])
            monitor._safe_transition("ord_c1", "COMPLETE",
                                     entry=monitor._watched["ord_c1"])
            row = store.fetch_one(
                "SELECT status, qty_filled, avg_fill_price FROM orders WHERE order_id = ?",
                ("KITE001",),
            )
            assert row is not None
            assert row["status"] == "COMPLETE"
            assert row["qty_filled"] == 10
            assert row["avg_fill_price"] == pytest.approx(2510.0)
            store.close()
            print("  OK BL-12: COMPLETE -> orders.status=COMPLETE persisted")

    def test_order_monitor_cancelled_updates_orders_table_status(self) -> None:
        """CANCELLED transition → orders.status='CANCELLED'; qty_filled=0 when no partial."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            self._seed_trade_and_order(store, "KITE002")
            om, osm, monitor, _ = self._make_pipeline(store)
            self._track(monitor, osm, "ord_x1", "KITE002")
            monitor._safe_transition("ord_x1", "OPEN",
                                     entry=monitor._watched["ord_x1"])
            monitor._safe_transition("ord_x1", "CANCELLED",
                                     entry=monitor._watched["ord_x1"])
            row = store.fetch_one(
                "SELECT status, qty_filled, avg_fill_price FROM orders WHERE order_id = ?",
                ("KITE002",),
            )
            assert row["status"] == "CANCELLED"
            assert row["qty_filled"] == 0
            assert row["avg_fill_price"] is None   # never filled
            store.close()
            print("  OK BL-12: CANCELLED -> orders.status=CANCELLED persisted")

    def test_order_monitor_rejected_updates_orders_table_status(self) -> None:
        """REJECTED maps to OSM FAILED → orders.status='FAILED'.

        (OSM is authoritative; rejected broker orders land in the FAILED
        terminal state, which is what persists to the orders table.)
        """
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            self._seed_trade_and_order(store, "KITE003")
            om, osm, monitor, _ = self._make_pipeline(store)
            self._track(monitor, osm, "ord_r1", "KITE003")
            # REJECTED broker status -> _handle_terminal with osm_state="FAILED"
            monitor._safe_transition("ord_r1", "FAILED",
                                     entry=monitor._watched["ord_r1"])
            row = store.fetch_one(
                "SELECT status FROM orders WHERE order_id = ?", ("KITE003",),
            )
            assert row["status"] == "FAILED"
            store.close()
            print("  OK BL-12: REJECTED (OSM=FAILED) -> orders.status=FAILED persisted")

    def test_partial_fill_updates_qty_filled_in_orders_table(self) -> None:
        """PARTIAL transition → qty_filled reflects broker-reported partial qty."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            self._seed_trade_and_order(store, "KITE004", qty=10)
            om, osm, monitor, _ = self._make_pipeline(store)
            self._track(monitor, osm, "ord_p1", "KITE004",
                        qty=10, filled_qty=4, avg_price=2505.0)
            monitor._safe_transition("ord_p1", "OPEN",
                                     entry=monitor._watched["ord_p1"])
            monitor._safe_transition("ord_p1", "PARTIAL",
                                     entry=monitor._watched["ord_p1"])
            row = store.fetch_one(
                "SELECT status, qty_filled, avg_fill_price FROM orders WHERE order_id = ?",
                ("KITE004",),
            )
            assert row["status"] == "PARTIAL"
            assert row["qty_filled"] == 4
            assert row["avg_fill_price"] == pytest.approx(2505.0)
            store.close()
            print("  OK BL-12: PARTIAL -> qty_filled=4 persisted")

    def test_order_manager_subscribes_to_order_status_changed(self) -> None:
        """OMgr10: when bus is provided, OM subscribes to OrderStatusChanged."""
        bus = EventBus()
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            self._seed_trade_and_order(store, "KITE_SUB")
            om = OrderManager(store, _log(), bus=bus)
            # Publish directly; handler must write to DB
            bus.publish(OrderStatusChanged(
                source_module="test", internal_order_id="ord_sub",
                broker_order_id="KITE_SUB", status="COMPLETE",
                qty_filled=10, avg_fill_price=2510.0,
            ))
            row = store.fetch_one(
                "SELECT status, qty_filled FROM orders WHERE order_id = ?",
                ("KITE_SUB",),
            )
            assert row["status"] == "COMPLETE"
            assert row["qty_filled"] == 10
            # And the bus actually has a subscriber registered for this type
            assert len(bus._subscribers.get(OrderStatusChanged, [])) == 1
            store.close()
            print("  OK OMgr10: bus-wired OM subscribes and persists snapshot")

    def test_order_manager_bus_none_does_not_subscribe(self) -> None:
        """Standalone mode (bus=None): no subscription, publishing elsewhere is a no-op for this instance."""
        bus = EventBus()
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            self._seed_trade_and_order(store, "KITE_NONE")
            # Construct WITHOUT bus
            _ = OrderManager(store, _log(), bus=None)
            # A separately published event on some OTHER bus must not update DB
            bus.publish(OrderStatusChanged(
                source_module="test", internal_order_id="ord_n1",
                broker_order_id="KITE_NONE", status="COMPLETE",
                qty_filled=7, avg_fill_price=2500.0,
            ))
            row = store.fetch_one(
                "SELECT status, qty_filled FROM orders WHERE order_id = ?",
                ("KITE_NONE",),
            )
            # Row untouched -- still at PENDING (insert_order default)
            assert row["status"] == "PENDING"
            assert row["qty_filled"] == 0
            assert OrderStatusChanged not in bus._subscribers
            store.close()
            print("  OK OMgr10: bus=None -> no subscription, orders row untouched")


# ─────────────────────────────────────────────────────────────────────────────
# BL-7b: OrderPlacer dependency injection (order_monitor + smart_tgt + cfg)
# ─────────────────────────────────────────────────────────────────────────────

class TestBl7bOrderPlacerDependencyInjection:
    """
    Locks the construction contract for the A.3.b wiring change:
      - order_monitor is a required dependency (no default).
      - smart_tgt_manager + smart_tgt_config are co-required (paired or neither).
      - ValueError message must name both field names so operators can fix it.
    """

    def _minimal_kwargs(self) -> dict:
        """Bare minimum kwargs to construct OrderPlacer (everything else default)."""
        return dict(
            entry_engine=MagicMock(spec=FullEntryEngine),
            order_manager=MagicMock(spec=OrderManager),
            fund_manager=MagicMock(),
            bus=EventBus(),
            logger=_log(),
            cost_calculator=MagicMock(spec=CostCalculator),
        )

    def test_order_placer_stores_injected_order_monitor(self) -> None:
        """order_monitor is injected and stored as attribute. (BL-7b)"""
        monitor = MagicMock(spec=OrderMonitor)
        placer = OrderPlacer(order_monitor=monitor, **self._minimal_kwargs())
        assert placer._order_monitor is monitor
        print("  OK BL-7b: order_monitor injected and stored")

    def test_order_placer_accepts_none_smart_tgt_manager(self) -> None:
        """Both smart_tgt params omitted → OK; attrs are None. (BL-7b)"""
        placer = OrderPlacer(
            order_monitor=MagicMock(spec=OrderMonitor),
            **self._minimal_kwargs(),
        )
        assert placer._smart_tgt_manager is None
        assert placer._smart_tgt_config is None
        print("  OK BL-7b: smart_tgt_manager=None, smart_tgt_config=None is valid")

    def test_order_placer_raises_when_smart_tgt_manager_without_config(self) -> None:
        """smart_tgt_manager provided without smart_tgt_config → ValueError. (BL-7b)"""
        from orders.smart_tgt_manager import SmartTgtManager
        with pytest.raises(ValueError, match="smart_tgt_config"):
            OrderPlacer(
                order_monitor=MagicMock(spec=OrderMonitor),
                smart_tgt_manager=MagicMock(spec=SmartTgtManager),
                smart_tgt_config=None,
                **self._minimal_kwargs(),
            )
        print("  OK BL-7b: guard raises ValueError naming smart_tgt_config")

    def test_order_placer_accepts_smart_tgt_manager_with_config(self) -> None:
        """Both smart_tgt params provided → OK; stored as attrs. (BL-7b)"""
        from core.config_loader import SmartTgtConfig
        from orders.smart_tgt_manager import SmartTgtManager
        mgr = MagicMock(spec=SmartTgtManager)
        cfg = SmartTgtConfig(enabled=True, trigger_pct=0.005, step_pct=0.003)
        placer = OrderPlacer(
            order_monitor=MagicMock(spec=OrderMonitor),
            smart_tgt_manager=mgr,
            smart_tgt_config=cfg,
            **self._minimal_kwargs(),
        )
        assert placer._smart_tgt_manager is mgr
        assert placer._smart_tgt_config is cfg
        print("  OK BL-7b: smart_tgt_manager + smart_tgt_config both stored")

    def test_order_placer_requires_cost_calculator(self) -> None:
        """cost_calculator is a REQUIRED ctor param — missing it raises TypeError. (BL-10a)"""
        kwargs = self._minimal_kwargs()
        kwargs.pop("cost_calculator")
        with pytest.raises(TypeError, match="cost_calculator"):
            OrderPlacer(
                order_monitor=MagicMock(spec=OrderMonitor),
                **kwargs,
            )
        print("  OK BL-10a: cost_calculator is required (TypeError on omission)")


# ─────────────────────────────────────────────────────────────────────────────
# BL-7c: OrderPlacer.track() wiring + interim _on_order_filled guard (A.3.c)
# ─────────────────────────────────────────────────────────────────────────────

class TestBl7cOrderPlacerTrackingWiring:
    """
    Locks behavior for A.3.c:
      - place() calls order_monitor.track() once per placed leg (ENTRY always,
        SL only for LIMIT_TRIPLE, TGT when present).
      - _fill_map gets one entry per tracked leg with correct leg + sides.
      - _on_order_filled peeks-only; non-ENTRY legs stay in _fill_map and
        do NOT commit capital until A.3.d wires the split dispatcher.
      - ENTRY-leg fills are still handled end-to-end (guard regression).
    """

    def _build(
        self,
        tmp_path: Path,
        default_protocol: str = "LIMIT_TRIPLE",
    ):
        """Build a real placer wired to a MagicMock OrderMonitor + MockAdapter."""
        store = _make_store(tmp_path)
        adapter = _MockAdapter()
        co_proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
        limit_proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        engine = FullEntryEngine(
            co_protocol=co_proto, limit_protocol=limit_proto,
            logger=_log(), default_protocol=default_protocol,
        )
        om = OrderManager(store, _log())
        fm = _MockFundManager()
        bus = EventBus()
        monitor = MagicMock(spec=OrderMonitor)
        placer = OrderPlacer(
            entry_engine=engine,
            order_manager=om,
            fund_manager=fm,
            bus=bus,
            logger=_log(),
            order_monitor=monitor,
            cost_calculator=MagicMock(spec=CostCalculator),  # BL-10a
            default_order_protocol=default_protocol,
        )
        return placer, monitor, adapter, store, fm, bus

    def test_place_entry_calls_order_monitor_track_for_entry_leg(self) -> None:
        """LIMIT_TRIPLE place() -> track() called 3x; _fill_map has 3 legs. (BL-7c)"""
        with TemporaryDirectory() as tmp:
            placer, monitor, adapter, store, _, _ = self._build(Path(tmp))
            sig_id = _seed_signal(store)

            placer.place(
                symbol="RELIANCE", side="BUY", qty=10,
                entry_price=2500.0, sl_price=2450.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_e",
            )

            # 3 track() calls: entry, SL, TGT
            assert monitor.track.call_count == 3, (
                f"expected 3 track() calls, got {monitor.track.call_count}"
            )

            # Collect legs from _fill_map
            with placer._fill_map_lock:
                legs = sorted(e.leg for e in placer._fill_map.values())
            assert legs == [_LEG_ENTRY, _LEG_SL, _LEG_TGT], (
                f"expected ENTRY/SL/TGT legs, got {legs}"
            )
            store.close()
            print("  OK BL-7c: LIMIT_TRIPLE place() tracks all 3 legs")

    def test_place_entry_tracks_all_three_legs_for_limit_triple(self) -> None:
        """LIMIT_TRIPLE: entry side = signal side; SL/TGT sides inverted. (BL-7c)"""
        with TemporaryDirectory() as tmp:
            placer, monitor, adapter, store, _, _ = self._build(Path(tmp))
            sig_id = _seed_signal(store)

            placer.place(
                symbol="TCS", side="BUY", qty=5,
                entry_price=4000.0, sl_price=3950.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_sides",
            )

            # Build per-leg side lookup from _fill_map (leg -> side inferred via direction/exit)
            # Easier: inspect the actual track() calls directly.
            sides_by_leg: Dict[str, str] = {}
            with placer._fill_map_lock:
                for internal_id, entry in placer._fill_map.items():
                    # Find the track() call whose internal_order_id matches
                    for call_args in monitor.track.call_args_list:
                        if call_args.kwargs.get("internal_order_id") == internal_id:
                            sides_by_leg[entry.leg] = call_args.kwargs["side"]
                            break

            assert sides_by_leg[_LEG_ENTRY] == "BUY"
            assert sides_by_leg[_LEG_SL] == "SELL"
            assert sides_by_leg[_LEG_TGT] == "SELL"
            store.close()
            print("  OK BL-7c: LIMIT_TRIPLE track sides ENTRY=BUY, SL/TGT=SELL")

    def test_co_protocol_skips_sl_track(self) -> None:
        """CO_PLUS_TGT: sl_broker_order_id empty -> track called 2x; no SL _FillEntry. (BL-7c)"""
        with TemporaryDirectory() as tmp:
            placer, monitor, adapter, store, _, _ = self._build(
                Path(tmp), default_protocol="CO_PLUS_TGT"
            )
            sig_id = _seed_signal(store)

            placer.place(
                symbol="INFY", side="BUY", qty=8,
                entry_price=1800.0, sl_price=1780.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_co",
            )

            # Only ENTRY + TGT tracked
            assert monitor.track.call_count == 2, (
                f"CO: expected 2 track() calls, got {monitor.track.call_count}"
            )

            with placer._fill_map_lock:
                legs = sorted(e.leg for e in placer._fill_map.values())
            assert legs == [_LEG_ENTRY, _LEG_TGT], (
                f"CO: expected ENTRY+TGT in _fill_map, got {legs}"
            )
            # No SL track ever happened
            for call_args in monitor.track.call_args_list:
                assert call_args.kwargs.get("side") != "__NEVER__"  # sanity
            store.close()
            print("  OK BL-7c: CO_PLUS_TGT skips SL track (2 legs only)")

    def test_on_order_filled_still_handles_entry_leg(self) -> None:
        """ENTRY-leg fill still pops + commits capital end-to-end. (BL-7c regression)"""
        with TemporaryDirectory() as tmp:
            placer, _, adapter, store, fm, bus = self._build(Path(tmp))
            sig_id = _seed_signal(store)

            placer.place(
                symbol="SBIN", side="BUY", qty=2,
                entry_price=600.0, sl_price=590.0,
                intent="INTRDAY" if False else "INTRADAY",
                signal_id=sig_id,
                reservation_id="res_entry_ok",
            )

            # Find the entry leg's internal_id (it's the first placed order — LIMIT_TRIPLE).
            entry_internal = adapter.placed[0]["internal_order_id"]

            bus.publish(OrderFilled(
                source_module="test",
                payload={},
                internal_order_id=entry_internal,
                broker_order_id="BROKER_ENTRY",
                symbol="SBIN",
                side="BUY",
                avg_fill_price=601.0,
                filled_qty=2,
                filled_at=now_ist().isoformat(),
            ))

            # Entry popped from _fill_map; capital committed.
            with placer._fill_map_lock:
                assert entry_internal not in placer._fill_map
            assert len(fm.committed) == 1, \
                f"expected 1 commit, got {len(fm.committed)}"
            assert fm.committed[0]["reservation_id"] == "res_entry_ok"
            store.close()
            print("  OK BL-7c: ENTRY fill path still handled (guard doesn't regress)")


# ─────────────────────────────────────────────────────────────────────────────
# BL-7d SUB-STEP 3: smart_tgt.register_trade wiring in _handle_entry_fill
# ─────────────────────────────────────────────────────────────────────────────

class TestBl7dEntryFillSmartTgt:
    """
    Locks behavior for SUB-STEP 3 of A.3.d:
      - CO_PLUS_TGT entry fill calls smart_tgt.register_trade with correct args.
      - LIMIT_TRIPLE entry fill does NOT call register_trade (static SL at broker).
      - smart_tgt_manager=None is a no-op (no crash, no register).
    """

    def _build(
        self,
        tmp_path: Path,
        default_protocol: str,
        smart_tgt_manager,
        smart_tgt_config,
    ):
        from core.config_loader import SmartTgtConfig
        store = _make_store(tmp_path)
        adapter = _MockAdapter()
        co_proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
        limit_proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        engine = FullEntryEngine(
            co_protocol=co_proto, limit_protocol=limit_proto,
            logger=_log(), default_protocol=default_protocol,
        )
        om = OrderManager(store, _log())
        fm = _MockFundManager()
        bus = EventBus()
        placer = OrderPlacer(
            entry_engine=engine,
            order_manager=om,
            fund_manager=fm,
            bus=bus,
            logger=_log(),
            order_monitor=MagicMock(spec=OrderMonitor),
            cost_calculator=MagicMock(spec=CostCalculator),
            default_order_protocol=default_protocol,
            smart_tgt_manager=smart_tgt_manager,
            smart_tgt_config=smart_tgt_config,
        )
        return placer, adapter, store, bus, om

    def _cfg(self):
        from core.config_loader import SmartTgtConfig
        return SmartTgtConfig(enabled=True, trigger_pct=0.005, step_pct=0.003)

    def test_co_entry_fill_registers_with_smart_tgt(self) -> None:
        """CO_PLUS_TGT entry fill triggers smart_tgt.register_trade with trade_id + fill price."""
        from orders.smart_tgt_manager import SmartTgtManager
        mgr = MagicMock(spec=SmartTgtManager)
        with TemporaryDirectory() as tmp:
            placer, adapter, store, bus, om = self._build(
                Path(tmp), "CO_PLUS_TGT", smart_tgt_manager=mgr, smart_tgt_config=self._cfg(),
            )
            sig_id = _seed_signal(store)
            placer.place(
                symbol="RELIANCE", side="BUY", qty=10,
                entry_price=2500.0, sl_price=2475.0,
                intent="COVER_ORDER", signal_id=sig_id,
                reservation_id="res_co_st",
            )
            entry_internal = adapter.placed[0]["internal_order_id"]

            bus.publish(OrderFilled(
                source_module="test", payload={},
                internal_order_id=entry_internal,
                broker_order_id="BROKER_ENTRY_CO",
                symbol="RELIANCE", side="BUY",
                avg_fill_price=2501.0, filled_qty=10,
                filled_at=now_ist().isoformat(),
            ))

            mgr.register_trade.assert_called_once()
            kwargs = mgr.register_trade.call_args.kwargs
            assert kwargs["symbol"] == "RELIANCE"
            assert kwargs["direction"] == "LONG"
            assert kwargs["entry_price"] == 2501.0
            assert kwargs["qty"] == 10
            assert kwargs["initial_sl"] == 2475.0
            assert kwargs["trigger_pct"] == 0.005
            assert kwargs["step_pct"] == 0.003
            store.close()
            print("  OK BL-7d SUB-STEP 3: CO_PLUS_TGT entry fill -> register_trade called")

    def test_limit_triple_entry_fill_skips_smart_tgt(self) -> None:
        """LIMIT_TRIPLE has static SL; register_trade must NOT fire even when manager present."""
        from orders.smart_tgt_manager import SmartTgtManager
        mgr = MagicMock(spec=SmartTgtManager)
        with TemporaryDirectory() as tmp:
            placer, adapter, store, bus, om = self._build(
                Path(tmp), "LIMIT_TRIPLE", smart_tgt_manager=mgr, smart_tgt_config=self._cfg(),
            )
            sig_id = _seed_signal(store)
            placer.place(
                symbol="TCS", side="BUY", qty=5,
                entry_price=4000.0, sl_price=3950.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_lt_st",
            )
            entry_internal = adapter.placed[0]["internal_order_id"]

            bus.publish(OrderFilled(
                source_module="test", payload={},
                internal_order_id=entry_internal,
                broker_order_id="BROKER_ENTRY_LT",
                symbol="TCS", side="BUY",
                avg_fill_price=4001.0, filled_qty=5,
                filled_at=now_ist().isoformat(),
            ))

            mgr.register_trade.assert_not_called()
            store.close()
            print("  OK BL-7d SUB-STEP 3: LIMIT_TRIPLE entry fill -> register_trade NOT called")

    def test_entry_fill_with_null_manager_is_noop(self) -> None:
        """smart_tgt_manager=None: no crash, no register call, entry still processed."""
        with TemporaryDirectory() as tmp:
            placer, adapter, store, bus, om = self._build(
                Path(tmp), "CO_PLUS_TGT", smart_tgt_manager=None, smart_tgt_config=None,
            )
            sig_id = _seed_signal(store)
            placer.place(
                symbol="SBIN", side="BUY", qty=2,
                entry_price=600.0, sl_price=595.0,
                intent="COVER_ORDER", signal_id=sig_id,
                reservation_id="res_no_mgr",
            )
            entry_internal = adapter.placed[0]["internal_order_id"]

            # Should not raise despite CO_PLUS_TGT protocol without a manager.
            bus.publish(OrderFilled(
                source_module="test", payload={},
                internal_order_id=entry_internal,
                broker_order_id="BROKER_ENTRY_NM",
                symbol="SBIN", side="BUY",
                avg_fill_price=600.5, filled_qty=2,
                filled_at=now_ist().isoformat(),
            ))

            # Entry leg popped (happy-path entry handling not regressed).
            with placer._fill_map_lock:
                assert entry_internal not in placer._fill_map
            store.close()
            print("  OK BL-7d SUB-STEP 3: smart_tgt_manager=None is a no-op")


# ─────────────────────────────────────────────────────────────────────────────
# BL-7d + BL-10a: _handle_exit_fill via leg dispatcher
# ─────────────────────────────────────────────────────────────────────────────

class TestBl7dExitFillHandling:
    """
    Locks behavior for SUB-STEPs 4-5 of A.3.d (BL-7d + BL-10a):
      - Dispatcher routes SL/TGT/EOD fills to _handle_exit_fill (not just ENTRY).
      - close_trade called with correct exit_reason per leg (SL_HIT/TGT_HIT/EOD_SQUAREOFF).
      - Direction-correct gross_pnl: LONG=(exit-entry)*qty, SHORT=(entry-exit)*qty.
      - Round-trip charges computed via cost_calculator.total_round_trip_cost.
      - fm.release_used called with direction + intent derived from protocol.
      - PositionClosed event published with net pnl after close_trade persists.
      - SmartTgtManager.unregister_trade fires for CO_PLUS_TGT only.
      - _fill_map entry popped on exit-fill dispatch.
      - Double-close (already CLOSED) is a WARNING + skip (no release, no publish).
      - Missing trade row is CRITICAL + skip.
    """

    # ---- factories ----------------------------------------------------------

    def _build(
        self,
        tmp_path: Path,
        protocol: str = "LIMIT_TRIPLE",
        smart_tgt_manager=None,
    ):
        """Build OrderPlacer with real store + OrderManager, mocked downstream deps."""
        from core.config_loader import SmartTgtConfig
        store = _make_store(tmp_path)
        adapter = _MockAdapter()
        co_proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
        limit_proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        engine = FullEntryEngine(
            co_protocol=co_proto, limit_protocol=limit_proto,
            logger=_log(), default_protocol=protocol,
        )
        om = OrderManager(store, _log())
        fm = _MockFundManager()
        bus = EventBus()
        cost_calc = MagicMock(spec=CostCalculator)
        cost_calc.total_round_trip_cost = MagicMock(return_value=25.0)

        cfg = SmartTgtConfig(enabled=True, trigger_pct=0.005, step_pct=0.003) \
            if smart_tgt_manager is not None else None

        placer = OrderPlacer(
            entry_engine=engine,
            order_manager=om,
            fund_manager=fm,
            bus=bus,
            logger=_log(),
            order_monitor=MagicMock(spec=OrderMonitor),
            cost_calculator=cost_calc,
            default_order_protocol=protocol,
            smart_tgt_manager=smart_tgt_manager,
            smart_tgt_config=cfg,
        )
        return placer, adapter, store, bus, om, fm, cost_calc

    def _seed_open_trade(
        self,
        store: StateStore,
        om: OrderManager,
        *,
        direction: str = "LONG",
        entry_fill: float = 2500.0,
        qty: int = 10,
        symbol: str = "RELIANCE",
        sl: float = 2450.0,
        tgt: float = 2600.0,
        protocol: str = "LIMIT_TRIPLE",
    ) -> tuple[str, str]:
        """Create signal + trade + record_entry_fill (status=OPEN). Returns (sig_id, trade_id)."""
        sig_id = _seed_signal(store)
        side = "BUY" if direction == "LONG" else "SELL"
        risk = abs(entry_fill - sl)
        trade_id = om.create_trade(
            signal_id=sig_id, symbol=symbol, direction=direction,
            strategy="gap_go_long", sector=None, qty=qty,
            entry_target_price=entry_fill, sl_initial=sl, tgt_initial=tgt,
            order_protocol=protocol,
            margin_reserved=entry_fill * qty * 0.20, risk_amount=risk * qty,
        )
        om.record_entry_fill(
            trade_id=trade_id, avg_fill_price=entry_fill,
            qty_filled=qty, filled_at=now_ist().isoformat(),
        )
        return sig_id, trade_id

    def _inject_exit(
        self,
        placer: OrderPlacer,
        *,
        internal_id: str,
        trade_id: str,
        leg: str,
        protocol: str,
        symbol: str = "RELIANCE",
        qty: int = 10,
        direction: str = "LONG",
    ) -> None:
        """Directly add an exit-leg _FillEntry to the _fill_map (bypass place())."""
        entry = _FillEntry(
            trade_id=trade_id, reservation_id="res_exit",
            symbol=symbol, qty=qty, leg=leg,
            order_protocol=protocol, direction=direction,
        )
        with placer._fill_map_lock:
            placer._fill_map[internal_id] = entry

    def _publish_fill(
        self, bus: EventBus, *, internal_id: str, price: float, qty: int,
        side: str, symbol: str,
    ) -> None:
        bus.publish(OrderFilled(
            source_module="test", payload={},
            internal_order_id=internal_id,
            broker_order_id=f"BRK_{internal_id}",
            symbol=symbol, side=side,
            avg_fill_price=price, filled_qty=qty,
            filled_at=now_ist().isoformat(),
        ))

    # ---- tests --------------------------------------------------------------

    def test_sl_fill_closes_trade_with_sl_hit(self) -> None:
        """SL leg fill -> close_trade(exit_reason='SL_HIT'); status CLOSED."""
        with TemporaryDirectory() as tmp:
            placer, _, store, bus, om, fm, _ = self._build(Path(tmp))
            _, trade_id = self._seed_open_trade(store, om)
            self._inject_exit(
                placer, internal_id="sl_x", trade_id=trade_id,
                leg=_LEG_SL, protocol="LIMIT_TRIPLE",
            )

            self._publish_fill(bus, internal_id="sl_x", price=2450.0,
                               qty=10, side="SELL", symbol="RELIANCE")

            row = om.get_trade(trade_id)
            assert row["status"] == "CLOSED"
            assert row["exit_reason"] == "SL_HIT"
            assert row["exit_price"] == 2450.0
            store.close()
            print("  OK BL-7d: SL fill -> status=CLOSED, exit_reason=SL_HIT")

    def test_tgt_fill_closes_trade_with_tgt_hit(self) -> None:
        """TGT leg fill -> close_trade(exit_reason='TGT_HIT')."""
        with TemporaryDirectory() as tmp:
            placer, _, store, bus, om, fm, _ = self._build(Path(tmp))
            _, trade_id = self._seed_open_trade(store, om)
            self._inject_exit(
                placer, internal_id="tgt_x", trade_id=trade_id,
                leg=_LEG_TGT, protocol="LIMIT_TRIPLE",
            )

            self._publish_fill(bus, internal_id="tgt_x", price=2600.0,
                               qty=10, side="SELL", symbol="RELIANCE")

            row = om.get_trade(trade_id)
            assert row["status"] == "CLOSED"
            assert row["exit_reason"] == "TGT_HIT"
            store.close()
            print("  OK BL-7d: TGT fill -> exit_reason=TGT_HIT")

    def test_eod_fill_closes_trade_with_eod_squareoff(self) -> None:
        """EOD leg fill -> close_trade(exit_reason='EOD_SQUAREOFF')."""
        with TemporaryDirectory() as tmp:
            placer, _, store, bus, om, fm, _ = self._build(Path(tmp))
            _, trade_id = self._seed_open_trade(store, om)
            self._inject_exit(
                placer, internal_id="eod_x", trade_id=trade_id,
                leg=_LEG_EOD, protocol="LIMIT_TRIPLE",
            )

            self._publish_fill(bus, internal_id="eod_x", price=2480.0,
                               qty=10, side="SELL", symbol="RELIANCE")

            row = om.get_trade(trade_id)
            assert row["exit_reason"] == "EOD_SQUAREOFF"
            store.close()
            print("  OK BL-7d: EOD fill -> exit_reason=EOD_SQUAREOFF")

    def test_long_tgt_fill_gross_pnl_direction_correct(self) -> None:
        """LONG TGT: gross_pnl = (exit-entry)*qty = (2600-2500)*10 = 1000."""
        with TemporaryDirectory() as tmp:
            placer, _, store, bus, om, fm, _ = self._build(Path(tmp))
            _, trade_id = self._seed_open_trade(store, om, direction="LONG")
            self._inject_exit(
                placer, internal_id="tgt_l", trade_id=trade_id,
                leg=_LEG_TGT, protocol="LIMIT_TRIPLE", direction="LONG",
            )

            self._publish_fill(bus, internal_id="tgt_l", price=2600.0,
                               qty=10, side="SELL", symbol="RELIANCE")

            row = om.get_trade(trade_id)
            assert row["gross_pnl"] == 1000.0, f"expected 1000, got {row['gross_pnl']}"
            assert row["charges"] == 25.0
            assert row["net_pnl"] == 975.0
            store.close()
            print("  OK BL-7d: LONG TGT gross_pnl=(exit-entry)*qty=1000, net=975")

    def test_short_tgt_fill_gross_pnl_direction_correct(self) -> None:
        """SHORT TGT: gross_pnl = (entry-exit)*qty = (2500-2400)*10 = 1000 (EF-3)."""
        with TemporaryDirectory() as tmp:
            placer, _, store, bus, om, fm, _ = self._build(Path(tmp))
            _, trade_id = self._seed_open_trade(
                store, om, direction="SHORT", entry_fill=2500.0, sl=2550.0, tgt=2400.0,
            )
            self._inject_exit(
                placer, internal_id="tgt_s", trade_id=trade_id,
                leg=_LEG_TGT, protocol="LIMIT_TRIPLE", direction="SHORT",
            )

            self._publish_fill(bus, internal_id="tgt_s", price=2400.0,
                               qty=10, side="BUY", symbol="RELIANCE")

            row = om.get_trade(trade_id)
            assert row["gross_pnl"] == 1000.0, (
                f"SHORT PnL bug (EF-3 regression): expected 1000, got {row['gross_pnl']}"
            )
            store.close()
            print("  OK BL-7d: SHORT TGT gross_pnl=(entry-exit)*qty=1000 (EF-3)")

    def test_cost_calc_called_with_round_trip_args(self) -> None:
        """cost_calculator.total_round_trip_cost receives qty/entry/exit/product."""
        with TemporaryDirectory() as tmp:
            placer, _, store, bus, om, fm, cost_calc = self._build(
                Path(tmp), protocol="LIMIT_TRIPLE",
            )
            _, trade_id = self._seed_open_trade(store, om)
            self._inject_exit(
                placer, internal_id="cc_x", trade_id=trade_id,
                leg=_LEG_SL, protocol="LIMIT_TRIPLE",
            )

            self._publish_fill(bus, internal_id="cc_x", price=2450.0,
                               qty=10, side="SELL", symbol="RELIANCE")

            cost_calc.total_round_trip_cost.assert_called_once()
            kwargs = cost_calc.total_round_trip_cost.call_args.kwargs
            assert kwargs["qty"] == 10
            assert kwargs["entry_price"] == 2500.0
            assert kwargs["exit_price"] == 2450.0
            assert kwargs["product"] == "MIS"  # LIMIT_TRIPLE -> MIS
            store.close()
            print("  OK BL-7d: cost_calc.total_round_trip_cost(qty, entry, exit, MIS)")

    def test_release_used_called_with_direction_and_intent(self) -> None:
        """fm.release_used gets direction=LONG|SHORT and intent derived from product."""
        with TemporaryDirectory() as tmp:
            placer, _, store, bus, om, fm, _ = self._build(
                Path(tmp), protocol="CO_PLUS_TGT",
            )
            _, trade_id = self._seed_open_trade(
                store, om, direction="LONG", protocol="CO_PLUS_TGT",
            )
            self._inject_exit(
                placer, internal_id="ru_x", trade_id=trade_id,
                leg=_LEG_TGT, protocol="CO_PLUS_TGT", direction="LONG",
            )

            self._publish_fill(bus, internal_id="ru_x", price=2600.0,
                               qty=10, side="SELL", symbol="RELIANCE")

            assert len(fm.released_used) == 1
            call_kwargs = fm.released_used[0]
            assert call_kwargs["symbol"] == "RELIANCE"
            assert call_kwargs["direction"] == "LONG"
            assert call_kwargs["intent"] == "COVER_ORDER"  # CO_PLUS_TGT -> CO -> COVER_ORDER
            assert call_kwargs["entry_price"] == 2500.0
            assert call_kwargs["exit_price"] == 2600.0
            assert call_kwargs["costs"] == 25.0
            store.close()
            print("  OK BL-7d: fm.release_used(direction=LONG, intent=COVER_ORDER)")

    def test_position_closed_published_with_net_pnl(self) -> None:
        """bus publishes PositionClosed(realized_pnl=NET) after close_trade."""
        from core.events import PositionClosed
        with TemporaryDirectory() as tmp:
            placer, _, store, bus, om, fm, _ = self._build(Path(tmp))
            sig_id, trade_id = self._seed_open_trade(store, om, direction="LONG")
            self._inject_exit(
                placer, internal_id="pc_x", trade_id=trade_id,
                leg=_LEG_TGT, protocol="LIMIT_TRIPLE", direction="LONG",
            )
            captured: List[PositionClosed] = []
            bus.subscribe(PositionClosed, lambda e: captured.append(e))

            self._publish_fill(bus, internal_id="pc_x", price=2600.0,
                               qty=10, side="SELL", symbol="RELIANCE")

            assert len(captured) == 1
            ev = captured[0]
            assert ev.trade_id == trade_id
            assert ev.signal_id == sig_id
            assert ev.symbol == "RELIANCE"
            assert ev.exit_price == 2600.0
            assert ev.realized_pnl == 975.0  # 1000 gross - 25 charges
            store.close()
            print("  OK BL-7d: PositionClosed published with net realized_pnl=975")

    def test_smart_tgt_unregister_called_for_co_plus_tgt(self) -> None:
        """CO_PLUS_TGT exit fill -> smart_tgt.unregister_trade(trade_id)."""
        from orders.smart_tgt_manager import SmartTgtManager
        mgr = MagicMock(spec=SmartTgtManager)
        with TemporaryDirectory() as tmp:
            placer, _, store, bus, om, fm, _ = self._build(
                Path(tmp), protocol="CO_PLUS_TGT", smart_tgt_manager=mgr,
            )
            _, trade_id = self._seed_open_trade(
                store, om, direction="LONG", protocol="CO_PLUS_TGT",
            )
            self._inject_exit(
                placer, internal_id="st_x", trade_id=trade_id,
                leg=_LEG_TGT, protocol="CO_PLUS_TGT", direction="LONG",
            )

            self._publish_fill(bus, internal_id="st_x", price=2600.0,
                               qty=10, side="SELL", symbol="RELIANCE")

            mgr.unregister_trade.assert_called_once_with(trade_id)
            store.close()
            print("  OK BL-7d: CO_PLUS_TGT exit -> smart_tgt.unregister_trade called")

    def test_smart_tgt_unregister_skipped_for_limit_triple(self) -> None:
        """LIMIT_TRIPLE exit -> unregister_trade NOT called (static SL, no registration)."""
        from orders.smart_tgt_manager import SmartTgtManager
        mgr = MagicMock(spec=SmartTgtManager)
        with TemporaryDirectory() as tmp:
            placer, _, store, bus, om, fm, _ = self._build(
                Path(tmp), protocol="LIMIT_TRIPLE", smart_tgt_manager=mgr,
            )
            _, trade_id = self._seed_open_trade(store, om)
            self._inject_exit(
                placer, internal_id="lt_x", trade_id=trade_id,
                leg=_LEG_TGT, protocol="LIMIT_TRIPLE",
            )

            self._publish_fill(bus, internal_id="lt_x", price=2600.0,
                               qty=10, side="SELL", symbol="RELIANCE")

            mgr.unregister_trade.assert_not_called()
            store.close()
            print("  OK BL-7d: LIMIT_TRIPLE exit -> unregister_trade NOT called")

    def test_exit_fill_pops_fill_map_entry(self) -> None:
        """After exit-fill dispatch, the _fill_map entry is removed."""
        with TemporaryDirectory() as tmp:
            placer, _, store, bus, om, fm, _ = self._build(Path(tmp))
            _, trade_id = self._seed_open_trade(store, om)
            self._inject_exit(
                placer, internal_id="pop_x", trade_id=trade_id,
                leg=_LEG_SL, protocol="LIMIT_TRIPLE",
            )
            with placer._fill_map_lock:
                assert "pop_x" in placer._fill_map  # precondition

            self._publish_fill(bus, internal_id="pop_x", price=2450.0,
                               qty=10, side="SELL", symbol="RELIANCE")

            with placer._fill_map_lock:
                assert "pop_x" not in placer._fill_map
            store.close()
            print("  OK BL-7d: exit-fill pops _fill_map entry")

    def test_double_close_is_warning_and_skip(self) -> None:
        """Re-firing an exit fill on an already-CLOSED trade: no release, no publish."""
        from core.events import PositionClosed
        with TemporaryDirectory() as tmp:
            placer, _, store, bus, om, fm, _ = self._build(Path(tmp))
            _, trade_id = self._seed_open_trade(store, om)

            # First close
            self._inject_exit(
                placer, internal_id="d1", trade_id=trade_id,
                leg=_LEG_SL, protocol="LIMIT_TRIPLE",
            )
            self._publish_fill(bus, internal_id="d1", price=2450.0,
                               qty=10, side="SELL", symbol="RELIANCE")
            assert om.get_trade(trade_id)["status"] == "CLOSED"
            assert len(fm.released_used) == 1  # first close released

            # Second close attempt on already-CLOSED trade
            captured: List[PositionClosed] = []
            bus.subscribe(PositionClosed, lambda e: captured.append(e))
            self._inject_exit(
                placer, internal_id="d2", trade_id=trade_id,
                leg=_LEG_TGT, protocol="LIMIT_TRIPLE",
            )
            self._publish_fill(bus, internal_id="d2", price=2600.0,
                               qty=10, side="SELL", symbol="RELIANCE")

            # release_used NOT called again; no PositionClosed emitted
            assert len(fm.released_used) == 1, "double-close must NOT call release_used"
            assert captured == [], "double-close must NOT publish PositionClosed"
            store.close()
            print("  OK BL-7d: double-close -> no release, no publish (WARN + skip)")


# ─────────────────────────────────────────────────────────────────────────────
# BL-8 / Phase C.2 — atomic persist + broker-order cleanup
# ─────────────────────────────────────────────────────────────────────────────

class _AdapterCancelFails:
    """Adapter mock whose cancel_order returns success=False (or raises)."""

    def __init__(self, raise_on_cancel: bool = False) -> None:
        from broker.zerodha_adapter import CancelResult  # noqa: F401
        self._raise = raise_on_cancel
        self._call_count = 0
        self.placed: List[dict] = []
        self.cancelled: List[str] = []

    def place_order(self, symbol, side, qty, price, order_type, intent,
                    tag=None, trigger_price=0.0, variety="regular"):
        self._call_count += 1
        po = _placed_order(symbol=symbol, side=side)
        self.placed.append({
            "symbol": symbol, "side": side,
            "broker_order_id": po.broker_order_id,
            "internal_order_id": po.internal_order_id,
        })
        return po

    def cancel_order(self, broker_order_id: str):
        from broker.zerodha_adapter import CancelResult
        self.cancelled.append(broker_order_id)
        if self._raise:
            raise RuntimeError("simulated cancel transport failure")
        return CancelResult(
            broker_order_id=broker_order_id,
            success=False,
            reason="simulated broker cancel rejection",
        )


class _RecordingKillSwitch:
    """KillSwitch mock that records hard_kill calls."""

    def __init__(self) -> None:
        self._active = False
        self.hard_kill_calls: List[dict] = []

    def is_active(self, intent: str = "entry") -> bool:
        return self._active

    def hard_kill(self, *, reason: str, triggered_by: str) -> None:
        self.hard_kill_calls.append(
            {"reason": reason, "triggered_by": triggered_by}
        )


class TestBl8AtomicPersist:
    """
    BL-8 / Phase C.2: atomic _persist_entry_orders + cancel-on-failure +
    hard_kill on DB-persist-after-broker-success.
    """

    def _make_placer(
        self,
        tmp_path,
        adapter=None,
        kill_switch=None,
        default_protocol="LIMIT_TRIPLE",
    ):
        store = _make_store(tmp_path)
        if adapter is None:
            adapter = _MockAdapter()
        co_proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
        limit_proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        engine = FullEntryEngine(
            co_protocol=co_proto, limit_protocol=limit_proto,
            logger=_log(), default_protocol=default_protocol,
        )
        om = OrderManager(store, _log())
        fm = _MockFundManager()
        bus = EventBus()
        placer = OrderPlacer(
            entry_engine=engine,
            order_manager=om,
            fund_manager=fm,
            bus=bus,
            logger=_log(),
            order_monitor=MagicMock(spec=OrderMonitor),
            cost_calculator=MagicMock(spec=CostCalculator),
            rr_ratio=2.0,
            default_order_protocol=default_protocol,
            kill_switch=kill_switch,
        )
        return placer, store, fm, bus, adapter, om

    # --- Test 1 -----------------------------------------------------------

    def test_happy_path_no_cancellation_no_hard_kill(self) -> None:
        """BL-8: happy path persists 3 rows; no cancels; no hard_kill."""
        with TemporaryDirectory() as tmp:
            ks = _RecordingKillSwitch()
            placer, store, fm, bus, adapter, om = self._make_placer(
                Path(tmp), kill_switch=ks,
            )
            sig_id = _seed_signal(store)

            placer.place(
                symbol="RELIANCE", side="BUY", qty=10,
                entry_price=2500.0, sl_price=2450.0,
                intent="INTRADAY", signal_id=sig_id,
                reservation_id="res_happy",
            )

            assert len(adapter.placed) == 3
            assert adapter.cancelled == []
            assert ks.hard_kill_calls == []

            rows = store.fetch_all("SELECT * FROM orders WHERE trade_id IN "
                                    "(SELECT trade_id FROM trades WHERE signal_id = ?)",
                                    (sig_id,))
            assert len(rows) == 3
            store.close()
            print("  OK BL-8: happy path -> 3 rows, no cancel, no hard_kill")

    # --- Test 2 -----------------------------------------------------------

    def test_persist_atomic_all_or_nothing(self) -> None:
        """OMgr11: insert_orders_atomic rolls back if any row fails."""
        from orders.order_manager import OrderInsertSpec
        with TemporaryDirectory() as tmp:
            store = _make_store(Path(tmp))
            sig_id = _seed_signal(store)
            om = OrderManager(store, _log())
            trade_id = om.create_trade(
                signal_id=sig_id, symbol="ZZZ", direction="LONG",
                strategy="x", sector=None, qty=1,
                entry_target_price=10.0, sl_initial=9.0, tgt_initial=12.0,
                order_protocol="LIMIT_TRIPLE", margin_reserved=10.0,
                risk_amount=1.0,
            )

            specs = [
                OrderInsertSpec(
                    broker_order_id="OK_ENTRY", leg="ENTRY",
                    transaction_type="BUY", order_type="LIMIT",
                    product="MIS", variety="regular", qty_requested=1,
                ),
                OrderInsertSpec(
                    broker_order_id="OK_ENTRY",  # PK collision -> 2nd INSERT fails
                    leg="SL",
                    transaction_type="SELL", order_type="SL-M",
                    product="MIS", variety="regular", qty_requested=1,
                ),
            ]

            with pytest.raises(Exception):
                om.insert_orders_atomic(trade_id, specs)

            rows = store.fetch_all(
                "SELECT * FROM orders WHERE trade_id = ?", (trade_id,)
            )
            assert len(rows) == 0, (
                "atomic batch must roll back: even the first INSERT must NOT "
                "be visible if a later one failed"
            )
            store.close()
            print("  OK BL-8: insert_orders_atomic rolls back all on partial failure")

    # --- Test 3 -----------------------------------------------------------

    def test_db_persist_failure_after_broker_success_cancels_all(self) -> None:
        """BL-8: DB persist failure after broker success -> all 3 cancelled."""
        with TemporaryDirectory() as tmp:
            ks = _RecordingKillSwitch()
            placer, store, fm, bus, adapter, om = self._make_placer(
                Path(tmp), kill_switch=ks,
            )
            sig_id = _seed_signal(store)

            cancelled_ids: List[str] = []

            def _boom(trade_id, specs):
                # Capture broker IDs the placer would have cancelled
                for s in specs:
                    cancelled_ids.append(s.broker_order_id)
                raise RuntimeError("simulated DB write failure")

            om.insert_orders_atomic = _boom  # type: ignore[assignment]

            with pytest.raises(RuntimeError, match="simulated DB write failure"):
                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=2500.0, sl_price=2450.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_persist_fail",
                )

            # All 3 broker-placed IDs cancelled by OrderPlacer
            assert sorted(adapter.cancelled) == sorted(cancelled_ids), (
                f"expected adapter.cancelled to match the 3 placed IDs; "
                f"got cancelled={adapter.cancelled} vs placed={cancelled_ids}"
            )
            assert "res_persist_fail" in fm.released
            store.close()
            print("  OK BL-8: DB-persist failure -> all broker orders cancelled")

    # --- Test 4 -----------------------------------------------------------

    def test_db_persist_failure_after_broker_success_fires_hard_kill(self) -> None:
        """BL-8: DB persist failure after broker success -> hard_kill fires."""
        with TemporaryDirectory() as tmp:
            ks = _RecordingKillSwitch()
            placer, store, fm, bus, adapter, om = self._make_placer(
                Path(tmp), kill_switch=ks,
            )
            sig_id = _seed_signal(store)

            def _boom(trade_id, specs):
                raise RuntimeError("DB unavailable")
            om.insert_orders_atomic = _boom  # type: ignore[assignment]

            with pytest.raises(RuntimeError, match="DB unavailable"):
                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=2500.0, sl_price=2450.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_hk",
                )

            assert len(ks.hard_kill_calls) >= 1, (
                "DB-persist-after-broker-success MUST fire kill_switch.hard_kill"
            )
            call0 = ks.hard_kill_calls[0]
            assert call0["triggered_by"] == "order_placer.place"
            assert "persist_entry_orders failed after broker success" in call0["reason"]
            store.close()
            print("  OK BL-8: DB-persist failure -> hard_kill fires")

    # --- Test 5 -----------------------------------------------------------

    def test_cancel_failure_during_cleanup_critical_logged_grep_tag(self) -> None:
        """BL-8: cancel_order returning success=False -> CRITICAL log w/ grep tag."""
        import io
        with TemporaryDirectory() as tmp:
            adapter = _AdapterCancelFails(raise_on_cancel=False)
            ks = _RecordingKillSwitch()
            placer, store, fm, bus, _adapter, om = self._make_placer(
                Path(tmp), adapter=adapter, kill_switch=ks,
            )
            sig_id = _seed_signal(store)

            # Capture CRITICAL logs
            log_buf = io.StringIO()
            handler = logging.StreamHandler(log_buf)
            handler.setLevel(logging.CRITICAL)
            handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
            target_logger = logging.getLogger("test_order_placer")
            target_logger.addHandler(handler)
            target_logger.setLevel(logging.CRITICAL)

            try:
                def _boom(trade_id, specs):
                    raise RuntimeError("DB write failed")
                om.insert_orders_atomic = _boom  # type: ignore[assignment]

                with pytest.raises(RuntimeError):
                    placer.place(
                        symbol="RELIANCE", side="BUY", qty=10,
                        entry_price=2500.0, sl_price=2450.0,
                        intent="INTRADAY", signal_id=sig_id,
                        reservation_id="res_cancel_fail",
                    )

                logs = log_buf.getvalue()
                assert "CANCEL_FAILED_MANUAL_INTERVENTION_REQUIRED" in logs, (
                    f"missing grep-friendly CRITICAL tag in logs:\n{logs}"
                )
                # Cleanup should NOT abort: all 3 placed IDs attempted
                assert len(adapter.cancelled) == 3
            finally:
                target_logger.removeHandler(handler)
            store.close()
            print("  OK BL-8: cancel-rejected -> CRITICAL grep tag, cleanup continues")

    # --- Test 6 -----------------------------------------------------------

    def test_co_plus_tgt_soft_fail_cancels_co(self) -> None:
        """OP-BL8f: CoPlusTgt soft-fail (CO live, TGT dead) cancels the CO."""
        with TemporaryDirectory() as tmp:
            # adapter that succeeds on CO place and FAILS on TGT place
            class _COSuccessTGTFail:
                def __init__(self):
                    self._n = 0
                    self.placed: List[dict] = []
                    self.cancelled: List[str] = []

                def place_order(self, symbol, side, qty, price, order_type,
                                intent, tag=None, trigger_price=0.0,
                                variety="regular"):
                    self._n += 1
                    if self._n == 1:
                        po = _placed_order(symbol=symbol, side=side,
                                            broker_id="CO_LIVE_123")
                        self.placed.append({"role": "CO",
                                             "broker_order_id": po.broker_order_id})
                        return po
                    raise BrokerError("simulated TGT broker rejection")

                def cancel_order(self, broker_order_id: str):
                    from broker.zerodha_adapter import CancelResult
                    self.cancelled.append(broker_order_id)
                    return CancelResult(
                        broker_order_id=broker_order_id,
                        success=True, reason="",
                    )

            adapter = _COSuccessTGTFail()
            ks = _RecordingKillSwitch()
            placer, store, fm, bus, _adapter, om = self._make_placer(
                Path(tmp), adapter=adapter, kill_switch=ks,
                default_protocol="CO_PLUS_TGT",
            )
            sig_id = _seed_signal(store)

            with pytest.raises(BrokerError):
                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=2500.0, sl_price=2450.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_co_soft",
                )

            assert "CO_LIVE_123" in adapter.cancelled, (
                "CoPlusTgt soft-fail MUST cancel the live CO via OrderPlacer; "
                f"got cancelled={adapter.cancelled}"
            )
            assert "res_co_soft" in fm.released
            # Soft-fail is NOT a DB-persist-after-broker-success path; no hard_kill
            assert ks.hard_kill_calls == [], (
                "soft-fail w/ successful cleanup must NOT fire hard_kill"
            )
            store.close()
            print("  OK BL-8: CoPlusTgt soft-fail cancels CO; no hard_kill")

    # --- Test 7 -----------------------------------------------------------

    def test_limit_triple_sl_fail_protocol_cleanup_not_double_cancelled(self) -> None:
        """BL-8: LimitTriple SL-fail protocol cancels ENTRY internally;
        OrderPlacer must NOT double-cancel (BrokerError path passes no IDs)."""
        with TemporaryDirectory() as tmp:
            class _SLFailAdapter:
                def __init__(self):
                    self._n = 0
                    self.placed: List[dict] = []
                    self.cancelled: List[str] = []

                def place_order(self, symbol, side, qty, price, order_type,
                                intent, tag=None, trigger_price=0.0,
                                variety="regular"):
                    self._n += 1
                    if self._n == 1:  # ENTRY succeeds
                        po = _placed_order(symbol=symbol, side=side,
                                            broker_id="ENTRY_LIVE_X")
                        self.placed.append({"role": "ENTRY",
                                             "broker_order_id": po.broker_order_id})
                        return po
                    raise BrokerError("simulated SL broker rejection")

                def cancel_order(self, broker_order_id: str):
                    from broker.zerodha_adapter import CancelResult
                    self.cancelled.append(broker_order_id)
                    return CancelResult(
                        broker_order_id=broker_order_id,
                        success=True, reason="",
                    )

            adapter = _SLFailAdapter()
            ks = _RecordingKillSwitch()
            placer, store, fm, bus, _adapter, om = self._make_placer(
                Path(tmp), adapter=adapter, kill_switch=ks,
                default_protocol="LIMIT_TRIPLE",
            )
            sig_id = _seed_signal(store)

            with pytest.raises(BrokerError):
                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=2500.0, sl_price=2450.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_sl_fail",
                )

            # Protocol cancelled ENTRY (1 call). OrderPlacer must NOT add a 2nd.
            assert adapter.cancelled == ["ENTRY_LIVE_X"], (
                "BrokerError path must pass NO broker_order_ids -- protocol "
                "already cancelled ENTRY internally; OrderPlacer must not "
                "double-cancel. Got: " + repr(adapter.cancelled)
            )
            assert "res_sl_fail" in fm.released
            assert ks.hard_kill_calls == [], "BrokerError path must NOT hard_kill"
            store.close()
            print("  OK BL-8: LimitTriple SL-fail -> single cancel by protocol, "
                  "no double-cancel")

    # --- Test 8 -----------------------------------------------------------

    def test_cancel_order_returning_false_does_not_abort_cleanup(self) -> None:
        """BL-8: cancel rejection on order #1 must not stop cancel of orders #2,#3."""
        with TemporaryDirectory() as tmp:
            adapter = _AdapterCancelFails(raise_on_cancel=False)
            ks = _RecordingKillSwitch()
            placer, store, fm, bus, _adapter, om = self._make_placer(
                Path(tmp), adapter=adapter, kill_switch=ks,
            )
            sig_id = _seed_signal(store)

            def _boom(trade_id, specs):
                raise RuntimeError("DB down")
            om.insert_orders_atomic = _boom  # type: ignore[assignment]

            with pytest.raises(RuntimeError):
                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=2500.0, sl_price=2450.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_cancel_continue",
                )

            assert len(adapter.cancelled) == 3, (
                "cancel rejection on one order must NOT abort cancel for "
                "the remaining orders. Got: " + repr(adapter.cancelled)
            )
            assert "res_cancel_continue" in fm.released  # release still runs
            store.close()
            print("  OK BL-8: cancel rejection does not abort remaining cleanup")

    # --- Test 9 -----------------------------------------------------------

    def test_protocol_reject_only_does_not_fire_hard_kill(self) -> None:
        """BL-8 scope boundary: protocol rejects entry cleanly -> no hard_kill.
        Capital tracking is intact (no broker orders live)."""
        with TemporaryDirectory() as tmp:
            class _EntryRejectAdapter:
                def __init__(self):
                    self.placed: List[dict] = []
                    self.cancelled: List[str] = []

                def place_order(self, *a, **k):
                    raise BrokerError("entry cleanly rejected")

                def cancel_order(self, broker_order_id: str):
                    from broker.zerodha_adapter import CancelResult
                    self.cancelled.append(broker_order_id)
                    return CancelResult(
                        broker_order_id=broker_order_id,
                        success=True, reason="",
                    )

            adapter = _EntryRejectAdapter()
            ks = _RecordingKillSwitch()
            placer, store, fm, bus, _adapter, om = self._make_placer(
                Path(tmp), adapter=adapter, kill_switch=ks,
            )
            sig_id = _seed_signal(store)

            with pytest.raises(BrokerError):
                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=2500.0, sl_price=2450.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_protocol_only",
                )

            # Scope boundary: protocol-only failure must NOT fire hard_kill
            assert ks.hard_kill_calls == [], (
                "BL-8 scope boundary VIOLATED: protocol-only failure (no broker "
                "orders accepted) MUST NOT fire kill_switch.hard_kill. "
                f"Got: {ks.hard_kill_calls}"
            )
            assert adapter.cancelled == [], (
                "no broker orders were placed -> nothing to cancel"
            )
            assert "res_protocol_only" in fm.released
            store.close()
            print("  OK BL-8: scope boundary -- protocol-only fail does NOT hard_kill")


# ─────────────────────────────────────────────────────────────────────────────
# EF-2 / Phase E.6 — track-failure cleanup symmetric to BL-8 persist-failure
# ─────────────────────────────────────────────────────────────────────────────

class TestEf2TrackFailureCleanup:
    """
    OP-EF2a-d: if order_monitor.track() raises after _persist_entry_orders
    succeeds, OrderPlacer.place() must:
      1. Pop any _fill_map entries for this trade that were added pre-raise.
      2. untrack() every successfully-tracked leg (idempotent).
      3. Emit CRITICAL log with grep tag EF2_TRACK_FAILURE_CLEANUP.
      4. Delegate to _handle_placement_failure for broker cancel + trade
         FAILED + reservation release.
      5. NOT fire hard_kill (capital tracking stays consistent -- protocol
         -only failure class per OP-BL8e).
      6. Propagate the original exception to signal_processor.
    """

    def _make_placer(
        self,
        tmp_path: Path,
        *,
        track_side_effect,
        kill_switch=None,
        default_protocol: str = "LIMIT_TRIPLE",
    ):
        """
        Build a placer with a MagicMock OrderMonitor whose .track() has the
        given side_effect and a real _MockAdapter whose cancel_order succeeds.
        Returns (placer, store, fm, bus, adapter, om, monitor).
        """
        store = _make_store(tmp_path)
        adapter = _MockAdapter()
        co_proto = CoPlusTgtProtocol(adapter=adapter, logger=_log())
        limit_proto = LimitTripleProtocol(adapter=adapter, logger=_log())
        engine = FullEntryEngine(
            co_protocol=co_proto, limit_protocol=limit_proto,
            logger=_log(), default_protocol=default_protocol,
        )
        om = OrderManager(store, _log())
        fm = _MockFundManager()
        bus = EventBus()
        monitor = MagicMock(spec=OrderMonitor)
        monitor.track.side_effect = track_side_effect
        placer = OrderPlacer(
            entry_engine=engine,
            order_manager=om,
            fund_manager=fm,
            bus=bus,
            logger=_log(),
            order_monitor=monitor,
            cost_calculator=MagicMock(spec=CostCalculator),
            rr_ratio=2.0,
            default_order_protocol=default_protocol,
            kill_switch=kill_switch,
        )
        return placer, store, fm, bus, adapter, om, monitor

    # --- Test 1 -----------------------------------------------------------

    def test_ef2_track_raises_on_entry_leg_full_cleanup(self) -> None:
        """
        EF-2: track() raises on FIRST leg (ENTRY). Nothing was successfully
        tracked. Verify cleanup handles empty successfully_tracked list.
        """
        import io
        with TemporaryDirectory() as tmp:
            ks = _RecordingKillSwitch()
            placer, store, fm, bus, adapter, om, monitor = self._make_placer(
                Path(tmp),
                track_side_effect=ValueError("boom: duplicate internal_id"),
                kill_switch=ks,
            )
            sig_id = _seed_signal(store)

            # Capture CRITICAL logs for the grep tag assertion
            log_buf = io.StringIO()
            handler = logging.StreamHandler(log_buf)
            handler.setLevel(logging.CRITICAL)
            handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
            target_logger = logging.getLogger("test_order_placer")
            target_logger.addHandler(handler)
            target_logger.setLevel(logging.CRITICAL)

            try:
                with pytest.raises(ValueError, match="boom: duplicate internal_id"):
                    placer.place(
                        symbol="RELIANCE", side="BUY", qty=10,
                        entry_price=2500.0, sl_price=2450.0,
                        intent="INTRADAY", signal_id=sig_id,
                        reservation_id="res_ef2_entry_fail",
                    )

                # No _fill_map entries for this trade
                with placer._fill_map_lock:
                    assert placer._fill_map == {}, (
                        f"expected empty _fill_map; got {placer._fill_map!r}"
                    )

                # untrack NOT called (nothing to untrack)
                assert monitor.untrack.call_count == 0, (
                    f"expected 0 untrack calls; got {monitor.untrack.call_count}"
                )

                # adapter.cancel_order called for ALL 3 broker IDs placed
                placed_broker_ids = sorted(p["broker_order_id"] for p in adapter.placed)
                assert sorted(adapter.cancelled) == placed_broker_ids, (
                    f"expected cancel for all 3 placed IDs; "
                    f"placed={placed_broker_ids}; cancelled={adapter.cancelled}"
                )

                # Trade FAILED, reservation released
                rows = store.fetch_all(
                    "SELECT status FROM trades WHERE signal_id = ?", (sig_id,)
                )
                assert len(rows) == 1
                assert rows[0]["status"] == "FAILED"
                assert "res_ef2_entry_fail" in fm.released

                # CRITICAL grep tag present
                logs = log_buf.getvalue()
                assert "EF2_TRACK_FAILURE_CLEANUP" in logs, (
                    f"missing EF2 grep tag in CRITICAL logs:\n{logs}"
                )

                # hard_kill NOT called
                assert ks.hard_kill_calls == [], (
                    f"EF-2 MUST NOT fire hard_kill (protocol-only failure class); "
                    f"got {ks.hard_kill_calls}"
                )
            finally:
                target_logger.removeHandler(handler)
                store.close()
            print("  OK EF-2: entry-leg track raise -> full cleanup, no hard_kill")

    # --- Test 2 -----------------------------------------------------------

    def test_ef2_track_raises_mid_loop_partial_cleanup(self) -> None:
        """
        EF-2: track() succeeds on ENTRY, raises on SL (partial population).
        Verify ENTRY's _fill_map entry is removed and untrack called for ENTRY.
        """
        import io
        with TemporaryDirectory() as tmp:
            ks = _RecordingKillSwitch()
            # ENTRY succeeds, SL raises, TGT never called
            placer, store, fm, bus, adapter, om, monitor = self._make_placer(
                Path(tmp),
                track_side_effect=[None, ValueError("boom on SL")],
                kill_switch=ks,
            )
            sig_id = _seed_signal(store)

            log_buf = io.StringIO()
            handler = logging.StreamHandler(log_buf)
            handler.setLevel(logging.CRITICAL)
            handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
            target_logger = logging.getLogger("test_order_placer")
            target_logger.addHandler(handler)
            target_logger.setLevel(logging.CRITICAL)

            try:
                with pytest.raises(ValueError, match="boom on SL"):
                    placer.place(
                        symbol="RELIANCE", side="BUY", qty=10,
                        entry_price=2500.0, sl_price=2450.0,
                        intent="INTRADAY", signal_id=sig_id,
                        reservation_id="res_ef2_sl_fail",
                    )

                # _fill_map empty: ENTRY entry was popped during cleanup
                with placer._fill_map_lock:
                    assert placer._fill_map == {}, (
                        f"ENTRY _fill_map entry must be popped during cleanup; "
                        f"got {placer._fill_map!r}"
                    )

                # track() called 2x (ENTRY ok, SL raises). TGT not reached.
                assert monitor.track.call_count == 2, (
                    f"expected 2 track calls (ENTRY ok + SL raise); "
                    f"got {monitor.track.call_count}"
                )

                # untrack called exactly once (for ENTRY).
                # NOTE: SL's track() raised before the _fill_map write, so SL
                # was never "successfully_tracked" -- no untrack for SL.
                assert monitor.untrack.call_count == 1, (
                    f"expected 1 untrack call (ENTRY only); "
                    f"got {monitor.untrack.call_count}"
                )
                # And it was for the ENTRY internal_id
                entry_iid = adapter.placed[0]["internal_order_id"]
                monitor.untrack.assert_called_once_with(entry_iid)

                # adapter.cancel_order called for ALL 3 placed broker IDs
                placed_broker_ids = sorted(p["broker_order_id"] for p in adapter.placed)
                assert sorted(adapter.cancelled) == placed_broker_ids, (
                    f"expected cancel for all 3 placed IDs; "
                    f"placed={placed_broker_ids}; cancelled={adapter.cancelled}"
                )

                # Trade FAILED, reservation released
                rows = store.fetch_all(
                    "SELECT status FROM trades WHERE signal_id = ?", (sig_id,)
                )
                assert rows[0]["status"] == "FAILED"
                assert "res_ef2_sl_fail" in fm.released

                # CRITICAL grep tag; legs_successfully_tracked field populated
                logs = log_buf.getvalue()
                assert "EF2_TRACK_FAILURE_CLEANUP" in logs

                # hard_kill NOT called
                assert ks.hard_kill_calls == [], (
                    f"EF-2 MUST NOT fire hard_kill; got {ks.hard_kill_calls}"
                )
            finally:
                target_logger.removeHandler(handler)
                store.close()
            print("  OK EF-2: partial-tracked cleanup -> untrack(ENTRY) only, no hard_kill")

    # --- Test 3 -----------------------------------------------------------

    def test_ef2_track_success_unchanged_behavior(self) -> None:
        """
        EF-2 regression guard: happy path unchanged. All 3 legs tracked,
        no cleanup, no cancel_order, no CRITICAL log, no exception.
        """
        import io
        with TemporaryDirectory() as tmp:
            ks = _RecordingKillSwitch()
            # track() returns None for every call -- normal success
            placer, store, fm, bus, adapter, om, monitor = self._make_placer(
                Path(tmp),
                track_side_effect=None,
                kill_switch=ks,
            )
            sig_id = _seed_signal(store)

            log_buf = io.StringIO()
            handler = logging.StreamHandler(log_buf)
            handler.setLevel(logging.CRITICAL)
            handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
            target_logger = logging.getLogger("test_order_placer")
            target_logger.addHandler(handler)
            target_logger.setLevel(logging.CRITICAL)

            try:
                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=2500.0, sl_price=2450.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_ef2_happy",
                )

                # All 3 legs in _fill_map
                with placer._fill_map_lock:
                    legs = sorted(e.leg for e in placer._fill_map.values())
                assert legs == [_LEG_ENTRY, _LEG_SL, _LEG_TGT], (
                    f"expected ENTRY/SL/TGT in _fill_map; got {legs}"
                )

                # 3 track calls, 0 untrack, 0 cancel_order
                assert monitor.track.call_count == 3
                assert monitor.untrack.call_count == 0
                assert adapter.cancelled == []

                # Trade in PENDING_FILL (not FAILED)
                rows = store.fetch_all(
                    "SELECT status FROM trades WHERE signal_id = ?", (sig_id,)
                )
                assert rows[0]["status"] == "PENDING_FILL"
                assert fm.released == []

                # No CRITICAL grep tag (no EF-2 cleanup ran)
                logs = log_buf.getvalue()
                assert "EF2_TRACK_FAILURE_CLEANUP" not in logs, (
                    f"happy path must NOT emit EF-2 grep tag; logs:\n{logs}"
                )

                # hard_kill NOT called
                assert ks.hard_kill_calls == []
            finally:
                target_logger.removeHandler(handler)
                store.close()
            print("  OK EF-2: happy path regression -- no cleanup, no grep tag")


# ─────────────────────────────────────────────────────────────────────────────
# BL-19: OrderPlacer retry loop scoped to BrokerRateLimit429Error (Phase D.1)
# ─────────────────────────────────────────────────────────────────────────────

class TestBl19PlacerRateLimitRetry:
    """
    BL-19: placer.place() retries ONLY BrokerRateLimit429Error up to
    rate_limit_backoff.max_placer_retries. No caller-side sleep -- the
    adapter's rate_limiter.penalize() freeze is the backoff pacing, which
    the next iteration's acquire() call respects. Non-429 BrokerError
    subclasses still get a single attempt (ZA11 / OP7).
    """

    def _make_placer_with_mock_engine(
        self,
        tmp_path: Path,
        engine_side_effect,
        max_placer_retries: int = 3,
    ):
        """Build a placer with a mocked entry engine whose .execute has the
        given side_effect. Returns (placer, store, fm, bus, om, engine_mock)."""
        from core.config_loader import RateLimitBackoffConfig
        store = _make_store(tmp_path)
        engine_mock = MagicMock(spec=FullEntryEngine)
        engine_mock.execute.side_effect = engine_side_effect
        om = OrderManager(store, _log())
        fm = _MockFundManager()
        bus = EventBus()
        placer = OrderPlacer(
            entry_engine=engine_mock,
            order_manager=om,
            fund_manager=fm,
            bus=bus,
            logger=_log(),
            order_monitor=MagicMock(spec=OrderMonitor),
            cost_calculator=MagicMock(spec=CostCalculator),
            rr_ratio=2.0,
            default_order_protocol="LIMIT_TRIPLE",
            rate_limit_backoff=RateLimitBackoffConfig(
                max_placer_retries=max_placer_retries,
                initial_delay_sec=0.01,   # tiny so the test stays fast even
                max_delay_sec=0.05,       # if any real penalize path fires
                jitter_sec=0.0,
            ),
        )
        return placer, store, fm, bus, om, engine_mock

    def _ok_entry_result(self) -> EntryResult:
        """Build a minimal successful EntryResult."""
        return EntryResult(
            success=True,
            order_protocol="LIMIT_TRIPLE",
            entry_internal_id="oid_entry_ok",
            entry_broker_order_id="BROKER_E_1",
            sl_internal_id="oid_sl_ok",
            sl_broker_order_id="BROKER_S_1",
            tgt_internal_id="oid_tgt_ok",
            tgt_broker_order_id="BROKER_T_1",
            rejection_reason="",
        )

    def test_placer_retries_on_429_up_to_max(self) -> None:
        """429 raised twice then success -> engine.execute called 3 times, trade OPEN."""
        import gc
        with TemporaryDirectory() as tmp:
            store = None
            try:
                side_effects = [
                    BrokerRateLimit429Error(
                        "broker 429 on place_order",
                        operation="place_order", category="order",
                        delay_sec=0.01, attempt=1,
                    ),
                    BrokerRateLimit429Error(
                        "broker 429 on place_order",
                        operation="place_order", category="order",
                        delay_sec=0.02, attempt=2,
                    ),
                    self._ok_entry_result(),
                ]
                placer, store, fm, bus, om, engine_mock = (
                    self._make_placer_with_mock_engine(
                        Path(tmp), engine_side_effect=side_effects,
                        max_placer_retries=3,
                    )
                )
                sig_id = _seed_signal(store)

                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=2500.0, sl_price=2450.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_bl19_retry",
                )

                assert engine_mock.execute.call_count == 3, (
                    f"Expected 3 engine calls (2 retries + success), "
                    f"got {engine_mock.execute.call_count}"
                )
                rows = store.fetch_all(
                    "SELECT status FROM trades WHERE signal_id = ?", (sig_id,)
                )
                assert len(rows) == 1
                assert rows[0]["status"] == "PENDING_FILL", (
                    f"Expected PENDING_FILL after successful retry, "
                    f"got {rows[0]['status']}"
                )
                assert fm.released == [], (
                    f"Reservation should not be released on successful retry, "
                    f"got released={fm.released}"
                )
                print("  OK BL-19: placer retries 2x on 429 then succeeds (3 engine calls)")
            finally:
                if store is not None:
                    store.close()
                gc.collect()

    def test_placer_gives_up_after_max_retries_and_propagates(self) -> None:
        """All attempts 429 -> engine called max_retries+1 times, FAILED + release."""
        import gc
        with TemporaryDirectory() as tmp:
            store = None
            try:
                max_retries = 3
                side_effects = [
                    BrokerRateLimit429Error(
                        "broker 429 on place_order",
                        operation="place_order", category="order",
                        delay_sec=0.01, attempt=n + 1,
                    )
                    for n in range(max_retries + 1)
                ]
                placer, store, fm, bus, om, engine_mock = (
                    self._make_placer_with_mock_engine(
                        Path(tmp), engine_side_effect=side_effects,
                        max_placer_retries=max_retries,
                    )
                )
                sig_id = _seed_signal(store)

                with pytest.raises(BrokerRateLimit429Error):
                    placer.place(
                        symbol="RELIANCE", side="BUY", qty=10,
                        entry_price=2500.0, sl_price=2450.0,
                        intent="INTRADAY", signal_id=sig_id,
                        reservation_id="res_bl19_exhaust",
                    )

                assert engine_mock.execute.call_count == max_retries + 1, (
                    f"Expected {max_retries + 1} engine calls (initial + {max_retries} "
                    f"retries), got {engine_mock.execute.call_count}"
                )
                rows = store.fetch_all(
                    "SELECT status FROM trades WHERE signal_id = ?", (sig_id,)
                )
                assert len(rows) == 1
                assert rows[0]["status"] == "FAILED", (
                    f"Expected FAILED after 429 exhaustion, got {rows[0]['status']}"
                )
                assert "res_bl19_exhaust" in fm.released, (
                    f"Expected reservation released on 429 exhaustion, "
                    f"got released={fm.released}"
                )
                print("  OK BL-19: placer gives up after max retries, trade FAILED + release")
            finally:
                if store is not None:
                    store.close()
                gc.collect()

    def test_placer_does_not_retry_other_broker_errors(self) -> None:
        """BrokerTimeoutError -> engine called ONCE, error propagates (ZA11 / OP7)."""
        import gc
        with TemporaryDirectory() as tmp:
            store = None
            try:
                side_effects = [BrokerTimeoutError("network timeout")]
                placer, store, fm, bus, om, engine_mock = (
                    self._make_placer_with_mock_engine(
                        Path(tmp), engine_side_effect=side_effects,
                        max_placer_retries=3,
                    )
                )
                sig_id = _seed_signal(store)

                with pytest.raises(BrokerTimeoutError):
                    placer.place(
                        symbol="RELIANCE", side="BUY", qty=10,
                        entry_price=2500.0, sl_price=2450.0,
                        intent="INTRADAY", signal_id=sig_id,
                        reservation_id="res_bl19_noretry",
                    )

                assert engine_mock.execute.call_count == 1, (
                    f"Non-429 BrokerError MUST get a single attempt (ZA11 / OP7), "
                    f"got {engine_mock.execute.call_count} calls"
                )
                rows = store.fetch_all(
                    "SELECT status FROM trades WHERE signal_id = ?", (sig_id,)
                )
                assert rows[0]["status"] == "FAILED"
                assert "res_bl19_noretry" in fm.released
                print("  OK BL-19: non-429 BrokerError gets single attempt, no retry")
            finally:
                if store is not None:
                    store.close()
                gc.collect()

    def test_placer_retry_does_not_sleep_directly(self) -> None:
        """
        Path A invariant: the placer retry loop does NOT sleep. Backoff is
        delivered by the adapter's rate_limiter.penalize() freeze, which the
        next iteration's acquire() honors. If a future refactor adds a
        time.sleep() to the placer retry, this test fails.
        """
        import gc
        import orders.order_placer as op_mod

        with TemporaryDirectory() as tmp:
            store = None
            original_sleep = None
            try:
                side_effects = [
                    BrokerRateLimit429Error(
                        "broker 429 on place_order",
                        operation="place_order", category="order",
                        delay_sec=0.01, attempt=1,
                    ),
                    self._ok_entry_result(),
                ]
                placer, store, fm, bus, om, engine_mock = (
                    self._make_placer_with_mock_engine(
                        Path(tmp), engine_side_effect=side_effects,
                        max_placer_retries=3,
                    )
                )
                sig_id = _seed_signal(store)

                sleep_calls: list[float] = []
                if hasattr(op_mod, "time"):
                    original_sleep = op_mod.time.sleep

                    def spy_sleep(sec: float) -> None:
                        sleep_calls.append(sec)
                        original_sleep(sec)

                    op_mod.time.sleep = spy_sleep  # type: ignore[assignment]

                placer.place(
                    symbol="RELIANCE", side="BUY", qty=10,
                    entry_price=2500.0, sl_price=2450.0,
                    intent="INTRADAY", signal_id=sig_id,
                    reservation_id="res_bl19_nosleep",
                )

                assert engine_mock.execute.call_count == 2
                assert sleep_calls == [], (
                    f"Path A invariant violated: placer retry slept {sleep_calls}. "
                    f"Backoff must come from rate_limiter.penalize, not caller sleep."
                )
                print(
                    "  OK BL-19: placer retry does NOT call time.sleep "
                    "(Path A: backoff via rate_limiter.penalize freeze)"
                )
            finally:
                if hasattr(op_mod, "time") and original_sleep is not None:
                    op_mod.time.sleep = original_sleep  # type: ignore[assignment]
                if store is not None:
                    store.close()
                gc.collect()


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        # OrderManager
        TestOrderManager().test_create_trade_returns_trd_id,
        TestOrderManager().test_create_trade_inserts_pending_fill_row,
        TestOrderManager().test_insert_order_creates_order_row,
        TestOrderManager().test_record_entry_fill_sets_open,
        TestOrderManager().test_update_trade_status,
        TestOrderManager().test_link_signal_trade,
        TestOrderManager().test_get_trade_returns_none_for_missing,
        TestOrderManager().test_get_orders_for_trade_multiple,
        # LimitTripleProtocol
        TestLimitTripleProtocol().test_happy_path_places_three_orders,
        TestLimitTripleProtocol().test_short_trade_uses_correct_exit_sides,
        TestLimitTripleProtocol().test_entry_failure_raises_no_sl_placed,
        TestLimitTripleProtocol().test_sl_failure_cancels_entry,
        TestLimitTripleProtocol().test_tgt_failure_raises_sl_still_standing,
        # CoPlusTgtProtocol
        TestCoPlusTgtProtocol().test_happy_path_places_co_and_tgt,
        TestCoPlusTgtProtocol().test_co_failure_raises,
        TestCoPlusTgtProtocol().test_tgt_failure_returns_failure,
        TestCoPlusTgtProtocol().test_short_co_trade_correct_sides,
        # FullEntryEngine
        TestFullEntryEngine().test_routes_limit_triple,
        TestFullEntryEngine().test_routes_co_plus_tgt,
        TestFullEntryEngine().test_unknown_protocol_raises,
        TestFullEntryEngine().test_default_protocol_used_when_not_provided,
        # OrderPlacer
        TestOrderPlacer().test_happy_path_creates_trade_and_places_orders,
        TestOrderPlacer().test_tgt_computed_from_rr_ratio,
        TestOrderPlacer().test_short_tgt_computed_correctly,
        TestOrderPlacer().test_caller_supplied_tgt_price_overrides_internal,
        TestOrderPlacer().test_none_tgt_price_uses_internal_computation,
        TestOrderPlacer().test_broker_failure_sets_trade_failed_releases_capital,
        TestOrderPlacer().test_fill_event_commits_capital_and_updates_trade,
        TestOrderPlacer().test_fill_for_unknown_internal_id_is_ignored,
        TestOrderPlacer().test_fill_map_entry_removed_after_fill,
        TestOrderPlacer().test_place_co_protocol_places_two_orders,
        # KillSwitchLastMile
        TestKillSwitchLastMile().test_kill_switch_active_aborts_no_adapter_call,
        TestKillSwitchLastMile().test_kill_switch_inactive_does_not_block,
        # ReservationRelease
        TestReservationRelease().test_reservation_released_on_broker_error,
        # EmptyBrokerOrderId
        TestEmptyBrokerOrderId().test_limit_triple_empty_entry_id_raises,
        TestEmptyBrokerOrderId().test_limit_triple_empty_sl_id_raises,
        TestEmptyBrokerOrderId().test_co_empty_co_id_raises,
        TestEmptyBrokerOrderId().test_co_empty_tgt_id_is_failure,
        # ProductResolverWiring
        TestProductResolverWiring().test_intraday_uses_mis_from_resolver,
        # BL-7a _FillEntry leg taxonomy
        TestBl7aFillEntryLegTaxonomy().test_fill_entry_rejects_invalid_leg,
        TestBl7aFillEntryLegTaxonomy().test_fill_entry_accepts_all_valid_legs,
        TestBl7aFillEntryLegTaxonomy().test_fill_entry_stores_order_protocol_and_direction,
        # BL-12 OrderStatusChanged event pipeline
        TestBl12OrderStatusEventPipeline().test_order_monitor_complete_updates_orders_table_status,
        TestBl12OrderStatusEventPipeline().test_order_monitor_cancelled_updates_orders_table_status,
        TestBl12OrderStatusEventPipeline().test_order_monitor_rejected_updates_orders_table_status,
        TestBl12OrderStatusEventPipeline().test_partial_fill_updates_qty_filled_in_orders_table,
        TestBl12OrderStatusEventPipeline().test_order_manager_subscribes_to_order_status_changed,
        TestBl12OrderStatusEventPipeline().test_order_manager_bus_none_does_not_subscribe,
        # BL-7b OrderPlacer dependency injection
        TestBl7bOrderPlacerDependencyInjection().test_order_placer_stores_injected_order_monitor,
        TestBl7bOrderPlacerDependencyInjection().test_order_placer_accepts_none_smart_tgt_manager,
        TestBl7bOrderPlacerDependencyInjection().test_order_placer_raises_when_smart_tgt_manager_without_config,
        TestBl7bOrderPlacerDependencyInjection().test_order_placer_accepts_smart_tgt_manager_with_config,
        TestBl7bOrderPlacerDependencyInjection().test_order_placer_requires_cost_calculator,
        TestBl7dEntryFillSmartTgt().test_co_entry_fill_registers_with_smart_tgt,
        TestBl7dEntryFillSmartTgt().test_limit_triple_entry_fill_skips_smart_tgt,
        TestBl7dEntryFillSmartTgt().test_entry_fill_with_null_manager_is_noop,
        # BL-7c OrderPlacer.track() wiring (A.3.c)
        TestBl7cOrderPlacerTrackingWiring().test_place_entry_calls_order_monitor_track_for_entry_leg,
        TestBl7cOrderPlacerTrackingWiring().test_place_entry_tracks_all_three_legs_for_limit_triple,
        TestBl7cOrderPlacerTrackingWiring().test_co_protocol_skips_sl_track,
        TestBl7cOrderPlacerTrackingWiring().test_on_order_filled_still_handles_entry_leg,
        # BL-7d + BL-10a exit-fill handling (A.3.d)
        TestBl7dExitFillHandling().test_sl_fill_closes_trade_with_sl_hit,
        TestBl7dExitFillHandling().test_tgt_fill_closes_trade_with_tgt_hit,
        TestBl7dExitFillHandling().test_eod_fill_closes_trade_with_eod_squareoff,
        TestBl7dExitFillHandling().test_long_tgt_fill_gross_pnl_direction_correct,
        TestBl7dExitFillHandling().test_short_tgt_fill_gross_pnl_direction_correct,
        TestBl7dExitFillHandling().test_cost_calc_called_with_round_trip_args,
        TestBl7dExitFillHandling().test_release_used_called_with_direction_and_intent,
        TestBl7dExitFillHandling().test_position_closed_published_with_net_pnl,
        TestBl7dExitFillHandling().test_smart_tgt_unregister_called_for_co_plus_tgt,
        TestBl7dExitFillHandling().test_smart_tgt_unregister_skipped_for_limit_triple,
        TestBl7dExitFillHandling().test_exit_fill_pops_fill_map_entry,
        TestBl7dExitFillHandling().test_double_close_is_warning_and_skip,
        # BL-8 / Phase C.2 atomic persist + cancel-on-failure + hard_kill
        TestBl8AtomicPersist().test_happy_path_no_cancellation_no_hard_kill,
        TestBl8AtomicPersist().test_persist_atomic_all_or_nothing,
        TestBl8AtomicPersist().test_db_persist_failure_after_broker_success_cancels_all,
        TestBl8AtomicPersist().test_db_persist_failure_after_broker_success_fires_hard_kill,
        TestBl8AtomicPersist().test_cancel_failure_during_cleanup_critical_logged_grep_tag,
        TestBl8AtomicPersist().test_co_plus_tgt_soft_fail_cancels_co,
        TestBl8AtomicPersist().test_limit_triple_sl_fail_protocol_cleanup_not_double_cancelled,
        TestBl8AtomicPersist().test_cancel_order_returning_false_does_not_abort_cleanup,
        TestBl8AtomicPersist().test_protocol_reject_only_does_not_fire_hard_kill,
        # EF-2 / Phase E.6 track-failure cleanup
        TestEf2TrackFailureCleanup().test_ef2_track_raises_on_entry_leg_full_cleanup,
        TestEf2TrackFailureCleanup().test_ef2_track_raises_mid_loop_partial_cleanup,
        TestEf2TrackFailureCleanup().test_ef2_track_success_unchanged_behavior,
        # BL-19 / Phase D.1 placer rate-limit retry loop
        TestBl19PlacerRateLimitRetry().test_placer_retries_on_429_up_to_max,
        TestBl19PlacerRateLimitRetry().test_placer_gives_up_after_max_retries_and_propagates,
        TestBl19PlacerRateLimitRetry().test_placer_does_not_retry_other_broker_errors,
        TestBl19PlacerRateLimitRetry().test_placer_retry_does_not_sleep_directly,
    ]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"FAILED: {fn.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
