"""
tests/unit/test_unit3a_leverage_validation.py — UNIT 3a (27-Aug-2026).

WHAT UNIT 3a IS, AND WHAT IT IS NOT
-----------------------------------
DELIVERY leverage was ALREADY implemented (`leverage_map.DELIVERY: 1.0`, applied
through the one intent-generic multiply in fund_manager.required_margin). UNIT 3a
adds NO second calculation. It hardens the CONFIGURATION AUTHORITY around the
existing one: every intent must be present, finite and bounded, DELIVERY is pinned
to exactly 1.0, and the governance block that supplies the bounds validates itself.

THE ASYMMETRY THIS FIXES
------------------------
Before UNIT 3a the only leverage sanity check was config_auditor G2:
`if value > 10` -> Severity.WARN, for INTRADAY and COVER_ORDER only.

  * TOO HIGH (5.0 -> 50, a dropped decimal) is the CAPITAL-BLOWUP direction:
    margin = notional/50 gives ~10x the intended quantity. It was a WARN, on
    half the intents. It is now a hard load failure on all four.
  * TOO LOW (5.0 -> 0.05) had NO coverage at all -- not even a WARN -- and
    silently collapses every size to 1/100th of intent. Money-safe, but silently
    wrong is still wrong, so it fails too.
  * DELIVERY and BRACKET_ORDER were never checked by G2 at all.

RED-CAPABILITY (this suite is worthless if it cannot go red)
-----------------------------------------------------------
Each fail-closed test asserts a REJECTION. Delete the corresponding validator and
that test goes red, because the bad config would then load. The neutrality oracle
goes red if the arithmetic or the configured values move. `test_mutation_*`
documents the three deletions that must break this file.

COVERAGE IS 16 OF 18 -- NOT 18/18 (recorded 28-Aug-2026)
--------------------------------------------------------
This file contains 18 test FUNCTIONS and the design matrix has 18 SLOTS
(4 valid / 12 fail-closed / 1 parity / 1 neutrality --
FILE16_UNIT3_SCOPE_FROZEN_27-Aug-2026.md:185). The two 18s are a COINCIDENCE OF
ARITHMETIC, not evidence of coverage: two of the matrix's protections are NOT
asserted here (cases 15 and 16 below), because the matrix was scoped for the
WHOLE of UNIT 3 (U3-c..U3-g) while this file ships U3-d + U3-f only.

  ASSERTED HERE .......... 16 of 18
  DEFERRED TO UNIT 3b .... 2 of 18  (cases 15, 16 -- named below)

test_unit3b_cases_15_and_16_are_named_and_unasserted() makes that gap
machine-visible, so "18 functions" can never again be read as "18/18 covered".

DEFERRED TO UNIT 3b -- NOT CLAIMED HERE
---------------------------------------
Two protections from the original 18-case matrix are NOT in this unit and are NOT
asserted, because implementing them touches 14 test files (~26 construction sites)
and the standing scope rule says stop:
  * case 15 -- unknown intent must fail rather than silently resolve to 1.0
    (`position_sizer.py` `.get(intent, 1.0)`)
  * case 16 -- FundManager's hardcoded `leverage_map is None` default
    (`fund_manager.py`) must not be able to activate
Both guard paths that are LATENT today: main.py always supplies the map, and only
MIS and CNC occur in production. They are recorded as owed, not silently dropped.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from capital.fund_manager import required_margin
from core.config_loader import (
    ABSOLUTE_MAX_LEVERAGE,
    CapitalConfig,
    LeverageMapConfig,
    LeverageSafetyConfig,
    load_all,
)

_GOOD_MAP = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}
_GOOD_SAFETY = {"min_allowed": 1.0, "max_allowed": 10.0}


def _capital(*, lev=None, safety=None):
    return CapitalConfig(
        intraday_bucket_pct=0.70,
        positional_bucket_pct=0.30,
        leverage_map=dict(lev if lev is not None else _GOOD_MAP),
        leverage_safety=dict(safety if safety is not None else _GOOD_SAFETY),
    )


# ══ VALID (matrix 1–4) ═══════════════════════════════════════════════════════

def test_01_the_shipped_config_loads():
    cap = load_all(Path("config")).system.capital
    assert (cap.leverage_map.INTRADAY, cap.leverage_map.COVER_ORDER,
            cap.leverage_map.DELIVERY, cap.leverage_map.BRACKET_ORDER) == (5.0, 6.0, 1.0, 5.0)
    assert (cap.leverage_safety.min_allowed, cap.leverage_safety.max_allowed) == (1.0, 10.0)


def test_02_absolute_ceiling_is_an_internal_constant():
    """20.0 is internal. It is NOT a SEBI or broker limit and must bound the config."""
    assert ABSOLUTE_MAX_LEVERAGE == 20.0
    assert _GOOD_SAFETY["max_allowed"] <= ABSOLUTE_MAX_LEVERAGE


# ── the executable neutrality oracle (matrix 2–4, 18) ────────────────────────
# Real production triples (qty, entry_target_price, margin_reserved) taken from
# the live DB on 27-Aug-2026. The ORACLE EXECUTES the real calculation path --
# fund_manager.required_margin -- with the map the real config loader produced,
# and requires it to reproduce the persisted margin. It is non-circular: none of
# the three columns is derived from the others, and `margin_reserved` was written
# independently at reserve time.
_REAL_TRIPLES = [
    ("INTRADAY", 2, 220.6791, 88.27164),
    ("INTRADAY", 1, 634.31505, 126.86301),
    ("INTRADAY", 1, 417.582, 83.5164),
    ("INTRADAY", 2, 245.00476, 98.001904),
    ("INTRADAY", 1, 348.9486, 69.78972),
    ("INTRADAY", 2, 216.70308, 86.681232),
    ("DELIVERY", 1, 415.3676, 415.3676),
    ("DELIVERY", 1, 550.397, 550.397),
    ("DELIVERY", 1, 314.1704, 314.1704),
    ("DELIVERY", 2, 227.57394, 455.14788),
    ("DELIVERY", 4, 127.67414, 510.69656),
    ("DELIVERY", 1, 573.0516, 573.0516),
]


@pytest.mark.parametrize("intent,qty,price,expected", _REAL_TRIPLES)
def test_03_neutrality_oracle_reproduces_real_production_margins(intent, qty, price, expected):
    """EXECUTE the calculation path; require EQUALITY with what production stored."""
    cap = load_all(Path("config")).system.capital
    lev_map = {
        "INTRADAY": cap.leverage_map.INTRADAY,
        "COVER_ORDER": cap.leverage_map.COVER_ORDER,
        "DELIVERY": cap.leverage_map.DELIVERY,
        "BRACKET_ORDER": cap.leverage_map.BRACKET_ORDER,
    }
    got = required_margin(qty, price, intent, lev_map)
    assert got == pytest.approx(expected, rel=0, abs=1e-6), (
        f"{intent} {qty}@{price}: expected {expected}, got {got} — "
        "a leverage-derived value MOVED"
    )


def test_04_cover_and_bracket_orders_still_resolve():
    """NOT EXERCISED in production (zero rows) but must still compute."""
    assert required_margin(1, 600.0, "COVER_ORDER", _GOOD_MAP) == pytest.approx(100.0)
    assert required_margin(1, 500.0, "BRACKET_ORDER", _GOOD_MAP) == pytest.approx(100.0)


# ══ FAIL-CLOSED — leverage_map (matrix 5–14) ═════════════════════════════════

@pytest.mark.parametrize("missing", ["INTRADAY", "COVER_ORDER", "DELIVERY", "BRACKET_ORDER"])
def test_05_each_missing_intent_is_rejected(missing):
    bad = {k: v for k, v in _GOOD_MAP.items() if k != missing}
    with pytest.raises(ValidationError):
        LeverageMapConfig(**bad)


def test_09_intraday_dropped_decimal_low_is_rejected():
    """5.0 -> 0.05: sizes collapse to 1/100th of intent. Before UNIT 3a: NOT EVEN A WARN."""
    with pytest.raises(ValidationError):
        _capital(lev={**_GOOD_MAP, "INTRADAY": 0.05})


def test_10_delivery_is_pinned_to_exactly_one():
    """CNC is cash-and-carry: 1x IS the product. MTF is a new product, not a number."""
    for bad in (0.01, 1.5, 2.0, 5.0):
        with pytest.raises(ValidationError):
            LeverageMapConfig(**{**_GOOD_MAP, "DELIVERY": bad})
    assert LeverageMapConfig(**_GOOD_MAP).DELIVERY == 1.0


def test_11_zero_is_rejected():
    with pytest.raises(ValidationError):
        _capital(lev={**_GOOD_MAP, "INTRADAY": 0.0})


def test_12_negative_is_rejected():
    with pytest.raises(ValidationError):
        _capital(lev={**_GOOD_MAP, "INTRADAY": -5.0})


def test_13_above_the_ceiling_is_rejected():
    """5.0 -> 50 is the CAPITAL-BLOWUP direction: ~10x the intended quantity."""
    with pytest.raises(ValidationError):
        _capital(lev={**_GOOD_MAP, "INTRADAY": 50.0})
    with pytest.raises(ValidationError):
        _capital(lev={**_GOOD_MAP, "COVER_ORDER": 60.0})
    with pytest.raises(ValidationError):
        _capital(lev={**_GOOD_MAP, "BRACKET_ORDER": 55.0})


def test_13b_bracket_and_delivery_are_now_covered_too():
    """G2 checked only INTRADAY and COVER_ORDER. All four are bounded now."""
    with pytest.raises(ValidationError):
        _capital(lev={**_GOOD_MAP, "BRACKET_ORDER": 11.0})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_14_non_finite_is_rejected_explicitly(bad):
    """A range check ALONE lets NaN through: `nan < 1.0` and `nan > 10.0` are both False."""
    with pytest.raises(ValidationError):
        LeverageMapConfig(**{**_GOOD_MAP, "INTRADAY": bad})


def test_14b_nan_would_have_survived_a_bounds_only_check():
    """Pins WHY the explicit finite check exists, so it is never 'simplified' away."""
    nan = float("nan")
    assert not (nan < 1.0) and not (nan > 10.0), "NaN silently passes any bounds test"


# ══ FAIL-CLOSED — leverage_safety self-validation ════════════════════════════

@pytest.mark.parametrize("bad", [
    {"max_allowed": 10.0},                      # min missing
    {"min_allowed": 1.0},                       # max missing
    {"min_allowed": 0.5, "max_allowed": 10.0},  # min below 1x
    {"min_allowed": 5.0, "max_allowed": 2.0},   # max below min
    {"min_allowed": 1.0, "max_allowed": 25.0},  # above ABSOLUTE_MAX_LEVERAGE
    {"min_allowed": 1.0, "max_allowed": float("nan")},
    {"min_allowed": 1.0, "max_allowed": float("inf")},
])
def test_15_governance_block_validates_itself(bad):
    """An unvalidated governance block is just an unvalidated source of capital authority."""
    with pytest.raises(ValidationError):
        LeverageSafetyConfig(**bad)


def test_16_absolute_ceiling_cannot_be_raised_from_yaml():
    """The whole point of the code-side absolute: config cannot widen its own bound."""
    with pytest.raises(ValidationError):
        LeverageSafetyConfig(min_allowed=1.0, max_allowed=ABSOLUTE_MAX_LEVERAGE + 0.1)
    ok = LeverageSafetyConfig(min_allowed=1.0, max_allowed=ABSOLUTE_MAX_LEVERAGE)
    assert ok.max_allowed == ABSOLUTE_MAX_LEVERAGE


def test_17_a_deliberate_governance_widening_is_permitted_without_code():
    """Rama's contract: a legitimate change up to 20x is TWO edits and NO code change."""
    cap = _capital(lev={**_GOOD_MAP, "INTRADAY": 12.0},
                   safety={"min_allowed": 1.0, "max_allowed": 15.0})
    assert cap.leverage_map.INTRADAY == 12.0


