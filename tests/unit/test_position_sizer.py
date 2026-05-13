"""
tests/unit/test_position_sizer.py

Validates capital/position_sizer.py against PS1-PS13 locked decisions.

Uses a MockFundManager that returns a scripted CapitalSnapshot.
No StateStore, no SQLite, no FundManager state mutations.

Run: python -m pytest tests/unit/test_position_sizer.py -v
Or:  python tests/unit/test_position_sizer.py  (standalone mode)
"""
from __future__ import annotations

import sys
import math
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from capital.fund_manager import CapitalSnapshot
from capital.position_sizer import PositionSizer, SizingResult
from core.time_authority import now_ist


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

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


class _MockFundManager:
    """Duck-typed FundManager stub: get_snapshot() returns a scripted snapshot."""

    def __init__(
        self,
        total: float = 100_000.0,
        intraday_avail: float = 70_000.0,
        positional_avail: float = 30_000.0,
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


class _MockLogger:
    """Captures warning() calls for assertion in tests."""

    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, msg: str, extra: dict | None = None) -> None:
        self.warnings.append((msg, extra or {}))

    def info(self, msg: str, extra: dict | None = None) -> None:
        pass

    def debug(self, msg: str, extra: dict | None = None) -> None:
        pass

    def error(self, msg: str, extra: dict | None = None) -> None:
        pass

    def critical(self, msg: str, extra: dict | None = None) -> None:
        pass


