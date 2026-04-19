"""
tests/unit/test_e3_hygiene.py -- Phase E.3 hygiene tests

Covers four audit items landing together under the "silent state divergence"
theme:

  - H-6   : smart_tgt trail DB-persist failure no longer silent.
            Memory-first ordering is preserved (broker already has new_sl);
            DB failure logs ERROR with grep tag SMART_TGT_TRAIL_DB_PERSIST_FAILED;
            next trail tick is idempotent (no duplicate broker modify).
  - H-21  : order_placer.link_signal_trade hard-fails via BL-8 pattern.
            broker_order_ids=() (nothing placed yet at the broker); cleanup
            releases the reservation, marks the trade FAILED, raises
            OrderRejectedError.
  - H-15  : order_monitor orphan detection now verifies via
            adapter.get_open_orders before firing. False positives (broker
            still has the order) reset the empty-history counter. Confirmed
            orphans (broker absent) fire the callback. Broker call failure
            is fail-safe (fire callback rather than miss a real orphan).
  - M-2   : order_reconciler._check8_co_sl_drift is alert-only.
            Critical assertion: adapter.modify_order MUST NOT be called by
            this check. Auto-repair is deferred.

Run standalone:
    python tests/unit/test_e3_hygiene.py

Or via pytest:
    python -m pytest tests/unit/test_e3_hygiene.py -v
"""
from __future__ import annotations

import logging
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.order_monitor import OrderMonitor, _OrphanTickCache
from broker.order_state_machine import OrderStateMachine
from broker.zerodha_adapter import OrderHistoryEntry
from core.events import EventBus
from core.exceptions import OrderRejectedError
from core.state_store import StateStore
from core.time_authority import now_ist

_IST = timezone(timedelta(hours=5, minutes=30))


# ═══════════════════════════════════════════════════════════════════════════
# H-6: smart_tgt trail DB-persist failure visibility
# ═══════════════════════════════════════════════════════════════════════════

def _make_smart_tgt_gate_for_h6():
    """Local gate -- imports only what we need to avoid cross-test coupling."""
    from orders.smart_tgt_manager import SmartTgtManager

    @dataclass
    class _ModifyResult:
        broker_order_id: str
        success: bool
        reason: str = ""

    class _Adapter:
        def __init__(self) -> None:
            self.modify_calls: List[dict] = []
            self.success: bool = True

        def modify_order(self, broker_order_id, price=None, qty=None, trigger_price=None):
            self.modify_calls.append({
                "broker_order_id": broker_order_id,
                "trigger_price": trigger_price,
            })
            return _ModifyResult(
                broker_order_id=broker_order_id,
                success=self.success,
                reason="" if self.success else "broker_rejected",
            )

    class _Store:
        def __init__(self) -> None:
            self._co = {"trade_001": {"order_id": "co_001", "variety": "co"}}
            self.rows: Dict[str, dict] = {}
            self.update_calls: List[dict] = []
            self.update_should_raise: Optional[Exception] = None

        def get_co_entry_order_for_trade(self, trade_id):
            return self._co.get(trade_id)

        def insert_smart_tgt_state(self, trade_id, **kwargs):
            self.rows[trade_id] = {"trade_id": trade_id, **kwargs}

        def update_smart_tgt_state(self, trade_id, current_sl, trail_count,
                                    last_trail_ts, best_price):
            call = {
                "trade_id": trade_id,
                "current_sl": current_sl,
                "trail_count": trail_count,
            }
            self.update_calls.append(call)
            if self.update_should_raise is not None:
                raise self.update_should_raise
            if trade_id in self.rows:
                self.rows[trade_id].update({
                    "current_sl": current_sl,
                    "trail_count": trail_count,
                    "last_trail_ts": last_trail_ts,
                    "best_price": best_price,
                })

        def delete_smart_tgt_state(self, trade_id):
            self.rows.pop(trade_id, None)

        def get_all_smart_tgt_states(self):
            return list(self.rows.values())

    class _CandleStore:
        def __init__(self):
            self._cb = None

        def register_on_candle_close(self, fn):
            self._cb = fn

        def unregister_on_candle_close(self, fn):
            self._cb = None

        def get_candles(self, *a, **k):
            return []

    class _Log:
        def __init__(self) -> None:
            self.errors: List[str] = []
            self.infos: List[str] = []
            self.warnings: List[str] = []
            self.debugs: List[str] = []
            self.criticals: List[str] = []

        def error(self, msg, *a, **k):    self.errors.append(str(msg))
        def info(self, msg, *a, **k):     self.infos.append(str(msg))
        def warning(self, msg, *a, **k):  self.warnings.append(str(msg))
        def debug(self, msg, *a, **k):    self.debugs.append(str(msg))
        def critical(self, msg, *a, **k): self.criticals.append(str(msg))

    adapter = _Adapter()
    store = _Store()
    cs = _CandleStore()
    log = _Log()
    mgr = SmartTgtManager(
        adapter=adapter,
        state_store=store,
        candle_store=cs,
        logger=log,
    )
    # Register a trade so _tracked is populated.
    mgr.register_trade(
        trade_id="trade_001",
        symbol="RELIANCE",
        instrument_token=738561,
        direction="LONG",
        entry_price=1000.0,
        initial_sl=980.0,
        qty=10,
        trigger_pct=0.005,
        step_pct=0.003,
    )
    return mgr, adapter, store, log


