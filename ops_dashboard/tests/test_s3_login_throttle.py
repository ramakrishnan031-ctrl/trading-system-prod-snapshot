"""ops_dashboard/tests/test_s3_login_throttle.py — S3: throttle, never lock out.

S3 (AB-910 §1.2): the dashboard locked an ACCOUNT for `lockout_minutes` after
`max_failures`, keyed by the SUBMITTED username. Anyone who knew Rama's username
could send 5 bad passwords and lock HIM out of his own trading dashboard for 15
minutes — repeatably, i.e. indefinitely. The control meant to stop an attacker
handed one a reliable denial-of-service against the only operator.

The audit proposed keying the lockout per-IP. That is strictly WORSE here: the
GUI is Waitress on 127.0.0.1:8500 behind the tailscaled proxy, so `remote_addr`
is ALWAYS 127.0.0.1 — per-IP collapses to ONE shared bucket and ANY failure would
lock out everyone, Rama included. X-Forwarded-For is caller-settable and so is
not a sound basis for the decision either. Hence: throttle, keyed on username,
gating nothing.

*** THE PROPERTY UNDER TEST: correct credentials ALWAYS authenticate. ***
No number of failures can refuse a valid login, so the DoS is structurally gone.
Brute force is slowed by delaying FAILED responses (exponential, capped) — the
only mechanism that slows guessing without standing between the operator and a
correct password (telling right from wrong REQUIRES verifying, so any
check-free refusal would refuse Rama too — that is just a lockout again).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.auth import (  # noqa: E402
    LoginAttemptTracker, authenticate, hash_password,
)

_USER = "rama"
_GOOD_PW = "correct horse battery staple"
_BAD_PW = "wrong"


@pytest.fixture
def auth_cfg():
    return {
        "username": _USER,
        "password_hash": hash_password(_GOOD_PW, iterations=1_000),
        "totp_secret": "",
        "totp_disabled": True,      # TOTP is exercised by its own AB-910 §1.3 tests
    }


@pytest.fixture
def tracker():
    return LoginAttemptTracker(max_failures=5, lockout_seconds=900,
                               throttle_base_seconds=1.0, throttle_max_seconds=8.0)


class _Sleeps:
    """Captures the applied delays instead of actually sleeping."""

    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _fail(auth_cfg, tracker, sleeps, n=1, user=_USER):
    for _ in range(n):
        ok, _err = authenticate(auth_cfg, tracker, user, _BAD_PW, "",
                                sleep_fn=sleeps)
        assert ok is False


# ─────────────────────────────────────────────────────────────────────────────
# THE property — an attacker can never deny Rama access
# ─────────────────────────────────────────────────────────────────────────────
def test_correct_password_still_works_after_a_long_failure_flood(auth_cfg, tracker):
    """*** THE S3 property. *** 50 failed attempts on Rama's username — the exact
    DoS the old lockout enabled — then the correct password MUST still work.

    RED-on-old: the old tracker locked the account after 5 failures and
    authenticate() returned "Locked out. Try again in Ns." for 15 minutes.
    """
    sleeps = _Sleeps()
    _fail(auth_cfg, tracker, sleeps, n=50)

    ok, err = authenticate(auth_cfg, tracker, _USER, _GOOD_PW, "", sleep_fn=sleeps)
    assert ok is True, f"the legitimate operator was denied access: {err}"
    assert err is None


def test_a_successful_login_is_never_delayed(auth_cfg, tracker):
    """The throttle must not even slow the operator down — only failures wait."""
    sleeps = _Sleeps()
    _fail(auth_cfg, tracker, sleeps, n=20)
    before = len(sleeps.calls)

    ok, _ = authenticate(auth_cfg, tracker, _USER, _GOOD_PW, "", sleep_fn=sleeps)
    assert ok is True
    assert len(sleeps.calls) == before, "a correct login must never sleep"


def test_success_resets_the_streak(auth_cfg, tracker):
    """After a correct login the next typo starts from zero — no lingering penalty."""
    sleeps = _Sleeps()
    _fail(auth_cfg, tracker, sleeps, n=10)
    assert authenticate(auth_cfg, tracker, _USER, _GOOD_PW, "", sleep_fn=sleeps)[0]
    assert tracker.failure_count(_USER) == 0
    assert tracker.throttle_delay(_USER) == 0.0


def test_there_is_no_lockout_state_at_all(auth_cfg, tracker):
    """The lockout is structurally gone, not merely lengthened."""
    sleeps = _Sleeps()
    _fail(auth_cfg, tracker, sleeps, n=100)
    assert tracker.is_locked(_USER) is False
    assert tracker.seconds_remaining(_USER) == 0


def test_a_wrong_username_flood_cannot_touch_the_real_account(auth_cfg, tracker):
    """A flood against 'admin' must leave Rama's account completely unpenalised."""
    sleeps = _Sleeps()
    _fail(auth_cfg, tracker, sleeps, n=50, user="admin")

    assert tracker.failure_count(_USER) == 0
    assert tracker.throttle_delay(_USER) == 0.0
    ok, _ = authenticate(auth_cfg, tracker, _USER, _GOOD_PW, "", sleep_fn=sleeps)
    assert ok is True


