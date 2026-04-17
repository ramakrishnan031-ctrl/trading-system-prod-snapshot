"""
tests/unit/test_startup_checks.py

Validates utils/startup_checks.py end-to-end:
  - detect_startup_scenario: all 5 detection paths (SC4)
  - check_clock_skew: pass, fail, broker error (SC5)
  - check_config_hash: first run, changed, unchanged (SC6)
  - check_scanner_connectivity: reachable, 404, timeout, empty (SC7)
  - check_webhook_endpoint: reachable, refused, body (SC8)
  - check_config_files_present: all present, missing files (SC9)
  - check_market_holiday_today: weekend, configured holiday, weekday (SC10)
  - check_required_secrets: all set, missing keys (SC11)
  - run_all_startup_checks: aggregate passing/blocking/warning cases (SC12)

Run: python -m pytest tests/unit/test_startup_checks.py -v
Or:  python tests/unit/test_startup_checks.py  (standalone mode)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Tuple
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.exceptions import ClockSkewTooLarge
from core.state_store import StateStore
from utils.startup_checks import (
    StartupScenario,
    StartupScenarioResult,
    ClockCheckResult,
    ConfigHashResult,
    ScannerCheck,
    ScannerPreflightResult,
    WebhookEndpointResult,
    StartupReport,
    detect_startup_scenario,
    check_clock_skew,
    check_config_hash,
    check_scanner_connectivity,
    check_webhook_endpoint,
    check_config_files_present,
    check_market_holiday_today,
    check_required_secrets,
    run_all_startup_checks,
)

_IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
# Mock / helper utilities
# ─────────────────────────────────────────────────────────────────────────────

class _CapturingLogger:
    """Minimal logger that collects messages for assertion."""

    def __init__(self) -> None:
        self.infos:    list = []
        self.warnings: list = []
        self.errors:   list = []

    def info(self, msg: str, *args: object) -> None:
        self.infos.append(msg % args if args else msg)

    def warning(self, msg: str, *args: object) -> None:
        self.warnings.append(msg % args if args else msg)

    def error(self, msg: str, *args: object) -> None:
        self.errors.append(msg % args if args else msg)

    def debug(self, msg: str, *args: object) -> None:
        pass

    def has_info(self, substr: str) -> bool:
        return any(substr in m for m in self.infos)

    def has_warning(self, substr: str) -> bool:
        return any(substr in m for m in self.warnings)

    def has_error(self, substr: str) -> bool:
        return any(substr in m for m in self.errors)


def _make_store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "test.db")


def _make_kill_switch(state: str = "INACTIVE", reason: str = "",
                      triggered_at: Optional[str] = None) -> MagicMock:
    """Build a mock KillSwitch with current_state() and status()."""
    ks = MagicMock()
    ks.status.return_value = {
        "state":        state,
        "reason":       reason,
        "triggered_at": triggered_at,
        "triggered_by": "test",
    }
    # current_state() is used internally in kill_switch module but not in
    # startup_checks (we call status() directly)
    return ks


class _MockTimeAuthority:
    """Real-class mock for time_authority module (avoids assert_ attribute conflict)."""

    def __init__(
        self,
        now: Optional[datetime] = None,
        skew_sec: float = 0.0,
        raise_on_assert: bool = False,
    ) -> None:
        self._now = now or datetime.now(_IST)
        self._skew = skew_sec
        self._raise = raise_on_assert

    def now_ist(self) -> datetime:
        return self._now

    def assert_clock_at_startup(self, broker_ts: datetime) -> float:
        if self._raise:
            raise ClockSkewTooLarge(
                "skew too large",
                skew_seconds=35.0,
                threshold_sec=30.0,
            )
        return self._skew


def _make_time_authority(
    now: Optional[datetime] = None,
    skew_sec: float = 0.0,
    raise_on_assert: bool = False,
) -> _MockTimeAuthority:
    return _MockTimeAuthority(now=now, skew_sec=skew_sec, raise_on_assert=raise_on_assert)


def _make_broker_adapter(server_time: Optional[datetime] = None,
                          raise_error: bool = False) -> MagicMock:
    adapter = MagicMock()
    if raise_error:
        adapter.get_server_time.side_effect = ConnectionError("broker unreachable")
    else:
        adapter.get_server_time.return_value = (
            server_time or datetime.now(_IST)
        )
    return adapter


def _seed_session(store: StateStore, session_date: str,
                  kill_state: str = "ACTIVE") -> None:
    """Insert a minimal session row into the store."""
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT OR REPLACE INTO session
              (id, session_date, account_id, broker, mode,
               trade_type, kill_state, session_start, last_updated)
            VALUES (1, ?, 'ACC1', 'zerodha', 'PAPER',
                    'INTRADAY', ?, ?, ?)
            """,
            (session_date, kill_state,
             session_date + "T09:00:00+05:30",
             session_date + "T09:00:00+05:30"),
        )


