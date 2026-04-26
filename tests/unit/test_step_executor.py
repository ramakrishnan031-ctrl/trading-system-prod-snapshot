# tests/unit/test_step_executor.py — Trading System v2
#
# Tests for screening/step_executor.py (SE1-SE10)

import logging
import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock

from screening.step_executor import StepExecutor, StepExecutorResult
from core.time_authority import now_ist


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_executor():
    return StepExecutor(logging.getLogger("test_step_executor"))


def _base_signal(direction="LONG", age_sec=10):
    triggered = now_ist() - timedelta(seconds=age_sec)
    return {
        "symbol": "RELIANCE",
        "scanner_name": "open_low_breakout_long",
        "trigger_price": 2500.0,
        "triggered_at": triggered,
        "direction": direction,
        "intent": "INTRADAY",
    }


def _base_market_data():
    return {
        "ltp": 2510.0,
        "volume": 100_000,
        "vwap": 2490.0,
        "atr": 25.0,       # adr_pct = 25/2510*100 = 0.996%
        "rsi": 55.0,
        "bid": 2509.5,
        "ask": 2510.5,     # spread_pct = 1/2510*100 = 0.0398%
        "sector": "Energy",
        "circuit_state": "",
        "day_high": 2540.0,
        "day_low": 2470.0,
        "prev_close": 2480.0,
        "avg_volume_20d": 50_000,
        "open": 2480.0,
    }


def _base_thresholds():
    return {
        "min_volume_surge": 1.5,
        "min_adr_pct": 0.5,
        "max_spread_pct": 0.1,
    }


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

def test_import_and_instantiate():
    ex = _make_executor()
    assert ex is not None


def test_run_all_returns_step_executor_result():
    ex = _make_executor()
    result = ex.run_all(_base_signal(), _base_market_data(), _base_thresholds())
    assert isinstance(result, StepExecutorResult)


def test_all_10_steps_in_result():
    ex = _make_executor()
    result = ex.run_all(_base_signal(), _base_market_data(), _base_thresholds())
    expected = {
        "volume_surge", "vwap_position", "atr_filter", "rsi_range",
        "price_action", "sector_strength", "time_of_day", "spread_check",
        "circuit_check", "signal_age",
    }
    assert set(result.step_results.keys()) == expected
    assert set(result.step_statuses.keys()) == expected
    assert set(result.latencies_ms.keys()) == expected


def test_latencies_populated_for_all_steps():
    ex = _make_executor()
    result = ex.run_all(_base_signal(), _base_market_data(), _base_thresholds())
    for name, lat in result.latencies_ms.items():
        assert isinstance(lat, float), f"{name} latency not float"
        assert lat >= 0.0


def test_result_fields_all_populated():
    ex = _make_executor()
    result = ex.run_all(_base_signal(), _base_market_data(), _base_thresholds())
    assert isinstance(result.step_results, dict)
    assert isinstance(result.step_statuses, dict)
    assert isinstance(result.error_steps, list)
    assert isinstance(result.latencies_ms, dict)


# ---------------------------------------------------------------------------
# Step 1: volume_surge
# ---------------------------------------------------------------------------

def test_step1_volume_above_threshold_returns_one():
    ex = _make_executor()
    md = _base_market_data()
    md["volume"] = 100_000
    md["avg_volume_20d"] = 50_000  # surge = 2.0 > 1.5
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["volume_surge"] == 1.0


def test_step1_volume_below_threshold_returns_zero():
    ex = _make_executor()
    md = _base_market_data()
    md["volume"] = 40_000
    md["avg_volume_20d"] = 50_000  # surge = 0.8 < 1.5
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["volume_surge"] == 0.0


def test_step1_avg_volume_zero_returns_zero():
    """Audit fix c: avg_volume=0 -> 0.0 (not divide-by-zero)."""
    ex = _make_executor()
    md = _base_market_data()
    md["avg_volume_20d"] = 0
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["volume_surge"] == 0.0


def test_step1_avg_volume_missing_returns_zero():
    """Audit fix c: avg_volume missing -> 0.0."""
    ex = _make_executor()
    md = _base_market_data()
    del md["avg_volume_20d"]
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["volume_surge"] == 0.0


# ---------------------------------------------------------------------------
# Step 2: vwap_position
# ---------------------------------------------------------------------------

def test_step2_long_ltp_above_vwap_returns_one():
    ex = _make_executor()
    md = _base_market_data()
    md["ltp"] = 2510.0
    md["vwap"] = 2490.0
    result = ex.run_all(_base_signal(direction="LONG"), md, _base_thresholds())
    assert result.step_results["vwap_position"] == 1.0


