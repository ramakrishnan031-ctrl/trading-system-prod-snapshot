# Q2.6 — MAE/MFE TRADE-STRUCTURE ANALYSIS (deriving stop/target from data)

**Date:** 13-Jul-2026 (off-market) · **Read-only**, DB copy · **91 trades** with excursion data (83 LONG, 8 SHORT).
MFE/MAE are direction-aware (verified on both longs and all 8 shorts). Nothing changed. **Evidence, not a decision.**

## The three answers (blunt)

**Q1 — Is the current −1R stop too wide? → NO clear free lunch (the "free lunch" is refuted).**
The median winner dips only **−0.35R** before turning (supports the instinct) — BUT the tail is real: **13/36
winners (36%) dipped past −0.5R**, and the deepest winner reached **−0.94R**. So a −0.5R stop would kill ~36%
of winners; a −0.6R stop kills ~28% (10/36). The stop sweep confirms expectancy does **not** improve
monotonically as you tighten — it gets *worse* from −0.8 to −0.5 (winners stopped faster than losers shrink),
then a −0.4R "optimum" appears only by stopping out **44% of winners** (a knife-edge overfit, not real). The
one mild, **noise-level** signal: −0.9R is marginally better than −1.0R (−0.007 vs −0.044, only 1 winner lost).
**There is no structural free R:R gain in tightening the stop.**

**Q2 — Is Rama's 1:2 target supported? → NO — the data says it is HARMFUL.**
The MFE distribution **collapses beyond 1.5R**: hit-rate is **32% at 1.5R but only 4% at 2.0R** (winners' MFE
p90 = 1.83R; only a handful ever reach 2R). Widening the target trades away the reliable 1.5R hits for the rare
2R hit. Expectancy worsens as the target widens: conservative bound 1.5R→−0.20 vs 2.0R→−0.87; even the
(uninformative) optimistic bound shows no gain. **Rama's 1:2 instinct is intuitive but contradicted by this
data — the moves simply are not there often enough. It would reduce expectancy.**

**Q3 — Data-derived optimal (stop, target)? → The surface is FLAT and NEGATIVE; there is no edge to be found
in the geometry.** The best conservative cell is **(−0.5R, +1.75R) → +0.000R** — literally breakeven, and a
knife-edge overfit on 91 trades. The current **(−1.0R, +1.5R) → −0.043R** sits right next to it. Every cell in
the grid lands in **[−0.9, +0.0]R** — you can shuffle expectancy around within a flat-negative band, but no
(stop, target) pair creates a real edge.

## What this means (it reinforces Q2.5)

**Neither the stop nor the target is the lever.** The expectancy surface is flat-negative because the
**entries do not produce enough favourable movement** — only 32% of trades reach even +1.5R, 4% reach +2R, and
winners cluster exactly at the 1.5R target (median MFE +1.57R). You cannot fix a no-edge entry set by moving the
exits. **M-S4 (better entry selection) remains the fix** — the exits (including the current 1:1.5) are already
close to the best this trade population supports.

## Structural vs overfit (mandatory honesty)

- **STRUCTURAL (trust these):** (1) the MFE distribution collapses past ~1.5R → wider targets are hit far less
  often; (2) winners' MAE has a real tail to −0.94R → the stop is *not* "twice as wide as needed"; (3) the whole
  surface is flat-negative → the problem is entries, not exit geometry.
- **OVERFIT / NOISE (do NOT act on):** the specific "optimal" cells (−0.5,+1.75) / (−0.4) — they win only by
  degenerate winner-stopping on 91 trades in one regime; the (−1.0→−0.9) nudge is within noise.
- **PATH DEPENDENCE:** MAE/MFE is an approximation, not a backtest. A tighter stop removes trades from the
  sample; a wider target changes what happens after the old target. The stop-vs-target race is assumed
  stop-first (conservative). This is directional evidence only.
- **SMALL SAMPLE / ONE REGIME:** 91 trades over ~2 months. **Re-derive on post-M-S4 data before trusting any of
  this** — better entries will shift both distributions (trades go less against you and further for you).

**Do not change the live stop/target on this analysis.** The actionable conclusions are negative: don't widen
to 1:2, don't chase a tighter stop — fix the entries (M-S4), then re-derive.

## LONG vs SHORT
LONG (n=83): same shape as overall (winners' MAE p10 −0.86R; no free lunch; wider target harmful). SHORT (n=8)
is **pure noise** — its apparent "best stop −0.9R / target 1.75R" cannot be trusted at n=8. If anything, a future
playbook could carry its own geometry, but there is no evidence here to set it.

---
*Read-only. Nothing changed. Script: `scratchpad/q26_mae_mfe.py` → `/tmp/q3/q26.py`.*
