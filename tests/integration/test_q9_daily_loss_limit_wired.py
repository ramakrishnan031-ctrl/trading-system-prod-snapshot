"""
tests/integration/test_q9_daily_loss_limit_wired.py — Q9 batch 1.

THE DAILY LOSS LIMIT, PROVEN THROUGH THE WIRED PATH.

Why this file exists (Q9 coverage map, docs/audit/q9_money_path_coverage_map_18jul2026.md):
the daily loss limit is the layer that stops the account bleeding on a bad day, and it was
the #1 gap by cost-of-silent-failure — BOTH halves were UNIT-ONLY and **no integration test
had ever driven a realized loss past the threshold**. The two tests that looked like coverage
accepted any of nine rejection statuses, so they proved "some gate rejected", never which.
Q5's refinement of the fixture-blindness lesson applied exactly here: the limit was
CONFIGURED (0.02 / 0.05 in conftest) and simply never REACHED, so its rejecting branch never
executed in the suite.

TWO HALVES — different mechanisms, both proven here:
  * PRE-TRADE gate (RE7)  — capital/risk_engine.py:568-604, reading
    fund_manager.get_snapshot().daily_realized_pnl → the next signal is REJECTED.
  * POST-CLOSE breach     — capital/fund_manager.py:1279-1290, computed inside release_used
    on the close itself → fires the on_daily_loss_breach callback.
Both read the SAME value: StateStore.get_daily_realized_net_pnl(today).

⚠️ ASSERTION DESIGN — READ BEFORE "IMPROVING" THIS FILE ⚠️
Do NOT hard-code a rupee threshold here. The reader's contract is mid-migration: today
get_daily_realized_net_pnl computes SUM(pnl_delta) - SUM(costs), while E4/W10 (branch
e4-w10-pnl-contract@ad34ee4, UNPUSHED, awaiting risk-posture sign-off) changes it to
SUM(pnl_delta) because pnl_delta is already NET. A literal figure would bake in today's
double-subtracting behaviour and break the day that branch lands; conversely an assertion
phrased in terms of the TRUE net loss would be wrong today, because today's reader
over-states the loss and therefore fires EARLIER.

So every assertion below is phrased RELATIVE TO THE READER'S OWN RETURNED VALUE:
    "whatever get_daily_realized_net_pnl() returns, once it is <= -(pct * total) the next
     signal is REJECTED_DAILY_LOSS; while it is above that, DAILY_LOSS is not the reason."
That is correct under BOTH contracts, it proves the WIRING (which is Q9's job), and it stays
green when E4/W10 merges.

  Q9 proves the layer is wired and fires. E4/W10 fixes whether the number it reads is right.
  Do not conflate them.

(Related known distortion, deliberately not depended upon: RMS/CHECK1 closes pass costs=0.0.
This scenario drives ordinary SL closes through order_manager, which cost them normally.)

SCENARIO SHAPE — constrained by the gate ORDER, which matters:
RE5 runs ... DAILY_TRADES, CONSECUTIVE_LOSSES, DAILY_LOSS ... so CONSECUTIVE_LOSSES is checked
BEFORE DAILY_LOSS. conftest sets max_consecutive_losses=4, so the scenario must cross the
5% pre-trade limit in **at most 3** losing closes or the wrong gate fires and the test would
pass for the wrong reason. With PAPER_CAPITAL=500,000 the limits are 10,000 (post-close, 2%)
and 25,000 (pre-trade, 5%); three ~9,000 losses cross 25,000 while keeping the consecutive
count at 3. Each polarity assertion is preceded by an explicit precondition assert on the
reader, so the test can never pass vacuously by failing to reach the threshold.
"""
from __future__ import annotations

import time

import pytest

from core.events import OrderFilled
from core.time_authority import now_ist
from tests.integration.conftest import PAPER_CAPITAL, SCANNER_NAME, SystemContext
from tests.integration.test_full_signal_flow import (
    _make_payload,
    _post_webhook,
    _seed_signal_row,
    _wait_for_signal,
    _wait_for_signal_status,
)
from unittest.mock import patch

