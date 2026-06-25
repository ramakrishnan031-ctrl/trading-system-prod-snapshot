"""
tests/unit/test_ramcoind_dup_exit_fix.py — RAMCOIND duplicate-exit fix (25-Jun-2026).

LAYER 2 (keystone): the reconciler one-live-SL / one-live-TGT invariant
(_check_duplicate_exits / _dedupe_exit_leg). Cancels a duplicate exit leg (e.g. the
G5b/LIMIT_TRIPLE placement race) keeping the canonical (earliest-placed) leg, never
dropping to zero protection, scoped so CO trades are never touched.

Real StateStore + a mock adapter (only .cancel_order/.place_order are read), reusing
the established reconciler-test harness.
"""
from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.time_authority import now_ist
from tests.unit.test_order_reconciler import _make_reconciler, _make_store, _cancel_res

_NOW = "2026-06-25T10:00:00+05:30"


def _trade(store, trade_id, *, symbol="RAMCOIND", direction="LONG", status="OPEN",
           qty_filled=1, sl_initial=334.38, entry=337.75, order_protocol="LIMIT_TRIPLE",
           entry_time=None, exit_price=None, exit_time=None):
    sig = f"sig_{trade_id}"
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,"
            "received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sig, symbol, "SC", "strat", _NOW, _NOW, _NOW, "TRADED",
             f"fp_{trade_id}", "2026-06-25"),
        )
        cur.execute(
            "INSERT INTO trades (trade_id,signal_id,symbol,direction,strategy,sector,"
            "qty_planned,qty_filled,entry_target_price,sl_initial,tgt_initial,"
            "margin_reserved,risk_amount,created_at,status,order_protocol,updated_at,"
            "entry_actual_price,entry_time,exit_price,exit_time) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, sig, symbol, direction, "strat", "X", qty_filled, qty_filled,
             entry, sl_initial, entry * 1.015, 100.0, 5.0, _NOW, status,
             order_protocol, _NOW, entry, entry_time, exit_price, exit_time),
        )


def _bp(symbol="RAMCOIND", qty=-1, avg=334.0):
    """A broker position snapshot object (only .symbol/.qty/.avg_price are read)."""
    return SimpleNamespace(symbol=symbol, qty=qty, avg_price=avg)


def _order(store, order_id, trade_id, *, leg, status="OPEN", variety="regular",
           placed_at, order_type="SL", txn="SELL", trigger_price=0.0, price=0.0):
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO orders (order_id,trade_id,leg,transaction_type,order_type,"
            "product,variety,qty_requested,status,trigger_price,price,placed_at,"
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, trade_id, leg, txn, order_type, "MIS", variety, 1, status,
             trigger_price, price, placed_at, placed_at),
        )


def _status(store, order_id):
    r = store.fetch_one("SELECT status FROM orders WHERE order_id=?", (order_id,))
    return r["status"] if r else "<missing>"


def _adapter(cancel_success=True, cancel_reason=""):
    a = SimpleNamespace()
    a.cancel_order = lambda oid: _cancel_res(cancel_success, cancel_reason)
    a.get_positions = lambda: []
    return a


# ── RAMCOIND shape: duplicate SL, keep canonical (earliest), cancel the later ──

def test_duplicate_sl_cancels_later_keeps_canonical(tmp_path: Path):
    store = _make_store(tmp_path)
    _trade(store, "t1")
    # canonical LIMIT_TRIPLE SL placed first; G5b duplicate placed ~1.3s later
    _order(store, "sl_canon", "t1", leg="SL", placed_at="2026-06-25T10:00:24.976+05:30",
           trigger_price=334.38)
    _order(store, "sl_dup", "t1", leg="SL", placed_at="2026-06-25T10:00:26.311+05:30",
           trigger_price=334.38)
    _order(store, "tgt1", "t1", leg="TGT", order_type="LIMIT", placed_at=_NOW, price=342.8)

    sent = []
    notifier = SimpleNamespace(send=lambda **k: sent.append(k))

    rec = _make_reconciler(store, adapter=_adapter(), notifier=notifier)
    actions = rec._check_duplicate_exits(store.get_all_open_trades())

    assert _status(store, "sl_dup") == "CANCELLED"      # later duplicate cancelled
    assert _status(store, "sl_canon") == "OPEN"          # canonical kept
    assert _status(store, "tgt1") == "OPEN"              # single TGT untouched
    dup = [a for a in actions if a.check_name == "DUPLICATE_SL"]
    assert dup and dup[0].success and "sl_dup" in dup[0].action_taken
    assert sent and sent[0]["severity"] == "WARNING"
    store.close()


