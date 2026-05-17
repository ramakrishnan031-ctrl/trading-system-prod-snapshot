"""
core/ids.py — Trading System v2

Purpose:
    Generate and validate the three internal ID types used throughout the system.
    Every signal, trade, and internal order gets one of these IDs at creation time.

Locked Design Decisions:
    ID1 — Format: "{prefix}{uuid4_hex}" — prefix + 32-char lowercase UUID4 hex.
           Examples: "sig_a3f5b8c2d1e4f6a7b8c9d0e1f2a3b4c5"
                     "trd_a3f5b8c2d1e4f6a7b8c9d0e1f2a3b4c5"
                     "ord_a3f5b8c2d1e4f6a7b8c9d0e1f2a3b4c5"
    ID2 — Prefixes: signal_id → "sig_", trade_id → "trd_", order_id → "ord_".
           "ord_" is the internal order ID; broker-assigned IDs are stored
           separately as broker_order_id in the state_store.
    ID3 — Full 32-char UUID4 hex, no truncation.
    ID4 — API: new_signal_id(), new_trade_id(), new_order_id() — zero-arg functions.
    ID5 — Validators: is_valid_signal_id(s), is_valid_trade_id(s), is_valid_order_id(s).
           Accept any object; return False (never raise) for invalid input.
    ID6 — No timestamp in the ID. Timestamp lives in L9 log envelope and state_store rows.
    ID7 — Layer 0: stdlib only (uuid, re). No imports from this codebase.

What This Module Does NOT Do:
    - Does not log (no logger import)
    - Does not raise custom exceptions (validators return bool)
    - Does not maintain state (pure functions)
    - Does not validate broker_order_id (broker owns that format)
"""
from __future__ import annotations

import re
import uuid

# ─────────────────────────────────────────────────────────────────────────────
# Constants (ID1, ID2, ID3)
# ─────────────────────────────────────────────────────────────────────────────

_PREFIX_SIGNAL = "sig_"
_PREFIX_TRADE  = "trd_"
_PREFIX_ORDER  = "ord_"

# Compiled regex: exactly 32 lowercase hex chars (ID3, ID5)
_HEX32 = re.compile(r"^[0-9a-f]{32}$")


# ─────────────────────────────────────────────────────────────────────────────
# Generators (ID4)
# ─────────────────────────────────────────────────────────────────────────────

def new_signal_id() -> str:
    """Return a new unique signal ID: "sig_" + 32-char UUID4 hex."""
    return _PREFIX_SIGNAL + uuid.uuid4().hex


def new_trade_id() -> str:
    """Return a new unique trade ID: "trd_" + 32-char UUID4 hex."""
    return _PREFIX_TRADE + uuid.uuid4().hex


def new_order_id() -> str:
    """Return a new unique internal order ID: "ord_" + 32-char UUID4 hex.

    Note: broker-assigned order IDs are stored as broker_order_id in state_store
    and are entirely separate from this internal ID.
    """
    return _PREFIX_ORDER + uuid.uuid4().hex


# ─────────────────────────────────────────────────────────────────────────────
# Validators (ID5)
# ─────────────────────────────────────────────────────────────────────────────

def is_valid_signal_id(s: object) -> bool:
    """Return True iff s is a str matching "sig_[0-9a-f]{32}"."""
    return (
        isinstance(s, str)
        and s.startswith(_PREFIX_SIGNAL)
        and _HEX32.match(s[len(_PREFIX_SIGNAL):]) is not None
    )


def is_valid_trade_id(s: object) -> bool:
    """Return True iff s is a str matching "trd_[0-9a-f]{32}"."""
    return (
        isinstance(s, str)
        and s.startswith(_PREFIX_TRADE)
        and _HEX32.match(s[len(_PREFIX_TRADE):]) is not None
    )


def is_valid_order_id(s: object) -> bool:
    """Return True iff s is a str matching "ord_[0-9a-f]{32}"."""
    return (
        isinstance(s, str)
        and s.startswith(_PREFIX_ORDER)
        and _HEX32.match(s[len(_PREFIX_ORDER):]) is not None
    )


# ─────────────────────────────────────────────────────────────────────────────
# Broker Tag Truncation (FIX-093)
# ─────────────────────────────────────────────────────────────────────────────

def truncate_tag_for_broker(tag: str) -> str:
    """
    Truncate tag to 16 chars for broker API compliance (FIX-093).

    Kite API enforces 20-char max on tag field, but empirical testing shows
    rejection at 17+ chars. Truncate to 16 chars (alphanumeric safe zone).

    Full trade_id is preserved in DB; only the broker tag is truncated.
    Internal deduplication still uses the full trade_id.

    Args:
        tag: Full tag string (typically trade_id: "trd_" + 32-char hex = 36 chars)

    Returns:
        Truncated tag (max 16 chars, alphanumeric)

    Example:
        >>> truncate_tag_for_broker("trd_a3f5b8c2d1e4f6a7b8c9d0e1f2a3b4c5")
        'trd_a3f5b8c2d1e4'
    """
    return tag[:16] if tag else ""
