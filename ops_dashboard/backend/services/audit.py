"""SCREEN 13 — AUDIT. Accountability and traceability across the trading system.

The approved design (`gui/13. Audit.png` + `.txt`) is binding for STRUCTURE.
The DATA is this system's, and where this system does not record something the
gap is stated rather than filled — Rama, 15-Aug: *"Do NOT fabricate: audit event
counts, users, login/logout audit history, source attribution,
requested/validated/applied/confirmed timestamps, retention period, old/new
values, reference IDs, any other audit evidence."*

WHAT THE SYSTEM ACTUALLY RECORDS (established by reading writers, ⛔ not assumed)
  ✅ system_events            STARTUP · SHUTDOWN · CRASH_DETECTED · CONFIG_DIFF
  ✅ config_snapshots         the FULL resolved AppConfig as JSON, per change
  ✅ reconciliation_log       check · action_taken · success (a real outcome)
  ✅ webhook_audit            inbound POST + response code
  ✅ control_tower_findings   detections
  ✅ preflight_runs           overall_status
  ⚠️ kill_switch_state        SINGLE ROW (CHECK (id=1)) — the CURRENT state only

⭐⭐ THE OLD → NEW VALUES ARE REAL, AND THIS IS HOW.
  `config_snapshots.config_json` is the full resolved AppConfig, written once per
  change (deduped by hash). Diffing CONSECUTIVE snapshots yields genuine
  field-level `old → new` pairs — the reference's centrepiece (`Max Orders
  100 → 150`) without inventing a single value.
  ⚠️ TIMESTAMP SEMANTICS, STATED ON THE SCREEN: a snapshot is written at STARTUP,
  so its stamp is *when the system OBSERVED this configuration*, ⛔ NOT when
  somebody edited the file. ⛔ A change is never inferred where two consecutive
  snapshots do not prove it.

⛔ WHAT DOES NOT EXIST, WITH THE SEARCH WIDTH
  ⛔ USER / who-changed — `ops_dashboard/backend/auth.py` touches NO database, so
     login/logout is never persisted ⇒ the AUTHENTICATION category has no source
     at all. Repo-wide grep for old_value/new_value/previous_value/changed_from
     over every .py and .sql (excluding tests and vendored sats/) returns ZERO.
     The only actor column in the schema is `kill_switch_state.triggered_by`,
     whose live value is `order_monitor` — a PROCESS, not a person.
  ⛔ SOURCE (Dashboard/System/API/Scheduler/Recovery Engine) — not recorded. The
     one exception is genuine rather than inferred: a `webhook_audit` row exists
     BECAUSE an inbound HTTP POST arrived, so its source IS the API.
  ⛔ LIFECYCLE — only ONE instant per record is recorded. `Applied` is real;
     Requested / Validated / Confirmed are not instrumented anywhere.
  ⛔ RETENTION PERIOD — no audit retention policy exists. `signal_retention_days`
     governs SIGNAL fingerprints, ⛔ not audit, and must not be shown as if it did.
  ⛔ CONTROL HISTORY — the kill-switch table cannot hold one.

⛔ NO SCANNER IDENTITY ANYWHERE. Project-wide rule: KEEP STRATEGY, REMOVE
SCANNER. `webhook_audit.scanner_name` is not even SELECTed (see db_reader).
"""
from __future__ import annotations

import json
from typing import Optional

from ..readers import db_reader
from . import freshness

# ── the approved vocabularies (13. Audit.txt) ────────────────────────────────
CATEGORIES = ("Configuration", "Control", "Strategy", "Risk", "Capital",
              "Service", "Authentication", "System")
STATUSES = ("Success", "Failed", "Partial")
SOURCES = ("Dashboard", "System", "API", "Scheduler", "Recovery Engine")
TIMELINE_STAGES = ("Requested", "Validated", "Applied", "Confirmed")

