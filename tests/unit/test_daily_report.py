"""
tests/unit/test_daily_report.py — Trading System v2

Unit tests for reports/daily_report.py (7-sheet xlsx generator).
Target: 20+ tests covering all sheet builders and helpers.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from reports.daily_report import (
    ReportData,
    _fmt_time,
    _fmt_datetime,
    _calc_slip_pct,
    _generate_tune_suggestions,
    _get_order_for_trade_leg,
    is_holiday_or_weekend,
    load_report_data,
    generate_daily_report,
    build_sheet_0_dashboard,
    build_sheet_1_signals,
    build_sheet_2_orders,
    build_sheet_3_capital,
    build_sheet_4_candles,
    build_sheet_5_telegram,
    build_sheet_6_strategy,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_trade():
    """A sample closed trade dict."""
    return {
        "trade_id": "trade-001",
        "signal_id": "sig-001",
        "symbol": "RELIANCE",
        "direction": "LONG",
        "strategy": "MOMENTUM",
        "qty_planned": 10,
        "qty_filled": 10,
        "entry_target_price": 2500.0,
        "entry_actual_price": 2502.0,
        "sl_initial": 2450.0,
        "tgt_initial": 2600.0,
        "margin_reserved": 5000.0,
        "created_at": "2026-05-15T09:30:00+05:30",
        "entry_time": "2026-05-15T09:31:00+05:30",
        "exit_time": "2026-05-15T11:00:00+05:30",
        "exit_price": 2600.0,
        "exit_reason": "TGT_HIT",
        "gross_pnl": 980.0,
        "charges": 50.0,
        "net_pnl": 930.0,
        "status": "CLOSED",
    }


@pytest.fixture
def sample_signal():
    """A sample signal dict."""
    return {
        "signal_id": "sig-001",
        "symbol": "RELIANCE",
        "scanner": "MOMENTUM_BREAK",
        "strategy": "MOMENTUM",
        "triggered_at": "2026-05-15T09:29:00+05:30",
        "received_at": "2026-05-15T09:29:30+05:30",
        "status": "TRADED",
        "trade_id": "trade-001",
        "trigger_price": 2498.0,
    }


@pytest.fixture
def sample_order():
    """A sample order dict."""
    return {
        "order_id": "broker-12345",
        "trade_id": "trade-001",
        "leg": "ENTRY",
        "transaction_type": "BUY",
        "order_type": "LIMIT",
        "product": "MIS",
        "variety": "regular",
        "qty_requested": 10,
        "price": 2500.0,
        "trigger_price": None,
        "status": "COMPLETE",
        "qty_filled": 10,
        "avg_fill_price": 2502.0,
        "placed_at": "2026-05-15T09:30:00+05:30",
    }


@pytest.fixture
def sample_report_data(sample_trade, sample_signal, sample_order):
    """Complete ReportData for testing sheet builders."""
    return ReportData(
        date_iso="2026-05-15",
        mode="PAPER",
        account="TEST001",
        opening_capital=100000.0,
        closing_capital_broker=100930.0,
        signals=[sample_signal],
        trades=[sample_trade],
        orders=[sample_order],
        fm_ledger=[
            {
                "ledger_id": 1,
                "ts": "2026-05-15T09:00:00+05:30",
                "entry_type": "INIT",
                "amount": 100000.0,
                "balance_before": 0.0,
                "balance_after": 100000.0,
            }
        ],
        screener_results=[
            {
                "signal_id": "sig-001",
                "score": 75,
                "tier": "HIGH",
                "status": "PASSED",
                "step_results": "{}",
                "latencies": "{}",
                "ts": "2026-05-15T09:29:35+05:30",
            }
        ],
        innings=[],
        system_events=[
            {
                "event_id": 1,
                "timestamp": "2026-05-15T09:15:00+05:30",
                "event_type": "STARTUP",
                "scenario": "COLD",
            }
        ],
        recon_log=[],
        gate_state=[],
        config={
            "system": {"excluded_symbols": ["E2E"]},
            "scoring": {"min_pass_score": 60},
        },
        excluded_symbols=["E2E"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper function tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatHelpers:
    """Tests for formatting helper functions."""

    def test_fmt_time_valid_iso(self):
        result = _fmt_time("2026-05-15T09:30:45+05:30")
        assert result == "09:30:45"

    def test_fmt_time_empty(self):
        assert _fmt_time("") == ""
        assert _fmt_time(None) == ""

    def test_fmt_time_invalid(self):
        result = _fmt_time("not-a-timestamp")
        assert result == "not-a-ti"

    def test_fmt_datetime_valid_iso(self):
        result = _fmt_datetime("2026-05-15T09:30:45+05:30")
        assert result == "2026-05-15 09:30:45"

    def test_fmt_datetime_empty(self):
        assert _fmt_datetime("") == ""
        assert _fmt_datetime(None) == ""

    def test_calc_slip_pct_positive(self):
        pct = _calc_slip_pct(100.0, 102.0)
        assert pct == 2.0

    def test_calc_slip_pct_zero_base(self):
        assert _calc_slip_pct(0.0, 102.0) == 0.0


class TestOrderLookup:
    """Tests for order lookup helper."""

    def test_get_order_for_trade_leg_found(self, sample_order):
        orders = [sample_order]
        result = _get_order_for_trade_leg(orders, "trade-001", "ENTRY")
        assert result is not None
        assert result["order_id"] == "broker-12345"

    def test_get_order_for_trade_leg_not_found(self, sample_order):
        orders = [sample_order]
        result = _get_order_for_trade_leg(orders, "trade-001", "SL")
        assert result is None

    def test_get_order_for_trade_leg_wrong_trade(self, sample_order):
        orders = [sample_order]
        result = _get_order_for_trade_leg(orders, "trade-999", "ENTRY")
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Holiday/weekend tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHolidayCheck:
    """Tests for holiday/weekend checking."""

    def test_weekend_saturday(self, tmp_path):
        # 2026-05-16 is Saturday
        assert is_holiday_or_weekend("2026-05-16", tmp_path) is True

    def test_weekend_sunday(self, tmp_path):
        # 2026-05-17 is Sunday
        assert is_holiday_or_weekend("2026-05-17", tmp_path) is True

    def test_weekday_no_holiday_file(self, tmp_path):
        # 2026-05-15 is Friday
        assert is_holiday_or_weekend("2026-05-15", tmp_path) is False

    def test_weekday_with_holiday(self, tmp_path):
        import yaml
        holiday_file = tmp_path / "nse_holidays_2026.yaml"
        holiday_file.write_text(yaml.dump({"holidays": ["2026-05-15"]}))

        assert is_holiday_or_weekend("2026-05-15", tmp_path) is True

    def test_weekday_not_in_holiday_list(self, tmp_path):
        import yaml
        holiday_file = tmp_path / "nse_holidays_2026.yaml"
        holiday_file.write_text(yaml.dump({"holidays": ["2026-01-26"]}))

        assert is_holiday_or_weekend("2026-05-15", tmp_path) is False


# ─────────────────────────────────────────────────────────────────────────────
# Tune suggestion tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTuneSuggestions:
    """Tests for auto-tune suggestion generation."""

    def test_tgt_hit_generates_positive_suggestion(self, sample_report_data):
        suggestions = _generate_tune_suggestions(sample_report_data)
        assert any("TGT hit perfectly" in s for s in suggestions)

    def test_sl_hit_adverse_move_suggests_widen(self):
        data = ReportData(
            date_iso="2026-05-15",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            closing_capital_broker=99000.0,
            signals=[],
            trades=[{
                "trade_id": "t1",
                "symbol": "INFY",
                "direction": "LONG",
                "sl_initial": 1500.0,
                "tgt_initial": 1600.0,
                "entry_actual_price": 1550.0,
                "exit_price": 1492.0,  # 0.5% below SL
                "exit_reason": "SL_HIT",
                "status": "CLOSED",
                "net_pnl": -580.0,
                "gross_pnl": -580.0,
                "strategy": "TEST",
            }],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            config={},
            excluded_symbols=[],
        )
        suggestions = _generate_tune_suggestions(data)
        assert any("widening SL" in s for s in suggestions)

    def test_high_entry_slippage_generates_critical(self):
        data = ReportData(
            date_iso="2026-05-15",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            closing_capital_broker=99000.0,
            signals=[],
            trades=[{
                "trade_id": "t1",
                "symbol": "TCS",
                "direction": "LONG",
                "entry_target_price": 3500.0,
                "entry_actual_price": 3580.0,  # 2.3% slip
                "sl_initial": 3400.0,
                "tgt_initial": 3600.0,
                "exit_price": 3400.0,
                "exit_reason": "SL_HIT",
                "status": "CLOSED",
                "net_pnl": -200.0,
                "gross_pnl": -200.0,
                "strategy": "TEST",
            }],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            config={},
            excluded_symbols=[],
        )
        suggestions = _generate_tune_suggestions(data)
        assert any("CRITICAL" in s and "slippage" in s for s in suggestions)

    def test_general_summary_always_generated(self, sample_report_data):
        suggestions = _generate_tune_suggestions(sample_report_data)
        assert any("GENERAL:" in s for s in suggestions)


# ─────────────────────────────────────────────────────────────────────────────
# Sheet builder tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSheetBuilders:
    """Tests for individual sheet builder functions."""

    def test_build_sheet_0_dashboard_creates_sheet(self, sample_report_data):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_0_dashboard(wb, sample_report_data)

        assert ws.title == "0_EOD_Dashboard"
        assert "EOD Dashboard" in str(ws.cell(row=1, column=1).value)

    def test_build_sheet_0_includes_all_sections(self, sample_report_data):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_0_dashboard(wb, sample_report_data)

        cell_values = [ws.cell(row=r, column=1).value for r in range(1, 50)]
        text = " ".join(str(v) for v in cell_values if v)

        assert "Day Overview" in text
        assert "Signal Funnel" in text
        assert "P&L Summary" in text
        assert "System Health" in text
        assert "Tuning" in text

    def test_build_sheet_1_signals_creates_sheet(self, sample_report_data):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_1_signals(wb, sample_report_data)

        assert ws.title == "1_Signals"
        assert ws.cell(row=1, column=1).value == "Trading Date"

    def test_build_sheet_1_signals_includes_recon_columns(self, sample_report_data):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_1_signals(wb, sample_report_data)

        headers = [ws.cell(row=1, column=c).value for c in range(1, 23)]
        assert "Delta" in headers
        assert "Total Rcvd" in headers

    def test_build_sheet_2_orders_creates_sheet(self, sample_report_data):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_2_orders(wb, sample_report_data)

        assert ws.title == "2_Orders"

    def test_build_sheet_3_capital_has_opening_row(self, sample_report_data):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_3_capital(wb, sample_report_data)

        assert ws.title == "3_Capital"
        assert ws.cell(row=2, column=3).value == "Opening"

    def test_build_sheet_3_capital_has_reconciliation(self, sample_report_data):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_3_capital(wb, sample_report_data)

        all_values = []
        for row in ws.iter_rows(min_row=1, max_row=30, max_col=2, values_only=True):
            all_values.extend([str(v) for v in row if v])

        text = " ".join(all_values)
        assert "RECONCILIATION" in text

    def test_build_sheet_4_candles_creates_sheet(self, sample_report_data):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_4_candles(wb, sample_report_data)

        assert ws.title == "4_Candles"

    def test_build_sheet_5_telegram_creates_sheet(self, sample_report_data):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_5_telegram(wb, sample_report_data)

        assert ws.title == "5_Telegram"

    def test_build_sheet_6_strategy_has_two_tables(self, sample_report_data):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_6_strategy(wb, sample_report_data)

        assert ws.title == "6_Strategy_Analysis"

        all_values = []
        for row in ws.iter_rows(min_row=1, max_row=30, max_col=1, values_only=True):
            all_values.extend([str(v) for v in row if v])

        text = " ".join(all_values)
        assert "STRATEGY-WISE" in text
        assert "TIME-OF-DAY" in text


# ─────────────────────────────────────────────────────────────────────────────
# Integration test
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateReport:
    """Integration test for full report generation."""

    def test_generate_daily_report_creates_file(self, sample_report_data, tmp_path):
        mock_store = MagicMock()
        mock_store.get_signals_for_date.return_value = sample_report_data.signals
        mock_store.get_trades_for_date.return_value = sample_report_data.trades
        mock_store.get_orders_for_date.return_value = sample_report_data.orders
        mock_store.get_fm_ledger_for_date.return_value = sample_report_data.fm_ledger
        mock_store.get_screener_results_for_date.return_value = sample_report_data.screener_results
        mock_store.get_innings_for_date.return_value = []
        mock_store.get_system_events_for_date.return_value = sample_report_data.system_events
        mock_store.get_reconciliation_log_for_date.return_value = []
        mock_store.get_all_gate_state.return_value = []
        mock_store.get_session_row.return_value = {
            "mode": "PAPER",
            "account_id": "TEST001",
        }

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "system_config.yaml").write_text("excluded_symbols: []\n")
        (config_dir / "scoring_weights.yaml").write_text("min_pass_score: 60\n")

        output_dir = tmp_path / "output"

        result = generate_daily_report(
            store=mock_store,
            date_iso="2026-05-15",
            output_dir=output_dir,
            config_dir=config_dir,
        )

        assert result.exists()
        assert result.name == "daily_report_2026-05-15.xlsx"

    def test_generate_daily_report_has_all_sheets(self, sample_report_data, tmp_path):
        import openpyxl

        mock_store = MagicMock()
        mock_store.get_signals_for_date.return_value = sample_report_data.signals
        mock_store.get_trades_for_date.return_value = sample_report_data.trades
        mock_store.get_orders_for_date.return_value = sample_report_data.orders
        mock_store.get_fm_ledger_for_date.return_value = sample_report_data.fm_ledger
        mock_store.get_screener_results_for_date.return_value = sample_report_data.screener_results
        mock_store.get_innings_for_date.return_value = []
        mock_store.get_system_events_for_date.return_value = sample_report_data.system_events
        mock_store.get_reconciliation_log_for_date.return_value = []
        mock_store.get_all_gate_state.return_value = []
        mock_store.get_session_row.return_value = {
            "mode": "PAPER",
            "account_id": "TEST001",
        }

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "system_config.yaml").write_text("excluded_symbols: []\n")
        (config_dir / "scoring_weights.yaml").write_text("min_pass_score: 60\n")

        output_dir = tmp_path / "output"

        result = generate_daily_report(
            store=mock_store,
            date_iso="2026-05-15",
            output_dir=output_dir,
            config_dir=config_dir,
        )

        wb = openpyxl.load_workbook(result)
        sheet_names = wb.sheetnames

        assert "0_EOD_Dashboard" in sheet_names
        assert "1_Signals" in sheet_names
        assert "2_Orders" in sheet_names
        assert "3_Capital" in sheet_names
        assert "4_Candles" in sheet_names
        assert "5_Telegram" in sheet_names
        assert "6_Strategy_Analysis" in sheet_names
        assert len(sheet_names) == 7


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Edge case tests."""

    def test_empty_data_generates_report(self, tmp_path):
        import openpyxl

        data = ReportData(
            date_iso="2026-05-15",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            closing_capital_broker=100000.0,
            signals=[],
            trades=[],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            config={},
            excluded_symbols=[],
        )

        wb = openpyxl.Workbook()
        wb.remove(wb.active)

        build_sheet_0_dashboard(wb, data)
        build_sheet_1_signals(wb, data)
        build_sheet_2_orders(wb, data)
        build_sheet_3_capital(wb, data)
        build_sheet_4_candles(wb, data)
        build_sheet_5_telegram(wb, data)
        build_sheet_6_strategy(wb, data)

        output_path = tmp_path / "test_report.xlsx"
        wb.save(output_path)

        assert output_path.exists()

    def test_missing_optional_fields_handled(self):
        trade = {
            "trade_id": "t1",
            "symbol": "TEST",
            "status": "CLOSED",
        }
        data = ReportData(
            date_iso="2026-05-15",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            closing_capital_broker=100000.0,
            signals=[],
            trades=[trade],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            config={},
            excluded_symbols=[],
        )

        suggestions = _generate_tune_suggestions(data)
        assert isinstance(suggestions, list)