def test_duplicate_tgt_cancels_later(tmp_path: Path):
    store = _make_store(tmp_path)
    _trade(store, "t1")
    _order(store, "sl1", "t1", leg="SL", placed_at=_NOW, trigger_price=334.38)
    _order(store, "tgt_canon", "t1", leg="TGT", order_type="LIMIT",
           placed_at="2026-06-25T10:00:25+05:30", price=342.8)
    _order(store, "tgt_dup", "t1", leg="TGT", order_type="LIMIT",
           placed_at="2026-06-25T10:05:00+05:30", price=342.8)

    rec = _make_reconciler(store, adapter=_adapter())
    actions = rec._check_duplicate_exits(store.get_all_open_trades())

    assert _status(store, "tgt_dup") == "CANCELLED"
    assert _status(store, "tgt_canon") == "OPEN"
    assert any(a.check_name == "DUPLICATE_TGT" for a in actions)
    store.close()


# ── N1: CO trades have no local SL leg — SL invariant must skip them ──

def test_co_trade_sl_not_deduped(tmp_path: Path):
    store = _make_store(tmp_path)
    _trade(store, "co1", order_protocol="CO_PLUS_TGT")
    # the CO entry IS the SL bracket (variety='co'); plus a standalone TGT.
    _order(store, "co_entry", "co1", leg="ENTRY", variety="co", placed_at=_NOW,
           order_type="SL", txn="BUY")
    _order(store, "co_tgt", "co1", leg="TGT", order_type="LIMIT", placed_at=_NOW, price=345)
    # Even if two stray SL legs existed on a CO trade, the SL invariant must NOT touch
    # them (N1) — detected via the CO entry's variety='co'.
    _order(store, "co_sl_a", "co1", leg="SL", placed_at="2026-06-25T10:01:00+05:30")
    _order(store, "co_sl_b", "co1", leg="SL", placed_at="2026-06-25T10:02:00+05:30")

    rec = _make_reconciler(store, adapter=_adapter())
    actions = rec._check_duplicate_exits(store.get_all_open_trades())

    assert _status(store, "co_sl_a") == "OPEN"   # CO SL legs untouched (N1)
    assert _status(store, "co_sl_b") == "OPEN"
    assert not any(a.check_name == "DUPLICATE_SL" for a in actions)
    store.close()


def test_single_sl_single_tgt_no_action(tmp_path: Path):
    store = _make_store(tmp_path)
    _trade(store, "t1")
    _order(store, "sl1", "t1", leg="SL", placed_at=_NOW, trigger_price=334.38)
    _order(store, "tgt1", "t1", leg="TGT", order_type="LIMIT", placed_at=_NOW, price=342.8)

    called = []
    adapter = _adapter()
    adapter.cancel_order = lambda oid: called.append(oid) or _cancel_res(True)
    rec = _make_reconciler(store, adapter=adapter)
    actions = rec._check_duplicate_exits(store.get_all_open_trades())

    assert called == []                       # nothing cancelled
    assert actions == []                      # no action
    assert _status(store, "sl1") == "OPEN"
    store.close()


def test_terminal_duplicate_not_counted(tmp_path: Path):
    """A CANCELLED/COMPLETE second SL is not 'live' → not a duplicate."""
    store = _make_store(tmp_path)
    _trade(store, "t1")
    _order(store, "sl1", "t1", leg="SL", placed_at=_NOW, trigger_price=334.38)
    _order(store, "sl_done", "t1", leg="SL", status="CANCELLED",
           placed_at="2026-06-25T10:01:00+05:30")

    called = []
    adapter = _adapter()
    adapter.cancel_order = lambda oid: called.append(oid) or _cancel_res(True)
    rec = _make_reconciler(store, adapter=adapter)
    actions = rec._check_duplicate_exits(store.get_all_open_trades())

    assert called == [] and actions == []
    store.close()


