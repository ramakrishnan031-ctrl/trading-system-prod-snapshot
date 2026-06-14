"""
scripts/gemini_trade_coach.py -- Trading System v2  FIX-151

Purpose:
    Trade quality coaching via Gemini. Runs after EOD review (16:40 IST).
    Reads today's closed trades (entry/exit/MFE/MAE), rejected signals,
    and candle data, then asks Gemini to grade each trade and extract lessons.

Usage:
    python scripts/gemini_trade_coach.py [--date YYYY-MM-DD] [--dry-run]

Cron:
    40 16 * * 1-5

Exit codes:
    0 -- success
    1 -- error
    2 -- no trades to coach
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core import db_connect  # O6: analytics tables live in analytics.db (ATTACHed)

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from core.logger import get_logger
from core.time_authority import now_ist, today_ist

DB_PATH = _ROOT / "data_store" / "trading_system.db"
OUTPUT_DIR = _ROOT / "reports" / "coach"

from scripts.agy_runner import run_trade_coach as _agy_trade_coach

_COACH_PROMPT = """\
You are a trading coach reviewing today's trades.
For each closed trade, assess:
1. Entry quality: was the entry timed well given the day's price action?
2. Exit quality: was exit too early (left profit on table) or too late?
3. Could SL have been placed better?
4. Any signals that were rejected but would have been winners?

Output structured per trade:
[SYMBOL] Entry: [grade A-F] Exit: [grade A-F] Notes: [specific]

