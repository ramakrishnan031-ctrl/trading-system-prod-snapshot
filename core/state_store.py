"""
core/state_store.py — Trading System v2

Purpose:
    Single SQLite-backed source of truth for all persistent runtime state.
    Every other module reads/writes state through this one interface.
    No other module touches SQLite directly.

Manual Input Required:
    No.

How It Works:
    1. On instantiation, opens a SQLite connection in WAL mode.
    2. Sets PRAGMA foreign_keys = ON (SQLite requires this per-connection).
    3. Loads schema.sql and runs it (idempotent — uses CREATE IF NOT EXISTS).
    4. Verifies schema_version matches code's expected version.
    5. Provides a transaction() context manager for atomic writes.
    6. Provides generic execute/fetch helpers for callers.
    7. Uses threading.local for connection-per-thread isolation
       (sqlite3 connections are not thread-safe).

Inputs:
    - db_path: pathlib.Path to the .db file (will be created if missing)
    - schema_path: pathlib.Path to schema.sql (defaults to sibling file)

Outputs:
    - Manages the SQLite database file at db_path
    - Manages WAL and SHM sidecar files automatically

Main Class:
    StateStore — see public API below.

Design Refs:
    - G1 (reconciler reads/writes state via this module)
    - G2a (signals/trades/orders schema)
    - G3 (capital_ledger atomicity)
    - G5a (system_events for scenario detection)
    - P7a (capital_snapshot 3-balance model)
    - Foundation Rule 1.3 (Single Responsibility): this module ONLY does
      SQLite plumbing. Business logic lives in higher layers.

What This Module Does NOT Do:
    - Does not compute capital math
    - Does not call invariant checks (caller's job, see capital/invariant.py)
    - Does not enforce business rules
    - Does not log to Telegram or send alerts
    - Does not subscribe to events
    - Does not import from any layer above core/
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    # H-17 SKIP: state_store sits below time_authority in the layering; importing
    # core.time_authority here would invert the dependency direction. Leaving as
    # datetime.now(_IST) is the deliberate exemption.
    return datetime.now(_IST).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

EXPECTED_SCHEMA_VERSION = 13

DEFAULT_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# SQLite PRAGMAs applied to every connection
_CONNECTION_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",      # WAL + NORMAL is durable enough for our use
    "PRAGMA foreign_keys = ON",         # Per-connection; SQLite requires this
    "PRAGMA temp_store = MEMORY",
    "PRAGMA busy_timeout = 5000",       # Wait up to 5s on a locked DB
)


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class StateStoreError(Exception):
    """Base exception for state_store errors."""


class SchemaVersionMismatch(StateStoreError):
    """DB schema version differs from code's expected version."""


class TransactionError(StateStoreError):
    """A transaction failed; the caller should treat its work as not committed."""


# ─────────────────────────────────────────────────────────────────────────────
# StateStore
# ─────────────────────────────────────────────────────────────────────────────

