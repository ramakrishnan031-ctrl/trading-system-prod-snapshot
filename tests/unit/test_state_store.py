"""
tests/unit/test_state_store.py

Validates core/state_store.py end-to-end:
  - Schema initialization is idempotent
  - Schema version check works
  - Transaction commit persists data
  - Transaction rollback discards data on exception
  - The signals dedup unique index actually rejects duplicates
  - Foreign keys are enforced
  - Thread safety: two threads writing concurrently don't corrupt each other
  - Connection per thread works correctly

Run: python -m pytest tests/unit/test_state_store.py -v
Or:  python tests/unit/test_state_store.py  (standalone mode)
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

# Make core importable when run standalone
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from datetime import datetime

from core.state_store import (
    StateStore,
    SchemaVersionMismatch,
    StateStoreError,
    EXPECTED_SCHEMA_VERSION,
)
from orders.shadow_tracker import Inning


# ─────────────────────────────────────────────────────────────────────────────
# Test helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_store(tmpdir: Path) -> StateStore:
    """Create a fresh StateStore in a temp directory."""
    db_path = tmpdir / "test.db"
    return StateStore(db_path)


def insert_test_signal(
    store: StateStore,
    signal_id: str,
    symbol: str = "RELIANCE",
    scanner: str = "GAP_GO_LONG",
    fingerprint: str = "fp_abc",
    fingerprint_date: str = "2026-04-14",
    status: str = "TRADED",
) -> None:
    """Insert a signal row using state_store.transaction()."""
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals
              (signal_id, symbol, scanner, strategy, triggered_at, received_at,
               expires_at, status, fingerprint, fingerprint_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id, symbol, scanner, scanner.lower(),
                "2026-04-14T09:30:00+05:30",
                "2026-04-14T09:30:01+05:30",
                "2026-04-14T09:31:31+05:30",
                status, fingerprint, fingerprint_date,
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_schema_creates_all_tables(tmpdir: Path) -> None:
    """All expected tables must exist after initialization.

    BL-5 (v10): capital_ledger removed (was dead; never written). Also
    asserts capital_ledger is NOT present — this is the dead-table
    retirement guard.
    """
    store = make_store(tmpdir)
    expected = {
        "schema_meta", "signals", "trades", "orders",
        "capital_snapshot", "system_events", "session",
        "fm_ledger", "kill_switch_state", "webhook_audit", "eod_squareoff_log",
        "reconciliation_log", "screener_results", "smart_tgt_state",
        "innings",
    }
    actual = set(store.table_names())
    missing = expected - actual
    assert not missing, f"Missing tables: {missing}"
    assert "capital_ledger" not in actual, \
        "capital_ledger must not be present post-BL-5 (v10 retirement)"
    print(f"  OK All {len(expected)} tables created; capital_ledger retired (v10)")
    store.close()


def test_schema_version_matches_expected(tmpdir: Path) -> None:
    """The schema_version row must equal EXPECTED_SCHEMA_VERSION."""
    store = make_store(tmpdir)
    version = store.get_schema_version()
    assert version == EXPECTED_SCHEMA_VERSION, \
        f"Expected v{EXPECTED_SCHEMA_VERSION}, got v{version}"
    print(f"  OK Schema version is v{version}")
    store.close()


def test_schema_initialization_is_idempotent(tmpdir: Path) -> None:
    """Re-opening the same DB must not error or duplicate tables."""
    store1 = make_store(tmpdir)
    store1.close()
    
    # Re-open the SAME db path
    store2 = StateStore(tmpdir / "test.db")
    tables = store2.table_names()
    assert "signals" in tables
    assert store2.get_schema_version() == EXPECTED_SCHEMA_VERSION
    print(f"  OK Re-open is idempotent ({len(tables)} tables found)")
    store2.close()


def test_transaction_commit_persists_data(tmpdir: Path) -> None:
    """Successful transaction must persist data after commit."""
    store = make_store(tmpdir)
    insert_test_signal(store, "sig-001")
    
    row = store.fetch_one("SELECT * FROM signals WHERE signal_id = ?", ("sig-001",))
    assert row is not None, "Inserted signal not found"
    assert row["symbol"] == "RELIANCE"
    assert row["status"] == "TRADED"
    print(f"  OK Committed signal persisted: {row['signal_id']} / {row['symbol']}")
    store.close()


def test_transaction_rollback_on_exception(tmpdir: Path) -> None:
    """Exception inside transaction must roll back all changes."""
    store = make_store(tmpdir)
    
    initial_count = store.row_count("signals")
    
    try:
        with store.transaction() as cur:
            cur.execute(
                """
                INSERT INTO signals
                  (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                   expires_at, status, fingerprint, fingerprint_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("sig-rollback", "TCS", "GAP_GO_LONG", "gap_go_long",
                 "2026-04-14T09:30:00+05:30", "2026-04-14T09:30:01+05:30",
                 "2026-04-14T09:31:31+05:30", "TRADED",
                 "fp_rollback", "2026-04-14"),
            )
            # Now raise an exception — this MUST roll back the insert above
            raise RuntimeError("simulated failure")
    except RuntimeError:
        pass  # expected
    
    final_count = store.row_count("signals")
    assert final_count == initial_count, \
        f"Rollback failed: count went from {initial_count} to {final_count}"
    
    # Verify the specific row is not there
    row = store.fetch_one(
        "SELECT * FROM signals WHERE signal_id = ?", ("sig-rollback",)
    )
    assert row is None, "Rolled-back row still exists"
    print(f"  OK Transaction rolled back; row count unchanged at {final_count}")
    store.close()


def test_dedup_unique_index_rejects_duplicates(tmpdir: Path) -> None:
    """
    The (fingerprint, fingerprint_date) unique index must reject a second
    insert with the same fingerprint+date. This is P6's database-level
    deduplication enforcement.
    """
    store = make_store(tmpdir)
    
    # First insert — should succeed
    insert_test_signal(store, "sig-dup-1", fingerprint="fp_xyz",
                       fingerprint_date="2026-04-14")
    
    # Second insert with same fingerprint+date — MUST fail
    raised = False
    try:
        insert_test_signal(store, "sig-dup-2", fingerprint="fp_xyz",
                           fingerprint_date="2026-04-14")
    except sqlite3.IntegrityError as e:
        raised = True
        print(f"  OK Duplicate rejected: {e}")
    
    assert raised, "Duplicate fingerprint+date was NOT rejected by unique index"
    
    # Third insert with same fingerprint but DIFFERENT date — MUST succeed
    insert_test_signal(store, "sig-dup-3", fingerprint="fp_xyz",
                       fingerprint_date="2026-04-15")
    
    count = store.row_count("signals")
    assert count == 2, f"Expected 2 rows (sig-dup-1 + sig-dup-3), got {count}"
    print(f"  OK Same fingerprint on different date allowed; {count} rows total")
    store.close()


def test_foreign_keys_enforced(tmpdir: Path) -> None:
    """
    PRAGMA foreign_keys = ON must be active. Inserting a row that violates
    a FK must fail.
    """
    store = make_store(tmpdir)
    
    # Try to insert an order row referencing a non-existent trade_id
    raised = False
    try:
        with store.transaction() as cur:
            cur.execute(
                """
                INSERT INTO orders
                  (order_id, trade_id, leg, transaction_type, order_type,
                   product, variety, qty_requested, status, placed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("ord-001", "trade-does-not-exist", "ENTRY", "BUY", "LIMIT",
                 "MIS", "regular", 10, "PENDING",
                 "2026-04-14T09:30:00+05:30", "2026-04-14T09:30:00+05:30"),
            )
    except sqlite3.IntegrityError as e:
        raised = True
        print(f"  OK FK violation caught: {e}")
    
    assert raised, "Foreign key violation was NOT caught"
    store.close()


def test_capital_snapshot_single_row_constraint(tmpdir: Path) -> None:
    """
    capital_snapshot has CHECK (id = 1). A second insert with id != 1 fails;
    a second insert with id = 1 fails on PK constraint.
    """
    store = make_store(tmpdir)
    
    # Insert id=1 — should succeed
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO capital_snapshot
              (id, cash_floor, realized_pnl_today, margin_used, margin_reserved,
               charges_today, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            """,
            (1_000_000.0, 0.0, 0.0, 0.0, 0.0, "2026-04-14T09:00:00+05:30"),
        )
    
    # Try inserting id=2 — should fail on CHECK constraint
    raised = False
    try:
        with store.transaction() as cur:
            cur.execute(
                """
                INSERT INTO capital_snapshot
                  (id, cash_floor, realized_pnl_today, margin_used, margin_reserved,
                   charges_today, updated_at)
                VALUES (2, ?, ?, ?, ?, ?, ?)
                """,
                (500_000.0, 0.0, 0.0, 0.0, 0.0, "2026-04-14T09:00:00+05:30"),
            )
    except sqlite3.IntegrityError as e:
        raised = True
        print(f"  OK Second-row CHECK violation caught: {e}")
    
    assert raised, "CHECK (id = 1) was not enforced"
    store.close()


