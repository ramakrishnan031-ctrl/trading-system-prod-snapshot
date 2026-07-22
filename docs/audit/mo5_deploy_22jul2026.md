# M-O5 — fix + deploy (22-Jul-2026, off-market)

Implements and deploys the fix designed in `mo5_investigation_22jul2026.md`
(reviewed twice). **Off-market, after close.** The service is **not** restarted;
this is an EOD-path change that loads at the next natural boot.

## The defect (recap)

`fire_now` (`orders/eod_squareoff.py`) set `_fired_for_date[today]=True` before
calling `_fire`, with **no reset on failure**. `check_and_fire` — the 15:17
scheduled backstop — reads the **same** flag and skips if set. So a `fire_now`
whose `_fire` raised (or that returned with positions/cancels un-squared) left the
flag stuck and **silently disabled the 15:17 squareoff**. `check_and_fire` already
reset-on-exception (`:223-229`); `fire_now` was the lone asymmetry.

`fire_now`'s only production caller is the **automatic** daily-loss-breach callback
(`main.py:780`, `_on_daily_loss_breach`) — not an operator. Reachability is LOW
(0 breaches in 30 days; `_fire` is raise-resistant) but the path is live.

## The fix

`fire_now` now mirrors `check_and_fire`:

- Flag still set **before** `_fire` — the concurrency claim is unchanged.
- **On exception:** reset the flag, then re-raise (byte-for-byte the
  `check_and_fire` pattern). Re-raise preserves the caller's existing propagation.
- **Rider 1 — partial failure without raise:** if `_fire` returns with
  `positions_failed > 0 or cancels_failed > 0`, reset the flag too. "Fired" is not
  "squared everything"; the un-squared legs must not be blocked from the 15:17
  retry. Same predicate the restart-recovery path already uses (`:1717`).
- **On full success:** flag **stays set** — the scheduled fire must not re-square
  what `fire_now` already squared.

**Why the retry is safe (from the investigation, not assumed):** the E.5
broker-position filter (`_exit_open_positions`) re-queries broker truth on every
fire and skips any DB row the broker reports flat — so a retry cannot double-square
an already-closed position. The per-position exit loop cannot raise mid-placement
(it catches per position), so a `_fire` that raised did so either before any order
was placed or after the loop; and a daily-loss breach vs the 15:17 retry are hours
apart. `check_and_fire` has run this exact reset-and-retry pattern in production the
whole time.

Diff: `orders/eod_squareoff.py` (`fire_now` only) + `tests/unit/test_eod_squareoff.py`
(4 new tests).

## Tests — every bug-detector shown RED before the fix

| Test | What it pins | Before fix |
|---|---|---|
| `test_fire_now_raise_resets_flag_allows_scheduled_retry` | `_fire` raises → flag reset → 15:17 `check_and_fire` DOES fire | **RED** (flag stuck → returned False) |
| `test_fire_now_partial_failure_resets_flag_allows_scheduled_retry` | Rider 1: `positions_failed>0` (no raise) → flag reset → retry fires | **RED** |
| `test_fire_now_retry_resets_daily_pnl_twice_nonzero` | the retry re-runs `reset_daily_pnl` → 2 non-zero `RESET_PNL` rows; SUM correct (B1) | **RED** |
| `test_check_and_fire_raise_resets_flag_allows_retry` | pins `check_and_fire`'s OWN reset-on-exception (coverage gap) | GREEN both (new pin) |
| `test_fire_now_marks_fired_flag` (existing) | success path: flag stays set, 15:17 does NOT re-fire | GREEN (preserved) |

