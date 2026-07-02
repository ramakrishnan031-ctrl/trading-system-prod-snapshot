"""GET /api/capacity — limit/used/remaining rows (STEP-0 inventory #1-#8)."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from ..auth import login_required
from ..services import capacity as capacity_svc

capacity_api = Blueprint("capacity_api", __name__)


@capacity_api.route("/api/capacity", methods=["GET"])
@login_required
def get_capacity():
    cfg = current_app.config["GUI_CONFIG"]
    return jsonify(capacity_svc.build_capacity(cfg))
