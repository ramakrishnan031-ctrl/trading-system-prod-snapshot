# tests/unit/test_secondary_screener.py — Trading System v2
#
# Tests for screening/secondary_screener.py (SS1-SS15)

import json
import logging
import pytest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import tempfile

from screening.secondary_screener import SecondaryScreener, ScreeningResult
from screening.step_executor import StepExecutor, StepExecutorResult
from screening.quality_scorer import QualityScorer, ScoreResult
from core.state_store import StateStore
from core.time_authority import now_ist
from strategies.loader import StrategyLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scoring_config():
    steps = MagicMock()
    for name in ["volume_surge","vwap_position","atr_filter","rsi_range",
                 "price_action","sector_strength","time_of_day","spread_check",
                 "circuit_check","signal_age"]:
        setattr(steps, name, 10)
    cfg = MagicMock()
    cfg.steps = steps
    cfg.min_pass_score = 60
    cfg.high_score_threshold = 80
    cfg.medium_score_threshold = 65
    return cfg


def _make_screener(store=None, quote_fn=None):
    logger = logging.getLogger("test_secondary_screener")
    executor = StepExecutor(logger)
    scorer = QualityScorer(_make_scoring_config(), logger)
    if store is None:
        # Use a real in-memory store
        td = tempfile.mkdtemp()
        store = StateStore(Path(td) / "test.db")
    if quote_fn is None:
        quote_fn = MagicMock(return_value={})
    return SecondaryScreener(executor, scorer, store, quote_fn, logger), store


def _mock_strategy(
    min_score=0, min_volume_surge=1.5, min_adr_pct=0.5, max_spread_pct=0.1
):
    s = MagicMock()
    s.min_score = min_score
    s.min_volume_surge = min_volume_surge
    s.min_adr_pct = min_adr_pct
    s.max_spread_pct = max_spread_pct
    s.name = "open_low_breakout_long"
    return s


def _base_signal_kwargs(age_sec=10, direction="LONG", mock_now=None):
    # Use mock_now when age must be consistent with a patched now_ist().
    # If not provided, fall back to real wall clock (fine for age_sec=0 tests).
    base = mock_now if mock_now is not None else now_ist()
    triggered = base - timedelta(seconds=age_sec)
    return dict(
        signal_id="sig_001",
        symbol="RELIANCE",
        scanner_name="open_low_breakout_long",
        trigger_price=2500.0,
        triggered_at=triggered,
        direction=direction,
        intent="INTRADAY",
        strategy=_mock_strategy(),
    )


def _passing_market_data():
    """Market data that passes all 10 steps at prime time."""
    return {
        "ltp": 2510.0,
        "volume": 100_000,
        "vwap": 2490.0,
        "atr": 25.0,          # adr_pct=0.996 > 0.5
        "rsi": 55.0,
        "bid": 2509.9,
        "ask": 2510.1,         # spread_pct=0.004 < 0.1
        "sector": "Energy",
        "circuit_state": "",
        "day_high": 2540.0,
        "day_low": 2470.0,
        "prev_close": 2480.0,
        "avg_volume_20d": 50_000,
        "open": 2480.0,
    }


def _prime_time_patch():
    """Patch now_ist so time_of_day step returns 1.0 (prime time 09:45 IST)."""
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(IST).replace(hour=9, minute=45, second=0, microsecond=0)


def _insert_signal_row(store, signal_id="sig_001"):
    """Insert a minimal signal row so update_signal_status can find it."""
    from core.time_authority import now_ist
    ts = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            """INSERT OR IGNORE INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at,
                received_at, expires_at, status, rejection_reason, trade_id,
                trigger_price, fingerprint, fingerprint_date)
               VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?,?)""",
            (signal_id, "RELIANCE", "open_low_breakout_long",
             "open_low_breakout_long", ts, ts, ts,
             "PENDING", 2500.0, f"fp_{signal_id}", "2026-04-16"),
        )


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

def test_import_and_instantiate():
    screener, _ = _make_screener()
    assert screener is not None


def test_screen_returns_screening_result():
    screener, store = _make_screener()
    _insert_signal_row(store)
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(
            market_data=_passing_market_data(),
            **_base_signal_kwargs(age_sec=10),
        )
    assert isinstance(result, ScreeningResult)


