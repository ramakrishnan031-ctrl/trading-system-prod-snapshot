"""
tests/unit/test_ma2_alert_send_deadline.py -- Trading System v2

M-A2: alert sends are fully synchronous in the caller's thread. The HTTP call has
a 5 s timeout, but nothing bounds the WHOLE send:

  * ``_SlidingWindowRateLimiter.acquire()`` is a ``while True`` poll with NO
    deadline -- under an alert storm it blocks the caller indefinitely.
  * the retry ladder (max_retries+1 attempts x timeout_sec, plus a backoff sleep
    between each) is per-chat and runs once per enabled channel.

That matters because the sends sit on live paths -- notably
``signals/signal_processor.py:1131`` ("Telegram alert: INTRADAY SIGNAL (fires
before order placement)"), with capital already reserved.

The fix is a TIMEOUT, not async dispatch: one wall-clock deadline per ``send()``,
threaded through the channel loop, the rate limiter, the HTTP timeout and the
backoff sleeps. Async was rejected deliberately -- it introduces the
publish-then-read race documented in M-O9.

These tests use a FAKE CLOCK (``time.monotonic`` + ``time.sleep`` patched inside
the notifier module) so they are deterministic and fast: no test sleeps for real.
"""
from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import requests

import alerts.telegram_notifier as tn
from alerts.telegram_notifier import TelegramNotifier, _SlidingWindowRateLimiter


# ==============================================================================
# Fake clock -- sleep() advances monotonic(); nothing blocks for real.
# ==============================================================================

class _FakeClock:
    """Stands in for the `time` module *as the notifier module sees it*.

    Replacing the module NAME inside alerts.telegram_notifier (not attributes on
    the real `time` module) keeps the fake clock from leaking into pytest,
    logging or any other thread for the duration of the test.
    """

    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, secs: float) -> None:
        self.slept.append(secs)
        self.t += max(0.0, float(secs))

    def advance(self, secs: float) -> None:
        self.t += float(secs)


def _install(clock: _FakeClock):
    return patch.object(tn, "time", clock)


def _make_notifier(tmpdir: Path, **kwargs) -> TelegramNotifier:
    log = logging.getLogger("test_ma2")
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return TelegramNotifier(
        bot_token="tok",
        chat_ids=kwargs.pop("chat_ids", ["-100111"]),
        failed_alerts_log_path=tmpdir / "failed_alerts.log",
        sentinel_dir=tmpdir / "sentinels",
        logger=log,
        timeout_sec=kwargs.pop("timeout_sec", 5.0),
        max_retries=kwargs.pop("max_retries", 3),
        retry_backoff_seconds=kwargs.pop("retry_backoff_seconds", 2.0),
        rate_limit_per_minute=kwargs.pop("rate_limit_per_minute", 20),
        enabled=True,
        **kwargs,
    )


class _HangingEndpoint:
    """requests.post stand-in for an endpoint that accepts and never answers.

    Consumes exactly the `timeout` it was given off the fake clock, then raises
    requests.Timeout -- what a real hung endpoint does.
    """

    def __init__(self, clock: _FakeClock) -> None:
        self.clock = clock
        self.calls: list[float] = []

    def __call__(self, url, json=None, timeout=None, **kw):  # noqa: A002
        self.calls.append(float(timeout))
        self.clock.advance(float(timeout))
        raise requests.Timeout("hung endpoint")


# ==============================================================================
# 1. The rate limiter must be interruptible
# ==============================================================================

