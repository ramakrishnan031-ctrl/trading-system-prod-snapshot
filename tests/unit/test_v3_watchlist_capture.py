"""
tests/unit/test_v3_watchlist_capture.py — V3 Step 10b · T2 EOD capture + watchlist store.

Covers compute_breakout_level (the deterministic LEVEL), the pb01_watchlist store helpers
(dedupe + anti-rehydration), and the capture worker (settled-only guard + capture + dedupe).
G-NO-REHYDRATION: a row stamped for a different trading_date is never loaded for today.
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace

from core.state_store import StateStore
from sr_detector.models import Candle
from v3_chain.watchlist_capture import WatchlistCaptureWorker, compute_breakout_level

_IST = None  # naive IST throughout (codebase convention in tests)


def _daily(n_before: int, breakout: date, *, highs=None):
    """n_before settled daily candles ending the day BEFORE `breakout`, + the breakout
    candle itself. highs: optional list for the prior sessions (else 100+i)."""
    out = []
    for i in range(n_before):
        d = date.fromordinal(breakout.toordinal() - (n_before - i))
        h = (highs[i] if highs else 100.0 + i)
        out.append(Candle(ts=datetime.combine(d, time(9, 15)), open=h - 1, high=h, low=h - 2,
                          close=h - 0.5, volume=1000))
    # the breakout candle (its high is irrelevant to LEVEL)
    out.append(Candle(ts=datetime.combine(breakout, time(9, 15)), open=120, high=130, low=119,
                      close=129, volume=5000))
    return out


# ── compute_breakout_level (spec §7) ──────────────────────────────────────────

def test_level_is_max_high_of_20_before_breakout():
    b = date(2026, 7, 10)
    # 20 prior sessions with highs 100..119; the max = 119.
    candles = _daily(20, b, highs=[100.0 + i for i in range(20)])
    assert compute_breakout_level(candles, b, 20) == 119.0


def test_level_ignores_breakout_day_and_later():
    b = date(2026, 7, 10)
    candles = _daily(20, b, highs=[100.0 + i for i in range(20)])
    # the breakout candle's high is 130 but it must NOT count toward LEVEL.
    assert compute_breakout_level(candles, b, 20) == 119.0


def test_level_none_when_insufficient_history():
    b = date(2026, 7, 10)
    candles = _daily(10, b)   # only 10 prior sessions, need 20
    assert compute_breakout_level(candles, b, 20) is None


# ── store helpers: dedupe + anti-rehydration ──────────────────────────────────

def _store():
    return StateStore(Path(tempfile.mkdtemp()) / "wl.db")


def _row(symbol="ABB", trading_date="2026-07-13", level=100.0):
    return {"symbol": symbol, "trading_date": trading_date, "level": level,
            "breakout_date": "2026-07-10", "source": "pb01_breakout_retest",
            "sr_zone_json": None, "status": "PENDING", "outcome_json": None,
            "captured_at": "t", "created_at": "t"}


def test_store_dedupe_unique_symbol_trading_date():
    s = _store()
    assert s.insert_pb01_watchlist(_row()) is True          # first insert
    assert s.insert_pb01_watchlist(_row(level=101.0)) is False  # dup (symbol,trading_date) → deduped
    rows = s.get_pb01_watchlist_for_date("2026-07-13")
    assert len(rows) == 1 and rows[0]["level"] == 100.0     # the earlier row wins (IGNORE)
    s.close()


def test_g_no_rehydration_stale_date_not_loaded():
    s = _store()
    s.insert_pb01_watchlist(_row(trading_date="2026-07-13"))   # stale (yesterday)
    s.insert_pb01_watchlist(_row(symbol="TCS", trading_date="2026-07-14"))  # today
    today = s.get_pb01_watchlist_for_date("2026-07-14")
    assert [r["symbol"] for r in today] == ["TCS"]            # the stale row is NEVER returned
    assert s.get_pb01_watchlist_for_date("2026-07-13")[0]["symbol"] == "ABB"
    s.close()


def test_store_update_status():
    s = _store()
    s.insert_pb01_watchlist(_row())
    rid = s.get_pb01_watchlist_for_date("2026-07-13")[0]["id"]
    s.update_pb01_watchlist_status(rid, "CONSUMED", outcome_json='{"x":1}', consumed_at="t2")
    assert s.get_pb01_watchlist_for_date("2026-07-13", pending_only=True) == []
    row = s.get_pb01_watchlist_for_date("2026-07-13")[0]
    assert row["status"] == "CONSUMED" and row["consumed_at"] == "t2"
    s.close()


# ── capture worker: settled-only + capture + dedupe ───────────────────────────

class _FakeFetcher:
    def __init__(self, daily):
        self._daily = daily
    def fetch_timeframes(self, symbol, intervals):
        return {"day": self._daily} if "day" in intervals else {}


class _FakeMW:
    market_close = time(15, 30)
    def next_trading_day(self, dt):
        return date.fromordinal(dt.date().toordinal() + 1)   # simple +1 (no holidays in test)


def _worker(store, daily, now):
    cfg = SimpleNamespace(level_lookback_sessions=20)
    return WatchlistCaptureWorker(
        config=cfg, store=store, fetcher=_FakeFetcher(daily), market_windows=_FakeMW(),
        logger=None, now_fn=lambda: now)


def test_capture_settled_eod_persists_row():
    b = date(2026, 7, 10)
    daily = _daily(20, b, highs=[100.0 + i for i in range(20)])
    s = _store()
    w = _worker(s, daily, now=datetime(2026, 7, 10, 16, 0))   # post-close → settled
    w._capture("pb01_breakout_retest", "ABB", datetime(2026, 7, 10, 15, 40))
    rows = s.get_pb01_watchlist_for_date("2026-07-11")        # next trading day
    assert len(rows) == 1 and rows[0]["level"] == 119.0 and rows[0]["breakout_date"] == "2026-07-10"
    s.close()


def test_capture_skips_forming_intraday_candle():
    b = date(2026, 7, 10)
    daily = _daily(20, b, highs=[100.0 + i for i in range(20)])
    s = _store()
    # alert fired intraday (11:00) on the breakout day → forming session → SKIP.
    w = _worker(s, daily, now=datetime(2026, 7, 10, 11, 0))
    w._capture("pb01_breakout_retest", "ABB", datetime(2026, 7, 10, 11, 0))
    assert s.get_pb01_watchlist_for_date("2026-07-11") == []
    s.close()


def test_capture_dedupes_repeat_firing():
    b = date(2026, 7, 10)
    daily = _daily(20, b, highs=[100.0 + i for i in range(20)])
    s = _store()
    w = _worker(s, daily, now=datetime(2026, 7, 10, 16, 0))
    w._capture("pb01_breakout_retest", "ABB", datetime(2026, 7, 10, 15, 40))
    w._capture("pb01_breakout_retest", "ABB", datetime(2026, 7, 10, 15, 45))   # repeat
    assert len(s.get_pb01_watchlist_for_date("2026-07-11")) == 1              # deduped
    s.close()


if __name__ == "__main__":
    import sys
    mod = sys.modules[__name__]
    for name in sorted(dir(mod)):
        if name.startswith("test_"):
            getattr(mod, name)()
            print(f"  OK {name}")
    print("\nT2 capture + watchlist store: all checks passed.")
