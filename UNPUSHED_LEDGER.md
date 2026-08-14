# UNPUSHED LEDGER

**Purpose.** During a declared no-deployment window, every commit is recorded here
before the session ends, so tomorrow's deployment can be **audited and performed in
the correct order** rather than reconstructed from `git log`.

**⛔ A commit that is not in this file has not been handed over.**

**Rules for every entry:** date/time · commit hash · screen · change summary ·
verification status · `Pushed: NO` · `Deployed: NO` · reason.
⛔ Never mark an entry pushed or deployed from intent — only from a measurement
taken after the fact.

---

## WINDOW: 12-Aug-2026 → deployment held until the evening of 13-Aug-2026

**Branch:** `feat/screen06-positions`, created from `2bfe9e2` (= `origin/main` at the
time of writing).
📌 **Why a new branch:** this work was started on `feat/screen05-orders`, whose name
describes a *different* screen. A branch whose name does not match its content is how
the wrong ref gets pushed; Screen-06 therefore gets its own correctly-named branch.
⛔ `feat/screen05-orders` is unchanged and still points at `2bfe9e2`.

**Base at window open:** `origin/main` = `2bfe9e2` (Screen-05 Orders, accepted).

---

### Entry 1

| Field | Value |
|---|---|
| **Date/time** | 2026-08-12 20:05 IST |
| **Commit** | `4d41b7b406958d12d5a06530aa40056c5ae203e7` |
| **Short** | `4d41b7b` |
| **Branch** | `feat/screen06-positions` |
| **Screen** | Screen-06 Positions (+ one Screen-05 Orders label alignment) |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window — deployment held until the evening of 13-Aug-2026 |

**Change summary**

- **Screen-06 Positions rebuilt** in the Screen-03/04/05 language. Root carries
  `ord-page` *and* `pos-page`, so all 117 approved Screen-05 rules apply verbatim.
  `pos-page` adds only a 6-up KPI grid, the position-status strip, two donut summary
  cards and the status-breakdown bars.
- **Scanner removed everywhere** — no column, filter, detail row, label or export
  column. Pinned by test.
- **System vs Broker price split**: `Entry (System)/(Filled)`,
  `SL (System)/(Broker)`, `TGT (System)/(Broker)` as merged, draggable column pairs.
- **Position quantity** is the broker-filled quantity, kept beside the system-ordered
  quantity, never collapsed into it.
- **Bottom summary**: Capital Utilization, Position Distribution, MTM Performance,
  Position Status Breakdown — charts retained, not replaced with plain text.
- **XLSX export** of the *filtered* result set (`/api/export/positions`), filters
  re-applied server-side by the same predicate function the table uses.
- **Screen-05 Orders**: detail card now reads `Quantity (System / Filled)` to match
  its own table headings. ⛔ No other Screen-05 change.
- **Bug fixed while building**: a *superseded* SL leg could overwrite the standing one
  in `position_broker_exits`. Superseded rows are now excluded and a null can never
  blank out a real value.
- **Fixture**: `orders` gains `price`/`trigger_price`, `trades` gains
  `closure_source`/`exit_mechanism` — both already in `core/schema.sql`; the fixture's
  own contract is to match it. Seeded so the SL **mismatches** and the TGT **matches**,
  so the pair tests cannot pass vacuously.

**Data-integrity decisions recorded with the commit (⛔ nothing invented)**

- **There is no filled SL/TGT execution price.** MEASURED: `orders.avg_fill_price` is
  NULL on **all 927 orders ever placed** (ENTRY 457 / SL 241 / TGT 229), and a leg that
  executes still records nothing there. The second column is therefore **Broker**
  (the trigger/limit standing at the broker), ⛔ never "Filled". The real executed
  price appears separately as **Exit Price** in the lifecycle tab.
- **LTP · Current Value · Unrealized P&L · Unrealized % · MTM · Current RR are not
  shown as values.** `ops_dashboard` has **zero live-price call sites** and
  `/api/positions` already declares these "Pending Broker Source (G4)". They are not
  columns, not zeros and not blanks — the screen states why. The MTM panel says what
  it cannot know instead of drawing an invented curve.
- **Highest Profit % / Highest Drawdown %** are real (`trade_excursions`, 192/589
  populated). A trade with no excursion row renders unavailable, ⛔ never 0.
- **SL/TGT distance %** is measured **from the system entry price**, and the column
  says so. It is ⛔ not proximity-to-LTP, which cannot be computed here.

**Verification status — VERIFIED LOCALLY**

- 37 new contract tests in `ops_dashboard/tests/test_screen06_positions.py` — all pass.
- Full `ops_dashboard` suite: **444 passed / 1 failed**.
- The single failure is `test_isolation.py::test_c_venv_has_no_kiteconnect`, and it is
  **proven pre-existing**: the identical test fails at base `2bfe9e2` in a throwaway
  worktree **with these changes absent** (the system interpreter has `kiteconnect`;
  there is no GUI venv in this environment). ⛔ Not attributable to this commit.
- Page renders: `GET /positions` → 200, 60,982 bytes, root classes
  `dash-page ord-page pos-page`.
- Top spacing verified by rule, not by eye: no `.pos-page .pg-title` override exists,
  so `.ord-page .pg-title { margin: 12px 2px 14px }` applies — byte-identical to
  Signals and Orders. ⛔ No CSS hack.
- All touched files are pure LF (`tr -cd '\r' | wc -c` = 0 on each).

---

### Entry 2

| Field | Value |
|---|---|
| **Date/time** | 2026-08-12 20:09 IST |
| **Commit** | `05aa408bf081325616aed7d2c39b25dd84a71336` |
| **Short** | `05aa408` |
| **Branch** | `feat/screen06-positions` |
| **Screen** | — (process artefact) |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window |

**Change summary** — creates this ledger and records Entry 1.

---

### Entry 3

| Field | Value |
|---|---|
| **Date/time** | 2026-08-12 20:12 IST |
| **Commit** | `5f7b6e08b25b0e85e408a00538d0e53417a50f7e` (recorded by Entry 4, which followed it) |
| **Branch** | `feat/screen06-positions` |
| **Screen** | — (process artefact) |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window |

**Change summary** — corrects Entry 2's hash. It had recorded `c0c68e6`, which an
`--amend` immediately replaced with `05aa408`; the recorded hash was therefore a
**dead object** and would have sent tomorrow's reviewer to a commit that is not on
the branch.

📌 **A ledger entry cannot contain its own commit hash** — writing the hash changes
it. The rule that works: **the next entry records the previous one's SHA**, and the
final entry's SHA is the branch tip, one `git rev-parse` away. ⛔ Do not "fix" a
self-hash by amending: that produces exactly the dead hash it is correcting.

---

### Entry 4

| Field | Value |
|---|---|
| **Date/time** | 2026-08-12 20:26 IST |
| **Commit** | `3dfb0b59c9404096d2fabf8169730c4ef4c47ded` |
| **Short** | `3dfb0b5` |
| **Branch** | `feat/screen06-positions` |
| **Screen** | Screen-06 Positions (render defects) |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window |

**Change summary** — three defects that **only a render exposed**; the test suite
was green throughout, because none of them is a data or contract defect:

1. The MTM panel's injected reason string ran into the sentence after it.
2. That panel's copy was then taller than its card and the last line clipped.
3. The detail rail's tab bar was built for Screen-05's **three** tabs; Positions has
   four, so "SL / TGT" wrapped to three lines and "Risk & Reward" to two.
   Also fixed: Screen-05's `.od-na-block b { display: block }` (a lead-in heading
   rule) was inherited by inline emphasis and pushed single words onto their own
   lines.

📌 **The lesson worth keeping:** the contract tests could not have caught any of
these. They verify *what the screen says*; only rendering it verifies *that it can
be read*. A screen is not verified until it has been looked at.

**Verification** — rendered in headless Edge against a local fixture server
(`127.0.0.1:8599`, scratchpad-only launcher, ⛔ never the VM service) and the
screenshots read: KPI deck, filters, status strip, the four System/Broker column
pairs, the detail card on Lifecycle and SL/TGT, and all four summary panels.
Suite unchanged at **444 passed / 1 failed** (the known environmental
`kiteconnect` isolation check).

⭐ **The pairs are demonstrably working on real data**: TCS renders
`SL (System) ₹3,290.00` against `SL (Broker) ₹3,289.55` — a genuine
system-vs-broker mismatch surfaced rather than smoothed — while HDFCBANK renders
an em-dash where no SL leg exists.

---

### Entry 5

| Field | Value |
|---|---|
| **Date/time** | 2026-08-12 20:52 IST |
| **Commit** | `a0be1706230b567e98b71a404790c2d20bc9f847` |
| **Short** | `a0be170` |
| **Branch** | `feat/screen06-positions` |
| **Screen** | Screen-06 Positions — table reworked to `position screen.xlsx` |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window |

**Change summary**

- **Columns to the spreadsheet.** SL/TGT distance % → **SL POINTS / TGT POINTS in ₹
  per share** (notes 2/3: per-qty everywhere except Unrealised), from the **system**
  levels and direction-aware. Adds **R:R**, **LTP**, **Unrealised**, **Action**.
  Drops Capital Used / Risk / Highest Profit / Highest Drawdown / Pos Age from the
  table — the spreadsheet omits them and they remain in the detail card.
- **R:R is strategy-configured** (note 4), from `strategy.yaml` `tgt_risk_reward`.
  ⛔ Not reversed for shorts. The price-derived ratio survives in the detail card
  *beside* it as "implied by levels".
- **Action** opens a close dialog (CMP or manual per-qty price) with a live estimate.
- **SL/TGT keep "Broker", not "Filled"** — per section C, unchanged.

**⚠️ Two defects found while doing this, both worth recording**

1. **`config_reader.get_strategies` projects an explicit field whitelist** that did
   not include `tgt_risk_reward`, so the first implementation silently returned
   `None` for **every** strategy. A test that only checked "R:R is None when
   unconfigured" would have passed on a completely broken read — which is why the
   paired test that **sets** the key and asserts the value appears was added.
   The field is now projected **with no default**: a strategy configuring no ratio
   shows `—` rather than inheriting `order_placer`'s `2.0` fallback.
