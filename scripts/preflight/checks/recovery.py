"""
scripts/preflight/checks/recovery.py -- Group 9 (Logs & audit hygiene).

Paths corrected during grounding: logs live in ``logs/`` and markers in
``data_store/cron_marks/`` (NOT /var/log/trading or /tmp/cron_marks).
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from scripts.preflight.base import Check, CheckContext, CheckResult, Criticality, FixResult


def _project_root(ctx: CheckContext) -> Path:
    return ctx.db_path.resolve().parent.parent


def _ensure_dir(path: Path) -> bool:
    path.mkdir(parents=True, exist_ok=True)
    try:
        # Owner-only (rwx). These dirs (logs/, data_store/cron_marks/) hold
        # operational data only the service user needs; nothing on the single-user
        # VM requires group/other access. Least privilege (SATS triage 2026-06-22).
        # insecure-file-permissions flags this and suggests 0o644, but that is
        # FILE-oriented advice: a directory needs the owner traversal (x) bit, so
        # 0o700 -- not 0o644 -- is the correct least-privilege value here. The
        # nosemgrep directive below MUST stay on the line directly above os.chmod.
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path.exists() and os.access(path, os.W_OK)


class TodayLogWritableCheck(Check):
    name = "today_log_writable"
    group = "Recovery"
    criticality = Criticality.CRITICAL
    auto_fixable = True
    fix_action = "ensure_dir_perms"
    expected_duration_ms = 20

    def _logs_dir(self, ctx: CheckContext) -> Path:
        return _project_root(ctx) / "logs"

    def run(self, ctx: CheckContext) -> CheckResult:
        d = self._logs_dir(ctx)
        if not d.exists():
            return self._failed(f"logs dir missing: {d}")
        if not os.access(d, os.W_OK):
            return self._failed(f"logs dir not writable: {d}")
        return self._passed(f"{d} writable")

    def fix(self, ctx: CheckContext) -> FixResult:
        d = self._logs_dir(ctx)
        before = "exists" if d.exists() else "missing"
        ok = _ensure_dir(d)
        return FixResult(ok, action=self.fix_action, before_state=before,
                         after_state="writable" if ok else "still-bad",
                         error_msg="" if ok else f"could not make {d} writable")


class CronMarksDirWritableCheck(Check):
    name = "cron_marks_dir_writable"
    group = "Recovery"
    criticality = Criticality.WARN
    auto_fixable = True
    fix_action = "ensure_dir_perms"
    expected_duration_ms = 20

    def _dir(self, ctx: CheckContext) -> Path:
        return _project_root(ctx) / "data_store" / "cron_marks"

    def run(self, ctx: CheckContext) -> CheckResult:
        d = self._dir(ctx)
        if d.exists() and os.access(d, os.W_OK):
            return self._passed(f"{d} writable")
        return self._failed(f"cron_marks dir missing/not writable: {d}")

    def fix(self, ctx: CheckContext) -> FixResult:
        d = self._dir(ctx)
        before = "exists" if d.exists() else "missing"
        ok = _ensure_dir(d)
        return FixResult(ok, action=self.fix_action, before_state=before,
                         after_state="writable" if ok else "still-bad",
                         error_msg="" if ok else f"could not make {d} writable")


class AlertBacklogCheck(Check):
    """A pile of undelivered critical_alert_*.flag sentinels => alert-watcher is
    likely not consuming. WARN (visibility) -- never blocks."""

    name = "alert_backlog"
    group = "Recovery"
    criticality = Criticality.WARN
    expected_duration_ms = 30
    THRESHOLD = 50

    def run(self, ctx: CheckContext) -> CheckResult:
        d = _project_root(ctx) / "data_store"
        flags = list(d.glob("critical_alert_*.flag")) if d.exists() else []
        n = len(flags)
        if n > self.THRESHOLD:
            return self._warn(f"{n} undelivered alert sentinels (alert-watcher backlog?)",
                              backlog=n)
        return self._passed(f"{n} pending alert sentinels", backlog=n)


class LogRotationCheck(Check):
    """Visibility-only: did yesterday's system log get written? SKIP (not WARN) when
    absent, since a non-trading day legitimately has no prior log."""

    name = "log_rotation"
    group = "Recovery"
    criticality = Criticality.INFO
    expected_duration_ms = 10

    def run(self, ctx: CheckContext) -> CheckResult:
        d = _project_root(ctx) / "logs"
        yday = (ctx.as_of_date - timedelta(days=1)).isoformat()
        f = d / f"system_{yday}.log"
        if f.exists():
            return self._passed(f"yesterday's log present ({f.name})")
        return self._skipped(f"no system log for {yday} (ok if prior day was non-trading)")


CHECKS = [
    TodayLogWritableCheck(),
    CronMarksDirWritableCheck(),
    AlertBacklogCheck(),
    LogRotationCheck(),
]
