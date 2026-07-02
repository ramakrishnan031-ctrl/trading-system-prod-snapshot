"""
ops_dashboard/backend/readers/host_reader.py

Service liveness via `systemctl is-active <unit>` (subprocess, 2s timeout, NO
sudo). On a non-Linux dev box (Windows PC) systemctl does not exist → every unit
reports "unavailable" (guarded by platform). Never raises to the caller.

States returned per unit: "active" | "inactive" | "failed" | "activating" |
"unknown" | "unavailable" (no systemctl / timeout / error).
"""
from __future__ import annotations

import platform
import shutil
import subprocess  # nosec B404 (fixed argv, no shell, no user input)
from typing import Optional

_TIMEOUT_SEC = 2
_KNOWN = {"active", "inactive", "failed", "activating", "deactivating", "reloading"}


def _systemctl_available() -> bool:
    if platform.system() != "Linux":
        return False
    return shutil.which("systemctl") is not None


def unit_state(unit: str) -> str:
    """`systemctl is-active <unit>` → normalized state string. Never raises."""
    if not _systemctl_available():
        return "unavailable"
    try:
        proc = subprocess.run(  # nosec B603 (no shell, fixed binary, fixed args)
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SEC,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "unavailable"
    out = (proc.stdout or "").strip().lower()
    if out in _KNOWN:
        return out
    # `is-active` prints "inactive"/"failed" with a non-zero exit; unknown text
    # (e.g. "unknown") falls through here.
    return out or "unknown"


def all_units(cfg: dict) -> list:
    """State for each configured unit. Returns a list of {unit, state} dicts."""
    units = cfg.get("units", []) or []
    return [{"unit": u, "state": unit_state(u)} for u in units]