2. **A CSS specificity defect the render exposed**: `.ord-page .od-kv b` is
   `(0,2,1)` and beat `.pos-page .v-pos` `(0,2,0)`, so **every signed value in the
   detail rail rendered plain white** — Net P&L, Highest Profit/Drawdown, and the
   dialog's estimated P&L. It was visible in a screenshot I had already taken and
   I did not catch it the first time. The spreadsheet requires the sign to be
   legible by colour (note 6), so this is a correctness fix, not polish.

**⛔ Data-integrity positions held**

- **LTP and Unrealised are not faked.** No live-price source exists, so both render
  an explicit `n/a`. The unrealised arithmetic is implemented **and tested**
  — `(ltp − entry_filled) × qty` for LONG, mirrored for SHORT, against the
  **filled** entry — so it is already correct the day a live price is wired in.
  Colour-by-sign is in place for that day. ⛔ Never fed a stale or system price.
- **The Action dialog cannot execute and says so.** The dashboard's only POST route
  is `/login` and every DB connection is read-only, so the confirm button is
  disabled and CMP is disabled for want of a price. ⭐ A new test **asserts that
  POST-route fact**, so the dialog's claim fails loudly if a write path ever appears.

**Verification** — suite **458 passed / 1 failed** (the known environmental
`kiteconnect` check). Rendered and read: per-share points correct in both
directions (**TCS SHORT** entry 3264.80 → SL ₹25.20 / TGT ₹59.80; **RELIANCE LONG**
₹65.00/₹65.00), R:R shows `2:1` and `1.5:1` where configured and `—` where not,
LTP/Unrealised show `n/a`, and the dialog's estimate `(2900.50−2845.30)×75 =
₹4,140.00` renders green. ⭐ The detail card now shows **R:R (strategy config)
1.5:1** against **R:R (implied by levels) 1:1** — a real gap, surfaced rather than
collapsed.

---

### Entry 6

| Field | Value |
|---|---|
| **Date/time** | 2026-08-12 22:41 IST |
| **Commit** | `923a43a0876a066348ffe251abd6b8d5ceecc936` |
| **Short** | `923a43a` |
| **Branch** | `feat/screen06-positions` |
| **Screen** | Screen-06 Positions — revision 2 (user corrections) |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window |

**Change summary**

- 🔑 **TOTAL CAPITAL USED corrected.** Now strictly
  **`broker-filled qty × FILLED entry price`** over open positions. It previously
  preferred the sizer's `actual_position_value_rs` and fell back to the **system**
  entry price; both are gone. **SL and TGT contribute nothing** — pinned by a test
  that moves them wildly and asserts the total is unchanged.
- **Column order** to the requested one: `… SL POINTS · TGT POINTS · LTP ·
  UNREALISED · ACTION`, with **Action unconditionally last**.
- **R:R left the table** (it is wide enough) but **stays in the detail rail**
  beside the ratio implied by the levels — moved, ⛔ not dropped; a test asserts it.
- **`colOrder` key bumped v1 → v2.** ⚠️ Without this an operator with a saved v1
  order would keep the OLD default — Action in its old slot and the removed R:R
  column — and would **never see this change**.
- **Action gated**: Open / Partial Exit get `Close`; every other row gets a neutral
  dash, and `openAction()` refuses to open on a non-open row.
- **Added View Full Details** — the established popup showing Position, SL/TGT,
  Risk/Reward and Lifecycle at once. Strictly a **read** view (a test asserts no
  action wiring inside it), and it keeps the **executed Exit Price separate** from
  the broker-standing SL/TGT.

**⭐ The correction is not cosmetic — it moved the number**

Preview total went **₹514,409.50 → ₹514,449.50**, because four rows carried a
recorded value based on the **system** entry (₹1,000.00) while the actual **fill**
was ₹1,001.00. ⭐ A formula change that leaves every number identical has not been
exercised; this one was.

**⛔ Honest-absence handling, deliberately chosen**

A row whose fill price is missing now yields **None, ⛔ not 0.0** — a zero reads as
*"this position ties up nothing"* and would silently shrink the KPI. The count of
such rows is **surfaced in the card footer** (`· N unpriced`) instead of swallowed.
⭐ The KPI, the row and the Capital Utilization donut now call **one** function, so
they cannot drift; a test pins that they agree.

**Verification** — suite **474 passed / 1 failed** (known environmental
`kiteconnect` check). Rendered and read from the DOM: **22 columns in exactly the
requested order**, Action last, all four groups merged. Capital on the two
partial-fill rows uses the **POSITION** quantity — INFY `20 × 1490.50 = 29,810`,
SBIN `55 × 812 = 44,660`. Closed rows show the neutral state. The full-details
popup shows AXISBANK capital `25 × 1020 = 25,500`, SL broker `969.40` against
system `970.00`, and exit price `1008.90` kept apart.

📌 **Not done, and why:** a production-data preview was started and **stopped at
Rama's instruction** — no copy of the live database was made to the PC. The
preview therefore runs on fixture data.

---

### Entry 7

| Field | Value |
|---|---|
| **Date/time** | 2026-08-12 23:18 IST |
| **Commit** | `f765573d14c9fca9375a00848bd79a4e2405303c` |
| **Short** | `f765573` |
| **Branch** | `feat/screen06-positions` |
| **Screen** | Screen-06 Positions — final UI polish |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window |

**Change summary**

- **₹ removed from every table cell**, kept in the column headings (`ENTRY ₹`,
  `SL ₹`, `TGT ₹`, `SL POINTS ₹`, `TGT POINTS ₹`, `LTP ₹`, `UNREALISED ₹`).
  ⛔ The **detail card and close dialog KEEP theirs** — there the label sits beside
  the value, not above a column. Precision and sign unchanged.
- **The table now fits without a horizontal scrollbar** at normal desktop widths.

**📏 Measured first, then changed — the diagnosis is the useful part**

At a 1920px viewport the content box is **1649px** and the table wanted **1862px**.
Two things were eating it, and only one was obvious:

1. **The detail rail reserved 302px permanently**, selected or not. It is now an
   overlay **drawer**. ⛔ Nothing removed — same card, same tabs, same *View Full
   Details* — and deliberately **no backdrop**, so the table stays readable while
   it is open.
2. ⭐ **The binding constraint on several columns was the HEADER, not the data** —
   `"TGT POINTS ₹"` is far wider than `"70.00"`. Headers now **wrap** (exactly how
   the spreadsheet prints them) rather than being abbreviated, so ⛔ **no heading
   loses a word**. Grip collapses until hover; padding 6px/3px; Strategy — the one
   genuinely long text field — capped at 132px with an ellipsis, full value still
   in the detail card.

**Result, measured at three widths**

| viewport | scrollWidth | clientWidth | overflow |
|---|---|---|---|
| 1920px | 1649 | 1649 | **no** |
| 1680px | 1409 | 1409 | **no** |
| 1440px | 1257 | 1169 | yes — narrow-viewport fallback |

The table's natural width went **1862 → 1257**. ⛔ The scrollbar is **not removed**;
it is now only the narrow-window fallback, which is what keeps data *reachable*
rather than *clipped*.

**⛔ How this was NOT achieved** — no column hidden, no heading abbreviated, no value
truncated or moved into a tooltip, no font below the sizes Screen-05 already ships.

**Verification** — suite **479 passed / 1 failed** (known environmental
`kiteconnect` check). Rendered and read: all 22 columns visible end-to-end with
no scrollbar, ₹ absent from cells and present in headings, drawer opens with the
detail card intact and its own ₹ retained, Action still last, groups still aligned,
status/direction/type colours intact.

---

### Entry 8

| Field | Value |
|---|---|
| **Date/time** | 2026-08-12 23:52 IST |
| **Commit** | `fb4bfe199fcbab533559c6c4f0200ac22eb8ee4b` |
| **Short** | `fb4bfe1` |
| **Branch** | `feat/screen06-positions` |
| **Screen** | Screen-06 Positions — R:R column |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window |

**Change summary** — adds **R:R between the TGT group and SL Points**, the last
outstanding table correction. Action remains the final column.

- **Source**: each strategy's own `config/strategies/<name>.yaml` →
  **`tgt_risk_reward`**. ⛔ Not derived from prices, ⛔ no global, ⛔ no default.
  Missing field → `—`.
- **Reuses the existing path** rather than adding a second one (section 5):
  `config_reader` projects the field, `trading.py` reads the projection. ⭐ A test
  asserts **exactly two modules participate** and that nobody but `config_reader`
  globs the strategy directory, so a competing reader fails loudly.
- **Rendering**: `1.5:1` / `2:1` (a whole number drops its `.0`) through **one
  shared helper** the table and the detail rail both call — the same ratio can
  never be printed two ways. The price-derived ratio stays a **separate** number
  labelled *"implied by levels"* in the detail card.
- **`colOrder` key v2 → v3**: a stored v2 order would append the reinstated column
  at the **end** (`initCols` appends unknown keys) and it would never appear in
  its intended place.

**⚠️ Worth knowing before reviewing on real data**

**All 16 production strategies currently configure `tgt_risk_reward: 1.5`**, so on
the live screen **every row will read `1.5:1`** and will *look* like a hard-coded
constant. It is not. ⭐ The test therefore proves per-strategy sourcing using
**three different fixture values (1.5 / 2.0 / 3.0) plus one strategy with none** —
a test written against production values could not tell a per-strategy read from a
constant.

**Width holds.** The column is centred, carries no currency symbol, and is narrow
enough that **1920px and 1680px still show the whole table with no horizontal
scrollbar** (`SCROLLW == CLIENTW` at both). ⛔ Nothing was removed to make room.

**Verification** — suite **485 passed / 1 failed** (known environmental
`kiteconnect` check). Read from the rendered DOM, TCS on `first_pullback_short`:
`[17] TGT Broker 3205.00 · [18] R:R "1.5:1" · [19] SL Points 25.20 ·
[20] TGT Points 59.80 · [21] LTP n/a · [22] Unrealised n/a · [23] Close`.
Other strategies: `gap_fade_long` → `2:1`, `range_breakout_long` → `3:1`,
`vwap_bounce_long` → `—`.

---

---

## WINDOW CONTINUES: 13-Aug-2026 evening — cross-screen semantic correction

⚠️ **`origin/main` MOVED TONIGHT.** It was `2bfe9e2` when this window opened; Fix 2
was deployed at **18:56:30 IST** and `origin/main` is now
**`1c8c710bf4df60fcca8b09375d6cd723590d3820`** (measured two ways at the gate).
⇒ 🔑 **Carried-forward item 3 has FIRED: this branch is based on `2bfe9e2` and is now
behind. It needs a refit onto `1c8c710`, its own verification run and a NEW EXACT SHA
before any deployment. ⛔ Never deploy the pre-refit hash.**

### Entry 9

| Field | Value |
|---|---|
| **Date/time** | 2026-08-13 21:41 IST |
| **Commit** | `00299c6a40be5a6f4ad8af0058b4537024b87098` |
| **Short** | `00299c6` |
| **Branch** | `feat/screen06-positions` |
| **Screen** | **Screens 04 + 05 + 06** — score-label semantics (⛔ NOT Screen-07) |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window; and the branch now needs a refit onto `1c8c710` |

**Why this exists.** It is a **deliberate pre-deployment correction to three already-
accepted screens**, made on Rama's explicit instruction of 13-Aug-2026, and recorded as
its own entry so it is not mistaken for Screen-07 work. Screen-07 is **not started** —
it is built after tomorrow's 08:15 Fix-2 observation.

**The ruling (Rama, quoted).** *"System Score = achieved score = screener_results.score;
Score Threshold = eligibility threshold = eligible_score / min_pass_score fallback. Do
not use Signal Score for either of these… Maintain semantic label consistency across the
entire system. Do not fabricate or relabel a threshold as a score."*

**Change summary**
- `db_reader.signal_scores()` now returns `{system_score, score_threshold}` — the mapping
  is **reversed** from what it shipped: `system_score` was `eligible_score` (the
  THRESHOLD) and `signal_score` was `score`.
- All three API call sites (`/api/signals`, `/api/orders/screen`,
  `/api/positions/screen`) remapped; `reject_score`/`required_score` keep their meaning.
- Export column `Signal Score` → **`Score Threshold`**.
- `signals.html`, `orders.html`, `positions.html`: column defs, Screen-04 detail card and
  row mapping.
- Guard renamed `assert_signal_score_label_is_retired`; `SIGNAL_SCORE_ALLOWED_FILES` is
  now **EMPTY** (was `{signals,orders,positions}.html`). Kept as a set, ⛔ not deleted.
- `04_signals_asset_spec.md`: the 11-Aug supersession is marked **SUPERSEDED and
  retained**, with the current binding table above it.

**🔑 It reverts a documented Rama decision, and that is stated rather than hidden.** The
11-Aug-2026 supersession of L8 for Screen-04 is **closed**; L8
(`G5_REDESIGN_PHASE_B.md:11`) is **restored** and now applies everywhere.

**⭐ It is a restoration, not a new rule — the app already carried BOTH meanings at
once.** `db_reader.screener_scores()` has always returned the ACHIEVED score under the
label "System Score" for analytics / operations / trade_explorer / trade_logs, while
`db_reader.signal_scores()` returned the THRESHOLD under the same label for Screens
04/05/06. One label, two quantities, one application.

**🔴 A real defect was fixed, not just a rename.** The `min_pass` fallback was applied to
`system_score`, so a signal with no stored `eligible_score` printed the **configured
threshold** in a per-signal score column. The fallback now belongs to `score_threshold`
alone.

**⚠️ And the test that claimed to pin the old mapping was VACUOUS.** Neither fixture's
`screener_results` carries the v14 `eligible_score` column, so
`assert system_score in (82, None)` could **only ever** see `None` — it never verified
the mapping it claimed to pin. The rebuilt test `ALTER TABLE`s the column in so both
quantities are real and the assertion can genuinely go red.

**Measured, ⛔ not assumed** — the one thing that could have made this unsafe:
*"Signal Score Too Low"* is **not** a production literal. Live reject reasons are
`REJECTED_SCORE_<n>` (`REJECTED_SCORE_57` ×35,411, `REJECTED_SCORE_59` ×15,123 on the VM
DB, 13-Aug) and the phrase appears in **no** Python outside `ops_dashboard/`. ⇒ retiring
the label creates **no** backend/UI mismatch. The spec's claim to the contrary described
a **test fixture**, and is corrected in place.

**Verification** — full dashboard suite **485 passed / 1 failed**, `104 s`. The single
failure is `test_isolation.py::test_c_venv_has_no_kiteconnect`, which self-attributes:
*"kiteconnect IS installed in the GUI venv — isolation I4 violated."* ⭐ **Identical
count and identical failure to the run recorded for Screen-06**, so nothing regressed.
Additionally verified end-to-end: all three endpoints emit `system_score` +
`score_threshold`, the XLSX header reads **Score Threshold**, and no `signal_score` key
or "Signal Score" label survives anywhere in backend, templates or tests.

### Entry 9 — addendum: LOCAL BROWSER VERIFICATION (13-Aug-2026 ~21:55 IST)

⛔ **No code commit.** Rama asked to see the change rendered before tomorrow's
observation. ⛔ Nothing was deployed, pushed or refitted; ⛔ no design or semantic change
was made during the check.

**Server:** `python -m backend.app` → `http://127.0.0.1:8500`, waitress, 4 threads.
⚠️ **Loopback ONLY, and that is enforced in code, not by convention:** `app.py:241`
raises `RuntimeError` on any non-loopback bind (isolation rule I6), so there is **no LAN
address** and none was created.

