# alert-watcher LOOP FIX + morning findings — 16-Jul-2026

**Status:** IMPLEMENTED + TESTED (local repo). **NOT pushed. NOT deployed.** Deploy is a
separate OFF-MARKET step (Section 4). Written during market hours (trading service HALTED for
the day, planned pause).

**Scope:** switch `alert-watcher.service` from the `--once` + `Restart=always` systemd respawn
loop to the EXISTING `--loop` daemon + `Restart=on-failure` (Option a, ChatGPT-approved), after
an investigate-first checklist; add a regression check so a respawning alert-watcher can never
read HEALTHY; record findings 2–4 + the broker-truth runbook + two register captures.

---

## Phase 1 — INVESTIGATE-FIRST checklist (read-only) — RESULT: **PASS → proceed with Option (a)**

### (a) Unit location — **repo-managed**
`deploy/systemd/alert-watcher.service` is in the repo and installed by
`deploy/install_vm_services.sh` (`cp … /etc/systemd/system/` → `systemctl enable`). ⇒ edit the
repo unit, deploy off-market via the normal push + install path. Its paths already use the
correct live tree (`/home/ubuntu/systems/trading-system`, venv `/home/ubuntu/systems/venv`).

### (b) `--loop` production-readiness (code review of `run_loop`/`main`) — **SOUND**

| Check | Verdict | Evidence |
|---|---|---|
| **Memory/handle leak** | **No leak** | `ThreadPoolExecutor` is in a `with` block (closed each pass); SMTP `server.quit()` in `finally`; the log `FileHandler` is created once (`if not log.handlers`) in `main()` before the loop; `counters`/`smtp_state` pruned+bounded each pass; the Telegram fallback uses stateless `requests.post(timeout=…)` (no persistent session). Only unbounded-ish item: the **date-embedded log** is opened once at start (named for the start date) and grows within a run — but F1 removed the per-pass 535 spam so growth is now proportional to real alert volume, and `log_cleanup` (`-mtime +30`) still reaps it. **Not a leak.** |
| **Poll interval** | **OK (60s)** | `alerts.watcher_interval_sec` is a real config field (default **60**, unset in YAML). Sleep is `stop_event.wait(interval)` — interruptible, not a busy-loop. Cadence changes from the old ~10s effective (RestartSec) to 60s; **acceptable** because the email sentinel is the BACKUP channel (criticals also emit direct-to-Telegram at creation). Tunable via one YAML line if tighter latency is wanted. |
| **Graceful shutdown** | **OK** | `main()` installs SIGTERM+SIGINT → `stop_event`; the loop's interruptible `wait()` wakes immediately; lock released in `finally`. Unit `KillSignal=SIGINT`. **Hardening applied:** `TimeoutStopSec` 10→**35** (> `_SMTP_TASK_TIMEOUT_SEC`=30) so a worst-case in-flight SMTP batch drains before SIGKILL. |
| **Single-instance** | **OK (stronger than `--once`)** | Pidfile lock (`watcher_lock_path`) acquired once in `main()` and **held for the whole `--loop` lifetime**; a live pid → exit 0. **Confirmed NO second invocation exists:** no `alert-watcher.timer`, no cron entry, no other `.sh` — the only installer is `install_vm_services.sh` (one `.service`). |
| **Survives transient failures** | **OK** | F1 makes `run_once` return 0 on ALL delivery/auth faults → the loop never dies on SMTP/network/Telegram errors; backoff + Telegram fallback + degraded marker operate every pass. Startup config error → `main()` returns 1 → `Restart=on-failure` retries (correct). *Note:* the `rc==2` stop-branch in `run_loop` is now **vestigial** (F1 means `run_once` no longer returns 2) — harmless defensive guard, left as-is. |
| **systemd rate-limits** | **Added** | `StartLimitIntervalSec=300` + `StartLimitBurst=5` in `[Unit]`: >5 failed starts in 300s trips the limiter → unit enters `failed` (LOUD, caught by the canary), instead of hammering. Tolerates a handful of legit restarts; a real crash-loop stops+alerts. `RestartSec` stays 10. |

### (c) Dependency on the `--once` respawn — **none breaks**
- `scripts/preflight/checks/services.py` whitelists `activating` as healthy for alert-watcher
  ("periodic-oneshot … spend most of their time in activating"). Post-fix the daemon is
  `active (running)` (already in `HEALTHY_STATES`) ⇒ the check **still passes**; only its
  rationale comment was stale → **updated** (that tolerance now serves security-watcher; respawn
  detection moved to the canary). `test_preflight_groups.py` still green.