def _seed_shutdown(store: StateStore, ts: str) -> None:
    store.insert_system_event(
        event_type="SHUTDOWN",
        timestamp=ts,
    )


def _make_market_windows(holidays: list = None) -> MagicMock:
    mw = MagicMock()
    mw.holidays = holidays or []
    return mw


def _http_ok(url: str, timeout: float) -> Tuple[int, str]:
    return (200, "<html>content</html>")


def _http_404(url: str, timeout: float) -> Tuple[int, str]:
    return (404, "")


def _http_timeout(url: str, timeout: float) -> Tuple[None, str]:
    raise ConnectionError("timed out")


def _make_scan_webhook_map(names=("scanner_a", "scanner_b")) -> MagicMock:
    """Build a mock ScanWebhookMapConfig."""
    mwm = MagicMock()
    entries = {}
    for name in names:
        e = MagicMock()
        e.chartink_url = f"https://chartink.com/screener/{name}"
        entries[name] = e
    mwm.scanners = entries
    return mwm


def _make_app_config(file_hashes: dict = None) -> MagicMock:
    ac = MagicMock()
    ac.file_hashes = file_hashes or {}
    return ac


# ─────────────────────────────────────────────────────────────────────────────
# detect_startup_scenario tests (SC4)
# ─────────────────────────────────────────────────────────────────────────────

def test_detect_cold_no_session_row(tmp_path: Path) -> None:
    """No session row at all -> COLD (SC4 step 1)."""
    store = _make_store(tmp_path)
    ks = _make_kill_switch()
    log = _CapturingLogger()
    today = date(2026, 4, 16)

    result = detect_startup_scenario(store, ks, today, log)

    assert result.scenario == StartupScenario.COLD
    assert result.session_date_previous is None
    assert result.shutdown_marker_found is False
    print("  OK detect_cold: no session row")
    store.close()


def test_detect_cold_previous_day(tmp_path: Path) -> None:
    """session.session_date < today -> COLD (SC4 step 4)."""
    store = _make_store(tmp_path)
    _seed_session(store, "2026-04-15")
    ks = _make_kill_switch()
    log = _CapturingLogger()
    today = date(2026, 4, 16)

    result = detect_startup_scenario(store, ks, today, log)

    assert result.scenario == StartupScenario.COLD
    assert result.session_date_previous == date(2026, 4, 15)
    print("  OK detect_cold: previous day session")
    store.close()


def test_detect_halt_hard_kill(tmp_path: Path) -> None:
    """kill_switch HARD_KILL -> HALT (SC4 step 2)."""
    store = _make_store(tmp_path)
    _seed_session(store, "2026-04-16")
    ks = _make_kill_switch(
        state="HARD_KILL",
        reason="manual_halt",
        triggered_at="2026-04-16T11:00:00+05:30",
    )
    log = _CapturingLogger()
    today = date(2026, 4, 16)

    result = detect_startup_scenario(store, ks, today, log)

    assert result.scenario == StartupScenario.HALT
    assert result.kill_state == "HARD_KILL"
    assert result.kill_reason == "manual_halt"
    print("  OK detect_halt: hard_kill active")
    store.close()


def test_detect_halt_soft_kill_same_day(tmp_path: Path) -> None:
    """SOFT_KILL triggered today, condition not cleared -> HALT (SC4 step 3)."""
    store = _make_store(tmp_path)
    _seed_session(store, "2026-04-16")
    ks = _make_kill_switch(
        state="SOFT_KILL",
        reason="daily_loss_limit",
        triggered_at="2026-04-16T13:30:00+05:30",
    )
    log = _CapturingLogger()
    today = date(2026, 4, 16)

    result = detect_startup_scenario(store, ks, today, log)

    assert result.scenario == StartupScenario.HALT
    assert result.kill_state == "SOFT_KILL"
    print("  OK detect_halt: soft_kill same-day")
    store.close()


def test_detect_warm_soft_kill_previous_day_with_shutdown(tmp_path: Path) -> None:
    """SOFT_KILL from yesterday (condition cleared) + SHUTDOWN found -> WARM."""
    store = _make_store(tmp_path)
    _seed_session(store, "2026-04-16")
    _seed_shutdown(store, "2026-04-16T15:30:00+05:30")
    ks = _make_kill_switch(
        state="SOFT_KILL",
        reason="daily_loss_limit",
        triggered_at="2026-04-15T14:00:00+05:30",  # yesterday
    )
    log = _CapturingLogger()
    today = date(2026, 4, 16)

    result = detect_startup_scenario(store, ks, today, log)

    assert result.scenario == StartupScenario.WARM
    assert result.shutdown_marker_found is True
    print("  OK detect_warm: soft_kill previous day, shutdown found")
    store.close()


