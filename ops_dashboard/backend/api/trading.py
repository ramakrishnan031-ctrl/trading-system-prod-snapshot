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


@trading_api.route("/api/orders/screen", methods=["GET"])
@login_required
def get_orders_screen():
    """Screen-05 Orders. ADDITIVE — /api/orders above is untouched.

    Row grain is the ENTRY order (see db_reader.order_screen_rows). Prices are
    SYSTEM prices only; Order Value is the ENTRY order value only.
    """
    cfg = current_app.config["GUI_CONFIG"]
    today = _date_param()
    rows = db_reader.order_screen_rows(cfg, today)
    ctx = db_reader.order_exec_context(cfg, [r.get("order_id") for r in rows])
    # Same two score quantities Screen-04 shows, from the same reader — ⛔ no
    # scoring logic is invented here:
    #   signal_score = this signal's own score (screener_results.score)
    #   system_score = the minimum it had to reach (eligible_score, falling back
    #                  to the configured min_pass_score)
    scores = db_reader.signal_scores(cfg, [r.get("signal_id") for r in rows])
    min_pass = config_reader.get_min_pass_score(cfg)
    for r in rows:
        r["exec"] = ctx.get(str(r.get("order_id"))) or None
        sc = scores.get(r.get("signal_id")) or {}
        r["signal_score"] = sc.get("signal_score")
        sys_score = sc.get("system_score")
        r["system_score"] = min_pass if sys_score is None else sys_score
    return jsonify({
        "date": today,
        "count": len(rows),
        "rows": rows,
        "kpis": db_reader.order_kpis(cfg, today),
        "row_cap": db_reader.list_signals_cap(),
    })


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


# ─────────────────────────────────────────────────────────────────────────────
# Screen-06 Positions (12-Aug-2026). ADDITIVE — /api/positions above is
# UNTOUCHED, and its G4 "unavailable" contract is the same one this screen
# honours rather than quietly working around.
# ─────────────────────────────────────────────────────────────────────────────

def _position_filters() -> dict:
    """The filter set Screen-06 exposes. ⛔ NO SCANNER — Strategy carries that
    relationship, so there is no scanner column, filter or detail row."""
    g = lambda k: (request.args.get(k) or "").strip()  # noqa: E731
    return {
        "strategy": g("strategy"),
        "symbol": g("symbol"),
        "trade_type": g("trade_type"),
        "direction": g("direction").upper(),
        "position_status": g("position_status"),
    }


def _apply_position_filters(rows: list, f: dict) -> list:
    """Server-side twin of the client's view(). ⭐ EXPORT AND TABLE MUST AGREE:
    the export route runs THIS function over the same rows, so 'Export
    represents the filtered result set' is true by construction rather than by
    two implementations that drift."""
    out = rows
    if f.get("strategy"):
        out = [r for r in out if r.get("strategy") == f["strategy"]]
    if f.get("symbol"):
        out = [r for r in out if r.get("symbol") == f["symbol"]]
    if f.get("trade_type"):
        out = [r for r in out if r.get("trade_type") == f["trade_type"]]
    if f.get("direction"):
        out = [r for r in out
               if str(r.get("direction") or "").upper() == f["direction"]]
    if f.get("position_status"):
        out = [r for r in out if r.get("position_status") == f["position_status"]]
    return out


def _position_rows_enriched(cfg, today: str) -> list:
    """Rows + broker-standing exits + excursions + the two score quantities.

    ⛔ Every enrichment is a LEFT-join in spirit: a row with no exit legs, no
    excursion row or no score keeps None and the UI renders an em-dash. Nothing
    is defaulted to zero, which would read as a measured value.
    """
    rows = db_reader.position_screen_rows(cfg, today)
    tids = [r.get("trade_id") for r in rows]
    exits = db_reader.position_broker_exits(cfg, tids)
    exc = db_reader.position_excursions(cfg, tids)

    # Same two score quantities Screens 04/05 show, from the same reader —
    # ⛔ no scoring logic is invented here.
    scores = db_reader.signal_scores(cfg, [r.get("signal_id") for r in rows])
    min_pass = config_reader.get_min_pass_score(cfg)

    # R:R IS STRATEGY-CONFIGURED, ⛔ NOT DERIVED FROM PRICES (spreadsheet note 4:
    # "R:R — refers to ratio as per each strategy.yaml wise"). Read from
    # config/strategies/*.yaml → `tgt_risk_reward`. ⛔ A strategy without one
    # yields None and renders '—'; ⛔ we never fall back to a price-derived
    # ratio, which would silently answer a different question.
    # ⛔ NOT reversed for SHORT — the configured ratio is the configured ratio.
    strategies = config_reader.get_strategies(cfg)

    for r in rows:
        tid = str(r.get("trade_id"))
        bx = exits.get(tid) or {}
        r["sl_broker"] = bx.get("sl_broker")
        r["sl_broker_status"] = bx.get("sl_broker_status")
        r["tgt_broker"] = bx.get("tgt_broker")
        r["tgt_broker_status"] = bx.get("tgt_broker_status")

        ex = exc.get(tid) or {}
        r["mfe_pct"] = ex.get("mfe_pct")
        r["mae_pct"] = ex.get("mae_pct")

        sc = scores.get(r.get("signal_id")) or {}
        r["signal_score"] = sc.get("signal_score")
        sys_score = sc.get("system_score")
        r["system_score"] = min_pass if sys_score is None else sys_score

        rr = (strategies.get(r.get("strategy")) or {}).get("tgt_risk_reward")
        try:
            r["rr_configured"] = float(rr) if rr is not None else None
        except (TypeError, ValueError):
            r["rr_configured"] = None
    return rows


