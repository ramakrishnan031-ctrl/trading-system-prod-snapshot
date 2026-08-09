"""tests/unit/test_alert_delivery_phase2.py — PHASE 2 of the alert remediation.

ONE site: `scripts/alert_watcher.py`'s Telegram fallback batch — the single genuine
propagation defect the 09-Aug sweep found.

⚠️ THIS PHASE HAS A DIFFERENT SHAPE FROM PHASES 0 AND 1, AND THAT IS THE POINT.
Phases 0/1 added a record to an EXISTING swallow. Here there is NO swallow: the
`notifier.send(...)` is unguarded, so the fix must STOP the propagation *and*
record. Same contract, same vocabulary, no second mechanism — `send_alert_recorded`
already does both, and it returns exactly the delivered/not-delivered boolean this
loop needs for `mark_delivered`.

🔑 THE BOUNDARY, TRACED BEFORE TOUCHING ANYTHING — and it is worse than the sweep
classified it. `run_once` ends `return 0` under the comment *"F1: a DELIVERY failure
is NOT a crash — never return non-zero for a delivery/auth fault (the cause of the
10s systemd crash-loop)."* An escaping `notifier.send` skips:
  · `_write_degraded_marker(...)`  — machine-visible, read by the canary/Officer
  · the `EMAIL DELIVERY DEGRADED` log line
  · `_prune_attempts` / `_save_attempts`  — the counter save
  · `return 0` itself
and `run_once` is called UNGUARDED from `run_loop` (:742) and from `main()` (:812,
whose `try` has only a `finally`). So the exception kills the long-lived `--loop`
watcher and exits non-zero — RE-CREATING the very systemd crash-loop the contract
comment names. ⛔ The sweep called this "bounded, observability plane"; that
classification is CORRECTED here: sentinels do persist, but availability of the
alert path itself is at stake.

⚠️ PARITY: ⛔ no paper coverage is claimed. `alert_watcher` is a CRON path — it runs
as its own process, outside the trading session, and reads no mode flag. It is
MODE-INDEPENDENT, which is a different statement from "paper exercises it", and the
distinction is deliberate.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.alert_watcher import SmtpAuthError, _telegram_fallback_batch, run_once
from tests.unit.test_alert_watcher import _make_cfg, _null_log, _write_sentinel


class _RaisingNotifier:
    """The failure the sweep found: the call does not come back."""

    def __init__(self):
        self.calls = 0

    def send(self, **kw):
        self.calls += 1
        raise RuntimeError("telegram 429 / socket timeout")


class _OkNotifier:
    def __init__(self):
        self.calls = 0

    def send(self, **kw):
        self.calls += 1
        return type("R", (), {"success": True, "delivered_to": ["c"],
                              "failed_to": [], "sentinel_path": None,
                              "failed_log_written": False})()


class _RecordingLog:
    def __init__(self):
        self.calls = []

    def _cap(self, lvl, msg, *a, **kw):
        self.calls.append({"level": lvl, "extra": kw.get("extra") or {}})

    def info(self, m, *a, **kw):     self._cap("info", m, *a, **kw)
    def warning(self, m, *a, **kw):  self._cap("warning", m, *a, **kw)
    def error(self, m, *a, **kw):    self._cap("error", m, *a, **kw)
    def critical(self, m, *a, **kw): self._cap("critical", m, *a, **kw)
    def debug(self, m, *a, **kw):    self._cap("debug", m, *a, **kw)

    def outcomes(self):
        return [c["extra"].get("outcome") for c in self.calls
                if "outcome" in (c["extra"] or {})]


class TestPhase2FallbackBatch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    # ── the propagation itself ───────────────────────────────────────────────
    def test_a_raising_notifier_does_NOT_escape_the_batch(self):
        cfg = _make_cfg(self.tmpdir)
        sd = Path(cfg.system.alerts.sentinel_dir)
        sd.mkdir(parents=True, exist_ok=True)
        pending = [_write_sentinel(sd, title="A")]
        log = _RecordingLog()
        delivered, stuck = _telegram_fallback_batch(pending, _RaisingNotifier(), log)
        self.assertEqual((delivered, stuck), (0, 1))
        self.assertIn("failed", log.outcomes(), "invariant 2: it must be recorded")

    def test_one_raising_sentinel_does_not_abort_the_REST_of_the_batch(self):
        """⭐ A second defect the same fix closes: today the first raise aborts
        every remaining sentinel in the pass."""
        cfg = _make_cfg(self.tmpdir)
        sd = Path(cfg.system.alerts.sentinel_dir)
        sd.mkdir(parents=True, exist_ok=True)
        pending = [_write_sentinel(sd, title=f"A{i}") for i in range(3)]
        n = _RaisingNotifier()
        delivered, stuck = _telegram_fallback_batch(pending, n, _RecordingLog())
        self.assertEqual(n.calls, 3, "every sentinel must still be attempted")
        self.assertEqual((delivered, stuck), (0, 3))

    def test_a_working_notifier_still_delivers_and_records(self):
        cfg = _make_cfg(self.tmpdir)
        sd = Path(cfg.system.alerts.sentinel_dir)
        sd.mkdir(parents=True, exist_ok=True)
        pending = [_write_sentinel(sd, title="A")]
        log = _RecordingLog()
        delivered, stuck = _telegram_fallback_batch(pending, _OkNotifier(), log)
        self.assertEqual((delivered, stuck), (1, 0))
        self.assertIn("delivered", log.outcomes())

    def test_no_notifier_is_still_all_stuck_and_never_raises(self):
        cfg = _make_cfg(self.tmpdir)
        sd = Path(cfg.system.alerts.sentinel_dir)
        sd.mkdir(parents=True, exist_ok=True)
        pending = [_write_sentinel(sd, title="A")]
        self.assertEqual(_telegram_fallback_batch(pending, None, _RecordingLog()),
                         (0, 1))


class TestPhase2ExitCodeContract(unittest.TestCase):
    """🔑 THE TEST THAT MUST EXIST: the file's own contract, end to end."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run_with_dead_smtp_and_raising_telegram(self):
        cfg = _make_cfg(self.tmpdir)
        sd = Path(cfg.system.alerts.sentinel_dir)
        sd.mkdir(parents=True, exist_ok=True)
        _write_sentinel(sd, title="A")
        with patch("scripts.alert_watcher._send_email",
                   side_effect=SmtpAuthError("auth failed")), \
             patch("scripts.alert_watcher._telegram_notifier",
                   return_value=_RaisingNotifier()):
            rc = run_once(cfg, log=_null_log())
        return cfg, sd, rc

    def test_exit_code_stays_ZERO_when_the_telegram_fallback_fails(self):
        """⛔ *"a DELIVERY failure is NOT a crash — never return non-zero"*. An
        escaping send both crashes the pass and can re-create the 10s systemd
        crash-loop the comment names."""
        _cfg, _sd, rc = self._run_with_dead_smtp_and_raising_telegram()
        self.assertEqual(rc, 0)

    def test_the_degraded_marker_is_written_even_when_telegram_raises(self):
        """⭐ The marker is the ONE machine-visible thing that would have told
        anyone. Today an escaping send skips it."""
        _cfg, sd, _rc = self._run_with_dead_smtp_and_raising_telegram()
        marker = sd / "alert_watcher_degraded.json"
        self.assertTrue(marker.exists(), "the degraded marker must be published")
        data = json.loads(marker.read_text(encoding="utf-8"))
        self.assertTrue(data.get("degraded"))

    def test_the_attempt_counters_are_still_saved(self):
        _cfg, sd, _rc = self._run_with_dead_smtp_and_raising_telegram()
        self.assertTrue(any(p.name.endswith(".json") for p in sd.iterdir()),
                        "the counter save must still run")

    def test_the_sentinel_survives_so_the_next_pass_retries(self):
        """⭐ The bound that keeps this out of the safety plane: nothing is lost."""
        _cfg, sd, _rc = self._run_with_dead_smtp_and_raising_telegram()
        self.assertTrue(any(p.suffix == ".flag" for p in sd.iterdir()))


