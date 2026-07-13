"""
tests/unit/test_v3_pb01_e2e.py — V3 Step 10b · end-to-end PB-01 watchlist pipeline.

Wires the REAL pieces the way main.py does — WatchlistCaptureWorker (EOD) → pb01_watchlist
store → Pb01EntryStage (next-morning) → Pb01WouldBeRunner.record (on_confirm) — over a REAL
StateStore, and proves one breakout flows capture → CONSUMED → a would-be record, with NO
order path anywhere. This is the composition proof main.py cannot unit-test.
"""
from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, time
from pathlib import Path

from core.config_loader import SRDetectorConfig, V3ChainConfig, WatchlistConfig
from core.state_store import StateStore
from sr_detector.models import Candle
from sr_detector.zone_builder import build_scoring_params, build_zone_knobs
from v3_chain.pb01_entry import Pb01EntryStage
from v3_chain.pb01_runner import Pb01WouldBeRunner
from v3_chain.watchlist_capture import WatchlistCaptureWorker

BREAKOUT = date(2026, 7, 10)      # day D (Chartink EOD alert fires)
TRADING = date(2026, 7, 11)       # day D+1 (next_trading_day → the entry session)


def _c(d, hh, mm, o, h, l, cl, v):
    return Candle(ts=datetime.combine(d, time(hh, mm)), open=o, high=h, low=l, close=cl, volume=int(v))


def _daily():
    # 22 prior sessions high=100 (→ LEVEL 100), vol 75000 (→ baseline 1000), then the breakout.
    out = []
    for i in range(22):
        d = date.fromordinal(BREAKOUT.toordinal() - (22 - i))
        out.append(_c(d, 9, 15, 99, 100, 98, 99.5, 75000))
    out.append(_c(BREAKOUT, 9, 15, 101, 130, 100, 129, 75000))   # breakout day (high irrelevant to LEVEL)
    return out


def _five():   # the D+1 retest on the 5-min timeframe (clean touch + strong confirmation)
    return [_c(TRADING, 9, 15, 100.5, 101.0, 100.4, 100.6, 800),
            _c(TRADING, 9, 20, 100.4, 100.5, 100.05, 100.2, 600),
            _c(TRADING, 9, 25, 100.1, 101.5, 100.0, 101.2, 2000)]   # CONFIRM


def _thirty():   # prior-day 30-min bars, TR=2 → ATR30 = 2.0
    return [_c(BREAKOUT, 10, 0, 99.0, 100.0, 98.0, 99.0, 5000) for _ in range(20)]


def _sixty():    # a mild uptrending 1h series for the HTF factor (prior day)
    return [_c(BREAKOUT, 9 + i, 15, 95 + i, 96 + i, 94 + i, 95.5 + i, 4000) for i in range(6)]


class _E2EFetcher:
    def __init__(self):
        self._m = {"day": _daily(), "5minute": _five(), "30minute": _thirty(), "60minute": _sixty()}

    def fetch_timeframes(self, symbol, intervals):
        return {tf: list(self._m.get(tf, [])) for tf in intervals if self._m.get(tf)}

    def fetch_interval(self, symbol, interval, lookback_days=None):
        return list(self._m.get(interval, []))


class _FakeMW:
    market_open = time(9, 15)
    market_close = time(15, 30)

    def next_trading_day(self, dt):
        return date.fromordinal(dt.date().toordinal() + 1)   # 07-10 → 07-11 (no holidays in test)


def test_full_pipeline_capture_to_would_be_record():
    store = StateStore(Path(tempfile.mkdtemp()) / "e2e.db")
    wb_path = Path(tempfile.mkdtemp()) / "pb01_wb.jsonl"
    fetcher = _E2EFetcher()
    sr = SRDetectorConfig()
    knobs, scoring = build_zone_knobs(sr), build_scoring_params(sr)
    wl_cfg = WatchlistConfig(would_be_log_path=str(wb_path))
    v3 = V3ChainConfig()

    # ── EOD (day D): the capture worker persists a watchlist row (LEVEL from OUR candles) ──
    capture = WatchlistCaptureWorker(
        config=wl_cfg, store=store, fetcher=fetcher, market_windows=_FakeMW(),
        logger=None, now_fn=lambda: datetime(2026, 7, 10, 16, 0), zone_knobs=knobs, zone_scoring=scoring)
    capture._capture("pb01_breakout_retest", "ABB", datetime(2026, 7, 10, 15, 40))

    rows = store.get_pb01_watchlist_for_date(TRADING.isoformat())
    assert len(rows) == 1 and rows[0]["level"] == 100.0 and rows[0]["status"] == "PENDING"

    # ── next morning (day D+1): entry stage → on_confirm = the place-free would-be runner ──
    runner = Pb01WouldBeRunner(
        config=wl_cfg, v3_cfg=v3, fetcher=fetcher, zone_knobs=knobs, zone_scoring=scoring,
        logger=None, now_fn=lambda: datetime(2026, 7, 11, 9, 41), regime_runner=None)
    entry = Pb01EntryStage(
        config=wl_cfg, v3_cfg=v3, store=store, fetcher=fetcher, market_windows=_FakeMW(),
        on_confirm=runner.record, logger=None, now_fn=lambda: datetime(2026, 7, 11, 9, 40))
    entry.poll_once()

    # the row was CONSUMED and exactly ONE would-be record was written.
    assert store.get_pb01_watchlist_for_date(TRADING.isoformat())[0]["status"] == "CONSUMED"
    lines = wb_path.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["symbol"] == "ABB" and rec["strategy"] == "pb01_breakout_retest"
    assert rec["live_entry"] == 101.2 and rec["v3_sl"] == 99.6   # min(pullback 100.0, level 100) − 0.2×2 = 99.6
    assert rec["score"]["playbook"] is not None                  # full 3-layer score
    assert rec["v3_verdict"].startswith("WOULD_")
    store.close()


if __name__ == "__main__":
    test_full_pipeline_capture_to_would_be_record()
    print("  OK test_full_pipeline_capture_to_would_be_record")
    print("\nPB-01 end-to-end: passed.")
