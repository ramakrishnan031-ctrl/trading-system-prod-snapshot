# E4/W10 — **DEPLOYED** 20-Jul-2026 22:16 IST

**`fm_ledger.pnl_delta` is NET. The daily-loss reader stops double-subtracting costs.**
Deployed at **`a266432`**, tag **`deploy-20jul-e4-w10-pnl-contract`**. `PC == origin == VM bare`.
Schema v44 both sides, no migration. Service left **`inactive`** — it restarts itself after
tomorrow's 08:15 token refresh.

Decision 01 / Option A, approved by Rama. Second attempt: the 20-Jul-evening attempt was stopped at
the regression gate (`e4_w10_deploy_stopped_20jul2026.md`); the blocker was the M-C1 live-seed safety
argument, re-derived and ChatGPT-approved (`mc1_live_seed_rederivation_20jul2026.md`).

**⚠️ Nothing tonight proves the fix works in production. The verification is TOMORROW — see §F.**

---

## A. The sequencing that de-risked this: C3's replacement shipped FIRST, on main

The riskiest edit was C3 — the test whose false premise stopped the last deploy. It was **removed
from the migration entirely** by observing that its replacement is **contract-agnostic**: it pins
structural facts that hold identically before and after E4/W10. So it was written, verified and
pushed **on main at `e8313f0`, before the merge existed**.

**Removed:** `test_the_reader_is_a_different_quantity_and_is_not_involved`. It asserted
`reader != carryover` — a **numerical proxy** for the structural claim "the reader is not involved".
Never equivalent: the gap merely equalled `Σcosts`, a *consequence* of the old contract. A naive
inversion to `reader == carryover` would have been wrong too — post-E4/W10 the two are equal **during
the session** but differ **after the EOD reset**, since the reader has no `entry_type` filter.

**Added, pinning the structural reasons:**

