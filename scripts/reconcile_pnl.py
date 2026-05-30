"""
scripts/reconcile_pnl.py — Trading System v2  (FIX-128 Fix B)

Purpose:
    Daily EOD script that fetches broker P&L from Zerodha, compares it
    against system net_pnl (sum of closed trades in DB), and alerts if the
    variance is significant.

    Run by cron at 16:15 IST — after market close and after brokerage
    charges settle. Must run AFTER the EOD squareoff and order fills are
    complete.

Decision rules:
    variance <= Rs 10  : status=OK, no alert
    variance > Rs 10   : status=VARIANCE_MINOR, CRITICAL log + Telegram alert
    variance > Rs 100  : status=VARIANCE_MAJOR, CRITICAL log + Telegram alert + SOFT_KILL

Paper mode:
    Broker P&L fetch is skipped (no real orders exist). status=PAPER_SKIPPED.
    system_pnl is still computed and logged for reference.

Exit codes:
    0 — OK or PAPER_SKIPPED
    1 — error during execution
    2 — variance > Rs 100 (SOFT_KILL triggered)
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

from core.config_loader import load_all as load_config
from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist, today_ist

_VARIANCE_MINOR_THRESHOLD = 10.0   # Rs: log CRITICAL + alert
_VARIANCE_MAJOR_THRESHOLD = 100.0  # Rs: log CRITICAL + alert + SOFT_KILL


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="reconcile_pnl",
        description="FIX-128: EOD broker vs system P&L reconciliation.",
    )
    parser.add_argument(
        "--config", metavar="PATH", default="config",
        help="Path to config directory (default: config/)",
    )
    parser.add_argument(
        "--db", metavar="PATH", default=None,
        help="Path to SQLite DB (default: from config)",
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD", default=None,
        help="Date to reconcile (default: today IST)",
    )
    parser.add_argument(
        "--mode", choices=["paper", "live"], default=None,
        help="Force paper/live mode (default: from TRADING_MODE env var)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute and log but do not write to DB or trigger kill switch.",
    )
    return parser.parse_args(argv)


# ─────────────────────────────────────────────────────────────────────────────
# Broker P&L fetch
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_broker_day_pnl(log: logging.Logger) -> float:
    """
    Fetch today's realized P&L from Zerodha positions API.

    Kite positions() returns day={symbol: {realised, unrealised, pnl, ...}}.
    We sum all 'realised' values from the day positions.

    Returns 0.0 on any failure (best-effort; reconcile_pnl logs the error).
    Raises RuntimeError with a descriptive message on hard failures.
    """
    from broker.zerodha_adapter import ZerodhaAdapter

    api_key = os.environ.get("ZERODHA_API_KEY", "")
    access_token = os.environ.get("ZERODHA_ACCESS_TOKEN", "")

    if not api_key or not access_token:
        raise RuntimeError(
            "ZERODHA_API_KEY and ZERODHA_ACCESS_TOKEN must be set for live P&L fetch"
        )

    adapter = ZerodhaAdapter(
        api_key=api_key,
        access_token=access_token,
        logger=log,
        paper=False,
    )

    try:
        raw_positions = adapter._kite.positions()
    except Exception as exc:
        raise RuntimeError(f"Kite positions() failed: {exc}") from exc

    day_positions = raw_positions.get("day", []) if isinstance(raw_positions, dict) else []
    total_realised = sum(
        float(pos.get("realised", 0.0) or 0.0)
        for pos in day_positions
    )
    log.info(
        "reconcile_pnl.broker_pnl_fetched",
        extra={
            "day_position_count": len(day_positions),
            "total_realised": total_realised,
        },
    )
    return total_realised


# ─────────────────────────────────────────────────────────────────────────────
# Core reconciliation logic
# ─────────────────────────────────────────────────────────────────────────────

def run_reconciliation(
    *,
    store: StateStore,
    date_iso: str,
    is_paper: bool,
    log: logging.Logger,
    notifier=None,
    kill_switch=None,
    mode_label: str = "LIVE",
    dry_run: bool = False,
) -> dict:
    """
    Run P&L reconciliation for date_iso. Returns result dict.

    result keys: date, broker_pnl, system_pnl, variance, status, notes
    """
    now_str = now_ist().isoformat()
    system_pnl = store.get_today_closed_pnl(date_iso)
    log.info(
        "reconcile_pnl.system_pnl",
        extra={"date": date_iso, "system_pnl": system_pnl},
    )

    if is_paper:
        result = {
            "date": date_iso,
            "broker_pnl": None,
            "system_pnl": system_pnl,
            "variance": None,
            "status": "PAPER_SKIPPED",
            "notes": "Paper mode: no broker P&L to fetch.",
        }
        log.info(
            "reconcile_pnl.paper_mode_skipped",
            extra={"system_pnl": system_pnl},
        )
        if not dry_run:
            store.upsert_pnl_reconciliation(
                date=date_iso,
                broker_pnl=None,
                system_pnl=system_pnl,
                variance=None,
                status="PAPER_SKIPPED",
                notes=result["notes"],
                created_at=now_str,
            )
        return result

    # Live mode: fetch broker P&L
    broker_pnl: float
    fetch_error: str = ""
    try:
        broker_pnl = _fetch_broker_day_pnl(log)
    except Exception as exc:
        fetch_error = str(exc)
        log.error("reconcile_pnl.broker_fetch_failed", extra={"error": fetch_error})
        result = {
            "date": date_iso,
            "broker_pnl": None,
            "system_pnl": system_pnl,
            "variance": None,
            "status": "ERROR",
            "notes": f"Broker P&L fetch failed: {fetch_error}",
        }
        if not dry_run:
            store.upsert_pnl_reconciliation(
                date=date_iso,
                broker_pnl=None,
                system_pnl=system_pnl,
                variance=None,
                status="ERROR",
                notes=result["notes"],
                created_at=now_str,
            )
        return result

    variance = abs(broker_pnl - system_pnl)
    log.info(
        "reconcile_pnl.variance",
        extra={
            "date": date_iso,
            "broker_pnl": broker_pnl,
            "system_pnl": system_pnl,
            "variance": variance,
        },
    )

    if variance <= _VARIANCE_MINOR_THRESHOLD:
        status = "OK"
        notes = None
    elif variance <= _VARIANCE_MAJOR_THRESHOLD:
        status = "VARIANCE_MINOR"
        notes = (
            f"Variance ₹{variance:.2f} > ₹{_VARIANCE_MINOR_THRESHOLD:.0f} threshold. "
            f"Broker: ₹{broker_pnl:.2f} | System: ₹{system_pnl:.2f}"
        )
    else:
        status = "VARIANCE_MAJOR"
        notes = (
            f"LARGE VARIANCE ₹{variance:.2f} > ₹{_VARIANCE_MAJOR_THRESHOLD:.0f} threshold. "
            f"Broker: ₹{broker_pnl:.2f} | System: ₹{system_pnl:.2f}. SOFT_KILL triggered."
        )

    result = {
        "date": date_iso,
        "broker_pnl": broker_pnl,
        "system_pnl": system_pnl,
        "variance": variance,
        "status": status,
        "notes": notes,
    }

    if status != "OK":
        log.critical(
            "reconcile_pnl.variance_alert",
            extra={
                "status": status,
                "broker_pnl": broker_pnl,
                "system_pnl": system_pnl,
                "variance": variance,
            },
        )
        if notifier is not None:
            try:
                severity = "ERROR" if status == "VARIANCE_MAJOR" else "WARNING"
                notifier.send(
                    severity=severity,
                    title=f"[{mode_label}] P&L RECONCILIATION — {status}",
                    body=(
                        f"Date: {date_iso}\n"
                        f"Broker P&L: ₹{broker_pnl:.2f}\n"
                        f"System P&L: ₹{system_pnl:.2f}\n"
                        f"Variance: ₹{variance:.2f}"
                    ),
                    source_module="reconcile_pnl",
                )
            except Exception as ne:
                log.error("reconcile_pnl.notifier_failed: %s", ne)

        if status == "VARIANCE_MAJOR" and kill_switch is not None:
            if not dry_run:
                try:
                    kill_switch.soft_kill(
                        reason=f"pnl_reconciliation_variance_{variance:.2f}",
                        triggered_by="reconcile_pnl",
                    )
                    log.critical(
                        "reconcile_pnl.soft_kill_triggered",
                        extra={"variance": variance},
                    )
                except Exception as kse:
                    log.error("reconcile_pnl.soft_kill_failed: %s", kse)

    if not dry_run:
        store.upsert_pnl_reconciliation(
            date=date_iso,
            broker_pnl=broker_pnl,
            system_pnl=system_pnl,
            variance=variance,
            status=status,
            notes=notes,
            created_at=now_str,
        )

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    args = _parse_args(argv)

    log = get_logger("reconcile_pnl")

    # Determine mode
    mode = args.mode or os.environ.get("TRADING_MODE", "live").lower()
    is_paper = (mode == "paper")
    mode_label = "PAPER" if is_paper else "LIVE"

    # Config
    config_dir = Path(args.config)
    try:
        app_config = load_config(config_dir)
    except Exception as exc:
        log.error("reconcile_pnl: config load failed: %s", exc)
        return 1

    # State store
    db_path = Path(args.db) if args.db else Path("data_store") / "trading.db"
    try:
        store = StateStore(db_path=db_path)
    except Exception as exc:
        log.error("reconcile_pnl: state_store open failed: %s", exc)
        return 1

    date_iso = args.date or today_ist()
    log.info(
        "reconcile_pnl.start",
        extra={"date": date_iso, "mode": mode_label, "dry_run": args.dry_run},
    )

    try:
        result = run_reconciliation(
            store=store,
            date_iso=date_iso,
            is_paper=is_paper,
            log=log,
            notifier=None,     # no Telegram in standalone script (no token in scope)
            kill_switch=None,  # standalone: log CRITICAL, manual intervention
            mode_label=mode_label,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        log.error("reconcile_pnl.unexpected_error: %s", exc, exc_info=True)
        store.close()
        return 1

    store.close()

    status = result["status"]
    log.info(
        "reconcile_pnl.complete",
        extra={
            "date": date_iso,
            "status": status,
            "variance": result.get("variance"),
            "broker_pnl": result.get("broker_pnl"),
            "system_pnl": result.get("system_pnl"),
        },
    )

    if status == "VARIANCE_MAJOR":
        return 2
    if status == "ERROR":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