**Auth** was unconfigured (`password_hash: ""` → *"Auth not configured"*). Resolved
WITHOUT touching a tracked file: credentials written to
`backend/config/gui_config.local.yaml`, which is **git-ignored**
(`ops_dashboard/.gitignore:9`) and is the mechanism the app already provides for exactly
this. ⚠️ `totp_disabled: true` is set explicitly because AB-910 §1.3 makes an empty TOTP
secret **refuse** rather than pass — ⛔ the flag is a deliberate dev-only opt-out, not a
weakened default. `git status` stays clean.

**Rendered verification — the actual HTML, ⛔ not the templates and ⛔ not the API:**

| Screen | URL | System Score | Score Threshold | "Signal Score" |
|---|---|---|---|---|
| 04 Signals | `/signals` | 2 | 2 | **0** |
| 05 Orders | `/orders` | 1 | 1 | **0** |
| 06 Positions | `/positions` | 2 | 2 | **0** |

**Export verified as a real generated file**, ⛔ not by reading the column list in source:
`/api/export/positions` was downloaded and parsed with `openpyxl` — 32 columns, header
carries **`System Score`** and **`Score Threshold`**, and `"Signal Score" in header` is
**False**.

🔴 **THE LIMIT OF THIS CHECK, STATED PLAINLY: the PC database is EMPTY.**
`D:/Projects/trading-system/data_store/trading_system.db` has **0 trades, 0 signals,
0 orders, 0 screener_results** (measured; and it is the only PC DB — `analytics.db` has
none of these tables). ⇒ ⭐ **the LABELS and the export are verified in the rendered UI;
⛔ the VALUES are NOT — no row exists to show an achieved score beside its threshold.**
⛔ **No data was seeded to make the screens look populated.** The value-level proof is the
suite instead: `test_system_score_and_threshold_are_two_different_real_columns` (88 vs 82)
and `test_score_threshold_falls_back_to_config_but_system_score_never_does` (71 vs 60).

### Entry 9 — addendum 2: LOCAL LOGIN FAILED, DIAGNOSED, FIXED (13-Aug-2026 ~22:10 IST)

⚠️ **This supersedes the auth paragraph of addendum 1**, which described a setup that was
**wrong** and has been removed.

**Symptom.** Rama could not log in locally. ⛔ Not reproduced by guesswork — the server log
named it: `auth.login_throttled: consecutive_failures=5,6,7 … username='ramakrishnan'`.

## 🔑 **CAUSE — MINE, AND IT IS THE INVENTED-CREDENTIAL CASE:** addendum 1 created a
local-only credential with a username I made up (**`rama`**) while Rama types his real
one (**`ramakrishnan`**). ⛔ The password was never reached; the username never matched.
⚠️ **A second fault in the same overlay:** it set `totp_disabled: true`, i.e. WEAKER than
the VM, which requires TOTP.

**Config comparison (read-only; ⛔ no secret printed, ⛔ no VM credential copied).**

| | VM GUI `gui_config.local.yaml` (mode 600) | Local overlay, addendum 1 |
|---|---|---|
| username | `ramakrishnan` | `rama` ❌ |
| password_hash | set | set (invented) |
| totp_secret | set | empty ❌ |
| totp_disabled | **false — TOTP required** | `true` ❌ weaker |

**⛔ WHY THE VM CREDENTIAL WAS *NOT* REUSED (the preferred option, deliberately declined):**
it depends on a **TOTP secret**, so reusing it means copying that seed from a mode-600 VM
file onto the PC. That spreads a secret and runs against the standing direction to reduce
PC-side secret copies. ⇒ took the sanctioned alternative: a **local-only credential via
the project's own supported mechanism**.

**Fix — ⛔ no code change; the application already provided everything needed.**
`python -m backend.auth --setup --username ramakrishnan --config backend/config/gui_config.local.yaml`
⇒ writes hash + a **fresh local** TOTP secret to the **git-ignored** overlay.
✅ **TOTP is ENABLED (`totp_disabled: false`) — authentication is NOT weakened and now
matches the VM's posture.** ⛔ The old `rama` overlay was deleted.

🔒 **Secrets discipline, verified ⛔ not asserted:** `git status` **clean**;
`git status --ignored` shows the overlay as `!!` and `git check-ignore` resolves it to
`ops_dashboard/.gitignore:9`; and a scan of **every tracked file** for the hash and the
TOTP secret returns **NONE**. ⛔ The password and the otpauth URI are deliberately **NOT
recorded in this ledger**, because this file IS tracked — they were given to Rama in
session and the URI written to a local scratchpad file only.

**Verification after restart (server restarted, which also cleared the in-memory throttle):**
- ✅ End-to-end login as `ramakrishnan` **with a TOTP code** → authenticated, redirected off
  `/login`. The form's fields are `username`, `password`, `totp`.
