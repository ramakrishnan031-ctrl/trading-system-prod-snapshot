"""
G5c — NEW analytics endpoints (all read-only, login_required, ADDITIVE — they add
routes and never touch any existing endpoint):
  GET /api/strategy-ranking     ?period&from&to
  GET /api/strategy-health      ?period&from&to
  GET /api/scanner-attribution  ?period&from&to
  GET /api/trades               ?period&from&to&strategy&direction&symbol   (Trade Explorer)
  GET /api/trade-story/<trade_id>
  GET /api/analytics/pnl        ?period&from&to        (distinct from today-scoped /api/pnl)
  GET /api/analytics/slippage   ?period&from&to&…      (distinct from today-scoped /api/slippage)
  GET /api/export/slippage      ?period&from&to&…      XLSX of the SAME filtered set
Every builder is read-only over db_reader.*_range (mode=ro); NO schema. The
existing /api/pnl, /api/strategies, /api/slippage, Reports stay UNTOUCHED.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..auth import login_required
from ..services import analytics_period, slippage_analytics

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
    """Screen 09. Read-only; every filter is optional and narrows ONE population
    that all panels share. ⛔ No scanner filter — strategy IS the scanner identity
    in this system (Rama, 14-Aug), so exposing both duplicated the same fact."""
    cfg = current_app.config["GUI_CONFIG"]
    p, frm, to = _period_args()

    def _arg(name, upper=False):
        v = (request.args.get(name) or "").strip()
        return (v.upper() if upper else v) or None

    return jsonify(analytics_period.build_pnl_analytics(
        cfg, p, frm, to,
        strategy=_arg("strategy"),
        symbol=_arg("symbol", upper=True),
        trade_type=_arg("trade_type", upper=True),
        direction=_arg("direction", upper=True),
        attribution_dim=(_arg("attr") or "strategy"),
        compare=_arg("compare"),
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Screen-10 Slippage Analytics (15-Aug-2026). ADDITIVE — the existing today-
# scoped `/api/slippage` in api/analytics.py is UNTOUCHED and still serves its
# own contract, exactly as `/api/pnl` survived `/api/analytics/pnl`.
# ─────────────────────────────────────────────────────────────────────────────

def _slippage_kwargs() -> dict:
    """The screen's filter set, parsed once so the JSON endpoint and the XLSX
    export cannot drift apart. ⛔ No scanner filter — strategy IS the scanner
    identity in this system (Rama, 14-Aug)."""
    def _arg(name, upper=False):
        v = (request.args.get(name) or "").strip()
        return (v.upper() if upper else v) or None

    return {
        "strategy": _arg("strategy"),
        "symbol": _arg("symbol", upper=True),
        "trade_type": _arg("trade_type", upper=True),
        "direction": _arg("direction", upper=True),
        "price_bucket": _arg("price_bucket"),
        "status": _arg("status", upper=True),
    }


@analytics2_api.route("/api/analytics/slippage", methods=["GET"])
@login_required
def get_analytics_slippage():
    """Screen 10. Read-only; every filter is optional and narrows ONE population
    that all panels share."""
    cfg = current_app.config["GUI_CONFIG"]
    p, frm, to = _period_args()
    return jsonify(slippage_analytics.build_slippage_analytics(
        cfg, p, frm, to, **_slippage_kwargs()))


@analytics2_api.route("/api/export/slippage", methods=["GET"])
@login_required
def export_slippage():
    """XLSX of the FILTERED Screen-10 result set — details, price buckets, both
    rankings and a summary, each on its own sheet.

    ⭐ It calls the SAME builder the screen calls, with the SAME arguments, and
    writes what that ONE payload returned — so no sheet can disagree with the
    table, or with another sheet, about what "filtered" means. ⛔ Not a second
    query with its own filter code, which is exactly how an export starts
    telling a different story from the screen.
    """
    import io

    from flask import send_file
    from openpyxl import Workbook

    cfg = current_app.config["GUI_CONFIG"]
    p, frm, to = _period_args()
    payload = slippage_analytics.build_slippage_analytics(
        cfg, p, frm, to, **_slippage_kwargs())

    wb = Workbook()
    wb.remove(wb.active)
    for title, header, rows in slippage_analytics.export_sheets(payload):
        ws = wb.create_sheet(title=title)
        ws.append(header)
        for row in rows:
            ws.append(row)
        ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True,
        download_name="slippage_%s_to_%s.xlsx" % (payload["from"], payload["to"]),
        mimetype=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
    )
