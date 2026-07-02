"""A-1/E-1 naked-orphan recovery — Phase B part 1 (foundational, isolated units).

Covers the PURE tag-correlation function and the ATOMIC guarded recovery-state
transitions. The adoption ORCHESTRATION (_adopt_or_fail) + the _check_unknown_in_flight
rewrite + the order_monitor reroute (Phases B-part-2 / C / D) land in a later pass;
these two units are safe, isolated, and unused until then.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from core.ids import truncate_tag_for_broker
from core.state_store import StateStore
from orders.order_manager import OrderManager
from orders.order_reconciler import correlate_entry_by_tag


def _order(tag, side, symbol="RELIANCE", qty=10, status="OPEN", oid="B1"):
    """Build a get_all_orders()-shaped dict."""
    return {
        "order_id": oid, "tag": tag, "status": status,
        "transaction_type": side, "symbol": symbol, "quantity": qty,
        "filled_quantity": 0, "average_price": 0.0, "trigger_price": 0.0,
    }


_TID = "trd_" + "a" * 32          # a concrete trade_id
_TAG = truncate_tag_for_broker(_TID)   # = _TID[:16] = "trd_aaaaaaaaaaaa"


# ── correlate_entry_by_tag (pure) ────────────────────────────────────────────
def test_correlate_match_single_entry_side():
    orders = [_order(_TAG, "BUY", "RELIANCE", 10)]
    kind, o = correlate_entry_by_tag(_TID, "LONG", "RELIANCE", 10, orders)
    assert kind == "MATCH" and o["order_id"] == "B1"


def test_correlate_absent_when_no_tag_match():
    orders = [_order("trd_other0000", "BUY", "RELIANCE", 10)]
    assert correlate_entry_by_tag(_TID, "LONG", "RELIANCE", 10, orders) == ("ABSENT", None)


def test_correlate_side_filter_excludes_exit_leg():
    # SL/TGT legs carry the SAME tag but the OPPOSITE side of a LONG entry (SELL).
    # They must NOT be mistaken for the entry.
    orders = [_order(_TAG, "SELL", "RELIANCE", 10)]
    assert correlate_entry_by_tag(_TID, "LONG", "RELIANCE", 10, orders) == ("ABSENT", None)


def test_correlate_short_entry_is_sell():
    orders = [_order(_TAG, "SELL", "RELIANCE", 10)]
    kind, o = correlate_entry_by_tag(_TID, "SHORT", "RELIANCE", 10, orders)
    assert kind == "MATCH"


def test_correlate_collision_narrowed_to_match():
    # Two orders share tag+side (a 48-bit tag collision); narrow by symbol+qty.
    orders = [
        _order(_TAG, "BUY", "RELIANCE", 10, oid="MINE"),
        _order(_TAG, "BUY", "TCS", 5, oid="OTHER"),
    ]
    kind, o = correlate_entry_by_tag(_TID, "LONG", "RELIANCE", 10, orders)
    assert kind == "MATCH" and o["order_id"] == "MINE"


def test_correlate_ambiguous_when_unnarrowable():
    # True collision that symbol+qty can't split → never blind-adopt.
    orders = [
        _order(_TAG, "BUY", "RELIANCE", 10, oid="A"),
        _order(_TAG, "BUY", "RELIANCE", 10, oid="B"),
    ]
    assert correlate_entry_by_tag(_TID, "LONG", "RELIANCE", 10, orders) == ("AMBIGUOUS", None)


def test_correlate_bad_direction_is_absent():
    orders = [_order(_TAG, "BUY", "RELIANCE", 10)]
    assert correlate_entry_by_tag(_TID, "", "RELIANCE", 10, orders) == ("ABSENT", None)


# ── atomic guarded recovery-state transitions ────────────────────────────────
def _mk_store_trade(status: str):
    td = tempfile.mkdtemp()
    store = StateStore(Path(td) / "t.db")
    om = OrderManager(store, __import__("logging").getLogger("t"))
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals (signal_id,symbol,scanner,strategy,triggered_at,
               received_at,expires_at,status,fingerprint,fingerprint_date)
               VALUES ('sig_1','RELIANCE','S','vwap_bounce_long','2026-07-02T09:30:00+05:30',
               '2026-07-02T09:30:00+05:30','2026-07-02T09:35:00+05:30','TRADED','fp1','2026-07-02')"""
        )
    tid = om.create_trade(
        signal_id="sig_1", symbol="RELIANCE", direction="LONG",
        strategy="vwap_bounce_long", sector=None, qty=10, entry_target_price=2500.0,
        sl_initial=2475.0, tgt_initial=2550.0, order_protocol="LIMIT_TRIPLE",
        margin_reserved=5000.0, risk_amount=250.0,
    )
    with store.transaction() as cur:
        cur.execute("UPDATE trades SET status=? WHERE trade_id=?", (status, tid))
    return store, tid


def test_adopt_recovery_to_open_wins_once():
    store, tid = _mk_store_trade("UNKNOWN_IN_FLIGHT")
    assert store.adopt_recovery_trade_to_open(tid) is True       # first cycle wins
    assert store.adopt_recovery_trade_to_open(tid) is False      # idempotent: already OPEN
    row = store.fetch_one("SELECT status, recovered_flag FROM trades WHERE trade_id=?", (tid,))
    assert row["status"] == "OPEN" and row["recovered_flag"] == 1


def test_adopt_from_pending_crash_state():
    store, tid = _mk_store_trade("PENDING")
    assert store.adopt_recovery_trade_to_open(tid) is True


def test_adopt_refuses_non_recovery_state():
    store, tid = _mk_store_trade("OPEN")
    assert store.adopt_recovery_trade_to_open(tid) is False      # not a recovery state


def test_fail_recovery_wins_once_and_guards():
    store, tid = _mk_store_trade("UNKNOWN_IN_FLIGHT")
    assert store.fail_recovery_trade(tid) is True
    assert store.fail_recovery_trade(tid) is False               # already FAILED
    assert store.fetch_one("SELECT status FROM trades WHERE trade_id=?", (tid,))["status"] == "FAILED"


def test_fail_refuses_open_trade():
    store, tid = _mk_store_trade("OPEN")
    assert store.fail_recovery_trade(tid) is False               # never FAIL a live trade
