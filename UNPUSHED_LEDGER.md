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

### Entry 19 — Screen-08 alignment BY COLUMN ROLE (final spec), ⛔ UNPUSHED

**Date/time:** 14-Aug-2026, committed `16:32 IST`
**Commit:** `51f31d9b683682f4011868d0047e0e6ece57bae4` (`51f31d9`)
**Branch:** `feat/screen06-positions` · **Files:** `static/style.css` ·
`templates/capital_risk.html` · **ALIGNMENT ONLY**
**Pushed: NO · Deployed: NO** — ⛔ still blocked, re-measured (below).

📜 **Rama's final spec — alignment is decided PER ROLE, and each role is targeted
SEPARATELY, ⛔ never by one `text-align` on every cell:**

| role | alignment | how |
|---|---|---|
| row / side labels | **LEFT** | plain `<td>` — ⛔ untouched, no rule needed |
| data column **HEADINGS** | **CENTER** | 🆕 `.cap-page .cap-table th` |
| data cells | **CENTER** | `td.cap-num/.dt-num/.rt/.ctr` (from `cd63b9f`) |
| **5b Simulation Input values** | **RIGHT** | 🆕 `.cap-page .sim-input td.cap-num` |

🔑 **SCOPED TO A NEW `.cap-page` CLASS ON PURPOSE.** `.cap-table` is **shared with
other screens**, so an unscoped heading rule would have silently re-aligned tables
nobody asked about. ⭐ **The scope is the difference between an alignment fix and an
unintended change to Screens 04-07.** ⛔ Do not remove it.

✅ **`th:first-child` STAYS LEFT — and this was VERIFIED, ⛔ not assumed.** Every
Screen-08 `<thead>` was enumerated; the first heading is the **row-label column in
all seven** cases, and every other `th` sits over numeric data:

| table | first th (LEFT) | remaining th (CENTER) |
|---|---|---|
| §4 Limits Monitor | `Limit Type` | Configured · Used · Remaining · Usage % · Status |
| §5 totals | `Totals` | the five capital columns |
| §5b Before/After grid | *(empty spacer)* | Intraday / MIS · Delivery / GTT |
| §5b totals | `Totals` | the five capital columns |
| §6 Strategy Allocation | `Strategy` | MIS (Real) · GTT (Real) · Total (Real) · % of Consumed |
| §7 Top Consumers | `Strategy` | Capital Used (Real) · % of Total |
| §9 Utilization Range | `Utilization Range` | Status |

📌 **The Simulation Input EXCEPTION needs `!important` AND higher specificity**
(`.cap-page .sim-input td.cap-num`) to beat the data-cell centring rule. ⭐ That
table has **no `<thead>`** — which is precisely why it is a label→value list rather
than a data grid, and why its values read better RIGHT-aligned.

✅ **ALIGNMENT ONLY.** ⛔ No layout, ⛔ no calculation, ⛔ no colour, ⛔ no label,
⛔ no content, ⛔ no route changed. **Screen-08 re-verified:** pinned simulation
still exact (`before 10,000 / 3,200 / 6,800 / 38,000 / 30,000`; `after 15,000 /
3,200 / 11,800 / 57,000 / 49,000`), `LIVE / ENGINE TRUTH` vs `PINNED SIMULATION`
separation intact, MIS 70%/5x and GTT 30%/1x unchanged.
✅ Full GUI suite **583 passed / 1 failed** (`test_c_venv_has_no_kiteconnect` — runs
`pip show` against `sys.executable`, **reads no repo file**).
✅ `/capital-risk` → **HTTP 200**, 53,836 B, zero template errors, `cap-page` scope
present, both CSS rules served, direct-open cookie-less.
⚠️ **HONEST LIMIT — cascade proof, ⛔ NOT a pixel proof.** Rules verified present,
scoped and served; ⛔ no browser engine was used to look at the result. ⭐ Rama's
visual pass on the five areas is the confirming step.

🔴 **PUSH STILL BLOCKED — re-measured:** `origin/main` = `1c8c710…`, branch **4
BEHIND**, `git push --dry-run origin HEAD:refs/heads/main` → **rejected,
non-fast-forward**. ⛔ No force-push, ⛔ no `--force-with-lease`, ⛔ no bypass.
✅ **NOTE: the P7 half of the gate is now CLOSED** — P7 scored ✅ CONFIRMED at
`16:25` (drift EMPTY at `16:21:56` → officer ran `16:22:01` → ` M` at `16:25:13`).
⇒ ⭐ **the only remaining blocker is the rebase**, which owes a NEW SHA and its own
verification run.

---

### Entry 19 — Screen-08 alignment BY COLUMN ROLE (final spec), ⛔ UNPUSHED

**Date/time:** 14-Aug-2026, committed `16:32 IST`
**Commit:** `51f31d9b683682f4011868d0047e0e6ece57bae4` (`51f31d9`)
**Branch:** `feat/screen06-positions` · **Files:** `static/style.css` ·
`templates/capital_risk.html` · **ALIGNMENT ONLY**
**Pushed: NO · Deployed: NO** — ⛔ still blocked, re-measured (below).

📜 **Rama's final spec — alignment is decided PER ROLE, and each role is targeted
SEPARATELY, ⛔ never by one `text-align` on every cell:**

| role | alignment | how |
|---|---|---|
| row / side labels | **LEFT** | plain `<td>` — ⛔ untouched, no rule needed |
| data column **HEADINGS** | **CENTER** | 🆕 `.cap-page .cap-table th` |
| data cells | **CENTER** | `td.cap-num/.dt-num/.rt/.ctr` (from `cd63b9f`) |
| **5b Simulation Input values** | **RIGHT** | 🆕 `.cap-page .sim-input td.cap-num` |

🔑 **SCOPED TO A NEW `.cap-page` CLASS ON PURPOSE.** `.cap-table` is **shared with
other screens**, so an unscoped heading rule would have silently re-aligned tables
nobody asked about. ⭐ **The scope is the difference between an alignment fix and an
unintended change to Screens 04-07.** ⛔ Do not remove it.

✅ **`th:first-child` STAYS LEFT — and this was VERIFIED, ⛔ not assumed.** Every
Screen-08 `<thead>` was enumerated; the first heading is the **row-label column in
all seven** cases, and every other `th` sits over numeric data:

| table | first th (LEFT) | remaining th (CENTER) |
|---|---|---|
| §4 Limits Monitor | `Limit Type` | Configured · Used · Remaining · Usage % · Status |
| §5 totals | `Totals` | the five capital columns |
| §5b Before/After grid | *(empty spacer)* | Intraday / MIS · Delivery / GTT |
| §5b totals | `Totals` | the five capital columns |
| §6 Strategy Allocation | `Strategy` | MIS (Real) · GTT (Real) · Total (Real) · % of Consumed |
| §7 Top Consumers | `Strategy` | Capital Used (Real) · % of Total |
| §9 Utilization Range | `Utilization Range` | Status |

📌 **The Simulation Input EXCEPTION needs `!important` AND higher specificity**
(`.cap-page .sim-input td.cap-num`) to beat the data-cell centring rule. ⭐ That
table has **no `<thead>`** — which is precisely why it is a label→value list rather
than a data grid, and why its values read better RIGHT-aligned.

✅ **ALIGNMENT ONLY.** ⛔ No layout, ⛔ no calculation, ⛔ no colour, ⛔ no label,
⛔ no content, ⛔ no route changed. **Screen-08 re-verified:** pinned simulation
still exact (`before 10,000 / 3,200 / 6,800 / 38,000 / 30,000`; `after 15,000 /
3,200 / 11,800 / 57,000 / 49,000`), `LIVE / ENGINE TRUTH` vs `PINNED SIMULATION`
separation intact, MIS 70%/5x and GTT 30%/1x unchanged.
✅ Full GUI suite **583 passed / 1 failed** (`test_c_venv_has_no_kiteconnect` — runs
`pip show` against `sys.executable`, **reads no repo file**).
✅ `/capital-risk` → **HTTP 200**, 53,836 B, zero template errors, `cap-page` scope
present, both CSS rules served, direct-open cookie-less.
⚠️ **HONEST LIMIT — cascade proof, ⛔ NOT a pixel proof.** Rules verified present,
scoped and served; ⛔ no browser engine was used to look at the result. ⭐ Rama's
visual pass on the five areas is the confirming step.

🔴 **PUSH STILL BLOCKED — re-measured:** `origin/main` = `1c8c710…`, branch **4
BEHIND**, `git push --dry-run origin HEAD:refs/heads/main` → **rejected,
non-fast-forward**. ⛔ No force-push, ⛔ no `--force-with-lease`, ⛔ no bypass.
✅ **NOTE: the P7 half of the gate is now CLOSED** — P7 scored ✅ CONFIRMED at
`16:25` (drift EMPTY at `16:21:56` → officer ran `16:22:01` → ` M` at `16:25:13`).
⇒ ⭐ **the only remaining blocker is the rebase**, which owes a NEW SHA and its own
verification run.

---

### Entry 20 — ✅ REBASE + RE-GATE COMPLETE. Screen-08 LOCKED. ⛔ NOT PUSHED, ⛔ NOT DEPLOYED (Rama's instruction)

**Date/time:** 14-Aug-2026, `16:43 IST`
**NEW EXACT SHA:** `c90aa00489b6074e270e5502af6837511caa6c29` (`c90aa00`)
**Old tip, PRESERVED as tag `screen08-prerebase-14aug`:** `3683224`
**Branch:** `feat/screen06-positions` · **0 BEHIND / 32 AHEAD** `origin/main`
**Pushed: NO · Deployed: NO**

📜 **RAMA, VERBATIM — and this is the authority for stopping here:**
*"Do the needful till local pc commit - screen approved & finalized, but don't
deploy to vm"*
🔑 **In this project a PUSH *is* the deploy** — `origin` is the VM bare repo and its
`post-receive` hook runs `checkout -f` into the live tree. ⇒ ⛔ *"don't deploy to
vm"* means **⛔ do not push**. The release checklist's push/deploy steps are
therefore **deliberately not performed**, ⛔ not forgotten.

### The rebase — pre-checked, then proven, ⛔ neither assumed
🔬 **CONFLICT PRE-CHECK BEFORE TOUCHING ANYTHING (read-only):**
`git merge-tree --write-tree origin/main HEAD` → exit **0**, tree
`056b112cee4f177a774c90ff08ea0e85b241bf5e`, **CONFLICT count 0**.
⭐ **And the reason it was safe is structural:** the four commits this branch
lacked are **exactly Fix 2's** (`b04c376` · `4581865` · `e45f6b2` · `1c8c710`) and
they touch **ZERO files under `ops_dashboard/`** — only `config/preflight.yaml`,
`scripts/preflight/checks/{broker,engine}.py`, their two tests and one doc.
🛟 **Safety tag written BEFORE the rebase:** `screen08-prerebase-14aug` → `3683224`.

✅ **Rebase 32/32 replayed cleanly onto the MEASURED `origin/main` `1c8c710`** —
⛔ not onto a SHA copied from a card. Working tree clean afterwards.

✅ **CONTENT PROVEN UNCHANGED, THREE WAYS:**
① `git diff screen08-prerebase-14aug HEAD -- ops_dashboard/` = **0 lines** ⇒ every
  byte of the GUI work survived identical.
② Whole-tree diff old-tip → new-tip is **exactly Fix 2's six files, 542 insertions
  / 7 deletions** ⇒ the ONLY difference is that the tip now *contains* Fix 2.
③ `git range-diff screen08-prerebase-14aug...HEAD` → **32 of 32 rows `=`**.
📌 **SHA MAP:** alignment commit `51f31d9` → **`b1f7bbd`** · tip `3683224` →
**`c90aa00`**. ⛔ **`51f31d9` is now HISTORICAL and must NEVER be pushed** — the
deployable object is `c90aa00`, exactly as `da49ccd`→`1c8c710` went for Fix 2.

### Re-gate ON THE NEW SHA (⛔ never gate the old and ship the new)
✅ **Full GUI suite on `c90aa00`: 583 passed / 1 failed.** The single failure is
`test_isolation.py::test_c_venv_has_no_kiteconnect`, which runs `pip show` against
`sys.executable` and **reads no repo file** ⇒ ⛔ it cannot be caused by this change;
it is an artefact of running system Python instead of `ops_dashboard/.venv`, which
does not exist on this machine. ⭐ Identical result to the pre-rebase run — **no
new failure, none disappeared.**

✅ **SCREEN-08 RE-VERIFIED AFTER THE REBASE, server restarted first:**
`BEFORE (10,000 · 3,200 · 6,800 · 38,000 · 30,000)` → **MATCH**
`AFTER  (15,000 · 3,200 · 11,800 · 57,000 · 49,000)` → **MATCH**
✅ **LIVE / PINNED separation intact:** live `total_live = 5,588.60`,
`payin_today.available = false` ⇒ ⛔ the pinned `10k/15k` has NOT leaked into live.
✅ `/capital-risk` → **HTTP 200**, 53,836 B, **zero** template errors; `cap-page`
scope, `LIVE / ENGINE TRUTH`, `PINNED SIMULATION` and the G-1 *"Pending Broker
Source"* panel all present; direct-open cookie-less.
⭐ **Approved alignment unchanged** — headings centred over their data, row labels
left, Simulation Input values right.

