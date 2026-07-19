# Decision — PerformanceAllocator (wire it, or leave `perf_weight ≡ 1.0`)

**Status:** OPEN — Rama's call. **Type:** capital/sizing mechanism. **Blocked by:** nothing; but its effect is likely masked by the concentration cap (below).
*Summary of the record, not a recommendation. Lettering is a label, not a ranking.*

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
