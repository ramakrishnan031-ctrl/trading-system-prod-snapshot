# M-C CAPITAL-SAFETY CLUSTER — consolidation, combined regression, sandbox hard_kill drill

**Date:** 16-Jul-2026, ~21:36–22:0x IST (off-market)
**Tag (the CODE point + rollback anchor):** `deploy-16jul-mc-cluster` → **`341cc57`**
**local `main`:** `2c7e05d` = `341cc57` + this docs commit (inert)
**Status:** VALIDATED + DRILLED. **UNPUSHED. Awaiting Rama's off-market deploy authorization.**
**Deployed VM/bare: `11abebb` — untouched.** No schema change.

> **⚠️ At deploy time, re-derive the SHA — do not trust the one written here.** Docs
> commits ride on top of the tag, so `main`'s HEAD will very likely have moved on again
> by the time the deploy runs. **The TAG is the code identity, not the branch SHA.**
> This exact trap already fired once today: the 16-Jul alert-watcher/F1 deploy
> instruction pinned `f68d15d` and `main` had advanced two docs commits past it. The
> right check is `git diff --name-only deploy-16jul-mc-cluster..HEAD` → **markdown
> ONLY** ⇒ the code being deployed IS the drilled, regression-green tag.

---

## 1. State at start

| | SHA |
|---|---|
| local `main` (pre-merge) | `16437ae` |
| **VM == bare (DEPLOYED)** | **`11abebb`** |
| M-C4 `mc4-killswitch-lock-16jul` | `1a91585` (fix `6c77525`) — merge-base `f68d15d` |
| M-C8 `mc8-async-hardkill-16jul` | `8f9ce0d` — merge-base `16437ae` |
| M-C5/M-C6/test_main `mc5-mc6-testmain-16jul` | `2154675` — merge-base `16437ae` |

Tree clean. M-C4's merge-base is *older* than main (main had advanced with docs-only
commits), so it was a real 3-way merge; the other two were current.

---

## 2. Merge + overlap resolution → `341cc57`

Three `--no-ff` merges: `c0c9376` (M-C4) → `8bc685a` (M-C8) → `341cc57` (M-C5/M-C6/test_main).

**Overlap map** (files touched by >1 branch): `docs/SYSTEM_MAP.md` ×3, `PATHS.md` ×2,
**`capital/kill_switch.py` ×2 (M-C4 + M-C8)** — the one that mattered.

### 2.1 `capital/kill_switch.py` — M-C4 ∩ M-C8: AUTO-MERGED, then VERIFIED

Git auto-merged it (the two fixes live in different regions: M-C4 in
`record_api_failure` + the class docstring; M-C8 in the constants/enum/`__init__`/
`hard_kill`/`_run_cancel`/the worker/the write helpers). **"Auto-merged" is not
"correct"**, and this is the emergency path, so both fixes were verified explicitly —
M-C4 by **AST**, not grep:

```
record_api_failure: the only `with self._lock` block is at line 730
                    soft_kill calls INSIDE that block = 0
                    soft_kill call site = line 750  → OUTSIDE the lock
⇒ M-C4 INTACT
```

| Property | Verified |
|---|---|
| M-C4: `soft_kill` called OUTSIDE the lock (never held across publish/Telegram send) | ✅ AST-proven above |
| M-C8: `_flatten_lock` is a **separate** `threading.Lock` (`:245`), NOT the KS4 `RLock` (`:221`) | ✅ |
| M-C8: kill STATE tripped **under `self._lock`** before dispatch; `_run_cancel()` called **outside** it | ✅ |
| M-C8: worker / single-flight / drain / `is_flatten_in_progress` present | ✅ |
| M-C8: conditional-write constants present | ✅ |
| All four fixes present in merged main (kill_switch, main.py, fund_manager, position_sizer, test_main) | ✅ |
| `py_compile` all four modules · `config_loader.load_all()` | ✅ COMPILE OK / CONFIG_LOAD_OK |

The two locks are **complementary, not conflicting**: M-C4 shrinks the scope of the KS4
`RLock`; M-C8 deliberately avoids that lock entirely for the flatten state. M-C8's own
code comments cite M-C4 as the reason for the dedicated lock — they were designed to
coexist, and they do.

### 2.2 Docs conflicts (trivial, keep-both) — plus two stale claims corrected

`SYSTEM_MAP.md` (twice) and `PATHS.md` (once) conflicted on adjacent banner blocks.
Resolved keep-both. While resolving, **two now-false statements inherited from the M-C8
branch were corrected rather than merged forward**:

- *"the async change landed with **no test rewritten**"* → **FALSE** (the full suite
  disproved it: `test_p0_live_day1_fixes::TestBugC_KillSwitchExit` drove `hard_kill`
  with an adapter and broke). Replaced with what is true, plus the trap: *a test that
  drives `hard_kill()` with an adapter gets a DISPATCH, not a result — `drain_flatten()`
  first, then assert.*
