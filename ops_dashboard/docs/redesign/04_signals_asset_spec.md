# Screen 04 — Signals (Birthplace of every trade) · Asset + Implementation Record

**Source spec:** `gui/04. Signals.txt` + `gui/04. Signals.png` (approved mockup)
**Asset library root:** `ops_dashboard/assets/`
**Precedent:** follows the Screen-01/02/03 asset policy — inline SVG, CSS, and data from
existing APIs; no new raster assets.

---

## Verdict: genuinely missing assets = **ZERO**

Every visual is (a) an existing production asset reused, (b) a small line icon rendered as
**inline SVG** (`SIG_ICONS`, same self-serve method as Screen-02's `DASH_ICONS` and Screen-03's
`STRAT_ICONS`), (c) a **CSS** treatment (pills, dots, timeline, age chips), or (d) **data**
already returned by an existing API.

| Mockup element | How it is satisfied | New asset? |
|---|---|---|
| Sidebar mark + "AlgoCore" | Reuse `base.html` sidebar (Screen-01/02 production asset) | No |
| Header status strip | Reuse `base.html` summary-bar — **untouched** | No |
| Title "Signals" + "Birthplace of every trade" | Text/CSS | No |
| 9 lifecycle KPI cards | `.kc` deck + 9 inline-SVG icons; two new tones (`kc-muted`, `kc-purple`) | No |
| Filters (8 controls + Reset/Apply) | Native `<select>`/`<input type=date>` + `.flt-*` | No |
| Sortable 15-column table with ▲▼ | `tableMixin` + `.st-*` (restated for `.sig-page`) | No |
| Trade Type / Status / Trade Result pills | CSS only | No |
| Rows-per-page 50/100/200/500 (default 200) | `tableMixin({defaultSize: 200})` | No |
| Export XLSX | Existing `export_button` macro (flag-gated, still OFF) | No |
| Signal Details rail + lifecycle timeline | CSS + `.lifecycle`/`.lc-*` (reused from components) | No |
| Signal Age / Status / Result legends | CSS dots + pills | No |

---

## The two scores — L8 RESTORED 13-Aug-2026; the 11-Aug supersession is CLOSED

> ### ⚠️ CURRENT, BINDING — Rama, 13-Aug-2026, system-wide
>
> | Column | Meaning | Real source |
> |---|---|---|
> | **System Score** | the **ACHIEVED** score | `screener_results.score` |
> | **Score Threshold** | the minimum required for eligibility | `screener_results.eligible_score`, falling back to config `min_pass_score` |
>
> ⛔ **"Signal Score" is RETIRED** — as a label *and* as a payload key. ⛔ A threshold is
> never presented as a score under any name. His words: *"If a screen has no genuine second
> score, omit 'Signal Score' rather than fabricate/relabel a threshold… Maintain semantic
> label consistency across the entire system."*
>
> This **reverts the 11-Aug supersession below and restores L8**, and it applies to Screens
> 04, 05, 06 and everything built after them.
>
> ⭐ **Why it is a restoration, not a new rule:** the app already carried *both* meanings of
> "System Score" simultaneously — `db_reader.screener_scores()` has always returned the
> ACHIEVED score under that label for analytics/operations/trade_explorer/trade_logs, while
> `db_reader.signal_scores()` returned the THRESHOLD under it for Screens 04/05/06. One
> label, two quantities, one application.
>
> 🔴 **A real defect went with it:** the min_pass fallback was applied to `system_score`, so a
> signal with no stored `eligible_score` printed the **configured threshold** in a per-signal
> score column. The fallback now belongs to `score_threshold` alone, pinned by
> `test_score_threshold_falls_back_to_config_but_system_score_never_does`.
>
> ⚠️ **And the test that claimed to pin the old mapping was VACUOUS:** neither fixture's
> `screener_results` carries the v14 `eligible_score` column, so the old
> `assert system_score in (82, None)` could only ever see `None`. The rebuilt test adds the
> column so both quantities are real and the assertion can genuinely go red.
>
> **Guard:** `conftest.assert_signal_score_label_is_retired()` — `SIGNAL_SCORE_ALLOWED_FILES`
> is now **EMPTY**, so it fails on *any* file. Kept as a set, ⛔ not deleted, so a future
> widening is again an explicit act. Four tests (`g5a`/`g5b`/`g5c`/`g5d`) call it.

### SUPERSEDED — the 11-Aug-2026 decision, retained because it was real

**L8** (`docs/G5_REDESIGN_PHASE_B.md`) locked *"single System Score = `screener_results.score`,
drop 'Signal Score'"*. **Rama superseded L8 for Screen-04 on 11-Aug-2026**, and redefined both
terms — they were two different quantities, not a rename:

| Column | Meaning (⛔ NO LONGER IN FORCE) | Real source |
|---|---|---|
| **System Score** | the minimum score required for eligibility | `screener_results.eligible_score`, falling back to config `min_pass_score` |
| **Signal Score** | this signal's own score | `screener_results.score` |

⛔ **Nothing was fabricated** under that scheme either; where neither source existed the cell
rendered `—`. ⚠️ **One supporting claim did not survive checking:** the note that a signal
scoring **64** against a required **70** *"is exactly the row whose `rejection_reason` reads
'Signal Score Too Low'"* describes a **test fixture**, ⛔ not production — live reject reasons
are `REJECTED_SCORE_<n>` (measured 13-Aug: `REJECTED_SCORE_57` ×35,411, `REJECTED_SCORE_59`
×15,123), and the phrase *"Signal Score"* appears in **no** Python outside `ops_dashboard/`.
⇒ retiring the label creates **no** backend/UI mismatch.

