"""
tests/unit/test_mc8_async_hardkill.py — M-C8 (16-Jul-2026).

M-C8: `KillSwitch.hard_kill` ran the indestructible exit loop
(`_exit_all_trades_indestructible`, bounded by `_HARD_KILL_MAX_RETRY_HOURS=2.0`)
on the CALLER's thread. Every production caller is on the fill/commit path
(order_placer:1499/:3750, fund_manager:974/:2259, drift_handler:231, main:647),
so an emergency HARD_KILL could starve the fill/event pipeline for up to two hours
— exactly when the system most needs fills to process. The flatten was never the
problem; blocking the caller was.

Fix: the ADAPTER path (production — main.py always calls set_adapter) dispatches
the loop to a NON-DAEMON worker and returns immediately. The LEGACY cancel_fn path
stays synchronous, which is why every pre-existing test still passes untouched.

Coverage (ChatGPT's mandatory set (a)-(g)):
  (a) hard_kill RETURNS IMMEDIATELY while the flatten runs      [RED on old]
  (b) SINGLE-FLIGHT: a repeat/cross-thread hard_kill never spawns a 2nd worker
  (c) CONDITIONAL WRITES: a concurrent fill is never clobbered  [RED on old]
  (d) SHUTDOWN drains, or times out cleanly with a CRITICAL
  (e) the eod-self-exit CANNOT fire mid-flatten                 [RED on old]
  (f) FIX-180/181/190/H-4 + the legacy path unchanged
  (g) the IDLE -> RUNNING -> DRAINING -> COMPLETE state machine

Run: python -m pytest tests/unit/test_mc8_async_hardkill.py -v
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from broker.zerodha_adapter import CancelResult, PlacedOrder
from capital import kill_switch as ks_mod
from capital.kill_switch import (
    CancellationReport,
    FlattenState,
    KillState,
    KillSwitch,
)
from core.events import EventBus
from core.state_store import StateStore

_NOW = "2026-07-16T18:00:00+05:30"
_LOG = logging.getLogger("test_mc8")


# ─────────────────────────────────────────────────────────────────────────────
# Test doubles / helpers
# ─────────────────────────────────────────────────────────────────────────────

class _CapturingCritical(logging.Handler):
    """Collects CRITICAL records only."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.CRITICAL:
            self.messages.append(record.getMessage())


class _BlockingFlattenAdapter:
    """Broker double whose place_order BLOCKS on `gate`, holding the flatten
    mid-flight so a test can observe the world while it runs."""

    def __init__(self, symbol: str = "RELIANCE", qty: int = 100) -> None:
        self.symbol = symbol
        self.net_qty = qty
        self.gate = threading.Event()      # test releases the flatten
        self.entered = threading.Event()   # flatten reached the broker
        self.placed: list[dict] = []

    def get_positions(self):
        return [SimpleNamespace(symbol=self.symbol, qty=self.net_qty, product="MIS")]

    def get_quote_raw(self, instruments):
        return {k: {"last_price": 2450.0} for k in instruments}

    def cancel_order(self, oid):
        return CancelResult(broker_order_id=oid, success=True, reason="")

    def place_order(self, symbol, side, qty, order_type, price, intent, tag=None, **kw):
        self.entered.set()
        self.gate.wait(timeout=10)  # HOLD the flatten here
        self.placed.append({"side": side, "qty": qty})
        self.net_qty = 0            # flat now, so the sweep finds nothing
        return PlacedOrder(
            internal_order_id="int_1", broker_order_id="brk_1", symbol=symbol,
            side=side, qty=qty, price=price, order_type=order_type,
            product="MIS", status="SUBMITTED", ts=datetime.now(),
        )


