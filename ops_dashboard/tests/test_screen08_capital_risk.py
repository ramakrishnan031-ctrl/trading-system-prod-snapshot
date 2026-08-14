"""Screen-08 Capital & Risk — contract tests (14-Aug-2026).

This screen exists because FOUR quantities are routinely conflated, so most of
these tests assert that one is NOT the other:

  real cash         broker cash the engine believes it holds (fm_ledger)
  real allocation   real cash x bucket pct (MIS 70 / GTT 30, from config)
  segment capacity  real allocation x leverage (5x / 1x) — BUYING POWER, not cash
  real committed    trades.margin_reserved — cash actually committed

⛔ Written so they CAN fail: the pinned fixture uses DIFFERENT leverages per
segment (5x vs 1x) and DIFFERENT committed amounts (1,200 vs 2,000), so a reader
that applied one leverage to both, or that summed the buckets before splitting
them, goes red. The MIS notional (6,000) and the GTT real capital (2,000) are
deliberately far apart so a notional/margin swap cannot pass.

THE PINNED SIMULATION (Rama's workbook, 14-Aug-2026) is reproduced exactly:
  opening 10,000 + pay-in 5,000 = 15,000 real cash
  MIS order value 6,000 @5x -> 1,200 real   GTT 2,000 @1x -> 2,000 real
  consumed 3,200, remaining real 11,800
  MIS segment 52,500, remaining 46,500 · GTT segment 4,500, remaining 2,500
"""
from __future__ import annotations

import sqlite3

import pytest

from backend.readers import db_reader


TODAY_PIN = "2026-08-14"

_SCHEMA = (
    """CREATE TABLE orders (order_id TEXT PRIMARY KEY, trade_id TEXT, leg TEXT,
        product TEXT, status TEXT, placed_at TEXT)""",
    """CREATE TABLE trades (trade_id TEXT PRIMARY KEY, symbol TEXT, strategy TEXT,
        margin_reserved REAL, created_at TEXT, status TEXT)""",
    """CREATE TABLE fm_ledger (ledger_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
        entry_type TEXT NOT NULL, amount REAL, bucket TEXT, balance_before REAL,
        balance_after REAL, pnl_delta REAL DEFAULT 0,
        date TEXT GENERATED ALWAYS AS (substr(ts,1,10)) STORED)""",
)


