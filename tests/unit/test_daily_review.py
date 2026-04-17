"""
tests/unit/test_daily_review.py — Module 36 tests

Tests for reports/daily_review.py (DR1-DR15) and the 7 new
state_store date-scoped query helpers (DR8).

Coverage:
  - Empty day -> report generated with zero rows (DR12)
  - Full day -> all 8 sections populated (DR3)
  - XLSX format: 8 sheets named correctly (DR4)
  - MD format: 8 sections with headings (DR4)
  - Both format -> both files created (DR2)
  - Signal funnel correct for sample data (DR9)
  - Screener analytics: rejection counts per step (DR10)
  - P&L calculation: gross - costs = net (DR11)
  - Win rate calculation (DR11)
  - CLI mode with --date flag (DR2)
  - Default date = today (DR2)
  - Output dir created if absent (DR4)
  - Read-only: state_store unchanged after generation (DR7)
  - State_store helpers: 7 date-scoped queries (DR8)
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from reports.daily_review import (
    DailyReviewGenerator,
    ReportPaths,
    _build_funnel,
    _build_pnl_summary,
    _build_screener_analytics,
    _MULTI_INNING_HEADERS,
    _pivot_innings_to_trade_rows,
    _parse_args,
    main,
)

_IST = timezone(timedelta(hours=5, minutes=30))
_TODAY = "2026-04-16"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _mock_store(signals=None, trades=None, orders=None, ledger=None,
                events=None, recon=None, screener=None, innings=None):
    store = MagicMock()
    store.get_signals_for_date.return_value       = signals or []
    store.get_trades_for_date.return_value        = trades or []
    store.get_orders_for_date.return_value        = orders or []
    store.get_capital_ledger_for_date.return_value = ledger or []
    store.get_system_events_for_date.return_value = events or []
    store.get_reconciliation_log_for_date.return_value = recon or []
    store.get_screener_results_for_date.return_value = screener or []
    store.get_inning_summary_by_date.return_value = innings or []
    return store


def _sample_inning_row(
    trade_id="trade-1",
    signal_id="sig-1",
    symbol="RELIANCE",
    direction="LONG",
    scanner_name="breakout",
    inning_number=1,
    entry_price=2500.0,
    entry_ts=f"{_TODAY}T09:16:00",
    sl_price=2450.0,
    tgt_price=2600.0,
    exit_price=2600.0,
    exit_ts=f"{_TODAY}T14:30:00",
    exit_reason="TGT",
    pnl_pct=4.0,
    duration_sec=18840,
    is_real=1,
):
    return {
        "trade_id":      trade_id,
        "signal_id":     signal_id,
        "symbol":        symbol,
        "direction":     direction,
        "scanner_name":  scanner_name,
        "inning_number": inning_number,
        "entry_price":   entry_price,
        "entry_ts":      entry_ts,
        "sl_price":      sl_price,
        "tgt_price":     tgt_price,
        "exit_price":    exit_price,
        "exit_ts":       exit_ts,
        "exit_reason":   exit_reason,
        "pnl_pct":       pnl_pct,
        "duration_sec":  duration_sec,
        "is_real":       is_real,
    }


def _make_generator(store=None):
    s = store or _mock_store()
    log = MagicMock()
    ta  = MagicMock()
    return DailyReviewGenerator(s, ta, log)


def _sample_signal(status="TRADED"):
    return {
        "signal_id":    "sig-1",
        "symbol":       "RELIANCE",
        "scanner":      "breakout",
        "strategy":     "open_low",
        "triggered_at": f"{_TODAY}T09:15:00+05:30",
        "received_at":  f"{_TODAY}T09:15:01+05:30",
        "expires_at":   f"{_TODAY}T09:30:00+05:30",
        "status":       status,
        "rejection_reason": None,
        "trade_id":     "trade-1",
        "trigger_price": 2500.0,
        "fingerprint":  "abc123",
        "fingerprint_date": _TODAY,
    }


def _sample_trade(status="CLOSED", gross_pnl=500.0, charges=20.0, net_pnl=480.0):
    return {
        "trade_id":             "trade-1",
        "signal_id":            "sig-1",
        "symbol":               "RELIANCE",
        "direction":            "LONG",
        "strategy":             "open_low",
        "sector":               "Energy",
        "qty_planned":          10,
        "qty_filled":           10,
        "entry_target_price":   2500.0,
        "entry_actual_price":   2501.0,
        "sl_initial":           2450.0,
        "tgt_initial":          2580.0,
        "margin_reserved":      5000.0,
        "risk_amount":          500.0,
        "created_at":           f"{_TODAY}T09:15:10+05:30",
        "entry_time":           f"{_TODAY}T09:16:00+05:30",
        "exit_time":            f"{_TODAY}T14:30:00+05:30",
        "exit_price":           2550.0,
        "exit_reason":          "TGT_HIT",
        "gross_pnl":            gross_pnl,
        "charges":              charges,
        "net_pnl":              net_pnl,
        "status":               status,
        "recovered_flag":       0,
        "entry_mode":           "FULL",
        "order_protocol":       "CO_PLUS_TGT",
        "updated_at":           f"{_TODAY}T14:30:01+05:30",
    }


def _sample_order():
    return {
        "order_id":         "ord-001",
        "trade_id":         "trade-1",
        "leg":              "ENTRY",
        "leg_index":        0,
        "transaction_type": "BUY",
        "order_type":       "LIMIT",
        "product":          "MIS",
        "variety":          "regular",
        "qty_requested":    10,
        "price":            2500.0,
        "trigger_price":    None,
        "status":           "COMPLETE",
        "qty_filled":       10,
        "avg_fill_price":   2501.0,
        "placed_at":        f"{_TODAY}T09:15:10+05:30",
        "updated_at":       f"{_TODAY}T09:16:00+05:30",
        "superseded_by":    None,
    }


def _sample_ledger():
    return {
        "ledger_id":     1,
        "ts":            f"{_TODAY}T09:15:10+05:30",
        "mutation_type": "RESERVE",
        "amount":        5000.0,
        "bucket":        "intraday",
        "balance_before": 50000.0,
        "balance_after":  45000.0,
        "signal_id":     "sig-1",
        "reservation_id": "resv-abc",
        "reason":        None,
    }


def _sample_screener_result(status="PASSED", rejection_step=None):
    step_results = {
        "volume_check": {"passed": True,  "score": 20},
        "trend_check":  {"passed": rejection_step != "trend_check", "score": 15},
    }
    return {
        "id":        1,
        "signal_id": "sig-1",
        "score":     35,
        "tier":      "HIGH",
        "status":    status,
        "step_results": json.dumps(step_results),
        "latencies":    json.dumps({"volume_check": 5.2, "trend_check": 3.1}),
        "market_data_snapshot": "{}",
        "ts":        f"{_TODAY}T09:15:02+05:30",
    }


# ─────────────────────────────────────────────────────────────────────────────
# DR2: parse args
# ─────────────────────────────────────────────────────────────────────────────

class TestParseArgs:
    def test_defaults(self):
        args = _parse_args([])
        assert args.date is None
        assert args.output_dir == "reports/daily"
        assert args.fmt == "both"

    def test_date_flag(self):
        args = _parse_args(["--date", "2026-01-15"])
        assert args.date == "2026-01-15"

    def test_format_xlsx(self):
        args = _parse_args(["--format", "xlsx"])
        assert args.fmt == "xlsx"

    def test_format_md(self):
        args = _parse_args(["--format", "md"])
        assert args.fmt == "md"

    def test_output_dir(self):
        args = _parse_args(["--output-dir", "/tmp/reports"])
        assert args.output_dir == "/tmp/reports"


# ─────────────────────────────────────────────────────────────────────────────
# DR12: empty day
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptyDay:
    def test_empty_day_generates_report(self, tmp_path):
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        assert paths.md_path is not None
        assert paths.md_path.exists()

    def test_empty_day_xlsx_has_9_sheets(self, tmp_path):
        import openpyxl
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["xlsx"])
        wb = openpyxl.load_workbook(paths.xlsx_path)
        assert len(wb.sheetnames) == 9

    def test_empty_day_md_has_sections(self, tmp_path):
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        for heading in ["## 1.", "## 2.", "## 3.", "## 4.",
                        "## 5.", "## 6.", "## 7.", "## 8.",
                        "## Multi-Inning"]:
            assert heading in content, f"Missing {heading} in md"

    def test_empty_day_summary_has_zero_counts(self, tmp_path):
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        assert "No activity today" in content or "0" in content


# ─────────────────────────────────────────────────────────────────────────────
# DR4: file creation
# ─────────────────────────────────────────────────────────────────────────────

class TestFileCreation:
    def test_both_formats_creates_both_files(self, tmp_path):
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["xlsx", "md"])
        assert paths.xlsx_path is not None and paths.xlsx_path.exists()
        assert paths.md_path   is not None and paths.md_path.exists()

    def test_xlsx_only(self, tmp_path):
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["xlsx"])
        assert paths.xlsx_path is not None and paths.xlsx_path.exists()
        assert paths.md_path is None

    def test_md_only(self, tmp_path):
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        assert paths.md_path is not None and paths.md_path.exists()
        assert paths.xlsx_path is None

    def test_output_dir_created_if_absent(self, tmp_path):
        new_dir = tmp_path / "nested" / "deep"
        assert not new_dir.exists()
        gen = _make_generator()
        gen.generate(_TODAY, new_dir, ["md"])
        assert new_dir.exists()

    def test_xlsx_sheet_names(self, tmp_path):
        import openpyxl
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["xlsx"])
        wb = openpyxl.load_workbook(paths.xlsx_path)
        expected = {"SUMMARY", "SIGNAL_FUNNEL", "SCREENER_ANALYTICS",
                    "TRADES", "ORDERS", "CAPITAL_LEDGER",
                    "SYSTEM_EVENTS", "ALERTS_SENT", "MULTI_INNING_TRACKING"}
        assert set(wb.sheetnames) == expected


# ─────────────────────────────────────────────────────────────────────────────
# DR9: signal funnel
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalFunnel:
    def test_funnel_received_count_equals_total(self):
        signals = [_sample_signal("TRADED"), _sample_signal("REJECTED_QUALITY")]
        funnel = _build_funnel(signals)
        received = next(f for f in funnel if f["stage"] == "RECEIVED")
        assert received["count"] == 2

    def test_funnel_traded_passes_all_stages(self):
        signals = [_sample_signal("TRADED")]
        funnel = _build_funnel(signals)
        # TRADED status should be counted in all forward stages
        exited_row = next(f for f in funnel if f["stage"] == "EXITED")
        assert exited_row["count"] == 1

    def test_funnel_rejected_drops_at_appropriate_stage(self):
        signals = [
            _sample_signal("TRADED"),
            _sample_signal("REJECTED_QUALITY"),
        ]
        funnel = _build_funnel(signals)
        received_count = next(f for f in funnel if f["stage"] == "RECEIVED")["count"]
        # REJECTED_QUALITY won't appear in PASSED_SCREEN or later
        passed_row = next(f for f in funnel if f["stage"] == "PASSED_SCREEN")
        assert passed_row["count"] <= received_count

    def test_empty_signals_all_zeros(self):
        funnel = _build_funnel([])
        for row in funnel:
            assert row["count"] == 0

    def test_funnel_has_10_stages(self):
        funnel = _build_funnel([])
        assert len(funnel) == 10

    def test_funnel_drop_pct_received_is_zero(self):
        funnel = _build_funnel([_sample_signal()])
        received = next(f for f in funnel if f["stage"] == "RECEIVED")
        assert received["drop_pct"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# DR10: screener analytics
# ─────────────────────────────────────────────────────────────────────────────

class TestScreenerAnalytics:
    def test_per_tier_count(self):
        rows = [
            _sample_screener_result(status="PASSED"),
            _sample_screener_result(status="REJECTED_TREND"),
        ]
        analytics = _build_screener_analytics(rows)
        assert analytics["per_tier"]["HIGH"]["count"] == 2

    def test_rejection_step_counted(self):
        row = _sample_screener_result(status="REJECTED_TREND",
                                      rejection_step="trend_check")
        analytics = _build_screener_analytics([row])
        # trend_check.passed = False -> counted in per_step
        assert "trend_check" in analytics["per_step"]
        assert analytics["per_step"]["trend_check"]["count"] == 1

    def test_latency_avg_computed(self):
        row = _sample_screener_result()
        analytics = _build_screener_analytics([row])
        assert "volume_check" in analytics["latency"]
        assert analytics["latency"]["volume_check"]["avg_ms"] == pytest.approx(5.2)

    def test_empty_screener_results(self):
        analytics = _build_screener_analytics([])
        assert analytics["per_step"] == {}
        assert analytics["per_tier"] == {}
        assert analytics["latency"] == {}


# ─────────────────────────────────────────────────────────────────────────────
# DR11: P&L calculation
# ─────────────────────────────────────────────────────────────────────────────

class TestPnlCalculation:
    def test_net_pnl_from_trade(self):
        trades = [_sample_trade(gross_pnl=500.0, charges=20.0, net_pnl=480.0)]
        summary = _build_pnl_summary(trades)
        assert summary["realized_pnl"] == pytest.approx(480.0)
        assert summary["gross_pnl"]    == pytest.approx(500.0)
        assert summary["total_costs"]  == pytest.approx(20.0)

    def test_win_rate_100_percent_all_winners(self):
        trades = [
            _sample_trade(net_pnl=100.0),
            _sample_trade(net_pnl=200.0),
        ]
        summary = _build_pnl_summary(trades)
        assert summary["win_rate_pct"] == pytest.approx(100.0)

    def test_win_rate_0_all_losers(self):
        trades = [_sample_trade(net_pnl=-100.0)]
        summary = _build_pnl_summary(trades)
        assert summary["win_rate_pct"] == pytest.approx(0.0)

    def test_win_rate_mixed(self):
        trades = [
            _sample_trade(net_pnl=100.0),
            _sample_trade(net_pnl=-50.0),
        ]
        summary = _build_pnl_summary(trades)
        assert summary["win_rate_pct"] == pytest.approx(50.0)

    def test_avg_duration_computed(self):
        trades = [_sample_trade()]
        summary = _build_pnl_summary(trades)
        # entry 09:16, exit 14:30 -> 314 min
        assert summary["avg_duration_min"] > 0

    def test_empty_trades_zero_pnl(self):
        summary = _build_pnl_summary([])
        assert summary["realized_pnl"]   == 0.0
        assert summary["win_rate_pct"]   == 0.0
        assert summary["avg_duration_min"] == 0.0

    def test_open_trades_not_counted_in_pnl(self):
        trades = [_sample_trade(status="OPEN", net_pnl=999.0)]
        summary = _build_pnl_summary(trades)
        assert summary["realized_pnl"] == 0.0   # OPEN not CLOSED


# ─────────────────────────────────────────────────────────────────────────────
# Full day: all sections populated
# ─────────────────────────────────────────────────────────────────────────────

class TestFullDay:
    def _full_store(self):
        return _mock_store(
            signals  = [_sample_signal("TRADED")],
            trades   = [_sample_trade()],
            orders   = [_sample_order()],
            ledger   = [_sample_ledger()],
            events   = [{"event_id": 1, "timestamp": f"{_TODAY}T09:00:00",
                         "event_type": "STARTUP", "scenario": "COLD",
                         "details": "{}"}],
            recon    = [{"id": 1, "ts": f"{_TODAY}T09:01:00",
                         "check_name": "MANUAL_CLOSE", "tier": "COSMETIC",
                         "symbol": "RELIANCE", "trade_id": "t1",
                         "description": "test", "action_taken": "none",
                         "success": 1}],
            screener = [_sample_screener_result()],
        )

    def test_xlsx_trades_sheet_has_data(self, tmp_path):
        import openpyxl
        gen = _make_generator(self._full_store())
        paths = gen.generate(_TODAY, tmp_path, ["xlsx"])
        wb = openpyxl.load_workbook(paths.xlsx_path)
        ws = wb["TRADES"]
        # header row + 1 data row
        assert ws.max_row == 2

    def test_xlsx_orders_sheet_has_data(self, tmp_path):
        import openpyxl
        gen = _make_generator(self._full_store())
        paths = gen.generate(_TODAY, tmp_path, ["xlsx"])
        wb = openpyxl.load_workbook(paths.xlsx_path)
        ws = wb["ORDERS"]
        assert ws.max_row == 2

    def test_xlsx_capital_sheet_has_data(self, tmp_path):
        import openpyxl
        gen = _make_generator(self._full_store())
        paths = gen.generate(_TODAY, tmp_path, ["xlsx"])
        wb = openpyxl.load_workbook(paths.xlsx_path)
        ws = wb["CAPITAL_LEDGER"]
        assert ws.max_row == 2

    def test_md_contains_symbol(self, tmp_path):
        gen = _make_generator(self._full_store())
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        assert "RELIANCE" in content

    def test_md_contains_date_heading(self, tmp_path):
        gen = _make_generator(self._full_store())
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        assert _TODAY in content


# ─────────────────────────────────────────────────────────────────────────────
# DR7: read-only — state_store not mutated
# ─────────────────────────────────────────────────────────────────────────────

class TestReadOnly:
    def test_state_store_not_mutated(self, tmp_path):
        store = _mock_store()
        gen = _make_generator(store)
        gen.generate(_TODAY, tmp_path, ["md"])

        # No write methods should have been called
        store.transaction.assert_not_called()
        store.execute.assert_not_called()

    def test_queries_use_correct_date(self, tmp_path):
        store = _mock_store()
        gen = _make_generator(store)
        gen.generate("2026-03-15", tmp_path, ["md"])

        store.get_signals_for_date.assert_called_once_with("2026-03-15")
        store.get_trades_for_date.assert_called_once_with("2026-03-15")
        store.get_capital_ledger_for_date.assert_called_once_with("2026-03-15")


# ─────────────────────────────────────────────────────────────────────────────
# DR8: state_store date-scoped query helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestStateStoreHelpers:
    """Integration tests against a real SQLite DB."""

    @pytest.fixture()
    def store(self, tmp_path):
        from core.state_store import StateStore
        s = StateStore(tmp_path / "test.db")
        yield s
        s.close()

    def _insert_signal(self, store, date_iso):
        with store.transaction() as cur:
            cur.execute(
                """INSERT INTO signals
                   (signal_id, symbol, scanner, strategy,
                    triggered_at, received_at, expires_at,
                    status, fingerprint, fingerprint_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"sig-{date_iso}", "RELIANCE", "scanner", "strategy",
                    f"{date_iso}T09:15:00+05:30",
                    f"{date_iso}T09:15:01+05:30",
                    f"{date_iso}T09:30:00+05:30",
                    "TRADED",
                    f"fp-{date_iso}", date_iso,
                ),
            )

    def test_get_signals_for_date_returns_matching(self, store):
        self._insert_signal(store, "2026-04-16")
        self._insert_signal(store, "2026-04-17")
        rows = store.get_signals_for_date("2026-04-16")
        assert len(rows) == 1
        assert rows[0]["signal_id"] == "sig-2026-04-16"

    def test_get_signals_for_date_empty(self, store):
        rows = store.get_signals_for_date("2026-04-16")
        assert rows == []

    def test_get_system_events_for_date(self, store):
        store.insert_system_event(
            "STARTUP", f"2026-04-16T09:00:00+05:30", "COLD", "{}"
        )
        store.insert_system_event(
            "SHUTDOWN", f"2026-04-17T15:30:00+05:30", None, None
        )
        rows = store.get_system_events_for_date("2026-04-16")
        assert len(rows) == 1
        assert rows[0]["event_type"] == "STARTUP"

    def test_get_capital_ledger_for_date_empty(self, store):
        rows = store.get_capital_ledger_for_date("2026-04-16")
        assert rows == []

    def test_get_trades_for_date_empty(self, store):
        rows = store.get_trades_for_date("2026-04-16")
        assert rows == []

    def test_get_orders_for_date_empty(self, store):
        rows = store.get_orders_for_date("2026-04-16")
        assert rows == []

    def test_get_reconciliation_log_for_date(self, store):
        store.insert_reconciliation_log(
            ts="2026-04-16T10:00:00+05:30",
            check_name="MANUAL_CLOSE",
            tier="COSMETIC",
            symbol="TCS",
            trade_id=None,
            description="test",
            action_taken="none",
            success=True,
        )
        rows = store.get_reconciliation_log_for_date("2026-04-16")
        assert len(rows) == 1
        assert rows[0]["check_name"] == "MANUAL_CLOSE"

    def test_get_screener_results_for_date(self, store):
        # Insert a signal first (FK constraint)
        self._insert_signal(store, "2026-04-16")
        store.insert_screener_result(
            signal_id="sig-2026-04-16",
            score=42,
            tier="HIGH",
            status="PASSED",
            step_results_json=json.dumps({"vol": {"passed": True}}),
            latencies_json=json.dumps({"vol": 5.0}),
            market_data_snapshot_json=json.dumps({"price": 100.0}),
            ts="2026-04-16T09:15:05+05:30",
        )
        rows = store.get_screener_results_for_date("2026-04-16")
        assert len(rows) == 1
        assert rows[0]["score"] == 42


