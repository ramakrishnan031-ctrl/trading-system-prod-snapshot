# Secondary Filtration / Execution Pipeline — Architecture Audit

**Scope:** the complete lifecycle of a signal AFTER it reaches the VM — from HTTP arrival to broker order. Scanner logic is out of scope. Traced from source; every claim cites `file:function`.

**Baseline commit:** `8116b74` (trade_type Option A). Config: `config/system_config.yaml`, 15 scanners in `config/scan_webhook_map.yaml`.

---

## 1. SIGNAL ENTRY

**How a signal enters the VM.** A Chartink scanner fires an HTTP `POST` to the VM's Flask receiver.

- **Transport:** HTTP/Flask. `signals/webhook_receiver.py` → `WebhookReceiver`, served on `webhook.bind_host:bind_port` = `0.0.0.0:5000` (`config/system_config.yaml:180-187`). Not TLS-terminated in-process; firewall + `?token=` auth are the perimeter (`docs/SYSTEM_MAP.md`, G0 report).
- **Routes** (`webhook_receiver._register_routes`, line 252):
  - `POST /webhook/<scanner_name>` → `_handle_webhook`
  - `GET /health` → queue depth + kill-switch state
- **NOT a file/queue drop.** No file ingestion, no external MQ. The only queue is the in-process `queue.Queue` between receiver and processor.

**Signal format** (Chartink JSON body, parsed in `_process_request`, line 399):
```json
{ "stocks": "TVSSCS,RELIANCE",
  "trigger_prices": "312.5,2840.0",
  "triggered_at": "2:34 pm",
  "scan_name": "Gap Fade Long" }   // optional; must match URL if present
```
- `stocks` / `trigger_prices` are comma-separated **strings**, positionally zipped (`webhook_receiver.py:539-543`).
- `triggered_at` accepts `"%Y-%m-%d %H:%M:%S"`, `"%I:%M %p"`, or `"%H:%M"`; localized to IST immediately (`:509-522`).

**Validation performed at the edge** (in order, `_handle_webhook`/`_process_request`):

| Order | Check | Reject | Source |
|---|---|---|---|
| 1 | Graceful-shutdown flag set | 503 | `:348` |
| 2 | Per-source-IP token bucket (`per_ip_burst`=60, `refill`=5/s) | 429 | `:358`, `_PerIpRateLimiter` |
| 3 | HMAC (`X-Webhook-Signature`) or `?token=` auth | 401 | `:411-433` |
| 4 | Scanner in `scan_webhook_map.scanners` | 404 | `:437` |
| 5 | Kill switch active | 403 | `:441` |
| 6 | Queue backpressure ≥ `capacity*0.80` | 503 | `:449` |
| 7 | Outside entry window (`market_windows.is_entry_allowed`) | 403 | `:456` |
| 8 | JSON parse + object shape | 400 | `:461-470` |
| 9 | Numeric field cast (`price`, `entry_price` critical) | 400 | `_cast_numeric_fields:282` |
| 10 | Required fields `stocks/trigger_prices/triggered_at` | 400 | `:483` |
| 11 | `scan_name` (if present) matches URL scanner | 400 | `:494-502` |

**Per-symbol** (`_process_signal`, line 653), after edge checks:
- symbol alias resolution (`config/symbol_aliases.yaml`), excluded-symbol drop (`excluded_symbols`), empty-symbol → `INVALID_SYMBOL`, price ≤ 0 → `INVALID_PRICE`, age > `expiry_sec` (600s) → `EXPIRED`, **atomic in-flight symbol claim** (`_claim_in_flight`) → `IN_PROCESS`, **TTL dedup** on `(symbol, scanner)` 300s → `DUPLICATE`, SHA-256 fingerprint uniqueness in DB → `DUPLICATE`, then **INSERT into `signals` (status QUEUED)** and `queue.put_nowait` → `QUEUE_FULL` if full.

The receiver does **no** screening, sizing, strategy resolution, or placement (WR16). It writes a `webhook_audit` row for every POST regardless of outcome (`_write_audit`).

---

## 2. SIGNAL PIPELINE (the real flow)

There are **two producers** into the queue and **three pipeline entry functions** in `signals/signal_processor.py`:

- `_process_one` — the normal path (direct webhook signal).
- `continue_from_gate` — resumes a signal that was parked by the **EntryGate** pullback watcher (`screening/entry_gate.py`) once LTP hits the pullback price. Skips screening/price-derivation (already done); re-runs the last-mile gates + sizing/risk/reserve/place.
- `continue_from_retest` — resumes an S&R-V2 WAIT_FOR_RETEST candidate (dormant: `sr_detector.wait_for_retest_enabled=false`).

**Dispatcher:** `_dispatcher_loop` (single thread) drains the queue and submits each tuple to a `ThreadPoolExecutor(max_workers=5)` (`signal_processor:319`). Workers run `_process_one_safe` → `_process_one`.

### Actual `_process_one` order (line 626)

