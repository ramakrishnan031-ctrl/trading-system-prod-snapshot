"""
tests/unit/test_structure_exit_manager.py -- SNR-V2 Phase B

Covers StructureExitManager (orders/structure_exit_manager.py):
  ACTION A (trail): only-tighten pass/skip, wrong-side skip, tick-snap, LIMIT
                    moves with trigger, static support no-op, per-candle debounce,
                    modify-failure leaves the SL.
  ACTION B (break): LONG/SHORT confirmed break → EXITING→cancel→flatten IN ORDER,
                    weak close / wick → no exit, gap backstop (broker flat → no
                    double-exit).
  Zone selection:   HIGH-only, nearest-beyond, already-broken excluded.
  Scope:            CO/CNC excluded; no-zone / no-trade → do nothing.
  Parity:           real paper == live for modify / cancel / flatten, and the
                    manager logic is mode-agnostic.
  Dormancy:         disabled → not subscribed, not running, no action.
"""
from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace

from core.config_loader import StructureExitConfig
from orders.price_math import calc_sl_limit_price, marketable_limit_price, round_to_tick
from orders.structure_exit_manager import StructureExitManager

NOW = datetime(2026, 6, 27, 10, 0, 0)
TICK = 0.05
_LOG = logging.getLogger("test_structure_exit")


# ── fakes ────────────────────────────────────────────────────────────────────

class Candle:
    def __init__(self, symbol, open, high, low, close, ts=NOW):
        self.symbol = symbol
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.ts = ts


class Zone:
    def __init__(self, band_low, band_high, confidence="HIGH"):
        self.band_low = band_low
        self.band_high = band_high
        self.confidence = confidence

    @property
    def center(self):
        return (self.band_low + self.band_high) / 2.0


class ZoneSet:
    def __init__(self, resistance=(), support=()):
        self.resistance = tuple(resistance)
        self.support = tuple(support)


class ZoneCache:
    def __init__(self, mapping=None):
        self._m = mapping or {}

    def get(self, symbol):
        return self._m.get(symbol)


class RecCursor:
    """Records every execute() and classifies the structure-exit writes."""

    def __init__(self, store):
        self._s = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        norm = " ".join(sql.split()).upper()
        self._s.sql_log.append((norm, params))
        if norm.startswith("UPDATE TRADES SET STATUS") and "EXITING" in [str(p) for p in params]:
            self._s.events.append("EXITING")
            self._s.trade_status = "EXITING"
        elif norm.startswith("UPDATE ORDERS SET STATUS = 'CANCELLED'"):
            self._s.events.append(("DBCANCEL", params[-1]))
        elif norm.startswith("INSERT OR IGNORE INTO ORDERS"):
            self._s.events.append("EOD_INSERT")
            self._s.eod_inserted = True


class FakeStore:
    def __init__(self, trades, sl_leg, resting, events):
        self._trades = trades
        self._sl_leg = sl_leg
        self._resting = resting
        self.events = events
        self.sql_log = []
        self.trade_status = None
        self.eod_inserted = False

    def get_open_intraday_positions(self):
        return list(self._trades)

    def fetch_one(self, sql, params=()):
        return self._sl_leg

    def fetch_all(self, sql, params=()):
        return list(self._resting)

    def transaction(self):
        return RecCursor(self)


class FakeAdapter:
    """Mode-agnostic adapter double (records calls; parity is proven separately
    against the REAL adapter's paper/live split)."""

    def __init__(self, events, ltp=100.0, positions=None, modify_ok=True, cancel_ok=True):
        self.events = events
        self._ltp = ltp
        self._positions = positions if positions is not None else []
        self.modify_ok = modify_ok
        self.cancel_ok = cancel_ok
        self.modify_calls = []
        self.cancel_calls = []
        self.place_calls = []

    def modify_order(self, order_id, price=None, qty=None, trigger_price=None, symbol=None):
        self.modify_calls.append(
            dict(order_id=order_id, price=price, trigger_price=trigger_price, symbol=symbol)
        )
        self.events.append(("MODIFY", trigger_price, price))
        return SimpleNamespace(success=self.modify_ok, reason="" if self.modify_ok else "rejected")

    def cancel_order(self, order_id, variety="regular"):
        self.cancel_calls.append(order_id)
        self.events.append(("CANCEL", order_id))
        return SimpleNamespace(success=self.cancel_ok, reason="")

    def get_quote(self, symbols):
        return {symbols[0]: SimpleNamespace(last_price=self._ltp)}

    def get_positions(self):
        return list(self._positions)

    def place_order(self, **kw):
        self.place_calls.append(kw)
        self.events.append(("PLACE", kw.get("side"), kw.get("qty")))
        return SimpleNamespace(
            internal_order_id="ord_struct", broker_order_id="BRK_STRUCT",
            price=kw.get("price"), ts=NOW,
        )


