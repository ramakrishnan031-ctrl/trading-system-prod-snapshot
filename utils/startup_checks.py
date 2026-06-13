"""
utils/startup_checks.py -- Trading System v2

Purpose:
    Library module providing all pre-trading startup checks.
    Pure functions + one detection function returning a StartupScenario.
    Called by main.py during Phase 0c (scenario detection) and Phase 0d
    (NTP + pre-flight checks).

    NO state mutations here -- writing system_events rows, updating the
    session table, or taking any process control action is the caller's job
    (SC15). This module only reads and reports.

Locked Decisions:
    SC1  -- Purpose: pure library, no side effects beyond logging
    SC2  -- StartupScenario enum: COLD / WARM / CRASH / HALT
    SC3  -- detect_startup_scenario() -> StartupScenarioResult
    SC4  -- Detection algorithm (G5a)
    SC5  -- check_clock_skew() -> ClockCheckResult
    SC6  -- check_config_hash() -> ConfigHashResult (CL4)
    SC7  -- check_scanner_connectivity() -> ScannerPreflightResult (P17)
    SC8  -- check_webhook_endpoint() -> WebhookEndpointResult (P17)
    SC9  -- check_config_files_present() -> list[str]
    SC10 -- check_market_holiday_today() -> bool
    SC11 -- check_required_secrets() -> list[str]
    SC12 -- run_all_startup_checks() -> StartupReport
    SC13 -- Layer 6 (utils/); injected deps via parameter
    SC14 -- Deterministic given same inputs
    SC15 -- NOT in scope: state mutations, process control, thread starting

What This Module Does NOT Do:
    - Does not write to the database (state mutations are caller's job)
    - Does not call sys.exit() or raise SystemExit
    - Does not start, stop, or construct subsystems
    - Does not import state_store, kill_switch, or broker directly
      (receives them as parameters for testability)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from importlib.metadata import version as pkg_version

from core.exceptions import ClockSkewTooLarge, ConfigError


# ─────────────────────────────────────────────────────────────────────────────
# IST convenience (stdlib-only; no time_authority import at module level)
# ─────────────────────────────────────────────────────────────────────────────

_IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
# SC2 -- StartupScenario enum
# ─────────────────────────────────────────────────────────────────────────────

class StartupScenario(Enum):
    """Four startup scenarios per G5a (SC2)."""
    COLD  = "COLD"   # new trading day
    WARM  = "WARM"   # same day, clean prior stop
    CRASH = "CRASH"  # same day, no SHUTDOWN marker found
    HALT  = "HALT"   # kill_switch was active


# ─────────────────────────────────────────────────────────────────────────────
# SC3 -- Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StartupScenarioResult:
    """Outcome of detect_startup_scenario() (SC3)."""
    scenario:               StartupScenario
    session_date_previous:  Optional[date]     # None on first-ever startup
    kill_state:             str                # "INACTIVE" | "SOFT_KILL" | "HARD_KILL"
    kill_reason:            str
    shutdown_marker_found:  bool
    last_shutdown_ts:       Optional[datetime]
    detection_details:      Dict[str, Any]


@dataclass(frozen=True)
class ClockCheckResult:
    """Outcome of check_clock_skew() (SC5)."""
    passed:        bool
    skew_sec:      float
    tolerance_sec: float
    broker_time:   datetime
    local_time:    datetime
    error:         str = ""


@dataclass(frozen=True)
class DbPermissionResult:
    """FIX-096: Outcome of check_db_permissions()."""
    passed:       bool
    db_path:      str
    db_dir:       str
    errors:       List[str] = field(default_factory=list)
    chown_commands: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConfigHashResult:
    """Outcome of check_config_hash() (SC6)."""
    changed:          bool
    changed_files:    List[str]
    previous_hashes:  Dict[str, str]
    current_hashes:   Dict[str, str]


@dataclass(frozen=True)
class ScannerCheck:
    """Per-scanner reachability result (SC7)."""
    scanner_name:         str
    url:                  str
    reachable:            bool
    status_code:          Optional[int]
    error:                Optional[str]
    response_has_content: bool


@dataclass(frozen=True)
class ScannerPreflightResult:
    """Aggregate scanner connectivity result (SC7)."""
    all_reachable: bool
    results:       List[ScannerCheck]


@dataclass(frozen=True)
class WebhookEndpointResult:
    """Outcome of check_webhook_endpoint() (SC8)."""
    reachable:     bool
    status_code:   Optional[int]
    response_body: Optional[str]


@dataclass(frozen=True)
class DiskSpaceResult:
    """Result of check_disk_space() (FIX-099)."""
    passed: bool              # True if free space >= min_free_disk_gb
    free_gb: float            # Free space in GB
    min_required_gb: float    # Minimum required from config
    error: Optional[str] = None  # Error message if check failed


@dataclass(frozen=True)
class NtpCheckResult:
    """FIX-129 (Item 27): Outcome of check_ntp_sync()."""
    passed:       bool       # True if drift within tolerance
    skipped:      bool       # True if check was skipped (e.g. paper mode)
    drift_sec:    float      # abs(local_time - ntp_time); 0.0 if skipped/failed
    warn_sec:     float      # warning threshold
    block_sec:    float      # blocking threshold
    ntp_host:     str        # NTP host queried
    error:        str = ""   # description if fetch failed


@dataclass(frozen=True)
class StartupReport:
    """Aggregate result of run_all_startup_checks() (SC12)."""
    ok:                  bool          # False if any blocking check failed
    scenario:            StartupScenario
    blocking_failures:   List[str]     # check names that caused ok=False
    warnings:            List[str]     # non-blocking issues
    scenario_details:    StartupScenarioResult
    clock:               ClockCheckResult
    config_hash:         ConfigHashResult
    scanners:            Optional[ScannerPreflightResult]  # None if skipped
    webhook:             Optional[WebhookEndpointResult]   # None if not requested
    market_holiday:      bool
    missing_secrets:     List[str]
    missing_config_files: List[str]
    instrument_cache_count: Optional[int] = None  # BL-20: None if check skipped
    disk_space:          Optional[DiskSpaceResult] = None  # FIX-099: None if skipped
    ntp:                 Optional[NtpCheckResult] = None  # FIX-129 Item 27
    temp_config:         Optional[TempConfigResult] = None  # FIX-151


# ─────────────────────────────────────────────────────────────────────────────
# SC4 -- Startup scenario detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_startup_scenario(
    state_store,
    kill_switch,
    today_date: date,
    logger,
) -> StartupScenarioResult:
    """
    Determine which of the four G5a startup scenarios applies (SC3, SC4).

    Algorithm (SC4):
      1. No session row at all -> COLD (first-ever startup)
      2. kill_switch state == HARD_KILL -> HALT (requires --resume per G5c)
      3. kill_switch state == SOFT_KILL:
           triggered_at.date() == today -> HALT (condition may still apply)
           triggered_at.date() < today  -> condition cleared; fall through
      4. session.session_date != today -> COLD (previous session was another day)
      5. Same day. SHUTDOWN event for today found -> WARM
         Same day. No SHUTDOWN event -> CRASH

    State mutations (writing STARTUP/CRASH_DETECTED rows) are the caller's
    responsibility per SC15.
    """
    session = state_store.get_session_row()

    # -- Step 1: no session ever written --
    if session is None:
        details = {"reason": "no_session_row"}
        logger.info("startup_scenario=COLD: no prior session row found")
        return StartupScenarioResult(
            scenario=StartupScenario.COLD,
            session_date_previous=None,
            kill_state="INACTIVE",
            kill_reason="",
            shutdown_marker_found=False,
            last_shutdown_ts=None,
            detection_details=details,
        )

    prev_date_str = session["session_date"]
    try:
        prev_date = date.fromisoformat(prev_date_str)
    except (ValueError, TypeError):
        prev_date = None

    # Collect kill_switch info
    ks_status = kill_switch.status()
    ks_state_str = ks_status.get("state", "INACTIVE")
    ks_reason = ks_status.get("reason", "") or ""
    ks_triggered_at_str = ks_status.get("triggered_at")

    # -- Step 2: HARD_KILL -> always HALT --
    if ks_state_str == "HARD_KILL":
        details = {
            "reason": "hard_kill_active",
            "kill_reason": ks_reason,
            "triggered_at": ks_triggered_at_str,
        }
        logger.info(
            "startup_scenario=HALT: hard_kill active (reason=%s)", ks_reason
        )
        return StartupScenarioResult(
            scenario=StartupScenario.HALT,
            session_date_previous=prev_date,
            kill_state="HARD_KILL",
            kill_reason=ks_reason,
            shutdown_marker_found=False,
            last_shutdown_ts=None,
            detection_details=details,
        )

    # -- Step 3: SOFT_KILL -- check if condition has cleared --
    if ks_state_str == "SOFT_KILL":
        condition_cleared = _soft_kill_condition_cleared(
            ks_triggered_at_str, today_date
        )
        if not condition_cleared:
            details = {
                "reason": "soft_kill_same_day",
                "kill_reason": ks_reason,
                "triggered_at": ks_triggered_at_str,
            }
            logger.info(
                "startup_scenario=HALT: soft_kill active same-day "
                "(reason=%s, triggered=%s)",
                ks_reason, ks_triggered_at_str,
            )
            return StartupScenarioResult(
                scenario=StartupScenario.HALT,
                session_date_previous=prev_date,
                kill_state="SOFT_KILL",
                kill_reason=ks_reason,
                shutdown_marker_found=False,
                last_shutdown_ts=None,
                detection_details=details,
            )
        # condition cleared: fall through to COLD/WARM/CRASH detection

    # -- Step 4: different day -> COLD --
    if prev_date is None or prev_date < today_date:
        details = {
            "reason": "new_trading_day",
            "previous_session_date": prev_date_str,
        }
        logger.info(
            "startup_scenario=COLD: new day (prev=%s, today=%s)",
            prev_date_str, today_date.isoformat(),
        )
        return StartupScenarioResult(
            scenario=StartupScenario.COLD,
            session_date_previous=prev_date,
            kill_state=ks_state_str,
            kill_reason=ks_reason,
            shutdown_marker_found=False,
            last_shutdown_ts=None,
            detection_details=details,
        )

    # -- Step 5: same day -- look for SHUTDOWN marker --
    shutdown_row = state_store.find_shutdown_event_for_date(today_date.isoformat())
    if shutdown_row is not None:
        shutdown_ts = _parse_ts(shutdown_row["timestamp"])
        details = {
            "reason": "clean_shutdown_found",
            "shutdown_ts": shutdown_row["timestamp"],
        }
        logger.info(
            "startup_scenario=WARM: SHUTDOWN event found (ts=%s)",
            shutdown_row["timestamp"],
        )
        return StartupScenarioResult(
            scenario=StartupScenario.WARM,
            session_date_previous=prev_date,
            kill_state=ks_state_str,
            kill_reason=ks_reason,
            shutdown_marker_found=True,
            last_shutdown_ts=shutdown_ts,
            detection_details=details,
        )
    else:
        # Audit #17: before classifying as CRASH, check if EOD squareoff
        # completed for today. If the operator stopped the process AFTER
        # the square-off but BEFORE the SHUTDOWN event was written, there
        # is no crash -- the system ran its end-of-day sequence and is
        # safe to resume as WARM.
        eod_row = None
        try:
            eod_row = state_store.get_eod_squareoff_log_for_date(
                today_date.isoformat()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "startup_scenario: eod_squareoff_log lookup failed: %s", exc
            )

        if eod_row is not None and (eod_row["status"] or "").upper() == "COMPLETE":
            details = {
                "reason": "eod_squareoff_complete_no_shutdown_marker",
                "eod_fired_at": eod_row["fired_at"],
                "eod_completed_at": eod_row["completed_at"],
            }
            logger.info(
                "startup_scenario=WARM: EOD squareoff COMPLETE for today "
                "(fired_at=%s); treating missing SHUTDOWN event as clean stop",
                eod_row["fired_at"],
            )
            return StartupScenarioResult(
                scenario=StartupScenario.WARM,
                session_date_previous=prev_date,
                kill_state=ks_state_str,
                kill_reason=ks_reason,
                shutdown_marker_found=False,
                last_shutdown_ts=_parse_ts(eod_row["completed_at"]),
                detection_details=details,
            )

        details = {
            "reason": "no_shutdown_marker",
            "session_date": prev_date_str,
        }
        logger.info(
            "startup_scenario=CRASH: same day, no SHUTDOWN event found"
        )
        return StartupScenarioResult(
            scenario=StartupScenario.CRASH,
            session_date_previous=prev_date,
            kill_state=ks_state_str,
            kill_reason=ks_reason,
            shutdown_marker_found=False,
            last_shutdown_ts=None,
            detection_details=details,
        )


def _soft_kill_condition_cleared(triggered_at_str: Optional[str], today: date) -> bool:
    """
    Return True if the soft_kill was triggered on a previous day (condition
    considered cleared for daily-reset reasons such as daily_loss; G5c).
    Same-day soft_kills are treated as still-active (conservative).
    """
    if not triggered_at_str:
        return False
    try:
        triggered_dt = datetime.fromisoformat(triggered_at_str)
        return triggered_dt.date() < today
    except (ValueError, AttributeError):
        return False


def _parse_ts(ts_str: str) -> Optional[datetime]:
    """Parse ISO-8601 timestamp string, returning None on failure."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SC5 -- NTP / broker clock check