```
signal dequeued
      ↓
[FIX-007] order rate-limiter pre-check (re-queue if token bucket empty)   _process_one_safe:360
      ↓
status = PROCESSING
      ↓
STEP 1 — PRE-FLIGHT (cheap, no I/O)
   • kill_switch.is_active("entry")            → REJECTED_KILL_SWITCH      :664
   • market_windows.is_entry_allowed           → REJECTED_OUTSIDE_ENTRY_WINDOW :669
   • signal age > signal_expiry_sec (60s)      → REJECTED_EXPIRED          :675
   • shadow_tracker.is_tracking(symbol)        → REJECTED_SHADOW_INNING_ACTIVE :688
      ↓
STEP 2 — STRATEGY RESOLUTION
   • scan_webhook_map[scanner] → strategy_name → strategy_obj  → REJECTED_UNKNOWN_STRATEGY :713
   • strategy_will_trade() control gate (LAYER 0/1/3)          → REJECTED_STRATEGY_CONTROL / REJECTED_TRADE_TYPE :739
   • per-strategy entry window                                → REJECTED_OUTSIDE_ENTRY_WINDOW :757
   • strategy_governor.check() circuit breaker                → REJECTED_STRATEGY_CIRCUIT_BREAKER :765
      ↓
STEP 3 — SECONDARY SCREEN (secondary_screener.screen)         :778
   • MIS learned-blocklist pre-drop (dormant: mis_filter.enabled=false)
   • quote fetch (quote_fn) — SKIPPED_QUOTE_UNAVAILABLE on failure
   • circuit-proximity reject (NOCIL)          → REJECTED_CIRCUIT_PROXIMITY
   • 10-step executor + quality score          → REJECTED_STEP_ERROR / REJECTED_SCORE_<n> / REJECTED_SIGNAL_AGE
      ↓  (screener writes its own signal status; processor returns on fail)
STEP 4 — PRICE DERIVATION (_derive_prices)                    :808
   • entry = trigger ± entry_offset_pct; SL = FIXED_PCT; bounds; gap buffer
      → REJECTED_INVALID_DERIVED_PRICE / REJECTED_ZERO_SL
      ↓
[SNR-V2] retest divert (dormant)                              :822
      ↓
[FIX-067] momentum fresh-LTP re-anchor (non-pullback strats)  :858
      ↓
STEP 5 — POSITION SIZING (position_sizer.calculate)           :887
   → REJECTED_SIZING_<constraint>  (CAPITAL/RISK/CONCENTRATION/BELOW_MIN/…)
      ↓
━━━ portfolio_lock (single critical section, :924) ━━━
   STEP 6a — per-strategy cap (_enforce_strategy_position_cap) → REJECTED_STRATEGY_POSITION_LIMIT
   STEP 6b — risk_engine.approve() [10 checks]                → REJECTED_<check>
   STEP 7  — fund_manager.reserve()                           → REJECTED_RESERVE_FAILED
   status = RESERVED
━━━ end lock ━━━
      ↓
STEP 8 — PLACEMENT
   • kill_switch re-check (FIX-070)             → REJECTED_KILL_SWITCH_LATE   :1004
   • stop_event (shutdown) check                → REJECTED_SHUTDOWN           :1013
   • entry_throttle.admit(symbol)               → REJECTED_ENTRY_THROTTLED    :1025
   • order_placer.place(...)                    → (broker sub-pipeline, §8)   :1036
      ↓
STEP 9 — status = PROCESSED; SR observer (async, non-gating)  :1132
```

**Note the divergence from the "textbook" flow in the prompt:** duplicate/blacklist checks happen at the **receiver edge**, not in the processor. Risk/capital/position-limit are **fused into one `portfolio_lock` critical section** (approve+reserve are atomic to close a TOCTOU race). "Open position check" is a *risk_engine sub-check* (`DUPLICATE_SYMBOL`), not a separate stage.

---

## 3. EVERY SECONDARY FILTER

Filters are in three tiers: **(A) pipeline pre-flight** (`signal_processor`), **(B) the screener** (`screening/`), **(C) risk approval** (`capital/risk_engine`). Below, "order" = execution order within `_process_one`.

### Tier B — `screening/secondary_screener.py::screen` (Step 3)

| # | Filter | Purpose | Source | Reject status | Reject condition |
|---|---|---|---|---|---|
| B0 | MIS learned-blocklist | Pre-drop symbols the broker recently MIS-blocked | `secondary_screener._is_mis_blocked_decision` | `REJECTED_NOT_MIS_TRADABLE` | symbol in blocklist AND product=MIS AND `mis_filter.enabled` & not shadow (**dormant**) |
| B1 | Quote fetch | Get LTP/bid/ask/circuit for scoring | `screen:134` | `SKIPPED_QUOTE_UNAVAILABLE` | `quote_fn` raises / no quote |
| B2 | Circuit-proximity | Reject entries at/beyond exit-clamp ceiling (no profitable TGT/valid SL fits band) | `_circuit_proximity_reason` | `REJECTED_CIRCUIT_PROXIMITY` | entry ≥ `upper*(1-margin)` or ≤ `lower*(1+margin)`; `entry_gate.circuit_proximity_reject_enabled=true` |
| B3 | Step executor (10 steps) | Run all quality gates | `step_executor.run_all` | `REJECTED_STEP_ERROR` | any step raised (all 10 always run) |
| B4 | Quality score | Weighted 0-100 | `quality_scorer.score` | `REJECTED_SCORE_<n>` | `total_score < effective_min` (`strategy.min_score` or `min_pass_score`=60) |
| B5 | Signal-age (defense) | Freshness re-check | `screen:275` | `REJECTED_SIGNAL_AGE` | `step_results["signal_age"]==0.0` (>60s) |

### Tier B, inner — the 10 steps (`screening/step_executor.py`)

All 10 run (no short-circuit, SE7); each has a 5s per-step timeout (`step_timeout_sec`); score ∈ [0,1]. Weights live in `scoring_weights.yaml` (ScoringConfig).

| Step | Function | Inputs | Score→0.0 (reject) condition |
|---|---|---|---|
| 1 volume_surge | `_step_1` | volume, avg_volume_20d, `min_volume_surge` | volume ≤ avg×surge, **or avg_volume_20d missing** |
| 2 vwap_position | `_step_2` | ltp, vwap, direction | LONG ltp≤vwap / SHORT ltp≥vwap / no vwap |
| 3 atr_filter | `_step_3` | atr, ltp, `min_adr_pct` | ADR% < min, **or atr missing** |
| 4 rsi_range | `_step_4` | rsi, direction | out of band; **missing/OOB → 0.5 neutral** |
| 5 price_action | `_step_5` | ltp, open, day_high/low | body% strength (never hard-0) |
| 6 sector_strength | `_step_6` | sector | placeholder: 1.0 if sector else 0.5 |
| 7 time_of_day | `_step_7` | minutes since open | 0.5/1.0/0.8/0.5 by time-bucket |
| 8 spread_check | `_step_8` | bid, ask, `max_spread_pct` | spread% > max; missing → 0.5 |
| 9 circuit_check | `_step_9` | circuit_state | upper/lower circuit → 0.0 |
| 10 signal_age | `_step_10` | triggered_at | >60s → 0.0, >30s → 0.5 |

> **Data-availability caveat (important for §11):** the live Kite quote (`_build_market_data`) supplies `ltp/bid/ask/volume/vwap/open/day_high/day_low/circuit`, but **`atr`, `rsi`, `sector`, `avg_volume_20d`, `prev_close` are hard-coded `None`** (`secondary_screener.py:347-352`). So steps 1 (volume_surge) and 3 (atr_filter) currently **always score 0.0**, and step 4 (rsi) always 0.5. The proportional scorer (`quality_scorer`, FIX-042) excludes missing steps from the denominator, but steps that return 0.0 (not missing) still drag the score. This is a **live scoring-fidelity gap**.

