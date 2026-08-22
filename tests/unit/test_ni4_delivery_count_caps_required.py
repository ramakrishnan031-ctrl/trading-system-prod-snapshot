"""
tests/unit/test_ni4_delivery_count_caps_required.py

NI-4 (22-Aug-2026) — the two remaining SILENT delivery defaults.

`max_open_delivery_positions: int = 3` and `max_daily_delivery_trades: int = 5`
carried schema defaults, so deleting either key from the YAML BOOTED the system on
a hardcoded delivery cap instead of refusing. That is the same defect class fix
item 1 removed for the five delivery pct keys — these are the two it did not cover.

⛔ THE VALUES DO NOT CHANGE. 3 and 5 are in the YAML and stay there. They are the
delivery book's own caps, deliberately TIGHTER than the intraday twins (5 / 10).
This file asserts that too, because "fill the delivery setting from intraday" would
have written 5 and 10 here and loosened two live risk limits.

The rejection is asserted through the line the BOOT actually logs — main's
_config_error_detail — not through the exception repr, because the operator's only
artifact on a failed 08:15 boot is that CRITICAL line.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from core.config_loader import RiskConfig, load_all          # noqa: E402
from core.exceptions import ConfigSchemaError                # noqa: E402

_KEYS = ("max_open_delivery_positions", "max_daily_delivery_trades")


def _load_mutated(mutate) -> str:
    """Copy config/, apply `mutate` to the parsed system_config, load, return the
    boot's rendered error line. Raises AssertionError if the load SUCCEEDS."""
    import main as m

    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "config"
        shutil.copytree(_REPO / "config", cfg)
        raw = yaml.safe_load((cfg / "system_config.yaml").read_text(encoding="utf-8"))
        mutate(raw)
        (cfg / "system_config.yaml").write_text(
            yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        try:
            load_all(cfg)
        except ConfigSchemaError as exc:
            return m._config_error_detail(exc)
    raise AssertionError("config loaded when it should have been REJECTED")


def test_control_an_untouched_copy_still_loads() -> None:
    """Without this every rejection below could be an artifact of copying/round-tripping."""
    with pytest.raises(AssertionError, match="loaded when it should"):
        _load_mutated(lambda raw: None)


def test_the_schema_fields_are_required_with_no_default() -> None:
    for key in _KEYS:
        field = RiskConfig.model_fields[key]
        assert field.is_required(), f"{key} still carries a schema default"


@pytest.mark.parametrize("key", _KEYS)
def test_missing_key_rejects_at_startup_naming_the_key(key: str) -> None:
    detail = _load_mutated(lambda raw: raw["risk"].pop(key))
    assert f"risk.{key}" in detail, detail
    assert "Field required" in detail, detail


@pytest.mark.parametrize("key", _KEYS)
def test_null_key_rejects_at_startup_naming_the_key_and_the_reason(key: str) -> None:
    detail = _load_mutated(lambda raw: raw["risk"].__setitem__(key, None))
    assert f"risk.{key}" in detail, detail
    assert "does NOT fall back" in detail, detail


@pytest.mark.parametrize("key", _KEYS)
def test_a_zero_or_negative_cap_still_rejects(key: str) -> None:
    """The pre-existing >= 1 rule must survive being split into its own validator."""
    detail = _load_mutated(lambda raw: raw["risk"].__setitem__(key, 0))
    assert f"risk.{key}" in detail, detail
    assert ">= 1" in detail, detail


def test_no_silent_3_or_5_anywhere_in_the_config_path() -> None:
    """A missing key must NOT produce a working AppConfig on the built-in numbers.

    Stated as its own assertion because "it raised" and "it did not silently default"
    are different claims, and only the second is the defect.
    """
    for key, silent_value in (("max_open_delivery_positions", 3),
                              ("max_daily_delivery_trades", 5)):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            shutil.copytree(_REPO / "config", cfg)
            raw = yaml.safe_load((cfg / "system_config.yaml").read_text(encoding="utf-8"))
            raw["risk"].pop(key)
            (cfg / "system_config.yaml").write_text(
                yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
            try:
                app = load_all(cfg)
            except ConfigSchemaError:
                continue
            raise AssertionError(
                f"{key} silently defaulted to {getattr(app.system.risk, key)!r} "
                f"(the built-in was {silent_value})")


def test_the_shipped_values_are_unchanged_and_are_not_the_intraday_ones() -> None:
    """Behaviour-neutrality, and the JOB 1 trap named explicitly.

    Filling these "from intraday" would have written 5 and 10 -- loosening the
    delivery slot cap 3->5 and the daily cap 5->10 on live money. They are the
    delivery book's own numbers and stay exactly as they are.
    """
    risk = load_all(_REPO / "config").system.risk
    assert risk.max_open_delivery_positions == 3
    assert risk.max_daily_delivery_trades == 5
    assert risk.max_open_positions == 5
    assert risk.max_daily_trades == 10
    assert risk.max_open_delivery_positions < risk.max_open_positions
    assert risk.max_daily_delivery_trades < risk.max_daily_trades
