# GUI REMAINING-CLOSURE DECISION MATRIX
**19-Aug-2026 ~13:20 IST · worktree `gui09` · HEAD `d81896c` · tree CLEAN · PUSHED=NO · DEPLOYED=NO**
⛔ **No implementation started. ⛔ Nothing deployed. ⭐ Prepared to make the decisions cheap, ⛔ not to pre-empt them.**

## A · WHAT DOES **NOT** NEED RAMA — RESOLVED HERE, RECORDED SO IT IS NOT RE-ASKED

| # | item | resolution | evidence |
|---|---|---|---|
| A1 | **Untracked layout-assurance doc** | ✅ **CLOSED** — committed on Rama's instruction as its **own docs-only commit** `d81896c`, ⛔ not folded into the S09/S12 fix. Tree now clean. | `git status --porcelain` = 0 entries |
| A2 | **S12 PNG/TXT throughput disagreement** | ✅ **CLOSED — not a miss, ⛔ no duplicate to add.** `12. System_Health.txt` lists only HEALTH TRENDS; **`11. Execution_Analytics.txt:173` owns "THROUGHPUT ANALYSIS / Per Minute"** and S11 implements it. The build followed the TXT. | both spec files read directly |
| A3 | **S12 13px floor** | ✅ **PASSES, ⛔ nothing owed.** `.sysh-axis` renders **13.00px at 1920 AND 1440** — `viewBox 0 0 560 180` in a 180px-high box pins the `meet` scale at 1.0 by HEIGHT. | `getScreenCTM()`, both viewports |
| A4 | **S12 x-axis collision** | ✅ **FIXED** `e2022d5` (5.4px overlap → 37.1px min gap). Gate 1 failed / 2,030 passed, zero new failures. | browser-measured, planted regression RED |

## B · DECISIONS THAT GENUINELY REQUIRE RAMA
⭐ Each row states **what I already determined** so only the judgement is left.

### B1 · 🔴 S09 EQUITY-CURVE AXIS LABELS — *the only one gating code today*
- **Determined:** the build has **0 SVG text**; `09. PnL_Analytics.png` draws **₹40,000…−₹10,000** (Y) and **09:15…15:30** (X). Data is sufficient — `equity_curve.{gross,net}` points carry **`{cum, ts}`** ⇒ ⛔ **no fabrication needed.**
- **Not a 13px violation** — it is a **missing visual feature**, which is why it was not built unilaterally.
- **Cost if authorised:** HTML-overlay labels (the proven S10/S11 pattern), focused tests, browser verify at both viewports, local commit. ⚠️ **Triggers another S09 re-approval.**
- **DECISION:** authorise · defer · or **record permanently as an accepted deviation**.

### B2 · 🔴 SHARED CHROME TYPOGRAPHY — *largest blast radius on the board*
- **Measured on both screens today:** `nv-group` **9.92px** ("Trading") · `sb-label` **10.24px** ("Trader") · `sb-poll` **9.92px** ("poll 5s") · `sb-pill` **12.48px** (DOWN/PAPER/INACTIVE/ENTRY_WINDOW) · `nv-label` **11px**. All **REM-declared**, all in **base/sidebar**.
- ⭐ **They are readable TEXT, ⛔ not the decorative glyphs the closure pass exempted** (drag handles 8.80px, sort arrows 9.28px).
- 🔑 **THE COST IS THE POINT:** these classes are on **all 22 screens**, so raising them **changes every already-approved screen** and re-opens those approvals. That is why it is a decision, ⛔ not a patch — and why the closure pass deliberately edited **no global class**.
- **DECISION:** raise to 13px and accept re-approval of all 22 · leave and record as an accepted deviation · or rule the sidebar chrome **out of scope** of the 13px spec.

### B3 · 🔴 S12 VISUAL RE-APPROVAL — *no work owed, approval only*
S12 has changed **twice** since its last approval: the typography pass `4ee1443`, then today's collision fix `e2022d5`. **DECISION: look and approve, or list changes wanted.**

### B4 · 🔴 S07 RR DAMAGE %
- **Determined:** `rr_damage_pct` lives in **`trade_slippage_log`**, a table **Trade Explorer does not read**; and S07 carries an explicit prior ruling that **R-multiple is NEVER printed in an R:R column**.
- **DECISION:** add the table read (new backend read — ⛔ breaks the "no backend change" rule the GUI campaign has held) · or keep **ABSENT** permanently.