# ── config path → approved category + module ─────────────────────────────────
# ⭐ Derived from the REAL section names in config_json, ⛔ not invented: the
# snapshot's top level is broker_costs · broker_limits · chartink_scanners ·
# file_hashes · nse_holidays · scan_webhook_map · scoring · slippage · system,
# and `system` carries risk · capital · portfolio_allocator · entry_gate · … .
# Longest prefix wins, so `system.risk` beats a bare `system`.
_PATH_CATEGORY = (
    ("system.risk", "Risk", "Risk Engine"),
    ("system.kill_switch", "Risk", "Kill Switch"),
    ("system.circuit_breaker", "Risk", "Circuit Breaker"),
    ("system.strategy_circuit_breaker", "Risk", "Strategy Circuit Breaker"),
    ("system.drift_handler", "Risk", "Drift Handler"),
    ("system.capital", "Capital", "Capital Engine"),
    ("system.portfolio_allocator", "Capital", "Portfolio Allocator"),
    ("system.position_sizing", "Capital", "Position Sizing"),
    ("system.entry_gate", "Strategy", "Entry Gate"),
    ("system.v3_chain", "Strategy", "V3 Chain"),
    ("system.signal_processor", "Strategy", "Signal Processor"),
    ("system.smart_tgt", "Strategy", "Smart Target"),
    ("system.structure_exit", "Strategy", "Structure Exit"),
    ("system.regime", "Strategy", "Regime"),
    ("system.sr_detector", "Strategy", "SR Detector"),
    ("scoring", "Strategy", "Scoring"),
    ("system.order_monitor", "Service", "Order Monitor"),
    ("system.order_reconciler", "Service", "Order Reconciler"),
    ("system.alerts", "Service", "Alerts"),
    ("system.broker", "Service", "Broker"),
    ("system.live_feed", "Service", "Live Feed"),
    ("system.eod_squareoff", "Service", "EOD Squareoff"),
    ("system.eod_reconcile", "Service", "EOD Reconcile"),
    ("system.eod_cleanup", "Service", "EOD Cleanup"),
)


def _classify_path(path: str) -> tuple:
    """(category, module) for a dotted config path. Longest prefix wins."""
    best = None
    for prefix, cat, mod in _PATH_CATEGORY:
        if (path == prefix or path.startswith(prefix + ".")) and (
                best is None or len(prefix) > len(best[0])):
            best = (prefix, cat, mod)
    if best:
        return best[1], best[2]
    # ⛔ Everything else is plain Configuration under its own section name —
    # ⛔ never guessed into Risk/Capital/Strategy just to fill those buckets.
    return "Configuration", path.split(".")[0]


# ── small helpers ────────────────────────────────────────────────────────────
def _parts(ts: Optional[str]) -> tuple:
    """(date, time) split of an ISO IST stamp. ⛔ Never invents either half."""
    if not ts:
        return None, None
    s = str(ts).replace("T", " ")
    return s[:10] or None, s[11:19] or None


def _gap(reason: str) -> dict:
    """A field this system does not record. ⛔ No value, and it can never read
    as a pass — same contract as Screen 12's instrumentation gaps."""
    return {"measured": False, "value": None, "reason": reason}


def _flatten(obj, prefix: str = "") -> dict:
    """dotted-path → scalar. Lists are compared whole (a list is one setting)."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, "%s.%s" % (prefix, k) if prefix else str(k)))
    else:
        out[prefix] = obj
    return out


def _render(v) -> str:
    """A config value as the operator sees it. ⛔ Never truncated to the point
    of changing meaning — an old/new pair the reader cannot trust is worse than
    no pair at all."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, dict)):
        s = json.dumps(v, separators=(",", ":"), sort_keys=True)
        return s if len(s) <= 160 else s[:157] + "…"
    return str(v)


_NOISY_PREFIXES = ("file_hashes.",)   # a hash change IS the change, not a cause


