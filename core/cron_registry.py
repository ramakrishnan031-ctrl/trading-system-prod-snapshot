"""
core/cron_registry.py -- Trading System v2  (TASK #3 / Cron Officer)

Loads config/cron_registry.yaml -- the single source of truth for every cron
job -- and answers the questions the Cron Officer + check_cron_drift need:

    * which jobs run today?            (cadence + NSE-holiday aware)
    * which jobs should have emitted a heartbeat by now?  (drift detection)
    * which jobs are critical?

Layer 0-1: depends only on stdlib + pydantic + yaml + core.exceptions +
utils.holiday_guard. No DB, no network, no logging side-effects at import.
"""
from __future__ import annotations

import re
from datetime import date, time
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from core.exceptions import ConfigMissingError, ConfigSchemaError

_DEFAULT_PATH = Path("config/cron_registry.yaml")
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})")


class CronJob(BaseModel):
    """One row of the registry (AR2-style frozen-ish schema)."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""  # injected from the YAML mapping key
    script: str
    schedule: str
    type: str = Field(pattern="^(python|shell)$")
    critical: bool = False
    market_day_only: bool = False
    cadence: str = Field(pattern="^(daily|market_day|intraday|hourly|weekly|monthly)$")
    monitored: bool = False
    weekday: Optional[int] = None       # 0=Mon..6=Sun (weekly cadence)
    day_of_month: Optional[int] = None  # 1..28 (monthly cadence)

    @property
    def due_time(self) -> Optional[time]:
        """Leading HH:MM of the schedule, or None for intraday/hourly/no-time."""
        m = _TIME_RE.match(self.schedule)
        if not m:
            return None
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return time(hh, mm)
        return None


def _is_trading_day(d: date, config_dir: Path) -> bool:
    """is_trading_day with a safe fallback to a plain weekday check if the
    holiday YAML is missing (never crash the registry on a missing calendar)."""
    from utils.holiday_guard import is_trading_day

    try:
        return is_trading_day(d, config_dir)
    except Exception:
        return d.weekday() < 5


class CronRegistry:
    """In-memory view of cron_registry.yaml."""

    def __init__(self, jobs: Dict[str, CronJob]) -> None:
        self._jobs = dict(jobs)

    # ── construction ──────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path = _DEFAULT_PATH) -> "CronRegistry":
        if not path.exists():
            raise ConfigMissingError(f"cron_registry.yaml not found at {path}", path=str(path))
        try:
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigSchemaError(f"cannot parse {path}: {exc}", path=str(path)) from exc

        raw_jobs = data.get("jobs")
        if not isinstance(raw_jobs, dict) or not raw_jobs:
            raise ConfigSchemaError("cron_registry.yaml has no 'jobs' mapping", path=str(path))

        jobs: Dict[str, CronJob] = {}
        for name, spec in raw_jobs.items():
            if not isinstance(spec, dict):
                raise ConfigSchemaError(f"job {name!r} is not a mapping", path=str(path))
            try:
                job = CronJob(name=name, **spec)
            except Exception as exc:  # pydantic ValidationError -> our schema error
                raise ConfigSchemaError(f"job {name!r}: {exc}", path=str(path)) from exc
            # cadence/field consistency
            if job.cadence == "weekly" and job.weekday is None:
                raise ConfigSchemaError(f"job {name!r}: weekly cadence needs 'weekday'", path=str(path))
            if job.cadence == "monthly" and job.day_of_month is None:
                raise ConfigSchemaError(f"job {name!r}: monthly cadence needs 'day_of_month'", path=str(path))
            jobs[name] = job
        return cls(jobs)

    # ── lookups ─────────────────────────────────────────────────────────────────
    def get(self, name: str) -> CronJob:
        if name not in self._jobs:
            raise KeyError(f"cron job {name!r} not in registry")
        return self._jobs[name]

    def all_jobs(self) -> List[CronJob]:
        return list(self._jobs.values())

    def count(self) -> int:
        return len(self._jobs)

    def critical_jobs(self) -> List[CronJob]:
        return [j for j in self._jobs.values() if j.critical]

    # ── scheduling logic ────────────────────────────────────────────────────────
    def is_due_on(self, job: CronJob, d: date, config_dir: Path = Path("config")) -> bool:
        """True if `job` is scheduled to run on date `d`."""
        if job.cadence in ("daily", "hourly"):
            return True
        if job.cadence in ("market_day", "intraday"):
            return _is_trading_day(d, config_dir)
        if job.cadence == "weekly":
            return d.weekday() == job.weekday
        if job.cadence == "monthly":
            return d.day == job.day_of_month
        return False

    def jobs_due_on(self, d: date, config_dir: Path = Path("config")) -> List[CronJob]:
        return [j for j in self._jobs.values() if self.is_due_on(j, d, config_dir)]

    def expected_heartbeat_jobs(
        self,
        d: date,
        config_dir: Path = Path("config"),
        before_time: Optional[time] = None,
    ) -> List[CronJob]:
        """
        Monitored jobs that are due on `d` and (if before_time given) were
        scheduled to run at or before that time — i.e. the set check_cron_drift
        should find a heartbeat for. Jobs with no parseable due_time (intraday/
        hourly) are included regardless of before_time.
        """
        out: List[CronJob] = []
        for j in self._jobs.values():
            if not j.monitored or not self.is_due_on(j, d, config_dir):
                continue
            if before_time is not None and j.due_time is not None and j.due_time > before_time:
                continue
            out.append(j)
        return out


def load_cron_registry(path: Path = _DEFAULT_PATH) -> CronRegistry:
    """Module-level convenience loader."""
    return CronRegistry.load(path)
