"""
tests/integration/test_q9_capital_invariant_wired.py — Q9 batch 3, item (C).

THE CAPITAL INVARIANT, ASSERTED AT EVERY LIFECYCLE STAGE ON THE WIRED PATH.

WHY THIS FILE EXISTS. Q4 established that money writes here are SINGLE-WRITER and correct at
the COLUMN level (`fm_ledger` written only at fund_manager.py:2365; `trades.gross_pnl/charges/
net_pnl` only at state_store.py:2268). The residual risk is therefore not a bad write but a bad
VALUE — every part individually right, only the RELATIONSHIP between them wrong. No
column-level or single-function test can catch that shape, and this file is the catcher.

THE LIVE PROOF THAT THE SHAPE EXISTS — E4/W10:
    WRITER  fund_manager.release_used stores pnl_delta = gross - costs   (ALREADY NET)
    READER  state_store.get_daily_realized_net_pnl = SUM(pnl_delta) - SUM(costs)
    => costs subtracted TWICE; the daily loss limit fires EARLY on an overstated loss.
Fixed on branch `e4-w10-pnl-contract`@`ad34ee4`, deliberately UNPUSHED pending Rama's
risk-posture sign-off (it changes WHEN the loss limit fires). This file DOCUMENTS that bug in
executable form; it does NOT fix it.

⚠️ THE INVARIANT THAT ACTUALLY HOLDS (verified against real code + runtime, not assumed):

  I1 (GLOBAL, holds):  (intraday+positional) available + reserved + used == fund_manager._total
      This is what production itself asserts — fund_manager.py:2268 calls
      assert_capital_invariant(cash_floor=_total, realized_pnl_today=0.0), i.e. rhs == _total,
      because fund_manager tracks _total directly (docstring :2209).

  I1-per-bucket (DOES **NOT** HOLD — do not assert it):  fund_manager.py:2235 guards the
      per-bucket checks behind "only when a partition is already negative", and its comment
      states that a legitimate PnL-shifted per-bucket split (avail+reserved+used != total*pct)
      is never reached. Asserting the identity per bucket would be WRONG.

  I2 (holds):  _total - starting_capital == realized net P&L (ground truth). Note _total moves
      by the FULL P&L including profits (runtime-verified: a winner raised it by exactly the
      net). P7a's `cash_floor + min(0, realized)` form in capital/invariant.py:89 is a
      DIFFERENT quantity — the tradable balance, where profits are withheld until T+1
      settlement. The two are not interchangeable; do not conflate them.

  I5 (the CONTRACT invariant — FALSE TODAY, by E4/W10): reader == independent ground truth.
      See TestContractInvariant below for the design and why it is a strict xfail.

INDEPENDENT GROUND TRUTH (§A3). Realized net P&L is recomputed from TRADE-level data —
`SUM(trades.net_pnl)` — which is a DIFFERENT table, written by a DIFFERENT path
(state_store.py:2268 on close) from the one the reader aggregates (`fm_ledger`, written by
fund_manager.release_used). Two computations sharing a source cannot cross-check each other;
these do not share one.

SCENARIO HAZARDS designed around (§A5, rule G):
  * The post-close daily-loss breach is NOT passive — _make_daily_loss_cb (main.py:757-800)
    force-closes positions via EOD fire_now and arms a soft kill. The fixture's limit is
    2% x 500,000 = 10,000; every scenario here stays far below it, and the tests ASSERT the
    kill never armed so a future sizing change cannot silently wreck the capital picture.
  * max_consecutive_losses=4 is checked before DAILY_LOSS in RE5 — irrelevant here (no
    scenario drives 4 losses) but noted.
  * The per-strategy cap (signal_processor.py:624-640) fires BEFORE the risk engine, so the
    multi-position case seeds across DIFFERENT strategies (the exact fix batch 2 needed).
  * paper_auto_fill_delay_sec=60 -> an admitted order stays UNFILLED and holds a LIVE
    reservation. The invariant accounts for that rather than being surprised by it.
  * RMS/CHECK1 closes pass costs=0.0; these are ORDINARY SL/TGT closes, costed normally, so
    nothing here depends on the zero-cost path.

PARITY (Rule #5): the capital path is shared, not duplicated per mode. FundManager takes no
mode argument and contains no paper/live branch (grep: no `paper`/`is_paper` in
capital/fund_manager.py); the divergence is downstream in broker/zerodha_adapter.py:348
(paper_mode -> _paper_place_order). So the identity proven here holds identically in live.
"""
from __future__ import annotations

