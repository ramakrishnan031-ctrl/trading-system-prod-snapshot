"""
scripts/preflight/checks/security.py -- Group 7 (Security), Phase A.

fail2ban + auditd are checked in Group 2 (services) -- NOT duplicated here. This
group covers: security-watcher liveness (state-file freshness), copy-protection
master switch, auth-recovery handler import, and the copy time-lock window sanity.
All alert-only (security != trading-safety; never blocks trading).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from scripts.preflight.base import Check, CheckContext, CheckResult, Criticality

SECURITY_STATE_MAX_AGE_SEC = 600   # watcher runs every ~60s; 10 min => clearly down


def _file_age_seconds(path: Path) -> Optional[float]:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _current_hour_ist() -> int:
    try:
        from core.time_authority import now_ist
        return now_ist().hour
    except Exception:  # pragma: no cover
        from datetime import datetime
        return datetime.now().hour


def _load_security_yaml(config_dir: Path) -> dict:
    import yaml
    f = config_dir / "security.yaml"
    if not f.exists():
        return {}
    return yaml.safe_load(f.read_text(encoding="utf-8")) or {}


class SecurityWatcherAliveCheck(Check):
    name = "security_watcher_alive"
    group = "Security"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 10

    def _state_file(self, ctx: CheckContext) -> Path:
        # data_store/security_state.json sits beside the DB (db_path = data_store/...)
        return ctx.db_path.parent / "security_state.json"

    def run(self, ctx: CheckContext) -> CheckResult:
        age = _file_age_seconds(self._state_file(ctx))
        if age is None:
            return self._failed("security_state.json missing — security-watcher not running?")
        if age > SECURITY_STATE_MAX_AGE_SEC:
            return self._failed(
                f"security_state.json stale ({int(age)}s > {SECURITY_STATE_MAX_AGE_SEC}s) — watcher down?",
                age_sec=int(age))
        return self._passed(f"security-watcher fresh ({int(age)}s)", age_sec=int(age))


class CopyProtectionStateCheck(Check):
    name = "copy_protection_state"
    group = "Security"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 20

    def run(self, ctx: CheckContext) -> CheckResult:
        f = ctx.config_dir / "security.yaml"
        if not f.exists():
            return self._failed("config/security.yaml missing")
        try:
            data = _load_security_yaml(ctx.config_dir)
        except Exception as exc:
            return self._failed(f"security.yaml parse error: {exc}")
        cp = data.get("copy_protection") or {}
        if cp.get("enabled") is True:
            return self._passed("copy protection enabled (hard-block)")
        return self._failed("copy protection DISABLED (expected enabled)",
                            enabled=cp.get("enabled"))


class AuthRecoveryPrimedCheck(Check):
    name = "auth_recovery_primed"
    group = "Security"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 30

    def run(self, ctx: CheckContext) -> CheckResult:
        try:
            import broker.auth_recovery as ar
        except Exception as exc:
            return self._failed(f"auth_recovery import failed: {exc}")
        needed = ("classify_broker_auth_error", "get_public_ip", "build_ip403_alert_body")
        missing = [fn for fn in needed if not hasattr(ar, fn)]
        if missing:
            return self._failed(f"auth_recovery missing: {', '.join(missing)}")
        return self._passed("auth_recovery importable + primed (IP-403 self-recovery)")


class TimeLockWindowCheck(Check):
    """Sanity: pre-flight at 08:30 should be OUTSIDE the 18:00-08:00 copy time-lock.
    WARN (informational) if inside -- VM->PC copies would be hard-blocked then."""

    name = "time_lock_window"
    group = "Security"
    criticality = Criticality.WARN
    expected_duration_ms = 10

    def run(self, ctx: CheckContext) -> CheckResult:
        start, end = 18, 8
        try:
            cp = _load_security_yaml(ctx.config_dir).get("copy_protection") or {}
            start = int(cp.get("time_lock_start", 18))
            end = int(cp.get("time_lock_end", 8))
        except Exception:
            pass
        h = _current_hour_ist()
        in_lock = (h >= start) or (h < end)   # wraps midnight
        if in_lock:
            return self._warn(
                f"inside copy time-lock ({start:02d}:00-{end:02d}:00) — VM→PC copies blocked (hour={h})",
                hour=h)
        return self._passed(f"outside copy time-lock (hour={h})", hour=h)


CHECKS = [
    SecurityWatcherAliveCheck(),
    CopyProtectionStateCheck(),
    AuthRecoveryPrimedCheck(),
    TimeLockWindowCheck(),
]
