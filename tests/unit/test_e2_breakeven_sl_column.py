"""
tests/unit/test_e2_breakeven_sl_column.py -- Trading System v2

E2: BreakevenManager._get_sl_broker_order_id selects a column that does not
exist. `orders` has no `broker_order_id`; the broker-assigned id IS the primary
key `order_id` ("broker-assigned ID", core/schema.sql:303). The bad SELECT
raises OperationalError, which the method's own `except Exception` swallows into
a db_lookup_error log and a None return -- so the breakeven SL advance would
silently never happen.

⚠️ LATENT: BreakevenManager is never constructed in main.py, so order_placer's
`self._breakeven_manager` is always None and register_trade() is never called.
Nothing changes at runtime.

WHY THIS SURVIVED FOUR FIX CYCLES: the existing suite's _MockStore hand-builds a
row keyed on "broker_order_id" -- a shape the real `orders` table cannot produce.
The fixture vouched for the nonexistent column. This file uses `real_schema_store`
(conftest), which runs real SQL against core/schema.sql and therefore raises on a
bad column exactly as production does; it cannot be vacuous.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from orders.breakeven_manager import BreakevenManager


def _log():
    log = logging.getLogger("test_e2_be")
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return log


def _seed_sl_order(store, *, order_id="KITE_SL_001", trade_id="t1",
                   status="TRIGGER_PENDING", placed_at="2026-07-24T10:00:00+05:30"):
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO orders
               (order_id, trade_id, leg, leg_index, transaction_type, order_type,
                product, variety, qty_requested, trigger_price, status,
                qty_filled, placed_at, updated_at)
               VALUES (?, ?, 'SL', 0, 'SELL', 'SL-M', 'MIS', 'regular', 10, 990.0,
                       ?, 0, ?, ?)""",
            (order_id, trade_id, status, placed_at, placed_at),
        )


class _NullAdapter:
    def modify_order(self, *a, **kw):    # pragma: no cover - not exercised here
        raise AssertionError("adapter must not be reached in these tests")


def _mgr(store):
    return BreakevenManager(adapter=_NullAdapter(), state_store=store, logger=_log())


class TestSlOrderIdLookupAgainstTheRealSchema:

    def test_the_column_the_query_selects_actually_exists(self, real_schema_store):
        """The premise, verified against core/schema.sql rather than asserted."""
        cols = {r[1] for r in real_schema_store.fetch_all("PRAGMA table_info(orders)")}
        assert "order_id" in cols
        assert "broker_order_id" not in cols, (
            "if this ever fails the E2 fix is obsolete -- re-read the schema"
        )

    def test_active_sl_order_id_is_returned(self, real_schema_store):
        """RED on HEAD: `SELECT broker_order_id FROM orders` raises
        OperationalError, the bare except swallows it, and the method returns
        None -- the breakeven advance silently never fires."""
        _seed_sl_order(real_schema_store)
        got = _mgr(real_schema_store)._get_sl_broker_order_id("t1")
        assert got == "KITE_SL_001"

    def test_no_sl_order_still_returns_none(self, real_schema_store):
        """PIN: absent row -> None (not an exception, not a stray id)."""
        assert _mgr(real_schema_store)._get_sl_broker_order_id("nope") is None

    @pytest.mark.parametrize("status", ["CANCELLED", "COMPLETE", "FAILED"])
    def test_terminal_sl_orders_are_excluded(self, real_schema_store, status):
        """PIN: the live-status set is unchanged. structure_exit_manager.py:74
        derives _SL_LIVE_EXCLUDE from this call site by reference, so widening it
        here would silently desync that module. E2 fixes the COLUMN only."""
        _seed_sl_order(real_schema_store, status=status)
        assert _mgr(real_schema_store)._get_sl_broker_order_id("t1") is None

    def test_the_rejected_clause_is_dead_because_the_status_is_illegal(
            self, real_schema_store):
        """RECORDED, not fixed. The query also excludes 'REJECTED', but the
        schema CHECK forbids that value outright -- order_monitor maps Kite's
        REJECTED to FAILED and no raw broker string reaches the column
        (core/schema.sql:322-332). So that clause can never match anything.
        Harmless; left in place because structure_exit_manager mirrors this exact
        set by reference and removing it here would desync the two."""
        with pytest.raises(sqlite3.IntegrityError):
            _seed_sl_order(real_schema_store, status="REJECTED")

    def test_expired_is_deliberately_still_treated_as_live(self, real_schema_store):
        """PIN of a known divergence, NOT an endorsement: state_store's
        get_sl_order_for_trade also excludes EXPIRED; this call site does not.
        Pinned so the difference is a decision, not a drift."""
        _seed_sl_order(real_schema_store, status="EXPIRED")
        assert _mgr(real_schema_store)._get_sl_broker_order_id("t1") == "KITE_SL_001"

    def test_a_db_error_is_still_swallowed_into_none(self, real_schema_store):
        """PIN: the fail-safe contract survives -- a broken store logs and
        returns None rather than propagating into the candle callback."""
        class _Boom:
            def fetch_one(self, *a, **kw):
                raise sqlite3.OperationalError("boom")
        assert _mgr(_Boom())._get_sl_broker_order_id("t1") is None
