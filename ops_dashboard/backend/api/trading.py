"""
Trading modules M2-M5 (all read-only, login_required, filtered + capped):
  GET /api/signals    ?date&scanner&strategy&family     (M2)
  GET /api/orders     ?date&leg&status&strategy&symbol  (M3)
  GET /api/positions                                    (M4, open-set trades)
  GET /api/holdings                                     (M5, gtt_state mirror)
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..auth import login_required
from ..readers import config_reader, db_reader
from ..services import freshness

trading_api = Blueprint("trading_api", __name__)

_FAMILIES = {"accepted", "rejected", "duplicated", "expired"}
_LEGS = {"ENTRY", "SL", "TGT", "EOD", "CO", "CANCEL"}


def _date_param() -> str:
    d = (request.args.get("date") or "").strip()
    # YYYY-MM-DD only; anything else falls back to today (no error surface).
    if len(d) == 10 and d[4] == "-" and d[7] == "-":
        return d
    return freshness.ist_today_iso()


@trading_api.route("/api/signals", methods=["GET"])
@login_required
def get_signals():
    cfg = current_app.config["GUI_CONFIG"]
    today = _date_param()
    family = (request.args.get("family") or "").strip().lower() or None
    if family and family not in _FAMILIES:
        family = None
    rows = db_reader.list_signals(
        cfg, today,
        scanner=(request.args.get("scanner") or "").strip() or None,
        strategy=(request.args.get("strategy") or "").strip() or None,
        family=family,
    )
    # Screen-04 shows TWO different quantities and they are not interchangeable:
    #   signal_score = this signal's own score  (screener_results.score)
    #   system_score = the minimum it had to reach (screener_results.eligible_score,
    #                  falling back to the configured min_pass_score)
    scores = db_reader.signal_scores(cfg, [r.get("signal_id") for r in rows])
    min_pass = config_reader.get_min_pass_score(cfg)
    for r in rows:
        s = scores.get(r.get("signal_id")) or {}
        r["signal_score"] = s.get("signal_score")
        sys_score = s.get("system_score")
        r["system_score"] = min_pass if sys_score is None else sys_score
        # Rejections show the score that was reached vs the score required.
        r["reject_score"] = r["signal_score"] if r.get("family") == "rejected" else None
        r["required_score"] = r["system_score"] if r.get("family") == "rejected" else None
    # Denominator strip (the ~82% pre-insert drop stays visible).
    funnel = db_reader.webhook_funnel(cfg, today)
    return jsonify({
        "date": today,
        "denominator": {
            "received": funnel["received"],
            "accepted": funnel["validated"],
            "rejected": funnel["rejected_total"],
            "duplicated_stored": db_reader.signals_duplicate_count(cfg, today),
            "stored": db_reader.signals_stored_count(cfg, today),
            "note": "received/accepted/rejected = webhook_audit aggregates "
                    "(pre-insert dupes inside rejected; per-signal dupe detail = W9)",
        },
        # Whole-day lifecycle counts for the KPI deck — computed over every
        # stored signal, NOT over the capped `rows` below.
        "counts": db_reader.signal_kpi_counts(cfg, today),
        "min_pass_score": min_pass,
        "row_cap": db_reader.list_signals_cap(),
        "count": len(rows),
        "rows": rows,
    })


@trading_api.route("/api/orders", methods=["GET"])
@login_required
def get_orders():
    cfg = current_app.config["GUI_CONFIG"]
    today = _date_param()
    leg = (request.args.get("leg") or "").strip().upper() or None
    if leg and leg not in _LEGS:
        leg = None
    rows = db_reader.list_orders(
        cfg, today, leg=leg,
        status=(request.args.get("status") or "").strip().upper() or None,
        strategy=(request.args.get("strategy") or "").strip() or None,
        symbol=(request.args.get("symbol") or "").strip().upper() or None,
    )
    return jsonify({"date": today, "count": len(rows), "rows": rows})


@trading_api.route("/api/positions", methods=["GET"])
@login_required
def get_positions():
    cfg = current_app.config["GUI_CONFIG"]
    today = freshness.ist_today_iso()
    session = db_reader.get_session_info(cfg)
    rows = db_reader.open_positions_list(cfg)
    # G5d additive: scanner attribution (system side). Broker MTM/LTP/RR stay
    # UNAVAILABLE (two-state) — never inferred.
    scanners = db_reader.scanner_for_trades(cfg, [r.get("trade_id") for r in rows])
    for r in rows:
        r["scanner"] = scanners.get(r.get("trade_id")) or "—"
    sc = config_reader.get_system_config(cfg, today)
    max_open = None
    if isinstance(sc, dict):
        try:
            v = (sc.get("risk") or {}).get("max_open_positions")
            max_open = int(v) if v is not None else None
        except (TypeError, ValueError):
            max_open = None
    return jsonify({
        "mode": session.get("mode"),          # data attribute, never a branch
        "count": len(rows),
        "max_open_positions": max_open,
        "open_states": list(db_reader.OPEN_STATES),    # contract-tested constant
        "unrealized_note": "G4",              # unrealized column renders '—' (tooltip G4)
        # G5d two-state: the broker-derived fields are honestly UNAVAILABLE.
        "unavailable": {"fields": ["ltp", "mtm", "unrealized", "current_rr"],
                        "reason": "Pending Broker Source (G4)"},
        "rows": rows,
    })


@trading_api.route("/api/holdings", methods=["GET"])
@login_required
def get_holdings():
    cfg = current_app.config["GUI_CONFIG"]
    rows = db_reader.holdings_list(cfg)
    return jsonify({
        "banner": "Broker is authority — local mirror; delivery parked (Slice 2.5)",
        "count": len(rows),
        "rows": rows,
    })
