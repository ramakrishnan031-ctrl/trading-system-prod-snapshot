"""
ops_dashboard/backend/services/system_health.py

SCREEN 12 — SYSTEM HEALTH.  Read-only over the existing host/db/metrics readers;
NO schema, NO new probe, NO write path.

═══════════════════════════════════════════════════════════════════════════════
WHAT IS REAL, AND WHAT IS NOT INSTRUMENTED
═══════════════════════════════════════════════════════════════════════════════
The reference design (`gui/12. System_Health.txt` + PNG) lists metrics this
system does not all collect. Following the Screen-11 precedent, every unmeasured
metric is emitted with `measured: false` and a reason and rendered as an
explicit gap — ⛔ never as 0, and ⛔ never as green.

  MEASURED
  ✅ systemd unit state / started-at / uptime / restarts  `systemctl show`
  ✅ trading-engine liveness + per-component checks       :8080/health
  ✅ trading-engine uptime                                health.uptime_seconds
  ✅ health-endpoint response time                        measured round trip
  ✅ database reachable / size / query latency / schema   file + timed query
  ✅ last successful order                                orders.filled_at
  ✅ last DB backup                                       data_store/backups/
  ✅ disk %                                               shutil (all platforms)
  ✅ RAM available + system load                          /proc + getloadavg (LINUX ONLY)
  ✅ readiness = LIVE required-service state × preflight   services × preflight_runs
  ✅ preflight per-group checks (HISTORICAL)              preflight_runs / _check_results
  ✅ disk % THROUGH DAY                                   analytics.system_metrics
  ✅ auto-recovery lifecycle                              preflight_autofix_log
  ✅ service events                                       system_events
  ✅ alerts                                               telegram_alerts · control_tower · sentinels

  ⛔ NOT INSTRUMENTED — no value is shown
  ⛔ CPU %          `system_metrics.cpu_pct` is a -1.0 SENTINEL. Source states it
                    outright: scripts/capture_metrics_baseline.py:121-126 —
                    "psutil is absent from the VM venv … [cpu/memory] still need
                    psutil and stay -1.0 — separate ticket".
  ⛔ RAM % history  same sentinel (`memory_mb`). The LIVE figure exists on Linux
                    as MemAvailable KB, but /proc/meminfo's MemTotal is not read,
                    so a PERCENTAGE cannot be formed without inventing a
                    denominator.
  ⛔ Network        no bandwidth/throughput metric exists anywhere in the repo.
  ⛔ Response-time history  only the CURRENT round trip is measured; nothing
                    persists it, so "Response Time Through Day" has no series.
  ⛔ DB connection count    SQLite is embedded — there is no server and no pool.

⛔⛔ THE RULE THIS MODULE EXISTS TO ENFORCE: a health screen that shows green for
something it did not measure is worse than no health screen. `UNKNOWN` is a
first-class state here and is coloured GREY, ⛔ never green.

STATUS VOCABULARY (the approved four):
  HEALTHY green · WARNING yellow · FAILED red · UNKNOWN grey
"""
from __future__ import annotations

import os
from typing import Optional

from ..readers import db_reader, host_reader, metrics_client
from . import freshness

HEALTHY, WARNING, FAILED, UNKNOWN = "HEALTHY", "WARNING", "FAILED", "UNKNOWN"
_STATUSES = (HEALTHY, WARNING, FAILED, UNKNOWN)

# Rank for "worst wins" rollups. ⛔ UNKNOWN outranks HEALTHY: not knowing is not
# the same as being well, and an overall HEALTHY built over an unknown component
# is exactly the false all-clear this screen must never give.
_RANK = {HEALTHY: 0, UNKNOWN: 1, WARNING: 2, FAILED: 3}

# systemd ActiveState → our four. `activating` is WARNING, ⛔ not FAILED: a unit
# with RestartSec is legitimately activating between beats (security-watcher's
# designed heartbeat — see the standing note that it is NOT broken).
_UNIT_STATUS = {
    "active": HEALTHY, "activating": WARNING, "reloading": WARNING,
    "deactivating": WARNING, "inactive": FAILED, "failed": FAILED,
    "unavailable": UNKNOWN, "unknown": UNKNOWN,
}

# Response-time bands in ms, applied ONLY where a round trip is actually
# measured. ⛔ Never applied to a systemd unit: `systemctl` latency is the
# dashboard's own subprocess cost, ⛔ not the service's responsiveness.
_RESP_WARN_MS, _RESP_FAIL_MS = 500.0, 2000.0

# The five readiness pillars the approved design names, mapped onto what
# preflight ACTUALLY produces. ⚠️ `check_group` covers Broker/Database/Engine/
# State directly; Capital has NO group of its own, so it is assembled from the
# named capital checks — recorded here rather than silently folded into Engine.
_PILLARS = (
    {"pillar": "Broker", "groups": ("Broker",), "checks": ()},
    {"pillar": "Database", "groups": ("Database",), "checks": ()},
    {"pillar": "Core Services", "groups": ("Services", "Engine"), "checks": (),
     "note": "the Services group declares ZERO checks in the current build; "
             "Core Services therefore rests on the Engine group"},
    {"pillar": "Capital", "groups": (), "checks": (
        "fund_manager_balance", "capital_deployment", "kite_funds_available"),
     "note": "no Capital group exists in preflight — assembled from the three "
             "named capital checks, which live under Engine and Broker"},
    {"pillar": "Risk", "groups": ("State",), "checks": (),
     "note": "the State group IS the risk-state gate: kill switch, open "
             "positions/orders at start, stuck exits, tgt-retry residue"},
)

# preflight check status → our four.
_CHECK_STATUS = {"PASS": HEALTHY, "AUTOFIXED": WARNING, "WARN": WARNING,
                 "FAIL": FAILED, "SKIPPED": UNKNOWN}

# ─────────────────────────────────────────────────────────────────────────────
# WHICH SERVICES GATE TRADING SAFETY *RIGHT NOW*
#
# ⛔ This is NOT a new safety policy invented in the GUI. It names the same
# components preflight itself gates on — its Broker / Database / Engine pillars
# — plus the systemd unit that IS the trader. Everything else (the watchers, the
# cron timer, this dashboard) can be degraded without making an OPEN position
# unsafe: they colour Overall Status but ⛔ do not force NOT READY.
#
# ⚠️ `security-watcher.service` is DELIBERATELY ABSENT. `activating/auto-restart`
# is its DESIGNED `RestartSec=60` heartbeat, so requiring it would fire a
# permanent false NOT READY — the exact "do not 'fix' the watcher" trap.
#
# ⚠️ `token-watcher.service` is absent for a different reason: it gates the NEXT
# 08:15 boot, ⛔ not the safety of a position open right now. Its failure is a
# real WARNING on the service table; it is not a live trading blocker.
_REQUIRED_UNITS = ("trading-system.service",)
_REQUIRED_KINDS = ("engine", "engine-component", "database")