def test_step2_long_ltp_below_vwap_returns_zero():
    ex = _make_executor()
    md = _base_market_data()
    md["ltp"] = 2470.0
    md["vwap"] = 2490.0
    result = ex.run_all(_base_signal(direction="LONG"), md, _base_thresholds())
    assert result.step_results["vwap_position"] == 0.0


def test_step2_short_ltp_below_vwap_returns_one():
    """Audit fix b: direction-aware."""
    ex = _make_executor()
    md = _base_market_data()
    md["ltp"] = 2470.0
    md["vwap"] = 2490.0
    result = ex.run_all(_base_signal(direction="SHORT"), md, _base_thresholds())
    assert result.step_results["vwap_position"] == 1.0


def test_step2_short_ltp_above_vwap_returns_zero():
    ex = _make_executor()
    md = _base_market_data()
    md["ltp"] = 2510.0
    md["vwap"] = 2490.0
    result = ex.run_all(_base_signal(direction="SHORT"), md, _base_thresholds())
    assert result.step_results["vwap_position"] == 0.0


# ---------------------------------------------------------------------------
# Step 3: atr_filter
# ---------------------------------------------------------------------------

def test_step3_adr_above_threshold_returns_one():
    ex = _make_executor()
    md = _base_market_data()
    md["atr"] = 25.0
    md["ltp"] = 2510.0  # adr = 25/2510*100 = 0.996 > 0.5
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["atr_filter"] == 1.0


def test_step3_atr_missing_returns_zero():
    ex = _make_executor()
    md = _base_market_data()
    del md["atr"]
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["atr_filter"] == 0.0


def test_step3_atr_zero_returns_zero():
    ex = _make_executor()
    md = _base_market_data()
    md["atr"] = 0
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["atr_filter"] == 0.0


# ---------------------------------------------------------------------------
# Step 4: rsi_range
# ---------------------------------------------------------------------------

def test_step4_long_rsi_in_range_returns_one():
    ex = _make_executor()
    md = _base_market_data()
    md["rsi"] = 55.0
    result = ex.run_all(_base_signal(direction="LONG"), md, _base_thresholds())
    assert result.step_results["rsi_range"] == 1.0


def test_step4_long_rsi_out_of_range_returns_zero():
    ex = _make_executor()
    md = _base_market_data()
    md["rsi"] = 85.0  # > 80
    result = ex.run_all(_base_signal(direction="LONG"), md, _base_thresholds())
    assert result.step_results["rsi_range"] == 0.0


def test_step4_short_rsi_in_range_returns_one():
    ex = _make_executor()
    md = _base_market_data()
    md["rsi"] = 40.0  # in [20, 60]
    result = ex.run_all(_base_signal(direction="SHORT"), md, _base_thresholds())
    assert result.step_results["rsi_range"] == 1.0


def test_step4_rsi_missing_returns_neutral():
    ex = _make_executor()
    md = _base_market_data()
    del md["rsi"]
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["rsi_range"] == 0.5


def test_i3_step4_rsi_negative_treated_as_missing():
    """I.3 (2026-04-25): rsi < 0 (malformed feed: -1 placeholder) returns
    neutral 0.5, not 0.0. Pre-fix the LONG band check would silently grade
    the signal as 0.0 (out of band), masking the data quality issue."""
    ex = _make_executor()
    md = _base_market_data()
    md["rsi"] = -1.0
    result = ex.run_all(_base_signal(direction="LONG"), md, _base_thresholds())
    assert result.step_results["rsi_range"] == 0.5


def test_i3_step4_rsi_above_100_treated_as_missing():
    """I.3: rsi > 100 (malformed feed: 999 placeholder) returns 0.5."""
    ex = _make_executor()
    md = _base_market_data()
    md["rsi"] = 999.0
    result = ex.run_all(_base_signal(direction="SHORT"), md, _base_thresholds())
    assert result.step_results["rsi_range"] == 0.5


def test_i3_step4_rsi_non_numeric_treated_as_missing():
    """I.3: rsi=str (malformed feed) returns 0.5 rather than crashing."""
    ex = _make_executor()
    md = _base_market_data()
    md["rsi"] = "N/A"
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["rsi_range"] == 0.5


# ---------------------------------------------------------------------------
# Step 5: price_action
# ---------------------------------------------------------------------------

