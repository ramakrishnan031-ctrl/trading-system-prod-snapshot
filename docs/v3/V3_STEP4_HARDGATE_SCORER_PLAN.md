# V3 STEP 4 — 03.03 HARD-GATE EXTRACTION + 03.04 SCORER RE-SCALE (COUPLED) — PLAN ONLY

**Date (IST):** 11-Jul-2026 · **Status:** DESIGN / PLAN — **no implementation code written, no live behaviour changed, nothing committed.** · **Baseline:** `main`@`8116b74` (Steps 1-3 built-but-unpushed, all default-OFF). · **Reviewers:** Web Claude + ChatGPT. A separate Step-4b instruction will authorize implementation of the approved plan.

> Coupled because removing 2 of the 10 weighted steps changes the proportional denominator (`total = Σachieved / Σweights_present × 100`), so the gate extraction (03.03) and the scorer re-scale/re-calibration (03.04) MUST ship together behind ONE default-OFF flag with a proven parity gate.

---

## P0. VERIFIED CURRENT STATE (source-confirmed)

- **Scored steps** (`screening/step_executor.py::run_all`, 10 steps): `volume_surge 15, vwap_position 10, atr_filter 10, rsi_range 10, price_action 15, sector_strength 10, time_of_day 5, spread_check 5, circuit_check 10, signal_age 10` (`config/scoring_weights.yaml`, single source; Σ=100). Each step returns a raw 0.0–1.0. **circuit_check** (step 9) = 0.0 iff `md.circuit_state ∈ {upper_circuit, lower_circuit}` else 1.0 (binary). **signal_age** (step 10) = `≤30s→1.0, ≤60s→0.5, >60s→0.0`.
- **Scorer** (`screening/quality_scorer.py::score`): **proportional** — `total = round(Σachieved_present / Σweights_present × 100)`, capped 100; `min_pass_score 60`; tiers `high 80 / medium 65`.
- **Existing hard rejects around the score** (`screening/secondary_screener.py::screen`, order):
  1. MIS-blocklist pre-drop (flag-gated, dormant).
  2. **Pre-fill circuit-proximity reject** (`_circuit_proximity_reason`, L159/L355): rejects entries at/beyond the exit-clamp ceiling of the day's circuit band (uses `DEFAULT_CIRCUIT_MARGIN_PCT`) — this is a **band-proximity** reject, distinct from the `circuit_check` step's **at-circuit** test.
  3. 10-step executor → score.
  4. per-strategy `min_score` override vs `min_pass_score`, then `REJECTED_SCORE_<n>` if below.
  5. **signal_age defense-in-depth** (L275): `if step_results["signal_age"] == 0.0 → REJECTED_SIGNAL_AGE` (hard reject regardless of total).
- **Intake freshness** (upstream, `webhook_receiver._process_signal`): `EXPIRED` if `age > expiry_sec` (**600s**); `signal_processor._process_one` re-checks `> _signal_expiry_sec` (60s default). So a signal reaching the scorer is already ≤60s-ish and not intake-expired.
- **Persistence** (`secondary_screener._persist` → `state_store.insert_screener_result`): writes `score`, `tier`, `status`, **`step_results_json` (every per-step raw score, incl. circuit_check + signal_age)**, `latencies_json`, `market_data_snapshot_json`, `eligible_score` (the per-signal effective threshold at the time). **This is the parity-proof enabler (P4).**

**Net today:** circuit + signal_age ALREADY act as gates for the extreme cases (at-circuit is penalized in-score / proximity hard-rejected pre-fill; age=0 hard-rejected). Their *score contribution* only differentiates signals that already survived those checks. That is precisely why extracting them to a gate + re-thresholding can be near-parity — but not bit-exact (see P3).

---

## P1. EXACT SEAM — extract into ONE pre-scoring Hard-Gate (MODIFY the existing path)

**The single live seam** = `screening/secondary_screener.py::screen()` (the only live caller is `signals/signal_processor.py:778`; also `continue_from_gate`/`continue_from_retest` route through the same `screen()`). Scorer/executor/screener are constructed once in `main.py` (2447-2455).