class TestPhase2ContractAndPractice(unittest.TestCase):
    def test_no_second_mechanism_was_invented(self):
        from alerts.delivery import ALERT_OUTCOMES, send_alert_recorded
        self.assertEqual(ALERT_OUTCOMES, ("delivered", "failed", "suppressed"))
        import inspect
        from scripts import alert_watcher
        self.assertIn("send_alert_recorded",
                      inspect.getsource(alert_watcher._telegram_fallback_batch))

    def test_the_batch_RUNS_on_a_bare_stub__the_carried_countermeasure(self):
        """§2 — the practice Phase 0 produced and Tick 2 failed to carry. Every
        new suite touching a live path gets ONE bare-stub case, from now on."""
        from alerts.delivery import send_alert_recorded

        class _Stub:                       # no logger methods beyond error
            def error(self, *a, **kw):
                pass

        self.assertFalse(send_alert_recorded(_RaisingNotifier(), _Stub(),
                                             severity="CRITICAL", title="t",
                                             body="b", source_module="unit"))

    def test_alert_watcher_is_a_CRON_path_and_mode_independent(self):
        """⚠️ ⛔ No paper coverage is claimed. This module reads no mode flag —
        it is mode-INDEPENDENT, which is a different statement."""
        import inspect
        from scripts import alert_watcher
        src = inspect.getsource(alert_watcher)
        for token in ("--mode", "is_paper", "paper_mode"):
            self.assertNotIn(token, src,
                             f"{token} would make this mode-dependent")


if __name__ == "__main__":
    unittest.main()
