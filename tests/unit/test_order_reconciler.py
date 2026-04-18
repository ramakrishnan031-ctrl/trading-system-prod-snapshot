"""
tests/unit/test_order_reconciler.py — Trading System v2

Unit tests for orders/order_reconciler.py (RC1-RC20).

Coverage:
  - 6 reconciliation checks (RC5a-f): MANUAL_CLOSE, ORPHAN_ADOPTION, HEALTHY,
    PARTIAL_CLOSE, POSITION_GREW, ORPHAN_ORDER
  - G5b crash-recovery SL: 4 direction×condition cases + placement failure
  - G3 capital drift: drift > tolerance and within tolerance
  - Lifecycle: start/stop, startup reconciliation (RC14)
  - Hybrid cadence: event-driven via OrderStateChanged (RC4)
  - Error handling: BrokerTimeoutError skips check (RC11);
    3x BrokerAuthError → soft_kill (RC12)
  - reconcile_once() non-reentrant (RC13)
  - reconciliation_log persisted for non-COSMETIC actions (RC10)
  - broker_orders_fn=None skips CHECK 6 with no error (RC5f)
  - Logger name "order_reconciler" (RC16)
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.events import CapitalDriftDetected, EventBus, OrderStateChanged, PositionClosed
from core.exceptions import BrokerAuthError, BrokerTimeoutError
from core.state_store import StateStore
from orders.order_reconciler import OrderReconciler, ReconciliationAction

_IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
# Stub data structures matching broker adapter types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Position:
    symbol: str
    qty: int
    avg_price: float
    product: str = "MIS"
    side: str = "BUY"


@dataclass
class _MarginInfo:
    net: float
    available: float
    used: float
    ts: datetime = field(default_factory=lambda: datetime.now(_IST))


@dataclass
class _Quote:
    symbol: str
    last_price: float
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    ts: datetime = field(default_factory=lambda: datetime.now(_IST))


@dataclass
class _PlacedOrder:
    internal_order_id: str
    broker_order_id: str
    symbol: str
    side: str
    qty: int
    price: float
    order_type: str
    product: str = "MIS"
    status: str = "SUBMITTED"
    ts: datetime = field(default_factory=lambda: datetime.now(_IST))
    trigger_price: float = 0.0
    variety: str = "regular"


# ─────────────────────────────────────────────────────────────────────────────
# Test fixtures / builder helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "test.db")


def _insert_trade(
    store: StateStore,
    trade_id: str,
    symbol: str = "RELIANCE",
    direction: str = "LONG",
    status: str = "OPEN",
    qty_filled: int = 10,
    sl_initial: float = 2450.0,
    entry_actual_price: float = 2500.0,
) -> None:
    """Insert a signal + trade row for reconciler tests."""
    sig_id = f"sig_{trade_id}"
    now = "2026-04-16T09:30:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals
              (signal_id, symbol, scanner, strategy, triggered_at, received_at,
               expires_at, status, fingerprint, fingerprint_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sig_id, symbol, "SCANNER", "strategy", now, now,
             "2026-04-16T09:35:00+05:30", "TRADED", f"fp_{trade_id}", "2026-04-16"),
        )
        cur.execute(
            """
            INSERT INTO trades
              (trade_id, signal_id, symbol, direction, strategy, sector,
               qty_planned, qty_filled, entry_target_price, sl_initial,
               tgt_initial, margin_reserved, risk_amount, created_at,
               status, order_protocol, updated_at, entry_actual_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (trade_id, sig_id, symbol, direction, "strategy", "ENERGY",
             qty_filled, qty_filled, entry_actual_price, sl_initial,
             entry_actual_price * 1.02, 10000.0, 500.0, now,
             status, "LIMIT_TRIPLE", now, entry_actual_price),
        )


def _insert_order(
    store: StateStore,
    order_id: str,
    trade_id: str,
    leg: str = "ENTRY",
    product: str = "MIS",
    status: str = "COMPLETE",
    trigger_price: float = 0.0,
) -> None:
    now = "2026-04-16T09:30:00+05:30"
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO orders
              (order_id, trade_id, leg, transaction_type, order_type, product,
               variety, qty_requested, status, trigger_price, placed_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, trade_id, leg, "BUY", "LIMIT", product,
             "regular", 10, status, trigger_price, now, now),
        )


