# FINAL VISUAL-REVIEW PACKAGE — S01 … S22
**19-Aug-2026 · 22 full-height captures at 1920 wide · QA fixture data · ⛔ no production data, ⛔ no VM write**

⭐ **This is the package Rama asked to see: every screen, after all authorised work.**
⛔ **Nothing here is approved.** Approval is a decision, ⛔ not a measurement — the columns below
record what was MEASURED so the eye is free for what only an eye can judge.

## HOW EACH CAPTURE WAS MADE
Two passes per screen, ⛔ deliberately not one:
1. the **probe** route reads `documentElement.scrollHeight` and re-checks page overflow and
   sub-13px text **at capture time**;
2. the **normal** route is then captured at **1920 × that exact height**, so the PNG carries the
   **whole page** with no empty canvas below it — and ⛔ **without** the probe's own JSON block,
   which would otherwise appear in the image.

📌 **Heights are real, ⛔ not padded** — S05 runs to 3000px and S08 to 3094px because those pages
genuinely are that tall; S19 sits at the 1080 floor because its content ends above it.

## WHAT TO CHECK ON EVERY SCREEN
| # | check | # | check |
|---|---|---|---|
| 1 | same **colours** | 8 | **pie / donut charts** |
| 2 | same **fonts** | 9 | **graph charts** |
| 3 | same **headings** | 10 | **real-time data areas** populating |
| 4 | **table alignment** | 11 | **screen-space utilisation** |
| 5 | **strategy — ⛔ NOT scanner — as the operational entity** | 12 | **no page overflow** |
| 6 | **drag / move placement** | 13 | **no clipping · no unintended wrapping** |
| 7 | **populated-data appearance** | 14 | matches the artwork **unless the written TXT overrides it** |
⚠️ **Where the written TXT/spec explicitly overrides the artwork, the written spec governs**
(that is how Q3 was ruled on S03, and how B1 and B6 were ruled today). ⛔ Neither source may be
silently reinterpreted.

## THE MEASURED COLUMNS — what they mean
- **OVF** — page-level horizontal overflow at capture. ⭐ **`no` on all 22.**
- **<13px** — rendered elements carrying their own text below the spec's 13px floor.
  ⭐ **0 on 12 screens**; every remaining count is a **decorative glyph** (drag grip / sort arrow)
  under the **B2 exemption**, ⛔ except S01's two, which are recorded below.
- **h** — the captured full-page height in px.

---

## THE INDEX — screen · capture · old artwork · what changed · what to look at

