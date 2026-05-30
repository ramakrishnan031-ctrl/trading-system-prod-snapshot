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
