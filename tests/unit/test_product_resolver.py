"""
tests/unit/test_product_resolver.py

Validates broker/product_resolver.py:
  - resolve() returns correct broker codes for valid intents (PR3, PR4)
  - resolve() raises ValueError for unknown intents (PR10)
  - resolve() raises ProductNotSupportedError for unknown broker (PR4)
  - resolve() raises ProductNotSupportedError for empty/null mapping (PR3, PR10)
  - intent_for() reverse lookup works correctly (PR5)
  - intent_for() raises ValueError for unknown broker or code (PR5)
  - Constructor raises ValueError for empty map or non-dict entry (PR6)
  - Defensive copy: mutating injected dict does not affect resolver (PR9)
  - known_intents property returns all 4 valid intents (PR2)

Run: python -m pytest tests/unit/test_product_resolver.py -v
Or:  python tests/unit/test_product_resolver.py  (standalone mode)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.product_resolver import ProductResolver
from core.exceptions import ProductNotSupportedError


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixture
# ─────────────────────────────────────────────────────────────────────────────

def _make_resolver() -> ProductResolver:
    """Standard zerodha product map matching system_config.yaml (PR3)."""
    return ProductResolver({
        "zerodha": {
            "INTRADAY":     "MIS",
            "DELIVERY":     "CNC",
            "COVER_ORDER":  "CO",
            "BRACKET_ORDER": "",   # empty = not supported (PR3)
        }
    })


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- resolve() happy path (PR3, PR4)
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_intraday_returns_mis() -> None:
    r = _make_resolver()
    assert r.resolve("INTRADAY") == "MIS"
    assert r.resolve("INTRADAY", "zerodha") == "MIS"
    print("  OK resolve(INTRADAY, zerodha) -> MIS")


def test_resolve_delivery_returns_cnc() -> None:
    r = _make_resolver()
    assert r.resolve("DELIVERY") == "CNC"
    assert r.resolve("DELIVERY", "zerodha") == "CNC"
    print("  OK resolve(DELIVERY, zerodha) -> CNC")


def test_resolve_cover_order_returns_co() -> None:
    r = _make_resolver()
    assert r.resolve("COVER_ORDER") == "CO"
    assert r.resolve("COVER_ORDER", "zerodha") == "CO"
    print("  OK resolve(COVER_ORDER, zerodha) -> CO")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- resolve() error cases (PR4, PR10)
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_bracket_order_raises_product_not_supported() -> None:
    """BRACKET_ORDER maps to empty string -> ProductNotSupportedError (PR3, PR10)."""
    r = _make_resolver()
    raised = False
    try:
        r.resolve("BRACKET_ORDER", "zerodha")
    except ProductNotSupportedError as exc:
        raised = True
        assert exc.context["intent"] == "BRACKET_ORDER"
        assert exc.context["broker"] == "zerodha"
        # available_intents should exclude BRACKET_ORDER (empty mapping)
        assert "BRACKET_ORDER" not in exc.context["available_intents"]
    assert raised, "Expected ProductNotSupportedError for BRACKET_ORDER"
    print("  OK resolve(BRACKET_ORDER, zerodha) raises ProductNotSupportedError")


def test_resolve_unknown_intent_raises_value_error() -> None:
    """Intent not in _VALID_INTENTS -> ValueError (PR2, PR10)."""
    r = _make_resolver()
    raised = False
    try:
        r.resolve("UNKNOWN_INTENT")
    except ValueError:
        raised = True
    assert raised, "Expected ValueError for unknown intent"
    print("  OK resolve(UNKNOWN_INTENT) raises ValueError")


def test_resolve_unknown_broker_raises_product_not_supported() -> None:
    """Broker not in product_map -> ProductNotSupportedError (PR4)."""
    r = _make_resolver()
    raised = False
    try:
        r.resolve("INTRADAY", "unknown_broker")
    except ProductNotSupportedError as exc:
        raised = True
        assert exc.context["broker"] == "unknown_broker"
        assert exc.context["intent"] == "INTRADAY"
    assert raised, "Expected ProductNotSupportedError for unknown broker"
    print("  OK resolve(INTRADAY, unknown_broker) raises ProductNotSupportedError")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- intent_for() reverse lookup (PR5)
# ─────────────────────────────────────────────────────────────────────────────

def test_intent_for_mis_returns_intraday() -> None:
    r = _make_resolver()
    assert r.intent_for("MIS") == "INTRADAY"
    assert r.intent_for("MIS", "zerodha") == "INTRADAY"
    print("  OK intent_for(MIS, zerodha) -> INTRADAY")


def test_intent_for_unknown_code_raises_value_error() -> None:
    """Broker code not in map -> ValueError (PR5)."""
    r = _make_resolver()
    raised = False
    try:
        r.intent_for("XX", "zerodha")
    except ValueError:
        raised = True
    assert raised, "Expected ValueError for unknown broker code"
    print("  OK intent_for(XX, zerodha) raises ValueError")


def test_intent_for_unknown_broker_raises_value_error() -> None:
    r = _make_resolver()
    raised = False
    try:
        r.intent_for("MIS", "unknown_broker")
    except ValueError:
        raised = True
    assert raised, "Expected ValueError for unknown broker in intent_for"
    print("  OK intent_for(MIS, unknown_broker) raises ValueError")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- constructor validation (PR6)
# ─────────────────────────────────────────────────────────────────────────────

def test_constructor_empty_dict_raises_value_error() -> None:
    """Empty product_map -> ValueError (PR6)."""
    raised = False
    try:
        ProductResolver({})
    except ValueError:
        raised = True
    assert raised, "Expected ValueError for empty product_map"
    print("  OK ProductResolver({}) raises ValueError")


def test_constructor_non_dict_broker_entry_raises_value_error() -> None:
    """Broker entry that is not a dict -> ValueError (PR6)."""
    raised = False
    try:
        ProductResolver({"zerodha": "not_a_dict"})  # type: ignore[arg-type]
    except ValueError as exc:
        raised = True
        assert "zerodha" in str(exc)
    assert raised, "Expected ValueError for non-dict broker entry"
    print("  OK ProductResolver({'zerodha': 'not_a_dict'}) raises ValueError")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- known_intents property (PR2)
# ─────────────────────────────────────────────────────────────────────────────

def test_known_intents_contains_all_four() -> None:
    """known_intents property must return all 4 valid intents (PR2)."""
    r = _make_resolver()
    intents = r.known_intents
    assert "INTRADAY" in intents
    assert "DELIVERY" in intents
    assert "COVER_ORDER" in intents
    assert "BRACKET_ORDER" in intents
    assert len(intents) == 4
    print(f"  OK known_intents has all 4 intents: {sorted(intents)}")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- defensive copy (PR9)
# ─────────────────────────────────────────────────────────────────────────────

def test_mutating_injected_dict_does_not_affect_resolver() -> None:
    """Mutating the source dict after construction must not change resolver behavior."""
    source: dict[str, dict[str, str]] = {
        "zerodha": {
            "INTRADAY": "MIS",
            "DELIVERY": "CNC",
            "COVER_ORDER": "CO",
            "BRACKET_ORDER": "",
        }
    }
    r = ProductResolver(source)

    # Mutate the injected map after construction
    source["zerodha"]["INTRADAY"] = "TAMPERED"
    source["zerodha"]["NEW_KEY"] = "NEW_VALUE"

    # Resolver must still return the original values
    assert r.resolve("INTRADAY") == "MIS", (
        "resolve() returned tampered value -- defensive copy not working"
    )
    print("  OK Mutating injected dict after construction does not affect resolver")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_resolve_intraday_returns_mis,
        test_resolve_delivery_returns_cnc,
        test_resolve_cover_order_returns_co,
        test_resolve_bracket_order_raises_product_not_supported,
        test_resolve_unknown_intent_raises_value_error,
        test_resolve_unknown_broker_raises_product_not_supported,
        test_intent_for_mis_returns_intraday,
        test_intent_for_unknown_code_raises_value_error,
        test_intent_for_unknown_broker_raises_value_error,
        test_constructor_empty_dict_raises_value_error,
        test_constructor_non_dict_broker_entry_raises_value_error,
        test_known_intents_contains_all_four,
        test_mutating_injected_dict_does_not_affect_resolver,
    ]

    print("=" * 70)
    print("product_resolver.py -- Test Suite")
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