# ─────────────────────────────────────────────────────────────────────────────

def check_clock_skew(
    time_authority,
    broker_adapter,
    logger,
    tolerance_sec: float = 30.0,
) -> ClockCheckResult:
    """
    Check VM clock against broker server time (SC5, G4).

    Calls broker_adapter.get_server_time() -> datetime to obtain a broker
    timestamp, then calls time_authority.assert_clock_at_startup(broker_ts).
    If ClockSkewTooLarge is raised the skew exceeded the threshold and
    the result has passed=False.

    Args:
        tolerance_sec: skew threshold for reporting; pass
            app_config.system.clock.startup_max_skew_sec (HIGH #5 fix).
    Caller (main.py) decides whether to refuse start per G4.
    """
    local_now = time_authority.now_ist()
    broker_ts = None
    try:
        broker_ts = broker_adapter.get_server_time()
        skew_sec = time_authority.assert_clock_at_startup(broker_ts)
        return ClockCheckResult(
            passed=True,
            skew_sec=float(skew_sec),
            tolerance_sec=tolerance_sec,
            broker_time=broker_ts,
            local_time=local_now,
        )
    except ClockSkewTooLarge as exc:
        skew = float(exc.context.get("skew_seconds", 0.0))
        tol  = float(exc.context.get("threshold_sec", tolerance_sec))
        logger.warning(
            "check_clock_skew: skew %.1fs exceeds tolerance %.0fs", skew, tol
        )
        return ClockCheckResult(
            passed=False,
            skew_sec=skew,
            tolerance_sec=tol,
            broker_time=broker_ts if broker_ts is not None else local_now,
            local_time=local_now,
            error=str(exc),
        )
    except Exception as exc:
        logger.error("check_clock_skew: broker adapter error: %s", exc)
        return ClockCheckResult(
            passed=False,
            skew_sec=0.0,
            tolerance_sec=tolerance_sec,
            broker_time=local_now,
            local_time=local_now,
            error=str(exc),
        )


