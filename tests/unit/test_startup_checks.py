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
    StartupCheckFailed,
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
    check_kill_switch_present,
    check_paper_capital_consistency,
    check_scanner_connectivity,
    reset_scanner_warnings,  # FIX-D
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
        self.debugs:   list = []  # FIX-D: capture debug messages

    def info(self, msg: str, *args: object) -> None:
        self.infos.append(msg % args if args else msg)

    def warning(self, msg: str, *args: object) -> None:
        self.warnings.append(msg % args if args else msg)

    def error(self, msg: str, *args: object) -> None:
        self.errors.append(msg % args if args else msg)

    def critical(self, msg: str, *args: object) -> None:
        self.errors.append(msg % args if args else msg)

    def debug(self, msg: str, *args: object) -> None:
        # FIX-D: capture debug messages instead of discarding
        self.debugs.append(msg % args if args else msg)

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
    """
    Insert a minimal session row into the store.

    The kill_state argument is retained for backwards-compatible call sites
    but is no longer persisted — CFG-7 (2026-04-26 audit) removed the
    session.kill_state column. The kill_switch_state table is canonical.
    """
    _ = kill_state
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT OR REPLACE INTO session
              (id, session_date, account_id, broker, mode,
               trade_type, session_start, last_updated)
            VALUES (1, ?, 'ACC1', 'zerodha', 'PAPER',
                    'INTRADAY', ?, ?)
            """,
            (session_date,
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
    ac.system.scanner_check_delay_sec = 0.0  # prevent MagicMock comparison error
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


def test_startup_check_failed_inherits_from_trading_system_error() -> None:
    """
    EXC-2 (2026-04-26 audit): StartupCheckFailed must inherit from
    ConfigError (and transitively TradingSystemError) so the top-level
    safety net in main.py catches startup readiness failures.
    """
    from core.exceptions import ConfigError, TradingSystemError

    assert issubclass(StartupCheckFailed, ConfigError)
    assert issubclass(StartupCheckFailed, TradingSystemError)
    print("  OK StartupCheckFailed inherits from ConfigError/TradingSystemError (EXC-2)")


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
# FIX-D: Scanner None suppression and delay tests
# ─────────────────────────────────────────────────────────────────────────────

def test_fixd_first_none_logs_warning(tmp_path: Path) -> None:
    """FIX-D: First None status for a scanner logs at WARNING level."""
    reset_scanner_warnings()  # FIX-D: Clean state
    swm = _make_scan_webhook_map(["scanner_none"])
    log = _CapturingLogger()

    def _returns_none(url: str, timeout: float):
        return (None, "")

    result = check_scanner_connectivity(swm, MagicMock(), _returns_none, log)

    assert result.all_reachable is False
    assert result.results[0].status_code is None
    assert result.results[0].reachable is False

    # Check that WARNING was logged for first None
    assert len(log.warnings) == 1
    assert "scanner_none" in log.warnings[0]
    assert "status None" in log.warnings[0]
    print("  OK FIX-D: first None status logs WARNING")


def test_fixd_second_none_logs_debug(tmp_path: Path) -> None:
    """FIX-D: Second None status for same scanner logs at DEBUG level."""
    reset_scanner_warnings()  # FIX-D: Clean state
    swm = MagicMock()
    e1 = MagicMock()
    e1.chartink_url = "https://chartink.com/scanner1"
    e2 = MagicMock()
    e2.chartink_url = "https://chartink.com/scanner1"
    swm.scanners = {"scanner1_first": e1, "scanner1_second": e2}

    log = _CapturingLogger()

    def _returns_none(url: str, timeout: float):
        return (None, "")

    # First call - should log WARNING for both scanners (first occurrence)
    result1 = check_scanner_connectivity(swm, MagicMock(), _returns_none, log)

    # Second call - should log DEBUG for both (suppressed)
    result2 = check_scanner_connectivity(swm, MagicMock(), _returns_none, log)

    # First cycle: 2 scanners, both first-time None -> 2 WARNINGs
    assert len(log.warnings) == 2
    assert all("status None" in w for w in log.warnings)

    # Second cycle: 2 scanners, both repeat None -> 2 DEBUGs
    assert len(log.debugs) >= 2  # May have debug from sleep message too
    debug_suppressed = [d for d in log.debugs if "status None again" in d]
    assert len(debug_suppressed) == 2
    print("  OK FIX-D: second None status logs DEBUG (suppressed)")


def test_fixd_delay_applied_before_check_loop(tmp_path: Path) -> None:
    """FIX-D: delay_sec parameter causes sleep before scanner checks."""
    reset_scanner_warnings()  # FIX-D: Clean state
    import time
    swm = _make_scan_webhook_map(["scanner_a"])
    log = _CapturingLogger()

    start = time.monotonic()
    result = check_scanner_connectivity(
        swm, MagicMock(), _http_ok, log, delay_sec=0.1  # Use small delay for test speed
    )
    elapsed = time.monotonic() - start

    assert result.all_reachable is True
    # Should have slept at least 0.1 seconds (allow generous margin for Windows timer granularity)
    assert elapsed >= 0.05
    # Check DEBUG log for sleep message
    assert len(log.debugs) == 1
    assert "sleeping" in log.debugs[0]
    assert "0.1" in log.debugs[0]
    print("  OK FIX-D: delay applied before scanner checks")


def test_fixd_non_none_status_always_logs_warning(tmp_path: Path) -> None:
    """FIX-D: Non-None status codes (e.g., 404, 500) always log WARNING."""
    reset_scanner_warnings()  # FIX-D: Clean state
    swm = MagicMock()
    e1 = MagicMock()
    e1.chartink_url = "https://chartink.com/scanner1"
    e2 = MagicMock()
    e2.chartink_url = "https://chartink.com/scanner1"
    swm.scanners = {"scanner1_first": e1, "scanner1_second": e2}

    log = _CapturingLogger()

    def _returns_404(url: str, timeout: float):
        return (404, "Not Found")

    # Call twice - both should log WARNING (non-None status not suppressed)
    result1 = check_scanner_connectivity(swm, MagicMock(), _returns_404, log)
    result2 = check_scanner_connectivity(swm, MagicMock(), _returns_404, log)

    # Should have 4 WARNINGs total (2 scanners × 2 calls, no suppression for 404)
    warnings_404 = [w for w in log.warnings if "status 404" in w]
    assert len(warnings_404) == 4
    print("  OK FIX-D: non-None status codes always log WARNING")


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
    # FIX-135 Item 47: strategies dir needed for check_strategy_configs
    strat_dir = cfg_dir / "strategies"
    strat_dir.mkdir()
    (strat_dir / "test_strat.yaml").write_text(
        "name: test_strat\ndisplay_name: Test\ndescription: test\n"
        "direction: LONG\nintent: INTRADAY\norder_protocol: LIMIT_TRIPLE\n"
        "pipeline: INTRADAY\nhorizon: SAME_DAY\n"
        "entry_method: LIMIT\nsl_method: FIXED_PCT\nsl_pct: 0.01\n"
        "tgt_method: RISK_REWARD\ntgt_risk_reward: 2.0\n"
        "smart_tgt_enabled: false\npullback_wait_enabled: false\n"
        "min_score: 0\nlot_size: 1\n",
        encoding="utf-8",
    )
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
# check_instrument_cache_size tests (BL-20)
# ─────────────────────────────────────────────────────────────────────────────

class _FakeCache:
    """Duck-typed instrument_cache: exposes .count() -> int."""
    def __init__(self, n: int) -> None:
        self._n = n
    def count(self) -> int:
        return self._n


def test_check_instrument_cache_size_passes_at_threshold(tmp_path: Path) -> None:
    """Exactly min_rows -> passed=True (BL-20)."""
    from utils.startup_checks import check_instrument_cache_size
    log = _CapturingLogger()
    passed, count = check_instrument_cache_size(_FakeCache(1000), log, min_rows=1000)
    assert passed is True
    assert count == 1000
    print("  OK cache_size: at-threshold passes")


def test_check_instrument_cache_size_fails_below_threshold(tmp_path: Path) -> None:
    """Below min_rows -> passed=False (BL-20)."""
    from utils.startup_checks import check_instrument_cache_size
    log = _CapturingLogger()
    passed, count = check_instrument_cache_size(_FakeCache(5), log, min_rows=1000)
    assert passed is False
    assert count == 5
    print("  OK cache_size: below-threshold fails")


def test_check_instrument_cache_size_none_returns_false(tmp_path: Path) -> None:
    """None cache (load failed) -> passed=False, count=0 (BL-20)."""
    from utils.startup_checks import check_instrument_cache_size
    log = _CapturingLogger()
    passed, count = check_instrument_cache_size(None, log, min_rows=1000)
    assert passed is False
    assert count == 0
    print("  OK cache_size: None cache fails")


def test_check_instrument_cache_size_custom_min_rows(tmp_path: Path) -> None:
    """Custom min_rows is honoured (BL-20)."""
    from utils.startup_checks import check_instrument_cache_size
    log = _CapturingLogger()
    passed, _ = check_instrument_cache_size(_FakeCache(50), log, min_rows=10)
    assert passed is True
    passed2, _ = check_instrument_cache_size(_FakeCache(50), log, min_rows=100)
    assert passed2 is False
    print("  OK cache_size: custom min_rows honoured")


def test_run_all_blocks_if_instrument_cache_too_small(tmp_path: Path) -> None:
    """Tiny cache -> ok=False, 'instrument_cache_too_small' in blocking (BL-20)."""
    kwargs, store, env_key = _base_run_args(tmp_path)
    store_ref = kwargs.pop("_store_ref")
    kwargs.pop("_env_key")
    kwargs["instrument_cache"] = _FakeCache(5)
    kwargs["min_instrument_rows"] = 1000
    try:
        report = run_all_startup_checks(**kwargs)
        assert report.ok is False
        assert "instrument_cache_too_small" in report.blocking_failures
        assert report.instrument_cache_count == 5
        print("  OK run_all: tiny instrument cache blocks")
    finally:
        os.environ.pop(env_key, None)
        store_ref.close()


def test_run_all_passes_with_healthy_instrument_cache(tmp_path: Path) -> None:
    """Healthy cache (>= min_rows) -> ok=True, count populated (BL-20)."""
    kwargs, store, env_key = _base_run_args(tmp_path)
    store_ref = kwargs.pop("_store_ref")
    kwargs.pop("_env_key")
    kwargs["instrument_cache"] = _FakeCache(2800)
    kwargs["min_instrument_rows"] = 1000
    try:
        report = run_all_startup_checks(**kwargs)
        assert report.ok is True
        assert "instrument_cache_too_small" not in report.blocking_failures
        assert report.instrument_cache_count == 2800
        print("  OK run_all: healthy instrument cache passes")
    finally:
        os.environ.pop(env_key, None)
        store_ref.close()


def test_run_all_skips_instrument_cache_when_none(tmp_path: Path) -> None:
    """instrument_cache=None (default) -> check skipped, count=None (BL-20)."""
    kwargs, store, env_key = _base_run_args(tmp_path)
    store_ref = kwargs.pop("_store_ref")
    kwargs.pop("_env_key")
    # Do NOT pass instrument_cache -- default None -> check skipped
    try:
        report = run_all_startup_checks(**kwargs)
        assert "instrument_cache_too_small" not in report.blocking_failures
        assert report.instrument_cache_count is None
        print("  OK run_all: None cache skips check")
    finally:
        os.environ.pop(env_key, None)
        store_ref.close()


# ─────────────────────────────────────────────────────────────────────────────
# F.1 / EF-7 -- check_paper_capital_consistency (paper-mode regression guard)
# ─────────────────────────────────────────────────────────────────────────────

def test_f1_ef7_startup_check_fires_on_divergence(tmp_path: Path) -> None:
    """Paper mode with adapter.net != fm.total -> StartupCheckFailed raised."""
    fm = MagicMock()
    fm.total = 50_000.0
    adapter = MagicMock()
    adapter.get_margins.return_value = MagicMock(net=500_000.0)  # stale 500k
    logger = MagicMock()

    try:
        check_paper_capital_consistency(fm, adapter, is_paper=True, logger=logger)
    except StartupCheckFailed as exc:
        assert "50000" in str(exc) or "50_000" in str(exc).replace(",", "") or \
               "50000.00" in str(exc)
        assert "500000" in str(exc).replace(",", "") or "500_000" in str(exc)
        # CRITICAL log with grep tag
        critical_calls = [c for c in logger.critical.call_args_list
                          if "EF7_STARTUP_CHECK_DIVERGENCE" in str(c)]
        assert len(critical_calls) == 1
        print("  OK EF-7 startup check fires on divergence")
        return
    raise AssertionError(
        "check_paper_capital_consistency did not raise on adapter/fm divergence"
    )


def test_f1_ef7_startup_check_passes_on_match(tmp_path: Path) -> None:
    """Paper mode with adapter.net == fm.total -> no raise, info log emitted."""
    fm = MagicMock()
    fm.total = 50_000.0
    fm.get_snapshot.return_value = MagicMock(total=50_000.0)
    adapter = MagicMock()
    adapter.get_margins.return_value = MagicMock(net=50_000.0)
    logger = MagicMock()

    check_paper_capital_consistency(fm, adapter, is_paper=True, logger=logger)

    logger.critical.assert_not_called()
    info_calls = [c for c in logger.info.call_args_list
                  if "check_paper_capital_consistency: OK" in str(c)]
    assert len(info_calls) == 1
    print("  OK EF-7 startup check passes on exact match")


def test_f1_ef7_startup_check_noop_in_live_mode(tmp_path: Path) -> None:
    """Live mode -> check short-circuits BEFORE calling adapter.get_margins."""
    fm = MagicMock()
    fm.total = 50_000.0
    adapter = MagicMock()
    # Force adapter.get_margins to raise if called -- would fail the test.
    adapter.get_margins.side_effect = AssertionError(
        "get_margins must not be called in live mode (no-op)"
    )
    logger = MagicMock()

    check_paper_capital_consistency(fm, adapter, is_paper=False, logger=logger)

    adapter.get_margins.assert_not_called()
    logger.critical.assert_not_called()
    logger.info.assert_not_called()
    print("  OK EF-7 startup check is a no-op in live mode")


# ─────────────────────────────────────────────────────────────────────────────
# Q4(b) -- check_kill_switch_present (LIVE requires a real kill_switch)
# ─────────────────────────────────────────────────────────────────────────────

def test_q4b_kill_switch_missing_live_raises(tmp_path: Path) -> None:
    """LIVE + kill_switch=None -> StartupCheckFailed + CRITICAL grep-tagged log."""
    logger = MagicMock()
    try:
        check_kill_switch_present(kill_switch=None, is_paper=False, logger=logger)
    except StartupCheckFailed as exc:
        assert "LIVE" in str(exc) and "kill_switch" in str(exc)
        critical = [c for c in logger.critical.call_args_list
                    if "KILL_SWITCH_MISSING_LIVE" in str(c)]
        assert len(critical) == 1
        print("  OK Q4(b) live + kill_switch=None aborts startup")
        return
    raise AssertionError("check_kill_switch_present did not raise in live mode with None")


def test_q4b_kill_switch_present_live_ok(tmp_path: Path) -> None:
    """LIVE + a real kill_switch -> no raise, info OK log."""
    logger = MagicMock()
    check_kill_switch_present(kill_switch=MagicMock(), is_paper=False, logger=logger)
    logger.critical.assert_not_called()
    assert any("check_kill_switch_present: OK" in str(c) for c in logger.info.call_args_list)
    print("  OK Q4(b) live + kill_switch present passes")


def test_q4b_kill_switch_missing_paper_warns_not_raises(tmp_path: Path) -> None:
    """PAPER + kill_switch=None -> WARNING (wiring bug), but NOT a startup abort."""
    logger = MagicMock()
    check_kill_switch_present(kill_switch=None, is_paper=True, logger=logger)  # must not raise
    logger.critical.assert_not_called()
    assert any("kill_switch is None in PAPER mode" in str(c) for c in logger.warning.call_args_list)
    print("  OK Q4(b) paper + kill_switch=None warns, does not abort")


def test_q4b_kill_switch_present_paper_ok(tmp_path: Path) -> None:
    """PAPER + a real kill_switch -> no raise, no warning, info OK log."""
    logger = MagicMock()
    check_kill_switch_present(kill_switch=MagicMock(), is_paper=True, logger=logger)
    logger.critical.assert_not_called()
    logger.warning.assert_not_called()
    assert any("check_kill_switch_present: OK" in str(c) for c in logger.info.call_args_list)
    print("  OK Q4(b) paper + kill_switch present passes")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner (no pytest dependency)
# ─────────────────────────────────────────────────────────────────────────────

# ==============================================================================
# check_db_permissions (FIX-096)
# ==============================================================================

def test_fix096_correct_permissions_passes(tmp_path: Path):
    """All DB files with correct permissions → passed=True."""
    import logging
    from utils.startup_checks import check_db_permissions
    log = logging.getLogger("test")
    log.addHandler(logging.NullHandler())

    db_path = tmp_path / "test.db"
    db_path.touch()
    (tmp_path / "test.db-wal").touch()
    (tmp_path / "test.db-shm").touch()

    result = check_db_permissions(str(db_path), log)
    assert result.passed, "Should pass with correct permissions"
    assert len(result.errors) == 0
    assert len(result.chown_commands) == 0


def test_fix096_wal_without_db_corrupted(tmp_path: Path):
    """WAL exists but DB missing → corrupted state error."""
    import logging
    from utils.startup_checks import check_db_permissions
    log = logging.getLogger("test")
    log.addHandler(logging.NullHandler())

    db_path = tmp_path / "test.db"
    wal_path = tmp_path / "test.db-wal"
    wal_path.touch()  # WAL exists, DB does not

    result = check_db_permissions(str(db_path), log)
    assert not result.passed
    assert any("CORRUPTED STATE" in e for e in result.errors), \
        f"Should detect corrupted state, got: {result.errors}"


def test_fix096_db_path_populated(tmp_path: Path):
    """Result contains db_path and db_dir fields."""
    import logging
    from utils.startup_checks import check_db_permissions
    log = logging.getLogger("test")
    log.addHandler(logging.NullHandler())

    db_path = tmp_path / "test.db"
    db_path.touch()

    result = check_db_permissions(str(db_path), log)
    assert result.db_path == str(db_path)
    assert result.db_dir == str(tmp_path)


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
        test_startup_check_failed_inherits_from_trading_system_error,
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
        # FIX-D: Scanner None suppression and delay
        test_fixd_first_none_logs_warning,
        test_fixd_second_none_logs_debug,
        test_fixd_delay_applied_before_check_loop,
        test_fixd_non_none_status_always_logs_warning,
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
        # check_instrument_cache_size (BL-20)
        test_check_instrument_cache_size_passes_at_threshold,
        test_check_instrument_cache_size_fails_below_threshold,
        test_check_instrument_cache_size_none_returns_false,
        test_check_instrument_cache_size_custom_min_rows,
        test_run_all_blocks_if_instrument_cache_too_small,
        test_run_all_passes_with_healthy_instrument_cache,
        test_run_all_skips_instrument_cache_when_none,
        # check_paper_capital_consistency (F.1 / EF-7)
        test_f1_ef7_startup_check_fires_on_divergence,
        test_f1_ef7_startup_check_passes_on_match,
        test_f1_ef7_startup_check_noop_in_live_mode,
        # check_kill_switch_present (Q4(b))
        test_q4b_kill_switch_missing_live_raises,
        test_q4b_kill_switch_present_live_ok,
        test_q4b_kill_switch_missing_paper_warns_not_raises,
        test_q4b_kill_switch_present_paper_ok,
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
