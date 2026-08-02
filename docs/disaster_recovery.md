# Disaster Recovery Runbook

**Last Updated:** 2026-06-23 (§5 recoverable set: self-maintaining cron framework — registry/canonical/hooks/watchdog)  
**Owner:** Trading System Operations

## Overview

This document covers recovery procedures for critical system failures.
The SQLite database is backed up nightly at 01:00 IST with 7-day retention.

---

## 1. Database Restore Procedure

### When to Use
- Database corruption detected
- Accidental data deletion
- Need to rollback to previous state

### Prerequisites
- SSH access to VM (161.118.187.249)
- ubuntu user credentials
- Trading system service stopped

### Steps

```bash
# 1. SSH to VM
ssh trading-vm

# 2. Stop the trading system
sudo systemctl stop trading-system

# 3. List available backups (most recent first)
ls -lt ~/systems/trading-system/data_store/backups/

# 4. Note the current (corrupted) DB size for comparison
ls -l ~/systems/trading-system/data_store/trading_system.db

# 5. Backup the corrupted file (optional, for forensics)
cp ~/systems/trading-system/data_store/trading_system.db \
   ~/systems/trading-system/data_store/corrupted_$(date +%Y%m%d_%H%M%S).db

# 6. Remove the corrupted DB and WAL files
rm ~/systems/trading-system/data_store/trading_system.db
rm -f ~/systems/trading-system/data_store/trading_system.db-wal
rm -f ~/systems/trading-system/data_store/trading_system.db-shm

# 7. Restore from backup (pick appropriate date)
cp ~/systems/trading-system/data_store/backups/trading_system-2026-06-01.db \
   ~/systems/trading-system/data_store/trading_system.db

# 8. Verify restored DB integrity
sqlite3 ~/systems/trading-system/data_store/trading_system.db "PRAGMA integrity_check;"
# Expected output: "ok"

# 9. Verify schema version matches code
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
    "SELECT value FROM schema_meta WHERE key='schema_version';"
# Should match EXPECTED_SCHEMA_VERSION in core/state_store.py (currently 23)

# 10. Count key tables to verify data
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
    "SELECT 'trades', COUNT(*) FROM trades UNION ALL \
     SELECT 'orders', COUNT(*) FROM orders UNION ALL \
     SELECT 'signals', COUNT(*) FROM signals;"

# 11. Restart the trading system
sudo systemctl start trading-system

# 12. Verify startup
sudo journalctl -u trading-system -n 50 --no-pager
```

### Post-Restore Verification
1. Check system logs for startup errors
2. Verify no schema version mismatch errors
3. Confirm fund_manager loaded correct capital state
4. Test Telegram alerts are working

---

## 2. Fresh Database Creation

### When to Use
- No backup available
- Starting completely fresh
- Migration to new schema

### Steps

```bash
# 1. Stop trading system
sudo systemctl stop trading-system

# 2. Remove old DB
rm ~/systems/trading-system/data_store/trading_system.db
rm -f ~/systems/trading-system/data_store/trading_system.db-wal
rm -f ~/systems/trading-system/data_store/trading_system.db-shm

# 3. Start trading system - it will create fresh DB
sudo systemctl start trading-system

# 4. The system will start with:
#    - Empty trades/orders/signals tables
#    - Fresh session row
#    - fund_manager will need capital re-initialization
```

**Important:** After fresh DB creation:
- Capital must be re-initialized (system will use configured starting_capital)
- All historical data is lost
- Run --interactive startup to verify login works

---

## 3. Token Refresh Failure

### Symptoms
- System won't start
- "Token expired" errors in logs
- `data_store/session/zerodha_token.json` is stale

### Resolution

```bash
# 1. Delete stale token
rm ~/systems/trading-system/data_store/session/zerodha_token.json

# 2. Option A: Run auto_refresh_token (uses TOTP)
cd ~/systems/trading-system
PYTHONPATH=. ~/systems/venv/bin/python scripts/auto_refresh_token.py

# 3. Option B: Run interactive login
cd ~/systems/trading-system
PYTHONPATH=. ~/systems/venv/bin/python main.py --interactive
```

---

## 4. Service Won't Start

### Diagnostic Steps

```bash
# 1. Check systemd status
sudo systemctl status trading-system

# 2. Check recent logs
sudo journalctl -u trading-system -n 100 --no-pager

# 3. Common issues:
# - Schema version mismatch: check EXPECTED_SCHEMA_VERSION
# - Port already in use: `sudo netstat -tlnp | grep 5000`
# - Kill switch active: check kill_switch_state table
# - Missing token: check data_store/session/zerodha_token.json
```

### Force Clear Kill Switch

A **previous-day** kill auto-clears at the next 08:15 startup — do nothing. Only a
**same-day** kill needs this.

```bash
# LIVE / VM — stops the unit, clears the kill, restarts it (refused clear => no start)
sudo bash deploy/resume.sh            # SOFT_KILL
sudo bash deploy/resume.sh --force    # also clears HARD_KILL (confirm root cause first)

# PAPER / PC (no systemd)
python scripts/clear_kill_switch.py --dry-run    # report only
python scripts/clear_kill_switch.py [--force]
```