class FakeMonitor:
    def __init__(self):
        self.tracked = []

    def track(self, **kw):
        self.tracked.append(kw)


def trade_row(direction="LONG", symbol="RELIANCE", qty=10, protocol="LIMIT_TRIPLE", trade_id="T1"):
    return {"trade_id": trade_id, "symbol": symbol, "direction": direction,
            "qty_filled": qty, "order_protocol": protocol}


def sl_leg(order_id="SL1", trigger=90.0, price=None):
    return {"order_id": order_id, "trigger_price": trigger, "price": price}


def make_manager(*, trades, sl_leg=None, resting=(), zones=None, ltp=100.0,
                 positions=None, modify_ok=True, cancel_ok=True,
                 order_monitor=None, cfg_over=None, mode="LIVE"):
    events = []
    store = FakeStore(trades, sl_leg, list(resting), events)
    adapter = FakeAdapter(events, ltp=ltp, positions=positions,
                          modify_ok=modify_ok, cancel_ok=cancel_ok)
    zc = ZoneCache(zones or {})
    base = dict(structure_exit_enabled=True, sl_buffer_pct=0.2, break_buffer_pct=0.0,
                require_strong_close=True, break_strong_close_frac=0.6,
                min_zone_confidence="HIGH")
    if cfg_over:
        base.update(cfg_over)
    cfg = StructureExitConfig(**base)
    mgr = StructureExitManager(
        adapter=adapter, state_store=store, zone_cache=zc, config=cfg,
        logger=_LOG, now_fn=lambda: NOW, instrument_cache=None,
        order_monitor=order_monitor, candle_store=None, notifier=None, mode=mode,
    )
    mgr.start()  # candle_store None → just flips _running True
    return mgr, adapter, store, events


# ── ACTION A: trail SL to structure ──────────────────────────────────────────

def test_action_a_long_trail_tightens_snaps_and_moves_limit():
    zones = {"RELIANCE": ZoneSet(support=[Zone(98.0, 99.0, "HIGH")])}
    c = Candle("RELIANCE", 104.0, 105.0, 103.5, 105.0)  # not a break (close>band_low)
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=90.0), zones=zones, ltp=105.0)
    mgr.on_1m_close(c)

    new_sl = round_to_tick(98.0 * 0.998, TICK, "nearest")   # 97.804 → 97.80
    assert new_sl == 97.8
    assert len(ad.modify_calls) == 1
    mc = ad.modify_calls[0]
    assert mc["trigger_price"] == new_sl
    # LIMIT moves with the trigger (SELL stop → below trigger), tick-snapped.
    exp_limit = calc_sl_limit_price("SELL", new_sl, 0.005, TICK)
    assert mc["price"] == exp_limit and exp_limit < new_sl
    assert abs(round(exp_limit / TICK) - exp_limit / TICK) < 1e-9   # on a tick
    assert mgr._last_sl_move_candle_ts["T1"] == c.ts.isoformat()    # debounce armed


def test_action_a_only_tighten_skips_when_not_higher():
    zones = {"RELIANCE": ZoneSet(support=[Zone(98.0, 99.0, "HIGH")])}
    c = Candle("RELIANCE", 104.0, 105.0, 103.5, 105.0)
    mgr, ad, store, ev = make_manager(   # current SL 99 ≥ new_sl 97.8 → skip
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=99.0), zones=zones, ltp=105.0)
    mgr.on_1m_close(c)
    assert ad.modify_calls == []
    assert "T1" not in mgr._last_sl_move_candle_ts


def test_action_a_static_support_no_redundant_modify():
    zones = {"RELIANCE": ZoneSet(support=[Zone(98.0, 99.0, "HIGH")])}
    c = Candle("RELIANCE", 104.0, 105.0, 103.5, 105.0)
    mgr, ad, store, ev = make_manager(   # current SL == new_sl 97.8 → strict > fails
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=97.8), zones=zones, ltp=105.0)
    mgr.on_1m_close(c)
    assert ad.modify_calls == []


