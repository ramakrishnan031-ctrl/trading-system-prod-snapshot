# GUI FINAL CLOSURE — STATUS MODEL, FOUR QUEUES, D3 TIERS, APPROVAL CHECKLIST
**19-Aug-2026 ~19:55 IST · tree CLEAN · PUSHED = NO · DEPLOYED = NO**
Suite **1 failed / 2,045 passed** — sole failure the known environmental
`test_isolation::test_c_venv_has_no_kiteconnect`, ⛔ **untouched** (⛔ the environment was ⛔ not
modified to make it pass).
⛔ **No source changed in this pass. Documentation only.**

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
| **BUSINESS / SPEC DECISIONS** | **6 groups open** | Queue B |
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
**6 + 5 + 11 = 22**, every screen appearing exactly once, S01–S22 fully covered.

| group | n | screens |
|---|---|---|
| **1 · FIRST APPROVAL** | **6** | **S01** ⭐ *(pre-auth · standalone `login.html` · ⛔ **independent of Q2** · ⚠️ never browser-tested)* · S04 · S05 · S06 · S07 · **S08** |
| **2 · RE-APPROVAL — CHANGED TODAY** | **5** | **S03** *(Q3 columns)* · **S09** *(axis labels)* · **S12** *(collision fix)* · **S14** *(overflow)* · **S17** *(4px + KPI ramp)* |
| **3 · RE-APPROVAL — Q2 SHARED-CHROME STRIP** | **11** | S02 · S10 · S11 · S13 · S15 · S16 · S18 · S19 · S20 · S21 · S22 |

⭐ **Q2 RE-QA STAYS TARGETED — measured, ⛔ not assumed:** all five raised classes occur in
**`base.html` and nowhere else** (18 occurrences there; **0** across the other 29 templates) ⇒
the changed surface is the **sidebar + top status strip**, identical on all 21. ⇒ check **that
strip plus a page-overflow glance at the required viewport**, ⛔ **not** a full re-audit of
unchanged page bodies.

---

## 3 · QUEUE B — BUSINESS / SPECIFICATION DECISIONS · **6 open** ⛔ no code until ruled

### 🔑 THE S03 NAMING SEPARATION — ⛔ TWO UNRELATED THINGS ARE BOTH CALLED "D1"
| item | status |
|---|---|
| **S03 PLACEMENT DECISION (placement D1)** — SL / TGT / ROI main-table placement, options A/B/C | ✅ **CLOSED — ACCEPTED = A.** Current main-table placement accepted. ⛔ It was **never** an unresolved item |
| **S03 CAPITAL SEMANTICS** — *historically shorthanded "S03 D1/D2"* | 🔴 **OPEN** — the Allocated / Used / Remaining derivation and the per-strategy allocation-cap question |
⛔ **The two must never be merged under one label again**: the shorthand can make a resolved item
look open, or an open item look resolved.

| | group | what is owed | ⛔ prohibition standing tonight |
|---|---|---|---|
| **B1** | **D3 TYPOGRAPHY — the 63 remaining sub-13px rules** | a **risk-based ruling per tier** — see §6 | ⛔ do **NOT** raise all 63 automatically |
| **B2** | **DECORATIVE-GLYPH EXEMPTION** | an explicit ruling; ⭐ kept **separate** from the 63-rule classification | ⛔ do **not silently broaden** it — the existing exemption concerns **page-scoped grips / sort-arrows** (`.aud-grip`, `.exec-grip`, `.st-arw`) and does ⛔ **not** mean every sub-13px glyph is automatically exempt |
| **B3** | **S07 RR DAMAGE %** | a ruling. `rr_damage_pct` lives in `trade_slippage_log`, a table Trade Explorer does not read; plus a prior ruling that R-multiple is never printed in an R:R column | ⛔ do not add or change the field without explicit ruling |
| **B4** | **REJECTION TAXONOMY (Screen 02 funnel)** | a ruling. `_RISK_REJECT_STATUSES` (10) / `_CAPITAL_REJECT_STATUSES` (3) are frozen tuples pinned by test | ⛔ do not alter shared rejection status semantics |
| **B5** | **S03 CAPITAL SEMANTICS** | a ruling on Allocated / Used / Remaining and the per-strategy cap. Backend declares `allocation_configured: None`, `allocation_basis: "global bucket"`; the UI derives `alloc = used + remaining` | ⛔ do not implement per-strategy allocation caps, ⛔ do not alter capital derivation |
| **B6** | **S03 EXPORT (F1 / Q3)** | the **Q3 copy-protection acceptance** ruling — ⭐ a **data-egress** question, ⛔ not a UI one. Currently a disabled stub | ⛔ do not enable XLSX / export functionality without explicit authorization |

---

## 4 · QUEUE C — AUTHORIZED TECHNICAL WORK · **EMPTY**
> ### **Authorized technical work remaining = 0.**

