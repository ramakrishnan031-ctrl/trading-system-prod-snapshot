"""
tests/unit/test_sr_v2_continue.py — SNR-V2 continue_from_retest + dormancy.

confirm → reserve + MARKET place with structure SL/TGT; reject-after-reserve →
release. Plus the dormancy no-ops (flag off → seam helpers inert).
"""
from __future__ import annotations

import logging
import threading
from datetime import time as dtime
from types import SimpleNamespace

from screening.retest_monitor import ParkedCandidate
from signals.signal_processor import SignalProcessor
from datetime import datetime

_LOG = logging.getLogger("test_sr_v2_continue")
ADDED = datetime(2026, 6, 26, 14, 0)


def _strategy():
    return SimpleNamespace(
        intent="INTRADAY", enabled=True, name="strat", lot_size=1,
        entry_start_time=dtime(9, 15), entry_end_time=dtime(15, 15),
        tgt_risk_reward=1.5, max_concurrent_positions=5,
    )


class _Sizing:
    success = True
    qty = 10
    breakdown = {}
    constraint = ""
    reason = ""


class _Sizer:
    def calculate(self, *a, **k):
        return _Sizing()


class _Risk:
    def approve(self, *a, **k):
        return SimpleNamespace(approved=True, failed_check="", reason="")


class _FM:
    def __init__(self):
        self.portfolio_lock = threading.Lock()
        self.reserved = []
        self.released = []

    def reserve(self, *a, **k):
        self.reserved.append((a, k))
        return SimpleNamespace(success=True, reservation_id="R1", reason_if_failed=None)

    def release(self, rid, reason=""):
        self.released.append((rid, reason))
        return True


class _Placer:
    def __init__(self):
        self.calls = []

    def place(self, **kw):
        self.calls.append(kw)


class _Store:
    def __init__(self):
        self.status = []

    def update_signal_status(self, sid, status, reason=None):
        self.status.append((sid, status, reason))

    def fetch_one(self, sql, params=()):
        return {"n": 0}     # per-strategy open-position count


class _MW:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def is_entry_allowed(self, now):
        return self.allowed

    def is_entry_allowed_for_strategy(self, now, strat):
        return True


def _sp(*, throttle_ok=True, mw_allowed=True):
    sp = SignalProcessor.__new__(SignalProcessor)
    sp._store = _Store()
    sp._ks = None
    sp._mw = _MW(mw_allowed)
    sp._strategies = {"strat": _strategy()}
    sp._trade_type = "INTRADAY"
    sp._force_intraday_only = True
    sp._strategy_governor = None
    sp._retest_sl_buffer_pct = 0.2
    sp._sizer = _Sizer()
    sp._perf_weights = {}
    sp._in_flight_lock = threading.Lock()
    sp._in_flight_count = 0
    sp._fm = _FM()
    sp._risk = _Risk()
    sp._placer = _Placer()
    sp._stop_event = threading.Event()
    sp._entry_throttle = SimpleNamespace(
        admit=lambda s: SimpleNamespace(allowed=throttle_ok, reason="throttled"))
    sp._stats = {"processed": 0, "placed": 0, "rejected": {}, "processed_no_placer": 0,
                 "total_ms": 0.0, "pipeline_total": 0}
    sp._stats_lock = threading.Lock()
    sp._quote_fn = lambda s: {"last_price": 226.5}
    sp._log = _LOG
    # override bound helpers that need wider state
    sp._derive_target = lambda e, s, st: e + (e - s) * 1.5
    sp._emit_signal_alert = lambda **k: None
    sp._sr_observe = lambda **k: None
    sp._bump_metric = lambda *a, **k: None
    return sp


def _parked():
    return ParkedCandidate(
        signal_id="SIG1", symbol="ACME", direction="LONG",
        zone_band_low=225.0, zone_band_high=226.0, entry_price=224.0, sl_price=219.0,
        strategy="strat", intent="INTRADAY", tier="A", trigger_price=224.0,
        sizing_inputs={}, added_at=ADDED, state="WAIT_CONFIRM")


