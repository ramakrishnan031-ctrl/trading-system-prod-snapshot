# V3 STEP 4b — BINDING PARITY ARTIFACT (Hard-Gate + scorer re-scale, OFF→enforce)

**Date (IST):** 12-Jul-2026 · **Status:** BINDING parity proof — read-only, no live change, `scoring.v3_hardgate_mode` stays **OFF**, nothing pushed. · **For:** Web Claude + ChatGPT + Rama sign-off BEFORE any off→shadow flip.

**Verdict: PARITY_OK — zero unexplained flips.** The OFF→enforce change preserves the pass/tier decision on **99.90%** of a month of real signals; the 0.10% that move are ALL the intended age-band admissions + a negligible integer-rounding edge.

---

## Corpus + method

- **Corpus:** 114,807 `screener_results` rows, range **2026-06-12 → 2026-07-10**, from the VM daily backup `data_store/backups/trading_system-2026-07-11.db` (a slim read-only extract of the needed columns was pulled to a local copy; the live/production DB was never touched).
- **Tool:** `scripts/v3_hardgate_parity_recompute.py` (in the dev work-tree; built-but-unpushed — that is why it wasn't on the VM where it was first looked for; the local dev DB has 0 screener_results rows, so it is not the data source).
- **Method:** for every row, reconstruct OLD (today's OFF path: full 10-step proportional total at 60/80/65 + the signal_age defense-in-depth) and NEW (enforce: Hard-Gate → 8-step re-scaled total at the v3 thresholds), both at the CURRENT config, and classify UNCHANGED / FLIP_PASS / FLIP_FAIL / TIER_SHIFT / NOW_GATED. The recompute reads the persisted per-step raw scores (`screener_results.step_results`), so it is deterministic and needs no live run.
- **Harness cross-check:** the recomputed OLD full-total equals the stored `score` on ALL scored rows (0 mismatches) — the recompute mirrors the engine's own proportional formula.

## Result — fitted thresholds (8-step scale): **min_pass 50 · medium 56 · high 75**

The data-fit sweep independently selected exactly the plan's fresh-reference thresholds (the flip-minimizing, zero-unexplained point).

| Classification | Count | Share | Explanation |
|---|---|---|---|
| **UNCHANGED** | **114,693** | 99.90% | identical pass + tier |
| **FLIP_PASS** | **104** | 0.091% | **age-band (signal_age==0.5)** — 30-60s signals OLD score-rejected only via the −5 age penalty; freshness is now a binary GATE so they pass on merit (A2-accepted intended improvement) |
| **FLIP_FAIL** | **10** | 0.009% | **rounding-boundary** — `old_total==60` exactly (a8≈39.55; 59.5 rounds UP to pass) while the ÷80 re-scale rounds to 49; inherent ÷100-vs-÷80 integer-rounding sliver, all fresh (age=1.0) non-circuit |
| TIER_SHIFT | 0 | — | none |
| NOW_GATED | 0 | — | none (no at-circuit signal ever passed OLD scoring in this corpus) |
| **UNEXPLAINED** | **0** | — | **the flip set is a strict subset of the explained residual** |

**PARITY_OK = True.**

## The two residual categories (both accepted / understood)

1. **Age-band (104 FLIP_PASS).** These are the design intent, ratified as A2: once freshness is a gate, a 45-second signal should not carry a scoring penalty vs a 25-second one. Every one is a signal that was fresh-enough (gate passes) and clears the re-scaled bar on the other 8 steps. Sample: `old=REJECTED_SCORE_57 → new=53/PASS [age0.5]` (and some `old=PASSED/55` under the historical 55-era min_pass, which correctly REJECT at today's 60 and re-admit at the v3 50 — same net "admit a fresh marginal signal").
2. **Rounding-boundary (10 FLIP_FAIL).** A precise, tiny (0.009%) artifact where OLD rounds `59.5→60` onto the pass line while the different NEW denominator rounds `49.4→49` just under. It exists at any integer threshold (the boundary moves with it) and is inherent to two proportional scales both `int(round(...))`-ed. All 10 are `old_total==60`, a8≈39.5, age=1.0, circuit=1.0.

## Two tool bugs found + root-fixed during this run (the recompute was wrong before)

1. **Column name.** The tool read `step_results_json`, but the `screener_results` COLUMN is `step_results` (the persist PARAMETER was `step_results_json`). `screener_results` also has no `direction` column, so the circuit-proximity re-derivation was dropped from the gate reconstruction — correct, because proximity is enforced PRE-scoring (any row with `step_results` already passed it), so it contributes zero flips; the only NOW_GATED source is at-circuit.
2. **Baseline threshold.** The tool first used the stored `status` as OLD — but `min_pass_score` was **55 from 06→10-Jul** (dropped 06-Jul, restored to 60 on 10-Jul), so ~23k rows scored 55-59 are stored `PASSED` yet would reject at today's 60. Fixed to RECOMPUTE OLD at the current 60/80/65 (the OFF-today baseline). This cut false FLIP_FAILs from 23,299 → 10.

Both fixes are covered by new tests in `tests/unit/test_v3_hardgate_parity.py` (recompute-at-current-config + rounding_boundary; 8 pass).

## Reproduce

```
# on a machine with the Step-4b work-tree + a screener_results DB backup:
python scripts/v3_hardgate_parity_recompute.py --db <backup.db>
```

## Sign-off gate

This artifact is the binding acceptance gate (G2). It confirms A1 (50/56/75 fresh-reference), A2 (age-band residual accepted), and A3 (no at-circuit surprises). **Nothing flips until Web Claude + ChatGPT + Rama sign off.** After sign-off (Rama, off-market): push → flip `v3_hardgate_mode` off→shadow (live soak compare) → enforce.