@trading_api.route("/api/positions/screen", methods=["GET"])
@login_required
def get_positions_screen():
    """Screen-06 Positions. ADDITIVE — /api/positions is untouched.

    Row grain is the TRADE (a position). Prices are split SYSTEM vs BROKER:
    Entry (System/Filled) and SL/TGT (System/Broker). ⛔ There is no filled
    SL/TGT execution price anywhere in the schema and none is synthesised —
    see db_reader.position_broker_exits for the measurement.
    """
    cfg = current_app.config["GUI_CONFIG"]
    today = _date_param()
    rows = _position_rows_enriched(cfg, today)
    return jsonify({
        "date": today,
        "count": len(rows),
        "rows": rows,
        "kpis": db_reader.position_kpis(cfg, today),
        "summary": db_reader.position_summary(cfg, today),
        "statuses": list(db_reader.POSITION_STATUSES),
        "row_cap": db_reader.list_signals_cap(),
        # The SAME honest contract /api/positions publishes. Repeated here so a
        # consumer of THIS endpoint cannot miss it.
        "unavailable": {
            "fields": ["ltp", "last_updated", "current_value", "unrealized_pnl",
                       "unrealized_pct", "mtm", "current_rr"],
            "reason": "Pending Broker Source (G4) — the GUI has no live price",
        },
    })


# Column order mirrors the approved table. (label, row-key) — ⛔ no Scanner.
_POS_EXPORT_COLS = [
    ("Trading Date", "date"), ("Time", "time"),
    ("Strategy", "strategy"), ("Symbol", "symbol"),
    ("Trade Type", "trade_type"), ("Direction", "direction"),
    ("System Score", "system_score"), ("Signal Score", "signal_score"),
    ("Position Status", "position_status"),
    ("Qty (System)", "qty_system"), ("Qty (Position)", "qty_position"),
    ("Entry Price (System)", "entry_target_price"),
    ("Entry Price (Filled)", "entry_actual_price"),
    ("SL (System)", "sl_initial"), ("SL (Broker)", "sl_broker"),
    ("TGT (System)", "tgt_initial"), ("TGT (Broker)", "tgt_broker"),
    ("SL Points (Rs/share)", "sl_points"),
    ("TGT Points (Rs/share)", "tgt_points"),
    ("R:R (strategy)", "rr_configured"),
    ("LTP", "ltp"), ("Unrealised", "unrealised"),
    ("Capital Used", "capital_used"), ("Risk Amount", "risk_amount"),
    ("Highest Profit %", "mfe_pct"), ("Highest Drawdown %", "mae_pct"),
    ("Exit Price", "exit_price"), ("Exit Reason", "exit_reason"),
    ("Net P&L", "net_pnl"),
    ("Entry Time", "entry_time"), ("Exit Time", "exit_time"),
    ("Trade ID", "trade_id"),
]


@trading_api.route("/api/export/positions", methods=["GET"])
@login_required
def export_positions():
    """XLSX of the FILTERED Positions result set (section L).

    ⭐ The filters are applied by _apply_position_filters — the same function
    the screen's own contract test pins — so the sheet and the table cannot
    disagree.
    ⛔ Columns that are unavailable by architecture (LTP / Current Value /
    Unrealized / MTM / Current RR) are NOT columns here. An empty column would
    imply the value exists and merely happened to be blank.
    """
    import io

    from openpyxl import Workbook
    from flask import send_file

    cfg = current_app.config["GUI_CONFIG"]
    today = _date_param()
    rows = _apply_position_filters(_position_rows_enriched(cfg, today),
                                   _position_filters())

    wb = Workbook()
    ws = wb.active
    ws.title = "Positions"
    ws.append([label for label, _ in _POS_EXPORT_COLS])
    for r in rows:
        ws.append([r.get(key) for _, key in _POS_EXPORT_COLS])
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True,
        download_name="positions_%s.xlsx" % today,
        mimetype=("application/vnd.openxmlformats-officedocument"
                  ".spreadsheetml.sheet"),
    )