def _config_changes(snaps: list) -> list:
    """Field-level old→new from CONSECUTIVE snapshots.

    ⛔ A change is emitted ONLY where two adjacent snapshots differ on that
    exact path — nothing is inferred, and a single snapshot (the state of this
    PC) yields ZERO changes rather than a guess.
    """
    out = []
    for prev, cur in zip(snaps, snaps[1:]):
        try:
            a = _flatten(json.loads(prev["config_json"]))
            b = _flatten(json.loads(cur["config_json"]))
        except (ValueError, TypeError, KeyError):
            continue
        if prev.get("config_hash") == cur.get("config_hash"):
            continue
        paths = sorted(set(a) | set(b))
        idx = 0
        for p in paths:
            if p.startswith(_NOISY_PREFIXES):
                continue
            old, new = a.get(p, "__absent__"), b.get(p, "__absent__")
            if old == new:
                continue
            idx += 1
            cat, mod = _classify_path(p)
            leaf = p.split(".")[-1]
            out.append({
                # Deterministic and stable: the snapshot's real primary key plus
                # the field's ordinal within that diff. ⛔ No counter, no clock,
                # no random component — it resolves to the same row every time.
                "ref_id": "CFG-%06d-%03d" % (int(cur["snapshot_id"]), idx),
                "src_table": "config_snapshots", "src_pk": cur["snapshot_id"],
                "ts": cur["snapshot_ts"], "category": cat, "module": mod,
                "action": "%s changed" % leaf,
                "field": p,
                "old_value": None if old == "__absent__" else _render(old),
                "new_value": None if new == "__absent__" else _render(new),
                # ⛔ WHO edited the file is NOT recorded anywhere.
                "user": None, "user_kind": None,
                # The snapshot proves the value was IN EFFECT at this stamp.
                "status": "Success",
                "source": None,
                "detail": ("configuration observed at startup on %s"
                           % (cur.get("snapshot_date") or "")),
            })
    return out


# ── the six approved KPI + the record set ────────────────────────────────────
_EVENT_MAP = {
    # event_type -> (category, module, action, status)
    "STARTUP": ("System", "Application", "System started", "Success"),
    "SHUTDOWN": ("System", "Application", "System stopped", "Success"),
    "CRASH_DETECTED": ("System", "Application", "Crash detected", "Failed"),
    "CONFIG_DIFF": ("Configuration", "Config Loader",
                    "Configuration change detected", "Success"),
    "KILL_AUTO_CLEARED": ("Control", "Kill Switch",
                          "Kill switch auto-cleared", "Success"),
    "EOD_SKIPPED_LATE": ("Service", "EOD", "EOD skipped — ran late", "Partial"),
    "RECOVERY": ("Service", "Recovery", "Recovery performed", "Success"),
    "KILL_SWITCH": ("Control", "Kill Switch", "Kill switch triggered", "Success"),
}


def _from_system_events(rows: list) -> list:
    out = []
    for r in rows:
        et = (r.get("event_type") or "").upper()
        cat, mod, act, st = _EVENT_MAP.get(
            et, ("System", "Application", et.replace("_", " ").title() or "Event",
                 None))
        if r.get("scenario"):
            act = "%s (%s)" % (act, r["scenario"])
        detail = r.get("details")
        # ⭐ CONFIG_DIFF carries the CHANGED FILE LIST — real evidence, surfaced
        # verbatim. It says WHICH files changed; the field-level old→new comes
        # from the snapshot diff. Two different facts, ⛔ not merged.
        if detail:
            try:
                d = json.loads(detail)
                if isinstance(d, dict) and d.get("changed_files"):
                    detail = "changed files: " + ", ".join(d["changed_files"])
                elif isinstance(d, dict):
                    detail = " · ".join("%s=%s" % (k, v) for k, v in sorted(d.items()))
            except (ValueError, TypeError):
                pass
        out.append({
            "ref_id": "SYSEVT-%06d" % int(r["event_id"]),
            "src_table": "system_events", "src_pk": r["event_id"],
            "ts": r.get("timestamp"), "category": cat, "module": mod,
            "action": act, "status": st,
            # ⭐ The system genuinely performed these. ⛔ CONFIG_DIFF is the
            # exception: the system DETECTED the change, it did not make it, so
            # the actor stays unrecorded rather than being credited to "system".
            "user": None if et == "CONFIG_DIFF" else "system",
            "user_kind": None if et == "CONFIG_DIFF" else "process",
            "source": None, "field": None,
            "old_value": None, "new_value": None,
            "detail": detail or None,
        })
    return out


