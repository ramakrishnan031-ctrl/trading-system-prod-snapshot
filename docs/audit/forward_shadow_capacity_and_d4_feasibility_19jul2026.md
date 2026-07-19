# Forward-shadow capacity + D4 feasibility (READ-ONLY, 19-Jul-2026)

**Two questions.** (A) Will the forward shadow keep growing — it is now the sole confirmation path for D3, D4, #10 and (via D3) D1? **Yes; nothing blocks Monday's append.** (B) Can D4 (alternative exit policies) actually be tested from persisted data, or was it misclassified the way D3 was? **It is *partially* testable — genuinely more than D3, because the out-of-sample 1-min candles survived — but the binding constraint is power, not data, and at 19 out-of-sample entered trades the traded-book D4 questions cannot be answered.**

**Deploy state:** PC == origin == VM bare == `2687778`; tag `deploy-19jul-consecutive-losses` → `d271525`; schema v44. System DOWN; book flat. The `scripts/forward_shadow_record.py` recorder was **not run** — the JSONL is byte-identical afterwards; the snapshot was queried `mode=ro`; the live `analytics.db` was read `mode=ro` (sha unchanged). Both artifacts proven untouched (end).

*No recommendation on D4, D3, D1 or #10. All are Rama's.*

---

## A. Will the forward shadow keep growing? — verified, nothing blocks Monday

| check | finding |
|---|---|
| **A1 cron installed** | `15 18 * * 1-5` (18:15 Mon-Fri) → `venv/bin/python scripts/forward_shadow_record.py`; `config/cron_registry.yaml:346-364` (`monitored: true`, `market_day_only: true`, `enabled: true`). Script present. Run via the interpreter, so the missing +x bit is irrelevant. |
| **A2 failure behaviour = LOUD** | registry `:349-350`: *"fail-safe & fail-LOUD (a crash raises a Telegram sentinel)."* The cron writes `data_store/cron_marks/forward_shadow_record.done` with `$rc`; `monitored: true` + the marker means the cron officer flags a non-zero rc. **It does not fail silently.** |
| **A3 output path / disk / locks** | `data_store/v3/` is writable (ubuntu:ubuntu); **72 G free / 25% used**; no `.lock`/`.tmp`/partial files (only `forward_shadow_fs-v1.jsonl` + a separate `would_be.jsonl`). |
| **A4 idempotency** | **Idempotent per (date, signal_id)** — `main:151-160` loads the JSONL, builds `seen` = signal_ids already present *for that date*, and skips them; empty → `return 0`. A re-run for a present date appends nothing. |
| **A4 contamination vector** | The idempotency guards re-runs of the **same** date. It does **not** guard a `--date <not-yet-present>` backfill: running the recorder for an old (in-window) date would append genuine records and **mix in-window dates into the out-of-sample file**. Its own header says a written record must never be mutated *"or the out-of-sample evidence is void."* **This is exactly why the recorder must never be run manually.** |
| **A5 what would stop Monday's append** | Nothing, provided the system trades Monday (populates `screener_results`) and the Kite token is present (08:15 TOTP refresh on a trading day). Without the token the recorder still appends but with `sim_R=None` (`_build_kite` → *"recording score/decision only"*) — a degradation, not a stop. **Monday would be out-of-sample day 4.** *(Reported, not fixed — a cron/script change is Rama's.)* |

---

## B. ⭐ Can D4 be tested from persisted data? — partially, and it differs from D3

### B1. The D4 claims, verified against persisted data (not inherited)

