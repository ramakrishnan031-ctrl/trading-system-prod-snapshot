# Broker Integration Audit — 2026-06-02

Investigation-only audit of 5 potential broker integration gaps.
No code changes made. All findings based on current HEAD (aae1543).

---

## GAP 1: SL Placement Failure After Entry Fill

### Scenario
Entry order fills successfully. System attempts to place SL order.
Broker rejects SL (margin, illegal price, freeze, etc.).

### Current Behavior

**LIMIT_TRIPLE mode** (separate SL order):

The code path is `_handle_entry_fill` (line 1660) -> `_place_limit_triple_exits` (line 2065).

1. After entry fill, `_place_limit_triple_exits` calls `engine.place_deferred_exits` to place SL + TGT at the actual filled qty (naked-short fix OP-NS1).
2. If placement raises a **LTP validation error** (broker rejects because trigger price violates LTP rule): the order is added to a retry queue (`_add_to_exit_retry`, line 2535). On each subsequent LTP tick, placement is retried up to `MAX_RETRIES = 3` times. After 3 failures, a **soft_kill** is triggered (prevents new trades but does not close existing ones), and a CRITICAL log is emitted with grep tag `LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED`.
3. If placement raises **any other error** (margin, freeze, network): `_fire_hard_kill_for_unprotected_position` is called immediately. This fires `kill_switch.hard_kill`, logs CRITICAL with tag `LIMIT_TRIPLE_EXITS_FAILED_POSITION_UNPROTECTED`, and does NOT re-raise (event handler context).
4. If broker accepts but **DB persist fails**: broker orders are cancelled best-effort, hard_kill fires.
5. If broker accepts and DB persists but **track() fails**: hard_kill fires. Orders are NOT cancelled (persist succeeded; reconciler is the backstop).

**CO_PLUS_TGT mode** (broker-managed SL):

SL is embedded in the CO bracket at the broker side. Only the TGT is placed separately after fill via `_place_co_tgt_exit` (line 2319). If TGT placement fails, hard_kill fires with tag `CO_TGT_FAILED_POSITION_UNPROTECTED`. The position still has the broker-managed SL.

### Is This Handled?
**Yes — well handled.**

### Risk Assessment
**LOW** for CO_PLUS_TGT (broker-native SL always present).
**MEDIUM** for LIMIT_TRIPLE — the position is naked between entry fill and SL placement success (or hard_kill). The retry mechanism handles the most common failure (LTP validation), but other failures go straight to hard_kill with no market-close attempt.

### Gap Identified
The system does NOT attempt to market-close the naked position after SL placement failure. It fires hard_kill (preventing new trades) and relies on the reconciler + manual intervention. The reconciler runs every 15 seconds (RC3, line 13) — so worst case, a position is naked for up to 15s + reaction time.

**Suggested fix**: After SL placement failure in LIMIT_TRIPLE, before hard_kill, attempt a market exit order for the filled qty. If market exit also fails, then fall through to existing hard_kill + reconciler path. This closes the window from "naked position + hard_kill" to "attempted exit + hard_kill".

---

## GAP 2: SL Modification Rejection (BreakevenManager / SmartTgtManager)

### Scenario
BreakevenManager wants to move SL to breakeven. Broker rejects modify.

### Current Behavior

**BreakevenManager** (`orders/breakeven_manager.py`, line 216):

1. `_advance_sl` calls `adapter.modify_order(broker_order_id, trigger_price=...)`.
2. If `modify_order` raises an **exception**: logs ERROR and returns. No retry. No alert. Original SL remains in place.
3. If `modify_order` returns `success=False`: logs ERROR with reason and returns. No retry. No alert. Original SL remains in place.
4. Only on success does it update internal state (`breakeven_applied = True`).

**Race condition handling**: `_get_sl_broker_order_id` (line 271) queries DB for an active SL order (`status NOT IN ('CANCELLED', 'COMPLETE', 'REJECTED', 'FAILED')`). If SL has already triggered (COMPLETE), the query returns None, and `_advance_sl` logs WARNING "no_sl_order" and returns safely. If SL triggered between the query and the modify call, the broker rejects the modify (order already executed), and the error path handles it.