def test_detect_warm_shutdown_found(tmp_path: Path) -> None:
    """Same day, SHUTDOWN event present -> WARM (SC4 step 5)."""
    store = _make_store(tmp_path)
    _seed_session(store, "2026-04-16")
    _seed_shutdown(store, "2026-04-16T15:30:00+05:30")
    ks = _make_kill_switch()
    log = _CapturingLogger()
    today = date(2026, 4, 16)

    result = detect_startup_scenario(store, ks, today, log)

    assert result.scenario == StartupScenario.WARM
    assert result.shutdown_marker_found is True
    assert result.last_shutdown_ts is not None
    print("  OK detect_warm: shutdown event found")
    store.close()


def test_detect_crash_no_shutdown(tmp_path: Path) -> None:
    """Same day, no SHUTDOWN event -> CRASH (SC4 step 5)."""
    store = _make_store(tmp_path)
    _seed_session(store, "2026-04-16")
    ks = _make_kill_switch()
    log = _CapturingLogger()
    today = date(2026, 4, 16)

    result = detect_startup_scenario(store, ks, today, log)

    assert result.scenario == StartupScenario.CRASH
    assert result.shutdown_marker_found is False
    print("  OK detect_crash: no shutdown event")
    store.close()


def test_scenario_result_fields_populated(tmp_path: Path) -> None:
    """StartupScenarioResult has all required fields populated (SC3)."""
    store = _make_store(tmp_path)
    _seed_session(store, "2026-04-16")
    _seed_shutdown(store, "2026-04-16T15:30:00+05:30")
    ks = _make_kill_switch()
    log = _CapturingLogger()
    today = date(2026, 4, 16)

    result = detect_startup_scenario(store, ks, today, log)

    assert isinstance(result.scenario, StartupScenario)
    assert isinstance(result.detection_details, dict)
    assert result.session_date_previous == date(2026, 4, 16)
    assert result.kill_state == "INACTIVE"
    print("  OK scenario_result: all fields present")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# check_clock_skew tests (SC5)
# ─────────────────────────────────────────────────────────────────────────────

def test_clock_skew_within_tolerance_passes(tmp_path: Path) -> None:
    """Skew within tolerance -> passed=True, skew_sec correct (SC5)."""
    ta = _make_time_authority(skew_sec=2.3)
    adapter = _make_broker_adapter()
    log = _CapturingLogger()

    result = check_clock_skew(ta, adapter, log)

    assert result.passed is True
    assert result.skew_sec == 2.3
    assert result.error == ""
    print("  OK clock_skew: within tolerance passes")


def test_clock_skew_too_large_fails(tmp_path: Path) -> None:
    """ClockSkewTooLarge raised -> passed=False, error populated (SC5, G4)."""
    ta = _make_time_authority(raise_on_assert=True)
    adapter = _make_broker_adapter()
    log = _CapturingLogger()

    result = check_clock_skew(ta, adapter, log)

    assert result.passed is False
    assert result.skew_sec == 35.0
    assert result.tolerance_sec == 30.0
    assert result.error != ""
    print("  OK clock_skew: too large -> failed")


def test_clock_skew_broker_error_fails(tmp_path: Path) -> None:
    """Broker adapter raises -> passed=False with error message (SC5)."""
    ta = _make_time_authority()
    adapter = _make_broker_adapter(raise_error=True)
    log = _CapturingLogger()

    result = check_clock_skew(ta, adapter, log)

    assert result.passed is False
    assert "broker unreachable" in result.error
    assert log.has_error("broker adapter")
    print("  OK clock_skew: broker error -> failed")


def test_clock_skew_naive_broker_timestamp(tmp_path: Path) -> None:
    """Naive broker timestamp (no tzinfo) handled without exception (regression)."""
    # Regression guard per memory: naive datetime must not cause AttributeError
    naive_ts = datetime.now().replace(tzinfo=None)  # naive
    ta = _MockTimeAuthority(skew_sec=0.5)

    adapter = MagicMock()
    adapter.get_server_time.return_value = naive_ts

    log = _CapturingLogger()
    result = check_clock_skew(ta, adapter, log)

    assert result.passed is True
    print("  OK clock_skew: naive broker timestamp handled")


def test_clock_skew_tolerance_sec_reflected_in_result() -> None:
    """HIGH #5 regression: tolerance_sec passed in is reflected in ClockCheckResult."""
    ta = _make_time_authority(skew_sec=1.0)
    adapter = _make_broker_adapter()
    log = _CapturingLogger()

    result = check_clock_skew(ta, adapter, log, tolerance_sec=45.0)

    assert result.passed is True
    assert result.tolerance_sec == 45.0, (
        f"Expected tolerance_sec=45.0, got {result.tolerance_sec}"
    )
    print("  OK HIGH #5: clock_skew tolerance_sec from config reflected in result")


# ─────────────────────────────────────────────────────────────────────────────
# check_config_hash tests (SC6)
# ─────────────────────────────────────────────────────────────────────────────