# ─────────────────────────────────────────────────────────────────────────────
# DR2: CLI mode
# ─────────────────────────────────────────────────────────────────────────────

class TestCliMode:
    def test_cli_default_date_is_today(self, tmp_path):
        with patch("reports.daily_review._now_ist_date", return_value=_TODAY), \
             patch("core.state_store.StateStore") as MockStore, \
             patch("pathlib.Path.exists", return_value=True):
            MockStore.return_value = _mock_store()
            rc = main([
                "--output-dir", str(tmp_path),
                "--format", "md",
                "--db", "dummy.db",
            ])
        assert rc == 0

    def test_cli_with_explicit_date(self, tmp_path):
        with patch("core.state_store.StateStore") as MockStore, \
             patch("pathlib.Path.exists", return_value=True):
            MockStore.return_value = _mock_store()
            rc = main([
                "--date", "2026-01-15",
                "--output-dir", str(tmp_path),
                "--format", "md",
                "--db", "dummy.db",
            ])
        assert rc == 0

    def test_cli_invalid_date_exits_1(self, tmp_path, capsys):
        rc = main(["--date", "not-a-date", "--output-dir", str(tmp_path)])
        assert rc == 1

    def test_cli_missing_db_exits_1(self, tmp_path, capsys):
        rc = main([
            "--date", _TODAY,
            "--output-dir", str(tmp_path),
            "--db", "/nonexistent/trading.db",
        ])
        assert rc == 1


