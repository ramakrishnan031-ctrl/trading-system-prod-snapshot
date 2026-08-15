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
# Screen-12 — per-unit lifecycle detail. ADDITIVE: `unit_state`/`all_units`
# above are byte-unchanged and keep their own callers.
# ─────────────────────────────────────────────────────────────────────────────
_SHOW_PROPS = ("ActiveState", "SubState", "ActiveEnterTimestamp",
               "ExecMainStatus", "NRestarts")


def unit_details(unit: str) -> dict:
    """`systemctl show <unit>` for the properties Screen 12 needs.

    ⭐ ONE subprocess call for all five properties, ⛔ not five calls — the units
    list is polled on every refresh and five `systemctl` spawns per unit would
    make the health screen the most expensive page in the dashboard.

    ⭐ `systemctl show -p <prop> --value` is the project's ESTABLISHED way to read
    unit properties (`deploy/token_watcher.sh:133-134`, `deploy/resume.sh:40`),
    so this reuses the pattern rather than inventing one.

    Never raises. On a non-Linux box (or any failure) every field is None and
    `available` is False — ⛔ the caller must render that as UNKNOWN, never as
    healthy and never as failed.
    """
    out = {"unit": unit, "available": False, "state": unit_state(unit),
           "sub_state": None, "started_at": None, "uptime_sec": None,
           "exec_main_status": None, "restarts": None}
    if not _systemctl_available():
        return out
    try:
        proc = subprocess.run(  # nosec B603 (no shell, fixed binary, fixed args)
            ["systemctl", "show", unit, "-p", ",".join(_SHOW_PROPS)],
            capture_output=True, text=True, timeout=_TIMEOUT_SEC, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return out
    props = {}
    for line in (proc.stdout or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            props[k.strip()] = v.strip()
    if not props:
        return out
    out["available"] = True
    out["sub_state"] = props.get("SubState") or None
    out["exec_main_status"] = props.get("ExecMainStatus") or None
    try:
        out["restarts"] = int(props.get("NRestarts") or 0)
    except ValueError:
        out["restarts"] = None
    ts = props.get("ActiveEnterTimestamp") or ""
    # systemd prints e.g. "Fri 2026-08-15 08:15:19 IST". An inactive unit prints
    # an EMPTY value — ⛔ that is "never started", not "started at epoch".
    out["started_at"] = ts or None
    out["uptime_sec"] = _uptime_from_systemd_stamp(ts)
    return out


def _uptime_from_systemd_stamp(stamp: str) -> Optional[int]:
    """Seconds since `ActiveEnterTimestamp`, or None when it cannot be trusted.

    ⛔ Returns None rather than 0 for an unparseable or absent stamp: a zero
    uptime reads as "just restarted", which is a materially different and
    alarming statement from "not known".

    ⚠️ The stamp carries a timezone ABBREVIATION ("IST"), which `strptime` cannot
    map to an offset. The date and clock time are parsed and compared against
    LOCAL wall-clock — correct here because the VM and the dashboard both run in
    IST, and stated so the assumption is visible rather than buried.
    """
    from datetime import datetime

    parts = (stamp or "").split()
    if len(parts) < 3:
        return None
    try:
        started = datetime.strptime(" ".join(parts[1:3]), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    delta = (datetime.now() - started).total_seconds()
    return int(delta) if delta >= 0 else None


def all_unit_details(cfg: dict) -> list:
    units = cfg.get("units", []) or []
    return [unit_details(u) for u in units]


def newest_backup(cfg: dict) -> Optional[dict]:
    """The most recent DB backup file, or None.

    ⭐ SOURCE IS THE REAL ARTEFACT, ⛔ not a config entry: `config/cron_registry
    .yaml` defines a `db_backup` job writing
    `data_store/backups/trading_system-<date>.db`, and this reads the directory
    that job writes. A configured-but-never-run backup therefore reports None
    rather than looking healthy because the cron entry exists.
    """
    root = cfg.get("paths", {}).get("data_store")
    if not root:
        return None
    d = os.path.join(root, "backups")
    if not os.path.isdir(d):
        return None
    best = None
    for name in os.listdir(d):
        if not name.endswith(".db"):
            continue
        try:
            st = os.stat(os.path.join(d, name))
        except OSError:
            continue
        if best is None or st.st_mtime > best["mtime"]:
            best = {"name": name, "mtime": st.st_mtime,
                    "size_mb": round(st.st_size / 2**20, 2)}
    return best


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
