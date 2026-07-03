"""db_reader queries against the seeded v41/v42 fixtures (runs on both)."""
from __future__ import annotations

from backend.readers import db_reader


def test_schema_version(gui_config, schema_version):
    assert db_reader.get_schema_version(gui_config) == schema_version


def test_session_and_kill(gui_config):
    s = db_reader.get_session_info(gui_config)
    assert s["mode"] == "PAPER" and s["trade_type"] == "INTRADAY" and s["account_id"] == "LFL836"
    ks = db_reader.get_kill_switch(gui_config)
    assert ks["state"] == "INACTIVE"


def test_webhook_funnel(gui_config, today):
    f = db_reader.webhook_funnel(gui_config, today)
    assert f["received"] == 100
    assert f["validated"] == 85
    assert f["rejected_total"] == 15


def test_signal_reject_families(gui_config, today):
    assert db_reader.signals_duplicate_count(gui_config, today) == 10
    assert db_reader.signals_risk_rejected(gui_config, today) == 3
    assert db_reader.signals_capital_rejected(gui_config, today) == 2


def test_orders_funnel(gui_config, today):
    e = db_reader.orders_entry_counts(gui_config, today)
    assert e["created"] == 70 and e["placed"] == 70 and e["filled"] == 60
    assert db_reader.orders_exit_leg_filled(gui_config, today, "SL")["count"] == 8
    assert db_reader.orders_exit_leg_filled(gui_config, today, "TGT")["count"] == 12


def test_trades_closed(gui_config, today):
    tc = db_reader.trades_closed_counts(gui_config, today)
    assert tc["closed"] == 4
    assert tc["other_exit"] == 2   # EOD + MANUAL (not SL_HIT/TGT_HIT)


def test_capacity_counters(gui_config, today):
    assert db_reader.daily_trades_used(gui_config, today) == 8
    assert db_reader.open_positions_count(gui_config) == 4
    assert db_reader.delivery_open_count(gui_config) == 0
    assert db_reader.delivery_daily_used(gui_config, today) == 0
    assert db_reader.consecutive_loss_streak(gui_config) == 3
    assert db_reader.opening_capital(gui_config, today) == 100000.0
    assert db_reader.realized_loss_today(gui_config, today) == 450.0
    assert db_reader.capital_usage(gui_config)["margin_used"] == 42000.0


def test_strategy_stats(gui_config, today):
    stats = db_reader.strategy_trade_stats(gui_config, today)
    assert stats["gap_fade_long"]["trades"] == 4
    assert stats["gap_fade_long"]["wins"] == 1
    assert stats["gap_fade_long"]["losses"] == 1
    assert stats["gap_fade_long"]["net_pnl"] == 100.0
    assert stats["gap_fade_long"]["open_count"] == 2
    assert stats["vwap_bounce_long"]["net_pnl"] == -125.0


def test_events(gui_config):
    ev = db_reader.recent_events(gui_config, limit=10)
    assert len(ev) == 3
    assert ev[0]["event_type"] == "CONFIG_DIFF"   # newest first
    assert {"timestamp", "event_type", "scenario", "details"} <= set(ev[0])
