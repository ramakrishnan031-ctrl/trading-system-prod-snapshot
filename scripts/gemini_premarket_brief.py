"""
scripts/gemini_premarket_brief.py -- Trading System v2  FIX-150

Purpose:
    Pre-market briefing at 08:55 IST (10 min before market open).
    Reads yesterday's EOD review, queries open positions and strategy
    state, pipes to Gemini for a 100-word operational briefing.
    Sends full briefing to Telegram.

Usage:
    python scripts/gemini_premarket_brief.py [--date YYYY-MM-DD] [--dry-run]

Cron:
    55 8 * * 1-5

Exit codes:
    0 -- success
    1 -- error
    2 -- no data to brief on
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.time_authority import now_ist, today_ist

DB_PATH = _ROOT / "data_store" / "trading_system.db"
EOD_REVIEW_DIR = _ROOT / "reports" / "log_review"
BRIEFING_DIR = _ROOT / "reports" / "briefing"

_GEMINI_BIN = os.environ.get("GEMINI_BIN", "agy")
_GEMINI_TIMEOUT = 90

_BRIEFING_PROMPT = """\
You are a pre-market briefer for an automated intraday trading system.
Based on yesterday's EOD review and current system state, write a 100-word
briefing for today's trader. Highlight:
- Carryover positions to watch (if any)
- Strategies needing attention (paused, demoted, or underperforming)
- Recurring issues from yesterday that may repeat today
- Anything the trader should mentally prepare for

No code suggestions. Pure operational briefing. Be specific and actionable.
If no issues: say "Clean state, standard monitoring" and note yesterday's result."""


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gemini_premarket_brief",
        description="FIX-150: Pre-market Gemini briefing at 08:55 IST.",
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None,
                        help="Today's date (default: auto)")
    parser.add_argument("--db", metavar="PATH", default=str(DB_PATH))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _yesterday(today_iso: str) -> str:
    from datetime import date
    d = date.fromisoformat(today_iso)
    prev = d - timedelta(days=1)
    if prev.weekday() >= 5:
        prev = prev - timedelta(days=(prev.weekday() - 4))
    return prev.isoformat()


def _load_eod_review(yesterday_iso: str) -> str:
    path = EOD_REVIEW_DIR / f"eod_review_{yesterday_iso}.md"
    if not path.exists():
        return f"No EOD review found for {yesterday_iso}."
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > 3000:
            content = content[:3000] + "\n... (truncated)"
        return content
    except OSError:
        return f"Could not read EOD review for {yesterday_iso}."