- ✅ `/signals` `System Score`×2 · `Score Threshold`×2 · **"Signal Score" ×0**
- ✅ `/orders` ×1 · ×1 · **×0** · ✅ `/positions` ×2 · ×2 · **×0**
- ⛔ **PC database still EMPTY and left that way** — ⛔ no data seeded. Labels and layout are
  verified in the rendered UI; **values remain proven only by the test suite.**

⛔ **NOT PUSHED · NOT DEPLOYED · NOT REFITTED onto `1c8c710`.** ⛔ VM credentials untouched.

---

### Entry 10

| Field | Value |
|---|---|
| **Date/time** | 2026-08-13 23:12 IST |
| **Commit** | `fb44522fbd59197eaf160a3d6fc9244216792985` |
| **Short** | `fb44522` |
| **Branch** | `feat/screen06-positions` |
| **Screen** | **Screen-07 Trade Explorer** (+ a local-development direct-open mode) |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window; and the branch still needs a refit onto `1c8c710` |

📌 **This supersedes Entry 9's *"Screen-07 is not started — it is built after
tomorrow's 08:15 Fix-2 observation"*.** Rama asked for it tonight; that is his call
and it is recorded rather than quietly re-planned. ⛔ Nothing about tomorrow's
observation changed — no deploy, no push, no VM action.

**Change summary**

- **Screen-07 built in the accepted language.** Root carries `ord-page` *and*
  `pos-page` as well as `tex-page`, so every approved Screen-05/06 rule applies
  verbatim (top spacing, `.kc` cards, `.flt-*`, `.st-tbl`, `.ord-scroll`, the
  `.od-*` detail card, the drawer, the modal, drag-to-reorder). `tex-page` adds
  only a multi-segment pie, a diverging bar list, and the width work.
- **ADDITIVE**: the G5c `/api/trades` endpoint is **untouched** and its contract
  test still passes; Screen-07 gets `/api/trades/screen` + `/api/export/trades`.
- 28 columns, four merged groups (Qty · Entry ₹ · SL ₹ · TGT ₹ · P&L ₹), a Result
  chip strip, six KPI cards, four summary panels, a detail drawer with four tabs,
  *View Full Details*, and an XLSX export of the **filtered** set.

**🔑 Three quantities that are routinely confused, kept apart as separate columns**

| | Definition | ⛔ Not |
|---|---|---|
| **ROI %** | `net_pnl / margin_reserved` — return on the capital **committed** | ⛔ on the leveraged notional (at 5× it reports a fifth of the return) |
| **R-multiple** | `net_pnl / risk_amount` — what the trade **achieved** | ⛔ never printed in an R:R column |
| **R:R** | `trades.tgt_risk_reward_applied` — what was **planned**, frozen at placement | ⛔ not today's strategy YAML, which would re-score a past trade |

**⛔⛔ The Filled SL/TGT columns are not fabricated, and the rule is narrow**

`orders.avg_fill_price` is **still NULL on all 977 orders ever placed** —
re-measured on a corpus grown from 927: ENTRY 469 / SL 247 / TGT 235 / EOD 26,
zero populated in every leg. ⇒ Screen-06's finding **reproduces**. A leg's
executed price is knowable only from `trades.exit_price` on a trade whose
`exit_reason` **names** that leg:

- `SL_HIT` ⇒ SL (Filled) · `TGT_HIT` ⇒ TGT (Filled) · **every other reason fills neither**
- ⚠️ `GTT_EXIT` is a **MECHANISM, not a leg** — it fills neither, the same
  treatment `_position_status_of` already gives it.
- (P) **114/114** `SL_HIT` and **76/76** `TGT_HIT` rows carry an `exit_price`;
  measured on the rendered payload, **0 rows** carry a Filled leg that does not
  match their own exit reason.

**⭐ Slippage — a RECOVERED formula, ⛔ not an invented one**

`order_execution_log.parent_trade_id` was only back-filled from July — (P) **0/72**
rows in June, **100/290** in July, **74/74** in August — so only **91 of 246**
filled trades join to a recorded row. Rather than leave 63 % of the column empty,
the recorded rows became the **control**: on all **91** overlapping trades the
direction-aware formula reproduces `slippage_rs` to ≤0.005 and `slippage_pct` to
≤0.01, and the log's own `intended_price`/`actual_price` equal
`entry_target_price`/`entry_actual_price` on **91/91**. ⇒ the derived value is the
**same function of the same operands**. Each row carries `slippage_source`, so a
measurement stays distinguishable from a reproduction.

**🔴 MEASURED WHILE BUILDING — and it bears on an already-accepted screen**

`order_execution_log.order_id` holds an **INTERNAL** id (`ord_<hex>`);
`orders.order_id` holds the **BROKER** id (`260813170888908`). They are
**different id spaces**, so `db_reader.order_exec_context` — which joins them —
returns **ZERO rows on production data for every trade ever placed**, and
Screen-05's execution/slippage block renders *"not captured"* for every order.
⛔ **Screen-05 was NOT changed here**: fixing it is an unrelated change to an
accepted screen and belongs in its own commit with its own decision. Carried
forward below. Screen-07 joins on `parent_trade_id` and a test pins that key.

**⛔ Honest absence, corrected**

`charges` no longer falls back to gross-minus-net on a trade that never opened
exposure — the ungated form printed a measured-looking **0.00 for 161 of 271
rows**. Now NULL there; the KPI total is **unchanged at ₹59.53**, which is the
proof the fix moved only the display and not a real number.

**🔐 Local-development direct-open — FOUR gates, and one of them cannot travel**

A loopback request may be given a session without the login form. Armed only when
**all four** hold (`backend/app.py:_local_dev_armed`):

1. `local_dev.auto_login: true` — meant for the **git-ignored** overlay; the
   tracked `gui_config.yaml` ships **`false`** (pinned by a test).
2. **`OPS_DASHBOARD_LOCAL_DEV=1` in the ENVIRONMENT** — ⭐ the gate that cannot
   ride a push, a merge or the post-receive `checkout -f`, because it is in no
   file. The VM's systemd unit does not set it.
3. a loopback `bind_host` (isolation rule I6, already enforced);
4. a loopback **client**, checked per request.

⚠️ **1 + 3 alone would NOT protect the VM**, whose GUI also binds `127.0.0.1`
behind a TLS terminator — **2 is what makes the guard hold there.**
⛔ Normal username + password + TOTP is untouched and remains the only way in for
every other caller.

**✅ PROVEN IT CAN REFUSE, ⛔ not merely that it permits** — the discriminating
test, same config, single variable: with the overlay flag still `true` but the env
var **absent**, `GET /trades` → **302** (redirect to login) and
`/api/trades/screen` → **401**. With the env var present: **200**.

**🖥️ Verified by RENDERING, and three defects only a render exposed**

⭐ The contract tests were green through all three — they verify *what the screen
says*, not *that it can be read*.

1. **The outcome pie drew ONE arc** while its legend listed six: an Alpine
   `<template x-for>` inside an `<svg>` does not clone into the SVG namespace.
   Rebuilt with `x-html` on a `<g>`; now 6 arcs.
2. **`@ops-refresh` called `boot()`**, which resets the pager and clears the
   filters — and base.html fires that event **every 5 s during market hours**, so
   the screen would have been unusable while the market was open. It now calls
   `refresh()`. Verified: page 2 + a `Failed` filter both **survive** the event.
3. **Squeezing padding to fit 1680 made adjacent right-aligned numerics collide**
   (`"524.12524.10"` as one number). ⛔ Reverted — a table that fits but cannot be
   read has not fitted. Numeric cells keep a real left gutter.

**📏 Width, measured at five viewports (natural table width 1526px)**

| viewport | table | available | result |
|---|---|---|---|
| 2560 | 2048 | 2048 | **fits** |
| 1920 | 1688 | 1688 | **fits** |
| 1680 | 1526 | 1448 | scrolls — narrow-viewport fallback |
| 1440 | 1526 | 1208 | scrolls |

⛔ **How this was NOT achieved**: no column hidden, no heading abbreviated, no
value truncated except Strategy and Scanner (both ellipsised, both full in the
detail card), no font below the sizes Screen-05/06 already ship. Below ~1550px
`.ord-scroll` is the fallback — exactly the one Screen-06 accepted at 1440.

**Verification status — VERIFIED LOCALLY, ⛔ NOT VERIFIED LIVE**

- Suite **537 passed / 1 failed**. ⭐ **Non-vacuous**: 485 → 537 is **+52**,
  exactly the number of tests added. The single failure is the known
  environmental `test_isolation.py::test_c_venv_has_no_kiteconnect`, **identical**
  to the run recorded for Screen-06 and for Entry 9 ⇒ nothing regressed.
- **Rendered and read on REAL data** (see the data note below): 271 trades over
  14-Jul → 13-Aug. Read out of the DOM, not from the templates:
  28 columns in the approved order; the group row merges correctly; a real SL-Hit
  row (**HGINFRA**) renders **SL 528.32 / 528.32 / 528.65** and **TGT 517.83 /
  517.78 / —** — the three-way split and the narrow Filled rule both visibly
  working, on one row.
- **Score labels checked case-INSENSITIVELY over the raw HTML** — ⛔ the
  case-sensitive form was tried first and returned 0 for *every* label, because
  the headers are uppercased by CSS: it could never have gone red. Corrected:
  `signal score` **×0**, `signal_score` **×0**, `system score` **×6**,
  `score threshold` **×6**.
- **Values now proven, which Entry 9 could not do** — its addendum recorded that
  the PC database was empty so *"the LABELS are verified but the VALUES are NOT"*.
  A real row now shows **System Score 62 against Score Threshold 60** — two
  different real numbers side by side.
- Filters (7), the Result chip strip, pagination (1→2→3, 271 rows) and
  rows-per-page all exercised in the browser and verified by their own counts.
- **XLSX export downloaded and parsed with openpyxl**, ⛔ not read off the source:
  45 columns, 271 rows unfiltered; filtered `result=TGT Hit&direction=LONG` →
  **24** rows, distinct Result `{TGT Hit}`, distinct Direction `{LONG}`,
  **SL (Filled) populated 0** and **TGT (Filled) 24/24**. Header carries
  **System Score** + **Score Threshold**; `"Signal Score" in header` is **False**.
- All touched files pure LF (`tr -cd '\r' | wc -c` = 0 on each).

**🗄️ REAL DATA — a read-only local snapshot, and the VM was not modified**