| # | screen | capture | old artwork (`D:\Projects\trading-system\gui\`) | h | OVF | <13px | ⭐ WHAT CHANGED TODAY — look here first | ⚠️ deviation to judge |
|---|---|---|---|---|---|---|---|---|
| **S01** | Login | `S01_login.png` | `01. Login-Screen.png` + `.txt` | 1080 | no | **2** | ⛔ nothing — standalone `login.html`, ⛔ never loads `style.css` | ⚠️ `<label>` **12.48px**, `.foot` **11.52px**. ⛔ **Left as drawn**: `01. Login-Screen.txt` has a TYPOGRAPHY section but states **no px minimum**, so raising them would be preference, ⛔ not compliance. ⚠️ ⛔ never browser-tested before today (pre-auth) |
| **S02** | Dashboard | `S02_dashboard.png` | `02. Dashboard.png` + `.txt` | 1212 | no | 0 | **B1**: the alert banner text and the `1 critical` chip were **12.8 / 10.56px** → now **13px**. **B4 verified here**: the pipeline shows all 12 spec stages **in the spec's order**, each with Count + `Last:` time | ⚠️ **a 13th stage, `Manual Exit`**, sits between TGT Hit and Trade Closed. The spec calls its list *"Preferred Flow"* and a manual exit is a real terminal state ⇒ an **addition**, ⛔ not a contradiction — but it **is** a deviation, so judge it |
| **S03** | Strategies | `S03_strategies.png` | `03. Strategies.png` + `.txt` | 1125 | no | 0 | **B6**: the **Export XLSX button is live** (top-right of the table). **B1**: the five filter labels were 12px → **13px**. Q3's four columns stand: **Trading Type** 2nd, **SL Hit · TGT Hit · ROI %** | ⚠️ **Allocated shows ₹70,000 on every row** — that is the **global intraday bucket**, ⛔ not a per-strategy cap (**B5**, validated). ⛔ Remaining is honest per row but **not additive** across rows |
| **S04** | Signals | `S04_signals.png` | `04. Signals.png` + `.txt` | 1765 | no | 0 | Q2 chrome strip + **B1** `.btn-logout` | ⚠️ its Export button is the **still-disabled** shared macro — ⛔ B6 covered S03 only |
| **S05** | Orders | `S05_orders.png` | `05. Orders.png` + `.txt` | 3000 | no | 2 | Q2 strip + **B1** | glyphs only: `.st-arw` 9px, `.st-grip` 10px |
| **S06** | Positions | `S06_positions.png` | `06. Positions.png` + `.txt` | 1364 | no | 1 | Q2 strip + **B1** | glyph only: `.st-arw` 9px |
| **S07** | Trade Explorer | `S07_trade_explorer.png` | `07. Trade_Explorer.png` + `.txt` | 1498 | no | 1 | Q2 strip + **B1** | 🔴 **RR Damage % is ABSENT and stays absent — B3 is BLOCKED**, see below. glyph: `.st-arw` 9px |
| **S08** | Capital & Risk | `S08_capital_risk.png` | `08. Capital_Risk.png` + `.txt` | 3094 | no | 0 | **B1**: the events list and its timestamps were 12.8px, the severity chips 10.56px → **13px** | ⚠️ **monitored risk**: at 1440 the `Opening Cash` / `Total Real Cash` values can overrun their card by ~9px **at six-figure capital**. Page overflow is 0; today's real capital fits |
| **S09** | P&L Analytics | `S09_pnl_analytics.png` | `09. PnL_Analytics.png` + `.txt` | 1348 | no | 0 | the **equity-curve axis labels** (₹ scale + session timeline) — new today | — |
| **S10** | Slippage | `S10_slippage.png` | `10. Slippage_Analytics.png` + `.txt` | 1444 | no | 1 | Q2 strip + **B1** | glyph: `.slp-arrow` 9.28px. ⭐ its chart labels are **HTML at a real 13px**, ⛔ not SVG |
| **S11** | Execution | `S11_execution.png` | `11. Execution_Analytics.png` + `.txt` | 1887 | no | 2 | Q2 strip + **B1** | glyphs: `.exec-arrow` 9.28, `.exec-grip` 8.8 |
| **S12** | System Health | `S12_services.png` | `12. System_Health.png` + `.txt` | 1845 | no | 2 | the **health-trend x-axis** collision fix (Disk tab) | ⚠️ CPU / RAM / Response read **NOT INSTRUMENTED** — ⭐ correct, ⛔ not a bug. glyphs: `.sysh-arrow`, `.sysh-grip` |
| **S13** | Audit | `S13_audit.png` | `13. Audit.png` + `.txt` | 1514 | no | 1 | Q2 strip + **B1** | glyph: `.aud-grip` 8.8px |
| **S14** | Trade Logs | `S14_trade_logs.png` | `14. Trade_Logs.png` + `.txt` | 2066 | no | 1 | **the bottom row-4 panels** — the 1440 page overflow is gone and the declared **1.6 : 0.8** proportion is restored | glyph: `.tlg-grip` 8.8px |
| **S15** | System Logs | `S15_logs.png` | `15. System_Logs.png` + `.txt` | 1880 | no | 1 | Q2 strip + **B1** | glyph: `.slg-grip` 8.8px |
| **S16** | Configuration | `S16_config.png` | `16. Configuration.png` + `.txt` | 1408 | no | 0 | Q2 strip + **B1** | — |
| **S17** | Controls | `S17_controls.png` | `17. Controls.png` + `.txt` | 1956 | no | 0 | **Control-History rail rows + KPI values** — text now wraps instead of spilling | 🔴 **18 RAW ISO TIMESTAMPS RENDER HERE** — measured, ⛔ not estimated: **1** KPI value (`Last Control Change` = `2026-08-19T17:05:00+05:30`), **11** in a data table (`.dt-td`), **6** in the Control-History rail (`.ctl-htime`). ⭐ S13 and S16 render **0**, so this is **S17-specific**. The overflow is fixed; the **values are unformatted**. ⛔ **Formatting them is a template change outside B1–B6 and was NOT done** |
| **S18** | Live Activity | `S18_live_activity.png` | `18. Live_Activity.png` + `.txt` | 1637 | no | 1 | Q2 strip + **B1** | ⚠️ `18. Live_Activity.txt` has **no readability section at all** — the 13px floor reaches it only through the shared rules. glyph: `.lav-grip` |
| **S19** | Strategy Ranking | `S19_strategy_ranking.png` | `19. Strategy_Ranking.png` + `.txt` | 1080 | no | 0 | Q2 strip + **B1** filter labels | ⭐ donut centre text is SVG: declared 4.72 → **renders 13.04px** |
| **S20** | Strategy Health | `S20_strategy_health.png` | `20. Strategy_Health.png` + `.txt` | 1472 | no | 0 | Q2 strip + **B1** filter labels | ⭐ donut centre text declared 4.41 → **renders 13.02px** |
| **S21** | Scanner Attribution | `S21_scanner_attribution.png` | `21. Scanner_Attribution.png` + `.txt` | 1531 | no | 0 | Q2 strip + **B1** | — |
| **S22** | Holdings | `S22_holdings.png` | `22. Holdings.png` + `.txt` | 1457 | no | 0 | Q2 strip + **B1** filter labels | ⭐ donut text declared 7.5 / 4.9 → **renders 20.00 / 13.07px** |