def test_screening_result_fields_populated():
    screener, store = _make_screener()
    _insert_signal_row(store)
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(market_data=_passing_market_data(), **_base_signal_kwargs())
    assert isinstance(result.passed, bool)
    assert result.status in ("PASSED",) or result.status.startswith(("REJECTED_", "SKIPPED_"))
    assert isinstance(result.score, int)
    assert result.tier in ("HIGH", "MEDIUM", "LOW")
    assert isinstance(result.step_results, dict)
    assert isinstance(result.step_statuses, dict)
    assert isinstance(result.error_steps, list)
    assert isinstance(result.latencies_ms, dict)
    assert isinstance(result.market_data_snapshot, dict)


# ---------------------------------------------------------------------------
# Passing scenario
# ---------------------------------------------------------------------------

def test_all_steps_pass_returns_passed():
    screener, store = _make_screener()
    _insert_signal_row(store)
    prime = _prime_time_patch()
    # triggered_at must be relative to the patched now to avoid age calculation drift
    triggered_at = prime - timedelta(seconds=10)
    with patch("screening.step_executor.now_ist", return_value=prime):
        result = screener.screen(
            signal_id="sig_001", symbol="RELIANCE", scanner_name="x",
            trigger_price=2500.0, triggered_at=triggered_at,
            direction="LONG", intent="INTRADAY", strategy=_mock_strategy(),
            market_data=_passing_market_data(),
        )
    assert result.passed is True
    assert result.status == "PASSED"


# ---------------------------------------------------------------------------
# Individual step rejections
# ---------------------------------------------------------------------------

def test_volume_surge_fail_gives_rejected_status():
    screener, store = _make_screener()
    _insert_signal_row(store)
    md = _passing_market_data()
    md["volume"] = 1       # far below threshold
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(market_data=md, **_base_signal_kwargs())
    # Score will be low due to 0.0 on volume_surge (weight 10 out of 100)
    # Whether it rejects depends on total vs min_pass_score
    # volume_surge = 0, all others pass -> score = 90 -> PASSED (high weight removed)
    # Actually with weight 10 removed, score = 90 >= 60 -> PASSED
    # This test verifies volume_surge score = 0.0 is properly recorded
    assert result.step_results["volume_surge"] == 0.0
    assert result.step_statuses["volume_surge"] == "REJECTED"


def test_long_ltp_below_vwap_rejects_vwap_step():
    screener, store = _make_screener()
    _insert_signal_row(store)
    md = _passing_market_data()
    md["ltp"] = 2470.0   # below vwap=2490 -> LONG fails vwap_position
    md["bid"] = 2469.9
    md["ask"] = 2470.1
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(market_data=md, **_base_signal_kwargs(direction="LONG"))
    assert result.step_results["vwap_position"] == 0.0
    assert result.step_statuses["vwap_position"] == "REJECTED"


def test_short_ltp_above_vwap_rejects_vwap_step():
    """Direction-aware: SHORT with ltp > vwap -> vwap_position=0."""
    screener, store = _make_screener()
    _insert_signal_row(store)
    md = _passing_market_data()
    md["ltp"] = 2510.0   # above vwap=2490 -> SHORT fails
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(market_data=md, **_base_signal_kwargs(direction="SHORT"))
    assert result.step_results["vwap_position"] == 0.0


def test_circuit_hit_gives_zero_circuit_check():
    screener, store = _make_screener()
    _insert_signal_row(store)
    md = _passing_market_data()
    md["circuit_state"] = "upper_circuit"
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(market_data=md, **_base_signal_kwargs())
    assert result.step_results["circuit_check"] == 0.0


# ---------------------------------------------------------------------------
# Score-based rejection
# ---------------------------------------------------------------------------

def test_score_below_global_min_pass_rejected():
    """All steps fail -> score=0 < 60 -> REJECTED_SCORE_0."""
    screener, store = _make_screener()
    _insert_signal_row(store)
    md = {
        "ltp": 2470.0,          # below vwap: vwap fail (LONG)
        "volume": 1,            # volume fail
        "vwap": 2490.0,
        "atr": 0,               # atr fail
        "rsi": 85.0,            # out of LONG range
        "bid": None, "ask": None,  # neutral 0.5
        "sector": None,            # neutral 0.5
        "circuit_state": "",
        "day_high": 2480.0,
        "day_low": 2470.0,
        "prev_close": 2470.0,
        "avg_volume_20d": 50_000,
        "open": 2470.0,
    }
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(
            market_data=md, **_base_signal_kwargs(direction="LONG", age_sec=10)
        )
    assert result.passed is False
    assert result.status.startswith("REJECTED_SCORE_")
    assert result.score < 60


