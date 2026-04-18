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

from broker.product_resolver import ProductResolver
from broker.zerodha_adapter import PlacedOrder
from core.events import EventBus, OrderFilled, OrderStatusChanged
from core.exceptions import BrokerAuthError, BrokerError, OrderRejectedError
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

    def __init__(self) -> None:
        self.committed: List[dict] = []
        self.released: List[str] = []

    def commit_to_used(self, reservation_id, actual_fill_price, actual_qty):
        self.committed.append({
            "reservation_id": reservation_id,
            "actual_fill_price": actual_fill_price,
            "actual_qty": actual_qty,
        })

    def release(self, reservation_id, reason=""):
        self.released.append(reservation_id)


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
            print("  OK BL-12: COMPLETE → orders.status=COMPLETE persisted")

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
            print("  OK BL-12: CANCELLED → orders.status=CANCELLED persisted")

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
            print("  OK BL-12: REJECTED (OSM=FAILED) → orders.status=FAILED persisted")

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
            print("  OK BL-12: PARTIAL → qty_filled=4 persisted")

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
            print("  OK OMgr10: bus=None → no subscription, orders row untouched")


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
