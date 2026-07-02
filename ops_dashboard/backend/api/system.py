"""
System screens (M11-M15, all read-only, login_required):
  GET /api/services — systemd units + trader /health + cron expected-vs-actual
                      + control-tower open findings
  GET /api/vm       — live RAM/load/disk (platform-guarded) + disk history;
                      CPU/RAM history stated as not collected (psutil absent)
  GET /api/logs     — whitelisted file list / hardened tail (M13)
  GET /api/audit    — unified paginated feed over 6 audit sources
  GET /api/alerts   — telegram_alerts + failed_alerts.log tail + sentinels
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..auth import login_required
from ..readers import config_reader, db_reader, host_reader, log_reader, metrics_client
from ..services import freshness

system_api = Blueprint("system_api", __name__)


def _ctx():
    cfg = current_app.config["GUI_CONFIG"]
    today = freshness.ist_today_iso()
    return cfg, today


@system_api.route("/api/services", methods=["GET"])
@login_required
def get_services():
    cfg, today = _ctx()
    jobs = config_reader.get_cron_jobs(cfg)
    beats = db_reader.latest_heartbeats(cfg, today)
    cron = []
    for name, meta in sorted(jobs.items()):
        if not meta["enabled"]:
            continue
        hb = beats.get(name)
        cron.append({
            "job": name, "monitored": meta["monitored"], "critical": meta["critical"],
            "cron_expression": meta["cron_expression"],
            "heartbeat": hb,      # None = not run today (expected-vs-actual is visual)
            "status": (hb or {}).get("status") if hb else
                      ("PENDING" if meta["monitored"] else "UNMONITORED"),
        })
    return jsonify({
        "today": today,
        "units": host_reader.all_units(cfg),
        "trader": metrics_client.get_trader_health(cfg),
        "cron": cron,
        "control_tower_findings": db_reader.control_tower_open_findings(cfg),
    })


@system_api.route("/api/vm", methods=["GET"])
@login_required
def get_vm():
    cfg, _today = _ctx()
    return jsonify({
        "live": host_reader.vm_stats(cfg),
        "disk_history": db_reader.system_metrics_disk_history(cfg),
        "cpu_ram_history": {
            "collected": False,
            "note": "not collected (psutil absent on the VM — system_metrics "
                    "cpu/mem are -1.0 sentinels); shown honestly, never charted",
        },
    })


@system_api.route("/api/logs", methods=["GET"])
@login_required
def get_logs():
    cfg, _today = _ctx()
    filename = (request.args.get("file") or "").strip()
    if not filename:
        return jsonify({"files": log_reader.list_log_files(cfg)})
    try:
        path = log_reader.resolve_log_path(cfg, filename)
    except PermissionError:
        return jsonify({"error": "forbidden path"}), 403
    except FileNotFoundError:
        return jsonify({"error": "no such log file"}), 404
    try:
        n = int(request.args.get("n", 200))
    except ValueError:
        n = 200
    tail = log_reader.tail_lines(path, n)
    rows = log_reader.parse_structured(
        tail["lines"],
        level=(request.args.get("level") or "").strip() or None,
        q=(request.args.get("q") or "").strip() or None,
        ref_id=(request.args.get("id") or "").strip() or None,
    )
    return jsonify({
        "file": filename, "file_size": tail["file_size"],
        "bytes_read": tail["bytes_read"], "truncated_scan": tail["truncated_scan"],
        "count": len(rows), "rows": rows,
        "download": "disabled (read-only viewer)",
    })


@system_api.route("/api/audit", methods=["GET"])
@login_required
def get_audit():
    cfg, _default = _ctx()
    date = (request.args.get("date") or "").strip()
    today = date if (len(date) == 10 and date[4] == "-" and date[7] == "-") else _default
    table = (request.args.get("table") or "").strip() or None
    severity = (request.args.get("severity") or "").strip() or None
    try:
        page = max(1, int(request.args.get("page", 1)))
        size = max(1, min(int(request.args.get("size", 50)), 200))
    except ValueError:
        page, size = 1, 50
    rows = db_reader.audit_feed(cfg, today, table=table, severity=severity, limit=500)
    start = (page - 1) * size
    return jsonify({
        "date": today, "total": len(rows), "page": page, "size": size,
        "rows": rows[start:start + size],
        "sources": ["system_events", "reconciliation_log", "webhook_audit",
                    "eod_verification", "preflight", "control_tower"],
    })


@system_api.route("/api/alerts", methods=["GET"])
@login_required
def get_alerts():
    cfg, today = _ctx()
    failed_tail = []
    try:
        path = log_reader.resolve_log_path(cfg, "failed_alerts.log")
        failed_tail = log_reader.tail_lines(path, 50)["lines"]
    except (PermissionError, FileNotFoundError):
        failed_tail = []
    return jsonify({
        "today": today,
        "alerts": db_reader.telegram_alerts_today(cfg, today),
        "failed_alerts_tail": failed_tail,
        "sentinels": host_reader.list_sentinels(cfg),
        "note": "Telegram is OUTBOUND-ONLY — no inbound command channel exists (G0 §3)",
    })