def test_trailed_canonical_sl_kept(tmp_path: Path):
    """N3: the earliest-placed SL (the OCO/trailed leg) is kept even though its
    trigger differs from the duplicate's (a trail advanced it)."""
    store = _make_store(tmp_path)
    _trade(store, "t1")
    _order(store, "sl_trailed", "t1", leg="SL", placed_at="2026-06-25T10:00:24+05:30",
           trigger_price=336.10)   # canonical, trail-advanced trigger
    _order(store, "sl_dup", "t1", leg="SL", placed_at="2026-06-25T10:00:26+05:30",
           trigger_price=334.38)   # G5b duplicate at the original SL
    rec = _make_reconciler(store, adapter=_adapter())
    rec._check_duplicate_exits(store.get_all_open_trades())
    assert _status(store, "sl_trailed") == "OPEN"     # canonical (earliest) kept
    assert _status(store, "sl_dup") == "CANCELLED"
    store.close()


def test_parity_paper_and_live(tmp_path: Path):
    """No mode branch → PAPER and LIVE dedupe identically."""
    results = {}
    for mode in ("PAPER", "LIVE"):
        store = _make_store(tmp_path / mode)
        _trade(store, "t1")
        _order(store, "sl_canon", "t1", leg="SL", placed_at="2026-06-25T10:00:24+05:30")
        _order(store, "sl_dup", "t1", leg="SL", placed_at="2026-06-25T10:00:26+05:30")
        rec = _make_reconciler(store, adapter=_adapter())
        rec._mode = mode
        rec._check_duplicate_exits(store.get_all_open_trades())
        results[mode] = (_status(store, "sl_canon"), _status(store, "sl_dup"))
        store.close()
    assert results["PAPER"] == results["LIVE"] == ("OPEN", "CANCELLED")


def test_cancel_safety_never_naked_replaces_sl(tmp_path: Path):
    """Cancel-safety (N5): if the canonical SL races to terminal during the cancel
    pass (zero live SL remain on an OPEN trade) → re-place a protective SL + CRITICAL."""
    store = _make_store(tmp_path)
    _trade(store, "t1")
    _order(store, "sl_canon", "t1", leg="SL", placed_at="2026-06-25T10:00:24+05:30",
           trigger_price=334.38)
    _order(store, "sl_dup", "t1", leg="SL", placed_at="2026-06-25T10:00:26+05:30",
           trigger_price=334.38)

    # adapter.cancel_order: cancelling the duplicate ALSO simulates the canonical
    # concurrently filling (race) → zero live SL remain at the re-verify.
    def _cancel(oid):
        if oid == "sl_dup":
            with store.transaction() as cur:
                cur.execute("UPDATE orders SET status='COMPLETE' WHERE order_id='sl_canon'")
        return _cancel_res(True)

    placed = []
    adapter = _adapter()
    adapter.cancel_order = _cancel
    adapter.place_order = lambda **k: placed.append(k) or SimpleNamespace(
        broker_order_id="sl_replace", product="MIS", variety="regular")
    quote = {"RAMCOIND": SimpleNamespace(last_price=337.0)}
    rec = _make_reconciler(store, adapter=adapter, quote_fn=lambda s: quote)

    notes = []
    rec._notifier = SimpleNamespace(send=lambda **k: notes.append(k))
    actions = rec._check_duplicate_exits(store.get_all_open_trades())

    assert placed, "a protective SL must be re-placed when the trade would be naked"
    dup = [a for a in actions if a.check_name == "DUPLICATE_SL"]
    assert dup and dup[0].tier == "CRITICAL"
    assert notes and notes[-1]["severity"] == "CRITICAL"
    store.close()