def _is_required(row: dict) -> bool:
    """Does this service gate trading safety RIGHT NOW?

    ⭐ Broker connectivity is covered ⛔ not by a unit row but by preflight's
    Broker pillar and by the engine's own `/health` component checks, both of
    which already feed the verdict — so there is no separate broker row to flag.
    """
    return (row.get("service") in _REQUIRED_UNITS
            or row.get("kind") in _REQUIRED_KINDS)


def _worst(statuses) -> str:
    """Worst-wins rollup. Empty ⇒ UNKNOWN, ⛔ never HEALTHY."""
    s = [x for x in statuses if x in _RANK]
    return max(s, key=lambda x: _RANK[x]) if s else UNKNOWN


def _fmt_uptime(sec: Optional[float]) -> Optional[str]:
    """"7d 14h 32m" — the reference's shape. None stays None."""
    if sec is None:
        return None
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, m = divmod(rem, 3600)[0], (rem % 3600) // 60
    if d:
        return "%dd %02dh %02dm" % (d, h, m)
    if h:
        return "%dh %02dm" % (h, m)
    return "%dm" % m


def _gap(reason: str) -> dict:
    """NOT INSTRUMENTED — the system never records this. ⛔ No value, ⛔ no status
    that could read green. More waiting will never fill it in."""
    return {"measured": False, "instrumented": False, "value": None,
            "status": UNKNOWN, "reason": reason}


def _nodata(reason: str) -> dict:
    """NO DATA — the metric IS real and IS collected; nothing has been recorded
    here yet (an empty table, or a host where the source is unavailable).

    ⛔ NOT the same as `_gap`, and the difference is not cosmetic: labelling a
    metric the system genuinely measures as "NOT INSTRUMENTED" UNDER-reports the
    system, exactly as charting a -1.0 sentinel would over-report it. The reason
    string carries the specifics.
    """
    return {"measured": False, "instrumented": True, "value": None,
            "status": UNKNOWN, "reason": reason}


def _val(value, status=HEALTHY, unit=None) -> dict:
    return {"measured": True, "instrumented": True, "value": value,
            "status": status, "unit": unit}


def _band(value, warn: float, fail: float) -> str:
    """Threshold band for a live utilisation figure. ⛔ None ⇒ UNKNOWN, never a
    green pass — an unread metric is not a healthy one."""
    if value is None:
        return UNKNOWN
    return FAILED if value >= fail else (WARNING if value >= warn else HEALTHY)


def _fmt_rate(bps: Optional[int]) -> Optional[str]:
    """Bytes/sec → a human rate. ⛔ Always carries the /s, so a THROUGHPUT can
    never be misread as a cumulative total."""
    if bps is None:
        return None
    for unit, div in (("GB/s", 2**30), ("MB/s", 2**20), ("KB/s", 2**10)):
        if bps >= div:
            return "%.1f %s" % (bps / div, unit)
    return "%d B/s" % bps


# ─────────────────────────────────────────────────────────────────────────────
# Services
# ─────────────────────────────────────────────────────────────────────────────
def _unit_rows(cfg: dict) -> list:
    out = []
    for u in host_reader.all_unit_details(cfg):
        st = _UNIT_STATUS.get((u.get("state") or "").lower(), UNKNOWN)
        # A unit that has restarted is still HEALTHY if it is active now — but
        # the restart count travels so an operator can see a flapping unit.
        out.append({
            "service": u["unit"], "kind": "systemd", "status": st,
            "raw_state": u.get("state"), "sub_state": u.get("sub_state"),
            "started_at": u.get("started_at"),
            "uptime_sec": u.get("uptime_sec"),
            "uptime": _fmt_uptime(u.get("uptime_sec")),
            "restarts": u.get("restarts"),
            "response_ms": None,
            "response_note": ("systemd state is a local query — there is no "
                              "request to time, so response time is N/A"),
            "last_heartbeat": None,
        })
    return out


def _engine_rows(trader: dict) -> list:
    """The trading engine and its INTERNAL components.

    ⭐ These are the reference's "Signal Engine / Risk Engine / …" done honestly:
    the engines are THREADS INSIDE `trading-system.service`, ⛔ not separate
    services, and :8080/health already publishes their liveness as
    `checks{name:{ok}}`. Listing the real component names is faithful; inventing
    four service rows to match the reference's example list would not be.
    """
    health = (trader or {}).get("health") or {}
    alive = bool((trader or {}).get("trader_alive"))
    resp = (trader or {}).get("response_ms")
    ts = health.get("timestamp")

    def _resp_status(ms):
        if ms is None:
            return UNKNOWN
        if ms >= _RESP_FAIL_MS:
            return FAILED
        return WARNING if ms >= _RESP_WARN_MS else HEALTHY

    if not alive:
        return [{
            "service": "Trading Engine", "kind": "engine", "status": FAILED,
            "raw_state": "unreachable", "sub_state": None, "started_at": None,
            "uptime_sec": None, "uptime": None, "restarts": None,
            "response_ms": resp, "last_heartbeat": None,
            "response_note": "health endpoint did not answer",
        }]

    overall = health.get("status")
    top = HEALTHY if overall == "healthy" else (
        WARNING if overall == "degraded" else UNKNOWN)
    up = health.get("uptime_seconds")
    rows = [{
        "service": "Trading Engine", "kind": "engine",
        # worst of (reported status, response-time band) — a healthy-but-slow
        # engine must not read as fully healthy.
        "status": _worst([top, _resp_status(resp)]),
        "raw_state": overall, "sub_state": None, "started_at": None,
        "uptime_sec": up, "uptime": _fmt_uptime(up), "restarts": None,
        "response_ms": resp, "last_heartbeat": ts, "response_note": None,
    }]
    for name, sub in sorted((health.get("checks") or {}).items()):
        ok = bool(sub.get("ok")) if isinstance(sub, dict) else bool(sub)
        rows.append({
            "service": name, "kind": "engine-component",
            "status": HEALTHY if ok else FAILED,
            "raw_state": "ok" if ok else "not ok", "sub_state": None,
            "started_at": None, "uptime_sec": None, "uptime": None,
            "restarts": None, "response_ms": None, "last_heartbeat": ts,
            "response_note": "component liveness is a boolean from /health — "
                             "no per-component latency is published",
            "detail": (sub.get("error") if isinstance(sub, dict) else None),
        })
    return rows


