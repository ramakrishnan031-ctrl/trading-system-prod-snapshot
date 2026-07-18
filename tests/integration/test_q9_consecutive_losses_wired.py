"""
tests/integration/test_q9_consecutive_losses_wired.py

Q9 LAYER 14 — the CONSECUTIVE_LOSSES gate (RE10), wired. The last unwired layer of
the Q9 programme, deliberately left out of the M-C1 batch because it needs >= 4
consecutive losing closes and that collides with its neighbours.

THE MAP
    capital/risk_engine.py:250   pnls   = store.recent_trade_pnls(max_consec + 1, today=today)
    capital/risk_engine.py:251   consec = _count_trailing_losses(pnls)
    capital/risk_engine.py:552   checks_run.append("CONSECUTIVE_LOSSES")
    capital/risk_engine.py:553   if consec >= max_consec:  reject("CONSECUTIVE_LOSSES", ...)
    core/state_store.py:851      recent_trade_pnls(n, today) -- the only data source

THERE IS NO COUNTER. This is the finding that shapes every test below: the streak is
RECOMPUTED on every approve() from the trades table, never incremented and never held
in memory. Three consequences, each proven here rather than described:

  * WHAT RESETS IT (a) any non-loss close -- _count_trailing_losses counts only the
    TRAILING run, and per RE10 a loss is net_pnl < -1e-6, so a breakeven breaks the
    streak too; (b) a NEW DAY -- FIX-183 scopes the query to SUBSTR(exit_time,1,10).
    FIX-183's own comment records why: a cross-day streak was a DEADLOCK, because
    breaking it needs a winning trade and the block makes one impossible.
  * IT SURVIVES A RESTART BY CONSTRUCTION -- same shape as batch 5's daily P&L
    (FIX-051). A restart is NOT a bypass. Proven in TestSurvivesRestart.
  * The gate reads at ENTRY while the streak only changes at EXIT, so positions
    already in flight are not retro-blocked. That is not a defect; it is why
    production has recorded streaks of 5 and 6 against a threshold of 4 (see below).

THE SCENARIO COLLISION (the whole difficulty)
    RE5 order:  ... 4.OPEN_POSITIONS  5.DAILY_TRADES  6.CONSECUTIVE_LOSSES  7.DAILY_LOSS
    ABOVE: DAILY_TRADES. The fixture allows 20 and we use 5 trades, so it cannot mask.
    BELOW: the DAILY_LOSS *gate* is check 7 -- AFTER us -- so it cannot mask the gate
           at all. The real hazard is the *post-close* breach arming a SOFT KILL, which
           would then fire KILL_SWITCH at check 1 and produce a rejection that looks
           like success. The fund_manager limit is 2% of 500,000 = Rs 10,000.
    So the losses must be SMALL. tests' shared _drive_close defaults to exit 410.0 =
    -9,000 gross per close; four of those is -36,000 and would blow through both
    limits. We drive exit 490.0 = -1,000 gross instead, asserted rather than assumed.

REACHABILITY (B9) -- measured against PRODUCTION, not the fixture. See the report:
    the precondition has been met on 3 of 21 trading days (streaks of 5, 5, 6), but
    the gate has produced 0 rejections in 32,928 signals, because by the time the
    streak completes the day's entries have already stopped for other reasons.
    REACHABLE, correctly configured, never yet binding.

⚠️ EVERY CLASS HERE MUST KEEP paper_auto_fill_delay_sec=60.0. DO NOT REMOVE IT.
    The fixture default is 0.05, i.e. the paper adapter auto-fills an order 50 ms after
    it is placed, ASYNCHRONOUSLY. These tests publish their own ENTRY/exit fills to
    control the exit price and therefore the sign of each close, so the adapter's
    auto-fill RACES them: win the race and the close lands at the intended price, lose
    it and the trade closes at the adapter's, corrupting the P&L and hence the streak.
    It cost one full-suite failure that passed in isolation and in tests/integration --
    the signature of a race, since only the slower full run lost it. Every other Q9
    batch sets 60.0 for the same reason.

TEST-ONLY. No production file is modified by this module.
"""
from __future__ import annotations

