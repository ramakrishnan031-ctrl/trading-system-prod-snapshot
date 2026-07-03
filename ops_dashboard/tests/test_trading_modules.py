"""M2-M5 — signals / orders / positions / holdings readers + API behavior."""
from __future__ import annotations

from backend.readers import db_reader


# ── M2 signals ──
def test_list_signals_all(gui_config, today):
    rows = db_reader.list_signals(gui_config, today)
    assert len(rows) == 24            # 10 dup + 3 risk + 1 expired + 2 capital + 8 queued
    assert {"signal_id", "received_at", "scanner", "strategy", "symbol",
            "status", "rejection_reason", "family"} <= set(rows[0])


def test_list_signals_family_filters(gui_config, today):
    assert len(db_reader.list_signals(gui_config, today, family="expired")) == 1
    assert len(db_reader.list_signals(gui_config, today, family="duplicated")) == 10
    assert len(db_reader.list_signals(gui_config, today, family="rejected")) == 5   # 3 risk + 2 capital
    assert len(db_reader.list_signals(gui_config, today, family="accepted")) == 8
    assert len(db_reader.list_signals(gui_config, today, strategy="vwap_bounce_long")) == 2


def test_signals_api_denominator(client, today):
    d = client.get("/api/signals").get_json()
    assert d["date"] == today
    dn = d["denominator"]
    assert dn["received"] == 100 and dn["accepted"] == 85 and dn["rejected"] == 15
    assert dn["stored"] == 24 and dn["duplicated_stored"] == 10
    assert d["count"] == 24


def test_signals_api_family_param(client):
    d = client.get("/api/signals?family=expired").get_json()
    assert d["count"] == 1
    assert d["rows"][0]["status"] == "REJECTED_EXPIRED"


# ── M3 orders ──
def test_list_orders_and_filters(gui_config, today):
    rows = db_reader.list_orders(gui_config, today)
    assert len(rows) == 92            # 70 entry + 8 SL + 12 TGT + superseded-old + failed
    sl = db_reader.list_orders(gui_config, today, leg="SL")
    assert len(sl) == 10              # 8 COMPLETE + 1 CANCELLED(old) + 1 FAILED
    failed = db_reader.list_orders(gui_config, today, status="FAILED")
    assert len(failed) == 1 and failed[0]["rejection_reason"].startswith("Invalid tags")
    gap = db_reader.list_orders(gui_config, today, strategy="gap_fade_long")
    assert len(gap) == 7


def test_orders_superseded_chain(gui_config, today):
    rows = db_reader.list_orders(gui_config, today, leg="SL", status="CANCELLED")
    assert len(rows) == 1
    assert rows[0]["order_id"] == "ord_sl_old"
    assert rows[0]["superseded_by"] == "ord_sl_0"


def test_orders_latency_computed(gui_config, today):
    rows = db_reader.list_orders(gui_config, today, leg="ENTRY", status="COMPLETE")
    assert rows and rows[0]["place_to_fill_ms"] == 60000   # 10:39 → 10:40


def test_orders_api(client, today):
    d = client.get("/api/orders?leg=SL").get_json()
    assert d["count"] == 10
    d = client.get("/api/orders?leg=BOGUS").get_json()     # invalid leg → ignored
    assert d["count"] == 92


# ── M4 positions ──
def test_open_states_contract():
    """The open-set MUST be the G0 §2.2 list exactly — pinned."""
    assert db_reader.OPEN_STATES == ("OPEN", "PARTIAL", "PENDING_FILL", "EXITING")


def test_positions_list(gui_config):
    rows = db_reader.open_positions_list(gui_config)
    assert len(rows) == 4
    by = {r["trade_id"]: r for r in rows}
    assert by["trd_o1"]["inning_no"] == 2          # innings-joined
    assert by["trd_o2"]["inning_no"] is None       # no innings row → renders '—'
    assert by["trd_o1"]["sl_initial"] == 990.0
    assert by["trd_o1"]["margin_reserved"] == 5000.0


def test_positions_api(client):
    d = client.get("/api/positions").get_json()
    assert d["count"] == 4
    assert d["max_open_positions"] == 5
    assert d["mode"] == "PAPER"                    # data attribute, not a branch
    assert d["open_states"] == ["OPEN", "PARTIAL", "PENDING_FILL", "EXITING"]
    assert d["unrealized_note"] == "G4"


# ── M5 holdings ──
def test_holdings_empty(gui_config):
    assert db_reader.holdings_list(gui_config) == []


def test_holdings_api_banner(client):
    d = client.get("/api/holdings").get_json()
    assert d["count"] == 0 and d["rows"] == []
    assert d["banner"].startswith("Broker is authority")
