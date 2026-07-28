# Decision — D1: the concentration cap (position sizing)

**Status:** OPEN — Rama's call. Evidence package updated 19-Jul (a corrected claim; see below).
**Type:** capital-posture. **Blocked by:** coupled to D3/scorer (see "What would settle it").
*Summary of the record, not a recommendation. Lettering is a label, not a ranking.*

## The choice
- **Option A — leave `max_concentration_pct` at its current value** (a ceiling of **10% of actual capital**, no leverage — ~Rs 987 at the 28-Jul capital of Rs 9,872.30; it was ~Rs 990 at the 19-Jul Rs 9,875.60). ⚠️ **The cap is a PERCENTAGE; the rupee ceiling moves with capital daily** — re-read `fm_ledger`'s latest INIT before deciding against a rupee figure. [[capital-vocabulary]]
- **Option B — raise it** (the sizing report modelled 0.10→0.25 and →0.50 as illustrative points).

## What is known (current evidence, with citations)
- **Concentration binds on 100% of sized trades** — `binding_constraint = concentration` in **298/298** (`docs/audit/q9_batch4_sizing_floors_caps_18jul2026.md`; memory `capital-chain-binding-constraint-analysis-13jul`). `qty_by_risk` binds **0** times; the risk sizer is dead by algebra (concentration binds ⟺ `sl_distance < 10% of price`, always true for 1–3% intraday stops).
- **Actual risk is ~1/20th of intended:** median **Rs 4.92** vs intended 1% = **Rs 98.76**. Tier 0.5 applies on 298/298. Median position **Rs 442**, max **Rs 999**. **~7% of buying power deployed.**
- **The >Rs 990 exclusion:** ~**23%** of priced signals are >Rs 990 and untradeable under the cap (`floor(990/price)=0`); threshold ~Rs 990 exact, share verified.
  - **⚠️ CORRECTED 19-Jul (supersedes the D1 evidence package's prior wording).** The exclusion was previously described as *"hitting LONGs ~4.5× harder."* Measured by **rate**, it is **LONG 10.00% vs SHORT 10.72%** — SHORTs marginally harder, within 0.7pp. The "4.5×" (and batch 4's later "9.54×") are *count* ratios that reflect the **10.2× long-volume skew** (28,027 vs 2,742 signals), not the cap treating longs differently. Source: `docs/audit/signal_mortality_census_19jul2026.md` claim 3, `docs/audit/throttle_selection_and_record_correction_19jul2026.md`.
- **Population-bias qualifier (census §B1):** the sizer sees a population **enriched 1.45× in >Rs 990 names** (34.73% at the sizer vs 24.02% at admission); the cap deletes that band before the risk engine (0.14% there). Batch 4's verdicts hold *for the population reaching the sizer*.
- **Scaling exposure (modelled):** raising the cap multiplies position size and therefore multiplies P&L in both directions — `docs/audit/sizing_interaction_impact_report_13jul2026.md` §4 gives **2× at conc 0.25, 4× at conc 0.50**. The current book is net-negative (−0.100R; expectancy autopsy).
- **Adjacent latent item:** `PerformanceAllocator` is never wired ⇒ `perf_weight ≡ 1.0`, and `position_sizer.py:506`'s 2× ceiling is latent-pinned (see the PerformanceAllocator decision file).

## What is unknown
- **Whether the book's expectancy would be positive at larger size** — *[not knowable from the current record]*: expectancy is measured on the throttle-and-fill-selected, cost-dominated small-position book (see §C). It cannot be read as the intended book's edge.
- **The traded behaviour of the >Rs 990 names** currently excluded — *[knowable from existing data]*: they can be simulated on 1-min paths exactly as the band study did (`ms4_fullrange_study`), without changing the cap.

## What changes if the chosen direction is wrong
- **If B (raise) and the edge is actually negative:** the realised loss scales with the multiplier (2× at 0.25, 4× at 0.50) on a book that is currently −0.100R net.
- **If A (leave) and the edge is actually positive:** the system continues deploying ~7% of capital in Rs 4.92-risk positions where cost (Rs 0.40) is ~6% of average net (Rs 6.99) — i.e. the edge, if any, is being spent on costs at this size.

## What would settle it
- A **positive-expectancy baseline**: the sizing report frames the cap as a lever to pull *after* expectancy is positive, which couples D1 to the scorer/M-S4 work (D3) — fix the ranking, re-soak, then the cap acts on a book whose sign is known. Cost: the D3 change + a fresh soak (N trading days). Until then, raising the cap acts on a book of unknown sign.