import time

import pytest

from core.events import OrderFilled
from core.time_authority import now_ist
from tests.integration.conftest import PAPER_CAPITAL, SystemContext
from tests.integration.test_full_signal_flow import _seed_signal_row

TOL = 0.01  # INV2: 1 paise, matching capital/invariant.py


# ── measurement helpers ──────────────────────────────────────────────────────

def _picture(ctx: SystemContext) -> dict:
    """The full money picture (Q9 standing rule A), plus the contract inputs."""
    s = ctx.fund_manager.get_snapshot()
    avail = s.intraday_avail + s.positional_avail
    reserved = s.intraday_reserved + s.positional_reserved
    used = s.intraday_used + s.positional_used
    return {
        "total": s.total,
        "avail": avail,
        "reserved": reserved,
        "used": used,
        "lhs": avail + reserved + used,
        "daily_realized_pnl": s.daily_realized_pnl,
        "reader": _reader(ctx),
        "truth": _ground_truth(ctx),
        "costs": _costs(ctx),
    }


def _reader(ctx: SystemContext) -> float:
    """What BOTH daily-loss halves enforce on (fund_manager.py:1279 and :1507)."""
    return ctx.store.get_daily_realized_net_pnl(now_ist().date().isoformat())


def _ground_truth(ctx: SystemContext) -> float:
    """Realized net P&L from TRADE-level data — independent of the fm_ledger aggregation."""
    row = ctx.store.fetch_one(
        "SELECT COALESCE(SUM(net_pnl), 0.0) AS v FROM trades WHERE net_pnl IS NOT NULL"
    )
    return float(row["v"])


def _costs(ctx: SystemContext) -> float:
    row = ctx.store.fetch_one("SELECT COALESCE(SUM(costs), 0.0) AS v FROM fm_ledger")
    return float(row["v"])


def _assert_invariant(ctx: SystemContext, stage: str) -> dict:
    """I1 (global). Both sides rounded to paise BEFORE comparing, mirroring INV7's
    float-drift fix in capital/invariant.py."""
    p = _picture(ctx)
    lhs, rhs = round(p["lhs"], 2), round(p["total"], 2)
    assert abs(lhs - rhs) <= TOL, (
        f"CAPITAL INVARIANT BROKEN at stage {stage!r}: "
        f"available({p['avail']:.2f}) + reserved({p['reserved']:.2f}) + used({p['used']:.2f}) "
        f"= {lhs:.2f}, but total = {rhs:.2f}  (delta {lhs - rhs:+.4f})"
    )
    # The daily-loss breach is not passive — if a scenario ever trips it, positions are
    # force-closed and a soft kill arms, which would silently invalidate everything above.
    assert not ctx.kill_switch.is_active("entry"), (
        f"kill switch armed during stage {stage!r} — the daily-loss breach fired and "
        f"force-closed positions; this scenario must stay below the fixture's limit"
    )
    return p


# ── the round-trip driver ────────────────────────────────────────────────────

