"""
ops_dashboard/backend/services/controls.py — SCREEN 17 CONTROLS composer.

📜 Design authority: ``docs/decisions/CONTROL_PLANE_DESIGN_17-Aug-2026.md``.
📜 Rama, 17-Aug: Screen 17 is the **operational control surface** (L4's
read-only interpretation is superseded); Screen 16 stays informational.

🔑 TWO SOURCES, AND THE DIFFERENCE IS THE WHOLE POINT:

  * **CONFIG** (`config_reader`, `db_reader`, `config_view`) — what is written
    down. Always available. This is what Screen 16 also shows, and information
    may legitimately appear on both screens.
  * **LIVE RUNTIME** (`control_client` → the trading process's control plane) —
    what the running trader is actually doing *right now*. ⛔ This is the ONLY
    thing a control may be rendered against.

⛔ When the control plane is unreachable, ``control_plane.available`` is False
and the UI must render controls as **UNAVAILABLE** — ⛔ never as operable, and
⛔ never fall back to the config value as though it were runtime truth. A config
file says what the next boot will do; it does not say what this process is doing.

⭐ SCANNER = STRATEGY (1:1, measured 17-Aug: 16 scanners → 16 distinct
strategies, scanner-name == strategy-name on 16/16). ⛔ No separate scanner
control identity is produced here; the strategy list IS the operational list.
"""
from __future__ import annotations

from typing import Any, Optional

from ..readers import config_reader, control_client, db_reader
from . import audit as audit_svc
from . import config_view, freshness


def _last_change_ts(history: list) -> Optional[str]:
    """Timestamp of the newest recorded control action, or None.

    ⛔ Returns None rather than a placeholder when nothing is recorded: the
    artwork's "Last Control Change" KPI must read as an explicit gap, never as
    a time that no audit row supports.
    """
    for row in history or []:
        ts = row.get("ts") or row.get("timestamp")
        if ts:
            return str(ts)
    return None


def _strategy_rows(strategies: dict, live_states: Optional[dict]) -> list:
    """One row per configured strategy.

    `configured_enabled` is the YAML value (what the NEXT boot will do);
    `live_enabled` is what the RUNNING process reports. They are kept apart
    deliberately — a divergence is real information, not noise to be smoothed.
    """
    rows = []
    for name in sorted(strategies):
        s = strategies[name] or {}
        cfg_enabled = bool(s.get("enabled", True))
        live = None if live_states is None else bool(live_states.get(name, cfg_enabled))
        rows.append({
            "name": name,
            "label": name.replace("_", " ").title(),
            "trade_type": (s.get("intent") or s.get("trade_type") or "INTRADAY"),
            "direction": s.get("direction"),
            "configured_enabled": cfg_enabled,
            "live_enabled": live,
            # ⚠️ Surfaced, never hidden: the running process and the file disagree.
            "diverged": (live is not None and live != cfg_enabled),
        })
    return rows


def _readiness(cfg: dict, ks: dict, live: Optional[dict]) -> list:
    """The five readiness tiles. ⛔ Each states its SOURCE; a tile with no source
    reports UNKNOWN rather than a comforting green."""
    out = []

    # Broker — the dashboard has no broker path at all (isolation I2).
    out.append({"name": "Broker", "state": "UNKNOWN",
                "detail": "no broker path from the dashboard (isolation I2/L4)"})

    # Database — we just read it; if that worked, it is ready.
    try:
        db_reader.get_kill_switch(cfg)
        out.append({"name": "Database", "state": "READY", "detail": "read-only connection OK"})
    except Exception as exc:                                     # noqa: BLE001
        out.append({"name": "Database", "state": "NOT READY",
                    "detail": f"{type(exc).__name__}"})

    # Services — the trading process answers the control plane or it does not.
    if live is not None and live.get("available"):
        out.append({"name": "Services", "state": "READY",
                    "detail": "trading process answered the control plane"})
    else:
        reason = (live or {}).get("reason") or "control plane unreachable"
        out.append({"name": "Services", "state": "NOT READY", "detail": reason})

    # Capital — reported by the running process, or unknown.
    body = ((live or {}).get("body") or {}).get("state") or {}
    limits = body.get("limits") or {}
    if limits.get("daily_loss_limit_pct") is not None:
        out.append({"name": "Capital", "state": "READY",
                    "detail": "limits reported by the running process"})
    else:
        out.append({"name": "Capital", "state": "UNKNOWN",
                    "detail": "no live limit reading available"})

    # Risk — the kill switch is the risk posture.
    state = (ks or {}).get("state") or "INACTIVE"
    out.append({
        "name": "Risk",
        "state": "READY" if state == "INACTIVE" else "NOT READY",
        "detail": f"kill switch {state}",
    })
    return out


