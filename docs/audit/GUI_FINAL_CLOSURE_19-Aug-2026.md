# GUI FINAL CLOSURE — STATUS MODEL, FOUR QUEUES, D3 TIERS, APPROVAL CHECKLIST
**19-Aug-2026 · UPDATED ~21:0x after the 20:02 finalization authorization · tree CLEAN**
**PUSHED = NO · DEPLOYED = NO**
Suite **1 failed / 2,067 passed** (§10) — sole failure the known environmental
`test_isolation::test_c_venv_has_no_kiteconnect`, ⛔ **untouched** (⛔ the environment was ⛔ not
modified to make it pass).
⭐ **B1–B6 are now RULED.** Five resolved, one blocked — §3. Source ⭐ **was** changed under
explicit authorisation (B1 typography, B6 export); every other item resolved with ⛔ **no code**.
📸 **The 22-screen visual-review package is `docs/audit/approval_final_19aug/`.**

## 🔑 STATEMENTS REQUIRED
> ### **Authorized technical work remaining = 0.**
> ### **Deferred monitored risk: S08 KPI internal overflow.**

---

## 0 · THE STATUS MODEL — ⛔ "TECHNICALLY COMPLETE" IS ⛔ NOT "PROJECT COMPLETE"
⭐ **The word CLOSED was carrying two meanings. It now carries one, and the other has its own
word.** ⛔ **"22/22 closed" must NEVER be written without "technically".**

| dimension | figure | meaning |
|---|---|---|
| **IMPLEMENTATION** | **22 / 22 technically complete** | code · tests · browser QA all done — ⚠️ **S01 excepted: pre-auth, ⛔ never browser-tested** |
| **FINAL CLOSURE** | **0 / 22 finally closed** | ⛔ visual approval has ⛔ **not** been granted |
| **AUTHORIZED TECHNICAL WORK** | **0 remaining** | Queue C is empty |
| **VISUAL APPROVAL** | **22 awaiting Rama** | see the correction in §2 |
| **BUSINESS / SPEC DECISIONS** | ✅ **5 resolved · 🔴 1 blocked (B3)** | Queue B — ruled 19-Aug |
| **MONITORED RISKS** | **3** | Queue D — ⛔ **NOT "remaining technical work"** |

---

## 1 · IMPLEMENTATION — 22 / 22 TECHNICALLY COMPLETE
⭐ Includes the only two screens ever genuinely BLOCKED:
- ✅ **S14 Trade Logs — TECHNICALLY CLOSED** (`8f78c67`): `.tlg-row4` bare-`fr` → `minmax(0,…)`,
  **8 widths verified clean**, the former 1440 page overflow **eliminated**, internal table
  scrolling **preserved**, rail/main relationship **preserved**. ⛔ No breakpoint, ⛔ no
  `main.content` rule, ⛔ no global overflow workaround. Both disproved hypotheses are **pinned
  by tests** so they cannot return.
- ✅ **S17 Controls — TECHNICALLY FIXED** (`b14dea3`): D2-B + D2-C completed, **10 viewport
  checks clean**, seven-screen KPI regression clean.

---

## 2 · QUEUE A — VISUAL APPROVAL
⛔ **No screen may be marked CLOSED without Rama's visual confirmation.**

### 🔴 A COUNT CORRECTION, MEASURED — ⛔ THE FIGURE IS **22**, ⛔ NOT 20
📌 **Stated plainly, because a wrong denominator under-reports what is owed.** The 19:45
directive gives *"20 screens total"* and then enumerates **21** (5 + 5 + 11), with **S08 absent
from every group**. Neither figure survives measurement:

| check | result |
|---|---|
| rows in the authoritative approval matrix marked **⏳ awaiting** | **22 of 22** |
| rows marked **visually approved** | **0** |
| ⇒ screens that could account for a *"remaining 2"* | ⛔ **none exist** |
| screens inside Q2's radius (`extends "base.html"`) | **21** — measured; **includes S08** `capital_risk.html` and **S22** `holdings.html` |
| screens outside it | **1** — **S01** `login.html`, ⛔ never extends `base.html`, ⛔ never loads `style.css` |