---

## THE SIX DECISIONS — evidence · decision · code · test
| | item | evidence | decision | code change | test | status |
|---|---|---|---|---|---|---|
| **B1** | D3 typography | **"Minimum: 13px"** in **20 of 22** TXT specs. Browser sweep of all 22 at **1440 AND 1920**: the real exposure was **8 findings in 5 declarations**, ⛔ not 63 | **RAISE the five** | ✅ `style.css` ×5 | `test_b1_typography_floor.py` (6) + the flipped `.flt-k` guard | ✅ **DONE** |
| **B2** | glyph exemption | the sub-floor remainder is **grips 8.8px** and **sort arrows 9.0–9.28px** — single symbols, ⛔ no readable text | **FORMALISED, bounded** | ⛔ none | 3 tests, incl. one that fails if readable text hides in the family | ✅ **DONE** |
| **B3** | S07 RR Damage % | the spec's **label** matches `rr_damage_pct`; the spec's **arithmetic** (1:2→1:1.6=20%) matches `rr_degradation_pct`. **Both exist**, are **different numbers**, and the project forbids presenting either as the other | 🔴 **BLOCKED — ⛔ not fabricated** | ⛔ none | 3 tests assert the absence **deliberately** | 🔴 **RAMA** |
| **B4** | rejection taxonomy | `02. Dashboard.txt` LIVE PIPELINE names all 12 stages; `STAGE_DEFS` already carries them **in that order**, each rendered with **Count + Last Event Time** | ✅ **VALIDATED** | ⛔ none | 5 tests, incl. one that fails on any **unannounced** stage | ✅ **DONE** |
| **B5** | S03 capital semantics | `used + remaining` resolves to `intraday_bucket_pct × opening` — the **global bucket**, exactly as `allocation_basis` declares | ✅ **VALIDATED** | ⛔ none | the guard **stays**, +2 assertions | ✅ **DONE** |
| **B6** | S03 export | `03. Strategies.txt` — **EXPORT: Download XLSX**. The `_xlsx` writer + `/api/export/*` already ship on **17 screens** | **IMPLEMENT, smallest** | ✅ route + `export_rows` + button | 6 tests, incl. header-order and the None-not-zero guard | ✅ **DONE** |

