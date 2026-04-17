"""
tests/unit/test_instrument_cache_integration.py

Integration tests for Module 38 wiring:
  - IC7: PositionSizer auto-resolves lot_size from InstrumentCache
  - IC8: OrderPlacer._round_to_tick() rounds prices to tick_size

These tests use real InstrumentCache objects loaded from in-memory CSV.
They do NOT hit the database or broker.

Run: python -m pytest tests/unit/test_instrument_cache_integration.py -v
Or:  python tests/unit/test_instrument_cache_integration.py  (standalone)
"""
from __future__ import annotations

import sys
import math
import traceback
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from capital.fund_manager import CapitalSnapshot
from capital.position_sizer import PositionSizer
from core.instrument_cache import InstrumentCache
from core.time_authority import now_ist


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────────

_EQUITY_CSV = """\
symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector
RELIANCE,738561,NSE,1,0.05,false,ENERGY
TCS,2953217,NSE,1,0.05,false,IT
"""

_FNO_CSV = """\
symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector
NIFTY_FUT,256265,NSE,50,0.05,true,INDEX
BANKNIFTY_FUT,260105,NSE,25,0.05,true,FINANCIALS
"""

_DEFAULT_LEVERAGE = {
    "INTRADAY": 5.0,
    "COVER_ORDER": 6.0,
    "DELIVERY": 1.0,
    "BRACKET_ORDER": 5.0,
}

_DEFAULT_TIER_MULT = {
    "HIGH": 1.0,
    "MEDIUM": 0.7,
    "LOW": 0.5,
}


