# tests/unit/test_t2_limit_paths.py — T2 LIMIT-paths regression protection (10-Jul)
"""
Locks in the 10-Jul MARKET->marketable-LIMIT fix so the live-canary blocker can't
return: EVERY one of the T2 proof script's 4 real placements must place a marketable
LIMIT — NEVER a raw MARKET. (Zerodha's API refuses MARKET without market-protection;
that InputException rejected the 10-Jul canary pre-fill.)

Hermetic: a fake adapter that ACCEPTS LIMIT (records it) and REJECTS MARKET exactly
like the live API, plus a fake kite with an injected LTP. No network, no real order,
no live DB. Drives all 4 placements:
    BUY (t2_entry) · square SELL (t2_square) · ensure_flat SELL (t2_ensure_flat) ·
    close_overnight SELL (t2_close)
and asserts each is a LIMIT priced via the live helper marketable_limit_price
(BUY above LTP / SELL below LTP, tick-aligned), that the adapter accepted it, that a
regression to MARKET is rejected, and that a missing LTP aborts (never MARKET).
"""
import logging
from types import SimpleNamespace

import pytest

from scripts.t2_cnc_gtt_realtest import (
    ensure_flat, run_single_session, run_close_overnight, _marketable_limit,
)
from orders.price_math import marketable_limit_price, EMERGENCY_EXIT_BUFFER_PCT

LOG = logging.getLogger("t2_limit_test")
_TICK = 0.05
_LTP = 100.0


def _expected(side: str) -> float:
    """The exact marketable price the live helper produces for our fixed LTP+tick."""
    return marketable_limit_price(side, _LTP, EMERGENCY_EXIT_BUFFER_PCT, _TICK)


class _MarketRejected(Exception):
    """Stand-in for kiteconnect InputException ('Market orders ... not allowed via API')."""


class LimitOnlyAdapter:
    """Accepts LIMIT (records + returns an order id); REJECTS MARKET like the live API.
    Exposes ``_resolve_tick`` — the adapter contract ``_marketable_limit`` relies on."""

    def __init__(self, tick: float = _TICK):
        self.orders: list[dict] = []      # recorded place_order kwargs (LIMIT only)
        self._tick = tick

    def _resolve_tick(self, symbol):      # noqa: D401 — fail-safe tick like the real adapter
        return self._tick

    def get_quote(self, symbols):         # only the dry-run path uses this; unused here
        return {}

    def place_order(self, *, symbol, side, qty, price, order_type, intent, tag):
        if order_type == "MARKET":        # the regression guard: MARKET is refused, never recorded
            raise _MarketRejected(
                "Market orders without market protection are not allowed via API"
            )
        self.orders.append(dict(symbol=symbol, side=side, qty=qty, price=price,
                                order_type=order_type, intent=intent, tag=tag))
        return SimpleNamespace(broker_order_id=f"ord{len(self.orders)}", product="CNC")

    def by_tag(self, tag: str) -> dict:
        return next(o for o in self.orders if o["tag"] == tag)


_GTT_OK = {
    "type": "two-leg",
    "condition": {"trigger_values": [97.0, 105.0]},
    "orders": [
        {"transaction_type": "SELL", "product": "CNC", "quantity": 1},
        {"transaction_type": "SELL", "product": "CNC", "quantity": 1},
    ],
}


class FakeKite:
    def __init__(self, ltp: float = _LTP, held: int = 0, status: str = "COMPLETE",
                 ltp_raises: bool = False):
        self._ltp, self._held, self._status = ltp, held, status
        self._ltp_raises = ltp_raises
        self.deleted: list[int] = []

    def ltp(self, syms):
        if self._ltp_raises:
            raise KeyError(f"No quote returned for {syms}")
        return {syms[0]: {"last_price": self._ltp}}

    def positions(self):
        return {"day": ([{"tradingsymbol": "IDEA", "quantity": self._held}] if self._held else [])}

    def order_history(self, oid):
        return [{"status": self._status}]

    def get_gtt(self, gid):
        return _GTT_OK

    def delete_gtt(self, gid):
        self.deleted.append(int(gid))


class FakeGttPlacer:
    def place_for_fill(self, **kw):
        return SimpleNamespace(gtt_id=1001, sl_trigger=97.0, tgt_trigger=105.0,
                               sl_limit=96.0, tgt_limit=104.5)


