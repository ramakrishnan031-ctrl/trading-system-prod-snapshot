"""
scripts/preflight/base.py -- the Check contract shared by every pre-flight check.

Dependency-light on purpose (no heavy core imports) so checks + tests import it
cheaply. Every check is a small class with class-level metadata + a run() (and an
optional fix() when auto_fixable). The orchestrator wraps run()/fix() in try/except
so a buggy check can NEVER crash the whole pre-flight -- a raised exception is
converted to a FAIL CheckResult.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional
from core.account_registry import primary_account_tag


class Criticality(enum.Enum):
    """How loud a failing check is. ALERT-ONLY -- never blocks trading."""

    CRITICAL = "CRITICAL"
    WARN = "WARN"
    INFO = "INFO"


class Status(enum.Enum):
    """Outcome of a single check run."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    AUTOFIXED = "AUTOFIXED"   # failed, then a safe auto-fix made it pass
    SKIPPED = "SKIPPED"       # not applicable (e.g. broker check in paper mode)


@dataclass
class CheckResult:
    """What a check returns. ``metrics`` is free-form per-check telemetry."""

    status: Status
    detail: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        """True if this result is not a hard failure (PASS/WARN/AUTOFIXED/SKIPPED)."""
        return self.status is not Status.FAIL


@dataclass
class FixResult:
    """Outcome of an auto-fix attempt -- audited verbatim."""

    success: bool
    action: str = ""
    before_state: str = ""
    after_state: str = ""
    error_msg: str = ""


@dataclass
class CheckContext:
    """
    Everything a check needs, injected by the orchestrator. Kept explicit so
    checks are pure(ish) and unit-testable -- a test builds a CheckContext
    pointing at a temp DB / config dir and calls check.run(ctx) directly.
    """

    config_dir: Path
    db_path: Path
    mode: str = "live"            # "live" | "paper" -- only broker checks branch on it
    account: str = field(default_factory=primary_account_tag)
    as_of_date: date = field(default_factory=date.today)
    phase: str = "A"             # "A" | "B" | "C"
    dry_run: bool = False        # True -> never send alerts / never mutate
    app_up: bool = False         # True -> trading-system.service is running (Phase B/C)
    extra: Dict[str, Any] = field(default_factory=dict)  # adapter handle, etc.

    @property
    def is_paper(self) -> bool:
        return str(self.mode).lower() == "paper"


class Check:
    """
    Base class for every pre-flight check.

    Subclasses set the class attributes and implement run() (and fix() when
    auto_fixable=True). Keep run() side-effect-free unless it is explicitly a
    fix; the orchestrator decides when (and whether) to call fix().
    """

    name: str = ""
    group: str = ""
    criticality: Criticality = Criticality.WARN
    auto_fixable: bool = False
    expected_duration_ms: int = 100

    def run(self, ctx: CheckContext) -> CheckResult:  # pragma: no cover - abstract
        raise NotImplementedError(f"{type(self).__name__}.run() not implemented")

    def fix(self, ctx: CheckContext) -> FixResult:  # pragma: no cover - optional
        raise NotImplementedError(f"{type(self).__name__}.fix() not implemented")

    # ── small helpers shared by subclasses ──────────────────────────────────
    @staticmethod
    def _passed(detail: str = "", **metrics: Any) -> CheckResult:
        return CheckResult(Status.PASS, detail=detail, metrics=metrics)

    @staticmethod
    def _failed(detail: str, **metrics: Any) -> CheckResult:
        return CheckResult(Status.FAIL, detail=detail, metrics=metrics)

    @staticmethod
    def _warn(detail: str, **metrics: Any) -> CheckResult:
        return CheckResult(Status.WARN, detail=detail, metrics=metrics)

    @staticmethod
    def _skipped(detail: str, **metrics: Any) -> CheckResult:
        return CheckResult(Status.SKIPPED, detail=detail, metrics=metrics)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Check {self.group}/{self.name} {self.criticality.value}>"
