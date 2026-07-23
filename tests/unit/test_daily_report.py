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
    _is_critical_event,
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
        excluded_symbols=["E2E"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper function tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCriticalEventClassification:
    """C1: routine auto-cleared events must not be counted as CRITICAL, without silencing
    a genuine incident (anti-vacuity). Keyed on the structured event_type, not free text."""

    def test_routine_auto_clear_is_not_critical(self):
        # KILL_AUTO_CLEARED fires every trading day (the prior-day kill auto-clear at boot).
        # Pre-fix it was the ONLY event ever matched, so "CRITICAL Count" read >= 1 daily.
        assert _is_critical_event("KILL_AUTO_CLEARED") is False

    def test_genuine_critical_and_kill_still_count(self):
        # ANTI-VACUITY: the routine exclusion must NOT swallow a real incident. A genuine
        # KILL/CRITICAL event_type (should one ever be written) must still be counted.
        assert _is_critical_event("HARD_KILL_TRIGGERED") is True
        assert _is_critical_event("SOFT_KILL_ACTIVATED") is True
        assert _is_critical_event("CRITICAL_FAILURE") is True

    def test_non_incident_events_ignored(self):
        for et in ("STARTUP", "SHUTDOWN", "CONFIG_DIFF", "", None):
            assert _is_critical_event(et) is False


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

    def test_weekday_with_holiday_dict_format(self, tmp_path):
        """M-R1: production lists holidays as DICTS ({date:, name:}); the old inline
        `date_iso in holidays` missed these entirely, so the report ran on real NSE
        holidays. Fails against the pre-fix code, passes against the holiday_guard delegation."""
        import yaml
        holiday_file = tmp_path / "nse_holidays_2026.yaml"
        holiday_file.write_text(yaml.dump(
            {"holidays": [{"date": "2026-05-15", "name": "Test Holiday"}]}))
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

    def test_dashboard_net_pnl_includes_closed_manual(self, sample_report_data, sample_trade):
        """M-R2: a CLOSED_MANUAL trade (EOD / manual close) is counted in Net P&L, not dropped.
        Pre-fix the dashboard filtered status=='CLOSED' only, understating P&L on close days."""
        import openpyxl
        import dataclasses

        def _net_pnl(trades):
            data = dataclasses.replace(sample_report_data, trades=trades)
            ws = build_sheet_0_dashboard(openpyxl.Workbook(), data)
            for row in ws.iter_rows():
                for c in row:
                    if c.value == "Net P&L":
                        raw = ws.cell(row=c.row, column=c.column + 1).value
                        return float(str(raw).replace("₹", "").replace(",", ""))
            raise AssertionError("Net P&L row not found on the dashboard")

        manual = dict(sample_trade, trade_id="trade-manual",
                      status="CLOSED_MANUAL", net_pnl=200.0)
        base = _net_pnl([sample_trade])                 # 930.0 (CLOSED only)
        with_manual = _net_pnl([sample_trade, manual])  # 930.0 + 200.0
        assert with_manual == base + 200.0, (base, with_manual)

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

    def test_build_sheet_3_capital_shows_honest_capital_summary(self, sample_report_data):
        """The old '[3_Capital]' reconciliation reported 'Closing Capital - Broker' from the last
        fm_ledger balance_after (the RESET_PNL 0.0 on a normal day), so the Reconcile Status read
        REVIEW on ~14 of 15 days against a broker figure that was never captured. It is now an
        honest system-side capital summary: no broker fiction, no permanently-REVIEW status."""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_3_capital(wb, sample_report_data)

        all_values = []
        for row in ws.iter_rows(min_row=1, max_row=30, max_col=2, values_only=True):
            all_values.extend([str(v) for v in row if v])

        text = " ".join(all_values)
        assert "CAPITAL SUMMARY" in text
        assert "Opening Capital" in text
        assert "Closing Capital" in text
        assert "Net P&L (Realized)" in text
        # the broker misnomer and its uninformative daily-REVIEW status are gone
        assert "Broker" not in text
        assert "Reconcile Status" not in text
        assert "Reconcile Variance" not in text

    def test_build_sheet_3_capital_strategy_fallback(self, sample_report_data):
        """Strategy column should fallback to signal.strategy if trade.strategy is empty."""
        import openpyxl

        # Create a trade with empty strategy
        trade_no_strategy = {
            "trade_id": "trade-002",
            "signal_id": "sig-001",  # Same signal as sample_signal which has strategy="MOMENTUM"
            "symbol": "TCS",
            "direction": "LONG",
            "strategy": "",  # Empty strategy
            "qty_planned": 5,
            "qty_filled": 5,
            "entry_target_price": 3500.0,
            "entry_actual_price": 3505.0,
            "sl_initial": 3450.0,
            "tgt_initial": 3600.0,
            "margin_reserved": 3500.0,
            "created_at": "2026-05-15T09:35:00+05:30",
            "entry_time": "2026-05-15T09:36:00+05:30",
            "exit_time": "2026-05-15T10:00:00+05:30",
            "exit_price": 3600.0,
            "exit_reason": "TGT_HIT",
            "gross_pnl": 475.0,
            "charges": 25.0,
            "net_pnl": 450.0,
            "status": "CLOSED",
        }

        data_with_empty_strategy = ReportData(
            date_iso=sample_report_data.date_iso,
            mode=sample_report_data.mode,
            account=sample_report_data.account,
            opening_capital=sample_report_data.opening_capital,
            signals=sample_report_data.signals,  # Contains sig-001 with strategy="MOMENTUM"
            trades=[trade_no_strategy],
            orders=sample_report_data.orders,
            fm_ledger=sample_report_data.fm_ledger,
            screener_results=sample_report_data.screener_results,
            innings=sample_report_data.innings,
            system_events=sample_report_data.system_events,
            recon_log=sample_report_data.recon_log,
            gate_state=sample_report_data.gate_state,
            excluded_symbols=sample_report_data.excluded_symbols,
        )

        wb = openpyxl.Workbook()
        ws = build_sheet_3_capital(wb, data_with_empty_strategy)

        # Check that row 3 (first trade row after opening) has strategy from signal
        # Column 4 is Strategy
        strategy_value = ws.cell(row=3, column=4).value
        assert strategy_value == "MOMENTUM", f"Expected 'MOMENTUM' from signal fallback, got {strategy_value!r}"

    def test_build_sheet_2_orders_strategy_fallback_to_signal(self, sample_report_data):
        """Strategy col (C) in Sheet 2_Orders should fallback to signal.strategy when trade.strategy is empty."""
        import openpyxl

        trade_no_strategy = {
            "trade_id": "trade-004",
            "signal_id": "sig-001",  # Signal has strategy="MOMENTUM"
            "symbol": "TCS",
            "direction": "LONG",
            "strategy": "",
            "qty_planned": 5,
            "qty_filled": 5,
            "entry_target_price": 3500.0,
            "entry_actual_price": 3505.0,
            "sl_initial": 3450.0,
            "tgt_initial": 3600.0,
            "margin_reserved": 3500.0,
            "created_at": "2026-05-15T09:35:00+05:30",
            "entry_time": "2026-05-15T09:36:00+05:30",
            "exit_time": "2026-05-15T10:00:00+05:30",
            "exit_price": 3600.0,
            "exit_reason": "TGT_HIT",
            "gross_pnl": 475.0,
            "charges": 25.0,
            "net_pnl": 450.0,
            "status": "CLOSED",
        }

        data = ReportData(
            date_iso=sample_report_data.date_iso,
            mode=sample_report_data.mode,
            account=sample_report_data.account,
            opening_capital=sample_report_data.opening_capital,
            signals=sample_report_data.signals,
            trades=[trade_no_strategy],
            orders=sample_report_data.orders,
            fm_ledger=sample_report_data.fm_ledger,
            screener_results=sample_report_data.screener_results,
            innings=sample_report_data.innings,
            system_events=sample_report_data.system_events,
            recon_log=sample_report_data.recon_log,
            gate_state=sample_report_data.gate_state,
            excluded_symbols=sample_report_data.excluded_symbols,
        )

        wb = openpyxl.Workbook()
        ws = build_sheet_2_orders(wb, data)

        # Row 4 = first trade row (rows 1-3 are headers); col 3 = Strategy
        strategy_value = ws.cell(row=4, column=3).value
        assert strategy_value == "MOMENTUM", f"Expected 'MOMENTUM' from signal fallback, got {strategy_value!r}"

    def test_build_sheet_4_candles_strategy_fallback_to_signal(self, sample_report_data):
        """Strategy col (C) in Sheet 4_Candles should fallback to signal.strategy when trade.strategy is empty."""
        import openpyxl

        trade_no_strategy = {
            "trade_id": "trade-005",
            "signal_id": "sig-001",  # Signal has strategy="MOMENTUM"
            "symbol": "INFY",
            "direction": "LONG",
            "strategy": "",
            "qty_planned": 8,
            "qty_filled": 8,
            "entry_target_price": 1500.0,
            "entry_actual_price": 1502.0,
            "sl_initial": 1470.0,
            "tgt_initial": 1560.0,
            "margin_reserved": 2400.0,
            "created_at": "2026-05-15T10:00:00+05:30",
            "entry_time": "2026-05-15T10:01:00+05:30",
            "exit_time": "2026-05-15T11:30:00+05:30",
            "exit_price": 1560.0,
            "exit_reason": "TGT_HIT",
            "gross_pnl": 464.0,
            "charges": 24.0,
            "net_pnl": 440.0,
            "status": "CLOSED",
        }

        data = ReportData(
            date_iso=sample_report_data.date_iso,
            mode=sample_report_data.mode,
            account=sample_report_data.account,
            opening_capital=sample_report_data.opening_capital,
            signals=sample_report_data.signals,
            trades=[trade_no_strategy],
            orders=sample_report_data.orders,
            fm_ledger=sample_report_data.fm_ledger,
            screener_results=sample_report_data.screener_results,
            innings=sample_report_data.innings,
            system_events=sample_report_data.system_events,
            recon_log=sample_report_data.recon_log,
            gate_state=sample_report_data.gate_state,
            excluded_symbols=sample_report_data.excluded_symbols,
        )

        wb = openpyxl.Workbook()
        ws = build_sheet_4_candles(wb, data)

        # Row 3 = first trade row; col 3 = Strategy
        strategy_value = ws.cell(row=3, column=3).value
        assert strategy_value == "MOMENTUM", f"Expected 'MOMENTUM' from signal fallback, got {strategy_value!r}"

    def test_build_sheet_4_candles_creates_sheet(self, sample_report_data):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_4_candles(wb, sample_report_data)

        assert ws.title == "4_Candles"

    def test_build_sheet_4_candles_excludes_cancelled_trades(self, sample_report_data):
        """Candle analysis should exclude CANCELLED trades to avoid NaN values."""
        import openpyxl

        # Add a CANCELLED trade to the data
        cancelled_trade = {
            "trade_id": "trade-cancelled",
            "signal_id": "sig-002",
            "symbol": "INFY",
            "direction": "LONG",
            "strategy": "MOMENTUM",
            "status": "CANCELLED",
            # Fields below would be None/0 for cancelled trades
            "entry_actual_price": None,
            "exit_price": None,
            "exit_reason": None,
        }

        data_with_cancelled = ReportData(
            date_iso=sample_report_data.date_iso,
            mode=sample_report_data.mode,
            account=sample_report_data.account,
            opening_capital=sample_report_data.opening_capital,
            signals=sample_report_data.signals,
            trades=[sample_report_data.trades[0], cancelled_trade],  # 1 CLOSED + 1 CANCELLED
            orders=sample_report_data.orders,
            fm_ledger=sample_report_data.fm_ledger,
            screener_results=sample_report_data.screener_results,
            innings=sample_report_data.innings,
            system_events=sample_report_data.system_events,
            recon_log=sample_report_data.recon_log,
            gate_state=sample_report_data.gate_state,
            excluded_symbols=sample_report_data.excluded_symbols,
        )

        wb = openpyxl.Workbook()
        ws = build_sheet_4_candles(wb, data_with_cancelled)

        # Count trade rows (skip headers in rows 1-2, check from row 3 onwards)
        # Only CLOSED trades should appear, not CANCELLED
        trade_rows = []
        symbol_col = 4  # Symbol column
        for row in range(3, 50):  # Check up to row 50
            symbol = ws.cell(row=row, column=symbol_col).value
            if symbol and symbol != "":
                trade_rows.append((row, symbol))

        # Should have exactly 1 trade (the CLOSED one)
        assert len(trade_rows) == 1, f"Expected 1 CLOSED trade, found {len(trade_rows)}: {trade_rows}"

        # Verify the CLOSED trade is present (RELIANCE from sample_trade)
        assert trade_rows[0][1] == "RELIANCE"

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

    def test_build_sheet_6_strategy_fallback_to_signal(self, sample_report_data):
        """Strategy analysis should use signal.strategy if trade.strategy is empty."""
        import openpyxl

        # Create a trade with empty strategy
        trade_no_strategy = {
            "trade_id": "trade-003",
            "signal_id": "sig-001",  # Same signal which has strategy="MOMENTUM"
            "symbol": "INFY",
            "direction": "SHORT",
            "strategy": "",  # Empty strategy
            "qty_planned": 8,
            "qty_filled": 8,
            "entry_target_price": 1500.0,
            "entry_actual_price": 1498.0,
            "sl_initial": 1550.0,
            "tgt_initial": 1450.0,
            "margin_reserved": 2400.0,
            "created_at": "2026-05-15T10:00:00+05:30",
            "entry_time": "2026-05-15T10:01:00+05:30",
            "exit_time": "2026-05-15T11:30:00+05:30",
            "exit_price": 1450.0,
            "exit_reason": "TGT_HIT",
            "gross_pnl": 384.0,
            "charges": 20.0,
            "net_pnl": 364.0,
            "status": "CLOSED",
        }

        data_with_empty_strategy = ReportData(
            date_iso=sample_report_data.date_iso,
            mode=sample_report_data.mode,
            account=sample_report_data.account,
            opening_capital=sample_report_data.opening_capital,
            signals=sample_report_data.signals,  # Contains sig-001 with strategy="MOMENTUM"
            trades=[trade_no_strategy],
            orders=sample_report_data.orders,
            fm_ledger=sample_report_data.fm_ledger,
            screener_results=sample_report_data.screener_results,
            innings=sample_report_data.innings,
            system_events=sample_report_data.system_events,
            recon_log=sample_report_data.recon_log,
            gate_state=sample_report_data.gate_state,
            excluded_symbols=sample_report_data.excluded_symbols,
        )

        wb = openpyxl.Workbook()
        ws = build_sheet_6_strategy(wb, data_with_empty_strategy)

        # Check that row 3 (first strategy row) has "MOMENTUM" from signal fallback
        # Column 2 is Strategy
        strategy_value = ws.cell(row=3, column=2).value
        assert strategy_value == "MOMENTUM", f"Expected 'MOMENTUM' from signal fallback, got {strategy_value!r}"

    def test_build_sheet_4_candles_ohlc_populated_from_db(self, sample_report_data):
        """When candle_map has data, OHLC cols H-K should be populated."""
        import openpyxl
        from dataclasses import replace

        data = replace(sample_report_data, candle_map={
            ("RELIANCE", "09:31"): {
                "open": 2490.0, "high": 2610.0,
                "low": 2488.0, "close": 2605.0, "is_synthetic": 0,
            },
        })
        wb = openpyxl.Workbook()
        ws = build_sheet_4_candles(wb, data)

        assert ws.cell(row=3, column=8).value == 2490.0, "Open mismatch"
        assert ws.cell(row=3, column=9).value == 2610.0, "High mismatch"
        assert ws.cell(row=3, column=10).value == 2488.0, "Low mismatch"
        assert ws.cell(row=3, column=11).value == 2605.0, "Close mismatch"
        assert ws.cell(row=3, column=12).value == "No"

    def test_build_sheet_4_candles_ohlc_dash_without_candle_data(self, sample_report_data):
        """Without candle data, OHLC cols show dash (not applicable)."""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_4_candles(wb, sample_report_data)

        assert ws.cell(row=3, column=8).value == "—"
        assert ws.cell(row=3, column=9).value == "—"
        assert ws.cell(row=3, column=10).value == "—"
        assert ws.cell(row=3, column=11).value == "—"
        assert ws.cell(row=3, column=12).value == "N/A"

    def test_build_sheet_6_drawdown_pct_positive_trade(self, sample_report_data):
        """When only wins exist, max_loss=0 so drawdown_pct=0."""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = build_sheet_6_strategy(wb, sample_report_data)

        # Row 3 = first strategy; col 17 = Drawdown %
        # sample_trade net_pnl=930 (win only), max_loss=0, drawdown=0
        drawdown = ws.cell(row=3, column=17).value
        assert isinstance(drawdown, float), f"Expected float, got {drawdown!r}"
        assert drawdown == 0.0

    def test_build_sheet_6_drawdown_pct_loss_trade(self):
        """Drawdown % = (max_loss / capital_used) * 100."""
        import openpyxl

        data = ReportData(
            date_iso="2026-05-15",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            signals=[],
            trades=[{
                "trade_id": "t1",
                "symbol": "INFY",
                "direction": "LONG",
                "strategy": "TEST",
                "net_pnl": -500.0,
                "gross_pnl": -500.0,
                "charges": 0,
                "margin_reserved": 10000.0,
                "status": "CLOSED",
            }],
            orders=[], fm_ledger=[], screener_results=[], innings=[],
            system_events=[], recon_log=[], gate_state=[],
            excluded_symbols=[],
        )

        wb = openpyxl.Workbook()
        ws = build_sheet_6_strategy(wb, data)

        # max_loss=-500, capital_used=10000 → -500/10000*100 = -5.0
        drawdown = ws.cell(row=3, column=17).value
        assert drawdown == -5.0, f"Expected -5.0, got {drawdown!r}"

    def test_build_sheet_6_drawdown_pct_zero_capital_guard(self):
        """Drawdown % should be 0.0 when capital_used is 0 (div/zero guard)."""
        import openpyxl

        data = ReportData(
            date_iso="2026-05-15",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            signals=[],
            trades=[{
                "trade_id": "t1",
                "symbol": "TCS",
                "direction": "LONG",
                "strategy": "TEST",
                "net_pnl": -100.0,
                "gross_pnl": -100.0,
                "charges": 0,
                "margin_reserved": 0,  # zero capital — guard against div/zero
                "status": "CLOSED",
            }],
            orders=[], fm_ledger=[], screener_results=[], innings=[],
            system_events=[], recon_log=[], gate_state=[],
            excluded_symbols=[],
        )

        wb = openpyxl.Workbook()
        ws = build_sheet_6_strategy(wb, data)

        drawdown = ws.cell(row=3, column=17).value
        assert drawdown == 0.0, f"Expected 0.0 for zero capital, got {drawdown!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Integration test
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateReport:
    """Integration test for full report generation."""

    def _setup_mock_store(self, sample_report_data):
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
        mock_store.get_candles_for_date.return_value = []
        mock_store.get_trade_excursions_for_date.return_value = []
        mock_store.get_session_row.return_value = {
            "mode": "PAPER",
            "account_id": "TEST001",
        }
        return mock_store

    def test_generate_daily_report_creates_file(self, sample_report_data, tmp_path):
        mock_store = self._setup_mock_store(sample_report_data)

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "system_config.yaml").write_text("excluded_symbols: []\n")

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

        mock_store = self._setup_mock_store(sample_report_data)

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "system_config.yaml").write_text("excluded_symbols: []\n")

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
            signals=[],
            trades=[trade],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
        )

        suggestions = _generate_tune_suggestions(data)
        assert isinstance(suggestions, list)


