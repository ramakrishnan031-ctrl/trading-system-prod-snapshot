"""
tests/integration/test_q9_post_restart_capital_wired.py — Q9 batch 5, item #5.

POST-RESTART CAPITAL RESTORATION, PROVEN THROUGH THE REAL RESTORE FUNCTIONS.

WHY THIS FILE EXISTS. A restart is where a trading system silently forgets what it owes. The
worst case is not losing capital state — it is losing the DAY'S REALIZED LOSS, because a system
that has already lost 3% would then get a fresh 3% budget and the restart itself becomes the
bypass of the daily loss limit.

⭐ §A4 — WHAT THIS TEST ACTUALLY DRIVES, AND WHAT IT DOES NOT (rule F, stated honestly).
A "restart" in a fixture is something the test constructs, which makes this the easiest layer
in the programme to fake by accident (batch 4 found the integration suite called
PositionSizer.calculate() ZERO times while sizing was nominally "covered").

_restart() below constructs FRESH objects over the SAME StateStore and drives, in main.py's
order, the REAL production functions that constitute state restoration:

    KillSwitch(...)                            __init__ -> _load_state_from_store()  KS3, :250
    kill_switch.clear_stale_state(today)       main.py:1768  (prior-day kills auto-clear)
    fund_manager.initialize(seed)              main.py:2259
    fund_manager.rehydrate_from_open_trades()  main.py:2286  (exit 3 on CapitalStateInconsistent)

RUNTIME-PROVEN to do real work, not to no-op (probe, and asserted in every test below):
    _replay_open_trade CALL COUNT = 1  ·  summary {'replayed_trades': 1, 'replayed_pnl_rows': 1}
    kill_state_after_load = SOFT_KILL (i.e. the fresh instance really did load from the DB)

⚠️ The KillSwitch MUST be rebuilt, not reused — an earlier draft of this file reused the
fixture's instance and its kill assertions were therefore vacuous: `is_active()` reads only the
in-memory `_state`, so reusing the object asserts a flag the test never restored. It would have
passed with the persistence load deleted.

*** WHAT IT DOES NOT REPRODUCE — the rest of the boot: ***
  * the paper adapter capital re-sync (FIX-156)  main.py:2299
  * order_reconciler.reconcile_once()            main.py:3207 — the SYNCHRONOUS startup
    reconcile against BROKER TRUTH, which resolves in-flight/PENDING entries.
So this file proves the CAPITAL-REPLAY and KILL-STATE halves of restart restoration. It does
NOT prove broker reconciliation, and must not be read as doing so.

THE RESTORE MAP (§A1), file:line:
    total            initialize(seed) then Phase 2 adds today's realized PnL   :1710
                     seed: PAPER = static paper_capital (main.py:2243)
                           LIVE  = broker.get_margins().net - today_realized_pnl_carryover()
                                   (main.py:2255-2258, M-C1)
    available        initialize() splits by bucket pct, then Phase 1/2 adjust  :1708
    reserved         Phase 1 replays RESERVE rows for OPEN/PARTIAL trades      :1688
    used             Phase 1 replays COMMIT rows for OPEN/PARTIAL trades       :1688
    realized P&L     *** NOT RESTORED — NOT HELD IN MEMORY AT ALL. ***  FIX-051 removed the
                     in-memory _daily_pnl; get_daily_realized_net_pnl(today) recomputes it from
                     fm_ledger on every read (state_store.py:2432), and BOTH daily-loss halves
                     read it that way (fund_manager.py:1279 pre-trade gate, :1507 snapshot).
                     It therefore survives a restart BY CONSTRUCTION: there is nothing to lose.
    open positions   trades table (status OPEN/PARTIAL), state_store.get_all_open_trades()
    reservations     *** OPEN/PARTIAL ONLY. *** get_all_open_trades() returns OPEN or PARTIAL,
                     so a PENDING_FILL trade's reservation is deliberately NOT replayed — see
                     TestPendingReservationIsNotCarried for why that is correct, not a leak.

⚠️ TWO QUANTITIES THAT MUST NOT BE CONFLATED (batch 3's lesson, and it bites here):
  rehydrate Phase 2 applies SUM(fm_ledger.pnl_delta) to _total.
  The daily-loss reader returns SUM(pnl_delta) - SUM(costs).
  Today those differ by exactly the E4/W10 double-subtraction. Every threshold assertion below
  is phrased RELATIVE TO THE READER'S OWN RETURNED VALUE (batch 1's discipline), never as a
  rupee figure, so this file stays correct under both contracts and needs no edit when E4/W10
  lands.

PARITY (Rule #5) — this is the ONE layer where paper and live genuinely differ in INPUTS, so it
is stated rather than waved through. There is ONE restore path, not two: both modes call the
same initialize() and the same rehydrate_from_open_trades(). Only the SEED differs
(main.py:2243 vs :2255). This is NOT the P1 /health two-implementations shape — there is no
duplicated logic that could drift. The live-only carryover subtraction
(today_realized_pnl_carryover, fund_manager.py:1777) exists precisely so live's seed cancels
Phase 2's re-addition "by construction", making live agree with paper; its docstring names
paper as "the parity reference". TestParity pins the single-path structure.
"""
from __future__ import annotations

