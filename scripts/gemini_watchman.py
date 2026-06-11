"""
scripts/gemini_watchman.py -- Trading System v2  FIX-153

Purpose:
    Live log watchman using agy CLI. Tails today's system log during
    market hours (09:15-16:30 IST), batches WARNING+ entries every 5 minutes,
    pipes them to agy CLI for real-time analysis.

    Also maintains a live flow trace (reports/flow_trace/trace_YYYY-MM-DD.md)
    capturing signal/order/trade pipeline events in real-time without API calls.
    At EOD, the trace is summarised by agy in a single call.

Usage:
    python scripts/gemini_watchman.py [--date YYYY-MM-DD] [--interval 300]
                                      [--threshold 20] [--dry-run]

Systemd:
    trading-watchman.service (BindsTo=trading-system.service)

Exit codes:
    0 -- normal exit (outside market hours or clean shutdown)
    1 -- fatal error
"""
from __future__ import annotations

import argparse
import re
import signal
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from core.logger import get_logger
from core.time_authority import now_ist, today_ist

_MARKET_OPEN = dtime(9, 15)
_MARKET_CLOSE = dtime(16, 30)

_WARNING_RE = re.compile(r"\b(WARNING|ERROR|CRITICAL)\b")

# Flow trace: pipeline events written raw (no API call during market hours)
_FLOW_KEYWORDS = ("SIGNAL", "SCREEN", "ORDER", "FILL", "PROTECTION",
                  "CLOSE", "CAPITAL", "SL_TRAIL")
_FLOW_RE = re.compile(r"\b(" + "|".join(_FLOW_KEYWORDS) + r")\b")

_WATCHMAN_PROMPT = """\
You are a trading system watchman. Observe ONLY — do not suggest fixes or code changes.

For each issue found, format strictly as:
[HH:MM:SS] [SEVERITY] [SYMBOL] — what happened (one line)

Severity levels:
- CRITICAL: capital risk, kill switch, broker failure, naked position
- WARN: order rejection, slippage > 1%, partial fill, dedup miss
- INFO: unusual but not actionable (latency spike, retry succeeded)

Rules:
1. Always quote exact timestamp from log
2. Always include symbol if mentioned
3. One line per issue — no paragraphs
4. If nothing found: write 'All clear'
5. NEVER suggest code changes, NEVER recommend trade actions
6. Maximum 15 issues per batch — pick the most important

Output only the issue lines or 'All clear'. No preamble, no summary."""

_FLOW_SUMMARY_PROMPT = """\
You are a trading system analyst reviewing today's live execution flow trace.
Attached is the complete pipeline event log for today's session.

Write a 10-line EOD summary covering:
1. Total signals received / screener pass rate
2. Orders placed vs fills received — any gaps?
3. SL/TGT placement — any anomalies?
4. Capital utilisation pattern
5. Any sequence that consistently preceded losses?
6. Flow gaps (expected events missing between stages)
7. Overall execution quality grade (A/B/C/D/F)

Be specific with symbols and timestamps.
No code suggestions. Observation and analysis only."""

from scripts.agy_runner import run_watchman as _agy_watchman

_shutdown = False


def _signal_handler(signum, frame):
    global _shutdown
    _shutdown = True


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="gemini_watchman",
        description="FIX-153: Live log watchman via agy CLI.",
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--log-dir", metavar="PATH", default=None)
    parser.add_argument("--output-dir", metavar="PATH", default=None)
    parser.add_argument("--flow-trace-dir", metavar="PATH", default=None)
    parser.add_argument("--interval", type=int, default=300,
                        help="Seconds between batch checks (default: 300)")
    parser.add_argument("--threshold", type=int, default=20,
                        help="Force early batch at this many WARNING+ lines (default: 20)")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _is_market_hours() -> bool:
    now = now_ist().time()
    return _MARKET_OPEN <= now <= _MARKET_CLOSE


def _find_log_file(log_dir: Path, date_iso: str) -> Path | None:
    candidates = [
        log_dir / f"system_{date_iso}.log",
        log_dir / f"trading_{date_iso}.log",
        log_dir / f"trading-system_{date_iso}.log",
    ]
    for p in candidates:
        if p.exists():
            return p
    matched = sorted(log_dir.glob(f"*{date_iso}*"))
    return matched[0] if matched else None


