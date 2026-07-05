"""
tests/unit/test_mo1_flatten_quote_key.py — Wave 1, M-O1 regression.

M-O1 (docs/audit/full_system_audit_04july2026.md): `OrderReconciler._flatten_broker_position`
looked up the quote with a DOUBLE-prefixed key. It called
`self._quote_fn([f"NSE:{symbol}"])`, but `_quote_fn` is `adapter.get_quote`, which
takes BARE symbols, internally prepends `NSE:` (broker/zerodha_adapter.py:1567) and
returns BARE-keyed Quotes (:1581). So the effective kite key was `NSE:NSE:SYM`, the
lookup missed, `ltp` was always None, and every kill-switch / inflight-orphan flatten
fell back to a RAW MARKET order — the FIX-181 marketable-limit slippage cap was
silently inoperative in BOTH modes.

Fix: use the bare-symbol idiom the other three quote sites already use
(`self._quote_fn([symbol])` / `raw.get(symbol)` — reconciler :1329, :2859).

Observable discriminator: the resulting exit order type/price — RAW MARKET (bug)
vs a FIX-181-capped marketable LIMIT (fixed). No DB is involved, so this exercises
the real `_flatten_broker_position` against a quote provider that mirrors
`adapter.get_quote`'s real bare-symbol key contract.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from orders.order_reconciler import OrderReconciler
from orders.price_math import (
    DEFAULT_TICK, EMERGENCY_EXIT_BUFFER_PCT, marketable_limit_price,
)

# adapter.get_quote's contract: BARE-symbol keys only (it adds NSE: internally and
# returns bare-keyed Quotes). A prefixed key like 'NSE:RELIANCE' therefore misses.
_KNOWN = {"RELIANCE": SimpleNamespace(last_price=2500.0)}


def _quote_fn_bare_keyed(keys):
    return {k: _KNOWN[k] for k in keys if k in _KNOWN}


class _RecordingAdapter:
    def __init__(self):
        self.orders = []

    def place_order(self, **kw):
        self.orders.append(kw)
        return SimpleNamespace(broker_order_id="B_MO1")


def _flatten(symbol="RELIANCE", qty=10):
    adapter = _RecordingAdapter()
    stub = SimpleNamespace(
        _quote_fn=_quote_fn_bare_keyed,
        _adapter=adapter,
        _log=logging.getLogger("test_mo1"),
    )
    bp = SimpleNamespace(qty=qty)
    ok = OrderReconciler._flatten_broker_position(stub, symbol, bp, "KILL", "trd_mo1")
    return ok, adapter.orders


def test_flatten_uses_capped_limit_not_raw_market():
    """M-O1: with the quote keyed in the real (bare-symbol) format the lookup
    resolves, ltp is set, and the flatten goes out as a FIX-181 marketable LIMIT —
    NOT a raw MARKET order.

    RED (unfixed, double-prefix 'NSE:RELIANCE'): the key misses, ltp is None →
    order_type == 'MARKET' (FIX-181 cap inoperative) → this test FAILS.
    """
    ok, orders = _flatten(symbol="RELIANCE", qty=10)  # long → SELL to flatten
    assert ok
    assert len(orders) == 1
    o = orders[0]
    assert o["side"] == "SELL"
    assert o["order_type"] == "LIMIT", (
        "M-O1: flatten fell back to raw MARKET — the quote key missed and the "
        "FIX-181 marketable-limit cap did not engage."
    )
    expected = marketable_limit_price(
        "SELL", 2500.0, EMERGENCY_EXIT_BUFFER_PCT, DEFAULT_TICK)
    assert o["price"] == expected
    assert o["price"] > 0.0   # not the MARKET sentinel (0.0)


def test_flatten_short_position_buys_capped_limit():
    """Direction-aware: a short (bp.qty < 0) flattens with a BUY marketable LIMIT
    once the quote resolves (proves the fix works on the short side too)."""
    ok, orders = _flatten(symbol="RELIANCE", qty=-10)  # short → BUY to flatten
    assert ok and len(orders) == 1
    o = orders[0]
    assert o["side"] == "BUY"
    assert o["order_type"] == "LIMIT"
    expected = marketable_limit_price(
        "BUY", 2500.0, EMERGENCY_EXIT_BUFFER_PCT, DEFAULT_TICK)
    assert o["price"] == expected