import pytest

from core.time_authority import now_ist
from tests.integration.conftest import SystemContext, PAPER_CAPITAL
from tests.integration.test_q9_daily_loss_limit_wired import (
    LOSS_ENTRY, PRE_TRADE_PCT, _drive_close, _probe_signal_status, _reader,
)
from tests.integration.test_q9_post_restart_capital_wired import _restart

TOL = 0.01

# A loss small enough that four of them stay well inside BOTH daily-loss limits.
# gross = 100 * (490 - 500) = -1,000 per close  (vs the shared default's -9,000).
SMALL_LOSS_EXIT = 490.0
# A win big enough that costs cannot flip it negative -- asserted in _drive_close.
WIN_EXIT = 510.0


def _today() -> str:
    return now_ist().date().isoformat()


def _streak(ctx: SystemContext) -> int:
    """The streak EXACTLY as the gate computes it -- same store call, same counter.

    Deliberately not a re-implementation: if either production function changes shape,
    this moves with it, and the anti-vacuity assertions below stay honest.
    """
    pnls = ctx.store.recent_trade_pnls(ctx.risk_engine._max_consec + 1, today=_today())
    return ctx.risk_engine._count_trailing_losses(pnls)


def _max_consec(ctx: SystemContext) -> int:
    """Read the threshold off the engine rather than hard-coding 4, so the test
    tracks the fixture instead of silently disagreeing with it."""
    return ctx.risk_engine._max_consec


def _assert_no_earlier_gate_can_mask(ctx: SystemContext, stage: str) -> None:
    """Checks 1-5 run BEFORE CONSECUTIVE_LOSSES. If any of them could fire, a
    rejection proves nothing. Asserted at the moment of the probe, not assumed."""
    assert not ctx.kill_switch.is_active(), (
        f"{stage}: the KILL SWITCH is armed (check 1) — it would mask this gate. "
        "A post-close daily-loss breach most likely soft-killed mid-scenario."
    )
    daily = ctx.store.count_trades_today(_today())
    assert daily < ctx.risk_engine._max_daily, (
        f"{stage}: DAILY_TRADES (check 5) would fire first — {daily} trades today "
        f"vs max={ctx.risk_engine._max_daily}"
    )
    open_now = ctx.store.count_open_positions()
    assert open_now < ctx.risk_engine._max_open, (
        f"{stage}: OPEN_POSITIONS (check 4) would fire first — {open_now} open "
        f"vs max={ctx.risk_engine._max_open}"
    )
    # The DAILY_LOSS gate is check 7 (after us) so it cannot mask, but if the day's
    # loss were already past the pre-trade limit the scenario would be muddled.
    limit = PRE_TRADE_PCT * ctx.fund_manager.get_snapshot().total
    assert _reader(ctx) > -limit, (
        f"{stage}: the day's realized loss {_reader(ctx):.2f} is already past the "
        f"pre-trade limit {-limit:.2f} — losses are sized too large for a clean result"
    )


def _drive_small_loss(ctx: SystemContext, symbol: str, seq: int) -> dict:
    return _drive_close(ctx, symbol, seq, exit_price=SMALL_LOSS_EXIT,
                        sl_price=SMALL_LOSS_EXIT, tag="cl_loss")


def _drive_win(ctx: SystemContext, symbol: str, seq: int) -> dict:
    return _drive_close(ctx, symbol, seq, exit_price=WIN_EXIT,
                        sl_price=SMALL_LOSS_EXIT, tag="cl_win")


_SYMS = ("RELIANCE", "TCS", "INFY", "SBIN", "WIPRO", "HDFCBANK", "ITC", "AXISBANK")


def _drive_losses(ctx: SystemContext, n: int, start: int = 1) -> None:
    for i in range(n):
        _drive_small_loss(ctx, _SYMS[start - 1 + i], start + i)