def _seed_open_trade(store: StateStore, symbol: str = "RELIANCE",
                     qty: int = 100, trade_status: str = "OPEN",
                     sl_status: str | None = None) -> None:
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, symbol, scanner, strategy, triggered_at, "
            "received_at, expires_at, status, fingerprint, fingerprint_date) VALUES "
            "('sig_mc8', ?, 'gap_go_long', 'gap_go_long', ?, ?, ?, 'PROCESSING', "
            "'fp_mc8', ?)",
            (symbol, _NOW, _NOW, _NOW, _NOW[:10]),
        )
        cur.execute(
            "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
            "qty_planned, qty_filled, entry_target_price, entry_actual_price, "
            "sl_initial, tgt_initial, margin_reserved, risk_amount, created_at, "
            "entry_time, order_protocol, status, updated_at) VALUES "
            "('T_MC8', 'sig_mc8', ?, 'LONG', 'gap_go_long', ?, ?, 2500.0, 2500.0, "
            "2450.0, 2600.0, 5000.0, 500.0, ?, ?, 'LIMIT_TRIPLE', ?, ?)",
            (symbol, qty, qty, _NOW, _NOW, trade_status, _NOW),
        )
        cur.execute(
            "INSERT INTO orders (order_id, trade_id, leg, transaction_type, order_type, "
            "product, variety, qty_requested, status, placed_at, updated_at) VALUES "
            "('ENTRY_MC8', 'T_MC8', 'ENTRY', 'BUY', 'LIMIT', 'MIS', 'regular', ?, "
            "'COMPLETE', ?, ?)",
            (qty, _NOW, _NOW),
        )
        if sl_status is not None:
            cur.execute(
                "INSERT INTO orders (order_id, trade_id, leg, transaction_type, "
                "order_type, product, variety, qty_requested, status, placed_at, "
                "updated_at) VALUES "
                "('SL_MC8', 'T_MC8', 'SL', 'SELL', 'SL-M', 'MIS', 'regular', ?, ?, ?, ?)",
                (qty, sl_status, _NOW, _NOW),
            )


def _make_ks(store, adapter=None, cancel_fn=None) -> KillSwitch:
    return KillSwitch(
        state_store=store, bus=EventBus(), logger=_LOG,
        adapter=adapter, on_hard_kill_cancel_fn=cancel_fn, enable_auto_trip=False,
    )


def _trade_status(store, trade_id: str = "T_MC8") -> str:
    return store.fetch_one(
        "SELECT status FROM trades WHERE trade_id = ?", (trade_id,)
    )["status"]


def _order_status(store, order_id: str) -> str:
    return store.fetch_one(
        "SELECT status FROM orders WHERE order_id = ?", (order_id,)
    )["status"]


@pytest.fixture
def store(tmp_path):
    s = StateStore(str(tmp_path / "mc8.db"))
    yield s
    s.close()


# ─────────────────────────────────────────────────────────────────────────────
# (a) hard_kill RETURNS IMMEDIATELY — the whole point of M-C8
# ─────────────────────────────────────────────────────────────────────────────

def test_a_hard_kill_returns_immediately_while_the_flatten_runs(store):
    """RED ON OLD: hard_kill used to run the loop inline, so it would block here
    for the adapter's full 10s hold (and in production, up to 2h)."""
    _seed_open_trade(store)
    adapter = _BlockingFlattenAdapter()
    ks = _make_ks(store, adapter=adapter)

    t0 = time.monotonic()
    report = ks.hard_kill("emergency", "test")
    elapsed = time.monotonic() - t0

    try:
        # The caller is free while the broker call is still blocked.
        assert elapsed < 0.5, (
            f"hard_kill blocked the caller for {elapsed:.2f}s — the fill/commit "
            f"path is starved (this is the M-C8 defect)"
        )
        assert adapter.entered.wait(3.0), "the worker never reached the broker"
        assert ks.is_flatten_in_progress() is True
        assert report.dispatched is True, "the async return must declare itself"
        # The kill STATE is tripped synchronously — that must NOT have gone async.
        assert ks.current_state() is KillState.HARD_KILL
        assert ks.is_active("entry") is True
    finally:
        adapter.gate.set()
        ks.drain_flatten(timeout=10)


