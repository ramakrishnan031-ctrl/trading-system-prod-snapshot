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


def _check_token() -> dict:
    """FIX-188: token validity (reuses scripts.zerodha_login.is_token_valid).

    C-3 (audit 02-Jul): do NOT expose account_id or raw exception strings in the
    payload — report a presence boolean + the (non-secret) expiry only.
    """
    try:
        from pathlib import Path
        from scripts.zerodha_login import is_token_valid, load_token

        token_path = Path("data_store/session/zerodha_token.json")
        tok = load_token(token_path) or {}
        account_id = tok.get("account_id", "")
        ok = bool(account_id) and is_token_valid(account_id, token_path)
        return {"ok": bool(ok), "account_present": bool(account_id), "expires_at": tok.get("expires_at")}
    except Exception:
        return {"ok": False, "error": "token_check_failed"}


def _check_kill_switch(state_store: Any, logger: Any) -> dict:
    """FIX-188: kill switch state from system_state (ok iff INACTIVE).

    C-3: genericise the raw exception string in the payload.
    """
    try:
        row = state_store.fetch_one(
            "SELECT state, reason FROM kill_switch_state WHERE id = 1", ()
        )
        state = row["state"] if (row and row["state"]) else "INACTIVE"
        reason = (row["reason"] or "") if row else ""
        return {"ok": state == "INACTIVE", "state": state, "reason": reason}
    except Exception:
        return {"ok": False, "error": "kill_switch_check_failed"}


def _create_app(state_store: Any, logger: Any, metrics_provider=None,
                tgt_retry_provider=None, daemon_liveness_provider=None) -> Flask:
    app = Flask("healthcheck")

    @app.route("/health", methods=["GET"])
    def health() -> Response:
        from core.time_authority import now_ist

        uptime = round(time.monotonic() - _start_time, 1)

        # DB check (also yields trades_today)
        db_check = {"ok": True}
        trades_today = 0
        try:
            today_iso = date.today().isoformat()
            row = state_store.fetch_one(
                "SELECT COUNT(*) AS cnt FROM trades WHERE DATE(created_at) = ?",
                (today_iso,),
            )
            trades_today = int(row["cnt"] or 0) if row else 0
        except Exception as exc:
            # C-3: keep the real error in the server log, not the (unauth) payload.
            db_check = {"ok": False, "error": "db_check_failed"}
            logger.error("healthcheck.trades_query_failed", extra={"error": str(exc)})

        # FIX-188: broaden /health beyond the DB — token validity + kill switch.
        checks = {
            "db": db_check,
            "token": _check_token(),
            "kill_switch": _check_kill_switch(state_store, logger),
        }
        # Post-mortem 24-Jun: surface the TGT-retry safety daemon's liveness so a
        # silently-dead/crash-looping worker turns /health 503 (and shows up as a
        # pre-flight Phase-B failing check), not just a CRITICAL email.
        if tgt_retry_provider is not None:
            try:
                snap = tgt_retry_provider()
                checks["tgt_retry"] = snap if isinstance(snap, dict) else {"ok": False}
            except Exception:
                checks["tgt_retry"] = {"ok": False, "error": "tgt_retry_provider_failed"}
        # E-4 (audit 02-Jul): surface the core daemon poll threads' liveness so a
        # silently-dead order_monitor / order_reconciler / eod_scheduler turns
        # /health 503 (and a pre-flight Phase-B failure) instead of ceasing
        # crash-recovery-SL / CHECK9 / orphan cleanup / capital-drift unnoticed.
        # The provider returns {name: {ok, ...}} — merged so each gates overall_ok.
        # live_feed connection is reported but non-gating (a feed disconnect
        # auto-heals via reconnect and must not false-alarm uptime monitors).
        if daemon_liveness_provider is not None:
            try:
                dsnap = daemon_liveness_provider()
                if isinstance(dsnap, dict):
                    for name, sub in dsnap.items():
                        checks[name] = sub if isinstance(sub, dict) else {"ok": bool(sub)}
            except Exception:
                checks["daemons"] = {"ok": False, "error": "daemon_liveness_provider_failed"}
        overall_ok = all(c.get("ok", False) for c in checks.values())

        body = json.dumps({
            "status": "healthy" if overall_ok else "degraded",
            "checks": checks,
            "uptime_seconds": uptime,
            "trades_today": trades_today,
            "timestamp": now_ist().isoformat(),
        })
        # 503 lets uptime monitors detect a degraded-but-listening process.
        return Response(body, status=200 if overall_ok else 503, mimetype="application/json")

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
            "queue_depth": 0,
            "queue_capacity": 0,
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
                "SELECT state FROM kill_switch_state WHERE id = 1",
                (),
            )
            if row:
                m["kill_switch_state"] = row["state"] or "INACTIVE"

            row = state_store.fetch_one(
                "SELECT received_at FROM signals WHERE DATE(received_at) = ? ORDER BY received_at DESC LIMIT 1",
                (today_iso,),
            )
            if row and row["received_at"]:
                ts = row["received_at"]
                m["last_signal_at"] = ts.split("T")[1][:8] if "T" in ts else ts

        except Exception as exc:
            logger.error("metrics.query_failed", extra={"error": str(exc)})

        # FIX-190 (Bug B): merge in-memory runtime counters (signals_processed /
        # entries_placed / entries_throttled / entries_rejected) from the signal
        # processor so live behaviour — esp. throttle drops, which never become
        # trades — is observable.
        if metrics_provider is not None:
            try:
                rt = metrics_provider()
                if isinstance(rt, dict):
                    m.update(rt)
            except Exception as exc:
                logger.error("metrics.runtime_provider_failed", extra={"error": str(exc)})

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
    host: str = "127.0.0.1",  # C-3: loopback-only — /health + /metrics expose P&L / capital / kill-switch posture. Was 0.0.0.0.
    metrics_provider=None,
    tgt_retry_provider=None,
    daemon_liveness_provider=None,
) -> Optional[threading.Thread]:
    """Start the healthcheck HTTP server in a daemon thread (HC2, HC3).

    FIX-190 (Bug B): metrics_provider() optionally supplies in-memory runtime
    counters merged into /metrics (signal processor's get_runtime_metrics).
    Post-mortem 24-Jun: tgt_retry_provider() optionally supplies the TGT-retry
    daemon's liveness snapshot, added to the /health checks."""
    app = _create_app(state_store, logger, metrics_provider, tgt_retry_provider,
                      daemon_liveness_provider)

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
