# G2a — Capacity Inventory: Configured Limits ↔ Live Counters

**Phase:** G2a (Ops Dashboard) · **Date:** 2026-07-03 IST · **Scope:** every configured limit/quota → its live "used" counter → "remaining".
**Sources (read-only, by value — no production imports):** `config/system_config.yaml`, `config/strategies/*.yaml` (15), `core/schema.sql` (v42), risk/capital check codes from `capital/risk_engine.py`, reject-status codes from `signals/signal_processor.py`.
**Consumer:** `ops_dashboard/backend/services/capacity.py` (v1 rows) + later G2b M20 groups (System/Risk/Capital/Strategies/Scanners/Execution/SmartTarget/Slippage).

> **"today" basis:** IST calendar date (fixed +05:30, India has no DST) computed in `services/freshness.py::ist_today_iso()`; date-prefix match on ISO timestamp columns (`col LIKE 'YYYY-MM-%'`) or the GENERATED `date` columns on `webhook_audit`/`fm_ledger`.
> **Capital basis:** day-opening capital = `fm_ledger` `INIT.balance_after` of the bucket for today (fallback: `capital_snapshot.cash_floor + margin_used + margin_reserved`). Percentage limits are resolved to ₹ against this. If no snapshot yet (pre-open), ₹ limits render `— (awaiting opening capital)` rather than a wrong number.

## A. Primary daily quotas — SHOWN in v1 Dashboard "Daily Capacity Monitor"