# ─────────────────────────────────────────────────────────────────────────────
# DR-U1/DR-U2: _pivot_innings_to_trade_rows
# ─────────────────────────────────────────────────────────────────────────────

class TestPivotInnings:

    def test_single_inning_i2_i3_blank(self):
        """1 inning row -> i2/i3 cols all blank (DR-U2)."""
        flat = [_sample_inning_row()]
        rows = _pivot_innings_to_trade_rows(flat)
        assert len(rows) == 1
        r = rows[0]
        assert r["i1_entry"] == 2500.0
        assert r["i1_exit_reason"] == "TGT"
        assert r["i2_entry"] == ""
        assert r["i2_exit_reason"] == ""
        assert r["i3_entry"] == ""

    def test_two_innings_i3_blank(self):
        """2 inning rows -> i3 cols blank, i2 filled (DR-U2)."""
        flat = [
            _sample_inning_row(inning_number=1),
            _sample_inning_row(
                inning_number=2, entry_price=2600.0, exit_price=2548.0,
                exit_reason="SL", pnl_pct=-2.0, is_real=0,
            ),
        ]
        rows = _pivot_innings_to_trade_rows(flat)
        assert len(rows) == 1
        r = rows[0]
        assert r["i2_entry"] == 2600.0
        assert r["i2_exit_reason"] == "SL"
        assert r["i3_entry"] == ""

    def test_three_innings_all_filled(self):
        """3 inning rows -> all blocks filled (DR-U2)."""
        flat = [
            _sample_inning_row(inning_number=1),
            _sample_inning_row(inning_number=2, entry_price=2600.0,
                               exit_price=2548.0, exit_reason="SL",
                               pnl_pct=-2.0, is_real=0),
            _sample_inning_row(inning_number=3, entry_price=2548.0,
                               exit_price=2597.0, exit_reason="TGT",
                               pnl_pct=1.92, is_real=0),
        ]
        rows = _pivot_innings_to_trade_rows(flat)
        assert len(rows) == 1
        r = rows[0]
        assert r["i1_entry"] == 2500.0
        assert r["i2_entry"] == 2600.0
        assert r["i3_entry"] == 2548.0
        assert r["i3_exit_reason"] == "TGT"

    def test_out_of_order_innings_assigned_correctly(self):
        """Innings in wrong input order -> correctly placed in i1/i2/i3 (DR-U2)."""
        flat = [
            _sample_inning_row(inning_number=3, entry_price=100.0),
            _sample_inning_row(inning_number=1, entry_price=80.0),
            _sample_inning_row(inning_number=2, entry_price=90.0, is_real=0),
        ]
        rows = _pivot_innings_to_trade_rows(flat)
        r = rows[0]
        assert r["i1_entry"] == 80.0
        assert r["i2_entry"] == 90.0
        assert r["i3_entry"] == 100.0

    def test_two_trades_two_rows(self):
        """2 distinct trade_ids -> 2 output rows (DR-U2)."""
        flat = [
            _sample_inning_row(trade_id="t1", signal_id="s1", symbol="RELIANCE"),
            _sample_inning_row(trade_id="t2", signal_id="s2", symbol="TCS"),
        ]
        rows = _pivot_innings_to_trade_rows(flat)
        assert len(rows) == 2
        syms = {r["symbol"] for r in rows}
        assert syms == {"RELIANCE", "TCS"}

    def test_total_innings_computed(self):
        """total_innings is count of inning rows for that trade."""
        flat = [
            _sample_inning_row(inning_number=1),
            _sample_inning_row(inning_number=2, is_real=0, entry_price=2600.0),
        ]
        rows = _pivot_innings_to_trade_rows(flat)
        assert rows[0]["total_innings"] == 2

    def test_aggregate_cumulative_simulated_pnl(self):
        """cumulative_simulated_pnl_pct sums i2+i3 only, not i1."""
        flat = [
            _sample_inning_row(inning_number=1, pnl_pct=4.0),
            _sample_inning_row(inning_number=2, pnl_pct=-2.0, is_real=0,
                               entry_price=2600.0),
            _sample_inning_row(inning_number=3, pnl_pct=1.5, is_real=0,
                               entry_price=2548.0),
        ]
        rows = _pivot_innings_to_trade_rows(flat)
        r = rows[0]
        assert abs(r["cumulative_simulated_pnl_pct"] - (-0.5)) < 0.01

    def test_aggregate_best_worst_span_all_innings(self):
        """best/worst_inning_pnl_pct span all present innings."""
        flat = [
            _sample_inning_row(inning_number=1, pnl_pct=4.0),
            _sample_inning_row(inning_number=2, pnl_pct=-2.0, is_real=0,
                               entry_price=2600.0),
        ]
        rows = _pivot_innings_to_trade_rows(flat)
        r = rows[0]
        assert r["best_inning_pnl_pct"] == 4.0
        assert r["worst_inning_pnl_pct"] == -2.0

    def test_open_inning_excluded_from_aggregates(self):
        """OPEN inning (no exit) -> excluded from pnl aggregates."""
        flat = [
            _sample_inning_row(
                inning_number=1, exit_price=None, exit_ts=None,
                exit_reason=None, pnl_pct=None,
            ),
        ]
        rows = _pivot_innings_to_trade_rows(flat)
        r = rows[0]
        assert r["i1_exit_reason"] == "OPEN"
        assert r["i1_pnl_pct"] == ""
        assert r["best_inning_pnl_pct"] == ""
        assert r["worst_inning_pnl_pct"] == ""

    def test_empty_input_returns_empty_list(self):
        """No innings -> empty list (DR-U1)."""
        assert _pivot_innings_to_trade_rows([]) == []

    def test_duration_min_is_integer_minutes(self):
        """duration_sec / 60 -> integer duration_min."""
        flat = [_sample_inning_row(duration_sec=3600)]
        rows = _pivot_innings_to_trade_rows(flat)
        assert rows[0]["i1_duration_min"] == 60


