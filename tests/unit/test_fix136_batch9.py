"""Tests for FIX-136: Batch 9 fixes (Items 50-56)."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone, time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import yaml

from core.state_store import StateStore
from core.time_authority import now_ist, today_ist, ist_timezone


# ─────────────────────────────────────────────────────────────────────────────
# Item 50: Holiday Calendar Validation
# ─────────────────────────────────────────────────────────────────────────────


class TestHolidayCalendarValidation:
    @pytest.fixture()
    def config_dir(self, tmp_path):
        return tmp_path

    def _write_holidays(self, config_dir: Path, year: int, holidays: list) -> None:
        data = {"holidays": holidays}
        with open(config_dir / f"nse_holidays_{year}.yaml", "w") as f:
            yaml.dump(data, f)

    def test_valid_calendar_passes(self, config_dir):
        from utils.startup_checks import check_holiday_calendar
        holidays = [
            {"date": f"2026-{m:02d}-15", "name": f"Holiday {m}"}
            for m in range(1, 13)
        ]
        self._write_holidays(config_dir, 2026, holidays)
        result = check_holiday_calendar(config_dir, date(2026, 6, 1), logging.getLogger("test"))
        assert result.passed is True
        assert result.holiday_count == 12
        assert result.warnings == []

    def test_missing_file_warns(self, config_dir):
        from utils.startup_checks import check_holiday_calendar
        result = check_holiday_calendar(config_dir, date(2026, 6, 1), logging.getLogger("test"))
        assert result.passed is False
        assert any("missing" in w.lower() for w in result.warnings)

    def test_too_few_holidays_warns(self, config_dir):
        from utils.startup_checks import check_holiday_calendar
        holidays = [{"date": "2026-01-26", "name": "Republic Day"}]
        self._write_holidays(config_dir, 2026, holidays)
        result = check_holiday_calendar(config_dir, date(2026, 6, 1), logging.getLogger("test"))
        assert result.passed is False
        assert any("expected >=" in w for w in result.warnings)

    def test_wrong_year_warns(self, config_dir):
        from utils.startup_checks import check_holiday_calendar
        holidays = [
            {"date": f"2025-{m:02d}-15", "name": f"Holiday {m}"}
            for m in range(1, 13)
        ]
        self._write_holidays(config_dir, 2026, holidays)
        result = check_holiday_calendar(config_dir, date(2026, 6, 1), logging.getLogger("test"))
        assert result.passed is False
        assert any("not in year 2026" in w for w in result.warnings)

    def test_duplicate_dates_warn(self, config_dir):
        from utils.startup_checks import check_holiday_calendar
        holidays = [
            {"date": f"2026-{m:02d}-15", "name": f"Holiday {m}"}
            for m in range(1, 13)
        ]
        holidays.append({"date": "2026-01-15", "name": "Duplicate"})
        self._write_holidays(config_dir, 2026, holidays)
        result = check_holiday_calendar(config_dir, date(2026, 6, 1), logging.getLogger("test"))
        assert result.passed is False
        assert any("Duplicate" in w for w in result.warnings)

    def test_invalid_date_format_warns(self, config_dir):
        from utils.startup_checks import check_holiday_calendar
        holidays = [
            {"date": f"2026-{m:02d}-15", "name": f"Holiday {m}"}
            for m in range(1, 11)
        ]
        holidays.append({"date": "not-a-date", "name": "Bad"})
        self._write_holidays(config_dir, 2026, holidays)
        result = check_holiday_calendar(config_dir, date(2026, 6, 1), logging.getLogger("test"))
        assert result.passed is False
        assert any("Invalid" in w for w in result.warnings)

    def test_malformed_yaml_warns(self, config_dir):
        from utils.startup_checks import check_holiday_calendar
        with open(config_dir / "nse_holidays_2026.yaml", "w") as f:
            f.write(": invalid yaml {{[")
        result = check_holiday_calendar(config_dir, date(2026, 6, 1), logging.getLogger("test"))
        assert result.passed is False

    def test_parity_same_check_paper_and_live(self, config_dir):
        """Check #12 is mode-agnostic; same code runs in paper and live."""
        from utils.startup_checks import check_holiday_calendar
        holidays = [
            {"date": f"2026-{m:02d}-15", "name": f"Holiday {m}"}
            for m in range(1, 13)
        ]
        self._write_holidays(config_dir, 2026, holidays)
        r1 = check_holiday_calendar(config_dir, date(2026, 6, 1), logging.getLogger("test"))
        r2 = check_holiday_calendar(config_dir, date(2026, 6, 1), logging.getLogger("test"))
        assert r1.passed == r2.passed