def test_thread_safety_concurrent_writes(tmpdir: Path) -> None:
    """
    Two threads writing different signals concurrently must both succeed
    without corruption. Each thread gets its own connection via threading.local.
    """
    store = make_store(tmpdir)
    errors = []
    
    def writer(thread_id: int, count: int) -> None:
        try:
            for i in range(count):
                insert_test_signal(
                    store,
                    signal_id=f"sig-t{thread_id}-{i}",
                    fingerprint=f"fp-t{thread_id}-{i}",
                )
        except Exception as e:
            errors.append((thread_id, str(e)))
    
    t1 = threading.Thread(target=writer, args=(1, 50))
    t2 = threading.Thread(target=writer, args=(2, 50))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    assert not errors, f"Thread errors: {errors}"
    
    count = store.row_count("signals")
    assert count == 100, f"Expected 100 signals from 2 threads, got {count}"
    print(f"  OK 100 concurrent inserts from 2 threads succeeded")
    store.close()


def test_close_then_reopen_preserves_data(tmpdir: Path) -> None:
    """Data must persist across close + reopen of the database."""
    db_path = tmpdir / "persist_test.db"
    
    store1 = StateStore(db_path)
    insert_test_signal(store1, "sig-persist", symbol="INFY")
    store1.close()
    
    store2 = StateStore(db_path)
    row = store2.fetch_one(
        "SELECT * FROM signals WHERE signal_id = ?", ("sig-persist",)
    )
    assert row is not None, "Data lost across reopen"
    assert row["symbol"] == "INFY"
    print(f"  OK Data persisted across close+reopen: {row['signal_id']}")
    store2.close()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for RE15 query-helper tests
# ─────────────────────────────────────────────────────────────────────────────

def insert_test_trade(
    store: StateStore,
    trade_id: str,
    symbol: str = "RELIANCE",
    sector: str = "ENERGY",
    status: str = "OPEN",
    margin_reserved: float = 10_000.0,
    net_pnl: float | None = None,
    created_date: str = "2026-04-14",
    exit_time: str | None = None,
    qty_filled: int | None = None,
) -> None:
    """
    Insert a signal row + trade row into the store.
    The signal FK must exist before the trade can be inserted.
    Unique fingerprint is derived from trade_id to avoid index conflicts.

    B.3 (2026-04-25): qty_filled is now status-aware.
    PENDING_FILL/PENDING_CANCEL -> 0, others -> qty_planned (=10).
    Override via the qty_filled kwarg when a test needs a specific value
    (e.g. testing the qty_filled > 0 filter in get_open_intraday_positions
    with a degenerate OPEN+qty_filled=0 row).
    """
    sig_id = f"sig_{trade_id}"
    created_at = f"{created_date}T09:30:00+05:30"
    if qty_filled is None:
        qty_filled = 0 if status in ("PENDING_FILL", "PENDING_CANCEL") else 10
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals
              (signal_id, symbol, scanner, strategy, triggered_at, received_at,
               expires_at, status, fingerprint, fingerprint_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sig_id, symbol, "SCANNER", "strategy",
                created_at, created_at,
                f"{created_date}T09:35:00+05:30",
                "TRADED", f"fp_{trade_id}", created_date,
            ),
        )
        cur.execute(
            """
            INSERT INTO trades
              (trade_id, signal_id, symbol, direction, strategy, sector,
               qty_planned, qty_filled, entry_target_price, sl_initial,
               tgt_initial, margin_reserved, risk_amount, created_at,
               status, order_protocol, updated_at, net_pnl, exit_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id, sig_id, symbol, "LONG", "strategy", sector,
                10, qty_filled, 2500.0, 2450.0, 2600.0,
                margin_reserved, 500.0, created_at,
                status, "LIMIT_TRIPLE", created_at,
                net_pnl, exit_time,
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# RE15 query helper tests
# ─────────────────────────────────────────────────────────────────────────────

def test_count_open_positions(tmp_path: Path) -> None:
    """count_open_positions() counts OPEN + PARTIAL, ignores PENDING_FILL/CLOSED."""
    store = StateStore(tmp_path / "test.db")
    assert store.count_open_positions() == 0

    insert_test_trade(store, "t1", status="OPEN")
    insert_test_trade(store, "t2", status="PARTIAL")
    insert_test_trade(store, "t3", status="PENDING_FILL")
    insert_test_trade(store, "t4", status="CLOSED", net_pnl=500.0)

    count = store.count_open_positions()
    assert count == 2, f"Expected 2 (OPEN+PARTIAL), got {count}"
    print(f"  OK count_open_positions = {count}")
    store.close()


def test_count_in_flight_orders(tmp_path: Path) -> None:
    """count_in_flight_orders() counts only PENDING_FILL trades."""
    store = StateStore(tmp_path / "test.db")
    assert store.count_in_flight_orders() == 0

    insert_test_trade(store, "t1", status="PENDING_FILL")
    insert_test_trade(store, "t2", status="PENDING_FILL")
    insert_test_trade(store, "t3", status="OPEN")

    count = store.count_in_flight_orders()
    assert count == 2, f"Expected 2 (PENDING_FILL), got {count}"
    print(f"  OK count_in_flight_orders = {count}")
    store.close()


def test_count_trades_today(tmp_path: Path) -> None:
    """count_trades_today() matches on SUBSTR(created_at,1,10) == date_iso."""
    store = StateStore(tmp_path / "test.db")

    insert_test_trade(store, "t1", created_date="2026-04-14")
    insert_test_trade(store, "t2", created_date="2026-04-14")
    insert_test_trade(store, "t3", created_date="2026-04-15")

    assert store.count_trades_today("2026-04-14") == 2
    assert store.count_trades_today("2026-04-15") == 1
    assert store.count_trades_today("2026-04-13") == 0
    print("  OK count_trades_today correct for all three dates")
    store.close()


def test_sector_exposure(tmp_path: Path) -> None:
    """
    sector_exposure() sums margin_reserved for PENDING_FILL/OPEN/PARTIAL
    in the given sector. Audit RE6 fix: counts in-flight + open together.
    CLOSED trades must NOT be counted.
    """
    store = StateStore(tmp_path / "test.db")

    insert_test_trade(store, "t1", sector="ENERGY", status="OPEN", margin_reserved=10_000.0)
    insert_test_trade(store, "t2", sector="ENERGY", status="PENDING_FILL", margin_reserved=8_000.0)
    insert_test_trade(store, "t3", sector="ENERGY", status="CLOSED", margin_reserved=5_000.0, net_pnl=200.0)
    insert_test_trade(store, "t4", sector="IT", status="OPEN", margin_reserved=12_000.0)

    energy = store.sector_exposure("ENERGY")
    it = store.sector_exposure("IT")
    unknown = store.sector_exposure("PHARMA")

    assert energy == 18_000.0, f"Expected 18000 (OPEN+PENDING_FILL), got {energy}"
    assert it == 12_000.0, f"Expected 12000, got {it}"
    assert unknown == 0.0, f"Expected 0 for missing sector, got {unknown}"
    print(f"  OK sector_exposure: ENERGY={energy}, IT={it}, PHARMA={unknown}")
    store.close()


def test_has_active_position(tmp_path: Path) -> None:
    """has_active_position() returns True for PENDING_FILL/OPEN/PARTIAL, False for CLOSED."""
    store = StateStore(tmp_path / "test.db")

    assert not store.has_active_position("RELIANCE")

    insert_test_trade(store, "t1", symbol="RELIANCE", status="OPEN")
    insert_test_trade(store, "t2", symbol="TCS", status="CLOSED", net_pnl=100.0)
    insert_test_trade(store, "t3", symbol="INFY", status="PENDING_FILL")

    assert store.has_active_position("RELIANCE"), "RELIANCE OPEN should be active"
    assert not store.has_active_position("TCS"), "TCS CLOSED should NOT be active"
    assert store.has_active_position("INFY"), "INFY PENDING_FILL should be active"
    assert not store.has_active_position("WIPRO"), "WIPRO not in DB should be False"
    print("  OK has_active_position correct for all cases")
    store.close()


def test_kill_switch_state_table_exists(tmp_path: Path) -> None:
    """kill_switch_state table must exist (KS9 schema addition)."""
    store = StateStore(tmp_path / "test.db")
    assert "kill_switch_state" in store.table_names()
    print("  OK kill_switch_state table exists in schema v3")
    store.close()


def test_kill_switch_state_single_row_constraint(tmp_path: Path) -> None:
    """kill_switch_state CHECK (id=1) enforces single-row invariant (KS9)."""
    store = StateStore(tmp_path / "test.db")

    # Insert id=1 row
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO kill_switch_state (id, state, reason, triggered_at, triggered_by)
            VALUES (1, 'INACTIVE', 'startup', '2026-04-14T09:00:00+05:30', 'system')
            """
        )

    # Second insert with id=2 must fail CHECK constraint
    raised = False
    try:
        with store.transaction() as cur:
            cur.execute(
                """
                INSERT INTO kill_switch_state (id, state, reason, triggered_at, triggered_by)
                VALUES (2, 'SOFT_KILL', 'test', '2026-04-14T09:01:00+05:30', 'test')
                """
            )
    except sqlite3.IntegrityError as e:
        raised = True
        print(f"  OK CHECK (id=1) caught second row: {e}")

    assert raised, "CHECK (id=1) was not enforced on kill_switch_state"
    store.close()