### B5 · 🔴 Q2 — SCREEN 02 REJECTION TAXONOMY / FUNNEL
- **Determined:** `_RISK_REJECT_STATUSES` (**10**) and `_CAPITAL_REJECT_STATUSES` (**3**) are frozen tuples; S03 renders **no** reject split; a test pins the PENDING state so no pass implements it silently.
- **DECISION:** rule the taxonomy (A–E) · or confirm it stays pinned.

### B6 · 🔴 S03 D1/D2 — CAPITAL SEMANTICS
- **Determined:** **no per-strategy allocation cap exists.** The backend declares it honestly — `"allocation_configured": None`, `"allocation_basis": "global bucket"` — and the UI derives `alloc = used + remaining`.
- **DECISION:** introduce a per-strategy cap concept · or confirm the honest global-bucket derivation is final.

### B7 · 🔴 S03 F1/Q3 — EXPORT
- **Determined:** Screen 03 renders the shared `export_button` macro, **disabled** until `table_export_enabled`; **no `/api/export/strategies` route exists**. Blocked on the **Q3 copy-protection acceptance** ruling — ⛔ a data-egress question, not a UI one.
- **DECISION:** rule Q3, then enable · or keep the stub.

### B8 · 🔴 S03 F3 — TRADING TYPE COLUMN
- 🔑 **A spec-vs-artwork conflict inside one screen:** the **TXT lists it**, the **artwork's table does not draw it**.
- ⭐ **RELEVANT PRECEDENT SET TODAY, offered as input ⛔ not as a decision:** on S12 the same conflict was resolved **in the TXT's favour**. Applying that consistently here would mean **ADDING** the column — and re-opening S03's approval.
- **DECISION:** add (TXT wins) · or keep absent (artwork wins) — ⭐ and ideally state the **general rule** so the next conflict is not re-litigated.

### B9 · 🔴 S03 F4 — SL / TGT / ROI FIELDS
Same class as B8; `sl_hit_count`, `tgt_hit_count`, `roi_pct` are all absent and pinned. **DECISION: add or keep absent.**

### B10 · 🔴 OUTSTANDING VISUAL APPROVALS — *approval only, no work*
**First approval:** 01 · 04 · 05 · 06 · 07 · 08 · **Approval:** 02 · 03 · 14 · 16 · **Re-approval:** 09 · 10 · 11 · **12** (see B3).

### B11 · 🔴 LAYOUT-ASSURANCE FOLLOW-UP (from `d81896c`)
The *"0 overflow on 21 screens"* claim rests on a harness with **0 references in the repo** and **unrecorded seeding**. Options tabled: **A** promote harness + seeded fixtures · **B** A + `scrollWidth <= clientWidth` in CI · **C** record-only `SEEDING.md` · **D** nothing.
⭐ **My recommendation stands: C now, A when there is a window.** B is the only one that makes overflow regress-testable but puts a browser in a 542 s gate.

### B12 · ⛔ GATE E — DEPLOYMENT
`gui-dashboard.service` restart + verification. **⛔ Not now. Requires explicit instruction; nothing will be pushed or deployed without it.**

## C · WHAT IS BLOCKED ON WHAT
- ⛔ **Nothing in B1–B11 is blocked on code.** Every one is a judgement; **B3/B10 need only Rama's eyes.**
- ⛔ **B12 is blocked on all of B1–B11**, because full closure requires screens changed after approval to be re-approved.
- ⚠️ **B2 is the schedule risk:** if the sidebar is raised, **every screen's approval re-opens**, so it should be ruled **before** the remaining first-approvals in B10 — ⛔ otherwise those approvals are taken twice.

## D · STATE, FOR THE RECORD
`HEAD d81896ce2382f681293b177563d115be3ebe09c0` · parent `e2022d5` · `160ebbc` intact ·
branch `feat/screen10-slippage-analytics` · tree **CLEAN** · **39 ahead / 5 behind** ·
`origin/main` `08b462b` · **PUSHED = NO · DEPLOYED = NO** · full suite **1 failed / 2,030 passed**
(known environmental `test_isolation::test_c_venv_has_no_kiteconnect`) · QA server stopped, 8599 free.
