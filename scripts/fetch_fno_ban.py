"""
scripts/fetch_fno_ban.py -- Trading System v2  FIX-136 Item 44

Purpose:
    Fetch today's NSE F&O ban list and store in DB.
    Stocks in F&O ban cannot have new F&O positions opened.
    Cash equity trades are unaffected.

    Run daily at 08:30 IST (before market open).

    FAIL-CLOSED: If API fetch fails or returns unexpected schema,
    a sentinel row (symbol="__FETCH_FAILED__") is stored. The runtime
    query function treats this as "all F&O symbols banned" for the day,
    forcing manual intervention before F&O trading resumes.

Exit codes:
    0 -- success (or no bans today)
    1 -- fetch failed (sentinel stored, CRITICAL alert)
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

import requests

from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist, today_ist

_FETCH_FAILED_SENTINEL = "__FETCH_FAILED__"


def fetch_fno_ban_symbols(
    log: logging.Logger,
    url: str = "https://www.nseindia.com/api/live-analysis-banned",
    min_expected_fields: int = 2,
) -> list[str]:
    """
    Fetch F&O ban list from NSE API.
    Returns list of banned symbol names.
    Raises RuntimeError on any failure (caller decides policy).
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    })
    session.get("https://www.nseindia.com", timeout=10)
    resp = session.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if not isinstance(data, (list, dict)):
        raise RuntimeError(f"Unexpected response type: {type(data).__name__}")

    items = data if isinstance(data, list) else data.get("data", [])
    if not isinstance(items, list):
        raise RuntimeError(f"Expected list in response, got {type(items).__name__}")

    symbols = []
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError(f"Non-dict item in response: {type(item).__name__}")
        if len(item) < min_expected_fields:
            raise RuntimeError(
                f"Item has {len(item)} fields, expected >= {min_expected_fields}: {item}"
            )
        sym = (item.get("symbol") or item.get("Symbol") or "").strip()
        if sym:
            symbols.append(sym)

    if len(items) > 0 and len(symbols) == 0:
        raise RuntimeError(
            f"Response had {len(items)} items but 0 valid symbols — schema may have changed"
        )

    log.info("fetch_fno_ban.fetched", extra={"count": len(symbols), "url": url})
    return symbols


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


def store_fetch_failed_sentinel(
    store: StateStore,
    ban_date: str,
    log: logging.Logger,
) -> None:
    """Store sentinel row indicating fetch failure — triggers fail-closed behavior."""
    now_str = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO fno_ban (symbol, ban_date, fetched_at) VALUES (?, ?, ?)",
            (_FETCH_FAILED_SENTINEL, ban_date, now_str),
        )
    log.critical("fetch_fno_ban.sentinel_stored: F&O signals will be BLOCKED for %s", ban_date)


def is_symbol_fno_banned(store: StateStore, symbol: str, date: Optional[str] = None) -> bool:
    """Check if a symbol is in today's F&O ban list.

    If the fetch-failed sentinel exists for this date, ALL symbols are
    considered banned (fail-closed).
    """
    date = date or today_ist()
    sentinel = store.fetch_one(
        "SELECT 1 FROM fno_ban WHERE symbol = ? AND ban_date = ?",
        (_FETCH_FAILED_SENTINEL, date),
    )
    if sentinel is not None:
        return True
    row = store.fetch_one(
        "SELECT 1 FROM fno_ban WHERE symbol = ? AND ban_date = ?",
        (symbol, date),
    )
    return row is not None


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="fetch_fno_ban",
        description="FIX-136: Fetch NSE F&O ban list and store in DB.",
    )
    parser.add_argument("--db", metavar="PATH", default=None)
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--url", metavar="URL", default=None)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("fetch_fno_ban")

    db_path = Path(args.db) if args.db else _ROOT / "data_store" / "trading_system.db"
    try:
        store = StateStore(db_path=db_path)
    except Exception as exc:
        log.error("fetch_fno_ban: state_store open failed: %s", exc)
        return 1

    ban_date = args.date or today_ist()

    url = args.url or "https://www.nseindia.com/api/live-analysis-banned"
    try:
        from core.config_loader import load_all
        cfg = load_all(Path("config"))
        url = cfg.system.fno_ban.url
        min_fields = cfg.system.fno_ban.min_expected_fields
        fail_closed = cfg.system.fno_ban.fail_closed
    except Exception:
        min_fields = 2
        fail_closed = True

    if args.url:
        url = args.url

    try:
        symbols = fetch_fno_ban_symbols(log, url=url, min_expected_fields=min_fields)
    except Exception as exc:
        log.critical("fetch_fno_ban.FETCH_FAILED: %s", exc)
        if fail_closed:
            store_fetch_failed_sentinel(store, ban_date, log)
        _send_critical_alert(log, f"F&O ban fetch FAILED: {exc}. F&O signals BLOCKED for {ban_date}.")
        store.close()
        return 1

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


def _send_critical_alert(log: logging.Logger, message: str) -> None:
    """Best-effort Telegram CRITICAL alert."""
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env()
        if notifier:
            notifier.send_critical(message)
    except Exception as exc:
        log.warning("fetch_fno_ban: telegram alert failed: %s", exc)


if __name__ == "__main__":
    sys.exit(main())