def test_kill_switch_state_insert_and_read(tmp_path: Path) -> None:
    """INSERT OR REPLACE round-trip on kill_switch_state (KS9, KS3)."""
    store = StateStore(tmp_path / "test.db")

    # Write SOFT_KILL state
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT OR REPLACE INTO kill_switch_state
              (id, state, reason, triggered_at, triggered_by)
            VALUES (1, ?, ?, ?, ?)
            """,
            ("SOFT_KILL", "daily loss limit", "2026-04-14T10:30:00+05:30", "fund_manager"),
        )

    row = store.fetch_one("SELECT * FROM kill_switch_state WHERE id = 1")
    assert row is not None, "kill_switch_state row not found after insert"
    assert row["state"] == "SOFT_KILL"
    assert row["reason"] == "daily loss limit"
    assert row["triggered_by"] == "fund_manager"

    # OR REPLACE overwrites with HARD_KILL
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT OR REPLACE INTO kill_switch_state
              (id, state, reason, triggered_at, triggered_by)
            VALUES (1, ?, ?, ?, ?)
            """,
            ("HARD_KILL", "api_timeout", "2026-04-14T10:35:00+05:30", "zerodha_adapter"),
        )

    row2 = store.fetch_one("SELECT * FROM kill_switch_state WHERE id = 1")
    assert row2["state"] == "HARD_KILL"
    assert row2["triggered_by"] == "zerodha_adapter"
    print(f"  OK kill_switch_state round-trip: SOFT_KILL -> HARD_KILL via OR REPLACE")
    store.close()


def test_update_signal_status(tmp_path: Path) -> None:
    """update_signal_status() writes new status + reason to signals table (SP9)."""
    store = StateStore(tmp_path / "test.db")

    insert_test_signal(store, "sig-upd-1", status="QUEUED")

    store.update_signal_status("sig-upd-1", "PROCESSING", "", "2026-04-15T10:00:00+05:30")
    row = store.fetch_one("SELECT status, rejection_reason FROM signals WHERE signal_id = ?",
                          ("sig-upd-1",))
    assert row["status"] == "PROCESSING"
    assert row["rejection_reason"] is None

    store.update_signal_status("sig-upd-1", "REJECTED_EXPIRED", "signal too old")
    row2 = store.fetch_one("SELECT status, rejection_reason FROM signals WHERE signal_id = ?",
                           ("sig-upd-1",))
    assert row2["status"] == "REJECTED_EXPIRED"
    assert row2["rejection_reason"] == "signal too old"

    # No-op for missing signal_id must not raise
    store.update_signal_status("sig-nonexistent", "PROCESSED")

    print("  OK update_signal_status: QUEUED -> PROCESSING -> REJECTED_EXPIRED, no-op ok")
    store.close()


def test_webhook_audit_insert_and_query(tmp_path: Path) -> None:
    """webhook_audit rows can be inserted and queried (WR13)."""
    store = StateStore(tmp_path / "test.db")

    assert "webhook_audit" in store.table_names()

    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO webhook_audit
              (ts, scanner_name, source_ip, payload_size_bytes,
               response_code, signals_accepted, signals_rejected, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-04-15T10:15:00+05:30", "gap_go_long", "127.0.0.1",
             256, 200, 2, 1, 12),
        )

    row = store.fetch_one(
        "SELECT * FROM webhook_audit WHERE scanner_name = ?", ("gap_go_long",)
    )
    assert row is not None, "webhook_audit row not found"
    assert row["response_code"] == 200
    assert row["signals_accepted"] == 2
    assert row["signals_rejected"] == 1
    assert row["duration_ms"] == 12
    print(f"  OK webhook_audit insert+query: {dict(row)}")
    store.close()


def insert_test_order(
    store: StateStore,
    order_id: str,
    trade_id: str,
    leg: str = "ENTRY",
    product: str = "MIS",
    status: str = "PENDING",
    qty: int = 10,
    variety: str = "regular",
) -> None:
    """Insert an orders row. trade_id must already exist."""
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO orders
              (order_id, trade_id, leg, transaction_type, order_type,
               product, variety, qty_requested, status, placed_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, trade_id, leg, "BUY", "LIMIT",
             product, variety, qty, status,
             "2026-04-15T09:30:00+05:30", "2026-04-15T09:30:00+05:30"),
        )


def test_recent_trade_pnls(tmp_path: Path) -> None:
    """
    recent_trade_pnls(n) returns n most-recent CLOSED trade pnls, newest first.
    Excludes NULL pnl rows and non-CLOSED trades.
    """
    store = StateStore(tmp_path / "test.db")

    assert store.recent_trade_pnls(5) == []

    insert_test_trade(store, "t1", status="CLOSED", net_pnl=500.0,  exit_time="2026-04-14T10:00:00+05:30")
    insert_test_trade(store, "t2", status="CLOSED", net_pnl=-300.0, exit_time="2026-04-14T11:00:00+05:30")
    insert_test_trade(store, "t3", status="CLOSED", net_pnl=-200.0, exit_time="2026-04-14T12:00:00+05:30")
    insert_test_trade(store, "t4", status="OPEN")  # not CLOSED, must be excluded
    insert_test_trade(store, "t5", status="CLOSED", net_pnl=None, exit_time="2026-04-14T13:00:00+05:30")  # NULL pnl

    pnls = store.recent_trade_pnls(10)
    assert len(pnls) == 3, f"Expected 3 (NULL and OPEN excluded), got {len(pnls)}"
    assert pnls[0] == -200.0, f"Most recent first; expected -200.0, got {pnls[0]}"
    assert pnls[1] == -300.0
    assert pnls[2] == 500.0

    # limit respected
    pnls2 = store.recent_trade_pnls(2)
    assert len(pnls2) == 2
    assert pnls2[0] == -200.0
    print(f"  OK recent_trade_pnls: {pnls} (limit=2: {pnls2})")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# EOD square-off query helper tests (EOD8, EOD9)
# ─────────────────────────────────────────────────────────────────────────────

def test_eod_squareoff_log_table_exists(tmp_path: Path) -> None:
    """eod_squareoff_log table must exist in schema v5 (EOD8)."""
    store = StateStore(tmp_path / "test.db")
    assert "eod_squareoff_log" in store.table_names()
    print("  OK eod_squareoff_log table exists in schema v5")
    store.close()


def test_get_pending_intraday_orders_empty(tmp_path: Path) -> None:
    """get_pending_intraday_orders() returns empty list when no data."""
    store = StateStore(tmp_path / "test.db")
    rows = store.get_pending_intraday_orders()
    assert rows == [], f"Expected [], got {rows}"
    print("  OK get_pending_intraday_orders returns [] when empty")
    store.close()


def test_get_pending_intraday_orders_mis_co_only(tmp_path: Path) -> None:
    """get_pending_intraday_orders() returns MIS and CO, excludes CNC (EOD5)."""
    store = StateStore(tmp_path / "test.db")

    # PENDING_FILL + MIS → included
    insert_test_trade(store, "t_mis", symbol="RELIANCE", status="PENDING_FILL")
    insert_test_order(store, "ord_mis", "t_mis", leg="ENTRY", product="MIS")

    # PENDING_FILL + CO → included
    insert_test_trade(store, "t_co", symbol="INFY", status="PENDING_FILL")
    insert_test_order(store, "ord_co", "t_co", leg="ENTRY", product="CO")

    # PENDING_FILL + CNC → excluded
    insert_test_trade(store, "t_cnc", symbol="TCS", status="PENDING_FILL")
    insert_test_order(store, "ord_cnc", "t_cnc", leg="ENTRY", product="CNC")

    # OPEN + MIS → excluded (not PENDING_FILL)
    insert_test_trade(store, "t_open", symbol="WIPRO", status="OPEN")
    insert_test_order(store, "ord_open", "t_open", leg="ENTRY", product="MIS")

    rows = store.get_pending_intraday_orders()
    assert len(rows) == 2, f"Expected 2 rows (MIS+CO), got {len(rows)}: {[dict(r) for r in rows]}"
    symbols = {r["symbol"] for r in rows}
    assert symbols == {"RELIANCE", "INFY"}, f"Expected RELIANCE+INFY, got {symbols}"
    broker_ids = {r["broker_order_id"] for r in rows}
    assert broker_ids == {"ord_mis", "ord_co"}
    print(f"  OK get_pending_intraday_orders: {len(rows)} rows (MIS+CO only)")
    store.close()