def _make_sizer(
    total: float = 100_000.0,
    intraday_avail: float = 70_000.0,
    positional_avail: float = 30_000.0,
    risk_per_trade_pct: float = 0.01,
    max_concentration_pct: float = 0.10,
    min_qty_threshold: int = 1,
    tier_multipliers: dict | None = None,
    logger=None,
    lot_skew_rejection_threshold: float = 0.25,  # FIX-021
) -> PositionSizer:
    fm = _MockFundManager(total, intraday_avail, positional_avail)
    return PositionSizer(
        fund_manager=fm,
        leverage_map=_DEFAULT_LEVERAGE,
        risk_per_trade_pct=risk_per_trade_pct,
        max_concentration_pct=max_concentration_pct,
        min_qty_threshold=min_qty_threshold,
        tier_multipliers=tier_multipliers or _DEFAULT_TIER_MULT,
        logger=logger,
        lot_skew_rejection_threshold=lot_skew_rejection_threshold,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- risk-bound sizing (PS2)
# ─────────────────────────────────────────────────────────────────────────────

def test_risk_bound_qty() -> None:
    """
    total=100k, risk=1% -> risk_rs=1000.
    entry=50, sl=40 -> sl_dist=10 -> qty_by_risk=100.
    intraday_avail=70k, leverage=5 -> margin_per_share=10 -> qty_by_capital=7000.
    conc=10% -> 10000/50=200.
    min(100, 7000, 200)=100 -> RISK-bound. tier=HIGH(1.0), lot=1.
    """
    sizer = _make_sizer(
        total=100_000.0, intraday_avail=70_000.0,
        risk_per_trade_pct=0.01, max_concentration_pct=0.10,
        tier_multipliers={"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.5},
    )
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="HIGH")
    assert result.success
    assert result.qty == 100
    assert result.constraint == "RISK"
    assert result.bucket == "intraday"
    print("  OK risk-bound qty: risk_rs=1000, sl_dist=10 -> qty=100, constraint=RISK (PS2)")


def test_capital_bound_qty() -> None:
    """
    total=100k, risk=1% -> risk_rs=1000, sl_dist=10 -> qty_by_risk=100.
    intraday_avail=500, leverage=5 -> margin_per_share=10 -> qty_by_capital=50.
    conc=10% -> 10000/50=200.
    min(100, 50, 200)=50 -> CAPITAL-bound.
    """
    sizer = _make_sizer(
        total=100_000.0, intraday_avail=500.0,
        risk_per_trade_pct=0.01, max_concentration_pct=0.10,
        tier_multipliers={"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.5},
    )
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="HIGH")
    assert result.success
    assert result.qty == 50
    assert result.constraint == "CAPITAL"
    print("  OK capital-bound qty: tiny bucket -> qty=50, constraint=CAPITAL (PS2)")


def test_concentration_bound_qty() -> None:
    """
    total=100k, risk=1%, sl_dist=10 -> qty_by_risk=100.
    intraday_avail=70k, lev=5 -> qty_by_capital=700.
    conc=2% -> 2000/500=4 -> CONCENTRATION-bound.
    entry=500, sl=490, sl_dist=10.
    """
    sizer = _make_sizer(
        total=100_000.0, intraday_avail=70_000.0,
        risk_per_trade_pct=0.01, max_concentration_pct=0.02,
        tier_multipliers={"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.5},
    )
    result = sizer.calculate("SYM", "BUY", 500.0, 490.0, "INTRADAY", score_tier="HIGH")
    assert result.success
    assert result.qty == 4
    assert result.constraint == "CONCENTRATION"
    print("  OK concentration-bound qty: conc_pct=2%, entry=500 -> qty=4, constraint=CONCENTRATION (PS2)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- tier multipliers (PS5, audit Bug 5)
# ─────────────────────────────────────────────────────────────────────────────

def test_tier_high_full_size() -> None:
    """HIGH tier: no reduction. qty_by_risk=100 -> tiered=100."""
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="HIGH")
    assert result.success
    assert result.qty == 100    # 1.0 * 100 = 100
    print("  OK tier HIGH: full size, qty=100 (PS5)")


def test_tier_medium_reduces_qty() -> None:
    """MEDIUM tier: floor(100 * 0.7) = 70."""
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="MEDIUM")
    assert result.success
    expected = math.floor(100 * 0.7)    # 70
    assert result.qty == expected, f"Expected {expected}, got {result.qty}"
    print(f"  OK tier MEDIUM: floor(100*0.7)={expected}, qty={result.qty} (PS5)")


def test_tier_low_reduces_qty_audit_regression() -> None:
    """
    LOW tier: floor(100 * 0.5) = 50.
    Audit Bug 5 regression: old code incorrectly applied 1.0 to LOW tier.
    """
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="LOW")
    assert result.success
    expected = math.floor(100 * 0.5)    # 50
    assert result.qty == expected, f"Expected {expected} (0.5 mult), got {result.qty}"
    # Regression: must NOT be 100 (the old 1.0 multiplier bug)
    assert result.qty != 100, "Audit Bug 5 regression: LOW tier must NOT give full qty"
    print(f"  OK tier LOW: floor(100*0.5)={expected}, not 100 (audit Bug 5 fix, PS5)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- lot size rounding (PS6)
# ─────────────────────────────────────────────────────────────────────────────

def test_lot_size_rounding_snaps_down() -> None:
    """lot_size=30, tiered_qty=100 -> (100//30)*30=90."""
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY",
                             score_tier="HIGH", lot_size=30)
    assert result.success
    assert result.qty == 90, f"Expected 90 (floor to 30), got {result.qty}"
    print("  OK lot_size=30: qty snaps from 100 to 90 (PS6)")


