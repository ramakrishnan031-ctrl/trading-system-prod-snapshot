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
    assert len(d["rows"]) == 8            # G2a dashboard-widget contract preserved
    assert {"key", "label", "used", "limit", "remaining", "status", "unit"} <= set(d["rows"][0])
    assert len(d["groups"]) == 6          # G2b-1 grouped screen
    assert {"name", "rows"} <= set(d["groups"][0])


def test_strategies_contract(client):
    r = client.get("/api/strategies")
    assert r.status_code == 200
    d = r.get_json()
    assert d["count"] == 5
    row = d["rows"][0]
    assert {"basic", "signals", "processing", "trading", "performance",
            "risk", "health"} <= set(row)                  # the 7 Rama groups
    assert {"name", "enabled", "scanners", "mode", "rank"} <= set(row["basic"])
    assert set(d["rankings"]) == {"net_pnl", "win_rate", "expectancy", "success_rate"}
    assert isinstance(d["scanner_level"], list)


def test_strategy_detail_contract(client):
    r = client.get("/api/strategies/gap_fade_long")
    assert r.status_code == 200
    d = r.get_json()
    assert d["strategy"]["basic"]["name"] == "gap_fade_long"
    assert set(d["rankings"]) == {"net_pnl", "win_rate", "expectancy", "success_rate"}
    assert client.get("/api/strategies/nope").status_code == 404


def test_signals_contract(client):
    d = client.get("/api/signals").get_json()
    assert {"date", "denominator", "count", "rows"} <= set(d)
    assert {"received", "accepted", "rejected", "duplicated_stored", "stored"} <= set(d["denominator"])
    if d["rows"]:
        assert {"signal_id", "received_at", "scanner", "strategy", "symbol",
                "status", "family"} <= set(d["rows"][0])


def test_orders_contract(client):
    d = client.get("/api/orders").get_json()
    assert {"date", "count", "rows"} <= set(d)
    if d["rows"]:
        assert {"order_id", "trade_id", "leg", "status", "qty_requested", "qty_filled",
                "placed_at", "filled_at", "rejection_reason", "superseded_by",
                "symbol", "strategy", "place_to_fill_ms"} <= set(d["rows"][0])


def test_positions_contract(client):
    d = client.get("/api/positions").get_json()
    assert {"mode", "count", "max_open_positions", "open_states",
            "unrealized_note", "rows"} <= set(d)
    if d["rows"]:
        assert {"trade_id", "symbol", "strategy", "direction", "qty_filled",
                "sl_initial", "tgt_initial", "risk_amount", "margin_reserved",
                "inning_no"} <= set(d["rows"][0])


def test_holdings_contract(client):
    d = client.get("/api/holdings").get_json()
    assert {"banner", "count", "rows"} <= set(d)


def test_auth_required_api(app):
    anon = app.test_client()   # no session
    for ep in ("/api/dashboard", "/api/pipeline", "/api/capacity", "/api/strategies",
               "/api/signals", "/api/orders", "/api/positions", "/api/holdings"):
        assert anon.get(ep).status_code == 401, ep


def test_auth_required_pages_redirect(app):
    anon = app.test_client()
    for page in ("/", "/strategies", "/signals", "/orders", "/positions",
                 "/holdings", "/capacity"):
        r = anon.get(page)
        assert r.status_code == 302, page
        assert "/login" in r.headers.get("Location", "")


def test_pages_render_when_authed(client):
    for page in ("/", "/strategies", "/signals", "/orders", "/positions",
                 "/holdings", "/capacity"):
        r = client.get(page)
        assert r.status_code == 200, page


def test_login_page_is_reachable_without_auth(app):
    anon = app.test_client()
    r = anon.get("/login")
    assert r.status_code == 200
    assert b"AlgoCore Systems" in r.data


# ─────────────────────────────────────────────────────────────────────────────
# P3 (17-Jul-2026) — route-map-driven guard invariant.
# ─────────────────────────────────────────────────────────────────────────────
# The two tests above enumerate the endpoints to check BY HAND. That is exactly the
# gap the fixture-blindness investigation flagged: a NEW blueprint is protected by the
# before_request guard, but nothing PROVES it — a forgotten endpoint just never gets an
# anon test. This walks app.url_map instead, so coverage cannot be skipped by omission:
# every endpoint not in _LOGIN_EXEMPT must deny an anonymous caller.
import re as _re


def _concrete_path(rule) -> str:
    """A requestable path for a rule: replace every <...> placeholder (incl.
    <path:filename> for the static route) with a dummy segment so the URL matches
    this rule and the before_request guard runs (the anon request is intercepted
    before the view, so the dummy value is never dereferenced)."""
    return _re.sub(r"<[^>]+>", "x", rule.rule)


def test_every_non_exempt_route_denies_anonymous(app):
    """P3: iterate app.url_map and assert EVERY endpoint not in _LOGIN_EXEMPT denies an
    anonymous client — /api/* -> 401, everything else -> 302 to /login (matching the
    before_request logic in backend/app.py). This is the automatic invariant behind the
    hand-written test_auth_required_* lists: a new route cannot skip coverage by being
    forgotten. Includes the Flask `static` endpoint, which app.py deliberately guards too."""
    from backend.app import _LOGIN_EXEMPT

    anon = app.test_client()
    checked = 0
    for rule in app.url_map.iter_rules():
        if rule.endpoint in _LOGIN_EXEMPT:
            continue
        methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}
        method = "GET" if "GET" in methods else (next(iter(methods)) if methods else "GET")
        path = _concrete_path(rule)
        resp = anon.open(path, method=method)
        if path.startswith("/api/"):
            assert resp.status_code == 401, (
                f"{method} {path} (endpoint {rule.endpoint!r}) must 401 an anonymous "
                f"caller, got {resp.status_code}"
            )
        else:
            loc = resp.headers.get("Location", "")
            assert resp.status_code == 302 and "/login" in loc, (
                f"{method} {path} (endpoint {rule.endpoint!r}) must 302 -> /login for an "
                f"anonymous caller, got {resp.status_code} Location={loc!r}"
            )
        checked += 1
    # Guard the guard: if this collapses to near-zero the loop proved nothing.
    assert checked >= 15, f"expected the invariant to cover many routes, only saw {checked}"
