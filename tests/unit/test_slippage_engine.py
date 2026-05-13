"""
tests/unit/test_slippage_engine.py

CFG-6 (2026-04-26 audit): paper-mode slippage applicator (P12).

Run: python -m pytest tests/unit/test_slippage_engine.py -v
Or:  python tests/unit/test_slippage_engine.py  (standalone)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from broker.slippage_engine import SlippageEngine
from core.config_loader import SlippageConfig, SlippageTierConfig
from core.instrument_cache import InstrumentCache


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

_CSV_MIXED = """\
symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector
RELIANCE,738561,NSE,1,0.05,true,ENERGY
TCS,2953217,NSE,1,0.05,true,IT
SMALLCO,123456,NSE,1,0.05,false,MISC
ZEROTICK,654321,NSE,1,0.05,false,MISC
"""


def _make_cache() -> InstrumentCache:
    td = tempfile.mkdtemp()
    p = Path(td) / "instruments.csv"
    p.write_text(_CSV_MIXED, encoding="utf-8")
    return InstrumentCache.load(p)


def _make_cfg(default_tier: str = "liquid") -> SlippageConfig:
    return SlippageConfig(
        tiers={
            "liquid": SlippageTierConfig(slippage_bps=5),
            "mid":    SlippageTierConfig(slippage_bps=15),
            "small":  SlippageTierConfig(slippage_bps=30),
        },
        default_tier=default_tier,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tier resolution (SE2)
# ─────────────────────────────────────────────────────────────────────────────

def test_tier_for_fno_returns_liquid() -> None:
    """RELIANCE has is_fno=True → 'liquid' tier."""
    eng = SlippageEngine(_make_cfg(), _make_cache())
    assert eng.tier_for("RELIANCE") == "liquid"
    assert eng.tier_for("TCS") == "liquid"
    print("  OK tier_for: F&O symbols → liquid")


def test_tier_for_non_fno_returns_mid() -> None:
    """SMALLCO has is_fno=False → 'mid' tier."""
    eng = SlippageEngine(_make_cfg(), _make_cache())
    assert eng.tier_for("SMALLCO") == "mid"
    print("  OK tier_for: non-F&O symbols → mid")


def test_tier_for_unknown_falls_back_to_default() -> None:
    """Unknown symbol → SlippageConfig.default_tier."""
    eng = SlippageEngine(_make_cfg(default_tier="small"), _make_cache())
    assert eng.tier_for("NOTHERE") == "small"
    print("  OK tier_for: unknown symbol → default_tier")


# ─────────────────────────────────────────────────────────────────────────────
# apply() math (SE3, SE5)
# ─────────────────────────────────────────────────────────────────────────────

def test_apply_buy_adds_slippage() -> None:
    """BUY: fill_price > requested price by tier bps."""
    eng = SlippageEngine(_make_cfg(), _make_cache())
    # RELIANCE → liquid → 5 bps. 5 bps of 2500.0 = 1.25 → tick=0.05 → 2501.25
    out = eng.apply("RELIANCE", "BUY", 2500.0)
    assert out == pytest.approx(2501.25, abs=1e-9), f"got {out}"
    print(f"  OK apply BUY 5bps: 2500 → {out}")


def test_apply_sell_subtracts_slippage() -> None:
    """SELL: fill_price < requested price by tier bps."""
    eng = SlippageEngine(_make_cfg(), _make_cache())
    out = eng.apply("RELIANCE", "SELL", 2500.0)
    assert out == pytest.approx(2498.75, abs=1e-9), f"got {out}"
    print(f"  OK apply SELL 5bps: 2500 → {out}")


def test_apply_mid_tier_uses_15bps() -> None:
    """SMALLCO is mid → 15 bps of 1000.0 = 1.50 → BUY → 1001.50."""
    eng = SlippageEngine(_make_cfg(), _make_cache())
    assert eng.apply("SMALLCO", "BUY", 1000.0) == pytest.approx(1001.50, abs=1e-9)
    assert eng.apply("SMALLCO", "SELL", 1000.0) == pytest.approx(998.50, abs=1e-9)
    print("  OK apply: mid-tier 15 bps math")


def test_apply_unknown_symbol_uses_default_tier() -> None:
    """Unknown symbol: no instrument row, no tick rounding, default tier bps."""
    eng = SlippageEngine(_make_cfg(default_tier="small"), _make_cache())
    # 30 bps of 100.0 = 0.30; no tick rounding for unknowns.
    out = eng.apply("NOTHERE", "BUY", 100.0)
    assert out == pytest.approx(100.30, abs=1e-9), f"got {out}"
    print(f"  OK apply unknown → default tier: {out}")


# ─────────────────────────────────────────────────────────────────────────────
# Tick rounding (SE4)
# ─────────────────────────────────────────────────────────────────────────────

def test_apply_buy_rounds_up_to_tick() -> None:
    """
    BUY rounds UP to next tick (conservative for trader).
    Price 100.07 with tick 0.05 → must round to 100.10, not 100.05.
    """
    eng = SlippageEngine(_make_cfg(), _make_cache())
    # SMALLCO, 15 bps of 100.04 = 0.1506 → 100.1906 → tick 0.05 → 100.20
    out = eng.apply("SMALLCO", "BUY", 100.04)
    assert out == pytest.approx(100.20, abs=1e-9), f"got {out}"
    print(f"  OK BUY rounds up to tick: 100.04 → {out}")


def test_apply_sell_rounds_down_to_tick() -> None:
    """SELL rounds DOWN to prev tick (conservative for trader)."""
    eng = SlippageEngine(_make_cfg(), _make_cache())
    # SMALLCO, 15 bps of 100.04 = 0.1506 → 100.04 - 0.1506 = 99.8894 → 99.85
    out = eng.apply("SMALLCO", "SELL", 100.04)
    assert out == pytest.approx(99.85, abs=1e-9), f"got {out}"
    print(f"  OK SELL rounds down to tick: 100.04 → {out}")


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases (SE6, SE7)
# ─────────────────────────────────────────────────────────────────────────────

def test_apply_zero_price_is_passthrough() -> None:
    """MARKET path can land here with price=0.0; no slippage applied."""
    eng = SlippageEngine(_make_cfg(), _make_cache())
    assert eng.apply("RELIANCE", "BUY", 0.0) == 0.0
    assert eng.apply("RELIANCE", "SELL", 0.0) == 0.0
    print("  OK apply price=0 short-circuit (MARKET-no-LTP)")


def test_apply_negative_price_is_passthrough() -> None:
    """Defensive: negative price returns unchanged (no math, no crash)."""
    eng = SlippageEngine(_make_cfg(), _make_cache())
    assert eng.apply("RELIANCE", "BUY", -10.0) == -10.0
    print("  OK apply price<0 short-circuit")


def test_apply_invalid_side_raises() -> None:
    """SE7: side outside BUY/SELL is a programming bug, raise ValueError."""
    eng = SlippageEngine(_make_cfg(), _make_cache())
    with pytest.raises(ValueError, match="BUY or SELL"):
        eng.apply("RELIANCE", "HOLD", 100.0)
    print("  OK apply side='HOLD' → ValueError")


def test_apply_side_case_insensitive() -> None:
    """side='buy' lowercase is accepted (defence-in-depth)."""
    eng = SlippageEngine(_make_cfg(), _make_cache())
    assert eng.apply("RELIANCE", "buy", 2500.0) == pytest.approx(2501.25)
    assert eng.apply("RELIANCE", "sell", 2500.0) == pytest.approx(2498.75)
    print("  OK apply side='buy'/'sell' lowercase accepted")


def test_ctor_rejects_default_tier_not_in_tiers() -> None:
    """Misconfigured default_tier is fail-fast at construction."""
    bad = SlippageConfig(
        tiers={"liquid": SlippageTierConfig(slippage_bps=5)},
        default_tier="missing",
    )
    with pytest.raises(ValueError, match="default_tier"):
        SlippageEngine(bad, _make_cache())
    print("  OK ctor rejects default_tier not in tiers")


def test_pure_idempotent() -> None:
    """SE6: same inputs produce same output every time, no state."""
    eng = SlippageEngine(_make_cfg(), _make_cache())
    a = eng.apply("RELIANCE", "BUY", 2500.0)
    b = eng.apply("RELIANCE", "BUY", 2500.0)
    c = eng.apply("RELIANCE", "BUY", 2500.0)
    assert a == b == c, f"expected idempotent: {a} {b} {c}"
    print("  OK apply is idempotent / pure")


# ─── FIX-014: Decimal tick rounding precision tests ─────────────────────────

def test_fix014_round_up_tick_001() -> None:
    """FIX-014: _round_up_to_tick with tick=0.01 precision."""
    from broker.slippage_engine import _round_up_to_tick
    assert _round_up_to_tick(10.004, 0.01) == 10.01, "10.004 → 10.01"
    assert _round_up_to_tick(10.010, 0.01) == 10.01, "10.010 → 10.01 (exact)"
    assert _round_up_to_tick(10.999, 0.01) == 11.00, "10.999 → 11.00 (rounds up)"
    print("  OK _round_up_to_tick tick=0.01")


def test_fix014_round_up_tick_005() -> None:
    """FIX-014: _round_up_to_tick with tick=0.05 precision."""
    from broker.slippage_engine import _round_up_to_tick
    assert _round_up_to_tick(10.02, 0.05) == 10.05, "10.02 → 10.05"
    assert _round_up_to_tick(10.05, 0.05) == 10.05, "10.05 → 10.05 (exact)"
    assert _round_up_to_tick(10.9999999998, 0.05) == 11.00, "10.9999999998 → 11.00 (precision edge)"
    print("  OK _round_up_to_tick tick=0.05")


def test_fix014_round_up_tick_025() -> None:
    """FIX-014: _round_up_to_tick with tick=0.25 precision."""
    from broker.slippage_engine import _round_up_to_tick
    assert _round_up_to_tick(100.1, 0.25) == 100.25, "100.1 → 100.25"
    assert _round_up_to_tick(100.25, 0.25) == 100.25, "100.25 → 100.25 (exact)"
    assert _round_up_to_tick(100.26, 0.25) == 100.50, "100.26 → 100.50"
    print("  OK _round_up_to_tick tick=0.25")


def test_fix014_round_down_tick_001() -> None:
    """FIX-014: _round_down_to_tick with tick=0.01 precision."""
    from broker.slippage_engine import _round_down_to_tick
    assert _round_down_to_tick(10.006, 0.01) == 10.00, "10.006 → 10.00"
    assert _round_down_to_tick(10.010, 0.01) == 10.01, "10.010 → 10.01 (exact)"
    assert _round_down_to_tick(10.999, 0.01) == 10.99, "10.999 → 10.99"
    print("  OK _round_down_to_tick tick=0.01")


def test_fix014_round_down_tick_005() -> None:
    """FIX-014: _round_down_to_tick with tick=0.05 precision."""
    from broker.slippage_engine import _round_down_to_tick
    assert _round_down_to_tick(10.07, 0.05) == 10.05, "10.07 → 10.05"
    assert _round_down_to_tick(10.05, 0.05) == 10.05, "10.05 → 10.05 (exact)"
    assert _round_down_to_tick(11.0000000002, 0.05) == 11.00, "11.0000000002 → 11.00 (precision edge)"
    print("  OK _round_down_to_tick tick=0.05")


def test_fix014_round_down_tick_025() -> None:
    """FIX-014: _round_down_to_tick with tick=0.25 precision."""
    from broker.slippage_engine import _round_down_to_tick
    assert _round_down_to_tick(100.3, 0.25) == 100.25, "100.3 → 100.25"
    assert _round_down_to_tick(100.25, 0.25) == 100.25, "100.25 → 100.25 (exact)"
    assert _round_down_to_tick(100.24, 0.25) == 100.00, "100.24 → 100.00"
    print("  OK _round_down_to_tick tick=0.25")


def test_fix014_round_up_negative_price() -> None:
    """FIX-014: _round_up_to_tick handles negative prices (theoretical edge)."""
    from broker.slippage_engine import _round_up_to_tick
    # Negative "round up" means toward zero (less negative)
    assert _round_up_to_tick(-10.04, 0.05) == -10.00, "-10.04 → -10.00"
    assert _round_up_to_tick(-10.05, 0.05) == -10.05, "-10.05 → -10.05 (exact)"
    print("  OK _round_up_to_tick negative prices")


def test_fix014_round_down_negative_price() -> None:
    """FIX-014: _round_down_to_tick handles negative prices (theoretical edge)."""
    from broker.slippage_engine import _round_down_to_tick
    # Negative "round down" means away from zero (more negative)
    assert _round_down_to_tick(-10.02, 0.05) == -10.05, "-10.02 → -10.05"
    assert _round_down_to_tick(-10.05, 0.05) == -10.05, "-10.05 → -10.05 (exact)"
    print("  OK _round_down_to_tick negative prices")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

TESTS = [
    test_tier_for_fno_returns_liquid,
    test_tier_for_non_fno_returns_mid,
    test_tier_for_unknown_falls_back_to_default,
    test_apply_buy_adds_slippage,
    test_apply_sell_subtracts_slippage,
    test_apply_mid_tier_uses_15bps,
    test_apply_unknown_symbol_uses_default_tier,
    test_apply_buy_rounds_up_to_tick,
    test_apply_sell_rounds_down_to_tick,
    test_apply_zero_price_is_passthrough,
    test_apply_negative_price_is_passthrough,
    test_apply_invalid_side_raises,
    test_apply_side_case_insensitive,
    test_ctor_rejects_default_tier_not_in_tiers,
    test_pure_idempotent,
    # FIX-014 decimal precision tests
    test_fix014_round_up_tick_001,
    test_fix014_round_up_tick_005,
    test_fix014_round_up_tick_025,
    test_fix014_round_down_tick_001,
    test_fix014_round_down_tick_005,
    test_fix014_round_down_tick_025,
    test_fix014_round_up_negative_price,
    test_fix014_round_down_negative_price,
]


def run_all_tests() -> int:
    print("=" * 70)
    print("slippage_engine.py -- Test Suite (CFG-6, 2026-04-26 audit)")
    print("=" * 70)
    failed = []
    for t in TESTS:
        print(f"\n-> {t.__name__}")
        try:
            t()
        except AssertionError as e:
            failed.append((t.__name__, f"AssertionError: {e}"))
            print(f"  FAIL: {e}")
        except Exception as e:
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR: {type(e).__name__}: {e}")
    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(TESTS)}")
        for n, e in failed:
            print(f"  FAIL {n}: {e}")
        return 1
    print(f"PASSED: all {len(TESTS)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
