# G2a — Capacity Inventory: Configured Limits ↔ Live Counters

> # 🏛️ AUTHORITY — **THIS DOCUMENT IS THE SINGLE SOURCE OF TRUTH FOR THE CONTROL INVENTORY**
>
> **RULING 1, TAKEN BY RAMA ON 07-Aug-2026.** Decision ledger row **1** (*"Which CONTROL INVENTORY
> survives — and, separately, where it lives"*), `docs/MASTER_PENDING_01-Aug-2026.md`.
>
> - **THIS FILE IS THE AUTHORITY.** Any statement about which controls exist, what they are keyed
>   on, or whether one binds, is settled here.
> - **The 27-row inventory at `docs/audit/audit_05jul2026.md:527` (§6.1) is SUPERSEDED.** It is
>   frozen, historical, and is **never to be updated again**. It remains readable as the 05-Jul
>   evidence set; ⛔ it is not to be cited as current.
> - **The 113-key config review (`docs/design/sizing/config_surface_review_06aug2026.md`) is
>   MERGED IN** (§F–§H below). It does **not** survive as a separate document of record; it is
>   retained as the *evidence* behind the merged axes, exactly as it always declared itself.
> - ⛔ **ANY FUTURE INVENTORY REVIEW COMPARES AGAINST THIS DOCUMENT.** It does not create a parallel
>   inventory. A review that cannot express its finding as a change to a row here is telling you
>   something about the row schema, not about the need for a second document.
> - **JOIN IDENTIFIER: the dotted config key** (`risk.max_daily_trades`, `position_sizing.tier_multipliers`).
>   Every row is reachable by it, and it is what any other document joins on.
>
> ### ⚠️ AMENDMENT, ADOPTED AT THE RULING'S FIRST APPLICATION (07-Aug-2026, same day)
> The dotted key is a sufficient **join** identifier and an insufficient **merge** identifier.
> Two documents can agree on every key and still disagree on what a *row* is. So the authority
> also declares, and a merging document must match or explicitly reconcile:
> - **ROW UNIT** — one row = **one configured limit that has a capacity semantic** (a ceiling, a
>   quota, a budget, a window, a threshold), together with the live counter that consumes it.
>   ⛔ Not "one YAML leaf key": a family whose keys are consumed as a unit is ONE row, and the row
>   states the family's **measured** leaf-key count.
> - **INCLUSION RULE** — a key is IN if changing it can change *how much* the system may do
>   (existence · size · execution · lifecycle · alerting). Pure transport/paths/logging/ports are OUT.
> - **VALUE PROVENANCE** — every value in this file is read from `config/system_config.yaml` at a
>   named date. ⛔ A value carried from a prior document without re-reading is not evidence.
>
> 🔴 **LOCATION IS A SEPARATE DECISION, NOT TAKEN.** This file lives under `ops_dashboard/docs/` — a
> subsystem folder holding a system-wide authority. That is a **known and accepted wart**. Moving it
> breaks `ops_dashboard/backend/services/capacity.py`'s consumer unless that changes in the same
> breath, so the move is its own card on its own day. ⛔ Do not move this file.

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
| 5 | `risk.max_consecutive_losses` = **4** <sub>(was recorded as 5 on 03-Jul and 05-Jul; **re-read 07-Aug** at `system_config.yaml:226` — see §H-1)</sub> | global / rolling | halt after N consecutive losing trades | streak = count of trailing `net_pnl<0` in `SELECT net_pnl FROM trades WHERE status IN ('CLOSED','CLOSED_MANUAL') AND net_pnl IS NOT NULL ORDER BY exit_time DESC` until first `net_pnl>=0` | `max(0, 5 − streak)` | **Y** |
| 6 | `signal_queue.capacity` = 300 (`backpressure_pct` = 0.80) | global / instant | webhook signal queue depth before HTTP 503 | live depth from `metrics_client` → `:8080/health.queue_size` (trader's in-memory queue; NOT persisted to DB). Trader down ⇒ `unavailable` | `capacity − queue_size`; 503 at `0.80×300 = 240` | **Y** (unavailable when trader down) |
| 7 | `risk.max_open_delivery_positions` = 3 | global / concurrent (delivery) | concurrent CNC positions cap | `COUNT(DISTINCT t.trade_id) FROM trades t JOIN orders o ON o.trade_id=t.trade_id AND o.leg='ENTRY' AND o.product='CNC' WHERE t.status IN ('OPEN','PARTIAL','PENDING_FILL','EXITING')` | `max(0, 3 − used)` | **Y** — ⚠️ **the "INERT" tag is SUPERSEDED: `force_intraday_only` is `false` and `delivery_enabled` is `true` @07-Aug (§H-2). This cap is LIVE.** |
| 8 | `risk.max_daily_delivery_trades` = 5 | global / day (delivery) | CNC entries per day cap | `COUNT(DISTINCT o.trade_id) FROM orders o WHERE o.leg='ENTRY' AND o.product='CNC' AND o.placed_at LIKE '<today>%'` | `max(0, 5 − used)` | **Y** — ⚠️ **"INERT" SUPERSEDED, same reason (§H-2). This cap is LIVE.** |

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

# 🔗 §F — MERGED AXES (Ruling 1's first application, 07-Aug-2026)

**Source merged in:** `docs/design/sizing/config_surface_review_06aug2026.md` (the "113-key review").
It always declared itself *"WORKING DOCUMENT. NOT AN AUTHORITY. NOT AN INVENTORY."* — that
self-label is what made it mergeable rather than a rival. It is retained as **evidence**;
⛔ it is no longer a document of record and must not be cited as one.

**All values below re-read from `config/system_config.yaml` on 07-Aug-2026 (repo HEAD `98e7406`),
not carried from either source document.** Corpus at that read: **301 leaf keys across 42 sections**
by YAML parse.

Three axes now attach to every row above, and to the new rows in §G:

| axis | values | what it answers |
|---|---|---|
| **pipeline** | `INTRADAY` · `DELIVERY` · `BOTH` · `per-intent` | which pipeline consumes this limit |
| **delivery twin?** | ✅ a `delivery_*` variant exists · ❌ none | can delivery be tuned without moving intraday |
| **binds today?** | 🔴 measured binding · ✅ armed · 🔴 **never** | has this limit ever changed an outcome |

### §F.1 — the axes, on the rows that have them measured

| row | key | pipeline | twin? | binds today? | evidence |
|---|---|---|---|---|---|
| 1 | `risk.max_daily_trades` = 10 | BOTH via `if/else` | ✅ #8 | 🔴 **5,146 rejections, but ONLY 09–10 Jul**; not once since | (P) `signals.status='REJECTED_DAILY_TRADES'` |
| 2 | `risk.max_open_positions` = 5 | BOTH via `if/else` | ✅ #7 | 🔴 **500 rejections, 09-Jul→07-Aug** | (P) `REJECTED_OPEN_POSITIONS` |
| 3 | `risk.daily_loss_limit_pct` = 0.03 | **BOTH, of TOTAL not of bucket** | ❌ | armed — **zero rejections ever** | (P) no `REJECTED_DAILY_LOSS` status exists in `signals` |
| 4 | `capital.intraday_bucket_pct` = 0.70 | INTRADAY | ✅ `positional_bucket_pct` (§G-2) | ✅ | (S) `fund_manager.resolve_bucket_allocation` |
| 5 | `risk.max_consecutive_losses` = **4** | **BOTH — deliberately shared, reason at the check** | ❌ by decision | 🔴 3 rejections | (S) `risk_engine.py:574-575` · (P) `REJECTED_CONSECUTIVE_LOSSES`=3 |
| 7 / 8 | delivery count caps 3 / 5 | DELIVERY | — (they *are* the twins) | ⚠️ **NO LONGER INERT** — see §H-2 | (S) `system_config.yaml:89`,`:102` |
| 12 | `position_sizing.max_concentration_pct` = 0.10 | **BOTH** | ❌ | 🔴🔴 **the only cap that has ever decided a quantity** — 3,486 rejections | (P) `REJECTED_SIZING_CONCENTRATION` |
| 11 | `position_sizing.risk_per_trade_pct` = 0.01 | BOTH | ✅ §G-4 (`null`) | 🔴 **NEVER** — no `REJECTED_SIZING_RISK` status exists | (P) full status distribution |
| 13 | `position_sizing.max_position_value_pct` = 0.40 | BOTH | ✅ §G-5 (`null`) | 🔴 **NEVER** | (P) full status distribution |
| 16 | `position_sizing.tier_multipliers` (HIGH 1.0 / **MEDIUM** 0.7 / LOW 0.5) | BOTH | ❌ — no `delivery_tier` key exists | 🔴 halves every position it touches | (S) config · review §A.3 |
| 17 | `risk.max_sector_exposure_pct` = 0.40 | BOTH | ❌ | 🔴 **NO** — `sector_cap_mode: observe` (§G-7) | (S) `system_config.yaml` |
| 18/19 | `signal_processor` throttles (4 keys) | BOTH | ❌ | 🔴 **436 rejections; the earliest-firing control on 19 of 21 trading days** | (P) `REJECTED_ENTRY_THROTTLED` |
| 14 | `position_sizing.max_single_order_qty` = 10000 | BOTH | ❌ | 🔴 NO | review §A.3 |
| 15 | `min_qty_threshold` · `min_tick_size` · `lot_skew_rejection_threshold` | BOTH | ❌ | lot floor reached routinely; no `INVALID_SL_DISTANCE` observed | review §A.3 |
| 24 | `smart_tgt.*` (**5** leaf keys) | INTRADAY | ❌ | 🔴 NO — `smart_tgt_state` 0 rows | review §A.3 |
| 25 | `drift_handler.*` (4) | BOTH | ❌ | latent | review §A.4 |
| 26 | `strategy_circuit_breaker.*` (**4**) | BOTH — `cutoff_time: 12:00` is an intraday clock | ❌ | 🔴 4,433 rejections | (P) `REJECTED_STRATEGY_CIRCUIT_BREAKER` |
| 27 | `circuit_breaker.*` (**3**) | BOTH — a 5-min partial-fill timeout on a days-scale hold | ❌ | ✅ | review §A.2 |
| 28 | `order_reconciler` drift tolerances | BOTH — a **leverage** calibration, not a policy | ❌ | 🔴 **firing** | already registered |

> ⭐ **THE ORDERING IS THE FINDING, AND IT SURVIVES THE MERGE.** The two limits that actually bind —
> **concentration (row 12)** and **the tier multiplier (row 16)** — have **no delivery twin**. The two
> that *have* twins (rows 11 and 13) have **never rejected anything**. ⛔ A delivery surface that
> mirrored the existing one would reproduce exactly the wrong two knobs.

---

# ➕ §G — NEW ROWS 31–44: control keys the review names that no row above covered

Each qualifies under the authority's **inclusion rule** (it can change existence · size · execution ·
lifecycle · alerting). Membership was checked against all 30 existing rows by dotted prefix.

| # | config key (dotted) | value @07-Aug | scope | pipeline | twin? | binds today? | why it is a row |
|---|---|---|---|---|---|---|---|
| **31** | `capital.leverage_map` | INTRADAY 5.0 · COVER_ORDER 6.0 · **DELIVERY 1.0** · BRACKET_ORDER 5.0 | per-intent | per-intent | ✅ keyed per intent | ✅ indirectly — the only rung on purchasing power; 24 `REJECTED_SIZING_CAPITAL` | the margin divisor; **already correctly isolated** and the template for the others |
| **32** | `capital.positional_bucket_pct` | 0.30 | global / instant | DELIVERY | ✅ (twin of row 4) | ✅ | row 4 named only the intraday half of a two-sided split |
| **33** | `capital.slm_margin_buffer_pct` | 0.05 | per-reservation | **BOTH** | ❌ | ✅ | a 5 % SL-Market buffer applied to CNC, **which has no SL-Market order** |
| **34** | `position_sizing.delivery_risk_per_trade_pct` | **`null`** (`:190`) | per-trade | DELIVERY | — (is the twin) | 🔴 **doubly inert** — `null`, and its parent has never bound | one of the two twins under decision-ledger row 4 |
| **35** | `position_sizing.delivery_max_position_value_pct` | **`null`** (`:191`) | per-trade | DELIVERY | — (is the twin) | 🔴 **doubly inert** | ditto; and Must-Land-Before constraint 8 attaches here |
| **36** | `risk.one_trade_per_symbol_direction_per_day` | **`true`** (`:224`) | per-symbol+direction / **day** | **BOTH — product-blind** | ❌ | 🔴 **65 rejections, 03-Aug→07-Aug** | ⭐ **the key Ruling 2 governs.** Absent from every prior inventory |
| **37** | `risk.sector_cap_mode` | `'observe'` | global / switch | BOTH | ❌ | 🔴 **NO — it is what makes row 17 inert** | a mode key that silently disarms another row's cap |
| **38** | `trading_hours.eod_entry_cutoff` | `'15:15'` | global / time | BOTH | ❌ | ✅ | ⭐ a **cutoff**, not a window — *"too late to observe before it carries unattended"* |
| **39** | `order_reconciler.check1_mid_fill_defer_sec` | **0.0 = OFF** | per-event | BOTH | ❌ | 🔴 NO | ⛔ paper cannot exercise it ⇒ ON = an unrehearsed path straight into LIVE |
| **40** | `eod_squareoff.*` | **6** leaf keys; `auto_resume_kill_switch: true` | global / EOD | INTRADAY (CNC exempt by EOD6) | ❌ | ✅ — except `auto_resume_kill_switch`, a **dead knob** (never passed to the ctor) | flatten protocol + grace; the dead knob is already registered as IA-P7-04 |
| **41** | `portfolio_allocator.*` | **7** leaf keys; `allocator_mode: 'shadow'` · `max_portfolio_deployment_pct: null` · `long_short_skew_max: null` | batch / instant | BOTH | ❌ | 🔴 **NO — shadow** | ⭐ it contains a **fourth** one-per-symbol rule (§H-4) |
| **42** | `structure_exit.*` | **6** leaf keys; `structure_exit_enabled: false` | per-trade exit | BOTH | ❌ | 🔴 NO — disabled | a whole exit family absent from the inventory |
| **43** | `mis_filter.*` | **3** leaf keys; `shadow: true` | per-symbol | INTRADAY | ❌ | 🔴 NO — shadow | the learned MIS blocklist |
| **44** | `alerts.*` | **31** leaf keys | global | BOTH | ❌ | ✅ | ⭐ Rama's separate-delivery-vocabulary decision lands entirely here |

### §G.1 — the arithmetic, with operands shown

```
  BEFORE   §A 8  +  §B 2  +  §C 7  +  §D 5  +  §E 8                    =  30 rows
  ADDED    §G 31..44 (each checked against all 30 by dotted prefix)     =  14 rows
  AFTER    30 + 14                                                     =  44 rows
```
**Independently re-added by section:** §A 1–8 = 8 · §B 9–10 = 2 · §C 11–17 = 7 · §D 18–22 = 5 ·
§E 23–30 = 8 · §G 31–44 = 14 ⇒ 8+2+7+5+8+14 = **44** ✅ and the highest row number is **44** ✅
(two checks that could have disagreed).

**Leaf-key coverage, MEASURED not counted by hand** — leaf keys in the 21 config families named by
the 44 rows: **169 of 301** (56.1 %). The 132 uncovered decompose as **98** signal-generation keys
(`sr_detector` 34 · `v3_chain` 39 · `regime` 15 · `watchlist` 10 — reproducing the review's "98
excluded" exactly, by an independent count) **+ 34** infra/transport/product-map/top-level scalars.
⇒ 98 + 34 = 132 ✅.

### §G.2 — ⛔ WHAT THIS MERGE DOES **NOT** CLAIM

- ⛔ **It does NOT claim to contain "the 113 keys".** The review's 113 is a **scope count**, never a
  row set: the document nowhere enumerates 113 rows, and its own bucket total is already on record as
  reconciling by accident (`31+34+6+42 = 113`; classified existing **107**; the DELIVERY-ONLY 6 *are
  not keys at all*; ≥4 real keys sat in no bucket; residual 6 = 4 named + **2 UNIDENTIFIED**).
  Manufacturing 113 rows here would have meant re-deriving the key list from YAML — **a new
  measurement wearing a merge's clothes, and a third inventory in all but name.** Recorded, not done.
- ⛔ **The review's §A.7 (Rama's six decided items) contributes ZERO rows** — the review itself records
  that *no key exists* for any of them. Those are the same 6 that made the 113 reconcile by accident;
  carrying them as rows would import that exact error into the authority.
- ⚠️ **The 301-vs-302 corpus ambiguity is NOT resolved.** Today's read reproduces **301 / 42 sections
  by YAML parse** — the same *method* as the review, so it corroborates one arm and settles nothing.
  The 302 came from an indentation walk, which was **not re-run**.

---

# 💥 §H — COLLISIONS FOUND DURING THE MERGE

⛔ **Every collision is listed with BOTH values and BOTH sources. None was silently resolved.**
⭐ Collisions are a finding, not friction.

### §H-1 · VALUE collision — `risk.max_consecutive_losses`
| source | value |
|---|---|
| this file, row 5 (03-Jul) | **5** |
| `audit_05jul2026.md:537` row 7 (05-Jul) | **5** |
| **measured `config/system_config.yaml:226` @07-Aug** | **4** |

Both inventories carry **5**; the live config is **4**. 🔴 **Resolution: the measured value governs.**
Row 5 above now reads 4. ⭐ *This is why the authority header adds VALUE PROVENANCE — a value
carried between documents without re-reading is not evidence.*

### §H-2 · 🔴🔴 SCOPE collision — the delivery caps are labelled INERT and are **NOT**
| source | claim |
|---|---|
| this file, rows 7 & 8 + decision **D6** | *"marked INERT (`force_intraday_only=true`)"* |
| `audit_05jul2026.md:536` row 6 | *"inert while `force_intraday_only: true`"* |
| **measured @07-Aug** | `force_intraday_only: **false**` (`:89`) · `delivery_enabled: **true**` (`:102`) · `trade_type: BOTH` (`:96`) |

⇒ **The premise of both inventories' inertness tag is false at HEAD.** Two real CNC positions are
open right now (`DIFFNKG` since 06-Aug, `MANINFRA` since 07-Aug). ⛔ The tag is not edited away — it
is **superseded here**, because an operator reading "INERT" about a live delivery cap is the exact
failure mode the authority exists to prevent.

### §H-3 · FAMILY-SIZE collisions — **10 of 11 shared families disagree with the measured config**
| family | this file names | the 113-key review says | **measured @07-Aug** |
|---|---|---|---|
| `alerts.*` | *(absent)* | **9** | **31** |
| `entry_gate.*` | 4 | **9** ("all 9") | **17** |
| `smart_tgt.*` | 3 | 5 | **5** |
| `strategy_circuit_breaker.*` | 3 | 4 | **4** |
| `circuit_breaker.*` | 3 | 2 | **3** |
| `clock.*` | 3 | *(excluded as infra)* | **6** |
| `order_reconciler.*` | 3 | 3 | **7** |
| `signal_queue.*` | 2 | *(excluded)* | **4** |
| `webhook.*` | 2 | *(excluded)* | **7** |
| `live_feed.*` | 1 | *(excluded)* | **3** |
| **`drift_handler.*`** | **4** | **4** | **4** ✅ |

⭐⭐ **Exactly ONE family of eleven — `drift_handler.*` — has a count both documents agree on *and*
that matches the config.** ⛔ This is the same defect shape as the 113 accident: **a family counted
as if it were a key.** Hence the authority's ROW-UNIT clause — a family is one row, and the row
states its **measured** leaf count.

### §H-4 · INCLUSION-RULE collision — the two documents disagree on 4 of 30 existing rows
`signal_queue.*` (row 6) · `webhook.*` (row 20) · `clock.*` (row 29) · `live_feed.*` (row 30) are
**rows here** and sit inside the review's **~90-key EXCLUDED infra families**.
🔴 **Resolution: this document's inclusion rule governs — it is the authority.** The four rows STAY.
The review's exclusion is recorded as *the scope of that review*, ⛔ never as a deletion.
⭐ **This is why "both are keyed on the dotted key" was not sufficient:** the join worked perfectly and
the two documents still disagreed about which keys belong at all.

### §H-5 · a fourth `DUPLICATE_SYMBOL`, in a place no inventory mentions
`allocation/portfolio_allocator.py:182-183` returns the label `DUPLICATE_SYMBOL` for a
one-per-symbol rule **within an admission batch** — a fourth product-blind symbol gate, inert only
because `allocator_mode: 'shadow'` (new row 41). It appears in **no** inventory, in neither the
27-row nor the 30-row set. Cross-referenced to Ruling 2; see
`docs/audit/rulings_1_2_verification_07aug2026.md` §H1.

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

**Row count (03-Jul original):** 30 limit-type keys catalogued (8 primary quotas + 2 per-strategy + 20 guards/rate/window/escalation). v1 capacity widget renders rows #1–#8; strategy panel renders #9–#10; the remaining 20 are catalogued for G2b M-groups with their live-counter source pre-identified.

**Row count (07-Aug, after the Ruling-1 merge):** **44** — `30 + 14` (§G.1 shows the operands and the
independent re-add). Rows **31–44** have **no v1 widget rendering**: they were catalogued for the
delivery-isolation decision, not for the capacity widget, and marking them `N` would have implied a
G2b plan that does not exist for them.

⚠️ **The 27-row set at `docs/audit/audit_05jul2026.md:527` is a DIFFERENT PARTITION, not an earlier
draft of this one** — it is keyed on *control* (enforcement point + stage), this one on *config key*.
`27 → 30` is not `+3`, and ⛔ the two counts must never be subtracted from one another.
