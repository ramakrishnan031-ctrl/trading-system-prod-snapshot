"""
tests/unit/test_cnc_gtt_slice25_p1.py — SLICE2.5-P1 (25-Jun-2026): CNC OCO-GTT
overnight protection (T1, paper / off-hours).

Covers: the CncGttPlacer (OCO params, one-GTT-per-trade modify, C8 validation), the
ONE centralized gate (DELIVERY → GTT; INTRADAY day legs unchanged), the order_placer
finalize, and the delivery_enabled master lock at the broker boundary (paper). The
real-API proof (real CNC entry + real GTT exit, no TPIN) is T2 — a market-hours script,
not run here.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from core.exceptions import BrokerError, OrderRejectedError
from orders.cnc_gtt import CncGttPlacer

_LOG = logging.getLogger("test_cnc_gtt")


class _RecAdapter:
    """Records place_gtt / modify_gtt params; supplies LTP + tick."""
    def __init__(self, ltp=337.0, tick=0.05):
        self.placed, self.modified = [], []
        self._ltp, self._tick = ltp, tick

    def get_quote(self, syms):
        return {s: SimpleNamespace(last_price=self._ltp) for s in syms}

    def _resolve_tick(self, _s):
        return self._tick

    def place_gtt(self, **kw):
        self.placed.append(kw)
        return "GTT123"

    def modify_gtt(self, **kw):
        self.modified.append(kw)
        return kw["gtt_id"]


def _placer(adapter, delivery_enabled=True):
    return CncGttPlacer(
        adapter, gtt_sl_limit_offset_pct=0.03, gtt_tgt_limit_offset_pct=0.005,
        delivery_enabled=delivery_enabled, logger=_LOG,
        quote_fn=adapter.get_quote, tick_fn=adapter._resolve_tick,
    )


# ── A. CncGttPlacer: OCO params, deep SL floor, fill-ensuring TGT ──────────────

def test_oco_gtt_params_correct():
    a = _RecAdapter(ltp=337.0)
    res = _placer(a).place_for_fill(
        symbol="RAMCOIND", exit_side="SELL", qty=1,
        sl_price=334.38, tgt_price=342.83, trade_id="t1")
    assert len(a.placed) == 1 and not a.modified
    kw = a.placed[0]
    assert kw["symbol"] == "RAMCOIND" and kw["exit_side"] == "SELL" and kw["qty"] == 1
    # triggers tick-rounded + ascending (SL below, TGT above)
    assert kw["sl_trigger"] < kw["tgt_trigger"]
    assert kw["sl_trigger"] == pytest.approx(334.40) and kw["tgt_trigger"] == pytest.approx(342.85)
    # SL leg = DEEP 3% protective floor below the SL trigger
    assert kw["sl_limit"] == pytest.approx(kw["sl_trigger"] * 0.97, abs=0.05)
    assert kw["sl_limit"] < kw["sl_trigger"]
    # TGT leg = small fill-ensuring offset just below the TGT trigger
    assert kw["tgt_trigger"] * 0.99 < kw["tgt_limit"] < kw["tgt_trigger"]
    assert kw["last_price"] == 337.0
    assert res.gtt_id == "GTT123" and not res.modified


def test_one_gtt_per_trade_modifies_on_second_fill():
    a = _RecAdapter(ltp=337.0)
    p = _placer(a)
    p.place_for_fill(symbol="RAMCOIND", exit_side="SELL", qty=1,
                     sl_price=334.38, tgt_price=342.83, trade_id="t1")
    # a later partial fill grows the qty -> MODIFY the same GTT, not a 2nd one
    res2 = p.place_for_fill(symbol="RAMCOIND", exit_side="SELL", qty=3,
                            sl_price=334.38, tgt_price=342.83, trade_id="t1")
    assert len(a.placed) == 1, "must not place a second GTT"
    assert len(a.modified) == 1 and a.modified[0]["gtt_id"] == "GTT123"
    assert a.modified[0]["qty"] == 3 and res2.modified


def test_c8_triggers_must_straddle_ltp():
    # LTP below the SL trigger (SL already breached at fill) -> clear error
    a = _RecAdapter(ltp=333.0)
    with pytest.raises(BrokerError, match="straddle"):
        _placer(a).place_for_fill(symbol="RAMCOIND", exit_side="SELL", qty=1,
                                  sl_price=334.38, tgt_price=342.83, trade_id="t1")
    assert not a.placed


def test_missing_ltp_raises():
    a = _RecAdapter()
    a.get_quote = lambda syms: {}      # no quote
    with pytest.raises(BrokerError, match="no LTP"):
        _placer(a).place_for_fill(symbol="RAMCOIND", exit_side="SELL", qty=1,
                                  sl_price=334.38, tgt_price=342.83, trade_id="t1")


# ── B. The ONE centralized gate (full_entry_engine) ───────────────────────────

class _StubProtocol:
    """A LIMIT_TRIPLE/CO stand-in: records place_exits calls, returns a day-leg result."""
    def __init__(self):
        self.calls = []

    def place_exits(self, **kw):
        from orders.order_protocol_limit import ExitLegsResult
        self.calls.append(kw)
        return ExitLegsResult(
            sl_broker_order_id="SLDAY", sl_internal_id="i1", sl_order_type="SL",
            sl_trigger_price=kw.get("sl_price", 0.0), sl_price=kw.get("sl_price", 0.0),
            tgt_broker_order_id="TGTDAY", tgt_price=kw.get("tgt_price"))


def _engine(placer):
    from orders.full_entry_engine import FullEntryEngine
    limit, co = _StubProtocol(), _StubProtocol()
    eng = FullEntryEngine(co_protocol=co, limit_protocol=limit, logger=_LOG,
                          cnc_gtt_placer=placer)
    return eng, limit, co


def test_gate_delivery_routes_to_gtt_suppresses_day_legs():
    a = _RecAdapter()
    eng, limit, _ = _engine(_placer(a))
    res = eng.place_deferred_exits(
        order_protocol="LIMIT_TRIPLE", symbol="RAMCOIND", entry_side="BUY", qty=1,
        sl_price=334.38, tgt_price=342.83, intent="DELIVERY", trade_id="t1")
    assert res.is_gtt and res.gtt_id == "GTT123"
    assert a.placed and not limit.calls, "CNC day legs must be suppressed"


def test_gate_intraday_unchanged_places_day_legs():
    a = _RecAdapter()
    eng, limit, _ = _engine(_placer(a))
    res = eng.place_deferred_exits(
        order_protocol="LIMIT_TRIPLE", symbol="RELIANCE", entry_side="BUY", qty=1,
        sl_price=2450.0, tgt_price=2600.0, intent="INTRADAY", trade_id="t2")
    assert not res.is_gtt
    assert limit.calls and not a.placed, "INTRADAY path unchanged — day legs placed, no GTT"


# ── C. Adapter parity + delivery_enabled master lock (paper) ───────────────────

def _paper_adapter(delivery_enabled):
    from tests.unit.test_zerodha_adapter import _make_adapter
    adapter, *_ = _make_adapter(paper=True,
                                quote_provider=lambda s: {x: SimpleNamespace(last_price=337.0) for x in s})
    adapter._delivery_enabled = delivery_enabled
    return adapter


def test_paper_place_gtt_returns_mock_id_and_records_legs():
    a = _paper_adapter(delivery_enabled=True)
    gid = a.place_gtt(symbol="RAMCOIND", exit_side="SELL", qty=1,
                      sl_trigger=334.40, sl_limit=324.35, tgt_trigger=342.85,
                      tgt_limit=341.10, last_price=337.0)
    assert gid.startswith("PAPER_GTT_")
    legs = a._gtt_legs("SELL", 1, 324.35, 341.10, "CNC")
    assert all(o["transaction_type"] == "SELL" and o["product"] == "CNC"
               and o["order_type"] == "LIMIT" and o["quantity"] == 1 for o in legs)


def test_guard_split_entry_refused_but_gtt_ops_allowed_when_disabled():
    # SLICE2.5-P2 (R2 guard split): delivery_enabled=false REFUSES a NEW CNC entry,
    # but ALLOWS protective GTT ops (place/modify/get/delete) for an existing
    # holding — overnight protection must survive disablement.
    a = _paper_adapter(delivery_enabled=False)
    # a real CNC entry order is still refused at the boundary
    with pytest.raises(OrderRejectedError, match="delivery_enabled=false"):
        a.place_order(symbol="RAMCOIND", side="BUY", qty=1, price=337.0,
                      order_type="LIMIT", intent="DELIVERY")
    # MIS is unaffected by the lock
    res = a.place_order(symbol="RAMCOIND", side="BUY", qty=1, price=337.0,
                        order_type="LIMIT", intent="INTRADAY")
    assert res.product == "MIS"
    # protective GTT ops ARE allowed even with delivery disabled
    gid = a.place_gtt(symbol="RAMCOIND", exit_side="SELL", qty=1, sl_trigger=334.4,
                      sl_limit=324.3, tgt_trigger=342.8, tgt_limit=341.1, last_price=337.0)
    assert gid.startswith("PAPER_GTT_")
    assert a.modify_gtt(gtt_id=gid, symbol="RAMCOIND", exit_side="SELL", qty=2,
                        sl_trigger=334.4, sl_limit=324.3, tgt_trigger=342.8,
                        tgt_limit=341.1, last_price=337.0) == gid
    assert a.get_gtt(gid)["condition"]["trigger_values"] == [334.4, 342.8]
    assert a.delete_gtt(gid) == gid
    assert a.get_gtt(gid) is None


# ── D. order_placer finalize: records a verified exit, no day-leg rows ─────────

def test_finalize_cnc_gtt_records_verification():
    from orders.order_placer import OrderPlacer
    op = OrderPlacer.__new__(OrderPlacer)        # bypass heavy __init__ for the unit
    op._log = _LOG
    recorded = []
    op._om = SimpleNamespace(record_exits_verification=lambda tid, v, d: recorded.append((tid, v, d)))
    legs = SimpleNamespace(gtt_id="GTT123", sl_trigger_price=334.4, sl_price=324.3, tgt_price=342.8)
    fe = SimpleNamespace(symbol="RAMCOIND")
    op._finalize_cnc_gtt("t1", fe, 1, legs, "entry_fill")
    assert recorded and recorded[0][0] == "t1" and recorded[0][1] == 1
    assert "GTT123" in recorded[0][2]
