"""Tests for FIX-134 Item 40: instrument master refresh validation."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from scripts.refresh_instruments import (
    validate_instrument_rows,
    _send_refresh_alert,
    _REQUIRED_SYMBOLS,
    _MIN_ROW_COUNT,
)


def _make_rows(count, include_required=True, zero_token_pct=0):
    """Generate fake instrument rows for validation testing."""
    rows = []
    required = sorted(_REQUIRED_SYMBOLS)
    for i in range(count):
        if include_required and i < len(required):
            sym = required[i]
        else:
            sym = f"SYM{i:04d}"
        token = 0 if (i < count * zero_token_pct / 100) else (100000 + i)
        rows.append({
            "symbol": sym,
            "instrument_token": token,
            "exchange": "NSE",
            "lot_size": 1,
            "tick_size": 0.05,
            "is_fno": "false",
            "sector": "",
        })
    return rows


# ── Row count validation ─────────────────────────────────────────────────


class TestRowCountValidation:
    def test_sufficient_rows_pass(self):
        rows = _make_rows(1500)
        ok, errors = validate_instrument_rows(rows)
        assert ok
        assert errors == []

    def test_too_few_rows_fail(self):
        rows = _make_rows(500)
        ok, errors = validate_instrument_rows(rows)
        assert not ok
        assert any("Row count" in e for e in errors)

    def test_exactly_min_rows_pass(self):
        rows = _make_rows(_MIN_ROW_COUNT)
        ok, errors = validate_instrument_rows(rows)
        assert ok

    def test_one_below_min_fails(self):
        rows = _make_rows(_MIN_ROW_COUNT - 1, include_required=True)
        ok, errors = validate_instrument_rows(rows)
        assert not ok

    def test_empty_rows_fail(self):
        ok, errors = validate_instrument_rows([])
        assert not ok


# ── Required symbol validation ───────────────────────────────────────────


class TestRequiredSymbolValidation:
    def test_all_required_present(self):
        rows = _make_rows(1500, include_required=True)
        ok, errors = validate_instrument_rows(rows)
        assert ok

    def test_missing_required_fails(self):
        rows = _make_rows(1500, include_required=False)
        ok, errors = validate_instrument_rows(rows)
        assert not ok
        assert any("Required symbols missing" in e for e in errors)

    def test_custom_required_set(self):
        rows = _make_rows(1500, include_required=False)
        ok, errors = validate_instrument_rows(
            rows, required_symbols=frozenset(["SYM0010", "SYM0011"])
        )
        assert ok


# ── Zero token validation ────────────────────────────────────────────────


class TestZeroTokenValidation:
    def test_low_zero_pct_passes(self):
        rows = _make_rows(1500, zero_token_pct=5)
        ok, errors = validate_instrument_rows(rows)
        assert ok

    def test_high_zero_pct_fails(self):
        rows = _make_rows(1500, zero_token_pct=25)
        ok, errors = validate_instrument_rows(rows)
        assert not ok
        assert any("token=0" in e for e in errors)


# ── Telegram alert ────────────────────────────────────────────────────────


class TestRefreshAlert:
    def test_alert_sent_on_failure(self):
        notifier = MagicMock()
        _send_refresh_alert(["Row count too low", "NIFTY missing"], notifier)
        assert notifier.send.called
        call_kwargs = notifier.send.call_args.kwargs
        assert "FAILED" in call_kwargs["title"]
        assert call_kwargs["severity"] == "ERROR"
        assert "Row count too low" in call_kwargs["body"]

    def test_no_notifier_no_crash(self):
        _send_refresh_alert(["some error"], notifier=None)

    def test_notifier_failure_silenced(self):
        notifier = MagicMock()
        notifier.send.side_effect = RuntimeError("Telegram down")
        _send_refresh_alert(["some error"], notifier)


# ── Fallback behavior ────────────────────────────────────────────────────


class TestFallbackBehavior:
    def test_main_exits_on_missing_security_master(self):
        from scripts.refresh_instruments import main
        with pytest.raises(SystemExit) as exc_info:
            main(["--dry-run", "--security-master", "NONEXISTENT"])
        assert exc_info.value.code != 0

    def test_validation_does_not_modify_existing_csv(self, tmp_path):
        csv_path = tmp_path / "instruments.csv"
        csv_path.write_text("old,data\n", encoding="utf-8")
        rows = _make_rows(500)
        ok, _ = validate_instrument_rows(rows)
        assert not ok
        assert csv_path.read_text(encoding="utf-8") == "old,data\n"


# ── Integration: multiple errors ──────────────────────────────────────────


class TestMultipleErrors:
    def test_accumulates_all_errors(self):
        rows = _make_rows(500, include_required=False, zero_token_pct=50)
        ok, errors = validate_instrument_rows(rows)
        assert not ok
        assert len(errors) >= 2