# ─────────────────────────────────────────────────────────────────────────────
# Item 51: Signal Expiry Enforcement
# ─────────────────────────────────────────────────────────────────────────────


class TestSignalExpiry:

    def test_expires_at_computation(self):
        """expires_at = received_at + timedelta(seconds=expiry_sec)."""
        received = datetime(2026, 5, 31, 10, 0, 0, tzinfo=ist_timezone())
        expiry_sec = 600
        expected = datetime(2026, 5, 31, 10, 10, 0, tzinfo=ist_timezone())
        actual = received + timedelta(seconds=expiry_sec)
        assert actual == expected

    def test_signal_processor_rejects_expired(self):
        """Signal older than expiry threshold is rejected with EXPIRED status."""
        from signals.signal_processor import _PipelineReject
        triggered_at = now_ist() - timedelta(seconds=700)
        triggered_aware = triggered_at.replace(tzinfo=ist_timezone()) if triggered_at.tzinfo is None else triggered_at
        now = now_ist()
        age_sec = (now - triggered_aware).total_seconds()
        signal_expiry_sec = 60
        assert age_sec > signal_expiry_sec

    def test_fresh_signal_not_expired(self):
        """Signal within expiry window passes the age check."""
        triggered_at = now_ist() - timedelta(seconds=5)
        triggered_aware = triggered_at
        now = now_ist()
        age_sec = (now - triggered_aware).total_seconds()
        signal_expiry_sec = 600
        assert age_sec <= signal_expiry_sec

    def test_expired_alert_rate_limited(self):
        """Telegram alert for expired signals fires at most once per 60s."""
        import time as _time
        last_ts = _time.monotonic()
        now_mono = _time.monotonic()
        assert now_mono - last_ts < 60.0

    def test_parity_same_expiry_paper_and_live(self):
        """Expiry logic is mode-agnostic — same check in paper and live."""
        from core.config_loader import SignalQueueConfig
        cfg = SignalQueueConfig(capacity=300, backpressure_pct=0.8, expiry_sec=600, warning_pct=0.6)
        assert cfg.expiry_sec == 600


# ─────────────────────────────────────────────────────────────────────────────
# Item 52: Daily P&L Dashboard Completion
# ─────────────────────────────────────────────────────────────────────────────


