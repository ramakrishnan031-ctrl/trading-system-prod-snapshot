"""
tests/unit/test_v3_pb01_entry.py — V3 Step 10b · T3 next-morning entry stage.

Drives Pb01EntryStage.poll_once() deterministically against a fake OhlcFetcher + a real
StateStore, proving the full state machine (CONSUMED / SKIPPED_GAP / INVALIDATED /
EXPIRED_WINDOW), G-FORMING-CANDLE, G-NO-REHYDRATION, first-retest-only, restart-safety
(re-derivation is idempotent), and G-NO-ORDER (the only side effects are the status
update + the on_confirm handoff — the stage has NO order/reserve path).
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace

from core.config_loader import V3ChainConfig, WatchlistConfig
from core.state_store import StateStore
from sr_detector.models import Candle
from v3_chain.pb01_entry import Pb01Confirmation, Pb01EntryStage

TODAY = date(2026, 7, 13)          # the trading_date (D+1)
LEVEL = 100.0


# ── candle builders ───────────────────────────────────────────────────────────

def _c(hh, mm, o, h, l, cl, v, day=TODAY):
    return Candle(ts=datetime.combine(day, time(hh, mm)), open=o, high=h, low=l,
                  close=cl, volume=int(v))


def _five_retest():
    """A clean retest: open flat (no gap), pull back to touch LEVEL, then a strong
    confirmation candle at 09:25 (close>LEVEL, body>=0.5, volume>=1.2×baseline)."""
    return [
        _c(9, 15, 100.5, 101.0, 100.4, 100.6, 800),    # opening candle (no gap)
        _c(9, 20, 100.4, 100.5, 100.05, 100.2, 600),   # pullback, weak body → no confirm
        _c(9, 25, 100.1, 101.5, 100.0, 101.2, 2000),   # CONFIRM: strong body + volume
    ]


def _thirty_prior():
    """20 prior-day 30-min bars, each TR=2.0 → ATR(14) = 2.0 (deterministic)."""
    return [_c(10, 0, 99.0, 100.0, 98.0, 99.0, 5000, day=date(2026, 7, 10))
            for _ in range(20)]


def _daily_prior():
    """20 prior daily bars, volume 75000 → SMA20/75 = baseline_5m_volume 1000."""
    out = []
    for i in range(20):
        d = date.fromordinal(TODAY.toordinal() - (20 - i))
        out.append(Candle(ts=datetime.combine(d, time(9, 15)), open=90, high=95, low=89,
                          close=94, volume=75000))
    return out


class _FakeFetcher:
    def __init__(self, five, thirty=None, daily=None):
        self._five = five
        self._thirty = thirty if thirty is not None else _thirty_prior()
        self._daily = daily if daily is not None else _daily_prior()
        self.calls = []

    def fetch_interval(self, symbol, interval, lookback_days=None):
        self.calls.append((symbol, interval, lookback_days))
        if interval == "5minute":
            return list(self._five)
        if interval == "30minute":
            return list(self._thirty)
        if interval == "day":
            return list(self._daily)
        return []


class _FakeMW:
    market_open = time(9, 15)


def _store():
    return StateStore(Path(tempfile.mkdtemp()) / "entry.db")


def _row(store, symbol="ABB", trading_date=TODAY.isoformat(), level=LEVEL):
    store.insert_pb01_watchlist({
        "symbol": symbol, "trading_date": trading_date, "level": level,
        "breakout_date": "2026-07-10", "source": "pb01_breakout_retest",
        "sr_zone_json": None, "status": "PENDING", "outcome_json": None,
        "captured_at": "t", "created_at": "t"})


def _stage(store, fetcher, now, on_confirm=None):
    return Pb01EntryStage(
        config=WatchlistConfig(), v3_cfg=V3ChainConfig(), store=store, fetcher=fetcher,
        market_windows=_FakeMW(), on_confirm=(on_confirm or (lambda c: None)),
        logger=None, now_fn=lambda: now)


def _status(store, trading_date=TODAY.isoformat(), symbol="ABB"):
    rows = store.get_pb01_watchlist_for_date(trading_date)
    return next(r["status"] for r in rows if r["symbol"] == symbol)


# ── CONSUMED (a confirmation fires) ───────────────────────────────────────────

def test_confirmation_consumes_and_hands_off():
    s = _store(); _row(s)
    fired = []
    st = _stage(s, _FakeFetcher(_five_retest()), datetime(2026, 7, 13, 9, 40),
                on_confirm=fired.append)
    st.poll_once()
    assert _status(s) == "CONSUMED"
    assert len(fired) == 1
    conf: Pb01Confirmation = fired[0]
    assert isinstance(conf, Pb01Confirmation)
    assert conf.entry_price == 101.2          # confirmation candle close
    assert conf.level == 100.0
    assert conf.pullback_low == 100.0         # session low at confirmation
    assert conf.as_of == datetime(2026, 7, 13, 9, 30)   # 09:25 bar CLOSE = decision instant
    assert abs(conf.atr30 - 2.0) < 1e-9
    assert abs(conf.baseline_5m_volume - 1000.0) < 1e-9
    s.close()


def test_first_retest_only_no_second_fire():
    s = _store(); _row(s)
    fired = []
    st = _stage(s, _FakeFetcher(_five_retest()), datetime(2026, 7, 13, 9, 40),
                on_confirm=fired.append)
    st.poll_once()
    st.poll_once()   # a second poll must NOT re-fire (row is terminal / no longer PENDING)
    assert len(fired) == 1
    s.close()


# ── SKIPPED_GAP ───────────────────────────────────────────────────────────────

def test_gap_guard_skips():
    s = _store(); _row(s)
    gapped = [_c(9, 15, 104.0, 105.0, 103.5, 104.5, 800)]   # open 104 > 100×1.03
    fired = []
    st = _stage(s, _FakeFetcher(gapped), datetime(2026, 7, 13, 9, 40), on_confirm=fired.append)
    st.poll_once()
    assert _status(s) == "SKIPPED_GAP"
    assert fired == []          # never handed off
    s.close()


# ── INVALIDATED ───────────────────────────────────────────────────────────────

def test_invalidation_on_close_below_hold_floor():
    s = _store(); _row(s)
    # hold_floor = 100 - 0.20×ATR30(2.0) = 99.6 ; a 5-min close 99.0 < 99.6 → INVALIDATED.
    broke = [_c(9, 15, 100.5, 101.0, 100.4, 100.6, 800),
             _c(9, 20, 100.3, 100.4, 98.5, 99.0, 900),
             _c(9, 25, 99.2, 101.5, 99.0, 101.2, 2000)]     # would confirm, but too late
    fired = []
    st = _stage(s, _FakeFetcher(broke), datetime(2026, 7, 13, 9, 40), on_confirm=fired.append)
    st.poll_once()
    assert _status(s) == "INVALIDATED"
    assert fired == []
    s.close()


# ── EXPIRED_WINDOW ────────────────────────────────────────────────────────────

def test_expiry_after_window_close():
    s = _store(); _row(s)
    st = _stage(s, _FakeFetcher(_five_retest()), datetime(2026, 7, 13, 11, 5))  # past 11:00
    st.poll_once()
    assert _status(s) == "EXPIRED_WINDOW"
    s.close()


# ── G-FORMING-CANDLE (never act on an unclosed candle) ────────────────────────

def test_forming_candle_not_acted_on():
    s = _store(); _row(s)
    fired = []
    f = _FakeFetcher(_five_retest())
    # now = 09:23: the 09:25 confirmation candle (closes 09:30) is still FORMING → the
    # stage must NOT confirm; the row stays PENDING.
    st = _stage(s, f, datetime(2026, 7, 13, 9, 23), on_confirm=fired.append)
    st.poll_once()
    assert _status(s) == "PENDING" and fired == []
    # advance to 09:30: the candle has CLOSED → now it confirms.
    st2 = _stage(s, f, datetime(2026, 7, 13, 9, 30), on_confirm=fired.append)
    st2.poll_once()
    assert _status(s) == "CONSUMED" and len(fired) == 1
    s.close()


# ── before the window opens: nothing happens ──────────────────────────────────

def test_before_window_noop():
    s = _store(); _row(s)
    st = _stage(s, _FakeFetcher(_five_retest()), datetime(2026, 7, 13, 9, 0))  # < 09:20
    st.poll_once()
    assert _status(s) == "PENDING"
    s.close()


# ── G-NO-REHYDRATION (only today's rows are ever loaded) ──────────────────────

def test_stale_row_never_fires():
    s = _store()
    _row(s, trading_date="2026-07-10")   # a stale row (an earlier day)
    fired = []
    # today = 2026-07-13 → the stale 07-10 row is never loaded → never fires.
    st = _stage(s, _FakeFetcher(_five_retest()), datetime(2026, 7, 13, 9, 40),
                on_confirm=fired.append)
    st.poll_once()
    assert fired == []
    assert _status(s, trading_date="2026-07-10") == "PENDING"   # untouched
    s.close()


# ── restart-safety: re-derivation is idempotent ───────────────────────────────

def test_reevaluation_is_deterministic():
    # Two independent stages (a "restart") over the same store+data reach the SAME
    # terminal state; the row's status is the only durable state.
    s = _store(); _row(s)
    f = _FakeFetcher(_five_retest())
    _stage(s, f, datetime(2026, 7, 13, 9, 40)).poll_once()
    assert _status(s) == "CONSUMED"
    # a fresh stage (fresh in-memory memo) sees the terminal row and does nothing new.
    fired = []
    _stage(s, f, datetime(2026, 7, 13, 9, 45), on_confirm=fired.append).poll_once()
    assert fired == [] and _status(s) == "CONSUMED"
    s.close()


if __name__ == "__main__":
    import sys
    mod = sys.modules[__name__]
    for name in sorted(dir(mod)):
        if name.startswith("test_"):
            getattr(mod, name)()
            print(f"  OK {name}")
    print("\nPB-01 entry stage: all checks passed.")