def build_controls_screen(cfg: dict, today: Optional[str] = None) -> dict:
    """The Screen 17 payload. Read-only composition; ⛔ writes go through
    ``control_client.post_action`` from the API layer, never from here."""
    today = today or freshness.ist_today_iso()

    sc = config_reader.get_system_config(cfg, today) or {}
    risk = sc.get("risk") or {}
    ps = sc.get("position_sizing") or {}
    alerts_cfg = sc.get("alerts") or {}
    strategies = config_reader.get_strategies(cfg) or {}
    ks = db_reader.get_kill_switch(cfg)

    live = control_client.get_status(cfg)
    live_ok = bool(live.get("available")) and live.get("status") == 200
    live_state = (live.get("body") or {}).get("state") if live_ok else None
    live_strats = (live_state or {}).get("strategies", {}).get("states") if live_state else None

    force_intraday = bool(sc.get("force_intraday_only", True))
    trade_type = sc.get("trade_type") or "INTRADAY"
    intraday_on = force_intraday or trade_type in ("INTRADAY", "BOTH")
    delivery_on = (not force_intraday) and trade_type in ("DELIVERY", "BOTH")
    if intraday_on and delivery_on:
        mode_label, mode_state = "Both", "BOTH"
    elif intraday_on:
        mode_label, mode_state = "Intraday Only", "INTRADAY_ONLY"
    elif delivery_on:
        mode_label, mode_state = "Delivery Only", "DELIVERY_ONLY"
    else:
        mode_label, mode_state = "All Disabled", "ALL_DISABLED"

    rows = _strategy_rows(strategies, live_strats)
    enabled_now = sum(1 for r in rows if (r["live_enabled"] if r["live_enabled"] is not None
                                          else r["configured_enabled"]))

    # CONTROL HISTORY — real audit rows, newest first. ⛔ Not invented.
    try:
        hist = audit_svc.build_audit(cfg) or {}
        history = (hist.get("rows") or hist.get("records") or [])[:12]
    except Exception as exc:                                     # noqa: BLE001
        history = []
        hist = {"error": f"{type(exc).__name__}: {exc}"}

    # Panels 10/11/12 are INFORMATION (Rama §7: duplication of information is
    # allowed; duplication of control authority is not).
    try:
        cv = config_view.build_config_view(cfg, today) or {}
    except Exception as exc:                                     # noqa: BLE001
        cv = {"error": f"{type(exc).__name__}: {exc}"}

    entries_paused = None
    if live_state:
        ep = live_state.get("entries_paused") or {}
        entries_paused = bool(ep.get("entries_paused"))

    return {
        "today": today,
        "as_of": (live_state or {}).get("as_of"),

        # 🔑 The gate the whole UI keys off. False ⇒ render controls UNAVAILABLE.
        "control_plane": {
            "available": live_ok,
            "reason": None if live_ok else (live.get("reason")
                                            or f"status {live.get('status')}"),
            "note": ("controls act on the RUNNING process; when this is false the "
                     "screen shows configuration only and no control may be operated"),
        },

        "trading_mode": {
            "label": mode_label, "state": mode_state,
            "intraday_enabled": intraday_on, "delivery_enabled": delivery_on,
            "trade_type": trade_type, "force_intraday_only": force_intraday,
        },
        "strategies": {
            "total": len(rows),
            "enabled_count": enabled_now,
            "paused_count": len(rows) - enabled_now,
            "rows": rows,
            "_identity": ("scanner = strategy (1:1); the strategy list IS the "
                          "operational control list"),
        },
        "alerts": {
            "telegram_enabled": alerts_cfg.get("telegram_enabled"),
            "source": "config",
        },
        "limits": {
            "configured": {
                "max_daily_trades": risk.get("max_daily_trades"),
                "max_open_positions": risk.get("max_open_positions"),
                "max_concentration_pct": ps.get("max_concentration_pct"),
                "daily_loss_limit_pct": risk.get("daily_loss_limit_pct"),
            },
            "live": (live_state or {}).get("limits"),
        },
        "market_protection": {
            "entries_paused": entries_paused,
            "kill_switch": (live_state or {}).get("kill_switch") or ks,
            "exits_allowed": (None if entries_paused is None else True),
            "_note": ("Pause New Entries is in-memory and clears on restart; "
                      "Full Trading Stop uses the persisted kill switch"),
        },
        "readiness": _readiness(cfg, ks, live),
        "active_controls_summary": {
            "trading_mode": mode_label,
            "active_strategies": f"{enabled_now} / {len(rows)}",
            "paused_strategies": f"{len(rows) - enabled_now} / {len(rows)}",
            # ⛔ NO `active_scanners` KEY. scanner = strategy 1:1, so a scanner
            # count is the strategy count under a second name (Rama, 17-Aug:
            # strategy is the authoritative identity). Emitting one would let a
            # second scanner concept back in through the payload.
            # ARTWORK KPI — "Last Control Change". Real audit source, newest first.
            # ⛔ None when nothing is recorded; the UI renders that as an em-dash
            # rather than inventing a time.
            "last_control_change": _last_change_ts(history),
            "trading_status": (ks or {}).get("state") or "INACTIVE",
            "alert_status": ("ENABLED" if alerts_cfg.get("telegram_enabled")
                             else ("DISABLED" if alerts_cfg.get("telegram_enabled") is False
                                   else "UNKNOWN")),
        },
        "control_history": history,
        "simulation_mode": {
            "mode": (live_state or {}).get("mode") or sc.get("mode") or "UNKNOWN",
            "editable": False,
            "note": "display only — mode changes belong to Configuration",
        },
        "config_view": cv,
    }


