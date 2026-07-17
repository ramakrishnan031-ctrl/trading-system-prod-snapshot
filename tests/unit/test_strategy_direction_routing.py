"""Consumer routing to the canonical strategy direction — the eod_squareoff EOD summary.

The per-strategy 🟢/🔴 grouping is routed to StrategyConfig.direction (passed in as a map)
instead of a majority vote over realized sides. Behaviour-neutral on REAL data (trades.direction
is always the strategy's declared direction), and more robust when trade rows are ambiguous.
"""
from __future__ import annotations

from orders.eod_squareoff import EodSquareoff


_CLOSED = [
    {"status": "CLOSED", "net_pnl": 10.0, "strategy": "gap_fade_short", "symbol": "X",
     "exit_reason": "TGT", "direction": "LONG",  # ambiguous/wrong per-trade side
     "order_protocol": "CO_PLUS_TGT", "trade_id": "t1", "risk_amount": 500.0},
]


def test_none_map_falls_back_to_vote_behaviour_neutral():
    # direction_map=None -> legacy majority vote over realized sides (LONG here) -> 🟢
    body = EodSquareoff._format_summary_body(_CLOSED, [], direction_map=None)
    assert "🟢 gap_fade_short" in body


def test_canonical_map_overrides_the_vote():
    # canonical StrategyConfig.direction (SHORT) drives the grouping -> 🔴, regardless of the
    # per-trade realized side. This is what makes a future dual-direction strategy label right.
    body = EodSquareoff._format_summary_body(_CLOSED, [], direction_map={"gap_fade_short": "SHORT"})
    assert "🔴 gap_fade_short" in body


def test_unknown_strategy_in_map_uses_vote():
    # a strategy absent from the map falls back to the vote (adopted/unknown case) — no crash
    body = EodSquareoff._format_summary_body(_CLOSED, [], direction_map={"other": "SHORT"})
    assert "🟢 gap_fade_short" in body