# ─────────────────────────────────────────────────────────────────────────────
# Per-strategy effective min_score tests (FIX-119)
# ─────────────────────────────────────────────────────────────────────────────

class TestPerStrategyEligibleScore:
    """Col N (Sheet 1) and Col F (Sheet 2) read eligible_score from screener_results."""

    def _make_data(self, eligible_score):
        signal = {
            "signal_id": "sig-A",
            "symbol": "TATAMOTORS",
            "strategy": "gap_fade_long",
            "scanner": "chartink",
            "status": "TRADED",
            "trade_id": "tr-A",
            "received_at": "2026-05-18T09:30:00+05:30",
            "triggered_at": "2026-05-18T09:29:55+05:30",
            "trigger_price": 800.0,
        }
        trade = {
            "trade_id": "tr-A",
            "signal_id": "sig-A",
            "symbol": "TATAMOTORS",
            "direction": "LONG",
            "strategy": "gap_fade_long",
            "qty_planned": 5,
            "qty_filled": 5,
            "entry_target_price": 800.0,
            "entry_actual_price": 801.0,
            "sl_initial": 780.0,
            "tgt_initial": 840.0,
            "gross_pnl": 500.0,
            "charges": 20.0,
            "net_pnl": 480.0,
            "status": "CLOSED",
            "created_at": "2026-05-18T09:30:00+05:30",
            "entry_time": "2026-05-18T09:30:05+05:30",
            "exit_time": "2026-05-18T10:45:00+05:30",
        }
        screener = {"signal_id": "sig-A", "score": 35, "status": "PASSED"}
        if eligible_score is not None:
            screener["eligible_score"] = eligible_score
        return ReportData(
            date_iso="2026-05-18",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            signals=[signal],
            trades=[trade],
            orders=[],
            fm_ledger=[],
            screener_results=[screener],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
        )

    def test_sheet_1_signals_eligible_score_from_db(self):
        import openpyxl
        data = self._make_data(30)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_1_signals(wb, data)
        ws = wb["1_Signals"]
        eligible_score_cell = ws.cell(row=2, column=14).value
        assert eligible_score_cell == 30, f"Expected 30, got {eligible_score_cell}"

    def test_sheet_2_orders_eligible_score_from_db(self):
        import openpyxl
        data = self._make_data(30)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_2_orders(wb, data)
        ws = wb["2_Orders"]
        eligible_score_cell = ws.cell(row=4, column=6).value
        assert eligible_score_cell == 30, f"Expected 30, got {eligible_score_cell}"

    def test_sheet_1_signals_dash_when_no_eligible_score(self):
        import openpyxl
        data = self._make_data(None)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_1_signals(wb, data)
        ws = wb["1_Signals"]
        eligible_score_cell = ws.cell(row=2, column=14).value
        assert eligible_score_cell == "—", f"Expected dash, got {eligible_score_cell}"


