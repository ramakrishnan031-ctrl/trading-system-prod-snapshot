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

## ⚠️ Carried forward for tomorrow's deployment review

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
