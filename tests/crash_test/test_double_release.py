"""
tests/crash_test/test_double_release.py — Verify double release guard.
Called by CT048 scenario.
"""

from __future__ import annotations

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))

from core.state_store import StateStore
from core.events import EventBus
from capital.fund_manager import FundManager

import logging

logger = logging.getLogger("crash_test.double_release")
logging.basicConfig(level=logging.WARNING)


def main():
    from tests.crash_test.ct_utils import DB_PATH

    store = StateStore(db_path=DB_PATH)
    bus = EventBus()

    fm = FundManager(
        state_store=store,
        bus=bus,
        logger=logger,
        intraday_bucket_pct=0.70,
        positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.10,
    )
    fm.initialize(broker_balance=100000.0)

    # Capture initial state
    snap_before = fm.get_snapshot()
    print(f"Before: available={snap_before.intraday_avail:.2f}")

    # Step 1: Reserve
    res = fm.reserve("TESTSTOCK", 1, 1000.0, "INTRADAY", "test_double_release_sig")
    if not res.success:
        print(f"SKIP: Reserve failed: {res.reason_if_failed}")
        sys.exit(0)

    rid = res.reservation_id
    print(f"Reserved: {rid}, margin={res.margin:.2f}")

    # Step 2: First release (should succeed)
    first_ok = fm.release(rid, "first release (crash test)")
    print(f"First release: {'OK' if first_ok else 'FAILED'}")

    snap_after_first = fm.get_snapshot()
    print(f"After first release: available={snap_after_first.intraday_avail:.2f}")

    # Step 3: Second release (should return False — idempotent, not raise)
    second_ok = fm.release(rid, "second release (crash test)")
    print(f"Second release: {'OK' if second_ok else 'REJECTED (expected)'}")

    snap_after_second = fm.get_snapshot()
    print(f"After second release: available={snap_after_second.intraday_avail:.2f}")

    # Step 4: Verify capital unchanged between first and second release
    capital_unchanged = abs(snap_after_first.intraday_avail - snap_after_second.intraday_avail) < 0.01

    # Report
    print("\n" + "=" * 60)
    print("Double Release Guard Test Results")
    print("=" * 60)

    results = []
    if first_ok:
        results.append(("PASS", "First release succeeded"))
    else:
        results.append(("FAIL", "First release failed"))

    if not second_ok:
        results.append(("PASS", "Second release correctly rejected (returned False)"))
    else:
        results.append(("FAIL", "Second release was NOT rejected (returned True)"))

    if capital_unchanged:
        results.append(("PASS", "Capital unchanged after double release attempt"))
    else:
        results.append(("FAIL", f"Capital changed: {snap_after_first.intraday_avail} -> {snap_after_second.intraday_avail}"))

    fail_count = 0
    for status, detail in results:
        print(f"  [{status}] {detail}")
        if status == "FAIL":
            fail_count += 1

    print(f"\nTotal: {len(results) - fail_count} PASS, {fail_count} FAIL")
    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
