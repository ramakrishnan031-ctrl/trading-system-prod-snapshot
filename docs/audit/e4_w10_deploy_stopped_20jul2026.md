# E4/W10 deploy — ATTEMPTED 20-Jul-2026, **STOPPED AT THE REGRESSION GATE**

**Outcome: NOT DEPLOYED. Nothing was pushed. `PC == origin == VM bare == 80fbe86`, unchanged.**

Decision 01 / Option A was approved and the window was clean. The merge itself was flawless — **zero
conflicts**. The gate stopped it: the full regression on the merge result produced **3 failures
attributable to the merge**, and they are not incidental. Two are detectors purpose-built by Q9
batch 3 to fire exactly when E4/W10 lands and to force "a deliberate flip as part of that migration".
The third is substantive: **it invalidates the stated basis of a separate capital-path safety
argument** (the M-C1 live-seed cancellation) and its own failure message says *"re-derive §A2"*.

Per the standing instruction — *anything beyond the baseline, stop and report; do not triage, do not
proceed* — the deploy stopped there. **Stopping cost nothing: N=0 of 23 days, and the change has never
altered a result.**

---

## A. Pre-flight (§A1) — all recorded

| check | result |
|---|---|
| Service | `inactive` — the designed 16:00:04 `eod_self_exit` |
| **Book flat** | **Yes.** Every trade in `trades` is terminal — the full status set is `FAILED`/`CLOSED`/`CLOSED_MANUAL`/`REJECTED`/`CANCELLED`; **there is no `OPEN` status at all**. Today: 3 `CLOSED` + 1 `CLOSED_MANUAL`, all with `exit_time` set |
| Orders | all terminal — `COMPLETE` 313, `CANCELLED` 309; none pending |
| **Rama's manual-flatten precondition** | **Satisfied by the session being over**, every position terminal and the service down. Stated explicitly rather than skipped, per the runbook's instruction not to assume it |
| Kill state | `SOFT_KILL` / `circuit_breaker_force_close_15:15` @ 15:15:00 — the **correct** post-cutoff state per checklist A5, not a fault. Auto-clears at tomorrow's 08:15 boot (prior-day rule) |
| **Today's EOD reset written** | **Yes** — `fm_ledger` `ledger_id 9212`, 15:17:05, the runbook §7 "deploy after a reset is clean" precondition |
| DB backup | `data_store/backups/pre_deploy_e4_w10_20260720.db`, 96,030,720 B, **`quick_check=ok`**, row parity verified on `trades`/`fm_ledger`/`orders` (369/2189/622) |
| Identity | `PC == origin == VM bare == 80fbe86` |
| Artifacts (pre) | `trading_system.db` `558e0091697eb075` · `forward_shadow_fs-v1.jsonl` `76b647311f268c83` · `analytics.db` `e849802b43cc6542` |

⚠️ **One self-caught error worth recording.** My first "book flat" query returned **202 open trades** —
it used `status NOT IN ('CLOSED','CANCELLED','REJECTED')`, which silently counted `FAILED` (164) and
`CLOSED_MANUAL` (38). The premise of my own query was wrong, not the book. Checking the actual status
distribution before believing the number is what caught it.

### The pre-deploy signature, measured (baseline for §C)
| date | Σ`pnl_delta` | Σ`costs` | actual `RESET_PNL.pnl_delta` | −(Σδ−Σc) |
|---|---|---|---|---|
| 2026-07-14 | −14.06 | 3.52 | 17.58 | **17.58 ✓** |
| 2026-07-15 | 0.99 | 1.82 | 0.83 | **0.83 ✓** |
| 2026-07-20 | −18.29 | 1.32 | **19.61** | **19.61 ✓** |

The old (Option-B) contract holds exactly on every day with data. Post-deploy it must become
`−Σpnl_delta` = **18.29**, differing by exactly `Σcosts` = 1.32.

---

## B. The merge (§A2) — zero conflicts

Verified **before** merging, not assumed: merge-base `4c148fb`; **0 commits on main since base touch
any of the five capital-path files**; `main.py` has exactly 1 (`34fd6a5`).

`git merge --no-ff e4-w10-pnl-contract` → **`Automatic merge went well`. Zero conflicts** — including
`main.py`, `PATHS.md` and `docs/SYSTEM_MAP.md`, all three of which the runbook flagged as likely.
Committed as `abad483` before any other step (runbook §2.3). **Never pushed.**

