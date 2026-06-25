# SYSTEM MAP — Trading System v2
# Last updated: 2026-06-25 by Claude Code (Opus) — SLICE2.5-P2: durable CNC overnight-GTT (schema v36 gtt_state) + CncGttMonitor reconcile/GTT_EXIT/recreate/F6/qty-mismatch/soft-kill + 15-min in-hours monitor + R2 guard split + delivery-trade exclusion from reconciler checks + GTT-exempt-from-sweeps; delivery_enabled stays false. DEPLOYED to main bad0aad 25-Jun ~22:23 (one-time authorized; standing rule restored); schema v36 applies at the Fri 08:15 in-window boot
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

### Agent CLIs (`~/tools/`, OUTSIDE the project)
| Path | What | Used by |
|---|---|---|
| `~/tools/antigravity/agy` | **Antigravity CLI** (`agy`, ~164 MB binary). Google-AI-Pro OAuth (`~/.gemini/`), NOT an API key. Engine behind the AI-ops crons. Governed by `AGENTS.md` ("Cross-Tool Agent Charter") when run in the project dir. | `scripts/gemini_*.py` (premarket brief, log review, trade coach, data integrity, weekly patterns); `.env` `GEMINI_BIN` |
| `~/tools/gemini/` | Node-based **Gemini CLI** (package.json + node_modules: @google, keytar, node-pty). | secondary AI tooling |
| `~/tools/claude/` | **Claude Code CLI** dir. `cron.log` = output of the 4×/day heartbeat (`/usr/bin/claude -p "random 8-char string"` @ 05:30/10:31/15:32/20:33). | claude heartbeat cron |

> ✅ **The claude heartbeat cron is GOVERNED** (23-Jun): the 4 cron lines now `cd /home/ubuntu/tools/claude`
> first, loading `~/tools/claude/AGENTS.md` (charter) + `~/tools/claude/.claude/settings.json` (hard
> `permissions.deny`: `Write/Edit(**/*.py)`, `Read/Write/Edit(**/*.db)` + `Bash(sqlite3:*)`,
> `Bash(systemctl|sudo|service:*)`, `Write/Edit(~/systems/**)`, read of the trading `.env`, `git push`).
> Mirrors AGY's prohibitions (no `.py` writes, no DB, no systemctl). **Verified 23-Jun:** heartbeat still
> works; a `.py` write AND a `.db` read were both blocked (the `.db`-read block — normally default-allowed —
> proves `settings.json` is loaded). Guardrail files are **VM-local** (outside the repo); crontab backup at
> `~/tools/claude/crontab.backup.*`. ⚠️ These 4 heartbeat lines live in the **live crontab only** — NOT in
> `deploy/cron/trading-system.cron`; a future canonical crontab reinstall would drop them (reconcile if you want them permanent).