# ── LAYER 1: G5b authoritative guard + settling window ────────────────────────

def _g5b_adapter(placed):
    a = SimpleNamespace()
    a.place_order = lambda **k: (placed.append(k), SimpleNamespace(
        broker_order_id="new_sl", product="MIS", variety="regular"))[1]
    a.cancel_order = lambda oid: _cancel_res(True)
    a.get_positions = lambda: []
    return a


def _broker_sl(symbol="RAMCOIND", side="SELL", trigger=334.38):
    """A broker open-order book showing a live SL (exit-side, trigger>0)."""
    return lambda: [{"order_id": "sl_live", "symbol": symbol, "status": "TRIGGER PENDING",
                     "transaction_type": side, "quantity": 1, "price": 332.7,
                     "trigger_price": trigger}]


def _quote(symbol="RAMCOIND", ltp=337.0):
    return lambda syms: {symbol: SimpleNamespace(last_price=ltp)}


def test_g5b_skips_when_broker_has_live_sl(tmp_path: Path):
    """The RAMCOIND race: no LOCAL SL row yet, but the broker already shows the
    just-placed SL → G5b must SKIP (authoritative check), placing no duplicate."""
    store = _make_store(tmp_path)
    old = (now_ist() - timedelta(minutes=30)).isoformat()   # past the settling window
    _trade(store, "t1", entry_time=old)
    placed = []
    rec = _make_reconciler(store, adapter=_g5b_adapter(placed),
                           quote_fn=_quote(), broker_orders_fn=_broker_sl())
    trade = store.get_all_open_trades()[0]
    act = rec._g5b_crash_recovery_sl(trade, broker_positions=None)
    assert act is None and placed == []     # skipped — no duplicate SL placed
    store.close()


def test_g5b_settling_window_skips_fresh_fill(tmp_path: Path):
    """Entry filled 3s ago → within the 10s settling window → G5b skips (exits are
    still being placed by the normal path)."""
    store = _make_store(tmp_path)
    fresh = (now_ist() - timedelta(seconds=3)).isoformat()
    _trade(store, "t1", entry_time=fresh)
    placed = []
    rec = _make_reconciler(store, adapter=_g5b_adapter(placed),
                           quote_fn=_quote(), broker_orders_fn=lambda: [])  # no SL at broker
    trade = store.get_all_open_trades()[0]
    act = rec._g5b_crash_recovery_sl(trade, broker_positions=None)
    assert act is None and placed == []
    store.close()


def test_g5b_recovers_genuinely_unprotected_old_fill(tmp_path: Path):
    """A real crash-recovery: fill is 30 min old, no SL anywhere → G5b PLACES the
    protective SL (the settling window must not block legitimate recovery)."""
    store = _make_store(tmp_path)
    old = (now_ist() - timedelta(minutes=30)).isoformat()
    _trade(store, "t1", entry_time=old)
    placed = []
    rec = _make_reconciler(store, adapter=_g5b_adapter(placed),
                           quote_fn=_quote(ltp=337.0), broker_orders_fn=lambda: [])
    trade = store.get_all_open_trades()[0]
    act = rec._g5b_crash_recovery_sl(trade, broker_positions=None)
    assert act is not None and act.success and placed, "old unprotected fill must recover"
    store.close()


def test_g5b_fallback_fill_map_skips(tmp_path: Path):
    """broker_orders_fn unwired (None) → fall back to order_placer._fill_map; a live
    SL leg there → skip."""
    store = _make_store(tmp_path)
    old = (now_ist() - timedelta(minutes=30)).isoformat()
    _trade(store, "t1", entry_time=old)
    placed = []
    rec = _make_reconciler(store, adapter=_g5b_adapter(placed),
                           quote_fn=_quote(), broker_orders_fn=None)
    rec._order_placer = SimpleNamespace(
        _fill_map={"x": SimpleNamespace(trade_id="t1", leg="SL")},
        _fill_map_lock=threading.Lock(),
    )
    trade = store.get_all_open_trades()[0]
    act = rec._g5b_crash_recovery_sl(trade, broker_positions=None)
    assert act is None and placed == []
    store.close()


