"""
tests/unit/test_h1_cancel_resting_exits.py — Wave 1, H-1 regression.

H-1 (docs/audit/full_system_audit_04july2026.md): OrderPlacer._cancel_trade_resting_exits
SELECTed a non-existent column ``orders.broker_order_id``. The real orders PK is
``order_id`` (core/schema.sql:271, "-- broker-assigned ID"); there is no
``broker_order_id`` column and no migration adds one. sqlite raised
OperationalError on EVERY call; the ``except`` swallowed it as a WARNING and
returned, so FIX-190 Bug E — cancelling a trade's resting SL/TGT at the broker
BEFORE an emergency flatten — never ran. The resting legs survived the flatten
and could fill against a now-flat book → naked reverse position.

Fix: ``SELECT order_id ...`` (order_id IS the broker-assigned id passed to
adapter.cancel_order).

Both tests run against the REAL schema via the reusable harness in
tests/conftest.py (real_schema_db / real_schema_store), so the dead column fails
exactly as it does in production — a hand-written mock schema would hide the bug.
"""
from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace

import pytest

from orders.order_placer import OrderPlacer

# The two query forms: the dead pre-H-1 column vs. the fixed real column.
_OLD_DEAD_QUERY = (
    "SELECT broker_order_id FROM orders WHERE trade_id = ? "
    "AND leg IN ('SL','TGT') "
    "AND status NOT IN ('CANCELLED','FAILED','EXPIRED','COMPLETE')"
)
_FIXED_QUERY = (
    "SELECT order_id FROM orders WHERE trade_id = ? "
    "AND leg IN ('SL','TGT') "
    "AND status NOT IN ('CANCELLED','FAILED','EXPIRED','COMPLETE')"
)


def _insert_order(conn: sqlite3.Connection, order_id: str, trade_id: str,
                  leg: str, status: str) -> None:
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


def _seed_resting_exits(conn: sqlite3.Connection, trade_id: str = "T_H1") -> str:
    """A trade with a live SL + live TGT (must be returned) plus decoys the WHERE
    clause must exclude: a cancelled SL (status filter), a completed ENTRY (leg
    filter), and a live SL on a different trade (trade_id filter)."""
    _insert_order(conn, "SL_LIVE", trade_id, "SL", "TRIGGER_PENDING")
    _insert_order(conn, "TGT_LIVE", trade_id, "TGT", "OPEN")
    _insert_order(conn, "SL_DEAD", trade_id, "SL", "CANCELLED")       # excluded: status
    _insert_order(conn, "ENTRY_DONE", trade_id, "ENTRY", "COMPLETE")  # excluded: leg
    _insert_order(conn, "SL_OTHER", "T_OTHER", "SL", "OPEN")          # excluded: trade_id
    conn.commit()
    return trade_id


# ── Harness self-validation + red/green at the SQL layer ─────────────────────
def test_real_schema_has_order_id_not_broker_order_id(real_schema_db):
    """The harness materializes the REAL schema: order_id exists, broker_order_id
    does not — and the pre-H-1 query is provably dead while the fixed one works."""
    cols = {r["name"] for r in real_schema_db.execute("PRAGMA table_info(orders)")}
    assert "order_id" in cols, "order_id must be the real orders column"
    assert "broker_order_id" not in cols, "broker_order_id must NOT exist (H-1)"

    trade_id = _seed_resting_exits(real_schema_db)

    # RED: the pre-H-1 query raises against the real schema (the swallowed error).
    with pytest.raises(sqlite3.OperationalError):
        real_schema_db.execute(_OLD_DEAD_QUERY, (trade_id,)).fetchall()

    # GREEN: the fixed query returns exactly the two live resting legs.
    rows = real_schema_db.execute(_FIXED_QUERY, (trade_id,)).fetchall()
    assert {r["order_id"] for r in rows} == {"SL_LIVE", "TGT_LIVE"}


# ── Method-level regression: the real _cancel_trade_resting_exits ────────────
def test_cancel_trade_resting_exits_cancels_live_sl_tgt(real_schema_store):
    """Exercise the REAL OrderPlacer._cancel_trade_resting_exits against the real
    schema.

    Pre-fix (broker_order_id): fetch_all raises OperationalError, the method's
    except swallows it and returns, so _cancel_broker_orders is NEVER called
    (the bug) → this assertion FAILS.
    Post-fix (order_id): it is called once with the two live resting order_ids
    → PASSES. This is the meaningful red/green the H-1 fix must satisfy.
    """
    trade_id = _seed_resting_exits(real_schema_store.conn)

    calls: list[tuple] = []
    stub = SimpleNamespace(
        _om=SimpleNamespace(_store=real_schema_store),
        _log=logging.getLogger("test_h1"),
        _cancel_broker_orders=lambda ids, reason: calls.append((list(ids), reason)),
    )

    # Call the real (unbound) method with the stub as ``self`` — runs the exact
    # SELECT + id-comprehension + cancel call, no heavy OrderPlacer construction.
    OrderPlacer._cancel_trade_resting_exits(stub, trade_id)

    assert len(calls) == 1, (
        "H-1 regression: _cancel_broker_orders was not called — the resting "
        "SL/TGT were never cancelled before the emergency flatten (the dead "
        "broker_order_id column was swallowed as a WARNING)."
    )
    ids, reason = calls[0]
    assert set(ids) == {"SL_LIVE", "TGT_LIVE"}
    assert trade_id in reason
