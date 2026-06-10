"""
scripts/gemini_weekly_patterns.py -- Trading System v2  FIX-151

Purpose:
    Weekly pattern detection. Runs Sunday 18:00 IST.
    Reads last 5 trading days of watchman notes, EOD reviews,
    and daily reports, then pipes to Gemini for recurring-pattern analysis.

Usage:
    python scripts/gemini_weekly_patterns.py [--date YYYY-MM-DD] [--dry-run]

Cron:
    0 18 * * 0  (Sundays at 18:00 IST)

Exit codes:
    0 -- success
    1 -- error
    2 -- no data to analyze
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.time_authority import now_ist, today_ist

WATCHMAN_DIR = _ROOT / "reports" / "watchman"
EOD_REVIEW_DIR = _ROOT / "reports" / "log_review"
DAILY_REPORT_DIR = _ROOT / "reports" / "output"
OUTPUT_DIR = _ROOT / "reports" / "weekly_patterns"

from scripts.agy_runner import run_weekly_patterns as _agy_weekly

_WEEKLY_PROMPT = """\
You are a senior trading ops analyst. Here are 5 days of operational data.
Identify RECURRING patterns ONLY (not one-off events):
- Issues that repeated 3+ days
- Systematic slippage patterns by strategy/time
- Recurring API failures or system anomalies
- Performance trends per strategy

Output structured sections:
## Recurring Issues
## Performance Trends
## Improvements to Investigate

Maximum 600 words. Be specific with dates and frequencies. No code suggestions."""


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gemini_weekly_patterns",
        description="FIX-151: Weekly pattern detection via Gemini.",
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None,
                        help="End date (default: today)")
    parser.add_argument("--days", type=int, default=5,
                        help="Number of trading days to analyze (default: 5)")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _trading_days(end_date: date, count: int) -> list[date]:
    days = []
    d = end_date
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _load_file(path: Path, max_chars: int = 2000) -> str:
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... (truncated)"
        return content
    except OSError:
        return ""


def _gather_data(days: list[date], log) -> str:
    parts = []
    for d in days:
        d_iso = d.isoformat()
        parts.append(f"\n{'='*60}\n=== {d_iso} ({d.strftime('%A')}) ===\n{'='*60}\n")

        watchman = _load_file(WATCHMAN_DIR / f"watchman_{d_iso}.md")
        if watchman:
            parts.append(f"\n--- WATCHMAN NOTES ---\n{watchman}\n")

        eod = _load_file(EOD_REVIEW_DIR / f"eod_review_{d_iso}.md")
        if eod:
            parts.append(f"\n--- EOD REVIEW ---\n{eod}\n")

        for ext in ("md", "txt"):
            report = _load_file(DAILY_REPORT_DIR / f"daily_report_{d_iso}.{ext}")
            if report:
                parts.append(f"\n--- DAILY REPORT ---\n{report}\n")
                break

    return "".join(parts)


def _call_gemini_cli(prompt: str, data: str, log) -> str | None:
    result = _agy_weekly(prompt, input_data=data)
    if result is None:
        log.error("weekly_patterns: all models unavailable")
    return result


def _send_telegram(message: str, log) -> None:
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=log)
        if not notifier:
            return
        notifier.send(
            severity="INFO",
            title="Weekly Patterns Report",
            body=message[:400],
            source_module="gemini_weekly_patterns",
        )
    except Exception as exc:
        log.debug("weekly_patterns_telegram_failed: %s", exc)


def run_analysis(
    end_date_iso: str,
    days_count: int,
    log,
    dry_run: bool = False,
) -> int:
    end = date.fromisoformat(end_date_iso)
    days = _trading_days(end, days_count)

    log.info("weekly_patterns: analyzing %d days ending %s", len(days), end_date_iso)

    data = _gather_data(days, log)
    if not data.strip() or len(data) < 50:
        log.info("weekly_patterns: insufficient data for analysis")
        return 2

    if dry_run:
        log.info("weekly_patterns: dry-run; data length=%d chars", len(data))
        return 0

    analysis = _call_gemini_cli(_WEEKLY_PROMPT, data, log)
    if analysis is None:
        analysis = "Gemini CLI unavailable. Manual review required for the past week's operational data."

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / f"patterns_{end_date_iso}.md"
    report_path.write_text(
        f"# Weekly Patterns Report -- {end_date_iso}\n\n"
        f"**Period:** {days[0].isoformat()} to {days[-1].isoformat()} "
        f"({len(days)} trading days)\n"
        f"**Generated at:** {now_ist().strftime('%H:%M IST')}\n\n"
        f"---\n\n"
        f"{analysis}\n",
        encoding="utf-8",
    )
    log.info("weekly_patterns: report saved to %s", report_path)

    _send_telegram(analysis, log)

    return 0


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("gemini_weekly_patterns")
    end_date_iso = args.date or today_ist()

    try:
        result = run_analysis(
            end_date_iso=end_date_iso,
            days_count=args.days,
            log=log,
            dry_run=args.dry_run,
        )

        try:
            from utils.cron_heartbeat import record_heartbeat
            record_heartbeat("gemini_weekly_patterns")
        except Exception:
            pass

        return result
    except Exception as exc:
        log.error("gemini_weekly_patterns failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