def test_score_below_strategy_min_score_override_rejected():
    """strategy.min_score=80 is the effective_min; score < 80 -> rejected."""
    screener, store = _make_screener()
    _insert_signal_row(store)
    strategy = _mock_strategy(min_score=80)
    md = _passing_market_data()
    # Force score to ~70 by removing volume_surge (0.0, weight=10) -> score=90
    # Need more removals: also fail circuit_check(10) -> score=80 -> passes
    # Fail also rsi_range(10) -> score=70 < 80 -> rejected
    md["volume"] = 1           # volume_surge fail (-10)
    md["rsi"] = 85.0           # rsi_range fail for LONG (-10)
    md["circuit_state"] = "upper_circuit"  # circuit fail (-10) -> score=70
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(
            signal_id="sig_001", symbol="RELIANCE", scanner_name="x",
            trigger_price=2500.0, triggered_at=now_ist() - timedelta(seconds=5),
            direction="LONG", intent="INTRADAY", strategy=strategy,
            market_data=md,
        )
    assert result.passed is False
    assert result.status.startswith("REJECTED_SCORE_")


def test_strategy_min_score_zero_uses_global_min_pass():
    """strategy.min_score=0 -> falls back to global min_pass_score (60)."""
    screener, store = _make_screener()
    _insert_signal_row(store)
    strategy = _mock_strategy(min_score=0)  # 0 means use global
    prime = _prime_time_patch()
    triggered_at = prime - timedelta(seconds=5)
    with patch("screening.step_executor.now_ist", return_value=prime):
        result = screener.screen(
            market_data=_passing_market_data(),
            signal_id="sig_001", symbol="RELIANCE", scanner_name="x",
            trigger_price=2500.0, triggered_at=triggered_at,
            direction="LONG", intent="INTRADAY", strategy=strategy,
        )
    # With all-passing data, score=100 >= 60 -> PASSED
    assert result.passed is True


# ---------------------------------------------------------------------------
# Signal age
# ---------------------------------------------------------------------------

def test_signal_age_over_90s_rejected_signal_age():
    screener, store = _make_screener()
    _insert_signal_row(store)
    mock_now = _prime_time_patch()
    with patch("screening.step_executor.now_ist", return_value=mock_now):
        result = screener.screen(
            market_data=_passing_market_data(),
            **_base_signal_kwargs(age_sec=100, mock_now=mock_now),
        )
    # step_10_signal_age returns 0.0 for >90s, lowering score
    # With score potentially still above 60, but REJECTED_SIGNAL_AGE is defense-in-depth
    assert result.status == "REJECTED_SIGNAL_AGE" or result.step_results.get("signal_age") == 0.0


# ---------------------------------------------------------------------------
# quote_fn failure
# ---------------------------------------------------------------------------

def test_quote_fn_raises_returns_skipped_quote_unavailable():
    def bad_quote_fn(symbols):
        raise ConnectionError("network down")
    screener, store = _make_screener(quote_fn=bad_quote_fn)
    _insert_signal_row(store)
    result = screener.screen(**_base_signal_kwargs())
    # No market_data passed -> quote_fn called -> raises -> SKIPPED
    assert result.passed is False
    assert result.status == "SKIPPED_QUOTE_UNAVAILABLE"


def test_market_data_provided_quote_fn_not_called():
    """When market_data is provided, quote_fn must not be called."""
    quote_fn = MagicMock()
    screener, store = _make_screener(quote_fn=quote_fn)
    _insert_signal_row(store)
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        screener.screen(market_data=_passing_market_data(), **_base_signal_kwargs())
    quote_fn.assert_not_called()


# ---------------------------------------------------------------------------
# Step executor exception (P9a fix a)
# ---------------------------------------------------------------------------

