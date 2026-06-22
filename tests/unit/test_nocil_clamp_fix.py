"""
tests/unit/test_nocil_clamp_fix.py

NOCIL circuit-clamp fix (22-Jun-2026) — behavioural matrix E.2–E.7.

The primitive itself (E.1) is covered in test_price_math.py
(test_gate_*). This file covers the CALLERS and the system-level guarantees:

  E.2  place_exits TGT unplaceable -> NO TGT sent, SL stands (NOCIL replay)
  E.3  place_exits SL  unplaceable -> SLUnplaceableError, nothing placed
       (+ benign SL clamp still places both legs — the no-over-fire guard)
  E.4  place_tgt_only retry: unplaceable -> placed=False; relaxes -> placed
  E.5  pre-fill circuit-proximity reject (framing-b, both legs; flag OFF)
  E.6  P2: tgt_retry_manager cycle completes (no TypeError) in market hours
  E.7  NO-BYPASS: clamp_exit_into_band is the sole chokepoint; CO-TGT +
       reconciler (G5b) assert-never-clamp.
"""
from __future__ import annotations

import logging
import pathlib
import re
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from broker.zerodha_adapter import PlacedOrder, Quote
from core.exceptions import SLUnplaceableError
from core.time_authority import now_ist
from orders.order_protocol_limit import LimitTripleProtocol
from orders.tgt_retry_manager import TGTRetryManager


def _log() -> logging.Logger:
    lg = logging.getLogger("test_nocil")
    lg.addHandler(logging.NullHandler())
    return lg


class _GateAdapter:
    """Minimal adapter: get_quote feeds the circuit band; place_order records."""

    def __init__(self, upper_circuit=None, lower_circuit=None, ltp=100.0):
        self.placed: list[dict] = []
        self.cancelled: list[str] = []
        self._uc = upper_circuit
        self._lc = lower_circuit
        self._ltp = ltp
        self._n = 0

    def get_quote(self, symbols):
        sym = symbols[0]
        return {
            sym: Quote(
                symbol=sym, last_price=self._ltp, bid=self._ltp, ask=self._ltp,
                volume=1000, ts=now_ist(),
                upper_circuit=self._uc, lower_circuit=self._lc,
            )
        }

    def place_order(self, symbol, side, qty, price, order_type, intent,
                    tag=None, trigger_price=0.0, variety="regular"):
        self._n += 1
        po = PlacedOrder(
            internal_order_id=f"ord_{self._n}", broker_order_id=f"K{self._n}",
            symbol=symbol, side=side, qty=qty, price=price, order_type=order_type,
            product="MIS", status="SUBMITTED", ts=now_ist(),
        )
        self.placed.append({
            "symbol": symbol, "side": side, "qty": qty, "price": price,
            "order_type": order_type, "trigger_price": trigger_price,
            "broker_order_id": po.broker_order_id,
        })
        return po

    def cancel_order(self, broker_order_id):
        from broker.zerodha_adapter import CancelResult
        self.cancelled.append(broker_order_id)
        return CancelResult(broker_order_id=broker_order_id, success=True, reason="")


# ── E.2: TGT unplaceable -> SL-only (the NOCIL fix) ──────────────────────────

def test_e2_place_exits_tgt_unplaceable_nocil_replay():
    """NOCIL: LONG fill 189.78, recalc TGT 197.12, upper 190.82 -> clamp 187.00
    < fill. The TGT must NOT be placed (no instantly-marketable SELL@187); the
    SL stands and the result is SL-only (tgt_placed=False)."""
    adapter = _GateAdapter(upper_circuit=190.82, lower_circuit=127.22, ltp=189.78)
    proto = LimitTripleProtocol(adapter=adapter, logger=_log())

    legs = proto.place_exits(
        symbol="NOCIL", entry_side="BUY", qty=2,
        sl_price=186.11, tgt_price=197.12,
        intent="INTRADAY", trade_id="trd_nocil", entry_fill=189.78,
    )

    assert legs.tgt_placed is False
    assert legs.tgt_broker_order_id is None
    assert legs.sl_broker_order_id  # SL stands
    # Exactly ONE order hit the broker: the SL. No TGT/LIMIT ever sent.
    assert len(adapter.placed) == 1
    assert adapter.placed[0]["order_type"] == "SL"
    assert adapter.placed[0]["side"] == "SELL"
    assert all(p["order_type"] == "SL" for p in adapter.placed)


def test_e2_place_exits_tgt_unplaceable_short():
    """SHORT mirror: buy-to-cover TGT clamped UP off the lower circuit to >= fill."""
    adapter = _GateAdapter(upper_circuit=130.0, lower_circuit=99.0, ltp=100.0)
    proto = LimitTripleProtocol(adapter=adapter, logger=_log())

    legs = proto.place_exits(
        symbol="X", entry_side="SELL", qty=2,
        sl_price=103.0, tgt_price=90.0,
        intent="INTRADAY", trade_id="trd_short", entry_fill=100.0,
    )

    assert legs.tgt_placed is False
    assert legs.sl_broker_order_id
    assert len(adapter.placed) == 1
    assert adapter.placed[0]["side"] == "BUY"   # SL (exit of a SHORT)