**SmartTgtManager** (`orders/smart_tgt_manager.py`, line 655):

1. On modify failure, checks if it's a **terminal order error** (order already complete/cancelled) via `_is_terminal_order_error` (line 690). If terminal: unregisters trade gracefully — no retry.
2. For **transient errors** (500, timeout, rate limit): increments `consecutive_failures` counter.
3. After `max_modify_failures` (default 3, configurable via FIX-142) consecutive transient failures: fires CRITICAL log + `on_critical_failure` callback. This callback is wired to trigger a Telegram alert.
4. On success: resets `consecutive_failures` to 0.
5. Original SL remains in place until modification succeeds — the broker holds the last confirmed SL.

### Is This Handled?
**Partial.**

### Risk Assessment
**LOW** — In both managers, the original SL remains active at the broker until explicitly modified. The position is never left unprotected by a modification failure. The risk is only that the SL doesn't advance to breakeven/trail, meaning potential profit is at risk, not capital.

### Gap Identified
1. **BreakevenManager has no retry and no alert on failure.** If modify fails due to a transient network error, it simply gives up until the next candle-close check triggers another attempt. There is no Telegram alert for repeated failures.
2. **SmartTgtManager fires CRITICAL after max failures but does NOT stop retrying.** After the critical alert, subsequent candle closes will continue attempting modifications (counter keeps incrementing, but no circuit breaker prevents further broker calls).
3. **Neither manager handles the race where SL triggers between LTP check and modify call.** The BreakevenManager query checks DB status (not real-time broker status). If SL fills between the DB query and the modify_order call, the broker will reject the modify. The error is logged but not specifically identified as "SL already triggered." This is safe (error path returns), but the log message is generic.

**Suggested fix**:
- BreakevenManager: add retry (1-2 attempts with backoff) and Telegram alert after persistent failure.
- SmartTgtManager: after max_modify_failures, unregister the trade (stop retrying) rather than continuing to hammer the broker.

---

## GAP 3: NSE Corporate Action Detection (Split/Bonus/Dividend)

### Scenario
Stock held overnight (positional). Next morning: split 1:5 happened.
Stored entry_price=1000, sys_sl=950 — but stock now trades at 200 post-split.

### Current Behavior

**Do we trade overnight?** Yes. Three strategy configs have `intent: "DELIVERY"`:
- `positional_momentum_long.yaml`
- `positional_sector_rotation.yaml`
- `positional_swing_long.yaml`

All three use LIMIT_TRIPLE protocol with ATR-based SL/TGT.

**Is there any corporate action detection?** No. A codebase search for "corporate_action", "stock split", "bonus issue", "ex_date", "price adjustment" returns zero results in production code (only noise hits from `str.split()` calls).

**Zerodha API**: Zerodha's Kite Connect API provides instrument data (via `instruments()` call used by `refresh_instruments.py`) which includes `last_price` and `tick_size`, but does NOT provide a corporate action calendar. NSE publishes corporate action data via a separate API/website that is not consumed.

**What would happen post-split?**
1. Entry price in DB: Rs 1000. SL in DB: Rs 950.
2. Post-split stock trades at Rs 200.
3. SL order at broker: the broker adjusts pending orders for corporate actions. Zerodha auto-adjusts CO/pending orders, but the timing and coverage is not guaranteed for all order types.
4. Our reconciler CHECK 4 (PARTIAL_CLOSE) or CHECK 1 (MANUAL_CLOSE) would detect qty mismatches if broker adjusts qty, but cannot detect price-only adjustments.
5. BreakevenManager/SmartTgtManager would attempt to trail SL to values based on the old (pre-split) price scale — modify_order would likely succeed (trigger_price=980 while stock at 200 means SL is far away and useless).

### Is This Handled?
**No.**

### Risk Assessment
**CRITICAL for DELIVERY positions.** A stock split would make existing SL/TGT orders meaningless. A 1:5 split turns a 5% SL into a 475% distance — the position is effectively unprotected.

**LOW for INTRADAY (MIS).** All intraday positions are squared off by EOD; no overnight corporate action exposure.

