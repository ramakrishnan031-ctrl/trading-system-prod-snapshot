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
