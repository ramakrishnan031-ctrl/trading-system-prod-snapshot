"""
ops_dashboard/backend/readers/control_client.py

The dashboard's ONLY link to the G3 control plane (design authority:
``docs/decisions/CONTROL_PLANE_DESIGN_17-Aug-2026.md``).

🔑 WHY HTTP AND NOT AN IMPORT. The control plane mutates objects that live in the
*trading* process; the dashboard is a **separate process on the same host**
(measured 17-Aug: dashboard PID 3674880, trader PID 3851262). Reaching it over
loopback HTTP keeps every isolation guarantee this service is built on — ⛔ no
production import (``test_isolation::test_a_no_production_imports``) and ⛔ no DB
write (``test_b_db_connection_is_readonly``). Mirrors ``metrics_client`` exactly:
stdlib ``urllib``, no ``requests`` dependency.

⛔ **AN UNREACHABLE CONTROL PLANE NEVER RAISES.** It returns ``available: False``
with a reason, so Screen 17 can say *"controls unavailable"* honestly instead of
rendering a button that would do nothing. A control that cannot be proven to have
applied must never be drawn as applied.

⛔ **THIS MODULE NEVER INVENTS STATE.** Every value it returns came from the
trading process's own reply.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional

_DEFAULT_BASE = "http://127.0.0.1:8600"


def _base(cfg: dict) -> str:
    cp = (cfg or {}).get("control_plane", {}) or {}
    return str(cp.get("base_url") or os.environ.get("CONTROL_URL") or _DEFAULT_BASE)


def _timeout(cfg: dict) -> float:
    cp = (cfg or {}).get("control_plane", {}) or {}
    return float(cp.get("timeout_sec", 3))


def _token(cfg: dict) -> Optional[str]:
    cp = (cfg or {}).get("control_plane", {}) or {}
    return cp.get("token") or os.environ.get("CONTROL_SECRET")


def _call(cfg: dict, path: str, payload: Optional[dict] = None,
          method: str = "GET") -> dict:
    """One request. Returns a dict that ALWAYS carries ``available``.

    ⛔ Never raises. A refusal from the plane (401/409/428/500) is NOT an
    outage — the body carries the real reason and is passed through, because
    "confirmation required" and "trader unreachable" are completely different
    operational statements and must never be collapsed.
    """
    token = _token(cfg)
    if not token:
        return {"available": False,
                "reason": "no control token configured for the dashboard",
                "status": None}
    url = _base(cfg).rstrip("/") + path
    data = json.dumps(payload or {}).encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Control-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=_timeout(cfg)) as resp:  # nosec B310 (fixed loopback URL)
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        return {"available": True, "status": 200, "body": body}
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
        except (ValueError, OSError):
            body = None
        # Reached the plane; it declined. That is an ANSWER, not an outage.
        return {"available": True, "status": exc.code, "body": body,
                "refused": True}
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return {"available": False, "status": None,
                "reason": f"control plane unreachable: {type(exc).__name__}"}


def get_status(cfg: dict) -> dict:
    """Live runtime control state, or ``available: False`` with a reason."""
    return _call(cfg, "/control/status", method="GET")


def post_action(cfg: dict, path: str, payload: dict) -> dict:
    """Send one control action. ⛔ The caller must render the RETURNED state."""
    return _call(cfg, path, payload=payload, method="POST")