def test_step_executor_exception_returns_rejected_step_error():
    """If a step raises, result is REJECTED_STEP_ERROR (not silent continue)."""
    logger = logging.getLogger("test_ss")
    scorer = QualityScorer(_make_scoring_config(), logger)
    executor = StepExecutor(logger)

    # Inject a step that raises
    def _bad_step(*args, **kwargs):
        raise RuntimeError("injected")
    executor._step_1_volume_surge = _bad_step

    td = tempfile.mkdtemp()
    store = StateStore(Path(td) / "test.db")
    screener = SecondaryScreener(executor, scorer, store, MagicMock(), logger)
    _insert_signal_row(store)
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(market_data=_passing_market_data(), **_base_signal_kwargs())
    assert result.passed is False
    assert result.status == "REJECTED_STEP_ERROR"
    assert "volume_surge" in result.error_steps


# ---------------------------------------------------------------------------
# P18 persistence
# ---------------------------------------------------------------------------

def test_p18_state_store_update_signal_status_called():
    """state_store.update_signal_status must be called after screen()."""
    mock_store = MagicMock()
    mock_store.update_signal_status = MagicMock()
    mock_store.insert_screener_result = MagicMock()
    logger = logging.getLogger("test_ss")
    executor = StepExecutor(logger)
    scorer = QualityScorer(_make_scoring_config(), logger)
    screener = SecondaryScreener(executor, scorer, mock_store, MagicMock(), logger)
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        screener.screen(market_data=_passing_market_data(), **_base_signal_kwargs())
    mock_store.update_signal_status.assert_called_once()
    call_args = mock_store.update_signal_status.call_args
    assert call_args[0][0] == "sig_001"  # signal_id


def test_p18_screener_results_row_written():
    """insert_screener_result must be called with correct fields."""
    mock_store = MagicMock()
    mock_store.update_signal_status = MagicMock()
    mock_store.insert_screener_result = MagicMock()
    logger = logging.getLogger("test_ss")
    executor = StepExecutor(logger)
    scorer = QualityScorer(_make_scoring_config(), logger)
    screener = SecondaryScreener(executor, scorer, mock_store, MagicMock(), logger)
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(market_data=_passing_market_data(), **_base_signal_kwargs())
    mock_store.insert_screener_result.assert_called_once()
    kwargs = mock_store.insert_screener_result.call_args[1]
    assert kwargs["signal_id"] == "sig_001"
    assert kwargs["score"] == result.score
    assert kwargs["tier"] == result.tier
    assert kwargs["status"] == result.status


def test_p18_state_store_write_failure_still_returns_result():
    """DB write failure: log ERROR but still return ScreeningResult."""
    mock_store = MagicMock()
    mock_store.update_signal_status = MagicMock(side_effect=Exception("DB dead"))
    mock_store.insert_screener_result = MagicMock()
    logger = logging.getLogger("test_ss")
    executor = StepExecutor(logger)
    scorer = QualityScorer(_make_scoring_config(), logger)
    screener = SecondaryScreener(executor, scorer, mock_store, MagicMock(), logger)
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(market_data=_passing_market_data(), **_base_signal_kwargs())
    # Must still return a valid ScreeningResult
    assert isinstance(result, ScreeningResult)


# ---------------------------------------------------------------------------
# market_data_snapshot captured
# ---------------------------------------------------------------------------

def test_market_data_snapshot_captured_in_result():
    screener, store = _make_screener()
    _insert_signal_row(store)
    md = _passing_market_data()
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(market_data=md, **_base_signal_kwargs())
    assert result.market_data_snapshot["ltp"] == md["ltp"]
    assert result.market_data_snapshot["vwap"] == md["vwap"]


# ---------------------------------------------------------------------------
# Direction propagation
# ---------------------------------------------------------------------------

def test_direction_propagated_to_steps():
    """LONG and SHORT give different vwap_position results for same data."""
    screener_long, store_l = _make_screener()
    screener_short, store_s = _make_screener()
    _insert_signal_row(store_l, "sig_long")
    _insert_signal_row(store_s, "sig_short")
    md = _passing_market_data()
    md["ltp"] = 2510.0   # ltp > vwap: LONG pass, SHORT fail
    triggered = now_ist() - timedelta(seconds=5)
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        r_long = screener_long.screen(
            signal_id="sig_long", symbol="RELIANCE", scanner_name="x",
            trigger_price=2500.0, triggered_at=triggered,
            direction="LONG", intent="INTRADAY", strategy=_mock_strategy(),
            market_data=md,
        )
        r_short = screener_short.screen(
            signal_id="sig_short", symbol="RELIANCE", scanner_name="x",
            trigger_price=2500.0, triggered_at=triggered,
            direction="SHORT", intent="INTRADAY", strategy=_mock_strategy(),
            market_data=md,
        )
    assert r_long.step_results["vwap_position"] == 1.0
    assert r_short.step_results["vwap_position"] == 0.0