# ── E.3: SL unplaceable -> raise; benign SL clamp -> still places both ───────

def test_e3_place_exits_sl_unplaceable_raises_nothing_placed():
    """LONG near its LOWER circuit: SL clamped UP to >= fill = instant stop-out.
    Must raise SLUnplaceableError BEFORE any order is sent (SL-first)."""
    adapter = _GateAdapter(upper_circuit=130.0, lower_circuit=99.0, ltp=100.0)
    proto = LimitTripleProtocol(adapter=adapter, logger=_log())

    with pytest.raises(SLUnplaceableError):
        proto.place_exits(
            symbol="X", entry_side="BUY", qty=2,
            sl_price=95.0, tgt_price=140.0,
            intent="INTRADAY", trade_id="trd_slun", entry_fill=100.0,
        )
    assert len(adapter.placed) == 0  # nothing placed — not even the SL


def test_e3_benign_sl_clamp_still_places_both_legs():
    """A LONG SL clamped UP off the lower circuit but still BELOW fill is a valid
    tighter stop -> MUST NOT be rejected; both SL + TGT place normally."""
    adapter = _GateAdapter(upper_circuit=140.0, lower_circuit=90.0, ltp=100.0)
    proto = LimitTripleProtocol(adapter=adapter, logger=_log())

    legs = proto.place_exits(
        symbol="X", entry_side="BUY", qty=2,
        sl_price=85.0, tgt_price=130.0,
        intent="INTRADAY", trade_id="trd_benign", entry_fill=100.0,
    )

    assert legs.tgt_placed is True
    assert len(adapter.placed) == 2
    # SL trigger clamped UP to lower*(1+margin)=91.80, still below the 100 fill.
    assert adapter.placed[0]["order_type"] == "SL"
    assert adapter.placed[0]["trigger_price"] == pytest.approx(91.80)
    assert adapter.placed[0]["trigger_price"] < 100.0


# ── E.4: place_tgt_only retry ────────────────────────────────────────────────

def test_e4_place_tgt_only_unplaceable_not_placed():
    adapter = _GateAdapter(upper_circuit=190.82, lower_circuit=127.22, ltp=189.78)
    proto = LimitTripleProtocol(adapter=adapter, logger=_log())

    res = proto.place_tgt_only(
        symbol="NOCIL", entry_side="BUY", qty=2, tgt_price=197.12,
        intent="INTRADAY", trade_id="trd_r", entry_fill=189.78,
    )
    assert res.placed is False
    assert len(adapter.placed) == 0  # nothing sent to the broker


def test_e4_place_tgt_only_placeable_after_band_relaxes():
    adapter = _GateAdapter(upper_circuit=210.0, lower_circuit=127.22, ltp=190.0)
    proto = LimitTripleProtocol(adapter=adapter, logger=_log())

    res = proto.place_tgt_only(
        symbol="NOCIL", entry_side="BUY", qty=2, tgt_price=197.12,
        intent="INTRADAY", trade_id="trd_r2", entry_fill=189.78,
    )
    assert res.placed is True
    assert res.tgt_price == pytest.approx(197.12)   # inside the relaxed band, unclamped
    assert len(adapter.placed) == 1
    assert adapter.placed[0]["order_type"] == "LIMIT"


# ── E.5: pre-fill circuit-proximity reject -> tests/unit/test_nocil_prefill.py
#         (lives with the screener change so each commit is independently green).


# ── E.6: P2 tgt_retry_manager — the 3-arg market-hours call (no TypeError) ────

def test_e6_tgt_retry_cycle_no_typeerror(monkeypatch):
    import orders.tgt_retry_manager as trm

    store = MagicMock()
    store.get_tgt_retry_candidates.return_value = []
    mgr = TGTRetryManager(
        state_store=store, order_placer=MagicMock(), market_hours_guard=True,
    )
    # 11:00 IST -> within DEFAULT_MARKET_OPEN/CLOSE (09:15-15:30): the guard must
    # PASS (proving the 3-arg call works) and we reach candidate fetch.
    monkeypatch.setattr(trm, "now_ist", lambda: datetime(2026, 6, 22, 11, 0, 0))
    out = mgr.run_once()  # pre-fix this raised TypeError every cycle
    assert out == []
    store.get_tgt_retry_candidates.assert_called_once()


# ── E.7: NO-BYPASS — the gate is the sole chokepoint ─────────────────────────

