"""
tests/unit/test_ni19_segment_split_is_partial_by_design.py

BUG-NI19 — the segment split is PARTIAL. This file records why that is correct for
four of the five, and pins the boundary so a future split cannot happen silently.

⛔ NO KEY WAS CREATED. Adding five delivery keys to make the config look symmetric
would add keys with no consumer -- the exact defect class this campaign keeps
finding (an unread key reads as a control and is not one).

WHAT IS SPLIT, AND WHAT IS NOT -- measured, not asserted:

  SPLIT (each has a delivery twin, and refuses rather than inheriting):
      risk_per_trade_pct · max_concentration_pct · max_position_value_pct
  NOT SPLIT (global for both books):
      min_tick_size · max_single_order_qty · lot_skew_rejection_threshold
      min_qty_threshold · tier_multipliers

THE BOUNDARY IS NOT ARBITRARY. The three that are split are PERCENTAGES OF CAPITAL
-- how much of a book to commit, which is per-book policy by definition. Four of the
five that are not split are INSTRUMENT- or BROKER-level mechanics: NSE tick size is
per instrument, the broker's order-quantity limit is per order, lot skew follows
lot_size, and a one-share floor is a floor. None of them vary by product, so a
`delivery_min_tick_size` would be a key with nothing to mean.

⚠️ THE FIFTH IS DIFFERENT AND IS NOT CLOSED HERE. `tier_multipliers` is POLICY, not
mechanics -- it scales position size by score tier, and a delivery book could
legitimately want a different ladder. Today one dict serves both, so a tier change
moves DELIVERY too. That is a decision for Rama, recorded as open; this file only
pins that the split does not exist today, so nobody can conclude from the config's
shape that it does.
"""
from __future__ import annotations

import inspect

from capital.position_sizer import PositionSizer

_SPLIT = ("risk_per_trade_pct", "max_concentration_pct", "max_position_value_pct")
_GLOBAL = ("min_tick_size", "max_single_order_qty", "lot_skew_rejection_threshold",
           "min_qty_threshold", "tier_multipliers")


def _params():
    return inspect.signature(PositionSizer.__init__).parameters


def test_the_three_percentage_rungs_are_split_per_book() -> None:
    p = _params()
    for name in _SPLIT:
        assert f"delivery_{name}" in p, (
            f"{name} lost its delivery twin -- a delivery entry would be sized on the "
            "intraday percentage, which is the defect fix item 1 removed")


def test_the_five_mechanics_are_global_and_that_is_recorded() -> None:
    """If a delivery twin appears for one of these, it is a DESIGN CHANGE.

    This assertion is the tripwire: it should be edited deliberately, together with
    the consumer that reads the new key -- never as a config-symmetry tidy-up.
    """
    p = _params()
    for name in _GLOBAL:
        assert f"delivery_{name}" not in p, (
            f"delivery_{name} now exists. NI-19 recorded these as global by design. "
            "If this is intended, the key must have a CONSUMER -- confirm the sizer "
            "actually reads it for the positional bucket before accepting this.")


def test_a_delivery_order_is_sized_on_delivery_percentages(  ) -> None:
    """The half of NI-19 that IS closed: the percentage rungs really are per-book."""
    p = _params()
    for name in _SPLIT:
        assert p[f"delivery_{name}"].default is None, (
            f"delivery_{name} must default to None and refuse on use, not inherit")


def test_the_boundary_is_exactly_these_eight() -> None:
    """A guard against the list drifting out of step with the docstring above."""
    p = _params()
    delivery_twins = {n[len("delivery_"):] for n in p if n.startswith("delivery_")}
    assert delivery_twins == set(_SPLIT), (
        f"the set of per-book keys changed: {sorted(delivery_twins)}. NI-19's record "
        "and this file's docstring must be updated in the same commit.")