| Test | Pins | Falsifier |
|---|---|---|
| `test_the_reset_pnl_row_cannot_reach_the_cancellation` | `reset_daily_pnl` writes `RESET_PNL.pnl_delta = −reader(today)` — the sole causal edge from reader into ledger — and `_today_release_used_pnl_rows` filters `entry_type='RELEASE_USED'`, so it is invisible to both sides | **#2, load-bearing** |
| `test_the_rehydrate_reader_call_is_observational_only` | `rehydrate_from_open_trades:1736` *does* call the reader (correcting §A2's false claim), but only as a log field | **#3** |

### ⭐ Verified able to FAIL, not merely observed green
A test that cannot fail is not evidence — which is exactly how the old C3 went wrong. Both were
plant-tested in the main tree and restored:

| Plant | Break | Result |
|---|---|---|
| **A** | helper widened to `entry_type IN ('RELEASE_USED','RESET_PNL')` | **Only** the reset test failed, on its *intended* assertion (all three anti-vacuity guards passed first): *"carryover moved −18094.5400 → 94.5400 after an EOD reset wrote pnl_delta=18189.0800"*. The residue **94.54 is exactly `Σcosts`** — the reader's cost term visibly leaking into the carryover |
| **B** | `self._total += daily_pnl` at `:1736` | **Only** the observational test failed: *"_total came out −999528765.4400 instead of broker.net=471234.5600"* |

After restore: `capital/` shows **0 modified files**; 14 passed. The file header's false §A2 paragraph
was corrected in place, and the **four falsification conditions** now live in `TestSharedHelper`'s
docstring (#2 marked load-bearing; #4, the midnight day-floor hazard, flagged pre-existing and
deliberately not pinned here).

---

## B. The merge and the two migration flips

`git merge --no-ff e4-w10-pnl-contract` → **ZERO conflicts**, second time running. `main.py`,
`PATHS.md`, `docs/SYSTEM_MAP.md` all auto-merged; **no capital-path file conflicted.**

**C1 — `test_reader_equals_independent_ground_truth`: strict `xfail` removed.** Worth stating
plainly: **that marker did exactly what Q9 batch 3 built it for.** Its `reason=` named this branch by
commit and said `strict=True` would turn the XPASS into a failure "so the migration is deliberate."
It XPASSed on the first merge, was reported as a failure, stopped that deploy at the gate, and forced
this flip to be a decision rather than an accident.

**C2 — renamed `test_the_double_counted_costs_discrepancy_is_gone_and_stays_gone`, re-aimed rather
than merely inverted.** A bare `reader == truth` would have duplicated C1 and quietly dropped
requirement (4). Its new job guards the failure mode **E4/W10 itself creates**:
`round_trip_costs_or_zero` is **FAIL-OPEN** — a cost-calculation failure degrades to `0.0` rather than
raising. A silent, systematic degradation would make `costs == 0`, `reader == truth` hold **trivially**,
and the suite stay green while cost accounting was dead. So it asserts:
1. `costs > 0` **first** (anti-vacuity + fail-open guard);
2. `delta == 0`;
3. `delta != −Σcosts` — so a *returning* double-subtract is named by its signature, not merely
   reported as "non-zero".

Merge and both flips landed in **one commit** (`a266432`); neither alone leaves the suite green.

---

## C. The gate — same-window double regression

Method per the corrected standing rule: **no fixed-number baseline.** Fresh BASE and MERGE runs in the
same session and window; `comm -23` on the failure **sets**.

| | BASE `e8313f0` | MERGE `a266432` |
|---|---|---|
| failed | 33 | **32** |
| passed | 4964 | 4986 |
| skipped | 4 | 4 |
| **xfailed** | **1** | **0** |
| collected | 5002 | **5022** |
| wall clock | 792.78 s | 798.13 s |

**The arithmetic reconciles exactly, as predicted in advance:** `xfailed` 1 → 0 (C1's marker removed),
collected **+20** (the E4/W10 test file's 20 tests — not the 19 the runbook claimed).

### ⭐ `comm -23` MERGE-ONLY = **0**. The gate passes.

### `comm -13` BASE-ONLY = 1 — investigated, not waved away
`test_instance_lock.py::TestSingleInstanceAcrossProcesses::test_p2_restart_after_crash_is_not_blocked`
failed in BASE and passed in MERGE. An unexpected *fix* is as interesting as an unexpected break, so
it was checked two ways rather than assumed:

1. **Structurally**: E4/W10's entire `main.py` diff is **two lines**, both
   `cost_calculator=cost_calculator` kwargs. Nothing that could touch instance locking.
2. **Empirically**: run 5× on the *same merged tree*, no code change between runs →
   **1 failed, 2 failed, 2 failed, 2 failed, 2 failed.** The count varies run-to-run.

⇒ Run-to-run flakiness in a PC-only concurrency test, not a fix attributable to the merge.

---

## D. Parity (§D2) — re-measured on the merged tree, not inherited

| Check | Result |
|---|---|
| Mode conditionals in the five merged capital files | **none** — the sole grep hit is a docstring at `order_reconciler.py:2988` reading *"ONE code path, no `if paper` branch"* |
| `round_trip_costs_or_zero` signature | **no mode parameter** — structurally incapable of branching |
| `CostCalculator` construction vs the mode branch | built `main.py:1858`, branch at `:1860` ⇒ **built before**, one instance to both modes |
| Reader `get_daily_realized_net_pnl` | **0** mode branches |

**One calculator built pre-branch, one reader, no conditionals ⇒ paper and live are affected
identically.**

---

## E. Deploy record

| | |
|---|---|
| Commit / tag | **`a266432`** / `deploy-20jul-e4-w10-pnl-contract` |
| Identity | PC == origin == VM bare == `a266432` |
| VM carries the change | `round_trip_costs_or_zero` present in `broker/cost_calculator.py`; the new reader contract present in `core/state_store.py` |
| post-receive | **`crontab AUTO-INSTALLED from canonical`** printed |
| Service | **`inactive`** — not started; restarts itself after the 08:15 token refresh |
| Artifacts (pre == post, byte-identical) | `trading_system.db` `558e0091697eb075` · `forward_shadow_fs-v1.jsonl` `76b647311f268c83` · `analytics.db` `e849802b43cc6542` |
| Backup retained | `data_store/backups/pre_deploy_e4_w10_20260720.db`, 96,030,720 B, `quick_check=ok` |

Artifacts unchanged is the expected result: a code checkout with the service down touches no data.

⚠️ **Unintended side effect, recorded:** `git push origin --tags` also pushed a pre-existing local tag
`phase-a-pre-spine-fix`. Harmless (a tag changes no tree), but it was not intended by this batch.

### 🔴 ROLLBACK — written down before it is needed
```bash
git revert -m 1 a266432 && git push origin main
```
Schema-free (v44 both ways), no data migration, **nothing to unwind in the ledger** (the daily-loss
control is per-day and zeroed nightly by `RESET_PNL`). Elapsed: a single push + checkout — minutes.

---

## F. ⚠️ The verification is TOMORROW — nothing tonight proves it

The observable that proves the reader now returns NET is written by the **nightly EOD reset**, so the
**first observation is tomorrow's**:

> **`RESET_PNL.pnl_delta` must equal `−Σpnl_delta` over the day's non-RESET rows — NOT
> `−(Σpnl_delta − Σcosts)`.** The two differ by exactly `Σcosts`.

On 20-Jul's numbers that is **18.29** rather than **19.61**. Checklist **A3 has been flipped** to make
this the GOOD condition and the old shape the defect, so tomorrow's run checks the right thing.

⚠️ **Two same-day caveats, both expected and harmless:**
- 20-Jul's own `RESET_PNL` row was written by the **old** code before this deploy. It is **19.61** and
  stays 19.61. **Do not flag it.**
- Reading 20-Jul under the new reader returns `Σcosts` = **+1.32** rather than 0. A small **positive**,
  so it cannot cause a spurious daily-loss breach (runbook §7 — the reason a post-reset window was the
  clean time to deploy).

---

## G. Queue additions from this work

1. **The midnight day-floor hazard.** The seed (`main.py:2257`) and `rehydrate_from_open_trades` each
   derive their own `start_of_today_iso` from their own `now_ist()`; a seed/rehydrate pair straddling
   midnight breaks the cancellation. **Pre-existing and contract-independent** — unchanged by this
   deploy. Fix: compute the floor once, pass it to both.
2. **Re-label the three W10 workarounds.** `ops_dashboard/backend/api/risk_capital.py:38`,
   `ops_dashboard/backend/services/capacity.py:16`, `reports/daily_trade_review.py:1053` each avoid the
   reader *because* it double-subtracted, all marked "approved permanent". Post-fix they **agree** with
   the new reader rather than conflict — not a defect, but their stated rationale is now false, which is
   the attribution-gloss shape. Re-label.

---

*Off-market, book flat, service never started. The deploy is a code checkout; no data was written.*
