"""Tests for tiered entry-slippage tolerance (price-band Rs abort)."""
from __future__ import annotations

from pathlib import Path

import pytest

from orders.order_placer import _slippage_abort_reason
from orders.price_math import tier_slippage_tolerance_rs

# Rama's starter bands as (max_price, max_slippage_rs).
_STARTER = [(100, 1.00), (200, 1.25), (500, 2.00), (999999, 3.00)]


# ── tier lookup (lower bound exclusive) ──────────────────────────────────────

@pytest.mark.parametrize("price,expected", [
    (80, 1.00), (99.99, 1.00),
    (100, 1.25),       # boundary: < is exclusive -> 100-200 band
    (150, 1.25),
    (200, 2.00),       # boundary -> 200-500 band
    (481.50, 2.00),    # THELEELA band
    (500, 3.00),       # boundary -> >500 band
    (600, 3.00), (50000, 3.00),
])
def test_tier_lookup(price, expected):
    assert tier_slippage_tolerance_rs(price, _STARTER, default_rs=2.0) == expected


def test_tier_lookup_above_all_bands_uses_default():
    assert tier_slippage_tolerance_rs(1_000_000, _STARTER, default_rs=2.5) == 2.5


def test_tier_lookup_empty_uses_default():
    assert tier_slippage_tolerance_rs(123, [], default_rs=2.0) == 2.0


def test_tier_lookup_unsorted_input_ok():
    shuffled = [(500, 2.00), (100, 1.00), (999999, 3.00), (200, 1.25)]
    assert tier_slippage_tolerance_rs(150, shuffled, 2.0) == 1.25


# ── abort decision ───────────────────────────────────────────────────────────

class _Cfg:
    def __init__(self, enabled=True, default=2.0, also_pct=True):
        self.enabled = enabled
        self.default_max_slippage_rs = default
        self.also_apply_pct_check = also_pct


def test_theleela_aborts_on_tier():
    # THELEELA: trigger 481.50, slip Rs 2.60 -> 200-500 band tol 2.00 -> ABORT
    reason = _slippage_abort_reason(481.50, 2.60, 0.54, _STARTER, _Cfg(), max_pct=1.0)
    assert reason is not None and "tier tolerance ₹2.00" in reason


def test_within_tier_allowed():
    # 481.50, slip Rs 1.50 < 2.00 tol, pct 0.31 < 1.0 -> allowed
    assert _slippage_abort_reason(481.50, 1.50, 0.31, _STARTER, _Cfg(), 1.0) is None


def test_high_price_band_allows_larger_rs():
    # 600 (>500 band tol 3.00), slip 2.80 < 3.00, pct 0.47 < 1.0 -> allowed
    assert _slippage_abort_reason(600.0, 2.80, 0.47, _STARTER, _Cfg(), 1.0) is None


def test_low_price_band_tight():
    # 80 (<100 band tol 1.00), slip 1.20 > 1.00 -> ABORT
    assert _slippage_abort_reason(80.0, 1.20, 1.5, _STARTER, _Cfg(), 1.0) is not None


def test_pct_belt_and_suspenders_still_aborts():
    # within tier (100 -> 100-200 tol 1.25, slip 1.20 ok) BUT pct 1.2% > 1% -> abort via pct
    reason = _slippage_abort_reason(100.0, 1.20, 1.20, _STARTER, _Cfg(also_pct=True), 1.0)
    assert reason is not None and "%" in reason


def test_also_pct_false_makes_tier_sole_gate():
    # tiers on, also_apply_pct_check False: within tier -> allowed even if pct > limit
    assert _slippage_abort_reason(100.0, 1.20, 1.20, _STARTER, _Cfg(also_pct=False), 1.0) is None


def test_tiers_disabled_falls_back_to_flat_pct():
    cfg = _Cfg(enabled=False)
    # slip Rs 2.60 would fail the tier, but tiers off -> only pct (0.54 < 1.0) -> allowed
    assert _slippage_abort_reason(481.50, 2.60, 0.54, _STARTER, cfg, 1.0) is None
    # pct over limit -> abort
    assert _slippage_abort_reason(481.50, 6.0, 1.25, _STARTER, cfg, 1.0) is not None


def test_no_tiers_config_uses_pct_only():
    # tiers_cfg None (backward compat) -> pct gate only
    assert _slippage_abort_reason(481.50, 2.60, 0.54, [], None, 1.0) is None
    assert _slippage_abort_reason(481.50, 6.0, 1.25, [], None, 1.0) is not None


# ── config wiring ────────────────────────────────────────────────────────────

def test_config_loads_starter_tiers():
    from core.config_loader import load_all
    cfg = load_all(Path("config"))
    st = cfg.system.entry_gate.slippage_tiers
    assert st.enabled is True
    assert [t.max_price for t in st.tiers] == [100, 200, 500, 999999]
    assert st.tiers[0].max_slippage_rs == 1.00
    assert st.default_max_slippage_rs == 2.00
    assert st.also_apply_pct_check is True