def test_lot_size_larger_than_computed_qty_fails() -> None:
    """
    tiered_qty=100, lot_size=150 -> (100//150)*150=0 -> BELOW_MIN.
    """
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY",
                             score_tier="HIGH", lot_size=150)
    assert not result.success
    assert result.qty == 0
    assert result.constraint == "BELOW_MIN"
    print("  OK lot_size(150) > tiered_qty(100) -> success=False, constraint=BELOW_MIN (PS6)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- FIX-021: Lot skew rejection
# ─────────────────────────────────────────────────────────────────────────────

def test_fix021_high_skew_rejected() -> None:
    """FIX-021: tiered=40, lot=25 -> final=25, skew=37.5% > 25% -> REJECTED_LOT_SKEW."""
    fm = _MockFundManager(total=100_000.0, intraday_avail=70_000.0)
    sizer = PositionSizer(
        fund_manager=fm,
        leverage_map=_DEFAULT_LEVERAGE,
        risk_per_trade_pct=0.01,
        max_concentration_pct=0.10,
        tier_multipliers=_DEFAULT_TIER_MULT,
        lot_skew_rejection_threshold=0.25,  # 25%
    )
    # Setup: tiered_qty = 40 (by picking entry/sl to produce qty_by_risk=40, tier=HIGH)
    # risk_rs = 100k * 0.01 = 1000, sl_dist = 25 -> qty_by_risk = floor(1000/25) = 40
    result = sizer.calculate("SYM", "BUY", 100.0, 75.0, "INTRADAY",
                             score_tier="HIGH", lot_size=25)
    # tiered_qty=40, final_qty=(40//25)*25=25, skew=(40-25)/40=0.375=37.5% > 25%
    assert not result.success
    assert result.qty == 0
    assert result.constraint == "REJECTED_LOT_SKEW"
    assert "37.5%" in result.reason or "0.375" in result.reason
    print("  OK FIX-021: tiered=40, lot=25 -> skew=37.5% > 25% -> REJECTED_LOT_SKEW")


def test_fix021_acceptable_skew_proceeds() -> None:
    """FIX-021: tiered=30, lot=25 -> final=25, skew=16.7% < 25% -> proceeds with qty=25."""
    fm = _MockFundManager(total=100_000.0, intraday_avail=70_000.0)
    sizer = PositionSizer(
        fund_manager=fm,
        leverage_map=_DEFAULT_LEVERAGE,
        risk_per_trade_pct=0.01,
        max_concentration_pct=0.10,
        tier_multipliers=_DEFAULT_TIER_MULT,
        lot_skew_rejection_threshold=0.25,  # 25%
    )
    # Setup: tiered_qty = 30 (risk_rs=1000, sl_dist=33.33... -> qty_by_risk=30)
    result = sizer.calculate("SYM", "BUY", 100.0, 66.67, "INTRADAY",
                             score_tier="HIGH", lot_size=25)
    # tiered_qty=30, final_qty=(30//25)*25=25, skew=(30-25)/30=0.1667=16.7% < 25%
    assert result.success
    assert result.qty == 25
    print("  OK FIX-021: tiered=30, lot=25 -> skew=16.7% < 25% -> proceeds with qty=25")


def test_fix021_lot_size_one_never_rejected() -> None:
    """FIX-021: lot_size=1 skips skew check entirely (equity default)."""
    fm = _MockFundManager(total=100_000.0, intraday_avail=70_000.0)
    sizer = PositionSizer(
        fund_manager=fm,
        leverage_map=_DEFAULT_LEVERAGE,
        risk_per_trade_pct=0.01,
        max_concentration_pct=0.10,
        tier_multipliers=_DEFAULT_TIER_MULT,
        lot_skew_rejection_threshold=0.01,  # Very strict 1% threshold
    )
    # Any qty with lot_size=1 should pass (no truncation, no skew)
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY",
                             score_tier="HIGH", lot_size=1)
    assert result.success
    assert result.qty == 100  # normal calculation
    print("  OK FIX-021: lot_size=1 -> skew check skipped, qty=100 (never rejected)")