def export_sheets(payload: dict) -> list:
    """[(sheet, header, rows)] from the SAME payload the screen was served, so an
    exported row can never disagree with the row on screen.

    ⛔ An unavailable value exports as an explicit marker, ⛔ never as a blank —
    a blank spreadsheet cell reads as zero or as "nothing happened".
    ⭐ Scanner rows are NOT emitted: scanner = strategy 1:1, so a scanner sheet
    would print one identity twice. The strategy sheet IS the control inventory.
    """
    def _c(v):
        return "UNAVAILABLE" if v is None else v

    acs = payload.get("active_controls_summary") or {}
    plane = payload.get("control_plane") or {}
    mode = payload.get("trading_mode") or {}
    lim = payload.get("limits") or {}
    conf, live = (lim.get("configured") or {}), (lim.get("live") or {})

    summary = [
        ("Generated For (IST date)", _c(payload.get("today"))),
        ("Runtime As Of", _c(payload.get("as_of"))),
        ("Control Plane Available", plane.get("available")),
        ("Control Plane Reason", _c(plane.get("reason"))),
        ("", ""),
        ("Trading Mode", _c(acs.get("trading_mode"))),
        ("Active Strategies", _c(acs.get("active_strategies"))),
        ("Paused Strategies", _c(acs.get("paused_strategies"))),
        ("Trading Status", _c(acs.get("trading_status"))),
        ("Alert Status", _c(acs.get("alert_status"))),
        ("Last Control Change", _c(acs.get("last_control_change"))),
        ("", ""),
        ("Simulation Mode", _c((payload.get("simulation_mode") or {}).get("mode"))),
    ]

    strat = [[r.get("label"), r.get("trade_type"), r.get("direction"),
              r.get("configured_enabled"),
              _c(r.get("live_enabled")), r.get("diverged")]
             for r in (payload.get("strategies") or {}).get("rows", [])]

    limits = [[k,
               _c(conf.get(k)),
               _c((live or {}).get(k))]
              for k in ("max_daily_trades", "max_open_positions",
                        "max_concentration_pct", "daily_loss_limit_pct")]

    ready = [[r.get("name"), r.get("state"), r.get("detail")]
             for r in (payload.get("readiness") or [])]

    hist = [[_c(h.get("ts") or h.get("timestamp")),
             _c(h.get("module")),
             _c(h.get("action") or h.get("event_type")),
             _c(h.get("user") or h.get("actor"))]
            for h in (payload.get("control_history") or [])]

    return [
        ("Active Controls Summary", ["Item", "Value"], summary),
        ("Strategy Controls",
         ["Strategy", "Trade Type", "Direction", "Configured", "Live", "Diverged"],
         strat),
        ("Runtime Limits", ["Parameter", "Configured", "Live"], limits),
        ("Readiness Check", ["Check", "State", "Detail"], ready),
        ("Control History", ["Time", "Module", "Action", "By"], hist),
    ]
