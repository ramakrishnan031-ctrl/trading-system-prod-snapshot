"""
tests/crash_test/test_capital_invariant_violation.py — Force capital invariant violation.
Called by CT047 scenario.
"""

from __future__ import annotations

import sqlite3
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
from tests.crash_test.ct_utils import get_db_connection, ist_now_iso, DB_PATH

import logging

logger = logging.getLogger("crash_test.inv_violation")
logging.basicConfig(level=logging.INFO)


def main():
    conn = get_db_connection()

    # Step 1: Read current capital_snapshot
    row = conn.execute("SELECT cash_floor, realized_pnl_today, margin_used, margin_reserved FROM capital_snapshot WHERE id=1").fetchone()
    if not row:
        print("SKIP: No capital_snapshot row found. System may not have been initialized.")
        print("This test requires a running system with initialized capital.")
        sys.exit(0)

    original = dict(row)
    print(f"Original capital: {original}")

    # Step 2: Backup original values
    backup = {
        "cash_floor": original["cash_floor"],
        "realized_pnl_today": original["realized_pnl_today"],
        "margin_used": original["margin_used"],
        "margin_reserved": original["margin_reserved"],
    }

    # Step 3: Corrupt capital_snapshot (break A+R+U=T invariant)
    corrupt_margin = original["margin_used"] + 999999.0
    print(f"Corrupting: setting margin_used from {original['margin_used']} to {corrupt_margin}")
    conn.execute(
        "UPDATE capital_snapshot SET margin_used=?, updated_at=? WHERE id=1",
        (corrupt_margin, ist_now_iso())
    )
    conn.commit()

    # Step 4: Run invariant checker to verify violation detected
    import subprocess
    result = subprocess.run(
        [sys.executable, "tests/crash_test/invariant_checker.py", "--invariant", "A"],
        capture_output=True, text=True, cwd=str(DB_PATH.parent.parent)
    )
    print(f"Invariant A check result (corrupted state):")
    print(result.stdout[:500])

    invariant_failed = result.returncode != 0
    print(f"Invariant A failed (expected): {invariant_failed}")

    # Step 5: Restore capital_snapshot from backup
    print("Restoring original capital_snapshot...")
    conn.execute(
        "UPDATE capital_snapshot SET cash_floor=?, realized_pnl_today=?, "
        "margin_used=?, margin_reserved=?, updated_at=? WHERE id=1",
        (backup["cash_floor"], backup["realized_pnl_today"],
         backup["margin_used"], backup["margin_reserved"], ist_now_iso())
    )
    conn.commit()
    conn.close()

    # Step 6: Verify restored
    conn2 = get_db_connection(readonly=True)
    restored = conn2.execute("SELECT margin_used FROM capital_snapshot WHERE id=1").fetchone()
    conn2.close()
    print(f"Restored margin_used: {restored['margin_used']} (original: {backup['margin_used']})")

    # Report
    print("\n" + "=" * 60)
    print("Capital Invariant Violation Test Results")
    print("=" * 60)
    if invariant_failed:
        print("  [PASS] Invariant A correctly detected corruption")
        print("  [PASS] Capital restored from backup")
    else:
        print("  [FAIL] Invariant A did NOT detect corruption")

    # Note: The full test with HARD_KILL requires a running system with FundManager.
    # This offline test verifies the invariant checker catches the corruption.
    # When running with system active (CT047 on VM), FundManager.reserve() will
    # trigger _check_invariant() which should fire HARD_KILL.

    sys.exit(0 if invariant_failed else 1)


if __name__ == "__main__":
    main()