def _make_reconciler(
    store: StateStore,
    adapter=None,
    fund_manager=None,
    kill_switch=None,
    notifier=None,
    bus=None,
    quote_fn=None,
    broker_orders_fn=None,
    poll_interval_sec: int = 60,
    capital_drift_tolerance: float = 50.0,
    enable_event_driven: bool = True,
) -> OrderReconciler:
    """Build an OrderReconciler with sensible mock defaults."""
    import logging
    from core.config_loader import OrderReconcilerConfig

    if adapter is None:
        adapter = MagicMock()
        adapter.get_positions.return_value = []
        adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    if fund_manager is None:
        snap = MagicMock()
        snap.total = 100_000.0
        fund_manager = MagicMock()
        fund_manager.get_snapshot.return_value = snap

    if kill_switch is None:
        kill_switch = MagicMock()

    if notifier is None:
        notifier = MagicMock()
        notifier.send.return_value = MagicMock(success=True)

    if bus is None:
        bus = EventBus()

    if quote_fn is None:
        quote_fn = lambda symbols: {}

    cfg = OrderReconcilerConfig(
        poll_interval_sec=poll_interval_sec,
        capital_drift_tolerance=capital_drift_tolerance,
        enable_event_driven=enable_event_driven,
    )

    logger = logging.getLogger("order_reconciler")

    return OrderReconciler(
        state_store=store,
        adapter=adapter,
        fund_manager=fund_manager,
        kill_switch=kill_switch,
        notifier=notifier,
        bus=bus,
        logger=logger,
        cfg=cfg,
        quote_fn=quote_fn,
        broker_orders_fn=broker_orders_fn,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Import and instantiation
# ─────────────────────────────────────────────────────────────────────────────

def test_import_and_instantiate(tmp_path: Path) -> None:
    """OrderReconciler can be instantiated without error."""
    store = _make_store(tmp_path)
    rec = _make_reconciler(store)
    assert rec is not None
    store.close()
    print("  OK OrderReconciler instantiates without error")


def test_logger_name(tmp_path: Path) -> None:
    """Logger name must be 'order_reconciler' (RC16)."""
    import logging
    store = _make_store(tmp_path)
    rec = _make_reconciler(store)
    assert rec._log.name == "order_reconciler"
    store.close()
    print("  OK logger name is 'order_reconciler' (RC16)")


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 3: HEALTHY — no action required
# ─────────────────────────────────────────────────────────────────────────────

def test_check3_healthy(tmp_path: Path) -> None:
    """HEALTHY: broker qty matches local qty_filled → COSMETIC action, no DB change."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="RELIANCE", qty_filled=10)
    _insert_order(store, "ord1", "t1", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = [_Position("RELIANCE", qty=10, avg_price=2500.0)]
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    rec = _make_reconciler(store, adapter=adapter)
    actions = rec.reconcile_once()

    healthy = [a for a in actions if a.check_name == "HEALTHY"]
    assert len(healthy) == 1
    assert healthy[0].tier == "COSMETIC"
    assert healthy[0].symbol == "RELIANCE"
    assert healthy[0].trade_id == "t1"

    # COSMETIC actions must NOT be persisted to reconciliation_log
    count = store.row_count("reconciliation_log")
    assert count == 0, f"COSMETIC should not be logged; got {count} rows"
    store.close()
    print("  OK CHECK3 HEALTHY: COSMETIC action, not persisted to reconciliation_log")


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 1: MANUAL_CLOSE
# ─────────────────────────────────────────────────────────────────────────────

def test_check1_manual_close(tmp_path: Path) -> None:
    """MANUAL_CLOSE: local OPEN but broker has no position → mark CLOSED_MANUAL."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="RELIANCE", status="OPEN")
    _insert_order(store, "ord1", "t1", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = []   # no positions at broker
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm)
    actions = rec.reconcile_once()

    mc = [a for a in actions if a.check_name == "MANUAL_CLOSE"]
    assert len(mc) == 1
    assert mc[0].tier == "RECOVERABLE"
    assert mc[0].symbol == "RELIANCE"
    assert mc[0].trade_id == "t1"
    assert mc[0].success is True

    # Trade should be marked CLOSED_MANUAL in DB
    row = store.fetch_one("SELECT status, exit_reason FROM trades WHERE trade_id=?", ("t1",))
    assert row["status"] == "CLOSED_MANUAL"
    assert row["exit_reason"] == "MANUAL"

    # Should be persisted to reconciliation_log
    log_row = store.fetch_one("SELECT * FROM reconciliation_log WHERE check_name=?", ("MANUAL_CLOSE",))
    assert log_row is not None
    assert log_row["tier"] == "RECOVERABLE"

    store.close()
    print("  OK CHECK1 MANUAL_CLOSE: trade marked CLOSED_MANUAL, logged to reconciliation_log")


def test_check1_manual_close_releases_capital(tmp_path: Path) -> None:
    """MANUAL_CLOSE calls fund_manager.release_used() as breakeven proxy."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="INFY", status="OPEN",
                  qty_filled=5, entry_actual_price=1500.0)
    _insert_order(store, "ord1", "t1", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm)
    rec.reconcile_once()

    # release_used called with entry == exit (breakeven)
    fm.release_used.assert_called_once()
    call_kwargs = fm.release_used.call_args
    assert call_kwargs.kwargs["symbol"] == "INFY"
    assert call_kwargs.kwargs["exit_price"] == 1500.0
    assert call_kwargs.kwargs["entry_price"] == 1500.0   # breakeven
    assert call_kwargs.kwargs["exit_qty"] == 5
    assert call_kwargs.kwargs["costs"] == 0.0
    # EF-3: reconciler reads direction from the trade row (default LONG here)
    assert call_kwargs.kwargs["direction"] == "LONG"

    store.close()
    print("  OK CHECK1 MANUAL_CLOSE: release_used called with breakeven exit=entry")


# ─────────────────────────────────────────────────────────────────────────────
# BL-10b: MANUAL_CLOSE publishes PositionClosed (out-of-band closure)
# ─────────────────────────────────────────────────────────────────────────────

def _stub_release_result(pnl_delta: float = 0.0):
    """Build a minimal release_used return value supporting .pnl_delta."""
    res = MagicMock()
    res.pnl_delta = pnl_delta
    res.margin_released = 10_000.0
    res.bucket = "intraday"
    res.reservation_id = "res_stub"
    return res


def test_manual_close_publishes_position_closed(tmp_path: Path) -> None:
    """BL-10b: MANUAL_CLOSE emits PositionClosed so shadow_tracker learns about it."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="INFY", status="OPEN",
                  qty_filled=5, entry_actual_price=1500.0)
    _insert_order(store, "ord1", "t1", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(
        net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap
    fm.release_used.return_value = _stub_release_result(pnl_delta=0.0)

    bus = EventBus()
    received: List[PositionClosed] = []
    bus.subscribe(PositionClosed, received.append)

    rec = _make_reconciler(store, adapter=adapter, bus=bus, fund_manager=fm)
    rec.reconcile_once()

    assert len(received) == 1, f"expected 1 PositionClosed, got {len(received)}"
    ev = received[0]
    assert ev.trade_id == "t1"
    assert ev.symbol == "INFY"
    assert ev.signal_id == "sig_t1"
    store.close()
    print("  OK BL-10b: MANUAL_CLOSE publishes PositionClosed")


def test_manual_close_position_closed_has_breakeven_exit_price(tmp_path: Path) -> None:
    """BL-10b: exit_price==entry_price (breakeven proxy, no real fill price known)."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t_bkv", symbol="INFY", status="OPEN",
                  qty_filled=5, entry_actual_price=1500.0)
    _insert_order(store, "ord_bkv", "t_bkv", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(
        net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap
    fm.release_used.return_value = _stub_release_result(pnl_delta=0.0)

    bus = EventBus()
    received: List[PositionClosed] = []
    bus.subscribe(PositionClosed, received.append)

    rec = _make_reconciler(store, adapter=adapter, bus=bus, fund_manager=fm)
    rec.reconcile_once()

    assert len(received) == 1
    assert received[0].exit_price == 1500.0, (
        "breakeven proxy: exit_price must equal entry_actual_price"
    )
    assert received[0].realized_pnl == 0.0, (
        "breakeven (costs=0): realized_pnl must be 0"
    )
    store.close()
    print("  OK BL-10b: PositionClosed.exit_price == entry (breakeven proxy)")


def test_manual_close_publish_failure_does_not_raise(tmp_path: Path) -> None:
    """BL-10b: a subscriber raising must not break the reconciler cycle."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t_pf", symbol="INFY", status="OPEN",
                  qty_filled=5, entry_actual_price=1500.0)
    _insert_order(store, "ord_pf", "t_pf", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(
        net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap
    fm.release_used.return_value = _stub_release_result(pnl_delta=0.0)

    bus = EventBus()

    def _boom(ev):
        raise RuntimeError("subscriber exploded")

    bus.subscribe(PositionClosed, _boom)

    rec = _make_reconciler(store, adapter=adapter, bus=bus, fund_manager=fm)
    actions = rec.reconcile_once()  # must not raise

    # Trade still CLOSED in DB, release_used still called
    fm.release_used.assert_called_once()
    row = store.fetch_one("SELECT status FROM trades WHERE trade_id=?", ("t_pf",))
    assert row["status"] == "CLOSED_MANUAL"
    # MANUAL_CLOSE action present
    assert any(a.check_name == "MANUAL_CLOSE" for a in actions)
    store.close()
    print("  OK BL-10b: publish failure is logged + swallowed (non-fatal)")


def test_manual_close_position_closed_source_module_is_reconciler(tmp_path: Path) -> None:
    """BL-10b: source_module='order_reconciler' separates telemetry from order_placer."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t_src", symbol="INFY", status="OPEN",
                  qty_filled=5, entry_actual_price=1500.0)
    _insert_order(store, "ord_src", "t_src", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(
        net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap
    fm.release_used.return_value = _stub_release_result(pnl_delta=0.0)

    bus = EventBus()
    received: List[PositionClosed] = []
    bus.subscribe(PositionClosed, received.append)

    rec = _make_reconciler(store, adapter=adapter, bus=bus, fund_manager=fm)
    rec.reconcile_once()

    assert len(received) == 1
    assert received[0].source_module == "order_reconciler", (
        "telemetry separation: must NOT claim to originate from order_placer"
    )
    store.close()
    print("  OK BL-10b: source_module='order_reconciler' (telemetry separation)")


def test_manual_close_for_short_publishes_position_closed(tmp_path: Path) -> None:
    """BL-10b: SHORT MANUAL_CLOSE also publishes; direction routed to release_used (EF-3)."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t_sh", symbol="INFY", direction="SHORT",
                  status="OPEN", qty_filled=5, entry_actual_price=1500.0)
    _insert_order(store, "ord_sh", "t_sh", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(
        net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap
    fm.release_used.return_value = _stub_release_result(pnl_delta=0.0)

    bus = EventBus()
    received: List[PositionClosed] = []
    bus.subscribe(PositionClosed, received.append)

    rec = _make_reconciler(store, adapter=adapter, bus=bus, fund_manager=fm)
    rec.reconcile_once()

    # EF-3: direction must be forwarded as SHORT
    fm.release_used.assert_called_once()
    assert fm.release_used.call_args.kwargs["direction"] == "SHORT"
    # PositionClosed still emits (breakeven => realized_pnl=0)
    assert len(received) == 1
    assert received[0].trade_id == "t_sh"
    store.close()
    print("  OK BL-10b: SHORT MANUAL_CLOSE publishes + routes direction (EF-3)")


def test_manual_close_skips_publish_when_entry_price_missing(tmp_path: Path) -> None:
    """
    BL-10b: when capital-release is skipped (missing entry_price/intent), the
    reconciler must NOT fabricate a PositionClosed event. Publishing with the
    planned target price would broadcast an unexecuted number to shadow_tracker.
    WARN log is the operator-visible signal; silence on the bus is intentional.
    """
    store = _make_store(tmp_path)
    # entry_actual_price=0.0 => capital-release branch is skipped
    _insert_trade(store, "t_miss", symbol="INFY", status="OPEN",
                  qty_filled=5, entry_actual_price=0.0)
    _insert_order(store, "ord_miss", "t_miss", leg="ENTRY",
                  product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(
        net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    bus = EventBus()
    received: List[PositionClosed] = []
    bus.subscribe(PositionClosed, received.append)

    rec = _make_reconciler(store, adapter=adapter, bus=bus, fund_manager=fm)
    rec.reconcile_once()

    # Release was skipped, publish was skipped
    fm.release_used.assert_not_called()
    assert received == [], (
        "design lock: do NOT publish PositionClosed when capital-release is skipped"
    )
    # Trade still marked CLOSED_MANUAL (mark_trade_manually_closed ran)
    row = store.fetch_one("SELECT status FROM trades WHERE trade_id=?", ("t_miss",))
    assert row["status"] == "CLOSED_MANUAL"
    store.close()
    print("  OK BL-10b: skips publish when capital-release is skipped (design lock)")


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 2: ORPHAN_ADOPTION
# ─────────────────────────────────────────────────────────────────────────────

def test_check2_orphan_adoption(tmp_path: Path) -> None:
    """ORPHAN_ADOPTION: broker position with no local trade → CapitalDriftDetected."""
    store = _make_store(tmp_path)
    # No local trades

    adapter = MagicMock()
    adapter.get_positions.return_value = [_Position("TCS", qty=5, avg_price=3500.0)]
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    bus = EventBus()
    received: list = []
    bus.subscribe(CapitalDriftDetected, received.append)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, bus=bus, fund_manager=fm)
    actions = rec.reconcile_once()

    orphan = [a for a in actions if a.check_name == "ORPHAN_ADOPTION"]
    assert len(orphan) == 1
    assert orphan[0].tier == "UNRECOVERABLE"
    assert orphan[0].symbol == "TCS"
    assert orphan[0].trade_id is None

    assert len(received) == 1, "CapitalDriftDetected must be published"

    store.close()
    print("  OK CHECK2 ORPHAN_ADOPTION: CapitalDriftDetected published, UNRECOVERABLE")


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 4: PARTIAL_CLOSE
# ─────────────────────────────────────────────────────────────────────────────

def test_check4_partial_close(tmp_path: Path) -> None:
    """PARTIAL_CLOSE: local qty_filled > broker qty → update qty_filled."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="WIPRO", status="OPEN", qty_filled=10)
    _insert_order(store, "ord1", "t1", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = [_Position("WIPRO", qty=7, avg_price=400.0)]
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm)
    actions = rec.reconcile_once()

    pc = [a for a in actions if a.check_name == "PARTIAL_CLOSE"]
    assert len(pc) == 1
    assert pc[0].tier == "RECOVERABLE"
    assert pc[0].success is True

    # qty_filled must be updated to broker qty
    row = store.fetch_one("SELECT qty_filled FROM trades WHERE trade_id=?", ("t1",))
    assert row["qty_filled"] == 7, f"Expected 7, got {row['qty_filled']}"

    store.close()
    print("  OK CHECK4 PARTIAL_CLOSE: qty_filled updated to broker qty")


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 5: POSITION_GREW
# ─────────────────────────────────────────────────────────────────────────────

def test_check5_position_grew(tmp_path: Path) -> None:
    """POSITION_GREW: broker qty > local qty_filled → CapitalDriftDetected, UNRECOVERABLE."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="HDFCBANK", status="OPEN", qty_filled=5)
    _insert_order(store, "ord1", "t1", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = [_Position("HDFCBANK", qty=8, avg_price=1600.0)]
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    bus = EventBus()
    received: list = []
    bus.subscribe(CapitalDriftDetected, received.append)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, bus=bus, fund_manager=fm)
    actions = rec.reconcile_once()

    pg = [a for a in actions if a.check_name == "POSITION_GREW"]
    assert len(pg) == 1
    assert pg[0].tier == "UNRECOVERABLE"
    assert pg[0].trade_id == "t1"

    assert len(received) == 1, "CapitalDriftDetected must be published"
    assert received[0].delta == 3.0   # 8 - 5

    store.close()
    print("  OK CHECK5 POSITION_GREW: CapitalDriftDetected with delta=3, UNRECOVERABLE")


# ─────────────────────────────────────────────────────────────────────────────
# CHECK 6: ORPHAN_ORDER
# ─────────────────────────────────────────────────────────────────────────────

def test_check6_orphan_order_detected(tmp_path: Path) -> None:
    """ORPHAN_ORDER: PENDING_FILL local order not in broker open orders → logged."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="INFY", status="PENDING_FILL", qty_filled=0)
    _insert_order(store, "broker_ord_abc", "t1", leg="ENTRY", product="MIS",
                  status="PENDING")

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    # Broker open orders doesn't include broker_ord_abc
    broker_orders_fn = lambda: [{"order_id": "some_other_order"}]

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm,
                           broker_orders_fn=broker_orders_fn)
    actions = rec.reconcile_once()

    orphan = [a for a in actions if a.check_name == "ORPHAN_ORDER"]
    assert len(orphan) == 1
    assert orphan[0].tier == "UNRECOVERABLE"
    assert orphan[0].symbol == "INFY"

    store.close()
    print("  OK CHECK6 ORPHAN_ORDER: orphan order detected and logged")


def test_check6_skipped_when_no_broker_orders_fn(tmp_path: Path) -> None:
    """CHECK 6 is skipped entirely when broker_orders_fn is None."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="INFY", status="PENDING_FILL", qty_filled=0)
    _insert_order(store, "broker_ord_abc", "t1", leg="ENTRY", product="MIS",
                  status="PENDING")

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm,
                           broker_orders_fn=None)   # None = skip
    actions = rec.reconcile_once()

    orphan = [a for a in actions if a.check_name == "ORPHAN_ORDER"]
    assert orphan == [], f"Expected no ORPHAN_ORDER when broker_orders_fn=None, got {orphan}"

    store.close()
    print("  OK CHECK6 skipped when broker_orders_fn is None")


# ─────────────────────────────────────────────────────────────────────────────
# G5b: CRASH_RECOVERY_SL
# ─────────────────────────────────────────────────────────────────────────────

def test_g5b_long_ltp_above_sl_places_slm(tmp_path: Path) -> None:
    """G5b LONG: LTP > sl_initial → place SL-M SELL."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="RELIANCE", direction="LONG",
                  status="OPEN", qty_filled=10, sl_initial=2450.0,
                  entry_actual_price=2500.0)
    _insert_order(store, "ord_entry", "t1", leg="ENTRY", product="MIS", status="COMPLETE")
    # No SL order

    adapter = MagicMock()
    adapter.get_positions.return_value = [_Position("RELIANCE", qty=10, avg_price=2500.0)]
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)
    placed = _PlacedOrder("int_1", "broker_sl_1", "RELIANCE", "SELL", 10, 0.0,
                          "SL-M", trigger_price=2450.0)
    adapter.place_order.return_value = placed

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    quote_fn = lambda syms: {"RELIANCE": _Quote("RELIANCE", last_price=2480.0)}

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm, quote_fn=quote_fn)
    actions = rec.reconcile_once()

    rc_sl = [a for a in actions if a.check_name == "CRASH_RECOVERY_SL"]
    assert len(rc_sl) == 1
    assert rc_sl[0].success is True
    assert rc_sl[0].tier == "RECOVERABLE"

    # Adapter should be called with SL-M SELL
    adapter.place_order.assert_called_once()
    call_kwargs = adapter.place_order.call_args.kwargs
    assert call_kwargs["side"] == "SELL"
    assert call_kwargs["order_type"] == "SL-M"
    assert call_kwargs["trigger_price"] == 2450.0

    # SL order should be in DB now
    sl_row = store.get_sl_order_for_trade("t1")
    assert sl_row is not None
    assert sl_row["order_id"] == "broker_sl_1"

    store.close()
    print("  OK G5b LONG LTP>sl: placed SL-M SELL at sl_initial, persisted to orders")


def test_g5b_long_ltp_below_sl_places_market(tmp_path: Path) -> None:
    """G5b LONG: LTP <= sl_initial → SL already breached, place MARKET SELL."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="RELIANCE", direction="LONG",
                  status="OPEN", qty_filled=10, sl_initial=2450.0,
                  entry_actual_price=2500.0)
    _insert_order(store, "ord_entry", "t1", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = [_Position("RELIANCE", qty=10, avg_price=2500.0)]
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)
    placed = _PlacedOrder("int_2", "broker_mkt_1", "RELIANCE", "SELL", 10, 0.0, "MARKET")
    adapter.place_order.return_value = placed

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    # LTP is below sl_initial
    quote_fn = lambda syms: {"RELIANCE": _Quote("RELIANCE", last_price=2420.0)}

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm, quote_fn=quote_fn)
    actions = rec.reconcile_once()

    rc_sl = [a for a in actions if a.check_name == "CRASH_RECOVERY_SL"]
    assert len(rc_sl) == 1
    assert rc_sl[0].success is True

    call_kwargs = adapter.place_order.call_args.kwargs
    assert call_kwargs["order_type"] == "MARKET"
    assert call_kwargs["side"] == "SELL"

    store.close()
    print("  OK G5b LONG LTP<=sl: placed MARKET SELL (SL already breached)")


def test_g5b_short_ltp_below_sl_places_slm(tmp_path: Path) -> None:
    """G5b SHORT: LTP < sl_initial → place SL-M BUY at sl_initial."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="INFY", direction="SHORT",
                  status="OPEN", qty_filled=5, sl_initial=1600.0,
                  entry_actual_price=1550.0)
    _insert_order(store, "ord_entry", "t1", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = [_Position("INFY", qty=-5, avg_price=1550.0, side="SELL")]
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)
    placed = _PlacedOrder("int_3", "broker_sl_2", "INFY", "BUY", 5, 0.0,
                          "SL-M", trigger_price=1600.0)
    adapter.place_order.return_value = placed

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    # LTP is below sl_initial (favorable for short)
    quote_fn = lambda syms: {"INFY": _Quote("INFY", last_price=1580.0)}

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm, quote_fn=quote_fn)
    actions = rec.reconcile_once()

    rc_sl = [a for a in actions if a.check_name == "CRASH_RECOVERY_SL"]
    assert len(rc_sl) == 1, f"Expected 1 CRASH_RECOVERY_SL, got {len(rc_sl)}"

    call_kwargs = adapter.place_order.call_args.kwargs
    assert call_kwargs["side"] == "BUY"
    assert call_kwargs["order_type"] == "SL-M"
    assert call_kwargs["trigger_price"] == 1600.0

    store.close()
    print("  OK G5b SHORT LTP<sl: placed SL-M BUY at sl_initial")


def test_g5b_short_ltp_above_sl_places_market(tmp_path: Path) -> None:
    """G5b SHORT: LTP >= sl_initial → place MARKET BUY (SL already breached)."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="INFY", direction="SHORT",
                  status="OPEN", qty_filled=5, sl_initial=1600.0,
                  entry_actual_price=1550.0)
    _insert_order(store, "ord_entry", "t1", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = [_Position("INFY", qty=-5, avg_price=1550.0)]
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)
    placed = _PlacedOrder("int_4", "broker_mkt_2", "INFY", "BUY", 5, 0.0, "MARKET")
    adapter.place_order.return_value = placed

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    # LTP is above sl_initial (SL already breached for short)
    quote_fn = lambda syms: {"INFY": _Quote("INFY", last_price=1620.0)}

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm, quote_fn=quote_fn)
    actions = rec.reconcile_once()

    rc_sl = [a for a in actions if a.check_name == "CRASH_RECOVERY_SL"]
    assert len(rc_sl) == 1

    call_kwargs = adapter.place_order.call_args.kwargs
    assert call_kwargs["order_type"] == "MARKET"
    assert call_kwargs["side"] == "BUY"

    store.close()
    print("  OK G5b SHORT LTP>=sl: placed MARKET BUY (SL already breached)")