⇒ 🔑 **The 20 / 21 gap is an enumeration slip — S08 dropped, and the stated count one lower than
its own list — ⛔ NOT two screens that were quietly approved.** ⛔ **No "2 closed" figure is
manufactured here:** the authoritative register identifies **zero** screens as visually approved.
**6 + 6 + 10 = 22**, every screen appearing exactly once, S01–S22 fully covered.
⚠️ **S02 moved from group 3 to group 2 after B1** — its alert banner and severity chips changed,
so it is ⛔ no longer a chrome-strip-only re-approval.

| group | n | screens |
|---|---|---|
| **1 · FIRST APPROVAL** | **6** | **S01** ⭐ *(pre-auth · standalone `login.html` · ⛔ **independent of Q2** · ⚠️ never browser-tested)* · S04 · S05 · S06 · S07 · **S08** |
| **2 · RE-APPROVAL — CHANGED TODAY** | **6** | 🆕 **S02** *(B1 banner + chips)* · **S03** *(Q3 columns · 🆕 B6 export · 🆕 B1 labels)* · **S09** *(axis labels)* · **S12** *(collision fix)* · **S14** *(overflow)* · **S17** *(4px + KPI ramp)* |
| **3 · RE-APPROVAL — SHARED CHROME ONLY** | **10** | S10 · S11 · S13 · S15 · S16 · S18 · S19 · S20 · S21 · S22 |

⭐ **Q2 RE-QA STAYS TARGETED — measured, ⛔ not assumed:** all five raised classes occur in
**`base.html` and nowhere else** (18 occurrences there; **0** across the other 29 templates) ⇒
the changed surface is the **sidebar + top status strip**, identical on all 21. ⇒ check **that
strip plus a page-overflow glance at the required viewport**, ⛔ **not** a full re-audit of
unchanged page bodies.

---

## 3 · QUEUE B — BUSINESS / SPECIFICATION DECISIONS · ✅ **5 RESOLVED · 🔴 1 BLOCKED**
🔑 **RULED AND EXECUTED 19-Aug-2026 under Rama's 20:02 authorisation.** ⛔ Nothing was invented:
each outcome below names the written source it rests on.

### 🔑 THE S03 NAMING SEPARATION — ⛔ TWO UNRELATED THINGS ARE BOTH CALLED "D1"
| item | status |
|---|---|
| **S03 PLACEMENT DECISION (placement D1)** — SL / TGT / ROI main-table placement, options A/B/C | ✅ **CLOSED — ACCEPTED = A.** ⛔ It was **never** an unresolved item |
| **S03 CAPITAL SEMANTICS** — *historically shorthanded "S03 D1/D2"* | ✅ **B5 — VALIDATED, ⛔ no code change** (below) |

| | group | evidence | decision | code | test | status |
|---|---|---|---|---|---|---|
| **B1** | **D3 typography** | **"Minimum: 13px"** appears in **20 of the 22** TXT specs. ⭐ **The 63 was a STYLESHEET count, ⛔ not an exposure count** — a browser sweep of all 22 screens at **1440 AND 1920** found the real exposure to be **8 findings in 5 declarations** | **RAISE the five** | ✅ 5 rules in `style.css` | 6 new + 1 flipped | ✅ **DONE** `aaf083d` |
| **B2** | **Decorative-glyph exemption** | what remains below the floor is **grips 8.8px** and **sort arrows 9.0–9.28px** — single symbols carrying ⛔ **no readable text** | **FORMALISED and BOUNDED** | ⛔ none | 3, incl. one that fails if readable text hides in the family | ✅ **DONE** `aaf083d` |
| **B3** | **S07 RR Damage %** | the spec's **LABEL** matches `rr_damage_pct`; the spec's own **ARITHMETIC** (1:2→1:1.6 = 20%) matches `rr_degradation_pct`. **Both exist, are different numbers**, and the project forbids presenting either as the other | 🔴 **BLOCKED — ⛔ NOT fabricated** | ⛔ none | 3 assert the absence **deliberately** | 🔴 **RAMA** |
| **B4** | **Rejection taxonomy** | `02. Dashboard.txt` LIVE PIPELINE names all **12** stages; `STAGE_DEFS` already carries them **in that order**, each rendered with **Count + Last Event Time** | ✅ **VALIDATED** | ⛔ none | 5, incl. one failing on any **unannounced** stage | ✅ **DONE** `d42e535` |
| **B5** | **S03 capital semantics** | `used + remaining` resolves to `intraday_bucket_pct × opening` — the **global bucket**, exactly as `allocation_basis` declares | ✅ **VALIDATED** | ⛔ none | the guard **stays**, +2 assertions | ✅ **DONE** `34b4d24` |
| **B6** | **S03 export** | `03. Strategies.txt` — **EXPORT: "Download XLSX"**. The `_xlsx` writer and the `/api/export/*` pattern already ship on **17 screens** | **IMPLEMENT, smallest compliant** | ✅ route + builder + button | 6, incl. header-order and None-not-zero | ✅ **DONE** `80f8d5b` |