class TestRateLimiterTimeout(unittest.TestCase):

    def test_acquire_accepts_a_timeout_and_reports_failure(self) -> None:
        """RED on HEAD: acquire() takes no timeout at all (TypeError)."""
        clock = _FakeClock()
        rl = _SlidingWindowRateLimiter(1)
        with _install(clock):
            self.assertTrue(rl.acquire(timeout=10.0))   # window empty -> immediate
            t_before = clock.t
            # window is now full for 60 s; a 5 s budget must give up, not spin.
            self.assertFalse(rl.acquire(timeout=5.0))
            self.assertLessEqual(clock.t - t_before, 5.0 + 1e-6)

    def test_acquire_without_timeout_still_blocks_until_a_slot_frees(self) -> None:
        """The unbounded form is PRESERVED (opt-out): timeout=None waits."""
        clock = _FakeClock()
        rl = _SlidingWindowRateLimiter(1)
        with _install(clock):
            self.assertTrue(rl.acquire())
            t_before = clock.t
            self.assertTrue(rl.acquire())          # spins on the fake clock
            self.assertGreater(clock.t - t_before, 59.0)

    def test_acquire_does_not_overshoot_the_deadline_by_a_poll_interval(self) -> None:
        """A 0.2 s budget must not sleep a whole 0.5 s poll past it."""
        clock = _FakeClock()
        rl = _SlidingWindowRateLimiter(1)
        with _install(clock):
            rl.acquire()
            t_before = clock.t
            self.assertFalse(rl.acquire(timeout=0.2))
            self.assertLessEqual(clock.t - t_before, 0.2 + 1e-6)


# ==============================================================================
# 2. A hung endpoint must not hold the caller past the deadline
# ==============================================================================