def test_a2_the_fill_thread_keeps_working_after_it_trips_a_hard_kill(store):
    """The actual defect, from the victim's point of view: a fill/commit-path
    thread trips hard_kill and must get straight back to processing fills.

    RED ON OLD: the calling thread was *inside* the 2h retry loop, so its next
    operation never ran — the fill pipeline was starved for the whole flatten.

    (Measuring is_active() only AFTER hard_kill returns proves nothing: on the old
    code hard_kill returns having already finished the flatten. The starvation is
    only visible by watching the caller's NEXT unit of work.)
    """
    _seed_open_trade(store)
    adapter = _BlockingFlattenAdapter()
    ks = _make_ks(store, adapter=adapter)

    fill_processed = threading.Event()

    def _fill_thread() -> None:
        ks.hard_kill("emergency", "order_placer")  # OLD: blocks here for the flatten
        ks.is_active("entry")                      # the next fill-path operation
        fill_processed.set()

    t = threading.Thread(target=_fill_thread, name="fill-thread")
    t.start()
    try:
        assert fill_processed.wait(2.0), (
            "the fill thread never got back to work after tripping hard_kill — it "
            "is stuck inside the flatten retry loop (the M-C8 defect)"
        )
        assert adapter.entered.wait(3.0), "the flatten is still genuinely running"
        assert ks.is_flatten_in_progress() is True
    finally:
        adapter.gate.set()
        ks.drain_flatten(timeout=10)
        t.join(10)


# ─────────────────────────────────────────────────────────────────────────────
# (b) SINGLE-FLIGHT
# ─────────────────────────────────────────────────────────────────────────────

def test_b_repeat_and_crossthread_hard_kill_never_spawns_a_second_worker(
    store, monkeypatch
):
    """Two concurrent flatten loops would race each other placing exits for the
    same positions. A repeat hard_kill must join/no-op, not start a second."""
    entered = threading.Event()
    release = threading.Event()
    entries: list[str] = []

    def _counting_flatten(self):
        entries.append(threading.current_thread().name)
        entered.set()
        release.wait(timeout=10)
        return CancellationReport(attempted=0, succeeded=0, failed=[])

    monkeypatch.setattr(
        KillSwitch, "_exit_all_trades_indestructible", _counting_flatten
    )
    ks = _make_ks(store, adapter=_BlockingFlattenAdapter())

    try:
        ks.hard_kill("first", "test")
        assert entered.wait(3.0), "worker never started"

        # Repeat on the SAME thread (the re-entrant already-HARD_KILL path)...
        ks.hard_kill("second", "test")
        # ...and from ANOTHER thread.
        t = threading.Thread(target=lambda: ks.hard_kill("third", "other"))
        t.start()
        t.join(5)

        assert len(entries) == 1, (
            f"single-flight violated: {len(entries)} flatten loops started "
            f"(threads={entries})"
        )
        assert entries[0] == "ks-hard-kill-flatten"
    finally:
        release.set()
        ks.drain_flatten(timeout=10)


def test_b2_a_new_hard_kill_after_completion_may_dispatch_again(store, monkeypatch):
    """Single-flight gates only what is IN FLIGHT. Once COMPLETE, a later
    emergency must still be able to flatten — otherwise the first hard_kill of
    the day would permanently disarm the mechanism."""
    calls: list[int] = []

    def _fast_flatten(self):
        calls.append(1)
        return CancellationReport(attempted=0, succeeded=0, failed=[])

    monkeypatch.setattr(KillSwitch, "_exit_all_trades_indestructible", _fast_flatten)
    ks = _make_ks(store, adapter=_BlockingFlattenAdapter())

    ks.hard_kill("first", "test")
    assert ks.drain_flatten(timeout=10)
    assert ks.flatten_state is FlattenState.COMPLETE

    ks.hard_kill("second", "test")
    assert ks.drain_flatten(timeout=10)
    assert len(calls) == 2, "a hard_kill after COMPLETE must dispatch a fresh flatten"


# ─────────────────────────────────────────────────────────────────────────────
# (c) CONDITIONAL / IDEMPOTENT DB WRITES — the core async-safety call
# ─────────────────────────────────────────────────────────────────────────────

