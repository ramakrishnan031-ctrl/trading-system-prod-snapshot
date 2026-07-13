"""P11 migration guard (AC1-AC5, 14-Jul): schema migrations run ON DB-OPEN, so ONLY
main.py's boot path (allow_migrate=True, off-market) may migrate the live DB; every other
StateStore opener refuses and fails LOUDLY (a CRITICAL sentinel is dropped). Never a silent
skip, never a 'run anyway' path.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.state_store import (
    StateStore,
    MigrationNotPermitted,
    EXPECTED_SCHEMA_VERSION,
)


# ── helpers ──────────────────────────────────────────────────────────────────
def _fresh_db(tmp_path: Path) -> Path:
    """A brand-new DB built to the CURRENT schema via the boot path (off-market)."""
    db = tmp_path / "s.db"
    s = StateStore(db, allow_migrate=True, market_open=False)
    s.close()
    return db


def _stamp_version(db: Path, version: int) -> None:
    """Rewrite the recorded schema_version to `version` to simulate an OLDER DB, so the
    next open sees old_version < EXPECTED and hits the migration gate."""
    s = StateStore(db, allow_migrate=True, market_open=False)   # opens current (no migration)
    with s.transaction() as cur:
        cur.execute("UPDATE schema_meta SET value=? WHERE key='schema_version'", (version,))
    s.close()


def _raw_version(db: Path) -> int:
    """Read schema_version with a raw sqlite connection — WITHOUT opening a StateStore
    (which would itself migrate)."""
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        return int(row[0])
    finally:
        conn.close()


# ── the guard must NOT break the normal cases ────────────────────────────────
def test_current_db_opens_for_a_non_boot_opener(tmp_path):
    """The NORMAL case (all 23 non-boot openers on a v44 DB when code expects v44):
    old_version == EXPECTED → no migration → opens fine with allow_migrate=False."""
    db = _fresh_db(tmp_path)
    s = StateStore(db)   # default allow_migrate=False
    assert s.get_schema_version() == EXPECTED_SCHEMA_VERSION
    s.close()


def test_fresh_db_builds_without_migrate_permission(tmp_path):
    """A brand-new DB (no prior version) is a fresh BUILD, not a migration → allowed even
    for a non-boot opener (the migration gate is `old_version is not None and <`)."""
    db = tmp_path / "fresh.db"
    s = StateStore(db)   # allow_migrate=False
    assert s.get_schema_version() == EXPECTED_SCHEMA_VERSION
    s.close()


# ── AC1: only the boot path migrates ─────────────────────────────────────────
def test_non_boot_opener_refuses_pending_migration(tmp_path):
    """AC1: a non-boot opener (allow_migrate=False) REFUSES a pending migration and leaves
    the DB UNTOUCHED (version stays old — no live-table rebuild by a cron/report/research)."""
    db = _fresh_db(tmp_path)
    _stamp_version(db, EXPECTED_SCHEMA_VERSION - 1)
    with pytest.raises(MigrationNotPermitted):
        StateStore(db)   # default allow_migrate=False
    assert _raw_version(db) == EXPECTED_SCHEMA_VERSION - 1   # not migrated


def test_boot_path_migrates_off_market(tmp_path):
    """AC1: main.py's boot path (allow_migrate=True) off-market (market_open=False) IS the
    one sanctioned migrator — it applies the pending migration."""
    db = _fresh_db(tmp_path)
    _stamp_version(db, EXPECTED_SCHEMA_VERSION - 1)
    s = StateStore(db, allow_migrate=True, market_open=False)
    assert s.get_schema_version() == EXPECTED_SCHEMA_VERSION
    s.close()


# ── AC2: even the boot path refuses while the market is open ──────────────────
def test_boot_path_refuses_during_market(tmp_path):
    """AC2: a mid-session boot (allow_migrate=True, market_open=True) with a pending
    migration REFUSES — a schema change pushed during market hours is a rule violation;
    fail loud rather than rebuild the live table under a running market."""
    db = _fresh_db(tmp_path)
    _stamp_version(db, EXPECTED_SCHEMA_VERSION - 1)
    with pytest.raises(MigrationNotPermitted):
        StateStore(db, allow_migrate=True, market_open=True)
    assert _raw_version(db) == EXPECTED_SCHEMA_VERSION - 1   # not migrated


# ── AC3: a blocked migration fires the CRITICAL alert (proven, like the V2 test) ──
def test_blocked_migration_fires_critical_sentinel(tmp_path):
    """AC3: FORCE a blocked migration → a CRITICAL sentinel is dropped in the DB's dir,
    naming the exact pending migration. An untested alert path is not an alert path."""
    db = _fresh_db(tmp_path)
    _stamp_version(db, EXPECTED_SCHEMA_VERSION - 1)
    with pytest.raises(MigrationNotPermitted):
        StateStore(db)   # non-boot → refuse + sentinel (sentinel_dir = db.parent = tmp_path)
    flags = list(tmp_path.glob("critical_alert_*.flag"))
    assert flags, "no CRITICAL sentinel written on a blocked migration (AC3)"
    text = flags[0].read_text(encoding="utf-8")
    assert f"v{EXPECTED_SCHEMA_VERSION - 1}" in text
    assert f"v{EXPECTED_SCHEMA_VERSION}" in text
    assert "state_store" in text   # source_module
