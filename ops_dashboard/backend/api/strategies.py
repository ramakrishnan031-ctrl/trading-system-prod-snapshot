"""
GET /api/strategies         — Strategy Control Tower (all strategies, rankings,
                              scanner-level unattributable rows).
GET /api/strategies/<name>  — single-strategy detail (same row shape + per-view ranks).
Replaces G2a /api/strategy_panel (strategy_panel evolved into strategy_tower).
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from ..auth import login_required
from ..services import strategy_tower

strategies_api = Blueprint("strategies_api", __name__)


@strategies_api.route("/api/strategies", methods=["GET"])
@login_required
def get_strategies():
    cfg = current_app.config["GUI_CONFIG"]
    return jsonify(strategy_tower.build_strategy_tower(cfg))


@strategies_api.route("/api/strategies/<name>", methods=["GET"])
@login_required
def get_strategy_detail(name: str):
    cfg = current_app.config["GUI_CONFIG"]
    detail = strategy_tower.strategy_detail(cfg, name)
    if detail is None:
        return jsonify({"error": f"unknown strategy: {name}"}), 404
    return jsonify(detail)