### Gap Identified
No corporate action detection, price adjustment, or morning validation exists. The system blindly trusts stored prices across sessions.

**Suggested fix**:
1. **Pre-market validation** (add to `premarket_healthcheck.py`): For each open DELIVERY position, fetch current LTP and compare against stored entry_price. If |LTP - entry_price| / entry_price > 30%, flag CRITICAL alert ("possible corporate action or extreme move").
2. **Long-term**: Consume NSE corporate action calendar. Before market open, check if any held symbol has an ex-date today. If yes, force soft_kill + alert for manual review.
3. **Mitigation now**: The 3 positional strategies are defined in YAML but may not be active in scanner. Confirm whether any DELIVERY trades have ever been placed. If not, this is a latent gap, not an active one.

---

## GAP 4: Broker-Side Forced Order Cancellation

### Scenario
NSE halts trading (lower circuit, news). Broker cancels open orders.

### Current Behavior

**Detection mechanism:**

1. **OrderMonitor** polls every 2 seconds (`poll_interval_sec: 2` in config, line 65). Each cycle calls `adapter.get_order_history(broker_order_id)` for every tracked order. If Kite returns status `CANCELLED`, the monitor calls `_handle_terminal(entry, "CANCELLED")` (line 762-763).

2. `_handle_terminal` (line 1004):
   - If order had partial fills (`filled_qty > 0`): emits `OrderPartiallyTerminated` event first, then transitions to CANCELLED.
   - Otherwise: transitions directly to CANCELLED.
   - Publishes `OrderStatusChanged` event.
   - Untracks the order.

3. **OrderPlacer** subscribes to `OrderStatusChanged` via `_on_order_status_changed` (line 1479). Behavior depends on the leg:
   - **ENTRY cancelled**: capital reservation is released. Trade marked FAILED.
   - **SL cancelled**: the order is untracked. **No immediate alert or recovery.** The position is now naked.
   - **TGT cancelled**: the order is untracked. No financial risk.

4. **Reconciler CHECK 9** (`_check9_missing_exits`, line 989): Runs every 15 seconds. For each OPEN trade, verifies the local SL order's `broker_order_id` appears in broker's open-order list. If missing: CRITICAL log + `soft_kill()` + Telegram alert (bypass backoff).

**Worst-case time-to-detect:**

| Scenario | Detection | Time |
|----------|-----------|------|
| Entry cancelled | OrderMonitor poll | 2 seconds |
| SL cancelled | OrderMonitor sees CANCELLED | 2 seconds for status change |
| SL cancelled (naked detection) | Reconciler CHECK 9 | up to 15 seconds |
| TGT cancelled | OrderMonitor poll | 2 seconds |

### Is This Handled?
**Partial.**

### Risk Assessment
**MEDIUM** — SL cancellation by exchange is detected within 2 seconds at the order level, but the system does NOT immediately act on it (does not auto-place a new SL or market-close). The reconciler catches the naked position within 15 seconds and fires soft_kill, but does not auto-remediate.

### Gap Identified
1. **No immediate reaction to SL cancellation.** When OrderMonitor detects SL status = CANCELLED, it publishes `OrderStatusChanged` but OrderPlacer's handler for exit-leg cancellation only untracks/logs — it does NOT alert, does NOT fire soft_kill, and does NOT attempt re-placement. The system relies entirely on the reconciler's 15-second cycle to notice the missing SL.
2. **No exchange halt detection.** The system cannot distinguish "exchange cancelled our order" from "we cancelled our own order." Both appear as CANCELLED status. There's no parsing of `rejection_reason` for exchange-specific reasons.
3. **CHECK 9 detects but does not remediate.** It fires soft_kill (preventing new trades) but does NOT attempt to place a replacement SL or market-close. Manual intervention is required.

**Suggested fix**:
1. In `_on_order_status_changed`: when an SL leg transitions to CANCELLED and the trade is still OPEN, immediately fire CRITICAL alert + attempt SL re-placement (or market exit if SL re-placement fails).
2. Parse `rejection_reason` from broker for exchange-specific keywords ("EXCHANGE", "CIRCUIT", "HALT") and log them distinctly for monitoring.