def test_e7_clamp_gate_is_sole_chokepoint():
    """clamp_exit_into_band must be called from ONLY order_protocol_limit.py.
    Regression-proofs the de-dup: no other orders/ module may place a clamped
    (potentially wrong-side) exit without the placeability gate."""
    orders_dir = pathlib.Path(__file__).resolve().parents[2] / "orders"
    # Match CALL sites (exclude the `def clamp_exit_into_band(` definition home).
    call_re = re.compile(r"(?<!def )\bclamp_exit_into_band\s*\(")
    callers = sorted(
        py.name for py in orders_dir.glob("*.py")
        if call_re.search(py.read_text(encoding="utf-8"))
    )
    assert callers == ["order_protocol_limit.py"], (
        f"clamp gate must be called ONLY from order_protocol_limit; found {callers}"
    )


# ── E.8: partial-fill — the gate verdict tracks the SETTLED average ──────────

def test_e8_gate_verdict_uses_settled_average_not_an_earlier_fill():
    """T3: exits are placed ONCE at terminal against the cumulative
    avg_fill_price. This locks that the wrong-side verdict is a function of the
    settled average passed as entry_fill — so a future change to the fill path
    that fed an earlier/partial fill instead would flip the verdict and fail."""
    # Same circuit band + same recalc'd TGT; only the settled fill differs.
    # Settled avg 189.78 (NOCIL) -> clamp 187.00 is wrong-side -> SL-only.
    adapter = _GateAdapter(upper_circuit=190.82, lower_circuit=127.22, ltp=189.78)
    proto = LimitTripleProtocol(adapter=adapter, logger=_log())
    legs_settled = proto.place_exits(
        symbol="NOCIL", entry_side="BUY", qty=2, sl_price=186.11, tgt_price=197.12,
        intent="INTRADAY", trade_id="trd_s1", entry_fill=189.78,
    )
    assert legs_settled.tgt_placed is False

    # A lower settled avg (185.00) leaves the clamped 187.00 ABOVE the fill ->
    # placeable. The ONLY difference is entry_fill -> verdict tracks it.
    adapter2 = _GateAdapter(upper_circuit=190.82, lower_circuit=127.22, ltp=185.0)
    proto2 = LimitTripleProtocol(adapter=adapter2, logger=_log())
    legs_lower = proto2.place_exits(
        symbol="NOCIL", entry_side="BUY", qty=2, sl_price=183.0, tgt_price=197.12,
        intent="INTRADAY", trade_id="trd_s2", entry_fill=185.0,
    )
    assert legs_lower.tgt_placed is True
    assert len(adapter2.placed) == 2  # SL + TGT, placed once each


# ── E.9: regression sentinels — normal profitable trades stay admitted ───────
# Real historical TGT_HIT winners (fill/sl/tgt from the 22-Jun DB; LLOYDSENGG sl
# approximate, prior day). Per the calibration, ONLY NOCIL ever had a binding
# clamp — these winners had NON-binding bands, so a wide band is the faithful
# representation. Asserts the gate admits normal winners (rejects only NOCIL's
# wrong-side geometry). Benign-but-binding clamps are covered by E.1/E.3.

@pytest.mark.parametrize("fill,sl,tgt", [
    (83.44, 81.0, 87.62),     # LLOYDSENGG (sl approx)
    (916.05, 902.3, 943.55),  # KSCL
    (389.85, 382.05, 405.45), # EMSLIMITED
    (212.31, 210.36, 216.73), # NIACL
])
def test_e9_normal_long_targets_stay_placeable(fill, sl, tgt):
    """The historical profitable TGT_HITs (well inside their bands, target above
    fill) must remain placeable — the gate rejects ONLY the wrong-side case."""
    # Wide band well clear of the entry: nothing clamps, TGT stays above fill.
    adapter = _GateAdapter(upper_circuit=fill * 1.20, lower_circuit=fill * 0.80,
                           ltp=fill)
    proto = LimitTripleProtocol(adapter=adapter, logger=_log())
    legs = proto.place_exits(
        symbol="X", entry_side="BUY", qty=1, sl_price=sl, tgt_price=tgt,
        intent="INTRADAY", trade_id="trd_sentinel", entry_fill=fill,
    )
    assert legs.tgt_placed is True
    assert len(adapter.placed) == 2


def test_e7_co_and_reconciler_assert_never_clamp():
    """The two documented exceptions (Option 2): CO-TGT is backstopped by the
    broker-managed CO SL; G5b recovery SL is LTP-guarded. Neither may invoke the
    clamp — that is what makes them safe to leave un-gated."""
    orders_dir = pathlib.Path(__file__).resolve().parents[2] / "orders"
    for name in ("order_protocol_co.py", "order_reconciler.py"):
        src = (orders_dir / name).read_text(encoding="utf-8")
        assert "clamp_exit_into_band" not in src, (
            f"{name} must NOT clamp (documented un-gated exception)"
        )
