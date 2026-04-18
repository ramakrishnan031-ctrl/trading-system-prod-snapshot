"""
tests/unit/test_strategies.py  -  Unit tests for strategies/schema.py
and strategies/loader.py (S1-S15)
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from core.exceptions import ConfigMissingError, ConfigSchemaError

# ── Helpers ──────────────────────────────────────────────────────────────────

# Path to the real strategy files
_STRATEGIES_DIR = Path(__file__).parent.parent.parent / "config" / "strategies"
_SCAN_WEBHOOK_MAP = Path(__file__).parent.parent.parent / "config" / "scan_webhook_map.yaml"

_ALL_15_NAMES = [
    "open_low_breakout_long",
    "first_pullback_long",
    "vwap_bounce_long",
    "gap_go_long",
    "gap_fade_long",
    "range_breakout_long",
    "open_high_breakdown_short",
    "first_pullback_short",
    "vwap_rejection_short",
    "gap_go_short",
    "gap_fade_short",
    "range_breakout_short",
    "positional_momentum_long",
    "positional_sector_rotation",
    "positional_swing_long",
]


def _valid_data() -> dict:
    """Minimal valid StrategyConfig dict (intraday LONG, FIXED_PCT SL/RR TGT)."""
    return {
        "name": "test_strategy",
        "display_name": "Test Strategy",
        "description": "A test strategy",
        "direction": "LONG",
        "intent": "INTRADAY",
        "order_protocol": "CO_PLUS_TGT",
        "entry_method": "LIMIT",
        "entry_offset_pct": 0.001,
        "sl_method": "FIXED_PCT",
        "sl_pct": 0.01,
        "sl_atr_multiplier": 1.5,
        "sl_min_pct": 0.003,
        "sl_max_pct": 0.05,
        "tgt_method": "RISK_REWARD",
        "tgt_pct": 0.0,
        "tgt_risk_reward": 2.0,
        "tgt_atr_multiplier": 2.5,
        "smart_tgt_enabled": True,
        "smart_tgt_trail_trigger_pct": 0.005,
        "smart_tgt_trail_step_pct": 0.003,
        "pullback_wait_enabled": True,
        "pullback_wait_tolerance_pct": 0.005,
        "pullback_wait_timeout_sec": 180,
        "min_score": 0,
        "min_volume_surge": 1.3,
        "min_adr_pct": 0.005,
        "max_spread_pct": 0.005,
        "max_risk_pct": 0.0,
        "lot_size": 1,
        "entry_start_time": "09:30",
        "entry_end_time": "13:30",
        "active_days": ["MON", "TUE", "WED", "THU", "FRI"],
    }


def _assert_raises_config_schema_error(data: dict) -> None:
    from strategies.schema import StrategyConfig
    try:
        StrategyConfig(**data)
        raise AssertionError("Expected ConfigSchemaError or ValidationError, none raised")
    except (ConfigSchemaError, Exception) as exc:
        # Pydantic raises ValidationError; validate_strategy wraps as ConfigSchemaError
        # For direct StrategyConfig(**data), Pydantic raises ValidationError
        name = type(exc).__name__
        assert name in ("ValidationError", "ConfigSchemaError"), (
            "Expected ValidationError or ConfigSchemaError, got %s: %s" % (name, exc)
        )


def _write_yaml(tmpdir: str, filename: str, content: str) -> Path:
    p = Path(tmpdir) / filename
    p.write_text(content, encoding="utf-8")
    return p


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_import_schema_and_loader() -> None:
    from strategies.schema import StrategyConfig, validate_strategy
    from strategies.loader import StrategyLoader
    assert StrategyConfig is not None
    assert validate_strategy is not None
    assert StrategyLoader is not None
    print("  OK import_schema_and_loader")


def test_valid_intraday_long_all_fields_populated() -> None:
    from strategies.schema import StrategyConfig
    cfg = StrategyConfig(**_valid_data())
    assert cfg.name == "test_strategy"
    assert cfg.direction == "LONG"
    assert cfg.intent == "INTRADAY"
    assert cfg.order_protocol == "CO_PLUS_TGT"
    assert cfg.sl_method == "FIXED_PCT"
    assert cfg.sl_pct == 0.01
    assert cfg.tgt_method == "RISK_REWARD"
    assert cfg.tgt_risk_reward == 2.0
    assert cfg.smart_tgt_enabled is True
    assert cfg.pullback_wait_enabled is True
    assert cfg.lot_size == 1
    assert cfg.active_days == ["MON", "TUE", "WED", "THU", "FRI"]
    print("  OK valid_intraday_long_all_fields_populated")


def test_missing_required_field_raises_error() -> None:
    data = _valid_data()
    del data["direction"]  # required, no default
    _assert_raises_config_schema_error(data)
    print("  OK missing_required_field_raises_error")


def test_unknown_field_raises_config_schema_error() -> None:
    """S4: extra='forbid' must reject unknown keys."""
    data = _valid_data()
    data["unknown_field_xyz"] = "bad"
    _assert_raises_config_schema_error(data)
    print("  OK unknown_field_raises_config_schema_error")


def test_invalid_direction_raises_error() -> None:
    data = _valid_data()
    data["direction"] = "BOTH"
    _assert_raises_config_schema_error(data)
    print("  OK invalid_direction_raises_error")


def test_invalid_intent_raises_error() -> None:
    data = _valid_data()
    data["intent"] = "SWING"
    _assert_raises_config_schema_error(data)
    print("  OK invalid_intent_raises_error")


def test_invalid_order_protocol_raises_error() -> None:
    data = _valid_data()
    data["order_protocol"] = "NAKED_MIS"
    _assert_raises_config_schema_error(data)
    print("  OK invalid_order_protocol_raises_error")


def test_sl_pct_zero_when_fixed_pct_raises_error() -> None:
    """S4: sl_pct must be > 0 when sl_method=FIXED_PCT."""
    data = _valid_data()
    data["sl_method"] = "FIXED_PCT"
    data["sl_pct"] = 0.0
    _assert_raises_config_schema_error(data)
    print("  OK sl_pct_zero_when_fixed_pct_raises_error")


def test_sl_min_greater_than_sl_pct_raises_error() -> None:
    """S4: sl_min_pct <= sl_pct when FIXED_PCT."""
    data = _valid_data()
    data["sl_method"] = "FIXED_PCT"
    data["sl_pct"] = 0.005
    data["sl_min_pct"] = 0.01   # min > sl_pct -> invalid
    _assert_raises_config_schema_error(data)
    print("  OK sl_min_greater_than_sl_pct_raises_error")


def test_tgt_pct_zero_when_fixed_pct_raises_error() -> None:
    """S4: tgt_pct must be > 0 when tgt_method=FIXED_PCT."""
    data = _valid_data()
    data["tgt_method"] = "FIXED_PCT"
    data["tgt_pct"] = 0.0
    _assert_raises_config_schema_error(data)
    print("  OK tgt_pct_zero_when_fixed_pct_raises_error")


def test_entry_start_geq_entry_end_raises_error() -> None:
    """S4: entry_start_time < entry_end_time."""
    data = _valid_data()
    data["entry_start_time"] = "13:30"
    data["entry_end_time"] = "09:30"
    _assert_raises_config_schema_error(data)
    # Also test equal times
    data2 = _valid_data()
    data2["entry_start_time"] = "12:00"
    data2["entry_end_time"] = "12:00"
    _assert_raises_config_schema_error(data2)
    print("  OK entry_start_geq_entry_end_raises_error")


def test_invalid_active_days_raises_error() -> None:
    """S4: active_days must be a subset of valid weekday names."""
    data = _valid_data()
    data["active_days"] = ["MON", "SAT"]  # SAT invalid
    _assert_raises_config_schema_error(data)
    # Empty list also invalid
    data2 = _valid_data()
    data2["active_days"] = []
    _assert_raises_config_schema_error(data2)
    print("  OK invalid_active_days_raises_error")


def test_negative_float_raises_error() -> None:
    """S4: negative ATR multiplier rejected."""
    data = _valid_data()
    data["sl_atr_multiplier"] = -1.5
    _assert_raises_config_schema_error(data)
    print("  OK negative_float_raises_error")


def test_lot_size_less_than_one_raises_error() -> None:
    """S4: lot_size >= 1."""
    data = _valid_data()
    data["lot_size"] = 0
    _assert_raises_config_schema_error(data)
    print("  OK lot_size_less_than_one_raises_error")


def test_validate_strategy_valid_file_returns_config() -> None:
    from strategies.schema import validate_strategy
    cfg = validate_strategy(_STRATEGIES_DIR / "open_low_breakout_long.yaml")
    assert cfg.name == "open_low_breakout_long"
    assert cfg.direction == "LONG"
    print("  OK validate_strategy_valid_file_returns_config")


def test_validate_strategy_invalid_file_raises_config_schema_error() -> None:
    from strategies.schema import validate_strategy
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_yaml = _write_yaml(tmpdir, "bad.yaml", "name: test\nbad_field: oops\n")
        try:
            validate_strategy(bad_yaml)
            raise AssertionError("Expected ConfigSchemaError")
        except ConfigSchemaError:
            pass
    print("  OK validate_strategy_invalid_file_raises_config_schema_error")


def test_validate_strategy_missing_file_raises_config_schema_error() -> None:
    from strategies.schema import validate_strategy
    try:
        validate_strategy(Path("/nonexistent/path/strategy.yaml"))
        raise AssertionError("Expected ConfigSchemaError")
    except ConfigSchemaError:
        pass
    print("  OK validate_strategy_missing_file_raises_config_schema_error")


def test_load_all_strategies_loads_15() -> None:
    from strategies.loader import StrategyLoader
    loader = StrategyLoader()
    strategies = loader.load_all_strategies(_STRATEGIES_DIR)
    assert len(strategies) == 15, "Expected 15 strategies, got %d" % len(strategies)
    for name in _ALL_15_NAMES:
        assert name in strategies, "Missing strategy: %s" % name
    print("  OK load_all_strategies_loads_15: loaded %d" % len(strategies))


def test_load_all_strategies_corrupt_file_raises_error() -> None:
    """S9: any invalid file -> ConfigSchemaError, no partial load."""
    from strategies.loader import StrategyLoader
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write one valid YAML
        good_content = """
