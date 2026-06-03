"""
tests/unit/test_revert_temp_config.py -- FIX-151

Tests for scripts/revert_temp_config.py and startup check #14.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

def _load_module():
    spec = importlib.util.spec_from_file_location(
        "revert_temp_config",
        Path("scripts/revert_temp_config.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_mod = _load_module()


@pytest.fixture
def log():
    return MagicMock()


@pytest.fixture
def config_dir_with_temp(tmp_path):
    cfg = tmp_path / "system_config.yaml"
    cfg.write_text(
        "capital:\n"
        "  daily_loss_limit: 100000.0  # TEMP: raised for paper\n"
        "risk:\n"
        "  max_consecutive_losses: 20  # TEMP: raised for paper\n"
        "  daily_loss_limit_pct: 1.00  # TEMP: 100% for paper\n"
        "order_reconciler:\n"
        "  capital_drift_tolerance: 100000.0  # TEMP: raised for paper\n"
        "drift_handler:\n"
        "  # TEMP: thresholds raised\n"
        "  log_only_threshold_rs: 100000.0\n"
        "  soft_kill_threshold_rs: 200000.0\n"
        "  hard_kill_threshold_rs: 500000.0\n",
        encoding="utf-8",
    )
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()
    (strat_dir / "gap_fade_long.yaml").write_text(
        "name: gap_fade_long\n"
        "min_score: 30  # TEMP: lowered for paper\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def config_dir_clean(tmp_path):
    cfg = tmp_path / "system_config.yaml"
    cfg.write_text(
        "capital:\n"
        "  daily_loss_limit: 1250.0\n"
        "risk:\n"
        "  max_consecutive_losses: 4\n"
        "  daily_loss_limit_pct: 0.05\n",
        encoding="utf-8",
    )
    strat_dir = tmp_path / "strategies"
    strat_dir.mkdir()
    (strat_dir / "gap_fade_long.yaml").write_text(
        "name: gap_fade_long\n"
        "min_score: 60\n",
        encoding="utf-8",
    )
    return tmp_path


class TestScanTempMarkers:
    def test_finds_temp_values(self, config_dir_with_temp, log):
        results = _mod.scan_temp_markers(config_dir_with_temp, log)
        active = [r for r in results if r["status"] == "ACTIVE_TEMP"]
        assert len(active) >= 5

    def test_clean_config(self, config_dir_clean, log):
        results = _mod.scan_temp_markers(config_dir_clean, log)
        active = [r for r in results if r["status"] == "ACTIVE_TEMP"]
        assert len(active) == 0

    def test_missing_file(self, tmp_path, log):
        results = _mod.scan_temp_markers(tmp_path, log)
        for r in results:
            assert r["status"] == "ALREADY_REVERTED"


class TestApplyReverts:
    def test_applies_reverts(self, config_dir_with_temp, log):
        results = _mod.scan_temp_markers(config_dir_with_temp, log)
        applied = _mod.apply_reverts(config_dir_with_temp, results, log)
        assert applied > 0

        content = (config_dir_with_temp / "system_config.yaml").read_text()
        assert "REVERTED" in content


class TestMain:
    def test_dry_run_with_temp(self, config_dir_with_temp, log, capsys):
        result = _mod.main(["--config-dir", str(config_dir_with_temp)])
        assert result == 2
        out = capsys.readouterr().out
        assert "ACTIVE TEMP" in out

    def test_dry_run_clean(self, config_dir_clean, log, capsys):
        result = _mod.main(["--config-dir", str(config_dir_clean)])
        assert result == 0
        out = capsys.readouterr().out
        assert "production-ready" in out

    def test_apply_without_confirm(self, config_dir_with_temp):
        result = _mod.main(["--config-dir", str(config_dir_with_temp), "--apply"])
        assert result == 3


class TestArgParsing:
    def test_defaults(self):
        args = _mod._parse_args([])
        assert not args.apply
        assert not args.confirm

    def test_apply_confirm(self):
        args = _mod._parse_args(["--apply", "--confirm"])
        assert args.apply
        assert args.confirm


# ─────────────────────────────────────────────────────────────────────────────
# Startup check #14
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckTempConfigValues:
    def test_detects_temp(self, config_dir_with_temp, log):
        from utils.startup_checks import check_temp_config_values
        result = check_temp_config_values(config_dir_with_temp, log)
        assert not result.passed
        assert result.temp_count > 0
        assert len(result.files_with_temp) > 0

    def test_clean_config(self, config_dir_clean, log):
        from utils.startup_checks import check_temp_config_values
        result = check_temp_config_values(config_dir_clean, log)
        assert result.passed
        assert result.temp_count == 0

    def test_empty_dir(self, tmp_path, log):
        from utils.startup_checks import check_temp_config_values
        result = check_temp_config_values(tmp_path, log)
        assert result.passed
        assert result.temp_count == 0
