# PATHS — Quick Reference (Trading System v2)
# Full map + audit: docs/SYSTEM_MAP.md  ·  Last updated: 2026-06-18

> ⚠️ Read `docs/SYSTEM_MAP.md` before any VM/system work. **Deploy ≠ restart.**

## VM (161.118.187.249, user `ubuntu`, IST)
| What | Path |
|---|---|
| Project root (running tree) | `/home/ubuntu/systems/trading-system/` |
| Python venv (shared) | `/home/ubuntu/systems/venv/bin/python` (3.12.3) |
| Main DB (v28) | `data_store/trading_system.db` |
| Analytics DB (ATTACHed) | `data_store/analytics.db` |
| Broker token | `data_store/session/zerodha_token.json` |
| Secrets | `.env` (root) + systemd drop-in (NOT in git) |
| Logs | `logs/system_YYYY-MM-DD.log`, `reconciler_*.log`, `trades_*.log`, `cron-*.log` |
| Master config | `config/system_config.yaml` |
| Accounts | `config/accounts.csv` (primary: LFL836) |
| Reports | `reports/{daily,daily_review,flow_trace,...}/` |
| Bare repo (deploy target) | `/home/ubuntu/trading-system.git/` (post-receive checks out tree) |
| Canonical cron | `deploy/cron/trading-system.cron` (live crontab DIVERGES — see SYSTEM_MAP) |

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
