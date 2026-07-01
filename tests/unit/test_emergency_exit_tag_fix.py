"""
tests/unit/test_emergency_exit_tag_fix.py — 01-Jul-2026.

Regression guard for the broken naked-position EMERGENCY flatten: the reconciler's
CHECK9 emergency market exit passed the full 36-char trade_id as the Kite `tag`,
which Zerodha rejects ("Invalid tags: max allowed tag length is 20") — so the last
line of defense against a naked position failed to place (1-Jul BANSALWIRE).

Root-cause fix = a defensive truncation GUARD at the single order-submission
chokepoint (`ZerodhaAdapter.place_order`), so NO caller can EVER submit an
over-length tag. Plus the call-site fix at `_emergency_market_close`.

These tests FAIL on the old code (the tag reaching the broker is 36 chars → the
Zerodha-mimicking kite rejects) and PASS on the fix (truncated to <=16).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from tests.unit.test_zerodha_adapter import MockKite, _make_adapter

_LOG = logging.getLogger("test_emergency_exit_tag")
# The real shape that broke it: "trd_" + 32 hex = 36 chars.
_LONG_TRADE_ID = "trd_8b4844dabb9b4f988ad857bbd51693bf"


class _TagLimitKite(MockKite):
    """A kite that mimics Zerodha: reject any order whose tag exceeds 20 chars,
    and record the tag actually submitted."""

    def __init__(self) -> None:
        super().__init__()
        self.last_tag: object = "__unset__"

    def place_order(self, **kwargs):  # type: ignore[override]
        tag = kwargs.get("tag")
        self.last_tag = tag
        if tag is not None and len(str(tag)) > 20:
            raise Exception("Invalid tags: max allowed tag length is 20")
        return self.place_order_return


def _place(adapter, tag):
    return adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=1, price=0.0,
        order_type="MARKET", intent="INTRADAY", tag=tag,
    )


# ── the boundary guard (the root-cause fix, protects ALL callers) ─────────────

def test_over_length_tag_truncated_and_accepted_at_boundary():
    kite = _TagLimitKite()
    adapter, *_ = _make_adapter(kite=kite)
    placed = _place(adapter, _LONG_TRADE_ID)          # 36 chars in
    assert placed.broker_order_id                     # accepted (would raise on old code)
    assert kite.last_tag is not None
    assert len(kite.last_tag) <= 20                   # broker-safe
    assert len(kite.last_tag) <= 16                   # matches truncate_tag_for_broker
    assert _LONG_TRADE_ID.startswith(kite.last_tag)   # a stable prefix (traceable)


def test_short_tag_passes_through_unchanged_idempotent():
    kite = _TagLimitKite()
    adapter, *_ = _make_adapter(kite=kite)
    _place(adapter, "rc_recovery_sl")                 # 14 chars — under the safe zone
    assert kite.last_tag == "rc_recovery_sl"           # unchanged (idempotent)


def test_none_tag_is_safe():
    kite = _TagLimitKite()
    adapter, *_ = _make_adapter(kite=kite)
    placed = _place(adapter, None)
    assert placed.broker_order_id
    assert kite.last_tag in (None, "")                 # not crashed; passed through


def test_hard_kill_length_tag_is_truncated():
    # the HARD_KILL sweep literal 'ks_hard_kill_sweep' is 18 chars (near the limit);
    # the boundary guard brings every submission safely to <=16.
    kite = _TagLimitKite()
    adapter, *_ = _make_adapter(kite=kite)
    _place(adapter, "ks_hard_kill_sweep")
    assert len(kite.last_tag) <= 16


# ── parity: paper mode never submits to Kite, so a long tag is a no-op ────────

def test_paper_mode_unaffected_by_long_tag():
    adapter, *_ = _make_adapter(paper=True)
    placed = _place(adapter, _LONG_TRADE_ID)           # paper: synth fill, no kite call
    assert placed.broker_order_id                      # no rejection in paper


# ── the reconciler CHECK9 emergency-exit call site (:2133) ────────────────────

class _RecordingAdapter:
    def __init__(self) -> None:
        self.last_tag: object = "__unset__"

    def place_order(self, **kwargs):
        self.last_tag = kwargs.get("tag")
        return SimpleNamespace(broker_order_id="REC123")


def test_check9_emergency_exit_uses_broker_safe_tag():
    from orders.order_reconciler import OrderReconciler
    rec = _RecordingAdapter()
    fake = SimpleNamespace(_adapter=rec, _order_mgr=SimpleNamespace(
        insert_order=lambda **kw: None))
    trade = {"trade_id": _LONG_TRADE_ID, "symbol": "IDEA", "direction": "LONG",
             "qty_filled": 1, "product": "MIS"}
    result = OrderReconciler._emergency_market_close(fake, trade, _LOG)
    assert result.startswith("placed")
    assert rec.last_tag is not None and len(rec.last_tag) <= 16
    assert _LONG_TRADE_ID.startswith(rec.last_tag)     # traceable prefix
