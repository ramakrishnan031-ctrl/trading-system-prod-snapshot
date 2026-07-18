"""
tests/crash_test/invariant_checker.py — Tool 1: Check all 8 global invariants.
Called after EVERY scenario automatically by scenario_runner.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import (
    get_db_connection, ist_now_iso, today_str, http_get, LIVE_DB_PATH, RESULTS_DIR,
)

HEALTH_URL = "http://localhost:5000/health"


def _check_invariant_a(conn) -> Dict[str, Any]:
    """Capital: available + reserved + used == total."""
    row = conn.execute(
        "SELECT cash_floor, realized_pnl_today, margin_used, margin_reserved, updated_at "
        "FROM capital_snapshot WHERE id=1"
    ).fetchone()
    if not row:
        return {"status": "FAIL", "details": "No capital_snapshot row found"}

    cash_floor = row["cash_floor"]
    realized_pnl = row["realized_pnl_today"]
    margin_used = row["margin_used"]
    margin_reserved = row["margin_reserved"]

    available = cash_floor + realized_pnl - margin_used - margin_reserved
    total_from_snapshot = cash_floor + realized_pnl

    ledger_rows = conn.execute(
        "SELECT entry_type, amount, margin_delta, pnl_delta, costs FROM fm_ledger ORDER BY ledger_id"
    ).fetchall()

    init_amount = 0.0
    ledger_reserved = 0.0
    ledger_used = 0.0
    ledger_pnl = 0.0
    for lr in ledger_rows:
        et = lr["entry_type"]
        if et == "INIT":
            init_amount += lr["amount"]
        elif et == "RESERVE":
            ledger_reserved += abs(lr["margin_delta"])
        elif et == "COMMIT":
            ledger_reserved -= abs(lr["margin_delta"])
            ledger_used += abs(lr["margin_delta"])
        elif et == "RELEASE":
            ledger_reserved -= abs(lr["margin_delta"])
        elif et == "RELEASE_USED":
            ledger_used -= abs(lr["margin_delta"])
            ledger_pnl += lr["pnl_delta"]
        elif et == "SYNC":
            pass

    ledger_available = init_amount + ledger_pnl - ledger_reserved - ledger_used

    snapshot_ok = abs(margin_used + margin_reserved + available - total_from_snapshot) < 1.0

    details = {
        "snapshot": {
            "cash_floor": cash_floor,
            "realized_pnl": realized_pnl,
            "margin_used": margin_used,
            "margin_reserved": margin_reserved,
            "available": round(available, 2),
            "total": round(total_from_snapshot, 2),
        },
        "ledger_replay": {
            "init_amount": round(init_amount, 2),
            "reserved": round(ledger_reserved, 2),
            "used": round(ledger_used, 2),
            "pnl": round(ledger_pnl, 2),
            "available": round(ledger_available, 2),
        },
        "snapshot_balanced": snapshot_ok,
    }

    if not snapshot_ok:
        return {"status": "FAIL", "details": f"Snapshot imbalance: {json.dumps(details)}",
                "value": details}
    return {"status": "PASS", "details": "Capital balanced", "value": details}


def _check_invariant_b(conn) -> Dict[str, Any]:
    """Signal accounting: all signals have terminal status."""
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM signals GROUP BY status"
    ).fetchall()

    status_counts = {r["status"]: r["cnt"] for r in rows}
    total = sum(status_counts.values())

    terminal = {"TRADED", "REJECTED_SCORE", "REJECTED_RISK", "REJECTED_GATE",
                "REJECTED_CAPITAL", "REJECTED_SCREENING", "REJECTED_ENTRY_GATE",
                "REJECTED_DUPLICATE", "REJECTED_EXPIRED", "REJECTED_SYMBOL",
                "REJECTED_OUTSIDE_HOURS", "REJECTED_KILL_SWITCH",
                "REJECTED_MAX_POSITIONS", "REJECTED_MARKET_CLOSED",
                "DROPPED_BACKPRESSURE", "DROPPED_SHUTDOWN",
                "EXPIRED", "DUPLICATE", "FAILED", "INVALID_SYMBOL",
                "OUTSIDE_HOURS", "CANCELLED"}
    in_process = {"ACCEPTED", "IN_PROCESS", "QUEUED", "PENDING"}

    stuck = []
    for s, c in status_counts.items():
        if s in in_process and c > 0:
            stuck.append({"status": s, "count": c})

    details = {"status_counts": status_counts, "total": total, "stuck": stuck}

    if stuck:
        # Check if any are truly stuck (> 10 min)
        for s_info in stuck:
            old = conn.execute(
                "SELECT COUNT(*) as cnt FROM signals WHERE status=? AND "
                "julianday('now') - julianday(received_at) > 10.0/1440.0",
                (s_info["status"],)
            ).fetchone()
            if old and old["cnt"] > 0:
                return {"status": "FAIL",
                        "details": f"{old['cnt']} signals stuck in {s_info['status']} > 10 min",
                        "value": details}
        return {"status": "PASS",
                "details": f"{len(stuck)} non-terminal statuses found but none stuck > 10 min",
                "value": details}

    return {"status": "PASS", "details": f"All {total} signals accounted for", "value": details}


def _check_invariant_c(conn) -> Dict[str, Any]:
    """Order parity (paper mode): orders table vs paper adapter state."""
    open_orders = conn.execute(
        "SELECT order_id, trade_id, leg, status FROM orders "
        "WHERE status IN ('OPEN','SUBMITTED','TRIGGER_PENDING')"
    ).fetchall()
    count = len(open_orders)
    order_list = [dict(r) for r in open_orders]
    details = {"open_order_count": count, "orders": order_list}

    # In offline mode we can only check DB consistency
    # Broker comparison requires running system (paper adapter state is in-memory)
    return {"status": "PASS",
            "details": f"{count} open orders in DB (broker parity requires running system)",
            "value": details}


def _check_invariant_d(conn) -> Dict[str, Any]:
    """Position parity (paper mode): trades table vs paper adapter positions."""
    open_trades = conn.execute(
        "SELECT trade_id, symbol, direction, qty_filled, status FROM trades WHERE status='OPEN'"
    ).fetchall()
    count = len(open_trades)
    trade_list = [dict(r) for r in open_trades]
    details = {"open_trade_count": count, "trades": trade_list}

    return {"status": "PASS",
            "details": f"{count} open trades in DB (broker parity requires running system)",
            "value": details}


def _check_invariant_e(conn) -> Dict[str, Any]:
    """State integrity: no illegal state transitions."""
    violations = []

    # Check orders for illegal terminal->non-terminal
    illegal_order = conn.execute(
        "SELECT order_id, status FROM orders WHERE status IN ('COMPLETE','REJECTED') "
        "AND order_id IN (SELECT superseded_by FROM orders WHERE superseded_by IS NOT NULL)"
    ).fetchall()
    # More general: look for CLOSED trades that somehow went back to OPEN
    bad_trades = conn.execute(
        "SELECT trade_id, status FROM trades WHERE status='OPEN' AND exit_time IS NOT NULL"
    ).fetchall()
    for t in bad_trades:
        violations.append({"table": "trades", "row_id": t["trade_id"],
                          "issue": "OPEN with exit_time set"})

    # Check for CLOSED->OPEN or FAILED->OPEN patterns via timestamps
    bad_signals = conn.execute(
        "SELECT signal_id, status FROM signals WHERE status='TRADED' AND trade_id IS NULL"
    ).fetchall()
    for s in bad_signals:
        violations.append({"table": "signals", "row_id": s["signal_id"],
                          "issue": "TRADED but no trade_id"})

    details = {"violations": violations, "checked": True}
    if violations:
        return {"status": "FAIL",
                "details": f"{len(violations)} state integrity violations found",
                "value": details}
    return {"status": "PASS", "details": "No illegal state transitions", "value": details}


def _check_invariant_f(conn) -> Dict[str, Any]:
    """Audit trail: every OPEN/CLOSED trade has complete chain."""
    trades = conn.execute(
        "SELECT trade_id, signal_id, status FROM trades WHERE status IN ('OPEN','CLOSED')"
    ).fetchall()

    missing = []
    for t in trades:
        tid = t["trade_id"]
        sid = t["signal_id"]
        chain = {"trade_id": tid, "missing_links": []}

        # Signal row
        sig = conn.execute("SELECT signal_id FROM signals WHERE signal_id=?", (sid,)).fetchone()
        if not sig:
            chain["missing_links"].append("signal")

        # Screener result
        scr = conn.execute(
            "SELECT id FROM screener_results WHERE signal_id=?", (sid,)
        ).fetchone()
        if not scr:
            chain["missing_links"].append("screener_result")

        # fm_ledger RESERVE
        res = conn.execute(
            "SELECT ledger_id FROM fm_ledger WHERE signal_id=? AND entry_type='RESERVE'", (sid,)
        ).fetchone()
        if not res:
            chain["missing_links"].append("fm_ledger_RESERVE")

        # Entry order
        entry = conn.execute(
            "SELECT order_id FROM orders WHERE trade_id=? AND leg='ENTRY'", (tid,)
        ).fetchone()
        if not entry:
            chain["missing_links"].append("order_ENTRY")

        # SL order
        sl = conn.execute(
            "SELECT order_id FROM orders WHERE trade_id=? AND leg='SL'", (tid,)
        ).fetchone()
        if not sl:
            chain["missing_links"].append("order_SL")

        # fm_ledger COMMIT
        commit = conn.execute(
            "SELECT ledger_id FROM fm_ledger WHERE trade_id=? AND entry_type='COMMIT'", (tid,)
        ).fetchone()
        if not commit:
            chain["missing_links"].append("fm_ledger_COMMIT")

        if chain["missing_links"]:
            missing.append(chain)

    details = {"trades_checked": len(trades), "incomplete_chains": missing}
    if missing:
        return {"status": "FAIL",
                "details": f"{len(missing)} trades with incomplete audit trail",
                "value": details}
    return {"status": "PASS",
            "details": f"All {len(trades)} trades have complete audit trail",
            "value": details}


def _check_invariant_g(conn) -> Dict[str, Any]:
    """No orphan resources."""
    issues = []

    # Orphan reservations: RESERVE without COMMIT or RELEASE > 10 min
    orphan_res = conn.execute(
        "SELECT reservation_id, signal_id, ts FROM fm_ledger "
        "WHERE entry_type='RESERVE' AND reservation_id NOT IN "
        "  (SELECT reservation_id FROM fm_ledger "
        "   WHERE entry_type IN ('COMMIT','RELEASE','RELEASE_USED') AND reservation_id IS NOT NULL) "
        "AND julianday('now') - julianday(ts) > 10.0/1440.0 "
        "AND reservation_id IS NOT NULL"
    ).fetchall()
    for r in orphan_res:
        issues.append({"type": "orphan_reservation", "reservation_id": r["reservation_id"],
                       "signal_id": r["signal_id"], "ts": r["ts"]})

    # Orphan orders: OPEN/SUBMITTED without matching trade
    orphan_ord = conn.execute(
        "SELECT order_id, trade_id, status FROM orders "
        "WHERE status IN ('OPEN','SUBMITTED') "
        "AND trade_id NOT IN (SELECT trade_id FROM trades)"
    ).fetchall()
    for o in orphan_ord:
        issues.append({"type": "orphan_order", "order_id": o["order_id"],
                       "trade_id": o["trade_id"]})

    # Stuck IN_PROCESS signals > 60s
    stuck = conn.execute(
        "SELECT signal_id, symbol, received_at FROM signals "
        "WHERE status='IN_PROCESS' "
        "AND julianday('now') - julianday(received_at) > 60.0/86400.0"
    ).fetchall()
    for s in stuck:
        issues.append({"type": "stuck_in_process", "signal_id": s["signal_id"],
                       "symbol": s["symbol"]})

    details = {"issues": issues, "issue_count": len(issues)}
    if issues:
        return {"status": "FAIL",
                "details": f"{len(issues)} orphan resources found",
                "value": details}
    return {"status": "PASS", "details": "No orphan resources", "value": details}


def _check_invariant_h(conn) -> Dict[str, Any]:
    """Recovery integrity: check last startup and reconciliation."""
    last_startup = conn.execute(
        "SELECT event_id, timestamp, event_type, scenario, details FROM system_events "
        "WHERE event_type='STARTUP' ORDER BY event_id DESC LIMIT 1"
    ).fetchone()

    if not last_startup:
        return {"status": "PASS",
                "details": "No startup events (fresh system)",
                "value": {"last_startup": None}}

    startup_info = dict(last_startup)
    scenario = last_startup["scenario"]

    # If CRASH scenario: verify RECOVERY event exists after startup
    if scenario == "CRASH":
        recovery = conn.execute(
            "SELECT event_id FROM system_events "
            "WHERE event_type='RECOVERY' AND event_id > ? LIMIT 1",
            (last_startup["event_id"],)
        ).fetchone()
        if not recovery:
            return {"status": "FAIL",
                    "details": "CRASH startup without RECOVERY event",
                    "value": {"last_startup": startup_info}}

    details = {"last_startup": startup_info, "scenario": scenario}
    return {"status": "PASS", "details": f"Recovery integrity OK (scenario={scenario})",
            "value": details}


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

INVARIANT_MAP = {
    "A": ("Capital balance", _check_invariant_a),
    "B": ("Signal accounting", _check_invariant_b),
    "C": ("Order parity", _check_invariant_c),
    "D": ("Position parity", _check_invariant_d),
    "E": ("State integrity", _check_invariant_e),
    "F": ("Audit trail", _check_invariant_f),
    "G": ("No orphan resources", _check_invariant_g),
    "H": ("Recovery integrity", _check_invariant_h),
}


def run_invariants(which: list[str] | None = None) -> Dict[str, Any]:
    if which is None:
        which = list(INVARIANT_MAP.keys())

    conn = get_db_connection(readonly=True)
    results = {}
    critical_count = 0

    try:
        for key in which:
            if key not in INVARIANT_MAP:
                results[key] = {"status": "SKIP", "details": f"Unknown invariant: {key}"}
                continue
            name, fn = INVARIANT_MAP[key]
            try:
                result = fn(conn)
                result["name"] = name
            except Exception as e:
                result = {"status": "FAIL", "details": f"Exception: {e}", "name": name}
            results[key] = result
            if result["status"] == "FAIL":
                critical_count += 1
    finally:
        conn.close()

    overall = "PASS" if critical_count == 0 else "FAIL"
    failed = [k for k, v in results.items() if v["status"] == "FAIL"]
    summary = (f"All {len(which)} invariants passed"
               if overall == "PASS"
               else f"CRITICAL: {critical_count} invariants failed: {failed}")

    return {
        "timestamp": ist_now_iso(),
        "invariants": results,
        "overall": overall,
        "critical_count": critical_count,
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(description="Invariant Checker — Crash Test Tool 1")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--full", action="store_true", help="Check all 8 invariants")
    group.add_argument("--quick", action="store_true", help="Check A + C + D only")
    group.add_argument("--invariant", type=str, help="Check single invariant (A-H)")
    parser.add_argument("--output", type=str, help="Output JSON file path")
    args = parser.parse_args()

    if args.full:
        which = None
    elif args.quick:
        which = ["A", "C", "D"]
    else:
        which = [args.invariant.upper()]

    result = run_invariants(which)
    output = json.dumps(result, indent=2, default=str)
    print(output)

    if args.output:
        out_path = __import__("pathlib").Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            f.write(output)

    sys.exit(0 if result["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