# conftest: FundManager(daily_loss_limit_pct=0.02) · RiskEngine(daily_loss_limit_pct=0.05)
POST_CLOSE_PCT = 0.02
PRE_TRADE_PCT = 0.05

# ~9,000 gross loss per close: big enough that 3 cross the 5% (25,000) pre-trade limit,
# small enough that 1 stays clear of the 2% (10,000) post-close limit — giving a clean
# negative case for BOTH halves. See the docstring on why 3 (not 4) closes.
LOSS_QTY = 100
LOSS_ENTRY = 500.0
LOSS_SL = 410.0


def _reader(ctx: SystemContext) -> float:
    """The value BOTH halves enforce on (fund_manager.py:1279 and :1507)."""
    return ctx.store.get_daily_realized_net_pnl(now_ist().date().isoformat())


def _capital_picture(ctx: SystemContext) -> dict:
    """The full money picture asserted at every transition (Q9 rule A)."""
    snap = ctx.fund_manager.get_snapshot()
    return {
        "total": snap.total,
        "intraday_avail": snap.intraday_avail,
        "intraday_reserved": snap.intraday_reserved,
        "intraday_used": snap.intraday_used,
        "daily_realized_pnl": snap.daily_realized_pnl,
        "reader": _reader(ctx),
    }


