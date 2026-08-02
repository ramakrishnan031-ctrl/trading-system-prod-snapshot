# Daily Operations Runbook

Standard operating procedures for the trading system.

## Morning Startup (08:00-09:14 IST)

### Automated (Cron)
| Time | Job | What it does |
|------|-----|-------------|
| 05:00 | Token cleanup | Removes yesterday's Zerodha token |
| 08:00 | Auto token refresh | Generates fresh token via TOTP |
| 08:30 | Premarket healthcheck | Validates secrets, DB, disk, config |
| 08:35 | F&O ban list fetch | Downloads NSE ban list |
| 08:55 | Premarket briefing | Gemini-powered operational brief → Telegram |

### Manual Checks
1. Check Telegram for healthcheck result (08:30)
2. Verify token generated: `cat data_store/session/zerodha_token.json | python3 -m json.tool`
3. Check system status: `sudo systemctl status trading-system`
4. Verify no kill switch active: `sqlite3 data_store/trading_system.db "SELECT state FROM kill_switch_state ORDER BY rowid DESC LIMIT 1;"`

### If Token Failed
```bash
# On Windows PC
scripts\zerodha_morning.bat
scripts\copy_token_to_vm.bat

# Or on VM directly
cd ~/systems/trading-system
PYTHONPATH=. python3 scripts/auto_refresh_token.py
```

## During Market (09:15-15:30)

### Monitoring Cadence
- **First 30 minutes**: Full attention. Watch every signal in Telegram.
- **09:45-15:00**: Check Telegram every 15 minutes.
- **15:00-15:30**: Watch for EOD squareoff at 15:17.

### Quick Health Check
```bash
ssh trading-vm
sudo journalctl -u trading-system -n 10 --no-pager
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
  "SELECT symbol, direction, status, net_pnl FROM trades
   WHERE date(created_at) = date('now') ORDER BY created_at DESC LIMIT 5;"
```

### What to Watch For
- **CRITICAL alerts**: Stop everything, investigate immediately
- **Kill switch activation**: System auto-stops new trades
- **Naked position alerts**: Position without SL (emergency)
- **Capital mismatch**: System vs broker divergence
- **Token expiry**: Orders will silently fail

### Common Alert Responses

| Alert | Action |
|-------|--------|
| `SOFT_KILL triggered` | No new trades. Existing positions managed. Check reason. |
| `HARD_KILL triggered` | All positions being closed. Verify in Zerodha. |
| `Naked position` | Check Zerodha for orphaned position. Manual SL if needed. |
| `Capital drift > threshold` | Check FundManager state. May need manual reconciliation. |
| `API failure (3 consecutive)` | Check Zerodha API status. Token may have expired. |
| `Slippage > 1%` | Review the trade. May indicate illiquid symbol. |

## EOD Procedures (15:30-17:00)

### Automated (Cron)
| Time | Job | What it does |
|------|-----|-------------|
| 15:17 | EOD squareoff | Closes all open positions |
| 15:40 | Candle fetch | Downloads 1-min OHLCV for traded symbols |
| 15:45 | Position reconciliation | Broker vs system position check |
| 15:50 | EOD cleanup | Clears stale signals, orders, fingerprints |
| 15:55 | EOD verification | Confirms all positions closed |
| 16:00 | Daily review report | xlsx + md report |
| 16:00 | WAL checkpoint | Compacts SQLite WAL |
| 16:05 | Daily report (excel) | Full trading report |
| 16:10 | Trade journal | Daily journal entry |
| 16:15 | Strategy metrics | Sharpe, win rate, demotion |
| 16:16 | Metrics summary | Daily system health summary |
| 16:20 | Gemini EOD review | AI log analysis |
| 16:40 | Trade coach | AI coaching per trade |
| 17:00 | Data integrity | Candle data vs Zerodha comparison |

### Manual EOD Verification
1. Check Telegram for "EOD VERIFIED" message (15:55)
2. Confirm Zerodha positions page is empty
3. Reconcile P&L:
```bash
sqlite3 ~/systems/trading-system/data_store/trading_system.db \
  "SELECT SUM(net_pnl) FROM trades
   WHERE status='CLOSED' AND date(created_at)=date('now');"
```
4. Compare against Zerodha "Today's P&L" — must match within Rs 5
5. Review daily report: `ls -la reports/output/daily_report_$(date +%Y-%m-%d).xlsx`

## Weekend / Holiday

System automatically detects holidays (via `config/nse_holidays_2026.yaml`)
and weekends. On non-trading days, `main.py` exits with code 0 immediately.