import time
from datetime import timedelta

import pytest

from capital.fund_manager import FundManager
from capital.kill_switch import KillState, KillSwitch
from core.events import OrderFilled
from core.time_authority import now_ist
from tests.integration.conftest import PAPER_CAPITAL, SystemContext
from tests.integration.test_full_signal_flow import _seed_signal_row
from tests.integration.test_q9_daily_loss_limit_wired import (
    PRE_TRADE_PCT, _drive_losing_close, _probe_signal_status,
)

TOL = 0.01


# ── the restart harness ──────────────────────────────────────────────────────

def _restart(ctx: SystemContext, seed: float = PAPER_CAPITAL,
             rewire_kill: bool = False) -> tuple:
    """One RESTART: FRESH objects over the SAME store, driven through the REAL production
    restore functions in main.py's order. Returns (fm, counts).

    ⚠️ The KillSwitch is REBUILT, not reused. That matters: `KillSwitch.__init__` restores the
    persisted state via _load_state_from_store() (capital/kill_switch.py:250, KS3 "Audit Issue
    #18 fix"), and `is_active()` reads ONLY the in-memory `_state`. A harness that reused the
    existing KillSwitch object would assert on a flag it never rebuilt and would prove nothing
    about persistence — it would pass even if the load were deleted.

    Order mirrors main.py:
        KillSwitch(...)                          -> __init__ calls _load_state_from_store()
        kill_switch.clear_stale_state(today)     main.py:1768  (prior-day kills auto-clear)
        fund_manager.initialize(seed)            main.py:2259
        fund_manager.rehydrate_from_open_trades() main.py:2286

    counts['replay_open_trade'] and counts['kill_state_after_load'] are the §A4 call/state
    proofs — every test asserts them, so a scenario that silently stopped exercising the real
    path cannot pass quietly.

    rewire_kill: also point the EXISTING order_placer at the rebuilt KillSwitch, mirroring a
    real boot where the placer is constructed with the new instance. Used only by the test
    that proves the RESTORED kill still blocks a placement.
    """
    ks2 = KillSwitch(
        state_store=ctx.store,
        bus=ctx.bus,
        logger=ctx.kill_switch._log,
        api_failure_threshold=3,
        enable_auto_trip=False,
    )
    counts = {
        "replay_open_trade": 0,
        "kill_state_after_load": ks2.current_state(),
    }
    ks2.clear_stale_state(now_ist().date())           # main.py:1768

    fm2 = FundManager(
        state_store=ctx.store,
        bus=ctx.bus,
        logger=ctx.fund_manager._log,
        intraday_bucket_pct=0.70,
        positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.02,
        leverage_map=ctx.fund_manager._leverage_map,
    )
    real_replay = fm2._replay_open_trade

    def _spy(trade, anomalies):
        counts["replay_open_trade"] += 1
        return real_replay(trade, anomalies)

    fm2._replay_open_trade = _spy
    fm2.initialize(seed)                              # main.py:2259
    counts["summary"] = fm2.rehydrate_from_open_trades()   # main.py:2286
    counts["kill_switch"] = ks2
    if rewire_kill:
        ctx.order_placer._ks = ks2
    return fm2, counts


def _pic(fm) -> dict:
    """The full money picture (Q9 standing rule A), quantity by quantity.
    Rule B1: an AGGREGATE match can hide two compensating errors, so nothing here is summed
    away — each quantity is asserted on its own."""
    s = fm.get_snapshot()
    return {
        "total": s.total,
        "intraday_avail": s.intraday_avail,
        "positional_avail": s.positional_avail,
        "intraday_reserved": s.intraday_reserved,
        "positional_reserved": s.positional_reserved,
        "intraday_used": s.intraday_used,
        "positional_used": s.positional_used,
        "daily_realized_pnl": s.daily_realized_pnl,
    }