- `SYSTEM_MAP.md` service row said rising `NRestarts` is "NORMAL … NOT a crash-loop" → **flipped**.
- Nothing else keys off the restart frequency (canary checks sentinel backlog/marker; cron_watchdog
  checks DB heartbeats).

**No STOP-gate trigger.** `--loop` is production-ready → Option (a) implemented.

---

## Phase 2 — Implement + Test (local; NO deploy)

### 3.1 Unit change — `deploy/systemd/alert-watcher.service`
| | Before | After |
|---|---|---|
| ExecStart | `alert_watcher.py` (default `--once`) | `alert_watcher.py **--loop**` |
| Restart | `always` | **`on-failure`** |
| TimeoutStopSec | 10 | **35** (drain in-flight SMTP batch) |
| StartLimit | (none) | **`StartLimitIntervalSec=300` + `StartLimitBurst=5`** |
| KillSignal / RestartSec | SIGINT / 10 | unchanged |

### 3.2 Respawn-never-HEALTHY regression check + test
**Gap found:** the monitoring canary's `check_sentinel_ingestion` only checks the F1 degraded
marker + `.flag` backlog — a respawning `--once` watcher still delivers each pass, so it read
**healthy**; and **nothing anywhere read `NRestarts`/`SubState`** (grep: zero usages repo-wide).
So the canary could (and did, silently) call a service respawning ~every 10s "healthy."

**Fix (minimal, scoped to respawn detection):** new pure classifier `_classify_respawn` +
injectable probe `check_service_respawn` in `scripts/monitoring_canary.py`, wired as a **5th
canary path** (`respawn`) into `run_canary`/`overall_ok`/report:
- **NOT-healthy** when `SubState=auto-restart` (a stable daemon is never mid-restart at sample
  time) **OR** `NRestarts` climbed by ≥3 at a rate >6/hr since the previous daily canary sample
  (persisted in `data_store/canary_service_state.json`).
- **Benign** on first run (baseline), counter reset (redeploy), or 1–2 legit restarts in a short
  gap (no false alarm). A systemctl/parse failure **degrades to healthy-with-note** (a
  respawn-check outage is not itself an alert-path failure) rather than firing a spurious WARNING.

**Test (fail-on-old / pass-on-new):** 9 tests in `test_monitoring_canary.py` — the imports don't
exist pre-fix (fail-on-old); post-fix they assert the incident shape (~8640 restarts/24h → **not
healthy**), auto-restart → not healthy, stable daemon → healthy, single-restart → no false alarm,
baseline/reset benign, and the wrapper detects a seeded loop + advances the baseline + degrades
on systemctl-missing. All pass.

### 3.3 `--loop` functional tests (`test_alert_watcher.py::TestRunLoopFunctional`)
- `test_loop_stays_up_across_real_clean_passes` — real `run_once` over 3 passes, temp sentinel
  dir, 2 sentinels → both `.delivered`, loop exits 0 (no crash/respawn).
- `test_loop_survives_smtp_auth_via_telegram_fallback` — SMTP auth fails inside the loop → loop
  does **not** stop (rc 0), sentinel delivered via **Telegram fallback**, `alert_watcher_degraded.json`
  written. (Single-instance is the pidfile lock in `main()`, already covered by `TestLockFile`.)

### Test results
```
python -m pytest tests/unit/test_alert_watcher.py tests/unit/test_monitoring_canary.py -q
  → 56 passed  (baseline 45 + 11 new)
python -m pytest tests/unit/test_preflight_groups.py -q  → 11 passed
py_compile: monitoring_canary.py, alert_watcher.py, preflight/checks/services.py → OK
```

---

## 3.4 Findings 2–4 + prevention

### F2 (paths) — **VERIFIED: PATHS.md + SYSTEM_MAP.md already correct; no correction needed there**
Both files already describe the live tree correctly — running tree
`/home/ubuntu/systems/trading-system`, venv `/home/ubuntu/systems/venv`, bare
`~/trading-system.git` (PATHS.md:6/74/89/95; SYSTEM_MAP.md:207/233/343/511). PATHS.md line 6 was
corrected this morning (morning-verify). **Residual stale-path sites (flagged, NOT auto-edited —
deploy-doc `.env` placement warrants a VM `ls` confirm off-market):**
- `DEPLOYMENT.md` (lines 113/126/128/136/163) still points `.env` + backups at
  `/home/ubuntu/trading-system/…` (missing `systems/`). The live unit reads
  `EnvironmentFile=/home/ubuntu/systems/trading-system/.env` and SMTP works, so DEPLOYMENT.md is
  stale — **recommend Rama confirm + correct off-market.**
- The stale duplicate `deploy/post-receive` (wrong `/home/ubuntu/trading-system`) — already a
  documented record-don't-fix footgun (SYSTEM_MAP:509; the canonical `deploy/hooks/post-receive`
  + live hook are correct).

