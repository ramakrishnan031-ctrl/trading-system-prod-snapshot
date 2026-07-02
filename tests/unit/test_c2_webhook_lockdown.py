"""C-2 webhook lockdown tests.

Covers the two Phase-2 hardening changes:
  1. per-source-IP token-bucket rate limiter (_PerIpRateLimiter) — burst-tolerant,
     denies a flood, refills over time, independent per IP.
  2. paper-parity: WEBHOOK_SECRET is required in BOTH modes
     (main.required_startup_secrets always includes it).
Plus the WebhookConfig per-IP field defaults + validators.

Fail-on-old / pass-on-fix: the flood-denial + always-required-secret assertions only
hold with the C-2 changes (old code had no per-IP limit and exempted paper from
WEBHOOK_SECRET).
"""
from __future__ import annotations

import pytest

from signals.webhook_receiver import _PerIpRateLimiter
from core.config_loader import WebhookConfig


# ── per-IP token-bucket rate limiter (time injected → deterministic) ──────────
def test_ip_limiter_allows_up_to_burst_then_denies():
    rl = _PerIpRateLimiter(burst=5, refill_per_sec=0.0)   # no refill → hard cap
    ip = "1.2.3.4"
    allowed = sum(1 for _ in range(5) if rl.allow(ip, now=100.0))
    assert allowed == 5                          # all 5 burst tokens spend
    assert rl.allow(ip, now=100.0) is False      # 6th in the same instant → denied


def test_ip_limiter_refills_over_time():
    rl = _PerIpRateLimiter(burst=2, refill_per_sec=1.0)
    ip = "9.9.9.9"
    assert rl.allow(ip, now=0.0) is True
    assert rl.allow(ip, now=0.0) is True
    assert rl.allow(ip, now=0.0) is False        # burst exhausted
    assert rl.allow(ip, now=1.0) is True         # +1s → 1 token refilled
    assert rl.allow(ip, now=1.0) is False


def test_ip_limiter_is_per_ip_independent():
    rl = _PerIpRateLimiter(burst=1, refill_per_sec=0.0)
    assert rl.allow("a", now=0.0) is True
    assert rl.allow("a", now=0.0) is False
    assert rl.allow("b", now=0.0) is True        # a different IP is unaffected


def test_ip_limiter_tolerates_chartink_openbell_burst():
    # production default (burst 60) must absorb a ~40-signal open-bell burst
    rl = _PerIpRateLimiter(burst=60, refill_per_sec=5.0)
    assert all(rl.allow("chartink", now=0.0) for _ in range(40))


def test_ip_limiter_evicts_idle_buckets():
    rl = _PerIpRateLimiter(burst=1, refill_per_sec=1.0, max_ips=2)
    rl.allow("a", now=0.0)
    rl.allow("b", now=0.0)
    # a third IP well after the others are idle+refilled → triggers eviction, still allowed
    assert rl.allow("c", now=1000.0) is True
    assert len(rl._buckets) <= 2


# ── WebhookConfig per-IP fields ──────────────────────────────────────────────
def _mk_webhook(**over):
    base = dict(bind_host="127.0.0.1", bind_port=5000, require_hmac=False)
    base.update(over)
    return WebhookConfig(**base)


def test_webhookconfig_per_ip_defaults():
    wc = _mk_webhook()
    assert wc.per_ip_rate_limit_enabled is True
    assert wc.per_ip_burst == 60
    assert wc.per_ip_refill_per_sec == 5.0


def test_webhookconfig_rejects_bad_per_ip_values():
    with pytest.raises(Exception):
        _mk_webhook(per_ip_burst=0)
    with pytest.raises(Exception):
        _mk_webhook(per_ip_refill_per_sec=-1.0)


# ── paper-parity: WEBHOOK_SECRET required in BOTH modes ──────────────────────
def test_required_startup_secrets_always_includes_webhook_secret():
    from main import required_startup_secrets
    secrets = required_startup_secrets("LFL836")
    assert "WEBHOOK_SECRET" in secrets            # pass-on-fix (paper used to omit it)
    assert "ZERODHA_API_KEY_LFL836" in secrets
    assert "ZERODHA_API_SECRET_LFL836" in secrets
    assert "TELEGRAM_BOT_TOKEN" in secrets
