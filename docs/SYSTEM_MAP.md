# SYSTEM MAP — Trading System v2
# Last updated: 2026-06-18 by VS Code Claude (Claude Code)
# ⚠️ READ THIS BEFORE TOUCHING ANYTHING ⚠️

This is the single authoritative path/ops reference. It complements (does not
replace) the design/runbook docs in `docs/` — see "Related Docs" at the bottom.
For a one-screen quick reference, see [`/PATHS.md`](../PATHS.md).

> ## ✅ CURRENT STATUS (2026-06-19) — RESUMED LIVE; FIX-190 CLOSED
> **Resumed live 13:24 IST** (HARD_KILL force-cleared via `deploy/resume.sh --force`,
> root cause fixed by Bug C/D; morning trades reconciled to CLOSED_MANUAL, orphans
> cancelled, capital consistent). Running LIVE with `live_test_mode` (now **max_open=4
> / 6-per-day, PERMANENT** — active bug-hunting with small qty on ₹10k).
> **FIX-190 live incident at ~10:00 IST.** First real trading after the Kite IP
> allowlist was fixed: a Chartink spike fired 5 entries in ~5s; THELEELA's target
> exceeded the upper-circuit band → a single recoverable TGT rejection cascaded
> into a full **HARD_KILL that flattened every open position and left orphan
> SL/TGT orders**, double-selling THELEELA into a naked short. Rama manually
> recovered (net loss ₹2.87).
>
> **Stage-4 ALL fixes LANDED (committed+pushed; unit + replay tested):** C
> (TGT-only no HARD_KILL), D (circuit-band clamp), A (reverse-aware flatten — no
> oversell), E (cancel resting exits — no orphans), F (no duplicate G5b SL), G
> (entry throttle: 20s gap / 3-per-60s + per-symbol 5-min cooldown), H (`live_test_mode`: live caps now **max_open=4 /
> 6-per-day, permanent**), I (in-session drift tolerance), **B** (status semantics
> correct via FIX-181 + observability metrics on `/metrics`: signals_processed /
> entries_placed / entries_throttled / entries_rejected — **but the daily-cap RACE
> was NOT closed**; see the 2026-06-19 "Bug B" Changelog entry for the
> reservation-aware DAILY_TRADES fix that closes the 18-Jun 8-vs-5 overshoot).
> Incident replay green
> (`tests/integration/test_fix190_incident_replay.py`). **Paper mode SKIPPED per
> Rama** (stay LIVE with tiny ₹10k for active bug-hunting). Service is LIVE with
> `live_test_mode=true` (4 positions / 6 trades-per-day, permanent). See memory
> `fix_190_incident`. (FIX-189 dash-cron / market-window / EOD-self-exit separate.)

## Pre-Work Checklist
- [ ] Read this file (and `PATHS.md`).
- [ ] Identify exactly which files/paths you will touch.
- [ ] Confirm no duplicate/canonical version exists for your target (see "Known Duplicates / Issues").
- [ ] Remember: **deploy ≠ restart**. `git push` updates the VM working tree but does NOT
      restart `trading-system.service`. A manual restart is required to activate code.
- [ ] Config edits change the file hash → may surface in startup checks; edit the canonical file only.
- [ ] After work: **update this file** (and the Changelog) if any path/file/cron/service changed.

---

## VM Paths  (host 161.118.187.249, user `ubuntu`, tz Asia/Kolkata / IST)

### Project Root
`/home/ubuntu/systems/trading-system/`  ← the running working tree (git-checked-out by deploy hook)

### Shared venv (OUTSIDE the project)
`/home/ubuntu/systems/venv/`  — Python **3.12.3**. Always invoke as
`PYTHONPATH=. /home/ubuntu/systems/venv/bin/python ...`.
Key pkgs: kiteconnect 5.1.0, pydantic 2.13.0, Flask 3.1.3, openpyxl 3.1.5, requests 2.33.1, pytest 9.0.3.

### Deploy (git push, NOT scp)
- Bare repo: `/home/ubuntu/trading-system.git/` with `hooks/post-receive`.
- Hook does: `git --work-tree=/home/ubuntu/systems/trading-system --git-dir=… checkout -f <branch>`.
- It does **NOT** restart the service. (See PC Paths → SCP Deployment Map.)

