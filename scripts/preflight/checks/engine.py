"""
scripts/preflight/checks/engine.py -- Group 8 (Engine readiness), Phase B.

Phase B runs at 09:14, by which time token-watcher has started the app. Pre-flight
is a SEPARATE process, so it cannot introspect the app's in-memory engine objects
directly -- it attests readiness via the app's own surfaces:
  * trading-system.service active (process up)               -> services group
  * GET :8080/health  -> 200/healthy (db + token + kill_switch, FIX-188)
  * GET :8080/metrics -> 200 (signal pipeline queryable; surfaces signals/positions)
  * fund_manager free cash sane (no NaN -- crash-test FIX). 25-Jul-2026: sourced
    live from fm_ledger + trades; capital_snapshot has 0 rows and is retired.
  * capital deployment % (alert-only, WARN; observability, never a gate)

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


# ── 25-Jul-2026: capital_snapshot is empty by design; read capital where it lives ──
# Three readers (this file, healthcheck_server.py, the GUI db_reader) pointed at
# capital_snapshot, a table nothing has written since the 3-balance model was
# retired -- 0 rows in production. So FundManagerBalanceCheck had only ever
# emitted "no capital_snapshot row yet (app may still be initialising)", on every
# single run, forever: a permanent WARN that looked like a transient one.
#
# Preflight is a SEPARATE process and must NOT construct a StateStore -- schema
# migrations run on DB-OPEN (P11, 14-Jul) and only main.py's boot may migrate.
# Hence raw sqlite3 here, mirroring the definitions used by
# core.state_store.get_day_opening_capital() (first INIT row of the day, ORDER BY
# ts) and by schema.sql's own description of the 3 balances.
_OPEN_POSITION_STATES = ("OPEN", "PARTIAL", "EXITING")   # margin actually deployed
_PENDING_STATES = ("PENDING_FILL",)                      # reserved, not yet filled


def _sum_margin(conn, states: tuple) -> float:
    placeholders = ",".join("?" * len(states))
    row = conn.execute(
        f"SELECT COALESCE(SUM(margin_reserved), 0.0) FROM trades WHERE status IN ({placeholders})",
        states,
    ).fetchone()
    return float(row[0] or 0.0)


def _capital_now(db_path, as_of_date) -> dict:
    """Live capital picture, read-only.

    opening         -- the day's FIRST INIT ledger row (restart-safe: INIT is one
                       row per PROCESS START, so a mid-day restart adds a second
                       and any SUM would double -- see the 25-Jul db_reader fix).
    margin_used     -- margin on OPEN positions   (schema.sql: "over open positions")
    margin_reserved -- margin on PENDING orders   (schema.sql: "not yet filled")

    Raises on a DB error; every caller wraps this, because preflight must degrade
    to a WARN and never let an exception escape (a crash here costs a trading day).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT balance_after FROM fm_ledger WHERE date = ? AND entry_type = 'INIT' "
            "ORDER BY ts ASC LIMIT 1",
            (as_of_date.isoformat(),),
        ).fetchone()
        return {
            # `has_init` is NOT the same question as `opening is None`. SQLite has
            # no NaN: a NaN written to balance_after comes back as NULL, so an INIT
            # row with a broken balance is indistinguishable from a NULL one — and
            # BOTH must FAIL, because that is exactly the NaN-balance guard this
            # check was built for in crash testing. Only the absence of the row is
            # a WARN. Collapsing the two would silently retire the guard.
            "has_init": row is not None,
            "opening": row[0] if row else None,
            "margin_used": _sum_margin(conn, _OPEN_POSITION_STATES),
            "margin_reserved": _sum_margin(conn, _PENDING_STATES),
        }
    finally:
        conn.close()


