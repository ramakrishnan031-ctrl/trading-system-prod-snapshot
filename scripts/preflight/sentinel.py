"""
scripts/preflight/sentinel.py -- the pre-flight status sentinel.

Single small JSON file the orchestrator rewrites after EACH phase + after each
auto-fix, so anything can read "is the system ready today?" without re-running
the checks. Read by the Cron Officer morning briefing (embeds a banner) and any
future dashboard.

Path: data_store/preflight/today.json  (ubuntu-writable; NOT /var/run which needs
root and is wiped on reboot). Written atomically (temp file + os.replace) so a
reader never sees a half-written file.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List

DEFAULT_SENTINEL_PATH = Path("data_store/preflight/today.json")

# phase status values
NOT_STARTED = "NOT_STARTED"
RUNNING = "RUNNING"
PASSED = "PASSED"
WARN = "WARN"
CRITICAL = "CRITICAL"

# overall status values
READY = "READY"
READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
CRITICAL_FAILURE = "CRITICAL_FAILURE"


@dataclass
class Sentinel:
    """The on-disk pre-flight state. Field names match the spec's schema."""

    run_date: str = ""
    phase_a_status: str = NOT_STARTED
    phase_a_completed_at: str = ""
    phase_b_status: str = NOT_STARTED
    phase_b_completed_at: str = ""
    phase_c_status: str = NOT_STARTED
    phase_c_completed_at: str = ""
    overall_status: str = READY
    critical_count: int = 0
    warning_count: int = 0
    autofix_count: int = 0
    blocking_failures: List[str] = field(default_factory=list)
    warnings_summary: List[str] = field(default_factory=list)
    alert_id: str = ""
    last_updated: str = ""
    mode: str = "live"
    account: str = "LFL836"


def load(path: Path = DEFAULT_SENTINEL_PATH) -> Sentinel:
    """Load the sentinel, or a fresh default if missing/corrupt."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f for f in Sentinel.__dataclass_fields__}  # type: ignore[attr-defined]
        return Sentinel(**{k: v for k, v in data.items() if k in known})
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return Sentinel()


def write(sentinel: Sentinel, path: Path = DEFAULT_SENTINEL_PATH) -> Path:
    """
    Atomically persist the sentinel. Writes to a temp file in the same dir then
    os.replace()s it over the target (atomic on POSIX + Windows), so a concurrent
    reader (Cron Officer briefing) never sees a partial file.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(sentinel), indent=2, sort_keys=False)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".today.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        # if replace already moved tmp this is a no-op; otherwise clean the stray
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return target