def test_action_a_wrong_side_skip_long():
    zones = {"RELIANCE": ZoneSet(support=[Zone(98.0, 99.0, "HIGH")])}
    c = Candle("RELIANCE", 98.5, 99.5, 98.2, 99.0)       # selected; not a break
    mgr, ad, store, ev = make_manager(   # ltp 97.5 ≤ new_sl 97.8 → wrong-side skip
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=90.0), zones=zones, ltp=97.5)
    mgr.on_1m_close(c)
    assert ad.modify_calls == []
    assert "T1" not in mgr._last_sl_move_candle_ts


def test_action_a_debounce_one_move_per_candle():
    zones = {"RELIANCE": ZoneSet(support=[Zone(98.0, 99.0, "HIGH")])}
    c = Candle("RELIANCE", 104.0, 105.0, 103.5, 105.0, ts=datetime(2026, 6, 27, 10, 0))
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=90.0), zones=zones, ltp=105.0)
    mgr.on_1m_close(c)
    mgr.on_1m_close(c)               # same candle.ts → debounced
    assert len(ad.modify_calls) == 1

    # a NEW candle (new ts) with a higher support → allowed again
    mgr._zone_cache._m["RELIANCE"] = ZoneSet(support=[Zone(99.0, 100.0, "HIGH")])
    c2 = Candle("RELIANCE", 104.0, 105.0, 103.5, 105.0, ts=datetime(2026, 6, 27, 10, 1))
    mgr.on_1m_close(c2)
    assert len(ad.modify_calls) == 2


def test_action_a_modify_failure_leaves_sl_and_no_debounce():
    zones = {"RELIANCE": ZoneSet(support=[Zone(98.0, 99.0, "HIGH")])}
    c = Candle("RELIANCE", 104.0, 105.0, 103.5, 105.0)
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=90.0), zones=zones,
        ltp=105.0, modify_ok=False)
    mgr.on_1m_close(c)
    assert len(ad.modify_calls) == 1                  # attempted once
    assert "T1" not in mgr._last_sl_move_candle_ts     # not armed on failure


def test_action_a_short_trail_tightens_down():
    zones = {"RELIANCE": ZoneSet(resistance=[Zone(101.0, 102.0, "HIGH")])}
    c = Candle("RELIANCE", 96.0, 96.5, 95.0, 95.0)     # below resistance; not a break
    mgr, ad, store, ev = make_manager(   # current SL 110 → new_sl 102*1.002≈102.20 < 110 → tighten
        trades=[trade_row("SHORT")], sl_leg=sl_leg(trigger=110.0), zones=zones, ltp=95.0)
    mgr.on_1m_close(c)
    new_sl = round_to_tick(102.0 * 1.002, TICK, "nearest")  # 102.204 → 102.20
    assert len(ad.modify_calls) == 1
    assert ad.modify_calls[0]["trigger_price"] == new_sl
    exp_limit = calc_sl_limit_price("BUY", new_sl, 0.005, TICK)   # BUY stop → above trigger
    assert ad.modify_calls[0]["price"] == exp_limit and exp_limit > new_sl


# ── ACTION B: confirmed-break exit ───────────────────────────────────────────

def test_action_b_long_break_exits_in_strict_order():
    zones = {"RELIANCE": ZoneSet(support=[Zone(100.0, 101.0, "HIGH")])}
    c = Candle("RELIANCE", 100.4, 100.5, 97.8, 98.0)   # close<band_low, strong LOWER close
    om = FakeMonitor()
    pos = [SimpleNamespace(symbol="RELIANCE", qty=10, product="MIS")]
    resting = [{"order_id": "SL1", "leg": "SL"}, {"order_id": "TGT1", "leg": "TGT"}]
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=90.0), resting=resting,
        zones=zones, ltp=98.0, positions=pos, order_monitor=om)
    mgr.on_1m_close(c)

    # strict order: EXITING → cancels → flatten place.
    assert ev[0] == "EXITING"
    exiting_idx = ev.index("EXITING")
    cancel_idxs = [i for i, e in enumerate(ev) if isinstance(e, tuple) and e[0] == "CANCEL"]
    place_idx = [i for i, e in enumerate(ev) if isinstance(e, tuple) and e[0] == "PLACE"][0]
    assert exiting_idx < min(cancel_idxs)
    assert max(cancel_idxs) < place_idx
    assert ("CANCEL", "SL1") in ev and ("CANCEL", "TGT1") in ev

    # flatten side/qty + marketable LIMIT.
    p = ad.place_calls[0]
    assert p["side"] == "SELL" and p["qty"] == 10 and p["intent"] == "INTRADAY"
    assert p["order_type"] == "LIMIT"
    assert p["price"] == marketable_limit_price("SELL", 98.0, 0.01, TICK)
    assert ad.modify_calls == []                       # ACTION B, not A
    assert store.trade_status == "EXITING"
    assert store.eod_inserted
    assert len(om.tracked) == 1 and om.tracked[0]["leg"] == "EOD"


