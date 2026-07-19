# Decision — D3: the `min_pass_score` threshold (band inversion)

**Status:** OPEN — Rama's call. **Type:** strategic (edge). **Blocked by:** interacts with the FREEZE decision (changing it during the regime measurement corrupts that measurement).
*Summary of the record, not a recommendation. Lettering is a label, not a ranking. The two evidence bodies below point in opposite directions and are given equal weight deliberately.*

## The choice
- **Option A — leave `min_pass_score` at 60.**
- **Option B — lower it** (the sweep modelled ~50 as an illustrative point).

## What is known — evidence that the current threshold selects the worst band
- **The scorer does not rank outcomes.** M-S4-fixed NEW score: Spearman **rho = +0.003** vs simulated R (permutation p≈0.91, inside the null); decile win-rates FLAT (D1→D10: 48/36/56/40/49/36/52/40/41/37%). **OUTCOME C, rigorously proven: no ranking power at any score level** (`docs/audit/ms4_fullrange_study_13jul2026.md`).
- **The score inverts at the top.** By old band, **50–54 wins 61% (+0.37R)** while the **60–65 band we trade wins 31% (−0.27R)**; pooled 35–59 (48%, n=654) vs 60–65 is **z=+3.95, p=7.8e-05 (4–5σ)**; it **survives a vol-normalised stop** (z=+4.90), so it is not a fixed-stop artifact (`docs/audit/band_inversion_investigation_13jul2026.md`). Mechanism: the top band selects stocks that have **already moved** (big `price_action` candle far above VWAP) — chasing extension.
- **Threshold sweep (freq-weighted, 1-min-path simulation):** min_pass **60 → 31% win / −0.274R** (the single worst); lowering to **~50 → 45% win / ~breakeven**, above the 43.5% breakeven bar. The lift comes from admitting the 40–44 and 50–54 bands. Caveat in the source: the optimum is noisy (45–49 is only 38%), so this is *"the excluded mid-band is better,"* not *"band 50 is the answer."*

## What is known — evidence that lowering it looked worse in the real book
- **In the actual traded book**, reconstructed per-day min-score (`docs/audit/regime_thesis_minscore_control_18jul2026.md`, memory `regime-minscore-control-18jul`): **min=60 → 6 days, win 45.7%, R/trade −0.139**; **min=55 → 4 days, win 33.3%, R/trade −0.330 (~2.4× worse).** Directionally the opposite of the backtest.
- **But this observation is fully confounded:** 6 days vs 4 days, both loss-making, in **different calendar weeks**. The source states plainly: *"an observation, not evidence."* It cannot be weighed against the backtest as if equivalent — it is a real-book signal of unknown validity, and the backtest is a counterfactual simulation of unknown live-transfer.

## What is unknown
- **Whether the backtest's ~breakeven lift transfers to the live book** — *[knowable only by running the system]* at a lower threshold for N days — and doing so **collides with the FREEZE decision** (a mid-window threshold change re-fragments the regime sample and resets its ~2.2-month clock).
- **Whether the inversion is stable across regimes** — *[not knowable from the current record]* (single-regime dataset, ~2 months).

## What changes if the chosen direction is wrong
- **If B (lower) and the real book behaves like the confounded observation, not the backtest:** admits weaker signals and worsens the book (the observation put it ~2.4× worse per R).
- **If A (leave) and the backtest is right:** the system keeps trading the empirically worst-performing band (31% win, −0.27R) while a better-performing band sits just below the cut.

## What would settle it
- A larger **out-of-sample** simulation (fresh 1-min data beyond 06-19…07-13) to test whether the mid-band advantage is stable rather than a two-month artifact — cost: data + a re-run of the existing harness, no deploy. A **live** test at a lower threshold would settle transfer but cannot run concurrently with the regime measurement (FREEZE).
