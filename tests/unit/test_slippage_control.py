"""Tests for entry-slippage control: %-of-SL (default) / flat_tiers / pct modes."""
from __future__ import annotations

from pathlib import Path

import pytest

from orders.order_placer import _compute_slippage_tolerance, _slippage_decision
from orders.price_math import tier_slippage_tolerance_rs

_STARTER = [(100, 1.00), (200, 1.25), (500, 2.00), (999999, 3.00)]


class _Cfg:
    def __init__(self, mode="sl_fraction", enabled=True, frac=0.22, cap=5.0, hard=10.0,
                 also_pct=True, default=2.0, tiers=None):
        self.mode = mode
        self.enabled = enabled
        self.max_slippage_fraction = frac
        self.absolute_cap_rs = cap
        self.hard_max_slippage_rs = hard
        self.also_apply_pct_check = also_pct
        self.default_max_slippage_rs = default
        self.tiers = tiers or []


def _sl(price, sl_pct, long=True):
    return price * (1 - sl_pct) if long else price * (1 + sl_pct)


# ── tier lookup (flat_tiers mode) ────────────────────────────────────────────

def test_tier_lookup_boundary_exclusive():
    assert tier_slippage_tolerance_rs(100, _STARTER, 2.0) == 1.25   # 100 -> 100-200 band
    assert tier_slippage_tolerance_rs(99.99, _STARTER, 2.0) == 1.00
    assert tier_slippage_tolerance_rs(481.50, _STARTER, 2.0) == 2.00


# ── _compute_slippage_tolerance: modes + backstops + hard ceiling ────────────

def test_sl_fraction_basic():
    # SL distance 9.62 (₹481 × 2%), frac 0.22 -> 2.12, under the ₹5 cap
    tol, sl_dist = _compute_slippage_tolerance(_Cfg(), 481.50, _sl(481.50, 0.02), [], 1.0)
    assert round(sl_dist, 2) == 9.63 and round(tol, 2) == 2.12


def test_sl_fraction_absolute_cap_bites():
    # ₹2000 × 2% = ₹40 dist; 40×0.22=8.80 -> capped at absolute_cap_rs 5.0
    tol, _ = _compute_slippage_tolerance(_Cfg(), 2000.0, _sl(2000, 0.02), [], 1.0)
    assert tol == 5.0


def test_sl_fraction_hard_ceiling_always():
    # huge SL + a high absolute_cap -> hard ceiling (10.0) still wins
    tol, _ = _compute_slippage_tolerance(_Cfg(cap=50.0, hard=10.0), 5000.0, _sl(5000, 0.05), [], 1.0)
    assert tol == 10.0


def test_sl_fraction_no_sl_falls_back_to_cap():
    tol, sl_dist = _compute_slippage_tolerance(_Cfg(cap=5.0), 300.0, None, [], 1.0)
    assert tol == 5.0 and sl_dist is None


def test_flat_tiers_mode():
    tol, sl_dist = _compute_slippage_tolerance(
        _Cfg(mode="flat_tiers", tiers=_STARTER), 481.50, _sl(481.50, 0.02), _STARTER, 1.0)
    assert tol == 2.00 and sl_dist is None


def test_pct_mode():
    tol, _ = _compute_slippage_tolerance(_Cfg(mode="pct"), 500.0, _sl(500, 0.01), [], 1.0)
    assert tol == 5.0   # 500 × 1%


# ── _slippage_decision: the 5 worked examples from the spec ───────────────────

def test_ex1_vwap_200_tight():
    cfg = _Cfg()
    sl = _sl(200.0, 0.008)   # 0.8% -> dist 1.60 -> tol min(0.352, 5) = 0.35
    assert _slippage_decision(cfg, 200.0, sl, 0.30, 0.15, [], 1.0)[0] is None       # allow
    assert _slippage_decision(cfg, 200.0, sl, 0.50, 0.25, [], 1.0)[0] is not None   # abort


def test_ex2_theleela_aborts():
    cfg = _Cfg()
    sl = _sl(481.50, 0.02)   # dist 9.63 -> tol 2.12
    reason, tol, sl_dist = _slippage_decision(cfg, 481.50, sl, 3.10, 0.64, [], 1.0)
    assert reason is not None and "of SL" in reason and round(tol, 2) == 2.12


def test_ex3_gap_fade_500():
    cfg = _Cfg()
    sl = _sl(500.0, 0.01)    # dist 5.00 -> tol 1.10
    assert _slippage_decision(cfg, 500.0, sl, 1.00, 0.20, [], 1.0)[0] is None       # allow
    assert _slippage_decision(cfg, 500.0, sl, 1.50, 0.30, [], 1.0)[0] is not None   # abort


def test_ex4_backstop_catches_wide_sl_high_price():
    cfg = _Cfg()
    sl = _sl(2000.0, 0.02)   # dist 40 -> tol capped at 5.0
    assert _slippage_decision(cfg, 2000.0, sl, 6.00, 0.30, [], 1.0)[0] is not None  # backstop abort