### Tier A — pipeline pre-flight & control (Steps 1-2)

| Filter | Purpose | Source | Reject status |
|---|---|---|---|
| Kill switch (entry) | Global halt | `signal_processor:664` | `REJECTED_KILL_SWITCH` |
| Market window | Global entry window | `market_windows.is_entry_allowed:669` | `REJECTED_OUTSIDE_ENTRY_WINDOW` |
| Signal age | Expiry (60s) | `:675` | `REJECTED_EXPIRED` |
| Shadow-inning guard | No overlap real+simulated | `:688` | `REJECTED_SHADOW_INNING_ACTIVE` |
| Strategy-control | LAYER 0/1/3 (`strategies/control.py`) | `:739` | `REJECTED_STRATEGY_CONTROL` / `REJECTED_TRADE_TYPE` |
| Per-strategy window | Narrower YAML window | `is_entry_allowed_for_strategy:757` | `REJECTED_OUTSIDE_ENTRY_WINDOW` |
| Strategy governor | Per-strategy loss circuit breaker | `strategy_governor.check:765` | `REJECTED_STRATEGY_CIRCUIT_BREAKER` |
| Derived-price sanity | entry/SL validity | `_derive_prices:808` | `REJECTED_INVALID_DERIVED_PRICE` / `REJECTED_ZERO_SL` |
| Target sanity | degenerate TGT | `_derive_target:1385` | `REJECTED_TGT_DISTANCE_TOO_SMALL` / `REJECTED_UNKNOWN_TGT_METHOD` |
| Entry throttle | Burst/gap/cooldown | `entry_throttle.admit:1025` | `REJECTED_ENTRY_THROTTLED` |

### Tier C — `capital/risk_engine.py::approve` (Step 6b) — 10 checks, short-circuit

Executed in `_run_checks` in this exact order; snapshot read once (RE11/RE16):

1. **KILL_SWITCH** — kill active
2. **SIZING_VALID** — `sizing_result.success`
3. **CAPITAL** — bucket available ≥ `margin_required`
4. **OPEN_POSITIONS** — `max_open_positions`=5 (authoritative: `max(open+reservations, db_active)+1`; delivery uses `max_open_delivery_positions`=3)
5. **DAILY_TRADES** — `max_daily_trades`=10 (reservation-aware; delivery `max_daily_delivery_trades`=5)
6. **CONSECUTIVE_LOSSES** — `max_consecutive_losses`=4 (today-scoped)
7. **DAILY_LOSS** — `abs(realized[+unrealized]) ≥ daily_loss_limit_pct`(3%)×total; unrealized is **shadow** (`daily_loss_include_unrealized=false`)
8. **SECTOR_EXPOSURE** — existing+this ≤ `max_sector_exposure_pct`(40%)×total
9. **CONTRARY_POSITION** — no opposite-direction active position (wash-trade)
10. **DUPLICATE_SYMBOL** — no existing open/in-flight for symbol

---

## 4. SIGNAL PRIORITY

**There is no prioritization. The system is strict FIFO with concurrent workers.**

- Producer → `queue.Queue` (FIFO), `capacity=300` (`signal_queue.capacity`).
- `_dispatcher_loop` pops in arrival order and submits to a `ThreadPoolExecutor(max_workers=5)` (`signal_processor.worker_count=5`). So up to **5 signals process concurrently**, dequeued FIFO.
- **No scanner priority, no score-based ordering, no capital-based ordering, no timestamp sort beyond arrival, no randomness.**
- When multiple signals arrive together (Chartink open-bell burst → one POST → N symbols), each symbol is enqueued in list order (`_process_request:565`) and drained FIFO.
- The only ordering side-effects are **serialization** (not prioritization):
  - `portfolio_lock` serializes the approve+reserve critical section — first worker to grab the lock wins the last capital/position slot.
  - `entry_throttle` (`min_gap_between_entries_sec=20`, `entry_burst_max=3/60s`, `per_symbol_cooldown=300s`) spaces out *placed* entries; excess entries are **rejected** (`REJECTED_ENTRY_THROTTLED`), not queued/deferred.
- The Telegram alert line `"Priority rank: #1 of 1 candidates"` (`_emit_signal_alert:439`) is **cosmetic hard-coded text**, not a real ranking.

**Implication:** under contention (many signals, 5 open-position cap, 20s throttle), *whoever the OS scheduler runs first* wins the slot. There is no notion of "best signal first."

---

## 5. CAPITAL MANAGEMENT

| Concern | Mechanism | Value / Source |
|---|---|---|
| **Allocation (buckets)** | Fixed split intraday/positional | `intraday_bucket_pct=0.70`, `positional_bucket_pct=0.30`; `conditional_allocation_enabled=false` (`capital:`). Buckets in `FundManager`; no cross-borrow. |
| **Position sizing** | Risk-based min of 3 candidates × tier × perf | `position_sizer.calculate`. `qty = min(qty_by_risk, qty_by_capital, qty_by_concentration)` then `×tier_mult ×perf_weight`, lot-rounded. |
| **Per-trade risk** | 1% of total capital | `risk_per_trade_pct=0.01` → `qty_by_risk = floor(total×0.01 / sl_distance)` |
| **Concentration** | ≤10% capital in one symbol | `max_concentration_pct=0.10` → `qty_by_concentration` |
| **Leverage** | Margin per share | `leverage_map`: INTRADAY 5×, CO 6×, DELIVERY 1×, BO 5× (live margin via `get_live_margin_pct` if adapter wired, else static) |
| **Hard value cap** | Anomaly guard | `max_position_value_pct=0.40` — REJECT if `qty×entry > 40%×capital` (`position_sizer:476`) |
| **Qty explosion guard** | Penny-stock guard | `min_tick_size=0.05` (SL-distance floor), `max_single_order_qty=10000` |
| **Tier multipliers** | Score→size | HIGH 1.0 / MEDIUM 0.70 / LOW 0.50 (`tier_multipliers`) |
| **Perf weighting** | Win-rate → size | `dynamic_by_winrate=true`, `min_multiplier=0.5`, `max_multiplier=2.0` (PerformanceAllocator → `perf_weights`) |
| **Max exposure (portfolio)** | Concurrent open cap | `max_open_positions=5` (risk_engine check 4) |
| **Max exposure (sector)** | Per-sector | `max_sector_exposure_pct=0.40` (check 8) |
| **Per-scanner / per-strategy limit** | Concurrent per strategy | `strategy.max_concurrent_positions` (default **2**) enforced atomically in `_enforce_strategy_position_cap` inside `portfolio_lock`. **This is the closest thing to a per-scanner limit** (1 scanner ↔ 1 strategy). |
| **Daily trade limit** | Entries/day | `max_daily_trades=10` (check 5) |
| **Daily loss limit** | Realized loss halt | `daily_loss_limit_pct=0.03` (3% of capital, check 7); post-close absolute breach via FundManager |
| **Consecutive-loss halt** | Streak breaker | `max_consecutive_losses=4`, today-scoped (check 6) |
| **Reservation** | Capital held before fill | `fund_manager.reserve()` inside lock; committed on fill, released on reject/fail/timeout-recovery |

