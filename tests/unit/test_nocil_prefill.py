"""
tests/unit/test_nocil_prefill.py

NOCIL fix — E.5: the PRE-FILL circuit-proximity rejection (secondary_screener,
framing-b, BOTH legs). Lives with the screener change (separate from the
post-fill placeability-gate tests in test_nocil_clamp_fix.py).

Reject an entry that sits at/beyond the exit-clamp ceiling (no profitable TGT or
valid SL could be placed inside the day's circuit band); fail-open otherwise;
fast-disable via the constructor flag.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

from core.time_authority import now_ist
from screening.secondary_screener import SecondaryScreener


def _log() -> logging.Logger:
    lg = logging.getLogger("test_nocil_prefill")
    lg.addHandler(logging.NullHandler())
    return lg


def _screener(enabled=True):
    return SecondaryScreener(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), _log(),
        circuit_proximity_reject_enabled=enabled,
    )


def test_e5_reason_long_rejects_upper_ceiling():
    s = _screener()
    md = {"upper_circuit": 190.82, "lower_circuit": 127.22}
    # NOCIL entry 189.9 >= 190.82*0.98 = 187.0 -> reject (no profitable TGT fits)
    assert s._circuit_proximity_reason(189.9, "LONG", md) is not None


def test_e5_reason_long_rejects_lower_floor():
    s = _screener()
    md = {"upper_circuit": 200.0, "lower_circuit": 99.0}
    # entry 100 <= 99*1.02 = 100.98 -> reject (no valid SL fits)
    assert s._circuit_proximity_reason(100.0, "LONG", md) is not None


def test_e5_reason_short_rejects_lower_floor():
    s = _screener()
    md = {"upper_circuit": 200.0, "lower_circuit": 99.0}
    assert s._circuit_proximity_reason(100.0, "SHORT", md) is not None


def test_e5_reason_short_rejects_upper_ceiling():
    s = _screener()
    md = {"upper_circuit": 102.0, "lower_circuit": 50.0}
    # SHORT entry 100 >= 102*0.98 = 99.96 -> reject (no valid SL fits)
    assert s._circuit_proximity_reason(100.0, "SHORT", md) is not None


def test_e5_reason_admits_feasible_entry():
    s = _screener()
    md = {"upper_circuit": 120.0, "lower_circuit": 80.0}
    # entry 100 well inside [81.6, 117.6] -> admit
    assert s._circuit_proximity_reason(100.0, "LONG", md) is None


def test_e5_reason_flag_off_never_rejects():
    s = _screener(enabled=False)
    md = {"upper_circuit": 190.82, "lower_circuit": 127.22}
    assert s._circuit_proximity_reason(189.9, "LONG", md) is None


def test_e5_reason_fail_open_missing_band():
    s = _screener()
    assert s._circuit_proximity_reason(189.9, "LONG", {}) is None
    assert s._circuit_proximity_reason(189.9, "LONG", {"upper_circuit": None}) is None


def test_e5_screen_returns_rejected_status_for_nocil_geometry():
    s = _screener()
    md = {"upper_circuit": 190.82, "lower_circuit": 127.22, "ltp": 189.9}
    res = s.screen(
        signal_id="sig_nocil", symbol="NOCIL", scanner_name="t",
        trigger_price=189.9, triggered_at=now_ist(), direction="LONG",
        intent="INTRADAY", strategy=MagicMock(), market_data=md,
    )
    assert res.passed is False
    assert res.status == "REJECTED_CIRCUIT_PROXIMITY"