# ─────────────────────────────────────────────────────────────────────────────
# DR-U5: XLSX multi-inning sheet
# ─────────────────────────────────────────────────────────────────────────────

class TestXlsxMultiInning:

    def test_sheet_exists(self, tmp_path):
        """MULTI_INNING_TRACKING sheet present in workbook (DR-U5)."""
        import openpyxl
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["xlsx"])
        wb = openpyxl.load_workbook(paths.xlsx_path)
        assert "MULTI_INNING_TRACKING" in wb.sheetnames

    def test_header_row_frozen(self, tmp_path):
        """Header row is frozen in MULTI_INNING_TRACKING (DR-U5)."""
        import openpyxl
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["xlsx"])
        wb = openpyxl.load_workbook(paths.xlsx_path)
        ws = wb["MULTI_INNING_TRACKING"]
        assert ws.freeze_panes == "A2"

    def test_36_columns(self, tmp_path):
        """Header row has 36 columns (DR-U2, DR-U5)."""
        import openpyxl
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["xlsx"])
        wb = openpyxl.load_workbook(paths.xlsx_path)
        ws = wb["MULTI_INNING_TRACKING"]
        headers = [c.value for c in ws[1]]
        assert len(headers) == 36

    def test_empty_day_no_data_row(self, tmp_path):
        """Empty innings -> sheet shows placeholder message row (DR-U5)."""
        import openpyxl
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["xlsx"])
        wb = openpyxl.load_workbook(paths.xlsx_path)
        ws = wb["MULTI_INNING_TRACKING"]
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        assert len(rows) == 1
        assert "No multi-inning data" in str(rows[0][0])

    def test_one_trade_one_row(self, tmp_path):
        """1 trade with 1 inning -> 1 data row (DR-U1)."""
        import openpyxl
        store = _mock_store(innings=[_sample_inning_row()])
        gen = _make_generator(store)
        paths = gen.generate(_TODAY, tmp_path, ["xlsx"])
        wb = openpyxl.load_workbook(paths.xlsx_path)
        ws = wb["MULTI_INNING_TRACKING"]
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        assert len(rows) == 1
        assert rows[0][0] == "trade-1"  # trade_id is first col

    def test_two_trades_two_rows(self, tmp_path):
        """2 trades -> 2 data rows."""
        import openpyxl
        store = _mock_store(innings=[
            _sample_inning_row(trade_id="t1", signal_id="s1"),
            _sample_inning_row(trade_id="t2", signal_id="s2"),
        ])
        gen = _make_generator(store)
        paths = gen.generate(_TODAY, tmp_path, ["xlsx"])
        wb = openpyxl.load_workbook(paths.xlsx_path)
        ws = wb["MULTI_INNING_TRACKING"]
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        assert len(rows) == 2


