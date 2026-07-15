# 15-Jul Combined-Deploy Integration — **STATUS: 🟢 CLEARED (ready for Rama's push)**

**Date:** 15-Jul-2026 (evening, off-market) · **Author:** VS Code Claude (integration task)
**Merged local main:** `32389c0` · **Deployment tag:** `deploy-15jul-combined` → `1645bd6`
**State:** UNPUSHED · **VM == bare remote:** `2dc69d5` (unchanged — nothing pushed/deployed)

> **Bottom line:** The two approved branches merged cleanly, the one genuine regression the
> combined suite caught (a stale test mock from Branch-B/F2) was traced to **test-only**
> staleness — **no production incompatibility** — fixed with `create_autospec`, and locked out
> for the future with a signature-lock + discovery-guard. Every deploy-integrity gate is green;
> the annotated rollback tag exists. **Nothing is pushed** — handed to Rama for the deploy
> runbook (§8).

This report supersedes the earlier BLOCKED revision. History (STOP → resume) is in §7.

---

## 1. Final state

| | |
|---|---|
| Merged `main` HEAD | `32389c0` |
| Deployment tag | `deploy-15jul-combined` (annotated `278e6fd`) → **`1645bd6`** (the fully-gated code HEAD) |
| Merge commit | `b749882` (parents `1c891b6` Branch B + `0c7a6dd` Branch A) |
| Schema | **v44**, unchanged (merge + fixes drifted nothing) |
| VM == bare | `2dc69d5` (22 commits behind local `main`; nothing pushed) |
| Working tree | clean |

---

## 2. Compatibility audit (read-only) — **NO production-side incompatibility**

Branch-B/F2 inserted a new optional `functional_status` param into `record_heartbeat`
(at position 4, **before** `db_path`). Every surface was enumerated and classified:

| Audit item | Finding |
|---|---|
| a. Definitions | **ONE** canonical `record_heartbeat` (`utils/cron_heartbeat.py:55`). No shadow/duplicate. |
| b. Production callers (~30) | **All COMPATIBLE.** Every caller passes `db_path` **by keyword** or omits it → inserting `functional_status` before `db_path` misbinds nothing; the param is optional (default `None`) so callers that omit it are unaffected. |
| c. Wrappers | `HeartbeatTimer.__exit__` passes `functional_status` **by keyword** (F2, correct); `reconstruct_excursions._record_heartbeat` forwards `db_path` by keyword, no `functional_status` → defaults. Both COMPATIBLE. |
| d/e. Test mocks | **Exactly two.** `test_fix135_fno_ban.py::_capture` = **STALE** (froze the old param list). `test_gemini_log_review_hardening.py` `lambda *a, **k` = COMPATIBLE but non-enforcing. |
| f. Reader side | `parse_functional_status` — one production consumer (`cron_officer.py:188`). COMPATIBLE. |
| g/h. Indirect forwarders | **None.** No `partial()` / `functools` / decorator / adapter / lambda freezes the old signature anywhere. |

**Gate result:** only test mocks affected → this is a test fix, not a production fix. Proceeded.

---

## 3. Fixes + prevention (test-only; no production or schema change)

- **Both mocks → `create_autospec(record_heartbeat)`** (`397f3f6`): the stand-ins are now built
  from the real function, so they enforce its live signature and cannot silently drift. Each
  test's assertions preserved (adapted the captured-list checks to `mock.call_args`).
- **Signature-lock contract test** (`1645bd6`, `tests/unit/test_cron_heartbeat_contract.py`):
  pins the exact parameter list/order/defaults (incl. `functional_status` **before** `db_path`,
  optional) — any future change fails here and forces updating the lock + the mocks together.
- **Discovery-guard test** (same file): scans the test tree for every `record_heartbeat` patch
  site and requires a signature-enforcing stand-in (`create_autospec` / `autospec=True`) or an
  explicit `lambda *a, **k`. **Proven to have teeth** — flags the old `_capture`, passes the
  autospec/permissive forms, and does not false-positive on the ~30 real callers.
- **Interface Change Checklist** engineering pattern (`32389c0`,
  `docs/monitoring_prevention_checklist.md`, referenced from `docs/SYSTEM_MAP.md`) + an explicit
  **scope lock** on the discovery guard (intentionally limited to `record_heartbeat`, no
  AST/repo-wide scanner this cycle).

---

## 4. Overlap file — both branches intact (Q4 integration test)

`scripts/generate_screened_stocks_csv.py` carries **both** change sets (Branch A read-only
`db_connect.connect_readonly` path + Branch B `_cron_main` functional-status from
`_csv_functional_status`). The Q4 integration test
(`tests/unit/test_integration_screened_csv_15jul.py`, `894db27`) asserts both **simultaneously**
on the merged code (**1 passed**): the read-only connection reads correctly and rejects writes
(A) **and** `_csv_functional_status()` returns OK / EMPTY_NO_DATA from the artifact (B).

---

## 5. Deploy-integrity gates — **all GREEN**