class FakeStore:
    def get_active_gtt_for_trade(self, trade_id):
        return {"gtt_id": 1001, "status": "ACTIVE", "qty": 1}

    def set_gtt_state_status(self, gid, status, ts):
        pass


def _assert_marketable_limit(order: dict, side: str) -> None:
    assert order["order_type"] == "LIMIT", f"{order['tag']} must be LIMIT, got {order['order_type']}"
    assert order["side"] == side
    assert order["price"] == _expected(side)              # exactly the live-helper marketable price
    if side == "BUY":
        assert order["price"] > _LTP                      # marketable BUY: above LTP (rounded up)
    else:
        assert order["price"] < _LTP                      # marketable SELL: below LTP (rounded down)


# ── the 4 real placements each place a marketable LIMIT the (mocked) API accepts ──

def test_buy_and_square_place_marketable_limit():
    a, k = LimitOnlyAdapter(), FakeKite(held=1)
    rc = run_single_session(a, k, FakeGttPlacer(), FakeStore(), "IDEA", 1, LOG)
    assert rc == 0
    _assert_marketable_limit(a.by_tag("t2_entry"), "BUY")     # path 1: BUY
    _assert_marketable_limit(a.by_tag("t2_square"), "SELL")   # path 2: square SELL


def test_ensure_flat_places_marketable_limit():
    a, k = LimitOnlyAdapter(), FakeKite(held=1)
    flat, _ = ensure_flat(a, k, "IDEA", LOG)
    assert flat is True
    _assert_marketable_limit(a.by_tag("t2_ensure_flat"), "SELL")   # path 3: ensure-flat SELL


def test_close_overnight_places_marketable_limit():
    a, k = LimitOnlyAdapter(), FakeKite(held=1)
    rc = run_close_overnight(a, k, "IDEA", 1, "1001", LOG)
    assert rc == 0
    _assert_marketable_limit(a.by_tag("t2_close"), "SELL")     # path 4: close-overnight SELL


def test_no_raw_market_on_any_path():
    """Belt-and-suspenders: across the buy+square+ensure-flat+close paths, every
    recorded order is LIMIT (the fake would have raised on any MARKET)."""
    a, k = LimitOnlyAdapter(), FakeKite(held=1)
    run_single_session(a, k, FakeGttPlacer(), FakeStore(), "IDEA", 1, LOG)
    run_close_overnight(a, k, "IDEA", 1, "1001", LOG)
    ensure_flat(a, k, "IDEA", LOG)
    assert a.orders, "expected recorded placements"
    assert all(o["order_type"] == "LIMIT" for o in a.orders)


# ── the MARKET-regression guard actually bites (a revert to MARKET would fail loudly) ──

def test_market_regression_is_rejected():
    a = LimitOnlyAdapter()
    with pytest.raises(_MarketRejected):
        a.place_order(symbol="IDEA", side="BUY", qty=1, price=0.0,
                      order_type="MARKET", intent="DELIVERY", tag="regressed")
    assert a.orders == []      # a MARKET order is never accepted/recorded


# ── a missing/invalid LTP ABORTS the placement (never a MARKET fallback) ──

def test_missing_ltp_aborts_no_market_fallback():
    a = LimitOnlyAdapter()
    # (a) the helper raises, and nothing is placed
    with pytest.raises(RuntimeError):
        _marketable_limit(a, FakeKite(ltp_raises=True), "IDEA", "BUY", LOG)
    assert a.orders == []
    # (b) the self-safe path: a held qty but no LTP -> ensure_flat returns False +
    #     flags MANUAL, and STILL places nothing (no blind MARKET square)
    flat, detail = ensure_flat(a, FakeKite(held=1, ltp_raises=True), "IDEA", LOG)
    assert flat is False and "MANUAL" in detail.upper()
    assert a.orders == []


def test_marketable_limit_prices_are_through_the_touch():
    """The helper prices are marketable (cross the touch) and tick-aligned."""
    a, k = LimitOnlyAdapter(), FakeKite()
    buy = _marketable_limit(a, k, "IDEA", "BUY", LOG)
    sell = _marketable_limit(a, k, "IDEA", "SELL", LOG)
    assert buy == _expected("BUY") and buy > _LTP
    assert sell == _expected("SELL") and sell < _LTP
    # tick-aligned (multiples of the 0.05 tick, no float drift)
    assert round(buy / _TICK) * _TICK == pytest.approx(buy)
    assert round(sell / _TICK) * _TICK == pytest.approx(sell)