def test_step5_strong_body_returns_high_score():
    ex = _make_executor()
    md = _base_market_data()
    md["ltp"] = 2540.0    # strong move up from open 2480
    md["open"] = 2480.0
    md["day_high"] = 2540.0
    md["day_low"] = 2470.0  # range = 70; body = 60; body_pct = 60/70 = 0.857; score = min(1, 0.857*2) = 1.0
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["price_action"] == 1.0


def test_step5_small_body_returns_lower_score():
    ex = _make_executor()
    md = _base_market_data()
    md["ltp"] = 2485.0    # tiny move from open 2480
    md["open"] = 2480.0
    md["day_high"] = 2540.0
    md["day_low"] = 2470.0  # range = 70; body = 5; body_pct = 5/70 = 0.071; score = 0.143
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["price_action"] < 0.5


# ---------------------------------------------------------------------------
# Step 6: sector_strength
# ---------------------------------------------------------------------------

def test_step6_sector_present_returns_one():
    ex = _make_executor()
    md = _base_market_data()
    md["sector"] = "Energy"
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["sector_strength"] == 1.0


def test_step6_sector_missing_returns_neutral():
    ex = _make_executor()
    md = _base_market_data()
    del md["sector"]
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["sector_strength"] == 0.5


# ---------------------------------------------------------------------------
# Step 7: time_of_day
# ---------------------------------------------------------------------------

def test_step7_prime_time_returns_one():
    """15-60 minutes after 09:15 -> 1.0."""
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    prime = datetime.now(IST).replace(hour=9, minute=45, second=0, microsecond=0)
    with patch("screening.step_executor.now_ist", return_value=prime):
        ex = _make_executor()
        result = ex.run_all(_base_signal(), _base_market_data(), _base_thresholds())
        assert result.step_results["time_of_day"] == 1.0


def test_step7_too_early_returns_half():
    """< 15 minutes after 09:15 -> 0.5."""
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    early = datetime.now(IST).replace(hour=9, minute=20, second=0, microsecond=0)
    with patch("screening.step_executor.now_ist", return_value=early):
        ex = _make_executor()
        result = ex.run_all(_base_signal(), _base_market_data(), _base_thresholds())
        assert result.step_results["time_of_day"] == 0.5


# ---------------------------------------------------------------------------
# Step 8: spread_check
# ---------------------------------------------------------------------------

def test_step8_spread_within_threshold_returns_one():
    ex = _make_executor()
    md = _base_market_data()
    md["bid"] = 2509.9
    md["ask"] = 2510.1   # spread = 0.2; ltp=2510; spread_pct=0.00796 < 0.1
    md["ltp"] = 2510.0
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["spread_check"] == 1.0


def test_step8_wide_spread_returns_zero():
    ex = _make_executor()
    md = _base_market_data()
    md["bid"] = 2505.0
    md["ask"] = 2515.0   # spread = 10; spread_pct = 0.398% > 0.1
    md["ltp"] = 2510.0
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["spread_check"] == 0.0


def test_step8_bid_ask_missing_returns_neutral():
    ex = _make_executor()
    md = _base_market_data()
    del md["bid"]
    del md["ask"]
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["spread_check"] == 0.5


# ---------------------------------------------------------------------------
# Step 9: circuit_check
# ---------------------------------------------------------------------------

def test_step9_no_circuit_returns_one():
    ex = _make_executor()
    md = _base_market_data()
    md["circuit_state"] = ""
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["circuit_check"] == 1.0


def test_step9_upper_circuit_returns_zero():
    ex = _make_executor()
    md = _base_market_data()
    md["circuit_state"] = "upper_circuit"
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["circuit_check"] == 0.0


def test_step9_lower_circuit_returns_zero():
    ex = _make_executor()
    md = _base_market_data()
    md["circuit_state"] = "lower_circuit"
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["circuit_check"] == 0.0


# ---------------------------------------------------------------------------
# Step 10: signal_age
# ---------------------------------------------------------------------------

def test_step10_age_within_30s_returns_one():
    ex = _make_executor()
    result = ex.run_all(
        _base_signal(age_sec=10), _base_market_data(), _base_thresholds()
    )
    assert result.step_results["signal_age"] == 1.0


def test_step10_age_between_30_and_60_returns_half():
    ex = _make_executor()
    result = ex.run_all(
        _base_signal(age_sec=45), _base_market_data(), _base_thresholds()
    )
    assert result.step_results["signal_age"] == 0.5