def test_get_open_intraday_positions_empty(tmp_path: Path) -> None:
    """get_open_intraday_positions() returns empty list when no data."""
    store = StateStore(tmp_path / "test.db")
    rows = store.get_open_intraday_positions()
    assert rows == [], f"Expected [], got {rows}"
    print("  OK get_open_intraday_positions returns [] when empty")
    store.close()


def test_get_open_intraday_positions_open_partial_only(tmp_path: Path) -> None:
    """get_open_intraday_positions() returns OPEN+PARTIAL with MIS/CO; excludes CLOSED+CNC (EOD5)."""
    store = StateStore(tmp_path / "test.db")

    # OPEN + MIS → included
    insert_test_trade(store, "t_open_mis", symbol="RELIANCE", status="OPEN")
    insert_test_order(store, "ord1", "t_open_mis", leg="ENTRY", product="MIS")

    # PARTIAL + CO → included
    insert_test_trade(store, "t_partial_co", symbol="INFY", status="PARTIAL")
    insert_test_order(store, "ord2", "t_partial_co", leg="ENTRY", product="CO")

    # CLOSED + MIS → excluded
    insert_test_trade(store, "t_closed", symbol="TCS", status="CLOSED", net_pnl=500.0)
    insert_test_order(store, "ord3", "t_closed", leg="ENTRY", product="MIS")

    # OPEN + CNC → excluded (delivery)
    insert_test_trade(store, "t_cnc", symbol="WIPRO", status="OPEN")
    insert_test_order(store, "ord4", "t_cnc", leg="ENTRY", product="CNC")

    # PENDING_FILL + MIS → excluded (not OPEN/PARTIAL)
    insert_test_trade(store, "t_pending", symbol="HDFCBANK", status="PENDING_FILL")
    insert_test_order(store, "ord5", "t_pending", leg="ENTRY", product="MIS")

    rows = store.get_open_intraday_positions()
    assert len(rows) == 2, f"Expected 2 rows (OPEN+PARTIAL, MIS+CO), got {len(rows)}: {[dict(r) for r in rows]}"
    symbols = {r["symbol"] for r in rows}
    assert symbols == {"RELIANCE", "INFY"}, f"Expected RELIANCE+INFY, got {symbols}"
    print(f"  OK get_open_intraday_positions: {len(rows)} rows (OPEN+PARTIAL, MIS+CO only)")
    store.close()


def test_b3_get_open_intraday_positions_excludes_qty_filled_zero(tmp_path: Path) -> None:
    """B.3 (2026-04-25): an OPEN trade with qty_filled=0 (race window /
    recovery edge case) is not a real position. Excluded so EOD does not
    attempt a MARKET reverse on a zero-qty row."""
    store = StateStore(tmp_path / "test.db")

    # OPEN + MIS + qty_filled=10 -> included
    insert_test_trade(
        store, "t_real", symbol="RELIANCE", status="OPEN", qty_filled=10,
    )
    insert_test_order(store, "ord_real", "t_real", leg="ENTRY", product="MIS")

    # OPEN + MIS + qty_filled=0 -> excluded by B.3 filter
    insert_test_trade(
        store, "t_phantom", symbol="INFY", status="OPEN", qty_filled=0,
    )
    insert_test_order(store, "ord_phantom", "t_phantom", leg="ENTRY", product="MIS")

    rows = store.get_open_intraday_positions()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "RELIANCE"
    assert rows[0]["qty_filled"] == 10
    print("  OK B.3: phantom OPEN+qty_filled=0 trade excluded")
    store.close()


def test_get_reservation_id_for_signal(tmp_path: Path) -> None:
    """get_reservation_id_for_signal() returns None if absent, reservation_id if present (EOD5)."""
    store = StateStore(tmp_path / "test.db")

    # Missing signal → None
    result = store.get_reservation_id_for_signal("sig_not_found")
    assert result is None, f"Expected None, got {result!r}"

    # Insert a fm_ledger RESERVE row (balance_before/after required NOT NULL)
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO fm_ledger
              (ts, entry_type, amount, bucket, balance_before, balance_after,
               signal_id, reservation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-04-15T09:30:00+05:30", "RESERVE", 10000.0,
             "intraday", 100000.0, 90000.0, "sig_abc123", "res_xyz789"),
        )

    result2 = store.get_reservation_id_for_signal("sig_abc123")
    assert result2 == "res_xyz789", f"Expected 'res_xyz789', got {result2!r}"

    # Non-RESERVE rows (reservation_id=NULL) should not be returned
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO fm_ledger
              (ts, entry_type, amount, bucket, balance_before, balance_after,
               signal_id, reservation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-04-15T09:31:00+05:30", "RELEASE", -10000.0,
             "intraday", 90000.0, 100000.0, "sig_no_reserve", None),
        )

    result3 = store.get_reservation_id_for_signal("sig_no_reserve")
    assert result3 is None, f"RELEASE row (no reservation_id) should return None, got {result3!r}"
    print("  OK get_reservation_id_for_signal: None/value/RELEASE-excluded all correct")
    store.close()


def test_get_eod_squareoff_log_returns_none_when_absent(tmp_path: Path) -> None:
    """get_eod_squareoff_log_for_date() returns None when no row exists (EOD9)."""
    store = StateStore(tmp_path / "test.db")
    row = store.get_eod_squareoff_log_for_date("2026-04-15")
    assert row is None, f"Expected None, got {row}"
    print("  OK get_eod_squareoff_log_for_date returns None when absent")
    store.close()


def test_insert_and_get_eod_squareoff_log(tmp_path: Path) -> None:
    """insert_eod_squareoff_log + get_eod_squareoff_log_for_date round-trip (EOD8)."""
    store = StateStore(tmp_path / "test.db")

    store.insert_eod_squareoff_log(
        fired_date="2026-04-15",
        fired_at="2026-04-15T15:17:00+05:30",
        positions_attempted=3,
        positions_succeeded=3,
        positions_failed=0,
        cancels_attempted=2,
        cancels_succeeded=2,
        cancels_failed=0,
        duration_sec=1.42,
    )

    row = store.get_eod_squareoff_log_for_date("2026-04-15")
    assert row is not None, "Row not found after insert"
    assert row["fired_date"] == "2026-04-15"
    assert row["fired_at"] == "2026-04-15T15:17:00+05:30"
    assert row["positions_attempted"] == 3
    assert row["positions_succeeded"] == 3
    assert row["positions_failed"] == 0
    assert row["cancels_attempted"] == 2
    assert row["cancels_succeeded"] == 2
    assert row["cancels_failed"] == 0
    assert abs(row["duration_sec"] - 1.42) < 0.001

    # Different date → still None
    row2 = store.get_eod_squareoff_log_for_date("2026-04-14")
    assert row2 is None, f"Different date should return None, got {row2}"
    print(f"  OK insert+get eod_squareoff_log round-trip verified (fired_date=2026-04-15)")
    store.close()


def test_insert_eod_squareoff_log_or_replace(tmp_path: Path) -> None:
    """INSERT OR REPLACE on same fired_date must overwrite the earlier row (EOD9 recovery)."""
    store = StateStore(tmp_path / "test.db")

    store.insert_eod_squareoff_log(
        fired_date="2026-04-15",
        fired_at="2026-04-15T15:17:00+05:30",
        positions_attempted=3,
        positions_succeeded=1,
        positions_failed=2,
        cancels_attempted=0,
        cancels_succeeded=0,
        cancels_failed=0,
        duration_sec=0.5,
    )

    # Recovery fire overwrites
    store.insert_eod_squareoff_log(
        fired_date="2026-04-15",
        fired_at="2026-04-15T15:19:00+05:30",
        positions_attempted=2,
        positions_succeeded=2,
        positions_failed=0,
        cancels_attempted=0,
        cancels_succeeded=0,
        cancels_failed=0,
        duration_sec=0.3,
    )

    row = store.get_eod_squareoff_log_for_date("2026-04-15")
    assert row is not None
    assert row["fired_at"] == "2026-04-15T15:19:00+05:30", "OR REPLACE must overwrite fired_at"
    assert row["positions_succeeded"] == 2, "OR REPLACE must overwrite positions_succeeded"
    assert row["positions_failed"] == 0, "OR REPLACE must overwrite positions_failed"

    # Verify only one row exists for this date
    count = store.row_count("eod_squareoff_log")
    assert count == 1, f"Expected 1 row (OR REPLACE), got {count}"
    print("  OK insert_eod_squareoff_log OR REPLACE overwrites on same fired_date (EOD9)")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# RC10 reconciliation_log table + RC-helper query tests (Module 25)
# ─────────────────────────────────────────────────────────────────────────────

def test_reconciliation_log_table_exists(tmp_path: Path) -> None:
    """reconciliation_log table must exist in schema v6 (RC10)."""
    store = StateStore(tmp_path / "test.db")
    assert "reconciliation_log" in store.table_names()
    print("  OK reconciliation_log table exists in schema v6")
    store.close()