At end: Top 3 lessons for tomorrow.
No code suggestions. Coaching only."""


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gemini_trade_coach",
        description="FIX-151: Trade quality coaching via Gemini.",
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--db", metavar="PATH", default=str(DB_PATH))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _get_closed_trades(db_path: str, date_iso: str) -> list[dict]:
    conn = db_connect.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT t.symbol, t.direction, t.strategy, t.qty_filled,
                      t.entry_actual_price, t.exit_price, t.exit_reason,
                      t.sl_initial, t.tgt_initial, t.gross_pnl, t.net_pnl,
                      t.entry_time, t.exit_time,
                      te.mfe_price, te.mfe_pct, te.mae_price, te.mae_pct
               FROM trades t
               LEFT JOIN trade_excursions te ON t.trade_id = te.trade_id
               WHERE t.status = 'CLOSED' AND DATE(t.created_at) = ?
               ORDER BY t.entry_time""",
            (date_iso,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _get_rejected_signals(db_path: str, date_iso: str) -> list[dict]:
    conn = db_connect.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT symbol, scanner, strategy, trigger_price,
                      rejection_reason, received_at
               FROM signals
               WHERE status LIKE 'REJECTED%%' AND DATE(received_at) = ?
               ORDER BY received_at""",
            (date_iso,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _get_candle_summary(db_path: str, symbol: str, date_iso: str) -> dict:
    conn = db_connect.connect(db_path)
    try:
        row = conn.execute(
            """SELECT MIN(low) as day_low, MAX(high) as day_high,
                      (SELECT open FROM candles WHERE symbol = ? AND date = ?
                       ORDER BY ts LIMIT 1) as day_open,
                      (SELECT close FROM candles WHERE symbol = ? AND date = ?
                       ORDER BY ts DESC LIMIT 1) as day_close,
                      COUNT(*) as candle_count
               FROM candles
               WHERE symbol = ? AND date = ?""",
            (symbol, date_iso, symbol, date_iso, symbol, date_iso),
        ).fetchone()
        if row and row[0] is not None:
            return {
                "day_low": row[0], "day_high": row[1],
                "day_open": row[2], "day_close": row[3],
                "candle_count": row[4],
            }
        return {}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def _build_data(trades: list[dict], rejected: list[dict],
                db_path: str, date_iso: str) -> str:
    parts = [f"--- TRADE COACHING DATA: {date_iso} ---\n\n"]

    if trades:
        parts.append(f"## Closed Trades ({len(trades)})\n\n")
        for t in trades:
            candle = _get_candle_summary(db_path, t["symbol"], date_iso)
            parts.append(
                f"### {t['symbol']} ({t['direction']}, {t['strategy']})\n"
                f"  Entry: {t['entry_actual_price']} at {t.get('entry_time', 'N/A')}\n"
                f"  Exit: {t['exit_price']} at {t.get('exit_time', 'N/A')} "
                f"({t['exit_reason']})\n"
                f"  SL: {t['sl_initial']}  TGT: {t['tgt_initial']}\n"
                f"  P&L: gross={t['gross_pnl']}, net={t['net_pnl']}\n"
                f"  MFE: {t.get('mfe_price', 'N/A')} ({t.get('mfe_pct', 'N/A')}%)\n"
                f"  MAE: {t.get('mae_price', 'N/A')} ({t.get('mae_pct', 'N/A')}%)\n"
            )
            if candle:
                parts.append(
                    f"  Day range: {candle['day_open']}-{candle['day_close']} "
                    f"(L:{candle['day_low']} H:{candle['day_high']}, "
                    f"{candle['candle_count']} candles)\n"
                )
            parts.append("\n")

    if rejected:
        parts.append(f"\n## Rejected Signals ({len(rejected)})\n\n")
        for r in rejected:
            parts.append(
                f"  {r['symbol']} ({r['strategy']}): "
                f"trigger={r.get('trigger_price', 'N/A')}, "
                f"reason={r.get('rejection_reason', 'N/A')}\n"
            )

    return "".join(parts)


def _call_gemini_cli(prompt: str, data: str, log) -> str | None:
    result = _agy_trade_coach(prompt, input_data=data)
    if result is None:
        log.error("trade_coach: all models unavailable")
    return result


def _extract_lessons(coaching: str) -> str:
    lines = coaching.split("\n")
    in_lessons = False
    lessons = []
    for line in lines:
        if "lesson" in line.lower() or "top 3" in line.lower():
            in_lessons = True
        if in_lessons:
            lessons.append(line)
    if lessons:
        return "\n".join(lessons[:10])
    return coaching[-400:] if len(coaching) > 400 else coaching


def _send_telegram(lessons: str, date_iso: str, log) -> None:
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=log)
        if not notifier:
            return
        notifier.send(
            severity="INFO",
            title=f"Trade Coach {date_iso}",
            body=lessons[:400],
            source_module="gemini_trade_coach",
        )
    except Exception as exc:
        log.debug("trade_coach_telegram_failed: %s", exc)


def run_coaching(
    date_iso: str,
    db_path: str,
    log,
    dry_run: bool = False,
) -> int:
    trades = _get_closed_trades(db_path, date_iso)
    rejected = _get_rejected_signals(db_path, date_iso)

    if not trades and not rejected:
        log.info("trade_coach: no trades or rejected signals for %s", date_iso)
        return 2

    log.info("trade_coach: %d closed trades, %d rejected signals for %s",
             len(trades), len(rejected), date_iso)

    data = _build_data(trades, rejected, db_path, date_iso)

    if dry_run:
        log.info("trade_coach: dry-run; data length=%d chars", len(data))
        return 0

    coaching = _call_gemini_cli(_COACH_PROMPT, data, log)
    if coaching is None:
        coaching = (
            f"Gemini unavailable. Manual review:\n"
            f"{len(trades)} closed trades, {len(rejected)} rejected signals.\n"
            f"Review reports/output/ for details."
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / f"coach_{date_iso}.md"
    report_path.write_text(
        f"# Trade Coaching Report -- {date_iso}\n\n"
        f"**Trades reviewed:** {len(trades)}\n"
        f"**Rejected signals:** {len(rejected)}\n"
        f"**Generated at:** {now_ist().strftime('%H:%M IST')}\n\n"
        f"---\n\n"
        f"{coaching}\n",
        encoding="utf-8",
    )
    log.info("trade_coach: report saved to %s", report_path)

    lessons = _extract_lessons(coaching)
    _send_telegram(lessons, date_iso, log)

    return 0


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("gemini_trade_coach")
    date_iso = args.date or today_ist()

    try:
        result = run_coaching(
            date_iso=date_iso,
            db_path=args.db,
            log=log,
            dry_run=args.dry_run,
        )

        try:
            from utils.cron_heartbeat import record_heartbeat
            record_heartbeat("gemini_trade_coach")
        except Exception:
            pass

        return result
    except Exception as exc:
        log.error("gemini_trade_coach failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