> ⛔ ~~**SUPERSEDED 02-Aug-2026 (ledger #8 / IA-XDOCS-01)** — this previously read
> `sqlite3 ~/systems/trading-system/data_store/trading_system.db "UPDATE
> kill_switch_state SET state='INACTIVE' WHERE id=1;"`.~~ A raw `UPDATE` writes no
> `system_events` audit trail, leaves `triggered_at`/`triggered_by` stale, **bypasses the
> HARD_KILL `--force` gate** (an emergency kill re-trips if its cause is unfixed), and has
> **no effect on a running service** — `is_active()` reads in-memory state, not the DB.
> ⛔ Restarting while a kill is still persisted returns **exit 4**, and
> `RestartPreventExitStatus=3 4` means systemd will **not** bring the service back.

---

## 5. Complete System Rebuild

If VM is lost and needs complete rebuild:

1. Provision new VM with Ubuntu 22.04
2. Install dependencies:
   ```bash
   sudo apt update && sudo apt install python3.11 python3.11-venv sqlite3
   ```
3. Clone repository
4. Create venv: `python3.11 -m venv ~/systems/venv`
5. Install packages: `pip install -r requirements.txt`
6. Restore DB from off-site backup (if available)
7. Configure systemd service
8. **Cron — self-maintaining framework (recoverable set; all git-tracked under `deploy/` + `config/`):**
   ```bash
   cd ~/systems/trading-system
   # a) regenerate the canonical from the source-of-truth registry, then install it:
   PYTHONPATH=. ~/systems/venv/bin/python scripts/generate_crontab.py --generate --out deploy/cron/trading-system.cron
   crontab deploy/cron/trading-system.cron            # live == generate(config/cron_registry.yaml)
   crontab -l > /tmp/live.cron
   PYTHONPATH=. ~/systems/venv/bin/python scripts/generate_crontab.py --gate --crontab /tmp/live.cron   # expect GATE PASS (zero drops)
   # b) arm the post-receive auto-install hook (regenerates + installs the crontab on every push):
   cp deploy/hooks/post-receive ~/trading-system.git/hooks/post-receive && chmod +x ~/trading-system.git/hooks/post-receive
   # c) DEFERRED (optional): the pre-receive equality guard is intentionally NOT installed — arm later
   #    via dry-run (CRON_GUARD_DRYRUN=1 in the installed copy) → enforce. Break-glass: rm the hook.
   # d) install the cron-watchdog (systemd, NOT cron — watch-the-watcher, fires 19:30 IST):
   sudo cp deploy/systemd/cron-watchdog.service deploy/systemd/cron-watchdog.timer /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now cron-watchdog.timer
   ```
   Source of truth = `config/cron_registry.yaml`; the canonical, the three `deploy/hooks/*`, and the
   `deploy/systemd/cron-watchdog.*` units are all git-tracked and re-installable. NB the 4 `~/tools/claude`
   heartbeat lines live in the **live crontab only** (not the canonical) — re-add them if you want them
   permanent (see SYSTEM_MAP → Agent CLIs). Detail: memory `cron_framework_armed_23jun`.
9. Restore agent-tooling guardrails (keeps the Claude heartbeat cron governed — no `.py`/DB/systemctl):
   ```bash
   mkdir -p ~/tools/claude/.claude
   cp deploy/tools/claude/AGENTS.md             ~/tools/claude/AGENTS.md
   cp deploy/tools/claude/.claude/settings.json ~/tools/claude/.claude/settings.json
   ```
   Also restore `~/tools/antigravity/agy` (Antigravity CLI) + `~/tools/gemini/` for the AI-ops (Gemini) crons.
10. Set environment variables (ZERODHA_*, TELEGRAM_*)
11. Test startup: `python main.py --interactive`

---

## 6. Backup Verification Drill

**Run quarterly to ensure backups are usable.**

```bash
# 1. Create test directory
mkdir -p /tmp/dr-test

# 2. Copy latest backup
cp ~/systems/trading-system/data_store/backups/trading_system-$(date +%Y-%m-%d).db \
   /tmp/dr-test/test.db

# 3. Run integrity check
sqlite3 /tmp/dr-test/test.db "PRAGMA integrity_check;"

# 4. Verify key tables have data
sqlite3 /tmp/dr-test/test.db \
    "SELECT 'trades', COUNT(*) FROM trades WHERE DATE(updated_at) >= date('now', '-7 days');"

# 5. Verify schema version
sqlite3 /tmp/dr-test/test.db \
    "SELECT value FROM schema_meta WHERE key='schema_version';"

# 6. Cleanup
rm -rf /tmp/dr-test
```

**Document results:** Date, backup file, integrity status, row counts

---

## Contact

- **Primary:** [Your Name] 
- **Telegram Channel:** Configured in TELEGRAM_CHANNEL_PRIMARY