def test_config_hash_first_run_no_previous(tmp_path: Path) -> None:
    """No previous hash in session -> changed=False, changed_files=[] (SC6)."""
    store = _make_store(tmp_path)
    ac = _make_app_config({"system_config.yaml": "abc123"})
    log = _CapturingLogger()

    result = check_config_hash(store, ac, log)

    assert result.changed is False
    assert result.changed_files == []
    assert result.previous_hashes == {}
    assert result.current_hashes == {"system_config.yaml": "abc123"}
    print("  OK config_hash: first run no change")
    store.close()


def test_config_hash_matches_no_change(tmp_path: Path) -> None:
    """Hashes match -> changed=False (SC6)."""
    store = _make_store(tmp_path)
    hashes = {"system_config.yaml": "aaa", "broker_costs.yaml": "bbb"}
    _seed_session(store, "2026-04-16")
    with store.transaction() as cur:
        cur.execute(
            "UPDATE session SET last_config_hash = ? WHERE id = 1",
            (json.dumps(hashes),),
        )
    ac = _make_app_config(hashes)
    log = _CapturingLogger()

    result = check_config_hash(store, ac, log)

    assert result.changed is False
    assert result.changed_files == []
    print("  OK config_hash: unchanged")
    store.close()


def test_config_hash_differs_changed_files(tmp_path: Path) -> None:
    """Hashes differ -> changed=True, changed_files lists differing files."""
    store = _make_store(tmp_path)
    old_hashes = {"system_config.yaml": "aaa", "broker_costs.yaml": "bbb"}
    new_hashes = {"system_config.yaml": "aaa", "broker_costs.yaml": "ccc"}
    _seed_session(store, "2026-04-16")
    with store.transaction() as cur:
        cur.execute(
            "UPDATE session SET last_config_hash = ? WHERE id = 1",
            (json.dumps(old_hashes),),
        )
    ac = _make_app_config(new_hashes)
    log = _CapturingLogger()

    result = check_config_hash(store, ac, log)

    assert result.changed is True
    assert "broker_costs.yaml" in result.changed_files
    assert "system_config.yaml" not in result.changed_files
    print("  OK config_hash: changed_files detected")
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# check_scanner_connectivity tests (SC7)
# ─────────────────────────────────────────────────────────────────────────────

def test_scanner_all_reachable(tmp_path: Path) -> None:
    """All scanners return 200 -> all_reachable=True (SC7)."""
    swm = _make_scan_webhook_map(["scanner_a", "scanner_b"])
    cs = MagicMock()
    cs.scanners = {}
    log = _CapturingLogger()

    result = check_scanner_connectivity(swm, cs, _http_ok, log)

    assert result.all_reachable is True
    assert len(result.results) == 2
    assert all(r.reachable for r in result.results)
    print("  OK scanner_connectivity: all reachable")


def test_scanner_one_404_unreachable(tmp_path: Path) -> None:
    """One scanner returns 404 -> reachable=False for that entry (SC7)."""
    swm = MagicMock()
    e_ok = MagicMock()
    e_ok.chartink_url = "https://chartink.com/ok-scanner"
    e_bad = MagicMock()
    e_bad.chartink_url = "https://chartink.com/bad-scanner"
    swm.scanners = {"ok_scanner": e_ok, "bad_scanner": e_bad}

    def _mixed(url: str, timeout: float):
        if "bad" in url:
            return (404, "")
        return (200, "ok")

    log = _CapturingLogger()
    result = check_scanner_connectivity(swm, MagicMock(), _mixed, log)

    assert result.all_reachable is False
    bad = next(r for r in result.results if r.scanner_name == "bad_scanner")
    ok  = next(r for r in result.results if r.scanner_name == "ok_scanner")
    assert bad.reachable is False
    assert bad.status_code == 404
    assert ok.reachable is True
    print("  OK scanner_connectivity: 404 -> unreachable")


def test_scanner_timeout_error(tmp_path: Path) -> None:
    """http_fetcher_fn raises -> reachable=False with error (SC7)."""
    swm = _make_scan_webhook_map(["scanner_x"])
    log = _CapturingLogger()

    result = check_scanner_connectivity(swm, MagicMock(), _http_timeout, log)

    assert result.all_reachable is False
    assert result.results[0].reachable is False
    assert result.results[0].error is not None
    print("  OK scanner_connectivity: timeout -> unreachable")


def test_scanner_empty_map(tmp_path: Path) -> None:
    """Empty scan_webhook_map -> all_reachable=True (vacuous, SC7)."""
    swm = MagicMock()
    swm.scanners = {}
    log = _CapturingLogger()

    result = check_scanner_connectivity(swm, MagicMock(), _http_ok, log)

    assert result.all_reachable is True
    assert result.results == []
    print("  OK scanner_connectivity: empty map -> vacuous True")