def test_c_mark_trade_exiting_does_not_clobber_an_unknown_in_flight_trade(store):
    """RED ON OLD. This is the gap the schema does NOT already cover.

    schema.sql:278 `trg_trades_terminal_status_guard` ABORTs a status change out of
    a TERMINAL state (CLOSED/CLOSED_MANUAL/FAILED/CANCELLED/REJECTED*), so those
    were already protected from the flatten. UNKNOWN_IN_FLIGHT is NOT terminal and
    NOT guarded: it is the A-2 ambiguity state — "the broker ack timed out, we do
    not know whether this filled". An unconditional EXITING write erases exactly
    that uncertainty and replaces it with a confident lie.
    """
    _seed_open_trade(store, trade_status="UNKNOWN_IN_FLIGHT")
    ks = _make_ks(store, adapter=_BlockingFlattenAdapter())

    ks._mark_trade_exiting("T_MC8")

    assert _trade_status(store) == "UNKNOWN_IN_FLIGHT", (
        "the flatten overwrote an UNKNOWN_IN_FLIGHT trade with EXITING — the "
        "ambiguity signal was destroyed by a losing race"
    )


def test_c1b_mark_trade_exiting_is_a_clean_noop_on_a_terminal_trade(store):
    """Defence in depth, and quieter. The terminal case is already blocked by
    trg_trades_terminal_status_guard — but on the OLD code that meant the trigger
    RAISEd ABORT, the transaction blew up, and _mark_trade_exiting logged a
    CRITICAL "DB write (EXITING) failed" for what is a perfectly ordinary race.
    Conditioning the write turns it into a no-op with an INFO, so a real DB failure
    stays distinguishable from routine concurrency.
    """
    _seed_open_trade(store, trade_status="CLOSED")
    ks = _make_ks(store, adapter=_BlockingFlattenAdapter())

    handler = _CapturingCritical()
    _LOG.addHandler(handler)
    try:
        ks._mark_trade_exiting("T_MC8")
    finally:
        _LOG.removeHandler(handler)

    assert _trade_status(store) == "CLOSED"
    assert not handler.messages, (
        f"a routine lost race logged CRITICAL noise: {handler.messages}"
    )


def test_c2_mark_trade_exiting_still_marks_a_live_trade(store):
    """The conditional must not be so tight it stops doing its job (FIX-190:
    marking EXITING is what stops a concurrent flatten re-selecting the trade)."""
    _seed_open_trade(store, trade_status="OPEN")
    ks = _make_ks(store, adapter=_BlockingFlattenAdapter())

    ks._mark_trade_exiting("T_MC8")

    assert _trade_status(store) == "EXITING"


def test_c3_cancel_resting_exits_does_not_clobber_a_sl_that_filled_in_the_race_window(
    store,
):
    """The precise race: the SELECT sees the SL live, we cancel at the broker, and
    the fill thread marks it COMPLETE *before* our write lands. RED ON OLD: the
    unconditional write recorded a FILLED stop-loss as CANCELLED — the DB would
    then show an exit that never happened and the reconciler would believe a closed
    position was still open.

    The racing adapter mutates the row inside cancel_order, i.e. in exactly the
    window between the SELECT and the UPDATE.
    """
    _seed_open_trade(store, sl_status="OPEN")

    class _RacingAdapter(_BlockingFlattenAdapter):
        def cancel_order(self, oid):
            # The fill thread wins the race, right here.
            with store.transaction() as cur:
                cur.execute(
                    "UPDATE orders SET status = 'COMPLETE' WHERE order_id = ?", (oid,)
                )
            return CancelResult(broker_order_id=oid, success=True, reason="")

    ks = _make_ks(store, adapter=_RacingAdapter())
    ks._cancel_trade_resting_exits("T_MC8")

    assert _order_status(store, "SL_MC8") == "COMPLETE", (
        "a FILLED SL was overwritten as CANCELLED by the flatten"
    )


def test_c4_cancel_resting_exits_still_cancels_a_live_sl(store):
    """Non-racing case unchanged: a genuinely live SL is cancelled (FIX-190 Bug E —
    orphan resting exits must not survive to re-fire into a naked position)."""
    _seed_open_trade(store, sl_status="OPEN")
    adapter = _BlockingFlattenAdapter()
    ks = _make_ks(store, adapter=adapter)

    ks._cancel_trade_resting_exits("T_MC8")

    assert _order_status(store, "SL_MC8") == "CANCELLED"


# ─────────────────────────────────────────────────────────────────────────────
# (d) SHUTDOWN drains — or times out cleanly and says so
# ─────────────────────────────────────────────────────────────────────────────

