# First Day of Live Trading Runbook

**Version:** 1.0  
**Last Updated:** 2026-06-03  
**System:** Trading System v2 (Rs 25K micro capital)

---

## Pre-Day Checklist (Sunday evening before Monday live)

- [ ] Backup latest paper trading DB (`sqlite3 ... ".backup ..."`)
- [ ] Confirm all 7 TEMP config values reverted to production values:
  - `capital.daily_loss_limit`: 100000 -> 1250 (5% of Rs 25K)
  - `risk.max_consecutive_losses`: 20 -> 4
  - `risk.daily_loss_limit_pct`: 1.00 -> 0.05
  - `webhook.require_hmac`: false -> keep false (Chartink `?token=` auth)
  - `order_reconciler.capital_drift_tolerance`: 100000 -> 50
  - `drift_handler thresholds`: 100K/200K/500K -> 250/1000/2500
  - `gap_fade_long.min_score`: 30 -> 60 (or remove TEMP)
- [ ] Run premarket healthcheck dry-run, all OK
- [ ] Telegram test message sent and received
- [ ] Gemini watchman service enabled (`sudo systemctl enable --now gemini-watchman`)
- [ ] Verify crontab matches `deploy/cron/trading-system.cron`
- [ ] Check disk space (`df -h`) -- at least 2 GB free
- [ ] Verify `.env` has all REQUIRED_SECRETS populated

---

## Day 1 Morning (08:00-09:14 IST)

- [ ] **08:00** -- Run `zerodha_morning.bat` on PC, token generated
- [ ] **08:05** -- Copy token to VM: `scripts/copy_token_to_vm.bat`
- [ ] **08:30** -- Healthcheck cron fires, check Telegram for all-OK message
- [ ] **08:45** -- Verify token on VM: `cat data_store/session/zerodha_token.json | python -m json.tool`
- [ ] **09:00** -- Manual check: `sudo systemctl status trading-system` (must be `active (running)`)
- [ ] **09:00** -- Verify: `sudo journalctl -u trading-system -n 20 --no-pager` (no errors)
- [ ] **09:10** -- Open watchman tail in 2nd SSH window:
  ```
  tail -f ~/systems/trading-system/reports/watchman/watchman_$(date +%Y-%m-%d).md
  ```
- [ ] **09:14** -- Final mental checklist: "Capital is Rs 25K. Max daily loss Rs 1,250. Max 4 consecutive losses. No FOMO."

---

## During Market (09:15-15:30)

### First 30 Minutes (09:15-09:45) -- FULL ATTENTION
- [ ] Stay at PC, watch every signal in Telegram
- [ ] On first signal: verify it appears in system logs within 2 seconds
- [ ] On first trade: verify Zerodha terminal shows matching order
- [ ] Confirm SL and TGT orders visible in Zerodha order book

### Rest of Day (09:45-15:30) -- REGULAR MONITORING
- [ ] Check Telegram every 15 minutes minimum
- [ ] On ANY `CRITICAL` Telegram alert: stop everything, investigate before next signal
- [ ] On first trade close: manually verify P&L matches Zerodha terminal
- [ ] Spot-check: system `trades` table matches Zerodha positions page
- [ ] Watch for kill_switch activations (Telegram will notify)

### Quick Health Check Command
```bash
ssh trading-vm
sudo journalctl -u trading-system -n 5 --no-pager
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
  "SELECT symbol, direction, status, gross_pnl FROM trades WHERE date(created_at) = date('now') ORDER BY created_at DESC LIMIT 5;"
```

---

## Abort Conditions

**Immediately close all positions and stop the system if ANY of these occur:**

1. **Slippage**: 2 consecutive trades with slippage > 1%
2. **Naked position**: Any naked position alert (position without SL)
3. **Capital mismatch**: System vs broker capital difference > Rs 50
4. **Token expiry**: Token expires mid-session (orders will fail silently)
5. **API failures**: 3 broker API failures in a row
6. **Kill switch**: HARD_KILL triggered (system auto-stops, but verify)
7. **Wrong fills**: Any order filled at completely wrong price (> 2% from limit)

### Abort Procedure
```bash
# 1. Stop the system
sudo systemctl stop trading-system

# 2. Check Zerodha for open positions
# (use Zerodha web/app to manually close any remaining)

# 3. Verify all positions closed
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
  "SELECT count(*) FROM trades WHERE status='OPEN';"

# 4. Record incident details for post-mortem
```

---

## EOD (15:30-17:00)

- [ ] **15:30** -- Confirm all positions squared off (Zerodha positions page = empty)
- [ ] **15:40** -- Candle fetch cron fires
- [ ] **15:50** -- EOD cleanup cron fires
- [ ] **15:55** -- EOD verify cron fires, check Telegram for VERIFIED
- [ ] **16:05** -- Daily report generated, review immediately:
  ```bash
  ls -la ~/systems/trading-system/reports/output/daily_report_$(date +%Y-%m-%d).xlsx
  ```
- [ ] **16:20** -- Gemini EOD review delivered, read fully
- [ ] **16:45** -- **CRITICAL**: Reconcile Zerodha terminal P&L vs system P&L
  ```bash
  sqlite3 ~/systems/trading-system/data_store/trading_system.db \
    "SELECT SUM(net_pnl) as total_pnl FROM trades WHERE date(created_at) = date('now') AND status='CLOSED';"
  ```
  Compare against Zerodha "Today's P&L" -- **must match within Rs 5**
- [ ] **17:00** -- Decision: continue tomorrow OR pause for fixes

---

## Day 1 Success Criteria

All of the following must be true to continue on Day 2:

1. **Reconciliation**: All trades match between system and broker (count + P&L)
2. **No CRITICAL alerts**: Zero unresolved CRITICAL Telegram messages
3. **No manual interventions**: System ran autonomously without human fixes
4. **Capital invariant**: System capital tracking matches broker throughout
5. **Kill switch clean**: No spurious kill switch activations
6. **Slippage acceptable**: All fills within 0.5% of limit price
7. **Timing correct**: All cron jobs fired on schedule (check cron_heartbeat)

### If ANY criterion fails:
- Do NOT trade live on Day 2
- Document the failure in `docs/incidents/`
- Fix the root cause
- Run another paper day to verify the fix
- Then retry live

---

## Emergency Contacts

- **Zerodha support**: support@zerodha.com / Zerodha app chat
- **VM provider**: Check provider dashboard for VM health
- **System logs**: `~/systems/trading-system/logs/`

---

## Post Day 1 (Evening)

- [ ] Write 3-sentence summary of the day
- [ ] If successful: plan Week 1 monitoring cadence (reduce from "every 15 min" to "every hour" by Friday)
- [ ] If issues: create fix plan before next session
