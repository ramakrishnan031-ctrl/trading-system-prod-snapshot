"""
broker/auth_recovery.py -- Trading System v2

Helpers for headless handling of Kite IP-allowlist (403) failures.

Context: Zerodha's API allowlists a STATIC IP for ORDER PLACEMENT (SEBI rule).
When the VM's public IP changes and isn't updated in the Kite developer console,
`place_order` returns a 403 PermissionException → BrokerAuthError. Reads
(positions/margins/quotes/history) are NOT IP-gated, so the system does not halt:
the entry is rejected (`signal_processor` SP12), `record_api_failure` ignores the
BrokerAuthError (FIX-185), and the NEXT signal retries — i.e. trading
**self-recovers automatically once the IP is allowlisted; no restart needed.**

The only thing missing is telling the operator WHAT to do. This module provides:
  * classify_broker_auth_error() — IP-allowlist vs token-expiry vs unknown
  * get_public_ip()              — the VM's current public IP (to paste into Kite)
  * build_ip403_alert_body()     — an actionable CRITICAL alert body

Pure / dependency-light (stdlib only) so it can be imported from capital/ without
breaking the layer rules.
"""
from __future__ import annotations


# IP-allowlist / permission markers. Zerodha's PermissionException is translated
# to "Zerodha permission denied: <msg>"; the IP message reads like "... your IP
# ... is not allowed to place orders".
_IP_MARKERS = (
    "permission denied",
    "not allowed to place",
    "ip not allowed",
    "not whitelisted",
    "not allowlisted",
    "ip address",
    "allowlist",
    "whitelist",
)

# Token/session-expiry markers (Zerodha TokenException -> "token/auth failure").
_TOKEN_MARKERS = (
    "token/auth failure",
    "token expired",
    "invalid token",
    "session expired",
    "access_token",
    "api_key",
)


def classify_broker_auth_error(error: object) -> str:
    """Classify a BrokerAuthError (or its message) so callers can react correctly.

    Returns one of:
      "IP_NOT_ALLOWLISTED" — order placement blocked by the Kite IP allowlist
                             (token is VALID; do NOT invalidate it — fix the IP).
      "TOKEN_EXPIRED"      — token/session is no longer valid (refresh it).
      "AUTH_UNKNOWN"       — some other auth failure.
    """
    text = str(error).lower()
    if any(m in text for m in _IP_MARKERS):
        return "IP_NOT_ALLOWLISTED"
    if any(m in text for m in _TOKEN_MARKERS) or "token" in text:
        return "TOKEN_EXPIRED"
    return "AUTH_UNKNOWN"


def get_public_ip(timeout: float = 5.0) -> str:
    """Best-effort current public IP of the VM (to paste into the Kite allowlist).
    Tries a couple of providers; never raises."""
    import urllib.request
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
                ip = resp.read().decode("utf-8", "replace").strip()
            if ip:
                return ip
        except Exception:
            continue
    return "unknown — check the VM's public IP manually"


def build_ip403_alert_body(public_ip: str, raw_error: str = "") -> str:
    """Actionable CRITICAL alert body: the exact IP to allowlist + the steps."""
    lines = [
        "Broker REJECTED order placement: this VM's IP is not in Zerodha's API "
        "allowlist (SEBI-mandated for order placement).",
        "",
        "ACTION REQUIRED:",
        f"1. Current VM public IP:  {public_ip}",
        "2. Zerodha: My Profile > API (developer console) > IP allowlist",
        f"3. Add / update the allowlisted IP to:  {public_ip}",
        "4. New entries resume AUTOMATICALLY on the next signal once allowlisted "
        "— no restart needed.",
    ]
    if raw_error:
        lines += ["", f"(broker said: {raw_error[:160]})"]
    lines += ["", "(This usually happens after the VM's public IP changes.)"]
    return "\n".join(lines)
