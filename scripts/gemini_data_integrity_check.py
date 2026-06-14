"""
scripts/gemini_data_integrity_check.py -- Trading System v2  FIX-150

Purpose:
    Daily candle/price data integrity check. Picks random traded symbols,
    fetches authoritative 1-min candles from Zerodha historical API,
    compares against our candles DB table, pipes findings to Gemini for review.

Usage:
    python scripts/gemini_data_integrity_check.py [--date YYYY-MM-DD] [--dry-run]

Cron:
    0 17 * * 1-5  (17:00 IST, after all data settled)

Exit codes:
    0 -- CLEAN (or dry-run)
    1 -- error
    2 -- DIVERGENCE_DETECTED
    3 -- no traded symbols today
"""
from __future__ import annotations

import argparse
import json
import random
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
from core.time_authority import today_ist

DB_PATH = _ROOT / "data_store" / "trading_system.db"
TOKEN_PATH = _ROOT / "data_store" / "session" / "zerodha_token.json"
OUTPUT_DIR = _ROOT / "reports" / "integrity"
API_KEY = "pvahsvuu3xjsefc7"

from scripts.agy_runner import run_data_integrity as _agy_integrity

_OHLC_THRESHOLD_PCT = 0.05
_VOLUME_THRESHOLD_PCT = 5.0
_SAMPLE_SIZE = 5

