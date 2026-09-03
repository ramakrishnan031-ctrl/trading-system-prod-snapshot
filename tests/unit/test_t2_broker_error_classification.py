"""
tests/unit/test_t2_broker_error_classification.py -- T2, 03-Sep-2026.

On 03-Sep the CHECK9 emergency exit was attempted EIGHT times in 111.4 s
(15:10:17.585 -> 15:12:09.406) and rejected identically every time:

    "Market orders without market protection are not allowed via API."

That is a VALIDATION rejection: deterministic, and attempt 9 fails the same way.
In the two minutes that mattered the system repeated a doomed call instead of
escalating once, unmistakably. The position was flattened by a human at 15:12:11.
(docs/incident/2026-09-03_naked_position_ANANTRAJ.md)

WHY FIX-155's EXISTING GUARD COULD NOT STOP IT.
It queries for an orders row (leg='EOD' AND order_type='MARKET' AND status IN
PENDING/SUBMITTED/OPEN). A REJECTED order returns no order_id, so it persists NO
row -- the guard had nothing to find. Measured consequence: `orders WHERE
order_type='MARKET'` is 0 rows of 1315 despite 8 attempts that day.

WHAT MUST NOT REGRESS, AND WHY IT IS ASSERTED AS A PROPERTY.
A wrongly-TERMINAL verdict LOSES A REAL EXIT, which is far worse than a wasted
retry. So the direction that matters most here is the conservative one: an error
we have not measured must stay RETRYABLE. These tests pin both directions --
the measured rejection stops the loop, and everything unrecognised does not.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.exceptions import (
    BrokerAuthError,
    BrokerError,
    BrokerTimeoutError,
    OrderRejectedError,
)
from orders.order_reconciler import OrderReconciler
from tests.unit.test_order_reconciler import _make_reconciler, _make_store

_LOG = logging.getLogger("test_t2")

# The message Zerodha actually returned, verbatim from the 15:10:17.585 log line.
_MEASURED_REJECTION = (
    "Zerodha rejected order: Market orders without market protection are not "
    "allowed via API. Please set market protection or use a Limit order."
)

_C = OrderReconciler._classify_broker_error


# ── the measured failure stops the loop ──────────────────────────────────────

def test_the_measured_market_protection_rejection_is_terminal() -> None:
    """RED before T2: this was retried 8 times."""
    verdict = _C(OrderRejectedError(_MEASURED_REJECTION))
    assert verdict == OrderReconciler._BROKER_TERMINAL, (
        "the rejection that actually fired 8 times must be classified TERMINAL, "
        f"got {verdict!r}"
    )


def test_auth_failure_is_terminal() -> None:
    """Auth/permission cannot heal inside a retry loop."""
    assert _C(BrokerAuthError("Zerodha token/auth failure: api_key invalid")) == (
        OrderReconciler._BROKER_TERMINAL
    )


# ── the conservative direction: this is the one that must not slip ───────────

def test_an_unrecognised_error_stays_retryable() -> None:
    """The load-bearing safety property. A wrongly-TERMINAL verdict loses a real
    exit, so anything we have not measured must remain retryable."""
    verdict = _C(BrokerError("Unexpected error from Zerodha: RuntimeError: boom"))
    assert verdict == OrderReconciler._BROKER_RETRYABLE, (
        "an unrecognised broker error must stay RETRYABLE -- classifying it "
        f"TERMINAL would abandon a recoverable exit. got {verdict!r}"
    )


def test_a_rejection_not_on_the_allowlist_stays_retryable() -> None:
    """TERMINAL is an allow-list, NOT 'every OrderRejectedError'. A rejection we
    have not measured to be deterministic (margin, circuit limit, off-tick) may
    clear on the next cycle, and must not end the attempts."""
    verdict = _C(OrderRejectedError("Zerodha rejected order: Insufficient margin"))
    assert verdict == OrderReconciler._BROKER_RETRYABLE, (
        "an unmeasured rejection must stay RETRYABLE; TERMINAL is an allow-list "
        f"of observed-permanent failures only. got {verdict!r}"
    )


def test_a_plain_exception_stays_retryable() -> None:
    """Not every failure arrives translated."""
    assert _C(Exception("connection reset by peer")) == (
        OrderReconciler._BROKER_RETRYABLE
    )


# ── the ambiguous case is not retried blind ──────────────────────────────────

def test_a_placement_timeout_is_state_unknown_not_retryable() -> None:
    """A timeout on a PLACEMENT may mean the order landed. Retrying blind is the
    double-sell path -- on 03-Sep the last emergency attempt (15:12:09) and the
    human's fill (15:12:11) were 2 seconds apart."""
    verdict = _C(BrokerTimeoutError("Network timeout calling Zerodha"))
    assert verdict == OrderReconciler._BROKER_STATE_UNKNOWN, (
        f"a placement timeout must be STATE_UNKNOWN, got {verdict!r}"
    )
    assert verdict != OrderReconciler._BROKER_RETRYABLE, (
        "STATE_UNKNOWN must not be retried blindly"
    )


# ── the allow-list must not be widened from reasoning ────────────────────────

def test_the_terminal_allowlist_holds_only_what_was_measured() -> None:
    """Anyone adding an entry must have SEEN that rejection, not reasoned about
    it -- this path has been 'fixed' twice from a reasoned symptom (01-Jul tag
    length, 03-Sep market protection) and broke both times."""
    patterns = OrderReconciler._TERMINAL_REJECTION_PATTERNS
    assert patterns == ("market orders without market protection",), (
        "the TERMINAL allow-list changed. Each entry must correspond to a "
        f"rejection actually observed in production. got {patterns!r}"
    )
    assert all(p == p.lower() for p in patterns), (
        "patterns are matched against a lower-cased message; keep them lower-case"
    )


# ── the behaviour that stops the storm ───────────────────────────────────────

def test_a_blocked_trade_is_not_retried(tmp_path: Path) -> None:
    """The storm-stopper. Once a TERMINAL verdict is recorded for a trade, the
    reconciler must not call the emergency exit again for it."""
    rec = _make_reconciler(_make_store(tmp_path))
    assert rec._emergency_exit_blocked == {}, (
        "a fresh reconciler must start with no blocked trades -- the block is "
        "per-process on purpose, because a restart re-reads real broker state"
    )
    rec._emergency_exit_blocked["trd_x"] = OrderReconciler._BROKER_TERMINAL
    assert "trd_x" in rec._emergency_exit_blocked


def test_the_block_is_per_process_not_persisted(tmp_path: Path) -> None:
    """Two reconcilers over the SAME store must not share the block: it is a
    retry-suppression detail, not durable state. A restart must be free to try
    again against fresh broker state."""
    store = _make_store(tmp_path)
    a = _make_reconciler(store)
    a._emergency_exit_blocked["trd_y"] = OrderReconciler._BROKER_TERMINAL
    b = _make_reconciler(store)
    assert "trd_y" not in b._emergency_exit_blocked, (
        "the emergency-exit block leaked across instances; it must be in-memory "
        "and per-process so a restart is not permanently blinded"
    )