def _parked_short():
    # SHORT: support zone [225,226]; after rejection price breaks DOWN below it.
    return ParkedCandidate(
        signal_id="SIG1", symbol="ACME", direction="SHORT",
        zone_band_low=225.0, zone_band_high=226.0, entry_price=227.0, sl_price=232.0,
        strategy="strat", intent="INTRADAY", tier="A", trigger_price=227.0,
        sizing_inputs={}, added_at=ADDED, state="WAIT_CONFIRM")


def test_confirm_reserves_and_places_market_with_structure_sl():
    sp = _sp()
    sp.continue_from_retest(_parked())
    assert len(sp._fm.reserved) == 1                      # capital reserved HERE
    assert len(sp._placer.calls) == 1
    call = sp._placer.calls[0]
    assert call["entry_order_type"] == "MARKET"
    assert abs(call["entry_price"] - 226.5) < 1e-6        # MARKET entry sized at LTP
    assert abs(call["sl_price"] - 225.0 * 0.998) < 1e-6   # structure SL = band_low − 0.2%
    assert call["tgt_price"] > call["entry_price"]        # R:R TGT above entry
    assert ("SIG1", "PROCESSED", None) in sp._store.status
    assert sp._fm.released == []                          # placer owns the reservation


def test_short_confirm_reserves_and_places_market_with_structure_sl_above():
    sp = _sp()
    sp._quote_fn = lambda s: {"last_price": 224.0}            # LTP below the broken support
    sp._derive_target = lambda e, s, st: e - (s - e) * 1.5    # SHORT R:R (mirror)
    sp.continue_from_retest(_parked_short())
    assert len(sp._fm.reserved) == 1                          # capital reserved HERE
    assert len(sp._placer.calls) == 1
    call = sp._placer.calls[0]
    assert call["side"] == "SELL"                             # SHORT entry
    assert call["entry_order_type"] == "MARKET"
    assert abs(call["entry_price"] - 224.0) < 1e-6            # MARKET entry sized at LTP
    assert abs(call["sl_price"] - 226.0 * 1.002) < 1e-6       # structure SL = band_high + 0.2% (ABOVE)
    assert call["sl_price"] > call["entry_price"]             # SHORT SL above entry
    assert call["tgt_price"] < call["entry_price"]            # SHORT TGT below entry
    assert ("SIG1", "PROCESSED", None) in sp._store.status
    assert sp._fm.released == []                              # placer owns the reservation


def test_short_bad_structure_when_entry_above_sl_releases_nothing():
    sp = _sp()
    sp._quote_fn = lambda s: {"last_price": 227.0}            # LTP ABOVE the structure SL (226.452)
    sp.continue_from_retest(_parked_short())
    assert sp._fm.reserved == [] and sp._fm.released == []    # rejected before sizing/reserve
    assert sp._placer.calls == []
    assert any(s[1] == "REJECTED_RETEST_BAD_STRUCTURE" for s in sp._store.status)


def test_reject_after_reserve_releases_capital():
    sp = _sp(throttle_ok=False)   # throttle rejects AFTER reserve
    sp.continue_from_retest(_parked())
    assert len(sp._fm.reserved) == 1
    assert len(sp._fm.released) == 1                      # reservation released on reject
    assert sp._placer.calls == []                         # never placed
    assert any(s[1] == "REJECTED_ENTRY_THROTTLED" for s in sp._store.status)


def test_reject_before_reserve_holds_no_capital():
    sp = _sp(mw_allowed=False)    # market-window closed → reject before sizing/reserve
    sp.continue_from_retest(_parked())
    assert sp._fm.reserved == [] and sp._fm.released == []
    assert sp._placer.calls == []
    assert any(s[1] == "REJECTED_OUTSIDE_ENTRY_WINDOW" for s in sp._store.status)


# ── dormancy ──────────────────────────────────────────────────────────────────

def test_dormant_seam_helpers_are_noops():
    sp = SignalProcessor.__new__(SignalProcessor)
    sp._zone_warmer = None
    sp._retest_diverter = None
    sp._log = _LOG
    # _warm_zones with no warmer is a no-op (no raise), both tuple + dict forms
    sp._warm_zones(("SIG", "SC", "ACME", 100.0, None))
    sp._warm_zones({"symbol": "ACME"})
    # late-bind works
    sp.set_retest_diverter("X")
    assert sp._retest_diverter == "X"
