# Crash Test Information Gathering — 04-Jun-2026
**Purpose:** Pre-crash-test system survey. All answers derived from reading code/config.
**Scope:** June 8-12 crash test planning.
**Status:** READ-ONLY task — no code modified.

---

## SECTION A — ARCHITECTURE & PROCESS MODEL

**A.1 Python processes during market hours (09:00-16:00 IST)**

| Process | How Started | PID File |
|---------|-------------|----------|
| `main.py` | systemd: `trading-system.service` | None (instance lock via socket) |
| `scripts/alert_watcher.py` | systemd: `alert-watcher.service` (Restart=always) | `data_store/alert_watcher.lock` |
| `scripts/gemini_watchman.py` | systemd: `trading-watchman.service` (BindsTo=trading-system) | None |
| `scripts/capture_metrics_baseline.py` | cron `*/5 9-15 * * 1-5` | None |
| `deploy/token_watcher.sh` (bash) | systemd: `token-watcher.service` (root, Restart=always) | None |

`main.py` also spawns internal daemon threads (see A.4).

**A.2 Python processes outside market hours**

| Process | Trigger | Schedule |
|---------|---------|----------|
| `token-watcher.service` (bash) | systemd (24/7) | every 30s poll |
| `alert-watcher.service` | systemd (24/7) | continuous |
| `scripts/premarket_healthcheck.py` | cron | 08:30 IST Mon-Fri |
| `scripts/gemini_premarket_brief.py` | cron | 08:55 IST Mon-Fri |
| `scripts/fetch_daily_candles.py` | cron | 15:40 IST Mon-Fri |
| `scripts/reconcile_positions.py` | cron | 15:45 IST Mon-Fri |
| `scripts/eod_verify.py` | cron | 15:55 IST Mon-Fri |
| `reports/daily_review.py` | cron | 16:00 IST Mon-Fri |
| `scripts/wal_checkpoint.py` | cron | 16:00 IST Mon-Fri |
| `reports/daily_report.py` | cron | 16:05 IST Mon-Fri |
| `scripts/compute_strategy_metrics.py` | cron | 16:15 IST Mon-Fri |
| `scripts/reconcile_pnl.py` | cron | 16:15 IST Mon-Fri |
| `scripts/gemini_log_review.py` | cron | 16:35 IST Mon-Fri |
| `scripts/gemini_trade_coach.py` | cron | 16:40 IST Mon-Fri |
| `scripts/gemini_data_integrity_check.py` | cron | 17:00 IST Mon-Fri |
| `scripts/check_cron_drift.py` | cron | 18:00 IST daily |
| `scripts/gemini_weekly_patterns.py` | cron | 18:00 IST Sundays |
| `scripts/backup_restore_drill.py` | cron | 03:00 IST 1st of month |

**A.3 Entry point**
File: `main.py`
Startup command from `trading-system.service`:
```
/home/ubuntu/systems/venv/bin/python /home/ubuntu/systems/trading-system/main.py --mode paper
```

**A.4 Single-process, multi-thread**
Single process with the following named threads:

| Thread Name | Role |
|-------------|------|
| `webhook-server` | Waitress WSGI serving Flask on port 5000 (8 threads, daemon) |
| `eod-scheduler` | polls EodSquareoff.check_and_fire() every 5s (daemon) |
| `eod-pre-alert` | sleeps until 14:45 IST, fires EOD warning once (daemon) |
| `in_flight_sweeper` | evicts stuck in_flight symbols after 60s (daemon) |
| Signal processor workers | ThreadPoolExecutor, 5 workers (`worker_count: 5`) |
| EntryGate workers | ThreadPoolExecutor (separate pool for gate workers) |
| Order monitor poll | polls broker every 2s |
| Order reconciler poll | reconciles every 15s |
| SmartTgtManager | async candle-close consumer for SL trailing |
| KiteTicker (live_feed) | WebSocket receive thread |
| CandleStore | internal OHLC aggregation thread |
| `clock_skew_probe` | probes broker time every 60s (live mode only) |
| `token_monitor` | checks token validity every 1800s |

Communication: all threads share in-memory objects (FundManager, OrderMonitor, StateStore). StateStore uses threading.local per-thread SQLite connections.

**A.5 Systemd unit files**

`trading-system.service` (condensed):
```
ExecStart=/home/ubuntu/systems/venv/bin/python .../main.py --mode paper
KillSignal=SIGINT
TimeoutStopSec=30
Restart=on-failure
RestartSec=10
RestartPreventExitStatus=3
```

`alert-watcher.service`:
```
ExecStart=/home/ubuntu/systems/venv/bin/python .../scripts/alert_watcher.py
KillSignal=SIGINT
Restart=always
RestartSec=10
```

`token-watcher.service` (root):
```
ExecStart=/bin/bash .../deploy/token_watcher.sh
Restart=always
RestartSec=10
```

`trading-watchman.service`:
```
ExecStart=/home/ubuntu/systems/venv/bin/python .../scripts/gemini_watchman.py
BindsTo=trading-system.service
Restart=on-failure
RestartSec=30
PATH=/home/ubuntu/tools/antigravity:/home/ubuntu/tools/gemini/node_modules/.bin:/home/ubuntu/systems/venv/bin:/usr/bin:/bin
```

**A.6 Restart policy**
- `Restart=on-failure`, `RestartSec=10`
- `RestartPreventExitStatus=3` (startup check failure — no restart loop)
- `StartLimitBurst` / `StartLimitIntervalSec`: NOT set in unit file → systemd defaults apply (5 starts in 10s)

**A.7 Background task queue**
None (no celery/rq/asyncio). Pure Python `threading.ThreadPoolExecutor` for signal workers.

**A.8 Python version on VM**
UNCERTAIN — not determinable from code. Uses `from __future__ import annotations` (requires Python 3.7+). venv exists at `/home/ubuntu/systems/venv/`.
*To verify: `ssh trading_vm_secure 'python3 --version'`*

**A.9 venv path**
`/home/ubuntu/systems/venv/` — shared across projects in `systems/` directory (confirmed by VM architecture docs).

**A.10 Network ports**
- Port 5000: Chartink webhook receiver (Flask/Waitress, bind 0.0.0.0)
- Port 8080: External healthcheck server (FIX-132 Item 15, `scripts/healthcheck_server.py`)
- Port 22: SSH (standard)
*Live port check requires VM: `ssh trading_vm_secure 'ss -tlnp'`*

---

## SECTION B — SYSTEM FLOW & STATE MACHINE

**B.1 Complete signal → order lifecycle**

