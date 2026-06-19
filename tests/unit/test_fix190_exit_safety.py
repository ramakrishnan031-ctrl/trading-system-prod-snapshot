"""
tests/unit/test_fix190_exit_safety.py

FIX-190 (Bug A): reverse-aware close helper — prevents the 19-Jun THELEELA
oversell where a second flatten of an already-closed long opened a naked short.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.position_helpers import broker_net_qty, determine_close_direction


class _Adapter:
    def __init__(self, positions=None, raises=False):
        self._positions = positions or []
        self._raises = raises

    def get_positions(self):
        if self._raises:
            raise RuntimeError("broker down")
        return self._positions


def _pos(symbol, qty):
    return SimpleNamespace(symbol=symbol, qty=qty)


def test_long_position_closes_with_sell():
    a = _Adapter([_pos("THELEELA", 1)])
    assert determine_close_direction(a, "THELEELA", "SELL", 1) == ("SELL", 1)


def test_short_position_closes_with_buy():
    # An oversold/short position must be COVERED with a BUY, not sold again.
    a = _Adapter([_pos("THELEELA", -1)])
    assert determine_close_direction(a, "THELEELA", "SELL", 1) == ("BUY", 1)


def test_flat_position_skips_no_order():
    # The core oversell fix: already flat -> (None, 0) -> caller fires nothing.
    a = _Adapter([_pos("THELEELA", 0)])
    assert determine_close_direction(a, "THELEELA", "SELL", 1) == (None, 0)


def test_symbol_absent_is_flat():
    a = _Adapter([_pos("AEROENTER", 3)])
    assert determine_close_direction(a, "THELEELA", "SELL", 1) == (None, 0)


def test_broker_error_falls_back_to_intended_exit():
    # Cannot confirm -> err toward flattening with the intended exit.
    a = _Adapter(raises=True)
    assert determine_close_direction(a, "THELEELA", "SELL", 1) == ("SELL", 1)
    assert broker_net_qty(a, "THELEELA") is None


def test_net_qty_sums_multiple_legs():
    a = _Adapter([_pos("X", 2), _pos("X", -1), _pos("Y", 5)])
    assert broker_net_qty(a, "X") == 1


def test_none_adapter_returns_none():
    assert broker_net_qty(None, "X") is None
    assert determine_close_direction(None, "X", "SELL", 3) == ("SELL", 3)


# ── Bug G entry throttle: see tests/unit/test_entry_throttle.py (EntryThrottle) ──


# ── FIX-190 (Bug B): runtime observability counters ──────────────────────────

def test_runtime_metrics_counters_and_snapshot():
    import threading
    from signals.signal_processor import SignalProcessor
    sp = SignalProcessor.__new__(SignalProcessor)  # bypass heavy __init__
    sp._rt_metrics_lock = threading.Lock()
    sp._rt_metrics = {
        "signals_processed": 0, "entries_placed": 0,
        "entries_throttled": 0, "entries_rejected": 0,
    }
    # FIX-190 (a): screener funnel totals from the SPW9 stats dict
    sp._stats_lock = threading.Lock()
    sp._stats = {
        "screener_passed": 4,
        "screener_rejected": {"REJECTED_SCORE_57": 210, "REJECTED_SCORE_52": 73},
        "screener_skipped": {"SKIPPED_QUOTE_UNAVAILABLE": 4},
    }
    sp._bump_metric("entries_placed")
    sp._bump_metric("entries_placed")
    sp._bump_metric("entries_throttled")
    m = sp.get_runtime_metrics()
    assert m["entries_placed"] == 2
    assert m["entries_throttled"] == 1
    assert m["entries_rejected"] == 0
    # screener funnel surfaced (the dominant rejection layer)
    assert m["signals_screened_passed"] == 4
    assert m["signals_screened_rejected"] == 283   # 210 + 73
    assert m["signals_screened_skipped"] == 4
    # snapshot is a copy — mutating it must not affect the live counters
    m["entries_placed"] = 99
    assert sp.get_runtime_metrics()["entries_placed"] == 2
