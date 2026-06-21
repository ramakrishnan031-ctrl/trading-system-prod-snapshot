"""
scripts/preflight/checks/engine.py -- Group 8 (Engine readiness), Phase B.

Phase B runs at 09:14, by which time token-watcher has started the app. Pre-flight
is a SEPARATE process, so it cannot introspect the app's in-memory engine objects
directly -- it attests readiness via the app's own surfaces:
  * trading-system.service active (process up)               -> services group
  * GET :8080/health  -> 200/healthy (db + token + kill_switch, FIX-188)
  * GET :8080/metrics -> 200 (signal pipeline queryable; surfaces signals/positions)
  * capital_snapshot.cash_floor sane (fund_manager balance, no NaN -- crash-test FIX)

Per-engine introspection (signal_processor / risk_engine / live_feed / shadow_tracker
threads individually) would need an app-side /readyz; not built -- /health + /metrics
are the externally-observable proxy. App DOWN at Phase B is CRITICAL (token-watcher's
job, not pre-flight's -- alert-only, never auto-started here).
"""
from __future__ import annotations

import json
import math
import sqlite3
import urllib.error
import urllib.request

from scripts.preflight.base import Check, CheckContext, CheckResult, Criticality

HEALTH_URL = "http://127.0.0.1:8080/health"
METRICS_URL = "http://127.0.0.1:8080/metrics"


def _http_get_json(url: str, timeout: float = 5.0) -> tuple[int, dict]:
    """(status_code, parsed_json). status 0 = unreachable. Monkeypatched in tests."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:           # 503 degraded still has a body
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}
    except Exception as exc:
        return 0, {"error": str(exc)}


class AppHealthCheck(Check):
    name = "app_health"
    group = "Engine"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 800

    def run(self, ctx: CheckContext) -> CheckResult:
        status, body = _http_get_json(HEALTH_URL)
        if status == 0:
            return self._failed(f"/health unreachable — app down? ({body.get('error', '')})")
        if status == 200 and body.get("status") == "healthy":
            return self._passed(f"/health healthy (uptime {body.get('uptime_seconds', '?')}s)")
        bad = [k for k, v in (body.get("checks") or {}).items() if not (isinstance(v, dict) and v.get("ok"))]
        return self._failed(f"/health degraded (HTTP {status}): failing {bad or '?'}")


class AppMetricsCheck(Check):
    name = "app_metrics"
    group = "Engine"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 800

    def run(self, ctx: CheckContext) -> CheckResult:
        status, body = _http_get_json(METRICS_URL)
        if status != 200:
            return self._failed(f"/metrics not 200 (HTTP {status}) — signal pipeline not queryable")
        return self._passed(
            f"/metrics ok (signals_received={body.get('signals_received', 0)}, "
            f"open_positions={body.get('open_positions', 0)})",
            signals_received=body.get("signals_received", 0),
            open_positions=body.get("open_positions", 0))


class FundManagerBalanceCheck(Check):
    """fund_manager readiness via capital_snapshot.cash_floor (the NaN-balance guard
    from crash testing). NaN / None / <= 0 => CRITICAL."""

    name = "fund_manager_balance"
    group = "Engine"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 20

    def run(self, ctx: CheckContext) -> CheckResult:
        if not ctx.db_path.exists():
            return self._failed(f"database not found: {ctx.db_path}")
        try:
            conn = sqlite3.connect(str(ctx.db_path))
            try:
                row = conn.execute(
                    "SELECT cash_floor, margin_used FROM capital_snapshot WHERE id = 1"
                ).fetchone()
            finally:
                conn.close()
        except Exception as exc:
            return self._warn(f"capital_snapshot unreadable: {exc}")
        if not row:
            return self._warn("no capital_snapshot row yet (app may still be initialising)")
        cash_floor = row[0]
        if cash_floor is None or (isinstance(cash_floor, float) and math.isnan(cash_floor)):
            return self._failed(f"fund_manager balance is NaN/None (cash_floor={cash_floor})")
        if float(cash_floor) <= 0:
            return self._failed(f"fund_manager cash_floor <= 0 ({cash_floor})")
        return self._passed(f"fund_manager balance OK (cash_floor=₹{float(cash_floor):,.0f})",
                            cash_floor=float(cash_floor))


class NtpStrictCheck(Check):
    """Phase-B NTP: by 09:14 the app is placing broker calls, so drift is no longer
    cosmetic. Escalates the Phase-A WARN band to CRITICAL (non-fix B, 21-Jun)."""

    name = "vm_ntp_strict"
    group = "Engine"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 5000
    WARN_SEC = 0.5
    FAIL_SEC = 2.0

    def run(self, ctx: CheckContext) -> CheckResult:
        from scripts.preflight.checks.vm_health import _ntp_skew_seconds
        skew = _ntp_skew_seconds()
        if skew is None:
            return self._skipped("clock check skipped (ntplib/network unavailable)")
        if skew > self.FAIL_SEC:
            return self._failed(f"clock skew {skew:.2f}s (> {self.FAIL_SEC}s) — broker calls reject drift",
                                skew_sec=round(skew, 3))
        if skew > self.WARN_SEC:
            return self._warn(f"clock skew {skew:.2f}s (> {self.WARN_SEC}s)", skew_sec=round(skew, 3))
        return self._passed(f"clock OK: skew {skew:.2f}s", skew_sec=round(skew, 3))


CHECKS = [
    AppHealthCheck(),
    AppMetricsCheck(),
    FundManagerBalanceCheck(),
    NtpStrictCheck(),
]
