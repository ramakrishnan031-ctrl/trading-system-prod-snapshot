"""
scripts/gemini_log_review.py -- Trading System v2  FIX-143

Purpose:
    Post-EOD AI log review using Gemini CLI (Google auth, no API key).
    Reads full day's WARNING+ log entries + watchman notes,
    sends to Gemini CLI for structured daily report.

Usage:
    python scripts/gemini_log_review.py [--date YYYY-MM-DD] [--dry-run]

Cron:
    35 16 * * 1-5  cd ~/systems/trading-system && ~/systems/venv/bin/python scripts/gemini_log_review.py

Exit codes:
    0 -- success (review generated)
    1 -- error (CLI failure)
    2 -- no logs to review (empty day)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.time_authority import today_ist

_GEMINI_BIN = os.environ.get("GEMINI_BIN", "agy")
_GEMINI_TIMEOUT = 120

_EOD_PROMPT = """\
You are a senior trading ops reviewer. Today's complete log + watchman notes attached.
Observe ONLY — do not suggest code fixes.

Output exactly these sections (skip a section if empty):

## Session Summary
- Trading window: HH:MM to HH:MM IST
- Total signals: X received, Y traded, Z rejected
- Total trades: A wins, B losses, C breakeven

## Critical Issues (capital/risk/system)
Format: [HH:MM:SS] [SYMBOL] — issue

## Order Execution Quality
- Slippage outliers (>1%): list with symbol + actual %
- Rejections: list with reason
- Partial fills: list

## System Health
- WebSocket events
- DB/API errors
- Kill switch events

## Patterns Noticed
Recurring issues across the session

## Recommendations
What to investigate manually (NO code suggestions)

Maximum 800 words total. Be specific with timestamps and symbols."""


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="gemini_log_review",
        description="FIX-143: AI-powered EOD log review via Gemini CLI.",
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--log-dir", metavar="PATH", default=None)
    parser.add_argument("--output-dir", metavar="PATH", default=None)
    parser.add_argument("--watchman-dir", metavar="PATH", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-lines", type=int, default=500)
    return parser.parse_args(argv)


def _extract_warning_plus(log_path: Path, max_lines: int = 500) -> str:
    """Extract WARNING/ERROR/CRITICAL lines from a log file."""
    if not log_path.exists():
        return ""
    pattern = re.compile(r"\b(WARNING|ERROR|CRITICAL)\b")
    lines = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if pattern.search(line):
                    lines.append(line.rstrip())
                    if len(lines) >= max_lines:
                        break
    except OSError:
        return ""
    return "\n".join(lines)


def _find_log_file(log_dir: Path, date_iso: str) -> Path:
    candidates = [
        log_dir / f"system_{date_iso}.log",
        log_dir / f"trading_{date_iso}.log",
        log_dir / f"trading-system_{date_iso}.log",
    ]
    for p in candidates:
        if p.exists():
            return p
    matched = sorted(log_dir.glob(f"*{date_iso}*"))
    return matched[0] if matched else candidates[0]


def _load_watchman_notes(watchman_dir: Path, date_iso: str) -> str:
    path = watchman_dir / f"watchman_{date_iso}.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _call_gemini_cli(prompt: str, data: str, log) -> str | None:
    """Call Gemini CLI: -p for the prompt, stdin for the data."""
    try:
        result = subprocess.run(
            [_GEMINI_BIN, "--print", prompt],
            input=data,
            capture_output=True,
            text=True,
            timeout=_GEMINI_TIMEOUT,
        )
        if result.returncode != 0:
            log.error("gemini_log_review: CLI returned %d: %s",
                      result.returncode, result.stderr[:300])
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        log.error("gemini_log_review: CLI timed out after %ds", _GEMINI_TIMEOUT)
        return None
    except FileNotFoundError:
        log.error("gemini_log_review: agy binary not found at '%s'", _GEMINI_BIN)
        return None
    except Exception as exc:
        log.error("gemini_log_review: CLI error: %s", exc)
        return None


def run_review(
    date_iso: str,
    log_dir: Path,
    output_dir: Path,
    watchman_dir: Path,
    log,
    dry_run: bool = False,
    max_lines: int = 500,
) -> int:
    """Run the EOD review pipeline. Returns exit code."""
    log_path = _find_log_file(log_dir, date_iso)
    log.info("gemini_log_review: extracting from %s", log_path)
    entries = _extract_warning_plus(log_path, max_lines=max_lines)
    watchman_notes = _load_watchman_notes(watchman_dir, date_iso)

    if not entries.strip() and not watchman_notes.strip():
        log.info("gemini_log_review: no WARNING+ entries for %s", date_iso)
        output_dir.mkdir(parents=True, exist_ok=True)
        review_path = output_dir / f"eod_review_{date_iso}.md"
        review_path.write_text(
            f"# EOD Review -- {date_iso}\n"
            f"Retention: 90 days. Source: full log + watchman notes.\n\n"
            f"No WARNING/ERROR/CRITICAL entries found. Clean day.\n",
            encoding="utf-8",
        )
        return 2

    data_parts = ["--- LOG ENTRIES ---\n", entries]
    if watchman_notes:
        data_parts.append("\n\n--- WATCHMAN NOTES ---\n")
        data_parts.append(watchman_notes)

    data = "".join(data_parts)

    if dry_run:
        log.info("gemini_log_review: dry-run; data length=%d chars, %d log lines, watchman=%s",
                 len(data), entries.count("\n") + 1 if entries else 0,
                 "yes" if watchman_notes else "no")
        return 0

    review_text = _call_gemini_cli(_EOD_PROMPT, data, log)
    if review_text is None:
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    review_path = output_dir / f"eod_review_{date_iso}.md"
    content = (
        f"# EOD Review -- {date_iso}\n"
        f"Retention: 90 days. Source: full log + watchman notes.\n\n"
        f"{review_text}\n"
    )
    review_path.write_text(content, encoding="utf-8")
    log.info("gemini_log_review: saved to %s (%d chars)", review_path, len(content))

    _send_telegram_summary(review_text, date_iso, log)

    # FIX-145: Record heartbeat for cron drift monitoring
    try:
        from utils.cron_heartbeat import record_heartbeat
        record_heartbeat("gemini_log_review")
    except Exception:
        pass

    return 0


def _send_telegram_summary(review_text: str, date_iso: str, log) -> None:
    """Best-effort Telegram summary of the AI review."""
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=log)
        if not notifier:
            return
        summary = review_text[:500]
        if len(review_text) > 500:
            summary += "\n..."
        notifier.send(
            severity="INFO",
            title=f"AI EOD Review {date_iso}",
            body=summary,
            source_module="gemini_log_review",
        )
    except Exception as exc:
        log.debug("gemini_log_review: Telegram send failed: %s", exc)


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("gemini_log_review")
    date_iso = args.date or today_ist()
    log_dir = Path(args.log_dir) if args.log_dir else _ROOT / "logs"
    output_dir = Path(args.output_dir) if args.output_dir else _ROOT / "reports" / "log_review"
    watchman_dir = Path(args.watchman_dir) if args.watchman_dir else _ROOT / "reports" / "watchman"

    return run_review(
        date_iso=date_iso,
        log_dir=log_dir,
        output_dir=output_dir,
        watchman_dir=watchman_dir,
        log=log,
        dry_run=args.dry_run,
        max_lines=args.max_lines,
    )


if __name__ == "__main__":
    sys.exit(main())
