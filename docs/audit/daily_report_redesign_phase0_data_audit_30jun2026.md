# Daily Report Redesign — Phase 0: Data-Availability Audit (Field Inventory Matrix)

- **Date (IST):** 2026-06-30 (Tue)
- **Mode:** AUDIT ONLY — no build, no schema change, no report code. This document IS the deliverable.
- **Author:** Claude Code (Opus 4.8), HIGH reasoning.
- **Live DB sampled:** VM `data_store/trading_system.db` (schema v40), session=LIVE/INTRADAY, 173 trades (61 closed, 94 FAILED, 9 CANCELLED, 9 REJECTED).

## Architecture rule applied (Rama, binds the project)
1. The report is AUTOMATED and reads from the **DB ONLY** — never live API, never log scraping, never non-DB raw sources.
2. The DB is populated by the **system's own files** (log/calc/system-driven writes).
3. NEVER MIX (system writes DB) ⊥ (report reads DB). **Any field NOT in the DB = `NEEDS_CAPTURE`** = a SYSTEM file must be changed to WRITE it to the DB first — it is NOT something the report fetches.

Classes: **EXISTS** (a DB `table.column` holds it) · **DERIVABLE** (report computes from existing DB columns) · **NEEDS_CAPTURE** (raw inputs not in DB → a system module must write them).

---

## 0. Headline conclusions (read this first)

- **The redesign is ~75% buildable today.** The two existing generators (`reports/daily_review.py`, `reports/daily_report.py`) already render most sheets from the DB; `daily_report.py` is effectively a 7-sheet proto of the 12-sheet redesign.
- **8 capture gaps** stand between today and the full 12-sheet report. They cluster into **4 capture work-items** (see §5). None block Tier-1.
- **One foundational caveat:** the *existing* `daily_report.py` already **violates the DB-only rule in 6 places** — it reads `system_config.yaml`, `scoring_weights.yaml`, `broker_costs.yaml`, `strategies/*.yaml`, `nse_holidays.yaml`, and `data_store/candles/*.csv` directly. The redesign must decide: snapshot these to DB (pure) or accept deterministic config-file reads (pragmatic). This is the **Config_data** decision.

---

## 1. THE FIELD INVENTORY MATRIX (per sheet)

> Convention: where a whole sheet is one table's columns, the table mapping is stated once and only the *exceptions / interesting fields* are itemised. Every `NEEDS_CAPTURE` field is named explicitly. `table.column` paths are in the v40 main DB unless marked `analytics.`.

### Sheet 1 — Dashboard  (Tier-1)
KPI roll-up. **All EXISTS/DERIVABLE.**

