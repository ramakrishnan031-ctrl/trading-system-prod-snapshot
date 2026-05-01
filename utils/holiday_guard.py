"""
utils/holiday_guard.py -- Trading System v2

Purpose:
    Lightweight holiday/weekend checker that runs BEFORE logger setup.
    Reads nse_holidays_<year>.yaml directly (no Pydantic, no logging).

Locked Design Decisions:
    SU6 -- is_trading_day() and next_trading_day() as standalone functions.
           Takes config_dir: Path; derives YAML filename from year.
           Returns False for Saturday/Sunday regardless of holiday list.
           Raises FileNotFoundError if the holiday YAML is absent.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import yaml


def _load_holiday_set(config_dir: Path, year: int) -> frozenset:
    """
    Load and return holiday dates for the given year as a frozenset[date].

    Supports two YAML entry formats:
      - Plain string: "2026-01-26"
      - Dict with date key: {date: "2026-01-26", name: "Republic Day"}

    Raises:
        FileNotFoundError: if the YAML file does not exist.
        ValueError: if a date entry cannot be parsed as YYYY-MM-DD.
    """
    yaml_path = config_dir / f"nse_holidays_{year}.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(
            f"Holiday file not found: {yaml_path}. "
            f"Expected nse_holidays_{year}.yaml in {config_dir}."
        )

    with open(yaml_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    holidays_raw = (data or {}).get("holidays") or []
    result: set = set()
    for entry in holidays_raw:
        if isinstance(entry, str):
            result.add(date.fromisoformat(entry))
        elif isinstance(entry, dict) and "date" in entry:
            result.add(date.fromisoformat(str(entry["date"])))
        else:
            raise ValueError(f"Unrecognised holiday entry format: {entry!r}")

    return frozenset(result)


def is_trading_day(today: date, config_dir: Path) -> bool:
    """
    Return True if today is a trading day (weekday AND not an NSE holiday).

    Raises:
        FileNotFoundError: if nse_holidays_<year>.yaml is absent.
    """
    if today.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    holiday_set = _load_holiday_set(config_dir, today.year)
    return today not in holiday_set


def next_trading_day(from_date: date, config_dir: Path) -> date:
    """
    Return the next trading day after from_date, skipping weekends and holidays.

    Raises:
        FileNotFoundError: if a required holiday YAML is absent.
    """
    d = from_date + timedelta(days=1)
    while True:
        if d.weekday() < 5:
            holiday_set = _load_holiday_set(config_dir, d.year)
            if d not in holiday_set:
                return d
        d += timedelta(days=1)


def get_holiday_name(today: date, config_dir: Path) -> str | None:
    """
    Return the holiday name for today, or None if not a listed holiday.

    For weekends, returns None (caller should handle weekend separately).
    """
    yaml_path = config_dir / f"nse_holidays_{today.year}.yaml"
    if not yaml_path.exists():
        return None

    with open(yaml_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    for entry in (data or {}).get("holidays") or []:
        if isinstance(entry, dict) and "date" in entry:
            if date.fromisoformat(str(entry["date"])) == today:
                return entry.get("name")
    return None
