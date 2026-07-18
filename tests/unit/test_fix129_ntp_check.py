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

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.startup_checks import check_ntp_sync, NtpCheckResult


def _log():
    return logging.getLogger("test_fix129_ntp")


# ---------------------------------------------------------------------------
# Why the injected offset must be compared with a tolerance, not with `>=`.
#
# check_ntp_sync (utils/startup_checks.py:534-548) does:
#       ntp_utc   = fetcher(host)     # our stub: time.time() at T0, + offset
#       local_utc = time.time()       # at T1, strictly after T0
#       drift     = abs(local_utc - ntp_utc)
#
# so the measured drift is (offset - elapsed), where elapsed = T1 - T0 > 0.
# It is therefore ALWAYS <= the injected offset, and `drift >= offset` can only
# hold when both error terms happen to vanish. Two terms, measured on this tree:
#
#   1. float64 representation. At epoch ~1.784e9 the value sits in [2^30, 2^31),
#      so one ULP is 2^-22 = 2.384e-07. `t + offset` cannot generally be
#      represented exactly, and the observed CI failure was a deficit of exactly
#      one ULP (assert 0.9999997615814209 >= 1.0).
#   2. real elapsed time between the two clock reads. Measured over 400k
#      samples: p99.9 = 7.2e-07, max = 9.1e-06 -- ~38x term 1, so the PHYSICAL
#      term dominates, not the float one.
#
# Tolerance: 50 ms. That is ~2e5x the ULP and ~5.5e3x the measured worst case,
# sized not by either error term but by the Windows scheduler quantum (~15.6 ms)
# -- one preemption landing between those two adjacent clock reads dwarfs
# everything float-related, and that is the only realistic way this flakes.
# It stays 20x BELOW the 1.0 s margin separating each injected offset from the
# nearest decision boundary (1.0 vs warn 2.0; 3.0 vs warn 2.0 / block 5.0), so
# it cannot mask a threshold misclassification -- which is not asserted here but
# PROVEN by the two guard tests at the bottom of this class:
#   test_the_tolerance_cannot_mask_a_threshold_misclassification  (bounds the tol)
#   test_a_drift_just_over_block_is_still_caught                  (bounds the decision)
#
# Measured flip rate of the OLD `>=` form, 100k trials each on this tree:
#   `drift_sec >= 1.0`  failed 17,940/100,000 (17.94%)
#   `drift_sec >= 3.0`  failed 18,324/100,000 (18.32%)
#   this form           failed      0/100,000  (worst deficit 1.43e-06)
# ---------------------------------------------------------------------------
_CLOCK_TOL = 0.05


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
        # was `>= 1.0`: a per-run coin flip, see _CLOCK_TOL above.
        assert result.drift_sec == pytest.approx(1.0, abs=_CLOCK_TOL)
        assert result.drift_sec < 2.0, "must stay below warn_sec to mean anything"
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
        # was `>= 3.0`: a per-run coin flip, see _CLOCK_TOL above.
        assert result.drift_sec == pytest.approx(3.0, abs=_CLOCK_TOL)
        assert 2.0 < result.drift_sec < 5.0, "must sit strictly between warn and block"
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

    # -- guards on the tolerance itself (added with the _CLOCK_TOL fix) --------

    def test_the_tolerance_cannot_mask_a_threshold_misclassification(self) -> None:
        """A tolerance wide enough to hide a real drift breach is worse than the flake.

        The smallest gap between an injected offset and a decision boundary
        anywhere in this file is 1.0 s (offset 1.0 vs warn 2.0; offset 3.0 vs
        warn 2.0 and block 5.0). _CLOCK_TOL must stay far under it.
        """
        smallest_margin_to_a_boundary = 1.0
        assert _CLOCK_TOL < smallest_margin_to_a_boundary / 10.0, (
            f"_CLOCK_TOL={_CLOCK_TOL} is within 10x of the "
            f"{smallest_margin_to_a_boundary}s margin to the nearest threshold — "
            "it could hide a misclassification"
        )
        print(f"  OK: tolerance {_CLOCK_TOL}s is "
              f"{smallest_margin_to_a_boundary / _CLOCK_TOL:.0f}x below the nearest boundary")

    def test_a_drift_just_over_block_is_still_caught(self) -> None:
        """Boundary sharpness: the tolerance must not blur the block decision.

        block_sec + 2*tol must still block. If widening the tolerance ever made
        this pass, the tolerance would have swallowed a real breach.
        """
        over_block = 5.0 + 2 * _CLOCK_TOL
        result = check_ntp_sync(
            logger=_log(),
            ntp_fetcher_fn=_make_fetcher(over_block),
            warn_sec=2.0,
            block_sec=5.0,
        )
        assert result.passed is False, (
            f"drift {result.drift_sec} exceeds block_sec=5.0 and MUST block"
        )
        assert result.drift_sec == pytest.approx(over_block, abs=_CLOCK_TOL)
        print(f"  OK: drift {over_block}s > block 5s still blocks")

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
