"""
scripts/healthcheck_server.py -- Trading System v2  FIX-132 Item 15

Purpose:
    Lightweight HTTP health endpoint for external uptime monitoring.
    Runs on port 8080 (separate from webhook port 5000).

Locked Design Decisions:
    HC1  -- GET /health returns JSON: {status, uptime_seconds, trades_today, timestamp}.
    HC2  -- Runs in a daemon thread started from main.py at boot.
    HC3  -- Uses waitress (already in requirements) for production WSGI.
    HC4  -- trades_today queries state_store for today's CLOSED+OPEN trade count.
    HC5  -- uptime_seconds computed from process start time.
    HC6  -- Graceful: daemon thread dies on process exit; no explicit stop needed.
"""
from __future__ import annotations

import json
import time
import threading
from datetime import date
from typing import Any, Optional

from flask import Flask, Response


_start_time = time.monotonic()


def _create_app(state_store: Any, logger: Any) -> Flask:
    app = Flask("healthcheck")

    @app.route("/health", methods=["GET"])
    def health() -> Response:
        uptime = round(time.monotonic() - _start_time, 1)
        trades_today = 0
        try:
            today_iso = date.today().isoformat()
            row = state_store.fetch_one(
                "SELECT COUNT(*) AS cnt FROM trades WHERE DATE(created_at) = ?",
                (today_iso,),
            )
            if row:
                trades_today = int(row["cnt"] or 0)
        except Exception as exc:
            logger.error("healthcheck.trades_query_failed", extra={"error": str(exc)})

        from core.time_authority import now_ist
        body = json.dumps({
            "status": "ok",
            "uptime_seconds": uptime,
            "trades_today": trades_today,
            "timestamp": now_ist().isoformat(),
        })
        return Response(body, status=200, mimetype="application/json")

    # FIX-133 Item 24: structured metrics endpoint
    @app.route("/metrics", methods=["GET"])
    def metrics() -> Response:
        uptime = round(time.monotonic() - _start_time, 1)
        today_iso = date.today().isoformat()
        m = {
            "trades_today": 0,
            "signals_received": 0,
            "signals_traded": 0,
            "open_positions": 0,
            "daily_pnl": 0.0,
            "capital_deployed_pct": 0.0,
            "kill_switch_state": "INACTIVE",
            "uptime_seconds": uptime,
            "last_signal_at": "",
        }
        try:
            row = state_store.fetch_one(
                "SELECT COUNT(*) AS cnt FROM trades WHERE DATE(created_at) = ?",
                (today_iso,),
            )
            if row:
                m["trades_today"] = int(row["cnt"] or 0)

            row = state_store.fetch_one(
                "SELECT COUNT(*) AS cnt FROM signals WHERE DATE(received_at) = ?",
                (today_iso,),
            )
            if row:
                m["signals_received"] = int(row["cnt"] or 0)

            row = state_store.fetch_one(
                "SELECT COUNT(*) AS cnt FROM signals WHERE DATE(received_at) = ? AND status = 'TRADED'",
                (today_iso,),
            )
            if row:
                m["signals_traded"] = int(row["cnt"] or 0)

            row = state_store.fetch_one(
                "SELECT COUNT(*) AS cnt FROM trades WHERE status IN ('OPEN', 'PARTIAL')",
                (),
            )
            if row:
                m["open_positions"] = int(row["cnt"] or 0)

            row = state_store.fetch_one(
                "SELECT COALESCE(SUM(net_pnl), 0.0) AS pnl FROM trades WHERE DATE(created_at) = ? AND status = 'CLOSED'",
                (today_iso,),
            )
            if row:
                m["daily_pnl"] = round(float(row["pnl"] or 0.0), 2)

            row = state_store.fetch_one(
                "SELECT margin_used, cash_floor FROM capital_snapshot WHERE id = 1",
                (),
            )
            if row:
                cash_floor = float(row["cash_floor"] or 1)
                margin_used = float(row["margin_used"] or 0)
                m["capital_deployed_pct"] = round(margin_used / max(cash_floor, 1) * 100, 2)

            row = state_store.fetch_one(
                "SELECT value FROM system_state WHERE key = 'kill_switch_state'",
                (),
            )
            if row:
                m["kill_switch_state"] = row["value"] or "INACTIVE"

            row = state_store.fetch_one(
                "SELECT received_at FROM signals WHERE DATE(received_at) = ? ORDER BY received_at DESC LIMIT 1",
                (today_iso,),
            )
            if row and row["received_at"]:
                ts = row["received_at"]
                m["last_signal_at"] = ts.split("T")[1][:8] if "T" in ts else ts

        except Exception as exc:
            logger.error("metrics.query_failed", extra={"error": str(exc)})

        from core.time_authority import now_ist
        m["timestamp"] = now_ist().isoformat()
        return Response(json.dumps(m), status=200, mimetype="application/json")

    @app.route("/metrics/prometheus", methods=["GET"])
    def metrics_prometheus() -> Response:
        """Prometheus text format (no library needed)."""
        uptime = round(time.monotonic() - _start_time, 1)
        today_iso = date.today().isoformat()
        lines = []

        def _q(sql, params=()):
            try:
                row = state_store.fetch_one(sql, params)
                return row if row else None
            except Exception:
                return None

        trades = _q("SELECT COUNT(*) AS cnt FROM trades WHERE DATE(created_at) = ?", (today_iso,))
        lines.append(f"trades_today {int(trades['cnt'] or 0) if trades else 0}")

        pnl = _q("SELECT COALESCE(SUM(net_pnl), 0.0) AS pnl FROM trades WHERE DATE(created_at) = ? AND status = 'CLOSED'", (today_iso,))
        lines.append(f"daily_pnl {float(pnl['pnl'] or 0) if pnl else 0.0:.2f}")

        open_pos = _q("SELECT COUNT(*) AS cnt FROM trades WHERE status IN ('OPEN', 'PARTIAL')", ())
        lines.append(f"open_positions {int(open_pos['cnt'] or 0) if open_pos else 0}")

        signals = _q("SELECT COUNT(*) AS cnt FROM signals WHERE DATE(received_at) = ?", (today_iso,))
        lines.append(f"signals_received {int(signals['cnt'] or 0) if signals else 0}")

        lines.append(f"uptime_seconds {uptime:.1f}")

        text = "\n".join(lines) + "\n"
        return Response(text, status=200, mimetype="text/plain; charset=utf-8")

    return app


def start_healthcheck_server(
    state_store: Any,
    logger: Any,
    port: int = 8080,
    host: str = "0.0.0.0",
) -> Optional[threading.Thread]:
    """Start the healthcheck HTTP server in a daemon thread (HC2, HC3)."""
    app = _create_app(state_store, logger)

    from waitress import serve as _waitress_serve

    thread = threading.Thread(
        target=_waitress_serve,
        args=(app,),
        kwargs={"host": host, "port": port, "threads": 1},
        name="healthcheck-server",
        daemon=True,
    )
    thread.start()
    logger.info("healthcheck_server.started", extra={"port": port, "host": host})
    return thread
