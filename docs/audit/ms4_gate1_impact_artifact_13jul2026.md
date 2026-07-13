# M-S4 GATE-1 IMPACT ARTIFACT (binding sign-off doc)

**Date:** 13-Jul-2026 · **Status:** §1–§2 DONE · **§3 backfill PENDING (the pivotal proof)** · **verdict PENDING.**
This is the doc Rama + ChatGPT sign before the M-S4 input-fix + re-fitted thresholds/tiers flip (GATE 1).
Criteria per the 13-Jul GATE-1 addendum (Web Claude + ChatGPT). **Nothing flips until §3 answers §4.**

## §1 — What the edge actually is (stated precisely; do NOT overclaim)

The Q2.8 harness (self-check passed) gave, on 134 closed trades:
- **P0 GROSS expectancy ≈ −0.007R** — i.e. **no measurable edge either way** at this sample size (not "flat by proof" — *undetectable* at n=134).
- **P0 NET expectancy ≈ −0.100R.** Decomposition: costs ≈ **0.093R/trade**, large relative to the ~0 gross.

**Precise claim:** gross expectancy is approximately flat (no measurable edge), and costs are large relative to
that flat gross, so they dominate the observed net loss. This is a **decomposition, not a causal verdict** — the
real conclusion comes from the measured post-M-S4 numbers (§3), not from this arithmetic.

## §2 — The DERIVED breakeven bar (empirical, corrects the ~47% estimate) — DONE

Derived from real data (n beside each input):

| input | value | n |
|---|---|---|
| actual NET win rate | **38.8%** | 134 |
| avg NET winner R | +1.308R | 52 |
| avg NET loser R | −1.009R | 82 |
| avg GROSS winner / loser R | +1.380 / −0.935R | 52 / 82 |
| avg realised cost R | 0.094R | 105 (+29 recorded 0) |
| SL_pct | median 1.00%, range [0.75%, 2.64%] | 134 |

**Breakeven win rate = |avgNetLoss| / (avgNetWin + |avgNetLoss|) = 1.009 / 2.317 = 43.5%.**
Actual **38.8%** → the book is **−4.7pp below breakeven**. **M-S4 must lift the net win rate past ~43.5%
(a ≈+4.7pp gain) to flip the book.** *(Web Claude's ~47% came from an idealised 1.5/1.0 model; the real data
gives ~44%. Record corrected.)*

**Structural cost note:** `cost_R = cost_rate / SL_pct = 0.105% / 1.00% ≈ 0.105R`. It is set by the STOP
DISTANCE, **not** by position size — so it does **not** improve when you size up (costs and R scale together).
This **independently supports the GATE-2 "do not scale" recommendation.** The only structural levers on cost_R
are a wider stop or fewer/better trades.

## §3 — The GATE-1 criterion (PENDING the backfill — the pivotal proof)

The artifact must NOT stop at "the score distribution shifted" (that says nothing about money). Method:
1. Recompute every historical signal's score with the REAL inputs (avg_volume_20d/atr14/rsi14 from the
   `core.daily_stats` no-lookahead core + a daily-candle backfill; sector from `risk_engine._resolve_sector`).
2. Apply the re-fitted `min_pass` → the set M-S4 WOULD have admitted.
3. **INTERSECT with the trades we ACTUALLY took** (whose outcomes we know).
4. Report the admitted set AND the rejected set, side by side.

**Required metrics — report for admitted AND rejected sets, with `n` beside EVERY number:**
realised win rate (vs 38.8%) · realised gross expectancy R (vs −0.007) · realised net expectancy R (vs −0.100) ·
target-hit rate (vs 36% reaching +1.5R) · avg winner R · avg loser R · LONG vs SHORT split · a confidence
interval (or n + a plain small-sample warning) on the win rate · **does the admitted set clear the §2
breakeven bar (43.5%)?**

**The inverse is equally informative:** of the trades M-S4 would REJECT — what was their ACTUAL P&L? Rejecting
our LOSERS is the edge; rejecting our WINNERS means M-S4 makes it WORSE and we must know now.