def test_action_b_short_break_exits():
    zones = {"RELIANCE": ZoneSet(resistance=[Zone(100.0, 101.0, "HIGH")])}
    c = Candle("RELIANCE", 101.2, 103.2, 100.5, 103.0)  # close>band_high, strong UPPER close
    om = FakeMonitor()
    pos = [SimpleNamespace(symbol="RELIANCE", qty=10, product="MIS")]
    resting = [{"order_id": "SL1", "leg": "SL"}, {"order_id": "TGT1", "leg": "TGT"}]
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("SHORT")], sl_leg=sl_leg(trigger=110.0), resting=resting,
        zones=zones, ltp=103.0, positions=pos, order_monitor=om)
    mgr.on_1m_close(c)
    assert ev[0] == "EXITING"
    assert ad.place_calls[0]["side"] == "BUY" and ad.place_calls[0]["qty"] == 10
    assert store.trade_status == "EXITING" and store.eod_inserted


def test_action_b_weak_close_no_exit():
    zones = {"RELIANCE": ZoneSet(support=[Zone(100.0, 101.0, "HIGH")])}
    # close below the band but a WEAK lower close (closes near the candle high)
    c = Candle("RELIANCE", 100.2, 100.5, 92.0, 99.9)
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=99.85), resting=[{"order_id": "SL1", "leg": "SL"}],
        zones=zones, ltp=99.9, positions=[SimpleNamespace(symbol="RELIANCE", qty=10, product="MIS")])
    mgr.on_1m_close(c)
    assert ad.place_calls == []                # no exit
    assert store.trade_status != "EXITING"
    assert store.eod_inserted is False


def test_action_b_wick_no_exit():
    zones = {"RELIANCE": ZoneSet(support=[Zone(100.0, 101.0, "HIGH")])}
    # wick: low pierces below the band but the close is back inside → not a break
    c = Candle("RELIANCE", 100.6, 101.0, 98.0, 100.5)
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=99.85), resting=[{"order_id": "SL1", "leg": "SL"}],
        zones=zones, ltp=100.5, positions=[SimpleNamespace(symbol="RELIANCE", qty=10, product="MIS")])
    mgr.on_1m_close(c)
    assert ad.place_calls == []
    assert store.trade_status != "EXITING"


def test_action_b_no_strong_close_required_exits_on_weak_when_disabled():
    # require_strong_close=False → a plain close beyond the band exits.
    zones = {"RELIANCE": ZoneSet(support=[Zone(100.0, 101.0, "HIGH")])}
    c = Candle("RELIANCE", 100.2, 100.5, 92.0, 99.9)   # weak lower close, but broke
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=90.0), resting=[{"order_id": "SL1", "leg": "SL"}],
        zones=zones, ltp=99.9, positions=[SimpleNamespace(symbol="RELIANCE", qty=10, product="MIS")],
        cfg_over={"require_strong_close": False})
    mgr.on_1m_close(c)
    assert store.trade_status == "EXITING"
    assert ad.place_calls and ad.place_calls[0]["side"] == "SELL"


# ── gap backstop ─────────────────────────────────────────────────────────────

def test_gap_backstop_no_open_trade_no_action():
    # broker SL filled first → no local OPEN trade → return early (no double-exit).
    zones = {"RELIANCE": ZoneSet(support=[Zone(100.0, 101.0, "HIGH")])}
    c = Candle("RELIANCE", 100.4, 100.5, 97.0, 98.0)
    mgr, ad, store, ev = make_manager(
        trades=[], sl_leg=None, zones=zones, ltp=98.0,
        positions=[SimpleNamespace(symbol="RELIANCE", qty=10, product="MIS")])
    mgr.on_1m_close(c)
    assert ad.place_calls == [] and ad.modify_calls == []
    assert store.trade_status is None