def test_scanner_returns_results_for_each(tmp_path: Path) -> None:
    """Result list has one ScannerCheck per scanner in map (SC7)."""
    names = ["sc_1", "sc_2", "sc_3"]
    swm = _make_scan_webhook_map(names)
    log = _CapturingLogger()

    result = check_scanner_connectivity(swm, MagicMock(), _http_ok, log)

    assert len(result.results) == 3
    result_names = {r.scanner_name for r in result.results}
    assert result_names == set(names)
    print("  OK scanner_connectivity: one result per scanner")


# ─────────────────────────────────────────────────────────────────────────────
# check_webhook_endpoint tests (SC8)
# ─────────────────────────────────────────────────────────────────────────────

def test_webhook_reachable_200(tmp_path: Path) -> None:
    """http_fetcher returns 200 -> reachable=True (SC8)."""
    log = _CapturingLogger()
    result = check_webhook_endpoint(
        "http://localhost:5000/health", _http_ok, log
    )
    assert result.reachable is True
    assert result.status_code == 200
    print("  OK webhook_endpoint: 200 -> reachable")


def test_webhook_connection_refused(tmp_path: Path) -> None:
    """http_fetcher raises -> reachable=False (SC8)."""
    log = _CapturingLogger()
    result = check_webhook_endpoint(
        "http://localhost:5000/health", _http_timeout, log
    )
    assert result.reachable is False
    assert result.status_code is None
    print("  OK webhook_endpoint: connection refused -> unreachable")


def test_webhook_returns_body(tmp_path: Path) -> None:
    """Response body captured in response_body field (SC8)."""
    log = _CapturingLogger()
    result = check_webhook_endpoint(
        "http://localhost:5000/health", _http_ok, log
    )
    assert result.response_body is not None
    assert len(result.response_body) > 0
    print("  OK webhook_endpoint: response_body populated")


# ─────────────────────────────────────────────────────────────────────────────
# check_config_files_present tests (SC9)
# ─────────────────────────────────────────────────────────────────────────────

def _make_config_dir(tmp_path: Path, exclude: list = None) -> Path:
    """Create a config dir with all required files minus any in exclude."""
    from datetime import datetime as _dt
    year = _dt.now().year
    required = [
        "system_config.yaml", "broker_costs.yaml", "broker_limits.yaml",
        "slippage_model.yaml", "scoring_weights.yaml",
        "scan_webhook_map.yaml", "chartink_scanners.yaml",
        f"nse_holidays_{year}.yaml",
        "instruments.csv",  # IC13: required at startup
        "accounts.csv",     # IC13: required at startup
    ]
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    for f in required:
        if not exclude or f not in exclude:
            (cfg_dir / f).write_text("# stub")
    return cfg_dir


def test_config_files_all_present(tmp_path: Path) -> None:
    """All required files present -> empty missing list (SC9)."""
    cfg_dir = _make_config_dir(tmp_path)
    log = _CapturingLogger()

    missing = check_config_files_present(cfg_dir, log)

    assert missing == [], f"Unexpected missing: {missing}"
    print("  OK config_files_present: all present")


def test_config_files_missing_one(tmp_path: Path) -> None:
    """One file absent -> that file name in missing list (SC9)."""
    cfg_dir = _make_config_dir(tmp_path, exclude=["system_config.yaml"])
    log = _CapturingLogger()

    missing = check_config_files_present(cfg_dir, log)

    assert "system_config.yaml" in missing
    assert len(missing) == 1
    print("  OK config_files_present: one missing detected")


def test_config_files_multiple_missing(tmp_path: Path) -> None:
    """Multiple absent files -> all in returned list (SC9)."""
    cfg_dir = _make_config_dir(
        tmp_path,
        exclude=["system_config.yaml", "broker_costs.yaml"]
    )
    log = _CapturingLogger()

    missing = check_config_files_present(cfg_dir, log)

    assert "system_config.yaml" in missing
    assert "broker_costs.yaml" in missing
    assert len(missing) == 2
    print("  OK config_files_present: multiple missing detected")


# ─────────────────────────────────────────────────────────────────────────────
# check_market_holiday_today tests (SC10)
# ─────────────────────────────────────────────────────────────────────────────

def test_market_holiday_saturday(tmp_path: Path) -> None:
    """Saturday -> True regardless of holiday list (SC10)."""
    mw = _make_market_windows()
    log = _CapturingLogger()
    saturday = date(2026, 4, 18)  # Saturday
    assert saturday.weekday() == 5

    result = check_market_holiday_today(mw, saturday, log)

    assert result is True
    print("  OK market_holiday: Saturday -> True")


def test_market_holiday_configured_date(tmp_path: Path) -> None:
    """Date in holidays list -> True (SC10)."""
    mw = _make_market_windows(holidays=["2026-04-14"])
    log = _CapturingLogger()
    holiday = date(2026, 4, 14)  # Tuesday but configured holiday

    result = check_market_holiday_today(mw, holiday, log)

    assert result is True
    print("  OK market_holiday: configured holiday -> True")