def _drive_close(ctx: SystemContext, symbol: str, seq: int,
                 exit_price: float = LOSS_SL, sl_price: float = LOSS_SL,
                 tag: str = "loss") -> dict:
    """Drive ONE real round-trip through the WIRED path: reserve → place → ENTRY
    fill → exit fill → close. Asserts the capital picture before and after (Q9 rule A).

    `exit_price` is the price the exit leg FILLS at, and it is what decides the sign:
    below LOSS_ENTRY is a loss, above it is a win. `sl_price` stays the price the SL
    order is PLACED at, so a winning exit is still placed with a realistic stop.

    Defaults reproduce the original losing round-trip exactly, so batches 1 and 5 are
    untouched. The win form exists for Q9's consecutive-losses RESET path, which has to
    prove a winner actually breaks the streak.

    Returns the closed trade row.
    """
    signal_id = f"q9_{tag}_{seq}"
    expect_loss = exit_price < LOSS_ENTRY
    _seed_signal_row(ctx, signal_id, symbol)

    before = _capital_picture(ctx)

    res = ctx.fund_manager.reserve(
        symbol=symbol, qty=LOSS_QTY, price=LOSS_ENTRY,
        intent="INTRADAY", signal_id=signal_id,
    )
    assert res.success, f"reserve failed on loss #{seq}: {res}"

    after_reserve = _capital_picture(ctx)
    assert after_reserve["intraday_reserved"] > before["intraday_reserved"], (
        f"loss #{seq}: reserve did not increase reserved capital "
        f"({before['intraday_reserved']} → {after_reserve['intraday_reserved']})"
    )
    # A reservation must not move realized P&L.
    assert after_reserve["reader"] == pytest.approx(before["reader"]), (
        f"loss #{seq}: reserving changed realized P&L — it must not"
    )

    ctx.order_placer.place(
        symbol=symbol, side="BUY", qty=LOSS_QTY,
        entry_price=LOSS_ENTRY, sl_price=sl_price,
        intent="INTRADAY", signal_id=signal_id,
        reservation_id=res.reservation_id,
    )

    with ctx.order_placer._fill_map_lock:
        entry_iid = next(
            iid for iid, fe in ctx.order_placer._fill_map.items()
            if fe.leg == "ENTRY" and fe.trade_id is not None
            and ctx.order_manager.get_trade(fe.trade_id)["symbol"] == symbol
            and ctx.order_manager.get_trade(fe.trade_id)["status"] != "CLOSED"
        )
        trade_id = ctx.order_placer._fill_map[entry_iid].trade_id

    ctx.bus.publish(OrderFilled(
        source_module="q9_test", internal_order_id=entry_iid,
        broker_order_id=f"PAPER_ENTRY_{symbol}_{seq}", symbol=symbol, side="BUY",
        filled_qty=LOSS_QTY, avg_fill_price=LOSS_ENTRY, expected_price=LOSS_ENTRY,
        slippage_pct=0.0, filled_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    ))
    opened = ctx.order_manager.get_trade(trade_id)
    assert opened["status"] == "OPEN", f"loss #{seq}: entry did not open the trade"

    after_open = _capital_picture(ctx)
    assert after_open["intraday_used"] > after_reserve["intraday_used"], (
        f"loss #{seq}: fill did not commit reserved → used capital"
    )

    with ctx.order_placer._fill_map_lock:
        sl_iid = next(
            iid for iid, fe in ctx.order_placer._fill_map.items()
            if fe.leg == "SL" and fe.trade_id == trade_id
        )
    ctx.bus.publish(OrderFilled(
        source_module="q9_test", internal_order_id=sl_iid,
        broker_order_id=f"PAPER_SL_{symbol}_{seq}", symbol=symbol, side="SELL",
        filled_qty=LOSS_QTY, avg_fill_price=exit_price, expected_price=exit_price,
        slippage_pct=0.0, filled_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    ))

    closed = ctx.order_manager.get_trade(trade_id)
    assert closed["status"] == "CLOSED", f"loss #{seq}: SL fill did not close the trade"

    after_close = _capital_picture(ctx)
    # gross/net relationship — NOT a hard-coded figure (see the module docstring).
    # Costs always push net BELOW gross, whichever side the trade landed on.
    assert closed["net_pnl"] <= closed["gross_pnl"], (
        f"{tag} #{seq}: net must sit below gross once costs are deducted"
    )
    if expect_loss:
        assert closed["gross_pnl"] < 0, f"{tag} #{seq}: expected a LOSS"
    else:
        assert closed["gross_pnl"] > 0, f"{tag} #{seq}: expected a WIN"
        assert closed["net_pnl"] > 0, (
            f"{tag} #{seq}: costs turned the win into a net loss ({closed['net_pnl']:.2f}); "
            f"it would NOT break a consecutive-loss streak — widen exit_price"
        )
    # The close must release the capital it committed and move realized P&L down.
    assert after_close["intraday_used"] == pytest.approx(before["intraday_used"]), (
        f"loss #{seq}: used capital not released on close"
    )
    assert after_close["intraday_reserved"] == pytest.approx(before["intraday_reserved"]), (
        f"loss #{seq}: reserved capital not released on close"
    )
    if expect_loss:
        assert after_close["reader"] < before["reader"], (
            f"{tag} #{seq}: a losing close did not move realized P&L downward "
            f"({before['reader']} → {after_close['reader']})"
        )
    else:
        assert after_close["reader"] > before["reader"], (
            f"{tag} #{seq}: a winning close did not move realized P&L upward "
            f"({before['reader']} → {after_close['reader']})"
        )
    # The snapshot the PRE-TRADE gate reads must agree with the reader.
    assert after_close["daily_realized_pnl"] == pytest.approx(after_close["reader"]), (
        "snapshot.daily_realized_pnl and get_daily_realized_net_pnl disagree — the "
        "pre-trade gate and the post-close breach would enforce on different numbers"
    )
    return closed


def _drive_losing_close(ctx: SystemContext, symbol: str, seq: int) -> dict:
    """The original losing round-trip, unchanged. Batches 1 and 5 call this."""
    return _drive_close(ctx, symbol, seq)


_TERMINAL_STATUSES = {
    "REJECTED_DAILY_LOSS", "REJECTED_OPEN_POSITIONS", "REJECTED_DAILY_TRADES",
    "REJECTED_KILL_SWITCH", "REJECTED_DUPLICATE_SYMBOL",
    "REJECTED_CONSECUTIVE_LOSSES", "REJECTED_SIZING_VALID", "REJECTED_CAPITAL",
    "REJECTED_STRATEGY_POSITION_LIMIT", "PASSED", "PLACED", "PROCESSED",
}