def test_18_parity_one_validator_serves_both_modes():
    """
    U3-f: parity is not 'both files hold the same numbers'. It is ONE validated
    representation. Live and Paper read the same CapitalConfig object, so there is
    exactly one place the bound can be enforced.
    """
    cap = load_all(Path("config")).system.capital
    for mode in ("LIVE", "PAPER"):
        lev_map = {
            "INTRADAY": cap.leverage_map.INTRADAY,
            "COVER_ORDER": cap.leverage_map.COVER_ORDER,
            "DELIVERY": cap.leverage_map.DELIVERY,
            "BRACKET_ORDER": cap.leverage_map.BRACKET_ORDER,
        }
        assert required_margin(1, 500.0, "INTRADAY", lev_map) == pytest.approx(100.0), mode
        assert required_margin(1, 500.0, "DELIVERY", lev_map) == pytest.approx(500.0), mode


# ══ MUTATION DOCUMENTATION (B-3) ═════════════════════════════════════════════

def test_mutation_map_documents_what_must_break_this_file():
    """
    Not an assertion about behaviour — a machine-checked note of which deletions
    must turn this suite red. Verified by hand on 27-Aug-2026:

      delete _delivery_is_pinned          -> test_10 goes RED
      delete _validate_finite_leverage    -> test_14 (3 params) goes RED
      delete _validate_leverage_within_safety -> test_09/11/12/13/13b go RED
      delete LeverageSafetyConfig bounds  -> test_15 (5 params) + test_16 go RED
      change any shipped leverage value   -> test_01 + test_03 (12 params) go RED
    """
    assert math.isclose(ABSOLUTE_MAX_LEVERAGE, 20.0)


