"""G5d — Operations/Investigation: read-only Controls (ZERO write path), Trade Logs
forensic feed, merged Live-Activity feed, two-state Positions/Holdings (broker side
honestly UNAVAILABLE — no fabricated numbers), final nav. Additive-only; existing
shapes frozen. Both v41+v42 via shared client/app.
"""
from __future__ import annotations

import os

from backend.services import operations

from conftest import assert_signal_score_label_is_retired   # noqa: E402

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


def test_controls_template_has_no_uncontrolled_write_surface():
    """SUPERSEDED-AND-REPLACED, 17-Aug-2026 — ⛔ NOT deleted.

    The original T4 asserted the Controls template contained **no `<button>` at
    all**. Rama's 17-Aug ruling makes Screen 17 the operational control surface,
    so that assertion now contradicts the approved design and cannot stand.

    🔑 WHAT IT WAS ACTUALLY PROTECTING, and what is re-pinned here instead:
      (a) the dashboard must not gain its OWN write path — no `<form>` posting
          to a dashboard route, no `type=submit`, no browser-native mutation;
      (b) every mutation must leave through the ONE allowlisted control endpoint
          so it reaches the trading process's control plane and is audited there;
      (c) the screen must never claim a control is live when the plane is down.

    ⛔ (a) is unchanged and still enforced. Buttons are now permitted, but they
    must be `type="button"` and act through `fetch("/api/controls/action")`.
    """
    src = open(os.path.join(_FRONTEND, "templates", "controls.html"),
               encoding="utf-8").read()
    low = src.lower()

    # (a) no browser-native write surface — the original protection, intact.
    assert "<form" not in low, "the Controls template must not contain a <form>"
    assert 'method="post"' not in low
    assert 'type="submit"' not in low

    # (b) every mutation leaves through the single allowlisted endpoint.
    assert '/api/controls/action' in src, "mutations must go through the control endpoint"
    for other in ('fetch("/api/controls-summary"', "method: \"PUT\"", "method: \"DELETE\""):
        assert other not in src

    # Buttons exist now (it is a control surface) and must be explicitly typed so
    # none can submit anything implicitly.
    assert 'type="button"' in low, "control buttons must be type=button"

    # (c) the screen gates its controls on the LIVE plane being reachable.
    assert "planeUp()" in src, "controls must be gated on control-plane availability"
    assert ':disabled="!planeUp()' in src, "controls must disable when the plane is down"


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
    """⚠️⚠️ CORRECTED 16-Aug-2026 (Screen 22). This asserted the G5d stub's
    "Pending Broker Source (G4/P1)", i.e. that the broker side had no source and
    would "activate when P1 ships". THAT CLAIM WAS STALE, and the test was
    keeping it alive:

      · P1 (`eod_broker_reconciliation`) is a DATE-LEVEL verdict table — one row
        per day, no symbols — so it was never the source this screen needed.
      · The PER-SYMBOL source, `position_reconciliation` (core/schema.sql TABLE
        23, v20), has existed all along and was simply not read.

    ⭐ THE INTENT SURVIVES AND IS WHAT IS PINNED HERE: the screen still shows
    Broker Qty / Delta Qty / Reconciliation Status, and it still fabricates
    nothing — what has no source now is the LIVE PRICE (Current Price / Market
    Value / Unrealized P&L), and the screen says so in those words.
    """
    src = open(os.path.join(_FRONTEND, "templates", "holdings.html"), encoding="utf-8").read()
    # the broker-first columns are still there — now computed, from a real source
    assert "Broker Qty" in src and "Delta Qty" in src
    assert "Reconciliation Status" in src
    assert "position_reconciliation" in src
    # ⛔ and the fields that genuinely have no source are still declared as gaps
    assert "NOT INSTRUMENTED" in src
    for field in ("Current Price", "Market Value", "Unrealized P&L"):
        assert field in src, field
    # ⛔ the retired claim must not come back by accident
    assert "Pending Broker Source (G4/P1)" not in src


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


# ── Additive-only: pinned shapes + HARD_KILL frozen; label retired (L8) ──────
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
    assert_signal_score_label_is_retired(_FRONTEND)