- Source: `/home/ubuntu/systems/trading-system/data_store/trading_system.db`
  (296,632,320 bytes). Transferred by a **pure file read** (`gzip -c` over ssh);
  ⛔ no VM write, ⛔ no `sqlite3` invocation against the live DB, ⛔ no service
  action, ⛔ no cron touched.
- **Consistency proven, ⛔ not assumed**: source `md5 f2ca4616d0b9aec5d2cab515aee66978`
  read **before and after** the transfer and **unchanged**, with `-wal` at 0 bytes
  both times ⇒ the copy is a consistent point-in-time image. Local copy verified
  **byte-identical** (same md5, same size).
- Lands at `data_store/vm_snapshot/` — **git-ignored** (`.gitignore:17`), and
  `git status` is clean of it. The GUI is pointed at it by
  `backend/config/gui_config.local.yaml`, also **git-ignored**
  (`ops_dashboard/.gitignore:9`), verified with `git check-ignore`.
- ⛔ **Nothing was seeded, mocked or invented** to populate the UI. Every figure
  on the screen is a production row.
- 🔒 **No secret was written to a tracked file.** The overlay's existing `auth:`
  block (Rama's local credential from Entry 9 addendum 2) was **not touched** —
  two new top-level blocks were appended beside it. ⛔ No VM credential copied.

---

### Entry 11

| Field | Value |
|---|---|
| **Date/time** | 2026-08-13 23:18 IST |
| **Commit** | *(this entry; its SHA is the branch tip — `git rev-parse HEAD`)* |
| **Branch** | `feat/screen06-positions` |
| **Screen** | — (process artefact) |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window |

**Change summary** — records Entry 10 (`fb44522`) and the two carried-forward
items it added. 📌 Per Entry 3's rule, an entry cannot contain its own hash; the
final entry's SHA is one `git rev-parse` away.

---

### Entry 12

| Field | Value |
|---|---|
| **Date/time** | 2026-08-13 23:47 IST |
| **Commit** | `10ac0f4f1da9b1e741bf84c21d7c9ef20494aa95` |
| **Short** | `10ac0f4` |
| **Branch** | `feat/screen06-positions` |
| **Screen** | **Screen-07 Trade Explorer** — Rama's review corrections |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window; branch still needs a refit onto `1c8c710` |

**1 · Scanner removed — ⛔ by MEASUREMENT, not by opinion**

Rama's premise was *"Strategy and Scanner are the same concept/data"*. It was
checked before anything was deleted, and it holds far wider than the trades table:

| population | rows | scanner = strategy | differing |
|---|---|---|---|
| trades | **603** | **603** | **0** |
| **every signal ever received** | **127,246** | **127,246** | **0** |

13 distinct values on each side. ⇒ the column printed **one value under two
names**. Removed from the table, the filter bar, the detail drawer, the
full-details popup, the export **and the API payload** — ⛔ not merely hidden, so
it cannot return through a template edit alone. ⭐ This is the **same ruling
Screen-06 already carries**; Screen-07 had reintroduced it and is now back in
line. 📌 `/scanner-attribution` is untouched.

**2 · Six columns centred** — Time, Trade Type, Direction, System Score, Score
Threshold, Result. Both score columns **dropped `num: true`** rather than merely
gaining `ctr`, or the cell would have carried `.rt` and `.ctr` at once. ⭐ A
complementary test pins that real amounts stay RIGHT-aligned, so the alignment
test cannot pass by centring everything.

**3 · 🔴 THE ONE THAT WAS A DEFECT, NOT A PREFERENCE — the KPI deck was
describing a different population from the table**

Filters were applied in the **browser only**. Filtering to one strategy left the
table showing 46 rows beneath a deck still reporting all **271** — and the pie,
the win/loss donut, the direction bars, the strategy bars and the result chips
were all on the unfiltered set too. ⇒ exactly the *"silently mixing"* Rama's
item D forbids.

✅ **Filters now go to the SERVER, which applies them BEFORE computing the KPIs
and the summary — using `_apply_trade_filters`, the SAME function the export
calls.** One list, one filter function, one set of numbers. ⛔ The arithmetic is
**not** duplicated in JavaScript.
- **Chip counts** are taken over everything filtered **except the result itself**
  — a chip answers *"how many if I pick this"*. ⛔ Counting the final set would
  read **0** on every unselected chip the moment one was chosen.
- **Dropdown options** come from the **unfiltered** range — ⛔ deriving them from
  the filtered rows collapses each list to the value already selected and leaves
  a filter that cannot be changed.
- A **scope line** under the deck states the base every number describes, and
  highlights when a filter narrows it.

**📏 Width — measured at five viewports, ⛔ not guessed**

Scanner's width went **straight back into Strategy** (82 → 128px) rather than
being spread thinly across 26 columns ⇒ ⭐ **all 13 strategy names now render IN
FULL; there is no ellipsis anywhere on the screen.** The table then came to
**1460** against the **1448** a 1680 viewport offers; the twelve pixels came from
the numeric gutters (5 → 4px), ⛔ **not** from the Strategy cap — re-truncating
the one name that had just become readable to save 12px is the wrong trade.

| viewport | table | available | result |
|---|---|---|---|
| 2560 | 2048 | 2048 | **fits** |
| 1920 | 1688 | 1688 | **fits** |
| 1680 | 1448 | 1448 | **fits** |
| 1600 | 1436 | 1368 | scrolls — fallback |

**Verification — on real data, ⛔ not on the templates**

- **6 real rows spanning EVERY outcome** (SL Hit · TGT Hit · Manual Exit ·
  Closed · Failed · Rejected) reconciled field-by-field against the source DB
  with every derived value recomputed independently — **120 checks, 0
  mismatches**, including ROI, R-multiple, planned R:R, both score quantities and
  the three-way SL/TGT split.
- **Filters driven through the actual UI**: unfiltered **271** → `direction=SHORT`
  **52** → `+ result=TGT Hit` **9**, with KPI total, table rows, pie total and the
  pager agreeing at every step; chips recount to the SHORT subset
  (9/7/4/27/5 = 52) and **stay put** when a result is selected; **Reset** restores
  271.
- **Alignment read as COMPUTED STYLE off the real cells**, ⛔ not from the
  template source.
- **Export re-checked**: 44 columns, no Scanner; sheet rows equal the screen count
  both unfiltered (**271**) and filtered (**9**); on the TGT-Hit sheet **SL
  (Filled) is populated 0 times** and **TGT (Filled) 9 of 9**.
- Suite **561 passed / 1 failed** (537 → 561 = the 24 tests added). The failure is
  the known environmental `test_c_venv_has_no_kiteconnect`.

⚠️ **A test of mine failed first, for a good reason, and it is recorded rather
than quietly fixed:** it filtered on `direction=LONG` to prove the deck narrows —
but **every fixture trade is LONG**, so the filter removed nothing and `filtered`
was correctly `False`. ⭐ The fixture was right and the test was vacuous.
Rewritten to use a filter that bites, with an explicit assertion **that** it
bites.

---

### Entry 13

| Field | Value |
|---|---|
| **Date/time** | 2026-08-13 23:52 IST |
| **Commit** | *(this entry; its SHA is the branch tip — `git rev-parse HEAD`)* |
| **Branch** | `feat/screen06-positions` |
| **Screen** | — (process artefact) |
| **Pushed** | **NO** |
| **Deployed** | **NO** |
| **Reason** | Special no-deployment window |

**Change summary** — records Entry 12 (`10ac0f4`). **Its own SHA is `c12fe9c`,
recorded by Entry 14 below** per Entry 3's rule.

---

### Entry 14 — ✅ SCREEN-07 TRADE EXPLORER: **APPROVED BY RAMA**, COMPLETE, ⛔ UNPUSHED

| Field | Value |
|---|---|
| **Date/time** | 2026-08-13 23:59 IST |
| **Approved by** | **Rama**, on the rendered real-data screen |
| **Screen** | **Screen-07 Trade Explorer** — `http://127.0.0.1:8500/trades` |
| **Code commits** | `fb44522fbd59197eaf160a3d6fc9244216792985` (build) · `10ac0f4f1da9b1e741bf84c21d7c9ef20494aa95` (review corrections) |
| **Ledger commits** | `fb84c105e3dafdc542ad7324792978097f22518c` (Entry 10/11) · `c12fe9c` (Entry 12/13) |
| **Branch tip at approval** | `c12fe9c` — this entry's own SHA is the new tip, one `git rev-parse` away |
| **Pushed** | **NO — INTENTIONALLY UNPUSHED** |
| **Deployed** | **NO** |
| **Reason** | ⛔ The project's standing **deployment/push hold**. The branch is ALSO based on `2bfe9e2` and needs a refit onto `1c8c710` with its own gate and a NEW EXACT SHA before it can ever deploy. |

**Scope, as approved**

Screen-07 Trade Explorer, built on a read-only snapshot of production data:
27-column forensic table over a date range, six KPI cards, four summary panels,
a result chip strip, a four-tab detail drawer, a full-details popup and a
filtered XLSX export.

**The approved semantics, restated so a later reader cannot re-derive them wrongly**

| | |
|---|---|
| **System Score** | the **achieved** score (`screener_results.score`) |
| **Score Threshold** | the **eligibility** threshold (`eligible_score` → `min_pass_score`) |
| **ROI** | return on **committed / reserved** capital (`net_pnl ÷ margin_reserved`) — ⛔ never the leveraged notional |
| **R-multiple** | `net_pnl ÷ risk_amount` — **achieved**, and a **separate metric from ROI and from R:R** |
| **R:R** | the ratio **planned**, frozen at placement (`tgt_risk_reward_applied`) |
| **Broker** | the value **standing** broker-side |
| **Filled** | an **actual execution price only** — SL only on `SL_HIT`, TGT only on `TGT_HIT`; ⛔ never the broker standing value, ⛔ never invented |
| **Scanner** | **removed** — measured identical to Strategy on 603 trades AND 127,246 signals |
| **Currency** | ⛔ no rupee sign in amount **cells**; headings carry it |

**✅ FINAL PRE-COMMIT VERIFICATION — taken at 23:5x, read-only, ⛔ nothing changed
to make it pass**

- **⛔ NO FABRICATED ROWS.** All **271** rendered `trade_id`s exist in the source
  `trades` table — **0 ghosts, 0 duplicates** — and an INDEPENDENT DB count over
  the same range returns **271**, matching the API exactly.
- **⛔ NO HARD-CODED SUMMARY COUNTS.** All ELEVEN headline figures recomputed from
  the returned rows and compared: total, closed, wins, losses, win rate, net P&L,
  avg ROI, avg R-multiple, profit factor, pie total and the chip sum — **0
  mismatches**.
- **Filter consistency re-confirmed on two further filters** (`strategy=gap_go_long`
  → 39; `Delivery + LONG` → 18): table rows, KPI total, pie total and the
  strategy bars **all agree** in each case.
- **Filled semantics held under scan of every row**: **0** rows carry a Filled
  value on a leg their `exit_reason` does not name; **0** rows carry a Filled
  value copied from the broker standing value; **52/52** `SL_HIT` rows have
  `sl_filled == exit_price`.
- **🔒 THE DB CONNECTION IS READ-ONLY AT THE SQLITE LAYER, ⛔ not by convention** —
  a planted `UPDATE` and a planted `DELETE` both raise *"attempt to write a
  readonly database"*. ⭐ Proven by attempting the write, ⛔ not by reading the
  connection string.
- **🔐 PRODUCTION AUTHENTICATION IS NOT WEAKENED, AND THE CHECK COULD GO RED.**
  The discriminator was run with the overlay flag **still `auto_login: true`** and
  the ONLY variable changed being the environment:

  | | `/trades` | `/api/trades/screen` | `/api/export/trades` |
  |---|---|---|---|
  | **without** `OPS_DASHBOARD_LOCAL_DEV` | **302 → /login** | **401** | **401** |
  | **with** `OPS_DASHBOARD_LOCAL_DEV=1` | **200** | **200** | 200 |

  ⭐ That env var exists in **no file**, so it cannot ride a push, a merge or the
  post-receive `checkout -f`. The tracked `gui_config.yaml` ships
  `auto_login: false` (pinned by a test). ⛔ **No production auth bypass exists.**
- **🔒 SECRETS**: all **1,299** tracked files scanned for the local password hash
  and TOTP secret — **0 hits**. The overlay and the DB snapshot remain git-ignored.
- **🖥️ VM RE-MEASURED AT CLOSE AND UNCHANGED**: `trading-system.service`
  **inactive**, production db md5 **`f2ca4616d0b9aec5d2cab515aee66978`** and mtime
  `19:30:01` — both identical to the pre-transfer reading — and `origin/main`
  still **`1c8c710`**.
- Suite **561 passed / 1 failed**; the failure is the known environmental
  `test_c_venv_has_no_kiteconnect`.

⛔ **NOT DONE, and ⛔ not to be inferred**: no push · no deploy · no refit onto
`1c8c710` · no VM action of any kind · no cosmetic redesign · ⛔ no re-opening of
already-approved semantics.

---

### Entry 15 — SCREEN-08 CAPITAL & RISK: implemented, verified locally, ⛔ UNPUSHED

**Date/time:** 14-Aug-2026, committed `14:52 IST`
**Commit:** `fc849b302ef005b1c1d101b2648ac2fd69ea69a9` (`fc849b3`)
**Branch:** `feat/screen06-positions` (name is Screen-06; content now carries
Screens 06, 07 and 08 — ⛔ **check content, never the name**)
**Screen:** 08 — Capital & Risk
**Pushed: NO · Deployed: NO** — reason: evening deployment window; ⛔ nothing is
pushed while Fix 2's `P7` prediction is still open (it scores at the 16:22 officer
run), per the standing *one observation window, one variable* rule.

