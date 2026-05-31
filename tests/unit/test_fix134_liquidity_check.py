"""Tests for FIX-134 Item 38: order book depth/spread check before entry."""
from __future__ import annotations

import logging
import types
from unittest.mock import MagicMock

import pytest


def _make_checker(
    liquidity_enabled=True,
    max_spread_pct=0.5,
    min_depth_qty=500,
    quote_data=None,
    mode="LIVE",
    notifier=None,
):
    """
    Build a minimal object with _check_liquidity wired.
    We bind the method from OrderPlacer onto a SimpleNamespace
    to avoid the heavyweight OrderPlacer constructor.
    """
    from orders.order_placer import OrderPlacer

    obj = types.SimpleNamespace()
    obj._liquidity_check_enabled = liquidity_enabled
    obj._liquidity_max_spread_pct = max_spread_pct
    obj._liquidity_min_depth_qty = min_depth_qty
    obj._log = logging.getLogger("test_liquidity")
    obj._notifier = notifier
    obj._mode = mode

    kite_mock = MagicMock()
    if quote_data is not None:
        kite_mock.quote.return_value = quote_data
    else:
        kite_mock.quote.return_value = {}

    adapter_mock = MagicMock()
    adapter_mock._kite = kite_mock
    obj._adapter = adapter_mock

    obj._check_liquidity = OrderPlacer._check_liquidity.__get__(obj)
    return obj, kite_mock


def _good_quote(symbol="RELIANCE", ltp=2500.0, bid=2499.0, ask=2501.0,
                buy_qty=1000, sell_qty=1000):
    """Build a quote dict that passes both spread and depth checks."""
    return {
        f"NSE:{symbol}": {
            "last_price": ltp,
            "depth": {
                "buy": [{"price": bid, "quantity": buy_qty}],
                "sell": [{"price": ask, "quantity": sell_qty}],
            },
        }
    }


# ── Disabled check ────────────────────────────────────────────────────────


class TestDisabledCheck:
    def test_disabled_returns_ok(self):
        obj, _ = _make_checker(liquidity_enabled=False)
        ok, reason = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True
        assert reason == ""

    def test_disabled_skips_quote_call(self):
        obj, kite = _make_checker(liquidity_enabled=False)
        obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        kite.quote.assert_not_called()


# ── Spread check ─────────────────────────────────────────────────────────


class TestSpreadCheck:
    def test_narrow_spread_passes(self):
        quote = _good_quote(bid=2499.0, ask=2501.0, ltp=2500.0)
        obj, _ = _make_checker(max_spread_pct=0.5, quote_data=quote)
        ok, reason = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True

    def test_wide_spread_rejected(self):
        quote = _good_quote(bid=2490.0, ask=2510.0, ltp=2500.0)
        obj, _ = _make_checker(max_spread_pct=0.5, quote_data=quote)
        ok, reason = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is False
        assert "spread" in reason

    def test_exact_threshold_passes(self):
        quote = _good_quote(bid=2493.75, ask=2506.25, ltp=2500.0)
        obj, _ = _make_checker(max_spread_pct=0.5, quote_data=quote)
        ok, reason = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True

    def test_just_above_threshold_rejected(self):
        quote = _good_quote(bid=2493.0, ask=2507.0, ltp=2500.0)
        obj, _ = _make_checker(max_spread_pct=0.5, quote_data=quote)
        ok, reason = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is False


# ── Depth check ──────────────────────────────────────────────────────────


class TestDepthCheck:
    def test_sufficient_depth_passes(self):
        quote = _good_quote(buy_qty=1000, sell_qty=1000)
        obj, _ = _make_checker(min_depth_qty=500, quote_data=quote)
        ok, reason = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True

    def test_buy_side_checks_sell_depth(self):
        quote = _good_quote(buy_qty=1000, sell_qty=100)
        obj, _ = _make_checker(min_depth_qty=500, quote_data=quote)
        ok, reason = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is False
        assert "depth" in reason

    def test_sell_side_checks_buy_depth(self):
        quote = _good_quote(buy_qty=100, sell_qty=1000)
        obj, _ = _make_checker(min_depth_qty=500, quote_data=quote)
        ok, reason = obj._check_liquidity("RELIANCE", "SELL", "t1", "s1")
        assert ok is False
        assert "depth" in reason

    def test_exact_min_depth_passes(self):
        quote = _good_quote(sell_qty=500)
        obj, _ = _make_checker(min_depth_qty=500, quote_data=quote)
        ok, reason = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True

    def test_one_below_min_depth_rejected(self):
        quote = _good_quote(sell_qty=499)
        obj, _ = _make_checker(min_depth_qty=500, quote_data=quote)
        ok, reason = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is False


