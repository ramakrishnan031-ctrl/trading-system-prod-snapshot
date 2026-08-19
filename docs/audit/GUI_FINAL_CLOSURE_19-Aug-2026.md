# GUI FINAL CLOSURE — FOUR QUEUES, D3 TIERS, AND THE APPROVAL CHECKLIST
**19-Aug-2026 ~19:50 IST · HEAD `5dfbdbf` · tree CLEAN · 54 ahead / 5 behind**
**PUSHED = NO · DEPLOYED = NO** · suite **1 failed / 2,045 passed**, known environmental
`test_isolation::test_c_venv_has_no_kiteconnect` — ⛔ untouched.
⛔ **No source changed in this pass.**

## 🔑 STATEMENTS REQUIRED
> ### **Authorized technical work remaining = 0.**
> ### **Deferred monitored risk: S08 KPI internal overflow.**

## 1 · FINAL GUI STATUS
| | count | detail |
|---|---|---|
| **Technically closed** (code · tests · browser all complete) | **22 of 22** | ⭐ incl. **S14** `8f78c67` and **S17** `b14dea3`, the only two ever genuinely BLOCKED |
| **Awaiting visual approval** | **20** | ⛔ **0 screens CLOSED** — approval is Rama's, ⛔ never assumed |
| **Business / spec decisions open** | **6** | Queue B |
| **Deferred / monitored risks** | **3** | Queue D |
📌 **S01 and S22 are not in the 20** — S01 needs first approval but is ⛔ **outside Q2's radius**;
it is listed in Queue A separately. *(20 = the Q2-affected campaign screens + those changed
today; S01 is the 21st line and is marked independent.)*

---

## 2 · THE FOUR QUEUES

### QUEUE A — VISUAL APPROVAL REQUIRED
⛔ **None may be marked closed without Rama's visual confirmation.**

| category | screens |
|---|---|
| **First approval** | **S01** ⭐ *(pre-auth · standalone `login.html` · never loads `style.css` · ⛔ **independent of Q2**)* · S04 · S05 · S06 · S07 |
| **Re-approval — changed today** | **S03** *(Q3 columns)* · **S09** *(axis labels)* · **S12** *(collision fix)* · **S14** *(overflow)* · **S17** *(4px + KPI ramp)* |
| **Re-approval — Q2 chrome strip only** | S02 · S10 · S11 · S13 · S15 · S16 · S18 · S19 · S20 · S21 · S22 |

### QUEUE B — BUSINESS / SPECIFICATION DECISIONS · **6 open**
🔴 **A NAMING COLLISION IS RESOLVED HERE, ⛔ NOT PAPERED OVER — there are TWO items called
"D1", and they are unrelated:**

| | item | status |
|---|---|---|
| — | **PLACEMENT D1** — SL/TGT/ROI main-table placement (A/B/C) | ✅ **CLOSED — ACCEPTED = A.** ⛔ Never listed as unresolved |
| **B-1** | **D3 — the remaining 63 sub-13px rules** | 🔴 OPEN — rule **per tier**, ⛔ no blanket conversion |
| **B-2** | **Decorative-glyph exemption** | 🔴 OPEN — every `.txt` sets a 13px minimum; the closure pass exempted grips/sort-arrows and ⛔ **that exemption was never ruled on** |
| **B-3** | **S07 RR Damage %** | 🔴 OPEN — `rr_damage_pct` lives in `trade_slippage_log`, a table Trade Explorer does not read; plus a prior ruling that R-multiple is never printed in an R:R column |
| **B-4** | **Rejection taxonomy (Screen 02 funnel)** | 🔴 OPEN — `_RISK_REJECT_STATUSES` (10) / `_CAPITAL_REJECT_STATUSES` (3), frozen tuples pinned by test |
| **B-5** | **S03 EXPORT (F1/Q3)** | 🔴 OPEN — disabled stub; blocked on the **Q3 copy-protection acceptance** ruling, ⭐ a data-egress question, ⛔ not a UI one |
| **B-6** | **S03 CAPITAL SEMANTICS** — ⚠️ *historically labelled "S03 D1/D2"; ⛔ **NOT** the placement D1* | 🔴 OPEN — no per-strategy cap exists; backend declares `allocation_configured: None`, `allocation_basis: "global bucket"`; UI derives `alloc = used + remaining` |

### QUEUE C — AUTHORIZED TECHNICAL WORK · **EMPTY**
> **Authorized technical work remaining = 0.**

⭐ **S14 = technically closed. S17 = technically fixed.** ⛔ No other technical item is
authorized. ⛔ **C-1 is NOT manufactured into work** — it is reclassified into Queue D, exactly
because *"remaining technical work"* must mean **authorised/required** work, ⛔ not every latent
edge case QA turns up.