### 🔑 B3 — THE EXACT MISSING DEFINITION (the only thing blocking it)
> **Which quantity must Screen 07's *"RR Damage %"* row show, given that the label is already
> bound on Screen 10 to a different number?**

| the project's two quantities | base | shown today as |
|---|---|---|
| `rr_damage_pct` = (entry_adverse + sl_adverse − tgt_favourable) ÷ `planned_sl_distance` × 100 | the planned **risk budget** | S10 — **"RR Damage %"** |
| `rr_degradation_pct` = 100 × (`planned_rr` − `actual_rr`) ÷ `planned_rr` | the planned **R:R** | S10 — **"RR Degradation % (avg)"** |
⭐ The project's own source calls the second *"the reference design's 1:2 → 1:1.6 = 20% arithmetic"* —
so **S07's example IS `rr_degradation_pct`**, while **S07's label IS `rr_damage_pct`'s name.**

| option | what it costs |
|---|---|
| ① spec label + spec arithmetic | ⛔ *"RR Damage %"* would mean **two different things** on S07 and S10 |
| ② spec arithmetic + honest label (*"RR Degradation %"*) | ⭐ keeps one-label-one-meaning; ⚠️ deviates from the spec's **wording** |
| ③ existing `rr_damage_pct` under the spec's label | ⭐ agrees with S10; ⛔ the three rows **won't reconcile** — 20% would not follow from 1:2 and 1:1.6 |
⚠️ **AND A SEPARATE DATA GAP:** S07 reads `trade_explorer_rows`, which does **not** join
`trade_slippage_log`. Even once the label is ruled, S07 needs that join — **data plumbing on a
trading screen**, ⛔ not a UI tweak.

---

## STATUS OF ALL 22 — ⛔ the five permitted states only
| state | n | screens |
|---|---|---|
| **VISUAL APPROVAL REQUIRED** | **22** | S01 … S22 — ⛔ all of them |
| **TECHNICALLY COMPLETE** | **22** | ⭐ implementation, tests and browser QA done |
| **BLOCKED BY SPEC/DATA DECISION** | **1** | **S07** — the RR ANALYSIS panel only; the rest of S07 is complete |
| **DEFERRED RISK** | **1 screen, 3 risks** | **S08** (KPI overflow) · 104 latent bare-`fr` · absent `main.content` guard |
| **FINALLY APPROVED** | **0** | ⛔ **none.** Approval is Rama's |

## MONITORED RISKS — ⛔ visible, ⛔ NOT authorised work
1. **S08 KPI internal overflow** — fixture-value `₹100000.00` at 1440 overruns a 150px box by ~9px. **Page overflow 0.** Fits at 1920. Today's real capital `₹10,622.70` fits. ⛔ Not confirmed live.
2. **104 latent bare-`fr` declarations** — 105 found, **1 was symptomatic** (`.tlg-row4`, fixed). ⛔ **No mass refactor.** The durable output is the detector: `display:grid` + `overflow-x:visible` + `scrollWidth > clientWidth`, asserted zero.
3. **Absent `main.content` guard** on S08/S13/S14/S15 — **narrowed by measurement**: `main.content` reads 1236px on all six screens tested ⇒ the `:has()` rule is a **safety net**, ⛔ not the sizing mechanism.

## ⛔ WHAT WAS NOT DONE
⛔ No S08 fix · ⛔ no bare-`fr` refactor · ⛔ no `main.content` rule added · ⛔ no glyph exemption
broadened · ⛔ no RR Damage field invented · ⛔ no rejection-status tuple altered · ⛔ no capital
derivation changed · ⛔ no per-strategy allocation cap · ⛔ no global export flag flipped ·
⛔ no screen redesigned · ⛔ **no push · no deploy · no VM change · no production data touched** ·
⛔ the known environmental `test_c_venv_has_no_kiteconnect` failure left **exactly as it was** —
⛔ the environment was **not** modified to make it green.