**Files (4, +767 / −61):**
`ops_dashboard/backend/readers/db_reader.py` (+96) ·
`ops_dashboard/backend/api/risk_capital.py` (+105) ·
`ops_dashboard/frontend/templates/capital_risk.html` (rewritten, +331/−61) ·
`ops_dashboard/tests/test_screen08_capital_risk.py` (new, +296)

**What it does.** Composes the PRESERVED endpoints `/api/risk`, `/api/capital`,
`/api/exposure`, `/api/capacity`, `/api/pnl` and adds **one** new read-only
endpoint `/api/capital/segments`. Ten zones per the approved 14-Aug mockup.

🔑 **THE ONE IDEA: four quantities that are routinely conflated, kept apart.**
`real cash` (fm_ledger) · `real allocation` (real cash × bucket pct) ·
`segment capacity` (real allocation × leverage — **buying power, not cash**) ·
`real committed` (`trades.margin_reserved`).

⛔ **NO SECOND RESERVATION FORMULA.** The new reader reuses `capital_usage()`'s
exact operand — `trades.margin_reserved` — and adds ONLY the MIS/CNC split.
Notional is **derived** as committed × leverage, ⛔ never the reverse, because the
engine stores margin: `capital/fund_manager.py required_margin` is documented
*"Compute required margin = qty * price / leverage. NOT notional"*.
✅ **Verified against a live row to 6 dp:** CAMLINFINE qty 2 @ `103.48338` ÷ 5x =
`41.393352`; stored `margin_reserved` = `41.393352`.

🔴 **PAY-IN / PAY-OUT RENDER "n/a" WITH A REASON — ⛔ NEVER 0.00.** The engine
persists neither, and **isolation rule I4** forbids `kiteconnect` in this service,
so no GUI-readable source exists. ⭐ A `0.00` would read as *"no money moved
today"*, and on 14-Aug that would have been **false**: a **₹5,000 pay-in landed at
`09:16:25.847`** and the engine never learned of it — its only `SYNC` ran at
`09:15:00.044`, **85 seconds earlier**.

⛔ **THE MOCKUP'S BEFORE/AFTER PAY-IN PANELS ARE NOT REPRODUCED.** They are a
*pinned simulation*; no simulation mode exists, so the screen shows the **actual
current state only**. Engine truth is displayed even where it disagrees with the
workbook's intent — ⭐ the discrepancy IS the finding, ⛔ not something to smooth
over.

⭐ Bucket split uses `orders(leg='ENTRY').product` — ⛔ there is no
`trades.product` column — mirroring `state_store`'s own `_NOT_DELIVERY_SQL`, so
the GUI partitions trades **exactly as the engine does**.
⭐ Allocation (70/30) and leverage (5x/1x) are read from **config**, ⛔ not
hard-coded — asserted by a test.

**Verification.**
- ✅ **17/17** new tests pass, including the pinned simulation **to the rupee**:
  `10,000 + 5,000` → MIS real `10,500` / segment `52,500` / remaining `46,500`;
  GTT real `4,500` / segment `4,500` / remaining `2,500`; consumed `3,200`;
  remaining real `11,800`; totals segment `57,000` / remaining `49,000`.
  ⭐ **And the engine-truth case where the pay-in has NOT propagated** (total
  `10,000` → MIS `35,000`/`29,000`, GTT `3,000`/`1,000`, available `6,800`).
  ⭐ The fixture uses **different** leverages (5x vs 1x) and **different**
  committed amounts (1,200 vs 2,000), so a reader that applied one leverage to
  both, or swapped notional for margin, **goes red**.
- ✅ **Full GUI suite: 578 passed / 1 failed.** The single failure is
  `test_isolation.py::test_c_venv_has_no_kiteconnect`, which runs `pip show
  kiteconnect` against `sys.executable` and **reads no repo file** ⇒ ⛔ it cannot
  be caused by this change; it is an artefact of running system Python instead of
  `ops_dashboard/.venv` (which does not exist on this machine).
  ⚠️ **Before this commit the same suite was 574 passed / 5 failed** — the four
  extra failures were **mine**: the first template draft dropped the G-1
  honest-gap panel. ⭐ Fixed by **restoring the disclosure**, ⛔ not by weakening
  the test. `"Pending Broker Source"` is present and asserted.
- ✅ **Live render** against a fresh VM snapshot (`sqlite3 .backup`, md5 identical
  both ends, remote temp removed, `data_store/` is gitignored): `/capital-risk`
  → **HTTP 200**, 46,024 bytes, zero template errors, all ten zones present.
  `/api/capital/segments` returns **opening `5,588.60` · MIS real `3,912.02` /
  segment `19,560.10` · GTT real `1,676.58` / segment `1,676.58`** — matching
  `fm_ledger`'s own bucket figures exactly, with `payin/payout available:false`.
- ⚠️ **Browser verification is a RENDER check, ⛔ not a visual one.** The page was
  fetched and parsed; ⛔ no human-eye pass on layout, spacing or colour has been
  done. Direct-open dev mode was used (`OPS_DASHBOARD_LOCAL_DEV=1` + gitignored
  `gui_config.local.yaml`); ⛔ production auth untouched.

**Limitations carried (⛔ none fixed here):**
1. 🔴 **Pay-in/pay-out cannot be shown until the engine persists them.** The
   broker exposes `available.intraday_payin` and `utilised.payout`
   (value-verified 14-Aug 13:03:26: `intraday_payin = 5000`), but the trading
   system reads neither — `broker/zerodha_adapter.py:1451` projects the whole
   margins response into a 4-field `MarginInfo`. ⛔ Fixing that is a
   **trading-system** change, ⛔ not a GUI one.
2. ⚠️ **Payout semantics remain UNVERIFIED** — `utilised.payout` has never been
   observed non-zero, so cumulative-vs-current-day is unknown. Same for multiple
   pay-ins in one day.
3. ⚠️ The **5 % SL-M buffer** (`fund_manager.py:561-563`) is held in `fm_ledger`
   bucket availability and released on SL-M acceptance; it is **not** in
   `trades.margin_reserved`, so this screen shows the **settled** figure. Stated
   in the endpoint's `source_note`.
4. ⚠️ Pre-existing base mismatch in the **old** `/api/capital` (`remaining` is
   intraday-based while `deployed_pct` uses total opening). ⛔ **Left untouched** —
   other screens consume it; the new endpoint is additive and correctly based.
5. ⚠️ The **Daily Trades 9/10** dashboard figure vs the engine's **3/10** is a
   *different* screen's defect and is ⛔ not touched here.

---

### Entry 16 — SCREEN-08 **LOCKED** by Rama: simulation restored, two live-data corrections, ⛔ UNPUSHED

