"""
tests/unit/test_h3_exit_retry_guard.py — Wave 1, H-3 regression.

H-3 (docs/audit/full_system_audit_04july2026.md): `_retry_limit_triple_exits`
fired from a STALE snapshot — it re-read nothing and its `_pending_exit_retry`
entries never expired. After an LTP-validation failure, G5b places a recovery SL
within ~15-30s; a later tick then fired the stale retry:
  - trade still OPEN     -> a SECOND SL (duplicate-SL / RAMCOIND oversell), OR
  - trade already CLOSED -> SL+TGT on a flat position -> naked reverse.

Fix: mirror `retry_tgt_for_trade` — re-read the trade (`get_trade_for_tgt_retry`),
bail unless OPEN/PARTIAL, skip the SL leg if a non-terminal SL exists
(`get_sl_order_for_trade`) and place only the missing TGT via `retry_tgt_for_trade`,
and drop entries older than `_EXIT_RETRY_TTL_SEC`.

These tests run the REAL `_retry_limit_triple_exits` + REAL StateStore query
methods (`get_trade_for_tgt_retry` / `get_sl_order_for_trade`) against the REAL
`core/schema.sql`. The conftest `RealSchemaStore` shim does not implement those
StateStore query methods (replicating them would be the very "divergent check" the
fix forbids), so the store here is a real `StateStore` over the real schema — the
same schema-backed principle as the H-1 harness. `place_deferred_exits` /
`retry_tgt_for_trade` are spied to assert the PLACEMENT DECISION.
"""
from __future__ import annotations

import logging
import threading
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from core.state_store import StateStore
from core.time_authority import now_ist
from orders.order_placer import (
    OrderPlacer, _ExitRetryParams, _FillEntry, _EXIT_RETRY_TTL_SEC,
)

_SCHEMA = Path("core/schema.sql")


def _make_store(tmp: Path) -> StateStore:
    return StateStore(db_path=tmp / "test.db", schema_path=_SCHEMA)


def _seed_signal(store, sig_id):
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, symbol, scanner, strategy, "
            "triggered_at, received_at, expires_at, status, fingerprint, "
            "fingerprint_date) VALUES (?, 'RELIANCE', 'gap_go_long', "
            "'gap_go_long', ?, ?, ?, 'PROCESSING', ?, ?)",
            (sig_id, now, now, now, "fp_" + sig_id, now[:10]),
        )
    return sig_id


def _seed_trade(store, trade_id, status):
    sig_id = _seed_signal(store, "sig_" + trade_id)
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
            "qty_planned, qty_filled, entry_target_price, entry_actual_price, "
            "sl_initial, tgt_initial, margin_reserved, risk_amount, created_at, "
            "entry_time, order_protocol, status, updated_at) VALUES "
            "(?, ?, 'RELIANCE', 'LONG', 'gap_go_long', 10, 10, 2500.0, 2500.0, "
            "2450.0, 2600.0, 5000.0, 500.0, ?, ?, 'LIMIT_TRIPLE', ?, ?)",
            (trade_id, sig_id, now, now, status, now),
        )


def _seed_sl_order(store, trade_id, status="TRIGGER_PENDING"):
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO orders (order_id, trade_id, leg, transaction_type, "
            "order_type, product, variety, qty_requested, status, placed_at, "
            "updated_at) VALUES (?, ?, 'SL', 'SELL', 'SL-M', 'MIS', 'regular', "
            "10, ?, ?, ?)",
            ("SL_" + trade_id, trade_id, status, now, now),
        )


