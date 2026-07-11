"""
tests/unit/test_market_regime.py — V3 03.02 Market Regime engine (Step 3).

Spec tests T1–T8 + fail-safe + determinism. The engine is SHADOW and pure w.r.t.
injected collaborators (fetcher / market_windows / exchange_status_fn) → the same
tests hold for paper and live (no mode branch).
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from core.config_loader import RegimeConfig
from regime import MarketRegimeShadowRunner, build_market_regime
from regime.models import (
    CONFIDENCE_MULTIPLIER,
    DAY_UNDETERMINED,
    DIR_BULL,
    DIR_SIDEWAYS,
    STATUS_OK,
    STATUS_UNKNOWN,
    VOL_HIGH,
)
from sr_detector.models import Candle

_MID = datetime(2026, 7, 6, 13, 0)     # ~59% through a 09:15–15:30 session
_EARLY = datetime(2026, 7, 6, 9, 20)   # 5 min in


# ── fakes ─────────────────────────────────────────────────────────────────────

class _FakeFetcher:
    def __init__(self, by_interval):
        self._by = by_interval

    def fetch_by_token(self, token, interval, lookback_days=None):
        return self._by.get(interval)


class _MW:
    market_open = time(9, 15)
    market_close = time(15, 30)

    def is_market_open(self, now):
        return True


class _RecLog:
    def __init__(self):
        self.critical_msgs = []

    def _rec(self, level, msg, *a):
        if level == "critical":
            self.critical_msgs.append(msg % a if a else msg)

    def critical(self, msg, *a):
        self._rec("critical", msg, *a)

    def info(self, *a):
        pass

    warning = error = debug = info


# ── candle builders (deterministic) ───────────────────────────────────────────

def _cd(day_offset, close, hi=None, lo=None):
    ts = datetime(2026, 1, 1) + timedelta(days=day_offset)
    c = float(close)
    return Candle(ts=ts, open=c, high=hi if hi is not None else c + 1.0,
                  low=lo if lo is not None else c - 1.0, close=c, volume=1000)


def _bull_daily(n=264):
    """Rising STAIRCASE: 5-bar up-thrust then a shallow 3-bar pullback. The
    pullback is deep/long enough (vs a 3-bar pivot window) that each thrust peak
    is a genuine swing HIGH and each pullback trough a swing LOW — both rising →
    HH/HL structure — while the net +14/cycle keeps ADX well above the trend
    threshold and price above both EMAs."""
    out, price = [], 100.0
    while len(out) < n:
        for _ in range(5):
            price += 4.0
            out.append(_cd(len(out), price))
        for _ in range(3):
            price -= 2.0
            out.append(_cd(len(out), price))
    return out[:n]


def _choppy_daily(n=260):
    """Flat index sitting on its EMAs → no directional votes → SIDEWAYS."""
    return [_cd(i, 100.0, hi=101.0, lo=99.0) for i in range(n)]


def _highvol_bull_daily(n=260):
    """Bull uptrend whose LAST bars accelerate (bigger bars) → cur ATR ≫ baseline
    → HIGH vol AND still BULL."""
    out, price = [], 100.0
    for i in range(n):
        step = 2.0 if i < n - 30 else 8.0     # accelerate the tail
        if i > 0 and i % 10 == 0:
            price -= step * 0.6
        else:
            price += step
        rng = 1.0 if i < n - 30 else 4.0       # widen intrabar range on the tail
        out.append(_cd(i, price, hi=price + rng, lo=price - rng))
    return out


def _intraday(n, start=(9, 15), rising=True):
    out, price = [], 100.0
    for i in range(n):
        ts = datetime(2026, 7, 6, start[0], start[1]) + timedelta(minutes=5 * i)
        price += 2.0 if rising else 0.0
        out.append(Candle(ts=ts, open=price - 1, high=price + 1, low=price - 2, close=price, volume=500))
    return out


def _engine(by_interval, *, now=_MID, exchange_status_fn=None, logger=None, enabled=True):
    return build_market_regime(
        config=RegimeConfig(enabled=enabled),
        fetcher=_FakeFetcher(by_interval),
        logger=logger or logging.getLogger("test_regime"),
        now_fn=lambda: now,
        market_windows=_MW(),
        exchange_status_fn=exchange_status_fn,
    )


# ── T1: bull, high confidence ────────────────────────────────────────────────

def test_T1_bull_high_confidence():
    eng = _engine({"day": _bull_daily(), "5minute": _intraday(30)})
    st = eng.compute()
    assert st.status == STATUS_OK
    assert st.direction.value == DIR_BULL
    assert st.direction.confidence == "HIGH"
    assert st.preference_multiplier == 1.0        # HIGH → 1.0


# ── T2: choppy → sideways, low/transition (multiplier ≤ 0.2) ─────────────────

def test_T2_choppy_sideways_low_multiplier():
    eng = _engine({"day": _choppy_daily(), "5minute": _intraday(30)})
    st = eng.compute()
    assert st.direction.value == DIR_SIDEWAYS
    assert st.direction.confidence in ("LOW", "TRANSITION")
    assert st.direction.multiplier <= 0.2


# ── T3: early session → day-type UNDETERMINED, low conf ──────────────────────

def test_T3_early_session_daytype_undetermined():
    # few intraday bars → undetermined regardless of session clock
    eng = _engine({"day": _bull_daily(), "5minute": _intraday(2)})
    st = eng.compute()
    assert st.day_type.value == DAY_UNDETERMINED
    assert st.day_type.confidence == "LOW"
    # also: full data but EARLY in the session → still undetermined/low
    eng2 = _engine({"day": _bull_daily(), "5minute": _intraday(2, start=(9, 15))}, now=_EARLY)
    st2 = eng2.compute()
    assert st2.day_type.value == DAY_UNDETERMINED


# ── T4: confirmed halt → extreme_flag TRUE ───────────────────────────────────

def test_T4_confirmed_halt_sets_extreme():
    for status in ("HALT", True, {"halted": True}, {"market_status": "CLOSED"}):
        eng = _engine({"day": _bull_daily(), "5minute": _intraday(30)},
                      exchange_status_fn=lambda s=status: s)
        assert eng.compute().extreme_flag is True


# ── T5: status feed DROPPED → extreme_flag FALSE + CRITICAL (not a halt) ──────

def test_T5_missing_status_feed_no_extreme_but_critical():
    log = _RecLog()
    eng = _engine({"day": _bull_daily(), "5minute": _intraday(30)},
                  exchange_status_fn=None, logger=log)
    st = eng.compute()
    assert st.extreme_flag is False               # absence is NOT a confirmed halt
    assert st.status == STATUS_OK                  # feed missing ≠ book halted
    assert any("exchange-status feed MISSING" in m for m in log.critical_msgs)


# ── T6: index data missing → UNKNOWN, multiplier 0 (book not halted) ─────────

def test_T6_missing_index_data_unknown_neutral():
    eng = _engine({"day": None, "5minute": None})   # fetch returns nothing
    st = eng.compute()
    assert st.status == STATUS_UNKNOWN
    assert st.preference_multiplier == 0.0
    assert st.direction.multiplier == 0.0 and st.volatility.multiplier == 0.0
    assert st.extreme_flag is False                 # neutral, not a stop


# ── T7: determinism ──────────────────────────────────────────────────────────

def test_T7_determinism():
    data = {"day": _bull_daily(), "5minute": _intraday(30)}
    assert _engine(data).compute() == _engine(data).compute()


# ── T8: BULL + HIGH_VOL emitted independently ────────────────────────────────

def test_T8_bull_and_high_vol_both_emitted():
    eng = _engine({"day": _highvol_bull_daily(), "5minute": _intraday(30)})
    st = eng.compute()
    assert st.direction.value == DIR_BULL          # direction axis
    assert st.volatility.value == VOL_HIGH         # volatility axis, independent
    assert st.status == STATUS_OK


# ── fail-safe: an axis error degrades to neutral, never crashes ──────────────

def test_axis_error_degrades_to_neutral():
    eng = _engine({"day": _bull_daily(), "5minute": _intraday(30)})

    def _boom(daily):
        raise RuntimeError("indicator blew up")

    eng._volatility = _boom                          # force one axis to fail
    st = eng.compute()
    assert st.status == STATUS_OK                     # other axes fine
    assert st.volatility.confidence == "TRANSITION"  # most-uncertain, multiplier 0
    assert st.volatility.multiplier == 0.0


def test_compute_never_raises_on_garbage_fetcher():
    class _Boom:
        def fetch_by_token(self, *a, **k):
            raise RuntimeError("network down")
    eng = build_market_regime(
        config=RegimeConfig(enabled=True), fetcher=_Boom(),
        logger=logging.getLogger("t"), now_fn=lambda: _MID,
        market_windows=_MW(), exchange_status_fn=None)
    st = eng.compute()
    assert st.status == STATUS_UNKNOWN and st.preference_multiplier == 0.0


# ── confidence → multiplier mapping (Task 4) ─────────────────────────────────

def test_confidence_multiplier_mapping():
    assert CONFIDENCE_MULTIPLIER == {"HIGH": 1.0, "MEDIUM": 0.5, "LOW": 0.2, "TRANSITION": 0.0}


# ── shadow runner: run_once computes + persists; disabled → no thread ────────

def test_runner_run_once_and_persist(tmp_path):
    path = str(tmp_path / "regime.json")
    eng = _engine({"day": _bull_daily(), "5minute": _intraday(30)})
    runner = MarketRegimeShadowRunner(engine=eng, logger=logging.getLogger("t"),
                                      now_fn=lambda: _MID, market_windows=_MW(),
                                      persist_path=path)
    st = runner.run_once()
    assert st.direction.value == DIR_BULL
    from regime.runner import read_persisted_regime
    payload = read_persisted_regime(path)
    assert payload is not None and payload["regime"]["direction"]["value"] == DIR_BULL


def test_runner_disabled_does_not_start():
    eng = _engine({"day": _bull_daily()}, enabled=False)
    runner = MarketRegimeShadowRunner(engine=eng, logger=logging.getLogger("t"),
                                      now_fn=lambda: _MID, market_windows=_MW())
    runner.start()
    assert runner._thread is None                    # disabled engine → no daemon
