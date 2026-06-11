"""
tests/crash_test/state_machine_validator.py — Tool 10: Validate state transitions.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Set

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import get_db_connection, ist_now_iso, today_str

# Legal state transition maps
ORDER_TRANSITIONS: Dict[str, Set[str]] = {
    "PENDING": {"SUBMITTED", "CANCELLED", "REJECTED", "OPEN"},
    "SUBMITTED": {"COMPLETE", "CANCELLED", "REJECTED", "OPEN", "TRIGGER_PENDING"},
    "OPEN": {"COMPLETE", "CANCELLED", "REJECTED", "TRIGGER_PENDING"},
    "TRIGGER_PENDING": {"SUBMITTED", "COMPLETE", "CANCELLED", "OPEN"},
    # Terminal states
    "COMPLETE": set(),
    "CANCELLED": set(),
    "REJECTED": set(),
}

TRADE_TRANSITIONS: Dict[str, Set[str]] = {
    "PENDING": {"PENDING_FILL", "CANCELLED", "FAILED"},
    "PENDING_FILL": {"OPEN", "CANCELLED", "FAILED"},
    "OPEN": {"CLOSED", "CLOSED_MANUAL", "PARTIAL"},
    "PARTIAL": {"OPEN", "CLOSED", "CLOSED_MANUAL"},
    # Terminal states
    "CLOSED": set(),
    "CLOSED_MANUAL": set(),
    "CANCELLED": set(),
    "FAILED": set(),
}

import re

_REJECTED_SCORE_RE = re.compile(r"^REJECTED_SCORE_\d+$")

SIGNAL_TERMINAL_EXACT = {
    "TRADED", "PROCESSED", "REJECTED_SCORE", "REJECTED_RISK", "REJECTED_GATE",
    "REJECTED_CAPITAL", "REJECTED_SCREENING", "REJECTED_ENTRY_GATE",
    "REJECTED_DUPLICATE", "REJECTED_EXPIRED", "REJECTED_SYMBOL",
    "REJECTED_OUTSIDE_HOURS", "REJECTED_KILL_SWITCH",
    "REJECTED_MAX_POSITIONS", "REJECTED_MARKET_CLOSED",
    "REJECTED_DAILY_TRADES", "REJECTED_DUPLICATE_SYMBOL",
    "REJECTED_OPEN_POSITIONS", "REJECTED_OUTSIDE_ENTRY_WINDOW",
    "REJECTED_SHADOW_INNING_ACTIVE", "REJECTED_SIZING_QTY_EXPLOSION_GUARD",
    "REJECTED_STRATEGY_POSITION_LIMIT", "REJECTED_SIZING_POSITION_VALUE_CAP",
    "REJECTED_SIGNAL_AGE", "REJECTED_RESERVE_FAILED",
    "REJECTED_CONSECUTIVE_LOSSES",
    "REJECTED_CONTRARY_POSITION",
    "REJECTED_SIZING_CAPITAL",
    "SKIPPED_QUOTE_UNAVAILABLE",
    "DROPPED_BACKPRESSURE", "DROPPED_SHUTDOWN",
    "EXPIRED", "DUPLICATE", "FAILED", "INVALID_SYMBOL",
    "OUTSIDE_HOURS", "CANCELLED",
}

def _is_known_signal_status(status: str) -> bool:
    return status in SIGNAL_TERMINAL_EXACT or bool(_REJECTED_SCORE_RE.match(status))

def _is_terminal_signal(status: str) -> bool:
    return _is_known_signal_status(status) and status not in ("PROCESSING",)

_ALL_SIGNAL_REJECTIONS = {
    "REJECTED_SCORE", "REJECTED_RISK", "REJECTED_GATE",
    "REJECTED_CAPITAL", "REJECTED_SCREENING", "REJECTED_ENTRY_GATE",
    "REJECTED_DUPLICATE", "REJECTED_EXPIRED", "REJECTED_SYMBOL",
    "REJECTED_OUTSIDE_HOURS", "REJECTED_KILL_SWITCH",
    "REJECTED_MAX_POSITIONS", "REJECTED_MARKET_CLOSED",
    "REJECTED_DAILY_TRADES", "REJECTED_DUPLICATE_SYMBOL",
    "REJECTED_OPEN_POSITIONS", "REJECTED_OUTSIDE_ENTRY_WINDOW",
    "REJECTED_SHADOW_INNING_ACTIVE", "REJECTED_SIZING_QTY_EXPLOSION_GUARD",
    "REJECTED_STRATEGY_POSITION_LIMIT", "REJECTED_SIZING_POSITION_VALUE_CAP",
    "REJECTED_SIGNAL_AGE", "REJECTED_RESERVE_FAILED",
    "REJECTED_CONSECUTIVE_LOSSES",
    "REJECTED_CONTRARY_POSITION",
    "REJECTED_SIZING_CAPITAL",
    "SKIPPED_QUOTE_UNAVAILABLE",
}

SIGNAL_TRANSITIONS: Dict[str, Set[str]] = {
    "ACCEPTED": {"IN_PROCESS", "PROCESSING", "TRADED", "PROCESSED", "EXPIRED", "DUPLICATE",
                 "OUTSIDE_HOURS", "INVALID_SYMBOL", "FAILED",
                 "DROPPED_BACKPRESSURE", "DROPPED_SHUTDOWN", "CANCELLED",
                 } | _ALL_SIGNAL_REJECTIONS,
    "IN_PROCESS": {"TRADED", "PROCESSED", "FAILED", "EXPIRED",
                   "DUPLICATE", "CANCELLED",
                   "DROPPED_BACKPRESSURE", "DROPPED_SHUTDOWN",
                   } | _ALL_SIGNAL_REJECTIONS,
    "PROCESSING": {"TRADED", "PROCESSED", "FAILED", "EXPIRED",
                   "DUPLICATE", "CANCELLED",
                   "DROPPED_BACKPRESSURE", "DROPPED_SHUTDOWN",
                   } | _ALL_SIGNAL_REJECTIONS,
    "QUEUED": {"ACCEPTED", "IN_PROCESS", "PROCESSING", "EXPIRED", "FAILED",
               "DROPPED_BACKPRESSURE", "DROPPED_SHUTDOWN", "CANCELLED",
               "PROCESSED", "TRADED"} | _ALL_SIGNAL_REJECTIONS,
    "PENDING": {"ACCEPTED", "IN_PROCESS", "PROCESSING", "EXPIRED", "FAILED",
                "DROPPED_BACKPRESSURE", "DROPPED_SHUTDOWN", "CANCELLED"},
}
# Terminal signal states cannot transition
for s in SIGNAL_TERMINAL_EXACT:
    SIGNAL_TRANSITIONS.setdefault(s, set())


def _validate_table(conn, table: str, id_col: str, status_col: str,
                    transitions: Dict[str, Set[str]],
                    since: Optional[str] = None) -> Dict:
    """Validate state transitions for a table."""
    where = ""
    params: list = []
    if since == "today":
        ts_col = "updated_at" if table in ("orders", "trades") else "received_at"
        where = f" WHERE date({ts_col}) = date('now', 'localtime')"

    query = f"SELECT {id_col}, {status_col} FROM {table}{where}"
    rows = conn.execute(query, params).fetchall()

    violations = []
    checked = len(rows)

    for row in rows:
        row_id = row[id_col]
        current = row[status_col]

        if table == "signals":
            if current not in transitions and not _is_known_signal_status(current):
                violations.append({
                    "row_id": row_id,
                    "issue": f"Unknown status: {current}",
                    "current_status": current,
                })
        elif current not in transitions:
            violations.append({
                "row_id": row_id,
                "issue": f"Unknown status: {current}",
                "current_status": current,
            })

    # Cross-check: look for impossible state combinations
    if table == "orders":
        # COMPLETE orders should not be superseded_by targets of active orders
        bad = conn.execute(
            "SELECT o1.order_id, o1.status, o2.order_id as ref_id, o2.status as ref_status "
            "FROM orders o1 JOIN orders o2 ON o1.order_id = o2.superseded_by "
            "WHERE o1.status IN ('COMPLETE','REJECTED') "
            "AND o2.status IN ('PENDING','OPEN','SUBMITTED')" + (
                " AND date(o1.updated_at) = date('now', 'localtime')" if since == "today" else ""
            )
        ).fetchall()
        for b in bad:
            violations.append({
                "row_id": b["order_id"],
                "issue": f"Terminal order {b['status']} referenced as superseded_by "
                         f"of active order {b['ref_id']} ({b['ref_status']})",
            })

    if table == "trades":
        bad = conn.execute(
            "SELECT trade_id, status FROM trades WHERE status='CLOSED' AND exit_time IS NULL"
            + (" AND date(updated_at) = date('now', 'localtime')" if since == "today" else "")
        ).fetchall()
        for b in bad:
            violations.append({
                "row_id": b["trade_id"],
                "issue": "CLOSED without exit_time",
            })

        # OPEN trades should NOT have exit_time
        bad = conn.execute(
            "SELECT trade_id, status FROM trades WHERE status='OPEN' AND exit_time IS NOT NULL"
            + (" AND date(updated_at) = date('now', 'localtime')" if since == "today" else "")
        ).fetchall()
        for b in bad:
            violations.append({
                "row_id": b["trade_id"],
                "issue": "OPEN with exit_time set",
            })

    if table == "signals":
        bad = conn.execute(
            "SELECT signal_id, status FROM signals WHERE status IN ('TRADED','PROCESSED') AND trade_id IS NULL"
            + (" AND date(received_at) = date('now', 'localtime')" if since == "today" else "")
        ).fetchall()
        for b in bad:
            violations.append({
                "row_id": b["signal_id"],
                "issue": "TRADED without trade_id",
            })

    return {
        "table": table,
        "violations": violations,
        "checked": checked,
        "clean": len(violations) == 0,
    }


def validate_all(since: Optional[str] = None) -> Dict:
    """Validate all tables."""
    conn = get_db_connection(readonly=True)
    results = {}

    results["orders"] = _validate_table(
        conn, "orders", "order_id", "status", ORDER_TRANSITIONS, since
    )
    results["trades"] = _validate_table(
        conn, "trades", "trade_id", "status", TRADE_TRANSITIONS, since
    )
    results["signals"] = _validate_table(
        conn, "signals", "signal_id", "status", SIGNAL_TRANSITIONS, since
    )

    conn.close()

    total_violations = sum(len(r["violations"]) for r in results.values())
    return {
        "timestamp": ist_now_iso(),
        "since": since,
        "results": results,
        "total_violations": total_violations,
        "overall": "CLEAN" if total_violations == 0 else "VIOLATIONS_FOUND",
    }


def main():
    parser = argparse.ArgumentParser(
        description="State Machine Validator — Crash Test Tool 10"
    )
    parser.add_argument("--table", type=str,
                        choices=["orders", "trades", "signals"],
                        help="Validate specific table")
    parser.add_argument("--all", action="store_true", help="Validate all tables")
    parser.add_argument("--since", type=str, help="Filter: 'today' or YYYY-MM-DD")
    args = parser.parse_args()

    if args.all:
        result = validate_all(args.since)
    elif args.table:
        conn = get_db_connection(readonly=True)
        table_config = {
            "orders": ("order_id", "status", ORDER_TRANSITIONS),
            "trades": ("trade_id", "status", TRADE_TRANSITIONS),
            "signals": ("signal_id", "status", SIGNAL_TRANSITIONS),
        }
        id_col, status_col, transitions = table_config[args.table]
        table_result = _validate_table(conn, args.table, id_col, status_col,
                                        transitions, args.since)
        conn.close()
        result = {
            "timestamp": ist_now_iso(),
            "results": {args.table: table_result},
            "total_violations": len(table_result["violations"]),
            "overall": "CLEAN" if table_result["clean"] else "VIOLATIONS_FOUND",
        }
    else:
        parser.print_help()
        sys.exit(0)

    print(json.dumps(result, indent=2, default=str))
    if result.get("total_violations", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
