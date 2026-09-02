"""SCREEN 14 — TRADE LOGS.  A forensic investigation console for trades.

`gui/14. Trade_Logs.png` + `.txt` are BINDING for structure. The one approved
project-wide deviation is applied: SCANNER IS REMOVED EVERYWHERE, Strategy is
retained.

═══════════════════════════════════════════════════════════════════════════════
⭐ THE CENTRAL FINDING, and the whole reason this module exists

This system stores NO trade-event table. A repo-wide search finds no row per
"Signal Accepted" or "Order Submitted" anywhere in `core/schema.sql`, and the
production JSON logs cannot supply them either: `core/logger.py` routes a record
to `trades_<date>.log` only when it carries a `signal_id`/`trade_id`/`order_id`,
and a wide check (every `extra={...}` log call in the tree) finds those bound in
14 order/broker/capital modules and in NOT ONE module under `signals/`,
`screening/` or `v3_chain/` — despite those making 91 and 44 log calls.

⇒ the events are DERIVED, exactly as Screen 13 derives configuration changes by
diffing consecutive `config_snapshots`. Each event is emitted ONLY where a real
stored timestamp proves it happened. A stage this system does not time is NEVER
given a borrowed stamp — it is reported as an explicit gap.

⛔ RISK PASSED and CAPITAL PASSED ARE NEVER EMITTED. A passing gate writes no
row, so their count is NOT zero — it is unmeasurable, and the screen says so.
A `0` there would read as "no signal ever passed risk", which is false.
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
from typing import Optional

from ..readers import db_reader
from . import freshness

# ── the approved vocabularies (design §EVENT TYPES / §SEVERITY / §COMPONENT) ──
EVENT_TYPES = (
    "Signal Received", "Signal Accepted", "Signal Rejected",
    "Risk Passed", "Risk Rejected",
    "Capital Passed", "Capital Rejected",
    "Order Created", "Order Submitted", "Order Filled",
    "SL Hit", "TGT Hit", "Position Closed",
)

#: Emitted only where a stored timestamp proves the event. The rest are gaps.
UNINSTRUMENTED_EVENTS = ("Risk Passed", "Capital Passed")

SEVERITIES = ("Info", "Warning", "Error", "Critical")
STATUSES = ("Success", "Warning", "Error")

COMPONENTS = ("Signal Engine", "Risk Engine", "Capital Engine",
              "Order Engine", "Broker Connector", "Exit Manager")

TIMELINE_STAGES = ("Signal Received", "Validation", "Risk", "Capital",
                   "Order", "Fill", "Position", "Exit")

# Event → owning component. ⭐ This is a MAPPING, not a measurement, and it is
# grounded: `db_reader._RISK_REJECT_STATUSES` / `_CAPITAL_REJECT_STATUSES` are
# documented in that module as coming from `capital/risk_engine.py` check codes
# and `signals/signal_processor` reject codes, so a REJECTED_DAILY_LOSS really
# was decided by the risk engine. ⛔ It is NOT read from a log `logger` field:
# these events are derived from the DB and have no log line of their own.
_COMPONENT_OF = {
    "Signal Received": "Signal Engine",
    "Signal Accepted": "Signal Engine",
    "Signal Rejected": "Signal Engine",
    "Risk Passed": "Risk Engine",
    "Risk Rejected": "Risk Engine",
    "Capital Passed": "Capital Engine",
    "Capital Rejected": "Capital Engine",
    "Order Created": "Order Engine",
    "Order Submitted": "Order Engine",
    "Order Filled": "Broker Connector",
    "SL Hit": "Exit Manager",
    "TGT Hit": "Exit Manager",
    "Position Closed": "Exit Manager",
}

_PRODUCT_TRADE_TYPE = {"MIS": "Intraday", "CO": "Intraday", "CNC": "Delivery"}

# A kill-switch rejection is the one reject that is a SYSTEM-level stop rather
# than a per-signal verdict, so it alone carries Critical.
_CRITICAL_STATUSES = ("REJECTED_KILL_SWITCH",)


# ── small helpers ────────────────────────────────────────────────────────────
def _parts(ts: Optional[str]) -> tuple:
    """(date, time) of an ISO IST stamp. ⛔ Never invents either half."""
    if not ts:
        return None, None
    s = str(ts).replace("T", " ")
    return (s[:10] or None), (s[11:19] or None)


def _norm(ts: Optional[str]) -> Optional[str]:
    """Comparable ISO form. Production stamps already carry +05:30 (IST); the
    offset is dropped for ordering only — ⛔ no timezone conversion happens
    anywhere in this module, because both the store and the display are IST."""
    if not ts:
        return None
    s = str(ts).replace("T", " ").strip()
    return s[:19] if len(s) >= 19 else s


def _gap(reason: str) -> dict:
    """A field this system does not record. ⛔ No value, and it can never read
    as a pass — the same contract Screens 12 and 13 use."""
    return {"measured": False, "value": None, "reason": reason}


def _num(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _sev_of(status: str) -> str:
    return {"Success": "Info", "Warning": "Warning", "Error": "Error"}.get(status, "Info")


def _ev(ref_id, ts, event_type, status, message, *, severity=None,
        trade_id=None, order_id=None, signal_id=None, symbol=None,
        strategy=None, direction=None, trade_type=None, **extra) -> dict:
    d, t = _parts(ts)
    row = {
        "ref_id": ref_id,
        "ts": _norm(ts), "date": d, "time": t,
        "trade_id": trade_id, "order_id": order_id, "signal_id": signal_id,
        "symbol": symbol, "strategy": strategy, "direction": direction,
        "trade_type": trade_type,
        "event_type": event_type,
        "status": status,
        "severity": severity or _sev_of(status),
        "message": message,
        "component": _COMPONENT_OF.get(event_type),
        # ⛔ NOT INSTRUMENTED, and stated once here rather than per call site:
        # a repo-wide search for an `XXX-0000` error-code scheme returns ZERO,
        # and no generic error-resolution state is stored anywhere.
        "error_code": None,
        "resolution": None,
    }
    row.update(extra)
    return row


# ── event synthesis, one source at a time ────────────────────────────────────
def _from_signals(rows: list, screener: dict) -> list:
    """Signal Received always; then the verdict the store actually recorded."""
    out = []
    risk = set(db_reader._RISK_REJECT_STATUSES)
    cap = set(db_reader._CAPITAL_REJECT_STATUSES)
    for r in rows:
        sid = r.get("signal_id")
        status_raw = (r.get("status") or "").strip().upper()
        sc = screener.get(sid) or {}
        score = sc.get("score")
        common = dict(signal_id=sid, symbol=r.get("symbol"),
                      strategy=r.get("strategy"), trade_id=r.get("trade_id"))

        msg = "Signal received"
        if score is not None:
            msg += " (Score: %s)" % score
        out.append(_ev("SIG-%s" % sid, r.get("received_at"), "Signal Received",
                       "Success", msg, trigger_price=_num(r.get("trigger_price")),
                       **common))

        reason = (r.get("rejection_reason") or "").strip()
        if status_raw in risk:
            out.append(_ev("RSKREJ-%s" % sid, r.get("received_at"), "Risk Rejected",
                           "Error", reason or status_raw.replace("_", " ").title(),
                           severity="Critical" if status_raw in _CRITICAL_STATUSES else "Error",
                           reject_status=status_raw, **common))
        elif status_raw in cap:
            out.append(_ev("CAPREJ-%s" % sid, r.get("received_at"), "Capital Rejected",
                           "Error", reason or status_raw.replace("_", " ").title(),
                           reject_status=status_raw, **common))
        elif status_raw.startswith("REJECTED") or status_raw.startswith("DROPPED_") \
                or status_raw.startswith("SKIPPED_") or status_raw.startswith("GATE_"):
            out.append(_ev("SIGREJ-%s" % sid, r.get("received_at"), "Signal Rejected",
                           "Error", reason or status_raw.replace("_", " ").title(),
                           reject_status=status_raw, **common))

        # ⭐ Signal Accepted is emitted ONLY on the screener's own `ts` — the
        # authoritative instant the accept/reject decision was taken. ⛔ It is
        # never stamped with the signal's ARRIVAL time, which is a different
        # event that already has its own row above.
        if sc.get("ts") and not str(sc.get("status") or "").upper().startswith("REJECT"):
            acc = "Signal accepted"
            if score is not None:
                acc += " (Score: %s)" % score
            out.append(_ev("SIGACC-%s" % sid, sc.get("ts"), "Signal Accepted",
                           "Success", acc, **common))
    return out


def _from_trades(rows: list, ttype: dict) -> list:
    """Order Created (created_at) and the exit events (exit_time)."""
    out = []
    for r in rows:
        tid = r.get("trade_id")
        common = dict(trade_id=tid, signal_id=r.get("signal_id"),
                      symbol=r.get("symbol"), strategy=r.get("strategy"),
                      direction=r.get("direction"), trade_type=ttype.get(tid))
        qty = r.get("qty_planned")
        px = _num(r.get("entry_target_price"))
        msg = "Order created"
        if px is not None and qty:
            msg = "Limit order created @ %s qty %s" % (px, qty)
        out.append(_ev("TRD-%s-CREATED" % tid, r.get("created_at"), "Order Created",
                       "Success", msg,
                       latency_ms=r.get("signal_to_order_ms"),
                       binding_constraint=r.get("binding_constraint"), **common))

        reason = (r.get("exit_reason") or "").strip().upper()
        status_raw = (r.get("status") or "").strip().upper()
        if r.get("exit_time"):
            xpx = _num(r.get("exit_price"))
            pnl = _num(r.get("net_pnl"))
            tail = ""
            if xpx is not None:
                tail = " @ %s" % xpx
            if reason == "SL_HIT":
                out.append(_ev("TRD-%s-SL" % tid, r.get("exit_time"), "SL Hit",
                               "Warning", "Stop loss hit" + tail,
                               net_pnl=pnl, exit_reason=reason, **common))
            elif reason == "TGT_HIT":
                out.append(_ev("TRD-%s-TGT" % tid, r.get("exit_time"), "TGT Hit",
                               "Success", "Target hit" + tail,
                               net_pnl=pnl, exit_reason=reason, **common))
            if status_raw.startswith("CLOSED"):
                out.append(_ev("TRD-%s-CLOSED" % tid, r.get("exit_time"),
                               "Position Closed", "Success",
                               "Position closed (%s)" % (reason or status_raw),
                               net_pnl=pnl, exit_reason=reason, **common))
    return out


def _from_orders(rows: list, ttype: dict) -> list:
    """Order Submitted (placed_at) and Order Filled (filled_at)."""
    out = []
    for r in rows:
        oid = r.get("order_id")
        tid = r.get("trade_id")
        leg = (r.get("leg") or "").strip().upper()
        common = dict(trade_id=tid, order_id=oid, symbol=r.get("symbol"),
                      strategy=r.get("strategy"), direction=r.get("direction"),
                      trade_type=ttype.get(tid))
        status_raw = (r.get("status") or "").strip().upper()

        if r.get("placed_at"):
            if status_raw in ("FAILED", "REJECTED") or (r.get("rejection_reason") or "").strip():
                out.append(_ev("ORD-%s-SUBMIT" % oid, r.get("placed_at"),
                               "Order Submitted", "Error",
                               (r.get("rejection_reason") or "Order failed at the broker"),
                               leg=leg, **common))
            else:
                out.append(_ev("ORD-%s-SUBMIT" % oid, r.get("placed_at"),
                               "Order Submitted", "Success",
                               "%s order submitted to exchange" % (leg or "Order").title(),
                               leg=leg, **common))

        if r.get("filled_at"):
            fpx = _num(r.get("avg_fill_price"))
            qf = r.get("qty_filled")
            # ⭐ PARTIAL comes from the ORDER STATE MACHINE's own status, ⛔ never
            # from `qty_filled < qty_requested`. Measured on the fixture: 80 of
            # 80 filled orders carry status COMPLETE with qty_filled 0, so the
            # arithmetic form called every one of them a partial fill — a
            # secondary, often-unpopulated column overruling the authoritative
            # state column. `orders.status` is a CHECKed OSM vocabulary; use it.
            partial = (r.get("status") or "").strip().upper() == "PARTIAL"
            msg = "%s order filled" % (leg or "Order").title()
            if fpx is not None:
                msg = "%s order filled at %s" % ((leg or "Order").title(), fpx)
            if qf:                       # ⛔ never print "qty 0"
                msg += " qty %s" % qf
            out.append(_ev("ORD-%s-FILL" % oid, r.get("filled_at"), "Order Filled",
                           "Warning" if partial else "Success",
                           msg + (" (partial)" if partial else ""),
                           leg=leg, fill_price=fpx, **common))
    return out


# ── the screen ───────────────────────────────────────────────────────────────
def _matches(e: dict, f: dict) -> bool:
    def eq(key, val):
        return not val or str(e.get(key) or "") == str(val)

    if not eq("event_type", f.get("event_type")):
        return False
    if not eq("status", f.get("status")):
        return False
    if not eq("strategy", f.get("strategy")):
        return False
    if not eq("symbol", f.get("symbol")):
        return False
    if not eq("direction", f.get("direction")):
        return False
    if not eq("trade_type", f.get("trade_type")):
        return False
    if f.get("trade_id") and f["trade_id"].lower() not in str(e.get("trade_id") or "").lower():
        return False
    if f.get("order_id") and f["order_id"].lower() not in str(e.get("order_id") or "").lower():
        return False
    q = (f.get("q") or "").strip().lower()
    if q:
        hay = " ".join(str(e.get(k) or "") for k in
                       ("trade_id", "order_id", "symbol", "strategy", "message",
                        "event_type", "component", "status"))
        if q not in hay.lower():
            return False
    return True


def build_trade_logs_screen(cfg: dict, start: Optional[str] = None,
                            end: Optional[str] = None, **filters) -> dict:
    """The whole screen from ONE filtered population, so no card, chart, table
    or export can disagree with the panel beside it."""
    today = freshness.ist_today_iso()
    end = end or today
    if not start:
        import datetime as _dt
        y, m, d = (int(x) for x in end.split("-"))
        start = (_dt.date(y, m, d) - _dt.timedelta(days=6)).isoformat()

    sig_rows = db_reader.tradelog_signals_range(cfg, start, end)
    trd_rows = db_reader.tradelog_trades_range(cfg, start, end)
    ord_rows = db_reader.tradelog_orders_range(cfg, start, end)
    exec_rows = db_reader.tradelog_execution_range(cfg, start, end)
    screener = db_reader.tradelog_screener(cfg, [r.get("signal_id") for r in sig_rows])

    # trade_id → Intraday/Delivery. ⚠️ There is NO `trades.product` column — the
    # product lives on the ENTRY order, so a trade whose ENTRY row is missing
    # has an UNKNOWN trade type and is left None rather than defaulted.
    ttype = {}
    for r in ord_rows:
        if (r.get("leg") or "").strip().upper() == "ENTRY" and r.get("trade_id"):
            ttype[r["trade_id"]] = _PRODUCT_TRADE_TYPE.get(
                str(r.get("product") or "").strip().upper())

    events = []
    events += _from_signals(sig_rows, screener)
    events += _from_trades(trd_rows, ttype)
    events += _from_orders(ord_rows, ttype)

    # Every event is kept only if ITS OWN day is in the window — the rule that
    # makes a multi-day trade land each event in exactly one window.
    events = [e for e in events if e["ts"] and start <= e["date"] <= end]

    # newest first; ⭐ ref_id breaks ties so equal stamps order DETERMINISTICALLY
    # and a refresh can never reshuffle, duplicate or drop two rows sharing one
    # timestamp.
    events.sort(key=lambda e: (e["ts"], e["ref_id"]), reverse=True)

    shown = [e for e in events if _matches(e, filters)]

    # ── KPI, over the SHOWN population ───────────────────────────────────────
    # ⭐ THE TWO CARDS LABELLED "TRADES" COUNT TRADES, ⛔ NOT EVENTS. Counting
    # events under a trade label would report ~200 "successful trades" from a
    # handful of real ones, purely because a filled trade emits several events.
    # Warnings and Errors keep counting EVENTS, because that is what those two
    # cards are labelled. Each card carries its own base in the sub-line, so no
    # percentage is shown without the population it is a percentage OF.
    n = len(shown)
    trade_ids = {e["trade_id"] for e in shown if e["trade_id"]}
    err_trades = {e["trade_id"] for e in shown
                  if e["trade_id"] and e["severity"] in ("Error", "Critical")}
    closed_trades = {e["trade_id"] for e in shown
                     if e["trade_id"] and e["event_type"] == "Position Closed"}
    succ = len(closed_trades - err_trades)
    fail = len(err_trades)
    warn = sum(1 for e in shown if e["severity"] == "Warning")
    errs = sum(1 for e in shown if e["severity"] in ("Error", "Critical"))
    nt = len(trade_ids)

    def pct(x, base):
        return round(100.0 * x / base, 2) if base else 0.0

    kpi = {
        "total_events": n,
        "trades_in_view": nt,
        "successful": succ, "successful_pct": pct(succ, nt),
        "failed": fail, "failed_pct": pct(fail, nt),
        "warnings": warn, "warnings_pct": pct(warn, n),
        "errors": errs, "errors_pct": pct(errs, n),
        "last_event_ts": shown[0]["ts"] if shown else None,
    }

    by_type = {t: sum(1 for e in shown if e["event_type"] == t) for t in EVENT_TYPES}
    by_sev = {s: sum(1 for e in shown if e["severity"] == s) for s in SEVERITIES}

    def opts(key):
        return sorted({str(e.get(key)) for e in events if e.get(key)})

    return {
        "generated_at": freshness.ist_now().strftime("%Y-%m-%d %H:%M:%S"),
        "from": start, "to": end,
        "records": shown,
        "count": n,
        "total_unfiltered": len(events),
        "kpi": kpi,
        "sparklines": _sparklines(shown, start, end),
        "by_event_type": by_type,
        "by_severity": by_sev,
        "over_time": _over_time(shown, start, end),
        "recovery": _recovery(db_reader.tradelog_recovery_range(cfg, start, end)),
        "errors": _recent_errors(shown),
        "critical_changes": _critical_changes(cfg, start, end),
        "retention": db_reader.tradelog_retention(cfg),
        "event_types": list(EVENT_TYPES),
        "uninstrumented_events": list(UNINSTRUMENTED_EVENTS),
        "severities": list(SEVERITIES),
        "statuses": list(STATUSES),
        "components": list(COMPONENTS),
        "timeline_stages": list(TIMELINE_STAGES),
        "filters": {"options": {
            "strategy": opts("strategy"), "symbol": opts("symbol"),
            "direction": opts("direction"), "trade_type": opts("trade_type"),
        }},
        "exec_rows": len(exec_rows),
        "gaps": {
            "risk_capital_pass": _gap(
                "a PASSING risk or capital gate writes no row anywhere, so "
                "'Risk Passed' and 'Capital Passed' cannot be counted — a 0 "
                "would wrongly read as 'nothing ever passed'"),
            "risk_capital_time": _gap(
                "Risk and Capital have no timestamp of their own; only the "
                "signal's arrival, the screener verdict, order creation, "
                "submission, fill and exit are separately stamped"),
            "error_code": _gap(
                "no error-code scheme exists in this system — a repo-wide "
                "search for an XXX-0000 code returns zero"),
            "resolution": _gap(
                "no generic error-resolution state is stored; only reconciler "
                "recovery actions record their own success"),
            "user": _gap(
                "no human attribution exists anywhere — ops_dashboard/auth.py "
                "persists nothing and no trading module records an operator"),
            "signal_path_logs": _gap(
                "the signal/risk/capital modules never bind signal_id, trade_id "
                "or order_id to a log record, so none of their lines reach "
                "trades_<date>.log; these events are derived from the database "
                "instead"),
            "retention_period": _gap(
                "signals, trades and orders have NO configured retention "
                "policy (scripts/db_retention.py prunes screener_results 90d, "
                "reconciliation_log 180d, webhook_audit 90d, fm_ledger 365d)"),
        },
        "note": ("All timestamps are IST (Asia/Kolkata) as stored. This system "
                 "records no trade-event table, so every event here is derived "
                 "from a real stored timestamp on signals, screener_results, "
                 "trades or orders. A stage with no stored time is shown as a "
                 "gap and is never given a borrowed one."),
    }


_MAX_BUCKETS = 400


def _buckets(rows: list, start: str, end: str) -> tuple:
    """(labels, keyfn) for the time axis.

    ⭐ FIXED BY A TEST, and the bug was the exact failure the brief forbids: a
    plain day-walk from `start` capped at 400 buckets meant that on a window
    wider than ~13 months NONE of the real event dates were in the index, so the
    chart reported ZERO while the table beside it showed every row. The axis is
    now derived from the DATA's own span inside the window, and falls back to
    MONTH buckets when that span is too long to draw daily — either way every
    event lands in exactly one bucket, so the series still sums to the table.
    """
    import datetime as _dt

    dates = sorted({r["date"] for r in rows if r.get("date")})
    lo_s = max(start, dates[0]) if dates else start
    hi_s = min(end, dates[-1]) if dates else end
    try:
        lo = _dt.date(*(int(x) for x in lo_s.split("-")))
        hi = _dt.date(*(int(x) for x in hi_s.split("-")))
    except (ValueError, TypeError):
        return [], (lambda d: None)
    if hi < lo:
        hi = lo
    span = (hi - lo).days + 1

    if span <= _MAX_BUCKETS:
        labels, cur = [], lo
        while cur <= hi:
            labels.append(cur.isoformat())
            cur += _dt.timedelta(days=1)
        return labels, (lambda d: d)

    labels, y, m = [], lo.year, lo.month
    while (y, m) <= (hi.year, hi.month):
        labels.append("%04d-%02d" % (y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return labels, (lambda d: (d or "")[:7])


def _sparklines(rows: list, start: str, end: str) -> dict:
    """Per-KPI daily series for the approved card sparklines. Real counts only.

    ⭐ Each series tracks the SAME quantity as the card above it: the two trade
    cards count DISTINCT TRADES per day, the event cards count events. A
    sparkline that trended a different quantity from its own number would be a
    picture of something the card never claims.
    """
    days, keyfn = _buckets(rows, start, end)
    idx = {d: i for i, d in enumerate(days)}
    out = {k: [0] * len(days) for k in
           ("total_events", "successful", "failed", "warnings", "errors")}
    closed_by_day: dict = {}
    err_by_day: dict = {}
    for e in rows:
        i = idx.get(keyfn(e["date"]))
        if i is None:
            continue
        out["total_events"][i] += 1
        if e["severity"] == "Warning":
            out["warnings"][i] += 1
        if e["severity"] in ("Error", "Critical"):
            out["errors"][i] += 1
            if e["trade_id"]:
                err_by_day.setdefault(i, set()).add(e["trade_id"])
        if e["event_type"] == "Position Closed" and e["trade_id"]:
            closed_by_day.setdefault(i, set()).add(e["trade_id"])
    for i in range(len(days)):
        closed = closed_by_day.get(i, set())
        bad = err_by_day.get(i, set())
        out["successful"][i] = len(closed - bad)
        out["failed"][i] = len(bad)
    out["days"] = days
    return out


def _over_time(rows: list, start: str, end: str) -> dict:
    """Daily event counts. ⭐ Every event falls in exactly one bucket, so the
    series SUMS to the population — a chart that double-counted would show a
    total larger than the table it sits under."""
    days, keyfn = _buckets(rows, start, end)
    idx = {d: i for i, d in enumerate(days)}
    counts = [0] * len(days)
    for e in rows:
        i = idx.get(keyfn(e["date"]))
        if i is not None:
            counts[i] += 1
    return {"days": days, "counts": counts, "total": sum(counts),
            "bucket": "month" if days and len(days[0]) == 7 else "day"}


def _critical_changes(cfg: dict, start: str, end: str, limit: int = 8) -> list:
    """CRITICAL CHANGES (Pinned) — risk/capital/control settings that MOVED.

    ⭐ REUSED, ⛔ not reimplemented: `audit._config_changes` is the diff Screen 13
    already proved against production, so the two screens cannot report a
    different history of the same config. A snapshot is written at STARTUP, so
    its stamp is when the system OBSERVED the change, not when a file was edited
    — the same caveat Screen 13 carries.
    """
    from . import audit as _audit
    try:
        rows = _audit._config_changes(
            db_reader.audit_config_snapshots(cfg, start, end))
    except Exception:
        return []
    out = []
    for r in rows:
        if r.get("category") not in ("Risk", "Capital", "Control", "Strategy"):
            continue
        d, t = _parts(r.get("ts"))
        out.append({
            "ref_id": r.get("ref_id"), "ts": _norm(r.get("ts")),
            "date": d, "time": t,
            "title": r.get("action"), "category": r.get("category"),
            "field": r.get("field"),
            "old_value": r.get("old_value"), "new_value": r.get("new_value"),
        })
        if len(out) >= limit:
            break
    return out


def _recovery(rows: list) -> list:
    """AUTO-RECOVERY HISTORY. ⛔ 'Recovered In' is NOT computed: reconciliation_log
    stores one instant per action and no duration, so the column is a gap."""
    out = []
    for r in rows:
        d, t = _parts(r.get("ts"))
        out.append({
            "ref_id": "RECOV-%s" % r.get("id"),
            "ts": _norm(r.get("ts")), "date": d, "time": t,
            "component": "Exit Manager" if (r.get("check_name") or "").upper().startswith("EOD")
                         else "Order Engine",
            "trigger": r.get("check_name"),
            "action": r.get("action_taken"),
            "status": "Success" if int(r.get("success") or 0) == 1 else "Error",
            "tier": r.get("tier"),
            "symbol": r.get("symbol"),
            "trade_id": r.get("trade_id"),
            "description": r.get("description"),
            "recovered_in": None,     # ⛔ not stored — no duration exists
        })
    return out


def _recent_errors(rows: list, limit: int = 12) -> list:
    """RECENT ERRORS. Error Code and Resolution Status stay None — this system
    has neither, and inventing `EXCH-1016` to fill the approved column would be
    exactly the fabrication the brief forbids."""
    out = []
    for e in rows:
        if e["severity"] in ("Error", "Critical"):
            out.append({
                "ref_id": e["ref_id"], "ts": e["ts"], "time": e["time"],
                "component": e["component"], "message": e["message"],
                "error_code": None, "resolution": None,
                "trade_id": e["trade_id"], "symbol": e["symbol"],
            })
        if len(out) >= limit:
            break
    return out


# ── per-trade detail: timeline, request/decision/output, replay ──────────────
def trade_detail(cfg: dict, trade_id: Optional[str] = None,
                 signal_id: Optional[str] = None) -> dict:
    """The TRADE TIMELINE + REQUEST/DECISION/OUTPUT + REPLAY payload.

    ⭐ Replay reconstructs the REAL lifecycle: each stage is filled only from a
    stored timestamp. ⛔ Risk and Capital have none, so they always come back
    unavailable with the reason attached — the replay shows what the system can
    prove, not a plausible animation.
    """
    if not trade_id and not signal_id:
        return {"found": False}

    trade = _one_trade(cfg, trade_id)
    if trade and not signal_id:
        signal_id = trade.get("signal_id")
    signal = _one_signal(cfg, signal_id)
    if signal and not trade and signal.get("trade_id"):
        trade = _one_trade(cfg, signal.get("trade_id"))
    if not trade and not signal:
        return {"found": False}

    sc = db_reader.tradelog_screener(cfg, [signal_id]).get(signal_id) if signal_id else None
    orders = _orders_of(cfg, (trade or {}).get("trade_id"))
    entry = next((o for o in orders if (o.get("leg") or "").upper() == "ENTRY"), None)

    def stage(name, ts, note=None, gap=None):
        d, t = _parts(ts)
        return {"stage": name, "ts": _norm(ts), "date": d, "time": t,
                "measured": ts is not None, "note": note, "gap": gap}

    stages = [
        stage("Signal Received", (signal or {}).get("received_at")),
        stage("Validation", (sc or {}).get("ts"),
              note=None if sc else "no screener verdict stored for this signal"),
        stage("Risk", None, gap="a passing risk check writes no row and no "
                                "timestamp; only a REJECTION is recorded"),
        stage("Capital", None, gap="a passing capital check writes no row and "
                                   "no timestamp; only a REJECTION is recorded"),
        stage("Order", (trade or {}).get("created_at")),
        stage("Fill", (entry or {}).get("filled_at") or (trade or {}).get("entry_time")),
        stage("Position", (trade or {}).get("entry_time")),
        stage("Exit", (trade or {}).get("exit_time"),
              note=None if (trade or {}).get("exit_time") else "position still open"),
    ]

    # REQUEST / DECISION / OUTPUT — real values only.
    score = (sc or {}).get("score")
    elig = (sc or {}).get("eligible_score")
    steps = _json_or_none((sc or {}).get("step_results"))
    lat = _json_or_none((sc or {}).get("latencies"))
    decision = None
    if signal:
        st = (signal.get("status") or "").upper()
        decision = "Rejected" if st.startswith(("REJECTED", "DROPPED_", "SKIPPED_")) \
            else ("Accepted" if (signal.get("trade_id") or sc) else st.title() or None)
    output = None
    if trade:
        output = "Order Created" if trade.get("created_at") else None
        if trade.get("entry_time"):
            output = "Order Filled"
        if trade.get("exit_time"):
            output = "Position Closed"

    rdo = {
        "input": {
            # ⭐ L8: `screener_results.score` is the ACHIEVED score and its
            # project-wide label is SYSTEM SCORE. ⛔ "Signal Score" is a RETIRED
            # label (Entry 9 ruling) and a tree-wide guard fails on it.
            "system_score": score,
            "eligible_score": elig,
            "trigger_price": _num((signal or {}).get("trigger_price")),
            "step_results": steps,
            "step_latencies_ms": lat,
        },
        "decision": {
            "verdict": decision,
            "signal_status": (signal or {}).get("status"),
            "rejection_reason": (signal or {}).get("rejection_reason"),
            "binding_constraint": (trade or {}).get("binding_constraint"),
        },
        "output": {
            "result": output,
            "trade_id": (trade or {}).get("trade_id"),
            "qty_planned": (trade or {}).get("qty_planned"),
            "qty_filled": (trade or {}).get("qty_filled"),
            "entry_price": _num((trade or {}).get("entry_actual_price")),
            "exit_price": _num((trade or {}).get("exit_price")),
            "exit_reason": (trade or {}).get("exit_reason"),
            "net_pnl": _num((trade or {}).get("net_pnl")),
        },
        "latency": {
            "signal_to_order_ms": (trade or {}).get("signal_to_order_ms"),
            "order_to_fill_ms": (trade or {}).get("order_to_fill_ms"),
            "total_latency_ms": (trade or {}).get("total_latency_ms"),
        },
    }

    return {
        "found": True,
        "trade_id": (trade or {}).get("trade_id"),
        "signal_id": signal_id,
        "symbol": (trade or signal or {}).get("symbol"),
        "strategy": (trade or signal or {}).get("strategy"),
        "direction": (trade or {}).get("direction"),
        "status": (trade or {}).get("status"),
        "timeline": stages,
        "stages_measured": sum(1 for s in stages if s["measured"]),
        "stages_total": len(stages),
        "request_response": rdo,
        "orders": orders,
    }


def _json_or_none(raw):
    if not raw:
        return None
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return v if isinstance(v, (dict, list)) else None
    except (ValueError, TypeError):
        return None


def _one_trade(cfg: dict, trade_id) -> Optional[dict]:
    if not trade_id:
        return None
    rows = db_reader.tradelog_trades_by_id(cfg, [trade_id])
    return rows[0] if rows else None


def _one_signal(cfg: dict, signal_id) -> Optional[dict]:
    if not signal_id:
        return None
    rows = db_reader.tradelog_signals_by_id(cfg, [signal_id])
    return rows[0] if rows else None


def _orders_of(cfg: dict, trade_id) -> list:
    if not trade_id:
        return []
    return db_reader.tradelog_orders_of_trade(cfg, trade_id)


# ── XLSX ─────────────────────────────────────────────────────────────────────
#: ⭐ The FIRST TEN are the approved table columns in the approved order —
#: Date · Time · Trade ID · Order ID · SYMBOL · STRATEGY · Event Type · Status ·
#: Message · Component — so the export opens in the order the operator was just
#: reading. Severity and Reference ID follow AFTER, never interleaved.
#: ⛔ There is deliberately no "Scanner" column, and a test asserts its absence.
EXPORT_HEADER = ("Date", "Time", "Trade ID", "Order ID", "Symbol", "Strategy",
                 "Event Type", "Status", "Message", "Component",
                 "Severity", "Reference ID")


def export_rows(payload: dict) -> list:
    """Exactly the rows the table is showing — the SAME filtered list object,
    so the export cannot describe a different population."""
    out = [list(EXPORT_HEADER)]
    for e in payload.get("records") or []:
        out.append([
            e.get("date"), e.get("time"), e.get("trade_id"), e.get("order_id"),
            e.get("symbol"), e.get("strategy"), e.get("event_type"),
            e.get("status"), e.get("message"), e.get("component"),
            e.get("severity"), e.get("ref_id"),
        ])
    return out