def test_get_all_open_trades_empty(tmp_path: Path) -> None:
    """get_all_open_trades() returns [] when no OPEN/PARTIAL trades exist."""
    store = StateStore(tmp_path / "test.db")
    rows = store.get_all_open_trades()
    assert rows == [], f"Expected [], got {rows}"
    print("  OK get_all_open_trades returns [] when empty")
    store.close()


def test_get_all_open_trades_returns_open_partial(tmp_path: Path) -> None:
    """get_all_open_trades() includes OPEN and PARTIAL; excludes CLOSED/PENDING_FILL."""
    store = StateStore(tmp_path / "test.db")

    insert_test_trade(store, "t_open",    symbol="INFY",     status="OPEN")
    insert_test_trade(store, "t_partial", symbol="RELIANCE", status="PARTIAL")
    insert_test_trade(store, "t_closed",  symbol="TCS",      status="CLOSED", net_pnl=100.0)
    insert_test_trade(store, "t_pending", symbol="WIPRO",    status="PENDING_FILL")

    rows = store.get_all_open_trades()
    ids = {r["trade_id"] for r in rows}
    assert "t_open"    in ids, "OPEN trade must be included"
    assert "t_partial" in ids, "PARTIAL trade must be included"
    assert "t_closed"  not in ids, "CLOSED trade must be excluded"
    assert "t_pending" not in ids, "PENDING_FILL trade must be excluded"
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    print(f"  OK get_all_open_trades returns OPEN+PARTIAL only: {ids}")
    store.close()


def test_get_orders_for_trade(tmp_path: Path) -> None:
    """get_orders_for_trade() returns all orders for a trade, sorted by leg."""
    store = StateStore(tmp_path / "test.db")

    insert_test_trade(store, "t1", status="OPEN")
    insert_test_order(store, "ord_entry", "t1", leg="ENTRY", status="COMPLETE")
    insert_test_order(store, "ord_sl",    "t1", leg="SL",    status="TRIGGER_PENDING")
    insert_test_order(store, "ord_tgt",   "t1", leg="TGT",   status="PENDING")

    # Insert order for a different trade to verify isolation
    insert_test_trade(store, "t2", symbol="INFY", status="OPEN")
    insert_test_order(store, "ord_other", "t2", leg="ENTRY", status="PENDING")

    rows = store.get_orders_for_trade("t1")
    assert len(rows) == 3, f"Expected 3 orders for t1, got {len(rows)}"
    legs = [r["leg"] for r in rows]
    assert "ENTRY" in legs
    assert "SL"    in legs
    assert "TGT"   in legs

    rows_t2 = store.get_orders_for_trade("t2")
    assert len(rows_t2) == 1, f"Expected 1 order for t2, got {len(rows_t2)}"

    rows_none = store.get_orders_for_trade("nonexistent")
    assert rows_none == [], f"Expected [] for unknown trade_id, got {rows_none}"

    print(f"  OK get_orders_for_trade: t1={legs}, t2 isolated, unknown=[]")
    store.close()


def test_get_sl_order_for_trade_active(tmp_path: Path) -> None:
    """get_sl_order_for_trade() returns active SL order row (not terminal status)."""
    store = StateStore(tmp_path / "test.db")

    insert_test_trade(store, "t1", status="OPEN")
    insert_test_order(store, "ord_entry", "t1", leg="ENTRY",  status="COMPLETE")
    insert_test_order(store, "ord_sl",    "t1", leg="SL",     status="TRIGGER_PENDING")

    row = store.get_sl_order_for_trade("t1")
    assert row is not None, "Expected active SL order row"
    assert row["order_id"] == "ord_sl"
    assert row["qty_requested"] == 10
    print(f"  OK get_sl_order_for_trade returns active SL: {row['order_id']}")
    store.close()


def test_get_sl_order_for_trade_none_when_absent(tmp_path: Path) -> None:
    """get_sl_order_for_trade() returns None when SL is terminal or absent."""
    store = StateStore(tmp_path / "test.db")

    insert_test_trade(store, "t1", status="OPEN")
    insert_test_order(store, "ord_entry",    "t1", leg="ENTRY", status="COMPLETE")
    # SL order that was cancelled (terminal) — must be excluded
    insert_test_order(store, "ord_sl_done",  "t1", leg="SL",    status="CANCELLED")

    row = store.get_sl_order_for_trade("t1")
    assert row is None, f"Expected None for terminal SL, got {row}"

    # No trade at all
    row2 = store.get_sl_order_for_trade("nonexistent")
    assert row2 is None, "Expected None for unknown trade_id"
    print("  OK get_sl_order_for_trade returns None for terminal/absent SL")
    store.close()


def test_mark_trade_manually_closed(tmp_path: Path) -> None:
    """mark_trade_manually_closed() sets status=CLOSED_MANUAL, exit_reason=MANUAL."""
    store = StateStore(tmp_path / "test.db")

    insert_test_trade(store, "t1", status="OPEN")

    store.mark_trade_manually_closed("t1")

    row = store.fetch_one("SELECT status, exit_reason FROM trades WHERE trade_id=?", ("t1",))
    assert row is not None
    assert row["status"] == "CLOSED_MANUAL", f"Expected CLOSED_MANUAL, got {row['status']}"
    assert row["exit_reason"] == "MANUAL", f"Expected MANUAL, got {row['exit_reason']}"
    print(f"  OK mark_trade_manually_closed: status={row['status']}, exit_reason={row['exit_reason']}")
    store.close()


def test_get_pending_all_products(tmp_path: Path) -> None:
    """get_pending_all_products() returns PENDING_FILL for ALL products (MIS, CO, CNC)."""
    store = StateStore(tmp_path / "test.db")

    assert store.get_pending_all_products() == [], "Expected [] when empty"

    # PENDING_FILL + MIS
    insert_test_trade(store, "t_mis", symbol="RELIANCE", status="PENDING_FILL")
    insert_test_order(store, "ord_mis", "t_mis", leg="ENTRY", product="MIS")

    # PENDING_FILL + CNC (included — unlike get_pending_intraday_orders)
    insert_test_trade(store, "t_cnc", symbol="INFY", status="PENDING_FILL")
    insert_test_order(store, "ord_cnc", "t_cnc", leg="ENTRY", product="CNC")

    # OPEN + MIS — excluded (not PENDING_FILL)
    insert_test_trade(store, "t_open", symbol="TCS", status="OPEN")
    insert_test_order(store, "ord_open", "t_open", leg="ENTRY", product="MIS")

    rows = store.get_pending_all_products()
    ids = {r["trade_id"] for r in rows}
    assert "t_mis" in ids, "MIS PENDING_FILL must be included"
    assert "t_cnc" in ids, "CNC PENDING_FILL must be included"
    assert "t_open" not in ids, "OPEN trade must be excluded"
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    print(f"  OK get_pending_all_products returns all products: {ids}")
    store.close()


def test_insert_reconciliation_log(tmp_path: Path) -> None:
    """insert_reconciliation_log() persists one RC action row (RC10)."""
    store = StateStore(tmp_path / "test.db")

    store.insert_reconciliation_log(
        ts="2026-04-16T10:00:00+05:30",
        check_name="MANUAL_CLOSE",
        tier="RECOVERABLE",
        symbol="RELIANCE",
        trade_id="t_rc_1",
        description="Position gone at broker; local trade still OPEN",
        action_taken="Marked CLOSED_MANUAL",
        success=True,
    )

    row = store.fetch_one(
        "SELECT * FROM reconciliation_log WHERE trade_id=?", ("t_rc_1",)
    )
    assert row is not None, "Expected row in reconciliation_log"
    assert row["check_name"] == "MANUAL_CLOSE"
    assert row["tier"] == "RECOVERABLE"
    assert row["symbol"] == "RELIANCE"
    assert row["success"] == 1, f"Expected success=1, got {row['success']}"

    # NULL trade_id must also be accepted (orphan/capital checks have no trade)
    store.insert_reconciliation_log(
        ts="2026-04-16T10:01:00+05:30",
        check_name="CAPITAL_DRIFT",
        tier="UNRECOVERABLE",
        symbol="",
        trade_id=None,
        description="Capital drift exceeds tolerance",
        action_taken="soft_kill triggered",
        success=True,
    )
    count = store.row_count("reconciliation_log")
    assert count == 2, f"Expected 2 rows, got {count}"
    print(f"  OK insert_reconciliation_log: 2 rows persisted (with + without trade_id)")
    store.close()


def test_screener_results_table_exists(tmp_path: Path) -> None:
    """screener_results table must exist in schema v7 (SS6)."""
    store = StateStore(tmp_path / "test.db")
    assert "screener_results" in store.table_names()
    print("  OK screener_results table exists in schema v7")
    store.close()


