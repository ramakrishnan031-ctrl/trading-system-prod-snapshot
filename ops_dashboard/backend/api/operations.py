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

from flask import Blueprint, current_app, jsonify, request, session

from ..auth import login_required
from ..readers import control_client
from ..services import controls, operations

operations_api = Blueprint("operations_api", __name__)


@operations_api.route("/api/controls-summary", methods=["GET"])
@login_required
def get_controls_summary():
    cfg = current_app.config["GUI_CONFIG"]
    return jsonify(operations.build_controls_summary(cfg))


# ─────────────────────────────────────────────────────────────────────────────
# SCREEN 17 CONTROLS (17-Aug-2026) — ADDITIVE. ⛔ `/api/controls-summary` above
# is UNTOUCHED; its contract is pinned by test_g5d_operations.
#
# 📜 Rama, 17-Aug: Screen 17 is the operational control surface. L4's read-only
# interpretation is superseded. Design authority:
# docs/decisions/CONTROL_PLANE_DESIGN_17-Aug-2026.md
#
# ⛔ THE DASHBOARD STILL WRITES NOTHING ITSELF. It validates, then forwards to the
# trading process's loopback control plane, and renders the state that comes
# BACK. There is no DB write and no production import anywhere in this path.
# ─────────────────────────────────────────────────────────────────────────────
@operations_api.route("/api/controls/screen", methods=["GET"])
@login_required
def get_controls_screen():
    cfg = current_app.config["GUI_CONFIG"]
    return jsonify(controls.build_controls_screen(cfg))


# An ALLOWLIST, ⛔ never a path passthrough — a proxy that forwards arbitrary
# paths to a plane that can halt trading is an open relay onto the kill switch.
_CONTROL_ACTIONS = {
    "entries.pause":   "/control/entries/pause",
    "entries.resume":  "/control/entries/resume",
    "trading.stop":    "/control/full-stop",
    "strategy.toggle": "/control/strategy",
    "limits.update":   "/control/limits",
}


@operations_api.route("/api/controls/action", methods=["POST"])
@login_required
def post_controls_action():
    cfg = current_app.config["GUI_CONFIG"]
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "")
    if action not in _CONTROL_ACTIONS:
        return jsonify({"ok": False, "error": f"unknown action {action!r}"}), 400

    payload = dict(body.get("payload") or {})
    payload.setdefault("actor", (session.get("user") if session else None) or "dashboard")

    result = control_client.post_action(cfg, _CONTROL_ACTIONS[action], payload)

    # ⛔ UNREACHABLE ≠ REFUSED. A plane that never answered must NOT be reported
    # as a declined action, and neither may be reported as success.
    if not result.get("available"):
        return jsonify({"ok": False, "applied": False, "reachable": False,
                        "error": result.get("reason")}), 503
    upstream = result.get("body") or {}
    status = int(result.get("status") or 200)
    return jsonify({
        "ok": bool(upstream.get("ok")) and status == 200,
        "applied": status == 200,
        "reachable": True,
        "upstream_status": status,
        "action": upstream.get("action"),
        "state": upstream.get("state"),
        "error": upstream.get("error") or upstream.get("detail"),
    }), (200 if status == 200 else status)


@operations_api.route("/api/export/controls-screen", methods=["GET"])
@login_required
def export_controls_screen():
    """XLSX of the control data this screen is currently showing (artwork panel
    "EXPORT — Export to XLSX").

    ⭐ SAME builder, SAME arguments as `/api/controls/screen`, so an exported row
    can never disagree with the row on screen.
    ⭐ Reuses the established `_xlsx` writer shared by the other screen exports —
    ⛔ no second export architecture is introduced.
    ⛔ READ-ONLY: this composes the same payload and writes a workbook. It does
    not contact the control plane and cannot operate any control.
    """
    from .analytics2 import _xlsx          # the one shared workbook writer

    cfg = current_app.config["GUI_CONFIG"]
    payload = controls.build_controls_screen(cfg)
    return _xlsx(controls.export_sheets(payload),
                 "controls_%s.xlsx" % (payload.get("today") or "unknown"))


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