def test_d_drain_is_a_noop_when_no_flatten_is_running(store):
    ks = _make_ks(store, adapter=_BlockingFlattenAdapter())
    assert ks.drain_flatten(timeout=0) is True
    assert ks.flatten_state is FlattenState.IDLE


def test_d2_drain_returns_false_when_the_flatten_overruns_the_grace(store):
    """External SIGTERM case: we cannot hold systemd for 2h, so the drain is
    bounded and reports honestly that the worker is still running."""
    _seed_open_trade(store)
    adapter = _BlockingFlattenAdapter()
    ks = _make_ks(store, adapter=adapter)
    ks.hard_kill("emergency", "test")
    try:
        assert adapter.entered.wait(3.0)
        assert ks.drain_flatten(timeout=0.2) is False, (
            "drain claimed success while the worker was still running"
        )
        assert ks.flatten_state is FlattenState.DRAINING
    finally:
        adapter.gate.set()
        assert ks.drain_flatten(timeout=10) is True
        assert ks.flatten_state is FlattenState.COMPLETE


def test_d3_shutdown_drains_the_worker_and_criticals_when_it_overruns(caplog):
    """main._shutdown must ASK the kill switch to drain, before it tears anything
    down, and must escalate loudly if the flatten outlives the stop grace."""
    import main as main_mod

    ks = MagicMock()
    ks.drain_flatten.return_value = False  # still running when the grace expires
    st = MagicMock()
    st.checkpoint_wal.return_value = {"busy": 0, "log": 0, "checkpointed": 0}

    prev = main_mod._shutdown_event.is_set()
    try:
        with caplog.at_level(logging.CRITICAL):
            main_mod._shutdown(
                signal_proc=MagicMock(), entry_gate=MagicMock(), smart_tgt=MagicMock(),
                order_reconciler=MagicMock(), order_monitor=MagicMock(),
                live_feed=MagicMock(), candle_store=MagicMock(), notifier=MagicMock(),
                store=st, kill_switch=ks, flatten_drain_timeout_sec=0.1, mode="PAPER",
            )
        ks.drain_flatten.assert_called_once_with(timeout=0.1)
        assert "POSITIONS MAY REMAIN OPEN" in caplog.text.upper()
    finally:
        if not prev:
            main_mod._shutdown_event.clear()


def test_d4_shutdown_without_a_kill_switch_still_works(caplog):
    """Back-compat: kill_switch is optional; absent it, shutdown is unchanged."""
    import main as main_mod

    st = MagicMock()
    st.checkpoint_wal.return_value = {"busy": 0, "log": 0, "checkpointed": 0}
    prev = main_mod._shutdown_event.is_set()
    try:
        main_mod._shutdown(
            signal_proc=MagicMock(), entry_gate=MagicMock(), smart_tgt=MagicMock(),
            order_reconciler=MagicMock(), order_monitor=MagicMock(),
            live_feed=MagicMock(), candle_store=MagicMock(), notifier=MagicMock(),
            store=st, mode="PAPER",
        )
    finally:
        if not prev:
            main_mod._shutdown_event.clear()


# ─────────────────────────────────────────────────────────────────────────────
# (e) the eod-self-exit CANNOT fire mid-flatten (the EXITING-blind race)
# ─────────────────────────────────────────────────────────────────────────────

def _past_window():
    import main as main_mod
    from datetime import time as _t
    window_end = _t(16, 0)
    now = datetime(2026, 7, 16, 16, 30)
    return main_mod, now, window_end


def test_e_eod_self_exit_does_not_fire_while_a_flatten_is_in_progress():
    """RED ON OLD: count_active_positions() counts only OPEN/PARTIAL/PENDING_FILL,
    and the flatten marks trades EXITING *early* — so a flatten still retrying
    reads as 0 active == flat, and the old code would exit the process out from
    under trades mid-exit. Note the store reports ZERO active here: that is the
    whole point. Only the kill switch can answer this."""
    main_mod, now, window_end = _past_window()
    st = MagicMock()
    st.count_active_positions.return_value = 0  # EXITING is invisible to this

    due, active = main_mod._eod_self_exit_due(
        st, now, window_end, flatten_in_progress_fn=lambda: True
    )

    assert due is False, "the process would have exited mid-flatten"
    assert active == main_mod._ACTIVE_FLATTEN_IN_PROGRESS