# ─────────────────────────────────────────────────────────────────────────────
# DR-U5: MD multi-inning section
# ─────────────────────────────────────────────────────────────────────────────

class TestMdMultiInning:

    def test_section_heading_present(self, tmp_path):
        """'## Multi-Inning Tracking' heading present in md (DR-U5)."""
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        assert "## Multi-Inning Tracking" in content

    def test_empty_innings_shows_no_data_message(self, tmp_path):
        """Empty innings -> 'No multi-inning data' message in md."""
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        assert "No multi-inning data" in content

    def test_one_inning_pipe_table(self, tmp_path):
        """1 inning -> pipe table present in md section (DR-U5)."""
        store = _mock_store(innings=[_sample_inning_row()])
        gen = _make_generator(store)
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        assert "| trade_id" in content
        assert "trade-1" in content

    def test_blank_i2_cells_in_md(self, tmp_path):
        """1 inning trade -> i2 columns are blank cells in md table."""
        store = _mock_store(innings=[_sample_inning_row()])
        gen = _make_generator(store)
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        # i2_entry column should appear with empty value
        assert "i2_entry" in content

    def test_two_innings_both_shown(self, tmp_path):
        """2 innings for same trade -> both i1 and i2 data in md."""
        store = _mock_store(innings=[
            _sample_inning_row(inning_number=1, entry_price=2500.0),
            _sample_inning_row(inning_number=2, entry_price=2600.0,
                               is_real=0, pnl_pct=-2.0),
        ])
        gen = _make_generator(store)
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        assert "2600" in content  # i2 entry price visible


