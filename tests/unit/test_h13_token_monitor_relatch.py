"""
tests/unit/test_h13_token_monitor_relatch.py

Wave-3 / H-13 — TokenMonitor expiry latch must RESET on a valid check, and only a
GENUINE token-invalid signal may fire the expiry sequence.

Root cause (pre-fix): check_now() treated ANY exception from profile_fn() as an
expiry, and _handle_expiry() set self._expiry_fired = True but nothing ever cleared
it. So (a) a transient network blip fired a false CRITICAL "TOKEN EXPIRED" +
SOFT_KILL and LATCHED, and (b) once latched, a later GENUINE expiry no-op'd — no
alert, no soft-kill — leaving the system attempting entries on a dead token until a
restart. The safety net reported healthy while doing nothing.

Fix: reset _expiry_fired on a successful check (re-arm), and classify the exception
(type == kiteconnect TokenException OR auth_recovery.classify_broker_auth_error ==
"TOKEN_EXPIRED") so a transient error neither fires nor latches.

Real collaborators:  TokenMonitor.check_now / _is_token_expiry / _handle_expiry /
                     the _expiry_fired latch, broker.auth_recovery.classify_broker_auth_error.
Simulated:           profile_fn (the Kite API boundary) — a sequenced fake raising
                     REAL kiteconnect exceptions; notifier = MagicMock (records
                     alert calls); on_expiry = list-append spy.

RED/GREEN: test_h13_relatch_after_recovery and test_h13_transient_does_not_disarm
FAIL against the unfixed monitor (latch never resets / a blip latches) and PASS
after the fix. test_h13_real_expiry_alerts_once passes both (once-per-episode intact).

Run: python -m pytest tests/unit/test_h13_token_monitor_relatch.py -v
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from kiteconnect.exceptions import TokenException, NetworkException, DataException

from broker.token_monitor import TokenMonitor
from broker.auth_recovery import classify_broker_auth_error


def _make_monitor(profile_fn, on_expiry=None, notifier=None) -> TokenMonitor:
    return TokenMonitor(
        profile_fn=profile_fn,
        on_expiry=on_expiry or (lambda: None),
        logger=logging.getLogger("test_h13"),
        check_interval_sec=1800,
        paper_mode=False,
        notifier=notifier,
        mode="TEST",
    )


def _seq_profile(*steps):
    """Return a profile_fn that, on each call, raises the next Exception in
    `steps` (or returns a valid profile dict for a None step / once exhausted)."""
    it = iter(steps)

    def _fn():
        try:
            step = next(it)
        except StopIteration:
            step = None
        if step is not None:
            raise step
        return {"user_id": "TEST"}

    return _fn


# ── classifier unit (the signal the fix keys on) ─────────────────────────────

def test_h13_is_token_expiry_classification() -> None:
    mon = _make_monitor(_seq_profile())
    assert mon._is_token_expiry(TokenException("Invalid `access_token`.")) is True
    assert mon._is_token_expiry(Exception("token expired")) is True
    assert mon._is_token_expiry(NetworkException("Connection timed out")) is False
    assert mon._is_token_expiry(DataException("Something went wrong")) is False
    # IP-allowlist means the token is VALID -> not an expiry.
    assert classify_broker_auth_error(
        Exception("Your IP is not allowed to place orders")
    ) == "IP_NOT_ALLOWLISTED"
    assert mon._is_token_expiry(
        Exception("Your IP is not allowed to place orders")
    ) is False
    print("  OK H-13 classify: TokenException/token-msg -> expiry; "
          "network/5xx/IP -> not expiry")


# ── Test 1 — real expiry alerts ONCE per episode ─────────────────────────────

def test_h13_real_expiry_alerts_once() -> None:
    expiry, notifier = [], MagicMock()
    mon = _make_monitor(
        _seq_profile(TokenException("Invalid `access_token`."),
                     TokenException("Invalid `access_token`.")),
        on_expiry=lambda: expiry.append(1),
        notifier=notifier,
    )
    assert mon.check_now() is False
    assert len(expiry) == 1                       # fired
    assert notifier.send.call_count == 1
    assert mon._expiry_fired is True
    # second consecutive invalid within the SAME episode -> no re-alert
    assert mon.check_now() is False
    assert len(expiry) == 1                        # still once
    assert notifier.send.call_count == 1
    print("  OK H-13 T1: real expiry alerts once; repeat within episode suppressed")


# ── Test 2 — re-arm after recovery (core bug; RED before fix) ─────────────────

def test_h13_relatch_after_recovery() -> None:
    expiry = []
    mon = _make_monitor(
        _seq_profile(
            TokenException("Invalid `access_token`."),   # expiry #1
            None,                                         # token valid -> reset
            TokenException("Invalid `access_token`."),   # expiry #2
        ),
        on_expiry=lambda: expiry.append(1),
    )
    mon.check_now()                                # fires #1
    assert len(expiry) == 1
    assert mon._expiry_fired is True
    mon.check_now()                                # valid -> latch resets
    assert mon._expiry_fired is False              # RED before fix (never reset)
    mon.check_now()                                # fires #2
    assert len(expiry) == 2                        # RED before fix (would be 1)
    print("  OK H-13 T2: latch re-arms after recovery -> second expiry alerts")


# ── Test 3 — a transient blip does NOT disarm real detection (RED before fix) ─

def test_h13_transient_does_not_disarm() -> None:
    expiry, notifier = [], MagicMock()
    mon = _make_monitor(
        _seq_profile(
            NetworkException("Connection timed out"),    # transient
            TokenException("Invalid `access_token`."),   # real expiry AFTER blip
        ),
        on_expiry=lambda: expiry.append(1),
        notifier=notifier,
    )
    # transient: no false expiry, no latch, no soft_kill, no CRITICAL alert
    assert mon.check_now() is False
    assert len(expiry) == 0
    assert mon._expiry_fired is False
    assert notifier.send.call_count == 0
    # real expiry still detected (RED before fix: the blip latched -> this no-ops)
    assert mon.check_now() is False
    assert len(expiry) == 1
    assert notifier.send.call_count == 1
    print("  OK H-13 T3: transient blip does not latch; later real expiry alerts")


if __name__ == "__main__":
    test_h13_is_token_expiry_classification()
    test_h13_real_expiry_alerts_once()
    test_h13_relatch_after_recovery()
    test_h13_transient_does_not_disarm()
    print("\nAll H-13 tests passed.")
