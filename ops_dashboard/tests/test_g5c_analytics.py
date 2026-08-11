"""G5c — NEW analytics screens: period layer, ranking, P&L breakdowns, scanner
attribution + opportunity quality, trade-story (honest 'not captured'), trade
explorer, health-state mapping. Exact values on the multi-day fixture. Additive-
only: existing endpoint shapes frozen. Both v41+v42 via shared client/app.
"""
from __future__ import annotations

import copy
import os
from datetime import timedelta

from backend.readers import db_reader
from backend.services import analytics_period, freshness

from conftest import (TODAY, YDAY, TENDAYS,                    # noqa: E402
                      assert_signal_score_confined_to_screen04)


# ── Period layer (anti-hallucination anchor) ─────────────────────────────────
def test_resolve_period():
    now = freshness.parse_ist(TODAY + "T20:00:00+05:30")
    assert freshness.resolve_period("today", now=now) == (TODAY, TODAY)
    w = freshness.resolve_period("week", now=now)
    assert w[1] == TODAY and w[0] <= YDAY <= w[1]          # yesterday inside the week
    m = freshness.resolve_period("month", now=now)
    assert m[1] == TODAY and m[0] <= TENDAYS               # 10-days-ago inside the month
    assert TENDAYS < w[0]                                   # but OUTSIDE the week
    assert freshness.resolve_period("custom", "2026-06-01", "2026-06-10") == ("2026-06-01", "2026-06-10")


def test_period_closed_trades_counts(gui_config):
    now = freshness.parse_ist(TODAY + "T20:00:00+05:30")
    t = freshness.resolve_period("today", now=now)
    w = freshness.resolve_period("week", now=now)
    m = freshness.resolve_period("month", now=now)
    assert len(db_reader.closed_trades_range(gui_config, *t)) == 4     # c1-c4
    assert len(db_reader.closed_trades_range(gui_config, *w)) == 6     # + w1,w2
    assert len(db_reader.closed_trades_range(gui_config, *m)) == 7     # + m1


# ── Strategy Ranking ─────────────────────────────────────────────────────────
def test_ranking_modes_and_values(client):
    d = client.get("/api/strategy-ranking?period=today").get_json()
    assert d["rankings"]["net_pnl"][0] == "gap_fade_long"          # +100 > vwap -125
    by = d["by_name"]
    assert by["gap_fade_long"]["net"] == 100.0
    assert by["gap_fade_long"]["profit_factor"] == 2.0 and by["gap_fade_long"]["roi_pct"] == 1.0
    assert by["gap_fade_long"]["win_rate"] == 50.0
    assert set(d["modes"]) == {"net_pnl", "roi", "win_rate", "profit_factor", "trade_count"}


def test_ranking_period_widens(client):
    wk = client.get("/api/strategy-ranking?period=week").get_json()["by_name"]
    assert wk["gap_fade_long"]["net"] == 150.0 and wk["gap_fade_long"]["trades"] == 4


# ── P&L Analytics (per-period breakdown + expectancy) ───────────────────────
def test_pnl_analytics_week(client):
    d = client.get("/api/analytics/pnl?period=week").get_json()
    t = d["totals"]
    assert t["net"] == 25.0 and t["trades"] == 6 and t["wins"] == 2 and t["losses"] == 4
    # expectancy = (2/6)*140 − (4/6)*63.75 = 4.17
    assert t["expectancy"] == 4.17
    ps = {r["strategy"]: r for r in d["per_strategy"]}
    assert ps["gap_fade_long"]["net"] == 150.0
    assert "realized only" in d["curve_note"]


def test_pnl_today_still_untouched(client):
    # the existing today-scoped endpoint keeps its shape + semantics
    d = client.get("/api/pnl").get_json()
    assert d["summary"]["net"] == -25.0 and d["summary"]["closed"] == 4
    assert "equity_curve" in d and "closed_trades" in d


# ── Scanner Attribution (funnel + quality + shared label) ────────────────────
def test_scanner_attribution(client):
    d = client.get("/api/scanner-attribution?period=today").get_json()
    by = {r["scanner"]: r for r in d["rows"]}
    g = by["gap_fade_long"]
    assert g["received"] == 58 and g["accepted"] == 50            # webhook 50+8 / 50
    assert g["trades"] == 2 and g["net"] == 100.0 and g["win_rate"] == 50.0
    # quality = 100 × (50/58) × min(1, 2/50) × (1/2) = 1.7
    assert g["quality_score"] == 1.7
    # unmapped scanner surfaces honestly, never a fabricated split (T6)
    assert by["momentum_combo"]["linked_strategy"] == "scanner-level (shared)"
    assert by["momentum_combo"]["trades"] == 0 and by["momentum_combo"]["quality_score"] == 0.0


