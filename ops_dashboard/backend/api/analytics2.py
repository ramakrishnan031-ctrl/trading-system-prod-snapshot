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
  GET /api/analytics/execution  ?period&from&to&…      (distinct from today-scoped /api/execution)
  GET /api/export/execution     ?period&from&to&…      XLSX of the SAME filtered set
  GET /api/system-health        ?status&kind          live snapshot (⛔ no period)
  GET /api/export/system-health ?status&kind          XLSX of the SAME view
Every builder is read-only over db_reader.*_range (mode=ro); NO schema. The
existing /api/pnl, /api/strategies, /api/slippage, /api/execution, Reports stay
UNTOUCHED.
"""
from __future__ import annotations

import io

from flask import Blueprint, current_app, jsonify, request, send_file

from ..auth import login_required
from ..services import (audit, analytics_period, execution_analytics,
                        slippage_analytics, system_health)


def _xlsx(sheets: list, download_name: str):
    """Write [(title, header, rows)] to a workbook and send it. Shared by the
    Screen-10 and Screen-11 exports so the two cannot drift in shape."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for title, header, rows in sheets:
        ws = wb.create_sheet(title=title)
        ws.append(header)
        for row in rows:
            ws.append(row)
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True, download_name=download_name,
        mimetype=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
    )

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
    cfg = current_app.config["GUI_CONFIG"]
    p, frm, to = _period_args()
    payload = slippage_analytics.build_slippage_analytics(
        cfg, p, frm, to, **_slippage_kwargs())
    return _xlsx(slippage_analytics.export_sheets(payload),
                 "slippage_%s_to_%s.xlsx" % (payload["from"], payload["to"]))


# ─────────────────────────────────────────────────────────────────────────────
# Screen-11 Execution Analytics (15-Aug-2026). ADDITIVE — the existing today-
# scoped `/api/execution` in api/analytics.py is UNTOUCHED and still serves its
# own contract, exactly as `/api/slippage` survived `/api/analytics/slippage`.
# ─────────────────────────────────────────────────────────────────────────────

def _execution_kwargs() -> dict:
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
        "status": _arg("status", upper=True),
    }


@analytics2_api.route("/api/analytics/execution", methods=["GET"])
@login_required
def get_analytics_execution():
    """Screen 11. Read-only; every filter is optional and narrows ONE population
    that all panels share."""
    cfg = current_app.config["GUI_CONFIG"]
    p, frm, to = _period_args()
    return jsonify(execution_analytics.build_execution_analytics(
        cfg, p, frm, to, **_execution_kwargs()))


@analytics2_api.route("/api/export/execution", methods=["GET"])
@login_required
def export_execution():
    """XLSX of the FILTERED Screen-11 result set — executions, delay summary,
    both rankings, the distribution and an overview.

    ⭐ It calls the SAME builder the screen calls, with the SAME arguments, so no
    sheet can disagree with the table or with another sheet. ⭐ The Delay Summary
    sheet carries the NOT-INSTRUMENTED rows verbatim: a spreadsheet that quietly
    omitted them would let a reader total the measured stages and believe they
    account for the whole lifecycle.
    """
    cfg = current_app.config["GUI_CONFIG"]
    p, frm, to = _period_args()
    payload = execution_analytics.build_execution_analytics(
        cfg, p, frm, to, **_execution_kwargs())
    return _xlsx(execution_analytics.export_sheets(payload),
                 "execution_%s_to_%s.xlsx" % (payload["from"], payload["to"]))


# ─────────────────────────────────────────────────────────────────────────────
# Screen-12 System Health (15-Aug-2026). ADDITIVE — the existing `/api/services`
# and `/api/vm` in api/system.py are UNTOUCHED and keep their own contracts.
#
# ⚠️ NOT under /api/analytics/: this screen is a LIVE OPERATIONAL SNAPSHOT, not a
# period-scoped analytic. It takes no `period`, and giving it one would invite a
# reader to believe the health of an hour ago is retrievable — it is not.
# ─────────────────────────────────────────────────────────────────────────────

