"""
tests/crash_test/state_inspector.py — Tool 3: Snapshot full system state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import (
    get_db_connection, ist_now_iso, http_get, DB_PATH, SNAPSHOTS_DIR,
)

HEALTH_URL = "http://localhost:5000/health"


def _capture_capital(conn) -> dict:
    row = conn.execute(
        "SELECT cash_floor, realized_pnl_today, margin_used, margin_reserved, "
        "last_broker_sync, sync_source, updated_at FROM capital_snapshot WHERE id=1"
    ).fetchone()
    if not row:
        return {"error": "No capital_snapshot row"}
    d = dict(row)
    d["available"] = round(d["cash_floor"] + d["realized_pnl_today"]
                           - d["margin_used"] - d["margin_reserved"], 2)
    d["total"] = round(d["cash_floor"] + d["realized_pnl_today"], 2)
    return d


def _capture_open_trades(conn) -> list:
    rows = conn.execute(
        "SELECT trade_id, symbol, direction, entry_actual_price, qty_filled, "
        "sl_initial, tgt_initial, status, created_at FROM trades WHERE status='OPEN'"
    ).fetchall()
    return [dict(r) for r in rows]


def _capture_open_orders(conn) -> list:
    rows = conn.execute(
        "SELECT order_id, trade_id, leg, order_type, transaction_type, "
        "price, trigger_price, qty_requested, qty_filled, status, placed_at "
        "FROM orders WHERE status IN ('OPEN','SUBMITTED','TRIGGER_PENDING')"
    ).fetchall()
    return [dict(r) for r in rows]


def _capture_kill_switch(conn) -> dict:
    row = conn.execute(
        "SELECT state, reason, triggered_at, triggered_by FROM kill_switch_state WHERE id=1"
    ).fetchone()
    if not row:
        return {"state": "NO_RECORD"}
    return dict(row)


def _capture_signal_queue() -> dict:
    health = http_get(HEALTH_URL)
    if health is None:
        return {"status": "SYSTEM_OFFLINE", "queue_depth": -1}
    return {
        "status": "ONLINE",
        "queue_depth": health.get("queue_depth", health.get("signal_queue_depth", -1)),
        "health": health,
    }


def _capture_fm_ledger_tail(conn) -> list:
    rows = conn.execute(
        "SELECT ledger_id, ts, entry_type, amount, bucket, balance_before, balance_after, "
        "signal_id, reservation_id, reason FROM fm_ledger ORDER BY ledger_id DESC LIMIT 10"
    ).fetchall()
    return [dict(r) for r in rows]


def _capture_system_events_tail(conn) -> list:
    rows = conn.execute(
        "SELECT event_id, timestamp, event_type, scenario, details "
        "FROM system_events ORDER BY event_id DESC LIMIT 5"
    ).fetchall()
    return [dict(r) for r in rows]


def _capture_in_flight(conn) -> list:
    rows = conn.execute(
        "SELECT signal_id, symbol, received_at, status FROM signals WHERE status='IN_PROCESS'"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # Flag if > 60s old
        from datetime import datetime, timezone, timedelta
        _IST = timezone(timedelta(hours=5, minutes=30))
        try:
            received = datetime.fromisoformat(r["received_at"])
            age = (datetime.now(_IST) - received).total_seconds()
            d["age_seconds"] = round(age, 1)
            d["stuck"] = age > 60
        except Exception:
            d["age_seconds"] = -1
            d["stuck"] = False
        result.append(d)
    return result


def _capture_thread_status() -> dict:
    health = http_get(HEALTH_URL)
    if health is None:
        return {"status": "THREAD_STATUS_UNAVAILABLE"}
    return health.get("threads", health.get("thread_status",
                      {"status": "THREAD_STATUS_UNAVAILABLE"}))


def _capture_db_info() -> dict:
    info = {}
    if DB_PATH.exists():
        stat = DB_PATH.stat()
        info["size_bytes"] = stat.st_size
        info["size_mb"] = round(stat.st_size / (1024 * 1024), 2)
        info["last_modified"] = str(stat.st_mtime)
    else:
        info["error"] = "DB file not found"

    wal_path = DB_PATH.parent / (DB_PATH.name + "-wal")
    if wal_path.exists():
        info["wal_size_bytes"] = wal_path.stat().st_size
    else:
        info["wal_size_bytes"] = 0

    # Row counts
    try:
        conn = get_db_connection(readonly=True)
        for table in ["signals", "trades", "orders", "fm_ledger",
                       "system_events", "screener_results"]:
            try:
                row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
                info[f"{table}_count"] = row["cnt"]
            except Exception:
                info[f"{table}_count"] = -1
        conn.close()
    except Exception as e:
        info["count_error"] = str(e)
    return info


def capture_full_state() -> dict:
    """Capture complete system state snapshot."""
    conn = get_db_connection(readonly=True)
    try:
        state = {
            "timestamp": ist_now_iso(),
            "capital": _capture_capital(conn),
            "open_trades": _capture_open_trades(conn),
            "open_orders": _capture_open_orders(conn),
            "kill_switch": _capture_kill_switch(conn),
            "signal_queue": _capture_signal_queue(),
            "fm_ledger_tail": _capture_fm_ledger_tail(conn),
            "system_events_tail": _capture_system_events_tail(conn),
            "in_flight": _capture_in_flight(conn),
            "thread_status": _capture_thread_status(),
            "db_info": _capture_db_info(),
        }
    finally:
        conn.close()
    return state


def diff_snapshots(before: dict, after: dict, prefix: str = "") -> list:
    """Compare two snapshots and return list of differences."""
    diffs = []
    all_keys = set(list(before.keys()) + list(after.keys()))
    for key in sorted(all_keys):
        path = f"{prefix}.{key}" if prefix else key
        bval = before.get(key)
        aval = after.get(key)
        if isinstance(bval, dict) and isinstance(aval, dict):
            diffs.extend(diff_snapshots(bval, aval, path))
        elif isinstance(bval, list) and isinstance(aval, list):
            if len(bval) != len(aval):
                diffs.append({"path": path, "change": "list_length",
                              "before": len(bval), "after": len(aval)})
            else:
                for i, (b, a) in enumerate(zip(bval, aval)):
                    if b != a:
                        diffs.append({"path": f"{path}[{i}]",
                                      "before": b, "after": a})
        elif bval != aval:
            diffs.append({"path": path, "before": bval, "after": aval})
    return diffs


def main():
    parser = argparse.ArgumentParser(description="State Inspector — Crash Test Tool 3")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", type=str, help="Save snapshot with label")
    group.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"),
                       help="Compare two snapshots by label")
    group.add_argument("--live", action="store_true", help="Print current state to stdout")
    args = parser.parse_args()

    if args.live:
        state = capture_full_state()
        print(json.dumps(state, indent=2, default=str))

    elif args.snapshot:
        state = capture_full_state()
        out_path = SNAPSHOTS_DIR / f"{args.snapshot}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(state, f, indent=2, default=str)
        print(f"Snapshot saved: {out_path}")
        print(f"  Capital available: {state['capital'].get('available', 'N/A')}")
        print(f"  Open trades: {len(state['open_trades'])}")
        print(f"  Open orders: {len(state['open_orders'])}")
        print(f"  Kill switch: {state['kill_switch'].get('state', 'N/A')}")

    elif args.diff:
        before_path = SNAPSHOTS_DIR / f"{args.diff[0]}.json"
        after_path = SNAPSHOTS_DIR / f"{args.diff[1]}.json"
        if not before_path.exists():
            print(f"ERROR: Snapshot not found: {before_path}")
            sys.exit(1)
        if not after_path.exists():
            print(f"ERROR: Snapshot not found: {after_path}")
            sys.exit(1)
        with open(before_path) as f:
            before = json.load(f)
        with open(after_path) as f:
            after = json.load(f)
        diffs = diff_snapshots(before, after)
        if not diffs:
            print("No differences found between snapshots.")
        else:
            print(f"Found {len(diffs)} differences:")
            for d in diffs:
                print(f"  {d['path']}: {d.get('before', '?')} -> {d.get('after', '?')}")
        print(json.dumps({"diff_count": len(diffs), "diffs": diffs}, indent=2, default=str))


if __name__ == "__main__":
    main()