def test_g5b_place_order_timeout_returns_failure_action(tmp_path: Path) -> None:
    """G5b: BrokerTimeoutError during place_order → success=False, no exception."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="RELIANCE", direction="LONG",
                  status="OPEN", qty_filled=10, sl_initial=2450.0,
                  entry_actual_price=2500.0)
    _insert_order(store, "ord_entry", "t1", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = [_Position("RELIANCE", qty=10, avg_price=2500.0)]
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)
    adapter.place_order.side_effect = BrokerTimeoutError("timeout")

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    quote_fn = lambda syms: {"RELIANCE": _Quote("RELIANCE", last_price=2480.0)}

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm, quote_fn=quote_fn)
    actions = rec.reconcile_once()   # must not raise

    rc_sl = [a for a in actions if a.check_name == "CRASH_RECOVERY_SL"]
    assert len(rc_sl) == 1
    assert rc_sl[0].success is False
    assert "timed out" in rc_sl[0].action_taken.lower()

    store.close()
    print("  OK G5b BrokerTimeoutError: success=False, no exception propagated")


def test_g5b_skipped_when_sl_order_exists(tmp_path: Path) -> None:
    """G5b is skipped for trades that already have an active SL order."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="RELIANCE", direction="LONG",
                  status="OPEN", qty_filled=10, sl_initial=2450.0)
    _insert_order(store, "ord_entry", "t1", leg="ENTRY", product="MIS", status="COMPLETE")
    _insert_order(store, "ord_sl", "t1", leg="SL", product="MIS",
                  status="TRIGGER_PENDING", trigger_price=2450.0)

    adapter = MagicMock()
    adapter.get_positions.return_value = [_Position("RELIANCE", qty=10, avg_price=2500.0)]
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm)
    actions = rec.reconcile_once()

    rc_sl = [a for a in actions if a.check_name == "CRASH_RECOVERY_SL"]
    assert rc_sl == [], f"G5b should be skipped when SL exists; got {rc_sl}"
    adapter.place_order.assert_not_called()

    store.close()
    print("  OK G5b skipped when active SL order already exists")