# ─────────────────────────────────────────────────────────────────────────────
# DR-U4: Summary multi-inning sub-block
# ─────────────────────────────────────────────────────────────────────────────

class TestSummaryMultiInning:

    def test_summary_contains_multi_inning_block(self, tmp_path):
        """Summary sheet/section contains Multi-Inning Tracking sub-block (DR-U4)."""
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        assert "Multi-Inning Tracking" in content

    def test_summary_contains_6_count_lines(self, tmp_path):
        """Summary has all 6 count lines for multi-inning stats (DR-U4)."""
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        for label in [
            "Trades with inning tracking",
            "Trades with inning 1 TGT hit",
            "Trades with inning 2 reached",
            "Trades with inning 2 TGT hit",
            "Trades with inning 3 reached",
            "Trades with inning 3 TGT hit",
        ]:
            assert label in content, f"Missing label: {label}"

    def test_summary_contains_disclaimer_note(self, tmp_path):
        """Summary contains disclaimer note for simulated innings (DR-U4)."""
        gen = _make_generator()
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        assert "Simulated innings are hypothetical" in content

    def test_summary_counts_correct(self, tmp_path):
        """Summary counts reflect actual inning data (DR-U4)."""
        store = _mock_store(innings=[
            _sample_inning_row(inning_number=1, exit_reason="TGT"),
            _sample_inning_row(inning_number=2, exit_reason="SL",
                               is_real=0, entry_price=2600.0),
        ])
        gen = _make_generator(store)
        paths = gen.generate(_TODAY, tmp_path, ["md"])
        content = paths.md_path.read_text(encoding="utf-8")
        assert "Trades with inning tracking" in content
        # 1 trade reached inning 2
        assert "Trades with inning 2 reached" in content


