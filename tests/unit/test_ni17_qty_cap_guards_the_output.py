"""
tests/unit/test_ni17_qty_cap_guards_the_output.py

BUG-NI17 — `max_single_order_qty` guarded an INPUT, not the order.

FIX-041's Guard 2 tests `qty_by_risk` at position_sizer.py, which is ONE rung and
sits BEFORE `raw_qty = min(risk, capital, concentration)`. Two consequences:

  * a large `qty_by_capital` or `qty_by_concentration` could never trip it; and
  * the tier x perf multiplier is applied AFTER the min and can reach 2x the
    tightest rung, so the ORDERED quantity can exceed the cap while the guarded
    input sits comfortably under it.

A cap that guards an input does not cap the order.

LATENT AT TODAY'S CAPITAL — MEASURED, NOT ASSUMED. `min_tick_size = 0.05` is
enforced first, so qty_by_risk <= R * risk_pct / 0.05 = 2,117 at R = Rs10,587, and
the 2x ceiling caps the output at 4,235 — both far under 10,000. The output guard
first becomes reachable at roughly Rs25,000 of capital (~2.4x today). A 432-point
grid across capital / price / SL / tier / perf_weight / intent shows ZERO
behaviour change at production settings.

These tests therefore drive the cap DOWN rather than capital up: the same
arithmetic gap, made reachable so it can be asserted.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from capital.fund_manager import CapitalSnapshot          # noqa: E402
from capital.position_sizer import PositionSizer          # noqa: E402
from core.time_authority import now_ist                   # noqa: E402

_LEV = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}
_R = 20_000.0


class _FM:
    def get_snapshot(self):
        return CapitalSnapshot(
            total=_R, intraday_avail=_R * 0.7, intraday_reserved=0.0,
            intraday_used=0.0, positional_avail=_R * 0.3, positional_reserved=0.0,
            positional_used=0.0, daily_realized_pnl=0.0,
            ts=now_ist().replace(tzinfo=None).isoformat())


def _sizer(cap: int):
    # concentration and position-value are opened up so the RISK rung binds and the
    # multiplier is what carries the quantity past the cap -- that is the NI-17 case.
    return PositionSizer(
        fund_manager=_FM(), leverage_map=_LEV,
        risk_per_trade_pct=0.01, max_concentration_pct=1.0,
        max_position_value_pct=1.0, max_single_order_qty=cap,
        logger=logging.getLogger("t_ni17"))


def _calc(cap: int, perf_weight: float):
    # entry 100 / sl 98 -> sl_distance 2 -> qty_by_risk = (20000*0.01)/2 = 100 == cap.
    # 100 is NOT > 100, so the INPUT guard cannot fire. perf_weight 2.0 doubles it.
    return _sizer(cap).calculate("SYM", "BUY", 100.0, 98.0, "INTRADAY",
                                 score_tier="HIGH", perf_weight=perf_weight)


def test_the_premise_the_input_guard_cannot_see_this() -> None:
    """Without the multiplier the order sits exactly ON the cap and is allowed."""
    r = _calc(cap=100, perf_weight=1.0)
    assert r.success, r.reason
    assert r.breakdown["qty_by_risk"] == 100, "premise: the guarded INPUT equals the cap"
    assert r.qty == 100


def test_the_multiplier_carries_the_ORDER_past_a_cap_the_input_never_trips() -> None:
    """THE DEFECT. qty_by_risk (100) never exceeds the cap (100), yet the ORDER would."""
    r = _calc(cap=100, perf_weight=2.0)
    assert r.breakdown["qty_by_risk"] == 100, "the guarded input is still under the cap"
    assert not r.success, (
        "the ORDER reached 200 against a cap of 100 and was allowed through — "
        "the cap is guarding an input, not the order")
    assert r.constraint == "QTY_EXPLOSION_GUARD"
    assert "final_qty=200" in r.reason and "max_single_order_qty=100" in r.reason


def test_production_settings_are_unaffected() -> None:
    """The guard is LATENT at the shipped cap -- this must not start rejecting."""
    r = _calc(cap=10_000, perf_weight=2.0)
    assert r.success, r.reason
    assert r.qty == 200


def test_the_input_guard_still_works_and_was_not_moved() -> None:
    """NI-17 ADDED a check; it must not have REMOVED the fail-fast one."""
    r = _calc(cap=50, perf_weight=1.0)
    assert not r.success
    assert r.constraint == "QTY_EXPLOSION_GUARD"
    assert "qty_by_risk=100" in r.reason, "the INPUT guard must still be the one that fired"