### 🔴 B3 — THE EXACT MISSING DEFINITION
> **Which quantity must S07's *"RR Damage %"* row show, given the label is already bound on S10
> to a different number?**

| quantity | base | shown today as |
|---|---|---|
| `rr_damage_pct` = (entry_adverse + sl_adverse − tgt_favourable) ÷ `planned_sl_distance` × 100 | the planned **risk budget** | **S10 — "RR Damage %"** |
| `rr_degradation_pct` = 100 × (`planned_rr` − `actual_rr`) ÷ `planned_rr` | the planned **R:R** | **S10 — "RR Degradation % (avg)"** |
⭐ The project's own source calls the second *"the reference design's 1:2 → 1:1.6 = 20% arithmetic"*
⇒ **S07's EXAMPLE is `rr_degradation_pct` while S07's LABEL is `rr_damage_pct`'s name.**
⛔ **It cannot be settled by taking the smaller change** — every option trades one project rule
against another: ① spec label + spec arithmetic ⇒ one label, two meanings across S07/S10 ·
② spec arithmetic + honest label ⇒ keeps one-label-one-meaning, deviates from the spec's wording ·
③ existing `rr_damage_pct` under the spec's label ⇒ agrees with S10, but the three rows ⛔ **do not
reconcile** on screen.
⚠️ **A SEPARATE DATA GAP:** S07 reads `trade_explorer_rows`, which does ⛔ **not** join
`trade_slippage_log` ⇒ even once the label is ruled, S07 needs that join — **data plumbing on a
trading screen**, ⛔ not a UI tweak.

### ⭐ TWO TIER-TABLE CLAIMS DID NOT SURVIVE MEASUREMENT — corrected here
The §6 table below is kept **as the record of what was believed**, ⛔ not as current truth:
- ⛔ **`.cap-table` was called the "highest-regret group, 13 screens".** It renders at **13.00px on
  all 13** — a page-scoped override wins everywhere. It was **never** a live violation.
- ⛔ **Eleven "MACRO reach" selectors render on NO campaign screen at all** — `.kpi-unit`,
  `.status-chip`, `.sc-k`, `.score-chip`, `.pcard-name`, `.pcard-foot`, `.dt-pg`, `.dt-pginfo`,
  `.dt-size`, `.nv-soon-tag`, `.silence`.
⇒ 🔑 **Acting on the 63 would have repainted the GUI for nothing.**


## 4 · QUEUE C — AUTHORIZED TECHNICAL WORK · **EMPTY**
> ### **Authorized technical work remaining = 0.**

⭐ **S14 and S17 are resolved; B1 and B6 are now implemented and verified.** ⛔ **No authorized
code item remains.** ⛔ B3 is ⛔ NOT authorized work — it is **blocked pending a ruling**, which
is a different thing and is tracked in Queue B, ⛔ not here.
⛔ **Technical work is NOT manufactured simply because QA discovered a latent edge case.** The
S08 finding is **reclassified into Queue D**, precisely because *"remaining technical work"* must
mean **authorised / required** work — ⛔ not every latent edge case a sweep turns up.

---