### Three runbook inaccuracies found (documentation only, no deploy impact)
1. **`broker/cost_calculator.py` is described as "(new, +54)". It is not new** — it pre-exists at base
   with 299 lines and the branch takes it to 353. The `+54` is exact; "new" refers to the added
   function `round_trip_costs_or_zero`.
2. **`test_e4_w10_pnl_contract.py` is described as "19 tests". It is 20** (`grep -c "def test_"`), and
   the collected delta confirms it: 5001 → 5021 = **+20**.
3. "**81 commits behind**" is now **94** — ordinary drift since the runbook was written on 19-Jul, not
   an error.

---

## C. The regression (§A3) — **the gate, and it stopped the deploy**

⚠️ **The historical "14-failure baseline" is not usable on this PC tonight**, so a fresh base run was
taken in the same window. Two reasons: the known-failure set is **time-of-day and calendar dependent**
(10 in-window / 11 outside / +1 on weekends), and `bash` on this PC resolves to
`C:\Users\rama\...\WindowsApps\bash.exe` — the **WSL stub**, not Git Bash. `shutil.which("bash")` finds
it, so the ~20 bash-subprocess tests in `test_fix065_market_hours_guard.py` and
`test_t4_deploy_preflight.py` do **not** skip; they run and fail. Both runs share one identical
environment 15 minutes apart, so **the diff is valid even though neither absolute number is 14.**

| | BASE `80fbe86` | MERGE `abad483` |
|---|---|---|
| failed | 33 | **36** |
| passed | 4963 | 4981 |
| skipped | 4 | 4 |
| **xfailed** | **1** | **0** |
| collected | 5001 | **5021** (+20) |
| wall clock | 795.04 s | 800.33 s |

Both runs completed well inside the pre-23:00 window (20:30–20:43 and 20:48–21:01), so the
**must-not-cross-midnight** rule was never at risk.

### `comm -23` — MERGE-ONLY = 3. BASE-ONLY = 0.
```
tests/integration/test_q9_capital_invariant_wired.py::TestContractInvariant::test_reader_equals_independent_ground_truth
tests/integration/test_q9_capital_invariant_wired.py::TestContractInvariant::test_the_discrepancy_is_exactly_the_double_counted_costs
tests/integration/test_q9_live_seed_mc1_wired.py::TestSharedHelper::test_the_reader_is_a_different_quantity_and_is_not_involved
```
All 33 base failures are pre-existing PC-environment noise (WSL-bash subprocess ×20, MagicMock
harness artifacts, PC-only `test_instance_lock`, the outside-window `test_interactive_startup`).
**Zero of them are attributable to the merge, and the merge fixed none of them.**

**Two of the three were predicted in writing before the run** (the arithmetic also predicted `xfailed`
1 → 0, which is exactly what happened). The third was not, and it is the important one.

### C1 — `test_reader_equals_independent_ground_truth` — **strict xfail → XPASS → FAILED**
Working as designed. Its own `reason=` names this branch by commit and says: *"When that lands this
XPASSes, and `strict=True` turns the XPASS into a FAILURE so the migration is deliberate."* The class
docstring adds: *"it cannot silently start passing when E4/W10 lands … forcing a deliberate flip as
part of that migration instead of a stale test quietly rotting in the suite."* **This failure is the
detector succeeding.** Required action: remove the `xfail` marker.

### C2 — `test_the_discrepancy_is_exactly_the_double_counted_costs` — **passed → FAILED**
Asserts `(reader − truth) == −Σcosts` **exactly**, so that a *new* value bug cannot hide behind the
old one. Post-fix `reader == truth`, so the delta is `0` while `costs > 0`. Required action: invert to
the new contract (`reader == truth`).

### C3 — ⭐ `test_the_reader_is_a_different_quantity_and_is_not_involved` — **the substantive one**
Not a marker flip. Its docstring states the safety argument for the M-C1 live-seed carryover:

