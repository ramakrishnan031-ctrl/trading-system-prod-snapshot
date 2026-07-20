# RUNBOOK — deploy the E4/W10 `pnl_delta` NET contract  (PREPARED, **NOT EXECUTED**)

> ⛔ **TIMING — READ FIRST.** This is a change to the **capital path** (the daily-loss control input). It goes **after Monday 20-Jul's live session has been observed**, in an unhurried window, through the careful loop — **never** as a "while we're here." It must not be the last change before a boot, and it does not go in on a trading morning. Decision **01 (E4/W10)** must be answered **Option A (adopt)** first — this runbook does nothing until then.
>
> ⛔ **NOTHING IN THIS FILE HAS BEEN EXECUTED.** No checkout, no dry-run merge, no branch touch. Writing it was read-only inspection only (`git log`/`merge-base`/`diff <commit>..<commit>`). Executing it is a separate, deliberate act.

This runbook exists so that a "yes" becomes **mechanical**, not a fresh design problem weeks from now. It is procedure, not advice.

---

## 0. Preconditions (before anything)
1. **Decision 01 answered "adopt" (Option A).** This is a risk-posture change (the limit fires *later*, on true net); Rama's explicit sign-off is the gate, held since 17-Jul (*"won't merge a risk-posture change on 'clear the list'"*).
2. **Rama flattens the book manually first.** **No flatten mechanism was built into this change, and none should be.** With the system down and the book flat there is currently nothing to flatten — but **do not assume that at deploy time**: confirm the book is flat (no open positions, no pending orders) at the moment of deploy.
3. **Off-market window**, system down or safely between sessions; schema is **v44** on both sides (no migration involved).
4. **A DB backup exists** (the standard pre-deploy backup, e.g. `pre_deploy_e4_w10_<date>.db`), `quick_check=ok`, before any push.

## 1. What deploys — branch `e4-w10-pnl-contract` @ `ad34ee4`
Two commits off base `4c148fb`: `1f4509e` (the contract migration) + `ad34ee4` (a self-review fix: pass `product` through, never default to MIS). **Never pushed** (`git ls-remote origin` has no such ref). Schema **v44 unchanged**.

**The contract it installs:** `fm_ledger.pnl_delta` **is NET**; `costs` becomes **observability-only and is never re-subtracted**. Reader `state_store.get_daily_realized_net_pnl` → `SUM(pnl_delta)`. The three backstop close paths (reconciler CHECK1 + CHECK4-partial, `cnc_gtt_monitor`) take the shared, mode-agnostic `broker/cost_calculator.round_trip_costs_or_zero` (**fail-OPEN** — a cost-calc failure degrades to 0.0 *loudly*, never blocks a capital release). CHECK1 + GTT financial writers gained a real `gross_pnl` (they had hardcoded `gross_pnl=net_pnl, charges=0.0`), preserving `Σ pnl_delta == Σ trades.net_pnl` (`PATHS.md:447`).

**Files (base..branch), 11 total:**
- Capital-path code (6): `broker/cost_calculator.py` (**+54; ⚠️ CORRECTED 20-Jul: NOT a new file** — it pre-exists at 299 lines and the branch takes it to 353; the new *function* is `round_trip_costs_or_zero`) · `capital/fund_manager.py` · `core/state_store.py` · `main.py` (+2, CostCalculator wiring ~`:1786`) · `orders/order_reconciler.py` · `orders/cnc_gtt_monitor.py`
- Tests (2): `tests/unit/test_e4_w10_pnl_contract.py` (new, **20** tests — ⚠️ CORRECTED 20-Jul from "19"; confirmed by the collected delta 5001 → 5021) · `tests/unit/test_migrations.py` (+10 — the **deliberate** contract inversion `450.0→500.0`)
- Docs (3): `PATHS.md` · `docs/SYSTEM_MAP.md` · `docs/audit/e4_w10_done_17jul2026.md`

## 2. Integrate onto current main — the branch is **81 commits behind** its base *(⚠️ 20-Jul: now **94** — ordinary drift, not an error. The merge was executed 20-Jul and produced **ZERO conflicts**, including all three files predicted below.)*
The branch was cut 17-Jul off `4c148fb`; main is now `1462984` (code identity `d271525` + docs). **This is not a fast-forward.** Verified overlap (read-only, `4c148fb..main`):

| what the branch touches | changed on main since base? | merge risk |
|---|---|---|
| `cost_calculator.py`, `fund_manager.py`, `state_store.py`, `order_reconciler.py`, `cnc_gtt_monitor.py` | **0 commits each** | **clean — the substantive capital merge is conflict-free** |
| `test_e4_w10_pnl_contract.py` (new), `test_migrations.py` | untouched on main | clean |
| `main.py` | **1 commit** (+87; the M-C1 live-seed / consecutive-losses work) | **check** — the branch's +2-line wiring (~`:1786`) vs main's additions (~`:2255`) are almost certainly different regions, but resolve by hand, keep both |
| `PATHS.md`, `docs/SYSTEM_MAP.md` | changed on main (decision-package batches) | **textual conflict likely — keep both sets of edits** |

**Sequence (repo convention — `origin` IS the VM bare repo, so a push to `main` deploys):**
1. `git checkout main` (at `1462984` or later); create an integration branch if preferred.
2. `git merge --no-ff e4-w10-pnl-contract` **(or** rebase the branch onto main then fast-forward**)**. Expect conflicts **only** in `main.py`, `PATHS.md`, `docs/SYSTEM_MAP.md` — resolve keeping both sides; the 5 capital-code files apply clean.
3. **Commit the integration BEFORE any attribution/checkout step** — a `git checkout HEAD -- <file>` during attribution silently wiped an uncommitted edit on 17-Jul; commit first, then verify with `git status`.

