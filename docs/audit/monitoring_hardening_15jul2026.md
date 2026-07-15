# Monitoring Hardening — 15-Jul-2026 (F1–F4 · Canary · Deploy-gate · Checklist)

Follows the 15-Jul health audit (VERDICT A trading = PASS, VERDICT B monitoring = FAIL:
SMTP dead, EOD emails + 8 sentinels undelivered while heartbeats read SUCCESS). Goal: make
two failure CLASSES impossible to survive unseen — **CLASS 1** "green heartbeat, dead work"
and **CLASS 2** "one dead channel blinds the operator". Off-market. Branch
`monitoring-hardening-15jul` off `main` `0a77c92`; **UNPUSHED**. No schema change anywhere.
Each fix is its own commit with a fail-on-old/pass-on-new test.

**Not in scope (Rama, parallel):** F0 = restore `ALERT_SMTP_PASSWORD`; the 3-fix branch
deploy + Phase-B prune.

---

## PHASE 0 — delivery-status mechanism (read-only) + STOP-GATE = PROCEED

**The paradox resolved.** The audit read "535 errors since ~18-Jun (~4,682)". The histogram
is **two clusters**: **64 on 18-Jun** (a brief blip, recovered same day) and **4,745 on
15-Jul** (today's loop) — **nothing between 19-Jun and 14-Jul**. `.delivered` is written
ONLY on a real `server.sendmail` success (`alert_watcher.py:422/471`), so the deliveries
through **14-Jul 21:20:09** were genuine; the credential broke overnight (first 535 today at
**03:20:17**). The 14 MB log is unrotated (F4).

**Mechanism (what F1/F2 must extend):**
- **Sentinel delivery-status** = file-based `.flag → .delivered / .failed` (`alerts/critical.py`).
  Pure writer; **no telegram, no secondary channel** in the watcher path (email-only).
- **Cron execution-status** = `cron_heartbeat` (schema-versioned table 28; one `status` +
  a free-form `message` column) via `record_heartbeat` / `HeartbeatTimer`. **No functional
  status exists.**

**STOP-GATE decision: PROCEED.** F1's Telegram fallback is a clean add (reuse
`TelegramNotifier.from_env`). F2's functional status fits the EXISTING free-form `message`
column (execution stays in `status`) → **no schema change** (a new column was NOT an option
— the table is schema-versioned). No conflicting mechanism, no design fork.

---

## F1 — alert_watcher robustness (`c405c30`)  ·  *Architecture (fallback) + Monitoring*

- **Root cause:** on SMTP auth failure `run_once` `return 2` → systemd (Restart=always/10s)
  crash-loop; email-only → sentinels unrecoverable (CLASS 2).
- **What changed:** never exits non-zero on a delivery fault; **Telegram fallback** delivers
  each stuck sentinel via the proven direct-Telegram channel (`send(write_sentinel=False)`)
  and marks it `.delivered`; **time-based backoff** (60s×2ⁿ, cap 1800s) skips the dead SMTP;
  a machine-visible **`alert_watcher_degraded.json`** marker is published on failure, cleared
  on recovery. Works in `--once` and `--loop`.
- **Test:** `TestF1SmtpRobustness` (3) + updated `test_smtp_auth_error_no_longer_crashes`.
  Before: `run_once` returned **2** on auth error, no fallback. After: **rc 0**, all sentinels
  delivered via Telegram, degraded marker written, backoff recorded. **38 pass.**
- **Regression-map:** only `run_loop`/systemd call `run_once`; no mode branch; the OLD
  "exit 2 on auth" contract is intentionally reversed (test updated). **Parity:** single path.

## F2 — EXECUTION vs FUNCTIONAL status (`795a417`)  ·  *Architecture (systemic)*

- **Root cause (CLASS 1):** a job records one heartbeat SUCCESS ("script ran") independent of
  whether its real function/delivery succeeded (EOD emails green while undelivered; screened
  CSV green while empty).
- **What changed:** two INDEPENDENT states — EXECUTION in the `status` column (unchanged);
  FUNCTIONAL encoded in the EXISTING `message` column as `[func=<STATUS>]` (no schema change).
  `record_heartbeat`/`HeartbeatTimer` gain `functional_status`; `parse_functional_status` is
  the reader. Applied to `generate_screened_csv` (`_cron_main` sets it from the CSV artifact:
  OK / EMPTY_NO_DATA / FAILED). The Cron Officer surfaces (a) per-job functional issues and
  (b) the **F1 email-degraded marker** — so a dead SMTP is VISIBLE even when every execution
  heartbeat is green.
- **Test:** `test_f2_functional_status` (4). Before: only one SUCCESS status; the Officer had
  no delivery-health line. After: status=SUCCESS + functional=EMPTY_NO_DATA coexist; the
  Officer emits a DEGRADED line naming `ALERT_SMTP_PASSWORD`. **Officer suite 39 pass.**
- **Regression-map:** the `[func=]` prefix is transparent to existing message consumers;
  execution `status` unchanged. **Parity:** single mode-agnostic path.

### F2 — heartbeat_db-job enumeration (recommended FUNCTIONAL criteria; incremental fast-follow)

