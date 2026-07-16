# M-C5 (CAS harden) + M-C6 (zero/negative → skip) + test_main logger-leak fix

**Date:** 16-Jul-2026 (IST) · **Branch:** `mc5-mc6-testmain-16jul` (off `main`@`16437ae`)
**Status:** IMPLEMENTED + TESTED, **UNPUSHED**. Deploys OFF-MARKET with the M-C cluster.
**Commits (3, independent):** `f87e587` test_main · `0847134` M-C5 · `6ca1bbe` M-C6.
**Schema:** unchanged. **Parity:** M-C5 and M-C6 are single mode-agnostic paths.

> **With these, the M-C capital-safety cluster is COMPLETE** — M-C4 (`6c77525`),
> M-C8 (`mc8-async-hardkill-16jul`@`8f9ce0d`), M-C5 + M-C6 (this branch). All four
> fixed, all four unpushed, pending consolidation → combined regression → sandbox
> hard_kill drill → off-market deploy.

> **⚠️ Two deliberate behaviour/test changes need ratification — see §5.**

---

## 1. Commit 1 — `f87e587` test_main leaks a MagicMock into `main._log`

**Root cause.** `tests/unit/test_main.py` patches `main.get_logger` → MagicMock.
`main._main_locked` declares `global _log` (`main.py:1538`) and rebinds it
(`_log = get_logger("main")`, `main.py:1626`), so every test that runs `main()`
replaces the module-level logger with that mock. `patch.multiple`/`patch` restore
`get_logger` on exit but **nothing restores `_log`** — it is a global that *main
itself* reassigned, not something the patch ever owned.

**Why it matters.** The leak is session-wide and **silent**. After any test here runs
`main()`, `main._log` stays a MagicMock for the rest of the session: `addHandler()` is
a no-op mock call, `_log.critical()` records nowhere, `caplog` reads empty. **A later
test asserting on main's logging does not fail — it stops testing anything and
passes.** That is how it was found: an M-C8 shutdown test asserting a CRITICAL passed
in isolation and failed after `test_main`, because it was asserting against a mock
that swallowed the log.

**Fix (test-only, contained — no production redesign).** An autouse module fixture
saves `main._log` before each test and restores it after. Autouse and module-wide on
purpose: there are **four** separate `patch.multiple("main", ...)` application sites
(`_run_main`, `_run`, and two inline), so patching them one-by-one would leave the next
site to reintroduce it.

**Regression prevented.** `test_zz_main_log_is_not_left_as_a_mock` asserts the *entry*
invariant the fixture provides. It is deliberately **last in the file** — pytest runs
tests in definition order, and last is the only position where it has meaning (i.e.
after many tests have each rebound `_log` to a mock). **RED with the fixture
disabled:** `isinstance(<MagicMock>, logging.Logger)` is False. Cross-module payoff
verified separately with a throwaway probe module: it now sees a real logger where the
assertion was previously vacuous.

**Result:** `test_main` 4 failed / 72 passed — the 4 are the known PC-env set,
unchanged; +1 new guard.

---

## 2. Commit 2 — `0847134` M-C5: `commit_adopted_entry` CAS

