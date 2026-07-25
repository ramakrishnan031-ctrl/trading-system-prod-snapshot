"""
Risk & capital screens (M7-M10, all read-only, login_required):
  GET /api/risk      — daily loss / consec losses / kill switch / positions / open risk
  GET /api/capital   — opening / allocated / used / remaining + fm_ledger view
  GET /api/exposure  — gross + per-strategy + per-symbol concentration
  GET /api/pnl       — realized splits + intraday equity curve (fm_ledger)
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from ..auth import login_required
from ..readers import config_reader, db_reader
from ..services import freshness

risk_capital_api = Blueprint("risk_capital_api", __name__)


def _ctx():
    cfg = current_app.config["GUI_CONFIG"]
    today = freshness.ist_today_iso()
    return cfg, today


@risk_capital_api.route("/api/risk", methods=["GET"])
@login_required
def get_risk():
    cfg, today = _ctx()
    sc = config_reader.get_system_config(cfg, today)
    risk = (sc.get("risk") or {}) if isinstance(sc, dict) else {}
    opening = db_reader.opening_capital(cfg, today)
    loss_pct = risk.get("daily_loss_limit_pct")
    loss_limit = round(float(loss_pct) * opening, 2) if (loss_pct and opening) else None
    ks = db_reader.get_kill_switch(cfg)
    return jsonify({
        "today": today,
        # D2 (approved permanent): reads fm_ledger.RELEASE_USED.pnl_delta losses
        # directly, NOT get_daily_realized_net_pnl. (W10 — that reader's cost
        # double-subtract — was fixed 2026-07-17; it now returns a clean
        # SUM(pnl_delta). RELEASE_USED.pnl_delta stays the direct realized-loss source.)
        "daily_loss": {
            "used": db_reader.realized_loss_today(cfg, today),
            "limit": loss_limit, "limit_pct": loss_pct,
            "source_note": "fm_ledger RELEASE_USED losses (D2; W10 avoided)",
        },
        "consecutive_losses": {
            "used": db_reader.consecutive_loss_streak(cfg),
            "limit": risk.get("max_consecutive_losses"),
        },
        "kill_switch": {
            "state": ks.get("state"), "reason": ks.get("reason"),
            "since": ks.get("triggered_at"), "by": ks.get("triggered_by"),
            "halted": ks.get("state") in ("SOFT_KILL", "HARD_KILL"),
        },
        "open_positions": {
            "used": db_reader.open_positions_count(cfg),
            "limit": risk.get("max_open_positions"),
        },
        "open_risk_amount": db_reader.open_risk_amount_sum(cfg),
        "opening_capital": round(opening, 2) if opening else None,
    })


@risk_capital_api.route("/api/capital", methods=["GET"])
@login_required
def get_capital():
    cfg, today = _ctx()
    sc = config_reader.get_system_config(cfg, today)
    cap_cfg = (sc.get("capital") or {}) if isinstance(sc, dict) else {}
    opening = db_reader.opening_capital(cfg, today)
    usage = db_reader.capital_usage(cfg, today)
    intraday_pct = cap_cfg.get("intraday_bucket_pct")
    positional_pct = cap_cfg.get("positional_bucket_pct")
    allocated = round(float(intraday_pct) * opening, 2) if (intraday_pct and opening) else None
    used = round(usage["margin_used"], 2)
    return jsonify({
        "today": today,
        "opening_capital": round(opening, 2) if opening else None,   # today's FIRST INIT row
        "buckets": {
            "intraday_pct": intraday_pct, "positional_pct": positional_pct,
            "intraday_allocated": allocated,
        },
        "used": used,
        "pending": round(usage["margin_reserved"], 2),
        "remaining": round(allocated - used, 2) if allocated is not None else None,
        "deployed_pct": round(100.0 * used / opening, 1) if opening else None,
        "realized_pnl_today": round(usage["realized_pnl_today"], 2),
        "ledger": db_reader.ledger_entries(cfg, today),              # newest first
    })


@risk_capital_api.route("/api/exposure", methods=["GET"])
@login_required
def get_exposure():
    cfg, today = _ctx()
    opening = db_reader.opening_capital(cfg, today)
    usage = db_reader.capital_usage(cfg, today)
    data = db_reader.exposure_breakdown(cfg, top_n=10)
    data.update({
        "today": today,
        "opening_capital": round(opening, 2) if opening else None,
        "margin_used": round(usage["margin_used"], 2),
        "margin_vs_capital_pct": (round(100.0 * usage["margin_used"] / opening, 1)
                                  if opening else None),
        # G5b additive (Capital & Risk exposure zone): long/short/net split.
        "by_direction": db_reader.exposure_by_direction(cfg),
    })
    return jsonify(data)


@risk_capital_api.route("/api/pnl", methods=["GET"])
@login_required
def get_pnl():
    cfg, today = _ctx()
    session = db_reader.get_session_info(cfg)
    summary = db_reader.pnl_summary_today(cfg, today)
    return jsonify({
        "today": today,
        "mode": session.get("mode"),
        # trades carry no mode column — one DB is one mode; stated, not split.
        "mode_note": "single-mode DB: trades are not mode-tagged; "
                     "this DB's session mode is shown as data",
        "summary": summary,
        "equity_curve": db_reader.equity_curve_points(cfg, today),   # fm_ledger seq
        "curve_note": "cumulative fm_ledger RELEASE_USED.pnl_delta (realized only)",
        # B8/A9 (additive — G2a byte-compat precedent): closed trades detail.
        "closed_trades": db_reader.closed_trades_today(cfg, today),
    })
