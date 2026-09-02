# GUI CLOSURE — DECISION-READY MATRIX (S01–S22) + D1/D2/D3 EVIDENCE
**19-Aug-2026 ~16:15 IST. ⛔ EVIDENCE ONLY — no source touched, nothing implemented.**
HEAD `cf3d0d5`, tree CLEAN, **47 ahead / 5 behind**, **PUSHED = NO · DEPLOYED = NO**.
Suite: **1 failed / 2,040 passed (545.53 s)**, sole failure the known environmental
`test_isolation::test_c_venv_has_no_kiteconnect` — ⛔ untouched.

📌 **TWO CORRECTIONS TO THE INCOMING CARD'S BASIS:** it cites `2a01398d` / **46 ahead**. Since
then **`cf3d0d5`** was committed (the S14/S17 blast radius this card asks for as item 3), so
HEAD is `cf3d0d5` and the branch is **47 ahead**.

## STATUS VOCABULARY
**IMPL-COMPLETE** = code built + tests pass · **VISUAL-APPROVAL-REQUIRED** = awaiting Rama's eyes
· **BLOCKED** = a measured defect prevents approval · **PENDING-DECISION** = a business/data call
is owed · **CLOSED** = approved and done. ⛔ **No screen is CLOSED. ⛔ Tests passing ≠ closure.**

## 1 · THE MATRIX

| SCREEN | CURRENT STATE | EVIDENCE | BLOCKER / DECISION | NEXT ACTION |
|---|---|---|---|---|
| **S01 Login** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | ⛔ **never browser-tested** (pre-auth; harness force-authenticates). `login.html` standalone, inline `<style>`, **never loads `style.css`** | ⛔ **OUTSIDE Q2 and outside every shared-CSS radius** | first visual approval, on its own merits |
| **S02 Dashboard** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept 1920+1440, 0 overflow | Q2 chrome (sidebar/topbar only) | re-approve chrome region |
| **S03 Strategies** | ⚠️ **CHANGED TODAY** · VISUAL-APPROVAL-REQUIRED · **PENDING-DECISION** | `bb4af7a`; capture ×2; 0 overflow with 4 new columns | 🔴 **D1** placement of SL/TGT/ROI + Q2 chrome | rule D1, then approve |
| **S04 Signals** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | Q2 chrome | first approval |
| **S05 Orders** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | Q2 chrome | first approval |
| **S06 Positions** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | Q2 chrome | first approval |
| **S07 Trade Explorer** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED · **PENDING-DECISION** | swept, 0 overflow (28 dynamic cols) | **RR Damage % absent** (parked) + Q2 chrome | first approval; RR Damage % separate |
| **S08 Capital & Risk** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | Q2 chrome | first approval |
| **S09 P&L Analytics** | ⚠️ **CHANGED TODAY** · VISUAL-APPROVAL-REQUIRED | `8b6090b`; captures ×3 incl. full-height; **9 labels @13.00px, 0 escaping** | Q2 chrome | re-approve |
| **S10 Slippage** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | typography pass + Q2 chrome | re-approve |
| **S11 Execution** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | typography pass + Q2 chrome | re-approve |
| **S12 System Health** | ⚠️ **CHANGED TWICE** · VISUAL-APPROVAL-REQUIRED | `e2022d5`; captures ×3; **12 labels @13.00px, min gap 37.1px** | Q2 chrome | re-approve |
| **S13 Audit** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | Q2 chrome | approval |
| **S14 Trade Logs** | 🔴 **BLOCKED** | **@1440 `1760/1416` = 344px**; clean at clientWidth ≤1400; pre-existing (stash-proven) | 🔴 **D2** | rule D2; ⛔ no approval until resolved |
| **S15 System Logs** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | Q2 chrome | approval |
| **S16 Configuration** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | Q2 chrome | approval |
| **S17 Controls** | 🔴 **BLOCKED** | **@1920 4px · @1440 83px · @2200 4px**; two defects (S17-a constant, S17-b ramp); pre-existing | 🔴 **D2** | rule D2; ⛔ no approval until resolved |
| **S18 Live Activity** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | Q2 chrome | approval |
| **S19 Strategy Ranking** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | Q2 chrome | approval |
| **S20 Strategy Health** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | Q2 chrome (+ **D1 option B host**) | approval |
| **S21 Scanner Attribution** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | Q2 chrome | approval |
| **S22 Holdings** | IMPL-COMPLETE · VISUAL-APPROVAL-REQUIRED | swept, 0 overflow | Q2 chrome | approval |

**CLOSED: 0 of 22.** ⛔ No screen is closed on passing tests.

### ⭐ RULE 8 — Q2's RE-QA SCOPE IS *TARGETED*, ⛔ NOT A FULL RE-AUDIT
**Measured:** all five raised classes live **only in `base.html`** — `.nv-group` ×4, `.sb-label`
×8, `.sb-poll` ×1, `.sb-pill` ×4, `.sb-ver` ×1 — i.e. the **sidebar nav + top status bar**, and
that chrome is **identical on all 21** campaign templates that extend it. ⛔ `login.html` does
not extend it (confirmed).
⇒ ⭐ **the affected REGION is the same strip on every screen, so re-QA can be scoped to that
strip plus a page-overflow check, ⛔ rather than re-auditing 21 full screens.** ⛔ The 9 legacy
routes also render it but are **outside the 01–22 approval scope**.

## 2 · D1 — S03 PLACEMENT · **SMALLEST CHANGE FIRST**