def test_fix021_threshold_configurable() -> None:
    """FIX-021: different threshold changes rejection behavior."""
    fm = _MockFundManager(total=100_000.0, intraday_avail=70_000.0)

    # Strict threshold: 10%
    sizer_strict = PositionSizer(
        fund_manager=fm,
        leverage_map=_DEFAULT_LEVERAGE,
        risk_per_trade_pct=0.01,
        max_concentration_pct=0.10,
        tier_multipliers=_DEFAULT_TIER_MULT,
        lot_skew_rejection_threshold=0.10,  # 10%
    )
    # tiered=30, lot=25 -> skew=16.7% > 10% -> rejected
    result_strict = sizer_strict.calculate("SYM", "BUY", 100.0, 66.67, "INTRADAY",
                                            score_tier="HIGH", lot_size=25)
    assert not result_strict.success
    assert result_strict.constraint == "REJECTED_LOT_SKEW"

    # Lenient threshold: 50%
    sizer_lenient = PositionSizer(
        fund_manager=fm,
        leverage_map=_DEFAULT_LEVERAGE,
        risk_per_trade_pct=0.01,
        max_concentration_pct=0.10,
        tier_multipliers=_DEFAULT_TIER_MULT,
        lot_skew_rejection_threshold=0.50,  # 50%
    )
    # tiered=30, lot=25 -> skew=16.7% < 50% -> proceeds
    result_lenient = sizer_lenient.calculate("SYM", "BUY", 100.0, 66.67, "INTRADAY",
                                              score_tier="HIGH", lot_size=25)
    assert result_lenient.success
    assert result_lenient.qty == 25
    print("  OK FIX-021: threshold=10% rejects, threshold=50% proceeds (configurable)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- bucket determination (PS7)
# ─────────────────────────────────────────────────────────────────────────────

def test_intraday_uses_intraday_bucket() -> None:
    """INTRADAY intent -> reads intraday_avail, bucket='intraday'."""
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0, positional_avail=30_000.0)
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="HIGH")
    assert result.success
    assert result.bucket == "intraday"
    # qty_by_capital should be based on 70k, not 30k
    # 70k / (50/5) = 70k / 10 = 7000; risk binds at 100
    assert result.qty == 100
    print("  OK INTRADAY uses intraday bucket (avail=70k), bucket='intraday' (PS7)")


def test_delivery_uses_positional_bucket() -> None:
    """DELIVERY intent -> reads positional_avail, bucket='positional', leverage=1x."""
    # total=100k, risk=1%, entry=50, sl=40, sl_dist=10 -> qty_by_risk=100
    # positional_avail=30k, lev=1 -> margin_per_share=50 -> qty_by_capital=600
    # conc=10% -> 10000/50=200
    # min(100, 600, 200)=100 -> RISK
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0, positional_avail=30_000.0)
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "DELIVERY", score_tier="HIGH")
    assert result.success
    assert result.bucket == "positional"
    print("  OK DELIVERY uses positional bucket, bucket='positional' (PS7)")


def test_intraday_exhausted_positional_has_cash_fails() -> None:
    """
    INTRADAY with intraday_avail=0 -> qty_by_capital=0 -> success=False.
    Positional still has cash but cross-bucket borrow is forbidden (FM3/PS7).
    """
    sizer = _make_sizer(
        total=100_000.0, intraday_avail=0.0, positional_avail=30_000.0,
    )
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="HIGH")
    assert not result.success
    assert result.qty == 0
    print("  OK intraday exhausted + positional full -> INTRADAY fails (no cross-bucket, FM3/PS7)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- margin calculation (PS2, PS11)
# ─────────────────────────────────────────────────────────────────────────────

def test_margin_calc_intraday_5x_leverage() -> None:
    """margin_required = qty * entry_price / leverage = 100 * 50 / 5 = 1000."""
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="HIGH")
    assert result.success
    expected_margin = result.qty * (50.0 / 5.0)
    assert abs(result.margin_required - expected_margin) < 0.01, (
        f"Expected margin={expected_margin}, got {result.margin_required}"
    )
    print(f"  OK INTRADAY margin = qty({result.qty}) * entry(50) / lev(5) = {expected_margin} (PS2)")