There is **no explicit per-scanner rupee budget** — scanners share the global buckets; the only per-scanner throttle is the per-strategy concurrent-position cap (2) and the per-symbol cooldown (300s).

---

## 6. DUPLICATE HANDLING

| Scenario | Handling | Source | Result |
|---|---|---|---|
| **Same stock, same scanner, within 300s** | TTLCache on `(symbol, scanner)` | `webhook_receiver:701-708` | `DUPLICATE` (no DB row, dropped at edge) |
| **Same stock, same scanner, same 300s fingerprint bucket** | SHA-256 fingerprint UNIQUE in `signals` | `:713-745` | `DUPLICATE` (DB IntegrityError) |
| **Same stock, DIFFERENT scanner, concurrent** | Atomic in-flight symbol claim (one symbol processed at a time) | `_claim_in_flight:768` | 2nd → `IN_PROCESS` (dropped) |
| **Same stock, DIFFERENT scanner, sequential** | Passes edge; caught downstream by `DUPLICATE_SYMBOL` risk check | `risk_engine:585` | `REJECTED_DUPLICATE_SYMBOL` |
| **Existing OPEN/in-flight position, same direction** | `has_active_position(symbol)` | `risk_engine:253,585` | `REJECTED_DUPLICATE_SYMBOL` |
| **Existing position, OPPOSITE direction (long vs short)** | `get_active_position_direction` wash-trade guard | `risk_engine:564-581` | `REJECTED_CONTRARY_POSITION` |
| **Existing pending order** | Counted in `count_in_flight_orders` / `has_active_position` | `risk_engine` | `REJECTED_DUPLICATE_SYMBOL` (or OPEN_POSITIONS cap) |
| **Rapid re-entry after exit (same symbol)** | Per-symbol cooldown 300s | `entry_throttle:98` | `REJECTED_ENTRY_THROTTLED` |

**Key point:** the system enforces **one live position per symbol** globally (DUPLICATE_SYMBOL + CONTRARY_POSITION). Two scanners firing the same stock cannot both open — the first to clear risk wins, the rest reject. In-flight symbol locking is at the receiver (in-memory, per-symbol), released by the processor in `finally`.

---

## 7. REJECTION REPORT (every reason)

### Edge (receiver) — HTTP-level, `signals/webhook_receiver.py`
`503 shutting_down` · `429 per-IP rate limit` · `401 HMAC/token` · `404 unknown scanner` · `403 kill switch` · `503 backpressure` · `403 outside window` · `400 malformed JSON` · `400 type-cast fail` · `400 missing field` · `400 scan_name mismatch` · per-symbol: `REJECTED_EXCLUDED_SYMBOL` · `INVALID_SYMBOL` · `INVALID_PRICE` · `EXPIRED` · `IN_PROCESS` · `DUPLICATE` · `QUEUE_FULL`.

### Pipeline — signal status `REJECTED_<CHECK>`, `signals/signal_processor.py`
`KILL_SWITCH` · `OUTSIDE_ENTRY_WINDOW` · `EXPIRED` · `SHADOW_INNING_ACTIVE` · `SHADOW_TRACKER_ERROR` · `UNKNOWN_STRATEGY` · `STRATEGY_CONTROL` · `TRADE_TYPE` · `STRATEGY_CIRCUIT_BREAKER` · `INVALID_DERIVED_PRICE` · `ZERO_SL` · `TGT_DISTANCE_TOO_SMALL` · `UNKNOWN_TGT_METHOD` · `REJECTED_NO_ATR_DATA` (HALT mode only) · `SIZING_<constraint>` · `SIZING_BROKER_ERROR` · `STRATEGY_POSITION_LIMIT` · `RISK_BROKER_ERROR` · `RESERVE_FAILED` · `RESERVE_BROKER_ERROR` · `KILL_SWITCH_LATE` · `SHUTDOWN` · `ENTRY_THROTTLED` · `RETEST_BAD_STRUCTURE` (retest path) · `PLACEMENT_FAILED` · `TIMEOUT` (ambiguous place timeout).

### Screener — `screening/secondary_screener.py`
`REJECTED_NOT_MIS_TRADABLE` · `SKIPPED_QUOTE_UNAVAILABLE` · `REJECTED_CIRCUIT_PROXIMITY` · `SKIPPED_EXECUTOR_ERROR` · `REJECTED_STEP_ERROR` · `SKIPPED_SCORER_ERROR` · `REJECTED_SCORE_<n>` · `REJECTED_SIGNAL_AGE`.

### Risk engine `failed_check` — `capital/risk_engine.py`
`KILL_SWITCH` · `SIZING_VALID` · `CAPITAL` · `OPEN_POSITIONS` · `DAILY_TRADES` · `CONSECUTIVE_LOSSES` · `DAILY_LOSS` · `SECTOR_EXPOSURE` · `CONTRARY_POSITION` · `DUPLICATE_SYMBOL`.

### Position sizer `constraint` — `capital/position_sizer.py`
`INVALID_SL_DISTANCE` · `QTY_EXPLOSION_GUARD` · `RISK`/`CAPITAL`/`CONCENTRATION` (raw_qty≤0) · `REJECTED_LOT_SKEW` · `POSITION_VALUE_CAP` · `BELOW_MIN` · `FLAT`.

