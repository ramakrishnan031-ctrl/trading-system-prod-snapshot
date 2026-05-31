"""Tests for FIX-135 Item 44 + FIX-136 Item 44: F&O ban period check."""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.state_store import StateStore
from core.time_authority import today_ist
from scripts.fetch_fno_ban import (
    fetch_fno_ban_symbols,
    store_fno_ban,
    store_fetch_failed_sentinel,
    is_symbol_fno_banned,
    _FETCH_FAILED_SENTINEL,
)


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    return StateStore(db_path=tmp_path / "test.db")


# ── Store and query ──────────────────────────────────────────────────────


class TestStoreFnoBan:
    def test_store_and_query(self, store):
        log = logging.getLogger("test")
        stored = store_fno_ban(store, ["RELIANCE", "INFY"], today_ist(), log)
        assert stored == 2
        assert is_symbol_fno_banned(store, "RELIANCE")
        assert is_symbol_fno_banned(store, "INFY")

    def test_not_banned_returns_false(self, store):
        assert is_symbol_fno_banned(store, "TCS") is False

    def test_different_date_not_banned(self, store):
        log = logging.getLogger("test")
        store_fno_ban(store, ["RELIANCE"], "2020-01-01", log)
        assert is_symbol_fno_banned(store, "RELIANCE", today_ist()) is False
        assert is_symbol_fno_banned(store, "RELIANCE", "2020-01-01") is True

    def test_duplicate_insert_replaces(self, store):
        log = logging.getLogger("test")
        store_fno_ban(store, ["RELIANCE"], today_ist(), log)
        store_fno_ban(store, ["RELIANCE"], today_ist(), log)
        row = store.fetch_one(
            "SELECT COUNT(*) AS n FROM fno_ban WHERE symbol = 'RELIANCE' AND ban_date = ?",
            (today_ist(),),
        )
        assert row["n"] == 1

    def test_empty_list_stores_nothing(self, store):
        log = logging.getLogger("test")
        stored = store_fno_ban(store, [], today_ist(), log)
        assert stored == 0

    def test_multiple_symbols_stored(self, store):
        log = logging.getLogger("test")
        symbols = ["A", "B", "C", "D"]
        stored = store_fno_ban(store, symbols, today_ist(), log)
        assert stored == 4
        for sym in symbols:
            assert is_symbol_fno_banned(store, sym)


# ── Equity vs F&O logic ──────────────────────────────────────────────────


class TestEquityVsFno:
    def test_equity_strategy_ignores_ban(self, store):
        """Pure equity strategies should NOT check F&O ban (intent=INTRADAY, product=MIS)."""
        log = logging.getLogger("test")
        store_fno_ban(store, ["RELIANCE"], today_ist(), log)
        assert is_symbol_fno_banned(store, "RELIANCE") is True

    def test_banned_symbol_detected(self, store):
        log = logging.getLogger("test")
        store_fno_ban(store, ["BAJFINANCE"], today_ist(), log)
        assert is_symbol_fno_banned(store, "BAJFINANCE") is True
        assert is_symbol_fno_banned(store, "HDFCBANK") is False


# ── Parity ────────────────────────────────────────────────────────────────


class TestParity:
    def test_same_check_paper_and_live(self, store):
        """Ban check is config-driven, identical for paper and live."""
        log = logging.getLogger("test")
        store_fno_ban(store, ["SBIN"], today_ist(), log)
        assert is_symbol_fno_banned(store, "SBIN") is True


# ── FIX-136: Fail-closed + URL config + response validation ─────────────


class TestFailClosed:
    def test_sentinel_blocks_all_symbols(self, store):
        log = logging.getLogger("test")
        store_fetch_failed_sentinel(store, today_ist(), log)
        assert is_symbol_fno_banned(store, "RELIANCE") is True
        assert is_symbol_fno_banned(store, "TCS") is True
        assert is_symbol_fno_banned(store, "ANYTHING") is True

    def test_sentinel_date_scoped(self, store):
        log = logging.getLogger("test")
        store_fetch_failed_sentinel(store, "2020-01-01", log)
        assert is_symbol_fno_banned(store, "RELIANCE", "2020-01-01") is True
        assert is_symbol_fno_banned(store, "RELIANCE", today_ist()) is False

    def test_normal_ban_still_works_without_sentinel(self, store):
        log = logging.getLogger("test")
        store_fno_ban(store, ["INFY"], today_ist(), log)
        assert is_symbol_fno_banned(store, "INFY") is True
        assert is_symbol_fno_banned(store, "TCS") is False


class TestResponseValidation:
    def test_valid_list_response(self):
        log = logging.getLogger("test")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"symbol": "RELIANCE", "name": "Reliance Industries"},
            {"symbol": "INFY", "name": "Infosys"},
        ]
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("scripts.fetch_fno_ban.requests") as mock_requests:
            mock_requests.Session.return_value = mock_session
            symbols = fetch_fno_ban_symbols(log, url="http://test.local/api")
        assert symbols == ["RELIANCE", "INFY"]

    def test_valid_dict_data_response(self):
        log = logging.getLogger("test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"symbol": "SBIN", "name": "SBI"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("scripts.fetch_fno_ban.requests") as mock_requests:
            mock_requests.Session.return_value = mock_session
            symbols = fetch_fno_ban_symbols(log, url="http://test.local/api")
        assert symbols == ["SBIN"]

    def test_unexpected_string_response_raises(self):
        log = logging.getLogger("test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = "not a list or dict"
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("scripts.fetch_fno_ban.requests") as mock_requests:
            mock_requests.Session.return_value = mock_session
            with pytest.raises(RuntimeError, match="Unexpected response type"):
                fetch_fno_ban_symbols(log, url="http://test.local/api")

    def test_item_too_few_fields_raises(self):
        log = logging.getLogger("test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"symbol": "X"}]
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("scripts.fetch_fno_ban.requests") as mock_requests:
            mock_requests.Session.return_value = mock_session
            with pytest.raises(RuntimeError, match="fields, expected >="):
                fetch_fno_ban_symbols(log, url="http://test.local/api")

    def test_items_but_no_symbols_raises(self):
        log = logging.getLogger("test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"foo": "bar", "baz": "qux"},
        ]
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("scripts.fetch_fno_ban.requests") as mock_requests:
            mock_requests.Session.return_value = mock_session
            with pytest.raises(RuntimeError, match="0 valid symbols"):
                fetch_fno_ban_symbols(log, url="http://test.local/api")

    def test_empty_list_ok(self):
        log = logging.getLogger("test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        with patch("scripts.fetch_fno_ban.requests") as mock_requests:
            mock_requests.Session.return_value = mock_session
            symbols = fetch_fno_ban_symbols(log, url="http://test.local/api")
        assert symbols == []


class TestFnoBanConfig:
    def test_config_loads_fno_ban_section(self):
        from core.config_loader import FnoBanConfig
        cfg = FnoBanConfig()
        assert "nseindia" in cfg.url
        assert cfg.fail_closed is True
        assert cfg.min_expected_fields == 2
