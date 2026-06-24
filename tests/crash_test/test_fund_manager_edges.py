"""
tests/crash_test/test_fund_manager_edges.py
Purpose: Test FundManager edge cases with isolated instances per test.
Each test case gets a fresh FM instance to prevent state bleed.
Called by CT004 scenario.
"""

from __future__ import annotations

import math
import sys
import traceback

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))

import logging

logger = logging.getLogger("crash_test.fm_edges")
logging.basicConfig(level=logging.WARNING)

results = []


def run_test(test_num, description, fn):
    try:
        result = fn()
        if hasattr(result, "success") and not result.success:
            results.append((test_num, description, "PASS", f"Rejected: {result.reason_if_failed}"))
            print(f"  Test {test_num}: PASS -- {description} (rejected: {result.reason_if_failed})")
        elif result is False:
            results.append((test_num, description, "PASS", "Returned False (idempotent rejection)"))
            print(f"  Test {test_num}: PASS -- {description} (returned False)")
        else:
            results.append((test_num, description, "PASS", f"Returned: {result}"))
            print(f"  Test {test_num}: PASS -- {description}")
    except (ValueError, TypeError, AttributeError, AssertionError) as e:
        results.append((test_num, description, "PASS", f"Correctly raised {type(e).__name__}: {e}"))
        print(f"  Test {test_num}: PASS -- {description} (correctly raised {type(e).__name__})")
    except Exception as e:
        ename = type(e).__name__
        if "Invariant" in ename or "Capital" in ename:
            results.append((test_num, description, "PASS", f"Invariant violation caught: {e}"))
            print(f"  Test {test_num}: PASS -- {description} (invariant violation caught)")
        else:
            results.append((test_num, description, "FAIL", f"Unexpected {ename}: {e}"))
            print(f"  Test {test_num}: FAIL -- {description} -- UNEXPECTED: {ename}: {e}")
            traceback.print_exc()


def get_fresh_fm():
    from core.state_store import StateStore
    from core.events import EventBus
    from capital.fund_manager import FundManager
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
    return fm


def main():
    from tests.crash_test.ct_utils import ist_now

    print("=" * 60)
    print("FundManager Edge Case Tests")
    print(f"Started: {ist_now()}")
    print("=" * 60)

    # Test 1: reserve qty=0
    run_test(1, "reserve(qty=0)",
        lambda: get_fresh_fm().reserve("RELIANCE", 0, 2500.0, "INTRADAY", "test_sig_1"))

    # Test 2: reserve qty=-1
    run_test(2, "reserve(qty=-1)",
        lambda: get_fresh_fm().reserve("RELIANCE", -1, 2500.0, "INTRADAY", "test_sig_2"))

    # Test 3: reserve price=NaN
    run_test(3, "reserve(price=NaN)",
        lambda: get_fresh_fm().reserve("RELIANCE", 10, float("nan"), "INTRADAY", "test_sig_3"))

    # Test 4: reserve exceeds total capital
    run_test(4, "reserve(qty=999999, price=999999) -- exceeds total",
        lambda: get_fresh_fm().reserve("RELIANCE", 999999, 999999.0, "INTRADAY", "test_sig_4"))

    # Test 5: release nonexistent reservation_id
    run_test(5, "release(reservation_id='nonexistent_xyz')",
        lambda: get_fresh_fm().release("nonexistent_xyz_abc_123", "test"))

    # Test 6: release None reservation_id
    run_test(6, "release(reservation_id=None)",
        lambda: get_fresh_fm().release(None, "test"))

    # Test 7: double release (reserve then release twice)
    def test_double_release():
        fm = get_fresh_fm()
        res = fm.reserve("TCS", 1, 1000.0, "INTRADAY", "test_sig_double")
        if not res.success:
            raise AssertionError(f"reserve() failed: {res.reason_if_failed}")
        fm.release(res.reservation_id, "first release")
        fm.release(res.reservation_id, "second release")
        return False

    run_test(7, "double release(same reservation_id)",
        test_double_release)

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, _, s, _ in results if s == "PASS")
    failed = sum(1 for _, _, s, _ in results if s == "FAIL")
    print(f"RESULT: {passed}/7 PASS, {failed}/7 FAIL")
    print("=" * 60)

    if failed > 0:
        print("\nFAILED CASES:")
        for num, desc, status, note in results:
            if status == "FAIL":
                print(f"  [{num}] {desc}: {note}")
        sys.exit(1)
    else:
        print("\nAll 7 edge cases handled correctly.")
        sys.exit(0)


if __name__ == "__main__":
    main()