## 5 · QUEUE D — DEFERRED / MONITORED RISKS · **3**
⚠️🔑 **READ THESE AS RISK-1 / RISK-2 / RISK-3.** They are numbered D1 / D2 / D3 to match the
directive, but ⛔ **they are NOT the decision D1 / D2 / D3** (placement D1 · D2-A/B/C · D3
typography). ⭐ **A third label collision is flagged here rather than left to be discovered.**
⛔ **QUEUE D IS NOT "REMAINING TECHNICAL WORK". ⛔ None of it is authorised for implementation.**

| | risk | evidence | classification |
|---|---|---|---|
| **D1** *(risk-1)* | **S08 KPI INTERNAL OVERFLOW** 🟡 | Observed **only** with the QA/fixture value **`₹100000.00`** at 1440: 27.2px ≈ 159px inside a **150px** value box ⇒ **~9px** internal KPI overflow. **Page-level overflow = 0.** **Fits at 1920** (card 245.7). ⭐ The current reported live capital **₹10,622.70** is one character shorter and **fits**. **Pre-existing** — proved by stash-and-remeasure, ⛔ **not** caused by D2-C | **DEFERRED / MONITORED RISK.** ⛔ **NOT a current GUI closure blocker.** ⛔ **Do NOT fix it now.** ⛔ Not confirmed live; it bites only once capital reaches six figures |
| **D2** *(risk-2)* | **104 LATENT BARE-`fr` DECLARATIONS** | The sweep found **105** in total (62 explicit, 43 `repeat(n, 1fr)`). **1 was symptomatic — `.tlg-row4`, now fixed.** Across all 21 authenticated screens @1440: **0 grids at or over their content floor**, **0 pages overflowing** | ⛔ **Do NOT mass-refactor them.** ⚠️ Fixture-bound: S14's own defect was invisible until its data grew ⇒ *"no grid at its floor today"* is ⛔ not *"no grid ever will be"* |
| **D3** *(risk-3)* | **`main.content` GUARD ABSENT on S08 · S13 · S14 · S15** | **The earlier hypothesis was narrowed by measurement:** `main.content` reads **1236px on all six screens tested**, constrained and unconstrained alike ⇒ the `:has()` rule is a **safety net, ⛔ not the sizing mechanism**. S14's actual cause was `.tlg-row4` intrinsic-width expansion and is **already fixed at source** | ⛔ **Do NOT add a global / `main.content` safety rule merely because it appears useful.** ⛔ **Do NOT reopen S08 / S13 / S15 opportunistically** |

⭐ **THE DURABLE OUTPUT OF D2 IS THE MEASURABLE DETECTOR, ⛔ NOT A PATCH LIST:**
`display: grid` **+** `overflow-x: visible` **+** `scrollWidth > clientWidth`, asserted **zero**.
📌 It belongs to future **layout-assurance Option A/B**, which ⛔ **remains unauthorized** and is
⛔ **not proposed here**. ⭐ It would have caught S14 instantly.

---

## 6 · B1 / D3 — DECISION-READY TIER TABLE
⛔ **Not every `<13px` rule violates the visual requirement**, so each class carries a **KIND**:
**TEXT** = functional readable text · **COMPACT** = dense data/meta · **GLYPH** = decorative.
**Measured across the 63: 44 TEXT · 18 COMPACT · 1 GLYPH.**
📌 ⭐ The closure pass's glyph exemption covers **page-scoped** grips and sort-arrows — ⛔ those
are **not in this 63 at all**, which is why only one GLYPH appears here. ⇒ **B2 remains a
separate ruling** and is ⛔ not answered by this table.

