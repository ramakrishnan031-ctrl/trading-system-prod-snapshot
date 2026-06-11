"""
tests/crash_test/ct_utils.py — Shared utilities for all 12 crash test tools.
"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# IST timezone
# ---------------------------------------------------------------------------
_IST = timezone(timedelta(hours=5, minutes=30))


def ist_now() -> datetime:
    return datetime.now(tz=_IST)


def ist_now_iso() -> str:
    return ist_now().isoformat()


def today_str() -> str:
    return ist_now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Path detection
# ---------------------------------------------------------------------------

def _detect_base_dir() -> Path:
    """Auto-detect project root on Windows PC or Linux VM."""
    # Walk up from this file to find project root (has core/ and config/)
    candidate = Path(__file__).resolve().parent.parent.parent
    if (candidate / "core").is_dir() and (candidate / "config").is_dir():
        return candidate
    # Fallback: common paths
    if platform.system() == "Linux":
        home = Path.home()
        vm_path = home / "systems" / "trading-system"
        if vm_path.is_dir():
            return vm_path
    pc_path = Path(r"D:\Projects\trading-system")
    if pc_path.is_dir():
        return pc_path
    return candidate


BASE_DIR = _detect_base_dir()
CRASH_TEST_DIR = BASE_DIR / "tests" / "crash_test"
REPORTS_DIR = BASE_DIR / "reports" / "crash_test"
RESULTS_DIR = REPORTS_DIR / "results"
SNAPSHOTS_DIR = REPORTS_DIR / "snapshots"
RESOURCES_DIR = REPORTS_DIR / "resources"
SCENARIOS_DIR = CRASH_TEST_DIR / "scenarios"
DB_PATH = BASE_DIR / "data_store" / "trading_system.db"


# Ensure output dirs exist
for _d in [RESULTS_DIR, SNAPSHOTS_DIR, RESOURCES_DIR]:
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db_connection(readonly: bool = False) -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")
    uri = f"file:{DB_PATH}?mode=ro" if readonly else str(DB_PATH)
    if readonly:
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_config_cache: Optional[dict] = None


def get_config(key: Optional[str] = None) -> Any:
    global _config_cache
    if _config_cache is None:
        config_path = BASE_DIR / "config" / "system_config.yaml"
        if config_path.exists():
            try:
                import yaml
                with open(config_path, "r") as f:
                    _config_cache = yaml.safe_load(f) or {}
            except ImportError:
                _config_cache = {}
        else:
            _config_cache = {}
    if key is None:
        return _config_cache
    keys = key.split(".")
    val = _config_cache
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return None
    return val


# ---------------------------------------------------------------------------
# Master tracker
# ---------------------------------------------------------------------------

MASTER_TRACKER_PATH = REPORTS_DIR / "master_tracker.json"


def load_master_tracker() -> Dict[str, Any]:
    if MASTER_TRACKER_PATH.exists():
        with open(MASTER_TRACKER_PATH, "r") as f:
            return json.load(f)
    return {"days": [], "scenarios": {}}


def update_master_tracker(scenario_id: str, result: Dict[str, Any]) -> None:
    tracker = load_master_tracker()
    tracker["scenarios"][scenario_id] = {
        "classification": result.get("classification", "UNKNOWN"),
        "timestamp": ist_now_iso(),
        "invariant_overall": result.get("invariant_results", {}).get("overall", "N/A"),
    }
    MASTER_TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MASTER_TRACKER_PATH, "w") as f:
        json.dump(tracker, f, indent=2)


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

def format_result(
    scenario_id: str,
    classification: str,
    details: Dict[str, Any],
    title: str = "",
    root_cause: str = "",
    failure_type: str = "",
    business_impact: str = "",
    technical_impact: str = "",
    recovery_status: str = "",
    fix_required: bool = False,
    fix_id: str = "",
    retest_required: bool = False,
    invariant_results: Optional[Dict] = None,
) -> Dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "title": title,
        "classification": classification,
        "root_cause": root_cause,
        "failure_type": failure_type,
        "business_impact": business_impact,
        "technical_impact": technical_impact,
        "recovery_status": recovery_status,
        "fix_required": fix_required,
        "fix_id": fix_id,
        "retest_required": retest_required,
        "invariant_results": invariant_results or {},
        "timestamp": ist_now_iso(),
        "details": details,
    }


# ---------------------------------------------------------------------------
# Telegram (graceful skip)
# ---------------------------------------------------------------------------

def send_telegram(message: str) -> bool:
    try:
        config_path = BASE_DIR / "config" / "system_config.yaml"
        if not config_path.exists():
            return False
        import yaml
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f) or {}
        tg = cfg.get("telegram", {})
        token = tg.get("bot_token", "")
        chat_id = tg.get("chat_id", "")
        if not token or not chat_id:
            return False
        import urllib.request
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: float = 5.0) -> Optional[Dict]:
    try:
        import urllib.request
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode())
    except Exception:
        return None


def http_post(url: str, payload: Dict, timeout: float = 10.0) -> tuple:
    """Returns (status_code, response_body_dict, latency_ms)."""
    import urllib.request
    import time
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    start = time.perf_counter()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        latency = (time.perf_counter() - start) * 1000
        body = json.loads(resp.read().decode())
        return (resp.status, body, latency)
    except urllib.error.HTTPError as e:
        latency = (time.perf_counter() - start) * 1000
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = {"error": str(e)}
        return (e.code, body, latency)
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        return (0, {"error": str(e)}, latency)


# ---------------------------------------------------------------------------
# JSONL logging
# ---------------------------------------------------------------------------

def append_jsonl(path: Path, record: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
