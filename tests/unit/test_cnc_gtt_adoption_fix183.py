"""
tests/unit/test_cnc_gtt_adoption_fix183.py — FIX-183 orphan-GTT ADOPTION.

A LIVE broker GTT (status 'active') with NO gtt_state row is ADOPTED: its row is
reconstructed from the broker GTT (SL/TGT split by trigger MAGNITUDE) and correlated
to its open delivery (CNC) trade — restoring "broker GTT = authority, gtt_state =
self-healing mirror". Wired as a NARROW prepass at the top of reconcile_once() so it
runs BEFORE CHECK1 (the C2.1 mis-close gap).

Paper, injected-state (no live broker): a real StateStore (v36) + a real paper
ZerodhaAdapter (paper get_gtts/holdings stores, identical dict shape) + a real
store-wired CncGttPlacer. A "persist-failed" orphan = a broker GTT placed straight
via adapter.place_gtt() (writes NO gtt_state row); a "carried holding" = seed_paper_
holding() (so the symbol is in holdings(), NOT positions() -> the reconciler's bp is
None).
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.config_loader import OrderReconcilerConfig
from core.events import EventBus
from orders.cnc_gtt import CncGttPlacer
from orders.cnc_gtt_monitor import CncGttMonitor
from orders.order_reconciler import OrderReconciler
from tests.unit.test_cnc_gtt_monitor import _Notifier, _adapter
from tests.unit.test_order_reconciler import _insert_order, _insert_trade, _make_store

_LOG = logging.getLogger("test_cnc_gtt_adoption_fix183")


# ── builders ────────────────────────────────────────────────────────────────────
def _placer(adapter, store):
    return CncGttPlacer(adapter, gtt_sl_limit_offset_pct=0.03, gtt_tgt_limit_offset_pct=0.002,
                        delivery_enabled=True, logger=_LOG, quote_fn=adapter.get_quote,
                        tick_fn=lambda _s: 0.05, store=store)


def _fm():
    fm = MagicMock()
    fm.release_used.return_value = SimpleNamespace(pnl_delta=0.0)
    snap = MagicMock()
    snap.total = 100_000.0
    fm.get_snapshot.return_value = snap
    return fm


def _mon_env(tmp_path: Path):
    """Monitor-level env: call mon.adopt_orphan_gtts() directly."""
    store = _make_store(tmp_path)
    adapter = _adapter()
    placer = _placer(adapter, store)
    notifier = _Notifier()
    mon = CncGttMonitor(store=store, adapter=adapter, placer=placer, fund_manager=_fm(),
                        kill_switch=MagicMock(), notifier=notifier, bus=EventBus(),
                        logger=_LOG, mode="PAPER", market_hours_fn=lambda: True)
    return SimpleNamespace(store=store, adapter=adapter, placer=placer,
                           notifier=notifier, mon=mon)


def _rec_env(tmp_path: Path, *, with_monitor: bool = True):
    """Reconciler-level env: drive rec.reconcile_once() (the prepass + CHECK1 path)."""
    store = _make_store(tmp_path)
    adapter = _adapter()
    notifier = _Notifier()
    fm = _fm()
    mon = None
    if with_monitor:
        mon = CncGttMonitor(store=store, adapter=adapter, placer=_placer(adapter, store),
                            fund_manager=fm, kill_switch=MagicMock(), notifier=notifier,
                            bus=EventBus(), logger=_LOG, mode="PAPER",
                            market_hours_fn=lambda: True)
    cfg = OrderReconcilerConfig(poll_interval_sec=60, capital_drift_tolerance=1e9)
    rec = OrderReconciler(state_store=store, adapter=adapter, fund_manager=fm,
                          kill_switch=MagicMock(), notifier=notifier, bus=EventBus(),
                          logger=_LOG, cfg=cfg, quote_fn=adapter.get_quote,
                          broker_orders_fn=None, mode="PAPER", cnc_gtt_monitor=mon,
                          market_hours_fn=lambda: True)
    return SimpleNamespace(store=store, adapter=adapter, notifier=notifier, fm=fm,
                           mon=mon, rec=rec)


def _seed_delivery(store, trade_id="t1", symbol="RAMCOIND", status="OPEN", qty=10,
                   entry=337.0, sl=320.0):
    """Seed an OPEN delivery trade = trades row + an ENTRY order with product=CNC (so
    get_all_open_trades surfaces product=CNC via its LEFT JOIN)."""
    _insert_trade(store, trade_id, symbol=symbol, status=status, qty_filled=qty,
                  sl_initial=sl, entry_actual_price=entry)
    _insert_order(store, f"ord_{trade_id}", trade_id, leg="ENTRY", product="CNC",
                  status="COMPLETE")


def _rowless_gtt(adapter, symbol="RAMCOIND", qty=10, sl_trig=320.0, sl_lim=310.0,
                 tgt_trig=360.0, tgt_lim=359.0, ltp=337.0):
    """A live broker GTT with NO gtt_state row (the persist-failed / pre-table case)."""
    return adapter.place_gtt(symbol=symbol, exit_side="SELL", qty=qty, sl_trigger=sl_trig,
                             sl_limit=sl_lim, tgt_trigger=tgt_trig, tgt_limit=tgt_lim,
                             last_price=ltp)


# ── 1. HAPPY: reconstruct + correlate + insert ───────────────────────────────────
def test_happy_adopt_reconstructs_and_correlates(tmp_path: Path):
    env = _mon_env(tmp_path)
    _seed_delivery(env.store, "t1", symbol="RAMCOIND", qty=10)
    gid = _rowless_gtt(env.adapter, symbol="RAMCOIND", qty=10,
                       sl_trig=320.0, sl_lim=310.0, tgt_trig=360.0, tgt_lim=359.0)
    assert env.store.get_active_gtt_for_trade("t1") is None       # no mirror yet

    out = env.mon.adopt_orphan_gtts()

    assert out == ["adopted:RAMCOIND"]
    row = env.store.get_active_gtt_for_trade("t1")
    assert row is not None
    assert row["gtt_id"] == int(gid)                              # correlated + stored
    assert row["trade_id"] == "t1" and row["symbol"] == "RAMCOIND"
    assert row["status"] == "ACTIVE" and row["needs_review"] == 0
    assert row["exit_side"] == "SELL" and row["qty"] == 10
    # SL/TGT derived by magnitude (lower trigger = SL, higher = TGT)
    assert row["sl_trigger"] == 320.0 and row["sl_limit"] == 310.0
    assert row["tgt_trigger"] == 360.0 and row["tgt_limit"] == 359.0
    # next pass is a clean no-op (row now exists -> SKIP), no spurious alert
    assert env.mon.adopt_orphan_gtts() == []
    assert env.adapter.get_gtts()                                 # GTT never deleted


# ── 2. *** THE C2.1 REGRESSION TEST ***: adopted BEFORE CHECK1, NOT CLOSED_MANUAL ─
def test_c21_rowless_carried_gtt_adopted_before_check1(tmp_path: Path):
    env = _rec_env(tmp_path, with_monitor=True)
    # delivery trade carried overnight (OPEN + ENTRY CNC), persist-failed row-less GTT
    _seed_delivery(env.store, "t1", symbol="RAMCOIND", qty=10)
    _rowless_gtt(env.adapter, symbol="RAMCOIND", qty=10)
    # carried holding lives in holdings(), NOT positions() -> bp is None at CHECK1
    env.adapter.seed_paper_holding("RAMCOIND", 10)
    assert env.adapter.get_positions() == []
    assert env.store.get_active_gtt_for_trade("t1") is None

    env.rec.reconcile_once()

    tr = env.store.fetch_one("SELECT status, exit_reason FROM trades WHERE trade_id='t1'")
    assert tr["status"] == "OPEN"                                 # NOT mis-CLOSED_MANUAL'd
    assert tr["exit_reason"] is None
    row = env.store.get_active_gtt_for_trade("t1")                # now self-healed
    assert row is not None and row["status"] == "ACTIVE" and row["qty"] == 10


def test_c21_without_adoption_demonstrates_the_gap(tmp_path: Path):
    # SAME setup but NO monitor wired -> CHECK1 mis-closes it (the bug FIX-183 closes).
    env = _rec_env(tmp_path, with_monitor=False)
    _seed_delivery(env.store, "t1", symbol="RAMCOIND", qty=10)
    _rowless_gtt(env.adapter, symbol="RAMCOIND", qty=10)
    env.adapter.seed_paper_holding("RAMCOIND", 10)

    env.rec.reconcile_once()

    tr = env.store.fetch_one("SELECT status FROM trades WHERE trade_id='t1'")
    assert tr["status"] == "CLOSED_MANUAL"                        # the C2.1 mis-close


# ── 3. 0 trades -> no adopt, WARN once, GTT left alone ───────────────────────────
def test_zero_trades_warns_once_and_leaves_gtt(tmp_path: Path):
    env = _mon_env(tmp_path)
    gid = _rowless_gtt(env.adapter, symbol="ZEEL")                # no open trade for ZEEL
    out = env.mon.adopt_orphan_gtts()
    assert out == ["adopt_no_trade:ZEEL"]
    assert env.store.get_gtt_state_by_id(gid) is None            # nothing inserted
    assert str(gid) in env.adapter._paper_gtts                   # NOT deleted
    assert len(env.notifier.sev("WARNING")) == 1
    # idempotent: a second pass does NOT re-alert (once per gtt_id)
    assert env.mon.adopt_orphan_gtts() == ["adopt_no_trade:ZEEL"]
    assert len(env.notifier.sev("WARNING")) == 1


# ── 4. >1 trades -> anomaly, no adopt, WARN ──────────────────────────────────────
def test_more_than_one_trade_not_adopted(tmp_path: Path):
    env = _mon_env(tmp_path)
    # force 2 OPEN CNC trades for one symbol (only reachable manually — bypass risk gate)
    _seed_delivery(env.store, "t1", symbol="RAMCOIND", qty=10)
    _seed_delivery(env.store, "t2", symbol="RAMCOIND", qty=5)
    gid = _rowless_gtt(env.adapter, symbol="RAMCOIND", qty=10)
    out = env.mon.adopt_orphan_gtts()
    assert out == ["adopt_ambiguous:RAMCOIND"]
    assert env.store.get_gtt_state_by_id(gid) is None
    assert env.store.get_active_gtt_for_trade("t1") is None
    assert env.store.get_active_gtt_for_trade("t2") is None
    assert env.notifier.sev("WARNING")


# ── 5. trade already has an ACTIVE row -> no adopt (M2 protected) ─────────────────
def test_trade_already_active_row_not_adopted(tmp_path: Path):
    env = _mon_env(tmp_path)
    _seed_delivery(env.store, "t1", symbol="RAMCOIND", qty=10)
    # placer places GTT_A AND writes its ACTIVE gtt_state row
    res = env.placer.place_for_fill(symbol="RAMCOIND", exit_side="SELL", qty=10,
                                    sl_price=320.0, tgt_price=360.0, trade_id="t1")
    # a SECOND, row-less GTT_B appears for the same symbol/trade
    gid_b = _rowless_gtt(env.adapter, symbol="RAMCOIND", qty=10)
    out = env.mon.adopt_orphan_gtts()
    assert out == ["adopt_already_active:RAMCOIND"]
    assert env.store.get_gtt_state_by_id(gid_b) is None          # GTT_B NOT adopted
    # t1 still has exactly ONE ACTIVE row (GTT_A) — no M2 violation introduced
    active = env.store.fetch_all(
        "SELECT gtt_id FROM gtt_state WHERE trade_id='t1' AND status='ACTIVE'")
    assert len(active) == 1 and active[0]["gtt_id"] == int(res.gtt_id)
    assert env.notifier.sev("WARNING")


# ── 6. non-active broker GTT (triggered/cancelled) -> skipped, no adopt/alert ─────
def test_non_active_gtt_not_adopted(tmp_path: Path):
    env = _mon_env(tmp_path)
    _seed_delivery(env.store, "t1", symbol="RAMCOIND", qty=10)
    gid = _rowless_gtt(env.adapter, symbol="RAMCOIND", qty=10)
    env.adapter._paper_gtts[str(gid)]["status"] = "triggered"    # fired, no longer live
    out = env.mon.adopt_orphan_gtts()
    assert out == []                                             # skipped entirely
    assert env.store.get_gtt_state_by_id(gid) is None
    assert not env.notifier.sent


# ── 7. broker unavailable -> defer, no crash ─────────────────────────────────────
def test_broker_unavailable_defers(tmp_path: Path):
    env = _mon_env(tmp_path)
    _seed_delivery(env.store, "t1", symbol="RAMCOIND", qty=10)

    def _boom():
        raise RuntimeError("kite down")

    env.adapter.get_gtts = _boom
    out = env.mon.adopt_orphan_gtts()
    assert out == ["adopt_deferred:broker_unavailable"]


# ── 8. *** MIS REGRESSION ***: prepass is a no-op for an intraday trade ──────────
def test_mis_trade_reconcile_unaffected_by_prepass(tmp_path: Path):
    env = _rec_env(tmp_path, with_monitor=True)
    # an OPEN MIS (intraday) trade, broker flat, NO GTTs at the broker
    _insert_trade(env.store, "m1", symbol="RELIANCE", status="OPEN", qty_filled=10)
    _insert_order(env.store, "ord_m1", "m1", leg="ENTRY", product="MIS", status="COMPLETE")
    assert env.adapter.get_gtts() == []                          # no GTTs -> prepass no-op

    env.rec.reconcile_once()

    # CHECK1 behaves EXACTLY as before (MIS, broker-flat -> CLOSED_MANUAL)
    tr = env.store.fetch_one("SELECT status FROM trades WHERE trade_id='m1'")
    assert tr["status"] == "CLOSED_MANUAL"
    # the prepass created NO gtt_state row (it never touches intraday)
    assert env.store.get_active_gtt_states() == []


# ── 9. magnitude-based SL/TGT derivation is robust to leg/trigger ORDER ───────────
def test_reconstruct_splits_sl_tgt_by_magnitude_not_index(tmp_path: Path):
    adapter = _adapter()
    # trigger_values DESCENDING ([tgt, sl]) with parallel legs ([tgt_leg, sl_leg]) —
    # index-0 is the TGT here; correct derivation must use magnitude, not position.
    legs = [
        {"transaction_type": "SELL", "quantity": 7, "order_type": "LIMIT",
         "product": "CNC", "price": 359.0},   # TGT leg (higher trigger)
        {"transaction_type": "SELL", "quantity": 7, "order_type": "LIMIT",
         "product": "CNC", "price": 310.0},   # SL leg (lower trigger)
    ]
    g = adapter._paper_gtt_record("777", "RAMCOIND", [360.0, 320.0], 337.0, legs,
                                  status="active")
    recon = CncGttMonitor._reconstruct_from_broker_gtt(g)
    assert recon is not None
    assert recon["sl_trigger"] == 320.0 and recon["sl_limit"] == 310.0
    assert recon["tgt_trigger"] == 360.0 and recon["tgt_limit"] == 359.0
    assert recon["exit_side"] == "SELL" and recon["qty"] == 7


def test_reconstruct_unreadable_returns_none(tmp_path: Path):
    adapter = _adapter()
    g = adapter._paper_gtt_record("888", "INFY", [100.0, 110.0], 105.0, [], status="active")
    assert CncGttMonitor._reconstruct_from_broker_gtt(g) is None   # empty legs -> None


def test_unreadable_active_gtt_warns_once_no_insert(tmp_path: Path):
    env = _mon_env(tmp_path)
    env.adapter._paper_gtts["888"] = env.adapter._paper_gtt_record(
        "888", "INFY", [100.0, 110.0], 105.0, [], status="active")
    out = env.mon.adopt_orphan_gtts()
    assert out == ["adopt_unreadable:888"]
    assert env.store.get_gtt_state_by_id("888") is None
    assert len(env.notifier.sev("WARNING")) == 1
    # adoption alerts carry the dedicated source_module
    assert any(s[2] == "cnc_gtt_adoption" for s in env.notifier.sent)