### Order placer (broker sub-pipeline) — `orders/order_placer.py`
`RR_GATE_FAILED` (`min_effective_rr=1.0`) · `link_signal_trade failed` · `kill_switch_active_last_mile` / `_presubmit` · `past EOD entry cutoff` (`eod_entry_cutoff=15:15`) · `slippage_exceeded` (slippage guard) · `REJECTED_PRICE_DRIFT` (margin top-up failed) · `insufficient_liquidity` · `16388 insufficient margin` (after 1 retry) · CNC blocked (`delivery_enabled=false`, `zerodha_adapter:558`) · any `BrokerError`.

---

## 8. ORDER PIPELINE (after approval)

Entry point: `orders/order_placer.py::place` (`:769`). All pre-submit gates run **after** the trade DB row is created, so every failure has a clean FAILED/REJECTED path + reservation release (`_handle_placement_failure`).

```
place()  [signal_processor already reserved capital]
  ↓ FIX-025 slippage protection (adjust limit toward release_ltp, gate path)
  ↓ compute tgt_price (caller-supplied or internal RR)
  ↓ FIX-136 R:R gate (min_effective_rr=1.0)                → OrderRejectedError
  ↓ OM.create_trade(status=PENDING_FILL)  ← DB ROW FIRST (OP4)
  ↓ OM.link_signal_trade                                   → FAILED+release on error
  ↓ OP-LM1 kill-switch last-mile                           → FAILED+release
  ↓ update_trade_status(PENDING)
  ↓ FIX-073 EOD entry cutoff (15:15)                       → REJECTED
  ↓ FIX-128 slippage guard (LTP vs trigger; sl_fraction 0.22 / cap 5rs / hard 10rs)  → REJECTED
  ↓ FIX-075 price-drift margin top-up (0.5%)               → REJECTED if top-up fails
  ↓ FIX-134 liquidity check (LIVE only; spread≤0.5%, depth≥500)  → CANCELLED
  ↓ ── retry loop (BL-19 / FIX-072) ──
  │    A-3 kill-switch re-check (presubmit TOCTOU)         → FAILED+release
  │    engine.execute(...)   → FullEntryEngine routes protocol
  │       • 429 → retry (max_placer_retries=3, no sleep; bucket thaws)
  │       • 16388 margin → invalidate cache, retry once
  │       • other OrderRejectedError → single attempt, cleanup, raise
  ↓ EntryResult (ENTRY leg placed; SL/TGT DEFERRED to fill — naked-short fix OP-NS1)
  ↓ _persist_entry_orders (atomic, BL-8) + _fill_map + order_monitor.track
```

**Broker validation & submission** — `broker/zerodha_adapter.py::place_order` (`:478`), the single chokepoint:
1. `_validate_place_order` (symbol/side/qty/price/trigger sanity) — before burning a rate token.
2. `_snap_order_to_tick` — authoritative tick alignment (paper+live parity, FIX-181).
3. **Product coercion** (Option A): if `force_intraday_only` and intent≠INTRADAY → coerce to INTRADAY → resolves to MIS (`:544`). Second, independent MIS guarantee.
4. `product_resolver.resolve(intent,"zerodha")` → broker_code (MIS/CNC/CO).
5. **Delivery lock**: `broker_code=="CNC" and not delivery_enabled` → `OrderRejectedError` (`:558`).
6. `osm.register` (state machine PENDING).
7. Paper → `_paper_place_order` (synth fill). Live → `rl.acquire()` (rate limit) → `truncate_tag_for_broker(tag)` (≤16 char, defensive boundary guard) → `kite.place_order(...)`.
8. On kite exception → OSM FAILED + `_translate_broker_exception` (typed BrokerError). On success → OSM SUBMITTED, return `PlacedOrder`.

**Protocols** (`FullEntryEngine.execute` → `orders/order_protocol_{limit,co}.py`):
- `LIMIT_TRIPLE` (default): ENTRY LIMIT; SL+TGT deferred to fill (`place_deferred_exits`) at *actual filled qty* (closes partial-fill naked-short window).
- `CO_PLUS_TGT`: CO bracket (broker-managed SL) + separate TGT.
- DELIVERY: OCO GTT via `CncGttPlacer` (dormant; `delivery_enabled=false`).

**Retry policy:**
- **429 rate limit** → retry up to `rate_limit_backoff.max_placer_retries`=3, no explicit sleep (the adapter already `penalize()`d the bucket; next `acquire()` blocks = backoff). Safe: each protocol raise cancels its own legs (OP-BL19d).
- **16388 insufficient margin** → invalidate margin cache, retry once; second 16388 → REJECTED.
- **Other BrokerError** → single attempt, cleanup, raise (ZA11/OP7).
- **Signal-level 429 pre-check** (`_process_one_safe:360`) → re-queue the whole signal rather than block the worker.

**Timeout handling (A-2, ambiguous):** `BrokerTimeoutError` from `place()` means the order *may already be live*. The processor does **NOT** retry (would double-enter). It marks signal `TIMEOUT`, keeps the reservation, and hands ownership to the 15s reconciler recovery (`_recover_in_flight_entries`), which correlates the entry by broker tag and adopts+protects or fails on confirmed absence (`signal_processor:1053-1073`).

**Failure handling:** every path routes through `_handle_placement_failure` → cancel broker orders (best-effort, logs `CANCEL_FAILED_MANUAL_INTERVENTION_REQUIRED`) → mark trade FAILED/REJECTED → release reservation → optional `hard_kill` only when DB persist fails *after* broker accepted (capital-tracking breach, OP-BL8e).

**Logging:** structured logs at every step (`order_placer.place_start`, `.entry_slippage_observed`, `.slippage_guard_exceeded`, `.price_drift_*`, `full_entry_engine.execute`, `place_order call_start/end`). Telegram alerts: INTRADAY SIGNAL (pre-place), ORDER PLACED (fill), SLIPPAGE GUARD, TGT/SL HIT.

**Fill lifecycle (async, event-driven):** `OrderPlacer` subscribes to `OrderFilled` / `OrderPartiallyTerminated` / `OrderStatusChanged`. On ENTRY fill → commit capital → record fill → place deferred SL+TGT at actual qty → register `SmartTgtManager`. Monitored by `broker/order_monitor.py` (poll `order_monitor.poll_interval_sec=2`) and the 15s `order_reconciler` (CHECK1-9). This is beyond "signal→order" but is where the order actually becomes a protected position.

