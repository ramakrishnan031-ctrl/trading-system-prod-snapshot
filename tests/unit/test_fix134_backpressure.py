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
    anonymous caller on 0.0.0.0:5000, and bypassed the per-IP limiter /webhook is behind.

    SCOPE: every test here runs require_hmac=False (the _make_config used by
    _make_receiver_with_secret omits the `webhook` block, so require_hmac resolves
    False). They cover the secret-vs-no-secret and token/HMAC/anon paths, NOT the
    require_hmac=True posture — that is owned by TestHealthRequireHmac below (P1,
    17-Jul). A green TestHealthAuth says nothing about whether /health honours
    require_hmac; don't mistake it for full /health auth coverage."""

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


# ─────────────────────────────────────────────────────────────────────────────
# P1 (17-Jul-2026) — /health honours require_hmac, exactly as /webhook does.
# ─────────────────────────────────────────────────────────────────────────────
# Every test in TestHealthAuth above runs require_hmac=False, because _make_config()
# omits the `webhook` block entirely -> getattr(..., "require_hmac", False) resolves
# False. So they are all green and all blind to the branch below: /health had no
# require_hmac refusal at all, and a token-only GET authenticated even in a deploy
# that had explicitly opted out of the token fallback.
#
# Found by the fixture-blindness investigation
# (docs/audit/fixture_blindness_investigation_17jul2026.md). It is the same shape as
# the S4 outage that halted 17-Jul: G.1's tests own require_hmac on /webhook,
# TestHealthAuth owns auth on /health, both suites green and correct, and the bug sat
# in the cell neither owned.
#
# LATENT when written (require_hmac: false in prod) — this arms the moment that flag
# flips, which is an open operator action. Shipped ahead of the flip so the flip
# cannot create the bug.


def _make_config_require_hmac(require_hmac: bool, capacity=100):
    """_make_config + the `webhook` block the receiver reads require_hmac from.

    Mirrors production shape: config.system.webhook.require_hmac (webhook_receiver
    resolves .webhook first, then .system.webhook).
    """
    cfg = _make_config(capacity=capacity)
    cfg.system.webhook = types.SimpleNamespace(
        bind_host="127.0.0.1", bind_port=5000, require_hmac=require_hmac,
    )
    return cfg


def _make_receiver_require_hmac(require_hmac: bool, capacity=100):
    sq = queue.Queue(maxsize=capacity)
    td = tempfile.mkdtemp()
    store = StateStore(Path(td) / "test.db")
    receiver = WebhookReceiver(
        sq, store, _make_config_require_hmac(require_hmac, capacity),
        _MockMarketWindows(), _MockKillSwitch(), _NullLogger(),
        secret_token=_FAKE_TOKEN,
    )
    return receiver, sq, store


def _health_hmac_header(secret: str) -> dict:
    """/health signs an EMPTY body (it is a GET) — see webhook_receiver's health route."""
    import hashlib
    import hmac as _h
    sig = _h.new(secret.encode(), b"", hashlib.sha256).hexdigest()
    return {"X-Webhook-Signature": f"sha256={sig}"}


