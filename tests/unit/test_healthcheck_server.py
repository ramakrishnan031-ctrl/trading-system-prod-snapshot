"""Tests for the FIX-188 /health enhancements (token + kill-switch + 200/503)."""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

from scripts.healthcheck_server import _check_kill_switch, _check_token, _create_app

_LOG = logging.getLogger("test_hc")


# ── _check_kill_switch ───────────────────────────────────────────────────────


class TestCheckKillSwitch:
    def _store(self, row):
        s = MagicMock()
        s.fetch_one.return_value = row  # SELECT state, reason FROM kill_switch_state WHERE id=1
        return s

    def test_inactive_ok(self):
        r = _check_kill_switch(self._store({"state": "INACTIVE", "reason": ""}), _LOG)
        assert r["ok"] is True and r["state"] == "INACTIVE"

    def test_soft_kill_not_ok(self):
        r = _check_kill_switch(self._store({"state": "SOFT_KILL", "reason": "API failures"}), _LOG)
        assert r["ok"] is False and r["state"] == "SOFT_KILL" and r["reason"] == "API failures"

    def test_no_row_defaults_inactive(self):
        assert _check_kill_switch(self._store(None), _LOG)["ok"] is True

    def test_query_error_not_ok(self):
        s = MagicMock()
        s.fetch_one.side_effect = RuntimeError("db gone")
        r = _check_kill_switch(s, _LOG)
        assert r["ok"] is False and "error" in r


# ── _check_token ─────────────────────────────────────────────────────────────


class TestCheckToken:
    def test_valid(self):
        with patch("scripts.zerodha_login.load_token",
                   return_value={"account_id": "LFL836", "expires_at": "2026-06-19T05:00:00+05:30"}), \
             patch("scripts.zerodha_login.is_token_valid", return_value=True):
            r = _check_token()
        assert r["ok"] is True and r["account_id"] == "LFL836"

    def test_missing_token(self):
        with patch("scripts.zerodha_login.load_token", return_value=None):
            r = _check_token()
        assert r["ok"] is False

    def test_invalid_token(self):
        with patch("scripts.zerodha_login.load_token", return_value={"account_id": "LFL836"}), \
             patch("scripts.zerodha_login.is_token_valid", return_value=False):
            assert _check_token()["ok"] is False


# ── /health route (200 / 503) ───────────────────────────────────────────────


class TestHealthRoute:
    def _client(self):
        store = MagicMock()
        store.fetch_one.return_value = {"cnt": 0}  # trades_today
        return _create_app(store, _LOG).test_client()

    def test_healthy_200(self):
        with patch("scripts.healthcheck_server._check_token", return_value={"ok": True}), \
             patch("scripts.healthcheck_server._check_kill_switch", return_value={"ok": True, "state": "INACTIVE"}):
            resp = self._client().get("/health")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "healthy"
        assert set(data["checks"]) == {"db", "token", "kill_switch"}

    def test_degraded_on_kill_switch_503(self):
        with patch("scripts.healthcheck_server._check_token", return_value={"ok": True}), \
             patch("scripts.healthcheck_server._check_kill_switch", return_value={"ok": False, "state": "SOFT_KILL"}):
            resp = self._client().get("/health")
        assert resp.status_code == 503
        assert json.loads(resp.data)["status"] == "degraded"

    def test_degraded_on_token_503(self):
        with patch("scripts.healthcheck_server._check_token", return_value={"ok": False}), \
             patch("scripts.healthcheck_server._check_kill_switch", return_value={"ok": True, "state": "INACTIVE"}):
            resp = self._client().get("/health")
        assert resp.status_code == 503
        assert json.loads(resp.data)["status"] == "degraded"


# ── tgt_retry liveness in /health (post-mortem 24-Jun) ───────────────────────


class TestHealthTgtRetry:
    """A silently-dead/crash-looping TGT-retry daemon must turn /health 503 so it
    shows up as a pre-flight Phase-B failing check, not just a CRITICAL email."""

    def _client(self, provider):
        store = MagicMock()
        store.fetch_one.return_value = {"cnt": 0}
        return _create_app(store, _LOG, tgt_retry_provider=provider).test_client()

    def _green(self):
        return patch("scripts.healthcheck_server._check_token", return_value={"ok": True}), \
               patch("scripts.healthcheck_server._check_kill_switch",
                     return_value={"ok": True, "state": "INACTIVE"})

    def test_no_provider_omits_check(self):
        # Backward-compatible: without a provider the checks set is unchanged.
        store = MagicMock()
        store.fetch_one.return_value = {"cnt": 0}
        t, k = self._green()
        with t, k:
            resp = _create_app(store, _LOG).test_client().get("/health")
        assert set(json.loads(resp.data)["checks"]) == {"db", "token", "kill_switch"}

    def test_running_healthy_200(self):
        t, k = self._green()
        with t, k:
            resp = self._client(lambda: {"ok": True, "state": "running"}).get("/health")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "healthy"
        assert data["checks"]["tgt_retry"]["state"] == "running"

    def test_crash_loop_degraded_503(self):
        t, k = self._green()
        with t, k:
            resp = self._client(
                lambda: {"ok": False, "state": "crash_loop",
                         "consecutive_failures": 7}).get("/health")
        assert resp.status_code == 503
        data = json.loads(resp.data)
        assert data["status"] == "degraded"
        assert data["checks"]["tgt_retry"]["ok"] is False

    def test_provider_raises_is_not_ok_503(self):
        def _boom():
            raise RuntimeError("snapshot failed")
        t, k = self._green()
        with t, k:
            resp = self._client(_boom).get("/health")
        assert resp.status_code == 503
        assert json.loads(resp.data)["checks"]["tgt_retry"]["ok"] is False