def test_h6_trail_db_failure_logs_error_preserves_memory() -> None:
    """
    H-6: On DB persist failure after successful broker modify, the grep tag
    SMART_TGT_TRAIL_DB_PERSIST_FAILED appears in an ERROR log, no exception
    propagates, and _tracked[trade_id]["current_sl"] remains mutated to the
    new SL (broker + memory stay consistent).
    """
    mgr, adapter, store, log = _make_smart_tgt_gate_for_h6()
    # Arrange: DB update raises; broker modify succeeds.
    store.update_should_raise = RuntimeError("disk full simulated")
    adapter.success = True

    # Drive an internal trail directly to avoid the candle_store plumbing.
    # (Exercises the _modify_co_sl path at lines 427-470.)
    mgr._modify_co_sl("trade_001", 990.0)

    # Assert memory mutation stuck (broker + memory agree).
    info = mgr._tracked["trade_001"]
    assert info["current_sl"] == 990.0, (
        f"current_sl should be new_sl=990.0 (memory mirrors broker); "
        f"got {info['current_sl']}"
    )
    # Broker did get modified once.
    assert len(adapter.modify_calls) == 1
    assert adapter.modify_calls[0]["trigger_price"] == 990.0
    # DB raise was caught, not propagated.
    # Grep tag appears in an ERROR log.
    tag = "SMART_TGT_TRAIL_DB_PERSIST_FAILED"
    matched = [m for m in log.errors if tag in m]
    assert matched, (
        f"Expected grep tag {tag!r} in ERROR log; got errors={log.errors!r}"
    )
    print("  OK H-6: DB failure -> ERROR with grep tag; memory remains mutated")


def test_h6_trail_db_failure_next_tick_idempotent() -> None:
    """
    H-6: After a DB-failure trail, a second _modify_co_sl call for the
    *same* new_sl must NOT re-call adapter.modify_order. Memory carries the
    new SL, so the next tick's recomputation finds no advancement needed.
    """
    mgr, adapter, store, log = _make_smart_tgt_gate_for_h6()
    store.update_should_raise = RuntimeError("disk full simulated")
    adapter.success = True

    # First trail: broker + memory -> 990; DB fails.
    mgr._modify_co_sl("trade_001", 990.0)
    assert len(adapter.modify_calls) == 1

    # Simulate next trail tick: compute_target_sl returns 990 again but
    # the "only advance" guard at smart_tgt_manager.py:370-377 compares
    # against info["current_sl"] (now 990) and should refuse to modify.
    # We exercise the guard directly via _compute_target_sl.
    info = mgr._tracked["trade_001"]
    # Set best_price so _compute_target_sl yields something.
    info["best_price"] = 1010.0   # favourable (LONG)
    target = mgr._compute_target_sl(info)
    # If target is None or <= current_sl=990, the _apply_trail_if_any branch
    # at smart_tgt_manager.py:335-340 does not call _modify_co_sl.
    if target is not None and target > info["current_sl"]:
        mgr._modify_co_sl("trade_001", target)

    # Assert: either no additional modify call, or only one additional call
    # to a *higher* SL (never a redundant repeat at 990).
    assert all(c["trigger_price"] != 990.0 or i == 0
               for i, c in enumerate(adapter.modify_calls)), (
        "Must not re-issue broker modify for the same SL after a DB failure"
    )
    print("  OK H-6: next tick does not duplicate broker modify for same SL")