def test_ex5_chemplasts_aborts():
    cfg = _Cfg()
    sl = _sl(221.0, 0.015)   # first_pullback 1.5% -> dist 3.32 -> tol 0.73
    assert _slippage_decision(cfg, 221.0, sl, 2.55, 1.15, [], 1.0)[0] is not None


def test_lloydsengg_aborts_under_sl_fraction():
    # FINDING vs spec Step 9.6 ("LLOYDSENGG allows"): that assumed flat_tiers.
    # Under sl_fraction(0.22): ₹83.01 positional 2% -> SL dist 1.66 -> tol 0.365;
    # the ₹0.43 slip is 26% of the SL distance (> 22%) -> ABORTS. (Correct per the
    # "≤22% of SL" rule; raise max_slippage_fraction to ~0.27 to allow it.)
    cfg = _Cfg()
    assert _slippage_decision(cfg, 83.01, _sl(83.01, 0.02), 0.43, 0.52, [], 1.0)[0] is not None


# ── belt-and-suspenders + disabled + parity-config ───────────────────────────

def test_flat_tiers_also_pct_belt():
    # within tier (100-200 tol 1.25, slip 1.20) but pct 1.2% > 1% -> abort via pct
    cfg = _Cfg(mode="flat_tiers", tiers=_STARTER, also_pct=True)
    assert _slippage_decision(cfg, 100.0, _sl(100, 0.01), 1.20, 1.20, _STARTER, 1.0)[0] is not None


def test_config_loads_slippage_control():
    from core.config_loader import load_all
    sc = load_all(Path("config")).system.entry_gate.slippage_control
    assert sc.enabled is True and sc.mode == "sl_fraction"
    assert sc.max_slippage_fraction == 0.22 and sc.absolute_cap_rs == 5.00
    assert sc.hard_max_slippage_rs == 10.0
    assert [t.max_price for t in sc.tiers] == [100, 200, 500, 999999]


def test_config_rejects_bad_mode():
    from core.config_loader import SlippageControlConfig
    with pytest.raises(Exception):
        SlippageControlConfig(mode="bogus")


# ── Phase 3a: override hierarchy (Symbol > Strategy > Band > Global) ───────────

from orders.order_placer import resolve_slippage_fraction, validate_slippage_overrides


class _Ov:
    def __init__(self, enabled=True, band=None, strat=None, sym=None):
        self.enabled = enabled
        self.by_price_band = band or {}
        self.by_strategy = strat or {}
        self.by_symbol = sym or {}


class _CfgOv:
    def __init__(self, frac=0.22, overrides=None):
        self.mode = "sl_fraction"
        self.max_slippage_fraction = frac
        self.absolute_cap_rs = 5.0
        self.hard_max_slippage_rs = 10.0
        self.overrides = overrides


# The exact config from spec Step 9.
_SPEC_OV = _Ov(band={"0-100": 0.18}, strat={"gap_fade": 0.25}, sym={"IDEA": 0.15})


def test_resolve_ex1_symbol_wins_over_everything():
    # IDEA (any strategy, any band) -> 0.15 (symbol wins)
    cfg = _CfgOv(overrides=_SPEC_OV)
    assert resolve_slippage_fraction(cfg, "IDEA", "vwap", "300-500") == (0.15, "symbol:IDEA")
    assert resolve_slippage_fraction(cfg, "IDEA", "gap_fade", "0-100") == (0.15, "symbol:IDEA")


def test_resolve_ex2_strategy_beats_band_and_global():
    # gap_fade on TATASTEEL @ Rs500 -> 0.25 (strategy wins over band/global)
    cfg = _CfgOv(overrides=_SPEC_OV)
    assert resolve_slippage_fraction(cfg, "TATASTEEL", "gap_fade", "500-1000") == (0.25, "strategy:gap_fade")


def test_resolve_ex3_band_wins_when_no_symbol_or_strategy():
    # vwap on a Rs80 stock (band 0-100) -> 0.18
    cfg = _CfgOv(overrides=_SPEC_OV)
    assert resolve_slippage_fraction(cfg, "SOMECO", "vwap", "0-100") == (0.18, "band:0-100")


def test_resolve_ex4_global_when_nothing_more_specific():
    # vwap on a Rs300 stock (band 300-500, no override) -> 0.22 global
    cfg = _CfgOv(overrides=_SPEC_OV)
    assert resolve_slippage_fraction(cfg, "SOMECO", "vwap", "300-500") == (0.22, "global")


def test_resolve_ex5_symbol_beats_strategy():
    # gap_fade on IDEA -> 0.15 (symbol beats strategy)
    cfg = _CfgOv(overrides=_SPEC_OV)
    assert resolve_slippage_fraction(cfg, "IDEA", "gap_fade", "0-100") == (0.15, "symbol:IDEA")


def test_resolve_no_overrides_is_global():
    assert resolve_slippage_fraction(_CfgOv(overrides=None), "IDEA", "gap_fade", "0-100") == (0.22, "global")
    assert resolve_slippage_fraction(_CfgOv(overrides=_Ov()), "IDEA", "gap_fade", "0-100") == (0.22, "global")


