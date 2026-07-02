"""
GET /api/dashboard — header/status view model:
trader_alive, mode, kill_switch, service_health[6 units], counters, freshness,
and the last-N events feed. One trader-health probe is shared with the summary.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from ..auth import login_required
from ..readers import db_reader, host_reader, metrics_client
from ..services import freshness, summary_bar

dashboard_api = Blueprint("dashboard_api", __name__)


@dashboard_api.route("/api/dashboard", methods=["GET"])
@login_required
def get_dashboard():
    cfg = current_app.config["GUI_CONFIG"]
    now = freshness.ist_now()
    today = freshness.ist_today_iso(now)
    trader_health = metrics_client.get_trader_health(cfg)

    summary = summary_bar.build_summary(cfg, today, now, trader_health=trader_health)
    return jsonify({
        "today": today,
        "summary": summary,
        "service_health": host_reader.all_units(cfg),
        "freshness": freshness.freshness_state(cfg, now),
        "events": db_reader.recent_events(cfg, limit=10),
    })
