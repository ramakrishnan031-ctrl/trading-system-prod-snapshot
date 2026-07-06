"""
tests/unit/test_h9_candle_offbyone_landmine.py

Wave-3 / H-9 — CandleStore had (a) an off-by-one in `_last_closed_window` and
(b) a DEAD late-tick discard keyed on `exchange_timestamp` that was also a
LANDMINE (wiring it, with the off-by-one, would discard every current-minute
tick → the store goes 100% synthetic).

DECISION = DELETE: the discard was never wired (the live dispatcher passes
exchange_timestamp=None), so removing it has zero runtime effect and removes the
landmine by construction; exchange_timestamp is only reliably present in
KiteTicker `full` mode, so wiring it would risk dropping real ticks on the candle
data every strategy consumes. The off-by-one is corrected unconditionally, and a
synthetic-only guard makes a silently-synthetic store LOUD.

These tests drive the REAL CandleStore aggregator. now_ist is patched to a fixed
clock only to make the window-boundary assertion deterministic; the logger is a
MagicMock (to observe the guard warning).

Real collaborators:  CandleStore.on_tick / _close_candles / _Accumulator /
                     get_candles (the real aggregator).
Simulated:           logger (MagicMock); now_ist patched to a fixed instant for
                     the off-by-one boundary test.

RED/GREEN: test_h9_last_closed_window_off_by_one asserts `_last_closed_window` is
the window that JUST CLOSED (10:00), not the new one (10:01). It FAILS against the
off-by-one (stamped 10:01) and PASSES after the fix.

Run: python -m pytest tests/unit/test_h9_candle_offbyone_landmine.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from data.candle_store import CandleStore

_IST = timezone(timedelta(hours=5, minutes=30))
_INTERVAL = 60


def _store() -> CandleStore:
    return CandleStore(MagicMock(), candle_interval_sec=_INTERVAL)


def _tick(store: CandleStore, token: int, ltp: float) -> None:
    # ts is informational only post-H-9; pass a plausible received time.
    store.on_tick(token, ltp, datetime.now(_IST))


# ── Test 1 — off-by-one boundary (core; RED before fix) ──────────────────────

def test_h9_last_closed_window_off_by_one() -> None:
    store = _store()
    store.set_token_map({101: "X"})
    # Timer fires AT 10:01:00 to close the 10:00 window.
    fired_at = datetime(2026, 5, 15, 10, 1, 0, tzinfo=_IST)
    with patch("data.candle_store.now_ist", return_value=fired_at):
        _tick(store, 101, 100.0)
        store._close_candles()

    expected_last_closed = fired_at.replace(second=0, microsecond=0) - timedelta(seconds=_INTERVAL)
    # 10:00, NOT 10:01. Off-by-one stamped 10:01 -> this FAILS (RED).
    assert store._last_closed_window == expected_last_closed
    assert store._last_closed_window == datetime(2026, 5, 15, 10, 0, 0, tzinfo=_IST)
    print("  OK H-9 T1: _last_closed_window = the window that closed (10:00), "
          "not the new one (10:01)  [RED before fix]")


# ── Test 2 — candles are REAL, not synthetic (landmine guard, mandatory) ─────

def test_h9_ticks_build_real_candles_not_synthetic() -> None:
    store = _store()
    store.set_token_map({101: "X"})
    for ltp in (100.0, 110.0, 90.0, 105.0):
        _tick(store, 101, ltp)
    store._close_candles()

    candles = store.get_candles(101, n=1)
    assert len(candles) == 1
    c = candles[0]
    # A normal tick stream must produce a REAL candle reflecting the fed ticks —
    # NOT a synthetic carry-forward. (Under the old discard+off-by-one wired, these
    # would have been dropped as "late" and the store would go 100% synthetic.)
    assert c.is_synthetic is False
    assert (c.open, c.high, c.low, c.close) == (100.0, 110.0, 90.0, 105.0)
    assert c.volume == 0  # no volume passed
    print("  OK H-9 T2: real ticks -> real candle (not synthetic); discard removed")


def test_h9_no_discard_all_ticks_accepted_including_out_of_order() -> None:
    # Formerly-"late" out-of-order ticks are now ACCEPTED (no exchange_timestamp
    # gate). All four fold into the one clock-driven window.
    store = _store()
    store.set_token_map({101: "X"})
    for ltp in (100.0, 108.0, 95.0, 101.0):
        _tick(store, 101, ltp)
    store._close_candles()
    c = store.get_candles(101, n=1)[0]
    assert (c.open, c.high, c.low, c.close) == (100.0, 108.0, 95.0, 101.0)
    print("  OK H-9 T2b: no tick discarded — all ticks build the candle")


# ── Test 3 — synthetic-only guard makes a starved feed LOUD ──────────────────

def test_h9_synthetic_only_guard_warns() -> None:
    store = _store()
    store.set_token_map({101: "X"})
    # Build one real candle so the token has history (synthetic candidate).
    _tick(store, 101, 100.0)
    store._close_candles()
    store._log.warning.reset_mock()

    # Three consecutive closes with NO ticks -> all-synthetic -> guard warns at 3.
    for _ in range(3):
        store._close_candles()

    assert store._consecutive_synthetic_only == 3
    warned = " ".join(str(c) for c in store._log.warning.call_args_list)
    assert "ZERO real ticks" in warned
    print("  OK H-9 T3: 3 consecutive tick-less closes -> synthetic-only WARNING")


def test_h9_guard_resets_on_real_tick() -> None:
    store = _store()
    store.set_token_map({101: "X"})
    _tick(store, 101, 100.0)
    store._close_candles()               # real -> counter 0
    store._close_candles()               # synthetic-only -> 1
    assert store._consecutive_synthetic_only == 1
    _tick(store, 101, 101.0)
    store._close_candles()               # real again -> reset
    assert store._consecutive_synthetic_only == 0
    print("  OK H-9 T3b: guard counter resets when real ticks resume")


# ── Test 4 — consumer sanity: get_candles returns the corrected candle ───────

def test_h9_consumer_get_candles_returns_real_ohlc() -> None:
    store = _store()
    store.set_token_map({101: "X"})
    _tick(store, 101, 200.0)
    _tick(store, 101, 260.0)
    _tick(store, 101, 190.0)
    store._close_candles()
    # The candle-consuming path (e.g. smart_tgt_manager.get_candles) reads OHLC.
    got = store.get_candles(101, n=1)
    assert len(got) == 1 and got[0].high == 260.0 and got[0].low == 190.0
    print("  OK H-9 T4: candle consumer reads correct OHLC")


if __name__ == "__main__":
    test_h9_last_closed_window_off_by_one()
    test_h9_ticks_build_real_candles_not_synthetic()
    test_h9_no_discard_all_ticks_accepted_including_out_of_order()
    test_h9_synthetic_only_guard_warns()
    test_h9_guard_resets_on_real_tick()
    test_h9_consumer_get_candles_returns_real_ohlc()
    print("\nAll H-9 tests passed.")
