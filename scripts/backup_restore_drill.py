"""
scripts/backup_restore_drill.py -- Trading System v2  FIX-149

Purpose:
    Automated backup restoration drill. Picks latest backup, restores to a
    temporary DB, verifies schema integrity, table completeness, FK consistency,
    and data sanity. Outputs a drill report.

    Designed to run monthly via cron (1st of month, 03:00 IST) or manually.

Usage:
    cd ~/systems/trading-system
    PYTHONPATH=. python scripts/backup_restore_drill.py

    Options:
      --backup-dir   Path to backups directory (default: data_store/backups/)
      --output-dir   Path for drill reports (default: docs/)
      --quiet        Suppress stdout, only write report

Exit codes:
    0 -- drill passed
    1 -- drill failed (restore or verification issues)
    2 -- no backups found
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.state_store import EXPECTED_SCHEMA_VERSION


EXPECTED_TABLES = [
    "schema_meta",
    "signals",
    "trades",
    "orders",
    "capital_snapshot",
    "system_events",
    "session",
    "fm_ledger",
    "kill_switch_state",
    "webhook_audit",
    "eod_squareoff_log",
    "reconciliation_log",
    "screener_results",
    "smart_tgt_state",
    "innings",
    "gate_state",
    "trade_excursions",
    "pnl_reconciliation",
    "telegram_alerts",
    "trade_journal",
    "position_reconciliation",
    "strategy_metrics",
    "shadow_trades",
    "fno_ban",
    "eod_verification",
    "cron_heartbeat",
    # O6 (v28): candles, system_metrics and system_metrics_daily were relocated
    # to analytics.db, which is a SEPARATE file (backed up separately). They are
    # intentionally NOT expected in a trading_system.db backup restore.
]

DATA_TABLES = ["signals", "trades", "orders"]

SAMPLE_QUERIES = [
    ("closed_trades", "SELECT COUNT(*) AS cnt FROM trades WHERE status='CLOSED'"),
    ("latest_signal_date", "SELECT MAX(DATE(triggered_at)) AS dt FROM signals"),
    ("open_trades", "SELECT COUNT(*) AS cnt FROM trades WHERE status='OPEN'"),
    ("total_orders", "SELECT COUNT(*) AS cnt FROM orders"),
]

FK_CHECKS = [
    (
        "trades_signal_fk",
        """SELECT t.trade_id FROM trades t
           LEFT JOIN signals s ON t.signal_id = s.signal_id
           WHERE s.signal_id IS NULL""",
    ),
    (
        "orders_trade_fk",
        """SELECT o.order_id FROM orders o
           LEFT JOIN trades t ON o.trade_id = t.trade_id
           WHERE t.trade_id IS NULL""",
    ),
]


def find_latest_backup(backup_dir: Path) -> Path | None:
    backups = sorted(
        backup_dir.glob("trading_system-*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return backups[0] if backups else None


def restore_backup(backup_path: Path, target_path: Path) -> None:
    shutil.copy2(backup_path, target_path)


def run_integrity_check(db_path: Path) -> tuple[bool, str]:
    conn = sqlite3.connect(str(db_path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        return result == "ok", result
    finally:
        conn.close()


def check_schema_version(db_path: Path) -> tuple[bool, int]:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            return False, -1
        version = int(row[0])
        return version == EXPECTED_SCHEMA_VERSION, version
    finally:
        conn.close()


def check_tables(db_path: Path) -> tuple[list[str], list[str], dict[str, int]]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        present = {r[0] for r in rows}

        missing = [t for t in EXPECTED_TABLES if t not in present]
        extra = sorted(present - set(EXPECTED_TABLES) - {"sqlite_sequence"})

        counts: dict[str, int] = {}
        for table in sorted(present - {"sqlite_sequence"}):
            cnt = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
            counts[table] = cnt

        return missing, extra, counts
    finally:
        conn.close()


def check_foreign_keys(db_path: Path) -> list[tuple[str, int]]:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    violations = []
    try:
        for name, query in FK_CHECKS:
            orphans = conn.execute(query).fetchall()
            if orphans:
                violations.append((name, len(orphans)))
    finally:
        conn.close()
    return violations


def run_sample_queries(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(db_path))
    results = {}
    try:
        for name, query in SAMPLE_QUERIES:
            row = conn.execute(query).fetchone()
            results[name] = str(row[0]) if row else "NULL"
    finally:
        conn.close()
    return results


def check_wal_clean(db_path: Path) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        return True
    except Exception:
        return False
    finally:
        conn.close()


def run_drill(backup_dir: Path, output_dir: Path, quiet: bool = False) -> bool:
    report_lines: list[str] = []

    def log(msg: str) -> None:
        report_lines.append(msg)
        if not quiet:
            print(msg)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    log(f"# Backup Restore Drill Report - {date_str}")
    log(f"")
    log(f"**Executed at:** {now.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"**Expected schema version:** {EXPECTED_SCHEMA_VERSION}")
    log(f"")

    # Step 1: Find latest backup
    backup_path = find_latest_backup(backup_dir)
    if backup_path is None:
        log(f"## FAIL: No backups found in {backup_dir}")
        log(f"")
        log(f"Ensure the nightly backup cron is running.")
        _write_report(output_dir, date_str, report_lines)
        return False

    backup_size = backup_path.stat().st_size
    log(f"## Backup Selected")
    log(f"- **File:** {backup_path.name}")
    log(f"- **Size:** {backup_size:,} bytes ({backup_size / 1024 / 1024:.1f} MB)")
    log(f"- **Modified:** {datetime.fromtimestamp(backup_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"")

    # Step 2: Restore to temp location
    tmp_dir = tempfile.mkdtemp(prefix="backup_drill_")
    test_db = Path(tmp_dir) / "restore_test.db"
    passed = True

    try:
        restore_backup(backup_path, test_db)
        log(f"## Restore")
        log(f"- Restored to: `{test_db}`")
        log(f"- Restored size: {test_db.stat().st_size:,} bytes")
        log(f"")

        # Step 3: Integrity check
        integrity_ok, integrity_result = run_integrity_check(test_db)
        log(f"## Integrity Check")
        if integrity_ok:
            log(f"- PRAGMA integrity_check: **PASSED**")
        else:
            log(f"- PRAGMA integrity_check: **FAILED** ({integrity_result})")
            passed = False
        log(f"")

        # Step 4: Schema version
        version_ok, version = check_schema_version(test_db)
        log(f"## Schema Version")
        if version_ok:
            log(f"- Version {version}: **MATCHES** (expected {EXPECTED_SCHEMA_VERSION})")
        else:
            log(f"- Version {version}: **MISMATCH** (expected {EXPECTED_SCHEMA_VERSION})")
            passed = False
        log(f"")

        # Step 5: Table completeness
        missing, extra, counts = check_tables(test_db)
        log(f"## Tables ({len(counts)} found, {len(EXPECTED_TABLES)} expected)")
        if missing:
            log(f"- **MISSING:** {', '.join(missing)}")
            passed = False
        else:
            log(f"- All {len(EXPECTED_TABLES)} expected tables present: **PASSED**")
        if extra:
            log(f"- Extra tables (informational): {', '.join(extra)}")
        log(f"")

        # Row counts
        log(f"### Row Counts")
        log(f"| Table | Rows |")
        log(f"|-------|------|")
        for table, cnt in sorted(counts.items()):
            marker = ""
            if table in DATA_TABLES and cnt == 0:
                marker = " (WARNING: empty)"
            log(f"| {table} | {cnt:,}{marker} |")
        log(f"")

        # Step 6: Foreign key check
        fk_violations = check_foreign_keys(test_db)
        log(f"## Foreign Key Integrity")
        if fk_violations:
            for name, count in fk_violations:
                log(f"- **{name}:** {count} orphaned rows")
            passed = False
        else:
            log(f"- No orphaned foreign keys: **PASSED**")
        log(f"")

        # Step 7: WAL checkpoint
        wal_ok = check_wal_clean(test_db)
        log(f"## WAL Checkpoint")
        log(f"- Checkpoint: {'**CLEAN**' if wal_ok else '**FAILED**'}")
        log(f"")

        # Step 8: Sample queries
        samples = run_sample_queries(test_db)
        log(f"## Sample Queries")
        for name, value in samples.items():
            log(f"- {name}: {value}")
        log(f"")

        # Verdict
        log(f"## Verdict")
        if passed:
            log(f"**DRILL PASSED** -- backup restoration verified successfully.")
        else:
            log(f"**DRILL FAILED** -- see issues above.")

    finally:
        # Cleanup
        try:
            if test_db.exists():
                os.remove(test_db)
            wal_file = Path(str(test_db) + "-wal")
            shm_file = Path(str(test_db) + "-shm")
            if wal_file.exists():
                os.remove(wal_file)
            if shm_file.exists():
                os.remove(shm_file)
            os.rmdir(tmp_dir)
        except Exception:
            pass

    _write_report(output_dir, date_str, report_lines)

    # Record heartbeat
    try:
        from utils.cron_heartbeat import record_heartbeat
        record_heartbeat("backup_restore_drill")
    except Exception:
        pass

    return passed


def _write_report(output_dir: Path, date_str: str, lines: list[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"backup_drill_{date_str}.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts.backup_restore_drill",
        description="Backup restoration drill -- verify backup integrity.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=_ROOT / "data_store" / "backups",
        help="Directory containing backup .db files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_ROOT / "docs",
        help="Directory for drill report output",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout output",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    ok = run_drill(
        backup_dir=args.backup_dir,
        output_dir=args.output_dir,
        quiet=args.quiet,
    )
    sys.exit(0 if ok else 1)
