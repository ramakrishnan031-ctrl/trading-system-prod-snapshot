"""SCREEN 15 — SYSTEM LOGS.  A technical troubleshooting console.

`gui/15. System_Logs.png` + `.txt` are BINDING for structure.

═══════════════════════════════════════════════════════════════════════════════
⭐ WHAT THIS SCREEN CAN AND CANNOT KNOW — measured before a line was written

THREE real sources, and they answer DIFFERENT parts of the approved table:

  · the dated JSON logs (`system_`/`reconciler_`/`trades_<date>.log`)
      → REAL severity (`level`) and REAL module (`logger`) and the message.
      ⛔ They carry NO event type and NO resolution status: a logger emits a
         line, not a lifecycle.
  · `system_events`  → the ONLY structured EVENT TYPE vocabulary in the schema.
      Measured on production: STARTUP · SHUTDOWN · CRASH_DETECTED · CONFIG_DIFF
      ⇒ only Service Started / Stopped / Restarted of the eleven approved types
      are evidenced. The rest are declared uninstrumented, ⛔ never shown as 0.
  · `cron_heartbeat` → 2132 rows with a STRUCTURED status (SUCCESS/FAILED), so
      the Scheduler is the one service whose Status column is real.

⛔ TRADING IMPACT IS NOT INSTRUMENTED AT ALL. Nothing in this system classifies
an event as No / Minor / Major impact or Halt Risk, and inferring it from a
severity would be exactly the fabrication the brief forbids — an ERROR in a
report generator and an ERROR in the order path are not the same risk, and
nothing stored distinguishes them. The whole panel says so.

⛔ ERROR CODE does not exist either: a repo-wide search for an `XXX-0000` scheme
returns zero. Neither does a resolution time, nor a recovery duration.
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
from typing import Optional

from ..readers import db_reader, log_reader
from . import freshness, system_health

# ── the approved vocabularies (design §SERVICES / §SEVERITY / §EVENT TYPES) ───
SERVICES = ("Signal Engine", "Risk Engine", "Capital Engine", "Order Engine",
            "Broker Connector", "Database", "Dashboard API", "Scheduler",
            "Recovery Engine", "System Monitor")

SEVERITIES = ("Info", "Warning", "Error", "Critical")

EVENT_TYPES = ("Service Started", "Service Stopped", "Service Restarted",
               "Connection Established", "Connection Lost", "Database Error",
               "API Error", "Timeout", "Authentication Failure",
               "Recovery Triggered", "Recovery Completed")

#: Evidenced by a stored, STRUCTURED value: the first three by
#: `system_events.event_type`, the last two by `reconciliation_log.success`.
#: ⛔ The other six are NOT zero — they are unmeasurable, and the panel prints
#: that instead of a count. ⚠️ This list and what `_from_*` can actually emit
#: must agree: a type marked uninstrumented while rows carry it would show a
#: count beside the words NOT INSTRUMENTED, and a test pins that they match.
INSTRUMENTED_EVENT_TYPES = ("Service Started", "Service Stopped",
                            "Service Restarted",
                            "Recovery Triggered", "Recovery Completed")

TIMELINE_STAGES = ("Event Detected", "Logged", "Action Taken", "Resolved")

TRADING_IMPACTS = ("No Impact", "Minor Impact", "Major Impact",
                   "Trading Halt Risk")

DEPENDENCIES = ("Chartink", "Broker", "Database", "Tailscale", "Internet", "VM")

_LEVEL_SEVERITY = {"DEBUG": "Info", "INFO": "Info", "WARNING": "Warning",
                   "ERROR": "Error", "CRITICAL": "Critical"}

# logger → approved SERVICE. ⭐ A documented MAPPING over a REAL field, ⛔ not a
# measurement and ⛔ not a guess about which process ran: `logger` is the module
# that emitted the line, and these are this repository's own package names. A
# logger that matches nothing maps to None and renders "—" — ⛔ it is never
# forced into a service to make the panel look complete.
_SERVICE_OF_MODULE = (
    ("Signal Engine",    ("signal_processor", "webhook_receiver", "entry_gate",
                          "v3_chain", "screening", "secondary_screener",
                          "quality_scorer", "sr_detector", "pb01_watchlist")),
    ("Risk Engine",      ("risk_engine", "kill_switch")),
    ("Capital Engine",   ("fund_manager", "portfolio_allocator")),
    ("Order Engine",     ("order_placer", "order_manager", "order_monitor",
                          "order_protocol", "tgt_retry_manager",
                          "breakeven_manager", "structure_exit_manager",
                          "cnc_gtt", "eod_squareoff", "smart_tgt")),
    ("Broker Connector", ("zerodha_adapter", "live_feed", "paper_quote_provider",
                          "token_monitor")),
    ("Database",         ("state_store",)),
    ("Dashboard API",    ("waitress", "ops_dashboard")),
    ("Scheduler",        ("cron", "scheduler")),
    ("Recovery Engine",  ("order_reconciler", "recovery")),
    ("System Monitor",   ("main", "healthcheck", "core.config_validator",
                          "config_validator", "strategies.loader",
                          "telegram_notifier")),
)


def _service_of(module: Optional[str]) -> Optional[str]:
    if not module:
        return None
    m = str(module).lower()
    for service, prefixes in _SERVICE_OF_MODULE:
        for p in prefixes:
            if m == p or m.startswith(p + ".") or m.startswith(p):
                return service
    return None


def _parts(ts: Optional[str]) -> tuple:
    if not ts:
        return None, None
    s = str(ts).replace("T", " ")
    return (s[:10] or None), (s[11:19] or None)


def _norm(ts: Optional[str]) -> Optional[str]:
    if not ts:
        return None
    s = str(ts).replace("T", " ").strip()
    return s[:19] if len(s) >= 19 else s


def _gap(reason: str) -> dict:
    return {"measured": False, "value": None, "reason": reason}


def _ev(ref_id, ts, service, module, severity, message, *,
        event_type=None, status=None, **extra) -> dict:
    d, t = _parts(ts)
    row = {
        "ref_id": ref_id, "ts": _norm(ts), "date": d, "time": t,
        "service": service, "module": module,
        "severity": severity,
        "event_type": event_type,      # None ⇒ NOT INSTRUMENTED in the UI
        "status": status,              # None ⇒ NOT INSTRUMENTED in the UI
        "message": message,
        # ⛔ None, always: no error-code scheme, no resolution tracking and no
        # trading-impact classification exists anywhere in this system.
        "error_code": None,
        "resolution_status": None,
        "resolution_time": None,
        "trading_impact": None,
    }
    row.update(extra)
    return row


# ── event synthesis ──────────────────────────────────────────────────────────
def _from_logfiles(read: dict) -> list:
    out = []
    for r in read.get("rows") or []:
        lvl = str(r.get("level") or "").upper()
        out.append(_ev(
            r["ref_id"], r.get("ts"),
            _service_of(r.get("logger")), r.get("logger"),
            _LEVEL_SEVERITY.get(lvl, "Info"), r.get("msg"),
            source="log", source_file=r.get("source_file"),
            trade_id=r.get("trade_id"), order_id=r.get("order_id"),
        ))
    return out


#: ⛔ Only where the STORED type means the approved type. `CONFIG_DIFF` and
#: `KILL_SWITCH` are real events but are NOT among the eleven approved types, so
#: they carry no event type rather than being forced into a near-enough one.
_EVENT_OF_SYSTEM_EVENT = {
    "STARTUP": "Service Started",
    "SHUTDOWN": "Service Stopped",
    "CRASH_DETECTED": "Service Restarted",
    # schema: "written when reconciler completes recovery procedure"
    "RECOVERY": "Recovery Completed",
}


def _from_system_events(rows: list) -> list:
    """⭐ The ONLY rows on this screen with a REAL event type."""
    out = []
    for r in rows:
        et_raw = (r.get("event_type") or "").upper()
        et = _EVENT_OF_SYSTEM_EVENT.get(et_raw)
        scen = r.get("scenario")
        sev = "Critical" if et_raw == "CRASH_DETECTED" else "Info"
        msg = et_raw.replace("_", " ").title()
        if scen:
            msg += " (scenario: %s)" % scen
        out.append(_ev(
            "SYSEVT-%s" % r.get("event_id"), r.get("timestamp"),
            "System Monitor", et_raw.lower(), sev, msg,
            event_type=et, source="system_events", scenario=scen,
        ))
    return out


def _from_cron(rows: list) -> list:
    """⭐ The ONLY rows with a REAL status — `cron_heartbeat.status` is a stored
    SUCCESS/PARTIAL/FAILED column, ⛔ not a phrase parsed out of a message."""
    out = []
    for r in rows:
        st_raw = (r.get("status") or "").upper()
        sev = "Error" if st_raw == "FAILED" else (
            "Warning" if st_raw == "PARTIAL" else "Info")
        dur = r.get("duration_sec")
        msg = r.get("message") or ("Scheduled job %s" % (r.get("job_name") or ""))
        out.append(_ev(
            "CRON-%s" % r.get("id"), r.get("executed_at"),
            "Scheduler", r.get("job_name"), sev, msg,
            status=st_raw.title() if st_raw else None,
            source="cron_heartbeat",
            # ⛔ never defaulted to 0 — populated on 4 of 2132 production rows
            duration_sec=dur,
        ))
    return out


def _from_recovery(rows: list) -> list:
    out = []
    for r in rows:
        ok = int(r.get("success") or 0) == 1
        out.append(_ev(
            "RECOV-%s" % r.get("id"), r.get("ts"),
            "Recovery Engine", r.get("check_name"),
            "Info" if ok else "Error",
            r.get("description") or r.get("action_taken") or "Recovery action",
            event_type="Recovery Completed" if ok else "Recovery Triggered",
            status="Success" if ok else "Failed",
            source="reconciliation_log",
            recovery_method=r.get("action_taken"),
            recovery_result="Success" if ok else "Failed",
            tier=r.get("tier"),
        ))
    return out


# ── the screen ───────────────────────────────────────────────────────────────
def _matches(e: dict, f: dict) -> bool:
    """FILTERS are EXACT (they are dropdowns of values that exist); the approved
    SEARCH panel is CONTAINS (it is free text). ⭐ They are separate parameters
    on purpose — binding a typed fragment to a dropdown's exact match would make
    the search silently return nothing for every partial word."""
    def eq(key, val):
        return not val or str(e.get(key) or "") == str(val)

    def has(key, val):
        return not val or str(val).strip().lower() in str(e.get(key) or "").lower()

    # ── the approved SEARCH fields ───────────────────────────────────────────
    if not has("service", f.get("service_q")):
        return False
    if not has("module", f.get("module_q")):
        return False
    if not has("message", f.get("message_q")):
        return False
    if not has("ref_id", f.get("ref_q")):
        return False
    # ⛔ There is deliberately NO error-code search parameter: no error-code
    # scheme exists in this system, so a box that accepted one could only ever
    # return nothing. The field is shown as the approved design requires and is
    # declared NOT INSTRUMENTED instead of quietly matching zero rows.

    # ── the approved FILTER dropdowns ────────────────────────────────────────
    if not eq("service", f.get("service")):
        return False
    if not eq("module", f.get("module")):
        return False
    if not eq("severity", f.get("severity")):
        return False
    if not eq("event_type", f.get("event_type")):
        return False
    if not eq("status", f.get("status")):
        return False
    q = (f.get("q") or "").strip().lower()
    if q:
        hay = " ".join(str(e.get(k) or "") for k in
                       ("service", "module", "message", "severity",
                        "event_type", "status", "ref_id"))
        if q not in hay.lower():
            return False
    return True


def build_system_logs(cfg: dict, start: Optional[str] = None,
                      end: Optional[str] = None, **filters) -> dict:
    """The whole screen from ONE filtered population, so no card, chart or panel
    can disagree with the table beside it."""
    today = freshness.ist_today_iso()
    end = end or today
    if not start:
        import datetime as _dt
        y, m, d = (int(x) for x in end.split("-"))
        start = (_dt.date(y, m, d) - _dt.timedelta(days=6)).isoformat()

    read = log_reader.read_system_logs(cfg, start, end)
    events = []
    events += _from_logfiles(read)
    events += _from_system_events(db_reader.syslog_events_range(cfg, start, end))
    events += _from_cron(db_reader.syslog_cron_range(cfg, start, end))
    events += _from_recovery(db_reader.tradelog_recovery_range(cfg, start, end))

    events = [e for e in events if e["ts"] and start <= e["date"] <= end]
    # newest first; ⭐ ref_id breaks ties so equal stamps order DETERMINISTICALLY
    # and a refresh can never reshuffle, duplicate or drop two rows.
    events.sort(key=lambda e: (e["ts"], e["ref_id"]), reverse=True)

    shown = [e for e in events if _matches(e, filters)]
    n = len(shown)

    def cnt(sev):
        return sum(1 for e in shown if e["severity"] == sev)

    def pct(x):
        return round(100.0 * x / n, 2) if n else 0.0

    kpi = {
        "total_events": n,
        "info": cnt("Info"), "info_pct": pct(cnt("Info")),
        "warnings": cnt("Warning"), "warnings_pct": pct(cnt("Warning")),
        "errors": cnt("Error"), "errors_pct": pct(cnt("Error")),
        "critical": cnt("Critical"), "critical_pct": pct(cnt("Critical")),
        "last_event_ts": shown[0]["ts"] if shown else None,
    }

    by_sev = {s: cnt(s) for s in SEVERITIES}
    by_event = {t: sum(1 for e in shown if e["event_type"] == t)
                for t in EVENT_TYPES}

    def opts(key):
        return sorted({str(e.get(key)) for e in events if e.get(key)})

    health = _health(cfg)
    services_panel = _services_panel(shown, health)

    return {
        "generated_at": freshness.ist_now().strftime("%Y-%m-%d %H:%M:%S"),
        "from": start, "to": end,
        "records": shown, "count": n, "total_unfiltered": len(events),
        "kpi": kpi,
        "sparklines": _sparklines(shown, start, end),
        "by_severity": by_sev,
        "by_event_type": by_event,
        "severity_donut": _donut(by_sev, n),
        "services": services_panel,
        "systemd_services": health["services"],
        "dependencies": health["dependencies"],
        "retention": db_reader.syslog_retention(cfg),
        "log_scan": {k: read[k] for k in
                     ("files", "lines", "skipped_not_json",
                      "skipped_foreign_shape", "bytes_read")},
        "service_names": list(SERVICES),
        "severities": list(SEVERITIES),
        "event_types": list(EVENT_TYPES),
        "instrumented_event_types": list(INSTRUMENTED_EVENT_TYPES),
        "timeline_stages": list(TIMELINE_STAGES),
        "trading_impacts": list(TRADING_IMPACTS),
        "filters": {"options": {
            "service": opts("service"), "module": opts("module"),
            "severity": [s for s in SEVERITIES if by_sev.get(s)],
            "event_type": [t for t in EVENT_TYPES if by_event.get(t)],
            "status": opts("status"),
        }},
        "gaps": {
            "trading_impact": _gap(
                "nothing in this system classifies an event's trading impact. "
                "Deriving it from severity would be a guess — an ERROR in a "
                "report generator and an ERROR in the order path are not the "
                "same risk, and nothing stored tells them apart"),
            "event_type": _gap(
                "only system_events carries a structured event type (STARTUP / "
                "SHUTDOWN / CRASH_DETECTED / CONFIG_DIFF), so eight of the "
                "eleven approved types have no source; a log line records a "
                "message, not a lifecycle"),
            "error_code": _gap(
                "no error-code scheme exists in this system — a repo-wide "
                "search for an XXX-0000 code returns zero, so the approved "
                "Error Code search field is shown but cannot be queried: a box "
                "that accepted one could only ever return nothing"),
            "resolution": _gap(
                "log lines carry no resolution status, time or resolved-at; "
                "only cron_heartbeat and reconciliation_log record an outcome"),
            "timeline": _gap(
                "a log line has ONE timestamp. 'Logged' is real; Event "
                "Detected, Action Taken and Resolved are not separately "
                "instrumented"),
            "recovery_duration": _gap(
                "no recovery duration is stored; cron_heartbeat.duration_sec "
                "is populated on 4 of 2132 production rows and is per-row"),
            "debug_log": _gap(
                "debug_<date>.log is PLAIN TEXT (its own formatter), so it is "
                "not parsed here; the alert-failure logs carry a different "
                "record shape and are counted as skipped rather than shown"),
        },
        "note": ("Severity "
                 "and Module come from the log record itself; Service is a "
                 "documented mapping over the module name. Status and Event "
                 "Type are shown only where a structured column records them."),
    }


#: The ONLY services a real probe names unambiguously. ⛔ Everything else is left
#: without a health status rather than mapped to a near-enough probe: Screen 12
#: already established that the reference's engines are THREADS inside one
#: systemd service, so claiming a per-engine health here would be inventing a
#: measurement that does not exist.
_HEALTH_SOURCE = {"Database": "database", "Broker Connector": "broker"}


def _services_panel(rows: list, health: dict) -> list:
    """The ten approved service names, each with what is genuinely known.

    ⭐ `events` and `worst` are REAL — counted from the very population the table
    is showing, which is exactly what a LOGS console can prove about a component.
    ⛔ `status` is filled only where a probe names that service; otherwise it is
    None and the UI prints NOT INSTRUMENTED. ⛔ A service is never called healthy
    because it happens to have logged nothing.
    """
    order = {s: i for i, s in enumerate(SEVERITIES)}
    counts: dict = {}
    worst: dict = {}
    for e in rows:
        svc = e.get("service")
        if not svc:
            continue
        counts[svc] = counts.get(svc, 0) + 1
        cur = worst.get(svc)
        if cur is None or order.get(e["severity"], 0) > order.get(cur, 0):
            worst[svc] = e["severity"]

    by_name = {}
    for row in health.get("services") or []:
        by_name[str(row.get("service") or "").lower()] = row

    out = []
    for name in SERVICES:
        probe = _HEALTH_SOURCE.get(name)
        status = None
        if probe:
            hit = health.get(probe) or {}
            status = hit.get("status") or None
        out.append({"service": name, "status": status,
                    "events": counts.get(name, 0), "worst": worst.get(name)})
    return out


def _health(cfg: dict) -> dict:
    """⭐ REUSED, ⛔ not reimplemented: Screen 12 already derives service and
    dependency health from real probes and already ruled that the reference's
    'Signal Engine / Risk Engine / …' are THREADS inside one systemd service —
    so the two screens cannot report different health for one system."""
    try:
        payload = system_health.build_system_health(cfg)
    except Exception:
        return {"services": [], "dependencies": [], "database": {}, "broker": {}}
    deps = list(payload.get("dependencies") or [])
    vm = payload.get("vm") or {}
    if not any(d.get("name") == "VM" for d in deps):
        deps.append({
            "name": "VM",
            "status": vm.get("status") or "UNKNOWN",
            "source": "host metrics collected on the VM itself",
            "measured": bool(vm.get("status")),
        })
    return {"services": payload.get("services") or [], "dependencies": deps,
            "database": payload.get("database") or {},
            "broker": payload.get("broker") or {}}


def _days(rows: list, start: str, end: str) -> tuple:
    """Contiguous buckets derived from the DATA's own span inside the window,
    falling back to months when that span is too long to draw daily — the fix
    Screen 14 needed so a chart cannot report a total the table disagrees with."""
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
    if (hi - lo).days + 1 <= 400:
        out, cur = [], lo
        while cur <= hi:
            out.append(cur.isoformat())
            cur += _dt.timedelta(days=1)
        return out, (lambda d: d)
    out, y, m = [], lo.year, lo.month
    while (y, m) <= (hi.year, hi.month):
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out, (lambda d: (d or "")[:7])


def _sparklines(rows: list, start: str, end: str) -> dict:
    days, keyfn = _days(rows, start, end)
    idx = {d: i for i, d in enumerate(days)}
    out = {k: [0] * len(days) for k in
           ("total_events", "info", "warnings", "errors", "critical")}
    for e in rows:
        i = idx.get(keyfn(e["date"]))
        if i is None:
            continue
        out["total_events"][i] += 1
        key = {"Info": "info", "Warning": "warnings",
               "Error": "errors", "Critical": "critical"}.get(e["severity"])
        if key:
            out[key][i] += 1
    out["days"] = days
    return out


def _donut(by_sev: dict, total: int) -> list:
    """The approved SEVERITY donut. ⭐ Arcs are cumulative fractions of the SAME
    population the table shows, so the ring closes at exactly 100%."""
    out, acc = [], 0.0
    for sev in SEVERITIES:
        v = by_sev.get(sev) or 0
        frac = (v / total) if total else 0.0
        out.append({"severity": sev, "count": v,
                    "pct": round(100.0 * frac, 2),
                    "offset": round(acc, 6), "frac": round(frac, 6)})
        acc += frac
    return out


# ── per-event detail: component timeline, error, recovery ────────────────────
def event_detail(payload: dict, ref_id: str) -> Optional[dict]:
    """Resolved from the SAME filtered build as the table, so a Reference ID
    always addresses the row the operator actually clicked."""
    rec = next((e for e in (payload.get("records") or [])
                if e["ref_id"] == ref_id), None)
    if rec is None:
        return None

    def stage(name, ts, gap=None):
        d, t = _parts(ts)
        return {"stage": name, "ts": _norm(ts), "date": d, "time": t,
                "measured": ts is not None, "gap": gap}

    # ⛔ Only "Logged" is real for a log line. A recovery row additionally has a
    # real "Action Taken" (the reconciler acted at that instant) and a real
    # "Resolved" when it reports success — ⛔ nothing else is stamped.
    is_recovery = rec.get("source") == "reconciliation_log"
    is_error = rec["severity"] in ("Error", "Critical")
    resolved_ok = is_recovery and rec.get("recovery_result") == "Success"
    timeline = [
        stage("Event Detected", None,
              gap="detection is not separately stamped; the log entry is the "
                  "first record of the event"),
        stage("Logged", rec.get("ts")),
        stage("Action Taken", rec.get("ts") if is_recovery else None,
              gap=None if is_recovery else
                  "no action is recorded against this event"),
        stage("Resolved", rec.get("ts") if resolved_ok else None,
              gap=None if resolved_ok else
                  "no resolution is recorded for this event"),
    ]

    return {
        "found": True,
        "record": rec,
        "timeline": timeline,
        "stages_measured": sum(1 for s in timeline if s["measured"]),
        "stages_total": len(timeline),
        # ⛔ ERROR INVESTIGATION describes an ERROR. On an INFO row there is
        # nothing to resolve, so a status is NOT borrowed from the event's own
        # outcome — "Resolution Status: Success" beside "Error Message: —" would
        # imply an error was investigated and closed when none was raised.
        "error": {
            "error_code": None,          # ⛔ no scheme exists
            "error_message": rec.get("message") if is_error else None,
            "resolution_status": rec.get("status") if (is_error or is_recovery)
                                 else None,
            "resolution_time": None,     # ⛔ not stored
            "resolved_at": None,         # ⛔ not stored
        },
        # ⛔ RECOVERY DURATION is a RECOVERY's duration. `cron_heartbeat
        # .duration_sec` is a scheduled JOB's runtime — real, but a different
        # quantity, and printing it here labelled "Recovery Duration" would
        # rename one measurement into another.
        "recovery": {
            "triggered": rec.get("ts") if is_recovery else None,
            "method": rec.get("recovery_method"),
            "result": rec.get("recovery_result"),
            "duration_sec": rec.get("duration_sec") if is_recovery else None,
        },
        "job_duration_sec": rec.get("duration_sec"),   # kept, under its own name
        "trading_impact": None,          # ⛔ never inferred
    }


def replay(payload: dict, ref_id: str, span: int = 6) -> list:
    """SYSTEM REPLAY — the events around this one, in real order.

    ⭐ It REPLAYS the recorded sequence; ⛔ it does not simulate a lifecycle. If
    the store holds only one event, the replay is one event long and says so.
    """
    rows = payload.get("records") or []
    i = next((k for k, e in enumerate(rows) if e["ref_id"] == ref_id), None)
    if i is None:
        return []
    lo = max(0, i - span)
    window = rows[lo:i + span + 1]
    return [{"ref_id": e["ref_id"], "ts": e["ts"], "time": e["time"],
             "service": e["service"], "module": e["module"],
             "severity": e["severity"], "event_type": e["event_type"],
             "message": e["message"], "is_focus": e["ref_id"] == ref_id}
            for e in reversed(window)]


# ── XLSX ─────────────────────────────────────────────────────────────────────
#: ⭐ The approved eight table columns first, in the approved order; Reference ID
#: follows AFTER. ⛔ There is no Scanner column and no Strategy column — Screen
#: 15's binding design specifies neither.
EXPORT_HEADER = ("Date", "Time", "Service", "Module", "Severity",
                 "Event Type", "Status", "Message", "Reference ID")


def export_rows(payload: dict) -> list:
    """Exactly the rows the table is showing — the SAME filtered list."""
    out = [list(EXPORT_HEADER)]
    for e in payload.get("records") or []:
        out.append([
            e.get("date"), e.get("time"), e.get("service"), e.get("module"),
            e.get("severity"),
            e.get("event_type") or "NOT INSTRUMENTED",
            e.get("status") or "NOT INSTRUMENTED",
            e.get("message"), e.get("ref_id"),
        ])
    return out