| Field | Class | Source / derivation |
|---|---|---|
| Trading date, Mode, Account | EXISTS | `session.session_date / mode / account_id` |
| Trade_type | EXISTS | `session.trade_type` |
| Signals received / after-dedup / passed-screen / converted | DERIVABLE | count `signals` by status + `screener_results` |
| Trades placed / filled / win / loss / breakeven | DERIVABLE | `trades` (status, net_pnl sign) |
| Gross P&L / Net P&L / Total costs | EXISTS/DERIVABLE | `trades.gross_pnl / net_pnl / charges` (+ 6 `cost_*` components) |
| Win rate, Avg R:R, Max drawdown, Best/Worst trade | DERIVABLE | `trades` |
| Opening / Closing capital (SYSTEM) | DERIVABLE | `fm_ledger` (balance_before/after, pnl_delta) — **NOT `capital_snapshot` (empty, see §App)** |
| Closing capital (BROKER), Broker net gain | **NEEDS_CAPTURE** | broker P&L not persisted — see Gap #3 |
| System uptime, Kill-switch events, ERROR/CRITICAL counts | DERIVABLE | `system_events` (STARTUP/SHUTDOWN/KILL_*) ; counts of ERROR/CRITICAL = **partial** (only what reaches `system_events`; generic errors LOGS-only — Gap #6) |
| Excluded symbols | **NEEDS_CAPTURE** (or config read) | `system_config.yaml excluded_symbols` — not in DB (Gap #7) |

### Sheet 2 — Config_data  (Tier-1*, needs a capture decision)
The resolved configuration the system ran with today.

| Field | Class | Source / gap |
|---|---|---|
| Config **hash** | EXISTS | `session.last_config_hash` ; `system_events` `CONFIG_DIFF` (details JSON) |
| Config **values** (trading_hours, capital, risk, position_sizing, leverage_map, scoring_weights, per-strategy params, excluded_symbols, broker_costs) | **NEEDS_CAPTURE** | All live in `config/*.yaml` + `config/strategies/*.yaml`. **No DB snapshot table exists; no config-snapshot DAO in `state_store.py`.** The current `daily_report.py` reads these YAMLs directly (rule violation). |

→ **DECISION (Rama):** (A) add a startup `config_snapshot` DB table that records the resolved SystemConfig (DB-pure), or (B) treat deterministic config-file reads as an allowed exception (the files ARE "the system's own files", not live API/logs). Recommended: **(A)** a small one-row-per-day snapshot for purity + historical accuracy ("what config was live on date X").

### Sheet 3 — Signals  (Tier-1)
**All EXISTS, heavily populated** (`signals`, `screener_results` 64,740 rows, `webhook_audit` 46,392 rows).

| Field group | Class | Source |
|---|---|---|
| signal id/symbol/scanner/strategy/trigger_price/timestamps/status/rejection_reason | EXISTS | `signals.*` |
| Algo score / eligible (min) score / score breakdown | EXISTS | `screener_results.score / eligible_score / step_results(JSON)` |
| Funnel counts (received→queued→passed→rejected→dup→placed→filled) | DERIVABLE | `signals` status + `screener_results` |
| Per-scanner received/accepted/rejected, dedup | EXISTS | `webhook_audit.*` |
| Signal→trade linkage | EXISTS | `signals.trade_id` / `trades.signal_id` |

### Sheet 4 — Orders (9 groups)  (Tier-1, one capture gap)
Per the existing `2_Orders` sheet (43 cols, group headers IDENTITY/TIME/SYSTEM/FILLED/DEVIATION/REVENUE/NET/META). **Almost all EXISTS.**

| Field group | Class | Source |
|---|---|---|
| Identity (date, strategy, direction, symbol, scores) | EXISTS | `trades` + `signals` + `screener_results` |
| Time (placed/fill/exit, time-in-trade) | EXISTS | `orders.placed_at/filled_at` + `trades.entry_time/exit_time`; latency `trades.signal_to_order_ms/order_to_fill_ms/total_latency_ms` |
| System qty/entry/SL/TGT/R:R | EXISTS | `trades.qty_planned/entry_target_price/sl_initial/tgt_initial` + `tgt_risk_reward_applied` |
| Actually filled (qty, entry, SL fill) | EXISTS | `orders.qty_filled/avg_fill_price` (per leg) + `trades.entry_actual_price` |
| Entry deviation / slippage / fill R:R | EXISTS | `order_execution_log` / `trade_slippage_log` (see Sheet 5) |
| Exit reason, SL trail count | EXISTS | `trades.exit_reason / sl_trail_count` |
| Revenue/costs (gross, brokerage, STT, exch, stamp, GST, total) | EXISTS | `trades.gross_pnl + cost_brokerage/cost_stt/cost_exchange_txn/cost_stamp_duty/cost_sebi/cost_gst` |
| Net P&L, ROI% | EXISTS/DERIVABLE | `trades.net_pnl`; ROI derived |
| Broker order id, trade id | EXISTS | `orders.order_id`, `trades.trade_id` |
| Per-order **broker margin blocked** | **NEEDS_CAPTURE** | only system `trades.margin_reserved` (flat 5x) stored — Gap #2 |

### Sheet 5 — Slippage  (Tier-1 — data says it's fully present, RAISED from Tier-2)
**EXISTS + live-populated** (`order_execution_log` 72, `trade_slippage_log` 36, `market_execution_context` 72). Writer = `orders/slippage_recorder.py` (async subscribers, active).

| Field | Class | Source |
|---|---|---|
| System-fixed / planned price | EXISTS | `order_execution_log.intended_price`; per-trade `trade_slippage_log.entry_signal_price` |
| Actual fill | EXISTS | `order_execution_log.actual_price`; `trade_slippage_log.entry_fill_price/sl_fill_price/tgt_fill_price` |
| Delta (slippage Rs/pct, per leg) | EXISTS | `order_execution_log.slippage_rs/slippage_pct`; `trade_slippage_log.{entry,sl,tgt}_slippage_rs/pct` |
| Impact-on-result | EXISTS | `trade_slippage_log.rr_damage_pct` (+ `planned_rr/actual_rr/planned_sl_distance/trade_result`) |
| Price band, tolerance rule applied | EXISTS | `trade_slippage_log.price_band`; `trades.tolerance_fraction_used/tolerance_source` (mirrored to `order_execution_log`) |
| Bid/ask/spread at fill | EXISTS (best-effort nullable) | `market_execution_context.bid_price/ask_price/spread_rs/spread_pct/...` — present but may be NULL |

### Sheet 6 — Candles / S&R  (Tier-3 sub-project)
| Field | Class | Source / gap |
|---|---|---|
| OHLCV candles | EXISTS | `analytics.candles` (fetched 15:40 `fetch_daily_candles`); CSV fallback `data_store/candles/*.csv` is a **non-DB read** in current report (Gap #7-adjacent) |
| Entry-candle OHLC per trade | EXISTS | `trade_excursions.entry_candle_open/high/low/close` |
| **MFE / MAE / missed-profit** | EXISTS but **SPARSE** | `trade_excursions.mfe_price/mfe_pct/mae_price/mae_pct` — **only 15 of 61 closed trades (~25%)**; post-EOD reconstruction, sub-minute + historical-backfill gaps (Gap #8) |
| S&R zones (nearest support/resistance, distance, confidence, confluence, flags) | EXISTS (per placed signal only) | `sr_detector_results.nearest_resistance_zone/nearest_support_zone/dist_*/.*_confidence/confluence_evidence/flags` — 29 rows, all outcome-backfilled. SHADOW, gated on `sr_detector.enabled=true` (now ON). **Not a reusable arbitrary-symbol S&R store** — one row per placed candidate only. |

### Sheet 7 — Capital  (Tier-2 split: system EXISTS, broker NEEDS_CAPTURE)
| Field | Class | Source / gap |
|---|---|---|
| Opening/closing capital, per-trade reserve/release, P&L deltas, costs | DERIVABLE | `fm_ledger` (853 rows; RESERVE/RELEASE/COMMIT/RELEASE_USED with balance_before/after, margin_delta, pnl_delta, costs) — **authoritative source** |
| Position value, SL-risk, target-profit per trade | EXISTS/DERIVABLE | `trades.actual_position_value_rs/risk_amount`; TGT derived |
| Margin blocked (SYSTEM estimate) | EXISTS | `trades.margin_reserved` (= qty·entry/leverage_map[intent]; INTRADAY 5x = ·0.20) |
| Margin blocked (BROKER actual) | **NEEDS_CAPTURE** | fetched live (`kite.order_margins().total`, `kite.margins().used`) but only in-memory cached + logged — Gap #2 |
| Additional funds added / funds-after-adjustment | EXISTS | `fm_ledger` `TOP_UP` rows (6) |
| `capital_snapshot` fast-cache | **DEAD** | 0 rows on live DB — **do not use**; derive from `fm_ledger` |

### Sheet 8 — Strategies  (Tier-1)
| Field | Class | Source |
|---|---|---|
| Per-strategy Sharpe / win-rate / avg-pnl / total-trades | EXISTS | `strategy_metrics` (53 rows; 9 today) — written by `compute_strategy_metrics` 16:15 cron |
| Per-strategy signals/processed/rejected/traded/wins/losses/P&L/drawdown | DERIVABLE | `trades` + `signals` grouped by strategy |
| Time-of-day buckets, score-breakdown-by-strategy | DERIVABLE | `trades` (time) + `screener_results.step_results(JSON)` |

### Sheet 9 — Telegram  (Tier-2 — whole sheet is NEEDS_CAPTURE)
| Field | Class | Source / gap |
|---|---|---|
| Every sent message (sent_at, module, alert type, symbol, qty, prices, full body, result) | **NEEDS_CAPTURE** | `telegram_alerts` table is **VESTIGIAL — never written.** `TelegramNotifier.send()` does HTTP POST only (+ `.flag` sentinels for CRITICAL, `failed_alerts.log` on failure). All 3 operational alerts (signal `_emit_signal_alert`, order `order_placer:1588`, EOD `eod_squareoff:635/644`) bypass the DB. Current report SYNTHESIZES this sheet from `trades`+`signals`. — Gap #1 |

### Sheet 10 — Exceptions  (Tier-2 split)
| Failure class | Class | Source / gap |
|---|---|---|
| Broker order REJECTIONS | EXISTS | `orders.status` (FAILED/REJECTED/CANCELLED) + `orders.rejection_reason` + `trades.status` |
| SL-placement failure / NAKED position (reconciler-detected) | EXISTS | `orders.reconciliation_status='SL_MISSING'` + `reconciliation_log` (`check_name='MISSING_EXITS'`, tier UNRECOVERABLE) |
| Reconciler drift actions (all) | EXISTS | `reconciliation_log` (7,758 rows) |
| Broker API TIMEOUTS / quote / historical failures | **NEEDS_CAPTURE** | per-event LOGS-only; only an in-memory counter (`kill_switch.record_api_failure`); only a *tripped breaker* leaves `kill_switch_state` — Gap #5 |
| Placement-time SL/TGT after-check mismatch | **NEEDS_CAPTURE** | `EXITS_AFTERCHECK_SL_MISMATCH` = CRITICAL-log + Telegram only, no DB row — Gap #6 |
| Generic uncaught exceptions / error events | **NEEDS_CAPTURE** | no central errors table; `system_events` is a fixed lifecycle set (STARTUP/SHUTDOWN/CRASH_DETECTED/CONFIG_DIFF/KILL_AUTO_CLEARED/EOD_SKIPPED_LATE), NOT an error sink — Gap #6 |
| CRITICAL sentinels | (filesystem) | `data_store/critical_alert_*.flag` — never a DB row |

### Sheet 11 — Reconciliation  (Tier-1 system side; Tier-2 broker side)
| Reconciliation | Class | Source / gap |
|---|---|---|
| Signals (received/qualified/rejected/dup) | EXISTS | `signals` + `screener_results` + `webhook_audit` |
| Orders (placed/failed) | EXISTS | `orders.status` |
| Trades (filled/cancelled/rejected) | EXISTS | `trades.status` |
| Capital — SYSTEM closing | DERIVABLE | `fm_ledger` |
| Capital — BROKER closing P&L | **NEEDS_CAPTURE** | `pnl_reconciliation` = **0 rows**; no cron writer (`reconcile_pnl` is in-process, doesn't persist) — Gap #3 |
| Positions — BROKER vs system | **NEEDS_CAPTURE (effectively)** | `position_reconciliation` = **0 rows**; `reconcile_positions` runs daily (15:45) but persists **only on mismatch** → no reliable daily OK record — Gap #4 |
| Margin — BROKER blocked vs system 5x | **NEEDS_CAPTURE** | Gap #2 |
| EOD verification (open trades, pending orders, pnl variance) | EXISTS | `eod_verification` (8 rows) + `eod_squareoff_log` (12 rows) |

### Sheet 12 — EOD_Action_Items  (Tier-3 sub-project)
| Field | Class | Source |
|---|---|---|
| Open/unflat trades, pending orders, pnl variance | DERIVABLE | `eod_verification`, `trades.status`, `orders.status` |
| Naked positions / orphan orders / SL-missing | DERIVABLE | `orders.reconciliation_status`, `reconciliation_log` |
| Outstanding ops findings | EXISTS | `control_tower_findings` (3 open) |
| Failed crons | DERIVABLE | `cron_heartbeat` (presence/absence) |
| Synthesised "do tomorrow" list | DERIVABLE | compose from the above (this is the sub-project's logic) |

---

## 2. Resolved high-uncertainty items (definitive)

1. **Telegram sent messages → NOT in DB.** `telegram_alerts` is vestigial; the notifier never writes it (HTTP + sentinels + failed-log only). SYSTEM_MAP already states this. → **NEEDS_CAPTURE** (Gap #1).
2. **MFE/MAE coverage → SPARSE.** `trade_excursions` has 15 of 61 closed trades (~25%). Post-EOD reconstruction (`reconstruct_excursions` 15:50) writes new ones; historical backfill is DEFERRED; sub-minute + NULL-exit trades get no row. Table EXISTS, coverage incomplete (Gap #8).
3. **Broker blocked margin (5x recon) → NOT stored.** Broker-actual is fetched live (`kite.order_margins().total` per-symbol; `kite.margins().used` aggregate) but only in-memory cached + logged. Only the SYSTEM estimate `trades.margin_reserved` (flat per-intent 5x) persists. `leverage_map` is per-intent, not per-symbol → broker-actual not derivable. → **NEEDS_CAPTURE** (Gap #2).
4. **Exceptions → partially queryable.** Order rejections + reconciler-detected naked positions EXIST in DB; broker API timeouts/quote/historical failures and generic exceptions are LOGS-ONLY (Gaps #5/#6).
5. **Per-trade slippage decomposition → fully EXISTS + live.** `order_execution_log` (per leg) + `trade_slippage_log` (per trade), with the system/actual/delta/impact 4-tuple complete (`rr_damage_pct` = impact). Writer wired and active.
6. **S&R levels → COMPUTED + STORED, but per-placed-signal only.** `sr_detector_results` persists nearest support/resistance zones + confidence + confluence + flags per placed candidate (JSON), outcome-backfilled at EOD. It is SHADOW (gated on `sr_detector.enabled`, now ON) and is NOT a reusable arbitrary-symbol level store. Candles themselves are persisted in `analytics.candles`.

---

## 3. Existing generators + cron (reuse)

| | `reports/daily_review.py` | `reports/daily_report.py` |
|---|---|---|
| Output | `reports/daily/<date>/daily_review.{xlsx,md}` (folder-dated) | `reports/output/daily_report_<date>.xlsx` (filename-dated) |
| Cron | ~16:00 (heartbeat `daily_review`, 23 runs) | **16:05 Mon-Fri** (`5 16 * * 1-5`), heartbeat `daily_report` |
| Sheets | 9: SUMMARY, SIGNAL_FUNNEL, SCREENER_ANALYTICS, TRADES, ORDERS, CAPITAL_LEDGER, SYSTEM_EVENTS, ALERTS_SENT(placeholder), MULTI_INNING_TRACKING | 7: 0_EOD_Dashboard(A-E), 1_Signals, 2_Orders, 3_Capital, 4_Candles, 5_Telegram, 6_Strategy_Analysis |
| Styling | minimal (own `_xlsx_sheet`) | **rich** — `reports/style_constants.py` (fonts/fills/borders/number-formats/EXIT_REASON_FILLS/ALERT_TYPE_FILLS), merged group headers, separator cols, conditional fills, `=SUM()` formulas |
| DB-only? | YES (100% DB via `state_store`) | **NO** — reads `system_config.yaml`, `scoring_weights.yaml`, `broker_costs.yaml`, `strategies/*.yaml`, `nse_holidays.yaml`, `data_store/candles/*.csv` |

**Reuse for the redesign:** `style_constants.py` toolkit; `daily_report.py` helpers `_disable_gridlines/_set_column_widths/_add_separator_column/_apply_cell_style/_get_order_for_trade_leg`; shared `state_store` date helpers `get_{signals,trades,orders,fm_ledger,screener_results,system_events,reconciliation_log}_for_date`; heartbeat `record_heartbeat(job_name)`. The redesign REPLACES `daily_review.py`'s slot with a new ISO name (`daily_trade_review_report_<date>.xlsx`). Note the two generators use different output roots/date conventions — unify.

**Populated tables NOT yet surfaced by either generator** (free for the redesign): `trade_journal` (26), `strategy_metrics` (53), `eod_squareoff_log` (12), `eod_verification` (8), `order_execution_log`/`trade_slippage_log`/`market_execution_context` (slippage), `sr_detector_results` (29), `control_tower_findings` (3).

---

## 4. Reconciliation feasibility

| Need | Computable today? |
|---|---|
| Signals received/qualified/rejected/dup | ✅ `signals`+`screener_results`+`webhook_audit` |
| Orders placed/failed | ✅ `orders` |
| Trades filled/cancelled/rejected | ✅ `trades` |
| Capital SYSTEM closing | ✅ `fm_ledger` (NOT `capital_snapshot`) |
| Capital BROKER closing P&L | ❌ Gap #3 (`pnl_reconciliation` empty) |
| Positions BROKER vs system | ❌ Gap #4 (`position_reconciliation` mismatch-only) |
| Margin BROKER blocked vs 5x | ❌ Gap #2 |

The **internal** reconciliation (signal→order→trade→system-capital) is fully computable now. Every gap is on the **broker comparison** side.

---

## 5. Confirmed tiering + the capture gaps

### Tier-1 — build now (all EXISTS/DERIVABLE)
**Dashboard, Signals, Orders-core, Strategies, Slippage (raised in), Reconciliation-SYSTEM-side.**
- *Config_data* is Tier-1 **pending the one capture decision** (§Sheet 2): snapshot config to DB vs accept config-file reads.

### Tier-2 — partial; each names its capture gap
| Sheet | Gap(s) | Capture work-item |
|---|---|---|
| **Telegram** | #1 | **W1:** make `TelegramNotifier.send()` INSERT a `telegram_alerts` row on every send (table already exists; no schema change). |
| **Capital** (broker margin) + **Reconciliation** (broker margin) | #2 | **W2:** persist broker-actual margin — add `trades.broker_margin_blocked` (schema migration) written at `order_placer.py:~853` from `position_sizer` margin_pct; and/or capture `kite.margins().used` into a `fm_ledger` SYNC / `capital_snapshot` row. |
| **Reconciliation** (broker P&L + positions) | #3, #4 | **W3:** wire a persisting EOD broker reconciliation — populate `pnl_reconciliation` (broker_pnl vs system_pnl) and write a daily `position_reconciliation` OK row (not mismatch-only). Tables already exist; needs a writer wired into the 15:45/EOD path. |
| **Exceptions** (broker API + generic) | #5, #6 | **W4:** persist broker API failures + generic error events — either a new `error_events`/`exception_log` table (schema) or extend `system_events` with an `API_FAILURE`/`ERROR` event_type written at the broker try/except sites + the placement after-check. |

### Tier-3 — sub-projects
- **Candles / S&R** — candles EXISTS; S&R per-signal EXISTS (shadow); **MFE/MAE sparse (Gap #8)** — improve coverage via the existing reconstruction + historical backfill before relying on it.
- **EOD_Action_Items** — DERIVABLE from `eod_verification`/`reconciliation_log`/`control_tower_findings`/`orders.reconciliation_status`; the value is the synthesis logic.

### Capture-gap register (for scoping as SEPARATE system-side tasks)
| # | Gap | Where to write | Schema change? |
|---|---|---|---|
| 1 | Telegram sends not logged | `alerts/telegram_notifier.py send()` → INSERT `telegram_alerts` | No (table exists) |
| 2 | Broker actual margin not stored | `orders/order_placer.py` / `capital/position_sizer.py` → `trades.broker_margin_blocked` | **Yes** (+1 col) |
| 3 | Broker P&L recon not persisted | wire `reconcile_pnl` to write `pnl_reconciliation` | No (table exists) |
| 4 | Broker position recon mismatch-only | `reconcile_positions` → write daily OK rows | No (table exists) |
| 5 | Broker API failures not persisted | broker try/except sites → DB | **Yes** (new table or system_events ext) |
| 6 | Generic exceptions / after-check not persisted | error sites → DB | **Yes** (new table or system_events ext) |
| 7 | Config values not in DB | startup → `config_snapshot` table | **Yes** (if option A) |
| 8 | MFE/MAE sparse (~25%) | improve `reconstruct_excursions` coverage + historical backfill | No (table exists) |

---

## Appendix — Live DB population snapshot (VM, 2026-06-30)

POPULATED: `trades` 173 (closed 61), `signals`/`screener_results` 64,740, `webhook_audit` 46,392, `fm_ledger` 853, `reconciliation_log` 7,758, `system_events` 93, `innings` 102, `trade_journal` 26, `strategy_metrics` 53, `order_execution_log` 72, `market_execution_context` 72, `trade_slippage_log` 36, `sr_detector_results` 29 (all backfilled), `eod_squareoff_log` 12, `eod_verification` 8, `control_tower_findings` 3, `trade_excursions` 15.

EMPTY / VESTIGIAL: `telegram_alerts` 0 (vestigial), `capital_snapshot` 0 (dead cache — use `fm_ledger`), `pnl_reconciliation` 0, `position_reconciliation` 0 (mismatch-only), `shadow_trades` 0. Runtime-state (expected 0 post-EOD): `smart_tgt_state`, `gate_state`, `gtt_state`.

— END Phase 0 audit —