class StateStore:
    """
    SQLite-backed state store.
    
    Thread-safety: this object is safe to share across threads. Each thread
    gets its own SQLite connection (via threading.local) on first use.
    
    Usage:
        store = StateStore(Path("data_store/trading_system.db"))
        
        # Generic queries
        rows = store.fetch_all("SELECT * FROM signals WHERE status = ?", ("TRADED",))
        
        # Transactional writes
        with store.transaction() as cur:
            cur.execute("INSERT INTO signals (...) VALUES (...)", (...))
            cur.execute("UPDATE session SET ... WHERE id = 1", (...))
            # commits on successful exit; rolls back on exception
        
        store.close()
    """
    
    def __init__(
        self,
        db_path: Path,
        schema_path: Path = DEFAULT_SCHEMA_PATH,
    ) -> None:
        self._db_path = Path(db_path)
        self._schema_path = Path(schema_path)
        
        # Per-thread connection storage. Each thread that calls a method
        # on this StateStore gets its own sqlite3.Connection.
        self._tls = threading.local()
        
        # Ensure parent directory exists
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize schema on the main thread's connection
        self._initialize_schema()
    
    # ─────────────────────────────────────────────────────────────────────────
    # Connection management (per-thread)
    #
    # Phase D / Audit 4.3 (closed as INFO, no code change):
    # The audit flagged threading.local() as a "connection memory leak" on
    # the assumption that workers are created and destroyed per-task. In v2
    # all SQL-touching workers run inside ThreadPoolExecutor pools (sized
    # in low single digits — signal_processor max 5, entry_gate worker
    # pool, smart_tgt_manager max 2). Pool threads are recycled, so the
    # per-thread sqlite3 connection count is bounded by the sum of pool
    # sizes — typically <= 12 connections for the whole process lifetime.
    # That is a bounded pool, not an unbounded leak. See the closure doc
    # for the full rationale. Leaving threading.local() in place.
    # ─────────────────────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """
        Return the connection for the calling thread, creating one if needed.
        Each thread that ever calls a StateStore method gets its own
        sqlite3.Connection — sqlite3 connections are not thread-safe.
        """
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = self._open_connection()
            self._tls.conn = conn
        return conn
    
    def _open_connection(self) -> sqlite3.Connection:
        """
        Open a fresh sqlite3 connection with the standard PRAGMAs applied.
        Called once per thread on first use.
        """
        conn = sqlite3.connect(
            str(self._db_path),
            isolation_level=None,       # We manage transactions explicitly via BEGIN/COMMIT
            check_same_thread=True,     # Catch accidental cross-thread use immediately
        )
        # Row factory: tuple-by-default but accessible by column name as well
        conn.row_factory = sqlite3.Row
        
        for pragma in _CONNECTION_PRAGMAS:
            conn.execute(pragma)
        
        return conn
    
    def close(self) -> None:
        """
        Close the connection for the calling thread. Other threads' connections
        remain open until they close themselves.
        
        Note: in normal shutdown, each thread should call close() before exiting.
        For the main thread, call this last during shutdown.
        """
        conn = getattr(self._tls, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._tls.conn = None
    
    # ─────────────────────────────────────────────────────────────────────────
    # Schema initialization & version check
    # ─────────────────────────────────────────────────────────────────────────
    
    def _initialize_schema(self) -> None:
        """
        Run schema.sql against the database. Idempotent because schema.sql
        uses CREATE TABLE IF NOT EXISTS. Then verify schema_version matches
        EXPECTED_SCHEMA_VERSION; raise SchemaVersionMismatch if not.
        """
        if not self._schema_path.exists():
            raise StateStoreError(
                f"Schema file not found at {self._schema_path}. "
                f"Cannot initialize state store."
            )
        
        schema_sql = self._schema_path.read_text(encoding="utf-8")
        
        conn = self._get_conn()
        # Execute the entire schema as one script
        conn.executescript(schema_sql)
        
        # Verify version
        version = self.get_schema_version()
        if version != EXPECTED_SCHEMA_VERSION:
            raise SchemaVersionMismatch(
                f"Database schema version is {version}, "
                f"code expects {EXPECTED_SCHEMA_VERSION}. "
                f"Run migrations or use a fresh database."
            )
    
    def get_schema_version(self) -> int:
        """Return the current schema_version stored in the schema_meta table."""
        row = self.fetch_one(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        )
        if row is None:
            raise StateStoreError(
                "schema_meta has no schema_version row — DB is corrupt or empty"
            )
        return int(row["value"])
    
    def migrate_to_latest(self) -> None:
        """
        Stub for future migrations. v1 has no prior versions.
        When a v2 schema is created, this method will detect the current
        version and run the appropriate migration scripts in order.
        """
        current = self.get_schema_version()
        if current == EXPECTED_SCHEMA_VERSION:
            return
        # Future: dispatch to migration functions based on (current, target)
        raise SchemaVersionMismatch(
            f"No migration path from v{current} to v{EXPECTED_SCHEMA_VERSION}"
        )
    
    # ─────────────────────────────────────────────────────────────────────────
    # Transaction context manager
    # ─────────────────────────────────────────────────────────────────────────
    
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        """
        Context manager that yields a cursor inside an explicit transaction.
        Commits on successful exit, rolls back on any exception.
        
        Usage:
            with store.transaction() as cur:
                cur.execute("INSERT INTO signals (...) VALUES (...)", (...))
                cur.execute("UPDATE session SET ... WHERE id = 1", (...))
        
        Notes:
            - Nested transactions are NOT supported (SQLite limitation).
              If you need nesting, refactor to a single transaction at the
              outermost call site.
            - Foreign key violations roll back the transaction at COMMIT time
              because PRAGMA foreign_keys = ON is set.
            - The cursor is closed automatically on exit.
        """
        conn = self._get_conn()
        cur = conn.cursor()
        try:
            # H-5: BEGIN IMMEDIATE acquires a RESERVED lock at transaction
            # start, serializing writers cleanly. Plain BEGIN (SQLite DEFERRED)
            # defers locking until the first write, which can deadlock when
            # two writers upgrade concurrently. Readers are unaffected.
            cur.execute("BEGIN IMMEDIATE")
            yield cur
            cur.execute("COMMIT")
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except sqlite3.Error:
                # ROLLBACK can fail if there's no active transaction;
                # that means the original BEGIN failed — re-raise the original.
                pass
            raise
        finally:
            cur.close()
    
    # ─────────────────────────────────────────────────────────────────────────
    # Generic query helpers
    # ─────────────────────────────────────────────────────────────────────────
    
    def execute(
        self,
        sql: str,
        params: Tuple[Any, ...] = (),
    ) -> sqlite3.Cursor:
        """
        Execute a single SQL statement. For writes, prefer transaction()
        which guarantees atomicity. This is a convenience for single
        statements that don't need transactional grouping.
        
        Returns the cursor (for caller to read lastrowid, rowcount, etc.).
        """
        conn = self._get_conn()
        return conn.execute(sql, params)
    
    def fetch_one(
        self,
        sql: str,
        params: Tuple[Any, ...] = (),
    ) -> Optional[sqlite3.Row]:
        """Execute a SELECT and return the first row, or None if empty."""
        cur = self._get_conn().execute(sql, params)
        try:
            return cur.fetchone()
        finally:
            cur.close()
    
    def fetch_all(
        self,
        sql: str,
        params: Tuple[Any, ...] = (),
    ) -> List[sqlite3.Row]:
        """Execute a SELECT and return all rows."""
        cur = self._get_conn().execute(sql, params)
        try:
            return cur.fetchall()
        finally:
            cur.close()
    
    # ─────────────────────────────────────────────────────────────────────────
    # Introspection helpers (useful for debugging and tests)
    # ─────────────────────────────────────────────────────────────────────────
    
    def table_names(self) -> List[str]:
        """Return all user table names in the database."""
        rows = self.fetch_all(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        return [row["name"] for row in rows]
    
    def row_count(self, table: str) -> int:
        """
        Return the row count for a given table. The table name is NOT
        parameterized (SQLite doesn't allow it); the caller is responsible
        for passing a known-safe table name.
        """
        # Whitelist check: only allow alphanumeric + underscore
        if not table.replace("_", "").isalnum():
            raise ValueError(f"Invalid table name: {table!r}")
        row = self.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")
        return int(row["n"]) if row else 0
    
    # ─────────────────────────────────────────────────────────────────────────
    # Risk engine query helpers (RE15)
    # All helpers are read-only; they issue SELECT queries only.
    # ─────────────────────────────────────────────────────────────────────────

    def count_open_positions(self) -> int:
        """
        Count trades whose positions are confirmed open (status OPEN or PARTIAL).
        Used by risk_engine OPEN_POSITIONS check (RE5).
        """
        row = self.fetch_one(
            "SELECT COUNT(*) AS n FROM trades WHERE status IN ('OPEN', 'PARTIAL')"
        )
        return int(row["n"]) if row else 0

    def count_in_flight_orders(self) -> int:
        """
        Count trades whose entry orders are placed but not yet filled
        (status PENDING_FILL). These still consume reserved capital.
        Used by risk_engine OPEN_POSITIONS check (RE5).
        """
        row = self.fetch_one(
            "SELECT COUNT(*) AS n FROM trades WHERE status = 'PENDING_FILL'"
        )
        return int(row["n"]) if row else 0

    def count_trades_today(self, date_iso: str) -> int:
        """
        Count trade rows created on the given IST date (YYYY-MM-DD).
        Matches against SUBSTR(created_at, 1, 10) — works with ISO-8601 IST
        strings stored in the DB (e.g., "2026-04-14T09:30:00+05:30").
        Counts one row per trade (not per signal); a signal that is dropped
        pre-trade is not counted here. Used by risk_engine DAILY_TRADES check
        (RE5).
        """
        row = self.fetch_one(
            "SELECT COUNT(*) AS n FROM trades "
            "WHERE SUBSTR(created_at, 1, 10) = ?",
            (date_iso,),
        )
        return int(row["n"]) if row else 0

    def count_signals_today(self, date_iso: str) -> int:
        """
        Count all signal rows received on the given IST date (YYYY-MM-DD).
        Matches against SUBSTR(received_at, 1, 10) — mirrors the
        count_trades_today pattern. Counts every signal (including dropped
        and rejected), which is a superset of count_trades_today.
        """
        row = self.fetch_one(
            "SELECT COUNT(*) AS n FROM signals "
            "WHERE SUBSTR(received_at, 1, 10) = ?",
            (date_iso,),
        )
        return int(row["n"]) if row else 0

    def sector_exposure(self, sector: str) -> float:
        """
        Total margin_reserved for all active trades (PENDING_FILL/OPEN/PARTIAL)
        in the given sector. Counts BOTH open positions AND in-flight reservations
        to catch concentration races (RE6 audit fix).
        Used by risk_engine SECTOR_EXPOSURE check (RE5, RE6).
        """
        row = self.fetch_one(
            """
            SELECT COALESCE(SUM(margin_reserved), 0.0) AS total
            FROM trades
            WHERE status IN ('PENDING_FILL', 'OPEN', 'PARTIAL')
              AND sector = ?
            """,
            (sector,),
        )
        return float(row["total"]) if row else 0.0

    def has_active_position(self, symbol: str) -> bool:
        """
        Return True if any trade for symbol is currently active
        (status PENDING_FILL, OPEN, or PARTIAL).
        Used by risk_engine DUPLICATE_SYMBOL check (RE5).
        """
        row = self.fetch_one(
            """
            SELECT COUNT(*) AS n FROM trades
            WHERE symbol = ?
              AND status IN ('PENDING_FILL', 'OPEN', 'PARTIAL')
            """,
            (symbol,),
        )
        return (int(row["n"]) if row else 0) > 0

    def recent_trade_pnls(self, n: int) -> List[float]:
        """
        Return the net_pnl values of the n most recently closed trades,
        ordered most-recent-first. Excludes rows where net_pnl IS NULL.
        Used by risk_engine CONSECUTIVE_LOSSES check (RE5, RE10).
        """
        rows = self.fetch_all(
            """
            SELECT net_pnl FROM trades
            WHERE status = 'CLOSED' AND net_pnl IS NOT NULL
            ORDER BY exit_time DESC
            LIMIT ?
            """,
            (n,),
        )
        return [float(row["net_pnl"]) for row in rows]

    # ─────────────────────────────────────────────────────────────────────────
    # EOD square-off query helpers (EOD8, EOD9)
    # ─────────────────────────────────────────────────────────────────────────

    def get_pending_intraday_orders(self) -> List[sqlite3.Row]:
        """
        Return trades with status=PENDING_FILL whose ENTRY leg has an
        intraday product (MIS or CO). Each row includes:
            trade_id, signal_id, symbol, direction, broker_order_id
        Used by eod_squareoff to cancel unfilled intraday entry orders (EOD5).
        Results sorted by symbol (Foundation Rule 3.7 deterministic order).
        """
        return self.fetch_all(
            """
            SELECT
                t.trade_id,
                t.signal_id,
                t.symbol,
                t.direction,
                o.order_id  AS broker_order_id
            FROM trades t
            JOIN orders o
              ON o.trade_id = t.trade_id
             AND o.leg = 'ENTRY'
            WHERE t.status = 'PENDING_FILL'
              AND o.product IN ('MIS', 'CO')
            ORDER BY t.symbol
            """
        )

    def get_open_orders_for_rehydration(self) -> List[sqlite3.Row]:
        """
        Return non-terminal orders that need fill polling on startup
        (Audit #21). Joined with the trades table so the caller gets the
        symbol without a second query.

        Row fields: order_id, status, transaction_type, qty_requested,
            price, placed_at, symbol, trade_id, leg, order_protocol,
            direction.

        B.1 (2026-04-25): leg/trade_id/order_protocol/direction added so
        OrderPlacer.rehydrate_fill_map can repopulate _fill_map for SL/TGT/EOD
        legs after restart. Without those columns a non-terminal exit fill
        landing post-restart would dispatch to a missing _fill_map entry and
        silently fail to close the trade.
        """
        return self.fetch_all(
            """
            SELECT
                o.order_id,
                o.status,
                o.transaction_type,
                o.qty_requested,
                o.price,
                o.placed_at,
                t.symbol,
                o.trade_id,
                o.leg,
                t.order_protocol,
                t.direction
            FROM orders o
            JOIN trades t
              ON t.trade_id = o.trade_id
            WHERE o.status IN ('PENDING', 'SUBMITTED', 'OPEN', 'PARTIAL',
                               'TRIGGER_PENDING')
            ORDER BY o.placed_at
            """
        )

    def get_pending_exit_orders_for_open_positions(self) -> List[sqlite3.Row]:
        """
        Return non-terminal SL/TGT orders for trades whose ENTRY is filled
        (status OPEN or PARTIAL) and whose product is intraday (MIS/CO).

        Used by eod_squareoff Step 3b (Audit #6): these exit legs must be
        cancelled BEFORE the MARKET squareoff fires, otherwise a late
        TGT/SL fill after the MARKET exit opens a naked reverse position.

        Row fields: trade_id, symbol, leg, variety, order_id
            (order_id is the broker_order_id; see orders.order_id PK).
        Sorted by symbol (Foundation Rule 3.7).
        """
        return self.fetch_all(
            """
            SELECT
                t.trade_id,
                t.symbol,
                o.leg,
                o.variety,
                o.order_id
            FROM trades t
            JOIN orders o
              ON o.trade_id = t.trade_id
            JOIN orders e
              ON e.trade_id = t.trade_id
             AND e.leg = 'ENTRY'
            WHERE t.status IN ('OPEN', 'PARTIAL')
              AND e.product IN ('MIS', 'CO')
              AND o.leg IN ('SL', 'TGT')
              AND o.status NOT IN ('COMPLETE', 'CANCELLED', 'REJECTED', 'FAILED')
            ORDER BY t.symbol, o.leg
            """
        )

    def get_open_intraday_positions(self) -> List[sqlite3.Row]:
        """
        Return trades with status OPEN or PARTIAL whose ENTRY leg has an
        intraday product (MIS or CO). Each row includes:
            trade_id, signal_id, symbol, direction, qty_filled,
            order_protocol, entry_broker_order_id, entry_variety
        Used by eod_squareoff (EOD5, EOD6) to either place a MARKET reverse
        exit (MIS / LIMIT_TRIPLE) or cancel the CO bracket via
        adapter.cancel_order(variety="co") (audit 3.1 — Zerodha forbids
        reverse MARKET for a live CO and auto-squares at 15:20 with a
        ₹50+GST penalty per position).

        B.3 (2026-04-25): qty_filled > 0 added to WHERE clause. A trade
        can be in status OPEN/PARTIAL with qty_filled=0 if the status was
        flipped before any fill arrived (race window or recovery edge
        case). Without this filter, EOD would attempt a MARKET reverse on
        a zero-qty position; broker would reject but the attempt wastes
        an order quota tick. Belt-and-braces with E.5's broker-position
        filter (which trims by adapter.get_positions()), but cheaper.

        Results sorted by symbol (Foundation Rule 3.7 deterministic order).
        """
        return self.fetch_all(
            """
            SELECT
                t.trade_id,
                t.signal_id,
                t.symbol,
                t.direction,
                t.qty_filled,
                t.order_protocol,
                o.order_id  AS entry_broker_order_id,
                o.variety   AS entry_variety
            FROM trades t
            JOIN orders o
              ON o.trade_id = t.trade_id
             AND o.leg = 'ENTRY'
            WHERE t.status IN ('OPEN', 'PARTIAL')
              AND t.qty_filled > 0
              AND o.product IN ('MIS', 'CO')
            GROUP BY t.trade_id
            ORDER BY t.symbol
            """
        )

    def sum_fm_ledger_margin_delta(self, reservation_id: str) -> float:
        """
        Sum margin_delta for all ledger rows of a given reservation_id (BL-3).

        For a live reservation: sum equals the current reserved margin (only
        RESERVE has fired, contributing +margin).
        For a closed reservation: sum is 0 (RESERVE +margin and RELEASE /
        RELEASE_USED -margin net out exactly), but the rid will not appear in
        FundManager._reservations either -- so BL-3's iteration over live
        reservations naturally skips closed ones.

        No entry_type filter is needed; the signed nature of margin_delta
        handles the accounting correctly. DO NOT add a filter here without
        first confirming what BL-3 needs -- a filter would break the
        invariant that sum-of-deltas == current-reserved-margin.

        Returns 0.0 if the reservation_id has no rows (caller treats this as
        a drift signal of magnitude == fm_margin).
        """
        row = self.fetch_one(
            """
            SELECT COALESCE(SUM(margin_delta), 0.0) AS s
            FROM fm_ledger
            WHERE reservation_id = ?
            """,
            (reservation_id,),
        )
        return float(row["s"]) if row is not None else 0.0

    def get_reservation_id_for_signal(self, signal_id: str) -> Optional[str]:
        """
        Return the most recent active reservation_id for a signal_id by
        querying fm_ledger for RESERVE rows. Returns None if not found.
        Used by eod_squareoff to release capital on cancel (EOD5).
        """
        row = self.fetch_one(
            """
            SELECT reservation_id FROM fm_ledger
            WHERE signal_id = ?
              AND entry_type = 'RESERVE'
              AND reservation_id IS NOT NULL
            ORDER BY ledger_id DESC
            LIMIT 1
            """,
            (signal_id,),
        )
        return row["reservation_id"] if row else None

    def get_eod_squareoff_log_for_date(self, date_iso: str) -> Optional[sqlite3.Row]:
        """
        Return the eod_squareoff_log row for the given YYYY-MM-DD date,
        or None if EOD has not run for that date. Used by EOD9 restart check.
        """
        return self.fetch_one(
            "SELECT * FROM eod_squareoff_log WHERE fired_date = ?",
            (date_iso,),
        )

    def insert_eod_squareoff_log(
        self,
        fired_date: str,
        fired_at: str,
        positions_attempted: int,
        positions_succeeded: int,
        positions_failed: int,
        cancels_attempted: int,
        cancels_succeeded: int,
        cancels_failed: int,
        duration_sec: float,
    ) -> None:
        """
        Insert (or replace) the eod_squareoff_log row for fired_date (EOD8).
        Uses INSERT OR REPLACE so a recovery-fire on the same date overwrites
        the failed original row.

        Single-shot write: status defaults to COMPLETE and completed_at is set
        to fired_at. For write-ahead use insert_eod_squareoff_log_start() +
        update_eod_squareoff_log_complete() instead (M-3).
        """
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO eod_squareoff_log
                  (fired_date, fired_at, positions_attempted, positions_succeeded,
                   positions_failed, cancels_attempted, cancels_succeeded,
                   cancels_failed, duration_sec, status, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETE', ?)
                """,
                (
                    fired_date, fired_at,
                    positions_attempted, positions_succeeded, positions_failed,
                    cancels_attempted, cancels_succeeded, cancels_failed,
                    duration_sec, fired_at,
                ),
            )

    def insert_eod_squareoff_log_start(
        self,
        fired_date: str,
        fired_at: str,
    ) -> None:
        """
        M-3 write-ahead: insert an IN_PROGRESS row at the start of an EOD fire.
        Zero counts, NULL completed_at. INSERT OR REPLACE so a retry after a
        crash overwrites the prior IN_PROGRESS row for the same date.
        """
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO eod_squareoff_log
                  (fired_date, fired_at, positions_attempted, positions_succeeded,
                   positions_failed, cancels_attempted, cancels_succeeded,
                   cancels_failed, duration_sec, status, completed_at)
                VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0.0, 'IN_PROGRESS', NULL)
                """,
                (fired_date, fired_at),
            )

    def update_eod_squareoff_log_complete(
        self,
        fired_date: str,
        positions_attempted: int,
        positions_succeeded: int,
        positions_failed: int,
        cancels_attempted: int,
        cancels_succeeded: int,
        cancels_failed: int,
        duration_sec: float,
        completed_at: str,
    ) -> None:
        """
        M-3 write-ahead: transition an IN_PROGRESS row to COMPLETE with final
        counts + completed_at. Called after a successful fire.
        """
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE eod_squareoff_log
                   SET positions_attempted = ?,
                       positions_succeeded = ?,
                       positions_failed    = ?,
                       cancels_attempted   = ?,
                       cancels_succeeded   = ?,
                       cancels_failed      = ?,
                       duration_sec        = ?,
                       status              = 'COMPLETE',
                       completed_at        = ?
                 WHERE fired_date = ?
                """,
                (
                    positions_attempted, positions_succeeded, positions_failed,
                    cancels_attempted, cancels_succeeded, cancels_failed,
                    duration_sec, completed_at, fired_date,
                ),
            )

    def update_signal_status(
        self,
        signal_id: str,
        status: str,
        reason: str = "",
        ts: str = "",   # accepted for SP9 API symmetry; not stored (no updated_at col)
    ) -> None:
        """
        Update the status and rejection_reason of a signal row (SP9, P16).

        Called at every state change in the processing pipeline so every
        signal_id has a terminal status + reason for audit purposes.
        No-op if signal_id does not exist (logs nothing; caller owns pre-check).
        """
        with self.transaction() as cur:
            cur.execute(
                "UPDATE signals SET status = ?, rejection_reason = ? WHERE signal_id = ?",
                (status, reason if reason else None, signal_id),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Reconciler query helpers (RC10)
    # ─────────────────────────────────────────────────────────────────────────

    def get_all_open_trades(self) -> List[sqlite3.Row]:
        """
        Return all trades with status OPEN or PARTIAL, regardless of product.

        Each row includes trade_id, signal_id, symbol, direction, qty_planned,
        qty_filled, status, sl_initial, entry_target_price, entry_actual_price,
        product, entry_broker_order_id. Used by order_reconciler for position-
        level drift detection (G1 checks 1-5) and by FundManager.rehydrate_
        from_open_trades for capital replay (BL-1). Sorted by symbol
        (Foundation Rule 3.7).
        """
        return self.fetch_all(
            """
            SELECT
                t.trade_id,
                t.signal_id,
                t.symbol,
                t.direction,
                t.qty_planned,
                t.qty_filled,
                t.status,
                t.sl_initial,
                t.entry_target_price,
                t.entry_actual_price,
                t.reservation_id,
                o.product,
                o.order_id AS entry_broker_order_id
            FROM trades t
            LEFT JOIN orders o
              ON o.trade_id = t.trade_id
             AND o.leg = 'ENTRY'
            WHERE t.status IN ('OPEN', 'PARTIAL')
            ORDER BY t.symbol
            """
        )

    def get_orders_for_trade(self, trade_id: str) -> List[sqlite3.Row]:
        """
        Return all order rows for a trade_id.

        Each row includes order_id, leg, status, trigger_price, price,
        qty_requested, qty_filled. Used by reconciler to inspect SL/TGT
        presence per trade. Sorted by leg, placed_at.
        """
        return self.fetch_all(
            """
            SELECT order_id, leg, status, trigger_price, price,
                   qty_requested, qty_filled
            FROM orders
            WHERE trade_id = ?
            ORDER BY leg, placed_at
            """,
            (trade_id,),
        )

    def get_sl_order_for_trade(self, trade_id: str) -> Optional[sqlite3.Row]:
        """
        Return the active SL order row for a trade, or None if absent.

        'Active' = leg='SL' and status not in terminal states. Returns the
        most recently placed SL (in case of trail replacements). Used by
        order_reconciler G5b crash-recovery SL check (RC7).
        """
        return self.fetch_one(
            """
            SELECT order_id, trigger_price, qty_requested
            FROM orders
            WHERE trade_id = ?
              AND leg = 'SL'
              AND status NOT IN ('CANCELLED', 'COMPLETE', 'FAILED',
                                  'EXPIRED', 'REJECTED')
            ORDER BY placed_at DESC
            LIMIT 1
            """,
            (trade_id,),
        )

    def mark_trade_manually_closed(self, trade_id: str) -> None:
        """
        Mark a trade as CLOSED_MANUAL.

        Called by order_reconciler CHECK 1 when broker position is gone but
        the local trade is still OPEN/PARTIAL — indicating manual broker close
        or SL-hit that was not relayed to the system.
        """
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE trades
                SET status = 'CLOSED_MANUAL',
                    exit_reason = 'MANUAL',
                    updated_at = ?
                WHERE trade_id = ?
                """,
                (_now_ist_iso(), trade_id),
            )

    def get_pending_all_products(self) -> List[sqlite3.Row]:
        """
        Return all PENDING_FILL trades regardless of product.

        Like get_pending_intraday_orders() but covers CNC and all products.
        Each row: trade_id, signal_id, symbol, direction, broker_order_id.
        Used by reconciler for CHECK 6 (orphan order detection on pending trades).
        """
        return self.fetch_all(
            """
            SELECT
                t.trade_id,
                t.signal_id,
                t.symbol,
                t.direction,
                o.order_id AS broker_order_id
            FROM trades t
            JOIN orders o
              ON o.trade_id = t.trade_id
             AND o.leg = 'ENTRY'
            WHERE t.status = 'PENDING_FILL'
            ORDER BY t.symbol
            """
        )

    def insert_reconciliation_log(
        self,
        ts: str,
        check_name: str,
        tier: str,
        symbol: str,
        trade_id: Optional[str],
        description: str,
        action_taken: str,
        success: bool,
    ) -> None:
        """
        Persist one reconciliation action to reconciliation_log (RC10).

        Called once per ReconciliationAction after each _reconcile() cycle.
        """
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO reconciliation_log
                    (ts, check_name, tier, symbol, trade_id,
                     description, action_taken, success)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, check_name, tier, symbol, trade_id,
                 description, action_taken, 1 if success else 0),
            )

    def insert_screener_result(
        self,
        signal_id: str,
        score: int,
        tier: str,
        status: str,
        step_results_json: str,
        latencies_json: str,
        market_data_snapshot_json: str,
        ts: str,
    ) -> None:
        """
        Persist one screening decision to screener_results (SS5, SS6).

        All JSON fields are pre-serialized strings. Called after every
        secondary_screener.screen() call for P18 analytics.
        """
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO screener_results
                    (signal_id, score, tier, status,
                     step_results, latencies, market_data_snapshot, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (signal_id, score, tier, status,
                 step_results_json, latencies_json, market_data_snapshot_json, ts),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # SmartTgtManager helpers (ST14 + ST15)
    # ─────────────────────────────────────────────────────────────────────────

    def get_co_entry_order_for_trade(
        self, trade_id: str
    ) -> Optional[sqlite3.Row]:
        """
        Return the CO ENTRY order row for a trade, or None if not found (ST14).

        Filters: leg='ENTRY' AND variety='co'. Returns first match (LIMIT 1).
        Used by SmartTgtManager to look up the broker_order_id for CO
        trigger_price modification.
        """
        return self.fetch_one(
            """
            SELECT *
            FROM orders
            WHERE trade_id = ?
              AND leg = 'ENTRY'
              AND variety = 'co'
            LIMIT 1
            """,
            (trade_id,),
        )

    def insert_smart_tgt_state(
        self,
        trade_id: str,
        symbol: str,
        instrument_token: int,
        direction: str,
        entry_price: float,
        initial_sl: float,
        current_sl: float,
        qty: int,
        trigger_pct: float,
        step_pct: float,
        registered_at: str,
        best_price: Optional[float] = None,
        trail_count: int = 0,
        last_trail_ts: Optional[str] = None,
    ) -> None:
        """Insert a new smart_tgt_state row (ST15). Called on register_trade."""
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO smart_tgt_state
                    (trade_id, symbol, instrument_token, direction,
                     entry_price, initial_sl, current_sl, qty,
                     trigger_pct, step_pct, best_price,
                     trail_count, last_trail_ts, registered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id, symbol, instrument_token, direction,
                    entry_price, initial_sl, current_sl, qty,
                    trigger_pct, step_pct, best_price,
                    trail_count, last_trail_ts, registered_at,
                ),
            )

    def update_smart_tgt_state(
        self,
        trade_id: str,
        current_sl: float,
        trail_count: int,
        last_trail_ts: str,
        best_price: Optional[float],
    ) -> None:
        """Update trail progress fields after a confirmed SL modification (ST15)."""
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE smart_tgt_state
                SET current_sl   = ?,
                    trail_count  = ?,
                    last_trail_ts = ?,
                    best_price   = ?
                WHERE trade_id = ?
                """,
                (current_sl, trail_count, last_trail_ts, best_price, trade_id),
            )

    def delete_smart_tgt_state(self, trade_id: str) -> None:
        """Remove a smart_tgt_state row on trade unregistration (ST15)."""
        with self.transaction() as cur:
            cur.execute(
                "DELETE FROM smart_tgt_state WHERE trade_id = ?",
                (trade_id,),
            )

    def get_all_smart_tgt_states(self) -> List[sqlite3.Row]:
        """Return all smart_tgt_state rows. Used by start() for crash recovery (ST8)."""
        return self.fetch_all("SELECT * FROM smart_tgt_state")

    # ─────────────────────────────────────────────────────────────────────────
    # Startup-checks helpers (SC4, SC6 — read-only)
    # ─────────────────────────────────────────────────────────────────────────

    def get_session_row(self) -> Optional[sqlite3.Row]:
        """
        Return the single session row (id=1), or None if it has never been
        written. Used by detect_startup_scenario to determine COLD/WARM/CRASH/HALT
        scenario (G5a, SC4).
        """
        return self.fetch_one("SELECT * FROM session WHERE id = 1")

    def find_shutdown_event_for_date(self, date_iso: str) -> Optional[sqlite3.Row]:
        """
        Return the most recent SHUTDOWN system_event whose timestamp falls on
        date_iso (YYYY-MM-DD), or None if no clean shutdown was recorded for
        that date. Used by detect_startup_scenario (G5a, SC4).

        Comparison: SUBSTR(timestamp, 1, 10) = date_iso.
        This works for ISO-8601 IST timestamps stored in the DB.
        """
        return self.fetch_one(
            """
            SELECT * FROM system_events
            WHERE event_type = 'SHUTDOWN'
              AND SUBSTR(timestamp, 1, 10) = ?
            ORDER BY event_id DESC
            LIMIT 1
            """,
            (date_iso,),
        )

    def insert_system_event(
        self,
        event_type: str,
        timestamp: str,
        scenario: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        """
        Append a row to system_events. Called by main.py for STARTUP, SHUTDOWN,
        CRASH_DETECTED, KILL_SWITCH, RECOVERY, CONFIG_DIFF events (G5a, SC15).

        Not called by startup_checks itself — state mutations are the caller's
        responsibility per SC15.
        """
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO system_events (timestamp, event_type, scenario, details)
                VALUES (?, ?, ?, ?)
                """,
                (timestamp, event_type, scenario, details),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Daily-report query helpers (DR8)
    # All are read-only SELECT queries scoped to a single YYYY-MM-DD date.
    # Return list[dict] so callers don't depend on sqlite3.Row internals.
    # ─────────────────────────────────────────────────────────────────────────

    def get_signals_for_date(self, date_iso: str) -> List[dict]:
        """Return all signals whose received_at falls on date_iso (DR8)."""
        rows = self.fetch_all(
            "SELECT * FROM signals WHERE DATE(received_at) = ?",
            (date_iso,),
        )
        return [dict(r) for r in rows]

    def get_trades_for_date(self, date_iso: str) -> List[dict]:
        """Return all trades created on date_iso (DR8)."""
        rows = self.fetch_all(
            "SELECT * FROM trades WHERE DATE(created_at) = ?",
            (date_iso,),
        )
        return [dict(r) for r in rows]

    def get_orders_for_date(self, date_iso: str) -> List[dict]:
        """Return all orders placed on date_iso (DR8)."""
        rows = self.fetch_all(
            "SELECT * FROM orders WHERE DATE(placed_at) = ?",
            (date_iso,),
        )
        return [dict(r) for r in rows]

    def get_fm_ledger_for_date(self, date_iso: str) -> List[dict]:
        """
        Return all fm_ledger rows for date_iso (DR8).

        BL-5: renamed from get_capital_ledger_for_date (the original name was
        a misnomer — the underlying query was always against fm_ledger, and
        the legacy capital_ledger table was never written).
        """
        rows = self.fetch_all(
            "SELECT * FROM fm_ledger WHERE DATE(ts) = ?",
            (date_iso,),
        )
        return [dict(r) for r in rows]

    def get_system_events_for_date(self, date_iso: str) -> List[dict]:
        """Return all system_events rows for date_iso (DR8)."""
        rows = self.fetch_all(
            "SELECT * FROM system_events WHERE DATE(timestamp) = ?",
            (date_iso,),
        )
        return [dict(r) for r in rows]

    def get_reconciliation_log_for_date(self, date_iso: str) -> List[dict]:
        """Return all reconciliation_log rows for date_iso (DR8)."""
        rows = self.fetch_all(
            "SELECT * FROM reconciliation_log WHERE DATE(ts) = ?",
            (date_iso,),
        )
        return [dict(r) for r in rows]

    def get_screener_results_for_date(self, date_iso: str) -> List[dict]:
        """Return all screener_results rows for date_iso (DR8)."""
        rows = self.fetch_all(
            "SELECT * FROM screener_results WHERE DATE(ts) = ?",
            (date_iso,),
        )
        return [dict(r) for r in rows]

    # ─────────────────────────────────────────────────────────────────────────
    # Innings helpers (SH9, SH10)
    # Used by shadow_tracker for per-inning persistence and reporting.
    # ─────────────────────────────────────────────────────────────────────────

    def insert_inning(self, inning) -> None:
        """
        Persist a new Inning row to the innings table (SH10).

        `inning` must be an Inning dataclass instance (duck-typed to avoid
        circular import from orders/shadow_tracker.py into core/).

        is_real stored as INTEGER 1/0. Raises sqlite3.IntegrityError on
        UNIQUE(trade_id, inning_number) violation (should not happen in normal
        flow).
        """
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO innings
                    (trade_id, inning_number, symbol, direction,
                     entry_price, entry_ts, sl_price, tgt_price,
                     exit_price, exit_ts, exit_reason, duration_sec,
                     pnl_pct, pnl_per_share, is_real)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inning.trade_id,
                    inning.inning_number,
                    inning.symbol,
                    inning.direction,
                    inning.entry_price,
                    inning.entry_ts.isoformat() if hasattr(inning.entry_ts, "isoformat") else str(inning.entry_ts),
                    inning.sl_price,
                    inning.tgt_price,
                    inning.exit_price,
                    inning.exit_ts.isoformat() if (inning.exit_ts is not None and hasattr(inning.exit_ts, "isoformat")) else inning.exit_ts,
                    inning.exit_reason,
                    inning.duration_sec,
                    inning.pnl_pct,
                    inning.pnl_per_share,
                    1 if inning.is_real else 0,
                ),
            )

    def update_inning_close(
        self,
        trade_id: str,
        inning_number: int,
        exit_price: float,
        exit_ts: str,
        exit_reason: str,
        duration_sec: int,
        pnl_pct: float,
        pnl_per_share: float,
    ) -> None:
        """
        Populate closure fields on an existing innings row (SH10).

        Called by shadow_tracker._close_inning() after SL/TGT/EOD hit detected.
        exit_ts should be ISO-8601 IST string.
        """
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE innings
                SET exit_price   = ?,
                    exit_ts      = ?,
                    exit_reason  = ?,
                    duration_sec = ?,
                    pnl_pct      = ?,
                    pnl_per_share = ?
                WHERE trade_id = ? AND inning_number = ?
                """,
                (
                    exit_price, exit_ts, exit_reason,
                    duration_sec, pnl_pct, pnl_per_share,
                    trade_id, inning_number,
                ),
            )

    def get_innings_for_trade(self, trade_id: str) -> List[dict]:
        """
        Return all innings for a trade_id, ordered by inning_number (SH10).

        Returns list[dict] so callers don't depend on sqlite3.Row internals.
        is_real returned as int (1/0) — caller converts to bool as needed.
        """
        rows = self.fetch_all(
            """
            SELECT * FROM innings
            WHERE trade_id = ?
            ORDER BY inning_number
            """,
            (trade_id,),
        )
        return [dict(r) for r in rows]

    def get_innings_for_date(self, date_iso: str) -> List[dict]:
        """
        Return all innings whose entry_ts starts with date_iso (SH10).

        date_iso: YYYY-MM-DD. Ordered by (trade_id, inning_number).
        Returns list[dict] for report-layer use.
        """
        rows = self.fetch_all(
            """
            SELECT * FROM innings
            WHERE substr(entry_ts, 1, 10) = ?
            ORDER BY trade_id, inning_number
            """,
            (date_iso,),
        )
        return [dict(r) for r in rows]

    def get_inning_summary_by_date(self, date_iso: str) -> List[dict]:
        """
        Return flat per-inning rows for date_iso joined with trades+signals (DR-U3).

        Joins innings -> trades -> signals to attach signal_id and scanner_name.
        Returns list[dict] ordered by (trade_id, inning_number).
        The caller (daily_review) pivots this flat list into per-trade rows.
        """
        rows = self.fetch_all(
            """
            SELECT
                i.trade_id,
                t.signal_id,
                i.symbol,
                i.direction,
                s.scanner       AS scanner_name,
                i.inning_number,
                i.entry_price,
                i.entry_ts,
                i.sl_price,
                i.tgt_price,
                i.exit_price,
                i.exit_ts,
                i.exit_reason,
                i.pnl_pct,
                i.duration_sec,
                i.is_real
            FROM innings i
            JOIN trades  t ON i.trade_id  = t.trade_id
            JOIN signals s ON t.signal_id = s.signal_id
            WHERE substr(i.entry_ts, 1, 10) = ?
            ORDER BY i.trade_id, i.inning_number
            """,
            (date_iso,),
        )
        return [dict(r) for r in rows]

    # ─────────────────────────────────────────────────────────────────────────
    # gate_state helpers (Audit 4.4 — entry-gate rehydration)
    # ─────────────────────────────────────────────────────────────────────────

    def insert_gate_state(self, entry: dict) -> None:
        """
        Persist an EntryGate WatchEntry. Idempotent INSERT OR REPLACE so a
        re-add of the same signal_id (e.g., after a crash mid-add) does not
        raise. The caller is expected to pass the WatchEntry as a dict.
        """
        keys = (
            "signal_id", "symbol", "direction", "trigger_price",
            "entry_price", "sl_price", "tgt_price",
            "tolerance_pct", "timeout_sec",
            "strategy_name", "tier", "scanner_name", "intent",
            "added_at", "extras_json",
        )
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO gate_state
                  (signal_id, symbol, direction, trigger_price,
                   entry_price, sl_price, tgt_price,
                   tolerance_pct, timeout_sec,
                   strategy_name, tier, scanner_name, intent,
                   added_at, extras_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(entry.get(k) for k in keys),
            )

    def delete_gate_state(self, signal_id: str) -> None:
        """Remove a gate_state row. Idempotent: missing row is a no-op."""
        with self.transaction() as cur:
            cur.execute(
                "DELETE FROM gate_state WHERE signal_id = ?", (signal_id,),
            )

    def get_all_gate_state(self) -> list[dict]:
        """Return every persisted gate_state row as a list of dicts."""
        with self.transaction() as cur:
            cur.execute("SELECT * FROM gate_state ORDER BY added_at ASC")
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def __repr__(self) -> str:
        return f"StateStore(db_path={self._db_path!r})"