# ─────────────────────────────────────────────────────────────────────────────
# G3: CAPITAL_DRIFT
# ─────────────────────────────────────────────────────────────────────────────

def test_g3_capital_drift_exceeds_tolerance(tmp_path: Path) -> None:
    """G3: drift > tolerance → CapitalDriftDetected published + CRITICAL alert."""
    store = _make_store(tmp_path)

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(net=90_000.0, available=70_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0  # local says 100k; broker says 90k
    fm.get_snapshot.return_value = snap

    bus = EventBus()
    received: list = []
    bus.subscribe(CapitalDriftDetected, received.append)

    notifier = MagicMock()
    notifier.send.return_value = MagicMock(success=True)

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm, bus=bus,
                           notifier=notifier, capital_drift_tolerance=50.0)
    actions = rec.reconcile_once()

    drift = [a for a in actions if a.check_name == "CAPITAL_DRIFT"]
    assert len(drift) == 1
    assert drift[0].tier == "UNRECOVERABLE"
    assert drift[0].trade_id is None

    assert len(received) == 1, "CapitalDriftDetected must be published"
    assert received[0].expected == 100_000.0
    assert received[0].actual == 90_000.0
    assert received[0].delta == 10_000.0

    # CRITICAL alert via notifier
    notifier.send.assert_called_once()
    call_kwargs = notifier.send.call_args.kwargs
    assert call_kwargs["severity"] == "CRITICAL"
    assert call_kwargs["source_module"] == "order_reconciler"

    store.close()
    print("  OK G3 CAPITAL_DRIFT: CapitalDriftDetected published, CRITICAL alert sent")


