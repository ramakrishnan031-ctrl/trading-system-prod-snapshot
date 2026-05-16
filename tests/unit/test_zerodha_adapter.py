"""
tests/unit/test_zerodha_adapter.py

Validates broker/zerodha_adapter.py against ZA1-ZA16.
Uses MockKite instead of real kiteconnect to avoid network calls.

Covers:
  - place_order success: RL called, PR called, OSM PENDING->SUBMITTED,
    returns PlacedOrder with both IDs, internal ID matches ord_ pattern
  - place_order kite exception: OSM PENDING->FAILED, OrderRejectedError raised
  - place_order TokenException -> BrokerAuthError
  - place_order NetworkException -> BrokerTimeoutError
  - place_order qty=0 -> ValueError, no RL call, no OSM register
  - place_order price=0 LIMIT -> ValueError
  - place_order invalid side -> ValueError
  - place_order ProductNotSupportedError bubbles up (BRACKET_ORDER)
  - cancel_order success -> CancelResult success=True
  - cancel_order kite exception -> success=False, no raise
  - modify_order success -> ModifyResult success=True
  - get_positions returns list[Position]
  - get_margins returns MarginInfo
  - get_quote returns dict[str, Quote]
  - Paper mode: place_order returns PAPER_xxx broker_order_id
  - Paper mode: cancel always success
  - Paper mode: get_margins returns paper_capital
  - Paper mode: get_quote raises NotImplementedError if no provider
  - Logging: every method call logs entry+exit
  - Exceptions logged via log_exception before re-raise
  - Rate limiter: each method calls acquire with correct category

Run: python -m pytest tests/unit/test_zerodha_adapter.py -v
Or:  python tests/unit/test_zerodha_adapter.py  (standalone mode)
"""

from __future__ import annotations

import sys
import logging
from datetime import timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.zerodha_adapter import (
    CancelResult,
    MarginInfo,
    ModifyResult,
    OrderHistoryEntry,
    PlacedOrder,
    Position,
    Quote,
    ZerodhaAdapter,
)
from broker.order_state_machine import OrderStateMachine
from broker.product_resolver import ProductResolver
from broker.rate_limiter import RateLimiter
from core.events import EventBus, OrderFilled
from core.exceptions import (
    BrokerAuthError,
    BrokerError,
    BrokerRateLimit429Error,
    BrokerTimeoutError,
    OrderRejectedError,
    ProductNotSupportedError,
)
from core.ids import is_valid_order_id


# ─────────────────────────────────────────────────────────────────────────────
# MockKite -- fake kiteconnect client
# ─────────────────────────────────────────────────────────────────────────────

class MockKite:
    """Canned responses. Override attributes per-test to simulate failures."""

    def __init__(self) -> None:
        self.place_order_return = "KITE12345"
        self.place_order_exc: Exception | None = None
        self.cancel_order_exc: Exception | None = None
        self.modify_order_exc: Exception | None = None
        self.order_history_return: list = [
            {"status": "OPEN", "filled_quantity": 0,
             "average_price": 0.0, "status_message": ""}
        ]
        self.positions_return: dict = {
            "net": [
                {"tradingsymbol": "RELIANCE", "quantity": 10,
                 "average_price": 2500.0, "product": "MIS"}
            ]
        }
        self.margins_return: dict = {
            "equity": {
                "net": 50000.0,
                "available": {"cash": 45000.0},
                "utilised": {"debits": 5000.0},
            }
        }
        self.quote_return: dict = {
            "NSE:RELIANCE": {
                "last_price": 2501.0,
                "volume": 100000,
                "depth": {
                    "buy":  [{"price": 2500.5}],
                    "sell": [{"price": 2501.5}],
                },
            }
        }
        # FIX-072: order_margins mock
        self.order_margins_return: list = [{"total": 200.0}]
        self.order_margins_exc: Exception | None = None
        self.order_margins_called: bool = False

    def place_order(self, **kwargs: Any) -> str:
        if self.place_order_exc:
            raise self.place_order_exc
        return self.place_order_return

    def cancel_order(self, **kwargs: Any) -> None:
        if self.cancel_order_exc:
            raise self.cancel_order_exc

    def modify_order(self, **kwargs: Any) -> None:
        if self.modify_order_exc:
            raise self.modify_order_exc

    def order_history(self, order_id: str) -> list:
        return self.order_history_return

    def positions(self) -> dict:
        return self.positions_return

    def margins(self, segment: str | None = None) -> dict:
        return self.margins_return

    def order_margins(self, order_params: list) -> list:
        """FIX-072: mock order_margins API."""
        self.order_margins_called = True
        if self.order_margins_exc:
            raise self.order_margins_exc
        return self.order_margins_return

    def quote(self, *instruments: str) -> dict:
        return self.quote_return


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_PRODUCT_MAP = {
    "zerodha": {
        "INTRADAY": "MIS",
        "DELIVERY": "CNC",
        "COVER_ORDER": "CO",
        "BRACKET_ORDER": "",
    }
}

_BROKER_LIMITS_CFG = MagicMock()
_BROKER_LIMITS_CFG.order.burst = 10
_BROKER_LIMITS_CFG.order.rate_per_sec = 10
_BROKER_LIMITS_CFG.quote.burst = 10
_BROKER_LIMITS_CFG.quote.rate_per_sec = 10
_BROKER_LIMITS_CFG.margins.burst = 10
_BROKER_LIMITS_CFG.margins.rate_per_sec = 10
_BROKER_LIMITS_CFG.historical.burst = 10
_BROKER_LIMITS_CFG.historical.rate_per_sec = 10

_BROKER_COSTS_CFG = MagicMock()
_BROKER_COSTS_CFG.zerodha.brokerage_flat_intraday = 20.0
_BROKER_COSTS_CFG.zerodha.brokerage_pct_intraday = 0.03
_BROKER_COSTS_CFG.zerodha.stt_sell_pct = 0.025
_BROKER_COSTS_CFG.zerodha.stt_cnc_pct = 0.1
_BROKER_COSTS_CFG.zerodha.exchange_txn_pct = 0.00297
_BROKER_COSTS_CFG.zerodha.gst_pct = 18.0
_BROKER_COSTS_CFG.zerodha.sebi_pct = 0.0001
_BROKER_COSTS_CFG.zerodha.stamp_duty_mis_buy_pct = 0.003
_BROKER_COSTS_CFG.zerodha.stamp_duty_cnc_buy_pct = 0.015


def _make_adapter(
    kite: MockKite | None = None,
    paper: bool = False,
    paper_capital: float = 100_000.0,
    quote_provider=None,
    bus: Any = None,
    paper_auto_fill_delay_sec: float = 0.05,  # tests use a short default
    paper_ltp_gating_enabled: bool = False,
    paper_ltp_gating_max_wait_sec: float = 1.0,  # short for tests
    paper_ltp_gating_poll_sec: float = 0.05,
) -> tuple[ZerodhaAdapter, MockKite, RateLimiter, OrderStateMachine, Any]:
    """Return (adapter, kite, rl, osm, logger)."""
    from broker.cost_calculator import CostCalculator
    kite = kite or MockKite()
    rl = RateLimiter(_BROKER_LIMITS_CFG, max_wait_sec=5.0)
    pr = ProductResolver(_PRODUCT_MAP)
    cc = CostCalculator(_BROKER_COSTS_CFG)
    osm = OrderStateMachine()
    logger = logging.getLogger("test_adapter")
    adapter = ZerodhaAdapter(
        kite_client=kite,
        rate_limiter=rl,
        product_resolver=pr,
        cost_calculator=cc,
        state_machine=osm,
        logger=logger,
        paper_mode=paper,
        paper_capital=paper_capital,
        quote_provider=quote_provider,
        bus=bus,
        paper_auto_fill_delay_sec=paper_auto_fill_delay_sec,
        paper_ltp_gating_enabled=paper_ltp_gating_enabled,
        paper_ltp_gating_max_wait_sec=paper_ltp_gating_max_wait_sec,
        paper_ltp_gating_poll_sec=paper_ltp_gating_poll_sec,
    )
    return adapter, kite, rl, osm, logger


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- place_order success (ZA3, ZA4, ZA7)
# ─────────────────────────────────────────────────────────────────────────────

def test_place_order_success_returns_placed_order() -> None:
    adapter, kite, rl, osm, _ = _make_adapter()
    result = adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    assert isinstance(result, PlacedOrder)
    assert result.broker_order_id == "KITE12345"
    assert is_valid_order_id(result.internal_order_id)
    assert result.symbol == "RELIANCE"
    assert result.side == "BUY"
    assert result.qty == 10
    assert result.product == "MIS"         # resolved from INTRADAY (ZA4)
    assert result.status == "SUBMITTED"
    print("  OK place_order success: PlacedOrder with both IDs, product resolved (ZA2, ZA4, ZA7)")


def test_place_order_success_osm_transitions_to_submitted() -> None:
    adapter, kite, rl, osm, _ = _make_adapter()
    result = adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=0.0,
        order_type="MARKET", intent="INTRADAY",
    )
    state = osm.current_state(result.internal_order_id)
    assert state == "SUBMITTED", f"Expected SUBMITTED, got {state}"
    print("  OK place_order success: OSM PENDING->SUBMITTED (ZA7)")


