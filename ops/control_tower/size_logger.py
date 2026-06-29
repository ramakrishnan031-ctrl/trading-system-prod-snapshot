"""ops/control_tower/size_logger.py -- Control Tower Phase 1a (F3).

Minimal daily size-logger. Per run it computes FRESH disk%, backup-dir size,
log-dir size and DB size, then UPSERTs ONE control_tower_trends row for today.
Starting it now seeds the trend history so the 7-day backup trend is ready when
the aggregator/report (Phase 1b/1c) ship.

Design notes:
  * disk_used_pct is computed FRESH via shutil.disk_usage() -- NOT the broken
    system_metrics.disk_used_pct (live value -1.0 / daily 0.0).
  * The UPSERT touches ONLY the four size columns (ON CONFLICT(date) DO UPDATE),
    leaving health_score / *_count for the aggregator -- so the size-logger and
    the aggregator can both write the same date row without clobbering.
  * NO aggregation, NO findings, NO report, NO alerts. That is Phase 1b/1c.

Run (cron 17:05 Mon-Fri): ops/control_tower/size_logger.py
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import db_connect  # noqa: E402

_IST = timezone(timedelta(hours=5, minutes=30))
_MB = 1024 * 1024
_log = logging.getLogger("control_tower.size_logger")


def _dir_size_bytes(path: Path) -> int:
    """Total size of files under `path` (recursive); 0 if absent."""
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def compute_sizes(root: Path, db_path: Path) -> dict:
    """Gather the four size metrics for `root` / `db_path`. Pure (filesystem
    reads only); deterministic given the environment."""
    usage = shutil.disk_usage(str(root))
    disk_used_pct = round(usage.used / usage.total * 100.0, 2) if usage.total else None
    return {
        "disk_used_pct": disk_used_pct,
        "backup_size_mb": round(_dir_size_bytes(root / "data_store" / "backups") / _MB, 2),
        "log_size_mb": round(_dir_size_bytes(root / "logs") / _MB, 2),
        "db_size_mb": round(db_path.stat().st_size / _MB, 2) if db_path.exists() else None,
    }


def upsert_trend(conn, date_str: str, sizes: dict) -> None:
    """UPSERT only the SIZE fields of the date's control_tower_trends row; the
    aggregator (1b/1c) owns health_score + the *_count columns."""
    conn.execute(
        """
        INSERT INTO control_tower_trends
            (date, disk_used_pct, backup_size_mb, log_size_mb, db_size_mb)
        VALUES (:date, :disk_used_pct, :backup_size_mb, :log_size_mb, :db_size_mb)
        ON CONFLICT(date) DO UPDATE SET
            disk_used_pct  = excluded.disk_used_pct,
            backup_size_mb = excluded.backup_size_mb,
            log_size_mb    = excluded.log_size_mb,
            db_size_mb     = excluded.db_size_mb
        """,
        {"date": date_str, **sizes},
    )
    conn.commit()


def run(db_path: Path, root: Path, now: datetime | None = None) -> dict:
    """Compute sizes for `root` and UPSERT today's trend row into `db_path`.
    Returns the row that was written. No heartbeat / no I/O beyond the UPSERT."""
    now = now or datetime.now(_IST)
    date_str = now.strftime("%Y-%m-%d")
    sizes = compute_sizes(root, db_path)
    conn = db_connect.connect(db_path, attach=False)
    try:
        upsert_trend(conn, date_str, sizes)
    finally:
        conn.close()
    return {"date": date_str, **sizes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Control Tower size-logger (Phase 1a F3)")
    ap.add_argument("--db", default=str(_ROOT / "data_store" / "trading_system.db"),
                    help="DB to UPSERT control_tower_trends into")
    ap.add_argument("--root", default=str(_ROOT),
                    help="project root for the disk/backup/log size reads")
    ap.add_argument("--no-heartbeat", action="store_true",
                    help="skip the cron_heartbeat write (used by the render harness)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db_path = Path(args.db)
    started = datetime.now(_IST)
    t0 = started.timestamp()
    try:
        result = run(db_path, Path(args.root), started)
        _log.info("control_tower.size_logger wrote %s", result)
        status = "SUCCESS"
        rc = 0
    except Exception as exc:  # never crash silently — heartbeat FAILED, exit 1
        _log.error("control_tower.size_logger FAILED: %s", exc, exc_info=True)
        status = "FAILED"
        rc = 1

    if not args.no_heartbeat:
        try:
            from utils.cron_heartbeat import record_heartbeat
            record_heartbeat(
                "control_tower_size_logger",
                status=status,
                duration_sec=datetime.now(_IST).timestamp() - t0,
                db_path=db_path,
            )
        except Exception:  # heartbeat must never change the job's exit
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
