"""SCREEN 18 — LIVE ACTIVITY.  The Mission Control Wall.

`gui/18. Live_Activity.png` + `.txt` are BINDING for structure.

═══════════════════════════════════════════════════════════════════════════════
⭐ WHAT THIS SCREEN CAN AND CANNOT KNOW — measured before a line was written

REAL, from this system's own stores:
  · the FEED — signals · ENTRY orders · positions opened · trades closed ·
    risk rejections + the kill switch · fm_ledger · system_events +
    cron_heartbeat · telegram_alerts. Eight sources, one per approved CATEGORY.
  · the PIPELINE — six stages counted off ONE base (today's stored signals).
  · STRATEGY ACTIVITY, RECENT WINNERS/LOSERS, ACTIVE POSITIONS (system side),
    CAPITAL UTILIZATION, MARKET PULSE — all from stored rows and stamps.

⛔ CURRENT MTM IS NOT INSTRUMENTED, AND IT IS THE HEADLINE GAP OF THIS SCREEN.
   It needs a live price. `ops_dashboard` has ZERO live-price call sites — the
   finding Screen 06 measured across the whole backend and `/api/positions`
   already declares ltp/mtm/unrealized/current_rr UNAVAILABLE, "Pending Broker
   Source (G4)". The KPI card and the ACTIVE POSITIONS columns LTP / MTM (₹) /
   MTM (%) therefore render the established NOT INSTRUMENTED state. ⭐ Today's
   REALIZED P&L is real and is a DIFFERENT quantity; the two are never
   substituted for each other.

⛔ THE OPEN-POSITIONS DELTA IS NOT INSTRUMENTED EITHER. "Open Positions" is a
   LIVE `trades.status` read; nothing stores what that set held at any past
   instant, and reconstructing one from entry/exit stamps would answer a
   different question from the card above it. Signals and Orders DO have a real
   yesterday — they are dated rows — so those two deltas are measured.

⛔ NO "ALL SYSTEMS OPERATIONAL" CLAIM IS PRINTED AS A MEASUREMENT. Nothing here
   measures the health of all services, and Screen 12 already ruled that the
   reference's Signal/Risk/Capital/Order "engines" are THREADS inside one
   systemd service, so a per-engine verdict cannot be claimed. TRADING STATUS
   shows the state that IS measured — the kill switch and the market phase —
   and prints that basis underneath.

⛔ TWO OF THE FOUR APPROVED SYSTEM-EVENT EXAMPLES HAVE NO SOURCE. `system_events`
   records STARTUP / SHUTDOWN / CRASH_DETECTED / CONFIG_DIFF / RECOVERY /
   KILL_AUTO_CLEARED / EOD_SKIPPED_LATE — so Service Restarted and Recovery
   Triggered are evidenced, while Broker Reconnected and Database Warning are
   not written by anything. They are declared uninstrumented, ⛔ never shown as 0
   and ⛔ never synthesised from a log line's severity.

⛔ LIVE MODE AND THE FEED PAUSE ARE DISPLAY CONTROLS. They start and stop THIS
   PAGE's polling. There is no write path anywhere in this dashboard (L4) and
   neither control touches the trading system.
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from typing import Optional

from ..readers import db_reader
from . import freshness

# ── the approved vocabularies (design §EVENT CATEGORIES / §STATUS / §PIPELINE) ─
CATEGORIES = ("Signal", "Order", "Position", "Trade", "Risk", "Capital",
              "System", "Alert")

#: The approved traffic light, and it is EXACTLY four states.
#: Green = Success · Yellow = Processing · Red = Failed · Blue = Information
STATUSES = ("Success", "Processing", "Failed", "Info")

STATUS_MEANING = {"Success": "Green — the step completed",
                  "Processing": "Yellow — the step is in flight",
                  "Failed": "Red — the step did not complete, or closed at a loss",
                  "Info": "Blue — recorded for information"}

PIPELINE_STAGES = ("Signal", "Validation", "Risk", "Capital", "Order", "Fill")

#: The approved FEED FILTERS tiles, in the approved order (the PNG's 4 x 2 grid).
FEED_FILTERS = ("All", "Signals", "Orders", "Positions", "Trades", "System",
                "Alerts", "Reset")

#: The approved SYSTEM EVENTS examples.
SYSTEM_EVENT_TYPES = ("Service Restarted", "Broker Reconnected",
                      "Database Warning", "Recovery Triggered")

#: Evidenced by a stored, STRUCTURED value: `system_events.event_type =
#: CRASH_DETECTED` and `= RECOVERY` (plus `reconciliation_log`). ⛔ The other two
#: are NOT zero — nothing writes them, and the panel prints that instead of a
#: count. ⚠️ This list and what `_from_system_events` can emit must agree; a test
#: pins that they do.
INSTRUMENTED_SYSTEM_EVENT_TYPES = ("Service Restarted", "Recovery Triggered")

#: MARKET PULSE window. The rate is events-in-the-window / window, and the panel
#: prints the window so a 0.00 reads as "nothing in the last hour", ⛔ not as a
#: broken meter.
PULSE_WINDOW_MIN = 60
PULSE_SPARK_MIN = 30

#: How many feed rows the panel shows before "View Full Feed" hands over to the
#: full forensic console. The payload carries the whole filtered set.
FEED_PAGE = 15


def _gap(reason: str) -> dict:
    return {"measured": False, "value": None, "reason": reason}


def _norm(ts: Optional[str]) -> Optional[str]:
    if not ts:
        return None
    s = str(ts).replace("T", " ").strip()
    return s[:19] if len(s) >= 19 else s


def _hhmmss(ts: Optional[str]) -> Optional[str]:
    s = _norm(ts)
    return s[11:19] if s and len(s) >= 19 else None


def _ev(ref_id, ts, category, event, *, symbol=None, strategy=None,
        details=None, status="Info", link=None, **extra) -> dict:
    row = {
        "ref_id": ref_id,
        "ts": _norm(ts),
        "time": _hhmmss(ts),
        "date": (_norm(ts) or "")[:10] or None,
        "category": category,
        "event": event,
        "symbol": symbol,
        "strategy": strategy,
        "details": details,
        "status": status,
        "link": link,
    }
    # ⭐ The approved fourth column is ONE heading — "Strategy / Symbol" — so the
    # combined cell is composed ONCE here and the table, the sort and the export
    # all read the same value. ⛔ Not composed again in the template, which is
    # how a sorted column starts disagreeing with the cell beside it.
    row["strategy_symbol"] = _strategy_symbol(strategy, symbol)
    row.update(extra)
    return row


def _strategy_symbol(strategy, symbol) -> str:
    """⛔ Not two columns: the binding design draws ONE heading, and an event
    with neither renders the em-dash the PNG itself shows on its Risk and
    Capital rows."""
    if strategy and symbol:
        return "%s / %s" % (strategy, symbol)
    return strategy or symbol or "—"


def _money(v) -> str:
    """₹ with the Indian grouping the rest of the app uses. ⛔ None is an
    em-dash, never 0.00 — a missing number is not a zero."""
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if n < 0 else ("+" if n > 0 else "")
    return sign + "₹" + _grouped(abs(n))


def _grouped(n: float, dp: int = 2) -> str:
    """Indian digit grouping (2,2,3) — 1,25,430.00, not 125,430.00."""
    s = ("%%.%df" % dp) % n
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return whole + ("." + frac if frac else "")


def _num(v, dp: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return _grouped(float(v), dp)
    except (TypeError, ValueError):
        return "—"


# ── event synthesis: ONE builder per approved CATEGORY ───────────────────────
def _signal_event(event: str) -> str:
    return event


def _from_signals(rows: list) -> list:
    """Category SIGNAL — every stored signal is a real, timestamped event.

    ⭐ The status follows the signal's own family: an accepted signal is a
    Success, a rejected one a Failure, and a duplicate or expired one is
    Information (it was dropped by design, not by failure).
    """
    out = []
    for r in rows:
        fam = r.get("family")
        status = {"accepted": "Success", "rejected": "Failed",
                  "duplicated": "Info", "expired": "Info"}.get(fam, "Info")
        bits = []
        if r.get("trigger_price") is not None:
            bits.append("Price: " + _num(r.get("trigger_price")))
        bits.append("Status: %s" % (r.get("status") or "—"))
        if r.get("rejection_reason"):
            bits.append(str(r["rejection_reason"]))
        out.append(_ev(
            "SIG-%s" % r.get("signal_id"), r.get("received_at"),
            "Signal", "Signal Received",
            symbol=r.get("symbol"), strategy=r.get("strategy"),
            details=" | ".join(bits), status=status, link="/signals",
            signal_id=r.get("signal_id"), family=fam,
        ))
    return out


def _from_orders(rows: list) -> list:
    """Category ORDER — ENTRY orders only.

    ⭐ TWO events per order where the store records two instants: Order Created
    at `placed_at` and Order Filled at `filled_at`. ⛔ A fill event is emitted
    ONLY when `filled_at` is set — never inferred from a COMPLETE status with no
    stamp, which would put an event on the wall at a time nothing recorded.

    ⛔ SL and TGT legs are NOT emitted here: their outcome is the TRADE event
    (SL Hit / TGT Hit), and emitting both would double-count one exit.
    """
    out = []
    for r in rows:
        oid = r.get("order_id")
        qty = r.get("qty_requested")
        px = r.get("entry_target_price")
        side = "BUY" if str(r.get("direction") or "").upper() == "LONG" else "SELL"
        result = r.get("order_result")
        created_status = "Failed" if result == "Rejected" else "Processing"
        det = "%s %s @ %s" % (side, _num(qty, 0), _num(px))
        if result and result not in ("Filled",):
            det += " | " + str(result)
        if r.get("rejection_reason"):
            det += " | " + str(r["rejection_reason"])
        out.append(_ev(
            "ORD-C-%s" % oid, r.get("placed_at"), "Order", "Order Created",
            symbol=r.get("symbol"), strategy=r.get("strategy"),
            details=det, status=created_status, link="/orders",
            order_id=oid, trade_id=r.get("trade_id"),
        ))
        if r.get("filled_at"):
            filled = r.get("qty_filled")
            out.append(_ev(
                "ORD-F-%s" % oid, r.get("filled_at"), "Order", "Order Filled",
                symbol=r.get("symbol"), strategy=r.get("strategy"),
                details="%s @ %s" % (_num(filled if filled else qty, 0), _num(px)),
                status="Success", link="/orders",
                order_id=oid, trade_id=r.get("trade_id"),
            ))
    return out


def _from_positions(rows: list) -> list:
    """Category POSITION — a position opened at `entry_time`."""
    out = []
    for r in rows:
        out.append(_ev(
            "POS-%s" % r.get("trade_id"), r.get("entry_time"),
            "Position", "Position Opened",
            symbol=r.get("symbol"), strategy=r.get("strategy"),
            details="Qty: %s | Entry: %s" % (
                _num(r.get("qty_filled"), 0),
                _num(r.get("entry_actual_price"))),
            status="Success", link="/positions", trade_id=r.get("trade_id"),
        ))
    return out


#: exit_reason → the approved event name. ⚠️ GTT_EXIT is a MECHANISM, not a leg
#: — it does not say whether the stop or the target fired — so it resolves to the
#: neutral "Trade Closed", the same treatment `_position_status_of` gives it.
_EXIT_EVENT = {"SL_HIT": "SL Hit", "TGT_HIT": "TGT Hit"}


def _from_trades(rows: list) -> list:
    """Category TRADE — SL Hit / TGT Hit / Trade Closed at `exit_time`.

    ⭐ THE STATUS IS THE OUTCOME'S SIGN, from the stored `net_pnl`: a close in
    the red is Red, a close in the green is Green. ⛔ It is NOT read off the exit
    reason — an SL that fires is the system working, and a trade can close at a
    loss for reasons other than a stop. A close with no P&L recorded is Info,
    ⛔ never assumed profitable.
    """
    out = []
    for r in rows:
        reason = str(r.get("exit_reason") or "").upper()
        event = _EXIT_EVENT.get(reason, "Trade Closed")
        net = r.get("net_pnl")
        status = "Info" if net is None else ("Failed" if float(net) < 0 else "Success")
        bits = []
        if reason == "SL_HIT" and r.get("sl_initial") is not None:
            bits.append("SL: " + _num(r.get("sl_initial")))
        elif reason == "TGT_HIT" and r.get("tgt_initial") is not None:
            bits.append("TGT: " + _num(r.get("tgt_initial")))
        bits.append("Exit: " + _num(r.get("exit_price")))
        bits.append("P&L: " + _money(net))
        out.append(_ev(
            "TRD-%s" % r.get("trade_id"), r.get("exit_time"), "Trade", event,
            symbol=r.get("symbol"), strategy=r.get("strategy"),
            details=" | ".join(bits), status=status, link="/trades",
            trade_id=r.get("trade_id"), net_pnl=net, exit_reason=r.get("exit_reason"),
        ))
    return out


def _from_risk(signal_rows: list, kill: dict) -> list:
    """Category RISK.

    ⭐ WHAT THIS SYSTEM ACTUALLY RECORDS as a risk event is a signal REJECTED BY
    A NAMED RISK CHECK (`signals.status = REJECTED_<check>`) and the kill switch.
    ⛔ There is no per-signal "risk check passed" record — a check that passes
    writes nothing — so no such row is manufactured. The gap is declared.
    """
    out = []
    risk_set = set(db_reader._RISK_REJECT_STATUSES)
    for r in signal_rows:
        st = str(r.get("status") or "").upper()
        if st not in risk_set:
            continue
        check = st[len("REJECTED_"):].replace("_", " ").title() if st.startswith(
            "REJECTED_") else st
        out.append(_ev(
            "RISK-%s" % r.get("signal_id"), r.get("received_at"),
            "Risk", "Risk Check Failed",
            symbol=r.get("symbol"), strategy=r.get("strategy"),
            details="Check: %s%s" % (check, (" | " + str(r["rejection_reason"]))
                                     if r.get("rejection_reason") else ""),
            status="Failed", link="/capital-risk",
            signal_id=r.get("signal_id"),
        ))
    state = str((kill or {}).get("state") or "INACTIVE").upper()
    if state in ("SOFT_KILL", "HARD_KILL") and kill.get("triggered_at"):
        out.append(_ev(
            "RISK-KILL", kill.get("triggered_at"), "Risk",
            "Kill Switch %s" % state,
            details="%s | by %s" % (kill.get("reason") or "—",
                                    kill.get("triggered_by") or "—"),
            status="Failed", link="/capital-risk",
        ))
    return out


#: fm_ledger.entry_type → the event name. ⛔ An entry type this map does not know
#: keeps its raw value rather than being folded into a near-enough name.
_LEDGER_EVENT = {
    "INIT": "Capital Initialised", "SYNC": "Capital Synced",
    "RESERVE": "Capital Reserved", "COMMIT": "Capital Committed",
    "RELEASE": "Capital Released", "RELEASE_USED": "Capital Released",
}


def _from_capital(rows: list) -> list:
    """Category CAPITAL — the append-only fm_ledger, which is the engine's own
    record of every rupee movement.

    ⭐ `pnl_delta` is ALREADY NET (the E4/W10 contract); `costs` is persisted
    alongside for observability and is ⛔ NEVER subtracted again.
    """
    out = []
    for r in rows:
        et = str(r.get("entry_type") or "").upper()
        bits = []
        if r.get("bucket"):
            bits.append("Bucket: %s" % r["bucket"])
        if r.get("pnl_delta") is not None:
            bits.append("P&L Δ: " + _money(r.get("pnl_delta")))
        if r.get("balance_after") is not None:
            bits.append("Balance: " + _money(r.get("balance_after")))
        out.append(_ev(
            "CAP-%s" % r.get("ledger_id"), r.get("ts"), "Capital",
            _LEDGER_EVENT.get(et, et.replace("_", " ").title() or "Capital Entry"),
            details=" | ".join(bits) or None, status="Info",
            link="/capital-risk", trade_id=r.get("trade_id"),
        ))
    return out


#: system_events.event_type → the approved SYSTEM EVENTS name, where the STORED
#: type MEANS the approved one. ⛔ CONFIG_DIFF / KILL_AUTO_CLEARED /
#: EOD_SKIPPED_LATE are real events but are NOT among the four approved
#: examples, so they keep their own readable name rather than being forced into
#: a near-enough one.
_SYSEVT_EVENT = {
    "CRASH_DETECTED": "Service Restarted",
    "RECOVERY": "Recovery Triggered",
}
_SYSEVT_NAME = {
    "STARTUP": "Service Started", "SHUTDOWN": "Service Stopped",
    "CONFIG_DIFF": "Config Changed",
    "KILL_AUTO_CLEARED": "Kill Switch Auto-Cleared",
    "EOD_SKIPPED_LATE": "EOD Skipped (late)",
}


def _from_system_events(rows: list) -> list:
    out = []
    for r in rows:
        raw = str(r.get("event_type") or "").upper()
        name = _SYSEVT_EVENT.get(raw) or _SYSEVT_NAME.get(
            raw, raw.replace("_", " ").title() or "System Event")
        status = "Failed" if raw == "CRASH_DETECTED" else "Info"
        det = []
        if r.get("scenario"):
            det.append("Scenario: %s" % r["scenario"])
        out.append(_ev(
            "SYS-%s" % (r.get("event_id") if r.get("event_id") is not None
                        else r.get("timestamp")),
            r.get("timestamp"), "System", name,
            details=" | ".join(det) or None, status=status, link="/logs",
            approved_type=_SYSEVT_EVENT.get(raw),
        ))
    return out


def _from_cron(rows: list) -> list:
    """Scheduler runs — the ONE source with a STRUCTURED status column, which is
    why a scheduled job can carry a real Success/Failed rather than a phrase
    parsed out of a message."""
    out = []
    for r in rows:
        st = str(r.get("status") or "").upper()
        status = {"SUCCESS": "Success", "FAILED": "Failed",
                  "PARTIAL": "Processing"}.get(st, "Info")
        det = []
        if r.get("message"):
            det.append(str(r["message"]))
        if r.get("duration_sec") is not None:
            det.append("%s sec" % r["duration_sec"])
        out.append(_ev(
            "CRON-%s" % r.get("id"), r.get("executed_at"), "System",
            "Scheduled Job: %s" % (r.get("job_name") or "—"),
            details=" | ".join(det) or None, status=status, link="/logs",
        ))
    return out


def _from_alerts(rows: list) -> list:
    """Category ALERT — telegram_alerts, the system's own alert record.

    ⭐ The feed's four-status vocabulary is kept EXACT: a CRITICAL/ERROR alert is
    Failed, an alert still being retried is Processing, everything else is Info.
    ⛔ A WARNING is NOT relabelled "Processing" — that word means in flight, and a
    warning is not in flight. The severity itself is carried verbatim in Details
    and drives the ALERTS BANNER, which has its own approved WARNING treatment.
    """
    out = []
    for i, r in enumerate(rows):
        sev = str(r.get("severity") or "").upper()
        dst = str(r.get("status") or "").upper()
        if sev in ("CRITICAL", "ERROR") or dst in ("FAILED", "ERROR"):
            status = "Failed"
        elif dst in ("PENDING", "RETRY", "RETRYING", "QUEUED"):
            status = "Processing"
        else:
            status = "Info"
        det = ["Severity: %s" % (sev or "—")]
        if r.get("source_module"):
            det.append(str(r["source_module"]))
        if r.get("status"):
            det.append("Delivery: %s" % r["status"])
        out.append(_ev(
            "ALT-%d-%s" % (i, r.get("sent_at") or ""), r.get("sent_at"),
            "Alert", r.get("title") or "Alert",
            details=" | ".join(det), status=status, link="/alerts",
            severity=sev,
        ))
    return out


# ── filtering ────────────────────────────────────────────────────────────────
#: The FEED FILTERS tiles / toolbar chips → the CATEGORY they select. "All" and
#: "Reset" clear the filter; both are the same act and are bound to one state so
#: the two controls can never disagree.
FILTER_CATEGORY = {
    "signals": "Signal", "orders": "Order", "positions": "Position",
    "trades": "Trade", "risk": "Risk", "capital": "Capital",
    "system": "System", "alerts": "Alert",
}


def _matches(e: dict, category: Optional[str]) -> bool:
    """⛔ CATEGORY IS THE ONLY FILTER, because it is the only one the approved
    design draws — the nine toolbar chips and the eight FEED FILTERS tiles both
    select a category and nothing else. A status filter or a search box would be
    an invention, so neither is accepted here or on the export."""
    return not category or str(e.get("category") or "") == category


def _resolve_category(raw: Optional[str]) -> Optional[str]:
    """Accepts the tile label ("Signals"/"All"/"Reset") or the category name."""
    v = (raw or "").strip()
    if not v or v.lower() in ("all", "reset"):
        return None
    if v in CATEGORIES:
        return v
    return FILTER_CATEGORY.get(v.lower())


# ── the screen ───────────────────────────────────────────────────────────────
def build_live_activity(cfg: dict, category: Optional[str] = None,
                        today: Optional[str] = None) -> dict:
    """The whole wall, from real rows only.

    ⭐ THE FEED FILTER NARROWS THE FEED, and only the feed — that is what the
    approved design calls it (FEED FILTERS). The KPI strip, the pipeline, the
    strategy table, the pulse and the capital ring describe THE DAY, and each
    prints its own base so no reader can mistake one for the other.
    """
    import datetime as _dt

    now = freshness.ist_now()
    today = today or freshness.ist_today_iso(now)
    y, m, d = (int(x) for x in today.split("-"))
    # ⚠️ THE CALENDAR DAY BEFORE, ⛔ not "the previous trading day": nothing here
    # resolves the holiday calendar, so the comparison names the DATE it used and
    # the card prints it. A Monday compares with Sunday and says so, rather than
    # silently reaching back to Friday under the word "yesterday".
    yday = (_dt.date(y, m, d) - _dt.timedelta(days=1)).isoformat()

    cat = _resolve_category(category)

    signals = db_reader.activity_signals_today(cfg, today)
    orders = db_reader.order_screen_rows(cfg, today)
    positions = db_reader.activity_positions_opened(cfg, today)
    exits = db_reader.activity_trade_exits(cfg, today)
    ledger = db_reader.ledger_entries(cfg, today)
    sysevts = db_reader.syslog_events_range(cfg, today, today)
    crons = db_reader.syslog_cron_range(cfg, today, today)
    alerts = db_reader.telegram_alerts_today(cfg, today)
    kill = db_reader.get_kill_switch(cfg)
    # ⭐ Read ONCE and shared by the KPI strip and the strategy table, so the two
    # cannot describe today with different numbers.
    day_counts = db_reader.activity_day_counts(cfg, today)

    events = []
    events += _from_signals(signals)
    events += _from_orders(orders)
    events += _from_positions(positions)
    events += _from_trades(exits)
    events += _from_risk(signals, kill)
    events += _from_capital(ledger)
    events += _from_system_events(sysevts)
    events += _from_cron(crons)
    events += _from_alerts(alerts)

    events = [e for e in events if e["ts"]]
    # newest first; ⭐ ref_id breaks ties so equal stamps order DETERMINISTICALLY
    # and a 5-second poll can never reshuffle, duplicate or drop two rows.
    events.sort(key=lambda e: (e["ts"], e["ref_id"]), reverse=True)

    shown = [e for e in events if _matches(e, cat)]

    by_category = {c: sum(1 for e in events if e["category"] == c)
                   for c in CATEGORIES}
    by_status = {s: sum(1 for e in events if e["status"] == s) for s in STATUSES}

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "today": today,
        "records": shown,
        "count": len(shown),
        "total_events": len(events),
        "feed_page": FEED_PAGE,
        "cap": db_reader.activity_cap(),
        "active": {"category": cat},
        "categories": list(CATEGORIES),
        "statuses": list(STATUSES),
        "status_meaning": dict(STATUS_MEANING),
        "feed_filters": list(FEED_FILTERS),
        "by_category": by_category,
        "by_status": by_status,
        "kpi": _kpi(cfg, today, yday, now, kill, day_counts),
        "pipeline": _pipeline(cfg, today),
        "strategy_activity": _strategy_activity(cfg, today, day_counts),
        "winners_losers": _winners_losers(exits),
        "system_events": _system_events_panel(sysevts, crons),
        "system_event_types": list(SYSTEM_EVENT_TYPES),
        "instrumented_system_event_types": list(INSTRUMENTED_SYSTEM_EVENT_TYPES),
        "alerts_banner": _alerts_banner(alerts),
        "market_pulse": _market_pulse(cfg, today, now),
        "active_positions": _active_positions(cfg),
        "capital": _capital(cfg, today),
        "live": {
            "poll_ms": freshness.poll_interval_ms(cfg, now),
            "phase": freshness.phase(cfg, now),
            "market_open": freshness.market_is_open(cfg, now),
            "note": ("Live Mode and the feed pause are DISPLAY controls: they "
                     "start and stop this page's polling. This dashboard has no "
                     "write path (L4) and neither control touches the trading "
                     "system."),
        },
        "gaps": {
            "current_mtm": _gap(
                "current MTM needs a live price and this dashboard has no "
                "live-price source — /api/positions already declares ltp, mtm, "
                "unrealized and current_rr unavailable (Pending Broker Source, "
                "G4). Today's REALIZED P&L is real and is a different quantity, "
                "so it is never substituted for MTM"),
            "open_positions_delta": _gap(
                "open positions is a live trades.status read and nothing stores "
                "what that set held at any past instant; reconstructing one from "
                "entry and exit stamps would answer a different question from "
                "the card above it. Signals and Orders are dated rows, so their "
                "yesterday comparison is measured"),
            "system_health_claim": _gap(
                "nothing here measures the health of every service, and Screen "
                "12 ruled that the Signal / Risk / Capital / Order engines are "
                "threads inside one systemd service — so no blanket everything-"
                "is-fine verdict is printed. Trading Status shows the kill "
                "switch and the market phase, which are measured"),
            "system_events": _gap(
                "system_events records STARTUP, SHUTDOWN, CRASH_DETECTED, "
                "CONFIG_DIFF, RECOVERY, KILL_AUTO_CLEARED and EOD_SKIPPED_LATE, "
                "so Service Restarted and Recovery Triggered are evidenced while "
                "Broker Reconnected and Database Warning are written by nothing"),
            "risk_check_passed": _gap(
                "a risk check that PASSES writes no row — only a rejection is "
                "stored, as signals.status = REJECTED_<check> — so the feed "
                "carries the rejections and never a manufactured pass event"),
        },
        "note": ("Every row is a stored event with its own recorded timestamp, "
                 "shown in IST exactly as stored. The feed filter narrows the "
                 "feed only; every other panel states its own base."),
    }


# ── KPI strip ────────────────────────────────────────────────────────────────
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")


def _delta_pct(now_n: int, prev_n: int) -> Optional[float]:
    """⛔ None, never 0.0 and never 100.0, when the base is zero: a percentage
    against an empty denominator is not a number."""
    if not prev_n:
        return None
    return round((now_n - prev_n) * 100.0 / prev_n, 2)


def _kpi(cfg: dict, today: str, yday: str, now, kill: dict, t: dict) -> dict:
    y = db_reader.activity_day_counts(cfg, yday)
    open_rows = db_reader.position_open_set(cfg)

    state = str((kill or {}).get("state") or "INACTIVE").upper()
    halted = state in ("SOFT_KILL", "HARD_KILL")
    phase = freshness.phase(cfg, now)
    if halted:
        trading_state, tone = "HALTED", "neg"
    elif freshness.market_is_open(cfg, now):
        trading_state, tone = "ACTIVE", "pos"
    else:
        trading_state, tone = "IDLE", "dim"
    basis = "Kill switch %s · %s" % (state, phase.replace("_", " "))
    if halted:
        basis += " · %s" % ((kill or {}).get("reason") or "no reason recorded")

    return {
        "current_time": now.strftime("%H:%M:%S"),
        "current_date": "%02d-%s-%04d" % (now.day, _MONTHS[now.month - 1], now.year),
        "weekday": _WEEKDAYS[now.weekday()],
        "signals_today": t["signals"],
        "signals_yesterday": y["signals"],
        "signals_delta_pct": _delta_pct(t["signals"], y["signals"]),
        "orders_today": t["orders"],
        "orders_yesterday": y["orders"],
        "orders_delta_pct": _delta_pct(t["orders"], y["orders"]),
        "open_positions": len(open_rows),
        # ⛔ NEVER a number — see gaps.open_positions_delta
        "open_positions_delta": None,
        # ⛔ NEVER a number — see gaps.current_mtm
        "current_mtm": None,
        "current_mtm_pct": None,
        "realized_pnl_today": _realized_today(cfg, today),
        "trading_status": trading_state,
        "trading_status_tone": tone,
        "trading_status_basis": basis,
        "compare_date": yday,
        "orders_base": "ENTRY orders placed today",
        "signals_base": "signals stored today",
    }


def _realized_today(cfg: dict, today: str) -> Optional[float]:
    """⭐ REAL, and deliberately shown beside the MTM gap: realised P&L on trades
    closed today. ⛔ It is NOT the MTM and never fills that card."""
    rows = db_reader.activity_trade_exits(cfg, today)
    vals = [r["net_pnl"] for r in rows if r.get("net_pnl") is not None]
    return round(sum(float(v) for v in vals), 2) if vals else None


# ── LIVE PROCESSING PIPELINE ─────────────────────────────────────────────────
def _pipeline(cfg: dict, today: str) -> dict:
    """The six approved stages. The stage with the most recent stamp is marked
    ACTIVE — ⛔ a highlight, not a health claim."""
    p = db_reader.activity_pipeline(cfg, today)
    stages = p["stages"]
    for s in stages:
        s["last"] = _norm(s.get("last"))
        s["last_time"] = _hhmmss(s.get("last"))
    stamped = [s for s in stages if s.get("last")]
    newest = max((s["last"] for s in stamped), default=None)
    for s in stages:
        s["active"] = bool(newest and s.get("last") == newest)
    return {"stages": stages, "base": p["base"], "dropped": p["dropped"],
            "names": list(PIPELINE_STAGES)}


# ── STRATEGY ACTIVITY ────────────────────────────────────────────────────────
def _strategy_activity(cfg: dict, today: str, day: dict) -> dict:
    """Strategy · Signals · Orders · Trades · Last Signal Time.

    ⭐ The three counts come from the SAME THREE BASES the KPI strip uses, so the
    table and the cards above it cannot describe different days of the same
    thing. The dot beside the time reports whether a signal arrived AT ALL today
    — ⛔ a stamp, not a health verdict.

    ⚠️ AN ORDER IS ATTRIBUTED THROUGH ITS TRADE ROW (`orders ⋈ trades.strategy`),
    so an ENTRY order whose trade row is absent CANNOT be attributed to any
    strategy. It is COUNTED and REPORTED as unattributed rather than silently
    dropped — a table whose column quietly totals less than the card above it is
    exactly the drift this screen is meant not to have.
    """
    rows = []
    for r in db_reader.activity_strategy_rows(cfg, today):
        last = _norm(r.get("last"))
        rows.append({
            "strategy": r["strategy"],
            "signals": r["signals"],
            "orders": r["orders"],
            "trades": r["trades"],
            "last_signal": last,
            "last_signal_time": _hhmmss(last),
            "dot": "on" if last else "off",
        })
    rows.sort(key=lambda r: (-r["signals"], -r["orders"], r["strategy"]))
    return {
        "rows": rows,
        "unattributed_orders": max(0, int(day.get("orders") or 0)
                                   - sum(r["orders"] for r in rows)),
        "unattributed_trades": max(0, int(day.get("trades") or 0)
                                   - sum(r["trades"] for r in rows)),
        "base": ("signals by received_at · ENTRY orders by placed_at · "
                 "positions opened by entry_time — the same three bases the "
                 "KPI strip uses"),
    }


# ── RECENT WINNERS / LOSERS ──────────────────────────────────────────────────
def _card(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {"amount": row.get("net_pnl"), "symbol": row.get("symbol"),
            "strategy": row.get("strategy"), "trade_id": row.get("trade_id"),
            "time": _hhmmss(row.get("exit_time")), "ts": _norm(row.get("exit_time"))}


def _winners_losers(exits: list) -> dict:
    """The four approved cards, over trades CLOSED TODAY.

    ⛔ A card with no qualifying trade is None and the panel says so — it is
    never filled with the nearest trade of the other sign, and never with ₹0.
    """
    priced = [r for r in exits if r.get("net_pnl") is not None]
    wins = [r for r in priced if float(r["net_pnl"]) > 0]
    losses = [r for r in priced if float(r["net_pnl"]) < 0]
    return {
        # `exits` is already newest-first by exit_time
        "last_profit": _card(wins[0] if wins else None),
        "last_loss": _card(losses[0] if losses else None),
        "best_trade": _card(max(priced, key=lambda r: float(r["net_pnl"]))
                            if priced else None),
        "worst_trade": _card(min(priced, key=lambda r: float(r["net_pnl"]))
                             if priced else None),
        "base": "trades closed today",
        "closed_today": len(exits),
        "priced": len(priced),
    }


# ── SYSTEM EVENTS panel ──────────────────────────────────────────────────────
#: The binding PNG draws FOUR entries in this panel, with "View All Events →"
#: carrying the rest. ⛔ The cap is never silent — the panel prints "showing 4 of
#: N", because a truncated list that says nothing reads as "a quiet day".
SYSTEM_EVENTS_SHOWN = 4


def _system_events_panel(sysevts: list, crons: list,
                         limit: int = SYSTEM_EVENTS_SHOWN) -> dict:
    """The recorded service-lifecycle events, newest first, each with the Info /
    Warning chip the approved design draws."""
    rows = _from_system_events(sysevts) + _from_cron(crons)
    rows = [r for r in rows if r["ts"]]
    rows.sort(key=lambda r: (r["ts"], r["ref_id"]), reverse=True)
    shown = []
    for r in rows[:limit]:
        chip = "Warning" if r["status"] in ("Failed", "Processing") else "Info"
        shown.append({"ref_id": r["ref_id"], "ts": r["ts"], "time": r["time"],
                      "event": r["event"], "details": r["details"], "chip": chip})
    return {"rows": shown, "shown": len(shown), "total": len(rows),
            "base": "system_events and cron_heartbeat, today"}


# ── ALERTS BANNER ────────────────────────────────────────────────────────────
_SEVERITY_BANNER = {"CRITICAL": "CRITICAL ALERT", "ERROR": "CRITICAL ALERT",
                    "WARNING": "WARNING", "WARN": "WARNING"}


def _alerts_banner(alerts: list, limit: int = 4) -> dict:
    """The approved banner: Critical Alerts and Warnings, from telegram_alerts.

    ⛔ Only rows whose STORED severity says critical or warning appear. Nothing
    is promoted to critical by keyword, and an INFO alert is never shown as a
    warning to make the banner look busy.
    """
    cards = []
    for i, a in enumerate(alerts):
        sev = str(a.get("severity") or "").upper()
        kind = _SEVERITY_BANNER.get(sev)
        if not kind:
            continue
        cards.append({
            "kind": kind,
            "severity": sev,
            "title": a.get("title") or "—",
            "detail": " · ".join(x for x in (a.get("source_module"),
                                             ("delivery " + str(a["status"]))
                                             if a.get("status") else None) if x),
            "time": _hhmmss(a.get("sent_at")),
            "ts": _norm(a.get("sent_at")),
        })
    crit = sum(1 for c in cards if c["kind"] == "CRITICAL ALERT")
    warn = sum(1 for c in cards if c["kind"] == "WARNING")
    return {"cards": cards[:limit], "critical": crit, "warnings": warn,
            "total": len(cards), "base": "telegram_alerts sent today"}


# ── MARKET PULSE ─────────────────────────────────────────────────────────────
def _minute_keys(now, count: int) -> list:
    """The last `count` HH:MM keys ending at the current minute, in order."""
    import datetime as _dt
    base = now.replace(second=0, microsecond=0)
    return [(base - _dt.timedelta(minutes=i)).strftime("%H:%M")
            for i in range(count - 1, -1, -1)]


def _market_pulse(cfg: dict, today: str, now) -> dict:
    """Signals / Orders / Trades per minute over a STATED window.

    ⭐ The rate is (events in the window) / (window length) and the panel prints
    the window, so a 0.00 reads as "nothing in the last hour" — ⛔ not as a
    broken meter. The delta compares this window with the one before it, which
    is the same measure over the same span.
    """
    pulse = db_reader.activity_pulse(cfg, today)
    cur_keys = _minute_keys(now, PULSE_WINDOW_MIN)
    prev_keys = _minute_keys(now, PULSE_WINDOW_MIN * 2)[:PULSE_WINDOW_MIN]
    spark_keys = cur_keys[-PULSE_SPARK_MIN:]

    series = []
    for key, label in (("signals", "Signals Per Minute"),
                       ("orders", "Orders Per Minute"),
                       ("trades", "Trades Per Minute")):
        m = pulse.get(key) or {}
        cur = sum(m.get(k, 0) for k in cur_keys)
        prev = sum(m.get(k, 0) for k in prev_keys)
        rate = round(cur / float(PULSE_WINDOW_MIN), 2)
        prev_rate = round(prev / float(PULSE_WINDOW_MIN), 2)
        series.append({
            "key": key, "label": label,
            "rate": rate, "prev_rate": prev_rate,
            "delta": round(rate - prev_rate, 2),
            "count": cur, "prev_count": prev,
            "spark": [m.get(k, 0) for k in spark_keys],
            "day_total": sum(m.values()),
        })
    return {"series": series, "window_min": PULSE_WINDOW_MIN,
            "spark_min": PULSE_SPARK_MIN,
            "as_of": now.strftime("%H:%M"),
            "base": ("events in the last %d minutes, divided by %d"
                     % (PULSE_WINDOW_MIN, PULSE_WINDOW_MIN))}


# ── ACTIVE POSITIONS ─────────────────────────────────────────────────────────
#: The approved ten columns, in the approved order. ⭐ Symbol BEFORE Strategy,
#: the convention Screens 13-15 settled. ⛔ No Scanner column.
POSITION_COLUMNS = ("Symbol", "Strategy", "Qty", "Entry Price", "LTP",
                    "MTM (₹)", "MTM (%)", "SL", "TGT", "Status")

#: ⛔ Unavailable BY ARCHITECTURE, not by omission — the same three fields
#: /api/positions already declares, under the same reason.
POSITION_UNAVAILABLE = ("ltp", "mtm_rs", "mtm_pct")
BROKER_REASON = "Pending Broker Source (G4)"


def _active_positions(cfg: dict) -> dict:
    """The CURRENT open set — all dates, deliberately.

    📌 A delivery position carried from an earlier day is still open exposure
    now and must not fall out of the wall merely because its entry date is not
    today. That is Screen 06's ruling and this screen reuses it rather than
    inventing a second definition.
    """
    rows = []
    for r in db_reader.open_positions_list(cfg):
        st = str(r.get("status") or "").upper()
        # ⭐ mirrors db_reader._position_status_of over the OPEN set exactly;
        # a test pins the two against each other so they cannot drift.
        display = "Partial Exit" if st == "PARTIAL" else "Open"
        rows.append({
            "trade_id": r.get("trade_id"),
            "symbol": r.get("symbol"),
            "strategy": r.get("strategy"),
            "direction": r.get("direction"),
            # the BROKER-FILLED quantity IS the position (Screen 06, section F)
            "qty": r.get("qty_filled"),
            "qty_planned": r.get("qty_planned"),
            "entry_price": r.get("entry_actual_price"),
            "entry_system": r.get("entry_target_price"),
            # ⛔ None, always — no live price exists in this dashboard
            "ltp": None, "mtm_rs": None, "mtm_pct": None,
            "sl": r.get("sl_initial"),
            "tgt": r.get("tgt_initial"),
            "status": display,
        })
    return {"rows": rows, "count": len(rows),
            "columns": list(POSITION_COLUMNS),
            "unavailable": {"fields": list(POSITION_UNAVAILABLE),
                            "reason": BROKER_REASON},
            "base": "the current open set (all dates), not today's entries"}


# ── CAPITAL UTILIZATION ──────────────────────────────────────────────────────
def _capital(cfg: dict, today: str) -> dict:
    """Utilized / Available / Total, REUSED from Screen 06's summary so the two
    screens cannot report different capital for one account.

    ⛔ When there is no INIT ledger row yet (pre-open) the total is None and the
    ring is not drawn — a ring built from a missing total would be a picture of
    nothing.
    """
    cap = (db_reader.position_summary(cfg, today) or {}).get("capital") or {}
    used = cap.get("used")
    total = cap.get("total")
    avail = cap.get("available")
    have = total is not None and float(total) > 0
    used_pct = round(float(used) / float(total) * 100.0, 2) if (have and used is not None) else None
    avail_pct = round(100.0 - used_pct, 2) if used_pct is not None else None
    # ⚠️ Deployed capital CAN exceed the day's opening capital (leverage, or an
    # opening INIT row written before a mid-day top-up). The legend keeps the
    # TRUE percentages — ⛔ they are never clipped to a tidy 100/0 — and the ring
    # is clamped with the panel saying so, because an arc cannot draw 324%.
    over = bool(used_pct is not None and used_pct > 100.0)
    return {
        "available_basis": have,
        "utilized": used, "available": avail, "total": total,
        "utilized_pct": used_pct, "available_pct": avail_pct,
        "over_utilized": over,
        "over_note": ("utilisation exceeds the day's opening capital, so the "
                      "ring is clamped at 100%; the figures beside it are the "
                      "real ones") if over else None,
        "unpriced": cap.get("unpriced"),
        "reason": None if have else (
            "no INIT row in fm_ledger for today yet, so the day's opening "
            "capital — the base every share resolves against — does not exist"),
        "base": ("filled qty x filled entry over open positions, against the "
                 "day's first INIT ledger row"),
    }


# ── XLSX ─────────────────────────────────────────────────────────────────────
#: ⭐ The approved six feed columns first, in the approved order; Reference ID
#: follows AFTER. ⛔ There is no Scanner column — Screen 18's binding design
#: specifies Strategy, and a test asserts the absence.
EXPORT_HEADER = ("Time", "Category", "Event", "Strategy / Symbol", "Details",
                 "Status", "Date", "Reference ID")


def strategy_symbol(e: dict) -> str:
    """The approved combined cell, as composed on the row itself."""
    return e.get("strategy_symbol") or _strategy_symbol(
        e.get("strategy"), e.get("symbol"))


def export_rows(payload: dict) -> list:
    """Exactly the rows the feed is showing — the SAME filtered list."""
    out = [list(EXPORT_HEADER)]
    for e in payload.get("records") or []:
        out.append([
            e.get("time"), e.get("category"), e.get("event"),
            strategy_symbol(e), e.get("details"), e.get("status"),
            e.get("date"), e.get("ref_id"),
        ])
    return out


# ═══════════════════════════════════════════════════════════════════════════
# SCREEN 02 — the dashboard's RECENT EVENTS feed
# ═══════════════════════════════════════════════════════════════════════════
# ⭐ ADDITIVE AND REUSED, ⛔ NOT a second event architecture: this calls the SAME
# per-category builders and the SAME deterministic sort as build_live_activity,
# so a row on the dashboard and the same row on Screen 18 can never disagree.
# ⛔ It does NOT build the wall (KPI strip / pipeline / capital / positions):
# `/api/dashboard` is polled every 5 s in market hours and the dashboard panel
# needs the feed alone.
#
# ⛔ WHY NOT `system_events` — the source this panel used until 18-Aug. MEASURED
# ON PRODUCTION (18-Aug-2026 12:09 IST, read-only): `system_events` holds ONLY
# STARTUP (74) · SHUTDOWN (72) · KILL_AUTO_CLEARED (40) · CONFIG_DIFF (28) —
# ⛔ ZERO trading events, all time. The approved artwork's rows are all TRADING
# events, so that source could never have shown what the panel promises. Its
# newest ten rows also spanned 13-Aug → 18-Aug with only TWO from today, while
# the template printed HH:MM:SS only — six days of boot noise read as today.
DASH_FEED_LIMIT = 10

#: The longest note this feed will emit. ⭐ THE BOUND IS THE FIX, and it is
#: enforced HERE rather than in CSS: the panel sits in a `46fr 29fr 25fr` grid
#: whose track width is set by its widest unbreakable content, so an unbounded
#: payload does not merely look wrong — it WIDENS THE WHOLE PAGE. The old feed
#: emitted a raw `{"changed_files": [...]}` blob and pushed Screen 02 into a
#: horizontal overflow at 1440. A field that cannot be long cannot do that.
DASH_NOTE_MAX = 40

#: category → the badge the approved Screen-02 artwork draws. ⭐ STRUCTURED: the
#: category is put on the row by the builder that KNOWS it. ⛔ The old dashboard
#: derived this by substring-matching the event TEXT (`evCat()`), and against
#: production's four `system_events` types every row fell through to one bucket
#: — four of its five branches were unreachable.
#: ⭐ The artwork draws SIGNAL / ORDER / RISK / EXIT; the remaining four
#: categories carry their own badge rather than being folded into a near-enough
#: one. `Trade` → EXIT because the artwork badges SL Hit / TGT Hit / Trade
#: Closed — all of them exits — with EXIT.
DASH_CATEGORY_BADGE = {
    "Signal": "SIGNAL",
    "Order": "ORDER",
    "Position": "POSITION",
    "Trade": "EXIT",
    "Risk": "RISK",
    "Capital": "CAPITAL",
    "System": "SYSTEM",
    "Alert": "ALERT",
}


def _dash_note(e: dict) -> Optional[str]:
    """The artwork's short parenthetical — e.g. `(NIFTY 23450 CE)`.

    ⭐ The SYMBOL is preferred because that is what the artwork shows on its
    Order and exit rows. A row with no symbol falls back to the FIRST field of
    the builder's own detail string (never the whole thing), and the result is
    hard-capped at DASH_NOTE_MAX. ⛔ Nothing is composed here that the builders
    did not already record.
    """
    sym = (e.get("symbol") or "").strip()
    if sym:
        return sym[:DASH_NOTE_MAX]
    det = (e.get("details") or "").strip()
    if not det:
        return None
    first = det.split(" | ")[0].strip()
    return (first[:DASH_NOTE_MAX] or None) if first else None


def build_recent_events(cfg: dict, limit: int = DASH_FEED_LIMIT,
                        today: Optional[str] = None) -> dict:
    """Screen 02's RECENT EVENTS — TODAY's real trading events, newest first.

    ⭐ SCOPED TO TODAY BY CONSTRUCTION: every source below is a today-scoped
    reader, so a quiet day returns an EMPTY feed. ⛔ It does NOT reach back for
    filler — the panel sits under a header of `Today` KPIs, and a row from six
    days ago printed as `08:15:43` is worse than no row at all.
    """
    now = freshness.ist_now()
    today = today or freshness.ist_today_iso(now)

    signals = db_reader.activity_signals_today(cfg, today)
    kill = db_reader.get_kill_switch(cfg)

    events = []
    events += _from_signals(signals)
    events += _from_orders(db_reader.order_screen_rows(cfg, today))
    events += _from_positions(db_reader.activity_positions_opened(cfg, today))
    events += _from_trades(db_reader.activity_trade_exits(cfg, today))
    events += _from_risk(signals, kill)
    events += _from_capital(db_reader.ledger_entries(cfg, today))
    events += _from_system_events(db_reader.syslog_events_range(cfg, today, today))
    events += _from_cron(db_reader.syslog_cron_range(cfg, today, today))
    events += _from_alerts(db_reader.telegram_alerts_today(cfg, today))

    # ⛔ A ROW FROM ANOTHER DAY MAY NOT ENTER A *TODAY* PANEL, and the date is
    # checked rather than assumed. Most sources here are already today-scoped
    # readers, but not all are: the kill-switch row is built from the PERSISTED
    # `kill_switch_state.triggered_at`, which survives across days — measured
    # 18-Aug, a kill triggered on 03-AUG arrived in this feed and rendered as
    # `15:34:38` with no date, i.e. as though it had just happened. That is the
    # precise defect this panel was rebuilt to stop, so the guard is explicit and
    # it is a PROPERTY of the feed, ⛔ not a property of any one source.
    events = [e for e in events if e["ts"] and e.get("date") == today]
    # newest first; ref_id breaks ties so a 5-second poll cannot reshuffle rows.
    events.sort(key=lambda e: (e["ts"], e["ref_id"]), reverse=True)

    rows = []
    for e in events[:max(0, int(limit))]:
        rows.append({
            "ts": e["ts"],
            "time": e["time"],
            "date": e["date"],
            "category": e["category"],
            "badge": DASH_CATEGORY_BADGE.get(e["category"], "SYSTEM"),
            "event": e["event"],
            "note": _dash_note(e),
            "status": e.get("status"),
            "link": e.get("link"),
            "ref_id": e.get("ref_id"),
        })

    return {
        "today": today,
        "records": rows,
        "count": len(rows),
        "total_today": len(events),
    }


#: The Signals Received sparkline's window. ⭐ THE SAME 30 MINUTES Screen 18's
#: MARKET PULSE sparks already use, so the two screens draw one definition of
#: "recent signal activity" and cannot disagree about it.
DASH_SPARK_MIN = PULSE_SPARK_MIN


def build_signals_spark(cfg: dict, today: Optional[str] = None, now=None) -> dict:
    """The approved artwork's sparkline on the SIGNALS RECEIVED card.

    ⭐ REAL PER-MINUTE ROWS, from the SAME `activity_pulse` reader Screen 18's
    pulse uses — ⛔ nothing smoothed, back-filled or interpolated, and ⛔ no second
    definition of the series. A minute with no signal is a real zero.

    ⛔ `available` IS FALSE WHEN EVERY POINT IS ZERO and the card then draws no
    line at all. A flat line along the axis is not a cheaper truth — it is a
    drawn chart, and a reader takes a drawn chart as a measurement of shape. The
    number above it already says nothing arrived.
    """
    now = now or freshness.ist_now()
    today = today or freshness.ist_today_iso(now)
    per_min = (db_reader.activity_pulse(cfg, today) or {}).get("signals") or {}
    keys = _minute_keys(now, DASH_SPARK_MIN)
    points = [int(per_min.get(k, 0)) for k in keys]
    return {
        "points": points,
        "window_min": DASH_SPARK_MIN,
        "total_today": int(sum(per_min.values())),
        "available": any(p > 0 for p in points),
    }
