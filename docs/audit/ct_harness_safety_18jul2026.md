# CT harness safety — the live-DB hazard, fixed by construction (18-Jul-2026)

The crash-test harness could open the **LIVE trading database WRITABLE**. The tests it
serves are destructive by design (corrupt state, cancel trades, reset capital), so a
writable live handle had to become *impossible*, not merely discouraged.

**Deployed:** code commit **`6b7a74f`**, tag **`deploy-18jul-ct-harness-safety` → `6b7a74f`**.
PC == VM bare == tag. **Test-infra only — zero production files changed.**
**The six parked destructive CTs are now UNBLOCKED but deliberately NOT RUN.**

---

## §2 Investigation

### Q1 — the recorded premise was HALF RIGHT (corrected)

The note said *"`ct_utils.py:63` hardcodes the LIVE database, opened WRITABLE, as a
MODULE-LEVEL CONSTANT — merely importing the harness acquires a writable handle on
production data."*

| Claim | Verdict | Evidence |
|---|---|---|
| The live DB path is hardcoded as a module constant | ✅ **TRUE** | `DB_PATH = BASE_DIR / "data_store" / "trading_system.db"` (base `ct_utils.py:63`) |
| The harness opens it **WRITABLE** | ✅ **TRUE** | `def get_db_connection(readonly: bool = False)` (`:75`) → `sqlite3.connect(str(DB_PATH))` (`:82`). The default is writable. |
| **Importing acquires the handle** | ❌ **FALSE** | Import only computes a `Path`. No `sqlite3.connect` runs at module level; the connection opens when the function is *called*. |

**Empirically proven on the true base tree** (throwaway worktree at base HEAD, dummy DB at
the hardcoded path so nothing real was touched):

```
OLD ct_utils.DB_PATH    = <base>/data_store/trading_system.db   ← the live-DB formula
OLD get_db_connection() = WRITABLE  (CREATE TABLE succeeded, no arguments passed)
```

So the hazard was real but its mechanism was **a default-writable function plus an
ambiguous name**, not import-time acquisition. That distinction shaped the fix: guarding
the import would have missed it entirely.

### Q2 — who imports it, and who was actually dangerous

19 import sites across the harness. Sorted by real risk:

| Risk | Sites |
|---|---|
| 🔴 **Writable live handle** | `cleanup.py:52,125,139` → `get_db_connection()` (no args) — and `do_hard()` runs `UPDATE trades SET status='CANCELLED'`, cancels orders, resets capital · `test_capital_invariant_violation.py:21` |
| 🔴 **Live path into StateStore** (writable + migration-on-open risk) | `test_double_release.py:25`, `test_fund_manager_edges.py:56`, `test_kill_switch_edges.py:36`, `test_position_sizer_edges.py:48` — all `StateStore(db_path=DB_PATH)`. **Not in the original note.** |
| 🟢 Read-only diagnostics (legitimate) | `invariant_checker.py:363`, `exactly_once_verifier.py:150`, `forensic_reconstructor.py:234/266/278`, `idempotency_tester.py:31` — all `readonly=True` |
| 🟢 File metadata only | `state_inspector.py:120-128` (`.exists()`, `.stat()`, WAL path) |

**On import alone, nothing touches the live DB** — confirming the Q1 correction. Pytest
collects only 6 tests here, all from `test_ramcoind_oversell_prevented.py`, which already
used `tmp_path` and never imported `ct_utils`. **The full suite was therefore never at
risk; the exposure was direct invocation of the harness tools.**

### Q3 — has it ever fired? **YES — with evidence**

`reports/crash_test/cleanup_log.jsonl` (on the VM) records real writes:

```
2026-06-05T16:15:15  soft_cleanup  ... "Released 37 orphan reservations"
2026-06-05T16:17:54  soft_cleanup  ... "Kill switch cleared (1 rows updated)"
2026-06-07T16:56:21  hard_cleanup  ... "Cancelled 0 open trades", "Cancelled 0 pending
                                        orders", "Reset capital..."
```

