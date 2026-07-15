# 15-Jul Combined-Deploy Integration — **STATUS: 🔴 BLOCKED (STOP + REPORT)**

**Date:** 15-Jul-2026 (evening, off-market) · **Author:** VS Code Claude (integration task)
**Merged local main:** `894db27` — **UNPUSHED, UNTAGGED, NOT deploy-ready**
**VM == bare remote:** `2dc69d5` (unchanged — nothing pushed, nothing deployed)

> **Bottom line:** The two approved branches **merged cleanly** (overlap resolved, integration
> test green), but the **mandatory full combined regression is RED on 2 genuine (non-env)
> tests**. Per the task's own gate — *"if the COMBINED regression fails … STOP and REPORT,
> do not force-resolve or fix forward"* — I **stopped**: **no deploy tag created, no EOD
> dry-run run, no confidence gates run, nothing pushed.** The blocker is a **one-line
> test-only fix** (a stale mock from Branch B's F2), described in §4 with the exact patch,
> for Rama to approve before the integration can be certified green.

---

## 1. What was done (and is clean)

| Step | Result |
|---|---|
| Merge Branch B `monitoring-hardening-15jul` (`0a77c92`→`1c891b6`) | ✅ fast-forward |
| Merge Branch A `fixes-eodcleanup-screened-fmledger-15jul` (base `5c5e1fb`) | ✅ 3-way merge → `b749882` (parents `1c891b6` + `0c7a6dd`) |
| Overlap `scripts/generate_screened_stocks_csv.py` (A read-only path + B `_cron_main` functional status) | ✅ auto-merged, both change sets intact, verified (see §3) |
| Doc conflicts `PATHS.md`, `SYSTEM_MAP.md` (both edited the LIVE-STATE region) | ✅ resolved `--ours` + prepended combined-deploy note |
| Integration test Q4 (`test_integration_screened_csv_15jul.py`) | ✅ **1 passed** — asserts BOTH overlap behaviors simultaneously → `894db27` |
| Schema drift check (`EXPECTED_SCHEMA_VERSION`) | ✅ **44** unchanged — merge changed no schema |

**Merged tree = `894db27`** (merge `b749882` + Q4 integration test). Working tree clean.

---

## 2. Combined regression — **RED (blocker)**

Full suite on merged main `894db27`:

```
14 failed, 5048 passed, 15 skipped, 89 warnings in 844.30s (14:04)
```

**Causal classification (proven against pre-merge baseline `0a77c92`):**

| # | Failing test | On baseline `0a77c92`? | Verdict |
|---|---|---|---|
| 1 | `ops_dashboard/tests/test_isolation.py::test_c_venv_has_no_kiteconnect` | FAIL | pre-existing PC-env |
| 2 | `test_fix181.py::…::test_inflight_orphan_flattened_when_kill_active` | FAIL | pre-existing PC-env |
| 3 | `test_interactive_startup.py::test_holiday_guard_missing_yaml_proceeds` | FAIL | pre-existing PC-env |
| 4 | `test_main.py::TestBl15WebhookSecretRequired::test_paper_mode_does_not_require_webhook_secret` | FAIL | pre-existing PC-env |
| 5–7 | `test_main.py::TestContinueFromGate::{price_hit…, no_placer…, stats_placed…}` | FAIL | pre-existing PC-env |
| 8–11 | `test_order_placer_fix061.py::{first_valid_ltp, successful_retry, exhausted_retries, non_ltp_error}` | FAIL | pre-existing PC-env |
| 12 | `test_phase17_batch2.py::test_fix077_flask_max_content_length` | FAIL | pre-existing PC-env |
| **13** | **`test_fix135_fno_ban.py::TestCronMainHeartbeat::test_success_records_heartbeat_exit_0`** | **PASS** | **🔴 REAL — branch-introduced** |
| **14** | **`test_fix135_fno_ban.py::TestCronMainHeartbeat::test_warn_failure_records_heartbeat_exit_0`** | **PASS** | **🔴 REAL — branch-introduced** |

**Method:** the 14 node-IDs were re-run on the pre-merge tip `0a77c92` (detached HEAD,
then returned to `main`). Result there: **12 failed, 2 passed.** The 12 fail *identically*
before the merge → pre-existing PC-env noise (memory `pc_test_env_hygiene.md`: ~32 known
PC-only env failures, green on VM). The **2** that **pass on baseline but fail on merged
main** are the genuine regression. This is a clean, exhaustive split — no other test changed
state.

---

## 3. The overlap file resolved correctly (for the record)

`scripts/generate_screened_stocks_csv.py` carries **both** branches:
- **Branch A (M-SC2b):** `main()` reads via `db_connect.connect_readonly(db_path)` (URI
  `mode=ro` + `query_only=ON`); `get_traded_symbols(conn, date)` / `get_non_traded_symbols(conn, date)`
  take a raw connection. The old `store.transaction(readonly=True)` TypeError path is gone
  (the only remaining `transaction(readonly=True)` occurrence is a *comment* at line 266).
- **Branch B (F2):** `_cron_main` sets `timer.functional_status` from `_csv_functional_status()`
  (OK / EMPTY_NO_DATA / FAILED / MISSING / UNKNOWN from the CSV artifact).

The Q4 integration test (`tests/unit/test_integration_screened_csv_15jul.py`, **1 passed**)
asserts both **simultaneously** on the merged code: the read-only connection reads correctly
**and** rejects writes (A), and `_csv_functional_status()` returns OK / EMPTY_NO_DATA from the
generated artifact (B). A merge that dropped either side fails this test.

**The overlap is NOT the blocker.** The blocker (§4) is unrelated to this file.

---

## 4. 🔴 The blocker — Branch B (F2) shipped a stale test mock

**Symptom** (both failing tests):
```
utils/cron_heartbeat.py:147 in __exit__ → record_heartbeat(…)
TypeError: TestCronMainHeartbeat.heartbeats.<locals>._capture()
           got an unexpected keyword argument 'functional_status'
```

**Root cause — a signature mismatch between production and a pre-existing test double:**
- **Production (Branch B / F2, commit `795a417`)** added a new `functional_status` kwarg to
  `record_heartbeat(...)` and made `HeartbeatTimer.__exit__` (`utils/cron_heartbeat.py:147‑154`)
  pass it on **every** exit.
- **Test (pre-existing, commit `5057425` — untouched by either branch)**:
  `tests/unit/test_fix135_fno_ban.py::TestCronMainHeartbeat.heartbeats` monkeypatches
  `record_heartbeat` with a local stub whose signature predates F2:
  ```python
  def _capture(job_name, status="SUCCESS", duration_sec=None, message=None, db_path=None):
  ```
- `fetch_fno_ban._cron_main` runs `with HeartbeatTimer(...)` → `__exit__` calls the patched
  `record_heartbeat(..., functional_status=...)` → the stub rejects the unknown kwarg → `TypeError`.

**Blast radius:** exactly these 2 tests (the only `HeartbeatTimer`-under-a-stale-mock pattern in
the suite; every other heartbeat test — including Branch B's own new ones — passed). **The
production F2 code is correct.** This is a **test-only** staleness.

**Why it wasn't caught earlier:** Branch B's Task-5 build ran the monitoring-focused + new
tests, not the whole suite. `test_fix135_fno_ban.py` is an **unrelated** F&O-ban test that
happens to exercise the shared `HeartbeatTimer`, so its stale stub only surfaces under a full
regression — which is exactly what this integration step mandates. **Working as designed: the
gate caught it.**

**Exact fix (one line, test-only) — NOT applied (fix-forward forbidden by the task gate):**
```diff
- def _capture(job_name, status="SUCCESS", duration_sec=None, message=None, db_path=None):
+ def _capture(job_name, status="SUCCESS", duration_sec=None, message=None,
+              functional_status=None, db_path=None):
```
`_capture` is defined once in the `heartbeats` fixture and used by both failing methods, so
this single line clears **both** failures. No production change; no schema change.

---

## 5. Why I stopped (and what I did NOT do)

The task states verbatim: *"If the overlap conflict is non-trivial, or the COMBINED
regression fails, or the EOD dry-run errors: **STOP and REPORT. Do not force-resolve or fix
forward.**"* and *"NO NEW CODE. This is integration only."*

The combined regression failed on 2 genuine tests. Even though the fix is a trivial test-only
one-liner, applying it **is** fix-forward and **is** a code change beyond the single permitted
Q4 artifact — and this is a **deploy-gating** decision that is Rama's to make. Therefore:

**NOT done (deliberately gated on a green regression):**
- ❌ No annotated tag `deploy-15jul-combined` created.
- ❌ No EOD-pipeline dry-run on a DB copy (§ was fully prepared — harness plan in §7 — but not run).
- ❌ No confidence gates (`deploy_assert.py` / `PRAGMA integrity_check` / `foreign_key_check`).
- ❌ Nothing pushed. Nothing deployed. No live restart. No schema change. Live DB untouched.

**Local state:** `main` = `894db27` (the clean merge + Q4 test), **unpushed, untagged**.
VM == bare == `2dc69d5`, unchanged.

---

## 6. Recommended path forward (Rama's call)

**Recommended (A):** authorize the one-line stale-mock fix in §4 as a **Branch-B follow-up
commit on the merged main**, then re-run the full regression (expect **14 → 12 failed**, the 12
being the known PC-env set), then resume this integration at step 3e (EOD dry-run on a copy) →
3f (gates) → 3g (tag `deploy-15jul-combined`) → pre-push checklist → runbook. Lowest-risk;
production code is already correct.

**Alternative (B):** kick the mock fix back to a dedicated Branch-B hygiene cycle and re-approve
Branch B before re-attempting the combined merge. Cleaner provenance, slower.

Either way the merge commits (`b749882`, `894db27`) are sound and can be kept — the only
outstanding work is the §4 one-liner + a re-run.

---

## 7. EOD dry-run harness (prepared, NOT run — for use once unblocked)

Design (single isolated process, **never touches the live DB**):
- Copy **both** `data_store/trading_system.db` **and** `data_store/analytics.db` (the ATTACHed
  analytics DB) into a scratch `data_store/` so `StateStore`'s ATTACH resolves against copies.
- **Neutralize live side-effects in-process** before running any step:
  `utils.cron_heartbeat.record_heartbeat` → no-op (else `eod_verify`/`eod_broker_reconcile`
  `main()` write a heartbeat to the **live** DB), and `TelegramNotifier.from_env` → `None`.
- Call each step's **`main(["--db", <copy>, …])` directly** (bypassing the `__main__`/`_cron_main`
  wrappers, which also heartbeat to live): `reconcile_pnl --dry-run`, `reconcile_positions --dry-run`,
  `eod_cleanup` (real, to exercise Branch-A's children-first FK-safe prune), `eod_verify`,
  `eod_broker_reconcile --mode paper`.
- `generate_screened_stocks_csv` and `wal_checkpoint` **hardcode the live path** (relative to
  `__file__`, not cwd) → exercise them against the copy via their internal functions
  (`connect_readonly(copy)` + `generate_csv(out=scratch)`; `StateStore(copy).checkpoint_wal()`).
- After all steps: `PRAGMA foreign_key_check` on the copy → must be **empty**; then delete the copy.

Confidence gates (§3f), once unblocked: `python scripts/deploy_assert.py` (defaults to live but
is **strictly read-only** `mode=ro` — cannot migrate) must exit 0; plus `PRAGMA integrity_check`
+ `foreign_key_check` on live via a read-only connection.

---

*No live system state was changed by this task. Read-only throughout, except local git merge
commits on `main` (unpushed) and this report.*
