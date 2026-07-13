# BAND-INVERSION INVESTIGATION — corrects a Phase-2 misreading

**Date:** 13-Jul-2026 (off-market) · **Read-only; nothing changed; M-S4 stays OFF.** · seed=20260713 ·
n=863 (150/band), 145 symbols, 06-19…07-13, ONE regime, ~2 months. Extends GATE-1 + Phase-2 (both stand as
artifacts; this corrects an *interpretation*).

## The correction (stated plainly)
Phase-2 dismissed the by-band non-monotonicity as "noise above 35" and read rho≈0 as "no ranking signal
anywhere." **That reading was wrong.** rho≈0 does not only mean "no signal" — it also arises from a signal
that **reverses**. The old score is a **filter at the bottom that INVERTS at the top**, and a Spearman/linear
correlation reports that non-monotonic shape as ~0.

## V1 — the inversion is statistically real (independently verified; Web Claude's z=5.21 confirmed)

| old band | n | win (uniform 1% stop) | net expR | z vs 60–65 | p |
|---|---|---|---|---|---|
| 0–34 | 59 | 19% | −0.74 | — | junk (correctly filtered) |
| 40–44 | 150 | 55% | +0.17 | +4.20 | 2.6e-05 |
| **50–54** | 150 | **61%** | **+0.37** | **+5.22** | **1.8e-07** |
| 55–59 | 150 | 43% | −0.04 | +2.16 | 0.031 |
| **60–65 (we trade this)** | 150 | **31%** | **−0.27** | — | *the worst band* |

Pooled 35–59 (48%, n=654) vs 60–65: z=+3.95, p=7.8e-05. **The band we actually trade underperforms the bands
below it, at 4–5 sigma on this dataset.**

## V2 — the volatility control (decisive): the inversion is NOT a fixed-stop artifact
- **V2a:** the old score does **not** track volatility — median daily ATR% is 3.94% at 50–54 vs **3.55% at
  60–65** (the high band is if anything *calmer*). So the uniform 1% stop is not systematically too tight for
  high-score signals.
- **V2b:** re-ran with a **vol-normalised stop** (SL=0.26×ATR → median ~1%, same seed/cost). 50–54 win 60% vs
  60–65 win 31%, **z=+4.90, p=9.8e-07 → the inversion SURVIVES.** It is a real effect on this dataset, not an
  artifact of the fixed stop.

## V3 — mechanism (H2, "extended moves"): supported
Mean ALIVE-step values, 50–54 → 60–65: `price_action` **0.83→1.00**, `vwap_position` 0.95→**1.00**,
`time_of_day` 0.60→0.93, `signal_age` 0.75→1.00. **What pushes a signal into the top band is a big decisive
candle (`price_action`) far above VWAP** — i.e. a stock that has **already moved.** H2 is mechanistically
supported: the high-score selection is systematically **buying the top / chasing extension.** (Time/age also
contribute, so it is not *solely* extension — but extension is a real, dominant component.)

## V4 — threshold sweep (H3): min_pass=60 selects the anti-edge
Freq-weighted over the true corpus distribution, uniform 1% stop (READ-ONLY — measured, not changed):

| min_pass | admitted bands | freq-wtd win | net expR |
|---|---|---|---|
| 40 | 40–65 | 44% | +0.002 |
| 45 | 45–65 | 44% | −0.005 |
| **50** | 50–65 | **45%** | **+0.013** |
| 55 | 55–65 | 42% | −0.052 |
| **60 (current)** | 60–65 | **31%** | **−0.274** |

**The current threshold selects the single worst band.** Lowering it to ~50 lifts the (freq-weighted) win rate
to 45% — *above* the 43.5% breakeven bar (GATE-1 §2) — and net expectancy from −0.27R to ~breakeven/slightly
positive. The lift comes mainly from admitting the 40–44 and 50–54 bands. (The optimum is noisy — 45–49 is only
38% — so this is "the excluded mid-band is better," not "band 50 is the answer.")

## V5 — restating the conclusion (with discipline)
- **M-S4 still does not create a monotonic ranker** (its rho was +0.003) — no case to flip M-S4 *for ranking*.
  That part of GATE-1/Phase-2 stands.
- **BUT Phase-2's "the scorer has no signal" is SUPERSEDED:** the scorer carries an **inverted** signal above
  ~35, and **`min_pass_score=60` is anti-selecting — it keeps the worst band and discards better ones.** This
  is a genuine, actionable finding that my earlier "no edge" framing missed. I got it wrong; the data corrects
  it.
- **The strategic picture shifts** from "no edge exists" to: **a weak positive edge plausibly sits in the
  40–54 band the system has excluded for months, and the current threshold actively selects against it** — and
  the *mechanism* (chasing extended moves) is exactly the trap the **V3/PB-01 pullback thesis** was built to
  avoid. PB-01 (enter on the pullback, not the breakout candle) is now supported by *data*, not only principle.

## What this does and does NOT justify
- **It does NOT justify a config change now.** A live `min_pass_score` change is a GATE-CLASS, real-money
  decision — **Rama's explicit sign-off + a forward shadow test**, not a nudge. The effect is small (~+0.01R,
  within noise), on ONE regime / ~2 months / 145 symbols; a 5-sigma win-rate gap on n=150 is still one regime.
- **It DOES justify:** (1) re-scoping the M-S4 work — the interesting question is no longer "wire the dead
  inputs to rank better" but "why does the alive-step score invert, and is the excluded band a real edge?";
  (2) prioritising the **PB-01 shadow** as the natural next experiment; (3) a forward **shadow** that scores
  live signals and records outcomes across the full range (the honest proof the backfill can only estimate).

## Reproducibility
seed=20260713 · same sample/sim/cost as Phase-2 · vol-stop SL=0.26×ATR (k s.t. median≈1%) · z = two-proportion
test · V4 freq weights from the corpus band counts (704/441/890/2249/2730/14063/950). Script:
`scratchpad/q2_inversion.py`. GATE-1 + Phase-2 remain separate standing artifacts.

---
*Read-only. Production config unchanged. M-S4 remains OFF.*
