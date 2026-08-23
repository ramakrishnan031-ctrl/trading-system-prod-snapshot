"""
tests/unit/test_ni18_constraint_reports_multiplier.py

BUG-NI18 — the `constraint` / `binding_constraint` field named a rung the delivered
quantity EXCEEDED.

`constraint` is decided from `raw_qty = min(risk, capital, concentration)`. The tier ×
perf multiplier is applied AFTER that, and `max(1, min(tiered_qty, raw_qty * 2))` lets
the result reach TWICE the tightest rung. The field was never updated, so it reported
e.g. `concentration` for a quantity double the concentration limit — an attribution
field naming a limit the result exceeds is a trap, not a diagnostic. Same family as
NI-1 (a warning that did not warn).

⛔ The QUANTITY is deliberately NOT changed by NI-18. The 2x ceiling is NI-16's, and
NI-16 is blocked on F2. This item fixes only what is REPORTED.

The OFF (flat) branch already re-pointed `constraint` at FLAT when flat was the tighter
ceiling; NI-18 is the same move for the ON branch.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from capital.fund_manager import CapitalSnapshot          # noqa: E402
from capital.position_sizer import PositionSizer          # noqa: E402
from core.time_authority import now_ist                   # noqa: E402

_LEV = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}


class _FM:
    def get_snapshot(self):
        return CapitalSnapshot(
            total=100_000.0, intraday_avail=70_000.0, intraday_reserved=0.0,
            intraday_used=0.0, positional_avail=30_000.0, positional_reserved=0.0,
            positional_used=0.0, daily_realized_pnl=0.0,
            ts=now_ist().replace(tzinfo=None).isoformat())


def _sizer():
    return PositionSizer(
        fund_manager=_FM(), leverage_map=_LEV,
        risk_per_trade_pct=0.01, max_concentration_pct=0.10,
        max_position_value_pct=0.40, logger=logging.getLogger("t_ni18"))


def _calc(perf_weight, tier="HIGH"):
    # entry 100 / sl 98 -> sl_distance 2 -> qty_by_risk 500; conc 10% of 100k / 100 = 100.
    # CONCENTRATION is the tightest rung, so it is what the field used to name.
    return _sizer().calculate("SYM", "BUY", 100.0, 98.0, "INTRADAY",
                              score_tier=tier, perf_weight=perf_weight)


def test_the_rung_is_named_when_it_actually_binds() -> None:
    """perf_weight = 1.0, HIGH tier -> multiplier is exactly 1 -> the rung really binds."""
    r = _calc(1.0)
    assert r.success
    assert r.qty == r.breakdown["raw_qty"]
    assert r.breakdown["binding_constraint"] == "concentration"
    assert "rung_before_multiplier" not in r.breakdown


@pytest.mark.parametrize("pw", [1.5, 2.0])
def test_the_rung_is_NOT_named_when_the_multiplier_lifts_above_it(pw: float) -> None:
    """THE DEFECT. Above the rung, the field must not claim the rung bound the result."""
    r = _calc(pw)
    assert r.success
    assert r.qty > r.breakdown["raw_qty"], "premise: this input must exceed the rung"
    assert r.breakdown["binding_constraint"] == "multiplier", (
        "the field named a rung the quantity EXCEEDS — that is the NI-18 defect")
    assert r.breakdown["rung_before_multiplier"] == "concentration", (
        "the rung must stay recoverable; NI-18 must not destroy information")


@pytest.mark.parametrize("tier", ["MEDIUM", "LOW"])
def test_a_shrinking_multiplier_still_names_the_rung(tier: str) -> None:
    """Below the rung the field is conservative, not a trap — deliberately unchanged."""
    r = _calc(1.0, tier=tier)
    assert r.success
    assert r.qty < r.breakdown["raw_qty"]
    assert r.breakdown["binding_constraint"] == "concentration"


def test_ni18_changed_the_label_and_NOT_the_quantity() -> None:
    """The property that makes NI-18 safe: qty is a pure function of the same inputs.

    Asserted as arithmetic rather than a fixed number, so it keeps holding if the
    config moves: the 2x ceiling still governs, exactly as before.
    """
    for pw in (0.5, 1.0, 1.5, 2.0, 4.0):
        r = _calc(pw)
        raw = r.breakdown["raw_qty"]
        tier_mult = r.breakdown["tier_weight_applied"]
        expected = min(int((raw * tier_mult * pw)), raw * 2)
        expected = max(1, expected) if tier_mult * pw > 0 else expected
        assert r.qty == expected, (
            f"perf_weight={pw}: NI-18 must not move the quantity "
            f"(got {r.qty}, expected {expected})")
