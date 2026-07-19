# Decision — PerformanceAllocator (wire it, or leave `perf_weight ≡ 1.0`)

**Status:** OPEN — Rama's call. **Type:** capital/sizing mechanism. **Blocked by:** nothing. ~~its effect is likely masked by the concentration cap~~ — **the masking premise is REFUTED (see the 19-Jul update); the reachability is now computed.**
*Summary of the record, not a recommendation. Lettering is a label, not a ranking.*

## ⚠️ UPDATE 19-Jul-2026 — the "COMPUTABLE NOW" label HOLDS, and the algebra refutes "masked by concentration" (`docs/audit/candle_retention_and_perfallocator_feasibility_19jul2026.md`)
- **Feasible and computed (298-trade power).** Unlike D3 (per-signal basis never persisted) and D4 (data survives but underpowered), the sizer's full breakdown **is persisted per trade** (schema v34: `qty_by_risk/capital/concentration`, `binding_constraint`, `tier_weight_applied`, `perf_weight_applied`). Coverage **298 of 361 trades** (the 63 without are pre-v34/recovered). This is the **only** one of the three "COMPUTABLE NOW" labels whose data *and* power both held.
- **The premise below ("masked by the concentration cap") is WRONG on mechanism and data.** `perf_weight` is applied **after** `raw_qty = min(risk, capital, concentration)` — it multiplies raw_qty up to a `2×raw_qty` ceiling (`position_sizer.py:446/503/506`). So concentration binding raw_qty (confirmed: `binding_constraint='concentration'` on **298/298**) does **not** prevent perf_weight from changing final qty.
- **Reachability (counterfactual over `perf_weight`'s configured clamp [0.5, 2.0], validated by reproducing `qty_planned` on 298/298):** final quantity changes on **233 of 298 trades (78%)** over the full range, and **151 of 298 (51%)** over a modest [0.8, 1.25] band. It is decorative on only **65** trades — those with `raw_qty=1`, pinned at 1 lot by the integer floor (not by concentration). So: **"it would bind on 233 (full range) / 151 (modest)," not "decorative on N/N."**
- **Neutral:** this is reachability, not merit. `perf_weight` would be driven by per-strategy win-rate (`PA2`), whose ranking power is unproven (M-S4 ρ +0.003; D3 OOS non-replication). Conditional on **today's unlevered sizing** — under 5× MIS leverage raw_qty grows, fewer trades pin at 1, so it would bind on even more (re-run required). Also: the Q9 batch-4 "only 4 of 15 sizing *guards* bind" does **not** transfer here — perf_weight is a multiplier, not one of those guards. Wiring/not-wiring remains Rama's call (couples to D1, the latent 2× ceiling `:506`, and leverage).

## The choice
- **Option A — leave it unwired:** `perf_weight ≡ 1.0` for every trade; the three performance keys stay decorative.
- **Option B — instantiate it** so `perf_weight` scales sizing by recent per-strategy performance.

## What is known (current evidence, with citations)
- **`PerformanceAllocator` is configured but never instantiated;** `perf_weight` is pinned at **1.0** on all trades, and **three config keys are decorative** (memory `q9-batch4-sizing-reachability-18jul`; the Q9 batch-4 evidence package).
- **`position_sizer.py:506` holds a 2× ceiling that is LATENT** — reachable only if `perf_weight` ever exceeds the ceiling, which cannot happen while it is pinned at 1.0. It is held by a test that fails the moment it becomes reachable (LIVE-vs-LATENT discipline, `feedback-live-vs-latent-findings`).
- **The concentration cap binds on 100% of sized trades** (see D1). A multiplier applied *before* a cap that already sets qty is masked by that cap in the same way the tier multiplier (0.5) is largely masked — the Q9 batch-4 finding that **only 4 of 15 sizing guards can bind** applies here.

## What is unknown
- **Whether `perf_weight` could ever bind given concentration binds 100%** — *[knowable from existing data]*: the same algebra used for the sizing-guard reachability (D1 / batch 4) would answer whether a `perf_weight > 1` can change final qty, or is dominated by the concentration ceiling in every case.
- **What per-strategy performance signal it would use, and whether that signal has predictive power** — *[not knowable from the current record]*: this couples to D2/D3 (the same "does the score/history rank outcomes?" question).

## What changes if the chosen direction is wrong
- **If B (wire) and it is masked by the concentration cap:** no behavioural change (effort for nothing), *unless* it de-masks the latent 2× ceiling (`position_sizer.py:506`), in which case sizing could **double** for a high-`perf_weight` strategy on a book of unknown sign (D1).
- **If A (leave) and per-strategy sizing would have helped:** forgoes a mechanism that up/down-weights by recent performance — but its value is unmeasured and, per D2/D3, the underlying ranking signal is currently absent.

## What would settle it
- The reachability algebra above (existing data, no deploy): does a `perf_weight ≠ 1` change final qty under the current concentration ceiling on any recorded trade? That converts "it's decorative" into "it is decorative on N/N trades" or "it would bind on M."
