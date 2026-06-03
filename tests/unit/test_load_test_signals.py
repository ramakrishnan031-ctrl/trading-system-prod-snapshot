"""
tests/unit/test_load_test_signals.py -- FIX-151

Tests for scripts/load_test_signals.py.

Coverage:
  - Payload generation
  - Scenario config
  - Assertion checks (burst, sustained, overflow)
  - Report generation
  - Main dry-run
  - CLI arg parsing
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

def _load_module():
    spec = importlib.util.spec_from_file_location(
        "load_test_signals",
        Path("scripts/load_test_signals.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_mod = _load_module()


class TestPayload:
    def test_make_payload_has_required_fields(self):
        p = _mod._make_payload("RELIANCE", "gap_fade_long", 150.0)
        assert p["stocks"] == "RELIANCE"
        assert p["trigger_prices"] == "150.0"
        assert "triggered_at" in p
        assert p["scan_name"] == "gap_fade_long"

    def test_make_payload_different_symbols(self):
        p1 = _mod._make_payload("TCS", "vwap_bounce_long")
        p2 = _mod._make_payload("INFY", "gap_go_short")
        assert p1["stocks"] != p2["stocks"]


class TestScenarioConfig:
    def test_burst_config(self):
        cfg = _mod._scenario_config("burst")
        assert cfg["total_signals"] == 100
        assert cfg["duration_sec"] == 10

    def test_sustained_config(self):
        cfg = _mod._scenario_config("sustained")
        assert cfg["total_signals"] == 250
        assert cfg["duration_sec"] == 300

    def test_overflow_config(self):
        cfg = _mod._scenario_config("overflow")
        assert cfg["total_signals"] == 200
        assert cfg["duration_sec"] == 5

    def test_unknown_scenario_raises(self):
        with pytest.raises(ValueError):
            _mod._scenario_config("unknown")


class TestAssertions:
    def test_burst_pass(self):
        summary = {"status_counts": {200: 80}, "latency_p95_ms": 500}
        assert _mod._check_assertions(summary, "burst") == []

    def test_burst_fail_low_success(self):
        summary = {"status_counts": {200: 10, 503: 90}, "latency_p95_ms": 500}
        failures = _mod._check_assertions(summary, "burst")
        assert len(failures) == 1
        assert "200" in failures[0]

    def test_burst_fail_high_latency(self):
        summary = {"status_counts": {200: 80}, "latency_p95_ms": 10000}
        failures = _mod._check_assertions(summary, "burst")
        assert any("latency" in f.lower() for f in failures)

    def test_sustained_pass(self):
        summary = {"status_counts": {200: 220}, "total_signals": 250}
        assert _mod._check_assertions(summary, "sustained") == []

    def test_sustained_fail(self):
        summary = {"status_counts": {200: 50, 503: 200}, "total_signals": 250}
        failures = _mod._check_assertions(summary, "sustained")
        assert len(failures) >= 1

    def test_overflow_pass(self):
        summary = {"status_counts": {200: 100, 503: 100}}
        assert _mod._check_assertions(summary, "overflow") == []

    def test_overflow_fail_no_503(self):
        summary = {"status_counts": {200: 200}}
        failures = _mod._check_assertions(summary, "overflow")
        assert len(failures) == 1
        assert "503" in failures[0]


class TestReport:
    def test_write_report(self, tmp_path):
        summary = {
            "total_signals": 100,
            "wall_time_sec": 10.5,
            "throughput_per_sec": 9.5,
            "status_counts": {200: 80, 503: 20},
            "latency_p50_ms": 50.0,
            "latency_p95_ms": 200.0,
            "latency_p99_ms": 500.0,
            "latency_min_ms": 10.0,
            "latency_max_ms": 800.0,
            "latency_avg_ms": 100.0,
            "queue_warnings": 5,
        }
        with patch.object(_mod, "REPORT_DIR", tmp_path):
            with patch.object(_mod, "today_ist", return_value="2026-06-03"):
                path = _mod._write_report(summary, [], "burst")
        assert path.exists()
        content = path.read_text()
        assert "PASS" in content
        assert "burst" in content

    def test_write_report_with_failures(self, tmp_path):
        summary = {
            "total_signals": 100,
            "status_counts": {200: 10},
            "latency_p50_ms": 50.0,
            "latency_p95_ms": 200.0,
            "latency_p99_ms": 500.0,
            "latency_min_ms": 10.0,
            "latency_max_ms": 800.0,
            "latency_avg_ms": 100.0,
            "queue_warnings": 0,
        }
        with patch.object(_mod, "REPORT_DIR", tmp_path):
            with patch.object(_mod, "today_ist", return_value="2026-06-03"):
                path = _mod._write_report(summary, ["test failed"], "burst")
        content = path.read_text()
        assert "FAIL" in content


class TestMain:
    @patch.object(_mod, "_run_scenario", return_value={"dry_run": True})
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_dry_run(self, mock_log, mock_run):
        result = _mod.main(["--scenario", "burst", "--dry-run"])
        assert result == 0

    @patch.object(_mod, "_run_scenario", side_effect=RuntimeError("boom"))
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_error_returns_1(self, mock_log, mock_run):
        result = _mod.main(["--scenario", "burst"])
        assert result == 1


class TestArgParsing:
    def test_burst(self):
        args = _mod._parse_args(["--scenario", "burst"])
        assert args.scenario == "burst"

    def test_sustained_with_target(self):
        args = _mod._parse_args(["--scenario", "sustained", "--target", "http://x:5000"])
        assert args.target == "http://x:5000"

    def test_dry_run(self):
        args = _mod._parse_args(["--scenario", "overflow", "--dry-run"])
        assert args.dry_run
