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
    """
    tm = cfg.get("trader_metrics", {})
    url = tm.get("health_url", "http://127.0.0.1:8080/health")
    timeout = float(tm.get("timeout_sec", 2))
    try:
        data = _get_json(url, timeout)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return {"trader_alive": False, "health": None}
    if not isinstance(data, dict):
        return {"trader_alive": False, "health": None}
    return {"trader_alive": True, "health": data}


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
