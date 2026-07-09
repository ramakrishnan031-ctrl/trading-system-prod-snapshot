# Screen 03 — Strategies (Strategy Control Tower) · Asset Requirement Specification

**Role:** Asset Manager (spec only — NO implementation in this step)
**Source spec:** `gui/03. Strategies.txt` + `gui/03. Strategies.png` (approved mockup)
**Asset library root:** `ops_dashboard/assets/`
**Precedent:** follows the Screen-01/02 asset policy and the same self-service inline-SVG
approach that Screen-02's KPI/pipeline/event icons already use in production.

---

## Verdict: genuinely missing assets = **ZERO**

Nothing on this screen requires a ChatGPT-generated (photographic/illustrative) asset.
Every visual is one of: (a) an existing production asset reused, (b) a small line/solid
**icon rendered as inline SVG** (self-served, no raster file — identical method to Screen-02's
`DASH_ICONS`/`EV_ICONS` maps already live in `dashboard.html`), (c) a **CSS** treatment
(status pills, usage bars, borders), or (d) **data** already returned by an existing API.

**→ There is nothing to stop and wait for. But per the agreed workflow I am STOPPING here and
NOT implementing** until you confirm this spec (and there is no asset-generation round to run,
since the missing-asset count is zero).

---

## Element-by-element inventory

| Mockup element | How it's satisfied | New asset? |
|---|---|---|
| Sidebar eagle brand mark + "AlgoCore" | **Reuse** `assets/branding/logos/algocore-mark.web.webp` (Screen-01/02 production asset) | No |
| Header status strip (TRADER/MODE/PHASE/KILL pills, Date, Time, LIVE badge, Log out) | **Reuse** the shared `base.html` summary-bar (already built for Screen-02) | No |
| Title "STRATEGIES" + "Strategy Control Tower" | Text/CSS | No |
| **8 Summary KPI cards** (Total Strategies, Active, Quiet, Silent, Total Signals, Total Trades, Total P&L, Win Rate) each with a tinted corner icon | Inline SVG icons (people, up-arrow, list, alert, plus-circle, trend, receipt, gauge) — same `.kc`/icon pattern as Screen-02's 12-card deck; P&L + Win Rate sparklines drawn from data | No |
| **Filters row** (Date Range w/ calendar glyph, Strategy, Scanner, Status, Trade Type, Direction, Reset, Apply) | Native `<select>`/`<input type=date>` + CSS; calendar/chevron are inline SVG or native | No |
| **Strategy Hierarchy** cards (First Pullback → Long/Short, VWAP Rejection, Breakout, Opening Range, "…and more") | CSS cards + a small inline-SVG strategy glyph; Long=green / Short=red via tokens | No |
| **Main sortable table** (15 cols) with per-row strategy glyph, sort arrows ▲▼, Usage% mini-bar, Status pill (ACTIVE/QUIET/SILENT) | Reuse the existing `data_table` component pattern + CSS (`cap-bar` style usage bars, status pills); sort arrows & row glyph are inline SVG/text | No |
| **Export XLSX** button (with icon) | Existing `export_button` macro (flag-gated) + inline SVG | No |
| **Quick Actions & Insights** strip (Strategy Details, Signals, Orders, Trades, Performance, Capital & Risk, Logs) | CSS cards + inline-SVG icons (magnifier, signal, receipt, download, trend, shield, document) | No |
| Status-logic legend footer | Text/CSS | No |

## Data readiness (implementation note, not an asset)
The 15 table columns + 8 KPIs map to the **existing** `/api/strategies`
(`services/strategy_tower.py`) rows, which already expose: strategy name, scanner set,
signals (received/accepted), orders (created/filled → Success %), trades (wins/losses →
Win %), `net_pnl`, capital `allocated/used/remaining` + `usage %`, `last_signal`,
`last_trade`, and the `silence` tier (→ Status ACTIVE/QUIET/SILENT). No new route/API/backend
is anticipated. The filter dropdowns (Scanner, Trade Type, Direction) will be assessed against
existing fields at implementation time; any genuine gap will be raised then (it is a data
question, not an asset).

## Production asset policy (reconfirmed)
No new master or optimized raster is introduced. Inline SVG icons ship in the template markup
(zero files). Only the already-committed `algocore-mark.web.webp` is reused. Master PNG/JPG
remain git-ignored (`assets/**/*.png|jpg|jpeg`).

---

**Next step (awaiting your go-ahead):** since zero assets need generation, on your confirmation
I proceed directly to the in-place Screen-03 implementation (routes/APIs/backend unchanged),
matching `gui/03. Strategies.png`.
