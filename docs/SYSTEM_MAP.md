# SYSTEM MAP — Trading System v2
# Last updated: 2026-06-18 by VS Code Claude (Claude Code)
# ⚠️ READ THIS BEFORE TOUCHING ANYTHING ⚠️

This is the single authoritative path/ops reference. It complements (does not
replace) the design/runbook docs in `docs/` — see "Related Docs" at the bottom.
For a one-screen quick reference, see [`/PATHS.md`](../PATHS.md).

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
| `symbol_aliases.yaml` | Symbol normalization | instrument resolution |
| `accounts.csv` | Account registry (LFL836 primary, paper_capital, env-var names) | `core/account_registry.py` |
| `accounts_multi_example.csv` | Template/example (NOT loaded) | — |
| `instruments.csv` | Instrument master (lot sizes, tokens) | `core/instrument_cache.py` |
| `config/reference_data/` | NSE reference data | reference lookups |
| `config/strategies/` | Per-strategy YAML configs | `strategies/loader.py` |
| `.env` (root, 0600-ish, NOT in git) | Secrets: ZERODHA_*, GEMINI_API_KEY, WEBHOOK_SECRET, etc. | systemd `EnvironmentFile` + cron `. .env` |
| `.env.example` | Template for `.env` | — |

> Telegram bot token / chat IDs are also injected via the systemd drop-in
> (`/etc/systemd/system/trading-system.service.d/*.conf`). **Secrets live in `.env` and the
> drop-in only — never commit them.**

### Python Modules (role per package)
| Package | Role | Key modules |
|---|---|---|
| `core/` | Infra: config, DB, events, time, IDs | config_loader, state_store, db_connect, events, logger, time_authority, market_windows, instrument_cache, account_registry, migrations, constants, schema.sql |
| `broker/` | Broker integration + polling | zerodha_adapter, angelone_adapter, order_monitor (2s fill poll), order_state_machine, rate_limiter, cost_calculator, slippage_engine, product_resolver, token_monitor, clock_skew_probe |
| `capital/` | Capital, risk, kill-switch | fund_manager, position_sizer, risk_engine, kill_switch, drift_handler, invariant, performance_allocator, shadow_engine, strategy_governor |
| `orders/` | Order lifecycle | order_placer, order_reconciler (15s), order_manager, eod_squareoff, smart_tgt_manager, breakeven_manager, sl_breach_monitor, entry_engine, full_entry_engine, order_protocol_co, order_protocol_limit, price_math, shadow_tracker |
| `signals/` | Ingestion | webhook_receiver, signal_processor |
| `screening/` | Signal screening/scoring | entry_gate, quality_scorer, secondary_screener, step_executor |
| `data/` | Market data | live_feed (WS ticks), candle_store |
| `alerts/` | Alerting | telegram_notifier, critical (sentinels) |
| `strategies/` | Strategy config | loader, schema |
| `utils/` | Utilities/preflight | startup_checks, holiday_guard, instance_lock, cron_heartbeat |
| `reports/` | Reporting | daily_report, daily_review, style_constants |
| `scripts/` | 36 ops/cron scripts | incl. 6 `gemini_*.py` (AI ops), auto_refresh_token, premarket_healthcheck, reconcile_positions/pnl, eod_cleanup/verify, etc. |
| `tests/` | 305 test files | unit/, integration/, crash_test/ |
| `main.py` (root) | App entrypoint | launched as `main.py --mode live` |

### Database  (`data_store/`)
- `trading_system.db` (~49 MB) — **MAIN** DB (schema **v28**). trades, orders, signals, fm_ledger, etc.
- `analytics.db` (~44 KB) — analytics split (v28); **ATTACHed** to the main DB at runtime.
- **Raw sqlite access MUST use `core.db_connect.connect`** (it sets up the ATTACH); plain `sqlite3`
  works only for read-only SELECTs against the main file.
- `data_store/session/zerodha_token.json` — broker session token (wiped 05:00 daily by cron).
- `data_store/backups/` — nightly `.backup` snapshots (7-day retention).
- `data_store/candles/` — candle artifacts.
- `data_store/critical_alert_*.flag` — CRITICAL sentinels written by `alerts/critical.py`,
  consumed by `alert-watcher.service`. (Currently accumulating — see Known Issues.)

### Logs  (`logs/`)
Daily-dated files: `system_YYYY-MM-DD.log`, `debug_*.log`, `reconciler_*.log`, `trades_*.log`,
plus per-cron `cron-*.log` / `*.log`. Plain `FileHandler` (not Rotating); a cron deletes `*.log`
older than 30 days (00:00). `failed_alerts.log` = Telegram delivery-failure JSON-lines.

### Reports & Outputs  (`reports/`)
`reports/daily/`, `reports/daily_review/`, `reports/flow_trace/`, `reports/log_review/`,
`reports/watchman/`, `reports/weekly_patterns/`, `reports/output/`, `reports/crash_test/`.

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

