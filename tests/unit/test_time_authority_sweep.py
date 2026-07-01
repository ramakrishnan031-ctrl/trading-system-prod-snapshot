"""
tests/unit/test_time_authority_sweep.py

Regression guards for H-17 / E.1 — every former datetime.now() bypass now
flows through core.time_authority.now_ist().

Covers the call sites replaced in E.1 (the reports/daily_review.py:_now_ist_date
site was retired with that module in the Phase-C report cutover, 01-Jul-2026):
  - orders/shadow_tracker.py:_parse_ts (empty branch)
  - orders/shadow_tracker.py:_parse_ts (invalid branch)
  - orders/order_reconciler.py:_now_ist
  - capital/risk_engine.py:approve (today = now_ist().date())
  - alerts/critical.py:write_critical_sentinel

The two explicitly SKIPPED sites (state_store.py:64, utils/startup_checks.py:627)
carry inline H-17 SKIP comments; a future maintainer looking to fix those must
read the comment before undoing the exemption. No test here enforces the skip.

Run: python -m pytest tests/unit/test_time_authority_sweep.py -v
Or:  python tests/unit/test_time_authority_sweep.py
"""
from __future__ import annotations

import inspect
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import alerts.critical as alerts_critical
import capital.risk_engine as risk_engine_mod
import core.time_authority as time_authority_mod
import orders.order_reconciler as reconciler_mod
import orders.shadow_tracker as shadow_tracker_mod

_IST = timezone(timedelta(hours=5, minutes=30), name="IST")
_FIXED_TS = datetime(2026, 4, 20, 9, 30, 0, tzinfo=_IST)


# ─────────────────────────────────────────────────────────────────────────────
# shadow_tracker._parse_ts — two branches
# ─────────────────────────────────────────────────────────────────────────────

def test_shadow_tracker_parse_ts_empty_uses_now_ist() -> None:
    """Empty ts_str → _parse_ts falls through to now_ist(), returns naive."""
    with patch.object(shadow_tracker_mod, "now_ist", return_value=_FIXED_TS) as mock_ni:
        result = shadow_tracker_mod._parse_ts("")
    assert mock_ni.call_count == 1
    assert result == _FIXED_TS.replace(tzinfo=None)
    assert result.tzinfo is None
    print("  OK H-17: shadow_tracker._parse_ts empty branch uses now_ist (naive)")


def test_shadow_tracker_parse_ts_invalid_uses_now_ist() -> None:
    """Un-parseable ts_str → _parse_ts falls through to now_ist()."""
    fixed = _FIXED_TS
    with patch.object(shadow_tracker_mod, "now_ist", return_value=fixed) as mock_ni:
        result = shadow_tracker_mod._parse_ts("not-an-iso-timestamp")
    assert mock_ni.call_count == 1
    # Invalid branch returns naive-IST (tzinfo stripped)
    assert result == fixed.replace(tzinfo=None)
    print("  OK H-17: shadow_tracker._parse_ts invalid branch uses now_ist")


# ─────────────────────────────────────────────────────────────────────────────
# order_reconciler._now_ist
# ─────────────────────────────────────────────────────────────────────────────

def test_order_reconciler_now_ist_uses_time_authority() -> None:
    """_now_ist() returns now_ist().isoformat(), not datetime.now()."""
    # _now_ist is a tiny instance method; construct OrderReconciler the cheap
    # way (bypass __init__ via __new__) because we only exercise one method.
    reconciler = reconciler_mod.OrderReconciler.__new__(reconciler_mod.OrderReconciler)
    with patch.object(reconciler_mod, "now_ist", return_value=_FIXED_TS) as mock_ni:
        result = reconciler._now_ist()
    assert mock_ni.call_count == 1
    assert result == _FIXED_TS.isoformat()
    print("  OK H-17: order_reconciler._now_ist uses time_authority")


# ─────────────────────────────────────────────────────────────────────────────
# risk_engine.approve — source-level + import-binding regression guard
# ─────────────────────────────────────────────────────────────────────────────

def test_risk_engine_approve_sources_date_from_now_ist() -> None:
    """risk_engine.approve reads 'today' via now_ist(), not datetime.now(_IST)."""
    # 1. Import binding: now_ist imported from core.time_authority.
    assert risk_engine_mod.now_ist is time_authority_mod.now_ist, (
        "capital.risk_engine.now_ist must be the same symbol as "
        "core.time_authority.now_ist"
    )
    # 2. Source guard: approve() uses now_ist(), not datetime.now(...).
    src = inspect.getsource(risk_engine_mod.RiskEngine.approve)
    assert "now_ist()" in src, "approve() must call now_ist()"
    assert "datetime.now(" not in src, (
        "approve() must not call datetime.now() directly (H-17 regression)"
    )
    print("  OK H-17: risk_engine.approve sources date from now_ist()")


# ─────────────────────────────────────────────────────────────────────────────
# alerts/critical.write_critical_sentinel
# ─────────────────────────────────────────────────────────────────────────────

def test_alerts_critical_sentinel_uses_time_authority() -> None:
    """write_critical_sentinel stamps the .flag file using now_ist(), not datetime.now()."""
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(alerts_critical, "now_ist", return_value=_FIXED_TS) as mock_ni:
            flag_path = alerts_critical.write_critical_sentinel(
                title="test",
                body="body",
                source_module="test_module",
                context={},
                sentinel_dir=tmp,
            )
        assert mock_ni.call_count == 1
        # Filename encodes the timestamp via strftime("%Y%m%d_%H%M%S") after a fixed prefix
        assert "20260420_093000_" in flag_path.name, (
            f"sentinel file must embed patched timestamp; got {flag_path.name}"
        )
    print("  OK H-17: alerts.critical.write_critical_sentinel uses now_ist")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_shadow_tracker_parse_ts_empty_uses_now_ist,
        test_shadow_tracker_parse_ts_invalid_uses_now_ist,
        test_order_reconciler_now_ist_uses_time_authority,
        test_risk_engine_approve_sources_date_from_now_ist,
        test_alerts_critical_sentinel_uses_time_authority,
    ]
    print("=" * 70)
    print("time_authority_sweep.py -- H-17 / E.1 Test Suite")
    print("=" * 70)
    failed = []
    for t in tests:
        print(f"\n-> {t.__name__}")
        try:
            t()
        except Exception as e:
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"  FAIL: {e}")
    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)}")
        for n, err in failed:
            print(f"  - {n}: {err}")
        return 1
    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
