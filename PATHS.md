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
| Cron audit | `data_store/cron_audit/` (Phase-1 findings + daily `job_list_<date>.json` snapshots) |
| Bare repo (deploy target) | `/home/ubuntu/trading-system.git/` (post-receive checks out tree) |
| Canonical cron | `deploy/cron/trading-system.cron` (live crontab == file since 21-Jun reinstall; `diff`=0) |
| Agent CLIs (outside project) | `~/tools/antigravity/agy` (Antigravity/`agy` — drives `gemini_*.py` AI-ops crons) · `~/tools/gemini/` (Gemini CLI, node) · `~/tools/claude/` (Claude Code; 4×/day heartbeat → `cron.log`). ✅ heartbeat now `cd`s into `~/tools/claude/` → governed by `AGENTS.md` + `.claude/settings.json` (no .py/DB/systemctl; verified 23-Jun). Live-crontab only (not in canonical cron) — see SYSTEM_MAP |

## Run a command on the VM
```bash
cd /home/ubuntu/systems/trading-system && . .env && \
  PYTHONPATH=. /home/ubuntu/systems/venv/bin/python <script.py>
```
Raw DB reads: use `core.db_connect.connect` (sets up the v28 ATTACH).

## Services (`systemctl`)
`trading-system.service` (main, `main.py --mode live`) · `token-watcher.service` ·
`alert-watcher.service` · `trading-watchman.service`

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
