"""
core/logger.py — Trading System v2

Purpose:
    Single structured logging facade for the entire system.
    Every module calls get_logger(__name__) and optionally bind_trade().
    main.py calls setup_logging() once at startup before anything else logs.

Locked Design Decisions:
    L1  — Four daily log files: system/trades/reconciler use JSON-lines;
           debug uses plain human-readable text.
    L2  — get_logger(name) → Logger. bind_trade(log, *, signal_id, trade_id,
           order_id) → TradeContext (LoggerAdapter). Callers prefer the wrapper.
    L3  — Routing by filter: reconciler log = order_reconciler*/order_monitor*;
           trades log = records with signal_id or trade_id or order_id;
           system log = INFO+ catch-all; debug log = all DEBUG+.
           One record can land in multiple files (intended).
    L4  — WARNING+ mirrored to stdout always. No config flag.
    L5  — get_logger(name) returns stdlib logging.Logger. No handlers attached here.
    L6  — Fresh files per setup_logging() call, append mode, filenames from today_ist().
    L7  — log_exception(log, exc): reads exc.SEVERITY for TradingSystemError;
           ERROR level for all others. Always includes exc_info and exc.context.
    L8  — setup_logging() creates the log directory (mkdir parents=True, exist_ok=True).
    L9  — JSON line field order: ts, level, logger, msg, signal_id?, trade_id?,
           order_id?, symbol?, <extra alphabetical>, exc_type?, exc_traceback?.
    L10 — get_logger caches by name (module-level dict). setup_logging() removes
           previous handlers before attaching new ones. Prevents duplicate-handler bug.
    L11 — Third-party HTTP client loggers are capped at INFO by setup_logging().
           Their DEBUG output contains full request URLs, which carry credentials in
           the path (see _THIRD_PARTY_HTTP_LOGGERS). Our own loggers are unaffected.

What This Module Does NOT Do:
    - Does not send alerts (handled by alerts/telegram_notifier.py)
    - Does not write to the SQLite state_store
    - Does not use third-party logging libraries (stdlib only, L5)
    - Does not hot-reload config; setup_logging() is called once at startup
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import copy
import queue
import sys
from datetime import datetime
from pathlib import Path

from core.exceptions import TradingSystemError
from core.time_authority import ist_timezone, today_ist


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Standard LogRecord attributes to exclude when extracting user-supplied extra fields.
# Verified against Python 3.14 LogRecord.__init__ + Formatter.format().
_STDLIB_ATTRS: frozenset[str] = frozenset({
    "args", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "taskName", "thread", "threadName",
    # Added by Formatter.format() before formatMessage():
    "message", "asctime",
})

# Extra keys that appear first in JSON output, in this fixed order (L9).
_PRIORITY_EXTRA: tuple[str, ...] = ("signal_id", "trade_id", "order_id", "symbol")

# SEVERITY string → stdlib log level (L7).
_SEVERITY_TO_LEVEL: dict[str, int] = {
    "INFO":     logging.INFO,
    "WARN":     logging.WARNING,
    "ERROR":    logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_PLAIN_FMT = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"

# Third-party HTTP client loggers, capped at INFO by setup_logging() (L11).
#
# WHY: the debug sink is DEBUG+/all-loggers (L3) and setup_logging() sets root to DEBUG,
# which opts us into the wire-level DEBUG output of every third-party library — output we
# never asked for. urllib3 (the transport under `requests`) logs the request line of every
# call it makes, path included:
#     urllib3.connectionpool DEBUG https://api.telegram.org:443 "POST /bot<TOKEN>/... " 200
# Telegram embeds the bot token in the URL PATH, so ANY HTTP debug logging writes a live
# credential to disk in cleartext by construction. urllib3 leaks the same URL from three
# call sites (request line, redirect, retry), so capping the emitter beats chasing formats.
#
# INFO (not WARNING) keeps their genuinely useful INFO+ records (retries, connection
# warnings). Our own loggers are module-named ("core.*", "alerts.*", …) and never match
# these names, so no application DEBUG output is suppressed.
#
# Libraries not currently installed are listed anyway: getLogger() on an absent module is
# harmless and pre-arms the cap if the dependency is ever added.
_THIRD_PARTY_HTTP_LOGGERS: tuple[str, ...] = (
    "urllib3",                      # transport under `requests` — the observed leaker
    "requests",
    "requests.packages.urllib3",    # legacy vendored alias
    "httpx",
    "httpcore",
)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level state (L10)
# ─────────────────────────────────────────────────────────────────────────────

_loggers: dict[str, logging.Logger] = {}
_active_handlers: list[logging.Handler] = []
_queue_listener: logging.handlers.QueueListener | None = None  # FIX-099


# ─────────────────────────────────────────────────────────────────────────────
# JSON Encoder (FIX-058: never drop a log record)
# ─────────────────────────────────────────────────────────────────────────────

class SafeJSONEncoder(json.JSONEncoder):
    """
    FIX-058: JSON encoder that never raises on unserializable objects.
    FIX-084: Converts NaN/Inf to None (JSON null) to prevent invalid JSON output.
    Prevents log record drops when extra fields contain datetime, Exception, or
    other non-JSON-serializable types.
    """
    def encode(self, o):
        import math
        # FIX-084: Preprocess to replace NaN/Inf with None
        if isinstance(o, float):
            if math.isnan(o) or math.isinf(o):
                return 'null'
        return super().encode(self._scrub_nan_inf(o))

    def _scrub_nan_inf(self, obj):
        """Recursively replace NaN/Inf with None in data structures."""
        import math
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
        elif isinstance(obj, dict):
            return {k: self._scrub_nan_inf(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._scrub_nan_inf(item) for item in obj]
        return obj

    def default(self, obj):
        from datetime import date
        from decimal import Decimal

        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):  # FIX-104: date support for secondary_screener
            return obj.isoformat()
        if isinstance(obj, Decimal):  # FIX-104: Decimal support for market data
            return float(obj)
        if isinstance(obj, Exception):
            return str(obj)
        # FIX-104: numpy type support for market data serialization
        if hasattr(obj, 'item'):  # numpy scalar
            val = obj.item()
            if isinstance(val, float):
                import math
                if math.isnan(val) or math.isinf(val):
                    return None
            return val
        if hasattr(obj, 'tolist'):  # numpy array
            return obj.tolist()
        try:
            return super().default(obj)
        except TypeError:
            return f"<unserializable:{type(obj).__name__}>"


# ─────────────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """
    Formats log records as compact JSON lines per the L9 field schema.
    Fields are emitted in the order specified by L9; absent optional fields
    are omitted entirely (no null values).
    """

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()

        data: dict[str, object] = {}

        # ── Fixed fields (always present, L9 order) ──────────────────────────
        ts_dt = datetime.fromtimestamp(record.created, tz=ist_timezone())
        data["ts"]     = ts_dt.isoformat(timespec="milliseconds")
        data["level"]  = record.levelname
        data["logger"] = record.name
        data["msg"]    = record.message

        # ── User-supplied extra fields ────────────────────────────────────────
        extra: dict[str, object] = {
            k: v for k, v in record.__dict__.items()
            if k not in _STDLIB_ATTRS
        }

        # Priority keys first, in L9 order, only when present
        for key in _PRIORITY_EXTRA:
            if key in extra:
                data[key] = extra.pop(key)

        # Remaining extra fields alphabetically
        for key in sorted(extra):
            data[key] = extra[key]

        # ── Exception fields (last, only when present, L9) ───────────────────
        if record.exc_info and record.exc_info[0] is not None:
            data["exc_type"]      = record.exc_info[0].__name__
            data["exc_traceback"] = self.formatException(record.exc_info)

        # FIX-058: SafeJSONEncoder prevents log record drops
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), cls=SafeJSONEncoder)


class _PlainFormatter(logging.Formatter):
    """
    Human-readable format for the debug log (L1).
    Timestamps use IST (G4, L6).
    """

    def __init__(self) -> None:
        super().__init__(fmt=_PLAIN_FMT)

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=ist_timezone())
        return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ─────────────────────────────────────────────────────────────────────────────
# Filters (L3)
# ─────────────────────────────────────────────────────────────────────────────

class _SystemFilter(logging.Filter):
    """Pass INFO+ records to the system log (catch-all)."""
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.INFO


class _TradesFilter(logging.Filter):
    """Pass records that carry at least one non-None trade ID."""
    def filter(self, record: logging.LogRecord) -> bool:
        return (
            getattr(record, "signal_id", None) is not None
            or getattr(record, "trade_id", None) is not None
            or getattr(record, "order_id", None) is not None
        )


class _ReconcilerFilter(logging.Filter):
    """Pass records from order_reconciler* or order_monitor* loggers."""
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(("order_reconciler", "order_monitor"))


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_logger(name: str) -> logging.Logger:
    """
    Return a Logger with the given name (L5, L10).
    Cached — same instance on repeated calls with the same name.
    Handlers are NOT attached here; call setup_logging() at startup first.
    """
    if name not in _loggers:
        _loggers[name] = logging.getLogger(name)
    return _loggers[name]


class TradeContext(logging.LoggerAdapter):
    """
    Logger wrapper with trade IDs bound as persistent extra fields (L2).
    Created by bind_trade(); not instantiated directly.
    """

    def process(self, msg: object, kwargs: dict) -> tuple[object, dict]:
        extra = dict(self.extra)
        extra.update(kwargs.get("extra") or {})
        kwargs["extra"] = extra
        return msg, kwargs


def bind_trade(
    log: logging.Logger,
    *,
    signal_id: str | None = None,
    trade_id: str | None = None,
    order_id: str | None = None,
) -> TradeContext:
    """
    Wrap log with bound trade IDs (L2). Only non-None IDs are bound.
    All subsequent calls on the returned TradeContext automatically include
    the bound IDs as extra fields, routing the record to the trades log (L3).

    Example::
        log = bind_trade(get_logger(__name__), signal_id="S1", trade_id="T1")
        log.info("order placed", extra={"order_id": "O1"})
    """
    context: dict[str, str] = {}
    if signal_id is not None:
        context["signal_id"] = signal_id
    if trade_id is not None:
        context["trade_id"] = trade_id
    if order_id is not None:
        context["order_id"] = order_id
    return TradeContext(log, context)


def log_exception(log: logging.Logger, exc: BaseException) -> None:
    """
    Log an exception at the level appropriate for its severity (L7).

    For TradingSystemError: uses exc.SEVERITY to determine log level and
    includes exc.context as structured extra fields.
    For any other exception: uses ERROR level with empty context.

    Always attaches the traceback via exc_info.
    """
    if isinstance(exc, TradingSystemError):
        level   = _SEVERITY_TO_LEVEL.get(exc.SEVERITY, logging.ERROR)
        context = dict(exc.context)
    else:
        level   = logging.ERROR
        context = {}

    log.log(
        level,
        str(exc),
        exc_info=(type(exc), exc, exc.__traceback__),
        extra=context or None,
    )


def cap_third_party_http_loggers() -> None:
    """
    Cap third-party HTTP client loggers at INFO so their DEBUG request lines — which
    carry credentials in the URL path — never reach the debug sink (L11).

    Called by setup_logging(). Exposed for entry points that configure logging
    themselves (e.g. logging.basicConfig) instead of going through setup_logging().
    Idempotent.
    """
    for name in _THIRD_PARTY_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)


def setup_logging(log_dir: Path = Path("logs")) -> None:
    """
    Configure the root logger with four file handlers and one stdout handler (L1–L10).

    FIX-099: File handlers use async QueueHandler to prevent disk-full thread freeze.

    Must be called once at startup by main.py before any module calls get_logger().
    Safe to call again (e.g., in tests): removes previously attached handlers first (L10).

    Args:
        log_dir: directory for log files. Created if absent (L8).
                 Default: Path("logs") relative to cwd (repo root).
    """
    global _active_handlers, _queue_listener

    # FIX-099: Stop previous queue listener if exists
    if _queue_listener is not None:
        _queue_listener.stop()
        _queue_listener = None

    # Remove handlers from any previous call to setup_logging() (L10).
    root = logging.getLogger()
    for handler in _active_handlers:
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    _active_handlers = []

    # Create log directory (L8).
    log_dir.mkdir(parents=True, exist_ok=True)
    date_str = today_ist()   # "YYYY-MM-DD" in IST (L6, G4)

    json_fmt  = _JsonFormatter()
    plain_fmt = _PlainFormatter()

    def _file(stem: str) -> logging.FileHandler:
        # One file per day, no mid-day rotation (Foundation Rule 1.7: resume-safe)
        # Date is embedded in filename; file is appended across restarts
        path = log_dir / f"{stem}_{date_str}.log"
        h = logging.FileHandler(path, mode="a", encoding="utf-8")
        h.setLevel(logging.DEBUG)   # level controlled by filters, not handler
        return h

    # ── system log: INFO+, JSON, all loggers ─────────────────────────────────
    h_system = _file("system")
    h_system.setFormatter(json_fmt)
    h_system.addFilter(_SystemFilter())

    # ── trades log: records with trade IDs, JSON ──────────────────────────────
    h_trades = _file("trades")
    h_trades.setFormatter(json_fmt)
    h_trades.addFilter(_TradesFilter())

    # ── reconciler log: order_reconciler*/order_monitor* loggers, JSON ────────
    h_reconciler = _file("reconciler")
    h_reconciler.setFormatter(json_fmt)
    h_reconciler.addFilter(_ReconcilerFilter())

    # ── debug log: DEBUG+, all loggers, plain text ────────────────────────────
    h_debug = _file("debug")
    h_debug.setFormatter(plain_fmt)

    # ── stdout: WARNING+, plain text (L4) ─────────────────────────────────────
    h_stdout = logging.StreamHandler(sys.stdout)
    h_stdout.setLevel(logging.WARNING)
    h_stdout.setFormatter(plain_fmt)

    # FIX-099: Async file logging via QueueHandler to prevent disk-full freeze
    # File handlers go through queue; stdout remains synchronous for immediate visibility
    #
    # _PassthroughQueueHandler overrides prepare() to pass a shallow copy of the
    # record unchanged. The default QueueHandler.prepare() pre-formats the record
    # and clears exc_info/exc_text, which prevents file-handler _JsonFormatter from
    # adding exc_type/exc_traceback fields. Thread-based QueueListener doesn't need
    # pickling, so we can skip the pre-format safely.
    class _PassthroughQueueHandler(logging.handlers.QueueHandler):
        def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
            return copy.copy(record)

    log_queue = queue.Queue(maxsize=10000)
    queue_handler = _PassthroughQueueHandler(log_queue)
    queue_handler.setLevel(logging.DEBUG)

    # QueueListener processes file handlers in background thread
    # respect_handler_level=True ensures each handler's filter + level are honored
    file_handlers = [h_system, h_trades, h_reconciler, h_debug]
    _queue_listener = logging.handlers.QueueListener(
        log_queue, *file_handlers, respect_handler_level=True
    )
    _queue_listener.start()

    # Root logger gets QueueHandler (for files) + stdout (synchronous)
    new_handlers = [queue_handler, h_stdout]

    root.setLevel(logging.DEBUG)
    for h in new_handlers:
        root.addHandler(h)

    # L11: root is now DEBUG, which would otherwise pull third-party HTTP wire logs
    # (and the credentials in their URLs) into the debug sink. Cap them at INFO.
    cap_third_party_http_loggers()

    _active_handlers = new_handlers


def shutdown_logging() -> None:
    """
    Stop the async logging queue listener and flush pending records (FIX-099).

    Called during shutdown to ensure all queued log records are written to disk
    before the process exits. Safe to call multiple times (idempotent).
    """
    global _queue_listener

    if _queue_listener is not None:
        _queue_listener.stop()  # Blocks until queue is empty
        _queue_listener = None
