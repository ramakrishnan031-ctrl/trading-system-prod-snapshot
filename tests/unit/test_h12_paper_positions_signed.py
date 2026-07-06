"""
tests/unit/test_h12_paper_positions_signed.py

Wave-3 / H-12 — the paper adapter's get_positions must return SIGNED net qty
(long > 0, short < 0), mirroring the live branch (kite "net" quantity is signed).

Root cause (pre-fix): the paper branch returned ``qty=abs(info["qty"])`` while the
live branch returns the signed ``quantity``. The paper book itself already tracks
a signed net (``new_qty = old_qty ± qty`` at the fill site), so a paper SHORT nets
< 0 in the book but surfaced > 0 through get_positions. The two most safety-critical
consumers derive the flatten DIRECTION from that sign:

  * broker/position_helpers.py::determine_close_direction  (net < 0 -> BUY)
  * capital/kill_switch.py HARD_KILL orphan sweep          ("SELL" if pqty > 0)
  * orders/order_reconciler.py::_flatten_broker_position   ("SELL" if bp.qty > 0)
  * orders/eod_squareoff.py::_place_marketable_limit_exit  ("SELL" if qty > 0)

so in paper a short read as +qty flattens with SELL -> DOUBLES the short (the
THELEELA oversell these helpers exist to prevent). This blinded every paper drill
of FIX-190. Magnitude-only consumers (abs()/!=0/==0) are unaffected either way.

These tests drive the REAL paper adapter: production place_order -> _synth_fill ->
paper book, then assert against the REAL get_positions + determine_close_direction.
No mocks are in the assertion path (paper mode never touches kite).

Real collaborators:  ZerodhaAdapter.place_order / _synth_fill / get_positions
                     (paper branch), the paper position book, Position,
                     broker.position_helpers.{broker_net_qty,determine_close_direction}.
Simulated/recorded:  none in the assertion path. The wait helper polls the signed
                     book (independent of the get_positions sign bug) so the arrange
                     step is stable under BOTH the unfixed and fixed adapter.

RED/GREEN: test_h12_paper_short_qty_negative and the SHORT leg of
test_h12_determine_close_direction_matches_live FAIL on the unfixed adapter
(qty=abs(...)) and PASS after the one-line fix (qty=info["qty"]).

Run: python -m pytest tests/unit/test_h12_paper_positions_signed.py -v
"""

from __future__ import annotations

import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.zerodha_adapter import Position, ZerodhaAdapter
from broker.order_state_machine import OrderStateMachine
from broker.product_resolver import ProductResolver
from broker.rate_limiter import RateLimiter
from broker.cost_calculator import CostCalculator
from broker.position_helpers import broker_net_qty, determine_close_direction
from unittest.mock import MagicMock


# ── minimal real-ish adapter wiring (paper mode never calls kite) ─────────────

_PRODUCT_MAP = {"zerodha": {"INTRADAY": "MIS", "DELIVERY": "CNC",
                            "COVER_ORDER": "CO", "BRACKET_ORDER": ""}}

_LIMITS = MagicMock()
for _cat in ("order", "quote", "margins", "historical"):
    getattr(_LIMITS, _cat).burst = 10
    getattr(_LIMITS, _cat).rate_per_sec = 10

_COSTS = MagicMock()
_COSTS.zerodha.brokerage_flat_intraday = 20.0
_COSTS.zerodha.brokerage_pct_intraday = 0.03
_COSTS.zerodha.stt_sell_pct = 0.025
_COSTS.zerodha.stt_cnc_pct = 0.1
_COSTS.zerodha.exchange_txn_pct = 0.00297
_COSTS.zerodha.gst_pct = 18.0
_COSTS.zerodha.sebi_pct = 0.0001
_COSTS.zerodha.stamp_duty_mis_buy_pct = 0.003
_COSTS.zerodha.stamp_duty_cnc_buy_pct = 0.015


def _paper_adapter() -> ZerodhaAdapter:
    """A real paper-mode ZerodhaAdapter with instant synth fills, no bus, no
    LTP-gating and no slippage (fills at the requested LIMIT price)."""
    return ZerodhaAdapter(
        kite_client=MagicMock(),                 # unused in paper mode
        rate_limiter=RateLimiter(_LIMITS, max_wait_sec=5.0),
        product_resolver=ProductResolver(_PRODUCT_MAP),
        cost_calculator=CostCalculator(_COSTS),
        state_machine=OrderStateMachine(),
        logger=logging.getLogger("test_h12"),
        paper_mode=True,
        paper_capital=1_000_000.0,
        bus=None,                                # book updates then returns pre-publish
        paper_auto_fill_delay_sec=0.0,           # instant synth fill
        paper_ltp_gating_enabled=False,          # legacy path -> fill at price
        delivery_enabled=True,
    )


def _book_qty(adapter: ZerodhaAdapter, symbol: str) -> int:
    """Signed net qty from the paper book (0 if flat/absent). Read under the
    fill lock, matching the writer — independent of the get_positions sign bug."""
    with adapter._paper_fills_lock:
        info = adapter._paper_positions.get(symbol)
        return int(info["qty"]) if info else 0