def test_e2_eod_self_exit_fires_normally_when_no_flatten_is_running():
    main_mod, now, window_end = _past_window()
    st = MagicMock()
    st.count_active_positions.return_value = 0

    due, active = main_mod._eod_self_exit_due(
        st, now, window_end, flatten_in_progress_fn=lambda: False
    )

    assert due is True and active == 0


def test_e3_eod_self_exit_is_failsafe_when_the_flatten_gate_raises():
    """Never exit on an unknown: if we cannot confirm no flatten, stay up."""
    main_mod, now, window_end = _past_window()
    st = MagicMock()
    st.count_active_positions.return_value = 0

    def _boom():
        raise RuntimeError("kill switch unavailable")

    due, active = main_mod._eod_self_exit_due(
        st, now, window_end, flatten_in_progress_fn=_boom
    )

    assert due is False
    assert active == main_mod._ACTIVE_FLATTEN_IN_PROGRESS


def test_e4_eod_self_exit_unchanged_when_no_gate_is_supplied():
    """Back-compat: the parameter is optional and defaults to the old behaviour."""
    main_mod, now, window_end = _past_window()
    st = MagicMock()
    st.count_active_positions.return_value = 0
    assert main_mod._eod_self_exit_due(st, now, window_end) == (True, 0)
    st.count_active_positions.return_value = 2
    assert main_mod._eod_self_exit_due(st, now, window_end) == (False, 2)


def test_e5_real_killswitch_gate_blocks_the_real_eod_decision(store):
    """End-to-end: the REAL KillSwitch.is_flatten_in_progress wired into the REAL
    _eod_self_exit_due, with a REAL flatten in flight."""
    import main as main_mod
    from datetime import time as _t

    _seed_open_trade(store)
    adapter = _BlockingFlattenAdapter()
    ks = _make_ks(store, adapter=adapter)
    ks.hard_kill("emergency", "test")
    try:
        assert adapter.entered.wait(3.0)
        st = MagicMock()
        st.count_active_positions.return_value = 0  # "flat" — and wrong
        due, active = main_mod._eod_self_exit_due(
            st, datetime(2026, 7, 16, 16, 30), _t(16, 0),
            flatten_in_progress_fn=ks.is_flatten_in_progress,
        )
        assert due is False
        assert active == main_mod._ACTIVE_FLATTEN_IN_PROGRESS
    finally:
        adapter.gate.set()
        ks.drain_flatten(timeout=10)

    # ...and once the flatten is COMPLETE the gate opens again.
    due, _ = main_mod._eod_self_exit_due(
        st, datetime(2026, 7, 16, 16, 30), _t(16, 0),
        flatten_in_progress_fn=ks.is_flatten_in_progress,
    )
    assert due is True


# ─────────────────────────────────────────────────────────────────────────────
# (f) preserved invariants + the legacy path
# ─────────────────────────────────────────────────────────────────────────────

def test_f_fix180_retry_bound_and_alert_dedup_are_unchanged():
    """M-C8 changed WHERE the loop runs, never its guards."""
    assert ks_mod._HARD_KILL_MAX_RETRY_HOURS == 2.0
    assert ks_mod._EXIT_ALERT_DEDUP_SEC == 300.0


def test_f2_the_flatten_select_and_write_condition_share_one_status_set():
    """If the SELECT and the EXITING write-condition ever drift, the write
    silently no-ops and the trade stays re-selectable — a double-sell risk."""
    assert ks_mod._FLATTEN_LIVE_TRADE_STATUSES == ("OPEN", "PARTIAL", "PENDING_FILL")
    assert ks_mod._FLATTEN_TERMINAL_ORDER_STATUSES == (
        "CANCELLED", "FAILED", "EXPIRED", "COMPLETE",
    )


