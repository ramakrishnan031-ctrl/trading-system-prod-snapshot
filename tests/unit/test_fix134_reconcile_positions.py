"""Tests for FIX-134 Item 31: position reconciliation."""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.state_store import StateStore
from core.time_authority import now_ist, today_ist
from scripts.reconcile_positions import (
    run_position_reconciliation,
    _get_system_positions,
    _resolve_credentials,
)


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    db = tmp_path / "test.db"
    return StateStore(db_path=db)


def _insert_trade(store, symbol, direction, qty_filled, status="OPEN", date_iso=None):
    date_iso = date_iso or today_ist()
    ts = f"{date_iso}T10:00:00+05:30"
    import uuid
    trade_id = str(uuid.uuid4())
    signal_id = str(uuid.uuid4())
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, fingerprint, fingerprint_date, trigger_price)
               VALUES (?, ?, 'test', 'test', ?, ?, ?, 'TRADED', ?, ?, 100.0)""",
            (signal_id, symbol, ts, ts, ts, f"fp-{signal_id}", date_iso),
        )
        cur.execute(
            """INSERT INTO trades
               (trade_id, signal_id, symbol, direction, strategy, qty_planned,
                qty_filled, entry_target_price, sl_initial, tgt_initial,
                margin_reserved, risk_amount, created_at, status,
                order_protocol, updated_at)
               VALUES (?, ?, ?, ?, 'test', ?, ?, 100.0, 95.0, 110.0,
                       2000.0, 500.0, ?, ?, 'LIMIT_TRIPLE', ?)""",
            (trade_id, signal_id, symbol, direction, qty_filled, qty_filled,
             ts, status, ts),
        )
    return trade_id


# ── System position extraction ─────────────────────────────────────────────


class TestSystemPositions:
    def test_no_open_trades(self, store):
        result = _get_system_positions(store, today_ist())
        assert result == {}

    def test_long_trade(self, store):
        _insert_trade(store, "RELIANCE", "LONG", 10)
        result = _get_system_positions(store, today_ist())
        assert result == {"RELIANCE": 10}

    def test_short_trade(self, store):
        _insert_trade(store, "INFY", "SHORT", 5)
        result = _get_system_positions(store, today_ist())
        assert result == {"INFY": -5}

    def test_closed_trade_excluded(self, store):
        _insert_trade(store, "TCS", "LONG", 10, status="CLOSED")
        result = _get_system_positions(store, today_ist())
        assert result == {}

    def test_zero_qty_excluded(self, store):
        _insert_trade(store, "HDFC", "LONG", 0)
        result = _get_system_positions(store, today_ist())
        assert result == {}


# ── Paper mode ─────────────────────────────────────────────────────────────


class TestPaperMode:
    def test_paper_returns_ok_for_all(self, store):
        _insert_trade(store, "RELIANCE", "LONG", 10)
        _insert_trade(store, "INFY", "SHORT", 5)
        results = run_position_reconciliation(
            store=store,
            date_iso=today_ist(),
            is_paper=True,
            log=logging.getLogger("test"),
        )
        assert all(r["status"] == "OK" for r in results)
        assert len(results) == 2

    def test_paper_writes_db(self, store):
        _insert_trade(store, "SBIN", "LONG", 20)
        run_position_reconciliation(
            store=store,
            date_iso=today_ist(),
            is_paper=True,
            log=logging.getLogger("test"),
        )
        row = store.fetch_one(
            "SELECT * FROM position_reconciliation WHERE symbol = 'SBIN'"
        )
        assert row is not None
        assert row["status"] == "OK"

    def test_paper_dry_run_no_db(self, store):
        _insert_trade(store, "SBIN", "LONG", 20)
        run_position_reconciliation(
            store=store,
            date_iso=today_ist(),
            is_paper=True,
            log=logging.getLogger("test"),
            dry_run=True,
        )
        row = store.fetch_one(
            "SELECT * FROM position_reconciliation WHERE symbol = 'SBIN'"
        )
        assert row is None


# ── Live mode ──────────────────────────────────────────────────────────────


class TestLiveMode:
    @patch("scripts.reconcile_positions._fetch_broker_positions")
    def test_all_match(self, mock_fetch, store):
        _insert_trade(store, "RELIANCE", "LONG", 10)
        mock_fetch.return_value = {"RELIANCE": 10}
        results = run_position_reconciliation(
            store=store,
            date_iso=today_ist(),
            is_paper=False,
            log=logging.getLogger("test"),
        )
        assert len(results) == 1
        assert results[0]["status"] == "OK"

    @patch("scripts.reconcile_positions._fetch_broker_positions")
    def test_orphan_at_broker(self, mock_fetch, store):
        mock_fetch.return_value = {"UNKNOWN": 15}
        results = run_position_reconciliation(
            store=store,
            date_iso=today_ist(),
            is_paper=False,
            log=logging.getLogger("test"),
        )
        orphans = [r for r in results if r["status"] == "ORPHAN_AT_BROKER"]
        assert len(orphans) == 1
        assert orphans[0]["symbol"] == "UNKNOWN"

    @patch("scripts.reconcile_positions._fetch_broker_positions")
    def test_missing_at_broker(self, mock_fetch, store):
        _insert_trade(store, "RELIANCE", "LONG", 10)
        mock_fetch.return_value = {}
        results = run_position_reconciliation(
            store=store,
            date_iso=today_ist(),
            is_paper=False,
            log=logging.getLogger("test"),
        )
        missing = [r for r in results if r["status"] == "MISSING_AT_BROKER"]
        assert len(missing) == 1
        assert missing[0]["symbol"] == "RELIANCE"

    @patch("scripts.reconcile_positions._fetch_broker_positions")
    def test_qty_mismatch(self, mock_fetch, store):
        _insert_trade(store, "INFY", "LONG", 10)
        mock_fetch.return_value = {"INFY": 15}
        results = run_position_reconciliation(
            store=store,
            date_iso=today_ist(),
            is_paper=False,
            log=logging.getLogger("test"),
        )
        assert results[0]["status"] == "QTY_MISMATCH"

    @patch("scripts.reconcile_positions._fetch_broker_positions")
    def test_mismatch_sends_telegram(self, mock_fetch, store):
        _insert_trade(store, "RELIANCE", "LONG", 10)
        mock_fetch.return_value = {}
        notifier = MagicMock()
        run_position_reconciliation(
            store=store,
            date_iso=today_ist(),
            is_paper=False,
            log=logging.getLogger("test"),
            notifier=notifier,
        )
        assert notifier.send.called
        call_kwargs = notifier.send.call_args
        assert "MISMATCH" in call_kwargs.kwargs.get("title", call_kwargs[1].get("title", ""))

    @patch("scripts.reconcile_positions._fetch_broker_positions")
    def test_broker_fetch_error(self, mock_fetch, store):
        mock_fetch.side_effect = RuntimeError("API down")
        results = run_position_reconciliation(
            store=store,
            date_iso=today_ist(),
            is_paper=False,
            log=logging.getLogger("test"),
        )
        assert results[0]["status"] == "ERROR"

    @patch("scripts.reconcile_positions._fetch_broker_positions")
    def test_results_stored_in_db(self, mock_fetch, store):
        _insert_trade(store, "TCS", "LONG", 5)
        mock_fetch.return_value = {"TCS": 5}
        run_position_reconciliation(
            store=store,
            date_iso=today_ist(),
            is_paper=False,
            log=logging.getLogger("test"),
        )
        row = store.fetch_one(
            "SELECT * FROM position_reconciliation WHERE symbol = 'TCS'"
        )
        assert row is not None
        assert row["status"] == "OK"
        assert row["broker_qty"] == 5
        assert row["system_qty"] == 5

    @patch("scripts.reconcile_positions._fetch_broker_positions")
    def test_mixed_positions(self, mock_fetch, store):
        _insert_trade(store, "RELIANCE", "LONG", 10)
        _insert_trade(store, "INFY", "SHORT", 5)
        mock_fetch.return_value = {"RELIANCE": 10, "INFY": -5, "ORPHAN": 3}
        results = run_position_reconciliation(
            store=store,
            date_iso=today_ist(),
            is_paper=False,
            log=logging.getLogger("test"),
        )
        statuses = {r["symbol"]: r["status"] for r in results}
        assert statuses["RELIANCE"] == "OK"
        assert statuses["INFY"] == "OK"
        assert statuses["ORPHAN"] == "ORPHAN_AT_BROKER"

    @patch("scripts.reconcile_positions._fetch_broker_positions")
    def test_credentials_threaded_to_fetch(self, mock_fetch, store):
        """Resolved api_key/access_token are passed through to the broker fetch."""
        mock_fetch.return_value = {}
        run_position_reconciliation(
            store=store,
            date_iso=today_ist(),
            is_paper=False,
            log=logging.getLogger("test"),
            api_key="KEY123",
            access_token="TOK456",
        )
        mock_fetch.assert_called_once()
        pos_args = mock_fetch.call_args[0]
        # signature: _fetch_broker_positions(log, api_key, access_token)
        assert pos_args[1] == "KEY123"
        assert pos_args[2] == "TOK456"


# ── Credential resolution (the 19-Jun fix: per-account env + token file) ─────


class TestResolveCredentials:
    """The script used to read generic ZERODHA_API_KEY/ACCESS_TOKEN (never set).
    Now it mirrors refresh_instruments: api_key from <account.api_key_env> +
    access_token from the token JSON via zerodha_login.load_token."""

    _CFG = Path("config")
    _LOG = logging.getLogger("test")

    def test_account_path_resolves(self, monkeypatch):
        monkeypatch.setenv("ZERODHA_API_KEY_LFL836", "peracct_key")
        with patch("scripts.zerodha_login.load_token",
                   return_value={"access_token": "tok_from_file"}), \
             patch("scripts.zerodha_login.is_token_valid", return_value=True):
            api_key, access_token = _resolve_credentials("LFL836", self._CFG, self._LOG)
        assert api_key == "peracct_key"
        assert access_token == "tok_from_file"

    def test_account_missing_env_raises(self, monkeypatch):
        monkeypatch.delenv("ZERODHA_API_KEY_LFL836", raising=False)
        with pytest.raises(RuntimeError, match="not set"):
            _resolve_credentials("LFL836", self._CFG, self._LOG)

    def test_account_not_found_raises(self):
        with pytest.raises(RuntimeError, match="not found"):
            _resolve_credentials("NOPE999", self._CFG, self._LOG)

    def test_token_file_missing_raises(self, monkeypatch):
        monkeypatch.setenv("ZERODHA_API_KEY_LFL836", "peracct_key")
        with patch("scripts.zerodha_login.load_token", return_value=None):
            with pytest.raises(RuntimeError, match="token file"):
                _resolve_credentials("LFL836", self._CFG, self._LOG)

    def test_empty_access_token_raises(self, monkeypatch):
        monkeypatch.setenv("ZERODHA_API_KEY_LFL836", "peracct_key")
        with patch("scripts.zerodha_login.load_token",
                   return_value={"access_token": "  "}):
            with pytest.raises(RuntimeError, match="access_token missing"):
                _resolve_credentials("LFL836", self._CFG, self._LOG)

    def test_stale_token_warns_but_proceeds(self, monkeypatch, caplog):
        monkeypatch.setenv("ZERODHA_API_KEY_LFL836", "peracct_key")
        with patch("scripts.zerodha_login.load_token",
                   return_value={"access_token": "tok"}), \
             patch("scripts.zerodha_login.is_token_valid", return_value=False):
            with caplog.at_level(logging.WARNING):
                api_key, access_token = _resolve_credentials("LFL836", self._CFG, self._LOG)
        assert (api_key, access_token) == ("peracct_key", "tok")
        assert any("stale" in r.message.lower() for r in caplog.records)

    def test_generic_fallback_when_no_account(self, monkeypatch):
        monkeypatch.setenv("ZERODHA_API_KEY", "generic_key")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "generic_tok")
        api_key, access_token = _resolve_credentials("", self._CFG, self._LOG)
        assert (api_key, access_token) == ("generic_key", "generic_tok")

    def test_generic_fallback_missing_raises(self, monkeypatch):
        monkeypatch.delenv("ZERODHA_API_KEY", raising=False)
        monkeypatch.delenv("ZERODHA_ACCESS_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="must be set"):
            _resolve_credentials("", self._CFG, self._LOG)