### QUEUE D — DEFERRED / MONITORED RISKS · **3** ⛔ none authorised for implementation
| | risk | evidence | why deferred |
|---|---|---|---|
| **D-1** | **S08 KPI internal overflow** 🟡 | `₹100000.00` at 27.2px ≈ 159px in a **150px** value box @1440 ⇒ `over 9`. Fits at 1920 (card 245.7). **Page overflow 0 at both.** Pre-existing — stash-and-remeasure proved it, ⛔ not caused by D2-C | ⭐ **fixture-sensitive**: today's real capital **₹10,622.70** is one character shorter and **fits**. ⛔ **Not confirmed in live data.** Bites only at six-figure capital |
| **D-2** | **104 latent bare-`fr` declarations** | 105 found; **1 was symptomatic (`.tlg-row4`, fixed)**. Sweep of all 21 screens @1440: **0 grids at or over their content floor** | ⛔ **NOT proof future data can never trigger them** — S14's own was invisible until its data grew. ⛔ **No mass refactor.** ⭐ The durable output is the **predicate**, ⛔ not a patch list |
| **D-3** | **`main.content` guard absent on S08 · S13 · S14 · S15** | `main.content` measures **1236px on all six screens tested**, constrained and unconstrained alike | ⭐ Re-measured and **narrowed**: the `:has()` rule is a **safety net, ⛔ not the sizing mechanism**; inert until content pushes against it. S14's cause was fixed at source instead |

⭐ **THE BARE-`fr` PREDICATE — recorded as a future capability, ⛔ not a task:**
`display: grid` **+** `overflow-x: visible` **+** `scrollWidth > clientWidth`, asserted **zero**.
⛔ **Layout-assurance Option A/B is NOT implemented and is not proposed here.**

---

## 3 · D3 DECISION-READY TIERS
⛔ **Not every `<13px` rule violates the visual requirement**, so each class carries a **KIND**:
**TEXT** = functional readable text · **COMPACT** = dense data/meta · **GLYPH** = decorative.
**Measured across the 63: 44 TEXT · 18 COMPACT · 1 GLYPH.**
📌 ⭐ The closure pass's glyph exemption covers **page-scoped** grips and sort-arrows
(`.aud-grip`, `.exec-grip`, `.st-arw`) — ⛔ those are **not in this 63 at all**, which is why only
one GLYPH appears here.

| tier | n | reach | ARTWORK REQUIREMENT | RISK IF RAISED | RECOMMENDATION *(⛔ not a decision)* |
|---|---|---|---|---|---|
| **① BASE** | **4** | **ALL 21** | `.txt` 13px minimum applies — all readable | ⚠️ re-opens **21** screens; ⭐ **same radius Q2 already paid** | ⭐ **cheapest genuine win** — could ride Q2's re-approval. `.btn-logout` 12.48 is readable text on every screen |
| **② MACRO** | **20** | via `components.html` — `.mono` **15 scr**, `.rt-btn` 9, `.events` 8 | mixed: mostly COMPACT data + controls | 🔴 **HIGHEST** — changes control and monospace density everywhere | ⛔ **do NOT raise wholesale**; if any, take `.rt-btn`/`.btn-export` (controls) and leave `.mono` (dense data) |
| **③ MULTI** | **6** | `.cap-table` **13 scr**, `.panel-sub` **13** | table density is drawn tight in every artwork | 🔴 **HIGH** — `.cap-table` governs row height on 13 screens; raising it changes rows-per-screen | ⛔ **highest-regret group.** Defer unless a specific screen is illegible |
| **④ SINGLE** | **5** | 1 screen each (S08 ×3, S20, S17) | per-screen | 🟢 **LOW** — one screen re-approval each | ⭐ **safe to raise individually** if any is judged illegible |
| **⑤ NO REACH** | **28** | legacy routes or nothing | outside the 01–22 campaign | 🟢 **~NONE on campaign screens** | ⭐ **cheapest to raise, least value.** ⛔ Search width stated: token grep only; **8 of 28 checked** |

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

## 4 · VISUAL APPROVAL CHECKLIST
⛔ **Not "all okay?" — each row names the ONE area that changed and the check that would catch a
regression there.** ⭐ The **old artwork in `D:/Projects/trading-system/gui` remains binding**
except where Rama explicitly chose the written spec over it (Q3 on S03).
📌 **Every row also carries the standing checks** — colours/fonts/cosmetics unchanged · table
alignment · headings and drag placement · charts/donuts where present · live-update areas ·
screen-space utilisation · no page overflow · no clipping or overlap.

