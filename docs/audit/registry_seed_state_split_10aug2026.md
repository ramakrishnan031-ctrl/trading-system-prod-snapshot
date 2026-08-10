# ITEM 6 — the daily-CRITICAL EOD report: SEED / STATE split

**`<BUILT · TESTED · ⛔ NOT PUSHED · ⛔ NOT DEPLOYED>`** — branch `fix/registry-seed-state-split`
off the **DEPLOYED** `645728d`, worktree `D:\Projects\trading-system-regsplit`.

> ⚠️ **PROVENANCE (`G1`): `"Go with B — build it"` was an AUTO-FILL, ⛔ NOT Rama.** It proceeds
> under his standing *"continue to fix remaining pending things now"* — **a defect repair, ⛔ not a
> policy change.** Recorded rather than assumed.

**PARITY:** this is a **CRON/REPORT path — MODE-INDEPENDENT.** It reads no broker quantity and takes
no trading decision. ⛔ Stated as such rather than claimed to be covered by paper.

---

## §1 · THE DEFECT, AS MEASURED — and the recorded premise was partly wrong

`scripts/strategy_registry_officer.py` wrote its **runtime state** into
`config/strategy_direction_registry.yaml`, a **git-TRACKED** file. Chain, traced not inferred:

1. `scripts/system_manager.py:1195` runs `git diff --stat HEAD` over the deployed work tree;
   `:1217-1223` raises `res.violation(...)` on any non-empty diff.
2. `generate_full_report:884` sums violations across **all** checks.
3. `main:1336` — `severity = "CRITICAL" if (violations or reasons) else "INFO"`.
   ⭐ **The report has NO WARNING severity — it is BINARY.** One violation from any check ⇒ the whole
   EOD report is CRITICAL.

### 🔴 The recorded premise is REFUTED in one respect, and it changed the fix
The register said the file is *"REWRITTEN DAILY at 16:22"*. **(P) `strategy_registry_officer.py:253-254`
— `save_registry` is called ONLY `if changed`.** Once every traded strategy is `CONFIRMED` the officer
**stops writing**. The real mechanism is a **PERSISTENT UNCOMMITTED DIVERGENCE**: HEAD holds the 17-Jul
seed (**MEASURED: 16 rows, all `PENDING`** — ⛔ not the recorded 18), the deployed file holds converged
state. ⇒ **"make the report treat a DAILY-REGENERATED file as expected" rests on a premise that does
not hold.**

### §1.1 · `registered_direction` has ZERO callers — CONFIRMED, with its width
**WIDTH: `grep -rn "registered_direction"` over the ENTIRE working tree, all file types, no include
filter.** Hits: the definition (`core/strategy_direction.py:91`), its two `__pycache__` binaries, two
doc lines. ⭐ **Sharper answer to the real question: the FILE's content is read by exactly ONE consumer
— `load_registry()` at `strategy_registry_officer.py:224`, the officer that also writes it.**
⛔ `eod_squareoff.py:695` is not a counter-example — it uses `build_direction_map`, which reads
`config/strategies/*.yaml`. ⛔ **`registered_direction` is NOT retired here — zero callers is a
finding, and retiring it is its own decision.**

---

## §2 · WHY (B), AND WHY (A) IS NOT THE SMALL OPTION IT LOOKS LIKE

🔬 **MEASURED, ⛔ NOT INFERRED — throwaway repo, the exact hook command
(`deploy/hooks/post-receive:31`, `git --work-tree=… --git-dir=… checkout -f`):**
**the first deploy after `git rm --cached` + `.gitignore` DELETES the live file.** Untracked-file
protection does **not** apply — at checkout time the path is still tracked at the OLD head.

| | (A) untrack | **(B) seed + state split — BUILT** | (C) classify in the report |
|---|---|---|---|
| root cause | ✅ | ✅ | ⛔ left in place |
| new config key | none | **none** | none |
| `first_seen` provenance | 🔴 **lost for 16** | ✅ **preserved** | n/a |
| one-time alert burst | 🔴 16 NEW | ✅ **none** | n/a |
| premise | ok | ok | ⛔ **refuted by §1** |

⭐ `.gitignore:35-39` (`config/instruments.csv`) is precedent for (A) and names **this exact hook and
incident** (*"instruments staleness incident 21-Jun-2026"*) — it is why (A) was taken seriously, and
the measurement above is why it was not taken.

---

## §3 · WHAT WAS BUILT — 2 source files, ⛔ nothing else

