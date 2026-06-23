"""
tests/unit/test_tick_failsafe_snap.py — 23-Jun tick fail-safe + modify_order snap.

Hardens the FIX-181 adapter chokepoint (broker/zerodha_adapter):
  - _resolve_tick / _snap_order_to_tick are FAIL-SAFE: a missing / zero tick →
    DEFAULT_TICK (0.05) snap + a throttled WARN, NEVER an un-rounded submission
    (the recurring silver/ETF "enter price in multiple of tick size" rejection).
  - modify_order snaps price + trigger_price BEFORE the paper branch (parity).
  - NO-BYPASS: place_order's submitted price reaches kite tick-aligned (gates the
    de-dup of order_placer._round_to_tick).
"""
from __future__ import annotations

import logging

import pytest

from core.exceptions import InstrumentNotFoundError
from orders.price_math import DEFAULT_TICK

# Reuse the real adapter builder + MockKite from the adapter suite.
from tests.unit.test_zerodha_adapter import _make_adapter, MockKite  # noqa: F401


def _is_tick_multiple(value: float, tick: float) -> bool:
    ratio = value / tick
    return abs(ratio - round(ratio)) < 1e-9


class _Cache:
    """Mirror InstrumentCache.tick_size: return the tick, or RAISE on a miss."""

    def __init__(self, ticks: dict) -> None:
        self._t = dict(ticks)

    def tick_size(self, symbol: str) -> float:
        if symbol not in self._t:
            raise InstrumentNotFoundError(f"{symbol} not in instruments")
        return self._t[symbol]


# IRIS present @0.05; TINYTICK @0.01; HDFCSILVER / SILVERBETA intentionally ABSENT.
_TICKS = {"IRIS": 0.05, "RELIANCE": 0.05, "TINYTICK": 0.01}


def _adapter_with_cache(paper: bool = False):
    adapter, kite, *_ = _make_adapter(paper=paper)
    adapter.set_instrument_cache(_Cache(_TICKS))
    return adapter, kite


# ── _resolve_tick: fail-safe + throttle ──────────────────────────────────────
def test_resolve_tick_present_is_symbol_specific():
    a, _ = _adapter_with_cache()
    assert a._resolve_tick("IRIS") == 0.05
    assert a._resolve_tick("TINYTICK") == 0.01          # NOT hardcoded 0.05


@pytest.mark.parametrize("sym", ["HDFCSILVER", "SILVERBETA"])
def test_resolve_tick_missing_falls_back_and_warns(sym, caplog):
    a, _ = _adapter_with_cache()
    with caplog.at_level(logging.WARNING):
        assert a._resolve_tick(sym) == DEFAULT_TICK     # 0.05, never None/0
    assert any("missing_tick_size" in r.getMessage() for r in caplog.records)


def test_resolve_tick_cache_none_falls_back():
    a, *_ = _make_adapter()                             # no cache wired
    assert a._resolve_tick("ANYTHING") == DEFAULT_TICK


def test_resolve_tick_nonpositive_falls_back():
    a, *_ = _make_adapter()
    a.set_instrument_cache(_Cache({"BAD": 0.0}))
    assert a._resolve_tick("BAD") == DEFAULT_TICK


def test_missing_tick_warning_throttled_once_per_symbol(caplog):
    a, _ = _adapter_with_cache()
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            a._resolve_tick("HDFCSILVER")
    warns = [r for r in caplog.records if "missing_tick_size" in r.getMessage()]
    assert len(warns) == 1                              # throttled per-symbol


# ── _snap_order_to_tick: never un-rounded + symbol-specific + SL direction ───
def test_snap_missing_symbol_never_unrounded():
    a, _ = _adapter_with_cache()
    price, _t = a._snap_order_to_tick("HDFCSILVER", "LIMIT", "SELL", 236.43, 0.0)
    assert price == pytest.approx(236.45)               # nearest 0.05, NOT 236.43
    assert _is_tick_multiple(price, 0.05)


def test_snap_iris_005():
    a, _ = _adapter_with_cache()
    price, _t = a._snap_order_to_tick("IRIS", "LIMIT", "BUY", 100.07, 0.0)
    assert price == pytest.approx(100.05)


def test_snap_tinytick_001_symbol_specific():
    a, _ = _adapter_with_cache()
    price, _t = a._snap_order_to_tick("TINYTICK", "LIMIT", "BUY", 100.073, 0.0)
    assert price == pytest.approx(100.07)               # 0.01 grid, not 0.05


def test_snap_sl_rounds_away_from_trigger_direction_preserved():
    a, _ = _adapter_with_cache()
    # SELL stop (exits LONG): limit must stay BELOW trigger → round DOWN.
    p_sell, _ = a._snap_order_to_tick("IRIS", "SL", "SELL", 100.07, 100.06)
    assert p_sell == pytest.approx(100.05)
    # BUY stop (exits SHORT): limit must stay ABOVE trigger → round UP.
    p_buy, _ = a._snap_order_to_tick("IRIS", "SL", "BUY", 100.02, 100.03)
    assert p_buy == pytest.approx(100.05)


# ── modify_order: snaps + Paper == Live parity ───────────────────────────────
def test_modify_order_live_snaps_price_and_trigger():
    a, kite = _adapter_with_cache(paper=False)
    cap: dict = {}
    kite.modify_order = lambda **kw: cap.update(kw)
    a.modify_order("OID", price=100.07, trigger_price=100.02, symbol="IRIS")
    assert cap["price"] == pytest.approx(100.05)
    assert cap["trigger_price"] == pytest.approx(100.00)


def test_modify_order_parity_snaps_in_both_modes(monkeypatch):
    import broker.zerodha_adapter as za
    calls: list = []
    real = za._round_nearest_to_tick
    monkeypatch.setattr(
        za, "_round_nearest_to_tick",
        lambda v, t: (calls.append((v, t)), real(v, t))[1],
    )
    a_live, kite = _adapter_with_cache(paper=False)
    kite.modify_order = lambda **kw: None
    a_live.modify_order("OID", price=100.07, trigger_price=100.02, symbol="IRIS")
    a_paper, _ = _adapter_with_cache(paper=True)
    a_paper.modify_order("OID", price=100.07, trigger_price=100.02, symbol="IRIS")
    # The same (value, tick) snap ran in BOTH modes (parity) — price 100.07 twice.
    assert calls.count((100.07, 0.05)) >= 2


# ── NO-BYPASS: place_order submits a tick-aligned price (gates the de-dup) ────
def test_no_bypass_place_order_submits_snapped_price():
    a, kite = _adapter_with_cache(paper=False)
    cap: dict = {}

    def _capture(**kw):
        cap.update(kw)
        return "KITE12345"

    kite.place_order = _capture
    a.place_order(symbol="HDFCSILVER", side="SELL", qty=2, price=236.43,
                  order_type="LIMIT", intent="INTRADAY")
    # Even with tick_size MISSING, the price reaching the broker is 0.05-aligned.
    assert cap["price"] == pytest.approx(236.45)
    assert _is_tick_multiple(cap["price"], 0.05)