### F3 (broker-truth flatten runbook) — **ADDED to SYSTEM_MAP.md as MANDATORY**
8-step "broker-truth verification before every manual intervention" procedure (stop engine →
verify stopped → read BROKER positions → compare vs DB → flatten ONLY broker-open → verify zero
positions → verify zero pending orders → restart only when intended). Rationale recorded: on
16-Jul the DB read ACI OPEN for minutes after its SL had filled → a DB-based flatten would have
opened a **naked short**; Zerodha regular orders have no broker-side OCO so a manual action races
the exit-manager unless the engine is stopped first. **NEVER flatten from DB state alone.**

### F4 (informational) — boot auto-cleared the 15-Jul SOFT_KILL as designed
The 16-Jul first boot's `clear_stale_state` wiped the prior-day soft-kill
(`circuit_breaker_force_close_15:15`, order_monitor) — **by design** (prior-day kills auto-clear
at 08:15). **Action: confirm-benign in the 15-Jul EOD review** (no unexpected re-arm).

---

## 3.5 Register captures (recorded, NOT fixed)
1. **No live halt/flatten API.** KillSwitch is loaded at boot only (in-memory); an intra-session
   stop requires **stop → set-kill → start → HALT (exit-4)**. A running-process live-kill/flatten
   capability is a **separate future architecture decision** (not built). Recorded in the SYSTEM_MAP
   broker-truth section.
2. **Process lesson.** Pre-market time-sensitive tasks need more runway than ~4 minutes — today's
   ~09:56 kill task lost its window to SSH round-trips + live-tree path discovery, so the system
   ran a live 10:00 session (3 clean trades, book flat). Schedule such tasks with margin.

**Also noted (not changed, out of scope):** `run_loop`'s `rc==2` branch is vestigial post-F1;
`test_alert_watcher.py::run_all_tests()` (the `__main__` standalone runner only, NOT the pytest
path) references a stale method name `test_smtp_auth_error_exits_2` (renamed to
`…_no_longer_crashes`) — harmless under pytest.

---

## 4. OFF-MARKET DEPLOY RUNBOOK  (SEPARATE — run only after 17:05, before next 08:15)
1. Merge branch `alertwatcher-loop-fix-16jul` → `main` (repo's branch-per-fix pattern; 3 commits:
   unit fix · canary respawn probe · docs), then `git push origin main` off-market.
2. On the VM: reinstall the unit → reload → restart **only** alert-watcher:
   ```bash
   sudo cp /home/ubuntu/systems/trading-system/deploy/systemd/alert-watcher.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl restart alert-watcher.service
   ```
   (Monitoring service only — does NOT touch trading-system.)
3. Confirm single long-running process (not respawning):
   ```bash
   systemctl show alert-watcher -p ActiveState -p SubState -p NRestarts -p ExecMainPID
   #   expect: ActiveState=active  SubState=running  NRestarts stops climbing
   systemctl status alert-watcher --no-pager     # one PID, uptime grows
   ```
4. **POST-DEPLOY 24h VALIDATION:** watch RSS + fd/handle count over the first 24h
   (`ps -o rss= -p <PID>`, `ls /proc/<PID>/fd | wc -l`). No growth expected. If it grows → ROLLBACK.
5. Next daily canary (08:20) records the `respawn` baseline, then flags any future respawn.

**ROLLBACK:** revert the unit change (`ExecStart` back to no-flag, `Restart=always`, `TimeoutStopSec=10`,
drop StartLimit) → `daemon-reload` → `restart alert-watcher`, off-market. The crash is already fixed
by F1, so rollback is loop-only (no functional loss). L2: `git revert` the fix commits.

---

## Files changed (UNPUSHED)
- `deploy/systemd/alert-watcher.service` — `--loop` + `on-failure` + StartLimit + TimeoutStopSec.
- `scripts/monitoring_canary.py` — `respawn` probe (`_classify_respawn` + `check_service_respawn`), 5th path.
- `scripts/preflight/checks/services.py` — rationale comment (behavior unchanged).
- `tests/unit/test_monitoring_canary.py` (+9), `tests/unit/test_alert_watcher.py` (+2).
- `docs/SYSTEM_MAP.md` — service row flipped + MANDATORY broker-truth section.
- `docs/audit/alertwatcher_loop_fix_16jul2026.md` (this report).

**Done = investigate PASS · unit prepared · respawn-regression + `--loop` functional tests pass ·
findings 2–4 + 8-step broker-truth runbook + 2 register captures recorded · deploy runbook ready ·
NOTHING pushed/restarted during market hours.** Rama deploys off-market tonight.