"1 rows updated" and "Released 37 orphan reservations" are **actual mutations of the live
database**. Reported without alarmism: these fall inside the deliberate crash-test window
(Days 1–6, early June; the DB then held little live trade data — the closed book starts
15-Jun), so they were very likely intentional at the time. **The point is not that harm
occurred — it is that nothing prevented it.**

### Q4 — what the parked CTs need (does scratch satisfy them?)

| CT | Needs | Scratch OK? |
|---|---|---|
| CT114 Disk Full | fill disk, running system | ✅ not DB-bound |
| CT127 Clock Backward | clock skew, running system | ✅ not DB-bound |
| CT130/132/133 | state manipulation + inspection | ✅ scratch carries the real schema |
| CT135 Cleanup During Trading | verify `cleanup.py --hard` refuses without `--force` | ✅ tests the gate logic, not the target DB |

**A scratch DB genuinely satisfies them.** It is built from the project's own
`core/schema.sql`, so it is structurally identical to live — constraints included (proven:
the guard test's `INSERT` had to satisfy the real `NOT NULL` set *and* a foreign key).

### Q5 — existing idiom to reuse

Yes, and **inside the harness itself**: `ct_day3_isolated.py:50-54` already did
`StateStore(db_path=<scratch>, schema_path=core/schema.sql)`. The unit suite uses the
pytest `tmp_path` + `_make_store` pattern (`test_order_reconciler.py:95`). The fix
**promotes the harness's own pattern into `ct_utils`** rather than inventing a mechanism.

---

## §3 The fix — scratch-safe by construction

**The ambiguous name *was* the bug, so `DB_PATH` was DELETED, not repointed.** A silently
repointed constant would be a new trap; an `ImportError` is loud and safe. Callers must
now say which database they mean:

* **`LIVE_DB_PATH` / `LIVE_ANALYTICS_DB_PATH`** — read-only diagnostics and the guard's
  comparison target. `state_inspector` / `invariant_checker` keep their **exact** previous
  semantics.
* **`SCRATCH_DB_PATH` / `make_scratch_db()` / `scratch_db_path()`** — every writable or
  destructive operation. Real schema; auto-bootstrapped.
* **`assert_not_live_db(path)` — the hard guard.** Raises `LiveDatabaseRefused`.
  **No override flag, no environment escape hatch. Fails closed.**
* **`get_db_connection(readonly, db_path)`** — `readonly=True` still reads the live DB
  (`mode=ro`, safe by construction: SQLite refuses writes); `readonly=False` routes through
  the guard and defaults to scratch.

**How the guard survives the foot-guns:** it compares `os.path.normcase(os.path.realpath(
os.path.abspath(os.path.expanduser(p))))` — which absorbs relative paths, `..`, `~`,
symlinks and Windows case — then *additionally* `os.path.samefile()` when both exist,
which is the only thing that catches a **hardlink** (realpath cannot resolve one). It also
refuses the `-wal` / `-shm` sidecars, since writing those corrupts the live DB just as
surely.

**`cleanup.py --live` now REFUSES.** Its purpose was resetting the *live* system; silently
resetting scratch while reporting success would be worse than stopping. Resetting the real
system is an **operator** action and belongs in `scripts/` with its own safeguards — not in
a destructive test harness. **⚠️ This is a deliberate capability removal, flagged for
Rama's decision, not a silent one.**

---

## §4 Proofs

**RED-on-old** — new tests copied onto a throwaway `git worktree` at base HEAD `163014d`
(**never `git stash`**; rc-checked): collection fails with
`ImportError: cannot import name 'LIVE_ANALYTICS_DB_PATH'`. Base blob greps confirm all
four new symbols absent (`assert_not_live_db`, `LiveDatabaseRefused`, `make_scratch_db`,
`SCRATCH_DB_PATH` = 0 occurrences each), and `DB_PATH`/writable-default present.

**The guard, proven route by route** (16 tests):

| Foot-gun | Result |
|---|---|
| absolute live path | refused |
| live **analytics** DB | refused |
| **relative** path resolving to live | refused |
| `..`-segment path | refused |
| `-wal` / `-shm` sidecars | refused |
| **`CT_SCRATCH_DIR` env override** aimed at live | refused (+ a test proving the env var really is the override route) |
| **symlink** → live | refused *(ran on the VM; skipped on Windows, which needs privilege)* |
| **hardlink** → live | refused *(the case `realpath` cannot resolve — only `samefile` catches it)* |
| default-writable `get_db_connection()` | refused |
| a scratch path | allowed |

**Capability preserved:** insert a real trade into scratch (satisfying the true `NOT NULL`
set and its FK parent), then run the very statement `cleanup.py` aimed at live —
`UPDATE trades SET status='CANCELLED'` — and observe it applied. Destructiveness intact.

**⭐ LIVE-DB UNTOUCHED — on the real VM, against the real production database:**

```
16 passed in 1.42s          ← zero skips: the symlink AND hardlink routes both executed
data_store/trading_system.db   IDENTICAL (size + sha256)
data_store/analytics.db        IDENTICAL (size + sha256)
VERDICT: LIVE DBs UNTOUCHED on the real VM
```
Same result on the PC around the whole `tests/crash_test` suite (20 passed, 1 skipped):
DB, analytics and `-wal` all identical by size + SHA-256. *Honest nuance:* the
`trading_system.db-shm` **mtime** moves, because any `mode=ro` reader must map SQLite's WAL
shared-memory index. Its **content is unchanged** and no data is written — the DB files
themselves are byte-identical.

Scratch output is contained in `data_store/ct_scratch/` (git-ignored); the live
`data_store/` still holds only the two real DBs, and the hardlink probe is cleaned up.

---

## §5 Regression + deploy

**Full suite (NOT scoped): 11 failed, 4933 passed, 5 skipped (778s).** New-failure set
versus the 18-Jul base baseline (41F/4877P at `a9cc41d`) is **EMPTY ⇒ ZERO ATTRIBUTABLE**.
The 11 are the known PC-env / Saturday calendar-gated set (`daily_trade_review`,
`order_placer_fix061` ×4, `fix181`, `phase17_batch2`, heavy `test_main.py` classes). Counts
reconcile: 4918 + 15 new guard tests = 4933. **No crash_test failures.**
*(The run was restarted after the final two test edits so the validated tree is exactly the
committed one.)*

**Deploy:** backup `pre_deploy_ct_harness_20260718.db` (+ analytics) — **verified sound**:
`quick_check=ok`, schema v44, 361 trades. Pushed → post-receive checkout + crontab
auto-install OK. **Verified: PC == VM bare == tag == `6b7a74f`**; the deployed tree really
contains the guard (`assert_not_live_db` ×1) and the old constant is gone (`^DB_PATH` ×0);
**schema v44 unchanged, integrity ok, 0 FK violations** (identical to pre-deploy — a
test-only change); services correct (`trading-system` inactive as expected on a down
Saturday, `alert-watcher` and `gui-dashboard` active).

**No runtime behaviour change of any kind** — nothing in this commit is imported by the
trading process.

The push also carried three earlier docs-only commits (`5c2f2c0`, `4df01b6`, `163014d`) per
the standing "fold into the next deploy" pattern.

---

## Open follow-ups (not done here)

1. **The six destructive CTs (CT114/127/130/132/133/135) are UNBLOCKED but NOT RUN.**
   Running them is a separate, deliberate, Rama-aware step (sandbox / off-hours).
2. **`cleanup.py`'s live-reset capability was removed.** If Rama wants it, it belongs in
   `scripts/` as an operator tool with a backup + confirmation gate — his decision.
3. `ct_day3_isolated.py` writes its scratch DBs into `data_store/` directly rather than
   `data_store/ct_scratch/`. Untidy but not a live-DB hazard; left alone (out of scope).