---

## GAP 5: Margin Call / Auto-Square-Off by Broker (RMS)

### Scenario
MIS position. Market moves sharply against. Zerodha's RMS auto-squares the position.

### Current Behavior

**How Zerodha RMS works**: When margin shortfall occurs, Zerodha's Risk Management System (RMS) places a MARKET order to close the position. This appears as a new order in the Kite API, NOT as a modification of our existing orders. Our SL/TGT orders remain open but become "orphaned" (position gone, orders still live).

**Detection path:**

1. **Reconciler CHECK 1** (`_check1_manual_close`, line 610): Every 15 seconds, compares local OPEN trades against `adapter.get_positions()`. If a local trade is OPEN but broker shows no position for that symbol → triggers CHECK 1: MANUAL_CLOSE.
   - Marks trade `CLOSED_MANUAL` in DB.
   - Releases capital using **entry_price as exit proxy** (breakeven PnL, NOT actual RMS exit price).
   - Publishes `PositionClosed` with `realized_pnl = 0` (breakeven assumption).
   - Does NOT cancel orphaned SL/TGT orders for this trade.

2. **Reconciler CHECK 9** (`_check9_missing_exits`): Would detect the SL order missing from broker's open-order list if RMS also cancelled it. But if the SL order is still open (RMS only closed the position, didn't cancel our orders), CHECK 9 sees SL as healthy.

3. **OrderMonitor**: If the RMS exit triggers our SL or TGT (price-based), the monitor will detect the fill normally. But RMS exits are MARKET orders placed by Zerodha separately — they don't fill our SL/TGT orders. Our orders remain open.

**Distinction between RMS exit vs SL hit vs TGT hit:**
- SL hit: Our tracked SL order goes to COMPLETE. `_handle_exit_fill` fires with `leg="SL"`.
- TGT hit: Our tracked TGT order goes to COMPLETE. `_handle_exit_fill` fires with `leg="TGT"`.
- RMS exit: A new order (not tracked by us) closes the position. Our SL/TGT remain open. Reconciler CHECK 1 catches the position vanishing.

### Is This Handled?
**Partial — with significant accounting errors.**

### Risk Assessment
**HIGH** — The position closure is detected (CHECK 1), but:

