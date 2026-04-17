"""
tests/unit/test_exceptions.py

Validates core/exceptions.py:
  - TradingSystemError is the root; catches all sub-exceptions
  - Every class declares a SEVERITY attribute in the valid set
  - Constructor stores kwargs in self.context dict
  - message str is accessible via str(e) / args[0]
  - Hierarchy isa checks (sub-roots and leaves all chain to root)
  - ClockSkewTooLarge importable from core.exceptions (new location)
  - ClockSkewTooLarge also importable via core.time_authority (re-export)
  - SEVERITY values are correct per E3/E4 decisions
  - Empty context is valid (no kwargs required)
  - Context keys are preserved exactly as passed

Run: python -m pytest tests/unit/test_exceptions.py -v
Or:  python tests/unit/test_exceptions.py  (standalone mode)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.exceptions import (
    BrokerAuthError,
    BrokerError,
    BrokerRateLimitError,
    BrokerTimeoutError,
    CapitalInvariantViolation,
    ClockSkewTooLarge,
    ConfigError,
    ConfigMissingError,
    ConfigSchemaError,
    DedupViolation,
    DuplicateSignalError,
    InvalidTransitionError,
    OrderRejectedError,
    ProductNotSupportedError,
    SignalError,
    SignalExpiredError,
    StateError,
    TimeError,
    TradingSystemError,
)

_VALID_SEVERITIES = {"INFO", "WARN", "ERROR", "CRITICAL"}

_ALL_CLASSES = [
    TradingSystemError,
    ConfigError, ConfigSchemaError, ConfigMissingError,
    StateError, CapitalInvariantViolation, DedupViolation,
    BrokerError, OrderRejectedError, BrokerAuthError, BrokerTimeoutError,
    BrokerRateLimitError, ProductNotSupportedError, InvalidTransitionError,
    TimeError, ClockSkewTooLarge,
    SignalError, SignalExpiredError, DuplicateSignalError,
]


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Root catch
# ─────────────────────────────────────────────────────────────────────────────

def test_root_catches_all_sub_exceptions() -> None:
    leaves = [
        ConfigSchemaError("bad schema"),
        ConfigMissingError("missing key"),
        CapitalInvariantViolation("invariant broken"),
        DedupViolation("dup"),
        OrderRejectedError("rejected"),
        BrokerAuthError("auth fail"),
        BrokerTimeoutError("timeout"),
        BrokerRateLimitError("rate limited", category="order", requested_tokens=1,
                             available_tokens=0.0, waited_sec=30.0, max_wait_sec=30.0),
        ProductNotSupportedError("not supported", intent="BRACKET_ORDER",
                                 broker="zerodha", available_intents=["INTRADAY"]),
        InvalidTransitionError("bad transition", order_id="ord_abc",
                               from_state="COMPLETE", to_state="OPEN",
                               allowed_next_states=[]),
        ClockSkewTooLarge("big skew"),
        SignalExpiredError("expired"),
        DuplicateSignalError("dup signal"),
    ]
    for exc in leaves:
        caught = False
        try:
            raise exc
        except TradingSystemError:
            caught = True
        assert caught, f"{type(exc).__name__} not caught by TradingSystemError"
    print(f"  OK All {len(leaves)} leaf exceptions caught by TradingSystemError")


def test_sub_root_catches_its_own_leaves() -> None:
    cases = [
        (ConfigError,  [ConfigSchemaError("x"), ConfigMissingError("x")]),
        (StateError,   [CapitalInvariantViolation("x"), DedupViolation("x")]),
        (BrokerError,  [OrderRejectedError("x"), BrokerAuthError("x"), BrokerTimeoutError("x"),
                        BrokerRateLimitError("x"), ProductNotSupportedError("x"),
                        InvalidTransitionError("x")]),
        (TimeError,    [ClockSkewTooLarge("x")]),
        (SignalError,  [SignalExpiredError("x"), DuplicateSignalError("x")]),
    ]
    for sub_root, leaves in cases:
        for exc in leaves:
            assert isinstance(exc, sub_root), \
                f"{type(exc).__name__} is not instance of {sub_root.__name__}"
    print("  OK All leaves are isinstance of their sub-root")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — SEVERITY attribute
# ─────────────────────────────────────────────────────────────────────────────

def test_every_class_has_severity_attribute() -> None:
    for cls in _ALL_CLASSES:
        assert hasattr(cls, "SEVERITY"), f"{cls.__name__} missing SEVERITY"
        assert isinstance(cls.SEVERITY, str), \
            f"{cls.__name__}.SEVERITY is not a str"
        assert cls.SEVERITY in _VALID_SEVERITIES, \
            f"{cls.__name__}.SEVERITY={cls.SEVERITY!r} not in {_VALID_SEVERITIES}"
    print(f"  OK All {len(_ALL_CLASSES)} classes have valid SEVERITY attribute")


def test_severity_values_match_decisions() -> None:
    # E2/E3/E4 pinned values
    assert TradingSystemError.SEVERITY == "ERROR"
    assert StateError.SEVERITY == "CRITICAL"
    assert CapitalInvariantViolation.SEVERITY == "CRITICAL"
    assert DedupViolation.SEVERITY == "WARN"
    assert BrokerAuthError.SEVERITY == "CRITICAL"
    assert ClockSkewTooLarge.SEVERITY == "CRITICAL"
    assert SignalError.SEVERITY == "WARN"
    assert SignalExpiredError.SEVERITY == "WARN"
    assert DuplicateSignalError.SEVERITY == "WARN"
    print("  OK SEVERITY values match E2/E3/E4 decisions")


def test_severity_accessible_on_instance() -> None:
    exc = CapitalInvariantViolation("invariant broken")
    assert exc.SEVERITY == "CRITICAL"
    print(f"  OK SEVERITY accessible on instance: {exc.SEVERITY}")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Constructor contract (E5)
# ─────────────────────────────────────────────────────────────────────────────

def test_message_stored_in_args() -> None:
    exc = TradingSystemError("something went wrong")
    assert str(exc) == "something went wrong"
    assert exc.args[0] == "something went wrong"
    print("  OK Message accessible via str(e) and args[0]")


def test_context_dict_stores_kwargs() -> None:
    exc = ClockSkewTooLarge(
        "clock off by 45s",
        skew_seconds=45.2,
        threshold_sec=30.0,
        local_time="2026-04-15T10:00:00+05:30",
    )
    assert exc.context["skew_seconds"] == 45.2
    assert exc.context["threshold_sec"] == 30.0
    assert exc.context["local_time"] == "2026-04-15T10:00:00+05:30"
    print(f"  OK context dict has {len(exc.context)} keys: {list(exc.context)}")


def test_empty_context_is_valid() -> None:
    exc = OrderRejectedError("order rejected")
    assert exc.context == {}
    print("  OK Empty context (no kwargs) is valid — context = {}")


def test_context_keys_preserved_exactly() -> None:
    exc = CapitalInvariantViolation(
        "invariant broken",
        expected=100000.0,
        actual=99999.5,
        delta=-0.5,
        operation="reserve_capital",
    )
    assert set(exc.context.keys()) == {"expected", "actual", "delta", "operation"}
    assert exc.context["delta"] == -0.5
    print(f"  OK Context keys preserved: {sorted(exc.context.keys())}")


def test_no_positional_args_beyond_message() -> None:
    # Passing a second positional arg should raise TypeError (not silently ignored)
    raised = False
    try:
        TradingSystemError("msg", "unexpected_positional")  # type: ignore[call-arg]
    except TypeError:
        raised = True
    assert raised, "Expected TypeError for second positional arg"
    print("  OK Second positional arg raises TypeError as expected")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Import location (E4: ClockSkewTooLarge moved from time_authority)
# ─────────────────────────────────────────────────────────────────────────────

def test_clockskew_importable_from_core_exceptions() -> None:
    from core.exceptions import ClockSkewTooLarge as CST
    exc = CST("big skew", skew_seconds=45.0)
    assert isinstance(exc, TimeError)
    assert isinstance(exc, TradingSystemError)
    print("  OK ClockSkewTooLarge importable from core.exceptions")


def test_clockskew_importable_via_time_authority_reexport() -> None:
    from core.time_authority import ClockSkewTooLarge as CST
    exc = CST("big skew", skew_seconds=45.0)
    assert isinstance(exc, TimeError)
    assert isinstance(exc, TradingSystemError)
    print("  OK ClockSkewTooLarge importable via core.time_authority (re-export)")


def test_clockskew_from_both_locations_is_same_class() -> None:
    from core.exceptions import ClockSkewTooLarge as FromExceptions
    from core.time_authority import ClockSkewTooLarge as FromTimeAuthority
    assert FromExceptions is FromTimeAuthority, \
        "Re-export is a different class object — should be identical"
    print("  OK Both import paths resolve to the same class object")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Hierarchy isa checks
# ─────────────────────────────────────────────────────────────────────────────

def test_full_isa_chain() -> None:
    chains = [
        (ConfigSchemaError("x"),        [ConfigError, TradingSystemError, Exception]),
        (ConfigMissingError("x"),        [ConfigError, TradingSystemError, Exception]),
        (CapitalInvariantViolation("x"), [StateError,  TradingSystemError, Exception]),
        (DedupViolation("x"),            [StateError,  TradingSystemError, Exception]),
        (OrderRejectedError("x"),        [BrokerError, TradingSystemError, Exception]),
        (BrokerAuthError("x"),           [BrokerError, TradingSystemError, Exception]),
        (BrokerTimeoutError("x"),        [BrokerError, TradingSystemError, Exception]),
        (BrokerRateLimitError("x"),      [BrokerError, TradingSystemError, Exception]),
        (ProductNotSupportedError("x"),  [BrokerError, TradingSystemError, Exception]),
        (InvalidTransitionError("x"),    [BrokerError, TradingSystemError, Exception]),
        (ClockSkewTooLarge("x"),         [TimeError,   TradingSystemError, Exception]),
        (SignalExpiredError("x"),        [SignalError,  TradingSystemError, Exception]),
        (DuplicateSignalError("x"),      [SignalError,  TradingSystemError, Exception]),
    ]
    for exc, ancestors in chains:
        for ancestor in ancestors:
            assert isinstance(exc, ancestor), \
                f"{type(exc).__name__} is not isinstance of {ancestor.__name__}"
    print(f"  OK All {len(chains)} isa chains verified to Exception root")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — ProductNotSupportedError (PR7)
# ─────────────────────────────────────────────────────────────────────────────

def test_product_not_supported_error_instantiation() -> None:
    exc = ProductNotSupportedError(
        "BRACKET_ORDER not supported",
        intent="BRACKET_ORDER",
        broker="zerodha",
        available_intents=["INTRADAY", "DELIVERY", "COVER_ORDER"],
    )
    assert str(exc) == "BRACKET_ORDER not supported"
    assert exc.context["intent"] == "BRACKET_ORDER"
    assert exc.context["broker"] == "zerodha"
    assert exc.context["available_intents"] == ["INTRADAY", "DELIVERY", "COVER_ORDER"]
    print("  OK ProductNotSupportedError: instantiation, message, context (PR7)")


def test_product_not_supported_error_severity() -> None:
    assert ProductNotSupportedError.SEVERITY == "ERROR"
    exc = ProductNotSupportedError("test", intent="X", broker="Y", available_intents=[])
    assert exc.SEVERITY == "ERROR"
    print("  OK ProductNotSupportedError.SEVERITY == 'ERROR' (PR7)")


def test_product_not_supported_error_isa_broker_error() -> None:
    exc = ProductNotSupportedError("test", intent="X", broker="Y", available_intents=[])
    assert isinstance(exc, BrokerError)
    assert isinstance(exc, TradingSystemError)
    assert isinstance(exc, Exception)
    print("  OK ProductNotSupportedError ISA BrokerError ISA TradingSystemError (PR7)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- InvalidTransitionError (OSM6)
# ─────────────────────────────────────────────────────────────────────────────

def test_invalid_transition_error_instantiation() -> None:
    exc = InvalidTransitionError(
        "Cannot transition COMPLETE -> OPEN",
        order_id="ord_abc123",
        from_state="COMPLETE",
        to_state="OPEN",
        allowed_next_states=[],
    )
    assert str(exc) == "Cannot transition COMPLETE -> OPEN"
    assert exc.context["order_id"] == "ord_abc123"
    assert exc.context["from_state"] == "COMPLETE"
    assert exc.context["to_state"] == "OPEN"
    assert exc.context["allowed_next_states"] == []
    assert exc.SEVERITY == "ERROR"
    assert isinstance(exc, BrokerError)
    assert isinstance(exc, TradingSystemError)
    print("  OK InvalidTransitionError: instantiation, context, severity, ISA chain (OSM6)")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_root_catches_all_sub_exceptions,
        test_sub_root_catches_its_own_leaves,
        test_every_class_has_severity_attribute,
        test_severity_values_match_decisions,
        test_severity_accessible_on_instance,
        test_message_stored_in_args,
        test_context_dict_stores_kwargs,
        test_empty_context_is_valid,
        test_context_keys_preserved_exactly,
        test_no_positional_args_beyond_message,
        test_product_not_supported_error_instantiation,
        test_product_not_supported_error_severity,
        test_product_not_supported_error_isa_broker_error,
        test_invalid_transition_error_instantiation,
        test_clockskew_importable_from_core_exceptions,
        test_clockskew_importable_via_time_authority_reexport,
        test_clockskew_from_both_locations_is_same_class,
        test_full_isa_chain,
    ]

    print("=" * 70)
    print("exceptions.py — Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as e:
            failed.append((test.__name__, f"AssertionError: {e}"))
            print(f"  FAIL FAIL: {e}")
        except Exception as e:
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))
            print(f"  FAIL ERROR: {type(e).__name__}: {e}")

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
