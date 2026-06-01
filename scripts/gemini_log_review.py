"""
scripts/gemini_log_review.py -- Trading System v2  FIX-142

Purpose:
    Post-EOD AI log review using Google Gemini API.
    Extracts WARNING+ log entries, sends to Gemini for analysis,
    saves review to reports/log_review/.

Usage:
    python scripts/gemini_log_review.py [--date YYYY-MM-DD] [--dry-run]

Cron:
    20 16 * * 1-5  (after daily report at 16:05)

Exit codes:
    0 -- success (review generated)
    1 -- error (API failure, missing config)
    2 -- no logs to review (empty day)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.time_authority import today_ist


_PROMPT_TEMPLATE = """\
You are a trading system ops reviewer. Analyze these log entries from an \
automated intraday trading system and provide:

1. Summary of what happened today (2-3 sentences)
2. Errors or issues found (bullet list)
3. Patterns or recurring problems (bullet list, or "None" if clean)
4. Suggestions for improvement (bullet list, or "None" if clean)

Keep response under 500 words. Be specific about error counts and symbols.

--- LOG ENTRIES ---
{logs}
"""


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="gemini_log_review",
        description="FIX-142: AI-powered EOD log review via Gemini.",
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--log-dir", metavar="PATH", default=None)
    parser.add_argument("--output-dir", metavar="PATH", default=None)
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


def _call_gemini(prompt: str, api_key: str) -> str:
    """Call Gemini API and return the response text."""
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")
    response = model.generate_content(prompt)
    return response.text


def run_review(
    date_iso: str,
    log_dir: Path,
    output_dir: Path,
    log,
    dry_run: bool = False,
    max_lines: int = 500,
) -> int:
    """Run the log review pipeline. Returns exit code."""
    log_path = log_dir / f"trading_{date_iso}.log"
    if not log_path.exists():
        alt = log_dir / f"trading-system_{date_iso}.log"
        if alt.exists():
            log_path = alt
        else:
            all_logs = sorted(log_dir.glob(f"*{date_iso}*"))
            if all_logs:
                log_path = all_logs[0]

    log.info("gemini_log_review: extracting from %s", log_path)
    entries = _extract_warning_plus(log_path, max_lines=max_lines)

    if not entries.strip():
        log.info("gemini_log_review: no WARNING+ entries for %s", date_iso)
        output_dir.mkdir(parents=True, exist_ok=True)
        review_path = output_dir / f"review_{date_iso}.md"
        review_path.write_text(
            f"# Log Review — {date_iso}\n\nNo WARNING/ERROR/CRITICAL entries found. Clean day.\n",
            encoding="utf-8",
        )
        return 2

    prompt = _PROMPT_TEMPLATE.format(logs=entries)

    if dry_run:
        log.info("gemini_log_review: dry-run; prompt length=%d chars, %d log lines",
                 len(prompt), entries.count("\n") + 1)
        return 0

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        log.error("gemini_log_review: GEMINI_API_KEY not set")
        return 1

    try:
        review_text = _call_gemini(prompt, api_key)
    except Exception as exc:
        log.error("gemini_log_review: API call failed: %s", exc)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    review_path = output_dir / f"review_{date_iso}.md"
    content = f"# Log Review — {date_iso}\n\n{review_text}\n"
    review_path.write_text(content, encoding="utf-8")
    log.info("gemini_log_review: saved to %s (%d chars)", review_path, len(content))

    _send_telegram_summary(review_text, date_iso, log)

    return 0


def _send_telegram_summary(review_text: str, date_iso: str, log) -> None:
    """Best-effort Telegram summary of the AI review."""
    try:
        from alerts.telegram_notifier import TelegramNotifier
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHANNEL_PRIMARY", "")
        if not bot_token or not chat_id:
            return
        lines = review_text.strip().split("\n")
        summary = "\n".join(lines[:10])
        if len(lines) > 10:
            summary += "\n..."
        notifier = TelegramNotifier(bot_token=bot_token, logger=log)
        notifier.send(
            chat_id=chat_id,
            message=f"AI Log Review {date_iso}\n\n{summary}",
        )
    except Exception as exc:
        log.debug("gemini_log_review: Telegram send failed: %s", exc)


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("gemini_log_review")
    date_iso = args.date or today_ist()
    log_dir = Path(args.log_dir) if args.log_dir else _ROOT / "logs"
    output_dir = Path(args.output_dir) if args.output_dir else _ROOT / "reports" / "log_review"

    return run_review(
        date_iso=date_iso,
        log_dir=log_dir,
        output_dir=output_dir,
        log=log,
        dry_run=args.dry_run,
        max_lines=args.max_lines,
    )


if __name__ == "__main__":
    sys.exit(main())