def test_market_holiday_regular_weekday(tmp_path: Path) -> None:
    """Regular weekday not in holiday list -> False (SC10)."""
    mw = _make_market_windows(holidays=["2026-04-14"])
    log = _CapturingLogger()
    weekday = date(2026, 4, 16)  # Thursday, not in list

    result = check_market_holiday_today(mw, weekday, log)

    assert result is False
    print("  OK market_holiday: regular weekday -> False")


# ─────────────────────────────────────────────────────────────────────────────
# check_required_secrets tests (SC11)
# ─────────────────────────────────────────────────────────────────────────────

def test_secrets_all_present(tmp_path: Path) -> None:
    """All env vars set -> empty missing list (SC11)."""
    log = _CapturingLogger()
    keys = ["_TST_KEY_A", "_TST_KEY_B"]
    os.environ["_TST_KEY_A"] = "val_a"
    os.environ["_TST_KEY_B"] = "val_b"
    try:
        missing = check_required_secrets(keys, log)
        assert missing == []
        print("  OK check_secrets: all present")
    finally:
        os.environ.pop("_TST_KEY_A", None)
        os.environ.pop("_TST_KEY_B", None)


def test_secrets_missing_one(tmp_path: Path) -> None:
    """One key absent -> that key in missing list (SC11)."""
    log = _CapturingLogger()
    key = "_TST_MISSING_KEY_XYZ"
    os.environ.pop(key, None)

    missing = check_required_secrets([key], log)

    assert key in missing
    assert len(missing) == 1
    print("  OK check_secrets: missing key detected")


def test_secrets_multiple_missing(tmp_path: Path) -> None:
    """Multiple keys absent -> all in returned list (SC11)."""
    log = _CapturingLogger()
    keys = ["_TST_MISS_1", "_TST_MISS_2"]
    for k in keys:
        os.environ.pop(k, None)

    missing = check_required_secrets(keys, log)

    assert set(missing) == set(keys)
    print("  OK check_secrets: multiple missing")


# ─────────────────────────────────────────────────────────────────────────────
# run_all_startup_checks tests (SC12)
# ─────────────────────────────────────────────────────────────────────────────

def _base_run_args(tmp_path: Path, today: date = None):
    """Return a dict of run_all_startup_checks kwargs with healthy defaults."""
    store = _make_store(tmp_path)
    _seed_session(store, "2026-04-15")  # previous day -> COLD

    _today = today or date(2026, 4, 16)
    ta = _MockTimeAuthority(
        now=datetime(_today.year, _today.month, _today.day, 9, 0, tzinfo=_IST),
        skew_sec=0.5,
    )

    adapter = _make_broker_adapter()
    mw = _make_market_windows()  # no holidays, Thursday = weekday

    cfg_dir = _make_config_dir(tmp_path)

    env_key = "_RUN_ALL_TEST_KEY"
    os.environ[env_key] = "set"

    swm = _make_scan_webhook_map([])

    return dict(
        state_store=store,
        kill_switch=_make_kill_switch(),
        time_authority=ta,
        broker_adapter=adapter,
        market_windows=mw,
        app_config=_make_app_config({}),
        scan_webhook_map=swm,
        chartink_scanners=MagicMock(),
        http_fetcher_fn=_http_ok,
        webhook_url=None,
        required_secrets=[env_key],
        config_dir=cfg_dir,
        logger=_CapturingLogger(),
        _store_ref=store,
        _env_key=env_key,
    ), store, env_key


def test_run_all_ok_all_pass(tmp_path: Path) -> None:
    """All checks pass -> StartupReport.ok=True, no blocking_failures (SC12)."""
    kwargs, store, env_key = _base_run_args(tmp_path)
    store_ref = kwargs.pop("_store_ref")
    env_key_pop = kwargs.pop("_env_key")
    try:
        report = run_all_startup_checks(**kwargs)
        assert report.ok is True
        assert report.blocking_failures == []
        assert isinstance(report.scenario_details, StartupScenarioResult)
        print("  OK run_all: all pass -> ok=True")
    finally:
        os.environ.pop(env_key_pop, None)
        store_ref.close()


def test_run_all_missing_config_file_blocks(tmp_path: Path) -> None:
    """Missing config file -> ok=False, 'missing_config_files' in blocking (SC12)."""
    kwargs, store, env_key = _base_run_args(tmp_path)
    store_ref = kwargs.pop("_store_ref")
    kwargs.pop("_env_key")
    # Delete one required file
    (kwargs["config_dir"] / "system_config.yaml").unlink()
    try:
        report = run_all_startup_checks(**kwargs)
        assert report.ok is False
        assert "missing_config_files" in report.blocking_failures
        assert "system_config.yaml" in report.missing_config_files
        print("  OK run_all: missing config file blocks")
    finally:
        os.environ.pop(env_key, None)
        store_ref.close()