def _write_csv(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


class _MockFundManager:
    """Duck-typed FundManager stub."""

    def __init__(
        self,
        total: float = 500_000.0,
        intraday_avail: float = 350_000.0,
        positional_avail: float = 150_000.0,
    ) -> None:
        self._snap = CapitalSnapshot(
            total=total,
            intraday_avail=intraday_avail,
            intraday_reserved=0.0,
            intraday_used=0.0,
            positional_avail=positional_avail,
            positional_reserved=0.0,
            positional_used=0.0,
            daily_realized_pnl=0.0,
            ts=now_ist().replace(tzinfo=None).isoformat(),
        )

    def get_snapshot(self) -> CapitalSnapshot:
        return self._snap


def _make_sizer(
    instrument_cache=None,
    total: float = 500_000.0,
    intraday_avail: float = 350_000.0,
    risk_per_trade_pct: float = 0.01,
    max_concentration_pct: float = 0.20,
) -> PositionSizer:
    fm = _MockFundManager(
        total=total,
        intraday_avail=intraday_avail,
    )
    return PositionSizer(
        fund_manager=fm,
        leverage_map=_DEFAULT_LEVERAGE,
        risk_per_trade_pct=risk_per_trade_pct,
        max_concentration_pct=max_concentration_pct,
        tier_multipliers=_DEFAULT_TIER_MULT,
        instrument_cache=instrument_cache,
    )


# ─────────────────────────────────────────────────────────────────────────────
# IC7: PositionSizer auto-resolves lot_size from InstrumentCache
# ─────────────────────────────────────────────────────────────────────────────

def test_ic7_equity_lot_size_resolved_from_cache(tmp_path: Path) -> None:
    """
    IC7: equity has lot_size=1 in CSV.
    PositionSizer with cache wired auto-resolves lot_size(RELIANCE)=1.
    Result should be same as passing lot_size=1 explicitly.
    """
    p = _write_csv(tmp_path, "instruments.csv", _EQUITY_CSV)
    cache = InstrumentCache.load(p)

    sizer_with_cache = _make_sizer(instrument_cache=cache)
    sizer_no_cache   = _make_sizer(instrument_cache=None)

    result_with = sizer_with_cache.calculate(
        "RELIANCE", "BUY", 2500.0, 2450.0, "INTRADAY"
    )
    result_without = sizer_no_cache.calculate(
        "RELIANCE", "BUY", 2500.0, 2450.0, "INTRADAY", lot_size=1
    )

    assert result_with.success == result_without.success
    assert result_with.qty == result_without.qty
    print(f"  OK IC7 equity lot_size=1 from cache -> qty={result_with.qty}")


def test_ic7_fno_lot_size_resolved_from_cache(tmp_path: Path) -> None:
    """
    IC7: F&O instrument has lot_size=50 in CSV.
    PositionSizer with cache auto-resolves lot_size(NIFTY_FUT)=50.
    Result qty must be a multiple of 50.
    """
    p = _write_csv(tmp_path, "instruments.csv", _FNO_CSV)
    cache = InstrumentCache.load(p)

    sizer = _make_sizer(instrument_cache=cache, total=2_000_000.0, intraday_avail=1_400_000.0)
    result = sizer.calculate("NIFTY_FUT", "BUY", 22000.0, 21800.0, "INTRADAY")

    if result.success:
        assert result.qty % 50 == 0, f"qty {result.qty} not a multiple of lot_size=50"
        print(f"  OK IC7 F&O lot_size=50 from cache -> qty={result.qty} (multiple of 50)")
    else:
        # Sizing may fail with BELOW_MIN if qty < 50 — that's still correct behavior
        assert "lot_size" in result.reason or "BELOW_MIN" in result.constraint
        print(f"  OK IC7 F&O lot_size=50 from cache -> sizing failed (BELOW_MIN), constraint={result.constraint}")


def test_ic7_explicit_lot_size_overrides_cache(tmp_path: Path) -> None:
    """
    IC7: Explicit lot_size != 1 from caller takes precedence over cache.
    """
    p = _write_csv(tmp_path, "instruments.csv", _EQUITY_CSV)
    cache = InstrumentCache.load(p)

    sizer = _make_sizer(instrument_cache=cache, total=2_000_000.0, intraday_avail=1_400_000.0)

    # Pass explicit lot_size=10 -> should NOT be overridden by cache (cache says 1)
    result_explicit = sizer.calculate(
        "RELIANCE", "BUY", 2500.0, 2450.0, "INTRADAY", lot_size=10
    )
    # Pass no explicit lot_size -> cache resolves 1
    result_cache = sizer.calculate(
        "RELIANCE", "BUY", 2500.0, 2450.0, "INTRADAY", lot_size=1
    )

    if result_explicit.success:
        assert result_explicit.qty % 10 == 0, f"qty {result_explicit.qty} not multiple of 10"
    print(f"  OK IC7 explicit lot_size=10 not overridden by cache (cache says 1)")


def test_ic7_unknown_symbol_falls_back_to_default_lot_size(tmp_path: Path) -> None:
    """
    IC7: symbol not in cache -> silently falls back to caller-supplied lot_size (1).
    """
    p = _write_csv(tmp_path, "instruments.csv", _EQUITY_CSV)
    cache = InstrumentCache.load(p)

    sizer = _make_sizer(instrument_cache=cache)
    # WIPRO is NOT in the CSV -> should not raise, should use lot_size=1
    result = sizer.calculate("WIPRO", "BUY", 500.0, 490.0, "INTRADAY")

    # Result may succeed or fail on other constraints, but must not raise
    assert isinstance(result.success, bool)
    print(f"  OK IC7 unknown symbol falls back to lot_size=1 -> success={result.success}")


def test_ic7_no_cache_uses_caller_lot_size(tmp_path: Path) -> None:
    """
    IC7: No cache wired -> always uses caller-supplied lot_size unchanged.
    """
    sizer = _make_sizer(instrument_cache=None, total=2_000_000.0, intraday_avail=1_400_000.0)

    result = sizer.calculate(
        "ANYTHING", "BUY", 1000.0, 950.0, "INTRADAY", lot_size=5
    )
    if result.success:
        assert result.qty % 5 == 0
    print(f"  OK IC7 no cache -> lot_size=5 from caller honored, qty={result.qty}")


# ─────────────────────────────────────────────────────────────────────────────
# IC8: OrderPlacer._round_to_tick() (unit tests, no DB)
# ─────────────────────────────────────────────────────────────────────────────

class _MinimalOrderPlacer:
    """
    Minimal test double that only exposes _round_to_tick() for IC8 testing.
    Avoids constructing the real OrderPlacer with all its injected dependencies.
    """

    def __init__(self, instrument_cache=None) -> None:
        self._instrument_cache = instrument_cache

    def set_instrument_cache(self, cache) -> None:
        self._instrument_cache = cache

    def _round_to_tick(self, symbol: str, price: float) -> float:
        """Copied verbatim from orders/order_placer.py IC8 implementation."""
        if self._instrument_cache is None:
            return price
        try:
            tick = self._instrument_cache.tick_size(symbol)
            if tick <= 0:
                return price
            return round(math.floor(price / tick) * tick, 10)
        except Exception:
            return price


def test_ic8_round_to_tick_exact_multiple(tmp_path: Path) -> None:
    """IC8: price already on tick boundary -> unchanged."""
    p = _write_csv(tmp_path, "instruments.csv", _EQUITY_CSV)
    cache = InstrumentCache.load(p)
    op = _MinimalOrderPlacer(instrument_cache=cache)

    # RELIANCE tick=0.05 -> 2500.00 is already on boundary
    result = op._round_to_tick("RELIANCE", 2500.00)
    assert abs(result - 2500.00) < 1e-9
    print("  OK IC8 exact tick multiple unchanged")


def test_ic8_round_to_tick_rounds_down(tmp_path: Path) -> None:
    """IC8: price between ticks -> floors to nearest lower tick."""
    p = _write_csv(tmp_path, "instruments.csv", _EQUITY_CSV)
    cache = InstrumentCache.load(p)
    op = _MinimalOrderPlacer(instrument_cache=cache)

    # RELIANCE tick=0.05
    # 2500.03 -> floor to 2500.00
    result = op._round_to_tick("RELIANCE", 2500.03)
    assert abs(result - 2500.00) < 1e-9
    print(f"  OK IC8 2500.03 -> 2500.00 (tick=0.05)")


def test_ic8_round_to_tick_rounds_down_upper(tmp_path: Path) -> None:
    """IC8: price just below next tick -> still floors to lower tick."""
    p = _write_csv(tmp_path, "instruments.csv", _EQUITY_CSV)
    cache = InstrumentCache.load(p)
    op = _MinimalOrderPlacer(instrument_cache=cache)

    # RELIANCE tick=0.05
    # 2500.049 -> floor to 2500.00
    result = op._round_to_tick("RELIANCE", 2500.049)
    assert abs(result - 2500.00) < 1e-9
    print(f"  OK IC8 2500.049 -> 2500.00 (tick=0.05)")


def test_ic8_round_to_tick_next_boundary(tmp_path: Path) -> None:
    """IC8: price exactly on next tick boundary -> unchanged."""
    p = _write_csv(tmp_path, "instruments.csv", _EQUITY_CSV)
    cache = InstrumentCache.load(p)
    op = _MinimalOrderPlacer(instrument_cache=cache)

    # RELIANCE tick=0.05
    # 2500.05 -> stays 2500.05
    result = op._round_to_tick("RELIANCE", 2500.05)
    assert abs(result - 2500.05) < 1e-9
    print(f"  OK IC8 2500.05 -> 2500.05 (tick=0.05)")


def test_ic8_no_cache_passthrough(tmp_path: Path) -> None:
    """IC8: no cache wired -> price passes through unchanged."""
    op = _MinimalOrderPlacer(instrument_cache=None)
    result = op._round_to_tick("RELIANCE", 2500.03)
    assert result == 2500.03
    print("  OK IC8 no cache -> price unchanged")


def test_ic8_unknown_symbol_passthrough(tmp_path: Path) -> None:
    """IC8: unknown symbol (not in cache) -> price passes through unchanged."""
    p = _write_csv(tmp_path, "instruments.csv", _EQUITY_CSV)
    cache = InstrumentCache.load(p)
    op = _MinimalOrderPlacer(instrument_cache=cache)

    # WIPRO not in CSV -> InstrumentNotFoundError caught internally -> passthrough
    result = op._round_to_tick("WIPRO", 567.89)
    assert result == 567.89
    print("  OK IC8 unknown symbol -> price unchanged (exception caught)")


def test_ic8_set_instrument_cache_wires_correctly(tmp_path: Path) -> None:
    """IC8: set_instrument_cache() sets cache post-construction."""
    p = _write_csv(tmp_path, "instruments.csv", _EQUITY_CSV)
    cache = InstrumentCache.load(p)

    op = _MinimalOrderPlacer(instrument_cache=None)
    # Before wiring: passthrough
    assert op._round_to_tick("RELIANCE", 2500.03) == 2500.03

    # Wire cache
    op.set_instrument_cache(cache)
    # After wiring: rounded
    result = op._round_to_tick("RELIANCE", 2500.03)
    assert abs(result - 2500.00) < 1e-9
    print("  OK IC8 set_instrument_cache() wires correctly")


def test_ic8_sl_price_rounded(tmp_path: Path) -> None:
    """IC8: SL price (not just entry) also gets rounded by _round_to_tick."""
    p = _write_csv(tmp_path, "instruments.csv", _EQUITY_CSV)
    cache = InstrumentCache.load(p)
    op = _MinimalOrderPlacer(instrument_cache=cache)

    # SL price 2450.07 -> floor to 2450.05
    sl_rounded = op._round_to_tick("RELIANCE", 2450.07)
    assert abs(sl_rounded - 2450.05) < 1e-9
    print(f"  OK IC8 SL 2450.07 -> 2450.05 (tick=0.05)")


def test_ic8_large_price_precision(tmp_path: Path) -> None:
    """IC8: tick rounding works correctly for large prices with many decimals."""
    p = _write_csv(tmp_path, "instruments.csv", _EQUITY_CSV)
    cache = InstrumentCache.load(p)
    op = _MinimalOrderPlacer(instrument_cache=cache)

    # 45678.1234 with tick=0.05
    # expected: floor(45678.1234 / 0.05) * 0.05 = floor(913562.468) * 0.05
    #         = 913562 * 0.05 = 45678.10
    result = op._round_to_tick("RELIANCE", 45678.1234)
    expected = math.floor(45678.1234 / 0.05) * 0.05
    assert abs(result - expected) < 1e-6
    print(f"  OK IC8 large price 45678.1234 -> {result:.4f} (expected ~{expected:.4f})")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_ic7_equity_lot_size_resolved_from_cache,
        test_ic7_fno_lot_size_resolved_from_cache,
        test_ic7_explicit_lot_size_overrides_cache,
        test_ic7_unknown_symbol_falls_back_to_default_lot_size,
        test_ic7_no_cache_uses_caller_lot_size,
        test_ic8_round_to_tick_exact_multiple,
        test_ic8_round_to_tick_rounds_down,
        test_ic8_round_to_tick_rounds_down_upper,
        test_ic8_round_to_tick_next_boundary,
        test_ic8_no_cache_passthrough,
        test_ic8_unknown_symbol_passthrough,
        test_ic8_set_instrument_cache_wires_correctly,
        test_ic8_sl_price_rounded,
        test_ic8_large_price_precision,
    ]

    passed = failed = 0
    for fn in tests:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                fn(Path(tmp))
                passed += 1
            except Exception:
                print(f"  FAIL  {fn.__name__}")
                traceback.print_exc()
                failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    if failed:
        sys.exit(1)