| # | config key (dotted) | scope | meaning | LIVE COUNTER source (table.column + WHERE / formula) | Remaining formula | v1? |
|---|---|---|---|---|---|---|
| 1 | `risk.max_daily_trades` = 10 | global / day | hard cap on entries placed per day | `COUNT(*) FROM trades WHERE created_at LIKE '<today>%' AND status NOT GLOB 'REJECTED*'` (a trade row is created at capital reservation; rejected-pre-reservation signals never create a trade row) | `max(0, 10 − used)` | **Y** |
| 2 | `risk.max_open_positions` = 5 | global / concurrent | portfolio-wide cap on concurrent open positions | `COUNT(*) FROM trades WHERE status IN ('OPEN','PARTIAL','PENDING_FILL','EXITING')` (not date-filtered — "currently open") | `max(0, 5 − open)` | **Y** |
| 3 | `risk.daily_loss_limit_pct` = 0.03 | global / day | SOLE daily-loss authority = 3% of capital | loss ₹ = `−SUM(pnl_delta) FROM fm_ledger WHERE date='<today>' AND entry_type='RELEASE_USED' AND pnl_delta<0` (realized losses today; clean per-trade realized, NOT `get_daily_realized_net_pnl` — that reader sums ALL `pnl_delta` rows including the EOD `RESET_PNL` counter-entry, which post-15:17 would zero the day's realized. **25-Jul-2026 RE-LABEL:** its W10 cost double-subtract was fixed 2026-07-17 and is no longer the reason); limit ₹ = `0.03 × opening_capital` | `max(0, limit₹ − loss₹)` | **Y** |
| 4 | `capital.intraday_bucket_pct` = 0.70 | global / instant | intraday (MIS/CO) capital bucket ceiling | used ₹ = `capital_snapshot.margin_used` (+`margin_reserved` for pending); limit ₹ = `0.70 × opening_capital` (fixed split; `conditional_allocation_enabled=false`) | `max(0, limit₹ − used₹)` | **Y** |
| 5 | `risk.max_consecutive_losses` = 5 | global / rolling | halt after N consecutive losing trades | streak = count of trailing `net_pnl<0` in `SELECT net_pnl FROM trades WHERE status IN ('CLOSED','CLOSED_MANUAL') AND net_pnl IS NOT NULL ORDER BY exit_time DESC` until first `net_pnl>=0` | `max(0, 5 − streak)` | **Y** |
| 6 | `signal_queue.capacity` = 300 (`backpressure_pct` = 0.80) | global / instant | webhook signal queue depth before HTTP 503 | live depth from `metrics_client` → `:8080/health.queue_size` (trader's in-memory queue; NOT persisted to DB). Trader down ⇒ `unavailable` | `capacity − queue_size`; 503 at `0.80×300 = 240` | **Y** (unavailable when trader down) |
| 7 | `risk.max_open_delivery_positions` = 3 | global / concurrent (delivery) | concurrent CNC positions cap | `COUNT(DISTINCT t.trade_id) FROM trades t JOIN orders o ON o.trade_id=t.trade_id AND o.leg='ENTRY' AND o.product='CNC' WHERE t.status IN ('OPEN','PARTIAL','PENDING_FILL','EXITING')` | `max(0, 3 − used)` | **Y — marked INERT** (`force_intraday_only=true`) |
| 8 | `risk.max_daily_delivery_trades` = 5 | global / day (delivery) | CNC entries per day cap | `COUNT(DISTINCT o.trade_id) FROM orders o WHERE o.leg='ENTRY' AND o.product='CNC' AND o.placed_at LIKE '<today>%'` | `max(0, 5 − used)` | **Y — marked INERT** |

## B. Per-strategy quotas — SHOWN in v1 Strategy panel (not the capacity widget)

| # | config key | scope | meaning | LIVE COUNTER source | Remaining | v1? |
|---|---|---|---|---|---|---|
| 9 | `strategies/<s>.max_concurrent_positions` (default 2; gap_fade=3) | per-strategy / concurrent | concurrent open per strategy | `COUNT(*) FROM trades WHERE strategy='<s>' AND status IN ('OPEN','PARTIAL','PENDING_FILL','EXITING')` | `max(0, cap − open_for_strategy)` | **Y** (strategy panel) |
| 10 | `strategies/<s>.enabled` | per-strategy / switch | strategy on/off (loaded once; RESTART to change) | config snapshot `config_json` or `config/strategies/<s>.yaml enabled` | n/a (boolean) | **Y** (strategy panel) |

## C. Per-trade / per-order guards — config-only (no daily counter) — v1 shows value, "used" is per-event

| # | config key | scope | meaning | LIVE COUNTER (per-event, not a running total) | v1? |
|---|---|---|---|---|---|
| 11 | `position_sizing.risk_per_trade_pct` = 0.01 | per-trade | 1% of capital risked per trade (sizing input) | applied per trade at sizing; per-trade `trades.risk_amount` is the realized value | Y (value only) |
| 12 | `position_sizing.max_concentration_pct` = 0.10 | per-symbol | ≤10% of capital in one symbol | per-symbol `SUM(actual_position_value_rs)` of open trades ÷ opening_capital | N — G2b M-Capital (needs per-symbol table) |
| 13 | `position_sizing.max_position_value_pct` = 0.40 | per-trade | hard cap qty×price ≤ 40% capital (anomaly guard) | per-trade guard; no running total | N — G2b (value shown in System group) |
| 14 | `position_sizing.max_single_order_qty` = 10000 | per-order | sanity cap on computed qty | per-order guard | N — G2b |
| 15 | `position_sizing.min_qty_threshold` = 1 / `min_tick_size` = 0.05 / `lot_skew_rejection_threshold` = 0.25 | per-trade | penny/lot/skew guards | per-event reject → `signals.status='REJECTED_LOT_SKEW'` etc. | N — G2b |
| 16 | `position_sizing.tier_multipliers` (HIGH 1.0/MED 0.70/LOW 0.50) + `dynamic_by_winrate` + `min_multiplier`/`max_multiplier` (0.5/2.0) | per-trade | score-tier × perf-weight sizing state | runtime state (PerformanceAllocator, in-memory; not persisted per-snapshot) | N — G2b M-SmartTarget/Strategies |
| 17 | `risk.max_sector_exposure_pct` = 0.40 | per-sector | ≤40% of capital in one sector | per-sector `SUM(actual_position_value_rs)` grouped by `trades.sector` ÷ opening_capital | N — G2b M-Capital (sector aggregation) |

## D. Throughput / rate / window controls — v1 partial

| # | config key | scope | meaning | LIVE COUNTER source | v1? |
|---|---|---|---|---|---|
| 18 | `signal_processor.entry_burst_max` = 3 / `entry_burst_window_sec` = 60 | global / rolling 60s | max 3 placed entries per 60s (anti-burst) | derived: `COUNT(*) FROM orders WHERE leg='ENTRY' AND placed_at >= now−60s` | N — G2b M-Execution (derived window) |
| 19 | `signal_processor.min_gap_between_entries_sec` = 20 / `per_symbol_cooldown_sec` = 300 | global / per-symbol | spacing between placed entries | in-memory throttle state (not persisted) | N — G2b M-Execution |
| 20 | `webhook.per_ip_burst` = 60 / `per_ip_refill_per_sec` = 5.0 | per-IP / instant | webhook per-IP token bucket → 429 | in-memory token bucket in the trader (NOT persisted); 429s visible only as `webhook_audit.response_code=429` counts | N — G2b M-Scanners (429 count derivable) |
| 21 | `trading_hours.entry_start`=10:00 / `entry_end`=15:00 / `eod_squareoff_time`=15:17 | global / time | daily entry window + square-off | `freshness.py` market-clock (drives GRAY-vs-RED + summary window badge) | **Y** (window status, via freshness/summary — not a "quota" row) |
| 22 | `kill_switch.api_failure_threshold` = 3 (`enable_auto_trip`) | global / rolling | consecutive API failures → auto SOFT_KILL | in-memory `_api_failure_count` (not persisted); current kill state IS in `kill_switch_state` | Partial — kill STATE shown in v1 header; the counter is not persisted |

## E. Config-only escalation / tolerance settings — G2b M-groups (v1 = not shown)

| # | config key | meaning | v1? |
|---|---|---|---|
| 23 | `entry_gate.slippage_control.*` (max_slippage_fraction 0.22 / absolute_cap_rs 5.0 / hard_max_slippage_rs 10.0) + `max_entry_slippage_pct` 1.0 | entry-slippage abort budget | N — G2b M-Slippage |
| 24 | `smart_tgt.*` (trigger_pct 0.005 / step_pct 0.003 / max_modify_failures 3) | SmartTgtManager trailing params | N — G2b M-SmartTarget |
| 25 | `drift_handler.*` (log 250 / soft_kill 1000 / hard_kill 2500 / cycles 3) | capital-drift escalation thresholds | N — G2b M-Risk (state = reconciler in-memory) |
| 26 | `strategy_circuit_breaker.*` (loss_multiplier 2.0 / cutoff 12:00 / lookback 10) | intraday strategy circuit breaker | N — G2b M-Strategies |
| 27 | `circuit_breaker.*` (partial_fill_timeout 5m / force_close 15:15 / max_api_failures 3) | position-level circuit breaker | N — G2b M-Execution |
| 28 | `order_reconciler.capital_drift_tolerance` 50 / `_pct` 0.10 / `human_order_margin_tolerance` 5000 | reconciler drift tolerances | N — G2b M-Risk |
| 29 | `clock.*` (warn 2 / alert 5 / halt 30s skew) | clock-skew tiers | N — G2b M-System |
| 30 | `live_feed.max_reconnect_attempts` 10 | WS reconnect → SOFT_KILL | N — G2b M-System |

---

## Ambiguity decisions (for Web Claude review)

- **D1 — "orders/trades used" counts CANCELLED/FAILED?** **DECISION: YES for the daily-trade counter (#1).** A `trades` row is created at capital *reservation*; a subsequently cancelled/failed entry still consumed a daily-trade attempt (the risk engine's `DAILY_TRADES` check counts at approval time). We count all non-`REJECTED*` trade rows created today. Rationale: matches the gate's own accounting; showing only filled trades would understate the quota and let the eye think there's more headroom than the engine allows. Pure-`REJECTED*` trade rows (never reserved) are excluded.
- **D2 — daily-loss "used" source.** **DECISION: `fm_ledger.RELEASE_USED.pnl_delta` (losses only), NOT `get_daily_realized_net_pnl`.** Rationale *as recorded at the time*: G0/W10 established `get_daily_realized_net_pnl` double-subtracts costs; `RELEASE_USED.pnl_delta` is the clean per-trade realized. **25-Jul-2026 — the DECISION stands, its REASON has changed:** W10 was fixed 2026-07-17, so the double-subtract is gone. The reader is still excluded because it sums ALL `pnl_delta` rows including the EOD `RESET_PNL` counter-entry, which post-15:17 would zero the day's realized loss. We take only negative deltas (loss consumed against the loss limit); positive P&L does not "refund" the loss budget within the day (the control is a floor on cumulative realized loss). Shown value = cumulative realized loss today.
- **D3 — capital "used" = margin_used only, or +margin_reserved?** **DECISION: show `margin_used` as the headline used, with `margin_reserved` (pending) added as a secondary "+pending" figure.** Rationale: the bucket ceiling binds on committed margin; pending reservations are transient. The widget shows `used / (used+pending) / limit` so an operator sees both.
- **D4 — opening capital when no `INIT` ledger row / no snapshot yet (pre-open).** **DECISION: fall back to `capital_snapshot.(cash_floor+margin_used+margin_reserved)`; if that too is absent, render ₹ limits as `— (awaiting opening capital)` and still show the count-based quotas (#1,#2,#5).** Rationale: never display a fabricated ₹ limit; count quotas don't depend on capital.
- **D5 — consecutive-loss streak definition.** **DECISION: trailing run of `net_pnl<0` over CLOSED/CLOSED_MANUAL trades ordered by `exit_time DESC`, stopping at the first `net_pnl>=0`; `net_pnl IS NULL` rows (unresolved) are skipped, not treated as wins/losses.** Rationale: mirrors the "consecutive losses" the kill-switch reacts to; a break-even (0) resets the streak (not a loss).
- **D6 — delivery caps (#7,#8) while `force_intraday_only=true`.** **DECISION: compute and show them, tagged `INERT (force_intraday_only)`.** Rationale: they're real configured limits; showing them inert (used will be 0) is honest and future-proofs the widget for when delivery is enabled — no GUI change needed later.
- **D7 — per-IP / entry-burst / kill api-failure counters are in-memory in the trader and NOT persisted.** **DECISION: v1 does not fabricate a live value for these; they're listed here and deferred to G2b where a derived proxy exists (e.g. `webhook_audit.response_code=429` count for per-IP, `orders.placed_at` window for entry-burst).** Rationale: isolation rule I2 (read-only DB) + no trader introspection API means the true bucket state is unreachable; a derived proxy is a G2b decision for Web Claude.
- **D8 — signal-queue depth (#6) requires the trader's `:8080/metrics`.** On the PC dev box (and whenever the trader is down) this is `unavailable`, never an error (isolation-safe). Rationale: it's the only live source; parity-safe (mode-agnostic).

**Row count:** 30 limit-type keys catalogued (8 primary quotas + 2 per-strategy + 20 guards/rate/window/escalation). v1 capacity widget renders rows #1–#8; strategy panel renders #9–#10; the remaining 20 are catalogued for G2b M-groups with their live-counter source pre-identified.
