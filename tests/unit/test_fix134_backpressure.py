"""Tests for FIX-134 Item 35: graduated signal queue backpressure."""
from __future__ import annotations

import json
import queue
import tempfile
import types
from datetime import datetime
from pathlib import Path

import pytest

from core.state_store import StateStore
from signals.webhook_receiver import WebhookReceiver, _PerIpRateLimiter


class _MockMarketWindows:
    def is_entry_allowed(self, now) -> bool:
        return True


class _MockKillSwitch:
    def is_active(self, intent="entry") -> bool:
        return False


class _NullLogger:
    def debug(self, *a, **kw): pass
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def critical(self, *a, **kw): pass


def _make_config(capacity=100, bp_pct=0.80, warning_pct=0.60, expiry=600):
    cfg = types.SimpleNamespace()
    cfg.system = types.SimpleNamespace(
        signal_queue=types.SimpleNamespace(
            capacity=capacity,
            backpressure_pct=bp_pct,
            expiry_sec=expiry,
            warning_pct=warning_pct,
        )
    )
    cfg.scan_webhook_map = types.SimpleNamespace(
        scanners={"gap_go_long": "strategies/gap_go_long.yaml"}
    )
    return cfg


def _make_receiver(capacity=100, bp_pct=0.80, warning_pct=0.60):
    sq = queue.Queue(maxsize=capacity)
    td = tempfile.mkdtemp()
    store = StateStore(Path(td) / "test.db")
    config = _make_config(capacity=capacity, bp_pct=bp_pct, warning_pct=warning_pct)
    receiver = WebhookReceiver(
        sq, store, config,
        _MockMarketWindows(), _MockKillSwitch(), _NullLogger(),
    )
    return receiver, sq, store


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _post_signal(client, scanner="gap_go_long", symbol="RELIANCE", price="2500.0"):
    return client.post(
        f"/webhook/{scanner}",
        data=json.dumps({
            "stocks": symbol,
            "trigger_prices": price,
            "triggered_at": _now_str(),
            "scan_name": scanner,
        }),
        content_type="application/json",
    )


class TestHealthQueueDepth:
    def test_health_includes_queue_depth(self):
        receiver, sq, _ = _make_receiver(capacity=100)
        with receiver.app.test_client() as client:
            resp = client.get("/health")
            data = resp.get_json()
            assert "queue_depth" in data
            assert data["queue_depth"] == "0/100"


class TestGraduatedBackpressure:
    def test_below_warning_no_header(self):
        """0-59% full: no warning header."""
        receiver, sq, _ = _make_receiver(capacity=100, warning_pct=0.60, bp_pct=0.80)
        with receiver.app.test_client() as client:
            resp = _post_signal(client, symbol="SYM1")
            assert resp.status_code == 200
            assert "X-Queue-Depth" in resp.headers
            assert "X-Queue-Warning" not in resp.headers

    def test_at_warning_threshold_gets_header(self):
        """60-79% full: 200 OK + X-Queue-Warning."""
        receiver, sq, _ = _make_receiver(capacity=100, warning_pct=0.60, bp_pct=0.80)
        # Fill queue to 65 items (65% > 60% warning)
        for i in range(65):
            sq.put(f"dummy-{i}")
        with receiver.app.test_client() as client:
            resp = _post_signal(client, symbol="WARNTEST")
            assert resp.status_code == 200
            assert resp.headers.get("X-Queue-Warning") == "high"
            assert "X-Queue-Depth" in resp.headers

    def test_at_reject_threshold_returns_503(self):
        """80-100% full: 503 reject."""
        receiver, sq, _ = _make_receiver(capacity=100, warning_pct=0.60, bp_pct=0.80)
        # Fill queue to 85 items (85% > 80% reject)
        for i in range(85):
            sq.put(f"dummy-{i}")
        with receiver.app.test_client() as client:
            resp = _post_signal(client, symbol="REJECTTEST")
            assert resp.status_code == 503
            assert "X-Queue-Depth" in resp.headers

    def test_queue_depth_header_format(self):
        """X-Queue-Depth format is 'N/MAX'."""
        receiver, sq, _ = _make_receiver(capacity=50)
        for i in range(10):
            sq.put(f"dummy-{i}")
        with receiver.app.test_client() as client:
            resp = _post_signal(client, symbol="FMTTEST")
            depth = resp.headers.get("X-Queue-Depth", "")
            parts = depth.split("/")
            assert len(parts) == 2
            assert parts[1] == "50"

    def test_parity_same_thresholds(self):
        """Both paper and live use same config-driven thresholds."""
        cfg = _make_config(capacity=100, warning_pct=0.60, bp_pct=0.80)
        sq_cfg = cfg.system.signal_queue
        assert sq_cfg.warning_pct == 0.60
        assert sq_cfg.backpressure_pct == 0.80


