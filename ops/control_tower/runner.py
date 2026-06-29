"""ops/control_tower/runner.py -- Control Tower Phase 1d daily runner.

The ONE entry point the 17:05 Mon-Fri cron invokes. It supersedes the standalone
Phase-1a size-logger cron (it calls that step itself, so sizes are logged once).
In order:

  1. size-logger (1a)   -> UPSERTs today's trend SIZE columns FIRST (disk%/backup/
                           log/db) so the report's 7-day backup trend has today.
  2. aggregator (1b/1c) -> the 4 monitor adapters + freshness + disk findings ->
                           findings lifecycle (UPSERT + auto-resolve) -> weighted
                           health score + status roll-up -> reporter (pull report
                           HTML/CSV + the noise-controlled Telegram delta via the
                           REAL TelegramNotifier).

Observability: a cron_heartbeat("control_tower") records the run for the cron
framework. The aggregator is told its OWN job name (self_job) so this in-flight
run is never mis-flagged as a "missed" cron job; check_cron_drift (18:00) is the
external monitor that confirms the runner actually heartbeated.

PARITY: the runner + Telegram are mode-neutral (no broker session, read-only DB
aggregation). Run (cron): ops/control_tower/runner.py
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ops.control_tower import aggregator, size_logger  # noqa: E402

_IST = timezone(timedelta(hours=5, minutes=30))
_log = logging.getLogger("control_tower.runner")

# This runner IS the registry job named `control_tower`; the aggregator excludes
# it from its own cron freshness check (its heartbeat is written post-run).
SELF_JOB = "control_tower"
_DEFAULT_DB = _ROOT / "data_store" / "trading_system.db"
_DEFAULT_REPORT_DIR = _ROOT / "data_store" / "control_tower"


def _build_notifier():
    """The REAL TelegramNotifier (env-driven). Returns None when tokens are
    absent (e.g. a PC run) -> the reporter silently skips the push."""
    try:
        from alerts.telegram_notifier import TelegramNotifier
        return TelegramNotifier.from_env(logger=_log)
    except Exception as exc:  # noqa: BLE001 — never let alerting break the run
        _log.error("control_tower.runner: Telegram notifier unavailable: %s", exc)
        return None


def run(db_path: Path, root: Path, config_dir: Path, *, report_dir: Path,
        telegram: bool = True, notifier=None, now: datetime | None = None) -> dict:
    """size-logger (sizes FIRST) -> aggregator (detection + lifecycle + health +
    status + reporter). Returns {"size": ..., "aggregation": ...}. Both steps
    share the SAME `now` so they write the same date row."""
    now = now or datetime.now(_IST)
    size_result = size_logger.run(db_path, root, now)
    if notifier is None and telegram:
        notifier = _build_notifier()
    agg = aggregator.run_aggregation(
        db_path, root, config_dir, now=now, write=True,
        notifier=notifier, report_dir=report_dir, self_job=SELF_JOB)
    return {"size": size_result, "aggregation": agg}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Control Tower daily runner (Phase 1d)")
    ap.add_argument("--db", default=str(_DEFAULT_DB),
                    help="DB the tower reads/writes (default trading_system.db)")
    ap.add_argument("--root", default=str(_ROOT),
                    help="project root for the disk/backup/log/freshness reads")
    ap.add_argument("--config-dir", default=str(_ROOT / "config"))
    ap.add_argument("--report-dir", default=str(_DEFAULT_REPORT_DIR),
                    help="where the HTML/CSV pull report is written")
    ap.add_argument("--no-telegram", action="store_true",
                    help="skip the real Telegram delta push (render/preview)")
    ap.add_argument("--no-heartbeat", action="store_true",
                    help="skip the cron_heartbeat write (render/preview harness)")
    ap.add_argument("--force", action="store_true",
                    help="run even on a non-trading day (render/preview); the cron "
                         "skips holidays/weekends so the freshness check never "
                         "false-flags missing market data when no trading occurred")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db_path = Path(args.db)

    # FOUNDATION RULE — no real alerts on non-trading days. On a weekday NSE
    # holiday the cron still fires (1-5) but no trading occurred, so the EOD/
    # candle/reconstruction stages are LEGITIMATELY absent: running the freshness
    # engine would emit false MISSING findings (and HIGH Telegram pushes). Skip
    # with a SKIPPED heartbeat instead. (disk_monitor + security-watcher cover
    # holidays on their own cadences.) --force bypasses for previews.
    if not args.force:
        try:
            from utils.cron_heartbeat import skip_if_non_trading_day
            if skip_if_non_trading_day(SELF_JOB, config_dir=Path(args.config_dir),
                                       db_path=db_path):
                _log.info("control_tower.runner: non-trading day -> skipped")
                return 0
        except Exception:  # noqa: BLE001 — fail-open if the calendar is unreadable
            _log.warning("control_tower.runner: trading-day check failed; running anyway")

    started = datetime.now(_IST)
    t0 = started.timestamp()
    try:
        result = run(db_path, Path(args.root), Path(args.config_dir),
                     report_dir=Path(args.report_dir),
                     telegram=not args.no_telegram, now=started)
        _log.info("control_tower.runner: %s", result.get("aggregation"))
        status = "SUCCESS"
        rc = 0
    except Exception as exc:  # never crash silently — heartbeat FAILED, exit 1
        _log.error("control_tower.runner FAILED: %s", exc, exc_info=True)
        status = "FAILED"
        rc = 1

    if not args.no_heartbeat:
        try:
            from utils.cron_heartbeat import record_heartbeat
            record_heartbeat(
                SELF_JOB, status=status,
                duration_sec=datetime.now(_IST).timestamp() - t0, db_path=db_path)
        except Exception:  # heartbeat must never change the job's exit
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