**Root cause.** The exactly-one-commit guard (`_commit_exists` — "no COMMIT row in
`fm_ledger` yet") is evaluated **inside** `self._lock`; the commit runs **outside** it.
It must: `commit_to_used` takes the lock itself and defers its BL-4 hard_kill to after
release (C.1), so calling it under the lock would risk deadlock.

That leaves a window the durable guard cannot cover. Two callers that did **not** gate
on the caller-side atomic trade-state transition both pass the guard (neither has
written a COMMIT row yet) and both reach `commit_to_used`.

**What actually breaks.** Not capital. `_apply_commit` (`fund_manager.py:1999`) pops the
reservation, so the second `commit_to_used` finds none, raises `ValueError`, and BL-4
fires **hard_kill**. *A spurious emergency halt caused by nothing but a race.*

**Not reachable today** — both production callers gate on an atomic transition
(`order_reconciler:3603` `adopt_recovery_trade_to_open`, `:3660`
`mark_recovery_trade_exiting`), so only one caller ever arrives. The fix makes the
**method safe on its own**, so a future non-gating caller cannot reintroduce it.

**Fix — Option A (CAS), explicitly NOT a lock across the I/O.** Three independent gates:

| # | Gate | Scope |
|---|---|---|
| 1 | the caller's atomic trade-state transition | primary, unchanged — the only reason this was never reachable |
| 2 | the `fm_ledger` COMMIT row (`_commit_exists`) | **durable** — stops a LATER cycle re-committing after a restart |
| 3 | **NEW:** an in-memory claim (`_commit_claims`) | **concurrent** — covers the in-flight window (2) structurally cannot see |

The claim is a test-and-set on a set, taken under the lock **already held** for the
guard — so guard-and-claim are one atomic step — and released in a `finally`. **No lock
is held across the commit I/O**: that is the M-4/M-C4 anti-pattern M-C8 exists to
avoid, and re-creating it here would put a broker-bound commit behind the lock that
gates every capital read.

Released on **success** (the durable guard takes over; holding it would only leak
memory) and on **failure** (releasing is what allows a legitimate retry; if the ledger
row was already written, guard (2) no-ops the retry anyway). `_commit_claims` is **not**
part of the 3-balance invariant — pure concurrency control, so a stale entry could only
cause a no-op, never a capital error.

**Regression prevented — RED ON OLD, showing both halves of the defect at once:**

```
E   assert CommitResult(reservation_id='9b8bec9c...', actual_margin=5000.0, ...) is None
    CRITICAL t_mc5:fund_manager.py:970 commit_to_used_failed_hard_kill
```
i.e. the second caller committed **and** the spurious hard_kill fired. The race is made
deterministic by parking only the **first** caller inside `commit_to_used` — blocking
both would deadlock the old code instead of failing it cleanly.

Also covered: the claim is released after success (a later call still no-ops via the
ledger guard) and after failure (no permanent lock-out of retries — a leaked claim would
silently disable commits for that reservation); and **the caller gate is kept explicitly
under test** — a gating caller still commits reserved→used, and the atomic transition
still admits exactly one winner.

**Result:** capital suites **238 pass** (fund_manager, e2_capital_tightening,
cnc_gtt_adoption, mo2_check4_partial_capital, phase3_delivery_caps, order_reconciler,
a1e1_recovery_matrix) — zero new failures.

---

## 3. Commit 3 — `6ca1bbe` M-C6: zero/negative multiplier → skip

**Root cause.** `position_sizer.calculate`:
```python
effective_mult = tier_mult * max(0.0, perf_weight)
tiered_qty     = floor(raw_qty * effective_mult)
tiered_qty     = max(1, min(tiered_qty, raw_qty * 2))     # FIX-133 Item 21
```
A **zero** multiplier means the sizing model said to trade **nothing**; `max(1, 0)`
turned that into **one lot** — real capital and real risk on exactly the signal it had
just declined. A **negative** multiplier did the same.

**Fix.** The floor was not wrong, it was doing two jobs. Its real one — stopping a
small-but-**positive** multiplier from rounding to zero and silently killing a wanted
trade — is untouched. Only the `<= 0` case is split off, returning
`SizingResult(success=False, qty=0, constraint="ZERO_MULTIPLIER")` + a WARNING + a full
breakdown, mirroring the existing `raw_qty <= 0` early exit. Negative is treated exactly
as zero: there is no meaning to a negative size.

**`<= 0` needs no tolerance** (confirmed from the code, per the instruction's
conditional): `perf_weight` is clamped `>= 0` one line above, so the product cannot be a
tiny FP negative — a negative here is a genuinely negative `tier_mult`.
`PositionSizingTierConfig` types HIGH/MEDIUM/LOW as **bare floats with no `ge=0` bound**,
so `tier_multipliers.HIGH = -1.0` loads cleanly — negative is a real config-typo path.
`-1.0 * 0.0 == -0.0`, which `<= 0` catches (a `< 0`-only guard would not); pinned by a
test so a future tidy cannot silently reopen it.

**Behaviour-neutral in production — verified, not assumed:** `performance_allocator`
clamps `min_weight=0.5` (PA3/PA8) and `signal_processor:893/:1827` defaults an unknown
strategy to `perf_weight=1.0`, so `effective_mult >= 0.5 × 0.5 = 0.25` and the skip
branch never fires today. **This is the hard PREREQUISITE for ever lowering
`min_weight`** — exactly when it would start to matter.

**Regression prevented — RED ON OLD** (proven by removing the branch and re-running):
zero → skip, negative → skip, and `-0.0` → skip all fail on the old floor. The
preservation tests are positive controls passing on **both**: tiny-positive (0.0001)
still floors to 1 lot — *if that ever goes red, the split has eaten FIX-133* —
normal-positive unchanged, 2x cap and monotonic scaling hold, and `min_weight=0.5 ×
tier=0.50` still sizes normally.

**Result:** sizing suites **59 pass** (position_sizer, fix133, delivery_scaffold, mc6);
`fix066` green.

---

## 4. Full regression

Run at 21:11–21:24 IST against the committed tree (`6ca1bbe`), verified to have **no
uncommitted `.py`** before starting:

```
11 failed, 4735 passed, 15 skipped in 751.85s (0:12:31)
```

**All 11 are the known time-gated PC-env set** (11 after 16:00 IST) — `test_main` ×4,
`test_order_placer_fix061` ×4, `test_fix181`, `test_interactive_startup`,
`test_phase17_batch2`. That is the *exact* set attributed against the true pre-M-C8
tree earlier today (see `mc8_async_hardkill_16jul2026.md` §6). **ZERO new failures.**

`test_end_to_end_smoke` did not recur — consistent with it being the load-sensitive
timing flake documented in the M-C8 report (that run took 17m34s; this one 12m31s).

**One warning chased down rather than waved off.** The run emitted
`PytestUnhandledThreadExceptionWarning: TypeError: '>' not supported between instances
of 'MagicMock' and 'int'`. It did not appear in earlier runs' logs — but that proved
nothing: those logs are `tail`-captured, so the line was simply outside the window.
Attributed properly instead, by A/B-ing the actual file: `test_main` emits it **once on
my branch and once on `main`'s version** (fixture provably absent, `grep -c
_restore_main_log` → 0). **Pre-existing, benign** (an unhandled exception in a daemon
thread at teardown, not a failure), unrelated to this work.

Attribution rule applied throughout (learned the hard way on M-C8): baseline against
the **true pre-change tree** (`git checkout main -- <files>`, then confirm the change is
*absent* via `grep -c`), **never `git stash`** — stash only stashes *uncommitted* work,
so a post-commit "baseline" silently contains the change on both sides and every number
matches.

Per-area: capital **238 pass** · sizing **59 pass** · `test_main` 4-known-fail / 72 pass
(+1 new guard).

---

## 5. ⚠️ Deliberate changes needing ratification

1. **M-C6 inverts a documented FIX-133 decision.**
   `test_fix133_dynamic_sizing.py::test_perf_weight_zero_floor_at_one` asserted
   `tiered_qty >= 1` for `perf_weight=0` and was *named* for it; the module docstring
   recorded `perf_weight=0 -> floor at 1` as intended. **That test encoded the defect.**
   It is renamed `test_perf_weight_zero_skips_the_trade` and now asserts the skip, with
   the reasoning and production-neutrality inline; the docstring is corrected. Every
   sibling FIX-133 assertion (2x cap, scaling, breakdown) is untouched and passes.
   The approved instruction explicitly directs this split, so this is expected — but it
   is a *behaviour change to a capital path* and should be acknowledged, not assumed.

2. **M-C5 adds in-memory state to FundManager** (`_commit_claims`). Deliberately not
   part of the 3-balance invariant. In-process only — coherent with the rest of the
   design (`_reservations` is in-memory too), and the durable `fm_ledger` guard remains
   the cross-restart defence. A DB-level CAS would have needed a UNIQUE constraint on
   `(reservation_id, entry_type)` = a schema change, which is out of scope.

---

## 6. Deploy / next

**NOTHING PUSHED.** Next phase (separate task): consolidate the M-C cluster
(M-C4 `6c77525` + M-C8 `8f9ce0d` + this branch) into main → **fresh combined
regression** (full suite, time-of-day-aware) → **sandbox hard_kill drill** (M-C8 is the
emergency path) → off-market cluster deploy. **Do not disturb the deployed `11abebb`.**

**Rollback:** the three commits are independent — revert any one alone. All schema-free.
</content>
