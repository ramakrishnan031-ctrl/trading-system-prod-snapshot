"""
scripts/preflight/checks/config_sanity.py -- Group "Config Sanity" (BUILD 2, 25-Jun).

Surfaces the Config Sanity Auditor (core/config_auditor) into the 08:30 Phase-A
check-set -> the consolidated 09:20 email + Telegram. ONE auditor run (memoised on the
shared CheckContext) feeds 7 per-group rows (A-G), each PASS / WARN / FAIL with a clear
message. Parity: the audit is pure with no mode branch, so paper and live evaluate
identically.

The BLOCK-level contradiction gate already fails the app at STARTUP (config_loader's
SystemConfig model_validator). Here group A is the belt-and-suspenders CRITICAL row: it
can only surface a contradiction if the running config is contradictory, in which case
load_all() itself raised and `app_config_valid` (Config Integrity) has already failed.
"""
from __future__ import annotations

from scripts.preflight.base import Check, CheckContext, CheckResult, Criticality

_CACHE_KEY = "_config_sanity_report"


def _build_report(ctx: CheckContext):
    """Build (or return the memoised) ConfigAuditReport for this run as a
    (report, error) tuple. On any failure report is None and error is a string. The
    result is cached on the shared CheckContext so the 7 group rows audit ONCE."""
    cached = ctx.extra.get(_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        from core.config_loader import load_all
        from core.config_auditor import audit_app_config

        app_config = load_all(ctx.config_dir)
        strategies = None
        try:
            from strategies.loader import StrategyLoader
            strategies = StrategyLoader().load_all_strategies(
                ctx.config_dir / "strategies",
                force_intraday_only=app_config.system.force_intraday_only,
            )
        except Exception:  # noqa: BLE001 — group A3 / by-strategy typo simply degrade
            strategies = None
        report = audit_app_config(
            app_config, config_dir=ctx.config_dir, strategies=strategies)
        result = (report, None)
    except Exception as exc:  # noqa: BLE001 — a contradictory config makes load_all raise
        result = (None, f"{type(exc).__name__}: {exc}")
    ctx.extra[_CACHE_KEY] = result
    return result


class _ConfigSanityGroupCheck(Check):
    """One pre-flight row per auditor group. Subclasses set name/criticality/group_code."""

    group = "Config Sanity"
    group_code = "A"
    expected_duration_ms = 220

    def run(self, ctx: CheckContext) -> CheckResult:
        from core.config_auditor import GROUP_TITLES, Severity

        report, error = _build_report(ctx)
        if report is None:
            return self._failed(f"auditor unavailable: {error}")

        findings = report.for_group(self.group_code)
        worst = report.worst_in_group(self.group_code)
        detail = "; ".join(f.message for f in findings) or GROUP_TITLES.get(self.group_code, "")
        if len(detail) > 220:
            detail = detail[:217] + "..."
        metrics = {
            "group": self.group_code,
            "title": GROUP_TITLES.get(self.group_code, self.group_code),
            "findings": [[f.severity.value, f.code, f.message] for f in findings],
        }
        if worst is Severity.BLOCK:
            return self._failed(detail, **metrics)
        if worst is Severity.WARN:
            return self._warn(detail, **metrics)
        return self._passed(detail, **metrics)


class ConfigSanityContradictionsCheck(_ConfigSanityGroupCheck):
    name = "A_contradictions"
    group_code = "A"
    criticality = Criticality.CRITICAL   # the only group that can BLOCK


class ConfigSanitySingleSourceCheck(_ConfigSanityGroupCheck):
    name = "B_single_source"
    group_code = "B"
    criticality = Criticality.WARN


class ConfigSanityCapitalRelativeCheck(_ConfigSanityGroupCheck):
    name = "C_capital_relative"
    group_code = "C"
    criticality = Criticality.WARN


class ConfigSanityActiveOverridesCheck(_ConfigSanityGroupCheck):
    name = "D_active_overrides"
    group_code = "D"
    criticality = Criticality.INFO       # visibility; a typo'd override still WARNs via status


class ConfigSanityLaunchPhaseCheck(_ConfigSanityGroupCheck):
    name = "E_launch_phase"
    group_code = "E"
    criticality = Criticality.INFO


class ConfigSanityStaleDefaultsCheck(_ConfigSanityGroupCheck):
    name = "F_stale_defaults"
    group_code = "F"
    criticality = Criticality.WARN


class ConfigSanityCrossFieldCheck(_ConfigSanityGroupCheck):
    name = "G_cross_field"
    group_code = "G"
    criticality = Criticality.WARN


CHECKS = [
    ConfigSanityContradictionsCheck(),
    ConfigSanitySingleSourceCheck(),
    ConfigSanityCapitalRelativeCheck(),
    ConfigSanityActiveOverridesCheck(),
    ConfigSanityLaunchPhaseCheck(),
    ConfigSanityStaleDefaultsCheck(),
    ConfigSanityCrossFieldCheck(),
]
