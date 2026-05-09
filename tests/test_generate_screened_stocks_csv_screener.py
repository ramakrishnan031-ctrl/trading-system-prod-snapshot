"""
test_generate_screened_stocks_csv_screener.py -- Trading System v2

Additional tests for screener rejection/SKIPPED patterns (Option A fix).

Verifies unified (SYMBOL) format captures screener rejections that were
previously missed due to format inconsistency.
"""
from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

# Import the script module
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from generate_screened_stocks_csv import parse_log_file, generate_csv


# ---------------------------------------------------------------------------
# Test: Screener rejection patterns (previously missed)
# ---------------------------------------------------------------------------

def test_parse_log_screener_rejected():
    """Screener rejected pattern extracts symbols (Option A fix)."""
    with TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "system_2026-05-08.log"
        log_path.write_text(
            '2026-05-08 09:30:00 INFO [signal_processor] Signal sig_001 (MANKIND) screener rejected: REJECTED_SCORE_58\n'
            '2026-05-08 09:31:00 INFO [signal_processor] Signal sig_002 (COFORGE) screener rejected: REJECTED_SCORE_62\n',
            encoding='utf-8'
        )

        traded, non_traded = parse_log_file(log_path)

        assert traded == []
        assert len(non_traded) == 2
        assert non_traded[0][0] == "MANKIND"
        assert "Score too low (58/100)" in non_traded[0][1]
        assert non_traded[1][0] == "COFORGE"
        assert "Score too low (62/100)" in non_traded[1][1]


def test_parse_log_screener_skipped():
    """Screener SKIPPED pattern extracts symbols (Option A fix)."""
    with TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "system_2026-05-08.log"
        log_path.write_text(
            '2026-05-08 09:30:00 WARNING [signal_processor] Signal sig_001 (SIGMAADV) screener SKIPPED: SKIPPED_QUOTE_UNAVAILABLE\n'
            '2026-05-08 09:31:00 WARNING [signal_processor] Signal sig_002 (TVSSCS) screener SKIPPED: SKIPPED_EXECUTOR_ERROR\n',
            encoding='utf-8'
        )

        traded, non_traded = parse_log_file(log_path)

        assert traded == []
        assert len(non_traded) == 2
        assert non_traded[0][0] == "SIGMAADV"
        assert "Quote unavailable" in non_traded[0][1]
        assert non_traded[1][0] == "TVSSCS"
        assert "Screener executor error" in non_traded[1][1]


def test_parse_log_mixed_all_types():
    """Mixed log with pipeline, screener, and skipped rejections."""
    with TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "system_2026-05-08.log"
        log_path.write_text(
            "2026-05-08 09:30:00 INFO [telegram_notifier] [PAPER] ✅ ORDER PLACED — ABLBL\n"
            '2026-05-08 09:31:00 INFO [signal_processor] Signal sig_001 (WESTLIFE) rejected at DUPLICATE_SYMBOL: already in portfolio\n'  # Pipeline
            '2026-05-08 09:32:00 INFO [signal_processor] Signal sig_002 (MANKIND) screener rejected: REJECTED_SCORE_58\n'  # Screener
            '2026-05-08 09:33:00 WARNING [signal_processor] Signal sig_003 (SIGMAADV) screener SKIPPED: SKIPPED_QUOTE_UNAVAILABLE\n'  # Skipped
            "2026-05-08 09:34:00 INFO [telegram_notifier] [PAPER] ✅ ORDER PLACED — AEROFLEX\n",
            encoding='utf-8'
        )

        traded, non_traded = parse_log_file(log_path)

        # Traded symbols
        assert traded == ["ABLBL", "AEROFLEX"]

        # Non-traded symbols (all rejection types)
        assert len(non_traded) == 3
        symbols = [nt[0] for nt in non_traded]
        assert "WESTLIFE" in symbols  # Pipeline rejection
        assert "MANKIND" in symbols   # Screener rejection
        assert "SIGMAADV" in symbols  # Screener skipped


def test_parse_log_gate_rejection():
    """Gate rejection pattern extracts symbols (Option A fix)."""
    with TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "system_2026-05-08.log"
        log_path.write_text(
            '2026-05-08 09:30:00 INFO [signal_processor] Gate signal sig_001 (BANDHANBNK) rejected at KILL_SWITCH: kill switch active\n',
            encoding='utf-8'
        )

        traded, non_traded = parse_log_file(log_path)

        assert traded == []
        assert len(non_traded) == 1
        assert non_traded[0][0] == "BANDHANBNK"
        assert "Kill switch" in non_traded[0][1]


def test_full_workflow_all_rejection_types():
    """End-to-end test with all rejection types → CSV."""
    with TemporaryDirectory() as tmpdir:
        # Create sample log
        log_path = Path(tmpdir) / "system_2026-05-08.log"
        log_path.write_text(
            "2026-05-08 09:30:00 INFO [telegram_notifier] [PAPER] ✅ ORDER PLACED — ABLBL\n"
            "2026-05-08 09:31:00 INFO [telegram_notifier] [PAPER] ✅ ORDER PLACED — AEROFLEX\n"
            '2026-05-08 09:32:00 INFO [signal_processor] Signal sig_001 (WESTLIFE) rejected at SCORE_48: too low\n'  # Pipeline
            '2026-05-08 09:33:00 INFO [signal_processor] Signal sig_002 (MANKIND) screener rejected: REJECTED_SCORE_58\n'  # Screener
            '2026-05-08 09:34:00 WARNING [signal_processor] Signal sig_003 (SIGMAADV) screener SKIPPED: SKIPPED_QUOTE_UNAVAILABLE\n'  # Skipped
            '2026-05-08 09:35:00 INFO [signal_processor] Signal sig_004 (TVSSCS) rejected at OUTSIDE_ENTRY_WINDOW: after 14:30\n',  # Pipeline
            encoding='utf-8'
        )

        # Parse log
        traded, non_traded = parse_log_file(log_path)

        # Generate CSV
        output_dir = Path(tmpdir) / "reports" / "daily_review"
        csv_path = generate_csv("2026-05-08", traded, non_traded, output_dir)

        # Verify CSV
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Header
        assert rows[0] == ["TRADED", "NON_TRADED", "REJECTION_REASON"]

        # Verify all rejection types appear
        non_traded_symbols = [row[1] for row in rows[1:] if row[1]]
        assert "WESTLIFE" in non_traded_symbols   # Pipeline rejection
        assert "MANKIND" in non_traded_symbols    # Screener rejection
        assert "SIGMAADV" in non_traded_symbols   # Screener SKIPPED
        assert "TVSSCS" in non_traded_symbols     # Pipeline rejection

        # Verify rejection reasons are expanded
        reasons = [row[2] for row in rows[1:] if row[2]]
        assert any("Score too low (48/100)" in r for r in reasons)
        assert any("Score too low (58/100)" in r for r in reasons)
        assert any("Quote unavailable" in r for r in reasons)
        assert any("Outside entry window" in r for r in reasons)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
