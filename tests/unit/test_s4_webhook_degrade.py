"""tests/unit/test_s4_webhook_degrade.py — the S4 follow-up, answered 27-Jul-2026.

S4 (17-Jul) cost a whole trading day: /health went behind the webhook secret, the
post-start self-check called it anonymously, got a 401, and `_shutdown_event.set()`
took the entire system down behind a clean exit 0. The 401 was fixed the same day.
The DECISION it exposed -- should one non-2xx from one local endpoint stop
everything? -- was left open as "Rama's call" and is answered here: DEGRADE.

The argument is safety, not convenience. No Flask means no signals, so entries stop
either way; halting ALSO stops exit management, the reconciler and the 15:17 EOD
squareoff. It turns "no new entries, protection still running" into "nothing running
at all".

Two properties, and the second is the one that makes the first legitimate:

  D1  the unreachable branch must NOT shut the system down
  D2  it MUST alarm out-of-band -- because liveness_probe cannot cover this case.
      liveness_probe detects a service that is DOWN; after this change the service
      is UP. Removing the block without adding an alarm would be removing a signal
      and adding nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _selfcheck_block() -> str:
    """The webhook self-check branch from main.py, CODE LINES ONLY.

    Comments are stripped deliberately: this file's own explanation names both
    `_shutdown_event.set()` and `liveness_probe`, so a scan that included prose
    would assert against its own commentary. (Measured on the #16a boot-gate test,
    where a comment-inclusive window let a disabled guard pass.)
    """
    src = Path("main.py").read_text(encoding="utf-8")
    # Anchor on the BRANCH, not on the log line inside it. Anchoring on the log line
    # left everything above it outside the window, so restoring the halt on the line
    # before was invisible and the test stayed GREEN. Measured.
    i = src.find("if not wh_result.reachable:")
    assert i > 0, "the webhook self-check branch is gone from main.py"
    window = src[i:i + 3500].splitlines()
    out = []
    for ln in window:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        out.append(ln)
        if "E-4 (audit 02-Jul)" in ln or "_daemon_liveness" in ln:
            break
    return "\n".join(out)


def test_d1_unreachable_webhook_does_not_shut_the_system_down():
    """RED on the pre-27-Jul code, which called _shutdown_event.set() here."""
    block = _selfcheck_block()
    assert "_shutdown_event.set()" not in block, (
        "the webhook self-check still halts the boot -- that also stops exit "
        "management, the reconciler and the 15:17 EOD squareoff")


def test_d2_the_degrade_alarms_out_of_band():
    """A degrade without an alarm is a silent failure.

    liveness_probe watches for a service that is DOWN, and this path leaves it UP,
    so the sentinel -> alert_watcher -> email chain is the ONLY thing that can reach
    a human here. A bare _log.critical() would not.
    """
    block = _selfcheck_block()
    assert "write_critical_sentinel" in block, (
        "the degraded branch does not raise an out-of-band CRITICAL -- liveness_probe "
        "cannot see this case, so nothing would reach anyone")


def test_d2_the_alarm_mechanism_actually_produces_a_watcher_readable_sentinel(tmp_path):
    """Verify the ALARM EXISTS, not just that the call is written down.

    Calls the real writer with the shape the degraded branch uses and asserts a
    .flag lands where alert_watcher looks. (conftest's _isolate_real_sentinels keeps
    this out of the live data_store.)
    """
    from alerts.critical import write_critical_sentinel

    p = write_critical_sentinel(
        title="Webhook endpoint unreachable at boot -- DEGRADED, still running",
        body="self-check could not reach http://127.0.0.1:5000/health",
        source_module="main.webhook_selfcheck",
        context={"url": "http://127.0.0.1:5000/health", "degraded": True, "shutdown": False},
        sentinel_dir=tmp_path,
    )
    assert p.exists(), "no sentinel written"
    assert p.suffix == ".flag", f"alert_watcher only reads .flag, got {p.suffix}"
    assert p.parent == tmp_path
    assert not list(tmp_path.glob("*.tmp")), "a .tmp was left behind (atomic rename failed)"
    text = p.read_text(encoding="utf-8")
    assert "DEGRADED" in text and "main.webhook_selfcheck" in text


def test_the_two_answers_are_deliberately_opposite():
    """The #16a config BLOCK and this DEGRADE come from ONE rule, applied to
    different preconditions: fail-fast when only a deliberate act can cause the
    failure, degrade-and-alarm when the environment can.

    Pinned together so a future edit that "makes them consistent" has to read why
    they differ first. RED if either side flips.
    """
    src = Path("main.py").read_text(encoding="utf-8")
    # the delivery foot-gun BLOCKS the boot (deliberate act: someone set a flag)
    i = src.find("slice25_delivery_capital_footgun")
    assert i > 0, "the #16a boot gate is gone"
    assert "return 3" in src[i:i + 400], "#16a must still stop the boot"
    # the webhook self-check does NOT (environmental: an endpoint did not answer)
    assert "_shutdown_event.set()" not in _selfcheck_block(), \
        "the webhook self-check must still degrade"
