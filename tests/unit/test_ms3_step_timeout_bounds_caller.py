"""
tests/unit/test_ms3_step_timeout_bounds_caller.py -- Trading System v2

M-S3: the per-step timeout does not bound what it claims to bound.

`StepExecutor` reuses ONE instance-level, single-worker ThreadPoolExecutor
across every run_all() call (FIX-100). `future.result(timeout=...)` only
abandons the WAIT -- a timed-out step is still running on that one worker, and
Python cannot interrupt it. So the next submit() queues behind the hung task and
times out too, and so on for the remaining steps; then EVERY LATER SIGNAL queues
behind the same hung task as well. One hung step therefore costs the caller
10x the timeout and poisons the screening pipeline permanently.

⚠️ INERT IN PRODUCTION -- measured, not assumed. Over 287,600 recorded step
latencies (screener_results.latencies, 12-Jun..24-Jul), the slowest single step
ever is 66.06 ms against a 5,000 ms timeout; zero rows reach even 1,000 ms and
no timeout signature exists. Structurally the ten step bodies are pure dict and
float arithmetic -- no I/O, no network, no lock, no DB; now_ist() is
datetime.now(tz). Nothing in them can block for 5 s. These tests drive the
defect with an injected blocking step because production cannot.

The fix ROTATES the poisoned executor instead of pretending the task was
cancelled: the hung worker is abandoned (shutdown(wait=False)) and a fresh
single-worker pool takes over, so the remaining steps of THIS signal and every
later signal run normally.
"""
from __future__ import annotations

import logging
import sys
import threading
import unittest
from datetime import datetime, time as dt_time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from screening.step_executor import StepExecutor


_MD = {
    "avg_volume_20d": 1000.0, "volume": 5000.0, "ltp": 100.0, "vwap": 99.0,
    "atr": 2.0, "rsi": 55.0, "open": 99.0, "day_high": 101.0, "day_low": 98.0,
    "sector": "IT", "bid": 99.9, "ask": 100.1, "circuit_state": "",
}
_THR = {"min_volume_surge": 1.5, "min_adr_pct": 0.5, "max_spread_pct": 0.5}
_SIG = {"direction": "LONG", "symbol": "TESTSYM", "triggered_at": None}


def _logger():
    log = logging.getLogger("test_ms3")
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return log


class _Blocker:
    """A step body that blocks the worker until released.

    `block_first_n` calls block; later calls return immediately, so a test can
    show that a LATER signal is (or is not) still poisoned by the earlier hang.
    """

    def __init__(self, block_first_n: int = 1) -> None:
        self.gate = threading.Event()
        self.calls = 0
        self.block_first_n = block_first_n
        self._lock = threading.Lock()

    def __call__(self, signal, md, thr, direction) -> float:
        with self._lock:
            self.calls += 1
            n = self.calls
        if n <= self.block_first_n:
            self.gate.wait(timeout=30.0)   # released in tearDown; never hangs CI
        return 1.0

    def release(self) -> None:
        self.gate.set()


