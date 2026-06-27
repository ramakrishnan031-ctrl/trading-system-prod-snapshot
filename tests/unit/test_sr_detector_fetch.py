"""
tests/unit/test_sr_detector_fetch.py — SNR-DETECTOR-V1 fetch helper.

Covers (spec K): the injected fetch closure paces via rate_limiter("historical");
the OhlcFetcher returns OHLCV Candles, caches within the session, and FAILS SAFE
(a fetch error / missing token yields no candles, never raises).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sr_detector.fetch import OhlcFetcher
from sr_detector.models import Candle

_NOW = datetime(2026, 6, 26, 12, 0)


def _row(i, price=100.0):
    return {"date": _NOW - timedelta(days=i), "open": price, "high": price + 1,
            "low": price - 1, "close": price, "volume": 1000}


class _RL:
    def __init__(self):
        self.calls = []

    def acquire(self, category, n=1):
        self.calls.append(category)


class _Kite:
    def __init__(self):
        self.calls = []

    def historical_data(self, **kw):
        self.calls.append(kw)
        return [_row(1), _row(2)]


class _Row:
    instrument_token = 999


class _Cache:
    def __init__(self, raise_it=False):
        self._raise = raise_it

    def get_by_symbol(self, sym):
        if self._raise:
            raise KeyError(sym)
        return _Row()


def test_closure_paces_via_rate_limiter_then_calls_kite():
    from main import _make_sr_fetch_fn
    rl, kite = _RL(), _Kite()
    fetch = _make_sr_fetch_fn(kite, rl)
    out = fetch(999, _NOW - timedelta(days=180), _NOW, "day")
    assert rl.calls == ["historical"]            # paced
    assert kite.calls[0]["interval"] == "day"    # then the broker call
    assert out and out[0]["close"] == 100.0


def test_closure_failsafe_when_no_kite():
    rl = _RL()
    from main import _make_sr_fetch_fn
    fetch = _make_sr_fetch_fn(None, rl)
    assert fetch(1, _NOW, _NOW, "day") == []
    assert rl.calls == []                         # no token consumed when no kite


def test_fetcher_returns_candles_and_caches():
    calls = {"n": 0}

    def fetch_fn(token, frm, to, interval):
        calls["n"] += 1
        return [_row(1), _row(2)]

    f = OhlcFetcher(fetch_fn, _Cache(), lookback_days=180, logger=None,
                    now_fn=lambda: _NOW, cache_ttl_sec=1800)
    out = f.fetch_timeframes("X", ["day", "60minute"])
    assert set(out) == {"day", "60minute"}
    assert all(isinstance(c, Candle) for c in out["day"])
    n_after_first = calls["n"]
    # second call within TTL is served from cache (no new fetch_fn calls)
    f.fetch_timeframes("X", ["day", "60minute"])
    assert calls["n"] == n_after_first


def test_fetcher_failsafe_on_fetch_error():
    def boom(*a, **k):
        raise RuntimeError("broker down")

    f = OhlcFetcher(boom, _Cache(), lookback_days=180, logger=None, now_fn=lambda: _NOW)
    # never raises; an all-failed fetch yields an empty dict -> detector logs fetch_failed
    assert f.fetch_timeframes("X", ["day", "60minute"]) == {}


def test_fetcher_failsafe_on_token_lookup_error():
    f = OhlcFetcher(lambda *a, **k: [_row(1)], _Cache(raise_it=True),
                    lookback_days=180, logger=None, now_fn=lambda: _NOW)
    assert f.fetch_timeframes("MISSING", ["day"]) == {}


def test_fetcher_empty_result_omits_interval():
    f = OhlcFetcher(lambda *a, **k: [], _Cache(), lookback_days=180,
                    logger=None, now_fn=lambda: _NOW)
    assert f.fetch_timeframes("X", ["day"]) == {}
