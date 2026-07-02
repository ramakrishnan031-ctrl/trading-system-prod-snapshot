"""
Analytics & config screens (M16-M20, all read-only, login_required):
  GET /api/slippage        — per-leg decomposition + tolerance breach flags
  GET /api/execution       — latency buckets + order_execution_log + recon actions
  GET /api/statistics      — win rate / avg R / expectancy + MFE-MAE + LONG-vs-SHORT
  GET /api/reports         — reports/output listing (download gated, default OFF)
  GET /api/reports/download — 403 while reports_download_enabled=false (Q3 pending)
  GET /api/config          — grouped snapshot view + drift banner (M20)
"""
from __future__ import annotations

import os

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from ..auth import login_required
from ..readers import config_reader, db_reader
from ..services import config_view, freshness

analytics_api = Blueprint("analytics_api", __name__)

_LAT_BUCKETS = ((0, 100), (100, 250), (250, 500), (500, 1000), (1000, 2000), (2000, None))


def _ctx():
    cfg = current_app.config["GUI_CONFIG"]
    today = freshness.ist_today_iso()
    return cfg, today


@analytics_api.route("/api/slippage", methods=["GET"])
@login_required
def get_slippage():
    cfg, today = _ctx()
    sc = config_reader.get_system_config(cfg, today)
    slc = (((sc.get("entry_gate") or {}).get("slippage_control")) or {}) if isinstance(sc, dict) else {}
    frac = float(slc.get("max_slippage_fraction") or 0.22)
    cap = float(slc.get("absolute_cap_rs") or 5.0)

    rows = db_reader.slippage_rows_today(cfg, today)
    per_strategy: dict = {}
    worst = []
    for r in rows:
        dist = r.get("planned_sl_distance")
        entry_slip = r.get("entry_slippage_rs")
        # %-of-SL-distance model (the system's own sl_fraction budget):
        # tolerance = min(fraction × SL distance, absolute ₹ cap); breach when
        # the adverse entry slippage exceeds it. Judgement call: entry leg only
        # (the model governs entry aborts; exits have no configured budget).
        tol = min(frac * float(dist), cap) if dist else None
        r["tolerance_rs"] = round(tol, 4) if tol is not None else None
        r["breach"] = bool(tol is not None and entry_slip is not None
                           and float(entry_slip) > tol)
        agg = per_strategy.setdefault(r["strategy_name"], {
            "strategy": r["strategy_name"], "trades": 0, "entry_slip_rs": 0.0,
            "breaches": 0, "avg_rr_damage_pct": 0.0, "_rrsum": 0.0, "_rrn": 0,
        })
        agg["trades"] += 1
        agg["entry_slip_rs"] = round(agg["entry_slip_rs"] + float(entry_slip or 0.0), 4)
        agg["breaches"] += 1 if r["breach"] else 0
        if r.get("rr_damage_pct") is not None:
            agg["_rrsum"] += float(r["rr_damage_pct"])
            agg["_rrn"] += 1
        worst.append(r)
    for agg in per_strategy.values():
        agg["avg_rr_damage_pct"] = round(agg["_rrsum"] / agg["_rrn"], 2) if agg["_rrn"] else None
        del agg["_rrsum"], agg["_rrn"]
    worst.sort(key=lambda r: float(r.get("entry_slippage_rs") or 0.0), reverse=True)
    return jsonify({
        "today": today,
        "model": {"mode": slc.get("mode"), "max_slippage_fraction": frac,
                  "absolute_cap_rs": cap},
        "count": len(rows), "rows": rows,
        "worst": worst[:10],
        "per_strategy": sorted(per_strategy.values(), key=lambda a: -a["entry_slip_rs"]),
    })


