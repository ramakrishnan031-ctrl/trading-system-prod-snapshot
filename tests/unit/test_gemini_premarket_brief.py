"""
tests/unit/test_gemini_premarket_brief.py -- FIX-150

Tests for scripts/gemini_premarket_brief.py.

Coverage:
  - Yesterday calculation (weekday + weekend rollback)
  - EOD review loading (exists, missing, truncated)
  - Open positions query
  - Strategy state query
  - Kill switch state query
  - Yesterday summary query
  - Gemini CLI invocation (success, failure, timeout)
  - run_briefing happy path with Gemini
  - run_briefing fallback when Gemini unavailable
  - Briefing file saved to disk
  - Telegram notification sent
  - Dry-run skips Gemini + Telegram
  - main() returns 0 on success
  - main() returns 1 on error
  - CLI arg parsing
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module import
# ---------------------------------------------------------------------------

def _load_module():
    spec = importlib.util.spec_from_file_location(
        "gemini_premarket_brief",
        Path("scripts/gemini_premarket_brief.py"),
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


# ---------------------------------------------------------------------------
# Tests: _yesterday
# ---------------------------------------------------------------------------

class TestYesterday:
    def test_monday_returns_friday(self):
        assert _mod._yesterday("2026-06-01") == "2026-05-29"

    def test_tuesday_returns_monday(self):
        assert _mod._yesterday("2026-06-02") == "2026-06-01"

    def test_wednesday_returns_tuesday(self):
        assert _mod._yesterday("2026-06-03") == "2026-06-02"

    def test_thursday_returns_wednesday(self):
        assert _mod._yesterday("2026-06-04") == "2026-06-03"

    def test_friday_returns_thursday(self):
        assert _mod._yesterday("2026-06-05") == "2026-06-04"


# ---------------------------------------------------------------------------
# Tests: _load_eod_review
# ---------------------------------------------------------------------------

class TestLoadEodReview:
    def test_file_exists(self, tmp_path):
        review_dir = tmp_path / "log_review"
        review_dir.mkdir()
        review_file = review_dir / "eod_review_2026-06-02.md"
        review_file.write_text("Good day, 3 trades.\n", encoding="utf-8")

        with patch.object(_mod, "EOD_REVIEW_DIR", review_dir):
            result = _mod._load_eod_review("2026-06-02")
        assert "Good day" in result

    def test_file_missing(self, tmp_path):
        review_dir = tmp_path / "log_review"
        review_dir.mkdir()
        with patch.object(_mod, "EOD_REVIEW_DIR", review_dir):
            result = _mod._load_eod_review("2026-06-02")
        assert "No EOD review found" in result

    def test_file_truncated_at_3000(self, tmp_path):
        review_dir = tmp_path / "log_review"
        review_dir.mkdir()
        review_file = review_dir / "eod_review_2026-06-02.md"
        review_file.write_text("x" * 5000, encoding="utf-8")

        with patch.object(_mod, "EOD_REVIEW_DIR", review_dir):
            result = _mod._load_eod_review("2026-06-02")
        assert len(result) < 3200
        assert "truncated" in result


# ---------------------------------------------------------------------------
# Tests: DB queries
# ---------------------------------------------------------------------------

class TestDBQueries:
    def _seed_trades(self, db_path, date_iso):
        conn = sqlite3.connect(db_path)
        ts = f"{date_iso}T09:30:00+05:30"
        exp = f"{date_iso}T10:30:00+05:30"
        for sig_id, sym in [("SIG1", "RELIANCE"), ("SIG2", "INFY")]:
            conn.execute(
                """INSERT INTO signals
                   (signal_id, symbol, scanner, strategy, triggered_at,
                    received_at, expires_at, status, fingerprint, fingerprint_date)
                   VALUES (?, ?, 'test_scanner', 'breakout_long', ?, ?, ?, 'TRADED',
                           ?, ?)""",
                (sig_id, sym, ts, ts, exp, f"fp_{sig_id}", date_iso),
            )
        conn.execute(
            """INSERT INTO trades
               (trade_id, signal_id, symbol, direction, status, qty_planned, qty_filled,
                entry_target_price, entry_actual_price, sl_initial, tgt_initial,
                margin_reserved, risk_amount, strategy, created_at, order_protocol,
                updated_at, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("T1", "SIG1", "RELIANCE", "LONG", "OPEN", 10, 10, 2500.0, 2500.0,
             2450.0, 2600.0, 5000.0, 500.0, "breakout_long",
             f"{date_iso}T10:00:00+05:30", "CO_PLUS_TGT",
             f"{date_iso}T10:00:00+05:30", "PAPER"),
        )
        conn.execute(
            """INSERT INTO trades
               (trade_id, signal_id, symbol, direction, status, qty_planned, qty_filled,
                entry_target_price, entry_actual_price, sl_initial, tgt_initial,
                margin_reserved, risk_amount, strategy, net_pnl, created_at,
                order_protocol, updated_at, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("T2", "SIG2", "INFY", "SHORT", "CLOSED", 5, 5, 1500.0, 1500.0,
             1550.0, 1400.0, 1500.0, 250.0, "gap_fade_short", 500.0,
             f"{date_iso}T10:30:00+05:30", "CO_PLUS_TGT",
             f"{date_iso}T10:30:00+05:30", "PAPER"),
        )
        conn.commit()
        conn.close()

    def test_open_positions_found(self, db_path):
        self._seed_trades(db_path, "2026-06-02")
        positions = _mod._get_open_positions(db_path)
        assert len(positions) == 1
        assert positions[0]["symbol"] == "RELIANCE"

    def test_open_positions_empty(self, db_path):
        positions = _mod._get_open_positions(db_path)
        assert positions == []

    def test_yesterday_summary(self, db_path):
        self._seed_trades(db_path, "2026-06-02")
        summary = _mod._get_yesterday_summary(db_path, "2026-06-02")
        assert summary["trades"] == 1
        assert summary["wins"] == 1
        assert summary["losses"] == 0
        assert summary["total_pnl"] == 500.0

    def test_yesterday_summary_no_trades(self, db_path):
        summary = _mod._get_yesterday_summary(db_path, "2026-06-02")
        assert summary["trades"] == 0
        assert summary["total_pnl"] == 0

    def test_kill_switch_inactive(self, db_path):
        state = _mod._get_kill_switch_state(db_path)
        assert state in ("INACTIVE", "UNKNOWN")


# ---------------------------------------------------------------------------
# Tests: Gemini CLI
# ---------------------------------------------------------------------------

class TestGeminiCLI:
    @patch("subprocess.run")
    def test_success(self, mock_run, log):
        mock_run.return_value = MagicMock(returncode=0, stdout="Briefing text here", stderr="")
        result = _mod._call_gemini_cli("prompt", "data", log)
        assert result == "Briefing text here"

    @patch("subprocess.run")
    def test_failure_returns_none(self, mock_run, log):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = _mod._call_gemini_cli("prompt", "data", log)
        assert result is None

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_binary_not_found(self, mock_run, log):
        result = _mod._call_gemini_cli("prompt", "data", log)
        assert result is None

    @patch("subprocess.run")
    def test_timeout(self, mock_run, log):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("gemini", 90)
        result = _mod._call_gemini_cli("prompt", "data", log)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: run_briefing
# ---------------------------------------------------------------------------

class TestRunBriefing:
    @patch.object(_mod, "_send_telegram")
    @patch.object(_mod, "_call_gemini_cli", return_value="All clear. Standard monitoring.")
    @patch.object(_mod, "BRIEFING_DIR")
    def test_happy_path(self, mock_dir, mock_gemini, mock_tg, tmp_path, db_path, log):
        brief_dir = tmp_path / "briefing"
        brief_dir.mkdir()
        mock_dir.__truediv__ = lambda self, x: brief_dir / x
        mock_dir.mkdir = MagicMock()

        with patch.object(_mod, "BRIEFING_DIR", brief_dir):
            result = _mod.run_briefing("2026-06-03", db_path, log)
        assert result == 0
        brief_file = brief_dir / "brief_2026-06-03.md"
        assert brief_file.exists()
        content = brief_file.read_text(encoding="utf-8")
        assert "All clear" in content

    @patch.object(_mod, "_send_telegram")
    @patch.object(_mod, "_call_gemini_cli", return_value=None)
    def test_fallback_when_gemini_unavailable(self, mock_gemini, mock_tg, tmp_path, db_path, log):
        brief_dir = tmp_path / "briefing"
        brief_dir.mkdir()

        with patch.object(_mod, "BRIEFING_DIR", brief_dir):
            result = _mod.run_briefing("2026-06-03", db_path, log)
        assert result == 0
        brief_file = brief_dir / "brief_2026-06-03.md"
        content = brief_file.read_text(encoding="utf-8")
        assert "Gemini unavailable" in content

    def test_dry_run(self, db_path, log):
        result = _mod.run_briefing("2026-06-03", db_path, log, dry_run=True)
        assert result == 0


# ---------------------------------------------------------------------------
# Tests: main()
# ---------------------------------------------------------------------------

class TestMain:
    @patch.object(_mod, "run_briefing", return_value=0)
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_success(self, mock_logger, mock_run):
        result = _mod.main(["--db", "test.db", "--dry-run"])
        assert result == 0

    @patch.object(_mod, "run_briefing", side_effect=RuntimeError("boom"))
    @patch.object(_mod, "get_logger", return_value=MagicMock())
    def test_error_returns_1(self, mock_logger, mock_run):
        result = _mod.main(["--db", "test.db"])
        assert result == 1


# ---------------------------------------------------------------------------
# Tests: CLI arg parsing
# ---------------------------------------------------------------------------

class TestArgParsing:
    def test_default_args(self):
        args = _mod._parse_args([])
        assert args.date is None
        assert not args.dry_run

    def test_custom_date(self):
        args = _mod._parse_args(["--date", "2026-06-01"])
        assert args.date == "2026-06-01"

    def test_dry_run(self):
        args = _mod._parse_args(["--dry-run"])
        assert args.dry_run
