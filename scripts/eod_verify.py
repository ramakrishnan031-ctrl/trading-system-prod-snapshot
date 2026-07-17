"""
scripts/eod_verify.py -- Trading System v2  FIX-137 Item 59

Purpose:
    End-of-day verification: confirms all positions closed, orders settled, and
    (in LIVE, when a broker P&L feeder has run) the broker-vs-system P&L matches.
    Runs at 15:55 IST after cleanup, before the report. Writes to eod_verification;
    sends a Telegram summary.

HONEST-VERIFY (Audit-B Phase-8 + M-SC1, 07-Jul):
    The P&L leg previously queried NON-EXISTENT columns (system_net_pnl/broker_net_pnl;
    real columns are broker_pnl/system_pnl/variance) and swallowed the resulting
    OperationalError into pnl_variance=0.0 → a spurious "no variance" → a FALSE
    "VERIFIED" that never actually checked P&L (11/11 historical rows VERIFIED,
    variance 0.0). Fixed: read the REAL columns AND emit an HONEST verdict — never a
    false VERIFIED. The P&L source (pnl_reconciliation) is populated ONLY by a broker
    feeder (reconcile_pnl un-cronned / P1 not yet authoritative → 0 rows today), so
    today the honest live verdict is P&L NOT CHECKED → overall PENDING.

Verdict (persisted eod_verification.status):
    ISSUES_FOUND — positions OPEN / orders PENDING / a real P&L DIVERGENCE.
    PENDING      — positions & orders clean, but P&L could NOT be checked in LIVE
                   (no broker feeder row yet). NOT a false VERIFIED.
    VERIFIED     — everything checkable is clean AND checked: LIVE with the broker
                   P&L matched, OR PAPER (P&L is N/A — no broker to reconcile against).

Parity (mode-aware, one code path):
    PAPER → P&L = N/A (no broker); positions/orders still verified → VERIFIED (paper)
    or ISSUES_FOUND. Never a false FAIL, never a broker-authoritative VERIFIED.
    LIVE  → the honest state above. `--mode` / TRADING_MODE selects; default live.

Exit codes:
    0 -- VERIFIED or PENDING (job ran; PENDING = known gap until a broker feeder exists)
    2 -- ISSUES_FOUND (a real problem — so cron/monitoring catches it; M-SC1 fix)
    1 -- script error
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist, today_ist

# Rs band on |broker - system| P&L before the P&L dimension is flagged a DIVERGENCE
# (preserves the pre-existing 100.0 threshold).
_PNL_TOLERANCE = 100.0

# P&L sub-verdict values (reported in the result dict + summary; the overall
# eod_verification.status rolls these up).
_PNL_NA = "N/A"                  # paper — no broker to reconcile against
_PNL_NOT_CHECKED = "NOT_CHECKED"  # live — no broker feeder row yet (honest PENDING)
_PNL_VERIFIED = "VERIFIED"        # live — broker P&L compared and within tolerance
_PNL_DIVERGENCE = "DIVERGENCE"    # live — broker P&L compared and OUT of tolerance


def _evaluate_pnl(
    store: StateStore, date_iso: str, is_paper: bool, log: logging.Logger
) -> tuple[str, float]:
    """HONEST P&L verdict — an error or a missing feeder is NEVER a false VERIFIED.

    Returns (pnl_status, pnl_variance). pnl_variance is meaningful ONLY when
    pnl_status is VERIFIED/DIVERGENCE (a real broker comparison); it is 0.0 for
    N/A and NOT_CHECKED (the eod_verification.pnl_variance column is NOT NULL, so
    the status disambiguates a real 0.0 from "not checked").

    PAPER → N/A (no broker). LIVE → read the REAL pnl_reconciliation columns
    (broker_pnl/variance, written by a broker feeder). No row, or a broker-skipped
    row (variance NULL), → NOT_CHECKED (no feeder is live yet — reconcile_pnl
    un-cronned, P1 not authoritative). A real comparison → VERIFIED (≤ tol) or
    DIVERGENCE (> tol).
    """
    if is_paper:
        return _PNL_NA, 0.0
    try:
        row = store.fetch_one(
            "SELECT variance, broker_pnl FROM pnl_reconciliation "
            "WHERE date = ? ORDER BY id DESC LIMIT 1",
            (date_iso,),
        )
    except Exception as exc:  # noqa: BLE001 — a real query error must NOT become a false VERIFIED
        log.error("eod_verify: pnl_reconciliation query failed (%s) -> P&L NOT CHECKED", exc)
        return _PNL_NOT_CHECKED, 0.0
    if row is None or row["broker_pnl"] is None or row["variance"] is None:
        # No broker-P&L feeder row today (or the broker leg was skipped).
        return _PNL_NOT_CHECKED, 0.0
    variance = float(row["variance"])
    if variance > _PNL_TOLERANCE:
        return _PNL_DIVERGENCE, variance
    return _PNL_VERIFIED, variance


def run_eod_verification(
    store: StateStore,
    date_iso: str,
    log: logging.Logger | None = None,
    *,
    is_paper: bool = False,
) -> dict:
    """
    Run all EOD verification checks and persist the HONEST result.

    Returns dict: date, open_trades, pending_orders, pnl_variance, pnl_status,
    mode, status, issues.
    """
    if log is None:
        log = logging.getLogger("eod_verify")

    open_trades = store.fetch_one(
        "SELECT COUNT(*) AS n FROM trades WHERE status IN ('OPEN', 'PARTIAL') "
        "AND substr(created_at, 1, 10) = ?",
        (date_iso,),
    )
    open_count = open_trades["n"] if open_trades else 0

    pending_orders = store.fetch_one(
        "SELECT COUNT(*) AS n FROM orders WHERE status = 'PENDING' "
        "AND substr(placed_at, 1, 10) = ?",
        (date_iso,),
    )
    pending_count = pending_orders["n"] if pending_orders else 0

    pnl_status, pnl_variance = _evaluate_pnl(store, date_iso, is_paper, log)

    issues = []
    if open_count > 0:
        issues.append(f"{open_count} positions still OPEN")
        log.critical("eod_verify: %d positions still OPEN for %s", open_count, date_iso)
    if pending_count > 0:
        issues.append(f"{pending_count} orders still PENDING")
        log.critical("eod_verify: %d orders still PENDING for %s", pending_count, date_iso)
    if pnl_status == _PNL_DIVERGENCE:
        issues.append(f"P&L variance Rs{pnl_variance:.2f}")
        log.warning("eod_verify: P&L variance Rs%.2f for %s", pnl_variance, date_iso)

    # HONEST roll-up — a false VERIFIED is never emitted:
    #   any hard issue                 -> ISSUES_FOUND
    #   live P&L un-checkable (no feed) -> PENDING (positions/orders clean; P&L NOT CHECKED)
    #   else (live matched | paper N/A) -> VERIFIED
    if issues:
        status = "ISSUES_FOUND"
    elif pnl_status == _PNL_NOT_CHECKED:
        status = "PENDING"
    else:
        status = "VERIFIED"

    verified_at = now_ist().isoformat()

    with store.transaction() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO eod_verification "
            "(date, open_trades, pending_orders, pnl_variance, status, verified_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (date_iso, open_count, pending_count, pnl_variance, status, verified_at),
        )

    log.info(
        "eod_verify.complete",
        extra={"date": date_iso, "status": status, "open": open_count,
               "pending": pending_count, "pnl_status": pnl_status,
               "mode": "PAPER" if is_paper else "LIVE"},
    )

    return {
        "date": date_iso,
        "open_trades": open_count,
        "pending_orders": pending_count,
        "pnl_variance": pnl_variance,
        "pnl_status": pnl_status,
        "mode": "PAPER" if is_paper else "LIVE",
        "status": status,
        "issues": issues,
    }


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="eod_verify")
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--db", metavar="PATH", default=None)
    parser.add_argument("--mode", metavar="paper|live", default=None,
                        help="paper|live (default: TRADING_MODE env, else live)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("eod_verify")

    db_path = Path(args.db) if args.db else _ROOT / "data_store" / "trading_system.db"
    try:
        store = StateStore(db_path=db_path)
    except Exception as exc:
        log.error("eod_verify: state_store open failed: %s", exc)
        return 1

    is_paper = (args.mode or os.environ.get("TRADING_MODE", "live")).lower() == "paper"
    date_iso = args.date or today_ist()
    result = run_eod_verification(store, date_iso, log, is_paper=is_paper)

    st = result["status"]
    if st == "ISSUES_FOUND":
        summary = f"EOD ISSUES FOUND: {date_iso} -- {'; '.join(result['issues'])}"
    elif st == "PENDING":
        summary = (f"EOD PENDING: {date_iso} -- positions/orders clear; "
                   f"P&L NOT CHECKED (no broker feeder yet — pending P1)")
    else:  # VERIFIED
        pnl_note = ("P&L N/A (paper)" if is_paper
                    else f"P&L verified (variance Rs{result['pnl_variance']:.2f})")
        summary = f"EOD VERIFIED: {date_iso} -- all clear ({pnl_note})"

    print(summary)

    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env()
        if notifier:
            notifier.send_info(summary)
    except Exception as exc:
        log.warning("eod_verify: telegram alert failed: %s", exc)

    # FIX-145: Record heartbeat for cron drift monitoring
    try:
        from utils.cron_heartbeat import record_heartbeat
        record_heartbeat("eod_verify")
    except Exception:
        pass

    store.close()

    # M-SC1: a real issue must exit NON-ZERO so cron/monitoring catches it (was
    # always exit 0). PENDING (a known gap until a broker feeder exists) and
    # VERIFIED are exit 0 — the job ran; PENDING's honesty lives in the status.
    return 2 if st == "ISSUES_FOUND" else 0


def _cron_main(argv=None) -> int:
    """Cron entry: S1 holiday-skip, then the real verify.

    S1 (2026-07-17): the registry declares this job market_day_only + cadence
    market_day, but nothing enforced it at the cron entry, so it ran on every
    NSE holiday. skip_if_non_trading_day FAILS OPEN (weekday fallback on any
    calendar error), so a trading day is never skipped. The guard is here and
    not in main() so a manual re-verify on a non-trading day still works.
    """
    from utils.cron_heartbeat import skip_if_non_trading_day

    if skip_if_non_trading_day("eod_verify"):
        return 0
    return main(argv)


if __name__ == "__main__":
    sys.exit(_cron_main())