def _build_stub(store):
    """A duck-typed OrderPlacer ``self``: real store + real guards; the placement
    engine and retry_tgt_for_trade are spied so tests assert the DECISION."""
    calls = {"place": [], "retry_tgt": [], "insert": []}
    legs = SimpleNamespace(
        is_gtt=False,
        sl_broker_order_id="SLB", sl_internal_id="SLI", sl_order_type="SL-M",
        sl_price=2450.0, sl_trigger_price=2450.0, sl_clamped=False,
        tgt_broker_order_id="TGB", tgt_internal_id="TGI", tgt_price=2600.0,
        tgt_clamped=False,
    )

    def _place_deferred_exits(**kw):
        calls["place"].append(kw)
        return legs

    def _retry_tgt(trade_id):
        calls["retry_tgt"].append(trade_id)
        return "placed"

    def _insert_atomic(trade_id, specs):
        calls["insert"].append((trade_id, [s.leg for s in specs]))

    stub = SimpleNamespace(
        _om=SimpleNamespace(_store=store, insert_orders_atomic=_insert_atomic),
        _engine=SimpleNamespace(place_deferred_exits=_place_deferred_exits),
        retry_tgt_for_trade=_retry_tgt,
        _log=logging.getLogger("test_h3"),
        _resolve_fill_rr=lambda *a, **k: 2.0,
        _product_resolver=SimpleNamespace(resolve=lambda intent: "MIS"),
        _fill_map={}, _fill_map_lock=threading.Lock(),
        _order_monitor=SimpleNamespace(track=lambda **k: None),
        _verify_exits_placed=lambda **k: None,
        _pending_exit_retry={}, _pending_exit_retry_lock=threading.Lock(),
        _cancel_broker_orders=lambda *a, **k: None,
        _emergency_market_exit=lambda *a, **k: True,
        _fire_hard_kill_for_unprotected_position=lambda *a, **k: None,
        _is_ltp_validation_error=lambda exc: False,
        _finalize_cnc_gtt=lambda *a, **k: None,
    )
    return stub, calls


def _params(trade_id, *, age_sec=0.0):
    fe = _FillEntry(
        trade_id=trade_id, reservation_id="r", symbol="RELIANCE", qty=10,
        leg="SL", order_protocol="LIMIT_TRIPLE", direction="LONG",
        side="BUY", sl_price=2450.0, intent="INTRADAY",
    )
    return _ExitRetryParams(
        trade_id=trade_id, fill_entry=fe, qty_filled=10, avg_fill_price=2500.0,
        reason="ltp_validation", enqueued_at=now_ist() - timedelta(seconds=age_sec),
    )


def test_closed_trade_bails_no_exits_placed():
    """Test 1 — CLOSED trade: a stale retry must place NOTHING (no naked reverse).
    RED on unfixed: places SL+TGT on a flat position."""
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        _seed_trade(store, "T1", "CLOSED")
        stub, calls = _build_stub(store)
        OrderPlacer._retry_limit_triple_exits(stub, _params("T1"))
        assert calls["place"] == [], "must NOT place SL+TGT on a closed trade"
        assert calls["retry_tgt"] == []
        assert stub._pending_exit_retry == {}, "stale entry must not be re-queued"
        store.close()


def test_open_with_existing_sl_skips_sl():
    """Test 2 — OPEN + non-terminal SL: skip the SL leg (no duplicate SL) and
    delegate only the missing TGT. RED on unfixed: duplicate SL placed."""
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        _seed_trade(store, "T2", "OPEN")
        _seed_sl_order(store, "T2", "TRIGGER_PENDING")
        stub, calls = _build_stub(store)
        OrderPlacer._retry_limit_triple_exits(stub, _params("T2"))
        assert calls["place"] == [], "must NOT place a second SL"
        assert calls["retry_tgt"] == ["T2"], "must delegate the missing TGT"
        store.close()


def test_open_without_sl_places_sl():
    """Test 3 — OPEN + no SL: the legitimate retry MUST place SL+TGT (guard must
    not over-bail). PASSES before and after (proves no over-bail)."""
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        _seed_trade(store, "T3", "OPEN")
        stub, calls = _build_stub(store)
        OrderPlacer._retry_limit_triple_exits(stub, _params("T3"))
        assert len(calls["place"]) == 1, "OPEN + no SL must place the exits"
        assert calls["retry_tgt"] == []
        assert calls["insert"] and calls["insert"][0][1] == ["SL", "TGT"]
        store.close()


def test_stale_entry_expires_without_placing():
    """Test 4 — an entry older than the TTL is dropped without placing anything
    (even though the trade is OPEN + unprotected, which would otherwise place)."""
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = _make_store(Path(tmp))
        _seed_trade(store, "T4", "OPEN")
        stub, calls = _build_stub(store)
        OrderPlacer._retry_limit_triple_exits(
            stub, _params("T4", age_sec=_EXIT_RETRY_TTL_SEC + 60))
        assert calls["place"] == [], "a stale entry must not place exits"
        assert calls["retry_tgt"] == []
        assert stub._pending_exit_retry == {}
        store.close()
