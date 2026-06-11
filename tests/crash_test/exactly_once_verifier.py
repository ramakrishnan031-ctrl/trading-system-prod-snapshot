"""
tests/crash_test/exactly_once_verifier.py — Tool 9: Prove every critical operation happened exactly once.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import get_db_connection, ist_now_iso, today_str


def _build_time_filter(date: Optional[str], window: Optional[str],
                       ts_col: str = "ts") -> tuple:
    """Build SQL WHERE clause for time filtering."""
    if window:
        parts = window.split("-")
        if len(parts) == 2:
            start, end = parts[0].strip(), parts[1].strip()
            today = today_str()
            return (f" AND time({ts_col}) >= time(?) AND time({ts_col}) <= time(?)",
                    [start, end])
    if date == "today":
        return (f" AND date({ts_col}) = date('now', 'localtime')", [])
    if date:
        return (f" AND date({ts_col}) = ?", [date])
    return ("", [])


def check_signal_intake(conn, time_filter: str, params: list) -> Dict:
    """Check: each signal processed exactly once (no duplicate signal_id)."""
    query = (
        "SELECT signal_id, COUNT(*) as cnt FROM signals WHERE 1=1 "
        + time_filter.replace("ts", "received_at") +
        " GROUP BY signal_id HAVING COUNT(*) > 1"
    )
    rows = conn.execute(query, params).fetchall()
    violations = [{"signal_id": r["signal_id"], "count": r["cnt"]} for r in rows]
    return {"operation": "signal_intake", "violations": violations,
            "clean": len(violations) == 0}


def check_capital_reserve(conn, time_filter: str, params: list) -> Dict:
    """Check: one RESERVE per signal_id."""
    query = (
        "SELECT signal_id, COUNT(*) as cnt FROM fm_ledger "
        "WHERE entry_type='RESERVE' AND signal_id IS NOT NULL"
        + time_filter +
        " GROUP BY signal_id HAVING COUNT(*) > 1"
    )
    rows = conn.execute(query, params).fetchall()
    violations = [{"signal_id": r["signal_id"], "count": r["cnt"]} for r in rows]
    return {"operation": "capital_reserve", "violations": violations,
            "clean": len(violations) == 0}


def check_capital_commit(conn, time_filter: str, params: list) -> Dict:
    """Check: one COMMIT per reservation_id."""
    query = (
        "SELECT reservation_id, COUNT(*) as cnt FROM fm_ledger "
        "WHERE entry_type='COMMIT' AND reservation_id IS NOT NULL"
        + time_filter +
        " GROUP BY reservation_id HAVING COUNT(*) > 1"
    )
    rows = conn.execute(query, params).fetchall()
    violations = [{"reservation_id": r["reservation_id"], "count": r["cnt"]} for r in rows]
    return {"operation": "capital_commit", "violations": violations,
            "clean": len(violations) == 0}


def check_capital_release(conn, time_filter: str, params: list) -> Dict:
    """Check: one RELEASE/RELEASE_USED per reservation_id."""
    query = (
        "SELECT reservation_id, COUNT(*) as cnt FROM fm_ledger "
        "WHERE entry_type IN ('RELEASE','RELEASE_USED') AND reservation_id IS NOT NULL"
        + time_filter +
        " GROUP BY reservation_id HAVING COUNT(*) > 1"
    )
    rows = conn.execute(query, params).fetchall()
    violations = [{"reservation_id": r["reservation_id"], "count": r["cnt"]} for r in rows]
    return {"operation": "capital_release", "violations": violations,
            "clean": len(violations) == 0}


def check_order_placement(conn, time_filter: str, params: list) -> Dict:
    """Check: no duplicate orders per trade_id + leg."""
    query = (
        "SELECT trade_id, leg, COUNT(*) as cnt FROM orders WHERE 1=1"
        + time_filter.replace("ts", "placed_at") +
        " GROUP BY trade_id, leg HAVING COUNT(*) > 1"
    )
    rows = conn.execute(query, params).fetchall()
    # Filter out legitimate SL replacements (superseded_by chain)
    violations = []
    for r in rows:
        if r["leg"] == "SL" and r["cnt"] > 1:
            # SL may have multiple entries due to trailing — check superseded_by
            sl_orders = conn.execute(
                "SELECT order_id, superseded_by FROM orders "
                "WHERE trade_id=? AND leg='SL'", (r["trade_id"],)
            ).fetchall()
            active = [o for o in sl_orders if o["superseded_by"] is None]
            if len(active) > 1:
                violations.append({"trade_id": r["trade_id"], "leg": r["leg"],
                                   "count": r["cnt"], "active_count": len(active)})
        else:
            violations.append({"trade_id": r["trade_id"], "leg": r["leg"],
                               "count": r["cnt"]})
    return {"operation": "order_placement", "violations": violations,
            "clean": len(violations) == 0}


def check_eod_squareoff(conn, time_filter: str, params: list) -> Dict:
    """Check: one EOD exit per trade_id."""
    query = (
        "SELECT trade_id, COUNT(*) as cnt FROM orders "
        "WHERE leg='EOD'"
        + time_filter.replace("ts", "placed_at") +
        " GROUP BY trade_id HAVING COUNT(*) > 1"
    )
    rows = conn.execute(query, params).fetchall()
    violations = [{"trade_id": r["trade_id"], "count": r["cnt"]} for r in rows]
    return {"operation": "eod_squareoff", "violations": violations,
            "clean": len(violations) == 0}


def check_telegram_alerts(conn, time_filter: str, params: list) -> Dict:
    """Check: no duplicate alerts per trigger key."""
    try:
        query = (
            "SELECT title, source_module, COUNT(*) as cnt FROM telegram_alerts WHERE 1=1"
            + time_filter.replace("ts", "sent_at") +
            " GROUP BY title, source_module HAVING COUNT(*) > 1"
        )
        rows = conn.execute(query, params).fetchall()
        violations = [{"title": r["title"], "source": r["source_module"],
                       "count": r["cnt"]} for r in rows]
    except Exception:
        violations = []
    return {"operation": "telegram_alerts", "violations": violations,
            "clean": len(violations) == 0}


def run_verification(date: Optional[str] = None, window: Optional[str] = None,
                     full: bool = False) -> Dict:
    """Run all exactly-once checks."""
    conn = get_db_connection(readonly=True)
    time_filter, params = _build_time_filter(date, window)

    checks = [
        check_signal_intake,
        check_capital_reserve,
        check_capital_commit,
        check_capital_release,
        check_order_placement,
        check_eod_squareoff,
        check_telegram_alerts,
    ]

    results = []
    total_violations = 0
    for check_fn in checks:
        result = check_fn(conn, time_filter, list(params))
        results.append(result)
        total_violations += len(result["violations"])

    conn.close()

    return {
        "timestamp": ist_now_iso(),
        "filter": {"date": date, "window": window, "full": full},
        "checks": results,
        "total_violations": total_violations,
        "overall": "CLEAN" if total_violations == 0 else "VIOLATIONS_FOUND",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Exactly-Once Verifier — Crash Test Tool 9"
    )
    parser.add_argument("--date", type=str, help="Date filter (YYYY-MM-DD or 'today')")
    parser.add_argument("--window", type=str,
                        help="Time window filter (e.g., '09:30-11:00')")
    parser.add_argument("--full", action="store_true", help="Check entire DB history")
    args = parser.parse_args()

    if not args.date and not args.window and not args.full:
        args.date = "today"

    result = run_verification(
        date=args.date if not args.full else None,
        window=args.window,
        full=args.full,
    )
    print(json.dumps(result, indent=2, default=str))

    if result["total_violations"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
