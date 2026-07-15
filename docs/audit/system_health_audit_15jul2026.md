# Whole-System Health Audit — 15-Jul-2026 (READ-ONLY)

**Scope:** two independent verdicts — (A) is the TRADING SYSTEM healthy (by ACTUAL output), and
(B) is the MONITORING telling the TRUTH — across 16 subsystems. **Read-only**: no code/config/schema/
DB/state change; DB via `sqlite3 -readonly` + `PRAGMA quick_check`/`foreign_key_check`; nothing fixed,
no push/deploy/restart. Deploy `2dc69d5`, schema v44. The ~108k signals backlog is EXPECTED (clears in
Phase B). Off-market, evening.

---

## ★ TWO TOP-LEVEL VERDICTS

> ### VERDICT A — TRADING SYSTEM: **PASS**
> The actual work was correct and safe. 7 trades within every cap (max_open 5/5 · daily_trades 10/7 ·
> daily_loss ₹296/₹0.99 · position_value ₹3,951/₹557), book flat & reconciled, net P&L ₹0.99, **DB
> integrity clean** (`quick_check=ok`, `foreign_key_check` empty, WAL=0), token valid, backups produced
> (340 MB), 0 orphans/partials, 0 crashes. The only two hard job failures — `eod_cleanup`,
> `generate_screened_csv` — are hygiene/reporting (non-trading) and already fixed on the unpushed branch.

> ### VERDICT B — MONITORING TRUTH: **FAIL**
> The monitor reports green while alerts are silently undelivered. **Email delivery is DEAD** (Gmail SMTP
> `535 BadCredentials`): the 15-Jul EOD emails never arrived and **8/8 critical sentinels today are
> UNDELIVERED**, yet the jobs' heartbeats say SUCCESS (Layer-9 masking). The `alert_watcher` is in an
> exit-2 restart-loop on the dead SMTP. `check_cron_drift` also false-alarms on 4 healthy jobs. Direct
> Telegram still works, so this is observability loss, not a trading outage.

**Deploy call (preview):** **PROCEED with the 3-fix branch** — no BLOCKER-class finding exists; all
monitoring/email defects are NON-BLOCKER. Open a separate monitoring-hardening cycle (urgent P0 = restore
the SMTP credential). Detail at the end.

---

## Per-subsystem (S1–S16) — ACTUAL (A) vs MONITORED-TRUTH (B)

| # | Subsystem | ACTUAL | MON-TRUTH | Evidence |
|---|---|---|---|---|
| S1 | Monitoring integrity | PASS | **FAIL** | 15-Jul heartbeats truthful (eod_cleanup+screened_csv correctly FAILED); BUT heartbeat SUCCESS masks email-delivery failure (Layer 9); heartbeat_db class can fake-success (14-Jul screened_csv, now fixed by 522da32) |
| S2 | Alert & report delivery | WARNING | **FAIL** | Reports GENERATED (artifacts present) but EMAIL delivery DEAD (`535 BadCredentials`); EOD emails missing; 8/8 sentinels undelivered; Telegram (direct) works |
| S3 | EOD sequence (actual work) | PASS | WARNING | candles/reconcile/eod_verify/broker_reconcile/wal_checkpoint/reconstruct/sr_backfill all ran; only eod_cleanup+generate_screened_csv FAILED (known, fixed-on-branch) |
| S4 | Backups & retention | PASS | PASS | db_backup 340 MB today; analytics_backup + backup_retention SUCCESS; db_retention_vacuum is Sunday-only (not due Wed) |
| S5 | Token lifecycle | PASS | PASS | token written 08:15:02; auth OK; `Token (LFL836): valid`; auto_refresh_token SUCCESS; token_cleanup 05:00 |
| S6 | Pre-flight | PASS | **FAIL** | preflight_phase_a/b/c markers present today (ran) — but check_cron_drift false-flags them "no heartbeat in 24h" (they use exit_code_file, not heartbeats) |
| S7 | Metrics & control | PASS | PASS | capture_metrics ×~80 SUCCESS; compute_strategy_metrics/metrics_summary/control_tower SUCCESS; disk_monitor hourly |
| S8 | Gemini jobs | PASS | PASS | brief/coach/integrity/log_review .md all have REAL content (coach: 4 trades/4175 rejects; integrity: CLEAN) — no silent API degradation |
| S9 | Forward-shadow recorder | PASS | PASS | fired 18:16 rc0, 2,535 rows appended; idempotent (0 dup (date,signal_id)) |
| S10 | Scheduler health | PASS | **FAIL** | crontab 47 lines, NO dups; but check_cron_drift (18:00) false-warns on 4 exit_code_file jobs it can't see |
| S11 | Services (systemd) | WARNING | **FAIL** | trading-system clean self-exit (expected); token-watcher/gui-dashboard active; **alert-watcher exit-2 loop (SMTP)**; security-watcher 1-min poll (exit 0) |
| S12 | Data integrity | PASS | PASS | `quick_check=ok`, `foreign_key_check` empty, WAL=0; 108k backlog is FK-consistent at rest |
| S13 | Config / flag hygiene | PASS | WARNING | shadow/default-off scaffolds are deliberate (v3_chain=shadow, regime/structure_exit off); STALE `officer.telegram_ban_until: 2026-06-23` (past → inert) |
| S14 | Security watcher | WARNING | WARNING | security-watcher polling (exit 0); stuck on Rama's own `BRi6` SSH key alert since 13-Jul (Q8 — no breach; Rama's standing deferral) |
| S15 | Process / resource | PASS | PASS | disk 81 GB free (16% used); 0 zombies; June logs 50–75 MB pending 30-day cleanup; alert_watcher.log 14 MB & growing from the loop |
| S16 | Deployment integrity | PASS | PASS | bare HEAD `2dc69d5`; live hook paths correct (`systems/trading-system`); schema v44; M-DP1 stale repo-tracked post-receive dup persists (live hook is correct) |

