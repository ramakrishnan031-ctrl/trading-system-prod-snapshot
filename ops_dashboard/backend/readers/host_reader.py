"""
ops_dashboard/backend/readers/host_reader.py

Service liveness via `systemctl is-active <unit>` (subprocess, 2s timeout, NO
sudo). On a non-Linux dev box (Windows PC) systemctl does not exist → every unit
reports "unavailable" (guarded by platform). Never raises to the caller.

States returned per unit: "active" | "inactive" | "failed" | "activating" |
"unknown" | "unavailable" (no systemctl / timeout / error).
"""
from __future__ import annotations

import os
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


# ─────────────────────────────────────────────────────────────────────────────
# G2b-2 — M12 VM stats (platform-guarded; Windows dev → "unavailable") + M15
# sentinel flags (read-only file presence).
# ─────────────────────────────────────────────────────────────────────────────
def vm_stats(cfg: dict) -> dict:
    """Live RAM/load/disk. Linux-only facts; anything unreadable → None +
    available=False. CPU/RAM history is NOT collected (psutil absent) — the
    caller renders that gap honestly; nothing is fabricated here."""
    import shutil as _shutil

    out: dict = {"available": platform.system() == "Linux",
                 "mem_available_kb": None, "loadavg": None,
                 "disk_root": None, "disk_data": None}
    if platform.system() == "Linux":
        try:
            with open("/proc/meminfo", "r", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("MemAvailable:"):
                        out["mem_available_kb"] = int(line.split()[1])
                        break
        except (OSError, ValueError, IndexError):
            pass
        try:
            out["loadavg"] = list(os.getloadavg())
        except (OSError, AttributeError):
            pass
    # Disk works on every platform (shutil) — root + data dir.
    for key, path in (("disk_root", os.path.abspath(os.sep)),
                      ("disk_data", cfg.get("paths", {}).get("data_store"))):
        if not path:
            continue
        try:
            u = _shutil.disk_usage(path)
            out[key] = {"path": path, "total_gb": round(u.total / 2**30, 2),
                        "used_gb": round(u.used / 2**30, 2),
                        "free_gb": round(u.free / 2**30, 2),
                        "used_pct": round(100.0 * u.used / u.total, 1)}
        except OSError:
            out[key] = None
    return out


def list_sentinels(cfg: dict) -> list:
    """critical_alert_*.flag files in data_store (M15 banner). Read-only."""
    root = cfg.get("paths", {}).get("data_store")
    if not root or not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        if name.startswith("critical_alert_") and name.endswith(".flag"):
            try:
                out.append({"name": name,
                            "mtime": os.stat(os.path.join(root, name)).st_mtime})
            except OSError:
                continue
    return out