def _round_trip(ctx: SystemContext, symbol: str, strategy: str, seq: int,
                exit_leg: str, qty: int = 100, entry: float = 500.0,
                sl: float = 490.0) -> tuple:
    """One REAL round-trip through the wired path, asserting the invariant at every stage.

    exit_leg: "SL" (loss) or "TGT" (win). Returns (closed_trade, stage_pictures).
    """
    sid = f"q9c_{seq}"
    _seed_signal_row(ctx, sid, symbol)
    stages = {"before": _assert_invariant(ctx, f"{seq}:before")}

    res = ctx.fund_manager.reserve(
        symbol=symbol, qty=qty, price=entry, intent="INTRADAY", signal_id=sid,
    )
    assert res.success, f"reserve failed on #{seq}: {res}"
    stages["reserved"] = _assert_invariant(ctx, f"{seq}:after-reserve")
    # ANTI-VACUITY: reserving must MOVE capital available -> reserved, and touch nothing else.
    assert stages["reserved"]["reserved"] > stages["before"]["reserved"], (
        f"#{seq}: reserve did not increase reserved capital"
    )
    assert stages["reserved"]["avail"] < stages["before"]["avail"], (
        f"#{seq}: reserve did not decrease available capital"
    )
    assert stages["reserved"]["used"] == pytest.approx(stages["before"]["used"])
    assert stages["reserved"]["reader"] == pytest.approx(stages["before"]["reader"]), (
        f"#{seq}: reserving moved realized P&L — it must not"
    )

    ctx.order_placer.place(
        symbol=symbol, side="BUY", qty=qty, entry_price=entry, sl_price=sl,
        intent="INTRADAY", signal_id=sid, reservation_id=res.reservation_id,
        strategy=strategy,
    )
    with ctx.order_placer._fill_map_lock:
        entry_iid = next(
            i for i, f in ctx.order_placer._fill_map.items()
            if f.leg == "ENTRY" and f.trade_id is not None
            and ctx.order_manager.get_trade(f.trade_id)["symbol"] == symbol
            and ctx.order_manager.get_trade(f.trade_id)["status"] not in ("CLOSED",)
        )
        trade_id = ctx.order_placer._fill_map[entry_iid].trade_id

    ctx.bus.publish(OrderFilled(
        source_module="q9c", internal_order_id=entry_iid,
        broker_order_id=f"E{seq}", symbol=symbol, side="BUY", filled_qty=qty,
        avg_fill_price=entry, expected_price=entry, slippage_pct=0.0,
        filled_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    ))
    assert ctx.order_manager.get_trade(trade_id)["status"] == "OPEN"
    stages["filled"] = _assert_invariant(ctx, f"{seq}:after-entry-fill")
    # ANTI-VACUITY: the fill converts reserved -> used.
    assert stages["filled"]["used"] > stages["reserved"]["used"], (
        f"#{seq}: entry fill did not commit capital into `used`"
    )
    assert stages["filled"]["reserved"] < stages["reserved"]["reserved"], (
        f"#{seq}: entry fill did not consume the reservation"
    )

    with ctx.order_placer._fill_map_lock:
        exit_iid = next(
            i for i, f in ctx.order_placer._fill_map.items()
            if f.leg == exit_leg and f.trade_id == trade_id
        )
    exit_px = sl if exit_leg == "SL" else float(
        ctx.order_manager.get_trade(trade_id)["tgt_initial"]
    )
    ctx.bus.publish(OrderFilled(
        source_module="q9c", internal_order_id=exit_iid,
        broker_order_id=f"X{seq}", symbol=symbol, side="SELL", filled_qty=qty,
        avg_fill_price=exit_px, expected_price=exit_px, slippage_pct=0.0,
        filled_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    ))
    closed = ctx.order_manager.get_trade(trade_id)
    assert closed["status"] == "CLOSED", f"#{seq}: exit fill did not close the trade"
    stages["closed"] = _assert_invariant(ctx, f"{seq}:after-close")
    # ANTI-VACUITY: the close releases the committed capital and moves realized P&L.
    assert stages["closed"]["used"] == pytest.approx(stages["before"]["used"]), (
        f"#{seq}: capital left stranded in `used` after close"
    )
    assert stages["closed"]["reader"] != pytest.approx(stages["filled"]["reader"]), (
        f"#{seq}: a completed round-trip did not move realized P&L at all"
    )
    return closed, stages


