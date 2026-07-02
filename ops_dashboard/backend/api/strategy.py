"""GET /api/strategy_panel — per-strategy today (enabled, signals, trades, P&L, rank)."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from ..auth import login_required
from ..services import strategy_panel

strategy_api = Blueprint("strategy_api", __name__)


@strategy_api.route("/api/strategy_panel", methods=["GET"])
@login_required
def get_strategy_panel():
    cfg = current_app.config["GUI_CONFIG"]
    return jsonify(strategy_panel.build_strategy_panel(cfg))
