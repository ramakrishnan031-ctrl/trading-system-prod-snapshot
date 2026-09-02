"""
ops_dashboard/backend/readers/metrics_client.py

Read-only HTTP GET to the production healthcheck server (:8080). Uses stdlib
urllib (no `requests` dependency). An unreachable trader NEVER raises to the
caller — it returns ``{"trader_alive": False, ...}``. This is the single
authority for "is the trader up right now".
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def _get_json(url: str, timeout: float) -> Any:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 (fixed loopback URL)
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def get_trader_health(cfg: dict) -> dict:
    """GET :8080/health. Returns a dict that ALWAYS has 'trader_alive'.

    Never raises: any network/parse failure ⇒ trader_alive=False.

    Screen-12 addition: `response_ms` is the MEASURED round trip to the health
    endpoint — the only genuine "response time" this system has for the trading
    engine. ⛔ It is None when the call failed, ⛔ never 0, because a failed call
    has no latency and 0 ms would read as instantaneous.

    ⚠️ The endpoint answers 503 when it is listening but DEGRADED. `urlopen`
    raises HTTPError on 503, and that body still carries the real health JSON —
    so it is READ rather than discarded. Treating a 503 as "unreachable" would
    report a degraded-but-running trader as dead, which is a materially
    different operational statement.
    """
    import time

    tm = cfg.get("trader_metrics", {})
    url = tm.get("health_url", "http://127.0.0.1:8080/health")
    timeout = float(tm.get("timeout_sec", 2))
    t0 = time.perf_counter()
    try:
        data = _get_json(url, timeout)
    except urllib.error.HTTPError as exc:          # 503 = degraded, still alive
        ms = round((time.perf_counter() - t0) * 1000.0, 1)
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
        except (ValueError, OSError):
            body = None
        if isinstance(body, dict):
            return {"trader_alive": True, "health": body, "response_ms": ms,
                    "http_status": exc.code}
        return {"trader_alive": False, "health": None, "response_ms": ms,
                "http_status": exc.code}
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return {"trader_alive": False, "health": None, "response_ms": None}
    ms = round((time.perf_counter() - t0) * 1000.0, 1)
    if not isinstance(data, dict):
        return {"trader_alive": False, "health": None, "response_ms": ms}
    return {"trader_alive": True, "health": data, "response_ms": ms,
            "http_status": 200}


def get_trader_metrics(cfg: dict) -> dict:
    """GET :8080/metrics. Never raises; unreachable ⇒ {'available': False}."""
    tm = cfg.get("trader_metrics", {})
    url = tm.get("metrics_url", "http://127.0.0.1:8080/metrics")
    timeout = float(tm.get("timeout_sec", 2))
    try:
        data = _get_json(url, timeout)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return {"available": False, "metrics": None}
    if not isinstance(data, dict):
        return {"available": False, "metrics": None}
    return {"available": True, "metrics": data}