**Extraction (root-cause ADAPT, no parallel path):**
- Introduce a NEW module `screening/hard_gate.py::HardGate` (the 03.03 battery). It is invoked INSIDE `screen()` **before** the scorer, at the point the pre-fill circuit reject already lives (step 2 above). The pre-fill `_circuit_proximity_reason` + the `circuit_check`/`signal_age` binary logic MOVE into `HardGate` — the circuit/age logic is **relocated, not duplicated** (delete the two step functions from the scored set; `_circuit_proximity_reason` becomes a gate rule; the L275 signal_age defense-in-depth becomes a gate rule).
- `step_executor` drops steps 9 & 10 from its `steps` list (8 remain). `scoring_weights.yaml` drops `circuit_check` + `signal_age` (8 weights, Σ=80). `quality_scorer` is unchanged in code (it derives step names from config) — it simply scores 8 steps.
- `HardGate.evaluate(signal_ctx) → GateVerdict(passed, reason, evidence)`; a fail short-circuits `screen()` to `REJECTED_<gate>` (same `ScreeningResult` shape/status vocabulary as today, so downstream is untouched).

**Why this is not a parallel path:** the gate sits at the exact spot the pre-fill circuit reject already occupies; the scorer keeps its identity and call site; only the step SET and the threshold CONSTANTS change. One code path, gated by the flag (P6).

---

## P2. RE-SCALE MATH (the crux)

Let **a8** = points achieved over the 8 KEPT steps (0–80), **age_c** = signal_age contribution ∈ {5 (age 30-60s), 10 (age ≤30s)}, and note **circuit_check = 10 (=1.0) for every gate-survivor** (an at-circuit signal is now gate-rejected, so it never reaches the scorer). Assume all 10/8 steps present (the proportional denominator = 100 old / 80 new; missing-step handling is unchanged and orthogonal).

- **OLD** total (survivor) `= (a8 + 10 + age_c)/100 × 100 = a8 + 10 + age_c`.
- **NEW** total `= a8/80 × 100 = 1.25 · a8`.

**Critical property:** re-weighting the 8 steps to sum to 100 (×1.25 each) is **scale-invariant** under proportional scoring (numerator and denominator both ×1.25) → it does **nothing** to the score. **Therefore the re-scale is achieved by re-calibrating THRESHOLDS, not by re-weighting.** Keep the 8 weights as-is (Σ=80); the score becomes "% of the 8-step battery achieved."

**Old→new score map for a survivor:** substituting `a8 = 0.8·new`:
`old = 0.8·new + 10 + age_c` ⇒ `new = 1.25·old − 12.5 − 1.25·age_c`.
- age ≤30s (age_c=10): `new = 1.25·old − 25`.
- age 30-60s (age_c=5): `new = 1.25·old − 18.75`.

**Worked examples (age ≤30s, circuit full):**

| a8 (of 80) | OLD = a8+20 | NEW = 1.25·a8 | old pass@60? | new pass@50? |
|---|---|---|---|---|
| 40 | 60 | 50.0 | PASS (=60) | PASS (=50) |
| 44 | 64 | 55.0 | PASS | PASS |
| 48 | 68 | 60.0 | PASS | PASS |
| 60 | 80 | 75.0 | PASS (high) | PASS |
| 38 | 58 | 47.5 | FAIL | FAIL |

The removed steps were ~"free" points for survivors (+20 at full), so the raw NEW score **de-inflates** relative to OLD; a straight threshold-hold at 60 would wrongly reject most live-passing signals. The fix is a threshold shift (P3), NOT a re-weight.

---

## P3. THRESHOLD RE-CALIBRATION FOR PARITY

Convert each OLD threshold to its NEW-score equivalent via the map above:

| OLD threshold | NEW equiv @ age≤30s (age_c=10) | NEW equiv @ age 30-60s (age_c=5) |
|---|---|---|
| min_pass 60 | **50.0** | 56.25 |
| medium 65 | **56.25** | 62.5 |
| high 80 | **75.0** | 81.25 |