def _tail_new_lines(
    log_path: Path, last_pos: int
) -> tuple[list[str], list[str], int]:
    """Read new lines since last_pos. Returns (warning_lines, flow_lines, new_pos)."""
    warning_lines: list[str] = []
    flow_lines: list[str] = []
    try:
        size = log_path.stat().st_size
        if size < last_pos:
            last_pos = 0
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(last_pos)
            for line in f:
                stripped = line.rstrip()
                if _WARNING_RE.search(stripped):
                    warning_lines.append(stripped)
                if _FLOW_RE.search(stripped):
                    flow_lines.append(stripped)
            new_pos = f.tell()
    except OSError:
        return [], [], last_pos
    return warning_lines, flow_lines, new_pos


def _call_agy(prompt: str, data: str, log) -> str | None:
    """Pipe data to agy CLI with cascade model selection."""
    result = _agy_watchman(prompt, input_data=data)
    if result is None:
        log.warning("gemini_watchman: all models unavailable — watchman skipped")
    return result


def _append_to_watchman_log(output_dir: Path, date_iso: str, timestamp: str,
                            response: str, line_count: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"watchman_{date_iso}.md"
    entry = (
        f"\n---\n### {timestamp} ({line_count} entries)\n\n"
        f"{response}\n"
    )
    with open(path, "a", encoding="utf-8") as f:
        if f.tell() == 0:
            f.write(
                f"# Watchman Notes -- {date_iso}\n"
                f"Retention: 30 days. Source: live log tail.\n"
            )
        f.write(entry)


def _append_to_flow_trace(flow_trace_dir: Path, date_iso: str,
                          lines: list[str]) -> None:
    """Write raw flow-event lines to the daily trace file. No API call."""
    flow_trace_dir.mkdir(parents=True, exist_ok=True)
    path = flow_trace_dir / f"trace_{date_iso}.md"
    with open(path, "a", encoding="utf-8") as f:
        if f.tell() == 0:
            f.write(
                f"# Live Flow Trace -- {date_iso}\n"
                f"Source: system log pipeline events. Raw — no commentary.\n\n"
            )
        for line in lines:
            f.write(line + "\n")


def _eod_flow_summary(flow_trace_dir: Path, date_iso: str, log,
                      dry_run: bool = False) -> None:
    """Read today's trace, pipe to agy for a 10-line EOD summary, append it."""
    trace_path = flow_trace_dir / f"trace_{date_iso}.md"
    if not trace_path.exists():
        log.info("gemini_watchman: no flow trace for %s, skipping EOD summary", date_iso)
        return

    try:
        trace_content = trace_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log.warning("gemini_watchman: could not read flow trace at %s", trace_path)
        return

    if not trace_content.strip():
        log.info("gemini_watchman: flow trace empty for %s, skipping summary", date_iso)
        return

    log.info("gemini_watchman: generating EOD flow summary from %d chars", len(trace_content))

    if dry_run:
        summary = f"[DRY RUN] Flow trace has {len(trace_content)} chars."
    else:
        summary = _call_agy(_FLOW_SUMMARY_PROMPT, trace_content, log)
        if summary is None:
            summary = "agy unavailable for EOD flow summary. Review trace manually."

    with open(trace_path, "a", encoding="utf-8") as f:
        f.write(
            f"\n---\n\n## EOD Summary ({now_ist().strftime('%H:%M IST')})\n\n"
            f"{summary}\n"
        )
    log.info("gemini_watchman: EOD flow summary appended to %s", trace_path)


def _send_critical_alert(response: str, date_iso: str, log) -> None:
    """If agy flags something critical, send Telegram alert."""
    critical_keywords = ["CRITICAL", "kill switch", "HARD_KILL", "breach",
                         "circuit breaker", "data loss", "corruption"]
    lower = response.lower()
    if not any(kw.lower() in lower for kw in critical_keywords):
        return
    if "all clear" in lower:
        return

    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=log)
        if not notifier:
            return
        summary = response[:400]
        notifier.send(
            severity="ERROR",
            title=f"Watchman Alert {date_iso}",
            body=summary,
            source_module="gemini_watchman",
        )
    except Exception as exc:
        log.debug("gemini_watchman: Telegram alert failed: %s", exc)


