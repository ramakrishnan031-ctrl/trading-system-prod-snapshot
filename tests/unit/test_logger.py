"""
tests/unit/test_logger.py

Validates core/logger.py against L1–L10 locked decisions:
  - get_logger returns same instance on repeated calls (L10)
  - setup_logging creates logs/ directory if absent (L8)
  - 4 log files created with today_ist() in filename (L6)
  - system/trades/reconciler files are JSON-lines; debug is plain text (L1)
  - bind_trade attaches signal_id, trade_id, order_id (L2)
  - record with trade IDs lands in trades log AND system log (L3)
  - record from order_reconciler* logger lands in reconciler log (L3)
  - non-reconciler logger record does NOT land in reconciler log (L3)
  - WARNING+ mirrored to stdout (L4)
  - DEBUG NOT mirrored to stdout (L4)
  - INFO NOT mirrored to stdout (L4)
  - log_exception maps TradingSystemError SEVERITY to log level (L7)
  - log_exception non-TradingSystemError uses ERROR level (L7)
  - log_exception context fields appear in JSON (L7)
  - JSON line field order matches L9 schema
  - JSON line has all required fixed fields (L9)
  - duplicate setup_logging() does not double-attach handlers (L10)
  - re-run confirms no interference with other test modules

All tests use tempfile.TemporaryDirectory for file isolation. Stdout tests
redirect sys.stdout before calling setup_logging() so the StreamHandler
captures to the buffer.

NOTE (Windows): _teardown() MUST be called inside the `with TemporaryDirectory`
block — after reading log contents but before the block exits — so all
FileHandlers are closed before Windows attempts to delete the temp directory.

Run: python tests/unit/test_logger.py  (standalone mode)
"""
from __future__ import annotations

import io
import json
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import core.logger as _logger_mod
from core.exceptions import ClockSkewTooLarge, TradingSystemError
from core.logger import (
    TradeContext,
    bind_trade,
    get_logger,
    log_exception,
    setup_logging,
)
from core.time_authority import today_ist


# ─────────────────────────────────────────────────────────────────────────────
# Test helpers
# ─────────────────────────────────────────────────────────────────────────────

def _flush_all() -> None:
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:
            pass