- *"25 new tests (8 RED-on-old)"* → **27 / 9** (the thread-start and drain-honesty guards
  landed after that banner was written).

---

## 3. Fresh combined regression (merged main `341cc57`) — **GREEN**

Run 21:41–21:55 IST against the committed merge, verified to have no uncommitted `.py`:

```
11 failed, 4763 passed, 15 skipped in 812.83s (0:13:32)
```

**All 11 are the known time-gated PC-env set** (11 after 16:00 IST) — `test_main` ×4,
`test_order_placer_fix061` ×4, `test_fix181`, `test_interactive_startup`,
`test_phase17_batch2`. That is the *exact* set attributed earlier today against the true
pre-M-C8 tree (`mc8_async_hardkill_16jul2026.md` §6). **ZERO new failures.**

The pass count also cross-checks the merge: the M-C5/M-C6 branch alone ran 4735 passed;
+27 (M-C8's suite) +1 (M-C4's new concurrency test) ≈ **4763** — i.e. every branch's
tests are present in the merged tree, none lost to a merge.

### 3.1 No behaviour divergence (the §4 gate)

Each fix behaves in the MERGED tree exactly as it did under isolated per-branch
validation:

| Suite | merged | isolated |
|---|---|---|
| M-C8 `test_mc8_async_hardkill` | **27 passed** | 27 passed |
| M-C5 `test_mc5_commit_adopted_cas` | **5 passed** | 5 passed |
| M-C6 `test_mc6_zero_multiplier_skip` | **7 passed** | 7 passed |
| M-C4 `test_kill_switch` | **48 passed** | 48 passed |
| `test_main` | **4 failed / 72 passed** | 4 failed / 72 passed |
| all cluster-touching suites together | **276 passed** | — |

**No divergence.** Attribution rule used throughout: any anomaly is attributed against
the **true pre-change tree** (`git checkout main -- <files>` + `grep -c` to confirm the
change is *absent*) — **never `git stash`**, which only stashes uncommitted work and
silently leaves the change on both sides of the comparison.