# ─────────────────────────────────────────────────────────────────────────────
# Cost columns from DB (FIX-124 — replaces FIX-121 runtime computation)
# ─────────────────────────────────────────────────────────────────────────────

class TestCostColumnsFromDB:

    def test_build_sheet_2_orders_expense_cols_from_db(self):
        """Cols 31-35 (brokerage/STT/etc.) read from trades.cost_* DB columns."""
        import openpyxl
        trade = {
            "trade_id": "t-exp-01",
            "signal_id": "sig-exp-01",
            "symbol": "RELIANCE",
            "direction": "LONG",
            "strategy": "gap_go_long",
            "qty_planned": 10,
            "qty_filled": 10,
            "entry_target_price": 2500.0,
            "entry_actual_price": 2500.0,
            "exit_price": 2520.0,
            "sl_initial": 2450.0,
            "tgt_initial": 2600.0,
            "gross_pnl": 200.0,
            "charges": 55.0,
            "net_pnl": 145.0,
            "status": "CLOSED",
            "created_at": "2026-05-18T09:30:00+05:30",
            "entry_time": "2026-05-18T09:30:05+05:30",
            "exit_time": "2026-05-18T10:45:00+05:30",
            "exit_reason": "TGT_HIT",
            "cost_brokerage": 40.0,
            "cost_stt": 6.3,
            "cost_exchange_txn": 3.0,
            "cost_stamp_duty": 0.75,
            "cost_gst": 7.74,
            "sl_trail_count": 2,
        }
        data = ReportData(
            date_iso="2026-05-18",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            signals=[],
            trades=[trade],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
        )
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_2_orders(wb, data)
        ws = wb["2_Orders"]
        assert ws.cell(4, 31).value == 40.0
        assert ws.cell(4, 32).value == 6.3
        assert ws.cell(4, 33).value == 3.0
        assert ws.cell(4, 34).value == 0.75
        assert ws.cell(4, 35).value == 7.74
        assert ws.cell(4, 28).value == 2

    def test_build_sheet_2_orders_zero_when_charges_zero(self):
        """Cols 31-35 show 0 when charges=0 (zero-fill rule: numeric cols never show dash)."""
        import openpyxl
        trade = {
            "trade_id": "t-no-cost",
            "signal_id": "sig-nc",
            "symbol": "TCS",
            "direction": "LONG",
            "strategy": "gap_go_long",
            "qty_planned": 5,
            "qty_filled": 5,
            "entry_target_price": 3000.0,
            "entry_actual_price": 3000.0,
            "sl_initial": 2950.0,
            "tgt_initial": 3100.0,
            "gross_pnl": 0,
            "charges": 0,
            "net_pnl": 0,
            "status": "CLOSED",
            "created_at": "2026-05-18T09:30:00+05:30",
            "entry_time": "2026-05-18T09:30:05+05:30",
            "exit_time": "2026-05-18T10:45:00+05:30",
            "exit_reason": "EOD",
        }
        data = ReportData(
            date_iso="2026-05-18",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            signals=[],
            trades=[trade],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
        )
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_2_orders(wb, data)
        ws = wb["2_Orders"]
        for col in (31, 32, 33, 34, 35):
            assert ws.cell(4, col).value == 0, f"Col {col} should be 0, got {ws.cell(4, col).value}"

    def test_build_sheet_2_orders_dash_when_charges_null(self):
        """Cols 31-35 show dash only when charges is genuinely NULL."""
        import openpyxl
        trade = {
            "trade_id": "t-null-cost",
            "signal_id": "sig-nc2",
            "symbol": "TCS",
            "direction": "LONG",
            "strategy": "gap_go_long",
            "qty_planned": 5,
            "qty_filled": 5,
            "entry_target_price": 3000.0,
            "entry_actual_price": 3000.0,
            "sl_initial": 2950.0,
            "tgt_initial": 3100.0,
            "gross_pnl": 0,
            "net_pnl": 0,
            "status": "CLOSED",
            "created_at": "2026-05-18T09:30:00+05:30",
            "entry_time": "2026-05-18T09:30:05+05:30",
            "exit_time": "2026-05-18T10:45:00+05:30",
            "exit_reason": "EOD",
        }
        data = ReportData(
            date_iso="2026-05-18",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            signals=[],
            trades=[trade],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
        )
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_2_orders(wb, data)
        ws = wb["2_Orders"]
        for col in (31, 32, 33, 34, 35):
            assert ws.cell(4, col).value == "—", f"Col {col} should be dash, got {ws.cell(4, col).value}"