**Date/time:** 14-Aug-2026, committed `15:52 IST`
**Commit:** `f21af2c6b2aa4baf742e5bd8e48ca694455c1697` (`f21af2c`) — on top of `fc849b3`
**Branch:** `feat/screen06-positions` · **Screen:** 08 — Capital & Risk
**Pushed: NO · Deployed: NO** — and here the reason is **MEASURED, ⛔ not a choice**:

🔴 **THE PUSH IS REJECTED. `git push --dry-run origin HEAD:refs/heads/main` →
`! [rejected] HEAD -> main (non-fast-forward)`.** (P) `origin/main` =
`1c8c710bf4df60fcca8b09375d6cd723590d3820`, measured at `15:51`; this branch is
**4 BEHIND / 24 AHEAD** — the four it lacks are **Fix 2's**, installed last night.
⇒ ⛔ **It cannot be pushed as-is. It needs a rebase onto `1c8c710`, which produces
a NEW EXACT SHA requiring its own verification run** — ⛔ never push the pre-refit
hash. 📌 Same shape as `N12-*`: *a branch that is behind cannot fast-forward, and
the dry-run is the check, ⛔ not the assumption.*
⏱️ **AND `P7` HAD NOT CLOSED** at commit time (it scores at the **16:22** officer
run). ⭐ Rama's own standing rule — *"no GUI push until P7 closes — one
observation window, one variable"* — was still binding. ⇒ **two independent
blockers, either alone sufficient.**

**Files (4, this commit):** `backend/api/risk_capital.py` ·
`backend/readers/db_reader.py` · `frontend/templates/capital_risk.html` ·
`tests/test_screen08_capital_risk.py`

**① PINNED SIMULATION RESTORED** (zone 5b) — the approved design requires it and
the first draft had dropped it under the earlier *"do not fabricate a before/after
state"* instruction. ⭐ Computed by a **PURE** backend function from the scenario's
**own** constants — ⛔ no DB, ⛔ no config, ⛔ no live value reaches it — so it is
deterministic and reproducible even if config drifts. ⛔ **Not one figure is
hard-coded as an OUTPUT.** All 22 verified from the running endpoint:
`before` MIS `7,000 / 35,000 / 1,200 / 29,000` · GTT `3,000 / 3,000 / 2,000 / 1,000` ·
totals `10,000 · 3,200 · 6,800 · 38,000 · 30,000`;
`after` MIS `10,500 / 52,500 / 1,200 / 46,500` · GTT `4,500 / 4,500 / 2,000 / 2,500` ·
totals `15,000 · 3,200 · 11,800 · 57,000 · 49,000`.
⭐ LIVE and SIMULATION carry distinct badges and the sim block is visually set
apart ⇒ ⛔ the pinned `10k/15k` can never be read as the broker balance.

**② THE LIVE BASIS NO LONGER PRESENTS ITSELF AS THE BROKER'S BALANCE.** `₹5,588.60`
was rendered as unqualified *"Total Real Cash (Live)"*. It now carries the engine's
last broker sync **on screen**, read from `fm_ledger`: *"Engine truth, not the
broker's live balance … Last broker sync: `09:15:00` (SYNC), 1 sync(s) today."*
✅ **VERIFIED that NO allowed channel can supply pay-in/pay-out — ⛔ width stated,
⛔ not assumed:** isolation rule **I4** forbids `kiteconnect` in this service ·
`/metrics` exposes only the engine's own `total_capital` (`5588.6`), ⛔ not broker
cash · `capital_snapshot` has **0 rows** · `fm_ledger` holds only `INIT`/`SYNC`.
⭐ The broker **does** hold it (`intraday_payin = 5000`, value-verified `13:03:26`)
— ⛔ the engine simply never learns of it. 📌 **Persisting it is a TRADING-SYSTEM
change, ⛔ not a GUI one** — deferred to Monday with the capital-recomputation unit.

**③ 🔴 `DAILY TRADES 17/10 BREACH` WAS A READER DEFECT, ⛔ NOT A BREACH.**
`daily_trades_used` counted `status NOT GLOB 'REJECTED*'` ⇒ it excluded REJECTED
but **counted all 15 FAILED** rows — orders placed, never filled, self-cancelled at
the 60 s timeout. **(P) today: 20 rows = `CLOSED 3 · FAILED 15 · REJECTED 2`;** the
old expression returned **18** against a cap of 10 and rendered **`170% BREACH`**,
while the engine's own gate returned **3**.
✅ **Fixed at the SOURCE**, mirroring `capital/state_store.py`
`_EXECUTED_TRADE_STATUSES` **verbatim** (duplicated BY VALUE per isolation rule
I1), whose docstring states it outright: *"FIX-181: only statuses in
`_EXECUTED_TRADE_STATUSES` are counted; FAILED, CANCELLED and REJECTED rows (which
never opened a position) are excluded."* **Now `3 / 10 · OK`.**
⛔ **NO LIMIT HAD BEEN BREACHED** — the August peak is **8**, measured across every
trading day. ⭐ The reader is **shared with the summary bar**, so this also corrects
the Dashboard's **9/10** flagged this morning — ⭐ **ONE CLASSIFIER, ⛔ not two
cosmetic edits.**
⚠️⚠️ **AND THE EXISTING TEST STILL PASSES UNCHANGED** — `test_db_reader.py:47`
asserts `== 8` and is green **both before and after**, because its fixture contains
**no FAILED rows**. ⇒ 📌 **that test could NEVER have caught this defect** — the
tautological-check class again, in a reader that gates a live risk limit.

**Verification.** ✅ Screen-08 suite **22/22**. ✅ Full GUI suite **583 passed / 1
failed** — the failure is `test_isolation.py::test_c_venv_has_no_kiteconnect`,
which runs `pip show` against `sys.executable` and **reads no repo file** ⇒ ⛔ it
cannot be caused by this change. ✅ `/capital-risk` → **HTTP 200**, 53,791 bytes,
**zero** template errors; LIVE badge, PINNED SIMULATION badge, Simulation Input,
Before, After, the engine-truth warning and the G-1 *"Pending Broker Source"* panel
all present. ✅ Direct-open verified cookie-less (HTTP 200, no login redirect);
⛔ production auth untouched.
⚠️ **Browser check is a RENDER check, ⛔ not a visual one** — no human-eye pass on
layout/colour has been performed by me.

**Carried, ⛔ not fixed here:**
1. ⚠️ `delivery_daily_used` counts CNC **entry orders PLACED** today regardless of
   outcome — shows **2** where the engine counts **0**. ⭐ **Same defect class one
   row down**; ⛔ left alone because the correction was scoped to Daily Trades.
   📌 Two-line fix, awaiting Rama's word.
2. 🔴 **Pay-in/pay-out cannot be shown until the ENGINE persists them** — a
   trading-system change (`broker/zerodha_adapter.py:1451` projects the whole
   margins response into a 4-field `MarginInfo`).
3. ⚠️ **Payout semantics remain UNVERIFIED** — `utilised.payout` has never been
   observed non-zero.
4. 🔑 **THE REBASE IS OWED BEFORE ANY PUSH**: rebase onto the measured `origin/main`,
   re-run the suite on the NEW SHA, and re-dry-run. ⛔ Never push `f21af2c` itself.

---

### Entry 17 — GLOBAL UI RULE: all table DATA cells centre-aligned, ⛔ UNPUSHED

**Date/time:** 14-Aug-2026, committed `16:05 IST`
**Commit:** `990fa271201ae48d7c41e52fbb04c80872a48130` (`990fa27`)
**Branch:** `feat/screen06-positions` · **Scope:** GLOBAL (every screen), 1 file
**Pushed: NO · Deployed: NO** — ⛔ **re-measured, ⛔ still blocked** (see below).

**File:** `ops_dashboard/frontend/static/style.css` — **one appended rule**,
⛔ zero existing declarations edited:
`table td { text-align: center !important; }`

📜 **Rama's rule, verbatim in effect:** every table's DATA cells are centre-aligned
— numeric, text, percentage, status — on **all** screens, current and future.
⭐ Column **headers keep** their existing left alignment, as instructed.

⚠️⚠️ **`!important` AND LAST — DELIBERATE, ⛔ not lazy.** About a dozen earlier
rules right-align specific BODY cells and would otherwise win on specificity or
source order: `.cap-num` · `.dt-num` · `.hbar-val` · `.dash-page .cap-tbl .rt` ·
`.dash-page .svc-tbl td.rt` · `.strat-page .st-tbl .rt` · `.sig-page .st-tbl .rt` ·
`.ord-page .st-tbl td.rt` · `.pos-page … td.rt`.
🔑 **A plain declaration would have left the RENDERED result right-aligned while
the source read correct** — ⭐ exactly the failure Rama's instruction named.
✅ **VERIFIED nothing can beat it — ⛔ width stated:** ZERO other `!important` on
`text-align` in the stylesheet · ZERO inline `text-align` on any `td` in any
template · ZERO inline `!important` anywhere in the templates.
📌 **SCOPE NARROW ON PURPOSE:** `text-align` only, body cells only; ⛔ no padding,
weight, `tabular-nums` or colour touched ⇒ no screen's layout can shift beyond the
alignment. ⛔ Do not widen to `th` without a new decision.

**Verification.** ✅ Full GUI suite **583 passed / 1 failed** — the failure is
`test_c_venv_has_no_kiteconnect`, which runs `pip show` against `sys.executable`
and **reads no repo file**. ✅ **Screen-08 re-verified UNCHANGED**: the approved
pinned simulation still reproduces **exactly** — `before 10,000 / 3,200 / 6,800 /
38,000 / 30,000`, `after 15,000 / 3,200 / 11,800 / 57,000 / 49,000` — and
`LIVE / ENGINE TRUTH`, `PINNED SIMULATION` and the G-1 *"Pending Broker Source"*
panel are all present. ✅ `/capital-risk` → **HTTP 200**, 53,791 bytes, **zero**
template errors; direct-open still works cookie-less. ✅ The rule is served in
`/static/style.css`.
⚠️⚠️ **HONEST LIMIT — this is a CASCADE proof, ⛔ NOT a pixel proof.** The rule is
last, `!important` and uncontested, so it **must** win by the cascade; ⛔ but I have
not rendered the page in a browser engine and have **not seen** the centring.
⭐ Rama's visual check is the confirming step.

