"""
GET /api/strategies         — Strategy Control Tower (all strategies, rankings,
                              scanner-level unattributable rows).
GET /api/strategies/<name>  — single-strategy detail (same row shape + per-view ranks).
GET /api/export/strategies   — XLSX of the SAME tower, through the SAME four filters.
Replaces G2a /api/strategy_panel (strategy_panel evolved into strategy_tower).
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..auth import login_required
from ..services import strategy_tower
from .analytics2 import _xlsx          # the one shared workbook writer

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


@strategies_api.route("/api/export/strategies", methods=["GET"])
@login_required
def export_strategies():
    """Screen 03, `03. Strategies.txt` — EXPORT: "Download XLSX".

    ⭐ SAME BUILDER, SAME FILTERS ⇒ the exported rows ARE the table's rows, in
    the table's order. The screen filters client-side over the whole tower, so
    the four filter names are accepted here and applied to the same values.
    ⛔ No second query and no second derivation: an export that re-reads the DB
    is how an export starts disagreeing with the screen it claims to be.
    """
    cfg = current_app.config["GUI_CONFIG"]
    payload = strategy_tower.build_strategy_tower(cfg)

    def _arg(name):
        return (request.args.get(name) or "").strip() or None

    rows = strategy_tower.export_rows(
        payload, strategy=_arg("strategy"), status=_arg("status"),
        trade_type=_arg("trade_type"), direction=_arg("direction"))
    return _xlsx([("Strategies", rows[0], rows[1:])],
                 "strategies_%s.xlsx" % (payload.get("today") or "unknown"))
