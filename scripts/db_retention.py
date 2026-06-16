"""
scripts/db_retention.py -- Trading System v2  (O5: DB-level row retention)

Purpose
-------
The append-only / high-volume tables in the state store have no row expiry.
Log *files* get a 30-day cron cleanup; DB rows never did, so over a year the
SQLite file bloats and the nightly ``.backup`` slows down (db_schema_review
15-Jun-2026, observation O5).

This script deletes rows older than a per-table retention window. It is the
DB analogue of the log-file cleanup cron. Critical/audit tables get long
windows (``fm_ledger`` = 365d); bulky non-critical analytics get short ones
(``candles`` / ``system_metrics`` = 90d).

Safety
------
* Only DELETEs rows strictly OLDER than the cutoff date (``< cutoff``), never
  today's rows.
* Each table is pruned in its own transaction; a failure on one table does not
  abort the others (an error is logged and the script continues, returning a
  non-zero exit code so the cron log flags it).
* ``--dry-run`` reports counts without deleting.
* VACUUM is OFF by default (it takes an exclusive lock and rewrites the whole
  file). Pass ``--vacuum`` to reclaim freed pages after a large prune; intended
  only for the nightly off-hours run.

Run after the nightly backup (e.g. 02:30 IST), so a pre-prune snapshot always
exists.

Exit codes
----------
    0 -- success (all tables pruned, or dry-run)
    1 -- one or more tables failed, or the store could not be opened
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import today_ist

# Retention policy: table -> (date_column_expr, default_retention_days).
#
# ``date_column_expr`` is compared against a 'YYYY-MM-DD' cutoff string. Tables
# carrying the O4 STORED ``date`` column use it directly (index-backed);
# the rest fall back to substr(ts, 1, 10).
#
# Windows are deliberately conservative for capital/audit tables:
#   fm_ledger          365d -- capital audit ledger; daily-loss only needs
#                              today, but keep a year for forensics/recon.
#   reconciliation_log 180d -- recon forensics.
#   bulky analytics     90d -- candles/system_metrics/webhook_audit/screener.
DEFAULT_RETENTION: dict[str, tuple[str, int]] = {
    "candles":            ("date", 90),
    "system_metrics":     ("date", 90),
    "webhook_audit":      ("date", 90),
    "screener_results":   ("substr(ts, 1, 10)", 90),
    "reconciliation_log": ("substr(ts, 1, 10)", 180),
    "fm_ledger":          ("date", 365),
}


def _cutoff(date_iso: str, days: int) -> str:
    """Return the 'YYYY-MM-DD' cutoff = date_iso - days."""
    return (
        datetime.strptime(date_iso, "%Y-%m-%d") - timedelta(days=days)
    ).strftime("%Y-%m-%d")


def run_retention(
    *,
    store: StateStore,
    date_iso: str,
    log: logging.Logger,
    policy: dict[str, tuple[str, int]] | None = None,
    overrides: dict[str, int] | None = None,
    dry_run: bool = False,
    vacuum: bool = False,
) -> dict[str, int]:
    """
    Prune rows older than each table's retention window. Returns a mapping of
    table -> rows deleted (or rows that WOULD be deleted, when ``dry_run``).

    ``overrides`` maps a table name to a replacement retention-days value.
    A failure pruning one table is logged and recorded as ``-1`` for that table;
    other tables are still processed.
    """
    policy = policy or DEFAULT_RETENTION
    overrides = overrides or {}
    results: dict[str, int] = {}
    failures = 0

    for table, (date_col, default_days) in policy.items():
        days = overrides.get(table, default_days)
        cutoff = _cutoff(date_iso, days)
        try:
            if dry_run:
                row = store.fetch_one(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE {date_col} < ?",
                    (cutoff,),
                )
                count = int(row["n"]) if row else 0
                log.info(
                    "db_retention.%s: %d rows < %s (%dd) [dry-run]",
                    table, count, cutoff, days,
                )
            else:
                with store.transaction() as cur:
                    cur.execute(
                        f"DELETE FROM {table} WHERE {date_col} < ?", (cutoff,)
                    )
                    count = cur.rowcount
                log.info(
                    "db_retention.%s_pruned: %d (cutoff=%s, %dd)",
                    table, count, cutoff, days,
                )
            results[table] = count
        except Exception as exc:  # noqa: BLE001 -- one table must not abort the rest
            failures += 1
            results[table] = -1
            log.error("db_retention.%s_failed: %s", table, exc, exc_info=True)

    total = sum(c for c in results.values() if c > 0)
    log.info(
        "db_retention.complete",
        extra={"total_pruned": total, "failures": failures, "dry_run": dry_run},
    )

    if vacuum and not dry_run and failures == 0:
        # VACUUM cannot run inside a transaction; execute() autocommits. The
        # main DB and the attached analytics DB (O6) are vacuumed separately —
        # most freed space lives in analytics (candles/system_metrics).
        for schema in ("", "analytics"):
            target = schema or "main"
            try:
                store.execute(f"VACUUM {schema}".strip())
                log.info("db_retention.vacuum_complete: %s", target)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                log.error("db_retention.vacuum_failed[%s]: %s", target, exc,
                          exc_info=True)

    results["_failures"] = failures
    return results


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="db_retention",
        description="O5: delete DB rows older than per-table retention windows.",
    )
    parser.add_argument("--db", metavar="PATH", default=None)
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None,
                        help="Reference 'today'; defaults to today (IST).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report counts without deleting.")
    parser.add_argument("--vacuum", action="store_true",
                        help="VACUUM after pruning to reclaim file space "
                             "(exclusive lock; off-hours only).")
    parser.add_argument("--set", metavar="TABLE=DAYS", action="append", default=[],
                        help="Override one table's retention window "
                             "(repeatable), e.g. --set candles=30.")
    return parser.parse_args(argv)


def _parse_overrides(items: list[str], log: logging.Logger) -> dict[str, int]:
    overrides: dict[str, int] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--set expects TABLE=DAYS, got {item!r}")
        table, _, days = item.partition("=")
        table = table.strip()
        if table not in DEFAULT_RETENTION:
            raise SystemExit(
                f"--set unknown table {table!r}; "
                f"valid: {', '.join(DEFAULT_RETENTION)}"
            )
        try:
            overrides[table] = int(days)
        except ValueError:
            raise SystemExit(f"--set {table}: days must be an integer, got {days!r}")
    return overrides


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("db_retention")

    overrides = _parse_overrides(args.set, log)

    db_path = Path(args.db) if args.db else _ROOT / "data_store" / "trading_system.db"
    try:
        store = StateStore(db_path=db_path)
    except Exception as exc:
        log.error("db_retention: state_store open failed: %s", exc)
        return 1

    date_iso = args.date or today_ist()
    log.info("db_retention.start", extra={"date": date_iso, "dry_run": args.dry_run})

    try:
        results = run_retention(
            store=store,
            date_iso=date_iso,
            log=log,
            overrides=overrides,
            dry_run=args.dry_run,
            vacuum=args.vacuum,
        )
    except Exception as exc:
        log.error("db_retention.unexpected_error: %s", exc, exc_info=True)
        store.close()
        return 1

    store.close()
    return 1 if results.get("_failures", 0) else 0


if __name__ == "__main__":
    sys.exit(main())
