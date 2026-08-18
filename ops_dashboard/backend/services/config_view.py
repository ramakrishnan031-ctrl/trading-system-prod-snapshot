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


# ═══════════════════════════════════════════════════════════════════════════
# SCREEN 16 — CONFIGURATION  (`gui/16. Configuration.png` is BINDING)
#
# "Today's Configuration Center — single source of truth for all active trading
# configuration." ⛔ INFORMATION BOARD ONLY. Everything below is read-only: no
# control, no toggle, no Apply/Cancel/Revert, no mutation path. Screen 17
# Controls owns the operational surface; this screen may show the SAME values,
# ⛔ never a way to change one.
#
# ⛔ EVERYTHING `build_config_view` ABOVE RETURNS IS UNTOUCHED. `/api/config`
#    has a live contract test (test_g2b2_screens::test_config_api_groups_and_drift)
#    and other callers; this is a SECOND builder on a SECOND endpoint, exactly
#    as Screen 17 added `/api/controls/screen` beside the old summary.
#
# 🔑 THE ARTWORK'S NUMBERS ARE MOCK DATA, AND THE REAL CONFIG DISAGREES WITH
#    NEARLY ALL OF THEM (measured on the VM, 18-Aug-2026): Max Open Positions is
#    5 not 10, Max Daily Trades 10 not 50, Min Pass Score 60 not 70, Daily Loss
#    Limit 3.00% not 5.00%, Sector Exposure 40% not 25%, Entry Start 10:00 not
#    09:20. ⛔ NOT ONE of the artwork's values is hard-coded here — the artwork
#    is binding for LAYOUT, LABELS and DENSITY; the running configuration is
#    binding for VALUES.
#
# ⛔ SCANNER IS NOT AN IDENTITY ON THIS SCREEN. Scanner→strategy is 1:1
#    (measured 18-Aug: 16 scanners → 16 distinct strategies, scanner-name ==
#    strategy-name on 16/16), so a Scanner column beside Strategy Name prints one
#    identity twice, and a SCANNER MAPPING table makes a scanner a configuration
#    object in its own right. The artwork draws both; both are removed BY
#    DECISION. Screen 21 Scanner Attribution already owns the mapping, and it
#    documents the same 1:1 measurement.
# ═══════════════════════════════════════════════════════════════════════════

_UNAVAILABLE = "—"

#: Artwork label → the key that actually holds it. Order IS the artwork's order.
_HOURS_ROWS = (("Market Open", "market_open"), ("Market Close", "market_close"),
               ("Entry Start", "entry_start"), ("Entry End", "entry_end"),
               ("EOD Entry Cutoff", "eod_entry_cutoff"),
               ("Squareoff Time", "eod_squareoff_time"))

#: Scoring factor key → the artwork's display name. An unknown key is title-cased
#: rather than dropped — a weight that vanished from the panel but still counted
#: toward the total would make the total unexplainable.
_FACTOR_LABELS = {
    "volume_surge": "Volume Surge", "vwap_position": "VWAP", "atr_filter": "ATR",
    "rsi_range": "RSI", "price_action": "Price Action",
    "sector_strength": "Sector Strength", "time_of_day": "Time Of Day",
    "spread_check": "Spread", "circuit_check": "Circuit", "signal_age": "Signal Age",
}

#: The artwork's 12 category tabs → the snapshot top-level keys each one owns.
#: "advanced" is the REMAINDER bucket and is computed, never listed: every
#: top-level key no other tab claims lands there, so nothing is silently dropped.
_CATEGORIES = (
    ("system", "System", ("clock", "logging", "webhook", "live_feed",
                          "special_sessions", "signal_queue", "excluded_symbols",
                          "fno_ban", "trade_type", "force_intraday_only",
                          "delivery_enabled", "product_map", "mis_filter",
                          "scanner_check_delay_sec")),
    ("trading_hours", "Trading Hours", ("trading_hours",)),
    ("capital", "Capital", ("capital",)),
    ("risk", "Risk", ("risk", "kill_switch", "drift_handler", "circuit_breaker",
                      "strategy_circuit_breaker")),
    ("position_sizing", "Position Sizing", ("position_sizing", "portfolio_allocator")),
    ("scoring", "Scoring", ()),
    ("slippage", "Slippage", ("slippage_bands",)),
    ("strategies", "Strategies", ()),
    ("alerts", "Alerts", ("alerts",)),
    ("broker", "Broker", ("broker",)),
    ("scanners", "Scanners", ()),
    ("advanced", "Advanced", ()),
)


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(value, dp: int = 2):
    """A stored FRACTION rendered as a percentage. None → None, never 0."""
    f = _f(value)
    return None if f is None else "%.*f%%" % (dp, f * 100.0)


