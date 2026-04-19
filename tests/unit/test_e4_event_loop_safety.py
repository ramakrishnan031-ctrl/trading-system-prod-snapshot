"""
tests/unit/test_e4_event_loop_safety.py

Phase E.4 tests: event-loop safety + schema v10->v11 (M-3 + EF-5) + M-1
TOCTOU closure + M-4 pipeline error shape.

Covers:
  H-7   post_wire_init defers the EOD recovery check until bus subscribers
        are registered (2 tests).
  H-13  shadow_tracker._close_inning: DB failure leaves inning active,
        does not pop, does not cascade (2 tests).
  H-16  webhook_receiver: stop() triggers 503 on new requests; shutdown
        order is webhook BEFORE signal_processor (2 tests).
  M-1   _claim_in_flight is atomic under concurrent same-symbol requests
        (1 test).
  M-3   EOD write-ahead: IN_PROGRESS row -> COMPLETE transition; recovery
        branch on restart with IN_PROGRESS; skip branch on COMPLETE;
        recovery failure leaves IN_PROGRESS + alerts (4 tests).
  M-4   _derive_prices raises _PipelineReject, not ValueError (1 test).
  EF-5  trades row persists reservation_id; rehydrate reads it from
        trades directly (2 tests).
  Schema v11: status column on eod_squareoff_log; reservation_id column
        on trades (2 tests).

Run: python -m pytest tests/unit/test_e4_event_loop_safety.py -v
"""
from __future__ import annotations

import logging
import queue
import sys
import tempfile
import threading
import time
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.order_state_machine import OrderStateMachine
from capital.kill_switch import KillState
from core.events import EodSquareoffComplete, EventBus, PositionClosed
from core.market_windows import MarketWindows
from core.state_store import StateStore, EXPECTED_SCHEMA_VERSION
from orders.eod_squareoff import EodSquareoff
from orders.shadow_tracker import Inning, ShadowTracker
from signals.signal_processor import _PipelineReject
from signals.webhook_receiver import WebhookReceiver


_IST = timezone(timedelta(hours=5, minutes=30), "IST")
_FIXED_DATE = date(2026, 4, 20)  # Monday, trading day


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ist(h: int, m: int, s: int = 0) -> datetime:
    return datetime(
        _FIXED_DATE.year, _FIXED_DATE.month, _FIXED_DATE.day,
        h, m, s, tzinfo=_IST,
    )


def _make_mock_store() -> MagicMock:
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = []
    store.get_eod_squareoff_log_for_date.return_value = None
    return store


def _build_eod(
    store: Optional[MagicMock] = None,
    notifier: Optional[MagicMock] = None,
) -> tuple[EodSquareoff, MagicMock, MagicMock]:
    if store is None:
        store = _make_mock_store()

    adapter = MagicMock(spec=["place_order", "cancel_order"])
    fm = MagicMock()
    ks = MagicMock()
    ks.is_active.return_value = False
    ks.current_state.return_value = KillState.INACTIVE
    bus = MagicMock(spec=EventBus)
    osm = OrderStateMachine(bus=None)
    mw = MarketWindows()
    logger = logging.getLogger("test_e4")

    eod = EodSquareoff(
        adapter=adapter,
        state_store=store,
        fund_manager=fm,
        state_machine=osm,
        bus=bus,
        market_windows=mw,
        time_authority=None,
        kill_switch=ks,
        logger=logger,
        notifier=notifier,
    )
    return eod, store, bus


# ─────────────────────────────────────────────────────────────────────────────
# H-7: post_wire_init defers recovery check
# ─────────────────────────────────────────────────────────────────────────────

def test_h7_check_restart_recovery_not_called_in_init() -> None:
    """EodSquareoff.__init__ must NOT invoke _check_restart_recovery."""
    store = _make_mock_store()
    # Arm: if recovery fired, fm.reset_daily_pnl would be called. Keep this
    # assertion lightweight by spying on the recovery method itself.
    with patch.object(
        EodSquareoff, "_check_restart_recovery",
    ) as spy:
        _build_eod(store=store)
        assert spy.call_count == 0, (
            "H-7: _check_restart_recovery must not run during __init__"
        )