def _build(tmp_path, *, total_after_movement: float, seed_trades: bool = True):
    """Pinned fixture. `total_after_movement` is what the engine BELIEVES it has:
    10,000 models the day before any pay-in reaches it; 15,000 models a pay-in
    that has propagated. Both are engine truth — the screen renders whichever
    the ledger holds, never the workbook's intent.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "pinned.db"
    conn = sqlite3.connect(db)
    for stmt in _SCHEMA:
        conn.execute(stmt)
    conn.execute(
        "INSERT INTO fm_ledger(ts,entry_type,amount,bucket,balance_before,balance_after) "
        "VALUES (?,?,?,?,?,?)",
        (f"{TODAY_PIN}T08:15:26.682837+05:30", "INIT", 10000.0, "both", 0.0, 10000.0))
    if total_after_movement != 10000.0:
        conn.execute(
            "INSERT INTO fm_ledger(ts,entry_type,amount,bucket,balance_before,balance_after) "
            "VALUES (?,?,?,?,?,?)",
            (f"{TODAY_PIN}T09:15:00.044739+05:30", "SYNC", 0.0, "both",
             10000.0, total_after_movement))
    if seed_trades:
        # MIS: 6,000 notional at 5x -> 1,200 real capital committed.
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?)",
                     ("t_mis", "AAA", "first_pullback_long", 1200.0,
                      f"{TODAY_PIN}T10:00:00+05:30", "OPEN"))
        conn.execute("INSERT INTO orders VALUES (?,?,?,?,?,?)",
                     ("o_mis", "t_mis", "ENTRY", "MIS", "COMPLETE",
                      f"{TODAY_PIN}T10:00:00+05:30"))
        # GTT/CNC: 2,000 notional at 1x -> 2,000 real capital committed.
        conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?)",
                     ("t_gtt", "BBB", "vwap_rejection_short", 2000.0,
                      f"{TODAY_PIN}T10:01:00+05:30", "OPEN"))
        conn.execute("INSERT INTO orders VALUES (?,?,?,?,?,?)",
                     ("o_gtt", "t_gtt", "ENTRY", "CNC", "COMPLETE",
                      f"{TODAY_PIN}T10:01:00+05:30"))
    conn.commit()
    conn.close()
    return {"paths": {"main_db": str(db)}}


def _segment_view(cfg, *, intraday_pct=0.70, delivery_pct=0.30,
                  intraday_lev=5.0, delivery_lev=1.0):
    """The endpoint's arithmetic, applied to the readers' output. Mirrors
    api/risk_capital.get_capital_segments so the numbers under test are the ones
    the screen renders.
    """
    total = db_reader.current_total_capital(cfg, TODAY_PIN)
    seg = db_reader.capital_by_segment(cfg, TODAY_PIN)
    out = {}
    for key, pct, lev in (("intraday", intraday_pct, intraday_lev),
                          ("delivery", delivery_pct, delivery_lev)):
        committed = seg[key]["real_committed"]
        real_alloc = round(pct * total, 2)
        remaining_real = round(real_alloc - committed, 2)
        out[key] = {
            "real_allocation": real_alloc,
            "segment_capacity": round(real_alloc * lev, 2),
            "real_committed": committed,
            "order_value_notional": round(committed * lev, 2),
            "remaining_real": remaining_real,
            "remaining_segment_capacity": round(remaining_real * lev, 2),
        }
    out["_total"] = total
    out["_consumed"] = round(seg["intraday"]["real_committed"]
                             + seg["delivery"]["real_committed"], 2)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# THE PINNED SIMULATION — exact figures, never rounded away
# ─────────────────────────────────────────────────────────────────────────────
def test_pinned_after_payin_reproduces_the_workbook_exactly(tmp_path):
    v = _segment_view(_build(tmp_path, total_after_movement=15000.0))

    assert v["_total"] == 15000.0
    assert v["_consumed"] == 3200.00
    assert round(v["_total"] - v["_consumed"], 2) == 11800.00

    mis = v["intraday"]
    assert mis["real_allocation"] == 10500.00
    assert mis["segment_capacity"] == 52500.00
    assert mis["real_committed"] == 1200.00
    assert mis["order_value_notional"] == 6000.00
    assert mis["remaining_real"] == 9300.00
    assert mis["remaining_segment_capacity"] == 46500.00

    gtt = v["delivery"]
    assert gtt["real_allocation"] == 4500.00
    assert gtt["segment_capacity"] == 4500.00
    assert gtt["real_committed"] == 2000.00
    assert gtt["order_value_notional"] == 2000.00
    assert gtt["remaining_real"] == 2500.00
    assert gtt["remaining_segment_capacity"] == 2500.00

    # The workbook's own totals row.
    assert round(mis["segment_capacity"] + gtt["segment_capacity"], 2) == 57000.00
    assert round(mis["remaining_segment_capacity"]
                 + gtt["remaining_segment_capacity"], 2) == 49000.00


def test_pinned_before_payin_is_the_engine_truth_today(tmp_path):
    """⭐ The state the engine ACTUALLY holds when a pay-in has not propagated.
    The screen must render THIS, not the workbook's intended 15,000.
    """
    v = _segment_view(_build(tmp_path, total_after_movement=10000.0))
    assert v["_total"] == 10000.0
    assert v["_consumed"] == 3200.00
    assert round(v["_total"] - v["_consumed"], 2) == 6800.00
    assert v["intraday"]["real_allocation"] == 7000.00
    assert v["intraday"]["segment_capacity"] == 35000.00
    assert v["intraday"]["remaining_segment_capacity"] == 29000.00
    assert v["delivery"]["real_allocation"] == 3000.00
    assert v["delivery"]["segment_capacity"] == 3000.00
    assert v["delivery"]["remaining_segment_capacity"] == 1000.00


def test_payin_moves_only_the_base_never_the_committed_capital(tmp_path):
    """A capital movement re-bases the buckets; it must NOT touch what existing
    orders have already committed. (§3 of the authorisation: additive, never a
    re-derivation.)"""
    before = _segment_view(_build(tmp_path / "a", total_after_movement=10000.0))
    after = _segment_view(_build(tmp_path / "b", total_after_movement=15000.0))
    assert before["_consumed"] == after["_consumed"] == 3200.00
    assert before["intraday"]["real_committed"] == after["intraday"]["real_committed"]
    assert before["delivery"]["real_committed"] == after["delivery"]["real_committed"]
    # ...while the capacity DID move, by exactly the allocated share x leverage.
    assert round(after["intraday"]["segment_capacity"]
                 - before["intraday"]["segment_capacity"], 2) == 17500.00   # 5000*0.7*5
    assert round(after["delivery"]["segment_capacity"]
                 - before["delivery"]["segment_capacity"], 2) == 1500.00    # 5000*0.3*1


# ─────────────────────────────────────────────────────────────────────────────
# Quantities that must not be confused
# ─────────────────────────────────────────────────────────────────────────────
def test_notional_is_derived_from_margin_never_the_reverse(tmp_path):
    """The engine stores MARGIN (qty*price/leverage, "NOT notional"). At 5x the
    two differ by 5x, and the committed figure must stay the margin.
    """
    v = _segment_view(_build(tmp_path, total_after_movement=15000.0))
    mis = v["intraday"]
    assert mis["order_value_notional"] == round(mis["real_committed"] * 5.0, 2)
    assert mis["real_committed"] != mis["order_value_notional"]
    # At 1x they coincide — which is exactly why MIS is the discriminating case.
    gtt = v["delivery"]
    assert gtt["real_committed"] == gtt["order_value_notional"] == 2000.00


def test_segment_capacity_is_not_real_cash(tmp_path):
    v = _segment_view(_build(tmp_path, total_after_movement=15000.0))
    assert v["intraday"]["segment_capacity"] > v["intraday"]["real_allocation"]
    assert round(v["intraday"]["segment_capacity"]
                 / v["intraday"]["real_allocation"], 4) == 5.0
    # Delivery is unlevered: capacity IS the cash.
    assert v["delivery"]["segment_capacity"] == v["delivery"]["real_allocation"]


def test_buckets_are_split_by_entry_product_not_by_trade_row(tmp_path):
    """Product lives on orders(leg='ENTRY'), never on trades. A reader that
    ignored the join would put all 3,200 in one bucket.
    """
    seg = db_reader.capital_by_segment(_build(tmp_path, total_after_movement=15000.0),
                                       TODAY_PIN)
    assert seg["intraday"]["real_committed"] == 1200.00
    assert seg["delivery"]["real_committed"] == 2000.00


def test_segments_sum_to_capital_usage(tmp_path):
    """The split must not invent or lose capital against the existing reader."""
    cfg = _build(tmp_path, total_after_movement=15000.0)
    seg = db_reader.capital_by_segment(cfg, TODAY_PIN)
    usage = db_reader.capital_usage(cfg, TODAY_PIN)
    assert round(seg["intraday"]["real_used"] + seg["delivery"]["real_used"], 2) \
        == round(usage["margin_used"], 2)
    assert round(seg["intraday"]["real_reserved"] + seg["delivery"]["real_reserved"], 2) \
        == round(usage["margin_reserved"], 2)


def test_opening_and_current_total_are_different_questions(tmp_path):
    cfg = _build(tmp_path, total_after_movement=15000.0)
    assert db_reader.opening_capital(cfg, TODAY_PIN) == 10000.0      # first INIT
    assert db_reader.current_total_capital(cfg, TODAY_PIN) == 15000.0  # latest


def test_strategy_split_by_segment(tmp_path):
    rows = db_reader.strategy_capital_by_segment(
        _build(tmp_path, total_after_movement=15000.0), TODAY_PIN)
    by = {r["strategy"]: r for r in rows}
    assert by["vwap_rejection_short"]["gtt_real"] == 2000.00
    assert by["vwap_rejection_short"]["mis_real"] == 0.0
    assert by["first_pullback_long"]["mis_real"] == 1200.00
    assert by["first_pullback_long"]["gtt_real"] == 0.0


def test_no_trades_yields_zero_not_none(tmp_path):
    """Empty state: zero committed is a measurement; None would render as '—'."""
    cfg = _build(tmp_path, total_after_movement=10000.0, seed_trades=False)
    seg = db_reader.capital_by_segment(cfg, TODAY_PIN)
    assert seg["intraday"]["real_committed"] == 0.0
    assert seg["delivery"]["real_committed"] == 0.0
    assert db_reader.strategy_capital_by_segment(cfg, TODAY_PIN) == []


def test_current_total_capital_is_none_when_no_ledger(tmp_path):
    """⛔ None, never 0.0 — a zero would render as "the account is empty"."""
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(db)
    for stmt in _SCHEMA:
        conn.execute(stmt)
    conn.commit()
    conn.close()
    assert db_reader.current_total_capital({"paths": {"main_db": str(db)}},
                                           TODAY_PIN) is None


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint contract — pay-in/pay-out must be honestly unavailable
# ─────────────────────────────────────────────────────────────────────────────
def test_endpoint_shape_and_unobserved_movements(client):
    r = client.get("/api/capital/segments")
    assert r.status_code == 200
    j = r.get_json()
    for k in ("today", "real_cash", "config", "segments", "strategies"):
        assert k in j
    rc = j["real_cash"]
    # ⛔ The engine persists neither, and isolation rule I4 forbids a broker call,
    # so these must be explicitly unavailable — NOT 0.0, which would read as
    # "no money moved today".
    for f in ("payin_today", "payout_today"):
        assert rc[f]["available"] is False
        assert rc[f]["value"] is None
        assert "I4" in rc[f]["reason"] or "isolation" in rc[f]["reason"]
    assert set(j["segments"]) == {"intraday", "delivery"}


def test_endpoint_reads_allocation_and_leverage_from_config(client):
    """⛔ Nothing hard-coded: the 70/30 and 5x/1x must arrive from config."""
    j = client.get("/api/capital/segments").get_json()
    cfg = j["config"]
    for k in ("intraday_pct", "delivery_pct", "intraday_leverage", "delivery_leverage"):
        assert k in cfg
    if cfg["intraday_pct"] is not None and cfg["delivery_pct"] is not None:
        assert round(cfg["intraday_pct"] + cfg["delivery_pct"], 6) == 1.0


def test_endpoint_requires_login(app):
    """Screen-08 is read-only but still authenticated (no security relaxation)."""
    c = app.test_client()
    r = c.get("/api/capital/segments", follow_redirects=False)
    assert r.status_code in (302, 401)
