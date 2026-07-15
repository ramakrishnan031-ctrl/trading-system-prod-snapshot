"""
utils/cron_heartbeat.py — FIX-145

Simple utility for cron scripts to record successful execution heartbeats.

Usage in any cron script:
    from utils.cron_heartbeat import record_heartbeat

    def main():
        start = time.time()
        # ... do work ...
        record_heartbeat("my_script", duration_sec=time.time() - start)
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from core.state_store import StateStore
from core.time_authority import now_ist


# F2 (15-Jul-2026): FUNCTIONAL status, recorded INDEPENDENTLY of the EXECUTION `status`.
#   EXECUTION  status ("the script ran / exited")   -> the `status` column (unchanged).
#   FUNCTIONAL status ("the artifact was generated / delivered / validated") -> encoded
#   in the existing free-form `message` column as `[func=<STATUS>] <message>` (NO schema
#   change). A job whose script exits 0 but whose real function failed (empty CSV,
#   undelivered email) records status=SUCCESS + functional_status=<the real outcome>, so
#   a delivery/artifact failure can no longer read as SUCCESS. The Cron Officer surfaces
#   the two states separately via parse_functional_status().
_FUNC_PREFIX = "[func="


def _encode_functional(functional_status: Optional[str], message: Optional[str]) -> Optional[str]:
    """Prefix `message` with a structured [func=...] marker; execution status stays in the
    `status` column. Returns the combined message (or the original when no functional status)."""
    if not functional_status:
        return message
    tail = (message or "").strip()
    return f"{_FUNC_PREFIX}{functional_status}]" + (f" {tail}" if tail else "")


def parse_functional_status(message: Optional[str]) -> Optional[str]:
    """Extract the FUNCTIONAL status from a heartbeat `message`, or None. Inverse of
    `_encode_functional` — used by the Cron Officer to show functional vs execution."""
    if not message or not message.startswith(_FUNC_PREFIX):
        return None
    end = message.find("]")
    if end < 0:
        return None
    return message[len(_FUNC_PREFIX):end].strip() or None


def record_heartbeat(
    job_name: str,
    status: str = "SUCCESS",
    duration_sec: Optional[float] = None,
    message: Optional[str] = None,
    functional_status: Optional[str] = None,
    db_path: Path = Path("data_store/trading_system.db"),
) -> bool:
    """
    Record a cron job heartbeat to the database.

    Call this at the END of a cron script after successful completion.

    Args:
        job_name: Short name for the cron job (e.g., "daily_report")
        status: EXECUTION status — SUCCESS (default) | PARTIAL | FAILED | SKIPPED
        duration_sec: Optional job duration
        message: Optional diagnostic message
        functional_status: F2 — the FUNCTIONAL outcome (did the artifact/delivery
            actually succeed), recorded independently of `status`. Encoded into
            `message` as `[func=<functional_status>] ...` (no schema change).

    Returns:
        True if heartbeat was recorded, False on error (never raises)
    """
    try:
        if not db_path.exists():
            return False

        store = StateStore(db_path)
        store.insert_cron_heartbeat(
            job_name=job_name,
            executed_at=now_ist().isoformat(),
            status=status,
            duration_sec=duration_sec,
            message=_encode_functional(functional_status, message),
        )
        store.close()
        return True
    except Exception:
        return False


class HeartbeatTimer:
    """
    Context manager for timing and recording a cron job heartbeat.

    Usage:
        with HeartbeatTimer("my_script") as hb:
            # do work
            if something_wrong:
                hb.status = "PARTIAL"
                hb.message = "some warning"
        # heartbeat recorded automatically on exit

    TASK #3 (Cron Officer): pass alert=True to also send a per-job alert on exit
    (FAILED always; SUCCESS only for jobs marked critical in the registry). The
    alerting lives in alerts/cron_alerts.py (lazy import) so record_heartbeat()
    stays a pure, dependency-light DB write.
    """

    def __init__(
        self,
        job_name: str,
        db_path: Path = Path("data_store/trading_system.db"),
        alert: bool = False,
        critical: Optional[bool] = None,
        config_dir: Path = Path("config"),
    ):
        self.job_name = job_name
        self.db_path = db_path
        self.status = "SUCCESS"                       # EXECUTION status
        self.functional_status: Optional[str] = None  # F2: FUNCTIONAL status (set by the job)
        self.message: Optional[str] = None
        self._start: float = 0.0
        self._started_iso: Optional[str] = None
        self.alert = alert
        self.critical = critical
        self.config_dir = Path(config_dir)

    def __enter__(self) -> "HeartbeatTimer":
        self._start = time.time()
        self._started_iso = now_ist().isoformat()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        duration = time.time() - self._start
        if exc_type is not None:
            self.status = "FAILED"
            self.message = f"{exc_type.__name__}: {exc_val}"
        ended_iso = now_ist().isoformat()

        record_heartbeat(
            job_name=self.job_name,
            status=self.status,
            duration_sec=duration,
            message=self.message,
            functional_status=self.functional_status,
            db_path=self.db_path,
        )

        if self.alert:
            try:
                from alerts.cron_alerts import alert_job_result

                alert_job_result(
                    self.job_name,
                    self.status,
                    duration_sec=duration,
                    started=self._started_iso,
                    ended=ended_iso,
                    message=self.message,
                    critical=self.critical,
                    config_dir=self.config_dir,
                )
            except Exception:
                pass  # alerting must never affect the job's exit

        return False  # don't suppress exceptions


def skip_if_non_trading_day(
    job_name: str,
    config_dir: Path | str = Path("config"),
    db_path: Path = Path("data_store/trading_system.db"),
) -> bool:
    """
    TASK #3 Layer 6: holiday guard for market_day_only jobs.

    If today (IST) is NOT an NSE trading day (weekend or holiday), record a
    SKIPPED heartbeat and return True so the caller can `return 0` early:

        if skip_if_non_trading_day("eod_verify"):
            return 0

    Falls back to a plain weekday check if the holiday calendar is unreadable.
    """
    from core.time_authority import now_ist

    try:
        from utils.holiday_guard import is_trading_day

        trading = is_trading_day(now_ist().date(), Path(config_dir))
    except Exception:
        trading = now_ist().weekday() < 5

    if not trading:
        record_heartbeat(
            job_name,
            status="SKIPPED",
            message="non-trading day (weekend/NSE holiday)",
            db_path=db_path,
        )
        return True
    return False
