"""db_reader queries against the seeded v41/v42 fixtures (runs on both)."""
from __future__ import annotations

import sqlite3

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
    assert db_reader.capital_usage(gui_config, today)["margin_used"] == 20000.0


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


# ─────────────────────────────────────────────────────────────────────────────
# 25-Jul-2026 — opening_capital is the day's FIRST INIT row, never the SUM.
#
# RED BEFORE THE FIX: db_reader.opening_capital ran
#     SELECT SUM(balance_after) FROM fm_ledger WHERE date=? AND entry_type='INIT'
# INIT is NOT unique per day. FundManager.initialize() writes one INIT row per
# PROCESS START -- its H-4 double-init guard is an in-memory per-process flag
# (capital/fund_manager.py:408-448) -- so a mid-day restart adds a second INIT
# row for the same date and the SUM doubled.
#
# MEASURED on production: 10 of 30 INIT dates carry more than one row; every one
# of the 58 INIT rows has bucket='both' and the FULL balance, so a multi-INIT day
# is always a restart duplicate and never a bucket split. On 2026-07-21 (the
# forced 11:57 restart) this returned 19,716.03 against a true opening of
# 9,857.30, so every percentage resolved against it read half its real value.
#
# The rule now matches state_store.get_day_opening_capital() -- ORDER BY ts ASC
# LIMIT 1 -- so the system has one definition of "the day's opening capital".
# ─────────────────────────────────────────────────────────────────────────────

_INIT_SQL = ("INSERT INTO fm_ledger(ts,entry_type,amount,bucket,balance_before,balance_after) "
             "VALUES(?,?,?,?,?,?)")


def _add_init(gui_config, ts: str, balance: float) -> None:
    """Append a production-shaped INIT row (bucket='both', full balance)."""
    conn = sqlite3.connect(gui_config["paths"]["main_db"])
    conn.execute(_INIT_SQL, (ts, "INIT", balance, "both", 0.0, balance))
    conn.commit()
    conn.close()


def test_opening_capital_ignores_a_restart_reseed(gui_config, today):
    """A mid-day restart adds a second INIT row. The opening capital is still the
    08:15 seed -- the SUM would have returned 200500.0 here."""
    _add_init(gui_config, f"{today}T11:57:41.742251+05:30", 100500.0)
    assert db_reader.opening_capital(gui_config, today) == 100000.0


def test_opening_capital_reproduces_the_measured_21jul_production_shape(gui_config):
    """The real defect, with the real numbers, on a date nothing else queries."""
    d = "2026-07-21"
    _add_init(gui_config, f"{d}T08:15:26.589351+05:30", 9857.30)
    _add_init(gui_config, f"{d}T11:57:41.742251+05:30", 9858.73)
    assert db_reader.opening_capital(gui_config, d) == 9857.30      # NOT 19716.03


def test_opening_capital_orders_by_timestamp_not_insertion_order(gui_config):
    """Pins the ORDER BY. The later-timestamped row is inserted FIRST, so it wins
    on rowid; only an ORDER BY ts can pick the 08:15 seed."""
    d = "2026-07-20"
    _add_init(gui_config, f"{d}T13:59:36.260932+05:30", 5555.55)   # inserted first
    _add_init(gui_config, f"{d}T08:15:21.936187+05:30", 9826.50)   # earlier ts
    assert db_reader.opening_capital(gui_config, d) == 9826.50


def test_opening_capital_unchanged_on_a_normal_single_init_day(gui_config, today):
    """Regression guard: the ordinary no-restart day must not move."""
    assert db_reader.opening_capital(gui_config, today) == 100000.0
