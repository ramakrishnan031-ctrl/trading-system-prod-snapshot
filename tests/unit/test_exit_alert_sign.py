# tests/unit/test_exit_alert_sign.py — real-time exit alert sign/consistency (30-Jun)
"""
The real-time TGT/SL exit alert must show the SIGNED net P&L (the same value stored
in trades.net_pnl and rendered by the EOD daily summary) — never a hardcoded "+".
Root cause (30-Jun CGCL manual-modify close): the TGT_HIT branch hardcoded pnl_sign="+",
so a real -₹6.07 loss was alerted as "🎯 TARGET HIT +₹6.07". The label stays
exit_reason-derived (consistent with the stored field + summary, which both label by
the filled leg); only the sign is fixed here.
"""
from orders.order_placer import OrderPlacer

F = OrderPlacer._format_exit_alert


def test_tgt_hit_loss_shows_minus():
    """The CGCL case: TGT-leg fill at a loss → '-₹6.07' (was '+₹6.07')."""
    title, body = F(exit_reason="TGT_HIT", symbol="CGCL", exit_price=224.80,
                    direction="LONG", net_pnl=-6.07, mode="LIVE")
    assert "TARGET HIT — CGCL" in title          # label still leg-derived (matches stored/summary)
    assert "Net P&L: -₹6.07" in body             # ...but the SIGN now matches the loss
    assert "Exit: ₹224.80" in body


def test_tgt_hit_profit_shows_plus():
    title, body = F(exit_reason="TGT_HIT", symbol="RAMCOSYS", exit_price=752.35,
                    direction="LONG", net_pnl=21.79, mode="LIVE")
    assert "TARGET HIT" in title
    assert "Net P&L: +₹21.79" in body


def test_sl_hit_loss_shows_minus():
    title, body = F(exit_reason="SL_HIT", symbol="BOROSCI", exit_price=161.99,
                    direction="LONG", net_pnl=-3.89, mode="LIVE")
    assert "STOP LOSS HIT" in title
    assert "Net P&L: -₹3.89" in body


def test_sl_hit_profit_shows_plus():
    """Trailing-SL / breakeven exit at a profit → '+₹X' (the symmetric case)."""
    title, body = F(exit_reason="SL_HIT", symbol="XYZ", exit_price=100.0,
                    direction="LONG", net_pnl=5.0, mode="LIVE")
    assert "STOP LOSS HIT" in title
    assert "Net P&L: +₹5.00" in body


def test_sign_always_follows_net_pnl():
    """Invariant across BOTH branches: the sign tracks the signed net P&L, never the leg."""
    for reason in ("TGT_HIT", "SL_HIT"):
        _, loss = F(exit_reason=reason, symbol="S", exit_price=1.0, direction="LONG",
                    net_pnl=-1.0, mode="LIVE")
        _, gain = F(exit_reason=reason, symbol="S", exit_price=1.0, direction="LONG",
                    net_pnl=1.0, mode="LIVE")
        assert "Net P&L: -₹1.00" in loss
        assert "Net P&L: +₹1.00" in gain


def test_zero_pnl_shows_plus():
    _, body = F(exit_reason="TGT_HIT", symbol="S", exit_price=1.0, direction="LONG",
                net_pnl=0.0, mode="LIVE")
    assert "Net P&L: +₹0.00" in body


def test_mode_and_direction_rendered():
    title, body = F(exit_reason="TGT_HIT", symbol="KEC", exit_price=525.65,
                    direction="SHORT", net_pnl=7.85, mode="PAPER")
    assert "[PAPER]" in title
    assert "Direction: SHORT" in body
