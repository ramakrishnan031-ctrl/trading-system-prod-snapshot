"""
tests/unit/test_gemini_data_integrity_check.py -- FIX-150

Tests for scripts/gemini_data_integrity_check.py.

Coverage:
  - Traded symbols query (found, none)
  - System candles retrieval
  - Instrument token lookup
  - Candle comparison: clean match, OHLC mismatch, volume mismatch, missing candles
  - Gemini CLI invocation (success, failure)
  - run_check happy path (clean)
  - run_check divergence path
  - run_check no traded symbols returns 3
  - Dry-run skips Zerodha fetch + Gemini
  - Telegram sent on divergence
  - Report file saved
  - main() returns 0 on success
  - main() returns 1 on error
  - CLI arg parsing
"""
from __future__ import annotations

import importlib.util
import sqlite3
from core import db_connect  # O6: ATTACH analytics.db
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module import
# ---------------------------------------------------------------------------

def _load_module():
    spec = importlib.util.spec_from_file_location(
        "gemini_data_integrity_check",
        Path("scripts/gemini_data_integrity_check.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_mod = _load_module()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    from core.state_store import StateStore
    db = tmp_path / "test.db"
    StateStore(db)
    return str(db)


@pytest.fixture
def log():
    return MagicMock()


def _seed_trades(db_path, date_iso, symbols):
    conn = db_connect.connect(db_path)
    ts = f"{date_iso}T09:30:00+05:30"
    exp = f"{date_iso}T10:30:00+05:30"
    for i, sym in enumerate(symbols):
        sig_id = f"SIG{i}"
        conn.execute(
            """INSERT OR IGNORE INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at,
                received_at, expires_at, status, fingerprint, fingerprint_date)
               VALUES (?, ?, 'test_scanner', 'breakout_long', ?, ?, ?, 'TRADED',
                       ?, ?)""",
            (sig_id, sym, ts, ts, exp, f"fp_{sig_id}", date_iso),
        )
        conn.execute(
            """INSERT INTO trades
               (trade_id, signal_id, symbol, direction, status, qty_planned,
                qty_filled, entry_target_price, entry_actual_price, sl_initial,
                tgt_initial, margin_reserved, risk_amount, strategy, created_at,
                order_protocol, updated_at, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"T{i}", sig_id, sym, "LONG", "CLOSED", 10, 10, 100.0, 100.0,
             95.0, 110.0, 200.0, 50.0, "breakout_long",
             f"{date_iso}T10:00:00+05:30", "CO_PLUS_TGT",
             f"{date_iso}T10:00:00+05:30", "PAPER"),
        )
    conn.commit()
    conn.close()


def _seed_candles(db_path, symbol, date_iso, candles, instrument_token=999999):
    conn = db_connect.connect(db_path)
    for c in candles:
        conn.execute(
            """INSERT INTO candles (symbol, instrument_token, ts, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, instrument_token, c["ts"], c["open"], c["high"], c["low"], c["close"], c["volume"]),
        )
    conn.commit()
    conn.close()


def _seed_instrument(db_path, symbol, token):
    conn = db_connect.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS instruments (
                instrument_token INTEGER PRIMARY KEY,
                exchange_token INTEGER,
                tradingsymbol TEXT NOT NULL,
                name TEXT,
                exchange TEXT,
                segment TEXT,
                instrument_type TEXT,
                lot_size INTEGER DEFAULT 1,
                tick_size REAL DEFAULT 0.05
            )"""
        )
        conn.execute(
            """INSERT INTO instruments
               (instrument_token, exchange_token, tradingsymbol, name,
                exchange, segment, instrument_type, lot_size, tick_size)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (token, token, symbol, symbol, "NSE", "NSE", "EQ", 1, 0.05),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests: _get_traded_symbols
# ---------------------------------------------------------------------------

class TestGetTradedSymbols:
    def test_returns_symbols(self, db_path):
        _seed_trades(db_path, "2026-06-03", ["RELIANCE", "INFY", "RELIANCE"])
        symbols = _mod._get_traded_symbols(db_path, "2026-06-03")
        assert set(symbols) == {"RELIANCE", "INFY"}

    def test_returns_empty_no_trades(self, db_path):
        symbols = _mod._get_traded_symbols(db_path, "2026-06-03")
        assert symbols == []

    def test_wrong_date_returns_empty(self, db_path):
        _seed_trades(db_path, "2026-06-02", ["RELIANCE"])
        symbols = _mod._get_traded_symbols(db_path, "2026-06-03")
        assert symbols == []


# ---------------------------------------------------------------------------
# Tests: _get_system_candles
# ---------------------------------------------------------------------------

class TestGetSystemCandles:
    def test_returns_candles(self, db_path):
        candles = [
            {"ts": "2026-06-03 09:15:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
            {"ts": "2026-06-03 09:16:00", "open": 100.5, "high": 102, "low": 100, "close": 101, "volume": 1200},
        ]
        _seed_candles(db_path, "RELIANCE", "2026-06-03", candles)
        result = _mod._get_system_candles(db_path, "RELIANCE", "2026-06-03")
        assert len(result) == 2
        assert result[0]["open"] == 100

    def test_returns_empty_no_data(self, db_path):
        result = _mod._get_system_candles(db_path, "RELIANCE", "2026-06-03")
        assert result == []


# ---------------------------------------------------------------------------
# Tests: _get_instrument_token
# ---------------------------------------------------------------------------

class TestGetInstrumentToken:
    def test_found(self, db_path):
        _seed_instrument(db_path, "RELIANCE", 738561)
        assert _mod._get_instrument_token(db_path, "RELIANCE") == 738561

    def test_not_found(self, db_path):
        assert _mod._get_instrument_token(db_path, "UNKNOWN") is None

    def test_table_missing_returns_none_and_warns(self, db_path, log):
        # db_path uses StateStore schema which has no instruments table (O8)
        result = _mod._get_instrument_token(db_path, "RELIANCE", log)
        assert result is None
        log.warning.assert_called_once()
        msg = log.warning.call_args[0][0]
        assert "instruments table not found" in msg

    def test_table_missing_no_log_does_not_raise(self, db_path):
        # log=None must not crash when table is absent
        assert _mod._get_instrument_token(db_path, "RELIANCE") is None


# ---------------------------------------------------------------------------
# Tests: _compare_candles
# ---------------------------------------------------------------------------

class TestCompareCandles:
    def test_clean_match(self):
        candles = [
            {"ts": "09:15:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000},
        ]
        result = _mod._compare_candles(candles, candles)
        assert not result["has_divergence"]
        assert result["ohlc_mismatches"] == []
        assert result["volume_mismatches"] == []

    def test_ohlc_mismatch(self):
        system = [{"ts": "09:15:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000}]
        zerodha = [{"ts": "09:15:00", "open": 105.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000}]
        result = _mod._compare_candles(system, zerodha)
        assert result["has_divergence"]
        assert len(result["ohlc_mismatches"]) >= 1
        assert result["ohlc_mismatches"][0]["field"] == "open"

    def test_volume_mismatch(self):
        system = [{"ts": "09:15:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000}]
        zerodha = [{"ts": "09:15:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 2000}]
        result = _mod._compare_candles(system, zerodha)
        assert result["has_divergence"]
        assert len(result["volume_mismatches"]) == 1

    def test_missing_in_system(self):
        system = []
        zerodha = [{"ts": "09:15:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000}]
        result = _mod._compare_candles(system, zerodha)
        assert result["missing_in_system"] == 1

    def test_missing_in_zerodha(self):
        system = [{"ts": "09:15:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000}]
        zerodha = []
        result = _mod._compare_candles(system, zerodha)
        assert result["missing_in_zerodha"] == 1

    def test_within_threshold_no_mismatch(self):
        system = [{"ts": "09:15:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000}]
        zerodha = [{"ts": "09:15:00", "open": 100.01, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000}]
        result = _mod._compare_candles(system, zerodha)
        assert not result["has_divergence"]


# ---------------------------------------------------------------------------
# Tests: Gemini CLI
# ---------------------------------------------------------------------------

class TestGeminiCLI:
    @patch("subprocess.run")
    def test_success(self, mock_run, log):
        mock_run.return_value = MagicMock(returncode=0, stdout="Status: CLEAN\nAll good.", stderr="")
        result = _mod._call_gemini_cli("prompt", "data", log)
        assert "CLEAN" in result

    @patch("subprocess.run")
    def test_failure(self, mock_run, log):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = _mod._call_gemini_cli("prompt", "data", log)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: run_check
# ---------------------------------------------------------------------------

class TestRunCheck:
    def test_no_traded_symbols_returns_3(self, db_path, log, tmp_path):
        result = _mod.run_check("2026-06-03", db_path, tmp_path / "out", 5, log)
        assert result == 3

    def test_dry_run(self, db_path, log, tmp_path):
        _seed_trades(db_path, "2026-06-03", ["RELIANCE"])
        candles = [
            {"ts": "2026-06-03 09:15:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
        ]
        _seed_candles(db_path, "RELIANCE", "2026-06-03", candles)

        result = _mod.run_check("2026-06-03", db_path, tmp_path / "out", 5, log, dry_run=True)
        assert result == 0

    @patch.object(_mod, "_send_telegram")
    @patch.object(_mod, "_call_gemini_cli", return_value="Status: CLEAN\nAll matched.")
    @patch.object(_mod, "_fetch_zerodha_candles")
    def test_clean_check(self, mock_fetch, mock_gemini, mock_tg, db_path, log, tmp_path):
        _seed_trades(db_path, "2026-06-03", ["RELIANCE"])
        _seed_instrument(db_path, "RELIANCE", 738561)
        sys_candles = [
            {"ts": "2026-06-03 09:15:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
        ]
        _seed_candles(db_path, "RELIANCE", "2026-06-03", sys_candles)
        mock_fetch.return_value = [
            {"ts": "2026-06-03 09:15:00", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000},
        ]

        out_dir = tmp_path / "integrity"
        result = _mod.run_check("2026-06-03", db_path, out_dir, 5, log)
        assert result == 0
        assert (out_dir / "integrity_2026-06-03.md").exists()
        mock_tg.assert_not_called()

    @patch.object(_mod, "_send_telegram")
    @patch.object(_mod, "_call_gemini_cli", return_value="Status: DIVERGENCE_DETECTED\nOpen mismatch.")
    @patch.object(_mod, "_fetch_zerodha_candles")
    def test_divergence_returns_2(self, mock_fetch, mock_gemini, mock_tg, db_path, log, tmp_path):
        _seed_trades(db_path, "2026-06-03", ["RELIANCE"])
        _seed_instrument(db_path, "RELIANCE", 738561)
        _seed_candles(db_path, "RELIANCE", "2026-06-03", [
            {"ts": "2026-06-03 09:15:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
        ])
        mock_fetch.return_value = [
            {"ts": "2026-06-03 09:15:00", "open": 200.0, "high": 201.0, "low": 199.0, "close": 200.5, "volume": 5000},
        ]

        out_dir = tmp_path / "integrity"
        result = _mod.run_check("2026-06-03", db_path, out_dir, 5, log)
        assert result == 2
        mock_tg.assert_called_once()

    @patch.object(_mod, "_call_gemini_cli", return_value=None)
    @patch.object(_mod, "_fetch_zerodha_candles", return_value=None)
    def test_zerodha_fetch_failed(self, mock_fetch, mock_gemini, db_path, log, tmp_path):
        _seed_trades(db_path, "2026-06-03", ["RELIANCE"])
        _seed_instrument(db_path, "RELIANCE", 738561)
        _seed_candles(db_path, "RELIANCE", "2026-06-03", [
            {"ts": "2026-06-03 09:15:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
        ])

        out_dir = tmp_path / "integrity"
        result = _mod.run_check("2026-06-03", db_path, out_dir, 5, log)
        assert result == 0

    @patch.object(_mod, "_fetch_zerodha_candles", return_value=None)
    def test_no_instrument_token(self, mock_fetch, db_path, log, tmp_path):
        _seed_trades(db_path, "2026-06-03", ["RELIANCE"])
        _seed_candles(db_path, "RELIANCE", "2026-06-03", [
            {"ts": "2026-06-03 09:15:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
        ])

        out_dir = tmp_path / "integrity"
        result = _mod.run_check("2026-06-03", db_path, out_dir, 5, log, dry_run=True)
        assert result == 0


# ---------------------------------------------------------------------------
# Tests: main()
# ---------------------------------------------------------------------------

class TestMain:
    @patch.object(_mod, "run_check", return_value=0)
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_success(self, mock_logger, mock_run):
        result = _mod.main(["--db", "test.db", "--dry-run"])
        assert result == 0

    @patch.object(_mod, "run_check", side_effect=RuntimeError("boom"))
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_error_returns_1(self, mock_logger, mock_run):
        result = _mod.main(["--db", "test.db"])
        assert result == 1


# ---------------------------------------------------------------------------
# Tests: CLI arg parsing
# ---------------------------------------------------------------------------

class TestArgParsing:
    def test_defaults(self):
        args = _mod._parse_args([])
        assert args.date is None
        assert not args.dry_run
        assert args.sample_size == 5

    def test_custom_date(self):
        args = _mod._parse_args(["--date", "2026-06-01"])
        assert args.date == "2026-06-01"

    def test_custom_sample_size(self):
        args = _mod._parse_args(["--sample-size", "3"])
        assert args.sample_size == 3
