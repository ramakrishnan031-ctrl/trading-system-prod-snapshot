"""
tests/unit/test_gemini_trade_coach.py -- FIX-151

Tests for scripts/gemini_trade_coach.py.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

def _load_module():
    spec = importlib.util.spec_from_file_location(
        "gemini_trade_coach",
        Path("scripts/gemini_trade_coach.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_mod = _load_module()


@pytest.fixture
def db_path(tmp_path):
    from core.state_store import StateStore
    db = tmp_path / "test.db"
    StateStore(db)
    return str(db)


@pytest.fixture
def log():
    return MagicMock()


def _seed_closed_trade(db_path, date_iso, symbol="RELIANCE"):
    conn = sqlite3.connect(db_path)
    ts = f"{date_iso}T09:30:00+05:30"
    exp = f"{date_iso}T10:30:00+05:30"
    conn.execute(
        """INSERT INTO signals
           (signal_id, symbol, scanner, strategy, triggered_at,
            received_at, expires_at, status, fingerprint, fingerprint_date)
           VALUES (?, ?, 'test_scanner', 'gap_fade_long', ?, ?, ?, 'TRADED',
                   ?, ?)""",
        (f"SIG_{symbol}", symbol, ts, ts, exp, f"fp_{symbol}", date_iso),
    )
    conn.execute(
        """INSERT INTO trades
           (trade_id, signal_id, symbol, direction, status, qty_planned,
            qty_filled, entry_target_price, entry_actual_price, sl_initial,
            tgt_initial, margin_reserved, risk_amount, strategy, net_pnl,
            gross_pnl, exit_price, exit_reason, entry_time, exit_time,
            created_at, order_protocol, updated_at, mode)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (f"T_{symbol}", f"SIG_{symbol}", symbol, "LONG", "CLOSED", 10, 10,
         100.0, 100.0, 95.0, 110.0, 200.0, 50.0, "gap_fade_long",
         45.0, 50.0, 104.5, "TGT_HIT",
         f"{date_iso}T10:00:00+05:30", f"{date_iso}T14:00:00+05:30",
         f"{date_iso}T10:00:00+05:30", "CO_PLUS_TGT",
         f"{date_iso}T14:00:00+05:30", "PAPER"),
    )
    conn.commit()
    conn.close()


def _seed_rejected_signal(db_path, date_iso, symbol="INFY"):
    conn = sqlite3.connect(db_path)
    ts = f"{date_iso}T09:30:00+05:30"
    exp = f"{date_iso}T10:30:00+05:30"
    conn.execute(
        """INSERT INTO signals
           (signal_id, symbol, scanner, strategy, triggered_at,
            received_at, expires_at, status, rejection_reason,
            fingerprint, fingerprint_date, trigger_price)
           VALUES (?, ?, 'test_scanner', 'gap_go_long', ?, ?, ?,
                   'REJECTED_RISK', 'max_positions', ?, ?, 1500.0)""",
        (f"SIG_REJ_{symbol}", symbol, ts, ts, exp, f"fp_rej_{symbol}", date_iso),
    )
    conn.commit()
    conn.close()


class TestGetClosedTrades:
    def test_found(self, db_path):
        _seed_closed_trade(db_path, "2026-06-03")
        trades = _mod._get_closed_trades(db_path, "2026-06-03")
        assert len(trades) == 1
        assert trades[0]["symbol"] == "RELIANCE"
        assert trades[0]["exit_reason"] == "TGT_HIT"

    def test_empty(self, db_path):
        trades = _mod._get_closed_trades(db_path, "2026-06-03")
        assert trades == []


class TestGetRejectedSignals:
    def test_found(self, db_path):
        _seed_rejected_signal(db_path, "2026-06-03")
        rejected = _mod._get_rejected_signals(db_path, "2026-06-03")
        assert len(rejected) == 1
        assert rejected[0]["rejection_reason"] == "max_positions"

    def test_empty(self, db_path):
        rejected = _mod._get_rejected_signals(db_path, "2026-06-03")
        assert rejected == []


