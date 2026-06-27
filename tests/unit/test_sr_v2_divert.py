"""
tests/unit/test_sr_v2_divert.py — SNR-V2 pre-placement divert (RetestDiverter).

in-zone HIGH long → divert (nothing reserved); out-of-zone → normal; cache MISS →
normal (fall-through); flag off → normal; non-long → normal; dup symbol → dropped.
"""
from __future__ import annotations

from datetime import datetime

from core.config_loader import SRDetectorConfig
from sr_detector.models import ScoredZone
from sr_detector.zone_cache import ZoneCache
from screening.retest_monitor import RetestDiverter

_T0 = datetime(2026, 6, 26, 14, 0)


class _FakeMonitor:
    def __init__(self, has=False):
        self.registered = []
        self._has = has

    def register(self, parked):
        self.registered.append(parked)

    def has_symbol(self, symbol):
        return self._has


class _FakeStore:
    def __init__(self):
        self.status = []
        self.sr_rows = []

    def update_signal_status(self, sid, status, reason=None):
        self.status.append((sid, status, reason))

    def insert_sr_detector_result(self, row):
        self.sr_rows.append(row)


def _high_res():
    return ScoredZone(100.0, 101.0, "RESISTANCE", 7.0, "HIGH", 8, ("day", "60minute", "30minute"))


def _diverter(*, enabled=True, monitor=None, cache=None, store=None):
    cfg = SRDetectorConfig(wait_for_retest_enabled=enabled)
    cache = cache or ZoneCache(ttl_sec=1800, now_fn=lambda: _T0)
    return RetestDiverter(
        zone_cache=cache, monitor=monitor or _FakeMonitor(), state_store=store or _FakeStore(),
        config=cfg, logger=None, now_fn=lambda: _T0, mode="live"), cache


def _call(d, *, side="BUY", entry=100.5, symbol="X"):
    return d.maybe_divert(
        signal_id="SIG1", symbol=symbol, side=side, direction="LONG",
        entry_price=entry, sl_price=98.0, strategy_name="strat", intent="INTRADAY",
        tier="A", trigger_price=100.4, score=62)


def test_in_zone_high_long_diverts():
    mon, store = _FakeMonitor(), _FakeStore()
    d, cache = _diverter(monitor=mon, store=store)
    cache.put("X", [_high_res()], [])
    assert _call(d) is True
    assert len(mon.registered) == 1
    parked = mon.registered[0]
    assert parked.zone_band_high == 101.0 and parked.state == "WAIT_BREAKOUT"
    assert ("SIG1", "RETEST_WAITING", None) in store.status
    assert store.sr_rows and store.sr_rows[0]["would_wait_for_retest"] == 1  # audited


def test_out_of_zone_falls_through():
    d, cache = _diverter()
    cache.put("X", [_high_res()], [])
    assert _call(d, entry=90.0) is False     # entry far below the band


def test_cache_miss_falls_through():
    d, _ = _diverter()                        # empty cache → miss
    assert _call(d) is False


def test_flag_off_never_diverts():
    d, cache = _diverter(enabled=False)
    cache.put("X", [_high_res()], [])
    assert d.enabled is False
    assert _call(d) is False


def test_short_side_never_diverts():
    d, cache = _diverter()
    cache.put("X", [_high_res()], [])
    assert _call(d, side="SELL") is False     # Phase A is long-only


def test_medium_confidence_does_not_divert():
    d, cache = _diverter()
    cache.put("X", [ScoredZone(100.0, 101.0, "RESISTANCE", 4.0, "MEDIUM", 3, ("day",))], [])
    assert _call(d) is False                  # require_confidence=HIGH


def test_duplicate_symbol_is_dropped_not_double_parked():
    mon, store = _FakeMonitor(has=True), _FakeStore()
    d, cache = _diverter(monitor=mon, store=store)
    cache.put("X", [_high_res()], [])
    assert _call(d) is True                    # handled (dropped)
    assert mon.registered == []                # NOT parked again
    assert ("SIG1", "REJECTED_RETEST_DUP", "symbol already parked in WAIT_FOR_RETEST") in store.status