_INTEGRITY_PROMPT = """\
You are a data integrity reviewer for a trading system.
Compare these Zerodha (authoritative) vs system candle data.
Flag any meaningful discrepancies. Ignore floating-point noise (<0.01%).

Output exactly:
- Status: CLEAN or DIVERGENCE_DETECTED
- Symbol-by-symbol comparison summary (1 line each)
- Details for any divergence (specific timestamps, values)
- Overall data quality assessment (1 sentence)

Maximum 300 words."""


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gemini_data_integrity_check",
        description="FIX-150: Daily candle data integrity check via Gemini.",
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--db", metavar="PATH", default=str(DB_PATH))
    parser.add_argument("--output-dir", metavar="PATH", default=str(OUTPUT_DIR))
    parser.add_argument("--sample-size", type=int, default=_SAMPLE_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _get_traded_symbols(db_path: str, date_iso: str) -> list[str]:
    conn = db_connect.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT DISTINCT t.symbol FROM trades t
               WHERE t.status IN ('CLOSED', 'OPEN')
               AND DATE(t.created_at) = ?""",
            (date_iso,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _get_system_candles(db_path: str, symbol: str, date_iso: str) -> list[dict]:
    conn = db_connect.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT ts, open, high, low, close, volume
               FROM candles
               WHERE symbol = ? AND DATE(ts) = ?
               ORDER BY ts""",
            (symbol, date_iso),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_instrument_token(db_path: str, symbol: str, log=None) -> int | None:
    conn = db_connect.connect(db_path)
    try:
        row = conn.execute(
            "SELECT instrument_token FROM instruments WHERE tradingsymbol = ? LIMIT 1",
            (symbol,),
        ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        if log is not None:
            log.warning(
                "integrity_check: instruments table not found in DB — token lookup skipped for %s"
                " (O8: instruments is CSV-based, not in schema.sql)",
                symbol,
            )
        return None
    finally:
        conn.close()


def _fetch_zerodha_candles(symbol: str, instrument_token: int, date_iso: str, log) -> list[dict] | None:
    """Fetch 1-min candles from Zerodha historical API."""
    try:
        token_data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        access_token = token_data.get("access_token", "")
    except Exception:
        log.warning("integrity_check: cannot read token for %s", symbol)
        return None

    if not access_token:
        return None

    try:
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(access_token)

        from_dt = f"{date_iso} 09:15:00"
        to_dt = f"{date_iso} 15:30:00"

        data = kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_dt,
            to_date=to_dt,
            interval="minute",
        )
        candles = []
        for row in data:
            candles.append({
                "ts": row["date"].strftime("%Y-%m-%d %H:%M:%S") if hasattr(row["date"], "strftime") else str(row["date"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
            })
        return candles
    except Exception as exc:
        log.warning("integrity_check: Zerodha fetch failed for %s: %s", symbol, exc)
        return None


def _compare_candles(system: list[dict], zerodha: list[dict]) -> dict:
    """Compare system vs Zerodha candles. Returns comparison report."""
    sys_by_ts = {c["ts"]: c for c in system}
    zrd_by_ts = {c["ts"]: c for c in zerodha}

    all_ts = sorted(set(sys_by_ts.keys()) | set(zrd_by_ts.keys()))
    missing_in_system = []
    missing_in_zerodha = []
    ohlc_mismatches = []
    volume_mismatches = []

    for ts in all_ts:
        s = sys_by_ts.get(ts)
        z = zrd_by_ts.get(ts)

        if s is None and z is not None:
            missing_in_system.append(ts)
            continue
        if z is None and s is not None:
            missing_in_zerodha.append(ts)
            continue

        for field in ("open", "high", "low", "close"):
            sv = float(s.get(field, 0))
            zv = float(z.get(field, 0))
            if zv > 0 and abs(sv - zv) / zv * 100 > _OHLC_THRESHOLD_PCT:
                ohlc_mismatches.append({
                    "ts": ts, "field": field,
                    "system": sv, "zerodha": zv,
                    "diff_pct": abs(sv - zv) / zv * 100,
                })

        sv = int(s.get("volume", 0))
        zv = int(z.get("volume", 0))
        if zv > 0 and abs(sv - zv) / zv * 100 > _VOLUME_THRESHOLD_PCT:
            volume_mismatches.append({
                "ts": ts,
                "system": sv, "zerodha": zv,
                "diff_pct": abs(sv - zv) / zv * 100,
            })

    has_divergence = bool(missing_in_system or ohlc_mismatches or volume_mismatches)

    return {
        "total_zerodha": len(zerodha),
        "total_system": len(system),
        "missing_in_system": len(missing_in_system),
        "missing_in_zerodha": len(missing_in_zerodha),
        "ohlc_mismatches": ohlc_mismatches[:10],
        "volume_mismatches": volume_mismatches[:10],
        "has_divergence": has_divergence,
    }


def _call_gemini_cli(prompt: str, data: str, log) -> str | None:
    result = _agy_integrity(prompt, input_data=data)
    if result is None:
        log.error("integrity_check: all models unavailable")
    return result


def _send_telegram(message: str, log) -> None:
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=log)
        if not notifier:
            return
        notifier.send(
            severity="WARNING",
            title="Data Integrity: DIVERGENCE_DETECTED",
            body=message[:500],
            source_module="gemini_data_integrity_check",
        )
    except Exception as exc:
        log.debug("integrity_telegram_failed: %s", exc)


def run_check(
    date_iso: str,
    db_path: str,
    output_dir: Path,
    sample_size: int,
    log,
    dry_run: bool = False,
) -> int:
    """Run the data integrity check. Returns exit code."""
    symbols = _get_traded_symbols(db_path, date_iso)
    if not symbols:
        log.info("integrity_check: no traded symbols for %s", date_iso)
        return 3

    sample = random.sample(symbols, min(sample_size, len(symbols)))
    log.info("integrity_check: checking %d symbols: %s", len(sample), sample)

    all_comparisons = {}
    any_divergence = False

    for symbol in sample:
        system_candles = _get_system_candles(db_path, symbol, date_iso)
        if not system_candles:
            all_comparisons[symbol] = {"status": "NO_SYSTEM_DATA", "system_count": 0}
            continue

        instrument_token = _get_instrument_token(db_path, symbol, log)
        if instrument_token is None:
            all_comparisons[symbol] = {
                "status": "NO_INSTRUMENT_TOKEN",
                "system_count": len(system_candles),
            }
            continue

        if dry_run:
            all_comparisons[symbol] = {
                "status": "DRY_RUN",
                "system_count": len(system_candles),
            }
            continue

        zerodha_candles = _fetch_zerodha_candles(symbol, instrument_token, date_iso, log)
        if zerodha_candles is None:
            all_comparisons[symbol] = {
                "status": "ZERODHA_FETCH_FAILED",
                "system_count": len(system_candles),
            }
            continue

        comparison = _compare_candles(system_candles, zerodha_candles)
        all_comparisons[symbol] = comparison
        if comparison["has_divergence"]:
            any_divergence = True

    report_data = json.dumps(all_comparisons, indent=2, default=str)

    if dry_run:
        log.info("integrity_check: dry-run complete for %d symbols", len(sample))
        return 0

    review_text = _call_gemini_cli(_INTEGRITY_PROMPT, report_data, log)
    if review_text is None:
        review_text = f"Gemini CLI unavailable. Raw comparison:\n\n{report_data}"

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"integrity_{date_iso}.md"
    report_path.write_text(
        f"# Data Integrity Check -- {date_iso}\n\n"
        f"**Symbols checked:** {', '.join(sample)}\n"
        f"**Status:** {'DIVERGENCE_DETECTED' if any_divergence else 'CLEAN'}\n\n"
        f"---\n\n"
        f"{review_text}\n\n"
        f"---\n\n"
        f"## Raw Comparison Data\n\n"
        f"```json\n{report_data}\n```\n",
        encoding="utf-8",
    )
    log.info("integrity_check: report saved to %s", report_path)

    if any_divergence:
        _send_telegram(
            f"Data integrity check for {date_iso}: DIVERGENCE_DETECTED\n"
            f"Symbols: {', '.join(sample)}\n"
            f"See reports/integrity/integrity_{date_iso}.md",
            log,
        )
        return 2

    return 0


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("gemini_data_integrity_check")
    date_iso = args.date or today_ist()

    try:
        result = run_check(
            date_iso=date_iso,
            db_path=args.db,
            output_dir=Path(args.output_dir),
            sample_size=args.sample_size,
            log=log,
            dry_run=args.dry_run,
        )

        try:
            from utils.cron_heartbeat import record_heartbeat
            record_heartbeat("gemini_data_integrity_check")
        except Exception:
            pass

        return result
    except Exception as exc:
        log.error("gemini_data_integrity_check failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
