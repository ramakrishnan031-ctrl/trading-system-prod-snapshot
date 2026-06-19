"""
core/migrations.py — Trading System v2

Purpose
-------
Idempotent, data-preserving schema migrations for the SQLite state store.

Background
----------
Historically the v2 schema was applied by running ``schema.sql`` (which uses
``CREATE TABLE IF NOT EXISTS``) and bumping ``schema_meta.schema_version``.
That works for adding *new tables*, but ``CREATE TABLE IF NOT EXISTS`` is a
silent no-op on a table that already exists — so adding a CHECK constraint,
a FOREIGN KEY, or a generated column to an *existing* table is impossible
through ``schema.sql`` alone. SQLite also cannot ``ALTER TABLE`` to add
constraints, and cannot ``ALTER TABLE ADD COLUMN`` a STORED generated column.

The only correct way to add a constraint/FK/STORED-column to an existing
table is the documented "12-step" table rebuild:
    https://www.sqlite.org/lang_altertable.html#otheralter

Design
------
* ``schema.sql`` remains the SINGLE SOURCE OF TRUTH. Each table's canonical
  definition (with its new constraints) lives there, inline.
* A migration rebuilds an *existing* table by re-applying ``schema.sql``'s
  CURRENT definition for that table — we extract the ``CREATE TABLE`` block
  for the table straight from the schema text, build a temp table from it,
  copy the intersecting columns, then swap. No DDL is duplicated here, so the
  fresh-build path and the migration path cannot drift apart.
* Every rebuild is IDEMPOTENT: if the live table's normalized DDL already
  equals the schema's normalized DDL, the rebuild is skipped. This also means
  the runner is safe to invoke on every startup.

Version map
-----------
    v24 -> v25 : O2 — status/enum CHECK constraints
                 (signals, trades, orders, kill_switch_state)
    v25 -> v26 : O1 — uniform FOREIGN KEY declarations
                 (screener_results, smart_tgt_state, shadow_trades,
                  reconciliation_log, innings)
    v26 -> v27 : O4 — stored ``date`` generated column + index
                 (fm_ledger, candles, system_metrics, webhook_audit)
    v27 -> v28 : O6 — analytics DB split. candles, system_metrics and
                 system_metrics_daily are RELOCATED out of trading_system.db
                 into analytics.db (relocate_analytics_tables, not a rebuild).
    v28 -> v29 : FIX-179 — trades.status CHECK adds 'EXITING' (kill_switch
                 hard_kill transitional state). Rebuild trades to widen CHECK.
    v29 -> v30 : TGT retry — trades gains needs_tgt_retry / tgt_retry_count /
                 tgt_last_retry_at columns. Rebuild trades (the rebuild copies
                 the intersecting old columns; the new ones take their DEFAULT).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Dict, List

# Tables rebuilt at each version step. The rebuild always re-applies the
# CURRENT schema.sql definition for the table, so listing a table here just
# tells the runner "this table's definition changed at this version — rebuild
# it if the live DB is below this version."
MIGRATION_TABLES: Dict[int, List[str]] = {
    25: ["signals", "trades", "orders", "kill_switch_state"],   # O2 CHECK
    26: ["screener_results", "smart_tgt_state", "shadow_trades",  # O1 FK
         "reconciliation_log", "innings"],
    # O4 date col. candles + system_metrics also got the date col at v27, but
    # they are RELOCATED to analytics.db at v28 (relocate_analytics_tables) where
    # their definition carries the date col — so they are no longer rebuilt
    # in-place here, and no longer appear in schema.sql.
    27: ["fm_ledger", "webhook_audit"],
    29: ["trades"],  # FIX-179: widen trades.status CHECK to allow 'EXITING'
    30: ["trades"],  # TGT retry: add needs_tgt_retry / tgt_retry_count / tgt_last_retry_at
}


class MigrationError(Exception):
    """A schema migration failed; the DB is left untouched (rolled back)."""


# ─────────────────────────────────────────────────────────────────────────────
# schema.sql parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_block(schema_text: str, prefix_re: str) -> List[str]:
    """
    Return every top-level statement in ``schema_text`` whose opening matches
    ``prefix_re`` (anchored at a line start, case-insensitive), captured up to
    and including the terminating semicolon, with balanced parentheses.
    """
    out: List[str] = []
    pat = re.compile(prefix_re, re.IGNORECASE | re.MULTILINE)
    for m in pat.finditer(schema_text):
        i = m.start()
        depth = 0
        j = i
        n = len(schema_text)
        while j < n:
            ch = schema_text[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == ";" and depth == 0:
                break
            j += 1
        out.append(schema_text[i : j + 1])
    return out


def extract_create_table(schema_text: str, table: str) -> str:
    """Return the full ``CREATE TABLE ... <table> ( ... );`` block from schema."""
    blocks = _extract_block(
        schema_text,
        rf"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{re.escape(table)}\b",
    )
    if not blocks:
        raise MigrationError(f"No CREATE TABLE for {table!r} found in schema.sql")
    if len(blocks) > 1:
        raise MigrationError(f"Multiple CREATE TABLE blocks for {table!r} in schema.sql")
    return blocks[0].strip()


def extract_indexes_for(schema_text: str, table: str) -> List[str]:
    """Return all ``CREATE [UNIQUE] INDEX ... ON <table> (...)`` blocks."""
    blocks = _extract_block(
        schema_text,
        r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+"
        + re.escape(table)
        + r"\b",
    )
    return [b.strip() for b in blocks]


def _normalize_ddl(sql: str) -> str:
    """Collapse whitespace and strip comments so two DDL strings compare equal."""
    # Strip -- line comments
    sql = re.sub(r"--[^\n]*", " ", sql)
    # Drop IF NOT EXISTS (sqlite_master never stores it)
    sql = re.sub(r"\bIF\s+NOT\s+EXISTS\s+", "", sql, flags=re.IGNORECASE)
    # Strip double-quotes around identifiers — ALTER TABLE RENAME quotes the
    # table name (CREATE TABLE "signals" ...) while a fresh CREATE does not.
    sql = sql.replace('"', "")
    # Collapse all whitespace
    sql = re.sub(r"\s+", " ", sql)
    # Normalize spacing around punctuation
    sql = re.sub(r"\s*([(),])\s*", r"\1", sql)
    return sql.strip().upper()


def _live_table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row[0] if row and row[0] else ""


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    # Exclude generated columns from the copy list — they are computed, not
    # inserted. PRAGMA table_xinfo exposes the "hidden" flag (>0 for generated).
    cols: List[str] = []
    for r in conn.execute(f"PRAGMA table_xinfo({table})").fetchall():
        # r: (cid, name, type, notnull, dflt_value, pk, hidden)
        name, hidden = r[1], r[6]
        if hidden in (2, 3):  # 2=VIRTUAL generated, 3=STORED generated
            continue
        cols.append(name)
    return cols


def _nongen_columns_in(conn: sqlite3.Connection, schema: str, table: str) -> List[str]:
    """Non-generated columns of ``schema.table`` (schema = 'main'|'analytics')."""
    cols: List[str] = []
    for r in conn.execute(f"PRAGMA {schema}.table_xinfo({table})").fetchall():
        name, hidden = r[1], r[6]
        if hidden in (2, 3):
            continue
        cols.append(name)
    return cols


def _table_in_schema(schema_text: str, table: str) -> bool:
    """True if schema.sql still defines ``table`` (relocated tables won't)."""
    try:
        extract_create_table(schema_text, table)
        return True
    except MigrationError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Table rebuild (12-step) — re-applies schema.sql's current definition
# ─────────────────────────────────────────────────────────────────────────────

def _rebuild_table_from_schema(
    conn: sqlite3.Connection,
    schema_text: str,
    table: str,
    log: logging.Logger,
) -> bool:
    """
    Rebuild ``table`` so its definition matches ``schema.sql``. Idempotent:
    returns False (no-op) if the live table DDL already matches the schema.

    Caller MUST NOT be inside a transaction (we manage BEGIN/COMMIT and toggle
    PRAGMA foreign_keys, which is a no-op inside a transaction).
    """
    target_ddl = extract_create_table(schema_text, table)
    live_ddl = _live_table_sql(conn, table)
    if not live_ddl:
        # Table doesn't exist yet — executescript(schema.sql) will create it
        # with the correct definition. Nothing to migrate.
        return False
    if _normalize_ddl(live_ddl) == _normalize_ddl(target_ddl):
        return False  # already at target — idempotent skip

    tmp = f"{table}__mig_new"
    tmp_ddl = re.sub(
        rf"(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?){re.escape(table)}\b",
        rf"\1{tmp}",
        target_ddl,
        count=1,
        flags=re.IGNORECASE,
    )
    if tmp not in tmp_ddl:
        raise MigrationError(f"Failed to rename table in DDL for {table!r}")

    # Columns common to both old and new definitions (intersection, in new order)
    old_cols = set(_table_columns(conn, table))
    # Build tmp first to learn its column set, inside the transaction.
    indexes = extract_indexes_for(schema_text, table)

    # foreign_keys must be OFF for a table rebuild (child rows would otherwise
    # be seen as violations mid-swap). This pragma is a no-op inside a txn, so
    # set it BEFORE BEGIN and restore AFTER COMMIT. (SQLite docs, 12-step.)
    fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    if fk_was_on:
        conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"DROP TABLE IF EXISTS {tmp}")
        conn.execute(tmp_ddl)
        new_cols = [c[1] for c in conn.execute(f"PRAGMA table_xinfo({tmp})").fetchall()
                    if c[6] not in (2, 3)]
        copy_cols = [c for c in new_cols if c in old_cols]
        col_list = ", ".join(copy_cols)
        conn.execute(
            f"INSERT INTO {tmp} ({col_list}) SELECT {col_list} FROM {table}"
        )
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
        for idx_sql in indexes:
            conn.execute(idx_sql)
        # Verify no FK violations were introduced before committing.
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise MigrationError(
                f"foreign_key_check failed after rebuilding {table!r}: {violations}"
            )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        if fk_was_on:
            conn.execute("PRAGMA foreign_keys = ON")

    log.info("migration: rebuilt table %s to match schema.sql", table)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Analytics DB split (O6, v28) — relocate tables out of main into analytics.db