def _health_kwargs() -> dict:
    def _arg(name, upper=False):
        v = (request.args.get(name) or "").strip()
        return (v.upper() if upper else v) or None

    return {"status_filter": _arg("status", upper=True), "kind_filter": _arg("kind")}


@analytics2_api.route("/api/system-health", methods=["GET"])
@login_required
def get_system_health():
    """Screen 12. Read-only; a live snapshot of infrastructure health."""
    cfg = current_app.config["GUI_CONFIG"]
    return jsonify(system_health.build_system_health(cfg, **_health_kwargs()))


@analytics2_api.route("/api/export/system-health", methods=["GET"])
@login_required
def export_system_health():
    """XLSX of the CURRENT view — services, readiness, dependencies, alerts,
    auto-recovery, events and a summary.

    ⭐ Same builder, same arguments, so the Services sheet carries exactly the
    rows the table shows under the current filter. ⭐ The Summary sheet spells
    out `NOT INSTRUMENTED` for CPU / RAM / Network with their reasons: a sheet
    that left them blank would let a reader assume the value was simply zero.
    """
    cfg = current_app.config["GUI_CONFIG"]
    payload = system_health.build_system_health(cfg, **_health_kwargs())
    return _xlsx(system_health.export_sheets(payload),
                 "system_health_%s.xlsx" % payload["today"])


# ─────────────────────────────────────────────────────────────────────────────
# SCREEN 13 — AUDIT
# ⛔ The pre-existing `/api/audit` (system.py) is left UNTOUCHED — it is a
# today-only 4-field feed with its own callers and its own tests. This is an
# ADDITIVE endpoint for the approved Audit screen.
# ─────────────────────────────────────────────────────────────────────────────
def _audit_kwargs() -> dict:
    def _arg(name):
        return (request.args.get(name) or "").strip() or None

    return {"start": _arg("start"), "end": _arg("end"),
            "category": _arg("category"), "action": _arg("action"),
            "status": _arg("status"), "module": _arg("module"),
            "user": _arg("user"), "q": _arg("q"),
            "bucket": _arg("bucket") or "7d"}


@analytics2_api.route("/api/audit-screen", methods=["GET"])
@login_required
def get_audit_screen():
    """Screen 13. Read-only. ONE filtered population feeds the KPIs, the table,
    the donut, the line chart, Top Actors, Critical Changes and the export."""
    cfg = current_app.config["GUI_CONFIG"]
    return jsonify(audit.build_audit(cfg, **_audit_kwargs()))


@analytics2_api.route("/api/audit-detail", methods=["GET"])
@login_required
def get_audit_detail():
    """The approved Audit Details panel for ONE record, with its timeline.

    ⭐ Resolved from the SAME filtered build as the table, so a Reference ID
    always addresses the row the operator actually clicked.
    """
    cfg = current_app.config["GUI_CONFIG"]
    ref = (request.args.get("ref_id") or "").strip()
    payload = audit.build_audit(cfg, **_audit_kwargs())
    rec = audit.record_detail(payload, ref)
    if rec is None:
        return jsonify({"error": "unknown reference id", "ref_id": ref}), 404
    return jsonify(rec)


@analytics2_api.route("/api/export/audit-screen", methods=["GET"])
@login_required
def export_audit_screen():
    """XLSX of the FILTERED view only (approved: "Export filtered results only").

    ⭐ Same builder, same arguments ⇒ the exported rows are the table's rows.
    ⛔ Unrecorded fields export as NOT INSTRUMENTED, never as a blank cell.
    """
    cfg = current_app.config["GUI_CONFIG"]
    payload = audit.build_audit(cfg, **_audit_kwargs())
    return _xlsx(audit.export_sheets(payload),
                 "audit_%s_%s.xlsx" % (payload["range"]["start"],
                                       payload["range"]["end"]))
