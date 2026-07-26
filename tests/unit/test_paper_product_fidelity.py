"""tests/unit/test_paper_product_fidelity.py -- SK-A (26-Jul-2026).

The paper adapter must report a position under the product it was actually ORDERED
with, not a constant.

ROOT CAUSE (pre-fix): `_synth_fill` wrote the paper position book with a literal
`"product": "MIS"` (zerodha_adapter.py:2165), so EVERY paper position -- CNC, CO,
anything -- surfaced through `get_positions()` as MIS. The value was already in hand:
`_paper_place_order` records `"product": broker_code or ""` on the very same
`_paper_fills` record (:1985), and `_synth_fill` reads that record three lines above
the hardcode (:2136).

⭐⭐ IT BLINDS TWO SAFETY MECHANISMS, IN PAPER ONLY (live is correct):

  1. FIX-015 / EOD6 -- eod_squareoff.py:1069-1073 builds broker_qty from positions
     filtered to ("MIS","CO"), and a symbol absent from broker_qty is SKIPPED
     (:1093). A delivery position is therefore exempt from the 15:17 square-off...
     in live. In paper it reported MIS, entered broker_qty, and got squared.
     ⇒ "paper-proven" -- the stated gate for delivery going live -- could not prove
     the one thing it had to.

  2. H-5 -- kill_switch.py:1588 derives the HARD_KILL orphan-sweep intent from
     `pos.product` ("Kite nets per product, so an orphan CNC position swept with an
     MIS (intent=INTRADAY) exit does NOT offset it: the CNC position stays AND a
     fresh naked MIS short is created"). In paper that field was always MIS, so the
     sweep always chose INTRADAY -- exactly the bug H-5 exists to prevent.

⚠️ AND THE FIXTURES COULD NOT TELL. Both mechanisms are already tested -- but against
hand-built objects that bypass the adapter: `test_fix015_delivery_positions_excluded_
from_broker_qty` uses `Position(product="CNC")` off a MagicMock; `test_h5_killswitch_
sweep_product` uses `SimpleNamespace(product="CNC")`. Those prove the CONSUMER is
correct GIVEN CNC input. Neither can prove the paper FEED ever produces it. The
mechanisms were right, the feed was wrong, and no existing test spanned the two.
These tests span it: they drive the REAL paper adapter and assert against the REAL
filter expression and the REAL EOD fire.

PARITY (Rule #5): live has always been correct -- `get_positions()`'s live branch
reads kite's own `product` field per position. This fix brings PAPER into agreement
with LIVE; it does not change live behaviour, and there is no live code in the
assertion path here (paper mode never touches kite).

RED/GREEN on the unfixed adapter:
  RED  : cnc_reports_cnc, co_reports_co, eod_filter_excludes_paper_cnc,
         paper_cnc_survives_the_1517_squareoff, h5_sweep_intent_is_delivery
  GREEN: every MIS test (the A3 regression guard -- 423 trades of history are MIS
         and the EOD path depends on that filter, so MIS must be proven unchanged
         BEFORE CNC is proven to work)

Run: python -m pytest tests/unit/test_paper_product_fidelity.py -v
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock

import pytest

from broker.cost_calculator import CostCalculator
from broker.order_state_machine import OrderStateMachine
from broker.product_resolver import ProductResolver
from broker.rate_limiter import RateLimiter
from broker.zerodha_adapter import ZerodhaAdapter
from capital.kill_switch import KillState
from core.events import EventBus
from core.market_windows import MarketWindows
from core.state_store import StateStore
from orders.eod_squareoff import EodSquareoff

_IST = timezone(timedelta(hours=5, minutes=30), "IST")
_FIXED_TEST_DATE = date(2026, 4, 20)  # Monday, trading day


def _ist(h: int, m: int, s: int = 0) -> datetime:
    return datetime(_FIXED_TEST_DATE.year, _FIXED_TEST_DATE.month,
                    _FIXED_TEST_DATE.day, h, m, s, tzinfo=_IST)


# ── real-ish paper adapter wiring (mirrors test_h12_paper_positions_signed) ────

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
    """A REAL paper-mode adapter with instant synth fills. delivery_enabled=True and
    force_intraday_only left at its default (False) so a DELIVERY intent genuinely
    resolves to CNC rather than being coerced to MIS upstream."""
    return ZerodhaAdapter(
        kite_client=MagicMock(),                 # unused in paper mode
        rate_limiter=RateLimiter(_LIMITS, max_wait_sec=5.0),
        product_resolver=ProductResolver(_PRODUCT_MAP),
        cost_calculator=CostCalculator(_COSTS),
        state_machine=OrderStateMachine(),
        logger=logging.getLogger("test_paper_product_fidelity"),
        paper_mode=True,
        paper_capital=1_000_000.0,
        bus=None,
        paper_auto_fill_delay_sec=0.0,
        paper_ltp_gating_enabled=False,
        delivery_enabled=True,
    )


def _book_qty(adapter: ZerodhaAdapter, symbol: str) -> int:
    """Signed net from the paper book, read under the writer's lock. Deliberately
    reads `qty` (never `product`) so the arrange step settles identically on the
    unfixed and fixed adapter."""
    with adapter._paper_fills_lock:
        info = adapter._paper_positions.get(symbol)
        return int(info["qty"]) if info else 0


def _fill(adapter: ZerodhaAdapter, symbol: str, intent: str, qty: int = 10,
          side: str = "BUY", price: float = 500.0) -> None:
    """Drive one REAL paper fill through production place_order -> _synth_fill and
    wait (bounded) until the book settles."""
    before = _book_qty(adapter, symbol)
    expected = before + qty if side == "BUY" else before - qty
    adapter.place_order(symbol=symbol, side=side, qty=qty, price=price,
                        order_type="LIMIT", intent=intent)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if _book_qty(adapter, symbol) == expected:
            return
        time.sleep(0.005)
    raise AssertionError(f"paper book for {symbol} did not settle to {expected}")


def _pos(adapter: ZerodhaAdapter, symbol: str):
    for p in adapter.get_positions():
        if p.symbol == symbol:
            return p
    return None


def _eod_broker_qty(adapter: ZerodhaAdapter) -> dict:
    """The EOD filter, verbatim from eod_squareoff.py:1069-1073. Reproduced (not
    imported) because it is an inline dict-comprehension; the end-to-end test below
    exercises the real one."""
    return {
        p.symbol: abs(int(p.qty))
        for p in adapter.get_positions()
        if int(p.qty) != 0 and p.product in ("MIS", "CO")
    }


# ── A3: the regression guard — MIS must be unchanged, proven FIRST ──────────────

def test_paper_mis_position_still_reports_mis() -> None:
    """423 trades of history are MIS and the EOD path depends on that filter."""
    adapter = _paper_adapter()
    _fill(adapter, "RELIANCE", "INTRADAY")
    p = _pos(adapter, "RELIANCE")
    assert p is not None and p.product == "MIS"


def test_eod_filter_still_includes_paper_mis() -> None:
    """The MIS position must still enter broker_qty, or EOD would stop squaring it."""
    adapter = _paper_adapter()
    _fill(adapter, "RELIANCE", "INTRADAY")
    assert "RELIANCE" in _eod_broker_qty(adapter)


def test_mis_and_cnc_coexist_without_the_mis_one_changing() -> None:
    """Adding a CNC position must not perturb the MIS one's classification."""
    adapter = _paper_adapter()
    _fill(adapter, "RELIANCE", "INTRADAY")
    _fill(adapter, "INFY", "DELIVERY")
    bq = _eod_broker_qty(adapter)
    assert "RELIANCE" in bq, "the MIS position must still be squared"
    assert "INFY" not in bq, "the CNC position must be exempt"