# ── the tests ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestCapitalInvariantWired:

    def test_invariant_holds_at_every_stage_of_a_losing_round_trip(self, wired_system):
        """Stages a-d: fresh -> reserve -> fill -> close, invariant + movement at each."""
        ctx = wired_system
        closed, st = _round_trip(ctx, "RELIANCE", "vwap_bounce_long", 1, exit_leg="SL")

        assert closed["gross_pnl"] < 0, "SL exit on a LONG must be a loss"
        assert closed["net_pnl"] < closed["gross_pnl"], "costs must make net worse than gross"
        # I2: _total moved by exactly the realized net P&L.
        assert st["closed"]["total"] - PAPER_CAPITAL == pytest.approx(
            st["closed"]["truth"], abs=TOL
        ), (
            f"I2 BROKEN: total moved by {st['closed']['total'] - PAPER_CAPITAL:.4f} but "
            f"realized net P&L is {st['closed']['truth']:.4f}"
        )
        assert st["closed"]["total"] < st["before"]["total"], "a loss must reduce total capital"

    def test_invariant_holds_through_a_winning_round_trip(self, wired_system):
        """Stage g. Nothing wired had ever driven a WINNER through the capital path — a sign
        error in the P&L direction is invisible to a losses-only test."""
        ctx = wired_system
        closed, st = _round_trip(ctx, "TCS", "vwap_bounce_long", 2, exit_leg="TGT")

        assert closed["gross_pnl"] > 0, "TGT exit on a LONG must be a profit"
        assert closed["net_pnl"] < closed["gross_pnl"], "costs still reduce a winner"
        assert closed["net_pnl"] > 0, "the win must survive costs for this assertion to mean anything"
        # ⭐ THE SIGN CHECK: a winner must RAISE total, by exactly the net.
        assert st["closed"]["total"] > st["before"]["total"], (
            f"A WINNING TRADE DID NOT INCREASE TOTAL CAPITAL "
            f"({st['before']['total']:.2f} -> {st['closed']['total']:.2f}) — P&L sign error"
        )
        assert st["closed"]["total"] - PAPER_CAPITAL == pytest.approx(
            st["closed"]["truth"], abs=TOL
        )

    def test_rejected_signal_moves_no_capital(self, wired_system):
        """Stage e: a signal rejected before reservation must move nothing at all."""
        ctx = wired_system
        before = _assert_invariant(ctx, "rejected:before")

        ctx.kill_switch.soft_kill(reason="q9c_reject_probe", triggered_by="q9_batch3")
        sid = "q9c_rejected"
        _seed_signal_row(ctx, sid, "INFY")
        res = ctx.fund_manager.reserve(
            symbol="INFY", qty=10, price=100.0, intent="INTRADAY", signal_id=sid,
        )
        from core.exceptions import OrderRejectedError
        with pytest.raises(OrderRejectedError):
            ctx.order_placer.place(
                symbol="INFY", side="BUY", qty=10, entry_price=100.0, sl_price=98.0,
                intent="INTRADAY", signal_id=sid, reservation_id=res.reservation_id,
                strategy="vwap_bounce_long",
            )
        ctx.kill_switch.resume(reason="q9c_reject_probe_done", resumed_by="q9_batch3")

        after = _picture(ctx)
        assert round(after["lhs"], 2) == pytest.approx(round(after["total"], 2), abs=TOL)
        for k in ("total", "avail", "reserved", "used", "reader", "truth"):
            assert after[k] == pytest.approx(before[k], abs=TOL), (
                f"a REJECTED signal moved {k}: {before[k]} -> {after[k]}"
            )

    def test_invariant_holds_with_concurrent_positions_across_strategies(self, wired_system):
        """Stage f: aggregation errors in reserved/used only show up with more than one
        position. Seeded across DIFFERENT strategies — the per-strategy cap
        (signal_processor.py:624-640) fires before the risk engine, which is exactly what
        batch 2 had to fix."""
        ctx = wired_system
        before = _assert_invariant(ctx, "multi:before")

        opened = []
        for i, (sym, strat) in enumerate(
            [("RELIANCE", "vwap_bounce_long"), ("TCS", "gap_go_long")], start=10
        ):
            sid = f"q9c_multi_{i}"
            _seed_signal_row(ctx, sid, sym)
            res = ctx.fund_manager.reserve(
                symbol=sym, qty=50, price=500.0, intent="INTRADAY", signal_id=sid,
            )
            assert res.success
            ctx.order_placer.place(
                symbol=sym, side="BUY", qty=50, entry_price=500.0, sl_price=490.0,
                intent="INTRADAY", signal_id=sid, reservation_id=res.reservation_id,
                strategy=strat,
            )
            with ctx.order_placer._fill_map_lock:
                iid = next(
                    k for k, f in ctx.order_placer._fill_map.items()
                    if f.leg == "ENTRY" and f.trade_id is not None
                    and ctx.order_manager.get_trade(f.trade_id)["symbol"] == sym
                    and ctx.order_manager.get_trade(f.trade_id)["status"] != "CLOSED"
                )
                tid = ctx.order_placer._fill_map[iid].trade_id
            ctx.bus.publish(OrderFilled(
                source_module="q9c", internal_order_id=iid, broker_order_id=f"M{i}",
                symbol=sym, side="BUY", filled_qty=50, avg_fill_price=500.0,
                expected_price=500.0, slippage_pct=0.0,
                filled_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))
            opened.append(tid)
            _assert_invariant(ctx, f"multi:after-open-{i}")

        two_open = _assert_invariant(ctx, "multi:both-open")
        assert len(opened) == 2
        # ANTI-VACUITY: two concurrent positions must hold strictly more `used` than one.
        assert two_open["used"] > before["used"], "no capital committed with 2 positions open"
        assert ctx.store.fetch_one(
            "SELECT COUNT(*) AS c FROM trades WHERE status IN ('OPEN','PARTIAL')"
        )["c"] == 2, "expected exactly 2 concurrent open positions"


