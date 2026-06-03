# Incident Response Guide

Procedures for handling trading system incidents during market hours.

## Severity Levels

| Level | Description | Response Time |
|-------|-------------|--------------|
| P0 | Capital at risk, naked positions, data loss | Immediate |
| P1 | Trading halted, no new orders | Within 5 minutes |
| P2 | Degraded (missed signals, slow fills) | Within 30 minutes |
| P3 | Non-critical (alert noise, cosmetic) | End of day |

## P0: Kill Switch Scenarios

### SOFT_KILL Triggered

**What happens:** No new trades accepted. Existing positions continue to be
managed (SL/TGT still active). EOD squareoff still fires.

**Common causes:**
- 3+ consecutive broker API failures
- Daily loss limit exceeded (5% of capital)
- Capital drift beyond soft_kill threshold

**Steps:**
1. Check Telegram for trigger reason
2. SSH to VM: `ssh trading-vm`
3. Check logs: `sudo journalctl -u trading-system -n 30 --no-pager | grep -i kill`
4. Check kill switch state:
```bash
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
  "SELECT state, reason, triggered_at FROM kill_switch_state ORDER BY rowid DESC LIMIT 1;"
```
5. If API failure → check Zerodha status page, verify token
6. If daily loss → verify P&L is real (not paper drift artifact)
7. To resume (if root cause resolved):
```bash
# System auto-resumes after EOD squareoff if auto_resume_kill_switch=true
# Manual resume: restart the service
sudo systemctl restart trading-system
```

### HARD_KILL Triggered

**What happens:** All open positions are being force-closed. System halts
completely after closure.

**Common causes:**
- Capital drift beyond hard_kill threshold (Rs 2500)
- Invariant assertion failure (capital accounting error)
- Manual trigger

**Steps:**
1. **DO NOT** restart immediately
2. Check all positions closed in Zerodha
3. Compare system trades vs Zerodha order book:
```bash
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
  "SELECT symbol, direction, qty_filled, status FROM trades
   WHERE status IN ('OPEN', 'PENDING_FILL');"
```
4. Verify capital state:
```bash
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
  "SELECT entry_type, amount, balance_after FROM fm_ledger
   ORDER BY id DESC LIMIT 10;"
```
5. Fix root cause before restarting
6. Restart with: `sudo systemctl start trading-system`

## P0: Naked Position Detected

A position exists at the broker without a corresponding SL order.

**Steps:**
1. Check Telegram for the symbol and direction
2. Open Zerodha web/app immediately
3. Place a manual SL order for the naked position
4. Check system logs for why SL wasn't placed:
```bash
grep "naked\|orphan" ~/systems/trading-system/logs/system_$(date +%Y-%m-%d).log
```
5. Common causes:
   - SL order rejected by broker (margin insufficient)
   - API timeout during SL placement
   - Order state machine inconsistency

## P0: Capital Mismatch

System capital tracking disagrees with broker by more than tolerance.

**Steps:**
1. Get system state:
```bash
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
  "SELECT entry_type, SUM(amount) FROM fm_ledger GROUP BY entry_type;"
```
2. Get broker state: Check Zerodha "Funds" page
3. Identify the gap:
   - Unreleased reservations? Check for PENDING_FILL trades that timed out
   - Missing P&L entries? Check for CLOSED trades without fm_ledger PNL entry
   - Double release? Check fm_ledger for duplicate RELEASE entries
4. If gap is small (< Rs 50): Monitor, it may self-correct at EOD
5. If gap is large: Stop the system, reconcile manually, restart

## P1: Broker API Down

**Steps:**
1. Check Zerodha status: https://status.zerodha.com
2. If planned maintenance: System will auto-SOFT_KILL after 3 failures
3. If unplanned:
   - Wait 5 minutes (transient failures recover)
   - Check token hasn't expired
   - If persistent: Stop the system, no trading until API recovers
4. After recovery: Restart system, it will detect WARM scenario and resume

## P1: Token Expiry Mid-Session

Zerodha tokens expire at 06:00 IST next day, but can occasionally fail
mid-session (revoked, API key issue).

**Symptoms:**
- Orders returning 403 or "Token expired"
- TokenMonitor triggers alert

**Steps:**
1. On Windows PC: Run `scripts\zerodha_morning.bat`
2. Copy to VM: `scripts\copy_token_to_vm.bat`
3. Token monitor will detect new token and resume
4. If auto-token available: `PYTHONPATH=. python3 scripts/auto_refresh_token.py`

## P1: Database Corruption

**Symptoms:**
- `SQLITE_CORRUPT` errors in logs
- Startup check #3 fails

**Steps:**
1. Stop the system: `sudo systemctl stop trading-system`
2. Check integrity:
```bash
sqlite3 ~/systems/trading-system/data_store/trading_system.db "PRAGMA integrity_check;"
```
3. If corrupted, restore from backup:
```bash
cp data_store/backups/trading_system-$(date -d yesterday +%Y-%m-%d).db \
   data_store/trading_system.db
```
4. Run backup restore drill to verify:
```bash
PYTHONPATH=. python3 scripts/backup_restore_drill.py
```
5. Restart: `sudo systemctl start trading-system`

## P2: Missed Signals

**Symptoms:** Chartink shows scanner triggered but no Telegram alert.

**Steps:**
1. Check webhook audit:
```bash
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
  "SELECT scanner_name, response_code, created_at FROM webhook_audit
   ORDER BY created_at DESC LIMIT 10;"
```
2. If 503 responses: Queue was full. Check if system was overloaded.
3. If 403 responses: Kill switch was active or outside entry window.
4. If no audit rows: Webhook never reached the system.
   - Check ngrok/tunnel status
   - Check Chartink webhook URL configuration
5. If 200 but no trade: Signal was screened out. Check screener_results:
```bash
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
  "SELECT * FROM screener_results WHERE date(created_at) = date('now')
   ORDER BY created_at DESC LIMIT 5;"
```

## Emergency Stop

If something is deeply wrong and you need to stop everything immediately:

```bash
# 1. Stop the service
sudo systemctl stop trading-system

# 2. Check for open positions in Zerodha
# Use Zerodha web/app to manually close ALL positions

# 3. Verify no positions remain
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
  "SELECT count(*) FROM trades WHERE status='OPEN';"

# 4. Document what happened
# Create incident file: docs/incidents/YYYY-MM-DD_description.md
```

## Post-Incident

1. Document root cause and timeline
2. Add test if the scenario was previously untested
3. Consider adding a new startup check if applicable
4. Update this runbook if the response procedure was unclear