### Deploy (git push, NOT scp)
- Bare repo: `/home/ubuntu/trading-system.git/` with `hooks/post-receive`.
- Hook does: `git --work-tree=/home/ubuntu/systems/trading-system --git-dir=… checkout -f <branch>`.
- It does **NOT** restart the service. (See PC Paths → SCP Deployment Map.)
- **Self-maintaining cron (ARMED 23-Jun):** `post-receive` (1336 B, from `deploy/hooks/post-receive`) now ALSO regenerates the crontab from the deployed `cron_registry.yaml` and **auto-installs** it *iff* `generate(registry) == deploy/cron/trading-system.cron` (else WARNs + skips — self-protecting). Replaced the old 329-B checkout-only hook. The **pre-receive** equality guard (`deploy/hooks/pre-receive`) is **DEFERRED — NOT installed** (deliberate, 23-Jun); `pre-commit` (`deploy/hooks/pre-commit`) is local-clone-only. See memory `cron_framework_armed_23jun` + Changelog 23-Jun.

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
| `cron_registry.yaml` | **Cron SINGLE EXECUTABLE source of truth** (~41 lines; TASK #3 + 23-Jun framework) → `scripts/generate_crontab.py --generate` emits `deploy/cron/trading-system.cron` (ASCII+LF, deterministic) | `core/cron_registry.py` → `cron_officer`, `check_cron_drift`; `scripts/generate_crontab.py` |
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
| `orders/` | Order lifecycle | order_placer, order_reconciler (15s; + SLICE2.5-P2 startup/15-min CNC-GTT monitor hook + delivery-trade exclusion), order_manager, eod_squareoff, smart_tgt_manager, tgt_retry_manager (30s TGT re-place), breakeven_manager, sl_breach_monitor, entry_engine, full_entry_engine, order_protocol_co, order_protocol_limit, price_math, shadow_tracker, cnc_gtt (CNC OCO-GTT placer + durable gtt_state persist/hydrate), **cnc_gtt_monitor** (overnight-GTT reconcile + GTT_EXIT + K6 ladder) |
| `signals/` | Ingestion | webhook_receiver, signal_processor, entry_throttle (Bug G: global min-gap/burst + per-symbol cooldown) |
| `screening/` | Signal screening/scoring | entry_gate, quality_scorer, secondary_screener, step_executor |
| `data/` | Market data | live_feed (WS ticks), candle_store |
| `alerts/` | Alerting | telegram_notifier, critical (sentinels) |
| `strategies/` | Strategy config | loader, schema |
| `utils/` | Utilities/preflight | startup_checks, holiday_guard, instance_lock, cron_heartbeat |
| `reports/` | Reporting | daily_report, daily_review, style_constants |
| `scripts/` | ops/cron scripts | incl. `cron_officer.py` (TASK #3 briefing/eod/check-change) + `cron_report_render.py` (rich HTML/Telegram render, pure), `system_manager.py` (TASK #5 EOD deep cross-check, 18:45), 6 `gemini_*.py` (AI ops), auto_refresh_token, premarket_healthcheck, reconcile_positions/pnl, eod_cleanup/verify, etc. |
| `tests/` | 305 test files | unit/, integration/, crash_test/ |
| `main.py` (root) | App entrypoint | launched as `main.py --mode live` |

### Database  (`data_store/`)
- `trading_system.db` (~161 MB on VM) — **MAIN** DB (schema **v36**; **v36 added `gtt_state`** — durable one-OCO-GTT-per-CNC-trade source of truth, SLICE2.5-P2, pure addition; v35 added trades.tgt_risk_reward_applied/exits_verified; v33 preflight_*; v32 tolerance_*; v30 added trades.needs_tgt_retry/…; v31
  added the slippage-intelligence layer: order_execution_log + trade_slippage_log + market_execution_context;
  **v32 added trades + order_execution_log `tolerance_fraction_used` / `tolerance_source` for the Phase 3a
  slippage tolerance override hierarchy**). trades, orders, signals, fm_ledger, etc.
- `analytics.db` (~44 KB) — analytics split (v28); **ATTACHed** to the main DB at runtime.
- **Raw sqlite access MUST use `core.db_connect.connect`** (it sets up the ATTACH); plain `sqlite3`
  works only for read-only SELECTs against the main file.
- `data_store/session/zerodha_token.json` — broker session token (wiped 05:00 daily by cron).
- `data_store/backups/` — nightly `.backup` snapshots (7-day retention).
- `data_store/candles/` — candle artifacts.
- `data_store/critical_alert_*.flag` — CRITICAL sentinels written by `alerts/critical.py`,
  consumed by `alert-watcher.service` (VM) → renamed to `.delivered`. **Local-only/gitignored**
  (`data_store/` is ignored). **Retention (23-Jun):** the `sentinel_retention` cron (02:05 daily)
  deletes `critical_alert_*.delivered` older than 7 days and **NEVER** touches `*.flag` (pending) —
  email is the authoritative record, so there is no archive. The PC dev tree has no watcher, so test
  runs that write real sentinels accumulate `.flag` there; **root-caused 23-Jun** by pointing
  `test_fix132_email_fallback.py`'s `sentinel_dir` at a tmp dir (was `"data_store"`). VM stays clean
  (0 `.flag`, watcher healthy); PC's 42 test flags removed 23-Jun (445 earlier on 19-Jun).

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

### SATS — Static Analysis (PC-only, manual; never deploys)
`sats/` is **git-ignored** (the scanners never reach the VM). Two pre-installed isolated
venvs hold the tools; run the scan `.bat`s **on demand** — no hooks, no automation:
| What | Path |
|---|---|
| SATS root | `D:\Projects\trading-system\sats\` |
| Bandit exe | `sats\bandit-env\Scripts\bandit.exe` (1.9.4) |
| Semgrep exe | `sats\semgrep-env\Scripts\semgrep.exe` (1.167.0) |
| Scan scripts | `sats\scripts\scan_bandit.bat`, `sats\scripts\scan_semgrep.bat` |
| Reports | `sats\reports\{bandit,semgrep}_<yyyyMMdd_HHmmss>.txt` |
| Semgrep baseline | `sats\semgrep_baseline.txt` (pinned commit; only findings NEW since it are reported) |

- Both scan the repo root (`-r` / target = `D:\Projects\trading-system`), **exclude `venv,sats,.git`**,
  and write a timestamped txt report + echo it to the console (`chcp 65001`, locale-independent
  PowerShell timestamp, auto-create `reports\`, tool run ONCE via `-o`/`--output` then `type`).
- Semgrep rulesets `p/python` + `p/security-audit` (login-free; first run downloads, cached after).
- Bandit reports all severities; add `-ll` to filter to medium+ if noisy.
- Bandit `-x` uses **absolute** paths — `bandit/core/manager.py` matches each token both as an
  fnmatch glob **and** as a path substring.
- **Windows UTF-8:** both `.bat`s set `PYTHONUTF8=1` — without it Semgrep/Bandit **crash** writing the
  report (`UnicodeEncodeError`); `chcp 65001` fixes only the console, not Python's cp1252 file writes.
- **Semgrep baseline:** if `sats\semgrep_baseline.txt` exists, `scan_semgrep.bat` adds
  `--baseline-commit <hash>` (and runs from the repo root for git) so only findings **NEW** since that
  commit are shown; delete the file for a full scan. Pinned at `65439ff` (2026-06-22 SATS triage).
- ⚠️ **Do not** modify / activate / reinstall the two venvs.

---

## Cron Jobs  (live `crontab -l` == `deploy/cron/trading-system.cron` == `generate(registry)` — **verified four-way sha256 `1469f905…` 23-Jun; 41 command-lines**; **source of truth: `config/cron_registry.yaml`** — TASK #3 + 23-Jun framework. The table below is an illustrative summary — the canonical/registry are authoritative.)
All market jobs run `cd … && set -a && . ./.env && set +a && PYTHONPATH=. venv/bin/python <script> >> logs/<log>`.
To change cron: edit `config/cron_registry.yaml` → `scripts/generate_crontab.py --generate --out deploy/cron/trading-system.cron` → commit + push (the `post-receive` hook **auto-installs** on the VM; or manually `crontab deploy/cron/trading-system.cron`). `--gate` proves zero-drops (+ only `sentinel_retention`); `--selftest` proves byte round-trip.

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
| 02:05 | daily | `find data_store -name 'critical_alert_*.delivered' -mtime +7 -delete` | **sentinel_retention** (DELIVERED alerts >7d, NEVER `.flag`; 23-Jun +1) |
| 02:30 | daily | `db_retention.py` (Sun: `--vacuum`) | DB row prune (TASK #3) |
| 09:20 | daily | `cron_officer.py --briefing` | Cron Officer morning briefing (was 04:55; live since 21-Jun reinstall) |
| 05:00 | daily | `rm session/zerodha_token.json` | force fresh login |
| 08:15 | Mon-Fri | `auto_refresh_token.py` | Headless TOTP token refresh (FIX-187; no manual OTP) |
| 08:30 / 09:14 / 09:15 | Mon-Fri | `scripts.preflight.orchestrator --phase A/B/C` | Pre-flight readiness, 3 phases (replaced premarket_healthcheck) |
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
| 18:50 | Mon-Fri | `cron_officer.py --eod-summary` | Cron Officer EOD report (was 18:30; floor 18:45 +5min so it captures system_manager_eod; live since 21-Jun reinstall) |
| 18:45 | Mon-Fri | `system_manager.py` | System Manager EOD deep cross-check (TASK #5) |
| 03:00 | 1st of month | `backup_restore_drill.py --quiet` | restore drill |
| hourly | every | `disk_monitor.py` | disk space |

> ✅ **Self-maintaining (23-Jun framework):** live `crontab -l` == canonical == `generate(registry)`
> (four-way sha256 `1469f905…`, 41 command-lines). `scripts/generate_crontab.py` is the deterministic
> generator; `scripts/check_cron_drift.py` (18:00) is the bidirectional content-pass drift check; the
> **post-receive** hook auto-installs on every push (the **pre-receive** equality guard is DEFERRED).
> Crontab backups at `~/tools/claude/crontab.backup.*` (+ legacy `data_store/crontab_backups/`).

## Systemd Services  (`/etc/systemd/system/`)
| Service | ExecStart | Purpose | Notes |
|---|---|---|---|
| `trading-system.service` | `venv/bin/python main.py --mode live` | Main app (live mode) | `Restart=on-failure`, `RestartSec=10`, **`RestartPreventExitStatus=3 4`** (4=HALT/SOFT_KILL no-restart, added 18-Jun); `EnvironmentFile=.env` + drop-in (Telegram secrets). **FIX-189 (19-Jun): main() exits 0 outside the broad service window [08:00–16:00 IST]** (startup guard; bypass: `--status`/`--dry-run`/`--interactive`/`--resume` or `TS_IGNORE_MARKET_WINDOW=1`) **and an `eod-self-exit` thread exits 0 once past 16:00 IST AND flat** (`count_active_positions()==0`) so it never idles overnight; never exits while a position is open. |
| `token-watcher.service` | `bash deploy/token_watcher.sh` | Auto-start app on fresh token | active |
| `alert-watcher.service` | `python scripts/alert_watcher.py` | Consume CRITICAL sentinel flags (email digest) | **enabled, delivering (18-Jun)**. NB: `Restart=always`+`RestartSec=10` and the script runs one pass then exits 0 → **periodic-oneshot**: `auto-restart`/rising `NRestarts` is NORMAL (a check every ~10s), NOT a crash-loop. |
| `trading-watchman.service` | (gemini watchman) | AI log monitor during market hours | `Wants=` by trading-system |
| `security-watcher.service` | `python scripts/security_monitor.py --watch` | **VM Security Manager Phase 1+2** — auth.log + file-integrity monitor (9 checks incl. Phase-2 copy-switch + copy-bypass), [LFL836] alerts | **enabled+active (19-Jun)**. `Type=simple`+`Restart=always`+`RestartSec=60` → periodic (~60s); model = alert-watcher. Reads `/var/log/auth.log` (ubuntu ∈ `adm`). Config `config/security.yaml` (standalone — NOT system_config.yaml, which is `extra="forbid"`). State `data_store/security_state.json`. Alert-ONLY (never blocks). |
| `cron-watchdog.timer`→`.service` | `venv/bin/python scripts/cron_watchdog.py` (oneshot) | **Tier-2 watch-the-watcher (ARMED 23-Jun)** — asserts `cron_officer_eod` + `check_cron_drift` both heartbeated today, else a CRITICAL sentinel via the **cron-INDEPENDENT** path (alert-watcher emails) | systemd (NOT cron) so it can't fail the way a dead crond / broken shared-env cron would. Fires **19:30 IST daily** (`Persistent=true`); first run **Wed 24-Jun 19:30**. `enabled`+`active`. From `deploy/systemd/cron-watchdog.{service,timer}`. |

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
> only**. **HARD-BLOCK ACTIVE on the VM (20-Jun 14:00):** `install_copy_protection.sh` symlinked
> `/usr/local/bin/{scp,sftp,rsync}` → `copy-guard` and `/usr/local/bin/request-copy`. `/usr/local/bin`
> precedes `/usr/bin` in PATH, so a VM-initiated `scp`/`sftp`/`rsync` hits the gate: **blocked without a
> token, allowed with one** (verified live: no-token → DENIED exit 1 real-scp-never-ran; `request-copy`
> → token → copy succeeds). git push (ssh) + interactive ssh are NOT wrapped (verified intact). **New
> workflow to copy VM→PC:** `request-copy '<reason>'` (15-min token), then `scp`/`rsync`; blocked
> 18:00–08:00 (hard time-lock, no override) / without a token / when >2 SSH sessions. **Rollback:**
> `bash deploy/security/install_copy_protection.sh --uninstall` (removes the symlinks; real `/usr/bin`
> binaries untouched).

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

---

## Circuit-band placeability gate (NOCIL fix, 23-Jun-2026)
**Invariant:** a resting protective exit (SL stop / TGT limit) recalc'd from the fill must land on
the **correct side of the entry fill** *and* inside the day's circuit band — never a wrong-side,
instantly-marketable order. (NOCIL 22-Jun: a LONG TGT recalc'd to 197.12 was clamped DOWN to
`upper×0.98 = 187.00`, **below** the 189.78 fill → instant SELL, 2.35 s scratch mislabeled TGT_HIT.)

- **Primitive (single chokepoint):** `orders/price_math.py` → `clamp_exit_into_band(price, *, leg,
  direction, entry_fill, upper_circuit, lower_circuit, …) → ClampResult(price, was_clamped,
  placeable, reason)`. Frozen result; pure (no I/O). Replaces the old side-agnostic
  `clamp_to_circuit_band`. Margin = `DEFAULT_CIRCUIT_MARGIN_PCT` (0.02) — the **single source** shared
  with the pre-fill rule. Called from **exactly 3 sites, all in `orders/order_protocol_limit.py`**
  (SL + TGT in `place_exits`, TGT in `place_tgt_only`); a no-bypass test enforces this.
- **Leg asymmetry (caller branches on `placeable=False`):** **TGT** unplaceable → hold (SL-only;
  `tgt_placed=False` → FIX-190 Bug-C → `mark_needs_tgt_retry` → TGTRetryManager). **SL** unplaceable →
  position cannot be stopped → `raise SLUnplaceableError` (`core/exceptions.py`) → order_placer's
  existing unprotected-position escalation (`_emergency_market_exit` + `_fire_hard_kill_for_unprotected_position`).
  `entry_fill` (= settled `avg_fill_price`) is threaded fill→`place_deferred_exits`→`place_exits`.
- **Pre-fill reject (framing-b, both legs):** `screening/secondary_screener.py` rejects an entry that
  sits at/beyond the exit-clamp ceiling (`entry ≥ upper×(1−m)` LONG / `≤ lower×(1+m)` SHORT, and the
  symmetric SL-side) → `REJECTED_CIRCUIT_PROXIMITY`. Fast-disable lever:
  `entry_gate.circuit_proximity_reject_enabled` (default **true**). The post-fill gate is **not** flagged.
- **Documented un-gated exceptions (never clamp → no wrong-side risk; test-enforced):** CO-TGT
  (`order_protocol_co.py` — broker-managed CO SL backstops it) and the G5b recovery SL
  (`order_reconciler.py` — LTP-guarded). Both fail loud (broker reject), never a silent wrong-side fill.
- **Parity:** all shared entry/exit code, no mode branch → Paper + Live together. **No schema change.**

## Changelog
- 2026-06-25 — Claude Code (Opus) — **SLICE2.5-P2: durable CNC overnight-GTT state + reconcile + GTT_EXIT + 15-min monitor (Phase 2 of the Delivery arc; schema v36; delivery_enabled=false — durability + safety only, no activation).**
  P1 kept the one-GTT-per-trade map IN MEMORY (lost on restart) and had no reconcile / leak monitor. P2 makes it durable + self-healing. **STEP 0 — schema v36:** new **TABLE 37 `gtt_state`** (gtt_id INTEGER PK = broker trigger id; trade_id FK→trades; exit_side/qty/sl_trigger/sl_limit/tgt_trigger/tgt_limit; status `ACTIVE|TRIGGERED|CANCELLED|EXPIRED|REJECTED|CLEANED` CHECK; `needs_review` Y2 latch; last_verified_at; 2 indexes) — PURE ADDITION (CREATE IF NOT EXISTS, no MIGRATION_TABLES rebuild, same path as v31/v33); `EXPECTED_SCHEMA_VERSION 35→36`. **Verified on a `.backup` of the live 161 MB DB: v35→v36, 0 tables rebuilt, all rowcounts intact, FK/integrity/WAL clean.** Y6: a trade may accrue MANY rows over its life (each recreate = new gtt_id = new row = history); the M2 one-GTT invariant is on `status='ACTIVE'`. **STEP 1 (adapter wiring):** `broker/zerodha_adapter` `get_gtt`/`get_gtts`/`delete_gtt`/`get_holdings` (live=Kite, paper=in-memory `_paper_gtts`/`_paper_holdings` + `seed_paper_holding`; paper gtt ids now **NUMERIC** `_PAPER_GTT_ID_BASE` since gtt_id is INTEGER PK). **STEP 1.5 (R2 guard split):** `delivery_enabled` gates ONLY the CNC **entry** (`place_order`); GTT ops are NOT gated — protection survives disablement (with delivery off there are no new entries, so a GTT op only ever touches a pre-existing holding). **STEP 2 (persist):** `CncGttPlacer` gains a store — `place_for_fill` writes/updates the `gtt_state` row (best-effort; broker GTT is the authority), `hydrate_from_store()` rebuilds the hot cache on boot, `forget()` for recreate; `core/state_store` gtt_state DAO. **STEP 3+3.5 — new `orders/cnc_gtt_monitor.py` `CncGttMonitor.reconcile()`:** per ACTIVE row gather (get_gtts + holdings + same-day CNC positions) → M1 (qty) + M2 (one ACTIVE) → **K6 ladder** [healthy→touch / **GTT_EXIT** (triggered+flat → finalise CLOSED + release delivery-bucket capital + publish PositionClosed SYSTEM-OWNED + CLEANED, idempotent via the OPEN→CLOSED gate so a double-observe never double-releases) / **F6** (triggered+still-holding → CRITICAL re-protect remaining) / **recreate** (missing+qty-match in-hours, Y3 fresh LTP) / **Y1 pre-open queue** (missing pre-open → queued, drained on the first in-hours cycle before the gather) / **qty-mismatch** (CRITICAL ONCE via Y2 needs_review + cancel wrong-qty GTT + NO recreate + NO soft-kill + intraday unaffected) / orphan-active-flat (delete+finalise) / **>1 ACTIVE → SOFT-KILL**] + **Y4** broker-gather failure → defer+alert (never crash / never treat no-data as flat). **STEP 4 (wiring):** `order_reconciler` startup [4a] + 15-min in-hours cadence [4b] (first-in-hours fires immediately for the Y1 drain) — reuses the daemon, no new scheduler; **delivery trades (ACTIVE gtt_state) EXCLUDED from CHECK1/3/4/5 + CHECK2 + CHECK9/G5b/duplicate** (a carried CNC holding lives in holdings() not positions() → CHECK1 would wrongly CLOSED_MANUAL it and G5b would place a spurious SL). **STEP 5 (leak/cap/exempt 8b):** orphan sweep = forensic-log FIRST then delete a LEAKED system GTT (still ACTIVE at broker, row non-ACTIVE); human/external GTT (no row) NEVER deleted (FIX-182); 50-cap WARNING/CRITICAL; confirmed+tested `sweep_stale_orders` touches ONLY the `orders` table — a `gtt_state` GTT is a live protective leg exempt from every cancel-sweep (delete_gtt is the sole remover). **STEP 6 (alerting):** `CncGttMonitor` alerts via `telegram_notifier.send(... source_module="cnc_gtt_monitor")`, WARNING (info/50-cap) vs CRITICAL (qty-mismatch/soft-kill/F6/re-protect-fail; CRITICAL writes a sentinel → email fallback), Y2 de-dup. **Parity:** the only live/paper difference is the broker I/O boundary; the reconcile + GTT_EXIT + ladder run end-to-end in paper (injected-state tests). **Tests:** `test_cnc_gtt_slice25_p2.py` (5) + `test_cnc_gtt_monitor.py` (13) + `test_cnc_gtt_step4_wiring.py` (4) + P1 asserts updated for numeric gids. Full PC unit suite **3793 passed / 32 pre-existing env-fails (test_main waitress + TOTP/NTP/interactive — green on VM) / 0 regressions**. Branch `slice25-p2-gtt-durability-25jun` ff-merged onto main → **DEPLOYED to main `bad0aad` 25-Jun ~22:23 IST** (one-time authorized push+restart; standing Rama-owns rule RESTORED after). Push → post-receive clean + crontab auto-installed (no hook errors); the 22:23 restart **exited 0/SUCCESS on the market-window guard** (post-close, by design — 813ms, before StateStore init), so **schema v36 applies at the Fri 08:15 in-window boot** (live DB still v35 tonight; `.backup` proved v35→v36 safe + deployed schema.sql/`EXPECTED_SCHEMA_VERSION` verified =36). Broker-session-free verify **15/15 PASS** (delivery_enabled=false, gtt_sl_off=0.03, Config Auditor 0 BLOCKs, all P2 modules import). **delivery_enabled stays false** — go-live gates before true remain: T2 · FIX-183 · C1-watch. See memory `slice25_p2_gtt_durability_25jun`.
- 2026-06-25 — Claude Code (Opus) — **DEPLOYED to main (one-time authorized push+restart): BUILD 2 + RAMCOIND fix + SLICE2.5-P1 all live.** `main` ff-merged `c55d5ff..598f4dc` (BUILD 2 `eb373c4` → RAMCOIND `6e8ca6a`/`25bcc95` → P1 `598f4dc`); pushed → post-receive deployed cleanly (crontab canonical, no hook errors). The 16:40 restart **exited 0/SUCCESS** = main.py's market-window guard (post-16:00, by design — NOT a crash). Verified independently (probe, broker-session-free): new config loads (`delivery_enabled=false`, `gtt_sl_limit_offset_pct=0.03`), **Config Sanity Auditor runs + passes (0 blocks)**, new modules import (`cnc_gtt`/`config_auditor`/preflight `config_sanity`; adapter `place_gtt`). System flat (0 open trades). Full live boot = the normal Fri 08:15 token-watcher auto-start. `delivery_enabled=false` → nothing trades CNC. One-time override; future pushes/restarts revert to Rama-owned.
- 2026-06-25 — Claude Code (Opus) — **SLICE2.5-P1: GTT-backed CNC overnight protection (Phase 1 of the Delivery arc; no DB schema; delivery_enabled=false — nothing trades CNC).**
  The CNC entry path was built but dormant (force_intraday_only rewrites DELIVERY→INTRADAY); a CNC position's day-validity LIMIT_TRIPLE SL/TGT legs expire at EOD → naked overnight, and no GTT existed (audit G1/G2). P1 replaces those day legs (CNC ONLY) with ONE broker-side two-leg **OCO GTT** placed right after the entry fill, so protection runs entry→exit and survives a VM outage. **Build (all tagged SLICE2.5-P1):** `system.delivery_enabled` (bool, default FALSE — master capability lock) + `capital.gtt_sl_limit_offset_pct` (0.03 DEEP protective SL floor, distinct from the 0.5% intraday offset); `orders/price_math.calc_gtt_limit_price` (dedicated GTT leg-limit helper — does NOT overload the intraday path); `broker/zerodha_adapter` `place_gtt`/`modify_gtt` (live → `kite.place_gtt`/`modify_gtt` OCO two-leg SELL CNC LIMIT; **paper → mock `PAPER_GTT_*` id + identical recorded params** = parity) + the **master lock at the broker boundary** (`place_order`/`place_gtt` refuse CNC when delivery_enabled=false; MIS/CO unaffected); `orders/cnc_gtt.CncGttPlacer` (tick-valid OCO legs reusing the EXACT sl_price + actual_tgt_price FIX-013; SL limit = trigger−3%, TGT limit = trigger−small; C8 straddle/min-distance validation; **ONE GTT per trade** via an in-memory map + `modify_gtt` on a later partial fill); ONE **centralized gate** in `full_entry_engine.place_deferred_exits` (`intent=="DELIVERY"` → `CncGttPlacer` → `ExitLegsResult(is_gtt=True, gtt_id)`; all 3 order_placer call sites inherit it) + `order_placer._finalize_cnc_gtt` (logs gtt_id + records a verified exit; NO day-leg persist / `_fill_map` OCO / day-leg after-check). **INTRADAY/MIS path byte-for-byte unchanged.** main.py wires it + logs `delivery_lock.status` at boot. **No DB schema** (gtt_id LOGGED only — durable carry-state persistence + a GTT leak/health/CA monitor are Phase 2). **Parity:** the only live/paper difference is the broker boundary. Tests: `tests/unit/test_cnc_gtt_slice25_p1.py` (T1, 9 paper). **T2** `scripts/t2_cnc_gtt_realtest.py` — the MARKET-HOURS real-API blocker (real CNC buy + real OCO GTT + real CNC sell with NO CDSL TPIN; + an `--arm/--close-overnight` variant); **delivery_enabled=true is considered ONLY after T2 passes.** Branch `slice25-p1-cnc-gtt-25jun` (stacked on BUILD2+RAMCOIND; pushes as its own change after they land). See memory `slice25_p1_cnc_gtt_25jun`.
- 2026-06-25 — Claude Code (Opus) — **RAMCOIND duplicate-exit fix — 4-layer permanent + parity fix for the G5b duplicate-SL race (highest-criticality exit/reconciler path; no schema).**
  Root cause (25-Jun RAMCOIND −1 over-sell): `orders/order_reconciler.py` `_g5b_crash_recovery_sl` placed a 2nd SL via a direct `adapter.place_order` (RC18, OUTSIDE the LIMIT_TRIPLE software OCO), guarded by the LOCAL `orders` table which `order_monitor.track` populates ~40ms AFTER the broker placement — a TOCTOU race (guard checked 12ms before the row landed). Both SLs shared the trigger, so on the stop-hit BOTH filled → +1 closed the long and the duplicate over-sold into a −1 naked short, which CHECK2 then disowned as a "human order". Recurring (IRFC 17-Jun, NIACL 22-Jun, RAMCOIND 25-Jun — all LONG only because LONGs are 93% of fills); harmful only on an SL-exit (RAMCOIND was the first). Exit-path inventory confirmed G5b is the UNIQUE out-of-OCO duplicate source. **4 layers (all shared paper+live, no mode branch):** **LAYER 2 (keystone)** new reconciler invariant `_check_duplicate_exits` every cycle — EXACTLY ONE live SL per OPEN/PARTIAL LIMIT_TRIPLE trade (CO excluded — its SL is in the broker CO bracket; detected via the `variety='co'` entry) + ONE live TGT (both protocols); >1 → cancel the non-canonical extra, KEEP the earliest-placed (`get_orders_for_trade` ORDER BY placed_at = the OCO/trailed leg), never drop to zero (re-verify + re-place via G5b if the cancel races a fill) → `DUPLICATE_SL`/`DUPLICATE_TGT` reconciliation_log + alert. Would alone have cancelled the RAMCOIND dup ~12 min before its 10:12:49 stop-hit. **LAYER 1** `_g5b_crash_recovery_sl` now has, before placing: a **10s settling window** (skip recovery for a trade whose persisted `entry_time` is < 10s ago — exits still being placed; mode-agnostic, deterministic) + an **authoritative check** `_already_has_live_sl` (broker order book via the existing `_broker_orders_fn` — an SL = exit-side order with `trigger_price>0`; `_fill_map` fallback when unwired) — race-proof, replaces trusting the lagging table. **LAYER 3** `_check2_orphan_adoption` recognises a SYSTEM over-sell (a recently-closed trade on the symbol, OPPOSITE side, ≈exit price, |qty|≤traded) → CRITICAL + AUTO-FLATTEN the residual (covering MARKET, once/day-guarded); a NAKED untracked position (no protective stop at the broker) → WARNING (never the old silent INFO); a protected human position stays silent (FIX-182 unchanged). **LAYER 4** `order_placer._verify_exits_placed` flags `>1` live SL/TGT (`DUPLICATE_SL/TGT`, the cheap same-path placement-time guard). **No DB schema** (existing `orders`/`reconciliation_log`; SYSTEM_OVERSELL is an additive event). **Direction-symmetric** — every layer is direction-agnostic (G5b places a BUY-stop SL for shorts the same way; L2 filters on `leg` not side; L3 mirrors short over-buy → +qty residual → SELL cover); proven by SHORT-side mirror tests. Tests: `tests/unit/test_ramcoind_dup_exit_fix.py` (24, incl. SHORT L1/L2/L3 mirrors) + acceptance gate `tests/crash_test/test_ramcoind_oversell_prevented.py` (6, paper+live: LONG over-sell + SHORT over-buy both end FLAT, never ±1). NOTE: LONG underperformance is NOT this bug (42/42 LONG exits placed correct distinct SL/TGT — separate parked review). Branch `ramcoind-duplicate-exit-fix-25jun`. **Deploy post-15:30 close (exit/reconciler path — off-market only); activates next restart.** See memory `ramcoind_duplicate_sl_incident_25jun`.
- 2026-06-25 — Claude Code (Opus) — **BUILD 2: Config Sanity Auditor — the enforcement capstone of the Config Authority work (no schema; validate+alert only).**
  ONE rule engine `core/config_auditor.py` (`audit()` → `ConfigAuditReport` of per-group `AuditFinding`s, each PASS/INFO/WARN/BLOCK), TWO callers (single source of truth — no rule lives twice). **Check groups:** **(A) Contradictions** — the BUILD 1 #10 `force_intraday_only`+`trade_type=DELIVERY` is **re-homed here** (BLOCK, message still carries "CONTRADICTORY CONFIG") + A2 trade_type-domain (BLOCK, belt-and-suspenders) + A3 "0 strategies would trade" (WARN, strategy-aware via `strategy_will_trade`). **(B) Single-source regression guards** (WARN) — flags a deleted key reappearing in raw YAML (`capital.daily_loss_limit`, `position_sizing.max_position_value_rs`, `risk.live_test_*`, scoring `tier_multipliers`); reads raw YAML because `extra="forbid"` already strips them from the model. **(C) Capital-relative sanity** (WARN) — pct ranges + the ladder `max_concentration_pct < max_position_value_pct` (the bug-guard must be LOOSER than routine concentration, else it binds first) + cumulative-risk-vs-daily-loss (the migrated BUILD 1 cross-check). **(D) Active-override listing** (INFO/WARN) — T3 visibility of every non-empty slippage override (reuses `orders.order_placer.validate_slippage_overrides` for typo/extreme WARNs). **(E) Launch-phase reminders** (INFO) — surfaces `[LAUNCH-PHASE]`-tagged params (`entry_start` 10:00) with a review note. **(F) Stale-default guard** (WARN) — introspects component ctor defaults (`PositionSizer.max_position_value_pct`=0.40, `FundManager.daily_loss_limit_pct`=0.03) vs config intent (BUILD 1 aligned them; catches a future YAML-vs-code drift). **(G) Cross-field sanity** (WARN) — the migrated entry-window/leverage/min-tick checks + per-strategy-window-in-envelope. **STARTUP** (`core/config_loader.py` `SystemConfig._cross_field_sanity_checks`) now delegates to the auditor (`audit_system_config`, groups `ACG`), logs WARNs and **fails fast on BLOCK** (unchanged boot behaviour + boot-log; B/D/E/F need pre-flight context). **PRE-FLIGHT** new group `scripts/preflight/checks/config_sanity.py` (7 rows A–G, group "Config Sanity") in **Phase A 08:30** → the consolidated **09:20 email + Telegram**; ONE auditor run memoised on the shared `CheckContext` feeds all 7 rows. **Parity:** the auditor is pure with no mode branch → paper + live identical (test-enforced). **No DB schema.** Tests: `tests/unit/test_config_auditor.py` (37: every group BLOCK/WARN/PASS, startup gate, pre-flight integration, parity). Full PC unit suite **3770 passed / 0 failed**. Sample 09:20 section: `Config Sanity (7/7 ok)` — A–F ✅, G ⚠️ (entry_end within 15min of squareoff, the lone pre-existing warn). Branch `build2-config-sanity-auditor-25jun`. **Deploy post-15:30 close; activates Fri 08:15 restart.** Completes the Config Authority arc (audit → authority map → framework → resolution → BUILD 1 cleanup → BUILD 2 enforcement). See memory `build2_config_sanity_auditor_25jun`.
- 2026-06-24 — Claude Code (Opus) — **BUILD 1: config authority fixes — single daily-loss source + capital-relative position cap + dead-config cleanup (12 audited conflicts; trading-path; no schema).**
  Resolves the 12 config conflicts from the Phase 1+2 audit (all LOCKED by Rama). **#1 SINGLE DAILY-LOSS SOURCE:** deleted the absolute `capital.daily_loss_limit` (was ₹300) — `risk.daily_loss_limit_pct` (3%) is now the SOLE authority. The FundManager post-close **realized** breach (`capital/fund_manager.py` `release_used`, `_daily_loss_limit_pct`) derives ₹ = `daily_loss_limit_pct × current capital` (`self._total`), the SAME pct the pre-trade RiskEngine MTM gate uses (`main.py:1828` feeds `app_config.system.risk.daily_loss_limit_pct` to BOTH). Basis split preserved (pre-trade = realized+unrealized MTM; post-close = realized). Scales: ₹10k→₹300, ₹1L→₹3000. **#2 CAPITAL-RELATIVE POSITION CAP:** `position_sizing.max_position_value_rs` (₹2500) → `max_position_value_pct` (0.40). `PositionSizer.calculate` computes `cap = 0.40 × total_capital` (the snapshot it already fetches) and keeps the **REJECT** (`POSITION_VALUE_CAP`, catastrophic-loss/bug-guard, NOT a clamp). Removes the ₹25k scaling cliff (a ₹30k position at ₹1L now passes; routine sizing still bound by concentration 10% + risk 1%). ₹10k→₹4000, ₹1L→₹40000. **#10 STARTUP-BLOCKING GUARD:** `force_intraday_only=true` + `trade_type=DELIVERY` (rewrites all→INTRADAY then gates out INTRADAY = 0 strategies trade) now raises a `ValidationError` in `SystemConfig._cross_field_sanity_checks` → **refuses to boot** (fail fast). **#3/#4/#11 DEAD CONFIG DELETED:** `live_test_mode`/`live_test_max_*` (were == base caps 5/10 → the `main.py` swap was a no-op; base caps now sole authority both modes) + `scoring_weights.yaml` `tier_multipliers` (sizer reads `system_config` 0.70, never this 0.75) + per-strategy `max_risk_pct` (dead in live; `replay_signals` falls back to 0.01). **#A.4 ALIGNED STALE DEFAULTS:** FundManager 10000→0.03, PositionSizer 50000→0.40, RiskEngine docstring 10/20/4/0.05→5/10/5/0.03. **#12 BOOT INVARIANT:** comment + `assert _startup_reconcile_done` so `reconcile_once()` provably precedes `signal_processor.start()`/webhook (phantom capital corrected before any trade; proven no-trade-in-window). **#5/#6/#7/#8 DOC-ONLY** + a PERMANENT-vs-LAUNCH-PHASE tagging convention in `system_config.yaml` (feeds BUILD 2). **AUDITORS (beyond the 12, forced by the deletions):** `scripts/system_manager.py` EOD + `scripts/preflight/checks/config_integrity.py` recompute the pct→₹ thresholds from a new `StateStore.get_day_opening_capital()` (fm_ledger INIT row, **OPENING** basis, `accounts.csv` paper_capital **bootstrap fallback**, no broker call → parity-safe); preflight `LiveTestModeCapsCheck`→`CapsConfigDriftCheck` (guards base caps). **No DB schema** (config + Pydantic field changes only). **Parity:** all on shared paper+live paths. Tests: full suite green except 1 pre-existing time-of-day market-window artifact (`test_interactive_startup`, exits 0 outside 08:00–16:00 IST — confirmed pre-existing on clean HEAD); added #10 + repurposed-preflight + auditor-capital-basis tests. Branch `build1-config-authority-fixes-24jun`. Activates next restart (08:15). BUILD 2 (Config Sanity Auditor) follows. See memory `build1_config_authority_fixes_24jun`.
- 2026-06-24 — Claude Code (Opus) — **Hygiene pack: date-coupled test fixed + 9 merged branches pruned (deployed post-15:30 close).**
  **(1) Date-coupled test FIXED:** `test_system_manager.py::test_report_integrity_missing_and_present` passed ONLY on 19-Jun — it wrote `system_2026-06-19.log` (mtime = run day) but called `report_integrity_check(day="2026-06-19")`, whose intentional anti-staleness guard warns when a file's mtime-day ≠ `day`; on any other day → "stale" not ✅. **Test bug, not a function bug** — fixed the test to derive `date.today()` (the function is correct, untouched). This was the lone remaining pytest failure; suite now fully green. **(2) tzdata PC-env failures:** ALREADY FINE (resolved by the 22-Jun tzdata install; `test_main`/`test_fix129`/`test_fix135` 122/122 green) — no action. **(3) Branch prune:** deleted the **9 fully-merged** session branches (local + origin) — `fix-fno-ban-endpoint-22jun`, `aftercheck-circuit-cap-fix-24jun`, `cron-officer-email-leak-fix-24jun`, `governor-severity-and-briefing-debounce-24jun`, `part-c-rr-1.5-all-strategies-24jun`, `preflight-activation`, `slice1-rr-fix-aftercheck-22jun`, `slice2-strategy-control-24jun`, `tgt-retry-postmortem-24jun` (each re-verified via `git branch --merged main` + `git branch -d` safety); kept unmerged `preflight-check-21jun`. **(4) CT crash-tests CT114/127/130/132/133/135:** PARKED, NOT run — destructive operator-run scenarios (`scenario_runner.py`, `shell=True` on the real repo: CT114 `fallocate`s the disk, CT132 `rm`s a config, **CT133 `sudo systemctl stop trading-system`**, CT135 cleanup-mid-trading); they are NOT pytest tests and don't gate the suite. Off-hours sandbox-VM drill only — never on the live box. **No restart** (test fix + branch prune). Deployed after the 15:30 IST close per Rama's gate (live trades protected). Branch `hygiene-pack-24jun`. See memory `hygiene_pack_24jun`. Lesson (`tasks/lessons.md`): don't date-couple tests — derive/inject the date.
- 2026-06-24 — Claude Code (Opus) — **Slice 2: 3-layer strategy control + status table (trading-path entry gate; no schema).**
  Adds a config-driven strategy control system above the entry path. **LAYER 1** `system_config.trade_type` (INTRADAY|DELIVERY|BOTH, default INTRADAY; `core/config_loader.py` SystemConfig + validator). **LAYER 3** `strategy.enabled` (`strategies/schema.py` `enabled: bool = True`; added `enabled: true` to all 15 YAMLs). **LAYER 0** `force_intraday_only` unchanged (load-time intent→INTRADAY rewrite). **Single resolver** `strategies/control.py::strategy_will_trade(strategy, *, trade_type, force_intraday_only) -> Verdict` is the SOLE authority — drives BOTH the entry gate AND the status table so they can't disagree. **Gate (trading-path, 2 sites):** `signals/signal_processor.py` `_process_one` (~:614) + `continue_from_gate` (~:1305), after strategy resolution / before the per-strategy window check; `will_trade=False → _PipelineReject("STRATEGY_CONTROL")` BEFORE sizing. SignalProcessor gained `trade_type`+`force_intraday_only` ctor args (wired from `app_config.system` in `main.py`, which also logs a one-line `strategy_control.summary` at boot). **Product wiring UNCHANGED — the gate only REJECTS; survivors place via the existing intent→product_resolver path** → INVARIANT: default (trade_type INTRADAY + force_intraday_only true) places MIS for every will-trade strategy, never CNC. **DECISION (Rama):** ship **ALL 15 enabled:true** = TRUE zero behaviour change — found the 3 `positional_*` "delivery" strategies are trading LIVE as intraday today (force rewrites them; `positional_sector_rotation` was 24-Jun's most active, +₹8.93), so disabling them (the brief's original plan) would STOP live strategies, not be zero-change; the switch exists for future use; real delivery (CNC) deferred to Slice 2.5. **Default = 15 WILL TRADE / 0 WON'T TRADE.** **Status table** (`scripts/strategy_status.py`): full 15-row Gmail-safe HTML table (pills) + compact Telegram (names in backtick code-spans → MarkdownV2-safe) folded into the Cron Officer 09:20 briefing (new `CronReport.strategy_status`); **Type column = TRUE declared intent** (DELIVERY for positional_*, from raw `validate_strategy` pre-rewrite), **Verdict = effective post-rewrite intent (==gate)**; footnote flags delivery-as-intraday; malformed YAML → CONFIG ERROR row. **No DB schema** (config + code + display only). Tests: `tests/unit/test_slice2_strategy_control.py` (28: truth table, gate both sites, status table verdict==gate, schema, INVARIANT, regression, CNC-paper) + fixed a **Part-C regression** (`test_positional_tgt_not_equal_entry_price` hardcoded R:R 2 → now RR-aware; Part-C skipped the full suite + missed it). Branch `slice2-strategy-control-24jun`. Activates next restart. See memory `slice2_strategy_control_24jun`.
- 2026-06-24 — Claude Code (Opus) — **Part C: all 15 strategies standardized to R:R 1.5 (config-only).**
  Rama's standardization — `tgt_risk_reward: 1.5` in every `config/strategies/*.yaml`. Before: `gap_fade_long`/`gap_fade_short` already 1.5, `gap_go_long`/`gap_go_short` 2.5→1.5, the other 11 2.0→1.5 (**13 files changed, 1 line each**; `tgt_method` stays `"RISK_REWARD"` in all 15). **No schema, no code** — the R:R MECHANISM was fixed + proven in Slice 1 (22-Jun): `tgt_risk_reward` is frozen at placement into `trades.tgt_risk_reward_applied` and re-read at fill, so the broker TGT uses each strategy's own R:R (NULL→2.0 fallback won't fire now). Verified: grep (15×1.5), `StrategyLoader.load_all_strategies` loads all 15 cleanly (pydantic `>0` validator passes, each parsed `tgt_risk_reward=1.5`), 176 real-config-loading tests green (all `2.0`/`2.5` test refs are self-contained fixtures, not real-config assertions). Takes effect on the **next restart** (running app holds old YAMLs until 08:15 tomorrow). To retune one later: edit its `tgt_risk_reward` → commit → push → restart (now reaches the broker). `docs/CONFIG_GUIDE.md` §11 noted; `.docx` manual needs a manual re-SCP. Branch `part-c-rr-1.5-all-strategies-24jun`. See memory `part_c_rr_standardized_1.5_24jun`.
- 2026-06-24 — Claude Code (Opus) — **Two alerting fixes: strategy_governor lost-alert (missing severity) + 09:20 briefing false-CRITICAL debounce.**
  **FIX 1 (`capital/strategy_governor.py:149`):** `_pause_strategy` called `notifier.send(title=, body=, source_module=)` with NO `severity` (a REQUIRED keyword-only arg) → every strategy-circuit-breaker trip raised `TypeError: ...missing ... 'severity'`, swallowed by the surrounding try/except → the "STRATEGY PAUSED" alert was **silently lost** (today's only 2 TypeErrors; the Issue-3 side-finding). It was the **only** `.send(` caller missing severity (grepped all sites). Fixed `severity="WARNING"` (convention: `kill_switch` HALT=CRITICAL; a single-strategy daily-loss pause = degraded-but-operating=WARNING, like `breakeven_manager`). Never surfaced earlier: the trip path was rarely hit live + the existing test used a bare `MagicMock` notifier (accepts any args → hid the bug). Tests: `_StrictNotifier` (enforces the real keyword-only signature) + regression `test_pause_alert_dispatches_with_required_severity` (empty `calls` on old code) + severity assert on the MagicMock test. **FIX 2 (`scripts/cron_officer.py` + `core/cron_registry.py`):** the 09:20 Cron Officer briefing computed a daily **false CRITICAL** from a heartbeat-commit race — `now_time = officer.morning_briefing_time` is a FIXED 09:20 (not wall-clock), and `_classify_job` marked a heartbeat_db job MISSED the instant `now_time >= due_time` with no row, so a job firing at the 09:20 cron cluster (e.g. `capture_metrics` */5) whose heartbeat hadn't committed → transient MISSED → CRITICAL (recompute = INFO). **Chose debounce over stagger** (root-cause + timing-agnostic + no self-maintaining-crontab churn): added `OfficerConfig.miss_grace_minutes` (default 2, also in the yaml `officer:` block) + a `grace_minutes` param on `_classify_job` — a heartbeat job due within `grace` minutes of the snapshot with no row → **PENDING** (just fired), not MISSED; `build_report` passes it. A genuine miss (due > grace ago) still → MISSED → CRITICAL (test-proven). **The `officer:`-block change does NOT alter the generated crontab** (generation reads `jobs:` only; verified byte-identical → 4-way sha intact, post-receive auto-install stays a no-op). Applies to the EOD report too (harmless — EOD already fires +5 min after the last job). Tests +6 in `test_cron_officer.py`. **No schema; alerting-only — FIX 1 activates next restart, FIX 2 next 09:20 cron.** Branch `governor-severity-and-briefing-debounce-24jun`. See memory `governor_severity_briefing_debounce_24jun`.
- 2026-06-24 — Claude Code (Opus) — **SL/TGT after-check is now circuit-clamp-aware (stops false-flagging a legitimately band-clamped exit).**
  Slice-1's after-check (`order_placer._verify_exits_placed`) flagged PACEDIGITK (Tue 23-Jun) `exits_verified=0` "TGT 216.15 != intended 220.41" — the **only `exits_verified=0` trade in the whole DB**. Mechanism: the fill-recalc TGT (220.41) sat above PACEDIGITK's upper circuit, so `place_exits`→`clamp_exit_into_band` legitimately clamped the placed TGT down to the band ceiling 216.15 and placed it there; the after-check then compared placed (216.15, clamped) vs intended (220.41, pre-clamp) → false mismatch (`_exit_price_mismatch` was even designed to catch clamps). **Fix (root cause, no schema):** the clamp ceiling was already in memory — `ExitLegsResult.tgt_price`/`.sl_trigger_price` hold the clamped price — so added `sl_clamped`/`tgt_clamped` bools to `ExitLegsResult` (defaults False, set in `LimitTripleProtocol.place_exits` from `sl_res`/`tgt_res.was_clamped`) and new `sl_clamp_price`/`tgt_clamp_price` params on `_verify_exits_placed`. New `_explained_by_clamp(placed, clamp_price)` = clamp recorded AND `placed ≈ band ceiling` (same tick tolerance): a price differing from intended is excused as a NOTE ("TGT/SL clamped to circuit band X (intended Y)") with `exits_verified=1` and **no alert** ONLY when fully explained by the band; a placed price matching NEITHER intended NOR band (or no clamp) still flags **CRITICAL(SL)/WARN(TGT)** — discriminator, NOT blind suppression (the band reference is the clamp's computed output, independent of the read-back order row). **Covers SL + TGT** (a LONG's SL can be clamped UP off the lower circuit → also was false-flaggable). Wired at both LIMIT_TRIPLE after-check sites; CO path unchanged (CO-TGT never clamps, CO-SL broker-managed). **Also fixed a latent bug:** the TGT-retry after-check site passed `legs=legs` to `_verify_exits_placed`, which has no `legs` param (a TypeError masked only because that retry path was dead until 23-Jun — Issue 3). **Forensics:** clamps are rare (0/0/1/1/0 over 18–24 Jun); PACEDIGITK is the sole after-check-era case. PACEDIGITK-shaped fixture re-classifies `0→1` (historical row left as the pre-fix record; live proof = next clamped trade). **Parity:** shared path, no mode branch → paper + live identical. Tests **+8** in `test_slice1_rr_aftercheck.py` (clamp-valid TGT+SL, discriminator placed≠band, real mismatch unchanged, missing-SL-still-CRITICAL-with-TGT-clamp, PACEDIGITK 0→1, parity, end-to-end via a circuit-capable adapter). **No schema change; alert/observability path only — activates next restart.** Branch `aftercheck-circuit-cap-fix-24jun`. See memory `aftercheck_circuit_cap_fix_24jun`.
- 2026-06-24 — Claude Code (Opus) — **tgt_retry_manager crash-loop post-mortem: confirm fix + add signature-lock, crash-loop alert, /health liveness (no harm Mon/Tue).**
  `orders/tgt_retry_manager.py` (the 30s TGT re-place safety daemon) error-looped every cycle Mon 22-Jun (**840**) + Tue 23-Jun (**230**) — `TypeError: is_within_market_hours() missing 2 required positional arguments` at `:161` (`is_within_market_hours(now)`, 1 arg). The `_loop` try/except caught it each time (process never died) → **840 silent ERRORs/day, ZERO alerts, the retry net dead for two live days.** **Root cause = BORN-BROKEN, not a latent regression:** `is_within_market_hours` was *created* by FIX-169 F18 (`c303e04`, 13-Jun) with the 3-arg signature `(now_t, open_t, close_t)` — it never had a 1-arg form; `tgt_retry_manager` was written 6 days later (`6ee5b7e`, 19-Jun) calling it with 1 arg → wrong from its first commit, never worked. Invisible until first run (deploy≠restart + FIX-189 market-window exit → loop didn't run until Mon 08:15). **Fix was INCIDENTAL** — rode along as P2 inside the NOCIL clamp fix (`8812026`, 23-Jun 02:06), restarted **23-Jun 12:28** (FIX-191 resume; last cycle_error 12:28:14, fixed `started` 12:28:29); today 0 crashes, confirmed. **HARM = NONE:** no trade has EVER been flagged `needs_tgt_retry` (`needs_tgt_retry=1 OR tgt_retry_count>0` empty all-time) + zero Bug-C SL-only events Mon/Tue → the candidate query (which the crash preceded) would have returned empty regardless. **PACEDIGITK** (Tue, `exits_verified=0`) is SEPARATE (Issue-2 circuit-cap): it filled, BOTH `sl_placed`+`tgt_placed` fired (TGT WAS placed), `needs_tgt_retry=0`, closed `SL_HIT`; `exits_verified=0` = `exits_verify_mismatch` after a circuit `exit_price_clamped_to_band`. **Why no test caught the born-broken bug:** the one manager test that enables the guard (`test_manager_skips_outside_market_hours`) **mocks** `is_within_market_hours` → arg-count masked. **ADDED (regression guard):** (1) signature-lock test (`inspect.signature` must be `(now_t, open_t, close_t)`, binds the 3-arg call, rejects the 1-arg call); (2) real-call regression tests (in/off-hours `run_once()` with the REAL un-mocked guard — reproduces the crash, now green); (3) **crash-loop self-detection** in `_loop` — ≥`crash_alert_threshold` (3 → ~90s) consecutive failures fire ONE throttled CRITICAL via the notifier (sentinel→email + Telegram), re-alert ≤1/h, INFO on recovery → "dead 2 days" becomes "1 email in 90s"; (4) **`health_snapshot()` on `/health`** (`tgt_retry_provider` threaded `main.py`→`scripts/healthcheck_server.py`): `ok=False`/503 when `dead` (never-started/thread-gone) or `crash_loop`, `disabled`→ok — and pre-flight **Phase B (`engine.py`) already curls `:8080/health`**, so a boot-dead daemon now auto-surfaces as `failing ['tgt_retry']` (no engine.py change). **Monitoring before = none** (in-proc thread, not a cron, no probe); **after = self-alert + /health 503 + Phase B**. Side-finding (separate, NOT fixed): today's 2 unrelated TypeErrors = `strategy_governor` calling `TelegramNotifier.send()` without `severity` (a lost circuit-breaker ALERT, not a trade action) — one-line fix queued. Tests +11 (`test_tgt_retry` +7, `test_healthcheck_server` +4). **No schema change. Alerting/observability only — restart activates the crash-detection + /health field.** Branch `tgt-retry-postmortem-24jun`. See memory `tgt_retry_crashloop_postmortem_24jun`.
- 2026-06-24 — Claude Code (Opus) — **Cron Officer post-ban CRITICAL briefing: malformed-email leak fixed + full job list in email.**
  First post-ban day (`telegram_ban_until` 2026-06-23 lapsed) the 09:20 briefing — CRITICAL via `_compute_severity` — routed through `cron_officer._send_telegram_md` → `TelegramNotifier.send(severity=CRITICAL)`, whose `_handle_critical` **unconditionally** wrote a bare `write_critical_sentinel` (no subject/html, body = raw Telegram MarkdownV2). `alert_watcher` SMTP'd it → an ugly raw-MarkdownV2 email (the "`24\-Jun…+11 more`" blob) IN ADDITION to the Telegram message. During the ban this was masked (ban branch emailed clean HTML + skipped Telegram); first post-ban day = first leak. **Root-cause fix (Option A, backward-compatible):** `TelegramNotifier.send`/`_handle_critical` gained `write_sentinel: bool = True` — default keeps the TG5 sentinel→email for EVERY existing CRITICAL caller (main/kill_switch/reconciler/eod_squareoff/token_monitor/order_placer — all verified unchanged); `False` suppresses BOTH the sentinel and the Telegram-failure email fallback. `cron_officer._send_telegram_md` now passes `write_sentinel=False` (Telegram can no longer emit a bare sentinel), and `deliver_report` writes ONE clean HTML email (`render_briefing_html`, subject `[LFL836] …`) for the post-ban CRITICAL case (`email_backup = is_eod or ban or severity=="CRITICAL"`). Net (Rama's call): post-ban CRITICAL briefing = clean Telegram **+** clean HTML email backup; post-ban INFO/WARN = Telegram only; ban window + EOD unchanged. **Full job list:** the "+N more" cap lives ONLY in `render_briefing_telegram` (compact, phone) — the HTML/plaintext email renderers never truncated, so routing the email through `render_briefing_html` restores the full ~39-job list automatically; added a "📧 Full list → email" Telegram footer when CRITICAL. **Part 3 (why today was CRITICAL):** transient false-positive — recomputed `build_report` is INFO (today's 19 heartbeats all SUCCESS, watcher fresh, preflight READY); NOT `daily_report` (PENDING_REDESIGN never escalates), NOT a real persistent miss — most consistent with a sub-second heartbeat-visibility race at the 09:20:00 cron boundary; self-cleared (debounce = separate follow-up). Tests: +5 (write_sentinel suppress ×2 + signature-lock updated; post-ban clean-HTML / INFO-telegram-only / 39-job no-truncation); full suite **3720 pass** (1 pre-existing PC-env `test_system_manager` fail). Branch `cron-officer-email-leak-fix-24jun`. Alerting-only — no schema, no restart (next 09:20 cron uses it). See memory `cron_officer_email_leak_fix_24jun`.
- 2026-06-24 — Claude Code (Opus) — **Preflight live_test_mode_caps envelope → 5/10 + test/prod sentinel isolation.**
  **TASK 1 (commit `9919652`):** the `live_test_mode_caps` CRITICAL preflight check (`scripts/preflight/checks/config_integrity.py` → `LIVE_TEST_EXPECTED_MAX_OPEN`/`_MAX_ENTRIES`) expected the OLD **4/6**, but the caps were deliberately RAISED to **5/10** on the 23-Jun FIX-191 resume → the 08:30 preflight would fire a false "caps drift" CRITICAL daily. Updated the expected envelope to 5/10 (it's the drift-guard's expected value, NOT the live source — live reads `risk.*` from system_config.yaml). Preflight is **alert-only** (`run_on_demand_if_missed` never blocks startup) so trading was never gated. `test_preflight_groups` caps tests updated. **TASK 2 (commit `72cdfd5`):** running the full suite on the LIVE VM emailed test-written CRITICALs — the alert-watcher consumes any `critical_alert_*.flag` in the real `data_store`, and tests exercising the sentinel path without a tmp dir (`test_fix142` gemini failure path; `test_preflight` CRITICALs via `deliver.py:77`) wrote into it. New `tests/conftest.py` autouse fixture `_isolate_real_sentinels` redirects any `write_critical_sentinel` whose `sentinel_dir` resolves to `<project>/data_store` (incl. the bare `"data_store"` default) → a per-test tmp sandbox; explicit tmp dirs pass through; patches the source + module-level importers. **Production alert path unchanged.** Regression test `tests/unit/test_sentinel_isolation.py`; 250 sentinel-writer tests pass with zero real flags. 3 leaked `.delivered` moved to `/tmp/leaked_test_sentinels_24jun/`. See memory `live_test_mode_permanent` + `test-sentinel-isolation-24jun`.
- 2026-06-23 — Claude Code (Opus) — **Tick-size fail-safe snap (trading path) + gemini_log_review hardening (tooling).**
  **PART 1 — tick (commits `478673f` + `1313a08`, pushed, NO restart → activates 03:30):** `broker/zerodha_adapter._snap_order_to_tick` **failed OPEN** — a missing/zero `tick_size` (symbol absent from `instruments.csv`, e.g. HDFCSILVER/SILVERBETA) returned the price **un-rounded** → Zerodha "enter price in multiple of tick size" rejection, losing valid silver/ETF signals. Fix at the **single adapter chokepoint** (no new chokepoint): (a) new `_resolve_tick()` → `DEFAULT_TICK` (0.05) fallback on cache-None / `InstrumentNotFoundError` / tick≤0 + throttled per-symbol WARN `snap_to_tick.missing_tick_size` (FIX-170 visibility); `_snap_order_to_tick` routes through it → **never un-rounded** (SL round-past-trigger direction unchanged); (b) `modify_order` gained optional `symbol` + snaps price/trigger **before the paper branch** (parity), threaded from `breakeven_manager`/`smart_tgt_manager` (both already pre-snapped → idempotent; this centralises it); (c) **de-dup** — removed the redundant `order_placer._round_to_tick` (float-floor, fail-open) + its 3 entry calls, gated on a green no-bypass test (`_tick_for` kept for the emergency-exit marketable-LIMIT). No-bypass audit: `place_order` + `modify_order` are the ONLY two broker-submission primitives; AngelOne adapter is dormant (unsnapped — needs the same if ever live). Tests `tests/unit/test_tick_failsafe_snap.py` (13) + 2 updated FIX-181 fail-open tests; 373/325 green. **FIX-170 (instrument-universe gap) = separate follow-up** — the 0.05 fallback + alert is the bridge. **PART 2 — gemini (commit `8c80a15`, cron tooling, next run):** `_extract_warning_plus` read the OLDEST-500 W/E/C lines with no restart cutoff → stale pre-restart errors surfaced as "current" (today's tgt_retry false alarm) + a heavy morning could consume the cap before the post-restart window. Added `_process_start_ts` (cutoff = last `main.py:1388` "Trading System v… starting" marker) + restart-aware skip (lexicographic +05:30 ts; whole-file fallback) + `_emit_review_failure_alert` (full-failure-ONLY CRITICAL sentinel → alert-watcher email + Telegram WARN + "REVIEW FAILED" stub; degraded-but-completed stays quiet). Tests `tests/unit/test_gemini_log_review_hardening.py` (6). See memory `tick_snap_failopen_23jun` + `gemini_log_review_windowing_23jun`.
- 2026-06-23 — Claude Code (Opus) — **Self-maintaining cron framework ARMED (registry = executable source of truth → auto-install + watchdog; pre-receive DEFERRED).**
  Armed the BUILD 1–6 framework (commits →`600e345`, pushed; VM bare HEAD `79c70e8` after the step-4 test commit). `config/cron_registry.yaml` is the **single executable source of truth**; `scripts/generate_crontab.py --generate` deterministically emits `deploy/cron/trading-system.cron` (ASCII+LF; fixed VM-path constants, no host lookup → identical output anywhere). **Equality model:** live `crontab -l` == canonical == `generate(registry)` — verified **four-way sha256 `1469f905…b9a23`** (PC committed / PC generated / VM deployed / VM live); **41 command-lines** (40 + new `sentinel_retention` 02:05). **Pre-arm gates G1–G4 all green** (G1 `--gate` zero-drops + only-`sentinel_retention`; G2 canonical==generate; G3 hooks/watchdog exec + valid shebangs; G4 canonical ASCII+LF), re-proven **natively on the VM** at push/reconcile. **ARMED:** **post-receive** (`~/trading-system.git/hooks/post-receive`, 1336 B) regenerates from the deployed registry + auto-installs the crontab *iff* `generate==canonical` (else WARN + skip — self-protecting); replaced the old 329-B checkout-only hook; proven by empty push `79c70e8` → zero drift. **cron-watchdog** systemd timer (`/etc/systemd/system/cron-watchdog.{service,timer}`) fires **19:30 IST daily** (`Persistent=true`), asserts `cron_officer_eod` + `check_cron_drift` both heartbeated today → CRITICAL sentinel via the **cron-INDEPENDENT** path if missing; **first run Wed 24-Jun 19:30**. **DEFERRED by choice (NOT installed):** **pre-receive** equality guard (`deploy/hooks/pre-receive`) — would hard-reject a push whose canonical != `generate(registry)` (+ blast-radius bound; override `[cron-canonical-override]`); arm later via dry-run (`CRON_GUARD_DRYRUN=1` injected into the *installed* copy) → enforce; break-glass `rm` the hook. Until armed, post-receive still keeps a bad canonical off the live crontab — it just won't reject the push. **pre-commit** (`deploy/hooks/pre-commit`) = local-clone-only. NB today's `check_cron_drift` heartbeat is absent (today's 18:00 ran on pre-deploy code; the deployed `check_cron_drift.py:197-198` self-heartbeats) — benign, self-corrects 24-Jun; do NOT manually trigger the watchdog today. See memory `cron_framework_armed_23jun`.
- 2026-06-23 — Claude Code (Opus) — **Sentinel retention cron + test-pollution root-cause fix.**
  `data_store/critical_alert_*` had no retention (slow unbounded growth). Added daily **sentinel_retention**
  (02:05): `find data_store -maxdepth 1 -name 'critical_alert_*.delivered' -mtime +7 -delete` — deletes
  DELIVERED alerts >7d, **NEVER** `*.flag`; marker `cron_marks/sentinel_retention.done`; registered in
  `cron_registry.yaml` + canonical `deploy/cron/trading-system.cron` (mirrors `backup_retention`). Email is
  the authoritative record, so `.delivered` are just local processed-markers (no archive). **Assessment
  (23-Jun, VM clock confirmed 23-Jun, NTP-synced):** VM = 61 `.delivered` (104 KB, 18–23 Jun), **0 `.flag`**
  (alert-watcher healthy → no delivery bug); PC dev tree = 42 `.flag` = test-suite artifacts
  (`test_fix132_email_fallback.py` wrote real sentinels via `sentinel_dir="data_store"`), deleted (gitignored
  noise). **Root cause fixed:** `test_fix132` now uses a throwaway tmp dir for `sentinel_dir` +
  `failed_alerts_log_path` (7/7 green, 0 sentinels written to `data_store/`). `data_store/` gitignored (0
  tracked sentinels). Two commits (cron / test fix). NOT pushed — cron activates on next push +
  `crontab deploy/cron/trading-system.cron`. (VM one-time archive skipped — all <7d; cron ages them out.)
- 2026-06-23 — Claude Code (Opus) — **FIX-191: API-failure breaker is connectivity-only (false SOFT_KILL halt) + halt alert WARN→CRITICAL + resume caps + Telegram restored.**
  **Incident (live):** at 10:07 a SOFT_KILL auto-tripped on "3 consecutive API failures" and `webhook_receiver`
  403'd EVERY signal for ~2h (150 webhooks/hr, **0 accepted**) — yet the broker was fine. Root cause: the
  consecutive-API-failure breaker (FIX-069 intent = transient connectivity outage) counted **business
  rejections**. `OrderRejectedError extends BrokerError`, and `signal_processor` calls `record_api_failure(be)`
  on ANY `BrokerError` at placement → 1 broker MIS-block reject + **2 client-side slippage-guard aborts**
  (`order_placer` raises `OrderRejectedError` BEFORE any broker call) = 3-in-a-row → trip. Only the 2 morning
  fills (GARUDA, PPLPHARMA, both SL) ever placed; the rest of the day was a **false** halt (consecutive-losses
  was NOT the cause — only 1 loss had closed at trip time). **Fix (permanent, single chokepoint):**
  `KillSwitch.record_api_failure` now **WHITELISTS** transient types — counts ONLY `BrokerTimeoutError` /
  `BrokerRateLimitError`; `OrderRejectedError` (broker reject OR slippage abort), `SLUnplaceableError`,
  `ProductNotSupportedError`, generic `BrokerError` no longer count; `BrokerAuthError` still excluded (FIX-185).
  No mode branch (paper+live parity). **Halt-alert severity:** `soft_kill()` notified at `severity="WARN"`,
  which **drops silently on a Telegram send failure with no email fallback** → today's halt reached Rama through
  ZERO channels. Raised the SOFT_KILL halt notification **WARN→CRITICAL** (routing only; kill stays SOFT_KILL;
  uses the existing CRITICAL email-fallback path). Scoped — routine WARN alerts unchanged, `hard_kill` has no
  notifier send, IP-403/exit alerts already CRITICAL. **Resume caps (parity, 10/5/5):** `max_daily_trades 20→10`,
  `live_test_max_open_positions 4→5`, `live_test_max_entries_per_day 6→10`, `max_consecutive_losses 2→5`.
  **Telegram restored** — root cause = **stale bot token** (all 277 historical `failed_alerts.log` "failed to
  deliver to ['-100…']" were the dead token, not the chat_id; 0 failures today). NOTE: the `telegram_alerts` DB
  table is **vestigial** — the notifier never writes it (sends via HTTP + sentinels + `failed_alerts.log`), so
  an empty `telegram_alerts` is NOT evidence of a Telegram problem. Tests: +2
  (`test_fix191_order_rejected_not_counted`, `test_fix191b_soft_kill_halt_alert_critical_emails`); 34/34
  kill_switch + 7/7 FIX-132 email-fallback green. Commits `7ff24b2` (FIX-191+caps) + `6bed838` (WARN→CRITICAL).
  **Deployed + resumed 12:28** (bare HEAD `6bed838`; kill cleared via `clear_kill_switch.py`; caps 5/10/5
  confirmed in the running process; signals flowing). Still open: F&O/MIS ban pre-screening (TVTODAY reached the
  broker — its reject no longer trips the kill post-FIX-191, but the pre-screen gap remains a separate item).
- 2026-06-23 — Claude Code (VS Code) — **NOCIL circuit-clamp fix: placeability gate + leg-asymmetric handling + dual pre-fill reject + P2 (tgt_retry).**
  Root cause (22-Jun NOCIL): the FIX-190 Bug-D circuit clamp was **side-agnostic** — a LONG TGT recalc'd
  above the upper circuit was clamped DOWN to `upper×0.98 = 187.00`, **below** the 189.78 fill, producing an
  instantly-marketable SELL (2.35 s scratch, net −₹0.56, mislabeled TGT_HIT). Forensics: **1/77 trades**
  (NOCIL only; mechanism system-wide for LIMIT_TRIPLE, trigger geometric — entry within ~2% of a circuit).
  The wrong-side guard already existed on the TGT-**retry** path (Guard 3 + a post-clamp re-check, commit
  `6ee5b7e`) but was **never** retrofitted to the primary `place_exits` site (clamp added earlier in
  `b4cd434`) — an accidental per-call-site omission; the retry guard had **never fired in prod** (the dead
  `is_within_market_hours` TypeError, fixed here as P2). **Fix (permanent, primitive layer):**
  (1) `clamp_to_circuit_band` → `clamp_exit_into_band(... leg, direction, entry_fill ...) → ClampResult`
  (frozen; adds the leg/direction polarity check vs the fill; the **single chokepoint**, see the section
  above); (2) the 3 LIMIT_TRIPLE sites consume `.placeable` — **TGT** unplaceable → SL-only/hold (Bug-C →
  TGTRetryManager), **SL** unplaceable → `SLUnplaceableError` → emergency-close + hard_kill (traced,
  reuses existing escalation; `_is_ltp_validation_error` returns False for it); `entry_fill=avg_fill_price`
  threaded through the engine; (3) pre-fill **circuit-proximity reject** in `secondary_screener`
  (framing-b BOTH legs, `entry_gate.circuit_proximity_reject_enabled`, default true — 0 over-rejection on
  the 77-trade history); (4) **de-dup** = removed the now-redundant retry-path Guard-3 wrong-side clause +
  post-clamp place-then-cancel re-check (kept the `tgt_price<=0` computation guard). **P2:**
  `tgt_retry_manager._run_once_locked` now calls `is_within_market_hours(now.time(), MARKET_OPEN,
  MARKET_CLOSE)` (was 1-arg → TypeError every cycle, silently disabling the retry sweep). **Scope = Option
  2:** gate the 3 LIMIT_TRIPLE clamp sites; CO-TGT + G5b-SL are evidence-justified, **test-enforced**
  never-clamp exceptions (no path silently un-guarded). **No schema change.** Parity: shared code, no mode
  branch. Tests: +new `tests/unit/test_nocil_clamp_fix.py` (E.1–E.9) + extensions; touched suites **293
  passing**; full sweep **3640 passed / 3 pre-existing env failures** (`test_interactive_startup`,
  `test_state_store::…fix156`, `test_system_manager` — confirmed identical on pre-change HEAD `d79c186`,
  all time/date-of-day dependent, modules untouched). 4 commits (de-dup isolated). Activates next VM
  restart; `clamp_exit_into_band` / `ClampResult` / `SLUnplaceableError` locations in PATHS.md.
- 2026-06-22 — Claude Code (VS Code) — **SATS first-scan triage + fixes (Semgrep: 23 findings).**
  Triaged all 23 Semgrep (`p/python` + `p/security-audit`) findings: **22 confirmed false positives**
  (logger-credential-leak rules firing on booleans / env-var NAMES / masked `totp[:3]***` / account ids /
  the keyword "token" meaning *instrument_token*; dynamic-urllib on hardcoded/config/localhost URLs;
  Telegram `exc` cannot leak the bot token — `TelegramNotifier._post_with_retry` swallows all `requests`
  exceptions) and **1 real fix**: `scripts/preflight/checks/recovery.py` `_ensure_dir` chmod
  **0o755 → 0o700** (owner-only) on the `logs/` + `data_store/cron_marks/` dirs (least privilege;
  single-user VM) — commit **ab8b12e**. The file-oriented `insecure-file-permissions` rule still flags the
  *directory* chmod (suggests 0o644, which would strip the traversal bit) → a documented one-line
  `# nosemgrep` (Rama-approved) sits directly above the call. **Scan-script fixes (PC-only, gitignored):**
  both `.bat`s now set `PYTHONUTF8=1` (Semgrep/Bandit crashed writing the report under Windows cp1252);
  `scan_semgrep.bat` gained an optional **baseline** via `sats\semgrep_baseline.txt` (pinned `65439ff`)
  so future scans surface only NEW findings (the 22 reviewed FPs suppressed). Post-fix full scan = **22**,
  recovery.py = **0**. No `# nosemgrep` scattered on the 22 FPs (baseline handles them). See PC Paths →
  SATS + memory `sats_triage_22jun` / `sats_tooling`.
- 2026-06-22 — Claude Code (VS Code) — **SATS static-analysis scan scripts created (PC-only, manual).**
  New `sats\scripts\scan_bandit.bat` + `scan_semgrep.bat` and `sats\reports\` (Bandit 1.9.4 / Semgrep
  1.167.0 in the pre-existing isolated venvs under `sats\`, which is git-ignored — tools never deploy to
  the VM). On-demand only (no hooks/automation): each scans the repo root, **excludes `venv,sats,.git`**,
  writes a timestamped txt report to `sats\reports\` and echoes it (`chcp 65001`, locale-independent
  PowerShell timestamp, auto-creates `reports\`, tool run ONCE via `-o`/`--output` then `type`). Semgrep
  rulesets `p/python` + `p/security-audit` (login-free; first run downloads, cached after); Bandit `-x`
  uses absolute paths (`manager.py` matches each token as glob **and** substring). See PC Paths → SATS +
  memory `sats_tooling`. Tool venvs left untouched.
- 2026-06-22 — Claude Code — **Slice 1: fill-time R:R fix (all 4 recalc sites) + SL/TGT after-check (schema v35).**
  Deployed to `main` (commit a730c63); activates on the VM's next restart (Tue 23-Jun 08:30), when the
  live DB migrates **v34→v35**. **Bug:** the deferred-exit TGT recalc used order_placer's hardcoded
  `self._rr_ratio` (default **2.0**, never overridden in `main.py:2063-2087`) instead of the originating
  strategy's `tgt_risk_reward`; deferred exits are the ONLY TGT that reaches the broker (placement defers,
  OP-NS1), so the broker TGT was **always 2.0-based** — gap_fade (1.5) / gap_go (2.5) traded at the wrong
  R:R; FIX-013's "preserve R:R" was unmet. **Fix (A):** strategy R:R frozen at placement
  (`trades.tgt_risk_reward_applied`, carried on the ENTRY `_FillEntry`), read at fill via
  `_resolve_fill_rr()` at **all 4** recalc sites — the 3 audited + the **TGT-retry path
  (`order_placer.py` ~line 2462) the audit missed** (same 2.0 bug); NULL/recovered (pre-v35) →
  fall back to `self._rr_ratio` 2.0 **+ WARNING**. **After-check (B):** `_verify_exits_placed()` reads
  the persisted SL/TGT order rows (ground truth; parity-safe — paper+live persist the same rows) and
  checks price + qty vs intended; **missing/wrong SL = CRITICAL**, **TGT-only = WARN**, **alert-only**
  (no auto-cancel this slice); verdict in `trades.exits_verified` / `exits_verify_detail` (READ-BACK,
  unlike the write-only v34 sizing cols); CO SL is broker-managed (bracket) → TGT-only check.
  **Schema v35:** trades += `tgt_risk_reward_applied` / `exits_verified` / `exits_verify_detail`
  (rebuild-trades migration, v34 pattern; `EXPECTED_SCHEMA_VERSION` 34→35); **DB-copy gated** on the real
  live DB (34→35, 77 trades preserved, integrity ok, idempotent). 16 new tests
  (`tests/unit/test_slice1_rr_aftercheck.py`); full suite = pre-existing baseline, zero new. **Sequencing
  (Option 2):** strategy YAMLs kept at current values (gap_fade 1.5 / gap_go 2.5 / rest 2.0) for one day so
  Tuesday's live trades prove the broker honours EACH strategy's distinct R:R; **all 15 → R:R 1.5 lands
  Wed 24-Jun** after the proof. Branch `slice1-rr-fix-aftercheck-22jun`.
- 2026-06-22 — Claude Code — **`fetch_fno_ban` endpoint fix + severity downgrade (fail-open for EQ).**
  Monday 08:35 the job emailed a CRITICAL: the old JSON endpoint
  `nseindia.com/api/live-analysis-banned` is dead (**404**). New source = NSE Clearing's daily CSV
  `https://nsearchives.nseindia.com/content/fo/fo_secban.csv` (browser UA + Accept headers; verified live
  → today 1 ban: KAYNES). Parser rewritten **JSON→CSV** (`parse_secban_csv`: header date `DD-MMM-YYYY`→ISO,
  `<serial>,<SYMBOL>` rows; HTML-block-page guard; no-ban = header only). **Finding:** `is_symbol_fno_banned`
  has **no runtime consumer** (only its own test) — the ban list never gated EQ (or anything), so the old
  "F&O signals BLOCKED" + fail-closed sentinel was misleading. Failure now **WARN not CRITICAL** and
  **fail-open**: on 404/timeout/HTML/stale-date → `log.warning` + Telegram WARN (no `critical_alert_*.flag`,
  **no email**) + heartbeat + **exit 0** (Cron Officer shows done, not failed/missed). Stale CSV (date≠today)
  is WARN + not stored. `fno_ban.fail_closed` default **true→false** (kept as a knob for a future F&O era;
  ON still only ever gates F&O via the sentinel, never EQ); removed dead `min_expected_fields`. Config
  `system_config.yaml` + `FnoBanConfig` updated. Tests rewritten (CSV parser + main() WARN/fail-open/
  heartbeat/exit-0). No crontab change (same 08:35 schedule + job name). Branch `fix-fno-ban-endpoint-22jun`.
- 2026-06-21 — Claude Code — **Diary #4: tier-multiplier ON/OFF position-sizing switch (Option δ flat-Rs).**
  `config/system_config.yaml → position_sizing.enabled` (default **true = ON**, byte-unchanged
  score-tier × perf sizing). **OFF** = flat Rs/order (`flat_value_rs`, Pydantic-validated > 0):
  flat is ONE MORE ceiling atop risk/capital/concentration (those still bind), score + perf NOT
  applied, < 1 lot → BELOW_MIN skip. Switch lives in `capital/position_sizer.py calculate()` above
  any paper/live split (parity clean). **Schema v34**: `trades` += 10 sizing-audit columns
  (tier_multiplier_mode / tier_weight_applied / perf_weight_applied / flat_value_rs_used /
  qty_by_{risk,capital,concentration,flat} / binding_constraint / actual_position_value_rs);
  chosen over order_execution_log (per-leg, fill-time) — one decision per trade, mirrors the
  tolerance_source precedent. `breakdown` plumbed signal_processor → order_placer.place() →
  create_trade; v34 = trades 12-step rebuild (MIGRATION_TABLES[34]), DB-copy-tested on the real
  live DB (32→34 idempotent, data preserved, integrity ok). Mode surfaced in pre-flight Phase A
  config display + Cron Officer EOD badge. Toggle = edit `enabled` + `systemctl restart`. Monday
  22-Jun ships default ON = **zero behavioural change** (VM-verified enabled=True/flat=None).
  ~25 tests; full suite 3560 green. Commits 4c0ba43 + 3382e55 + 57d4ead. Memory
  `diary_4_tier_multiplier_switch_21jun`.
- 2026-06-21 — Claude Code — **Pre-flight check system BUILT + ACTIVATED (Monday 22-Jun = first live proof).**
  New `scripts/preflight/` — a 3-phase daily pre-market readiness check: **Phase A 08:30**
  (infra, app down), **Phase B 09:14** (engine readiness via `:8080/health`+`/metrics`+DB,
  app up), **Phase C 09:15→09:20** (passive signal-warmup watch, `--watch-sec 285`). **55
  checks / 10 groups**; **ALERT-ONLY** (exit 0 always — Rama is the gate, never auto-blocks);
  **auto-fix only on a DB/OS SAFE whitelist** (in-memory app state = alert-only). Sentinel
  `data_store/preflight/today.json`; email on CRITICAL/phase-C + Telegram outside the ban
  (reuses `write_critical_sentinel` content_type=html + `cron_officer._send_telegram_md`).
  **Cron Officer morning briefing now embeds a pre-flight banner** (reads the sentinel;
  NOT_RUN/stale → briefing CRITICAL). **premarket_healthcheck SUBSUMED by Phase A** (5
  checks ported + parity-tested; removed from cron+registry; script kept w/ deprecation
  header, delete after Mon+Tue proof). **Schema v33** (+preflight_runs / preflight_check_results
  / preflight_autofix_log; pure additions, no MIGRATION_TABLES; DB-copy tested on the real
  64MB live DB: 32→33 idempotent, data preserved, integrity ok; live DB migrates at the
  Monday restart). `main.py` startup hook runs Phase A/B on-demand if a 06:00-09:20 restart
  missed the cron slot. Cron reinstalled (`crontab -l | diff`=0, 3 preflight lines live, 13
  markers); snapshots in `data_store/cron_audit/crontab_{pre,post}_install_preflight_21-Jun-2026.txt`.
  ~150 preflight tests; 116 cron tests unchanged. Commits 165c845 (schema) + d1355e8 (cron+hook)
  + the preflight feature commits. Memory `preflight_system_21jun`. NB Monday is the live proof;
  full SYSTEM_MAP cron-table rows + CONFIG_GUIDE update land post-proof.
- 2026-06-21 — Claude Code — **Cron Officer revision ACTIVATED — crontab reinstalled (the held step done).**
  Rama signed off the HTML samples (clean EOD / morning briefing / Fri-19-Jun dry-run) + Bug A/B/C
  dispositions. **Pre-activation safety:** `bash -n deploy/cron/trading-system.cron` clean; marker-tail
  safety proven — a broken/unwritable `data_store/cron_marks` dir does NOT alter the job's captured exit
  (the job runs first, `rc=$?` captures it, the `; mkdir -p … ; echo > …done` tail is append-only and
  after `;`); the 3 backup lines (db_backup/analytics_backup/backup_retention) preserve the exit code with
  the marker write strictly AFTER completion. VM snapshots saved:
  `data_store/cron_audit/crontab_{pre,post}_install_21-Jun-2026.txt` (named for the actual install date —
  the runbook said 20-Jun, but activation slipped to Sun 21-Jun). **Reinstall:**
  `crontab deploy/cron/trading-system.cron` (VM file md5 == local `3c6871a8…`) → **`crontab -l | diff` = 0**,
  **10** exit-code marker lines present, new times live (**briefing 09:20 / EOD 18:50 / system_manager
  18:45**), old **04:55/18:30 removed**, `cron_marks/` pre-created. Also untracked the accidental
  `reports/system_manager/2026-06-19.txt` sweep + gitignored `reports/{system_manager,cron_officer}/`
  (commit **f8edc4a**, pushed → VM tree redeployed). **Sunday 21-Jun (holiday)** suppresses the real
  morning/EOD reports (expected) — only passive markers fire (disk_monitor hourly, gemini_weekly_patterns
  18:00). **Monday 22-Jun = first live proof** (09:20 briefing email + 18:50 EOD email; Telegram still
  banned → `[LFL836-BAN]` subject; **Tue 23-Jun** Telegram auto-resumes and the prefix drops). Registry +
  officer code were already in 3dd72b0. Memory `cron_officer_revision_20jun` updated → ACTIVATED.
- 2026-06-20 — Claude Code — **Cron Officer revision — Phase 2-6 (full visibility + Bug A/B fixes + rich
  email/Telegram).** Commit 3dd72b0 (pushed; **crontab reinstall + docs PENDING Rama's dry-run sign-off**).
  EXTENDS `config/cron_registry.yaml` (no fork). **Phase-1 finding:** the 7 "missed" on 19-Jun were **3
  permanent bugs + 4 environmental** (FIX-189 morning, already fixed); registry was already 33 jobs, "20"
  was the EOD *expected* subset. **Bug A** registry key `gemini_data_integrity` → **`gemini_data_integrity_check`**
  (matches the heartbeat the script records — 0 heartbeats under the old key ever; cron file needed no
  change). **Bug B** `cron_officer_eod` records a `STARTED` heartbeat BEFORE building the report → stops
  self-reporting missed. **Bug C** (`daily_report` has no heartbeat) DEFERRED → shown **⏸ Pending** (never
  CRITICAL; lands with the xlsx redesign). **Schema:** `category` DERIVED from `cadence` (single source of
  truth, no 33-job duplication) + `heartbeat_required`/`detection_method`/`excluded_reason` (defaults
  derived) + `OfficerConfig` (`officer:` block: day-window 00:00-23:59, morning **09:20**, EOD floor 18:45
  +5min = **18:50**, `telegram_ban_until: 2026-06-23` auto-clears). `build_report` classifies **EVERY** job
  due today by detection method: `heartbeat_db`, or `exit_code_file` (a marker the cron line appends —
  `; rc=$?; … > data_store/cron_marks/<name>.done`; missing → **NO_SIGNAL**, never a false CRITICAL), or
  `none`. **Rich delivery** (`scripts/cron_report_render.py`, pure/tested): HTML email (Gmail-safe inline-CSS
  tables — severity banner, stat cards, progress bar, status pills, per-task table, change-log, excluded,
  footer) + plain mirror + Telegram MarkdownV2 (`escape_md_v2`). `alerts/critical.py` + `alert_watcher._build_email`
  extended for `content_type=text/html` → multipart/alternative (fail-fast w/o `plain_fallback`) + verbatim
  `subject` — **backward-compatible** (no content_type → plain, unchanged). EOD always emails; morning
  briefing emails during the ban, else Telegrams; subject carries severity emoji + **`[LFL836-BAN]`** in the
  ban window. New CLI `--force-dry-run` / `--as-of-date`. **Verified** (dry-run on the real VM DB, as-of Fri
  19-Jun): **17 done / 3 real-missed / 1 ⏸ pending** (vs old "12/20, 7 missed"); HTML samples in
  `reports/cron_officer/`. +20 tests; 116 cron/alert green; legacy `build_eod_summary`/`build_briefing` kept.
  See memory `cron_officer_revision_20jun`. NB **ACTIVATED 2026-06-21** — the live crontab now == the
  canonical file (09:20/18:50; `crontab -l | diff`=0); see the 2026-06-21 entry above.
- 2026-06-20 — Claude Code — **Slippage overrides ship FULLY EMPTY (commit 8fd54a8, follow-up to 2ce54ab).**
  Rama's call: set `by_price_band` to `{}` too (was the lone example `0-100: 0.18`) → every entry resolves to
  the global **0.22** baseline everywhere for clean data collection first; add per-symbol/strategy/band
  overrides later from real evidence. Verified live on the VM (`load_all` → all maps `{}`). The v30→v32
  migration was confirmed clean on a copy of the real DB (63 trades preserved). Docs/test updated.
- 2026-06-20 — Claude Code — **Slippage tolerance override hierarchy — Phase 3a (MANUAL, schema v32).**
  Lets Rama set per-symbol / per-strategy / per-price-band entry-slippage tolerances NOW (from trading
  knowledge), without waiting for the Phase-3b auto-recommender. New `entry_gate.slippage_control.overrides`
  block (`enabled` + `by_price_band` / `by_strategy` / `by_symbol` maps, **all ship EMPTY → pure global
  0.22 baseline everywhere** for clean data collection first). The effective `sl_fraction` is resolved **MOST-SPECIFIC-WINS: Symbol > Strategy > Price
  Band > Global** by the pure `resolve_slippage_fraction()` (orders/order_placer.py), threaded into
  `_slippage_decision` via a new `fraction_override` arg → the override drives the pre-order abort. Resolved
  up-front in `place()` (band from `signal_trigger_price`/`entry_price` via `get_price_band`) so it both
  enforces the guard AND is recorded. **Transparency:** `tolerance_source` (e.g. `symbol:IDEA` /
  `strategy:gap_fade` / `band:0-100` / `global`) + the fraction are logged on every entry
  (`entry_slippage_observed` + `slippage_guard_exceeded` + the Telegram abort) and **persisted**:
  **schema v32** adds `tolerance_fraction_used` (REAL) + `tolerance_source` (TEXT) to **trades** (written by
  `OrderManager.create_trade`) and **order_execution_log** (the async `slippage_recorder` copies them from
  the parent trade onto the execution row). `MIGRATION_TABLES[32] = [trades, order_execution_log]` rebuilds
  both (new cols → NULL); `EXPECTED_SCHEMA_VERSION` 31→32. **Validation:** config hard-rejects fractions
  outside `(0,1]`; `validate_slippage_overrides()` warns at startup on extreme values (<0.05/>0.50) and on
  `by_symbol`/`by_strategy` keys that match no known instrument/strategy (typo → silently ignored). **System
  Manager EOD: 10th check** `SLIPPAGE OVERRIDES` (visibility-only — never a violation / never SOFT_KILL):
  active override counts + per-`tolerance_source` placed/rejected usage today. **Parity:** mode-agnostic,
  paper + live. Only `sl_fraction` mode consults overrides (flat_tiers/pct unchanged). Closes the loop:
  Phase 1 records WHAT happened, Phase 3a records WHICH rule applied → Phase 3b can later analyse
  effectiveness (join `trade_slippage_log.rr_damage_pct` on `trade_id`). **+v31→v32 migration verified on a
  built DB (DROP-COLUMN-simulated v31 → migrate → cols added, rows preserved, round-trips).** +27 tests
  (control 20 / recorder 1 / migration 1 + version-pin fixes); 367 affected tests green. CONFIG_GUIDE.md +
  slippage_intelligence.md updated. Activates next restart. Commit 2ce54ab.
- 2026-06-20 — Claude Code — **Slippage intelligence Phase 1 — raw data layer (schema v31).** Permanent
  execution-intelligence tables (RAW facts only; analytics computed on-demand in Phase-2 reports — NO
  aggregate/stale tables). **v31** adds 3 append-only tables to the MAIN DB: `order_execution_log` (per
  filled leg), `trade_slippage_log` (per trade, incl. **`rr_damage_pct`** = % of the planned risk budget
  execution ate — the key metric), `market_execution_context` (Priority-2, nullable bid/ask/spread).
  `EXPECTED_SCHEMA_VERSION` 30→31 (pure additions; `CREATE IF NOT EXISTS` + executescript creates them,
  no rebuild). New `orders/slippage_recorder.py` `SlippageRecorder` subscribes **ASYNC** to `OrderFilled`
  (→ order log + context) and `PositionClosed` (→ trade roll-up) — async dispatch means the EventBus
  logs but NEVER re-raises handler exceptions, so recording is **fully decoupled and can never block
  trade execution** (handlers also try/except; inserts best-effort/never-raise). Pure tested helpers:
  adverse/favourable slip (adverse=positive), `calc_rr_damage_pct` (Rama's example 20%), `get_price_band`,
  `build_trade_slippage_row`. Wired in main.py (construction wrapped). `slippage_bands` config (default
  0-100..1000+). **Parity:** records paper + live (events fire in both). **Verified live: v30→v31
  migration runs cleanly on a copy of the real DB** (3 tables created, 63 trades intact). +19 tests; 156
  migration/state_store/config tests pass. Queries + roadmap in `docs/slippage_intelligence.md`. Commit
  71d4186. Activates next restart (migration at StateStore init). Phase 2 (reports) + Phase 3 (adaptive
  engine: Global→Band→Strategy→Symbol) pending.
- 2026-06-20 — Claude Code — **Slippage tolerance → %-of-SL-distance (replaces today's flat-Rs tiers).**
  Calibration investigation (`slippage_calibration_data_20jun`) found **SL is fixed (signal-based) and
  TGT recalcs from the fill (FIX-013)** ⇒ entry slippage directly inflates the risk budget. Rama's model:
  cap slippage as a FRACTION of the SL distance. `entry_gate.slippage_control` (replaces `slippage_tiers`),
  mode-selectable: **sl_fraction (DEFAULT)** `tolerance = min(SL_dist × max_slippage_fraction(0.22),
  absolute_cap_rs(₹5))` — auto-scales with price AND strategy SL%; `flat_tiers` (today's) + `pct` kept for
  A/B; **`hard_max_slippage_rs(₹10)`** absolute ceiling in every mode; `also_apply_pct_check`
  belt-and-suspenders; disabled → legacy flat %. `SL_dist = |signal_trigger − sl_price|` (both available
  in `place()`; cap-only if SL missing). New `_compute_slippage_tolerance` / `_slippage_decision`
  (order_placer, pure/tested); guard uses them; the calibration log now records
  `sl_distance_rs`/`tolerance_rs`/**`fraction_of_sl_used`** per entry (tune 0.22 from real numbers).
  **Step 7.3 (LIMIT=signal+tolerance) deliberately NOT done** — it's a no-op when the guard passes (the
  `release_ltp` cap dominates), risks NON-FILLS when planned-entry ≠ trigger, and doesn't fix the CO/SL
  gap; the pre-order ABORT is the safe enforcement. Verified live (all 5 spec examples: THELEELA
  ₹3.10 > ₹2.12 ABORT, etc.). **FINDING:** LLOYDSENGG (₹0.43 = 26% of its 2% SL) ABORTS at 0.22 (spec
  Step 9.6 assumed flat_tiers) → raise `max_slippage_fraction` to ~0.27 to allow it. +18 tests; 137 pass.
  Commits a08901e + 3ddb774. Activates next restart.
- 2026-06-20 — Claude Code — **Tiered entry-slippage abort (per-price-band, in RUPEES).** [superseded
  same day by the %-of-SL model above; flat_tiers retained as a mode] Augments the
  flat `entry_gate.max_entry_slippage_pct` (1%) with a tiered Rs tolerance by price band, calibratable in
  config without code changes. The pre-order guard (`order_placer.place()`, the FIX-128 LTP-vs-trigger
  check) now aborts when `|LTP − signal_trigger|` exceeds the band's `max_slippage_rs`. Starter bands
  (Rama to calibrate over live days): **<100→₹1.00, 100-200→₹1.25, 200-500→₹2.00, >500→₹3.00** (lower
  bound EXCLUSIVE → 100.00 is in the 100-200 band). **THELEELA (trigger 481.50, slip ₹2.60) now ABORTS**
  (200-500 band tol ₹2.00; it passed the old flat 1% = ₹4.81). `also_apply_pct_check: true` keeps the
  flat % as belt-and-suspenders; `enabled: false` → flat % is the sole gate (backward-compatible). New
  `tier_slippage_tolerance_rs` (orders/price_math.py) + `_slippage_abort_reason` (orders/order_placer.py)
  + `EntrySlippageTiersConfig`/`SlippageTier` (config_loader, `extra="forbid"`) + the
  `entry_gate.slippage_tiers` block in system_config.yaml. Every entry logs `entry_slippage_observed`
  (Rs+pct) for calibration; an abort logs `slippage_guard_exceeded` + a WARNING (Telegram + email
  fallback). **Parity:** runs in `place()` for paper + live; config is mode-agnostic. Verified live
  (config loads; ₹2.60 → abort, ₹1.50/₹2.80-at-600 → allow). +18 tests. Commit d464f33. Activates next
  restart; abort-only (safe direction), editable. NB the system_config.yaml hash change → the
  security-watcher emits one WARNING "system_config changed" (expected for a config deploy).
- 2026-06-20 — Claude Code — **Copy approval is Telegram-INDEPENDENT (verified) + email fallback.**
  Rama's check: does VM→PC copy work with Telegram banned (IN, until 23-Jun)? **YES** — `request-copy`
  writes the token to a LOCAL file (+ audits `copy_audit.log`) BEFORE `send_alert`, and the gate
  decision is purely local (time-lock/switch/session/token); `send_alert` is best-effort and never
  blocks. Proven live (token granted + copy succeeded, Telegram down). One gap fixed:
  `copy_gate.send_alert` INFO "token issued" / WARNING denials were Telegram-only → now an **email
  fallback** writes a sentinel (→ alert-watcher email) for ANY tier when Telegram did NOT deliver
  (`result.sentinel_path`/`success`), with `context.severity` keeping the subject correctly labelled
  (INFO/WARNING) and NO email spam when Telegram is up (delivered → skip); CRITICAL still exactly one
  email. Security CRITICALs (bypass / protection-disabled, via `security_monitor._send`) already
  emailed. Verified live (deployed `send_alert` writes an INFO-labelled sentinel). +4 tests. Commit
  cd85994.
- 2026-06-20 — Claude Code — **Copy protection: HARD-BLOCK ACTIVATED** (was detection-only). Rama's
  go-ahead, done at 14:00 Sat (markets closed, service down — safest window). Pre-checks confirmed NO
  cron / post-receive hook / systemd unit invokes `scp`/`sftp`/`rsync`, real binaries present, PATH puts
  `/usr/local/bin` before `/usr/bin`. `bash deploy/security/install_copy_protection.sh` symlinked
  `/usr/local/bin/{scp,sftp,rsync}` → `deploy/security/bin/copy-guard` + `/usr/local/bin/request-copy`.
  **Verified live:** `which scp`→wrapper; no-token `scp` → `DENIED (NO_TOKEN)` exit 1, real scp never
  executed; `request-copy` → 15-min token → `scp` exit 0, copy succeeds (real binary reached); token
  revoked. **git push (this commit) + interactive ssh confirmed unaffected** (ssh is not wrapped).
  Fail-safe = block-on-error. Time-lock 18:00–08:00 absolute (unit-tested; not live-testable at 14:00).
  Audit: no-token denials are logged (not alerted — normal case); `request-copy` issuance + TIME_LOCK/
  SESSION denials alert; raw-scp bypass still → CRITICAL via auditd. **Rollback:**
  `bash deploy/security/install_copy_protection.sh --uninstall`. Memory `vm_security_phase2`.
- 2026-06-20 — Claude Code — **Kite IP-403: actionable alert (headless self-recovery, no halt).**
  Investigated the "morning manual resume" blamed on the IP-allowlist 403. Deep trace: **post-FIX-185 a
  pure IP-403 ALREADY self-recovers** — `place_order` 403 → `PermissionException` → `BrokerAuthError` →
  `signal_processor` rejects the signal (SP12) + `record_api_failure` ignores it (FIX-185, never trips);
  reads are NOT IP-gated so `order_monitor`/`order_reconciler` never hit their 3×-auth escalation and the
  token is never invalidated → the next signal after the IP is fixed just succeeds. **No halt, no
  restart, no token invalidation.** So the proposed `token-watcher` restart-retry + a token-invalidation
  guard were **moot**; the only real gap was the operator not being told WHAT to fix. Added that:
  new `broker/auth_recovery.py` (`classify_broker_auth_error` IP-vs-token-vs-unknown; `get_public_ip`;
  `build_ip403_alert_body`) + `KillSwitch.record_api_failure()` now fires **ONE CRITICAL alert/hour** on
  an IP-allowlist 403 with the VM's **current public IP + exact Kite steps** (via the injected notifier →
  its CRITICAL path writes the sentinel → **email**, the live channel while Telegram is banned). Throttled
  1/hr (None sentinel = first always fires), runs outside the lock, best-effort; **FIX-185 preserved**
  (still never trips). **Verified live:** `get_public_ip()` → `161.118.187.249`; classification + alert
  body render correctly. Parity-safe (mode-agnostic). +13 tests. Commit 7126034. (See
  `kite_ip_allowlist_dependency` — the Kite-console IP update remains Rama's external step; this just
  makes the system TELL him the IP.)
- 2026-06-20 — Claude Code — **Kill switch: HEADLESS prior-day auto-clear + `KILL_AUTO_CLEARED` audit.**
  Rama's decision: the system ALWAYS starts headless — EVERY prior-day kill auto-clears at next-day
  startup regardless of type (scheduled, emergency, HARD_KILL, loss-limit, System Manager EOD); the
  safety net shifts from "block startup" → EOD report analysis (Task B, later). Investigation
  (`softkill_investigation_20jun`) found this already worked: **`clear_stale_state()`** (FIX-127,
  `main.py:1439`) already clears ALL prior-day kills and **`auto_clear_scheduled_kill()`** (FIX-154,
  `main.py:1444`) clears same-day *scheduled* kills when flat. This commit **solidifies** the guarantee
  (explicit docstring) and adds the audit bridge: new `KillSwitch._record_cleared_kill()` writes a
  `system_events` row (`event_type=KILL_AUTO_CLEARED`; details JSON: previous_state / reason /
  triggered_by / triggered_at / classification `scheduled|emergency` / cleared_via) on every auto-clear.
  Same-day kills still persist within the day (the `triggered_date >= today` guard is unchanged →
  loss-limit/HARD_KILL stay active intraday). The System Manager "SOFT_KILL for tomorrow" is dated its
  RUN time (`now_ist`, never post-dated) so it clears as prior-day. **Monday 22-Jun: the live kill
  (`circuit_breaker_force_close_15:15` from Fri) auto-clears at 08:30 — NO `resume.sh` needed** (proven
  against a DB copy: → INACTIVE + audit row, classification=scheduled). NB the morning manual-resume
  Rama hits is a *same-day* emergency re-trigger (Kite IP-403 `BrokerAuthError` → HALT exit 4), NOT the
  scheduled kill — a separate token/IP issue. Parity-safe (kill switch is mode-agnostic). +4 tests; 42
  kill_switch tests pass. Commit a60868f.
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