# ─────────────────────────────────────────────────────────────────────────────

def relocate_analytics_tables(
    conn: sqlite3.Connection,
    tables,
    log: logging.Logger,
) -> int:
    """
    Move each analytics table that still lives in the MAIN database into the
    attached ``analytics`` database, then drop it from main. Returns the count
    relocated.

    Preconditions: the connection has ``analytics`` ATTACHed and the analytics
    tables already exist (db_connect.init_analytics_schema). Caller must NOT be
    inside a transaction.

    Idempotent: a table already absent from main is skipped (already moved).
    Each table moves in its own transaction and ``analytics.<t>`` is cleared
    first, so a crash mid-migration can be retried without duplicating rows
    (matters for tables without a UNIQUE constraint, e.g. system_metrics).
    """
    moved = 0
    for t in tables:
        in_main = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone()
        if not in_main:
            continue  # already relocated — idempotent skip

        # Copy only columns common to both definitions, excluding generated
        # ones (analytics.<t> recomputes its STORED `date` column).
        main_cols = _nongen_columns_in(conn, "main", t)
        an_cols = set(_nongen_columns_in(conn, "analytics", t))
        copy_cols = [c for c in main_cols if c in an_cols]
        if not copy_cols:
            raise MigrationError(
                f"relocate {t!r}: no common columns between main and analytics"
            )
        col_list = ", ".join(copy_cols)

        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(f"DELETE FROM analytics.{t}")
            conn.execute(
                f"INSERT INTO analytics.{t} ({col_list}) "
                f"SELECT {col_list} FROM main.{t}"
            )
            conn.execute(f"DROP TABLE main.{t}")
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        moved += 1
        log.info("migration: relocated table %s -> analytics.db", t)
    return moved


