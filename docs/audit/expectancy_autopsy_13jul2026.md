# Q2.5 — THE EXPECTANCY AUTOPSY (entries vs exits)

**Date:** 13-Jul-2026 (off-market) · **Author:** VS Code Claude · **Read-only**, DB copy (`/tmp/q3/copy.db`).
**Sample:** 134 closed trades (2026-06-15 → 07-13), **100% `LIMIT_TRIPLE`** (no CO trail; A7 N/A).

## THE VERDICT (blunt)

**The negative book is caused by a WEAK ENTRY EDGE, not by bad exits.** The exit structure is sound;
the win rate is a hair too low for the R:R, so the GROSS book is flat, and costs tip it negative.
**M-S4 (better entry selection) is the right fix — the plan holds — *provided it actually lifts the
win/target-reach rate*, because today there is no gross edge to protect.**

## The evidence

**Exits are SOUND — the "cut winners short / let losers run" smoking gun is REFUTED:**
- **Winners hit their TARGET 79% of the time** (TGT_HIT 41/52); only 21% were squared off. Losers hit
  the full SL 76% (62/82). Exactly what the design intends.
- **R structure is healthy:** winner gross-R avg **+1.38** (median +1.50, = the 1.5 R:R target); loser
  gross-R avg **−0.93** (median −1.02, ≈ the −1R stop). Stops and targets both work.
- **MFE (decisive test):** winners captured **+1.35R of a +1.63R** favourable excursion — only **0.28R
  left on the table**. We are *not* systematically cutting winners short. Losers reached only +0.49R
  favourable on average before reversing; just **13% of losers were ever ≥+1R green** before losing.

**The problem is the WIN RATE / entry quality:**
- **Only 32% of trades ever reach the +1.5R target zone** (29/91 with MFE data). That is an entry ceiling.
- Gross **realized R:R = 1.56 vs breakeven 1.58** — essentially *at* breakeven. Gross win rate 38.8%; a
  1.5-R:R system needs >40%. It lands a hair under → **gross P&L is flat (−Rs5.62)**.
- **Costs then tip it negative:** costs Rs53.51 = **952% of |gross|**; net = **−Rs59.13**. Cost/trade
  Rs0.40 is ~6% of avg |net| Rs6.99 — heavy *because positions are tiny* (concentration-capped, Q3).

**Long vs short (net):**
- **LONG carries the loss:** n=124, win 37.1%, realized R:R 1.45 < breakeven 1.70, **expectancy −0.132R**.
- **SHORT is positive** but n=10 (noise): win 60%, expectancy +0.165R. The negative expectancy lives in
  the long book — consistent with Q3's >Rs990 long-book distortion.

> **⚠️ NOTE 19-Jul-2026 (record-correction batch).** "Q3's >Rs990 long-book distortion" is a **selection/count** effect of the 91%-long book, **not** the concentration cap treating longs more harshly — by rejection *rate* the cap hits SHORTs marginally harder (LONG 10.00% vs SHORT 10.72%; census `signal_mortality_census_19jul2026.md` claim 3). The empirical observation here (LONG n=124 carries the net loss) is unaffected; only the *attribution* to a long-specific cap bias is corrected.

**15:17 squareoff:** 29 MANUAL exits net **−Rs22.60** (18 of 29 in loss); the 15:15–15:20 cohort (17) is
−Rs24.29. Modest, skews negative (closing trades that never resolved), but not the primary driver.

## What this means for the roadmap

1. **M-S4 stays the priority — the current plan is correct.** Rama's worry (that exits might be the
   problem, so M-S4 wouldn't help) is *not* borne out: exits are sound. Better entry selection is exactly
   the lever that can move the win rate.
2. **But the bar is explicit:** the gross book has **no edge today**. M-S4 must lift the target-reach /
   win rate from ~39% to comfortably above ~42–45% to clear costs and create a real gross edge. If a
   post-fix re-soak does NOT show a measurable win-rate/expectancy improvement, the scorer fix alone is
   insufficient and the thesis needs rethinking. **The GATE-1 impact artifact should report the projected
   win-rate/expectancy lift, not just the score-distribution shift.**
3. **Costs are a real second-order drag, amplified by tiny size** (Q3). This does *not* argue for scaling
   size now (the book is negative — Q3), but it does mean the edge fix and the eventual size decision
   compound: a real edge at a larger size dilutes the cost %.
4. **A minor exit tweak is available but not the fix:** ~13% of losers were once ≥+1R green. A
   breakeven-stop-after-+1R rule could rescue a few, but it is second-order — do NOT reprioritise the
   exit engine over M-S4 on this.

**Caveats:** 134 closed trades, ~2 months, one market regime, past fills ≠ future fills. Given those, the
answer still stands: **entries/edge, not exits.**

---
*Read-only. Nothing changed. Script: `scratchpad/q25_autopsy.py` → `/tmp/q3/autopsy.py`.*