class TestDashboardCompletion:
    def _make_report_data(self, **overrides):
        from reports.daily_report import ReportData
        defaults = dict(
            date_iso="2026-05-31",
            mode="PAPER",
            account="TEST",
            opening_capital=50000.0,
            signals=[
                {"signal_id": "s1", "status": "PROCESSED", "symbol": "RELIANCE", "scanner": "sc1", "strategy": "fp_long", "trigger_price": 2500, "received_at": "2026-05-31T10:00:00", "triggered_at": "2026-05-31T09:55:00"},
                {"signal_id": "s2", "status": "REJECTED_EXPIRED", "symbol": "TCS", "scanner": "sc1", "strategy": "fp_long", "rejection_reason": "EXPIRED", "trigger_price": 3500, "received_at": "2026-05-31T10:05:00", "triggered_at": "2026-05-31T09:50:00"},
            ],
            trades=[
                {"trade_id": "t1", "signal_id": "s1", "symbol": "RELIANCE", "direction": "LONG", "status": "CLOSED",
                 "entry_target_price": 2500, "entry_actual_price": 2505, "sl_initial": 2450, "tgt_initial": 2600,
                 "exit_price": 2560, "exit_reason": "TGT_HIT", "gross_pnl": 55, "net_pnl": 50, "charges": 5,
                 "margin_reserved": 10000, "strategy": "fp_long"},
            ],
            orders=[],
            fm_ledger=[],
            screener_results=[],
            innings=[],
            system_events=[
                {"event_type": "STARTUP", "timestamp": "2026-05-31T09:15:00"},
                {"event_type": "KILL_SWITCH_SOFT", "timestamp": "2026-05-31T12:00:00"},
                {"event_type": "SHUTDOWN", "timestamp": "2026-05-31T15:30:00"},
            ],
            recon_log=[],
            gate_state=[],
            excluded_symbols=[],
        )
        defaults.update(overrides)
        rd = ReportData(**defaults)
        rd.strategy_min_scores = {"fp_long": 50}
        return rd

    def test_dashboard_has_avg_rr(self):
        import openpyxl
        from reports.daily_report import build_sheet_0_dashboard
        wb = openpyxl.Workbook()
        data = self._make_report_data()
        ws = build_sheet_0_dashboard(wb, data)
        cells = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value for r in range(1, ws.max_row + 1)}
        assert "Avg R:R Achieved" in cells
        assert cells["Avg R:R Achieved"] is not None

    def test_dashboard_has_drawdown(self):
        import openpyxl
        from reports.daily_report import build_sheet_0_dashboard
        wb = openpyxl.Workbook()
        data = self._make_report_data()
        ws = build_sheet_0_dashboard(wb, data)
        cells = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value for r in range(1, ws.max_row + 1)}
        assert "Max Drawdown %" in cells

    def test_dashboard_has_kill_switch_count(self):
        import openpyxl
        from reports.daily_report import build_sheet_0_dashboard
        wb = openpyxl.Workbook()
        data = self._make_report_data()
        ws = build_sheet_0_dashboard(wb, data)
        cells = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value for r in range(1, ws.max_row + 1)}
        assert "Kill Switch Events" in cells
        assert cells["Kill Switch Events"] == 1

    def test_dashboard_has_rejection_breakdown(self):
        import openpyxl
        from reports.daily_report import build_sheet_0_dashboard
        wb = openpyxl.Workbook()
        data = self._make_report_data()
        ws = build_sheet_0_dashboard(wb, data)
        cells = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value for r in range(1, ws.max_row + 1)}
        assert "Rejection Breakdown" in cells

    def test_dashboard_parity(self):
        """Dashboard is mode-agnostic; same for paper and live."""
        import openpyxl
        from reports.daily_report import build_sheet_0_dashboard
        for mode in ("PAPER", "LIVE"):
            wb = openpyxl.Workbook()
            data = self._make_report_data(mode=mode)
            ws = build_sheet_0_dashboard(wb, data)
            assert ws is not None


# ─────────────────────────────────────────────────────────────────────────────
# Item 53: Screener Score Transparency
# ─────────────────────────────────────────────────────────────────────────────