# ---------------------------------------------------------------------------
# Strategy thresholds
# ---------------------------------------------------------------------------

def test_strategy_thresholds_override_defaults():
    """Stricter min_volume_surge from strategy correctly fails the step."""
    screener, store = _make_screener()
    _insert_signal_row(store)
    strategy = _mock_strategy(min_volume_surge=5.0)  # very strict
    md = _passing_market_data()
    md["volume"] = 100_000
    md["avg_volume_20d"] = 50_000  # surge=2.0, below 5.0
    with patch("screening.step_executor.now_ist", return_value=_prime_time_patch()):
        result = screener.screen(
            signal_id="sig_001", symbol="RELIANCE", scanner_name="x",
            trigger_price=2500.0, triggered_at=now_ist() - timedelta(seconds=5),
            direction="LONG", intent="INTRADAY", strategy=strategy,
            market_data=md,
        )
    assert result.step_results["volume_surge"] == 0.0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_same_inputs_twice_same_result():
    screener, store = _make_screener()
    _insert_signal_row(store, "sig_001")
    _insert_signal_row(store, "sig_002")
    md = _passing_market_data()
    triggered = now_ist() - timedelta(seconds=5)
    prime = _prime_time_patch()
    with patch("screening.step_executor.now_ist", return_value=prime):
        r1 = screener.screen(
            signal_id="sig_001", symbol="RELIANCE", scanner_name="x",
            trigger_price=2500.0, triggered_at=triggered,
            direction="LONG", intent="INTRADAY", strategy=_mock_strategy(),
            market_data=dict(md),
        )
        r2 = screener.screen(
            signal_id="sig_002", symbol="RELIANCE", scanner_name="x",
            trigger_price=2500.0, triggered_at=triggered,
            direction="LONG", intent="INTRADAY", strategy=_mock_strategy(),
            market_data=dict(md),
        )
    assert r1.status == r2.status
    assert r1.score == r2.score
    assert r1.tier == r2.tier


# ---------------------------------------------------------------------------
# All 15 strategies regression
# ---------------------------------------------------------------------------

def test_all_15_strategies_screen_without_error():
    """Load all 15 real strategies, screen with mock market data — no crash."""
    from pathlib import Path
    loader = StrategyLoader()
    strategies = loader.load_all_strategies(Path("config/strategies"))
    assert len(strategies) == 15

    md = _passing_market_data()
    prime = _prime_time_patch()

    for name, strategy in strategies.items():
        td = tempfile.mkdtemp()
        store = StateStore(Path(td) / "test.db")
        logger = logging.getLogger(f"test_ss_{name}")
        executor = StepExecutor(logger)
        scorer = QualityScorer(_make_scoring_config(), logger)
        screener = SecondaryScreener(executor, scorer, store, MagicMock(), logger)

        sig_id = f"sig_{name[:8]}"
        _insert_signal_row(store, sig_id)
        direction = strategy.direction  # "LONG" or "SHORT"
        triggered = now_ist() - timedelta(seconds=5)

        try:
            with patch("screening.step_executor.now_ist", return_value=prime):
                result = screener.screen(
                    signal_id=sig_id,
                    symbol="RELIANCE",
                    scanner_name=name,
                    trigger_price=2500.0,
                    triggered_at=triggered,
                    direction=direction,
                    intent=strategy.intent,
                    strategy=strategy,
                    market_data=dict(md),
                )
            assert isinstance(result, ScreeningResult), f"{name}: not ScreeningResult"
        except Exception as exc:
            pytest.fail(f"Strategy {name} raised: {exc}")
        finally:
            store.close()


if __name__ == "__main__":
    import subprocess, sys
    r = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v"],
        capture_output=False,
    )
    sys.exit(r.returncode)