# ── Trade story (honest "not captured") ──────────────────────────────────────
def test_trade_story_and_honest_gap(client):
    d = client.get("/api/trade-story/trd_c1").get_json()
    assert d["system_score"] == 72 and d["scanner"] == "gap_fade_long"
    labels = [s["label"] for s in d["timeline"]]
    assert labels[0] == "Signal Received" and "Validation" in labels
    val = next(s for s in d["timeline"] if s["label"] == "Validation")
    assert "not captured" in val["note"]           # G-2 per-stage timings unpersisted
    assert client.get("/api/trade-story/nope").status_code == 404


# ── Trade Explorer (per-trade table + System Score join) ─────────────────────
def test_trade_explorer(client):
    d = client.get("/api/trades?period=today").get_json()
    assert d["count"] == 8                          # c1-c4 + o1-o4 created today
    by = {r["trade_id"]: r for r in d["rows"]}
    assert by["trd_c1"]["system_score"] == 72 and by["trd_c1"]["scanner"] == "gap_fade_long"
    assert by["trd_o1"]["system_score"] is None     # no screener score seeded → honest null


# ── Health-state mapping (pure) hits all 5 states; integration on the fixture ─
def test_health_state_mapper_all_states():
    hs = analytics_period.health_state
    assert hs(False, "GREEN", None) == "Disabled"
    assert hs(True, "GREEN", "RED") == "Silent"      # silence RED wins over GREEN badge
    assert hs(True, "RED", "GREEN") == "Warning"
    assert hs(True, "YELLOW", "GREEN") == "Quiet"
    assert hs(True, "GREEN", "GREEN") == "Healthy"


def _health_at(gui_config, hhmm):
    cfg = copy.deepcopy(gui_config)
    cfg["market_clock"]["active_weekdays"] = [0, 1, 2, 3, 4, 5, 6]
    now = freshness.parse_ist(f"{TODAY}T{hhmm}:00+05:30")
    return analytics_period.build_strategy_health(cfg, "week", now=now)


def test_health_integration(gui_config):
    h = _health_at(gui_config, "10:40")              # in entry window → silence alarmed
    by = {r["strategy"]: r for r in h["rows"]}
    assert by["gap_fade_long"]["state"] == "Healthy" and by["gap_fade_long"]["health_score"] == 90
    assert by["first_pullback_long"]["state"] == "Silent"     # enabled, no signal, in-window
    assert by["vwap_bounce_long"]["state"] == "Quiet"         # YELLOW badge, fresh signal
    assert by["gap_fade_short"]["state"] == "Disabled"
    # period signal trend ≥ today (week includes yesterday's linking signals)
    assert by["gap_fade_long"]["signals_period"] >= by["gap_fade_long"]["signals_today"]


# ── Endpoints require auth; screens render ───────────────────────────────────
def test_g5c_endpoints_require_auth(app):
    anon = app.test_client()
    for ep in ("/api/strategy-ranking", "/api/strategy-health", "/api/scanner-attribution",
               "/api/trades", "/api/trade-story/trd_c1", "/api/analytics/pnl"):
        assert anon.get(ep).status_code == 401, ep


def test_g5c_screens_render(client):
    for route in ("/strategy-ranking", "/strategy-health", "/scanner-attribution",
                  "/trades", "/pnl-analytics"):
        assert client.get(route).status_code == 200, route


# ── Additive-only: existing pinned shapes frozen; no "Signal Score" ──────────
def test_pinned_shapes_frozen_after_g5c(client):
    assert len(client.get("/api/dashboard").get_json()["service_health"]) == 6
    assert len(client.get("/api/pipeline").get_json()["stages"]) == 13
    cap = client.get("/api/capacity").get_json()
    assert len(cap["rows"]) == 8 and len(cap["groups"]) == 6
    assert set(client.get("/api/strategies").get_json()["rankings"]) == {
        "net_pnl", "win_rate", "expectancy", "success_rate"}


def test_no_signal_score_after_g5c():
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    assert_signal_score_confined_to_screen04(root)