# ═════════════════════════════════════════════════════════════════════════════
# 1. THE STREAK ITSELF — what increments it, and what resets it (B1a)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestTheStreakMechanics:

    def test_each_losing_close_advances_the_streak_by_exactly_one(self, wired_system):
        """ANTI-VACUITY FOUNDATION: everything below asserts the streak reached a
        value, so the streak reading itself has to be shown to move first."""
        ctx = wired_system
        assert _streak(ctx) == 0, "a fresh fixture must start with no streak"

        for i in range(1, _max_consec(ctx) + 1):
            _drive_small_loss(ctx, _SYMS[i - 1], i)
            assert _streak(ctx) == i, (
                f"after {i} consecutive losing closes the streak reads {_streak(ctx)}, "
                f"expected {i} — the gate would fire at the wrong time"
            )

    def test_the_loss_sizing_actually_stays_inside_both_daily_limits(self, wired_system):
        """The collision guard, asserted rather than trusted (the brief's arithmetic
        was for a different helper). If this fails, every result below is suspect."""
        ctx = wired_system
        _drive_losses(ctx, _max_consec(ctx))

        realized = _reader(ctx)
        assert realized < 0, "anti-vacuity: the scenario realized no loss at all"

        # (a) the POST-CLOSE breach that would soft-kill: fund_manager's 2%.
        post_close_limit = 0.02 * PAPER_CAPITAL
        assert abs(realized) < post_close_limit, (
            f"{_max_consec(ctx)} losses realized {realized:.2f}, at/past the post-close "
            f"limit {-post_close_limit:.2f} — a soft kill would mask this gate"
        )
        # (b) the PRE-TRADE gate's own limit (check 7, after us, but keep it clean).
        pre_trade_limit = PRE_TRADE_PCT * ctx.fund_manager.get_snapshot().total
        assert realized > -pre_trade_limit, (
            f"{_max_consec(ctx)} losses realized {realized:.2f}, past the pre-trade "
            f"limit {-pre_trade_limit:.2f}"
        )
        assert not ctx.kill_switch.is_active(), (
            "the kill switch ARMED during the scenario — the breach callback fired"
        )

    def test_a_winning_close_resets_the_streak_to_zero(self, wired_system):
        """B1a / B5 — PROVEN, not described. Drive to threshold-minus-one, close a
        WINNER, and the streak must break."""
        ctx = wired_system
        n = _max_consec(ctx)
        _drive_losses(ctx, n - 1)
        assert _streak(ctx) == n - 1, "setup did not reach threshold-minus-one"

        won = _drive_win(ctx, _SYMS[n - 1], n)
        assert won["net_pnl"] > 0, "anti-vacuity: the 'win' did not close positive"

        assert _streak(ctx) == 0, (
            f"a WINNING close left the streak at {_streak(ctx)} instead of 0 — the "
            "streak does not reset on a win, so the system would halt for the day"
        )

    def test_a_loss_after_a_win_starts_a_fresh_streak_not_a_resumed_one(self, wired_system):
        """The reset must be real, not cosmetic: the next loss must read 1, not n."""
        ctx = wired_system
        n = _max_consec(ctx)
        _drive_losses(ctx, n - 1)
        _drive_win(ctx, _SYMS[n - 1], n)
        _drive_small_loss(ctx, _SYMS[n], n + 1)

        assert _streak(ctx) == 1, (
            f"after win-then-loss the streak reads {_streak(ctx)}, expected 1 — the "
            "win did not truly break the run"
        )

    def test_the_streak_is_scoped_to_today(self, wired_system):
        """FIX-183. A yesterday loss must not count, or a 2-loss EOD deadlocks the
        next morning (FIX-183's own comment records exactly that outage)."""
        ctx = wired_system
        n = _max_consec(ctx)
        _drive_losses(ctx, n)
        assert _streak(ctx) == n

        # Re-date every close to yesterday; today's streak must collapse to 0.
        with ctx.store.transaction() as cur:
            cur.execute(
                "UPDATE trades SET exit_time = REPLACE(exit_time, ?, ?) "
                "WHERE status = 'CLOSED'",
                (_today(), (now_ist().date().replace(day=max(1, now_ist().day - 1))).isoformat()),
            )
        assert _streak(ctx) == 0, (
            "yesterday's losses still count toward today's streak — FIX-183's "
            "cross-day deadlock is back"
        )
        # and the un-scoped form still sees them, proving the rows were not destroyed
        assert len(ctx.store.recent_trade_pnls(n + 1)) >= n, (
            "the re-dating deleted the rows — this test proves nothing"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 2. THE GATE — positive and negative through the WIRED path (B3, B4)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestTheGateItself:

    def test_at_the_threshold_the_next_signal_is_rejected_consecutive_losses(self, wired_system):
        """B3 POSITIVE — the SPECIFIC status, no multi-way accept (rule G)."""
        ctx = wired_system
        n = _max_consec(ctx)
        _drive_losses(ctx, n)

        # ANTI-VACUITY: the streak must actually be at the threshold BEFORE we
        # attribute the rejection to it.
        assert _streak(ctx) == n, (
            f"streak is {_streak(ctx)}, not {n} — a rejection here would be for some "
            "other reason and the test would pass for the wrong one"
        )
        _assert_no_earlier_gate_can_mask(ctx, "positive case")

        status = _probe_signal_status(ctx, _SYMS[n], 300.0, ignore={"PASSED"})
        assert status == "REJECTED_CONSECUTIVE_LOSSES", (
            f"with {n} consecutive losses (max={n}) the signal terminated as {status!r} "
            f"rather than REJECTED_CONSECUTIVE_LOSSES — the gate did not bind"
        )

    def test_at_threshold_minus_one_the_gate_does_not_fire(self, wired_system):
        """B4 NEGATIVE — without this, a gate that rejects EVERYTHING is
        indistinguishable from one that works."""
        ctx = wired_system
        n = _max_consec(ctx)
        _drive_losses(ctx, n - 1)

        assert _streak(ctx) == n - 1, "setup did not reach threshold-minus-one"
        _assert_no_earlier_gate_can_mask(ctx, "negative case")

        status = _probe_signal_status(ctx, _SYMS[n], 300.0, ignore={"PASSED"})
        assert status != "REJECTED_CONSECUTIVE_LOSSES", (
            f"the gate fired at {n - 1} losses with max={n} — it binds one loss early"
        )

    def test_after_a_win_breaks_the_streak_trading_resumes(self, wired_system):
        """B5 — the operational half of the reset: not just that the counter drops,
        but that the system actually starts accepting signals again."""
        ctx = wired_system
        n = _max_consec(ctx)
        _drive_losses(ctx, n)
        assert _probe_signal_status(ctx, _SYMS[n], 300.0, ignore={"PASSED"}) == "REJECTED_CONSECUTIVE_LOSSES"

        _drive_win(ctx, _SYMS[n + 1], n + 1)
        assert _streak(ctx) == 0, "anti-vacuity: the win did not break the streak"
        _assert_no_earlier_gate_can_mask(ctx, "post-win resume")

        status = _probe_signal_status(ctx, _SYMS[n + 2], 300.0, ignore={"PASSED"})
        assert status != "REJECTED_CONSECUTIVE_LOSSES", (
            f"after a winning close broke the streak the next signal was STILL "
            f"rejected as {status!r} — the halt does not lift"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 3. RESTART (B1c) — is the restart a bypass?
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestSurvivesRestart:
    """The same question batch 5 asked of the daily loss and the kill state. If the
    streak resets on restart, a restart hands the system a fresh run of losses."""

    def test_the_streak_is_identical_after_a_restart(self, wired_system):
        ctx = wired_system
        n = _max_consec(ctx)
        _drive_losses(ctx, n)
        before = _streak(ctx)
        assert before == n, "anti-vacuity: no streak to preserve"

        _restart(ctx)

        assert _streak(ctx) == before, (
            f"THE STREAK DID NOT SURVIVE THE RESTART: {before} -> {_streak(ctx)}. "
            "A restart would hand the system a fresh run of losses."
        )

    def test_the_gate_still_fires_on_the_restored_streak(self, wired_system):
        """The value surviving is necessary but not sufficient — the GATE must still
        bind on it (batch 5's distinction)."""
        ctx = wired_system
        n = _max_consec(ctx)
        _drive_losses(ctx, n)
        _restart(ctx)

        assert _streak(ctx) == n
        _assert_no_earlier_gate_can_mask(ctx, "after restart")

        status = _probe_signal_status(ctx, _SYMS[n], 300.0, ignore={"PASSED"})
        assert status == "REJECTED_CONSECUTIVE_LOSSES", (
            f"after a restart the streak is {_streak(ctx)} (max={n}) but the signal "
            f"terminated as {status!r} — the gate does not enforce on restored state"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 4. PARITY (B7) + REACHABILITY (B9) — structural
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestParityAndReachability:

    def test_the_gate_has_no_paper_or_live_branch(self, wired_system):
        """Rule #5. The streak reads the trades table and the threshold is one
        config value; neither is mode-dependent. Asserted on the source so it fails
        the moment a mode branch is introduced."""
        import inspect
        from capital import risk_engine as re_mod

        import re as _re

        src = inspect.getsource(re_mod.RiskEngine._run_checks)
        start = src.index("CONSECUTIVE_LOSSES")
        window = src[start:start + 600].lower()
        # WORD-BOUNDARY, not substring: the block says "no deLIVEry variant", so a
        # naive `"live" in window` fires on an unrelated word. That false positive is
        # this batch's own instance of the guard-too-broad trap (batch 4's tier
        # multiplier, the report fix's guard matching its own comment).
        for token in ("paper", "live", "paper_mode", "is_live"):
            assert not _re.search(rf"\b{token}\b", window), (
                f"the CONSECUTIVE_LOSSES block mentions {token!r} as a WORD — the gate "
                "may have acquired a mode branch and paper/live parity is no longer "
                "structural"
            )

    def test_the_streak_has_exactly_one_data_source(self, wired_system):
        """If a second source appears, the gate and any reporting of it can drift —
        the same shape as the M-C1 shared-helper argument."""
        import inspect
        from capital import risk_engine as re_mod

        src = inspect.getsource(re_mod.RiskEngine)
        assert src.count("recent_trade_pnls") == 1, (
            f"recent_trade_pnls is called {src.count('recent_trade_pnls')} times in "
            "RiskEngine — the streak must have exactly one data source"
        )
        assert src.count("_count_trailing_losses(") == 2, (
            "expected exactly one definition and one call of _count_trailing_losses"
        )

    def test_production_config_leaves_the_gate_reachable(self, wired_system):
        """B9 — the algebra against PRODUCTION values, not the fixture's.

        The gate needs max_consec closes plus one more entry attempt. DAILY_TRADES
        sits above it, so if max_daily_trades <= max_consecutive_losses the gate
        would be squeezed out the way batch 4's qty_by_risk was.
        """
        from pathlib import Path

        from core.config_loader import load_all

        cfg = load_all(Path(__file__).resolve().parents[2] / "config")
        max_consec = cfg.system.risk.max_consecutive_losses
        max_daily = cfg.system.risk.max_daily_trades

        assert max_daily > max_consec, (
            f"PRODUCTION max_daily_trades={max_daily} <= max_consecutive_losses="
            f"{max_consec}: DAILY_TRADES would always fire first and the "
            "consecutive-losses gate would be UNREACHABLE"
        )
        # Recorded for the report: 10 vs 4 today, so there is room for the 5th entry
        # attempt that the gate needs in order to bind.
        assert max_consec >= 1
