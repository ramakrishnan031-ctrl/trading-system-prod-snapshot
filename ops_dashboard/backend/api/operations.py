"""
G5d — Operations/Investigation endpoints (read-only, login_required, ADDITIVE —
new routes, no existing endpoint touched):
  GET /api/controls-summary                 (read-only Controls, L4 — ZERO write path)
  GET /api/trade-logs   ?period&from&to&strategy&scanner&exit_reason
  GET /api/activity     ?limit               (the merged attention feed, L10)
All compose read-only over EXISTING tables (mode=ro); NO schema. Broker-side data
is never assembled — it stays honestly UNAVAILABLE (G-1/P1).
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..auth import login_required
from ..services import operations

operations_api = Blueprint("operations_api", __name__)


@operations_api.route("/api/controls-summary", methods=["GET"])
@login_required
def get_controls_summary():
    cfg = current_app.config["GUI_CONFIG"]
    return jsonify(operations.build_controls_summary(cfg))


@operations_api.route("/api/trade-logs", methods=["GET"])
@login_required
def get_trade_logs():
    cfg = current_app.config["GUI_CONFIG"]
    p = (request.args.get("period") or "week").strip().lower()
    if p not in ("today", "week", "month", "custom"):
        p = "week"
    return jsonify(operations.build_trade_logs(
        cfg, p,
        from_date=(request.args.get("from") or "").strip() or None,
        to_date=(request.args.get("to") or "").strip() or None,
        strategy=(request.args.get("strategy") or "").strip() or None,
        scanner=(request.args.get("scanner") or "").strip() or None,
        exit_reason=(request.args.get("exit_reason") or "").strip().upper() or None,
    ))


@operations_api.route("/api/activity", methods=["GET"])
@login_required
def get_activity():
    cfg = current_app.config["GUI_CONFIG"]
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
    return jsonify(operations.build_activity(cfg, limit=limit))
