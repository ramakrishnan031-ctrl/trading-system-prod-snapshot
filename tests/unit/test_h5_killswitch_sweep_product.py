"""
tests/unit/test_h5_killswitch_sweep_product.py — Wave 2, H-5.

The HARD_KILL broker-position sweep (KillSwitch._exit_all_trades_indestructible)
hardcoded intent="INTRADAY" when flattening an orphan broker position. Kite nets
per product, so an orphan CNC position swept with an MIS exit does NOT offset it —
the CNC position stays AND a fresh naked MIS short is created. The first pass is
product-aware (Bug C: _PRODUCT_TO_INTENT.get(product)); the sweep reintroduced the
hardcode.

Fix: read `product` from the broker-position payload and map via _PRODUCT_TO_INTENT
(MIS→INTRADAY, CNC→DELIVERY, NRML→DELIVERY), falling back to INTRADAY only when the
product is absent — mirroring the first pass.

These tests drive the REAL sweep (no local open trades → the first pass is a no-op
and the sweep runs) with a recording adapter that returns one orphan position and
records the intent passed to place_order. The adapter does NOT enforce the delivery
gate — this validates the INTENT CHOSEN per product, not adapter acceptance.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from broker.zerodha_adapter import CancelResult, PlacedOrder
from capital.kill_switch import KillSwitch
from core.events import EventBus
from core.state_store import StateStore


class _SweepAdapter:
    """Broker-boundary sim: returns one orphan position (no matching local trade)
    and records the intent passed to place_order. Does NOT enforce the delivery
    gate — the test asserts the intent CHOSEN, not adapter acceptance."""

    def __init__(self, position):
        self._position = position
        self.placed = []

    def get_positions(self):
        return [self._position]

    def get_quote_raw(self, instruments):
        return {k: {"last_price": 2450.0} for k in instruments}

    def cancel_order(self, oid):
        return CancelResult(broker_order_id=oid, success=True, reason="")

    def place_order(self, symbol, side, qty, order_type, price, intent, tag=None, **kw):
        self.placed.append({"symbol": symbol, "side": side, "qty": qty,
                            "intent": intent, "tag": tag})
        return PlacedOrder(
            internal_order_id="int_s", broker_order_id="brk_s", symbol=symbol,
            side=side, qty=qty, price=price, order_type=order_type, product="MIS",
            status="SUBMITTED", ts=datetime.now(),
        )


def _sweep_intent(tmp_path, position):
    store = StateStore(str(tmp_path / "h5.db"))   # no trades → first pass no-op → sweep runs
    adapter = _SweepAdapter(position)
    ks = KillSwitch(state_store=store, bus=EventBus(),
                    logger=logging.getLogger("test_h5"), adapter=adapter,
                    enable_auto_trip=False)
    ks._exit_all_trades_indestructible()
    store.close()
    assert len(adapter.placed) == 1 and adapter.placed[0]["tag"] == "ks_hard_kill_sweep"
    return adapter.placed[0]["intent"], adapter.placed[0]["side"]


def test_sweep_cnc_orphan_is_spared_q4(tmp_path):
    """[SUPERSEDED CONTRACT, 02-Aug-2026 — ledger #2 / Q4] The original H-5
    test asserted a CNC orphan is flattened under DELIVERY intent ("CNC orphan
    swept as INTRADAY → naked MIS short"). Q4 (Rama, 30-Jul) narrows the
    HARD_KILL invariant to "no live INTRADAY position": a CNC position now
    SURVIVES the kill — the sweep SPARES it (logged CRITICAL-loud) and places
    NOTHING. H-5's real concern (never exit under the WRONG product) lives on
    in the NRML test below, which still flattens under its own DELIVERY intent."""
    store = StateStore(str(tmp_path / "h5.db"))
    adapter = _SweepAdapter(SimpleNamespace(symbol="RELIANCE", qty=10, product="CNC"))
    ks = KillSwitch(state_store=store, bus=EventBus(),
                    logger=logging.getLogger("test_h5"), adapter=adapter,
                    enable_auto_trip=False)
    ks._exit_all_trades_indestructible()
    store.close()
    assert adapter.placed == [], (
        "Q4/ledger #2: a CNC orphan must be SPARED by the HARD_KILL sweep "
        "(delivery survives the kill), not flattened."
    )


def test_sweep_nrml_orphan_uses_delivery_intent(tmp_path):
    """Test 4: an NRML orphan → DELIVERY intent (NRML→DELIVERY). RED on unfixed."""
    intent, _ = _sweep_intent(
        tmp_path, SimpleNamespace(symbol="RELIANCE", qty=10, product="NRML"))
    assert intent == "DELIVERY"


def test_sweep_mis_orphan_uses_intraday_intent(tmp_path):
    """Test 2 (unchanged path): an MIS orphan → INTRADAY (common case preserved)."""
    intent, _ = _sweep_intent(
        tmp_path, SimpleNamespace(symbol="RELIANCE", qty=10, product="MIS"))
    assert intent == "INTRADAY"


def test_sweep_absent_product_falls_back_to_intraday(tmp_path):
    """Test 3: a position with no product → INTRADAY fallback."""
    intent, _ = _sweep_intent(
        tmp_path, SimpleNamespace(symbol="RELIANCE", qty=10))   # no product attr
    assert intent == "INTRADAY"
