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

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple

from core.exceptions import StateError
from core import migrations
from core import db_connect
from core.db_connect import ANALYTICS_TABLES

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    # H-17 SKIP: state_store sits below time_authority in the layering; importing
    # core.time_authority here would invert the dependency direction. Leaving as
    # datetime.now(_IST) is the deliberate exemption.
    return datetime.now(_IST).isoformat()


def _parse_ist_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored timestamp to a tz-aware IST datetime.

    Handles BOTH stored formats: naive-IST 'YYYY-MM-DD HH:MM:SS' (the 15:40
    candle backfill) AND ISO-8601 with a +05:30 offset (trade entry/exit
    times). A naive value is assumed IST; an aware value is converted to IST.
    Returns None if empty/unparseable. Used ONLY for excursion-window
    comparison (compute_trade_excursions) — it does not change how timestamps
    are stored anywhere.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace(" ", "T"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_IST)
    return dt.astimezone(_IST)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

EXPECTED_SCHEMA_VERSION = 41  # W0 (report-redesign foundation): +config_snapshots (resolved-config-per-date). Pure addition — no rebuild.

DEFAULT_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# SQLite PRAGMAs applied to every connection
_CONNECTION_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = FULL",        # FIX-076: FULL guarantees crash survivability on networked block storage
    "PRAGMA foreign_keys = ON",         # Per-connection; SQLite requires this
    "PRAGMA temp_store = MEMORY",
    "PRAGMA busy_timeout = 30000",      # Wait up to 30s on a locked DB (FIX-006)
)


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class StateStoreError(StateError):
    """
    Base exception for state_store errors.

    Inherits from core.exceptions.StateError so the top-level
    `except TradingSystemError` safety net in main.py catches schema /
    transaction failures (E1, E3). 2026-04-26 audit EXC-1.
    """


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
            timeout=30,                 # Connection-level wait for DB lock (FIX-006)
        )
        # Row factory: tuple-by-default but accessible by column name as well
        conn.row_factory = sqlite3.Row

        for pragma in _CONNECTION_PRAGMAS:
            conn.execute(pragma)

        # O6: ATTACH analytics.db so unqualified candles/system_metrics queries
        # resolve to the relocated tables. The analytics tables are created by
        # _initialize_schema (init_analytics_schema) before the first attach.
        db_connect.attach_analytics(conn, self._db_path)

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

    def checkpoint_wal(self) -> dict:
        """
        FIX-131 Item 23: WAL checkpoint using PASSIVE mode for routine use.

        PASSIVE allows readers to proceed; moves completed WAL frames to main DB.
        Safe to call from any thread including cron-triggered scripts.
        Intended for: graceful shutdown + 16:00 IST cron job.

        Returns dict with checkpoint stats: {busy, log, checkpointed}
        """
        conn = self._get_conn()
        cursor = conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        row = cursor.fetchone()
        return {
            "busy": row[0] if row else 0,
            "log": row[1] if row else 0,
            "checkpointed": row[2] if row else 0,
        }

    def checkpoint(self, live_feed=None) -> dict:
        """
        FIX-047: Execute WAL checkpoint(TRUNCATE) to reclaim disk space.
        FIX-088: Skip checkpoint if called from ticker thread (prevents GIL block → TCP Zero Window).

        Called by EOD squareoff after all positions closed. Checkpoint moves
        WAL entries back to main DB file and truncates WAL to zero bytes.
        Safe to call with active connections (WAL mode allows concurrent readers).

        Args:
            live_feed: Optional LiveFeedManager for thread identity check (FIX-088)

        Returns dict with checkpoint stats: {busy, log, checkpointed}
        """
        # FIX-088: Guard against checkpoint on ticker thread
        if live_feed is not None:
            import threading
            ticker_thread_id = getattr(live_feed, '_ticker_thread_id', None)
            if ticker_thread_id is not None and threading.get_ident() == ticker_thread_id:
                # Called from ticker thread - skip checkpoint
                import logging
                log = logging.getLogger("state_store")
                log.warning(
                    "checkpoint() called from ticker thread - skipping to prevent GIL block (FIX-088)"
                )
                return {"busy": 0, "log": 0, "checkpointed": 0, "skipped": True}

        conn = self._get_conn()
        cursor = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        row = cursor.fetchone()
        result = {
            "busy": row[0] if row else 0,
            "log": row[1] if row else 0,
            "checkpointed": row[2] if row else 0,
        }
        return result
    
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

        # O6: create analytics.db and its tables BEFORE opening (and ATTACHing
        # from) the main connection, so the relocation step and any analytics
        # write have their target tables present. Idempotent (CREATE IF NOT
        # EXISTS); a no-op once analytics.db is established.
        db_connect.init_analytics_schema(self._db_path)

        conn = self._get_conn()

        # Capture the pre-existing version BEFORE applying schema.sql. The
        # schema's trailing INSERT OR REPLACE bumps schema_version to the
        # latest, which would otherwise mask the DB's real starting version.
        # None for a brand-new database.
        old_version = self._read_existing_version(conn)

        # Migrate BEFORE applying schema.sql. An existing DB created before a
        # constraint/FK/generated-column was added needs its affected tables
        # rebuilt — CREATE TABLE IF NOT EXISTS silently skips an existing table,
        # and SQLite cannot ALTER in a CHECK, a FOREIGN KEY, or a STORED
        # generated column. run_migrations re-applies schema.sql's current
        # definition for those tables (extracted from the schema text, so it
        # does not depend on executescript having run). Idempotent.
        #
        # Order matters: schema.sql's trailing INSERT bumps schema_version, so
        # it must run AFTER a successful migration. If a migration fails (e.g.
        # legacy data violates a new CHECK) it raises and rolls back, leaving
        # the version untouched so the migration is retried on next startup —
        # never a version that claims success over an un-rebuilt table.
        if old_version is not None and old_version < EXPECTED_SCHEMA_VERSION:
            mig_log = logging.getLogger("state_store.migrations")
            migrations.run_migrations(
                conn,
                schema_sql,
                old_version,
                EXPECTED_SCHEMA_VERSION,
                mig_log,
            )
            # O6 (v28): move candles/system_metrics[_daily] out of the main DB
            # into the attached analytics.db. Idempotent — skips tables already
            # relocated. Runs after the in-place rebuilds above and before the
            # schema.sql re-apply (which no longer defines these tables).
            migrations.relocate_analytics_tables(conn, ANALYTICS_TABLES, mig_log)

        # Execute the entire schema as one script. Creates any missing tables
        # (with their current constraints) on a fresh DB; a no-op on tables that
        # already exist (incl. ones just rebuilt above); always bumps the
        # schema_version row to the latest.
        conn.executescript(schema_sql)

        # Verify version
        version = self.get_schema_version()
        if version != EXPECTED_SCHEMA_VERSION:
            raise SchemaVersionMismatch(
                f"Database schema version is {version}, "
                f"code expects {EXPECTED_SCHEMA_VERSION}. "
                f"Run migrations or use a fresh database."
            )

    def _read_existing_version(self, conn: sqlite3.Connection) -> Optional[int]:
        """
        Return the schema_version stored in an EXISTING DB, or None if this is
        a brand-new database (no schema_meta table / no schema_version row yet).
        Called before schema.sql is applied, so it must not assume any table.
        """
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            return None  # schema_meta table doesn't exist yet — fresh DB
        if row is None:
            return None
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return None
    
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

    def count_active_positions(self) -> int:
        """
        Count ALL trades with live (or about-to-be-live) exposure in a SINGLE
        atomic query: status OPEN, PARTIAL, or PENDING_FILL.

        Bug E (P0 2026-06-15): the risk_engine OPEN_POSITIONS check used to sum
        two separate queries — count_open_positions() (OPEN/PARTIAL) and
        count_in_flight_orders() (PENDING_FILL). A trade that transitioned
        PENDING_FILL -> OPEN *between* the two queries was counted in NEITHER
        (the first query ran before it became OPEN, the second after it left
        PENDING_FILL), so the position cap could be exceeded. This single query
        closes that window. See risk_engine OPEN_POSITIONS check (RE5).
        """
        row = self.fetch_one(
            "SELECT COUNT(*) AS n FROM trades "
            "WHERE status IN ('OPEN', 'PARTIAL', 'PENDING_FILL')"
        )
        return int(row["n"]) if row else 0

    def get_trades_by_status_and_symbol(
        self, statuses: tuple[str, ...], symbol: str
    ) -> List[sqlite3.Row]:
        """
        FIX-181: return trades for `symbol` whose status is in `statuses`,
        newest first. Used by the reconciler to tell a genuine broker-side
        orphan (no local record) apart from one that matches a local in-flight
        (PENDING_FILL/PENDING) trade whose entry filled at the broker — the
        latter must be flattened on HARD_KILL, not abandoned (GICRE incident).
        """
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        return self.fetch_all(
            f"SELECT * FROM trades "
            f"WHERE symbol = ? AND status IN ({placeholders}) "
            f"ORDER BY created_at DESC",
            (symbol, *statuses),
        )

    # FIX-181: a trade "counts" against the daily limit only if it actually
    # reached the broker as a live (or live-bound) position. FAILED/CANCELLED/
    # REJECTED rows never opened exposure and must not burn the daily quota —
    # otherwise a burst of broker rejections (e.g. BHARATGEAR-type FAILED on
    # first-live-day) silently exhausts max_daily_trades and halts trading.
    _EXECUTED_TRADE_STATUSES = (
        "PENDING_FILL", "OPEN", "PARTIAL", "EXITING", "CLOSED", "CLOSED_MANUAL",
    )

    def count_trades_today(self, date_iso: str) -> int:
        """
        Count EXECUTED trade rows created on the given IST date (YYYY-MM-DD).

        FIX-181: only statuses in _EXECUTED_TRADE_STATUSES are counted; FAILED,
        CANCELLED and REJECTED rows (which never opened a position) are excluded.
        Matches against SUBSTR(created_at, 1, 10) — works with ISO-8601 IST
        strings stored in the DB (e.g., "2026-04-14T09:30:00+05:30").
        Counts one row per trade (not per signal). Used by risk_engine
        DAILY_TRADES check (RE5).
        """
        placeholders = ",".join("?" for _ in self._EXECUTED_TRADE_STATUSES)
        row = self.fetch_one(
            f"SELECT COUNT(*) AS n FROM trades "
            f"WHERE SUBSTR(created_at, 1, 10) = ? "
            f"AND status IN ({placeholders})",
            (date_iso, *self._EXECUTED_TRADE_STATUSES),
        )
        return int(row["n"]) if row else 0

    # Bug B (2026-06-19): the subset of executed statuses that are NO LONGER
    # in-flight — i.e. _EXECUTED_TRADE_STATUSES minus PENDING_FILL. A trade in
    # one of these has had its fund_manager reservation popped (commit at fill /
    # release on exit), so it is NOT in _reservations. count_live_reservations()
    # covers the in-flight remainder (reserved-not-placed + PENDING_FILL), so
    # settled + live_reservations partition today's quota usage with no overlap.
    # Used by the reservation-aware DAILY_TRADES check (risk_engine RE5).
    _SETTLED_TRADE_STATUSES = (
        "OPEN", "PARTIAL", "EXITING", "CLOSED", "CLOSED_MANUAL",
    )

    def count_settled_trades_today(self, date_iso: str) -> int:
        """
        Count today's EXECUTED trades that are no longer in-flight (Bug B):
        statuses in _SETTLED_TRADE_STATUSES (= _EXECUTED_TRADE_STATUSES minus
        PENDING_FILL). Mirrors count_trades_today's date match. This is the
        DB-truth half of the reservation-aware daily cap; the other half is
        fund_manager.count_live_reservations() (the PENDING_FILL +
        reserved-not-placed in-flight entries). Keeping PENDING_FILL OUT here is
        deliberate: it is double-counted by the live reservation it still holds,
        and counting it in both terms would over-tighten the cap.
        """
        placeholders = ",".join("?" for _ in self._SETTLED_TRADE_STATUSES)
        row = self.fetch_one(
            f"SELECT COUNT(*) AS n FROM trades "
            f"WHERE SUBSTR(created_at, 1, 10) = ? "
            f"AND status IN ({placeholders})",
            (date_iso, *self._SETTLED_TRADE_STATUSES),
        )
        return int(row["n"]) if row else 0

    # ── SLICE2.5-PHASE-3 (A): delivery-scoped (CNC) count caps ─────────────────
    # Product-keyed via the ENTRY-leg order: a trade is "delivery" iff its ENTRY
    # order's product is 'CNC' (the actual product written at placement; precedent
    # = the MIS/CO ENTRY-join at get_intraday_entry_orders / get_all_open_trades).
    # COUNT(DISTINCT trade_id) so a multi-leg ENTRY (SCALE) counts the trade once.
    # Inert while force_intraday_only=true: no CNC entry orders are ever written
    # then, so both methods return 0.

    def count_open_delivery_positions(self) -> int:
        """Count concurrent open DELIVERY (CNC) positions: trades in OPEN/PARTIAL/
        PENDING_FILL whose ENTRY-leg order product is 'CNC'. Used by the risk_engine
        OPEN_POSITIONS delivery branch (SLICE2.5-PHASE-3 A)."""
        row = self.fetch_one(
            "SELECT COUNT(DISTINCT t.trade_id) AS n FROM trades t "
            "JOIN orders o ON o.trade_id = t.trade_id "
            "                 AND o.leg = 'ENTRY' AND o.product = 'CNC' "
            "WHERE t.status IN ('OPEN', 'PARTIAL', 'PENDING_FILL')"
        )
        return int(row["n"]) if row else 0

    def count_daily_delivery_trades(self, date_iso: str) -> int:
        """Count today's EXECUTED DELIVERY (CNC) entries: trades created on date_iso
        in _EXECUTED_TRADE_STATUSES whose ENTRY-leg order product is 'CNC'. Mirrors
        count_trades_today's date/status match. Used by the risk_engine DAILY_TRADES
        delivery branch (SLICE2.5-PHASE-3 A)."""
        placeholders = ",".join("?" for _ in self._EXECUTED_TRADE_STATUSES)
        row = self.fetch_one(
            f"SELECT COUNT(DISTINCT t.trade_id) AS n FROM trades t "
            f"JOIN orders o ON o.trade_id = t.trade_id "
            f"                 AND o.leg = 'ENTRY' AND o.product = 'CNC' "
            f"WHERE SUBSTR(t.created_at, 1, 10) = ? "
            f"AND t.status IN ({placeholders})",
            (date_iso, *self._EXECUTED_TRADE_STATUSES),
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

    def get_active_position_direction(self, symbol: str) -> Optional[str]:
        """
        Return the direction (LONG or SHORT) of any active trade for symbol,
        or None if no active position exists.

        Active = status PENDING_FILL, OPEN, or PARTIAL.

        Used by risk_engine CONTRARY_POSITION check (FIX-019 wash trade prevention).
        If multiple active trades exist (should not happen post-duplicate check),
        returns the direction of the first row found.
        """
        row = self.fetch_one(
            """
            SELECT direction FROM trades
            WHERE symbol = ?
              AND status IN ('PENDING_FILL', 'OPEN', 'PARTIAL')
            LIMIT 1
            """,
            (symbol,),
        )
        return row["direction"] if row else None

    def recent_trade_pnls(self, n: int, today: Optional[str] = None) -> List[float]:
        """
        Return the net_pnl values of the n most recently closed trades,
        ordered most-recent-first. Excludes rows where net_pnl IS NULL.
        Used by risk_engine CONSECUTIVE_LOSSES check (RE5, RE10).

        FIX-183: when ``today`` (an IST date string, YYYY-MM-DD) is given, the
        streak is scoped to that trading day only — matching the daily reset of
        DAILY_LOSS / DAILY_TRADES. A consecutive-loss streak that carried across
        days was a deadlock: it blocks entries, and breaking the streak requires
        a winning trade, which an entry block makes impossible (the morning after
        a 2-loss EOD, every signal was rejected). Day-scoping (SUBSTR(exit_time,
        1, 10) mirrors count_trades_today) keeps the within-day circuit breaker
        while letting each new day start clean. ``today=None`` preserves the
        legacy cross-day behaviour for callers/tests that want it.
        """
        if today is not None:
            rows = self.fetch_all(
                """
                SELECT net_pnl FROM trades
                WHERE status IN ('CLOSED', 'CLOSED_MANUAL') AND net_pnl IS NOT NULL
                  AND SUBSTR(exit_time, 1, 10) = ?
                ORDER BY exit_time DESC
                LIMIT ?
                """,
                (today, n),
            )
        else:
            rows = self.fetch_all(
                """
                SELECT net_pnl FROM trades
                WHERE status IN ('CLOSED', 'CLOSED_MANUAL') AND net_pnl IS NOT NULL
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

    def get_orphaned_pending_trades(self) -> List[sqlite3.Row]:
        """
        FIX-071 Part B: Find PENDING trades that never completed the broker call.

        These are trades where:
        - trades.status = 'PENDING' (set by FIX-071 Part A before broker call)
        - Either: no orders row exists yet (crashed before engine.execute() returned)
        - Or: orders row exists but order_id is NULL (shouldn't happen but defensive)

        Returns rows with: trade_id, symbol, direction, created_at, status

        Used by OrderMonitor.rehydrate_from_store() to clean up orphaned
        placement attempts on startup. These trades cannot be monitored (no
        broker_order_id to poll) and must be marked FAILED with capital released.
        """
        return self.fetch_all(
            """
            SELECT
                t.trade_id,
                t.symbol,
                t.direction,
                t.created_at,
                t.status
            FROM trades t
            LEFT JOIN orders o
              ON o.trade_id = t.trade_id
             AND o.leg = 'ENTRY'
            WHERE t.status = 'PENDING'
              AND (o.order_id IS NULL OR o.order_id = '')
            ORDER BY t.created_at
            """
        )

    def cancel_stale_paper_orders(self, today_iso: str) -> int:
        """
        Paper mode startup cleanup: mark non-terminal orders from PREVIOUS
        days as CANCELLED. After restart, paper_fills dict is empty so these
        orders can never fill. Without cleanup the reconciler logs ORPHAN_ORDER
        every cycle (~7500 warnings/day).

        Returns the number of orders cancelled.
        """
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE orders
                SET status = 'CANCELLED', updated_at = ?
                WHERE status IN ('PENDING', 'SUBMITTED', 'OPEN', 'PARTIAL',
                                 'TRIGGER_PENDING')
                  AND date(placed_at) < date(?)
                """,
                (_now_ist_iso(), today_iso),
            )
            return cursor.rowcount

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

    def get_stuck_exiting_trades(self, older_than_iso: str) -> List[sqlite3.Row]:
        """
        Task 4 (2026-06-19): return trades stuck in EXITING whose last update
        (the moment they entered EXITING) is at or before `older_than_iso` —
        i.e. EXITING for longer than the reconciler's stuck timeout.

        Same JOIN/columns as get_all_open_trades so order_reconciler can feed a
        stuck row straight into CHECK 1 (_check1_manual_close) unchanged. EXITING
        is set by a HARD_KILL / emergency flatten (Bug A, FIX-190) and is meant to
        be transient (EXITING -> CLOSED/CLOSED_MANUAL); a process death mid-exit
        leaves it stuck with locked capital + orphan SL/TGT (the 19-Jun incident),
        which CHECK1/G5b never resolve (they only see OPEN/PARTIAL/PENDING_FILL).
        Timestamps are ISO-8601 IST (identical +05:30 offset), so the lexical
        `<=` compares chronologically. Sorted by symbol (Foundation Rule 3.7).
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
            WHERE t.status = 'EXITING'
              AND t.updated_at <= ?
            ORDER BY t.symbol
            """,
            (older_than_iso,),
        )

    def revert_exiting_to_open(self, trade_id: str) -> bool:
        """
        Task 4 (2026-06-19): flip a stuck EXITING trade back to OPEN, but only if
        it is still EXITING (atomic guard against a concurrent finalize). Used by
        order_reconciler when a trade stuck in EXITING STILL has a live broker
        position — handing it back to normal management (SL/TGT, EOD squareoff, or
        an active kill switch's flatten loop) is the generalized form of the
        19-Jun manual recovery. Returns True if the flip happened, False if the
        trade had already left EXITING. Capital is intentionally untouched: a live
        position still legitimately holds its reservation/used margin.
        """
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE trades
                SET status = 'OPEN',
                    updated_at = ?
                WHERE trade_id = ?
                  AND status = 'EXITING'
                """,
                (_now_ist_iso(), trade_id),
            )
            return cur.rowcount > 0

    def adopt_recovery_trade_to_open(self, trade_id: str) -> bool:
        """A-1/E-1: atomically flip a recovery-state trade to OPEN when its broker
        entry is ADOPTED (timeout UNKNOWN_IN_FLIGHT / crash PENDING / PENDING_FILL).

        Mirrors the mark_trade_manually_closed atomic guard: returns True iff THIS
        call won the transition, so concurrent reconciler cycles (or the crash +
        timeout feeds converging) can never adopt the same trade twice. Sets
        recovered_flag=1 for the audit trail. Capital is committed by the caller
        AFTER a True return (exactly-once)."""
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE trades
                SET status = 'OPEN',
                    recovered_flag = 1,
                    updated_at = ?
                WHERE trade_id = ?
                  AND status IN ('UNKNOWN_IN_FLIGHT', 'PENDING', 'PENDING_FILL')
                """,
                (_now_ist_iso(), trade_id),
            )
            return cur.rowcount > 0

    def fail_recovery_trade(self, trade_id: str) -> bool:
        """A-1/E-1: atomically mark a recovery-state trade FAILED — ONLY to be called
        once broker ABSENCE is confirmed (never on a blind/unreachable poll). Guarded
        so a late adoption cannot be clobbered and capital is released exactly once by
        the caller AFTER a True return."""
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE trades
                SET status = 'FAILED',
                    updated_at = ?
                WHERE trade_id = ?
                  AND status IN ('UNKNOWN_IN_FLIGHT', 'PENDING')
                """,
                (_now_ist_iso(), trade_id),
            )
            return cur.rowcount > 0

    # ── TGT retry (Task, 2026-06-19) ─────────────────────────────────────────
    # A LIMIT_TRIPLE trade whose SL is live but whose TGT could not be placed
    # (FIX-190 Bug C) is flagged needs_tgt_retry=1; TGTRetryManager re-attempts
    # the TGT on a backoff schedule and clears the flag on success / give-up.

    def mark_needs_tgt_retry(self, trade_id: str) -> bool:
        """Flag a trade as owing a TGT placement (Bug C: SL live, TGT not placed).
        Resets the retry counter and stamps tgt_last_retry_at = now (the failed
        placement time) so the first retry waits one backoff interval. Returns
        True if a row was updated."""
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE trades
                SET needs_tgt_retry = 1,
                    tgt_retry_count = 0,
                    tgt_last_retry_at = ?,
                    updated_at = ?
                WHERE trade_id = ?
                """,
                (_now_ist_iso(), _now_ist_iso(), trade_id),
            )
            return cur.rowcount > 0

    def clear_needs_tgt_retry(self, trade_id: str) -> bool:
        """Clear the TGT-retry flag (on success or give-up). tgt_retry_count is
        intentionally preserved for forensics. Returns True if a row changed."""
        with self.transaction() as cur:
            cur.execute(
                "UPDATE trades SET needs_tgt_retry = 0, updated_at = ? WHERE trade_id = ?",
                (_now_ist_iso(), trade_id),
            )
            return cur.rowcount > 0

    def bump_tgt_retry(self, trade_id: str) -> int:
        """Record a TGT retry attempt: increment tgt_retry_count and stamp
        tgt_last_retry_at = now. Returns the NEW count (0 if the trade is gone)."""
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE trades
                SET tgt_retry_count = tgt_retry_count + 1,
                    tgt_last_retry_at = ?,
                    updated_at = ?
                WHERE trade_id = ?
                """,
                (_now_ist_iso(), _now_ist_iso(), trade_id),
            )
            row = cur.execute(
                "SELECT tgt_retry_count FROM trades WHERE trade_id = ?", (trade_id,)
            ).fetchone()
            return int(row["tgt_retry_count"]) if row else 0

    def get_tgt_retry_candidates(self) -> List[sqlite3.Row]:
        """Trades currently owing a TGT placement (needs_tgt_retry=1) and still
        live (OPEN/PARTIAL). Lightweight scheduling view for TGTRetryManager —
        the placement path re-reads full context via get_trade_for_tgt_retry."""
        return self.fetch_all(
            """
            SELECT trade_id, symbol, tgt_retry_count, tgt_last_retry_at
            FROM trades
            WHERE needs_tgt_retry = 1
              AND status IN ('OPEN', 'PARTIAL')
            ORDER BY tgt_last_retry_at
            """
        )

    def get_trade_for_tgt_retry(self, trade_id: str) -> Optional[sqlite3.Row]:
        """Full context for a TGT retry: trade fields + the ENTRY order's product
        (so intent/exit-side can be derived). Returns None if the trade is gone.
        Used by OrderPlacer.retry_tgt_for_trade, which re-checks status + SL
        standing immediately before placing (the manager's snapshot may be stale)."""
        return self.fetch_one(
            """
            SELECT
                t.trade_id,
                t.signal_id,
                t.symbol,
                t.direction,
                t.qty_filled,
                t.entry_actual_price,
                t.sl_initial,
                t.tgt_initial,
                t.status,
                t.needs_tgt_retry,
                t.tgt_retry_count,
                t.tgt_risk_reward_applied,
                o.product
            FROM trades t
            LEFT JOIN orders o
              ON o.trade_id = t.trade_id
             AND o.leg = 'ENTRY'
            WHERE t.trade_id = ?
            """,
            (trade_id,),
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

    def mark_trade_manually_closed(self, trade_id: str) -> bool:
        """
        Mark a trade as CLOSED_MANUAL if it is still OPEN, PARTIAL or EXITING.

        Called by order_reconciler CHECK 1 when broker position is gone but
        the local trade is still live — indicating a manual broker close, an
        SL-hit not relayed to the system, or (Task 4, 2026-06-19) a trade left
        in EXITING by a HARD_KILL / emergency flatten that flattened at the
        broker but died before finalizing the DB. EXITING -> CLOSED_MANUAL is a
        valid terminal transition, so CHECK 1 can finalize a stuck EXITING trade
        directly (no more manual EXITING->OPEN flip needed).

        Returns True if the update happened, False if the trade was already
        in a terminal status (guards against double-release race with
        order_placer exit fill processing).
        """
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE trades
                SET status = 'CLOSED_MANUAL',
                    exit_reason = 'MANUAL',
                    updated_at = ?
                WHERE trade_id = ?
                  AND status IN ('OPEN', 'PARTIAL', 'EXITING')
                """,
                (_now_ist_iso(), trade_id),
            )
            return cur.rowcount > 0

    def record_manual_close_financials(
        self,
        trade_id: str,
        exit_price: float,
        net_pnl: float,
        exit_time: Optional[str] = None,
        charges: float = 0.0,
    ) -> bool:
        """
        Bug 4 (FIX-180): populate the trade row's exit financials after a
        CLOSED_MANUAL transition.

        order_reconciler CHECK 1 already resolves the real broker exit price
        and releases capital with the correct pnl via fm.release_used(), so the
        fm_ledger is right — but mark_trade_manually_closed() only set status +
        exit_reason, leaving exit_price/net_pnl/exit_time NULL. That made
        trades.net_pnl disagree with fm_ledger.pnl_delta and broke per-trade
        reporting. This writes those fields.

        RMS/manual closes pass costs=0.0 (see capital release call), so
        gross_pnl == net_pnl and charges == 0.0. Guarded to CLOSED_MANUAL rows
        only (idempotent; never clobbers a normal CLOSED row). exit_time is
        preserved if already set. Returns True if the row was updated.
        """
        ts = exit_time or _now_ist_iso()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE trades
                SET exit_price = ?,
                    exit_time  = COALESCE(exit_time, ?),
                    gross_pnl  = ?,
                    charges    = ?,
                    net_pnl    = ?,
                    updated_at = ?
                WHERE trade_id = ?
                  AND status = 'CLOSED_MANUAL'
                """,
                (exit_price, ts, net_pnl, charges, net_pnl, ts, trade_id),
            )
            return cur.rowcount > 0

    def get_pending_all_products(self) -> List[sqlite3.Row]:
        """
        Return all PENDING_FILL trades regardless of product.

        Like get_pending_intraday_orders() but covers CNC and all products.
        Each row: trade_id, signal_id, symbol, direction, broker_order_id, reservation_id.
        Used by reconciler for CHECK 6 (orphan order detection on pending trades).
        FIX-B: Added reservation_id for orphan auto-close capital release.
        """
        return self.fetch_all(
            """
            SELECT
                t.trade_id,
                t.signal_id,
                t.symbol,
                t.direction,
                o.order_id AS broker_order_id,
                t.reservation_id
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
        eligible_score: Optional[int] = None,
    ) -> None:
        """
        Persist one screening decision to screener_results (SS5, SS6).

        All JSON fields are pre-serialized strings. Called after every
        secondary_screener.screen() call for P18 analytics.
        v14: eligible_score is the per-strategy min_score threshold.
        """
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO screener_results
                    (signal_id, score, tier, status,
                     step_results, latencies, market_data_snapshot, ts,
                     eligible_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (signal_id, score, tier, status,
                 step_results_json, latencies_json, market_data_snapshot_json, ts,
                 eligible_score),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # S&R Detector V1 shadow log (SNR-DETECTOR-V1, schema v37)
    # ─────────────────────────────────────────────────────────────────────────

    # Insert column order — single source so the SQL and the value tuple cannot
    # drift. actual_*/win_loss/pnl/hypothetical_retest_result are EOD-backfilled
    # and intentionally absent here (default NULL).
    _SR_INSERT_COLS = (
        "signal_id", "symbol", "ts", "mode", "strategy", "direction", "score",
        "intended_entry", "actual_fill", "nearest_resistance_zone",
        "nearest_support_zone", "dist_to_resistance_pct", "dist_to_support_pct",
        "resistance_confidence", "support_confidence", "confluence_evidence",
        "breakout_volume", "flags", "would_wait_for_retest", "proposed_retest_entry",
        "proposed_retest_sl", "structure_status", "detector_version", "created_at",
    )

    def insert_sr_detector_result(self, row: dict) -> None:
        """
        Persist one shadow S&R observation (append-only). Imitates the
        screener_results write: explicit columns inside a transaction. The caller
        (sr_detector.detector._write) already guards against exceptions; the
        explicit column set means a missing key surfaces clearly in tests rather
        than a silent NULL.
        """
        cols = self._SR_INSERT_COLS
        placeholders = ", ".join("?" for _ in cols)
        values = tuple(row.get(c) for c in cols)
        with self.transaction() as cur:
            cur.execute(
                f"INSERT INTO sr_detector_results ({', '.join(cols)}) "
                f"VALUES ({placeholders})",
                values,
            )

    def get_sr_results_for_backfill(self, date_iso: str) -> list:
        """
        Return sr_detector_results rows for date_iso (by ts date) still awaiting
        outcome backfill (actual_result IS NULL). Used by the EOD backfill step.
        """
        return self.fetch_all(
            "SELECT id, signal_id, symbol, direction FROM sr_detector_results "
            "WHERE date(ts) = ? AND actual_result IS NULL",
            (date_iso,),
        )

    def update_sr_outcome(
        self,
        sr_id: int,
        *,
        actual_fill: Optional[float],
        actual_result: Optional[str],
        win_loss: Optional[str],
        pnl: Optional[float],
    ) -> None:
        """Backfill the EOD outcome columns for one sr_detector_results row."""
        with self.transaction() as cur:
            cur.execute(
                "UPDATE sr_detector_results "
                "SET actual_fill = ?, actual_result = ?, win_loss = ?, pnl = ? "
                "WHERE id = ?",
                (actual_fill, actual_result, win_loss, pnl, sr_id),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Candle persistence helpers (v14)
    # ─────────────────────────────────────────────────────────────────────────

    def insert_candle(
        self,
        symbol: str,
        instrument_token: int,
        ts: str,
        interval_sec: int,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: int = 0,
        is_synthetic: int = 0,
    ) -> None:
        """Persist one minute candle. Duplicate (token, ts, interval) is ignored."""
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO candles
                    (symbol, instrument_token, ts, interval_sec,
                     open, high, low, close, volume, is_synthetic)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol, instrument_token, ts, interval_sec,
                 open_, high, low, close, volume, is_synthetic),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Trade excursion helpers (v14)
    # ─────────────────────────────────────────────────────────────────────────

    def insert_trade_excursion(
        self,
        trade_id: str,
        mfe_price: Optional[float],
        mfe_pct: Optional[float],
        mae_price: Optional[float],
        mae_pct: Optional[float],
        entry_candle_open: Optional[float] = None,
        entry_candle_high: Optional[float] = None,
        entry_candle_low: Optional[float] = None,
        entry_candle_close: Optional[float] = None,
    ) -> None:
        """Write or update trade excursion row (v14). Upsert on trade_id."""
        from core.time_authority import now_ist
        ts = now_ist().isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO trade_excursions
                    (trade_id, mfe_price, mfe_pct, mae_price, mae_pct,
                     entry_candle_open, entry_candle_high,
                     entry_candle_low, entry_candle_close, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (trade_id, mfe_price, mfe_pct, mae_price, mae_pct,
                 entry_candle_open, entry_candle_high,
                 entry_candle_low, entry_candle_close, ts),
            )

    def compute_trade_excursions(
        self, trade_id: str,
    ) -> Optional[Dict]:
        """
        Compute MFE/MAE from candles table for a closed trade (v14).
        Returns dict with mfe_price/pct, mae_price/pct, entry candle OHLC,
        or None if insufficient data.

        SIGN CONVENTION (SIGNED — full information; confirmed 2026-06-28):
        MFE = best FAVOURABLE move vs entry; it MAY be negative if the trade
        never traded favourable (a never-green long / never-red short). MAE =
        worst ADVERSE move vs entry; it MAY be positive if it never went
        adverse. mfe_pct/mae_pct are direction-signed (LONG vs SHORT inverted)
        and NOT floored at zero — the literal best/worst excursion is kept.
        Reconstructed from 1-min candles, so sub-minute trades yield no row.

        DATETIME-SAFE COMPARISON (MFE/MAE Option B, 2026-06-28): candles.ts is
        stored naive-IST ('YYYY-MM-DD HH:MM:SS', the 15:40 EOD backfill) while
        trades.*_time are ISO-8601 +05:30. A lexical `ts BETWEEN` silently
        matched ZERO candles (' ' < 'T' at index 10) → the root cause of the
        empty trade_excursions table. We now parse BOTH sides to tz-aware
        Asia/Kolkata datetimes and compare as datetimes. The `date` column
        (indexed, == substr(ts,1,10)) is a cheap pre-filter; the parsed-datetime
        window is authoritative. Candle STORAGE format is unchanged — only the
        comparison here. The direction math below is unchanged.
        """
        trade = self.fetch_one(
            "SELECT symbol, direction, entry_actual_price, entry_time, exit_time "
            "FROM trades WHERE trade_id = ?",
            (trade_id,),
        )
        if not trade or not trade["entry_time"] or not trade["exit_time"]:
            return None

        entry_price = trade["entry_actual_price"]
        if not entry_price or entry_price <= 0:
            return None

        entry_dt = _parse_ist_dt(trade["entry_time"])
        exit_dt = _parse_ist_dt(trade["exit_time"])
        if entry_dt is None or exit_dt is None:
            return None

        candidates = self.fetch_all(
            """
            SELECT open, high, low, close, ts FROM candles
            WHERE symbol = ? AND date BETWEEN ? AND ?
            ORDER BY ts
            """,
            (trade["symbol"], entry_dt.date().isoformat(), exit_dt.date().isoformat()),
        )
        rows = []
        for r in candidates:
            cdt = _parse_ist_dt(r["ts"])
            if cdt is not None and entry_dt <= cdt <= exit_dt:
                rows.append(r)
        if not rows:
            return None

        direction = trade["direction"]
        highs = [r["high"] for r in rows]
        lows = [r["low"] for r in rows]
        max_high = max(highs)
        min_low = min(lows)

        if direction == "LONG":
            mfe_price = max_high
            mae_price = min_low
        else:
            mfe_price = min_low
            mae_price = max_high

        mfe_pct = round((mfe_price - entry_price) / entry_price * 100, 4)
        mae_pct = round((mae_price - entry_price) / entry_price * 100, 4)
        if direction == "SHORT":
            mfe_pct = -mfe_pct
            mae_pct = -mae_pct

        first = rows[0]
        return {
            "mfe_price": mfe_price,
            "mfe_pct": mfe_pct,
            "mae_price": mae_price,
            "mae_pct": mae_pct,
            "entry_candle_open": first["open"],
            "entry_candle_high": first["high"],
            "entry_candle_low": first["low"],
            "entry_candle_close": first["close"],
        }

    def insert_excursion_reconstruction_run(
        self,
        *,
        run_id: str,
        mode: str,
        started_at: str,
        completed_at: str,
        trades_examined: int,
        trades_written: int,
        trades_skipped_unreconstructable: int,
        trades_failed: int,
        status: str,
        notes: Optional[str] = None,
    ) -> None:
        """Write one excursion_reconstruction_runs audit row (schema v39).

        One row per scripts/reconstruct_excursions.py run (mode 'daily' |
        'backfill'). Idempotent on run_id via INSERT OR REPLACE.
        """
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO excursion_reconstruction_runs
                    (run_id, mode, started_at, completed_at, trades_examined,
                     trades_written, trades_skipped_unreconstructable,
                     trades_failed, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, mode, started_at, completed_at, trades_examined,
                 trades_written, trades_skipped_unreconstructable,
                 trades_failed, status, notes),
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
    # gtt_state — durable one-OCO-GTT-per-CNC-trade source of truth (SLICE2.5-P2)
    # Y6: a trade may accumulate MANY rows over its life (each recreate = new
    # gtt_id = new row = history); the M2 one-GTT invariant is on status='ACTIVE'.
    # ─────────────────────────────────────────────────────────────────────────

    def insert_gtt_state(
        self,
        *,
        gtt_id,
        trade_id: str,
        symbol: str,
        exit_side: str,
        qty: int,
        sl_trigger: float,
        sl_limit: float,
        tgt_trigger: float,
        tgt_limit: float,
        created_at: str,
        status: str = "ACTIVE",
    ) -> None:
        """Insert a new gtt_state row (ACTIVE by default; last_verified_at = created_at,
        since a fresh place is confirmed at the broker)."""
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO gtt_state
                    (gtt_id, trade_id, symbol, exit_side, qty,
                     sl_trigger, sl_limit, tgt_trigger, tgt_limit,
                     status, needs_review, last_verified_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (gtt_id, trade_id, symbol, exit_side, int(qty),
                 sl_trigger, sl_limit, tgt_trigger, tgt_limit,
                 status, created_at, created_at, created_at),
            )

    def update_gtt_state_legs(
        self,
        *,
        gtt_id,
        qty: int,
        sl_trigger: float,
        sl_limit: float,
        tgt_trigger: float,
        tgt_limit: float,
        updated_at: str,
    ) -> int:
        """Update an existing gtt_state row after a modify_gtt (a partial fill grew the
        qty / legs changed). Keeps the SAME gtt_id. Returns rows affected."""
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE gtt_state
                   SET qty = ?, sl_trigger = ?, sl_limit = ?,
                       tgt_trigger = ?, tgt_limit = ?, updated_at = ?
                 WHERE gtt_id = ?
                """,
                (int(qty), sl_trigger, sl_limit, tgt_trigger, tgt_limit, updated_at, gtt_id),
            )
            return cur.rowcount

    def get_active_gtt_for_trade(self, trade_id: str) -> Optional[sqlite3.Row]:
        """Return the single ACTIVE gtt_state row for a trade (None if none).
        M2: at most one ACTIVE per open trade."""
        return self.fetch_one(
            "SELECT * FROM gtt_state WHERE trade_id = ? AND status = 'ACTIVE' "
            "ORDER BY created_at DESC LIMIT 1",
            (trade_id,),
        )

    def get_active_gtt_states(self) -> List[sqlite3.Row]:
        """All ACTIVE gtt_state rows. Hydrates the placer's hot cache on boot; also
        read by the Phase-2 reconcile + the 50-cap guard."""
        return self.fetch_all("SELECT * FROM gtt_state WHERE status = 'ACTIVE'")

    def get_gtt_state_by_id(self, gtt_id) -> Optional[sqlite3.Row]:
        """Return the gtt_state row for a broker trigger id (any status), or None.
        Used by the orphan sweep to tell a leaked SYSTEM GTT (row exists, non-ACTIVE)
        from a human/external GTT (no row at all)."""
        try:
            key = int(gtt_id)
        except (TypeError, ValueError):
            key = gtt_id
        return self.fetch_one("SELECT * FROM gtt_state WHERE gtt_id = ?", (key,))

    def set_gtt_state_status(self, gtt_id, status: str, updated_at: str) -> int:
        """Transition a gtt_state row's status (ACTIVE->TRIGGERED->CLEANED, or
        ACTIVE->CANCELLED/EXPIRED/REJECTED). Returns rows affected."""
        with self.transaction() as cur:
            cur.execute(
                "UPDATE gtt_state SET status = ?, updated_at = ? WHERE gtt_id = ?",
                (status, updated_at, gtt_id),
            )
            return cur.rowcount

    def set_gtt_state_needs_review(self, gtt_id, needs_review: int, updated_at: str) -> int:
        """Y2: latch (1) / clear (0) the needs_review flag so an anomaly alerts ONCE
        per state rather than every reconcile cycle. Returns rows affected."""
        with self.transaction() as cur:
            cur.execute(
                "UPDATE gtt_state SET needs_review = ?, updated_at = ? WHERE gtt_id = ?",
                (1 if needs_review else 0, updated_at, gtt_id),
            )
            return cur.rowcount

    def touch_gtt_state_verified(self, gtt_id, last_verified_at: str) -> int:
        """Stamp last_verified_at after a healthy broker re-verify (no other change)."""
        with self.transaction() as cur:
            cur.execute(
                "UPDATE gtt_state SET last_verified_at = ?, updated_at = ? WHERE gtt_id = ?",
                (last_verified_at, last_verified_at, gtt_id),
            )
            return cur.rowcount

    def mark_trade_closed_gtt(self, trade_id: str, exit_reason: str = "GTT_EXIT") -> bool:
        """SLICE2.5-P2: idempotency gate for a GTT-fired exit. OPEN/PARTIAL -> CLOSED
        with a GTT exit_reason. Returns True iff THIS call transitioned the row — a
        duplicate observer (postback + reconcile, 3.5e) gets False and MUST NOT
        double-release capital. Distinct from CLOSED_MANUAL: a GTT exit is
        SYSTEM-OWNED, not an external/human close."""
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE trades SET status = 'CLOSED', exit_reason = ?, updated_at = ?
                WHERE trade_id = ? AND status IN ('OPEN', 'PARTIAL')
                """,
                (exit_reason, _now_ist_iso(), trade_id),
            )
            return cur.rowcount > 0

    def record_gtt_close_financials(
        self,
        trade_id: str,
        exit_price: float,
        net_pnl: float,
        charges: float = 0.0,
        exit_time: Optional[str] = None,
    ) -> bool:
        """Write exit financials onto a GTT-closed trade (status='CLOSED' with a GTT
        exit_reason). Guarded by `exit_reason LIKE 'GTT%'` so it can never clobber a
        normally-closed row. GTT exits pass costs=0.0 (gross==net). Returns True if
        the row was updated."""
        ts = exit_time or _now_ist_iso()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE trades
                SET exit_price = ?, exit_time = COALESCE(exit_time, ?),
                    gross_pnl = ?, charges = ?, net_pnl = ?, updated_at = ?
                WHERE trade_id = ? AND status = 'CLOSED' AND exit_reason LIKE 'GTT%'
                """,
                (exit_price, ts, net_pnl, charges, net_pnl, ts, trade_id),
            )
            return cur.rowcount > 0

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
    # Slippage intelligence Phase 1 — best-effort, NEVER-raise inserts (v31).
    # The recorder runs on async event threads; a logging failure must never
    # propagate, so every insert is wrapped and returns a bool.
    # ─────────────────────────────────────────────────────────────────────────

    _OEL_COLS = (
        "order_id", "parent_trade_id", "signal_id", "symbol", "strategy_name",
        "leg", "order_type", "side", "intended_price", "actual_price",
        "slippage_rs", "slippage_pct", "qty", "filled_qty", "is_partial",
        "retry_count", "status", "order_timestamp", "fill_timestamp",
        "exchange_timestamp",
        "tolerance_fraction_used", "tolerance_source",   # Phase 3a
    )
    _TSL_COLS = (
        "trade_id", "trade_date", "symbol", "strategy_name", "side", "qty",
        "price_band", "entry_signal_price", "entry_fill_price",
        "entry_slippage_rs", "entry_slippage_pct", "sl_trigger_price",
        "sl_fill_price", "sl_slippage_rs", "sl_slippage_pct", "tgt_price",
        "tgt_fill_price", "tgt_slippage_rs", "tgt_slippage_pct",
        "planned_sl_distance", "planned_rr", "actual_rr", "rr_damage_pct",
        "trade_result", "exit_reason",
    )
    _MEC_COLS = (
        "trade_id", "order_id", "symbol", "leg", "captured_at", "ltp",
        "bid_price", "ask_price", "spread_rs", "spread_pct", "bid_qty",
        "ask_qty", "volume_traded", "recent_range_pct",
    )

    def _best_effort_insert(self, table: str, allowed: tuple, row: dict) -> bool:
        """Insert the whitelisted columns present in `row` into `table`. Returns
        False on any error (NEVER raises) — slippage logging is best-effort and
        must not affect trading. `table`/`allowed` are code constants (no
        injection); only values are parameterised."""
        cols = [c for c in allowed if c in row]
        if not cols:
            return False
        try:
            placeholders = ", ".join("?" for _ in cols)
            with self.transaction() as cur:
                cur.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                    tuple(row[c] for c in cols),
                )
            return True
        except Exception:
            return False

    def insert_order_execution_log(self, row: dict) -> bool:
        return self._best_effort_insert("order_execution_log", self._OEL_COLS, row)

    def insert_trade_slippage_log(self, row: dict) -> bool:
        return self._best_effort_insert("trade_slippage_log", self._TSL_COLS, row)

    def insert_market_execution_context(self, row: dict) -> bool:
        return self._best_effort_insert("market_execution_context", self._MEC_COLS, row)

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
            # O4 (v27): use the indexed stored `date` column (== DATE(ts)).
            "SELECT * FROM fm_ledger WHERE date = ?",
            (date_iso,),
        )
        return [dict(r) for r in rows]

    def get_daily_realized_net_pnl(self, date_iso: str) -> float:
        """
        FIX-056: Return NET realized PnL for date_iso (gross PnL minus costs).

        Sums fm_ledger.pnl_delta (gross PnL from trade closes) and subtracts
        fm_ledger.costs (brokerage, STT, taxes). Returns 0.0 if no rows.

        Args:
            date_iso: Date string in YYYY-MM-DD format (YYYY-MM-DD)

        Returns:
            Net PnL = sum(pnl_delta) - sum(costs) for the given date.
        """
        row = self.fetch_one(
            # O4 (v27): use the indexed stored `date` column (== DATE(ts)).
            """SELECT COALESCE(SUM(pnl_delta) - SUM(COALESCE(costs, 0.0)), 0.0) as net_pnl
               FROM fm_ledger WHERE date = ?""",
            (date_iso,),
        )
        return row["net_pnl"] if row else 0.0

    def get_day_opening_capital(self, date_iso: str) -> Optional[float]:
        """
        BUILD 1 (#1/#2, 24-Jun): return the day's OPENING capital for date_iso,
        read from the fm_ledger INIT row (FundManager.initialize writes one INIT
        row per process start with balance_after = the day's starting capital).

        Used by the EOD/pre-flight auditors to recompute the now capital-relative
        thresholds (daily_loss_limit_pct × capital, max_position_value_pct ×
        capital) without a broker call — the single, parity-safe (same DB in
        paper + live) capital source.

        OPENING (not current) basis is deliberate: the threshold should not move
        intraday as realized PnL swings. Returns None if no INIT row exists for
        the day (fresh DB / non-trading day) so callers can apply a fallback.
        """
        row = self.fetch_one(
            # O4 (v27): use the indexed stored `date` column (== DATE(ts)).
            """SELECT balance_after FROM fm_ledger
               WHERE date = ? AND entry_type = 'INIT'
               ORDER BY ts ASC LIMIT 1""",
            (date_iso,),
        )
        return float(row["balance_after"]) if row and row["balance_after"] is not None else None

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

    def get_candles_for_date(self, date_iso: str) -> List[dict]:
        """Return all candle rows whose ts falls on date_iso (DR-v14)."""
        rows = self.fetch_all(
            # O4 (v27): use the indexed stored `date` column (== DATE(ts)).
            "SELECT * FROM candles WHERE date = ?",
            (date_iso,),
        )
        return [dict(r) for r in rows]

    def get_trade_excursions_for_date(self, date_iso: str) -> List[dict]:
        """Return trade_excursion rows for trades created on date_iso (DR-v14)."""
        rows = self.fetch_all(
            "SELECT te.* FROM trade_excursions te "
            "JOIN trades t ON te.trade_id = t.trade_id "
            "WHERE DATE(t.created_at) = ?",
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
        The caller pivots this flat list into per-trade rows (the multi-inning
        report view — deferred to W13; formerly reports/daily_review.py, retired).
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

    def release_gate_state(self, signal_id: str, status: str, reason: str = "") -> None:
        """
        FIX-010: atomically delete the gate_state row AND update the signal
        status in a single transaction, so a crash between the two cannot
        leave a zombie gate_state with a stale signal status.

        Equivalent to calling delete_gate_state() then update_signal_status()
        but wrapped in one BEGIN/COMMIT.
        """
        with self.transaction() as cur:
            cur.execute(
                "DELETE FROM gate_state WHERE signal_id = ?", (signal_id,),
            )
            cur.execute(
                "UPDATE signals SET status = ?, rejection_reason = ? WHERE signal_id = ?",
                (status, reason if reason else None, signal_id),
            )

    def clear_all_gate_state(self) -> int:
        """
        FIX-046: Clear all gate_state rows (EOD cleanup to prevent stale signal rehydration).
        Returns count of rows deleted.
        """
        with self.transaction() as cur:
            cur.execute("DELETE FROM gate_state")
            return cur.rowcount

    def get_all_gate_state(self) -> list[dict]:
        """Return every persisted gate_state row as a list of dicts."""
        with self.transaction() as cur:
            cur.execute("SELECT * FROM gate_state ORDER BY added_at ASC")
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    # ─────────────────────────────────────────────────────────────────────────
    # SNR-V2 Phase A — retest_state (WAIT_FOR_RETEST parking; mirrors gate_state)
    # ─────────────────────────────────────────────────────────────────────────

    _RETEST_INSERT_COLS = (
        "signal_id", "symbol", "direction", "zone_band_low", "zone_band_high",
        "entry_price", "sl_price", "strategy", "intent", "tier", "state",
        "trigger_price", "sizing_inputs", "timeout_at", "added_at", "created_at",
    )

    def insert_retest_state(self, row: dict) -> None:
        """
        Persist a diverted WAIT_FOR_RETEST candidate. Idempotent INSERT OR REPLACE
        keyed on signal_id (a re-divert / re-add of the same signal does not raise).
        """
        cols = self._RETEST_INSERT_COLS
        placeholders = ", ".join("?" for _ in cols)
        with self.transaction() as cur:
            cur.execute(
                f"INSERT OR REPLACE INTO retest_state "
                f"(id, {', '.join(cols)}) "
                f"VALUES ((SELECT id FROM retest_state WHERE signal_id = ?), {placeholders})",
                (row.get("signal_id"),) + tuple(row.get(c) for c in cols),
            )

    def update_retest_state(self, signal_id: str, state: str) -> None:
        """Advance the persisted state snapshot for a parked candidate."""
        with self.transaction() as cur:
            cur.execute(
                "UPDATE retest_state SET state = ? WHERE signal_id = ?",
                (state, signal_id),
            )

    def release_retest_state(self, signal_id: str, status: str, reason: str = "") -> None:
        """
        Atomically delete the retest_state row AND update the signal status in one
        transaction (mirrors release_gate_state) — no zombie row with a stale status.
        """
        with self.transaction() as cur:
            cur.execute("DELETE FROM retest_state WHERE signal_id = ?", (signal_id,))
            cur.execute(
                "UPDATE signals SET status = ?, rejection_reason = ? WHERE signal_id = ?",
                (status, reason if reason else None, signal_id),
            )

    def clear_all_retest_state(self) -> int:
        """Clear all retest_state rows (EOD cleanup). Returns count deleted."""
        with self.transaction() as cur:
            cur.execute("DELETE FROM retest_state")
            return cur.rowcount

    def get_all_retest_state(self) -> list[dict]:
        """Return every persisted retest_state row as a list of dicts."""
        with self.transaction() as cur:
            cur.execute("SELECT * FROM retest_state ORDER BY added_at ASC")
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    # ─────────────────────────────────────────────────────────────────────────
    # Order reconciliation status helpers (FIX-129 Item 26)
    # ─────────────────────────────────────────────────────────────────────────

    def update_order_reconciliation_status(
        self,
        order_id: str,
        status: str,
    ) -> None:
        """
        FIX-129 (Item 26): Set reconciliation_status on an orders row.

        Valid values: "OK" | "MISMATCH" | "SL_MISSING" | "ORPHAN"
        Called by order_reconciler when its checks pass or detect a mismatch.
        """
        with self.transaction() as cur:
            cur.execute(
                "UPDATE orders SET reconciliation_status = ? WHERE order_id = ?",
                (status, order_id),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # P&L reconciliation helpers (FIX-128 Fix B)
    # ─────────────────────────────────────────────────────────────────────────

    def get_today_closed_pnl(self, date_iso: str) -> float:
        """
        Return sum of net_pnl for trades CLOSED today (FIX-128 Fix B).

        Only terminal statuses (CLOSED) are included. Open trades have
        unrealized P&L not yet counted. Returns 0.0 if no closed trades today.
        """
        row = self.fetch_one(
            """SELECT COALESCE(SUM(net_pnl), 0.0) AS total
               FROM trades
               WHERE status IN ('CLOSED', 'CLOSED_MANUAL')
                 AND DATE(updated_at) = ?""",
            (date_iso,),
        )
        return float(row["total"]) if row else 0.0

    def upsert_pnl_reconciliation(
        self,
        date: str,
        broker_pnl: Optional[float],
        system_pnl: float,
        variance: Optional[float],
        status: str,
        notes: Optional[str],
        created_at: str,
    ) -> None:
        """
        Upsert a pnl_reconciliation row for date (FIX-128 Fix B).

        Uses INSERT OR REPLACE so a re-run on the same date overwrites the
        previous result. Allows scripts/reconcile_pnl.py to be run multiple
        times idempotently.
        """
        with self.transaction() as cur:
            cur.execute(
                """INSERT OR REPLACE INTO pnl_reconciliation
                       (date, broker_pnl, system_pnl, variance, status, notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (date, broker_pnl, system_pnl, variance, status, notes, created_at),
            )

    def get_pnl_reconciliation(self, date_iso: str) -> Optional[dict]:
        """Return pnl_reconciliation row for date_iso, or None if not found."""
        row = self.fetch_one(
            "SELECT * FROM pnl_reconciliation WHERE date = ?",
            (date_iso,),
        )
        return dict(row) if row else None

    # ─────────────────────────────────────────────────────────────────────────
    # FIX-145: Cron heartbeat helpers
    # ─────────────────────────────────────────────────────────────────────────

    def insert_cron_heartbeat(
        self,
        job_name: str,
        executed_at: str,
        status: str = "SUCCESS",
        duration_sec: Optional[float] = None,
        message: Optional[str] = None,
    ) -> None:
        """
        Record a cron job execution heartbeat.

        Called at the END of each cron script to record successful execution.
        status: SUCCESS (default) | PARTIAL (completed with warnings) | FAILED
        """
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO cron_heartbeat
                       (job_name, executed_at, status, duration_sec, message)
                   VALUES (?, ?, ?, ?, ?)""",
                (job_name, executed_at, status, duration_sec, message),
            )

    def get_cron_heartbeats_since(self, since_iso: str) -> List[dict]:
        """
        Return all cron heartbeats since the given ISO timestamp.

        Used by scripts/check_cron_drift.py to verify all expected jobs ran.
        """
        rows = self.fetch_all(
            """SELECT job_name, executed_at, status, duration_sec, message
               FROM cron_heartbeat
               WHERE executed_at >= ?
               ORDER BY executed_at DESC""",
            (since_iso,),
        )
        return [dict(r) for r in rows]

    def get_last_heartbeat_for_job(self, job_name: str) -> Optional[dict]:
        """Return the most recent heartbeat for a specific cron job."""
        row = self.fetch_one(
            """SELECT job_name, executed_at, status, duration_sec, message
               FROM cron_heartbeat
               WHERE job_name = ?
               ORDER BY executed_at DESC
               LIMIT 1""",
            (job_name,),
        )
        return dict(row) if row else None

    def __repr__(self) -> str:
        return f"StateStore(db_path={self._db_path!r})"
