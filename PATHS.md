# PATHS — Quick Reference (Trading System v2)
# Full map + audit: docs/SYSTEM_MAP.md  ·  Last updated: 2026-06-23

> ⚠️ Read `docs/SYSTEM_MAP.md` before any VM/system work. **Deploy ≠ restart.**

## VM (161.118.187.249, user `ubuntu`, IST)
| What | Path |
|---|---|
| Project root (running tree) | `/home/ubuntu/systems/trading-system/` |
| Python venv (shared) | `/home/ubuntu/systems/venv/bin/python` (3.12.3) |
| Main DB (v32) | `data_store/trading_system.db` |
| Analytics DB (ATTACHed) | `data_store/analytics.db` |
| Broker token | `data_store/session/zerodha_token.json` |
| Secrets | `.env` (root) + systemd drop-in (NOT in git) |
| Logs | `logs/system_YYYY-MM-DD.log`, `reconciler_*.log`, `trades_*.log`, `cron-*.log` |
| Master config | `config/system_config.yaml` |
| Cron source of truth | `config/cron_registry.yaml` (→ `core/cron_registry.py`; `officer:` block = Cron Officer settings) |
| Accounts | `config/accounts.csv` (primary: LFL836) |
| Reports | `reports/{daily,daily_review,flow_trace,system_manager,cron_officer,...}/` |
| Cron-job markers | `data_store/cron_marks/<job>.done` (exit-code markers the Officer reads) |
| Alert sentinels + retention | `data_store/critical_alert_*.flag`→`.delivered` (alert-watcher). `sentinel_retention` cron (02:05 daily) deletes `.delivered` >7d, **NEVER** `.flag`; no archive (email is the record). Tests must NOT use `sentinel_dir="data_store"` |
| Cron audit | `data_store/cron_audit/` (Phase-1 findings + daily `job_list_<date>.json` snapshots) |
| Bare repo (deploy target) | `/home/ubuntu/trading-system.git/` (post-receive checks out tree) |
| Canonical cron | `deploy/cron/trading-system.cron` = `generate(cron_registry.yaml)` via `scripts/generate_crontab.py` (ASCII+LF). live==canonical==generate (4-way sha256 `1469f905…`, 41 lines, 23-Jun); post-receive auto-installs on push |
| Agent CLIs (outside project) | `~/tools/antigravity/agy` (Antigravity/`agy` — drives `gemini_*.py` AI-ops crons) · `~/tools/gemini/` (Gemini CLI, node) · `~/tools/claude/` (Claude Code; 4×/day heartbeat → `cron.log`). ✅ heartbeat now `cd`s into `~/tools/claude/` → governed by `AGENTS.md` + `.claude/settings.json` (no .py/DB/systemctl; verified 23-Jun). Live-crontab only (not in canonical cron) — see SYSTEM_MAP |

## Run a command on the VM
```bash
cd /home/ubuntu/systems/trading-system && . .env && \
  PYTHONPATH=. /home/ubuntu/systems/venv/bin/python <script.py>
```
Raw DB reads: use `core.db_connect.connect` (sets up the v28 ATTACH).

## Services (`systemctl`)
`trading-system.service` (main, `main.py --mode live`) · `token-watcher.service` ·
`alert-watcher.service` · `trading-watchman.service` · `security-watcher.service` ·
`cron-watchdog.timer` (19:30 daily watch-the-watcher, ARMED 23-Jun)

## PC (developer machine)
| What | Path / value |
|---|---|
| Local repo | `D:\Projects\trading-system` |
| Deploy | `git push origin main` → bare repo hook → VM working tree (**no scp; no auto-restart**) |
| SSH alias | `trading-vm` (key `trading_vm_secure`, passwordless) |

## Activate deployed code on VM (manual)
```bash
ssh trading-vm 'sudo systemctl restart trading-system.service'
```

## Top-level packages
`core/` infra · `broker/` integration+polling · `capital/` risk/kill-switch ·
`orders/` order lifecycle · `signals/` ingestion · `screening/` scoring ·
`data/` market data · `alerts/` telegram · `scripts/` ops+gemini · `tests/` (305)

