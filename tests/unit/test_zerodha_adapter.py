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
from core.exceptions import (
    BrokerAuthError,
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
) -> tuple[ZerodhaAdapter, MockKite, RateLimiter, OrderStateMachine, Any]:
    """Return (adapter, kite, rl, osm, logger)."""
    from broker.cost_calculator import CostCalculator
    kite = kite or MockKite()
    rl = RateLimiter(_BROKER_LIMITS_CFG, max_wait_sec=5.0)
    pr = ProductResolver(_PRODUCT_MAP)
    cc = CostCalculator(_BROKER_COSTS_CFG)
    osm = OrderStateMachine(bus=None)
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


def test_paper_mode_get_quote_raises_not_implemented_without_provider() -> None:
    adapter, _, _, _, _ = _make_adapter(paper=True)
    raised = False
    try:
        adapter.get_quote(["RELIANCE"])
    except NotImplementedError:
        raised = True
    assert raised, "Expected NotImplementedError for paper get_quote without provider"
    print("  OK paper mode get_quote without provider -> NotImplementedError (ZA10)")


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
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

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