| claim (inherited) | verified? |
|---|---|
| no exit-management engine has ever fired | ✅ **confirmed, stronger**: `sl_trail_count > 0` on **0 of all 361 trades** (the record's "0/134" was a subset); `smart_tgt_state` empty. |
| `order_protocol` is dead config → LIMIT_TRIPLE | ✅ **confirmed**: `order_protocol` = LIMIT_TRIPLE on **361/361**; `entry_mode` = FULL on 361/361. |
| winners' true MFE ~2.93R vs the 1.5R exit | ⚠️ **not reproduced** — see B2/C. |
| BE-after-0.5R ≈ −0.010R; config-only ≈ −0.117R | ⚠️ **not re-verifiable on its own data** — see B2. |

### B2. The feasibility verdict — the central output

D4 is about **alternative** exit policies, which need the **price path after entry**, not just the outcome under the current policy. What persisted:

- **Forward-shadow JSONL** — holds only the **final `sim_R` under one fixed policy** (`:217-224`: no path, no MFE, no MAE). ⇒ cannot test alternative exits.
- **`trade_excursions` (109 rows)** — MFE/MAE **for traded trades**, but the MFE is **capped by the current exit** (a winner that hits the +1.5R TGT exits there): measured winners' MFE = **1.56R all-era**. This cannot answer "money left on the table," which needs the *uncapped* path.
- **1-min candles — SURVIVE (the key difference from D3).** Not in the DB snapshot (they live in the ATTACHed `analytics.db`, which the snapshot did not preserve — a preservation gap worth noting), but present in the **live `analytics.db`**: 07-13 3,750 / 07-14 23,165 / 07-15 34,298 / 07-16 11,962 candle rows. **So the full post-entry path IS reconstructable read-only for 13-16 Jul.**
- **The 13-Jul exit study's per-trade sims were NOT persisted** (aggregate table only) — the **same standing constraint** as the band study. And there are **no 1-min candles before 13-Jul**, so the 2.93R claim **cannot be recomputed on its own window**.

**Verdict:** D4 is *partially* testable out-of-sample — **more than D3** (its OOS path data survives). **But the binding constraint has flipped from data (D3) to power (D4):** only **19 entered trades exist out-of-sample (07-14/15/16), 9 of them winners.** That is far too few to confirm or refute any exit-policy effect. The larger *signal-population* counterfactual (simulate alternative exits on all OOS signal-paths via the candles) is feasible read-only but (a) re-implements the exit-sim harness — with a real risk of diverging from `simulate_true_path` and answering a subtly different question — and (b) answers a counterfactual-entry question, not the traded-book decision D4 poses.

### B3. Precisely what is testable, and what is not

| D4 question | testable from persisted data? |
|---|---|
| Has any exit engine ever fired? Is `order_protocol` dead? | ✅ **yes — facts, verified above.** |
| Actual (capped) MFE/MAE of the traded book | ✅ yes (`trade_excursions`), but tiny-n out-of-sample. |
| Uncapped full-path MFE ("2.93R") of winners | ⚠️ **computable for 13-16 Jul only** (candles), at n=9 OOS winners — **underpowered** (§C). Not computable pre-13-Jul. |
| Alternative exit policies (BE-after-0.5R, etc.) vs current | ⚠️ **feasible in principle** from the surviving candles, but requires a faithful re-run of the exit-sim harness; traded-book n is far too small; the signal-population version is a different (counterfactual) question. |
| Re-slice the 13-Jul exit study | ❌ **no** — per-trade sims not persisted; no pre-13-Jul 1-min candles. |

---

## C. The test that IS feasible, run with the D3 discipline — power first

**Power first (§C2).** The traded out-of-sample book is **19 entered / 9 winners**. No exit-policy or MFE claim can be distinguished at this n; the honest answer is up front: **cannot distinguish.** Reported anyway, with intervals-by-instability rather than false precision.

**Uncapped full-path MFE (from `analytics.db` candles), genuine OOS 07-14/15/16** (07-13 shown separately as in-window):

| population | n | winners' median MFE | winners' mean MFE |
|---|---:|---:|---:|
| **OOS (14-16 Jul)** | 19 (9 win) | **1.44R** | 1.75R |
| + 07-13 (in-window) | 28 (12 win) | 1.52R | 3.63R |

- **The "~2.93R median winners' MFE" claim does not reproduce out-of-sample** — the OOS median is **1.44R**, near the current 1.5R TGT, not 2.93R. But the estimate is **unstable to the point of meaninglessness at this n**: adding a single in-window day swings the *mean* from 1.75R to 3.63R (skew + tiny sample). **This is a "cannot distinguish," not a refutation** — the opposite of D3, where power was adequate.
- **Losers' uncapped MFE (mean 1.78R OOS) ≈ winners' (1.75R)** — the max favorable excursion does not separate winners from losers out-of-sample, a faint "gave-back-the-gain" signature that would favour a breakeven/trailing exit *if it held* — but at n=10 losers it does not hold up as evidence.
- **Negative control (§C3):** a permutation control does not cleanly apply to a per-trade max-excursion point estimate (there is no group contrast to shuffle); the binding issue is n, not a broken pipeline. The `trade_excursions` cross-check (capped winners 1.56R, consistent with exiting at the 1.5R TGT) confirms the measurement is sane.
- **Robust vs fragile (§C4):** the *facts* (no engine, dead config) are robust and verified; every *quantitative* exit claim is fragile at this sample and should not be built on.

---

## D. Forward-shadow capacity — how long until each question is answerable

Growth is one market day per day the system runs. Observed OOS rates: ~1,450 scored signals/day; ~6 entered trades/day; ~3 winners/day; 60-65-band ~54/day.

| question | within-regime days needed | note |
|---|---|---|
| **D3 — band relationship** | already ~powered at 3 days (it did *not* replicate); a few more firm the point estimate | the pooled contrast needed ~2-4 days; 3 exist |
| **D4 — traded-book exits / MFE** | **~17 trading days for ~50 winners; ~34 for ~100** (at ~3 winners/day) | power-bound, not data-bound |
| **#10 — does the score rank?** | answered *negatively* so far (M-S4 rho +0.003 + D3's OOS non-replication); more days tighten, won't flip the sign within-regime | |

**⭐ D2 — what more days CANNOT answer.** Both the discovery window and every out-of-sample day so far are **single-regime**. A **cross-regime** verdict — does any of this generalise beyond the current market — needs a **regime change**, which is not a number of trading days and cannot be scheduled. No amount of forward-shadow accumulation converts a single-regime answer into a cross-regime one.

**⭐ D4 — the triage's reliability.** Of the three items the §C triage called **COMPUTABLE NOW**, **two have now proved otherwise for opposite reasons**: **D3** was not computable (the per-signal basis was never persisted), and **D4** is computable but **underpowered** (the data survives; the sample does not). The label conflated "the data exists" with "the question is answerable." The remaining one — **PerformanceAllocator** — should therefore be treated as *unverified until its data and power are checked*, not as computable-on-demand; and it is already deferred pending the sizing/leverage picture. **This is a statement about the triage, not about any decision.**

**Decision-file + triage updates:** `05_d4_exit_policy.md` and the index are updated neutrally — D4 reclassified **COMPUTABLE NOW → PARTIALLY COMPUTABLE / UNDERPOWERED (NEEDS THE SYSTEM RUNNING)**.

---

## PROOF OF READ-ONLY (both artifacts)
- `scripts/forward_shadow_record.py` **not run** in any form. Forward-shadow JSONL: **7,827 lines, mtime 2026-07-16 18:15** before and after.
- Live DB **before/after:** sha256 `6df0c09a…`, mtime `2026-07-19 11:14:29`, size `89,968,640` — identical. Live `analytics.db` sha `bb229f44…` identical before/after each read (`mode=ro`).

*Docs-only. No code, config, schema or flag changed. D4 remains Rama's, and unresolved.*