| Job | Recommended functional criterion |
|---|---|
| `generate_screened_csv` | **DONE** — non-empty CSV artifact (OK / EMPTY_NO_DATA / FAILED). |
| `system_manager_eod`, `cron_officer_eod` | report generated **AND delivered** — surfaced now via the sentinel `.delivered` state + the F1 email-degraded marker (Officer). Add `functional_status="ENQUEUED"` when the sentinel is written. |
| `daily_report`, `daily_trade_review` | xlsx exists + size ≥ a floor (not a stub). |
| `eod_verify` | result == VERIFIED (not PENDING). |
| `eod_broker_reconcile` | positions/orders VERIFIED. |
| `reconcile_positions` | reconciliation clean (no non-OK rows). |
| `refresh_instruments` | instrument-cache size ≥ min_rows. |
| `auto_refresh_token` | fresh token file written + broker auth OK. |
| `fetch_fno_ban` | ban list fetched (not the 404 fail-open fallback). |
| `gemini_premarket_brief` / `gemini_log_review` / `gemini_trade_coach` / `gemini_data_integrity_check` | `.md` has real content (not an API-failure placeholder). |
| `control_tower` | aggregation ran + report written. |
| `trade_journal`, `compute_strategy_metrics` | artifact/rows produced. |
| `wal_checkpoint`, `fetch_daily_candles`, `reconstruct_excursions`, `sr_detector_backfill`, `backup_retention`, `forward_shadow_record` | job-specific completion (candles fetched / excursions written / rows appended). |

*(Converting the tail is incremental — the mechanism + the jobs that BIT us are done; the
rest are cheap follow-ups, one `timer.functional_status = ...` line each.)*

## F3 — check_cron_drift marker-awareness (`d960760`)  ·  *Monitoring*

- **Root cause:** PASS 1 checked heartbeats for EVERY monitored job, but the four
  exit_code_file jobs (preflight_phase_a/b/c, sr_detector_backfill) write markers, not
  heartbeats → a standing daily FALSE "no heartbeat in 24h".
- **What changed:** dispatch by `effective_detection_method`, mirroring the Cron Officer —
  heartbeat_db → heartbeat row; exit_code_file → fresh `cron_marks/<name>.done` (reuses the
  Officer's `_read_marker`, no duplicate logic).
- **Test:** `test_f3_cron_drift_markers` (2). Before: a marker job with a fresh marker was
  flagged. After: not flagged; a marker job that genuinely didn't run is still flagged
  (no false negative). **content-drift suite 6 pass.** **Parity:** single path.

## F4 — hygiene (`5311fe6` + test `e57fdac`)  ·  *Operational / hygiene*

- **Root cause:** (a) stale `officer.telegram_ban_until: '2026-06-23'` (inert, confusing);
  (b) `alert_watcher.log` was the only FIXED-name log → unbounded (14 MB; never cleaned).
- **What changed:** (a) ban → `null`; (b) date-embed the watcher log
  (`alert_watcher_<date>.log`, cleaned by log_cleanup) per Foundation Rule 1.7 (NOT a
  RotatingFileHandler mid-day split). F1 removed the growth source.
- **Test:** `test_f4_hygiene` (ban purged) + updated `test_log_file_created` (date-embedded)
  + updated `test_officer_config_eod_time_and_ban`. **alert_watcher suite 38 pass.**

## CANARY — daily monitoring self-test (`11520b4`)  ·  *Architecture (P-3 self-check)*

`scripts/monitoring_canary.py` (registered daily 08:20). Probes four paths — EMAIL (SMTP
login, no send), TELEGRAM (getMe), SENTINEL (F1 marker + backlog), DASHBOARD (systemctl) —
QUIET when healthy, LOUD when broken (Telegram WARNING → CRITICAL sentinel fallback if
Telegram is also down, riding F1). Records execution + F2 functional_status. The EMAIL probe
FAILS until F0 restores the credential — **that failing check, announced over Telegram, IS
the canary working.** Satisfies all seven prevention-checklist items itself.
**Test:** `test_monitoring_canary` (7). **Parity:** single path.

## DEPLOY-GATE + PREVENTION CHECKLIST (`66cb182` + doc)  ·  *Operational / Architecture*

- **Deploy gate** `scripts/deploy_assert.py`: a lightweight tripwire — BLOCKS a deploy ONLY
  on a BLOCKER class (DB `quick_check` / `foreign_key_check` / schema-version parity);
  monitoring defects WARN, never block. Read-only (mode=ro; no migration).
  **Test:** `test_deploy_assert` (3).
- **Prevention checklist** `docs/monitoring_prevention_checklist.md` — the seven items no new
  cron/service ships without; referenced from `docs/SYSTEM_MAP.md`.

---

## Status + prevention classification

| Item | Commit | Prevention class |
|---|---|---|
| F1 alert_watcher fallback/no-crash | `c405c30` | Architecture + Monitoring |
| F2 execution-vs-functional | `795a417` | Architecture (systemic) |
| F3 cron-drift markers | `d960760` | Monitoring |
| F4 hygiene | `5311fe6`, `e57fdac` | Operational / hygiene |
| Canary | `11520b4` | Architecture (P-3 self-check) |
| Deploy-gate + checklist | `66cb182`, this doc | Operational / Architecture |

**Accepted limitation (fast-follow, not blocking):** non-auth persistent SMTP errors still
use the existing retry ladder without the Telegram fallback; the heartbeat_db functional
tail (table above) is converted incrementally; switching the alert-watcher unit to `--loop`
is a Rama ops step (F1 works in both modes).

The two silent-failure classes can no longer survive unseen: CLASS 1 (functional status +
the Officer surfacing it, incl. the email-degraded marker) and CLASS 2 (Telegram fallback +
degraded marker + the daily canary). Full touched-suite green; 5,068-test collect clean.
Nothing pushed.
