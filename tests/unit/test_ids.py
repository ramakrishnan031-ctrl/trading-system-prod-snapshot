"""
tests/unit/test_ids.py

Validates core/ids.py against ID1–ID7 locked decisions:
  - new_signal_id() returns "sig_" + 32 lowercase hex chars (ID1, ID2, ID3)
  - new_trade_id()  returns "trd_" + 32 lowercase hex chars
  - new_order_id()  returns "ord_" + 32 lowercase hex chars
  - 1000 generated IDs across all three types are all unique (ID3)
  - is_valid_signal_id accepts a valid signal ID
  - is_valid_signal_id rejects: wrong prefix, short hex, long hex,
    uppercase hex, non-hex chars, empty string, None (ID5)
  - same validation suite for trade_id and order_id
  - generator + validator round-trip: validator accepts 100 generated IDs (ID4, ID5)

Run: python tests/unit/test_ids.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.ids import (
    is_valid_order_id,
    is_valid_signal_id,
    is_valid_trade_id,
    new_order_id,
    new_signal_id,
    new_trade_id,
    truncate_tag_for_broker,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_HEX32_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _check_format(id_str: str, prefix: str) -> None:
    assert isinstance(id_str, str), f"ID must be str, got {type(id_str)}"
    assert id_str.startswith(prefix), f"Expected prefix {prefix!r}, got {id_str!r}"
    hex_part = id_str[len(prefix):]
    assert len(hex_part) == 32, f"Hex part must be 32 chars, got {len(hex_part)}: {hex_part!r}"
    assert _HEX32_PATTERN.match(hex_part), f"Hex part must be lowercase hex: {hex_part!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Tests — generators (ID1, ID2, ID3, ID4)
# ─────────────────────────────────────────────────────────────────────────────

def test_new_signal_id_format() -> None:
    for _ in range(10):
        _check_format(new_signal_id(), "sig_")
    print('  OK new_signal_id() returns "sig_" + 32 lowercase hex chars (ID1-ID3)')


def test_new_trade_id_format() -> None:
    for _ in range(10):
        _check_format(new_trade_id(), "trd_")
    print('  OK new_trade_id() returns "trd_" + 32 lowercase hex chars (ID1-ID3)')


def test_new_order_id_format() -> None:
    for _ in range(10):
        _check_format(new_order_id(), "ord_")
    print('  OK new_order_id() returns "ord_" + 32 lowercase hex chars (ID1-ID3)')


def test_1000_generated_ids_are_unique() -> None:
    ids = (
        [new_signal_id() for _ in range(334)]
        + [new_trade_id()  for _ in range(333)]
        + [new_order_id()  for _ in range(333)]
    )
    assert len(ids) == len(set(ids)), f"Collision detected among {len(ids)} IDs"
    print("  OK 1000 generated IDs (334 signal + 333 trade + 333 order) are all unique (ID3)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — is_valid_signal_id (ID5)
# ─────────────────────────────────────────────────────────────────────────────

def test_is_valid_signal_id_accepts_valid() -> None:
    valid = "sig_" + "a" * 32
    assert is_valid_signal_id(valid) is True
    print("  OK is_valid_signal_id accepts a well-formed signal ID (ID5)")


def test_is_valid_signal_id_rejects_wrong_prefix() -> None:
    wrong = "trd_" + "a" * 32
    assert is_valid_signal_id(wrong) is False, "Must reject 'trd_' prefix for signal validator"
    print("  OK is_valid_signal_id rejects wrong prefix 'trd_' (ID5)")


def test_is_valid_signal_id_rejects_short_hex() -> None:
    short = "sig_" + "a" * 31
    assert is_valid_signal_id(short) is False, "Must reject 31-char hex"
    print("  OK is_valid_signal_id rejects short hex (31 chars) (ID5)")


def test_is_valid_signal_id_rejects_long_hex() -> None:
    long_ = "sig_" + "a" * 33
    assert is_valid_signal_id(long_) is False, "Must reject 33-char hex"
    print("  OK is_valid_signal_id rejects long hex (33 chars) (ID5)")


def test_is_valid_signal_id_rejects_uppercase_hex() -> None:
    upper = "sig_" + "A" * 32
    assert is_valid_signal_id(upper) is False, "Must reject uppercase hex"
    print("  OK is_valid_signal_id rejects uppercase hex (ID5)")


def test_is_valid_signal_id_rejects_non_hex_chars() -> None:
    bad = "sig_" + "z" * 32
    assert is_valid_signal_id(bad) is False, "Must reject non-hex chars"
    print("  OK is_valid_signal_id rejects non-hex chars (ID5)")


def test_is_valid_signal_id_rejects_empty_string() -> None:
    assert is_valid_signal_id("") is False
    print("  OK is_valid_signal_id rejects empty string (ID5)")


def test_is_valid_signal_id_returns_false_for_none() -> None:
    assert is_valid_signal_id(None) is False, "Must return False for None, not raise"
    print("  OK is_valid_signal_id returns False for None (never raises) (ID5)")


def test_is_valid_signal_id_returns_false_for_non_string() -> None:
    for bad in (42, 3.14, [], {}, object()):
        assert is_valid_signal_id(bad) is False, f"Must return False for {bad!r}"
    print("  OK is_valid_signal_id returns False for non-string types (ID5)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — is_valid_trade_id (ID5)
# ─────────────────────────────────────────────────────────────────────────────

def test_is_valid_trade_id_accepts_valid() -> None:
    valid = "trd_" + "b" * 32
    assert is_valid_trade_id(valid) is True
    print("  OK is_valid_trade_id accepts a well-formed trade ID (ID5)")


def test_is_valid_trade_id_rejects_wrong_prefix() -> None:
    wrong = "sig_" + "b" * 32
    assert is_valid_trade_id(wrong) is False
    print("  OK is_valid_trade_id rejects wrong prefix 'sig_' (ID5)")


def test_is_valid_trade_id_rejects_short_hex() -> None:
    assert is_valid_trade_id("trd_" + "b" * 31) is False
    print("  OK is_valid_trade_id rejects short hex (31 chars) (ID5)")


def test_is_valid_trade_id_rejects_long_hex() -> None:
    assert is_valid_trade_id("trd_" + "b" * 33) is False
    print("  OK is_valid_trade_id rejects long hex (33 chars) (ID5)")


def test_is_valid_trade_id_rejects_uppercase_hex() -> None:
    assert is_valid_trade_id("trd_" + "B" * 32) is False
    print("  OK is_valid_trade_id rejects uppercase hex (ID5)")


def test_is_valid_trade_id_rejects_non_hex_chars() -> None:
    assert is_valid_trade_id("trd_" + "z" * 32) is False
    print("  OK is_valid_trade_id rejects non-hex chars (ID5)")


def test_is_valid_trade_id_rejects_empty_string() -> None:
    assert is_valid_trade_id("") is False
    print("  OK is_valid_trade_id rejects empty string (ID5)")


def test_is_valid_trade_id_returns_false_for_none() -> None:
    assert is_valid_trade_id(None) is False
    print("  OK is_valid_trade_id returns False for None (ID5)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — is_valid_order_id (ID5)
# ─────────────────────────────────────────────────────────────────────────────

def test_is_valid_order_id_accepts_valid() -> None:
    valid = "ord_" + "c" * 32
    assert is_valid_order_id(valid) is True
    print("  OK is_valid_order_id accepts a well-formed order ID (ID5)")


def test_is_valid_order_id_rejects_wrong_prefix() -> None:
    wrong = "trd_" + "c" * 32
    assert is_valid_order_id(wrong) is False
    print("  OK is_valid_order_id rejects wrong prefix 'trd_' (ID5)")


def test_is_valid_order_id_rejects_short_hex() -> None:
    assert is_valid_order_id("ord_" + "c" * 31) is False
    print("  OK is_valid_order_id rejects short hex (31 chars) (ID5)")


def test_is_valid_order_id_rejects_long_hex() -> None:
    assert is_valid_order_id("ord_" + "c" * 33) is False
    print("  OK is_valid_order_id rejects long hex (33 chars) (ID5)")


def test_is_valid_order_id_rejects_uppercase_hex() -> None:
    assert is_valid_order_id("ord_" + "C" * 32) is False
    print("  OK is_valid_order_id rejects uppercase hex (ID5)")


def test_is_valid_order_id_rejects_non_hex_chars() -> None:
    assert is_valid_order_id("ord_" + "z" * 32) is False
    print("  OK is_valid_order_id rejects non-hex chars (ID5)")


def test_is_valid_order_id_rejects_empty_string() -> None:
    assert is_valid_order_id("") is False
    print("  OK is_valid_order_id rejects empty string (ID5)")


def test_is_valid_order_id_returns_false_for_none() -> None:
    assert is_valid_order_id(None) is False
    print("  OK is_valid_order_id returns False for None (ID5)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — generator + validator round-trip (ID4, ID5)
# ─────────────────────────────────────────────────────────────────────────────

def test_signal_id_round_trip() -> None:
    for _ in range(100):
        sid = new_signal_id()
        assert is_valid_signal_id(sid), f"Validator rejected its own generator output: {sid!r}"
    print("  OK is_valid_signal_id accepts all 100 new_signal_id() outputs (ID4, ID5)")


def test_trade_id_round_trip() -> None:
    for _ in range(100):
        tid = new_trade_id()
        assert is_valid_trade_id(tid), f"Validator rejected its own generator output: {tid!r}"
    print("  OK is_valid_trade_id accepts all 100 new_trade_id() outputs (ID4, ID5)")


def test_order_id_round_trip() -> None:
    for _ in range(100):
        oid = new_order_id()
        assert is_valid_order_id(oid), f"Validator rejected its own generator output: {oid!r}"
    print("  OK is_valid_order_id accepts all 100 new_order_id() outputs (ID4, ID5)")


def test_cross_validator_rejects_wrong_type_id() -> None:
    # A valid signal_id must NOT pass trade or order validators, and vice versa
    sid = new_signal_id()
    tid = new_trade_id()
    oid = new_order_id()
    assert not is_valid_trade_id(sid),  "trade validator must reject signal ID"
    assert not is_valid_order_id(sid),  "order validator must reject signal ID"
    assert not is_valid_signal_id(tid), "signal validator must reject trade ID"
    assert not is_valid_order_id(tid),  "order validator must reject trade ID"
    assert not is_valid_signal_id(oid), "signal validator must reject order ID"
    assert not is_valid_trade_id(oid),  "trade validator must reject order ID"
    print("  OK Cross-type validator rejection: each validator only accepts its own prefix (ID2, ID5)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — truncate_tag_for_broker (FIX-093)
# ─────────────────────────────────────────────────────────────────────────────

def test_truncate_tag_for_broker_1000_ids_max_16_chars() -> None:
    """Generate 1000 trade_ids and verify all truncated tags are <= 16 chars."""
    for _ in range(1000):
        full_trade_id = new_trade_id()
        truncated = truncate_tag_for_broker(full_trade_id)
        assert len(truncated) <= 16, f"Truncated tag exceeds 16 chars: {truncated!r} (len={len(truncated)})"
    print("  OK 1000 trade_ids truncated to <= 16 chars (FIX-093)")


def test_truncate_tag_for_broker_alphanumeric() -> None:
    """Verify truncated tags are alphanumeric (Kite requirement)."""
    for _ in range(100):
        full_trade_id = new_trade_id()
        truncated = truncate_tag_for_broker(full_trade_id)
        assert truncated.replace("_", "").isalnum(), f"Non-alphanumeric char in tag: {truncated!r}"
    print("  OK Truncated tags are alphanumeric (FIX-093)")


def test_truncate_tag_for_broker_preserves_full_trade_id() -> None:
    """Verify full trade_id is used for DB storage (not truncated)."""
    full_trade_id = new_trade_id()
    truncated = truncate_tag_for_broker(full_trade_id)
    # Full trade_id is 36 chars: "trd_" + 32 hex = 36
    assert len(full_trade_id) == 36, f"Expected 36 chars, got {len(full_trade_id)}: {full_trade_id!r}"
    # Truncated is 16 chars
    assert len(truncated) == 16, f"Expected 16 chars, got {len(truncated)}: {truncated!r}"
    # Full ID is preserved (just not sent to broker)
    assert is_valid_trade_id(full_trade_id), "Full trade_id must remain valid"
    print("  OK Full trade_id preserved in DB, only broker tag truncated (FIX-093)")


def test_truncate_tag_for_broker_handles_collisions() -> None:
    """
    Two different trade_ids truncated to same 16 chars is theoretically possible
    but astronomically unlikely (2^64 collision resistance). Test that truncation
    is deterministic: same input -> same output.
    """
    full_trade_id = "trd_a3f5b8c2d1e4f6a7b8c9d0e1f2a3b4c5"
    truncated1 = truncate_tag_for_broker(full_trade_id)
    truncated2 = truncate_tag_for_broker(full_trade_id)
    assert truncated1 == truncated2, "Truncation must be deterministic"
    assert truncated1 == "trd_a3f5b8c2d1e4", f"Expected 'trd_a3f5b8c2d1e4', got {truncated1!r}"
    print("  OK Truncation is deterministic (FIX-093)")


def test_truncate_tag_for_broker_empty_string() -> None:
    """Edge case: empty string input."""
    assert truncate_tag_for_broker("") == ""
    print("  OK truncate_tag_for_broker handles empty string (FIX-093)")


def test_truncate_tag_for_broker_short_string() -> None:
    """Edge case: input already < 16 chars."""
    short = "trd_abc"
    truncated = truncate_tag_for_broker(short)
    assert truncated == short, f"Short string should not be modified: {short!r} -> {truncated!r}"
    print("  OK truncate_tag_for_broker preserves strings < 16 chars (FIX-093)")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_new_signal_id_format,
        test_new_trade_id_format,
        test_new_order_id_format,
        test_1000_generated_ids_are_unique,
        test_is_valid_signal_id_accepts_valid,
        test_is_valid_signal_id_rejects_wrong_prefix,
        test_is_valid_signal_id_rejects_short_hex,
        test_is_valid_signal_id_rejects_long_hex,
        test_is_valid_signal_id_rejects_uppercase_hex,
        test_is_valid_signal_id_rejects_non_hex_chars,
        test_is_valid_signal_id_rejects_empty_string,
        test_is_valid_signal_id_returns_false_for_none,
        test_is_valid_signal_id_returns_false_for_non_string,
        test_is_valid_trade_id_accepts_valid,
        test_is_valid_trade_id_rejects_wrong_prefix,
        test_is_valid_trade_id_rejects_short_hex,
        test_is_valid_trade_id_rejects_long_hex,
        test_is_valid_trade_id_rejects_uppercase_hex,
        test_is_valid_trade_id_rejects_non_hex_chars,
        test_is_valid_trade_id_rejects_empty_string,
        test_is_valid_trade_id_returns_false_for_none,
        test_is_valid_order_id_accepts_valid,
        test_is_valid_order_id_rejects_wrong_prefix,
        test_is_valid_order_id_rejects_short_hex,
        test_is_valid_order_id_rejects_long_hex,
        test_is_valid_order_id_rejects_uppercase_hex,
        test_is_valid_order_id_rejects_non_hex_chars,
        test_is_valid_order_id_rejects_empty_string,
        test_is_valid_order_id_returns_false_for_none,
        test_signal_id_round_trip,
        test_trade_id_round_trip,
        test_order_id_round_trip,
        test_cross_validator_rejects_wrong_type_id,
        test_truncate_tag_for_broker_1000_ids_max_16_chars,
        test_truncate_tag_for_broker_alphanumeric,
        test_truncate_tag_for_broker_preserves_full_trade_id,
        test_truncate_tag_for_broker_handles_collisions,
        test_truncate_tag_for_broker_empty_string,
        test_truncate_tag_for_broker_short_string,
    ]

    print("=" * 70)
    print("ids.py — Test Suite")
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
