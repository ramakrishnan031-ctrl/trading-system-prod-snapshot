"""
scripts/fetch_daily_candles.py — Fetch 1-minute OHLCV candles from Zerodha for daily report.

Runs on the VM after market close (cron: 15:40 IST Mon-Fri).
Generates candle_data_YYYY-MM-DD.csv consumed by reports/daily_report.py --candle-dir.

Usage:
    python scripts/fetch_daily_candles.py [YYYY-MM-DD]
    python scripts/fetch_daily_candles.py --backfill --from 2026-05-08 --to 2026-05-18

Output:
    data_store/candles/candle_data_YYYY-MM-DD.csv
"""
from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_PATH  = ROOT / "data_store" / "session" / "zerodha_token.json"
OUTPUT_DIR  = ROOT / "data_store" / "candles"
DB_PATH     = ROOT / "data_store" / "trading_system.db"

API_KEY = "pvahsvuu3xjsefc7"


def _get_traded_symbols(date_iso: str) -> list[str]:
    """Return distinct symbols from PROCESSED signals on date_iso."""
    import sqlite3
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT symbol FROM signals "
        "WHERE status = 'PROCESSED' AND date(triggered_at) = ?",
        (date_iso,),
    )
    symbols = [row[0] for row in cur.fetchall()]
    conn.close()
    return symbols


def _parse_args(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="fetch_daily_candles")
    parser.add_argument("date", nargs="?", default=None, help="Single date YYYY-MM-DD")
    parser.add_argument("--backfill", action="store_true", help="Backfill mode: fetch for a date range")
    parser.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD", help="Backfill start date")
    parser.add_argument("--to", dest="to_date", metavar="YYYY-MM-DD", help="Backfill end date")
    return parser.parse_args(argv)


def _trading_days_in_range(start: str, end: str) -> list[str]:
    """Return list of weekday dates (Mon-Fri) between start and end inclusive."""
    from datetime import timedelta
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    days = []
    current = s
    while current <= e:
        if current.weekday() < 5:
            days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return days


def _fetch_single_day(trade_date: str, kite, inst_map: dict) -> None:
    """Fetch candles for a single trading day and store to CSV + DB."""
    symbols = _get_traded_symbols(trade_date)
    if not symbols:
        print(f"  {trade_date}: No PROCESSED signals — skipping.")
        return
    print(f"  {trade_date}: {len(symbols)} symbols")

    from_dt = datetime.strptime(trade_date, "%Y-%m-%d").replace(hour=9, minute=0)
    to_dt   = datetime.strptime(trade_date, "%Y-%m-%d").replace(hour=15, minute=31)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / f"candle_data_{trade_date}.csv"

    all_rows: list[dict] = []
    failed: list[str] = []

    for i, symbol in enumerate(symbols, 1):
        token = inst_map.get(symbol)
        if not token:
            print(f"    [{i:3d}/{len(symbols)}] {symbol:<20} — NOT FOUND in NSE instruments")
            failed.append(symbol)
            continue
        try:
            candles = kite.historical_data(
                instrument_token=token,
                from_date=from_dt,
                to_date=to_dt,
                interval="minute",
            )
            if candles:
                for c in candles:
                    all_rows.append({
                        "symbol":   symbol,
                        "datetime": c["date"].strftime("%Y-%m-%d %H:%M:%S"),
                        "open":     c["open"],
                        "high":     c["high"],
                        "low":      c["low"],
                        "close":    c["close"],
                        "volume":   c["volume"],
                    })
                print(f"    [{i:3d}/{len(symbols)}] {symbol:<20} — {len(candles)} candles")
            else:
                print(f"    [{i:3d}/{len(symbols)}] {symbol:<20} — no data")
                failed.append(symbol)
        except Exception as e:
            print(f"    [{i:3d}/{len(symbols)}] {symbol:<20} — ERROR: {e}")
            failed.append(symbol)

        time.sleep(0.35)

    if all_rows:
        with open(output_file, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["symbol", "datetime", "open", "high", "low", "close", "volume"]
            )
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"  {trade_date}: Saved {len(all_rows)} rows -> {output_file.name}")
        _insert_into_candles_db(all_rows, inst_map)
    else:
        print(f"  {trade_date}: No candle data fetched.")

    if failed:
        print(f"  {trade_date}: Failed ({len(failed)}): {', '.join(failed)}")


def main(argv=None) -> None:
    args = _parse_args(argv)

    if args.backfill:
        if not args.from_date or not args.to_date:
            print("ERROR: --backfill requires --from and --to")
            sys.exit(1)
        dates = _trading_days_in_range(args.from_date, args.to_date)
        print(f"\nCandle Backfill — {len(dates)} trading days ({args.from_date} to {args.to_date})")
    else:
        trade_date = args.date or datetime.now().strftime("%Y-%m-%d")
        dates = [trade_date]
        print(f"\nCandle Fetcher (VM) — Date: {trade_date}")

    print("=" * 55)

    if not TOKEN_PATH.exists():
        print(f"ERROR: Token not found at {TOKEN_PATH}")
        sys.exit(1)

    with open(TOKEN_PATH) as f:
        access_token = json.load(f).get("access_token")
    print(f"Token loaded: {access_token[:8]}...")

    try:
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(access_token)
        profile = kite.profile()
        print(f"Connected: {profile['user_id']} — {profile['user_name']}")
    except Exception as e:
        print(f"ERROR connecting to Zerodha: {e}")
        sys.exit(1)

    print("Loading instrument map...")
    instruments = kite.instruments("NSE")
    inst_map = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}

    for d in dates:
        _fetch_single_day(d, kite, inst_map)

    print(f"\nDone. Processed {len(dates)} day(s).")


def _insert_into_candles_db(rows: list[dict], inst_map: dict[str, int]) -> None:
    """Insert fetched candle rows into the candles DB table (v14 schema)."""
    import sqlite3
    if not DB_PATH.exists():
        print("WARNING: DB not found, skipping candles DB insert")
        return
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    inserted = 0
    for row in rows:
        token = inst_map.get(row["symbol"], 0)
        try:
            cur.execute(
                "INSERT OR IGNORE INTO candles "
                "(symbol, instrument_token, ts, interval_sec, open, high, low, close, volume, is_synthetic) "
                "VALUES (?, ?, ?, 60, ?, ?, ?, ?, ?, 0)",
                (
                    row["symbol"],
                    token,
                    row["datetime"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    print(f"Saved DB: {inserted} candle rows inserted into candles table")


if __name__ == "__main__":
    main()