**Exact single-threshold parity is IMPOSSIBLE** — because OLD folded `signal_age` (the 0.5 tier) INTO the pass/tier decision, and a single NEW threshold cannot reproduce a decision that depended on a now-removed input. The residual is the narrow band between the two age columns.

**Recommended calibration (the V3-intended semantics):** freshness is now a **binary gate** — once a signal is "fresh enough" (past the gate) it should NOT carry a scoring penalty for being 45s vs 25s old. So calibrate to the **age≤30s column** (the "no age penalty" reference):
- **min_pass 60 → 50 · medium 65 → 56 · high 80 → 75** (on the 8-step/Σ80 proportional scale).

Consequences, quantified in P4 against history:
- **age ≤30s survivors: EXACT parity** (pass + tier identical).
- **age 30-60s survivors: marginally MORE PERMISSIVE** — signals whose OLD pass/tier only failed because of the −5 age penalty now pass/upgrade. Band = a8 ∈ [40,45) for pass, [60,65) for high, etc. This is an intentional improvement, not a regression; must be quantified and accepted.
- **at-circuit signals (circuit_check=0 in OLD that still passed on score): now GATE-REJECTED** — the intended V3 semantic (don't trade a signal at its circuit). Expected count ≈ 0 (rare; the pre-fill proximity gate already removes most). Quantify.

Alternative conservative calibration (match age 30-60s: min_pass 56 / medium 62 / high 81) **never admits** a new signal but **drops** fresh signals in the flip band — presented to ChatGPT as the trade-off. **Final values MUST be data-fit (P4), not taken from this analytic derivation alone.**

---

## P4. PARITY PROOF METHOD (deterministic, offline — no live run)

Because `screener_results.step_results_json` persists every per-step raw score, the proof is a **pure offline recompute** over the historical `screener_results` corpus (VM `data_store/trading_system.db`, and PC nightly backups):

For every historical row:
1. Reconstruct OLD: `old_total`, `old_tier`, `old_pass` (already stored as `score`/`tier`/`status` — cross-check the recompute == stored to validate the harness).
2. Reconstruct the GATE outcome: would `HardGate` (circuit + age + proximity) reject it? (circuit_check==0 → reject; signal_age==0 → reject; proximity from the snapshot).
3. Reconstruct NEW: drop circuit+age, `a8 = Σ kept-step achieved`, `new_total = a8/80×100`, apply candidate thresholds (P3), get `new_tier`, `new_pass`.
4. Classify each signal: **UNCHANGED** / **FLIP-PASS** (old fail→new pass) / **FLIP-FAIL** (old pass→new fail) / **TIER-SHIFT** / **NOW-GATED** (old scored, new gate-reject).

**Acceptance:** for the candidate thresholds, report the exact counts + the specific signals in each non-UNCHANGED bucket, and confirm every FLIP/TIER-SHIFT is explained by the age-band / at-circuit semantics above (nothing unexplained). A build-gate test drives this recompute on a real backup and asserts the flip set ⊆ {explained residual}. This is the binding parity artifact ChatGPT/Rama sign off on before enforce.

**Live shadow proof (belt-and-suspenders, P6 shadow mode):** with the flag in `shadow`, the running system computes BOTH old and new for each live signal and logs the pair; after N sessions the live flip set is compared to the offline prediction (must match).

---

## P5. HARD-GATE MODULE DESIGN (full V3 battery; live-vs-shadow boundary)

`screening/hard_gate.py::HardGate.evaluate(ctx) → GateVerdict`. The module HOUSES the full V3 battery but each rule declares a **scope** (LIVE | PLAYBOOK-shadow) and a **missing-data rule**:

| Gate rule | Scope (current) | Missing-data rule | Source |
|---|---|---|---|
| **circuit (at-circuit)** | **LIVE** | fail-CLOSED if `circuit_state` present & at-circuit; absent band → admit (as today) | relocated `circuit_check` |
| **circuit-proximity** | **LIVE** | fail-OPEN on missing/non-numeric band (as today, `_circuit_proximity_reason`) | relocated pre-fill |
| **freshness (signal_age)** | **LIVE** | age>cutoff → reject; missing `triggered_at` → admit (neutral, as today's 0.5) | relocated L275 + step 10 |
| **liquidity** | **LIVE** (where a source exists; else no-op) | **fail-CLOSED** (no liquidity data on a must-have → reject) | spread/quote (existing) |
| **confirmation** | **PLAYBOOK-shadow** | **fail-CLOSED** | 03.01 structure |
| **valid pullback** | **PLAYBOOK-shadow** | **fail-CLOSED** | 03.01 |
| **R:R (from 03.01 levels)** | **PLAYBOOK-shadow** | **fail-CLOSED** | 03.01 (ANCHOR-only until swings validated) |
| **strong-HTF / regime preference** | **PLAYBOOK-shadow** | **fail-OPEN** (must-not-have) | 03.02 |
| **extreme (03.02 flag)** | **PLAYBOOK-shadow** | **fail-OPEN** (absence ≠ halt — matches 03.02) | 03.02 `extreme_flag` |

**Boundary rule:** on the **current live flow**, `HardGate` runs ONLY the LIVE-scope rules (circuit + proximity + freshness + liquidity) — i.e. exactly what gates today, just relocated. The PLAYBOOK-shadow rules are evaluated + logged only for signals on the V3-playbook path (shadow) and **never** reject a live order in this step. R:R uses **ANCHOR-only** levels (03.01 `confidence_class=ANCHOR_ONLY`) until swing validation flips it. Missing-data policy is explicit per rule: **must-have → fail-CLOSED** (confirmation/pullback/R:R/liquidity), **must-not-have → fail-OPEN** (strong-HTF/extreme).

---

## P6. DEFAULT-OFF FLAG (the safety spine)

Add `scoring.v3_hardgate_mode: off | shadow | enforce` (default **off**) — one flag, three states, at the `screen()` seam:
- **off (default):** `screen()` runs the EXISTING 10-step scorer with the EXISTING thresholds → **byte-identical** live path. `hard_gate.py` may exist but is not on the live path. The 8-step config is NOT applied. (Implementation keeps both the 10-step and 8-step step-sets available, selected by the flag, so `off` truly reproduces today.)
- **shadow:** run BOTH — (a) the OLD 10-step scorer decides the LIVE outcome (unchanged), AND (b) the NEW gate+8-step+re-thresholded path is computed and its `(pass, tier, gate_reason)` logged/persisted alongside. Compare old-vs-new per signal (P4 live proof). **Live decisions still follow OLD.**
- **enforce:** the NEW gate+8-step path decides the LIVE outcome; OLD kept computed-and-logged for one more soak window, then removed.

**Feasibility at the seam:** confirmed — `screen()` is a single method with a single live caller and a single construction site (`main.py`), and the scorer already derives its step set from config. A mode switch there is clean; no parallel screener object, no forked scorer class. The re-calibrated thresholds live in `scoring_weights.yaml` behind the flag (or a `scoring.v3_thresholds` block applied only in shadow/enforce), so `off` reads the original 60/80/65.

---

## P7. REGRESSION MAPPING (callers + side-effects + suite)

**Callers / construction:** `screen()` ← `signal_processor` (`_process_one` :778, `continue_from_gate`, `continue_from_retest`). Scorer/executor/screener built once in `main.py:2447-2455`. `scoring_weights.yaml` → `config_loader.ScoringConfig` → `QualityScorer` (only reader).

**Consumers of `screener_results.score` / thresholds (side-effects to check):**
- `reports/daily_trade_review.py` — reads `scoring.min_pass_score`/`high_score_threshold` from the **config snapshot** (L335/L1253) → auto-follows; **build-gate test asserts `min_pass_score=60`** (SYSTEM_MAP note) → **must update** when the value changes. Reads `screener_results.score`/`tier` for Orders/Dashboard/Strategies sheets — display only; historical rows keep their as-of score (no retroactive change).
- `reports/daily_report.py` — same read pattern.
- **GUI** (`ops_dashboard/backend/{services/strategy_tower.py, services/strategy_score.py, readers/db_reader.py}`) — reads score/tier for display; thresholds in `gui_config.yaml scorecard:` are the GUI's OWN (separate from the engine) — note but out-of-scope; the engine score they display simply changes scale under enforce.
- `core/config_auditor.py` — only scoring rule is **B4** (reject `tier_multipliers` in scoring_weights). **No** threshold-range/ordering rule today → re-calibration doesn't trip it. *Proposal:* add a NEW invariant `high > medium > min_pass` post-change (defensive), for ChatGPT.
- `state_store.insert_screener_result` / getters — schema unchanged (score is an INT column); no migration.

**Regression suite (before/after):**
- `tests/unit/test_step_executor.py` (10→8 steps; the circuit/age step tests move to `test_hard_gate.py`), `test_quality_scorer.py` (8-step proportional; thresholds), `test_secondary_screener.py`, `test_nocil_prefill.py` (proximity → gate), `test_mis_blocklist.py`, integration `conftest.py` scorer wiring, crash-test `ct_day2_isolated.py`.
- NEW `test_hard_gate.py` (each rule + missing-data policy + live/shadow scope) + the **offline parity recompute build-gate** (P4).
- `test_daily_trade_review.py` min_pass spot-check update; `test_config_auditor.py` if a new invariant is added.
- **flag-OFF proves byte-identity:** a test asserting that with `v3_hardgate_mode=off`, `screen()` output (score/tier/status) is identical to the pre-change code on a fixture corpus.
- Full-suite before/after with the **clean-tree vs dirty-tree stash-diff** (the Step 1-3 method) to prove zero *new* failures beyond the intended, updated tests.

---

## P8. RISKS + OPEN QUESTIONS (for Web Claude / ChatGPT)

1. **Final threshold values.** Analytic start = min_pass 60→**50**, medium 65→**56**, high 80→**75** (age≤30s reference). MUST be **data-fit** on the historical corpus to minimize decision flips. *Q: fresh-reference (recommended, slightly permissive on 30-60s) vs conservative (never-admit, drops some fresh)?*
2. **Exact parity is provably impossible** (age folded into the OLD pass decision). *Q: accept the quantified age-band residual as an intentional improvement?* (Recommended yes — freshness-as-gate is the design intent.)
3. **at-circuit-but-passed signals become gate-rejected.** Expected ≈0; *Q: confirm acceptable once quantified.*
4. **Freshness gate cutoff.** OLD hard-rejects age>60s (signal_age=0). *Q: keep 60s as the gate cutoff, or align to the 03.03 spec's freshness value?* (Phase-0 flagged the 90s-vs-60s-vs-600s ambiguity — needs a single authoritative number.)
5. **Re-weight is a no-op** under proportional scoring — confirm reviewers agree the change is threshold-only (keep 8 weights at Σ80), avoiding a pointless re-weight that could confuse future readers.
6. **GUI/report threshold displays** shift scale under enforce; confirm no downstream consumer hard-codes a 0-100 assumption that breaks (spot-checked: they display the stored int; OK).
7. **min_score per-strategy override** (`strategy.min_score`) is on the OLD 0-100 scale; under enforce it must be re-interpreted on the 8-step scale. *Q: re-scale per-strategy overrides too (map ×… ) or leave (they're mostly 0=use-global)?* — needs an inventory of non-zero `min_score` in `config/strategies/*.yaml`.

---

## ACCEPTANCE (plan)

Plan sections P1-P8 complete; premises source-verified; the extraction is a MODIFY-in-place of the single `screen()` seam (no parallel path, no forked scorer); the re-scale is **threshold-only** (re-weight is scale-invariant); exact parity is shown impossible with the residual quantified and made data-fit; the parity proof is a deterministic offline recompute from persisted `step_results_json` + a live shadow compare; the whole change sits behind a default-OFF `v3_hardgate_mode` (off→shadow→enforce). **No code written, no config changed, no live behaviour altered, ledger untouched.** Ready for Web Claude + ChatGPT review; Step-4b will implement the approved values.
