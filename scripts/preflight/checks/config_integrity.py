"""
scripts/preflight/checks/config_integrity.py -- Group 6 (Config Integrity).

config_files_present + required_secrets are ported VERBATIM (same file list, same
secret list) from scripts/premarket_healthcheck.py so the retirement of that
script loses no coverage -- enforced by tests/unit/test_preflight_parity.py.
"""
from __future__ import annotations

import os

from scripts.preflight.base import Check, CheckContext, CheckResult, Criticality

# Ported 1:1 from premarket_healthcheck.CONFIG_FILES (the year file is resolved
# from ctx.as_of_date so it tracks the calendar like the original).
_BASE_CONFIG_FILES = (
    "system_config.yaml",
    "broker_costs.yaml",
    "broker_limits.yaml",
    "slippage_model.yaml",
    "scoring_weights.yaml",
    "scan_webhook_map.yaml",
    "chartink_scanners.yaml",
)

# Ported 1:1 from premarket_healthcheck.REQUIRED_SECRETS.
REQUIRED_SECRETS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHANNEL_PRIMARY",
    "WEBHOOK_SECRET",
)


def config_files_for(year: int) -> list[str]:
    """The full expected config-file set for a given calendar year."""
    return [*_BASE_CONFIG_FILES, f"nse_holidays_{year}.yaml"]


class ConfigFilesPresentCheck(Check):
    name = "config_files_present"
    group = "Config Integrity"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 10

    def run(self, ctx: CheckContext) -> CheckResult:
        expected = config_files_for(ctx.as_of_date.year)
        missing = [f for f in expected if not (ctx.config_dir / f).exists()]
        if missing:
            return self._failed(f"missing config: {', '.join(missing)}", missing=missing)
        return self._passed(f"all {len(expected)} config files present")


class RequiredSecretsCheck(Check):
    name = "required_secrets"
    group = "Config Integrity"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 5

    def run(self, ctx: CheckContext) -> CheckResult:
        missing = [s for s in REQUIRED_SECRETS if not os.environ.get(s)]
        if missing:
            return self._failed(f"missing secrets: {', '.join(missing)}", missing=missing)
        return self._passed(f"all {len(REQUIRED_SECRETS)} required secrets set")


# Rama's hard live-test caps (memory live_test_mode_permanent). Drift here is a
# CRITICAL alert -- changing them needs Rama, so this is alert-only (no auto-fix).
LIVE_TEST_EXPECTED_MAX_OPEN = 4
LIVE_TEST_EXPECTED_MAX_ENTRIES = 6


class ConfigYamlValidCheck(Check):
    name = "config_yaml_valid"
    group = "Config Integrity"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 120

    def run(self, ctx: CheckContext) -> CheckResult:
        import yaml

        bad: list[str] = []
        files = sorted(ctx.config_dir.glob("*.yaml"))
        for f in files:
            try:
                yaml.safe_load(f.read_text(encoding="utf-8"))
            except Exception as exc:
                bad.append(f"{f.name}: {exc}")
        if bad:
            return self._failed("invalid YAML: " + "; ".join(bad[:5]), bad=bad)
        return self._passed(f"all {len(files)} config/*.yaml parse")


class LiveTestModeCapsCheck(Check):
    name = "live_test_mode_caps"
    group = "Config Integrity"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 40

    def run(self, ctx: CheckContext) -> CheckResult:
        import yaml

        cfg = ctx.config_dir / "system_config.yaml"
        if not cfg.exists():
            return self._failed("system_config.yaml missing")
        try:
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            return self._failed(f"system_config.yaml parse error: {exc}")

        risk = data.get("risk") or {}
        if not risk.get("live_test_mode"):
            return self._skipped("live_test_mode off — caps not active")

        mo = risk.get("live_test_max_open_positions")
        me = risk.get("live_test_max_entries_per_day")
        if mo == LIVE_TEST_EXPECTED_MAX_OPEN and me == LIVE_TEST_EXPECTED_MAX_ENTRIES:
            return self._passed(f"live_test caps OK (open={mo}, entries/day={me})",
                                max_open=mo, max_entries=me)
        return self._failed(
            f"live_test caps drift: open={mo} (want {LIVE_TEST_EXPECTED_MAX_OPEN}), "
            f"entries/day={me} (want {LIVE_TEST_EXPECTED_MAX_ENTRIES})",
            max_open=mo, max_entries=me)


class AppConfigValidCheck(Check):
    """Full Pydantic validation of the app config (reuses config_loader.load_all).
    This subsumes the Phase-3a slippage-override validation (slippage_control is part
    of AppConfig with extra='forbid' + fraction-range checks)."""

    name = "app_config_valid"
    group = "Config Integrity"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 300

    def run(self, ctx: CheckContext) -> CheckResult:
        try:
            from core.config_loader import load_all
            load_all(ctx.config_dir)
        except Exception as exc:
            return self._failed(f"config validation failed: {exc}")
        return self._passed("AppConfig loads + validates (incl. slippage overrides)")


class StrategyConfigsValidCheck(Check):
    name = "strategy_configs_valid"
    group = "Config Integrity"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 200

    def run(self, ctx: CheckContext) -> CheckResult:
        try:
            from strategies.loader import StrategyLoader
            strats = StrategyLoader().load_all_strategies(ctx.config_dir / "strategies")
        except Exception as exc:
            return self._failed(f"strategy config invalid: {exc}")
        return self._passed(f"all {len(strats)} strategy configs valid")


class LongStrategiesEnabledCheck(Check):
    """LONG underperforms (memory: restrict pending review) -> WARN while any LONG
    strategy config is present. Visibility-only."""

    name = "long_strategies_enabled"
    group = "Config Integrity"
    criticality = Criticality.WARN
    expected_duration_ms = 200

    def run(self, ctx: CheckContext) -> CheckResult:
        try:
            from strategies.loader import StrategyLoader
            strats = StrategyLoader().load_all_strategies(ctx.config_dir / "strategies")
        except Exception as exc:
            return self._warn(f"could not load strategies: {exc}")
        longs = sorted(n for n, c in strats.items() if getattr(c, "direction", "") == "LONG")
        if longs:
            return self._warn(
                f"{len(longs)} LONG strategies present (LONG underperforms — review): "
                + ", ".join(longs[:6]) + ("…" if len(longs) > 6 else ""),
                long_count=len(longs))
        return self._passed("no LONG strategies loaded")


class CronRegistryValidCheck(Check):
    """Registry integrity (parses + has jobs). The full registry<->crontab drift
    diff is the Cron Officer's job (check_cron_drift, 18:00) -- not duplicated here."""

    name = "cron_registry_valid"
    group = "Config Integrity"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 50

    def run(self, ctx: CheckContext) -> CheckResult:
        try:
            from core.cron_registry import load_cron_registry
            reg = load_cron_registry(ctx.config_dir / "cron_registry.yaml")
            jobs = reg.all_jobs()
        except Exception as exc:
            return self._failed(f"cron_registry.yaml invalid: {exc}")
        if not jobs:
            return self._failed("cron_registry.yaml has no jobs")
        return self._passed(f"cron registry valid ({len(jobs)} jobs) "
                            "[crontab drift -> Cron Officer check_cron_drift]")


CHECKS = [
    ConfigFilesPresentCheck(),
    RequiredSecretsCheck(),
    ConfigYamlValidCheck(),
    LiveTestModeCapsCheck(),
    AppConfigValidCheck(),
    StrategyConfigsValidCheck(),
    LongStrategiesEnabledCheck(),
    CronRegistryValidCheck(),
]