> *"The daily-loss reader is `SUM(pnl_delta) − SUM(costs)`; the cancellation uses raw `pnl_delta` on
> both sides. … **Because the reader is not involved, E4/W10 (which changes the reader's contract)
> cannot disturb the cancellation.**"*

It asserts `reader != carryover`, with the failure message: *"the reader and the carryover have become
the same quantity — **re-derive §A2**; the cancellation's independence from the E4/W10 contract rested
on them differing."*

After E4/W10 the reader becomes `SUM(pnl_delta)` — **precisely the carryover's quantity.** The
premise on which the independence claim rested is now false.

**Important: the cancellation itself is NOT broken.** Its own invariant test,
`test_the_carryover_equals_exactly_what_phase_2_re_applies`, still **passes** — the carryover still
equals what Phase 2 re-applies. What is void is the *reason* previously given for why E4/W10 could not
disturb it. **That argument must be re-derived — it is capital-path reasoning, not a test edit.** This
is exactly what the gate exists to catch, and it is why "flip three tests and re-run" would have been
the wrong response.

**Why the runbook missed all three:** `test_q9_capital_invariant_wired.py` and
`test_q9_live_seed_mc1_wired.py` were written on **18-Jul**, *after* the branch was cut on **17-Jul**.
The branch could not have updated them, and the runbook (19-Jul) predicted only "collected rises,
failures stay at 14". The interaction is real and was invisible until the merge was actually built.

---

## D. Parity (§A4) — verified structurally, not assumed

- `round_trip_costs_or_zero(calculator, *, qty, entry_price, exit_price, product, logger, context)`
  takes **no mode parameter** — it is structurally incapable of branching on paper/live.
- Grepping **every added line** across the six changed code files for `is_paper|paper|live|mode`
  returns **only comments** asserting mode-agnosticism — there is not one mode conditional.
- The `CostCalculator` is built at `main.py:1858`, **before** the `if args.mode == "paper"` branch at
  `main.py:1860`, and the same instance is handed to both modes.
- The reader `get_daily_realized_net_pnl` (`core/state_store.py:2447`) is a pure SQL sum — no branch.

**One calculator built pre-branch, one reader, no conditionals ⇒ paper and live are affected
identically.** The runbook's "one reader" premise holds.

---

## E. State right now — and the exact commands

**Nothing was deployed.** `git push` was never run. Local `main` was reset to the pre-merge commit so
the documented `PC == origin == VM bare` invariant is restored and no future push can carry a
half-finished capital change by accident.

| item | state |
|---|---|
| `PC main` / `origin/main` / VM bare | **`80fbe86`** (identical) |
| Working tree | clean |
| Branch `e4-w10-pnl-contract` | **untouched at `ad34ee4`** |
| Merge commit `abad483` | discarded locally; **trivially reproducible, 0 conflicts** |
| VM artifacts | unchanged — nothing was written |
| DB backup | **kept** at `data_store/backups/pre_deploy_e4_w10_20260720.db` (valid for the retry) |

```bash
# reproduce the merge (zero conflicts):
git merge --no-ff e4-w10-pnl-contract

# ROLLBACK, had it been pushed — schema-free, minutes (runbook §6):
git revert -m 1 <merge-commit> && git push origin main
#   no schema step (v44 both ways), no ledger unwind (the control is per-day, zeroed nightly).
```

---

## F. What must happen before the next attempt

Not tonight — recorded so the retry is mechanical:

1. **Re-derive §A2 of the M-C1 live-seed argument (C3).** The blocking item. Establish on the new
   contract why the carryover cancellation is still sound when the reader and the carryover are the
   *same* quantity, then rewrite the test to pin whatever the new reason is. **Capital-path reasoning
   — it needs its own review, not a test edit.**
2. **Flip C1** — remove the `strict=True` xfail; the test should assert plain green.
3. **Flip C2** — invert to `reader == truth` on the new contract.
4. Re-run the full regression; `comm -23` merge-only must be **empty**.
5. Then deploy per the runbook, and correct the three documentation inaccuracies in §B.

**Checklist A3 was NOT flipped to the new signature** — E4/W10 did not ship, so Option-B remains
correct and flipping it would have made tomorrow wrong in the other direction. It **was** fixed for a
separate, deploy-independent defect: the check was **vacuous**. It compared `RESET_PNL.amount`
(**always 0.0**) against an `expected_reset` computed over *all* rows **including the `RESET_PNL` row
itself** (also **0.0**) — i.e. `0.0 == 0.0`, green for reasons unrelated to the contract. Both queries
now compare `pnl_delta` and exclude the RESET row, and the block is **pre-armed** with the exact value
(`18.29`) that becomes correct the day this ships, so the flip cannot be missed.

---

*Off-market, book flat, service never started. No code, config, schema, cron or systemd change reached
the VM. The only lasting artifacts are this report, the corrected checklist, and the DB backup.*