```
Chartink POST /webhook/<scanner_name>
  → WebhookReceiver._handle_webhook()         [signals/webhook_receiver.py]
  → WebhookReceiver._process_request()
    → HMAC check (if require_hmac=true)
    → market_hours check
    → kill_switch check
    → symbol exclusion list check
    → signal expiry check (600s)
    → SHA-256 dedup fingerprint check (5-min window)
    → IN_PROCESS check (per-symbol lock)
    → cast numeric fields
    → insert signals table (status=ACCEPTED)
    → signal_queue.put_nowait()
  → HTTP 200 {"accepted": N}

signal_queue → SignalProcessor dispatcher thread
  → ThreadPoolExecutor.submit(_process_one_safe)
  → SignalProcessor._process_one()
    → Step 1: kill_switch.is_active("entry")
    → Step 2: market_windows check (entry hours)
    → Step 3: strategy lookup via scan_webhook_map
    → Step 4: SecondaryScreener.screen()      [screening/secondary_screener.py]
               → StepExecutor runs N steps (quote, candles, etc.)
               → QualityScorer.score()
               → writes screener_results table
    → Step 5: _derive_prices() (entry offset + SL)
    → Step 6: _derive_target() (FIXED_PCT or RISK_REWARD)
    → Step 7: PositionSizer.compute()
    → Step 8: RiskEngine.approve()
    → Step 9: FundManager.reserve()           [capital/fund_manager.py]
    → Step 10: EntryGate.add() (pullback gate)
      → blocks until price_hit or timeout
      → on PRICE_HIT: signal_processor.continue_from_gate()
    → Step 11 (FIX-070): second kill_switch check
    → Step 12: OrderPlacer.place()            [orders/order_placer.py]
               → FullEntryEngine (CoPlusTgt or LimitTriple protocol)
               → ZerodhaAdapter.place_order()
               → OrderMonitor.track()
               → fund_manager.commit_to_used()
```

**B.2 Position fill → exit lifecycle**

```
OrderMonitor polls broker every 2s
  → fill detected → OrderStateMachine transition → publish OrderFilled
  → OrderPlacer._on_fill_event()
    → ENTRY fill: update trades.status=OPEN, place SL+TGT orders
    → SL fill: close_trade(exit_reason=SL_HIT), fund_manager.release_used()
    → TGT fill: close_trade(exit_reason=TGT_HIT), cancel SL order

SmartTgtManager: on each candle close
  → trails SL upward (LONG) / downward (SHORT) if trigger_pct threshold hit
  → ZerodhaAdapter.modify_order() → update smart_tgt_state table

EodSquareoff at 15:17 IST:
  → soft_kill()
  → cancel all INTRADAY/CO ENTRY orders
  → MARKET exit for all OPEN positions
  → log eod_squareoff_log table
  → publish EodSquareoffComplete
  → optional soft_kill resume
```

**B.3 Signal states**

Webhook-layer statuses (written immediately):
`ACCEPTED` | `DUPLICATE` | `EXPIRED` | `INVALID_SYMBOL` | `INVALID_PRICE` | `QUEUE_FULL` | `OUTSIDE_HOURS` | `IN_PROCESS`

Pipeline statuses (updated by signal_processor):
`TRADED` → became a live trade
`REJECTED_<step>` → pipeline rejection (e.g. `REJECTED_KILL_SWITCH`, `REJECTED_SCREENER_SCORE`, `REJECTED_RISK_ENGINE`, `REJECTED_CAPITAL_INSUFFICIENT`)
`DROPPED_*` → post-queue drops

**B.4 Order states**
`PENDING` → `OPEN` / `SUBMITTED` → `COMPLETE` | `CANCELLED` | `REJECTED` | `TRIGGER_PENDING`
(from orders.status column; state machine in `broker/order_state_machine.py`)

**B.5 Trade/position states**
`PENDING_FILL` → `OPEN` | `PARTIAL` → `CLOSED` | `CANCELLED` | `FAILED`
(from trades.status column)

**B.6 Central state stores**

| Store | Survives Restart? | Rehydrated? |
|-------|-------------------|-------------|
| SQLite DB (trading_system.db) | YES | N/A (persisted) |
| FundManager._reservations (dict) | NO | YES — from fm_ledger via rehydrate_from_open_trades() |
| OrderMonitor._tracked_orders | NO | YES — order_monitor.rehydrate_from_store() |
| OrderPlacer._fill_map | NO | YES — order_placer.rehydrate_fill_map() |
| SmartTgtManager tracked trades | NO | YES — from smart_tgt_state table |
| EntryGate._watchlist | NO | YES — from gate_state table (Audit 4.4) |
| signal_queue | NO | LOST (in-flight signals abandoned on crash) |
| WebhookReceiver._in_flight | NO | LOST (30s TTL sweeper clears on restart) |

**B.7 How system knows it has an open position in RELIANCE**
Primary: DB query `SELECT * FROM trades WHERE symbol='RELIANCE' AND status='OPEN'`.
Secondary: FundManager._reservations (in-memory) mirrors active commitments.
Sync: rehydrate_from_open_trades() rebuilds in-memory from DB on warm start.

**B.8 In-flight signals/orders during restart**
- Signals in queue: LOST (Python queue is in-memory). Chartink may re-send if it retries.
- Open orders: REHYDRATED via order_monitor.rehydrate_from_store() from orders table.
- Capital state: REBUILT via fund_manager.rehydrate_from_open_trades() from fm_ledger.
- Kill switch: LOADED from kill_switch_state table; auto-cleared if from previous day.

---

## SECTION C — KILL SWITCH & SAFETY MECHANISMS

**C.1 Kill switch trigger conditions**

| Trigger | Kill Type | What It Stops | Recovery |
|---------|-----------|----------------|----------|
| Clock skew > 30s | SOFT_KILL | New entries | Automatic when skew drops |
| 3 consecutive API failures (auto-trip) | SOFT_KILL | New entries | `--resume` flag |
| BrokerAuthError | SOFT_KILL | New entries | Manual token refresh + restart |
| Orphan order detected | SOFT_KILL | New entries | `--resume` after fixing orphan |
| Daily loss limit breach (Rs 1L TEMP) | SOFT_KILL | New entries + fires EOD squareoff | Auto-clears next day |
| Token expiry detected | SOFT_KILL | New entries | Token refresh + `--resume` |
| Capital invariant violation (FM19) | HARD_KILL | ALL orders + exits | Manual investigation + `--resume` |
| 3 consecutive broker API failures (circuit_breaker) | HARD_KILL | ALL orders + exits | `--resume` |
| 15:15 circuit breaker (force_close_time) | SOFT_KILL | New entries; triggers EOD squareoff | Auto-clears |
| WebSocket 10 consecutive reconnect failures | SOFT_KILL | New entries | Restart service |

**C.2 Kill switch persistence**
DB table: `kill_switch_state` (single row, id=1). Persisted BEFORE in-memory state update (KS9). Also: stale state (previous day) auto-cleared by `clear_stale_state(today)` on startup.

**C.3 Hard kill sequence** (from kill_switch.py:hard_kill)
1. Acquire RLock
2. Persist `HARD_KILL` to kill_switch_state table
3. Update in-memory state
4. Publish `KillSwitchActivated` event (notifies all subscribers)
5. Call `on_hard_kill_cancel_fn` (cancels all open broker orders)
6. Release lock