# ── A2: the adapter reflects the order it was given ────────────────────────────

def test_paper_cnc_position_reports_cnc() -> None:
    """RED pre-fix: reported MIS."""
    adapter = _paper_adapter()
    _fill(adapter, "INFY", "DELIVERY")
    p = _pos(adapter, "INFY")
    assert p is not None
    assert p.product == "CNC", (
        "a paper CNC position must report CNC -- the product is already on the "
        "_paper_fills record written by _paper_place_order"
    )


def test_paper_co_position_reports_co() -> None:
    """RED pre-fix. The hardcode mislabelled CO as MIS too. Behaviourally masked for
    EOD (the filter accepts both) but it is the same defect and H-5 reads it."""
    adapter = _paper_adapter()
    _fill(adapter, "TCS", "COVER_ORDER")
    p = _pos(adapter, "TCS")
    assert p is not None and p.product == "CO"


# ── A4: the assertion that was previously impossible ──────────────────────────

def test_eod_filter_excludes_paper_cnc() -> None:
    """⭐ RED pre-fix. A symbol absent from broker_qty is SKIPPED by EOD
    (eod_squareoff.py:1093), so absence here IS the exemption."""
    adapter = _paper_adapter()
    _fill(adapter, "INFY", "DELIVERY")
    assert "INFY" not in _eod_broker_qty(adapter), (
        "a paper CNC position must be exempt from the EOD square-off filter"
    )


