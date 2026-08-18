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

import csv
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
            # ⚠️ CORRECTED 16-Aug-2026: the comment here read "INTRADAY |
            # POSITIONAL". ⛔ POSITIONAL is NOT the enum — the production
            # validator `strategies/schema.py::_val_intent` permits exactly
            # INTRADAY or DELIVERY, and a scan of all 16 strategy YAMLs finds
            # only those two (13 / 3). This is the Trade Type source for Screens
            # 19/20; it is normalised in ONE place, services/strategy_meta.py.
            "intent": s.get("intent"),          # INTRADAY | DELIVERY (→ Trade Type)
            "order_protocol": s.get("order_protocol"),
            "max_concurrent_positions": int(s.get("max_concurrent_positions", 2)),
            "entry_start_time": s.get("entry_start_time"),
            "entry_end_time": s.get("entry_end_time"),
            # Screen-06 R:R column. ⛔ NO DEFAULT — a strategy that does not
            # configure a ratio must come back None so the UI can show '—'.
            # Defaulting to 2.0 here (order_placer's fallback) would put a
            # number on screen that the strategy never asked for.
            "tgt_risk_reward": s.get("tgt_risk_reward"),
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


def get_min_pass_score(cfg: dict) -> Optional[int]:
    """The configured minimum score a signal must reach to be eligible.

    Screen-04 labels this the **System Score** and shows it beside each signal's
    own score. It is a per-signal FALLBACK only: the authoritative value is
    `screener_results.eligible_score` (the threshold that actually applied to
    that signal). This is read for rows written before that column existed.

    Source order: config/scoring_weights.yaml `min_pass_score` (the scoring
    config the screener uses), then system_config.yaml. Returns None if neither
    is readable — the UI then renders '—' rather than a guessed number.
    """
    path = os.path.join(cfg["paths"]["config_dir"], "scoring_weights.yaml")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            v = raw.get("min_pass_score")
            if v is not None:
                return int(v)
        except (OSError, yaml.YAMLError, TypeError, ValueError):
            pass
    try:
        sysconf = get_system_config(cfg, "")
        v = dotted(sysconf, "scoring.min_pass_score")
        if v is None:
            v = sysconf.get("min_pass_score") if isinstance(sysconf, dict) else None
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def get_cron_jobs(cfg: dict) -> dict:
    """Parse config/cron_registry.yaml read-only → {job_name: {monitored,
    enabled, cron_expression, marker_name}} (M11 expected-heartbeat join).
    Root key is `jobs:`; the `officer:` block is settings, not a job.
    """
    path = os.path.join(cfg["paths"]["config_dir"], "cron_registry.yaml")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}
    jobs = raw.get("jobs") or {}
    if not isinstance(jobs, dict):
        return {}
    out: dict = {}
    for name, entry in jobs.items():
        if not isinstance(entry, dict):
            continue
        out[str(name)] = {
            "monitored": bool(entry.get("monitored", False)),
            "enabled": bool(entry.get("enabled", True)),
            "cron_expression": entry.get("cron_expression"),
            "marker_name": entry.get("marker_name"),
            "critical": bool(entry.get("critical", False)),
        }
    return out


def load_accounts(cfg: dict) -> list:
    """All rows of config/accounts.csv as header-keyed dicts, read-only. [] on any
    error (missing dir/file, malformed CSV) so the header falls back gracefully."""
    config_dir = (cfg.get("paths") or {}).get("config_dir")
    if not config_dir:
        return []
    path = os.path.join(config_dir, "accounts.csv")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except (OSError, csv.Error, UnicodeDecodeError):
        return []


def active_account(cfg: dict, account_id: Optional[str] = None) -> dict:
    """The currently-selected trading account row from config/accounts.csv.

    Prefers the live session's account_id; else the primary (is_primary=TRUE),
    else the first enabled row, else the first row. {} if the roster is unreadable.
    Columns (by value): account_id, broker, label (= client name), is_primary,
    enabled, ...
    """
    rows = load_accounts(cfg)
    if not rows:
        return {}
    if account_id:
        wanted = str(account_id).strip()
        for r in rows:
            if (r.get("account_id") or "").strip() == wanted:
                return r
    for flag in ("is_primary", "enabled"):
        for r in rows:
            if str(r.get(flag, "")).strip().upper() == "TRUE":
                return r
    return rows[0]


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