def _pct_direct(value, dp: int = 2):
    """A value already stored AS a percentage (broker_costs.yaml is)."""
    f = _f(value)
    return None if f is None else "%.*f%%" % (dp, f)


def _time12(hhmm):
    """'15:30' → '03:30 PM' (the artwork's clock format). Junk passes through."""
    if not hhmm or ":" not in str(hhmm):
        return hhmm
    try:
        h, m = str(hhmm).split(":")[:2]
        h, m = int(h), int(m)
    except (TypeError, ValueError):
        return hhmm
    ampm = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return "%02d:%02d %s" % (h12, m, ampm)


def _row(label, value, note=None):
    """One label/value line. `value is None` IS the unavailable state — the
    template renders the marker; ⛔ no caller substitutes a stand-in."""
    return {"label": label, "value": value, "note": note}


def _flatten(node, prefix: str = "") -> dict:
    """Config tree → {dotted.path: leaf}. Lists are leaves (JSON) so an ordering
    change shows as one row rather than as N phantom rows."""
    out: dict = {}
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).startswith("_"):
                continue
            out.update(_flatten(value, "%s.%s" % (prefix, key) if prefix else str(key)))
    elif isinstance(node, list):
        out[prefix] = json.dumps(node, sort_keys=False, default=str)
    else:
        out[prefix] = node
    return out


def _module_of(dotted: str) -> str:
    """The CONFIGURATION HISTORY 'Module' column: the group the parameter's
    top-level key belongs to, reusing the SAME map the drift banner uses so the
    two panels can never disagree about where a key lives."""
    return _group_of(str(dotted).split(".")[0], None)


def _leaf_diff(old_system: dict, new_system: dict) -> list:
    """Parameter-level changes between two snapshot bodies, sorted by path."""
    old_flat, new_flat = _flatten(old_system), _flatten(new_system)
    rows = []
    for key in sorted(set(old_flat) | set(new_flat)):
        before, after = old_flat.get(key), new_flat.get(key)
        if before != after:
            rows.append({
                "parameter": key,
                "module": _module_of(key),
                "old": _UNAVAILABLE if before is None else str(before),
                "new": _UNAVAILABLE if after is None else str(after),
            })
    return rows


