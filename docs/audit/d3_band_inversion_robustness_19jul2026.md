# D3 — does the band inversion survive outside the window it was found in? (READ-ONLY, 19-Jul-2026)

**Bottom line, stated with the discipline the question demands:** the discovery finding's *robust* half (the bottom filter — 0-34 is junk) **replicates** out-of-sample; its *actionable* half (the 60-65 "top-band underperformance," the only reason to lower `min_pass_score`) **does not** — it is absent-to-reversed on the first 3 genuine out-of-sample days. **But 3 out-of-sample days is one short regime window, exactly as the ~2-month discovery was one regime — a single non-replication no more refutes the effect than the discovery confirmed it.** D3 is **not resolvable from data that exists today.**

**Deploy state:** PC == origin == VM bare == `9dd5cdb`; tag `deploy-19jul-consecutive-losses` → `d271525`; schema v44. System DOWN; book flat. The DB snapshot was queried `mode=ro`; the forward-shadow JSONL was read (never written); **no `scripts/*.py` was run** (running the recorder would write to the immutable out-of-sample artifact — see §C). Live DB proven untouched (end).

*No recommendation appears here on `min_pass_score`, D1, or the throttle. D3 is Rama's and is GATE-CLASS: it never changes on backfill evidence alone.*

---

## A. What is actually configured, and what was actually claimed

