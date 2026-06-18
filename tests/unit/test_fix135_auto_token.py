"""Tests for headless Zerodha TOTP token refresh.

FIX-135 Item 49 introduced the TOTP refresh; FIX-187 fixed two blocking bugs:
  P0-A -- token was saved in a minimal format that failed
          zerodha_login.is_token_valid() (live startup gate) and lacked the
          api_key field the paper quote provider needs.
  P0-B -- request_token was read from the /api/twofa response instead of the
          connect/login redirect chain.
These tests lock both fixes plus the new error handling.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from scripts.auto_refresh_token import (
    _AuthError,
    _LOGIN_URL,
    _TWOFA_URL,
    _fetch_request_token,
    _request_token_from_url,
    _resolve_env,
    generate_totp,
    login_and_get_request_token,
    main,
)
# FIX-187: the headless refresh reuses the canonical save/validate helpers.
from scripts.zerodha_login import is_token_valid, load_token, save_token

_LOG = logging.getLogger("test_auto_token")


# ── Test doubles ────────────────────────────────────────────────────────────


class FakeResp:
    def __init__(self, status_code=200, json_data=None, headers=None, text="", url=""):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers


class FakeSession:
    """Routes POST by URL and serves queued GET responses."""

    def __init__(self, login_resp=None, twofa_resps=None, connect_resps=None):
        self.headers = {}
        self._login = login_resp
        self._twofa = list(twofa_resps or [])
        self._connect = list(connect_resps or [])
        self.posts = []
        self.gets = []

    def post(self, url, data=None, timeout=None):
        self.posts.append((url, data))
        if url == _LOGIN_URL:
            return self._login
        if url == _TWOFA_URL:
            return self._twofa.pop(0)
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, allow_redirects=None, timeout=None):
        self.gets.append((url, allow_redirects))
        return self._connect.pop(0)


def _login_ok():
    return FakeResp(200, {"status": "success", "data": {"request_id": "req123"}})


def _redirect_with_token(token="TOK123"):
    return FakeResp(
        302, headers={"Location": f"https://app.example/cb?request_token={token}&status=success"}
    )


# ── TOTP generation ───────────────────────────────────────────────────────


class TestTotpGeneration:
    def test_generates_6_digit_code(self):
        totp = generate_totp("JBSWY3DPEHPK3PXP")
        assert len(totp) == 6 and totp.isdigit()

    def test_delegates_to_pyotp(self):
        import pyotp
        secret = "JBSWY3DPEHPK3PXP"
        assert generate_totp(secret) == pyotp.TOTP(secret).now()


# ── request_token extraction (P0-B) ─────────────────────────────────────────


class TestRequestTokenExtraction:
    def test_parse_from_url(self):
        assert _request_token_from_url("https://x/cb?request_token=ABC&status=success") == "ABC"

    def test_parse_missing(self):
        assert _request_token_from_url("https://x/cb?status=success") is None
        assert _request_token_from_url("") is None

    def test_fetch_single_hop(self):
        session = FakeSession(connect_resps=[_redirect_with_token("HOP0")])
        assert _fetch_request_token(session, "apikey", _LOG) == "HOP0"

    def test_fetch_multi_hop(self):
        session = FakeSession(connect_resps=[
            FakeResp(302, headers={"Location": "https://kite.zerodha.com/connect/finish?sess=1"}),
            _redirect_with_token("HOP1"),
        ])
        assert _fetch_request_token(session, "apikey", _LOG) == "HOP1"
        assert len(session.gets) == 2

    def test_fetch_not_found_raises(self):
        session = FakeSession(connect_resps=[FakeResp(200, json_data={}, headers={})])
        with pytest.raises(_AuthError):
            _fetch_request_token(session, "apikey", _LOG)


# ── Headless login flow ─────────────────────────────────────────────────────


class TestLoginFlow:
    def test_happy_path(self):
        session = FakeSession(
            login_resp=_login_ok(),
            twofa_resps=[FakeResp(200, {"status": "success"})],
            connect_resps=[_redirect_with_token("TOKOK")],
        )
        token = login_and_get_request_token(
            session, "user", "pass", "JBSWY3DPEHPK3PXP", "apikey", _LOG
        )
        assert token == "TOKOK"
        # 2FA payload shape
        twofa_payload = dict(session.posts[1][1])
        assert twofa_payload["twofa_type"] == "totp"
        assert twofa_payload["request_id"] == "req123"
        assert len(twofa_payload["twofa_value"]) == 6

    def test_wrong_password_is_auth_error(self):
        session = FakeSession(
            login_resp=FakeResp(200, {"status": "error", "message": "wrong password"}),
        )
        with pytest.raises(_AuthError, match="Login failed"):
            login_and_get_request_token(session, "u", "p", "JBSWY3DPEHPK3PXP", "k", _LOG)

    def test_totp_retry_then_success(self):
        session = FakeSession(
            login_resp=_login_ok(),
            twofa_resps=[
                FakeResp(200, {"status": "error", "message": "invalid totp"}),
                FakeResp(200, {"status": "success"}),
            ],
            connect_resps=[_redirect_with_token("AFTERRETRY")],
        )
        with patch("scripts.auto_refresh_token.time.sleep"):
            token = login_and_get_request_token(session, "u", "p", "JBSWY3DPEHPK3PXP", "k", _LOG)
        assert token == "AFTERRETRY"
        assert len(session.posts) == 3  # login + 2x twofa

    def test_totp_fails_twice_is_auth_error(self):
        session = FakeSession(
            login_resp=_login_ok(),
            twofa_resps=[
                FakeResp(200, {"status": "error", "message": "invalid totp"}),
                FakeResp(200, {"status": "error", "message": "invalid totp"}),
            ],
        )
        with patch("scripts.auto_refresh_token.time.sleep"):
            with pytest.raises(_AuthError, match="2FA failed"):
                login_and_get_request_token(session, "u", "p", "JBSWY3DPEHPK3PXP", "k", _LOG)


# ── Credential resolution (account-specific, generic fallback) ───────────────


class TestResolveEnv:
    def test_prefers_account_specific(self):
        with patch.dict("os.environ", {"ZERODHA_TOTP_LFL836": "acct", "ZERODHA_TOTP_SECRET": "gen"}):
            val, name = _resolve_env("ZERODHA_TOTP_LFL836", "ZERODHA_TOTP_SECRET")
        assert (val, name) == ("acct", "ZERODHA_TOTP_LFL836")

    def test_falls_back_to_generic(self):
        with patch.dict("os.environ", {"ZERODHA_TOTP_SECRET": "gen"}, clear=False):
            import os
            os.environ.pop("ZERODHA_TOTP_LFL836", None)
            val, name = _resolve_env("ZERODHA_TOTP_LFL836", "ZERODHA_TOTP_SECRET")
        assert (val, name) == ("gen", "ZERODHA_TOTP_SECRET")

    def test_none_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            assert _resolve_env("A", "B") == (None, None)


# ── Token format parity (P0-A regression) ───────────────────────────────────


class TestTokenParity:
    def test_saved_token_passes_is_token_valid(self, tmp_path):
        """The canonical save_token (now used by auto_refresh_token) must produce
        a token that the live-startup gate is_token_valid() accepts."""
        token_path = tmp_path / "zerodha_token.json"
        save_token("LFL836", "zerodha", "apikey123", "access999", token_path)
        assert is_token_valid("LFL836", token_path) is True

    def test_saved_token_has_paper_provider_fields(self, tmp_path):
        """Paper quote provider reads token_data['api_key'] -- must be present."""
        token_path = tmp_path / "zerodha_token.json"
        save_token("LFL836", "zerodha", "apikey123", "access999", token_path)
        data = load_token(token_path)
        assert data["api_key"] == "apikey123"
        assert data["access_token"] == "access999"
        assert data["account_id"] == "LFL836"
        assert "date" in data  # required by is_token_valid


# ── main() entrypoint ────────────────────────────────────────────────────────

_FULL_ENV = {
    "ZERODHA_USER_ID": "TEST",
    "ZERODHA_PASSWORD": "pass",
    "ZERODHA_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
    "ZERODHA_API_KEY": "key",
    "ZERODHA_API_SECRET": "secret",
}


class TestMainEntrypoint:
    def test_missing_env_vars_returns_1(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch("scripts.auto_refresh_token._alert"), \
                patch("scripts.auto_refresh_token._heartbeat"):
            assert main(["--dry-run"]) == 1

    def test_dry_run_with_env_returns_0(self):
        with patch.dict("os.environ", _FULL_ENV, clear=True):
            assert main(["--dry-run"]) == 0

    def test_success_path_alerts_and_heartbeats(self):
        with patch.dict("os.environ", _FULL_ENV, clear=True), \
                patch("scripts.auto_refresh_token.run_refresh", return_value="access999") as rr, \
                patch("scripts.auto_refresh_token._alert") as alert, \
                patch("scripts.auto_refresh_token._heartbeat") as hb:
            rc = main([])
        assert rc == 0
        rr.assert_called_once()
        assert alert.call_args[0][1] == "INFO"
        assert hb.call_args[0][0] == "SUCCESS"

    def test_failure_path_alerts_critical_and_heartbeats_failed(self):
        with patch.dict("os.environ", _FULL_ENV, clear=True), \
                patch("scripts.auto_refresh_token.run_refresh", side_effect=_AuthError("bad creds")), \
                patch("scripts.auto_refresh_token._alert") as alert, \
                patch("scripts.auto_refresh_token._heartbeat") as hb:
            rc = main([])
        assert rc == 1
        assert alert.call_args[0][1] == "CRITICAL"
        assert hb.call_args[0][0] == "FAILED"