def _fill(adapter: ZerodhaAdapter, symbol: str, side: str, qty: int,
          price: float = 500.0) -> None:
    """Drive one REAL paper fill and wait (bounded) until the signed book
    reflects the resulting net, so assertions run against settled state."""
    before = _book_qty(adapter, symbol)
    expected = before + qty if side == "BUY" else before - qty
    adapter.place_order(symbol=symbol, side=side, qty=qty, price=price,
                        order_type="LIMIT", intent="INTRADAY")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if _book_qty(adapter, symbol) == expected:
            return
        time.sleep(0.005)
    raise AssertionError(
        f"paper book for {symbol} did not settle to {expected} "
        f"(last={_book_qty(adapter, symbol)})"
    )


def _pos(adapter: ZerodhaAdapter, symbol: str):
    for p in adapter.get_positions():
        if p.symbol == symbol:
            return p
    return None


# ── Test 1 — long -> qty > 0 ──────────────────────────────────────────────────

def test_h12_paper_long_qty_positive() -> None:
    adapter = _paper_adapter()
    _fill(adapter, "RELIANCE", "BUY", 100)
    p = _pos(adapter, "RELIANCE")
    assert p is not None
    assert p.qty == 100          # signed, positive, correct magnitude
    assert p.side == "BUY"
    print("  OK H-12 T1: paper long -> qty == +100 (BUY)")


# ── Test 2 — short -> qty < 0 (the core bug; RED on unfixed, GREEN on fixed) ──

def test_h12_paper_short_qty_negative() -> None:
    adapter = _paper_adapter()
    _fill(adapter, "TATASTEEL", "SELL", 60)
    p = _pos(adapter, "TATASTEEL")
    assert p is not None
    # Unfixed adapter returns abs(-60)=+60 here -> this assertion FAILS (RED).
    assert p.qty == -60          # signed, negative
    assert p.qty < 0
    assert p.side == "SELL"
    print("  OK H-12 T2: paper short -> qty == -60 (SELL)  [RED before fix]")


# ── Test 3 — flat -> no row / qty == 0 (matches live's !=0 filter) ────────────

def test_h12_paper_flat_no_row() -> None:
    adapter = _paper_adapter()
    _fill(adapter, "INFY", "BUY", 50)
    _fill(adapter, "INFY", "SELL", 50)          # net 0 -> book pops the symbol
    assert _pos(adapter, "INFY") is None         # no row, like live (quantity!=0)
    assert broker_net_qty(adapter, "INFY") == 0  # consumer sees flat
    print("  OK H-12 T3: paper flat -> no position row, net == 0")


# ── Test 4 — partial exit -> residual signed (+60) ───────────────────────────

def test_h12_paper_partial_exit_residual_signed() -> None:
    adapter = _paper_adapter()
    _fill(adapter, "SBIN", "BUY", 100)
    _fill(adapter, "SBIN", "SELL", 40)           # real book net = 100 - 40 = 60
    p = _pos(adapter, "SBIN")
    assert p is not None
    assert p.qty == 60                            # residual, signed positive
    assert p.side == "BUY"
    print("  OK H-12 T4: paper long 100 - exit 40 -> residual qty == +60")


# ── Test 5 — determine_close_direction on paper matches live's signed input ──

def test_h12_determine_close_direction_matches_live() -> None:
    adapter = _paper_adapter()

    # paper SHORT: unfixed -> broker_net_qty sums abs=+60 -> ("SELL",60) (doubles
    # the short). Fixed -> net=-60 -> ("BUY",60). RED before fix, GREEN after.
    _fill(adapter, "WIPRO", "SELL", 60)
    assert determine_close_direction(adapter, "WIPRO", "SELL", 60) == ("BUY", 60)

    # paper LONG: net=+100 -> ("SELL",100).
    _fill(adapter, "LT", "BUY", 100)
    assert determine_close_direction(adapter, "LT", "BUY", 100) == ("SELL", 100)

    # Parity: the paper decisions must equal what live produces for the SAME
    # signed positions. A tiny stub returning live's signed contract:
    class _LiveLike:
        def __init__(self, positions):
            self._p = positions

        def get_positions(self):
            return self._p

    live_short = _LiveLike([Position(symbol="WIPRO", qty=-60, avg_price=500.0,
                                     product="MIS", side="SELL")])
    live_long = _LiveLike([Position(symbol="LT", qty=100, avg_price=500.0,
                                    product="MIS", side="BUY")])
    assert determine_close_direction(adapter, "WIPRO", "SELL", 60) == \
        determine_close_direction(live_short, "WIPRO", "SELL", 60)
    assert determine_close_direction(adapter, "LT", "BUY", 100) == \
        determine_close_direction(live_long, "LT", "BUY", 100)
    print("  OK H-12 T5: determine_close_direction paper==live "
          "(short->BUY, long->SELL)  [short RED before fix]")


if __name__ == "__main__":
    test_h12_paper_long_qty_positive()
    test_h12_paper_short_qty_negative()
    test_h12_paper_flat_no_row()
    test_h12_paper_partial_exit_residual_signed()
    test_h12_determine_close_direction_matches_live()
    print("\nAll H-12 tests passed.")