class TestStepTimeoutBoundsTheCaller(unittest.TestCase):

    def setUp(self) -> None:
        self.blocker = _Blocker()
        self.ex = StepExecutor(
            logger=_logger(),
            market_open=dt_time(9, 15),
            step_timeout_sec=0.05,
        )

    def tearDown(self) -> None:
        self.blocker.release()
        try:
            self.ex.shutdown()
        except Exception:
            pass

    # ── the defect ────────────────────────────────────────────────────────────

    def test_one_hung_step_does_not_time_out_every_other_step(self) -> None:
        """RED on HEAD: steps 2..10 queue behind the hung worker -> all TIMEOUT."""
        self.ex._step_1_volume_surge = self.blocker
        res = self.ex.run_all(_SIG, _MD, _THR)

        self.assertEqual(res.step_statuses["volume_surge"], "TIMEOUT")
        timed_out = [k for k, v in res.step_statuses.items() if v == "TIMEOUT"]
        self.assertEqual(
            timed_out, ["volume_surge"],
            "only the hung step may time out; the other nine must still run",
        )
        self.assertEqual(len(res.step_statuses), 10)
        # and they must carry REAL scores, not the neutral 0.5 a timeout leaves
        self.assertEqual(res.step_results["vwap_position"], 1.0)
        self.assertEqual(res.step_results["circuit_check"], 1.0)

    def test_a_hung_step_does_not_poison_the_next_signal(self) -> None:
        """RED on HEAD: the shared single worker is still stuck, so signal #2
        times out on every step even though its own steps are instant."""
        self.ex._step_1_volume_surge = self.blocker      # blocks once
        self.ex.run_all(_SIG, _MD, _THR)                 # signal 1: hangs step 1

        res2 = self.ex.run_all(_SIG, _MD, _THR)          # signal 2: nothing slow
        self.assertNotIn(
            "TIMEOUT", set(res2.step_statuses.values()),
            "a later signal must not inherit the earlier hang",
        )
        self.assertEqual(len(res2.step_statuses), 10)

    def test_the_poisoned_executor_is_replaced_not_reused(self) -> None:
        """RED on HEAD: the instance keeps the same poisoned pool forever."""
        self.ex._step_1_volume_surge = self.blocker
        before = self.ex._executor
        self.ex.run_all(_SIG, _MD, _THR)
        self.assertIsNot(
            self.ex._executor, before,
            "the pool holding the hung task must be abandoned, not reused",
        )

    # ── behaviour that must NOT change ────────────────────────────────────────

    def test_pin_timeout_still_scores_neutral_and_records_latency(self) -> None:
        """PIN (green on HEAD): the TIMEOUT contract itself is unchanged --
        neutral 0.5, status TIMEOUT, a latency recorded, and NOT counted as an
        error or as the rejection point. Goes red if the fix alters it."""
        self.ex._step_1_volume_surge = self.blocker
        res = self.ex.run_all(_SIG, _MD, _THR)
        self.assertEqual(res.step_results["volume_surge"], 0.5)
        self.assertEqual(res.step_statuses["volume_surge"], "TIMEOUT")
        self.assertIn("volume_surge", res.latencies_ms)
        self.assertNotIn("volume_surge", res.error_steps)
        self.assertNotEqual(res.rejected_at, "volume_surge")

    def test_pin_clean_run_is_unaffected(self) -> None:
        """PIN: with no hung step, all ten run and none times out."""
        res = self.ex.run_all(_SIG, _MD, _THR)
        self.assertEqual(len(res.step_results), 10)
        self.assertNotIn("TIMEOUT", set(res.step_statuses.values()))

    def test_pin_exclude_steps_still_honoured(self) -> None:
        """PIN: the v3 exclude path is untouched."""
        res = self.ex.run_all(_SIG, _MD, _THR,
                              exclude_steps={"circuit_check", "signal_age"})
        self.assertEqual(len(res.step_results), 8)
        self.assertNotIn("circuit_check", res.step_results)

    def test_shutdown_after_a_rotation_is_clean_and_idempotent(self) -> None:
        """A rotation must not leave shutdown() shutting down a stale pool."""
        self.ex._step_1_volume_surge = self.blocker
        self.ex.run_all(_SIG, _MD, _THR)
        self.blocker.release()
        self.ex.shutdown()
        self.ex.shutdown()          # idempotent (FIX-100 contract)

    def test_run_all_after_shutdown_does_not_resurrect_the_pool(self) -> None:
        """shutdown() is terminal: a post-shutdown run must not silently
        rotate itself a brand-new working executor."""
        self.ex.shutdown()
        res = self.ex.run_all(_SIG, _MD, _THR)
        self.assertTrue(self.ex._executor_shutdown)
        self.assertEqual(len(res.step_statuses), 10)   # never raises (SE5)


if __name__ == "__main__":
    unittest.main()
