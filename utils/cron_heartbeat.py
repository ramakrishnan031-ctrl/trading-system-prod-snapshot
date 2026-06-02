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


def record_heartbeat(
    job_name: str,
    status: str = "SUCCESS",
    duration_sec: Optional[float] = None,
    message: Optional[str] = None,
    db_path: Path = Path("data_store/trading_system.db"),
) -> bool:
    """
    Record a cron job heartbeat to the database.

    Call this at the END of a cron script after successful completion.

    Args:
        job_name: Short name for the cron job (e.g., "daily_report")
        status: SUCCESS (default) | PARTIAL | FAILED
        duration_sec: Optional job duration
        message: Optional diagnostic message

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
            message=message,
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
    """

    def __init__(
        self,
        job_name: str,
        db_path: Path = Path("data_store/trading_system.db"),
    ):
        self.job_name = job_name
        self.db_path = db_path
        self.status = "SUCCESS"
        self.message: Optional[str] = None
        self._start: float = 0.0

    def __enter__(self) -> "HeartbeatTimer":
        self._start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        duration = time.time() - self._start
        if exc_type is not None:
            self.status = "FAILED"
            self.message = f"{exc_type.__name__}: {exc_val}"

        record_heartbeat(
            job_name=self.job_name,
            status=self.status,
            duration_sec=duration,
            message=self.message,
            db_path=self.db_path,
        )
        return False  # don't suppress exceptions