def test_insert_screener_result(tmp_path: Path) -> None:
    """insert_screener_result() persists one screening decision row (SS5, SS6)."""
    import json
    store = StateStore(tmp_path / "test.db")

    store.insert_screener_result(
        signal_id="sig_001",
        score=75,
        tier="MEDIUM",
        status="PASSED",
        step_results_json=json.dumps({"volume_surge": 1.0, "vwap_position": 0.0}),
        latencies_json=json.dumps({"volume_surge": 0.5, "vwap_position": 0.3}),
        market_data_snapshot_json=json.dumps({"ltp": 2510.0, "vwap": 2490.0}),
        ts="2026-04-16T10:00:00+05:30",
    )

    row = store.fetch_one(
        "SELECT * FROM screener_results WHERE signal_id=?", ("sig_001",)
    )
    assert row is not None
    assert row["score"] == 75
    assert row["tier"] == "MEDIUM"
    assert row["status"] == "PASSED"
    assert row["signal_id"] == "sig_001"
    assert row["ts"] == "2026-04-16T10:00:00+05:30"
    # JSON fields round-trip
    sr = json.loads(row["step_results"])
    assert sr["volume_surge"] == 1.0
    md = json.loads(row["market_data_snapshot"])
    assert md["ltp"] == 2510.0
    print("  OK insert_screener_result: row persisted with correct fields")
    store.close()


def test_insert_screener_result_multiple_rows(tmp_path: Path) -> None:
    """Multiple screening rows for same signal_id are allowed (many per signal)."""
    import json
    store = StateStore(tmp_path / "test.db")
    for i in range(3):
        store.insert_screener_result(
            signal_id="sig_multi",
            score=50 + i * 10,
            tier="LOW",
            status="REJECTED_volume_surge",
            step_results_json=json.dumps({}),
            latencies_json=json.dumps({}),
            market_data_snapshot_json=json.dumps({}),
            ts=f"2026-04-16T10:0{i}:00+05:30",
        )
    count = store.row_count("screener_results")
    assert count == 3, f"Expected 3 rows, got {count}"
    print("  OK insert_screener_result: 3 rows for same signal_id allowed")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# ST14 + ST15: CO entry order lookup + smart_tgt_state table tests (Module 32)
# ─────────────────────────────────────────────────────────────────────────────

def test_smart_tgt_state_table_exists(tmp_path: Path) -> None:
    """smart_tgt_state table must exist in schema v8 (ST15)."""
    store = StateStore(tmp_path / "test.db")
    assert "smart_tgt_state" in store.table_names()
    print("  OK smart_tgt_state table exists in schema v8")
    store.close()


def test_get_co_entry_order_returns_co_row(tmp_path: Path) -> None:
    """get_co_entry_order_for_trade() returns the CO ENTRY order row (ST14)."""
    store = StateStore(tmp_path / "test.db")
    insert_test_trade(store, "t_co", symbol="RELIANCE", status="OPEN")
    insert_test_order(store, "co_broker_001", "t_co", leg="ENTRY",
                      product="CO", variety="co")

    row = store.get_co_entry_order_for_trade("t_co")
    assert row is not None, "Expected CO row, got None"
    assert row["order_id"] == "co_broker_001"
    assert row["leg"] == "ENTRY"
    assert row["variety"] == "co"
    print(f"  OK get_co_entry_order_for_trade returns CO row: {row['order_id']}")
    store.close()


def test_get_co_entry_order_excludes_non_co_entry(tmp_path: Path) -> None:
    """get_co_entry_order_for_trade() does not return regular ENTRY orders (ST14)."""
    store = StateStore(tmp_path / "test.db")
    insert_test_trade(store, "t_mis", symbol="INFY", status="OPEN")
    insert_test_order(store, "mis_broker_001", "t_mis", leg="ENTRY",
                      product="MIS", variety="regular")

    row = store.get_co_entry_order_for_trade("t_mis")
    assert row is None, f"Expected None for non-CO order, got {row}"
    print("  OK get_co_entry_order_for_trade excludes regular variety")
    store.close()


def test_get_co_entry_order_returns_none_when_absent(tmp_path: Path) -> None:
    """get_co_entry_order_for_trade() returns None when no matching row (ST14)."""
    store = StateStore(tmp_path / "test.db")
    row = store.get_co_entry_order_for_trade("nonexistent_trade")
    assert row is None, f"Expected None, got {row}"
    print("  OK get_co_entry_order_for_trade returns None when absent")
    store.close()


def test_insert_smart_tgt_state_and_get_all(tmp_path: Path) -> None:
    """insert_smart_tgt_state + get_all_smart_tgt_states round-trip (ST15)."""
    store = StateStore(tmp_path / "test.db")

    assert store.get_all_smart_tgt_states() == []

    store.insert_smart_tgt_state(
        trade_id="t_st_1",
        symbol="RELIANCE",
        instrument_token=738561,
        direction="LONG",
        entry_price=2500.0,
        initial_sl=2450.0,
        current_sl=2450.0,
        qty=10,
        trigger_pct=0.005,
        step_pct=0.003,
        registered_at="2026-04-16T09:35:00+05:30",
    )

    rows = store.get_all_smart_tgt_states()
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
    row = rows[0]
    assert row["trade_id"] == "t_st_1"
    assert row["symbol"] == "RELIANCE"
    assert row["direction"] == "LONG"
    assert abs(row["entry_price"] - 2500.0) < 0.001
    assert abs(row["current_sl"] - 2450.0) < 0.001
    assert row["trail_count"] == 0
    assert row["best_price"] is None
    assert row["last_trail_ts"] is None
    print("  OK insert_smart_tgt_state + get_all round-trip verified")
    store.close()


def test_update_smart_tgt_state(tmp_path: Path) -> None:
    """update_smart_tgt_state() updates current_sl, trail_count, best_price (ST15)."""
    store = StateStore(tmp_path / "test.db")

    store.insert_smart_tgt_state(
        trade_id="t_upd",
        symbol="TCS",
        instrument_token=2953217,
        direction="LONG",
        entry_price=3000.0,
        initial_sl=2940.0,
        current_sl=2940.0,
        qty=5,
        trigger_pct=0.005,
        step_pct=0.003,
        registered_at="2026-04-16T09:40:00+05:30",
    )

    store.update_smart_tgt_state(
        trade_id="t_upd",
        current_sl=2960.0,
        trail_count=1,
        last_trail_ts="2026-04-16T10:00:00+05:30",
        best_price=3025.0,
    )

    rows = store.get_all_smart_tgt_states()
    assert len(rows) == 1
    row = rows[0]
    assert abs(row["current_sl"] - 2960.0) < 0.001
    assert row["trail_count"] == 1
    assert row["last_trail_ts"] == "2026-04-16T10:00:00+05:30"
    assert abs(row["best_price"] - 3025.0) < 0.001
    print(f"  OK update_smart_tgt_state: current_sl={row['current_sl']}, trail_count={row['trail_count']}")
    store.close()


def test_delete_smart_tgt_state(tmp_path: Path) -> None:
    """delete_smart_tgt_state() removes the row; idempotent on re-delete (ST15)."""
    store = StateStore(tmp_path / "test.db")

    store.insert_smart_tgt_state(
        trade_id="t_del",
        symbol="INFY",
        instrument_token=408065,
        direction="SHORT",
        entry_price=1800.0,
        initial_sl=1836.0,
        current_sl=1836.0,
        qty=8,
        trigger_pct=0.005,
        step_pct=0.003,
        registered_at="2026-04-16T09:45:00+05:30",
    )

    assert len(store.get_all_smart_tgt_states()) == 1, "Row not inserted"

    store.delete_smart_tgt_state("t_del")
    assert len(store.get_all_smart_tgt_states()) == 0, "Row not deleted"

    # Idempotent: no exception on re-delete
    store.delete_smart_tgt_state("t_del")
    store.delete_smart_tgt_state("nonexistent")
    print("  OK delete_smart_tgt_state: row removed, re-delete is no-op")
    store.close()


def test_get_all_smart_tgt_states_multiple_rows(tmp_path: Path) -> None:
    """get_all_smart_tgt_states() returns all rows for multiple trades (ST15)."""
    store = StateStore(tmp_path / "test.db")

    for i in range(3):
        store.insert_smart_tgt_state(
            trade_id=f"t_multi_{i}",
            symbol=f"SYM_{i}",
            instrument_token=100000 + i,
            direction="LONG",
            entry_price=1000.0 + i * 100,
            initial_sl=980.0 + i * 100,
            current_sl=980.0 + i * 100,
            qty=10,
            trigger_pct=0.005,
            step_pct=0.003,
            registered_at="2026-04-16T09:30:00+05:30",
        )

    rows = store.get_all_smart_tgt_states()
    assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}"
    ids = {row["trade_id"] for row in rows}
    assert ids == {"t_multi_0", "t_multi_1", "t_multi_2"}
    print(f"  OK get_all_smart_tgt_states: {len(rows)} rows returned")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# SC4/SC6 session + system_events helpers (startup_checks support)
# ─────────────────────────────────────────────────────────────────────────────

def test_get_session_row_returns_none_when_no_session(tmp_path: Path) -> None:
    """get_session_row returns None when session table is empty (SC4)."""
    store = StateStore(tmp_path / "test.db")
    row = store.get_session_row()
    assert row is None, f"Expected None, got {row}"
    print("  OK get_session_row: None when no session")
    store.close()