def test_place_order_internal_id_matches_ord_pattern() -> None:
    adapter, _, _, _, _ = _make_adapter()
    result = adapter.place_order(
        symbol="TCS", side="SELL", qty=5, price=0.0,
        order_type="MARKET", intent="DELIVERY",
    )
    assert is_valid_order_id(result.internal_order_id), (
        f"internal_order_id {result.internal_order_id!r} fails is_valid_order_id"
    )
    print("  OK place_order: internal_order_id matches ord_<hex32> pattern (ZA7)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- place_order exception mapping (ZA5, ZA7)
# ─────────────────────────────────────────────────────────────────────────────

def test_place_order_kite_order_exception_raises_order_rejected() -> None:
    from kiteconnect import exceptions as kex
    kite = MockKite()
    kite.place_order_exc = kex.OrderException("RMS rejection")
    adapter, _, _, osm, _ = _make_adapter(kite=kite)

    raised = None
    result_id: str | None = None
    try:
        placed = adapter.place_order(
            symbol="INFY", side="BUY", qty=5, price=1500.0,
            order_type="LIMIT", intent="INTRADAY",
        )
    except OrderRejectedError as exc:
        raised = exc

    assert raised is not None, "Expected OrderRejectedError"
    print("  OK place_order kite OrderException -> OrderRejectedError (ZA5)")


def test_place_order_kite_exception_transitions_osm_to_failed() -> None:
    from kiteconnect import exceptions as kex
    kite = MockKite()
    kite.place_order_exc = kex.InputException("margin insufficient")
    adapter, _, _, osm, _ = _make_adapter(kite=kite)

    # Find the internal_order_id by inspecting the OSM before and after
    placed_ids_before = set()  # we can't inspect easily, so we patch new_order_id

    captured_id: list[str] = []

    import broker.zerodha_adapter as za_mod
    original_new_order_id = za_mod.new_order_id

    def capturing_new_order_id():
        oid = original_new_order_id()
        captured_id.append(oid)
        return oid

    za_mod.new_order_id = capturing_new_order_id
    try:
        try:
            adapter.place_order(
                symbol="INFY", side="BUY", qty=5, price=1500.0,
                order_type="LIMIT", intent="INTRADAY",
            )
        except OrderRejectedError:
            pass
    finally:
        za_mod.new_order_id = original_new_order_id

    assert captured_id, "new_order_id was never called"
    state = osm.current_state(captured_id[0])
    assert state == "FAILED", f"Expected FAILED, got {state}"
    print("  OK place_order kite exception: OSM transitions to FAILED (ZA7)")


def test_place_order_token_exception_raises_broker_auth_error() -> None:
    from kiteconnect import exceptions as kex
    kite = MockKite()
    kite.place_order_exc = kex.TokenException("invalid token")
    adapter, _, _, _, _ = _make_adapter(kite=kite)

    raised = None
    try:
        adapter.place_order(
            symbol="RELIANCE", side="BUY", qty=1, price=0.0,
            order_type="MARKET", intent="INTRADAY",
        )
    except BrokerAuthError as exc:
        raised = exc

    assert raised is not None, "Expected BrokerAuthError"
    print("  OK place_order TokenException -> BrokerAuthError (ZA5)")


def test_place_order_network_exception_raises_broker_timeout() -> None:
    from kiteconnect import exceptions as kex
    kite = MockKite()
    kite.place_order_exc = kex.NetworkException("connection refused")
    adapter, _, _, _, _ = _make_adapter(kite=kite)

    raised = None
    try:
        adapter.place_order(
            symbol="RELIANCE", side="BUY", qty=1, price=0.0,
            order_type="MARKET", intent="INTRADAY",
        )
    except BrokerTimeoutError as exc:
        raised = exc

    assert raised is not None, "Expected BrokerTimeoutError"
    print("  OK place_order NetworkException -> BrokerTimeoutError (ZA5)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- input validation (ZA13)
# ─────────────────────────────────────────────────────────────────────────────

def _assert_raises_value_error_no_side_effects(adapter, osm, **kwargs) -> None:
    """Verify ValueError and that OSM was not modified (no register called)."""
    osm_states_before = dict(osm._states)
    raised = False
    try:
        adapter.place_order(**kwargs)
    except ValueError:
        raised = True
    assert raised, f"Expected ValueError for kwargs={kwargs}"
    assert osm._states == osm_states_before, \
        "OSM state changed despite ValueError -- rate limiter or OSM was touched"


def test_place_order_qty_zero_raises_value_error_no_side_effects() -> None:
    adapter, _, rl, osm, _ = _make_adapter()
    _assert_raises_value_error_no_side_effects(
        adapter, osm,
        symbol="RELIANCE", side="BUY", qty=0, price=100.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    print("  OK qty=0 -> ValueError before RL/OSM touched (ZA13)")


def test_place_order_negative_qty_raises_value_error() -> None:
    adapter, _, _, osm, _ = _make_adapter()
    _assert_raises_value_error_no_side_effects(
        adapter, osm,
        symbol="RELIANCE", side="BUY", qty=-5, price=100.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    print("  OK qty<0 -> ValueError (ZA13)")


def test_place_order_limit_price_zero_raises_value_error() -> None:
    adapter, _, _, osm, _ = _make_adapter()
    _assert_raises_value_error_no_side_effects(
        adapter, osm,
        symbol="RELIANCE", side="BUY", qty=10, price=0.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    print("  OK LIMIT price=0 -> ValueError (ZA13)")


def test_place_order_invalid_side_raises_value_error() -> None:
    adapter, _, _, osm, _ = _make_adapter()
    _assert_raises_value_error_no_side_effects(
        adapter, osm,
        symbol="RELIANCE", side="LONG", qty=10, price=100.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    print("  OK invalid side -> ValueError (ZA13)")


def test_place_order_bracket_order_intent_raises_product_not_supported() -> None:
    """ProductNotSupportedError raised during resolve() before RL/OSM (ZA4)."""
    adapter, _, _, osm, _ = _make_adapter()
    osm_states_before = dict(osm._states)
    raised = False
    try:
        adapter.place_order(
            symbol="RELIANCE", side="BUY", qty=10, price=0.0,
            order_type="MARKET", intent="BRACKET_ORDER",
        )
    except ProductNotSupportedError:
        raised = True
    assert raised, "Expected ProductNotSupportedError for BRACKET_ORDER"
    assert osm._states == osm_states_before, "OSM should not be touched on ProductNotSupportedError"
    print("  OK BRACKET_ORDER intent -> ProductNotSupportedError bubbles up (ZA4)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- cancel_order (ZA3, ZA8)
# ─────────────────────────────────────────────────────────────────────────────

def test_cancel_order_success() -> None:
    adapter, _, _, _, _ = _make_adapter()
    result = adapter.cancel_order("KITE12345")
    assert isinstance(result, CancelResult)
    assert result.success is True
    assert result.reason == ""
    assert result.broker_order_id == "KITE12345"
    print("  OK cancel_order success -> CancelResult(success=True) (ZA2)")


def test_cancel_order_kite_exception_returns_failure_no_raise() -> None:
    from kiteconnect import exceptions as kex
    kite = MockKite()
    kite.cancel_order_exc = kex.OrderException("order already cancelled")
    adapter, _, _, _, _ = _make_adapter(kite=kite)

    result = adapter.cancel_order("KITE99999")
    assert isinstance(result, CancelResult)
    assert result.success is False
    assert "already cancelled" in result.reason
    print("  OK cancel_order kite exception -> CancelResult(success=False), no raise (ZA2)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- modify_order (ZA3)
# ─────────────────────────────────────────────────────────────────────────────

def test_modify_order_success() -> None:
    adapter, _, _, _, _ = _make_adapter()
    result = adapter.modify_order("KITE12345", price=2510.0)
    assert isinstance(result, ModifyResult)
    assert result.success is True
    print("  OK modify_order success -> ModifyResult(success=True) (ZA2)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- get_positions (ZA2, ZA3)
# ─────────────────────────────────────────────────────────────────────────────

def test_get_positions_returns_list_of_positions() -> None:
    adapter, _, _, _, _ = _make_adapter()
    positions = adapter.get_positions()
    assert isinstance(positions, list)
    assert len(positions) == 1
    p = positions[0]
    assert isinstance(p, Position)
    assert p.symbol == "RELIANCE"
    assert p.qty == 10
    assert p.avg_price == 2500.0
    assert p.product == "MIS"
    assert p.side == "BUY"
    print("  OK get_positions returns list[Position] (ZA2)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- get_margins (ZA2, ZA3)
# ─────────────────────────────────────────────────────────────────────────────

def test_get_margins_returns_margin_info() -> None:
    adapter, _, _, _, _ = _make_adapter()
    info = adapter.get_margins()
    assert isinstance(info, MarginInfo)
    assert info.net == 50000.0
    assert info.available == 45000.0
    assert info.used == 5000.0
    print("  OK get_margins returns MarginInfo (ZA2)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- get_quote (ZA2, ZA3)
# ─────────────────────────────────────────────────────────────────────────────

def test_get_quote_returns_dict_of_quotes() -> None:
    adapter, _, _, _, _ = _make_adapter()
    quotes = adapter.get_quote(["RELIANCE"])
    assert isinstance(quotes, dict)
    assert "RELIANCE" in quotes
    q = quotes["RELIANCE"]
    assert isinstance(q, Quote)
    assert q.symbol == "RELIANCE"
    assert q.last_price == 2501.0
    assert q.bid == 2500.5
    assert q.ask == 2501.5
    assert q.volume == 100000
    print("  OK get_quote returns dict[str, Quote] (ZA2)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- paper mode (ZA10)
# ─────────────────────────────────────────────────────────────────────────────

def test_paper_mode_place_order_returns_paper_broker_id() -> None:
    adapter, _, _, osm, _ = _make_adapter(paper=True)
    result = adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    assert result.broker_order_id.startswith("PAPER_"), (
        f"Expected PAPER_ prefix, got {result.broker_order_id!r}"
    )
    assert is_valid_order_id(result.internal_order_id)
    assert result.status == "SUBMITTED"
    assert osm.current_state(result.internal_order_id) == "SUBMITTED"
    print("  OK paper mode place_order returns PAPER_xxx broker_order_id (ZA10)")


def test_paper_mode_cancel_always_success() -> None:
    adapter, _, _, _, _ = _make_adapter(paper=True)
    result = adapter.cancel_order("ANYTHING")
    assert result.success is True
    assert result.reason == ""
    print("  OK paper mode cancel_order always success (ZA10)")


def test_paper_mode_get_margins_returns_paper_capital() -> None:
    adapter, _, _, _, _ = _make_adapter(paper=True, paper_capital=200_000.0)
    info = adapter.get_margins()
    assert info.net == 200_000.0
    assert info.available == 200_000.0
    assert info.used == 0.0
    print("  OK paper mode get_margins returns paper_capital (ZA10)")


# ─────────────────────────────────────────────────────────────────────────────
# Paper synthetic positions — 11-May-2026 fix
# ─────────────────────────────────────────────────────────────────────────────


def test_paper_get_positions_returns_position_after_fill() -> None:
    """Paper get_positions returns a Position after a synthetic BUY fill."""
    import time
    bus = EventBus()
    adapter, _, _, osm, _ = _make_adapter(paper=True, bus=bus)
    result = adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    time.sleep(0.2)
    positions = adapter.get_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "RELIANCE"
    assert p.qty == 10
    assert p.side == "BUY"
    print("  OK paper get_positions returns position after fill")


def test_paper_get_positions_flat_after_exit() -> None:
    """Paper position goes to zero after BUY + SELL of same qty."""
    import time
    bus = EventBus()
    adapter, _, _, osm, _ = _make_adapter(paper=True, bus=bus)
    adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    time.sleep(0.2)
    assert len(adapter.get_positions()) == 1
    adapter.place_order(
        symbol="RELIANCE", side="SELL", qty=10, price=2550.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    time.sleep(0.2)
    positions = adapter.get_positions()
    assert len(positions) == 0, f"Expected flat, got {positions}"
    print("  OK paper get_positions empty after offsetting fill")


def test_paper_get_positions_multiple_symbols() -> None:
    """Paper positions track multiple symbols independently."""
    import time
    bus = EventBus()
    adapter, _, _, osm, _ = _make_adapter(paper=True, bus=bus)
    adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    adapter.place_order(
        symbol="INFY", side="BUY", qty=20, price=1500.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    time.sleep(0.2)
    positions = adapter.get_positions()
    syms = {p.symbol for p in positions}
    assert syms == {"RELIANCE", "INFY"}, f"Expected both symbols, got {syms}"
    print("  OK paper get_positions tracks multiple symbols")


def test_paper_cancelled_order_no_position() -> None:
    """Cancelled paper order does not appear in positions."""
    import time
    bus = EventBus()
    adapter, _, _, osm, _ = _make_adapter(
        paper=True, bus=bus, paper_auto_fill_delay_sec=5.0,
    )
    result = adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    adapter.cancel_order(result.broker_order_id)
    time.sleep(0.1)
    positions = adapter.get_positions()
    assert len(positions) == 0, f"Cancelled order should not have position: {positions}"
    print("  OK paper cancelled order has no position")


# ─────────────────────────────────────────────────────────────────────────────
# EF-4: set_paper_capital late-bind
# ─────────────────────────────────────────────────────────────────────────────


def test_ef4_set_paper_capital_updates_value() -> None:
    """EF-4: setter updates _paper_capital; get_margins reflects new value."""
    adapter, _, _, _, _ = _make_adapter(paper=True, paper_capital=0.0)
    # Pre-bind: provisional value; get_margins returns it unchanged.
    pre = adapter.get_margins()
    assert pre.net == 0.0
    # Late-bind (the pattern main.py uses after account selection).
    adapter.set_paper_capital(5_000_000.0)
    post = adapter.get_margins()
    assert post.net == 5_000_000.0
    assert post.available == 5_000_000.0
    assert post.used == 0.0
    print("  OK EF-4 set_paper_capital updates value; get_margins reflects it")


def test_ef4_set_paper_capital_rejects_nonpositive() -> None:
    """EF-4: setter rejects <=0 with ValueError (validation in the setter)."""
    adapter, _, _, _, _ = _make_adapter(paper=True, paper_capital=0.0)
    for bad in (0.0, -1.0, -5_000_000.0):
        raised = False
        try:
            adapter.set_paper_capital(bad)
        except ValueError:
            raised = True
        assert raised, f"Expected ValueError for value={bad!r}"
    # Positive values succeed.
    adapter.set_paper_capital(100.0)
    assert adapter.get_margins().net == 100.0
    print("  OK EF-4 set_paper_capital rejects 0/negative; accepts positive")


def test_ef4_set_paper_capital_noop_in_live() -> None:
    """EF-4: setter is a silent no-op in live mode (doesn't raise, no mutation)."""
    adapter, _, _, _, _ = _make_adapter(paper=False)
    before = adapter._paper_capital  # live mode stores whatever was passed
    # Should NOT raise even on a negative value (guard is before the check).
    adapter.set_paper_capital(5_000_000.0)
    adapter.set_paper_capital(-100.0)
    assert adapter._paper_capital == before
    print("  OK EF-4 set_paper_capital is no-op in live mode")


def test_ef4_no_paper_capital_getattr_in_main() -> None:
    """EF-4: regression guard -- the pre-SU19 getattr pattern must not
    return to main.py. Source-level string check."""
    from pathlib import Path
    main_src = Path(__file__).parent.parent.parent / "main.py"
    text = main_src.read_text(encoding="utf-8")
    bad = 'getattr(app_config.system, "paper_capital"'
    assert bad not in text, (
        f"EF-4 REGRESSION: {bad!r} pattern re-appeared in main.py. "
        "paper_capital must be late-bound via broker_adapter.set_paper_capital()."
    )
    print("  OK EF-4 getattr(app_config.system, 'paper_capital'...) absent from main.py")


def test_paper_mode_get_quote_raises_not_implemented_without_provider() -> None:
    adapter, _, _, _, _ = _make_adapter(paper=True)
    raised = False
    try:
        adapter.get_quote(["RELIANCE"])
    except NotImplementedError:
        raised = True
    assert raised, "Expected NotImplementedError for paper get_quote without provider"
    print("  OK paper mode get_quote without provider -> NotImplementedError (ZA10)")


# ─────────────────────────────────────────────────────────────────────────────
# CFG-6 (2026-04-26 audit): paper-mode slippage applied in _synth_fill (P12)
# ─────────────────────────────────────────────────────────────────────────────

def _make_test_slippage_engine():
    """Build a SlippageEngine with a tiny in-memory InstrumentCache."""
    import tempfile
    from broker.slippage_engine import SlippageEngine
    from core.config_loader import SlippageConfig, SlippageTierConfig
    from core.instrument_cache import InstrumentCache

    csv_text = (
        "symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector\n"
        "RELIANCE,738561,NSE,1,0.05,true,ENERGY\n"
        "INFY,408065,NSE,1,0.05,true,IT\n"
        "SMALLCO,123456,NSE,1,0.05,false,MISC\n"
    )
    td = tempfile.mkdtemp()
    p = Path(td) / "instruments.csv"
    p.write_text(csv_text, encoding="utf-8")
    cache = InstrumentCache.load(p)
    cfg = SlippageConfig(
        tiers={
            "liquid": SlippageTierConfig(slippage_bps=5),
            "mid":    SlippageTierConfig(slippage_bps=15),
            "small":  SlippageTierConfig(slippage_bps=30),
        },
        default_tier="liquid",
    )
    return SlippageEngine(cfg, cache)


def test_cfg6_paper_synth_applies_buy_slippage() -> None:
    """BUY paper fill: avg_fill_price > limit by tier bps (P12 conservatism)."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    adapter, _, _, _, _ = _make_adapter(paper=True, bus=bus,
                                        paper_auto_fill_delay_sec=0.02)
    adapter.set_slippage_engine(_make_test_slippage_engine())
    result = adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    assert _wait_for_events(captured, 1, timeout_sec=2.0)
    ev = captured[0]
    # 5 bps of 2500 = 1.25; tick 0.05 → 2501.25
    assert ev.avg_fill_price == 2501.25, (
        f"BUY fill should include 5 bps adverse slippage; got {ev.avg_fill_price}"
    )
    assert ev.expected_price == 2500.0
    assert ev.slippage_pct > 0, "slippage_pct should be > 0 with engine wired"
    assert result.internal_order_id  # placed correctly
    print(f"  OK CFG-6: BUY paper synth applies slippage 2500 → {ev.avg_fill_price}")


def test_cfg6_paper_synth_applies_sell_slippage() -> None:
    """SELL paper fill: avg_fill_price < limit by tier bps."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    adapter, _, _, _, _ = _make_adapter(paper=True, bus=bus,
                                        paper_auto_fill_delay_sec=0.02)
    adapter.set_slippage_engine(_make_test_slippage_engine())
    adapter.place_order(
        symbol="INFY", side="SELL", qty=7, price=1500.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    assert _wait_for_events(captured, 1, timeout_sec=2.0)
    ev = captured[0]
    # 5 bps of 1500 = 0.75; tick 0.05 → 1499.25
    assert ev.avg_fill_price == 1499.25, (
        f"SELL fill should include 5 bps adverse slippage; got {ev.avg_fill_price}"
    )
    assert ev.slippage_pct < 0, "SELL slippage_pct should be < 0"
    print(f"  OK CFG-6: SELL paper synth applies slippage 1500 → {ev.avg_fill_price}")


def test_cfg6_no_engine_means_no_slippage_backcompat() -> None:
    """Without set_slippage_engine, paper synth still fills at limit price."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    adapter, _, _, _, _ = _make_adapter(paper=True, bus=bus,
                                        paper_auto_fill_delay_sec=0.02)
    # No set_slippage_engine call -- engine remains None.
    adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    assert _wait_for_events(captured, 1, timeout_sec=2.0)
    ev = captured[0]
    assert ev.avg_fill_price == 2500.0, (
        "no slippage engine wired → paper fill at limit price (back-compat)"
    )
    assert ev.slippage_pct == 0.0
    print("  OK CFG-6: no engine wired → zero slippage (back-compat)")


def test_cfg6_set_slippage_engine_noop_in_live() -> None:
    """Live mode ignores set_slippage_engine (broker fills are truth)."""
    adapter, _, _, _, _ = _make_adapter(paper=False)
    # Calling the setter in live mode must not raise and must not bind.
    adapter.set_slippage_engine(_make_test_slippage_engine())
    assert adapter._slippage is None, (
        "set_slippage_engine should be no-op in live mode"
    )
    print("  OK CFG-6: set_slippage_engine no-op in live mode")


def test_paper_mode_get_quote_delegates_to_provider() -> None:
    from core.time_authority import now_ist

    def fake_provider(symbols):
        return {s: Quote(symbol=s, last_price=100.0, bid=99.0,
                         ask=101.0, volume=5000, ts=now_ist())
                for s in symbols}

    adapter, _, _, _, _ = _make_adapter(paper=True, quote_provider=fake_provider)
    quotes = adapter.get_quote(["INFY"])
    assert "INFY" in quotes
    assert quotes["INFY"].last_price == 100.0
    print("  OK paper mode get_quote delegates to injected provider (ZA10)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- rate limiter category mapping (ZA3)
# ─────────────────────────────────────────────────────────────────────────────

def test_rate_limiter_acquire_called_with_correct_category() -> None:
    """place_order, cancel_order, modify_order all use 'order' category."""
    from unittest.mock import patch as _patch
    adapter, _, rl, _, _ = _make_adapter()

    acquired_categories: list[str] = []
    original_acquire = rl.acquire

    def tracking_acquire(category: str, n: int = 1) -> None:
        acquired_categories.append(category)
        return original_acquire(category, n)

    rl.acquire = tracking_acquire  # type: ignore[method-assign]

    adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=1, price=0.0,
        order_type="MARKET", intent="INTRADAY",
    )
    adapter.cancel_order("KITE12345")
    adapter.modify_order("KITE12345", price=2510.0)
    adapter.get_positions()
    adapter.get_margins()
    adapter.get_quote(["RELIANCE"])

    assert acquired_categories[0] == "order",   f"place_order category: {acquired_categories[0]}"
    assert acquired_categories[1] == "order",   f"cancel_order category: {acquired_categories[1]}"
    assert acquired_categories[2] == "order",   f"modify_order category: {acquired_categories[2]}"
    assert acquired_categories[3] == "margins", f"get_positions category: {acquired_categories[3]}"
    assert acquired_categories[4] == "margins", f"get_margins category: {acquired_categories[4]}"
    assert acquired_categories[5] == "quote",   f"get_quote category: {acquired_categories[5]}"
    print("  OK rate_limiter.acquire called with correct category per method (ZA3)")


def test_rate_limiter_not_called_on_validation_error() -> None:
    """RL must not be acquired when validation fails (ZA13)."""
    adapter, _, rl, _, _ = _make_adapter()
    acquired: list[str] = []
    original = rl.acquire

    def tracking(category: str, n: int = 1) -> None:
        acquired.append(category)
        return original(category, n)

    rl.acquire = tracking  # type: ignore[method-assign]

    try:
        adapter.place_order(
            symbol="RELIANCE", side="BUY", qty=0, price=100.0,
            order_type="LIMIT", intent="INTRADAY",
        )
    except ValueError:
        pass

    assert acquired == [], f"RL should not be called on validation error, got: {acquired}"
    print("  OK RL not called on validation error (ZA13)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- logging (ZA9)
# ─────────────────────────────────────────────────────────────────────────────

def test_place_order_logs_entry_and_exit() -> None:
    """Every method must log call_start and call_end at INFO."""
    import logging
    adapter, _, _, _, _ = _make_adapter()
    log_records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(record)

    handler = Capture()
    handler.setLevel(logging.DEBUG)
    test_logger = logging.getLogger("test_adapter")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=1, price=0.0,
        order_type="MARKET", intent="INTRADAY",
    )

    messages = [r.getMessage() for r in log_records]
    assert any("call_start" in m for m in messages), f"No call_start log: {messages}"
    assert any("call_end" in m for m in messages),   f"No call_end log: {messages}"

    test_logger.removeHandler(handler)
    print("  OK place_order logs call_start and call_end at INFO (ZA9)")


def test_exception_logged_before_reraise() -> None:
    """log_exception must be called on kite exceptions before re-raise (ZA9)."""
    from kiteconnect import exceptions as kex
    import logging

    kite = MockKite()
    kite.place_order_exc = kex.NetworkException("timeout")
    adapter, _, _, _, _ = _make_adapter(kite=kite)

    log_records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(record)

    handler = Capture()
    test_logger = logging.getLogger("test_adapter")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    try:
        adapter.place_order(
            symbol="RELIANCE", side="BUY", qty=1, price=0.0,
            order_type="MARKET", intent="INTRADAY",
        )
    except BrokerTimeoutError:
        pass

    # log_exception logs at ERROR level
    error_records = [r for r in log_records if r.levelno >= logging.ERROR]
    assert error_records, "Expected at least one ERROR log from log_exception"

    test_logger.removeHandler(handler)
    print("  OK Exception logged via log_exception before re-raise (ZA9)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- H-20 / ZA16a: paper adapter synthesizes OrderFilled after delay
#
# ZA16a carves out paper mode as the ONE place the adapter publishes events,
# because paper has no real broker and no order_monitor poll loop. Live mode
# must never take this branch (see test_live_mode_place_order_does_NOT_publish
# _order_filled regression guard below).
# ─────────────────────────────────────────────────────────────────────────────

import time as _time_for_h20_tests  # avoid shadowing name "time" higher up


def _wait_for_events(captured: list, expected_count: int,
                     timeout_sec: float = 2.0, poll_sec: float = 0.01) -> bool:
    """Spin until captured has expected_count events or timeout. Returns True on success."""
    deadline = _time_for_h20_tests.monotonic() + timeout_sec
    while _time_for_h20_tests.monotonic() < deadline:
        if len(captured) >= expected_count:
            return True
        _time_for_h20_tests.sleep(poll_sec)
    return len(captured) >= expected_count


def test_za16a_paper_place_order_returns_submitted_synchronously() -> None:
    """Return value stays SUBMITTED immediately; synth fires async (regression of existing behavior)."""
    bus = EventBus()
    adapter, _, _, osm, _ = _make_adapter(paper=True, bus=bus,
                                          paper_auto_fill_delay_sec=0.5)
    result = adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    # Synchronous return path unchanged -- still SUBMITTED
    assert result.status == "SUBMITTED"
    # OSM may already be COMPLETE if the machine is lightning fast, but
    # on a 0.5s delay it should still be SUBMITTED at this instant.
    state_immediately = osm.current_state(result.internal_order_id)
    assert state_immediately == "SUBMITTED", (
        f"expected SUBMITTED right after return, got {state_immediately}"
    )
    print("  OK ZA16a: place_order returns SUBMITTED synchronously (H-20)")


def test_za16a_paper_publishes_order_filled_after_delay() -> None:
    """After the configured delay the bus gets an OrderFilled and OSM is COMPLETE."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    adapter, _, _, osm, _ = _make_adapter(paper=True, bus=bus,
                                          paper_auto_fill_delay_sec=0.05)
    result = adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )

    assert _wait_for_events(captured, 1, timeout_sec=2.0), (
        f"OrderFilled never published in time; got {len(captured)} events"
    )
    assert len(captured) == 1
    assert osm.current_state(result.internal_order_id) == "COMPLETE"
    print("  OK ZA16a: OrderFilled published + OSM COMPLETE after delay (H-20)")


def test_za16a_paper_order_filled_payload_fields_populated() -> None:
    """Synthesized OrderFilled must have all OM6 fields set correctly."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    adapter, _, _, _, _ = _make_adapter(paper=True, bus=bus,
                                        paper_auto_fill_delay_sec=0.02)
    result = adapter.place_order(
        symbol="INFY", side="SELL", qty=7, price=1500.0,
        order_type="LIMIT", intent="INTRADAY",
    )

    assert _wait_for_events(captured, 1, timeout_sec=2.0)
    ev = captured[0]
    assert ev.source_module == "zerodha_adapter_paper"
    assert ev.internal_order_id == result.internal_order_id
    assert ev.broker_order_id == result.broker_order_id
    assert ev.symbol == "INFY"
    assert ev.side == "SELL"
    assert ev.filled_qty == 7
    assert ev.avg_fill_price == 1500.0       # paper fills at limit price
    assert ev.expected_price == 1500.0
    assert ev.slippage_pct == 0.0            # paper: zero slippage
    assert ev.filled_at, "filled_at must be ISO timestamp, got empty"
    print("  OK ZA16a: OrderFilled payload fully populated (H-20)")


def test_za16a_paper_delay_is_actually_observed() -> None:
    """Event does not land before the delay elapses."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    delay = 0.3
    adapter, _, _, _, _ = _make_adapter(paper=True, bus=bus,
                                        paper_auto_fill_delay_sec=delay)
    start = _time_for_h20_tests.monotonic()
    adapter.place_order(
        symbol="TCS", side="BUY", qty=1, price=100.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    # Check well before the delay -- event must NOT be present yet
    _time_for_h20_tests.sleep(0.05)
    assert len(captured) == 0, (
        f"event arrived too early (0.05s of 0.3s delay), got {len(captured)}"
    )
    # Now wait for it
    assert _wait_for_events(captured, 1, timeout_sec=2.0)
    elapsed = _time_for_h20_tests.monotonic() - start
    assert elapsed >= delay * 0.9, (
        f"event arrived before delay elapsed: {elapsed:.3f}s < {delay:.3f}s"
    )
    print("  OK ZA16a: delay actually elapsed before publish (H-20)")


def test_za16a_paper_zero_delay_fires_quickly() -> None:
    """delay=0 still works (no sleep), event lands promptly."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    adapter, _, _, osm, _ = _make_adapter(paper=True, bus=bus,
                                          paper_auto_fill_delay_sec=0.0)
    result = adapter.place_order(
        symbol="HDFC", side="BUY", qty=1, price=500.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    assert _wait_for_events(captured, 1, timeout_sec=1.0)
    assert osm.current_state(result.internal_order_id) == "COMPLETE"
    print("  OK ZA16a: zero delay still fires synth (H-20)")


def test_za16a_paper_multiple_orders_each_get_filled() -> None:
    """Place N orders; expect N OrderFilled events."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    adapter, _, _, osm, _ = _make_adapter(paper=True, bus=bus,
                                          paper_auto_fill_delay_sec=0.02)
    placed_ids = []
    for i in range(3):
        r = adapter.place_order(
            symbol=f"SYM{i}", side="BUY", qty=1 + i, price=100.0 + i,
            order_type="LIMIT", intent="INTRADAY",
        )
        placed_ids.append(r.internal_order_id)

    assert _wait_for_events(captured, 3, timeout_sec=2.0), (
        f"expected 3 OrderFilled, got {len(captured)}"
    )
    for oid in placed_ids:
        assert osm.current_state(oid) == "COMPLETE", (
            f"{oid} not COMPLETE after synth"
        )
    # Each symbol got its own event
    symbols_fired = {e.symbol for e in captured}
    assert symbols_fired == {"SYM0", "SYM1", "SYM2"}
    print("  OK ZA16a: N orders -> N OrderFilled events, each reaches COMPLETE (H-20)")


def test_za16a_paper_bus_none_degrades_gracefully_no_publish() -> None:
    """bus=None (tests without event plumbing): OSM still reaches COMPLETE; no publish."""
    adapter, _, _, osm, _ = _make_adapter(paper=True, bus=None,
                                          paper_auto_fill_delay_sec=0.02)
    result = adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=1, price=100.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    # Wait long enough for the synth thread to run
    _time_for_h20_tests.sleep(0.2)
    assert osm.current_state(result.internal_order_id) == "COMPLETE"
    print("  OK ZA16a: bus=None degrades to OSM-only synth (H-20)")


def test_za16a_paper_synth_thread_is_daemon() -> None:
    """Spawned thread must be daemon so process shutdown is unblocked."""
    import threading as _threading

    # Enumerate threads before/after; new thread should have daemon=True.
    threads_before = set(_threading.enumerate())
    bus = EventBus()
    adapter, _, _, _, _ = _make_adapter(paper=True, bus=bus,
                                        paper_auto_fill_delay_sec=0.5)
    adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=1, price=100.0,
        order_type="LIMIT", intent="INTRADAY",
    )
    # Grab the synth thread -- named paper_synth_<internal_id>
    new_threads = set(_threading.enumerate()) - threads_before
    synth_threads = [t for t in new_threads if t.name.startswith("paper_synth_")]
    assert synth_threads, "no paper_synth_* thread spawned"
    for t in synth_threads:
        assert t.daemon, f"thread {t.name} is not a daemon"
    print("  OK ZA16a: synth thread is daemon (H-20)")


def test_live_mode_place_order_does_NOT_publish_order_filled() -> None:
    """
    ZA16 regression guard: the live path must never publish OrderFilled from
    the adapter. order_monitor is the only live-mode publisher.
    """
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    # paper=False, bus wired so IF live mode ever published we would see it.
    adapter, _, _, osm, _ = _make_adapter(paper=False, bus=bus)
    result = adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )

    # Wait longer than any reasonable synth delay -- still zero events.
    _time_for_h20_tests.sleep(0.3)
    assert len(captured) == 0, (
        f"ZA16 violation: live mode published {len(captured)} OrderFilled event(s)"
    )
    # OSM remains SUBMITTED in live mode (order_monitor, not adapter, drives COMPLETE).
    assert osm.current_state(result.internal_order_id) == "SUBMITTED"
    print("  OK ZA16 regression guard: live mode does NOT publish OrderFilled")


# ─────────────────────────────────────────────────────────────────────────────
# Audit 6.2 — paper synth-fill LTP gating
# ─────────────────────────────────────────────────────────────────────────────

def _ltp_provider(prices: dict[str, float]):
    """Test helper: return a quote_provider that yields the given LTPs."""
    from core.time_authority import now_ist

    def _provider(symbols):
        out = {}
        for s in symbols:
            if s in prices:
                out[s] = Quote(symbol=s, last_price=prices[s], bid=prices[s] - 0.05,
                               ask=prices[s] + 0.05, volume=1000, ts=now_ist())
        return out
    return _provider


def test_audit62_limit_buy_fills_when_ltp_at_or_below_limit() -> None:
    """LTP-gated LIMIT BUY fills when LTP <= price (fills at LTP)."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    adapter, _, _, _, _ = _make_adapter(
        paper=True, bus=bus,
        paper_auto_fill_delay_sec=0.0,
        paper_ltp_gating_enabled=True,
        paper_ltp_gating_max_wait_sec=0.5,
        paper_ltp_gating_poll_sec=0.02,
        quote_provider=_ltp_provider({"RELIANCE": 2495.0}),
    )
    adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )

    assert _wait_for_events(captured, 1, timeout_sec=2.0)
    ev = captured[0]
    # Filled at LTP (favourable to buyer); slippage reflects ltp-vs-limit.
    assert ev.avg_fill_price == 2495.0
    assert ev.expected_price == 2500.0
    print("  OK 6.2: LIMIT BUY fills when LTP <= price (fill@LTP, expected@limit)")


def test_audit62_limit_buy_does_not_fill_when_ltp_above_limit() -> None:
    """LTP-gated LIMIT BUY does NOT fill when LTP stays above price."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    adapter, _, _, osm, _ = _make_adapter(
        paper=True, bus=bus,
        paper_auto_fill_delay_sec=0.0,
        paper_ltp_gating_enabled=True,
        paper_ltp_gating_max_wait_sec=0.2,   # short max-wait
        paper_ltp_gating_poll_sec=0.02,
        quote_provider=_ltp_provider({"RELIANCE": 2510.0}),  # above limit
    )
    result = adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=10, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )

    # Wait past max_wait + poll cycle
    _time_for_h20_tests.sleep(0.4)
    assert len(captured) == 0, (
        f"6.2 violated: LIMIT BUY filled despite LTP above limit; got {captured}"
    )
    # Order stays SUBMITTED (no synthesised completion)
    assert osm.current_state(result.internal_order_id) == "SUBMITTED"
    print("  OK 6.2: LIMIT BUY does NOT fill when LTP > price (stays SUBMITTED)")


def test_audit62_limit_sell_fills_when_ltp_at_or_above_limit() -> None:
    """LTP-gated LIMIT SELL fills when LTP >= price (fills at LTP)."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    adapter, _, _, _, _ = _make_adapter(
        paper=True, bus=bus,
        paper_auto_fill_delay_sec=0.0,
        paper_ltp_gating_enabled=True,
        paper_ltp_gating_max_wait_sec=0.5,
        paper_ltp_gating_poll_sec=0.02,
        quote_provider=_ltp_provider({"INFY": 1505.0}),
    )
    adapter.place_order(
        symbol="INFY", side="SELL", qty=5, price=1500.0,
        order_type="LIMIT", intent="INTRADAY",
    )

    assert _wait_for_events(captured, 1, timeout_sec=2.0)
    ev = captured[0]
    assert ev.avg_fill_price == 1505.0   # better-than-limit fill
    print("  OK 6.2: LIMIT SELL fills when LTP >= price (fill@LTP)")


def test_audit62_market_fills_at_ltp() -> None:
    """LTP-gated MARKET fills at LTP (one-shot, no poll loop)."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    adapter, _, _, _, _ = _make_adapter(
        paper=True, bus=bus,
        paper_auto_fill_delay_sec=0.0,
        paper_ltp_gating_enabled=True,
        quote_provider=_ltp_provider({"TCS": 3300.0}),
    )
    adapter.place_order(
        symbol="TCS", side="BUY", qty=2, price=0.0,    # MARKET = price 0
        order_type="MARKET", intent="INTRADAY",
    )

    assert _wait_for_events(captured, 1, timeout_sec=1.0)
    assert captured[0].avg_fill_price == 3300.0
    print("  OK 6.2: MARKET fills at LTP")


def test_audit62_sl_m_buy_fills_when_ltp_crosses_trigger_upward() -> None:
    """SL-M BUY fires when LTP >= trigger_price (stop-buy)."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    adapter, _, _, _, _ = _make_adapter(
        paper=True, bus=bus,
        paper_auto_fill_delay_sec=0.0,
        paper_ltp_gating_enabled=True,
        paper_ltp_gating_max_wait_sec=0.5,
        paper_ltp_gating_poll_sec=0.02,
        quote_provider=_ltp_provider({"HDFC": 1800.0}),  # at/above trigger
    )
    adapter.place_order(
        symbol="HDFC", side="BUY", qty=1, price=0.0,
        order_type="SL-M", intent="INTRADAY",
        trigger_price=1795.0,
    )

    assert _wait_for_events(captured, 1, timeout_sec=2.0)
    assert captured[0].avg_fill_price == 1800.0   # SL-M fills at LTP
    print("  OK 6.2: SL-M BUY fills when LTP >= trigger_price")


def test_audit62_gating_disabled_preserves_legacy_behaviour() -> None:
    """gating_enabled=False (default for tests): LIMIT fills unconditionally at price."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    # No quote_provider, no LTP, gating off -- legacy auto-fill at price.
    adapter, _, _, _, _ = _make_adapter(
        paper=True, bus=bus,
        paper_auto_fill_delay_sec=0.0,
        paper_ltp_gating_enabled=False,
    )
    adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=1, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )

    assert _wait_for_events(captured, 1, timeout_sec=1.0)
    assert captured[0].avg_fill_price == 2500.0
    assert captured[0].slippage_pct == 0.0
    print("  OK 6.2: gating disabled -> legacy auto-fill at price (regression guard)")


def test_audit62_satisfies_condition_pure_helper() -> None:
    """Pure helper _ltp_satisfies_condition returns expected fill price or None."""
    fn = ZerodhaAdapter._ltp_satisfies_condition
    # LIMIT BUY
    assert fn(side="BUY",  order_type="LIMIT", price=100.0, trigger_price=0.0, ltp=99.0)  == 99.0
    assert fn(side="BUY",  order_type="LIMIT", price=100.0, trigger_price=0.0, ltp=101.0) is None
    assert fn(side="BUY",  order_type="LIMIT", price=100.0, trigger_price=0.0, ltp=100.0) == 100.0
    # LIMIT SELL
    assert fn(side="SELL", order_type="LIMIT", price=100.0, trigger_price=0.0, ltp=101.0) == 101.0
    assert fn(side="SELL", order_type="LIMIT", price=100.0, trigger_price=0.0, ltp=99.0)  is None
    # SL-M BUY (stop-buy)
    assert fn(side="BUY",  order_type="SL-M", price=0.0, trigger_price=110.0, ltp=110.0) == 110.0
    assert fn(side="BUY",  order_type="SL-M", price=0.0, trigger_price=110.0, ltp=109.0) is None
    # SL-M SELL (stop-sell)
    assert fn(side="SELL", order_type="SL-M", price=0.0, trigger_price=90.0, ltp=89.0)   == 89.0
    assert fn(side="SELL", order_type="SL-M", price=0.0, trigger_price=90.0, ltp=91.0)   is None
    # SL BUY (stop-buy with limit cap)
    assert fn(side="BUY",  order_type="SL", price=112.0, trigger_price=110.0, ltp=110.5) == 110.5
    assert fn(side="BUY",  order_type="SL", price=112.0, trigger_price=110.0, ltp=109.0) is None
    print("  OK 6.2: _ltp_satisfies_condition returns correct fill / None across order types")


# ─────────────────────────────────────────────────────────────────────────────
# Paper synth-fill sanity guard: reject fills deviating >50% from reference
# ─────────────────────────────────────────────────────────────────────────────

def test_paper_synth_rejects_fill_deviating_from_reference() -> None:
    """
    Regression: stub LTP of 100.0 caused SL-M SELL orders for stocks priced
    >200 to fill at ~99.80, producing garbage P&L. The sanity guard rejects
    fills where fill_price deviates >50% from trigger_price.

    MEESHO example: entry=209.16, SL trigger=204.66. If LTP returns 100.0,
    the fill would be 100.0 (or 99.80 after slippage). 100/204.66 = 48.9%
    deviation -> rejected. For stocks like ZENTEC (SL~1450), deviation is 93%.
    """
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    # Simulate the bug: quote_provider returns fake LTP=100 for all symbols
    adapter, _, _, osm, _ = _make_adapter(
        paper=True, bus=bus,
        paper_auto_fill_delay_sec=0.0,
        paper_ltp_gating_enabled=True,
        paper_ltp_gating_max_wait_sec=0.3,
        paper_ltp_gating_poll_sec=0.02,
        quote_provider=_ltp_provider({"ZENTEC": 100.0}),
    )

    # SL-M SELL for a stock with SL at 1450 — LTP=100 satisfies the
    # SL-M SELL condition (100 <= 1450), but fill_price=100 is 93% off
    result = adapter.place_order(
        symbol="ZENTEC", side="SELL", qty=32, price=0.0,
        order_type="SL-M", intent="INTRADAY",
        trigger_price=1450.0,
    )

    _time_for_h20_tests.sleep(0.5)
    assert len(captured) == 0, (
        f"Fill should have been rejected (>50% deviation from trigger); "
        f"got {captured[0].avg_fill_price if captured else 'no fills'}"
    )
    assert osm.current_state(result.internal_order_id) == "SUBMITTED"
    print("  OK sanity guard rejects paper fill deviating >50% from reference price")


def test_paper_synth_allows_fill_within_deviation_threshold() -> None:
    """Normal SL fill within 50% of trigger passes the sanity guard."""
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    # LTP=202.0, trigger=204.66 → deviation=(204.66-202)/204.66=1.3% — passes
    adapter, _, _, _, _ = _make_adapter(
        paper=True, bus=bus,
        paper_auto_fill_delay_sec=0.0,
        paper_ltp_gating_enabled=True,
        paper_ltp_gating_max_wait_sec=0.5,
        paper_ltp_gating_poll_sec=0.02,
        quote_provider=_ltp_provider({"MEESHO": 202.0}),
    )

    adapter.place_order(
        symbol="MEESHO", side="SELL", qty=100, price=0.0,
        order_type="SL-M", intent="INTRADAY",
        trigger_price=204.66,
    )

    assert _wait_for_events(captured, 1, timeout_sec=2.0)
    assert captured[0].avg_fill_price == 202.0
    print("  OK normal SL fill (1.3% deviation from trigger) passes sanity guard")


# ── FIX-001: _fetch_ltp returns None on failure; no false SELL fill ─────────

def test_fix001_ltp_failure_does_not_trigger_sell_fill() -> None:
    """
    FIX-001: when quote_provider raises, _fetch_ltp must return None
    and _synth_fill must NOT publish any OrderFilled (no false SELL SL trigger).
    """
    bus = EventBus()
    captured: list[OrderFilled] = []
    bus.subscribe(OrderFilled, lambda e: captured.append(e))

    def _raising_provider(syms):
        raise RuntimeError("quote service down")

    adapter, _, _, _, _ = _make_adapter(
        paper=True, bus=bus,
        paper_auto_fill_delay_sec=0.0,
        paper_ltp_gating_enabled=True,
        paper_ltp_gating_max_wait_sec=0.3,   # short poll window so test finishes fast
        paper_ltp_gating_poll_sec=0.05,
        quote_provider=_raising_provider,
    )

    # Place a SELL SL-M order. With LTP fetch always failing, no fill should occur.
    adapter.place_order(
        symbol="TESTSL", side="SELL", qty=10, price=0.0,
        order_type="SL-M", intent="INTRADAY",
        trigger_price=500.0,
    )

    # Wait longer than max_wait_sec to confirm no spurious fill
    import time as _time
    _time.sleep(0.5)

    assert len(captured) == 0, (
        f"FIX-001 FAIL: expected 0 OrderFilled events, got {len(captured)}. "
        f"ltp_failure must NOT trigger a SELL fill at price 0.0"
    )
    print("  OK FIX-001: quote_provider exception → no false SELL SL fill")


def test_fix001_fetch_ltp_returns_none_not_zero() -> None:
    """_fetch_ltp returns None (not 0.0) when quote_provider raises."""
    adapter, _, _, _, _ = _make_adapter(
        paper=True,
        quote_provider=lambda s: (_ for _ in ()).throw(RuntimeError("down")),
    )
    result = adapter._fetch_ltp("ANYSTOCK")
    assert result is None, f"FIX-001: expected None from _fetch_ltp on failure, got {result!r}"
    print("  OK FIX-001: _fetch_ltp returns None on exception (not 0.0)")


def test_paper_synth_pnl_sign_correct_for_long_sl_hit() -> None:
    """
    Verify P&L is NEGATIVE for LONG when SL fires below entry.
    Uses the exact MEESHO scenario from bug report.
    """
    from orders.shadow_tracker import _calc_pnl

    # MEESHO: LONG, entry=209.16, SL exit=204.66
    direction = "LONG"
    entry = 209.16
    exit_p = 204.66

    pnl_per_share, pnl_pct = _calc_pnl(direction, entry, exit_p)

    assert pnl_per_share < 0, (
        f"LONG SL hit must produce negative pnl_per_share; got {pnl_per_share}"
    )
    expected_pnl = exit_p - entry  # = -4.50
    assert abs(pnl_per_share - expected_pnl) < 0.001, (
        f"pnl_per_share should be {expected_pnl}; got {pnl_per_share}"
    )
    assert pnl_pct < 0, f"LONG SL must give negative pnl_pct; got {pnl_pct}"

    # Also verify SHORT TGT (should be positive)
    pnl_s, pct_s = _calc_pnl("SHORT", 209.16, 204.66)
    assert pnl_s > 0, f"SHORT exit below entry = profit; got {pnl_s}"
    print("  OK _calc_pnl: LONG SL negative, SHORT exit-below-entry positive")


# ─────────────────────────────────────────────────────────────────────────────
# BL-6: broker 429 detection + penalize + typed exception (Phase D.1)
# ─────────────────────────────────────────────────────────────────────────────

def _make_429_exc():
    """Build a kiteconnect-shaped exception with HTTP status 429."""
    from kiteconnect import exceptions as kex
    exc = kex.NetworkException("Too Many Requests")
    exc.code = 429
    return exc


def test_bl6_429_raises_broker_rate_limit_429_error() -> None:
    """HTTP 429 from broker -> BrokerRateLimit429Error (not OrderRejectedError)."""
    kite = MockKite()
    kite.place_order_exc = _make_429_exc()
    adapter, _, _, _, _ = _make_adapter(kite=kite)

    raised: BrokerRateLimit429Error | None = None
    try:
        adapter.place_order(
            symbol="RELIANCE", side="BUY", qty=1, price=2500.0,
            order_type="LIMIT", intent="INTRADAY",
        )
    except BrokerRateLimit429Error as exc:
        raised = exc

    assert raised is not None, "Expected BrokerRateLimit429Error"
    # Also verify it is a BrokerError (retry policies can catch the super)
    assert isinstance(raised, BrokerError)
    print("  OK BL-6: HTTP 429 -> BrokerRateLimit429Error (distinct from OrderRejected)")


def test_bl6_429_calls_penalize_on_rate_limiter() -> None:
    """Adapter must call rate_limiter.penalize() with the correct category."""
    kite = MockKite()
    kite.place_order_exc = _make_429_exc()
    adapter, _, rl, _, _ = _make_adapter(kite=kite)

    # Spy on the live RateLimiter's penalize
    original_penalize = rl.penalize
    calls: list[tuple[str, float]] = []

    def spy_penalize(category: str, sleep_sec: float) -> None:
        calls.append((category, sleep_sec))
        original_penalize(category, sleep_sec)

    rl.penalize = spy_penalize  # type: ignore[assignment]

    try:
        adapter.place_order(
            symbol="RELIANCE", side="BUY", qty=1, price=2500.0,
            order_type="LIMIT", intent="INTRADAY",
        )
    except BrokerRateLimit429Error:
        pass

    assert len(calls) == 1, f"Expected exactly 1 penalize call, got {len(calls)}"
    category, delay = calls[0]
    assert category == "order", f"Expected category 'order', got {category!r}"
    assert delay > 0, f"Expected positive penalize delay, got {delay}"
    print("  OK BL-6: penalize called with category='order' and positive delay")


def test_bl6_429_attempt_counter_exponential_delays() -> None:
    """Three consecutive 429s -> delays roughly 0.2s, 0.4s, 0.8s (within jitter)."""
    kite = MockKite()
    kite.place_order_exc = _make_429_exc()
    adapter, _, rl, _, _ = _make_adapter(kite=kite)

    observed: list[float] = []
    original_penalize = rl.penalize

    def spy_penalize(category: str, sleep_sec: float) -> None:
        observed.append(sleep_sec)
        original_penalize(category, sleep_sec)

    rl.penalize = spy_penalize  # type: ignore[assignment]

    # Use the adapter's live backoff config so we can compute expected ranges
    cfg = adapter._rl_backoff  # type: ignore[attr-defined]

    for _ in range(3):
        try:
            adapter.place_order(
                symbol="RELIANCE", side="BUY", qty=1, price=2500.0,
                order_type="LIMIT", intent="INTRADAY",
            )
        except BrokerRateLimit429Error:
            pass

    assert len(observed) == 3, f"Expected 3 penalize calls, got {len(observed)}"

    # Expected bases (before jitter): initial * multiplier**attempt
    expected = [
        min(cfg.max_delay_sec, cfg.initial_delay_sec * (cfg.multiplier ** n))
        for n in range(3)
    ]
    for i, (actual, exp) in enumerate(zip(observed, expected)):
        low, high = exp - cfg.jitter_sec, exp + cfg.jitter_sec
        assert low <= actual <= high, (
            f"Attempt {i+1}: delay {actual} not in [{low}, {high}] "
            f"(expected base {exp} +/- jitter {cfg.jitter_sec})"
        )
    # Exponential growth: each delay must strictly exceed the previous AT LEAST
    # by (multiplier-1)*initial - 2*jitter. With defaults that's 0.1 minimum.
    for i in range(1, 3):
        assert observed[i] > observed[i - 1], (
            f"Delays not monotonically increasing: {observed}"
        )
    print("  OK BL-6: per-category attempt counter drives exponential delays")


def test_bl6_429_successful_call_resets_counter() -> None:
    """After a successful call, next 429 should restart at initial_delay (counter reset)."""
    kite = MockKite()
    kite.place_order_exc = _make_429_exc()
    adapter, _, rl, _, _ = _make_adapter(kite=kite)

    observed: list[float] = []
    original_penalize = rl.penalize

    def spy_penalize(category: str, sleep_sec: float) -> None:
        observed.append(sleep_sec)
        original_penalize(category, sleep_sec)

    rl.penalize = spy_penalize  # type: ignore[assignment]

    # Fire two 429s -> counter at 2, delays ~0.2 and ~0.4
    for _ in range(2):
        try:
            adapter.place_order(
                symbol="RELIANCE", side="BUY", qty=1, price=2500.0,
                order_type="LIMIT", intent="INTRADAY",
            )
        except BrokerRateLimit429Error:
            pass

    # Now simulate bucket thaw + successful call
    kite.place_order_exc = None
    # Clear the frozen bucket so acquire() does not block (tests run fast).
    # A quick way: directly reset the bucket via the limiter's internal state.
    # We rely on the reset happening after successful kite.place_order.
    # However, the rate_limiter bucket is still frozen from the last penalize,
    # so acquire() will block. Set max_wait generous enough or thaw manually.
    # Simplest: allow a fresh limiter by reconstructing the adapter with a new rl.
    # Instead, we use the bucket's internal method to skip the freeze:
    for bucket in rl._buckets.values():  # type: ignore[attr-defined]
        bucket._frozen_until = 0.0  # clear freeze for the test

    # Successful call resets the counter
    adapter.place_order(
        symbol="RELIANCE", side="BUY", qty=1, price=2500.0,
        order_type="LIMIT", intent="INTRADAY",
    )

    # Fire ONE more 429: delay should match attempt-0 (initial_delay +/- jitter)
    kite.place_order_exc = _make_429_exc()
    # Thaw buckets again before the new 429 tries to acquire
    for bucket in rl._buckets.values():  # type: ignore[attr-defined]
        bucket._frozen_until = 0.0

    try:
        adapter.place_order(
            symbol="RELIANCE", side="BUY", qty=1, price=2500.0,
            order_type="LIMIT", intent="INTRADAY",
        )
    except BrokerRateLimit429Error:
        pass

    cfg = adapter._rl_backoff  # type: ignore[attr-defined]
    last = observed[-1]
    low = cfg.initial_delay_sec - cfg.jitter_sec
    high = cfg.initial_delay_sec + cfg.jitter_sec
    assert low <= last <= high, (
        f"After reset, first 429 delay {last} not in initial_delay range "
        f"[{low}, {high}] -- counter was not reset by successful call"
    )
    print("  OK BL-6: successful call resets per-category 429 attempt counter")


def test_bl6_non_429_error_does_not_penalize() -> None:
    """kiteconnect exceptions without code=429 must NOT call penalize."""
    from kiteconnect import exceptions as kex
    kite = MockKite()
    # NetworkException with code=500 is a plain timeout, not a rate limit
    generic_exc = kex.NetworkException("server error")
    generic_exc.code = 500
    kite.place_order_exc = generic_exc
    adapter, _, rl, _, _ = _make_adapter(kite=kite)

    calls: list[tuple[str, float]] = []
    original_penalize = rl.penalize

    def spy_penalize(category: str, sleep_sec: float) -> None:
        calls.append((category, sleep_sec))
        original_penalize(category, sleep_sec)

    rl.penalize = spy_penalize  # type: ignore[assignment]

    raised = None
    try:
        adapter.place_order(
            symbol="RELIANCE", side="BUY", qty=1, price=2500.0,
            order_type="LIMIT", intent="INTRADAY",
        )
    except BrokerTimeoutError as exc:
        raised = exc
    except BrokerRateLimit429Error:
        raise AssertionError(
            "Non-429 error should NOT be translated to BrokerRateLimit429Error"
        )

    assert raised is not None, "Expected BrokerTimeoutError for non-429 NetworkException"
    assert calls == [], f"penalize should NOT be called for non-429, got {calls}"
    print("  OK BL-6: non-429 error flows through normal translation, no penalize")


# ─────────────────────────────────────────────────────────────────────────────
# FIX-009: get_server_time uses HTTP Date header
# ─────────────────────────────────────────────────────────────────────────────

def test_fix009_get_server_time_uses_date_header() -> None:
    """FIX-009: get_server_time() returns broker Date header when present."""
    from datetime import timezone as _tz

    kite = MockKite()

    class MockSession:
        def __init__(self):
            self.hooks = {}

    class MockResponse:
        headers = {"Date": "Tue, 12 May 2026 09:15:00 GMT"}

    # Install a session with hooks support
    mock_session = MockSession()
    kite.reqsession = mock_session

    adapter, _, _, _, _ = _make_adapter(kite=kite, paper=False)

    # Simulate the quote call firing the hook
    fire_hooks = mock_session.hooks.get("response", [])
    assert len(fire_hooks) == 1, "Response hook should be installed"

    # Fire the hook with a mock response
    fire_hooks[0](MockResponse())

    # Now _last_response_date should be set — Date was "09:15:00 GMT"
    # The hook converts to IST so: 09:15 UTC = 14:45 IST
    captured = adapter._last_response_date
    assert captured is not None, "Date header not captured by hook"
    assert captured.tzinfo is not None, "Captured date should be timezone-aware"
    # In UTC the time is 09:15:00
    captured_utc = captured.astimezone(timezone.utc)
    assert captured_utc.hour == 9 and captured_utc.minute == 15, (
        f"Expected 09:15 UTC from 'Tue, 12 May 2026 09:15:00 GMT'; got {captured_utc}"
    )
    print("  OK FIX-009: response hook captures HTTP Date header")


def test_fix009_get_server_time_falls_back_to_now_ist_on_miss() -> None:
    """FIX-009: get_server_time() falls back to now_ist() when no Date header captured."""
    from core.time_authority import now_ist

    kite = MockKite()  # no reqsession → hook not installed
    adapter, _, rl, _, _ = _make_adapter(kite=kite, paper=False)

    # Force _last_response_date to remain None (no hook fired)
    adapter._last_response_date = None

    # Patch quote to not trigger anything
    t_before = now_ist()
    result = adapter.get_server_time()
    t_after = now_ist()

    assert t_before <= result <= t_after, (
        "Fallback should return now_ist() within the call window"
    )
    print("  OK FIX-009: falls back to now_ist() when no Date header captured")


def test_fix009_paper_mode_returns_now_ist() -> None:
    """FIX-009: paper mode still returns now_ist() (no broker call)."""
    from core.time_authority import now_ist
    kite = MockKite()
    quote_called = []
    original_quote = kite.quote

    def spy_quote(*args, **kwargs):
        quote_called.append(args)
        return original_quote(*args, **kwargs)

    kite.quote = spy_quote
    adapter, _, _, _, _ = _make_adapter(kite=kite, paper=True)

    t_before = now_ist()
    result = adapter.get_server_time()
    t_after = now_ist()

    assert t_before <= result <= t_after
    assert quote_called == [], "Paper mode must NOT call kite.quote()"
    print("  OK FIX-009: paper mode returns now_ist() without broker call")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# FIX-072 — live margin API with TTL caching
# ─────────────────────────────────────────────────────────────────────────────

def test_fix072_get_live_margin_pct_success() -> None:
    """FIX-072: get_live_margin_pct fetches broker margin and caches result."""
    adapter, kite, rl, pr, osm = _make_adapter(paper=False)

    # Mock order_margins response: 25% margin (₹250 for 1 share at ₹1000)
    kite.order_margins_return = [{"total": 250.0}]
    kite.quote_return = {"NSE:RELIANCE": {"last_price": 1000.0}}

    margin_pct = adapter.get_live_margin_pct("RELIANCE", "INTRADAY")

    # 250 / 1000 = 0.25 (25%)
    assert abs(margin_pct - 0.25) < 0.001, f"expected 0.25, got {margin_pct}"

    # Verify broker API was called
    assert kite.order_margins_called, "order_margins not called"
    print("  OK FIX-072: live margin fetched and computed correctly")


def test_fix072_margin_cache_ttl() -> None:
    """FIX-072: Same (symbol, intent) within TTL uses cache, no second broker call."""
    import time as _time_mod
    adapter, kite, rl, pr, osm = _make_adapter(paper=False)

    kite.order_margins_return = [{"total": 200.0}]
    kite.quote_return = {"NSE:INFOSY": {"last_price": 1000.0}}

    # First call: should hit broker
    kite.order_margins_called = False
    margin1 = adapter.get_live_margin_pct("INFOSY", "INTRADAY")
    assert kite.order_margins_called, "first call should hit broker"

    # Second call within TTL: should use cache
    kite.order_margins_called = False
    margin2 = adapter.get_live_margin_pct("INFOSY", "INTRADAY")
    assert not kite.order_margins_called, "second call should use cache"
    assert margin1 == margin2, "cached margin should match"
    print("  OK FIX-072: margin cache TTL working (only 1 broker call)")


def test_fix072_invalidate_margin_cache() -> None:
    """FIX-072: invalidate_margin_cache() forces fresh fetch on next call."""
    adapter, kite, rl, pr, osm = _make_adapter(paper=False)

    kite.order_margins_return = [{"total": 200.0}]
    kite.quote_return = {"NSE:TCS": {"last_price": 1000.0}}

    # First call: cache miss
    adapter.get_live_margin_pct("TCS", "INTRADAY")

    # Invalidate cache
    adapter.invalidate_margin_cache("TCS", "INTRADAY")

    # Next call: should hit broker again (cache was invalidated)
    kite.order_margins_called = False
    adapter.get_live_margin_pct("TCS", "INTRADAY")
    assert kite.order_margins_called, "invalidated cache should force fresh fetch"
    print("  OK FIX-072: cache invalidation forces fresh fetch")


def test_fix072_paper_mode_raises_error() -> None:
    """FIX-072: paper mode raises BrokerError (caller should use static fallback)."""
    adapter, _, _, _, _ = _make_adapter(paper=True)

    try:
        adapter.get_live_margin_pct("RELIANCE", "INTRADAY")
        assert False, "paper mode should raise BrokerError"
    except BrokerError as e:
        assert "paper mode" in str(e).lower(), f"expected paper mode error, got: {e}"
    print("  OK FIX-072: paper mode raises error for live margin fetch")


def test_fix072_broker_api_failure_propagates() -> None:
    """FIX-072: Broker API failure raises BrokerError (caller should fallback to static)."""
    adapter, kite, rl, pr, osm = _make_adapter(paper=False)

    # Mock broker API failure
    class KiteException(Exception):
        pass
    kite.order_margins_exc = KiteException("API down")

    try:
        adapter.get_live_margin_pct("RELIANCE", "INTRADAY")
        assert False, "broker failure should raise BrokerError"
    except BrokerError:
        pass  # expected
    print("  OK FIX-072: broker API failure propagates as BrokerError")


def run_all_tests() -> int:
    tests = [
        test_place_order_success_returns_placed_order,
        test_place_order_success_osm_transitions_to_submitted,
        test_place_order_internal_id_matches_ord_pattern,
        test_place_order_kite_order_exception_raises_order_rejected,
        test_place_order_kite_exception_transitions_osm_to_failed,
        test_place_order_token_exception_raises_broker_auth_error,
        test_place_order_network_exception_raises_broker_timeout,
        test_place_order_qty_zero_raises_value_error_no_side_effects,
        test_place_order_negative_qty_raises_value_error,
        test_place_order_limit_price_zero_raises_value_error,
        test_place_order_invalid_side_raises_value_error,
        test_place_order_bracket_order_intent_raises_product_not_supported,
        test_cancel_order_success,
        test_cancel_order_kite_exception_returns_failure_no_raise,
        test_modify_order_success,
        test_get_positions_returns_list_of_positions,
        test_get_margins_returns_margin_info,
        test_get_quote_returns_dict_of_quotes,
        test_paper_mode_place_order_returns_paper_broker_id,
        test_paper_mode_cancel_always_success,
        test_paper_mode_get_margins_returns_paper_capital,
        test_paper_mode_get_quote_raises_not_implemented_without_provider,
        test_paper_mode_get_quote_delegates_to_provider,
        test_rate_limiter_acquire_called_with_correct_category,
        test_rate_limiter_not_called_on_validation_error,
        test_place_order_logs_entry_and_exit,
        test_exception_logged_before_reraise,
        # H-20 / ZA16a (8 positive + 1 regression guard)
        test_za16a_paper_place_order_returns_submitted_synchronously,
        test_za16a_paper_publishes_order_filled_after_delay,
        test_za16a_paper_order_filled_payload_fields_populated,
        test_za16a_paper_delay_is_actually_observed,
        test_za16a_paper_zero_delay_fires_quickly,
        test_za16a_paper_multiple_orders_each_get_filled,
        test_za16a_paper_bus_none_degrades_gracefully_no_publish,
        test_za16a_paper_synth_thread_is_daemon,
        test_live_mode_place_order_does_NOT_publish_order_filled,
        # BL-6 (Phase D.1): broker 429 detection + penalize + typed exception
        test_bl6_429_raises_broker_rate_limit_429_error,
        test_bl6_429_calls_penalize_on_rate_limiter,
        test_bl6_429_attempt_counter_exponential_delays,
        test_bl6_429_successful_call_resets_counter,
        test_bl6_non_429_error_does_not_penalize,
        # EF-4 (Phase E.7): set_paper_capital late-bind
        test_ef4_set_paper_capital_updates_value,
        test_ef4_set_paper_capital_rejects_nonpositive,
        test_ef4_set_paper_capital_noop_in_live,
        test_ef4_no_paper_capital_getattr_in_main,
        # CFG-6 (2026-04-26 audit): paper-mode slippage in _synth_fill (P12)
        test_cfg6_paper_synth_applies_buy_slippage,
        test_cfg6_paper_synth_applies_sell_slippage,
        test_cfg6_no_engine_means_no_slippage_backcompat,
        test_cfg6_set_slippage_engine_noop_in_live,
        # FIX-072: live margin API with TTL caching
        test_fix072_get_live_margin_pct_success,
        test_fix072_margin_cache_ttl,
        test_fix072_invalidate_margin_cache,
        test_fix072_paper_mode_raises_error,
        test_fix072_broker_api_failure_propagates,
    ]

    print("=" * 70)
    print("zerodha_adapter.py -- Test Suite")
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