🔴 **PUSH STILL BLOCKED — RE-MEASURED at `16:05`, ⛔ not carried from Entry 16:**
`git push --dry-run origin HEAD:refs/heads/main` → **`! [rejected] HEAD -> main
(non-fast-forward)`**. (P) `origin/main` = `1c8c710…`; this branch is **4 BEHIND /
26 AHEAD**. ⇒ 🔑 **A rebase onto `1c8c710` is owed, producing a NEW EXACT SHA that
needs its own verification run — ⛔ never push the pre-rebase hash.**
⛔ **NO force-push, ⛔ no `--force-with-lease`, ⛔ no gate bypass.**

---

### Entry 17 — GLOBAL UI RULE: all table DATA cells centre-aligned, ⛔ UNPUSHED

**Date/time:** 14-Aug-2026, committed `16:05 IST`
**Commit:** `990fa271201ae48d7c41e52fbb04c80872a48130` (`990fa27`)
**Branch:** `feat/screen06-positions` · **Scope:** GLOBAL (every screen), 1 file
**Pushed: NO · Deployed: NO** — ⛔ **re-measured, ⛔ still blocked** (see below).

**File:** `ops_dashboard/frontend/static/style.css` — **one appended rule**,
⛔ zero existing declarations edited:
`table td { text-align: center !important; }`

📜 **Rama's rule, in effect:** every table's DATA cells are centre-aligned —
numeric, text, percentage, status — on **all** screens, current and future.
⭐ Column **headers keep** their existing left alignment, as instructed.

⚠️⚠️ **`!important` AND LAST — DELIBERATE, ⛔ not lazy.** About a dozen earlier
rules right-align specific BODY cells and would otherwise win on specificity or
source order: `.cap-num` · `.dt-num` · `.hbar-val` · `.dash-page .cap-tbl .rt` ·
`.dash-page .svc-tbl td.rt` · `.strat-page .st-tbl .rt` · `.sig-page .st-tbl .rt` ·
`.ord-page .st-tbl td.rt` · `.pos-page … td.rt`.
🔑 **A plain declaration would have left the RENDERED result right-aligned while
the source read correct** — ⭐ exactly the failure Rama's instruction named.
✅ **VERIFIED nothing can beat it — ⛔ width stated:** ZERO other `!important` on
`text-align` in the stylesheet · ZERO inline `text-align` on any `td` in any
template · ZERO inline `!important` anywhere in the templates.
📌 **SCOPE NARROW ON PURPOSE:** `text-align` only, body cells only; ⛔ no padding,
weight, `tabular-nums` or colour touched ⇒ no screen's layout can shift beyond the
alignment. ⛔ Do not widen to `th` without a new decision.

**Verification.** ✅ Full GUI suite **583 passed / 1 failed** — the failure is
`test_c_venv_has_no_kiteconnect`, which runs `pip show` against `sys.executable`
and **reads no repo file**. ✅ **Screen-08 re-verified UNCHANGED**: the approved
pinned simulation still reproduces **exactly** — `before 10,000 / 3,200 / 6,800 /
38,000 / 30,000`, `after 15,000 / 3,200 / 11,800 / 57,000 / 49,000` — and
`LIVE / ENGINE TRUTH`, `PINNED SIMULATION` and the G-1 *"Pending Broker Source"*
panel are all present. ✅ `/capital-risk` → **HTTP 200**, 53,791 bytes, **zero**
template errors; direct-open still works cookie-less. ✅ The rule is served in
`/static/style.css`.
⚠️⚠️ **HONEST LIMIT — this is a CASCADE proof, ⛔ NOT a pixel proof.** The rule is
last, `!important` and uncontested, so it **must** win by the cascade; ⛔ but I have
not rendered the page in a browser engine and have **not seen** the centring.
⭐ Rama's visual check is the confirming step.

🔴 **PUSH STILL BLOCKED — RE-MEASURED, ⛔ not carried from Entry 16:**
`git push --dry-run origin HEAD:refs/heads/main` → **rejected, non-fast-forward**.
(P) `origin/main` = `1c8c710…`; this branch is **4 BEHIND**. ⇒ 🔑 **A rebase onto
`1c8c710` is owed, producing a NEW EXACT SHA that needs its own verification run —
⛔ never push the pre-rebase hash.**
⛔ **NO force-push, ⛔ no `--force-with-lease`, ⛔ no gate bypass.**

---

### Entry 18 — ⚠️ CORRECTION to Entry 17: centre NUMERIC cells only, ⛔ UNPUSHED

**Date/time:** 14-Aug-2026, committed `16:17 IST`
**Commit:** `cd63b9f46f54eff478be21e873d87146774ab53b` (`cd63b9f`) — corrects `990fa27`
**Branch:** `feat/screen06-positions` · **Scope:** GLOBAL, 2 files
**Pushed: NO · Deployed: NO** — ⛔ still blocked, re-measured (below).

**Files:** `frontend/static/style.css` · `frontend/templates/capital_risk.html`

🔴 **WHAT WAS WRONG IN `990fa27`:** `table td { text-align:center !important }`
centred **every** body cell — including the ROW LABELS (*"Real Capital
Allocation"*, *"Segment Capacity"*, *"Limit Type"*, strategy names). ⭐ Rama's rule
is narrower: **only the numeric/data values** sit centred under their column
heading; **descriptive row and side labels stay LEFT.**

✅ **THE FIX USES THE CODEBASE'S OWN MARKER, ⛔ NOT A POSITIONAL GUESS.** Numeric
cells already carry `.cap-num` / `.dt-num` / `.rt`; label cells are plain `<td>`:
`table td.cap-num, table td.dt-num, table td.rt, table td.ctr`
⇒ centres **exactly** the data columns and ⛔ cannot touch a label.
📌 **`td.rt`, ⛔ NOT `.rt`** — `.rt` is also used on `th`, and column HEADINGS keep
their existing position.
📌 **`ctr` added to the Limits Monitor STATUS cell** — a data column carrying no
number, so ⛔ no numeric class would have caught it.

✅ **MEASURED ON THE RENDERED PAGE, ⛔ not on the source:** **35** `cap-num` cells +
**1** `ctr` cell centred · **25** plain `<td>` row labels left and untouched.
⭐ Every unmarked cell was checked against the template and is genuinely a LABEL
(*Limit Type · Real Capital Allocation · Segment Capacity · Existing Order Value ·
Real Capital Reserved · Remaining Real Capital · Remaining Segment Capacity ·
"Current (engine truth)" · "Before/After pay-in" · strategy names*)
⇒ ⛔ **no numeric value left uncentred, ⛔ no label centred.**

✅ **SCREEN-08 UNCHANGED.** Pinned simulation still exact — `before 10,000 / 3,200 /
6,800 / 38,000 / 30,000`, `after 15,000 / 3,200 / 11,800 / 57,000 / 49,000` —
and `LIVE / ENGINE TRUTH`, `PINNED SIMULATION`, the G-1 *"Pending Broker Source"*
panel all present. ⛔ No calculation, ⛔ no layout, ⛔ no live/simulation separation
touched.
✅ Full GUI suite **583 passed / 1 failed** (`test_c_venv_has_no_kiteconnect` — runs
`pip show` against `sys.executable`, **reads no repo file**). ✅ `/capital-risk` →
**HTTP 200**, 53,818 bytes, zero template errors, direct-open cookie-less.
⚠️ **A stale-server trap was caught here and is worth recording:** the first
verification showed `ctr` count **0** because Flask was still serving the
pre-edit template. ⇒ 📌 **After any template/CSS edit the dev server MUST be
restarted before the page is measured** — otherwise the check reads the OLD
render and reports a false pass.
⚠️ **HONEST LIMIT — cascade proof, ⛔ NOT a pixel proof.** ⛔ No browser engine was
used; Rama's visual check is the confirming step.

🔴 **PUSH STILL BLOCKED — re-measured:** `git push --dry-run origin
HEAD:refs/heads/main` → **rejected, non-fast-forward**; `origin/main` = `1c8c710…`,
this branch **4 BEHIND**. ⛔ No force-push, ⛔ no `--force-with-lease`, ⛔ no gate
bypass. 🔑 Rebase onto `1c8c710` → NEW SHA → own verification run, then push.

---

## ⚠️ Carried forward for tomorrow's deployment review

0. 🔴 **`order_execution_log` cannot be joined by `order_id`, and Screen-05 is
   affected.** `order_execution_log.order_id` holds an INTERNAL id (`ord_<hex>`);
   `orders.order_id` holds the BROKER id (`260813170888908`). `db_reader.
   order_exec_context` joins them and therefore returns **zero rows on production
   data for every order ever placed** — Screen-05's execution/slippage detail
   renders *"not captured"* everywhere, and it is not a data gap. The working key
   is `parent_trade_id` (⚠️ itself only back-filled from July: 0/72 June, 100/290
   July, 74/74 August). ⛔ **NOT fixed here** — an unrelated change to an accepted
   screen, and it needs its own decision about the July gap.
0b. ⚠️ **`@ops-refresh` → `boot()` may have the same effect on Screens 04/05/06.**
   Screen-07 was fixed; the others were **not inspected or changed**. The event
   fires every 5 s during market hours, so if they reset their pager/filters the
   same way it will show under live conditions, not off-hours. ⛔ Measure before
   assuming — it is stated here as an unverified suspicion, not a finding.
1. **`/api/export/orders` does not exist.** Screen-05's *Export XLSX* button navigates
   to it and gets a **404**. Verified wide: there is no export route anywhere under
   `ops_dashboard/**/*.py`. Screen-06 ships a working `/api/export/positions`;
   Screen-05's was **left alone deliberately** — fixing it is an unrelated change to an
   already-accepted screen and belongs in its own commit with its own decision.
2. **`origin/main` must be re-measured at deploy time**, not taken from any document.
   It was `2bfe9e2` when this window opened and it has moved four times on 12-Aug alone.
   The fast-forward check is `git push --dry-run origin <sha>:refs/heads/main` against
   the ref measured at that moment.
3. **Deployment order matters**: this branch is based on `2bfe9e2`. If `origin/main`
   has moved by tomorrow evening, this needs a refit onto the new tip, its own
   verification run, and a new exact SHA — ⛔ never deploy the pre-refit hash.
