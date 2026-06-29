"""29-Jun alert enhancements — unit tests.

CHANGE 1: Direction (LONG/SHORT) line is the FIRST body line on
          INTRADAY SIGNAL (signal_processor) and ORDER PLACED (order_placer).
CHANGE 2: EOD DAILY SUMMARY redesign (eod_squareoff) — Long/Short split,
          Best/Worst with direction+reason, per-strategy 🟢/🔴 + per-trade
          detail sub-lines.

The EOD assertions use a fixture that reproduces REAL 25-Jun-2026 closed
trades (4 LONG / 1 SHORT), so they double as a regression lock on the actual
numbers (Long −₹9.08, Short +₹8.75).
"""
from __future__ import annotations

import logging

from orders.eod_squareoff import EodSquareoff
from orders.order_placer import OrderPlacer
from signals.signal_processor import SignalProcessor


def _closed_25jun() -> list[dict]:
    """5 closed trades from 25-Jun (4 LONG + 1 SHORT)."""
    return [
        {"symbol": "RAMCOIND", "direction": "LONG", "strategy": "open_low_breakout_long",
         "status": "CLOSED", "qty_filled": 1, "entry_actual_price": 337.75,
         "entry_target_price": 337.76, "exit_price": 334.0, "exit_reason": "SL_HIT",
         "risk_amount": 3.3776, "margin_reserved": 67.5524,
         "order_protocol": "LIMIT_TRIPLE", "net_pnl": -4.1, "trade_id": "t1"},
        {"symbol": "LINCOLN", "direction": "LONG", "strategy": "first_pullback_long",
         "status": "CLOSED_MANUAL", "qty_filled": 1, "entry_actual_price": 630.8,
         "entry_target_price": 630.8, "exit_price": 632.5, "exit_reason": "MANUAL",
         "risk_amount": 9.4623, "margin_reserved": 126.1637,
         "order_protocol": "LIMIT_TRIPLE", "net_pnl": 1.7, "trade_id": "t2"},
        {"symbol": "INDIANHUME", "direction": "LONG", "strategy": "vwap_bounce_long",
         "status": "CLOSED", "qty_filled": 1, "entry_actual_price": 339.4,
         "entry_target_price": 339.4, "exit_price": 336.85, "exit_reason": "SL_HIT",
         "risk_amount": 2.7165, "margin_reserved": 67.912,
         "order_protocol": "LIMIT_TRIPLE", "net_pnl": -2.9, "trade_id": "t3"},
        {"symbol": "DYCL", "direction": "LONG", "strategy": "vwap_bounce_long",
         "status": "CLOSED", "qty_filled": 1, "entry_actual_price": 377.6,
         "entry_target_price": 377.6, "exit_price": 374.2, "exit_reason": "SL_HIT",
         "risk_amount": 3.021, "margin_reserved": 75.5244,
         "order_protocol": "LIMIT_TRIPLE", "net_pnl": -3.78, "trade_id": "t4"},
        {"symbol": "BANDHANBNK", "direction": "SHORT", "strategy": "first_pullback_short",
         "status": "CLOSED", "qty_filled": 2, "entry_actual_price": 203.48,
         "entry_target_price": 203.48, "exit_price": 198.9, "exit_reason": "TGT_HIT",
         "risk_amount": 6.1045, "margin_reserved": 81.3933,
         "order_protocol": "LIMIT_TRIPLE", "net_pnl": 8.75, "trade_id": "t5"},
    ]


# ── CHANGE 2: EOD DAILY SUMMARY ───────────────────────────────────────────────

def test_eod_long_short_split_totals_match_overall_wl() -> None:
    body = EodSquareoff._format_summary_body(_closed_25jun(), [])
    assert "Win rate: 40.0% (2W 3L)" in body                 # overall: 2W 3L
    assert "🟢 Long Trades : 4 (1W 3L) | P&L: -₹9.08" in body   # 1W + 1W = 2W
    assert "🔴 Short Trades: 1 (1W 0L) | P&L: +₹8.75" in body   # 3L + 0L = 3L
    print("  OK EOD long/short split totals match overall W/L (4 LONG/1 SHORT)")


def test_eod_best_worst_carry_direction_and_reason() -> None:
    body = EodSquareoff._format_summary_body(_closed_25jun(), [])
    assert "Best:  BANDHANBNK +₹8.75 (SHORT | TGT_HIT)" in body
    assert "Worst: RAMCOIND -₹4.10 (LONG | SL_HIT)" in body
    print("  OK EOD best/worst show DIRECTION | exit_reason")