name: good_strategy
display_name: "Good"
description: "OK"
direction: "LONG"
intent: "INTRADAY"
order_protocol: "CO_PLUS_TGT"
entry_method: "LIMIT"
entry_offset_pct: 0.001
sl_method: "FIXED_PCT"
sl_pct: 0.01
sl_atr_multiplier: 1.5
sl_min_pct: 0.003
sl_max_pct: 0.05
tgt_method: "RISK_REWARD"
tgt_pct: 0.0
tgt_risk_reward: 2.0
tgt_atr_multiplier: 2.5
smart_tgt_enabled: true
smart_tgt_trail_trigger_pct: 0.005
smart_tgt_trail_step_pct: 0.003
pullback_wait_enabled: false
pullback_wait_tolerance_pct: 0.005
pullback_wait_timeout_sec: 180
min_score: 0
min_volume_surge: 1.3
min_adr_pct: 0.005
max_spread_pct: 0.005
max_risk_pct: 0.0
lot_size: 1
entry_start_time: "09:30"
entry_end_time: "13:30"
active_days: [MON, TUE, WED, THU, FRI]
"""
        _write_yaml(tmpdir, "good_strategy.yaml", good_content)
        # Write one corrupt YAML (unknown field)
        _write_yaml(tmpdir, "bad_strategy.yaml", "name: bad\nunknown_field: oops\n")

        loader = StrategyLoader()
        try:
            loader.load_all_strategies(Path(tmpdir))
            raise AssertionError("Expected ConfigSchemaError")
        except ConfigSchemaError:
            pass
    print("  OK load_all_strategies_corrupt_file_raises_error")


def test_get_strategy_existing_name_returns_config() -> None:
    from strategies.loader import StrategyLoader
    loader = StrategyLoader()
    loader.load_all_strategies(_STRATEGIES_DIR)
    cfg = loader.get_strategy("gap_go_long")
    assert cfg.name == "gap_go_long"
    assert cfg.direction == "LONG"
    assert cfg.pullback_wait_enabled is False  # S13: gap_go has no pullback
    print("  OK get_strategy_existing_name_returns_config")


def test_get_strategy_missing_name_raises_config_missing_error() -> None:
    from strategies.loader import StrategyLoader
    loader = StrategyLoader()
    loader.load_all_strategies(_STRATEGIES_DIR)
    try:
        loader.get_strategy("nonexistent_strategy_xyz")
        raise AssertionError("Expected ConfigMissingError")
    except ConfigMissingError:
        pass
    print("  OK get_strategy_missing_name_raises_config_missing_error")


def test_scan_webhook_map_all_15_strategies_exist() -> None:
    """S10: all entries in scan_webhook_map must resolve to loaded YAML files."""
    from strategies.loader import StrategyLoader
    loader = StrategyLoader()
    # Should NOT raise — all 15 referenced strategies exist
    strategies = loader.load_all_strategies(
        _STRATEGIES_DIR,
        scan_webhook_map_path=_SCAN_WEBHOOK_MAP,
    )
    assert len(strategies) == 15
    print("  OK scan_webhook_map_all_15_strategies_exist")


def test_scan_webhook_map_missing_strategy_raises_error() -> None:
    """S10: scan_webhook_map referencing a missing strategy -> ConfigMissingError."""
    from strategies.loader import StrategyLoader
    import shutil
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        strat_dir = tmppath / "strategies"
        strat_dir.mkdir()
        # Copy one good strategy YAML into strategies subdir
        shutil.copy(_STRATEGIES_DIR / "open_low_breakout_long.yaml", strat_dir)
        # Write a scan_webhook_map (outside strat_dir) referencing a non-existent strategy
        bad_map = tmppath / "scan_webhook_map.yaml"
        bad_map.write_text(
            "scanners:\n"
            "  some_scanner:\n"
            "    strategy: nonexistent_strategy\n"
            "    chartink_url: https://chartink.com\n",
            encoding="utf-8",
        )
        loader = StrategyLoader()
        try:
            loader.load_all_strategies(strat_dir, scan_webhook_map_path=bad_map)
            raise AssertionError("Expected ConfigMissingError")
        except ConfigMissingError:
            pass
    print("  OK scan_webhook_map_missing_strategy_raises_error")


def test_long_strategy_direction_and_defaults() -> None:
    from strategies.loader import StrategyLoader
    loader = StrategyLoader()
    loader.load_all_strategies(_STRATEGIES_DIR)
    cfg = loader.get_strategy("open_low_breakout_long")
    assert cfg.direction == "LONG"
    assert cfg.intent == "INTRADAY"
    assert cfg.order_protocol == "CO_PLUS_TGT"
    assert cfg.sl_method == "FIXED_PCT"
    assert cfg.tgt_method == "RISK_REWARD"
    assert cfg.smart_tgt_enabled is True
    print("  OK long_strategy_direction_and_defaults")


def test_short_strategy_direction() -> None:
    from strategies.loader import StrategyLoader
    loader = StrategyLoader()
    loader.load_all_strategies(_STRATEGIES_DIR)
    for short_name in ["gap_go_short", "gap_fade_short", "open_high_breakdown_short",
                       "first_pullback_short", "vwap_rejection_short", "range_breakout_short"]:
        cfg = loader.get_strategy(short_name)
        assert cfg.direction == "SHORT", "%s should be SHORT" % short_name
    print("  OK short_strategy_direction: all 6 SHORT strategies verified")


def test_positional_strategy_fields() -> None:
    """
    S6: DELIVERY, LIMIT_TRIPLE, ATR-based SL, RISK_REWARD TGT, no smart_tgt,
    entry_end 14:30.

    BL-16: tgt_method changed from "ATR" to "RISK_REWARD" because the ATR
    branch in signal_processor._derive_target falls back to FIXED_PCT which,
    with tgt_pct=0.0, produces target == entry (guaranteed loss).
    """
    from strategies.loader import StrategyLoader
    loader = StrategyLoader()
    loader.load_all_strategies(_STRATEGIES_DIR)
    for name in ["positional_momentum_long", "positional_sector_rotation", "positional_swing_long"]:
        cfg = loader.get_strategy(name)
        assert cfg.intent == "DELIVERY", "%s intent should be DELIVERY" % name
        assert cfg.order_protocol == "LIMIT_TRIPLE", "%s should use LIMIT_TRIPLE" % name
        assert cfg.sl_method == "ATR", "%s should use ATR SL" % name
        assert cfg.tgt_method == "RISK_REWARD", "%s should use RISK_REWARD TGT (BL-16)" % name
        assert cfg.tgt_risk_reward > 0, "%s tgt_risk_reward must be > 0" % name
        assert cfg.smart_tgt_enabled is False, "%s smart_tgt should be disabled" % name
        assert cfg.pullback_wait_enabled is False, "%s pullback_wait should be disabled" % name
        assert cfg.entry_end_time == "14:30", "%s entry_end should be 14:30" % name
    print("  OK positional_strategy_fields: all 3 DELIVERY strategies verified (BL-16)")


def test_all_15_yaml_files_validate() -> None:
    """Regression: every file in config/strategies/ validates cleanly."""
    from strategies.schema import validate_strategy
    yaml_files = sorted(_STRATEGIES_DIR.glob("*.yaml"))
    assert len(yaml_files) == 15, "Expected 15 YAML files, got %d" % len(yaml_files)
    for yaml_path in yaml_files:
        try:
            cfg = validate_strategy(yaml_path)
            assert cfg.name, "name field empty in %s" % yaml_path.name
        except ConfigSchemaError as exc:
            raise AssertionError(
                "Strategy YAML %s failed validation: %s" % (yaml_path.name, exc)
            ) from exc
    print("  OK all_15_yaml_files_validate")


def test_defaults_populated_correctly() -> None:
    """S5: defaults match S13 spec for intraday long strategies."""
    from strategies.schema import validate_strategy
    cfg = validate_strategy(_STRATEGIES_DIR / "open_low_breakout_long.yaml")
    assert cfg.sl_atr_multiplier == 1.5
    assert cfg.sl_min_pct == 0.003
    assert cfg.sl_max_pct == 0.05
    assert cfg.tgt_atr_multiplier == 2.5
    assert cfg.tgt_risk_reward == 2.0
    assert cfg.smart_tgt_trail_trigger_pct == 0.005
    assert cfg.smart_tgt_trail_step_pct == 0.003
    assert cfg.pullback_wait_tolerance_pct == 0.005
    assert cfg.pullback_wait_timeout_sec == 180
    assert cfg.lot_size == 1
    assert cfg.min_score == 0      # 0 = use global
    assert cfg.max_risk_pct == 0.0  # 0.0 = use global
    print("  OK defaults_populated_correctly")


def test_gap_go_pullback_disabled() -> None:
    """S13: gap_go strategies have pullback_wait_enabled=false."""
    from strategies.loader import StrategyLoader
    loader = StrategyLoader()
    loader.load_all_strategies(_STRATEGIES_DIR)
    for name in ["gap_go_long", "gap_go_short", "gap_fade_long", "gap_fade_short",
                 "range_breakout_long", "range_breakout_short"]:
        cfg = loader.get_strategy(name)
        assert cfg.pullback_wait_enabled is False, (
            "%s should have pullback_wait_enabled=false" % name
        )
    print("  OK gap_go_pullback_disabled: 6 momentum strategies verified")


def test_atr_sl_strategy_accepts_zero_sl_pct() -> None:
    """ATR SL strategies may have sl_pct=0.0 (unused field, no validation error)."""
    from strategies.schema import validate_strategy
    cfg = validate_strategy(_STRATEGIES_DIR / "positional_momentum_long.yaml")
    assert cfg.sl_method == "ATR"
    assert cfg.sl_pct == 0.0  # 0.0 is valid when sl_method=ATR
    print("  OK atr_sl_strategy_accepts_zero_sl_pct")


def test_loader_empty_dir_raises_error() -> None:
    """load_all_strategies on empty dir -> ConfigSchemaError."""
    from strategies.loader import StrategyLoader
    with tempfile.TemporaryDirectory() as tmpdir:
        loader = StrategyLoader()
        try:
            loader.load_all_strategies(Path(tmpdir))
            raise AssertionError("Expected ConfigSchemaError")
        except ConfigSchemaError:
            pass
    print("  OK loader_empty_dir_raises_error")


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_import_schema_and_loader,
        test_valid_intraday_long_all_fields_populated,
        test_missing_required_field_raises_error,
        test_unknown_field_raises_config_schema_error,
        test_invalid_direction_raises_error,
        test_invalid_intent_raises_error,
        test_invalid_order_protocol_raises_error,
        test_sl_pct_zero_when_fixed_pct_raises_error,
        test_sl_min_greater_than_sl_pct_raises_error,
        test_tgt_pct_zero_when_fixed_pct_raises_error,
        test_entry_start_geq_entry_end_raises_error,
        test_invalid_active_days_raises_error,
        test_negative_float_raises_error,
        test_lot_size_less_than_one_raises_error,
        test_validate_strategy_valid_file_returns_config,
        test_validate_strategy_invalid_file_raises_config_schema_error,
        test_validate_strategy_missing_file_raises_config_schema_error,
        test_load_all_strategies_loads_15,
        test_load_all_strategies_corrupt_file_raises_error,
        test_get_strategy_existing_name_returns_config,
        test_get_strategy_missing_name_raises_config_missing_error,
        test_scan_webhook_map_all_15_strategies_exist,
        test_scan_webhook_map_missing_strategy_raises_error,
        test_long_strategy_direction_and_defaults,
        test_short_strategy_direction,
        test_positional_strategy_fields,
        test_all_15_yaml_files_validate,
        test_defaults_populated_correctly,
        test_gap_go_pullback_disabled,
        test_atr_sl_strategy_accepts_zero_sl_pct,
        test_loader_empty_dir_raises_error,
    ]

    passed = 0
    failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            failed += 1
            print("  FAIL %s: %s" % (fn.__name__, exc))

    print("\n" + "=" * 60)
    print("test_strategies.py: %d/%d passed" % (passed, len(tests)))
    if failed:
        print("  FAILED: %d" % failed)
    return failed


if __name__ == "__main__":
    sys.exit(run_all_tests())