def _from_kill_switch(row: Optional[dict]) -> list:
    if not row:
        return []
    state = (row.get("state") or "").upper()
    if state == "INACTIVE":
        return []          # ⛔ nothing happened; an INACTIVE row is not an action
    return [{
        # Keyed on the real triggered_at, so a NEW kill produces a NEW id while
        # a refresh of the same state produces the SAME one.
        "ref_id": "KILL-%s" % (str(row.get("triggered_at") or "")[:19]
                               .replace("-", "").replace(":", "").replace("T", "-")),
        "src_table": "kill_switch_state", "src_pk": row.get("id"),
        "ts": row.get("triggered_at"), "category": "Control",
        "module": "Kill Switch", "action": "Kill switch %s" % state,
        "status": "Success",
        "user": row.get("triggered_by") or None,
        "user_kind": "process" if row.get("triggered_by") else None,
        "source": None, "field": None, "old_value": None, "new_value": None,
        "detail": row.get("reason"),
    }]


def _from_reconciliation(rows: list) -> list:
    return [{
        "ref_id": "RECON-%06d" % int(r["id"]),
        "src_table": "reconciliation_log", "src_pk": r["id"],
        "ts": r.get("ts"), "category": "Service", "module": "Reconciler",
        "action": "%s — %s" % (r.get("check_name"), r.get("action_taken")),
        "status": "Success" if r.get("success") else "Failed",
        "user": "system", "user_kind": "process", "source": None,
        "field": None, "old_value": None, "new_value": None,
        "detail": "%s · tier %s" % (r.get("symbol") or "", r.get("tier") or ""),
    } for r in rows]


def _from_webhook(rows: list) -> list:
    out = []
    for r in rows:
        code = int(r.get("response_code") or 0)
        out.append({
            "ref_id": "WHK-%06d" % int(r["id"]),
            "src_table": "webhook_audit", "src_pk": r["id"],
            "ts": r.get("ts"), "category": "System", "module": "Webhook",
            "action": "Signal webhook received",
            "status": "Failed" if code >= 400 else "Success",
            "user": None, "user_kind": None,
            # ⭐ THE ONE GENUINE SOURCE VALUE: this row exists BECAUSE an inbound
            # HTTP POST arrived, so its origin IS the API. ⛔ Not an inference.
            "source": "API",
            "field": None, "old_value": None, "new_value": None,
            "detail": "HTTP %d · accepted %s / rejected %s"
                      % (code, r.get("signals_accepted"), r.get("signals_rejected")),
        })
    return out


def _from_control_tower(rows: list) -> list:
    return [{
        "ref_id": "CTF-%06d" % int(r["id"]),
        "src_table": "control_tower_findings", "src_pk": r["id"],
        "ts": r.get("scan_time"), "category": "Service",
        "module": (r.get("resource_type") or "Control Tower").title(),
        "action": "%s finding: %s" % ((r.get("category") or "").title(),
                                      r.get("resource_name") or ""),
        # ⛔ A FINDING IS A DETECTION, NOT AN ACTION WITH AN OUTCOME. Only a
        # RESOLVED one records a result; an OPEN one has no status to report and
        # must ⛔ not be coloured as a failure of something somebody did.
        "status": "Success" if (r.get("status") or "").upper() == "RESOLVED" else None,
        "user": "system", "user_kind": "process", "source": None,
        "field": None, "old_value": None, "new_value": None,
        "detail": r.get("reason"),
    } for r in rows]


def _from_preflight(rows: list) -> list:
    st_map = {"READY": "Success", "READY_WITH_WARNINGS": "Partial",
              "CRITICAL_FAILURE": "Failed"}
    return [{
        "ref_id": "PREFLT-%s" % str(r.get("run_id"))[:24],
        "src_table": "preflight_runs", "src_pk": r.get("run_id"),
        "ts": r.get("completed_at") or r.get("started_at"),
        "category": "System", "module": "Preflight",
        "action": "Preflight phase %s" % (r.get("phase") or "?"),
        "status": st_map.get((r.get("overall_status") or "").upper()),
        "user": "system", "user_kind": "process", "source": None,
        "field": None, "old_value": None, "new_value": None,
        "detail": "%s/%s passed" % (r.get("passed"), r.get("total_checks")),
    } for r in rows]