## Cron Jobs  (live `crontab -l`, ~28 jobs; canonical copy: `deploy/cron/trading-system.cron`)
All market jobs run `cd … && . .env && PYTHONPATH=. venv/bin/python <script> >> logs/<log>`.

| Time (IST) | Days | Script | Purpose |
|---|---|---|---|
| 00:00 | daily | `find logs -mtime +30 -delete` | log cleanup |
| 01:00 | daily | sqlite3 `.backup` trading_system.db | nightly DB backup |
| 02:00 | daily | `find backups -mtime +7 -delete` | backup retention |
| 05:00 | daily | `rm session/zerodha_token.json` | force fresh login |
| 08:00 | Mon-Fri | `auto_refresh_token.py` | TOTP token refresh |
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
| 18:00 | Mon-Fri | `check_cron_drift.py` | cron heartbeat drift |
| 03:00 | 1st of month | `backup_restore_drill.py --quiet` | restore drill |
| hourly | every | `disk_monitor.py` | disk space |

> ⚠️ The **live crontab diverges** from `deploy/cron/trading-system.cron` (the committed canonical
> copy). See Known Issues — reconcile before relying on either as truth.

## Systemd Services  (`/etc/systemd/system/`)
| Service | ExecStart | Purpose | Notes |
|---|---|---|---|
| `trading-system.service` | `venv/bin/python main.py --mode live` | Main app (live mode) | `Restart=on-failure`, `RestartSec=10`, `RestartPreventExitStatus=3`; `EnvironmentFile=.env` + drop-in (Telegram secrets). **Currently in auto-restart/crash-loop** — see Issues. |
| `token-watcher.service` | `bash deploy/token_watcher.sh` | Auto-start app on fresh token | active |
| `alert-watcher.service` | `python scripts/alert_watcher.py` | Consume CRITICAL sentinel flags | sentinel monitor |
| `trading-watchman.service` | (gemini watchman) | AI log monitor during market hours | `Wants=` by trading-system |

Drop-in dir: `trading-system.service.d/` (holds Telegram env vars — secrets).

---

## Known Duplicates / Issues  (audit 2026-06-18)
1. **Live crontab ≠ `deploy/cron/trading-system.cron`** — e.g. live runs `refresh_instruments`
   09:00 Mon-Fri; committed copy has it 18:00 Sun, plus the committed copy has analytics.db
   backup (01:05) and `db_retention.py` (02:30) jobs that the live crontab does **not** show.
   → Reconcile and re-sync the canonical file.
2. **`trading-system.service` crash-loop** — `activating (auto-restart)`. Root cause: Kite IP
   allowlist / place_order 403 (see mempalace `kite_ip_allowlist_dependency`, FIX-185). Operational.
3. **~70 stale `critical_alert_*.flag` sentinels** in `data_store/` (13–18 Jun, many from the
   crash-loop). `alert-watcher.service` should consume them; they are piling up → review.
4. **Two git remotes** on PC (`origin` + `vm`) point to the same bare repo → redundant.

## PENDING CLEANUP — awaiting Rama's approval (do NOT delete without sign-off)
| Path | Why | Safe to remove? |
|---|---|---|
| `/home/ubuntu/systems/trading-system/trading.db` | **0-byte dead DB** at root; canonical DBs are in `data_store/` | Yes (verify 0 bytes first) |
| `data_store/critical_alert_*.flag` (consumed ones) | Stale sentinels from crash-loop | Only after alert-watcher confirms processed |
| root `CRON_FIX_2026_05_18.md`, `FIXES_DAILY_REPORT_2026_05_18.md` | Dated one-off notes at repo root (belong in `docs/` or archive) | Likely (review content) |
| root `deploy_audit_fixes.ps1` | One-off PowerShell deploy helper | Review (may be superseded by git-push deploy) |
| Duplicate git remote `vm` (PC) | Same target as `origin` | Yes (`git remote remove vm`) |

---

## Related Docs (do not duplicate these — cross-reference)
- `docs/01_system_architecture.md` — architecture
- `docs/03_daily_operations_runbook.md` / `docs/RUNBOOK.md` — daily ops
- `docs/04_db_schema_reference.md` — DB schema (v28)
- `docs/05_incident_response.md`, `docs/disaster_recovery.md` — incidents
- `docs/06_deployment_guide.md` — deployment detail

## Changelog
- 2026-06-18 — VS Code Claude — Initial creation. Full VM+PC audit (structure, configs, modules,
  DB, logs, ~28 cron jobs, 4 systemd services, tools, deploy mechanism). Flagged: root `trading.db`
  (0-byte dead), crontab divergence, sentinel accumulation, duplicate git remote, crash-loop.
