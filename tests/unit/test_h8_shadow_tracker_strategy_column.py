"""
tests/unit/test_h8_shadow_tracker_strategy_column.py

Wave-3 / H-8 — ShadowTracker's FIX-013 inning-1 TGT recalc keyed on a column that
does not exist (`strategy_name`); the real trades column is `strategy`
(core/schema.sql:124). Same dead-column class as H-1 and kill_switch:977.

Because the access was guarded by `"strategy_name" in trade.keys()`, it failed
SILENTLY (always "" -> the RISK_REWARD branch never ran), so inning-1 `tgt_price`
was persisted as the THEORETICAL target from the DB, not the fill-recalc'd one —
inning analytics were computed against the wrong target when entry slippage
occurred. This is analytics-only (ShadowTracker is the shadow/observer path).

These tests drive the REAL ShadowTracker._on_position_closed against a REAL
StateStore (core/schema.sql applied) via a PositionClosed event, and read the
persisted inning back — reusing the existing shadow_tracker harness (do not
rebuild it).

Real collaborators:  ShadowTracker._on_position_closed, the RISK_REWARD recalc
                     (orders.price_math.calc_tgt_price), the REAL StateStore /
                     schema (trades + innings), EventBus.
Simulated:           the strategy config (_MockStrategy), time/market-window
                     fakes, notifier fake (from the sibling harness).

RED/GREEN: test_h8_risk_reward_tgt_recalc_uses_real_strategy_column asserts the
inning-1 tgt is the RR-recalc'd 2630 (not the theoretical 2600). Against the old
`strategy_name` column the branch is skipped -> tgt stays 2600 -> FAIL (RED);
after the fix -> 2630 (GREEN).

Run: python -m pytest tests/unit/test_h8_shadow_tracker_strategy_column.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.events import EventBus, PositionClosed
from core.state_store import StateStore

# Reuse the existing schema-backed shadow_tracker harness (do NOT rebuild it).
from tests.unit.test_shadow_tracker import (
    _make_tracker,
    _seed_signal,
    _seed_trade,
    _MockStrategy,
    _FakeNotifier,
)

# entry_actual=2510, sl=2450, rr=2.0 -> risk=60 -> RR tgt = 2510 + 2*60 = 2630.
# DB tgt_initial (theoretical) seeded at 2600 -> the two differ, so the assertion
# distinguishes "recalc ran" (2630) from "branch skipped" (2600).
_ENTRY = 2510.0
_SL = 2450.0
_THEORETICAL_TGT = 2600.0
_RR = 2.0
_RR_RECALC_TGT = _ENTRY + _RR * abs(_ENTRY - _SL)   # 2630.0


def _drive(store: StateStore, tgt_method: str) -> list:
    bus = EventBus()
    strat = _MockStrategy(direction="LONG", tgt_method=tgt_method, tgt_risk_reward=_RR)
    _make_tracker(
        store, bus,
        notifier=_FakeNotifier(),
        strategies={"strategy1": strat},
        max_innings=1,            # no cascade -> isolates inning-1 creation
    )
    _seed_signal(store, "sig1")
    _seed_trade(
        store, "t1", "sig1",
        entry_actual=_ENTRY, sl=_SL, tgt=_THEORETICAL_TGT,
        exit_price=_THEORETICAL_TGT, exit_reason="TGT_HIT", strategy="strategy1",
    )
    bus.publish(PositionClosed(
        source_module="test", trade_id="t1", symbol="RELIANCE",
        signal_id="sig1", exit_price=_THEORETICAL_TGT, realized_pnl=1000.0,
    ))
    return store.get_innings_for_trade("t1")


# ── core RED/GREEN — RISK_REWARD recalc keys on the real `strategy` column ────

def test_h8_risk_reward_tgt_recalc_uses_real_strategy_column(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "h8_rr.db")
    innings = _drive(store, tgt_method="RISK_REWARD")

    assert len(innings) == 1
    # Fix present: the branch resolves strategy="strategy1" and recalculates the
    # target from the ACTUAL entry -> 2630. Old wrong column -> 2600 (RED).
    assert innings[0]["tgt_price"] == pytest.approx(_RR_RECALC_TGT)
    assert innings[0]["tgt_price"] != pytest.approx(_THEORETICAL_TGT)
    print("  OK H-8: RISK_REWARD inning-1 tgt recalc'd to 2630 via real "
          "`strategy` column  [RED=2600 before fix]")


# ── control — a non-RR strategy still uses the theoretical tgt (guard) ────────

def test_h8_fixed_pct_strategy_keeps_theoretical_tgt(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "h8_fixed.db")
    innings = _drive(store, tgt_method="FIXED_PCT")

    assert len(innings) == 1
    # The recalc branch is gated on tgt_method == RISK_REWARD; a FIXED_PCT
    # strategy must leave inning-1 tgt at the DB theoretical value (the fix does
    # not over-recalc). Passes both before and after.
    assert innings[0]["tgt_price"] == pytest.approx(_THEORETICAL_TGT)
    print("  OK H-8 control: FIXED_PCT keeps theoretical tgt (2600) — branch "
          "correctly gated")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_h8_risk_reward_tgt_recalc_uses_real_strategy_column(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_h8_fixed_pct_strategy_keeps_theoretical_tgt(Path(d))
    print("\nAll H-8 tests passed.")