| option | change required | cost | re-opens |
|---|---|---|---|
| **A — accept current main-table placement** | ⭐ **NONE. Already built and captured.** | **zero** | only S03's own pending approval |
| **B — move to an existing surface (S20 Strategy Health)** | remove 3 columns from S03 + add a per-strategy block to S20 | moderate; S20 has no per-strategy detail block either — it is a ranking/health screen | **S03 *and* S20** |
| **C — new detail surface on S03** | build a per-strategy detail page/drawer + routing + tests | ⚠️ **largest** — a new surface, not a field move | S03, plus a new screen with no artwork |

⭐ **A is the smallest change and is the only option requiring no code.** ⛔ **C creates UI that
no artwork specifies** — the brief forbids a speculative detail page.
📌 **The honest tension:** the TXT places these three under `DETAIL PAGE → Additional`, so **A is
an interpretation, ⛔ not literal compliance** — that is exactly what D1 must rule.

## 3 · D2 — S14/S17 BLAST RADIUS (full detail in `S14_S17_OVERFLOW_DIAGNOSIS_19-Aug-2026.md`, `cf3d0d5`)

| | **S14** | **S17-a** | **S17-b** |
|---|---|---|---|
| defect | breakpoint `max-width:1400px` vs a **1416px** clientWidth | constant **~4px** at every width incl. 2200 | ramp: +19@1800 → +89@1380 |
| files | `style.css` only | `style.css` only | `style.css` only |
| selectors | `.tlg-page .tlg-work` | `.ctl-page .ctl-hrow/.ctl-hist` | `.ctl-page .ctl-shell/.ctl-rail/.ctl-row*` |
| **shared CSS involved?** | ⛔ **NO** — page-scoped | ⛔ **NO** | ⛔ **NO** |
| independent of each other? | ✅ different pages | ✅ | ✅ |
| **other numbered screens needing regression QA** | ⛔ **NONE** | ⛔ **NONE** | ⛔ **NONE** |
| size | **one number** | small | ⚠️ **not small** — ~700px band |

✅ **Every rule is `.tlg-page`/`.ctl-page`-scoped, 0 unscoped rules, each class in exactly one
template** ⇒ **no approved screen is re-opened by either fix, and S14 and S17 can be fixed
independently.** ⚠️ **But "isolated" ≠ "small": S17-b needs a width-sweep verification, and S14
hides a design call (should the rail stack at 1440, or shrink?).**

## 4 · D3 — THE 63 RULES, GROUPED BY ACTUAL BLAST RADIUS
🔑 **"63 rules" conflates five very different risks. ⛔ Do not decide it as one number.**

| tier | classes | renders on | risk if raised |
|---|---|---|---|
| **① BASE chrome** | **4** — `.nv-soon-tag` 8.0 · `.nav-status` 11.52 & 12.0 · **`.btn-logout` 12.48** | **all 21** (same strip as Q2) | ⚠️ same radius as Q2, re-opens 21 |
| **② SHARED MACRO** (`components.html`) | **20** — incl. `.mono` (15 scr) · `.rt-btn` (9) · `.events` (8) · `.btn-export` (6) · `.flt` (6) · `.dt-*` pager · `.kpi-label` · `.status-chip` | many, via macro import | 🔴 **HIGHEST — drives control + table density everywhere** |
| **③ MULTI-SCREEN** | **6** — **`.cap-table` (13 screens)** · **`.panel-sub` (13)** · `.dt-th` (2) · `.panel-link` (2) · `.info-banner` (2) | 2–13 screens | 🔴 **`.cap-table` is the single widest class — table density on 13 screens** |
| **④ SINGLE-SCREEN** | **5** — `.bn-label`/`.curve-meta`/`.cap-status` (S08) · `.silence` (S20) · `.cfg-key` (S17) | 1 screen each | 🟢 **cheapest, narrowest** |
| **⑤ NOT ON ANY CAMPAIGN SCREEN** | **28** | legacy routes or nothing | 🟢 **little/no campaign impact** |

**⑤ splits further — spot-checked 8 of 28:**
- **legacy-route only** (outside the 01–22 scope): `.logpre`→`alerts` · `.hbar-label`→`exposure` ·
  `.fam-pill`→`alerts`,`capital` · `.cap-inert`→`capacity`
- **referenced by NO template at all**: `.failstrip` · `.svc-state` · `.twg` · `.drift-banner`

⛔⛔ **SEARCH WIDTH, STATED: "not on any campaign screen" means a TOKEN GREP of the 21 campaign
templates found nothing.** ⛔ It does **not** prove the class is dead — a dynamically-composed
`:class` binding would be missed. ⛔ **And only 8 of the 28 were spot-checked; the split is NOT
extrapolated to the other 20.**

⭐ **RECOMMENDATION — ⛔ NOT "raise all":** the tiers are separately decidable. **④ (5 classes)
is nearly free. ⑤ (28) is close to free but needs its usage claim widened before acting. ① (4)
carries exactly Q2's radius and could ride the same re-approval. ② and ③ are the real cost — 26
classes driving shared controls and tables across up to 13 screens each.**
⛔ **I am not recommending an option; the grouping is so Rama can choose per tier rather than
per-63.**

## 5 · REMAINING APPROVAL BLOCKERS
1. 🔴 **D1** — S03 placement (S03 blocked) · 2. 🔴 **D2** — S14 + S17 (both blocked) ·
3. 🔴 **D3** — 63 rules, per tier · 4. **D5** — 22 visual approvals, none granted ·
5. **Parked:** S07 RR Damage % · rejection taxonomy · S03 D1/D2 · S03 export · Gate E ·
layout-assurance Option A.
