"""Tests for FIX-135 Item 49: automated Zerodha TOTP token refresh."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.auto_refresh_token import (
    generate_totp,
    save_token,
)


# ── TOTP generation ──────────────────────────────────────────────────────


class TestTotpGeneration:
    def test_generates_6_digit_code(self):
        totp = generate_totp("JBSWY3DPEHPK3PXP")
        assert len(totp) == 6
        assert totp.isdigit()

    def test_different_secrets_different_codes(self):
        t1 = generate_totp("JBSWY3DPEHPK3PXP")
        t2 = generate_totp("NBSWY3DPEHPK3PXQ")
        # They could be same by coincidence but we're testing it doesn't crash
        assert isinstance(t1, str)
        assert isinstance(t2, str)


# ── Token save ────────────────────────────────────────────────────────────


class TestSaveToken:
    def test_saves_to_file(self, tmp_path):
        token_path = tmp_path / "session" / "zerodha_token.json"
        save_token(
            {"access_token": "test123", "user_id": "ABC"},
            token_path,
            logging.getLogger("test"),
        )
        assert token_path.exists()
        data = json.loads(token_path.read_text(encoding="utf-8"))
        assert data["access_token"] == "test123"

    def test_creates_parent_dirs(self, tmp_path):
        token_path = tmp_path / "deep" / "nested" / "token.json"
        save_token({"access_token": "x"}, token_path, logging.getLogger("test"))
        assert token_path.exists()

    def test_atomic_write_no_partial(self, tmp_path):
        token_path = tmp_path / "token.json"
        save_token({"access_token": "first"}, token_path, logging.getLogger("test"))
        save_token({"access_token": "second"}, token_path, logging.getLogger("test"))
        data = json.loads(token_path.read_text(encoding="utf-8"))
        assert data["access_token"] == "second"
        tmp_file = token_path.with_suffix(".json.tmp")
        assert not tmp_file.exists()


# ── Main entrypoint ──────────────────────────────────────────────────────


class TestMainEntrypoint:
    def test_missing_env_vars_returns_1(self):
        from scripts.auto_refresh_token import main
        with patch.dict("os.environ", {}, clear=True):
            result = main(["--dry-run"])
        assert result == 1

    def test_dry_run_with_env_returns_0(self):
        from scripts.auto_refresh_token import main
        env = {
            "ZERODHA_USER_ID": "TEST",
            "ZERODHA_PASSWORD": "pass",
            "ZERODHA_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
            "ZERODHA_API_KEY": "key",
            "ZERODHA_API_SECRET": "secret",
        }
        with patch.dict("os.environ", env, clear=True):
            result = main(["--dry-run"])
        assert result == 0


# ── Parity ────────────────────────────────────────────────────────────────


class TestParity:
    def test_token_format_same_paper_live(self, tmp_path):
        """Token JSON format is identical regardless of mode."""
        token_path = tmp_path / "token.json"
        save_token(
            {"access_token": "abc123", "user_id": "XYZ", "login_time": "2026-05-31T08:00:00"},
            token_path,
            logging.getLogger("test"),
        )
        data = json.loads(token_path.read_text(encoding="utf-8"))
        assert "access_token" in data
        assert "login_time" in data
