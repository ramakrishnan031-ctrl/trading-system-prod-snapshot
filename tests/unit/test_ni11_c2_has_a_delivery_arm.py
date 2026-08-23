"""
tests/unit/test_ni11_c2_has_a_delivery_arm.py

BUG-NI11 — C2 checked only the intraday ladder.

C2 exists to keep the catastrophic-loss backstop (`max_position_value_pct`) LOOSER
than what routine sizing can produce (`max_concentration_pct` x the largest
multiplier). NI-2 corrected the intraday arm to include the multiplier term. But
fix item 1 gave DELIVERY its own pair with identical semantics, and C2 did not look
at them at all: a delivery pair could be set to any inverted combination and the
audit stayed silent.

Adding the delivery arm completes an existing check. It is not new policy, and no
threshold moved.

The multiplier ceiling is deliberately the SAME term for both books: `tier_multipliers`
is global (NI-19), so a delivery order is sized on delivery percentages but scaled by
the intraday multiplier ladder. A delivery-specific ceiling would invent a split the
code does not have.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.config_auditor import audit

_CFG = Path("config")


@pytest.fixture(scope="module")
def base_system():
    from core.config_loader import load_all
    return load_all(_CFG).system


def _mut(base, **dotted):
    s = base.model_copy(deep=True)
    for key, value in dotted.items():
        obj = s
        parts = key.split("__")
        for p in parts[:-1]:
            obj = getattr(obj, p)
        setattr(obj, parts[-1], value)
    return s


def _codes(res):
    return {f.code for f in res.findings}


def test_the_shipped_delivery_pair_is_silent(base_system):
    """Premise: 0.10 conc / 0.40 posv is a correct ladder and must not warn."""
    r = audit(base_system, groups="C")
    assert not any("C2d" in c for c in _codes(r)), (
        "the shipped delivery pair is correctly ordered; C2d must stay silent")


def test_an_INVERTED_delivery_pair_now_WARNS(base_system):
    """THE DEFECT: this configuration was previously accepted in silence."""
    s = _mut(base_system, position_sizing__delivery_max_position_value_pct=0.08)
    r = audit(s, groups="C")
    hits = [f for f in r.findings if f.code == "C2d_delivery_position_cap_not_looser"]
    assert hits, (
        "delivery posv 8% is BELOW the delivery routine ceiling (conc 10% x mult); "
        "the delivery backstop would bind on ordinary trades and C2 said nothing")
    m = hits[0].message
    assert "delivery_max_position_value_pct" in m
    assert "\u00d7" in m, "the message must render the multiply sign, not an escape"
    assert hits[0].metrics["delivery_max_concentration_pct"] == pytest.approx(0.10)


def test_the_delivery_arm_uses_the_multiplier_term_like_NI2_did(base_system):
    """A pair that passes a NAIVE posv > conc test but fails the effective one."""
    s = _mut(base_system,
             position_sizing__delivery_max_concentration_pct=0.25,
             position_sizing__delivery_max_position_value_pct=0.40,
             position_sizing__max_multiplier=2.0)
    r = audit(s, groups="C")
    assert 0.40 > 0.25, "premise: the naive comparison passes"
    assert any(f.code == "C2d_delivery_position_cap_not_looser" for f in r.findings), (
        "0.25 x 2.0 = 0.50 > 0.40 -- the delivery backstop binds on routine sizing, "
        "which is exactly what NI-2 taught the intraday arm to catch")


def test_the_intraday_arm_is_untouched(base_system):
    """NI-11 must ADD an arm, not disturb the one NI-2 fixed."""
    s = _mut(base_system,
             position_sizing__max_concentration_pct=0.25,
             position_sizing__max_position_value_pct=0.40,
             position_sizing__max_multiplier=2.0)
    r = audit(s, groups="C")
    assert any(f.code == "C2_position_cap_not_looser" for f in r.findings)