def _timeline(rec: dict) -> list:
    """The approved four stages.

    ⛔ ONLY `Applied` IS REAL. The system records exactly one instant per audit
    record — the moment the thing took effect. Requested / Validated / Confirmed
    are not instrumented anywhere, and ⛔ a missing stage is never rendered as a
    completed one (Rama: *"do not silently convert missing stages into
    successful stages"*).
    """
    reason = ("no request/validation/confirmation instant is recorded — the "
              "system persists one timestamp per audit record")
    out = []
    for stage in TIMELINE_STAGES:
        if stage == "Applied" and rec.get("ts"):
            out.append({"stage": stage, "ts": rec["ts"], "measured": True,
                        "note": "the moment the change took effect"})
        else:
            out.append({"stage": stage, "ts": None, "measured": False,
                        "note": reason})
    return out


def build_audit(cfg: dict, start: Optional[str] = None, end: Optional[str] = None,
                category: Optional[str] = None, action: Optional[str] = None,
                status: Optional[str] = None, module: Optional[str] = None,
                user: Optional[str] = None, q: Optional[str] = None,
                bucket: str = "7d") -> dict:
    """The whole screen from ONE filtered population, so no panel can disagree
    with the table it sits beside."""
    today = freshness.ist_today_iso()
    end = end or today
    if not start:
        # Default window matches the reference's "Last 7 Days" chart control.
        y, m, d = (int(x) for x in end.split("-"))
        import datetime as _dt
        start = (_dt.date(y, m, d) - _dt.timedelta(days=6)).isoformat()

    records = []
    records += _from_system_events(db_reader.audit_system_events_range(cfg, start, end))
    records += _config_changes(db_reader.audit_config_snapshots(cfg, start, end))
    records += _from_reconciliation(db_reader.audit_reconciliation_range(cfg, start, end))
    records += _from_webhook(db_reader.audit_webhook_range(cfg, start, end))
    records += _from_control_tower(db_reader.audit_control_tower_range(cfg, start, end))
    records += _from_preflight(db_reader.audit_preflight_range(cfg, start, end))

    ks = _from_kill_switch(db_reader.audit_kill_switch(cfg))
    records += [k for k in ks if k["ts"] and start <= str(k["ts"])[:10] <= end]

    for r in records:
        r["date"], r["time"] = _parts(r.get("ts"))

    # newest first; ⭐ ref_id breaks ties so equal timestamps order DETERMINISTICALLY
    # and a refresh can never reshuffle two rows that share a stamp.
    records.sort(key=lambda r: (str(r.get("ts") or ""), str(r.get("ref_id") or "")),
                 reverse=True)

    # ── filter options come from the POPULATION, so a filter can never offer a
    # value that matches nothing, nor hide one that exists.
    options = {
        "category": sorted({r["category"] for r in records if r.get("category")}),
        "action": sorted({r["action"] for r in records if r.get("action")}),
        "status": [s for s in STATUSES
                   if any(r.get("status") == s for r in records)],
        "module": sorted({r["module"] for r in records if r.get("module")}),
        "user": sorted({r["user"] for r in records if r.get("user")}),
    }

    shown = records
    if category:
        shown = [r for r in shown if r.get("category") == category]
    if action:
        shown = [r for r in shown if r.get("action") == action]
    if status:
        shown = [r for r in shown if (r.get("status") or "") == status]
    if module:
        shown = [r for r in shown if r.get("module") == module]
    if user:
        shown = [r for r in shown if (r.get("user") or "") == user]
    if q:
        needle = q.strip().lower()
        if needle:
            def _hit(r):
                return any(needle in str(r.get(k) or "").lower() for k in
                           ("action", "category", "module", "user", "status",
                            "source", "ref_id", "field", "old_value",
                            "new_value", "detail"))
            shown = [r for r in shown if _hit(r)]

    today_rows = [r for r in shown if (r.get("date") or "") == today]
    last = shown[0] if shown else None

    return {
        "today": today,
        "generated_at": freshness.ist_now().isoformat(),
        "range": {"start": start, "end": end},
        "filters": {
            "active": {k: v for k, v in (("category", category), ("action", action),
                                         ("status", status), ("module", module),
                                         ("user", user), ("q", q)) if v},
            "options": options,
        },
        # ── the six approved KPI, in the approved order ──────────────────────
        "kpi": {
            "total_events": len(shown),
            "todays_changes": len(today_rows),
            "configuration_changes": sum(
                1 for r in today_rows if r.get("category") == "Configuration"),
            "control_actions": sum(
                1 for r in today_rows if r.get("category") == "Control"),
            "failed_actions": sum(
                1 for r in today_rows if r.get("status") == "Failed"),
            "last_event": {"ts": last.get("ts") if last else None,
                           "date": last.get("date") if last else None,
                           "time": last.get("time") if last else None,
                           "action": last.get("action") if last else None},
        },
        "kpi_note": ("Total is the FILTERED population; the four 'today' counters "
                     "and Last Audit Event follow the same filter. ⛔ No counter "
                     "is computed over a different population than the table."),
        "records": shown,
        "total_records": len(records),
        "sparklines": _sparklines(shown, start, end),
        "distribution": _distribution(shown),
        "over_time": _over_time(shown, start, end),
        "top_users": _top_users(shown),
        "critical": _critical(shown),
        "retention": _retention(cfg),
        "timeline_stages": list(TIMELINE_STAGES),
        "categories": list(CATEGORIES),
        "statuses": list(STATUSES),
        "sources": list(SOURCES),
        "gaps": {
            "user": _gap("who performed an action is NOT CAPTURED: auth.py "
                         "persists nothing, so there is no login/logout history "
                         "and no human attribution anywhere in the schema"),
            "authentication": _gap("the Authentication category has NO source — "
                                   "GUI login/logout is never written to the DB"),
            "source": _gap("Dashboard/System/Scheduler/Recovery Engine origins "
                           "are not recorded; only an inbound webhook proves its "
                           "own source (API)"),
            "timeline": _gap("only one instant per record exists — Requested, "
                             "Validated and Confirmed are not instrumented"),
            "retention_period": _gap("no audit retention policy exists; "
                                     "signal_retention_days governs SIGNAL "
                                     "fingerprints, not audit records"),
            "control_history": _gap("kill_switch_state is a single row "
                                    "(CHECK (id=1)) — it holds the CURRENT state, "
                                    "so control actions have no history"),
        },
        "note": ("Timestamps are IST. Configuration old→new values are derived "
                 "by diffing consecutive config snapshots; a snapshot is written "
                 "at STARTUP, so its stamp is when the system OBSERVED the "
                 "configuration, not when the file was edited."),
    }