def _teardown() -> None:
    """Remove and close all handlers attached by the most recent setup_logging() call.

    Must be called INSIDE the `with TemporaryDirectory` block on Windows so
    file handles are released before the OS attempts to delete the directory.
    """
    root = logging.getLogger()
    for h in list(_logger_mod._active_handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    _logger_mod._active_handlers.clear()


def _read_lines(path: Path) -> list[str]:
    """Flush, read, return non-empty lines from a log file."""
    _flush_all()
    text = path.read_text(encoding="utf-8")
    return [ln for ln in text.splitlines() if ln.strip()]


def _log_file(log_dir: Path, stem: str) -> Path:
    return log_dir / f"{stem}_{today_ist()}.log"


# ─────────────────────────────────────────────────────────────────────────────
# Tests — get_logger caching (L10)
# ─────────────────────────────────────────────────────────────────────────────

def test_get_logger_returns_same_instance_on_repeated_calls() -> None:
    a = get_logger("test_cache_module")
    b = get_logger("test_cache_module")
    assert a is b, "get_logger must return the identical Logger object on repeated calls"
    print("  OK get_logger returns same instance on repeated calls (L10)")


def test_get_logger_different_names_return_different_instances() -> None:
    a = get_logger("test_cache_alpha")
    b = get_logger("test_cache_beta")
    assert a is not b
    print("  OK Different names return different Logger instances")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — setup_logging creates directory and files (L6, L8)
# ─────────────────────────────────────────────────────────────────────────────

def test_setup_logging_creates_logs_dir_if_absent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "nested" / "logs"
        assert not d.exists()
        setup_logging(log_dir=d)
        exists = d.exists() and d.is_dir()
        _teardown()   # close handlers before TemporaryDirectory.__exit__ deletes tmp

    assert exists
    print("  OK setup_logging creates log directory (including parents) if absent (L8)")


def test_4_log_files_created_with_today_ist_date() -> None:
    date = today_ist()
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        found = {stem: (d / f"{stem}_{date}.log").exists()
                 for stem in ("system", "trades", "reconciler", "debug")}
        _teardown()

    for stem, exists in found.items():
        assert exists, f"Expected {stem}_{date}.log to exist"
    print(f"  OK 4 log files created with date {date} in filename (L6)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — JSON vs plain format (L1)
# ─────────────────────────────────────────────────────────────────────────────

def test_system_file_is_json_lines() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_json_sys")
        log.info("check system json format")
        lines = _read_lines(_log_file(d, "system"))
        _teardown()

    assert lines, "system log must have at least one line"
    data = json.loads(lines[0])   # raises if not valid JSON
    assert "ts" in data and "level" in data and "msg" in data
    print("  OK system log is valid JSON-lines (L1)")


def test_reconciler_file_is_json_lines() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("order_reconciler_test")
        log.info("reconciler json check")
        lines = _read_lines(_log_file(d, "reconciler"))
        _teardown()

    assert lines
    json.loads(lines[0])   # raises if not valid JSON
    print("  OK reconciler log is valid JSON-lines (L1)")


def test_trades_file_is_json_lines() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = bind_trade(get_logger("test_json_trades"), signal_id="SIG1")
        log.info("trades json check")
        lines = _read_lines(_log_file(d, "trades"))
        _teardown()

    assert lines
    json.loads(lines[0])
    print("  OK trades log is valid JSON-lines (L1)")


def test_debug_file_is_plain_text_not_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_plain_debug")
        log.debug("this line should be plain text")
        lines = _read_lines(_log_file(d, "debug"))
        _teardown()

    assert lines
    try:
        json.loads(lines[0])
        assert False, "debug log line should NOT be valid JSON"
    except (json.JSONDecodeError, ValueError):
        pass   # expected — it is plain text
    print("  OK debug log is plain text, not JSON (L1)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — bind_trade (L2)
# ─────────────────────────────────────────────────────────────────────────────

def test_bind_trade_returns_trade_context() -> None:
    log = get_logger("test_bind_type")
    ctx = bind_trade(log, signal_id="S1", trade_id="T1")
    assert isinstance(ctx, TradeContext)
    print("  OK bind_trade returns TradeContext instance (L2)")


def test_bind_trade_attaches_ids_to_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = bind_trade(
            get_logger("test_bind_ids"),
            signal_id="SIG99",
            trade_id="TRD99",
            order_id="ORD99",
        )
        log.info("bound IDs test")
        lines = _read_lines(_log_file(d, "trades"))
        _teardown()

    assert lines, "trades log should have the record with bound IDs"
    data = json.loads(lines[0])
    assert data["signal_id"] == "SIG99"
    assert data["trade_id"]  == "TRD99"
    assert data["order_id"]  == "ORD99"
    print("  OK bind_trade IDs appear in the trades log record (L2)")


def test_bind_trade_skips_none_ids() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = bind_trade(
            get_logger("test_bind_nones"),
            signal_id="SIG1",
            trade_id=None,    # should be omitted
            order_id=None,    # should be omitted
        )
        log.info("partial bind test")
        lines = _read_lines(_log_file(d, "trades"))
        _teardown()

    data = json.loads(lines[0])
    assert data["signal_id"] == "SIG1"
    assert "trade_id" not in data
    assert "order_id" not in data
    print("  OK None IDs not bound by bind_trade (L2)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — routing (L3)
# ─────────────────────────────────────────────────────────────────────────────

def test_record_with_trade_id_lands_in_trades_log() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = bind_trade(get_logger("test_route_trades"), trade_id="T42")
        log.info("trades routing check")
        trades_lines = _read_lines(_log_file(d, "trades"))
        _teardown()

    assert trades_lines, "Record with trade_id must appear in trades log"
    data = json.loads(trades_lines[0])
    assert data["trade_id"] == "T42"
    print("  OK Record with trade_id lands in trades log (L3)")


def test_record_with_trade_id_also_lands_in_system_log() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = bind_trade(get_logger("test_route_both"), signal_id="S77")
        log.info("multi-file routing check")
        system_lines = _read_lines(_log_file(d, "system"))
        trades_lines = _read_lines(_log_file(d, "trades"))
        _teardown()

    assert system_lines, "Record must also land in system log"
    assert trades_lines, "Record must land in trades log"
    sys_msgs = [json.loads(l)["msg"] for l in system_lines]
    assert "multi-file routing check" in sys_msgs
    print("  OK Record with signal_id lands in BOTH trades and system logs (L3)")


def test_record_without_trade_ids_not_in_trades_log() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_route_no_ids")
        log.info("no trade IDs here")
        trades_lines = _read_lines(_log_file(d, "trades"))
        _teardown()

    msgs = [json.loads(l)["msg"] for l in trades_lines]
    assert "no trade IDs here" not in msgs
    print("  OK Record without trade IDs does NOT appear in trades log (L3)")


def test_reconciler_logger_lands_in_reconciler_log() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("order_reconciler")
        log.info("reconciler routing check")
        rec_lines = _read_lines(_log_file(d, "reconciler"))
        _teardown()

    assert rec_lines, "order_reconciler logger must route to reconciler log"
    data = json.loads(rec_lines[0])
    assert data["logger"] == "order_reconciler"
    print("  OK order_reconciler logger routes to reconciler log (L3)")


def test_order_monitor_logger_lands_in_reconciler_log() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("order_monitor")
        log.info("monitor in reconciler log")
        rec_lines = _read_lines(_log_file(d, "reconciler"))
        _teardown()

    assert rec_lines
    data = json.loads(rec_lines[0])
    assert data["logger"] == "order_monitor"
    print("  OK order_monitor logger routes to reconciler log (L3)")


def test_non_reconciler_logger_not_in_reconciler_log() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("capital_fund_manager")
        log.info("not a reconciler message")
        rec_lines = _read_lines(_log_file(d, "reconciler"))
        _teardown()

    msgs = [json.loads(l)["msg"] for l in rec_lines]
    assert "not a reconciler message" not in msgs
    print("  OK Non-reconciler logger does NOT appear in reconciler log (L3)")


def test_debug_record_lands_in_debug_log() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_debug_route")
        log.debug("debug only message")
        debug_lines = _read_lines(_log_file(d, "debug"))
        system_lines = _read_lines(_log_file(d, "system"))
        _teardown()

    assert any("debug only message" in l for l in debug_lines), \
        "DEBUG record must appear in debug log"
    assert not any("debug only message" in l for l in system_lines), \
        "DEBUG record must NOT appear in system log (INFO+ only)"
    print("  OK DEBUG record lands in debug log but not system log (L3)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — stdout routing (L4)
# ─────────────────────────────────────────────────────────────────────────────

def test_warning_mirrored_to_stdout() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        buf = io.StringIO()
        sys.stdout = buf
        try:
            setup_logging(log_dir=d)
            log = get_logger("test_stdout_warn")
            log.warning("this warning goes to stdout")
            _flush_all()
            output = buf.getvalue()
        finally:
            sys.stdout = sys.__stdout__
        _teardown()

    assert "this warning goes to stdout" in output
    print("  OK WARNING mirrored to stdout (L4)")


def test_info_not_mirrored_to_stdout() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        buf = io.StringIO()
        sys.stdout = buf
        try:
            setup_logging(log_dir=d)
            log = get_logger("test_stdout_info")
            log.info("this info should NOT appear in stdout")
            _flush_all()
            output = buf.getvalue()
        finally:
            sys.stdout = sys.__stdout__
        _teardown()

    assert "this info should NOT appear in stdout" not in output
    print("  OK INFO not mirrored to stdout (L4)")


def test_debug_not_mirrored_to_stdout() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        buf = io.StringIO()
        sys.stdout = buf
        try:
            setup_logging(log_dir=d)
            log = get_logger("test_stdout_debug")
            log.debug("this debug should NOT appear in stdout")
            _flush_all()
            output = buf.getvalue()
        finally:
            sys.stdout = sys.__stdout__
        _teardown()

    assert "this debug should NOT appear in stdout" not in output
    print("  OK DEBUG not mirrored to stdout (L4)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — log_exception (L7)
# ─────────────────────────────────────────────────────────────────────────────

def test_log_exception_warn_severity_logs_at_warning() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_exc_warn")
        try:
            raise ClockSkewTooLarge("skew", skew_seconds=5.0)
        except ClockSkewTooLarge as exc:
            log_exception(log, exc)
        lines = _read_lines(_log_file(d, "system"))
        _teardown()

    exc_lines = [json.loads(l) for l in lines if "skew" in l]
    assert exc_lines, "Exception must appear in system log"
    # ClockSkewTooLarge.SEVERITY = "CRITICAL"
    assert exc_lines[0]["level"] == "CRITICAL"
    print("  OK ClockSkewTooLarge (SEVERITY=CRITICAL) logged at CRITICAL level (L7)")


def test_log_exception_error_severity_uses_error_level() -> None:
    from core.exceptions import OrderRejectedError
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_exc_error")
        try:
            raise OrderRejectedError("bad order", reason="margin")
        except OrderRejectedError as exc:
            log_exception(log, exc)
        lines = _read_lines(_log_file(d, "system"))
        _teardown()

    exc_lines = [json.loads(l) for l in lines if "bad order" in l]
    assert exc_lines
    assert exc_lines[0]["level"] == "ERROR"
    print("  OK OrderRejectedError (SEVERITY=ERROR) logged at ERROR level (L7)")


def test_log_exception_non_trading_system_error_uses_error_level() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_exc_vanilla")
        try:
            raise ValueError("plain python error")
        except ValueError as exc:
            log_exception(log, exc)
        lines = _read_lines(_log_file(d, "system"))
        _teardown()

    exc_lines = [json.loads(l) for l in lines if "plain python error" in l]
    assert exc_lines, "Non-TradingSystemError must appear in system log"
    assert exc_lines[0]["level"] == "ERROR"
    print("  OK Non-TradingSystemError logged at ERROR level (L7)")


def test_log_exception_context_fields_in_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_exc_ctx")
        try:
            raise ClockSkewTooLarge(
                "big skew",
                skew_seconds=45.0,
                threshold_sec=30.0,
            )
        except ClockSkewTooLarge as exc:
            log_exception(log, exc)
        lines = _read_lines(_log_file(d, "system"))
        _teardown()

    exc_lines = [json.loads(l) for l in lines if "big skew" in l]
    assert exc_lines
    data = exc_lines[0]
    assert data.get("skew_seconds") == 45.0, f"skew_seconds missing: {data}"
    assert data.get("threshold_sec") == 30.0
    assert "exc_type" in data
    assert "exc_traceback" in data
    print("  OK log_exception: context fields and exc_type/traceback in JSON (L7)")


def test_log_exception_non_tse_has_empty_context_in_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_exc_no_ctx")
        try:
            raise RuntimeError("runtime boom")
        except RuntimeError as exc:
            log_exception(log, exc)
        lines = _read_lines(_log_file(d, "system"))
        _teardown()

    exc_lines = [json.loads(l) for l in lines if "runtime boom" in l]
    assert exc_lines
    data = exc_lines[0]
    assert "exc_type" in data
    assert data["exc_type"] == "RuntimeError"
    # No extra context keys beyond the fixed L9 fields
    extra_keys = set(data.keys()) - {"ts", "level", "logger", "msg", "exc_type", "exc_traceback"}
    assert not extra_keys, f"Unexpected context keys for non-TSE: {extra_keys}"
    print("  OK Non-TradingSystemError has no extra context keys in JSON (L7)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — JSON field order (L9)
# ─────────────────────────────────────────────────────────────────────────────

def test_json_line_has_required_fixed_fields() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_json_fields")
        log.info("fixed fields test")
        lines = _read_lines(_log_file(d, "system"))
        _teardown()

    data = json.loads(lines[0])
    for field in ("ts", "level", "logger", "msg"):
        assert field in data, f"Required field {field!r} missing from JSON line"
    assert data["level"] == "INFO"
    assert data["logger"] == "test_json_fields"
    assert data["msg"] == "fixed fields test"
    print("  OK JSON line has all required fixed fields: ts, level, logger, msg (L9)")


def test_json_line_field_order_matches_l9_schema() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_json_order")
        log.info(
            "order test",
            extra={
                "signal_id": "S1",
                "trade_id": "T1",
                "symbol": "RELIANCE",
                "custom_z": "last",
                "custom_a": "first_alpha",
            },
        )
        lines = _read_lines(_log_file(d, "system"))
        _teardown()

    data = json.loads(lines[0])
    keys = list(data.keys())

    # Fixed prefix must be in position
    assert keys[0] == "ts",     f"keys[0] must be 'ts', got {keys[0]!r}"
    assert keys[1] == "level",  f"keys[1] must be 'level', got {keys[1]!r}"
    assert keys[2] == "logger", f"keys[2] must be 'logger', got {keys[2]!r}"
    assert keys[3] == "msg",    f"keys[3] must be 'msg', got {keys[3]!r}"

    # Priority extra keys follow (signal_id, trade_id, order_id omitted, symbol)
    assert keys[4] == "signal_id", f"keys[4] must be 'signal_id', got {keys[4]!r}"
    assert keys[5] == "trade_id",  f"keys[5] must be 'trade_id', got {keys[5]!r}"
    assert keys[6] == "symbol",    f"keys[6] must be 'symbol', got {keys[6]!r}"

    # Remaining extra: alphabetical (custom_a before custom_z)
    remaining = keys[7:]
    assert "custom_a" in remaining
    assert "custom_z" in remaining
    assert remaining.index("custom_a") < remaining.index("custom_z"), \
        "Extra fields must be alphabetically ordered (L9)"

    print("  OK JSON field order matches L9 schema (L9)")


def test_json_priority_extra_keys_omitted_when_absent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_json_omit")
        log.info("no trade IDs")
        lines = _read_lines(_log_file(d, "system"))
        _teardown()

    data = json.loads(lines[0])
    for key in ("signal_id", "trade_id", "order_id", "symbol"):
        assert key not in data, f"Absent optional field {key!r} must be omitted from JSON"
    print("  OK Absent optional fields (signal_id, trade_id, etc.) omitted from JSON (L9)")


def test_json_ts_is_ist_iso8601_with_offset() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        log = get_logger("test_json_ts")
        log.info("ts format check")
        lines = _read_lines(_log_file(d, "system"))
        _teardown()

    data = json.loads(lines[0])
    ts = data["ts"]
    # Must contain IST offset +05:30
    assert "+05:30" in ts, f"ts must contain IST offset +05:30, got {ts!r}"
    # Must contain milliseconds (.NNN)
    assert "." in ts, f"ts must contain milliseconds, got {ts!r}"
    print(f"  OK ts is IST ISO-8601 with milliseconds: {ts!r} (L9)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — duplicate handler guard (L10)
# ─────────────────────────────────────────────────────────────────────────────

def test_duplicate_setup_logging_does_not_double_attach_handlers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)

        # First setup + first log message
        setup_logging(log_dir=d)
        log = get_logger("test_dup_guard")
        log.info("message one")
        _flush_all()

        # Second setup (same dir, same date → same filenames) + second message
        setup_logging(log_dir=d)
        log = get_logger("test_dup_guard")
        log.info("message two")
        lines = _read_lines(_log_file(d, "system"))
        _teardown()

    # Exactly 2 messages, not 3 or 4 (which would indicate duplicate handlers)
    assert len(lines) == 2, (
        f"Expected exactly 2 lines in system log, got {len(lines)}: {lines}"
    )
    msgs = [json.loads(l)["msg"] for l in lines]
    assert "message one" in msgs
    assert "message two" in msgs
    print("  OK Duplicate setup_logging() does not double-attach handlers (L10)")


def test_active_handlers_list_matches_root_handlers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        setup_logging(log_dir=d)
        root_handlers = logging.getLogger().handlers
        all_present = all(h in root_handlers for h in _logger_mod._active_handlers)
        _teardown()

    assert all_present, "All active handlers must be on root logger"
    print("  OK _active_handlers matches root logger handlers after setup (L10)")


def test_fix058_safe_json_encoder_handles_datetime() -> None:
    """FIX-058: SafeJSONEncoder converts datetime to ISO format."""
    from datetime import datetime
    from core.logger import SafeJSONEncoder

    dt = datetime(2026, 5, 15, 14, 30, 45, 123456)
    result = json.dumps({"ts": dt}, cls=SafeJSONEncoder)
    parsed = json.loads(result)

    assert "2026-05-15T14:30:45.123456" in parsed["ts"], f"datetime not ISO formatted: {parsed['ts']}"
    print("  OK SafeJSONEncoder handles datetime objects")


def test_fix058_safe_json_encoder_handles_exception() -> None:
    """FIX-058: SafeJSONEncoder converts Exception to string."""
    from core.logger import SafeJSONEncoder

    exc = ValueError("test error")
    result = json.dumps({"error": exc}, cls=SafeJSONEncoder)
    parsed = json.loads(result)

    assert parsed["error"] == "test error", f"Exception not stringified: {parsed['error']}"
    print("  OK SafeJSONEncoder handles Exception objects")


def test_fix058_safe_json_encoder_handles_unserializable() -> None:
    """FIX-058: SafeJSONEncoder handles arbitrary unserializable objects."""
    from core.logger import SafeJSONEncoder

    class CustomObj:
        pass

    obj = CustomObj()
    result = json.dumps({"obj": obj}, cls=SafeJSONEncoder)
    parsed = json.loads(result)

    assert "<unserializable:CustomObj>" == parsed["obj"], f"Unserializable not tagged: {parsed['obj']}"
    print("  OK SafeJSONEncoder handles unserializable objects")


def test_fix058_safe_json_encoder_complex_nested() -> None:
    """FIX-058: SafeJSONEncoder handles complex nested dict with all edge cases."""
    from datetime import datetime
    from core.logger import SafeJSONEncoder

    class Weird:
        pass

    data = {
        "ts": datetime(2026, 5, 15, 9, 15, 0),
        "error": RuntimeError("boom"),
        "weird": Weird(),
        "normal": "ok",
        "nested": {
            "inner_dt": datetime(2026, 5, 15, 15, 30, 0),
            "value": 42,
        },
    }

    result = json.dumps(data, cls=SafeJSONEncoder)
    parsed = json.loads(result)

    assert "2026-05-15T09:15:00" in parsed["ts"]
    assert parsed["error"] == "boom"
    assert "<unserializable:Weird>" == parsed["weird"]
    assert parsed["normal"] == "ok"
    assert "2026-05-15T15:30:00" in parsed["nested"]["inner_dt"]
    assert parsed["nested"]["value"] == 42
    print("  OK SafeJSONEncoder handles complex nested structures")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_get_logger_returns_same_instance_on_repeated_calls,
        test_get_logger_different_names_return_different_instances,
        test_setup_logging_creates_logs_dir_if_absent,
        test_4_log_files_created_with_today_ist_date,
        test_system_file_is_json_lines,
        test_reconciler_file_is_json_lines,
        test_trades_file_is_json_lines,
        test_debug_file_is_plain_text_not_json,
        test_bind_trade_returns_trade_context,
        test_bind_trade_attaches_ids_to_records,
        test_bind_trade_skips_none_ids,
        test_record_with_trade_id_lands_in_trades_log,
        test_record_with_trade_id_also_lands_in_system_log,
        test_record_without_trade_ids_not_in_trades_log,
        test_reconciler_logger_lands_in_reconciler_log,
        test_order_monitor_logger_lands_in_reconciler_log,
        test_non_reconciler_logger_not_in_reconciler_log,
        test_debug_record_lands_in_debug_log,
        test_warning_mirrored_to_stdout,
        test_info_not_mirrored_to_stdout,
        test_debug_not_mirrored_to_stdout,
        test_log_exception_warn_severity_logs_at_warning,
        test_log_exception_error_severity_uses_error_level,
        test_log_exception_non_trading_system_error_uses_error_level,
        test_log_exception_context_fields_in_json,
        test_log_exception_non_tse_has_empty_context_in_json,
        test_json_line_has_required_fixed_fields,
        test_json_line_field_order_matches_l9_schema,
        test_json_priority_extra_keys_omitted_when_absent,
        test_json_ts_is_ist_iso8601_with_offset,
        test_duplicate_setup_logging_does_not_double_attach_handlers,
        test_active_handlers_list_matches_root_handlers,
        test_fix058_safe_json_encoder_handles_datetime,
        test_fix058_safe_json_encoder_handles_exception,
        test_fix058_safe_json_encoder_handles_unserializable,
        test_fix058_safe_json_encoder_complex_nested,
    ]

    print("=" * 70)
    print("logger.py — Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as e:
            failed.append((test.__name__, f"AssertionError: {e}"))
            print(f"  FAIL: {e}")
        except Exception as e:
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR: {type(e).__name__}: {e}")
        finally:
            # Best-effort cleanup between tests to avoid handler leakage
            try:
                _teardown()
            except Exception:
                pass

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1

    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