def test_g3_capital_drift_within_tolerance(tmp_path: Path) -> None:
    """G3: drift <= tolerance → no action, no event."""
    store = _make_store(tmp_path)

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(net=99_980.0, available=79_980.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0  # delta = 20, tolerance = 50
    fm.get_snapshot.return_value = snap

    bus = EventBus()
    received: list = []
    bus.subscribe(CapitalDriftDetected, received.append)

    notifier = MagicMock()

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm, bus=bus,
                           notifier=notifier, capital_drift_tolerance=50.0)
    actions = rec.reconcile_once()

    drift = [a for a in actions if a.check_name == "CAPITAL_DRIFT"]
    assert drift == [], f"Expected no CAPITAL_DRIFT action; got {drift}"
    assert received == [], "No CapitalDriftDetected when within tolerance"
    notifier.send.assert_not_called()

    store.close()
    print("  OK G3 CAPITAL_DRIFT: no action when delta <= tolerance")


def test_g3_get_margins_timeout_skips_check(tmp_path: Path) -> None:
    """G3: BrokerTimeoutError from get_margins → skip drift check (RC11)."""
    store = _make_store(tmp_path)

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.side_effect = BrokerTimeoutError("margins timeout")

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm)
    actions = rec.reconcile_once()   # must not raise

    drift = [a for a in actions if a.check_name == "CAPITAL_DRIFT"]
    assert drift == [], f"G3 must be skipped on timeout; got {drift}"

    store.close()
    print("  OK G3 BrokerTimeoutError from get_margins: check skipped (RC11)")


