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

# ── LIVE database paths — for READ-ONLY inspection and for the guard ONLY ────
# These are NEVER opened writable by this harness (see assert_not_live_db).
# The old ambiguous name ``DB_PATH`` was DELETED on purpose (18-Jul-2026): it
# meant "the live DB" while being handed to writable openers (StateStore, a
# default-writable get_db_connection), which is exactly how a destructive test
# harness ends up mutating production data. An ambiguous name IS the hazard, so
# callers must now say which one they mean — LIVE_DB_PATH (read-only) or
# SCRATCH_DB_PATH / make_scratch_db() (writable). Importing the old name now
# fails loudly with ImportError rather than silently resolving somewhere.
LIVE_DB_PATH = BASE_DIR / "data_store" / "trading_system.db"
LIVE_ANALYTICS_DB_PATH = BASE_DIR / "data_store" / "analytics.db"
_LIVE_DBS = (LIVE_DB_PATH, LIVE_ANALYTICS_DB_PATH)

# Scratch area for every WRITABLE/destructive harness operation. Overridable via
# CT_SCRATCH_DIR (itself guarded — an override that points at the live DB is
# refused, not honoured).
SCRATCH_DIR = Path(os.environ.get("CT_SCRATCH_DIR") or (BASE_DIR / "data_store" / "ct_scratch"))
SCRATCH_DB_PATH = SCRATCH_DIR / "ct_scratch.db"


# Ensure output dirs exist
for _d in [RESULTS_DIR, SNAPSHOTS_DIR, RESOURCES_DIR]:
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Database — scratch-safe BY CONSTRUCTION
# ---------------------------------------------------------------------------

class LiveDatabaseRefused(RuntimeError):
    """Raised when the crash-test harness is asked to WRITE to a live database.

    The crash tests are destructive by design (they corrupt state, cancel trades,
    reset capital). A writable handle on the live DB must therefore be impossible,
    not merely discouraged — so this refusal is a hard failure with no override
    flag and no environment escape hatch. Fail CLOSED.
    """


def _canonical(path) -> str:
    """Fully-resolved, symlink-free, case-normalised path string.

    Defeats the obvious foot-guns that a naive ``==`` comparison would miss:
    a relative path, ``..`` segments, ``~``, a symlink/junction pointing at the
    live DB, and (on Windows) a case difference.
    """
    p = os.path.expanduser(str(path))
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def assert_not_live_db(path) -> Path:
    """HARD GUARD: refuse (raise ``LiveDatabaseRefused``) if ``path`` is a live DB.

    Checked against every live database, and against their SQLite sidecars
    (``-wal`` / ``-shm``) — writing those corrupts the live DB just as surely.
    Comparison is on the canonical path, and additionally via ``os.path.samefile``
    when both exist, which also catches hardlinks and bind mounts that resolve to
    the same file by a different name.

    Returns the path unchanged when it is safe, so callers can wrap inline.
    """
    target = _canonical(path)
    for live in _LIVE_DBS:
        live_c = _canonical(live)
        if target == live_c or target in (live_c + "-wal", live_c + "-shm"):
            raise LiveDatabaseRefused(
                f"REFUSED: the crash-test harness may never open a LIVE database "
                f"writable.\n  requested: {path}\n  resolves to: {target}\n"
                f"  live DB:   {live}\n"
                f"Use make_scratch_db() / SCRATCH_DB_PATH instead. For read-only "
                f"inspection of the live system use get_db_connection(readonly=True) "
                f"or get_live_db_readonly()."
            )
        try:
            if os.path.exists(target) and os.path.exists(live_c) and os.path.samefile(target, live_c):
                raise LiveDatabaseRefused(
                    f"REFUSED: {path} is the SAME FILE as the live database {live} "
                    f"(hardlink/bind-mount). The harness may never write to it."
                )
        except OSError:
            pass  # samefile can fail on exotic paths — the canonical check above already ran
    return Path(path)


def scratch_db_path(name: str = "ct_scratch.db") -> Path:
    """Path to a scratch DB inside SCRATCH_DIR. Guarded: even a CT_SCRATCH_DIR
    override that resolves onto a live DB is refused rather than honoured."""
    p = SCRATCH_DIR / name
    assert_not_live_db(p)
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    return p


def make_scratch_db(name: str = "ct_scratch.db", fresh: bool = True) -> Path:
    """Create (or reuse) a scratch DB with the real schema and return its path.

    This is what every destructive crash test operates on. It reuses the project's
    own ``core/schema.sql`` via StateStore — the same bootstrap the harness already
    used in ct_day3_isolated.py — so a scratch DB is structurally identical to the
    live one and the CTs keep their full destructive capability.
    """
    p = scratch_db_path(name)
    if fresh:
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(p) + suffix)
            if f.exists():
                f.unlink()
    from core.state_store import StateStore  # local import: no import-time coupling
    StateStore(db_path=str(p), schema_path=str(BASE_DIR / "core" / "schema.sql")).close()
    return p


def get_db_connection(readonly: bool = False, db_path=None) -> sqlite3.Connection:
    """Open a harness DB connection.

    readonly=True  -> a READ-ONLY (``mode=ro``) handle; defaults to the LIVE DB,
                      which is the harness's legitimate diagnostic use (inspecting
                      the real system during/after a scenario). Safe by construction:
                      SQLite refuses writes on a mode=ro handle.
    readonly=False -> a WRITABLE handle; defaults to the SCRATCH DB and is passed
                      through assert_not_live_db(), so a live path — however it was
                      supplied — raises instead of opening.
    """
    if readonly:
        path = Path(db_path) if db_path is not None else LIVE_DB_PATH
        if not path.exists():
            raise FileNotFoundError(f"Database not found: {path}")
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        # NOTE: no journal_mode pragma here — it is a WRITE and fails on a ro handle.
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    path = Path(db_path) if db_path is not None else SCRATCH_DB_PATH
    assert_not_live_db(path)            # ← the load-bearing refusal
    if not path.exists() and path.parent == SCRATCH_DIR:
        make_scratch_db(path.name)      # auto-bootstrap inside the scratch area
    if not path.exists():
        raise FileNotFoundError(
            f"Scratch database not found: {path}. Call make_scratch_db() first."
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_live_db_readonly() -> sqlite3.Connection:
    """Explicit READ-ONLY handle on the live DB, for diagnostics that want to say so."""
    return get_db_connection(readonly=True, db_path=LIVE_DB_PATH)


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
