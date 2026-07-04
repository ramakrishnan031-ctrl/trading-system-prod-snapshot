# Ops Dashboard — Shared Component Framework (G5a)

Reusable frontend building blocks for the G5 redesign. Every later screen
(G5b–G5d) composes these; do NOT hand-roll a table/filter/KPI/panel that a
component already covers. Pattern = **Alpine-over-JSON** (the G2 precedent):
markup binds to the page's Alpine scope; interactive components need their
matching mixin spread into the page `x-data`.

- **Markup:** `frontend/templates/components.html` (Jinja macros).
- **Behaviour:** `frontend/static/components.js` (Alpine mixins — `tableMixin`,
  `filterMixin`, `periodMixin`; loaded synchronously before Alpine in `base.html`).
- **Styles:** `frontend/static/style.css` (`/* == G5a component framework == */`).
- **Nav shell:** `base.html` (5-group sidebar; not a macro — it is the shell).

**Isolation:** no external URLs (I7 CDN gate scans `components.js`/`components.html`).
**Single score (L8):** `score_chip` renders **System Score** only. There is no
"Signal Score" anywhere. **Export (L7):** `export_button` is flag-gated, DEFAULT
OFF. **Two-state (L3/L9):** `two_state_panel` renders an honest UNAVAILABLE state,
never a fabricated value.

## How a G5b screen consumes them

```jinja
{% extends "base.html" %}
{% from "components.html" import data_table, filter_bar, kpi_row, kpi_card, export_button %}
{% block nav_orders %}active{% endblock %}
{% block content %}
<div x-data="ordersPage()" x-init="load()" @ops-refresh.window="load()">
  {% call kpi_row() %}
    {{ kpi_card("Orders", "data.count", tone="info") }}
  {% endcall %}
  {{ filter_bar([{"key":"leg","label":"Leg","type":"select","options":["ENTRY","SL","TGT"]},
                 {"key":"symbol","label":"Symbol"}]) }}
  {{ export_button("Export Orders") }}
  {{ data_table("orders", [
       {"key":"order_id","label":"Order","kind":"mono"},
       {"key":"status","label":"Status"},
       {"key":"qty_filled","label":"Filled","kind":"num"},
       {"key":"placed_at","label":"Placed","kind":"ts"}],
       rows_expr="data.rows") }}
</div>
<script>
function ordersPage() {
  return Object.assign(
    pageBase("/api/orders"),
    tableMixin({ defaultSize: 100 }),
    filterMixin([{ key: "leg" }, { key: "symbol" }]),
    { qs() { return this.fQuery(); } }        // FilterBar drives the endpoint query
  );
}
</script>
{% endblock %}
```

## Component contracts

| # | Component (macro) | Mixin | Contract |
|---|---|---|---|
| 1 | `data_table(id, columns, rows_expr, empty_text)` | `tableMixin({defaultSize,pageSizes,sortKey,sortDir})` | `columns` = `[{key,label,sortable=True,kind}]`, `kind ∈ text\|num\|money\|mono\|ts\|date`. Renders sortable ▲▼ headers, `tCell`-formatted cells, empty row, pager + rows-per-page (`50/100/200/500`). Rows come from the page's `rows_expr`. |
| 2 | `filter_bar(filters, on_change)` | `filterMixin([{key,default}])` | `filters` = `[{key,label,type(text\|select\|date),options?,placeholder?}]`; writes `fVals[key]`, calls `on_change` (default `load()`); `Reset` clears + reloads. Screen reads `fQuery()`. |
| 3 | `kpi_row()` / `kpi_card(label, value_expr, tone, href, unit)` | — | `kpi_row` wraps `{% call %}`'d cards. `tone ∈ info\|pos\|neg\|warn`; optional `href` (clickable KPI); `value_expr` is an Alpine expr. |
| 4 | `period_selector()` | `periodMixin(default)` | Today/Week/Month/Custom → `pPeriod` + `pFrom/pTo`; screen reads `pQuery()`. **G5a emits only**; screens wire it onto their endpoint in G5b+. |
| 5 | `events_alerts_panel(events_expr, alerts_expr, title)` | — | Merged Recent-Events + Alerts (L10). `alerts_expr` = `/api/alerts.alerts`; `events_expr` = `recent_events`. Severity chips. Render-only in G5a. |
| 6 | `two_state_panel(available_expr, reason)` | — | `{% call %}` slot rendered when `available_expr` truthy; else honest UNAVAILABLE state (default reason "Pending Broker Source (G4/P1)"). Never fabricates (L3/L9). |
| 7 | `export_button(label, target)` | — (reads `gui_flags`) | Flag-gated by `gui_flags.table_export_enabled` (context processor; DEFAULT OFF). OFF → `disabled` + tooltip. NO export logic wired in G5a (L7). |
| 8 | `score_chip(value_expr, label)` | — | **System Score only** (screener_results.score). No "Signal Score" (L8). |
| 9 | `medal(rank_expr)` | — | 🥇🥈🥉 for ranks 1-3, else `#n`. |
| 10 | `status_chip(state_expr, prefix)` | — | Standardized state pill; CSS class = `{prefix}-{STATE}`. |
| 11 | `funnel_pipeline_card(stages_expr)` | — | Renders the given stages 1:1 (reuses `.pcard`). The **13-stage** guarantee lives in `/api/pipeline` (UNTOUCHED in G5a); this macro is count-agnostic. |
| 12 | `lifecycle_timeline(steps_expr)` | — | Vertical timeline; `steps` = `[{label,ts,state}]`. Render-only in G5a. |
| 13 | **NavShell** (in `base.html`) | — | 5-group sidebar (Dashboard landing · Trading · Analytics · Operations · Investigation); `{% block nav_* %}active{% endblock %}` per existing route; not-yet-built screens render as dimmed `nv-soon` placeholders (no `href`, no 404); collapses to icon-only ≤900px. |

## What G5b can now assume
- These 13 components exist, are styled, tested render-only, and stable.
- The 5-group nav is live; a new screen adds its route in `app.py:_PAGES` + a
  template `{% extends "base.html" %}` and flips its `nv-soon` placeholder to a
  real `{% block nav_* %}active{% endblock %}` link.
- `gui_flags` (`table_export_enabled`, `reports_download_enabled`) is available
  in every template context (default False).
- Global table standard (sort/rows-per-page/common-column-order/≥13px) is the
  `data_table` default — apply per-screen in its own sub-phase (no half-converted
  screens: a screen keeps its current table until its sub-phase converts it).