# ─────────────────────────────────────────────────────────────────────────────
# Error handling: RC11 and RC12
# ─────────────────────────────────────────────────────────────────────────────

def test_rc11_get_positions_timeout_skips_checks_1_to_5(tmp_path: Path) -> None:
    """RC11: BrokerTimeoutError from get_positions → checks 1-5 skipped, no raise."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", status="OPEN")

    adapter = MagicMock()
    adapter.get_positions.side_effect = BrokerTimeoutError("timeout")
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm)
    actions = rec.reconcile_once()   # must not raise

    check_names = {a.check_name for a in actions}
    assert "MANUAL_CLOSE" not in check_names, "Check 1 must be skipped on timeout"
    assert "HEALTHY" not in check_names
    assert "PARTIAL_CLOSE" not in check_names

    store.close()
    print("  OK RC11: get_positions timeout skips checks 1-5, no exception")


def test_rc12_three_consecutive_auth_errors_trigger_soft_kill(tmp_path: Path) -> None:
    """RC12: 3 consecutive BrokerAuthErrors → kill_switch.soft_kill()."""
    store = _make_store(tmp_path)

    adapter = MagicMock()
    adapter.get_positions.side_effect = BrokerAuthError("auth fail")
    adapter.get_margins.side_effect = BrokerAuthError("auth fail")

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    kill_switch = MagicMock()

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm,
                           kill_switch=kill_switch)

    # Run 3 cycles — each increments the counter
    rec.reconcile_once()
    kill_switch.soft_kill.assert_not_called()

    rec.reconcile_once()
    kill_switch.soft_kill.assert_not_called()

    rec.reconcile_once()
    kill_switch.soft_kill.assert_called_once()

    store.close()
    print("  OK RC12: soft_kill called after 3 consecutive BrokerAuthErrors")


def test_rc12_counter_resets_on_success(tmp_path: Path) -> None:
    """RC12: successful broker call resets the auth error counter."""
    store = _make_store(tmp_path)

    adapter = MagicMock()
    adapter.get_positions.side_effect = [
        BrokerAuthError("fail"),
        BrokerAuthError("fail"),
        [],              # success — counter resets
        BrokerAuthError("fail"),
        BrokerAuthError("fail"),
        # Would need a 3rd fail after reset to trigger soft_kill again
    ]
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    kill_switch = MagicMock()

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm,
                           kill_switch=kill_switch)

    rec.reconcile_once()   # fail 1
    rec.reconcile_once()   # fail 2
    rec.reconcile_once()   # success: counter resets
    rec.reconcile_once()   # fail 1 again
    rec.reconcile_once()   # fail 2 again

    kill_switch.soft_kill.assert_not_called()   # never reached 3

    store.close()
    print("  OK RC12: auth error counter resets after successful broker call")


# ─────────────────────────────────────────────────────────────────────────────
# RC10: reconciliation_log persistence
# ─────────────────────────────────────────────────────────────────────────────

def test_rc10_non_cosmetic_actions_persisted(tmp_path: Path) -> None:
    """RC10: non-COSMETIC actions are persisted to reconciliation_log."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="RELIANCE", status="OPEN")
    _insert_order(store, "ord1", "t1", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = []   # triggers MANUAL_CLOSE (RECOVERABLE)
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm)
    rec.reconcile_once()

    count = store.row_count("reconciliation_log")
    assert count >= 1, f"Expected at least 1 reconciliation_log row; got {count}"

    row = store.fetch_one("SELECT * FROM reconciliation_log WHERE check_name='MANUAL_CLOSE'")
    assert row is not None
    assert row["tier"] == "RECOVERABLE"
    assert row["symbol"] == "RELIANCE"
    assert row["trade_id"] == "t1"

    store.close()
    print("  OK RC10: RECOVERABLE action persisted to reconciliation_log")