def test_g5b_race_parity_paper_and_live(tmp_path: Path):
    """The authoritative skip is identical in PAPER and LIVE (no mode branch)."""
    out = {}
    for mode in ("PAPER", "LIVE"):
        store = _make_store(tmp_path / mode)
        old = (now_ist() - timedelta(minutes=30)).isoformat()
        _trade(store, "t1", entry_time=old)
        placed = []
        rec = _make_reconciler(store, adapter=_g5b_adapter(placed),
                               quote_fn=_quote(), broker_orders_fn=_broker_sl())
        rec._mode = mode
        act = rec._g5b_crash_recovery_sl(store.get_all_open_trades()[0], broker_positions=None)
        out[mode] = (act, placed)
        store.close()
    assert out["PAPER"] == out["LIVE"] == (None, [])


# ── LAYER 3: CHECK2 system-oversell recognition + naked safety-net ────────────

def test_system_oversell_detected_and_flattened(tmp_path: Path):
    """The RAMCOIND residual shape: a LONG trade closed ~1 min ago at 334, broker
    shows an untracked -1 @ 334 → recognised as a SYSTEM over-sell → covered + CRITICAL."""
    store = _make_store(tmp_path)
    recent = (now_ist() - timedelta(seconds=60)).isoformat()
    _trade(store, "t1", status="CLOSED", direction="LONG", qty_filled=1,
           exit_price=334.0, exit_time=recent)
    placed, notes = [], []
    rec = _make_reconciler(store, adapter=_g5b_adapter(placed))
    rec._notifier = SimpleNamespace(send=lambda **k: notes.append(k))
    act = rec._check2_orphan_adoption("RAMCOIND", _bp("RAMCOIND", -1, 334.0))
    assert act.check_name == "SYSTEM_OVERSELL" and act.tier == "CRITICAL"
    assert placed and placed[0]["side"] == "BUY" and placed[0]["qty"] == 1
    assert placed[0]["order_type"] == "MARKET"
    assert notes and notes[-1]["severity"] == "CRITICAL"
    store.close()


def test_oversell_not_double_flattened(tmp_path: Path):
    store = _make_store(tmp_path)
    recent = (now_ist() - timedelta(seconds=60)).isoformat()
    _trade(store, "t1", status="CLOSED", direction="LONG", qty_filled=1,
           exit_price=334.0, exit_time=recent)
    placed = []
    rec = _make_reconciler(store, adapter=_g5b_adapter(placed))
    rec._notifier = SimpleNamespace(send=lambda **k: None)
    bp = _bp("RAMCOIND", -1, 334.0)
    rec._check2_orphan_adoption("RAMCOIND", bp)        # flattens once
    act2 = rec._check2_orphan_adoption("RAMCOIND", bp)  # must NOT cover again
    assert len(placed) == 1, "the residual must be covered only once"
    assert act2.check_name != "SYSTEM_OVERSELL"
    store.close()


def test_ambiguous_orphan_is_human_with_warning(tmp_path: Path):
    """No recent matching closed trade → treat as a human order (not auto-managed),
    but a NAKED untracked position (no protective stop at the broker) is surfaced with
    a WARNING — never a silent INFO (3b)."""
    store = _make_store(tmp_path)
    placed, notes = [], []
    rec = _make_reconciler(store, adapter=_g5b_adapter(placed),
                           broker_orders_fn=lambda: [])  # no protective stop → naked
    rec._notifier = SimpleNamespace(send=lambda **k: notes.append(k))
    act = rec._check2_orphan_adoption("IDEA", _bp("IDEA", -5, 12.0))
    assert act.check_name == "ORPHAN_ADOPTION"
    assert placed == []                                  # human order — not auto-managed
    assert notes and notes[-1]["severity"] == "WARNING"  # but never silent (naked)
    store.close()


