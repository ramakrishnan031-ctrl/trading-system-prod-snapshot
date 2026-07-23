# Report Data Contract — Trading System v2

**Purpose.** This is the **permanent contract between the SYSTEM (writers) and the
REPORT (readers)** for the redesigned daily report. The report reads **only the DB**
(never YAML / logs / live API). Every field the report renders must trace to a DB
source owned by a system writer.

**The rule: the contract changes FIRST.** When a report field is added or its meaning
changes, update this table (and the owning writer) **before** touching report
rendering. A field with no DB source is `NEEDS_CAPTURE` — build the capture (a writer)
first; do not let the report reach back into YAML/logs to "fill the gap".

**Classification**
- `EXISTS` — already in the DB today; the report can read it now.
- `DERIVABLE` — computable from existing DB rows (no new capture needed).
- `NEEDS_CAPTURE` — not in the DB; a system writer must be built to record it.

**Columns:** `Sheet | Field | DB Source (table.column [/ JSON path]) | Owner Module | Classification | Comments`

> Source of truth for the field inventory: the Phase-0 audit matrix
> `docs/audit/daily_report_redesign_phase0_data_audit_30jun2026.md`. Rows for
> **Orders / Signals / Reconciliation / Strategies / Slippage / Dashboard** are
> appended at **each sheet's build** from that matrix — this file starts with the
> **Config** rows (built by W0).

---

## Sheet: Config_data  (built by **W0**, 01-Jul-2026)

The resolved configuration the system ran with on a given date. **W0** adds the
`config_snapshots` table (schema **v41**, pure addition) and a startup writer
(`core/config_snapshotter.py::snapshot_config`) that persists the FULL resolved
`AppConfig` (`core/config_loader.load_all` → `model_dump(mode="json")`) once per
startup, in **both paper and live** (mode is a column). The report reads
`config_snapshots.config_json` (a JSON blob) — never the YAML.