def test_margin_calc_delivery_1x_leverage() -> None:
    """DELIVERY margin = qty * entry_price / 1.0 (no leverage)."""
    # Use lower entry so qty_by_capital doesn't bind too early
    # positional_avail=30k, lev=1 -> margin_per_share=100 -> qty_by_capital=300
    # qty_by_risk: 100k*1%=1000, sl_dist=10 -> 100. conc=10%->10000/100=100.
    # min(100, 300, 100)=100 -> RISK or CONCENTRATION
    sizer = _make_sizer(
        total=100_000.0, intraday_avail=70_000.0, positional_avail=30_000.0,
    )
    result = sizer.calculate("SYM", "BUY", 100.0, 90.0, "DELIVERY", score_tier="HIGH")
    assert result.success
    expected_margin = result.qty * (100.0 / 1.0)   # 1x leverage
    assert abs(result.margin_required - expected_margin) < 0.01
    print(f"  OK DELIVERY margin = qty({result.qty}) * entry(100) / lev(1) = {expected_margin} (PS2)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- SL direction sanity (PS10)
# ─────────────────────────────────────────────────────────────────────────────

def test_sl_wrong_side_buy_logs_warning() -> None:
    """BUY with sl_price > entry_price -> WARNING logged, calc proceeds."""
    mock_log = _MockLogger()
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0, logger=mock_log)
    # BUY at 40, SL at 50 (SL above entry — wrong side)
    result = sizer.calculate("SYM", "BUY", 40.0, 50.0, "INTRADAY", score_tier="HIGH")
    # Calc proceeds (abs(40-50)=10 -> same sl_distance)
    assert result.success, f"Calc should proceed despite wrong SL side: {result.reason}"
    assert len(mock_log.warnings) == 1
    assert "sl_direction_warning" in mock_log.warnings[0][0]
    print("  OK BUY sl>entry: WARNING logged, calculation proceeds (PS10)")


def test_sl_wrong_side_sell_logs_warning() -> None:
    """SELL with sl_price < entry_price -> WARNING logged, calc proceeds."""
    mock_log = _MockLogger()
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0, logger=mock_log)
    # SELL at 50, SL at 40 (SL below entry — wrong side for SELL)
    result = sizer.calculate("SYM", "SELL", 50.0, 40.0, "INTRADAY", score_tier="HIGH")
    assert result.success, f"Calc should proceed despite wrong SL side: {result.reason}"
    assert len(mock_log.warnings) == 1
    assert "sl_direction_warning" in mock_log.warnings[0][0]
    print("  OK SELL sl<entry: WARNING logged, calculation proceeds (PS10)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- PS8 validation (raises ValueError)
# ─────────────────────────────────────────────────────────────────────────────

def test_entry_price_zero_raises_valueerror() -> None:
    sizer = _make_sizer()
    raised = False
    try:
        sizer.calculate("SYM", "BUY", 0.0, 40.0, "INTRADAY")
    except ValueError:
        raised = True
    assert raised
    print("  OK entry_price=0 -> ValueError (PS8)")


def test_sl_equals_entry_returns_failure() -> None:
    """MED #8: sl_price == entry_price -> SizingResult failure, no ValueError or ZeroDivision."""
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    result = sizer.calculate("SYM", "BUY", 50.0, 50.0, "INTRADAY")
    assert not result.success, "sl==entry must produce failure result"
    assert result.constraint == "SL_DISTANCE_ZERO"
    assert "SL_DISTANCE_ZERO" in result.reason or "zero SL distance" in result.reason
    assert result.qty == 0
    print("  OK sl_price==entry_price -> SizingResult failure (MED #8, SL_DISTANCE_ZERO)")


def test_invalid_intent_raises_valueerror() -> None:
    sizer = _make_sizer()
    raised = False
    try:
        sizer.calculate("SYM", "BUY", 50.0, 40.0, "FUTURES")
    except ValueError:
        raised = True
    assert raised
    print("  OK invalid intent -> ValueError (PS8)")


def test_invalid_tier_raises_valueerror() -> None:
    sizer = _make_sizer()
    raised = False
    try:
        sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="ULTRA")
    except ValueError:
        raised = True
    assert raised
    print("  OK invalid score_tier -> ValueError (PS8)")


