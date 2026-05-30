"""
tests/unit/test_fix129_order_state_validation.py

Tests for FIX-129 Item 26: reconciliation_status column on orders table.
  - update_order_reconciliation_status() writes OK/SL_MISSING/MISMATCH/ORPHAN
  - Schema v16 has the column
  - StateStore helper is idempotent (UPDATE on missing order_id is a no-op)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.ids import new_signal_id, new_trade_id, new_order_id
from core.state_store import StateStore
from core.time_authority import now_ist


def _log():
    return logging.getLogger("test_fix129_osv")


def _make_store(tmp: Path) -> StateStore:
    return StateStore(db_path=tmp / "test.db")


def _insert_order(store: StateStore, order_id: str, trade_id: str, leg: str = "SL") -> None:
    """Insert a minimal orders row for testing."""
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            """INSERT OR IGNORE INTO orders
               (order_id, trade_id, leg, leg_index, transaction_type, order_type,
                product, variety, qty_requested, status, placed_at, updated_at)
               VALUES (?, ?, ?, 0, 'SELL', 'SL-M', 'MIS', 'regular', 10, 'OPEN', ?, ?)""",
            (order_id, trade_id, leg, now, now),
        )


def _insert_trade(store: StateStore, trade_id: str, sig_id: str) -> None:
    """Insert minimal trade + signal rows."""
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            """INSERT OR IGNORE INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, fingerprint, fingerprint_date)
               VALUES (?, 'RELIANCE', 'test', 'test', ?, ?, ?, 'TRADED', ?, ?)""",
            (sig_id, now, now, now, sig_id[:8], now[:10]),
        )
        cur.execute(
            """INSERT OR IGNORE INTO trades
               (trade_id, signal_id, symbol, direction, strategy, status,
                qty_planned, entry_target_price, sl_initial, tgt_initial,
                order_protocol, margin_reserved, risk_amount, created_at, updated_at)
               VALUES (?, ?, 'RELIANCE', 'LONG', 'test', 'OPEN',
                       10, 1000.0, 950.0, 1100.0, 'LIMIT_TRIPLE', 2000.0, 500.0, ?, ?)""",
            (trade_id, sig_id, now, now),
        )


class TestReconciliationStatusColumn:

    def test_schema_v16_has_reconciliation_status_column(self) -> None:
        """Schema v16 orders table includes reconciliation_status column."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            sig_id = new_signal_id()
            trade_id = new_trade_id()
            order_id = new_order_id()
            _insert_trade(store, trade_id, sig_id)
            _insert_order(store, order_id, trade_id)

            # Verify column exists by reading it
            row = store.fetch_one("SELECT reconciliation_status FROM orders WHERE order_id = ?", (order_id,))
            assert row is not None
            assert "reconciliation_status" in dict(row)
            assert row["reconciliation_status"] is None  # NULL by default
            store.close()
            print("  OK: schema v16 has reconciliation_status column (NULL by default)")

    def test_update_sets_ok_status(self) -> None:
        """update_order_reconciliation_status() sets status to 'OK'."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            sig_id = new_signal_id()
            trade_id = new_trade_id()
            order_id = new_order_id()
            _insert_trade(store, trade_id, sig_id)
            _insert_order(store, order_id, trade_id)

            store.update_order_reconciliation_status(order_id, "OK")

            row = store.fetch_one("SELECT reconciliation_status FROM orders WHERE order_id = ?", (order_id,))
            assert row["reconciliation_status"] == "OK"
            store.close()
            print("  OK: update_order_reconciliation_status sets 'OK'")

    def test_update_sets_sl_missing_status(self) -> None:
        """update_order_reconciliation_status() sets status to 'SL_MISSING'."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            sig_id = new_signal_id()
            trade_id = new_trade_id()
            order_id = new_order_id()
            _insert_trade(store, trade_id, sig_id)
            _insert_order(store, order_id, trade_id)

            store.update_order_reconciliation_status(order_id, "SL_MISSING")

            row = store.fetch_one("SELECT reconciliation_status FROM orders WHERE order_id = ?", (order_id,))
            assert row["reconciliation_status"] == "SL_MISSING"
            store.close()
            print("  OK: update_order_reconciliation_status sets 'SL_MISSING'")

    def test_update_missing_order_id_is_noop(self) -> None:
        """UPDATE on non-existent order_id doesn't raise."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            # Should not raise even for unknown order_id
            store.update_order_reconciliation_status("NONEXISTENT_ORDER", "OK")
            store.close()
            print("  OK: UPDATE on missing order_id is no-op (no error)")

    def test_status_can_be_overwritten(self) -> None:
        """reconciliation_status can be updated multiple times (idempotent updates)."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            sig_id = new_signal_id()
            trade_id = new_trade_id()
            order_id = new_order_id()
            _insert_trade(store, trade_id, sig_id)
            _insert_order(store, order_id, trade_id)

            store.update_order_reconciliation_status(order_id, "OK")
            store.update_order_reconciliation_status(order_id, "SL_MISSING")  # overwrite
            store.update_order_reconciliation_status(order_id, "OK")           # restore

            row = store.fetch_one("SELECT reconciliation_status FROM orders WHERE order_id = ?", (order_id,))
            assert row["reconciliation_status"] == "OK"
            store.close()
            print("  OK: reconciliation_status can be overwritten")


if __name__ == "__main__":
    tests = [
        TestReconciliationStatusColumn().test_schema_v16_has_reconciliation_status_column,
        TestReconciliationStatusColumn().test_update_sets_ok_status,
        TestReconciliationStatusColumn().test_update_sets_sl_missing_status,
        TestReconciliationStatusColumn().test_update_missing_order_id_is_noop,
        TestReconciliationStatusColumn().test_status_can_be_overwritten,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
