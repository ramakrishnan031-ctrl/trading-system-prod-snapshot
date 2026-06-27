"""
tests/unit/test_sr_v2_zone_cache.py — SNR-V2 ZoneCache + ZoneWarmer.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from core.config_loader import SRDetectorConfig
from sr_detector.models import Candle, ScoredZone
from sr_detector.zone_builder import build_scoring_params, build_zone_knobs
from sr_detector.zone_cache import ZoneCache
from sr_detector.zone_warmer import ZoneWarmer

_T0 = datetime(2026, 6, 26, 14, 0)


class _Clock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t


def _zone(kind):
    return ScoredZone(100.0, 101.0, kind, 6.0, "HIGH", 5, ("day", "60minute"))


# ── ZoneCache ─────────────────────────────────────────────────────────────────

def test_put_then_get_returns_zoneset():
    c = ZoneCache(ttl_sec=1800, now_fn=lambda: _T0)
    c.put("X", [_zone("RESISTANCE")], [_zone("SUPPORT")])
    zs = c.get("X")
    assert zs is not None and len(zs.resistance) == 1 and len(zs.support) == 1


def test_miss_returns_none():
    c = ZoneCache(ttl_sec=1800, now_fn=lambda: _T0)
    assert c.get("UNKNOWN") is None


def test_expiry_is_a_miss():
    clk = _Clock(_T0)
    c = ZoneCache(ttl_sec=60, now_fn=clk.now)
    c.put("X", [_zone("RESISTANCE")], [])
    clk.t = _T0 + timedelta(seconds=61)
    assert c.get("X") is None


def test_symbols_due_for_rewarm():
    clk = _Clock(_T0)
    c = ZoneCache(ttl_sec=1800, now_fn=clk.now)
    c.put("X", [_zone("RESISTANCE")], [])
    clk.t = _T0 + timedelta(seconds=1600)        # within 300s of the 1800 TTL
    assert "X" in c.symbols_due_for_rewarm(margin_sec=300)
    clk.t = _T0 + timedelta(seconds=1000)
    assert c.symbols_due_for_rewarm(margin_sec=300) == []


# ── ZoneWarmer ──────────────────────────────────────────────────────────────

def _triangle(n, base_ts, step_min, lo=480.0, hi=520.0, period=20):
    out, half = [], period // 2
    for i in range(n):
        ph = i % period
        price = lo + (hi - lo) * (ph / half) if ph <= half else hi - (hi - lo) * ((ph - half) / half)
        out.append(Candle(ts=base_ts + timedelta(minutes=step_min * i), open=price,
                          high=price + 0.5, low=price - 0.5, close=price, volume=1000))
    return out


class _FakeFetcher:
    def __init__(self, miss=False):
        self.miss = miss
        self.calls = []

    def fetch_timeframes(self, symbol, intervals):
        self.calls.append(symbol)
        if self.miss:
            return {}
        return {
            "day": _triangle(140, _T0 - timedelta(days=140), 1440),
            "60minute": _triangle(140, _T0 - timedelta(hours=140), 60),
            "30minute": _triangle(140, _T0 - timedelta(minutes=30 * 140), 30),
        }


def _warmer(fetcher):
    cfg = SRDetectorConfig()
    cache = ZoneCache(ttl_sec=1800, now_fn=lambda: _T0)
    w = ZoneWarmer(
        fetcher=fetcher, cache=cache, knobs=build_zone_knobs(cfg),
        scoring=build_scoring_params(cfg), logger=None, now_fn=lambda: _T0)
    return w, cache


def test_warm_caches_resistance_and_support():
    w, cache = _warmer(_FakeFetcher())
    assert w.warm("TESTSTK") is True
    zs = cache.get("TESTSTK")
    assert zs is not None
    assert any(z.confidence == "HIGH" for z in zs.resistance)   # the 520 zone
    assert len(zs.support) >= 1                                  # support cached too (Phase B)


def test_warm_fetch_miss_leaves_cache_untouched():
    w, cache = _warmer(_FakeFetcher(miss=True))
    assert w.warm("TESTSTK") is False
    assert cache.get("TESTSTK") is None


def test_enqueue_dedups():
    w, _ = _warmer(_FakeFetcher())
    w.enqueue("AAA")
    w.enqueue("AAA")
    # both calls accepted but the pending set dedups → at most one queued entry
    assert w._q.qsize() == 1