def test_invalid_lot_size_raises_valueerror() -> None:
    sizer = _make_sizer()
    raised = False
    try:
        sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", lot_size=0)
    except ValueError:
        raised = True
    assert raised
    print("  OK lot_size=0 -> ValueError (PS8)")


def test_invalid_side_raises_valueerror() -> None:
    sizer = _make_sizer()
    raised = False
    try:
        sizer.calculate("SYM", "LONG", 50.0, 40.0, "INTRADAY")
    except ValueError:
        raised = True
    assert raised
    print("  OK invalid side ('LONG') -> ValueError (PS8)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- determinism (PS13)
# ─────────────────────────────────────────────────────────────────────────────

def test_determinism_same_inputs_same_result() -> None:
    """Same inputs + same snapshot -> identical SizingResult (PS13)."""
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    r1 = sizer.calculate("RELIANCE", "BUY", 50.0, 40.0, "INTRADAY", score_tier="MEDIUM")
    r2 = sizer.calculate("RELIANCE", "BUY", 50.0, 40.0, "INTRADAY", score_tier="MEDIUM")
    assert r1.success == r2.success
    assert r1.qty == r2.qty
    assert r1.constraint == r2.constraint
    assert r1.margin_required == r2.margin_required
    assert r1.risk_amount == r2.risk_amount
    assert r1.breakdown == r2.breakdown
    print("  OK determinism: same inputs -> identical SizingResult (PS13)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- SizingResult structure (PS4)
# ─────────────────────────────────────────────────────────────────────────────

def test_breakdown_shows_all_three_candidates() -> None:
    """SizingResult.breakdown contains qty_by_risk, qty_by_capital, qty_by_concentration."""
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="HIGH")
    assert result.success
    bd = result.breakdown
    assert "qty_by_risk" in bd, "breakdown missing qty_by_risk"
    assert "qty_by_capital" in bd, "breakdown missing qty_by_capital"
    assert "qty_by_concentration" in bd, "breakdown missing qty_by_concentration"
    assert "tier_multiplier" in bd, "breakdown missing tier_multiplier"
    assert bd["qty_by_risk"] == 100
    assert bd["qty_by_capital"] == 7000     # 70000 / (50/5) = 7000
    assert bd["qty_by_concentration"] == 200  # 100000*10%/50 = 200
    print("  OK breakdown shows qty_by_risk=100, qty_by_capital=7000, qty_by_conc=200 (PS4)")


def test_reason_nonempty_on_success_and_failure() -> None:
    """reason field is non-empty on both success and failure (PS4)."""
    sizer = _make_sizer(total=100_000.0, intraday_avail=0.0)
    # Failure: intraday exhausted
    fail_result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="HIGH")
    assert not fail_result.success
    assert fail_result.reason != "", "reason must be non-empty on failure"

    sizer2 = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    ok_result = sizer2.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="HIGH")
    assert ok_result.success
    assert ok_result.reason != "", "reason must be non-empty on success"
    print("  OK reason field is non-empty on both success and failure (PS4)")


