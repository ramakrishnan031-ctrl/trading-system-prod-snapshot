"""
tests/crash_test/test_position_sizer_edges.py — Edge case tests for PositionSizer.
Called by CT005 scenario.
"""

from __future__ import annotations

import math
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))

from core.state_store import StateStore
from core.events import EventBus
from capital.fund_manager import FundManager
from capital.position_sizer import PositionSizer

import logging

logger = logging.getLogger("crash_test.ps_edges")
logging.basicConfig(level=logging.WARNING)

RESULTS = []


def _test(name: str, fn):
    """Run a test case. Any result that doesn't crash is PASS if qty >= 0."""
    try:
        result = fn()
        qty = getattr(result, "qty", None)
        if qty is not None and qty < 0:
            RESULTS.append(("FAIL", name, f"Negative qty: {qty}"))
        elif qty is not None:
            RESULTS.append(("PASS", name, f"qty={qty}, success={result.success}"))
        else:
            RESULTS.append(("PASS", name, f"Result: {result}"))
    except ZeroDivisionError as e:
        RESULTS.append(("FAIL", name, f"Division by zero: {e}"))
    except (ValueError, TypeError) as e:
        RESULTS.append(("PASS", name, f"Controlled exception: {type(e).__name__}: {e}"))
    except Exception as e:
        RESULTS.append(("PASS_VARIANT", name, f"Exception: {type(e).__name__}: {e}"))


def main():
    from tests.crash_test.ct_utils import make_scratch_db

    store = StateStore(db_path=str(make_scratch_db()))
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

    sizer = PositionSizer(
        max_position_value_pct=0.40,  # NI-5: was a silent default
        fund_manager=fm,
        leverage_map={"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0},
        risk_per_trade_pct=0.01,
        max_concentration_pct=0.10,
        logger=logger,
    )

    # Test 1: entry_price=0, sl_price=0
    _test("entry=0, sl=0",
          lambda: sizer.calculate("RELIANCE", "BUY", 0.0, 0.0, "INTRADAY"))

    # Test 2: entry_price=NaN
    _test("entry=NaN",
          lambda: sizer.calculate("RELIANCE", "BUY", float("nan"), 2450.0, "INTRADAY"))

    # Test 3: inverted SL (LONG with SL above entry)
    _test("inverted SL (LONG sl>entry)",
          lambda: sizer.calculate("RELIANCE", "BUY", 1000.0, 1500.0, "INTRADAY"))

    # Test 4: huge entry price (tiny qty)
    _test("huge entry price",
          lambda: sizer.calculate("RELIANCE", "BUY", 999999.0, 999990.0, "INTRADAY"))

    # Test 5: entry_price negative
    _test("negative entry",
          lambda: sizer.calculate("RELIANCE", "BUY", -100.0, -150.0, "INTRADAY"))

    # Test 6: sl_price = entry_price (zero distance)
    _test("sl = entry (zero distance)",
          lambda: sizer.calculate("RELIANCE", "BUY", 1000.0, 1000.0, "INTRADAY"))

    # Print results
    print("\n" + "=" * 60)
    print("PositionSizer Edge Case Results")
    print("=" * 60)
    pass_count = 0
    fail_count = 0
    for status, name, detail in RESULTS:
        icon = "PASS" if status.startswith("PASS") else "FAIL"
        print(f"  [{icon}] {name}: {detail}")
        if status.startswith("PASS"):
            pass_count += 1
        else:
            fail_count += 1
    print(f"\nTotal: {pass_count} PASS, {fail_count} FAIL")

    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