| tier | n | KIND mix | screen reach | ARTWORK REQUIREMENT | RISK IF RAISED | RECOMMENDATION *(⛔ not a decision)* |
|---|---|---|---|---|---|---|
| **① BASE** | **4** | TEXT 4 | **ALL 21** | `.txt` 13px minimum applies — all four are readable text | ⚠️ re-opens **21** screens; ⭐ **the same radius Q2 has already paid** | ⭐ **cheapest genuine win** — could ride Q2's re-approval. `.btn-logout` 12.48px is readable text on every screen |
| **② MACRO** | **20** | TEXT 11 · COMPACT 9 | via `components.html` — `.mono` **15 scr**, `.rt-btn` **9**, `.events` **8** | mixed: mostly COMPACT data + controls | 🔴 **HIGHEST** — changes control and monospace density everywhere at once | ⛔ **do NOT raise wholesale.** If any, take `.rt-btn` / `.btn-export` (controls) and leave `.mono` (dense data) |
| **③ MULTI** | **6** | TEXT 4 · COMPACT 2 | `.cap-table` **13 scr** · `.panel-sub` **13 scr** | table density is drawn tight in every artwork | 🔴 **HIGH** — `.cap-table` governs row height on **13 screens**; raising it changes rows-per-screen | ⛔ **highest-regret group.** Defer unless a specific screen is judged illegible |
| **④ SINGLE** | **5** | TEXT 4 · COMPACT 1 | 1 screen each (S08 ×3 · S20 · S17) | per-screen | 🟢 **LOW** — one screen re-approval each | ⭐ **safe to raise individually** if any is judged illegible |
| **⑤ NO REACH** | **28** | TEXT 21 · COMPACT 6 · GLYPH 1 | legacy routes or nothing | outside the 01–22 campaign | 🟢 **~NONE on campaign screens** | ⭐ **cheapest to raise, least value.** ⛔ **Search width stated:** token grep only; **8 of 28** individually checked |

### D3 — THE 63 CLASSIFIED (px · reach · kind)

**①BASE**

| class | px | reach | kind |
|---|---|---|---|
| `.btn-logout` | 12.48 | ALL 21 | TEXT |
| `.nav-status` | 12.0 | ALL 21 | TEXT |
| `.nav-status` | 11.52 | ALL 21 | TEXT |
| `.nv-soon-tag` | 8.0 | ALL 21 | TEXT |

**②MACRO**

| class | px | reach | kind |
|---|---|---|---|
| `.events` | 12.8 | S02,S03,S08,S12,S13,S14,S15,S18 | COMPACT |
| `.flt` | 12.8 | S09,S10,S11,S12,S16,S17 | TEXT |
| `.btn-export` | 12.16 | S05,S06,S07,S10,S11,S12 | TEXT |
| `.mono` | 12.16 | S05,S06,S07,S11,S12,S13,S14,S15,S16,S17, | TEXT |
| `.dt-pg` | 11.84 | macro | COMPACT |
| `.pcard-name` | 11.84 | macro | TEXT |
| `.ps-btn` | 11.84 | S09,S10,S11 | TEXT |
| `.rt-btn` | 11.84 | S09,S10,S11,S12,S13,S14,S15,S18,S19 | TEXT |
| `.dt-pginfo` | 11.52 | macro | COMPACT |
| `.dt-size` | 11.52 | macro | COMPACT |
| `.score-chip` | 11.52 | macro | TEXT |
| `.dt-arrow` | 11.2 | macro | GLYPH |
| `.lc-ts` | 11.2 | S04 | COMPACT |
| `.kpi-unit` | 10.88 | macro | COMPACT |
| `.status-chip` | 10.88 | macro | TEXT |
| `.ts-note` | 10.88 | macro | COMPACT |
| `.kpi-label` | 10.56 | S16 | COMPACT |
| `.sev-chip` | 10.56 | S02 | TEXT |
| `.pcard-foot` | 10.24 | macro | COMPACT |
| `.sc-k` | 9.6 | macro | COMPACT |

**③MULTI**

| class | px | reach | kind |
|---|---|---|---|
| `.info-banner` | 12.8 | S02,S03 | TEXT |
| `.panel-link` | 11.84 | S08,S18 | COMPACT |
| `.panel-sub` | 11.84 | S08,S11,S12,S13,S14,S15,S16,S17,S18,S19, | COMPACT |
| `.cap-table` | 11.52 | S08,S09,S10,S11,S12,S13,S14,S15,S18,S19, | TEXT |
| `.cap-table` | 10.88 | S08,S09,S10,S11,S12,S13,S14,S15,S18,S19, | TEXT |
| `.dt-th` | 10.88 | S16,S17 | TEXT |

**④SINGLE**

| class | px | reach | kind |
|---|---|---|---|
| `.cfg-key` | 12.16 | S17 | COMPACT |
| `.cap-status` | 10.88 | S08 | TEXT |
| `.silence` | 10.88 | S20 | TEXT |
| `.bn-label` | 10.56 | S08 | TEXT |
| `.curve-meta` | 10.56 | S08 | COMPACT |

