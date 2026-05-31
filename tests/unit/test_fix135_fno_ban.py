"""Tests for FIX-135 Item 44: F&O ban period check."""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.state_store import StateStore
from core.time_authority import today_ist
from scripts.fetch_fno_ban import (
    store_fno_ban,
    is_symbol_fno_banned,
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
