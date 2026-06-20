"""Tests for broker/auth_recovery.py (Kite IP-403 headless handling)."""
from __future__ import annotations

import pytest

from broker.auth_recovery import build_ip403_alert_body, classify_broker_auth_error
from core.exceptions import BrokerAuthError


@pytest.mark.parametrize("msg,expected", [
    ("Zerodha permission denied: your IP 1.2.3.4 is not allowed to place orders", "IP_NOT_ALLOWLISTED"),
    ("Zerodha permission denied: forbidden", "IP_NOT_ALLOWLISTED"),
    ("IP not allowed to place orders", "IP_NOT_ALLOWLISTED"),
    ("Your IP address is not whitelisted", "IP_NOT_ALLOWLISTED"),
    ("Zerodha token/auth failure: token expired", "TOKEN_EXPIRED"),
    ("Invalid token", "TOKEN_EXPIRED"),
    ("session expired", "TOKEN_EXPIRED"),
    ("some other broker weirdness", "AUTH_UNKNOWN"),
])
def test_classify(msg, expected):
    assert classify_broker_auth_error(msg) == expected


def test_classify_accepts_exception_object():
    exc = BrokerAuthError("Zerodha permission denied: IP not allowed to place orders")
    assert classify_broker_auth_error(exc) == "IP_NOT_ALLOWLISTED"


def test_build_body_has_ip_steps_and_self_recovery():
    body = build_ip403_alert_body("203.0.113.9", "Zerodha permission denied: ...")
    assert "203.0.113.9" in body                     # the IP to paste
    assert "IP allowlist" in body                    # where
    assert "no restart needed" in body.lower()       # self-recovery promise
    assert "permission denied" in body               # raw error echoed
