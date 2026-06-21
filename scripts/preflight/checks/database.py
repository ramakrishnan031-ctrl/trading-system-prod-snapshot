"""
scripts/preflight/checks/database.py -- Group 3 (Database & Schema).

db_schema_version is the parity port of premarket_healthcheck.check_database, but
SAFER: it reads the schema_meta row with a raw connection and does NOT construct a
StateStore -- so it can never trigger a migration as a side-effect (the spec is
explicit: pre-flight must NOT auto-migrate; the trading-system handles that at
startup). Detection parity is preserved (PASS iff version == EXPECTED).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.preflight.base import Check, CheckContext, CheckResult, Criticality, FixResult


def expected_schema_version() -> int:
    from core.state_store import EXPECTED_SCHEMA_VERSION
    return int(EXPECTED_SCHEMA_VERSION)


def _read_schema_version(db_path: Path) -> int | None:
    """Raw read of schema_meta.schema_version -- no StateStore, no migration."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None
    finally:
        conn.close()


def _read_journal_mode(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        conn.close()


class DbFileExistsCheck(Check):
    name = "db_file_exists"
    group = "Database"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 5

    def run(self, ctx: CheckContext) -> CheckResult:
        if ctx.db_path.exists():
            return self._passed(str(ctx.db_path))
        return self._failed(f"database not found: {ctx.db_path}")


class DbSchemaVersionCheck(Check):
    """Alert-only by design -- pre-flight never migrates (spec).

    Direction matters (Fix 1, 21-Jun):
      DB == code  -> PASS  (no migration pending)
      DB <  code  -> WARN  (NORMAL pre-migration state at Phase A; app migrates at
                            startup -- NOT a CRITICAL, else every migration Monday
                            false-alarms)
      DB >  code  -> CRITICAL (code older than DB -> likely a code rollback without
                            a DB rollback; dangerous)
    """

    name = "db_schema_version"
    group = "Database"
    criticality = Criticality.CRITICAL   # only the DB>code branch is a blocking FAIL
    expected_duration_ms = 10

    def run(self, ctx: CheckContext) -> CheckResult:
        if not ctx.db_path.exists():
            return self._failed(f"database not found: {ctx.db_path}")
        try:
            version = _read_schema_version(ctx.db_path)
            expected = expected_schema_version()
        except Exception as exc:
            return self._failed(f"schema read error: {exc}")
        if version is None:
            return self._failed("schema_meta.schema_version row missing")
        if version == expected:
            return self._passed(f"schema v{version} OK",
                                db_version=version, code_version=expected)
        if version < expected:
            return self._warn(
                f"migration pending: app will migrate v{version}→v{expected} at startup",
                db_version=version, code_version=expected)
        # version > expected -- code is behind the DB
        return self._failed(
            f"code older than DB (code v{expected} < DB v{version}) — rollback without "
            "a DB rollback? investigate",
            db_version=version, code_version=expected)


class DbJournalModeWalCheck(Check):
    name = "db_journal_mode"
    group = "Database"
    criticality = Criticality.CRITICAL
    auto_fixable = True
    fix_action = "set_journal_mode_wal"
    expected_duration_ms = 10

    def run(self, ctx: CheckContext) -> CheckResult:
        if not ctx.db_path.exists():
            return self._failed(f"database not found: {ctx.db_path}")
        mode = _read_journal_mode(ctx.db_path)
        if mode == "wal":
            return self._passed("journal_mode=wal", journal_mode=mode)
        return self._failed(f"journal_mode={mode} (expected wal)", journal_mode=mode)

    def fix(self, ctx: CheckContext) -> FixResult:
        before = _read_journal_mode(ctx.db_path)
        conn = sqlite3.connect(str(ctx.db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        finally:
            conn.close()
        after = _read_journal_mode(ctx.db_path)
        return FixResult(
            success=(after == "wal"),
            action=self.fix_action,
            before_state=f"journal_mode={before}",
            after_state=f"journal_mode={after}",
            error_msg="" if after == "wal" else f"journal_mode still {after}",
        )


class DbWritableCheck(Check):
    name = "db_writable"
    group = "Database"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 50

    def run(self, ctx: CheckContext) -> CheckResult:
        if not ctx.db_path.exists():
            return self._failed(f"database not found: {ctx.db_path}")
        try:
            conn = sqlite3.connect(str(ctx.db_path), timeout=5)
            try:
                # acquiring the reserved write-lock and rolling back proves the DB
                # is writable WITHOUT touching any row.
                conn.execute("BEGIN IMMEDIATE")
                conn.rollback()
            finally:
                conn.close()
        except Exception as exc:
            return self._failed(f"DB not writable: {exc}")
        return self._passed("write-lock acquired + rolled back")


CHECKS = [
    DbFileExistsCheck(),
    DbSchemaVersionCheck(),
    DbJournalModeWalCheck(),
    DbWritableCheck(),
]