---

## 9. CONFIGURATION (everything affecting signal processing)

| Config key | Meaning | Default | Source file |
|---|---|---|---|
| `trading_hours.entry_start` | Global entry window open | `10:00` | system_config.yaml |
| `trading_hours.entry_end` | Global entry window close | `15:00` | ″ |
| `trading_hours.eod_entry_cutoff` | Absolute placement deadline | `15:15` | ″ |
| `signal_queue.capacity` | Queue max | `300` | ″ |
| `signal_queue.backpressure_pct` | 503 threshold | `0.80` | ″ |
| `signal_queue.expiry_sec` | Edge signal expiry | `600` | ″ |
| `excluded_symbols` | Edge symbol drop | 5 symbols | ″ |
| `force_intraday_only` | MIS-only breaker (LAYER 0) | `true` | ″ |
| `trade_type` | Master product gate (LAYER 1) | `INTRADAY` | ″ |
| `delivery_enabled` | CNC master lock | `false` | ″ |
| `mis_filter.{enabled,shadow,ttl_days}` | MIS learned blocklist | `false/true/1` | ″ |
| `capital.intraday_bucket_pct` / `positional_bucket_pct` | Bucket split | `0.70/0.30` | ″ |
| `capital.leverage_map` | Margin per intent | 5/6/1/5 | ″ |
| `position_sizing.enabled` | Tier×perf vs flat | `true` | ″ |
| `position_sizing.risk_per_trade_pct` | Per-trade risk | `0.01` | ″ |
| `position_sizing.max_concentration_pct` | Per-symbol cap | `0.10` | ″ |
| `position_sizing.max_position_value_pct` | Hard qty×price cap | `0.40` | ″ |
| `position_sizing.min_tick_size` / `max_single_order_qty` | Explosion guards | `0.05/10000` | ″ |
| `position_sizing.tier_multipliers` | HIGH/MED/LOW | 1.0/0.7/0.5 | ″ |
| `position_sizing.{dynamic_by_winrate,min_multiplier,max_multiplier}` | Perf sizing | true/0.5/2.0 | ″ |
| `risk.max_open_positions` | Portfolio concurrent cap | `5` | ″ |
| `risk.max_daily_trades` | Entries/day | `10` | ″ |
| `risk.max_sector_exposure_pct` | Sector cap | `0.40` | ″ |
| `risk.max_consecutive_losses` | Streak halt | `4` | ″ |
| `risk.daily_loss_limit_pct` | Daily loss | `0.03` | ″ |
| `risk.daily_loss_include_unrealized` | MTM enforcement | `false` (shadow) | ″ |
| `risk.max_open_delivery_positions` / `max_daily_delivery_trades` | Delivery caps | `3/5` (inert) | ″ |
| `webhook.{require_hmac,dedup_window_seconds}` | Auth / dedup TTL | `false/300` | ″ |
| `webhook.per_ip_{burst,refill_per_sec}` | IP token bucket | `60/5.0` | ″ |
| `signal_processor.worker_count` | Concurrency | `5` | ″ |
| `signal_processor.pipeline_timeout_sec` | Per-signal deadline | `30` | ″ |
| `signal_processor.tgt_min_pct` | Degenerate-TGT guard | `0.003` | ″ |
| `signal_processor.min_gap_between_entries_sec` | Throttle gap | `20` | ″ |
| `signal_processor.entry_burst_max` / `entry_burst_window_sec` | Burst throttle | `3/60` | ″ |
| `signal_processor.per_symbol_cooldown_sec` | Re-entry cooldown | `300` | ″ |
| `scoring_weights.yaml min_pass_score` | Score gate | `60` | scoring_weights.yaml |
| `scoring_weights.yaml high/medium_score_threshold` | Tiers | (config) | ″ |
| `entry_gate.circuit_proximity_reject_enabled` | NOCIL reject | `true` | system_config.yaml |
| `entry_gate.max_entry_slippage_pct` | Coarse slippage abort | `1.0` | ″ |
| `entry_gate.min_effective_rr` | R:R gate | `1.0` | ″ |
| `entry_gate.slippage_control.*` | sl_fraction 0.22 / cap 5 / hard 10 | — | ″ |
| `entry_gate.{liquidity_check_enabled,max_spread_pct,min_depth_qty}` | Liquidity | true/0.5/500 | ″ |
| `strategy_circuit_breaker.*` | Per-strategy loss pause | enabled/2.0/12:00/10d | ″ |
| `kill_switch.api_failure_threshold` | Auto soft-kill | `3` | ″ |
| Per-strategy YAML: `enabled`, `intent`, `direction`, `min_score`, `max_concurrent_positions`, `entry_start/end_time`, `entry_method`, `entry_offset_pct`, `sl_method`, `sl_pct`, `sl_min/max_pct`, `sl_gap_buffer_pct`, `tgt_method`, `tgt_pct`, `tgt_risk_reward`, `pullback_wait_enabled`, `min_volume_surge`, `min_adr_pct`, `max_spread_pct`, `lot_size` | Strategy behavior | — | `config/strategies/*.yaml` |

---

## 10. BOTTLENECKS

**Network I/O on the hot path (the dominant latency).** For a single non-pullback signal, the pipeline makes up to **4 sequential broker/quote round-trips** before the order even submits:
1. `secondary_screener` quote fetch (Step 3).
2. `FIX-067` momentum fresh-LTP re-anchor (`signal_processor:861`).
3. `order_placer` slippage-guard LTP (`_fetch_ltp`, `:1002`).
4. `order_placer` price-drift LTP (`_fetch_ltp`, `:1096`) + (LIVE) liquidity quote (`_check_liquidity`).

Each is a Kite call subject to the shared `RateLimiter`. On a burst these serialize behind the token bucket. This 2-4s of pipeline latency is exactly why FIX-067 re-anchors (the trigger price goes stale) — a symptom, not a fix, of the latency.

**Sequential per-signal, 5-wide.** `worker_count=5`. A 40-symbol open-bell burst processes 5-at-a-time; combined with `min_gap_between_entries_sec=20` and `entry_burst_max=3/60s`, **at most 3 entries can be placed per minute** regardless of how many signals qualify. The rest reject `ENTRY_THROTTLED`. This is a deliberate safety valve but a **hard throughput ceiling** — with 26+7 new strategies firing, most qualifying signals will be throttle-rejected, not placed.