**⑤NOREACH**

| class | px | reach | kind |
|---|---|---|---|
| `.drift-banner` | 12.8 | none | TEXT |
| `.g3-list` | 12.8 | none | TEXT |
| `.cap-group-title` | 12.48 | legacy:capacity | TEXT |
| `.svc-chip` | 12.16 | none | TEXT |
| `.twg-row` | 12.16 | none | TEXT |
| `.hbar-label` | 11.84 | legacy:exposure | TEXT |
| `.hbar-val` | 11.84 | legacy:exposure | TEXT |
| `.log-table` | 11.84 | none | TEXT |
| `.wrapcell` | 11.84 | legacy:alerts,capital | TEXT |
| `.bn-sub` | 11.52 | legacy:capital,exposure,risk,statistics, | COMPACT |
| `.logpre` | 11.52 | legacy:alerts | TEXT |
| `.cap-note` | 11.2 | legacy:capacity | COMPACT |
| `.cfg-val` | 11.2 | none | TEXT |
| `.drift-key` | 11.2 | none | COMPACT |
| `.rt-label` | 11.2 | none | TEXT |
| `.failstrip` | 10.88 | none | TEXT |
| `.fam-pill` | 10.88 | legacy:alerts,capital | TEXT |
| `.svc-state` | 10.88 | none | TEXT |
| `.tw-dir` | 10.88 | none | TEXT |
| `.tw-mode` | 10.56 | none | TEXT |
| `.tw-scanners` | 10.56 | none | TEXT |
| `.dn` | 10.24 | none | TEXT |
| `.score-reasons` | 10.24 | none | TEXT |
| `.twg` | 10.24 | none | TEXT |
| `.bn-note` | 9.92 | legacy:risk,statistics | COMPACT |
| `.score-badge` | 9.92 | none | TEXT |
| `.cap-inert` | 9.28 | legacy:capacity | TEXT |
| `.mf-step` | 8.96 | none | TEXT |

---


---

## 7 · FINAL PROJECT COMPLETION — WHAT IS STILL REQUIRED
⛔ **"Technically complete" is ⛔ NOT "project complete".** All seven must land, in order:

| | prerequisite | state |
|---|---|---|
| **1** | **Rama's visual approval** of every screen still pending | 🔴 **22 pending** — §8 |
| **2** | **Resolution of the six business / spec decision groups** required for the final specification | ⚠️ **5 resolved · 🔴 1 left (B3)** — §3 |
| **3** | **Final 01–22 regression confirmation** *after* all approved changes | ✅ **run after B1–B6** — full ops_dashboard suite + a 22-screen browser sweep; see §10 |
| **4** | **Clean working tree** | ✅ CLEAN |
| **5** | **Local commits + unpushed ledger updated** | ✅ this pass included |
| **6** | **Deployment gate / `gui-dashboard.service` verification** — ⛔ **only after Rama's explicit final authorization** | ⛔ not started, ⛔ not authorized |
| **7** | **Push / deployment** — ⛔ only when explicitly authorized | ⛔ **PUSHED = NO · DEPLOYED = NO** |

---

## 8 · VISUAL APPROVAL CHECKLIST
📸 🔑 **THE CAPTURES NOW EXIST — `docs/audit/approval_final_19aug/`**: one
**full-height** PNG per screen at **1920 wide**, plus **`INDEX.md`**, which maps every screen to
its capture, its old artwork, its measured height, its page-overflow and sub-13px counts, and the
**one deviation** worth judging on it. ⭐ Heights are each page's real `scrollHeight` — ⛔ not
padded canvas, and ⛔ not the probe route (whose JSON block would have appeared in the image).

⛔ **Not "approve all 22".** Each row below names the **ONE area that changed** and the check
that would catch a regression there. Compare every pending screen against its old design in
**`D:\Projects\trading-system\gui\<NN. Name>.png` + `.txt`**.

