# B1 live-seed extraction + midnight day-floor — **DEPLOYED** 21-Jul-2026

**`main()` now derives the boot day-floor ONCE and passes it to both the live seed and rehydrate.**
Merge `e21cf9e` (branch `b1-midnight-day-floor`, `151d3b4`). Off-market, book flat, service
`inactive` until the 08:15 token refresh. Schema v44, no migration. Backup retained:
`data_store/backups/pre_deploy_b1_midnight_floor_21jul.db` (`quick_check=ok`, 2260 fm_ledger rows).

Implements `docs/decisions/DESIGN_midnight_day_floor.md` (B5: do B1 **with** the floor fix, one edit)
and the B1 half of `docs/audit/boot_path_pair_design_20jul2026.md`. Unblocked by E4/W10 CONFIRMED.

---

## A. The change (main.py only; fund_manager untouched)

1. **Extracted** the inline M-C1 live seed (formerly in `_main_locked`'s body) into a module-level
   `compute_live_seed(broker_adapter, fund_manager, start_of_today_iso=None) -> float` — the
   expression is byte-identical except it now passes the floor argument.
2. **Derive the floor once:** `_start_of_today_iso = now_ist().replace(hour=0,…).isoformat()` computed
   a single time in `_main_locked`, before the `if paper/else` seed selection.
3. **Pass the same floor to both** boot call sites: `compute_live_seed(…, _start_of_today_iso)` (live
   only) and `fund_manager.rehydrate_from_open_trades(_start_of_today_iso)` (both modes).

**The hazard it closes (DESIGN B3):** the seed and rehydrate each derived their own `now_ist()` floor
~44 ms apart. An off-schedule boot straddling 00:00 (crash-restart / manual / systemd retry) would put
the two floors on opposite sides of midnight, so the seed's Σ and Phase-2's re-addition span different
day-row-sets and the cancellation leaves a residue of **−Σ(D)** (on a loss day: positive → `_total`
inflated ~0.2% → permissive daily-loss threshold, one self-correcting session). The scheduled 08:15
boot never straddles midnight; this only ever fires off-schedule.

`fund_manager` is **unchanged** — both callees already accept `start_of_today_iso`; their `None`-fallback
is kept for standalone/test callers.

### ⚠️ The A4 honesty note — how a second derivation is prevented
The single-floor guarantee at boot is **BY CONVENTION** at the two call sites, not structurally enforced:
the fund_manager helpers still derive their own floor when called with `None`, so a future edit that
passes `None` (or adds a third derivation) could reintroduce divergence. That convention is pinned by
`test_boot_derives_the_day_floor_once_and_passes_it_to_both`, which reads `main.py`, extracts the
day-floor variable passed to `compute_live_seed`, and asserts the SAME variable reaches
`rehydrate_from_open_trades` and is derived from `now_ist()` exactly once. Making it structural (one boot
helper doing both) would enlarge the boot-path diff for an astronomically-rare hazard — deferred.

---

## B. ⭐ An instruction contradiction, surfaced not papered over (A1 vs A2/A3)

The instruction's A1 asked to keep the whole-file regex pin green with **zero test edits**, while A2/A3
require the seed to pass the floor. The pin (`test_q9_post_restart_capital_wired.py:668`) matched
**empty parens** `today_realized_pnl_carryover\(\)`; passing the floor makes the call
`today_realized_pnl_carryover(_start_of_today_iso)`, which cannot match `\(\)`. The two requirements are
mutually exclusive against the actual regex — an artifact of the earlier no-floor B1 design that did not
survive folding in B5.

**Resolution (forced):** relax the pin `\(\)` → `\(` — one purpose-preserving edit; it still fails if the
subtraction is removed or the seed/rehydrate stop sharing `_today_release_used_pnl_rows`. Keeping empty
parens would have meant *not* passing the floor to the seed, i.e. not fixing the hazard. The behavioural
coverage now carries the weight: the wired `_live_seed()` calls the real `compute_live_seed`, and the new
T5 tests exercise it directly.

---

## C. The falsifying test — shown able to fail (DESIGN B6)

`tests/unit/test_mc1_live_seed_rehydrate.py`, scratch DB with real `RELEASE_USED` rows on day D:

| test | asserts | on HEAD (`8fdd286`) | on the fix |
|---|---|---|---|
| **T5a** divergent floors | seed floor D, rehydrate floor D+1 → `_total == broker.net − Σ(D)`, and `≠ broker.net` (anti-vacuity `Σ(D)≠0` first) | **green** (proves the hazard mechanism is real) | green |
| **T5b** single floor | one floor via the real `compute_live_seed` → `_total == broker.net` | **RED** (compute_live_seed absent) | **green** |
| **T5c** None-fallback | `carryover()` == `carryover(today-floor)` | green | green |
| **structural pin** | main() derives one floor, passes it to both | **RED** (pattern absent) | **green** |

**RED-on-HEAD demonstrated live:** reverting only `main.py` to `8fdd286` (with the new tests present)
→ `2 failed, 9 passed` — exactly T5b and the structural pin fail; T5a stays green, confirming the
divergence is a genuine failure mode, not a vacuous test.

---

## D. The gate — same-window double regression

`python -m pytest tests/unit tests/integration -q -p no:cacheprovider`, both runs ~20:2x–20:5x IST
(same window, same Tuesday, PC / cpython-3.11 / pytest-9.0.3).

| | BASE `8fdd286` | MERGE `151d3b4` |
|---|---|---|
| failed | 11 | 11 |
| passed | 5010 | **5014** |
| skipped | 4 | 4 |
| wall clock | 836 s | 829 s |

**`comm -23` MERGE-only = 0. `comm -13` BASE-only = 0. The failure SETS are identical** — the 10
documented PC-env failures (`test_main`×4, `test_order_placer_fix061`×4, `test_fix181`×1,
`test_phase17_batch2`×1) plus the time-gated `test_interactive_startup::test_holiday_guard_missing_yaml_proceeds`
(run outside 08:00–16:00). MERGE +4 passing reconciles exactly to the 4 new tests. **The gate passes.**

## E. Parity (§C3) — re-measured on the changed tree, not inherited
Paper's seed (`selected_account.paper_capital`) is untouched; rehydrate receives the shared floor in
**both** modes (same value as the old None-derived floor on any normal boot). The paper/live parity tests
(`test_T2_paper_untouched`, `test_T1c_multi_bucket_parity_to_paper`, `TestParity`,
`test_both_modes_use_the_same_restore_function`) all passed in MERGE. One calculator of the floor, one
`initialize` call site, one rehydrate — paper and live affected identically.

---

## F. Deploy record

| | |
|---|---|
| Merge / tag | **`e21cf9e`** / `deploy-21jul-b1-midnight-floor` |
| Behavioural identity | seed arithmetic byte-identical bar the floor arg; one extra stack frame; `now_ist()` called once for the boot floor instead of twice (that IS the fix); no ordering/error-handling/logging change |
| Service | **`inactive`** — NOT started; restarts itself after the 08:15 token refresh |
| Backup | `data_store/backups/pre_deploy_b1_midnight_floor_21jul.db` (`quick_check=ok`) |

### 🔴 ROLLBACK — written before it is needed
```bash
git revert -m 1 e21cf9e && git push origin main
```
Schema-free (v44 both ways), no data migration, nothing to unwind in the ledger. Minutes.

---

## G. ⚠️ Tomorrow's 08:15 boot — what it verifies, stated honestly (§C6)

The scheduled 08:15 boot is a **cold, flat, mid-morning** boot: Σ(today)=0 and nowhere near midnight, so
the floor divergence **cannot manifest**. Therefore:

- **It verifies the B1 EXTRACTION** (the risky part — §4 named it "the failure mode to guard against"):
  `fund_manager.rehydrated` must show the seed landing on `broker.net`, `replayed_pnl_rows=0`,
  `replayed_trades=0`, `run_all_startup_checks` clean, no capital-drift alert. Exactly Monday's C1 shape.
- **It does NOT exercise the midnight fix.** That is verified by the falsifying tests (T5a/T5b) and the
  structural pin — a rare-event guard a normal boot won't reach; saying otherwise would overclaim.

## H. Queue (unchanged, from §3/§5 — not in this batch)
Broker closing-capital ₹0 trace (`[3_Capital]` REVIEW) · wave-7 triage · the three W10 re-labels ·
prune #09 (Rama) · C3 items · security-watcher CRITICAL 1→2 glance.

*Off-market, book flat, service never started. The deploy is a code checkout; no data was written.*