def _db_row(dbh: dict) -> dict:
    if not dbh.get("reachable"):
        st = FAILED
    elif dbh.get("query_ms") is not None and dbh["query_ms"] >= _RESP_FAIL_MS:
        st = FAILED
    elif dbh.get("query_ms") is not None and dbh["query_ms"] >= _RESP_WARN_MS:
        st = WARNING
    else:
        st = HEALTHY
    return {"service": "Database", "kind": "database", "status": st,
            "raw_state": "reachable" if dbh.get("reachable") else "unreachable",
            "sub_state": None, "started_at": None, "uptime_sec": None,
            "uptime": None, "restarts": None,
            "response_ms": dbh.get("query_ms"), "last_heartbeat": None,
            "response_note": "measured round trip of a real query"}


def _dashboard_row(resp_ms: float) -> dict:
    """The dashboard itself. ⭐ Trivially HEALTHY — you are reading its output —
    and it is listed because the reference does, ⛔ not because it is a probe."""
    return {"service": "Dashboard API", "kind": "dashboard", "status": HEALTHY,
            "raw_state": "serving this request", "sub_state": None,
            "started_at": None, "uptime_sec": None, "uptime": None,
            "restarts": None, "response_ms": resp_ms, "last_heartbeat": None,
            "response_note": "time spent building this payload"}


# ─────────────────────────────────────────────────────────────────────────────
# VM / DB / broker / dependencies
# ─────────────────────────────────────────────────────────────────────────────
def _vm_health(cfg: dict) -> dict:
    vm = host_reader.vm_stats(cfg)
    disk = vm.get("disk_data") or vm.get("disk_root")
    load = (vm.get("loadavg") or [None])[0]
    mem_kb = vm.get("mem_available_kb")

    disk_pct = disk.get("used_pct") if disk else None
    disk_status = UNKNOWN
    if disk_pct is not None:
        disk_status = FAILED if disk_pct >= 90 else (
            WARNING if disk_pct >= 80 else HEALTHY)

    return {
        "platform_supported": bool(vm.get("available")),
        # ⭐⭐ THE PRODUCTION PATH. CPU %, RAM % and Network are read LIVE from
        # the kernel's own interfaces (/proc/stat · /proc/meminfo · /proc/net/dev)
        # — stdlib only, ⛔ no psutil, ⛔ no agent, ⛔ no third-party plugin. On the
        # real Linux VM these populate automatically on the first page load and
        # need ⛔ NO GUI change; off Linux the source does not exist, so each
        # falls back to the SAME honest gap it showed before.
        "cpu_pct": (_val(vm["cpu_pct"], _band(vm["cpu_pct"], 80, 95), "%")
                    if vm.get("cpu_pct") is not None
                    else _nodata("CPU utilisation is read live from /proc/stat, "
                                 "which exists only on Linux — unavailable on "
                                 "this host")),
        "ram_pct": (_val(vm["mem_used_pct"], _band(vm["mem_used_pct"], 85, 95), "%")
                    if vm.get("mem_used_pct") is not None
                    else _nodata("RAM % is computed live from /proc/meminfo "
                                 "MemTotal/MemAvailable — Linux-only, "
                                 "unavailable on this host")),
        # ⭐ RAM-available and load ARE instrumented — they are read straight from
        # /proc and getloadavg on the Linux VM this system runs on. Absent here
        # only because this host is Windows, so they are NO DATA, ⛔ never
        # "NOT INSTRUMENTED": the production system genuinely reports both.
        "ram_available_mb": (_val(round(mem_kb / 1024.0, 1), HEALTHY, "MB")
                             if mem_kb is not None
                             else _nodata("/proc/meminfo is Linux-only — read on "
                                          "the VM, unavailable on this host")),
        "disk_pct": (_val(disk_pct, disk_status, "%") if disk_pct is not None
                     else _nodata("disk usage could not be read on this host")),
        "disk_detail": disk,
        # ⭐ Throughput across real interfaces (⛔ loopback excluded). It is a
        # RATE differenced from /proc/net/dev's cumulative counters, ⛔ never a
        # cumulative total presented as a speed.
        "network": (_val(_fmt_rate(vm["net_bytes_per_sec"]), HEALTHY)
                    if vm.get("net_bytes_per_sec") is not None
                    else _nodata("network throughput is differenced live from "
                                 "/proc/net/dev — Linux-only, unavailable on "
                                 "this host")),
        "system_load": (_val(round(load, 2), HEALTHY) if load is not None
                        else _nodata("os.getloadavg() is Linux-only — read on the "
                                     "VM, unavailable on this host")),
    }


def _broker_health(cfg: dict, pf: Optional[dict]) -> dict:
    """Broker state read from the PREFLIGHT BROKER CHECKS, which are the system's
    own authority on whether the broker path works.

    ⛔ "Connected" is deliberately NOT synthesised from the presence of a token
    file: the reference asks for Broker / Login / Token as SEPARATE facts, and
    preflight measures them separately (`kite_token_file_exists` ·
    `kite_token_fresh_today` · `kite_profile_call_ok`). Collapsing them would
    hide the case this screen exists to catch — a token that EXISTS but is
    STALE, which presents as a silent no-trade morning.
    """
    by = {c["check_name"]: c for c in ((pf or {}).get("checks") or [])}

    def _st(name):
        c = by.get(name)
        return _CHECK_STATUS.get((c or {}).get("status"), UNKNOWN) if c else UNKNOWN

    token_file, token_fresh = _st("kite_token_file_exists"), _st("kite_token_fresh_today")
    profile = _st("kite_profile_call_ok")
    order = db_reader.last_successful_order(cfg)
    return {
        "broker_status": _worst([profile, _st("kite_orders_endpoint")]),
        "login_status": profile,
        "token_status": _worst([token_file, token_fresh]),
        "token_detail": {"file_exists": token_file, "fresh_today": token_fresh},
        "margin_call": _st("kite_margin_call_ok"),
        "funds_available": _st("kite_funds_available"),
        "last_api_call": _gap("no per-call broker API timestamp is recorded; the "
                              "closest real evidence is the last successful order"),
        # ⭐ INSTRUMENTED — `orders.filled_at` is written on every fill. An empty
        # result is NO DATA (nothing has filled), ⛔ not an instrumentation gap.
        "last_successful_order": (
            _val(order.get("filled_at"), HEALTHY) if order
            else _nodata("no order has ever filled in this database — the fill "
                         "timestamp itself IS recorded (orders.filled_at)")),
        "checked_at": (pf or {}).get("completed_at") or (pf or {}).get("started_at"),
    }