### Config Files  (`config/`)
| File | Purpose | Read by |
|---|---|---|
| `system_config.yaml` | Master config (trading_hours, capital, risk, position_sizing, leverage_map, alerts.telegram, order_reconciler, eod_squareoff, …) | `core/config_loader.py` → SystemConfig |
| `broker_costs.yaml` | Brokerage/STT/charges model | `broker/cost_calculator.py` |
| `broker_limits.yaml` | Rate-limit buckets (order/quote/historical/margins) + 429 backoff | `broker/rate_limiter.py` |
| `slippage_model.yaml` | Slippage assumptions | `broker/slippage_engine.py` |
| `scoring_weights.yaml` | Quality scorer weights | `screening/quality_scorer.py` |
| `scan_webhook_map.yaml` | Chartink scan → strategy mapping | `signals/webhook_receiver.py` |
| `chartink_scanners.yaml` | Scanner definitions | screening/signals |
| `nse_holidays_2026.yaml` | Trading-holiday calendar | `core/market_windows.py` |
| `cron_registry.yaml` | **Cron job source of truth** (30 jobs; TASK #3) | `core/cron_registry.py` → `cron_officer`, `check_cron_drift` |
| `symbol_aliases.yaml` | Symbol normalization | instrument resolution |
| `accounts.csv` | Account registry (LFL836 primary, paper_capital, env-var names) | `core/account_registry.py` |
| `accounts_multi_example.csv` | Template/example (NOT loaded) | — |
| `instruments.csv` | Instrument master (lot sizes, tokens) | `core/instrument_cache.py` |
| `config/reference_data/` | NSE reference data | reference lookups |
| `config/strategies/` | Per-strategy YAML configs | `strategies/loader.py` |
| `.env` (root, 0600-ish, NOT in git) | Secrets: ZERODHA_* — incl. `ZERODHA_USER_ID`/`ZERODHA_PASSWORD` + per-account `ZERODHA_TOTP_<acct>` (e.g. `ZERODHA_TOTP_LFL836`) for headless TOTP login (FIX-187); `GEMINI_BIN` (path to the `agy` CLI — agy auths via OAuth tokens in `~/.gemini/`, NOT an API key), WEBHOOK_SECRET, etc. | systemd `EnvironmentFile` + cron `set -a && . ./.env && set +a` |
| `.env.example` | Template for `.env` | — |

> Telegram bot token / chat IDs are also injected via the systemd drop-in
> (`/etc/systemd/system/trading-system.service.d/*.conf`). **Secrets live in `.env` and the
> drop-in only — never commit them.**

> **Email alerts (alert-watcher SMTP)** — `system_config.yaml` → `alerts.smtp`: Gmail
> `smtp.gmail.com:587` TLS, `username`/`from_address`/`to_addresses` = `ramakrishnan031@gmail.com`,
> password via `password_env: ALERT_SMTP_PASSWORD` (set in `.env`, not committed). `alert_watcher.py`
> emails CRITICAL sentinels with subject `[LFL836] <SEVERITY> — <title>` (digest when >3 pending).

### Python Modules (role per package)
| Package | Role | Key modules |
|---|---|---|
| `core/` | Infra: config, DB, events, time, IDs | config_loader, cron_registry, state_store, db_connect, events, logger, time_authority, market_windows, instrument_cache, account_registry, migrations, constants, schema.sql |
| `broker/` | Broker integration + polling | zerodha_adapter, angelone_adapter, order_monitor (2s fill poll), order_state_machine, rate_limiter, cost_calculator, slippage_engine, product_resolver, token_monitor, clock_skew_probe |
| `capital/` | Capital, risk, kill-switch | fund_manager, position_sizer, risk_engine, kill_switch, drift_handler, invariant, performance_allocator, shadow_engine, strategy_governor |
| `orders/` | Order lifecycle | order_placer, order_reconciler (15s), order_manager, eod_squareoff, smart_tgt_manager, tgt_retry_manager (30s TGT re-place), breakeven_manager, sl_breach_monitor, entry_engine, full_entry_engine, order_protocol_co, order_protocol_limit, price_math, shadow_tracker |
| `signals/` | Ingestion | webhook_receiver, signal_processor, entry_throttle (Bug G: global min-gap/burst + per-symbol cooldown) |
| `screening/` | Signal screening/scoring | entry_gate, quality_scorer, secondary_screener, step_executor |
| `data/` | Market data | live_feed (WS ticks), candle_store |
| `alerts/` | Alerting | telegram_notifier, critical (sentinels) |
| `strategies/` | Strategy config | loader, schema |
| `utils/` | Utilities/preflight | startup_checks, holiday_guard, instance_lock, cron_heartbeat |
| `reports/` | Reporting | daily_report, daily_review, style_constants |
| `scripts/` | ops/cron scripts | incl. `cron_officer.py` (TASK #3 briefing/eod/check-change), `system_manager.py` (TASK #5 EOD deep cross-check, 18:45), 6 `gemini_*.py` (AI ops), auto_refresh_token, premarket_healthcheck, reconcile_positions/pnl, eod_cleanup/verify, etc. |
| `tests/` | 305 test files | unit/, integration/, crash_test/ |
| `main.py` (root) | App entrypoint | launched as `main.py --mode live` |

### Database  (`data_store/`)
- `trading_system.db` (~49 MB) — **MAIN** DB (schema **v30**; v29 added trades.EXITING, v30 added trades.needs_tgt_retry/tgt_retry_count/tgt_last_retry_at for the TGT-retry mechanism). trades, orders, signals, fm_ledger, etc.
- `analytics.db` (~44 KB) — analytics split (v28); **ATTACHed** to the main DB at runtime.
- **Raw sqlite access MUST use `core.db_connect.connect`** (it sets up the ATTACH); plain `sqlite3`
  works only for read-only SELECTs against the main file.
- `data_store/session/zerodha_token.json` — broker session token (wiped 05:00 daily by cron).
- `data_store/backups/` — nightly `.backup` snapshots (7-day retention).
- `data_store/candles/` — candle artifacts.
- `data_store/critical_alert_*.flag` — CRITICAL sentinels written by `alerts/critical.py`,
  consumed by `alert-watcher.service` (VM) → renamed to `.delivered`. **Local-only/gitignored**
  (`data_store/` is ignored), so the PC accumulates them whenever the system/tests run there with
  no watcher — periodically `rm data_store/critical_alert_*.flag` on the PC (VM stays clean via the
  watcher). VM clean as of 19-Jun (0 `.flag`); PC's 445 stale (mostly test) flags removed 19-Jun.

### Logs  (`logs/`)
Daily-dated files: `system_YYYY-MM-DD.log`, `debug_*.log`, `reconciler_*.log`, `trades_*.log`,
plus per-cron `cron-*.log` / `*.log`. Plain `FileHandler` (not Rotating); a cron deletes `*.log`
older than 30 days (00:00). `failed_alerts.log` = Telegram delivery-failure JSON-lines.

### Reports & Outputs  (`reports/`)
`reports/daily/`, `reports/daily_review/`, `reports/flow_trace/`, `reports/log_review/`,
`reports/watchman/`, `reports/weekly_patterns/`, `reports/output/`, `reports/crash_test/`,
`reports/system_manager/` (TASK #5 `<date>.txt` EOD reports). NB (audit 19-Jun): daily
report is `reports/output/daily_report_<date>.xlsx`; most other reports are `.md` (not `.xlsx`);
`reports/daily/` + `reports/daily_review/` are currently empty.

### Tools / External
- `gemini` CLI at `/usr/local/bin/gemini` — drives the 6 `scripts/gemini_*.py` AI-ops jobs
  (a.k.a. "Antigravity/AGY" automation) + `trading-watchman.service`.
- `sqlite3` CLI for DB ops/backups.

---

## PC Paths  (developer machine, Windows)

### Local Project
`D:\Projects\trading-system`  (this git repo; mirrors the VM working tree).

### SCP Deployment Map
**There is no SCP.** Deployment is **git push**:
```
PC:  git push origin main            # origin = vm = trading-vm:~/trading-system.git
VM:  bare repo post-receive hook  →  git checkout -f  →  /home/ubuntu/systems/trading-system
     (service is NOT restarted automatically — restart manually to activate code)
```
- Git remotes (PC): `origin` **and** `vm` both point to `trading-vm:~/trading-system.git` (duplicate; see Issues).
- SSH host alias `trading-vm` (key `trading_vm_secure`, passwordless).

---

## Cron Jobs  (live `crontab -l` == `deploy/cron/trading-system.cron`, 34 lines; **source of truth: `config/cron_registry.yaml`** — TASK #3)
All market jobs run `cd … && set -a && . ./.env && set +a && PYTHONPATH=. venv/bin/python <script> >> logs/<log>`.
To change cron: edit `config/cron_registry.yaml` → regenerate the file → `crontab deploy/cron/trading-system.cron`.

> ⚠️ **FIX-189 (2026-06-19) — cron MUST run under bash.** cron's default `/bin/sh`
> is **dash**, whose `.` (source) builtin will NOT load a relative path without a
> slash → a bare `. .env` fails with `sh: .: .env: not found` and **every job dies
> before Python** (no token refresh, no heartbeats, no per-cron logs). The
> canonical file therefore carries TWO guards: a leading **`SHELL=/bin/bash`** line
> AND **`. ./.env`** (the `./` makes even dash source from CWD). Keep both on any
> regeneration. `tests/unit/test_cron_registry.py::TestFix189CronShell` enforces it.

> ⚠️ **ENV-EXPORT FIX (2026-06-19) — source MUST be `set -a && . ./.env && set +a`.**
> `.env` uses bare `VAR=value` (no `export`), so a plain `. ./.env` sets shell
> variables the child `python` does **NOT** inherit → every cron job ran WITHOUT
> its `.env` secrets (`ZERODHA_API_KEY_LFL836`, `TELEGRAM_*`).
> FIX-189 made jobs *run*; this made them get their secrets (the 19-Jun 15:45
> reconcile_positions broker-creds + "Telegram env not set" were both this).
> `set -a` (allexport) exports everything sourced. Keep the wrapper on every
> Python job line. `tests/unit/test_cron_registry.py::TestCronEnvExport` enforces it.

| Time (IST) | Days | Script | Purpose |
|---|---|---|---|
| 00:00 | daily | `find logs -mtime +30 -delete` | log cleanup |
| 01:00 | daily | sqlite3 `.backup` trading_system.db | nightly DB backup |
| 01:05 | daily | sqlite3 `.backup` analytics.db | analytics DB backup (TASK #3) |
| 02:00 | daily | `find backups -mtime +7 -delete` (both DBs) | backup retention |
| 02:30 | daily | `db_retention.py` (Sun: `--vacuum`) | DB row prune (TASK #3) |
| 04:55 | daily | `cron_officer.py --briefing` | Cron Officer morning briefing (TASK #3) |
| 05:00 | daily | `rm session/zerodha_token.json` | force fresh login |
| 08:15 | Mon-Fri | `auto_refresh_token.py` | Headless TOTP token refresh (FIX-187; no manual OTP) |
| 08:30 | Mon-Fri | `premarket_healthcheck.py` | preflight health |
| 08:35 | Mon-Fri | `fetch_fno_ban.py` | F&O ban list |
| 08:55 | Mon-Fri | `gemini_premarket_brief.py` | AI premarket brief |
| 09:00 | Mon-Fri | `refresh_instruments.py --account LFL836` | instrument master |
| */5 09–15 | Mon-Fri | `capture_metrics_baseline.py` | metrics capture |
| 15:40 | Mon-Fri | `fetch_daily_candles.py` | OHLCV fetch |
| 15:45 | Mon-Fri | `reconcile_positions.py` | broker↔local positions |
| 15:50 | Mon-Fri | `eod_cleanup.py` | stale signals/orders cleanup |
| 15:55 | Mon-Fri | `eod_verify.py` | EOD verification |
| 16:00 | Mon-Fri | `reports/daily_review.py` | EOD review |
| 16:00 | Mon-Fri | `wal_checkpoint.py` | WAL checkpoint |
| 16:01 | Mon-Fri | `generate_screened_stocks_csv.py` | screened CSV |
| 16:05 | Mon-Fri | `-m reports.daily_report` | daily xlsx report |
| 16:10 | Mon-Fri | `trade_journal.py` | trade journal |
| 16:15 | Mon-Fri | `compute_strategy_metrics.py` | strategy metrics |
| 16:16 | Mon-Fri | `capture_metrics_baseline.py --summarize` | metrics summary |
| 16:20 | Mon-Fri | `gemini_log_review.py` | AI log review |
| 16:40 | Mon-Fri | `gemini_trade_coach.py` | AI trade coaching |
| 17:00 | Mon-Fri | `gemini_data_integrity_check.py` | candle integrity |
| 18:00 | Sun | `gemini_weekly_patterns.py` | weekly patterns |
| 18:00 | Mon-Fri | `check_cron_drift.py` | cron heartbeat drift (registry-driven) |
| 18:30 | Mon-Fri | `cron_officer.py --eod-summary` | Cron Officer EOD report (TASK #3) |
| 18:45 | Mon-Fri | `system_manager.py` | System Manager EOD deep cross-check (TASK #5) |
| 03:00 | 1st of month | `backup_restore_drill.py --quiet` | restore drill |
| hourly | every | `disk_monitor.py` | disk space |

> ✅ **Synced (TASK #3, 18-Jun):** the live crontab now equals `deploy/cron/trading-system.cron`,
> which is generated from `config/cron_registry.yaml` (the source of truth). The Cron Officer
> (`scripts/cron_officer.py --check-change`) flags any future drift. Old crontab backed up to
> `data_store/crontab_backups/`.

## Systemd Services  (`/etc/systemd/system/`)
| Service | ExecStart | Purpose | Notes |
|---|---|---|---|
| `trading-system.service` | `venv/bin/python main.py --mode live` | Main app (live mode) | `Restart=on-failure`, `RestartSec=10`, **`RestartPreventExitStatus=3 4`** (4=HALT/SOFT_KILL no-restart, added 18-Jun); `EnvironmentFile=.env` + drop-in (Telegram secrets). **FIX-189 (19-Jun): main() exits 0 outside the broad service window [08:00–16:00 IST]** (startup guard; bypass: `--status`/`--dry-run`/`--interactive`/`--resume` or `TS_IGNORE_MARKET_WINDOW=1`) **and an `eod-self-exit` thread exits 0 once past 16:00 IST AND flat** (`count_active_positions()==0`) so it never idles overnight; never exits while a position is open. |
| `token-watcher.service` | `bash deploy/token_watcher.sh` | Auto-start app on fresh token | active |
| `alert-watcher.service` | `python scripts/alert_watcher.py` | Consume CRITICAL sentinel flags (email digest) | **enabled, delivering (18-Jun)**. NB: `Restart=always`+`RestartSec=10` and the script runs one pass then exits 0 → **periodic-oneshot**: `auto-restart`/rising `NRestarts` is NORMAL (a check every ~10s), NOT a crash-loop. |
| `trading-watchman.service` | (gemini watchman) | AI log monitor during market hours | `Wants=` by trading-system |
| `security-watcher.service` | `python scripts/security_monitor.py --watch` | **VM Security Manager Phase 1+2** — auth.log + file-integrity monitor (9 checks incl. Phase-2 copy-switch + copy-bypass), [LFL836] alerts | **enabled+active (19-Jun)**. `Type=simple`+`Restart=always`+`RestartSec=60` → periodic (~60s); model = alert-watcher. Reads `/var/log/auth.log` (ubuntu ∈ `adm`). Config `config/security.yaml` (standalone — NOT system_config.yaml, which is `extra="forbid"`). State `data_store/security_state.json`. Alert-ONLY (never blocks). |

> **VM security tooling (Phase 1, 19-Jun)** — also installed at OS level (NOT via git):
> **fail2ban** (`/etc/fail2ban/jail.local` from `deploy/security/jail.local`; sshd jail, `ignoreself`,
> reads auth.log, no static IP allow-list — Rama's IPs are dynamic) and **auditd**
> (`/etc/audit/rules.d/trading-security.rules` from `deploy/security/`; watches `.env`/`config/`/
> systemd units/`authorized_keys`/`sudoers` — query `sudo ausearch -k <key>`). All three services
> `enabled` (reboot-survive). **SSH hardening #2 APPLIED (19-Jun): `PermitRootLogin no`** via
> drop-in `/etc/ssh/sshd_config.d/99-trading-security.conf` (`deploy/security/sshd_config.d/`;
> validated `sshd -t` → `reload ssh`; ubuntu access unaffected — no root keys exist). Idle timeout
> (#1) intentionally NOT set (long `tail -f` sessions); `MaxSessions` left at 10. Phase 2 (copy
> protection) **BUILT 20-Jun** (code+config+tests in git; OS-level activation staged — see the
> Phase 2 note below); Phase 3 (EOD integration) **DONE 20-Jun** (Changelog). See memory `vm_security_phase1` /
> `vm_security_phase2`.

> **VM security tooling (Phase 2 — copy protection, 20-Jun, code in git; NOT yet activated on the
> VM)** — controls **VM→PC** file copying. `scripts/copy_gate.py` = the policy engine (priority:
> **TIME-LOCK 18:00–08:00 IST absolute** › OFF-switch › session-cap › 15-min token) + token store
> (`data_store/security/copy_token.json`) + JSON-lines audit (`data_store/security/copy_audit.log`).
> `scripts/request_copy.py` (`request-copy <reason>`) mints a 15-min token (audited + Telegram).
> `deploy/security/bin/copy-guard` is symlinked as `/usr/local/bin/{scp,sftp,rsync}` and consults the
> gate before exec'ing the real binary — **hard-blocks VM-INITIATED copies** (fail-safe: blocks if the
> gate can't be consulted). `security_monitor.py` gained two checks: **(8)** `copy_protection` ON→OFF
> transition → CRITICAL, **(9)** auditd `copy_attempt` bypass (a raw outbound scp/sftp/rsync with no
> token) → CRITICAL. auditd `copy_attempt` execve rules added to `trading-security.rules`.
> **Limitation:** a **PC-INITIATED pull** (PC is the client, VM's sshd serves it) cannot be
> hard-blocked from the VM side without risky `ForceCommand`/subsystem changes — it is **detect+alert
> only**. **Detection-only is LIVE on the VM (20-Jun):** the auditd `copy_attempt` rules are loaded
> and the two monitor checks are verified (a raw outbound copy raises a CRITICAL), but the blocking
> wrappers are NOT symlinked yet (scp workflow unchanged). **To add hard-block** later, run
> `bash deploy/security/install_copy_protection.sh` (reversible: `--uninstall`); git push / interactive
> ssh are NOT wrapped, so it cannot lock you out.

Drop-in dir: `trading-system.service.d/` (holds Telegram env vars — secrets).

---

## Known Duplicates / Issues  (audit 2026-06-18; cleanup applied same day)
1. ~~Live crontab ≠ `deploy/cron/trading-system.cron`~~ — **RESOLVED 2026-06-18 (TASK #3)**: the
   registry `config/cron_registry.yaml` is now the source of truth; the canonical file was regenerated
   and **installed** (`crontab deploy/cron/trading-system.cron`) — live == file (33 lines). The
   previously-missing analytics.db backup (01:05) + `db_retention` (02:30) were added; `auto_refresh_token`
   moved 08:00→08:15. Old crontab backed up to `data_store/crontab_backups/`.
2. **[PARTIAL] `trading-system.service` — emergency-kill recovery + headless** — crash-loop FIXED
   (`RestartPreventExitStatus=3 4`). FIX-188 (18-Jun) additionally stopped **token-watcher** from
   hammer-restarting an exit-4 HALT every 30s (now exit-code aware: same-day HALT → back off + 1
   alert/day; prior-day HALT → one clean start so it auto-recovers) and broadened `/health` (token +
   kill_switch, 200/503). The emergency SOFT_KILL was cleared (operator `--resume`) and the service
   **handed off to systemd — now `active`, `NRestarts=0`**. Still OPEN (EXTERNAL): the Kite dev-console
   **IP allowlist** for the VM IP (place_order 403 root cause; see `kite_ip_allowlist_dependency`,
   FIX-185) — required for live order placement. **Resume cleanly with `deploy/resume.sh`** (stop →
   `scripts/clear_kill_switch.py` clears the kill in the DB without the instance lock → start under
   systemd) — FIX-188b; do NOT use a standalone `main.py --resume` (it competes for instance-lock port 5001).
3. ~~`alert-watcher.service` SMTP delivery~~ — **RESOLVED 2026-06-18 (verified)**: valid Gmail App
   Password set in `.env`; the 16 pending sentinels delivered (`Digest delivered: 16 alerts → .delivered`;
   `.flag`=0, `.failed`=0; digest emailed to `ramakrishnan031@gmail.com`, subject
   `[LFL836] CRITICAL — DIGEST: 16 alerts`). No auth errors after the valid password.
4. ~~Two git remotes on PC~~ — **RESOLVED 2026-06-18** (`vm` remote removed; `origin` remains).

## PENDING CLEANUP
✅ **All previously-listed items were actioned on 2026-06-18** (see Changelog): root `trading.db`
deleted, 56 stale `critical_alert_*.flag` removed, dated notes archived to `docs/archive/`,
`deploy_audit_fixes.ps1` removed, duplicate `vm` git remote removed. **No items currently pending.**
New open operational issues are tracked under "Known Issues" above (crontab divergence, crash-loop,
inactive alert-watcher).

---

## Related Docs (do not duplicate these — cross-reference)
- `docs/CONFIG_GUIDE.md` — **Rama-facing config reference (TASK #8)**: every setting in plain
  language, effective-values table, override precedence, common scenarios, safety warnings
- `docs/system_manuals/*.docx` — **Word-format manuals for Rama** (`trading_System_v2_runbook.docx`,
  `config_file_guide.docx`, `chartink_mounted_strategies.docx`). ⚠️ **GITIGNORED** (binary;
  `.gitignore` → `docs/system_manuals/*.docx`) — so `git push` does **NOT** carry them. Sync
  out-of-band: `scp docs/system_manuals/*.docx trading-vm:~/systems/trading-system/docs/system_manuals/`.
  Re-scp after any edit. VM copies verified byte-identical + valid OOXML (19-Jun).
- `docs/01_system_architecture.md` — architecture
- `docs/03_daily_operations_runbook.md` / `docs/RUNBOOK.md` — daily ops
- `docs/04_db_schema_reference.md` — DB schema (v28)
- `docs/05_incident_response.md`, `docs/disaster_recovery.md` — incidents
- `docs/06_deployment_guide.md` — deployment detail

## Changelog
- 2026-06-20 — Claude Code — **VM Security Manager Phase 3 (EOD integration) + CRITICAL email dedupe.**
  **(A) Double-email fix** — `security_monitor._send` + `copy_gate.send_alert` used to write a sentinel
  explicitly AND let `TelegramNotifier.send()` write one (TG5) → 2 emails per security CRITICAL. Now
  they capture `send()`'s `SendResult` and write their own ONLY when `send()` did not
  (`result.sentinel_path is None` — notifier missing / Telegram disabled via the master switch / send
  raised), so each CRITICAL = **exactly one email**, and email still fires when Telegram is unavailable
  (the sole channel while Telegram is banned to 22-Jun). **(B) System Manager 9th check**
  `security_check` (18:45, after tomorrow_readiness): (1) daily copy-audit summary from
  `data_store/security/copy_audit.log`; (2) a `COPY_BYPASS_DETECTED` / `COPY_PROTECTION_DISABLED` today
  is a **VIOLATION** → escalates the EOD report to CRITICAL (→ email) + exit 3, **but never trips the
  trading SOFT_KILL** (security ≠ trading-safety halt); (3) health: auditd `copy_attempt` rules loaded
  (`sudo -n auditctl -l`), `copy_protection.enabled`, audit-log writable, **security-watcher alive**
  (`security_state.json` freshness). `security_monitor` now persists freshly-alerted bypass/switch
  findings to `copy_audit.log` (single EOD source; post-dedup only → no per-pass bloat). **(C) Cron
  Officer** `--eod-summary` appends a **security-watcher liveness** line and escalates to CRITICAL if
  the watcher is stale/down (`build_eod_summary` signature left intact). **Verified live on the VM:**
  the 9th check renders 0 violations with `3 auditd rules / enabled / watcher alive (42s)`, and the
  Cron Officer prints the watcher line. +15 tests (`test_system_manager_security.py`,
  `test_cron_officer_security.py`, `_send` dedupe + copy-audit persistence in `test_security_monitor.py`).
  Commits 202b98f (A), bb351e0 (B+C). NB pre-existing `test_system_manager.py::test_report_integrity_*`
  fails on any day ≠ 2026-06-19 (date-hardcoded; verified via stash, not a regression). Memory
  `vm_security_phase2`.
- 2026-06-20 — Claude Code — **VM Security Manager Phase 2 (copy protection) — BUILT (code in git;
  OS activation staged).** Controls **VM→PC** copying. New `scripts/copy_gate.py` = policy engine
  (strict priority: **TIME-LOCK 18:00–08:00 IST is absolute** and overrides even the OFF switch / a
  live token › master switch OFF = unrestricted › session-cap › a valid 15-min token) + atomic token
  store (`data_store/security/copy_token.json`) + append-only JSON-lines audit
  (`data_store/security/copy_audit.log`). New `scripts/request_copy.py` (`request-copy <reason>`,
  `--status`, `--revoke`) mints a 15-min token, audited + Telegram. `deploy/security/bin/copy-guard`
  (symlinked as `/usr/local/bin/{scp,sftp,rsync}`) consults the gate before exec'ing the real
  binary — **hard-blocks VM-INITIATED copies**, fail-safe (blocks if the gate errors); nothing
  automated on the VM uses these clients, and git/ssh are unwrapped so no lockout. `security_monitor.py`
  gained **2 checks** (now 9): (8) `copy_protection` ON→OFF transition → CRITICAL (who/when), (9) auditd
  `copy_attempt` bypass — an outbound scp/sftp/rsync run with no token → CRITICAL (conservative: skips
  inbound `scp -t` sinks so PC→VM pushes don't false-fire). `copy_protection:` block added to the
  standalone `config/security.yaml` (NOT system_config.yaml); auditd execve rules added to
  `deploy/security/trading-security.rules`. **Known limitation:** a PC-INITIATED *pull* (PC is the
  client; VM sshd serves it) can't be hard-blocked from the VM without risky `ForceCommand`/subsystem
  changes on the live trading VM → **detect+alert only** (and modern scp's sftp-subsystem path may
  evade the execve rule; reliable pull-detection needs the deferred sftp `-l INFO` logging). 52
  unit tests green (`test_copy_gate.py` + extended `test_security_monitor.py`); CLIs smoke-tested
  end-to-end (deny→issue→allow→revoke lifecycle in the audit log). **DETECTION-ONLY ACTIVATED on the
  VM 20-Jun** (Rama's choice): auditd `copy_attempt` rules installed (`/etc/audit/rules.d/`,
  `augenrules --load`; the `b64` rule confirmed capturing `scp` execve on this **aarch64** VM) + the
  two monitor checks verified live (real `ausearch -i` parsed, 0 false-positives on a benign `scp`).
  **Blocking wrappers deliberately NOT symlinked** → Rama's `scp` workflow is unchanged; run
  `bash deploy/security/install_copy_protection.sh` later to add hard-block. **Follow-up commit
  7129b38** fixed two findings surfaced by the live aarch64 box: real `ausearch -i` uses a **2-digit
  year** (parser now accepts 2-/4-digit) and `ausearch` **blocks on stdin** (now `stdin=DEVNULL`,
  query uses the locale-proof `-ts recent`). Phase 3 (System Manager EOD integration — a 9th
  System-Manager security check reading the copy audit log) still pending. Memory `vm_security_phase2`.
- 2026-06-19 — Claude Code — **VM Security Manager Phase 1 (monitoring + alerts) — built + deployed.**
  Alert-ONLY (never blocks; key-only SSH is the gate). New `scripts/security_monitor.py` (7 isolated
  checks: new/changed authorized_keys, non-whitelisted sudo, failed-login spike, NEW successful-login
  IP, sensitive-file content-hash change, active-session count, root-probe spike), `config/security.yaml`
  (standalone — system_config.yaml is `extra="forbid"`), `deploy/systemd/security-watcher.service`
  (`Type=simple` periodic ~60s), `deploy/security/jail.local` (fail2ban) + `trading-security.rules`
  (auditd), 15 unit tests. **Live on VM:** installed fail2ban+auditd; auditd 10 file-watches loaded;
  fail2ban sshd jail running (pre-tested — 0 legit/management IPs in the bannable set; bans rare as
  the botnet is low-per-IP); security-watcher enabled+active; baseline captured; **SSH access verified
  intact at every step**. Whitelist path bug (fail2ban-client/auditctl bin↔sbin) found via the live
  first pass + fixed. NOT done: SSH hardening (needs Rama OK), copy-protection (P2), EOD integration
  (P3). Commits a701b0d→fcfa9a8. Memory `vm_security_phase1`.
- 2026-06-19 — Claude Code — **Cron env-export fix (ROOT CAUSE) + reconcile_positions creds (Item A)
  + per-episode drift logging (Item B).** Investigating the 15:45 `reconcile_positions` FAILED revealed
  a deeper root cause: cron jobs ran **without any `.env` secrets** — `.env` is bare `VAR=value` (no
  `export`), so `. ./.env && python` sets shell vars the child Python never inherits (proven on VM:
  all secrets MISSING under the cron pattern, SET with `set -a`). FIX-189 made jobs *run*; they still
  got no secrets. **Fix:** every Python cron line now sources via **`set -a && . ./.env && set +a`**
  (allexport) — `deploy/cron/trading-system.cron` (29 lines) + `config/cron_registry.yaml` header +
  `TestCronEnvExport`. Crontab **reinstalled** on VM (backup in `data_store/crontab_backups/`); probe
  confirms `ZERODHA_API_KEY_LFL836`/`TELEGRAM_*` now reach Python; all 5 broker-crons wrapped.
  **Item A:** `scripts/reconcile_positions.py` read never-set generic `ZERODHA_API_KEY`/`ACCESS_TOKEN`
  → now mirrors `refresh_instruments` (`_resolve_credentials`: per-account `api_key_env` +
  `access_token` from token JSON via `load_token`; `--account` default LFL836, generic fallback). Cron
  passes `--account LFL836`. **Verified end-to-end on VM: exit 0 + heartbeat SUCCESS** (was exit 1 /
  FAILED). **Item B:** `order_reconciler._g3_capital_drift` logged a `reconciliation_log` row every 15s
  cycle even while the alert was throttled (144 rows for 3 alerts on 19-Jun) → now per-EPISODE
  (`_drift_episode_active`): a row only on episode-start or a real alert; throttle unchanged. Tests:
  130 affected unit tests green (cron_registry 23, reconcile 25, order_reconciler 82). Commits bff0cad
  (env-export+A), 89c0c20 (B). Item A activates Monday 15:45; env-export already live; Item B next restart.
- 2026-06-19 — Claude Code — **Doc: `docs/system_manuals/*.docx` location recorded.** Three Word
  manuals (`trading_System_v2_runbook.docx`, `config_file_guide.docx`,
  `chartink_mounted_strategies.docx`) live in `docs/system_manuals/`. **Gitignored** (added
  `docs/system_manuals/*.docx` to `.gitignore`, commit 2424a21) → NOT carried by `git push`;
  synced to the VM out-of-band via `scp` (verified byte-identical + valid docx). Added to Related Docs.
- 2026-06-19 — Claude Code — **TASK #8: `docs/CONFIG_GUIDE.md`** — comprehensive Rama-facing
  config reference. Audited all config files (`system_config.yaml`, `scoring_weights.yaml`,
  `broker_costs/limits.yaml`, `slippage_model.yaml`, `scan_webhook_map.yaml`,
  `chartink_scanners.yaml`, `cron_registry.yaml`, `accounts.csv`, `strategies/*.yaml`, `.env`).
  12 sections + a "Currently Effective Values" quick table, an **Override Precedence** section
  (live_test_mode > base caps; dual daily-loss; smallest sizing cap wins; force_intraday_only),
  Common Scenarios with edit+restart commands, Dangerous-Changes warnings, Quick Commands.
  Key correction documented: **`mode` (paper/live) is the `--mode` CLI flag in the systemd
  ExecStart, NOT a YAML key** — and **config edits require a restart** to take effect.
  Cross-referenced under Related Docs. Docs-only.
- 2026-06-19 — Claude Code — **Task: standalone TGT retry mechanism** (closes the
  "TGT fails forever" gap left by FIX-190 Bug C — the 19-Jun THELEELA TGT that failed
  at 10:00:28 and was never retried). When a LIMIT_TRIPLE TGT can't be placed but the
  SL is live (Bug C SL-only), `order_placer._persist_sl_only_protected` now flags the
  trade (`trades.needs_tgt_retry=1`, new schema **v30** columns: needs_tgt_retry /
  tgt_retry_count / tgt_last_retry_at — auto-migrates v29→v30 on restart). New
  `orders/tgt_retry_manager.py` `TGTRetryManager` (30s daemon, started after smart_tgt,
  stopped in `_shutdown`) re-attempts the TGT on exponential backoff
  (**30/60/120/240/480s, give up after 5** → position stays SL-protected) via
  `OrderPlacer.retry_tgt_for_trade` → `FullEntryEngine.place_deferred_tgt_only` →
  `LimitTripleProtocol.place_tgt_only` (re-clamps to the CURRENT circuit band each try —
  Bug D; the band may relax). Guards: SL must still be standing (never place a naked
  TGT), no double-TGT (idempotent), never place an unprofitable TGT, skip while the kill
  switch is active or outside market hours. The placed TGT is registered in `_fill_map` +
  order_monitor so a fill triggers the software OCO (cancels the SL). Telegram INFO on
  success / WARNING on give-up. Config `tgt_retry:` (enabled/poll_interval_sec/max_attempts/
  backoff_base_sec; optional, defaults reproduce the schedule). State lives in the trades
  table → survives restart. 18 tests in `test_tgt_retry.py`; 454 affected-suite tests green
  (v29→v30 migration verified live). Activates on next restart. Files: `orders/tgt_retry_manager.py`
  (new), `orders/order_placer.py`, `orders/order_protocol_limit.py`, `orders/full_entry_engine.py`,
  `core/schema.sql`, `core/migrations.py`, `core/state_store.py`, `core/config_loader.py`,
  `config/system_config.yaml`, `main.py`.
- 2026-06-19 — Claude Code — **Task 4: reconciler resolves trades stuck in EXITING**
  (closes the `followup_reconciler_exiting_gap` exposed by the 19-Jun incident). A
  HARD_KILL / emergency flatten marks a trade EXITING before flattening (Bug A, FIX-190);
  if the process dies mid-exit the trade lingered in EXITING with locked capital + orphan
  SL/TGT, and CHECK1/G5b never touched it (they only see OPEN/PARTIAL/PENDING_FILL) — the
  morning incident needed a manual EXITING→OPEN flip. New `_check_stuck_exiting` runs each
  reconcile cycle (startup via `reconcile_once()` + every 15s) inside the broker-positions
  guard: for EXITING trades older than `reconciler.stuck_exiting_timeout_minutes` (default
  **30**), **flat at broker → CHECK1 finalize (CLOSED_MANUAL + release capital + cancel
  orphans)** — `mark_trade_manually_closed` now accepts EXITING; **still holding → revert
  EXITING→OPEN** (`revert_exiting_to_open`) + WARNING so SL/TGT/EOD/kill-switch resume
  management. Fresh EXITING (active exit) is left alone. CHECK2 guarded so a held EXITING
  position is not mis-adopted as an orphan. New `state_store.get_stuck_exiting_trades()` +
  `revert_exiting_to_open()`; EXITING→OPEN added to the crash-test state-machine validator;
  config key `stuck_exiting_timeout_minutes`. 6 new tests (incident replay flat→CLOSED_MANUAL,
  fresh-not-touched, stale-held→OPEN, + 3 store helpers). 323 touched-suite tests green
  (`test_main` waitress failures are a pre-existing local-venv gap, not a regression).
  Activates on next restart. Files: `orders/order_reconciler.py`, `core/state_store.py`,
  `core/config_loader.py`, `config/system_config.yaml`, `tests/crash_test/state_machine_validator.py`.
- 2026-06-19 — Claude Code — **Bug B metrics: `/metrics` broker quota gauges.** Added
  `broker_in_flight` / `broker_filled_today` / `broker_quota_used` / `broker_quota_max` /
  `broker_quota_available` to `SignalProcessor.get_runtime_metrics()` (merged into `/metrics`
  via the existing `metrics_provider`). They mirror the reservation-aware DAILY_TRADES gate
  exactly — `used = max(daily_count, settled_today + count_live_reservations())` — so live
  monitoring reflects the real cap decision, not a parallel tally. Fully guarded (best-effort;
  never raises/blocks). 2 tests. Files: `signals/signal_processor.py`. (NB: the `/metrics/prometheus`
  text endpoint is unchanged — it already omits the runtime counters.)
- 2026-06-19 — Claude Code — **Bug B: reservation-aware DAILY_TRADES cap** (extends
  FIX-185 to the daily limit). Audit found the prior "B already correct" was only
  half-true: FIX-181 fixed *what* counts (rejects excluded → retry) and FIX-190 added
  funnel metrics, but the `DAILY_TRADES` check was still a bare `count_trades_today`
  DB read with **no in-flight accounting** — the daily-cap twin of the position-cap
  TOCTOU race FIX-185 already closed. `approve()` reads `daily_count` inside
  `portfolio_lock`, but the candidate's `PENDING_FILL` trade row is inserted later by
  `order_placer.place()` **outside** the lock, so a burst all read the same pre-burst
  count and passed → overshoot (18-Jun 8-vs-5). Fix mirrors FIX-185 exactly:
  `effective_daily = max(daily_count, count_settled_trades_today() + count_live_reservations())`
  — `settled_today` = today's executed trades minus `PENDING_FILL`; `live_reservations`
  = the in-flight half (reserved-not-placed + `PENDING_FILL`); the two partition with
  no double-count (commit pops the reservation at fill), and `daily_count` (incl.
  `PENDING_FILL`) is the restart floor. `reserve()` runs inside the same lock, so the
  count is race-consistent. Rejections enter neither term → slot frees → next signal
  retries to reach max (the "5→3 undershoot" was never a counting bug — just no further
  signals). New `state_store.count_settled_trades_today()` + 6 unit tests (burst race,
  final-slot, rejection-frees-slot, restart floor, 18-Jun overshoot replay, settled-count
  partition). 346 tests green locally. Files: `capital/risk_engine.py`,
  `core/state_store.py`, `tests/unit/test_risk_engine.py`, `tests/unit/test_state_store.py`.
  Activates on next restart. (NB: `REJECTED` is NOT a valid `trades.status` — rejects are
  `FAILED`/`CANCELLED` or never get a trade row.)
- 2026-06-18 — VS Code Claude — Initial creation. Full VM+PC audit (structure, configs, modules,
  DB, logs, ~28 cron jobs, 4 systemd services, tools, deploy mechanism). Flagged: root `trading.db`
  (0-byte dead), crontab divergence, sentinel accumulation, duplicate git remote, crash-loop.
- 2026-06-18 — VS Code Claude — Cleanup: removed dead `trading.db`, deleted 56 stale `*.flag` files,
  moved dated notes (`CRON_FIX_2026_05_18.md`, `FIXES_DAILY_REPORT_2026_05_18.md`) to `docs/archive/`,
  removed `deploy_audit_fixes.ps1`, removed duplicate `vm` git remote. Discovered `alert-watcher.service`
  is INACTIVE (now tracked as open issue #3).
- 2026-06-18 — VS Code Claude — Infra fixes: (A) `alert-watcher.service` enabled + started (survives
  reboot) — delivery still blocked by placeholder SMTP config (issue #3). (B) systemd unit
  `RestartPreventExitStatus=3 4` (live + `deploy/systemd/trading-system.service`) so an exit-4 HALT no
  longer crash-loops — verified service settles to `failed` (NRestarts froze). (C) deleted 0-byte PC
  `trading.db`.
- 2026-06-18 — VS Code Claude — SMTP configured for alert-watcher: `alerts.smtp` → Gmail
  `ramakrishnan031@gmail.com` (send+receive), `ALERT_SMTP_PASSWORD` env; crisp account-tagged email
  subject `[LFL836] <SEVERITY> — <title>` (single + digest builders in `alert_watcher.py`). Transport
  verified (STARTTLS connects) but Gmail returns 535 BadCredentials — `ALERT_SMTP_PASSWORD` is not a
  valid 16-char App Password. Issue #3 BLOCKED on a valid Gmail App Password (Rama to set).
- 2026-06-18 — VS Code Claude — SMTP delivery VERIFIED after a valid Gmail App Password was set:
  16 pending sentinels delivered (`.flag`→`.delivered`), digest emailed. Issue #3 resolved.
- 2026-06-18 — Claude Code — FIX-187: `scripts/auto_refresh_token.py` rewritten to a fully
  headless TOTP login — eliminates the daily manual OTP step (08:00 Mon-Fri cron, unchanged).
  Reuses `zerodha_login.exchange_request_token`+`save_token` (token-format parity: passes
  `is_token_valid()` and carries `api_key` for the paper quote provider); `request_token` via the
  `connect/login?v=3` redirect chain; account-specific→generic credential resolution; Telegram +
  retry + cron-heartbeat. `pyotp==2.9.0` added to requirements and installed in the venv. **Verified
  live** (token valid + `kite.profile()` OK + heartbeat SUCCESS). TOTP secret is the per-account
  `ZERODHA_TOTP_<acct>` in `.env` (single source of truth; a stale/duplicate `ZERODHA_TOTP_SECRET`
  was removed). Commit ff0984b.
- 2026-06-18 — Claude Code — FIX-188: **headless fixes**. (A) `deploy/token_watcher.sh` is now
  exit-code aware (ExecMainStatus/ExecMainExitTimestamp): same-day exit 4 (HALT) / exit 3 → no restart
  + 1 Telegram/day + 5-min back-off; prior-day 4/3 → one clean start (auto-recovers overnight HALTs);
  exit 1/2 → restart w/ 3-per-hour backoff; clean exit-0 today → no post-EOD restart. Stops the 30s
  hammer-restart of a HALT-failed service. (B) `scripts/healthcheck_server.py` `/health` now reports
  `{db, token, kill_switch}` and returns 200/503 (reads the `kill_switch_state` table; also fixed the
  same latent wrong-table bug in `/metrics`). Verified live; service handed off to systemd (active).
  Commits 7a12cf2, 8cf8685.
- 2026-06-18 — Claude Code — FIX-188b: **systemd-friendly resume**. `scripts/clear_kill_switch.py`
  clears (resumes) the kill switch in the DB via `KillSwitch.resume()` WITHOUT acquiring the instance
  lock or starting the loop (`--dry-run`, `--force` for HARD_KILL); `deploy/resume.sh` wraps
  stop → clear → reset-failed → start under systemd. Closes the manual-vs-systemd `--resume` lock
  conflict (no more standalone `main.py --resume` competing for port 5001). 6 tests; live dry-run
  verified. Commit 9ddcddf.
- 2026-06-18 — Claude Code — TASK #3: **Cron Officer**. New `config/cron_registry.yaml` (single source
  of truth, 30 jobs) + `core/cron_registry.py` loader; `scripts/cron_officer.py` (`--briefing` 04:55,
  `--eod-summary` 18:30, `--check-change`); `check_cron_drift.py` now registry-driven (hardcoded list
  removed). Universal heartbeats via `HeartbeatTimer(alert=True)` (8 silent jobs instrumented) +
  `alerts/cron_alerts.py` per-job alert policy (reuses TelegramNotifier tiers; honors the master switch)
  + `skip_if_non_trading_day()` holiday guard. **Crontab regenerated + installed** (live == canonical,
  33 lines): `auto_refresh_token` 08:00→08:15; +`cron_officer` briefing/eod; +analytics.db backup (01:05);
  +`db_retention` (02:30 + Sun VACUUM, activated); backup-retention now covers analytics. Old crontab
  backed up to `data_store/crontab_backups/`. 40 new tests. Commits …→6fd0ed9.
- 2026-06-19 — Claude Code — **FIX-189: dash-cron + overnight-run + false-alert fixes.**
  Root cause of the 19-Jun morning incident: cron's default `/bin/sh` is dash, so
  the bare `. .env` in every market job failed (`sh: .: .env: not found`) — no token
  refresh (08:15), no heartbeats, no per-cron logs. FIX-187's "verified live" had only
  ever been exercised via a manual **bash** run, so the dash bug never surfaced.
  Fixes: **(P0-A)** `deploy/cron/trading-system.cron` now leads with `SHELL=/bin/bash`
  and sources via `. ./.env` (belt-and-suspenders); registry header documents the rule.
  **(P1-A)** `main.py` exits 0 outside the broad service window [08:00–16:00 IST] and
  `deploy/token_watcher.sh` only starts the service in-window — the service no longer
  runs overnight (the 18-Jun 23:22 start that produced the false 04:24 capital-drift and
  07:07 KiteTicker CRITICALs). **(P1-B)** `order_reconciler._g3_capital_drift` skips the
  alert when broker `net==0.0` outside market hours (overnight funds endpoint returns 0),
  and `live_feed._on_noreconnect` downgrades max-reconnect-exhausted to WARNING (no
  SOFT_KILL/CRITICAL) outside market hours — both still escalate normally in-session.
  **(P2)** token-watcher start is window-gated + already idempotent on `ActiveState`.
  16 new tests. Kite dev-console IP allowlist remains Rama's external step. Commits 2ac32ae,
  0f722f7, 5603013, 66380b6 — pushed+deployed; crontab installed; service recovered (`/health`
  HEALTHY).
- 2026-06-19 — Claude Code — **FIX-190 Stage 4: incident fixes** (commits 51f5e94→870bbc9).
  Root-cause cluster of the 10:00 cascade fixed: **C** TGT-only failure returns a partial
  (SL-protected) result instead of raising → no emergency-exit/HARD_KILL; **D** SL/TGT clamped
  into the circuit band before placing; **A** reverse-aware flatten (`broker/position_helpers`)
  in the HARD_KILL first pass + emergency exit (skip if flat, BUY to cover a short, mark EXITING)
  → no oversell; **E** cancel a trade's resting SL/TGT before any flatten → no orphans; **F** G5b
  skips recovery-SL when a live SL already exists → no duplicate SL. Guardrails: **H**
  `live_test_mode` (RiskConfig; live caps max_open=1 / 3-per-day), **I** in-session capital-drift
  tolerance = max(Rs, expected*10%). Defensive hardening of the new flatten helpers (never crash
  the indestructible loop). **G** entry throttle (signal_processor 20s / 3-per-60s, 575a1dc).
  **B** already correct (FIX-181 excludes rejects + FIX-185 reservation cap; only metrics deferred).
  Incident replay test (3af8310) green. Paper mode SKIPPED per Rama (stay LIVE, tiny capital).
  Service stays DOWN until Rama's explicit go-ahead → LIVE with live_test_mode.
- 2026-06-19 — Claude Code — Cleanup: removed **445** stale `critical_alert_*.flag` from the **PC**
  `data_store/` (31-May→18-Jun, mostly `source_module:test`). Local-only/gitignored — never tracked,
  no `.gitignore` change. VM already clean (alert-watcher consumes → `.delivered`).
- 2026-06-19 — Claude Code — **FIX-189 (P1-A completion): EOD window-end self-exit.** Found while
  verifying the assumed "EOD self-exit" that it did **not** exist (runtime loop only waits on the
  shutdown event; EOD squareoff just trips a scheduled SOFT_KILL; no timer/cron stops the process) —
  so a service started in-window ran all night (harmless after P1-B, but not clean). Added an
  `eod-self-exit` daemon thread (`_start_eod_self_exit_thread` / `_eod_self_exit_due`): once past
  `SERVICE_WINDOW_END` (16:00 IST) **and** flat (`StateStore.count_active_positions()==0` over
  OPEN/PARTIAL/PENDING_FILL) it sets the shutdown event → `main()` exits 0 → systemd won't restart →
  token-watcher's exit-0-today path skips the restart → clean overnight + clean 08:30 start. **Never**
  exits while a position is open (stays up to manage residual positions; count error → stays up).
  Armed only for normal starts (skipped for `--interactive`/`--resume`/`TS_IGNORE_MARKET_WINDOW`).
  Parity-safe. 6 new tests. Commit d61ece9. Activates on next service restart (≈ next 08:30).
- 2026-06-19 — Claude Code — **TASK #5: System Manager EOD** (`scripts/system_manager.py`, 18:45
  Mon-Fri, after the Cron Officer EOD). Deep daily cross-check beyond job execution: 8 isolated
  checks (config-vs-actual w/ live_test_mode effective caps · order quality/slippage · report
  integrity on REAL paths · system health: PRAGMA integrity_check/heartbeats/disk/token/restarts ·
  strategy health · risk events: HARD_KILL via LOG since it's not in any table · vs-yesterday ·
  tomorrow readiness). Telegram + CRITICAL-sentinel→email + saved `reports/system_manager/<date>.txt`.
  Trips tomorrow's SOFT_KILL only on a real violation (cap breach / DB-integrity fail / HARD_KILL
  today). Registry + canonical crontab updated (FIX-189 `. ./.env`) + installed (registry==crontab).
  10 unit tests; validated on real VM data. First run Mon 22-Jun. Commits 8d37f4f→29e477e.
- 2026-06-19 — Claude Code — **live_test_mode → PERMANENT bug-hunting caps**: raised
  `risk.live_test_max_open_positions` 1→**4** and kept `live_test_max_entries_per_day`=**6**
  (commit 6b6979c). Rama's strategy — active bug-hunting on live with controlled exposure: 4
  concurrent positions exercises concurrency/race paths, small qty (1-2 shares) on ₹10k caps loss.
  No auto-disable (manual only). Effective live caps now 4/6 (override base 5/20). Activates on
  next restart (today's 16:00 EOD self-exit → Mon 08:30 auto-start). NOT temporary; no revert.
- 2026-06-19 — Claude Code — **Bug G entry throttle — full integration** (commit c0554c6). The
  global throttle (min-gap 20s + burst 3/60s) was already wired in FIX-190; this adds a clean
  `signals/entry_throttle.py` `EntryThrottle` (thread-safe, ATOMIC `admit(symbol)` check-and-record —
  no TOCTOU burst race) with a NEW **per-symbol cooldown** (`per_symbol_cooldown_sec: 300` — no
  re-entry of the same symbol within 5 min) and **per-reason /metrics** (entries_throttled_min_gap /
  _burst / _per_symbol / entries_admitted). Single chokepoint at both `place()` sites; DROP on
  throttle (no queuing). 13 unit tests incl. 19-Jun 5-in-5s burst replay (→1 admitted) + thread-safety
  (50 concurrent → exactly burst_max). Activates next restart.