def _kpis(cfg, today, system, scoring, session, snap) -> list:
    """The artwork's six top KPI cards. Each carries a sub-line, and every
    sub-line that states a percentage states ITS BASE — a bare percentage with
    no base is not a number."""
    risk = system.get("risk") if isinstance(system.get("risk"), dict) else {}
    broker = system.get("broker") if isinstance(system.get("broker"), dict) else {}

    mode = (snap or {}).get("mode") or session.get("mode")
    trade_type = (snap or {}).get("trade_type") or session.get("trade_type")
    paper = (mode or "").upper() == "PAPER"

    max_open = risk.get("max_open_positions")
    open_now = db_reader.open_positions_count(cfg)
    max_trades = risk.get("max_daily_trades")
    used_trades = db_reader.daily_trades_used(cfg, today)

    def _util(used, cap):
        f = _f(cap)
        if f is None or f <= 0 or used is None:
            return "%s (cap unavailable)" % used
        return "%s (%.0f%%)" % (used, 100.0 * used / f)

    # Daily loss: the LIMIT is a percentage of opening capital, so the used
    # figure is shown against that exact base — ⛔ never a bare "remaining %".
    loss_pct = _f(risk.get("daily_loss_limit_pct"))
    opening = db_reader.opening_capital(cfg, today)
    loss_used = round(db_reader.realized_loss_today(cfg, today), 2)
    if opening is not None and loss_pct is not None:
        budget = round(loss_pct * opening, 2)
        loss_sub = "Used ₹%.2f of ₹%.2f (base: opening capital ₹%.2f)" % (
            loss_used, budget, opening)
    elif loss_pct is not None:
        loss_sub = "Opening capital not yet recorded — ₹ budget unavailable"
    else:
        loss_sub = None

    primary = broker.get("primary")
    return [
        {"key": "trade_mode", "label": "Trade Mode",
         "value": ("%s TRADING" % mode.upper()) if mode else None,
         "sub": "Paper: %s%s" % ("Enabled" if paper else "Disabled",
                                 (" · %s" % trade_type) if trade_type else ""),
         "tone": "info"},
        {"key": "broker", "label": "Broker",
         "value": primary.upper() if primary else None,
         "sub": ("Last sync: %s" % str(session.get("last_updated"))[11:19])
                if session.get("last_updated") else "Last sync: unavailable",
         "tone": "info"},
        {"key": "max_open_positions", "label": "Max Open Positions",
         "value": max_open, "sub": "Utilized: %s" % _util(open_now, max_open),
         "tone": "info"},
        {"key": "max_daily_trades", "label": "Max Daily Trades",
         "value": max_trades, "sub": "Completed: %s" % _util(used_trades, max_trades),
         "tone": "info"},
        {"key": "min_pass_score", "label": "Minimum Pass Score",
         "value": scoring.get("min_pass_score"),
         "sub": ("High score: %s+" % scoring["high_score_threshold"])
                if scoring.get("high_score_threshold") is not None
                else "High-score threshold unavailable",
         "tone": "info"},
        {"key": "daily_loss_limit", "label": "Daily Loss Limit",
         "value": _pct(risk.get("daily_loss_limit_pct")), "sub": loss_sub,
         "tone": "warn"},
    ]


def _slippage(system) -> dict:
    """The approved SLIPPAGE CONFIGURATION panel, from the gate that actually
    enforces it: `entry_gate.slippage_control`.

    ⚠️ THE ARTWORK'S THIRD COLUMN IS RE-LABELLED, ⛔ NOT INVENTED. It reads
    "Allowed Slippage (%)" and draws a constant 0.50% on every band. The running
    system does apply a constant percentage control across bands — but it is
    `max_slippage_fraction`, a fraction OF THE STOP DISTANCE, not of price.
    Printing it under a bare "(%)" header would state a real number against the
    wrong base, so the header names the base. ⛔ No per-band percentage of price
    exists in configuration and none is derived.
    """
    eg = system.get("entry_gate") if isinstance(system.get("entry_gate"), dict) else {}
    slc = eg.get("slippage_control") if isinstance(eg.get("slippage_control"), dict) else {}
    tiers = slc.get("tiers") if isinstance(slc.get("tiers"), list) else []

    of_sl = _pct(slc.get("max_slippage_fraction"))
    rows, low = [], 0
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        top = _f(tier.get("max_price"))
        # The last tier is an open top (999999 is the sentinel), drawn as "N+".
        band = ("%g+" % low) if (top is None or top >= 999999) else ("%g - %g" % (low, top))
        rows.append({"band": band,
                     "max_rs": _f(tier.get("max_slippage_rs")),
                     "pct_of_sl": of_sl})
        if top is not None and top < 999999:
            low = top
    return {
        "enabled": bool(slc.get("enabled")) if slc else None,
        "mode": slc.get("mode"),
        "rows": rows,
        "params": [
            _row("Mode", slc.get("mode")),
            _row("Max slippage (% of stop distance)", of_sl),
            _row("Absolute cap", ("₹%.2f" % _f(slc["absolute_cap_rs"]))
                 if _f(slc.get("absolute_cap_rs")) is not None else None),
            _row("Hard maximum", ("₹%.2f" % _f(slc["hard_max_slippage_rs"]))
                 if _f(slc.get("hard_max_slippage_rs")) is not None else None),
            _row("Default when no band matches", ("₹%.2f" % _f(slc["default_max_slippage_rs"]))
                 if _f(slc.get("default_max_slippage_rs")) is not None else None),
            _row("Also apply % check", str(bool(slc.get("also_apply_pct_check")))
                 if slc else None),
        ],
        "analytics_bands": system.get("slippage_bands")
                           if isinstance(system.get("slippage_bands"), list) else [],
    }