---

## *** SILENT FAILURES FOUND *** (green heartbeat / SUCCESS while actually failing)

1. **`system_manager_eod` (18:45)** — heartbeat **SUCCESS**, report generated (`reports/system_manager/
   2026-07-15.txt`), but its **EOD email never delivered** (SMTP 535) and its sentinel is UNDELIVERED.
   Silent since **15-Jul** (delivered fine through 14-Jul 21:20).
2. **`cron_officer_eod` (18:50)** — heartbeat **SUCCESS**, but the **detailed EOD email never delivered**
   (SMTP 535). Only the short Telegram headline fired (direct path); "Full details → email" was lost.
3. **`alert_watcher` (service)** — **8/8 critical sentinels today UNDELIVERED** (0 delivered). It finds
   the 8 pending, tries a digest EMAIL (>3 threshold), hits SMTP 535, and **`return 2`** → systemd
   restarts every 10s → permanent loop (14 MB log & growing). It is **email-only — no Telegram fallback**,
   so once SMTP dies, sentinel alerts are unrecoverable.
4. **`generate_screened_csv` — HISTORICAL (14-Jul & earlier)** — the exemplar: OLD code caught the
   DB-not-found error, wrote an empty CSV, and **returned 0 (faked SUCCESS)** → green heartbeat while the
   CSV was empty. **FIXED by `522da32`** (15-Jul it correctly reported FAILED). The M-SC2b readonly-kwarg
   residual is fixed on the branch.
5. **Layer-9 pattern (systemic)** — the EOD report jobs write a heartbeat **SUCCESS** independently of
   whether the email/sentinel actually delivered. Heartbeat ≠ delivery. This is the root monitoring gap.

**Inverse defect (false ALARM, not silent success):** **`check_cron_drift` (18:00)** warns
"Monitored, due-today jobs with NO heartbeat in 24h: preflight_phase_a/b/c, sr_detector_backfill" — but
all 4 **ran today** (markers present); they use `detection_method: exit_code_file`, which check_cron_drift
doesn't consult. A standing daily false-positive → erodes trust in the drift monitor.

---

## MISSING 15-JUL EOD EMAILS — ROOT CAUSE (9-layer tree, both jobs)

Applied independently to `system_manager_eod` (18:45) and `cron_officer_eod` (18:50):

| Layer | system_manager_eod | cron_officer_eod |
|---|---|---|
| 1. cron fired | ✅ heartbeat @18:45:05 | ✅ heartbeat @18:50:01 |
| 2. script started | ✅ | ✅ |
| 3. script crashed | ✅ no (completed) | ✅ no (completed) |
| 4. report artifact generated | ✅ `2026-07-15.txt` + sentinel body | ✅ Telegram headline + email body built |
| **5. email API / SMTP** | **❌ FAIL** — Gmail `535 5.7.8 BadCredentials` | **❌ FAIL** — same SMTP |
| 6. notification wrapper | ❌ downstream of L5 (alert_watcher digest send) | ❌ downstream of L5 |
| 7. attachment | n/a | n/a |
| **8. rate-limit / auth** | **❌ AUTH** — `ALERT_SMTP_PASSWORD` invalid/expired | **❌ AUTH** — same |
| **9. success-before-send** | **⚠️ PRESENT** — heartbeat SUCCESS despite failed send | **⚠️ PRESENT** |

