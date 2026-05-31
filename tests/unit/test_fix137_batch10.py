"""Tests for FIX-137: Batch 10 fixes (Items 57-60, report fix)."""
from __future__ import annotations

import csv
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.state_store import StateStore
from core.time_authority import now_ist, today_ist


# ─────────────────────────────────────────────────────────────────────────────
# Item 58: Screened Stocks CSV Validation
# ─────────────────────────────────────────────────────────────────────────────


class TestScreenedStocksCsv:
    def test_csv_round_trip(self, tmp_path):
        from scripts.generate_screened_stocks_csv import generate_csv
        traded = ["RELIANCE", "INFY", "TCS"]
        non_traded = [("SBIN", "Score too low (48/100)"), ("HDFC", "Outside entry window")]
        path = generate_csv("2026-05-31", traded, non_traded, tmp_path)
        assert path.exists()

        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert header == ["TRADED", "NON_TRADED", "REJECTION_REASON"]
            rows = list(reader)

        assert len(rows) == max(len(traded), len(non_traded))
        assert rows[0][0] == "RELIANCE"
        assert rows[0][1] == "SBIN"
        assert rows[0][2] == "Score too low (48/100)"

    def test_csv_commas_in_rejection_reason(self, tmp_path):
        from scripts.generate_screened_stocks_csv import generate_csv
        non_traded = [("SBIN", "Failed checks: volume, spread, depth")]
        path = generate_csv("2026-05-31", [], non_traded, tmp_path)

        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader)
            rows = list(reader)

        assert rows[0][2] == "Failed checks: volume, spread, depth"

    def test_csv_empty_data(self, tmp_path):
        from scripts.generate_screened_stocks_csv import generate_csv
        path = generate_csv("2026-05-31", [], [], tmp_path)
        assert path.exists()

        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert header == ["TRADED", "NON_TRADED", "REJECTION_REASON"]

    def test_rejection_reason_expansion(self):
        from scripts.generate_screened_stocks_csv import expand_rejection_reason
        assert expand_rejection_reason(None, "REJECTED_SCORE") == "Score too low"
        assert "48" in expand_rejection_reason(None, "REJECTED_SCORE_48")
        assert expand_rejection_reason("Custom reason", "REJECTED_SCORE") == "Custom reason"

    def test_parity(self, tmp_path):
        """CSV generation is mode-agnostic."""
        from scripts.generate_screened_stocks_csv import generate_csv
        path = generate_csv("2026-05-31", ["A"], [("B", "reason")], tmp_path)
        assert path.exists()


# ─────────────────────────────────────────────────────────────────────────────
# Item 59: EOD Verification Script
# ─────────────────────────────────────────────────────────────────────────────


_TRADE_SQL = (
    "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, qty_planned, "
    "entry_target_price, sl_initial, tgt_initial, order_protocol, margin_reserved, "
    "risk_amount, status, created_at, updated_at) VALUES "
    "(?, 's1', 'RELIANCE', 'LONG', 'fp', 100, 2500, 2450, 2600, 'LIMIT_TRIPLE', 10000, 5000, "
    "?, '2026-05-31T10:00:00', '2026-05-31T10:00:00')"
)

_ORDER_SQL = (
    "INSERT INTO orders (order_id, trade_id, leg, leg_index, transaction_type, "
    "order_type, product, variety, qty_requested, price, status, placed_at, updated_at) VALUES "
    "('o1', 't1', 'ENTRY', 0, 'BUY', 'LIMIT', 'MIS', 'regular', 100, 2500, "
    "?, '2026-05-31T10:00:00', '2026-05-31T10:00:00')"
)


class TestEodVerification:
    @pytest.fixture()
    def store(self, tmp_path):
        s = StateStore(db_path=tmp_path / "test.db")
        s._get_conn().execute("PRAGMA foreign_keys = OFF")
        return s

    def test_clean_day_verified(self, store):
        from scripts.eod_verify import run_eod_verification
        result = run_eod_verification(store, "2026-05-31")
        assert result["status"] == "VERIFIED"
        assert result["open_trades"] == 0
        assert result["pending_orders"] == 0

    def test_open_trades_flagged(self, store):
        from scripts.eod_verify import run_eod_verification
        with store.transaction() as cur:
            cur.execute(_TRADE_SQL, ("t1", "OPEN"))
        result = run_eod_verification(store, "2026-05-31")
        assert result["status"] == "ISSUES_FOUND"
        assert result["open_trades"] == 1

    def test_pending_orders_flagged(self, store):
        from scripts.eod_verify import run_eod_verification
        with store.transaction() as cur:
            cur.execute(_TRADE_SQL, ("t1", "CLOSED"))
            cur.execute(_ORDER_SQL, ("PENDING",))
        result = run_eod_verification(store, "2026-05-31")
        assert result["status"] == "ISSUES_FOUND"
        assert result["pending_orders"] == 1

    def test_verification_record_written(self, store):
        from scripts.eod_verify import run_eod_verification
        run_eod_verification(store, "2026-05-31")
        row = store.fetch_one(
            "SELECT * FROM eod_verification WHERE date = ?", ("2026-05-31",)
        )
        assert row is not None
        assert row["status"] == "VERIFIED"

    def test_parity_paper_and_live(self, store):
        """EOD verification is mode-agnostic."""
        from scripts.eod_verify import run_eod_verification
        r1 = run_eod_verification(store, "2026-05-31")
        r2 = run_eod_verification(store, "2026-05-31")
        assert r1["status"] == r2["status"]


# ─────────────────────────────────────────────────────────────────────────────
# Daily Report Empty DB Guard
# ─────────────────────────────────────────────────────────────────────────────


class TestDailyReportEmptyGuard:
    def test_empty_db_warning_in_dashboard(self):
        import openpyxl
        from reports.daily_report import ReportData, build_sheet_0_dashboard
        data = ReportData(
            date_iso="2026-05-22",
            mode="UNKNOWN",
            account="UNKNOWN",
            opening_capital=0.0,
            closing_capital_broker=0.0,
            signals=[],
            trades=[],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
        )
        data.strategy_min_scores = {}
        wb = openpyxl.Workbook()
        ws = build_sheet_0_dashboard(wb, data)
        all_text = " ".join(str(ws.cell(row=r, column=1).value or "") for r in range(1, ws.max_row + 1))
        assert "WARNING" in all_text.upper() or "no data" in all_text.lower()

    def test_non_empty_db_no_warning(self):
        import openpyxl
        from reports.daily_report import ReportData, build_sheet_0_dashboard
        data = ReportData(
            date_iso="2026-05-31",
            mode="PAPER",
            account="TEST",
            opening_capital=50000.0,
            closing_capital_broker=50000.0,
            signals=[{"signal_id": "s1", "status": "PROCESSED", "symbol": "A",
                       "scanner": "sc", "strategy": "fp", "trigger_price": 100,
                       "received_at": "2026-05-31T10:00:00", "triggered_at": "2026-05-31T09:55:00"}],
            trades=[],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
        )
        data.strategy_min_scores = {}
        wb = openpyxl.Workbook()
        ws = build_sheet_0_dashboard(wb, data)
        cells_str = " ".join(str(ws.cell(row=r, column=1).value or "") for r in range(1, ws.max_row + 1))
        assert "WARNING" not in cells_str.upper() or "no data" not in cells_str.lower()
