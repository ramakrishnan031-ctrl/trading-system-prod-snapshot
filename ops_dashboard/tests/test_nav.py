"""T2 — NavShell: the 5-group menu rendered over the EXISTING routes.

Proves: every current route still 200s + renders inside the new shell; the five
groups are present with the correct landing (Dashboard); the AlgoCore rebrand is
label-only; the standalone screens that leave the sidebar (merged/consolidated
per Phase B §D) stay reachable by direct URL — their endpoints are NOT deleted.
Runs on both v41 + v42 fixtures via the shared `client`.
"""
from __future__ import annotations

import os

# Menu routes that have a home in the 5-group sidebar (all live after G5d).
_MENU_ROUTES = ("/", "/strategies", "/signals", "/orders", "/positions", "/holdings",
                "/strategy-ranking", "/strategy-health", "/scanner-attribution",
                "/trades", "/pnl-analytics", "/slippage", "/execution",
                "/live-activity", "/services", "/capital-risk", "/config", "/controls",
                "/audit", "/trade-logs", "/logs")

# Standalone screens that DROPPED OFF the sidebar (merged/consolidated) but remain
# reachable by direct URL — endpoints not deleted.
_NON_MENU_ROUTES = ("/risk", "/capital", "/exposure", "/capacity", "/pnl",
                    "/vm", "/statistics", "/reports", "/alerts")

_GROUPS = ("Trading", "Analytics", "Operations", "Investigation")


def test_all_existing_routes_render_in_new_shell(client):
    for route in _MENU_ROUTES + _NON_MENU_ROUTES:
        r = client.get(route)
        assert r.status_code == 200, route
        assert b"sidebar" in r.data, route          # rendered inside the shell


def test_landing_is_dashboard(client):
    html = client.get("/").get_data(as_text=True)
    assert "<title>AlgoCore Systems — Dashboard</title>" in html


def test_five_group_nav_present(client):
    html = client.get("/").get_data(as_text=True)
    for grp in _GROUPS:
        assert '<div class="nv-group">' + grp + "</div>" in html, grp
    assert 'href="/"' in html                        # Dashboard landing item


def test_nav_fully_live_no_placeholders(client):
    # G5d completes the menu: every screen is now a live link — no nv-soon left.
    html = client.get("/").get_data(as_text=True)
    assert "nv-soon" not in html
    for label in ("Live Activity", "Trade Logs", "Strategy Ranking",
                  "Scanner Attribution", "Capital &amp; Risk", "System Health"):
        assert label in html, label


def test_algocore_rebrand_is_label_only(client, app):
    # Brand label changed in the shell...
    assert "AlgoCore" in client.get("/").get_data(as_text=True)
    # ...and on the login page (anon client — an authed session 302s to dashboard).
    login = app.test_client().get("/login").get_data(as_text=True)
    assert "AlgoCore Systems" in login
    assert "Ops Dashboard" not in login
    # ...but the internal product name (module path) is UNCHANGED.
    import backend.app as app_module
    assert app_module.__name__.startswith("backend")     # still ops_dashboard/backend


def test_non_menu_endpoints_still_reachable(client):
    for route in _NON_MENU_ROUTES:
        assert client.get(route).status_code == 200, route


def test_mobile_collapse_media_query_present():
    css = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "frontend", "static", "style.css")
    src = open(css, encoding="utf-8").read()
    assert "@media (max-width: 900px)" in src
    assert ".sidebar { width: 46px" in src           # collapses to icon-only
    assert ".nv-label, .nv-group, .sb-brand, .nv-soon-tag { display: none; }" in src