def test_resolve_disabled_overrides_ignored():
    cfg = _CfgOv(overrides=_Ov(enabled=False, sym={"IDEA": 0.15}))
    assert resolve_slippage_fraction(cfg, "IDEA", "gap_fade", "0-100") == (0.22, "global")


def test_resolve_band_none_falls_through():
    # No price band (None) but a strategy override exists -> strategy wins.
    cfg = _CfgOv(overrides=_SPEC_OV)
    assert resolve_slippage_fraction(cfg, "X", "gap_fade", None) == (0.25, "strategy:gap_fade")
    # No band + no other override -> global.
    assert resolve_slippage_fraction(cfg, "X", "vwap", None) == (0.22, "global")


# ── fraction_override threads into the tolerance computation ──────────────────

def test_fraction_override_changes_tolerance():
    # SL dist 1.66 (Rs83 × 2%). global 0.22 -> 0.365; IDEA 0.15 -> 0.249.
    sl = _sl(83.0, 0.02)
    tol_glob, _ = _compute_slippage_tolerance(_Cfg(), 83.0, sl, [], 1.0)
    tol_idea, _ = _compute_slippage_tolerance(_Cfg(), 83.0, sl, [], 1.0, fraction_override=0.15)
    assert round(tol_glob, 3) == 0.365 and round(tol_idea, 3) == 0.249


def test_fraction_override_tightens_abort_decision():
    # A 0.30 slip passes under global 0.22 but the IDEA 0.15 override aborts it.
    sl = _sl(83.0, 0.02)
    assert _slippage_decision(_Cfg(), 83.0, sl, 0.30, 0.36, [], 1.0)[0] is None
    assert _slippage_decision(_Cfg(), 83.0, sl, 0.30, 0.36, [], 1.0, fraction_override=0.15)[0] is not None


def test_fraction_override_ignored_for_flat_tiers():
    # Override fraction has no effect on flat_tiers mode (it uses tier Rs).
    tol, _ = _compute_slippage_tolerance(
        _Cfg(mode="flat_tiers", tiers=_STARTER), 481.50, _sl(481.50, 0.02), _STARTER, 1.0,
        fraction_override=0.05)
    assert tol == 2.00


# ── validate_slippage_overrides: startup warnings (typo + extreme catch) ──────

def test_validate_extreme_fraction_warned():
    ov = _Ov(sym={"IDEA": 0.02, "RELIANCE": 0.30})   # 0.02 < 0.05 extreme
    w = validate_slippage_overrides(ov)
    assert any("extreme" in x and "IDEA" in x for x in w)
    assert not any("RELIANCE" in x for x in w)       # 0.30 is in range


def test_validate_unknown_symbol_warned():
    ov = _Ov(sym={"IDEA": 0.15, "TYPOO": 0.20})
    w = validate_slippage_overrides(ov, known_symbols={"IDEA", "RELIANCE"})
    assert any("TYPOO" in x and "not a known instrument" in x for x in w)
    assert not any("IDEA" in x and "not a known" in x for x in w)


def test_validate_unknown_strategy_warned():
    ov = _Ov(strat={"gap_fade": 0.25, "nonsense": 0.20})
    w = validate_slippage_overrides(ov, known_strategies={"gap_fade", "vwap_bounce"})
    assert any("nonsense" in x and "not a loaded strategy" in x for x in w)


def test_validate_clean_config_no_warnings():
    ov = _Ov(band={"0-100": 0.18}, strat={"gap_fade": 0.25}, sym={"IDEA": 0.15})
    w = validate_slippage_overrides(ov, known_symbols={"IDEA"}, known_strategies={"gap_fade"})
    assert w == []


def test_validate_disabled_overrides_no_warnings():
    ov = _Ov(enabled=False, sym={"TYPOO": 0.99})
    assert validate_slippage_overrides(ov, known_symbols={"IDEA"}) == []


# ── config: overrides block loads + rejects bad fractions ─────────────────────

def test_config_loads_overrides_block():
    from core.config_loader import load_all
    ov = load_all(Path("config")).system.entry_gate.slippage_control.overrides
    assert ov.enabled is True
    assert ov.by_price_band.get("0-100") == 0.18   # the shipped example
    assert ov.by_strategy == {} and ov.by_symbol == {}


def test_config_overrides_reject_out_of_range_fraction():
    from core.config_loader import SlippageOverridesConfig
    with pytest.raises(Exception):
        SlippageOverridesConfig(by_symbol={"IDEA": 1.5})    # > 1.0
    with pytest.raises(Exception):
        SlippageOverridesConfig(by_symbol={"IDEA": 0.0})    # not > 0
    with pytest.raises(Exception):
        SlippageOverridesConfig(by_strategy={"x": -0.1})    # negative
    # valid boundary values accepted
    SlippageOverridesConfig(by_symbol={"IDEA": 1.0}, by_price_band={"0-100": 0.05})