## Strategy control — 3-layer (Slice 2, 24-Jun)
| What | Location |
|---|---|
| Resolver (single source of truth) | `strategies/control.py::strategy_will_trade(strategy, *, trade_type, force_intraday_only)` → `Verdict(will_trade, reason, product)` |
| LAYER 1 master gate | `system_config.yaml` `trade_type: INTRADAY|DELIVERY|BOTH` (default INTRADAY) → `core/config_loader.py` SystemConfig + validator |
| LAYER 3 per-strategy switch | `config/strategies/*.yaml` `enabled: true` (all 15) → `strategies/schema.py` `enabled: bool = True` |
| LAYER 0 breaker (unchanged) | `force_intraday_only` (load-time intent→INTRADAY rewrite in `strategies/loader.py`) |
| Entry gate (2 sites) | `signals/signal_processor.py` `_process_one` + `continue_from_gate` → `_PipelineReject("STRATEGY_CONTROL")`; placement path UNCHANGED (gate only rejects) |
| Status table | `scripts/strategy_status.py` (build + html/telegram/plain) → folded into Cron Officer 09:20 briefing (`CronReport.strategy_status`); Type=true intent, Verdict=gate |
| Default state | trade_type INTRADAY + force_intraday_only true + all 15 enabled → 15 WILL TRADE / 0 WON'T (zero behaviour change). Delivery (CNC) parked → Slice 2.5 |

## Order-exit safety — circuit-band placeability gate (NOCIL fix, 23-Jun)
| What | Location |
|---|---|
| Placeability primitive + result | `orders/price_math.py` → `clamp_exit_into_band()` / `ClampResult` (margin `DEFAULT_CIRCUIT_MARGIN_PCT`) |
| SL-unplaceable exception | `core/exceptions.py` → `SLUnplaceableError` (→ emergency-close + hard_kill) |
| The **only** 3 clamp call sites | `orders/order_protocol_limit.py` — `place_exits` (SL+TGT), `place_tgt_only` (TGT) |
| Pre-fill reject + fast-disable flag | `screening/secondary_screener.py` · config `entry_gate.circuit_proximity_reject_enabled` (default true) |
| Never-clamp exceptions (test-enforced) | `orders/order_protocol_co.py` (CO-TGT), `orders/order_reconciler.py` (G5b SL) |
| Tests | `tests/unit/test_nocil_clamp_fix.py` (E.1–E.9), `tests/unit/test_price_math.py` |

Detail: `docs/SYSTEM_MAP.md` → "Circuit-band placeability gate".

**Tick-size snap (single chokepoint, fail-safe 23-Jun):** every broker submission is tick-snapped at `broker/zerodha_adapter.py` → `_snap_order_to_tick` / `_resolve_tick` (fallback `DEFAULT_TICK` 0.05 + throttled WARN on a missing `tick_size`; covers `place_order` **and** `modify_order`, parity). `orders/order_placer._round_to_tick` was **removed** — the adapter is the sole snap point. Tests: `tests/unit/test_tick_failsafe_snap.py`.

## Duplicate-exit safety — RAMCOIND fix (25-Jun): G5b can no longer over-sell
| What | Location |
|---|---|
| Root | G5b crash-recovery placed a 2nd SL outside the OCO via a TOCTOU race on the lagging local `orders` table → both filled on the stop-hit → −1 over-sell (RAMCOIND 25-Jun; also IRFC 17-Jun / NIACL 22-Jun) |
| **L2 keystone** invariant | `orders/order_reconciler.py` `_check_duplicate_exits` / `_dedupe_exit_leg` — exactly one live SL (LIMIT_TRIPLE only; CO excluded via `variety='co'`) + one live TGT per open trade; cancels the non-canonical (later-placed) extra, never drops to zero |
| **L1** G5b guard | `_g5b_crash_recovery_sl` → 10s settling window (`_entry_fill_age_seconds`) + authoritative broker/`_fill_map` check (`_already_has_live_sl`, `_G5B_SETTLING_WINDOW_SEC`) instead of the lagging table |
| **L3** over-sell / naked | `_check2_orphan_adoption` → `_detect_system_oversell` (CRITICAL + auto-flatten) · `_position_is_naked` (naked → WARNING, never silent; protected human → silent, FIX-182) |
| **L4** after-check | `orders/order_placer.py` `_verify_exits_placed` → flags `>1` live SL/TGT (`DUPLICATE_SL/TGT`) |
| Tests | `tests/unit/test_ramcoind_dup_exit_fix.py` · acceptance gate `tests/crash_test/test_ramcoind_oversell_prevented.py` (paper+live: position ends FLAT, never −1) |

No DB schema; parity (shared paper+live, no mode branch). Detail: `docs/SYSTEM_MAP.md` Changelog 2026-06-25 · memory `ramcoind_duplicate_sl_incident_25jun`.

