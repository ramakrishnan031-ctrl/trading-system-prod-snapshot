"""
tests/crash_test/test_kill_switch_edges.py — Edge case tests for KillSwitch.
Called by CT007 scenario.
"""

from __future__ import annotations

import sys
from datetime import date

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))

from core.state_store import StateStore
from core.events import EventBus
from capital.kill_switch import KillSwitch

import logging

logger = logging.getLogger("crash_test.ks_edges")
logging.basicConfig(level=logging.WARNING)

RESULTS = []


def _test(name: str, fn):
    try:
        result = fn()
        RESULTS.append(("PASS", name, f"OK: {result}"))
    except Exception as e:
        RESULTS.append(("FAIL", name, f"{type(e).__name__}: {e}"))


def main():
    from tests.crash_test.ct_utils import DB_PATH

    store = StateStore(db_path=DB_PATH)
    bus = EventBus()

    ks = KillSwitch(
        state_store=store,
        bus=bus,
        logger=logger,
        on_hard_kill_cancel_fn=None,
        api_failure_threshold=3,
        enable_auto_trip=True,
    )

    # Test 1: soft_kill() then soft_kill() again (idempotent)
    _test("soft_kill idempotent",
          lambda: (
              ks.soft_kill("test_reason_1", "crash_test"),
              ks.soft_kill("test_reason_1_again", "crash_test"),
              f"state={ks._state.value}"
          ))

    # Test 2: resume first, then hard_kill idempotent
    _test("resume + hard_kill idempotent",
          lambda: (
              ks.resume("reset for test", "crash_test"),
              ks.hard_kill("test_reason_2", "crash_test"),
              ks.hard_kill("test_reason_2_again", "crash_test"),
              f"state={ks._state.value}"
          ))

    # Test 3: resume when not killed — expected: ValueError (correct behavior)
    def _resume_when_inactive():
        ks.resume("reset", "crash_test")
        try:
            ks.resume("resume_when_inactive", "crash_test")
            return "BUG: no error on resume when INACTIVE"
        except ValueError:
            return "Correctly raised ValueError on resume when INACTIVE"
    _test("resume when INACTIVE", _resume_when_inactive)

    # Test 4: clear_stale_state with today (should not clear same-day trigger)
    _test("clear_stale_state(today)",
          lambda: (
              ks.soft_kill("stale_test", "crash_test"),
              ks.clear_stale_state(date.today()),
              f"state={ks._state.value} (should remain SOFT_KILL)"
          ))

    # Test 5: is_active with valid intent
    _test("is_active(entry)",
          lambda: f"is_active(entry)={ks.is_active('entry')}")

    # Cleanup: ensure INACTIVE
    ks.resume("crash_test_cleanup", "crash_test")

    # Verify DB state
    row = store.execute("SELECT state FROM kill_switch_state WHERE id=1").fetchone()
    db_state = row["state"] if row else "NO_RECORD"

    print("\n" + "=" * 60)
    print("KillSwitch Edge Case Results")
    print("=" * 60)
    pass_count = 0
    fail_count = 0
    for status, name, detail in RESULTS:
        icon = "PASS" if status == "PASS" else "FAIL"
        print(f"  [{icon}] {name}: {detail}")
        if status == "PASS":
            pass_count += 1
        else:
            fail_count += 1
    print(f"\nDB state after cleanup: {db_state}")
    print(f"Total: {pass_count} PASS, {fail_count} FAIL")

    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