**C.4 Manual steps to resume after hard kill**
```bash
sudo systemctl stop trading-system
python main.py --resume  # (or restart with --resume via systemd override)
# OR: sudo systemctl start trading-system with ExecStart override including --resume
```

**C.5 Kill switch fires mid-order placement**
The kill switch check at OrderPlacer is at `FIX-070` — AFTER the full pipeline, BEFORE ZerodhaAdapter.place_order() is called. If kill switch fires WHILE the Kite HTTP call is in-flight: the call completes or times out naturally; the fill (if any) will be reconciled by order_reconciler. No special handling for mid-call kill.

**C.6 Last-mile kill switch check**
In `signals/signal_processor.py` around line 722:
```python
# FIX-070: Second kill-switch check after pipeline processing.
if self._ks.is_active("entry"):
    self._fm.release(reservation_id, "kill_switch_after_pipeline")
    # abort placement
```

**C.7 Circuit breakers**

| Breaker | Threshold | Action | Config Location |
|---------|-----------|--------|-----------------|
| Daily loss limit | Rs 100,000 (TEMP, was Rs 10,000) | EOD squareoff + SOFT_KILL | system_config.yaml: capital.daily_loss_limit |
| Daily loss % | 100% (TEMP, was 5%) | SOFT_KILL | system_config.yaml: risk.daily_loss_limit_pct |
| Max open positions | 10 | Reject new signals | system_config.yaml: risk.max_open_positions |
| Max daily trades | 20 | Reject new signals | system_config.yaml: risk.max_daily_trades |
| Max sector exposure | 40% | Reject signal | system_config.yaml: risk.max_sector_exposure_pct |
| Max consecutive losses | 20 (TEMP, was 4) | Reject new signals | system_config.yaml: risk.max_consecutive_losses |
| Strategy circuit breaker | loss > 2x avg (pause before 12:00) | Pause strategy | system_config.yaml: strategy_circuit_breaker |
| Force close | 15:15 IST | Cancel pending entries + EOD squareoff | system_config.yaml: circuit_breaker.force_close_time |
| API failure (auto-trip) | 3 consecutive | SOFT_KILL | system_config.yaml: kill_switch.api_failure_threshold |
| API failure (hard kill) | 3 consecutive | HARD_KILL | system_config.yaml: circuit_breaker.max_api_failures |
| WebSocket reconnect | 10 failures | SOFT_KILL | system_config.yaml: live_feed.max_reconnect_attempts |
| Clock skew | 30s | SOFT_KILL | system_config.yaml: clock.halt_skew_sec |
| Partial fill timeout | 5 min | Cancel stuck ENTRY order | system_config.yaml: circuit_breaker.partial_fill_timeout_minutes |

**C.8 Scanner connectivity failure behavior**
Fixed by FIX-127 + FIX-D. The 10,652-restart loop root cause:
- Persisted SOFT_KILL from previous day + systemd Restart=on-failure + token-watcher triggered restarts
- Fix 1: `clear_stale_state(today)` — auto-clears previous-day kill switch state on startup
- Fix 2: Holiday sentinel file `.holiday_notified_YYYY-MM-DD` prevents duplicate holiday Telegram alerts
- Fix 3: `RestartPreventExitStatus=3` — startup check failure exits 3, systemd does NOT restart
- Scanner check has `scanner_check_delay_sec: 5.0` network stabilization delay

---

## SECTION D — WEBHOOK & SIGNAL INGESTION

**D.1 HTTP framework**
Flask app served by Waitress WSGI server (8 threads, connection_limit=100). Port 5000, bind 0.0.0.0. No nginx/reverse proxy — direct internet-facing.

**D.2 HMAC validation**
`require_hmac: false` in system_config.yaml (Chartink cannot sign payloads). In live mode `WEBHOOK_SECRET` is required and used as `?token=` URL parameter auth. True HMAC disabled — known gap, mitigated by Oracle Cloud security group + iptables.

**D.3 Webhook endpoint**
`POST /webhook/<scanner_name>` — `scanner_name` must match an entry in `config/scan_webhook_map.yaml`.
`GET /health` — returns queue depth and kill switch status.

**D.4 Signal dedup**
Window: 300 seconds (5 min), configured at `system_config.yaml: webhook.dedup_window_seconds`.
Key format: SHA-256 of `(scanner_name + symbol + trigger_minute)`. Stored in TTLCache(maxsize=10000, ttl=300). Also persisted via UNIQUE index on signals table `(fingerprint, fingerprint_date)`.

**D.5 Signal expiry**
600 seconds (10 min). Configured at `system_config.yaml: signal_queue.expiry_sec: 600`.
Checked at: (1) webhook ingestion vs `triggered_at`, (2) signal_processor pre-flight check.

**D.6 Backpressure**
Threshold: 80% of capacity = 240 signals → HTTP 503. X-Queue-Warning header at 60% fill (FIX-134 Item 35).

**D.7 Queue implementation**
`queue.Queue(maxsize=300)` — stdlib thread-safe FIFO. Capacity: 300 signals.

**D.8 If webhook_receiver crashes**
It runs as a daemon thread inside main.py (`webhook-server` thread using Waitress). If the thread crashes, systemd will restart the whole main.py process on next failure detection (via Restart=on-failure). NOT a separate process.

**D.9 Simultaneous webhooks — same symbol, different strategies**
IN_PROCESS lock is per-symbol (not per strategy). First signal acquires the lock; second signal for the same symbol receives `IN_PROCESS` status and is queued but the in-flight dict blocks concurrent processing. They process sequentially.

**D.10 IN_PROCESS tracking**
Mechanism: `WebhookReceiver._in_flight` Python dict (in-memory only, no DB persistence).
Entry: `{symbol: {'acquired_at': monotonic_ts, 'heartbeat_at': monotonic_ts}}`
Heartbeat: `signal_processor` calls heartbeat every cycle while processing.
Eviction: `_run_sweeper` daemon evicts if `(now - heartbeat_at) > 60s`.
Release: `in_flight_release_fn` called in `signal_processor._process_one_safe()` finally block (SP7).
Stuck symbol: cleared by 60s sweeper timeout, OR restart (lost on restart — in-memory only).

---

## SECTION E — SECONDARY SCREENING & SCORING

**E.1 Zerodha API calls in secondary screening**

| Call | Endpoint | Rate Limit | Timeout |
|------|----------|------------|---------|
| `get_quote()` | Kite quote API | 3/s burst | 10s (read_sec) |
| `historical_data()` (if ATR steps) | Kite historical | 2/s burst | 10s (read_sec) |

**E.2 Zerodha API down during screening**
Signal is REJECTED. SecondaryScreener raises exception on API failure → pipeline short-circuits with `REJECTED_<step_name>` status. No retry, no queue. Capital is NOT reserved yet so no cleanup needed.

**E.3 Scoring components**
Defined in `config/scoring_weights.yaml` and `screening/quality_scorer.py`. Components include step results from StepExecutor (proximity to trigger, volume, VWAP relationship, etc.). Weighted sum → tier (HIGH/MEDIUM/LOW).

