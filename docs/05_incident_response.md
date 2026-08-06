# Incident Response Guide

Procedures for handling trading system incidents during market hours.

> ⭐ **BEFORE TREATING AN ALERT AS AN INCIDENT, CHECK [`expected_alarms.md`](expected_alarms.md).**
> Several CRITICAL-grade messages fire on a **completely healthy** day — the 15:15
> breaker's 4-line chorus, the post-schema-push refuse-window, the delivery-reconciler
> strings, and the deliberate loudness added by ledger #2/#2b/#2c-R. Measured: on 31-Jul,
> a healthy day with **zero real incidents**, the system emitted **49 alerts**.
> ⛔ That document lowers the **alarm**, never the **check** — every entry there carries a
> *"THIS IS REAL IF…"* line. If what you are looking at does not match the expected
> signature exactly, it is an incident: continue here.

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
# A PRIOR-DAY kill clears itself at the next 08:15 boot (clear_stale_state,
# 20-Jun-2026) -- nothing to do. The steps below are for a SAME-DAY kill.
#
# ⛔ DO NOT `systemctl restart` -- for a same-day EMERGENCY kill that FAILS.
#    Startup hits StartupScenario.HALT and exits 4; the unit does NOT come back.
# ⛔ DO NOT run `main.py --resume` directly -- it competes with the service for
#    the instance lock (port 5001). That was the 18-Jun collision.
sudo bash deploy/resume.sh            # SOFT_KILL
sudo bash deploy/resume.sh --force    # also clears HARD_KILL
# It does three things: stop the unit + `systemctl reset-failed` -> clear the
# kill switch in the DB -> start the service under systemd. If the clear is
# refused it STOPS and does not start the service -- resolve, then re-run.
```
⚠️ **Fix the root cause FIRST.** An emergency kill (API/IP 403, capital drift,
etc.) simply re-trips otherwise.

> **⛔ WHAT YOU WILL ACTUALLY SEE — and it is NOT a restart loop.**
> `systemctl is-active trading-system` reads **`failed`**, and the journal says
> `Failed with result 'exit-code'` (measured: 21-Jul-2026 11:37:53). The unit
> exits **once** and stays down — `RestartPreventExitStatus=3 4` in the unit file
> deliberately stops systemd hammering the same kill (the 18-Jun crash-loop).
> ⭐ **Contrast the normal night:** a clean 17:35 self-exit is exit 0 →
> `Deactivated successfully` → **`inactive (dead)`**. `inactive` is healthy;
> **`failed` is the HALT.**
>
> **⚠️ THIS IS A CLASS, NOT A DRIFT-ONLY CASE.** `SCHEDULED_KILL_REASONS` holds
> exactly **two** literals (`circuit_breaker_force_close_15:15`, `EOD_SQUAREOFF`)
> against **26** `soft_kill`/`hard_kill` call sites — so **every other kill path
> is an EMERGENCY kill** and behaves this way: capital drift, token expiry,
> live-feed queue-full / reconnect-exhausted / consumer-dead, the API-failure
> auto-trip, fund-manager invariant, order-placer, reconciler, cnc_gtt_monitor,
> System Manager EOD.
>
> **✅ DETECTION IS ALREADY COVERED — you will be told.** `liveness_probe`
> (cron `*/5 9-15 * * 1-5`) raises **ONE CRITICAL** via Telegram, with the
> CRITICAL-sentinel email fallback, within **~5 minutes** of the unit not being
> active during **[09:00, 16:00)** on a trading day — including the 15:15
> squareoff. ⚠️ Its window ends at 15:59, so a 16:00–17:35 outage is unwatched
> (a separate, already-tracked item — not re-litigated here).

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

## P0: Delivery (CNC) GTT — DO NOT CANCEL

⛔ **DO NOT CANCEL A SYSTEM-PLACED GTT WHILE `cnc_gtt_monitor` IS RUNNING.**
The monitor treats a missing GTT as damage and repairs it — `orders/cnc_gtt_monitor.py`,
the *"GTT missing; holding intact"* auto-recreate branch. A cancelled GTT reads as
missing. **To stop a GTT being replaced, stop the monitor — not the GTT.**

- 🏷️ **(P) OBSERVED for the EXPIRY case** — 06-Aug-2026 10:02:13: GTT `330648138`
  expired and was auto-recreated as `330657774` in the same cycle.
- 🏷️ **(I) INFERRED for the CANCEL case** — the branch is identical (both present as
  "missing"), but no cancel has yet been observed reaching the broker. ⭐ Upgrade this
  label on the first observed cancel.

⚠️ **You are racing a ~15-minute cycle** (measured 06-Aug: 10:02:13 → 10:17:25 →
10:32:36 → 10:47:46). A cancel only counts as successful if the *next* cycle still
shows the GTT gone. ⛔ Verify from `grep -c "place_gtt call_end" logs/system_<date>.log`
— a recreate increments it — **not from the Kite GTT tab, which never shows GTT ids.**

### The phantom reservation this can leave behind

`_finalize_gtt_exit` is the ONLY path that closes a delivery trade **and** releases its
reservation, and it is gated on `held == 0`. If `held` is wrong the trade stays `OPEN`
and its capital stays reserved **permanently** — replayed by `rehydrate_from_open_trades`
at every 08:15 boot.

- **Symptom:** the delivery bucket opens short, and the failure presents as
  "no signals qualified" — indistinguishable from a quiet day.
- **Check:** compare `fund_manager.initialize`'s `positional_avail` in the boot log
  against the first positional `RESERVE`'s `balance_before` in `fm_ledger`. A gap is a
  carried reservation. *(06-Aug: 2802.54 vs 2215.14 = exactly one stranded trade.)*
- ⚠️ **A clean release cannot be keyed to its trade.** The `RELEASE_USED` row carries
  **neither `trade_id` nor `reservation_id`** (verified 06-Aug, ledger 10222). Match by
  timestamp + bucket + amount, or you will read a successful release as a missing one.

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
