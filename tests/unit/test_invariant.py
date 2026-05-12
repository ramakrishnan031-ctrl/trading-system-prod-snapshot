"""
tests/unit/test_invariant.py

Validates capital/invariant.py against INV1-INV10 locked decisions:
  - Valid balanced inputs -> no exception (INV1, INV2)
  - Realized profit ignored (P7a): avail+res+used == cash_floor, not cash+profit
  - Realized loss counts: avail+res+used must == cash_floor - abs(loss)
  - Off-by-paise (delta=0.005 < 0.01) -> pass; off-by-rupee -> raise (INV2)
  - Negative balance guards: available, reserved, used, cash_floor (INV6)
  - Negative realized_pnl_today allowed (losses are valid P7a)
  - Custom tolerance respected (INV3)
  - CapitalInvariantViolation context dict has all 12 INV4 fields
  - lhs, rhs, delta computed correctly in context
  - compute_lhs, compute_rhs, compute_tradable_balance helpers (INV5)
  - CapitalInvariantViolation SEVERITY == 'CRITICAL' (E2, E4)
  - CapitalInvariantViolation isa StateError isa TradingSystemError (E3, E4)
  - Pure: same inputs -> same outcome every time (INV9)
  - Thread-safe: concurrent calls with no races (INV9)
  - Importable as: from capital.invariant import assert_capital_invariant

Run: python -m pytest tests/unit/test_invariant.py -v
Or:  python tests/unit/test_invariant.py  (standalone mode)
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from capital.invariant import (
    assert_capital_invariant,
    compute_lhs,
    compute_rhs,
    compute_tradable_balance,
)
from core.exceptions import CapitalInvariantViolation, StateError, TradingSystemError


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def _assert_raises(
    expected_exc: type,
    *args,
    **kwargs,
) -> CapitalInvariantViolation:
    """Call assert_capital_invariant, expect a CapitalInvariantViolation."""
    try:
        assert_capital_invariant(*args, **kwargs)
    except CapitalInvariantViolation as exc:
        return exc
    raise AssertionError(
        f"Expected {expected_exc.__name__} but no exception was raised"
    )


# ------------------------------------------------------------------------------
# Tests -- valid balanced inputs (INV1, INV2)
# ------------------------------------------------------------------------------

def test_valid_balances_no_pnl() -> None:
    """Zero P&L: available + reserved + used == cash_floor. No exception."""
    assert_capital_invariant(
        margin_available=700.0,
        margin_reserved=200.0,
        margin_used=100.0,
        cash_floor=1000.0,
        realized_pnl_today=0.0,
    )
    print("  OK valid balances (no P&L): 700+200+100 == 1000 + min(0,0)")


def test_valid_balances_with_realized_loss() -> None:
    """Loss reduces tradable: avail+res+used == cash_floor - abs(loss). No exception."""
    # cash=1000, loss=-200: rhs = 1000 + min(0,-200) = 800
    assert_capital_invariant(
        margin_available=600.0,
        margin_reserved=100.0,
        margin_used=100.0,
        cash_floor=1000.0,
        realized_pnl_today=-200.0,
    )
    print("  OK valid balances with loss: 800 == 1000 + min(0,-200)")


def test_valid_balances_realized_profit_ignored() -> None:
    """
    Profit IGNORED per P7a: T+1 settlement.
    RHS = cash_floor + min(0, +500) = cash_floor + 0 = cash_floor.
    LHS must equal cash_floor, NOT cash_floor + profit.
    """
    # cash=1000, profit=+500: rhs = 1000 + 0 = 1000; LHS must be 1000
    assert_capital_invariant(
        margin_available=700.0,
        margin_reserved=200.0,
        margin_used=100.0,   # avail+res+used = 1000 == cash_floor
        cash_floor=1000.0,
        realized_pnl_today=500.0,
    )
    print("  OK profit ignored (P7a): LHS=1000 == cash_floor=1000, profit not counted")


def test_valid_zero_balances() -> None:
    """All zeros is valid (empty state)."""
    assert_capital_invariant(
        margin_available=0.0,
        margin_reserved=0.0,
        margin_used=0.0,
        cash_floor=0.0,
        realized_pnl_today=0.0,
    )
    print("  OK all-zero balances are valid")


# ------------------------------------------------------------------------------
# Tests -- tolerance boundary (INV2)
# ------------------------------------------------------------------------------

def test_off_by_paise_passes() -> None:
    """delta=0.005 < default tolerance=0.01 -> no exception."""
    # LHS = 1000.005, RHS = 1000.0, delta = 0.005 < 0.01
    assert_capital_invariant(
        margin_available=700.005,
        margin_reserved=200.0,
        margin_used=100.0,
        cash_floor=1000.0,
        realized_pnl_today=0.0,
    )
    print("  OK off-by-paise (delta=0.005) within tolerance -> no exception")


def test_off_by_rupee_raises() -> None:
    """delta=1.0 > default tolerance=0.01 -> CapitalInvariantViolation."""
    exc = _assert_raises(
        CapitalInvariantViolation,
        margin_available=701.0,
        margin_reserved=200.0,
        margin_used=100.0,
        cash_floor=1000.0,
        realized_pnl_today=0.0,
    )
    assert abs(exc.context["delta"] - 1.0) < 0.001, f"delta should be ~1.0, got {exc.context['delta']}"
    print(f"  OK off-by-rupee (delta=1.0) raises CapitalInvariantViolation")


# ------------------------------------------------------------------------------
# Tests -- negative balance guards (INV6)
# ------------------------------------------------------------------------------

def test_negative_available_raises() -> None:
    """margin_available < -tolerance -> NEGATIVE_MARGIN_AVAILABLE violation."""
    exc = _assert_raises(
        CapitalInvariantViolation,
        margin_available=-1.0,
        margin_reserved=0.0,
        margin_used=0.0,
        cash_floor=0.0,
        realized_pnl_today=0.0,
    )
    assert "NEGATIVE_MARGIN_AVAILABLE" in exc.context.get("mutation_type", ""), (
        f"Expected NEGATIVE_MARGIN_AVAILABLE in mutation_type, got {exc.context.get('mutation_type')}"
    )
    print("  OK negative margin_available raises (INV6)")


def test_negative_reserved_raises() -> None:
    """margin_reserved < -tolerance -> NEGATIVE_MARGIN_RESERVED violation."""
    exc = _assert_raises(
        CapitalInvariantViolation,
        margin_available=100.0,
        margin_reserved=-0.5,
        margin_used=0.0,
        cash_floor=100.0,
        realized_pnl_today=0.0,
    )
    assert "NEGATIVE_MARGIN_RESERVED" in exc.context.get("mutation_type", ""), (
        f"Expected NEGATIVE_MARGIN_RESERVED, got {exc.context.get('mutation_type')}"
    )
    print("  OK negative margin_reserved raises (INV6)")


def test_negative_used_raises() -> None:
    """margin_used < -tolerance -> NEGATIVE_MARGIN_USED violation."""
    exc = _assert_raises(
        CapitalInvariantViolation,
        margin_available=100.0,
        margin_reserved=0.0,
        margin_used=-2.0,
        cash_floor=100.0,
        realized_pnl_today=0.0,
    )
    assert "NEGATIVE_MARGIN_USED" in exc.context.get("mutation_type", ""), (
        f"Expected NEGATIVE_MARGIN_USED, got {exc.context.get('mutation_type')}"
    )
    print("  OK negative margin_used raises (INV6)")


def test_negative_cash_floor_raises() -> None:
    """cash_floor < -tolerance -> NEGATIVE_CASH_FLOOR violation."""
    exc = _assert_raises(
        CapitalInvariantViolation,
        margin_available=0.0,
        margin_reserved=0.0,
        margin_used=0.0,
        cash_floor=-100.0,
        realized_pnl_today=0.0,
    )
    assert "NEGATIVE_CASH_FLOOR" in exc.context.get("mutation_type", ""), (
        f"Expected NEGATIVE_CASH_FLOOR, got {exc.context.get('mutation_type')}"
    )
    print("  OK negative cash_floor raises (INV6)")


def test_negative_realized_pnl_allowed() -> None:
    """realized_pnl_today may be negative (losses are valid per P7a). No guard."""
    # cash=1000, loss=-500: rhs = 500; LHS must be 500
    assert_capital_invariant(
        margin_available=300.0,
        margin_reserved=100.0,
        margin_used=100.0,
        cash_floor=1000.0,
        realized_pnl_today=-500.0,
    )
    print("  OK negative realized_pnl_today allowed (P7a losses are valid)")


def test_negative_guard_within_tolerance_allowed() -> None:
    """Values in range [-tolerance, 0) are allowed (floating-point noise)."""
    # margin_available = -0.005 (within tolerance of 0.01)
    # cash_floor=1000, rhs=1000; lhs = -0.005+0+0 = -0.005; delta=-1000.005 (will fail main check)
    # The guard itself won't fire since -0.005 >= -0.01
    # But main check will fail since delta is huge -- that's expected behavior
    # Just verify the GUARD doesn't fire
    guard_fired = False
    try:
        assert_capital_invariant(
            margin_available=-0.005,  # within tolerance, guard does NOT fire
            margin_reserved=0.0,
            margin_used=0.0,
            cash_floor=0.0,          # rhs=0, lhs=-0.005, delta=-0.005 within tolerance
            realized_pnl_today=0.0,
            tolerance=0.01,
        )
    except CapitalInvariantViolation as exc:
        # If it raises, it must be the MAIN check (delta), not the negative guard
        mt = exc.context.get("mutation_type", "")
        guard_fired = "NEGATIVE" in mt
    assert not guard_fired, "Negative guard fired for value within tolerance"
    print("  OK negative guard does not fire for values within tolerance [-tol, 0)")


# ------------------------------------------------------------------------------
# Tests -- custom tolerance (INV3)
# ------------------------------------------------------------------------------

def test_custom_tolerance_passes() -> None:
    """delta=0.10 with tolerance=0.50 -> no exception."""
    assert_capital_invariant(
        margin_available=700.10,
        margin_reserved=200.0,
        margin_used=100.0,
        cash_floor=1000.0,
        realized_pnl_today=0.0,
        tolerance=0.50,
    )
    print("  OK custom tolerance=0.50 passes for delta=0.10")


def test_custom_tolerance_raises() -> None:
    """delta=0.10 with tolerance=0.05 -> raises."""
    _assert_raises(
        CapitalInvariantViolation,
        margin_available=700.10,
        margin_reserved=200.0,
        margin_used=100.0,
        cash_floor=1000.0,
        realized_pnl_today=0.0,
        tolerance=0.05,
    )
    print("  OK custom tolerance=0.05 raises for delta=0.10")


# ------------------------------------------------------------------------------
# Tests -- context dict (INV4)
# ------------------------------------------------------------------------------

def test_context_dict_has_all_12_fields() -> None:
    """CapitalInvariantViolation context contains all 12 locked INV4 fields."""
    exc = _assert_raises(
        CapitalInvariantViolation,
        margin_available=900.0,
        margin_reserved=200.0,
        margin_used=100.0,  # LHS=1200 vs RHS=1000 -> delta=200
        cash_floor=1000.0,
        realized_pnl_today=0.0,
        bucket="intraday",
        mutation_type="reserve",
        reservation_id="res_abc123",
    )
    required_keys = {
        "bucket", "mutation_type", "reservation_id",
        "margin_available", "margin_reserved", "margin_used",
        "cash_floor", "realized_pnl_today",
        "lhs", "rhs", "delta", "tolerance",
    }
    missing = required_keys - set(exc.context.keys())
    assert not missing, f"Missing INV4 context keys: {missing}"
    print(f"  OK context dict has all 12 INV4 fields: {sorted(exc.context.keys())}")


def test_context_lhs_rhs_delta_correct() -> None:
    """lhs, rhs, delta in context are computed correctly."""
    exc = _assert_raises(
        CapitalInvariantViolation,
        margin_available=600.0,
        margin_reserved=100.0,
        margin_used=100.0,   # LHS=800
        cash_floor=1000.0,
        realized_pnl_today=0.0,  # RHS=1000; delta=800-1000=-200
    )
    assert abs(exc.context["lhs"] - 800.0) < 0.001, f"lhs wrong: {exc.context['lhs']}"
    assert abs(exc.context["rhs"] - 1000.0) < 0.001, f"rhs wrong: {exc.context['rhs']}"
    assert abs(exc.context["delta"] - (-200.0)) < 0.001, f"delta wrong: {exc.context['delta']}"
    print(f"  OK lhs={exc.context['lhs']}, rhs={exc.context['rhs']}, delta={exc.context['delta']}")


def test_context_caller_fields_preserved() -> None:
    """bucket, mutation_type, reservation_id from caller are in context."""
    exc = _assert_raises(
        CapitalInvariantViolation,
        margin_available=999.0,
        margin_reserved=0.0,
        margin_used=0.0,   # LHS=999 != RHS=500
        cash_floor=500.0,
        realized_pnl_today=0.0,
        bucket="positional",
        mutation_type="commit",
        reservation_id="res_xyz789",
    )
    assert exc.context["bucket"] == "positional"
    assert exc.context["mutation_type"] == "commit"
    assert exc.context["reservation_id"] == "res_xyz789"
    print("  OK bucket, mutation_type, reservation_id preserved in context")


# ------------------------------------------------------------------------------
# Tests -- compute_lhs, compute_rhs, compute_tradable_balance (INV5)
# ------------------------------------------------------------------------------

def test_compute_lhs_sums_correctly() -> None:
    """compute_lhs returns margin_available + margin_reserved + margin_used."""
    assert compute_lhs(700.0, 200.0, 100.0) == 1000.0
    assert compute_lhs(0.0, 0.0, 0.0) == 0.0
    assert abs(compute_lhs(333.33, 333.33, 333.34) - 1000.0) < 0.001
    print("  OK compute_lhs sums all three partitions correctly")


def test_compute_rhs_profit_case() -> None:
    """Profit: min(0, +500) = 0 -> rhs = cash_floor."""
    rhs = compute_rhs(cash_floor=1000.0, realized_pnl_today=500.0)
    assert abs(rhs - 1000.0) < 0.001, f"Expected 1000.0, got {rhs}"
    print(f"  OK compute_rhs profit case: rhs={rhs} (profit not counted, P7a)")


def test_compute_rhs_loss_case() -> None:
    """Loss: min(0, -300) = -300 -> rhs = cash_floor - 300."""
    rhs = compute_rhs(cash_floor=1000.0, realized_pnl_today=-300.0)
    assert abs(rhs - 700.0) < 0.001, f"Expected 700.0, got {rhs}"
    print(f"  OK compute_rhs loss case: rhs={rhs}")


def test_compute_rhs_zero_pnl() -> None:
    """Zero P&L: min(0, 0) = 0 -> rhs = cash_floor."""
    rhs = compute_rhs(cash_floor=1000.0, realized_pnl_today=0.0)
    assert abs(rhs - 1000.0) < 0.001, f"Expected 1000.0, got {rhs}"
    print(f"  OK compute_rhs zero P&L: rhs={rhs}")


def test_compute_tradable_balance_matches_compute_rhs() -> None:
    """compute_tradable_balance must return the same value as compute_rhs."""
    for cash, pnl in [(1000.0, 500.0), (1000.0, -300.0), (500.0, 0.0), (0.0, -100.0)]:
        tb = compute_tradable_balance(cash, pnl)
        rhs = compute_rhs(cash, pnl)
        assert abs(tb - rhs) < 0.0001, (
            f"Mismatch for cash={cash}, pnl={pnl}: tradable={tb}, rhs={rhs}"
        )
    print("  OK compute_tradable_balance == compute_rhs for all test cases (INV5)")


# ------------------------------------------------------------------------------
# Tests -- exception hierarchy (E2, E3, E4)
# ------------------------------------------------------------------------------

def test_severity_is_critical() -> None:
    """CapitalInvariantViolation.SEVERITY == 'CRITICAL' (E2, E4)."""
    assert CapitalInvariantViolation.SEVERITY == "CRITICAL", (
        f"Expected SEVERITY='CRITICAL', got {CapitalInvariantViolation.SEVERITY!r}"
    )
    print("  OK CapitalInvariantViolation.SEVERITY == 'CRITICAL' (E2, E4)")


def test_isa_state_error() -> None:
    """CapitalInvariantViolation isa StateError (E3, E4)."""
    assert issubclass(CapitalInvariantViolation, StateError), (
        "CapitalInvariantViolation must be a subclass of StateError"
    )
    print("  OK CapitalInvariantViolation isa StateError (E3, E4)")


def test_isa_trading_system_error() -> None:
    """CapitalInvariantViolation isa TradingSystemError (E1, E3)."""
    assert issubclass(CapitalInvariantViolation, TradingSystemError), (
        "CapitalInvariantViolation must be a subclass of TradingSystemError"
    )
    print("  OK CapitalInvariantViolation isa TradingSystemError (E1, E3)")


# ------------------------------------------------------------------------------
# Tests -- purity and thread safety (INV9)
# ------------------------------------------------------------------------------

def test_pure_same_inputs_same_result() -> None:
    """Same inputs called 100 times -> identical outcome (INV9)."""
    outcomes = []
    for _ in range(100):
        raised = False
        try:
            assert_capital_invariant(
                margin_available=700.0, margin_reserved=200.0, margin_used=100.0,
                cash_floor=1000.0, realized_pnl_today=0.0,
            )
        except CapitalInvariantViolation:
            raised = True
        outcomes.append(raised)
    assert len(set(outcomes)) == 1, f"Non-deterministic result: {set(outcomes)}"
    assert outcomes[0] is False, "Should not raise for valid inputs"

    # Also verify the failing path is deterministic
    fail_outcomes = []
    for _ in range(100):
        raised = False
        try:
            assert_capital_invariant(
                margin_available=999.0, margin_reserved=0.0, margin_used=0.0,
                cash_floor=500.0, realized_pnl_today=0.0,
            )
        except CapitalInvariantViolation:
            raised = True
        fail_outcomes.append(raised)
    assert all(fail_outcomes), "Should always raise for invalid inputs"
    print("  OK same inputs -> same outcome across 100 calls (INV9 purity)")


def test_thread_safe_50_concurrent_calls() -> None:
    """5 threads x 10 calls each -> no races, no corruption (INV9)."""
    errors: list[Exception] = []
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(10):
            try:
                # Valid call -- should not raise
                assert_capital_invariant(
                    margin_available=700.0, margin_reserved=200.0, margin_used=100.0,
                    cash_floor=1000.0, realized_pnl_today=0.0,
                )
                with lock:
                    results.append(True)
                # Invalid call -- should always raise
                try:
                    assert_capital_invariant(
                        margin_available=999.0, margin_reserved=0.0, margin_used=0.0,
                        cash_floor=500.0, realized_pnl_today=0.0,
                    )
                    with lock:
                        errors.append(AssertionError("Expected raise but got None"))
                except CapitalInvariantViolation:
                    with lock:
                        results.append(False)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"
    assert len(results) == 100, f"Expected 100 results (50 valid + 50 invalid), got {len(results)}"
    valid_count = sum(1 for r in results if r is True)
    invalid_count = sum(1 for r in results if r is False)
    assert valid_count == 50, f"Expected 50 valid, got {valid_count}"
    assert invalid_count == 50, f"Expected 50 invalid, got {invalid_count}"
    print(f"  OK 5 threads x 10 pairs = 100 calls: {valid_count} valid, {invalid_count} invalid, no races (INV9)")


# ------------------------------------------------------------------------------
# Tests -- importability (INV8)
# ------------------------------------------------------------------------------

def test_importable_from_capital_invariant() -> None:
    """from capital.invariant import assert_capital_invariant must work (INV8)."""
    from capital.invariant import assert_capital_invariant as fn  # noqa: PLC0415
    assert callable(fn)
    print("  OK from capital.invariant import assert_capital_invariant works (INV8)")


# ------------------------------------------------------------------------------
# INV7 / Audit 2.2 -- Float-drift Option B
# ------------------------------------------------------------------------------

def test_inv7_sub_paise_drift_does_not_raise() -> None:
    """
    INV7: an accumulated IEEE-754 drift below 0.5 paise (rounds to 0)
    must NOT raise. Pre-fix, lhs - rhs = 1e-13 would still pass tolerance,
    but the simulated post-many-ops drift below explicitly demonstrates the
    OPT-B comparison: round-to-paise on each side then compare.
    """
    # Construct a drift in the noise floor; round() collapses both sides
    # to the same paise, so abs(delta) == 0.
    drift = sum(0.1 for _ in range(10)) - 1.0  # classic 0.1 + 0.1 ... noise
    # drift is ~1.1e-16, below 0.005, so rounds to 0.
    assert_capital_invariant(
        margin_available=100_000.00 + drift,
        margin_reserved=0.0,
        margin_used=0.0,
        cash_floor=100_000.00,
        realized_pnl_today=0.0,
    )
    print(f"  OK INV7: sub-paise drift {drift!r} ignored (Audit 2.2 / Option B)")


def test_inv7_half_paise_drift_passes() -> None:
    """
    INV7: a 0.004 (less than half-paise) drift on either side rounds away
    and passes. This is the critical post-Option-B behaviour: pre-fix the
    raw subtraction left abs(delta)=0.004 inside tolerance, but a series
    of such drifts could compound to 0.011 and breach. Post-fix each side
    is independently rounded to paise so the drift cannot accumulate.
    """
    assert_capital_invariant(
        margin_available=99_999.996,   # rounds to 100_000.00
        margin_reserved=0.0,
        margin_used=0.0,
        cash_floor=100_000.00,
        realized_pnl_today=0.0,
    )
    print("  OK INV7: half-paise drift rounds away (Audit 2.2 / Option B)")


def test_inv7_rupee_breach_still_raises() -> None:
    """
    INV7 regression: a real 1-rupee discrepancy MUST still raise. Rounding
    to paise must not weaken detection of genuine accounting errors.
    """
    try:
        assert_capital_invariant(
            margin_available=99_999.00,    # 1 rupee short
            margin_reserved=0.0,
            margin_used=0.0,
            cash_floor=100_000.00,
            realized_pnl_today=0.0,
        )
    except CapitalInvariantViolation as exc:
        # Both sides rounded; delta should be exactly -1.00 (no float noise)
        assert abs(exc.context["delta"] + 1.0) < 1e-9, (
            f"expected delta == -1.00 after rounding; got {exc.context['delta']}"
        )
        print(
            f"  OK INV7: 1-rupee breach still raises with delta={exc.context['delta']:.2f} "
            "(Audit 2.2 regression guard)"
        )
        return
    raise AssertionError("expected CapitalInvariantViolation for 1-rupee shortfall")


def test_inv7_negative_guard_ignores_sub_paise_negative() -> None:
    """
    INV7: a -1e-15 spurious negative on margin_available (arithmetic noise)
    must NOT trigger NEGATIVE_MARGIN_AVAILABLE. The guard now rounds to
    paise before the negative check so logical zero is treated as zero.
    """
    spurious_negative = -1e-15
    assert_capital_invariant(
        margin_available=spurious_negative,
        margin_reserved=0.0,
        margin_used=100_000.00,
        cash_floor=100_000.00,
        realized_pnl_today=0.0,
    )
    print("  OK INV7: spurious -1e-15 on margin_available no longer trips NEGATIVE_* (Audit 2.2)")


# ------------------------------------------------------------------------------
# FIX-005: _guard_non_negative rounding bug
# ------------------------------------------------------------------------------

def test_fix005_minus_0_014_raises_with_tolerance_0_01() -> None:
    """
    FIX-005 regression: value=-0.014, tolerance=0.01 must RAISE.

    Old code: round(-0.014, 2) == -0.01, and -0.01 >= -0.01 is True → silently
    passes a genuinely-negative value. Fixed by removing rounding from guard.
    """
    from capital.invariant import CapitalInvariantViolation, _guard_non_negative

    raised = False
    try:
        _guard_non_negative(
            field_name="margin_available",
            value=-0.014,
            bucket="INTRADAY",
            original_mutation_type="RELEASE",
            reservation_id=None,
            margin_available=-0.014,
            margin_reserved=0.0,
            margin_used=0.0,
            cash_floor=0.0,
            realized_pnl_today=0.0,
            tolerance=0.01,
        )
    except CapitalInvariantViolation:
        raised = True
    assert raised, "FIX-005: -0.014 with tolerance=0.01 must raise CapitalInvariantViolation"
    print("  OK FIX-005: -0.014 raises (was silently passing due to rounding bug)")


def test_fix005_minus_0_009_passes_with_tolerance_0_01() -> None:
    """
    FIX-005: value=-0.009, tolerance=0.01 must PASS (within tolerance).

    -0.009 >= -0.01 is True → should not raise.
    """
    from capital.invariant import _guard_non_negative

    _guard_non_negative(
        field_name="margin_available",
        value=-0.009,
        bucket="INTRADAY",
        original_mutation_type="RELEASE",
        reservation_id=None,
        margin_available=-0.009,
        margin_reserved=0.0,
        margin_used=0.0,
        cash_floor=0.0,
        realized_pnl_today=0.0,
        tolerance=0.01,
    )
    print("  OK FIX-005: -0.009 passes with tolerance=0.01 (within tolerance)")


# ------------------------------------------------------------------------------
# Standalone runner
# ------------------------------------------------------------------------------

def run_all_tests() -> int:
    tests = [
        test_valid_balances_no_pnl,
        test_valid_balances_with_realized_loss,
        test_valid_balances_realized_profit_ignored,
        test_valid_zero_balances,
        test_off_by_paise_passes,
        test_off_by_rupee_raises,
        test_negative_available_raises,
        test_negative_reserved_raises,
        test_negative_used_raises,
        test_negative_cash_floor_raises,
        test_negative_realized_pnl_allowed,
        test_negative_guard_within_tolerance_allowed,
        test_custom_tolerance_passes,
        test_custom_tolerance_raises,
        test_context_dict_has_all_12_fields,
        test_context_lhs_rhs_delta_correct,
        test_context_caller_fields_preserved,
        test_compute_lhs_sums_correctly,
        test_compute_rhs_profit_case,
        test_compute_rhs_loss_case,
        test_compute_rhs_zero_pnl,
        test_compute_tradable_balance_matches_compute_rhs,
        test_severity_is_critical,
        test_isa_state_error,
        test_isa_trading_system_error,
        test_pure_same_inputs_same_result,
        test_thread_safe_50_concurrent_calls,
        test_importable_from_capital_invariant,
        # INV7 / Audit 2.2 -- float-drift Option B
        test_inv7_sub_paise_drift_does_not_raise,
        test_inv7_half_paise_drift_passes,
        test_inv7_rupee_breach_still_raises,
        test_inv7_negative_guard_ignores_sub_paise_negative,
        test_fix005_minus_0_014_raises_with_tolerance_0_01,
        test_fix005_minus_0_009_passes_with_tolerance_0_01,
    ]

    print("=" * 70)
    print("capital/invariant.py -- Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as exc:
            failed.append((test.__name__, f"AssertionError: {exc}"))
            print(f"  FAIL: {exc}")
        except Exception as exc:
            failed.append((test.__name__, f"{type(exc).__name__}: {exc}"))
            print(f"  ERROR: {type(exc).__name__}: {exc}")

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