def _probe_signal_status(ctx: SystemContext, symbol: str, price: float,
                         ignore: set[str] | None = None) -> str | None:
    """Push ONE signal through the wired webhook→screen→risk path; return its terminal status.

    ``ignore`` drops statuses from the wait set. It exists because "PASSED" is the
    SCREENING verdict (screening/secondary_screener.py:353,512), reached BEFORE the
    risk engine rules — so waiting on it can return while the risk verdict is still
    in flight, and any positive assertion on a specific REJECTED_* becomes a race.
    Callers asserting a specific risk rejection should pass ignore={"PASSED"}.
    Default behaviour is unchanged.
    """
    ctx.sim_kite.set_rich_quote(symbol, ltp=price)
    rich_md = ctx.sim_kite.get_market_data(symbol)
    with patch.object(ctx.screener, "_build_market_data", return_value=rich_md):
        code, data = _post_webhook(
            ctx, SCANNER_NAME, _make_payload(symbol=symbol, price=str(price))
        )
        assert code == 200 and data["accepted"] == 1
        signal_id = _wait_for_signal(ctx, symbol, timeout=3.0)
        assert signal_id is not None
        terminal = _wait_for_signal_status(
            ctx, signal_id,
            _TERMINAL_STATUSES - (ignore or set()),
            timeout=6.0,
        )
    return terminal