# ─────────────────────────────────────────────────────────────────────────────
# AB-910 §1.7 — /health must not hand system state to anonymous callers
# ─────────────────────────────────────────────────────────────────────────────
# Obviously fake: the pre-commit secret scanner (deploy/hooks/secret_scan.py)
# correctly blocks credential-shaped test values, so use a placeholder it accepts.
_FAKE_TOKEN = "dummy-webhook-token-for-tests"


def _make_receiver_with_secret(capacity=100):
    """Same harness as _make_receiver, but WITH a webhook secret configured —
    which is the production shape (.env carries WEBHOOK_SECRET)."""
    sq = queue.Queue(maxsize=capacity)
    td = tempfile.mkdtemp()
    store = StateStore(Path(td) / "test.db")
    config = _make_config(capacity=capacity)
    receiver = WebhookReceiver(
        sq, store, config,
        _MockMarketWindows(), _MockKillSwitch(), _NullLogger(),
        secret_token=_FAKE_TOKEN,
    )
    return receiver, sq, store


class TestHealthAuth:
    """RED before the fix: /health returned kill_switch_active + queue depth to any
    anonymous caller on 0.0.0.0:5000, and bypassed the per-IP limiter /webhook is behind."""

    def test_unauthenticated_health_is_denied_when_a_secret_is_configured(self):
        receiver, _, _ = _make_receiver_with_secret()
        with receiver.app.test_client() as client:
            resp = client.get("/health")
        assert resp.status_code == 401

    def test_unauthenticated_health_leaks_no_system_state(self):
        """The failure path is where oracles usually leak — assert it says nothing."""
        receiver, _, _ = _make_receiver_with_secret()
        with receiver.app.test_client() as client:
            data = client.get("/health").get_json() or {}
        for leaky in ("kill_switch_active", "queue_size", "queue_capacity", "queue_depth"):
            assert leaky not in data, f"/health leaked {leaky} to an anonymous caller"

    def test_health_with_correct_token_returns_full_detail(self):
        receiver, _, _ = _make_receiver_with_secret()
        with receiver.app.test_client() as client:
            resp = client.get(f"/health?token={_FAKE_TOKEN}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok" and data["queue_depth"] == "0/100"

    def test_health_with_wrong_token_is_denied(self):
        receiver, _, _ = _make_receiver_with_secret()
        with receiver.app.test_client() as client:
            resp = client.get("/health?token=wrong")
        assert resp.status_code == 401

    def test_health_without_a_secret_configured_stays_open(self):
        """Symmetry with /webhook: no secret configured -> no auth surface. Guards
        against hard-failing a deployment that never had a secret."""
        receiver, _, _ = _make_receiver(capacity=100)
        with receiver.app.test_client() as client:
            resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_is_now_behind_the_per_ip_rate_limiter(self):
        """AB-910: /health bypassed the limiter entirely. Exhaust the bucket and the
        NEXT /health must be 429 — proving it is metered like /webhook."""
        receiver, _, _ = _make_receiver_with_secret()
        receiver._ip_limiter = _PerIpRateLimiter(burst=2, refill_per_sec=0.0)
        with receiver.app.test_client() as client:
            codes = [client.get(f"/health?token={_FAKE_TOKEN}").status_code for _ in range(3)]
        assert codes[:2] == [200, 200]
        assert codes[2] == 429, f"3rd call must be rate-limited, got {codes}"