# ─────────────────────────────────────────────────────────────────────────────
# FIX-125: Report data gap fixes
# ─────────────────────────────────────────────────────────────────────────────

class TestFix125EligibleScoreFallback:
    """BUG 1/3: eligible_score falls back to strategy_min_scores when DB is NULL."""

    def test_sheet_1_eligible_score_from_strategy_min_scores(self):
        import openpyxl
        signal = {
            "signal_id": "sig-fb",
            "symbol": "HDFCBANK",
            "strategy": "first_pullback_long",
            "scanner": "chartink",
            "status": "TRADED",
            "trade_id": "tr-fb",
            "received_at": "2026-05-18T09:30:00+05:30",
            "triggered_at": "2026-05-18T09:29:55+05:30",
            "trigger_price": 1600.0,
        }
        screener = {"signal_id": "sig-fb", "score": 70, "status": "PASSED"}
        data = ReportData(
            date_iso="2026-05-18",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            signals=[signal],
            trades=[],
            orders=[],
            fm_ledger=[],
            screener_results=[screener],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
            strategy_min_scores={"first_pullback_long": 55},
        )
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_1_signals(wb, data)
        ws = wb["1_Signals"]
        assert ws.cell(row=2, column=14).value == 55

    def test_sheet_2_eligible_score_from_strategy_min_scores(self):
        import openpyxl
        trade = {
            "trade_id": "tr-fb2",
            "signal_id": "sig-fb2",
            "symbol": "INFY",
            "direction": "LONG",
            "strategy": "gap_go_long",
            "qty_planned": 5,
            "qty_filled": 5,
            "entry_target_price": 1500.0,
            "entry_actual_price": 1502.0,
            "sl_initial": 1470.0,
            "tgt_initial": 1560.0,
            "gross_pnl": 0,
            "charges": 0,
            "net_pnl": 0,
            "status": "CLOSED",
            "created_at": "2026-05-18T09:30:00+05:30",
            "entry_time": "2026-05-18T09:30:05+05:30",
            "exit_time": "2026-05-18T10:45:00+05:30",
            "exit_reason": "EOD",
        }
        screener = {"signal_id": "sig-fb2", "score": 60, "status": "PASSED"}
        data = ReportData(
            date_iso="2026-05-18",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            signals=[],
            trades=[trade],
            orders=[],
            fm_ledger=[],
            screener_results=[screener],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
            strategy_min_scores={"gap_go_long": 45},
        )
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_2_orders(wb, data)
        ws = wb["2_Orders"]
        assert ws.cell(4, 6).value == 45


