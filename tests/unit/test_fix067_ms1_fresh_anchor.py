"""FIX-067 / M-S1 (Wave-5): momentum fresh-LTP re-anchor.

Momentum strategies (pullback_wait_enabled=False) place immediately, so the webhook
trigger_price can be stale by the time the pipeline reaches placement. The fix fetches
the live LTP and RE-ANCHORS the ENTIRE placement basis onto it — entry, SL, target,
the sizing inputs (qty), and the reserved capital — using the correct get_quote
convention, while leaving the H-7 portfolio_lock cap+reserve section atomic/untouched.

Two coupled defects this suite pins:
  D1 (plumbing): the old `_quote_fn(symbol)` (bare string) + `Quote.get("last_price")`
      was INERT — get_quote takes a LIST and returns dict[str, Quote] (frozen dataclass,
      attr .last_price), so the fresh branch silently fell back to the stale price in
      BOTH live and paper. Same twin in `_retest_entry_estimate`.
  D2 (logic, audit M-S1): even with a fresh LTP, the recomputed SL was discarded (`_`),
      so SL/TGT/sizing/reservation stayed anchored to the stale trigger price.

Real collaborators: the REAL SignalProcessor pipeline (`_process_one`) with the REAL
`_derive_prices` / `_derive_target` math. Only the quote feed, placer, sizer, and fund
manager are test doubles, and each RECORDS the value the pipeline computed — so every
assertion is against a real derivation, never a stubbed output.

RED (pre-fix, HEAD 2023948) → GREEN (post-fix) is demonstrated by running this file
against `git stash`-ed source (all fail) then the fixed source (all pass).

Run: python -m pytest tests/unit/test_fix067_ms1_fresh_anchor.py -v
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest

from broker.zerodha_adapter import Quote
from signals.signal_processor import SignalProcessor
from tests.unit.test_signal_processor import (
    _make_proc,
    _make_store,
    _MockFundManager,
    _MockStrategy,
    _now_tup,
    _insert_queued_signal,
)

_TS = datetime(2026, 7, 7, 10, 0, 0)  # fixed; SUT only reads Quote.last_price


def _quote(symbol: str, last_price: float) -> Quote:
    """A real broker Quote (frozen dataclass) — exercises the exact `.last_price`
    attribute access the production convention uses."""
    return Quote(symbol=symbol, last_price=last_price, bid=0.0, ask=0.0, volume=0, ts=_TS)


def _list_quote_fn(price_by_symbol: dict[str, float]):
    """A faithful get_quote double: takes a LIST and returns dict[str, Quote] keyed by
    the bare symbol — identical to zerodha_adapter.get_quote AND _make_paper_quote_provider.

    Records every call's argument so the LIST convention (D1) can be asserted. Crucially,
    it iterates its argument exactly as get_quote does: the OLD code passing a bare string
    iterates per-char (no char matches a real symbol → empty dict → the caller falls back
    to stale), faithfully reproducing the D1 bug; the NEW code passing [symbol] keys the
    real symbol and returns the live quote.
    """
    calls: list = []

    def qf(symbols):
        calls.append(symbols)
        return {s: _quote(s, price_by_symbol[s]) for s in symbols if s in price_by_symbol}

    qf.calls = calls
    return qf


@dataclass(frozen=True)
class _Sizing:
    success: bool = True
    qty: int = 10
    margin_required: float = 5000.0
    risk_amount: float = 500.0
    bucket: str = "intraday"
    constraint: str = "RISK"
    reason: str = "ok"
    breakdown: dict = field(default_factory=dict)


class _CaptureSizer:
    """Records the (entry, sl) it is sized against — proving qty is computed off the
    fresh SL-distance."""

    def __init__(self):
        self.calls: list = []

    def calculate(self, symbol, direction, entry_price, sl_price, intent,
                  score_tier="MEDIUM", lot_size=1, perf_weight=1.0):
        self.calls.append({
            "symbol": symbol,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "sl_distance": abs(entry_price - sl_price),
        })
        return _Sizing()


class _CaptureFM(_MockFundManager):
    """Real portfolio_lock (RLock, inherited) + records the price reserve() is booked
    against — proving reserved capital == fresh order value."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.reserve_calls: list = []

    def reserve(self, symbol, qty, price, intent, signal_id=None, strategy=None):
        self.reserve_calls.append({
            "symbol": symbol, "qty": qty, "price": price, "strategy": strategy,
        })
        return super().reserve(symbol, qty, price, intent, signal_id, strategy)


