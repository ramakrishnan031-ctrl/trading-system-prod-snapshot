"""
ops_dashboard/backend/services/config_view.py

M20 Config View — today's config_snapshots.config_json grouped EXACTLY into:
System · Risk · Capital · Strategies · Scanners · Execution · Smart Target ·
Slippage. Read-only.

Header: snapshot ts, config hash, last-change date (most recent snapshot whose
hash differs from the immediately-prior snapshot). DRIFT BANNER when today's
hash ≠ yesterday's, listing changed top-level system keys mapped to their group
(group-level JSON diff, not a deep diff).

Strategies + Scanners come from config files (per-strategy config is NOT inside
config_json — W0.1), labeled honestly; they are excluded from snapshot drift.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Optional

from ..readers import config_reader, db_reader
from . import freshness

# Top-level system_config key → group. Unlisted keys fall back to "System"
# (nothing is ever silently dropped).
_KEY_GROUP = {
    # System
    "broker": "System", "trading_hours": "System", "special_sessions": "System",
    "signal_queue": "System", "excluded_symbols": "System",
    "force_intraday_only": "System", "trade_type": "System",
    "delivery_enabled": "System", "product_map": "System", "mis_filter": "System",
    "clock": "System", "webhook": "System", "logging": "System",
    "alerts": "System", "live_feed": "System", "fno_ban": "System",
    "scanner_check_delay_sec": "System",
    # Risk
    "risk": "Risk", "kill_switch": "Risk", "drift_handler": "Risk",
    "strategy_circuit_breaker": "Risk", "circuit_breaker": "Risk",
    # Capital
    "capital": "Capital", "position_sizing": "Capital",
    # Execution
    "signal_processor": "Execution", "order_monitor": "Execution",
    "order_reconciler": "Execution", "eod_squareoff": "Execution",
    "entry_gate": "Execution", "tgt_retry": "Execution", "paper": "Execution",
    "eod_reconcile": "Execution", "shadow_tracker": "Execution",
    "sr_detector": "Execution", "structure_exit": "Execution",
    # Smart Target
    "smart_tgt": "Smart Target",
    # Slippage
    "slippage_bands": "Slippage",
}
GROUPS = ("System", "Risk", "Capital", "Strategies", "Scanners",
          "Execution", "Smart Target", "Slippage")


def _system_tree(config_json: str) -> dict:
    try:
        parsed = json.loads(config_json)
    except (ValueError, TypeError):
        return {}
    system = parsed.get("system")
    return system if isinstance(system, dict) else {}


def _group_of(key: str, value) -> str:
    if key == "entry_gate":
        return "Execution"          # slippage_control split out below
    return _KEY_GROUP.get(key, "System")


def _grouped(system: dict) -> dict:
    """Group the snapshot's system tree; entry_gate.slippage_control → Slippage."""
    out: dict = {g: {} for g in GROUPS}
    for key, value in system.items():
        if key.startswith("_"):
            continue
        if key == "entry_gate" and isinstance(value, dict):
            eg = dict(value)
            slc = eg.pop("slippage_control", None)
            out["Execution"]["entry_gate"] = eg
            if slc is not None:
                out["Slippage"]["slippage_control"] = slc
            continue
        out[_group_of(key, value)][key] = value
    return out


def _changed_keys(sys_a: dict, sys_b: dict) -> list:
    """Top-level keys whose serialized value differs (group-level diff)."""
    changed = []
    for key in sorted(set(sys_a) | set(sys_b)):
        if key.startswith("_"):
            continue
        if json.dumps(sys_a.get(key), sort_keys=True, default=str) != \
           json.dumps(sys_b.get(key), sort_keys=True, default=str):
            changed.append({"key": key, "group": _group_of(key, None)})
    return changed


def _last_change_date(history: list) -> Optional[str]:
    """Most recent snapshot whose hash differs from the immediately-prior one.

    history is newest-first; 'prior' = the next (older) entry.
    """
    for i in range(len(history) - 1):
        if history[i]["config_hash"] != history[i + 1]["config_hash"]:
            return history[i]["snapshot_date"]
    return None


def build_config_view(cfg: dict, today: Optional[str] = None, now=None) -> dict:
    now = now or freshness.ist_now()
    today = today or freshness.ist_today_iso(now)

    snap = db_reader.latest_config_snapshot(cfg, today)
    history = db_reader.config_snapshot_history(cfg)

    header = {
        "snapshot_date": snap.get("snapshot_date") if snap else None,
        "snapshot_ts": snap.get("snapshot_ts") if snap else None,
        "config_hash": (snap.get("config_hash") or "")[:16] if snap else None,
        "mode": snap.get("mode") if snap else None,
        "last_change_date": _last_change_date(history),
        "source": "config_snapshot" if snap else "unavailable",
    }

    system = _system_tree(snap["config_json"]) if snap else {}
    groups = _grouped(system)

    # Strategies + Scanners from files (NOT in config_json — W0.1), honest label.
    groups["Strategies"] = {
        "_note": "from config/strategies/*.yaml — per-strategy config is not in "
                 "the snapshot (W0.1); excluded from drift",
        "strategies": config_reader.get_strategies(cfg),
    }
    groups["Scanners"] = {
        "_note": "from config/scan_webhook_map.yaml; excluded from drift",
        "scan_webhook_map": config_reader.get_scan_webhook_map(cfg),
    }

    # Drift banner: today's latest hash vs yesterday's latest hash.
    yesterday = (freshness.parse_ist(today + "T00:00:00") - timedelta(days=1)).strftime("%Y-%m-%d")
    y_snap = db_reader.config_snapshot_for_date(cfg, yesterday)
    drift = {"active": False, "yesterday": yesterday, "changed": []}
    if snap and y_snap and y_snap["config_hash"] != snap["config_hash"]:
        drift["active"] = True
        drift["changed"] = _changed_keys(_system_tree(y_snap["config_json"]), system)
    elif snap and y_snap is None:
        drift["note"] = "no snapshot for yesterday — drift not evaluable"

    return {
        "today": today,
        "header": header,
        "drift": drift,
        "groups": [{"name": g, "data": groups[g]} for g in GROUPS],
    }
