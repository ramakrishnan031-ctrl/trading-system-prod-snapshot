# 16-Jul Combined Deploy — Consolidation (alert-watcher + F1) — read-only integration

**Status:** MERGED to local `main` + TAGGED. **NOT pushed. NOT deployed.** One combined off-market
deploy for tonight (§4). Integration only — **no new code**; both branches preserved intact.

## State
| | SHA | Note |
|---|---|---|
| VM == bare (`origin/main`) | `c9fb298` | 15-Jul combined deploy LIVE (schema v44). Instruction's "2dc69d5" was stale. |
| pre-merge `main` | `3b9041a` | c9fb298 + 3 docs-only commits |
| BRANCH 1 `alertwatcher-loop-fix-16jul` | `681b078` | base `daa36cd` (main diverged by 1 = 3b9041a) |
| BRANCH 2 `f1-trades-sector-observe-16jul` | `3eafa90` | base `3b9041a` (= main; linear) |
| **MERGED `main`** | **`1d5337d`** | alert-watcher merge (`1d5337d`) over F1 merge (`e8226db`) over `3b9041a` |
| **TAG** | **`deploy-16jul-alertwatcher-f1` → `1d5337d`** | annotated; single-tag rollback anchor (local, unpushed) |

## Merge (both `--no-ff` → per-branch merge commits for clean L1 rollback)
1. `git merge --no-ff f1-trades-sector-observe-16jul` → `e8226db` (F1 is linear over main → conflict-free).
2. `git merge --no-ff alertwatcher-loop-fix-16jul` → `1d5337d` (3-way off `daa36cd`).

### Config overlap — AUTO-MERGED cleanly (no STOP)
Both touch `core/config_loader.py` but **different classes** → git auto-merged, **both field sets present**:
- BRANCH 1: `AlertsConfig.respawn_restart_delta_threshold` (3) + `respawn_rate_per_hour_threshold` (6.0).
- BRANCH 2: `RiskConfig.sector_cap_mode` (observe) + `sector_unknown_alert_pct` (0.20) + validators.
`config/system_config.yaml` was touched by F1 only (BRANCH 1 kept its respawn thresholds override-only)
→ no YAML merge needed. Config model validates (config_loader test suite green).

### Only conflict = `PATHS.md` (trivial doc, resolved keep-both)
The top banners overlapped: HEAD (main) carried the operator-docs + F1 banners; the alert-watcher branch
carried an **updated** respawn-finding line. Resolved by keeping HEAD's two banners **and** taking the
alert-watcher branch's updated finding (`--ours` + a one-line finding swap). No markers remain; SYSTEM_MAP
+ test_config_loader auto-merged.

## Combined regression (full suite on merged `main`) — CLEAN
```
python -m pytest tests/unit tests/integration -q   →  4700 passed / 10 failed / 15 skipped (12m41s)
```
**The 10 failures are ALL PRE-EXISTING PC-env** — verified IDENTICAL on the pre-merge base `3b9041a`
(same 10 tests, same `Mock`-subscriptable / `int(Mock)` / thread-timing artifacts; green on the VM).
**ZERO new failures from the merge.** (test_main×4, test_order_placer_fix061×4, test_fix181×1,
test_phase17_batch2×1 — all mock-harness/env, not real.)

## Self-consistency gates (merged `main`) — ALL PASS
- `deploy_assert.py` → **rc=0** (blockers clear): quick_check ok · foreign_key_check clean ·
  schema_version live=44 expected=44 · email_delivery ok (WARNING). *(A first run hit the known Windows
  cp1252 `✅`-emoji `UnicodeEncodeError` → rc=1 cosmetic; `PYTHONIOENCODING=utf-8` → rc=0. VM exits 0.)*
- `PRAGMA integrity_check` → **ok** · `PRAGMA foreign_key_check` → **CLEAN (0)** · schema **v44** (both
  branches schema-free — no migration).

---

## §4 UNIFIED OFF-MARKET DEPLOY RUNBOOK (NOT executed now — after 17:05, before 08:15)
1. **One push:** `git push origin main` (+ `git push origin deploy-16jul-alertwatcher-f1`). Both branches
   + docs land in one push. **Deploy ≠ restart.**
2. **Verify deploy:** bare HEAD == `1d5337d`; hook checked out to `/home/ubuntu/systems/trading-system/`;
   schema v44; services healthy.
3. **ALERT-WATCHER track (monitoring; does NOT touch trading-system):**
   ```bash
   sudo cp /home/ubuntu/systems/trading-system/deploy/systemd/alert-watcher.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl restart alert-watcher.service
   systemctl show alert-watcher -p ActiveState -p SubState -p NRestarts -p ExecMainPID
   #   expect: active / running / NRestarts stops climbing
   ```
   **SOAK 24h:** RSS · fd (`ls /proc/<PID>/fd|wc -l`) · threads (`ps -o nlwp=`) · DB handles — none may
   grow; canary green incl. the new `respawn` probe. Rollback if any grows.
4. **F1 track (BEHAVIOUR-NEUTRAL on deploy — trades.sector fills; gate-8 `sector_cap_mode=observe`
   = log-only):** OBSERVE SOAK ≥ 1 full trading session. Collect + ARCHIVE: UNKNOWN-sector %, WOULD_REJECT
   count, sector distribution, DQ-alert count, any unexpected concentration. Extend to a 2nd session only
   on anomalies. → Then a SHORT EVIDENCE REPORT, and **ONLY after Rama's EXPLICIT approval** flip
   `risk.sector_cap_mode: observe → enforce` (off-market) to activate the live 40% sector cap.
5. **Next-EOD sanity:** eod_cleanup / screened_csv pass; canary all green (incl. respawn guard);
   check_cron_drift false-alarm gone.

**ROLLBACK:** L1 per-branch revert of the merge commit — `git revert -m 1 1d5337d` (alert-watcher) and/or
`git revert -m 1 e8226db` (F1); off-market `daemon-reload`+restart for the unit. L2 full de-integration —
reset `main` to `3b9041a` (pre-both-branches). The tag `deploy-16jul-alertwatcher-f1` (→ `1d5337d`) is the
named reference for the exact deployed code (docs ride on top, inert). **F1 enforce is an INSTANT CONFIG
de-fang** (`sector_cap_mode → observe`) — no revert needed to de-fang the sector cap.

---

**Done = merged clean `main` (`1d5337d`), config overlap auto-resolved (both field sets), PATHS trivial
conflict resolved · combined regression 4700 pass / 10 known-PC-env (zero-new, verified on 3b9041a) ·
deploy_assert rc=0 + integrity ok + FK clean + schema v44 · tag `deploy-16jul-alertwatcher-f1` ·
unified runbook ready · NOTHING pushed.** Rama runs the runbook off-market tonight.