def test_protected_human_position_stays_silent(tmp_path: Path):
    """A human position WITH its own protective stop at the broker → NOT naked →
    silent (FIX-182 unchanged)."""
    store = _make_store(tmp_path)
    placed, notes = [], []
    protective = [{"order_id": "h_sl", "symbol": "IDEA", "transaction_type": "BUY",
                   "trigger_price": 13.0, "status": "TRIGGER PENDING"}]
    rec = _make_reconciler(store, adapter=_g5b_adapter(placed),
                           broker_orders_fn=lambda: protective)
    rec._notifier = SimpleNamespace(send=lambda **k: notes.append(k))
    rec._check2_orphan_adoption("IDEA", _bp("IDEA", -5, 12.0))
    assert notes == []                                   # protected human order → silent
    store.close()


def test_oversell_wrong_side_not_flattened(tmp_path: Path):
    """A residual on the SAME side as the closed trade is not an over-sell."""
    store = _make_store(tmp_path)
    recent = (now_ist() - timedelta(seconds=60)).isoformat()
    _trade(store, "t1", status="CLOSED", direction="LONG", qty_filled=1,
           exit_price=334.0, exit_time=recent)
    placed = []
    rec = _make_reconciler(store, adapter=_g5b_adapter(placed))
    rec._notifier = SimpleNamespace(send=lambda **k: None)
    act = rec._check2_orphan_adoption("RAMCOIND", _bp("RAMCOIND", +1, 334.0))
    assert act.check_name == "ORPHAN_ADOPTION" and placed == []
    store.close()


# ── LAYER 4: after-check duplicate flag (placement-time, same-path guard) ──────

def _seed_sl(om, trade_id, oid, *, trigger=99.0, price=98.5, qty=10):
    om.insert_order(trade_id=trade_id, broker_order_id=oid, leg="SL",
                    transaction_type="SELL", order_type="SL", product="MIS",
                    variety="regular", qty_requested=qty, price=price, trigger_price=trigger)


def test_aftercheck_flags_duplicate_sl(tmp_path: Path):
    from tests.unit.test_slice1_rr_aftercheck import _make_placer, _make_trade, _seed_exit
    placer, store, om, _ = _make_placer(tmp_path)
    trade_id = _make_trade(placer, store)
    _seed_sl(om, trade_id, "SL_A")
    _seed_sl(om, trade_id, "SL_B")                      # a same-path duplicate SL
    _seed_exit(om, trade_id, "TGT", price=101.5, qty=10)
    placer._verify_exits_placed(trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                                intended_sl=99.0, intended_tgt=101.5, qty_filled=10)
    row = store.fetch_one(
        "SELECT exits_verified, exits_verify_detail FROM trades WHERE trade_id=?", (trade_id,))
    assert row["exits_verified"] == 0 and "DUPLICATE_SL" in row["exits_verify_detail"]
    store.close()


def test_aftercheck_flags_duplicate_tgt(tmp_path: Path):
    from tests.unit.test_slice1_rr_aftercheck import _make_placer, _make_trade, _seed_exit
    placer, store, om, _ = _make_placer(tmp_path)
    trade_id = _make_trade(placer, store)
    _seed_sl(om, trade_id, "SL_A")
    _seed_exit(om, trade_id, "TGT", price=101.5, qty=10)
    om.insert_order(trade_id=trade_id, broker_order_id="TGT_B", leg="TGT",
                    transaction_type="SELL", order_type="LIMIT", product="MIS",
                    variety="regular", qty_requested=10, price=101.5, trigger_price=0.0)
    placer._verify_exits_placed(trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                                intended_sl=99.0, intended_tgt=101.5, qty_filled=10)
    row = store.fetch_one(
        "SELECT exits_verified, exits_verify_detail FROM trades WHERE trade_id=?", (trade_id,))
    assert row["exits_verified"] == 0 and "DUPLICATE_TGT" in row["exits_verify_detail"]
    store.close()