`git diff --stat 645728d` = **`core/strategy_direction.py` + `scripts/strategy_registry_officer.py`**
(+ the new test file). **(P) `scripts/system_manager.py` is UNTOUCHED** — that, not a test, is the
strongest guarantee that **no severity was weakened and no path was whitelisted**. **(P) `git diff
645728d -- '*.yaml' '*.yml'` is EMPTY ⇒ ⛔ no new config key.**

- **`config/strategy_direction_registry.yaml` — stays TRACKED, becomes a READ-ONLY SEED.** Production
  never writes it. ⇒ the deployed tree equals HEAD permanently.
- **Runtime state → `data_store/strategy_direction_registry.yaml`** — ⭐ `data_store/` is **already
  gitignored (`.gitignore:17`) ⇒ NO new ignore line**, and `checkout -f` leaves it alone.
- `load_registry(path, seed_path=None)` — the seed is a **FALLBACK, never an override** (once state
  exists it wins outright, else a deploy would silently revert converged state). ⭐ `seed_path` is
  **opt-in**, so every existing caller is byte-unchanged.
- `save_registry` now `mkdir`s its parent (data_store/ may be absent in a fresh checkout).

---

## §4 · TESTS — RED-first, and *"the alarm turned green"* is NOT the contract

**MEASURED on the PRE-FIX tree: `8 FAILED / 3 PASSED`.** ⭐ **The 3 that passed are exactly the ones
that MUST be green on both trees** — the two anti-suppression controls and the seed-reproducibility
guard. **Post-fix: `11 PASSED`.**

| test | pre-fix | role |
|---|---|---|
| `…does_not_write_into_the_tracked_config_dir` | **RED** | the defect itself |
| `…state_is_written_to_data_store` | **RED** | the new location |
| `…leaves_the_deployed_tree_equal_to_head` | **RED** | the false CRITICAL, end-to-end through the REAL `deployed_tree_check` |
| 🔑 `test_CONTROL_a_genuine_tracked_drift_still_goes_critical` | **GREEN** | ⭐ **the anti-suppression control — if it ever goes quiet, the fix has become suppression** |
| 🔑 `test_CONTROL_an_unrelated_tracked_file_still_violates` | **GREEN** | the check's scope is unchanged |
| `…survives_the_deploy_hooks_checkout_f` | **RED** | state survives the deploy |
| `…falls_back_to_the_seed_silently` | **RED** | ⭐ **the zero-loss proof: no NEW re-announcement, `first_seen` preserved** |
| `…state_takes_precedence_over_the_seed` | **RED** | seed is fallback, not override |
| `…seed_fallback_is_opt_in_and_absence_is_still_empty` | **RED** | the fail-safe is preserved |
| `…officer_still_converges_second_run…` | **RED** | convergence preserved |
| `…the_tracked_seed_stays_reproducible` | **GREEN** | the seed still boots a fresh machine |

**Adjacent set** (per the carry-the-countermeasure rule — it includes the file named after the thing
changed): `test_strategy_registry_officer.py` · `test_system_manager.py` ·
`test_system_manager_deployed_tree.py` ⇒ **56 passed.**

---

## §5 · MIGRATION — what happens on the first deploy

1. `checkout -f` restores `config/strategy_direction_registry.yaml` to the seed (all-`PENDING`).
   ⭐ **This is what CLEARS the standing divergence.**
2. The next 16:22 run finds **no** `data_store/…` state ⇒ **falls back to the tracked seed** (16 rows,
   `first_seen: 2026-07-17`) ⇒ re-`CONFIRM`s from the DB (silent, `reconcile:107`) ⇒ writes
   `data_store/…`.
3. ⇒ **0 new registrations, 0 alert burst, `first_seen` intact.** Thereafter `config/` is never
   written again ⇒ **the deployed tree equals HEAD permanently.**

> ⚠️ **ONE BOUNDED UNKNOWN, STATED NOT GLOSSED:** if the LIVE registry has rows the seed lacks, those
> are re-announced as NEW **once**. **Bounded:** `test_committed_seed_matches_config_directions`
> passes at HEAD ⇒ the seed lists exactly the configured strategies, so this can only affect a
> **signal-only scanner with no YAML** registered after 17-Jul. ⛔ **Not checkable from the PC — no VM
> command was run.** Consequence is one informational alert, ⛔ no data loss.

---

## §6 · WHAT THIS DOES NOT DO
⛔ No severity weakened · ⛔ no path whitelisted · ⛔ `system_manager.py` untouched ·
⛔ `registered_direction` NOT retired · ⛔ nothing bundled (⛔ not Tick 2, F6, Fix 1, Fix 2, Fix 3,
sizing, alert phases, or either frozen prediction) · ⛔ no push, no deploy, no VM command.