def run_watchman(
    date_iso: str,
    log_dir: Path,
    output_dir: Path,
    flow_trace_dir: Path,
    log,
    interval: int = 300,
    threshold: int = 20,
    dry_run: bool = False,
) -> int:
    """Main watchman loop. Returns exit code."""
    if not _is_market_hours():
        log.info("gemini_watchman: outside market hours, exiting")
        return 0

    log_path = _find_log_file(log_dir, date_iso)
    if log_path is None:
        log.info("gemini_watchman: no log file for %s yet, will poll", date_iso)

    log.info("gemini_watchman: starting for %s (interval=%ds, threshold=%d)",
             date_iso, interval, threshold)

    last_pos = 0
    if log_path and log_path.exists():
        last_pos = log_path.stat().st_size

    pending_warning_lines: list[str] = []
    last_batch_time = time.monotonic()

    while not _shutdown:
        if not _is_market_hours():
            log.info("gemini_watchman: market closed, shutting down")
            break

        if log_path is None or not log_path.exists():
            log_path = _find_log_file(log_dir, date_iso)
            if log_path and log_path.exists():
                last_pos = 0

        if log_path and log_path.exists():
            new_warning, new_flow, last_pos = _tail_new_lines(log_path, last_pos)
            pending_warning_lines.extend(new_warning)
            if new_flow:
                _append_to_flow_trace(flow_trace_dir, date_iso, new_flow)

        elapsed = time.monotonic() - last_batch_time
        should_batch = (
            (elapsed >= interval and len(pending_warning_lines) > 0) or
            len(pending_warning_lines) >= threshold
        )

        if should_batch:
            batch_text = "\n".join(pending_warning_lines[-500:])
            batch_count = len(pending_warning_lines)
            timestamp = now_ist().strftime("%H:%M:%S")

            log.info("gemini_watchman: sending batch of %d lines at %s",
                     batch_count, timestamp)

            if dry_run:
                log.info("gemini_watchman: dry-run; %d lines, skipping CLI call",
                         batch_count)
                _append_to_watchman_log(
                    output_dir, date_iso, timestamp,
                    f"[DRY RUN] {batch_count} lines would be analyzed", batch_count,
                )
            else:
                response = _call_agy(_WATCHMAN_PROMPT, batch_text, log)
                if response:
                    _append_to_watchman_log(
                        output_dir, date_iso, timestamp, response, batch_count,
                    )
                    _send_critical_alert(response, date_iso, log)
                else:
                    log.warning("gemini_watchman: no response for batch at %s", timestamp)

            pending_warning_lines.clear()
            last_batch_time = time.monotonic()

        time.sleep(10)

    if pending_warning_lines:
        batch_text = "\n".join(pending_warning_lines[-500:])
        timestamp = now_ist().strftime("%H:%M:%S")
        log.info("gemini_watchman: final batch of %d lines", len(pending_warning_lines))
        if not dry_run:
            response = _call_agy(_WATCHMAN_PROMPT, batch_text, log)
            if response:
                _append_to_watchman_log(
                    output_dir, date_iso, timestamp, response, len(pending_warning_lines),
                )

    # EOD: summarise the flow trace with a single agy call
    _eod_flow_summary(flow_trace_dir, date_iso, log, dry_run=dry_run)

    log.info("gemini_watchman: session complete for %s", date_iso)
    return 0


def main(argv=None) -> int:
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    args = _parse_args(argv)
    log = get_logger("gemini_watchman")
    date_iso = args.date or today_ist()
    log_dir = Path(args.log_dir) if args.log_dir else _ROOT / "logs"
    output_dir = Path(args.output_dir) if args.output_dir else _ROOT / "reports" / "watchman"
    flow_trace_dir = (
        Path(args.flow_trace_dir) if args.flow_trace_dir
        else _ROOT / "reports" / "flow_trace"
    )

    return run_watchman(
        date_iso=date_iso,
        log_dir=log_dir,
        output_dir=output_dir,
        flow_trace_dir=flow_trace_dir,
        log=log,
        interval=args.interval,
        threshold=args.threshold,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
