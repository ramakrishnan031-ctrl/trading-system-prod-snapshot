"""API contract — JSON schema of every endpoint + auth enforcement."""
from __future__ import annotations


def test_dashboard_contract(client):
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    d = r.get_json()
    assert {"today", "summary", "service_health", "freshness", "events"} <= set(d)
    assert {"mode", "trader_alive", "kill_switch", "counters", "phase"} <= set(d["summary"])
    assert len(d["service_health"]) == 6
    assert {"unit", "state"} <= set(d["service_health"][0])
    assert {"phase", "poll_ms", "active"} <= set(d["freshness"])


def test_pipeline_contract(client):
    r = client.get("/api/pipeline")
    assert r.status_code == 200
    d = r.get_json()
    assert len(d["stages"]) == 13
    assert {"key", "name", "kind", "count", "color", "active"} <= set(d["stages"][0])
    assert {"state", "halted"} <= set(d["halt"])


def test_capacity_contract(client):
    r = client.get("/api/capacity")
    assert r.status_code == 200
    d = r.get_json()
    assert len(d["rows"]) == 8
    assert {"key", "label", "used", "limit", "remaining", "status", "unit"} <= set(d["rows"][0])


def test_strategy_contract(client):
    r = client.get("/api/strategy_panel")
    assert r.status_code == 200
    d = r.get_json()
    assert d["count"] == 4
    assert {"name", "enabled", "trades", "net_pnl", "rank", "max_concurrent"} <= set(d["rows"][0])


def test_auth_required_api(app):
    anon = app.test_client()   # no session
    assert anon.get("/api/dashboard").status_code == 401
    assert anon.get("/api/pipeline").status_code == 401


def test_auth_required_page_redirects(app):
    anon = app.test_client()
    r = anon.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")


def test_login_page_is_reachable_without_auth(app):
    anon = app.test_client()
    r = anon.get("/login")
    assert r.status_code == 200
    assert b"Ops Dashboard" in r.data
