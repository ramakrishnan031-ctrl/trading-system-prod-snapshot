#!/usr/bin/env python3
"""
scripts/deploy_assert.py -- Trading System v2  (15-Jul-2026 monitoring hardening)

A LIGHTWEIGHT pre-deploy assertion — a tripwire, NOT a heavy gate. It BLOCKS a deploy only
on a BLOCKER-class failure (database integrity · schema-version parity · trading/capital
correctness). Monitoring / reporting / observability defects are WARNINGS that never block:
the 15-Jul design decision is that a validated code change must not be held hostage by an
email/alert-delivery problem (open a monitoring-hardening cycle instead).

DB reads are strictly READ-ONLY (URI mode=ro), so this can never migrate the schema on open.

Exit codes:
    0 -- no BLOCKER failed (may include WARNINGs) -> safe to deploy
    2 -- a BLOCKER-class assertion failed -> HOLD the deploy
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.state_store import EXPECTED_SCHEMA_VERSION

BLOCKER = "BLOCKER"
WARNING = "WARNING"

# A single check result: (name, class, ok, detail).
Check = tuple


def _ro_conn(db_path: Path) -> sqlite3.Connection:
    """Read-only URI connection — structurally cannot write or migrate."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.execute("PRAGMA query_only = ON")
    return conn


def assert_db_integrity(db_path: Path) -> list[Check]:
    """BLOCKER-class: quick_check clean, no dangling FKs, schema version == code EXPECTED."""
    checks: list[Check] = []
    if not Path(db_path).exists():
        return [("db.exists", BLOCKER, False, f"DB not found at {db_path}")]
    try:
        conn = _ro_conn(db_path)
    except Exception as exc:  # noqa: BLE001
        return [("db.open", BLOCKER, False, f"cannot open read-only: {exc}")]
    try:
        qc = (conn.execute("PRAGMA quick_check").fetchone() or ["?"])[0]
        checks.append(("db.quick_check", BLOCKER, qc == "ok", str(qc)))
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        checks.append(("db.foreign_key_check", BLOCKER, len(fk) == 0,
                       "clean" if not fk else f"{len(fk)} violation(s)"))
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        ver = int(row[0]) if row and row[0] is not None else None
        checks.append(("db.schema_version", BLOCKER, ver == EXPECTED_SCHEMA_VERSION,
                       f"live={ver} expected={EXPECTED_SCHEMA_VERSION}"))
    finally:
        conn.close()
    return checks


def assert_monitoring(sentinel_dir: Path) -> list[Check]:
    """WARNING-class (NEVER blocks): surface a known-degraded alert channel so the operator
    sees it, but do not hold a validated code deploy for a delivery/observability defect."""
    degraded = (Path(sentinel_dir) / "alert_watcher_degraded.json").exists()
    return [("monitoring.email_delivery", WARNING, not degraded,
             "degraded (ALERT_SMTP_PASSWORD?)" if degraded else "ok")]


def run_assertions(db_path: Path, sentinel_dir: Path) -> tuple[int, list[Check]]:
    checks = assert_db_integrity(db_path) + assert_monitoring(sentinel_dir)
    blocker_failed = any(cls == BLOCKER and not ok for _n, cls, ok, _d in checks)
    return (2 if blocker_failed else 0), checks


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Lightweight pre-deploy assertion (tripwire).")
    p.add_argument("--db", type=Path, default=_ROOT / "data_store" / "trading_system.db")
    p.add_argument("--sentinel-dir", type=Path, default=_ROOT / "data_store")
    args = p.parse_args(argv)

    rc, checks = run_assertions(args.db, args.sentinel_dir)
    for name, cls, ok, detail in checks:
        icon = "✅" if ok else ("🔴" if cls == BLOCKER else "⚠️")
        print(f"{icon} [{cls}] {name}: {detail}")
    print("DEPLOY: " + ("HOLD — BLOCKER failed" if rc == 2 else "OK (blockers clear)"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