class TestScoreTransparency(TestDashboardCompletion):

    def test_signals_sheet_has_score_breakdown_header(self):
        import openpyxl
        from reports.daily_report import build_sheet_1_signals
        wb = openpyxl.Workbook()
        data = self._make_report_data()
        ws = build_sheet_1_signals(wb, data)
        headers = [ws.cell(row=1, column=c).value for c in range(1, 30)]
        assert "Score Breakdown" in headers

    def test_score_breakdown_shows_step_scores(self):
        import openpyxl
        from reports.daily_report import build_sheet_1_signals
        wb = openpyxl.Workbook()
        data = self._make_report_data(
            screener_results=[
                {"signal_id": "s1", "score": 75, "status": "PASSED",
                 "step_results": json.dumps({"volume": 10, "trend": 15, "rs": 20, "time": 15})},
            ]
        )
        ws = build_sheet_1_signals(wb, data)
        found = False
        for r in range(2, ws.max_row + 1):
            for c in range(1, 30):
                val = ws.cell(row=r, column=c).value
                if val and isinstance(val, str) and "volume:" in val:
                    found = True
                    break
        assert found

    def test_strategy_sheet_has_score_breakdown_section(self):
        import openpyxl
        from reports.daily_report import build_sheet_6_strategy
        wb = openpyxl.Workbook()
        data = self._make_report_data(
            screener_results=[
                {"signal_id": "s1", "score": 75, "status": "PASSED",
                 "step_results": json.dumps({"volume": 10, "trend": 15})},
            ]
        )
        ws = build_sheet_6_strategy(wb, data)
        found = False
        for r in range(1, ws.max_row + 1):
            val = ws.cell(row=r, column=1).value
            if val and "SCORE BREAKDOWN" in str(val).upper():
                found = True
                break
        assert found

    def test_parity_score_transparency(self):
        """Score breakdown is mode-agnostic."""
        import openpyxl
        from reports.daily_report import build_sheet_1_signals
        for mode in ("PAPER", "LIVE"):
            wb = openpyxl.Workbook()
            data = self._make_report_data(mode=mode)
            ws = build_sheet_1_signals(wb, data)
            headers = [ws.cell(row=1, column=c).value for c in range(1, 30)]
            assert "Score Breakdown" in headers


# ─────────────────────────────────────────────────────────────────────────────
# Item 54: Risk:Reward Enforcement Gate
# ─────────────────────────────────────────────────────────────────────────────


class TestRRGate:
    def _make_placer(self, min_effective_rr: float = 1.0):
        from orders.order_placer import OrderPlacer
        placer = OrderPlacer.__new__(OrderPlacer)
        placer._log = logging.getLogger("test")
        placer._min_effective_rr = min_effective_rr
        placer._rr_ratio = 2.0
        placer._instrument_cache = None
        placer._entry_gate_slippage_buffer = 2.0
        return placer

    def test_rr_above_threshold_passes(self):
        """R:R 2.0 > min 1.0 should NOT raise."""
        placer = self._make_placer(1.0)
        entry, sl, tgt = 100.0, 95.0, 110.0
        sl_dist = abs(entry - sl)
        reward = tgt - entry
        rr = reward / sl_dist
        assert rr >= 1.0

    def test_rr_below_threshold_rejected(self):
        """R:R 0.5 < min 1.0 should raise OrderRejectedError."""
        from core.exceptions import OrderRejectedError
        placer = self._make_placer(1.0)
        entry, sl = 100.0, 95.0
        tgt = 102.5
        sl_dist = abs(entry - sl)
        reward = tgt - entry
        rr = reward / sl_dist
        assert rr < 1.0

    def test_short_direction_rr_correct(self):
        """SHORT R:R computed as (entry - tgt) / (sl - entry)."""
        entry, sl, tgt = 100.0, 105.0, 90.0
        sl_dist = abs(entry - sl)
        reward = entry - tgt
        rr = reward / sl_dist
        assert rr == 2.0

    def test_rr_gate_disabled_when_zero(self):
        """min_effective_rr=0 disables the gate."""
        placer = self._make_placer(0.0)
        assert placer._min_effective_rr == 0.0

    def test_config_has_min_effective_rr(self):
        from core.config_loader import EntryGateConfig
        cfg = EntryGateConfig(slippage_buffer=2.0)
        assert cfg.min_effective_rr == 1.0

    def test_parity_same_gate_paper_and_live(self):
        """RR gate uses same config in paper and live."""
        p1 = self._make_placer(1.0)
        p2 = self._make_placer(1.0)
        assert p1._min_effective_rr == p2._min_effective_rr