class TestFix125CostFallback:
    """BUG 5: cost columns back-calculate from charges when DB breakdown is NULL."""

    def test_cost_fallback_computation(self):
        import openpyxl
        trade = {
            "trade_id": "t-fallback",
            "signal_id": "sig-fall",
            "symbol": "RELIANCE",
            "direction": "LONG",
            "strategy": "gap_go_long",
            "qty_planned": 10,
            "qty_filled": 10,
            "entry_target_price": 2500.0,
            "entry_actual_price": 2500.0,
            "exit_price": 2520.0,
            "sl_initial": 2450.0,
            "tgt_initial": 2600.0,
            "gross_pnl": 200.0,
            "charges": 55.0,
            "net_pnl": 145.0,
            "status": "CLOSED",
            "created_at": "2026-05-18T09:30:00+05:30",
            "entry_time": "2026-05-18T09:30:05+05:30",
            "exit_time": "2026-05-18T10:45:00+05:30",
            "exit_reason": "TGT_HIT",
        }
        data = ReportData(
            date_iso="2026-05-18",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            signals=[],
            trades=[trade],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
            broker_rates={
                "brokerage_flat_intraday": 20.0,
                "brokerage_pct_intraday": 0.03,
                "stt_sell_pct": 0.025,
                "exchange_txn_pct": 0.00297,
                "sebi_pct": 0.0001,
                "gst_pct": 18.0,
                "stamp_duty_mis_buy_pct": 0.003,
            },
        )
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_2_orders(wb, data)
        ws = wb["2_Orders"]
        assert isinstance(ws.cell(4, 31).value, float), "Brokerage should be computed"
        assert ws.cell(4, 31).value > 0, "Brokerage should be positive"
        assert isinstance(ws.cell(4, 32).value, float), "STT should be computed"


