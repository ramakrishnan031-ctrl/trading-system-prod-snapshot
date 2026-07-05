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
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml
from cachetools import TTLCache
from flask import Flask, request, jsonify

from core.ids import new_signal_id
from core.time_authority import ist_timezone, now_ist


class _PerIpRateLimiter:
    """Thread-safe per-source-IP token bucket (C-2, 02-Jul-2026).

    Bounds request flooding from a single IP while tolerating Chartink's
    legitimate open-bell burst (~40 signals from one IP): each IP gets ``burst``
    tokens, refilled at ``refill_per_sec`` (one token per request; empty -> deny).
    ``now`` is injectable for deterministic tests. Idle, fully-refilled buckets are
    evicted lazily so the map can't grow unbounded under a spoofed-IP flood.
    """

    def __init__(self, burst: int, refill_per_sec: float, *, max_ips: int = 8192) -> None:
        self._burst: float = float(max(1, int(burst)))
        self._refill: float = max(0.0, float(refill_per_sec))
        self._max_ips: int = max_ips
        self._buckets: dict[str, list[float]] = {}   # ip -> [tokens, last_ts]
        self._lock = threading.Lock()

    def allow(self, ip: str, *, now: Optional[float] = None) -> bool:
        ts = time.monotonic() if now is None else now
        with self._lock:
            b = self._buckets.get(ip)
            if b is None:
                if len(self._buckets) >= self._max_ips:
                    self._evict_idle(ts)
                self._buckets[ip] = [self._burst - 1.0, ts]
                return True
            tokens = min(self._burst, b[0] + (ts - b[1]) * self._refill)
            if tokens < 1.0:
                b[0], b[1] = tokens, ts
                return False
            b[0], b[1] = tokens - 1.0, ts
            return True

    def _evict_idle(self, now: float) -> None:
        # Evict on IDLE TIME, not token count (the stored count is stale — it
        # doesn't reflect refill-since-last). A bucket untouched for >60s is idle;
        # if that IP returns it simply starts full again (harmless, it was quiet).
        stale = [ip for ip, (_tok, last) in self._buckets.items() if (now - last) > 60.0]
        for ip in stale:
            self._buckets.pop(ip, None)


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
        # BL-18: if the deployed config declares require_hmac=True, refuse to
        # construct without a secret. Prevents silent downgrade where config
        # claims HMAC is enforced but the receiver silently accepts unsigned
        # requests because secret_token was None.
        #
        # Shape-tolerant resolution: production passes AppConfig (has .system
        # .webhook.require_hmac), tests pass a flat SimpleNamespace (may or
        # may not have .webhook). Absent -> treated as non-strict.
        _webhook_cfg = getattr(config, "webhook", None)
        if _webhook_cfg is None:
            _system_cfg = getattr(config, "system", None)
            if _system_cfg is not None:
                _webhook_cfg = getattr(_system_cfg, "webhook", None)
        _require_hmac = bool(getattr(_webhook_cfg, "require_hmac", False))
        if _require_hmac and not secret_token:
            raise ValueError(
                "WebhookReceiver: config.webhook.require_hmac=True but "
                "secret_token is empty. Set WEBHOOK_SECRET env var or "
                "flip require_hmac to False for non-prod deployments."
            )

        self._queue = signal_queue
        self._store = state_store
        self._config = config
        self._mw = market_windows
        self._ks = kill_switch
        self._log = logger
        self._secret = secret_token
        # G.1 (2026-04-25): persist for request-time enforcement. When True
        # the token-param fallback is disabled -- HMAC is the sole accepted
        # auth surface (token in URL is logged by nginx and weaker than
        # HMAC over the body).
        self._require_hmac = _require_hmac

        # WR17: in-flight symbol set (thread-safe)
        # FIX-011: heartbeat-aware lock tracking. Each entry stores:
        #   {'acquired_at': monotonic_ts, 'heartbeat_at': monotonic_ts}
        # Sweeper evicts if (now - heartbeat_at) > 60s, NOT (now - acquired_at).
        self._in_flight: dict[str, dict[str, float]] = {}
        self._in_flight_lock = threading.Lock()
        self._in_flight_timeout_sec: float = 60.0  # evict if no heartbeat for 60s

        # H-16: set during graceful shutdown to reject new webhooks with 503
        # before signal_processor is stopped. In-flight requests drain
        # naturally; only NEW requests see the flag.
        self._shutting_down = threading.Event()

        # HIGH #9: background sweeper evicts stuck in_flight entries
        self._sweeper_stop = threading.Event()
        self._sweeper_thread = threading.Thread(
            target=self._run_sweeper, name="in_flight_sweeper", daemon=True
        )
        self._sweeper_thread.start()

        # FIX-032: Load symbol aliases once at startup
        # Chartink webhook sends alternate symbol names (e.g., TVSSCS) that don't
        # match Zerodha's trading symbols (TVSSRICHAK). Load the mapping from
        # config/symbol_aliases.yaml to resolve at webhook edge before any DB write
        # or in-flight check uses the wrong symbol name.
        self._alias_map = self._load_symbol_aliases()

        # FIX-036: TTL-based deduplication cache
        # FIX-131 Item 17: TTL driven by dedup_window_seconds (default 300s / 5 min).
        # Key: (symbol, scanner_name). Thread-safe via lock wrapper.
        try:
            _dedup_sec = int(getattr(_webhook_cfg, "dedup_window_seconds", 300))
        except (TypeError, ValueError):
            _dedup_sec = 300
        self._dedup_window_seconds: int = max(60, _dedup_sec)
        self._dedup_cache = TTLCache(maxsize=10000, ttl=self._dedup_window_seconds)
        self._dedup_lock = threading.Lock()

        # C-2 (02-Jul-2026): per-source-IP rate limiter (token bucket). Disabled -> None.
        _rl_on = bool(getattr(_webhook_cfg, "per_ip_rate_limit_enabled", True))
        if _rl_on:
            _burst = int(getattr(_webhook_cfg, "per_ip_burst", 60) or 60)
            _refill = float(getattr(_webhook_cfg, "per_ip_refill_per_sec", 5.0) or 5.0)
            self._ip_limiter: Optional[_PerIpRateLimiter] = _PerIpRateLimiter(_burst, _refill)
        else:
            self._ip_limiter = None

        self.app = Flask(__name__)
        self.app.config["TESTING"] = False
        self.app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # FIX-077: 1MB hard limit prevents OOM
        self._register_routes()

    # ------------------------------------------------------------------
    # FIX-032: Symbol alias loading
    # ------------------------------------------------------------------

    def _load_symbol_aliases(self) -> dict[str, str]:
        """
        FIX-032: Load symbol name aliases from config/symbol_aliases.yaml.

        Returns dict mapping Chartink symbol names to Zerodha trading symbols.
        Empty dict if file doesn't exist or is empty (fail-open: no aliases = passthrough).
        """
        alias_path = Path("config/symbol_aliases.yaml")
        if not alias_path.exists():
            self._log.warning(
                "webhook_receiver: symbol_aliases.yaml not found at %s - "
                "no alias translation will occur", alias_path
            )
            return {}

        try:
            with open(alias_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            # Normalize: keys and values to uppercase strings
            alias_map = {
                str(k).upper(): str(v).upper()
                for k, v in data.items()
                if k and v
            }
            self._log.info(
                "webhook_receiver: loaded %d symbol aliases from %s",
                len(alias_map), alias_path
            )
            return alias_map
        except Exception as exc:
            self._log.error(
                "webhook_receiver: failed to load symbol_aliases.yaml: %s - "
                "no alias translation will occur", exc
            )
            return {}

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
            q_cap = receiver._config.system.signal_queue.capacity
            return jsonify({
                "status": "ok",
                "kill_switch_active": ks_active,
                "queue_size": q_size,
                "queue_capacity": q_cap,
                "queue_depth": f"{q_size}/{q_cap}",
            }), 200

        @app.route("/webhook/<scanner_name>", methods=["POST"])
        def webhook(scanner_name: str):
            return receiver._handle_webhook(scanner_name)

        @app.errorhandler(500)
        def internal_error(exc):
            receiver._log.critical(f"Unhandled exception in webhook handler: {exc}")
            return jsonify({"error": "Internal server error"}), 500

    # ------------------------------------------------------------------
    # FIX-074: Type-cast numeric fields at ingestion
    # ------------------------------------------------------------------

    def _cast_numeric_fields(self, signal: dict[str, Any]) -> tuple[bool, str]:
        """
        FIX-074: Cast known numeric fields from strings to float/int.

        Chartink sends numeric values as strings. Cast them before queueing
        to prevent downstream TypeError in PositionSizer or other components.

        Returns (success, error_msg):
        - (True, "") if all critical fields cast successfully
        - (False, "reason") if a critical field failed to cast

        Critical fields: price, entry_price (must be castable or reject)
        Non-critical fields: trigger_price, sl_pct, target_pct (set to None on failure)
        """
        # Float fields (non-critical by default)
        float_fields = ["trigger_price", "sl_pct", "target_pct"]
        # Critical float fields (must cast successfully)
        critical_float_fields = ["price", "entry_price"]

        # Try casting critical fields first
        for field in critical_float_fields:
            if field in signal and signal[field] is not None:
                try:
                    signal[field] = float(signal[field])
                except (ValueError, TypeError) as exc:
                    return False, f"critical field {field}={signal[field]!r} cannot be cast to float: {exc}"

        # Non-critical float fields: set to None on failure
        for field in float_fields:
            if field in signal and signal[field] is not None:
                try:
                    signal[field] = float(signal[field])
                except (ValueError, TypeError):
                    self._log.warning(
                        "webhook_receiver: could not cast %s=%r to float - setting to None",
                        field, signal[field]
                    )
                    signal[field] = None

        # Integer fields (e.g., qty) - currently none in Chartink format, but prepare for future
        int_fields = ["qty"]
        for field in int_fields:
            if field in signal and signal[field] is not None:
                try:
                    signal[field] = int(signal[field])
                except (ValueError, TypeError):
                    self._log.warning(
                        "webhook_receiver: could not cast %s=%r to int - setting to None",
                        field, signal[field]
                    )
                    signal[field] = None

        return True, ""

    # ------------------------------------------------------------------
    # Main request handler (WR4, WR5)
    # ------------------------------------------------------------------

    def _handle_webhook(self, scanner_name: str):
        start_mono = time.monotonic()
        source_ip: str = request.remote_addr or "unknown"
        raw_body: bytes = request.get_data()
        payload_size: int = len(raw_body)

        # H-16: reject NEW requests during graceful shutdown. In-flight
        # requests continue to completion; only newly arriving ones get 503.
        if self._shutting_down.is_set():
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            self._write_audit(
                scanner_name, source_ip, payload_size, 503, 0, 0, duration_ms,
            )
            return jsonify({"error": "Service shutting down; retry later"}), 503

        # C-2 (02-Jul-2026): per-source-IP rate limit. Bounds a single IP flooding
        # /webhook; Chartink's open-bell burst is absorbed by the token bucket. On
        # exceed -> 429 (before auth/parse/queue work, so a flood is cheap to reject).
        if self._ip_limiter is not None and not self._ip_limiter.allow(source_ip):
            duration_ms = int((time.monotonic() - start_mono) * 1000)
            self._write_audit(
                scanner_name, source_ip, payload_size, 429, 0, 0, duration_ms,
            )
            self._log.warning(
                "webhook/%s: per-IP rate limit exceeded for %s -> 429",
                scanner_name, source_ip,
            )
            return jsonify({"error": "Rate limit exceeded; slow down"}), 429

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
            # Log raw body on 400 errors for debugging Chartink format
            if response_code == 400:
                self._log.warning(
                    "webhook/%s: 400 response | raw_body=%r",
                    scanner_name, raw_body[:1000],
                )
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
        sq_cfg = self._config.system.signal_queue

        # WR8: auth validation (when secret configured)
        # Accepted methods:
        #   1. X-Webhook-Signature: sha256=<hex>  — HMAC over raw body
        #   2. ?token=<secret>                    — query-param bearer
        #      (Chartink-compatible; ONLY accepted when require_hmac=False)
        # G.1 (2026-04-25): when require_hmac=True the token-param path is
        # disabled. Tokens in URL are logged by nginx and weaker than HMAC
        # over the body; allowing token fallback in a "strict HMAC" deploy
        # contradicts the config's stated security posture.
        if self._secret:
            sig_header: str = request.headers.get("X-Webhook-Signature", "")
            token_param: str = request.args.get("token", "")
            if sig_header.startswith("sha256="):
                provided_hex = sig_header[7:]
                expected_hex = _hmac.new(
                    self._secret.encode(), raw_body, hashlib.sha256
                ).hexdigest()
                if not _hmac.compare_digest(provided_hex, expected_hex):
                    return jsonify({"error": "HMAC signature mismatch"}), 401
            elif self._require_hmac:
                # G.1: HMAC required, no signature header -> reject. Do not
                # consult token_param; deployments that flip require_hmac=True
                # have explicitly opted out of the legacy token fallback.
                return jsonify({
                    "error": "HMAC signature required (require_hmac=True); "
                             "token param is not accepted"
                }), 401
            elif token_param:
                if not _hmac.compare_digest(token_param, self._secret):
                    return jsonify({"error": "Invalid token"}), 401
            else:
                return jsonify({"error": "Missing auth: provide X-Webhook-Signature header or ?token= param"}), 401

        # WR4: scanner_name must be in scan_webhook_map
        known_scanners: dict[str, Any] = self._config.scan_webhook_map.scanners
        if scanner_name not in known_scanners:
            return jsonify({"error": f"Unknown scanner: {scanner_name!r}"}), 404

        # WR5: kill_switch active -> 403
        if self._ks and self._ks.is_active():
            return jsonify({"error": "Kill switch active; signals rejected"}), 403

        # WR6 / FIX-134 Item 35: graduated backpressure
        capacity: int = sq_cfg.capacity
        q_size = self._queue.qsize()
        bp_threshold = int(capacity * sq_cfg.backpressure_pct)
        warn_threshold = int(capacity * getattr(sq_cfg, "warning_pct", 0.60))
        if q_size >= bp_threshold:
            resp = jsonify({"error": "Signal queue at capacity; retry later"})
            resp.headers["X-Queue-Depth"] = f"{q_size}/{capacity}"
            return resp, 503

        # WR5: outside entry window -> 403
        now = now_ist()
        if not self._mw.is_entry_allowed(now):
            return jsonify({"error": "Outside entry window"}), 403

        # Parse JSON body
        try:
            body: dict = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError) as exc:
            self._log.warning(
                "webhook/%s: Malformed JSON: %s | raw=%r",
                scanner_name, exc, raw_body[:500],
            )
            return jsonify({"error": f"Malformed JSON: {exc}"}), 400

        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        # FIX-074: Cast numeric fields before processing
        # Handles both top-level numeric fields and per-signal fields if present
        cast_ok, cast_err = self._cast_numeric_fields(body)
        if not cast_ok:
            self._log.warning(
                "webhook/%s: Type cast failed: %s | body_keys=%r",
                scanner_name, cast_err, list(body.keys()),
            )
            return jsonify({"error": f"Invalid payload: {cast_err}"}), 400

        # Required field presence (scan_name optional - derive from URL if missing)
        for field in ("stocks", "trigger_prices", "triggered_at"):
            if field not in body:
                self._log.warning(
                    "webhook/%s: Missing field %r | body_keys=%r",
                    scanner_name, field, list(body.keys()),
                )
                return jsonify({"error": f"Missing required field: {field!r}"}), 400

        # WR4: scan_name in body is optional; if present, validate it matches
        # Chartink sends "GAP FADE LONG" but URL uses "gap_fade_long", so we
        # normalize both to lowercase with underscores before comparing
        body_scan_name = body.get("scan_name")
        if body_scan_name is not None:
            normalized_body = body_scan_name.lower().replace(" ", "_")
            if normalized_body != scanner_name:
                self._log.warning(
                    "webhook/%s: scan_name mismatch: body=%r (normalized=%r) vs path=%r",
                    scanner_name, body_scan_name, normalized_body, scanner_name,
                )
                return jsonify({"error": "scan_name in body does not match scanner_name path param"}), 400

        # Parse triggered_at - Chartink sends "HH:MM am/pm", we also accept "YYYY-MM-DD HH:MM:SS"
        # FIX-022: Immediately localize to IST after parsing to prevent timezone-naive/aware subtraction errors
        triggered_at_raw = str(body["triggered_at"]).strip()
        triggered_at: datetime | None = None
        # Try multiple formats
        for fmt in ("%Y-%m-%d %H:%M:%S", "%I:%M %p", "%H:%M"):
            try:
                parsed = datetime.strptime(triggered_at_raw, fmt)
                if fmt in ("%I:%M %p", "%H:%M"):
                    # Time-only format: use today's date
                    today = now.date()
                    triggered_at = datetime(today.year, today.month, today.day,
                                            parsed.hour, parsed.minute, 0)
                else:
                    triggered_at = parsed
                # FIX-022: Apply IST timezone immediately after parsing
                # Chartink sends naive strings; we assume IST and make them aware
                if triggered_at.tzinfo is None:
                    triggered_at = triggered_at.replace(tzinfo=ist_timezone())
                    self._log.debug("webhook/%s: localized naive triggered_at to IST", scanner_name)
                break
            except ValueError:
                continue
        if triggered_at is None:
            self._log.warning(
                "webhook/%s: Invalid triggered_at=%r", scanner_name, triggered_at_raw,
            )
            return jsonify({"error": "Invalid triggered_at; expected HH:MM am/pm or YYYY-MM-DD HH:MM:SS"}), 400

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

        # S-1B.1 (2026-07-05): sanitize the copy PERSISTED to
        # signals.webhook_payload so the webhook secret never lands at rest.
        # Chartink echoes the configured URL (incl. ?token=<SECRET>) inside the
        # body's `webhook_url` field, and the raw body is stored verbatim below.
        # STORAGE-ONLY: the parsed `body` used for signal processing (stocks/
        # trigger_prices/triggered_at/scan_name, already extracted above) is
        # untouched, and auth (:410-432) ran before this — redaction is post-auth.
        stored_payload = self._sanitize_payload_for_storage(
            raw_body.decode("utf-8", errors="replace")
        )

        results = []
        accepted_count = 0
        rejected_count = 0

        for raw_symbol, price_str in zip(symbols, price_strs):
            # FIX-032: Apply symbol alias at webhook edge BEFORE any operation
            # (DB write, in-flight check, queue push). Chartink sends alternate
            # names (e.g., TVSSCS) that must be resolved to Zerodha symbols
            # (TVSSRICHAK) to prevent ghost locks and instrument cache misses.
            symbol = self._alias_map.get(raw_symbol.upper(), raw_symbol)
            if symbol != raw_symbol:
                self._log.debug(
                    "webhook_receiver: symbol alias applied: raw=%s → resolved=%s",
                    raw_symbol, symbol
                )

            # FIX-C: Check excluded symbols after alias resolution
            excluded_symbols = getattr(self._config.system, "excluded_symbols", [])
            if symbol.upper() in [s.upper() for s in excluded_symbols]:
                self._log.debug(
                    "webhook_receiver: symbol %s rejected (in excluded_symbols list)",
                    symbol
                )
                results.append({"symbol": symbol, "status": "REJECTED_EXCLUDED_SYMBOL"})
                rejected_count += 1
                continue

            item = self._process_signal(
                scanner_name, symbol, price_str,
                triggered_at, received_at, today_iso, expiry_sec,
                webhook_payload=stored_payload,
            )
            results.append(item)
            if item["status"] == "ACCEPTED":
                accepted_count += 1
            else:
                rejected_count += 1

        # HIGH #6: return 503 when queue is full so client knows to retry
        any_queue_full = any(r["status"] == "QUEUE_FULL" for r in results)
        http_status = 503 if any_queue_full else 200
        resp = jsonify({
            "accepted": accepted_count,
            "rejected": rejected_count,
            "results": results,
        })
        # FIX-134 Item 35: graduated backpressure headers
        current_depth = self._queue.qsize()
        resp.headers["X-Queue-Depth"] = f"{current_depth}/{capacity}"
        if current_depth >= warn_threshold:
            resp.headers["X-Queue-Warning"] = "high"
        return resp, http_status

    # ------------------------------------------------------------------
    # Persisted-payload sanitizer (S-1B.1)
    # ------------------------------------------------------------------

    def _sanitize_payload_for_storage(self, payload: str) -> str:
        """S-1B.1: redact the webhook secret from the copy persisted to
        signals.webhook_payload. Chartink echoes the configured webhook URL --
        including ``?token=<SECRET>`` -- inside the body's ``webhook_url`` field,
        so storing the raw body verbatim would leak the secret plaintext at rest.

        STORAGE-ONLY: the argument is only ever written to the DB; the parsed
        ``body`` used for signal processing is never passed here, and auth
        (:410-432) runs before this and is unchanged. Belt-and-suspenders, all
        applied to the stored copy:
          (a) replace the literal secret value wherever it appears;
          (b) regex-redact any ``token=<value>`` query param;
          (c) blank the ``webhook_url`` field value (ignored by parsing).
        Useful audit fields (stocks/trigger_prices/triggered_at/scan_name) are
        preserved.
        """
        sanitized = payload
        # (a) literal secret anywhere -> placeholder
        if self._secret:
            sanitized = sanitized.replace(self._secret, "<REDACTED>")
        # (b) any token=<value> (value up to & " ' whitespace or end-of-string)
        sanitized = re.sub(r"token=[^&\"'\s]+", "token=<REDACTED>", sanitized)
        # (c) blank the webhook_url field value entirely (JSON string, tolerant
        #     of escaped chars); it is ignored by parsing
        sanitized = re.sub(
            r'("webhook_url"\s*:\s*)"(?:[^"\\]|\\.)*"',
            r'\1"<REDACTED>"',
            sanitized,
        )
        return sanitized

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
        webhook_payload: Optional[str] = None,
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

        # WR6: signal expiry
        # FIX-022: triggered_at is now guaranteed IST-aware from parsing,
        # but handle legacy naive datetimes defensively
        now = now_ist()
        if triggered_at.tzinfo is None:
            triggered_at_aware = triggered_at.replace(tzinfo=ist_timezone())
        else:
            triggered_at_aware = triggered_at
        age_sec = (now - triggered_at_aware).total_seconds()
        if age_sec > expiry_sec:
            return {"symbol": symbol, "status": "EXPIRED"}

        # M-1: atomically claim the symbol as in-flight. Closes the TOCTOU
        # gap where the legacy check-then-add admitted concurrent same-symbol
        # signals with DIFFERENT fingerprints (e.g., different minute-rollup)
        # that would both pass the in_flight check and both get enqueued.
        # From here every reject path MUST release; every accepted path lets
        # signal_processor release at completion via release_in_flight().
        if not self._claim_in_flight(symbol):
            return {"symbol": symbol, "status": "IN_PROCESS"}

        # FIX-036: TTLCache deduplication (replaces minute-string fingerprint)
        # Check if (symbol, scanner_name) was seen within last 300 seconds.
        # Thread-safe via lock wrapper.
        dedup_key = (symbol, scanner_name)
        with self._dedup_lock:
            if dedup_key in self._dedup_cache:
                # Duplicate within TTL window
                self._release_in_flight(symbol)
                return {"symbol": symbol, "status": "DUPLICATE"}
            # Mark as seen in cache
            self._dedup_cache[dedup_key] = True

        # WR7 / FIX-131 Item 17: compute dedup fingerprint using configurable bucket.
        # floor(unix_ts / dedup_window_seconds) gives same bucket for all signals
        # within the same N-second window, surviving minute/hour boundaries.
        epoch_bucket = int(triggered_at.timestamp() // self._dedup_window_seconds)
        fp_raw = f"{scanner_name}|{symbol}|{epoch_bucket}"
        fingerprint = hashlib.sha256(fp_raw.encode()).hexdigest()

        # WR9: insert signal row, then push to queue
        signal_id = new_signal_id()
        triggered_at_iso = triggered_at.isoformat()
        received_at_iso = received_at.isoformat()
        expires_at_dt = received_at + timedelta(seconds=expiry_sec)
        expires_at_iso = expires_at_dt.isoformat()

        try:
            with self._store.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO signals
                      (signal_id, symbol, scanner, strategy,
                       triggered_at, received_at, expires_at,
                       status, fingerprint, fingerprint_date, trigger_price,
                       webhook_payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal_id, symbol, scanner_name, scanner_name,
                        triggered_at_iso, received_at_iso, expires_at_iso,
                        "QUEUED", fingerprint, today_iso, price,
                        webhook_payload,
                    ),
                )
        except sqlite3.IntegrityError:
            # Race: another concurrent request inserted same fingerprint first
            self._release_in_flight(symbol)
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
            self._release_in_flight(symbol)
            return {"symbol": symbol, "status": "QUEUE_FULL"}

        # Claim already recorded atomically above; signal_processor will
        # release on completion.
        return {"symbol": symbol, "status": "ACCEPTED", "signal_id": signal_id}

    def _claim_in_flight(self, symbol: str) -> bool:
        """
        M-1 / FIX-011: atomically claim `symbol` as in-flight. Returns True if
        newly claimed; False if already present. Stores a dict with both
        acquired_at and heartbeat_at timestamps. Caller MUST call
        _release_in_flight(symbol) on any reject path after a successful
        claim (DUPLICATE / QUEUE_FULL / IntegrityError). On accepted path,
        signal_processor's release_in_flight() handles cleanup.
        """
        with self._in_flight_lock:
            if symbol in self._in_flight:
                return False
            now_mono = time.monotonic()
            self._in_flight[symbol] = {
                'acquired_at': now_mono,
                'heartbeat_at': now_mono,
            }
            return True

    def _release_in_flight(self, symbol: str) -> None:
        """Internal: remove symbol from the in-flight dict (M-1)."""
        with self._in_flight_lock:
            self._in_flight.pop(symbol, None)

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

    def update_heartbeat(self, symbol: str) -> None:
        """
        FIX-011: Update the heartbeat timestamp for an in-flight symbol.
        Called by signal_processor at checkpoints during processing to prove
        the worker is still alive. If symbol is not in-flight (already released
        or never claimed), silently no-op.
        """
        with self._in_flight_lock:
            entry = self._in_flight.get(symbol)
            if entry is not None:
                entry['heartbeat_at'] = time.monotonic()

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
        """
        Graceful shutdown hook. Called by main.py shutdown handler BEFORE
        signal_processor.stop() so new webhooks see 503 while in-flight
        requests drain naturally (H-16).
        """
        self._shutting_down.set()
        self._sweeper_stop.set()
        self._log.info(
            "WebhookReceiver.stop() called; new requests will return 503"
        )

    # ------------------------------------------------------------------
    # In-flight sweeper (HIGH #9)
    # ------------------------------------------------------------------

    def _run_sweeper(self) -> None:
        """
        FIX-011: Background daemon that evicts in_flight entries with no
        heartbeat for >60s. Runs every 60s. A lock that has been held for
        400s but continues heartbeating is NOT evicted (active processing).
        A lock with no heartbeat for 60s IS evicted (stalled worker).
        """
        while not self._sweeper_stop.wait(timeout=60.0):
            now_mono = time.monotonic()
            evicted = []
            with self._in_flight_lock:
                for sym, entry in list(self._in_flight.items()):
                    heartbeat_at = entry['heartbeat_at']
                    if now_mono - heartbeat_at > self._in_flight_timeout_sec:
                        evicted.append((sym, entry['acquired_at'], heartbeat_at))
                for sym, _, _ in evicted:
                    del self._in_flight[sym]
            for sym, acquired_at, heartbeat_at in evicted:
                held_sec = now_mono - acquired_at
                stall_sec = now_mono - heartbeat_at
                self._log.critical(
                    "in_flight_sweeper: evicted STALLED symbol %s "
                    "(held=%.0fs, no heartbeat for %.0fs); "
                    "signal_processor worker likely crashed or deadlocked",
                    sym, held_sec, stall_sec,
                )
