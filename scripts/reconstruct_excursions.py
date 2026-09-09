"""
scripts/reconstruct_excursions.py — Trading System v2 · MFE/MAE (Option B)

Post-EOD reconstruction of per-trade MFE/MAE into the `trade_excursions` table.
Runs AFTER the 15:40 candle backfill (scripts/fetch_daily_candles.py). For each
closed trade in scope:
    1. ensure_candles(symbol, date)  — fetch-if-missing via kite.historical_data
       (PAPER + LIVE parity: both read data_store/session/zerodha_token.json)
    2. compute MFE/MAE via StateStore.compute_trade_excursions (datetime-safe;
       the old lexical `ts BETWEEN` silently matched zero candles — root cause)
    3. INSERT OR REPLACE trade_excursions   (idempotent / resume-safe)

OUTCOME TAXONOMY — every examined trade resolves to EXACTLY one of:
    WRITTEN                       — a trade_excursions row was written (in a
                                    dry-run: WOULD be written from in-table candles).
    WOULD_FETCH                   — DRY-RUN ONLY, non-alarming preview: candles
                                    are absent but fetchable — the live run will
                                    fetch + write. (A real run never returns this;
                                    it either WRITES after a fetch or FAILS.)
    SKIPPED_UNRECONSTRUCTABLE     — PERMANENT, non-writable, non-alarming:
                                    NULL/invalid entry/exit/price, OR a sub-minute
                                    window shorter than the 1-min candle
                                    granularity (candles present, none in-window).
    FAILED                        — transient/unexpected, ALARMING + retryable:
                                    compute or insert threw, OR candles are
                                    unavailable (no rows; fetch failed / no token).

HARD GUARD:  written + would_fetch + skipped + failed == examined
    A broken identity means a trade vanished silently → the run FAILS loudly.
    (A real run never produces WOULD_FETCH, so its guard is written+skipped+failed.)
Run status:  FAILED iff failed>0 OR the identity breaks;
             OK_WITH_NOTES iff skipped>0 OR would_fetch>0 (and no failures);
             OK otherwise.
Exit code:   0 for OK / OK_WITH_NOTES; 1 for FAILED. One audit row per real run
             is written to excursion_reconstruction_runs.

Usage:
    python scripts/reconstruct_excursions.py --daily [--dry-run]
    python scripts/reconstruct_excursions.py --date 2026-06-25 [--dry-run]
    python scripts/reconstruct_excursions.py --all-closed [--dry-run]   # historical backfill
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # cron heartbeat import works without PYTHONPATH=.
    sys.path.insert(0, str(ROOT))

# C-1 (02-Jul): load .env so the api_key resolves under cron (matches
# scripts/reconcile_positions.py / auto_refresh_token.py). Double-load is a no-op.
from dotenv import load_dotenv
from core.account_registry import primary_api_key, primary_api_key_env
load_dotenv(ROOT / ".env")

DB_PATH = ROOT / "data_store" / "trading_system.db"
TOKEN_PATH = ROOT / "data_store" / "session" / "zerodha_token.json"
MARKER_PATH = ROOT / "data_store" / "cron_marks" / "fetch_daily_candles.done"
LOCK_PATH = ROOT / "data_store" / "locks" / "reconstruct_excursions.lock"

# C-1 (02-Jul): api_key read from env (.env), NEVER hardcoded — survives a future
# api_key rotation and never re-exposes a secret.
API_KEY = primary_api_key()
JOB_NAME = "reconstruct_excursions"

# Outcome labels (also the audit-row semantics).
WRITTEN = "WRITTEN"           # a row was (or in dry-run, WOULD be) written
WOULD_FETCH = "WOULD_FETCH"   # DRY-RUN ONLY preview: candles absent, the live run would fetch + write
SKIPPED = "SKIPPED_UNRECONSTRUCTABLE"
FAILED = "FAILED"

# A sub-minute-skip count above this raises a CRITICAL sentinel (1.3): many
# trades shorter than the 1-min candle granularity suggests something systemic
# (candle gaps / clock). NULL-exit skips are NOT counted here (J4).
SUB_MINUTE_ALERT_THRESHOLD = 5

_CLOSED_STATUSES = ("CLOSED", "CLOSED_MANUAL")

# A fetcher takes (symbol, date_iso) and returns (instrument_token, [kite rows])
# or None when candles cannot be fetched (no token / no data).
Fetcher = Callable[[str, str], Optional[Tuple[int, list]]]


# ─────────────────────────────────────────────────────────────────────────────
# Candle availability — fetch-if-missing (PAPER + LIVE parity)
# ─────────────────────────────────────────────────────────────────────────────

def ensure_candles(store, symbol: str, dates: List[str], *, fetcher: Optional[Fetcher], log) -> int:
    """Ensure candles exist for `symbol` on each date; fetch-if-missing.

    Returns the total candle count across `dates` after any fetch. Writes use the
    existing space-format `store.insert_candle` (INSERT OR IGNORE, idempotent) —
    candle STORAGE format is intentionally unchanged. When `fetcher` is None
    (dry-run, or no broker token) nothing is fetched and the current count stands.
    """
    total = 0
    for d in dates:
        n = store.fetch_one(
            "SELECT COUNT(*) AS c FROM candles WHERE symbol = ? AND date = ?", (symbol, d)
        )["c"]
        if n == 0 and fetcher is not None:
            try:
                fetched = fetcher(symbol, d)
            except Exception as exc:  # fetch is best-effort; a failure → trade FAILED upstream
                log.warning("reconstruct.fetch_failed symbol=%s date=%s err=%s", symbol, d, exc)
                fetched = None
            if fetched:
                token, rows = fetched
                for c in rows:
                    cdate = c.get("date")
                    ts = cdate.strftime("%Y-%m-%d %H:%M:%S") if hasattr(cdate, "strftime") else str(cdate)
                    store.insert_candle(
                        symbol=symbol,
                        instrument_token=token,
                        ts=ts,
                        interval_sec=60,
                        open_=c["open"], high=c["high"], low=c["low"], close=c["close"],
                        volume=int(c.get("volume", 0) or 0),
                        is_synthetic=0,
                    )
                n = store.fetch_one(
                    "SELECT COUNT(*) AS c FROM candles WHERE symbol = ? AND date = ?", (symbol, d)
                )["c"]
        total += n
    return total


# ─────────────────────────────────────────────────────────────────────────────
# Per-trade reconstruction + run loop (pure; testable with a fake store/fetcher)
# ─────────────────────────────────────────────────────────────────────────────

def reconstruct_one(store, trade, *, fetcher: Optional[Fetcher], dry_run: bool, log) -> Tuple[str, str]:
    """Reconstruct one trade. Returns (outcome, note). Never raises."""
    tid = trade["trade_id"]
    sym = trade["symbol"]
    et = trade["entry_time"]
    xt = trade["exit_time"]
    ep = trade["entry_actual_price"]

    # Permanent skips (no window / no valid entry) — these never reconstruct.
    if not et or not xt:
        log.warning(
            "reconstruct.skip_unreconstructable trade=%s symbol=%s reason=missing_time entry=%r exit=%r",
            tid, sym, et, xt,
        )
        return SKIPPED, "missing entry/exit time"
    if not ep or ep <= 0:
        log.warning(
            "reconstruct.skip_unreconstructable trade=%s symbol=%s reason=bad_entry_price price=%r",
            tid, sym, ep,
        )
        return SKIPPED, "missing/invalid entry price"

    dates = sorted({str(et)[:10], str(xt)[:10]})
    try:
        present = ensure_candles(store, sym, dates, fetcher=fetcher, log=log)
        exc_data = store.compute_trade_excursions(tid)
    except Exception as exc:  # noqa: BLE001 — classify, never abort the run
        log.error("reconstruct.failed trade=%s symbol=%s err=%s", tid, sym, exc, exc_info=True)
        return FAILED, f"exception: {exc.__class__.__name__}: {exc}"

    if exc_data:
        if not dry_run:
            try:
                store.insert_trade_excursion(trade_id=tid, **exc_data)
            except Exception as exc:  # noqa: BLE001
                log.error("reconstruct.insert_failed trade=%s symbol=%s err=%s", tid, sym, exc, exc_info=True)
                return FAILED, f"insert exception: {exc.__class__.__name__}: {exc}"
        log.info(
            "reconstruct.%s trade=%s symbol=%s mfe_pct=%s mae_pct=%s",
            "would_write" if dry_run else "written", tid, sym,
            exc_data.get("mfe_pct"), exc_data.get("mae_pct"),
        )
        return WRITTEN, ""

    # compute returned None — three sub-cases:
    #  (a) candles present for the date but none inside the window → PERMANENT
    #      sub-minute skip (trade shorter than the 1-min candle granularity);
    #  (b) candles absent, dry-run → WOULD_FETCH preview (live run will fetch);
    #  (c) candles absent, real run → FAILED (we tried / had no token).
    if present > 0:
        log.warning(
            "reconstruct.sub_minute_window trade=%s symbol=%s window=[%s..%s] candles_for_dates=%d",
            tid, sym, et, xt, present,
        )
        return SKIPPED, "sub-minute window: shorter than 1-min candle granularity (candles present, none inside [entry,exit])"

    if dry_run:
        log.info(
            "reconstruct.would_fetch trade=%s symbol=%s dates=%s — live run would fetch + write",
            tid, sym, dates,
        )
        return WOULD_FETCH, "candles absent — live run would fetch + write"

    log.warning(
        "reconstruct.candles_unavailable trade=%s symbol=%s dates=%s — FAILED (retryable on a trading day)",
        tid, sym, dates,
    )
    return FAILED, "candles unavailable (no rows; fetch failed or no token)"


def run(store, trades, *, fetcher: Optional[Fetcher], dry_run: bool, log) -> dict:
    """Reconstruct all `trades`. Returns a stats dict (incl. the hard-guard flag)."""
    examined = len(trades)
    written = would_fetch = skipped = failed = sub_minute = 0
    failures: List[Tuple[str, str, str]] = []

    for t in trades:
        outcome, note = reconstruct_one(store, t, fetcher=fetcher, dry_run=dry_run, log=log)
        if outcome == WRITTEN:
            written += 1
        elif outcome == WOULD_FETCH:
            would_fetch += 1
        elif outcome == SKIPPED:
            skipped += 1
            if note.startswith("sub-minute window"):
                sub_minute += 1
        else:  # FAILED
            failed += 1
            failures.append((t["trade_id"], t["symbol"], note))

    return {
        "examined": examined,
        "written": written,
        "would_fetch": would_fetch,
        "skipped": skipped,
        "failed": failed,
        "sub_minute": sub_minute,
        "guard_ok": (written + would_fetch + skipped + failed == examined),
        "failures": failures,
    }


def select_trades(store, *, all_closed: bool, date_iso: Optional[str]) -> list:
    """Closed trades (CLOSED / CLOSED_MANUAL) in scope. Daily mode keys on the
    exit DATE (so NULL-exit trades surface only under --all-closed, where they
    are legitimately SKIPPED rather than silently absent from every daily run)."""
    cols = ("trade_id, symbol, direction, entry_actual_price, entry_time, exit_time")
    if all_closed:
        return list(store.fetch_all(
            f"SELECT {cols} FROM trades WHERE status IN ('CLOSED','CLOSED_MANUAL') "
            "ORDER BY exit_time"
        ))
    return list(store.fetch_all(
        f"SELECT {cols} FROM trades WHERE status IN ('CLOSED','CLOSED_MANUAL') "
        "AND substr(COALESCE(exit_time,''),1,10) = ? ORDER BY exit_time",
        (date_iso,),
    ))


def summarize(stats: dict, *, mode: str, date_iso: Optional[str], dry_run: bool) -> str:
    """One-line human summary (audit notes + stdout + heartbeat message)."""
    base = (
        f"mode={mode} date={date_iso or 'ALL'} dry_run={dry_run} "
        f"examined={stats['examined']} written={stats['written']} "
        f"would_fetch={stats['would_fetch']} "
        f"skipped_unreconstructable={stats['skipped']} (sub_minute={stats['sub_minute']}) "
        f"failed={stats['failed']} guard_ok={stats['guard_ok']}"
    )
    if stats["failures"]:
        head = "; ".join(f"{tid}/{sym}:{note}" for tid, sym, note in stats["failures"][:5])
        base += f" | first_failures: {head}"
    return base


# ─────────────────────────────────────────────────────────────────────────────
# I/O glue — kite fetcher, lock-file, backfill marker, alerts, heartbeat
# ─────────────────────────────────────────────────────────────────────────────

def _build_fetcher(log) -> Optional[Fetcher]:
    """Build a read-only historical-data fetcher from the broker token file.
    Returns None if the token is absent or the broker connect fails (the caller
    then proceeds with fetch disabled → trades needing candles resolve FAILED,
    retryable on a trading day with a live token). Mirrors fetch_daily_candles."""
    if not TOKEN_PATH.exists():
        log.warning("reconstruct.no_token path=%s — fetch-if-missing disabled", TOKEN_PATH)
        return None
    try:
        import json
        from kiteconnect import KiteConnect

        if not API_KEY:
            raise RuntimeError((primary_api_key_env() or "<no api_key_env in accounts.csv>") + " not set (.env not loaded / var missing)")
        access_token = json.loads(TOKEN_PATH.read_text()).get("access_token")
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(access_token)
        kite.profile()  # fail fast if the token is dead
        inst_map = {i["tradingsymbol"]: i["instrument_token"] for i in kite.instruments("NSE")}
    except Exception as exc:  # noqa: BLE001
        log.warning("reconstruct.broker_connect_failed err=%s — fetch-if-missing disabled", exc)
        return None

    def fetch(symbol: str, date_iso: str):
        token = inst_map.get(symbol)
        if not token:
            log.warning("reconstruct.no_token_for_symbol symbol=%s", symbol)
            return None
        frm = datetime.strptime(date_iso, "%Y-%m-%d").replace(hour=9, minute=0)
        to = datetime.strptime(date_iso, "%Y-%m-%d").replace(hour=15, minute=31)
        rows = kite.historical_data(
            instrument_token=token, from_date=frm, to_date=to, interval="minute"
        )
        return (token, rows) if rows else None

    return fetch


def _may_need_fetch(store, trades) -> bool:
    """True if any in-scope trade has a date with zero candles in the table (so a
    broker fetch is worth building). Keeps the daily path token-independent."""
    for t in trades:
        et, xt = t["entry_time"], t["exit_time"]
        if not et or not xt:
            continue
        for d in sorted({str(et)[:10], str(xt)[:10]}):
            row = store.fetch_one(
                "SELECT COUNT(*) AS c FROM candles WHERE symbol = ? AND date = ?", (t["symbol"], d)
            )
            if row["c"] == 0:
                return True
    return False


def _check_backfill_marker(today_iso: str, log) -> None:
    """Observability gate (L2): log whether the 15:40 backfill succeeded today.
    NEVER blocks — ensure_candles self-heals, so this is an optimisation."""
    if not MARKER_PATH.exists():
        log.warning("reconstruct.backfill_marker_missing path=%s — proceeding (self-heal)", MARKER_PATH)
        return
    try:
        content = MARKER_PATH.read_text().strip()
        rc_ok = content.split()[0] == "0"
        today_ok = today_iso in content
    except Exception:  # noqa: BLE001
        content, rc_ok, today_ok = "<unreadable>", False, False
    if rc_ok and today_ok:
        log.info("reconstruct.backfill_confirmed marker=%r", content)
    else:
        log.warning("reconstruct.backfill_marker_stale_or_failed marker=%r — proceeding (self-heal)", content)


def _acquire_lock(log) -> bool:
    """Single-instance file lock (PID-based, stale-aware). Returns False if a live
    instance already holds it."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                pid = int(LOCK_PATH.read_text().strip() or "0")
            except Exception:  # noqa: BLE001
                pid = 0
            try:
                from utils.instance_lock import _pid_is_alive
                alive = bool(pid) and _pid_is_alive(pid)
            except Exception:  # noqa: BLE001
                alive = bool(pid)
            if alive:
                log.warning("reconstruct.lock_held pid=%s lock=%s — another run active; exiting", pid, LOCK_PATH)
                return False
            log.warning("reconstruct.stale_lock pid=%s — reclaiming", pid)
            try:
                LOCK_PATH.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                return False
    return False