def test_run_all_missing_secret_blocks(tmp_path: Path) -> None:
    """Missing secret key -> ok=False, 'missing_secrets' in blocking (SC12)."""
    kwargs, store, env_key = _base_run_args(tmp_path)
    store_ref = kwargs.pop("_store_ref")
    kwargs.pop("_env_key")
    missing_key = "_RUN_ALL_ABSENT_KEY"
    os.environ.pop(missing_key, None)
    kwargs["required_secrets"] = [missing_key]
    try:
        report = run_all_startup_checks(**kwargs)
        assert report.ok is False
        assert "missing_secrets" in report.blocking_failures
        assert missing_key in report.missing_secrets
        print("  OK run_all: missing secret blocks")
    finally:
        store_ref.close()


def test_run_all_clock_skew_blocks(tmp_path: Path) -> None:
    """Clock skew too large -> ok=False, 'clock_skew' in blocking (SC12, G4)."""
    kwargs, store, env_key = _base_run_args(tmp_path)
    store_ref = kwargs.pop("_store_ref")
    kwargs.pop("_env_key")
    kwargs["time_authority"] = _make_time_authority(raise_on_assert=True)
    try:
        report = run_all_startup_checks(**kwargs)
        assert report.ok is False
        assert "clock_skew" in report.blocking_failures
        assert report.clock.passed is False
        print("  OK run_all: clock skew blocks")
    finally:
        os.environ.pop(env_key, None)
        store_ref.close()


def test_run_all_config_hash_changed_warns_not_blocks(tmp_path: Path) -> None:
    """Config hash changed -> ok=True, 'config_hash_changed' in warnings (SC12)."""
    kwargs, store, env_key = _base_run_args(tmp_path)
    store_ref = kwargs.pop("_store_ref")
    kwargs.pop("_env_key")
    # Seed old hashes
    old_hashes = {"system_config.yaml": "old_hash"}
    _seed_session(store_ref, "2026-04-15")
    with store_ref.transaction() as cur:
        cur.execute(
            "UPDATE session SET last_config_hash = ? WHERE id = 1",
            (json.dumps(old_hashes),),
        )
    # Provide different current hashes
    kwargs["app_config"] = _make_app_config({"system_config.yaml": "new_hash"})
    try:
        report = run_all_startup_checks(**kwargs)
        assert report.ok is True
        assert "config_hash_changed" in report.warnings
        assert "clock_skew" not in report.blocking_failures
        print("  OK run_all: config hash change warns only")
    finally:
        os.environ.pop(env_key, None)
        store_ref.close()


def test_run_all_scanner_unreachable_warns_not_blocks(tmp_path: Path) -> None:
    """Scanner unreachable -> ok=True (warning only), WARM scenario (SC12)."""
    tmp = tmp_path / "scanner_test"
    tmp.mkdir()
    store = _make_store(tmp)
    _seed_session(store, "2026-04-16")
    _seed_shutdown(store, "2026-04-16T15:30:00+05:30")
    ta = _MockTimeAuthority(now=datetime(2026, 4, 16, 9, 0, tzinfo=_IST))
    mw = _make_market_windows()
    cfg_dir = _make_config_dir(tmp)
    env_key = "_SCAN_TEST_KEY"
    os.environ[env_key] = "set"
    swm = _make_scan_webhook_map(["bad_scanner"])
    log = _CapturingLogger()
    try:
        report = run_all_startup_checks(
            state_store=store,
            kill_switch=_make_kill_switch(),
            time_authority=ta,
            broker_adapter=_make_broker_adapter(),
            market_windows=mw,
            app_config=_make_app_config({}),
            scan_webhook_map=swm,
            chartink_scanners=MagicMock(),
            http_fetcher_fn=_http_timeout,
            webhook_url=None,
            required_secrets=[env_key],
            config_dir=cfg_dir,
            logger=log,
        )
        assert report.ok is True
        assert "scanner_unreachable" in report.warnings
        print("  OK run_all: scanner unreachable warns only")
    finally:
        os.environ.pop(env_key, None)
        store.close()


def test_run_all_halt_scenario_blocks(tmp_path: Path) -> None:
    """HALT scenario -> ok=False, 'halt_requires_resume' in blocking (SC12, G5c)."""
    kwargs, store, env_key = _base_run_args(tmp_path)
    store_ref = kwargs.pop("_store_ref")
    kwargs.pop("_env_key")
    # Seed session as today
    _seed_session(store_ref, "2026-04-16")
    kwargs["kill_switch"] = _make_kill_switch(
        state="HARD_KILL",
        reason="manual_halt",
        triggered_at="2026-04-16T11:00:00+05:30",
    )
    try:
        report = run_all_startup_checks(**kwargs)
        assert report.ok is False
        assert "halt_requires_resume" in report.blocking_failures
        assert report.scenario == StartupScenario.HALT
        print("  OK run_all: halt scenario blocks")
    finally:
        os.environ.pop(env_key, None)
        store_ref.close()


