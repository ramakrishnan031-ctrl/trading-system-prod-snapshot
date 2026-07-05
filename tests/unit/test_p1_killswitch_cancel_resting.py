"""
tests/unit/test_p1_killswitch_cancel_resting.py — Wave 2, P1 (capital/kill_switch.py:977).

The HARD_KILL flatten's `KillSwitch._cancel_trade_resting_exits` is the twin of H-1:
it SELECTed the non-existent column `orders.broker_order_id` (the real PK is
`order_id`, the broker-assigned id, core/schema.sql:271). sqlite raised
OperationalError on EVERY call; the `except` swallowed it and returned, so a
HARD_KILL flatten never cancelled the trade's resting SL/TGT at the broker — they
survived and could fill against a now-flat book (naked reverse; the AEROENTER-orphan
class).

Fix: `SELECT order_id ...` and cancel each leg by `order_id` (mirrors the H-1 fix,
commit 168e70d, and structure_exit_manager's already-correct version).

Runs the REAL `KillSwitch._cancel_trade_resting_exits` against the REAL schema via
the H-1 harness (tests/conftest.py `real_schema_store` / RealSchemaStore).
"""
from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace

import pytest

from capital.kill_switch import KillSwitch

_OLD_DEAD_QUERY = (
    "SELECT order_id, broker_order_id, leg FROM orders WHERE trade_id = ? "
    "AND leg IN ('SL','TGT') "
    "AND status NOT IN ('CANCELLED','FAILED','EXPIRED','COMPLETE')"
)


def _insert_order(conn, order_id, trade_id, leg, status):
    conn.execute(
        """
        INSERT INTO orders (
            order_id, trade_id, leg, transaction_type, order_type,
            product, variety, qty_requested, status, placed_at, updated_at
        ) VALUES (?, ?, ?, 'SELL', 'SL-M', 'MIS', 'regular', 10, ?,
                  '2026-07-05T10:00:00+05:30', '2026-07-05T10:00:00+05:30')
        """,
        (order_id, trade_id, leg, status),
    )


def _seed_resting_exits(conn, trade_id="T_P1"):
    """A trade with a live SL + live TGT (must be cancelled) plus decoys the WHERE
    clause must exclude: a cancelled SL, a completed ENTRY, and an SL on another trade."""
    _insert_order(conn, "SL_LIVE", trade_id, "SL", "TRIGGER_PENDING")
    _insert_order(conn, "TGT_LIVE", trade_id, "TGT", "OPEN")
    _insert_order(conn, "SL_DEAD", trade_id, "SL", "CANCELLED")       # excluded: status
    _insert_order(conn, "ENTRY_DONE", trade_id, "ENTRY", "COMPLETE")  # excluded: leg
    _insert_order(conn, "SL_OTHER", "T_OTHER", "SL", "OPEN")          # excluded: trade_id
    conn.commit()
    return trade_id


def test_harness_real_schema_old_query_is_dead(real_schema_db):
    """The real schema has no broker_order_id column: the pre-fix SELECT raises."""
    cols = {r["name"] for r in real_schema_db.execute("PRAGMA table_info(orders)")}
    assert "order_id" in cols and "broker_order_id" not in cols
    trade_id = _seed_resting_exits(real_schema_db)
    with pytest.raises(sqlite3.OperationalError):
        real_schema_db.execute(_OLD_DEAD_QUERY, (trade_id,)).fetchall()


def test_hard_kill_cancels_resting_sl_tgt_by_order_id(real_schema_store):
    """The REAL KillSwitch._cancel_trade_resting_exits cancels the two live resting
    legs at the broker by their order_id, before the HARD_KILL flatten.

    Pre-fix (broker_order_id): fetch_all raises OperationalError, the except swallows
    it and returns → adapter.cancel_order is NEVER called → this test FAILS.
    Post-fix (order_id): cancel_order is called for SL_LIVE and TGT_LIVE → PASSES.
    """
    trade_id = _seed_resting_exits(real_schema_store.conn)

    cancelled = []
    stub = SimpleNamespace(
        _store=real_schema_store,
        _adapter=SimpleNamespace(cancel_order=lambda oid: cancelled.append(oid)),
        _log=logging.getLogger("test_p1"),
    )

    KillSwitch._cancel_trade_resting_exits(stub, trade_id)

    assert set(cancelled) == {"SL_LIVE", "TGT_LIVE"}, (
        "P1 (kill_switch:977): the HARD_KILL flatten did not cancel the resting "
        "SL/TGT — the dead broker_order_id column was swallowed as a WARNING."
    )