### Status
✅ **DESIGN LOCKED** (Rama's approval). ✅ Rebase done. ✅ Re-gated. ✅ Ledger current.
⛔ **PUSH: NOT PERFORMED — by instruction, ⛔ not by blocker.** 🔑 The
non-fast-forward that blocked every earlier attempt is now **RESOLVED** (0 behind);
`git push --dry-run origin c90aa00:refs/heads/main` would now fast-forward. ⛔ It
was NOT run as a real push and ⛔ nothing was sent.
⛔ **DEPLOY: NOT PERFORMED** — same instruction; ⛔ the VM is untouched, ⛔ no service
restarted, ⛔ `origin/main` still `1c8c710`.
📌 **WHEN THE PUSH IS AUTHORISED:** re-measure `origin/main` at that moment
(⛔ never from this entry — it moves on GUI days too), re-run `--dry-run`, then push
the **explicit refspec `c90aa00:refs/heads/main`**, ⛔ never `git push origin main`.

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

---

## WINDOW: 14→28-Aug-2026 — THE GUI BUILD-OUT AND THE VISUAL-APPROVAL CAMPAIGN

**Branch:** `feat/screen10-slippage-analytics` (continues `feat/screen06-positions`)
**Base at window open:** merge-base `6fa8a1c` (14-Aug).
**Pushed: NO · Deployed: NO** — 🔬 re-measured 30-Aug: `git ls-remote origin
feat/screen10-slippage-analytics` returns **0 refs**. Nothing of this window has
ever left the PC.

### Entry 21 — ALL 22 SCREENS BUILT · 8 VISUALLY APPROVED · ⛔ NOT PUSHED

**Date/time:** 14-Aug → 28-Aug-2026 · **HEAD `d0a4054`**
**Commits since Entry 20 (`8bebcd8`): 90** — 🔬 measured, not counted by hand.
**Pushed: NO · Deployed: NO**

#### What the 90 commits contain

| dates | work |
|---|---|
| 14–15 Aug | **S09** P&L Analytics · **S10** Slippage Analytics · **S11** Execution Analytics · **S12** System Health (UNKNOWN as a first-class state) · **S13** Audit · **S14** Trade Logs · **S15** System Logs |
| 16–17 Aug | **S18** Live Activity · **S19** Strategy Ranking · **S20** Strategy Health · **S21** Scanner Attribution · **S22** Holdings · **S17** Controls |
| 18–20 Aug | The **visual-approval campaign** — 32 commits on 19-Aug alone. S01–S05 approved; S06 approved on its pre-correction render; the 13px-floor decision re-opened all 22 |
| 24 Aug | **S08** Capital & Risk — a **FULL REBUILD**, ⛔ not a patch (`c479b40`), then approved |
| 27 Aug | **S09** heatmaps rebuilt as the artwork's two-row matrix (`292a750`); approved |
| 28 Aug | **S10** Export panel + genuinely column-driven table (`c37e718`); approved (`d0a4054`) |

⭐ Also in this window, and it is the one structural change rather than a screen:
**`efeb0b7` — the column reorder became ONE implementation, not fifteen.**
`colDragMixin()` in `static/components.js:95`. `Object.assign` places page members
LAST, so the existing screens keep their own overrides and are unchanged.
🔬 **Measured 30-Aug: 16 of 30 screen templates use the mixin, and ALL 16 iterate
`cols` inside `<tbody>` ⇒ 16/16 genuinely move the DATA, 0 header-only.**
⚠️ That matters because wiring alone is not the feature: a table with POSITIONAL
`<td>`s moves the LABEL and leaves the DATA behind.

#### 🔴 APPROVAL STANDING — 🔬 from `VISUAL_APPROVAL_LEDGER_19-Aug-2026.md` at HEAD

| state | n | screens |
|---|---|---|
| 🟢 **VISUALLY APPROVED** | **8** | S01 · S02 · S03 · S04 · S05 · S08 · S09 · S10 |
| ⏳ **QUALIFIED** | **2** | **S06** — approved on the **PRE-correction** render; the correction (`b47e148`) is verified and re-rendered but ⛔ never seen. **S07** — approved 14-Aug under the PREVIOUS ledger; `66fc82e` landed after it, unseen; the 13px-floor decision makes this a **RE-approval** |
| ⏳ **NEVER SHOWN** | **12** | S11 – S22 |

⛔ **The recorded figure "9 approved / 13 awaiting a first approval" was WRONG in
both halves** and is corrected here: it is **8 / 12**, with 2 in a qualified state.

#### ⚠️ WHAT THE GREEN ONES DO **NOT** MEAN

- ⛔ **Neither S09 nor S10 is `VERIFIED LIVE`.**
- **S09** was approved on **SEEDED DEMO data** — 🔬 the local DB holds **0 closed
  trades** (last written 03-Aug), so the render used **75 demo trades**.
  ⭐ Composition/colour/alignment are confirmed; ⛔ the populated PRODUCTION
  appearance is not.
- **S10** was approved on a **read-only extract of real VM data** (2.5 MB).
  ⚠️ Its amber/red slippage status path is **LIVE BUT NEVER EXERCISED** —
  🔬 real slippage is ~₹0.00–0.02, **100% within limit**, 0 near, 0 exceeded.
- **S04** owes Q1·Q2 · **S05** owes Q3·Q4 · **S08** carries **2 post-approval
  restorations** (gauge geometry, gated export button) that ⛔ have not been seen.
- ✅ **DEMO-DB HYGIENE HELD:** the `gui_config.local.yaml` pointer was reverted
  **byte-identically** (that file forbids leaving one), the demo DB lives OUTSIDE
  the repo, and 🔬 the real DB's mtime is unchanged at **03-Aug 16:08** ⇒ it was
  never opened for writing.

#### ✅ THE REFIT IS STILL ESSENTIALLY FREE — 🔬 RE-MEASURED 30-Aug

- **89 ahead / 39 behind `effff24`.** ⚠️ The previously recorded *"37 behind"* was
  measured against `52ccb4f`, ⛔ not `effff24` — a different comparand, ⛔ not drift.
- 🔬 **ZERO COLLISIONS, whole tree:** 135 gui09 files vs 68 main files since the
  merge-base, **0 overlap**.
- 🔬 main has touched `ops_dashboard/` in **0** files since `6fa8a1c`; gui09 in **87**.
- ⇒ ⭐ A **rebase**, ⛔ not a merge fight. ⚠️ But it only grows more behind with
  every main push — after the 30-Aug push it becomes **48 behind** `39292d3`.

#### ⛔ WHAT IS NOT DONE

- ⛔ **NOT PUSHED, NOT DEPLOYED.** 0 remote refs.
- ⛔ **S11–S22 have never been shown for approval.** ⭐ S11 Execution Analytics is
  BUILT (`3979b1b`, 15-Aug) — ⛔ what it needs is its FIRST visual approval, ⛔ not
  a build.
- ⛔ **S06 and S07 need Rama's eyes on renders that already exist** — ⭐ the
  cheapest two wins on the board, because the work is done and only the sight is
  missing.
- ⛔ No rebase attempted. ⛔ `origin/main` must be re-measured at refit time, ⛔ never
  taken from this document.

### Entry 22 — S11 EXECUTION ANALYTICS APPROVED. ⛔ NOT PUSHED

**Date/time:** 30-Aug-2026, approved ~15:5x IST · **Branch:** `feat/screen10-slippage-analytics`
**Pushed: NO · Deployed: NO**

⭐ S11 was already BUILT (`3979b1b`, 15-Aug) and Scanner was already removed
(15-Aug, ruling 1). This window is the **visual-approval pass** and the three
corrections it produced. ⛔ The screen was NOT rebuilt.

#### The three changes

1. **INTRADAY & DELIVERY EXECUTION ANALYSIS** now occupies the footprint the
   reference gives Scanner Execution Ranking — **between Strategy and Symbol**,
   exactly where the artwork places it. ⭐ ⛔ NOT a new aggregation: the SAME
   generic `_rank` the two rankings beside it use, keyed on a pipeline label
   derived from the ENTRY product by the SAME rule the capital path uses
   (`pipeline_for_product`: not-CNC ⇒ intraday). ⛔ **UNKNOWN is its own row** —
   a trade with no ENTRY order has no product, and folding it into Intraday
   would put a fabricated dimension into the one analysis whose job is to
   separate the books.
   🔬 On the REAL-VM snapshot: Intraday 187 · Delivery 84 · UNKNOWN 36 =
   **307 = execution_count exactly**, a true partition.

2. **Symbol became a LEFT label column** (`lbl:true`) — heading AND every data
   cell move together. ⛔ The column SET is unchanged, so `COLS_KEY` stays v1 and
   no operator's saved column order is discarded.

3. **A TRADE STATE filter**, new this window. ⚠️ The screen rendered TWO
   status-like columns and only one was filterable:
     · `status`      = the DELAY BAND (fast/moderate/slow/unmeasured)
     · `trade_state` = the LIFECYCLE position (closed/open/pending/rejected)
   ⇒ *"show me only the CLOSED ones"* was impossible on a screen that displays
   the column. It runs through the SAME single filter gate every panel reads, so
   the KPI deck, table, rankings, distribution, throughput, warnings AND the XLSX
   export narrow together — ⛔ no panel can describe a different population.
   ⭐ The dropdown offers the FULL vocabulary, ⛔ not just the states present this
   period: the option to isolate REJECTED must not vanish on a clean day, which
   is exactly when it is looked for. ⭐ `resetFilters()` hard-codes its set and
   was corrected, or Reset would have left the new filter stuck on.
   🔬 Verified: no filter 260 → CLOSED 173 → OPEN 54 → REJECTED 16, each set
   containing only its own state.

⭐ Also added: **the bottom EXPORT BAR** the artwork carries and S11 lacked
entirely (the four in-panel buttons were the only export affordance). Note left,
`Export Current View` + `Export XLSX` right — the S09 `pnl-exportbar` shape,
scoped to `.exec-page`. ⛔ Live handlers, ⛔ NOT the gated `export_button` macro:
S11 owns a real `/api/export/execution` route.

⭐ And **the last user-facing Scanner wording is gone** — the *"Scanner ranking
omitted"* note was REMOVED outright, not reworded: it existed to explain an EMPTY
footprint, which is now filled. 🔬 Rendered Scanner mentions: **0**.

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S11 IS NOT `VERIFIED LIVE`.** It was approved on **DEMO data** (260
synthetic trades), because the real-VM snapshot was too sparse to judge density —
🔬 202 of its 307 rows never filled, so most timing cells read as dashes. ⭐ The
demo DB lives OUTSIDE the repo and the pointer was reverted byte-identically
(md5 `6a6d93df…`, 0 pointer lines); 🔬 the real DB's mtime is unchanged at
**03-Aug 16:08** ⇒ never opened for writing.

⚠️ **THE 25-COLUMN ARTWORK STILL CANNOT BE BUILT**, and that is unchanged and
correct. 🔬 Re-verified this window: `order_execution_log.exchange_timestamp` is
a real column that is **NEVER WRITTEN** — the sole caller of
`insert_order_execution_log` (`orders/slippage_recorder.py:149`) builds its row
with `order_timestamp` and `fill_timestamp` and **no `exchange_timestamp` key**.
Risk and capital record no timestamp at all; `orders` has ONE instant, not a
create/submit pair. ⇒ 10 of the artwork's columns have no source. They remain
**NOT INSTRUMENTED**, ⛔ never `0.00`. ⛔ `Signal Score` stays retired (L8, the
Screen-04 exception only).

⚠️ **Two differences from the PNG were REPORTED, ⛔ not changed** — they need
👤 Rama's ruling, not a build decision:
  · **KPI cards carry no icons or sub-labels.** The icon needs the SHARED
    `kpi_card` macro, which every screen uses; the `MINTRADAY_001`-style
    sub-labels need a backend field naming WHICH execution was fastest/slowest.
  · **The page title** renders in the house `panel-title` style, ⛔ not the PNG's
    large uppercase. 🔬 S09 and S10 — both approved — use the identical pattern
    (`pnl-h1` / `slp-h1` / `exec-h1`); changing S11 alone would make it the odd
    one out among approved screens.

**Gate:** 🔬 **142 S11 tests pass** (+8 this window). Six pre-existing tests
pinned the OLD design and were updated to the new one — ⭐ the Scanner assertion
got STRICTER, from *"exactly 1 mention"* to **0**. Full dashboard suite:
🔬 **2081 passed, 1 failed** — `test_c_venv_has_no_kiteconnect`, an environment
artifact (the GUI venv does not exist on this worktree); ⭐ it fails identically
on a pristine checkout, so it is ⛔ not an S11 failure.


### Entry 23 — S12 SYSTEM HEALTH APPROVED. ⛔ NOT PUSHED

**Date/time:** 30-Aug-2026, approved ~17:3x IST · **Branch:** `feat/screen10-slippage-analytics`
**Build:** `bb1e0a9` · **Pushed: NO · Deployed: NO**

⭐ A real pass against the original artwork, ⛔ not a cosmetic patch. Six changes,
all inside Screen 12, plus one pre-existing defect found by measurement.

#### The six

1. **THE BAND IS FIVE CARDS** — Overall · Healthy · Warning · Failed · Uptime.
   The **UNKNOWN card is removed**: it was a sixth KPI the artwork does not
   carry. ⚠️ ⛔ **Dropping the CARD must not drop the STATE** — UNKNOWN is a real
   service status, and on a host without `systemctl` it is the *majority* state.
   ⇒ its survival in the **table, the status filter, the legend AND the colour
   map** is pinned by test, so a later edit cannot quietly delete the concept.

2. **Icons and per-status shares restored** — shield · ♥ · ⚠ · ✖.
   ⭐ `pctOf` returns an **em dash, ⛔ never `0.00%`**, when the fleet size is
   unknown: a confident zero would be a fabricated denominator.

3. **VM METRICS NESTED INTO THE UPTIME CARD**, as the artwork draws them; the
   separate rail panel is deleted and the service table takes the full row.
   ⭐ RAM-available has no row in the artwork but IS genuinely measured, so it is
   **kept rather than dropped** — ⛔ losing a real number to a layout change is a
   silent regression. 🔬 All six still route through `gapCell`, so a moved metric
   cannot become a bare number.

4. **DEPENDENCIES BECOME FIVE HORIZONTAL ICON CARDS** in a band of their own —
   five cards do not fit legibly in a third of the page. ⛔ Same binding, same
   five names, same statuses, same probe text on hover; presentation only.

5. ⭐ **THE THROUGHPUT PANEL — present in the PNG, ABSENT from the design TXT.**
   Bound to the **EXISTING `activity_pulse` reader**: ⛔ no new query, ⛔ no new
   table, ⛔ nothing derived from a capped row list (which would make a busy hour
   look calm on exactly the day it mattered).
     · Signals Received ← `signals.received_at`
     · Orders Created   ← ENTRY `orders.placed_at`
     · Orders Filled    ← **`trades.entry_time`**
   ⚠️ **THE THIRD SERIES IS THE FILL, ⛔ NOT AN ORDER STATUS.** A trade row exists
   once its ENTRY actually filled; reading `orders.status` instead would count a
   broker **acknowledgement** as a fill.
   ⛔ **AN EMPTY CHART HERE IS A REAL ZERO, ⛔ NOT AN INSTRUMENTATION GAP.** All
   three tables are read live, so `instrumented` stays **True** and the panel says
   **NO ACTIVITY**. Saying NOT INSTRUMENTED would assert the system cannot count
   its own orders, and ⭐ that assertion would be false. 🔬 Pinned by a test that
   went RED under mutation.
   ⚠️ **ONE DELIBERATE LABEL DEVIATION:** the artwork says **PER MINUTE**. A
   session is ~375 minutes and will not fit that panel, so the per-minute counts
   are **summed into hourly buckets and the panel says `(per hour)`**. ⭐ The
   label follows the AGGREGATION, ⛔ it is not inherited from the drawing.

6. **THE BOTTOM EXPORT BAR**, wired to the **existing** `/api/export/system-health`
   with the page's own filter params — ⛔ not a gated placeholder. A **Throughput
   sheet** was added so the workbook cannot omit a panel the screen shows.

#### ⭐ A PRE-EXISTING DEFECT, FOUND BY MEASUREMENT

**The 13px typography floor was acting as a CAP.** `.sysh-page .num-neg` (and its
siblings) sit at the **same specificity** as `.sysh-page .sysh-ov-v` and appear
**later in the file** — so the moment Alpine put the semantic colour class on the
status word, the artwork's largest text silently collapsed.
🔬 **MEASURED: computed `font-size: 13px` where `1.7rem` was written.** Now 27.2px.
⭐ Fixed by restating the rule one level deeper — ⛔ the floor itself is untouched,
because every other screen depends on it, and `test_b1_typography_floor` still
passes. The same latent bug on `.sysh-up-v` was closed at the same time.

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S12 IS NOT `VERIFIED LIVE`. It was approved on DEMO DATA.**

⚠️ 👤 **30-Aug-2026 IS A SUNDAY — and on weekends and NSE holidays the VM and the
trading system DO NOT RUN; the system sits in SOFT_KILL.** ⇒ `Trading Engine
FAILED`, `Uptime NOT AVAILABLE`, `NOT READY` and every empty series are the
**CORRECT off-market state**, ⛔ not a fault. ⛔ Never open an incident on an
off-market reading. ⚠️ ⛔ Do not confuse that with the **dev-host gap** — every
systemd unit `UNKNOWN` because Windows has no `systemctl` — which ⛔ never resolves
on any day.

⇒ 👤 Rama's standing instruction: *"always show me every screen using vm db's
input data or your own demo data for clean review."* ⭐ An empty screen ⛔ cannot be
reviewed. ⚠️ This **supersedes the Web-Claude S12 ruling**, which said not to build
a demo payload for the Windows gap — ⛔ a card is not Rama.

⭐ **THE FILL CAME FROM AN OUT-OF-REPO REVIEW HARNESS** that patches the **READER
LAYER in-process** and serves the real app on **:8501**, with the honest screen
left up on :8500 for comparison. ⛔ NO repo edit · ⛔ no demo DB · ⛔ no config
pointer · ⛔ **nothing to revert** — kill the process and the demo is gone.
🔬 Verified after the render: exactly the 4 modified files, **0** new untracked.
⭐ Patching at the **reader boundary** was deliberate: every status rollup, the
worst-wins logic, the readiness gates, the semantic colouring and the export all
ran **FOR REAL** on demo inputs — ⛔ a hand-written payload would have proved
nothing about the screen. ⛔ MODE was left at PAPER: a demo server must never
paint the word LIVE on a trading header.

⭐ **WHAT THE FILLED RENDER EXPOSED THAT THE EMPTY ONE COULD NOT:**
  · **Trading Readiness NOT READY** with the live-vs-historical gate working —
    *"preflight judged the system READY at 08:30:41, but that verdict is older
    than this failure"* + a **SUPERSEDED** banner. ⭐ That is the 15-Aug defect's
    fix, and it is **invisible on an empty screen**.
  · An alert reading severity **WARNING** but rendering **GREEN** (*"Tailscale
    connection restored"*) — the semantic-colour rule holding.
  · **TRIGGERED rendering amber**, ⛔ not counted as a recovery.
  · Four different dependency states across the five cards.
  · Both charts drawing: trends (0–27.5, 08:00→15:50) and throughput
    (0–64, 09:00→15:00).

#### Gate

🔬 **117 S12 tests pass (+13 this window).** One pre-existing test pinned the OLD
workbook sheet list and was updated. ⭐ RED-capability proven by **mutation**:
forcing `instrumented=False` turned the throughput honesty tests red; reverted.
Full dashboard suite: 🔬 **2100 passed, 1 failed** — `test_c_venv_has_no_kiteconnect`,
an environment artifact (this worktree has no `ops_dashboard/.venv`, so pytest
runs under the system Python); ⭐ it fails identically on a pristine checkout, so
it is ⛔ not an S12 failure.
🔬 **37 of 37 added CSS selectors are `.sysh-page`-scoped**; `components.html` is
untouched ⇒ ⛔ no other screen's `kpi_card` render moves.

#### ⏸ Reported, ⛔ not changed

· **No uptime percentage** (the PNG shows 99.82%) — ⛔ no such measurement exists.
· **`Last Successful Order` renders the full ISO stamp**, where the PNG shows only
  the time. Pre-existing; ⛔ left alone under the freeze.
· **The PNG's "View All" links** are absent — this build uses real filters and
  pagination instead. Pre-existing, ⛔ not part of this pass.


### Entry 24 — S13 AUDIT APPROVED. ⛔ NOT PUSHED

**Date/time:** 30-Aug-2026, approved ~20:1x IST · **Branch:** `feat/screen10-slippage-analytics`
**Build:** `3fa2e78` · **Pushed: NO · Deployed: NO**

⭐ **S13 WAS ALREADY FULLY BUILT.** Every panel the PNG draws existed and was
bound to real audit readers ⇒ this was a **visual pass**, ⛔ not a rebuild.
⛔ No backend, ⛔ no readers, ⛔ no bindings, ⛔ no export route touched.

#### The three corrections

1. **CRITICAL CHANGES moved out of the table-row rail** to sit beside the lower
   analytics — the relationship the design TXT names verbatim (*"the original PNG
   places this as a right-side panel aligned with the lower analytics area.
   Preserve that relationship."*).
   ⚠️ ⭐ **It is ALSO a measured defect fix.** With a record selected the rail
   stood 🔬 **1474px against a 528px table — 946px of dead space** beside it,
   page height 2457px. After: 🔬 **448px**, page **2074px**.
   🔴 **The void appeared ONLY AFTER a row was clicked** — ⛔ which is exactly why
   an unselected screenshot never showed it, and why the brief's "no unintended
   vertical dead space" check has to be run on a SELECTED record.
   ⭐ The new `.aud-lower` band deliberately **mirrors `.aud-main`**
   (`minmax(0,1fr) 330px`) so the panel lands directly beneath AUDIT DETAILS.
   ⭐ A test pins BOTH rules to the same columns: if they drift the panel stops
   lining up and the whole point of the move is lost.

2. **The bottom export bar** — note-left / buttons-right with the shared
   `.btn-export`, matching S09/S11/S12. ⚠️ The rule was **ALREADY
   `space-between`**; `flex-wrap: wrap` plus the long honesty note wrapped the
   buttons onto a second line at the LEFT, so the bar read nothing like the
   artwork. ⭐ `nowrap` is the load-bearing part, ⛔ not the alignment.

3. **One width for every filter select.** Action rendered ~2× its siblings purely
   because its option strings are longer — ⛔ a control's width must not encode
   its data. 🔬 All five now exactly 168px.

⏸ **REPORTED, ⛔ NOT CHANGED — 👤 Rama's ruling owed:** the artwork puts OLD/NEW in
a right sub-COLUMN of Audit Details. 🔬 Its rail is ~412 CSS px against our 330.
The design TXT specifies the **stack itself** — *OLD VALUE ↓ NEW VALUE* — which is
exactly what renders; only beside-vs-below differs, and widening the rail would
squeeze a nine-column table.

#### ⚠️ SIX REAL INSTRUMENTATION GAPS THIS SCREEN SURFACES

⛔ The demo does **not** paper over any of them.

- 🔴 **WHO changed a setting is NOT CAPTURED ANYWHERE IN THE SCHEMA.** ⇒ the PNG's
  `ramakrishnan` User column ⛔ cannot be built. The column shows the recorded
  **PROCESS** or an explicit gap, and Top Users states *"all recorded actors are
  PROCESSES, not people"* and **EXCLUDES** the unattributed records rather than
  assigning them to anyone.
- **TIMELINE — only ONE instant per record exists.** Applied is real; Requested,
  Validated and Confirmed render NOT INSTRUMENTED. ⛔ Never manufactured.
- **SOURCE** — only an inbound webhook proves its own origin (API).
  Dashboard/Scheduler/Recovery Engine are not recorded.
- **RETENTION PERIOD** — no audit retention policy exists. The real oldest→newest
  span is shown and labelled a **MEASUREMENT**, ⛔ never a policy.
- **CONTROL HISTORY** — `kill_switch_state` is a single row ⇒ current state only.
- **AUTHENTICATION** — no source at all.

⭐ **THE OLD→NEW VALUES ARE GENUINE.** They are produced by the real
`_config_changes` diff of **consecutive config snapshots** — the SAME code path
production uses — ⛔ not written by hand.

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S13 IS NOT `VERIFIED LIVE`. It was approved on DEMO DATA.**
🔬 The real VM snapshot holds **3** audit records across the whole of July–August,
and 30-Aug is a Sunday (system correctly in SOFT_KILL). ⛔ Three rows cannot show
whether a 10-row table, an 8-slice donut, a 7-day chart or a paginator reads
correctly.

⭐ The fill ran through the **out-of-repo review harness on :8501**, feeding **RAW
TABLE ROWS** to the real readers so `build_audit` performed its own diffing,
filtering and aggregation on top — ⛔ no repo edit, ⛔ no demo DB, ⛔ no config
pointer, ⛔ nothing to revert.

#### Gate

🔬 **77 S13 tests pass (+4 this window).** ⭐ RED-capability proven by **mutation**:
restoring `flex-wrap: wrap` and changing the select width turned both new tests
red; reverted. Full dashboard suite: 🔬 **2100 passed, 1 failed** —
`test_c_venv_has_no_kiteconnect`, the environment artifact that fails identically
on a pristine checkout. 🔬 Every added CSS selector is `.aud-page`-scoped, pinned
by a test that walks the added block. ⛔ No console messages; ⛔ no horizontal
overflow.


### Entry 25 — S14 TRADE LOGS APPROVED. ⛔ NOT PUSHED

**Date/time:** 30-Aug-2026, approved ~22:4x IST · **Branch:** `feat/screen10-slippage-analytics`
**Build:** `3885208` · **Pushed: NO · Deployed: NO**

⭐ **S14 WAS ALREADY FULLY BUILT** — 12 panels, all bindings, 83 tests — and ⭐
**Scanner was ALREADY REMOVED, and DEFENDED AT THREE LAYERS**: the rendered page
AND the JSON payload · the export header · **the SQL itself** (`signals.scanner`
exists in the schema and is ⛔ deliberately UNREAD). ⭐ The link to the SEPARATE
**Scanner Attribution** screen is correctly untouched — a different thing, ⛔ not
a leak. ⇒ this was a **visual pass**, ⛔ not a rebuild.

#### 1. ⭐ THE TRADE REPLAY PANEL — built into a MEASURED hole

🔬 **The defect was measured before anything was built:** `.tlg-rail` runs
**543px past** the bottom of `.tlg-row3`, leaving a blank region of
**1222 × 559 px** INSIDE `.tlg-left`, between TRADE TIMELINE / EVENT DETAILS and
the AUTO-RECOVERY / RECENT ERRORS / EXPORT row.

⚠️ ⭐ **That measurement drove the PLACEMENT, and the placement was the whole
correction.** A full-page-width band appended below both columns would have left
the hole **exactly where it was** and ⛔ merely made the page taller. The panel
therefore sits INSIDE `.tlg-left`. 🔬 Dead band **559px → 16px** (the normal grid
gap); panel **1222 × 345**.

⛔ **NO NEW BACKEND.** The eight stages ARE `detail.timeline` — the same ones the
TRADE TIMELINE above renders, from the same detail response. ⭐ The service's own
docstring already anticipated it: *"Replay reconstructs the REAL lifecycle: each
stage is filled only from a real stored timestamp."*

⭐⭐ **THE ANIMATION STEPS ONLY THROUGH `measured` STAGES.** That ONE rule makes a
fabricated transition **structurally impossible** rather than merely
discouraged — an unmeasured stage is never a step — and it delivers all three
required behaviours with ⛔ no special-casing:
🔬 **VERIFIED LIVE ON TWO TRADES:**
  · **completed** — stepped `Signal Received → Validation → Order → Fill →
    Position → Exit`, ⭐ **SKIPPING Risk and Capital** because neither has a
    timestamp.
  · **zero-fill `TRD-2026-000807`** — **Order 10:58:34 marked TERMINAL**;
    Fill and Position **not reached**; Exit **pending / position still open**.
    ⭐ RECENT ERRORS corroborates at the same instant: *"Cancelled with zero fill
    on timeout"*.

⭐ **Four states drawn distinctly, because they MEAN different things:**
recorded · **NOT INSTRUMENTED** (carrying the system's own reason) · not
reached · currently replaying.

#### 2. The lower row rebalanced — ⚠️ and my first attempt was worse

AUTO-RECOVERY HISTORY and RECENT ERRORS were **both clipped** ("RECOVERE…",
"RESOLU… STATUS", "NOT INSTR…") while the EXPORT panel held `.8fr` it does not
use.
⚠️ ⭐ **RECORDED HONESTLY: the first fix was worse than the defect.** Wrapping the
cells cleared the clip but turned every RECENT ERRORS row into **three lines**.
🔬 Measured, reverted, kept the **width change only** — each panel retains its own
`overflow-x: auto`, the house pattern for wide content. ⛔ The honest wording was
never shortened and ⛔ the 13px floor was never breached.

#### 3. An S13 test of mine, fixed at root

S13's *touched-no-other-screen* check scanned to **END-OF-FILE**, so the very
next screen to append a correctly-scoped block failed it — 🔬 S14's
`.tlg-page .tlg-row4` did exactly that. ⭐ **The WINDOW was wrong, ⛔ not the
assertion**; it is now bounded to its own block and ⭐ re-proved RED-capable.

#### ⭐ THE EVIDENCE SOURCE — the brief's *VM > review*

🔬 Inspected `/home/ubuntu/systems/trading-system/reports/log_review/eod_review_2026-08-28.md`
(the latest available) and **reproduced** it. ⭐ The payload's counts land on the
file **exactly**: **Signal Received 4,686 · Signal Accepted 16 · Capital Rejected
58 · Order Created 16 · Position Closed 5 (TGT 1 / SL 3)** — plus the real
symbols, the real rejection reasons, both slippage-guard figures verbatim, and
both kill-switch instants. ⭐ **The REAL 28-Aug date is kept** — it falls inside
S14's own default 7-day window, so ⛔ no re-dating was needed.

⚠️ ⭐ One demo-data defect I caused and fixed: my first generator placed
*Signal Received* AFTER its own *Order*. ⛔ A forensic console showing that is not
reviewable. Signals are now derived BACKWARDS from the order they produced.

#### ⚠️ SEVEN GAPS THE SCREEN SURFACES — ⛔ the demo does NOT paper over them

- 🔴 **NO error-code scheme exists in this system** — a repo-wide search for an
  `XXX-0000` code returns **ZERO** ⇒ the artwork's `EXCH-1016` / `RISK-2001` /
  `CAP-3002` ⛔ **CANNOT be built**. Renders NOT INSTRUMENTED.
- 🔴 **A PASSING risk or capital gate writes NO ROW** ⇒ *Risk Passed* and
  *Capital Passed* show **NOT INSTRUMENTED, ⛔ never 0** — ⭐ a 0 would wrongly
  read as *nothing ever passed*.
- **Risk and Capital have no timestamp of their own** ⇒ the timeline reads
  *"n of 8 stages recorded"* and names the two gaps.
- ⛔ **No resolution state** ⇒ the artwork's *Resolved* column has no source.
- ⛔ **No *Recovered In*** ⇒ the artwork's *18 sec* cannot be built.
- ⛔ **No retention policy** on signals/trades/orders ⇒ the artwork's *67 Days* is
  NOT INSTRUMENTED. ⚠️ The log file's own *"90 days"* governs OTHER tables — ⭐ the
  service is right to refuse it.
- ⛔ **No human attribution anywhere** ⇒ User is NOT INSTRUMENTED.

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S14 IS NOT `VERIFIED LIVE`.** It was approved on data **reproduced from the
28-Aug log review** through the out-of-repo harness on **:8501** — ⛔ no repo
edit, ⛔ no demo DB, ⛔ no config pointer, ⛔ nothing to revert.

#### Gate

🔬 **89 S14 tests pass (+6 this window)**, pinning the replay's honesty rules —
placement inside `.tlg-left`, reuse of `detail.timeline` with ⛔ no fetch of its
own, measured-only stepping, ⛔ no timestamp it does not have, the honest empty
state, and ⛔ no Scanner / ⛔ no unscoped CSS. ⭐ RED-capability proven by
**mutation**: letting the replay step through EVERY stage turned the guard red;
reverted. Full dashboard suite: 🔬 **2104 passed, 1 failed** —
`test_c_venv_has_no_kiteconnect`, the environment artifact.

⚠️ **A nuisance, ⛔ not a page defect:** screenshot capture timed out three times
on this screen. It carries **9,428** client-side rows, which strains the renderer
during capture; ⭐ the page itself stayed responsive and every measurement came
back clean.


---

### Entry 26 — S15 SYSTEM LOGS APPROVED. ⛔ NOT PUSHED

**Date/time:** 01-Sep-2026, approved ~11:2x IST · **Branch:** `feat/screen10-slippage-analytics`
**Build:** `7ec180a` · **Pushed: NO · Deployed: NO**

⭐ S15 was already built and had been given the wide-page fix in `0ca38e2`. 👤 Rama
returned it with three corrections. ⭐ **The first turned out not to be a missing
feature at all, but a live defect that no test could see** — and finding that is
the substance of this entry.

#### 1. 🔴 EVENT TYPE / STATUS WERE BLANK — a REAL DEFECT, ⛔ not an unbuilt feature

The `NOT INSTRUMENTED` markup **was already in the template**. Each
`<template x-if>` held **TWO sibling spans** — the value span and the gap span.
🔬 **Alpine 3.14.1 builds an `x-if` branch with
`content.cloneNode(true).firstElementChild`** — it renders **ONLY the first root
and DISCARDS every later sibling**. So the gap span was **never created**, and
the survivor carried `x-show="r.status"`, which is `display:none` **exactly when
the value is missing**.

⭐⭐ **The one thing this screen must never do — show a blank where a gap belongs —
is what it did, and it did it SILENTLY:** ⛔ no error, ⛔ no console warning, ⛔ no
failing test. ⚠️ The markup read correctly to a reviewer; only the render was wrong.

🔬 **MEASURED A/B on TWO live instances against the same VM extract** (`:8501` at
pristine `0ca38e2`, `:8500` with the correction):
**BEFORE 20 of 20 Event Type / Status cells BLANK → AFTER 0 of 20.**

⭐ **SWEPT THE CLASS, ⛔ not the instance:** all **33 templates** scanned for
multi-root `x-if` blocks. 🔬 **Exactly two exist and both are these** ⇒ ⛔ no other
screen is affected. A new test pins the rule so it cannot return.

⭐ **Real values are untouched:** a Scheduler row still shows its real
`Status = Success`, and `RECOV-7903` shows a real `Event Type = Recovery
Completed`. ⇒ the screen distinguishes *"recorded"* from *"not instrumented"*,
which is the whole point.

#### 2. ⭐ COMPONENT TIMELINE — the PNG's HORIZONTAL diagram, restored

It was a vertical `<ol>`. ⛔ That is precisely what the binding TXT forbids:
*"Do not replace diagrams/pictorial elements with plain text when the original
design shows a visual component."* ⭐ The TXT's `Event Detected ↓ Logged ↓ Action
Taken ↓ Resolved` is its ASCII rendering of the SAME four stages; **SOURCE
AUTHORITY 1 (the PNG) is binding for composition.**

Now four circular pictorial nodes on one connecting rail, stage label under each
node, timestamp under the label, footer strip. ⛔ **NO new backend, ⛔ no new
data** — same `detail.timeline`, same `measured` flag, same gap reasons (now on
the node's `title`). ⭐ The rail is a `::before` on every stage after the first,
drawn from the previous node's centre, so it is positioned **BY** the nodes and
⛔ cannot drift out of step with them.

⛔ **THE PNG's "Duration: 2 sec" HAS NO SOURCE HERE and says NOT INSTRUMENTED.**
🔬 The stages that ARE measured share the single log timestamp, so subtracting
them would have produced a **manufactured `0 sec` dressed as a measurement**.

⚠️ **Two layout defects found by MEASURING, ⛔ not by eye:**
- 🔬 the shared `.slg-ni` `white-space: nowrap` made all four labels overrun a
  **102px** column (the label needs **123px** on one line) and collide into one
  run-on block ⇒ the nowrap is lifted **only inside the timeline**, where the
  longest word needs **94px** and fits.
- 🔬 then row 3 moved to **the artwork's own proportions** — COMPONENT TIMELINE is
  the widest panel in the PNG, **~1 : 1.34 : 1 : 1** — taking the stage column to
  **123px**. ⛔ No word shortened to a dash, ⛔ nothing below the 13px floor.

#### 3. ⭐ SEARCH + EVENT TYPES — INTEGRATED into FILTERS, ⛔ not deleted

⭐ **Compared against BOTH sources first, as the correction required.** 🔬 The
**PNG places NEITHER panel**: its search is one box in the table toolbar (kept),
and Event Type is a **filter dropdown**. 🔬 The **TXT** lists a SEARCH section and
asks to *"preserve the investigation/search **capability**"* — a capability,
⛔ not a full-width block — and lists **no EVENT TYPES section at all**.

⇒ all five approved fields move into the control block the artwork **does** draw,
unchanged: Service Name · Module · Error Code · Message · Reference ID, still
CONTAINS matches, still separate from the exact-match dropdowns. ⛔ **ERROR CODE
stays SHOWN and DISABLED** — no error-code scheme exists in this system.

⭐ The eleven event-type counts survive too, compacted from an 11-row table to a
chip strip under their own dropdown, **keeping the distinction that matters**:
🔬 `Service Restarted 0` is **instrumented and genuinely zero**, while
`Connection Lost NOT INSTRUMENTED` is **unmeasurable**. ⛔ A 0 is never shown for
the latter.

⛔ `.slg-rowx` and both panel classes are **REMOVED, ⛔ not emptied** — no dead
selectors linger. 🔬 **Page height 2158px → 1941px (−217px)**; the removed row was
**438px**, the FILTERS panel grew **125px → 346px**.

#### 4. ⚠️ A REGRESSION FOUND ON ARRIVAL — the suite was ALREADY RED

🔴 Before any of this work, the S15 suite stood at **8 failed / 72 passed**.
🔬 `0ca38e2` changed the page root to `class="dash-page slg-page"`, and the test
helper hard-coded the literal `'<div class="slg-page"'`. ⭐ It matched nothing,
returned **-1**, and took **EIGHT tests** down with it — every one reporting
*"the system-logs page root is missing"* rather than the real change.
⇒ the matcher is now **class-aware**, so adding a class alongside cannot blind it
again. ⭐ **The width fix itself is untouched**, per 👤 Rama's instruction.

#### 5. ⭐ THE EVIDENCE — the LATEST real VM data, per the brief's HARD RULE

🔬 Today's dated `system_`/`reconciler_`/`trades_` logs (**28-Aug…01-Sep**) plus
`system_events` / `cron_heartbeat` / `reconciliation_log` extracted **READ-ONLY**
from the live **428 MB** VM DB (⛔ the DB itself was never copied; ⛔ nothing was
written on the VM outside `/tmp`).
🔬 **11,656 events · Info 11,565 (99.22%) · Warning 83 (0.71%) · Error 8 (0.07%) ·
Critical 0 · last event 10:49:24 TODAY.**
⭐ **Severity is genuinely Info-dominated and is ⛔ NOT reshaped toward the PNG's
55%.** ⛔ No Scanner added anywhere.

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S15 IS NOT `VERIFIED LIVE`.** It was approved on a **READ-ONLY VM EXTRACT**
rendered locally through the git-ignored `gui_config.local.yaml` pointer —
⛔ no repo edit, ⛔ no demo DB, ⛔ nothing to revert.

⚠️ These remain genuinely **NOT INSTRUMENTED** and the screen says so rather than
inventing values: **Trading Impact** (nothing classifies it) · **Error Code** (no
scheme exists repo-wide) · **Resolution Time / Resolved At / Recovery Duration**
(not stored) · **Event Detected** (a log line is the first record of itself) ·
per-engine health (the engines are THREADS inside one systemd service —
Screen 12's ruling).

#### Gate

🔬 **S15 82 passed, 0 failed** (from **8F / 72P**), including **two new guards** —
one pinning the Alpine single-root rule, one pinning the horizontal diagram.
🔬 Full dashboard suite **2112 passed, 1 failed** = `test_c_venv_has_no_kiteconnect`,
⭐ **proven environmental by running it against a PRISTINE checkout of HEAD, where
it fails identically** — it shells out to `pip show` and ⛔ cannot see HTML or CSS.
⭐ RED-capability proven for the blank-cell audit **by injecting a blank cell**,
which the probe caught, and for the console check **by emitting a probe warning**,
which it captured; ⇒ ⛔ neither zero was vacuous.


---

### Entry 27 — S18 LIVE ACTIVITY APPROVED. ⛔ NOT PUSHED

**Date/time:** 01-Sep-2026, approved ~13:0x IST · **Branch:** `feat/screen10-slippage-analytics`
**Build:** `f9d5fe3` + `f860f82` · **Pushed: NO · Deployed: NO**

⭐ S18 was already substantially built, with its decisions recorded: informational
only, MTM/LTP gaps declared, Scanner absent. 👤 Rama's correction asked for the
artwork's row spans and the removal of a 🔬 measured dead band. It took **TWO
passes**, and the second one **overturned my own explanation** — which is the
part worth keeping.

#### 1. ⭐ THE ARTWORK'S THREE BANDS (`f9d5fe3`)

🔬 The work area was two independent strips carrying four panels each. The PNG
draws **three bands**: band A is two strips (feed ǀ pipeline · strategy); bands
B and C then run **ACROSS BOTH**, x205→1240 —

    A  feed                    ǀ pipeline · strategy
    B  winners ǀ system events ǀ feed filters
    C  active positions        ǀ capital utilization

⇒ **FEED FILTERS** moved into band B and **CAPITAL UTILIZATION** into band C,
both bands given `grid-column: 1 / -1` at the artwork's own ratios (B 1.4:1:1
from 415:290:300; C 2:1 from 680:340).

⚠️⚠️ **AN EARLIER SESSION HAD BUILT FULL-WIDTH ROWS AND RECORDED THEM REJECTED**,
for a real mechanism: a grid row is as tall as its tallest cell, so a short feed
left a band beneath it. ⭐ **That mechanism is ADDRESSED, ⛔ not ignored** — only
`.lav-main` stretches, and the feed's own scroll WINDOW takes the slack, so band
A closes on **REAL ROWS becoming visible** (430px → 560px against 540 stored
events). 🔬 feed 694, pipeline+strategy 694, **difference 0**.

#### 2. 🔴 THE RESIDUAL BAND — AND MY OWN WRONG EXPLANATION (`f860f82`)

After pass 1 a **150px** band remained under RECENT WINNERS / LOSERS. ⛔ I had
explained it as unavoidable: *"stretching would only relocate the emptiness
inside the cards."* 🔴 **THAT WAS WRONG, and only MEASURING THE ARTWORK showed
it.**

🔬 **PIXEL-SCANNED THE PNG, ⛔ did not estimate it.** Three blank columns, one
inside each band-B panel — **x612** (winners), **x918** (system events),
**x1235** (feed filters) — ⭐ **ALL THREE return the SAME border rows: top
y=586, bottom y=785.** ⇒ the artwork's three band-B panels are **EXACTLY equal
height, 199px**. Band C is the same shape, 797→969.
⇒ ⭐ **The artwork STRETCHES its bands, and refusing to is what left the gap.**

⭐ **The scale also proved the tall panel was never the problem.** The PNG's left
content band is x202–1247 = **1045px** against our **1961px** ⇒ **×1.877**. The
artwork's 199px band scales to **373.4** — and SYSTEM EVENTS measures **375**
here. ⇒ SYSTEM EVENTS was correctly sized all along.
⚠️ The `align-items: start` this replaced came from a **BAND-A** lesson that had
been **over-generalised** to the bands.

**Three changes, every figure read off the artwork:**
- `align-items: stretch` on `.lav-rowb` / `.lav-rowc`.
- 🔬 **WINNERS card footprint 174 → 218px** — the artwork's card band runs
  y634→750, so 116px × 1.877. ⚠️ The first pass used 174 from a 1.66 scale that
  was **inferred**, ⛔ not measured.
- 🔬 **FEED FILTERS tile footprint 149 → 240px** — the artwork's two tile rows
  run y629→686 and y701→757, so 128px × 1.877. ⭐ Our tiles were genuinely
  **undersized**, and that is what put the slack there once the band stretched.

⭐⭐ **IT FIXED THE GEOMETRY RATHER THAN HIDING IT** — the test 👤 Rama set. 🔬 The
space BELOW the winners cards measures **157** against the artwork's **155.8**,
and the card-to-panel ratio is **58% in both**. Band B 375 vs 373.4 · card 218
vs 217.7 · tile block 240 vs 240.2 · filters slack 135 vs 133.2 — ⭐ all within
~2px.

🔬 **RESIDUAL DEAD BAND 206px → 150px → 0px.** Band B tops all **1006**, bottoms
all **1381**; band C **1397→1690**; ⛔ no horizontal overflow, ⛔ no clipping.

#### ⚠️ WHAT IS NOT CLOSED, and is REPORTED rather than padded

🔬 Band C measures **293** against the artwork's **322.8**. ⭐ Both its panels are
equal and full so there is **no gap**; forcing the difference would need
arbitrary padding, which the brief forbids.

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S18 IS NOT `VERIFIED LIVE`.** 👤 At Rama's instruction the approval render
fills **ALERTS BANNER** (1 CRITICAL + 2 WARNING) and **ACTIVE POSITIONS** (4
rows, one PARTIAL) with **DEMO** data, held **ENTIRELY in the out-of-repo review
extract** — ⛔ no repo edit, ⛔ no demo DB in the tree, ⛔ nothing to revert.
🔬 A `grep` over `*.py`/`*.html`/`*.css` finds none of it in the repo.
⚠️ 🔬 **I caught one incoherence in my OWN demo before showing it:** the first
sizing drove capital to **90.33%** utilised, because the figures resolve against
the day's **REAL** opening capital — one INIT row, **₹10,469.40 at 08:15:11** —
⛔ not the ₹18,469 I had assumed. Resized to the artwork's own split,
**₹4,414.80 (42.17%) / ₹6,054.60 (57.83%)**.

⭐ **THE REAL DATA IS REAL:** `signals` / `orders` / `trades` / `fm_ledger` /
`telegram_alerts` / `kill_switch_state` pulled **READ-ONLY** from the live VM DB
at **11:41:02 today** — 🔬 787 signals, 533 feed events, 2 closed trades, 88
ledger rows. ⛔ The 428 MB DB itself was never copied.

⚠️ Still genuinely **NOT INSTRUMENTED** and saying so: **CURRENT MTM** and
**LTP / MTM (₹) / MTM (%)** (Pending Broker Source G4 — no live price), the
**open-positions delta** (no stored history), and **Broker Reconnected /
Database Warning** (nothing writes them).

#### Gate

🔬 **S18 116 passed** — unchanged from the pre-change baseline. Full dashboard
suite 🔬 **2112 passed, 1 failed** = `test_c_venv_has_no_kiteconnect`, ⭐ proven
environmental against a pristine checkout of HEAD.
⭐ **FOUR guards updated, and they stay guards:** the two-strips test became a
three-bands test pinning band membership, the `1 / -1` spans and the artwork's
ratios; the collapsed-order test now expects `feed/pipe/strat/rowb/rowc`; the
artificial-height guard now reads each rule's **SUBJECT** rather than any
ancestor (a height applies to the element a rule SELECTS, so a card footprint is
no longer forbidden for the accident of its selector path); and it now asserts
the bands **DO** stretch, carrying the artwork measurement that justifies it,
while still asserting band A is ⛔ not a full-width row.

⛔ **Scanner absent** — 0 occurrences in rendered text, class names and
attributes. ⛔ **S16 and S17 untouched** — 👤 held by Rama's 01-Sep decision to
build them LAST.

⚠️ **A NUISANCE, ⛔ not a page defect:** screenshot capture timed out repeatedly
mid-session. 🔬 The feed renders **all 540 records** into the DOM (`feed_page: 15`
comes back in the payload but the template does not use it), and that DOM size
defeats the capture injector — the same strain Entry 25 recorded for S14.
⭐ JS evaluation stayed responsive throughout and every measurement came back
clean. 🏷️ **OPEN for 👤 Rama:** whether the feed should page client-side.


---

### Entry 28 — S19 STRATEGY RANKING APPROVED. ⛔ NOT PUSHED

**Date/time:** 01-Sep-2026, approved ~13:4x IST · **Branch:** `feat/screen10-slippage-analytics`
**Build:** `f1e97a9` · **Pushed: NO · Deployed: NO**

⭐ 👤 The brief invoked the **PRE-BUILD REVIEW GATE** — S19 is business/signal-path
adjacent — so the investigation came first and ⛔ no code was touched until it
had cleared. ⭐ **It cleared as UI-ONLY**, and that verdict is the entry's point.

#### 1. ⭐ WHAT ALREADY EXISTED — verified at `d5130bb` with file:line

⭐ **The ranking engine is complete and correct, and it is NOT a parallel engine.**
`services/strategy_ranking.py` owns the composite score (`_score` :93) in the
artwork's own **35/25/20/20** weights (`SCORE_WEIGHTS` :56), the trend
(`_classify_trend` :142) against a **real previous equal-length window**, the six
KPIs (`_kpi` :324) and the five modes (`MODES` :48). 🔬 It reads
`db_reader.closed_trades_range` :172 and aggregates through
`analytics_period._aggregate` :174 ⇒ ⭐ **it REUSES the existing engine**; ⛔ no
duplicate, ⛔ no shadow calculation. Export = `/api/export/strategy-ranking`
(`analytics2.py:550`).
⭐ Column drag already worked (`colDragMixin()` `strategy_ranking.html:313`,
`draggable="true"` :136) and horizontal scroll was already contained to the wrap.

⚖️ **A DATED RULING FOUND AND RESPECTED:** the table carries **16** headers against
the spec's 15. The extra **TRADE TYPE** column is a **recorded prior instruction**
(template header: *"inserted immediately after Strategy exactly as instructed"*),
sourced from the strategy's own YAML `intent` through `strategy_meta.py`, and
⛔ explicitly NOT the order product. ⇒ ⛔ left alone, ⛔ not reported as a deviation.

#### 2. 🔴 THE ONLY GAP — THE TABLE-BEHAVIOUR CONTRACT

🔬 `.sr-tbl-wrap` carried `overflow-x` **alone** (`style.css:4486`) with **no height
bound**, so the wrap grew to fit every row and the **WHOLE PAGE** scrolled —
**1353px against a 1264px viewport** — just to reach rows 13-16. 🔬 The header
computed `position: static` ⇒ ⛔ nothing was frozen. ⭐ Real data returns **SIXTEEN**
strategies, so this genuinely bit.

⭐ **REUSED THE ESTABLISHED PATTERN, ⛔ did not write a second one:** S11's
`.exec-rank-scroll thead th` (`:2850`) and S18's `.lav-feed thead th` (`:4081`)
already do sticky-header-over-bounded-scroll.

🔬🔬 **491px IS MEASURED AT SUB-PIXEL, AND THE ROUNDING MATTERED.** thead
**30.92px**, each row **38.33px** ⇒ row 12's bottom edge sits at **490.92**.
⚠️ Rounding to 31 and 38 gives **487**, and 487 shows only **ELEVEN** rows —
⭐ on this table a 4px arithmetic error costs a whole row. ⇒ the figure is taken
from the rendered box, ⛔ never from integer arithmetic.
⛔ `max-height`, ⛔ never `height`: a day with fewer than twelve strategies must
⛔ not open an empty region under the last row (S11's own choice). ⛔ No
pagination substituted, ⛔ no row truncated — all sixteen stay reachable.

#### 3. ⭐ VERIFIED IN THE BROWSER, ⛔ not from the source

🔬 **12 rows fully visible** · wrap **491 client vs 645 scroll** ⇒ the body scrolls ·
**the header's top does not move** across a full scroll to the bottom, which
reveals **ranks 13-16** · **column drag still works with the sticky header** (the
real HTML5 handlers were driven: TRADES moved 3→5, sort still fired, order
restored) · page **1353 → 1199** · ⛔ no page-wide horizontal overflow.

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S19 IS NOT `VERIFIED LIVE`.** ⚠️ The default *Today* window holds only **2**
completed trades and reads as dashes. ⭐ The approval render therefore used **the
screen's OWN Date Range filter** — *This Month*, **117 REAL completed trades
across 13 of 16 strategies** (2026-08-03→09-01). ⛔ No demo rows, ⛔ no config
pointer, ⛔ nothing fabricated, and ⛔ the default period is unchanged in code.

#### Gate

🔬 **S19 75 passed** (74 baseline + the one new guard). ⭐ **RED-CAPABILITY PROVEN BY
MUTATION:** turning `max-height` into `height` turned the guard red, and dropping
`position: sticky` turned it red; both reverted, green restored.
🔬 Full dashboard suite **2113 passed, 1 failed** = `test_c_venv_has_no_kiteconnect`,
the environment artifact — ⭐ the passed count rose **2112 → 2113** by exactly the
guard added, ⇒ ⛔ zero regressions.
⛔ Scanner absent (0 in text, classes, attributes). ⛔ S16/S17 untouched — 👤 held to
be built LAST. ⛔ Paper/live parity untouched — no mode-specific path added.
🔬 `git diff --name-only` returned **nothing** under `backend/`, `services/`,
`readers/` or `api/` ⇒ ⭐ the UI-only verdict is measured, ⛔ not asserted.


---

### Entry 29 — S20 STRATEGY HEALTH APPROVED. ⛔ NOT PUSHED

**Date/time:** 01-Sep-2026, approved ~14:2x IST · **Branch:** `feat/screen10-slippage-analytics`
**Build:** `a2fe2e4` · **Pushed: NO · Deployed: NO**

⭐ 👤 Two changes were asked for — a serial column and S19's table interaction —
and the investigation ran first. ⭐ **Both proved UI-ONLY.** 🔬 `git diff
--name-only` returned **nothing** under `backend/`, `services/`, `readers/` or
`api/`, and every CSS selector added is scoped to `.sh-page` ⇒ the verdict is
**measured**, ⛔ not asserted.

#### 1. ⭐ WHAT ALREADY EXISTED — verified at `file:line` first

⭐ The backend already satisfies the spec, so ⛔ nothing was rebuilt:
· the health score is the **real weighted composite** at exactly **Activity 30 /
Signal Quality 25 / Acceptance Rate 20 / Trade Activity 15 / Errors 10**
(`strategy_health.py:57-61`) — ⛔ not a status lookup;
· **silent detection reads `gui_config.silence.yellow_max_min` = 120 min =
2 Hours** and is **READ-TIME ONLY, ⛔ nothing written** (`:186-203`) ⇒ ⭐ the
read-only dashboard boundary holds;
· **"Scanner Offline" keeps the artwork's exact label** under a recorded 16-Aug
ruling (`:64`, `:550`) and is **derived**, ⛔ not invented;
· export is `/api/export/strategy-health` (`analytics2.py:594`);
· column drag already worked.

#### 2. ⭐⭐ THE SERIAL COLUMN IS NOT DATA — that is the whole point

It renders the row's **INDEX IN THE CURRENTLY SORTED, FILTERED SET** through
`x-for="(r, i)"` ⇒ ⛔ never read from the row, ⛔ never stored, so re-sorting or
filtering **renumbers 1..n on the spot**.
🔬 **VERIFIED IN THE BROWSER:** sorting by health score kept the serials
**1,2,3,4** while the STRATEGIES beneath them changed; filtering to Disabled
gave **`1`**, to Silent **`1..15`**, unfiltered **`1..16`**.
⛔ It carries `nosort` — sorting BY a row number would sort by the very display
order the sort produces. ⭐ It stays **draggable**, so column-order interaction
is unchanged.

⚠️⚠️ **THE `COLS_KEY` BUMP TO v3 IS NOT COSMETIC.** A stored **v2** order lists the
old ELEVEN keys and `initCols` **appends anything missing**, so a returning
operator would have found the new **`#` column at the FAR RIGHT** instead of
first. ⭐ That is precisely the **Screen-14 lesson the template's own v2 note
already records** — the same trap, one screen later.

#### 3. ⭐ THE FREEZE-PANE, ON THIS SCREEN'S OWN FOOTPRINT

Reuses S11/S18/S19 rather than a **fourth** scrolling implementation.
⭐ **FOURTEEN rows, ⛔ not S19's twelve** — this artwork draws 14 and says
*"Showing 1 to 14 of 14"*. 🔬 **546px MEASURED AT SUB-PIXEL**: thead **32.00** +
14 × **36.67** ⇒ row 14's bottom at **545.33**. ⭐ S19's rounding lesson
**applied rather than repeated** (there, rounding showed 11 rows instead of 12).
⛔ `min-height: 350px` is **KEPT** — the empty-day footprint, a different job.
⛔ **S20 ONLY** — 👤 the global table rule stays deferred until every screen is built.

🔬 **VERIFIED IN THE BROWSER, ⛔ not from source:** 14 rows visible · wrap **546
client vs 619 scroll** · **the header's top does not move** across a full scroll,
which reaches **row 16** · column drag works with the sticky header · the serial
header **refuses to sort** · page **1485 → 1412**.

⚠️ **A WIDTH I DID NOT CLAIM.** The 1440px check ⛔ could not be done as a resize —
the browser sits at **75% page zoom**, so the CSS viewport never changed.
⇒ ⭐ rather than report a width that was not really rendered, the **MECHANISM**
was tested: squeezing the panel to **1400 / 1100 / 900px**, the table clamps to
its **1258px** min-width and the **WRAP** scrolls horizontally while the **PAGE
never overflows**, header sticky at every width.

#### 4. ⚠️ TWO EXISTING TESTS NEEDED REPAIR — and both are left SHARPER

· `APPROVED_COLUMNS` now leads with **`#`**.
· 🔴 the heading-fits-on-one-line test matched `.sh-page .sh-tbl th` **BY PREFIX**,
which silently grabbed the new `thead th` rule and asserted `nowrap` against it.
⭐ The match is now **exact**; ⛔ the property is unchanged.

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S20 IS NOT `VERIFIED LIVE`.** ⚠️ It was approved on a genuinely **QUIET** real
day — 🔬 15 Silent / 1 Disabled, 787 signals, 6 trades — and ⭐ that is exactly
what the screen shows. ⛔ No demo rows, ⛔ no config pointer, ⛔ nothing fabricated.

⚠️ Still **NOT INSTRUMENTED** and saying so: **HEALTH TIMELINE** (*"no state
history is stored"*) and **RECENT HEALTH EVENTS** (*"state transitions are not
recorded"*). ⛔ The artwork's *"(Example)"* timeline was **NOT** turned into
production history.
⛔ **Scanner is not duplicated** — 🔬 exactly ONE occurrence, the approved
**"Scanner Offline"** label; ⛔ no Scanner column, ⛔ no Scanner filter.

#### Gate

🔬 **S20 102 passed** (100 baseline + 2 guards). ⭐ **RED-CAPABILITY PROVEN BY THREE
MUTATIONS:** moving the serial out of first position, reading it from the row
instead of the loop index, and dropping the 14-row bound each turned a guard
red; all reverted.
🔬 Full dashboard suite **2115 passed, 1 failed** = `test_c_venv_has_no_kiteconnect`,
the environment artifact — ⭐ the passed count rose **2113 → 2115** by exactly the
two guards added ⇒ ⛔ zero regressions.
⛔ S16 and S17 untouched — 👤 held to be built LAST. ⛔ No global/shared CSS rule
touched.


---

### Entry 30 — S21 SCANNER ATTRIBUTION APPROVED. ⛔ NOT PUSHED

**Date/time:** 01-Sep-2026, approved ~16:0x IST · **Branch:** `feat/screen10-slippage-analytics`
**Build:** `921969c` · **Pushed: NO · Deployed: NO**

⚖️⚖️ **THIS ENTRY EXISTS FOR ONE REASON: A DATED RULING OF RAMA'S WAS REVERSED**,
and a reversal must be recorded where the next reader will meet it, ⛔ never
silently applied.

#### 1. ⚖️ THE 16-Aug RULING, AND ITS 01-Sep SUPERSESSION

📄 The template AND the service both carried: *"⛔⛔ NO SCANNER COLUMN IN THE MAIN
TABLE (Rama, 16-Aug). Scanner and Strategy are 1:1 — 🔬 measured: 16 scanners onto
16 distinct strategies, each named after its strategy — so the artwork's
`Scanner` column is dropped"*, with the identity surviving in SCANNER MAPPING.

👤 **The 01-Sep contract reverses it and answers that reasoning head-on:** *"Keep
the word Scanner wherever it is meaningful in this screen; do not rename or
remove the Scanner concept merely because it maps 1:1 to Strategy."*

⚠️⚠️ **THE 1:1 MEASUREMENT ITSELF STILL HOLDS — it was never wrong, only its
CONCLUSION was overturned.** ⇒ the Scanner cell reads the **SAME row's own
`scanners` list**, which the payload has carried all along: ⭐ one identity shown
twice by request, ⛔ NOT a second dataset.
🔬 **VERIFIED ON THE RENDERED SCREEN: 16 of 16 rows satisfy `scanners ==
[strategy]`, zero divergence.**
⭐ And what the old rule was REALLY protecting — that Scanner must never become a
second independent dataset — is now guarded by **its own test** rather than by
the column's absence. ⭐ That is the durable part; the column was only ever the
means.

#### 2. ⭐ THE TWO COLUMNS

⭐ **`#` IS A SERIAL, ⛔ NOT THE PAYLOAD'S `rank`.** It is the row's index in the
CURRENT sort, so re-sorting renumbers 1..n on the spot — 🔬 confirmed in the
browser: sorting by Signals kept the serials **1,2,3,4** while the SCANNERS
beneath them changed. ⛔ It carries `nosort` (sorting BY a row number sorts by the
order the sort itself produced); ⭐ it stays draggable; ⭐ medals follow the top
three POSITIONS as the artwork draws. ⛔ `rank` is untouched and still drives
SCANNER RANKING.

⭐ **TRADE TYPE NEEDED NO NEW CALCULATION** — it was already in the payload, the
strategy's own YAML `intent` through the ONE shared `strategy_meta` path Screens
19 and 20 use. ⛔ Never inferred from the scanner's NAME, which the contract
forbids in as many words. 🔬 Rendered values are only ever `Intraday`/`Delivery`.

⚠️ **`COLS_KEY` → v2, ⛔ NOT COSMETIC.** A stored **v1** order lists the OLD
thirteen keys and `initCols` **appends anything missing**, so a returning
operator would have got Scanner and Trade Type at the **FAR RIGHT and no `#` at
all**. ⭐ Screens 14 and 20 both paid for this; ⭐ **this is the third occurrence
and the FIRST caught before shipping rather than after.**

#### 3. ⚠️ ONE BACKEND FILE, AND ONLY BECAUSE THE CHANGE FORCED IT

`EXPORT_HEADER` is bound to the table's labels **by an existing test**, so adding
columns required the export to follow — which the guidance permits explicitly
(*"unless the requested S21 change requires a correction"*). 🔬 Verified against a
**real downloaded workbook**: header matches, rows carry serial / scanner /
strategy / trade type, all three sheets survive.

#### 4. ⚠️ THREE STALE RECORDS CORRECTED

⛔ Leaving them would have had the codebase assert two contradictory things:
· the **footer note** still told readers *"Strategy is the identity shown; the
  scanner name is in SCANNER MAPPING"*;
· the **service docstring's heading** still read *"NO SCANNER COLUMN IN THE MAIN
  TABLE"* above a body describing its own supersession;
· the **payload still published a `scanner_column` GAP** for a column that now
  exists.
⭐ **A stale explanation is worse than none — it teaches the reader the wrong
model.**

#### 5. ⛔ THE DEFERRED GLOBAL TABLE RULE WAS NOT APPLIED

👤 Rama: the freeze-pane / body-scroll / header-drag rule is a GLOBAL pass after
all 22 screens are built, and ⛔ *"do not treat its absence as an S21 defect."*
⭐ **Its absence was MEASURED, ⛔ not merely skipped:** 🔬 `thead` computes
`position: static`, the wrap has `max-height: none`, it does ⛔ not scroll
vertically, and all 16 rows sit in the viewport. ⛔ No global or shared selector
touched, ⛔ no compensating visual change made.

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S21 IS NOT `VERIFIED LIVE`** — approved on ONE real trading day.
⭐ **THE DATA IS THE 15:54 VM RE-PULL, ⛔ not the 11:41 extract.** 👤 The instruction
forbids stale logs where newer exist, and 🔬 the difference was large: **787 →
4,083 signals**, 2 → 3 closed trades. ⛔ No demo rows, ⛔ nothing fabricated.

#### Gate

🔬 **S21 142 passed** (+1 test, collected twice across the parametrised `v41`/`v42`
fixture). ⭐ Two superseded tests **rewritten, ⛔ not deleted**: the
no-Scanner-column test became the **not-a-second-dataset** guard, and the
workbook test's leftover *"Scanner not in header"* assertion became its opposite.
🔬 Full dashboard suite **2117 passed, 1 failed** = `test_c_venv_has_no_kiteconnect`,
the environment artifact ⇒ ⛔ zero regressions.
⛔ No page-wide horizontal overflow. ⛔ S16/S17 untouched — 👤 held to be built LAST.

---

### Entry 31 — S22 HOLDINGS APPROVED. ⛔ NOT PUSHED

**Date/time:** 01-Sep-2026, approved ~19:5x IST · **Branch:** `feat/screen10-slippage-analytics`
**Build:** `4b08372` · **Pushed: NO · Deployed: NO**

⭐⭐ **THE SCREEN WAS ALREADY BUILT TO THE CONTRACT.** The fifteen columns in the
required order, pagination on the existing `tableMixin` (⛔ no parallel model),
⛔ no Scanner column, ⛔ no serial, Sync Now present but **disabled with its
reason**, and every price-derived figure declared a GAP rather than filled with a
substituted entry price. ⛔ **None of that was touched.** Two defects were found,
and ⛔ **neither was the KPI.**

#### 1. ⭐ A FILTER RETURNS TO PAGE ONE

🔬 Filtering 28 rows down to 21 while `tPage` was **3** rendered *"Showing 21 to
21 of 21 holdings"* and **ONE row** — which a reader takes to mean *the filter
matched almost nothing*, ⛔ not *the page is stale*.
⭐ The rows-per-page select already reset the page and `resetFilters()` already
set `tPage = 1`; ⛔ the five filter controls did not.
⛔⛔ **THE RESET CANNOT LIVE INSIDE `load()`** — the shared refresh binding
`@ops-refresh.window="load()"` fires on **every poll**, so putting it there would
yank a reader back to page 1 mid-read. ⭐ **Both directions are pinned by tests**,
and both were proven red-capable by mutation.

#### 2. ⭐ A STALE STAMP NOW SAYS WHICH DAY — AND THIS IS THE WHOLE STORY

🔬 `hhmmss()` did `slice(11, 19)` and **discarded the date**, so the **18-Aug**
reconciliation drew as a bare `15:45:02` — beside a **green ● CRON dot** — on
01-Sep. ⚠️ A reader necessarily takes that for *today at 15:45*.
⭐ Today keeps the bare time ⇒ ⛔ panel density unchanged in the normal case; the
date appears **ONLY when it carries information**.

#### 3. ⚖️ THE KPI WAS NEVER WRONG — 👤 AND NO NUMBER WAS CHANGED TO MAKE IT AGREE

👤 Rama asked whether `System Holdings = 0` beside a row reading `System Qty = 1`
was a defect. ⛔ **It is not.** Two TIME BASES and two GRAINS, each publishing its
own base in the payload:

| value | source | base | means |
|---|---|---|---|
| System Holdings **0** | rows with `origin in (system, both)` | open system positions | **now** |
| table System Qty **1** | `position_reconciliation.system_qty` | the 18-Aug record | **18-Aug** |
| System Only **1** · Orphan **1** | `MISSING_AT_BROKER` | reconciled symbols | 18-Aug |
| Unknown Position **1** | rows with no `strategy` | holdings rows | row-grain |

🔬 **The 0 is MEASURED, ⛔ not assumed: `0` trades sit in `OPEN_STATES`** — all 821
are FAILED/CLOSED/REJECTED/CLOSED_MANUAL/CANCELLED. The `UTTAMSUGAR` row carries
`origin: "recon"` and is correctly excluded. ⭐ `_recon_only_row` had already named
this exact case in its own docstring. ⭐ **Once the date is visible the difference
explains itself** — which is why the fix was the timestamp, ⛔ not the KPI.

#### 4. 🔬 VM EVIDENCE — PRIMARY SOURCES, BECAUSE THE REVIEW LOG HAD FAILED

⛔ The latest `reports/log_review/eod_review_2026-09-01.md` **is itself a failure
notice** (*"REVIEW FAILED — all Gemini cascade models exhausted"*) and carries no
reconciliation content, so it could ⛔ not serve as evidence.
· 🔬 `/var/log/syslog` — **`CRON[967509]` fired `reconcile_positions` TODAY at
  15:45:01** ⇒ ⭐ **the cron is HEALTHY**, ⛔ not dead as the log mtime suggests;
· 🔬 `scripts/reconcile_positions.py` writes **one row per symbol in the
  broker∪system union** ⇒ ⭐ **a flat book writes NOTHING**, which is why
  `cron-reconcile-positions.log` has not grown since 18-Aug;
· ⚠️ ⇒ **"Last Reconciliation" is the last run that PRODUCED A ROW, ⛔ not the
  last run.** ⛔ Left as-is: a run-level record does not exist to correct it with.

#### 5. ⛔ WHAT WAS DELIBERATELY NOT DONE

⛔ **Symbol stays `78px`.** 🔬 At a TRUE 1920 `UTTAMSUGAR` needs **101px** and
truncates — 👤 Rama holds this as a **SEPARATE visual decision**. ⚠️ It reads fine
in the screenshots only because the browser sits at 75% zoom.
⛔ **The deferred global table rule was NOT applied** — 🔬 `style.css` has **ZERO**
modifications and no sticky/frozen `thead` was added.
⛔ S16/S17 untouched — 👤 held to be built LAST. ⛔ No other screen, ⛔ no unrelated
production issue touched.

#### ⚠️ TWO METHOD ERRORS OF MINE, RECORDED BECAUSE THEY GENERALISE

⚠️ **A "1920" MEASUREMENT THAT WAS NOT 1920.** 🔬 The browser reports
`devicePixelRatio 0.75`, so a maximised 1920 window is a **2549px CSS viewport**.
⛔ The first overflow/clipping pass measured the wrong width and did not count;
⭐ redone in **same-origin iframes at exact dimensions**, since a maximised window
ignores resize.
⚠️ **A VACUOUS ZERO.** 🔬 The panel-overlap detector returned `0 overlaps` — but a
900px nudge ALSO returned 0, because the nudged panel simply left the viewport.
⭐ Recalibrated until a **60px sideways nudge produced a detected 48×96 overlap**;
⛔ only then was the zero worth reporting. ⭐ **A green is evidence only if it
could have been red.**

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S22 IS NOT `VERIFIED LIVE`** — approved on a review render over a read-only
extract, ⛔ never on the VM.
⭐ **The 28-row render is DEMO data for density judgement only** — 👤 the artwork's
own population — and is ⛔ **never** presented as trading evidence. ⭐ The REAL
render carries **one** row, and that is the honest production state.

#### Gate

🔬 **Focused S22: 139 passed, 0 failed** (76 → 80 test functions; 4 added, all
proven red-capable by mutation).
🔬 **Full dashboard suite: 2122 passed, 1 failed** = `test_c_venv_has_no_kiteconnect`
— 📄 the environment artifact (⛔ `ops_dashboard/.venv` does not exist on this PC,
so the suite runs under the system python, which carries `kiteconnect` for live
trading). ⭐ **Proven pre-existing by differential: stashed, it fails identically.**
⇒ ⛔ **ZERO new failures.**
⚠️ **One S15 failure appeared in a single ABNORMAL run** (`KeyError: 'timeline'`,
2:14:33 wall time under 3 dashboards + Chrome) and ⛔ **did not reproduce**: runs
1 and 3 are 554.17s / 552.96s with the SAME single failure. ⛔ Its mechanism was
NOT proven — 3 hypotheses were wrong — so ⛔ no cause is asserted.
🔬 Export parseable, 3 sheets, genuinely filtered (28 / 21 / 3 / 3, matching the
UI). 🔬 @1920 **and** @1440: ⛔ no page overflow, ⛔ no clipped headers, 15 columns,
6 KPIs, **0 overlaps across 19 panels**. 🔬 Console clean — the only messages all
session were the canaries that PROVE the capture works.

---

### Entry 32 — S17 CONTROLS APPROVED. ⛔ NOT PUSHED

**Date/time:** 01-Sep-2026, approved ~22:0x IST · **Branch:** `feat/screen10-slippage-analytics`
**Build:** `c8e1238` · **Pushed: NO · Deployed: NO**

🔴🔴 **THE HEADLINE IS ARCHITECTURAL, ⛔ NOT COSMETIC: THE CONTROL PLANE WAS
DESIGNED ON 17-Aug AND NEVER BUILT ON THE TRADING SIDE.**

🔬 Verified exhaustively on the VM: ⛔ nothing listens on `:8600`; ⛔ `CONTROL_SECRET`
is absent from `.env`; ⛔ nothing outside `ops_dashboard` references a control
plane anywhere in the deployed tree; ⛔ **the trading process serves NO HTTP AT
ALL** (⛔ no route, ⛔ no `HTTPServer`). `control_client.py` is a well-built client
for a server that does not exist — ⛔ and is not even deployed, living only on
this unpushed branch.

⇒ ⭐ **S17 IS TRUTHFULLY 100% VIEW, ⛔ NOT THE SPEC'S 95/5.** ⛔ **NO WRITE PATH WAS
CREATED.** Every control renders **disabled WITH its reason**, Readiness reports
**Broker UNKNOWN · Services NOT READY** rather than a comforting green, and
⭐ **UNREACHABLE stays distinct from REFUSED** — *"confirmation required"* and
*"trader unreachable"* are different operational statements.
⏸ Whether to BUILD the trading-side plane is a separate decision, ⛔ out of scope.

#### 1. ⭐ FIVE TRUTH DEFECTS — ⛔ none of them the control semantics

⭐ **THE LABEL WAS A TITLE-CASED KEY** over an authoritative `display_name`.
🔬 3 of 16 differ. Two are casing the artwork itself spells **VWAP**; ⛔ the third
is not cosmetic — `pb01_breakout_retest` title-cases to *"Pb01 Breakout Retest"*
and **SILENTLY DROPS the `(shadow)` marker**.

⭐ **A SHADOW IS NOT A PAUSED STRATEGY.** Disabled BY DESIGN behind a promotion
gate — 📄 *"FAIL-CLOSED: never trades until the spec-13 promotion gate"*, 🔬 **0
trades and 0 signals in its whole life** — so it is badged and its toggle carries
**its own guard**, independent of the plane being down. ⛔ It must never read as
something an operator switched off and could switch back on.

⭐ **THE `#` COLUMN** the contract names three times was missing.

⛔⛔ **THE ARTWORK DRAWS ALL SEVEN LIMIT PARAMETERS TWICE**, under an Intraday
table and a Delivery table. 🔬 **The config splits only THREE.** Printing one
global number under two headings claims the modes are independently configured
when they are not — ⛔ **a duplicated value is a fabricated distinction**, on the
screen whose whole purpose is to say what the system will actually do.
⇒ 👤 Rama chose the **honest mixed layout**: the 3 shown split, the globals saying
they govern both, and ⭐ **Max Qty (Lots) `NOT INSTRUMENTED`, ⛔ never 0** — a 0
would claim a configured limit of **zero lots**.

⭐ **RAW ISO STAMPS**, and this caused the worst visual defect on the page: 🔬 a
32-character stamp took **244px of a 265px** history row, left **14px** for the
action text and wrapped it **ONE CHARACTER PER LINE** ⇒ a **4393px-tall** panel,
now **458px**. Made date-aware (control history spans DAYS): today `10:07:17`,
earlier days `31-Aug 17:35:04`.

#### 2. 👤 THE VISUAL-FIT REJECTION — ⭐ THE MOST IMPORTANT LESSON HERE

👤 **Rama rejected the first S17 render**, and the ruling is worth quoting:
*"The previous review incorrectly treated functional correctness, panel
non-overlap, and regression results as evidence that the screen was visually
matched. That is not sufficient."* · *"A layout can have zero overlap and still be
badly designed."*
⛔⛔ **HE WAS RIGHT.** I had reported *no overflow · no overlap · tests green* as
though that were visual acceptance. ⭐ **IT IS A SEPARATE GATE.**

🔬 **MEASURED against the PNG (edge-detected, 1536×1024) at a TRUE 1920:**

| | artwork | before | after |
|---|---|---|---|
| page height | ~1280 implied | **2366px = 2.19×** | **1540px = 1.43×** |
| main rows | 4 clean bands | **9 scattered tops** | **4 bands** 326/349/225/255 |
| row-2 widths | 11 : 34 : 24 | 360/495/495 | **198/747/405** |
| rail left | 1582 | 1584 | 1584 ⭐ already matched |

⭐ The horizontal composition was ALREADY close; ⛔ **the failure was vertical.**
🔬 Three UNBOUNDED panels dragged their rows down — `12. HISTORY` **814px**, the
rail history **525px**, `RUNTIME LIMITS` **564px**.
⇒ rows stretch to equal heights · row 2 re-proportioned to the artwork's own
**11:34:24** · long lists scroll **INSIDE their footprint** (⭐ the same pattern
the approved Strategy Controls table already uses — ⛔ **nothing deleted, nothing
hidden**) · the two limit groups **side by side** as the artwork draws them.

⚠️ `table-layout: fixed` → **auto**: 🔬 fixed gave four EQUAL ~55px columns, so
every label wrapped onto three lines and the header collided into
**"PARAMETERINTRADAY"**. ⭐ Caught by ZOOMING IN, ⛔ by no metric.

#### 3. 👤 ORDERING IS RAMA'S

⭐ **12 Intraday → 3 Delivery → the shadow LAST**, ⛔ not alphabetical-by-key,
which interleaved the delivery book into the middle of the intraday one on the
operational control list. 🔬 Verified 1–16 on the rendered screen.

#### ⚠️ THREE METHOD ERRORS OF MINE

⚠️ **A VACUOUS TEST THAT PASSED AGAINST A REVERTED IMPLEMENTATION.** It asserted
`"display_name" in inspect.getsource(...)` — and **my own COMMENT contains that
word**. ⭐ Exactly the *"scan what renders, not what is written about it"* trap
📄 this very test file documents at `_markup()`. 🔬 Caught by mutation; rewritten
to assert BEHAVIOUR.
⚠️ **I FIXED TWO OF THREE TIMESTAMP SITES** and missed section 12 — found only by
SCROLLING the rendered page. ⭐ The test now sweeps every site.
⚠️ **MY FIRST CSS BROKE THE PROJECT'S OWN GUARDS** — 11/12px text under the 13px
floor and a raw `#e0a458` — ⭐ and its own tests caught both. ⚠️ It also DUPLICATED
two existing rules; consolidated to the ONE that is genuinely new.

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S17 IS NOT `VERIFIED LIVE`** — approved on a review render over a read-only
extract, ⛔ never on the VM, and ⛔ **no control was ever operated** because none
can be.
⛔ **THE COMPOSITION IS CLOSER, ⛔ NOT MATCHED.** ⚠️ Remaining, and reported BEFORE
approval: **1.43× vs the artwork's implied ~1.19×**; the final row is
`BROKER COSTS · CONFIGURATION COMPARISON · HISTORY` where the artwork draws
`CONFIGURATION SNAPSHOT · INFORMATION` (⛔ panels NOT deleted — a content decision
Rama owns); the rail history is still cramped at 300px; and 1440 runs 2.32× tall
via the **pre-existing** `≤1500` rail reflow.

#### Gate

🔬 **Focused S17: 46 passed, 0 failed** (39 baseline + 7). ⭐ All 6 new tests
proven **red-capable by mutation**, one of them only after being caught vacuous.
🔬 **Full dashboard suite: 2129 passed, 1 failed** = `test_c_venv_has_no_kiteconnect`,
📄 the environment artifact ⇒ ⛔ **ZERO new failures** — ⭐ and the count is
**IDENTICAL to the pre-layout run**, so the composition work broke nothing.
🔬 @1920: ⛔ no page overflow, ⛔ no clipped headers, **0 overlaps** (⭐ detector
calibrated until three separate nudges each fired — a first zero was VACUOUS).
🔬 @1440: 2090px, 6 bands, 0 overlaps, ⛔ no overflow.
⛔ Shared `.tbl-scroll` untouched · ⭐ every new selector `.ctl-page`-scoped ·
⛔ deferred global table rule NOT implemented · ⛔ no other screen touched.
⏸ **S16 Configuration is now the ONLY screen left.**

### Entry 33 — S16 CONFIGURATION APPROVED. ⛔ NOT PUSHED · 🏁 **THE 22nd AND LAST SCREEN**

**Date/time:** 02-Sep-2026, approved ~14:0x IST · **Branch:** `feat/screen10-slippage-analytics`
**Build:** `b776cc8` · **Pushed: NO · Deployed: NO**

🏁 **THE GUI CAMPAIGN'S 22 SCREENS ARE ALL BUILT AND ALL SHOWN.** ⛔ That is ⛔ NOT
"the campaign is finished" — ⏸ S06 still owes a re-render sighting, ⏸ S07 a
re-approval, and ⏸ the global table rule is still DEFERRED. 📄 See the carry list.

#### 1. 🔴 THE HEADLINE — 🔬 THE CONFIG SPLITS **NINE** PARAMETERS, ⛔ NOT THREE

🔬 **MEASURED FROM THE ENFORCERS at the deployed SHA `39292d3`, ⛔ not from key
names** — this is the whole finding, and it corrects a number this project has
been carrying since 01-Sep:

| where | what it resolves per book |
|---|---|
| `capital/position_sizer.py:375-384` | `risk_per_trade_pct` · `max_concentration_pct` · `max_position_value_pct` |
| `capital/risk_engine.py:326-343` | `daily_loss_limit_pct` · `max_sector_exposure_pct` · the two COUNT caps |
| `capital/risk_engine.py:557-560` · `:655-660` | ⭐ OPEN_POSITIONS and DAILY_TRADES **BRANCH on `bucket == "positional"`** |

⇒ ⭐ **A DELIVERY ENTRY NEVER CONSULTS `max_open_positions` OR `max_daily_trades`
AT ALL.** Add the capital bucket split and the leverage map ⇒ **NINE**.

🔬 At the deployed config that is **5 vs 3** open positions and **10 vs 5** daily
trades — ⚠️ **a LIVE difference, ⛔ not a cosmetic one.**
🔬 **COMPLETENESS, from the config rather than from a list I wrote:** the deployed
YAML holds **EXACTLY SEVEN** delivery-scoped keys and the panel names **all seven**.
⭐ Now pinned by a test that SWEEPS the configuration, so a NEW delivery key added
upstream cannot silently go unshown.

⛔ **AN UNSET DELIVERY KEY RAISES AT BOOT — it never inherits.** The screen renders
the unavailable marker on that side and marks the row `partial`; ⛔ it never
borrows the intraday number. ⭐ Proven by dropping each of the eight keys in turn.

#### 2. ⛔ AND THE CONVERSE — FOUR ARE GENUINELY GLOBAL

⭐ **Minimum Eligible Score · Max Qty (Per Order) · Max Consecutive Losses · Price
Drift Threshold** have ⛔ NO delivery twin. Shown **ONCE**, under `GLOBAL LIMITS`,
🔑 **each with the reason it is shared** — 📄 `max_consecutive_losses` is
deliberately shared (*"the streak breaker is a portfolio-wide circuit"*,
`risk_engine.py:642-644`); a signal is scored before its product is chosen.
⛔ **A duplicated value is a FABRICATED DISTINCTION** — the same rule S17 follows.
👤 Rama confirmed this explicitly: *"Do NOT force Intraday/Delivery columns for"*
those four.

⚠️ 🔬 **TWO OF THE FOUR PARAMETERS THE SPEC NAMED AS MODE-SPECIFIC ARE NOT.**
`min_pass_score` (60) is global; `max_single_order_qty` (10,000 **shares**) is
global. ⛔ No delivery column was manufactured for either.

#### 3. 🔴 TWO TRUTH DEFECTS IN **APPROVED S17**, ⏸ REPORTED AND ⛔ NOT TOUCHED

👤 The S16 spec says *"Do not modify S17"*, so ⛔ nothing was changed. ⏸ Both stand:

⛔ S17's **GLOBAL** table states *"one value governs BOTH modes — the config has no
delivery variant"* over **Max Trades 10** and **Max Positions 5**. 🔬 **FALSE at
the deployed config**: `max_open_delivery_positions: 3` and
`max_daily_delivery_trades: 5` exist and gate every delivery entry. ⚠️ S17's
`_limits` looked for a `delivery_*` PREFIX and these two use an **INFIX**.
⛔ S17's *"Minimum Eligible Score"* reads `v3_chain.min_pass_score` — 📄 a value the
config file itself labels a **non-gating SEED** (*"10a records the score, does NOT
gate on it"*). ⭐ Same number (60), ⛔ wrong source. The live gate is
`scoring_weights.yaml min_pass_score`, which is what S16 now reads.

#### 4. ⭐ THE PANELS THAT REPLACED THE ARTWORK'S BOTTOM ROW

⛔ **STRATEGY CONFIGURATION IS GONE** — the panel, the `strategies` payload key, the
category tab AND the export sheet. ⭐ S17 owns strategy enable/disable; a read-only
copy here is still a second strategy-control surface, and ⚠️ two screens showing
the same switch is how they drift apart. ⛔ The **Scanners** tab went with it: it
existed only to state a 1:1 relationship between two things this screen no longer
shows. ⇒ 🔬 the category list is now **exactly the ten** the revised design names.

⭐ **THE ROW IS REPLACED, ⛔ NOT EMPTIED:** `MODE-SPECIFIC CONFIGURATION` |
`GLOBAL LIMITS`, at the artwork's own two-panel proportion. ⭐ Nine parameters left
the SYSTEM CONFIGURATION quad and **each card states how many of its own moved**,
so a shorter card explains itself rather than reading as one that quietly lost
rows. 🔬 **Nothing is shown twice** — a test intersects the quad's labels with the
mode panel's and requires the intersection EMPTY.

#### 5. 🔬 MEASURED FIXES THE ARTWORK ALONE WOULD NOT HAVE FOUND

⚠️ **THE RAIL WAS 96px TOO NARROW, AND THE COST WAS NOT COSMETIC.** 🔬 Edge-detected
off `16. Configuration.png`: the content column spans x 192→1522 and the rail
starts at 1146 ⇒ **28.3%**, i.e. **476px** at 1920. The build carried **380px** ⇒
`CONFIGURATION COMPARISON` rendered a **horizontal scrollbar that showed the
Parameter column and hid BOTH value columns** — a comparison panel with no values.

⚠️ **`CONFIGURATION HISTORY` GAVE THE WIDTH TO THE WRONG COLUMNS.** 🔬 Auto layout
returned **81px** to `Changed By` — a column that is ALWAYS the unavailable marker
— and **55/61px** to `Old`/`New`, so an email broke into **five four-character
fragments**. ⭐ The two value columns now carry a `min-width` floor.
⚠️ ⛔ **`table-layout: fixed` was NOT the answer** — 📄 it is what collapsed S17's
header into *"PARAMETERINTRADAY"*. ⭐ `min-width` on the CELL raises the column
minimum without it.
⚠️ 🔬 **A `width` ON AN `auto` TABLE IS ONLY A SUGGESTION** — the percentages alone
came back **47/70/98/66/68/79**, overridden by the header words' own min-content.

⭐ **PAGE HEIGHT 1760 → 1558px** at a true 1920 (**1.44×**), 4 clean main bands,
main ends **1499** and the rail **1529** — ⭐ so the SECONDARY column is ⛔ not the
taller one. ⚠️ 🔬 At the first attempt it WAS: 1582 vs 1499, fixed by defaulting
the history to 6 rows (*"6 of 14"* + *View all*), ⛔ not by deleting anything.

#### 6. 👤 THE RAIL POLISH — RAMA'S OWN INSTRUCTION, AND ITS ONE TRAP

📜 *"Prefer sensible truncation/ellipsis with a clear tooltip/detail affordance."*
⭐ Every rail cell is **ONE LINE** (uniform 26px rows, columns aligned down the
panel). ⛔ **NOTHING IS HIDDEN, and there are THREE routes to every value, ⛔ not
one:** the `title` (🔬 28 of 57 cells truncate; **all 28** carry the exact full
value), a **FULL VALUES toggle** that unwraps every row in place (🔬 28 → 0
truncated, page 1558 → 1673, no overflow) — ⭐ so a value is reachable **without a
mouse** — and the XLSX export, which truncates nothing.
⚠️ **A TOOLTIP ALONE WOULD HAVE BEEN THE WEAKER GUARANTEE this project has been
caught by before.** ⭐ The toggle is what makes truncation a PRESENTATION choice.

⚠️ **END-ELLIPSIS CUT OFF THE PART THAT IDENTIFIES A PATH.** 🔬 Found by LOOKING at
the render: **four consecutive comparison rows became the identical string**
*"trading_hours.mis_squar…"* — the panel showed four changes and **named none of
them**. ⇒ ⭐ configuration paths **MIDDLE**-truncate. 🔬 7 of 7 unique again.
⭐ The 24/11-character budgets are **MEASURED** (7.62px per mono glyph at 197px and
97px), ⛔ not picked — a first draft at 26/14 put **TWO ellipses** on every path.

#### 7. 👤 THE SEVEN CENTRED DATA-COLUMN HEADINGS (02-Sep)

🔬 **WHAT WAS ACTUALLY WRONG, measured before it was described:** the **DATA** was
ALREADY centred by 👤 Rama's own 14-Aug global rule
`table td.dt-num { text-align: center !important }` (`style.css:1974`); only the
**HEADERS** were still right-aligned from `.dt-num { text-align: right }` (`:437`),
so each heading sat **off the edge of its own centred column**.
⇒ ⭐ **This is S16 catching up with the SAME 14-Aug column-role spec S08 already
follows** — 📄 *"data column HEADINGS …. CENTER (over their own data)"* (`:1978`).

⭐ An opt-in `cfg16-hc` class names **each of the seven cells**, ⛔ rather than
centring every `.dt-num` header on the page — ⭐ that is the difference between
APPLYING a correction and GENERALISING one. 🔬 Computed `text-align` over EVERY
`thead th`: **exactly 7 centred, 0 others**, 0 headers wrapped.
🔬 **0 body cells on this page are right-aligned, before or after**, and the shared
14-Aug rule is untouched (⭐ a test pins it).
⚠️ 👤 The card said *"the eight highlighted header cells"* but enumerated **SEVEN**.
⭐ Exactly the seven named were centred; ⛔ an eighth was NOT invented. ⏸ Reported
before approval.

#### ⚠️ THREE OF MY OWN GUARDS FIRED, AND ONE WAS VACUOUS

⚠️ **THE VIEW-STATE BUTTON ALLOW-LIST WENT RED** on the new toggle — ⭐ working
exactly as intended; the class was added to it deliberately.
⛔ **A COMPLETENESS TEST PASSED A MUTATION IT SHOULD HAVE KILLED.** 🔬 The shared
`conftest` fixture carries only **2 of the 7** delivery keys, so "no delivery key
is left off the panel" was checking two of them. ⭐ It now sweeps
`_DEPLOYED_SYSTEM` as well, and the mutation goes red.
⛔⛔ **MY CSS RULE EXTRACTOR WAS SILENTLY CHECKING HALF THE STYLESHEET.**
🔬 `(?:^|[{}])` **CONSUMES** the brace and `re.findall` does ⛔ not overlap — so each
rule's closing brace was eaten by its own match and could not anchor the next.
It returned **54 of 108** rules. ⚠️ **My non-vacuity FLOOR was too weak to notice**
(the halved count still cleared it). ⭐ Fixed with a **lookbehind**, and the floor
replaced by an **EXACT identity** (`rules == count("{") − count("@media")`), which
⭐ DOES go red when the regex is reverted. 🔬 Re-swept: **0 unscoped rules**.
⭐ **GENERALISE: ⛔ a FLOOR is not a non-vacuity check — an IDENTITY is.**

#### Gate

🔬 **Focused S16: 103 passed, 0 failed** (73 baseline + 30). ⭐ **35 of 35 mutations
behaved as designed** — 34 RED, 1 deliberately GREEN (⭐ a CSS *comment* that merely
mentions a banned declaration must **not** fail the guard that reads the
stylesheet).
🔬 **Full dashboard suite: 2159 passed, 1 failed** = `test_c_venv_has_no_kiteconnect`,
📄 the environment artifact ⇒ ⛔ **ZERO new failures**. ⭐ The baseline **2129/1** was
MEASURED, ⛔ not recalled — by stashing this work and re-running.
⚠️ 🔬 **Three S17 failures in the first full run were 100% ENVIRONMENTAL** — a stale
`PYTHONPATH` pointing at `D:\Projects\trading-system`, which has no `controls.py`.
⛔ Not a regression; all 36 pass with the correct path.
🔬 **@1920:** 1558px · 4 bands · ⛔ 0 page overflow · ⛔ 0 inner h-scroll · **0
overlaps** (⭐ detector calibrated until four nudges each fired — ⚠️ a first zero
counted parent-child containment and was thrown out) · ⛔ 0 clipped headings · ⛔ 0
vertical clips · ⛔ 0 stray ellipsis outside the opt-in class · ⛔ 0 console messages
(⭐ reader proven live with a probe).
🔬 **@1440:** 2666px · 0 overlaps · 0 clips · ⛔ no overflow. ⭐ The rail drops under
the main column below 1500 so ⛔ nothing is cramped; ⚠️ it is TALLER than a
two-column 1440 would be — ⏸ reported BEFORE approval.
⛔ Every CSS change falls inside the **SCREEN 16 block** (`style.css:6066-6433`) —
🔬 verified by diff-hunk line ranges · ⛔ no shared rule edited · ⛔ no other screen
touched · ⛔ `config_view.build_config_view` (`/api/config`) byte-unchanged.

#### 🔬 THE RENDER THE APPROVAL WAS GIVEN ON

⭐ **The DEPLOYED configuration**, extracted **read-only** with
`git show 39292d3:config/system_config.yaml` — ⚠️ `gui09` forked 14-Aug, **BEFORE**
the delivery keys landed on 22-Aug, so its own config would have rendered five of
the nine mode rows unavailable and the review would have been of a config **nobody
runs**. 🔬 All nine rows diffed against that YAML: **9 of 9 match**.
⭐ Comparison and History were built from **THREE REAL historical versions** of the
same file (`39292d3` · `52ccb4f` · `0197923`) ⇒ ⛔ the diffs are the diffs that
genuinely happened. ⛔ **Nothing fabricated.**
⭐ Harness lived **OUTSIDE the repo** (session scratchpad); ⛔ the production DB was
never opened and ⛔ the VM was never touched.

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S16 IS NOT `VERIFIED LIVE`** — 🟢 VISUALLY APPROVED on a local review render
over a read-only config extract, ⛔ never on the VM.
⛔ **THE COMPOSITION IS CLOSER, ⛔ NOT MATCHED.** ⏸ Reported BEFORE approval:
**1.44× vs the artwork's implied ~1.19×**; 1440 runs **2666px**; and the rail's
6-column History is the tightest panel on the page — ⏸ moving it to the main
column would fix it and cost ~180px of height, 👤 Rama's call.

### Entry 34 — S06 POSITIONS: THE CORRECTED RENDER WAS SIGHTED AND APPROVED. ⛔ NOT PUSHED

**Date/time:** 02-Sep-2026, approved ~14:4x IST · **Branch:** `feat/screen10-slippage-analytics`
**Build:** `92b927c` (the render) · the correction is `b47e148` (20-Aug) · **Code changed THIS TASK: NO** · **Pushed: NO · Deployed: NO**

⭐ **THIS ENTRY CLOSES A DEBT, ⛔ IT DOES NOT RECORD A CHANGE.** 👤 Rama's card:
*"This is a VISUAL RE-APPROVAL task only … If the existing corrected screenshot is
still available, show it rather than changing code."*
🔬 `git status --porcelain` = **0** before and after. ⛔ No commit of code, ⛔ no new
test, ⛔ no CSS. ⭐ The only artefact is this record.

#### ⚠️ CORRECTION TO THIS ENTRY'S OWN FIRST DRAFT — ⛔ "UNCHANGED" WAS FALSE

⛔ I first wrote **"Build: `b47e148` — UNCHANGED"**. 🔬 **THAT IS WRONG, and the
diff says so:** `positions.html` is **22 lines different** between `b47e148` and
HEAD. ⭐ `efeb0b7` — *"the column reorder becomes ONE implementation, not
fifteen"* — landed AFTER the correction and **replaced S06's own inline
`onDragStart`/`onDrop`/`onDragEnd` with the shared `colDragMixin()`**.

⚠️ ⭐ **THAT IS NOT AN IDLE DETAIL: the drag is exactly what the correction depends
on.** 📄 `b47e148` states the separator is computed from the **CURRENT column
order** (`groups()` / `isSep()` keyed by `c.key`), ⛔ **never `nth-child`**, so a
drag carries the separator with its band — and `efeb0b7` re-implemented that drag.

🔬 **SO IT WAS MEASURED ON THE LIVE RENDER, ⛔ not assumed:**

| column order | separators per row |
|---|---|
| default | **5** |
| `symbol` moved INTO the `ENTRY ₹` band | **7** — ⭐ the separators FOLLOWED the columns |
| after the screen's own `resetCols()` | **5** |

⇒ ⭐ **THE CORRECTION SURVIVES THE REFACTOR.** A foreign column dropped inside a
band creates new boundaries and the rules move to them — ⛔ they are not
positional. 🔬 `onDragStart` / `onDrop` / `onDragEnd` are present, now from the
shared mixin.

⛔ **WHAT RAMA APPROVED THEREFORE INCLUDES `efeb0b7`**, ⛔ not `b47e148` alone.
⭐ Both were already committed and in the tree that was rendered; ⛔ nothing was
changed for this task. ⭐ **GENERALISE: "the correction is unchanged" and "the
FILE is unchanged" are different claims — 🔬 check the file, ⛔ never infer it
from the commit that made the correction.**

#### ⭐ WHAT WAS OWED, AND WHY

📄 Entry 21 (19-Aug) recorded S06 as ⏳ **QUALIFIED**: *"approved on the
PRE-correction render; the correction (`b47e148`) is verified and re-rendered but
⛔ never seen."* ⇒ ⭐ the debt was a **SIGHTING**, ⛔ never a rebuild.
⭐ **Entry 21's STATE table is left EXACTLY as written** — 📄 it was true on 19-Aug.
⛔ **This entry SUPERSEDES it; it does not rewrite it.**

#### 🔬 THE CORRECTION, RE-MEASURED ON THE LIVE RENDER

📄 `b47e148`, quoting 👤 Rama in its own message: *"one minor change … some very
minor correction to improve viewability of Grouped colums"*.

🔬 **THE DEFECT (measured 20-Aug):** the four grouped bands were **exactly
adjacent, zero gap** — `QTY` ended at **x=1159** where `ENTRY` began at **1159**;
`ENTRY` **1272** = `SL` **1272**; `SL` **1392** = `TGT` **1392**. ⇒ ⚠️ eight
`SYSTEM|BROKER` sub-headings read as **one undifferentiated run** and an operator
could not see where a band ended.

⭐ **THE FIX:** a **1px rule at every band BOUNDARY**, on **all three levels** — the
group row, the sub-heading row AND the body — so a band stays traceable down the
rows. ⭐ Reuses the group underline's own `--card-bd` token ⇒ ⛔ no new colour, ⛔ no
new visual concept, ⛔ no extra width.
⭐ `sep` is computed from the **CURRENT column order** (`groups()` / `isSep()` keyed
by `c.key`), ⛔ **never `nth-child`** — 🔴 these columns are REORDERABLE, so a drag
carries the separator with its band.

🔬 **VERIFIED LIVE 02-Sep, at both viewports: 5 separators on EACH of 10 rows**
(group + sub-heading + 8 body) — ⭐ uniform, ⛔ no row missing one.

#### Gate

🔬 **@1920×1080 (TRUE, via an iframe — ⚠️ the host window is dpr 0.75 ⇒ 2549 CSS px):**
page **1377px (1.275×)** · ⛔ **0 page horizontal overflow** · ⛔ **0 clipped header
or data cells** · ⭐ 5×10 separators.
🔬 **@1440×900:** **1624px (1.804×)** · ⛔ 0 page overflow · ⛔ 0 clipped cells ·
⭐ 5×10 separators. ⭐ KPI strip reflows 6-across → **3+3**, filters to two rows, and
the wide table scrolls **INSIDE its own panel** — ⭐ **PRE-EXISTING** S06 behaviour,
⛔ not introduced here.
🔬 **Focused S06: 81 passed, 0 failed** — ⭐ including
`test_grouped_bands_are_separated_by_a_rule_at_every_boundary`, 📄 the guard
`b47e148` added for exactly this correction (⭐ proven non-vacuous then by **5**
plants, all red).
⛔ **NO environment-only failure in this run** — 📄 the `kiteconnect` isolation
artifact lives in `test_isolation.py`, ⛔ which is not in the S06 set.

⭐ **FILLED FOR REVIEW, ⛔ not a screen of dashes:** **8** position rows across
**all seven** statuses (Open 4 · SL Hit 1 · TGT Hit 1 · Manual Exit 1 · Expired 1),
so density, alignment and badge contrast were all judgeable.

#### ⚠️ REPORTED BEFORE APPROVAL, ⛔ NOT A DEFECT OF THE CORRECTION

⭐ The **MTM PERFORMANCE** panel states *"MTM is not available to this screen"* and
explains that an intraday MTM curve needs a **live price** while the dashboard
reads only the local DB. ⇒ ⭐ that is the screen being **HONEST about an
instrumentation gap**, ⛔ not a rendering fault, and ⛔ it is untouched by `b47e148`.

#### ⛔ WHAT THE APPROVAL DOES NOT MEAN

🔴 **S06 IS NOT `VERIFIED LIVE`** — 🟢 VISUALLY APPROVED on a local review render
over the fixture DB, ⛔ never on the VM.

#### ⏸ THE STANDING NOW

🟢 **21 of 22 fully approved.** ⏸ **S07 Trade Explorer is the LAST qualified screen**
— 📄 approved 14-Aug under the PREVIOUS ledger; `66fc82e` landed after it, unseen;
⭐ the 13px-floor decision makes it a **RE-approval**. ⇒ ⭐ same render-only path.
⏸ **The global table rule is DUE** — it was deferred *until all 22 are done*.
