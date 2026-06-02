"""
scripts/gemini_watchman.py -- Trading System v2  FIX-143

Purpose:
    Live log watchman using Gemini CLI. Tails today's system log during
    market hours (09:15-16:30 IST), batches WARNING+ entries every 5 minutes,
    pipes them to Gemini CLI for real-time analysis.

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
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.time_authority import now_ist, today_ist

_MARKET_OPEN = dtime(9, 15)
_MARKET_CLOSE = dtime(16, 30)

_WARNING_RE = re.compile(r"\b(WARNING|ERROR|CRITICAL)\b")

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

_GEMINI_BIN = os.environ.get("GEMINI_BIN", "gemini")
_GEMINI_TIMEOUT = 60

_shutdown = False


def _signal_handler(signum, frame):
    global _shutdown
    _shutdown = True


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="gemini_watchman",
        description="FIX-143: Live log watchman via Gemini CLI.",
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--log-dir", metavar="PATH", default=None)
    parser.add_argument("--output-dir", metavar="PATH", default=None)
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


def _tail_new_lines(log_path: Path, last_pos: int) -> tuple[list[str], int]:
    """Read new WARNING+ lines since last_pos. Returns (lines, new_pos)."""
    warning_lines = []
    try:
        size = log_path.stat().st_size
        if size < last_pos:
            last_pos = 0
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(last_pos)
            for line in f:
                if _WARNING_RE.search(line):
                    warning_lines.append(line.rstrip())
            new_pos = f.tell()
    except OSError:
        return [], last_pos
    return warning_lines, new_pos


def _call_gemini(log_batch: str, log) -> str | None:
    """Pipe log batch to Gemini CLI and return response."""
    try:
        result = subprocess.run(
            [_GEMINI_BIN, "-p", _WATCHMAN_PROMPT],
            input=log_batch,
            capture_output=True,
            text=True,
            timeout=_GEMINI_TIMEOUT,
        )
        if result.returncode != 0:
            log.warning("gemini_watchman: CLI returned %d: %s",
                        result.returncode, result.stderr[:200])
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        log.warning("gemini_watchman: CLI timed out after %ds", _GEMINI_TIMEOUT)
        return None
    except FileNotFoundError:
        log.error("gemini_watchman: gemini binary not found at '%s'", _GEMINI_BIN)
        return None
    except Exception as exc:
        log.warning("gemini_watchman: CLI error: %s", exc)
        return None


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


def _send_critical_alert(response: str, date_iso: str, log) -> None:
    """If Gemini flags something critical, send Telegram alert."""
    critical_keywords = ["CRITICAL", "kill switch", "HARD_KILL", "breach",
                         "circuit breaker", "data loss", "corruption"]
    lower = response.lower()
    if not any(kw.lower() in lower for kw in critical_keywords):
        return
    if "all clear" in lower:
        return

    try:
        from alerts.telegram_notifier import TelegramNotifier
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHANNEL_PRIMARY", "")
        if not bot_token or not chat_id:
            return
        notifier = TelegramNotifier(bot_token=bot_token, logger=log)
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

    pending_lines: list[str] = []
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
            new_lines, last_pos = _tail_new_lines(log_path, last_pos)
            pending_lines.extend(new_lines)

        elapsed = time.monotonic() - last_batch_time
        should_batch = (
            (elapsed >= interval and len(pending_lines) > 0) or
            len(pending_lines) >= threshold
        )

        if should_batch:
            batch_text = "\n".join(pending_lines[-500:])
            batch_count = len(pending_lines)
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
                response = _call_gemini(batch_text, log)
                if response:
                    _append_to_watchman_log(
                        output_dir, date_iso, timestamp, response, batch_count,
                    )
                    _send_critical_alert(response, date_iso, log)
                else:
                    log.warning("gemini_watchman: no response for batch at %s", timestamp)

            pending_lines.clear()
            last_batch_time = time.monotonic()

        time.sleep(10)

    if pending_lines:
        batch_text = "\n".join(pending_lines[-500:])
        timestamp = now_ist().strftime("%H:%M:%S")
        log.info("gemini_watchman: final batch of %d lines", len(pending_lines))
        if not dry_run:
            response = _call_gemini(batch_text, log)
            if response:
                _append_to_watchman_log(
                    output_dir, date_iso, timestamp, response, len(pending_lines),
                )

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

    return run_watchman(
        date_iso=date_iso,
        log_dir=log_dir,
        output_dir=output_dir,
        log=log,
        interval=args.interval,
        threshold=args.threshold,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