**E.4 Minimum score threshold**
Per-strategy in `config/strategies/*.yaml` via `min_eligible_score` field. Stored in `screener_results.eligible_score` column (v14 schema).

**E.5 Screening latency**
UNCERTAIN — no measured baseline available from code. Hard deadline: `pipeline_timeout_sec: 30` (entire pipeline including screening). Individual step timeouts depend on Zerodha API latency (~100-500ms typical).

---

## SECTION F — CAPITAL & FUND MANAGEMENT

**F.1 Capital invariant equation**
```
available + reserved + used == total
```
Checked inside EVERY mutation before commit (FM2). Violation raises `CapitalInvariantViolation` → hard_kill (FM19).

**F.2 TEMP config values (ALL 7)**

| Parameter | TEMP Value | Production Value | Location |
|-----------|------------|-----------------|----------|
| `capital.daily_loss_limit` | 100,000 | 10,000 | system_config.yaml:71 |
| `risk.max_consecutive_losses` | 20 | 4 | system_config.yaml:99 |
| `risk.daily_loss_limit_pct` | 1.00 (100%) | 0.05 (5%) | system_config.yaml:100 |
| `order_reconciler.capital_drift_tolerance` | 100,000 | 50 | system_config.yaml:185 |
| `drift_handler.log_only_threshold_rs` | 100,000 | 250 | system_config.yaml:218 |
| `drift_handler.soft_kill_threshold_rs` | 200,000 | 1,000 | system_config.yaml:219 |
| `drift_handler.hard_kill_threshold_rs` | 500,000 | 2,500 | system_config.yaml:220 |

**F.3 Capital allocation mechanism**
`FundManager.reserve(amount, bucket, signal_id)` under `threading.RLock` (FM5).
Write-ahead: fm_ledger INSERT (entry_type=RESERVE) BEFORE in-memory mutation (FM10/BL-5).
reservation_id (16 hex chars) returned to caller for later release/commit.

**F.4 Capital release on trade close**
`FundManager.release_used(reservation_id, pnl, costs, direction)` under RLock.
Double-release prevention: reservation_id checked in `_reservations` dict; unknown reservation_id raises `ValueError`. fm_ledger row with entry_type=RELEASE_USED records the PnL.

**F.5 Paper trading capital**
Account LFL836 (primary): `paper_capital = 1,000,000` (Rs 10L) per `config/accounts.csv`.
Note: Live micro-capital is Rs 25,000 (set via broker margins in live mode, not this config value).

**F.6 Concurrent allocation (5 signals in 1 second)**
`threading.RLock` serializes all capital mutations. ThreadPoolExecutor workers queue behind the lock. No starvation risk — typical reserve() is ~1ms.

**F.7 Available capital < minimum position size**
`PositionSizer.compute()` returns qty=0 → pipeline raises `_PipelineReject("CAPITAL_INSUFFICIENT", ...)` → signal status `REJECTED_CAPITAL_INSUFFICIENT`. Capital already reserved: NOT reserved at this stage.

**F.8 Position sizing formula**
```python
required_margin = qty * entry_price / leverage   # FM4 (NOT notional)
risk_amount = qty * abs(entry_price - sl_price)  # per trade

# Size computation:
size_by_risk = risk_per_trade_pct * total / risk_per_share
size_by_capital = max_concentration_pct * total / entry_price
final_qty = min(size_by_risk, size_by_capital)
final_qty *= tier_multiplier  # HIGH=1.0, MEDIUM=0.70, LOW=0.50

# Guards: min_qty_threshold=1, lot_skew_rejection_threshold=0.25,
#         max_position_value_rs=50000, max_single_order_qty=10000
```
Inputs: total capital, risk_per_trade_pct (1%), max_concentration_pct (10%), SL distance, tier, leverage, instrument lot_size.

---

## SECTION G — ORDER MANAGEMENT & BROKER INTERACTION

**G.1 Order types**

| Protocol | Entry | Stop Loss | Target |
|----------|-------|-----------|--------|
| CO_PLUS_TGT | CO (Cover Order = LIMIT+SL) | Built into CO | LIMIT (separate) |
| LIMIT_TRIPLE | LIMIT | SL-M | LIMIT |
| Paper mode | Synthesized LIMIT | Synthesized SL-M | Synthesized LIMIT |

Paper and live use the same protocol — no divergence on order type decision (UNIFIED).

**G.2 Order placement timeout**
`read_sec: 10` (KiteConnect HTTP timeout). On timeout: `BrokerTimeoutError` → `record_api_failure()` → after 3 consecutive → SOFT_KILL.

**G.3 SL and TGT management**
NOT OCO. Manually managed:
- CO_PLUS_TGT: CO has built-in SL at broker. Target is a separate LIMIT. When CO fills, place LIMIT target. When target fills, cancel CO's exit leg.
- LIMIT_TRIPLE: Three separate orders. When SL-M fills, cancel LIMIT target and vice versa.
- SmartTgtManager: trails SL by cancel-and-replace (new SL-M order, supersedes old).

**G.4 Orphan order detection**
OrderReconciler runs every 15s. Check `SL_MISSING`: trade is OPEN but no live SL order found at broker → reconciliation_status=SL_MISSING → triggers naked position emergency exit (FIX-148). Orphan callback fires → SOFT_KILL.

**G.5 Order reconciliation**
Frequency: 15s (order_reconciler.poll_interval_sec).
Checks: MANUAL_CLOSE (broker says COMPLETE, system says OPEN), ORPHAN_ADOPTION, quantity mismatch, SL_MISSING, CANCEL_DRIFT.

**G.6 Partial fills**
`partial_fill_timeout_minutes: 5` — after 5 min with a PARTIAL fill on an ENTRY order, it is cancelled. The partially filled quantity goes through full close flow (not abandoned). Exit protection legs only placed after full entry fill.

**G.7 EOD squareoff**
Triggers at: 15:17 IST (3-min buffer before Zerodha RMS auto-squareoff at 15:20).
Sequence (EOD5): SOFT_KILL → cancel INTRADAY/CO ENTRY orders → MARKET exit for all OPEN positions → log eod_squareoff_log → publish EodSquareoffComplete → optional resume.
CNC (delivery) positions: NOT touched.
If squareoff order fails: logs WARNING, position remains open. order_monitor continues watching; next reconciler cycle may detect and escalate.

**G.8 Zerodha API rate limit handling**
Order: 8/s burst, 8/s sustained. Quote: 3/s. Historical: 2/s.
On 429: BL-6 exponential backoff: 0.2s → 0.4s → 0.8s → ... (max 5s). Max 3 retries in OrderPlacer. 4th step: SOFT_KILL.
RateLimiter token bucket: `broker.rate_limiter.acquire(category)` called BEFORE every broker API call.

