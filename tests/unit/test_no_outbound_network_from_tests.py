"""tests/unit/test_no_outbound_network_from_tests.py

27-Jul-2026. The suite was sending REAL Telegram messages to the operator's live
channel: two tests in test_interactive_startup.py patch ``is_trading_day`` and
``next_trading_day`` but not ``main._send_holiday_notification``, which does a raw
urllib POST with the production ``TELEGRAM_BOT_TOKEN``. ~50 real "MARKET IS CLOSED"
messages between 6-May and 27-Jul, including trading days while the system traded.

The guard is ``tests/conftest.py::_block_outbound_network``. These tests pin BOTH
directions, because a guard that is wrong in the other direction would silence the
only out-of-band alert path in production -- a far worse defect than the one fixed.
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

# NB: pytest imports conftest.py as the top-level module `conftest`, so
# `from tests.conftest import ...` would load a SECOND copy and the class
# identities would not match. Match on the message instead -- stable either way.
_BLOCKED = "BLOCKED outbound"

_ROOT = Path(__file__).resolve().parents[2]


# ── direction 1: SILENT under test ────────────────────────────────────────────

def test_outbound_connection_is_blocked():
    with pytest.raises(RuntimeError, match=_BLOCKED):
        socket.create_connection(("api.telegram.org", 443), timeout=1)


def test_raw_socket_connect_is_blocked():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match=_BLOCKED):
            s.connect(("8.8.8.8", 53))
    finally:
        s.close()


def test_loopback_is_still_allowed():
    """The guard must not break tests that bind/probe local ports (instance-lock,
    healthcheck, the webhook self-check). connect_ex to a closed local port returns
    an errno rather than raising -- what matters is that the GUARD did not fire."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        rc = s.connect_ex(("127.0.0.1", 59399))   # nothing listening; must not raise
        assert isinstance(rc, int)
    finally:
        s.close()


# ── the real leak, and BOTH directions in ONE assertion ───────────────────────

def test_the_holiday_sender_still_TRIES_to_send_but_cannot_leave_the_machine(monkeypatch):
    """One test, both directions.

    If ``_send_holiday_notification`` reaches the socket layer aimed at Telegram,
    then in PRODUCTION -- where this guard does not exist -- it still sends. That is
    the "loud in production" half, proven by observation rather than by assuming the
    absence of a change.

    And the guard raising is the "silent under test" half: the bytes never leave.

    RED before the fix: the connection succeeded and a real message was delivered.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-for-this-test")
    monkeypatch.setenv("TELEGRAM_CHANNEL_PRIMARY", "fake-chat-id")

    attempts: list = []
    guarded = socket.create_connection          # already wrapped by the conftest guard

    def spy(address, *args, **kwargs):
        attempts.append(address)
        return guarded(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", spy)

    from main import _send_holiday_notification
    _send_holiday_notification("MARKET IS CLOSED (test)")   # swallows all exceptions

    assert attempts, (
        "the sender never reached the socket layer -- if this is a no-op under test "
        "it may be a no-op in PRODUCTION too, which would be the worse defect")
    assert any("telegram" in str(a[0]).lower() for a in attempts), (
        f"expected a Telegram target, got {attempts!r}")


# ── direction 2: production is untouched, structurally ────────────────────────

def test_the_guard_lives_only_in_the_test_harness():
    """PRODUCTION CANNOT BE SILENCED BY THIS, and the proof is that production never
    references it. A mode flag inside the sender would have had to be tested; a guard
    that production cannot import cannot misfire there."""
    hits = []
    for p in _ROOT.rglob("*.py"):
        rel = p.relative_to(_ROOT).as_posix()
        if rel.startswith(("tests/", "venv/", "sats/", "ops_dashboard/.venv/")):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "_block_outbound_network" in txt or "OutboundNetworkBlocked" in txt:
            hits.append(rel)
    assert not hits, f"the test-only guard leaked into production code: {hits}"


def test_the_two_tests_that_caused_the_leak_are_still_the_shape_that_needs_the_guard():
    """Pins WHY the guard is needed: these tests patch the decision but not the
    sender. If someone later patches the sender too, the guard is still correct --
    but this test documents the actual mechanism at the actual site, so the next
    reader does not have to rediscover it from a Telegram screenshot."""
    src = (_ROOT / "tests/unit/test_interactive_startup.py").read_text(encoding="utf-8")
    assert 'patch.object(_main_module, "is_trading_day", return_value=False)' in src
    assert "_send_holiday_notification" not in src, (
        "if this test now patches the sender, update this docstring -- the guard "
        "remains the load-bearing protection for every OTHER unpatched sender")