def _sparklines(rows: list, start: str, end: str) -> dict:
    """Per-KPI daily series for the approved card sparklines. Real counts only."""
    import datetime as _dt
    y0, m0, d0 = (int(x) for x in start.split("-"))
    y1, m1, d1 = (int(x) for x in end.split("-"))
    days, cur, last = [], _dt.date(y0, m0, d0), _dt.date(y1, m1, d1)
    while cur <= last and len(days) < 120:
        days.append(cur.isoformat())
        cur += _dt.timedelta(days=1)

    def series(pred):
        by = {d: 0 for d in days}
        for r in rows:
            d = r.get("date")
            if d in by and pred(r):
                by[d] += 1
        return [by[d] for d in days]

    return {
        "days": days,
        "total_events": series(lambda r: True),
        "todays_changes": series(lambda r: True),
        "configuration_changes": series(lambda r: r.get("category") == "Configuration"),
        "control_actions": series(lambda r: r.get("category") == "Control"),
        "failed_actions": series(lambda r: r.get("status") == "Failed"),
    }


def _distribution(rows: list) -> dict:
    """Audit Category Distribution — the approved donut.

    ⭐ Counts RECONCILE to the filtered population by construction: every record
    has exactly one category and the slices are built from that same list.
    """
    counts = {}
    for r in rows:
        counts[r.get("category") or "Unknown"] = counts.get(r.get("category") or "Unknown", 0) + 1
    total = sum(counts.values())
    slices = [{"category": c, "count": n,
               "pct": round(100.0 * n / total, 1) if total else 0.0}
              for c, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"slices": slices, "total": total,
            "reconciles": sum(s["count"] for s in slices) == total}


