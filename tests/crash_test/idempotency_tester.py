"""
tests/crash_test/idempotency_tester.py — Tool 8: Prove scenario idempotency.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import (
    ist_now_iso, RESULTS_DIR, SNAPSHOTS_DIR, BASE_DIR,
)

TOOL_DIR = Path(__file__).resolve().parent


def _run_tool(tool_name: str, args: list[str]) -> tuple:
    cmd = [sys.executable, str(TOOL_DIR / tool_name)] + args
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
    return result.returncode, result.stdout, result.stderr


def _capture_state_summary() -> Dict[str, Any]:
    """Capture relevant state for comparison."""
    from tests.crash_test.ct_utils import get_db_connection
    conn = get_db_connection(readonly=True)
    summary = {}

    # Capital state
    row = conn.execute(
        "SELECT cash_floor, realized_pnl_today, margin_used, margin_reserved "
        "FROM capital_snapshot WHERE id=1"
    ).fetchone()
    if row:
        summary["capital"] = {
            "available": round(row["cash_floor"] + row["realized_pnl_today"]
                               - row["margin_used"] - row["margin_reserved"], 2),
            "reserved": row["margin_reserved"],
            "used": row["margin_used"],
            "total": round(row["cash_floor"] + row["realized_pnl_today"], 2),
        }

    # Signal counts by status
    rows = conn.execute("SELECT status, COUNT(*) as cnt FROM signals GROUP BY status").fetchall()
    summary["signal_counts"] = {r["status"]: r["cnt"] for r in rows}

    # Kill switch
    ks = conn.execute("SELECT state FROM kill_switch_state WHERE id=1").fetchone()
    summary["kill_switch"] = ks["state"] if ks else "NO_RECORD"

    # Open trade count
    row = conn.execute("SELECT COUNT(*) as cnt FROM trades WHERE status='OPEN'").fetchone()
    summary["open_trades"] = row["cnt"]

    # Open order count
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM orders WHERE status IN ('OPEN','SUBMITTED','TRIGGER_PENDING')"
    ).fetchone()
    summary["open_orders"] = row["cnt"]

    # Orphan check
    orphans = conn.execute(
        "SELECT COUNT(*) as cnt FROM fm_ledger WHERE entry_type='RESERVE' "
        "AND reservation_id NOT IN "
        "(SELECT reservation_id FROM fm_ledger "
        " WHERE entry_type IN ('COMMIT','RELEASE','RELEASE_USED') "
        " AND reservation_id IS NOT NULL) "
        "AND reservation_id IS NOT NULL"
    ).fetchone()
    summary["orphan_reservations"] = orphans["cnt"]

    conn.close()
    return summary


def _compare_states(s1: Dict, s2: Dict) -> List[Dict]:
    """Compare two state summaries, return list of diffs."""
    diffs = []

    # Capital comparison
    c1 = s1.get("capital", {})
    c2 = s2.get("capital", {})
    for key in ["available", "reserved", "used", "total"]:
        v1 = c1.get(key, 0)
        v2 = c2.get(key, 0)
        if abs(v1 - v2) > 1.0:
            diffs.append({"field": f"capital.{key}", "run1": v1, "run2": v2})

    # Kill switch
    if s1.get("kill_switch") != s2.get("kill_switch"):
        diffs.append({"field": "kill_switch",
                      "run1": s1.get("kill_switch"), "run2": s2.get("kill_switch")})

    # Open trades/orders should be same
    if s1.get("open_trades") != s2.get("open_trades"):
        diffs.append({"field": "open_trades",
                      "run1": s1.get("open_trades"), "run2": s2.get("open_trades")})
    if s1.get("open_orders") != s2.get("open_orders"):
        diffs.append({"field": "open_orders",
                      "run1": s1.get("open_orders"), "run2": s2.get("open_orders")})

    # Orphan reservations should not grow
    if s2.get("orphan_reservations", 0) > s1.get("orphan_reservations", 0):
        diffs.append({"field": "orphan_reservations",
                      "run1": s1.get("orphan_reservations"),
                      "run2": s2.get("orphan_reservations")})

    return diffs


def run_idempotency_test(scenario_id: str, runs: int = 3) -> Dict:
    """Run a scenario N times and check for drift."""
    print(f"Idempotency test: {scenario_id} x {runs} runs")
    print("=" * 60)

    states = []
    all_diffs = []

    for i in range(runs):
        print(f"\n--- Run {i+1}/{runs} ---")

        # Run scenario
        rc, out, err = _run_tool("scenario_runner.py", ["--scenario", scenario_id, "--force"])
        if rc != 0:
            print(f"  Scenario run failed: {err[:200]}")

        # Capture state
        state = _capture_state_summary()
        states.append(state)
        print(f"  State: capital={state.get('capital', {}).get('available', '?')}, "
              f"open_trades={state.get('open_trades', '?')}, "
              f"kill_switch={state.get('kill_switch', '?')}")

        # Compare with first run
        if i > 0:
            diffs = _compare_states(states[0], state)
            if diffs:
                print(f"  DRIFT from run 1: {len(diffs)} differences")
                for d in diffs:
                    print(f"    {d['field']}: {d['run1']} -> {d['run2']}")
                all_diffs.extend(diffs)
            else:
                print(f"  Identical to run 1")

        # Cleanup between runs (except last)
        if i < runs - 1:
            print(f"  Cleaning up...")
            _run_tool("cleanup.py", ["--soft", "--force"])

    result_status = "IDEMPOTENT" if not all_diffs else "DRIFT_DETECTED"
    result = {
        "scenario_id": scenario_id,
        "runs": runs,
        "result": result_status,
        "diffs": all_diffs,
        "states": states,
        "timestamp": ist_now_iso(),
    }

    print(f"\n{'='*60}")
    print(f"Result: {result_status}")
    if all_diffs:
        print(f"Drifts detected: {len(all_diffs)}")
    print(f"{'='*60}")

    # Save result
    out_path = RESULTS_DIR / f"idempotency_{scenario_id}.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"Result saved: {out_path}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Idempotency Tester — Crash Test Tool 8")
    parser.add_argument("--scenario", type=str, help="Scenario ID to test")
    parser.add_argument("--runs", type=int, default=3, help="Number of runs (default: 3)")
    args = parser.parse_args()

    if not args.scenario:
        parser.print_help()
        sys.exit(0)

    run_idempotency_test(args.scenario, args.runs)


if __name__ == "__main__":
    main()
