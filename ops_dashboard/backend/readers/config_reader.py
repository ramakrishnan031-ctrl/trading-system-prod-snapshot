"""
ops_dashboard/backend/readers/config_reader.py

Resolves the trading system's effective configuration for "today", READ-ONLY:
  1. Prefer today's config_snapshots.config_json (the config as-it-was, DB-pure)
     — config_json is AppConfig.model_dump(json); the system_config.yaml tree
     lives under config_json["system"].
  2. Fallback: parse config/system_config.yaml read-only if no snapshot yet.

Per-strategy config is NOT in config_json (separate StrategyLoader domain), so
strategies are always parsed from config/strategies/*.yaml read-only.

No production import: YAML is parsed by value (I1). Files are only ever read.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Any, Optional

import yaml

from . import db_reader


def get_system_config(cfg: dict, today: str) -> dict:
    """The system_config tree (risk/capital/signal_queue/... top-level).

    Snapshot-first, YAML-fallback. Returns {} only if neither source exists.
    """
    snap = db_reader.latest_config_snapshot(cfg, today)
    if snap is not None:
        try:
            parsed = json.loads(snap["config_json"])
            system = parsed.get("system")
            if isinstance(system, dict) and system:
                system = dict(system)
                system["_source"] = "config_snapshot"
                system["_snapshot_ts"] = snap.get("snapshot_ts")
                return system
        except (ValueError, KeyError, TypeError):
            pass
    # Fallback: read config/system_config.yaml directly (read-only).
    path = os.path.join(cfg["paths"]["config_dir"], "system_config.yaml")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if isinstance(data, dict):
            data["_source"] = "yaml_fallback"
            return data
    return {"_source": "unavailable"}


def get_strategies(cfg: dict) -> dict:
    """Parse config/strategies/*.yaml read-only → {name: {fields...}}."""
    out: dict = {}
    strat_dir = os.path.join(cfg["paths"]["config_dir"], "strategies")
    for path in sorted(glob.glob(os.path.join(strat_dir, "*.yaml"))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                s = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        name = s.get("name") or os.path.splitext(os.path.basename(path))[0]
        out[name] = {
            "name": name,
            "display_name": s.get("display_name", name),
            "enabled": bool(s.get("enabled", True)),
            "direction": s.get("direction"),
            "order_protocol": s.get("order_protocol"),
            "max_concurrent_positions": int(s.get("max_concurrent_positions", 2)),
            "entry_start_time": s.get("entry_start_time"),
            "entry_end_time": s.get("entry_end_time"),
        }
    return out


def get_scan_webhook_map(cfg: dict) -> dict:
    """Parse config/scan_webhook_map.yaml read-only → {scanner: strategy}.

    Format (S14, validated by strategies/loader.py:93-137 by value): each
    scanner entry carries exactly ONE `strategy` key, so scanner→strategy is
    1:1 or N:1 — never 1:N (attribution doc §0.1). Missing file → {}.
    """
    path = os.path.join(cfg["paths"]["config_dir"], "scan_webhook_map.yaml")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}
    scanners = raw.get("scanners") or {}
    if not isinstance(scanners, dict):
        return {}
    out: dict = {}
    for scanner, entry in scanners.items():
        strategy = entry.get("strategy") if isinstance(entry, dict) else entry
        if strategy:
            out[str(scanner)] = str(strategy)
    return out


def dotted(config: dict, path: str, default: Any = None) -> Any:
    """Navigate a dotted key path into a nested dict; default if any hop misses."""
    cur: Any = config
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def config_meta(cfg: dict, today: str) -> dict:
    """Source + snapshot metadata for display ('config as-of')."""
    snap = db_reader.latest_config_snapshot(cfg, today)
    if snap is not None:
        return {
            "source": "config_snapshot",
            "snapshot_date": snap.get("snapshot_date"),
            "snapshot_ts": snap.get("snapshot_ts"),
            "mode": snap.get("mode"),
            "trade_type": snap.get("trade_type"),
            "account_id": snap.get("account_id"),
            "config_hash": (snap.get("config_hash") or "")[:12],
        }
    return {"source": "yaml_fallback", "snapshot_date": None, "snapshot_ts": None}