@analytics_api.route("/api/execution", methods=["GET"])
@login_required
def get_execution():
    cfg, today = _ctx()
    lat = db_reader.latency_rows_today(cfg, today)

    def buckets(key):
        counts = []
        vals = [r[key] for r in lat if r.get(key) is not None]
        for lo, hi in _LAT_BUCKETS:
            n = sum(1 for v in vals if v >= lo and (hi is None or v < hi))
            counts.append({"bucket": f"{lo}-{hi if hi is not None else '∞'} ms", "count": n})
        return {"buckets": counts, "n": len(vals)}

    return jsonify({
        "today": today,
        "latency": {
            "signal_to_order_ms": buckets("signal_to_order_ms"),
            "order_to_fill_ms": buckets("order_to_fill_ms"),
            "total_latency_ms": buckets("total_latency_ms"),
        },
        "execution_log": db_reader.execution_log_today(cfg, today),
        "reconciliation_actions": db_reader.audit_feed(cfg, today,
                                                       table="reconciliation_log"),
    })


@analytics_api.route("/api/statistics", methods=["GET"])
@login_required
def get_statistics():
    cfg, today = _ctx()
    bundle = db_reader.statistics_bundle(cfg, today)
    per_dir = []
    for r in bundle["per_direction"]:
        wins, losses = int(r["wins"]), int(r["losses"])
        decided = wins + losses
        win_rate = round(100.0 * wins / decided, 1) if decided else None
        avg_win = round(float(r["win_sum"]) / wins, 2) if wins else 0.0
        avg_loss = round(abs(float(r["loss_sum"]) / losses), 2) if losses else 0.0
        expectancy = (round((wins / decided) * avg_win - (losses / decided) * avg_loss, 2)
                      if decided else None)
        per_dir.append({
            "direction": r["direction"], "closed": int(r["closed"]),
            "wins": wins, "losses": losses, "win_rate": win_rate,
            "net": round(float(r["net"]), 2), "avg_r": round(float(r["avg_r"]), 3),
            "expectancy": expectancy,
        })
    # LONG vs SHORT permanent split panel — absent side shown as zeros, honestly.
    for side in ("LONG", "SHORT"):
        if not any(d["direction"] == side for d in per_dir):
            per_dir.append({"direction": side, "closed": 0, "wins": 0, "losses": 0,
                            "win_rate": None, "net": 0.0, "avg_r": 0.0, "expectancy": None})
    per_dir.sort(key=lambda d: d["direction"])
    return jsonify({
        "today": today,
        "per_direction": per_dir,
        "trades_with_excursions": bundle["trades_with_excursions"],
        "innings": bundle["innings"],
    })


@analytics_api.route("/api/reports", methods=["GET"])
@login_required
def get_reports():
    cfg, _today = _ctx()
    root = cfg["paths"].get("reports_dir")
    files = []
    total = 0
    if root and os.path.isdir(root):
        for name in sorted(os.listdir(root), reverse=True):
            if not name.endswith(".xlsx"):
                continue
            st = os.stat(os.path.join(root, name))
            files.append({"name": name, "size_bytes": st.st_size, "mtime": st.st_mtime})
            total += st.st_size
    return jsonify({
        "count": len(files), "files": files,
        "total_size_bytes": total,
        "retention_note": "reports/output has NO retention — grows unbounded (G0 §4.4)",
        "download_enabled": bool(cfg.get("reports_download_enabled", False)),
        "download_note": "Q3 (copy-protection bypass acceptance) pending with Rama"
                         " — button disabled until answered",
    })


@analytics_api.route("/api/reports/download", methods=["GET"])
@login_required
def download_report():
    cfg, _today = _ctx()
    if not bool(cfg.get("reports_download_enabled", False)):
        return jsonify({"error": "downloads disabled (Q3 pending)"}), 403
    name = (request.args.get("file") or "").strip()
    if not name or name != os.path.basename(name) or not name.endswith(".xlsx"):
        return jsonify({"error": "invalid file"}), 400
    return send_from_directory(cfg["paths"]["reports_dir"], name, as_attachment=True)


@analytics_api.route("/api/config", methods=["GET"])
@login_required
def get_config():
    cfg, _today = _ctx()
    return jsonify(config_view.build_config_view(cfg))