---

## Data readiness — every column mapped to a real column

| UI column | Source |
|---|---|
| Trading Date · Time · Signal Age | `signals.received_at` (+ `expires_at`) |
| Symbol · Strategy · Scanner · Status · Reject Reason | `signals` |
| **Trade Type** | `orders.product` on the `ENTRY` leg — ⚠️ **there is no `trades.product`** |
| Direction · Trade Result · Trade Duration | `trades` via `trades.signal_id` |
| System / Signal / Reject / Required Score | `screener_results` (+ config fallback) |
| KPI deck (9 counts) | `signal_kpi_counts()` — whole-day SQL, **not** the capped row list |

### Honest gaps (stated, never filled with invention)
1. **Validated / Risk Passed / Capital Passed have no persisted timestamps (G-2).** The
   timeline shows them as *not captured*, matching `build_trade_story`'s existing rule, with a
   note under the timeline.
2. **A trade whose `ENTRY` order row is missing yields `product NULL`** → Trade Type renders
   `—`, never defaulting to Intraday. Pinned by a test.
3. **The row list is capped at 500** (`_LIST_CAP`). When the day exceeds it the screen says so
   in-line rather than showing a partial day silently; the KPI counts stay whole-day.

---

## Verification performed

- **Backend:** `tests/test_screen04_signals.py` — 8 tests × 2 schema variants = **16**, each
  seeding its own signal→order→trade→screener chain. The shared fixture seeds **no** row with a
  trade or score, so without these the new joins were entirely unexercised.
- **Negative controls (a green that could have been red):** inverting the `CNC→Delivery`
  mapping turned the Trade Type test red; planting *"Signal Score"* in `orders.html` turned the
  L8 guard red. Both restored, md5-verified.
- **Suite:** **396 passed** (380 before + 16 new).
- **Visual:** rendered in headless Edge against a seeded temp DB at **1920×1200** (all 15
  columns fit) and **1600×1180** (table scrolls horizontally inside `.tbl-scroll`; the KPI deck
  stays 9-across down to 1360px).
- **Line endings:** CR count 0 on every edited file.

## Amendments — Rama, 11-Aug-2026 (after first visual review)

1. **Scanner removed from Screen-04 entirely.** Strategy and Scanner carry the same data in this
   system, so the filter, the table column and the Signal-Details row were all removed. ⚠️ This
   *reverses* the earlier "do not remove Scanner" instruction, which applied before that
   duplication was established. **The backend is unchanged** — `/api/signals` still returns
   `scanner` (the existing contract test asserts it), it is simply no longer surfaced.
   The sidebar link to the **separate Scanner Attribution screen stays** — the TXT specifies it
   as its own screen.
2. **Default column order fixed at 14 columns** (Strategy now precedes Symbol):
   `Trading Date · Time · Strategy · Symbol · Trade Type · Direction · System Score ·
   Signal Score · Status · Reject Reason · Reject Score · Required Score · Trade Result ·
   Trade Duration`.
3. **Top spacing** — `.pg-title` gained a 12px top margin, putting the title on the same
   12/14/16px rhythm as the other screens instead of sitting hard against the summary bar.
4. **Draggable column headings.** Header *and* body cells now iterate the **same `cols` array**,
   so a column's data cannot drift from its heading — that coupling is structural, not a
   convention. Order persists in `localStorage` (`screen04.signals.colOrder.v1`) and is rebuilt
   from the current definitions on load, so a stale entry can neither hide a column nor
   resurrect a removed one. A "Reset column order" control appears only when the order differs
   from default. Sorting is keyed on `c.key` and is unaffected by position; a drag is suppressed
   from firing a sort click.

**Reorder contract checks** (16, run in Node against the page's real JS — they can go red):
default order · Scanner absent · drop lands at the target index · no column lost or duplicated ·
sort still works and flips · order persists and restores · reset clears storage · stale order
still yields all 14 · a removed column cannot return · corrupt storage falls back to default ·
post-drop click does not sort.

⚠️ **Not automated:** actual mouse drag-and-drop in a browser (no driver installed). The
reorder *logic* is covered by the checks above; the *gesture* needs a human once.

## Deviations from the mockup, and why

1. **Header order follows the global rule, not the PNG** — LEFT `Trader/Mode/Kill/Phase/Broker
   ID/Client Name`, RIGHT `Trading Date/Current Time/Log out`. `base.html` already implemented
   this; `Broker ID`/`Client Name` bind to `summary.account_id`/`summary.client_name`, never
   hard-coded. **No header change was needed or made.**
2. **Rows-per-page reads 50 / 100 / 200 / 500.** The PNG shows "50 100 200 200"; the TXT says
   500, and `tableMixin`'s default set is already `[50,100,200,500]`.
3. **Column headers wrap to two lines** (e.g. "System / Score"). That is what keeps 15 columns
   inside the viewport while data text stays at the 13px floor the spec requires.
4. **Scanner is retained** as both filter and column (Rama, 11-Aug) — the Screen-03 removal was
   specific to Strategies, where Strategy and Scanner duplicated each other.