⭐ **S14 and S17 are resolved. ⛔ No authorized code item remains.**
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
| **2** | **Resolution of the six business / spec decision groups** required for the final specification | 🔴 **6 open** — §3 |
| **3** | **Final 01–22 regression confirmation** *after* all approved changes | ⏳ not yet — nothing approved yet |
| **4** | **Clean working tree** | ✅ CLEAN |
| **5** | **Local commits + unpushed ledger updated** | ✅ this pass included |
| **6** | **Deployment gate / `gui-dashboard.service` verification** — ⛔ **only after Rama's explicit final authorization** | ⛔ not started, ⛔ not authorized |
| **7** | **Push / deployment** — ⛔ only when explicitly authorized | ⛔ **PUSHED = NO · DEPLOYED = NO** |

---

## 8 · VISUAL APPROVAL CHECKLIST
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

### 8a · GROUP 2 — RE-APPROVAL, CHANGED TODAY ⭐ *look here first*
| Screen | Key changed area | Required visual check | Status |
|---|---|---|---|
| **S03** Strategies | **4 new main-table columns**: Trading Type (col 2) + SL Hit · TGT Hit · ROI % | Trading Type reads Intraday/Delivery/`—` and sits **immediately after Strategy** · **Scanner still absent** (point 5) · ROI shows **`—` not `0%`** where null · row alignment unchanged (labels left, numerics right) · table still fits, no page scroll | ⏳ |
| **S09** P&L Analytics | **Equity-curve axis labels (new)** | ₹ scale down the left, session timeline underneath · labels **clear of the plot**, none clipped at the panel edge · curve shape unchanged · **compare against `09. PnL_Analytics.png`** | ⏳ |
| **S12** System Health | **Health-trend x-axis** (Disk tab) | click **Disk** — the chart's **last two time labels no longer overlap** · y-axis values still present · CPU/RAM/Response still read **NOT INSTRUMENTED** (correct, ⛔ not a bug) | ⏳ |
| **S14** Trade Logs | **Bottom row-4 panels** (`.tlg-errs` / `.tlg-export`) | **no sideways page scroll at 1440** · the **rail stays beside** the main table · Recent-Errors and Export panels now sit in their **intended 1.6 : 0.8 proportion** (Export was previously squashed) · event table still scrolls **inside** its own region | ⏳ |
| **S17** Controls | **Control-History rail rows + KPI values** | no sideways page scroll at any width · **Control History** action text wraps rather than spilling out of the rail · **`Last Control Change`** value wraps inside its card ⚠️ *(it renders a **raw ISO timestamp** — see note)* | ⏳ |

⚠️ **S17 note, so it is judged rather than discovered:** `Last Control Change` displays the raw
`2026-08-19T17:05:00+05:30`. The **overflow is fixed**, but **the value is unformatted** — the
real remedy is to format it, which is a **template** change outside D2-C's authorised CSS-only
shape. ⛔ Reported, ⛔ not fixed.

### 8b · GROUP 3 — RE-APPROVAL, Q2 CHROME STRIP ONLY ⭐ *one identical check, eleven screens*
| Screens | Key changed area | Required visual check | Status |
|---|---|---|---|
| S02 · S10 · S11 · S13 · S15 · S16 · S18 · S19 · S20 · S21 · S22 | **Sidebar + top status strip only** (`base.html`) | **TRADING / ANALYTICS / OPERATIONS / INVESTIGATION** nav headings · **TRADER / MODE / KILL / PHASE** labels · the status **pills** · **"poll 5s"** · **"version 2.0.0"** — all now **13px**; check they still fit their strip, wrap nowhere, and have not pushed the page | ⏳ |
📌 ⭐ **The page bodies of these eleven did NOT change** — ⛔ a full re-audit is not required, only
the strip plus a page-overflow glance at the required viewport.

### 8c · GROUP 1 — FIRST APPROVAL ⭐ *never approved before*
| Screen | Key changed area | Required visual check | Status |
|---|---|---|---|
| **S01** Login | ⛔ **NOTHING changed** — standalone `login.html`, own inline `<style>`, **never loads `style.css`** | Full first-approval pass against `01. Login-Screen.png`. ⛔ **Independent of Q2** · ⚠️ **never browser-tested** (pre-auth; the QA harness force-authenticates) | ⏳ |
| **S04** Signals | Q2 strip only | full first-approval pass vs `04. Signals.png` + the strip | ⏳ |
| **S05** Orders | Q2 strip only | full first-approval pass vs `05. Orders.png` — ⭐ incl. the **two donuts** restored in the closure pass | ⏳ |
| **S06** Positions | Q2 strip only | full first-approval pass vs `06. Positions.png` | ⏳ |
| **S07** Trade Explorer | Q2 strip only | full first-approval pass vs `07. Trade_Explorer.png` — ⚠️ **RR Damage % is deliberately absent** (B3) | ⏳ |
| **S08** Capital & Risk | Q2 strip only | full first-approval pass vs `08. Capital_Risk.png` — ⭐ incl. the **gauge + top-consumer bars** restored in the closure pass · ⚠️ **note Queue D risk-1**: at 1440 `Opening Cash` / `Total Real Cash` can overrun their card by ~9px with six-figure values | ⏳ |

---

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