**G.9 Specific broker error handling**
- "Order variety not allowed" / "RMS insufficient funds" (FIX-148): raises `OrderRejectedError`, signal logged as REJECTED, capital released.
- Naked position (no SL after fill): OrderReconciler detects SL_MISSING → emergency MARKET close via `_emergency_exit()`.
- "Insufficient funds": RMS handling — positions reduced or skipped.

**G.10 Fill timeout**
60s (`order_monitor.fill_timeout_sec`). On expiry: ZerodhaAdapter.cancel_order() called. If the cancel fails, log error and continue watching.

**G.11 Paper mode fill simulation**
LTP-gating enabled. Flow:
1. Paper order placed → OSM: PENDING → SUBMITTED
2. Daemon thread polls `broker_adapter.get_quote()` every 5s
3. If LTP crosses limit price → OSM: SUBMITTED → COMPLETE → publish OrderFilled
4. Max wait: 21600s (6h, covers full market day including exit orders)
5. Fill price = limit price (no slippage simulation by default; slippage engine wired but uses model from config/slippage_model.yaml)

---

## SECTION H — DATABASE & PERSISTENCE

**H.1 DB path and size**
Path on VM: `/home/ubuntu/systems/trading-system/data_store/trading_system.db`
Current size: UNCERTAIN — check with `ssh trading_vm_secure 'ls -lh ~/systems/trading-system/data_store/trading_system.db'`

**H.2 Schema version**
v24 (FIX-150: added system_metrics + system_metrics_daily tables). `EXPECTED_SCHEMA_VERSION = 24` in state_store.py.

**H.3 WAL mode**
YES — `PRAGMA journal_mode = WAL` applied to every connection (state_store.py:82). Also `PRAGMA synchronous = FULL` for crash survivability (FIX-076).

**H.4 WAL checkpoint cron**
YES — `scripts/wal_checkpoint.py` runs at `0 10 * * 1-5` UTC (= 16:00 IST) via cron.
Also: WAL checkpoint at shutdown (`store.checkpoint_wal()` in `_shutdown()`).

**H.5 Tables (27 active)**

| Table | Purpose |
|-------|---------|
| schema_meta | Schema version |
| signals | All incoming webhook signals |
| trades | Position lifecycle |
| orders | Per-broker-order rows |
| capital_snapshot | Current capital state (single row) |
| system_events | STARTUP/SHUTDOWN/CRASH/KILL_SWITCH events |
| session | Session metadata (single row) |
| fm_ledger | Write-ahead capital mutation log |
| kill_switch_state | Persisted kill switch state (single row) |
| webhook_audit | Per-POST audit log |
| eod_squareoff_log | EOD fire history |
| reconciliation_log | Reconciler action audit |
| screener_results | Per-signal screening outcome |
| smart_tgt_state | SL trailing state (crash recovery) |
| innings | Shadow tracker multi-inning |
| gate_state | EntryGate watchlist (crash recovery) |
| candles | 1-min OHLC from live feed |
| trade_excursions | MFE/MAE per trade |
| pnl_reconciliation | Broker vs system P&L comparison |
| telegram_alerts | Alert delivery log |
| trade_journal | Structured daily trade journal |
| position_reconciliation | Broker vs system position comparison |
| strategy_metrics | Per-strategy daily performance |
| shadow_trades | Simulated parallel trade outcomes |
| fno_ban | NSE F&O ban list |
| eod_verification | EOD verification results |
| cron_heartbeat | Cron job execution tracking |
| system_metrics | 5-min health snapshots |
| system_metrics_daily | Daily metric summaries |

Row counts: UNCERTAIN — query live DB.

**H.6 DB transaction usage**
All capital mutations use `store.transaction()` context manager (atomic).
Signal inserts, order writes, trade updates use transactions.
Some reads (e.g., fetch_all queries) are bare (no transaction — read-only, safe).

**H.7 Connection model**
`threading.local` per-thread SQLite connection (NOT pooling). Each thread opens its own connection. `check_same_thread` not explicitly overridden (each thread uses its own connection, no sharing). `busy_timeout = 30000` (30s wait on SQLITE_BUSY).