**ROOT CAUSE = Layer 5/8: Gmail SMTP authentication failure (`535 BadCredentials`) — `ALERT_SMTP_PASSWORD`
is invalid/expired.** Reports generate fine; delivery is dead. Delivery worked through **14-Jul 21:20**
(11 sentinels delivered, 0 pending that day) and broke on 15-Jul → the credential expired/was revoked
**externally** (unchanged in `.env`; not touched by the deploy). *(A separate earlier 535 incident on
18-Jun was fixed; 07–14-Jul delivered normally.)* Compounded by **Layer 9** (heartbeat SUCCESS masks it)
and by the `alert_watcher` being **email-only** (no Telegram fallback) so sentinel alerts have no backup
channel when SMTP is down. **Telegram parity:** the direct-emit Telegram path is healthy (the cron_officer
headline fired); only the SMTP/email + sentinel-digest channel is dead.

---

## FINDINGS CLASSIFIED

**BLOCKER (capital · trading correctness · risk controls · data/DB integrity): NONE.**
Trading, capital, risk caps, reconciliation, and DB integrity all PASS (Verdict A). No corruption; the
108k backlog is FK-consistent and expected.

**NON-BLOCKER (monitoring · reporting · email · dashboards · observability):**
- 🔴 **SMTP email delivery DEAD** (`535 BadCredentials`) → EOD emails + all sentinel alerts undelivered.
  *Urgent for observability (Rama is blind to email/sentinel alerts), but not a trading blocker.*
- 🔴 **`alert_watcher` exit-2 restart-loop** — email-only, no graceful retry (returns 2 on auth fail), no
  Telegram fallback; burns CPU + grows its log every 10s. A robustness defect exposed by the SMTP outage.
- 🟠 **Layer-9 masking** — EOD/report jobs record heartbeat SUCCESS independent of delivery.
- 🟠 **`check_cron_drift` false-positive** on exit_code_file jobs (heartbeat-only detection).
- 🟡 **generate_screened_csv fake-success** (fixed `522da32`) + **eod_cleanup/screened_csv job failures**
  (fixed on the branch, deploying).
- 🟡 alert_watcher.log growth (14 MB, +/10s) — disk-fill risk over weeks (disk currently 81 GB free).
- ⚪ STALE `officer.telegram_ban_until: 2026-06-23`; M-DP1 stale repo-tracked post-receive dup (live hook
  correct); security-watcher stuck on Rama's own `BRi6` key (Q8, deferred).

---

## DEPLOY-READINESS RECOMMENDATION

**PROCEED — deploy the validated 3-fix branch (`fixes-eodcleanup-screened-fmledger-15jul`).** Per the
gate rule, HOLD only on a BLOCKER-class finding; **there is none.** Verdict A (trading) is PASS with clean
DB integrity; Verdict B (monitoring) is FAIL but every defect is monitoring/reporting/observability
(NON-BLOCKER). The branch is trading-hygiene (eod_cleanup FK, screened_csv, fm_ledger), touches nothing in
the alert/email path, and deploying it *improves* the monitor (removes two daily FAILED heartbeats).

**Open a separate monitoring-hardening cycle** (design → review → implement), ordered:
1. **P0 — restore the SMTP credential** (`ALERT_SMTP_PASSWORD`, Gmail app password) so email + sentinel
   delivery resume and the alert_watcher stops looping. *Urgent; do before relying on any email alert.*
2. `alert_watcher` robustness — don't `return 2`/crash-loop on auth failure; add a Telegram fallback for
   sentinels; back off retries.
3. Close Layer-9 — heartbeat SUCCESS must require actual delivery (or a distinct delivery-status signal).
4. `check_cron_drift` — consult exit_code_file markers, not heartbeats only.
5. Cheap: purge stale `telegram_ban_until`; alert_watcher.log rotation.

**Read-only audit — nothing changed. The next (monitoring-hardening) cycle starts from this report.**
