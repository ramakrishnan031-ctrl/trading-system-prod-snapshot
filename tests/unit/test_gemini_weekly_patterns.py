"""
tests/unit/test_gemini_weekly_patterns.py -- FIX-151

Tests for scripts/gemini_weekly_patterns.py.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

def _load_module():
    spec = importlib.util.spec_from_file_location(
        "gemini_weekly_patterns",
        Path("scripts/gemini_weekly_patterns.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_mod = _load_module()


@pytest.fixture
def log():
    return MagicMock()


class TestTradingDays:
    def test_5_trading_days(self):
        days = _mod._trading_days(date(2026, 6, 6), 5)
        assert len(days) == 5
        for d in days:
            assert d.weekday() < 5

    def test_skips_weekends(self):
        days = _mod._trading_days(date(2026, 6, 7), 5)
        assert len(days) == 5
        assert date(2026, 6, 6) not in days or date(2026, 6, 7) not in days

    def test_single_day(self):
        days = _mod._trading_days(date(2026, 6, 3), 1)
        assert len(days) == 1
        assert days[0] == date(2026, 6, 3)


class TestLoadFile:
    def test_existing_file(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("content here", encoding="utf-8")
        assert "content here" in _mod._load_file(f)

    def test_missing_file(self, tmp_path):
        assert _mod._load_file(tmp_path / "nope.md") == ""

    def test_truncation(self, tmp_path):
        f = tmp_path / "big.md"
        f.write_text("x" * 5000, encoding="utf-8")
        result = _mod._load_file(f, max_chars=100)
        assert len(result) < 200
        assert "truncated" in result


class TestGatherData:
    def test_empty_dirs(self, log, tmp_path):
        with patch.object(_mod, "WATCHMAN_DIR", tmp_path / "w"), \
             patch.object(_mod, "EOD_REVIEW_DIR", tmp_path / "e"), \
             patch.object(_mod, "DAILY_REPORT_DIR", tmp_path / "d"):
            data = _mod._gather_data([date(2026, 6, 3)], log)
        assert "2026-06-03" in data

    def test_with_watchman_data(self, log, tmp_path):
        w_dir = tmp_path / "watchman"
        w_dir.mkdir()
        (w_dir / "watchman_2026-06-03.md").write_text("All good.", encoding="utf-8")

        with patch.object(_mod, "WATCHMAN_DIR", w_dir), \
             patch.object(_mod, "EOD_REVIEW_DIR", tmp_path / "e"), \
             patch.object(_mod, "DAILY_REPORT_DIR", tmp_path / "d"):
            data = _mod._gather_data([date(2026, 6, 3)], log)
        assert "All good." in data


class TestGeminiCLI:
    @patch("subprocess.run")
    def test_success(self, mock_run, log):
        mock_run.return_value = MagicMock(returncode=0, stdout="## Recurring Issues\nNone.", stderr="")
        result = _mod._call_gemini_cli("prompt", "data", log)
        assert "Recurring" in result

    @patch("subprocess.run")
    def test_failure(self, mock_run, log):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        assert _mod._call_gemini_cli("prompt", "data", log) is None


class TestRunAnalysis:
    @patch.object(_mod, "_send_telegram")
    @patch.object(_mod, "_call_gemini_cli", return_value="## Recurring Issues\nNone found.")
    def test_happy_path(self, mock_gemini, mock_tg, log, tmp_path):
        w_dir = tmp_path / "watchman"
        w_dir.mkdir()
        (w_dir / "watchman_2026-06-03.md").write_text("OK", encoding="utf-8")

        with patch.object(_mod, "WATCHMAN_DIR", w_dir), \
             patch.object(_mod, "EOD_REVIEW_DIR", tmp_path / "e"), \
             patch.object(_mod, "DAILY_REPORT_DIR", tmp_path / "d"), \
             patch.object(_mod, "OUTPUT_DIR", tmp_path / "out"):
            result = _mod.run_analysis("2026-06-03", 1, log)
        assert result == 0
        assert (tmp_path / "out" / "patterns_2026-06-03.md").exists()

    def test_no_data_still_produces_headers(self, log, tmp_path):
        with patch.object(_mod, "WATCHMAN_DIR", tmp_path / "w"), \
             patch.object(_mod, "EOD_REVIEW_DIR", tmp_path / "e"), \
             patch.object(_mod, "DAILY_REPORT_DIR", tmp_path / "d"), \
             patch.object(_mod, "_call_gemini_cli", return_value="No patterns."), \
             patch.object(_mod, "_send_telegram"), \
             patch.object(_mod, "OUTPUT_DIR", tmp_path / "out"):
            result = _mod.run_analysis("2026-06-03", 1, log)
        assert result == 0

    def test_dry_run(self, log, tmp_path):
        w_dir = tmp_path / "watchman"
        w_dir.mkdir()
        (w_dir / "watchman_2026-06-03.md").write_text("OK", encoding="utf-8")

        with patch.object(_mod, "WATCHMAN_DIR", w_dir), \
             patch.object(_mod, "EOD_REVIEW_DIR", tmp_path / "e"), \
             patch.object(_mod, "DAILY_REPORT_DIR", tmp_path / "d"):
            result = _mod.run_analysis("2026-06-03", 1, log, dry_run=True)
        assert result == 0


class TestMain:
    @patch.object(_mod, "run_analysis", return_value=0)
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_success(self, mock_log, mock_run):
        result = _mod.main(["--date", "2026-06-03", "--dry-run"])
        assert result == 0

    @patch.object(_mod, "run_analysis", side_effect=RuntimeError("boom"))
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_error(self, mock_log, mock_run):
        result = _mod.main(["--date", "2026-06-03"])
        assert result == 1


class TestArgParsing:
    def test_defaults(self):
        args = _mod._parse_args([])
        assert args.date is None
        assert args.days == 5
        assert not args.dry_run

    def test_custom(self):
        args = _mod._parse_args(["--date", "2026-06-01", "--days", "3"])
        assert args.date == "2026-06-01"
        assert args.days == 3
