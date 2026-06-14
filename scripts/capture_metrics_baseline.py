"""
scripts/capture_metrics_baseline.py -- Trading System v2  FIX-150

Purpose:
    Capture system health metrics every 5 minutes during market hours.
    Stores CPU, memory, DB size, log size, threads, disk usage in
    system_metrics table (schema v24).

    At EOD (--summarize), computes daily avg/min/max/p95 and stores in
    system_metrics_daily. Sends Telegram alert if any metric exceeds
    80% of historical max (drift detection).

Usage:
    # Capture a single snapshot (cron: every 5 min during market)
    python scripts/capture_metrics_baseline.py

    # EOD daily summary
    python scripts/capture_metrics_baseline.py --summarize

    # Dry-run (print but don't store)
    python scripts/capture_metrics_baseline.py --dry-run

Cron:
    */5 9-15 * * 1-5  capture snapshot
    0 16 * * 1-5      --summarize (after market close)

Exit codes:
    0 -- success
    1 -- error
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import db_connect  # O6: analytics tables live in analytics.db (ATTACHed)

from core.logger import get_logger
from core.time_authority import now_ist, today_ist

DB_PATH = _ROOT / "data_store" / "trading_system.db"
LOG_DIR = _ROOT / "logs"

_DRIFT_THRESHOLD = 0.80


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="capture_metrics_baseline",
        description="FIX-150: System health metrics capture.",
    )
    parser.add_argument("--db", metavar="PATH", default=str(DB_PATH))
    parser.add_argument("--summarize", action="store_true",
                        help="Compute and store daily summary")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _get_cpu_pct() -> float:
    try:
        import psutil
        return psutil.cpu_percent(interval=1)
    except ImportError:
        return -1.0


def _get_memory_mb() -> float:
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        return proc.memory_info().rss / (1024 * 1024)
    except ImportError:
        return -1.0


def _get_db_size_mb(db_path: str) -> float:
    p = Path(db_path)
    if not p.exists():
        return 0.0
    size = p.stat().st_size
    wal = Path(db_path + "-wal")
    if wal.exists():
        size += wal.stat().st_size
    return size / (1024 * 1024)


def _get_log_size_mb(log_dir: Path) -> float:
    if not log_dir.exists():
        return 0.0
    total = sum(f.stat().st_size for f in log_dir.glob("*.log") if f.is_file())
    return total / (1024 * 1024)


def _get_thread_count() -> int:
    try:
        import psutil
        return psutil.Process(os.getpid()).num_threads()
    except ImportError:
        import threading
        return threading.active_count()


def _get_open_fds() -> int:
    try:
        import psutil
        return psutil.Process(os.getpid()).num_fds()
    except (ImportError, AttributeError):
        # num_fds() not available on Windows
        return -1


def _get_disk_used_pct() -> float:
    try:
        import psutil
        usage = psutil.disk_usage(str(_ROOT))
        return usage.percent
    except ImportError:
        return -1.0


def capture_snapshot(db_path: str, log, dry_run: bool = False) -> dict:
    """Capture a single system metrics snapshot."""
    metrics = {
        "timestamp": now_ist().isoformat(),
        "cpu_pct": _get_cpu_pct(),
        "memory_mb": _get_memory_mb(),
        "db_size_mb": _get_db_size_mb(db_path),
        "log_size_mb": _get_log_size_mb(LOG_DIR),
        "open_fds": _get_open_fds(),
        "thread_count": _get_thread_count(),
        "disk_used_pct": _get_disk_used_pct(),
    }

    log.info(
        "metrics_snapshot cpu=%.1f%% mem=%.1fMB db=%.1fMB log=%.1fMB threads=%d disk=%.1f%%",
        metrics["cpu_pct"], metrics["memory_mb"], metrics["db_size_mb"],
        metrics["log_size_mb"], metrics["thread_count"], metrics["disk_used_pct"],
    )

    if dry_run:
        return metrics

    conn = db_connect.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO system_metrics
               (timestamp, cpu_pct, memory_mb, db_size_mb, log_size_mb,
                open_fds, thread_count, disk_used_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                metrics["timestamp"], metrics["cpu_pct"], metrics["memory_mb"],
                metrics["db_size_mb"], metrics["log_size_mb"],
                metrics["open_fds"], metrics["thread_count"], metrics["disk_used_pct"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return metrics


def compute_daily_summary(db_path: str, log, dry_run: bool = False) -> dict | None:
    """Compute daily avg/min/max/p95 from today's snapshots."""
    date_iso = today_ist()

    conn = db_connect.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT cpu_pct, memory_mb, db_size_mb, log_size_mb,
                      open_fds, thread_count, disk_used_pct
               FROM system_metrics
               WHERE DATE(timestamp) = ?
               ORDER BY timestamp""",
            (date_iso,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        log.info("metrics_summary: no snapshots for %s", date_iso)
        return None

    def _stats(values):
        vals = sorted(v for v in values if v >= 0)
        if not vals:
            return {"avg": 0, "min_val": 0, "max_val": 0, "p95": 0}
        n = len(vals)
        avg = sum(vals) / n
        p95_idx = min(int(n * 0.95), n - 1)
        return {
            "avg": round(avg, 2),
            "min_val": round(vals[0], 2),
            "max_val": round(vals[-1], 2),
            "p95": round(vals[p95_idx], 2),
        }

    cols = ["cpu_pct", "memory_mb", "db_size_mb", "log_size_mb",
            "open_fds", "thread_count", "disk_used_pct"]
    col_idx = {c: i for i, c in enumerate(cols)}
    summary = {}
    for col in cols:
        values = [row[col_idx[col]] for row in rows]
        summary[col] = _stats(values)

    log.info(
        "metrics_daily_summary date=%s snapshots=%d cpu_p95=%.1f%% mem_p95=%.1fMB disk_p95=%.1f%%",
        date_iso, len(rows),
        summary["cpu_pct"]["p95"], summary["memory_mb"]["p95"],
        summary["disk_used_pct"]["p95"],
    )

    if dry_run:
        return summary

    conn = db_connect.connect(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO system_metrics_daily
               (date, snapshot_count,
                cpu_avg, cpu_max, cpu_p95,
                memory_avg_mb, memory_max_mb, memory_p95_mb,
                db_size_mb, log_size_mb,
                thread_avg, thread_max,
                disk_used_avg_pct, disk_used_max_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                date_iso, len(rows),
                summary["cpu_pct"]["avg"], summary["cpu_pct"]["max_val"],
                summary["cpu_pct"]["p95"],
                summary["memory_mb"]["avg"], summary["memory_mb"]["max_val"],
                summary["memory_mb"]["p95"],
                summary["db_size_mb"]["max_val"], summary["log_size_mb"]["max_val"],
                summary["thread_count"]["avg"], summary["thread_count"]["max_val"],
                summary["disk_used_pct"]["avg"], summary["disk_used_pct"]["max_val"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    _check_drift(db_path, date_iso, summary, log)

    return summary


def _check_drift(db_path: str, date_iso: str, summary: dict, log) -> None:
    """Alert if any metric exceeds 80% of historical max."""
    conn = db_connect.connect(db_path)
    try:
        row = conn.execute(
            """SELECT MAX(cpu_max) as cpu, MAX(memory_max_mb) as mem,
                      MAX(disk_used_max_pct) as disk, MAX(db_size_mb) as db
               FROM system_metrics_daily
               WHERE date < ?""",
            (date_iso,),
        ).fetchone()
    finally:
        conn.close()

    if row is None or row[0] is None:
        return

    hist_cpu, hist_mem, hist_disk, hist_db = row
    alerts = []

    if hist_cpu and summary["cpu_pct"]["max_val"] > hist_cpu * _DRIFT_THRESHOLD:
        alerts.append(f"CPU: {summary['cpu_pct']['max_val']:.1f}% (hist max: {hist_cpu:.1f}%)")
    if hist_mem and summary["memory_mb"]["max_val"] > hist_mem * _DRIFT_THRESHOLD:
        alerts.append(f"Memory: {summary['memory_mb']['max_val']:.1f}MB (hist max: {hist_mem:.1f}MB)")
    if hist_disk and summary["disk_used_pct"]["max_val"] > hist_disk * _DRIFT_THRESHOLD:
        alerts.append(f"Disk: {summary['disk_used_pct']['max_val']:.1f}% (hist max: {hist_disk:.1f}%)")
    if hist_db and summary["db_size_mb"]["max_val"] > hist_db * _DRIFT_THRESHOLD:
        alerts.append(f"DB size: {summary['db_size_mb']['max_val']:.1f}MB (hist max: {hist_db:.1f}MB)")

    if alerts:
        msg = "Metrics drift detected:\n" + "\n".join(f"- {a}" for a in alerts)
        log.warning("metrics_drift: %s", msg)
        _send_telegram(msg, log)


def _send_telegram(message: str, log) -> None:
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=log)
        if not notifier:
            return
        notifier.send(
            severity="WARNING",
            title="System Metrics Drift",
            body=message,
            source_module="capture_metrics_baseline",
        )
    except Exception as exc:
        log.debug("metrics_telegram_failed: %s", exc)


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("capture_metrics_baseline")

    try:
        if args.summarize:
            compute_daily_summary(args.db, log, dry_run=args.dry_run)
        else:
            capture_snapshot(args.db, log, dry_run=args.dry_run)

        try:
            from utils.cron_heartbeat import record_heartbeat
            record_heartbeat("capture_metrics_baseline")
        except Exception:
            pass

        return 0
    except Exception as exc:
        log.error("capture_metrics_baseline failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
