# Open Decisions — Index

Assembled 19-Jul-2026 so each of Rama's open choices sits in one place with the **current, corrected** evidence, instead of scattered across ~a dozen audit reports written over three weeks. **Every file states options, evidence for each, what is unknown, the exposure in each direction, and what would settle it — and recommends nothing.** The order below is by file number (a label), not by priority.

Deploy state at assembly: PC == origin == VM bare == `3dda9f7`; code tag `deploy-19jul-consecutive-losses` → `d271525`; schema v44. System DOWN; book flat.

## The decisions

| # | Decision | Type | Status | What blocks / gates it |
|---|---|---|---|---|
| [01](01_e4_w10_pnl_contract.md) | **E4/W10** — the `pnl_delta` contract (daily-loss input) | capital-posture | OPEN — built, tested, **unpushed** (`e4-w10-pnl-contract`@`ad34ee4`) | a posture sign-off; deploy also needs a manual flatten first |
| [02](02_d1_concentration_sizing.md) | **D1** — the concentration cap (sizing) | capital-posture | OPEN | coupled to D3 — the cap is a lever on a book whose sign is unknown |
| [03](03_d2_strategic_direction.md) | **D2** — strategic direction / strategy mix | strategic | OPEN | a positive-control baseline + regime attribution (Q10, token-blocked) |
| [04](04_d3_min_pass_threshold.md) | **D3** — the `min_pass_score` threshold (band inversion) | strategic (edge) | OPEN | tension with FREEZE (#08); backtest vs real-book point opposite ways |
| [05](05_d4_exit_policy.md) | **D4** — exit policy | strategic (edge) | OPEN | out-of-sample test; the edge question routes to M-S4 |
| [06](06_performance_allocator.md) | **PerformanceAllocator** — wire it or leave `perf_weight ≡ 1.0` | sizing mechanism | OPEN | likely masked by the concentration cap (reachability algebra open) |
| [07](07_regime_enable.md) | **Regime** — enable / build Phase 1 / leave | strategic | OPEN | Q10 (~2.2 months + token); do-not-flip-mid-soak constraint |
| [08](08_freeze_min_pass_during_measurement.md) | **Freeze `min_pass_score`** during the measurement? | measurement hygiene | OPEN | downstream of Regime (#07) and D3 (#04) |
| [09](09_prune_retention.md) | **Prune retention** & status-selectivity | data-retention | OPEN — **new** (census §A4) | Rama's intent on whether rejection-composition is a recurring need |
| [10](10_entry_throttle_admission.md) | **Entry-throttle admission** — arrival-order vs ranked | strategic / mechanism | OPEN — **new** (throttle analysis) | ranking value coupled to D3; **counter-case is strong** |

## How they are coupled (stated, not ranked)
- **D1 ↔ D3:** the concentration cap (D1) scales P&L in both directions; the sizing report frames it as a lever to pull only once expectancy is positive, which depends on the scorer (D3).
- **D3 ↔ #08 (Freeze):** changing `min_pass_score` (D3) resets the regime measurement's clock; freezing (#08) forbids the D3 change. They cannot both be exercised in the same window.
- **D3 ↔ #10 (Throttle):** ranked admission (#10 Option C) is only worth building if the score ranks — the same open question as D3.
- **D2 ↔ #07 (Regime) ↔ Q10:** per-strategy edge and regime attribution both depend on Q10, which is NOT DETERMINABLE at n=23 and whose backfill is token-blocked.
- **#06 (PerformanceAllocator) ↔ D1:** a size multiplier is masked by the concentration cap that binds 100% of trades.

## Operator actions (not decisions)
These are things to *do*, not choices to make — see [ACTIONS_not_decisions.md](ACTIONS_not_decisions.md): the Q10 Part B backfill (token-blocked) and the standing security actions.