### Gaps Identified
1. **Incorrect PnL accounting.** CHECK 1 uses `entry_price as exit proxy` — it records PnL = 0 (breakeven). In reality, an RMS exit means the market moved significantly against the position (that's why RMS triggered), so the actual loss could be substantial. The daily P&L report, capital tracking, and all downstream metrics will be wrong.
2. **Orphaned SL/TGT orders not cancelled.** CHECK 1 marks the trade CLOSED_MANUAL but does NOT cancel the remaining SL/TGT orders at the broker. These orders remain open. If price later reaches the SL or TGT level, they will fill — creating a new unintended position (opposite direction). This is caught by CHECK 2 (ORPHAN_ADOPTION) on the next cycle, but creates unnecessary risk.
3. **No RMS-specific detection.** The system cannot distinguish RMS squareoff from manual close via Zerodha terminal. Both appear as "position gone, orders still live." There's no log-level distinction for operational analysis.
4. **Detection latency.** CHECK 1 runs on the reconciler's 15-second cycle. RMS squareoffs typically happen during fast moves — 15 seconds of open orphaned orders in a volatile market is risky.
5. **Capital tracking drift.** release_used with breakeven PnL means FundManager's realized_pnl_today is wrong. Subsequent invariant checks may pass with incorrect values. The daily_loss_limit check uses this figure — a large unrecorded loss could allow further trading that should have been blocked.

**Suggested fix**:
1. When CHECK 1 detects MANUAL_CLOSE: immediately cancel all open orders for that trade_id (SL + TGT).
2. Fetch the actual exit fill from broker's order history to get the real exit_price. Use Zerodha's `orders()` API to find recent MARKET orders for the symbol that we didn't place — these are likely RMS exits.
3. Log RMS exits distinctly. If the exit was not initiated by us and the position was MIS, tag it as `exit_reason="RMS_SQUAREOFF"` for reporting.

---

## ADDITIONAL: Other Broker Integration Gaps Found

### A1: OCO Race Window (SL + TGT simultaneous fills)

**Current handling**: When an exit fill arrives (SL or TGT), `_handle_exit_fill` calls `_cancel_oco_siblings` (line 1970) to cancel the other exit leg. `close_trade` is called first and guards against double-exit via ValueError("already CLOSED").

**Gap**: There is a window between SL filling and TGT being cancelled where TGT could also fill. The `close_trade` double-close guard catches this (logs WARNING, skips release_used), but the second fill's qty is now a new naked position at the broker. This is caught by CHECK 2 (ORPHAN_ADOPTION) — but creates brief unintended exposure.

**Risk**: LOW — The window is small (milliseconds between close_trade and cancel_order), and OCO-like behavior at NSE is sequential (one fills, other is auto-cancelled for bracket orders). LIMIT_TRIPLE SL + TGT are independent orders at the exchange, so the theoretical window exists.

### A2: Stale Order Status After Network Partition

**Current handling**: OrderMonitor polls every 2s. If `get_order_history` raises `BrokerTimeoutError`, the order is simply skipped for that cycle. If `BrokerAuthError` occurs 3 times consecutively, the monitor stops entirely.

**Gap**: During a network partition (5-10 minutes), orders can fill at the broker but the monitor has no visibility. On reconnect, the monitor resumes polling and catches up. But during the partition, `_check_fill_timeout` may cancel ENTRY orders that actually filled (60-second timeout). The cancel request to broker will fail (network down), and the order remains — but the internal state marks it CANCELLED. On reconnect, the monitor would see COMPLETE and publish OrderStatusChanged, but the state machine may reject CANCELLED -> COMPLETE transition (already terminal).

**Risk**: MEDIUM — Network partitions during market hours are rare but not impossible. The reconciler catches position mismatches, but the state machine rejection could prevent normal fill processing.

### A3: Missing Position Qty Change on Partial RMS Exit

**Current handling**: CHECK 4 (PARTIAL_CLOSE) detects `broker_qty < local_qty`. It logs WARNING and fires soft_kill. But it does NOT adjust local qty, does NOT partially release capital, and does NOT resize SL/TGT orders.

**Gap**: If RMS partially exits a position (common when multiple MIS positions exist and RMS needs to free margin proportionally), the local state is permanently out of sync until manual intervention.

**Risk**: MEDIUM — Partial RMS exits are less common than full squareoffs, but the system has no auto-remediation path.

---

## Summary

| Gap | Description | Handled? | Severity |
|-----|------------|----------|----------|
| 1 | SL placement failure after entry fill | Yes | MEDIUM (LIMIT_TRIPLE: no market-close attempt) |
| 2 | SL modification rejection | Partial | LOW (original SL remains, but BreakevenMgr has no retry/alert) |
| 3 | Corporate action detection | No | CRITICAL (DELIVERY positions: SL/TGT prices meaningless post-split) |
| 4 | Broker-side forced cancellation | Partial | MEDIUM (SL cancellation has 15s detection gap, no auto-remediation) |
| 5 | RMS auto-squareoff | Partial | HIGH (wrong PnL, orphaned orders not cancelled, capital tracking drift) |
| A1 | OCO race window | Yes | LOW |
| A2 | Stale order status after network partition | Partial | MEDIUM |
| A3 | Partial RMS exit | Partial | MEDIUM |

### Priority Order for Fixes
1. **GAP 5 (RMS squareoff)** — HIGH. Wrong capital accounting + orphaned orders = compounding risk.
2. **GAP 3 (Corporate actions)** — CRITICAL but latent. Add pre-market LTP sanity check as minimum viable fix. Full corporate action calendar is a larger effort. Confirm whether DELIVERY strategies are active.
3. **GAP 4 (SL cancellation)** — MEDIUM. Add immediate SL-cancelled handler in OrderPlacer. Don't rely on 15s reconciler.
4. **GAP 1 (SL placement failure)** — MEDIUM. Add market-close attempt before hard_kill.
5. **GAP 2 (SL modify rejection)** — LOW. Add retry + alert to BreakevenManager.
