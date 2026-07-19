# Decision — D3: the `min_pass_score` threshold (band inversion)

**Status:** OPEN — Rama's call. **Type:** strategic (edge). **Blocked by:** the FREEZE decision (changing `min_pass_score` mid-measurement resets the regime clock). **Triage (updated 19-Jul): COMPUTABLE NOW → NEEDS THE SYSTEM RUNNING** — see the update below.
*Summary of the record, not a recommendation. Lettering is a label, not a ranking. Three evidence bodies now bear on this — the in-window discovery study, the confounded real-book observation, and (new, 19-Jul) the first genuine out-of-sample test — and they do not agree.*

## ⚠️ UPDATE 19-Jul-2026 — the first genuine out-of-sample test (`docs/audit/d3_band_inversion_robustness_19jul2026.md`)
- **Config correction:** the live gate is **`config/scoring_weights.yaml:22` `min_pass_score: 60`** (tiers `medium=65`/`high=80` at `:28-29`); all 16 strategies set `min_score: 0`, so the global governs. (`system_config.yaml:423`'s `min_pass_score: 60` is the *separate* V3-chain **shadow** seed, non-gating — do not cite it as the live threshold.)
- **The forward shadow is the only genuine out-of-sample evidence, and it now holds 3 post-discovery days** (07-14/15/16, 4,360 scored signals; `data_store/v3/forward_shadow_fs-v1.jsonl`). On them:
  - the **ROBUST half replicates** — the bottom filter holds (0-34 win 20.3%, R −0.47: junk out-of-sample too);
  - the **ACTIONABLE half does NOT** — 60-65 is *not* the worst band out-of-sample; it has the highest mid/high-band win rate (46.3%) and the only positive mean R (+0.085). The sharpest discovery contrast **reverses** (50-54 vs 60-65 z=−2.72 out-of-sample vs +5.22 in-window); the pooled effect is **indistinguishable from noise** (permutation p=0.27, negative control sound); and the test was roughly **powered** for the discovery-sized effect (~2-4 OOS days needed, 3 available).
- **Caveat that cuts both ways:** 3 OOS days is one regime, exactly as the ~2-month discovery was one regime. A single non-replication no more refutes the effect than the discovery confirmed it. **D3 is not resolvable from data that exists today.**
- **Net:** the actionable premise (min_pass=60 buys the anti-edge top band) is **materially weaker than the sections below present it** — it failed its first out-of-sample test. Read the "evidence that the current threshold selects the worst band" section as *what was found in one window*, now with a second (out-of-sample) window pointing the other way.

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
- **The out-of-sample test has begun (19-Jul) and is the confirmation path — but it needs more than 3 days.** The forward-shadow recorder (`data_store/v3/forward_shadow_fs-v1.jsonl`) appends one record per scored signal whenever the system runs; a robust verdict needs enough post-discovery days across more than one regime. The first 3 days already point away from the finding.
- **The in-window sub-period re-slice of the discovery data is NOT reproducible read-only** — the per-signal sims were never persisted, and regenerating them requires running the recorder, which appends to the immutable out-of-sample artifact. The original study cannot be re-sliced without the harness.
- A **live** test at a lower threshold would settle transfer directly but collides with FREEZE (it resets the regime clock).