def test_h7_check_restart_recovery_called_by_post_wire_init() -> None:
    """post_wire_init() must invoke _check_restart_recovery exactly once."""
    store = _make_mock_store()
    with patch.object(
        EodSquareoff, "_check_restart_recovery",
    ) as spy:
        eod, *_ = _build_eod(store=store)
        assert spy.call_count == 0
        eod.post_wire_init()
        assert spy.call_count == 1, (
            "H-7: post_wire_init must run _check_restart_recovery"
        )


# ─────────────────────────────────────────────────────────────────────────────
# H-13: shadow_tracker _close_inning early return on DB failure
# ─────────────────────────────────────────────────────────────────────────────

class _FakeTA:
    def __init__(self) -> None:
        self._ts = datetime(2026, 4, 20, 10, 30, 0)

    def now_ist(self) -> datetime:
        return self._ts

    def today_ist(self) -> str:
        return self._ts.strftime("%Y-%m-%d")


class _FakeMW:
    def is_market_open(self, now: datetime) -> bool:
        return True


def _make_shadow_tracker(tmp_path: Path, strategies: Optional[dict] = None) -> tuple[ShadowTracker, StateStore]:
    store = StateStore(tmp_path / "e4.db")
    bus = EventBus()
    st = ShadowTracker(
        state_store=store,
        bus=bus,
        live_feed=object(),
        market_windows=_FakeMW(),
        time_authority=_FakeTA(),
        notifier=None,
        strategies=strategies,
        max_innings=3,
        alert_per_inning=False,
        enabled=True,
    )
    return st, store