## Delivery (CNC) — SLICE2.5-P1+P2 (25-Jun): durable GTT overnight protection (delivery_enabled=false)
| What | Location |
|---|---|
| Master lock (R2 split) | `system_config.yaml` `delivery_enabled` (default **false**). **P2 guard split:** gates ONLY the CNC **entry** (`zerodha_adapter.place_order`); protective GTT ops (`place_gtt`/`modify_gtt`/`delete_gtt`/`get_gtt(s)`/`get_holdings`) are NOT gated — protection survives disablement. Real CNC entry needs delivery_enabled=true AND force_intraday_only=false AND trade_type∈{DELIVERY,BOTH} |
| GTT placer | `orders/cnc_gtt.py` `CncGttPlacer` — OCO legs (SL limit = trigger − `capital.gtt_sl_limit_offset_pct` 3%; TGT limit = trigger − `sl_limit_offset_pct`), C8 validate, ONE GTT/trade. **P2:** persists to `gtt_state` (durable), `hydrate_from_store()` rebuilds the hot cache on boot, `forget()` for recreate |
| Durable state (v36) | `core/schema.sql` **TABLE 37 `gtt_state`** (gtt_id INTEGER PK, status ACTIVE→TRIGGERED→CLEANED/…, needs_review, FK→trades) = the one-per-trade source of truth (replaces P1's in-memory map). `core/state_store.py` DAO (insert/update/get_active/by_id/status/needs_review/verified + `mark_trade_closed_gtt`/`record_gtt_close_financials`) |
| Reconcile + GTT_EXIT | `orders/cnc_gtt_monitor.py` `CncGttMonitor.reconcile()` — re-verifies each GTT vs broker (get_gtts + holdings + positions), K6 ladder (healthy / **GTT_EXIT** finalise+capital-release / F6 re-protect / recreate / qty-mismatch CRITICAL-once+needs_review / orphan delete / >1-ACTIVE soft-kill) + 50-cap. Wired into `order_reconciler` (startup [4a] + 15-min in-hours [4b]); delivery trades EXCLUDED from the position/SL/exit checks |
| Adapter GTT/holdings I/O | `broker/zerodha_adapter.py` `place_gtt`/`modify_gtt`/`get_gtt`/`get_gtts`/`delete_gtt`/`get_holdings` — live=Kite, paper=in-memory store (`_paper_gtts`/`_paper_holdings`, `seed_paper_holding`). Paper gtt ids are **NUMERIC** (`_PAPER_GTT_ID_BASE`) since gtt_state.gtt_id is INTEGER PK |
| Limit math | `orders/price_math.py` `calc_gtt_limit_price` (dedicated; not the intraday 0.5% path) |
| Centralized gate | `orders/full_entry_engine.py` `place_deferred_exits` (intent==DELIVERY → GTT, `ExitLegsResult.is_gtt`); `order_placer._finalize_cnc_gtt` (logs gtt_id; no day legs). INTRADAY unchanged |
| Tests / T2 | `tests/unit/test_cnc_gtt_slice25_p1.py` · `test_cnc_gtt_slice25_p2.py` · `test_cnc_gtt_monitor.py` · `test_cnc_gtt_step4_wiring.py` · `scripts/t2_cnc_gtt_realtest.py` (market-hours real-API/TPIN proof — the blocker before enabling delivery) |

P2 = durability + safety (schema v36 `gtt_state`, reconcile, GTT_EXIT, 15-min monitor); delivery_enabled stays **false** (no activation). **DEPLOYED to main `bad0aad` 25-Jun ~22:23 (one-time authorized; rule restored); schema v36 applies at the Fri 08:15 boot.** Detail: SYSTEM_MAP Changelog 2026-06-25 · memory `slice25_p2_gtt_durability_25jun`.

## SATS — static analysis (PC-only, manual; `sats/` is git-ignored, never deploys)
| What | Path |
|---|---|
| SATS root | `D:\Projects\trading-system\sats\` |
| Bandit venv exe | `sats\bandit-env\Scripts\bandit.exe` (1.9.4) |
| Semgrep venv exe | `sats\semgrep-env\Scripts\semgrep.exe` (1.167.0) |
| Scan scripts | `sats\scripts\scan_bandit.bat`, `sats\scripts\scan_semgrep.bat` |
| Scan reports | `sats\reports\{bandit,semgrep}_<yyyyMMdd_HHmmss>.txt` |
| Semgrep baseline | `sats\semgrep_baseline.txt` (pinned `65439ff`; only NEW findings reported) |

On-demand only (no hooks/automation) — double-click a `.bat`. Both scan the repo root,
exclude `venv,sats,.git`, write a timestamped txt report **and** echo it to the console.
Both `.bat`s set `PYTHONUTF8=1` (else Semgrep/Bandit crash writing the report on Windows cp1252).
Semgrep rulesets `p/python` + `p/security-audit` (login-free; first run downloads, cached after);
`scan_semgrep.bat` honours `semgrep_baseline.txt` (delete it for a full scan).
Bandit reports all severities — add `-ll` for medium+. **Do not** touch the two venvs.

## Pre-flight (`scripts/preflight/`) — daily pre-market readiness, ALERT-ONLY
3 phases via cron Mon-Fri: **A 08:30** (infra) · **B 09:14** (engine readiness) · **C 09:15→09:20**
(signal warmup). Sentinel `data_store/preflight/today.json` · log `logs/preflight.log` · markers
`data_store/cron_marks/preflight_phase_{a,b,c}.done`. Run: `python -m scripts.preflight.orchestrator
--phase A|B|C [--dry-run --as-of-date YYYY-MM-DD]`. Schema v33 (preflight_runs / preflight_check_results
/ preflight_autofix_log). Cron Officer briefing embeds its sentinel banner. (Replaced premarket_healthcheck.)

## Config Sanity Auditor — BUILD 2 (25-Jun), the Config Authority enforcement layer
| What | Location |
|---|---|
| Rule engine (single source) | `core/config_auditor.py` → `audit()` / `audit_system_config()` (startup subset `ACG`) / `audit_app_config()` (full A–G) → `ConfigAuditReport` (`AuditFinding`: PASS/INFO/WARN/BLOCK) |
| Check groups | **A** contradictions (BLOCK, incl. #10) · **B** single-source regression guards · **C** capital-relative sanity (conc < pos-cap ladder) · **D** active-override listing · **E** launch-phase reminders · **F** stale-default guard · **G** cross-field |
| STARTUP caller (fail-fast BLOCK) | `core/config_loader.py` `SystemConfig._cross_field_sanity_checks` → `raise_if_blocked()` (ValidationError, "CONTRADICTORY CONFIG") |
| PRE-FLIGHT caller (Phase A → 09:20) | `scripts/preflight/checks/config_sanity.py` (7 rows, group "Config Sanity"; one memoised audit run) |
| Tests | `tests/unit/test_config_auditor.py` (37) |

Detail: `docs/SYSTEM_MAP.md` Changelog 2026-06-25 · memory `build2_config_sanity_auditor_25jun`. No DB schema; parity (no mode branch).

## Self-maintaining cron (registry → canonical → auto-install, ARMED 23-Jun)
| What | Path / fact |
|---|---|
| Source of truth | `config/cron_registry.yaml` (executable) |
| Generator | `scripts/generate_crontab.py --generate [--out FILE]` → `deploy/cron/trading-system.cron` (ASCII+LF, deterministic). Also `--gate` (zero-drops proof), `--selftest` (byte round-trip), `--bootstrap`, `--check` |
| Equality | live `crontab -l` == canonical == `generate(registry)` (4-way sha256 `1469f905…`, 41 lines) |
| **post-receive** (ARMED) | `~/trading-system.git/hooks/post-receive` (from `deploy/hooks/post-receive`) — auto-installs the crontab on every push **iff** `generate==canonical`, else WARN+skip |
| **pre-receive** (DEFERRED) | `deploy/hooks/pre-receive` — **NOT installed** (by choice 23-Jun); would hard-reject a push whose canonical != generate. Arm: install + inject `CRON_GUARD_DRYRUN=1` (dry-run) → clear to enforce. Break-glass: `rm` the hook |
| pre-commit (optional) | `deploy/hooks/pre-commit` — local clones only |
| **cron-watchdog** (ARMED) | `/etc/systemd/system/cron-watchdog.{service,timer}` — systemd (NOT cron); **19:30 IST daily**; asserts `cron_officer_eod`+`check_cron_drift` heartbeated → CRITICAL sentinel (cron-independent) if not. 1st run 24-Jun |

Detail: `docs/SYSTEM_MAP.md` (Deploy + Cron Jobs + Systemd) · memory `cron_framework_armed_23jun`.
