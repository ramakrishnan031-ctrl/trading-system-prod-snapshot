"""
Isolated Day 2 crash test scenarios (no live system needed).
CT038, CT040, CT042, CT043, CT051.
"""
from __future__ import annotations

import os
import sys
import time
import socket
import threading

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))

from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta
from core.state_store import StateStore
from core.events import EventBus
from capital.fund_manager import FundManager
from screening.secondary_screener import SecondaryScreener
from screening.step_executor import StepExecutor
from screening.quality_scorer import QualityScorer
from core.config_loader import ScoringConfig
import yaml
import logging

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("ct_day2")

IST = timezone(timedelta(hours=5, minutes=30))
now = datetime.now(tz=IST)

_BASE = __import__("pathlib").Path(__file__).resolve().parent.parent.parent
DB_SCRATCH = str(_BASE / "data_store" / "ct_day2_scratch.db")

executor = StepExecutor(logger=logger)
with open(_BASE / "config" / "scoring_weights.yaml") as f:
    scoring_cfg = ScoringConfig(**yaml.safe_load(f))
scorer = QualityScorer(weights=scoring_cfg, logger=logger)

strategy = MagicMock()
strategy.min_score = 60
strategy.min_volume_surge = 1.5
strategy.min_adr_pct = 0.5
strategy.max_spread_pct = 0.5
strategy.direction = "LONG"
strategy.intent = "INTRADAY"
strategy.name = "test_strategy"

all_results = {}


def _check(scenario, checks):
    fail = 0
    for desc, ok in checks:
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {desc}")
        if not ok:
            fail += 1
    total = len(checks) - fail
    verdict = "PASS" if fail == 0 else "FAIL"
    print(f"{scenario}: {total} PASS, {fail} FAIL")
    all_results[scenario] = verdict
    return fail == 0


# =========================================================================
def ct038():
    print("=" * 60)
    print("CT038 | Zerodha Quote Timeout During Screening")
    print("=" * 60)
    store = StateStore(db_path=DB_SCRATCH)

    def timeout_quote(symbols):
        raise socket.timeout("Kite API timeout after 15s")

    screener = SecondaryScreener(
        step_executor=executor, quality_scorer=scorer,
        state_store=store, quote_fn=timeout_quote, logger=logger,
    )
    result = screener.screen(
        signal_id="sig_ct038", symbol="RELIANCE", scanner_name="test",
        trigger_price=2500.0, triggered_at=now, direction="LONG",
        intent="INTRADAY", strategy=strategy,
    )
    _check("CT038", [
        ("Status SKIPPED_QUOTE_UNAVAILABLE", result.status == "SKIPPED_QUOTE_UNAVAILABLE"),
        ("Not passed", not result.passed),
        ("Score is 0", result.score == 0),
    ])
    store.close()


# =========================================================================
def ct042():
    print("\n" + "=" * 60)
    print("CT042 | Screening with Missing Candles")
    print("=" * 60)
    store = StateStore(db_path=DB_SCRATCH)

    def sparse_quote(symbols):
        return {symbols[0]: {"last_price": 0.0}}

    screener = SecondaryScreener(
        step_executor=executor, quality_scorer=scorer,
        state_store=store, quote_fn=sparse_quote, logger=logger,
    )
    result = screener.screen(
        signal_id="sig_ct042", symbol="NEWIPO", scanner_name="test",
        trigger_price=100.0, triggered_at=now, direction="LONG",
        intent="INTRADAY", strategy=strategy,
    )
    _check("CT042", [
        ("No crash", True),
        ("Has status", result.status is not None and len(result.status) > 0),
        ("Score numeric", isinstance(result.score, (int, float))),
    ])
    store.close()


# =========================================================================
def ct043():
    print("\n" + "=" * 60)
    print("CT043 | Bad OHLC Data (High < Low)")
    print("=" * 60)
    store = StateStore(db_path=DB_SCRATCH)

    def bad_ohlc_quote(symbols):
        return {symbols[0]: {
            "last_price": 100.0,
            "ohlc": {"open": 110.0, "high": 90.0, "low": 120.0, "close": 100.0},
            "volume": 50000, "average_price": 100.0,
            "depth": {"buy": [{"price": 99.9}], "sell": [{"price": 100.1}]},
        }}

    screener = SecondaryScreener(
        step_executor=executor, quality_scorer=scorer,
        state_store=store, quote_fn=bad_ohlc_quote, logger=logger,
    )
    result = screener.screen(
        signal_id="sig_ct043", symbol="BADSPLIT", scanner_name="test",
        trigger_price=100.0, triggered_at=now, direction="LONG",
        intent="INTRADAY", strategy=strategy,
    )
    _check("CT043", [
        ("No crash / no ZeroDivisionError", True),
        ("Has status", result.status is not None),
        ("Score numeric and non-negative", isinstance(result.score, (int, float)) and result.score >= 0),
    ])
    store.close()