def test_gap_backstop_broker_flat_skips_flatten():
    # local trade still OPEN (DB lag) but broker reports flat → mark EXITING, cancel,
    # but DO NOT place a flatten (no double-exit).
    zones = {"RELIANCE": ZoneSet(support=[Zone(100.0, 101.0, "HIGH")])}
    c = Candle("RELIANCE", 100.4, 100.5, 97.0, 98.0)
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=90.0),
        resting=[{"order_id": "SL1", "leg": "SL"}], zones=zones, ltp=98.0, positions=[])
    mgr.on_1m_close(c)
    assert store.trade_status == "EXITING"
    assert ad.place_calls == []                # broker flat → no flatten


# ── zone selection ───────────────────────────────────────────────────────────

def test_zone_selection_high_only():
    zones = {"RELIANCE": ZoneSet(support=[Zone(98.0, 99.0, "MEDIUM")])}   # not HIGH
    c = Candle("RELIANCE", 104.0, 105.0, 103.5, 105.0)
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=90.0), zones=zones, ltp=105.0)
    mgr.on_1m_close(c)
    assert ad.modify_calls == [] and ad.place_calls == []


def test_zone_selection_nearest_below_chosen_long():
    zones = {"RELIANCE": ZoneSet(support=[Zone(90.0, 91.0, "HIGH"), Zone(98.0, 99.0, "HIGH")])}
    c = Candle("RELIANCE", 104.0, 105.0, 103.5, 105.0)
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=80.0), zones=zones, ltp=105.0)
    mgr.on_1m_close(c)
    new_sl = round_to_tick(98.0 * 0.998, TICK, "nearest")   # from the NEARER 98–99 zone
    assert ad.modify_calls[0]["trigger_price"] == new_sl


def test_zone_selection_already_broken_not_selected():
    # the only support sits entirely above the candle → excluded (band_low > high).
    zones = {"RELIANCE": ZoneSet(support=[Zone(110.0, 111.0, "HIGH")])}
    c = Candle("RELIANCE", 104.0, 105.0, 103.5, 105.0)
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=90.0), zones=zones, ltp=105.0)
    mgr.on_1m_close(c)
    assert ad.modify_calls == [] and ad.place_calls == []


def test_no_zone_does_nothing():
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=90.0), zones={}, ltp=105.0)
    mgr.on_1m_close(Candle("RELIANCE", 104.0, 105.0, 103.5, 105.0))
    assert ad.modify_calls == [] and ad.place_calls == []
    assert store.trade_status is None


# ── scope: CO / CNC excluded ─────────────────────────────────────────────────

def test_co_trade_excluded():
    zones = {"RELIANCE": ZoneSet(support=[Zone(100.0, 101.0, "HIGH")])}
    c = Candle("RELIANCE", 100.4, 100.5, 97.0, 98.0)   # would break if it were LIMIT_TRIPLE
    mgr, ad, store, ev = make_manager(
        trades=[trade_row("LONG", protocol="CO_PLUS_TGT")], sl_leg=None, zones=zones,
        ltp=98.0, positions=[SimpleNamespace(symbol="RELIANCE", qty=10, product="MIS")])
    mgr.on_1m_close(c)
    assert ad.place_calls == [] and ad.modify_calls == []
    assert store.trade_status is None


# ── parity: real paper == live ───────────────────────────────────────────────

def _real_adapters():
    from tests.unit.test_zerodha_adapter import MockKite, _make_adapter

    class RecKite(MockKite):
        def __init__(self):
            super().__init__()
            self.modify_kwargs = []

        def modify_order(self, **kw):
            self.modify_kwargs.append(kw)
            return super().modify_order(**kw)

    paper, _, _, _, _ = _make_adapter(paper=True)
    rk = RecKite()
    live, _, _, _, _ = _make_adapter(kite=rk, paper=False)
    return paper, live, rk