class FundManagerBalanceCheck(Check):
    """fund_manager readiness via free cash (the NaN-balance guard from crash testing).
    NaN / None / <= 0 => CRITICAL.

    ⚠️ MEANING UNCHANGED (25-Jul-2026). This still asks exactly one question — "is
    the fund manager's free cash sane?" — with the same three FAIL conditions. Only
    the SOURCE moved, from the permanently-empty capital_snapshot.cash_floor to the
    live derivation the 3-balance model defines: opening capital minus the margin
    deployed on open positions.

    The deployment PERCENTAGE deliberately does NOT live here. It is its own
    alert-only check below, so this one keeps a single meaning and a CRITICAL here
    still means what it always meant.
    """

    name = "fund_manager_balance"
    group = "Engine"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 20

    def run(self, ctx: CheckContext) -> CheckResult:
        if not ctx.db_path.exists():
            return self._failed(f"database not found: {ctx.db_path}")
        try:
            cap = _capital_now(ctx.db_path, ctx.as_of_date)
        except Exception as exc:
            return self._warn(f"capital state unreadable: {exc}")

        opening = cap["opening"]
        if not cap["has_init"]:
            # Genuinely transient, unlike the old permanent WARN: preflight B runs
            # at 09:14 and the INIT row is written ~08:15, so this means the app has
            # not seeded today at all.
            return self._warn(
                f"no INIT ledger row for {ctx.as_of_date.isoformat()} yet "
                "(app may still be initialising)")
        # The row EXISTS but carries no usable balance -> the crash-test NaN guard.
        if opening is None or (isinstance(opening, float) and math.isnan(opening)):
            return self._failed(f"fund_manager balance is NaN/None (opening={opening})")

        cash_floor = float(opening) - cap["margin_used"]
        if math.isnan(cash_floor):
            return self._failed(f"fund_manager balance is NaN (cash_floor={cash_floor})")
        if cash_floor <= 0:
            return self._failed(
                f"fund_manager cash_floor <= 0 ({cash_floor:,.2f}; opening "
                f"{float(opening):,.2f} - margin_used {cap['margin_used']:,.2f})")
        return self._passed(f"fund_manager balance OK (cash_floor=₹{cash_floor:,.0f})",
                            cash_floor=round(cash_floor, 2),
                            opening_capital=round(float(opening), 2),
                            margin_used=round(cap["margin_used"], 2))


class CapitalDeploymentCheck(Check):
    """How much of today's capital is deployed: margin_used / total_capital.

    NEW and SEPARATE on purpose (25-Jul-2026). It would have been cheaper to hang
    this off FundManagerBalanceCheck, but that check answers "is free cash sane?"
    and a CRITICAL from it must keep meaning exactly that. Overloading it would
    make one signal carry two questions, and the deployment % is an observability
    number, not a readiness gate.

    Hence ALERT-ONLY: criticality WARN, so an unusual deployment level is reported
    and can never escalate the run to CRITICAL.

    ⭐ DEFINITION (new, dated): capital_deployed_pct = margin_used / total_capital,
    where total_capital is the day's opening capital (first INIT row) and
    margin_used is margin on OPEN positions. The previous definition, in
    healthcheck_server.py, was margin_used / cash_floor -- a ratio against the
    REMAINING cash rather than the total, which grows without bound as the book
    fills. It never emitted a value in production (capital_snapshot has 0 rows),
    so no series and no consumer holds the old meaning.
    """

    name = "capital_deployment"
    group = "Engine"
    criticality = Criticality.WARN
    expected_duration_ms = 20

    def run(self, ctx: CheckContext) -> CheckResult:
        if not ctx.db_path.exists():
            return self._warn(f"database not found: {ctx.db_path}")
        try:
            cap = _capital_now(ctx.db_path, ctx.as_of_date)
        except Exception as exc:
            return self._warn(f"capital state unreadable: {exc}")

        opening, used = cap["opening"], cap["margin_used"]
        # Div-0 guard: say WHY, never emit a silent 0.0 that reads as "nothing
        # deployed" when the truth is "we could not tell".
        if opening is None:
            reason = ("no INIT ledger row for " + ctx.as_of_date.isoformat()
                      if not cap["has_init"] else "the INIT row has a NULL/NaN balance")
            return self._warn(
                f"capital_deployed_pct unavailable: {reason}",
                margin_used=round(used, 2))
        opening = float(opening)
        if math.isnan(opening) or opening <= 0:
            return self._warn(
                f"capital_deployed_pct unavailable: total_capital is {opening} "
                "(non-positive or NaN)", margin_used=round(used, 2))

        pct = used / opening * 100.0
        return self._passed(
            f"capital deployed {pct:.1f}% (margin_used ₹{used:,.0f} / total ₹{opening:,.0f}; "
            f"pending ₹{cap['margin_reserved']:,.0f})",
            capital_deployed_pct=round(pct, 2),
            margin_used=round(used, 2),
            margin_reserved=round(cap["margin_reserved"], 2),
            total_capital=round(opening, 2))


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
    CapitalDeploymentCheck(),     # 25-Jul-2026: alert-only (WARN), never a gate
    NtpStrictCheck(),
]
