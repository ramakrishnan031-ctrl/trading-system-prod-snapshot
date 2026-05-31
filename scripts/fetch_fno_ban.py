"""
scripts/fetch_fno_ban.py -- Trading System v2  FIX-135 Item 44

Purpose:
    Fetch today's NSE F&O ban list and store in DB.
    Stocks in F&O ban cannot have new F&O positions opened.
    Cash equity trades are unaffected.

    Run daily at 08:30 IST (before market open).

Exit codes:
    0 -- success (or no bans today)
    1 -- error during execution
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist, today_ist

_NSE_FNO_BAN_URL = "https://www.nseindia.com/api/fo-ban-underlyings-tool"


def fetch_fno_ban_symbols(log: logging.Logger) -> list[str]:
    """
    Fetch F&O ban list from NSE API.
    Returns list of banned symbol names.
    Best-effort: returns empty list on any failure.
    """
    try:
        import requests
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })
        session.get("https://www.nseindia.com", timeout=10)
        resp = session.get(_NSE_FNO_BAN_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        symbols = []
        for item in data.get("data", []):
            sym = item.get("symbol", "").strip()
            if sym:
                symbols.append(sym)
        log.info("fetch_fno_ban.fetched", extra={"count": len(symbols)})
        return symbols
    except Exception as exc:
        log.error("fetch_fno_ban.fetch_failed: %s", exc)
        return []


def store_fno_ban(
    store: StateStore,
    symbols: list[str],
    ban_date: str,
    log: logging.Logger,
) -> int:
    """Store banned symbols in DB. Returns count stored."""
    now_str = now_ist().isoformat()
    count = 0
    for sym in symbols:
        try:
            with store.transaction() as cur:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO fno_ban
                      (symbol, ban_date, fetched_at)
                    VALUES (?, ?, ?)
                    """,
                    (sym, ban_date, now_str),
                )
            count += 1
        except Exception as exc:
            log.error("fetch_fno_ban.store_failed: %s sym=%s", exc, sym)
    return count


def is_symbol_fno_banned(store: StateStore, symbol: str, date: Optional[str] = None) -> bool:
    """Check if a symbol is in today's F&O ban list."""
    date = date or today_ist()
    row = store.fetch_one(
        "SELECT 1 FROM fno_ban WHERE symbol = ? AND ban_date = ?",
        (symbol, date),
    )
    return row is not None


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="fetch_fno_ban",
        description="FIX-135: Fetch NSE F&O ban list and store in DB.",
    )
    parser.add_argument("--db", metavar="PATH", default=None)
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("fetch_fno_ban")

    db_path = Path(args.db) if args.db else Path("data_store") / "trading.db"
    try:
        store = StateStore(db_path=db_path)
    except Exception as exc:
        log.error("fetch_fno_ban: state_store open failed: %s", exc)
        return 1

    ban_date = args.date or today_ist()
    symbols = fetch_fno_ban_symbols(log)
    log.info("fetch_fno_ban.start", extra={"date": ban_date, "count": len(symbols)})

    if args.dry_run:
        for sym in symbols:
            print(f"  BAN: {sym}")
        print(f"Dry run: {len(symbols)} symbols (not stored)")
        store.close()
        return 0

    stored = store_fno_ban(store, symbols, ban_date, log)
    log.info("fetch_fno_ban.complete", extra={"stored": stored})
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
