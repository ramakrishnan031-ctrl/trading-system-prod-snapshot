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
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Optional


def _parse_hhmm(s: str, default: time) -> time:
    """Parse 'HH:MM' to a time; fall back to `default` on any error."""
    m = _TIME_RE.match(s or "")
    if not m:
        return default
    hh, mm = int(m.group(1)), int(m.group(2))
    return time(hh, mm) if (0 <= hh <= 23 and 0 <= mm <= 59) else default

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.exceptions import ConfigMissingError, ConfigSchemaError

_DEFAULT_PATH = Path("config/cron_registry.yaml")
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})")

# Phase 2.2: category is DERIVED from the existing `cadence` (single source of
# truth — no duplicate field on all 33 jobs). An explicit `category:` override
# wins only where a job's real-world expectation differs from its cadence.
_VALID_CATEGORIES = ("MARKET_DAY", "DAILY", "WEEKLY", "MONTHLY", "ON_DEMAND")
_CADENCE_TO_CATEGORY = {
    "market_day": "MARKET_DAY",
    "intraday": "MARKET_DAY",   # intraday jobs run on market days
    "daily": "DAILY",
    "hourly": "DAILY",          # hourly jobs run every day
    "weekly": "WEEKLY",
    "monthly": "MONTHLY",
}
# Phase 2.3: how the Officer verifies a job ran. heartbeat_db = cron_heartbeat
# row (python jobs); exit_code_file = a marker the cron line writes ($? + ts);
# log_marker = a success token grepped from a log; none = not verifiable.
_VALID_DETECTION = ("heartbeat_db", "exit_code_file", "log_marker", "none")


def resolve_category(cadence: str, explicit: Optional[str] = None) -> str:
    """Phase 2.2: the effective category. Explicit override wins; otherwise
    derived from cadence. Unknown cadence -> ON_DEMAND (never crash)."""
    if explicit:
        return explicit
    return _CADENCE_TO_CATEGORY.get(cadence, "ON_DEMAND")


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
    # ── Phase 2: visibility/detection (all OPTIONAL; sensible defaults derived) ──
    category: Optional[str] = None            # override of the cadence-derived category
    heartbeat_required: Optional[bool] = None # default = monitored
    detection_method: Optional[str] = None    # default derived from type/monitored
    excluded_reason: Optional[str] = None     # why a job is intentionally not monitored

    @field_validator("category")
    @classmethod
    def _valid_category(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_CATEGORIES:
            raise ValueError(f"category must be one of {_VALID_CATEGORIES}, got {v!r}")
        return v

    @field_validator("detection_method")
    @classmethod
    def _valid_detection(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_DETECTION:
            raise ValueError(f"detection_method must be one of {_VALID_DETECTION}, got {v!r}")
        return v

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

    # ── Phase 2: effective (resolved) visibility properties ────────────────────
    @property
    def effective_category(self) -> str:
        return resolve_category(self.cadence, self.category)

    @property
    def effective_heartbeat_required(self) -> bool:
        """Whether the Officer expects a completion signal. Defaults to the
        existing `monitored` flag unless explicitly overridden."""
        return self.monitored if self.heartbeat_required is None else self.heartbeat_required

    @property
    def effective_detection_method(self) -> str:
        """How the Officer verifies this job. Explicit wins; else derived:
        heartbeat-required jobs -> heartbeat_db; every other job ->
        exit_code_file (a marker the cron line writes). A missing marker is
        reported as 'no signal' (informational), never a false CRITICAL."""
        if self.detection_method is not None:
            return self.detection_method
        if self.effective_heartbeat_required:
            return "heartbeat_db"
        return "exit_code_file"


class OfficerConfig(BaseModel):
    """Phase 6: Cron Officer day-window + alert-timing + Telegram-ban settings.
    Optional `officer:` block in cron_registry.yaml; all fields have defaults so
    an absent block reproduces today's behaviour."""

    model_config = ConfigDict(extra="forbid")

    day_window_start: str = "00:00"        # IST; counter window start (reset)
    day_window_end: str = "23:59"          # IST; counter window end
    morning_briefing_time: str = "09:20"   # IST; morning Telegram/email trigger
    eod_floor_time: str = "18:45"          # IST; earliest the EOD report fires
    eod_gap_minutes: int = 5               # fire +N min after the last expected job
    telegram_ban_until: Optional[str] = None  # "YYYY-MM-DD" inclusive; None = no ban

    def ban_active(self, today: date) -> bool:
        """True if `today` (IST date) is on/before telegram_ban_until."""
        if not self.telegram_ban_until:
            return False
        try:
            return today <= date.fromisoformat(self.telegram_ban_until)
        except ValueError:
            return False


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

    def __init__(self, jobs: Dict[str, CronJob],
                 officer: Optional[OfficerConfig] = None) -> None:
        self._jobs = dict(jobs)
        self.officer = officer or OfficerConfig()

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

        officer_raw = data.get("officer") or {}
        if not isinstance(officer_raw, dict):
            raise ConfigSchemaError("cron_registry.yaml 'officer' must be a mapping", path=str(path))
        try:
            officer = OfficerConfig(**officer_raw)
        except Exception as exc:
            raise ConfigSchemaError(f"officer config: {exc}", path=str(path)) from exc
        return cls(jobs, officer)

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

    def last_due_time(self, d: date, config_dir: Path = Path("config")) -> Optional[time]:
        """Latest scheduled HH:MM among jobs due on `d`, EXCLUDING the EOD officer
        itself (so the officer can fire after the last real job)."""
        times = [j.due_time for j in self.jobs_due_on(d, config_dir)
                 if j.due_time is not None and j.name != "cron_officer_eod"]
        return max(times) if times else None

    def eod_report_time(self, d: date, config_dir: Path = Path("config")) -> time:
        """Phase 5.2/6.4: when the EOD report should fire — max(floor, last due
        job + gap_minutes). Used to derive the crontab line and as the report's
        cut-off `before_time`."""
        floor = _parse_hhmm(self.officer.eod_floor_time, time(18, 45))
        last = self.last_due_time(d, config_dir)
        if last is None:
            return floor
        cand = (datetime.combine(d, last) + timedelta(minutes=self.officer.eod_gap_minutes)).time()
        return max(floor, cand)

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
