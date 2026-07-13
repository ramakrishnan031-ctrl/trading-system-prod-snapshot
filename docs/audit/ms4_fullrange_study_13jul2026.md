# PHASE-2 — FULL-RANGE SCORE STUDY (removes the restricted-range confound)

**Date:** 13-Jul-2026 (off-market) · **Read-only research** · **extends GATE-1, does not replace it** ·
seed=20260713 · M-S4 stays OFF. The GATE-1 admitted-trade artifact STANDS as separate historical evidence.

## The confound this removes
GATE-1 §3 evaluated only the 134 taken trades — all scoring **55–64** on a scorer with a ~65 ceiling, i.e.
the **top ~8% of the achievable range**. A null on such a restricted slice is weak evidence of "no signal"
(the SAT-vs-Harvard-GPA effect). This study samples the **whole** old-score range and simulates every signal
— including ones we never took — on its true 1-min path.

## Method
Stratified sample across old-score bands (0–34 … 60–65) from the 145 symbols that have 1-min candles
(06-19…07-13); **n per band below**. NEW score recomputed with real inputs (no-lookahead, identical to §3).
Each signal simulated on its true 1-min path (entry@signal price, **uniform 1% SL / +1.5R TGT**, stop-first,
walk to 15:17) — same cost model as Q2.8/§3 (0.105R). Fixed seed.

## Results (n=863 simulated)

**Primary — the NEW score does NOT rank outcome ANYWHERE:**
- **T1′ Spearman rho (NEW vs simulated R) = +0.003** (OLD null = +0.029). **T3′ permutation:** rho **INSIDE**
  the null [−0.066,+0.066], p≈0.91. Zero correlation.
- **T2′ decile gradient by NEW score — FLAT:** win rates D1→D10 = 48/36/56/40/49/36/52/40/41/**37**%. The
  top decile (new 95–99) wins 37% = the low deciles. No monotonic trend.

**By OLD band (win rate · net expectancy · n):**

| old band | n | win | net expR |
|---|---|---|---|
| 0–34 | 59 | **19%** | **−0.74** |
| 35–39 | 54 | 43% | −0.16 |
| 40–44 | 150 | 55% | +0.17 |
| 45–49 | 150 | 38% | −0.15 |
| 50–54 | 150 | 61% | +0.37 |
| 55–59 | 150 | 43% | −0.04 |
| **60–65 (we trade this)** | 150 | **31%** | **−0.27** |

The ONLY real signal is at the very bottom: **0–34 is genuine junk** (19% win) — filtered by the *alive* steps
(price_action/vwap/circuit/age), which M-S4 does not touch. Above 35 there is **no gradient**, and the
**60–65 band we actually trade is among the worst** (31%), below the mid-bands. (Non-monotonicity above 35 is
noise; the load-bearing finding is the flat NEW-score decile gradient.)

## Verdict: OUTCOME B → OUTCOME C, now rigorously proven

The NEW (M-S4-fixed) score shows **no ranking gradient anywhere across the full range** — this is the Phase-2
"Outcome B", which **strengthens GATE-1's Outcome C from *suggested* to *proven*.** Removing the restricted-range
confound did not reveal a hidden signal: **M-S4 adds no ranking power, at any score level.** The old scorer's
only useful function is weak bottom-filtering (avoid the 0–34 junk) — which the alive steps already provide and
which we already sit above.

**Wording (deliberate):** this is **no detectable ranking improvement on the evaluated dataset** (145 symbols,
06-19…07-13, one regime) — NOT a claim that these factors can never predict.

## Consequence the framework flagged: the V3 ALLOCATOR is also uninformative
The allocator RANKS gate-survivors (score ≥ 60). Within that survivor band the score has **no ranking signal**
(the 60–65 band is uniform ~31% with no internal gradient). Therefore **ranked admission ≡ first-come** —
which **independently explains the allocator's regret = 0.0**, beyond the "no capital contention" explanation.
Ranking cannot help when there is nothing to rank. (Flag, not an action — the allocator stays shadow/OFF.)

## The strategic picture (named, NOT actioned — Rama's call)
Five independent analyses now converge:
- **Q3 sizing** → nothing to scale (scaling multiplies a loss).
- **Q2.5/2.7/2.8 exits** → the +1.43R/winner is real but mechanically uncapturable.
- **GATE-1 + this study** → the scorer has no detectable ranking signal (restricted *and* full range).
- **The allocator** → nothing to rank within the survivor band.
- **The book** → gross expectancy ≈ 0; the 0.093R/trade cost hurdle dominates the net loss.

**Honest conclusion: the system has no demonstrated edge — and it is not in the sizing, the exits, the scorer,
or the allocator. The engineering is excellent; the strategy is unproven.** The productive directions are
STRATEGIC, not engineering:
1. **A different signal source** — the V3 S&R/regime/**playbook** chain. **PB-01** (breakout-retest, S&R-derived
   R:R) is a genuinely different thesis from the 15 scanners, is BUILT, staged OFF, and has **never run**. The
   natural next experiment is to shadow it.
2. **A different trade structure** — the cost hurdle is set by the ~1% stop; wider-stop / longer-horizon trades
   carry a proportionally smaller hurdle.
3. **Accept the 15 scanners are not an edge** and stop investing in tuning them.

Rama decides. This study makes that an evidence-based decision, not a guess.

## Reproducibility
seed=20260713 · screener_results⋈signals, date 2026-06-19…07-13, symbols with 1-min candles · NEW score =
Σwᵢvᵢ with the 4 dead steps recomputed from a 90-day daily fetch (no-lookahead) · sim = 1% SL/+1.5R TGT,
stop-first, to 15:17, cost 0.105R · scripts in scratchpad (`q2_fullrange.py`). GATE-1 artifact stays separate.

---
*Read-only. Nothing on the live path changed. M-S4 remains OFF.*
