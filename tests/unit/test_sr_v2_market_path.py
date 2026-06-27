"""
tests/unit/test_sr_v2_market_path.py — SNR-V2 MARKET entry route + parity.

order_type default LIMIT unchanged; the MARKET route threads through to the
adapter (price=0); paper synthesises the MARKET fill at LTP (paper == live: the
same place_order(order_type="MARKET") call, divergence only at the adapter split).
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace

from core.events import OrderFilled
from core.time_authority import now_ist
from broker.zerodha_adapter import Quote
from orders.order_protocol_limit import LimitTripleProtocol
from tests.unit.test_zerodha_adapter import _make_adapter

_LOG = logging.getLogger("test_sr_v2_market")


class _MockAdapter:
    def __init__(self):
        self.calls = []

    def place_order(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(broker_order_id="B1", internal_order_id="I1")


def _exec(proto, entry_order_type):
    return proto.execute(
        symbol="ACME", side="BUY", qty=10, entry_price=224.30, sl_price=219.5,
        tgt_price=235.0, intent="INTRADAY", trade_id="T1", tag="t",
        entry_order_type=entry_order_type,
    )


def test_default_is_limit_unchanged():
    a = _MockAdapter()
    proto = LimitTripleProtocol(adapter=a, logger=_LOG)
    proto.execute(symbol="ACME", side="BUY", qty=10, entry_price=224.30,
                  sl_price=219.5, tgt_price=235.0, intent="INTRADAY", trade_id="T1", tag="t")
    assert a.calls[0]["order_type"] == "LIMIT"
    assert a.calls[0]["price"] == 224.30           # LIMIT carries the entry price


def test_market_route_threads_through_with_zero_price():
    a = _MockAdapter()
    proto = LimitTripleProtocol(adapter=a, logger=_LOG)
    _exec(proto, "MARKET")
    assert a.calls[0]["order_type"] == "MARKET"
    assert a.calls[0]["price"] == 0.0              # MARKET → price 0 (kite/tick-snap ignore)


class _CaptureBus:
    def __init__(self):
        self.fills = []

    def publish(self, event):
        if isinstance(event, OrderFilled):
            self.fills.append(event)

    def subscribe(self, *a, **k):
        pass


def _ltp_provider(price):
    def provider(symbols):
        return {s: Quote(symbol=s, last_price=price, bid=price - 1, ask=price + 1,
                         volume=5000, ts=now_ist()) for s in symbols}
    return provider


def test_paper_market_fills_at_ltp():
    bus = _CaptureBus()
    adapter, _, _, _, _ = _make_adapter(
        paper=True, quote_provider=_ltp_provider(226.0), bus=bus,
        paper_ltp_gating_enabled=True, paper_auto_fill_delay_sec=0.02,
    )
    placed = adapter.place_order(
        symbol="ACME", side="BUY", qty=10, price=0.0,
        order_type="MARKET", intent="INTRADAY",
    )
    assert placed.order_type == "MARKET"
    # wait for the async synth fill
    deadline = time.monotonic() + 3.0
    while not bus.fills and time.monotonic() < deadline:
        time.sleep(0.02)
    assert bus.fills, "paper MARKET order never synthesised a fill"
    assert abs(bus.fills[0].avg_fill_price - 226.0) < 1e-6   # filled at LTP (parity with live market fill)