def _strategies(cfg) -> list:
    """STRATEGY CONFIGURATION rows — Strategy Name · Enabled · Trade Type · Status.

    ⛔ NO SCANNER COLUMN. Scanner→strategy is 1:1 and the names are identical, so
    the column would print the row's own identity a second time.
    ⛔ `enabled` is DISPLAYED, never operated: this screen has no toggle, no
    endpoint and no mutation path. Screen 17 owns that.
    """
    rows = []
    for name, s in sorted(config_reader.get_strategies(cfg).items(),
                          key=lambda kv: (kv[1].get("display_name") or kv[0]).lower()):
        on = bool(s.get("enabled"))
        rows.append({
            "name": name,
            "label": s.get("display_name") or name,
            "enabled": on,
            "trade_type": s.get("intent"),          # INTRADAY | DELIVERY | None
            "status": "ACTIVE" if on else "DISABLED",
            "direction": s.get("direction"),
        })
    return rows


def _categories(system, scoring, strategies, broker_costs, scan_map) -> list:
    """The artwork's 12 category tabs, each carrying its REAL rows.

    ⛔ NOTHING IS SILENTLY DROPPED: "Advanced" is the remainder of the snapshot
    tree after every other tab has taken its keys, so every leaf the running
    system carries is reachable from some tab.
    ⛔ THE "Scanners" TAB CARRIES NO PER-SCANNER ROW. It states the measured
    strategy-to-scanner relationship and its count; one row per scanner would be
    exactly the separate scanner identity this screen must not create.
    """
    claimed, out = set(), []
    for key, label, owns in _CATEGORIES:
        claimed.update(owns)
        rows = []
        for top in owns:
            if top in system:
                for path, leaf in sorted(_flatten({top: system[top]}).items()):
                    rows.append({"parameter": path,
                                 "value": _UNAVAILABLE if leaf is None else str(leaf)})
        if key == "scoring":
            for path, leaf in sorted(_flatten(scoring).items()):
                rows.append({"parameter": "scoring_weights.%s" % path,
                             "value": _UNAVAILABLE if leaf is None else str(leaf)})
        elif key == "strategies":
            for s in strategies:
                rows.append({"parameter": s["name"],
                             "value": "%s / %s" % (s["status"],
                                                   s["trade_type"] or _UNAVAILABLE)})
        elif key == "broker":
            for path, leaf in sorted(_flatten(broker_costs).items()):
                rows.append({"parameter": "broker_costs.%s" % path,
                             "value": _UNAVAILABLE if leaf is None else str(leaf)})
        elif key == "slippage":
            eg = system.get("entry_gate") if isinstance(system.get("entry_gate"), dict) else {}
            slc = eg.get("slippage_control")
            if isinstance(slc, dict):
                for path, leaf in sorted(_flatten({"entry_gate.slippage_control": slc}).items()):
                    rows.append({"parameter": path,
                                 "value": _UNAVAILABLE if leaf is None else str(leaf)})
        elif key == "scanners":
            names = sorted(set(scan_map.values()))
            rows.append({"parameter": "scanners_mapped", "value": str(len(scan_map))})
            rows.append({"parameter": "distinct_strategies_targeted", "value": str(len(names))})
            rows.append({"parameter": "relationship",
                         "value": "1:1 - scanner is not a separate configuration "
                                  "object; the strategy is the identity"})
        out.append({"key": key, "label": label, "rows": rows, "count": len(rows)})

    remainder = [k for k in sorted(system) if k not in claimed and not k.startswith("_")]
    adv = []
    for top in remainder:
        for path, leaf in sorted(_flatten({top: system[top]}).items()):
            adv.append({"parameter": path,
                        "value": _UNAVAILABLE if leaf is None else str(leaf)})
    for entry in out:
        if entry["key"] == "advanced":
            entry["rows"], entry["count"] = adv, len(adv)
    return out