def test_aftercheck_single_legs_ok(tmp_path: Path):
    from tests.unit.test_slice1_rr_aftercheck import _make_placer, _make_trade, _seed_exit
    placer, store, om, _ = _make_placer(tmp_path)
    trade_id = _make_trade(placer, store)
    _seed_sl(om, trade_id, "SL_A")
    _seed_exit(om, trade_id, "TGT", price=101.5, qty=10)
    placer._verify_exits_placed(trade_id=trade_id, symbol="SYM", protocol="LIMIT_TRIPLE",
                                intended_sl=99.0, intended_tgt=101.5, qty_filled=10)
    row = store.fetch_one(
        "SELECT exits_verified, exits_verify_detail FROM trades WHERE trade_id=?", (trade_id,))
    assert row["exits_verified"] == 1 and "DUPLICATE" not in (row["exits_verify_detail"] or "")
    store.close()


# ── SHORT-SIDE SYMMETRY (mirror of every layer) ───────────────────────────────

def test_short_duplicate_sl_deduped(tmp_path: Path):
    """L2 mirror: a SHORT trade with two live BUY-stop SLs → dedupe cancels the later,
    keeps the canonical (the invariant filters on leg='SL', not direction)."""
    store = _make_store(tmp_path)
    _trade(store, "s1", symbol="ABFRL", direction="SHORT", qty_filled=1,
           sl_initial=102.0, entry=100.0)
    _order(store, "sl_canon", "s1", leg="SL", txn="BUY",
           placed_at="2026-06-25T11:00:24+05:30", trigger_price=102.0)
    _order(store, "sl_dup", "s1", leg="SL", txn="BUY",
           placed_at="2026-06-25T11:00:26+05:30", trigger_price=102.0)
    rec = _make_reconciler(store, adapter=_adapter())
    rec._check_duplicate_exits(store.get_all_open_trades())
    assert _status(store, "sl_dup") == "CANCELLED"
    assert _status(store, "sl_canon") == "OPEN"
    store.close()


def test_short_g5b_skips_when_broker_has_live_sl(tmp_path: Path):
    """L1 mirror: a SHORT trade's SL is a BUY stop; G5b's authoritative check looks for
    a live BUY SL at the broker and skips → no duplicate."""
    store = _make_store(tmp_path)
    old = (now_ist() - timedelta(minutes=30)).isoformat()
    _trade(store, "s1", symbol="ABFRL", direction="SHORT", qty_filled=1,
           sl_initial=102.0, entry=100.0, entry_time=old)
    placed = []
    broker_sl = lambda: [{"order_id": "sl", "symbol": "ABFRL", "status": "TRIGGER PENDING",
                          "transaction_type": "BUY", "quantity": 1, "price": 102.5,
                          "trigger_price": 102.0}]
    rec = _make_reconciler(store, adapter=_g5b_adapter(placed),
                           quote_fn=_quote("ABFRL", ltp=100.5), broker_orders_fn=broker_sl)
    act = rec._g5b_crash_recovery_sl(store.get_all_open_trades()[0], broker_positions=None)
    assert act is None and placed == []        # SHORT race also skips — no duplicate BUY SL
    store.close()


def test_system_overbuy_on_short_flattened(tmp_path: Path):
    """L3 mirror: a SHORT closed ~1 min ago at 102; broker shows an untracked +1 @ 102
    (a duplicate BUY SL filled → an over-BUY) → SYSTEM_OVERSELL → covered with a SELL."""
    store = _make_store(tmp_path)
    recent = (now_ist() - timedelta(seconds=60)).isoformat()
    _trade(store, "s1", symbol="ABFRL", status="CLOSED", direction="SHORT", qty_filled=1,
           exit_price=102.0, exit_time=recent, entry=100.0)
    placed, notes = [], []
    rec = _make_reconciler(store, adapter=_g5b_adapter(placed))
    rec._notifier = SimpleNamespace(send=lambda **k: notes.append(k))
    act = rec._check2_orphan_adoption("ABFRL", _bp("ABFRL", +1, 102.0))   # +1 over-buy residual
    assert act.check_name == "SYSTEM_OVERSELL" and act.tier == "CRITICAL"
    assert placed and placed[0]["side"] == "SELL" and placed[0]["qty"] == 1
    assert notes and notes[-1]["severity"] == "CRITICAL"
    store.close()