| Gate | Result |
|---|---|
| **a. Full combined regression** (merged main) | **`5052 passed, 12 failed, 15 skipped`** in 841s. `14 → 12`: the 2 F2 heartbeat tests now pass. The **12 remaining are EXACTLY the known PC-env set** (order_placer×4, test_main×4, fix181, interactive_startup, flask, ops_dashboard venv-isolation) — identical to the pre-merge baseline `0a77c92`; **zero new**. |
| **b. EOD dry-run on a DB COPY** (never live) | All 7 steps `OK`: reconcile_pnl, reconcile_positions, **eod_cleanup(real — Branch-A FK-safe prune)**, eod_verify (VERIFIED), **eod_broker_reconcile (exercises fm_ledger `ledger_id` fix — no "no such column: id")**, generate_screened (read-only path), wal_checkpoint. **`foreign_key_check` CLEAN + `integrity_check` ok** on the copy. Copy deleted. Live side-effects neutralized in-process (record_heartbeat → no-op; Telegram → None). |
| **c. deploy_assert.py** (read-only `mode=ro`) | **rc = 0** (blockers clear): quick_check ok · foreign_key_check clean · schema_version live=44 expected=44 · monitoring.email_delivery ok (WARNING class). *(Note: on the Windows PC console the CLI's ✅-emoji print raises `UnicodeEncodeError` under cp1252 → exit 1; under UTF-8 — as on the VM — it exits 0. Cosmetic, VM-unaffected; the authoritative `run_assertions()` rc is 0.)* |
| **d. PRAGMA integrity_check** (live, read-only) | **ok** |
| **e. PRAGMA foreign_key_check** (live, read-only) | **empty (clean)** |
| **f. Deployment tag** | `deploy-15jul-combined` created at `1645bd6` (annotated; single-tag rollback point). |

---

## 6. Pre-push checklist — all ticked

- [x] **Working tree clean** — `git status` clean; on `main` `32389c0`.
- [x] **SHAs recorded** — merged main `32389c0`; tag `deploy-15jul-combined` → `1645bd6`; VM==bare `2dc69d5`.
- [x] **Fresh DB backup available** — Rama's command before push:
      `sqlite3 data_store/trading_system.db ".backup data_store/backups/pre_deploy_15jul_combined.db"` (VM-side, off-market).
- [x] **Deploy/migration ledger updated** — `UNPUSHED_PENDING_DEPLOY_LEDGER` = merged main + tag UNPUSHED; VM still `2dc69d5`.
- [x] **SYSTEM_MAP.md updated** — LIVE-STATE header reflects the CLEARED state + tag + Interface Change Checklist.
- [x] **Canary job registered** — `monitoring_canary` in `config/cron_registry.yaml`: `schedule: 08:20 daily`, `monitored: true`.
- [x] **deploy_assert passes** — rc 0 (blockers clear).
- [x] **DB integrity passes** — live `integrity_check` ok + `foreign_key_check` empty; schema v44.
- [x] **Full combined regression green** — only the 12 known PC-env failures remain.

---

## 7. History — STOP → resume (for the record)

1. Merge built clean; Q4 test green; **but the mandatory combined regression was RED on 2 real
   tests** (`test_fix135_fno_ban::TestCronMainHeartbeat`). Per the task gate (regression red →
   STOP, no fix-forward) the run **STOPPED** with a BLOCKED report — no tag, no dry-run, nothing
   pushed. Baseline `0a77c92` proved the split: 12 pre-existing PC-env + 2 real.
2. ChatGPT + Web Claude approved the resume path (audit → confirm test-only → autospec → contract
   + discovery guard → gates → tag). Executed here: audit found **no production issue**; the two
   mocks were autospec'd; prevention added; the full regression went **14 → 12**; the dry-run,
   gates, and tag all completed green.

---

## 8. Rama's deploy runbook (off-market, in order — NOT executed here)

1. **F0 — restore `ALERT_SMTP_PASSWORD`** (fresh Gmail app-password in `.env`). Stops the
   alert_watcher loop + resumes email/sentinel delivery. Independent of the push.
2. **PUSH merged `main` → VM** (one push; the docs commits ride along, inert). Take the fresh
   backup (§6) first.
3. **VERIFY DEPLOY:** post-receive hook landed in `/home/ubuntu/systems/trading-system/`; bare
   HEAD == `32389c0`; schema v44; canary (08:20) registered; services healthy. (Tag pushes with
   `git push --tags` or `git push origin deploy-15jul-combined`.)
4. ***ONLY AFTER step 3*** — **PHASE-B PRUNE (supervised):** fresh live backup →
   `eod_cleanup --signal-retention-days 7` (or `30`) to clear the ~108k fingerprint backlog →
   capture before/after → `foreign_key_check` clean → `trades` UNCHANGED. (Default 90d prunes 0
   now.) Do **not** run Phase-B before verifying the deployed code.
5. **NEXT-SESSION VERIFY (next EOD):** `eod_cleanup` + `generate_screened_csv` PASS; canary
   reports the four paths (email green once F0 done); functional status visible in the Cron
   Officer; the check_cron_drift false-alarm gone.

**Rollback — two levels:** **L1** per-branch revert (+ off-market restart, as one unit;
schema-free, no data loss). **L2** single-tag rollback: reset to `deploy-15jul-combined`'s first
parent if an unexpected interaction appears post-deploy.

---

*No live system state was changed by this task. Read-only throughout, except local git commits
+ the annotated tag on `main` (unpushed) and this report. The EOD dry-run touched only a deleted
copy.*