# ── Best-effort fallback ─────────────────────────────────────────────────


class TestBestEffort:
    def test_empty_quote_returns_ok(self):
        obj, _ = _make_checker(quote_data={})
        ok, _ = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True

    def test_missing_symbol_returns_ok(self):
        obj, _ = _make_checker(quote_data={"NSE:OTHER": {}})
        ok, _ = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True

    def test_zero_ltp_returns_ok(self):
        quote = _good_quote(ltp=0.0)
        obj, _ = _make_checker(quote_data=quote)
        ok, _ = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True

    def test_zero_bid_returns_ok(self):
        quote = _good_quote(bid=0.0)
        obj, _ = _make_checker(quote_data=quote)
        ok, _ = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True

    def test_exception_returns_ok(self):
        obj, kite = _make_checker()
        kite.quote.side_effect = RuntimeError("API down")
        ok, _ = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True

    def test_no_depth_key_returns_ok(self):
        obj, _ = _make_checker(quote_data={
            "NSE:RELIANCE": {"last_price": 2500.0}
        })
        ok, _ = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True

    def test_empty_buy_depth_returns_ok(self):
        obj, _ = _make_checker(quote_data={
            "NSE:RELIANCE": {
                "last_price": 2500.0,
                "depth": {"buy": [], "sell": [{"price": 2501, "quantity": 1000}]},
            }
        })
        ok, _ = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True


# ── Telegram alerts ──────────────────────────────────────────────────────


class TestTelegramAlerts:
    def test_spread_rejection_sends_alert(self):
        notifier = MagicMock()
        quote = _good_quote(bid=2490.0, ask=2510.0, ltp=2500.0)
        obj, _ = _make_checker(max_spread_pct=0.5, quote_data=quote, notifier=notifier)
        obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert notifier.send.called
        kw = notifier.send.call_args.kwargs
        assert "LIQUIDITY" in kw["title"]
        assert "Spread" in kw["body"]

    def test_depth_rejection_sends_alert(self):
        notifier = MagicMock()
        quote = _good_quote(sell_qty=100)
        obj, _ = _make_checker(min_depth_qty=500, quote_data=quote, notifier=notifier)
        obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert notifier.send.called
        kw = notifier.send.call_args.kwargs
        assert "Depth" in kw["body"]

    def test_notifier_failure_silenced(self):
        notifier = MagicMock()
        notifier.send.side_effect = RuntimeError("Telegram down")
        quote = _good_quote(bid=2490.0, ask=2510.0, ltp=2500.0)
        obj, _ = _make_checker(max_spread_pct=0.5, quote_data=quote, notifier=notifier)
        ok, reason = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is False

    def test_no_alert_on_pass(self):
        notifier = MagicMock()
        quote = _good_quote()
        obj, _ = _make_checker(quote_data=quote, notifier=notifier)
        obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        notifier.send.assert_not_called()


# ── Config model ─────────────────────────────────────────────────────────


class TestConfigModel:
    def test_entry_gate_config_has_liquidity_fields(self):
        from core.config_loader import EntryGateConfig
        cfg = EntryGateConfig(slippage_buffer=2.0)
        assert cfg.max_spread_pct == 0.5
        assert cfg.min_depth_qty == 500
        assert cfg.liquidity_check_enabled is True

    def test_parity_paper_skips(self):
        """Paper mode: liquidity_check_enabled=False at construction (mode != LIVE in place())."""
        obj, kite = _make_checker(liquidity_enabled=False, mode="PAPER")
        ok, _ = obj._check_liquidity("RELIANCE", "BUY", "t1", "s1")
        assert ok is True
        kite.quote.assert_not_called()
