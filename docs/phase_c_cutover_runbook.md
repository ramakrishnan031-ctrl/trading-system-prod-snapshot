# Phase C — Report-generator cutover runbook (deploy + first-run + rollback)

**Date prepared:** 2026-07-01 (IST). **Prepared by:** VS Code Claude. **Executed by:** Rama (OFF-MARKET).
**Change:** cut the EOD report over from the old generators to the redesigned DB-pure
`reports/daily_trade_review.py`. Retire `daily_review.py`; keep `daily_report.py` in parallel for a
short bake-in.

> **DEPLOY WINDOW:** OFF-MARKET only. NOT during market hours (09:15–15:30) and NOT during the EOD
> report/reconcile window (15:30–17:05). Evening (after ~17:10) or a weekend/holiday is ideal.
> **DB BACKUP FIRST** — this branch carries W0's schema **v40 → v41** migration (adds `config_snapshots`),
> which runs the first time the new code opens the DB (the manual run in step 3, or the next 08:15 boot).

---

## What changed (files in this deploy)

Report redesign (all of it lands in this push — W0 + 7 sheets + Phase-B.1 fixes):
- `reports/daily_trade_review.py` — the new generator (+ Phase-B.1 fixes; + Phase-C `main()`:
  `--date` now defaults to today IST, and it records a `cron_heartbeat("daily_trade_review")`).
- `core/config_snapshotter.py`, `core/migrations.py`, `core/schema.sql` — W0 `config_snapshots` (v41).
- `reports/daily_report.py` / `reports/daily_review.py` — **unchanged** (kept for parallel run / rollback).

Cutover (this phase):
- `config/cron_registry.yaml` — `daily_review` set `enabled: false` (RETIRED, entry kept); new
  `daily_trade_review` job added at **16:07 Mon-Fri** (monitored, `env_wrapper: python`).
- `deploy/cron/trading-system.cron` — regenerated canonical (daily_review line gone; daily_trade_review
  line in; daily_report kept). Pure ASCII+LF; byte-identical to `generate(registry)`.
- `core/cron_registry.py` — `expected_heartbeat_jobs` now skips **disabled** jobs (so the retired
  daily_review is never "expected" → no false MISSED from the Cron Officer).
- `scripts/system_manager.py` — added an EOD deliverable check for `daily_trade_review_report_<day>.xlsx`
  (daily_report.xlsx check kept for the bake-in).
- Tests: `test_daily_trade_review.py` (+entrypoint test), `test_cron_registry.py` (+cutover expectations).

**Cron is repo-managed:** the post-receive hook regenerates the crontab from the *deployed* registry,
checks it byte-equals `deploy/cron/trading-system.cron`, and **auto-installs** it. So the cron change
deploys **on push** — Rama does not hand-edit the VM crontab.

**W13 guardrail (do NOT violate):** do not set `shadow_tracker.enabled=false` and do not drop the
`innings` table. `shadow_tracker.is_tracking()` gates live re-entries (not dead code) and `daily_report.py`
still reads `innings`. (The Multi-Inning report *view* is deferred, backfillable — see follow-ups.)

---

## DEPLOY STEPS (Rama, OFF-MARKET)

1. **DB backup** (both DBs — the branch migrates v40→v41):
   ```
   ssh trading-vm 'cd ~/systems/trading-system && sqlite3 data_store/trading_system.db ".backup data_store/backups/pre_report_cutover_$(date +%Y%m%d_%H%M%S).db"'
   ```
   (The nightly 01:00 backup also exists; this is an explicit pre-deploy snapshot.)

2. **Commit + push the branch** (from the PC, off-market). The report-redesign work is uncommitted —
   stage it, commit, and push to `main` (clean ff → post-receive auto-installs the crontab):
   ```
   git add -A
   git commit -m "Report redesign: DB-pure daily_trade_review live (Phase C cutover)"
   git push origin <branch>:main
   ```
   Watch the push output for: `post-receive: crontab AUTO-INSTALLED from canonical.` (If instead you see
   `WARNING canonical != generate(registry) — crontab NOT installed`, STOP and investigate line endings /
   registry drift — do not proceed.)

3. **ONE manual run on the LIVE VM** (it has only ever run on the PC). This also performs the v41 migration
   on the live DB (backed up in step 1) and writes a heartbeat:
   ```
   ssh trading-vm 'cd ~/systems/trading-system && set -a && . ./.env && set +a && PYTHONPATH=. /home/ubuntu/systems/venv/bin/python reports/daily_trade_review.py --date 2026-06-30'
   ls -la ~/systems/trading-system/reports/output/daily_trade_review_report_2026-06-30.xlsx   # exists, 7 sheets
   ```
   (Use a recent real trading date. Expect a `migration complete: v40 -> v41` line — that is the W0 add.)

