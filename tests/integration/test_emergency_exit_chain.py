"""
tests/integration/test_emergency_exit_chain.py — Wave 1 VALIDATION.

End-to-end integration test proving the four Wave-1 emergency-exit fixes COMPOSE on
one real flatten (what the four isolated unit tests do not show). It drives the REAL
`OrderPlacer._emergency_market_exit` for a LONG OPEN trade with resting SL + TGT and
asserts, in sequence:

  (H-1)  the resting SL + TGT are CANCELLED at the broker BEFORE the flatten is placed.
  (cap)  the flatten is a FIX-181 MARKETABLE LIMIT (LTP-derived), not a raw MARKET order.
  (H-2)  the exit fill drives the EXITING trade to CLOSED immediately, with `release_used`
         called once with REAL non-zero costs and `PositionClosed` published — capital
         freed and the loss recorded now, not via the ~30-min costs=0.0 stuck-exiting sweep.
  (H-3)  a stale exit-retry fired on the now-closed trade places NO second SL.

REAL collaborators (the logic under test runs for real): OrderPlacer, OrderManager,
FullEntryEngine + LIMIT/CO protocols, StateStore over the real core/schema.sql, the real
CostCalculator (broker_costs.yaml), EventBus, and the real methods
`_emergency_market_exit` / `_cancel_trade_resting_exits` (H-1) / `_cancel_broker_orders` /
`_handle_exit_fill` → `close_trade` (H-2) / `_retry_limit_triple_exits` (H-3) /
`_fetch_ltp` + `marketable_limit_price` (cap) / `determine_close_direction`.
SIMULATED (the legitimate broker + capital-sink seams): the broker adapter (records
place/cancel in order, returns a signed LONG position and a live LTP) and the fund
manager (records `release_used`); order_monitor is a stub.

H-12 independence: direction is resolved from an injected, correctly-signed LONG broker
position (net +qty → SELL) via the recording adapter — not the real paper adapter whose
get_positions strips the sign (H-12). A LONG is unambiguous either way; a paper drill of
the SHORT reverse-flatten direction still waits for H-12 (Wave 3).
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import yaml

from broker.cost_calculator import CostCalculator
from broker.order_monitor import OrderMonitor
from broker.product_resolver import ProductResolver
from broker.zerodha_adapter import CancelResult, PlacedOrder
from core.config_loader import BrokerCostsConfig
from core.events import EventBus, OrderFilled, PositionClosed
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.full_entry_engine import FullEntryEngine
from orders.order_manager import OrderManager
from orders.order_placer import OrderPlacer, _ExitRetryParams, _FillEntry
from orders.order_protocol_co import CoPlusTgtProtocol
from orders.order_protocol_limit import LimitTripleProtocol
from orders.price_math import marketable_limit_price

_LOG = logging.getLogger("it_emergency_exit_chain")
_NOW = "2026-07-05T10:00:00+05:30"


class _RecordingAdapter:
    """Broker-boundary simulator: records place/cancel in ORDER, exposes a correctly
    signed LONG position (H-12-independent) and a live LTP via get_quote_raw."""

    def __init__(self, ltp, symbol, position_qty):
        self.ltp = ltp
        self.symbol = symbol
        self.position_qty = position_qty  # signed: long > 0
        self.events = []      # ordered [("cancel", boid) | ("place", rec)]
        self.placed = []
        self.cancelled = []
        self._n = 0

    def get_quote_raw(self, instruments):
        return {inst: {"last_price": self.ltp} for inst in instruments}

    def get_positions(self):
        return [SimpleNamespace(symbol=self.symbol, qty=self.position_qty)]

    def place_order(self, symbol, side, qty, price, order_type, intent,
                    tag=None, trigger_price=0.0, variety="regular"):
        self._n += 1
        po = PlacedOrder(
            internal_order_id=f"int_{self._n}", broker_order_id=f"brk_{self._n}",
            symbol=symbol, side=side, qty=qty, price=price, order_type=order_type,
            product="MIS", status="SUBMITTED", ts=datetime.now(),
        )
        rec = {"symbol": symbol, "side": side, "qty": qty, "price": price,
               "order_type": order_type, "tag": tag,
               "broker_order_id": po.broker_order_id,
               "internal_order_id": po.internal_order_id}
        self.placed.append(rec)
        self.events.append(("place", rec))
        return po

    def cancel_order(self, broker_order_id):
        self.cancelled.append(broker_order_id)
        self.events.append(("cancel", broker_order_id))
        return CancelResult(broker_order_id=broker_order_id, success=True, reason="")


class _RecordingFundManager:
    def __init__(self):
        self.released_used = []

    def release_used(self, **kw):
        self.released_used.append(kw)


def _cost_calculator():
    cfg = BrokerCostsConfig.model_validate(
        yaml.safe_load(Path("config/broker_costs.yaml").read_text(encoding="utf-8")))
    return CostCalculator(cfg)


def _seed_signal(store, sig_id="sig_emx"):
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, symbol, scanner, strategy, triggered_at, "
            "received_at, expires_at, status, fingerprint, fingerprint_date) VALUES "
            "(?, 'RELIANCE', 'gap_go_long', 'gap_go_long', ?, ?, ?, 'PROCESSING', ?, ?)",
            (sig_id, _NOW, _NOW, _NOW, "fp_" + sig_id, _NOW[:10]),
        )
    return sig_id


def _insert_resting_order(store, order_id, trade_id, leg, status):
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO orders (order_id, trade_id, leg, transaction_type, order_type, "
            "product, variety, qty_requested, status, placed_at, updated_at) VALUES "
            "(?, ?, ?, 'SELL', 'SL-M', 'MIS', 'regular', 10, ?, ?, ?)",
            (order_id, trade_id, leg, status, _NOW, _NOW),
        )


def test_emergency_exit_chain_composes(tmp_path):
    store = StateStore(str(tmp_path / "emx.db"))
    adapter = _RecordingAdapter(ltp=2450.0, symbol="RELIANCE", position_qty=10)  # signed LONG
    engine = FullEntryEngine(
        co_protocol=CoPlusTgtProtocol(adapter=adapter, logger=_LOG),
        limit_protocol=LimitTripleProtocol(adapter=adapter, logger=_LOG),
        logger=_LOG,
    )
    om = OrderManager(store, _LOG)
    fm = _RecordingFundManager()
    bus = EventBus()
    placer = OrderPlacer(
        entry_engine=engine, order_manager=om, fund_manager=fm, bus=bus, logger=_LOG,
        order_monitor=MagicMock(spec=OrderMonitor), cost_calculator=_cost_calculator(),
        product_resolver=ProductResolver(
            {"zerodha": {"INTRADAY": "MIS", "DELIVERY": "CNC", "COVER_ORDER": "CO"}}),
        broker_adapter=adapter,   # self._adapter — used by _emergency_market_exit
        mode="LIVE",
    )
    pc_events = []
    bus.subscribe(PositionClosed, lambda e: pc_events.append(e))

    # ── seed a LONG OPEN trade with resting SL + TGT ──
    sig_id = _seed_signal(store)
    trade_id = om.create_trade(
        signal_id=sig_id, symbol="RELIANCE", direction="LONG", strategy="gap_go_long",
        sector=None, qty=10, entry_target_price=2500.0, sl_initial=2450.0,
        tgt_initial=2600.0, order_protocol="LIMIT_TRIPLE",
        margin_reserved=5000.0, risk_amount=500.0,
    )
    om.record_entry_fill(trade_id=trade_id, avg_fill_price=2500.0, qty_filled=10,
                         filled_at=_NOW)
    _insert_resting_order(store, "SL_BRK", trade_id, "SL", "TRIGGER_PENDING")
    _insert_resting_order(store, "TGT_BRK", trade_id, "TGT", "OPEN")

    entry_fe = _FillEntry(
        trade_id=trade_id, reservation_id="res1", symbol="RELIANCE", qty=10, leg="ENTRY",
        order_protocol="LIMIT_TRIPLE", direction="LONG", side="BUY", sl_price=2450.0,
        intent="INTRADAY",
    )

    # ── drive the REAL emergency exit ──
    assert placer._emergency_market_exit(trade_id, entry_fe, qty=10,
                                         reason="sl_placement_failed") is True

    # (H-1) resting SL + TGT cancelled at the broker BEFORE the flatten is placed
    assert set(adapter.cancelled) == {"SL_BRK", "TGT_BRK"}, "H-1: resting legs not cancelled"
    place_idxs = [i for i, (k, _) in enumerate(adapter.events) if k == "place"]
    cancel_idxs = [i for i, (k, _) in enumerate(adapter.events) if k == "cancel"]
    assert place_idxs, "a flatten order must be placed"
    assert all(ci < place_idxs[0] for ci in cancel_idxs), \
        "H-1: the SL/TGT cancels must precede the flatten placement"

    # (cap) the flatten is a FIX-181 marketable LIMIT (LTP-derived), NOT raw MARKET
    flatten = adapter.placed[0]
    assert flatten["side"] == "SELL"                 # LONG → SELL (H-12-independent)
    assert flatten["order_type"] == "LIMIT", "cap: flatten fell back to raw MARKET"
    expected_px = marketable_limit_price(
        "SELL", 2450.0, placer._emergency_exit_buffer_pct, placer._tick_for("RELIANCE"))
    assert flatten["price"] == expected_px and flatten["price"] > 0.0

    # ── drive the exit FILL → H-2 immediate close + release ──
    bus.publish(OrderFilled(
        source_module="test", payload={},
        internal_order_id=flatten["internal_order_id"],
        broker_order_id=flatten["broker_order_id"],
        symbol="RELIANCE", side="SELL", avg_fill_price=2450.0, filled_qty=10,
        filled_at=_NOW,
    ))

    # (H-2) full close+release immediately with REAL, non-zero costs
    row = om.get_trade(trade_id)
    assert row["status"] == "CLOSED", "H-2: EXITING trade did not close on the exit fill"
    assert len(fm.released_used) == 1, "H-2: release_used must be called exactly once"
    assert fm.released_used[0]["costs"] > 0.0, "H-2: real costs, not the 0.0 fallback"
    assert (row["charges"] or 0.0) > 0.0, "H-2: real charges recorded on the trade row"
    assert len(pc_events) == 1 and pc_events[0].trade_id == trade_id, \
        "H-2: PositionClosed must be published"

    # ── (H-3) a stale exit-retry fired on the now-CLOSED trade places NO second SL ──
    n_before = len(adapter.placed)
    placer._retry_limit_triple_exits(_ExitRetryParams(
        trade_id=trade_id, fill_entry=entry_fe, qty_filled=10, avg_fill_price=2500.0,
        reason="ltp_validation", enqueued_at=now_ist(),
    ))
    assert len(adapter.placed) == n_before, \
        "H-3: a stale retry must NOT place a second SL/TGT on a closed trade"

    store.close()