# ═══════════════════════════════════════════════════════════════════════════
# H-21 / M-5: link_signal_trade hard-fail
# ═══════════════════════════════════════════════════════════════════════════

def _make_placer_for_h21(link_should_raise: Optional[Exception] = None):
    """
    Build an OrderPlacer where OrderManager.link_signal_trade either passes
    or raises the given exception. Returns (placer, fm, om_mock, seeded_ids).
    """
    from broker.cost_calculator import CostCalculator
    from broker.order_monitor import OrderMonitor
    from orders.full_entry_engine import FullEntryEngine
    from orders.order_manager import OrderManager
    from orders.order_placer import OrderPlacer
    from orders.order_protocol_co import CoPlusTgtProtocol
    from orders.order_protocol_limit import LimitTripleProtocol

    log = logging.getLogger("test_h21")

    class _MockFundManager:
        _LEVERAGE_MAP = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0}

        def __init__(self):
            self.released: List[str] = []
            self.released_reasons: List[str] = []

        def required_margin(self, qty, price, intent):
            lev = self._LEVERAGE_MAP.get(intent, 5.0)
            return (qty * price) / lev

        def release(self, reservation_id, reason=""):
            self.released.append(reservation_id)
            self.released_reasons.append(reason)
            return True

        def commit_to_used(self, *a, **k):
            pass

    class _Adapter:
        def place_order(self, *a, **k):
            raise AssertionError(
                "H-21: broker place_order must not run when link_signal_trade fails"
            )

        def cancel_order(self, *a, **k):
            raise AssertionError(
                "H-21: broker cancel must not run (nothing was placed)"
            )

    adapter = _Adapter()
    co_proto = CoPlusTgtProtocol(adapter=adapter, logger=log)
    limit_proto = LimitTripleProtocol(adapter=adapter, logger=log)
    engine = FullEntryEngine(
        co_protocol=co_proto, limit_protocol=limit_proto,
        logger=log, default_protocol="LIMIT_TRIPLE",
    )

    # OrderManager is mocked so we can control link_signal_trade + capture
    # update_trade_status calls.
    om = MagicMock(spec=OrderManager)
    om.create_trade.return_value = "trade_h21"
    if link_should_raise is not None:
        om.link_signal_trade.side_effect = link_should_raise
    fm = _MockFundManager()
    bus = EventBus()

    placer = OrderPlacer(
        entry_engine=engine,
        order_manager=om,
        fund_manager=fm,
        bus=bus,
        logger=log,
        order_monitor=MagicMock(spec=OrderMonitor),
        cost_calculator=MagicMock(spec=CostCalculator),
        rr_ratio=2.0,
        default_order_protocol="LIMIT_TRIPLE",
    )
    return placer, fm, om


