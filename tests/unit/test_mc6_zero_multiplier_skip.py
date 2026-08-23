"""
tests/unit/test_mc6_zero_multiplier_skip.py — M-C6 (16-Jul-2026).

M-C6: `PositionSizer.calculate` computed
    effective_mult = tier_mult * max(0.0, perf_weight)
    tiered_qty     = floor(raw_qty * effective_mult)
    tiered_qty     = max(1, min(tiered_qty, raw_qty * 2))     # FIX-133 Item 21

A ZERO multiplier means the sizing model said to trade NOTHING. `max(1, 0)` turned
that into ONE LOT — real capital and real risk on exactly the signal it had just
declined. A NEGATIVE multiplier (a config typo: PositionSizingTierConfig types
HIGH/MEDIUM/LOW as bare floats with no ge=0 bound) did the same.

The floor is not wrong, it was just doing two jobs. Its real one — keeping a
small-but-POSITIVE multiplier from rounding to zero and silently killing a wanted
trade — is preserved below. Only the zero/negative case is split off.

NOT reachable in production today: performance_allocator clamps min_weight=0.5
(PA3/PA8) and signal_processor defaults an unknown strategy to perf_weight=1.0, so
effective_mult >= 0.25. This fix is the hard PREREQUISITE for ever lowering
min_weight — which is precisely when it would start to matter.

Run: python -m pytest tests/unit/test_mc6_zero_multiplier_skip.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from capital.fund_manager import CapitalSnapshot
from capital.position_sizer import PositionSizer


def _make_sizer(total=200_000.0, risk_pct=0.01, max_conc=0.20, tier_multipliers=None):
    fm = MagicMock()
    fm.get_snapshot.return_value = CapitalSnapshot(
        total=total, intraday_avail=total, intraday_reserved=0.0, intraday_used=0.0,
        positional_avail=total, positional_reserved=0.0, positional_used=0.0,
        daily_realized_pnl=0.0, ts="2026-07-16T09:20:00",
    )
    kwargs = dict(
        fund_manager=fm,
        leverage_map={"INTRADAY": 5.0, "POSITIONAL": 1.0},
        risk_per_trade_pct=risk_pct,
        max_concentration_pct=max_conc,
        max_position_value_pct=0.40,  # NI-5: was a silent default
    )
    if tier_multipliers is not None:
        kwargs["tier_multipliers"] = tier_multipliers
    return PositionSizer(**kwargs)


def _calc(sizer, **kw):
    return sizer.calculate("TEST", "BUY", 1000.0, 985.0, "INTRADAY", **kw)


# ─────────────────────────────────────────────────────────────────────────────
# The fix: zero / negative -> SKIP
# ─────────────────────────────────────────────────────────────────────────────

def test_zero_multiplier_skips_instead_of_flooring_to_one_lot():
    """RED ON OLD: max(1, floor(raw_qty * 0)) == 1 -> a position the sizing model
    explicitly sized to nothing."""
    result = _calc(_make_sizer(), perf_weight=0.0)

    assert result.success is False, "a zero multiplier must not produce a tradeable size"
    assert result.qty == 0
    assert result.constraint == "ZERO_MULTIPLIER"
    assert result.breakdown["tiered_qty"] == 0
    assert "ZERO_MULTIPLIER" in result.reason


def test_negative_multiplier_skips_too():
    """RED ON OLD: a negative effective_mult also floored to 1 lot. There is no
    meaning to a negative size — treat it exactly as zero.

    Reachable via a config typo: PositionSizingTierConfig types HIGH/MEDIUM/LOW as
    bare floats with NO ge=0 bound, so tier_multipliers.HIGH = -1.0 loads cleanly.
    """
    sizer = _make_sizer(tier_multipliers={"HIGH": -1.0, "MEDIUM": 0.70, "LOW": 0.50})

    result = _calc(sizer, score_tier="HIGH", perf_weight=1.0)

    assert result.breakdown["tier_mult"] == -1.0, "the negative tier must reach sizing"
    assert result.success is False
    assert result.qty == 0
    assert result.constraint == "ZERO_MULTIPLIER"


def test_negative_tier_times_zero_perf_is_negative_zero_and_still_skips():
    """-1.0 * 0.0 == -0.0 in IEEE754. `<= 0` catches it; a naive `== 0` would too,
    but a `< 0`-only guard would not. Pinning the edge so a future 'tidy' of the
    condition cannot silently reopen it."""
    sizer = _make_sizer(tier_multipliers={"HIGH": -1.0, "MEDIUM": 0.70, "LOW": 0.50})

    result = _calc(sizer, score_tier="HIGH", perf_weight=0.0)

    assert result.success is False
    assert result.qty == 0
    assert result.constraint == "ZERO_MULTIPLIER"


# ─────────────────────────────────────────────────────────────────────────────
# FIX-133's floor is PRESERVED for every positive multiplier
# ─────────────────────────────────────────────────────────────────────────────

def test_tiny_positive_multiplier_still_gets_one_lot():
    """The floor's REAL job, untouched: a small-but-positive multiplier whose
    product rounds to zero must still trade one lot, not be skipped. This is the
    case M-C6 must not break — if this goes red, the split ate FIX-133."""
    result = _calc(_make_sizer(), perf_weight=0.0001)

    assert result.breakdown["tiered_qty"] == 1, (
        "a positive multiplier that rounds to 0 must be floored to 1 (FIX-133), "
        "NOT skipped — only an exact zero/negative means 'trade nothing'"
    )
    assert result.constraint != "ZERO_MULTIPLIER"


def test_normal_positive_multiplier_is_unchanged():
    """The overwhelmingly common path must be byte-identical to pre-M-C6."""
    result = _calc(_make_sizer(), perf_weight=1.0)

    assert result.success is True
    assert result.qty > 0
    assert result.constraint != "ZERO_MULTIPLIER"


def test_the_2x_cap_and_scaling_are_unchanged():
    """M-C6 touched only the <=0 branch: the 2x cap and monotonic scaling still hold."""
    sizer = _make_sizer()
    base = _calc(sizer, perf_weight=1.0)
    reduced = _calc(sizer, perf_weight=0.5)
    extreme = _calc(sizer, perf_weight=3.0)

    raw_qty = base.breakdown["raw_qty"]
    assert reduced.qty <= base.qty
    assert extreme.breakdown["tiered_qty"] <= raw_qty * 2
    assert extreme.qty >= base.qty


def test_production_is_unaffected_because_the_allocator_clamps_min_weight():
    """Behaviour-neutral proof: at the allocator's floor (min_weight=0.5) and the
    lowest tier multiplier (0.50), effective_mult = 0.25 > 0 -> the skip branch never
    fires today. M-C6 only bites once min_weight is lowered."""
    result = _calc(_make_sizer(), score_tier="LOW", perf_weight=0.5)

    assert result.constraint != "ZERO_MULTIPLIER"
    assert result.breakdown["tiered_qty"] >= 1
