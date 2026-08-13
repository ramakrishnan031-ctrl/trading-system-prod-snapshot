"""T3 — G5a shared components render with sample props + honor the locked rules:
single System Score (L8), ExportButton OFF by default (L7), TwoStatePanel honest
(L3/L9), pipeline stays 13 stages, additive-only pinned counts unmoved.

Markup-level assertions (this stack has no JS runner — the interactive sort /
pagination behaviour of tableMixin is verified in Rama's browser pass). Runs on
both v41 + v42 fixtures via the shared `app`/`client`.
"""
from __future__ import annotations

import os

from flask import render_template_string

from conftest import assert_signal_score_label_is_retired   # noqa: E402


def _render(app, snippet):
    with app.test_request_context():
        return render_template_string(snippet)


# ── 1 DataTable ──────────────────────────────────────────────────────────────
def test_data_table_has_sort_on_every_column_and_rows_per_page(app):
    html = _render(app,
        "{% from 'components.html' import data_table %}"
        "{{ data_table('t', [{'key':'a','label':'Col A'},"
        "{'key':'b','label':'Col B','kind':'num'}], rows_expr='rows') }}")
    assert "Col A" in html and "Col B" in html
    assert "tSort('a')" in html and "tSort('b')" in html      # sort ▲▼ every column
    assert "tArrow('a')" in html and "tArrow('b')" in html
    assert 'x-model.number="tPageSize"' in html               # rows-per-page selector
    assert "tPaged(rows)" in html


# ── 2 FilterBar ──────────────────────────────────────────────────────────────
def test_filter_bar_inputs_select_and_reset(app):
    html = _render(app,
        "{% from 'components.html' import filter_bar %}"
        "{{ filter_bar([{'key':'sym','label':'Symbol'},"
        "{'key':'leg','label':'Leg','type':'select','options':['ENTRY','SL']}]) }}")
    assert "fVals['sym']" in html and "fVals['leg']" in html
    assert "Reset filters" in html and "fReset()" in html
    assert '<option value="ENTRY">' in html


# ── 3 KpiRow / KpiCard ───────────────────────────────────────────────────────
def test_kpi_row_and_card(app):
    html = _render(app,
        "{% from 'components.html' import kpi_row, kpi_card %}"
        "{% call kpi_row() %}{{ kpi_card('Net Profit', 'data.net', tone='pos') }}{% endcall %}")
    assert "kpi-row" in html and "kpi-pos" in html
    assert "Net Profit" in html and 'x-text="data.net"' in html


# ── 4 PeriodSelector ─────────────────────────────────────────────────────────
def test_period_selector_emits_all_periods(app):
    html = _render(app, "{% from 'components.html' import period_selector %}{{ period_selector() }}")
    for label in ("Today", "Week", "Month", "Custom"):
        assert label in html
    assert "pSet('today')" in html and "pSet('custom')" in html


# ── 5 EventsAlertsPanel (L10 merge) ──────────────────────────────────────────
def test_events_alerts_panel_merges_both(app):
    html = _render(app, "{% from 'components.html' import events_alerts_panel %}{{ events_alerts_panel() }}")
    assert "sev-chip" in html          # alerts severity
    assert "ev-type" in html           # events
    assert "Recent Events" in html


# ── 6 TwoStatePanel (L3/L9 honest gap) ───────────────────────────────────────
def test_two_state_panel_is_honest_never_fabricates(app):
    html = _render(app,
        "{% from 'components.html' import two_state_panel %}"
        "{% call two_state_panel('data.available') %}VALUE{% endcall %}")
    assert "Pending Broker Source (G4/P1)" in html
    assert "ts-unavailable" in html
    assert "never inferred" in html


# ── 7 ExportButton (L7 default OFF) ──────────────────────────────────────────
def test_export_button_disabled_by_default(app):
    html = _render(app, "{% from 'components.html' import export_button %}{{ export_button('Export') }}")
    assert "disabled" in html
    assert "Q3" in html                # tooltip states why it is off


# ── 8 ScoreChip (L8 single score) ────────────────────────────────────────────
def test_score_chip_is_system_score_only(app):
    html = _render(app, "{% from 'components.html' import score_chip %}{{ score_chip('row.system_score') }}")
    assert "System Score" in html
    assert "Signal Score" not in html


# ── 9/10/12 Medals, StatusChip, LifecycleTimeline ────────────────────────────
def test_medal_status_timeline_render(app):
    html = _render(app,
        "{% from 'components.html' import medal, status_chip, lifecycle_timeline %}"
        "{{ medal('r.rank') }}{{ status_chip('r.state') }}{{ lifecycle_timeline('r.steps') }}")
    assert "medal" in html and "🥇" in html
    assert "status-chip" in html
    assert "lifecycle" in html and "lc-step" in html


# ── 11 FunnelPipelineCard (keep 13 stages) ───────────────────────────────────
def test_funnel_pipeline_card_renders_stage_cards(app):
    html = _render(app,
        "{% from 'components.html' import funnel_pipeline_card %}{{ funnel_pipeline_card('stages') }}")
    assert "pipeline-grid" in html and "pcard" in html
    assert "(stages || [])" in html


def test_pipeline_endpoint_still_13_stages(client):
    # The 13-stage guarantee lives in /api/pipeline (UNTOUCHED in G5a); the card
    # renders whatever it is given.
    assert len(client.get("/api/pipeline").get_json()["stages"]) == 13


# ── Additive-only invariant: the equality-pinned counts must not move ─────────
def test_pinned_contract_counts_unmoved(client):
    assert len(client.get("/api/dashboard").get_json()["service_health"]) == 6
    assert len(client.get("/api/pipeline").get_json()["stages"]) == 13
    cap = client.get("/api/capacity").get_json()
    assert len(cap["rows"]) == 8 and len(cap["groups"]) == 6
    assert set(client.get("/api/strategies").get_json()["rankings"]) == {
        "net_pnl", "win_rate", "expectancy", "success_rate"}


# ── L8 RESTORED 13-Aug-2026: no "Signal Score" ANYWHERE (exception closed) ───
def test_no_signal_score_label_anywhere_in_frontend():
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
    assert_signal_score_label_is_retired(root)


# ── Framework wiring: components.js loaded + URL-free (I7 CDN discipline) ─────
def test_components_js_loaded_and_url_free():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = open(os.path.join(root, "frontend", "templates", "base.html"), encoding="utf-8").read()
    assert "/static/components.js" in base
    cjs = open(os.path.join(root, "frontend", "static", "components.js"), encoding="utf-8").read()
    for fn in ("function tableMixin", "function filterMixin", "function periodMixin"):
        assert fn in cjs
    assert "http://" not in cjs and "https://" not in cjs