def test_f3_legacy_cancel_fn_path_stays_synchronous_and_keeps_ks6_rerun(store):
    """The legacy path must be untouched: a REAL synchronous report, and KS6's
    re-run-on-repeat semantics. It must never touch the worker."""
    calls: list[int] = []

    def cancel_fn():
        calls.append(1)
        return CancellationReport(attempted=1, succeeded=1, failed=[])

    ks = _make_ks(store, adapter=None, cancel_fn=cancel_fn)  # NO adapter

    r1 = ks.hard_kill("first", "test")
    assert r1.dispatched is False, "the legacy report is a real result, not a dispatch"
    assert (r1.attempted, r1.succeeded) == (1, 1)
    assert calls == [1]

    ks.hard_kill("second", "test")
    assert calls == [1, 1], "KS6: the legacy path re-runs cancellation on repeat"

    assert ks.flatten_state is FlattenState.IDLE, "legacy path must not start a worker"
    assert ks.is_flatten_in_progress() is False


def test_f4_adapter_path_runs_the_same_real_flatten_end_to_end(store):
    """The worker runs the REAL _exit_all_trades_indestructible: the trade is
    flattened (SELL 100) and marked EXITING, exactly as the sync path did."""
    _seed_open_trade(store)
    adapter = _BlockingFlattenAdapter()
    adapter.gate.set()  # do not hold it; let the flatten run through
    ks = _make_ks(store, adapter=adapter)

    ks.hard_kill("emergency", "test")
    assert ks.drain_flatten(timeout=10)

    assert adapter.placed == [{"side": "SELL", "qty": 100}]
    assert _trade_status(store) == "EXITING"


# ─────────────────────────────────────────────────────────────────────────────
# (g) the state machine
# ─────────────────────────────────────────────────────────────────────────────

def test_g_state_machine_idle_running_draining_complete(store):
    _seed_open_trade(store)
    adapter = _BlockingFlattenAdapter()
    ks = _make_ks(store, adapter=adapter)

    assert ks.flatten_state is FlattenState.IDLE

    ks.hard_kill("emergency", "test")
    try:
        assert ks.flatten_state is FlattenState.RUNNING
        assert adapter.entered.wait(3.0)

        result: list[bool] = []
        drainer = threading.Thread(
            target=lambda: result.append(ks.drain_flatten(timeout=10))
        )
        drainer.start()

        deadline = time.monotonic() + 3.0
        while (ks.flatten_state is not FlattenState.DRAINING
               and time.monotonic() < deadline):
            time.sleep(0.01)
        assert ks.flatten_state is FlattenState.DRAINING
    finally:
        adapter.gate.set()

    drainer.join(10)
    assert result == [True]
    assert ks.flatten_state is FlattenState.COMPLETE
    assert ks.is_flatten_in_progress() is False


def test_g2_a_crashing_worker_still_reaches_complete(store, monkeypatch):
    """Safety-critical: if the worker died leaving the state at RUNNING,
    is_flatten_in_progress() would answer True forever and the eod-self-exit gate
    would hold the process open all night waiting on a flatten that is not
    running. COMPLETE-on-crash keeps the gate honest."""
    def _boom(self):
        raise RuntimeError("broker exploded mid-flatten")

    monkeypatch.setattr(KillSwitch, "_exit_all_trades_indestructible", _boom)
    ks = _make_ks(store, adapter=_BlockingFlattenAdapter())

    ks.hard_kill("emergency", "test")
    assert ks.drain_flatten(timeout=10) is True
    assert ks.flatten_state is FlattenState.COMPLETE
    assert ks.is_flatten_in_progress() is False


def test_g3_the_worker_is_not_a_daemon(store):
    """A daemon thread would be killed the instant the process decides to exit —
    mid-flatten, positions open. Non-daemon is the backstop behind the gate."""
    _seed_open_trade(store)
    adapter = _BlockingFlattenAdapter()
    ks = _make_ks(store, adapter=adapter)
    ks.hard_kill("emergency", "test")
    try:
        assert adapter.entered.wait(3.0)
        assert ks._flatten_thread is not None
        assert ks._flatten_thread.daemon is False
        assert ks._flatten_thread.name == "ks-hard-kill-flatten"
    finally:
        adapter.gate.set()
        ks.drain_flatten(timeout=10)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