**A raising/failing mock is required** — paper `get_positions()` returns `[]` and
places zero exits, so the realistic `_fire` raise/partial-fail cannot reproduce in
paper (Rule #5). T1/T5 drive the raise via the unwrapped `soft_kill` (`:349`);
T2/T3 drive `positions_failed` via `place_order` raising `BrokerError` (caught
per-position).

- RED run (unfixed): `3 failed, 1 passed` (T1/T2/T3 fail; T5 pins existing).
- GREEN run (fixed): the 4 new tests pass; **full `test_eod_squareoff.py` = 68 passed / 0 failed**.

## Regression — BASE vs MERGE (`comm -23`, no fixed-number baseline)

Fresh same-session BASE vs MERGE, full `tests/unit/` (4917 tests). Both runs in
the **same working directory** (env-equivalent), only the 2 code files reverted
for BASE via a verified patch (`git apply --check` before revert; restored after —
never stash).

- MERGE (HEAD + this change): **11 failed, 4913 passed, 4 skipped** (782 s)
- BASE (clean HEAD, same tree): **11 failed, 4909 passed, 4 skipped** (761 s)
- **MERGE-only (regressions introduced): EMPTY** ✅
- **BASE-only (tests my change "fixed"): EMPTY** ✅ — the 11 failures are an
  identical, pre-existing PC-env set (order_placer retries, flask config, webhook
  secret, startup, one reconciler inflight-orphan), none in `test_eod_squareoff.py`
  and none exercising `fire_now`. The `+4 passed` in MERGE are exactly the new tests.

**Method note (a caught pitfall):** the first BASE attempt used a `git worktree`,
which produced 30 spurious BASE-only failures — the worktree lacked the main tree's
untracked local files and resolved `python` to the Microsoft-Store alias, so
env-dependent tests (`test_main`, `test_t4_deploy_preflight`) failed there
regardless of code. Re-running BASE in the **same** tree removed the confound and
gave `comm -23` empty both ways. Lesson: a worktree is a clean *code* baseline, not
a clean *environment* baseline.

## Deploy notes (ship WITH the code — §B)

**B1 — RESET_PNL must be verified by SUM, never a single row.** `reset_daily_pnl`
(`fund_manager.py:1617`) writes `entry_type='RESET_PNL'` with `pnl_delta = -old_pnl`.
A Rider-1 retry calls it **twice** in one day (the partial-fail `fire_now` + the
15:17 retry); the second row carries `pnl_delta = -(net at retry)`, which is **NOT
zero** if net moved between the two fires. Any verification of the day's reset must
`SUM(pnl_delta)` over the `RESET_PNL` rows (the whole day still sums to 0), never
read a single row. Recorded in `docs/SYSTEM_MAP.md` (highest-propagation surface).

**B2 — the tests are the only verification this change will ever get.** There has
been **no daily-loss breach in 30 days**, and `_fire` is engineered to swallow
routine broker failures — so there is **no natural production-verification day**.
Unlike E4/W10 (verified at a real `RESET_PNL`) and B1-boot (verified at 08:15),
nothing will confirm M-O5 at a future 15:17 or boot. The RED-first demonstrations
are the entire evidence base.

**B3 — tomorrow's 08:15 boot does NOT verify this.** An EOD-path change is not
exercised by a morning boot; only a daily-loss breach (or the test suite) exercises
`fire_now`.

## Backup / deploy / rollback — CONFIRMED (filled 22-Jul from real post-deploy output)

- **Backup:** `data_store/backups/pre_deploy_mo5_20260722.db` on the VM (111 MB,
  taken 16:46 IST before the push). Integrity **verified read-only**: `PRAGMA
  quick_check` = `ok`; `schema_meta.schema_version` = `44` (the app's source of
  truth — SQLite `PRAGMA user_version` is `0` by design, so v44 is read from the
  table, not the pragma). Schema-free change ⇒ the backup is an anchor, not a
  required unwind path.
- **Deploy — DONE.** `git push origin main` landed on the VM bare
  (`~/trading-system.git`, `main` = `4585bd2`); post-receive checked out to
  `/home/ubuntu/systems/trading-system` — **verified**: the deployed
  `orders/eod_squareoff.py` sha256 (prefix `908669a0`) is byte-identical to the
  `4585bd2` blob (`git show 4585bd2:orders/eod_squareoff.py | sha256sum`). Crontab
  present (47 trading jobs; AUTO-INSTALL re-run is idempotent). **Service left
  inactive** (self-exited 16:00, book flat) — NOT restarted, so the EOD-path change
  loads at the next 08:15 boot. Schema **v44 unchanged, no migration**.
- **Verify — HOLDS NOW:** the M-O5 deploy point is `4585bd2`, tag
  **`deploy-22jul-mo5`** (annotated `ae53dfd` → `4585bd2`, pushed to origin). PC
  `main` == origin (VM bare) == VM checkout — confirmed identical
  (`git ls-remote origin refs/heads/main` == local HEAD; the VM working tree by the
  hash match above). *This deploy-record completion is a docs-only follow-up commit
  riding on top of `4585bd2`; it changes no code, schema, or cron and does not move
  the deployed behaviour.*
- **Rollback:** `git revert 4585bd2` (schema-free; the change is one method) and
  re-push off-market; or reset origin to `13518d6` (its parent). No state/schema to
  unwind.

*Off-market; no order placed; the service was not restarted; the EOD path was not
triggered. paper==live (the fix is mode-agnostic; the flag logic is identical).*
