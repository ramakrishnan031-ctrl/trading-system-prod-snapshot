"""
tests/crash_test/cleanup.py — Tool 7: Reset system state between scenarios.

⚠️ SCOPE CHANGE (18-Jul-2026): this tool now resets the SCRATCH database only.

It previously issued its destructive statements (delete signals, clear the kill
switch, release reservations, ``UPDATE trades SET status='CANCELLED'``, reset
capital) against the LIVE trading database — reports/crash_test/cleanup_log.jsonl
records real live writes on 05-Jun and 07-Jun 2026. The harness is now scratch-safe
by construction (ct_utils.assert_not_live_db), so those statements can no longer
reach production data.

The tool deliberately REFUSES rather than silently cleaning scratch when the caller
clearly means the live system (``--live``): a cleanup that reports success while the
live system is untouched is worse than one that stops. Resetting the real system
after a scenario is an OPERATOR action and belongs in scripts/ with its own
safeguards — not in a destructive test harness.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Dict

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import (
    get_db_connection, ist_now_iso, today_str, append_jsonl, http_get,
    REPORTS_DIR, BASE_DIR,
)

CLEANUP_LOG = REPORTS_DIR / "cleanup_log.jsonl"
HEALTH_URL = "http://localhost:5000/health"


def _log(action: str, details: dict) -> None:
    record = {"ts": ist_now_iso(), "action": action, **details}
    append_jsonl(CLEANUP_LOG, record)


def _check_open_positions(conn) -> int:
    row = conn.execute("SELECT COUNT(*) as cnt FROM trades WHERE status='OPEN'").fetchone()
    return row["cnt"] if row else 0


def _run_invariant_check(mode: str = "--quick") -> Dict:
    cmd = [sys.executable, str(BASE_DIR / "tests" / "crash_test" / "invariant_checker.py"), mode]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return {"overall": "ERROR", "error": result.stderr or result.stdout}


def do_status() -> None:
    """Print current state without modifications."""
    cmd = [sys.executable, str(BASE_DIR / "tests" / "crash_test" / "state_inspector.py"), "--live"]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
    print(result.stdout)


def do_soft(force: bool = False) -> Dict:
    """Soft cleanup: clear signals, reset kill switch, release stuck reservations."""
    conn = get_db_connection()
    today = today_str()

    # Safety check
    open_pos = _check_open_positions(conn)
    if open_pos > 0 and not force:
        conn.close()
        msg = f"WARNING: System has {open_pos} open positions. Use --force to proceed."
        print(msg)
        return {"status": "BLOCKED", "reason": msg}

    actions = []

    # 1. Clear today's non-terminal signals
    cur = conn.execute(
        "DELETE FROM signals WHERE date(received_at)=? AND status IN ('ACCEPTED','IN_PROCESS')",
        (today,)
    )
    actions.append(f"Deleted {cur.rowcount} non-terminal signals")

    # 2. Clear kill switch
    cur = conn.execute(
        "UPDATE kill_switch_state SET state='INACTIVE', reason='crash_test_cleanup', "
        "triggered_at=?, triggered_by='cleanup.py' WHERE id=1 AND state != 'INACTIVE'",
        (ist_now_iso(),)
    )
    actions.append(f"Kill switch cleared ({cur.rowcount} rows updated)")

    # 3. Release orphan reservations (> 5 min without COMMIT/RELEASE)
    orphans = conn.execute(
        "SELECT reservation_id FROM fm_ledger "
        "WHERE entry_type='RESERVE' AND reservation_id IS NOT NULL "
        "AND reservation_id NOT IN "
        "  (SELECT reservation_id FROM fm_ledger "
        "   WHERE entry_type IN ('COMMIT','RELEASE','RELEASE_USED') "
        "   AND reservation_id IS NOT NULL) "
        "AND julianday('now') - julianday(ts) > 5.0/1440.0"
    ).fetchall()
    for o in orphans:
        rid = o["reservation_id"]
        # Get the original RESERVE amount
        orig = conn.execute(
            "SELECT amount, balance_before, balance_after, bucket, signal_id FROM fm_ledger "
            "WHERE reservation_id=? AND entry_type='RESERVE' LIMIT 1", (rid,)
        ).fetchone()
        if orig:
            conn.execute(
                "INSERT INTO fm_ledger (ts, entry_type, amount, bucket, balance_before, "
                "balance_after, reservation_id, reason) VALUES (?, 'RELEASE', ?, ?, ?, ?, ?, ?)",
                (ist_now_iso(), -orig["amount"], orig["bucket"],
                 orig["balance_after"], orig["balance_before"],
                 rid, "crash_test_orphan_cleanup")
            )
    actions.append(f"Released {len(orphans)} orphan reservations")

    conn.commit()
    conn.close()

    _log("soft_cleanup", {"actions": actions})

    # Run invariant check
    inv = _run_invariant_check("--quick")
    inv_a = inv.get("invariants", {}).get("A", {}).get("status", "N/A")
    actions.append(f"Invariant A: {inv_a}")

    print("Soft cleanup complete:")
    for a in actions:
        print(f"  - {a}")
    return {"status": "OK", "actions": actions, "invariant_a": inv_a}


def do_hard(force: bool = False) -> Dict:
    """Hard cleanup: soft + close trades, cancel orders, reset capital."""
    conn = get_db_connection()

    open_pos = _check_open_positions(conn)
    if open_pos > 0 and not force:
        conn.close()
        msg = f"WARNING: System has {open_pos} open positions. Use --force to proceed."
        print(msg)
        return {"status": "BLOCKED", "reason": msg}
    conn.close()

    # Do soft first
    soft_result = do_soft(force=True)
    actions = soft_result.get("actions", [])

    conn = get_db_connection()

    # Cancel open trades
    cur = conn.execute(
        "UPDATE trades SET status='CANCELLED', updated_at=? WHERE status='OPEN'",
        (ist_now_iso(),)
    )
    actions.append(f"Cancelled {cur.rowcount} open trades")

    # Cancel pending orders
    cur = conn.execute(
        "UPDATE orders SET status='CANCELLED', updated_at=? "
        "WHERE status IN ('OPEN','SUBMITTED','TRIGGER_PENDING','PENDING')",
        (ist_now_iso(),)
    )
    actions.append(f"Cancelled {cur.rowcount} pending orders")

    # Reset capital to starting value
    try:
        import csv
        accounts_path = BASE_DIR / "config" / "accounts.csv"
        if accounts_path.exists():
            with open(accounts_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    starting = float(row.get("capital", row.get("starting_capital", 50000)))
                    conn.execute(
                        "UPDATE capital_snapshot SET cash_floor=?, realized_pnl_today=0, "
                        "margin_used=0, margin_reserved=0, updated_at=? WHERE id=1",
                        (starting, ist_now_iso())
                    )
                    actions.append(f"Reset capital to {starting}")
                    break
    except Exception as e:
        actions.append(f"Capital reset skipped: {e}")

    conn.commit()
    conn.close()

    inv = _run_invariant_check("--full")
    actions.append(f"Invariant check: {inv.get('overall', 'N/A')}")

    _log("hard_cleanup", {"actions": actions})
    print("Hard cleanup complete:")
    for a in actions:
        print(f"  - {a}")
    return {"status": "OK", "actions": actions}


def do_nuclear(force: bool = False) -> Dict:
    """Nuclear cleanup: hard + restart service."""
    if not force:
        print("WARNING: Nuclear cleanup requires --force flag.")
        return {"status": "BLOCKED", "reason": "--force required"}

    hard_result = do_hard(force=True)
    actions = hard_result.get("actions", [])

    # Restart service
    import platform
    if platform.system() == "Linux":
        result = subprocess.run(
            ["sudo", "systemctl", "restart", "trading-system"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            actions.append("Service restarted")
        else:
            actions.append(f"Service restart failed: {result.stderr}")

        # Wait for startup
        print("Waiting 15s for startup...")
        time.sleep(15)

        # Check health
        health = http_get(HEALTH_URL)
        if health:
            actions.append(f"Health check: OK")
        else:
            actions.append("Health check: SYSTEM_OFFLINE")
    else:
        actions.append("Service restart skipped (not Linux)")

    inv = _run_invariant_check("--full")
    actions.append(f"Final invariant check: {inv.get('overall', 'N/A')}")

    _log("nuclear_cleanup", {"actions": actions})
    print("Nuclear cleanup complete:")
    for a in actions:
        print(f"  - {a}")
    return {"status": "OK", "actions": actions}


def main():
    parser = argparse.ArgumentParser(description="Cleanup — Crash Test Tool 7")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--soft", action="store_true", help="Soft cleanup")
    group.add_argument("--hard", action="store_true", help="Hard cleanup (requires --force)")
    group.add_argument("--nuclear", action="store_true",
                       help="Nuclear cleanup (requires --force)")
    group.add_argument("--status", action="store_true", help="Show current state")
    parser.add_argument("--force", action="store_true",
                        help="Required for --hard and --nuclear if positions open")
    parser.add_argument("--live", action="store_true",
                        help="(REFUSED) live-system cleanup was removed from this harness")
    args = parser.parse_args()

    # Fail CLOSED and LOUDLY: never let an operator believe the live system was
    # cleaned when this tool only ever touches scratch now.
    if args.live:
        print(
            "REFUSED: cleanup.py no longer operates on the LIVE database.\n"
            "  This is a destructive crash-test tool; it now resets the SCRATCH DB only\n"
            "  (see tests/crash_test/ct_utils.py::assert_not_live_db).\n"
            "  Resetting the real system is an OPERATOR action — do it deliberately,\n"
            "  off-market, with a backup taken first.",
            file=sys.stderr,
        )
        return 2

    print("cleanup.py: operating on the SCRATCH database (live DB is protected).")

    if args.status:
        do_status()
    elif args.soft:
        do_soft(force=args.force)
    elif args.hard:
        do_hard(force=args.force)
    elif args.nuclear:
        do_nuclear(force=args.force)


if __name__ == "__main__":
    main()
