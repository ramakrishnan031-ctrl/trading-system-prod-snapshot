"""GET /api/pipeline — 13 stage cards + halt rail."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from ..auth import login_required
from ..services import pipeline_state

pipeline_api = Blueprint("pipeline_api", __name__)


@pipeline_api.route("/api/pipeline", methods=["GET"])
@login_required
def get_pipeline():
    cfg = current_app.config["GUI_CONFIG"]
    return jsonify(pipeline_state.build_pipeline(cfg))