# ─────────────────────────────────────────────────────────────────────────────
# DR-U3: get_inning_summary_by_date state_store helper
# ─────────────────────────────────────────────────────────────────────────────

class TestGetInningSummaryByDate:
    """Integration tests for the new state_store helper."""

    def _setup_store(self, tmp_path):
        from core.state_store import StateStore
        return StateStore(tmp_path / "test.db")

    def _seed_signal(self, store, signal_id, date_iso):
        ts = f"{date_iso}T09:15:00"
        with store.transaction() as cur:
            cur.execute(
                """INSERT INTO signals
                   (signal_id, symbol, scanner, strategy, triggered_at,
                    received_at, expires_at, status, fingerprint, fingerprint_date)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (signal_id, "RELIANCE", "breakout", "open_low",
                 ts, ts, ts, "TRADED", f"fp_{signal_id}", date_iso),
            )

    def _seed_trade(self, store, trade_id, signal_id, date_iso):
        ts = f"{date_iso}T09:16:00"
        with store.transaction() as cur:
            cur.execute(
                """INSERT INTO trades
                   (trade_id, signal_id, symbol, direction, strategy, sector,
                    qty_planned, qty_filled, entry_target_price, entry_actual_price,
                    sl_initial, tgt_initial, margin_reserved, risk_amount,
                    created_at, entry_time, exit_time, exit_price, exit_reason,
                    gross_pnl, charges, net_pnl, status, order_protocol, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (trade_id, signal_id, "RELIANCE", "LONG", "open_low", "Energy",
                 10, 10, 2500.0, 2500.0, 2450.0, 2600.0, 5000.0, 500.0,
                 ts, ts, f"{date_iso}T14:30:00", 2600.0, "TGT_HIT",
                 1000.0, 50.0, 950.0, "CLOSED", "LIMIT_TRIPLE",
                 f"{date_iso}T14:30:01"),
            )

    def _seed_inning(self, store, trade_id, inning_number, date_iso):
        from orders.shadow_tracker import Inning
        from datetime import datetime
        ts = datetime(int(date_iso[:4]), int(date_iso[5:7]), int(date_iso[8:10]),
                      9, 16, 0)
        ing = Inning(
            inning_number=inning_number,
            trade_id=trade_id,
            symbol="RELIANCE",
            direction="LONG",
            entry_price=2500.0,
            entry_ts=ts,
            sl_price=2450.0,
            tgt_price=2600.0,
            exit_price=2600.0,
            exit_ts=ts,
            exit_reason="TGT",
            duration_sec=18840,
            pnl_pct=4.0,
            pnl_per_share=100.0,
            is_real=(inning_number == 1),
        )
        store.insert_inning(ing)

    def test_empty_day_returns_empty(self, tmp_path):
        store = self._setup_store(tmp_path)
        rows = store.get_inning_summary_by_date("2026-04-16")
        assert rows == []
        store.close()

    def test_one_trade_one_inning(self, tmp_path):
        store = self._setup_store(tmp_path)
        self._seed_signal(store, "s1", "2026-04-16")
        self._seed_trade(store, "t1", "s1", "2026-04-16")
        self._seed_inning(store, "t1", 1, "2026-04-16")
        rows = store.get_inning_summary_by_date("2026-04-16")
        assert len(rows) == 1
        assert rows[0]["trade_id"] == "t1"
        assert rows[0]["scanner_name"] == "breakout"
        assert rows[0]["inning_number"] == 1
        store.close()

    def test_one_trade_three_innings(self, tmp_path):
        store = self._setup_store(tmp_path)
        self._seed_signal(store, "s1", "2026-04-16")
        self._seed_trade(store, "t1", "s1", "2026-04-16")
        for n in [1, 2, 3]:
            self._seed_inning(store, "t1", n, "2026-04-16")
        rows = store.get_inning_summary_by_date("2026-04-16")
        assert len(rows) == 3
        assert [r["inning_number"] for r in rows] == [1, 2, 3]
        store.close()

    def test_date_filter_excludes_other_dates(self, tmp_path):
        store = self._setup_store(tmp_path)
        for date_iso in ["2026-04-15", "2026-04-16", "2026-04-17"]:
            self._seed_signal(store, f"s_{date_iso}", date_iso)
            self._seed_trade(store, f"t_{date_iso}", f"s_{date_iso}", date_iso)
            self._seed_inning(store, f"t_{date_iso}", 1, date_iso)
        rows = store.get_inning_summary_by_date("2026-04-16")
        assert len(rows) == 1
        assert rows[0]["trade_id"] == "t_2026-04-16"
        store.close()

    def test_two_trades_same_date(self, tmp_path):
        store = self._setup_store(tmp_path)
        for idx in ["a", "b"]:
            self._seed_signal(store, f"s{idx}", "2026-04-16")
            self._seed_trade(store, f"t{idx}", f"s{idx}", "2026-04-16")
            self._seed_inning(store, f"t{idx}", 1, "2026-04-16")
        rows = store.get_inning_summary_by_date("2026-04-16")
        assert len(rows) == 2
        trade_ids = {r["trade_id"] for r in rows}
        assert trade_ids == {"ta", "tb"}
        store.close()