**Mandatory caveats (state, don't bury):** this measures M-S4's RANKING on trades we ALREADY took — it CANNOT
measure trades M-S4 would have admitted but we never took (no outcome exists). It is a PARTIAL, directional
estimate, not a backtest. Small sample (~134, ~2 months, one regime) — **small samples must not drive the
deployment decision;** n beside every number.

## §3 — RESULTS (backfill complete; seed=20260713; 90-day daily fetch; 122/122 symbols; 134/134 trades with full daily stats)

M-S4 shifts scores from **[55,64] → [69,99]** (as predicted). Then, on the REALISED outcomes:

**PRIMARY (all 134, restricted-range: every taken trade passed the old bar):**
- **T1 rank-correlation (Spearman rho vs realised R):** NEW = **−0.077** · OLD (null) = +0.024. (vs win 0/1: NEW −0.067 / OLD +0.058.) Neither ranks; the fixed score is marginally *negative*.
- **T2 quintile gradient (the headline) — NO monotonic gradient:** NEW win rates by ascending score = 42/44/41/**26**/41% (top quintile 41% = bottom 42%); OLD = 31/41/44/37/41%. Neither score separates winners from losers.
- **T3 permutation null (2000 shuffles):** rho_new **INSIDE** the null band [−0.163,+0.172], two-sided p ≈ 0.36 → **no detectable signal.**
- **T4 (demoted):** top-half by NEW score won **34.3%** vs bottom-half **43.3%** (±~11pp) — the *wrong* direction, within noise.

**EXPLORATORY (T5 near-miss — the funnel-opening question, seed=20260713, 400 of 4,298 sampled, 1% SL/+1.5R TGT approx, no cost):**
- The 55–59 rejects M-S4 would newly admit: would-be win **38.5%** (±4.8pp), gross exp **−0.104R**, target-hit 28.5% — **SIMILAR to (slightly worse than) the trades we took** (38.8% / −0.007R / 36%).

**Reading:** M-S4 does **not** rank the taken trades better (primary evidence converges on *no signal*), and the trades it would newly admit are **no better**. The deeper finding: **these 10 screening factors — even fully wired — have no detectable predictive power over this trade population** (old *and* fixed scores both fail to separate ~38%-win trades). Consistent with Q2.5–2.8 (no gross edge; the edge, if any, does not live in these factors).

**Caveats (honoured, not buried):** n=134 taken (restricted range attenuates correlation) + 400 near-miss; one regime, ~2 months; T1–T4 measure ranking on trades we ALREADY took (cannot see admitted-but-never-taken — T5 approximates that, exploratorily); the −0.077 rho is *within* noise → the claim is "**no detectable improvement**," NOT "M-S4 is harmful." Partial, directional estimate — not a backtest.

## §4 — VERDICT: **OUTCOME C** (does not improve; do NOT flip)

Per the pre-registered framework, this is **Outcome C: M-S4 does NOT improve ranking/selection on this data** (and marginally inverts, within noise). **DO NOT FLIP M-S4 as a scoring/enforce/selection change** on this evidence — there is no measured benefit to gate on, and the near-misses it would admit are no better.

- The **engineering fix is still worth completing** (a scorer with 4 dead inputs is objectively broken; wire them + re-fit so the *code* is correct and future data can be measured). But that is an engineering correctness fix, **not** a justification to change live selection.
- **Re-measure on FRESH forward shadow data** before ever reconsidering an enforce — this backfill is an estimate; a live re-measure on the fixed scorer is the proof, and it may differ (different regime, unrestricted range).
- **The bigger, harder finding to surface to Rama:** the screening-score approach (these 10 factors) shows no predictive power here. If the system is to develop an edge, it likely needs a *different* signal source (the V3 S&R/regime/playbook chain, or better entry logic) — not a re-weighting of these factors. This is exactly the kind of thing far cheaper to learn from a backfill than from live capital.

### The three predefined outcomes (for the record)

- **A. Admitted set CLEARS the 43.5% bar** → M-S4 plausibly flips the book → proceed to flip (off-market,
  supervised session).
- **B. Improves but does NOT clear the bar** → a real improvement, not sufficient alone → report plainly; Rama
  faces a genuine strategic question (do not paper over it).
- **C. Does NOT improve, or REJECTS OUR WINNERS** → the scoring thesis is WRONG → **DO NOT FLIP**, say so
  straight. A hard finding, and far cheaper learned from a backfill than from live capital.

**Report whichever outcome the data gives. Do not soften C to protect the plan.**

## §5 — After the flip (if it happens)
Repeat this entire analysis on FRESH shadow/live data before any production enforce (the backfill is an
estimate; the live re-measure is the proof). Re-derive the exit / MAE / MFE work post-M-S4 — better entries
change every distribution.

## Build status (this artifact's prerequisites)
- ✅ `core.candle_math.rsi`, schema v44 `daily_symbol_stats` (+drill), state_store helpers, `core.daily_stats`
  (no-lookahead core) — all tested, committed on `m-s4-fix-13jul`.
- ⏳ pre-market cache job (I/O glue) · wire `_build_market_data` (default-OFF) · **the §3 backfill + recompute +
  re-fit** · fill §3 + the §4 verdict.

---
*§2 script inline in this session; §3 backfill script to follow. Read-only until GATE 1.*