def test_paper_cnc_survives_the_1517_squareoff() -> None:
    """⭐⭐ END-TO-END, through the REAL EodSquareoff and the REAL paper adapter:
    a delivery position must still be held after the 15:17 fire.

    RED pre-fix: the position reports MIS, enters broker_qty, and an exit order is
    placed against it."""
    adapter = _paper_adapter()
    _fill(adapter, "INFY", "DELIVERY", qty=10)
    assert _book_qty(adapter, "INFY") == 10, "arrange: the CNC position is open"

    row = MagicMock()
    row.__getitem__ = lambda _self, key: {
        "trade_id": "trd_cnc", "signal_id": "sig_cnc", "symbol": "INFY",
        "direction": "LONG", "qty_filled": 10, "order_protocol": "LIMIT_TRIPLE",
        "entry_broker_order_id": "", "entry_variety": "regular",
    }[key]

    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = [row]
    store.get_eod_squareoff_log_for_date.return_value = None

    ks = MagicMock()
    ks.is_active.return_value = False
    ks.current_state.return_value = KillState.INACTIVE

    eod = EodSquareoff(
        adapter=adapter, state_store=store, fund_manager=MagicMock(),
        state_machine=OrderStateMachine(), bus=MagicMock(spec=EventBus),
        market_windows=MarketWindows(), time_authority=None, kill_switch=ks,
        logger=logging.getLogger("test_paper_cnc_eod"),
        order_monitor=MagicMock(), inter_order_delay_ms=0, exit_protocol="MARKET",
    )

    eod.check_and_fire(_ist(15, 17))

    assert _book_qty(adapter, "INFY") == 10, (
        "the CNC position was squared off by the 15:17 EOD fire -- EOD6 says "
        "delivery positions are never touched"
    )


# ── the second blinded consumer: H-5's sweep product ──────────────────────────

def test_h5_sweep_intent_for_a_paper_cnc_position_is_delivery() -> None:
    """⭐ RED pre-fix. kill_switch.py:1588 maps the position's product to a sweep
    intent; an orphan CNC swept as INTRADAY does not offset it and creates a fresh
    naked MIS short (H-5's own rationale)."""
    from capital.kill_switch import _PRODUCT_TO_INTENT

    adapter = _paper_adapter()
    _fill(adapter, "INFY", "DELIVERY")
    p = _pos(adapter, "INFY")
    assert p is not None
    sweep_intent = _PRODUCT_TO_INTENT.get(getattr(p, "product", "") or "", "INTRADAY")
    assert sweep_intent == "DELIVERY", (
        "sweeping a CNC position as INTRADAY leaves the CNC position open AND "
        "opens a naked MIS short"
    )


# ── the fallback, where nothing is known ──────────────────────────────────────

@pytest.mark.parametrize("intent,expected", [
    ("INTRADAY", "MIS"), ("DELIVERY", "CNC"), ("COVER_ORDER", "CO"),
])
def test_paper_position_product_is_always_a_usable_value(intent: str, expected: str) -> None:
    """⚠️ The book must never hold an EMPTY product.

    `get_positions()` reads `info.get("product", "MIS")` (zerodha_adapter.py:1109) —
    that default fires only when the KEY IS MISSING, not when the value is empty. An
    empty string would therefore surface as "" and fall OUT of the ("MIS","CO") EOD
    filter, i.e. be silently CARRIED — the UNSAFE direction. The writer normalises
    with `or "MIS"` so an unknown product degrades to *squared*, never to a carry.

    A default is legitimate only where nothing is known; MIS is the safe choice
    because being squared is recoverable and being silently carried is not."""
    adapter = _paper_adapter()
    _fill(adapter, "RELIANCE", intent)
    p = _pos(adapter, "RELIANCE")
    assert p is not None
    assert p.product == expected
    assert p.product, "an empty product would be silently exempt from the EOD filter"
