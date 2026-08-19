# GUI CLOSURE / RE-APPROVAL PACK — 19-Aug-2026

**Evidence only. ⛔ No code was written for this pass.** Worktree `gui09`, HEAD `8101cfd`,
**PUSHED = NO · DEPLOYED = NO**. Production DB never opened; VM read-only throughout.
Captures: `docs/audit/approval_19aug/` (9 PNGs, 1.7 MB).

---

## A · Q2 — SHARED CHROME AT 13px · `1a647f4498e04249cb7499267c1cf038e72d4003`

**The five authorised rules, before → after (rem base 16px):**

| rule | was | now | what it renders |
|---|---|---|---|
| `.nv-group` | `.62rem` = **9.92px** | **13px** | TRADING · ANALYTICS · OPERATIONS · INVESTIGATION |
| `.sb-label` | `.64rem` = **10.24px** | **13px** | TRADER · MODE · KILL · PHASE |
| `.sb-poll` | `.62rem` = **9.92px** | **13px** | "poll 5s" |
| `.sb-pill` | `.78rem` = **12.48px** | **13px** | DOWN · PAPER · INACTIVE · ENTRY_WINDOW |
| `.sb-ver` | **11px** | **13px** | "version 2.0.0" (span is `.nv-label`, inherits) |

**VERIFIED:** swept all **21 authenticated screens** at **1920×1080** and **1440×900** — none of
the five appears in any screen's sub-13px set. Visible in every capture (sidebar + top bar).

⛔ **SCOPE HELD AT FIVE — the remaining 63 are NOT implemented.** A full sweep found **68**
non-page-scoped rules under 13px. ⛔ The 13px decision is **NOT** read as a blanket
"all text everywhere must be 13px". The other 63 stay a **separate decision item** —
raising them now would start a second uncontrolled 22-screen approval cycle.
⚠️ **`.btn-logout` (12.48px) is the most visible survivor** — readable text, present on
**every** screen's sub-13px list. It is listed, ⛔ not changed.

### 🔴 SCREENS RE-OPENED BY Q2
`.nv-group` · `.sb-label` · `.sb-poll` · `.sb-pill` · `.sb-ver` live in **`base.html`**, which
every page extends ⇒ **ALL 22 SCREENS ARE RE-OPENED.** ⛔ Not hidden, ⛔ not minimised — it is
the accepted cost of the ruling.

---

## B · Q3 — SCREEN 03 · `bb4af7a17ff906f8c02b4c87ace7f787d3535ea6`

**Captures:** `S03_strategies_1920x1080.png` · `S03_strategies_1440x900.png`

**RENDERED COLUMN ORDER, read off the capture:**
`STRATEGY · **TRADING TYPE** · SIGNALS · ORDERS · TRADES · SUCCESS % · WIN % · **SL HIT** ·
**TGT HIT** · P&L (₹) · **ROI %** · ALLOCATED (₹) · USED (₹) · REMAINING (₹) · USAGE % ·
LAST SIGNAL · LAST TRADE · STATUS`

| field | where it renders | value source | verified in capture |
|---|---|---|---|
| **Trading Type** | **main table, column 2**, immediately after Strategy | `basic.trade_type` ← strategy YAML `intent` | ✅ Intraday / Delivery / `—` |
| **SL Hit** | main table, after Win % | `sl_tgt_hits.sl_hits` | ✅ integers |
| **TGT Hit** | main table, after SL Hit | `sl_tgt_hits.tgt_hits` | ✅ integers |
| **ROI %** | main table, after P&L | `performance.roi_pct` (**base = attribution R4, net ÷ Σ margin_reserved today**) | ✅ `0.5%`, `0%`, and `—` where null |

✅ **Preserved and confirmed in the capture:** Scanner absent from the operational table ·
existing column order otherwise unchanged · table alignment (labels left, numerics right) ·
dark cosmetics · no page-level horizontal overflow at either viewport.
⭐ **ROI renders `—`, ⛔ not `0%`, where no margin was reserved** — the two states stay distinct.

### 🔴 B-2 · PLACEMENT INTERPRETATION — REQUIRES RAMA'S APPROVAL, ⛔ NOT DECLARED COMPLIANT
`03. Strategies.txt` places the fields as follows:
- **`Trading type: (INTRADAY/DELIVERY) ▲▼` → MAIN TABLE** ✅ implemented exactly there.
- **`SL Hit Count` · `TGT Hit Count` · `ROI %` → `DETAIL PAGE → Additional`** ⛔ **NOT the main
  table.**

**Screen 03 has no detail-page surface.** "Strategy Details" is a quick action linking **out** to
`/strategy-health` (Screen 20). The three fields were therefore placed in the **only
per-strategy surface that exists**.

🏷️ **STATUS: `PLACEMENT INTERPRETATION — AWAITING RAMA`. ⛔ This is NOT literal spec compliance
and is ⛔ not being reported as such.** ⛔ No speculative detail page was created.
**Rama's options:** accept the main-table placement · or authorise a detail surface (a separate,
larger piece of work) · or move them to Screen 20.

---

## C · Q4 — SEEDING RECORD · `8101cfd6705d268c7b3030dfe674eda5d83caa93`
✅ **`ops_dashboard/docs/SEEDING.md` exists** — 75 lines, record-only.
⛔ **No harness promoted** into `ops_dashboard/qa/`. ⛔ **No CI/browser gating added.**
Option **A** remains planned future work, ⛔ not started.

---

## D · PAGE OVERFLOW — PRE-EXISTING, ⛔ NOT FIXED

| screen | viewport | scrollWidth / clientWidth | overflow |
|---|---|---|---|
| **S14 trade_logs** | 1440×900 | **1760 / 1416** | **344 px** |
| **S17 controls** | 1920×1080 | **1900 / 1896** | **4 px** |
| **S17 controls** | 1440×900 | **1499 / 1416** | **83 px** |