### 8·0 · THE STANDING COMPARISON — applies to every screen below
| # | verify | # | verify |
|---|---|---|---|
| 1 | same **colours** | 8 | **pie / donut charts** present and correct |
| 2 | same **fonts** | 9 | **graph charts** present and correct |
| 3 | same **headings** | 10 | **real-time dashboard data areas** live and populating |
| 4 | **table alignment** | 11 | **screen-space utilisation** |
| 5 | **strategy — ⛔ NOT scanner — as the operational entity** | 12 | **no page overflow** |
| 6 | **drag / move placement** | 13 | **no clipping** · **no unintended wrapping** |
| 7 | **populated-data appearance** | 14 | matches the artwork *unless* the written **TXT / spec explicitly overrides it** |
⚠️ **Where written TXT/spec explicitly overrides artwork, the written spec governs** (this is how
**Q3** was ruled on S03). ⛔ **Neither source may be silently reinterpreted.**

### 8a · ⛔ ONE CHECKLIST, ⛔ NOT TWO
🔑 **The per-screen rows now live in `docs/audit/approval_final_19aug/INDEX.md`, beside the
captures.** ⛔ They are deliberately **not** duplicated here: two checklists drift, and a drifting
checklist is worse than one. What stays here is the **grouping** and the **two facts that changed
the grouping today**.

### 8b · WHAT B1 DID TO THE GROUPS — ⭐ read this before using the old grouping
⚠️ **B1's `.btn-logout` raise reaches ALL 21 shared-CSS screens**, exactly as Q2 did. ⇒ the
"Q2 chrome strip only" group now carries **TWO** shared changes, ⛔ not one:
| what | where | check |
|---|---|---|
| **Q2** — nav headings · TRADER/MODE/KILL/PHASE labels · status pills · "poll" · "version" | `base.html`, all 21 | still fit their strip, wrap nowhere, have not pushed the page |
| 🆕 **B1** — the **Log out** button | `base.html`, all 21 | 12.48px → **13px**; still fits the top-right strip beside the clock |
📌 ⭐ **The page BODIES of the eleven strip-only screens still did not change** — ⛔ a full re-audit
is still not required.

### 8c · THE THREE GROUPS, AS THEY NOW STAND
| group | n | screens | ⭐ what to look at |
|---|---|---|---|
| **1 · FIRST APPROVAL** | **6** | **S01** *(⛔ outside every shared radius · ⚠️ never browser-tested before today)* · S04 · S05 · S06 · S07 | full pass vs the artwork · **S07: RR Damage % is ABSENT and stays absent (B3)** |
| **2 · RE-APPROVAL — CHANGED TODAY** | **6** | **S02** 🆕 *(B1 banner + chips)* · **S03** *(Q3 columns · 🆕 B6 export live · 🆕 B1 filter labels)* · **S09** · **S12** · **S14** · **S17** | the named area on each — see `INDEX.md` |
| **3 · RE-APPROVAL — SHARED CHROME ONLY** | **10** | S10 · S11 · S13 · S15 · S16 · S18 · S19 · S20 · S21 · S22 | the **strip + the Log out button**, plus a page-overflow glance |
⚠️ **S02 MOVED from group 3 to group 2** — B1 changed its alert banner and severity chips, so it is
⛔ no longer a strip-only re-approval. ⭐ Stated rather than left for Rama to discover.


## 9 · WHAT IS **NOT** BEING DONE
⛔ No source change · ⛔ **no S08 fix** · ⛔ no change to the **104 latent bare-`fr`** declarations ·
⛔ **no D3 implementation** · ⛔ no alteration of **glyph exemptions** · ⛔ no **S07 RR Damage %** ·
⛔ no change to the **rejection taxonomy** · ⛔ no change to **S03 capital semantics** · ⛔ **S03
export not enabled** · ⛔ **no push** · ⛔ **no deploy** · ⛔ no layout-assurance harness · ⛔ no
visual redesign · ⛔ **S14 not reopened** (`main.content`, breakpoint, `.tlg-row4`, rail, table
widths, global overflow — all untouched) · ⛔ no unrelated screen reopened because a different
screen was fixed · ⛔ **the environment was NOT modified to eliminate the known
`test_c_venv_has_no_kiteconnect` failure** · ⛔ no prior implementation commit amended.
📌 **Live trading is active** — this pass touched ⛔ no live order, ⛔ no position, ⛔ no broker
state, ⛔ no production DB, ⛔ no production configuration, ⛔ no trading process. QA data is
isolated; VM evidence is read-only.