### A1. The live gate (read from code, per the standing rule)
- **`config/scoring_weights.yaml:22` — `min_pass_score: 60`.** This file is the single source (`:4` "NO weights are hardcoded… this file is the single source"); the live secondary-screener gate is `quality_scorer.py:119` `passed = total_score >= self._weights.min_pass_score`. **This is the "old score" the band study is about.**
- **Tier thresholds:** `high_score_threshold: 80` (`:28`), `medium_score_threshold: 65` (`:29`) → ≥80 HIGH, 65-79 MEDIUM, <65 LOW. The traded 60-65 band is entirely **LOW** tier (matches the sizing audit's "100% LOW"). The scoring-side `tier_multipliers` was deleted 24-Jun (`:24-27`); size tiers live in `system_config.position_sizing` (0.70/0.50).
- **Per-strategy overrides: none.** All 16 strategy YAMLs set `min_score: 0` (`config/strategies/*.yaml:39-41`) ⇒ the global 60 governs universally, no override confound.
- **⚠️ A distinction the decision files should carry:** `system_config.yaml:423` *also* has `min_pass_score: 60`, but that is under the **`v3_chain:` section** (`:387`) and is the V3 hard-gate **shadow** seed — `:422` says *"10a records the score, does NOT gate on it."* The V3 shadow thresholds are separate (`scoring_weights.yaml:42-44` `v3_min_pass_score: 50` / 56 / 75). **The live gate is `scoring_weights.yaml:22`, not `system_config.yaml:423`.**

### A2. The original claim, restated from the source (verified, not inherited)
- **Discovery window: 2026-06-19 → 07-13** (`band_inversion_investigation_13jul2026.md`, `ms4_fullrange_study_13jul2026.md`), **~18 trading days, ONE regime, ~2 months of calendar**; **n=863, stratified 150/band**. That is essentially the whole scored-signal history that had 1-min candles at the time.
- **The claim:** by old-score band, 50-54 wins 61% (+0.37R) while **60-65 (the traded band) wins 31% (−0.27R)** — pooled 35-59 vs 60-65 z=+3.95, survives a vol-normalised stop (z=+4.90). Mechanism: the top band selects already-moved stocks (chasing extension).
- **What the authors themselves marked (verified in the reports):**
  - **ROBUST:** the **bottom filter** — *"0-34 is genuine junk (19% win)"* — and **top-band underperformance** (60-65 among the worst).
  - **DO NOT BUILD ON:** the **exact peaks/dips above 35** — ms4 says *"Non-monotonicity above 35 is noise"* — and any band-pass rule or exact threshold; band_inversion V4 says *"the optimum is noisy… this is 'the excluded mid-band is better,' not 'band 50 is the answer.'"*
  - The effect is small: `min_pass=50` reaches **+0.013R** (breakeven) — the absence of a loss, not the presence of an edge.

---

## B. ⭐ GENUINE OUT-OF-SAMPLE — the forward shadow (the ONLY true test)

### B1. Inventory (from the data, not from when it was believed to start)
The forward-shadow recorder is an append-only **JSONL** (not a DB table): `data_store/v3/forward_shadow_fs-v1.jsonl`, **7,827 records, method `fs-v1`**, last written **16-Jul 18:15** (it has not grown since — the system is down). Per signal it records `old_score`/`old_band`, the M-S4-fixed `ms4_score`, the live `decision`+reason, the true-path **`sim_R`** (same SL/TGT/cost as the Phase-2 audit) and `realized_pnl` for traded signals. `forward_shadow_record.py:8` calls it *"the OUT-OF-SAMPLE evidence a live min_pass_score change requires."* **It is recording correctly — this is not a §B4 "empty/broken" finding.**

| date | records | status |
|---|---:|---|
| 2026-07-13 | 3,467 | **in-window** (07-13 is the last discovery day — overlap, not OOS) |
| 2026-07-14 | 1,644 | genuine OOS |
| 2026-07-15 | 2,535 | genuine OOS |
| 2026-07-16 | 181 | genuine OOS (short session) |

**Genuine out-of-sample = 07-14/15/16 = 3 trading days, 4,360 scored signals.**

### B2. Does the band relationship replicate? (win rate = `sim_R > 0`; `mean R` net of cost)

| old band | n | OOS win% | OOS mean R | discovery win% |
|---|---:|---:|---:|---:|
| 0-34 | 118 | **20.3%** | −0.468 | 19% (junk) |
| 35-39 | 15 | 13.3% | −0.506 | 43% |
| 40-44 | 29 | 48.3% | −0.200 | 55% |
| 45-49 | 287 | 53.0% | −0.027 | 38% |
| 50-54 | 344 | **33.7%** | −0.009 | **61% (the "best")** |
| 55-59 | 3,182 | 42.0% | −0.058 | 43% |
| **60-65** | 162 | **46.3%** | **+0.085** | **31% (the "worst")** |

- **The bottom filter HOLDS:** 0-34 (and 35-39) are genuinely bad OOS (20.3% / 13.3% win, R ≈ −0.47/−0.51). The authors' *robust* claim survives.
- **The inversion FAILS:** 60-65 is **not** the worst OOS — it has the highest win rate of the mid/high bands and the **only positive mean R**. The sharpest discovery contrast **reverses**: 50-54 vs 60-65 is **z = −2.72** OOS (discovery was +5.22). The pooled 35-59 vs 60-65 is z = −1.07 (reversed, non-significant). Above the 0-34/35-39 floor, OOS `mean R` is if anything mildly *increasing* in score (0-34 −0.47 → 60-65 +0.085) — the opposite of an inversion.

### B3. Power — and this is NOT the "too small to say" case
`sim_R` for 60-65 arrives ~54/day, 35-59 ~1,286/day. To detect the discovery-sized win-rate gaps at α=0.05 / 80% power: the **pooled 48% vs 31%** needs ~126 in the limiting (60-65) group ≈ **~2 OOS days**; the **sharp 61% vs 31%** ≈ **~1 day**; the small 43.5% vs 31% ≈ **~4 days**. **We have 3 OOS days (162 in 60-65, 344 in 50-54)** — roughly adequate power for the pooled and more than adequate for the sharp contrast. So the non-replication is a **genuine failure to reproduce an effect we were powered to see**, not merely an underpowered shrug. *(Caveat: this power assumes the discovery effect size transfers to the natural distribution; see the stratification note below.)*

### B4. A methodological asymmetry worth stating plainly
The discovery study **stratified** — 150 signals per band — to balance power across bands. The live/forward distribution is **not** balanced: **73% of scored signals fall in 55-59** and only **~4% in 60-65** (OOS: 3,182 vs 162). So the discovery result describes a **balanced counterfactual sample**, not the traded distribution. The win-rate-by-band comparison above is still like-for-like (same bands, same sim), but the "we trade the worst band" framing is about a stratified reconstruction, not the live book — and in the live book the 60-65 that pass the gate are then mostly killed downstream (of 162 OOS 60-65 signals, **27 PROCESSED**; the rest rejected by concentration/throttle/open-positions/duplicate).

---

## C. IN-WINDOW ROBUSTNESS — labelled in-window, and mostly NOT reproducible read-only

**Everything in this section is in-window robustness. It tests whether the effect is a stable feature of the discovery data or an artefact of slicing; it cannot speak to a different regime.**

### C1-C2. The full sub-period / holdout re-slice is not feasible read-only — itself a finding
The discovery window's **per-signal simulated outcomes were never persisted** — only the aggregate band table in the report. The forward-shadow JSONL only begins **07-13**, so it covers the discovery window's **last day only** (06-19 → 07-12 is absent). Regenerating the per-signal discovery sims would require running `scripts/forward_shadow_record.py --date …` for those dates, which **appends to the immutable `fs-v1` artifact** (its header: *"never mutate a written record or an existing file, or the out-of-sample evidence is void"*) — forbidden under read-only. **So a faithful in-window sub-period test cannot be run without the harness, and the discovery study is not reproducible read-only from persisted artifacts.** This matters: the designated confirmation path (the forward shadow) is sound, but the *original* study's per-signal basis is gone.

### C3. The one in-window day available (07-13) shows the effect is day-dominated
07-13 (the last discovery day, natural distribution) band win% / mean R:

| band | 0-34 | 50-54 | 55-59 | 60-65 |
|---|---:|---:|---:|---:|
| 07-13 win% | **75.3%** | 43.2% | 43.2% | 52.9% |
| 07-13 mean R | **+0.676** | −0.138 | −0.036 | +0.269 |

On this single in-window day, **even the "robust" bottom filter reverses** — 0-34 wins 75% (pooled discovery: 19%). One day cannot establish anything, but it demonstrates that **day-level (market/regime) variance is large relative to the ~17-30pp band effect** — consistent with the effect being fragile to sub-period slicing, which is precisely what the authors predicted for the *exact peaks* and is now visible for the *whole ranking* on at least one day.

### C4. Negative control — the pipeline is sound
Permuting `sim_R` labels across the OOS signals 500× and recomputing the pooled 35-59 vs 60-65 win gap: **permuted mean ≈ −0.003 (≈ 0, as it must be if the measurement is sound)**, and the observed gap (−0.042) sits inside the null (**|permuted| ≥ observed in 27%**, p=0.27). So (a) the pipeline is not manufacturing an effect, and (b) the observed OOS band gap is indistinguishable from noise. **The measurement is trustworthy and the effect is not there.**

### C5. Effect sizes, framed as the authors framed them
Keep the original framing: the best the discovery study offered was `min_pass=50` → **+0.013R**, i.e. the *removal of a loss*, not an edge. Out-of-sample there is not even that: above the 0-34/35-39 floor every band's `mean R` sits within ±0.09R of zero. This is a **no-edge / bottom-filter-only** picture, not a band-pass opportunity.

---

## D. What D3 now rests on

| leg | status |
|---|---|
| **Genuine out-of-sample** | **3 days (07-14/15/16), ~adequately powered, and the inversion did NOT replicate** — 60-65 best-of-band, sharp contrast reversed, pooled effect = noise. The **bottom filter did** replicate. |
| **In-window robustness** | **Not reproducible read-only** (per-signal discovery sims not persisted; regenerating writes the immutable OOS artifact). The one available in-window day shows day-domination. |
| **Unanswerable from existing data** | whether the inversion is regime-specific — **both** the discovery (~2 months, one regime) and the OOS (3 days, one regime) are single-regime, pointing opposite ways. Neither is dispositive. |

**So D3's actionable premise — that `min_pass=60` systematically buys the anti-edge top band — is materially weaker than `04_d3_min_pass_threshold.md` presents it:** it failed its first genuine out-of-sample test. It is **not refuted** (one OOS regime cannot do that), but it is **no longer supported by out-of-sample evidence**, and the only confirmation path is more forward-shadow days.

**D2 (the FREEZE collision, noted not resolved):** the genuine confirmation path is forward-shadow accumulation, which requires the system running — and changing `min_pass_score` mid-accumulation would reset the regime measurement's clock (`08_freeze_min_pass_during_measurement.md`). D3 and #08 still cannot both be exercised in the same window. Rama's to resolve.

**D4 — triage reclassification:** in the §C triage, D3 was **COMPUTABLE NOW** (out-of-sample backtest). That was wrong in the way the last batch's lesson predicts: the "out-of-sample backtest" is the forward shadow, which is **3 days and needs more** — and the in-window re-slice is not reproducible read-only. **D3 moves COMPUTABLE NOW → NEEDS THE SYSTEM RUNNING** (forward-shadow days; the first 3 already point away from the finding). The decision file and the triage table are updated accordingly.

---

## PROOF OF READ-ONLY
- DB queries ran on the `mode=ro` snapshot; the forward-shadow JSONL was read with `python3`/`cat` only (no writes, no `--date` regeneration). No `scripts/*.py --db`.
- **Live DB before:** sha256 `6df0c09a…`, mtime `2026-07-19 11:14:29`, size `89,968,640`. **After: identical** (recorded at commit).

*Docs-only. No code, config, schema or flag changed. D3 remains Rama's, and unresolved.*