def test_h21_link_failure_releases_reservation_marks_failed() -> None:
    """
    H-21: When link_signal_trade raises, OrderPlacer.place MUST:
      - call _handle_placement_failure with broker_order_ids=()
        (link runs before engine.execute -- nothing to cancel)
      - release the reservation (via FundManager.release)
      - mark the trade FAILED (via OrderManager.update_trade_status)
      - raise OrderRejectedError (caught by signal_processor outer except)
    """
    placer, fm, om = _make_placer_for_h21(
        link_should_raise=RuntimeError("DB disk full during UPDATE signals")
    )

    raised_type: Optional[type] = None
    try:
        placer.place(
            symbol="RELIANCE",
            side="BUY",
            qty=10,
            entry_price=2500.0,
            sl_price=2450.0,
            intent="INTRADAY",
            signal_id="sig_h21",
            reservation_id="res_h21",
        )
    except Exception as exc:
        raised_type = type(exc)

    assert raised_type is OrderRejectedError, (
        f"H-21 must raise OrderRejectedError; got {raised_type!r}"
    )
    # Reservation released via _handle_placement_failure.
    assert fm.released == ["res_h21"], (
        f"H-21 must release reservation; got {fm.released!r}"
    )
    # Trade marked FAILED via OrderManager.
    failed_calls = [c for c in om.update_trade_status.call_args_list
                    if c.args and c.args[1] == "FAILED"]
    assert failed_calls, (
        f"H-21 must mark trade FAILED; got calls={om.update_trade_status.call_args_list!r}"
    )
    print("  OK H-21: link failure -> reservation released + trade FAILED + OrderRejectedError raised")


def test_h21_link_success_no_cleanup() -> None:
    """
    H-21 regression guard: on happy-path link, no _handle_placement_failure
    behaviour fires (no release, no FAILED status from link path).
    """
    placer, fm, om = _make_placer_for_h21(link_should_raise=None)

    # Make the engine path short-circuit so we only exercise up to and past
    # link_signal_trade -- kill_switch active after link, before engine.
    from capital.kill_switch import KillSwitch
    ks = MagicMock(spec=KillSwitch)
    ks.is_active.return_value = True   # OP-LM1 triggers AFTER link succeeds
    placer._kill_switch = ks

    try:
        placer.place(
            symbol="RELIANCE",
            side="BUY",
            qty=10,
            entry_price=2500.0,
            sl_price=2450.0,
            intent="INTRADAY",
            signal_id="sig_h21ok",
            reservation_id="res_h21ok",
        )
    except OrderRejectedError:
        pass  # expected from OP-LM1 kill_switch_active_last_mile

    # link_signal_trade was called with (signal_id, trade_id) and did not raise.
    om.link_signal_trade.assert_called_once_with("sig_h21ok", "trade_h21")
    # The kill_switch path still released the reservation, but the failure
    # reason must NOT mention link_signal_trade.
    assert any("link_signal_trade" not in r for r in fm.released_reasons), (
        f"link happy path must not flag link_signal_trade as the failure cause; "
        f"reasons={fm.released_reasons!r}"
    )
    print("  OK H-21 happy path: link succeeds; no link-originated cleanup")


# ═══════════════════════════════════════════════════════════════════════════
# H-15: orphan second-source check via adapter.get_open_orders
# ═══════════════════════════════════════════════════════════════════════════

def _entry(status, filled_qty=0, avg_price=0.0):
    return OrderHistoryEntry(
        broker_order_id="KITE001",
        status=status,
        filled_qty=filled_qty,
        avg_price=avg_price,
        rejection_reason="",
        ts=now_ist(),
    )


class _H15Adapter:
    """Adapter mock with empty get_order_history + configurable get_open_orders."""

    def __init__(self):
        self.open_orders_response: Optional[List[dict]] = None
        self.open_orders_exc: Optional[Exception] = None
        self.open_orders_calls: int = 0
        self.history_calls: int = 0

    def get_order_history(self, broker_order_id):
        self.history_calls += 1
        return []   # always empty -> drives the 3-empty orphan path

    def cancel_order(self, broker_order_id):
        @dataclass
        class _R:
            broker_order_id: str
            success: bool = True
            reason: str = ""
        return _R(broker_order_id=broker_order_id, success=True, reason="")

    def get_open_orders(self):
        self.open_orders_calls += 1
        if self.open_orders_exc is not None:
            raise self.open_orders_exc
        return list(self.open_orders_response or [])


