"""
scripts/preflight/startup_hook.py -- on-demand pre-flight if a restart missed the
cron-scheduled phase.

If trading-system restarts in the morning window [06:00, 09:20) IST AFTER a phase's
scheduled time but that phase's sentinel shows it did not run (the cron missed it
because the box / service was down), launch the phase on-demand as a best-effort
detached subprocess. No-op on a normal morning (cron runs the phase regardless of
the app). Never raises; never blocks startup.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from datetime import time as _time
from pathlib import Path
from typing import Callable, List, Optional

from scripts.preflight import sentinel as _sentinel

try:
    from core.logger import get_logger
    _log = get_logger("preflight.startup_hook")
except Exception:  # pragma: no cover
    import logging
    _log = logging.getLogger("preflight.startup_hook")

WINDOW_START = _time(6, 0)
WINDOW_END = _time(9, 20)
# Only the gated phases are force-run on a missed restart (C is a passive watch).
PHASE_TIMES = {"A": _time(8, 30), "B": _time(9, 14)}


def _now_ist() -> datetime:
    try:
        from core.time_authority import now_ist
        return now_ist()
    except Exception:  # pragma: no cover
        return datetime.now()


def _is_trading_day(d, config_dir: Path) -> bool:
    try:
        from utils.holiday_guard import is_trading_day
        return is_trading_day(d, config_dir)
    except Exception:  # pragma: no cover
        return d.weekday() < 5


def _phase_not_run(s: "_sentinel.Sentinel", phase: str, today_iso: str) -> bool:
    if s.run_date != today_iso:
        return True  # stale / absent sentinel -> today's run did not happen
    status = getattr(s, f"phase_{phase.lower()}_status", _sentinel.NOT_STARTED)
    return status in (_sentinel.NOT_STARTED, _sentinel.RUNNING)  # RUNNING = crashed mid-run


def _launch(phase: str, config_dir: Path, db_path: Path,
            launcher: Optional[Callable[[str], None]]) -> None:
    if launcher is not None:
        launcher(phase)
        return
    cmd = [sys.executable, "-m", "scripts.preflight.orchestrator", "--phase", phase,
           "--config-dir", str(config_dir), "--db-path", str(db_path)]
    if phase == "C":
        cmd += ["--watch-sec", "0"]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # detached


def run_on_demand_if_missed(
    config_dir: Path = Path("config"),
    db_path: Path = Path("data_store/trading_system.db"),
    *,
    now: Optional[datetime] = None,
    launcher: Optional[Callable[[str], None]] = None,
    sentinel_path: Path = _sentinel.DEFAULT_SENTINEL_PATH,
) -> List[str]:
    """Launch any past-due-but-not-run phase. Returns the phases launched. Never raises."""
    now = now or _now_ist()
    launched: List[str] = []
    try:
        if not (WINDOW_START <= now.time() < WINDOW_END):
            return launched
        if not _is_trading_day(now.date(), config_dir):
            return launched
        s = _sentinel.load(sentinel_path)
        today_iso = now.date().isoformat()
        for phase, sched in PHASE_TIMES.items():
            if now.time() >= sched and _phase_not_run(s, phase, today_iso):
                _launch(phase, config_dir, db_path, launcher)
                launched.append(phase)
        if launched:
            _log.warning("preflight.startup_hook.launched", extra={"phases": launched})
    except Exception as exc:  # never break startup
        _log.error("preflight.startup_hook.failed", extra={"error": str(exc)})
    return launched
