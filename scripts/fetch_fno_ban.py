"""
scripts/fetch_fno_ban.py -- Trading System v2  FIX-136 Item 44
                            (endpoint fix + severity downgrade, 22-Jun-2026)

Purpose:
    Fetch today's NSE F&O ban list and store it in the DB.
    Stocks in F&O ban cannot have NEW F&O positions opened.
    Cash equity (NSE-EQ) trades are unaffected.

    Runs daily at 08:35 IST (before market open).

Source (verified live 22-Jun-2026):
    NSE Clearing's daily "securities in ban" CSV — the same file clearing
    members consume:
        https://nsearchives.nseindia.com/content/fo/fo_secban.csv

    The old JSON endpoint (nseindia.com/api/live-analysis-banned) was retired
    and now returns 404.

    CSV format:
        Line 1 (header): "Securities in Ban For Trade Date DD-MMM-YYYY:"
        Lines 2..N     : "<serial>,<SYMBOL>"
        No bans today  : header line only (no data rows)

    nseindia.com /api/ paths are bot-blocked without browser headers; the
    nsearchives CSV path is more permissive but we still send a real
    User-Agent + Accept to be safe.

Failure policy — FAIL-OPEN for EQ (22-Jun-2026 change):
    The ban list currently has NO runtime consumer that gates NSE-EQ entries
    (is_symbol_fno_banned() is referenced only by tests). A fetch failure must
    therefore NOT block trading and must NOT page anyone. On ANY soft failure
    (404, timeout, network, HTML block page, stale CSV date) we:
        * log a WARNING (not CRITICAL),
        * send a WARN alert (Telegram only — NO critical sentinel, NO email),
        * record the heartbeat, and
        * exit 0 — the job RAN; the data was just unavailable.
    This keeps Cron Officer's "failed" count for genuinely broken jobs.

    The fail_closed knob (config: fno_ban.fail_closed, default OFF) preserves
    the old conservative "block all F&O" sentinel behaviour for a future F&O
    era — flip it ON only when F&O trading is enabled.

Exit codes:
    0 -- success, no bans, OR soft failure (WARN; fail-open)
    1 -- hard error only (e.g. cannot open the state store)
"""
from __future__ import annotations

import argparse
import logging
import re
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

# NSE Clearing daily securities-in-ban CSV (replaces the dead /api/ JSON endpoint).
_DEFAULT_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"