**ATTRIBUTION EVIDENCE — before/after, ⛔ not inferred:**

| | with Q2+Q3 applied | with Q2+Q3 **stashed** (`git stash push`) |
|---|---|---|
| S14 @1440 | `1760 / 1416` | **`1760 / 1416` — identical** |
| S17 @1920 | `1900 / 1896` | **`1900 / 1896` — identical** |
| S17 @1440 | `1499 / 1416` | **`1499 / 1416` — identical** |
| S03 @both | **no overflow** | no overflow |

⇒ 🏷️ **PRE-EXISTING. ⛔ Neither Q2 nor Q3 causes any of them.** Restore proved byte-exact
(`style.css` md5 `bc167f58…`, `strategies.html` md5 `a63e8b71…`).
⛔ **NOT FIXED — recorded as closure blockers awaiting an explicit decision.**

📌 **ON THE ORIGINAL "0 OVERFLOW" CLAIM — STATED PRECISELY, ⛔ NOT OVERSTATED:**
> *"Not reproducible under the current seeded fixture; the original harness/seeding cannot
> currently be reconstructed."*

⛔ It is **not** called false and **not** called vacuous — no evidence establishes either. The
original harness has 0 references in the repo and its per-screen seeding was never recorded, so
the two runs cannot be compared. ⭐ This is exactly the gap `SEEDING.md` now closes going forward.

---

## E · APPROVAL / RE-APPROVAL MATRIX

| Screen | Current state | Action required | Reason |
|---|---|---|---|
| **S01 Login** | built, never approved | **FIRST APPROVAL** + re-verify | Q2 chrome |
| **S02 Dashboard** | approved 18-Aug | **RE-APPROVAL** | Q2 chrome |
| **S03 Strategies** | ⚠️ **CHANGED TODAY** (Q3) | **RE-APPROVAL** + rule the **placement interpretation** (B-2) | Q3 + Q2 chrome |
| **S04 Signals** | built, never approved | **FIRST APPROVAL** + re-verify | Q2 chrome |
| **S05 Orders** | built, never approved | **FIRST APPROVAL** + re-verify | Q2 chrome |
| **S06 Positions** | built, never approved | **FIRST APPROVAL** + re-verify | Q2 chrome |
| **S07 Trade Explorer** | built, never approved | **FIRST APPROVAL** + re-verify; **RR Damage % still absent** (own decision) | Q2 chrome |
| **S08 Capital & Risk** | built, never approved | **FIRST APPROVAL** + re-verify | Q2 chrome |
| **S09 P&L Analytics** | ⚠️ **CHANGED TODAY** (axis labels) | **RE-APPROVAL** | axis labels + Q2 chrome |
| **S10 Slippage** | approved 15-16 Aug | **RE-APPROVAL** | typography pass + Q2 chrome |
| **S11 Execution** | approved 15-16 Aug | **RE-APPROVAL** | typography pass + Q2 chrome |
| **S12 System Health** | ⚠️ **CHANGED TWICE** (typography, then collision fix) | **RE-APPROVAL** | `e2022d5` + Q2 chrome |
| **S13 Audit** | built | **APPROVAL** + re-verify | Q2 chrome |
| **S14 Trade Logs** | awaiting approval | **APPROVAL BLOCKED** — 🔴 **344px overflow @1440** | pre-existing overflow + Q2 chrome |
| **S15 System Logs** | built | **APPROVAL** + re-verify | Q2 chrome |
| **S16 Configuration** | awaiting approval | **APPROVAL** + re-verify | Q2 chrome |
| **S17 Controls** | built | **APPROVAL BLOCKED** — 🔴 **overflow @1920 (4px) AND @1440 (83px)** | pre-existing overflow + Q2 chrome |
| **S18 Live Activity** | built | **APPROVAL** + re-verify | Q2 chrome |
| **S19 Strategy Ranking** | built | **APPROVAL** + re-verify | Q2 chrome |
| **S20 Strategy Health** | built | **APPROVAL** + re-verify | Q2 chrome |
| **S21 Scanner Attribution** | built | **APPROVAL** + re-verify | Q2 chrome |
| **S22 Holdings** | built | **APPROVAL** + re-verify | Q2 chrome |

⚠️ **EVERY ROW carries "Q2 chrome"** — that is the single decision's full blast radius, stated
once per screen rather than summarised away.

### S09 / S12 FINAL SPOT-CHECK AFTER THE SHARED CSS CHANGE — ⛔ NO REGRESSION
| | 1920×1080 | 1440×900 |
|---|---|---|
| **S09** overlay labels | 9 @ **13.00px**, 0 escaping, no overflow | 9 @ **13.00px**, 0 escaping, no overflow |
| **S12** axis labels | 12 @ **13.00px**, min gap 37.1px, no overflow | 12 @ **13.00px**, min gap 37.1px, no overflow |

⛔ **No code was changed** — no regression was found.

---

## DECISIONS REQUIRED
1. 🔴 **S03 placement interpretation (B-2)** — accept main-table placement, authorise a detail
   surface, or relocate to S20.
2. 🔴 **S14 @1440 (344px) and S17 @1920/@1440 overflow** — authorise a fix, or accept and record.
3. 🔴 **The other 63 sub-13px shared rules** — raise, exempt, or leave open.
4. **Visual approvals** per the matrix above — 22 screens, all re-opened by Q2.
5. Still parked, ⛔ untouched: **S07 RR Damage %** · **Q2 rejection taxonomy** · **S03 D1/D2** ·
   **S03 F1/Q3 export** · **Gate E** · **layout-assurance Option A**.
