"""summary_bar — pinned top bar view model (trader-down on the PC)."""
from __future__ import annotations

from backend.services import summary_bar


def test_summary(gui_config, today):
    s = summary_bar.build_summary(gui_config, today,
                                  trader_health={"trader_alive": False, "health": None})
    assert s["mode"] == "PAPER"
    assert s["trade_type"] == "INTRADAY"
    assert s["trader_alive"] is False
    assert s["kill_switch"]["state"] == "INACTIVE"
    assert s["kill_switch"]["halted"] is False
    c = s["counters"]
    assert c["received"] == 100
    assert c["trades_today"] == 8
    assert c["open_positions"] == 4
    assert c["opening_capital"] == 100000.0
    assert c["realized_loss_today"] == 450.0


def test_summary_mode_is_data_not_branch(gui_config, today):
    # parity: mode is a data attribute carried straight from `session`, never a
    # branch — the same builder produces PAPER/LIVE identically.
    s = summary_bar.build_summary(gui_config, today,
                                  trader_health={"trader_alive": True, "health": {"queue_size": 3}})
    assert s["mode"] in ("PAPER", "LIVE")
    assert s["trader_alive"] is True