def _reader(ctx: SystemContext) -> float:
    """What BOTH daily-loss halves enforce on (fund_manager.py:1279 and :1507)."""
    return ctx.store.get_daily_realized_net_pnl(now_ist().date().isoformat())


def _assert_global_invariant(fm, stage: str) -> None:
    """Batch 3's I1 — the GLOBAL identity (the per-bucket form does NOT hold;
    fund_manager.py:2235 guards those checks behind 'only when already negative')."""
    p = _pic(fm)
    lhs = round(p["intraday_avail"] + p["positional_avail"]
                + p["intraday_reserved"] + p["positional_reserved"]
                + p["intraday_used"] + p["positional_used"], 2)
    rhs = round(p["total"], 2)
    assert abs(lhs - rhs) <= TOL, (
        f"CAPITAL INVARIANT BROKEN at {stage!r}: available+reserved+used = {lhs:.2f} "
        f"but total = {rhs:.2f} (delta {lhs - rhs:+.4f})"
    )


def _open_a_position(ctx: SystemContext, symbol: str, strategy: str, sid: str,
                     qty: int, entry: float, sl: float) -> str:
    """A real OPEN position: reserve -> place -> ENTRY fill. Returns trade_id."""
    _seed_signal_row(ctx, sid, symbol, strategy=strategy)
    res = ctx.fund_manager.reserve(symbol=symbol, qty=qty, price=entry,
                                   intent="INTRADAY", signal_id=sid)
    assert res.success, f"reserve failed for {symbol}: {res}"
    ctx.order_placer.place(symbol=symbol, side="BUY", qty=qty, entry_price=entry,
                           sl_price=sl, intent="INTRADAY", signal_id=sid,
                           reservation_id=res.reservation_id, strategy=strategy)
    with ctx.order_placer._fill_map_lock:
        iid = next(i for i, f in ctx.order_placer._fill_map.items()
                   if f.leg == "ENTRY" and f.trade_id is not None
                   and ctx.order_manager.get_trade(f.trade_id)["symbol"] == symbol
                   and ctx.order_manager.get_trade(f.trade_id)["status"] not in ("CLOSED",))
        tid = ctx.order_placer._fill_map[iid].trade_id
    ctx.bus.publish(OrderFilled(
        source_module="q9b5", internal_order_id=iid, broker_order_id=f"E_{symbol}",
        symbol=symbol, side="BUY", filled_qty=qty, avg_fill_price=entry,
        expected_price=entry, slippage_pct=0.0,
        filled_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
    assert ctx.order_manager.get_trade(tid)["status"] == "OPEN"
    return tid


def _place_unfilled(ctx: SystemContext, symbol: str, strategy: str, sid: str,
                    qty: int, entry: float, sl: float) -> None:
    """A real UNFILLED order holding a LIVE reservation (paper_auto_fill_delay_sec=60)."""
    _seed_signal_row(ctx, sid, symbol, strategy=strategy)
    res = ctx.fund_manager.reserve(symbol=symbol, qty=qty, price=entry,
                                   intent="INTRADAY", signal_id=sid)
    assert res.success
    ctx.order_placer.place(symbol=symbol, side="BUY", qty=qty, entry_price=entry,
                           sl_price=sl, intent="INTRADAY", signal_id=sid,
                           reservation_id=res.reservation_id, strategy=strategy)


# ═════════════════════════════════════════════════════════════════════════════
# 1. THE CORE SCENARIO — restored quantity by quantity (B1)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestRestorationCoreScenario:

    def test_open_position_and_realized_pnl_are_restored_quantity_by_quantity(self, wired_system):
        """Real state to lose: a CLOSED round-trip (realized P&L), an OPEN position (used),
        and an UNFILLED order (live reservation). Then restart and check each quantity."""
        ctx = wired_system
        _drive_losing_close(ctx, "RELIANCE", 1)                       # realized P&L
        _open_a_position(ctx, "TCS", "gap_go_long", "b5_open", 50, 400.0, 392.0)   # used
        _place_unfilled(ctx, "INFY", "range_breakout_long", "b5_pend", 40, 300.0, 294.0)

        before = _pic(ctx.fund_manager)
        reader_before = _reader(ctx)
        _assert_global_invariant(ctx.fund_manager, "before-restart")

        # ANTI-VACUITY: the scenario must actually have built state worth losing.
        assert before["intraday_used"] > 0, "no OPEN position — nothing for Phase 1 to restore"
        assert before["intraday_reserved"] > 0, "no live reservation in the scenario"
        assert reader_before < 0, "no realized loss — the P&L question would be trivial"

        fm2, counts = _restart(ctx)
        after = _pic(fm2)

        # §A4: the REAL restore code ran AND did real work.
        assert counts["replay_open_trade"] == 1, (
            f"Phase 1 replayed {counts['replay_open_trade']} trades, expected 1 — the real "
            f"restore path did not execute over the OPEN position"
        )
        assert counts["summary"]["replayed_trades"] == 1
        assert counts["summary"]["replayed_pnl_rows"] >= 1, (
            "Phase 2 replayed no P&L rows — today's realized P&L was not carried"
        )
        assert counts["summary"]["anomalies"] == [], (
            f"rehydrate reported anomalies: {counts['summary']['anomalies']}"
        )

        # QUANTITY BY QUANTITY (rule B1: an aggregate match hides compensating errors).
        assert after["total"] == pytest.approx(before["total"], abs=TOL), (
            f"TOTAL not restored: {before['total']:.2f} -> {after['total']:.2f}"
        )
        assert after["intraday_used"] == pytest.approx(before["intraday_used"], abs=TOL), (
            f"USED not restored: {before['intraday_used']:.2f} -> {after['intraday_used']:.2f} "
            f"— the OPEN position's committed capital was lost"
        )
        assert after["positional_used"] == pytest.approx(before["positional_used"], abs=TOL)
        assert after["daily_realized_pnl"] == pytest.approx(
            before["daily_realized_pnl"], abs=TOL), (
            f"DAILY REALIZED P&L not restored: {before['daily_realized_pnl']:.2f} -> "
            f"{after['daily_realized_pnl']:.2f}"
        )
        _assert_global_invariant(fm2, "after-restart")

    def test_the_reader_still_agrees_with_the_snapshot_after_restart(self, wired_system):
        """B7. A restart that restores each quantity plausibly but leaves the pre-trade gate
        and the snapshot enforcing on DIFFERENT numbers is the value-shaped bug this
        programme exists to catch."""
        ctx = wired_system
        _drive_losing_close(ctx, "RELIANCE", 1)
        _open_a_position(ctx, "TCS", "gap_go_long", "b5_open2", 50, 400.0, 392.0)

        fm2, counts = _restart(ctx)
        assert counts["replay_open_trade"] == 1

        assert fm2.get_snapshot().daily_realized_pnl == pytest.approx(_reader(ctx), abs=TOL), (
            "after restart the snapshot and get_daily_realized_net_pnl disagree — the "
            "pre-trade gate and the post-close breach would enforce on different numbers"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 2. ⭐ THE DAILY LOSS LIMIT ACROSS A RESTART (B3) — the headline
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestDailyLossSurvivesRestart:
    """A2a — the highest-severity question in this batch. If the day's realized loss resets,
    a restart hands the system a fresh loss budget and IS the bypass."""

    def test_realized_pnl_is_identical_after_a_restart(self, wired_system):
        """POSITIVE. Asserted RELATIVE to the reader's own value, never as a rupee figure —
        the reader's contract changes under E4/W10 and a literal would bake in today's
        double-subtraction."""
        ctx = wired_system
        _drive_losing_close(ctx, "RELIANCE", 1)
        _drive_losing_close(ctx, "TCS", 2)
        reader_before = _reader(ctx)

        # ANTI-VACUITY: a zero P&L would make "unchanged" trivially true.
        assert reader_before < 0, "no realized loss to preserve — the assertion would be vacuous"

        fm2, counts = _restart(ctx)

        assert _reader(ctx) == pytest.approx(reader_before, abs=TOL), (
            f"THE DAY'S REALIZED P&L DID NOT SURVIVE THE RESTART: {reader_before:.4f} -> "
            f"{_reader(ctx):.4f}. A restart would hand the system a fresh daily-loss budget."
        )
        assert fm2.get_snapshot().daily_realized_pnl == pytest.approx(reader_before, abs=TOL)
        assert counts["summary"]["replayed_pnl_rows"] >= 2, (
            f"expected >= 2 P&L rows carried, got "
            f"{counts['summary']['replayed_pnl_rows']} — Phase 2 did not walk both closes"
        )

    def test_the_limit_still_enforces_on_the_restored_value(self, wired_system):
        """The value surviving is necessary but not sufficient — the GATE must still fire on
        it. Batch 1 wired the gate itself; this proves it still bites after a restart."""
        ctx = wired_system

        def pre_trade_limit() -> float:
            """The pre-trade gate's own limit, derived from the fixture's configured pct and
            the CURRENT total — never a hard-coded rupee figure. (The limit MOVES: total
            shrinks as losses realize, so it must be re-read, not cached — batch 1's finding.)"""
            return PRE_TRADE_PCT * ctx.fund_manager.get_snapshot().total

        # NEGATIVE first: below the limit, DAILY_LOSS must NOT be the rejection reason.
        _drive_losing_close(ctx, "RELIANCE", 1)
        reader_low = _reader(ctx)
        assert reader_low > -pre_trade_limit(), (
            f"one loss ({reader_low:.2f}) already crossed the limit "
            f"({-pre_trade_limit():.2f}) — the negative case is unprovable; reduce LOSS sizing"
        )
        _restart(ctx)
        status_below = _probe_signal_status(ctx, "WIPRO", 300.0)
        assert status_below != "REJECTED_DAILY_LOSS", (
            f"after a restart the reader is {_reader(ctx):.2f}, above the limit "
            f"{-pre_trade_limit():.2f}, yet the signal was rejected for DAILY_LOSS"
        )

        # POSITIVE: drive past the limit, restart, and the gate must fire SPECIFICALLY.
        for seq, sym in enumerate(("TCS", "INFY", "SBIN"), start=2):
            _drive_losing_close(ctx, sym, seq)
            if _reader(ctx) <= -pre_trade_limit():
                break
        reader_high = _reader(ctx)
        assert reader_high <= -pre_trade_limit(), (
            f"losses ({reader_high:.2f}) never crossed the pre-trade limit "
            f"({-pre_trade_limit():.2f}) — increase loss sizing"
        )

        fm2, counts = _restart(ctx)
        # The restored value is the one the gate will see.
        assert _reader(ctx) == pytest.approx(reader_high, abs=TOL)

        status_above = _probe_signal_status(ctx, "HDFCBANK", 300.0)
        assert status_above == "REJECTED_DAILY_LOSS", (
            f"AFTER A RESTART the reader is {_reader(ctx):.2f}, past the limit "
            f"{-pre_trade_limit():.2f}, but the signal terminated as {status_above!r} rather "
            f"than REJECTED_DAILY_LOSS — the limit does not enforce on the restored value"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 3. KILL STATE ACROSS A RESTART (B4)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestKillStateSurvivesRestart:
    """A2b. Batch 3 established that the daily-loss breach ARMS A SOFT KILL
    (_make_daily_loss_cb, main.py:757-800 -> EOD fire_now -> kill_switch.soft_kill), so
    'breached, then restarted' is a real sequence. If the kill does not survive, the restart
    IS the bypass."""

    def test_a_same_day_kill_is_still_armed_after_a_restart(self, wired_system):
        ctx = wired_system
        assert not ctx.kill_switch.is_active("entry"), "fixture should start with kill clear"

        # Open the position FIRST: batch 2 proved order_placer re-checks the kill at the last
        # mile (order_placer.py:1004/:1289), so a position cannot be opened once it is armed.
        _open_a_position(ctx, "TCS", "gap_go_long", "b5_k1", 50, 400.0, 392.0)

        ctx.kill_switch.soft_kill(reason="q9b5_breach_sim", triggered_by="q9_batch5")
        assert ctx.kill_switch.is_active("entry"), "soft_kill did not arm"

        fm2, counts = _restart(ctx)
        assert counts["replay_open_trade"] == 1, (
            "the open position was not replayed — this scenario must carry real capital state "
            "across the restart, not just the kill flag"
        )

        # ⭐ Asserted on the REBUILT switch. The restoring mechanism is
        # KillSwitch.__init__ -> _load_state_from_store() (kill_switch.py:250, KS3);
        # is_active() reads only the in-memory _state, so a fresh instance that failed to
        # load would report INACTIVE here.
        ks2 = counts["kill_switch"]
        assert counts["kill_state_after_load"] == KillState.SOFT_KILL, (
            f"the REBUILT KillSwitch loaded {counts['kill_state_after_load']} from the store, "
            f"expected SOFT_KILL — a restart would clear a same-day kill and BE the bypass"
        )
        assert ks2.is_active("entry"), (
            "THE KILL DID NOT SURVIVE THE RESTART — a same-day kill must persist; "
            "clear_stale_state must only clear PRIOR-day kills"
        )
        row = ctx.store.fetch_all("SELECT state FROM kill_switch_state")
        assert row and row[0]["state"] == "SOFT_KILL", (
            f"kill_switch_state is {[dict(r) for r in row]}, expected SOFT_KILL persisted"
        )

    def test_a_prior_day_kill_is_auto_cleared_by_the_restart(self, wired_system):
        """NEGATIVE half (rule B). A same-day kill must persist; a PRIOR-day kill must NOT.
        That asymmetry is the whole contract of clear_stale_state (kill_switch.py:252) and
        Rama's 2026-06-20 headless decision: a new trading day always starts clean.

        Without this, 'the kill survives a restart' would be indistinguishable from 'the kill
        can never be cleared', which would block every subsequent trading day."""
        ctx = wired_system
        ctx.kill_switch.soft_kill(reason="q9b5_prior_day", triggered_by="q9_batch5")

        # Backdate the PERSISTED row by one day — the same row _load_state_from_store reads.
        with ctx.store.transaction() as cur:
            cur.execute(
                "UPDATE kill_switch_state SET triggered_at = ? WHERE id = 1",
                ((now_ist() - timedelta(days=1)).isoformat(),),
            )

        fm2, counts = _restart(ctx)
        ks2 = counts["kill_switch"]

        # It WAS loaded as active (proving the load ran)...
        assert counts["kill_state_after_load"] == KillState.SOFT_KILL, (
            "the prior-day kill was not even loaded — this test would pass vacuously"
        )
        # ...and then correctly auto-cleared by clear_stale_state.
        assert not ks2.is_active("entry"), (
            "a PRIOR-DAY kill survived the restart — every following trading day would be "
            "blocked (clear_stale_state did not fire)"
        )
        assert ks2.current_state() == KillState.INACTIVE

    def test_the_restored_kill_still_blocks_an_entry_at_the_last_mile(self, wired_system):
        """Armed is not the same as enforcing. Batch 2 proved the last-mile re-check itself
        (order_placer.py:1004 OP-LM1 / :1289 A-3); this proves the STATE it reads survived the
        restart and still blocks a real placement.

        Driven through order_placer directly rather than the webhook: with a kill armed the
        RECEIVER rejects the POST outright (an earlier, coarser gate), which would prove the
        receiver's check rather than the last-mile one.
        """
        from core.exceptions import OrderRejectedError

        ctx = wired_system
        ctx.kill_switch.soft_kill(reason="q9b5_block", triggered_by="q9_batch5")
        _fm2, counts = _restart(ctx, rewire_kill=True)
        ks2 = counts["kill_switch"]
        assert counts["kill_state_after_load"] == KillState.SOFT_KILL
        assert ks2.is_active("entry"), "precondition: kill must survive the restart"

        broker_calls: list = []
        real = ctx.adapter.place_order

        def _spy(*a, **k):
            broker_calls.append(k.get("symbol") or (a[0] if a else "?"))
            return real(*a, **k)

        ctx.adapter.place_order = _spy

        sid = "b5_blocked"
        _seed_signal_row(ctx, sid, "WIPRO", strategy="vwap_bounce_long")
        res = ctx.fund_manager.reserve(symbol="WIPRO", qty=10, price=300.0,
                                       intent="INTRADAY", signal_id=sid)
        assert res.success, "reserve should still succeed; the kill blocks at placement"
        before = _pic(ctx.fund_manager)

        with pytest.raises(OrderRejectedError) as exc:
            ctx.order_placer.place(
                symbol="WIPRO", side="BUY", qty=10, entry_price=300.0, sl_price=294.0,
                intent="INTRADAY", signal_id=sid, reservation_id=res.reservation_id,
                strategy="vwap_bounce_long")
        assert "kill_switch_active" in str(exc.value), (
            f"blocked, but not by the kill switch: {exc.value}"
        )
        # Asserted on the BROKER CALL RECORD, not a status string (batch 2's pattern).
        assert broker_calls == [], (
            f"an order reached the broker with a restored kill armed: {broker_calls}"
        )
        # No capital leak: the reservation is released by _handle_placement_failure.
        after = _pic(ctx.fund_manager)
        assert after["intraday_reserved"] <= before["intraday_reserved"], (
            "the blocked placement left capital reserved"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 4. THE PENDING RESERVATION — the NEGATIVE case (B2)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestPendingReservationIsNotCarried:
    """B2's negative half: a quantity that must NOT be blindly carried across a restart.

    get_all_open_trades() (state_store.py) returns only OPEN or PARTIAL, so a PENDING_FILL
    trade — an order placed but not yet confirmed filled — is NOT replayed by Phase 1 and its
    reservation is NOT restored. That is DELIBERATE, not a leak: from the DB alone the system
    cannot know whether the broker filled that order, and the project's standing rule is that
    positions come from BROKER TRUTH (never act on DB state alone). The capital is RETURNED to
    available, and main.py:3207's synchronous order_reconciler.reconcile_once() then adopts or
    fails the in-flight entry against the broker.

    The failure this guards against is the opposite one: silently RESTORING the reservation
    would leave capital committed to an order the broker may never have accepted.
    """

    def test_a_pending_reservation_is_released_not_restored_and_not_lost(self, wired_system):
        ctx = wired_system
        _place_unfilled(ctx, "INFY", "range_breakout_long", "b5_p1", 40, 300.0, 294.0)

        before = _pic(ctx.fund_manager)
        assert before["intraday_reserved"] > 0, "scenario did not create a live reservation"
        status = ctx.store.fetch_all(
            "SELECT status FROM trades WHERE symbol = ?", ("INFY",))[0]["status"]
        assert status not in ("OPEN", "PARTIAL"), (
            f"INFY trade is {status!r}; this scenario needs a not-yet-filled trade"
        )

        fm2, counts = _restart(ctx)
        after = _pic(fm2)

        # Phase 1 correctly skipped it.
        assert counts["replay_open_trade"] == 0, (
            f"Phase 1 replayed {counts['replay_open_trade']} trades — a PENDING trade must "
            f"not be replayed (get_all_open_trades returns OPEN/PARTIAL only)"
        )
        # The reservation is NOT carried...
        assert after["intraday_reserved"] == pytest.approx(0.0, abs=TOL), (
            f"a PENDING reservation was restored ({after['intraday_reserved']:.2f}) — capital "
            f"would stay committed to an order the broker may never have accepted"
        )
        # ...and the capital is RETURNED, not lost.
        assert after["intraday_avail"] > before["intraday_avail"], (
            f"the released reservation did not return to available "
            f"({before['intraday_avail']:.2f} -> {after['intraday_avail']:.2f})"
        )
        assert after["total"] == pytest.approx(before["total"], abs=TOL), (
            "releasing a pending reservation must not change total capital"
        )
        _assert_global_invariant(fm2, "after-restart-pending")


# ═════════════════════════════════════════════════════════════════════════════
# 5. IDEMPOTENCY (B5)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestIdempotency:
    """A2d. The production-relevant question is whether TWO BOOT CYCLES (a systemd restart
    loop) double-count — each boot is a new process, so a new FundManager, a fresh
    initialize() and a fresh replay of the same unchanged DB rows."""

    def test_two_boot_cycles_produce_identical_state(self, wired_system):
        ctx = wired_system
        _drive_losing_close(ctx, "RELIANCE", 1)
        _open_a_position(ctx, "TCS", "gap_go_long", "b5_i1", 50, 400.0, 392.0)

        fm_boot1, c1 = _restart(ctx)
        boot1 = _pic(fm_boot1)
        fm_boot2, c2 = _restart(ctx)
        boot2 = _pic(fm_boot2)

        assert c1["replay_open_trade"] == 1 and c2["replay_open_trade"] == 1
        for k in boot1:
            assert boot2[k] == pytest.approx(boot1[k], abs=TOL), (
                f"BOOT #2 DOUBLE-COUNTED {k}: boot1={boot1[k]:.2f} boot2={boot2[k]:.2f} — a "
                f"systemd restart loop would compound capital state"
            )
        _assert_global_invariant(fm_boot2, "boot2")
        assert _reader(ctx) < 0, "realized P&L vanished across two boots"

    def test_rehydrate_is_not_idempotent_within_one_instance(self, wired_system):
        """Documents a real asymmetry, deliberately and without alarm.

        initialize() has an explicit double-call guard (H-4: WARNING + no-op,
        fund_manager.py:414-420). rehydrate_from_open_trades() has NO such guard — calling it
        twice on the SAME instance re-applies Phase 1 and Phase 2 and double-counts.

        This is NOT reachable in production: main.py:2286 calls it exactly once, there is no
        retry loop, and a restart is a new process (proven idempotent above). It is pinned here
        so that if a future caller ever adds a second invocation, the asymmetry is already
        documented rather than discovered in production.
        """
        ctx = wired_system
        _open_a_position(ctx, "TCS", "gap_go_long", "b5_i2", 50, 400.0, 392.0)

        fm2, _ = _restart(ctx)
        once = _pic(fm2)
        fm2.rehydrate_from_open_trades()
        twice = _pic(fm2)

        assert twice["intraday_used"] > once["intraday_used"], (
            "rehydrate appears idempotent within one instance — if a guard was added, delete "
            "this test and record the change; it currently documents the opposite"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 6. PARITY (B6) — one restore path, two seeds
# ═════════════════════════════════════════════════════════════════════════════

class TestParity:
    """The restore LOGIC is shared; only the SEED differs. Pinned structurally so a second
    implementation cannot appear unnoticed (the P1 /health shape)."""

    def test_rehydrate_has_no_mode_branch(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[2] / "capital" / "fund_manager.py"
        text = src.read_text(encoding="utf-8")
        start = text.index("def rehydrate_from_open_trades")
        end = text.index("def today_realized_pnl_carryover")
        body = "\n".join(
            ln for ln in text[start:end].splitlines() if not ln.lstrip().startswith("#")
        )
        for token in ("paper_mode", "is_paper", "live_mode"):
            assert token not in body, (
                f"rehydrate_from_open_trades now branches on {token!r} — capital restoration "
                f"has forked between paper and live and parity can no longer be argued"
            )

    def test_the_live_seed_still_subtracts_the_carryover(self):
        """⚠️ COVERAGE BOUNDARY, pinned structurally.

        Production runs `main.py --mode live`, but the wired fixture is paper_mode=True, so
        every test above exercises the PAPER seed (static paper_capital). The LIVE seed is
        `main.compute_live_seed(broker_adapter, fund_manager, floor)` (= broker.get_margins().net
        - today_realized_pnl_carryover(floor), M-C1). Since B1 (21-Jul-2026) extracted it into a
        callable function it IS now exercised directly — by test_q9_live_seed_mc1_wired.py and by
        tests/unit/test_mc1_live_seed_rehydrate.py. This regex is kept as a cheap structural
        backstop: the subtraction has not been rewritten away in place.

        The subtraction is what stops a LIVE mid-day warm restart double-counting today's
        realized P&L (the broker's net already includes it, and rehydrate Phase 2 re-adds it).
        Removing it would silently inflate live reservable capital.
        """
        from pathlib import Path
        import re
        main = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
        assert re.search(
            # `\(` not `\(\)`: B5 (21-Jul-2026) passes the shared day-floor as an argument, so the
            # call is today_realized_pnl_carryover(_start_of_today_iso), not empty-parens.
            r"broker_adapter\.get_margins\(\)\.net\s*\n\s*-\s*fund_manager\.today_realized_pnl_carryover\(",
            main,
        ), (
            "the LIVE capital seed no longer subtracts today_realized_pnl_carryover(...) — a live "
            "mid-day warm restart would double-count today's realized P&L (M-C1). The seed is now "
            "compute_live_seed(); see tests/unit/test_mc1_live_seed_rehydrate.py"
        )
        # And the two must keep sharing one row-selection, or the cancellation stops being exact.
        fm = (Path(__file__).resolve().parents[2] / "capital" / "fund_manager.py").read_text(
            encoding="utf-8")
        assert fm.count("_today_release_used_pnl_rows(") >= 3, (
            "rehydrate Phase 2 and today_realized_pnl_carryover no longer share "
            "_today_release_used_pnl_rows — the live-seed subtraction and the Phase-2 "
            "re-addition could diverge and stop cancelling"
        )

    def test_there_is_exactly_one_restore_call_site(self):
        from pathlib import Path
        import re
        main = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
        sites = re.findall(r"\.rehydrate_from_open_trades\(", main)
        assert len(sites) == 1, (
            f"expected exactly ONE rehydrate call site in main.py, found {len(sites)} — a "
            f"second invocation would double-count (see "
            f"TestIdempotency::test_rehydrate_is_not_idempotent_within_one_instance)"
        )
        # Both modes converge on the same initialize() call; only the seed differs.
        assert "fund_manager.initialize(_startup_capital)" in main, (
            "the single shared initialize() call site has moved — re-derive the parity argument"
        )