class TestFix125TimeOfDayRejected:
    """BUG 7: Sheet 6 time-of-day Rejected column computed correctly."""

    def test_rejected_column_computed(self):
        import openpyxl
        signals = [
            {"signal_id": f"s{i}", "strategy": "TEST", "received_at": "2026-05-18T09:20:00+05:30",
             "status": "REJECTED_SCREEN", "symbol": f"SYM{i}"}
            for i in range(5)
        ]
        data = ReportData(
            date_iso="2026-05-18",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            signals=signals,
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
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_6_strategy(wb, data)
        ws = wb["6_Strategy_Analysis"]

        found_time_of_day = False
        for r in range(1, 50):
            if ws.cell(r, 1).value == "TIME-OF-DAY ANALYSIS":
                header_row = r + 1
                first_data_row = r + 2
                found_time_of_day = True
                break

        assert found_time_of_day, "TIME-OF-DAY section not found"
        rejected_val = ws.cell(first_data_row, 5).value
        assert isinstance(rejected_val, int), f"Rejected should be int, got {type(rejected_val)}: {rejected_val}"
        assert rejected_val >= 0


class TestFix125NumericZeroFill:
    """General rule: numeric columns show 0, not dash or None."""

    def test_sheet_2_numeric_cols_zero_not_dash(self):
        import openpyxl
        trade = {
            "trade_id": "t-zf",
            "signal_id": "sig-zf",
            "symbol": "TCS",
            "direction": "LONG",
            "strategy": "TEST",
            "qty_planned": 5,
            "qty_filled": 5,
            "entry_target_price": 3000.0,
            "entry_actual_price": 3000.0,
            "sl_initial": 3000.0,
            "tgt_initial": 3000.0,
            "gross_pnl": 0,
            "charges": 0,
            "net_pnl": 0,
            "status": "CLOSED",
            "created_at": "2026-05-18T09:30:00+05:30",
            "entry_time": "2026-05-18T09:30:05+05:30",
            "exit_time": "2026-05-18T10:45:00+05:30",
            "exit_reason": "EOD",
            "exit_price": 3000.0,
        }
        data = ReportData(
            date_iso="2026-05-18",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            signals=[],
            trades=[trade],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
        )
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_2_orders(wb, data)
        ws = wb["2_Orders"]
        assert isinstance(ws.cell(4, 12).value, int), f"Time in trade should be int, got {ws.cell(4, 12).value!r}"
        assert ws.cell(4, 18).value == 0, "Sys R:R should be 0, not dash"
        assert ws.cell(4, 36).value == 0, "Total costs should be 0 for CLOSED+charges=0"


class TestFix126DisplayRules:
    """FIX-126: dash-vs-zero display rules for open/failed trades."""

    def _make_data(self, trades, signals=None):
        return ReportData(
            date_iso="2026-05-20",
            mode="PAPER",
            account="TEST",
            opening_capital=100000.0,
            signals=signals or [],
            trades=trades,
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
        )

    def _open_trade(self):
        return {
            "trade_id": "t-open",
            "signal_id": "sig-open",
            "symbol": "INFY",
            "direction": "LONG",
            "strategy": "TEST",
            "qty_planned": 10,
            "qty_filled": 10,
            "entry_target_price": 1500.0,
            "entry_actual_price": 1502.0,
            "sl_initial": 1450.0,
            "tgt_initial": 1600.0,
            "gross_pnl": 0,
            "charges": None,
            "net_pnl": 0,
            "status": "OPEN",
            "created_at": "2026-05-20T09:30:00+05:30",
            "entry_time": "2026-05-20T09:31:00+05:30",
            "exit_time": None,
            "exit_price": None,
            "exit_reason": "",
        }

    def test_bug2_open_trade_cost_cols_show_dash(self):
        """BUG 2: Cost cols AE-AJ show dash for non-CLOSED trades."""
        import openpyxl
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_2_orders(wb, self._make_data([self._open_trade()]))
        ws = wb["2_Orders"]
        for col in (31, 32, 33, 34, 35, 36):
            assert ws.cell(4, col).value == "—", f"Col {col} should be dash for open trade"

    def test_bug3_open_trade_exit_price_dash(self):
        """BUG 3: Exit price shows dash when None/0."""
        import openpyxl
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_2_orders(wb, self._make_data([self._open_trade()]))
        ws = wb["2_Orders"]
        assert ws.cell(4, 43).value == "—", "Exit price should be dash for open trade"

    def test_bug4_open_trade_time_in_trade_dash(self):
        """BUG 4: Time in Trade shows dash for trades without exit_time."""
        import openpyxl
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_2_orders(wb, self._make_data([self._open_trade()]))
        ws = wb["2_Orders"]
        assert ws.cell(4, 12).value == "—", "Time in trade should be dash for open trade"

    def test_bug6_sl_risk_tgt_profit_always_shown(self):
        """BUG 6: SL Risk and Target Profit shown for all trades with sys_sl/sys_tgt."""
        import openpyxl
        trade = self._open_trade()
        trade["status"] = "CLOSED"
        trade["exit_time"] = "2026-05-20T14:00:00+05:30"
        trade["exit_price"] = 1480.0
        trade["exit_reason"] = "SL_HIT"
        trade["charges"] = 52.0
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_3_capital(wb, self._make_data([trade]))
        ws = wb["3_Capital"]
        sl_risk = ws.cell(3, 10).value
        tgt_profit = ws.cell(3, 11).value
        assert isinstance(sl_risk, (int, float)) and sl_risk > 0, f"SL Risk should be numeric, got {sl_risk!r}"
        assert isinstance(tgt_profit, (int, float)) and tgt_profit > 0, f"Target Profit should be numeric, got {tgt_profit!r}"

    def test_bug6_no_sl_shows_dash(self):
        """BUG 6: SL Risk shows dash when sl_initial is not set."""
        import openpyxl
        trade = self._open_trade()
        trade["status"] = "CLOSED"
        trade["sl_initial"] = 0
        trade["tgt_initial"] = 0
        trade["exit_time"] = "2026-05-20T14:00:00+05:30"
        trade["exit_price"] = 1480.0
        trade["exit_reason"] = "EOD"
        trade["charges"] = 52.0
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_3_capital(wb, self._make_data([trade]))
        ws = wb["3_Capital"]
        assert ws.cell(3, 10).value == "—", "SL Risk should be dash when sl_initial=0"
        assert ws.cell(3, 11).value == "—", "Target Profit should be dash when tgt_initial=0"

    def test_bug7_telegram_sent_at_uses_created_at_fallback(self):
        """BUG 7: Sent At uses created_at when entry_time is None."""
        import openpyxl
        trade = self._open_trade()
        trade["entry_time"] = None
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_5_telegram(wb, self._make_data([trade]))
        ws = wb["5_Telegram"]
        sent_at = ws.cell(5, 2).value
        assert sent_at == "09:30:00", f"Sent At should use created_at fallback, got {sent_at!r}"

    def test_bug8_processed_includes_traded_signals(self):
        """BUG 8: Processed count includes TRADED/FILLED/CLOSED statuses."""
        import openpyxl
        signals = [
            {"signal_id": "s1", "strategy": "ALPHA", "received_at": "2026-05-20T09:30:00+05:30", "status": "TRADED"},
            {"signal_id": "s2", "strategy": "ALPHA", "received_at": "2026-05-20T09:35:00+05:30", "status": "PROCESSED"},
            {"signal_id": "s3", "strategy": "ALPHA", "received_at": "2026-05-20T09:40:00+05:30", "status": "REJECTED"},
        ]
        trade = self._open_trade()
        trade["strategy"] = "ALPHA"
        trade["signal_id"] = "s1"
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        build_sheet_6_strategy(wb, self._make_data([trade], signals))
        ws = wb["6_Strategy_Analysis"]
        processed = ws.cell(3, 4).value
        assert processed == 2, f"Processed should count TRADED+PROCESSED signals (2), got {processed}"
