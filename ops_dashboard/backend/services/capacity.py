"""
ops_dashboard/backend/services/capacity.py

Capacity Monitor — G2a rows #1-#8 (the Dashboard widget, unchanged shape in
`rows`) + the G2b-1 completion: ALL 30 inventory keys grouped for the capacity
screen (`groups`): Orders · Positions · Capital · Risk · Strategy ·
System-guards.

Row types:
  quota       — CONFIGURED / USED / REMAINING + status color
  config_only — configured value(s) + note (in-memory counter; D7: NEVER
                fabricate a live value)
  window      — time-window status (inside/outside now, via freshness)

Daily-loss "used" reads fm_ledger.RELEASE_USED.pnl_delta directly — NOT
get_daily_realized_net_pnl. 25-Jul-2026 RE-LABEL: W10 (that reader's cost
double-subtract) was FIXED 2026-07-17 and is no longer the reason. The live
reason is RESET_PNL — that reader sums ALL pnl_delta rows including the EOD
RESET_PNL counter-entry, which post-15:17 would zero the day's realized.
Decision D2, approved permanent by the G2a review.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from ..readers import config_reader, db_reader, metrics_client
from . import freshness

_WARN_FRAC = 0.80  # used ≥ 80% of limit → WARNING


def _status(used, limit) -> str:
    if limit is None:
        return "AWAITING"
    if limit <= 0:
        return "OK"
    if used >= limit:
        return "BREACH"
    if used >= _WARN_FRAC * limit:
        return "WARNING"
    return "OK"


def _row(key, label, scope, unit, used, limit, *, inert=False,
         status=None, note=None, extra=None) -> dict:
    if inert:
        status = "INERT"
    if status is None:
        status = _status(used if used is not None else 0, limit)
    remaining = None
    pct = None
    if limit is not None and used is not None:
        remaining = max(0, limit - used) if unit == "count" else max(0.0, limit - used)
        if limit > 0:
            pct = round(100.0 * used / limit, 1)
    row = {
        "key": key, "label": label, "scope": scope, "unit": unit,
        "used": used, "limit": limit, "remaining": remaining,
        "pct": pct, "status": status, "inert": inert, "note": note,
        "type": "quota",
    }
    if extra:
        row.update(extra)
    return row


def _config_row(key, label, configured, *, note=None, extra=None) -> dict:
    row = {
        "key": key, "label": label, "type": "config_only",
        "configured": configured, "status": "CONFIG",
        "note": note or "live counter in-process only",
        "used": None, "limit": None, "remaining": None, "pct": None, "inert": False,
    }
    if extra:
        row.update(extra)
    return row


def _window_row(key, label, window_str, inside: bool) -> dict:
    return {
        "key": key, "label": label, "type": "window",
        "configured": window_str, "status": "INSIDE" if inside else "OUTSIDE",
        "used": None, "limit": None, "remaining": None, "pct": None,
        "inert": False, "note": None,
    }


def _int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def build_capacity(cfg: dict, today: Optional[str] = None, now=None,
                   system_config: Optional[dict] = None,
                   trader_health: Optional[dict] = None) -> dict:
    now = now or freshness.ist_now()
    today = today or freshness.ist_today_iso(now)
    sc = system_config if system_config is not None else config_reader.get_system_config(cfg, today)
    if not isinstance(sc, dict):
        sc = {}

    risk = sc.get("risk", {}) or {}
    capital = sc.get("capital", {}) or {}
    sq = sc.get("signal_queue", {}) or {}
    ps = sc.get("position_sizing", {}) or {}
    sp = sc.get("signal_processor", {}) or {}
    wh = sc.get("webhook", {}) or {}
    ks = sc.get("kill_switch", {}) or {}
    dh = sc.get("drift_handler", {}) or {}
    scb = sc.get("strategy_circuit_breaker", {}) or {}
    cb = sc.get("circuit_breaker", {}) or {}
    rc = sc.get("order_reconciler", {}) or {}
    clk = sc.get("clock", {}) or {}
    lf = sc.get("live_feed", {}) or {}
    eg = sc.get("entry_gate", {}) or {}
    slc = (eg.get("slippage_control") or {}) if isinstance(eg, dict) else {}
    stg = sc.get("smart_tgt", {}) or {}
    force_intraday = bool(sc.get("force_intraday_only", True))

    opening = db_reader.opening_capital(cfg, today)
    cap_usage = db_reader.capital_usage(cfg, today)
    exposure = db_reader.exposure_extremes(cfg)

    # ── G2a primary rows #1-#8 (Dashboard widget contract preserved) ──
    daily_trades = _row("max_daily_trades", "Daily Trades", "global/day", "count",
                        db_reader.daily_trades_used(cfg, today), _int(risk.get("max_daily_trades")))
    open_pos = _row("max_open_positions", "Open Positions", "global/concurrent", "count",
                    db_reader.open_positions_count(cfg), _int(risk.get("max_open_positions")))

    loss_pct = _float(risk.get("daily_loss_limit_pct"))
    loss_used = round(db_reader.realized_loss_today(cfg, today), 2)
    if opening is not None and loss_pct is not None:
        daily_loss = _row("daily_loss_limit", "Daily Loss (₹)", "global/day", "rs",
                          loss_used, round(loss_pct * opening, 2),
                          extra={"limit_pct": loss_pct, "opening_capital": round(opening, 2)})
    else:
        daily_loss = _row("daily_loss_limit", "Daily Loss (₹)", "global/day", "rs",
                          loss_used, None, status="AWAITING",
                          note="opening capital not yet recorded",
                          extra={"limit_pct": loss_pct})

    intraday_pct = _float(capital.get("intraday_bucket_pct"))
    used_margin = round(cap_usage["margin_used"], 2)
    pending_margin = round(cap_usage["margin_reserved"], 2)
    if opening is not None and intraday_pct is not None:
        intraday_cap = _row("intraday_capital", "Intraday Capital (₹)", "global/instant", "rs",
                            used_margin, round(intraday_pct * opening, 2),
                            extra={"pending": pending_margin, "limit_pct": intraday_pct,
                                   "opening_capital": round(opening, 2)})
    else:
        intraday_cap = _row("intraday_capital", "Intraday Capital (₹)", "global/instant", "rs",
                            used_margin, None, status="AWAITING",
                            note="opening capital not yet recorded",
                            extra={"pending": pending_margin, "limit_pct": intraday_pct})

    consec = _row("max_consecutive_losses", "Consecutive Losses", "global/rolling", "count",
                  db_reader.consecutive_loss_streak(cfg), _int(risk.get("max_consecutive_losses")))

    th = trader_health if trader_health is not None else metrics_client.get_trader_health(cfg)
    q_limit = _int(sq.get("capacity"))
    bp = _float(sq.get("backpressure_pct"))
    bp_at = round(bp * q_limit) if (bp and q_limit) else None
    if th.get("trader_alive") and isinstance(th.get("health"), dict) and th["health"].get("queue_size") is not None:
        queue_row = _row("signal_queue", "Signal Queue", "global/instant", "count",
                         _int(th["health"].get("queue_size")) or 0, q_limit,
                         extra={"backpressure_at": bp_at})
    else:
        queue_row = _row("signal_queue", "Signal Queue", "global/instant", "count",
                         None, q_limit, status="UNAVAILABLE",
                         note="trader down — live queue depth unavailable",
                         extra={"backpressure_at": bp_at})

    deliv_open = _row("max_open_delivery_positions", "Delivery Open", "global/concurrent", "count",
                      db_reader.delivery_open_count(cfg), _int(risk.get("max_open_delivery_positions")),
                      inert=force_intraday,
                      note="inert (force_intraday_only)" if force_intraday else None)
    deliv_daily = _row("max_daily_delivery_trades", "Delivery Trades", "global/day", "count",
                       db_reader.delivery_daily_used(cfg, today), _int(risk.get("max_daily_delivery_trades")),
                       inert=force_intraday,
                       note="inert (force_intraday_only)" if force_intraday else None)

    primary = [daily_trades, open_pos, daily_loss, intraday_cap, consec,
               queue_row, deliv_open, deliv_daily]

    # ── G2b-1 completion rows ──
    # Orders group extras
    burst_max = _int(sp.get("entry_burst_max"))
    burst_win = _int(sp.get("entry_burst_window_sec")) or 60
    since_iso = (now - timedelta(seconds=burst_win)).isoformat()
    burst_row = _row("entry_burst", f"Entry Burst ({burst_win}s)", "global/rolling", "count",
                     db_reader.entries_in_window(cfg, since_iso), burst_max,
                     note="ENTRY orders placed in the rolling window (DB-derived)")
    qty_row = _row("max_single_order_qty", "Max Order Qty (today's max)", "per-order", "count",
                   db_reader.max_order_qty_today(cfg, today), _int(ps.get("max_single_order_qty")),
                   note="largest single order today vs the sanity cap")
    gap_row = _config_row("min_gap_between_entries", "Entry Gap / Symbol Cooldown",
                          {"min_gap_sec": _int(sp.get("min_gap_between_entries_sec")),
                           "per_symbol_cooldown_sec": _int(sp.get("per_symbol_cooldown_sec"))})
    hours = cfg.get("market_clock", {})
    entry_window_row = _window_row(
        "entry_window", "Entry Window",
        f"{hours.get('entry_start', '10:00')}–{hours.get('entry_end', '15:00')} IST",
        freshness.in_entry_window(cfg, now))
    market_window_row = _window_row(
        "market_window", "Market Session",
        f"{hours.get('market_open', '09:15')}–{hours.get('market_close', '15:30')} IST",
        freshness.market_is_open(cfg, now))

    # Capital group extras (exposure guards — DB-readable actuals, ₹ vs pct×opening)
    conc_pct = _float(ps.get("max_concentration_pct"))
    ws_name, ws_val = exposure["worst_symbol"]
    conc_row = _row("max_concentration", "Symbol Concentration (₹, worst)", "per-symbol", "rs",
                    round(ws_val, 2),
                    round(conc_pct * opening, 2) if (conc_pct and opening) else None,
                    status=None if (conc_pct and opening) else "AWAITING",
                    note=f"worst symbol: {ws_name}" if ws_name else "no open positions",
                    extra={"limit_pct": conc_pct})
    sect_pct = _float(risk.get("max_sector_exposure_pct"))
    sc_name, sc_val = exposure["worst_sector"]
    sector_row = _row("max_sector_exposure", "Sector Exposure (₹, worst)", "per-sector", "rs",
                      round(sc_val, 2),
                      round(sect_pct * opening, 2) if (sect_pct and opening) else None,
                      status=None if (sect_pct and opening) else "AWAITING",
                      note=f"worst sector: {sc_name}" if sc_name else "no open positions",
                      extra={"limit_pct": sect_pct})
    posval_pct = _float(ps.get("max_position_value_pct"))
    posval_row = _row("max_position_value", "Largest Position (₹)", "per-trade", "rs",
                      round(exposure["largest_position_value"], 2),
                      round(posval_pct * opening, 2) if (posval_pct and opening) else None,
                      status=None if (posval_pct and opening) else "AWAITING",
                      note="largest open position vs the anomaly cap",
                      extra={"limit_pct": posval_pct})

    # Risk group config-only rows
    kill_row = _config_row("kill_api_failure", "Kill-switch API-failure trip",
                           {"threshold": _int(ks.get("api_failure_threshold")),
                            "auto_trip": bool(ks.get("enable_auto_trip", True))},
                           extra={"kill_state": db_reader.get_kill_switch(cfg).get("state")})
    drift_row = _config_row("drift_thresholds", "Capital-drift escalation (₹)",
                            {"log_only": _float(dh.get("log_only_threshold_rs")),
                             "soft_kill": _float(dh.get("soft_kill_threshold_rs")),
                             "hard_kill": _float(dh.get("hard_kill_threshold_rs")),
                             "cycles": _int(dh.get("consecutive_cycles_before_escalate"))},
                            note="escalation state lives in the reconciler (in-process)")
    scb_row = _config_row("strategy_circuit_breaker", "Strategy Circuit Breaker",
                          {"loss_multiplier": _float(scb.get("loss_multiplier")),
                           "cutoff_time": scb.get("cutoff_time"),
                           "lookback_days": _int(scb.get("lookback_days")),
                           "enabled": bool(scb.get("enabled", False))})
    cb_row = _config_row("position_circuit_breaker", "Position Circuit Breaker",
                         {"partial_fill_timeout_min": _int(cb.get("partial_fill_timeout_minutes")),
                          "force_close_time": cb.get("force_close_time"),
                          "max_api_failures": _int(cb.get("max_api_failures"))})
    recon_row = _config_row("reconciler_tolerances", "Reconciler Drift Tolerances (₹)",
                            {"capital_drift": _float(rc.get("capital_drift_tolerance")),
                             "in_session_pct": _float(rc.get("capital_drift_tolerance_pct")),
                             "human_order_margin": _float(rc.get("human_order_margin_tolerance"))})

    # Strategy group
    tiers_row = _config_row("tier_multipliers", "Tier / Performance Multipliers",
                            {"tiers": ps.get("tier_multipliers"),
                             "dynamic_by_winrate": bool(ps.get("dynamic_by_winrate", False)),
                             "min_multiplier": _float(ps.get("min_multiplier")),
                             "max_multiplier": _float(ps.get("max_multiplier"))},
                            note="applied per-trade at sizing (PerformanceAllocator in-process)")
    per_strategy_row = _config_row("per_strategy_caps", "Per-strategy caps",
                                   {"where": "Strategy Tower → RISK group"},
                                   note="max_concurrent_positions + capital rendered per strategy")

    # System-guards group
    ip_row = _config_row("per_ip_rate_limit", "Webhook per-IP limiter",
                         {"burst": _int(wh.get("per_ip_burst")),
                          "refill_per_sec": _float(wh.get("per_ip_refill_per_sec")),
                          "enabled": bool(wh.get("per_ip_rate_limit_enabled", True))},
                         note="token bucket in-process; DB-visible proxy = 429s today",
                         extra={"rate_limited_today": db_reader.rate_limited_posts_today(cfg, today)})
    dedup_row = _config_row("dedup_window", "Webhook Dedup Window (s)",
                            {"seconds": _int(wh.get("dedup_window_seconds"))},
                            note="TTL cache in-process; pre-insert drops in webhook_audit only")
    clock_row = _config_row("clock_skew", "Clock-skew tiers (s)",
                            {"warn": _float(clk.get("warn_skew_sec")),
                             "alert": _float(clk.get("alert_skew_sec")),
                             "halt": _float(clk.get("halt_skew_sec"))})
    feed_row = _config_row("live_feed_reconnect", "Live-feed reconnect cap",
                           {"max_attempts": _int(lf.get("max_reconnect_attempts"))})
    slip_row = _config_row("slippage_budget", "Entry-slippage budget",
                           {"mode": slc.get("mode"),
                            "max_fraction_of_sl": _float(slc.get("max_slippage_fraction")),
                            "absolute_cap_rs": _float(slc.get("absolute_cap_rs")),
                            "hard_max_rs": _float(slc.get("hard_max_slippage_rs")),
                            "max_entry_slippage_pct": _float(eg.get("max_entry_slippage_pct"))},
                           note="per-entry guard; evaluated per order (in-process)")
    smart_row = _config_row("smart_tgt", "SmartTarget trailing",
                            {"enabled": bool(stg.get("enabled", False)),
                             "trigger_pct": _float(stg.get("trigger_pct")),
                             "step_pct": _float(stg.get("step_pct")),
                             "max_modify_failures": _int(stg.get("max_modify_failures"))},
                            note="per-trade trailing state in smart_tgt_state + in-process")

    groups = [
        {"name": "Orders", "rows": [daily_trades, burst_row, qty_row, gap_row,
                                    entry_window_row, market_window_row]},
        {"name": "Positions", "rows": [open_pos, deliv_open, deliv_daily]},
        {"name": "Capital", "rows": [intraday_cap, daily_loss, conc_row, sector_row, posval_row]},
        {"name": "Risk", "rows": [consec, kill_row, drift_row, scb_row, cb_row, recon_row]},
        {"name": "Strategy", "rows": [tiers_row, per_strategy_row]},
        {"name": "System-guards", "rows": [queue_row, ip_row, dedup_row, clock_row,
                                           feed_row, slip_row, smart_row]},
    ]

    return {
        "today": today,
        "config_source": sc.get("_source", "unknown"),
        "opening_capital": round(opening, 2) if opening is not None else None,
        "rows": primary,      # G2a dashboard-widget contract (8 rows)
        "groups": groups,     # G2b-1 capacity screen (all 30 inventory keys)
    }