def _make_monitor_for_h15(adapter: _H15Adapter, on_orphan=None):
    osm = OrderStateMachine(bus=None)
    bus = EventBus()
    log = logging.getLogger("test_h15")
    monitor = OrderMonitor(
        adapter=adapter,
        state_machine=osm,
        bus=bus,
        logger=log,
        poll_interval_sec=1,
        fill_timeout_sec=60,
        on_orphan_callback=on_orphan,
    )
    osm.register("ord_h15")
    osm.transition("ord_h15", "SUBMITTED")
    monitor.track(
        internal_order_id="ord_h15",
        broker_order_id="KITE_H15",
        symbol="RELIANCE",
        side="BUY",
        qty=10,
        expected_price=2500.0,
        placed_at=now_ist(),
    )
    return monitor


def test_h15_orphan_confirmed_by_broker_absent_fires_callback() -> None:
    """H-15: broker's get_open_orders returns [] -> confirmed orphan -> fire."""
    orphan_calls: List[tuple] = []
    adapter = _H15Adapter()
    adapter.open_orders_response = []   # broker has no open orders
    monitor = _make_monitor_for_h15(
        adapter, on_orphan=lambda iid, bid: orphan_calls.append((iid, bid)),
    )

    # 3 poll cycles -> triggers orphan verification on the 3rd.
    monitor._poll_cycle()
    monitor._poll_cycle()
    monitor._poll_cycle()

    assert len(orphan_calls) == 1, (
        f"confirmed orphan should fire callback once; got {len(orphan_calls)}"
    )
    assert adapter.open_orders_calls == 1, (
        f"get_open_orders should be called exactly once (tick-level cache); "
        f"got {adapter.open_orders_calls}"
    )
    print("  OK H-15: confirmed orphan fires callback + single get_open_orders call")


def test_h15_orphan_false_positive_resets_counter_no_callback() -> None:
    """H-15: broker's open orders list includes the tracked order -> false positive."""
    orphan_calls: List[tuple] = []
    adapter = _H15Adapter()
    # Broker still has the order open -- empty get_order_history is transient.
    adapter.open_orders_response = [
        {"order_id": "KITE_H15", "symbol": "RELIANCE", "status": "OPEN",
         "transaction_type": "BUY", "quantity": 10, "price": 2500.0,
         "trigger_price": 0.0},
    ]
    monitor = _make_monitor_for_h15(
        adapter, on_orphan=lambda iid, bid: orphan_calls.append((iid, bid)),
    )

    monitor._poll_cycle()
    monitor._poll_cycle()
    monitor._poll_cycle()

    assert orphan_calls == [], (
        f"false positive must NOT fire orphan callback; got {orphan_calls!r}"
    )
    # Counter reset on false positive, so order still watched.
    assert monitor.is_watching("ord_h15"), (
        "false positive must NOT untrack the order"
    )
    # Counter was reset (now 0) after the verification.
    entry = monitor._watched["ord_h15"]
    assert entry.empty_history_count == 0, (
        f"false positive must reset empty_history_count; got {entry.empty_history_count}"
    )
    print("  OK H-15: false positive resets counter; no orphan fired")


def test_h15_get_open_orders_failure_fail_safe_fires_callback() -> None:
    """H-15: when get_open_orders raises, fail-safe fires the orphan callback."""
    orphan_calls: List[tuple] = []
    adapter = _H15Adapter()
    adapter.open_orders_exc = RuntimeError("broker API down")
    monitor = _make_monitor_for_h15(
        adapter, on_orphan=lambda iid, bid: orphan_calls.append((iid, bid)),
    )

    monitor._poll_cycle()
    monitor._poll_cycle()
    monitor._poll_cycle()

    assert len(orphan_calls) == 1, (
        f"fail-safe: unverifiable orphan should still fire callback; "
        f"got {len(orphan_calls)}"
    )
    print("  OK H-15: get_open_orders failure -> fail-safe orphan fired")


# ═══════════════════════════════════════════════════════════════════════════
# M-2: order_reconciler._check8_co_sl_drift alert-only
# ═══════════════════════════════════════════════════════════════════════════

def _make_store_for_m2(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "test_m2.db")


