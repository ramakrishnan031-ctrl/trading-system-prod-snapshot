"""
tests/unit/test_fix129_ntp_check.py

Tests for FIX-129 Item 27: NTP clock sync check in startup_checks.
  - drift within warn threshold → passed=True
  - drift between warn and block → passed=True, warns
  - drift >= block threshold → passed=False (blocking)
  - NTP fetch failure → skipped=True, passed=True (best-effort, don't block)
  - check_ntp_sync uses injected ntp_fetcher_fn for testability
"""
from __future__ import annotations

import logging
import sys
import time as _time_mod
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.startup_checks import check_ntp_sync, NtpCheckResult


def _log():
    return logging.getLogger("test_fix129_ntp")


def _make_fetcher(offset_sec: float):
    """Return a fetcher that returns (local_utc + offset_sec) to simulate drift."""
    def _fetcher(host: str) -> float:
        return _time_mod.time() + offset_sec
    return _fetcher


def _failing_fetcher(host: str) -> float:
    raise OSError("Network unreachable")


class TestNtpCheck:

    def test_no_drift_passes(self) -> None:
        """Drift ~0s → passed=True, skipped=False."""
        result = check_ntp_sync(
            logger=_log(),
            ntp_fetcher_fn=_make_fetcher(0.0),
            warn_sec=2.0,
            block_sec=5.0,
        )
        assert result.passed is True
        assert result.skipped is False
        assert result.drift_sec < 1.0
        print("  OK: no drift → passed=True")

    def test_drift_within_warn_passes(self) -> None:
        """Drift 1s < warn_sec=2s → passed=True."""
        result = check_ntp_sync(
            logger=_log(),
            ntp_fetcher_fn=_make_fetcher(1.0),
            warn_sec=2.0,
            block_sec=5.0,
        )
        assert result.passed is True
        assert result.drift_sec >= 1.0
        print("  OK: drift 1s < warn 2s → passed=True")

    def test_drift_between_warn_and_block(self) -> None:
        """Drift 3s: warn_sec=2s, block_sec=5s → passed=True (warn only)."""
        result = check_ntp_sync(
            logger=_log(),
            ntp_fetcher_fn=_make_fetcher(3.0),
            warn_sec=2.0,
            block_sec=5.0,
        )
        assert result.passed is True, "between warn and block → not blocking"
        assert result.drift_sec >= 3.0
        print("  OK: drift 3s between warn/block → passed=True (warning only)")

    def test_drift_exceeds_block_fails(self) -> None:
        """Drift 6s >= block_sec=5s → passed=False (blocking failure)."""
        result = check_ntp_sync(
            logger=_log(),
            ntp_fetcher_fn=_make_fetcher(6.0),
            warn_sec=2.0,
            block_sec=5.0,
        )
        assert result.passed is False
        assert result.drift_sec >= 5.0
        assert result.error  # error message should be populated
        print("  OK: drift 6s >= block 5s → passed=False (blocking)")

    def test_fetch_failure_is_skipped_not_blocking(self) -> None:
        """NTP fetch failure → skipped=True, passed=True (best-effort)."""
        result = check_ntp_sync(
            logger=_log(),
            ntp_fetcher_fn=_failing_fetcher,
            warn_sec=2.0,
            block_sec=5.0,
        )
        assert result.skipped is True
        assert result.passed is True  # don't block on NTP failure
        assert result.error  # error message populated
        assert result.drift_sec == 0.0
        print("  OK: NTP fetch failure → skipped=True, passed=True (best-effort)")

    def test_result_contains_ntp_host(self) -> None:
        """NtpCheckResult contains the host that was queried."""
        result = check_ntp_sync(
            logger=_log(),
            ntp_host="time.cloudflare.com",
            ntp_fetcher_fn=_make_fetcher(0.0),
            warn_sec=2.0,
            block_sec=5.0,
        )
        assert result.ntp_host == "time.cloudflare.com"
        print("  OK: result.ntp_host matches queried host")

    def test_thresholds_stored_in_result(self) -> None:
        """NtpCheckResult stores warn_sec and block_sec for caller inspection."""
        result = check_ntp_sync(
            logger=_log(),
            ntp_fetcher_fn=_make_fetcher(0.0),
            warn_sec=3.0,
            block_sec=10.0,
        )
        assert result.warn_sec == 3.0
        assert result.block_sec == 10.0
        print("  OK: warn_sec and block_sec stored in result")


if __name__ == "__main__":
    tests = [
        TestNtpCheck().test_no_drift_passes,
        TestNtpCheck().test_drift_within_warn_passes,
        TestNtpCheck().test_drift_between_warn_and_block,
        TestNtpCheck().test_drift_exceeds_block_fails,
        TestNtpCheck().test_fetch_failure_is_skipped_not_blocking,
        TestNtpCheck().test_result_contains_ntp_host,
        TestNtpCheck().test_thresholds_stored_in_result,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