def _release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _alert(status: str, stats: dict, summary: str, log) -> None:
    """Raise a CRITICAL sentinel (alert-watcher → email + Telegram) when the run
    FAILED, the hard guard broke, or 0-candle windows exceed the threshold."""
    guard_broken = not stats["guard_ok"]
    sub_minute_excess = stats["sub_minute"] > SUB_MINUTE_ALERT_THRESHOLD
    if status != "FAILED" and not guard_broken and not sub_minute_excess:
        return
    try:
        from alerts.critical import write_critical_sentinel

        if guard_broken:
            title = "MFE/MAE reconstruction GUARD BROKEN — a trade vanished"
        elif status == "FAILED":
            title = f"MFE/MAE reconstruction FAILED ({stats['failed']} trade(s))"
        else:
            title = f"MFE/MAE reconstruction: {stats['sub_minute']} sub-minute windows"
        write_critical_sentinel(
            title=title,
            body=summary,
            source_module="scripts.reconstruct_excursions",
            context={k: stats[k] for k in ("examined", "written", "would_fetch", "skipped", "failed", "sub_minute", "guard_ok")},
        )
    except Exception as exc:  # noqa: BLE001 — alerting must never change the exit path
        log.error("reconstruct.alert_failed err=%s", exc)


def _record_heartbeat(status: str, summary: str) -> None:
    hb_status = "SUCCESS" if status in ("OK", "OK_WITH_NOTES") else "FAILED"
    try:
        from utils.cron_heartbeat import record_heartbeat
        record_heartbeat(JOB_NAME, status=hb_status, message=summary, db_path=DB_PATH)
    except Exception:  # noqa: BLE001
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args(argv=None):
    p = argparse.ArgumentParser(prog="reconstruct_excursions")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--daily", action="store_true", help="Reconstruct trades closed today (IST).")
    g.add_argument("--date", metavar="YYYY-MM-DD", help="Reconstruct trades closed on this date.")
    g.add_argument("--all-closed", action="store_true", help="Historical backfill: every closed trade.")
    p.add_argument("--dry-run", action="store_true", help="Report only; write nothing (no candles, no excursions, no audit row).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    from core.logger import get_logger
    from core.time_authority import now_ist

    log = get_logger("reconstruct_excursions")
    today_iso = now_ist().date().isoformat()

    if args.all_closed:
        mode, date_iso = "backfill", None
    else:
        mode, date_iso = "daily", (args.date or today_iso)

    if not _acquire_lock(log):
        return 0  # benign: another instance already running

    status = "OK"
    summary = f"mode={mode} date={date_iso or 'ALL'} (no trades processed)"
    stats: Optional[dict] = None
    try:
        if mode == "daily":
            _check_backfill_marker(today_iso, log)

        from core.state_store import StateStore
        store = StateStore(DB_PATH)
        try:
            trades = select_trades(store, all_closed=args.all_closed, date_iso=date_iso)
            log.info("reconstruct.start mode=%s date=%s dry_run=%s trades=%d",
                     mode, date_iso, args.dry_run, len(trades))

            fetcher = None
            if not args.dry_run and trades and _may_need_fetch(store, trades):
                fetcher = _build_fetcher(log)

            started_at = now_ist().isoformat()
            stats = run(store, trades, fetcher=fetcher, dry_run=args.dry_run, log=log)
            completed_at = now_ist().isoformat()

            if not stats["guard_ok"] or stats["failed"] > 0:
                status = "FAILED"
            elif stats["skipped"] > 0 or stats["would_fetch"] > 0:
                status = "OK_WITH_NOTES"
            else:
                status = "OK"

            summary = summarize(stats, mode=mode, date_iso=date_iso, dry_run=args.dry_run)
            log.info("reconstruct.done %s status=%s", summary, status)

            if not args.dry_run:
                store.insert_excursion_reconstruction_run(
                    run_id=uuid.uuid4().hex,
                    mode=mode,
                    started_at=started_at,
                    completed_at=completed_at,
                    trades_examined=stats["examined"],
                    trades_written=stats["written"],
                    trades_skipped_unreconstructable=stats["skipped"],
                    trades_failed=stats["failed"],
                    status=status,
                    notes=summary,
                )
            _alert(status, stats, summary, log)
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 — top-level safety net
        status = "FAILED"
        summary = f"reconstruct_excursions crashed: {exc.__class__.__name__}: {exc}"
        log.error("reconstruct.crash err=%s", exc, exc_info=True)
        try:
            from alerts.critical import write_critical_sentinel
            write_critical_sentinel(
                title="MFE/MAE reconstruction CRASHED",
                body=summary,
                source_module="scripts.reconstruct_excursions",
            )
        except Exception:  # noqa: BLE001
            pass
    finally:
        _release_lock()

    _record_heartbeat(status, summary)
    print(summary)
    return 0 if status in ("OK", "OK_WITH_NOTES") else 1


def _cron_main(argv=None) -> int:
    """Cron entry: S1 holiday-skip, then the real job.

    S1 (2026-07-17): the registry declares this job market_day_only + cadence
    market_day, but NOTHING enforced it at the cron entry — `cadence` only tells
    the Cron Officer not to EXPECT a heartbeat on a holiday; cron still fired the
    job. skip_if_non_trading_day FAILS OPEN (weekday fallback on any calendar
    error) so a trading day is never skipped. The guard is here and not in main()
    so a manual/ad-hoc run on a non-trading day is never blocked.
    """
    from utils.cron_heartbeat import skip_if_non_trading_day

    if skip_if_non_trading_day("reconstruct_excursions"):
        return 0
    return main(argv)


if __name__ == "__main__":
    sys.exit(_cron_main())