def _over_time(rows: list, start: str, end: str) -> dict:
    """Audit Events Over Time — the approved line chart.

    ⭐ EVERY day in the range is emitted, so a quiet day is a REAL zero and the
    line cannot join two non-adjacent days as though they were consecutive.
    """
    import datetime as _dt
    y0, m0, d0 = (int(x) for x in start.split("-"))
    y1, m1, d1 = (int(x) for x in end.split("-"))
    by, cur, last = {}, _dt.date(y0, m0, d0), _dt.date(y1, m1, d1)
    while cur <= last and len(by) < 400:
        by[cur.isoformat()] = 0
        cur += _dt.timedelta(days=1)
    outside = 0
    for r in rows:
        d = r.get("date")
        if d in by:
            by[d] += 1
        else:
            outside += 1
    pts = [{"day": d, "count": n} for d, n in sorted(by.items())]
    return {"points": pts, "total": sum(p["count"] for p in pts),
            "outside_range": outside,
            "conserved": sum(p["count"] for p in pts) + outside == len(rows)}


def _top_users(rows: list) -> dict:
    """Top Users (by Changes) — the approved panel.

    ⚠️ EVERY actor this system records is a PROCESS, not a person: `system`,
    `order_monitor`. The panel keeps its approved layout and says so, rather
    than presenting machine actors as people or inventing human names.
    Records with no actor are counted separately, ⛔ never assigned to anybody.
    """
    counts, unattributed = {}, 0
    for r in rows:
        u = r.get("user")
        if u:
            counts[u] = counts.get(u, 0) + 1
        else:
            unattributed += 1
    total = sum(counts.values())
    users = [{"user": u, "kind": "process", "changes": n,
              "pct": round(100.0 * n / total, 2) if total else 0.0}
             for u, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"users": users, "attributed": total, "unattributed": unattributed,
            "total": total,
            "note": ("all recorded actors are PROCESSES, not people — human "
                     "attribution is not instrumented"),
            "unattributed_note": ("%d record(s) carry no actor at all and are "
                                  "excluded from the ranking rather than "
                                  "assigned to anyone" % unattributed)}


_CRITICAL_CATEGORIES = ("Risk", "Capital", "Control")


def _critical(rows: list, limit: int = 8) -> list:
    """Critical Changes — the approved pinned section.

    ⭐ DERIVED FROM REAL RECORDS, ⛔ not a hard-coded example list: a change is
    critical when it touched Risk, Capital or Control, or when it FAILED.
    """
    out = [r for r in rows
           if r.get("category") in _CRITICAL_CATEGORIES or r.get("status") == "Failed"]
    return [{"ref_id": r["ref_id"], "ts": r.get("ts"), "time": r.get("time"),
             "date": r.get("date"), "category": r.get("category"),
             "action": r.get("action"), "status": r.get("status"),
             "old_value": r.get("old_value"), "new_value": r.get("new_value"),
             "field": r.get("field"), "detail": r.get("detail")}
            for r in out[:limit]]


def _retention(cfg: dict) -> dict:
    """Retention Overview — real store metadata.

    ⛔ RETENTION PERIOD IS A DECLARED GAP. No audit retention policy exists in
    this system; `signal_retention_days: 90` governs SIGNAL fingerprints and
    showing it here would attribute a policy to audit that nobody wrote.
    ⭐ The span between oldest and newest IS real and is labelled as a SPAN,
    ⛔ never as a policy.
    """
    meta = db_reader.audit_retention_meta(cfg)
    span = None
    if meta.get("oldest") and meta.get("newest"):
        try:
            import datetime as _dt
            a = _dt.date(*(int(x) for x in str(meta["oldest"])[:10].split("-")))
            b = _dt.date(*(int(x) for x in str(meta["newest"])[:10].split("-")))
            span = (b - a).days
        except (ValueError, TypeError):
            span = None
    return {
        "records": meta.get("records", 0),
        "oldest": meta.get("oldest"),
        "newest": meta.get("newest"),
        "span_days": span,
        "span_note": ("observed span between the oldest and newest audit record "
                      "— a MEASUREMENT, not a retention policy"),
        "retention_period": _gap("no audit retention policy exists in this "
                                 "system; signal_retention_days governs SIGNAL "
                                 "fingerprints, not audit records"),
        "per_source": meta.get("per_source", {}),
        "scope_note": ("the whole audit store, ⛔ not the current filter — a "
                       "retention figure that shrank with a date filter would "
                       "not be a retention figure"),
    }