def _seed_trade_for_shadow(store: StateStore, trade_id: str) -> None:
    now_s = "2026-04-20T09:35:00"
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals
              (signal_id, symbol, scanner, strategy, triggered_at, received_at,
               expires_at, status, fingerprint, fingerprint_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("sig_" + trade_id, "RELIANCE", "sc", "strategy1",
             now_s, now_s, now_s, "TRADED",
             "fp_" + trade_id, "2026-04-20"),
        )
        cur.execute(
            """
            INSERT INTO trades
              (trade_id, signal_id, symbol, direction, strategy, sector,
               qty_planned, qty_filled, entry_target_price, entry_actual_price,
               sl_initial, tgt_initial, margin_reserved, risk_amount,
               created_at, status, order_protocol, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (trade_id, "sig_" + trade_id, "RELIANCE", "LONG", "strategy1",
             "ENERGY", 10, 10, 2500.0, 2500.0,
             2450.0, 2600.0, 5000.0, 500.0, now_s, "OPEN", "LIMIT_TRIPLE",
             now_s),
        )


def test_h13_inning_close_db_failure_leaves_inning_active(tmp_path: Path) -> None:
    """DB failure in _close_inning must keep the inning in _active_innings."""
    st, store = _make_shadow_tracker(tmp_path)
    _seed_trade_for_shadow(store, "t_h13_a")

    inning = Inning(
        inning_number=1,
        trade_id="t_h13_a",
        symbol="RELIANCE",
        direction="LONG",
        entry_price=2500.0,
        entry_ts=datetime(2026, 4, 20, 10, 0, 0),
        sl_price=2450.0,
        tgt_price=2600.0,
        exit_price=None, exit_ts=None, exit_reason=None,
        duration_sec=None, pnl_pct=None, pnl_per_share=None,
        is_real=True,
    )
    with st._lock:
        st._active_innings["t_h13_a"] = inning

    # Force update_inning_close to raise
    store.update_inning_close = MagicMock(side_effect=RuntimeError("db broken"))

    st._close_inning(inning, exit_price=2450.0, exit_reason="SL")

    with st._lock:
        assert "t_h13_a" in st._active_innings, (
            "H-13: failed DB write must not pop the inning"
        )
    store.close()


def test_h13_inning_close_db_failure_does_not_cascade_inning_2(tmp_path: Path) -> None:
    """DB failure must not cascade to start inning 2 (no new inning persisted)."""
    strategies = {
        "strategy1": types.SimpleNamespace(
            direction="LONG", entry_method="LIMIT",
            entry_offset_pct=0.0, sl_method="FIXED_PCT",
            sl_pct=0.02, sl_min_pct=0.003, sl_max_pct=0.05,
            tgt_method="FIXED_PCT", tgt_pct=0.04,
            tgt_risk_reward=2.0,
        ),
    }
    st, store = _make_shadow_tracker(tmp_path, strategies=strategies)
    _seed_trade_for_shadow(store, "t_h13_b")

    inning = Inning(
        inning_number=1,
        trade_id="t_h13_b",
        symbol="RELIANCE",
        direction="LONG",
        entry_price=2500.0,
        entry_ts=datetime(2026, 4, 20, 10, 0, 0),
        sl_price=2450.0,
        tgt_price=2600.0,
        exit_price=None, exit_ts=None, exit_reason=None,
        duration_sec=None, pnl_pct=None, pnl_per_share=None,
        is_real=True,
    )
    store.insert_inning(inning)
    with st._lock:
        st._active_innings["t_h13_b"] = inning

    # Fail update_inning_close to trigger H-13 branch
    store.update_inning_close = MagicMock(side_effect=RuntimeError("db broken"))

    st._close_inning(inning, exit_price=2450.0, exit_reason="SL")

    # No second inning must have been persisted
    rows = store.get_innings_for_trade("t_h13_b")
    inning_numbers = [r["inning_number"] for r in rows]
    assert inning_numbers == [1], (
        f"H-13: cascade must be suppressed on DB failure, got innings={inning_numbers}"
    )
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# H-16: webhook_receiver 503 after stop(), shutdown order
# ─────────────────────────────────────────────────────────────────────────────

class _NullLogger:
    def debug(self, *a, **kw): pass
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def critical(self, *a, **kw): pass


def _make_wr(tmp_path: Path) -> tuple[WebhookReceiver, StateStore, queue.Queue]:
    store = StateStore(tmp_path / "e4_wr.db")
    sq: queue.Queue = queue.Queue(maxsize=20)
    cfg = types.SimpleNamespace()
    cfg.signal_queue = types.SimpleNamespace(
        capacity=20, backpressure_pct=0.8, expiry_sec=60,
    )
    cfg.scan_webhook_map = types.SimpleNamespace(
        scanners={"gap_go_long": "strategies/gap_go_long.yaml"},
    )
    mw = types.SimpleNamespace(is_entry_allowed=lambda now: True)
    ks = types.SimpleNamespace(is_active=lambda intent="entry": False)
    receiver = WebhookReceiver(
        sq, store, cfg, mw, ks, _NullLogger(), secret_token=None,
    )
    return receiver, store, sq


def test_h16_webhook_receiver_returns_503_after_stop(tmp_path: Path) -> None:
    """Once stop() is called, new webhook requests receive 503."""
    receiver, store, _ = _make_wr(tmp_path)
    receiver.stop()
    payload = {
        "stocks": "RELIANCE",
        "trigger_prices": "2500.0",
        "triggered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scan_name": "gap_go_long",
    }
    with receiver.app.test_client() as client:
        resp = client.post("/webhook/gap_go_long", json=payload)
    assert resp.status_code == 503, (
        f"H-16: expected 503 after stop(), got {resp.status_code}"
    )
    body = resp.get_json()
    assert "shutting down" in body["error"].lower()
    store.close()


def test_h16_shutdown_order_webhook_before_signal_proc() -> None:
    """main._shutdown must call webhook_receiver.stop() BEFORE signal_proc.stop()."""
    import main  # import here to avoid top-level side effects
    order: list[str] = []

    wr = MagicMock()
    wr.stop = MagicMock(side_effect=lambda: order.append("webhook"))
    sp = MagicMock()
    sp.stop = MagicMock(side_effect=lambda: order.append("signal_proc"))
    eg = MagicMock(); eg.stop = MagicMock()
    st = MagicMock(); st.stop = MagicMock()
    orec = MagicMock(); orec.stop = MagicMock()
    omon = MagicMock(); omon.stop = MagicMock()
    lf = MagicMock(); lf.stop = MagicMock()
    cs = MagicMock(); cs.stop = MagicMock()
    notifier = MagicMock()
    store = MagicMock(); store.close = MagicMock()

    main._shutdown(
        signal_proc=sp,
        entry_gate=eg,
        smart_tgt=st,
        order_reconciler=orec,
        order_monitor=omon,
        live_feed=lf,
        candle_store=cs,
        notifier=notifier,
        store=store,
        webhook_receiver=wr,
        clock_skew_probe=None,
    )

    assert order.index("webhook") < order.index("signal_proc"), (
        f"H-16: webhook.stop must precede signal_proc.stop; order={order}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# M-1: _claim_in_flight TOCTOU closure
# ─────────────────────────────────────────────────────────────────────────────

def test_m1_claim_in_flight_atomic_under_concurrent_same_symbol(tmp_path: Path) -> None:
    """
    With N concurrent claims for the same symbol, exactly 1 must win.
    The remaining N-1 must see False (already claimed).
    """
    receiver, store, _ = _make_wr(tmp_path)
    n_threads = 50
    results: list[bool] = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker() -> None:
        barrier.wait()  # release all threads simultaneously
        claimed = receiver._claim_in_flight("RELIANCE")
        with results_lock:
            results.append(claimed)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    wins = sum(1 for r in results if r)
    assert wins == 1, (
        f"M-1: exactly one thread must win the atomic claim, got {wins} wins"
    )
    assert len(results) == n_threads
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# M-3: EOD write-ahead IN_PROGRESS -> COMPLETE + recovery flow
# ─────────────────────────────────────────────────────────────────────────────

def test_m3_eod_writes_in_progress_then_complete() -> None:
    """_fire() must write IN_PROGRESS at start and UPDATE to COMPLETE at end."""
    store = _make_mock_store()
    call_order: list[str] = []

    def _start(*a, **kw):
        call_order.append("in_progress")

    def _complete(*a, **kw):
        call_order.append("complete")

    store.insert_eod_squareoff_log_start.side_effect = _start
    store.update_eod_squareoff_log_complete.side_effect = _complete

    eod, *_ = _build_eod(store=store)
    eod.fire_now(reason="test", triggered_by="unit")

    assert call_order == ["in_progress", "complete"], (
        f"M-3: expected [in_progress, complete], got {call_order}"
    )


def test_m3_restart_with_in_progress_fires_recovery() -> None:
    """Restart with status=IN_PROGRESS row must recovery-fire."""
    store = _make_mock_store()
    store.get_eod_squareoff_log_for_date.return_value = {
        "fired_date": "2026-04-20",
        "fired_at": "2026-04-20T15:17:00",
        "positions_attempted": 0, "positions_succeeded": 0,
        "positions_failed": 0,
        "cancels_attempted": 0, "cancels_succeeded": 0,
        "cancels_failed": 0, "duration_sec": 0.0,
        "status": "IN_PROGRESS",
    }

    # Recovery-fire uses now_ist() as its "now" -- patch to a time post-EOD
    with patch("orders.eod_squareoff.now_ist", return_value=_ist(15, 20)):
        eod, store, _ = _build_eod(store=store)
        # Recovery fires in post_wire_init; check that complete was written
        eod.post_wire_init()

    assert store.insert_eod_squareoff_log_start.call_count >= 1, (
        "M-3: recovery-fire must write a fresh IN_PROGRESS row"
    )
    assert store.update_eod_squareoff_log_complete.call_count >= 1, (
        "M-3: successful recovery must transition row to COMPLETE"
    )


def test_m3_restart_with_complete_skips_eod() -> None:
    """Restart with status=COMPLETE row must skip recovery (no fire)."""
    store = _make_mock_store()
    store.get_eod_squareoff_log_for_date.return_value = {
        "fired_date": "2026-04-20",
        "fired_at": "2026-04-20T15:17:00",
        "positions_attempted": 3, "positions_succeeded": 3,
        "positions_failed": 0,
        "cancels_attempted": 1, "cancels_succeeded": 1,
        "cancels_failed": 0, "duration_sec": 2.4,
        "status": "COMPLETE",
    }

    with patch("orders.eod_squareoff.now_ist", return_value=_ist(15, 20)):
        eod, store, _ = _build_eod(store=store)
        eod.post_wire_init()

    assert store.insert_eod_squareoff_log_start.call_count == 0, (
        "M-3: COMPLETE status must not trigger a new write-ahead"
    )
    assert store.update_eod_squareoff_log_complete.call_count == 0, (
        "M-3: COMPLETE status must not trigger a new fire"
    )


def test_m3_recovery_failure_leaves_in_progress_alerts() -> None:
    """
    If recovery-fire itself raises, the IN_PROGRESS row stays and the
    notifier.send(severity='CRITICAL', ...) path is invoked.
    """
    store = _make_mock_store()
    store.get_eod_squareoff_log_for_date.return_value = {
        "fired_date": "2026-04-20",
        "fired_at": "2026-04-20T15:17:00",
        "positions_attempted": 0, "positions_succeeded": 0,
        "positions_failed": 0,
        "cancels_attempted": 0, "cancels_succeeded": 0,
        "cancels_failed": 0, "duration_sec": 0.0,
        "status": "IN_PROGRESS",
    }

    notifier = MagicMock()

    with patch("orders.eod_squareoff.now_ist", return_value=_ist(15, 20)):
        eod, store, _ = _build_eod(store=store, notifier=notifier)
        # Force _fire to raise so recovery fails
        eod._fire = MagicMock(side_effect=RuntimeError("recovery broken"))
        eod.post_wire_init()

    # COMPLETE update must NOT run (recovery failed before that step)
    assert store.update_eod_squareoff_log_complete.call_count == 0, (
        "M-3: recovery failure must not UPDATE to COMPLETE"
    )
    notifier.send.assert_called_once()
    kwargs = notifier.send.call_args.kwargs
    assert kwargs.get("severity") == "CRITICAL"


# ─────────────────────────────────────────────────────────────────────────────
# M-4: _derive_prices raises _PipelineReject (not ValueError)
# ─────────────────────────────────────────────────────────────────────────────

def test_m4_invalid_derived_price_raises_pipeline_reject() -> None:
    """_derive_prices with entry<=0 must raise _PipelineReject, not ValueError."""
    from signals.signal_processor import SignalProcessor
    strategy = types.SimpleNamespace(
        direction="LONG",
        entry_method="LIMIT",
        entry_offset_pct=1.5,  # offset>1 forces entry<=0
        sl_method="FIXED_PCT",
        sl_pct=0.02, sl_min_pct=0.003, sl_max_pct=0.05,
    )
    sp = SignalProcessor.__new__(SignalProcessor)
    sp._log = logging.getLogger("test_m4")
    sp._atr_fallback_mode = "WARN"

    with pytest.raises(_PipelineReject) as excinfo:
        sp._derive_prices(100.0, strategy)

    assert excinfo.value.check == "INVALID_DERIVED_PRICE"
    assert "entry_price=" in excinfo.value.reason


# ─────────────────────────────────────────────────────────────────────────────
# EF-5: reservation_id flows to trades row + rehydrate reads it
# ─────────────────────────────────────────────────────────────────────────────

def test_ef5_trades_row_persists_reservation_id(tmp_path: Path) -> None:
    """create_trade with reservation_id must store it in the trades row."""
    from orders.order_manager import OrderManager
    store = StateStore(tmp_path / "ef5.db")
    bus = MagicMock(spec=EventBus)
    om = OrderManager(
        state_store=store,
        logger=logging.getLogger("test_ef5a"),
        bus=bus,
    )

    # Seed signal row so FK is satisfied
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals
              (signal_id, symbol, scanner, strategy, triggered_at, received_at,
               expires_at, status, fingerprint, fingerprint_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("sig_ef5", "RELIANCE", "sc", "strategy1",
             "2026-04-20T09:35:00", "2026-04-20T09:35:00",
             "2026-04-20T09:35:00", "QUEUED",
             "fp_ef5", "2026-04-20"),
        )

    trade_id = om.create_trade(
        signal_id="sig_ef5",
        symbol="RELIANCE",
        direction="LONG",
        strategy="strategy1",
        sector="ENERGY",
        qty=10,
        entry_target_price=2500.0,
        sl_initial=2450.0,
        tgt_initial=2600.0,
        order_protocol="LIMIT_TRIPLE",
        margin_reserved=5000.0,
        risk_amount=500.0,
        reservation_id="deadbeefcafef00d",
    )
    row = store.fetch_one(
        "SELECT reservation_id FROM trades WHERE trade_id = ?", (trade_id,)
    )
    assert row is not None
    assert row["reservation_id"] == "deadbeefcafef00d"
    store.close()


def test_ef5_rehydrate_reads_reservation_id_from_trades(tmp_path: Path) -> None:
    """get_all_open_trades must surface trades.reservation_id for rehydrate."""
    store = StateStore(tmp_path / "ef5b.db")

    # Seed one open trade with an explicit reservation_id
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals
              (signal_id, symbol, scanner, strategy, triggered_at, received_at,
               expires_at, status, fingerprint, fingerprint_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("sig_ef5b", "RELIANCE", "sc", "strategy1",
             "2026-04-20T09:35:00", "2026-04-20T09:35:00",
             "2026-04-20T09:35:00", "TRADED",
             "fp_ef5b", "2026-04-20"),
        )
        cur.execute(
            """
            INSERT INTO trades
              (trade_id, signal_id, symbol, direction, strategy, sector,
               qty_planned, qty_filled, entry_target_price, entry_actual_price,
               sl_initial, tgt_initial, margin_reserved, risk_amount,
               created_at, status, order_protocol, updated_at,
               reservation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("t_ef5b", "sig_ef5b", "RELIANCE", "LONG", "strategy1", "ENERGY",
             10, 10, 2500.0, 2500.0, 2450.0, 2600.0,
             5000.0, 500.0, "2026-04-20T09:35:00",
             "OPEN", "LIMIT_TRIPLE", "2026-04-20T09:35:00",
             "feed1234beefcafe"),
        )

    rows = store.get_all_open_trades()
    assert len(rows) == 1
    # Row must expose reservation_id (EF-5 additive select column)
    assert rows[0]["reservation_id"] == "feed1234beefcafe"
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# Schema v11 smoke tests
# ─────────────────────────────────────────────────────────────────────────────

def test_schema_v11_has_status_column(tmp_path: Path) -> None:
    """eod_squareoff_log must have status + completed_at columns in v11."""
    store = StateStore(tmp_path / "sv11a.db")
    # schema_meta version check
    row = store.fetch_one("SELECT value FROM schema_meta WHERE key='schema_version'")
    assert row is not None
    assert int(row["value"]) == EXPECTED_SCHEMA_VERSION == 11

    # Column existence
    cols = {
        r["name"] for r in store.fetch_all("PRAGMA table_info(eod_squareoff_log)")
    }
    assert "status" in cols, f"v11 missing status column; cols={cols}"
    assert "completed_at" in cols, f"v11 missing completed_at column; cols={cols}"
    store.close()


def test_schema_v11_has_reservation_id_column(tmp_path: Path) -> None:
    """trades must have reservation_id column in v11."""
    store = StateStore(tmp_path / "sv11b.db")
    cols = {
        r["name"] for r in store.fetch_all("PRAGMA table_info(trades)")
    }
    assert "reservation_id" in cols, (
        f"v11 missing trades.reservation_id column; cols={cols}"
    )
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback

    # Pytest-style tests that take tmp_path need a Path from tempfile
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td)

        simple = [
            test_h7_check_restart_recovery_not_called_in_init,
            test_h7_check_restart_recovery_called_by_post_wire_init,
            test_h16_shutdown_order_webhook_before_signal_proc,
            test_m3_eod_writes_in_progress_then_complete,
            test_m3_restart_with_in_progress_fires_recovery,
            test_m3_restart_with_complete_skips_eod,
            test_m3_recovery_failure_leaves_in_progress_alerts,
            test_m4_invalid_derived_price_raises_pipeline_reject,
        ]
        needs_tmp = [
            test_h13_inning_close_db_failure_leaves_inning_active,
            test_h13_inning_close_db_failure_does_not_cascade_inning_2,
            test_h16_webhook_receiver_returns_503_after_stop,
            test_m1_claim_in_flight_atomic_under_concurrent_same_symbol,
            test_ef5_trades_row_persists_reservation_id,
            test_ef5_rehydrate_reads_reservation_id_from_trades,
            test_schema_v11_has_status_column,
            test_schema_v11_has_reservation_id_column,
        ]

        passed = 0
        failed: list[str] = []
        for fn in simple:
            try:
                fn()
                print(f"  PASS  {fn.__name__}")
                passed += 1
            except Exception:
                print(f"  FAIL  {fn.__name__}")
                traceback.print_exc()
                failed.append(fn.__name__)
        for i, fn in enumerate(needs_tmp):
            sub = tp / f"sub_{i}"
            sub.mkdir()
            try:
                fn(sub)
                print(f"  PASS  {fn.__name__}")
                passed += 1
            except Exception:
                print(f"  FAIL  {fn.__name__}")
                traceback.print_exc()
                failed.append(fn.__name__)

        total = len(simple) + len(needs_tmp)
        print(f"\n{passed}/{total} passed")
        if failed:
            print("FAILED:", failed)
            sys.exit(1)
