"""Tests for FIX-135 Item 47: strategy config schema validation at startup."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from utils.startup_checks import check_strategy_configs


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    strats_dir = tmp_path / "strategies"
    strats_dir.mkdir()
    return tmp_path


def _write_valid_yaml(strats_dir: Path, name: str = "test_strat"):
    (strats_dir / f"{name}.yaml").write_text(
        f"""name: {name}
display_name: Test Strategy
description: A test strategy
direction: LONG
intent: INTRADAY
order_protocol: LIMIT_TRIPLE
pipeline: INTRADAY
horizon: SAME_DAY
entry_method: LIMIT
sl_method: FIXED_PCT
sl_pct: 0.01
tgt_method: RISK_REWARD
tgt_risk_reward: 2.0
smart_tgt_enabled: false
pullback_wait_enabled: false
min_score: 0
lot_size: 1
""",
        encoding="utf-8",
    )


def _write_invalid_yaml(strats_dir: Path, name: str = "bad_strat"):
    (strats_dir / f"{name}.yaml").write_text(
        """name: bad_strat
display_name: Bad
description: missing required fields
direction: INVALID_DIRECTION
""",
        encoding="utf-8",
    )


class TestCheckStrategyConfigs:
    def test_valid_configs_pass(self, config_dir):
        _write_valid_yaml(config_dir / "strategies", "good_one")
        errors = check_strategy_configs(config_dir, logging.getLogger("test"))
        assert errors == []

    def test_invalid_config_returns_error(self, config_dir):
        _write_invalid_yaml(config_dir / "strategies", "bad_one")
        errors = check_strategy_configs(config_dir, logging.getLogger("test"))
        assert len(errors) > 0

    def test_missing_strategies_dir_returns_error(self, tmp_path):
        errors = check_strategy_configs(tmp_path / "nonexistent", logging.getLogger("test"))
        assert len(errors) > 0
        assert "not found" in errors[0]

    def test_production_strategies_valid(self):
        errors = check_strategy_configs(Path("config"), logging.getLogger("test"))
        assert errors == [], f"Production strategy validation failed: {errors}"

    def test_empty_dir_returns_error(self, config_dir):
        errors = check_strategy_configs(config_dir, logging.getLogger("test"))
        assert len(errors) > 0

    def test_mixed_valid_invalid_returns_error(self, config_dir):
        _write_valid_yaml(config_dir / "strategies", "good_one")
        _write_invalid_yaml(config_dir / "strategies", "bad_one")
        errors = check_strategy_configs(config_dir, logging.getLogger("test"))
        assert len(errors) > 0
