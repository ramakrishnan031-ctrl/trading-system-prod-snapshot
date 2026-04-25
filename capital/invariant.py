"""
capital/invariant.py -- Trading System v2

Purpose:
    Pure stateless assertion function that verifies capital balances satisfy
    the G3 Level 1 invariant at the moment of call. Extracted from
    fund_manager.py FM11 to create a single, reusable, testable anchor.

Locked Design Decisions:
    INV1 -- Pure stateless assertion. Raises CapitalInvariantViolation on
            breach. Returns None on success.
    INV2 -- Invariant equation (G3, P7a):
              margin_available + margin_reserved + margin_used
                == cash_floor + min(0, realized_pnl_today)
            Tolerance: 0.01 (1 paise). Profits ignored until T+1 settlement.
    INV3 -- Single public function: assert_capital_invariant().
    INV4 -- CapitalInvariantViolation context dict has exactly 12 fields:
            bucket, mutation_type, reservation_id, margin_available,
            margin_reserved, margin_used, cash_floor, realized_pnl_today,
            lhs, rhs, delta, tolerance.
    INV5 -- Three exported helpers: compute_lhs, compute_rhs,
            compute_tradable_balance.
    INV6 -- Negative balance guards: available, reserved, used, cash_floor
            must each be >= -tolerance. realized_pnl_today may be negative.
    INV7 -- Float-Drift Fix Option B (Audit 2.2, locked 2026-04-24): both
            sides of the invariant comparison are rounded to 2 dp (paise)
            BEFORE comparing to tolerance. IEEE-754 arithmetic accumulates
            ~1e-14 error per op; over hundreds of trades that drift can
            cross the 0.01 tolerance window even when the ledger is logically
            consistent. Rounding to paise (the actual unit in which broker
            reports money) eliminates this drift without converting the
            ledger to Decimal end-to-end. Negative-balance guards round the
            field value too, so a -1e-15 spurious negative is treated as 0.
    INV8 -- Layer 3 (capital/). Imports: stdlib + core.exceptions only.
    INV9 -- Deterministic, thread-safe by virtue of being pure (no shared
            state, no I/O).

What This Module Does NOT Do:
    - Does not log (caller logs after catching per INV10)
    - Does not recover from violations (caller's responsibility)
    - Does not detect broker drift (G1 reconciler -- separate module)
    - Does not persist state
    - Does not import state_store, logger, or config_loader
"""
from __future__ import annotations

from core.exceptions import CapitalInvariantViolation

_DEFAULT_TOLERANCE: float = 0.01  # 1 paise (INV2)


# ------------------------------------------------------------------------------
# Helper functions (INV5)
# ------------------------------------------------------------------------------

def compute_lhs(
    margin_available: float,
    margin_reserved: float,
    margin_used: float,
) -> float:
    """
    Left-hand side of the G3 invariant: sum of the three capital partitions.

    Args:
        margin_available: available margin (not reserved or in use)
        margin_reserved:  capital reserved but not yet deployed
        margin_used:      capital in active positions (deployed)

    Returns:
        margin_available + margin_reserved + margin_used
    """
    return margin_available + margin_reserved + margin_used


def compute_rhs(cash_floor: float, realized_pnl_today: float) -> float:
    """
    Right-hand side of the G3 invariant (INV2, P7a).

    Losses count immediately; profits are ignored until T+1 settlement.
    Equation: cash_floor + min(0, realized_pnl_today)

    Args:
        cash_floor:          broker-settled base capital
        realized_pnl_today:  today's net realized P&L (may be negative)

    Returns:
        cash_floor + min(0, realized_pnl_today)
    """
    return cash_floor + min(0.0, realized_pnl_today)


def compute_tradable_balance(cash_floor: float, realized_pnl_today: float) -> float:
    """
    How much capital is available for new trades per P7a.

    Identical to compute_rhs: tradable = cash_floor + min(0, realized_pnl_today).
    Losses reduce tradable immediately; profits do not increase it until T+1.

    Args:
        cash_floor:          broker-settled base capital
        realized_pnl_today:  today's net realized P&L (may be negative)

    Returns:
        cash_floor + min(0, realized_pnl_today)
    """
    return compute_rhs(cash_floor, realized_pnl_today)


# ------------------------------------------------------------------------------
# Public assertion API (INV3)
# ------------------------------------------------------------------------------