**H.8 SQLITE_BUSY handling**
`PRAGMA busy_timeout = 30000` — SQLite waits up to 30s before raising `OperationalError`. No application-level retry loop (relies on SQLite's internal busy handling).

**H.9 DB backup**
`scripts/backup_restore_drill.py` — monthly cron (1st of month, 03:00 IST). Tests backup AND restore. Destination path: UNCERTAIN — check script.

**H.10 DB indices**
Yes — comprehensive indices on all major query paths. Key ones:
- `idx_signals_fingerprint_today` (UNIQUE on dedup)
- `idx_trades_status`, `idx_trades_symbol`, `idx_trades_created_at`
- `idx_orders_trade_id`, `idx_orders_status`, `idx_orders_leg`
- `idx_fm_ledger_ts`, `idx_fm_ledger_reservation_id`, `idx_fm_ledger_session_id`
- `idx_candles_symbol_ts`
All foreign key columns are indexed. No known missing indices.

---

## SECTION I — TELEGRAM & ALERTS

**I.1 Telegram library**
Custom `alerts/telegram_notifier.py` — direct HTTP POST to `https://api.telegram.org/bot<token>/sendMessage`. No third-party bot library.

**I.2 Alert types**
System lifecycle: startup, shutdown, kill_switch, critical_failure, orphan_order.
Trading: daily_loss_limit, circuit_breaker, EOD_squareoff, EOD_pre_alert (14:45 warning).
Config/ops: config_changed, token_expiry, clock_skew.
AI reports: premarket_brief, eod_review, trade_coach, data_integrity.
Watchman: per-batch WARNING/ERROR/CRITICAL findings.

**I.3 Telegram API down**
Retry: `max_retries: 3`, `retry_backoff_seconds: 2.0`, `rate_limit_per_minute: 20`.
After retries: logged to `logs/failed_alerts.log` + sentinel `.flag` file written to `data_store/`.
alert-watcher.service picks up sentinel and sends email fallback.
Trading flow: NOT blocked. All Telegram sends are best-effort (exception caught silently).

**I.4 Telegram sending mode**
Fire-and-forget. `notifier.send()` is synchronous HTTP POST with short timeout but wrapped in try/except. Does NOT block trading operations.

**I.5 Email fallback (FIX-132 Item 10)**
Triggers: when Telegram fails for CRITICAL severity after all retries.
SMTP: Gmail smtp.gmail.com:587 TLS.
Config: `system_config.yaml: alerts.email_fallback`.
Env vars: `ALERT_EMAIL_USER`, `ALERT_EMAIL_PASSWORD`, `ALERT_EMAIL_TO`.
Delivery: `scripts/alert_watcher.py` processes sentinel files, sends email via SMTP.

---

## SECTION J — EOD REPORTS & CLEANUP

**J.1 EOD report trigger times**
15:40, 15:45, 15:50, 15:55, 16:00, 16:05, 16:10, 16:15, 16:20, 16:35, 16:40, 17:00 IST (see A.2 table).

**J.2 Files generated per EOD**

| File | Location | Content |
|------|----------|---------|
| `daily_report_YYYY-MM-DD.xlsx` | `reports/output/` | 7-sheet master report |
| `daily_review_YYYY-MM-DD.xlsx/.md` | `reports/daily/` | 9-section review (DB only) |
| `eod_review_YYYY-MM-DD.md` | `reports/log_review/` | AI log analysis |
| `watchman_YYYY-MM-DD.md` | `reports/watchman/` | Intraday WARNING batches |
| `trace_YYYY-MM-DD.md` | `reports/flow_trace/` | Live pipeline event trace (FIX-153) |
| `coach_YYYY-MM-DD.md` | `reports/coach/` | Trade quality grades |
| `brief_YYYY-MM-DD.md` | `reports/briefing/` | Next-morning premarket brief |
| `patterns_YYYY-MM-DD.md` | `reports/weekly_patterns/` | Sunday weekly analysis |
| `integrity_YYYY-MM-DD.md` | `reports/integrity/` | Candle data integrity |

**J.3 If EOD report fails**
Cron exits non-zero, logged to `logs/cron.log`. Does NOT affect next-day startup — reports are informational. Telegram alert NOT sent on report failure (no wiring for this).

**J.4 daily_cleanup.sh**
UNCERTAIN — script not found in codebase. The `eod_cleanup` cron job (15:50 IST) exists per check_cron_drift.py but the actual script was not located in `scripts/` or `deploy/`.

**J.5 Weekly/monthly cleanup**
Weekly: `scripts/gemini_weekly_patterns.py` — analyzes 5 trading days, saves to `reports/weekly_patterns/`.
Monthly: `scripts/backup_restore_drill.py` — backup + restore drill on 1st of month at 03:00 IST.

**J.6 Log rotation**
Foundation Rule 1.7 (FIX from memory: `feedback_log_rotation_fix.md`): `RotatingFileHandler` replaced with `FileHandler` — one log file per day. No mid-day duplicates.
Logrotate config: UNCERTAIN — not found in repo. VM-level logrotate may be configured separately.

---

## SECTION K — CRON JOBS

**K.1 Complete cron schedule (reconstructed from script docstrings)**
```
# Pre-market
?? ?? * * 1-5   auto_refresh_token          (08:00 IST — script not located)
30  3  * * 1-5  fetch_fno_ban               (08:30 IST / 03:00 UTC)
30  3  * * 1-5  premarket_healthcheck       (08:30 IST)
25  3  * * 1-5  gemini_premarket_brief      (08:55 IST)

# Market hours
*/5 3-10 * * 1-5  capture_metrics_baseline  (every 5 min, 09:00-16:00 IST)

# Post-market cleanup chain
10 10 * * 1-5   fetch_daily_candles         (15:40 IST)
15 10 * * 1-5   reconcile_positions         (15:45 IST)
20 10 * * 1-5   eod_cleanup                 (15:50 IST)
25 10 * * 1-5   eod_verify                  (15:55 IST)

# Reports chain
30 10 * * 1-5   daily_review                (16:00 IST)
 0 10 * * 1-5   wal_checkpoint              (16:00 IST)
 1 16 * * 1-5   generate_screened_stocks    (16:01 IST — literal from script)
 5 10 * * 1-5   daily_report                (16:05 IST)
10 10 * * 1-5   trade_journal               (16:10 IST)
15 10 * * 1-5   compute_strategy_metrics    (16:15 IST)
15 10 * * 1-5   reconcile_pnl               (16:15 IST)
35 10 * * 1-5   gemini_log_review           (16:35 IST)
40 10 * * 1-5   gemini_trade_coach          (16:40 IST)
 0 11 * * 1-5   gemini_data_integrity       (17:00 IST)
30 12 * * *     check_cron_drift            (18:00 IST)

# Weekly
 0 12 * * 0     gemini_weekly_patterns      (18:00 IST Sundays)

# Monthly
 0  21 1 * *    backup_restore_drill        (03:00 IST 1st of month)
```
Note: All IST times converted from UTC. Actual crontab on VM may differ — verify with `ssh trading_vm_secure 'crontab -l'`.

**K.2 Silent failure behavior**
Most scripts: exit non-zero → logged to cron.log. No Telegram alerts on cron failure (except check_cron_drift.py which monitors heartbeats and alerts on missing jobs). Critical gap: if a cleanup script fails silently, it may not be noticed until check_cron_drift runs at 18:00 IST.

**K.3 Duplicate cron entries**
UNCERTAIN — was flagged in a prior audit. Need to verify: `ssh trading_vm_secure 'crontab -l'`.

**K.4 Cron overlap with market hours**
`capture_metrics_baseline` runs every 5 min during 09:00-16:00 IST — read-only, DB SELECT only, no impact on trading.
All other crons: post-market (15:40+ IST) or pre-market (08:00-09:00 IST).

---

## SECTION L — ANTIGRAVITY / AGY INTEGRATION

**L.1 All agy scripts**

| Script | Purpose | Trigger | Schedule |
|--------|---------|---------|----------|
| `gemini_watchman.py` | Live log watchman + flow trace | systemd BindsTo trading-system | 09:15-16:30 market hours |
| `gemini_log_review.py` | EOD log analysis | cron | 16:35 IST Mon-Fri |
| `gemini_premarket_brief.py` | Pre-market operational brief | cron | 08:55 IST Mon-Fri |
| `gemini_trade_coach.py` | Trade quality coaching | cron | 16:40 IST Mon-Fri |
| `gemini_weekly_patterns.py` | 5-day recurring pattern analysis | cron | 18:00 IST Sundays |
| `gemini_data_integrity_check.py` | Candle data vs Zerodha comparison | cron | 17:00 IST Mon-Fri |

**L.2 Process isolation**
agy scripts run as isolated processes (cron/systemd). NOT inside the trading-system process. gemini_watchman is a SEPARATE systemd service (trading-watchman.service).

**L.3 Can agy modify trading system files?**
NO. AGENTS.md hard boundaries:
- READ: anything on VM
- WRITE: only reports/, docs/, /tmp/
- NEVER: edit .py/.yaml/.yml/.sh/.json/.csv/.db files, run git/systemctl, place broker orders
Isolation enforced by AGENTS.md charter (agy reads this at startup).

**L.4 agy crash impact on trading system**
Zero. trading-watchman.service is `BindsTo=trading-system.service` (watchman dies if trading-system dies). But trading-system is NOT bound to watchman — watchman can crash/restart independently with no trading impact.

**L.5 trading-watchman.service**
```
[Unit]
BindsTo=trading-system.service
[Service]
User=ubuntu
PATH=/home/ubuntu/tools/antigravity:/home/ubuntu/tools/gemini/node_modules/.bin:/home/ubuntu/systems/venv/bin:/usr/bin:/bin
ExecStart=/home/ubuntu/systems/venv/bin/python .../scripts/gemini_watchman.py
Restart=on-failure
RestartSec=30
```

**L.6 Flow trace**
Location: `reports/flow_trace/trace_YYYY-MM-DD.md`
Event keywords captured: SIGNAL, SCREEN, ORDER, FILL, PROTECTION, CLOSE, CAPITAL, SL_TRAIL
Written: raw lines appended directly from log (no API call) during market hours.
EOD summary: single `agy --print` call at market close, appended as "## EOD Summary" section.

---

## SECTION M — NETWORK & FIREWALL

**M.1 iptables**
UNCERTAIN — live VM only. Known rule from install_vm_services.sh: `INPUT -p tcp --dport 5000 -j ACCEPT` at position 5. Default Oracle Ubuntu chain ends with REJECT.
*Verify: `ssh trading_vm_secure 'sudo iptables -L -n'`*

**M.2 Oracle Cloud security list**
Open to internet: Port 5000 (Chartink webhooks), Port 22 (SSH), Port 8080 (healthcheck — added FIX-132).
*Verify via Oracle Cloud console.*

**M.3 Outbound connections**

| Host | Purpose | Protocol |
|------|---------|----------|
| api.kite.trade | Zerodha REST API | HTTPS |
| wss.kite.trade | Zerodha KiteTicker WebSocket | WSS |
| api.telegram.org | Telegram Bot API | HTTPS |
| www.nseindia.com | F&O ban list fetch | HTTPS |
| generativelanguage.googleapis.com | agy (Antigravity/Gemini API) — cron scripts only | HTTPS |
| NTP servers | System time sync | UDP 123 |

**M.4 VPN/tunnel**
No VPN. Direct internet from VM. SSH access via key: `trading_vm_secure` (no passphrase).

**M.5 Network drop handling**

| Connection | Reconnect Logic | Max Retries | Action on Failure |
|------------|-----------------|-------------|-------------------|
| Zerodha WebSocket | Exponential backoff: 1s→30s | 10 attempts | SOFT_KILL |
| Zerodha REST API | No retry (ZA11) | 1 attempt | BrokerTimeoutError → record_api_failure |
| Chartink webhook (inbound) | Waitress queue (connection_limit=100) | N/A | 503 if queue full |
| Telegram | 3 retries, 2s backoff | 3 | Email fallback via alert-watcher |

**M.6 NTP**
UNCERTAIN — check with `ssh trading_vm_secure 'timedatectl status'`. System has clock skew monitoring (halt at 30s). IST timezone confirmed (Oracle Cloud VM, memory locked at UTC+5:30).

---

## SECTION N — DISK, MEMORY & SYSTEM RESOURCES

**N.1-N.9: ALL UNCERTAIN**
These require live VM access. Commands to run:
```bash
ssh trading_vm_secure 'df -h'
ssh trading_vm_secure 'free -h'
ssh trading_vm_secure 'nproc'
ssh trading_vm_secure 'cat /proc/meminfo | grep -E "MemTotal|SwapTotal"'
ssh trading_vm_secure 'ulimit -a'
ssh trading_vm_secure 'ls -lh ~/systems/trading-system/data_store/trading_system.db'
ssh trading_vm_secure 'ls -lh ~/systems/trading-system/logs/*.log | tail -5'
```

DB growth rate: UNCERTAIN — estimated 1-5 MB/day based on 5-20 trades/day.
Log growth rate: UNCERTAIN — estimated 10-50 MB/day during market hours.
Disk monitoring: `scripts/check_disk_space.py` exists (FIX-132, disk monitor). Threshold: `logging.min_free_disk_gb: 2.0` (blocks startup if < 2GB free).
Swap: UNCERTAIN.

---

## SECTION O — SECURITY & CREDENTIALS

**O.1 Credential storage**
`EnvironmentFile=/home/ubuntu/systems/trading-system/.env` (injected via systemd).
Zerodha access_token: `data_store/session/zerodha_token.json` (written by zerodha_login.py).
API keys/secrets: `.env` file only (never hardcoded).

**O.2 Credentials in logs**
UNCERTAIN — would need to grep live logs. Code does NOT explicitly log API keys. `broker/zerodha_adapter.py` logs order IDs and prices, not credentials.

**O.3 .env in .gitignore**
YES — `.gitignore` line 6: `.env`

**O.4 SSH access**
`trading_vm_secure` SSH key (no passphrase, per `feedback_ssh_automation.md`). Only `ubuntu` user on VM per all systemd units.

**O.5 Webhook accessibility**
Open to internet on port 5000 (Oracle Cloud security list + iptables ACCEPT rule). No IP whitelisting. `require_hmac: false` in paper mode — known gap. In live mode: `WEBHOOK_SECRET` token param required.

**O.6 Trading system user**
`ubuntu` (all service files: `User=ubuntu`). Exception: `token-watcher.service` runs as root to allow `systemctl start trading-system`.

**O.7 File permissions**
UNCERTAIN — check on VM:
```bash
ssh trading_vm_secure 'ls -la ~/systems/trading-system/.env ~/systems/trading-system/data_store/trading_system.db ~/systems/trading-system/data_store/session/zerodha_token.json ~/systems/trading-system/config/'
```

---

## SECTION P — ERROR HANDLING & RECOVERY

**P.1 Error categorization**

| Category | Exception | Behavior |
|----------|-----------|----------|
| Recoverable | `BrokerTimeoutError`, `BrokerRateLimitError` | record_api_failure; retry or backoff |
| Broker auth | `BrokerAuthError` | SOFT_KILL + token invalidation at shutdown |
| Capital violation | `CapitalInvariantViolation` | HARD_KILL immediately |
| Startup failure | `StartupCheckFailed` | exit(3) — no restart |
| Signal rejection | `_PipelineReject` | REJECTED_<check> status; no crash |
| General | `Exception` | Caught in _process_one_safe; signal marked FAILED |

**P.2 Auto-restart vs manual intervention**
Auto-restart (Restart=on-failure): exit codes 1, 2.
Manual required: exit code 3 (startup check failure — RestartPreventExitStatus=3), exit 4 (HALT without --resume), exit 6 (token missing in live mode).

**P.3 Main loop exception handling**
Main loop is `_shutdown_event.wait()` — no exceptions possible here.
Top-level in `__main__`:
```python
except TradingSystemError as e:
    _log.critical("system_error", exc_info=True)
    sys.exit(1)
except Exception as e:
    _log.critical("unexpected_error", exc_info=True)
    sys.exit(2)
```

**P.4 Cold-start checks** (from run_all_startup_checks, 14+ checks)
1. Clock skew (NTP vs broker time)
2. Config hash diff (detect config changes since last session)
3. Scanner connectivity (pre-flight)
4. Config files present (all required YAMLs exist)
5. Required env secrets present
6. Instrument cache minimum row count (>= configured minimum)
7. Kill switch state (already handled by detect_startup_scenario)
8. Paper capital consistency (EF-7: FundManager matches adapter)
9. Disk space check (min_free_disk_gb: 2.0)
10. NTP check (FIX-129 Item 27)
11. Webhook endpoint reachable (post-Flask start)
12. F&O ban list fetch (startup check #14)
Plus: market holiday check, instance lock acquisition, port availability.

**P.5 Crash recovery on restart (mid-market CRASH scenario)**

| State | How Recovered |
|-------|---------------|
| Open positions | `fund_manager.rehydrate_from_open_trades()` from fm_ledger + trades |
| Pending entry orders | `order_monitor.rehydrate_from_store()` from orders table |
| SL/TGT fill map | `order_placer.rehydrate_fill_map()` from orders table |
| Capital state | Rebuilt from fm_ledger replay (RESERVE/COMMIT/RELEASE_USED entries) |
| Kill switch state | Loaded from kill_switch_state table; auto-cleared if previous day |
| Gate state | `entry_gate` rehydrates from gate_state table |
| Smart SL state | `smart_tgt_manager` rehydrates from smart_tgt_state table |
| In-flight signals | LOST — Chartink may re-send; or signals expire naturally |
| Signal queue | LOST — in-memory |

Startup reconciliation: `order_reconciler.reconcile_once()` fires at Phase 0f to detect MANUAL_CLOSE and other drifts that occurred while system was down.

**P.6 SIGTERM handler**
`KillSignal=SIGINT` in systemd unit (sends SIGINT, not SIGTERM). SIGINT/SIGTERM both set `_shutdown_event`.
Graceful shutdown sequence (30s `TimeoutStopSec`):
1. Set shutdown_event
2. Invalidate token if BrokerAuthError occurred
3. Stop webhook_receiver (reject new webhooks with 503)
4. Stop signal_processor
5. Stop entry_gate
6. Stop smart_tgt_manager
7. Stop order_reconciler
8. Cancel all ENTRY orders (FIX-129 Item 45)
9. Stop order_monitor
10. Stop clock_skew_probe (live only)
11. Stop token_monitor
12. Disconnect live_feed
13. Stop candle_store
14. Send "System Stopping" Telegram notification
15. Write SHUTDOWN system_event to DB
16. WAL checkpoint (PASSIVE)
17. Close DB connection

**P.7 SIGKILL behavior**
Immediate death. State left behind:
- DB: all committed transactions survive (WAL + FULL sync). No partial writes visible.
- in-memory: lost (fm_ledger, in_flight, signal queue)
- Next startup: CRASH scenario detected (no SHUTDOWN marker). All rehydration paths run.
- kill_switch: preserved in kill_switch_state table; auto-cleared if from previous day.
- Orders at broker: still live; order_monitor.rehydrate_from_store() + reconciler will find them.

---

## SECTION Q — TESTING INFRASTRUCTURE

**Q.1 Test counts**
2868 passed, 12 skipped, 0 failed (04-Jun-2026, commit 681955d).

**Q.2 Test organization**
```
tests/
  conftest.py           # shared fixtures
  fixtures/             # fixture data
  integration/          # end-to-end tests
    test_alerts_pipeline.py
    test_end_to_end_smoke.py
    test_full_signal_flow.py
  unit/                 # ~60 unit test files
    test_*.py           # one file per module
```

**Q.3 Real Zerodha API in tests**
NO — all broker calls mocked (MagicMock or stub adapter). `tests/integration/sim_kite.py` provides simulated Kite client.

**Q.4 Mocked in tests**
- Broker adapter: MagicMock or ZerodhaAdapter(paper_mode=True)
- DB: temp SQLite (TemporaryDirectory in conftest)
- Time: `now_ist()` mocked via monkeypatch where needed
- Network: no real HTTP calls in unit tests

**Q.5 Restart recovery tests**
YES — `tests/integration/test_full_signal_flow.py` and `tests/unit/test_eod_squareoff.py` cover warm-restart scenarios. `tests/unit/test_startup_checks.py` covers COLD/WARM/CRASH/HALT scenarios.

**Q.6 Kill switch activation + recovery tests**
YES — `tests/unit/test_kill_switch.py` covers all state transitions, soft_kill, hard_kill, resume, clear_stale_state.

**Q.7 Concurrent signal processing tests**
UNCERTAIN — `tests/unit/test_signal_processor.py` tests sequential processing; concurrent load via ThreadPoolExecutor not explicitly tested.

**Q.8 Known flaky tests**
12 skipped (stable, not flaky). One pre-existing skip in `tests/unit/test_secondary_screener.py` (module 30 known skip). Clock-related test in `test_clock_skew_probe.py` generates `PytestUnhandledThreadExceptionWarning` from MagicMock timeout bug (harmless, pre-existing).

---

## SECTION R — DEPLOYMENT & SYNC

**R.1 Deploy mechanism**
`git push origin main` triggers post-receive hook on VM bare repo that performs `git checkout` into `/home/ubuntu/systems/trading-system/`. Confirmed from push output: "Deploying main to /home/ubuntu/systems/trading-system... Deployment complete."

**R.2 PC↔VM parity verification**
Commit hash comparison: `git log -1 --oneline` on both PC and VM. After push, both should show same HEAD.

**R.3 Pre-deploy validation**
Process (not enforced): run full test suite locally before push. No CI gate — manual discipline.

**R.4 Restart after deploy**
Manual: `ssh trading_vm_secure 'sudo systemctl restart trading-system trading-watchman'`
OR: token-watcher auto-starts trading-system on next fresh token (e.g., next morning).

**R.5 Checking commit hash on VM**
```bash
ssh trading_vm_secure 'cd ~/systems/trading-system && git log -1 --oneline'
```

---

## SUMMARY

**Questions answered:** ~107 of ~120
**Marked UNCERTAIN:** ~13 (all require live VM command output: disk/memory/CPU stats, actual crontab, iptables live state, logrotate config, Python version, credential file permissions, DB row counts)
**Marked N/A:** 0

**Key crash test risk areas identified from this survey:**
1. **Signal queue loss on crash** — in-flight signals lost; depends on Chartink retrying
2. **HMAC disabled** — webhook is unauthenticated in paper mode
3. **TEMP values** — all 7 safety thresholds artificially raised; system will NOT kill switch on realistic losses
4. **accounts.csv paper_capital=1,000,000** — 10x higher than intended Rs 25K live capital
5. **in_flight persistence** — stuck symbols only cleared by 60s sweeper or restart
6. **SystemD StartLimitBurst** — not set; default 5 starts/10s may allow crash loops before backoff
7. **No StartLimitIntervalSec** — rapid successive crashes not rate-limited beyond default
8. **EOD squareoff failure handling** — soft: if squareoff MARKET order fails, position remains open
9. **Cron silent failures** — only detected at 18:00 IST by check_cron_drift, not in real-time
10. **Mid-pipeline kill switch** — if kill fires during Kite HTTP call, that call completes; fill may be received after kill

---

*Generated: 04-Jun-2026 | Source: code + config reads | Author: VS Code Claude (Sonnet 4.6)*
