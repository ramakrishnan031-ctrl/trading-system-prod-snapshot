"""
G5c — NEW analytics endpoints (all read-only, login_required, ADDITIVE — they add
routes and never touch any existing endpoint):
  GET /api/strategy-ranking     ?period&from&to
  GET /api/strategy-health      ?period&from&to
  GET /api/scanner-attribution  ?period&from&to
  GET /api/trades               ?period&from&to&strategy&direction&symbol   (Trade Explorer)
  GET /api/trade-story/<trade_id>
  GET /api/analytics/pnl        ?period&from&to        (distinct from today-scoped /api/pnl)
Every builder is read-only over db_reader.*_range (mode=ro); NO schema. The
existing /api/pnl, /api/strategies, /api/slippage, Reports stay UNTOUCHED.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..auth import login_required
from ..services import analytics_period

analytics2_api = Blueprint("analytics2_api", __name__)

_PERIODS = {"today", "week", "month", "custom"}


def _period_args():
    p = (request.args.get("period") or "today").strip().lower()
    if p not in _PERIODS:
        p = "today"
    frm = (request.args.get("from") or "").strip() or None
    to = (request.args.get("to") or "").strip() or None
    return p, frm, to


@analytics2_api.route("/api/strategy-ranking", methods=["GET"])
@login_required
def get_strategy_ranking():
    cfg = current_app.config["GUI_CONFIG"]
    p, frm, to = _period_args()
    return jsonify(analytics_period.build_strategy_ranking(cfg, p, frm, to))


@analytics2_api.route("/api/strategy-health", methods=["GET"])
@login_required
def get_strategy_health():
    cfg = current_app.config["GUI_CONFIG"]
    p, frm, to = _period_args()
    return jsonify(analytics_period.build_strategy_health(cfg, p, frm, to))


@analytics2_api.route("/api/scanner-attribution", methods=["GET"])
@login_required
def get_scanner_attribution():
    cfg = current_app.config["GUI_CONFIG"]
    p, frm, to = _period_args()
    return jsonify(analytics_period.build_scanner_attribution(cfg, p, frm, to))


@analytics2_api.route("/api/trades", methods=["GET"])
@login_required
def get_trades():
    cfg = current_app.config["GUI_CONFIG"]
    p, frm, to = _period_args()
    return jsonify(analytics_period.build_trade_explorer(
        cfg, p, frm, to,
        strategy=(request.args.get("strategy") or "").strip() or None,
        direction=(request.args.get("direction") or "").strip().upper() or None,
        symbol=(request.args.get("symbol") or "").strip().upper() or None,
    ))


@analytics2_api.route("/api/trade-story/<trade_id>", methods=["GET"])
@login_required
def get_trade_story(trade_id: str):
    cfg = current_app.config["GUI_CONFIG"]
    story = analytics_period.build_trade_story(cfg, trade_id)
    if story is None:
        return jsonify({"error": f"unknown trade: {trade_id}"}), 404
    return jsonify(story)


@analytics2_api.route("/api/analytics/pnl", methods=["GET"])
@login_required
def get_analytics_pnl():
    cfg = current_app.config["GUI_CONFIG"]
    p, frm, to = _period_args()
    return jsonify(analytics_period.build_pnl_analytics(cfg, p, frm, to))