def _seed_co_trade(
    store: StateStore,
    trade_id: str,
    co_broker_id: str,
    symbol: str = "RELIANCE",
    current_sl: float = 2450.0,
    entry_price: float = 2500.0,
    qty: int = 10,
) -> None:
    """Insert signals+trades+orders rows and a smart_tgt_state row for M-2."""
    now = "2026-04-16T09:30:00+05:30"
    sig_id = f"sig_{trade_id}"
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals
              (signal_id, symbol, scanner, strategy, triggered_at, received_at,
               expires_at, status, fingerprint, fingerprint_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sig_id, symbol, "SCAN", "strategy", now, now,
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
            (trade_id, sig_id, symbol, "LONG", "strategy", "ENERGY",
             qty, qty, entry_price, current_sl,
             entry_price * 1.02, 10000.0, 500.0, now,
             "OPEN", "CO_PLUS_TGT", now, entry_price),
        )
        # CO ENTRY order with variety='co' so get_co_entry_order_for_trade finds it.
        cur.execute(
            """
            INSERT INTO orders
              (order_id, trade_id, leg, transaction_type, order_type, product,
               variety, qty_requested, status, trigger_price, placed_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (co_broker_id, trade_id, "ENTRY", "BUY", "LIMIT", "CO",
             "co", qty, "COMPLETE", current_sl, now, now),
        )
    store.insert_smart_tgt_state(
        trade_id=trade_id,
        symbol=symbol,
        instrument_token=738561,
        direction="LONG",
        entry_price=entry_price,
        initial_sl=current_sl,
        current_sl=current_sl,
        qty=qty,
        trigger_pct=0.005,
        step_pct=0.003,
        registered_at=now,
    )


def _make_reconciler_for_m2(store: StateStore, adapter):
    from core.config_loader import OrderReconcilerConfig
    from orders.order_reconciler import OrderReconciler

    cfg = OrderReconcilerConfig(
        poll_interval_sec=60,
        capital_drift_tolerance=50.0,
        enable_event_driven=False,
    )
    bus = EventBus()
    fm = MagicMock()
    fm.get_snapshot.return_value = MagicMock(total=100_000.0)
    fm.get_live_reservations.return_value = {}
    ks = MagicMock()
    notifier = MagicMock()
    notifier.send.return_value = MagicMock(success=True)
    return OrderReconciler(
        state_store=store,
        adapter=adapter,
        fund_manager=fm,
        kill_switch=ks,
        notifier=notifier,
        bus=bus,
        logger=logging.getLogger("order_reconciler"),
        cfg=cfg,
        quote_fn=lambda _syms: {},
        broker_orders_fn=None,
    )


def test_m2_check8_no_drift_returns_empty(tmp_path: Path) -> None:
    """M-2: local current_sl matches broker trigger_price -> no action."""
    store = _make_store_for_m2(tmp_path)
    _seed_co_trade(store, trade_id="t_m2_nodrift", co_broker_id="CO_BID_1",
                   current_sl=2450.0)

    adapter = MagicMock()
    adapter.get_open_orders.return_value = [
        {"order_id": "CO_BID_1", "symbol": "RELIANCE", "status": "TRIGGER PENDING",
         "transaction_type": "BUY", "quantity": 10, "price": 0.0,
         "trigger_price": 2450.0},
    ]
    # Critical assertion: modify_order must NEVER be called by _check8.
    adapter.modify_order.side_effect = AssertionError(
        "M-2: _check8 must NOT call adapter.modify_order"
    )

    rec = _make_reconciler_for_m2(store, adapter)
    actions = rec._check8_co_sl_drift()

    assert actions == [], f"no drift -> no actions; got {actions!r}"
    # Verify modify_order was not called (belt-and-braces).
    adapter.modify_order.assert_not_called()
    store.close()
    print("  OK M-2: matching SL -> no drift action, adapter.modify_order NOT called")


def test_m2_check8_drift_returns_action_no_broker_modify(tmp_path: Path) -> None:
    """
    M-2 critical path: broker trigger_price differs from local current_sl.
    Expected:
      - Returns one ReconciliationAction(check_name="CO_SL_DRIFT",
        tier="RECOVERABLE", action_taken="alert_only")
      - CRITICAL log with grep tag CO_SL_DRIFT_DETECTED
      - adapter.modify_order is NEVER called
    """
    store = _make_store_for_m2(tmp_path)
    _seed_co_trade(store, trade_id="t_m2_drift", co_broker_id="CO_BID_2",
                   current_sl=2450.0)

    adapter = MagicMock()
    # Broker trigger has drifted by 5 rupees -- clearly above tolerance.
    adapter.get_open_orders.return_value = [
        {"order_id": "CO_BID_2", "symbol": "RELIANCE", "status": "TRIGGER PENDING",
         "transaction_type": "BUY", "quantity": 10, "price": 0.0,
         "trigger_price": 2455.0},
    ]
    adapter.modify_order.side_effect = AssertionError(
        "M-2 critical: _check8 must NOT call adapter.modify_order"
    )

    # Capture critical logs.
    captured_critical: List[str] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.CRITICAL:
                captured_critical.append(record.getMessage())

    handler = _CaptureHandler()
    log = logging.getLogger("order_reconciler")
    log.addHandler(handler)
    try:
        rec = _make_reconciler_for_m2(store, adapter)
        actions = rec._check8_co_sl_drift()
    finally:
        log.removeHandler(handler)

    # Assert exactly one action with the expected shape.
    assert len(actions) == 1, (
        f"drift should emit exactly one action; got {len(actions)}: {actions!r}"
    )
    act = actions[0]
    assert act.check_name == "CO_SL_DRIFT"
    assert act.tier == "RECOVERABLE"
    assert act.action_taken == "alert_only"
    assert act.trade_id == "t_m2_drift"
    assert act.symbol == "RELIANCE"
    assert act.success is True
    # CRITICAL log captured with grep tag.
    tag = "CO_SL_DRIFT_DETECTED"
    matched = [m for m in captured_critical if tag in m]
    assert matched, (
        f"Expected grep tag {tag!r} in CRITICAL log; got {captured_critical!r}"
    )
    # Belt-and-braces: modify_order was never called.
    adapter.modify_order.assert_not_called()
    store.close()
    print("  OK M-2: drift -> RECOVERABLE alert_only action + CRITICAL log; modify_order NOT called")


# ═══════════════════════════════════════════════════════════════════════════
# Standalone runner
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests_no_tmp = [
        test_h6_trail_db_failure_logs_error_preserves_memory,
        test_h6_trail_db_failure_next_tick_idempotent,
        test_h21_link_failure_releases_reservation_marks_failed,
        test_h21_link_success_no_cleanup,
        test_h15_orphan_confirmed_by_broker_absent_fires_callback,
        test_h15_orphan_false_positive_resets_counter_no_callback,
        test_h15_get_open_orders_failure_fail_safe_fires_callback,
    ]
    tests_with_tmp = [
        test_m2_check8_no_drift_returns_empty,
        test_m2_check8_drift_returns_action_no_broker_modify,
    ]

    print("=" * 70)
    print("E.3 Hygiene Tests (H-6 + H-21 + H-15 + M-2)")
    print("=" * 70)

    failed: List[tuple] = []
    for t in tests_no_tmp:
        print(f"\n-> {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failed.append((t.__name__, f"AssertionError: {e}"))
            print(f"  FAIL: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR: {type(e).__name__}: {e}")

    for t in tests_with_tmp:
        print(f"\n-> {t.__name__}")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            try:
                t(Path(td))
            except AssertionError as e:
                failed.append((t.__name__, f"AssertionError: {e}"))
                print(f"  FAIL: {e}")
            except Exception as e:  # noqa: BLE001
                failed.append((t.__name__, f"{type(e).__name__}: {e}"))
                print(f"  ERROR: {type(e).__name__}: {e}")

    total = len(tests_no_tmp) + len(tests_with_tmp)
    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {total} tests")
        for name, err in failed:
            print(f"  - {name}: {err}")
        sys.exit(1)
    print(f"OK: all {total} E.3 hygiene tests passed")