| Sheet | Field | DB Source (table.column / JSON path) | Owner Module | Classification | Comments |
|---|---|---|---|---|---|
| Config | Snapshot date | `config_snapshots.snapshot_date` | Config Snapshotter | **EXISTS** (W0) | YYYY-MM-DD IST; the report keys the day's config off this. Index `idx_config_snapshots_date`. |
| Config | Snapshot timestamp | `config_snapshots.snapshot_ts` | Config Snapshotter | **EXISTS** (W0) | ISO IST when written; distinguishes multiple same-day rows on a config change. |
| Config | Mode | `config_snapshots.mode` | Config Snapshotter | **EXISTS** (W0) | PAPER \| LIVE. |
| Config | Account | `config_snapshots.account_id` | Config Snapshotter | **EXISTS** (W0) | Primary/selected account (e.g. LFL836). |
| Config | Trade type | `config_snapshots.trade_type` (= `config_json $.system.trade_type`) | Config Snapshotter | **EXISTS** (W0) | INTRADAY \| DELIVERY \| BOTH. |
| Config | Config **hash** | `config_snapshots.config_hash` | Config Snapshotter | **EXISTS** (W0) | sha256 of the stable/sorted `config_json`. (Also: `session.last_config_hash` = per-FILE hashes; `system_events` `CONFIG_DIFF`. Distinct from W0's full-config hash.) |
| Config | trading_hours | `config_snapshots.config_json $.system.trading_hours` | Config Snapshotter | **EXISTS** (W0) | entry_start/entry_end/eod_entry_cutoff/eod_squareoff_time/market_open/market_close. |
| Config | capital (+ leverage_map) | `config_json $.system.capital` (`.leverage_map`) | Config Snapshotter | **EXISTS** (W0) | buckets, sl/gtt offsets, emergency buffer, per-intent leverage. |
| Config | risk | `config_json $.system.risk` | Config Snapshotter | **EXISTS** (W0) | max_open_positions, max_daily_trades, daily_loss_limit_pct, sector, consecutive, delivery caps. |
| Config | position_sizing | `config_json $.system.position_sizing` | Config Snapshotter | **EXISTS** (W0) | risk_per_trade_pct, concentration, tier multipliers, perf-weighting knobs. |
| Config | scoring_weights | `config_json $.scoring` | Config Snapshotter | **EXISTS** (W0) | quality-scorer weights + min_pass_score. |
| Config | broker_costs | `config_json $.broker_costs` | Config Snapshotter | **EXISTS** (W0) | brokerage/STT/charges model. |
| Config | slippage model / control | `config_json $.slippage` + `$.system.entry_gate.slippage_control` | Config Snapshotter | **EXISTS** (W0) | assumptions + entry-slippage abort tolerance. |
| Config | excluded_symbols | `config_json $.system.excluded_symbols` | Config Snapshotter | **EXISTS** (W0) | closes audit **Gap #7** (was NEEDS_CAPTURE / config-read). |
| Config | delivery_enabled / force_intraday_only | `config_json $.system.delivery_enabled` / `$.system.force_intraday_only` | Config Snapshotter | **EXISTS** (W0) | delivery-lock + intraday breaker state. |
| Config | file hashes (per file) | `config_json $.file_hashes` | Config Snapshotter | **EXISTS** (W0) | filename → sha256; also mirrored in `session.last_config_hash`. |
| Config | **per-strategy params** | `config/strategies/*.yaml` (StrategyLoader) — **NOT in config_snapshots** | *(follow-up: W-strategies)* | **NEEDS_CAPTURE** | Strategy configs load via `strategies/loader.StrategyLoader` (late, main.py ~2292) — a SEPARATE config domain, not part of the `load_all()` AppConfig W0 snapshots. Capture as a follow-up (own table or a `_strategies` key). Report must NOT read the YAML. |

### Notes / guardrails (Config)
- **DB-only for the report.** `config_snapshotter` is a *system* task and may read the
  resolved AppConfig (it never reads YAML files directly — it serializes the already-
  loaded config object). The report reads only `config_snapshots`.
- **No secrets.** By CL5 the resolved config expands **no** env vars, so `config_json`
  holds only env-var **names** (e.g. `TELEGRAM_BOT_TOKEN`, `ALERT_SMTP_PASSWORD`), never
  values; the one plaintext-fallback field `alerts.smtp.password` is empty in production.
  W0 stores the config **unfiltered** and logs only the row id + hash prefix.
- **Idempotency.** One row per distinct `(snapshot_date, config_hash)`: a same-config
  restart is a no-op; a same-day config change writes a second row (timestamps order them).
- **`capital_snapshot` is DEAD** — do not use it for the report; capital figures come from
  `fm_ledger` (see the Dashboard sheet rows, appended at that sheet's build).

---

## Sheet: Orders  (built by the **forensic master**, `reports/daily_trade_review.py`, 01-Jul-2026)

One row per **trade placed** — "what happened to this trade?" from the DB alone. Owner
module for the whole sheet: **DailyTradeReview** (`reports/daily_trade_review.py`), which
reads ONLY the DB (StateStore date-scoped getters + direct `fetch_all`). 69 columns in 9
colour-banded groups. `trades` is the spine (`get_trades_for_date`, key `created_at`); joins:
`orders.trade_id`, `signals.signal_id`=`trades.signal_id`, `screener_results.signal_id`,
`trade_slippage_log.trade_id`, `trade_excursions.trade_id`, `sr_detector_results.signal_id`,
`reconciliation_log.trade_id`, `config_snapshots.snapshot_date`.

| Group | Field | DB Source (table.column) | Classification | Comments |
|---|---|---|---|---|
| A Signal | Trade Seq # | derived (ordinal by `trades.created_at`) | **DERIVABLE** | per-day 1..N |
| A Signal | Date | `trades.created_at[:10]` | **EXISTS** | |
| A Signal | Signal Time | `signals.triggered_at` | **EXISTS** | Chartink trigger (HH:MM:SS) |
| A Signal | VM Receipt Time | `signals.received_at` | **EXISTS** | |
| A Signal | Symbol / Strategy / Direction | `trades.symbol` / `.strategy` / `.direction` | **EXISTS** | |
| A Signal | Trade Type | derived from `orders.product` (ENTRY): MIS/CO→INTRADAY, CNC→DELIVERY | **DERIVABLE** | |
| A Signal | Signal Score | `screener_results.score` (max, by signal_id) | **EXISTS** | |
| A Signal | Min Score | `config_snapshots.config_json $.scoring.min_pass_score` | **EXISTS** (W0) | `— pending W0` for pre-W0 dates (no snapshot) |
| B Planned | Qty / Sys Entry / Sys SL / Sys TGT / Risk Amt | `trades.qty_planned` / `.entry_target_price` / `.sl_initial` / `.tgt_initial` / `.risk_amount` | **EXISTS** | |
| B Planned | Reward Amt / Planned RR / SL% / TGT% | derived from entry/sl/tgt (RR prefers `trades.tgt_risk_reward_applied`) | **DERIVABLE** | |
| B Planned | Capital Consumed | `trades.margin_reserved` (system-side) | **EXISTS** | fm_ledger RESERVE is the ledger view |
| B Planned | Broker Margin Blocked | — | **NEEDS_CAPTURE** | `— pending W2` (trades.broker_margin_blocked) |
| C Lifecycle | Entry Placed / SL Created / TGT Created | `orders.placed_at` WHERE leg=ENTRY/SL/TGT | **EXISTS** | |
| C Lifecycle | Entry Filled / Exit Time | `trades.entry_time` / `.exit_time` | **EXISTS** | |
| C Lifecycle | Duration min | derived (`exit_time − entry_time`) | **DERIVABLE** | |
| C Lifecycle | Entry Delay s | `trades.order_to_fill_ms`/1000 (ENTRY placed→filled) | **EXISTS** | FLAG 1: confirmable |
| C Lifecycle | **Exit Trigger** ⚑1 | — | **NEEDS_CAPTURE** | `— pending W5`. FLAG 1: no exit-trigger timestamp exists distinct from fill (`orders.filled_at`≈`trades.exit_time`; resting SL/TGT placed_at is at entry) |
| C Lifecycle | **Exit Delay s** ⚑1 | — (depends on Exit Trigger) | **NEEDS_CAPTURE** | `— pending W5` |
| D Execution | Filled Qty / Filled Entry / Filled Exit | `trades.qty_filled` / `.entry_actual_price` / `.exit_price` | **EXISTS** | |
| D Execution | Filled SL / Filled TGT | `orders.avg_fill_price` (COMPLETE SL/TGT leg) ∥ `trade_slippage_log.sl_fill_price`/`.tgt_fill_price` | **EXISTS** | sparse (needs a filled exit leg) |
| D Execution | Exit Reason | `trades.exit_reason` | **EXISTS** | raw stored reason |
| D Execution | **Trade Closure Type** ⚑2 | `classify_closure()` over `trades.exit_reason`+`.status`+`reconciliation_log.check_name` | **DERIVABLE (honest)** | FLAG 2 — see the **Closure Type source note** below. Values: SL / TGT / EOD_SQUAREOFF / **SYSTEM_CLOSE** / RECON_CLOSE / RECON_EOD_CLOSE / ORPHAN_RECOVERY / UNKNOWN / — (open). Precedence order preserved; RECON_CLOSE narrowed to reconciler-INITIATED only; evidence → Remarks |
| E P&L | Gross / Net | `trades.gross_pnl` / `.net_pnl` | **EXISTS** | |
| E P&L | Brokerage / Exchange / STT / GST / SEBI / Stamp | `trades.cost_brokerage` / `.cost_exchange_txn` / `.cost_stt` / `.cost_gst` / `.cost_sebi` / `.cost_stamp_duty` | **EXISTS** | Σcomponents == `trades.charges` (validated) |
| E P&L | Total Charges | `trades.charges` | **EXISTS** | |
| E P&L | ROI% | derived (`net_pnl` / `margin_reserved` × 100) | **DERIVABLE** | |
| F Slippage | Price Slab | `trade_slippage_log.price_band` | **EXISTS** | sparse (one row per completed trade) |
| F Slippage | Allowed Slip | derived (`trades.tolerance_fraction_used` × `trade_slippage_log.planned_sl_distance`) | **DERIVABLE** | NULL for non-sl_fraction modes |
| F Slippage | Actual Slip / Slip% | `trade_slippage_log.entry_slippage_rs` / `.entry_slippage_pct` | **EXISTS** | |
| F Slippage | Slip Δ / Slip Cost | derived (actual−allowed) / (actual×qty_filled) | **DERIVABLE** | |
| F Slippage | Profit Impact | `trade_slippage_log.rr_damage_pct` | **EXISTS** | the key slippage metric |
| G Quality | MAE / MFE | `trade_excursions.mae_pct` / `.mfe_pct` | **EXISTS** | **SPARSE** — `N/A — no data` when no excursion row (only reconstructed post-EOD) |
| G Quality | MFE Cap / Efficiency% / Grade | derived from `trade_excursions.mfe_price`+`trades` fills | **DERIVABLE** | grade A–F from efficiency |
| G Quality | RR Achieved | `trade_slippage_log.actual_rr` | **EXISTS** | |
| H S&R | Nearest Support / Resistance (+tf, dist%) | `sr_detector_results.nearest_support_zone`/`.nearest_resistance_zone` (JSON: band, timeframes) + `dist_to_*_pct` + `*_confidence` | **EXISTS** | **PARTIAL** — only when the detector logged the signal (shadow); `N/A — no data` otherwise |
| I Meta | Trade Status | `trades.status` | **EXISTS** | |
| I Meta | Trade Result | derived (`net_pnl` sign) | **DERIVABLE** | WIN/LOSS/BREAKEVEN |
| I Meta | **Data Freshness** ⚑3 | composite: `trade_excursions` existence (MFE:RECON) + `sr_detector_results.actual_result` NULL/NOT (S&R:DETECT/BACKFILL) | **DERIVABLE (partial)** | FLAG 3: S&R 2-state derivable; MFE/MAE RECONSTRUCTED-vs-BACKFILLED split **NEEDS_CAPTURE** — `— pending W6` (no per-trade provenance on `trade_excursions`; `excursion_reconstruction_runs` holds only aggregate counts) |
| I Meta | Remarks | derived (closure evidence + recovered/exits_mismatch/tgt_retry flags) | **DERIVABLE** | |
| I Meta | System Trade ID | `trades.trade_id` | **EXISTS** | |
| I Meta | Broker Order ID | `orders.order_id` (ENTRY leg) | **EXISTS** | order_id IS the broker id |
| I Meta | Exchange Order ID | — | **NEEDS_CAPTURE** | `— pending W7` (no `exchange_order_id` column on `orders`; only `order_execution_log.exchange_timestamp` exists) |

### Notes / guardrails (Orders)
- **DB-ONLY** — no yaml/csv/api reads. Reuses `reports/style_constants.py` (palette) only;
  the 4 tiny cell helpers are mirrored locally to stay decoupled from `daily_report.py`.
- **PARALLEL run** — a NEW generator (`daily_trade_review_report_<date>.xlsx`); does not
  edit/retire/re-cron `daily_report.py` / `daily_review.py`.
- **Parity** — mode-agnostic; PAPER and LIVE render identically (`mode` is a column/context).
- **Follow-ups surfaced by this sheet** (missing-data labels, never fabricated): **W5** exit-trigger
  timestamp (FLAG 1), **W6** `trade_excursions` provenance tag (FLAG 3 RECON/BACKFILL split),
  **W7** `orders.exchange_order_id` capture, **W8** per-trade closure_source (FLAG 2 — see note);
  plus existing **W2** `trades.broker_margin_blocked`.
- **Data note** — a trade whose stored `exit_reason` disagrees with its realised P&L (e.g. the
  30-Jun CGCL `TGT_HIT` at a loss) is rendered faithfully to the DB — the forensic master reflects
  stored truth, it does not correct it.

### Closure Type source note (which values are distinguishable today; which await W8)
Verified 01-Jul against code + real data. The label is TRUE, not convenient — no time heuristics.

| Close path | `trades.exit_reason` | `trades.status` | `reconciliation_log.check_name` | Positively identifiable? → label |
|---|---|---|---|---|
| SL-hit | `SL_HIT` / `EMERGENCY_SL_TICK` | CLOSED | (none) | **YES → SL** |
| TGT-hit | `TGT_HIT` | CLOSED | (none) | **YES → TGT** |
| Emergency-path EOD | `EOD_SQUAREOFF` | CLOSED | (none) | **YES → EOD_SQUAREOFF** |
| Reconciler stuck-exit finalize (incl. kill) | `MANUAL` | CLOSED_MANUAL | **`STUCK_EXITING`** (reconciler-INITIATED) | **YES → RECON_CLOSE** (rare) |
| Orphan / oversell recovery | (null) | FAILED | `ORPHAN_*` / `SYSTEM_OVERSELL` / `INFLIGHT_ORPHAN*` | **YES → ORPHAN_RECOVERY** |
| **Daily-EOD squareoff (15:17)** | `MANUAL` | CLOSED_MANUAL | `MANUAL_CLOSE` (reconciler *detects*) | **NO → SYSTEM_CLOSE** |
| **Operator-manual close** | `MANUAL` | CLOSED_MANUAL | `MANUAL_CLOSE` | **NO → SYSTEM_CLOSE** |
| **RMS / un-relayed SL** | `MANUAL` | CLOSED_MANUAL | `MANUAL_CLOSE` | **NO → SYSTEM_CLOSE** |

- **`MANUAL_CLOSE` is the reconciler DETECTING an external close (`mark_trade_manually_closed`), NOT a
  repair** — so it is NOT mapped to RECON_CLOSE (which would over-claim). Only `STUCK_EXITING`
  (reconciler-initiated finalize) is RECON_CLOSE.
- The daily-EOD squareoff writes **no positive per-trade marker** (`eod_squareoff.py` only sets
  `exit_reason='EOD_EXIT_FAILED'` on failure and CANCELLED on stale-pending; success is finalized by
  the reconciler as `MANUAL_CLOSE`). `eod_squareoff_log` is **per-day** (no trade key). So daily-EOD /
  operator / RMS are genuinely **indistinguishable** → the honest bucket is **SYSTEM_CLOSE**
  ("external close via the manual/system path; trigger not separately recorded"), NOT RECON_CLOSE and
  NOT a time-guessed EOD. A positive EOD marker, when present, still wins as EOD_SQUAREOFF; a
  co-occurring `MANUAL_CLOSE` is treated as EOD bookkeeping. `RECON_EOD_CLOSE` only when BOTH a
  reconciler-initiated marker AND a positive EOD marker exist.
- **W8 (permanent fix, one line):** at close time, each close path writes a per-trade
  `trades.closure_source` (e.g. daily-EOD squareoff → `'EOD_SQUAREOFF'`; kill → `'KILL'`; leave manual/RMS
  as `'EXTERNAL'`) — or the daily-EOD job sets `trades.exit_reason='EOD_SQUAREOFF'` when it fires the exit —
  so SYSTEM_CLOSE splits into positively-identified EOD vs operator/RMS instead of collapsing to
  `MANUAL_CLOSE`. Until then, SYSTEM_CLOSE is the truthful label.

---

## Sheet: Signals  (built by `reports/daily_trade_review.py` sheet 2, 01-Jul-2026)

**BASIS (honest, forced by the data):** *one row per signal that REACHED STORAGE* — i.e. a
persisted `signals` row. Duplicates are dropped at the webhook TTLCache/fingerprint dedup
**before** any INSERT (`signals/webhook_receiver.py` returns `status='DUPLICATE'` with no row),
so they have **no per-signal row** — only aggregate counts in `webhook_audit`. Score/secondary/
capital REJECTS **are** stored (a `QUEUED` row updated to `REJECTED_*`). Verified 30-Jun: 46,337
received (webhook) → 8,477 (18%) reach storage; 37,860 (82%) dropped pre-storage.

**Reconciliation (two levels, no fabricated identity):**
- **Webhook-level (aggregate context):** Received = `SUM(webhook_audit.signals_accepted+signals_rejected)`;
  reached-storage = `SUM(signals_accepted)` (== `COUNT(signals)`); dropped-pre-storage = `SUM(signals_rejected)`
  (dupes + invalid/out-of-window/backpressure; **not** broken down per-signal → **W9**).
- **Storage-level (this sheet's rows, Δ=0 identity):** `Received(stored) = Qualified + Rejected + Skipped/Other`.
  Duplicate is **NOT** an additive storage bucket (webhook dupes have no rows; only symbol-level
  `REJECTED_DUPLICATE_SYMBOL` is stored, a subset of Rejected).

| Field | DB Source (table.column) | Classification | Comments |
|---|---|---|---|
| Date / Signal Time / VM Receipt | `signals.received_at[:10]` / `.triggered_at` / `.received_at` | **EXISTS** | |
| Processing ms | `screener_results.latencies` (JSON total) | **DERIVABLE** | sparse; `N/A` if unparseable |
| Symbol / Strategy | `signals.symbol` / `.strategy` (== `.scanner`) | **EXISTS** | |
| Trade Type / Direction | `trades` (via `signals.trade_id`) — traded signals only | **EXISTS (partial)** | `—` for non-traded (signals row stores no direction/product) |
| Score | `screener_results.score` (by signal_id) | **EXISTS** | `N/A` for pre-score rejects (no screener row, e.g. SHADOW_INNING) |
| Min Score / Score Δ | `config_snapshots.config_json $.scoring.min_pass_score` | **EXISTS** (W0) | `— pending W0` for pre-W0 dates |
| Qualified / Rejected / Duplicate (Y/N) | derived from `signals.status` bucket | **DERIVABLE** | Qualified=PROCESSED/TRADED/PLACEMENT_FAILED/…; Rejected=`GLOB REJECTED*`; Duplicate=`*DUPLICATE*` (stored subset only) |
| Rejection Reason | `signals.rejection_reason` ∥ `signals.status` | **EXISTS** | |
| Secondary Filter Result | `screener_results.status` | **EXISTS** | `not reached` if it stopped earlier |
| Capital Check Result | derived from stage (`REJECTED: <status>` / `PASSED` / `not reached`) | **DERIVABLE** | |
| Final Decision | derived from bucket (TRADED/REJECTED/SKIPPED/…) | **DERIVABLE** | |
| Processing Stage Reached | `_signal_stage()` over `signals.status` + screener-row + trade_id | **DERIVABLE** | RECEIVED→SCORED→SECONDARY_FILTER→CAPITAL_CHECK→ORDERABLE→ORDER_CREATED |
| Signal ID / Trade ID | `signals.signal_id` / `signals.trade_id` | **EXISTS** | Trade ID links to the Orders sheet |

### Notes / guardrails (Signals)
- **Follow-up W9 (permanent fix, one line):** persist the dropped-pre-storage signals (or a raw
  receiver-signal log) with a per-signal drop reason (duplicate / invalid / out-of-window / backpressure),
  so the all-signals denominator becomes **per-signal complete** and Received = Qualified + Rejected +
  Duplicate can hold at the row level. Until then, SYSTEM basis = "signals that reached storage".
- Conditional formatting: rejected → amber, duplicate → grey, qualified → green.
- Cross-sheet integrity (30-Jun): the 18 Qualified (ORDER_CREATED) signals == the 18 Orders trades.

---

## Sheet: Reconciliation  (built by `reports/daily_trade_review.py` sheet 3, 01-Jul-2026)

The **integrity backbone** — never fake PASS, never fake FAIL, never compute outside the DB.
Each block: `Identity | LHS | RHS | STATUS (PASS/FAIL/PENDING_CAPTURE) | Verified-At | Detail`.
An **OVERALL VERDICT** (the Dashboard banner reads this): FAIL if any block FAIL; else
"PASS — N pending capture" if any PENDING; else PASS.

| Block | Identity | DB Source | Status logic |
|---|---|---|---|
| 1 · SIGNAL-STORAGE | `stored = qualified + rejected + skipped + other` | `signals` (via `build_signal_records`) | **PASS** iff Δ=0; Δ = #**unmapped** statuses (schema-drift → **FAIL**). Funnel context: `webhook_audit` received → stored; dropped pre-storage = PENDING_CAPTURE (W9) |
| 2 · ORDER | `qualified = order_placed_ok + placement_failed` | `trades` + `orders` (ENTRY/CO leg presence) | **PASS** iff Δ=0. placed_ok = trade has an entry order; placement_failed = none (rejected pre-order) |
| 3 · TRADE | `placed = entered + cancelled + rejected/failed + pending` | `trades.status` | **PASS** iff Δ=0; Δ = #unmapped statuses (schema-drift → **FAIL**). `entered` = STATUS reached a position (open/partial/exiting/closed/closed_manual). **Renamed from "filled" in Phase-B.1** so the word "filled" has ONE meaning report-wide — the Dashboard's qty-based execution metric. A `CLOSED_MANUAL` record with `qty_filled=0` (entry order cancelled) is `entered` here (status class) but NOT `filled` on the Dashboard — two different, now-unambiguous counts. |
| 4 · CAPITAL | `opening + realized_pnl = closing` (ledger == trades) | `fm_ledger` (INIT `balance_after`; `RELEASE_USED.pnl_delta`) vs `Σ trades.net_pnl` (CLOSED/CLOSED_MANUAL) | **PASS** iff cross-source drift ≤ ₹1.00; else **FAIL**. `PENDING_CAPTURE` if no INIT row |
| 5 · BROKER | `system P&L = broker P&L · positions · margin` | — (broker P&L/positions/margin **not persisted**) | always **PENDING_CAPTURE** (never FAIL) — **W2** (broker margin) / **W3** (pnl+position reconciliation wiring) |

### Notes / guardrails (Reconciliation)
- **CAPITAL realized source (important):** this block sums `Σ fm_ledger.RELEASE_USED.pnl_delta` directly
  (the clean trade-close realized, == `Σ trades.net_pnl`, verified 30-Jun = ₹3.24 both) — **NOT**
  `get_daily_realized_net_pnl`, which sums **ALL** `pnl_delta` rows including the EOD `RESET_PNL`
  counter-entry, so post-15:17 it would zero the day's realized. *(**W10** — that reader's earlier cost
  double-subtract (it returned `SUM(pnl_delta) − SUM(costs)` while `RELEASE_USED.pnl_delta` already = gross − costs,
  removing costs twice) — was **fixed 2026-07-17**; it now returns a clean `SUM(pnl_delta)`. The `RESET_PNL`
  ledger row is a **by-design daily EOD reset**, NOT "pollution".)* A genuine divergence (e.g. the known
  RMS-close `costs=0.0` quirk) **FAILs the block honestly** — it is flagged, not hidden.
- **Partition blocks (1, 3) are FAIL-able:** the buckets are explicit status sets, not a silent catch-all —
  an unmapped/new status makes Δ≠0 → FAIL (a schema-drift alarm).
- **FAIL path proven** (build-gate): injecting +₹100 into a closed trade's `net_pnl` on a copy DB flipped
  CAPITAL PASS→FAIL and OVERALL→FAIL; removed after. A reconciliation sheet only ever seen to PASS is unproven.
- **30-Jun result:** all 4 computable blocks PASS; BROKER PENDING; **OVERALL "PASS — 1 pending capture"**.
- **Signals sheet re-labelled funnel-first** (same identity, block 1 here): Received (webhook 46,337) →
  Reached storage (8,477, 18.3%) → Qualified (18) → Traded (18); Rejected/Skipped/Δ under the storage line.

---

## Sheet: Config  (built by `reports/daily_trade_review.py` sheet 4, 01-Jul-2026 — first consumer of W0)

The **rendered** Config sheet: a sectioned two-column key/value view of the day's resolved
`AppConfig`, read **only** from `config_snapshots.config_json` (the day's latest row by `snapshot_ts`).
Nested values render as indented **sub-rows**. **No snapshot for the date** (pre-W0 history) → the whole
sheet is an honest `— pending W0 (no config snapshot for this date)` placeholder, never blank. Header:
`Config snapshot for <date>` + Snapshot ID · Captured-at (`snapshot_ts`) · Mode · Account · trade_type · hash.

| Section | Field(s) | config_json path | Classification | Comments |
|---|---|---|---|---|
| SYSTEM | trade_type / force_intraday_only / delivery_enabled | `$.system.{trade_type,force_intraday_only,delivery_enabled}` | **EXISTS** (W0) | mode / intraday-breaker / delivery-lock state |
| SYSTEM | trading_hours | `$.system.trading_hours.*` | **EXISTS** (W0) | entry_start/entry_end/eod_entry_cutoff/eod_squareoff_time/market_open/market_close (sub-rows) |
| SYSTEM | leverage_map (× multiplier) | `$.system.capital.leverage_map.*` | **EXISTS** (W0) | INTRADAY/COVER_ORDER/BRACKET_ORDER/DELIVERY (sub-rows) |
| SYSTEM | max_capital | — (not in config_json) | **NOT IN CONFIG** | runtime account balance → honest `— not in config (runtime account balance)` |
| RISK | max_open_positions / max_daily_trades / max_consecutive_losses / daily_loss_limit_pct / max_sector_exposure_pct | `$.system.risk.*` | **EXISTS** (W0) | |
| RISK | max_position_value_pct / max_concentration_pct / risk_per_trade_pct | `$.system.position_sizing.*` | **EXISTS** (W0) | |
| SCORING | min_pass_score / high_score_threshold / medium_score_threshold | `$.scoring.*` | **EXISTS** (W0) | |
| SCORING | weights | `$.scoring.steps.*` | **EXISTS** (W0) | per-step weights (Σ=100) as sub-rows, sorted desc |
| SLIPPAGE | model / default_tier / tiers (slippage_bps) | `$.slippage.{default_tier,tiers.*.slippage_bps}` | **EXISTS** (W0) | tier-based bps (liquid/mid/small) — the real shape, **NOT** a price-slab→allowed table |
| SLIPPAGE | entry-gate tolerance | `$.system.entry_gate.{max_entry_slippage_pct,slippage_buffer}` + `$.system.slippage_bands` | **EXISTS** (W0) | entry-slippage abort tolerance |
| BROKER COSTS | brokerage / STT / exchange / GST / SEBI / stamp rates | `$.broker_costs.zerodha.*` | **EXISTS** (W0) | equity rates; futures/options sub-dicts present, not expanded |
| STRATEGY | per-strategy params | — (not in config_json) | **NEEDS_CAPTURE** | `— pending W0.1` — strategies load via StrategyLoader (see Config_data note + W0.1 below) |

### Notes / guardrails (Config sheet)
- **DB-only** — reads `config_snapshots` only, never the YAML. Renders values **faithfully** (raw types; bool→`True`/`False`; None→blank; no unit re-interpretation).
- **Build-gate spot-check (passed):** `min_pass_score=60` · `daily_loss_limit_pct=0.03` · `leverage_map INTRADAY=5.0` · `entry_end=15:00` — each renders from `config_json` unchanged (plus 4 more asserted in `test_daily_trade_review.py`).
- **Follow-up W0.1 (permanent fix):** a strategy-config snapshot writer (`config_strategy_snapshots` table + a writer AFTER `StrategyLoader`) fills the STRATEGY section for future dates — until then it is honestly `— pending W0.1`.

---

## Sheet: Strategies  (built by `reports/daily_trade_review.py` sheet 5, 01-Jul-2026 — first analytics sheet)

Per-strategy analytics, aggregated **from the `trades` truth layer** (the same Orders `records` +
Signals `srecords` the earlier sheets proved) — **NOT** from `strategy_metrics` (a thin, derived,
CLOSED-only EOD rollup: only sharpe/win_rate/avg_pnl/total_trades). Win = `net_pnl > 0`, loss =
`net_pnl < 0` (the system's own definition, `compute_strategy_metrics`). 5 spaced tables, each labelled
with its BASIS; every value sums real per-trade rows — no fabricated aggregates.

| Table | Basis | DB Source | Fields / notes |
|---|---|---|---|
| T1 PERFORMANCE (per strategy) | this day | `trades` (Orders `records`) + `signals` (Signals `srecords`) | signals(reached-storage) · trades · wins · losses · win% · loss% · gross · net · ROI%(=Σnet/Σcapital) · capital_used · max_win · max_loss. Signal-only strategies (0 trades) shown. **win%/loss% are over DECIDED trades (wins+losses)** — they sum to 100% and match the Dashboard headline, and are NOT diluted by never-filled attempts (the `trades` column still counts every record). No decided trade → both render `N/A — no data` (Phase-B.1 FIX 1; helper `_win_loss_pct`). T2/T3 win% use the same denominator. |
| T2 TIME-BUCKET (30-min) | this day | `trades.entry_time` + `signals.received_at` | trades bucketed by ENTRY time; signals by VM-receipt time; empty buckets omitted. |
| T3 LONG vs SHORT | this day | `trades.direction` | signals = the signals that produced these trades (1 per trade). |
| T4 RISK:REWARD (per strategy) | this day | `trades` (planned) + filled legs / `trade_slippage_log` (actual, RR) | planned SL%/TGT% = abs(entry−sys_sl)/entry and abs(sys_tgt−entry)/entry; actual = from filled SL/TGT legs; avg RR = `trade_slippage_log.actual_rr`. |
| T5 RANKING | **TRAILING N sessions** (default 20, by entry date) | `trades` + `trade_slippage_log` over the window | per strategy: trades_n · win% · net ROI% · avg RR · confidence · composite · rank. |

### Composite / ranking (T5 — transparent, shown on-sheet)
`composite = (0.40·win%_norm + 0.40·netROI%_norm + 0.20·avgRR_norm) × confidence`, where
`confidence = min(trades_n / 20, 1)` and each `_norm` is a min-max across strategies (0.5 for all when
equal — never a fabricated spread). Ranked by composite desc. **Confidence deliberately stops a
low-sample strategy from topping a high-sample one:** a 1-trade/100%-win strategy scores ~1.0 on raw
normalized metrics but × 0.05 confidence → composite ~0.05, ranking it BELOW an 8–15-trade strategy.
The window is stated on-sheet and shrinks to however many distinct trade-sessions exist (e.g. 11 on the
30-Jun DB); confidence still divides by 20, so nothing reaches full confidence on a short window.

### Notes / guardrails (Strategies)
- **DB-only**, mode-agnostic (PAPER/LIVE render identically). Reuses the day's already-built Orders/Signals
  records for T1–T4; one trailing query for T5.
- **`strategy_metrics` intentionally NOT used** — it drops CLOSED_MANUAL and lacks signals/gross/ROI/
  direction/time-bucket/RR. The raw `trades` layer is authoritative and matches the Orders sheet.
- **Build-gate (both parts, PASSED)** on the real 30-Jun VM backup: (a) T1 aggregates recomputed
  independently for every strategy (fresh loop) + a raw-SQL cross-check on the 2 highest-trade strategies
  (`DATE(created_at)`) — all match; (b) composite sanity — the 1-trade/100% strategies are down-weighted
  (composite ~0.05, rank #7–9) while an 8-trade/50% strategy tops (#1); components shown for top+bottom.
  T3 LONG net = ₹3.24 over 18 trades (== the Orders TOTALS).

---

## Sheet: Slippage  (built by `reports/daily_trade_review.py` sheet 6, 01-Jul-2026 — last analytics sheet)

Absorbs the parked "Slippage Phase-2 reports". Source = `trade_slippage_log` (the per-trade roll-up
`orders/slippage_recorder.py` writes) + the Orders `records` (gross P&L + entry tolerance). DB-pure,
mode-agnostic. 6 blocks.

**Decomposition (3 legs, verified against `orders/slippage_recorder.calc_rr_damage_pct`):** total slip cost
per trade = `(entry_slippage_rs + sl_slippage_rs − tgt_slippage_rs) × qty` — ENTRY adverse(+), SL-exit
adverse(+), TGT-exit favourable(−). The 3 components sum to the total by construction. **There is NO 4th
leg** — SL/TGT ARE the exits; EOD/manual-exit slippage is NOT recorded by the roll-up (flagged).

**Tier note (important):** `slippage.tiers` (liquid/mid/small bps) is resolved at RUNTIME by
`broker/slippage_engine.tier_for(symbol)` via InstrumentCache (a paper-fill model) and is **NOT persisted
per trade** — no DB-pure tier exists. So Block 2 buckets by the RECORDED `price_band`; the configured tier
bps (from W0 `config_snapshots`) are shown as a reference. Follow-up **W12** = persist the resolved tier per trade.

| Block | Content | DB Source |
|---|---|---|
| 1 SUMMARY + DECOMPOSITION | total slip cost · ENTRY/SL/TGT component totals (sum to total) · slippage as % of gross P&L | `trade_slippage_log` + Orders `records.gross` |
| 2 BAND ANALYSIS | per `price_band`: trades · avg/max/min actual entry bps · total cost (+ configured tier-bps reference) | `trade_slippage_log.price_band` + `config_snapshots` |
| 3 STRATEGY-WISE | per strategy: trades · avg bps · total slip cost | `trade_slippage_log.strategy_name` |
| 4 STOCK-WISE | per symbol: trades · avg/max bps · total cost | `trade_slippage_log.symbol` |
| 5 WORST-20 | by slip cost: seq · symbol · strategy · entry slip ₹/sh · total cost · excess-over-tolerance | `trade_slippage_log` + Orders `records.slip_delta` |
| 6 10-DAY TREND | last 10 sessions: date · trades · avg bps · total cost | `trade_slippage_log.trade_date` (trailing) |

### Notes / guardrails (Slippage)
- **DB-only**, PAPER/LIVE identical. Aggregates at full precision, rounds once for display — so the
  decomposition sums to the total **exactly** (no rounding leakage).
- **Build-gate (all three, PASSED)** on the real 30-Jun VM backup: (a) total slip cost ₹19.65 == Σ per-trade
  recomputed independently from the log; (b) the 3-leg decomposition ENTRY(−0.13)+SL(0.20)+TGT(19.59)=19.65
  == total (no leakage), each component matching the independent sum; (c) band '200-300' stats recomputed
  independently match. Signal surfaced: slippage cost = 249% of the day's ₹7.89 gross (dominated by CGCL's
  adverse TGT_HIT-at-loss exit) — rendered faithfully.
- **Follow-ups:** **W12** persist the resolved slippage tier per trade (enables true per-tier vs-expected);
  EOD/manual-exit slippage capture (the roll-up records only SL_HIT/TGT_HIT exit legs).

---

## Sheet: Dashboard  (built by `reports/daily_trade_review.py`, 01-Jul-2026 — FINAL sheet, placed FIRST)

Summarizes the six sealed sheets. **DB-pure**: every headline derives from the SAME sources the detail sheets
use (the already-built `records`/`srecords`/`sdata`/`slipdata`/`rmeta`) — so the summary CANNOT disagree with the
detail (the build-gate proves it). Deterministic (rule-based) highlights only — no AI narrative (that stays Tier-3).

**7 sections (top-down):**
1. **Reconciliation banner** — reads the Reconciliation OVERALL verdict (green PASS / red FAIL / amber "PASS — N pending"). A FAIL is the headline, ABOVE profitability.
2. **Data coverage panel** — per section: % or status + the W-task (Signals 100%/⟨per-date⟩%-webhook-dropped→W9 [Phase-B.1 FIX 2: computed from `smeta.wh_dropped/wh_received`, was a hardcoded "82%" that was wrong on quiet days — e.g. 16-Jun's true 41.8%] · Orders 100% · Reconciliation 100%-internal/broker-PENDING→W2/W3 · Config post-W0 & W0.1 · Strategies 100% · Slippage 100%-price_band/tier→W12 · MFE/MAE _computed_%→W6 · Telegram 0%→W1).
3. **Yesterday-vs-Today deltas** — prior trading day resolved data-driven (`MAX(trade-date) < today`, skips weekends/holidays); net · win% · capital · avg-slippage · signals · trades · strategy-leader, today/prior/Δ.
4. **Trading summary** — signals (webhook/stored/qualified) · traded/placed/filled · long/short · closure-type breakdown.
5. **Profitability** — gross · charges · net · ROI% · profit-factor · win-rate · avg/max win/loss · slippage cost + %-of-gross.
6. **Capital** — opening · peak · closing · drift · max-concurrent (sweep-line over entry/exit times) · utilization%.
7. **Deterministic highlights** — ~6 rule-based flags (reconciliation≠PASS · high-slippage > threshold + driver · long-vs-short skew · biggest winner/loser · low-sample-strategy count · pending-capture count). Labelled "deterministic, not AI".

**Sources:** reconciliation `rmeta` · Orders `records` · Signals `smeta` · Strategies `sdata` · Slippage `slipdata` · `fm_ledger` (opening/peak/drift) · `trade_excursions` (MFE/MAE coverage) · `config_snapshots` (Config coverage) · prior-day `build_records`/`build_signal_records`.

### Notes / guardrails (Dashboard)
- **Summary-agrees-with-detail is the contract.** Build-gate (all cross-checks PASSED, real 30-Jun VM backup):
  Dashboard net ₹3.24 == Orders TOTALS; banner == Reconciliation OVERALL; long 17 == Strategies T3; closure
  breakdown == Orders; slip ₹19.65 / 249% == Slippage; MFE/MAE 55.6% == independent excursion count; yesterday
  ₹29.34 / 16-trades == independent prior-day aggregate; Δ == today − prior; final tab order == [Dashboard,
  Reconciliation, Orders, Signals, Strategies, Slippage, Config].
- **No AI** — highlights are deterministic; the LLM narrative stays Tier-3.
- **FINAL TAB ORDER** (sheets renamed to drop numeric prefixes): Dashboard · Reconciliation · Orders · Signals · Strategies · Slippage · Config.

---

## Metric definitions (report-wide — one meaning per label)
- **win% / loss%** (Strategies T1/T2/T3, Dashboard direction split + skew highlight): numerator = wins
  (net>0) / losses (net<0); **denominator = DECIDED trades = wins + losses** (a definite outcome). This is the
  SAME denominator as the §5 Dashboard headline win-rate, so every win% in the report agrees and win%+loss%
  sum to 100%. Never-filled / cancelled / still-open records are NOT in the denominator. No decided trade →
  `N/A — no data` (never a misleading 0.0%). Helper: `_win_loss_pct`.
- **filled** (Dashboard trading summary): an EXECUTION metric = `count(qty_filled > 0)` — the trade got shares,
  regardless of how it closed (a `CLOSED_MANUAL`/`SYSTEM_CLOSE` trade with `qty_filled>0` counts).
- **entered** (Reconciliation Block-3): a STATUS-partition class = status ∈ {open, partial, exiting, closed,
  closed_manual}. Distinct from `filled`; a `CLOSED_MANUAL` record with `qty_filled=0` is `entered` but not `filled`.
- **webhook-dropped %** (Dashboard coverage): computed per-date from `smeta.wh_dropped / wh_received`, NOT hardcoded.

## Phase B (parallel-run) — DONE: double-confirmed CONDITIONAL GO (01-Jul-2026)
Ran the new generator PARALLEL to the old `daily_review.py` (DB-pure) + `daily_report.py` (non-DB) across 6 dates
(16/17/22/24/29/30-Jun) on real VM backups — TWICE (independent model runs). Every load-bearing financial fact
agreed or was an explained IMPROVEMENT (the new generator correctly includes `CLOSED_MANUAL` trades the old ones
drop — DB-verified `Σtrades.net_pnl == Σfm_ledger.RELEASE_USED.pnl_delta` every date). All 3 honesty gates held
(16-Jun CAPITAL honest FAIL; 17-Jun sparse MFE/MAE blanks; 24-Jun loss sign). 3 report-only bugs found → **all
fixed in Phase-B.1 (below)**. The old generators showed their own defects (CLOSED-only P&L that self-contradicts
their capital sheets; `daily_report.py` false-MATCH reconciliation; broken signal funnels) — evidence FOR the migration.

## Phase-B.1 (pre-Phase-C fixes) — DONE (01-Jul-2026, `reports/daily_trade_review.py` only; no P&L/capital/recon math touched)
- **FIX 1 — win%-denominator dilution** (Strategies T1/T2/T3 + Dashboard direction-skew): now over DECIDED trades
  (see definitions above); direction-skew highlight gated on decided trades per side (a lone never-filled short no
  longer triggers "SHORT underperforms"). Re-verified on 17/22/24/29/30-Jun: every win% == wins/(wins+losses),
  win%+loss% == 100, T3 aggregate == Dashboard headline.
- **FIX 2 — hardcoded coverage %** → per-date `wh_dropped/wh_received`. Re-verified: 16-Jun 41.8% (was wrongly "82%"),
  busy days genuinely 81–82%.
- **FIX 3 — "filled" label clash** → Reconciliation Block-3 bucket renamed `filled`→`entered` (status class);
  Dashboard `filled` stays qty-based. One meaning per label report-wide.
- Tests: `tests/unit/test_daily_trade_review.py` — 56 pass (5 new fail-on-old regressions + `_trade_bucket` updated).

## Deferred work-items (post-Phase-C, tracked so they are not forgotten)
- **W13 · Multi-Inning / Shadow-Reentry sheet** — the OLD `daily_review.py` has a 36-column `MULTI_INNING_TRACKING`
  view (inning 1 = the real trade; innings 2-3 = SIMULATED re-entries) from the `innings` table (written live by
  `orders/shadow_tracker.py`). The NEW report does NOT surface it. **DEFER (Tier-2), safe:** the `innings` data
  persists in the DB independent of any report and is fully backfillable via `state_store.get_inning_summary_by_date`,
  so retiring `daily_review.py` loses NO data; and the 16-Jun NULL-net forensic signal is caught louder by the
  Reconciliation CAPITAL drift check. **Phase-C guardrails (REQUIRED):** do NOT disable `shadow_tracker.enabled`
  and do NOT drop the `innings` table — `shadow_tracker.is_tracking()` gates live re-entries (it is NOT dead code),
  and `daily_report.py` also still reads the table. Add W13 as a follow-up sheet whenever convenient.

## Tier-1 report COMPLETE — all 7 sheets built + cross-validated (01-Jul-2026)
Dashboard + the six detail sheets are built, each with a passing build-gate. Phase B done (above); Phase-B.1 fixes done.
- **Phase C — DEPLOYED (01-Jul ~21:37 IST, `main 01e07b7`).** `daily_trade_review` LIVE @ 16:07 Mon-Fri
  (`config/cron_registry.yaml`, monitored → heartbeat); `daily_review` **DELETED** (git rm module + dedicated test +
  registry entry per Rama's directive — git history is the rollback; its `reports/daily/` outputs removed);
  `daily_report` kept in parallel for a ~3–5-day bake-in. `main()` `--date` defaults to today IST + records a
  `cron_heartbeat`. `scripts/system_manager.py` gained an EOD check for the new xlsx. v41 migration applied on deploy;
  the manual-run gate PASSED (7-sheet 1 MB workbook, net=3.24 ties to Phase B, heartbeat SUCCESS). **W13 guardrail
  honoured:** `shadow_tracker.enabled` + the `innings` table untouched (Multi-Inning view deferred).
  As-run steps + rollback: **`docs/phase_c_cutover_runbook.md`**.

_Last updated: 2026-07-01 — W0 seeded Config_data; Orders + Signals + Reconciliation + Config + Strategies + Slippage + Dashboard built (all 7 sheets, cross-gates passed, tab order Dashboard·Reconciliation·Orders·Signals·Strategies·Slippage·Config); **Phase B parallel-run DONE (double-confirmed CONDITIONAL GO)**; **Phase-B.1 fixes DONE** (FIX 1 win%-denominator / FIX 2 per-date coverage % / FIX 3 "filled"→"entered" label; 56 tests pass; re-verified on affected dates, 3 honesty gates still hold); metric definitions recorded (win%/filled/entered/coverage); **W13 Multi-Inning deferral logged with Phase-C guardrails**. **★ NEXT = Phase C (retire daily_review + re-cron; keep shadow_tracker enabled + innings table).**_