def _get_open_positions(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT symbol, direction, qty_filled, entry_actual_price,
                      sl_initial, tgt_initial, strategy
               FROM trades WHERE status = 'OPEN'"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_strategy_state(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT strategy, date, win_rate, avg_pnl, sharpe_ratio,
                      trade_count, status
               FROM strategy_metrics
               WHERE date = (SELECT MAX(date) FROM strategy_metrics)
               ORDER BY strategy"""
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _get_kill_switch_state(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT state, reason FROM kill_switch_state ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row:
            return f"{row[0]}: {row[1]}" if row[1] else row[0]
        return "INACTIVE"
    except sqlite3.OperationalError:
        return "UNKNOWN"
    finally:
        conn.close()


def _get_yesterday_summary(db_path: str, yesterday_iso: str) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """SELECT COUNT(*) as trades,
                      SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                      SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) as losses,
                      COALESCE(SUM(net_pnl), 0) as total_pnl
               FROM trades
               WHERE status = 'CLOSED' AND DATE(created_at) = ?""",
            (yesterday_iso,),
        ).fetchone()
        return {
            "trades": row[0] or 0,
            "wins": row[1] or 0,
            "losses": row[2] or 0,
            "total_pnl": round(row[3] or 0, 2),
        }
    finally:
        conn.close()


def _call_gemini_cli(prompt: str, data: str, log) -> str | None:
    try:
        result = subprocess.run(
            [_GEMINI_BIN, "--print", prompt],
            input=data,
            capture_output=True,
            text=True,
            timeout=_GEMINI_TIMEOUT,
        )
        if result.returncode != 0:
            log.error("premarket_brief: agy CLI returned %d: %s",
                      result.returncode, result.stderr[:300])
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        log.error("premarket_brief: agy CLI timed out after %ds", _GEMINI_TIMEOUT)
        return None
    except FileNotFoundError:
        log.error("premarket_brief: agy binary not found at '%s'", _GEMINI_BIN)
        return None
    except Exception as exc:
        log.error("premarket_brief: Gemini CLI error: %s", exc)
        return None


def _send_telegram(briefing: str, date_iso: str, log) -> None:
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=log)
        if not notifier:
            return
        notifier.send(
            severity="INFO",
            title=f"Pre-Market Briefing {date_iso}",
            body=briefing[:1000],
            source_module="gemini_premarket_brief",
        )
    except Exception as exc:
        log.debug("premarket_telegram_failed: %s", exc)


def run_briefing(
    today_iso: str,
    db_path: str,
    log,
    dry_run: bool = False,
) -> int:
    """Generate pre-market briefing. Returns exit code."""
    yesterday_iso = _yesterday(today_iso)

    eod_review = _load_eod_review(yesterday_iso)
    open_positions = _get_open_positions(db_path)
    strategy_state = _get_strategy_state(db_path)
    kill_state = _get_kill_switch_state(db_path)
    yesterday_summary = _get_yesterday_summary(db_path, yesterday_iso)

    data_parts = [
        f"--- TODAY: {today_iso} ---\n",
        f"--- YESTERDAY: {yesterday_iso} ---\n",
        f"Yesterday result: {yesterday_summary['trades']} trades, "
        f"{yesterday_summary['wins']}W/{yesterday_summary['losses']}L, "
        f"P&L: Rs {yesterday_summary['total_pnl']}\n\n",
    ]

    if open_positions:
        data_parts.append("--- OPEN POSITIONS ---\n")
        for pos in open_positions:
            data_parts.append(
                f"{pos['symbol']} {pos['direction']} qty={pos['qty_filled']} "
                f"entry={pos['entry_actual_price']} SL={pos['sl_initial']} "
                f"TGT={pos['tgt_initial']} strategy={pos['strategy']}\n"
            )
        data_parts.append("\n")
    else:
        data_parts.append("No open positions carried overnight.\n\n")

    if strategy_state:
        data_parts.append("--- STRATEGY STATE ---\n")
        for s in strategy_state:
            data_parts.append(
                f"{s['strategy']}: win_rate={s.get('win_rate', 'N/A')}, "
                f"sharpe={s.get('sharpe_ratio', 'N/A')}, "
                f"status={s.get('status', 'ACTIVE')}\n"
            )
        data_parts.append("\n")

    data_parts.append(f"Kill switch: {kill_state}\n\n")

    data_parts.append("--- YESTERDAY EOD REVIEW ---\n")
    data_parts.append(eod_review)

    data = "".join(data_parts)

    if dry_run:
        log.info("premarket_brief: dry-run; data length=%d chars", len(data))
        return 0

    briefing = _call_gemini_cli(_BRIEFING_PROMPT, data, log)
    if briefing is None:
        briefing = (
            f"Gemini unavailable. Manual brief:\n"
            f"Yesterday: {yesterday_summary['trades']} trades, "
            f"Rs {yesterday_summary['total_pnl']} P&L.\n"
            f"Open positions: {len(open_positions)}.\n"
            f"Kill switch: {kill_state}."
        )

    BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
    brief_path = BRIEFING_DIR / f"brief_{today_iso}.md"
    brief_path.write_text(
        f"# Pre-Market Briefing -- {today_iso}\n\n"
        f"**Generated at:** {now_ist().strftime('%H:%M IST')}\n\n"
        f"---\n\n"
        f"{briefing}\n",
        encoding="utf-8",
    )
    log.info("premarket_brief: saved to %s", brief_path)

    _send_telegram(briefing, today_iso, log)

    return 0


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("gemini_premarket_brief")
    today_iso = args.date or today_ist()

    try:
        result = run_briefing(
            today_iso=today_iso,
            db_path=args.db,
            log=log,
            dry_run=args.dry_run,
        )

        try:
            from utils.cron_heartbeat import record_heartbeat
            record_heartbeat("gemini_premarket_brief")
        except Exception:
            pass

        return result
    except Exception as exc:
        log.error("gemini_premarket_brief failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