_EXPECTED_HEADER_PREFIX = "Securities in Ban For Trade Date"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,text/plain,*/*",
}

_MONTHS = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05", "JUN": "06",
    "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}
_DATE_RE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})")


# ──────────────────────────────────────────────────────────────────────────────
# Parse + fetch
# ──────────────────────────────────────────────────────────────────────────────

def _header_date_iso(header: str) -> Optional[str]:
    """Extract the trade date from the CSV header as YYYY-MM-DD, or None.

    Locale-independent: uses an explicit month map rather than strptime("%b").
    """
    m = _DATE_RE.search(header)
    if not m:
        return None
    day, mon, year = m.group(1), m.group(2).upper(), m.group(3)
    mm = _MONTHS.get(mon)
    if mm is None:
        return None
    return f"{year}-{mm}-{int(day):02d}"


def parse_secban_csv(text: str) -> tuple[Optional[str], list[str]]:
    """Parse the NSE secban CSV text into (trade_date_iso, [symbols]).

    Pure function — no I/O. Raises RuntimeError on anything that is not a
    well-formed secban CSV (empty body, HTML error/block page, unexpected
    header) so the caller can treat it as a soft fetch failure.

    The no-ban case (header only) returns (date, []), which is valid.
    """
    if text is None or not text.strip():
        raise RuntimeError("empty response body")

    head_probe = text.lstrip()[:200].lower()
    if head_probe.startswith("<") or "<!doctype" in head_probe or "<html" in head_probe:
        raise RuntimeError("got an HTML page, not CSV (NSE may be blocking the request)")

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("no non-empty lines in response")

    header = lines[0]
    if not header.startswith(_EXPECTED_HEADER_PREFIX):
        raise RuntimeError(f"unexpected header (schema may have changed): {header[:80]!r}")

    trade_date = _header_date_iso(header)

    symbols: list[str] = []
    for row in lines[1:]:
        parts = [p.strip() for p in row.split(",")]
        # Expected data row: "<serial>,<SYMBOL>". Skip anything malformed.
        if len(parts) >= 2 and parts[1]:
            symbols.append(parts[1].upper())

    return trade_date, symbols


def fetch_fno_ban_symbols(
    log: logging.Logger,
    url: str = _DEFAULT_URL,
    timeout_sec: float = 15.0,
) -> tuple[Optional[str], list[str]]:
    """Fetch + parse the NSE F&O ban CSV.

    Returns (trade_date_iso, [symbols]). Raises on any failure (the caller
    decides policy — see main()).
    """
    session = requests.Session()
    session.headers.update(_BROWSER_HEADERS)
    resp = session.get(url, timeout=timeout_sec)
    resp.raise_for_status()

    trade_date, symbols = parse_secban_csv(resp.text)
    log.info(
        "fetch_fno_ban.fetched",
        extra={"count": len(symbols), "trade_date": trade_date, "url": url},
    )
    return trade_date, symbols


# ──────────────────────────────────────────────────────────────────────────────
# Store + query
# ──────────────────────────────────────────────────────────────────────────────

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
    """Store the fail-closed sentinel — marks ALL F&O symbols banned for the day.

    Only used when config fno_ban.fail_closed is ON (default OFF). Kept for a
    future F&O era. Logs at WARNING (not CRITICAL): even when ON, this gates
    only F&O symbols via is_symbol_fno_banned(); NSE-EQ is never affected.
    """
    now_str = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO fno_ban (symbol, ban_date, fetched_at) VALUES (?, ?, ?)",
            (_FETCH_FAILED_SENTINEL, ban_date, now_str),
        )
    log.warning(
        "fetch_fno_ban.sentinel_stored (fail_closed ON): F&O symbols treated as banned for %s",
        ban_date,
    )


def is_symbol_fno_banned(store: StateStore, symbol: str, date: Optional[str] = None) -> bool:
    """Check if a symbol is in today's F&O ban list.

    If the fetch-failed sentinel exists for this date (fail_closed mode), ALL
    symbols are considered banned.
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


# ──────────────────────────────────────────────────────────────────────────────
# CLI / orchestration
# ──────────────────────────────────────────────────────────────────────────────

def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="fetch_fno_ban",
        description="Fetch the NSE F&O ban list (CSV) and store it in the DB.",
    )
    parser.add_argument("--db", metavar="PATH", default=None)
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--url", metavar="URL", default=None)
    return parser.parse_args(argv)


def _send_alert(log: logging.Logger, message: str, level: str = "WARNING") -> None:
    """Best-effort Telegram alert at the given level.

    WARNING (the default here) goes via the INFO/WARN tier: Telegram-only,
    dropped on failure, and — crucially — NO critical sentinel is written, so
    the VM alert-watcher does NOT turn it into an email.
    """
    try:
        from alerts.telegram_notifier import TelegramNotifier

        notifier = TelegramNotifier.from_env()
        if notifier:
            notifier.send_alert(message, level=level)
    except Exception as exc:
        log.warning("fetch_fno_ban: telegram alert failed: %s", exc)


def _soft_fail(
    log: logging.Logger,
    store: StateStore,
    ban_date: str,
    fail_closed: bool,
    reason: str,
) -> int:
    """Handle a non-fatal fetch/parse failure: WARN, fail-open, exit 0.

    EQ trading is unaffected. If fail_closed is ON (future F&O), also write the
    conservative sentinel so F&O symbols are blocked.
    """
    log.warning("fetch_fno_ban.soft_fail: %s", reason)
    if fail_closed:
        try:
            store_fetch_failed_sentinel(store, ban_date, log)
        except Exception as exc:
            log.error("fetch_fno_ban: sentinel store failed: %s", exc)
    msg = (
        f"F&O ban list fetch failed ({reason}). NSE-EQ trading unaffected. "
        f"If trading F&O today, verify the ban list manually at nseclearing.in."
    )
    _send_alert(log, msg, level="WARNING")
    store.close()
    return 0


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

    url = _DEFAULT_URL
    fail_closed = False
    try:
        from core.config_loader import load_all

        cfg = load_all(Path("config"))
        url = cfg.system.fno_ban.url
        fail_closed = cfg.system.fno_ban.fail_closed
    except Exception:
        pass  # defaults: new CSV endpoint, fail-open

    if args.url:
        url = args.url

    # ── Fetch (soft-fail on any error) ────────────────────────────────────
    try:
        trade_date, symbols = fetch_fno_ban_symbols(log, url=url)
    except Exception as exc:
        return _soft_fail(log, store, ban_date, fail_closed, reason=str(exc))

    if args.dry_run:
        print(f"Trade date in CSV: {trade_date}")
        for sym in symbols:
            print(f"  BAN: {sym}")
        print(f"Dry run: {len(symbols)} symbol(s) (not stored)")
        store.close()
        return 0

    # ── Freshness guard: CSV must be for today (else stale → WARN, no store) ──
    if trade_date != ban_date:
        return _soft_fail(
            log,
            store,
            ban_date,
            fail_closed,
            reason=f"stale CSV date {trade_date!r} != today {ban_date!r}",
        )

    # ── Success ───────────────────────────────────────────────────────────
    stored = store_fno_ban(store, symbols, ban_date, log)
    log.info(
        "fetch_fno_ban.complete",
        extra={"date": ban_date, "stored": stored, "symbols": symbols},
    )
    store.close()
    return 0


def _cron_main(argv=None) -> int:
    """Cron entry: holiday-skip + heartbeat + per-job alert (TASK #3)."""
    from utils.cron_heartbeat import HeartbeatTimer, skip_if_non_trading_day

    if skip_if_non_trading_day("fetch_fno_ban"):
        return 0
    timer = HeartbeatTimer("fetch_fno_ban", alert=True)
    with timer:
        rc = main(argv)
        rc = 0 if rc is None else rc
        if rc != 0:
            timer.status = "FAILED"
            timer.message = f"exit code {rc}"
    return rc


if __name__ == "__main__":
    sys.exit(_cron_main())