# ─────────────────────────────────────────────────────────────────────────────
# Brute force IS still slowed
# ─────────────────────────────────────────────────────────────────────────────
def test_typos_are_free_then_the_delay_grows_exponentially_and_caps(auth_cfg, tracker):
    """A few honest typos cost nothing; sustained guessing backs off, capped.

    max_failures=5 -> failures 1-4 free; 5th earns 1s, then 2s, 4s, 8s, capped.
    The cap is deliberate: each delayed failure holds a Waitress worker thread,
    so an unbounded delay would trade the old DoS for a new one.
    """
    sleeps = _Sleeps()
    _fail(auth_cfg, tracker, sleeps, n=4)
    assert sleeps.calls == [], "the first few typos must not be throttled"

    _fail(auth_cfg, tracker, sleeps, n=6)
    assert sleeps.calls == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0], sleeps.calls
    assert max(sleeps.calls) <= tracker.throttle_max_seconds


def test_delay_is_capped_however_long_the_flood_runs(auth_cfg, tracker):
    sleeps = _Sleeps()
    _fail(auth_cfg, tracker, sleeps, n=200)
    assert max(sleeps.calls) == 8.0


def test_a_quiet_period_decays_the_streak(auth_cfg, tracker):
    """Yesterday's typos must not throttle today's login."""
    sleeps = _Sleeps()
    for _ in range(10):
        authenticate(auth_cfg, tracker, _USER, _BAD_PW, "", now=1_000.0, sleep_fn=sleeps)
    assert tracker.throttle_delay(_USER, now=1_000.0) > 0

    later = 1_000.0 + tracker.lockout_seconds + 1
    assert tracker.failure_count(_USER, now=later) == 0
    assert tracker.throttle_delay(_USER, now=later) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Addendum §2 — every throttle activation is auditable
# ─────────────────────────────────────────────────────────────────────────────
def test_every_throttle_activation_is_logged_with_count_and_delay(auth_cfg, tracker, caplog):
    """Repeated-failure activity must be auditable, not silent."""
    import logging

    sleeps = _Sleeps()
    _fail(auth_cfg, tracker, sleeps, n=4)          # below the threshold
    with caplog.at_level(logging.WARNING, logger="ops_dashboard.auth"):
        _fail(auth_cfg, tracker, sleeps, n=2)      # 5th and 6th -> throttled

    recs = [r for r in caplog.records if "login_throttled" in r.getMessage()]
    assert len(recs) == 2, "each throttle activation must emit exactly one WARNING"
    msg = recs[0].getMessage()
    assert "consecutive_failures=5" in msg
    assert "applied_delay=1.0s" in msg
    assert "remote_addr=" in msg and "xff=" in msg   # source, logged not trusted


def test_unthrottled_failures_are_not_logged_as_throttle_events(auth_cfg, tracker, caplog):
    """Below the threshold nothing is throttled, so nothing is logged — the audit
    line means "backoff engaged", not merely "a login failed"."""
    import logging

    sleeps = _Sleeps()
    with caplog.at_level(logging.WARNING, logger="ops_dashboard.auth"):
        _fail(auth_cfg, tracker, sleeps, n=4)
    assert [r for r in caplog.records if "login_throttled" in r.getMessage()] == []
