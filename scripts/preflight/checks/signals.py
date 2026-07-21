"""
scripts/preflight/checks/signals.py -- Group 10 (Signal warmup), Phase C.

Phase C is a PASSIVE watch 09:15-09:20 (the orchestrator re-samples these checks
until ~09:20). No synthetic signal is injected (the real webhook endpoint has real
consequences -- Rama's correction). It reads the app's surfaces only:
  * webhook /health (:5000) reachable -- Chartink signals can physically arrive
  * /metrics signals_received >= 1 -- a real signal showed up (WARN if zero: could be
    a genuinely quiet open OR a broken webhook -> verify externally, never CRITICAL)
  * /metrics queue_depth vs queue_capacity -- no 503 backpressure building
"""
from __future__ import annotations

from scripts.preflight.base import Check, CheckContext, CheckResult, Criticality
from scripts.preflight.checks import engine

WEBHOOK_HEALTH_URL = "http://127.0.0.1:5000/health"


class WebhookResponsiveCheck(Check):
    name = "webhook_responsive"
    group = "Signals"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 800

    def run(self, ctx: CheckContext) -> CheckResult:
        status, body = engine._http_get_json(WEBHOOK_HEALTH_URL)
        # AB-910 §1.7 (84cee3e) put the webhook /health behind the secret; this check
        # calls it UNAUTHENTICATED by design (a token in the URL would leak the secret
        # into logs — the 17-Jul token-at-rest sweep). So a 401 is the endpoint
        # ANSWERING and proves the one thing this check exists to prove: Flask is
        # listening and Chartink signals can physically arrive. Mirrors the S4 boot
        # self-check (utils/startup_checks.py:807, which treats 2xx-or-401 as reachable).
        # SCOPE: this tolerance is for the LOCAL /health only. Do NOT copy it to
        # check_scanner (external Chartink), where a 401 IS a real anomaly. Anything
        # else — unreachable (0), 5xx, 404 — is still a genuine failure.
        if status == 200 or status == 401:
            return self._passed(f"webhook /health {status} — Flask up (signals can arrive)")
        if status == 0:
            return self._failed(f"webhook unreachable — Chartink signals cannot arrive "
                                f"({body.get('error', '')})")
        return self._failed(f"webhook /health HTTP {status}")


class SignalsArrivedCheck(Check):
    """Zero signals = WARN, never CRITICAL: a quiet open is legitimate; pair with the
    webhook check (up + zero = probably quiet; down + zero = the real problem)."""

    name = "signals_arrived"
    group = "Signals"
    criticality = Criticality.WARN
    expected_duration_ms = 800

    def run(self, ctx: CheckContext) -> CheckResult:
        status, m = engine._http_get_json(engine.METRICS_URL)
        if status != 200:
            return self._warn(f"could not read /metrics for signal count (HTTP {status})")
        n = int(m.get("signals_received", 0) or 0)
        if n >= 1:
            return self._passed(f"{n} signal(s) received today (last {m.get('last_signal_at') or '?'})",
                                signals_received=n)
        return self._warn("no signals received yet — quiet open OR webhook issue; "
                          "verify externally if the market is active", signals_received=0)


class WebhookBackpressureCheck(Check):
    name = "webhook_backpressure"
    group = "Signals"
    criticality = Criticality.WARN
    expected_duration_ms = 800

    def run(self, ctx: CheckContext) -> CheckResult:
        status, m = engine._http_get_json(engine.METRICS_URL)
        if status != 200:
            return self._skipped(f"no /metrics (HTTP {status})")
        depth = int(m.get("queue_depth", 0) or 0)
        cap = int(m.get("queue_capacity", 0) or 0)
        if cap and depth >= cap * 0.9:
            return self._warn(f"webhook queue near capacity ({depth}/{cap}) — 503 backpressure risk",
                              queue_depth=depth, queue_capacity=cap)
        return self._passed(f"webhook queue {depth}/{cap or '?'} (no backpressure)",
                            queue_depth=depth, queue_capacity=cap)


CHECKS = [
    WebhookResponsiveCheck(),
    SignalsArrivedCheck(),
    WebhookBackpressureCheck(),
]