4. **Verify the cutover landed:**
   ```
   ssh trading-vm 'cd ~/systems/trading-system
     crontab -l | grep -E "daily_trade_review|daily_review|daily_report"   # trade_review IN (16:07); daily_review GONE; daily_report kept
     crontab -l | diff - deploy/cron/trading-system.cron && echo "crontab == canonical (no drift)"
     sqlite3 data_store/trading_system.db "SELECT name FROM sqlite_master WHERE name=\"config_snapshots\";"   # present (v41)
     sqlite3 data_store/trading_system.db "SELECT COUNT(*) FROM innings;"   # unchanged (W13: >0)
     grep -E "enabled" config/system_config.yaml | head; grep -A2 "shadow_tracker" config/system_config.yaml | grep enabled   # shadow_tracker.enabled: true
   '
   ```

5. **First scheduled run** = next trading day **16:07 IST** → run the First-Run Checklist below.

---

## FIRST-RUN CHECKLIST (next trading day, ~16:10 IST)

- [ ] **Fired on time?** `ssh trading-vm 'tail -5 ~/systems/trading-system/logs/cron-daily-trade-review.log'` — shows a run at ~16:07, ends with `Wrote .../daily_trade_review_report_<today>.xlsx`, no traceback.
- [ ] **Workbook produced?** `ls -la reports/output/daily_trade_review_report_<today>.xlsx` — present, > ~50 KB.
- [ ] **Heartbeat recorded?** `sqlite3 data_store/trading_system.db "SELECT status,executed_at FROM cron_heartbeat WHERE job_name='daily_trade_review' ORDER BY id DESC LIMIT 1;"` — SUCCESS, today.
- [ ] **Output sane?** open the xlsx: 7 sheets (Dashboard·Reconciliation·Orders·Signals·Strategies·Slippage·Config); Dashboard banner present; net P&L ties to the Orders total; win% consistent (Phase-B.1).
- [ ] **Officer clean?** the 17:05 Control-Tower / EOD Officer shows `daily_trade_review` COMPLETED, no MISSED for `daily_review`, no drift alert.
- [ ] **daily_report still ran** (bake-in safety net) — `daily_report_<today>.xlsx` also present.

**If the new run FAILS** (no file / traceback / bad numbers): you are NOT blind — `daily_report` still ran
in parallel. Execute the Rollback, then re-investigate off-market.

---

## ROLLBACK

The bake-in means a first-run failure is not an outage — `daily_report.xlsx` is still produced. To revert
the cutover itself:

**Option 1 — git revert (cleanest, redeploys via the hook):**
```
git revert <phase-c-cutover-commit>        # or: restore the pre-cutover cron_registry.yaml + canonical
git push origin <branch>:main              # post-receive regenerates + reinstalls the OLD crontab
```
This restores `daily_review` (16:00) and removes the `daily_trade_review` cron line.

**Option 2 — registry flip (if you only want the old report back, keep investigating the new one):**
Edit `config/cron_registry.yaml`: `daily_review` → `enabled: true`; `daily_trade_review` → `enabled: false`.
Then `python scripts/generate_crontab.py --generate --out deploy/cron/trading-system.cron`, commit, push.

**Option 3 — VM-only emergency (no push path):** `ssh trading-vm 'crontab -e'` and hand-restore the
`daily_review` line / remove the `daily_trade_review` line. (Temporary — the next push regenerates from the
registry, so also do Option 1/2 to make it durable.)

**The v41 migration does NOT need rollback.** `config_snapshots` is a pure additive table; the old code
ignores it. If you must, `DROP TABLE config_snapshots;` — but it is harmless to leave. The step-1 DB backup
is the ultimate fallback.

**Do NOT, during rollback, disable shadow_tracker or drop the innings table (W13).**

---

## FOLLOW-UPS (after a clean ~3–5 trading-day bake-in)

1. **Retire `daily_report`:** set `enabled: false` in `cron_registry.yaml`, regenerate canonical, push. Then:
   - remove `daily_report` from `_PENDING_REDESIGN_JOBS` in `scripts/cron_officer.py` (it now heartbeats;
     the "pending redesign" deferral is resolved by this cutover), and
   - remove the `daily_report.xlsx` `_check(...)` line in `scripts/system_manager.py` (keep the
     `daily_trade_review.xlsx` check).
2. **W13 — Multi-Inning/Shadow-Reentry sheet:** add to `daily_trade_review.py` when convenient
   (backfillable via `state_store.get_inning_summary_by_date`). Deferred, no data lost.
3. Once both old generators are retired and stable, consider deleting their cron *entries* (not the .py
   files) from the registry for tidiness.