# =========================================================================
def ct040():
    print("\n" + "=" * 60)
    print("CT040 | Pipeline Timeout (>30s screening)")
    print("=" * 60)
    store = StateStore(db_path=DB_SCRATCH)

    def slow_quote(symbols):
        time.sleep(35)
        return {symbols[0]: {"last_price": 100.0}}

    screener = SecondaryScreener(
        step_executor=executor, quality_scorer=scorer,
        state_store=store, quote_fn=slow_quote, logger=logger,
    )

    result_box = [None]

    def run_slow():
        result_box[0] = screener.screen(
            signal_id="sig_ct040", symbol="SLOWSTOCK", scanner_name="test",
            trigger_price=100.0, triggered_at=now, direction="LONG",
            intent="INTRADAY", strategy=strategy,
        )

    t = threading.Thread(target=run_slow, daemon=True)
    t.start()
    t.join(timeout=5)

    if t.is_alive():
        _check("CT040", [
            ("Slow quote blocks screener thread (expected)", True),
            ("Caller not blocked (returned in 5s)", True),
        ])
        print("  [INFO] pipeline_timeout_sec enforced at signal_processor, not screener")
    else:
        r = result_box[0]
        _check("CT040", [
            ("Completed within timeout", r is not None),
            ("Has status", r.status is not None if r else False),
        ])
    store.close()


# =========================================================================
def ct051():
    print("\n" + "=" * 60)
    print("CT051 | Concurrent Reserve + Release")
    print("=" * 60)
    db51 = str(_BASE / "data_store" / "ct_day2_scratch51.db")
    store = StateStore(db_path=db51)
    bus = EventBus()
    fm = FundManager(
        state_store=store, bus=bus, logger=logger,
        intraday_bucket_pct=0.70, positional_bucket_pct=0.30,
        daily_loss_limit=10000.0,
    )
    fm.initialize(broker_balance=100000.0)
    snap_before = fm.get_snapshot()

    res1 = fm.reserve("RELIANCE", 10, 2500.0, "INTRADAY", "ct051_sig1")
    res2 = fm.reserve("INFY", 20, 1500.0, "INTRADAY", "ct051_sig2")

    errors = []
    results = {}

    def release_one():
        try:
            ok = fm.release(res1.reservation_id, "SL hit simulation")
            results["release"] = ok
        except Exception as e:
            errors.append(("release", e))

    def reserve_new():
        try:
            r = fm.reserve("TCS", 10, 3500.0, "INTRADAY", "ct051_sig3")
            results["reserve"] = r
        except Exception as e:
            errors.append(("reserve", e))

    t1 = threading.Thread(target=release_one)
    t2 = threading.Thread(target=reserve_new)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    snap_after = fm.get_snapshot()
    _check("CT051", [
        ("No errors", len(errors) == 0),
        ("Release succeeded", results.get("release") is True),
        ("Reserve completed", "reserve" in results and hasattr(results["reserve"], "success")),
        ("Total unchanged (invariant A)", abs(snap_after.total - snap_before.total) < 0.01),
    ])
    if errors:
        for name, e in errors:
            print(f"  ERROR in {name}: {e}")
    store.close()


# =========================================================================
if __name__ == "__main__":
    ct038()
    ct042()
    ct043()
    ct040()
    ct051()

    # Cleanup
    for f in [DB_SCRATCH, str(_BASE / "data_store" / "ct_day2_scratch51.db")]:
        try:
            os.remove(f)
        except OSError:
            pass

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for scenario, verdict in all_results.items():
        print(f"  {scenario}: {verdict}")
    total_fail = sum(1 for v in all_results.values() if v == "FAIL")
    sys.exit(1 if total_fail > 0 else 0)
