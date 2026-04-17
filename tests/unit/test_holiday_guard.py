"""
tests/unit/test_holiday_guard.py -- Trading System v2

Tests for utils/holiday_guard.py (SU6, SU20).

Run: python -m pytest tests/unit/test_holiday_guard.py -v
Or:  python tests/unit/test_holiday_guard.py  (standalone)
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.holiday_guard import is_trading_day, next_trading_day


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_holidays(config_dir: Path, year: int, dates: list) -> None:
    """Write a minimal nse_holidays_<year>.yaml with plain-string entries."""
    lines = ["holidays:\n"]
    for d in dates:
        lines.append(f'  - "{d}"\n')
    (config_dir / f"nse_holidays_{year}.yaml").write_text("".join(lines), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# is_trading_day
# ─────────────────────────────────────────────────────────────────────────────

def test_trading_weekday_returns_true(tmp_path: Path) -> None:
    """A normal Wednesday with no holidays configured returns True."""
    _write_holidays(tmp_path, 2026, [])
    assert is_trading_day(date(2026, 4, 15), tmp_path) is True  # Wednesday


def test_saturday_returns_false(tmp_path: Path) -> None:
    """Saturday is never a trading day, regardless of holiday config."""
    _write_holidays(tmp_path, 2026, [])
    assert is_trading_day(date(2026, 4, 18), tmp_path) is False  # Saturday


def test_sunday_returns_false(tmp_path: Path) -> None:
    """Sunday is never a trading day."""
    _write_holidays(tmp_path, 2026, [])
    assert is_trading_day(date(2026, 4, 19), tmp_path) is False  # Sunday


def test_configured_nse_holiday_returns_false(tmp_path: Path) -> None:
    """A weekday that is in the holiday list returns False."""
    _write_holidays(tmp_path, 2026, ["2026-01-26"])
    assert is_trading_day(date(2026, 1, 26), tmp_path) is False  # Republic Day (Monday)


def test_missing_yaml_raises_file_not_found(tmp_path: Path) -> None:
    """Missing holiday YAML raises FileNotFoundError with a clear message."""
    with pytest.raises(FileNotFoundError, match="nse_holidays_2026"):
        is_trading_day(date(2026, 4, 15), tmp_path)


# ─────────────────────────────────────────────────────────────────────────────
# next_trading_day
# ─────────────────────────────────────────────────────────────────────────────

def test_next_trading_day_skips_weekend(tmp_path: Path) -> None:
    """Friday -> next trading day is Monday (skips Sat + Sun)."""
    _write_holidays(tmp_path, 2026, [])
    result = next_trading_day(date(2026, 4, 17), tmp_path)  # Friday
    assert result == date(2026, 4, 20)  # Monday


def test_next_trading_day_skips_holiday(tmp_path: Path) -> None:
    """If the day after is a holiday, keeps walking forward."""
    # 2026-01-26 is Monday (Republic Day); next trading day is Tuesday 2026-01-27
    _write_holidays(tmp_path, 2026, ["2026-01-26"])
    result = next_trading_day(date(2026, 1, 25), tmp_path)  # Sunday
    assert result == date(2026, 1, 27)  # Tuesday (Monday was holiday)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback

    tests = [
        test_trading_weekday_returns_true,
        test_saturday_returns_false,
        test_sunday_returns_false,
        test_configured_nse_holiday_returns_false,
        test_missing_yaml_raises_file_not_found,
        test_next_trading_day_skips_weekend,
        test_next_trading_day_skips_holiday,
    ]

    passed = failed = 0
    for fn in tests:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                fn(Path(tmp))
                print(f"  OK  {fn.__name__}")
                passed += 1
            except Exception:
                print(f"  FAIL  {fn.__name__}")
                traceback.print_exc()
                failed += 1

    print(f"\n{passed}/{passed + failed} passed")
    if failed:
        sys.exit(1)