# ── SCREEN 21 — SCANNER MAPPING (16-Aug-2026) ────────────────────────────────
# ADDITIVE: `get_scan_webhook_map` above is byte-unchanged and keeps its callers.
# It returns {scanner: strategy} and DISCARDS `chartink_url`, which is exactly
# the field the approved SCANNER MAPPING panel needs.
def get_scanner_registry(cfg: dict) -> list:
    """The scanner registry, verbatim: [{scanner, strategy, chartink_url}].

    ⭐ THE URL IS REAL AND READ FROM `config/scan_webhook_map.yaml`. The approved
    artwork draws a Scanner URL column with `https://chartink.com/screener/123`
    placeholders; the production file carries the actual screener URLs (measured
    16-Aug: 16 scanners, every one with a `chartink_url`). ⛔ No URL is ever
    constructed, guessed or templated from a scanner name — an entry without one
    comes back None and the panel prints the unavailable marker.

    ⭐ SCANNER AND STRATEGY ARE 1:1 IN THIS SYSTEM (measured 16-Aug: 16 scanners
    → 16 DISTINCT strategies, and every scanner name IS its strategy name). That
    measurement is why Screen 21's main table carries STRATEGY ONLY — a Scanner
    column beside it would print the same identity twice. This panel is the ONE
    place the scanner name legitimately appears, because naming the mapping is
    the panel's whole purpose. The loader permits N:1, so nothing here assumes
    the 1:1 holds; `strategy` is read per entry.
    """
    path = os.path.join(cfg["paths"]["config_dir"], "scan_webhook_map.yaml")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return []
    scanners = raw.get("scanners") or {}
    if not isinstance(scanners, dict):
        return []
    out = []
    for scanner, entry in sorted(scanners.items()):
        if isinstance(entry, dict):
            strategy, url = entry.get("strategy"), entry.get("chartink_url")
        else:
            strategy, url = entry, None
        out.append({"scanner": str(scanner),
                    "strategy": str(strategy) if strategy else None,
                    "chartink_url": str(url) if url else None})
    return out


# ── SCREEN 16 — CONFIGURATION (18-Aug-2026) ──────────────────────────────────
# ADDITIVE. Two approved panels — SCORING ENGINE (WEIGHTS) and BROKER COSTS —
# read files that no reader exposed yet. ⛔ Neither is inside `config_json`:
# scoring lives in `config/scoring_weights.yaml` and broker costs in
# `config/broker_costs.yaml`, so a snapshot-only screen could not have shown
# either. Both return {} when the file is missing, and the screen then renders
# its unavailable state — ⛔ no default weight and no default cost is ever
# invented, because a fabricated cost reads exactly like a measured one.
def get_scoring_weights(cfg: dict) -> dict:
    """`config/scoring_weights.yaml` verbatim, read-only. {} if unreadable."""
    path = os.path.join(cfg["paths"]["config_dir"], "scoring_weights.yaml")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def get_broker_costs(cfg: dict, broker: Optional[str] = None) -> dict:
    """`config/broker_costs.yaml` for ONE broker, read-only. {} if unreadable.

    The file is keyed by broker name (measured 18-Aug: a single `zerodha` key).
    `broker` selects the block; when it is absent or unknown the SOLE key is used
    if there is exactly one, otherwise {} — ⛔ never an arbitrary first key,
    which would silently show another broker's costs as this broker's.
    """
    path = os.path.join(cfg["paths"]["config_dir"], "broker_costs.yaml")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(raw, dict):
        return {}
    if broker:
        block = raw.get(str(broker).strip().lower())
        if isinstance(block, dict):
            return block
    if len(raw) == 1:
        only = next(iter(raw.values()))
        return only if isinstance(only, dict) else {}
    return {}