def build_config_center(cfg: dict, today=None, now=None) -> dict:
    """SCREEN 16 - the read-only Configuration Center payload."""
    now = now or freshness.ist_now()
    today = today or freshness.ist_today_iso(now)

    snap = db_reader.latest_config_snapshot(cfg, today)
    session = db_reader.get_session_info(cfg) or {}
    system = _system_tree(snap["config_json"]) if snap else {}
    source = "config_snapshot"
    if not system:
        # ⛔ FALLBACK IS LABELLED, NEVER SILENT: without today's snapshot the
        # screen reads the YAML on disk, which is the config that WILL apply,
        # not the one that DID. The header says which.
        system = {k: v for k, v in config_reader.get_system_config(cfg, today).items()
                  if not str(k).startswith("_")}
        source = "yaml_fallback" if system else "unavailable"

    scoring = config_reader.get_scoring_weights(cfg)
    broker = system.get("broker") if isinstance(system.get("broker"), dict) else {}
    broker_costs = config_reader.get_broker_costs(cfg, broker.get("primary"))
    strategies = _strategies(cfg)
    scan_map = config_reader.get_scan_webhook_map(cfg)

    hours = system.get("trading_hours") if isinstance(system.get("trading_hours"), dict) else {}
    capital = system.get("capital") if isinstance(system.get("capital"), dict) else {}
    risk = system.get("risk") if isinstance(system.get("risk"), dict) else {}
    sizing = system.get("position_sizing") if isinstance(system.get("position_sizing"), dict) else {}

    lev = capital.get("leverage_map") if isinstance(capital.get("leverage_map"), dict) else {}
    lev_txt = " / ".join("%s %gx" % (k, _f(v)) for k, v in lev.items()
                         if _f(v) is not None) or None

    # ── CONFIGURATION COMPARISON: current vs the most recent snapshot whose hash
    #    DIFFERS. ⛔ Not "yesterday" — production ran 10 identical days in a row
    #    (measured 18-Aug), so a yesterday-comparison is empty almost always and
    #    the panel would look broken while the real last change sat days back.
    trail = db_reader.config_snapshot_trail(cfg)
    previous, comparison = None, []
    if trail:
        for row in trail[1:]:
            if row["config_hash"] != trail[0]["config_hash"]:
                previous = row
                break
        if previous is not None:
            comparison = _leaf_diff(_system_tree(previous["config_json"]),
                                    _system_tree(trail[0]["config_json"]))

    # ── CONFIGURATION HISTORY: every hash transition in the trail, expanded to
    #    parameter level. ⛔ "Changed By" HAS NO SOURCE — the system records no
    #    per-change author (G-5). It is rendered as unavailable with the reason,
    #    ⛔ never filled with the operator's name or "system".
    history = []
    for i in range(len(trail) - 1):
        if trail[i]["config_hash"] == trail[i + 1]["config_hash"]:
            continue
        for change in _leaf_diff(_system_tree(trail[i + 1]["config_json"]),
                                 _system_tree(trail[i]["config_json"])):
            entry = {"date": trail[i]["snapshot_date"], "ts": trail[i]["snapshot_ts"],
                     "changed_by": None}
            entry.update(change)
            history.append(entry)

    weights = scoring.get("steps") if isinstance(scoring.get("steps"), dict) else {}
    weight_rows = [{"factor": _FACTOR_LABELS.get(k, str(k).replace("_", " ").title()),
                    "key": k, "weight": _f(v)}
                   for k, v in weights.items()]
    total_weight = sum(r["weight"] for r in weight_rows if r["weight"] is not None) \
        if weight_rows else None

    return {
        "today": today,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "header": {
            "source": source,
            "snapshot_date": (snap or {}).get("snapshot_date"),
            "snapshot_ts": (snap or {}).get("snapshot_ts"),
            "config_hash": ((snap or {}).get("config_hash") or "")[:16] or None,
            "mode": (snap or {}).get("mode") or session.get("mode"),
            "trade_type": (snap or {}).get("trade_type") or session.get("trade_type"),
            "last_change_date": _last_change_date(
                db_reader.config_snapshot_history(cfg)),
        },
        "kpis": _kpis(cfg, today, system, scoring, session, snap),
        "categories": _categories(system, scoring, strategies, broker_costs, scan_map),
        "trading_hours": [_row(label, _time12(hours.get(key)))
                          for label, key in _HOURS_ROWS],
        "capital": [
            _row("Intraday Allocation", _pct(capital.get("intraday_bucket_pct")),
                 "share of deployable capital"),
            _row("Delivery Allocation", _pct(capital.get("positional_bucket_pct")),
                 "share of deployable capital"),
            _row("Leverage", lev_txt, "per product, from leverage_map"),
            _row("SL Buffer", _pct(capital.get("sl_limit_offset_pct")),
                 "sl_limit_offset_pct"),
            _row("Emergency Exit Buffer", _pct(capital.get("emergency_exit_buffer_pct"))),
        ],
        "risk": [
            _row("Max Open Positions", risk.get("max_open_positions")),
            _row("Max Daily Trades", risk.get("max_daily_trades")),
            _row("Max Consecutive Losses", risk.get("max_consecutive_losses")),
            _row("Daily Loss Limit %", _pct(risk.get("daily_loss_limit_pct")),
                 "of opening capital"),
            _row("Sector Exposure %", _pct(risk.get("max_sector_exposure_pct")),
                 ("mode: %s" % risk.get("sector_cap_mode"))
                 if risk.get("sector_cap_mode") else None),
            _row("Price Drift Threshold", _pct(risk.get("price_drift_threshold"))),
        ],
        "position_sizing": [
            _row("Risk Per Trade", _pct(sizing.get("risk_per_trade_pct")),
                 "of deployable capital"),
            _row("Max Position Size", _pct(sizing.get("max_position_value_pct")),
                 "max_position_value_pct"),
            _row("Max Concentration", _pct(sizing.get("max_concentration_pct"))),
            _row("Min Qty Threshold", sizing.get("min_qty_threshold")),
            _row("Min Tick Size", sizing.get("min_tick_size")),
        ],
        "scoring": {
            "min_pass_score": scoring.get("min_pass_score"),
            "high_score_threshold": scoring.get("high_score_threshold"),
            "medium_score_threshold": scoring.get("medium_score_threshold"),
            "weights": weight_rows,
            "total_weight": total_weight,
            "available": bool(weight_rows),
        },
        "slippage": _slippage(system),
        "strategies": strategies,
        "broker_costs": [
            _row("Brokerage", _pct_direct(broker_costs.get("brokerage_pct_intraday"), 4),
                 ("capped at Rs %g per order" % _f(broker_costs["brokerage_flat_intraday"]))
                 if _f(broker_costs.get("brokerage_flat_intraday")) is not None else None),
            _row("STT", _pct_direct(broker_costs.get("stt_sell_pct"), 4), "intraday, sell side"),
            _row("GST", _pct_direct(broker_costs.get("gst_pct"), 2)),
            _row("Exchange Charges", _pct_direct(broker_costs.get("exchange_txn_pct"), 5)),
            _row("SEBI Charges", _pct_direct(broker_costs.get("sebi_pct"), 5)),
            _row("Stamp Duty", _pct_direct(broker_costs.get("stamp_duty_mis_buy_pct"), 4),
                 "intraday, buy side"),
        ],
        "comparison": {
            "current_date": (trail[0]["snapshot_date"] if trail else None),
            "current_hash": ((trail[0]["config_hash"] if trail else "") or "")[:12] or None,
            "previous_date": (previous or {}).get("snapshot_date"),
            "previous_hash": ((previous or {}).get("config_hash") or "")[:12] or None,
            "rows": comparison,
            "note": None if previous is not None else
                    "no earlier snapshot with a different configuration",
        },
        "history": history,
        "changed_by_note": "Change author is not captured anywhere in the system "
                           "(G-5, single operator) - shown as unavailable, never inferred.",
        "search_categories": ["Risk", "Capital", "Strategy", "Score", "Slippage", "Broker"],
    }