# ── the CONTRACT invariant (I5) — the E4/W10 catcher ─────────────────────────

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestContractInvariant:
    """I5: the reader's realized P&L must equal the independent ground truth.

    ⚠️ DESIGN — READ BEFORE CHANGING EITHER TEST BELOW.

    I5 is FALSE TODAY by the known E4/W10 bug, so it cannot be a plain green assertion, and
    it must not be "fixed" into passing. The pair below satisfies four requirements at once:

      1. green today — the suite does not go red on a known, accepted bug (strict xfail);
      2. it cannot silently start passing when E4/W10 lands — strict xfail reports an XPASS
         as a FAILURE, forcing a deliberate flip as part of that migration instead of a stale
         test quietly rotting in the suite;
      3. no rewrite needed then, and no hard-coded rupee figure anywhere (rule D);
      4. a NEW discrepancy cannot hide behind the old one — the second test asserts, plain
         green, that the delta is EXACTLY the double-counted costs and nothing else.

    Strict xfail was chosen over alternatives (a skip hides it; a plain assertion of current
    buggy behaviour bakes the bug in and would need rewriting; a warning is ignorable).
    """

    @pytest.mark.xfail(
        strict=True,
        reason="E4/W10: get_daily_realized_net_pnl double-subtracts costs "
               "(pnl_delta is already NET). Fixed on branch e4-w10-pnl-contract@ad34ee4, "
               "UNPUSHED pending Rama's risk-posture sign-off. When that lands this XPASSes, "
               "and strict=True turns the XPASS into a FAILURE so the migration is deliberate.",
    )
    def test_reader_equals_independent_ground_truth(self, wired_system):
        ctx = wired_system
        _round_trip(ctx, "RELIANCE", "vwap_bounce_long", 20, exit_leg="SL")
        p = _picture(ctx)
        assert p["costs"] > 0, "scenario must incur real costs or this proves nothing"
        assert p["reader"] == pytest.approx(p["truth"], abs=TOL), (
            f"CONTRACT INVARIANT: reader={p['reader']:.4f} vs ground truth={p['truth']:.4f}"
        )

    def test_the_discrepancy_is_exactly_the_double_counted_costs(self, wired_system):
        """Requirement (4): pins the delta to E4/W10 EXACTLY, so a NEW value bug breaks the
        suite immediately even while the old one is still xfailed.

        reader = SUM(pnl_delta) - SUM(costs), and pnl_delta is already (gross - costs),
        so reader = truth - SUM(costs)  =>  reader - truth == -SUM(costs), exactly.
        """
        ctx = wired_system
        _round_trip(ctx, "RELIANCE", "vwap_bounce_long", 21, exit_leg="SL")
        p = _picture(ctx)
        assert p["costs"] > 0, "no costs were incurred — the delta would be trivially 0"
        assert (p["reader"] - p["truth"]) == pytest.approx(-p["costs"], abs=TOL), (
            f"THE READER/GROUND-TRUTH DISCREPANCY IS NO LONGER EXACTLY THE DOUBLE-COUNTED "
            f"COSTS: reader({p['reader']:.4f}) - truth({p['truth']:.4f}) = "
            f"{p['reader'] - p['truth']:.4f}, but -costs = {-p['costs']:.4f}. Either E4/W10 "
            f"changed shape or there is a NEW value-level bug — investigate before touching "
            f"this test."
        )