# ─────────────────────────────────────────────────────────────────────────────
# Public runner
# ─────────────────────────────────────────────────────────────────────────────

def run_migrations(
    conn: sqlite3.Connection,
    schema_text: str,
    from_version: int,
    target_version: int,
    log: logging.Logger | None = None,
) -> int:
    """
    Bring an existing DB from ``from_version`` up to ``target_version`` by
    rebuilding the tables whose definitions changed at each intermediate
    version. Returns the number of tables rebuilt.

    Idempotent and safe to call on every startup: a table already matching
    schema.sql is skipped. Each table rebuild is individually atomic.
    """
    log = log or logging.getLogger("migrations")
    if from_version >= target_version:
        return 0

    rebuilt = 0
    seen: set[str] = set()
    for version in range(from_version + 1, target_version + 1):
        for table in MIGRATION_TABLES.get(version, []):
            if table in seen:
                continue
            seen.add(table)
            # A table listed for an older version may since have been removed
            # from schema.sql (e.g. relocated to analytics.db at v28). Skip it —
            # the relevant later step handles it.
            if not _table_in_schema(schema_text, table):
                log.debug("migration: %s not in schema.sql — skipping rebuild", table)
                continue
            try:
                if _rebuild_table_from_schema(conn, schema_text, table, log):
                    rebuilt += 1
            except Exception as exc:  # noqa: BLE001
                raise MigrationError(
                    f"migration v{from_version}->v{target_version} failed "
                    f"rebuilding {table!r}: {exc}"
                ) from exc
    log.info(
        "migration complete: v%d -> v%d (%d tables rebuilt)",
        from_version, target_version, rebuilt,
    )
    return rebuilt