def export_sheets(payload: dict, q: str = "") -> list:
    """[(sheet, header, rows)] for the XLSX export - built from the SAME payload
    the screen renders, so a cell cannot disagree with the panel above it.

    `q` is the screen's search term. The approved EXPORT panel says "Export
    filtered configuration data", so a non-empty term keeps only rows that match
    it - the workbook is the filtered view, not a second, wider one. A sheet
    whose rows all filter out is still written (with its header) so the reader
    can see it was considered and came back empty, rather than silently vanishing.
    """
    def kv(rows):
        return [[r.get("label"), r.get("value") if r.get("value") is not None
                 else _UNAVAILABLE, r.get("note") or ""] for r in rows]

    slip = payload.get("slippage") or {}
    scoring = payload.get("scoring") or {}
    sheets = [
        ("KPIs", ["Metric", "Value", "Detail"],
         [[k.get("label"), k.get("value") if k.get("value") is not None else _UNAVAILABLE,
           k.get("sub") or ""] for k in payload.get("kpis") or []]),
        ("Trading Hours", ["Parameter", "Value", "Note"], kv(payload.get("trading_hours") or [])),
        ("Capital", ["Parameter", "Value", "Note"], kv(payload.get("capital") or [])),
        ("Risk", ["Parameter", "Value", "Note"], kv(payload.get("risk") or [])),
        ("Position Sizing", ["Parameter", "Value", "Note"],
         kv(payload.get("position_sizing") or [])),
        ("Scoring", ["Factor", "Weight (%)"],
         [[w.get("factor"), w.get("weight")] for w in scoring.get("weights") or []] +
         [["TOTAL", scoring.get("total_weight")]]),
        ("Slippage", ["Price Band (Rs)", "Max Slippage (Rs)", "Max % of stop distance"],
         [[r.get("band"), r.get("max_rs"), r.get("pct_of_sl")] for r in slip.get("rows") or []]),
        ("Strategies", ["Strategy", "Enabled", "Trade Type", "Status"],
         [[s.get("label"), "YES" if s.get("enabled") else "NO",
           s.get("trade_type") or _UNAVAILABLE, s.get("status")]
          for s in payload.get("strategies") or []]),
        ("Broker Costs", ["Charge", "Value", "Note"], kv(payload.get("broker_costs") or [])),
        ("Comparison", ["Parameter", "Module", "Old Value", "New Value"],
         [[r.get("parameter"), r.get("module"), r.get("old"), r.get("new")]
          for r in (payload.get("comparison") or {}).get("rows") or []]),
        ("History", ["Date", "Module", "Parameter", "Old Value", "New Value", "Changed By"],
         [[h.get("date"), h.get("module"), h.get("parameter"), h.get("old"),
           h.get("new"), h.get("changed_by") or _UNAVAILABLE]
          for h in payload.get("history") or []]),
    ]
    needle = (q or "").strip().lower()
    if not needle:
        return sheets
    # ⭐ THE SHEET'S OWN NAME IS PART OF THE MATCH, exactly as the screen matches
    # the panel's name. The approved "popular searches" are CATEGORY names, so a
    # search for "Slippage" must select the Slippage sheet whole rather than
    # empty it — no cell in that table contains the word "slippage".
    # ⛔ Screen and workbook use the SAME rule; a second rule here would let the
    # export disagree with the panel it came from.
    return [(title, header,
             rows if needle in title.lower() else
             [r for r in rows
              if any(needle in str(c).lower() for c in r if c is not None)])
            for title, header, rows in sheets]