---

## 10 · FINAL REGRESSION — ⛔ RUN **AFTER** ALL AUTHORISED WORK, ⛔ NOT BEFORE

### 10a · TEST SUITE
| | result |
|---|---|
| **full `ops_dashboard` suite** | **1 failed · 2,067 passed** (574.90 s) |
| the one failure | `test_isolation::test_c_venv_has_no_kiteconnect` — **known environmental**, ⛔ **untouched**; ⛔ the environment was **not** modified to make it green |
| **new failures** | ⭐ **ZERO** |
| baseline before this phase | 1 failed · **2,045** passed ⇒ **+22 tests**, all new and all passing |
📌 **The +22:** B1 typography **6** · B3/B4 spec decisions **8** · Screen 03 (B5 guard + B6 export + the behavioural filter test) **8**.
⚠️ **An earlier run of this suite was DISCARDED, ⛔ not reported:** it started before the last test
was added, so its number would have described code that no longer existed. ⭐ **A stale green is
still a false statement.**

### 10b · BROWSER SWEEP — ALL 22 SCREENS
| measurement | 1440 × 900 | 1920 × 1080 |
|---|---|---|
| **page-level horizontal overflow** | ⭐ **0 of 22** | ⭐ **0 of 22** |
| **sub-13px readable text, S02–S22** | ⭐ **0** | ⭐ **0** |
| **sub-13px remaining** | **11 decorative glyph classes** (grips 8.8px · sort arrows 9.0–9.28px) — **B2** | same |
| **S01** | `<label>` 12.48 · `.foot` 11.52 — ⛔ **no written px minimum exists for S01** | same |
| **SVG user-unit text** | ⭐ **0 below the floor** — `.cap-g-tick` 13.39 · `.sr-donut` 13.04 · `.sh-donut` 13.02 · `.hld-donut` 20.00 / 13.07 | 13.63 / 13.04 / 13.02 / 20.00 / 13.07 |
📌 **Rendered, ⛔ not declared:** SVG font-size is in **user units**, so `rendered = declared ×
(container ÷ viewBox)`, taken from `getScreenCTM()`. ⛔ A declared value proves nothing here.

### 10c · THE EXPORT, VERIFIED BY OPENING THE WORKBOOK
⛔ **Not "it returned 200".** The file was downloaded and parsed:
**5 rows + header, 18 columns, `freeze_panes A2`**, and the values match the rendered table cell
for cell (`Gap Fade Long` → 70 · 7 · 4 · 85.7% · 50% · 1 · 1 · ₹100.00 · 0.5% · ₹70,000 · ₹10,000 ·
₹60,000 · 14.3%). Filters driven through the real endpoint: **Intraday 3 · Delivery 1 · LONG 4 ·
one strategy 1 · unmatched value 0**.
🔑 ⚠️ **A near-miss worth recording:** my first hand-check used `trade_type=DELIVERY` and returned
an **empty** workbook. That was **my** wrong input — the screen's `<option>` emits `Delivery` — but
**a real case mismatch would look identical**, and every source-string assertion would still have
passed. ⇒ the test now drives the endpoint with **the values the screen actually emits**, and
asserts an unmatched value yields **0 rows, ⛔ never the whole set**.

### 10d · WHAT THE REGRESSION DOES **NOT** COVER — ⛔ stated, not implied
- ⛔ **Fixture data only.** The sweep describes the **QA fixture**, ⛔ not production. S08's KPI
  overflow is exactly this class: it needs a **six-figure** capital value to appear.
- ⛔ **Two viewports only** — 1440 and 1920. S14 and S17 were swept across **8** and **10** widths
  when they were fixed; the other twenty were ⛔ **not**.
- ⛔ **No interaction sweep.** Tabs, modals, sort states and collapsed panels are ⛔ **not** in the
  sub-13px numbers — the probe sees what is **rendered**, and unrendered markup cannot be measured.
- ⛔ **Nothing was verified LIVE.** ⛔ **DEPLOYED = NO** ⇒ ⛔ nothing here is `VERIFIED LIVE`.