def test_eod_strategy_emoji_and_per_trade_detail() -> None:
    body = EodSquareoff._format_summary_body(_closed_25jun(), [])
    assert "🟢 open_low_breakout_long  1T 0%WR  -₹4.10" in body
    assert "🔴 first_pullback_short  1T 100%WR  +₹8.75" in body
    # detail sub-line: STOCK - DIR - ₹entry - ₹exit (TAG) - ±₹pnl
    assert "• RAMCOIND - LONG - ₹337.75 - ₹334.00 (SL) - -₹4.10" in body
    assert "• BANDHANBNK - SHORT - ₹203.48 - ₹198.90 (TGT) - +₹8.75" in body
    assert "• LINCOLN - LONG - ₹630.80 - ₹632.50 (MAN) - +₹1.70" in body
    # 🟢 long strategies listed before 🔴 short strategies
    assert body.index("🟢 open_low_breakout_long") < body.index("🔴 first_pullback_short")
    print("  OK EOD per-strategy emoji + per-trade detail sub-lines")


def test_eod_preserves_existing_fields() -> None:
    body = EodSquareoff._format_summary_body(_closed_25jun(), [])
    assert "P&L: -₹0.33 | Win rate: 40.0% (2W 3L)" in body
    assert "Avg R: -0.38 | Capital used: ₹418.55" in body
    assert "Smart TGT: FIXED=5 TRAIL=0 DEFEND=0" in body
    print("  OK EOD preserves P&L / win-rate / Avg R / capital / Smart TGT")


def test_eod_human_untracked_note() -> None:
    body = EodSquareoff._format_summary_body(_closed_25jun(), ["ITC", "SBIN"])
    assert "Human/untracked orders today: ITC, SBIN (not managed by system)" in body
    print("  OK EOD human/untracked note")


def test_eod_exit_tag_mapping() -> None:
    assert EodSquareoff._exit_tag("SL_HIT") == "SL"
    assert EodSquareoff._exit_tag("TGT_HIT") == "TGT"
    assert EodSquareoff._exit_tag("EOD") == "EOD"
    assert EodSquareoff._exit_tag("MANUAL") == "MAN"
    assert EodSquareoff._exit_tag("TIMEOUT") == "TO"
    assert EodSquareoff._exit_tag("CIRCUIT_BREAKER") == "CB"
    assert EodSquareoff._exit_tag(None) == "—"
    print("  OK EOD exit-reason → short tag mapping")


# ── CHANGE 1: Direction line on trade-wise alerts ─────────────────────────────

class _CapNotifier:
    def __init__(self) -> None:
        self.kw: dict = {}

    def send(self, **kw) -> None:
        self.kw = kw


def _emit_signal(direction: str) -> str:
    cap = _CapNotifier()

    class _Stub:
        _mode = "LIVE"
        _notifier = cap
        _log = logging.getLogger("test")

    SignalProcessor._emit_signal_alert(
        _Stub(), symbol="RAMCOIND", strategy_name="open_low_breakout_long",
        score=72, entry_price=337.75, sl_price=334.0, tgt_price=343.38,
        qty=1, direction=direction,
    )
    return cap.kw["body"]


def test_signal_alert_direction_line_first_long() -> None:
    body = _emit_signal("LONG")
    assert body.startswith("Direction: LONG\n")
    assert "\nStrategy: open_low_breakout_long" in body  # Direction sits ABOVE Strategy
    print("  OK INTRADAY SIGNAL: Direction line first (LONG)")


def test_signal_alert_direction_normalises_buy_sell() -> None:
    assert _emit_signal("SHORT").startswith("Direction: SHORT\n")
    assert _emit_signal("SELL").startswith("Direction: SHORT\n")   # SELL → SHORT
    assert _emit_signal("BUY").startswith("Direction: LONG\n")     # BUY  → LONG
    print("  OK INTRADAY SIGNAL: BUY/SELL normalised to LONG/SHORT")


def test_order_placed_body_direction_line_first() -> None:
    body = OrderPlacer._format_order_placed_body(
        direction="SHORT", entry_price=203.48, qty=2, now_hm="10:05",
        sl_price=201.45, tgt_price=206.53, smart_on=True,
    )
    assert body.startswith("Direction: SHORT\n")
    assert "\nFill: ₹203.48 | Qty: 2 | 10:05 IST" in body  # Direction sits ABOVE Fill
    assert "Smart TGT monitoring: ACTIVE (FIXED mode)" in body
    # normalisation + smart-off branch
    assert OrderPlacer._format_order_placed_body(
        direction="BUY", entry_price=1, qty=1, now_hm="09:30",
        sl_price=1, tgt_price=1, smart_on=False,
    ).startswith("Direction: LONG\n")
    print("  OK ORDER PLACED: Direction line first + normalisation")