def test_run_all_report_contains_all_sub_results(tmp_path: Path) -> None:
    """StartupReport has all sub-result fields populated (SC12)."""
    kwargs, store, env_key = _base_run_args(tmp_path)
    store_ref = kwargs.pop("_store_ref")
    kwargs.pop("_env_key")
    try:
        report = run_all_startup_checks(**kwargs)
        assert isinstance(report.clock, ClockCheckResult)
        assert isinstance(report.config_hash, ConfigHashResult)
        assert isinstance(report.scenario_details, StartupScenarioResult)
        assert isinstance(report.missing_secrets, list)
        assert isinstance(report.missing_config_files, list)
        assert isinstance(report.blocking_failures, list)
        assert isinstance(report.warnings, list)
        print("  OK run_all: all sub-results present")
    finally:
        os.environ.pop(env_key, None)
        store_ref.close()


def test_run_all_market_holiday_warns(tmp_path: Path) -> None:
    """Trading holiday -> ok=True, 'market_holiday' in warnings (SC12, SC10)."""
    kwargs, store, env_key = _base_run_args(tmp_path)
    store_ref = kwargs.pop("_store_ref")
    kwargs.pop("_env_key")
    # Use a Saturday as today
    kwargs["time_authority"] = _MockTimeAuthority(
        now=datetime(2026, 4, 18, 9, 0, tzinfo=_IST)
    )
    try:
        report = run_all_startup_checks(**kwargs)
        assert report.ok is True
        assert "market_holiday" in report.warnings
        assert report.market_holiday is True
        print("  OK run_all: holiday warns only")
    finally:
        os.environ.pop(env_key, None)
        store_ref.close()


def test_run_all_cold_skips_scanner_check(tmp_path: Path) -> None:
    """COLD scenario -> scanners field is None (check skipped per SC12)."""
    kwargs, store, env_key = _base_run_args(tmp_path)
    store_ref = kwargs.pop("_store_ref")
    kwargs.pop("_env_key")
    # prev day session -> COLD scenario
    try:
        report = run_all_startup_checks(**kwargs)
        assert report.scenario == StartupScenario.COLD
        assert report.scanners is None
        print("  OK run_all: COLD skips scanner check")
    finally:
        os.environ.pop(env_key, None)
        store_ref.close()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner (no pytest dependency)
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        # detect_startup_scenario (SC4)
        test_detect_cold_no_session_row,
        test_detect_cold_previous_day,
        test_detect_halt_hard_kill,
        test_detect_halt_soft_kill_same_day,
        test_detect_warm_soft_kill_previous_day_with_shutdown,
        test_detect_warm_shutdown_found,
        test_detect_crash_no_shutdown,
        test_scenario_result_fields_populated,
        # check_clock_skew (SC5)
        test_clock_skew_within_tolerance_passes,
        test_clock_skew_too_large_fails,
        test_clock_skew_broker_error_fails,
        test_clock_skew_naive_broker_timestamp,
        test_clock_skew_tolerance_sec_reflected_in_result,
        # check_config_hash (SC6)
        test_config_hash_first_run_no_previous,
        test_config_hash_matches_no_change,
        test_config_hash_differs_changed_files,
        # check_scanner_connectivity (SC7)
        test_scanner_all_reachable,
        test_scanner_one_404_unreachable,
        test_scanner_timeout_error,
        test_scanner_empty_map,
        test_scanner_returns_results_for_each,
        # check_webhook_endpoint (SC8)
        test_webhook_reachable_200,
        test_webhook_connection_refused,
        test_webhook_returns_body,
        # check_config_files_present (SC9)
        test_config_files_all_present,
        test_config_files_missing_one,
        test_config_files_multiple_missing,
        # check_market_holiday_today (SC10)
        test_market_holiday_saturday,
        test_market_holiday_configured_date,
        test_market_holiday_regular_weekday,
        # check_required_secrets (SC11)
        test_secrets_all_present,
        test_secrets_missing_one,
        test_secrets_multiple_missing,
        # run_all_startup_checks (SC12)
        test_run_all_ok_all_pass,
        test_run_all_missing_config_file_blocks,
        test_run_all_missing_secret_blocks,
        test_run_all_clock_skew_blocks,
        test_run_all_config_hash_changed_warns_not_blocks,
        test_run_all_scanner_unreachable_warns_not_blocks,
        test_run_all_halt_scenario_blocks,
        test_run_all_report_contains_all_sub_results,
        test_run_all_market_holiday_warns,
        test_run_all_cold_skips_scanner_check,
    ]

    print("=" * 70)
    print("startup_checks.py -- Test Suite (SC1-SC15)")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            try:
                test(Path(td))
            except AssertionError as e:
                failed.append((test.__name__, f"AssertionError: {e}"))
                print(f"  FAIL FAIL: {e}")
            except Exception as e:
                import traceback
                failed.append((test.__name__, f"{type(e).__name__}: {e}"))
                print(f"  FAIL ERROR: {type(e).__name__}: {e}")
                traceback.print_exc()

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1
    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