class _CapturePlacer:
    def __init__(self):
        self.calls: list = []

    def place(self, **kwargs):
        self.calls.append(kwargs)


def _drive(*, stale=2500.0, fresh=2550.0, strategy=None, quote_fn=None,
           symbol="RELIANCE", scanner="gap_go_long"):
    """Drive ONE momentum webhook signal end-to-end through the real _process_one and
    return the recording doubles. trigger_price = `stale`; the live quote = `fresh`."""
    store, _ = _make_store()
    sig_id = "sig_ms1_001"
    _insert_queued_signal(store, sig_id, symbol=symbol, scanner=scanner)

    placer = _CapturePlacer()
    sizer = _CaptureSizer()
    fm = _CaptureFM()
    strat = strategy or _MockStrategy(name="gap_go_long_v1", direction="LONG")
    if quote_fn is None:
        quote_fn = _list_quote_fn({symbol: fresh})

    proc, _, _ = _make_proc(
        store=store, placer=placer, sizer=sizer, fm=fm,
        strategies={"gap_go_long_v1": strat},
        scan_webhook_map={scanner: {"strategy": "gap_go_long_v1"}},
    )
    proc._quote_fn = quote_fn  # harness hardcodes quote_fn=None; inject like _rate_limiter
    proc._process_one_safe(_now_tup(sig_id, scanner=scanner, symbol=symbol, price=stale))
    return proc, placer, sizer, fm, strat


# ── T1 — D1 convention: get_quote is called with a LIST, and the branch is live ──────
def test_t1_d1_convention_calls_get_quote_with_a_list():
    proc, placer, sizer, fm, strat = _drive()
    qf = proc._quote_fn
    assert qf.calls, "quote_fn was never called on the momentum path"
    # The fix mirrors the 7 correct get_quote callers: a LIST, not a bare string.
    assert qf.calls[0] == ["RELIANCE"], f"expected list arg [RELIANCE], got {qf.calls[0]!r}"
    assert isinstance(qf.calls[0], list)
    # ...and the fresh price actually reached placement (branch is LIVE, not inert).
    assert placer.calls, "place() was not called"
    assert placer.calls[0]["entry_price"] == pytest.approx(2550.0)


# ── T2 — M-S1 core: SL and TGT re-anchored onto the fresh LTP (were stale-anchored) ──
def test_t2_fresh_sl_and_tgt_reanchored_from_live_ltp():
    proc, placer, sizer, fm, strat = _drive(stale=2500.0, fresh=2550.0)
    exp_entry, exp_sl = proc._derive_prices(2550.0, strat)          # REAL derivation
    exp_tgt = proc._derive_target(exp_entry, exp_sl, strat)
    call = placer.calls[0]
    assert call["entry_price"] == pytest.approx(exp_entry)
    assert call["sl_price"] == pytest.approx(exp_sl)               # M-S1: fresh SL (was discarded)
    assert call["tgt_price"] == pytest.approx(exp_tgt)            # M-S1: fresh TGT

    # ...and demonstrably NOT the stale trigger-derived basis (the pre-fix behaviour).
    stale_entry, stale_sl = proc._derive_prices(2500.0, strat)
    stale_tgt = proc._derive_target(stale_entry, stale_sl, strat)
    assert call["sl_price"] != pytest.approx(stale_sl)
    assert call["tgt_price"] != pytest.approx(stale_tgt)


