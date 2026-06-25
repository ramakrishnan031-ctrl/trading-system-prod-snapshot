"""
tests/unit/test_cnc_gtt_slice25_p2.py — SLICE2.5-P2 durable gtt_state.

STEP 2 coverage: placement writes an ACTIVE gtt_state row; a later partial fill
MODIFIES the same row (one ACTIVE per trade); the hot cache hydrates from
gtt_state on boot so a restart modifies (never duplicates) a surviving GTT;
persistence is best-effort (broker GTT is the authority). Real StateStore (v36)
+ a mock adapter, reusing the reconciler test harness.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from orders.cnc_gtt import CncGttPlacer
from tests.unit.test_order_reconciler import _make_store

_LOG = logging.getLogger("test_cnc_gtt_p2")
_NOW = "2026-06-25T10:00:00+05:30"


def _seed_trade(store, trade_id, *, symbol="RAMCOIND", status="OPEN"):
    """Minimal signal + trade so the gtt_state FK (-> trades) is satisfiable."""
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
            "INSERT INTO trades (trade_id,signal_id,symbol,direction,strategy,qty_planned,"
            "qty_filled,entry_target_price,sl_initial,tgt_initial,margin_reserved,"
            "risk_amount,created_at,status,order_protocol,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_id, sig, symbol, "LONG", "strat", 1, 1, 337.0, 334.0, 343.0,
             100.0, 5.0, _NOW, status, "LIMIT_TRIPLE", _NOW),
        )


class _Adapter:
    """Records GTT calls; mints NUMERIC gtt ids (gtt_state.gtt_id is INTEGER PK)."""

    def __init__(self):
        self.calls = []
        self._seq = 0

    def get_quote(self, syms):
        return {s: SimpleNamespace(last_price=337.0) for s in syms}

    def place_gtt(self, **kw):
        self._seq += 1
        gid = str(7_000_000 + self._seq)
        self.calls.append(("place", gid, kw["qty"]))
        return gid

    def modify_gtt(self, *, gtt_id, **kw):
        self.calls.append(("modify", str(gtt_id), kw["qty"]))
        return str(gtt_id)


def _placer(store, adapter=None):
    a = adapter or _Adapter()
    p = CncGttPlacer(
        a, gtt_sl_limit_offset_pct=0.03, gtt_tgt_limit_offset_pct=0.002,
        delivery_enabled=True, logger=_LOG,
        quote_fn=a.get_quote, tick_fn=lambda _s: 0.05, store=store,
    )
    return p, a


def _place(p, trade_id, qty):
    return p.place_for_fill(symbol="RAMCOIND", exit_side="SELL", qty=qty,
                            sl_price=334.0, tgt_price=343.0, trade_id=trade_id)


def test_placement_writes_active_gtt_state_row(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_trade(store, "t1")
    p, _a = _placer(store)
    res = _place(p, "t1", 1)
    rows = store.fetch_all("SELECT * FROM gtt_state")
    assert len(rows) == 1
    r = rows[0]
    assert r["trade_id"] == "t1" and r["status"] == "ACTIVE" and r["needs_review"] == 0
    assert str(r["gtt_id"]) == str(res.gtt_id)
    assert r["qty"] == 1 and r["exit_side"] == "SELL"
    assert r["sl_trigger"] == res.sl_trigger and r["tgt_trigger"] == res.tgt_trigger
    assert r["sl_limit"] is not None and r["tgt_limit"] is not None
    assert r["created_at"] and r["updated_at"]


def test_second_fill_modifies_same_row_one_active(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_trade(store, "t1")
    p, _a = _placer(store)
    r1 = _place(p, "t1", 1)
    r2 = _place(p, "t1", 3)            # later partial fill grew the qty
    assert r2.modified is True and r2.gtt_id == r1.gtt_id
    rows = store.fetch_all("SELECT * FROM gtt_state")
    assert len(rows) == 1              # updated, not duplicated
    assert rows[0]["qty"] == 3         # qty grew
    assert len(store.get_active_gtt_states()) == 1


def test_hydrate_rebuilds_cache_so_restart_modifies(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_trade(store, "t1")
    p1, _a1 = _placer(store)
    r1 = _place(p1, "t1", 1)
    # simulate restart: fresh placer (empty cache), same durable store
    p2, a2 = _placer(store)
    assert p2.gtt_for("t1") is None
    assert p2.hydrate_from_store() == 1
    assert p2.gtt_for("t1") == str(r1.gtt_id)
    r2 = _place(p2, "t1", 2)
    assert r2.modified is True                       # modified, not a new GTT
    assert a2.calls and a2.calls[0][0] == "modify"   # adapter saw modify, not place
    assert len(store.fetch_all("SELECT * FROM gtt_state")) == 1  # no duplicate row


def test_persist_is_best_effort_on_store_error(tmp_path: Path):
    store = _make_store(tmp_path)
    _seed_trade(store, "t1")

    class _BadStore:
        def __getattr__(self, name):           # delegate everything else to the real store
            return getattr(store, name)

        def insert_gtt_state(self, **kw):       # ... but the write blows up
            raise RuntimeError("db down")

    p, _a = _placer(_BadStore())
    res = _place(p, "t1", 1)
    assert res.gtt_id                          # broker GTT placed despite persist failure
    assert store.fetch_all("SELECT * FROM gtt_state") == []  # no row, but no crash


def test_get_active_gtt_for_trade_returns_only_active(tmp_path: Path):
    # Y6: a trade may accumulate many rows (history); exactly one is ACTIVE.
    store = _make_store(tmp_path)
    _seed_trade(store, "t1")
    store.insert_gtt_state(gtt_id=111, trade_id="t1", symbol="RAMCOIND", exit_side="SELL",
                           qty=1, sl_trigger=334.0, sl_limit=324.0, tgt_trigger=343.0,
                           tgt_limit=342.0, created_at=_NOW)
    with store.transaction() as cur:
        cur.execute("UPDATE gtt_state SET status='CLEANED' WHERE gtt_id=111")
    store.insert_gtt_state(gtt_id=222, trade_id="t1", symbol="RAMCOIND", exit_side="SELL",
                           qty=1, sl_trigger=334.0, sl_limit=324.0, tgt_trigger=343.0,
                           tgt_limit=342.0, created_at=_NOW)
    active = store.get_active_gtt_for_trade("t1")
    assert active is not None and active["gtt_id"] == 222
    assert len(store.fetch_all("SELECT * FROM gtt_state WHERE trade_id='t1'")) == 2  # history kept
    assert len(store.get_active_gtt_states()) == 1