def test_rc10_cosmetic_actions_not_persisted(tmp_path: Path) -> None:
    """RC10: COSMETIC (HEALTHY) actions are NOT persisted to reconciliation_log."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="RELIANCE", status="OPEN", qty_filled=10)
    _insert_order(store, "ord1", "t1", leg="ENTRY", product="MIS", status="COMPLETE")
    _insert_order(store, "ord_sl", "t1", leg="SL", product="MIS",
                  status="TRIGGER_PENDING", trigger_price=2450.0)

    adapter = MagicMock()
    adapter.get_positions.return_value = [_Position("RELIANCE", qty=10, avg_price=2500.0)]
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm)
    rec.reconcile_once()

    count = store.row_count("reconciliation_log")
    assert count == 0, f"COSMETIC (HEALTHY) must not be logged; got {count}"

    store.close()
    print("  OK RC10: COSMETIC (HEALTHY) action not persisted to reconciliation_log")


# ─────────────────────────────────────────────────────────────────────────────
# RC13: non-reentrant (lock prevents concurrent cycles)
# ─────────────────────────────────────────────────────────────────────────────

def test_rc13_concurrent_cycle_returns_empty(tmp_path: Path) -> None:
    """RC13: if lock is held, reconcile_once() returns [] immediately."""
    store = _make_store(tmp_path)

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm)

    # Acquire the lock manually to simulate a running cycle
    acquired = rec._lock.acquire(blocking=False)
    assert acquired, "Lock should be acquirable before any cycle"

    try:
        result = rec.reconcile_once()
        assert result == [], f"Expected [] when lock is held; got {result}"
    finally:
        rec._lock.release()

    store.close()
    print("  OK RC13: reconcile_once returns [] when lock already held")


# ─────────────────────────────────────────────────────────────────────────────
# RC4: event-driven reconciliation
# ─────────────────────────────────────────────────────────────────────────────

def test_rc4_order_state_changed_triggers_reconcile(tmp_path: Path) -> None:
    """RC4: OrderStateChanged event triggers reconcile_once()."""
    store = _make_store(tmp_path)

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    bus = EventBus()

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm, bus=bus,
                           enable_event_driven=True)
    rec.start()

    initial_calls = adapter.get_positions.call_count

    # Publish OrderStateChanged event
    bus.publish(OrderStateChanged(source_module="test", order_id="ord_1", from_state="PENDING", to_state="COMPLETE"))
    # Give event a moment to process (synchronous bus, so it's immediate)

    time.sleep(0.05)
    final_calls = adapter.get_positions.call_count

    rec.stop()

    assert final_calls > initial_calls, (
        f"get_positions should be called more after OrderStateChanged; "
        f"initial={initial_calls}, final={final_calls}"
    )

    store.close()
    print("  OK RC4: OrderStateChanged event triggers reconcile_once()")


# ─────────────────────────────────────────────────────────────────────────────
# RC14: startup reconciliation
# ─────────────────────────────────────────────────────────────────────────────

def test_rc14_startup_reconciliation(tmp_path: Path) -> None:
    """RC14: start() performs startup reconciliation before poll thread begins."""
    store = _make_store(tmp_path)

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm,
                           poll_interval_sec=60, enable_event_driven=False)

    assert adapter.get_positions.call_count == 0, "No calls before start()"
    rec.start()

    # Startup reconciliation should have run immediately
    assert adapter.get_positions.call_count >= 1, "Startup reconcile must call get_positions"

    rec.stop()
    store.close()
    print("  OK RC14: start() triggers startup reconciliation before thread begins")


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency: second reconcile doesn't re-fire for already-handled trades
# ─────────────────────────────────────────────────────────────────────────────

def test_manual_close_idempotent(tmp_path: Path) -> None:
    """MANUAL_CLOSE result is idempotent: second cycle on CLOSED_MANUAL trade is no-op."""
    store = _make_store(tmp_path)
    _insert_trade(store, "t1", symbol="RELIANCE", status="OPEN")
    _insert_order(store, "ord1", "t1", leg="ENTRY", product="MIS", status="COMPLETE")

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm)

    # First cycle: marks trade CLOSED_MANUAL
    actions1 = rec.reconcile_once()
    mc1 = [a for a in actions1 if a.check_name == "MANUAL_CLOSE"]
    assert len(mc1) == 1

    # Second cycle: trade is now CLOSED_MANUAL — get_all_open_trades() won't return it
    actions2 = rec.reconcile_once()
    mc2 = [a for a in actions2 if a.check_name == "MANUAL_CLOSE"]
    assert mc2 == [], f"Second cycle must not re-fire MANUAL_CLOSE; got {mc2}"

    store.close()
    print("  OK idempotency: second reconcile cycle does not re-fire MANUAL_CLOSE")


# ─────────────────────────────────────────────────────────────────────────────
# Empty state: reconcile_once() on empty DB returns []
# ─────────────────────────────────────────────────────────────────────────────

def test_reconcile_empty_db_returns_empty(tmp_path: Path) -> None:
    """reconcile_once() on empty DB with no broker positions returns []."""
    store = _make_store(tmp_path)

    adapter = MagicMock()
    adapter.get_positions.return_value = []
    adapter.get_margins.return_value = _MarginInfo(net=100_000.0, available=80_000.0, used=20_000.0)

    fm = MagicMock()
    snap = MagicMock(); snap.total = 100_000.0
    fm.get_snapshot.return_value = snap

    rec = _make_reconciler(store, adapter=adapter, fund_manager=fm)
    actions = rec.reconcile_once()

    assert actions == [], f"Expected [] on empty DB; got {actions}"
    store.close()
    print("  OK reconcile_once on empty DB returns []")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_import_and_instantiate,
        test_logger_name,
        # CHECK 3: HEALTHY
        test_check3_healthy,
        # CHECK 1: MANUAL_CLOSE
        test_check1_manual_close,
        test_check1_manual_close_releases_capital,
        # BL-10b: MANUAL_CLOSE publishes PositionClosed
        test_manual_close_publishes_position_closed,
        test_manual_close_position_closed_has_breakeven_exit_price,
        test_manual_close_publish_failure_does_not_raise,
        test_manual_close_position_closed_source_module_is_reconciler,
        test_manual_close_for_short_publishes_position_closed,
        test_manual_close_skips_publish_when_entry_price_missing,
        # CHECK 2: ORPHAN_ADOPTION
        test_check2_orphan_adoption,
        # CHECK 4: PARTIAL_CLOSE
        test_check4_partial_close,
        # CHECK 5: POSITION_GREW
        test_check5_position_grew,
        # CHECK 6: ORPHAN_ORDER
        test_check6_orphan_order_detected,
        test_check6_skipped_when_no_broker_orders_fn,
        # G5b: CRASH_RECOVERY_SL
        test_g5b_long_ltp_above_sl_places_slm,
        test_g5b_long_ltp_below_sl_places_market,
        test_g5b_short_ltp_below_sl_places_slm,
        test_g5b_short_ltp_above_sl_places_market,
        test_g5b_place_order_timeout_returns_failure_action,
        test_g5b_skipped_when_sl_order_exists,
        # G3: CAPITAL_DRIFT
        test_g3_capital_drift_exceeds_tolerance,
        test_g3_capital_drift_within_tolerance,
        test_g3_get_margins_timeout_skips_check,
        # Error handling: RC11, RC12
        test_rc11_get_positions_timeout_skips_checks_1_to_5,
        test_rc12_three_consecutive_auth_errors_trigger_soft_kill,
        test_rc12_counter_resets_on_success,
        # RC10: reconciliation_log persistence
        test_rc10_non_cosmetic_actions_persisted,
        test_rc10_cosmetic_actions_not_persisted,
        # RC13: non-reentrant
        test_rc13_concurrent_cycle_returns_empty,
        # RC4: event-driven
        test_rc4_order_state_changed_triggers_reconcile,
        # RC14: startup reconciliation
        test_rc14_startup_reconciliation,
        # Idempotency
        test_manual_close_idempotent,
        # Empty state
        test_reconcile_empty_db_returns_empty,
    ]

    print("=" * 70)
    print("order_reconciler.py -- Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            try:
                test(Path(td))
            except AssertionError as e:
                failed.append((test.__name__, f"AssertionError: {e}"))
                print(f"  FAIL FAIL: {e}")
            except Exception as e:
                failed.append((test.__name__, f"{type(e).__name__}: {e}"))
                print(f"  FAIL ERROR: {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1

    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