# ── T3 — sizing inputs + reservation price both on the fresh anchor ──────────────────
def test_t3_sizing_and_reservation_use_fresh_anchor():
    proc, placer, sizer, fm, strat = _drive(stale=2500.0, fresh=2550.0)
    exp_entry, exp_sl = proc._derive_prices(2550.0, strat)

    # qty is sized off the FRESH entry/SL (so sized risk == actual risk).
    assert sizer.calls, "sizer.calculate() was not called"
    assert sizer.calls[0]["entry_price"] == pytest.approx(exp_entry)
    assert sizer.calls[0]["sl_price"] == pytest.approx(exp_sl)

    # capital is reserved against the FRESH entry (reserved capital == order value),
    # and the strategy tag (H-7 per-strategy cap wiring) is preserved.
    assert fm.reserve_calls, "reserve() was not called"
    assert fm.reserve_calls[0]["price"] == pytest.approx(exp_entry)
    assert fm.reserve_calls[0]["strategy"] == "gap_go_long_v1"


# ── T4 — H-7 portfolio_lock section preserved: fetch is BEFORE the lock, cap+reserve in ──
def test_t4_h7_portfolio_lock_section_unchanged_by_reanchor():
    src = Path("signals/signal_processor.py").read_text(encoding="utf-8")
    # The fresh-quote fetch/re-anchor is positioned BEFORE portfolio_lock — never holds
    # the lock across a network quote fetch.
    i_fetch = src.index("re-anchoring entry/SL/TGT/sizing/reservation")
    i_lock = src.index("with self._fm.portfolio_lock:")
    assert i_fetch < i_lock, "fresh-quote re-anchor must precede the portfolio_lock section"
    # The H-7 cap-check stays the single, deduped location invoked by all three paths.
    assert src.count("def _enforce_strategy_position_cap") == 1
    assert src.count("self._enforce_strategy_position_cap(") == 3
    # Both quote sites use the D1 LIST convention; no bare-string call survives.
    assert src.count("self._quote_fn([symbol])") == 2
    assert "self._quote_fn(symbol)" not in src


# ── T5 — the retest twin (_retest_entry_estimate) uses the live LTP (was inert) ──────
def test_t5_retest_entry_estimate_uses_live_ltp_via_list_convention():
    store, _ = _make_store()
    proc, _, _ = _make_proc(store=store)
    proc._quote_fn = _list_quote_fn({"RELIANCE": 1234.5})
    # post-fix: returns the LIVE ltp (list convention → Quote.last_price).
    assert proc._retest_entry_estimate("RELIANCE", 1000.0) == pytest.approx(1234.5)
    # best-effort contract preserved: an unknown symbol falls back to the reclaim level.
    assert proc._retest_entry_estimate("UNKNOWN", 999.0) == pytest.approx(999.0)


# ── T6 — parity: a paper-style provider yields the identical re-anchor ───────────────
def test_t6_parity_paper_style_provider_same_reanchor():
    # Mimics _make_paper_quote_provider: takes a LIST, returns dict[str, Quote] keyed by
    # the ORIGINAL (bare) symbol with a real Quote (a real Kite LTP in production).
    def paper_qf(symbols):
        return {s: _quote(s, 2550.0) for s in symbols}

    proc, placer, sizer, fm, strat = _drive(stale=2500.0, fresh=2550.0, quote_fn=paper_qf)
    exp_entry, exp_sl = proc._derive_prices(2550.0, strat)
    exp_tgt = proc._derive_target(exp_entry, exp_sl, strat)
    call = placer.calls[0]
    assert call["entry_price"] == pytest.approx(exp_entry)
    assert call["sl_price"] == pytest.approx(exp_sl)
    assert call["tgt_price"] == pytest.approx(exp_tgt)


# ── Guard — pullback (non-momentum) strategies do NOT fetch/re-anchor on this path ───
def test_pullback_strategy_skips_fresh_reanchor():
    strat = _MockStrategy(name="gap_go_long_v1", direction="LONG", pullback_wait_enabled=True)
    qf = _list_quote_fn({"RELIANCE": 2550.0})
    proc, placer, sizer, fm, strat = _drive(stale=2500.0, strategy=strat, quote_fn=qf)
    # EntryGate owns current price for pullback; this path must NOT fetch a fresh quote.
    assert qf.calls == [], "pullback strategy must not fetch a fresh quote on this path"