def assert_capital_invariant(
    margin_available: float,
    margin_reserved: float,
    margin_used: float,
    cash_floor: float,
    realized_pnl_today: float,
    bucket: str = "global",
    mutation_type: str = "",
    reservation_id: str = "",
    tolerance: float = _DEFAULT_TOLERANCE,
) -> None:
    """
    Assert that capital balances satisfy the G3 Level 1 invariant.

    Negative balance guards (INV6) run first: available, reserved, used, and
    cash_floor must each be >= -tolerance. realized_pnl_today may be negative.

    Main invariant (INV2, P7a):
        margin_available + margin_reserved + margin_used
            == cash_floor + min(0, realized_pnl_today)

    Args:
        margin_available:    available margin (not reserved or in use)
        margin_reserved:     capital reserved but not yet deployed
        margin_used:         capital in active positions
        cash_floor:          broker-settled base capital (must be >= -tolerance)
        realized_pnl_today:  today's net realized P&L (may be negative; losses
                             reduce the invariant RHS immediately per P7a)
        bucket:              "intraday", "positional", or "global" (context only)
        mutation_type:       e.g. "reserve", "release" (context only)
        reservation_id:      reservation being acted on (context only)
        tolerance:           acceptable floating-point delta (default 0.01)

    Returns:
        None on success.

    Raises:
        CapitalInvariantViolation: if any negative guard fails, or if
            abs(lhs - rhs) > tolerance. Exception context contains all
            12 INV4 fields.
    """
    # -- INV6: negative balance guards -----------------------------------------
    _guard_non_negative(
        "margin_available", margin_available,
        bucket, mutation_type, reservation_id,
        margin_available, margin_reserved, margin_used,
        cash_floor, realized_pnl_today, tolerance,
    )
    _guard_non_negative(
        "margin_reserved", margin_reserved,
        bucket, mutation_type, reservation_id,
        margin_available, margin_reserved, margin_used,
        cash_floor, realized_pnl_today, tolerance,
    )
    _guard_non_negative(
        "margin_used", margin_used,
        bucket, mutation_type, reservation_id,
        margin_available, margin_reserved, margin_used,
        cash_floor, realized_pnl_today, tolerance,
    )
    _guard_non_negative(
        "cash_floor", cash_floor,
        bucket, mutation_type, reservation_id,
        margin_available, margin_reserved, margin_used,
        cash_floor, realized_pnl_today, tolerance,
    )

    # -- INV2 + INV7: main invariant check (round to paise before compare) -----
    # Float-drift fix Option B: round to 2 dp so accumulated IEEE-754 noise
    # below 1 paise cannot push abs(delta) past tolerance. Money is paise-
    # quantised at the broker; sub-paise differences are arithmetic noise,
    # not real discrepancies.
    lhs = round(compute_lhs(margin_available, margin_reserved, margin_used), 2)
    rhs = round(compute_rhs(cash_floor, realized_pnl_today), 2)
    delta = lhs - rhs

    if abs(delta) > tolerance:
        raise CapitalInvariantViolation(
            f"Capital invariant violated after {mutation_type!r}: "
            f"lhs={lhs:.4f} rhs={rhs:.4f} delta={delta:.4f}",
            bucket=bucket,
            mutation_type=mutation_type,
            reservation_id=reservation_id,
            margin_available=margin_available,
            margin_reserved=margin_reserved,
            margin_used=margin_used,
            cash_floor=cash_floor,
            realized_pnl_today=realized_pnl_today,
            lhs=lhs,
            rhs=rhs,
            delta=delta,
            tolerance=tolerance,
        )


# ------------------------------------------------------------------------------
# Internal helper
# ------------------------------------------------------------------------------

def _guard_non_negative(
    field_name: str,
    value: float,
    bucket: str,
    original_mutation_type: str,
    reservation_id: str,
    margin_available: float,
    margin_reserved: float,
    margin_used: float,
    cash_floor: float,
    realized_pnl_today: float,
    tolerance: float,
) -> None:
    """Raise CapitalInvariantViolation if value < -tolerance (INV6 + INV7).

    INV7: round value to 2 dp before the negative check. A spurious
    -1e-15 from arithmetic noise is logically zero; rounding to paise
    avoids false NEGATIVE_* violations on otherwise-consistent ledgers.
    """
    if round(value, 2) >= -tolerance:
        return
    violation = f"NEGATIVE_{field_name.upper()}"
    lhs = compute_lhs(margin_available, margin_reserved, margin_used)
    rhs = compute_rhs(cash_floor, realized_pnl_today)
    raise CapitalInvariantViolation(
        f"Capital balance violation: {violation}: {value:.4f}",
        bucket=bucket,
        mutation_type=violation,
        reservation_id=reservation_id,
        margin_available=margin_available,
        margin_reserved=margin_reserved,
        margin_used=margin_used,
        cash_floor=cash_floor,
        realized_pnl_today=realized_pnl_today,
        lhs=lhs,
        rhs=rhs,
        delta=lhs - rhs,
        tolerance=tolerance,
    )