def _dependencies(cfg: dict, pf: Optional[dict], units: list, dbh: dict,
                  today: str) -> list:
    """The five approved dependencies, each from a REAL signal.

    ⛔ A dependency is never HEALTHY merely because it is configured. Where no
    probe exists the state is UNKNOWN with the reason attached.
    """
    by = {c["check_name"]: c for c in ((pf or {}).get("checks") or [])}
    unit_by = {u["service"]: u for u in units}

    def _chk(name):
        c = by.get(name)
        return _CHECK_STATUS.get((c or {}).get("status"), UNKNOWN) if c else UNKNOWN

    webhook = _worst([_chk("webhook_responsive"), _chk("signals_arrived")])
    tail = unit_by.get("tailscale.service") or unit_by.get("tailscaled.service")
    return [
        {"name": "Chartink", "status": webhook,
         "source": "preflight webhook_responsive + signals_arrived — the inbound "
                   "signal path IS the Chartink dependency",
         "measured": webhook != UNKNOWN},
        {"name": "Broker API", "status": _chk("kite_profile_call_ok"),
         "source": "preflight kite_profile_call_ok (a real API round trip)",
         "measured": _chk("kite_profile_call_ok") != UNKNOWN},
        {"name": "Database", "status": HEALTHY if dbh.get("reachable") else FAILED,
         "source": "a real query executed while building this payload",
         "measured": True},
        {"name": "Tailscale",
         "status": tail["status"] if tail else UNKNOWN,
         "source": ("systemd unit state" if tail else
                    "no tailscale unit is configured in gui_config.units, and no "
                    "other tailscale probe exists — ⛔ not assumed healthy"),
         "measured": bool(tail)},
        {"name": "Internet", "status": UNKNOWN,
         "source": "no internet-reachability probe exists in this system. The "
                   "broker API check is the closest real signal and is listed "
                   "separately — ⛔ it is not reused here as a proxy",
         "measured": False},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Readiness · recovery · events · alerts
# ─────────────────────────────────────────────────────────────────────────────
def _preflight_verdict(pf: Optional[dict]) -> dict:
    """GATE ① — the morning's go/no-go, read from the SYSTEM'S OWN preflight run.

    ⚠️⚠️ THIS IS HISTORY, ⛔ NOT THE CURRENT ANSWER. It is a point-in-time
    judgement stamped 08:30 / 09:14; a service can die at 11:00. The screen's
    authoritative verdict is `_live_readiness()` below, which uses this as ONE of
    its two gates. ⛔ Do not render this object as the headline readiness state —
    doing exactly that is the defect Rama rejected on 15-Aug.

    ⛔ NOT a copy of Overall Status: the reference is explicit that a
    non-critical warning may still permit trading, and preflight already encodes
    that as `READY_WITH_WARNINGS`.
    """
    if not pf:
        return {"ready": None, "verdict": UNKNOWN,
                "reason": "no preflight run has been recorded for today, so "
                          "readiness has not been evaluated — ⛔ absence of a "
                          "verdict is not a READY verdict",
                "corroborated": False, "checks_evaluated": 0,
                "overall_status": None, "phase": None, "evaluated_at": None,
                "pillars": [{"pillar": p["pillar"], "status": UNKNOWN,
                             "checks": [], "failed": [],
                             "note": p.get("note")} for p in _PILLARS],
                "blockers": []}

    checks = pf.get("checks") or []
    pillars, blockers = [], []
    for spec in _PILLARS:
        mine = [c for c in checks
                if (c.get("check_group") in spec["groups"])
                or (c.get("check_name") in spec["checks"])]
        sts = [_CHECK_STATUS.get(c.get("status"), UNKNOWN) for c in mine]
        st = _worst(sts) if mine else UNKNOWN
        failed = [c["check_name"] for c, s in zip(mine, sts) if s == FAILED]
        blockers.extend(
            "%s — %s" % (spec["pillar"], n) for c, n in
            ((c, c["check_name"]) for c in mine)
            if _CHECK_STATUS.get(c.get("status")) == FAILED
            and (c.get("criticality") or "").upper() == "CRITICAL")
        pillars.append({"pillar": spec["pillar"], "status": st,
                        "checks": len(mine), "failed": failed,
                        "note": spec.get("note"),
                        "unevaluated": not mine})

    overall = (pf.get("overall_status") or "").upper()
    ready = overall in ("READY", "READY_WITH_WARNINGS")
    verdict = HEALTHY if overall == "READY" else (
        WARNING if overall == "READY_WITH_WARNINGS" else
        FAILED if overall else UNKNOWN)
    if overall == "":
        ready = None
    reason = {
        "READY": "all preflight checks passed",
        "READY_WITH_WARNINGS": "preflight passed with warnings — no critical "
                               "check failed, so trading is permitted",
        "CRITICAL_FAILURE": "a CRITICAL preflight check failed",
    }.get(overall, "preflight recorded no overall status")

    # ⚠️⚠️ CORROBORATION. A run can report READY while carrying NO check rows —
    # and a verdict that nothing could have contradicted is exactly the vacuous
    # green this project has been bitten by before. The system's own verdict is
    # still reported verbatim (⛔ it is not overridden into NOT READY, which
    # would be inventing a failure), but the badge is degraded to WARNING and
    # the gap is stated, because "READY, evaluated against nothing" and "READY,
    # 49 checks passed" must not look identical to an operator.
    evaluated = sum(p["checks"] for p in pillars)
    corroborated = evaluated > 0
    if ready and not corroborated:
        verdict = WARNING
        reason = ("preflight reported %s but recorded NO check results — the "
                  "verdict could not be corroborated against any pillar"
                  % (pf.get("overall_status") or "?"))
    if blockers:
        reason += " · blocking: " + "; ".join(sorted(set(blockers))[:5])
    return {"ready": ready, "verdict": verdict, "reason": reason,
            # ⛔ READY_WITH_WARNINGS must NOT collapse to a bare "READY" — the
            # warnings are the reason an operator looks at this block at all.
            "label": ("READY (WITH WARNINGS)" if overall == "READY_WITH_WARNINGS"
                      else "READY" if ready
                      else "NOT READY" if ready is False else "NOT EVALUATED"),
            "corroborated": corroborated, "checks_evaluated": evaluated,
            "overall_status": pf.get("overall_status"), "phase": pf.get("phase"),
            "evaluated_at": pf.get("completed_at") or pf.get("started_at"),
            "failed_critical": pf.get("failed_critical"),
            "warnings": pf.get("warnings"), "total_checks": pf.get("total_checks"),
            "pillars": pillars, "blockers": sorted(set(blockers))}


def _live_readiness(pfv: dict, services: list) -> dict:
    """THE AUTHORITATIVE CURRENT VERDICT — computed from the LIVE state.

    ⚠️⚠️ THE DEFECT THIS REPLACES (Rama, 15-Aug, blocking): the first build made
    preflight's MORNING verdict the headline answer and merely printed a warning
    underneath when live state contradicted it. The screen therefore rendered
    **"Trading Readiness: READY" beside "Trading Engine: FAILED"** — it answered
    its own headline question, *"is trading safe right now?"*, with a stale yes.
    His ruling, quoted: *"Make the live readiness verdict authoritative … A stale
    preflight READY result must never remain the main current READY state after a
    required live service has failed."*

    ⭐ I had argued the GUI should not overrule preflight. That was wrong in the
    way that matters: displaying a stale READY as the CURRENT verdict is itself
    the unsafe act, and applying preflight's OWN rule (a required component down
    is unsafe) to live data is ⛔ not a new policy — it is the same policy, read
    against a fresher clock.

    TWO GATES, BOTH MUST PASS — ⛔ neither re-derives the other:
      ① PREFLIGHT — the morning go/no-go. A `CRITICAL_FAILURE` is ⛔ NOT cured by
         services being up now: a missing holiday calendar stays missing.
      ② LIVE — every service that gates trading safety must be up NOW. A morning
         PASS is ⛔ NOT evidence about 11:00.
    ⇒ READY only when BOTH permit. Worst-wins, the same rollup as everywhere else.

    ⛔ UNKNOWN NEVER READS AS READY. If a required service cannot be evaluated
    (no `systemctl`, engine unreachable) the verdict is NOT CONFIRMED — ⛔ not
    READY (an unearned all-clear) and ⛔ not NOT READY (a fabricated incident).
    """
    req = [s for s in services if s.get("required")]
    live = _worst([s["status"] for s in req])          # ⛔ empty ⇒ UNKNOWN
    failed = [s["service"] for s in req if s["status"] == FAILED]
    unknown = [s["service"] for s in req if s["status"] == UNKNOWN]

    pf_ready = pfv.get("ready")                        # True / False / None
    blockers = list(pfv.get("blockers") or [])
    blockers += ["live — %s is FAILED" % s for s in failed]

    if live == FAILED:
        ready, verdict = False, FAILED
        reason = ("%d service(s) that trading depends on are FAILED right now: %s"
                  % (len(failed), ", ".join(failed[:4])))
        if pf_ready:
            reason += (" — preflight judged the system %s at %s, but that verdict "
                       "is older than this failure"
                       % (pfv.get("overall_status") or "READY",
                          pfv.get("evaluated_at") or "an earlier time"))
    elif pf_ready is False:
        ready, verdict = False, FAILED
        reason = ("preflight blocked trading (%s) and no live signal can clear a "
                  "failed critical pre-check" % (pfv.get("overall_status") or "?"))
    elif live == UNKNOWN:
        ready, verdict = None, UNKNOWN
        reason = ("trading readiness could not be confirmed — %d required "
                  "service(s) could not be evaluated: %s"
                  % (len(unknown), ", ".join(unknown[:4]) or "none reported"))
    elif pf_ready is None:
        ready, verdict = None, UNKNOWN
        reason = (pfv.get("reason")
                  or "no preflight verdict exists for today, so readiness is "
                     "unconfirmed — ⛔ absence of a verdict is not a READY verdict")
    else:
        ready = True
        degraded = live == WARNING or pfv.get("verdict") == WARNING
        verdict = WARNING if degraded else HEALTHY
        reason = ("every service trading depends on is up now, and preflight "
                  "permitted trading at %s" % (pfv.get("evaluated_at") or "boot"))
        if degraded:
            reason += " — degraded, but no critical dependency has failed"

    return {
        "ready": ready, "verdict": verdict, "reason": reason,
        "label": ("READY" if ready else
                  "NOT READY" if ready is False else "NOT CONFIRMED"),
        "as_of": "now",
        "live_status": live,
        "failed_required": failed,
        "unknown_required": unknown,
        "blockers": sorted(set(blockers)),
        # ⭐ AUDITABLE: the operator can see WHICH services were allowed to gate
        # the verdict, so the policy is inspectable rather than buried in code.
        "required_services": [{"service": s["service"], "kind": s["kind"],
                               "status": s["status"]} for s in req],
        "basis": ("BOTH gates must permit: ① preflight's morning verdict and "
                  "② the live status of every service trading depends on. ⛔ The "
                  "morning verdict alone cannot make this READY."),
        # Preflight travels as clearly-labelled HISTORY, ⛔ never as the answer.
        "preflight": pfv,
    }


def _recovery(cfg: dict, today: str) -> dict:
    """Auto-recovery lifecycle. ⛔ TRIGGERED is not SUCCESS."""
    rows = db_reader.preflight_autofix_events(cfg, today)
    out = []
    for r in rows:
        res = (r.get("result") or "").upper()
        state = ("SUCCESS" if res == "SUCCESS" else
                 "FAILED" if res == "FAILED" else "TRIGGERED")
        out.append({
            "at": r.get("attempted_at"), "check": r.get("check_name"),
            "action": r.get("fix_action"), "state": state,
            # ⭐ TRIGGERED is WARNING, ⛔ not HEALTHY: an attempt in flight is not
            # a recovery, and colouring it green would report a broken component
            # as fixed.
            "status": {"SUCCESS": HEALTHY, "FAILED": FAILED}.get(state, WARNING),
            "error": r.get("error_msg"),
            "before": r.get("before_state"), "after": r.get("after_state"),
        })
    return {"events": out,
            "triggered": sum(1 for e in out if e["state"] == "TRIGGERED"),
            "success": sum(1 for e in out if e["state"] == "SUCCESS"),
            "failed": sum(1 for e in out if e["state"] == "FAILED"),
            "note": "a TRIGGERED row is an attempt that has not resolved — "
                    "⛔ never counted as a recovery"}


# system_events.event_type → (status, human label). Only the types main.py
# actually writes; an unrecognised type keeps its raw name and UNKNOWN.
_EVENT_MAP = {
    "STARTUP": (HEALTHY, "Service started"),
    "SHUTDOWN": (UNKNOWN, "Service stopped"),
    "CRASH_DETECTED": (FAILED, "Crash detected"),
    "CONFIG_DIFF": (WARNING, "Configuration changed"),
    "KILL_AUTO_CLEARED": (HEALTHY, "Kill switch auto-cleared"),
    "EOD_SKIPPED_LATE": (WARNING, "EOD skipped — ran late"),
}


def _events(cfg: dict, limit: int = 30) -> list:
    out = []
    for e in db_reader.system_events_recent(cfg, limit):
        st, label = _EVENT_MAP.get((e.get("event_type") or "").upper(),
                                   (UNKNOWN, e.get("event_type") or "event"))
        out.append({"at": e.get("timestamp"), "type": e.get("event_type"),
                    "label": label, "status": st,
                    "scenario": e.get("scenario"),
                    "detail": (e.get("details") or "")[:180]})
    return out


# Alert severity → status. ⭐ This is what drives the SEMANTIC TEXT COLOUR on the
# screen: the alert's own words are coloured, ⛔ not just an icon or a badge.
_SEV_STATUS = {"CRITICAL": FAILED, "ERROR": FAILED, "WARNING": WARNING,
               "WARN": WARNING, "INFO": UNKNOWN, "SUCCESS": HEALTHY}

# Titles that describe a RESOLUTION rather than a problem. ⭐ Without this a
# "Connection restored" alert inherits its feed's severity and is painted red —
# the exact defect the approved design calls out: a positive event must read as
# positive even though it lives in an Alerts card.
_POSITIVE_MARKERS = ("restored", "recovered", "recovery success", "success",
                     "resolved", "cleared", "reconnected", "back online",
                     "healthy", "completed successfully")
_NEGATIVE_MARKERS = ("failed", "failure", "down", "lost", "error", "critical",
                     "unreachable", "timeout", "rejected", "halted", "not ready")


def classify_alert(severity: Optional[str], title: str) -> str:
    """Semantic status for one alert — severity FIRST, then the text.

    ⭐ THE TEXT CAN OVERRIDE THE SEVERITY IN ONE DIRECTION ONLY: a message that
    plainly reports a RESOLUTION is promoted to HEALTHY even if it arrived on a
    CRITICAL-severity feed, because that is how recovery notices are emitted.
    ⛔ The reverse is NOT done — a message is never demoted below its declared
    severity on the strength of a word, which would let a real failure be
    painted green by a hopeful title.
    """
    sev = (severity or "").strip().upper()
    base = _SEV_STATUS.get(sev, UNKNOWN)
    t = (title or "").lower()
    if any(m in t for m in _NEGATIVE_MARKERS):
        return base if _RANK[base] >= _RANK[WARNING] else base
    if any(m in t for m in _POSITIVE_MARKERS):
        return HEALTHY
    return base


def _alerts(cfg: dict, today: str, limit: int = 25) -> list:
    out = []
    for a in db_reader.telegram_alerts_today(cfg, today):
        title = a.get("title") or a.get("body") or ""
        out.append({"at": a.get("sent_at"), "severity": a.get("severity"),
                    "title": title, "status": classify_alert(a.get("severity"), title),
                    "source": a.get("source_module"), "kind": "alert"})
    for f in db_reader.control_tower_open_findings(cfg, limit=20):
        title = "%s — %s" % (f.get("resource_name") or f.get("category") or "finding",
                             f.get("reason") or "")
        out.append({"at": f.get("scan_time"), "severity": f.get("severity"),
                    "title": title.strip(" —"),
                    "status": classify_alert(f.get("severity"), title),
                    "source": "control_tower", "kind": "finding"})
    for s in host_reader.list_sentinels(cfg):
        out.append({"at": None, "severity": "CRITICAL",
                    "title": "Critical sentinel flag present: %s" % s["name"],
                    "status": FAILED, "source": "data_store", "kind": "sentinel"})
    return sorted(out, key=lambda x: (x.get("at") or ""), reverse=True)[:limit]


def _trends(cfg: dict) -> dict:
    """Health trends over the day.

    ⛔ THREE DISTINCT REASONS A PANEL CAN BE EMPTY, AND THEY MUST NOT BE MERGED:
      ① NOT INSTRUMENTED — the collector never records a real value. CPU and RAM:
        `system_metrics.cpu_pct`/`memory_mb` are `-1.0` SENTINELS because psutil
        is absent from the VM venv (`capture_metrics_baseline.py:121-126`). More
        history will never help; this needs psutil ON THE VM.
      ② NOT PERSISTED — the value IS measured live but nothing stores a series.
        Response time: every probe is timed, none is written to any table.
      ③ NO DATA ON THIS HOST — instrumented, real, simply not collected here.
        Disk: `disk_used_pct` is genuine (shutil, since 29-Jun) and the VM writes
        ~75 snapshots/day, but a dev PC that never ran the collector has none.

    ⭐ ③ IS NOT A GAP IN THE SYSTEM — saying "NOT INSTRUMENTED" over a metric the
    VM really collects would under-report the system, exactly as charting a
    `-1.0` sentinel would over-report it.
    """
    rows = db_reader.system_metrics_history(cfg, limit=60)

    def _series(col, sentinel_reason, unit, note=None):
        """Build one trend from the STORED column.

        ⭐⭐ THE PRODUCTION BEHAVIOUR RAMA REQUIRED: this reads the real column
        every time. The MOMENT the collector starts persisting real values on
        the VM, `pts` is non-empty and the chart renders — ⛔ with no GUI change,
        no redesign and no second monitoring system. The screen upgrades itself
        because it asks the data, ⛔ rather than hard-coding a verdict about it.
        """
        pts = [{"ts": r.get("timestamp"), "value": r.get(col)}
               for r in rows if r.get(col) is not None and r.get(col) >= 0]
        if pts:
            return {"measured": True, "instrumented": True, "series": pts,
                    "unit": unit, "reason": None, "note": note}
        # ⛔ No real point. Distinguish "the collector wrote sentinels" (proof of
        # a real instrumentation gap) from "no rows at all" (nothing observed) —
        # both currently render NOT INSTRUMENTED because the collector's source
        # says it writes -1.0 without psutil, but the reasons differ.
        seen_sentinel = any(r.get(col) is not None and r.get(col) < 0 for r in rows)
        return {"measured": False, "instrumented": False, "series": [],
                "unit": unit,
                "reason": (sentinel_reason if seen_sentinel or not rows else
                           sentinel_reason + " (no usable sample in the last "
                           "%d snapshots)" % len(rows)),
                "note": note}

    return {
        "cpu": _series("cpu_pct",
                       "system_metrics.cpu_pct is a -1.0 sentinel — psutil is "
                       "absent from the VM venv, so no CPU series is persisted "
                       "(capture_metrics_baseline.py:121-126). ⭐ The LIVE CPU "
                       "figure above does NOT depend on this — it is read from "
                       "/proc/stat — but the HISTORY needs the collector",
                       "%"),
        "ram": _series("memory_mb",
                       "system_metrics.memory_mb is the same -1.0 sentinel; the "
                       "live RAM figure above is read from /proc/meminfo and is "
                       "unaffected", "MB"),
        "response_time": {"measured": False, "instrumented": False, "series": [],
                          "reason": "the health-endpoint round trip is measured "
                                    "live but never persisted, so there is no "
                                    "series to plot"},
        # ⭐ `instrumented` stays True even with no rows: disk IS collected, so
        # an empty dev-PC chart must not be misread as a hole in the system.
        "disk": dict(_series("disk_used_pct", "", "%",
                             "disk is the host metric with a real persisted "
                             "history — shown because it exists, ⛔ not as a "
                             "stand-in for CPU"),
                     instrumented=True,
                     reason=(None if any(
                         r.get("disk_used_pct") is not None
                         and r["disk_used_pct"] >= 0 for r in rows)
                         else "disk usage IS collected (real, shutil-based) but "
                              "this host has recorded no snapshots — the "
                              "5-minute collector runs on the VM")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# The screen
# ─────────────────────────────────────────────────────────────────────────────
def build_system_health(cfg, status_filter=None, kind_filter=None) -> dict:
    """Screen 12. A live operational snapshot — ⛔ not a historical query."""
    import time

    today = freshness.ist_today_iso()

    # ⚠️ The EXTERNAL probes are timed OUTSIDE the dashboard's own clock. The
    # first version started the timer here and reported the total as "Dashboard
    # API response time" — which on a host where the trading engine is down is
    # dominated by that probe's 2-second timeout, so the dashboard row rendered
    # ~2000 ms and RED for a dashboard that was answering instantly. Each
    # component's latency now belongs to the component that incurred it: the
    # engine's round trip is on the Trading Engine row, the query on the
    # Database row, and this timer covers only the assembly work.
    trader = metrics_client.get_trader_health(cfg)
    dbh = db_reader.db_health(cfg)
    pf = db_reader.latest_preflight(cfg, today)

    t0 = time.perf_counter()
    services = _unit_rows(cfg) + _engine_rows(trader) + [_db_row(dbh)]
    services.append(_dashboard_row(round((time.perf_counter() - t0) * 1000.0, 2)))
    # Which of these gate trading safety RIGHT NOW — see `_is_required`.
    for s in services:
        s["required"] = _is_required(s)

    options = {"status": list(_STATUSES),
               "kind": sorted({s["kind"] for s in services})}
    active = {"status": (status_filter or "").upper() or None,
              "kind": kind_filter or None}
    shown = services
    if active["status"]:
        shown = [s for s in shown if s["status"] == active["status"]]
    if active["kind"]:
        shown = [s for s in shown if s["kind"] == active["kind"]]

    counts = {s: sum(1 for x in services if x["status"] == s) for s in _STATUSES}
    overall = _worst([s["status"] for s in services])

    # System uptime = the TRADING ENGINE's own uptime, ⛔ not the dashboard's and
    # ⛔ not a value that resets when this page refreshes: it comes from the
    # trader process's monotonic clock via /health.
    up_sec = ((trader.get("health") or {}).get("uptime_seconds")
              if trader.get("trader_alive") else None)

    # Read once — the live VM probe samples /proc twice over a short interval,
    # so calling it a second time would double that cost for no new information.
    payload_vm = _vm_health(cfg)

    # ⚠️⚠️ THE HEADLINE QUESTION OF THIS SCREEN — "is trading safe RIGHT NOW?" —
    # is answered by the LIVE verdict, ⛔ never by preflight's morning stamp.
    pfv = _preflight_verdict(pf)
    readiness = _live_readiness(pfv, services)

    # Preflight is retained as HISTORY. When the live verdict disagrees with it,
    # say so ON the historical block, so the operator can see that the morning's
    # answer has been overtaken rather than silently dropped. ⛔ This is context,
    # ⛔ not the verdict — the verdict above already accounts for it.
    if pfv.get("ready") and readiness.get("ready") is not True:
        pfv["superseded"] = {
            "by": readiness["label"],
            "message": ("this verdict is SUPERSEDED: preflight judged the system "
                        "%s at %s, but the live state now reads %s"
                        % (pfv.get("overall_status") or "READY",
                           pfv.get("evaluated_at") or "an earlier time",
                           readiness["label"])),
        }

    return {
        "today": today,
        "generated_at": freshness.ist_now().isoformat(),
        "overall": {
            "status": overall,
            "note": {HEALTHY: "All monitored services are running",
                     WARNING: "At least one service is degraded",
                     FAILED: "At least one service has FAILED — intervention required",
                     UNKNOWN: "Some services could not be evaluated"}[overall],
            "rollup": "worst-wins across every service. ⛔ UNKNOWN outranks "
                      "HEALTHY — not knowing is not the same as being well",
        },
        "counts": counts,
        "total_services": len(services),
        "uptime": {"measured": up_sec is not None,
                   "seconds": up_sec, "text": _fmt_uptime(up_sec),
                   "source": "trading engine /health uptime_seconds (its own "
                             "monotonic clock — ⛔ unaffected by a page refresh)",
                   "reason": (None if up_sec is not None
                              else "the trading engine is not answering, so it "
                                   "reports no uptime")},
        "last_health_check": {
            "trader": (trader.get("health") or {}).get("timestamp"),
            "preflight": pfv.get("evaluated_at"),
            "dashboard": freshness.ist_now().isoformat(),
            "note": "three independent clocks — the engine's own health stamp, "
                    "the last preflight run, and this page's build time",
        },

        "services": shown,
        "services_all": len(services),
        "filters": {"active": {k: v for k, v in active.items() if v},
                    "options": options},

        "vm": payload_vm,
        "database": dict(dbh, status=_db_row(dbh)["status"],
                         last_backup=host_reader.newest_backup(cfg)),
        "broker": _broker_health(cfg, pf),
        "dependencies": _dependencies(cfg, pf, services, dbh, today),
        "events": _events(cfg),
        "alerts": _alerts(cfg, today),
        "recovery": _recovery(cfg, today),
        "readiness": readiness,
        "trends": _trends(cfg),

        # ⚠️ This note is derived from the RUNNING HOST, ⛔ not hard-coded — the
        # old fixed text claimed CPU/RAM/Network were uninstrumented, which
        # stopped being true once they were wired to the kernel's interfaces and
        # would have read as a permanent false gap on the production VM.
        "instrumentation_note": (
            ("Live CPU %, RAM %, RAM available, Network and System Load are read "
             "from this host's native interfaces (/proc/stat, /proc/meminfo, "
             "/proc/net/dev, getloadavg) — no psutil, no agent, no plugin. "
             "Persisted CPU/RAM HISTORY is a separate matter: the 5-minute "
             "collector writes -1.0 sentinels without psutil, so those trend "
             "tabs stay empty until the collector records real samples.")
            if payload_vm.get("platform_supported") else
            ("This host is not Linux, so /proc and getloadavg do not exist and "
             "live CPU %, RAM %, RAM available, Network and System Load read as "
             "NO DATA — the metric is real, this machine simply cannot serve it. "
             "On the production Linux VM the same code populates them with no "
             "GUI change. Disk % is measured everywhere (shutil). Network and "
             "CPU/RAM history remain gaps as stated per-metric.")),
        "status_guide": [
            {"status": HEALTHY, "label": "Healthy", "rule": "running and responsive"},
            {"status": WARNING, "label": "Warning", "rule": "degraded but not failed"},
            {"status": FAILED, "label": "Failed", "rule": "down, unreachable or a failed check"},
            {"status": UNKNOWN, "label": "Unknown",
             "rule": "could not be evaluated — ⛔ never counted as healthy"},
        ],
    }


# ⛔ No Scanner dimension exists on this screen at all — it is infrastructure,
# not attribution.
EXPORT_COLS = [
    ("Service", "service"), ("Kind", "kind"), ("Status", "status"),
    # ⭐ Whether this service gates the live readiness verdict — the reader can
    # then see WHY a NOT READY was produced, ⛔ not just that it was.
    ("Gates Trading", "required"),
    ("Raw State", "raw_state"), ("Sub State", "sub_state"),
    ("Started At", "started_at"), ("Uptime", "uptime"),
    ("Uptime (sec)", "uptime_sec"), ("Restarts", "restarts"),
    ("Last Heartbeat", "last_heartbeat"),
    ("Response Time (ms)", "response_ms"), ("Response Note", "response_note"),
]


def export_sheets(payload: dict) -> list:
    """[(sheet, header, rows)] — every sheet from the ONE payload the screen was
    served, so no sheet can disagree with the page."""
    ov, rd, vm = payload["overall"], payload["readiness"], payload["vm"]
    db, bk = payload["database"], payload["broker"]
    _pf = rd.get("preflight") or {}          # historical verdict, ⛔ not current

    def _m(d):
        """Render a measured/gap dict for a spreadsheet cell.

        ⛔ Never a blank — a spreadsheet reader takes an empty cell for zero. And
        ⛔ never "NOT INSTRUMENTED" for something the system does measure: NO DATA
        says the metric is real but nothing has been recorded.
        """
        if not isinstance(d, dict):
            return d
        if d.get("measured"):
            return d.get("value")
        return "NO DATA" if d.get("instrumented") else "NOT INSTRUMENTED"

    def _why(d):
        return "" if not isinstance(d, dict) else (d.get("reason") or "")

    summary = [
        ("Generated at (IST)", payload["generated_at"]),
        ("Overall Status", ov["status"]), ("Overall note", ov["note"]),
        ("Healthy / Warning / Failed / Unknown",
         "%s / %s / %s / %s" % tuple(payload["counts"][s] for s in
                                     ("HEALTHY", "WARNING", "FAILED", "UNKNOWN"))),
        ("Uptime", payload["uptime"]["text"] or "NOT AVAILABLE"),
        ("Uptime source", payload["uptime"]["source"]),
        ("", ""),
        # ⚠️ The CURRENT verdict, from live state. Preflight follows it as
        # clearly-labelled history so the spreadsheet cannot imply a stale READY.
        ("TRADING READINESS (NOW)", rd["label"]),
        ("Readiness verdict", rd["verdict"]), ("Readiness reason", rd["reason"]),
        ("Readiness basis", rd["basis"]),
        ("Live status of required services", rd["live_status"]),
        ("Required services FAILED now", ", ".join(rd["failed_required"]) or "none"),
        ("Required services UNKNOWN now", ", ".join(rd["unknown_required"]) or "none"),
        ("Blocking", "; ".join(rd["blockers"]) or "nothing"),
        ("PREFLIGHT VERDICT (HISTORICAL)", _pf.get("label")),
        ("Preflight overall_status", _pf.get("overall_status")),
        ("Preflight phase", _pf.get("phase")),
        ("Preflight evaluated at", _pf.get("evaluated_at")),
        ("Preflight superseded", (_pf.get("superseded") or {}).get("message") or "no"),
        ("", ""),
        ("CPU %", _m(vm["cpu_pct"])), ("CPU % why", _why(vm["cpu_pct"])),
        ("RAM %", _m(vm["ram_pct"])), ("RAM % why", _why(vm["ram_pct"])),
        ("RAM available (MB)", _m(vm["ram_available_mb"])),
        ("Disk %", _m(vm["disk_pct"])),
        ("Network", _m(vm["network"])), ("Network why", _why(vm["network"])),
        ("System Load", _m(vm["system_load"])),
        ("", ""),
        ("DB reachable", db.get("reachable")), ("DB size (MB)", db.get("size_mb")),
        ("DB query time (ms)", db.get("query_ms")),
        ("DB schema version", db.get("schema_version")),
        ("DB connection count", "NOT APPLICABLE"),
        ("DB connection count why", db.get("connection_count_note")),
        ("Last backup", (db.get("last_backup") or {}).get("name") or "NONE FOUND"),
        ("", ""),
        ("Broker status", bk["broker_status"]), ("Login status", bk["login_status"]),
        ("Token status", bk["token_status"]),
        ("Last API call", _m(bk["last_api_call"])),
        ("Last API call why", _why(bk["last_api_call"])),
        ("Last successful order", _m(bk["last_successful_order"])),
        ("", ""),
        ("INSTRUMENTATION GAP", payload["instrumentation_note"]),
    ]
    return [
        ("Services", [h for h, _k in EXPORT_COLS],
         [[r.get(k) for _h, k in EXPORT_COLS] for r in payload["services"]]),
        ("Readiness", ["Pillar", "Status", "Checks", "Failed checks", "Note"],
         [[p["pillar"], p["status"], p["checks"], ", ".join(p["failed"]),
           p.get("note") or ""] for p in _pf.get("pillars") or []]),
        ("Dependencies", ["Dependency", "Status", "Measured", "Source"],
         [[d["name"], d["status"], d["measured"], d["source"]]
          for d in payload["dependencies"]]),
        ("Alerts", ["At", "Severity", "Semantic status", "Kind", "Source", "Title"],
         [[a["at"], a["severity"], a["status"], a["kind"], a["source"], a["title"]]
          for a in payload["alerts"]]),
        ("Auto-Recovery", ["At", "Check", "Action", "State", "Status", "Error"],
         [[e["at"], e["check"], e["action"], e["state"], e["status"], e["error"]]
          for e in payload["recovery"]["events"]]),
        ("Service Events", ["At", "Type", "Label", "Status", "Detail"],
         [[e["at"], e["type"], e["label"], e["status"], e["detail"]]
          for e in payload["events"]]),
        ("Summary", ["Measure", "Value"], [list(x) for x in summary]),
    ]
