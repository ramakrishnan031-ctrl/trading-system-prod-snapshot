"""
scripts/preflight/checks/services.py -- Group 2 (OS / support services).

ONLY the always-on support + OS daemons are checked in Phase A. The app-lifecycle
services (trading-system, trading-watchman) are intentionally NOT here: per Fork 1
the app auto-starts on token arrival (~08:15-09:00), so being down at 08:30 is
EXPECTED -- those are checked in Phase B and are never auto-started by pre-flight
(token-watcher owns that).

Spec-correction (grounding 21-Jun): cron_officer / tgt-retry / reconciler are NOT
systemd services (cron job / threads) -- removed from this list.

Auto-fix = `sudo -n systemctl start <svc>` (idempotent; ubuntu has NOPASSWD sudo
on the VM). The readers are module-level so tests can monkeypatch them.
"""
from __future__ import annotations

import subprocess

from scripts.preflight.base import Check, CheckContext, CheckResult, Criticality, FixResult


# security-watcher is a periodic-oneshot (Restart=always + RestartSec=60) that spends most of
# its time in "activating" between cycles -- treat that as healthy, else it false-FAILs every
# few seconds. (alert-watcher was in this class until 16-Jul-2026; it is now a long-lived
# `--loop` daemon in state "active", already covered below -- the monitoring canary's respawn
# probe, not this pre-market gate, is what now catches an alert-watcher respawn regression.)
HEALTHY_STATES = ("active", "activating", "reloading")


def _active_state(service: str) -> str:
    try:
        out = subprocess.run(["systemctl", "is-active", service],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _is_active(service: str) -> bool:
    return _active_state(service) in HEALTHY_STATES


def _start(service: str) -> None:
    subprocess.run(["sudo", "-n", "systemctl", "start", service],
                   capture_output=True, text=True, timeout=25)


class ServiceActiveCheck(Check):
    group = "Services"
    auto_fixable = True
    fix_action = "systemctl_start_service"
    expected_duration_ms = 200

    def __init__(self, service: str, criticality: Criticality = Criticality.CRITICAL,
                 auto_fixable: bool = True):
        self.service = service
        stem = service.replace(".service", "").replace("-", "_")
        self.name = f"svc_{stem}"
        self.criticality = criticality
        # Phase B uses auto_fixable=False for trading-system: a down app is alert-only
        # (token-watcher owns the lifecycle; pre-flight must not start trading).
        self.auto_fixable = auto_fixable

    def run(self, ctx: CheckContext) -> CheckResult:
        state = _active_state(self.service)
        if state in HEALTHY_STATES:
            return self._passed(f"{self.service} {state}")
        return self._failed(f"{self.service} {state} (expected active)")

    def fix(self, ctx: CheckContext) -> FixResult:
        before = "active" if _is_active(self.service) else "inactive"
        _start(self.service)
        active = _is_active(self.service)
        return FixResult(
            success=active,
            action=self.fix_action,
            before_state=before,
            after_state="active" if active else "inactive",
            error_msg="" if active else f"{self.service} still inactive after start",
        )


# Always-on support + OS daemons (NOT trading-system / trading-watchman).
SUPPORT_SERVICES = (
    "alert-watcher.service",
    "token-watcher.service",
    "security-watcher.service",
    "cron",
    "fail2ban",
    "auditd",
)

CHECKS = [ServiceActiveCheck(svc) for svc in SUPPORT_SERVICES]
