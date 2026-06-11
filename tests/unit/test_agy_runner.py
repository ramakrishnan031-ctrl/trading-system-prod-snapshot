"""Tests for scripts/agy_runner.py — model cascade, path resolution, quota detection."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from scripts.agy_runner import (
    FORBIDDEN_MODELS,
    QUOTA_ERROR_PATTERNS,
    TIER1_CASCADE,
    TIER2_CASCADE,
    get_agy_bin,
    is_quota_error,
    run_agy,
    run_trade_coach,
    run_watchman,
    run_with_cascade,
)


class TestGetAgyBin:
    def test_from_env_var(self, tmp_path):
        fake_bin = tmp_path / "agy"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        with patch.dict("os.environ", {"GEMINI_BIN": str(fake_bin)}):
            assert get_agy_bin() == str(fake_bin)

    def test_fails_loudly_when_not_found(self):
        with patch.dict("os.environ", {"GEMINI_BIN": ""}, clear=False):
            with patch("scripts.agy_runner.shutil.which", return_value=None):
                with patch("scripts.agy_runner.os.path.isfile", return_value=False):
                    with pytest.raises(RuntimeError, match="agy binary not found"):
                        get_agy_bin()


class TestIsQuotaError:
    @pytest.mark.parametrize("pattern", QUOTA_ERROR_PATTERNS)
    def test_detects_each_pattern(self, pattern):
        assert is_quota_error(f"Error: {pattern} for this model") is True

    def test_normal_output_returns_false(self):
        assert is_quota_error("The trading system looks healthy today.") is False

    def test_empty_returns_false(self):
        assert is_quota_error("") is False

    def test_none_returns_false(self):
        assert is_quota_error(None) is False


class TestRunAgy:
    @patch("scripts.agy_runner.get_agy_bin", return_value="/usr/bin/agy")
    @patch("scripts.agy_runner.subprocess.run")
    def test_returns_none_on_quota_error(self, mock_run, mock_bin):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Error: quota exceeded for this model",
            stderr="",
        )
        result = run_agy("test prompt", "SomeModel")
        assert result is None

    @patch("scripts.agy_runner.get_agy_bin", return_value="/usr/bin/agy")
    @patch("scripts.agy_runner.subprocess.run")
    def test_returns_output_on_success(self, mock_run, mock_bin):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="All clear\n",
            stderr="",
        )
        result = run_agy("test prompt", "SomeModel")
        assert result == "All clear"

    @patch("scripts.agy_runner.get_agy_bin", return_value="/usr/bin/agy")
    @patch("scripts.agy_runner.subprocess.run")
    def test_passes_input_data_as_stdin(self, mock_run, mock_bin):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="response\n", stderr=""
        )
        run_agy("prompt", "Model", input_data="log data here")
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs.get("input") == "log data here" or \
               call_kwargs[1].get("input") == "log data here"

    @patch("scripts.agy_runner.get_agy_bin", return_value="/usr/bin/agy")
    @patch("scripts.agy_runner.subprocess.run")
    def test_skip_permissions_flag_present_by_default(self, mock_run, mock_bin):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok\n", stderr=""
        )
        run_agy("prompt", "Model")
        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" in cmd
        assert cmd[1] == "--dangerously-skip-permissions"  # must be at index 1, not splitting --model

    @patch("scripts.agy_runner.get_agy_bin", return_value="/usr/bin/agy")
    @patch("scripts.agy_runner.subprocess.run")
    def test_skip_permissions_flag_absent_when_disabled(self, mock_run, mock_bin):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok\n", stderr=""
        )
        run_agy("prompt", "Model", skip_permissions=False)
        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd


class TestRunWithCascade:
    @patch("scripts.agy_runner.run_agy")
    def test_cascade_falls_through_on_quota(self, mock_run_agy):
        mock_run_agy.side_effect = [None, None, "output from third"]
        result = run_with_cascade("prompt", tier=1, task_name="test")
        assert result == "output from third"
        assert mock_run_agy.call_count == 3

    @patch("scripts.agy_runner.run_agy")
    def test_cascade_returns_none_when_all_exhausted(self, mock_run_agy):
        mock_run_agy.return_value = None
        result = run_with_cascade("prompt", tier=2, task_name="test")
        assert result is None

    @patch("scripts.agy_runner.run_agy")
    def test_forbidden_models_never_called(self, mock_run_agy):
        mock_run_agy.return_value = None
        run_with_cascade("prompt", tier=1, task_name="test")
        run_with_cascade("prompt", tier=2, task_name="test")
        called_models = [call.args[1] for call in mock_run_agy.call_args_list]
        for forbidden in FORBIDDEN_MODELS:
            assert forbidden not in called_models

    @patch("scripts.agy_runner.run_agy")
    def test_tier2_never_calls_claude(self, mock_run_agy):
        mock_run_agy.return_value = None
        run_with_cascade("prompt", tier=2, task_name="test")
        called_models = [call.args[1] for call in mock_run_agy.call_args_list]
        for model in called_models:
            assert "Claude" not in model


class TestConvenienceFunctions:
    @patch("scripts.agy_runner.run_with_cascade")
    def test_watchman_uses_tier2(self, mock_cascade):
        mock_cascade.return_value = "ok"
        run_watchman("prompt")
        assert mock_cascade.call_args.kwargs["tier"] == 2 or \
               mock_cascade.call_args[1].get("tier") == 2 or \
               mock_cascade.call_args[0][1] == 2

    @patch("scripts.agy_runner.run_with_cascade")
    def test_trade_coach_uses_tier1(self, mock_cascade):
        mock_cascade.return_value = "ok"
        run_trade_coach("prompt")
        call_kwargs = mock_cascade.call_args
        tier = call_kwargs.kwargs.get("tier") or call_kwargs[1].get("tier")
        assert tier == 1
