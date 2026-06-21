"""
tests/unit/test_preflight_config_rest.py -- Group 6 config-rest checks
(app_config_valid, strategy_configs_valid, long_strategies_enabled,
cron_registry_valid). PASS cases run against the REAL shipped config (these checks
exist to guard that config); FAIL cases use an empty temp dir.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from scripts.preflight.base import CheckContext, Status
from scripts.preflight.checks import config_integrity

REAL = Path("config")


def _ctx(cfg: Path) -> CheckContext:
    return CheckContext(config_dir=cfg, db_path=Path("data_store/trading_system.db"),
                        as_of_date=date(2026, 6, 22))


def test_app_config_valid_on_real_config():
    assert config_integrity.AppConfigValidCheck().run(_ctx(REAL)).status is Status.PASS


def test_app_config_valid_fails_on_empty(tmp_path):
    assert config_integrity.AppConfigValidCheck().run(_ctx(tmp_path)).status is Status.FAIL


def test_strategy_configs_valid_on_real():
    assert config_integrity.StrategyConfigsValidCheck().run(_ctx(REAL)).status is Status.PASS


def test_strategy_configs_valid_fails_on_empty(tmp_path):
    assert config_integrity.StrategyConfigsValidCheck().run(_ctx(tmp_path)).status is Status.FAIL


def test_long_strategies_warn_on_real():
    # the repo ships LONG strategy configs -> WARN (visibility, non-blocking)
    res = config_integrity.LongStrategiesEnabledCheck().run(_ctx(REAL))
    assert res.status is Status.WARN and res.metrics.get("long_count", 0) > 0


def test_cron_registry_valid_on_real():
    res = config_integrity.CronRegistryValidCheck().run(_ctx(REAL))
    assert res.status is Status.PASS


def test_cron_registry_valid_fails_on_empty(tmp_path):
    assert config_integrity.CronRegistryValidCheck().run(_ctx(tmp_path)).status is Status.FAIL