# ─────────────────────────────────────────────────────────────────────────────
# FIX-129 Item 27 -- NTP clock sync check
# ─────────────────────────────────────────────────────────────────────────────

def _query_ntp_time(host: str, timeout_sec: float = 5.0) -> float:
    """
    Query an NTP server via UDP and return its UTC timestamp as a float
    (seconds since Unix epoch).

    Uses NTP v3 request (48-byte packet, RFC 5905). Pure stdlib — no ntplib.
    Raises OSError/socket.timeout on network failure.
    """
    import socket
    import struct

    NTP_PORT = 123
    NTP_EPOCH_DELTA = 2208988800  # seconds between NTP epoch (1900) and Unix epoch (1970)

    # NTP client request: li=0, version=3, mode=3 → byte = 0b00011011 = 0x1B
    request = b"\x1b" + b"\x00" * 47

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout_sec)
        s.sendto(request, (host, NTP_PORT))
        data, _ = s.recvfrom(1024)

    if len(data) < 48:
        raise ValueError(f"NTP response too short: {len(data)} bytes")

    # Transmit Timestamp is at bytes 40-47 (big-endian 64-bit fixed point: 32 int + 32 frac)
    integ, frac = struct.unpack("!II", data[40:48])
    ntp_time = integ + frac / 2**32
    return ntp_time - NTP_EPOCH_DELTA


def check_ntp_sync(
    logger,
    ntp_host: str = "pool.ntp.org",
    warn_sec: float = 2.0,
    block_sec: float = 5.0,
    timeout_sec: float = 5.0,
    ntp_fetcher_fn: Optional[Callable] = None,  # injectable for tests
) -> NtpCheckResult:
    """
    FIX-129 (Item 27): Check local clock against NTP server.

    Queries `ntp_host` (default: pool.ntp.org) via UDP/123.
    Compares the returned UTC time with the local clock.

    Decision:
      drift < warn_sec  → passed=True (OK)
      warn_sec <= drift < block_sec → passed=False, warning (non-blocking)
      drift >= block_sec → passed=False, blocking failure

    Args:
        ntp_fetcher_fn: injectable for tests. Signature: (host: str) -> float
            (UTC timestamp). If None, uses _query_ntp_time.
    """
    import time as _time_mod

    fetcher = ntp_fetcher_fn or (lambda host: _query_ntp_time(host, timeout_sec))

    try:
        ntp_utc = fetcher(ntp_host)
    except Exception as exc:
        logger.warning("check_ntp_sync: failed to query %s: %s", ntp_host, exc)
        return NtpCheckResult(
            passed=True,    # best-effort: don't block startup on NTP unreachable
            skipped=True,
            drift_sec=0.0,
            warn_sec=warn_sec,
            block_sec=block_sec,
            ntp_host=ntp_host,
            error=str(exc),
        )

    local_utc = _time_mod.time()
    drift = abs(local_utc - ntp_utc)
    passed = drift < block_sec

    if drift >= block_sec:
        logger.critical(
            "check_ntp_sync: BLOCKING drift %.2fs >= %.0fs threshold (host=%s)",
            drift, block_sec, ntp_host,
        )
    elif drift >= warn_sec:
        logger.warning(
            "check_ntp_sync: drift %.2fs >= %.0fs warn threshold (host=%s)",
            drift, warn_sec, ntp_host,
        )
    else:
        logger.info(
            "check_ntp_sync: drift %.3fs OK (host=%s)", drift, ntp_host
        )

    return NtpCheckResult(
        passed=passed,
        skipped=False,
        drift_sec=drift,
        warn_sec=warn_sec,
        block_sec=block_sec,
        ntp_host=ntp_host,
        error="" if passed else f"drift {drift:.2f}s >= block threshold {block_sec:.0f}s",
    )


# ─────────────────────────────────────────────────────────────────────────────
# SC6 -- Config hash diff check (CL4)
# ─────────────────────────────────────────────────────────────────────────────