def record_detail(payload: dict, ref_id: str) -> Optional[dict]:
    """The approved Audit Details panel for one record, with its timeline."""
    for r in payload.get("records", []):
        if r.get("ref_id") == ref_id:
            return dict(r, timeline=_timeline(r))
    return None


# ── export ───────────────────────────────────────────────────────────────────
EXPORT_COLS = [
    ("Date", "date"), ("Time", "time"), ("Category", "category"),
    ("Module", "module"), ("Action", "action"), ("User", "user"),
    ("Status", "status"), ("Source", "source"), ("Reference ID", "ref_id"),
    ("Field", "field"), ("Old Value", "old_value"), ("New Value", "new_value"),
    ("Detail", "detail"),
]


def export_sheets(payload: dict) -> list:
    """[(sheet, header, rows)] from the SAME filtered payload the screen was
    served, so an exported row can never disagree with the row on screen.

    ⛔ An unrecorded field exports as NOT INSTRUMENTED, never as a blank — a
    blank spreadsheet cell reads as zero or as 'none happened'.
    """
    def _c(v):
        return "NOT INSTRUMENTED" if v is None else v

    rows = payload.get("records", [])
    dist, ot = payload["distribution"], payload["over_time"]
    tu, ret = payload["top_users"], payload["retention"]
    k = payload["kpi"]
    summary = [
        ("Range", "%s .. %s" % (payload["range"]["start"], payload["range"]["end"])),
        ("Total Audit Events", k["total_events"]),
        ("Today's Changes", k["todays_changes"]),
        ("Configuration Changes", k["configuration_changes"]),
        ("Control Actions", k["control_actions"]),
        ("Failed Actions", k["failed_actions"]),
        ("Last Audit Event", k["last_event"]["ts"] or "NONE"),
        ("", ""),
        ("Audit Records (store)", ret["records"]),
        ("Oldest Record", ret["oldest"] or "NONE"),
        ("Newest Record", ret["newest"] or "NONE"),
        ("Observed Span (days)", ret["span_days"] if ret["span_days"] is not None
         else "NOT INSTRUMENTED"),
        ("Retention Period", "NOT INSTRUMENTED"),
        ("Retention Period why", ret["retention_period"]["reason"]),
        ("", ""),
        ("Attributed actors", tu["attributed"]),
        ("Unattributed records", tu["unattributed"]),
        ("Actor note", tu["note"]),
        ("", ""),
        ("INSTRUMENTATION GAPS", "; ".join(
            "%s: %s" % (k2, v["reason"]) for k2, v in payload["gaps"].items())),
    ]
    return [
        ("Audit Events", [h for h, _k in EXPORT_COLS],
         [[_c(r.get(key)) for _h, key in EXPORT_COLS] for r in rows]),
        ("Category Distribution", ["Category", "Count", "% of Total"],
         [[s["category"], s["count"], s["pct"]] for s in dist["slices"]]),
        ("Events Over Time", ["Day", "Events"],
         [[p["day"], p["count"]] for p in ot["points"]]),
        ("Top Actors", ["Actor", "Kind", "Changes", "% of Total"],
         [[u["user"], u["kind"], u["changes"], u["pct"]] for u in tu["users"]]),
        ("Critical Changes", ["Date", "Time", "Category", "Action", "Status",
                              "Field", "Old Value", "New Value"],
         [[c["date"], c["time"], c["category"], c["action"], _c(c["status"]),
           _c(c["field"]), _c(c["old_value"]), _c(c["new_value"])]
          for c in payload["critical"]]),
        ("Retention", ["Source", "Records", "Oldest", "Newest"],
         [[s, v["records"], v["oldest"] or "NONE", v["newest"] or "NONE"]
          for s, v in sorted(ret["per_source"].items())]),
        ("Summary", ["Measure", "Value"], [list(x) for x in summary]),
    ]