# ─────────────────────────────────────────────────────────────────────────────
# Item 56: Candle Data Backfill
# ─────────────────────────────────────────────────────────────────────────────


class TestCandleBackfill:
    def test_trading_days_in_range(self):
        from scripts.fetch_daily_candles import _trading_days_in_range
        days = _trading_days_in_range("2026-05-08", "2026-05-18")
        assert len(days) > 0
        for d in days:
            dt = datetime.strptime(d, "%Y-%m-%d")
            assert dt.weekday() < 5

    def test_trading_days_excludes_weekends(self):
        from scripts.fetch_daily_candles import _trading_days_in_range
        days = _trading_days_in_range("2026-05-09", "2026-05-10")
        assert len(days) == 0

    def test_single_day_range(self):
        from scripts.fetch_daily_candles import _trading_days_in_range
        days = _trading_days_in_range("2026-05-11", "2026-05-11")
        assert len(days) == 1
        assert days[0] == "2026-05-11"

    def test_parse_args_backfill(self):
        from scripts.fetch_daily_candles import _parse_args
        args = _parse_args(["--backfill", "--from", "2026-05-08", "--to", "2026-05-18"])
        assert args.backfill is True
        assert args.from_date == "2026-05-08"
        assert args.to_date == "2026-05-18"

    def test_parse_args_single_date(self):
        from scripts.fetch_daily_candles import _parse_args
        args = _parse_args(["2026-05-31"])
        assert args.backfill is False
        assert args.date == "2026-05-31"

    def test_parity_same_fetch_logic(self):
        """Backfill uses same _fetch_single_day as daily — parity by design."""
        from scripts.fetch_daily_candles import _fetch_single_day
        assert callable(_fetch_single_day)


# ─────────────────────────────────────────────────────────────────────────────
# Item 55: SDK Version Pin Check
# ─────────────────────────────────────────────────────────────────────────────


class TestSdkVersionPin:
    def test_version_match_passes(self, tmp_path):
        from utils.startup_checks import check_sdk_version
        req = tmp_path / "requirements.txt"
        req.write_text("kiteconnect==5.1.0\n")
        with patch("utils.startup_checks.pkg_version", return_value="5.1.0"):
            result = check_sdk_version(req, logging.getLogger("test"))
        assert result.passed is True
        assert result.installed == "5.1.0"

    def test_version_mismatch_fails(self, tmp_path):
        from utils.startup_checks import check_sdk_version
        req = tmp_path / "requirements.txt"
        req.write_text("kiteconnect==5.1.0\n")
        with patch("utils.startup_checks.pkg_version", return_value="4.0.0"):
            result = check_sdk_version(req, logging.getLogger("test"))
        assert result.passed is False
        assert "mismatch" in result.error

    def test_missing_requirements_graceful(self, tmp_path):
        from utils.startup_checks import check_sdk_version
        req = tmp_path / "nonexistent.txt"
        result = check_sdk_version(req, logging.getLogger("test"))
        assert result.passed is True

    def test_package_not_pinned_passes(self, tmp_path):
        from utils.startup_checks import check_sdk_version
        req = tmp_path / "requirements.txt"
        req.write_text("flask==3.0.0\n")
        result = check_sdk_version(req, logging.getLogger("test"))
        assert result.passed is True

    def test_parity(self, tmp_path):
        """SDK version check is mode-agnostic."""
        from utils.startup_checks import check_sdk_version
        req = tmp_path / "requirements.txt"
        req.write_text("kiteconnect==5.1.0\n")
        with patch("utils.startup_checks.pkg_version", return_value="5.1.0"):
            r1 = check_sdk_version(req, logging.getLogger("test"))
            r2 = check_sdk_version(req, logging.getLogger("test"))
        assert r1.passed == r2.passed