def check_config_hash(
    state_store,
    current_app_config,
    logger,
) -> ConfigHashResult:
    """
    Compare the current AppConfig file hashes against the last-saved hashes
    in the session table (CL4, SC6).

    The session.last_config_hash column stores a JSON-serialised dict of
    {filename: sha256_hex}. On first run (None), changed=False is returned
    (it is not a config change; it is simply the first time hashes are seen).
    """
    session = state_store.get_session_row()
    current_hashes: Dict[str, str] = dict(getattr(current_app_config, "file_hashes", {}))

    previous_hashes: Dict[str, str] = {}
    if session is not None and session["last_config_hash"] is not None:
        try:
            loaded = json.loads(session["last_config_hash"])
            if isinstance(loaded, dict):
                previous_hashes = {str(k): str(v) for k, v in loaded.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning(
                "check_config_hash: could not parse last_config_hash as JSON"
            )

    if not previous_hashes:
        # First run or corrupt stored hash: not a diff
        logger.info("check_config_hash: no previous hashes; treating as first run")
        return ConfigHashResult(
            changed=False,
            changed_files=[],
            previous_hashes={},
            current_hashes=current_hashes,
        )

    changed_files = sorted(
        fname
        for fname, h in current_hashes.items()
        if previous_hashes.get(fname) != h
    )
    if changed_files:
        logger.info(
            "check_config_hash: %d file(s) changed: %s",
            len(changed_files), changed_files,
        )

    return ConfigHashResult(
        changed=bool(changed_files),
        changed_files=changed_files,
        previous_hashes=previous_hashes,
        current_hashes=current_hashes,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SC7 -- Scanner connectivity pre-flight (P17)
# ─────────────────────────────────────────────────────────────────────────────

# FIX-D: Module-level set to track scanners that have logged None warning
# (persists across calls to suppress repeated warnings)
_logged_none_warnings: set[str] = set()


def reset_scanner_warnings() -> None:
    """Reset the module-level None warning tracker (for testing)."""
    global _logged_none_warnings
    _logged_none_warnings = set()


def check_scanner_connectivity(
    scan_webhook_map,
    chartink_scanners,
    http_fetcher_fn: Callable[[str, float], Tuple[Optional[int], str]],
    logger,
    timeout_sec: float = 10.0,
    delay_sec: float = 0.0,
) -> ScannerPreflightResult:
    """
    Verify Chartink scanner URLs are reachable via http_fetcher_fn (P17, SC7).

    Iterates over scan_webhook_map.scanners and uses each entry's chartink_url.
    Falls back to chartink_scanners.scanners[name] if chartink_url not found
    on the entry. Does NOT parse Chartink HTML -- reachability only.

    http_fetcher_fn: (url, timeout) -> (status_code or None, body_snippet)
    delay_sec: Optional delay before starting scanner checks (FIX-D: network stabilization)

    FIX-D: Suppresses repeated None-status warnings. First None for a scanner logs
    at WARNING level; subsequent None results for same scanner log at DEBUG only.
    """
    import time

    # FIX-D: Optional delay before scanner checks (network stabilization)
    if delay_sec > 0:
        logger.debug("check_scanner: sleeping %.1fs before scanner checks", delay_sec)
        time.sleep(delay_sec)

    scanners_dict: Dict[str, Any] = _get_scanners_dict(scan_webhook_map)
    chartink_dict: Dict[str, str] = _get_chartink_dict(chartink_scanners)
    results: List[ScannerCheck] = []

    # FIX-D: Use module-level set to suppress repeated None warnings across calls
    global _logged_none_warnings

    for scanner_name, entry in scanners_dict.items():
        url = _resolve_url(scanner_name, entry, chartink_dict)
        if not url:
            logger.warning("check_scanner: no URL found for %s", scanner_name)
            results.append(ScannerCheck(
                scanner_name=scanner_name,
                url="",
                reachable=False,
                status_code=None,
                error="no_url_configured",
                response_has_content=False,
            ))
            continue

        try:
            status_code, body = http_fetcher_fn(url, timeout_sec)
            reachable = status_code is not None and 200 <= status_code < 300
            if not reachable:
                # FIX-D: Suppress repeated None warnings (network startup transients)
                if status_code is None:
                    if scanner_name not in _logged_none_warnings:
                        # First None for this scanner -> WARNING
                        logger.warning(
                            "check_scanner: %s returned status None (unreachable)", scanner_name
                        )
                        _logged_none_warnings.add(scanner_name)
                    else:
                        # Subsequent None for same scanner -> DEBUG
                        logger.debug(
                            "check_scanner: %s returned status None again (suppressed)", scanner_name
                        )
                else:
                    # Non-None status (e.g., 404, 500) -> always log WARNING
                    logger.warning(
                        "check_scanner: %s returned status %s", scanner_name, status_code
                    )
            results.append(ScannerCheck(
                scanner_name=scanner_name,
                url=url,
                reachable=reachable,
                status_code=status_code,
                error=None,
                response_has_content=bool(body and body.strip()),
            ))
        except Exception as exc:
            logger.warning(
                "check_scanner: %s unreachable (%s): %s", scanner_name, url, exc
            )
            results.append(ScannerCheck(
                scanner_name=scanner_name,
                url=url,
                reachable=False,
                status_code=None,
                error=str(exc),
                response_has_content=False,
            ))

    all_reachable = all(r.reachable for r in results) if results else True
    return ScannerPreflightResult(all_reachable=all_reachable, results=results)


def _get_scanners_dict(scan_webhook_map: Any) -> Dict[str, Any]:
    """Extract the scanners mapping from a ScanWebhookMapConfig or plain dict."""
    raw = getattr(scan_webhook_map, "scanners", scan_webhook_map)
    if isinstance(raw, dict):
        return raw
    return {}


def _get_chartink_dict(chartink_scanners: Any) -> Dict[str, str]:
    """Extract the scanner-name -> url mapping from ChartinkScannersConfig or dict."""
    raw = getattr(chartink_scanners, "scanners", chartink_scanners)
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return {}


def _resolve_url(scanner_name: str, entry: Any, chartink_dict: Dict[str, str]) -> str:
    """Resolve the Chartink URL for a scanner entry."""
    # ScannerEntryConfig has chartink_url attribute
    if hasattr(entry, "chartink_url"):
        return str(entry.chartink_url)
    # plain string
    if isinstance(entry, str):
        return entry
    # dict with chartink_url key
    if isinstance(entry, dict) and "chartink_url" in entry:
        return str(entry["chartink_url"])
    # fall back to chartink_scanners map
    return chartink_dict.get(scanner_name, "")


# ─────────────────────────────────────────────────────────────────────────────
# SC8 -- Webhook endpoint self-test (P17)
# ─────────────────────────────────────────────────────────────────────────────

def check_webhook_endpoint(
    webhook_url: str,
    http_fetcher_fn: Callable[[str, float], Tuple[Optional[int], str]],
    logger,
    timeout_sec: float = 5.0,
) -> WebhookEndpointResult:
    """
    Verify the local webhook /health endpoint is reachable (P17, SC8).

    Called by main.py AFTER starting the Flask webhook receiver, to confirm
    Flask is listening. Equivalent to `curl http://localhost:5000/health`.
    """
    try:
        status_code, body = http_fetcher_fn(webhook_url, timeout_sec)
        reachable = status_code is not None and 200 <= status_code < 300
        if not reachable:
            logger.warning(
                "check_webhook: endpoint %s returned status %s",
                webhook_url, status_code,
            )
        return WebhookEndpointResult(
            reachable=reachable,
            status_code=status_code,
            response_body=body if body else None,
        )
    except Exception as exc:
        logger.warning("check_webhook: endpoint %s unreachable: %s", webhook_url, exc)
        return WebhookEndpointResult(
            reachable=False,
            status_code=None,
            response_body=None,
        )


# ─────────────────────────────────────────────────────────────────────────────
# SC9 -- Config file presence check
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_CONFIG_FILES_STATIC = [
    "system_config.yaml",
    "broker_costs.yaml",
    "broker_limits.yaml",
    "slippage_model.yaml",
    "scoring_weights.yaml",
    "scan_webhook_map.yaml",
    "chartink_scanners.yaml",
    "instruments.csv",   # IC13: instrument master data required at startup
    "accounts.csv",      # IC13: account registry required at startup
]


def check_config_files_present(
    config_dir: Path,
    logger,
) -> List[str]:
    """
    Verify all required config files exist in config_dir (SC9).

    Returns a list of missing file names (empty = all present). A non-empty
    return causes main.py to refuse start with a clear error before attempting
    Pydantic parsing (which gives cryptic messages on missing files).

    The nse_holidays file uses the current calendar year.
    """
    # FIX-107: Use explicit IST offset for year calculation to avoid timezone edge
    # cases during New Year transitions. This check runs before time_authority is
    # constructed, but we can still apply the IST offset (+05:30) directly.
    IST_OFFSET = timedelta(hours=5, minutes=30)
    current_year = (datetime.now(timezone.utc) + IST_OFFSET).year
    required = _REQUIRED_CONFIG_FILES_STATIC + [
        f"nse_holidays_{current_year}.yaml",
    ]
    missing: List[str] = []
    for fname in required:
        if not (config_dir / fname).exists():
            logger.warning("check_config_files: missing %s", fname)
            missing.append(fname)
    return missing


# ─────────────────────────────────────────────────────────────────────────────
# SC10 -- Market holiday check
# ─────────────────────────────────────────────────────────────────────────────

def check_market_holiday_today(
    market_windows,
    today_date: date,
    logger,
) -> bool:
    """
    Return True if today is a trading holiday (SC10).

    market_windows: any object with a `holidays` attribute (list[str] of
    YYYY-MM-DD strings). Satisfied by NseHolidaysConfig from config_loader.
    Saturday (weekday=5) and Sunday (weekday=6) are always holidays.
    """
    weekday = today_date.weekday()
    if weekday >= 5:  # Saturday or Sunday
        logger.info(
            "check_market_holiday: weekend (%s=%s)", today_date, today_date.strftime("%A")
        )
        return True

    holidays: List[str] = getattr(market_windows, "holidays", []) or []
    today_str = today_date.isoformat()
    if today_str in holidays:
        logger.info("check_market_holiday: configured holiday on %s", today_str)
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# BL-20 -- Instrument cache size guard
# ─────────────────────────────────────────────────────────────────────────────

def check_instrument_cache_size(
    instrument_cache,          # duck-typed: only requires .count() -> int
    logger,
    min_rows: int = 1000,
) -> Tuple[bool, int]:
    """
    Return (passed, actual_count).

    Audit BL-20: the repo previously shipped a stub instruments.csv with
    ~20 rows; the signal pipeline silently dropped any symbol not in the
    cache, so nearly every scanner signal was rejected. A healthy full
    NSE universe contains ~2500-3000 rows. This check blocks startup if
    the loaded cache is too thin to be useful, forcing the operator to
    re-run `scripts/refresh_instruments.py` before trading.

    Duck-typed on purpose: any object exposing ``.count() -> int`` works,
    which keeps startup_checks free of a hard import of InstrumentCache
    (SC13: Layer 6, injected deps).
    """
    if instrument_cache is None:
        logger.warning(
            "check_instrument_cache_size: cache is None (load failed or skipped)"
        )
        return (False, 0)

    count = int(instrument_cache.count())
    if count < min_rows:
        logger.warning(
            "check_instrument_cache_size: FAIL count=%d < min_rows=%d "
            "(re-run scripts/refresh_instruments.py)",
            count, min_rows,
        )
        return (False, count)

    logger.info(
        "check_instrument_cache_size: OK count=%d >= min_rows=%d",
        count, min_rows,
    )
    return (True, count)


# ─────────────────────────────────────────────────────────────────────────────
# F.1 / EF-7 -- paper_capital regression guard (post-E.7 setter consolidation)
# ─────────────────────────────────────────────────────────────────────────────

class StartupCheckFailed(ConfigError):
    """
    Raised by a startup check when the detected state is unsafe to run.

    Inherits from core.exceptions.ConfigError (E1, E3) so the top-level
    `except TradingSystemError` safety net in main.py catches startup /
    readiness failures with structured logging. 2026-04-26 audit EXC-2.
    """


def check_paper_capital_consistency(
    fund_manager,
    broker_adapter,
    is_paper: bool,
    logger,
    tolerance: float = 0.01,
) -> None:
    """
    Paper-mode regression guard: verify adapter.get_margins().net == fm.total.

    After E.7 wired broker_adapter.set_paper_capital() from AccountRow, both
    the adapter and the FundManager derive paper capital from the same source.
    They MUST match at startup. Any divergence indicates a regression (e.g.,
    main.py re-ordered, setter not called, adapter constructed after this
    check) -- catch it loudly before the event loop starts.

    No-op in live mode: adapter.get_margins() reads real broker margins which
    will not match fm.total exactly (intraday float, pending orders, etc.).

    Raises StartupCheckFailed on divergence. The G3 reconciler would catch
    this later via CapitalDriftDetected, but that path is minutes-to-cycles
    slow and emits a flood of noise during paper trial -- fail fast here.
    """
    if not is_paper:
        return

    margins = broker_adapter.get_margins()
    adapter_net = float(margins.net)
    fm_total = float(fund_manager.get_snapshot().total)
    delta = abs(adapter_net - fm_total)

    if delta > tolerance:
        logger.critical(
            "EF7_STARTUP_CHECK_DIVERGENCE adapter=%.4f fm=%.4f delta=%.4f "
            "tolerance=%.4f",
            adapter_net, fm_total, delta, tolerance,
        )
        raise StartupCheckFailed(
            f"paper_capital divergence at startup: adapter.get_margins().net="
            f"{adapter_net:.2f} vs fund_manager.total={fm_total:.2f} "
            f"(delta={delta:.2f} > tolerance={tolerance:.2f}). "
            f"Regression in the E.7 setter path -- inspect main.py ordering "
            f"around broker_adapter.set_paper_capital()."
        )

    logger.info(
        "check_paper_capital_consistency: OK adapter=%.2f fm=%.2f delta=%.4f",
        adapter_net, fm_total, delta,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SC11 -- Secret env var presence check
# ─────────────────────────────────────────────────────────────────────────────

def check_required_secrets(
    required_keys: List[str],
    logger,
) -> List[str]:
    """
    Verify all required environment variable keys are set (SC11).

    Uses os.environ.get(). Does NOT log actual secret values -- only key names.
    Returns list of missing key names (empty = all present).
    """
    missing: List[str] = []
    for key in required_keys:
        if not os.environ.get(key):
            logger.warning("check_required_secrets: missing env var %s", key)
            missing.append(key)
    return missing


# ─────────────────────────────────────────────────────────────────────────────
# FIX-096 -- DB permission check
# ─────────────────────────────────────────────────────────────────────────────

def check_db_permissions(
    db_path: str,
    logger,
) -> DbPermissionResult:
    """
    FIX-096: Verify DB file permissions before StateStore init.

    Checks read/write permissions on:
    - DB directory
    - .db file (if exists)
    - .db-wal file (if exists)
    - .db-shm file (if exists)

    Returns:
        DbPermissionResult with passed=False and chown commands if any fail.

    Edge cases:
    - .db-wal exists but .db missing → corrupted state error
    - DB directory not writable → blocker
    """
    db_file = Path(db_path)
    db_dir = db_file.parent
    errors = []
    chown_commands = []

    # Get current user for chown command
    import getpass
    try:
        current_user = getpass.getuser()
    except Exception:
        current_user = "USER"

    # Check 1: DB directory must be writable
    if not db_dir.exists():
        errors.append(f"DB directory does not exist: {db_dir}")
    elif not os.access(db_dir, os.R_OK | os.W_OK):
        errors.append(f"DB directory not readable/writable: {db_dir}")
        if os.name != 'nt':  # Unix/Linux only
            chown_commands.append(f"sudo chown {current_user}:{current_user} {db_dir}")

    # Check 2: .db file (if exists)
    if db_file.exists():
        if not os.access(db_file, os.R_OK | os.W_OK):
            errors.append(f"DB file not readable/writable: {db_file}")
            if os.name != 'nt':
                chown_commands.append(f"sudo chown {current_user}:{current_user} {db_file}")

    # Check 3: .db-wal file (if exists)
    wal_file = db_file.with_suffix('.db-wal')
    if wal_file.exists():
        # Corruption check: WAL exists but DB missing
        if not db_file.exists():
            errors.append(f"CORRUPTED STATE: {wal_file.name} exists but {db_file.name} missing")
            return DbPermissionResult(
                passed=False,
                db_path=str(db_file),
                db_dir=str(db_dir),
                errors=errors,
                chown_commands=chown_commands,
            )

        if not os.access(wal_file, os.R_OK | os.W_OK):
            errors.append(f"WAL file not readable/writable: {wal_file}")
            if os.name != 'nt':
                chown_commands.append(f"sudo chown {current_user}:{current_user} {wal_file}")

    # Check 4: .db-shm file (if exists)
    shm_file = db_file.with_suffix('.db-shm')
    if shm_file.exists():
        if not os.access(shm_file, os.R_OK | os.W_OK):
            errors.append(f"SHM file not readable/writable: {shm_file}")
            if os.name != 'nt':
                chown_commands.append(f"sudo chown {current_user}:{current_user} {shm_file}")

    passed = len(errors) == 0

    if not passed:
        for error in errors:
            logger.critical("check_db_permissions: %s", error)
        if chown_commands:
            logger.critical("check_db_permissions: FIX with these commands:")
            for cmd in chown_commands:
                logger.critical("  %s", cmd)
    else:
        logger.info("check_db_permissions: OK all files readable/writable")

    return DbPermissionResult(
        passed=passed,
        db_path=str(db_file),
        db_dir=str(db_dir),
        errors=errors,
        chown_commands=chown_commands,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FIX-099 -- Disk space check
# ─────────────────────────────────────────────────────────────────────────────

def check_disk_space(
    log_dir: Path,
    min_free_gb: float,
    logger,
) -> DiskSpaceResult:
    """
    FIX-099: Check free disk space before logger initialization.

    Prevents disk-full paralysis by blocking startup if insufficient space.
    Should be called BEFORE setup_logging() to ensure space for log writes.

    Args:
        log_dir: Directory where logs will be written
        min_free_gb: Minimum free space required (from system_config.yaml)
        logger: Logger instance (logs to stderr if file logging not yet active)

    Returns:
        DiskSpaceResult with passed=True if free >= min_free_gb, False otherwise.

    Critical behavior:
        If disk space < min_free_gb, logs CRITICAL to stderr (not file) and
        returns passed=False. Caller should exit with code 1.
    """
    import shutil

    try:
        # Ensure log_dir exists for disk_usage check
        log_dir.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(log_dir)
        free_gb = usage.free / (1024 ** 3)

        if free_gb < min_free_gb:
            error_msg = (
                f"Insufficient disk space. Free: {free_gb:.1f}GB — "
                f"minimum required: {min_free_gb}GB. Free disk space before starting."
            )
            # Log to stderr since file logging may not be initialized yet
            import sys
            sys.stderr.write(f"CRITICAL: {error_msg}\n")
            sys.stderr.flush()
            logger.critical("check_disk_space: %s", error_msg)

            return DiskSpaceResult(
                passed=False,
                free_gb=free_gb,
                min_required_gb=min_free_gb,
                error=error_msg,
            )

        logger.info(
            "check_disk_space: OK free=%.1fGB >= required=%.1fGB",
            free_gb, min_free_gb,
        )
        return DiskSpaceResult(
            passed=True,
            free_gb=free_gb,
            min_required_gb=min_free_gb,
        )

    except Exception as exc:
        error_msg = f"disk_usage check failed: {exc}"
        logger.error("check_disk_space: %s", error_msg)
        return DiskSpaceResult(
            passed=False,
            free_gb=0.0,
            min_required_gb=min_free_gb,
            error=error_msg,
        )


# ─────────────────────────────────────────────────────────────────────────────
# SC-FIX135 -- Strategy config schema validation
# ─────────────────────────────────────────────────────────────────────────────

def check_strategy_configs(config_dir: Path, logger) -> list[str]:
    """
    FIX-135 Item 47: Validate all strategy YAML files against StrategyConfig schema.
    Returns list of error strings (empty = all valid).
    """
    from strategies.loader import StrategyLoader
    from strategies.schema import StrategyConfig
    strategies_dir = config_dir / "strategies"
    errors: list[str] = []

    if not strategies_dir.exists():
        msg = f"strategies directory not found: {strategies_dir}"
        logger.critical("check_strategy_configs: %s", msg)
        errors.append(msg)
        return errors

    try:
        loader = StrategyLoader()
        loader.load_all_strategies(strategies_dir)
        logger.info(
            "check_strategy_configs: OK all strategies validated"
        )
    except Exception as exc:
        msg = f"strategy config validation failed: {exc}"
        logger.critical("check_strategy_configs: %s", msg)
        errors.append(msg)

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# FIX-136 Item 55 -- SDK version pin check
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SdkVersionResult:
    """Outcome of check_sdk_version()."""
    passed: bool
    expected: str
    installed: str
    error: str = ""


def check_sdk_version(
    requirements_path: Path,
    logger,
    package_name: str = "kiteconnect",
) -> SdkVersionResult:
    """
    FIX-136 Item 55: Verify installed SDK version matches requirements.txt pin.
    Returns FAIL if version mismatch or package not installed.
    """
    expected = ""
    try:
        if requirements_path.exists():
            for line in requirements_path.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or not stripped:
                    continue
                if stripped.split("#")[0].strip().startswith(f"{package_name}=="):
                    expected = stripped.split("==")[1].split("#")[0].strip()
                    break
    except Exception as exc:
        msg = f"Failed to read requirements.txt: {exc}"
        logger.warning("check_sdk_version: %s", msg)
        return SdkVersionResult(passed=False, expected="", installed="", error=msg)

    if not expected:
        logger.info("check_sdk_version: %s not pinned in requirements.txt", package_name)
        return SdkVersionResult(passed=True, expected="", installed="")

    try:
        installed = pkg_version(package_name)
    except Exception:
        msg = f"{package_name} not installed"
        logger.critical("check_sdk_version: %s", msg)
        return SdkVersionResult(passed=False, expected=expected, installed="", error=msg)

    if installed != expected:
        msg = f"{package_name} version mismatch: installed={installed} expected={expected}"
        logger.critical("check_sdk_version: %s", msg)
        return SdkVersionResult(passed=False, expected=expected, installed=installed, error=msg)

    logger.info("check_sdk_version: OK %s==%s", package_name, installed)
    return SdkVersionResult(passed=True, expected=expected, installed=installed)


# ─────────────────────────────────────────────────────────────────────────────
# FIX-136 Item 50 -- Holiday calendar validation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HolidayCalendarResult:
    """Outcome of check_holiday_calendar()."""
    passed: bool
    warnings: List[str]
    holiday_count: int
    year: int


def check_holiday_calendar(
    config_dir: Path,
    today_date: date,
    logger,
    min_holidays: int = 10,
) -> HolidayCalendarResult:
    """
    FIX-136 Item 50: Validate nse_holidays_YYYY.yaml for current year.

    Checks:
      1. File exists for current year
      2. At least min_holidays listed (NSE has ~14/year)
      3. All dates are in the correct year
      4. No duplicate dates
    Returns WARNING (not blocking) on any issue.
    """
    import yaml

    year = today_date.year
    fname = f"nse_holidays_{year}.yaml"
    fpath = config_dir / fname
    warnings: List[str] = []

    if not fpath.exists():
        msg = f"Holiday calendar missing: {fname}"
        logger.warning("check_holiday_calendar: %s", msg)
        warnings.append(msg)
        return HolidayCalendarResult(passed=False, warnings=warnings, holiday_count=0, year=year)

    try:
        with open(fpath, "r") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        msg = f"Holiday calendar parse error: {exc}"
        logger.warning("check_holiday_calendar: %s", msg)
        warnings.append(msg)
        return HolidayCalendarResult(passed=False, warnings=warnings, holiday_count=0, year=year)

    if not isinstance(data, dict) or "holidays" not in data:
        msg = f"Holiday calendar missing 'holidays' key"
        logger.warning("check_holiday_calendar: %s", msg)
        warnings.append(msg)
        return HolidayCalendarResult(passed=False, warnings=warnings, holiday_count=0, year=year)

    holidays = data["holidays"]
    if not isinstance(holidays, list):
        msg = f"Holiday calendar 'holidays' is not a list"
        logger.warning("check_holiday_calendar: %s", msg)
        warnings.append(msg)
        return HolidayCalendarResult(passed=False, warnings=warnings, holiday_count=0, year=year)

    count = len(holidays)
    if count < min_holidays:
        msg = f"Holiday calendar has {count} entries (expected >= {min_holidays})"
        logger.warning("check_holiday_calendar: %s", msg)
        warnings.append(msg)

    seen_dates: set = set()
    for entry in holidays:
        if isinstance(entry, dict):
            d = entry.get("date", "")
        else:
            d = str(entry)
        d_str = str(d)
        if d_str in seen_dates:
            msg = f"Duplicate holiday date: {d_str}"
            logger.warning("check_holiday_calendar: %s", msg)
            warnings.append(msg)
        seen_dates.add(d_str)
        try:
            parsed = date.fromisoformat(d_str)
            if parsed.year != year:
                msg = f"Holiday date {d_str} is not in year {year}"
                logger.warning("check_holiday_calendar: %s", msg)
                warnings.append(msg)
        except (ValueError, TypeError):
            msg = f"Invalid holiday date format: {d_str}"
            logger.warning("check_holiday_calendar: %s", msg)
            warnings.append(msg)

    passed = len(warnings) == 0
    if passed:
        logger.info("check_holiday_calendar: OK year=%d count=%d", year, count)

    return HolidayCalendarResult(passed=passed, warnings=warnings, holiday_count=count, year=year)


# ─────────────────────────────────────────────────────────────────────────────
# FIX-151 -- TEMP config value check
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TempConfigResult:
    """Outcome of check_temp_config_values()."""
    passed: bool
    temp_count: int
    files_with_temp: List[str]


def check_temp_config_values(
    config_dir: Path,
    logger,
) -> TempConfigResult:
    """
    FIX-151: Scan config YAMLs for '# TEMP' markers.
    Returns WARNING with count. Blocking only when capital > 25K
    (checked by caller in run_all_startup_checks).
    """
    temp_count = 0
    files_with_temp: List[str] = []

    yaml_files = list(config_dir.glob("*.yaml")) + list(config_dir.glob("strategies/*.yaml"))

    for fpath in yaml_files:
        try:
            content = fpath.read_text(encoding="utf-8")
            count = content.count("# TEMP")
            if count > 0:
                temp_count += count
                files_with_temp.append(f"{fpath.name}({count})")
        except OSError:
            continue

    if temp_count > 0:
        logger.warning(
            "check_temp_config: %d TEMP markers found in: %s",
            temp_count, ", ".join(files_with_temp),
        )
    else:
        logger.info("check_temp_config: OK no TEMP markers found")

    return TempConfigResult(
        passed=(temp_count == 0),
        temp_count=temp_count,
        files_with_temp=files_with_temp,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SC12 -- Aggregate startup check runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_startup_checks(
    state_store,
    kill_switch,
    time_authority,
    broker_adapter,
    market_windows,
    app_config,
    scan_webhook_map,
    chartink_scanners,
    http_fetcher_fn: Callable[[str, float], Tuple[Optional[int], str]],
    webhook_url: Optional[str],
    required_secrets: List[str],
    config_dir: Path,
    logger,
    instrument_cache=None,             # BL-20: optional; skip check if None
    min_instrument_rows: int = 1000,   # BL-20
) -> StartupReport:
    """
    Run all startup checks and return an aggregate StartupReport (SC12).

    Checks run in order:
      1. Config file presence (blocking)
      2. Required secrets (blocking)
      3. Startup scenario detection (HALT is blocking)
      4. Clock skew (blocking)
      5. Config hash diff (warning only)
      6. Market holiday (warning only)
      7. Scanner connectivity (warning only; skipped on COLD scenario)
      8. Webhook endpoint (skipped if webhook_url is None)
      9. Instrument cache size (blocking; skipped if instrument_cache is None
         — caller is expected to have handled load failure separately) (BL-20)

    Collects ALL results before returning -- does NOT abort on first failure.
    Caller reads ok=False and blocking_failures to decide terminal action.
    """
    blocking_failures: List[str] = []
    warnings:          List[str] = []
    today_date = time_authority.now_ist().date()

    # 1. Config files
    missing_config = check_config_files_present(config_dir, logger)
    if missing_config:
        blocking_failures.append("missing_config_files")

    # 2. Required secrets
    missing_secrets = check_required_secrets(required_secrets, logger)
    if missing_secrets:
        blocking_failures.append("missing_secrets")

    # 3. Scenario detection
    scenario_details = detect_startup_scenario(
        state_store, kill_switch, today_date, logger
    )
    if scenario_details.scenario == StartupScenario.HALT:
        blocking_failures.append("halt_requires_resume")

    # 4. Clock skew (HIGH #5: use config tolerance, not hardcoded 30s)
    clock_result = check_clock_skew(
        time_authority,
        broker_adapter,
        logger,
        tolerance_sec=app_config.system.clock.startup_max_skew_sec,
    )
    if not clock_result.passed:
        blocking_failures.append("clock_skew")

    # 5. Config hash diff
    config_hash_result = check_config_hash(state_store, app_config, logger)
    if config_hash_result.changed:
        warnings.append("config_hash_changed")

    # 6. Market holiday
    is_holiday = check_market_holiday_today(market_windows, today_date, logger)
    if is_holiday:
        warnings.append("market_holiday")

    # 7. Scanner connectivity -- skipped on COLD (no prior session to compare)
    scanner_result: Optional[ScannerPreflightResult] = None
    if scenario_details.scenario != StartupScenario.COLD:
        # FIX-D: Get scanner check delay from config (defaults to 0 if not present)
        scanner_delay_sec = getattr(app_config.system, "scanner_check_delay_sec", 0.0)
        scanner_result = check_scanner_connectivity(
            scan_webhook_map, chartink_scanners, http_fetcher_fn, logger,
            delay_sec=scanner_delay_sec
        )
        if not scanner_result.all_reachable:
            warnings.append("scanner_unreachable")

    # 8. Webhook endpoint (optional; caller provides url after Flask starts)
    webhook_result: Optional[WebhookEndpointResult] = None
    if webhook_url:
        webhook_result = check_webhook_endpoint(
            webhook_url, http_fetcher_fn, logger
        )

    # 9. Instrument cache size (BL-20)
    instrument_cache_count: Optional[int] = None
    if instrument_cache is not None:
        passed, instrument_cache_count = check_instrument_cache_size(
            instrument_cache, logger, min_rows=min_instrument_rows
        )
        if not passed:
            blocking_failures.append("instrument_cache_too_small")

    # 10. Strategy config validation (FIX-135 Item 47)
    strategy_errors = check_strategy_configs(config_dir, logger)
    if strategy_errors:
        blocking_failures.append("invalid_strategy_configs")

    # 11. Holiday calendar validation (FIX-136 Item 50)
    holiday_cal = check_holiday_calendar(config_dir, today_date, logger)
    if not holiday_cal.passed:
        warnings.append("holiday_calendar_issues")

    # 12. SDK version pin check (FIX-136 Item 55)
    sdk_result = check_sdk_version(config_dir.parent / "requirements.txt", logger)
    if not sdk_result.passed:
        blocking_failures.append("sdk_version_mismatch")

    # 13. NTP clock sync (FIX-129 Item 27)
    # warn_sec=2, block_sec=5 per spec. Best-effort: NTP unreachable → skipped, not blocking.
    ntp_result = check_ntp_sync(
        logger=logger,
        ntp_host="pool.ntp.org",
        warn_sec=2.0,
        block_sec=5.0,
    )
    if not ntp_result.passed and not ntp_result.skipped:
        blocking_failures.append("ntp_clock_skew")
    elif ntp_result.drift_sec >= ntp_result.warn_sec and not ntp_result.skipped:
        warnings.append("ntp_clock_drift_warning")

    # 14. TEMP config values (FIX-151)
    temp_result = check_temp_config_values(config_dir, logger)
    if not temp_result.passed:
        warnings.append(f"temp_config_values({temp_result.temp_count})")

    ok = len(blocking_failures) == 0

    if ok:
        logger.info(
            "run_all_startup_checks: OK scenario=%s warnings=%s",
            scenario_details.scenario.value, warnings,
        )
    else:
        logger.warning(
            "run_all_startup_checks: FAILED blocking=%s", blocking_failures
        )

    return StartupReport(
        ok=ok,
        scenario=scenario_details.scenario,
        blocking_failures=blocking_failures,
        warnings=warnings,
        scenario_details=scenario_details,
        clock=clock_result,
        config_hash=config_hash_result,
        scanners=scanner_result,
        webhook=webhook_result,
        market_holiday=is_holiday,
        missing_secrets=missing_secrets,
        missing_config_files=missing_config,
        instrument_cache_count=instrument_cache_count,  # BL-20
        ntp=ntp_result,  # FIX-129 Item 27
        temp_config=temp_result,  # FIX-151
    )
