"""G5b — reuse screens: additive backend fields (scanner attribution, long/short
exposure, SL/TGT hits, profit factor, slippage rankings/trend) with exact seed
values; the 7 screens render on G5a components; Capital & Risk consolidates 4→1
while the old endpoints stay 200; honest G-gaps rendered. Additive-only: existing
endpoint keys/lengths unmoved. Both v41+v42 fixtures via shared client/app.
"""
from __future__ import annotations

import os

from backend.readers import db_reader
from backend.services import strategy_tower

from conftest import assert_signal_score_label_is_retired   # noqa: E402


# ── Additive readers — exact values on the seed ──────────────────────────────
def test_scanner_for_trades_join(gui_config):
    m = db_reader.scanner_for_trades(gui_config, ["trd_c1", "trd_c4", "trd_missing"])
    assert m == {"trd_c1": "gap_fade_long", "trd_c4": "gap_fade_long"}   # missing omitted
    assert db_reader.scanner_for_trades(gui_config, []) == {}


def test_strategy_sltgt_hits(gui_config, today):
    h = db_reader.strategy_sltgt_hits(gui_config, today)
    assert h["gap_fade_long"] == {"sl_hits": 1, "tgt_hits": 1}    # trd_c1 SL_HIT, trd_c4 TGT_HIT
    assert h["vwap_bounce_long"] == {"sl_hits": 0, "tgt_hits": 0}  # EOD + MANUAL exits


def test_exposure_by_direction(gui_config):
    d = db_reader.exposure_by_direction(gui_config)
    assert d == {"long_value": 40000.0, "short_value": 0.0, "net_value": 40000.0,
                 "long_positions": 4, "short_positions": 0}


def test_profit_factor_today(gui_config, today):
    # closed net: -100, -50, -75, +200 → wins 200 / losses 225
    assert db_reader.profit_factor_today(gui_config, today) == 0.89


def test_slippage_trend(gui_config, today):
    assert db_reader.slippage_trend_today(gui_config, today) == [
        {"hour": "10", "avg_slippage": 1.75, "n": 2}]


# ── /api/slippage additive keys (existing keys byte-unchanged) ───────────────
def test_slippage_endpoint_additive(client):
    d = client.get("/api/slippage").get_json()
    assert d["count"] == 2                                    # pre-existing intact
    assert d["worst"][0]["entry_slippage_rs"] == 3.0
    ps = {r["scanner"]: r for r in d["per_scanner"]}
    assert ps["gap_fade_long"]["trades"] == 2 and ps["gap_fade_long"]["breaches"] == 1
    assert ps["gap_fade_long"]["worst_rs"] == 3.0 and ps["gap_fade_long"]["avg_slip_rs"] == 1.75
    assert {r["symbol"] for r in d["per_symbol"]} == {"AAA"}
    pb = {r["band"]: r for r in d["price_buckets"]}
    assert pb["200-300"]["trades"] == 2 and pb["200-300"]["breaches"] == 1
    assert d["trend"] == [{"hour": "10", "avg_slippage": 1.75, "n": 2}]


# ── /api/exposure by_direction additive (existing keys intact) ───────────────
def test_exposure_endpoint_additive(client):
    d = client.get("/api/exposure").get_json()
    assert d["gross_exposure"] == 40000.0                     # pre-existing intact
    assert d["by_direction"] == {"long_value": 40000.0, "short_value": 0.0,
                                 "net_value": 40000.0, "long_positions": 4, "short_positions": 0}


# ── /api/dashboard profit_factor additive ────────────────────────────────────
def test_dashboard_profit_factor_additive(client):
    c = client.get("/api/dashboard").get_json()["summary"]["counters"]
    assert c["profit_factor"] == 0.89
    assert "received" in c and "net_pnl_today" in c          # existing counters intact


# ── strategy_tower additive per-row keys (funnel/failures dicts unmoved) ─────
def test_strategy_tower_additive_keys(gui_config, today):
    t = strategy_tower.build_strategy_tower(gui_config, today)
    gap = next(r for r in t["rows"] if r["basic"]["name"] == "gap_fade_long")
    assert gap["family"] == "gap_fade"
    assert gap["sl_tgt_hits"] == {"sl_hits": 1, "tgt_hits": 1}
    assert gap["performance"]["profit_factor"] == 2.0        # win_sum 200 / |loss_sum| 100
    # equality-pinned dicts + rankings unmoved (additive discipline)
    assert gap["funnel"] == {"received": 70, "accepted": 8, "orders_created": 7,
                             "filled": 6, "trades_closed": 2}
    assert set(t["rankings"]) == {"net_pnl", "win_rate", "expectancy", "success_rate"}


# ── Screen render (authed) ───────────────────────────────────────────────────
_G5B_SCREENS = ("/", "/config", "/logs", "/audit", "/slippage", "/strategies", "/capital-risk")


def test_g5b_screens_render(client):
    for route in _G5B_SCREENS:
        assert client.get(route).status_code == 200, route


def test_capital_risk_consolidates_but_old_routes_live(client):
    html = client.get("/capital-risk").get_data(as_text=True)
    for zone in ("Capital", "Risk", "Exposure", "Limits"):       # 4 zones
        assert zone in html, zone
    assert "Pending Broker Source" in html                        # MTM honest (G-1)
    assert "Long Exposure" in html and "Net Exposure" in html
    for route in ("/risk", "/capital", "/exposure", "/capacity"):  # old endpoints preserved
        assert client.get(route).status_code == 200, route


# ── Honest-gap render proofs (never fabricated) ──────────────────────────────
def test_honest_gap_strings_rendered(client):
    assert "not captured" in client.get("/config").get_data(as_text=True).lower()
    assert "not captured" in client.get("/audit").get_data(as_text=True).lower()
    # ⚠️ PHRASE UPDATED, PROPERTY UNCHANGED. The G5b logs viewer said
    # "resolution status not tracked (G-5)". Screen 15 replaced that screen and
    # says the same thing in the vocabulary Screens 12-15 all use: Resolution
    # Status renders NOT INSTRUMENTED and the payload carries a `resolution` gap
    # with its reason. ⛔ The assertion was NOT relaxed to make a change pass —
    # the honesty it guards was verified on the new screen first, and it is now
    # stated in more places than before.
    logs_html = client.get("/logs").get_data(as_text=True)
    assert "Resolution Status" in logs_html
    assert "NOT INSTRUMENTED" in logs_html
    assert "Pending Broker Source" in client.get("/capital-risk").get_data(as_text=True)


# ── ExportButton still OFF everywhere; "Signal Score" retired (L8 restored) ──
def test_export_off_and_no_signal_score():
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    assert_signal_score_label_is_retired(root)


def test_export_button_disabled_on_g5b_screens(client):
    # default flags OFF → the shared ExportButton renders disabled with the Q3 tooltip
    html = client.get("/slippage").get_data(as_text=True)
    assert "btn-export" in html and "disabled" in html and "Q3" in html