The run's `PytestUnhandledThreadExceptionWarning: TypeError: '>' not supported between
instances of 'MagicMock' and 'int'` is **pre-existing and benign** — already attributed
by A/B-ing `main`'s own `test_main` (fixture provably absent → same warning count).
Not a failure; unrelated to this cluster.

---

## 4. SANDBOX hard_kill DRILL — **35/35 PASS**

**Safety.** Mock broker (no network, no Zerodha, no real orders) + a **fresh scratch DB
built from `core/schema.sql` in a temp dir**. The live store is never opened — asserted
in code (`assert "data_store" not in str(path)`) and confirmed externally: the live DB's
mtime was **identical before and after** the drill (`21:41:53.250357800`). Harness:
`scratchpad/mc_cluster_drill.py` (validation tooling, deliberately **not** committed —
this task adds no code to the repo).

| # | Check | Result |
|---|---|---|
| 1 | `hard_kill` **returns immediately** while the flatten runs | ✅ returned in <0.5s |
| 1 | the returned report **declares the dispatch** (`dispatched=True`, not a fake result) | ✅ |
| 1 | kill **STATE tripped SYNCHRONOUSLY** — `is_active("entry"/"exit")` blocks the instant it returns | ✅ |
| 1 | the flatten really runs on the worker; worker is **NON-DAEMON** and named | ✅ |
| 2 | **SINGLE-FLIGHT**: a repeat hard_kill (re-entrant) spawns no second worker | ✅ |
| 2 | **SINGLE-FLIGHT**: a cross-thread hard_kill spawns no second worker | ✅ |
| 3 | state machine **IDLE → RUNNING → DRAINING → COMPLETE** | ✅ all four observed |
| 3 | `is_flatten_in_progress()` False once COMPLETE | ✅ |
| 3 | the position is **actually flattened** at the broker | ✅ `SELL 10`, net → 0 |
| 4 | after COMPLETE a later emergency **dispatches a fresh flatten** (not disarmed) | ✅ 2 orders |
| 5 | **CONCURRENT FILL**: broker already flat → **no exit fired, no naked short** (FIX-190 A) | ✅ `placed=[]` |
| 5 | broker truth authoritative — net stays 0, **no oversell** | ✅ |
| 5 | the trade is still marked `EXITING` (marked without re-firing) | ✅ |
| 5 | **conditional write**: `UNKNOWN_IN_FLIGHT` is **not** clobbered to `EXITING` | ✅ |
| 6 | **eod-self-exit REFUSES to fire mid-flatten** — with the store reporting **0 active** | ✅ the EXITING-blind race, closed |
| 6 | external-SIGTERM: bounded grace expires → **CRITICAL "positions may remain open"** | ✅ |
| 6 | `_shutdown` **joins** the worker (no orphan flatten) | ✅ |
| 7 | **thread-start failure → the flatten runs INLINE** (never dropped) | ✅ `SELL 10`, net → 0 |
| 7 | state honest (COMPLETE, not wedged at RUNNING) | ✅ |
| 8 | **M-C4**: `is_active()` **not blocked** while the auto-trip's notifier send is in flight | ✅ gate took **0.000s** |
| 8 | the soft_kill still activated | ✅ |
| 9 | **M-C5**: two concurrent NON-GATING commits → the loser is a **clean no-op** | ✅ 1 COMMIT row |
| 9 | **no spurious hard_kill** fired from the race | ✅ |
| 9 | the winner's commit completed (reserved → used) | ✅ |
| 10 | **M-C6**: zero multiplier **SKIPS** (no 1-lot placed) | ✅ |
| 10 | negative multiplier **SKIPS** | ✅ |
| 10 | **FIX-133 PRESERVED**: a tiny positive multiplier still floors to 1 lot | ✅ |
| 10 | a normal positive multiplier is unchanged | ✅ |
| 11 | **3-balance capital invariant holds** — at init, after reserve, **after the flatten**, and after the M-C5 race | ✅ intraday 70000==70000, positional 30000==30000 |

**VERDICT: PASS — NO capital-path invariant violation.**

### 4.1 The first drill run failed 10/35 — and it was the HARNESS, not the code

Recorded because "the drill went red" is exactly the moment to be rigorous rather than
quick. First run: **25/35**, with `placed=[]` — the flatten fired *no orders at all*.

That contradicted the 27 passing M-C8 unit tests (one of which asserts a full end-to-end
flatten), so the harness was suspected first and **diagnosed before touching anything**:
`OrderManager.create_trade` leaves **`qty_filled = 0`** (status `PENDING_FILL` — not yet
filled), and the flatten *correctly* skips `local_qty == 0`: **an unfilled trade has no
broker position to flatten.** The drill had seeded positions that didn't exist. All ten
failures cascaded from that one seeding error (plus one bad assertion reading
`RacingBroker.net_qty` instead of its overridden `get_positions()`).

**No product code was changed** (tree clean = 0 throughout); the harness was fixed to
seed `qty_filled` and re-run → 35/35. **This was never a STOP condition**: the stop
criterion is a capital-path invariant violation, and every invariant check passed in
*both* runs.

---

## 5. Deliverables

- Merged main **`341cc57`**; tag **`deploy-16jul-mc-cluster`** → `341cc57`.
- This report. Per-fix detail: `mc4_killswitch_lock_16jul2026.md` ·
  `mc8_async_hardkill_16jul2026.md` · `mc5_mc6_testmain_16jul2026.md`.
- **NOTHING pushed. NOTHING deployed.**

---

## 6. OFF-MARKET DEPLOY RUNBOOK (executed later, on Rama's authorization only)

Off-market = past 17:05, before 08:15; never 09:00–15:30 or 15:30–17:05.

1. **Fresh VM backup** — `sqlite3 data_store/trading_system.db ".backup data_store/backups/pre_deploy_mc_cluster.db"`; confirm non-empty + `integrity_check` ok.
2. **Push** — `git push origin main` and `git push origin deploy-16jul-mc-cluster`.
3. **Verify deploy** — bare HEAD == **the local `main` HEAD confirmed at run time** (NOT a SHA copied from this report). The gate that actually matters: `git diff --name-only deploy-16jul-mc-cluster..HEAD` returns **markdown only** ⇒ the deployed code IS the drilled tag `341cc57`. Then: post-receive checkout landed in `/home/ubuntu/systems/trading-system/`; **schema v44 unchanged (no migration)**; `integrity_check` ok + `foreign_key_check` clean; services healthy.
4. **Completion checklist** — all four fixes present in the checked-out tree; working tree clean; **no code modified during deploy**.
5. **Memory/ledger only AFTER verified.**
6. **Note:** `trading-system.service` is currently HALTED on a planned operator SOFT_KILL and **auto-clears at the next 08:15 boot** (`main.py:1607` `clear_stale_state` wipes any prior-day kill) — the cluster goes live at that boot. No restart is owed by this deploy.

**ROLLBACK** — all schema-free, no data migration:
- **L1**: revert an individual fix commit (the four are independent).
- **L2**: revert a whole `--no-ff` merge (`c0c9376` / `8bc685a` / `341cc57`).
- **L3**: reset main to `16437ae` (pre-cluster) — the deployed code point `11abebb` + docs.

---

## 7. Standing caveats carried into the deploy

- **M-C8 changes the EMERGENCY path.** The drill exercises it under a mock broker; it
  cannot prove real-broker latency behaviour. The first live HARD_KILL after this deploy
  is the real test.
- **M-C6 inverts a documented FIX-133 decision** (`perf_weight=0` → skip, was → 1 lot).
  Ratified. Behaviour-neutral today (`min_weight=0.5` clamp); it becomes load-bearing the
  moment `min_weight` is lowered.
- **KS6's "re-runs cancellation" is now path-specific** — preserved for the legacy
  `cancel_fn` path, single-flight for the adapter path (production).
</content>
