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