### 4a · CHANGED TODAY — ⭐ look here first
| Screen | Key changed area | Required visual check | Status |
|---|---|---|---|
| **S03** Strategies | **4 new main-table columns**: Trading Type (col 2) + SL Hit · TGT Hit · ROI % | Trading Type reads Intraday/Delivery/`—` and sits **immediately after Strategy** · **Scanner still absent** · ROI shows **`—` not `0%`** where null · row alignment unchanged (labels left, numerics right) · table still fits, no page scroll | ⏳ |
| **S09** P&L Analytics | **Equity-curve axis labels (new)** | ₹ scale down the left, session timeline underneath · labels **clear of the plot**, none clipped at the panel edge · curve shape unchanged · **compare against `09. PnL_Analytics.png`** | ⏳ |
| **S12** System Health | **Health-trend x-axis** (Disk tab) | click **Disk** — the chart's **last two time labels no longer overlap** · y-axis values still present · CPU/RAM/Response still read **NOT INSTRUMENTED** (correct, not a bug) | ⏳ |
| **S14** Trade Logs | **Bottom row-4 panels** (`.tlg-errs` / `.tlg-export`) | **no sideways page scroll at 1440** · the **rail stays beside** the main table · Recent-Errors and Export panels now sit in their **intended 1.6 : 0.8 proportion** (Export was previously squashed) · event table still scrolls **inside** its own region | ⏳ |
| **S17** Controls | **Control-History rail rows + KPI values** | no sideways page scroll at any width · **Control History** action text wraps rather than spilling out of the rail · **`Last Control Change`** value wraps inside its card ⚠️ *(it renders a **raw ISO timestamp** — see note)* | ⏳ |

⚠️ **S17 note, so it is judged rather than discovered:** `Last Control Change` displays the raw
`2026-08-19T17:05:00+05:30`. The overflow is fixed, but **the value is unformatted** — the real
remedy is to format it, which is a **template** change outside D2-C's authorised CSS-only shape.
⛔ Reported, ⛔ not fixed.

### 4b · Q2 CHROME STRIP ONLY — ⭐ one identical check, eleven screens
| Screen | Key changed area | Required visual check | Status |
|---|---|---|---|
| S02 · S10 · S11 · S13 · S15 · S16 · S18 · S19 · S20 · S21 · S22 | **Sidebar + top status strip only** (`base.html`) | **TRADING/ANALYTICS/OPERATIONS/INVESTIGATION** nav headings · **TRADER/MODE/KILL/PHASE** labels · the status **pills** · **"poll 5s"** · **"version 2.0.0"** — all now **13px**; check they still fit their strip, wrap nowhere, and have not pushed the page | ⏳ |
📌 ⭐ **The page bodies of these eleven did NOT change** — ⛔ a full re-audit is not required, only
the strip plus a page-overflow glance.

### 4c · FIRST APPROVAL — ⭐ never approved before
| Screen | Key changed area | Required visual check | Status |
|---|---|---|---|
| **S01** Login | ⛔ **NOTHING changed** — standalone `login.html`, own inline `<style>`, **never loads `style.css`** | Full first-approval pass against `01. Login-Screen.png`. ⛔ **Independent of Q2** · ⚠️ **never browser-tested** (pre-auth; the QA harness force-authenticates) | ⏳ |
| **S04** Signals | Q2 strip only | full first-approval pass vs `04. Signals.png` + strip | ⏳ |
| **S05** Orders | Q2 strip only | full first-approval pass vs `05. Orders.png` — ⭐ incl. the **two donuts** restored in the closure pass | ⏳ |
| **S06** Positions | Q2 strip only | full first-approval pass vs `06. Positions.png` | ⏳ |
| **S07** Trade Explorer | Q2 strip only | full first-approval pass vs `07. Trade_Explorer.png` — ⚠️ **RR Damage % is deliberately absent** (B-3) | ⏳ |
| **S08** Capital & Risk | Q2 strip only | full first-approval pass vs `08. Capital_Risk.png` — ⭐ incl. the **gauge + top-consumer bars** restored in the closure pass · ⚠️ **note D-1**: at 1440 `Opening Cash` / `Total Real Cash` can overrun their card by ~9px with six-figure values | ⏳ |

---

## 5 · WHAT IS **NOT** BEING DONE
⛔ No source change · ⛔ no S08 fix · ⛔ no D3 typography change · ⛔ no layout-assurance harness ·
⛔ no mass bare-`fr` refactor · ⛔ no visual redesign · ⛔ no push · ⛔ no deploy · ⛔ S14 not
reopened (`main.content`, breakpoint, `.tlg-row4`, rail, table widths, global overflow — all
untouched) · ⛔ no unrelated screen reopened because a different screen was fixed.
