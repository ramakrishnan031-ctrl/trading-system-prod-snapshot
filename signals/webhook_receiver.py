"""
signals/webhook_receiver.py -- Trading System v2

Purpose:
    Flask-based HTTP entry point for Chartink scanner webhooks.
    Validates payload, deduplicates, enforces backpressure/expiry,
    and enqueues (signal_id, scanner_name, symbol, price, triggered_at)
    tuples for downstream signal_processor. Never blocks on heavy work.

Locked Design Decisions:
    WR1  -- HTTP-facing gateway; no heavy work in request thread
    WR2  -- Flask, single file; POST /webhook/<scanner_name> + GET /health
    WR3  -- Constructor: signal_queue, state_store, config, market_windows,
             kill_switch, logger, secret_token=None
    WR4  -- Payload: stocks, trigger_prices, triggered_at, scan_name
    WR5  -- Response codes: 200/400/401/403/404/503/500
    WR6  -- Backpressure (503) + per-signal expiry check
    WR7  -- SHA-256 fingerprint dedup at minute precision
    WR8  -- Optional HMAC validation via X-Webhook-Signature header
    WR9  -- Insert into signals table then push to queue
    WR10 -- Per-signal status: ACCEPTED/DUPLICATE/EXPIRED/INVALID_SYMBOL/
             INVALID_PRICE/QUEUE_FULL/OUTSIDE_HOURS/IN_PROCESS
    WR11 -- Thread-safe; each Flask request in its own thread
    WR12 -- stop() for graceful shutdown
    WR13 -- webhook_audit row per POST regardless of outcome
    WR14 -- signal_queue.expiry_sec + webhook config section
    WR15 -- Layer 5 (signals/)
    WR16 -- NOT in scope: screening, strategy lookup, order placement
    WR17 -- In-flight symbol tracking to prevent concurrent processing

What This Module Does NOT Do:
    - Does not screen or score signals (signals/signal_processor)
    - Does not look up strategies or map scanner to order parameters
    - Does not place orders
    - Does not send Telegram alerts on rejection
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import queue
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from flask import Flask, request, jsonify

from core.ids import new_signal_id
from core.time_authority import now_ist

_IST = timezone(timedelta(hours=5, minutes=30), name="IST")


class WebhookReceiver:
    """
    Thin validating HTTP gateway for Chartink scanner webhooks.

    Usage::
        receiver = WebhookReceiver(signal_queue, state_store, config,
                                   market_windows, kill_switch, logger)
        receiver.app.run(host=config.webhook.bind_host,
                         port=config.webhook.bind_port)
    """

    def __init__(
        self,
        signal_queue: queue.Queue,
        state_store: Any,
        config: Any,          # SystemConfig (duck-typed for testability)
        market_windows: Any,  # MarketWindows
        kill_switch: Any,     # KillSwitch (may be None in tests)
        logger: Any,
        secret_token: Optional[str] = None,
    ) -> None:
        self._queue = signal_queue
        self._store = state_store
        self._config = config
        self._mw = market_windows
        self._ks = kill_switch
        self._log = logger
        self._secret = secret_token

        # WR17: in-flight symbol set (thread-safe)
        # HIGH #9: dict[symbol, enqueue_monotonic] for timeout sweeping
        self._in_flight: dict[str, float] = {}
        self._in_flight_lock = threading.Lock()
        self._in_flight_timeout_sec: float = 300.0  # 5 min hard eviction

        # HIGH #9: background sweeper evicts stuck in_flight entries
        self._sweeper_stop = threading.Event()
        self._sweeper_thread = threading.Thread(
            target=self._run_sweeper, name="in_flight_sweeper", daemon=True
        )
        self._sweeper_thread.start()

        self.app = Flask(__name__)
        self.app.config["TESTING"] = False
        self._register_routes()

    # ------------------------------------------------------------------
    # Route registration
    # ------------------------------------------------------------------

    def _register_routes(self) -> None:
        app = self.app
        receiver = self  # closure reference

        @app.route("/health", methods=["GET"])
        def health():
            ks_active = bool(receiver._ks.is_active()) if receiver._ks else False
            q_size = receiver._queue.qsize()
            q_cap = receiver._config.signal_queue.capacity
            return jsonify({
                "status": "ok",
                "kill_switch_active": ks_active,
                "queue_size": q_size,
                "queue_capacity": q_cap,
            }), 200

        @app.route("/webhook/<scanner_name>", methods=["POST"])
        def webhook(scanner_name: str):
            return receiver._handle_webhook(scanner_name)

        @app.errorhandler(500)
        def internal_error(exc):
            receiver._log.critical(f"Unhandled exception in webhook handler: {exc}")
            return jsonify({"error": "Internal server error"}), 500

    # ------------------------------------------------------------------
    # Main request handler (WR4, WR5)
    # ------------------------------------------------------------------

    def _handle_webhook(self, scanner_name: str):
        start_mono = time.monotonic()
        source_ip: str = request.remote_addr or "unknown"
        raw_body: bytes = request.get_data()
        payload_size: int = len(raw_body)

        response_code = 500
        accepted_count = 0
        rejected_count = 0

        try:
            resp = self._process_request(scanner_name, raw_body)
            response_code = resp[1] if isinstance(resp, tuple) else 200
            # Extract accepted/rejected from 200 responses
            if response_code == 200 and isinstance(resp, tuple):
                data = resp[0].get_json(silent=True) or {}
                accepted_count = data.get("accepted", 0)
                rejected_count = data.get("rejected", 0)
            return resp
        except Exception as exc:
            self._log.critical(f"Unhandled exception processing /webhook/{scanner_name}: {exc}")
            response_code = 500
            return jsonify({"error": "Internal server error"}), 500
        finally:
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            self._write_audit(
                scanner_name, source_ip, payload_size,
                response_code, accepted_count, rejected_count, duration_ms,
            )

    def _process_request(self, scanner_name: str, raw_body: bytes):
        sq_cfg = self._config.signal_queue

        # WR8: HMAC validation (when secret configured)
        if self._secret:
            sig_header: str = request.headers.get("X-Webhook-Signature", "")
            if not sig_header.startswith("sha256="):
                return jsonify({"error": "Missing or malformed X-Webhook-Signature header"}), 401
            provided_hex = sig_header[7:]
            expected_hex = _hmac.new(
                self._secret.encode(), raw_body, hashlib.sha256
            ).hexdigest()
            if not _hmac.compare_digest(provided_hex, expected_hex):
                return jsonify({"error": "HMAC signature mismatch"}), 401

        # WR4: scanner_name must be in scan_webhook_map
        known_scanners: dict[str, str] = self._config.scan_webhook_map.scanners
        if scanner_name not in known_scanners:
            return jsonify({"error": f"Unknown scanner: {scanner_name!r}"}), 404

        # WR5: kill_switch active -> 403
        if self._ks and self._ks.is_active():
            return jsonify({"error": "Kill switch active; signals rejected"}), 403

        # WR6: backpressure check (before entry-window so fast path wins)
        capacity: int = sq_cfg.capacity
        bp_threshold = int(capacity * sq_cfg.backpressure_pct)
        if self._queue.qsize() >= bp_threshold:
            return jsonify({"error": "Signal queue at capacity; retry later"}), 503

        # WR5: outside entry window -> 403
        now = now_ist()
        if not self._mw.is_entry_allowed(now):
            return jsonify({"error": "Outside entry window"}), 403

        # Parse JSON body
        try:
            body: dict = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError) as exc:
            return jsonify({"error": f"Malformed JSON: {exc}"}), 400

        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        # Required field presence
        for field in ("stocks", "trigger_prices", "triggered_at", "scan_name"):
            if field not in body:
                return jsonify({"error": f"Missing required field: {field!r}"}), 400

        # WR4: scan_name must match path param (defense in depth)
        if body["scan_name"] != scanner_name:
            return jsonify({"error": "scan_name in body does not match scanner_name path param"}), 400

        # Parse triggered_at (IST naive, format: "YYYY-MM-DD HH:MM:SS")
        try:
            triggered_at: datetime = datetime.strptime(
                str(body["triggered_at"]), "%Y-%m-%d %H:%M:%S"
            )
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid triggered_at; expected YYYY-MM-DD HH:MM:SS"}), 400

        # Parse stocks / trigger_prices
        stocks_raw = body["stocks"]
        prices_raw = body["trigger_prices"]
        if not isinstance(stocks_raw, str) or not isinstance(prices_raw, str):
            return jsonify({"error": "stocks and trigger_prices must be comma-separated strings"}), 400

        symbols = [s.strip() for s in stocks_raw.split(",")]
        price_strs = [p.strip() for p in prices_raw.split(",")]

        if len(symbols) != len(price_strs):
            return jsonify({"error": "stocks and trigger_prices list length mismatch"}), 400

        # Process each stock independently (WR6, WR10)
        received_at = now_ist()
        today_iso: str = received_at.date().isoformat()
        expiry_sec: int = sq_cfg.expiry_sec

        results = []
        accepted_count = 0
        rejected_count = 0

        for symbol, price_str in zip(symbols, price_strs):
            item = self._process_signal(
                scanner_name, symbol, price_str,
                triggered_at, received_at, today_iso, expiry_sec,
            )
            results.append(item)
            if item["status"] == "ACCEPTED":
                accepted_count += 1
            else:
                rejected_count += 1

        # HIGH #6: return 503 when queue is full so client knows to retry
        any_queue_full = any(r["status"] == "QUEUE_FULL" for r in results)
        http_status = 503 if any_queue_full else 200
        return jsonify({
            "accepted": accepted_count,
            "rejected": rejected_count,
            "results": results,
        }), http_status

    # ------------------------------------------------------------------
    # Per-signal processing (WR9, WR10, WR17)
    # ------------------------------------------------------------------

    def _process_signal(
        self,
        scanner_name: str,
        symbol: str,
        price_str: str,
        triggered_at: datetime,
        received_at: datetime,
        today_iso: str,
        expiry_sec: int,
    ) -> dict[str, Any]:

        # WR10: validate symbol
        if not symbol:
            return {"symbol": symbol, "status": "INVALID_SYMBOL"}

        # WR10: validate price
        try:
            price = float(price_str)
        except (ValueError, TypeError):
            return {"symbol": symbol, "status": "INVALID_PRICE"}
        if price <= 0:
            return {"symbol": symbol, "status": "INVALID_PRICE"}

        # WR6: signal expiry (triggered_at is naive IST; attach _IST for comparison)
        now = now_ist()
        triggered_at_aware = triggered_at.replace(tzinfo=_IST)
        age_sec = (now - triggered_at_aware).total_seconds()
        if age_sec > expiry_sec:
            return {"symbol": symbol, "status": "EXPIRED"}

        # WR17: in-flight check (symbol already being processed downstream)
        with self._in_flight_lock:
            if symbol in self._in_flight:
                return {"symbol": symbol, "status": "IN_PROCESS"}

        # WR7: compute dedup fingerprint at minute precision
        minute_str = triggered_at.strftime("%Y-%m-%d %H:%M")
        fp_raw = f"{scanner_name}|{symbol}|{minute_str}"
        fingerprint = hashlib.sha256(fp_raw.encode()).hexdigest()

        # Pre-check for duplicate (fast path; avoids unnecessary DB write)
        existing = self._store.fetch_one(
            "SELECT signal_id FROM signals WHERE fingerprint = ? AND fingerprint_date = ?",
            (fingerprint, today_iso),
        )
        if existing is not None:
            return {"symbol": symbol, "status": "DUPLICATE"}

        # WR9: insert signal row, then push to queue
        signal_id = new_signal_id()
        triggered_at_iso = triggered_at.isoformat()
        received_at_iso = received_at.isoformat()

        try:
            with self._store.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO signals
                      (signal_id, symbol, scanner, strategy,
                       triggered_at, received_at, expires_at,
                       status, fingerprint, fingerprint_date, trigger_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal_id, symbol, scanner_name, scanner_name,
                        triggered_at_iso, received_at_iso, received_at_iso,
                        "QUEUED", fingerprint, today_iso, price,
                    ),
                )
        except sqlite3.IntegrityError:
            # Race: another concurrent request inserted same fingerprint first
            return {"symbol": symbol, "status": "DUPLICATE"}

        # Push to signal_queue
        entry = (signal_id, scanner_name, symbol, price, triggered_at)
        try:
            self._queue.put_nowait(entry)
        except queue.Full:
            # Mark QUEUE_FULL in DB so signal is not silently lost
            try:
                with self._store.transaction() as cur:
                    cur.execute(
                        "UPDATE signals SET status = 'QUEUE_FULL' WHERE signal_id = ?",
                        (signal_id,),
                    )
            except Exception as upd_exc:
                self._log.error(f"Failed to mark QUEUE_FULL for {signal_id}: {upd_exc}")
            return {"symbol": symbol, "status": "QUEUE_FULL"}

        # Add to in-flight dict AFTER successful enqueue (WR17)
        with self._in_flight_lock:
            self._in_flight[symbol] = time.monotonic()

        return {"symbol": symbol, "status": "ACCEPTED", "signal_id": signal_id}

    # ------------------------------------------------------------------
    # In-flight management (WR17)
    # ------------------------------------------------------------------

    def release_in_flight(self, symbol: str) -> None:
        """
        Remove symbol from the in-flight set.
        Called by signal_processor when it finishes processing a signal.
        """
        with self._in_flight_lock:
            self._in_flight.pop(symbol, None)

    # ------------------------------------------------------------------
    # Audit logging (WR13)
    # ------------------------------------------------------------------

    def _write_audit(
        self,
        scanner_name: str,
        source_ip: str,
        payload_size_bytes: int,
        response_code: int,
        signals_accepted: int,
        signals_rejected: int,
        duration_ms: int,
    ) -> None:
        ts = now_ist().isoformat()
        try:
            with self._store.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO webhook_audit
                      (ts, scanner_name, source_ip, payload_size_bytes,
                       response_code, signals_accepted, signals_rejected,
                       duration_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts, scanner_name, source_ip, payload_size_bytes,
                        response_code, signals_accepted, signals_rejected,
                        duration_ms,
                    ),
                )
        except Exception as exc:
            self._log.error(f"Failed to write webhook_audit row: {exc}")

    # ------------------------------------------------------------------
    # Shutdown (WR12)
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Graceful shutdown hook. Called by main.py shutdown handler."""
        self._sweeper_stop.set()
        self._log.info("WebhookReceiver.stop() called; shutting down")

    # ------------------------------------------------------------------
    # In-flight sweeper (HIGH #9)
    # ------------------------------------------------------------------

    def _run_sweeper(self) -> None:
        """
        Background daemon that evicts in_flight entries older than
        _in_flight_timeout_sec (300s).  Runs every 60s.
        Prevents permanently locked symbols when signal_processor crashes
        before the finally block runs.
        """
        while not self._sweeper_stop.wait(timeout=60.0):
            now_mono = time.monotonic()
            evicted = []
            with self._in_flight_lock:
                for sym, added_at in list(self._in_flight.items()):
                    if now_mono - added_at > self._in_flight_timeout_sec:
                        evicted.append(sym)
                for sym in evicted:
                    del self._in_flight[sym]
            for sym in evicted:
                self._log.warning(
                    "in_flight_sweeper: evicted stuck symbol %s "
                    "(in_flight > %.0fs); signal_processor may have crashed",
                    sym,
                    self._in_flight_timeout_sec,
                )