**`portfolio_lock` global serialization.** approve+reserve for *every* signal runs under one lock (`signal_processor:924`). Correct (closes TOCTOU) but means capital admission is single-threaded — the 5 workers converge to 1 here. Under heavy load this is the serialization point.

**`step_executor` per-step thread + 5s timeout.** Each of the 10 steps is submitted to a single-worker `ThreadPoolExecutor` with a 5s `future.result(timeout=...)` (`step_executor:110`). Steps are pure CPU (no I/O) so the timeout rarely fires, but the submit/await adds overhead per step per signal, and a single shared 1-worker executor serializes steps within a signal.

**Duplicate/redundant quote fetches.** Slippage-guard LTP and price-drift LTP are two separate `_fetch_ltp` calls for the same symbol microseconds apart (`order_placer:1002` and `:1096`) — could be one.

**Race conditions — mostly closed, by design:**
- In-flight symbol claim (M-1 atomic), fingerprint UNIQUE, dedup TTL — receiver races closed.
- OPEN_POSITIONS / DAILY_TRADES reservation-aware authoritative counts (FIX-185, Bug B) — cap-overshoot races closed.
- Per-strategy cap inside `portfolio_lock` (H-7) — burst race closed.
- Kill-switch re-checks at 3 points (pre-flight, post-pipeline FIX-070, pre-submit A-3) — kill TOCTOU closed.
- Ambiguous place-timeout handed to single-owner recovery (A-2) — double-entry closed.

**Non-bottleneck but worth noting:** the SR observer, shadow tracker, zone warmer are all async/non-gating (fire-and-return), so they don't add hot-path latency.

---

## 11. ARCHITECTURE REVIEW

**Excellent (keep, do not touch):**
- **The fused `portfolio_lock` approve+reserve critical section.** Single source of atomicity for capital+position admission; the reservation-aware authoritative counts are genuinely well-engineered against restart-burst races. This is the safety keystone.
- **The single broker chokepoint** (`zerodha_adapter.place_order`) with tick-snap + product coercion + delivery lock + tag truncation all in one place. Double-locked MIS-only guarantee (control gate + product coercion).
- **Deferred SL/TGT at actual fill qty** (naked-short fix, OP-NS1) — correct handling of partial fills.
- **Ambiguous-timeout single-owner recovery** (A-2) — the discipline of "don't retry an ambiguous placement" is exactly right.
- **The strategy-control resolver** (`strategies/control.py`) as the single source of truth shared by gate + status table — cannot drift.
- **Idempotent capital reservation with exactly-once release** across every reject/fail/timeout path.

**Acceptable (works, but shows age):**
- The `_process_one` function is ~590 lines with deeply nested try/except and three near-duplicate copies (`_process_one`, `continue_from_gate`, `continue_from_retest`). Correct but hard to maintain; the three paths drift-risk on future changes.
- The FIX-/audit-tag naming convention (FIX-190, H-7, A-2…) is institutional memory but opaque to newcomers.
- Screener persists to DB on every signal (2 writes) even for rejects — fine at current volume.

**Should be redesigned:**
- **The 10-step scoring is running on mostly-absent data.** `atr`, `rsi`, `sector`, `avg_volume_20d` are hard-`None` in `_build_market_data`, so volume_surge and atr_filter **always score 0.0** and rsi is always neutral. The quality score is effectively driven by vwap_position, spread, circuit, time-of-day, price-action, signal-age. **Before adding 26+7 strategies, wire a real instrument/indicator cache** or drop the dead steps — otherwise `min_pass_score=60` is being met on a thin, partly-degenerate signal.
- **Throughput ceiling vs. new strategy count.** 5 workers + 3-entries/60s throttle + 5 open-position cap was tuned for 15 scanners. 48 strategies will mostly hit `ENTRY_THROTTLED` / `OPEN_POSITIONS`. Either the caps scale with capital or most new scanners are decorative.
- **Redundant hot-path quote fetches** (§10) — consolidate to one LTP snapshot passed through the placement gates.

