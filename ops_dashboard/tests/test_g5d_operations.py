"""G5d — Operations/Investigation: read-only Controls (ZERO write path), Trade Logs
forensic feed, merged Live-Activity feed, two-state Positions/Holdings (broker side
honestly UNAVAILABLE — no fabricated numbers), final nav. Additive-only; existing
shapes frozen. Both v41+v42 via shared client/app.
"""
from __future__ import annotations

import os

from backend.services import operations

from conftest import assert_signal_score_confined_to_screen04   # noqa: E402

_FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")


# ── Controls — READ-ONLY summary (L4) ────────────────────────────────────────
def test_controls_summary_fields(client):
    d = client.get("/api/controls-summary").get_json()
    assert d["read_only"] is True
    assert d["trade_type"]["label"] == "Intraday Only"
    assert d["strategies"]["enabled_count"] == 4 and d["strategies"]["total"] == 5
    assert d["limits"]["max_daily_trades"] == 10 and d["limits"]["max_open_positions"] == 5
    assert d["limits"]["max_concentration_pct"] == 0.10 and d["limits"]["daily_loss_limit_pct"] == 0.03
    assert d["kill_switch"]["state"] == "INACTIVE"
    assert d["telegram_alerts"]["configured"] is None          # honest — not in snapshot
    assert d["future_controls"]["available"] is False


def test_controls_template_is_read_only():
    """T4: the Controls template contains ZERO write surface (no form/button/POST)."""
    src = open(os.path.join(_FRONTEND, "templates", "controls.html"), encoding="utf-8").read().lower()
    assert "<form" not in src and "<button" not in src
    assert 'method="post"' not in src and 'type="submit"' not in src


# ── Trade Logs — forensic feed with full attribution + recon actions ─────────
def test_trade_logs(client):
    d = client.get("/api/trade-logs?period=week").get_json()
    assert d["count"] == 10                                     # 8 today + 2 yesterday (w1,w2)
    by = {r["trade_id"]: r for r in d["rows"]}
    assert by["trd_c1"]["scanner"] == "gap_fade_long" and by["trd_c1"]["system_score"] == 72
    assert by["trd_c1"]["strategy"] == "gap_fade_long"
    # trd_c3 has a reconciliation_log action (MANUAL_CLOSE)
    assert len(by["trd_c3"]["recon_actions"]) == 1
    assert by["trd_c3"]["recon_actions"][0]["check"] == "MANUAL_CLOSE"


# ── Live Activity — merged feed across ≥4 of 7 sources, newest first ─────────
def test_activity_merged_feed(client):
    d = client.get("/api/activity").get_json()
    types = set(d["types"])
    assert {"SIGNAL", "ORDER", "TRADE", "CAPITAL"} <= types     # ≥4 sources present
    assert "SYSTEM" in types                                    # system_events too
    tss = [r["ts"] for r in d["rows"]]
    assert tss == sorted(tss, reverse=True)                     # newest first
    # every row carries a type badge + deep-link
    assert all(r.get("type") and r.get("link") for r in d["rows"])


# ── Positions two-state: system full, broker UNAVAILABLE (no fabricated value) ─
def test_positions_two_state(client):
    d = client.get("/api/positions").get_json()
    assert d["unavailable"]["reason"] == "Pending Broker Source (G4)"
    assert set(d["unavailable"]["fields"]) == {"ltp", "mtm", "unrealized", "current_rr"}
    assert d["count"] == 4                                      # system side renders fully
    assert all("scanner" in r for r in d["rows"])              # additive scanner attribution
    # pre-existing contract intact
    assert d["unrealized_note"] == "G4" and "open_states" in d


def test_positions_template_broker_unavailable():
    src = open(os.path.join(_FRONTEND, "templates", "positions.html"), encoding="utf-8").read()
    assert "Pending Broker Source (G4)" in src


def test_holdings_template_broker_first_two_state():
    src = open(os.path.join(_FRONTEND, "templates", "holdings.html"), encoding="utf-8").read()
    assert "Pending Broker Source (G4/P1)" in src
    # broker/delta/recon columns are honest placeholders, not computed numbers
    assert "Broker Qty" in src and "Delta Qty" in src


def test_holdings_endpoint_system_side(client):
    d = client.get("/api/holdings").get_json()
    assert {"banner", "count", "rows"} <= set(d)               # contract intact
    # gtt_state is empty in the fixture → system side empty, broker side still honest
    assert d["count"] == 0


# ── Nav complete + every screen 200; new endpoints auth-gated ────────────────
_G5D_SCREENS = ("/controls", "/trade-logs", "/live-activity", "/positions", "/holdings")


def test_g5d_screens_render(client):
    for route in _G5D_SCREENS:
        assert client.get(route).status_code == 200, route


def test_g5d_endpoints_require_auth(app):
    anon = app.test_client()
    for ep in ("/api/controls-summary", "/api/trade-logs", "/api/activity"):
        assert anon.get(ep).status_code == 401, ep


# ── Additive-only: pinned shapes + HARD_KILL blink frozen; no Signal Score ───
def test_pinned_shapes_frozen_after_g5d(client):
    assert len(client.get("/api/dashboard").get_json()["service_health"]) == 6
    assert len(client.get("/api/pipeline").get_json()["stages"]) == 13
    cap = client.get("/api/capacity").get_json()
    assert len(cap["rows"]) == 8 and len(cap["groups"]) == 6
    assert set(client.get("/api/strategies").get_json()["rankings"]) == {
        "net_pnl", "win_rate", "expectancy", "success_rate"}


def test_hard_kill_blink_still_single():
    base = open(os.path.join(_FRONTEND, "templates", "base.html"), encoding="utf-8").read()
    assert base.count("chip-blink") == 1


def test_no_signal_score_after_g5d():
    assert_signal_score_confined_to_screen04(_FRONTEND)