class TestHealthRequireHmac:
    """P1: require_hmac=True means HMAC-only on /health, as it already did on /webhook."""

    def test_require_hmac_resolves_true(self):
        """Guard: if this is False the rest of the class is vacuous — it would be
        re-testing require_hmac=False and passing for the wrong reason."""
        receiver, _, _ = _make_receiver_require_hmac(True)
        assert receiver._require_hmac is True

    def test_token_only_is_refused_when_require_hmac(self):
        """*** THE FIX. RED ON OLD CODE: returned 200 + kill_switch_active +
        queue_depth *** — i.e. the AB-910 §1.7 oracle stayed open to a URL-borne
        token in a deploy that had explicitly disabled the token fallback."""
        receiver, _, _ = _make_receiver_require_hmac(True)
        with receiver.app.test_client() as client:
            resp = client.get(f"/health?token={_FAKE_TOKEN}")   # a CORRECT token
        assert resp.status_code == 401, (
            "require_hmac=True must refuse a token-only /health, exactly as /webhook does"
        )
        # And the refusal must still leak nothing (the failure path is where oracles leak).
        data = resp.get_json() or {}
        for leaky in ("kill_switch_active", "queue_size", "queue_capacity", "queue_depth"):
            assert leaky not in data, f"/health 401 leaked {leaky}"

    def test_valid_hmac_still_authenticates_when_require_hmac(self):
        """Regression: the refusal must not lock out the ONE method require_hmac allows."""
        receiver, _, _ = _make_receiver_require_hmac(True)
        with receiver.app.test_client() as client:
            resp = client.get("/health", headers=_health_hmac_header(_FAKE_TOKEN))
        assert resp.status_code == 200, "valid HMAC must still authenticate /health"
        assert (resp.get_json() or {})["status"] == "ok"

    def test_wrong_hmac_is_refused_when_require_hmac(self):
        receiver, _, _ = _make_receiver_require_hmac(True)
        with receiver.app.test_client() as client:
            resp = client.get("/health", headers={"X-Webhook-Signature": "sha256=deadbeef"})
        assert resp.status_code == 401

    def test_require_hmac_true_does_not_fall_through_to_token(self):
        """Mirrors test_g1_require_hmac_true_rejects_invalid_hmac_does_not_fall_through:
        a bad signature must NOT be rescued by a valid token."""
        receiver, _, _ = _make_receiver_require_hmac(True)
        with receiver.app.test_client() as client:
            resp = client.get(f"/health?token={_FAKE_TOKEN}",
                              headers={"X-Webhook-Signature": "sha256=deadbeef"})
        assert resp.status_code == 401

    # ── require_hmac=False: the legacy posture must be untouched ──────────────

    def test_token_still_works_when_require_hmac_false(self):
        receiver, _, _ = _make_receiver_require_hmac(False)
        with receiver.app.test_client() as client:
            resp = client.get(f"/health?token={_FAKE_TOKEN}")
        assert resp.status_code == 200, "require_hmac=False must keep the token fallback"
        assert (resp.get_json() or {})["queue_depth"] == "0/100"

    def test_hmac_still_works_when_require_hmac_false(self):
        receiver, _, _ = _make_receiver_require_hmac(False)
        with receiver.app.test_client() as client:
            resp = client.get("/health", headers=_health_hmac_header(_FAKE_TOKEN))
        assert resp.status_code == 200

    def test_no_secret_stays_open_regardless_of_require_hmac(self):
        """require_hmac gates WHICH credential is accepted, never whether a
        secret-less deployment has an auth surface at all."""
        sq = queue.Queue(maxsize=100)
        td = tempfile.mkdtemp()
        store = StateStore(Path(td) / "test.db")
        receiver = WebhookReceiver(
            sq, store, _make_config_require_hmac(False),
            _MockMarketWindows(), _MockKillSwitch(), _NullLogger(),
            secret_token=None,
        )
        with receiver.app.test_client() as client:
            assert client.get("/health").status_code == 200


class TestS4SeamUnchangedByP1:
    """*** THE S4 GUARD. ***

    S4 halted production on 17-Jul because a change to /health's auth altered what the
    UNAUTHENTICATED boot self-check (main.py:3238) saw, and startup_checks turned that
    into _shutdown_event.set() — a whole trading day, 0 trades, behind a clean exit 0.

    P1 touches that exact surface, so it must prove the no-credential response is
    untouched: the boot self-check sends no token and no signature, and must still get
    a 401 that check_webhook_endpoint reads as reachable — under BOTH require_hmac
    settings. If this class ever goes red, P1 has re-created S4.
    """

    def test_unauthenticated_health_is_401_under_both_require_hmac_settings(self):
        """The no-credential path must be identical whether or not require_hmac is on —
        it never reaches the new branch's token fallback either way."""
        for require_hmac in (False, True):
            receiver, _, _ = _make_receiver_require_hmac(require_hmac)
            with receiver.app.test_client() as client:
                resp = client.get("/health")            # exactly as main.py:3238 calls it
            assert resp.status_code == 401, (
                f"require_hmac={require_hmac}: the boot self-check's unauthenticated "
                f"/health must still answer 401, got {resp.status_code}"
            )
            assert (resp.get_json() or {}) == {"error": "authentication required"}, (
                f"require_hmac={require_hmac}: the boot self-check's response body "
                f"changed — S4 was caused by exactly this kind of drift"
            )

    def test_boot_self_check_still_maps_that_401_to_reachable(self):
        """The full seam: the REAL /health 401 that P1 produces, fed to the REAL boot
        check. reachable=False here is what fired _shutdown_event on 17-Jul."""
        from utils.startup_checks import check_webhook_endpoint

        for require_hmac in (False, True):
            receiver, _, _ = _make_receiver_require_hmac(require_hmac)
            with receiver.app.test_client() as client:
                resp = client.get("/health")

            result = check_webhook_endpoint(
                "http://127.0.0.1:5000/health",
                lambda url, timeout, r=resp: (r.status_code, r.get_data(as_text=True)),
                _NullLogger(),
            )
            assert result.reachable is True, (
                f"require_hmac={require_hmac}: main.py fires _shutdown_event when this "
                f"is False — the system halts at boot and trades nothing"
            )
            assert result.status_code == 401