def test_success_implies_qty_meets_minimums() -> None:
    """success=True -> qty >= min_qty_threshold and qty >= lot_size (PS4, PS6)."""
    for lot_size in [1, 5, 10, 25]:
        sizer = _make_sizer(
            total=1_000_000.0, intraday_avail=700_000.0,
            min_qty_threshold=1,
        )
        result = sizer.calculate(
            "SYM", "BUY", 50.0, 40.0, "INTRADAY",
            score_tier="HIGH", lot_size=lot_size,
        )
        if result.success:
            assert result.qty >= 1, f"qty={result.qty} < min_qty_threshold=1"
            assert result.qty >= lot_size, f"qty={result.qty} < lot_size={lot_size}"
            assert result.qty % lot_size == 0, f"qty={result.qty} not divisible by lot_size={lot_size}"
    print("  OK success=True implies qty >= min_qty_threshold and lot_size-aligned (PS4, PS6)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- risk_amount field (PS4)
# ─────────────────────────────────────────────────────────────────────────────

def test_risk_amount_equals_qty_times_sl_distance() -> None:
    """risk_amount = final_qty * abs(entry_price - sl_price)."""
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    entry, sl = 50.0, 40.0
    result = sizer.calculate("SYM", "BUY", entry, sl, "INTRADAY", score_tier="HIGH")
    assert result.success
    expected_risk = result.qty * abs(entry - sl)
    assert abs(result.risk_amount - expected_risk) < 0.01
    print(f"  OK risk_amount = qty({result.qty}) * sl_dist({abs(entry-sl)}) = {expected_risk} (PS4)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- SizingResult is frozen dataclass (PS4)
# ─────────────────────────────────────────────────────────────────────────────

def test_sizing_result_is_frozen() -> None:
    """SizingResult is frozen dataclass -- cannot be mutated."""
    sizer = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    result = sizer.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY")
    raised = False
    try:
        result.qty = 999    # type: ignore[misc]
    except Exception:
        raised = True
    assert raised, "SizingResult must be frozen (immutable)"
    print("  OK SizingResult is frozen (immutable dataclass) (PS4)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- SL distance uses abs() regardless of side (PS2, PS10)
# ─────────────────────────────────────────────────────────────────────────────

def test_sl_distance_uses_abs_regardless_of_side() -> None:
    """
    Wrong SL side for BUY (sl=60 > entry=50) -> sl_dist = abs(50-60) = 10.
    Same qty as correct-side SL of 40 (dist=10).
    """
    sizer_correct = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    sizer_wrong = _make_sizer(total=100_000.0, intraday_avail=70_000.0)
    r_correct = sizer_correct.calculate("SYM", "BUY", 50.0, 40.0, "INTRADAY", score_tier="HIGH")
    r_wrong = sizer_wrong.calculate("SYM", "BUY", 50.0, 60.0, "INTRADAY", score_tier="HIGH")
    # Both have sl_dist=10 -> same qty_by_risk
    assert r_correct.success and r_wrong.success
    assert r_correct.qty == r_wrong.qty, "abs(sl_dist) must produce same qty regardless of direction"
    print("  OK sl_distance = abs(entry-sl) regardless of side (PS2, PS10)")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_risk_bound_qty,
        test_capital_bound_qty,
        test_concentration_bound_qty,
        test_tier_high_full_size,
        test_tier_medium_reduces_qty,
        test_tier_low_reduces_qty_audit_regression,
        test_lot_size_rounding_snaps_down,
        test_lot_size_larger_than_computed_qty_fails,
        test_fix021_high_skew_rejected,
        test_fix021_acceptable_skew_proceeds,
        test_fix021_lot_size_one_never_rejected,
        test_fix021_threshold_configurable,
        test_intraday_uses_intraday_bucket,
        test_delivery_uses_positional_bucket,
        test_intraday_exhausted_positional_has_cash_fails,
        test_margin_calc_intraday_5x_leverage,
        test_margin_calc_delivery_1x_leverage,
        test_sl_wrong_side_buy_logs_warning,
        test_sl_wrong_side_sell_logs_warning,
        test_entry_price_zero_raises_valueerror,
        test_sl_equals_entry_returns_failure,
        test_invalid_intent_raises_valueerror,
        test_invalid_tier_raises_valueerror,
        test_invalid_lot_size_raises_valueerror,
        test_invalid_side_raises_valueerror,
        test_determinism_same_inputs_same_result,
        test_breakdown_shows_all_three_candidates,
        test_reason_nonempty_on_success_and_failure,
        test_success_implies_qty_meets_minimums,
        test_risk_amount_equals_qty_times_sl_distance,
        test_sizing_result_is_frozen,
        test_sl_distance_uses_abs_regardless_of_side,
    ]

    print("=" * 70)
    print("position_sizer.py -- Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as e:
            failed.append((test.__name__, f"AssertionError: {e}"))
            print(f"  FAIL: {e}")
        except Exception as e:
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR: {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1

    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