def test_get_session_row_returns_row(tmp_path: Path) -> None:
    """get_session_row returns the single session row when present (SC4)."""
    store = StateStore(tmp_path / "test.db")
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO session (id, session_date, account_id, broker, mode,
                trade_type, session_start, last_updated)
            VALUES (1, '2026-04-16', 'ACC123', 'zerodha', 'PAPER',
                'INTRADAY', '2026-04-16T09:00:00+05:30',
                '2026-04-16T09:00:00+05:30')
            """
        )
    row = store.get_session_row()
    assert row is not None, "Expected session row"
    assert row["session_date"] == "2026-04-16"
    assert row["mode"] == "PAPER"
    print("  OK get_session_row: returns populated row")
    store.close()


def test_find_shutdown_event_missing(tmp_path: Path) -> None:
    """find_shutdown_event_for_date returns None when no SHUTDOWN event exists."""
    store = StateStore(tmp_path / "test.db")
    row = store.find_shutdown_event_for_date("2026-04-16")
    assert row is None, f"Expected None, got {row}"
    print("  OK find_shutdown_event: None when absent")
    store.close()


def test_find_shutdown_event_found(tmp_path: Path) -> None:
    """find_shutdown_event_for_date returns the matching SHUTDOWN row."""
    store = StateStore(tmp_path / "test.db")
    store.insert_system_event(
        event_type="SHUTDOWN",
        timestamp="2026-04-16T15:30:00+05:30",
        scenario=None,
        details=None,
    )
    row = store.find_shutdown_event_for_date("2026-04-16")
    assert row is not None, "Expected SHUTDOWN row"
    assert row["event_type"] == "SHUTDOWN"
    assert "2026-04-16" in row["timestamp"]
    print("  OK find_shutdown_event: row found by date")
    store.close()


def test_insert_system_event(tmp_path: Path) -> None:
    """insert_system_event writes a row with all fields to system_events."""
    store = StateStore(tmp_path / "test.db")
    store.insert_system_event(
        event_type="STARTUP",
        timestamp="2026-04-16T09:15:00+05:30",
        scenario="WARM",
        details='{"session_date": "2026-04-16"}',
    )
    rows = store.fetch_all("SELECT * FROM system_events WHERE event_type='STARTUP'")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "STARTUP"
    assert rows[0]["scenario"] == "WARM"
    print("  OK insert_system_event: row written")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# SH9/SH10 innings table + helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_inning(trade_id: str, inning_number: int = 1, is_real: bool = True) -> Inning:
    ts = datetime(2026, 4, 16, 10, 0, 0)
    return Inning(
        inning_number=inning_number,
        trade_id=trade_id,
        symbol="RELIANCE",
        direction="LONG",
        entry_price=2500.0,
        entry_ts=ts,
        sl_price=2450.0,
        tgt_price=2600.0,
        exit_price=None,
        exit_ts=None,
        exit_reason=None,
        duration_sec=None,
        pnl_pct=None,
        pnl_per_share=None,
        is_real=is_real,
    )


def test_innings_table_exists(tmp_path: Path) -> None:
    """innings table created in schema v9 (SH9)."""
    store = StateStore(tmp_path / "test.db")
    rows = store.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='innings'"
    )
    assert len(rows) == 1
    print("  OK innings table exists in schema v9")
    store.close()


def test_insert_inning(tmp_path: Path) -> None:
    """insert_inning persists all fields; is_real stored as 1/0 (SH10)."""
    store = StateStore(tmp_path / "test.db")
    ing = _make_inning("t_ins1")
    store.insert_inning(ing)

    rows = store.get_innings_for_trade("t_ins1")
    assert len(rows) == 1
    r = rows[0]
    assert r["trade_id"] == "t_ins1"
    assert r["inning_number"] == 1
    assert r["symbol"] == "RELIANCE"
    assert r["direction"] == "LONG"
    assert abs(r["entry_price"] - 2500.0) < 0.01
    assert abs(r["sl_price"] - 2450.0) < 0.01
    assert abs(r["tgt_price"] - 2600.0) < 0.01
    assert r["is_real"] == 1
    assert r["exit_price"] is None
    print("  OK insert_inning persists all fields with is_real=1")
    store.close()


def test_update_inning_close(tmp_path: Path) -> None:
    """update_inning_close sets exit fields on an open inning (SH10)."""
    store = StateStore(tmp_path / "test.db")
    ing = _make_inning("t_upd", inning_number=2, is_real=False)
    store.insert_inning(ing)

    store.update_inning_close(
        trade_id="t_upd",
        inning_number=2,
        exit_price=2600.0,
        exit_ts="2026-04-16T12:00:00",
        exit_reason="TGT",
        duration_sec=7200,
        pnl_pct=4.0,
        pnl_per_share=100.0,
    )

    rows = store.get_innings_for_trade("t_upd")
    assert len(rows) == 1
    r = rows[0]
    assert abs(r["exit_price"] - 2600.0) < 0.01
    assert r["exit_reason"] == "TGT"
    assert r["duration_sec"] == 7200
    assert abs(r["pnl_pct"] - 4.0) < 0.01
    assert abs(r["pnl_per_share"] - 100.0) < 0.01
    print("  OK update_inning_close populates exit fields")
    store.close()


def test_get_innings_for_trade(tmp_path: Path) -> None:
    """get_innings_for_trade returns all innings for a trade in order (SH10)."""
    store = StateStore(tmp_path / "test.db")
    for n in [3, 1, 2]:
        store.insert_inning(_make_inning("t_ord", inning_number=n, is_real=(n == 1)))

    rows = store.get_innings_for_trade("t_ord")
    assert [r["inning_number"] for r in rows] == [1, 2, 3]
    assert rows[0]["is_real"] == 1
    assert rows[1]["is_real"] == 0
    print("  OK get_innings_for_trade returns rows ordered by inning_number")
    store.close()


def test_get_innings_for_date(tmp_path: Path) -> None:
    """get_innings_for_date filters innings to the requested date (SH10)."""
    store = StateStore(tmp_path / "test.db")

    ts_today = datetime(2026, 4, 16, 10, 0, 0)
    ts_other = datetime(2026, 4, 15, 10, 0, 0)

    store.insert_inning(Inning(
        inning_number=1, trade_id="t_d1", symbol="RELIANCE",
        direction="LONG", entry_price=2500.0, entry_ts=ts_today,
        sl_price=2450.0, tgt_price=2600.0,
        exit_price=None, exit_ts=None, exit_reason=None,
        duration_sec=None, pnl_pct=None, pnl_per_share=None, is_real=True,
    ))
    store.insert_inning(Inning(
        inning_number=1, trade_id="t_d2", symbol="TCS",
        direction="LONG", entry_price=3000.0, entry_ts=ts_other,
        sl_price=2940.0, tgt_price=3120.0,
        exit_price=None, exit_ts=None, exit_reason=None,
        duration_sec=None, pnl_pct=None, pnl_per_share=None, is_real=True,
    ))

    rows_today = store.get_innings_for_date("2026-04-16")
    rows_other = store.get_innings_for_date("2026-04-15")
    assert len(rows_today) == 1 and rows_today[0]["trade_id"] == "t_d1"
    assert len(rows_other) == 1 and rows_other[0]["trade_id"] == "t_d2"
    print("  OK get_innings_for_date filters by date correctly")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# DR-U3 get_inning_summary_by_date
# ─────────────────────────────────────────────────────────────────────────────

def _seed_innings_for_date(store: StateStore, trade_id: str, n_innings: int,
                           date_iso: str) -> None:
    """Insert signal+trade+n innings for a given date."""
    insert_test_trade(store, trade_id, created_date=date_iso)
    ts = datetime(int(date_iso[:4]), int(date_iso[5:7]), int(date_iso[8:10]),
                  9, 30, 0)
    for n in range(1, n_innings + 1):
        store.insert_inning(Inning(
            inning_number=n,
            trade_id=trade_id,
            symbol="RELIANCE",
            direction="LONG",
            entry_price=2500.0,
            entry_ts=ts,
            sl_price=2450.0,
            tgt_price=2600.0,
            exit_price=2600.0,
            exit_ts=ts,
            exit_reason="TGT",
            duration_sec=3600,
            pnl_pct=4.0,
            pnl_per_share=100.0,
            is_real=(n == 1),
        ))


def test_get_inning_summary_by_date_empty(tmp_path: Path) -> None:
    """Empty innings -> empty list returned (DR-U3)."""
    store = StateStore(tmp_path / "test.db")
    rows = store.get_inning_summary_by_date("2026-04-16")
    assert rows == []
    print("  OK get_inning_summary_by_date: empty day returns []")
    store.close()


def test_get_inning_summary_by_date_one_inning(tmp_path: Path) -> None:
    """1 trade with 1 inning -> 1 row with correct joined fields (DR-U3)."""
    store = StateStore(tmp_path / "test.db")
    _seed_innings_for_date(store, "t1", 1, "2026-04-16")
    rows = store.get_inning_summary_by_date("2026-04-16")
    assert len(rows) == 1
    r = rows[0]
    assert r["trade_id"] == "t1"
    assert r["inning_number"] == 1
    assert r["scanner_name"] == "SCANNER"
    assert r["symbol"] == "RELIANCE"
    print("  OK get_inning_summary_by_date: 1 inning joins correctly")
    store.close()


def test_get_inning_summary_by_date_three_innings(tmp_path: Path) -> None:
    """1 trade with 3 innings -> 3 rows ordered by inning_number (DR-U3)."""
    store = StateStore(tmp_path / "test.db")
    _seed_innings_for_date(store, "t1", 3, "2026-04-16")
    rows = store.get_inning_summary_by_date("2026-04-16")
    assert len(rows) == 3
    assert [r["inning_number"] for r in rows] == [1, 2, 3]
    print("  OK get_inning_summary_by_date: 3 innings ordered correctly")
    store.close()


def test_get_inning_summary_by_date_filters_date(tmp_path: Path) -> None:
    """Innings on other dates excluded; only the queried date returned (DR-U3)."""
    store = StateStore(tmp_path / "test.db")
    for date_iso in ["2026-04-15", "2026-04-16", "2026-04-17"]:
        _seed_innings_for_date(store, f"t_{date_iso}", 1, date_iso)
    rows = store.get_inning_summary_by_date("2026-04-16")
    assert len(rows) == 1
    assert rows[0]["trade_id"] == "t_2026-04-16"
    print("  OK get_inning_summary_by_date: date filter works correctly")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# BL-3 / Phase B.5: sum_fm_ledger_margin_delta helper
# ─────────────────────────────────────────────────────────────────────────────

def test_bl3_sum_fm_ledger_margin_delta_unknown_rid_returns_zero(
    tmp_path: Path,
) -> None:
    """
    BL-3: sum_fm_ledger_margin_delta() returns 0.0 for an unknown
    reservation_id. The caller (BL-3 _check7) treats this as a drift
    signal: if fm_margin > 0 but ledger_sum == 0, the delta == fm_margin
    and it will publish CapitalDriftDetected.
    """
    store = StateStore(tmp_path / "test.db")
    result = store.sum_fm_ledger_margin_delta("rid_does_not_exist")
    assert result == 0.0
    assert isinstance(result, float)
    print("  OK sum_fm_ledger_margin_delta returns 0.0 for unknown rid (BL-3)")
    store.close()


def test_bl3_sum_fm_ledger_margin_delta_sums_signed(tmp_path: Path) -> None:
    """
    BL-3: sum_fm_ledger_margin_delta() sums signed margin_delta with NO
    entry_type filter (load-bearing per the docstring).

    Live reservation: only RESERVE row -> sum == +reserved margin.
    Closed reservation: RESERVE +m and RELEASE -m net to 0.
    Multiple RELEASE rows for the same rid (defensive): sum still nets.
    """
    store = StateStore(tmp_path / "test.db")
    rid_live = "rid_live_001"
    rid_closed = "rid_closed_002"

    # Live: just RESERVE
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO fm_ledger
              (ts, entry_type, amount, bucket, balance_before, balance_after,
               signal_id, reservation_id, margin_delta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-04-19T09:30:00+05:30", "RESERVE", 1000.0,
             "intraday", 70_000.0, 69_000.0, "sig_a", rid_live, 1000.0),
        )

    assert store.sum_fm_ledger_margin_delta(rid_live) == 1000.0

    # Closed: RESERVE then RELEASE_USED with equal magnitude
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO fm_ledger
              (ts, entry_type, amount, bucket, balance_before, balance_after,
               signal_id, reservation_id, margin_delta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-04-19T09:31:00+05:30", "RESERVE", 500.0,
             "intraday", 69_000.0, 68_500.0, "sig_b", rid_closed, 500.0),
        )
        cur.execute(
            """
            INSERT INTO fm_ledger
              (ts, entry_type, amount, bucket, balance_before, balance_after,
               signal_id, reservation_id, margin_delta, pnl_delta, direction)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-04-19T10:00:00+05:30", "RELEASE_USED", -500.0,
             "intraday", 68_500.0, 69_100.0, "sig_b", rid_closed,
             -500.0, 100.0, "LONG"),
        )

    # Closed reservation: RESERVE(+500) + RELEASE_USED(-500) = 0.
    # No entry_type filter: would have been wrong if we filtered out
    # RELEASE_USED -- the +500 RESERVE would remain and report drift.
    assert store.sum_fm_ledger_margin_delta(rid_closed) == 0.0

    # The "live" rid is unchanged by the closed-rid inserts.
    assert store.sum_fm_ledger_margin_delta(rid_live) == 1000.0

    print("  OK sum_fm_ledger_margin_delta sums signed; no entry_type filter (BL-3)")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner (no pytest dependency)
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    """Run all tests sequentially. Returns 0 on success, 1 on any failure."""
    tests = [
        test_schema_creates_all_tables,
        test_schema_version_matches_expected,
        test_schema_initialization_is_idempotent,
        test_transaction_commit_persists_data,
        test_transaction_rollback_on_exception,
        test_dedup_unique_index_rejects_duplicates,
        test_foreign_keys_enforced,
        test_capital_snapshot_single_row_constraint,
        test_thread_safety_concurrent_writes,
        test_close_then_reopen_preserves_data,
        # RE15 query helper tests
        test_count_open_positions,
        test_count_in_flight_orders,
        test_count_trades_today,
        test_sector_exposure,
        test_has_active_position,
        test_recent_trade_pnls,
        # KS9 kill_switch_state table tests
        test_kill_switch_state_table_exists,
        test_kill_switch_state_single_row_constraint,
        test_kill_switch_state_insert_and_read,
        # SP9 signal status helper test
        test_update_signal_status,
        # WR13 webhook_audit table test
        test_webhook_audit_insert_and_query,
        # EOD8/EOD9 eod_squareoff helpers
        test_eod_squareoff_log_table_exists,
        test_get_pending_intraday_orders_empty,
        test_get_pending_intraday_orders_mis_co_only,
        test_get_open_intraday_positions_empty,
        test_get_open_intraday_positions_open_partial_only,
        test_b3_get_open_intraday_positions_excludes_qty_filled_zero,
        test_get_reservation_id_for_signal,
        test_get_eod_squareoff_log_returns_none_when_absent,
        test_insert_and_get_eod_squareoff_log,
        test_insert_eod_squareoff_log_or_replace,
        # RC10 reconciliation_log + RC-helper query tests
        test_reconciliation_log_table_exists,
        test_get_all_open_trades_empty,
        test_get_all_open_trades_returns_open_partial,
        test_get_orders_for_trade,
        test_get_sl_order_for_trade_active,
        test_get_sl_order_for_trade_none_when_absent,
        test_mark_trade_manually_closed,
        test_get_pending_all_products,
        test_insert_reconciliation_log,
        # SS6 screener_results table tests
        test_screener_results_table_exists,
        test_insert_screener_result,
        test_insert_screener_result_multiple_rows,
        # ST14+ST15 CO lookup + smart_tgt_state table tests
        test_smart_tgt_state_table_exists,
        test_get_co_entry_order_returns_co_row,
        test_get_co_entry_order_excludes_non_co_entry,
        test_get_co_entry_order_returns_none_when_absent,
        test_insert_smart_tgt_state_and_get_all,
        test_update_smart_tgt_state,
        test_delete_smart_tgt_state,
        test_get_all_smart_tgt_states_multiple_rows,
        # SC4/SC6 session + system_events helpers
        test_get_session_row_returns_none_when_no_session,
        test_get_session_row_returns_row,
        test_find_shutdown_event_missing,
        test_find_shutdown_event_found,
        test_insert_system_event,
        # SH9/SH10 innings table + helpers
        test_innings_table_exists,
        test_insert_inning,
        test_update_inning_close,
        test_get_innings_for_trade,
        test_get_innings_for_date,
        # DR-U3 inning summary helper
        test_get_inning_summary_by_date_empty,
        test_get_inning_summary_by_date_one_inning,
        test_get_inning_summary_by_date_three_innings,
        test_get_inning_summary_by_date_filters_date,
        # BL-3 / Phase B.5: sum_fm_ledger_margin_delta helper
        test_bl3_sum_fm_ledger_margin_delta_unknown_rid_returns_zero,
        test_bl3_sum_fm_ledger_margin_delta_sums_signed,
    ]

    print("=" * 70)
    print("state_store.py -- Test Suite (incl. SC4/SC6 session helpers)")
    print("=" * 70)
    
    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            try:
                test(Path(td))
            except AssertionError as e:
                failed.append((test.__name__, f"AssertionError: {e}"))
                print(f"  FAIL FAIL: {e}")
            except Exception as e:
                failed.append((test.__name__, f"{type(e).__name__}: {e}"))
                print(f"  FAIL ERROR: {type(e).__name__}: {e}")
    
    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1
    
    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
