"""
tests/unit/test_preflight_scanner.py — Module 35 tests

Tests for scripts/preflight_scanner_check.py (PF1-PF8).

Coverage:
  - All reachable -> exit 0
  - One unreachable -> exit 1
  - Verbose flag prints snippets
  - Custom timeout respected
  - Missing config dir -> exit 1
  - Bad config YAML -> exit 1
"""

from __future__ import annotations

import importlib
import sys
import types
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module import helper (scripts/ may not be on sys.path in test runner)
# ---------------------------------------------------------------------------

def _load_module():
    """Import scripts.preflight_scanner_check as a module."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "preflight_scanner_check",
        Path("scripts/preflight_scanner_check.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_scanner_check(name, url="https://chartink.com/s/foo", reachable=True,
                        status_code=200, error=None, response_has_content=True):
    from utils.startup_checks import ScannerCheck
    return ScannerCheck(
        scanner_name=name,
        url=url,
        reachable=reachable,
        status_code=status_code,
        error=error,
        response_has_content=response_has_content,
    )


def _make_preflight_result(checks):
    from utils.startup_checks import ScannerPreflightResult
    all_ok = all(c.reachable for c in checks)
    return ScannerPreflightResult(all_reachable=all_ok, results=checks)


def _make_mock_app_config():
    cfg = MagicMock()
    cfg.scan_webhook_map.scanners = {
        "scanner_a": {"strategy": "s1", "chartink_url": "https://chartink.com/s/a"},
        "scanner_b": {"strategy": "s2", "chartink_url": "https://chartink.com/s/b"},
    }
    cfg.chartink_scanners.scanners = {
        "scanner_a": "https://chartink.com/s/a",
        "scanner_b": "https://chartink.com/s/b",
    }
    return cfg


# ---------------------------------------------------------------------------
# PF2 / PF3: parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_defaults(self):
        args = _mod._parse_args([])
        assert args.config == "config"
        assert args.timeout == 10
        assert args.verbose is False

    def test_custom_config(self):
        args = _mod._parse_args(["--config", "/tmp/cfg"])
        assert args.config == "/tmp/cfg"

    def test_custom_timeout(self):
        args = _mod._parse_args(["--timeout", "30"])
        assert args.timeout == 30

    def test_verbose_flag(self):
        args = _mod._parse_args(["--verbose"])
        assert args.verbose is True


# ---------------------------------------------------------------------------
# PF3 / PF4: all reachable -> exit 0
# ---------------------------------------------------------------------------

class TestAllReachable:
    def test_exit_0_when_all_reachable(self, tmp_path, capsys):
        checks = [
            _make_scanner_check("scanner_a", reachable=True, status_code=200),
            _make_scanner_check("scanner_b", reachable=True, status_code=200),
        ]
        preflight_result = _make_preflight_result(checks)

        with patch.object(_mod, "requests") as mock_requests, \
             patch("core.config_loader.load_all", return_value=_make_mock_app_config()), \
             patch("utils.startup_checks.check_scanner_connectivity",
                   return_value=preflight_result):
            rc = _mod.main(["--config", str(tmp_path)])

        # tmp_path is a real dir so config dir check passes; load_all is mocked
        assert rc == 0

    def test_ok_message_printed(self, tmp_path, capsys):
        checks = [_make_scanner_check("scanner_a", reachable=True)]
        preflight_result = _make_preflight_result(checks)

        with patch("core.config_loader.load_all", return_value=_make_mock_app_config()), \
             patch("utils.startup_checks.check_scanner_connectivity",
                   return_value=preflight_result):
            _mod.main(["--config", str(tmp_path)])

        captured = capsys.readouterr()
        assert "OK" in captured.out


# ---------------------------------------------------------------------------
# PF3: one unreachable -> exit 1
# ---------------------------------------------------------------------------

class TestOneUnreachable:
    def test_exit_1_when_any_unreachable(self, tmp_path):
        checks = [
            _make_scanner_check("scanner_a", reachable=True, status_code=200),
            _make_scanner_check("scanner_b", reachable=False, status_code=None,
                                error="Connection refused"),
        ]
        preflight_result = _make_preflight_result(checks)

        with patch("core.config_loader.load_all", return_value=_make_mock_app_config()), \
             patch("utils.startup_checks.check_scanner_connectivity",
                   return_value=preflight_result):
            rc = _mod.main(["--config", str(tmp_path)])

        assert rc == 1

    def test_failed_scanner_name_in_output(self, tmp_path, capsys):
        checks = [
            _make_scanner_check("scanner_a", reachable=True, status_code=200),
            _make_scanner_check("scanner_b", reachable=False, error="timeout"),
        ]
        preflight_result = _make_preflight_result(checks)

        with patch("core.config_loader.load_all", return_value=_make_mock_app_config()), \
             patch("utils.startup_checks.check_scanner_connectivity",
                   return_value=preflight_result):
            _mod.main(["--config", str(tmp_path)])

        captured = capsys.readouterr()
        assert "scanner_b" in captured.out
        assert "FAILED" in captured.out


# ---------------------------------------------------------------------------
# PF5: verbose mode
# ---------------------------------------------------------------------------

class TestVerboseFlag:
    def test_verbose_prints_snippet_section(self, tmp_path, capsys):
        checks = [_make_scanner_check("scanner_a", reachable=True, status_code=200)]
        preflight_result = _make_preflight_result(checks)

        # Simulate fetcher capturing a snippet
        def _fake_check(swm, cs, fetcher_fn, logger, timeout_sec=10.0):
            # Call fetcher to populate snippets dict
            fetcher_fn("https://chartink.com/s/a", timeout_sec)
            return preflight_result

        with patch("core.config_loader.load_all", return_value=_make_mock_app_config()), \
             patch("utils.startup_checks.check_scanner_connectivity",
                   side_effect=_fake_check), \
             patch.object(_mod.requests, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "<html>Chartink scanner page content</html>"
            mock_get.return_value = mock_resp
            _mod.main(["--config", str(tmp_path), "--verbose"])

        captured = capsys.readouterr()
        # Verbose section prints scanner name in brackets
        assert "[scanner_a]" in captured.out

    def test_verbose_shows_error_when_unreachable(self, tmp_path, capsys):
        checks = [
            _make_scanner_check("scanner_a", reachable=False, error="timeout",
                                response_has_content=False)
        ]
        preflight_result = _make_preflight_result(checks)

        with patch("core.config_loader.load_all", return_value=_make_mock_app_config()), \
             patch("utils.startup_checks.check_scanner_connectivity",
                   return_value=preflight_result):
            _mod.main(["--config", str(tmp_path), "--verbose"])

        captured = capsys.readouterr()
        assert "[scanner_a]" in captured.out
        assert "timeout" in captured.out


# ---------------------------------------------------------------------------
# PF4: custom timeout forwarded
# ---------------------------------------------------------------------------

class TestTimeoutRespected:
    def test_custom_timeout_passed_to_check(self, tmp_path):
        checks = [_make_scanner_check("scanner_a", reachable=True)]
        preflight_result = _make_preflight_result(checks)
        captured_kwargs = {}

        def _spy_check(swm, cs, fetcher_fn, logger, timeout_sec=10.0):
            captured_kwargs["timeout_sec"] = timeout_sec
            return preflight_result

        with patch("core.config_loader.load_all", return_value=_make_mock_app_config()), \
             patch("utils.startup_checks.check_scanner_connectivity",
                   side_effect=_spy_check):
            _mod.main(["--config", str(tmp_path), "--timeout", "25"])

        assert captured_kwargs["timeout_sec"] == 25.0

    def test_default_timeout_is_10(self, tmp_path):
        checks = [_make_scanner_check("scanner_a", reachable=True)]
        preflight_result = _make_preflight_result(checks)
        captured_kwargs = {}

        def _spy_check(swm, cs, fetcher_fn, logger, timeout_sec=10.0):
            captured_kwargs["timeout_sec"] = timeout_sec
            return preflight_result

        with patch("core.config_loader.load_all", return_value=_make_mock_app_config()), \
             patch("utils.startup_checks.check_scanner_connectivity",
                   side_effect=_spy_check):
            _mod.main(["--config", str(tmp_path)])

        assert captured_kwargs["timeout_sec"] == 10.0


# ---------------------------------------------------------------------------
# PF3: missing / bad config -> exit 1
# ---------------------------------------------------------------------------

class TestMissingConfig:
    def test_nonexistent_config_dir_exits_1(self, capsys):
        rc = _mod.main(["--config", "/does/not/exist/xyz123"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.err

    def test_load_all_raises_exits_1(self, tmp_path, capsys):
        with patch("core.config_loader.load_all",
                   side_effect=ValueError("bad YAML")):
            rc = _mod.main(["--config", str(tmp_path)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "ERROR" in captured.err


# ---------------------------------------------------------------------------
# PF6: http fetcher captures snippets for verbose
# ---------------------------------------------------------------------------

class TestHttpFetcher:
    def test_fetcher_returns_status_and_snippet(self):
        snippets = {}
        fetcher = _mod._make_capturing_fetcher(snippets)

        with patch.object(_mod.requests, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "Hello " * 100   # long body
            mock_get.return_value = mock_resp

            status, body = fetcher("https://example.com", 10.0)

        assert status == 200
        assert len(body) <= _mod._SNIPPET_LEN
        assert snippets.get("https://example.com") is not None

    def test_fetcher_returns_none_on_network_error(self):
        snippets = {}
        fetcher = _mod._make_capturing_fetcher(snippets)

        with patch.object(_mod.requests, "get",
                          side_effect=_mod.requests.RequestException("conn refused")):
            status, body = fetcher("https://example.com", 5.0)

        assert status is None

    def test_timeout_override_applied(self):
        snippets = {}
        fetcher = _mod._make_capturing_fetcher(snippets, timeout_override=7)
        called_with = {}

        with patch.object(_mod.requests, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "ok"
            mock_get.return_value = mock_resp

            fetcher("https://example.com", 10.0)   # should use 7, not 10
            called_with["timeout"] = mock_get.call_args[1]["timeout"]

        assert called_with["timeout"] == 7
