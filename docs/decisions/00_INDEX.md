# Open Decisions — Index

Assembled 19-Jul-2026 so each of Rama's open choices sits in one place with the **current, corrected** evidence, instead of scattered across ~a dozen audit reports written over three weeks. **Every file states options, evidence for each, what is unknown, the exposure in each direction, and what would settle it — and recommends nothing.** The order below is by file number (a label), not by priority.

Deploy state at assembly: PC == origin == VM bare == `3dda9f7`; code tag `deploy-19jul-consecutive-losses` → `d271525`; schema v44. System DOWN; book flat.

## The decisions

| # | Decision | Type | Status | What blocks / gates it |
|---|---|---|---|---|
| [01](01_e4_w10_pnl_contract.md) | **E4/W10** — the `pnl_delta` contract (daily-loss input) | capital-posture | OPEN — **exposure now COMPUTED (19-Jul): N=0**; residual is a posture sign-off | threshold was wrong in the file (fixture) — corrected to 3%×~Rs10k ≈ **Rs300**; N=0, closest approach Rs 243; deploy needs a manual flatten first · `e4_w10_outcome_impact_19jul2026.md` |
| [02](02_d1_concentration_sizing.md) | **D1** — the concentration cap (sizing) | capital-posture | OPEN | coupled to D3 — the cap is a lever on a book whose sign is unknown |
| [03](03_d2_strategic_direction.md) | **D2** — strategic direction / strategy mix | strategic | OPEN | a positive-control baseline + regime attribution (Q10, token-blocked) |
| [04](04_d3_min_pass_threshold.md) | **D3** — the `min_pass_score` threshold (band inversion) | strategic (edge) | OPEN — **1st out-of-sample test done (19-Jul): the inversion did NOT replicate** (the bottom filter did) | reclassified **COMPUTABLE NOW → NEEDS THE SYSTEM RUNNING** (forward-shadow days); tension with FREEZE; `d3_band_inversion_robustness_19jul2026.md` |
| [05](05_d4_exit_policy.md) | **D4** — exit policy | strategic (edge) | OPEN — **facts verified; exit claims UNDERPOWERED OOS** (19 entered / 9 winners) | **NEEDS RUNNING** (power-bound, not data-bound); **candle retention SETTLED 19-Jul: SURVIVES** — 90d prune (`db_retention.py`) governs `analytics.candles`, seed expires ~12–15 Oct vs sample matures ~mid-Aug…mid-Sep, rolling window 62>34 trading days; `candle_retention_and_perfallocator_feasibility_19jul2026.md` + `forward_shadow_capacity_and_d4_feasibility_19jul2026.md` |
| [06](06_performance_allocator.md) | **PerformanceAllocator** — wire it or leave `perf_weight ≡ 1.0` | sizing mechanism | OPEN | **COMPUTABLE-NOW label HOLDS (19-Jul, computed on 298 trades)** — the *only* one of the three that did; **"masked by concentration" REFUTED**: perf_weight is a post-cap multiplier, would change final qty on **233/298 (78%)** over [0.5,2.0], **151/298** over [0.8,1.25]; conditional on unlevered sizing; `candle_retention_and_perfallocator_feasibility_19jul2026.md` |
| [07](07_regime_enable.md) | **Regime** — enable / build Phase 1 / leave | strategic | OPEN | Q10 (~2.2 months of forward within-cell data); do-not-flip-mid-soak constraint. *(20-Jul: "+ token" struck — regime **computes today**, runtime-verified; the token/backfill was never on its path.)* |
| [08](08_freeze_min_pass_during_measurement.md) | **Freeze `min_pass_score`** during the measurement? | measurement hygiene | OPEN | downstream of Regime (#07) and D3 (#04) |
| [09](09_prune_retention.md) | **Prune retention** & status-selectivity | data-retention | ✅ **CLOSED 25-Jul — Option A, leave as-is, NO code change** | — (decided: 2,667 rows vs ~9.9 MB/day is noise, not value) |
| [10](10_entry_throttle_admission.md) | **Entry-throttle admission** — arrival-order vs ranked | strategic / mechanism | OPEN — **new** (throttle analysis) | ranking value coupled to D3; **counter-case is strong** |

## How they are coupled (stated, not ranked)
- **D1 ↔ D3:** the concentration cap (D1) scales P&L in both directions; the sizing report frames it as a lever to pull only once expectancy is positive, which depends on the scorer (D3).
- **D3 ↔ #08 (Freeze):** changing `min_pass_score` (D3) resets the regime measurement's clock; freezing (#08) forbids the D3 change. They cannot both be exercised in the same window.
- **D3 ↔ #10 (Throttle):** ranked admission (#10 Option C) is only worth building if the score ranks — the same open question as D3.
- **D2 ↔ #07 (Regime) ↔ Q10:** per-strategy edge and regime attribution both depend on Q10, which is NOT DETERMINABLE at n=23 ~~and whose backfill is token-blocked~~.
  - **⚠️ CORRECTED 20-Jul-2026:** the backfill is **not** a regime blocker — the engine reads Kite live and issues 0 SQL on its compute path, and it **computed today** (271 daily bars, `status: OK`). What remains binding for #07 is the ~2.2 months of forward within-cell data and the unbuilt live consumer. *No claim is made here about D2's own dependency.* See `docs/audit/regime_computability_verification_20jul2026.md`.
- **#06 (PerformanceAllocator) ↔ D1:** ~~a size multiplier is masked by the concentration cap that binds 100% of trades~~ **← REFUTED 19-Jul** (`../audit/candle_retention_and_perfallocator_feasibility_19jul2026.md`; sweep `../audit/masking_premise_sweep_19jul2026.md`). `perf_weight` is a **post-cap multiplier** (applied after `raw_qty`, up to a 2× ceiling), so it is **not** masked — it changes final qty on **233/298 (78%)** over [0.5, 2.0]. #06's real gate is the multiplier's **merit (D2/D3)** + the **sizing/leverage** picture (where it genuinely touches D1), *not* the concentration cap. Conditional on today's unlevered sizing.

## Operator actions (not decisions)
These are things to *do*, not choices to make — see [ACTIONS_not_decisions.md](ACTIONS_not_decisions.md): the Q10 Part B backfill (token-blocked) and the standing security actions.

## Decidable-now briefs + the E4/W10 runbook (added 19-Jul)
**Triage of all ten** (which are answerable now vs gated, with each gate named): [`../audit/decision_readiness_triage_19jul2026.md`](../audit/decision_readiness_triage_19jul2026.md). Three turn only on Rama's judgement today — one-screen briefs (they do **not** supersede the full files):
- [`BRIEF_01_e4_w10_pnl_contract.md`](BRIEF_01_e4_w10_pnl_contract.md) · [`BRIEF_08_freeze_min_pass.md`](BRIEF_08_freeze_min_pass.md) · [`BRIEF_09_prune_retention.md`](BRIEF_09_prune_retention.md)
- [`RUNBOOK_e4_w10_deploy.md`](RUNBOOK_e4_w10_deploy.md) — the E4/W10 deploy, **prepared but NOT executed**; gated on decision 01 + observing Monday.