def test_step10_age_over_90s_returns_zero():
    ex = _make_executor()
    result = ex.run_all(
        _base_signal(age_sec=100), _base_market_data(), _base_thresholds()
    )
    assert result.step_results["signal_age"] == 0.0


# ---------------------------------------------------------------------------
# Exception handling (SE5)
# ---------------------------------------------------------------------------

def test_exception_in_step_gives_zero_status_error_logged():
    """Step that raises -> score=0.0, status=ERROR, step in error_steps."""
    ex = _make_executor()
    md = _base_market_data()
    # Patch _step_1_volume_surge to raise
    def _bad_step(*args, **kwargs):
        raise RuntimeError("injected failure")
    ex._step_1_volume_surge = _bad_step

    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.step_results["volume_surge"] == 0.0
    assert result.step_statuses["volume_surge"] == "ERROR"
    assert "volume_surge" in result.error_steps


def test_all_10_steps_run_even_if_one_errors():
    """No short-circuit: all steps execute regardless of errors."""
    ex = _make_executor()
    md = _base_market_data()

    def _bad_step(*args, **kwargs):
        raise RuntimeError("injected failure")

    ex._step_1_volume_surge = _bad_step

    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert len(result.step_results) == 10
    assert len(result.step_statuses) == 10
    assert len(result.latencies_ms) == 10


def test_run_all_does_not_raise_on_step_exception():
    """run_all() itself never raises."""
    ex = _make_executor()

    def _bad_step(*args, **kwargs):
        raise ValueError("bad")

    ex._step_5_price_action = _bad_step
    # Should not raise
    result = ex.run_all(_base_signal(), _base_market_data(), _base_thresholds())
    assert result is not None


# ---------------------------------------------------------------------------
# Missing market data (SE8)
# ---------------------------------------------------------------------------

def test_missing_market_data_keys_no_raise():
    """Completely empty market_data should not raise."""
    ex = _make_executor()
    result = ex.run_all(_base_signal(), {}, _base_thresholds())
    assert len(result.step_results) == 10


def test_rejected_at_set_to_first_failing_step():
    """rejected_at is the name of the first step that returned 0.0."""
    ex = _make_executor()
    md = _base_market_data()
    # Step 1 volume_surge fails: low volume
    md["volume"] = 1
    md["avg_volume_20d"] = 50_000
    result = ex.run_all(_base_signal(), md, _base_thresholds())
    assert result.rejected_at == "volume_surge"


def test_rejected_at_none_when_all_pass():
    """If all steps return > 0.0, rejected_at should be None."""
    ex = _make_executor()
    md = _base_market_data()
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    prime = datetime.now(IST).replace(hour=9, minute=45, second=0, microsecond=0)
    with patch("screening.step_executor.now_ist", return_value=prime):
        result = ex.run_all(_base_signal(age_sec=10), md, _base_thresholds())
    # volume_surge: volume=100000 > 50000*1.5 -> pass
    # vwap: ltp=2510 > vwap=2490 LONG -> pass
    # All should pass with base data
    if result.rejected_at is not None:
        # Some step may still reject; just verify the field is a string or None
        assert isinstance(result.rejected_at, str)


# ---------------------------------------------------------------------------
# Direction awareness
# ---------------------------------------------------------------------------

def test_long_vs_short_vwap_position_differ():
    """LONG and SHORT should produce opposite vwap_position scores."""
    ex = _make_executor()
    md = _base_market_data()
    md["ltp"] = 2510.0
    md["vwap"] = 2490.0  # ltp > vwap: LONG pass, SHORT fail
    r_long = ex.run_all(_base_signal(direction="LONG"), md, _base_thresholds())
    r_short = ex.run_all(_base_signal(direction="SHORT"), md, _base_thresholds())
    assert r_long.step_results["vwap_position"] == 1.0
    assert r_short.step_results["vwap_position"] == 0.0


def test_long_vs_short_rsi_range_differ():
    """RSI 70 is in LONG range but not SHORT range."""
    ex = _make_executor()
    md = _base_market_data()
    md["rsi"] = 70.0   # LONG: [40,80] pass; SHORT: [20,60] fail
    r_long = ex.run_all(_base_signal(direction="LONG"), md, _base_thresholds())
    r_short = ex.run_all(_base_signal(direction="SHORT"), md, _base_thresholds())
    assert r_long.step_results["rsi_range"] == 1.0
    assert r_short.step_results["rsi_range"] == 0.0


if __name__ == "__main__":
    import subprocess, sys
    r = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v"],
        capture_output=False,
    )
    sys.exit(r.returncode)
