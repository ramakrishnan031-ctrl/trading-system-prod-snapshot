"""
tests/integration/test_fix190_incident_replay.py

Replays the core of the 19-Jun-2026 live incident (THELEELA: a target priced
above the upper circuit band → TGT rejected → emergency exit + HARD_KILL
flatten-all → oversold naked short + orphan SL/TGT) and asserts it can no longer
happen:

  - Bug D: a TGT above the upper circuit is CLAMPED into the band and placed
           (no rejection) — the trigger is removed.
  - Bug C: if a TGT is still rejected (no circuit data to clamp), place_exits
           returns a PARTIAL result with the SL standing — it does NOT raise, so
           the caller never escalates to emergency-exit / HARD_KILL.
  - Bug A: a reverse-aware flatten of an already-flat position fires NOTHING
           (no second SELL → no naked short).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.position_helpers import determine_close_direction
from core.exceptions import OrderRejectedError
from orders.order_protocol_limit import LimitTripleProtocol


def _log():
    return logging.getLogger("test_fix190_replay")


class _Adapter:
    """Mock broker. ENTRY/SL always succeed; the TGT (exit-side LIMIT with no
    trigger) is rejected when reject_tgt=True. get_quote returns the configured
    circuit band."""

    def __init__(self, upper=None, lower=None, reject_tgt=False):
        self.placed = []
        self._upper = upper
        self._lower = lower
        self._reject_tgt = reject_tgt
        self._n = 0

    def get_quote(self, symbols):
        sym = symbols[0]
        return {sym: SimpleNamespace(
            last_price=484.6, upper_circuit=self._upper, lower_circuit=self._lower,
        )}

    @staticmethod
    def _is_tgt(kw):
        return kw.get("order_type") == "LIMIT" and "trigger_price" not in kw

    def place_order(self, **kw):
        self._n += 1
        if self._is_tgt(kw) and self._reject_tgt:
            raise OrderRejectedError(
                "Your order price is higher than the current upper circuit "
                "limit of 503.35"
            )
        self.placed.append(kw)
        return SimpleNamespace(broker_order_id=f"b{self._n}", internal_order_id=f"i{self._n}")


def test_replay_bugD_tgt_above_circuit_is_clamped_and_placed():
    """THELEELA: TGT 505 > upper circuit 503.35 -> clamped into band -> placed."""
    adapter = _Adapter(upper=503.35, lower=440.0, reject_tgt=False)
    proto = LimitTripleProtocol(adapter=adapter, logger=_log())
    result = proto.place_exits(
        symbol="THELEELA", entry_side="BUY", qty=1,
        sl_price=471.9, tgt_price=505.0,
        intent="INTRADAY", trade_id="trd_theleela",
    )
    assert result.tgt_placed is True
    tgt = [p for p in adapter.placed if _Adapter._is_tgt(p)][-1]
    assert tgt["price"] <= 503.35, "TGT must be clamped below the upper circuit"


def test_replay_bugC_tgt_reject_stays_sl_protected_no_cascade():
    """No circuit data to clamp -> TGT rejected -> PARTIAL (SL stands), no raise
    -> caller never HARD_KILLs the book."""
    adapter = _Adapter(upper=None, lower=None, reject_tgt=True)
    proto = LimitTripleProtocol(adapter=adapter, logger=_log())
    result = proto.place_exits(
        symbol="THELEELA", entry_side="BUY", qty=1,
        sl_price=471.9, tgt_price=505.0,
        intent="INTRADAY", trade_id="trd_theleela",
    )
    assert result.tgt_placed is False
    assert result.sl_broker_order_id          # SL placed -> protected
    assert any(p["order_type"] == "SL" for p in adapter.placed)
    assert not any(_Adapter._is_tgt(p) for p in adapter.placed)  # TGT not placed


def test_replay_bugA_already_flat_fires_no_second_exit():
    """An already-flat position must not be sold again (the THELEELA oversell)."""
    flat = SimpleNamespace(get_positions=lambda: [SimpleNamespace(symbol="THELEELA", qty=0)])
    assert determine_close_direction(flat, "THELEELA", "SELL", 1) == (None, 0)
    # an oversold short is COVERED with a BUY, never sold again
    short = SimpleNamespace(get_positions=lambda: [SimpleNamespace(symbol="THELEELA", qty=-1)])
    assert determine_close_direction(short, "THELEELA", "SELL", 1) == ("BUY", 1)
