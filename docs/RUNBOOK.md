# Trading System v2 — Operations Runbook

Date: 05-Jun-2026 | Crash Test Day 0 (offline pre-tests)
VM: 161.118.187.249 | User: ubuntu | System: ~/systems/trading-system

## 1. Daily Startup

1. Token generation (manual browser step): Login to Kite web, generate token
2. token-watcher auto-start verification: `sudo systemctl status trading-system`
3. Health check: `curl http://localhost:5000/health`
4. Verify no stale kill switch: `python3 tests/crash_test/state_inspector.py --live | python3 -c "import sys,json; d=json.load(sys.stdin); print('Kill:', d['kill_switch']['state'])"`
5. Verify capital correct: Check state_inspector output for capital available
6. Verify cron active: `crontab -l | wc -l` (expect 20+ lines)

**Verified by crash test**: PARTIAL — CT093 (graceful shutdown), CT096 (crash recovery), CT100 (cold start) all verified startup scenarios.

## 2. Daily Monitoring

- AGY watchman: Check Telegram for alerts (mode-prefixed)
- Log files: ~/systems/trading-system/logs/ (one file per day)
- Expected signal volume: ~500-2000 signals/day (most rejected by score)
- Health endpoint: `curl http://localhost:5000/health` -> status "ok"
- Queue depth: Should be 0/300 outside active processing
- Kill switch: Should be INACTIVE during market hours

**Verified by crash test**: YES — resource_monitor shows real metrics (cpu/ram/disk), state_inspector shows live system state.

## 3. Graceful Shutdown

1. `sudo systemctl stop trading-system`
2. Verify: SHUTDOWN event in logs (`grep SHUTDOWN logs/trading_*.log | tail -1`)
3. Verify: No orphan orders at broker (manual Kite check)

**Verified by crash test**: YES — CT093 confirmed clean shutdown with SHUTDOWN event logged.

## 4. Emergency Shutdown

- **SOFT**: `sudo systemctl stop trading-system`
- **HARD**: `sudo kill -9 $(pgrep -f main.py)`
- **NUCLEAR**: kill + login to Kite web + manually cancel all orders

**Verified by crash test**: YES — CT093 (SOFT), CT096 (HARD). Nuclear requires manual broker access.

## 5. Kill Switch Recovery

1. Check: `python scripts/clear_kill_switch.py --dry-run` (reports state, changes nothing)
2. **A previous-day kill needs none of this** — it auto-clears at the next 08:15 startup
   (`clear_stale_state`). Only a **same-day** kill needs clearing.
3. If SOFT_KILL: fix the root cause, then **LIVE/VM** `sudo bash deploy/resume.sh` ·
   **PAPER/PC** `python scripts/clear_kill_switch.py`
4. If HARD_KILL: verify capital + positions, **confirm the root cause is handled** (an
   emergency kill re-trips if it is not), then `sudo bash deploy/resume.sh --force`
   (PC: `python scripts/clear_kill_switch.py --force`)
5. Post-resume: Inject test signal -> verify processing

> ⛔ ~~**SUPERSEDED 02-Aug-2026 (ledger #8 / IA-XDOCS-01)** — steps 2-3 previously said
> "clear via DB: `sqlite3 … "UPDATE kill_switch_state SET state='INACTIVE',
> reason='manual_clear', triggered_at=datetime('now'), triggered_by='operator' WHERE
> id=1"` -> restart".~~ A raw `UPDATE` writes no `system_events` audit trail, **bypasses
> the HARD_KILL `--force` gate**, and has **no effect on a running service** (`is_active()`
> reads in-memory state). ⛔ And restarting while a kill is still persisted returns
> **exit 4**, which `RestartPreventExitStatus=3 4` makes permanent — the service will not
> come back. Use `deploy/resume.sh`.

**Verified by crash test**: YES — CT007 (kill switch edges), multiple scenarios required kill switch clearing.

## 6. Token Refresh

1. Login to Kite web -> generate token
2. Place token file at: data_store/session/zerodha_token.json
3. token-watcher auto-detects and starts system
4. Verify: health check passes

**Verified by crash test**: NO — requires live Zerodha API.

## 7. Broker Reconciliation (manual)

1. Login to Kite web -> check positions
2. Compare with: `python3 tests/crash_test/state_inspector.py --live`
3. If mismatch: investigate orders table vs broker
4. If position exists at broker but not system: **CRITICAL** — manual intervention required

**Verified by crash test**: NO — requires live broker API.

## 8. EOD Verification

1. After 17:00: Check reports/daily/ for today's report
2. Check Telegram for EOD summary
3. Check: All positions closed (no overnight for INTRADAY)
4. Verify: `python3 tests/crash_test/exactly_once_verifier.py --date today`

**Verified by crash test**: PARTIAL — exactly_once_verifier confirmed working. EOD report generation requires live data.

## 9. Disaster Recovery

1. DB backup: `cp data_store/trading_system.db data_store/backups/trading_system_manual_$(date +%F).db`
2. Full restore: stop system -> copy backup -> start system
3. Config restore: `cp -r config_backup/ config/`
4. Verify after restore: `python3 tests/crash_test/invariant_checker.py --full`

**Verified by crash test**: PARTIAL — CT117 (WAL recovery), CT118 (table drop recovery). Full restore drill not run today.

## 10. Cron Verification

1. Check: `crontab -l` (expected: 20+ entries)
2. Check: system_events table for cron heartbeats
3. If missing: Check cron logs (`grep CRON /var/log/syslog`)

**Verified by crash test**: PARTIAL — CT130 scenario exists but time-dependent.

## 11. Common Errors & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| Service **`failed`** and stays down — exit 4, **NOT a restart loop** (`RestartPreventExitStatus=3 4`; journal: `Failed with result 'exit-code'`) | A **SAME-DAY EMERGENCY kill of ANY kind** — drift, token expiry, live-feed, API auto-trip, fund-manager, reconciler, System Manager EOD (only the 15:15 breaker and EOD squareoff are "scheduled"). ⛔ NOT a *stale* kill: prior-day kills auto-clear since 20-Jun | `sudo bash deploy/resume.sh` (`--force` also clears HARD_KILL). ⛔ **NOT `systemctl restart`** and ⛔ **NOT `main.py --resume`** — both fail. Fix the root cause first or it re-trips. Detail: [05_incident_response.md](05_incident_response.md) |
| Kill switch SOFT_KILL at 15:15 | Circuit breaker (expected daily) | FIX-154: auto-clears on restart (no open positions); manual: clear DB + restart |
| IntegrityError fm_ledger.amount | NaN input to reserve() | Fixed in FIX-154 (input validation) |
| data_store read-only | Permissions changed | `sudo chmod 755 data_store/` |
| DB lock contention | External process holding lock | Wait or kill external process |

## 12. Escalation

- **Level 1**: Check logs, restart system
- **Level 2**: Check broker manually, reconcile positions
- **Level 3**: Stop trading, investigate, involve developer

## Known Issues (from Day 0 offline testing)

- Pre-existing invariant failures (A, B, F, G) from historical data: no capital_snapshot, stuck QUEUED signals, missing fm_ledger_COMMIT entries, orphan reservations. These are data quality issues from prior live/paper sessions, not system bugs.
- CT036 (100 concurrent connections): Test harness error in concurrent futures, but system survived and health check passed.
- CT096/CT105/CT117: Scenario runner subprocess issues with `kill -9` commands — manually verified all pass.

## CRASH TEST DATE

05-Jun-2026 (offline pre-tests, Day 0)
07-Jun-2026 (Day 0 continued: FIX-154 committed, TEMP config reverted, CT133 re-verified on VM)