## 3. Pre-deploy checks (on the **integrated** tree, not the branch in isolation)
1. **Full regression on the merge result.** ~~Current baseline: **14 failed · 5025 passed · 5 skipped · 1 xfailed = 5045 collected** … the **failure set must stay at those 14**.~~
   **⚠️ REPLACED 20-Jul-2026 — THERE IS NO FIXED-NUMBER BASELINE.** "14" was a property of one
   environment at one moment, not of the code: the known-failure set is **time-of-day and calendar
   dependent** (10 in-window / 11 outside / +1 on weekends), and on the PC `bash` resolves to the **WSL
   stub** (`WindowsApps\bash.exe`), so `shutil.which("bash")` succeeds and ~20 bash-subprocess tests
   (`test_fix065_market_hours_guard` ×17, `test_t4_deploy_preflight` ×3) **run and fail instead of
   skipping**. Measured 20-Jul: BASE `80fbe86` = 33 failed / 5001 collected; MERGE = 36 failed / 5021.
   **METHOD: take a fresh BASE run in the SAME session and window as the candidate run, and `comm -23`
   the two failure sets. Neither absolute number is meaningful; the differential is exactly meaningful.
   Merge-only must be EMPTY.** Two branch-edited files (`state_store.py`, `fund_manager.py`) are
   contract-central, so do **not** take a green run on trust: any delta gets attributed.
   **⚠️ Expect 3 merge-only failures until the Q9 detectors are migrated** — see
   `docs/audit/e4_w10_deploy_stopped_20jul2026.md` and the re-derivation in
   `docs/audit/mc1_live_seed_rederivation_20jul2026.md`. They are **not** covered by this runbook
   because both test files were written 18-Jul, *after* the branch was cut on 17-Jul.
2. ⚠️ **The regression MUST NOT cross midnight** (standing rule): `_TODAY` is captured at collection but the engine dates at execution — a run started after ~23:15 IST corrupts attribution silently. Run it in one pre-23:00 window.
3. **Identity + artifacts:** confirm `PC == origin == VM bare` before the push; fingerprint the three artifacts (live DB, forward-shadow JSONL, `analytics.db`) so "before" is on record.

## 4. Deploy (push + tag)
1. `git push origin main` — this **deploys**: the post-receive checks out to `/home/ubuntu/systems/trading-system` and reinstalls the crontab (**confirm the `post-receive: crontab AUTO-INSTALLED from canonical` line prints**; the reinstall is invariant unless `deploy/cron/*` changed, which this branch does not).
2. `git tag deploy-<ddmon>-e4-w10-pnl-contract <merge-commit>` and `git push origin --tags` (repo convention, mirrors `deploy-19jul-consecutive-losses`).
3. Re-confirm `PC == origin == VM bare` at the new HEAD.

## 5. Post-deploy verification — a concrete, checkable signature
The observable that proves the reader now returns **NET**:

- **At the ledger (live, read-only `mode=ro` on `data_store/trading_system.db`):** before this change every `RESET_PNL` row equals **−(Σpnl_delta − Σcosts)** for its day; after it, each `RESET_PNL` must equal **−Σpnl_delta** (the costs term is gone). On the first EOD reset on the new code, sum that day's `RELEASE_USED` `pnl_delta` and `costs` and confirm `RESET_PNL == −Σpnl_delta`, **not** `−(Σpnl_delta − Σcosts)`. For any losing day with non-zero costs the two differ by exactly `Σcosts`.
- **At the unit level (already pinned):** the reader returns **−100.0** (not −140.0) for a NET row of −100 / costs 40; `tests/unit/test_e4_w10_pnl_contract.py` (19) encodes this and is RED on the old tree.
- **Sanity:** `Σ pnl_delta == Σ trades.net_pnl` still holds (both writers moved together); `daily_realized_pnl` in the next `CapitalSnapshot` reflects true net.

## 6. Rollback — schema-free, minutes
Revert the deployed change and re-push:
- if merged `--no-ff`: `git revert -m 1 <merge-commit>`; if rebased/fast-forwarded: `git revert` the 2-commit range `1f4509e..ad34ee4`.
- `git push origin main` re-deploys the reverted tree (same post-receive path). **No schema step** (v44 both ways), no data migration, nothing to unwind in the ledger (the control is per-day and zeroes nightly). Elapsed: a single push + checkout — **minutes**.

## 7. The 36 pre-fix `costs=0` rows — not backfilled, and why the timing helps
36 historical `RELEASE_USED` rows were written gross (`costs=0`); their real costs were never computed, so **any backfill would be fabrication** — they are left as-is. This is bounded: the daily-loss control is **per-day and zeroed nightly by `RESET_PNL`**, so only *today's* rows feed the limit, and from the first boot on the new code every row is NET. **Deploying after a day's EOD reset** makes the new reader read that day as `Σcosts` (a small positive ⇒ no spurious breach ⇒ harmless), which is why an off-market, post-reset window is the clean time to do it.

---

*Prepared 19-Jul-2026 as decision-support. No step executed. The branch was not checked out, merged, rebased, tagged or pushed. This runbook does not authorise the deploy — decision 01 and the manual flatten do.*
