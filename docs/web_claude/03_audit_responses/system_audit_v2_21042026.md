# Trading System v2: Final Audit Summary

## Critical Issues by Priority

---

### 🔴 RED FLAGS (Must Fix Before Live Capital)

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| 1 | **Shadow Fill Race** | `OrderPlacer.py` | Orders sent to broker before DB commit. Instant fills → orphaned positions, stuck capital |
| 2 | **FundManager Ledger Atomicity** | `fund_manager.py` | DB write before memory update. Crash between → permanent capital corruption on restart |
| 3 | **SQLite Write Contention** | `state_store.py` | Multiple threads writing concurrently → `database is locked`, dropped trades |
| 4 | **Naked LIMIT_TRIPLE Positions** | `order_protocol_limit.py` | SL leg fails, ENTRY filled during cancel attempt → open unhedged position |
| 5 | **Missing OCO Orchestration** | `OrderPlacer.py` | TGT fills but SL remains active → price reversal triggers unintended position |
| 6 | **EOD Square-Off Broker Rejection** | `eod_squareoff.py` | Blind MARKET orders without cancelling pending TGT/SL → rejection, overnight exposure |
| 7 | **Partial-Fill Capital Leak** | `OrderPlacer.py` | Order cancelled after partial fill → no OrderFilled event → capital stuck in reserved |
| 8 | **Float-Point SL Rejection** | `SmartTgtManager.py` | Trailing SL not rounded to tick_size → broker rejects every update |

---

### 🟡 YELLOW FLAGS (Fix First Week of Live)

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| 9 | **SmartTgt Reconnect Amnesia** | `SmartTgtManager.py` | `best_price` lost on restart → trailing SL starts over, locked profits unprotected |
| 10 | **Market Order Slippage Blindspot** | `OrderMonitor.py` | `expected=0.0` for MARKET orders → 0% slippage logged, poisoned analytics |
| 11 | **Pin Dependencies** | `requirements.txt` | `>=` for kiteconnect/flask → auto-updates may break APIs |
| 12 | **Reconciler Rate-Limit DDOS** | `OrderReconciler.py` | Sync event trigger → 18 API calls in 500ms → 429 errors, thread freeze |
| 13 | **Cost Calculator FNO Blindness** | `CostCalculator.py` | STT calculated as cash equity for options → phantom taxes, invariant trigger, HARD_KILL |
| 14 | **EOD Recovery Race vs RMS** | `eod_squareoff.py` | Restart after RMS auto-square → sells already-closed positions → opens naked shorts |
| 15 | **Alerting Bottleneck** | `alert_watcher.py` | Sequential SMTP sends → 1-2 alerts/min → major delays during failures |
| 16 | **Webhook Backpressure Deadlock** | `webhook_receiver.py` | Queue fills, workers block 30s on RateLimiter → all webhooks rejected |
| 17 | **Startup Scenario Blind Spot** | `startup_checks.py` | Manual shutdown after square-off but before event → misclassifies COLD vs WARM |
| 18 | **Hardcoded Time Authority** | `step_executor.py` | `_MARKET_OPEN = 09:15` ignores config → special sessions break |
| 19 | **Ghost-SL on Reconnect** | `OrderReconciler.py` | _check8 "corrects" broker SL to stale local → violates "never retreat" |
| 20 | **Shadow Tracker Simulation Drift** | `shadow_tracker.py` | Uses LTP instead of Bid/Ask → over-optimistic simulated fills |
| 21 | **In-Memory State Loss** | `OrderMonitor.py` | Crash restart → _watched empty → no polling of open orders |
| 22 | **Webhook Sweeper KeyError** | `webhook_receiver.py` | Evicts stuck symbol → worker later tries to remove → KeyError crash |
| 23 | **EventBus Blast Radius** | `events.py` | Non-critical subscriber exception → crashes OrderMonitor loop |

---

## Required Fixes by Component

### OrderPlacer.py
```
1. Two-Phase Commit:
   - INSERT trade/orders with status='PLACING' BEFORE broker API
   - UPDATE to 'SUBMITTED' after broker response

2. Subscribe to OrderStateChanged (not just OrderFilled):
   - On CANCELLED/FAILED with qty_filled > 0 → commit partial, release remainder

3. OCO Logic:
   - On TGT fill → cancel sibling SL
   - On SL fill → cancel sibling TGT
```

### fund_manager.py
```
Wrap ledger INSERT + memory update in try/except with ROLLBACK:
  try:
      db.insert(ledger_row)
      update_balance()
  except Exception:
      db.rollback()
      raise
```

### state_store.py
```
Add retry logic with exponential backoff:
  for attempt in range(5):
      try:
          with transaction():
              ...
      except sqlite3.OperationalError as e:
          if "locked" in str(e):
              time.sleep(0.1 * (2 ** attempt))
              continue
          raise
```

### order_protocol_limit.py
```
If SL leg fails:
    check ENTRY fill status first
    if filled → raise error (manual intervention)
    else → cancel ENTRY
```

### SmartTgtManager.py
```
1. Persist best_price to smart_tgt_state table:
   - Save on every trail update
   - Load during rehydrate()

2. Round to tick_size before broker update:
   tick_size = instrument_cache[token].tick_size
   new_sl = round(raw_sl / tick_size) * tick_size
```

### eod_squareoff.py
```
Sequence:
  1. Cancel all pending ENTRY orders
  2. Cancel all pending TGT and SL orders (unblock shares)
  3. For MIS: MARKET orders
  4. For CO: send cancel to SL leg (triggers broker exit)
  5. Recovery mode: call get_positions() first → only exit what broker reports
```

### OrderReconciler.py
```
Remove OrderStateChanged subscription
Keep only daemon poll thread (every 15 sec)
```

### CostCalculator.py
```
Add is_fno parameter:
  if is_fno:
      # Options: STT on premium only
      # Futures: different rates
  else:
      # Cash equity rates
```

### requirements.txt
```
Change all >= to ==:
  kiteconnect==4.2.0
  flask==3.0.0
  (pin all dependencies)
```

---

## Already Good (Do Not Touch)

| Component | Reason |
|-----------|--------|
| G3 Capital Invariants (`invariant.py`) | Bulletproof separation of reserves |
| OrderStateMachine | Strict one-way transitions |
| OrderReconciler hybrid approach | Self-healing + safe halts |

---

## Go/No-Go Decision

| Environment | Status |
|-------------|--------|
| Paper Trading | ✅ Ready |
| Live Capital | ❌ **Not yet** |

### Prerequisites for Live:

- [ ] #1 Shadow Fill Race
- [ ] #2 FundManager Atomicity
- [ ] #5 OCO Orchestration
- [ ] #6 EOD Square-Off Rules
- [ ] #7 Partial-Fill Leak
- [ ] #8 Tick-Size Rounding

**Once these 6 RED FLAGS are fixed → system is production-ready.**