class TestBuildData:
    def test_with_trades(self, db_path):
        _seed_closed_trade(db_path, "2026-06-03")
        trades = _mod._get_closed_trades(db_path, "2026-06-03")
        data = _mod._build_data(trades, [], db_path, "2026-06-03")
        assert "RELIANCE" in data
        assert "TGT_HIT" in data

    def test_with_rejected(self, db_path):
        _seed_rejected_signal(db_path, "2026-06-03")
        rejected = _mod._get_rejected_signals(db_path, "2026-06-03")
        data = _mod._build_data([], rejected, db_path, "2026-06-03")
        assert "INFY" in data
        assert "max_positions" in data

    def test_empty(self, db_path):
        data = _mod._build_data([], [], db_path, "2026-06-03")
        assert "2026-06-03" in data


class TestExtractLessons:
    def test_extracts_lessons(self):
        text = "Some analysis.\n\n## Top 3 Lessons\n1. Be patient.\n2. Wider SL.\n3. Better timing."
        lessons = _mod._extract_lessons(text)
        assert "Top 3 Lessons" in lessons
        assert "Be patient" in lessons

    def test_no_lessons_section(self):
        text = "Just analysis text without lessons section."
        lessons = _mod._extract_lessons(text)
        assert len(lessons) > 0


class TestGeminiCLI:
    @patch("subprocess.run")
    def test_success(self, mock_run, log):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="[RELIANCE] Entry: B Exit: A Notes: good timing",
            stderr="",
        )
        result = _mod._call_gemini_cli("prompt", "data", log)
        assert "RELIANCE" in result

    @patch("subprocess.run")
    def test_failure(self, mock_run, log):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        assert _mod._call_gemini_cli("prompt", "data", log) is None


class TestRunCoaching:
    def test_no_trades(self, db_path, log):
        result = _mod.run_coaching("2026-06-03", db_path, log)
        assert result == 2

    def test_dry_run(self, db_path, log):
        _seed_closed_trade(db_path, "2026-06-03")
        result = _mod.run_coaching("2026-06-03", db_path, log, dry_run=True)
        assert result == 0

    @patch.object(_mod, "_send_telegram")
    @patch.object(_mod, "_call_gemini_cli", return_value="[RELIANCE] Entry: B Exit: A\n\n## Top 3 Lessons\n1. Good.")
    def test_happy_path(self, mock_gemini, mock_tg, db_path, log, tmp_path):
        _seed_closed_trade(db_path, "2026-06-03")
        with patch.object(_mod, "OUTPUT_DIR", tmp_path / "coach"):
            result = _mod.run_coaching("2026-06-03", db_path, log)
        assert result == 0
        assert (tmp_path / "coach" / "coach_2026-06-03.md").exists()

    @patch.object(_mod, "_send_telegram")
    @patch.object(_mod, "_call_gemini_cli", return_value=None)
    def test_gemini_unavailable(self, mock_gemini, mock_tg, db_path, log, tmp_path):
        _seed_closed_trade(db_path, "2026-06-03")
        with patch.object(_mod, "OUTPUT_DIR", tmp_path / "coach"):
            result = _mod.run_coaching("2026-06-03", db_path, log)
        assert result == 0
        content = (tmp_path / "coach" / "coach_2026-06-03.md").read_text()
        assert "Gemini unavailable" in content


class TestMain:
    @patch.object(_mod, "run_coaching", return_value=0)
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_success(self, mock_log, mock_run):
        result = _mod.main(["--db", "test.db", "--dry-run"])
        assert result == 0

    @patch.object(_mod, "run_coaching", side_effect=RuntimeError("boom"))
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_error(self, mock_log, mock_run):
        result = _mod.main(["--db", "test.db"])
        assert result == 1


class TestArgParsing:
    def test_defaults(self):
        args = _mod._parse_args([])
        assert args.date is None
        assert not args.dry_run