def test_parity_modify_cancel_flatten_paper_equals_live():
    paper, live, rk = _real_adapters()
    trig = 97.8
    lim = calc_sl_limit_price("SELL", trig, 0.005, TICK)

    # modify_order: both succeed; live kite receives the same tick-snapped values.
    rp = paper.modify_order("SL1", price=lim, trigger_price=trig, symbol="RELIANCE")
    rl = live.modify_order("SL1", price=lim, trigger_price=trig, symbol="RELIANCE")
    assert rp.success is True and rl.success is True
    assert rk.modify_kwargs[0]["trigger_price"] == trig and rk.modify_kwargs[0]["price"] == lim

    # cancel_order: both succeed.
    assert paper.cancel_order("SL1").success is True
    assert live.cancel_order("SL1").success is True

    # place_order (flatten): both return a PlacedOrder with both ids.
    pp = paper.place_order(symbol="RELIANCE", side="SELL", qty=10, price=97.0,
                           order_type="LIMIT", intent="INTRADAY", tag="STRUCT_EXIT")
    pl = live.place_order(symbol="RELIANCE", side="SELL", qty=10, price=97.0,
                          order_type="LIMIT", intent="INTRADAY", tag="STRUCT_EXIT")
    assert pp.broker_order_id and pl.broker_order_id
    assert pp.internal_order_id.startswith("ord_") and pl.internal_order_id.startswith("ord_")


def test_parity_manager_is_mode_agnostic():
    zones = {"RELIANCE": ZoneSet(support=[Zone(100.0, 101.0, "HIGH")])}
    c = Candle("RELIANCE", 100.4, 100.5, 97.8, 98.0)
    seqs = {}
    for mode in ("PAPER", "LIVE"):
        mgr, ad, store, ev = make_manager(
            trades=[trade_row("LONG")], sl_leg=sl_leg(trigger=90.0),
            resting=[{"order_id": "SL1", "leg": "SL"}, {"order_id": "TGT1", "leg": "TGT"}],
            zones=zones, ltp=98.0,
            positions=[SimpleNamespace(symbol="RELIANCE", qty=10, product="MIS")],
            order_monitor=FakeMonitor(), mode=mode)
        mgr.on_1m_close(c)
        seqs[mode] = list(ev)
    assert seqs["PAPER"] == seqs["LIVE"]   # identical event sequence regardless of mode


# ── dormancy ─────────────────────────────────────────────────────────────────

class _RecCandleStore:
    def __init__(self):
        self.subs = []

    def register_on_candle_close(self, fn):
        self.subs.append(fn)

    def unregister_on_candle_close(self, fn):
        try:
            self.subs.remove(fn)
        except ValueError:
            pass


def _disabled_manager():
    events = []
    store = FakeStore([trade_row("LONG")], sl_leg(trigger=90.0), [], events)
    adapter = FakeAdapter(events, positions=[SimpleNamespace(symbol="RELIANCE", qty=10, product="MIS")])
    cfg = StructureExitConfig(structure_exit_enabled=False)
    cs = _RecCandleStore()
    mgr = StructureExitManager(
        adapter=adapter, state_store=store,
        zone_cache=ZoneCache({"RELIANCE": ZoneSet(support=[Zone(100.0, 101.0, "HIGH")])}),
        config=cfg, logger=_LOG, now_fn=lambda: NOW, candle_store=cs)
    return mgr, adapter, store, cs


def test_dormant_when_disabled_no_subscribe_no_action():
    mgr, adapter, store, cs = _disabled_manager()
    mgr.start()
    assert cs.subs == []            # NOT subscribed
    assert mgr._running is False
    # even a would-be-break candle does nothing
    mgr.on_1m_close(Candle("RELIANCE", 100.4, 100.5, 97.0, 98.0))
    assert adapter.modify_calls == [] and adapter.cancel_calls == [] and adapter.place_calls == []
    assert store.trade_status is None


def test_enabled_subscribes_and_unsubscribes():
    events = []
    store = FakeStore([], None, [], events)
    adapter = FakeAdapter(events)
    cfg = StructureExitConfig(structure_exit_enabled=True)
    cs = _RecCandleStore()
    mgr = StructureExitManager(
        adapter=adapter, state_store=store, zone_cache=ZoneCache({}),
        config=cfg, logger=_LOG, now_fn=lambda: NOW, candle_store=cs)
    mgr.start()
    assert cs.subs == [mgr.on_1m_close] and mgr._running is True
    mgr.stop()
    assert cs.subs == [] and mgr._running is False