### Sunday Automated Jobs
| Time | Job |
|------|-----|
| 18:00 | Instrument master refresh |
| 18:00 | Weekly pattern detection (Gemini) |

## Common Troubleshooting

### System Won't Start
1. Check `sudo journalctl -u trading-system -n 50`
2. Common causes:
   - Token missing → run auto_refresh_token.py
   - Kill switch from yesterday → auto-clears on new day
   - Config change → check startup check output
   - Port 5000 in use → `sudo lsof -i :5000`

### No Signals Coming In
1. Check webhook health: `curl http://localhost:5000/health`
2. Check Chartink scanner pages are running
3. Check ngrok/tunnel is active
4. Look for "outside entry window" in logs

### Order Placement Failing
1. Check token validity: `python3 -c "from kiteconnect import KiteConnect; ..."`
2. Check kill switch state
3. Check rate limiter (3 req/sec limit)
4. Check if symbol is in F&O ban list

### Kill Switch Won't Clear
A **previous-day** kill auto-clears at the next 08:15 startup — every type, including
HARD_KILL (`clear_stale_state`, `capital/kill_switch.py:284`). **Only a SAME-DAY kill
needs clearing by hand.**

**LIVE / VM — the sanctioned path:**
```bash
sudo bash deploy/resume.sh            # SOFT_KILL
sudo bash deploy/resume.sh --force    # also clears HARD_KILL (emergency)
```
It stops the unit (releasing the instance lock and any restart loop), clears the kill via
`scripts/clear_kill_switch.py`, then starts the service — and if the clear is **refused it
does not start the service**.

**PAPER / PC (no systemd — `resume.sh` is VM-only):**
```bash
python scripts/clear_kill_switch.py --dry-run   # report state, change nothing
python scripts/clear_kill_switch.py [--force]   # clear
```

⛔ **Never clear a kill by editing the DB, and never restart to "clear" one.** A raw
`UPDATE`:
- **does nothing to a running service** — `is_active()` reads *in-memory* state
  (`kill_switch.py:487-491`), not the DB, so the process stays killed until it restarts;
- leaves `triggered_at`/`triggered_by` stale and writes **no `system_events` audit trail**,
  so the row still attributes the clear to whoever tripped the kill;
- **bypasses the HARD_KILL guard** — `clear_kill_switch.py` refuses a HARD_KILL without
  `--force` precisely because an emergency kill **re-trips if the root cause is unfixed**.
- ⛔ **And if the clear did not actually take, the restart HALTS the box:** a boot with a
  kill still persisted returns **exit 4** (`main.py:1925-1932`), and
  `RestartPreventExitStatus=3 4` (`deploy/systemd/trading-system.service:35`) tells systemd
  **not** to bring it back. Fix the root cause first.

> ~~**SUPERSEDED 02-Aug-2026 (ledger #8 / IA-XDOCS-01)** — kept legible. This section used
> to say "Previous-day kill switches auto-clear at startup. If still stuck:" followed by
> `sqlite3 data_store/trading_system.db "UPDATE kill_switch_state SET state='INACTIVE',
> reason='manual_clear' WHERE rowid = (SELECT MAX(rowid) FROM kill_switch_state);"` and
> `sudo systemctl restart trading-system`.~~ It was the pre-`resume.sh` anti-pattern: no
> audit trail, no HARD_KILL gate, no effect on the running process, and its restart step is
> exactly what converts a stuck kill into an exit-4 HALT.

### Database Locked
```bash
# 1. Checkpoint the WAL
python3 scripts/wal_checkpoint.py
# 2. If STILL locked, check for an active kill BEFORE restarting anything
sqlite3 data_store/trading_system.db \
  "SELECT state, triggered_at FROM kill_switch_state WHERE id=1;"
```
- **`INACTIVE`** → `sudo systemctl restart trading-system` is safe.
- ⛔ **HARD_KILL (any date), or SOFT_KILL triggered TODAY** → a restart returns **exit 4**
  and **systemd will not bring the service back** (`utils/startup_checks.py:242-286` →
  `main.py:1932`). Clear it first with `sudo bash deploy/resume.sh` (which restarts for
  you), or leave the service alone.
  ⚠️ **This is the COMMON case after 15:15**, when the daily circuit-breaker SOFT_KILL is
  active and persists overnight by design.

> ~~**SUPERSEDED 02-Aug-2026 (ledger #8 / IA-XDOCS-01)**~~ — this previously said only
> *"If still locked, restart service: `sudo systemctl restart trading-system`"*, an
> **unconditional** restart that HALTS the system whenever a same-day kill coincides.