class TestSendDeadline(unittest.TestCase):

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_hung_endpoint_returns_within_the_deadline(self) -> None:
        """RED on HEAD: HEAD runs the full ladder (4 posts + 3 backoffs = 26 s).

        deadline 10 s, timeout 5 s, backoff 2 s:
            post#1 -> t=5, backoff -> t=7, post#2 capped to 3 s -> t=10, stop.
        """
        clock = _FakeClock()
        n = _make_notifier(self.tmp, send_deadline_seconds=10.0,
                           timeout_sec=5.0, max_retries=3,
                           retry_backoff_seconds=2.0)
        post = _HangingEndpoint(clock)
        t0 = clock.t
        with _install(clock), patch("alerts.telegram_notifier.requests.post", new=post):
            res = n.send("ERROR", "t", "b", "unit_test")
        self.assertFalse(res.success)
        self.assertLessEqual(clock.t - t0, 10.0 + 1e-6,
                             "send() blocked past its deadline")
        self.assertEqual(len(post.calls), 2,
                         "the ladder must stop once the budget is spent")

    def test_the_last_http_timeout_is_clamped_to_the_remaining_budget(self) -> None:
        """A 5 s HTTP timeout must not be allowed to overrun a 3 s remainder."""
        clock = _FakeClock()
        n = _make_notifier(self.tmp, send_deadline_seconds=10.0,
                           timeout_sec=5.0, max_retries=3,
                           retry_backoff_seconds=2.0)
        post = _HangingEndpoint(clock)
        with _install(clock), patch("alerts.telegram_notifier.requests.post", new=post):
            n.send("ERROR", "t", "b", "unit_test")
        self.assertEqual(post.calls, [5.0, 3.0])

    def test_deadline_is_shared_across_channels_not_per_channel(self) -> None:
        """RED on HEAD: HEAD pays the full ladder ONCE PER CHAT (3x26 s)."""
        clock = _FakeClock()
        n = _make_notifier(self.tmp, chat_ids=["-1", "-2", "-3"],
                           send_deadline_seconds=10.0, timeout_sec=5.0,
                           max_retries=3, retry_backoff_seconds=2.0)
        post = _HangingEndpoint(clock)
        t0 = clock.t
        with _install(clock), patch("alerts.telegram_notifier.requests.post", new=post):
            res = n.send("ERROR", "t", "b", "unit_test")
        self.assertLessEqual(clock.t - t0, 10.0 + 1e-6)
        self.assertEqual(len(res.failed_to), 3)

    def test_rate_limit_storm_does_not_hold_the_caller(self) -> None:
        """RED on HEAD: the 2nd send spins ~60 s inside acquire() with no bound."""
        clock = _FakeClock()
        n = _make_notifier(self.tmp, rate_limit_per_minute=1,
                           send_deadline_seconds=5.0)

        def ok(url, json=None, timeout=None, **kw):  # noqa: A002
            class _R:
                status_code = 200
                headers: dict = {}
                text = "ok"
            return _R()

        with _install(clock), patch("alerts.telegram_notifier.requests.post", new=ok):
            first = n.send("INFO", "t", "b", "unit_test")
            self.assertTrue(first.success)
            t0 = clock.t
            second = n.send("INFO", "t", "b", "unit_test")
        self.assertFalse(second.success, "the storm-blocked send must give up")
        self.assertLessEqual(clock.t - t0, 5.0 + 1e-6)

    def test_deadline_none_preserves_the_unbounded_ladder(self) -> None:
        """Opt-out pin: send_deadline_seconds=None keeps today's behaviour."""
        clock = _FakeClock()
        n = _make_notifier(self.tmp, send_deadline_seconds=None,
                           timeout_sec=5.0, max_retries=3,
                           retry_backoff_seconds=2.0)
        post = _HangingEndpoint(clock)
        with _install(clock), patch("alerts.telegram_notifier.requests.post", new=post):
            n.send("ERROR", "t", "b", "unit_test")
        self.assertEqual(post.calls, [5.0, 5.0, 5.0, 5.0])

    def test_a_healthy_endpoint_is_untouched_by_the_deadline(self) -> None:
        """Anti-vacuity: the deadline must not break the normal success path."""
        clock = _FakeClock()
        n = _make_notifier(self.tmp, send_deadline_seconds=30.0)
        seen: list = []

        def ok(url, json=None, timeout=None, **kw):  # noqa: A002
            seen.append(timeout)

            class _R:
                status_code = 200
                headers: dict = {}
                text = "ok"
            return _R()

        with _install(clock), patch("alerts.telegram_notifier.requests.post", new=ok):
            res = n.send("INFO", "t", "b", "unit_test")
        self.assertTrue(res.success)
        self.assertEqual(res.delivered_to, ["-100111"])
        self.assertEqual(seen, [5.0], "healthy send keeps the configured timeout")

    def test_parity_paper_mode_send_is_bounded_the_same_way(self) -> None:
        """PARITY: with telegram_alerts_in_paper_mode=true paper does real HTTP,
        so paper must get the identical bound (one code path, no mode branch)."""
        clock = _FakeClock()
        n = _make_notifier(self.tmp, paper_mode=True, send_in_paper_mode=True,
                           send_deadline_seconds=10.0, timeout_sec=5.0,
                           max_retries=3, retry_backoff_seconds=2.0)
        post = _HangingEndpoint(clock)
        t0 = clock.t
        with _install(clock), patch("alerts.telegram_notifier.requests.post", new=post):
            n.send("ERROR", "t", "b", "unit_test")
        self.assertLessEqual(clock.t - t0, 10.0 + 1e-6)
        self.assertEqual(len(post.calls), 2)

    def test_critical_still_writes_its_sentinel_before_the_deadline_bites(self) -> None:
        """The bound must not cost an alert: CRITICAL writes the sentinel FIRST
        (TG5), so giving up on Telegram never loses the alert."""
        clock = _FakeClock()
        n = _make_notifier(self.tmp, send_deadline_seconds=10.0)
        post = _HangingEndpoint(clock)
        with _install(clock), patch("alerts.telegram_notifier.requests.post", new=post):
            res = n.send("CRITICAL", "kill", "body", "kill_switch")
        self.assertFalse(res.success)
        self.assertIsNotNone(res.sentinel_path)
        self.assertTrue(Path(res.sentinel_path).exists())


class TestDeadlineConfigWiring(unittest.TestCase):

    def test_telegram_config_exposes_send_deadline_seconds(self) -> None:
        """RED on HEAD: TelegramConfig has no such field (extra='forbid')."""
        from core.config_loader import TelegramConfig
        cfg = TelegramConfig(
            channels=[{"chat_id_env": "X", "label": "x", "enabled": True}],
            send_deadline_seconds=25.0,
        )
        self.assertEqual(cfg.send_deadline_seconds, 25.0)

    def test_system_config_yaml_sets_the_key(self) -> None:
        """The shipped config must carry the bound, not rely on the default."""
        import yaml
        raw = yaml.safe_load(
            (Path(__file__).parent.parent.parent
             / "config" / "system_config.yaml").read_text(encoding="utf-8"))
        tg = raw["alerts"]["telegram"]
        self.assertIn("send_deadline_seconds", tg)
        self.assertGreater(float(tg["send_deadline_seconds"]), 0.0)


if __name__ == "__main__":
    unittest.main()