@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestDailyLossLimitWired:
    """Both halves of the daily loss limit, positive AND negative, through the real path."""

    def test_daily_loss_limit_both_halves_positive_and_negative(self, wired_system):
        ctx = wired_system

        # Production wires the breach callback at construction (main.py:757
        # _make_daily_loss_breach_cb → CRITICAL + notifier + soft_kill); the integration
        # fixture leaves it None. Attach a recorder so the POST-CLOSE half's dispatch is
        # observable — this is the same attribute the constructor sets.
        breach_calls: list[float] = []
        ctx.fund_manager._on_loss_breach = lambda: breach_calls.append(_reader(ctx))

        assert ctx.fund_manager.get_snapshot().total == pytest.approx(PAPER_CAPITAL)

        # ⚠️ BOTH halves compute their limit as pct * the CURRENT total, and `total`
        # SHRINKS as realized losses accrue (risk_engine.py:569, fund_manager.py:1280).
        # Re-derive at each check rather than caching a start-of-day figure, or the test
        # asserts against a limit the gate is not using.
        def post_close_limit() -> float:
            return POST_CLOSE_PCT * ctx.fund_manager.get_snapshot().total

        def pre_trade_limit() -> float:
            return PRE_TRADE_PCT * ctx.fund_manager.get_snapshot().total

        # ── loss #1 — below BOTH limits ────────────────────────────────────────
        _drive_losing_close(ctx, "RELIANCE", 1)
        r1 = _reader(ctx)
        assert r1 > -post_close_limit(), (
            f"scenario precondition failed: after 1 loss the reader ({r1:.2f}) already "
            f"crossed the post-close limit ({-post_close_limit():.2f}); the negative case "
            f"cannot be proven — reduce LOSS_* sizing"
        )
        # NEGATIVE (post-close half): below the limit → the breach must NOT have fired.
        assert breach_calls == [], (
            f"post-close breach fired at {r1:.2f}, above the limit {-post_close_limit():.2f}"
        )

        # ── loss #2 — crosses the POST-CLOSE limit, still below the PRE-TRADE limit ──
        _drive_losing_close(ctx, "TCS", 2)
        r2 = _reader(ctx)
        assert r2 <= -post_close_limit(), (
            f"scenario precondition failed: after 2 losses the reader ({r2:.2f}) has not "
            f"crossed the post-close limit ({-post_close_limit():.2f})"
        )
        # POSITIVE (post-close half): crossing the limit MUST dispatch the breach.
        assert len(breach_calls) >= 1, (
            f"POST-CLOSE HALF DID NOT FIRE: reader {r2:.2f} <= limit {-post_close_limit():.2f} "
            f"but on_daily_loss_breach was never called (fund_manager.py:1279-1290)"
        )

        # NEGATIVE (pre-trade half): still above the 5% limit → DAILY_LOSS must NOT be
        # the rejection reason. (It may pass or be rejected for another reason; what is
        # forbidden is DAILY_LOSS.)
        assert r2 > -pre_trade_limit(), (
            f"scenario precondition failed: reader {r2:.2f} already crossed the pre-trade "
            f"limit {-pre_trade_limit():.2f} after 2 losses — the negative case is unprovable"
        )
        status_below = _probe_signal_status(ctx, "INFY", 1500.0)
        assert status_below != "REJECTED_DAILY_LOSS", (
            f"PRE-TRADE GATE FIRED TOO EARLY: reader {r2:.2f} is above the limit "
            f"{-pre_trade_limit():.2f}, yet the signal was rejected for DAILY_LOSS"
        )
        # Being admitted, this probe legitimately holds a reservation (the fixture's
        # paper_auto_fill_delay_sec=60 means it never fills). That outstanding reservation
        # is accounted for in the final picture below — it is evidence the gate let it
        # through, not leakage.
        probe_reserved = _capital_picture(ctx)["intraday_reserved"]

        # ── loss #3 — crosses the PRE-TRADE limit ──────────────────────────────
        _drive_losing_close(ctx, "WIPRO", 3)
        r3 = _reader(ctx)
        assert r3 <= -pre_trade_limit(), (
            f"scenario precondition failed: after 3 losses the reader ({r3:.2f}) has not "
            f"crossed the pre-trade limit ({-pre_trade_limit():.2f}); increase LOSS_* sizing "
            f"— but keep the count at 3, because max_consecutive_losses=4 is checked FIRST"
        )
        # Guard the scenario against the neighbouring gates, so a REJECTED_DAILY_LOSS
        # verdict can only mean the DAILY_LOSS gate fired.
        open_positions = ctx.store.fetch_one(
            "SELECT COUNT(*) AS c FROM trades WHERE status IN ('OPEN','PARTIAL')"
        )["c"]
        assert open_positions == 0, (
            f"{open_positions} open positions — OPEN_POSITIONS could fire before DAILY_LOSS"
        )

        # POSITIVE (pre-trade half): rejected with REJECTED_DAILY_LOSS *specifically*.
        status_above = _probe_signal_status(ctx, "SBIN", 400.0)
        assert status_above == "REJECTED_DAILY_LOSS", (
            f"PRE-TRADE GATE DID NOT FIRE: reader {r3:.2f} <= limit {-pre_trade_limit():.2f} "
            f"but the signal terminated as {status_above!r}, not REJECTED_DAILY_LOSS "
            f"(capital/risk_engine.py:598-604)"
        )

        # No trade may be created for a signal the gate rejected.
        assert ctx.store.fetch_all("SELECT * FROM trades WHERE symbol = ?", ("SBIN",)) == []

        # Final capital picture: the limit blocked new risk without corrupting the ledger.
        final = _capital_picture(ctx)
        # Every COMMITTED rupee from the three closed losers was released ...
        assert final["intraday_used"] == pytest.approx(0.0), (
            "committed capital left stranded after all trades closed"
        )
        # ... and the only reservation outstanding is the admitted negative probe (the
        # rejected signal must not have reserved anything).
        assert final["intraday_reserved"] == pytest.approx(probe_reserved), (
            f"reserved capital moved after the rejected signal "
            f"({probe_reserved} → {final['intraday_reserved']}) — a REJECTED signal must "
            f"never reserve capital"
        )
        assert final["daily_realized_pnl"] == pytest.approx(final["reader"])
        assert final["total"] < PAPER_CAPITAL, "realized losses must reduce total capital"