**Should be removed / consolidated:**
- The cosmetic `"Priority rank: #1 of 1"` alert text (implies ranking that doesn't exist).
- One of the two `_fetch_ltp` calls in `order_placer.place`.
- Dead scoring steps (or make them real).

**Never change:**
- `force_intraday_only` + `delivery_enabled` locks and the product-coercion chokepoint — these are the reason no CNC has leaked. The double-lock is intentional; keep both.
- The `portfolio_lock` atomic boundary.
- The A-2 no-retry-on-ambiguous-timeout rule.

---

## 12. FUTURE COMPATIBILITY (15 → 26 intraday + 7 BTST)

| Capability | Supported today? | What must change |
|---|---|---|
| **15 redesigned scanners** | ✅ Yes | 1:1 `scan_webhook_map` entry per scanner + a `config/strategies/<name>.yaml`. Startup validates every scanner maps to a loaded strategy. |
| **26 intraday strategies** | ⚠️ Structurally yes, operationally throttled | Add 26 map+YAML pairs. **But** `worker_count=5`, `entry_burst_max=3/60s`, `max_open_positions=5`, per-strategy cap 2 will throttle-reject most of them. Must scale worker_count, throttle windows, and position caps with capital, or accept that most scanners never place. No code change to *route* them; heavy config/capacity change to *trade* them. |
| **7 BTST strategies** | ❌ Not without unlocking delivery | BTST = hold overnight = **DELIVERY/CNC intent**. Today `force_intraday_only=true` + `delivery_enabled=false` + `trade_type=INTRADAY` mean any DELIVERY-intent strategy is **dormant** (control gate returns WON'T TRADE) and CNC is hard-blocked at the adapter. The CNC path (`CncGttPlacer`, OCO GTT overnight protection, delivery caps) is fully built but **inert**. Enabling requires: `delivery_enabled=true` + `force_intraday_only=false` + `trade_type∈{DELIVERY,BOTH}` **and** passing the T2 real-API GTT/TPIN proof (currently deferred). This is the single biggest gap for BTST. |
| **Market filter** | ⚠️ Partial | No index/breadth "market regime" filter exists. Individual gates (VWAP, circuit, time-of-day) are per-symbol. A market filter would be a **new pre-flight gate** in `_process_one` Step 1 or a new screener step — additive, no architectural break. |
| **Trade ranking** | ❌ Absent | FIFO only (§4). Real ranking needs a **batching layer**: collect candidates in a window, score, sort, admit top-N under capital. This is a genuine architectural addition — the current design admits signals independently as they arrive; there is no "compare candidates" stage. |
| **AI scoring** | ⚠️ Seam exists | `quality_scorer` is a pluggable weighted scorer; an AI score could be an added step or a replacement scorer. The SR detector already demonstrates the async-observer seam. Additive. |
| **Risk engine** | ✅ Present & extensible | `risk_engine.approve` is a clean 10-check gate; adding checks is low-risk (append to `_run_checks`). Already has delivery-aware branches. |
| **Watchlist engine** | ✅ Present (EntryGate) | `screening/entry_gate.py` is a price-watch/pullback engine with restart-safe rehydration. A richer watchlist would extend it. |
| **Execution engine** | ✅ Present | `FullEntryEngine` + protocols + adapter. Protocol-pluggable (LIMIT_TRIPLE / CO_PLUS_TGT / GTT). MARKET route exists. Solid. |

**Bottom line for future-compat:** routing 48 strategies is trivial (config). *Trading* them needs (a) BTST → unlock the delivery lifecycle (biggest item), (b) capacity/throttle/cap scaling, (c) fixing the degenerate scoring data before more strategies lean on `min_pass_score`, and (d) a **new batching+ranking stage** if "best signal first" is desired — the current FIFO/independent-admission model has no place to rank.

---

## 13. FINAL FLOWCHART (arrival → order)

```
                    Chartink POST /webhook/<scanner>
                                │
                    ┌───────────▼─────────────┐
                    │  WebhookReceiver (edge)  │
                    │ shutdown?503 IPrate?429  │
                    │ auth?401 scanner?404     │
                    │ kill?403 backpr?503      │
                    │ window?403 JSON?400      │
                    │ fields?400 castfail?400  │
                    └───────────┬─────────────┘
                    per-symbol: alias→excluded?→INVALID?→EXPIRED?
                     in-flight claim?IN_PROCESS  dedup?DUPLICATE
                                │  INSERT signals(QUEUED)
                                ▼
                        queue.Queue (FIFO, cap 300)
                                │
                    _dispatcher_loop → ThreadPoolExecutor(5)
                                │  (FIX-007 order-rate pre-check → re-queue)
                                ▼
         ┌──────────────  _process_one  ──────────────┐
         │ status=PROCESSING                          │
         │ STEP1  kill? window? age>60? shadow-inning?│───►REJECTED_*
         │ STEP2  scan_map→strategy; control gate     │───►REJECTED_STRATEGY_CONTROL/TRADE_TYPE
         │        per-strategy window; gov circuit    │───►REJECTED_*
         │ STEP3  screener: MISblock→quote→circuit    │───►REJECTED_CIRCUIT_PROXIMITY
         │        →10 steps→score<60?                 │───►REJECTED_SCORE_n / STEP_ERROR
         │ STEP4  derive entry/SL (bounds, gap)       │───►REJECTED_INVALID_DERIVED_PRICE
         │        [retest divert — dormant]           │
         │        [FIX-067 fresh-LTP re-anchor]       │
         │ STEP5  position_sizer (risk/cap/conc/tier) │───►REJECTED_SIZING_*
         │ ┌───── portfolio_lock ─────┐               │
         │ │ per-strategy cap (2)     │───►REJECTED_STRATEGY_POSITION_LIMIT
         │ │ risk.approve() 10 checks │───►REJECTED_{CAPITAL,OPEN_POSITIONS,
         │ │                          │    DAILY_TRADES,CONSEC_LOSSES,DAILY_LOSS,
         │ │                          │    SECTOR,CONTRARY,DUPLICATE}
         │ │ fund.reserve()           │───►REJECTED_RESERVE_FAILED
         │ │ status=RESERVED          │               │
         │ └──────────────────────────┘               │
         │ STEP8  kill re-check? shutdown? throttle?  │───►REJECTED_{KILL_LATE,SHUTDOWN,THROTTLED}
         │        order_placer.place() ───────────────┼──────────┐
         └────────────────────────────────────────────┘          │
                                                                  ▼
                              ┌────────  order_placer.place  ────────┐
                              │ RR gate(1.0)? create_trade(PENDING)  │
                              │ link? kill-LM? EOD cutoff(15:15)?    │───►FAILED/REJECTED
                              │ slippage guard? price-drift topup?   │───►REJECTED (+release)
                              │ liquidity(LIVE)?                     │───►CANCELLED
                              │ retry loop: A-3 kill re-check        │
                              │   engine.execute → protocol          │
                              │     LIMIT_TRIPLE: ENTRY (SL/TGT       │
                              │     deferred to fill)                │
                              └───────────────┬──────────────────────┘
                                              ▼
                        zerodha_adapter.place_order (chokepoint)
                        validate→ticksnap→coerce MIS(force)→
                        resolve product→CNC lock(delivery=false)?→
                        OSM register→ kite.place_order (tag≤16)
                              │        429→retry3  16388→retry1
                              ▼
                     PlacedOrder (SUBMITTED) → _fill_map + order_monitor.track
                              │
                    ┌─────────▼──────────┐   (async, event-driven)
                    │ OrderFilled event  │
                    │ commit capital →   │
                    │ place SL+TGT @ fill│
                    │ qty → SmartTgt →   │
                    │ status=OPEN        │
                    └────────────────────┘
                     [15s order_reconciler CHECK1-9 backstop;
                      ambiguous timeout→recovery adopts/fails]
```

---

### Appendix — files touched in this trace
`signals/webhook_receiver.py` · `signals/signal_processor.py` · `signals/entry_throttle.py` · `screening/secondary_screener.py` · `screening/step_executor.py` · `screening/quality_scorer.py` · `screening/entry_gate.py` · `strategies/control.py` · `capital/position_sizer.py` · `capital/risk_engine.py` · `capital/fund_manager.py` (reserve/snapshot) · `orders/order_placer.py` · `orders/full_entry_engine.py` · `broker/zerodha_adapter.py` · `config/system_config.yaml` · `config/scan_webhook_map.yaml`.
