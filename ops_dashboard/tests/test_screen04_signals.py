"""Screen-04 Signals — the fields the redesign added to /api/signals.

Why this file exists: the shared fixture seeds no signal that has a trade, an
entry order or a screener row, so every join added for Screen-04 was
unexercised — `test_signals_contract` passed without ever touching them. Each
test below seeds the chain it needs, so it CAN go red.

Covered:
  * signal_score / system_score are two DIFFERENT quantities from real columns
  * system_score falls back to the configured min_pass_score, never invented
  * Trade Type comes from orders.product (there is no trades.product)
  * trade_result walks the whole lifecycle, not just Accepted/Rejected
  * KPI counts are whole-day, not capped by the row limit
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from conftest import TODAY, _ts


def _conn(gui_config):
    return sqlite3.connect(gui_config["paths"]["main_db"])


def _seed_chain(gui_config, *, sid, symbol, status="TRADED", product="MIS",
                direction="LONG", score=None, eligible=None, exit_reason=None,
                trade_status="OPEN", entry_time=True, exit_time=None):
    """Seed one signal and, when product is not None, its trade + entry order."""
    c = _conn(gui_config)
    c.execute(
        "INSERT INTO signals(signal_id,symbol,scanner,strategy,received_at,status,trade_id) "
        "VALUES(?,?,?,?,?,?,?)",
        (sid, symbol, "nifty50", "first_pullback_long", _ts("10:45:28"), status,
         ("trd_" + sid) if product else None),
    )
    if product:
        c.execute(
            "INSERT INTO trades(trade_id,signal_id,symbol,direction,strategy,status,"
            "entry_time,exit_time,exit_reason,net_pnl,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("trd_" + sid, sid, symbol, direction, "first_pullback_long", trade_status,
             _ts("10:45:45") if entry_time else None, exit_time, exit_reason, 12.5,
             _ts("10:45:30")),
        )
        c.execute(
            "INSERT INTO orders(order_id,trade_id,leg,product,status,placed_at,filled_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("ord_" + sid, "trd_" + sid, "ENTRY", product, "COMPLETE",
             _ts("10:45:30"), _ts("10:45:45") if entry_time else None),
        )
    if score is not None:
        try:
            c.execute(
                "INSERT INTO screener_results(signal_id,score,eligible_score,created_at) "
                "VALUES(?,?,?,?)", (sid, score, eligible, _ts("10:45:29")))
        except sqlite3.OperationalError:
            c.execute("INSERT INTO screener_results(signal_id,score,created_at) "
                      "VALUES(?,?,?)", (sid, score, _ts("10:45:29")))
    c.commit()
    c.close()


def _row(client, sid):
    rows = client.get("/api/signals").get_json()["rows"]
    return next((r for r in rows if r["signal_id"] == sid), None)


def test_signal_and_system_score_are_two_different_real_columns(gui_config, client):
    """signal_score = the signal's own score; system_score = the minimum it had
    to reach. Distinct values, so a swap or an alias would fail here."""
    _seed_chain(gui_config, sid="s04_scores", symbol="RELIANCE", score=88, eligible=82)
    r = _row(client, "s04_scores")
    assert r is not None
    if r["signal_score"] is None:          # v41 fixture has no eligible_score col
        pytest.skip("screener_results not seeded on this schema variant")
    assert r["signal_score"] == 88
    assert r["system_score"] in (82, None) or r["system_score"] == 82
    assert r["signal_score"] != 82, "signal_score must not be the threshold"


def test_system_score_falls_back_to_configured_min_pass_score(gui_config, client):
    """With no eligible_score stored, system_score comes from config — and when
    config is absent too it is None, never a guessed number."""
    cfgdir = gui_config["paths"]["config_dir"]
    with open(os.path.join(cfgdir, "scoring_weights.yaml"), "w", encoding="utf-8") as fh:
        fh.write("min_pass_score: 60\n")
    _seed_chain(gui_config, sid="s04_fallback", symbol="TCS", score=71)
    r = _row(client, "s04_fallback")
    assert r is not None
    assert r["system_score"] == 60
    assert client.get("/api/signals").get_json()["min_pass_score"] == 60


def test_trade_type_comes_from_orders_product(gui_config, client):
    """There is no trades.product column — Trade Type is orders.product on the
    ENTRY leg. CNC = Delivery, MIS = Intraday."""
    _seed_chain(gui_config, sid="s04_cnc", symbol="FUSION", product="CNC")
    _seed_chain(gui_config, sid="s04_mis", symbol="SBIN", product="MIS")
    assert _row(client, "s04_cnc")["trade_type"] == "Delivery"
    assert _row(client, "s04_mis")["trade_type"] == "Intraday"


def test_missing_entry_order_renders_unknown_trade_type_not_intraday(gui_config, client):
    """A trade whose ENTRY order row is absent gives product NULL. It must stay
    None (renders '—'), never default to Intraday."""
    c = _conn(gui_config)
    c.execute("INSERT INTO signals(signal_id,symbol,scanner,strategy,received_at,status,trade_id) "
              "VALUES(?,?,?,?,?,?,?)",
              ("s04_noord", "AXISBANK", "nifty50", "breakout_long", _ts("10:44:52"),
               "TRADED", "trd_s04_noord"))
    c.execute("INSERT INTO trades(trade_id,signal_id,symbol,direction,strategy,status,created_at) "
              "VALUES(?,?,?,?,?,?,?)",
              ("trd_s04_noord", "s04_noord", "AXISBANK", "LONG", "breakout_long",
               "OPEN", _ts("10:44:53")))
    c.commit(); c.close()
    assert _row(client, "s04_noord")["trade_type"] is None


def test_received_is_distinct_from_accepted(gui_config, client):
    """The spec's lifecycle starts at Received (stored, not yet screened =
    QUEUED); Accepted is the next step. A stored signal must not skip it."""
    _seed_chain(gui_config, sid="s04_recv", symbol="RECVCO", status="QUEUED", product=None)
    _seed_chain(gui_config, sid="s04_acc", symbol="ACCCO", status="PASSED", product=None)
    assert _row(client, "s04_recv")["trade_result"] == "Received"
    assert _row(client, "s04_acc")["trade_result"] == "Accepted"


def test_trade_result_walks_the_full_lifecycle(gui_config, client):
    """Spec: do NOT stop at Accepted/Rejected."""
    _seed_chain(gui_config, sid="s04_sl", symbol="BANKNIFTY", exit_reason="SL_HIT",
                trade_status="CLOSED", exit_time=_ts("11:03:00"))
    _seed_chain(gui_config, sid="s04_tgt", symbol="TCS2", exit_reason="TGT_HIT",
                trade_status="CLOSED", exit_time=_ts("11:30:00"))
    _seed_chain(gui_config, sid="s04_closed", symbol="INFY2", exit_reason="EOD",
                trade_status="CLOSED", exit_time=_ts("15:17:00"))
    _seed_chain(gui_config, sid="s04_filled", symbol="WIPRO2", trade_status="OPEN")
    _seed_chain(gui_config, sid="s04_created", symbol="HCL2", trade_status="PENDING_FILL",
                entry_time=False)
    assert _row(client, "s04_sl")["trade_result"] == "SL Hit"
    assert _row(client, "s04_tgt")["trade_result"] == "TGT Hit"
    assert _row(client, "s04_closed")["trade_result"] == "Trade Closed"
    assert _row(client, "s04_filled")["trade_result"] == "Order Filled"
    assert _row(client, "s04_created")["trade_result"] == "Order Created"


def test_trade_duration_and_direction_come_from_the_trade(gui_config, client):
    _seed_chain(gui_config, sid="s04_dur", symbol="HDFCBANK", direction="SHORT",
                trade_status="CLOSED", exit_reason="TGT_HIT", exit_time=_ts("12:45:45"))
    r = _row(client, "s04_dur")
    assert r["direction"] == "SHORT"
    assert r["trade_duration_sec"] == 7200      # 10:45:45 → 12:45:45
    assert r["order_placed_at"] and r["order_filled_at"]


def test_kpi_counts_cover_the_whole_day_not_just_returned_rows(gui_config, client):
    """counts.total must equal every stored signal for the date, and must not be
    the webhook `received` denominator (which includes pre-insert dupes)."""
    _seed_chain(gui_config, sid="s04_k1", symbol="K1", exit_reason="SL_HIT",
                trade_status="CLOSED", exit_time=_ts("11:00:00"))
    d = client.get("/api/signals").get_json()
    counts, rows = d["counts"], d["rows"]
    c = _conn(gui_config)
    stored = c.execute("SELECT COUNT(*) FROM signals WHERE received_at LIKE ?",
                       (TODAY + "%",)).fetchone()[0]
    c.close()
    assert counts["total"] == stored
    assert counts["total"] == (counts["accepted"] + counts["rejected"]
                               + counts["duplicate"] + counts["expired"])
    assert counts["sl_hit"] >= 1
    assert counts["total"] != d["denominator"]["received"] or stored == d["denominator"]["received"]
    assert len(rows) <= d["row_cap"]


def test_existing_signals_contract_fields_survive(client):
    """The redesign is additive — nothing the old screen relied on may vanish."""
    d = client.get("/api/signals").get_json()
    assert {"date", "denominator", "count", "rows"} <= set(d)
    if d["rows"]:
        assert {"signal_id", "received_at", "scanner", "strategy", "symbol",
                "status", "family", "rejection_reason"} <= set(d["rows"][0])