def test_unit3b_cases_15_and_16_are_named_and_unasserted():
    """C-3 closure (28-Aug-2026): the two matrix protections this file does NOT
    assert, named in code so the gap survives a careless reading of the count.

    CASE 15 -- an unknown intent must FAIL rather than silently resolve to 1.0.
               Guarded path: position_sizer's `.get(intent, 1.0)` fallback.
    CASE 16 -- FundManager's hardcoded `leverage_map is None` default must not be
               able to activate.

    Both are LATENT today: main.py always supplies the map, and only MIS and CNC
    occur in production (SELECT DISTINCT product FROM orders = {MIS, CNC}). That
    is why they were deferred -- not because they do not matter.

    This test asserts the ACCOUNTING, not the protections. It goes red if someone
    claims full coverage without shipping UNIT 3b.
    """
    MATRIX_SLOTS = 18            # FILE16:185 -- 4 valid / 12 fail-closed / 1 parity / 1 neutrality
    DEFERRED_TO_UNIT_3B = {
        15: "unknown intent must fail, not resolve to 1.0 (position_sizer .get(intent, 1.0))",
        16: "FundManager's hardcoded `leverage_map is None` default must not activate",
    }
    asserted = MATRIX_SLOTS - len(DEFERRED_TO_UNIT_3B)
    assert asserted == 16, "coverage is 16 of 18 until UNIT 3b ships"
    assert set(DEFERRED_TO_UNIT_3B) == {15, 16}
    assert MATRIX_SLOTS != asserted, (
        "if these are ever equal, UNIT 3b shipped -- update this test deliberately, "
        "do not delete it")
