"""
tests/unit/test_schema_version_failfast.py — fresh-audit §B.1 guard.

`core/state_store.py::_initialize_schema` must REFUSE to open a database whose
stored schema_version is NEWER than EXPECTED_SCHEMA_VERSION, instead of letting
the UNCONDITIONAL executescript() silently stamp the version DOWN (the pre-fix
silent-downgrade: v42 P1 DB opened by v41 code after a code revert booted clean
at v41, stranding the newer schema's tables).

Schema-backed — exercises the real StateStore against the real core/schema.sql,
no mocks (the class of test the 04-Jul audit recommended for raw-SQL paths).

  T1 — newer DB  → fail-fast, stored version NOT mutated   (red→green core)
  T2 — older DB  → still migrates up to EXPECTED           (migrate-up intact)
  T3 — equal/fresh DB → boots + terminal-guard trigger present (brick check)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.state_store import (
    StateStore,
    EXPECTED_SCHEMA_VERSION,
    SchemaVersionMismatch,
)


def _stamp_version(db: Path, version: int) -> None:
    """Overwrite the stored schema_version on an existing DB (raw sqlite)."""
    raw = sqlite3.connect(str(db))
    try:
        raw.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
            (str(version),),
        )
        raw.commit()
    finally:
        raw.close()


def _read_version(db: Path) -> int:
    raw = sqlite3.connect(str(db))
    try:
        row = raw.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    finally:
        raw.close()
    return int(row[0])


def _trigger_exists(db: Path, name: str) -> bool:
    raw = sqlite3.connect(str(db))
    try:
        row = raw.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (name,),
        ).fetchone()
    finally:
        raw.close()
    return row is not None


# ── T1 — newer DB → fail-fast, no silent downgrade ───────────────────────────
def test_newer_db_fails_fast_without_downgrade(tmp_path):
    db = tmp_path / "newer.db"
    # Build a real current-schema DB, then stamp it one version NEWER than code.
    StateStore(db).close()
    newer = EXPECTED_SCHEMA_VERSION + 1
    _stamp_version(db, newer)

    # Re-open with the current code: must REFUSE (fail-fast), not downgrade.
    with pytest.raises(SchemaVersionMismatch):
        StateStore(db)

    # The stored version must be UNTOUCHED — executescript never ran to stamp
    # it back down (that WAS the silent downgrade the guard now prevents).
    assert _read_version(db) == newer, "newer schema_version was mutated on refuse"


# ── T2 — older DB → migrate-up path intact (no brick) ────────────────────────
def test_older_db_still_migrates_up(tmp_path):
    db = tmp_path / "older.db"
    # Build a current-schema DB, then stamp it one version OLDER so the
    # `old_version < EXPECTED` migrate-up gate runs. Proves the new
    # `old_version > EXPECTED` guard does not interfere with migrate-up.
    StateStore(db).close()
    _stamp_version(db, EXPECTED_SCHEMA_VERSION - 1)

    store = StateStore(db)
    try:
        assert store.get_schema_version() == EXPECTED_SCHEMA_VERSION
    finally:
        store.close()


# ── T3 — equal/fresh DB → boots + terminal-guard trigger present ─────────────
def test_equal_version_boots_and_creates_trigger(tmp_path):
    db = tmp_path / "fresh.db"
    store = StateStore(db)
    try:
        # Equal-version boot (the path every production start takes) is unaffected.
        assert store.get_schema_version() == EXPECTED_SCHEMA_VERSION
    finally:
        store.close()
    # executescript still ran on the legal path → the terminal-guard trigger
    # (schema.sql, Wave-5) must exist. Proves the guard didn't short-circuit boot.
    assert _trigger_exists(db, "trg_trades_terminal_status_guard")
