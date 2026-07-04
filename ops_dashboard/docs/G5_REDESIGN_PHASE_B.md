# G5 — OPS DASHBOARD REDESIGN · PHASE B: DESIGN

**Date (IST):** 04-Jul-2026 (Sat, market CLOSED) · **Mode:** DESIGN ONLY — zero code/schema/route/config edits.
**Model:** Opus 4.8 (1M), max effort. **Predecessor:** `G5_REDESIGN_PHASE_A.md` (ACCEPTED) — its `file:line` / `spec NN §` citations and gaps G-1..G-6 are the design baseline.
**Deliverable:** this document. The only repo writes are this doc + PATHS.md + SYSTEM_MAP.md GUI-section; memory updated separately.

**Git posture:** DESIGN — I **HELD** (no branch/commit/push) per the no-push posture + in-flight working-tree changes. Docs written to tree, ready for `gui-g5-phaseB-04jul` when Rama chooses. No code/schema/route/config touched.

## Binding constraints (enforced throughout)

**Rama's 10 LOCKED DECISIONS** L1 redesign-in-place · L2 preserve-first (labels may rebrand, internal names stay) · L3 Broker→System→Delta→Visibility, honest-unavailable never faked · L4 Controls = read-only summary (A) now + G3 placeholder (B), no writes · L5 Capital & Risk = one screen under **Operations**, composes existing endpoints · L6 Reports endpoint UNTOUCHED in B · L7 XLSX = one shared component, 2 flags, default OFF · L8 single **System Score = `screener_results.score`**, drop "Signal Score" · L9 Positions/Holdings two-state AVAILABLE/UNAVAILABLE · L10 Events+Alerts merged into screens.

**Parity (global):** `mode` (PAPER/LIVE) is a **displayed data attribute** sourced from `session.mode` (`db_reader.get_session_info`) — never a branch. **No screen introduces a mode-specific code path.** The single DB is single-mode; trades carry no mode column (stated, `risk_capital.py:116-117`). Per-screen parity notes appear only where mode is rendered.

**Additive-only contract rule (THE load-bearing design invariant):** Several contract assertions are **equality**, not subset — `set(rankings) == {4 keys}` (`test_api_contract.py:44`), `len(service_health) == 6` (`:11`), `len(stages) == 13` (`:20`), `len(rows) == 8` & `len(groups) == 6` (`:29,31`). Therefore **new data ALWAYS lands in a NEW response field; existing keys, their values, and their pinned lengths are never mutated.** New ranking modes → `rankings_extra` (not `rankings`); extra service detail → new fields on each unit dict / the `/api/services` payload (never grows `/api/dashboard.service_health` past 6); Capital&Risk extra limit groups → a new field (never grows capacity `groups` past 6). This keeps all 238 cases green while every screen gains data.

**Menu (final, L5 places Capital & Risk under Operations):**
```
Dashboard (landing)
Trading:       Strategies · Signals · Orders · Positions · Holdings
Analytics:     Strategy Ranking · Strategy Health · Scanner Attribution ·
               Trade Explorer · P&L Analytics · Slippage Analytics · Execution Analytics
Operations:    Live Activity · System Health · Capital & Risk · Configuration · Controls
Investigation: Audit · Trade Logs · System Logs
```
21 menu screens + Login = **22 screens designed** (§A). **NEW screens** (no current equivalent): Strategy Ranking, Strategy Health, Scanner Attribution, Trade Explorer, P&L Analytics, Live Activity, Trade Logs — marked ⭐ below.

**Global table standard (applies to every data table; designed once in §B, referenced per screen):** sort ▲▼ on all columns, rows-per-page selector, common column order (Trading Date · Time · Strategy · Scanner · Symbol · Trade Type · Direction · **System Score** · …), Inter/Segoe/IBM-Plex ≥13px, high contrast, flag-gated XLSX export (L7). Not repeated per screen.

---

## SECTION A — SCREEN-BY-SCREEN DESIGN

Format per screen: **Purpose · Layout (wireframe) · Feeds (endpoints) · Additive vs existing · Merges (L10) · Two-state (L9/L3) · Export (L7) · Table**. Reuse screens are terse; NEW/complex screens get detail.

### 1. Login `spec 01`  — PRESERVE (rebrand-only, zero backend change)
- **Purpose:** authenticate (single-user PBKDF2+TOTP+lockout, `auth.py` untouched).
- **Layout:** desktop 40% Control-Tower blueprint visual (self-hosted SVG/data-URI, low-opacity monochrome, **no CDN** — I7 CDN gate) | 60% login card. Mobile: card only.
- **Rebrand (L2, label layer only):** "Ops Dashboard"→"AlgoCore Systems", subtitle "Operations Control Tower", "Secure Read-Only Access"; footer "Read Only · TOTP Protected". Fields unchanged (Username/Password/6-digit TOTP), button "Sign In".
- **Feeds:** `/login` GET/POST (unchanged). **Additive:** none. **Parity:** n/a.

### 2. Dashboard `spec 02` — PRESERVE + EXTEND-ADDITIVE
- **Purpose:** Mission-control; answer system/signals/trades/limits/strategies/stuck in 15s.
- **Layout:**
```
[Header: Trading Date · IST clock]
[TODAY'S TRADING SUMMARY — KPI row: Signals Rcvd/Acc/Rej · Orders Created/Filled ·
   Open/Closed Trades · Wins/Losses · Win% · Profit Factor · Today's P&L]   (clickable→screens)
[LIVE PIPELINE — 13 stage cards, horizontal, Count + Last-Event time]  (CONTRACT: keep 13)
[DAILY CAPACITY MONITOR — Used/Cap/Left/Usage% + color 0-60/60-80/80-100]
[SERVICE HEALTH strip (6 units) · Last Update]   [RECENT EVENTS feed]   [ALERTS banner ⟵ merged]
[STRATEGY SUMMARY (compact): Strategies 15 · Active/Quiet/Silent · Best/Worst]
```
- **Feeds:** composes `/api/dashboard` (summary/service_health/events/freshness) + `/api/pipeline` (13 stages, last_event already present) + `/api/capacity` (pct already present, `capacity.py:56`) + `/api/strategies` (compact summary + best/worst from `rankings.net_pnl`).
- **Additive:** `/api/dashboard.summary.counters` gains `signals_rejected, orders_created, orders_filled, closed_trades, wins, losses, win_rate, profit_factor` (all from existing `db_reader` fns — `orders_entry_counts`, `trades_closed_counts`, `pnl_summary_today`; Profit Factor = Σwin ÷ |Σloss|). Strategy-Tower cards **removed from Dashboard** → moved to Strategy screens (`spec 02 §REMOVE`).
- **Merges (L10):** Recent Events (system_events) + Alerts banner (telegram/sentinels via `/api/alerts`).
- **Export:** no. **Parity:** mode pill (display-only, existing `base.html:88`). **Table:** none (KPI/cards).

### 3. Strategies `spec 02-Strategies` — PRESERVE + EXTEND-ADDITIVE
- **Purpose:** Strategy Control Tower — dense sortable table + detail drilldown.
- **Layout:** `[KPI: total/active/quiet/silent/disabled] [Filters: date-range·strategy·scanner·status·trade-type·direction·reset] [MAIN TABLE ↓] [Detail page: tabs Overview/Signals/Orders/Trades/Performance/Capital/Risk/Logs]`. Strategy hierarchy grouping (First Pullback → Long/Short) via base-name.
- **Table cols:** Strategy·Scanner·Signals·Orders·Trades·Success%·Win%·P&L·Allocated·Used·Remaining·Usage%·Last Signal·Last Trade·Status (all sortable, rows/page).
- **Feeds:** `/api/strategies` + `/api/strategies/<name>` (already emit the 7 groups + scanner sets + capital view).
- **Additive:** per-row `performance.profit_factor`, `trading.sl_hits`/`tgt_hits` (new GROUP BY `trades.exit_reason`), Detail "Logs" tab via `/api/logs?id=<trade/strategy>` (reuse `log_reader` ref_id). Existing 7-group shape unchanged (contract `:41`).
- **Merges:** none. **Two-state:** n/a. **Export:** XLSX (flag). **Parity:** mode from tower `basic.mode`. **Table:** full standard.

### 4. Signals `spec 03` — PRESERVE + EXTEND-ADDITIVE
- **Purpose:** birthplace of every trade; full lifecycle per signal.
- **Layout:** `[Filters: date·symbol·strategy·scanner·trade-type·direction·status·signal-result·reset] [MAIN TABLE] [row-click → Lifecycle timeline modal] [Scanner Attribution → its own screen]`.
- **Table cols (common order + L8 single score):** Trading Date·Time·Symbol·Strategy·Scanner·Trade Type·Direction·**System Score**·Status·Trade Result·Trade Duration. Signal-age color (0-30s green/30-120 yellow/>120 red/expired gray). Reject Reason always visible + **Required Score** (min_pass_score).
- **Feeds:** `/api/signals` (list + denominator strip preserved) + NEW `/api/signals/<signal_id>` (lifecycle timeline).
- **Additive:** `list_signals` rows gain `system_score` (`screener_results.score` join by signal_id, `schema.sql:619`), `trade_type`/`direction`/`trade_result`/`trade_duration` (signal→trade join via `trades.signal_id`), `required_score` (config `min_pass_score`). Lifecycle = `trade_story` service (§F). **L8:** NO "Signal Score" column. Raw payload available via `signals.webhook_payload` in the detail.
- **Merges:** none. **Export:** XLSX (flag). **Rows/page:** default 200. **Parity:** rows are mode-agnostic.

### 5. Orders `spec 04` — PRESERVE + EXTEND-ADDITIVE
- **Purpose:** order creation→fill→broker interaction visibility.
- **Layout:** `[Filters] [MAIN TABLE w/ grouped headers: Entry/SL/TGT (System|Broker-Filled), Slippage (Allowed|Actual|%)] [row-click → Order Lifecycle + Broker Response (collapsed)]`.
- **Table cols:** common order + Order ID · **Broker Order ID (= order_id, note)** · Order Type(product) · Status · planned-vs-actual price groups · slippage · execution timing (Signal/Create/Submit/**Exchange-Accept**/Fill).
- **Feeds:** `/api/orders` + NEW `/api/orders/<order_id>` (lifecycle + broker response).
- **Additive:** `list_orders` rows gain `system_score`, `trade_type`/`direction`, planned prices (`trades.sl_initial/tgt_initial/entry_target_price`), `slippage_allowed`/`slippage_actual` (`trade_slippage_log`), `exchange_timestamp` (`order_execution_log`, `schema.sql:1007`). **Note (L2):** "Order ID"=="Broker Order ID" — `orders.order_id` IS broker-assigned (`schema.sql:271`); render one value, label both, no fake internal id.
- **Two-state (L3):** broker **raw** response (Raw Payload/Exchange Response) = **UNAVAILABLE** panel "Not persisted (G-4) — showing `rejection_reason` only". Never fabricate.
- **Export:** XLSX (flag). **Rows/page:** default 100.

### 6. Positions `spec 05` — PRESERVE + EXTEND + TWO-STATE (L9)
- **Purpose:** live positions, exposure, SL/TGT tracking, lifecycle.
- **Layout:** `[KPI: Open · Long · Short · Capital Used · (Current MTM ⟶ UNAVAILABLE) · Realized P&L] [Filters] [TABLE] [row→Position Lifecycle]`.
- **AVAILABLE (system, DB):** qty, entry system price, entry broker price, current value (entry×qty), SL/TGT (system), SL/TGT distance (vs entry), position age, capital used/%, position weight, risk/reward, expected RR, inning#.
- **UNAVAILABLE (G-1, L9/L3):** Current LTP, Current MTM, Unrealized P&L/%, **Current RR**, SL/TGT (broker), Highest Profit%/Drawdown% (live) → explicit **"Pending Broker Source (G4)"** cells (precedent: `/api/positions` already `—`/G4, `trading.py:99`). MFE/MAE shown only for CLOSED trades (`trade_excursions`, post-EOD).
- **Feeds:** `/api/positions` (extend additively: `position_age`, `capital_pct`, `position_weight`, `expected_rr`; a stable `unavailable: {mtm, ltp, unrealized, current_rr, reason:"G4"}` block) + NEW `/api/positions/<trade_id>` lifecycle.
- **Export:** XLSX (flag). **Parity:** mode display-only (`trading.py:95`).

### 7. Holdings `spec 21` — PRESERVE + EXTEND + TWO-STATE (L9), Broker-First (L3)
- **Purpose:** Broker-first reconciliation center (Broker=truth / System=expected / Delta).
- **Layout:**
```
[KPI: Broker Holdings ⟶ UNAVAILABLE · System Holdings N · Matched/Mismatch ⟶ pending · Total Value(system) · Total P&L ⟶ pending]
[SIDE-BY-SIDE: Broker Snapshot (UNAVAILABLE panel) | System Snapshot (gtt_state, full)]
[MAIN TABLE: system rows full; Broker Qty/Delta/Recon-Status = pending-broker cells]
[ORPHAN DETECTOR: system-side signals only]
```
- **AVAILABLE:** System side = `gtt_state` mirror (`holdings_list`), product, system qty, strategy ownership, source.
- **UNAVAILABLE (G-2, L3):** Broker Qty, Delta Qty, Reconciliation Status, current price/value, Last Broker Sync, Broker Account → **"Pending Broker Source (G4/P1)"**. Design note: the future EOD delta source is `eod_broker_reconciliation` (P1) — **UNPUSHED/absent from live schema**; the panel reads it via a graceful reader (empty→pending) so it lights up automatically if P1 ships. **Never fabricate the broker side.**
- **Feeds:** `/api/holdings` (extend: `system_snapshot` full + `broker: {available:false, reason, source:"eod_broker_reconciliation (P1 pending)"}`). **Export:** XLSX (flag).

### 8. ⭐ Strategy Ranking `spec 18` — NEW screen (reuses `/api/strategies`)
- **Purpose:** leaderboard; best/worst, ROI, win%, profit factor, allocation guidance.
- **Layout:** `[KPI: Best · Worst · Highest ROI · Highest Win% · Highest PF · Most Trades] [Ranking-mode toggle: Net P&L|ROI|Win%|Profit Factor|Trade Count] [Period: Today|Week|Month|Custom] [RANKING TABLE w/ 🥇🥈🥉] [Trend arrows]`.
- **Table cols:** Rank·Strategy·Trades·Wins·Losses·Win%·ROI%·Profit Factor·Gross·Net·Avg Trade·Best·Worst.
- **Feeds:** `/api/strategies` (existing `rankings` 4 keys) + NEW **`rankings_extra`** field (roi/profit_factor/trade_count) — **additive, does NOT touch the equality-checked `rankings`** + NEW `/api/strategy-ranking?period=` for Week/Month (multi-period reader).
- **Additive/New:** `rankings_extra`, per-row `composite_score` (0-100, reuse `daily_trade_review` T5 concept), `trend` (Improving/Stable/Declining via trailing-session compare). **Export:** XLSX. **Parity:** ranks mode-agnostic.

### 9. ⭐ Strategy Health `spec 19` — NEW screen (reuses `strategy_tower` health/scorecard)
- **Purpose:** operational health; silent/degraded/warning detection.
- **Layout:** `[KPI: Active·Quiet·Silent·Disabled·Warnings·Errors] [HEALTH TABLE] [Warnings section ⟵ Alerts merged] [row→drilldown Overview/Signals/Orders/Trades/Health/Warnings] [Health timeline]`.
- **Table cols:** Strategy·Status(🟢🟡🟠🔴⚫)·Last Signal·Last Trade·Signals·Orders·Trades·Rejections·**Health Score(0-100)**.
- **Feeds:** `/api/strategies` (health block `:243` + scorecard + silence, already present) + NEW `/api/strategy-health?period=` (week aggregates).
- **New:** `health_score` numeric 0-100 (derived from existing scorecard inputs — activity/acceptance/trade-activity/errors), health timeline (intraday status transitions — derived), week counts.
- **Merges (L10):** Warnings section = per-strategy alerts (`telegram_alerts`/scorecard reasons). **Export:** XLSX.

### 10. ⭐ Scanner Attribution `spec 20` — NEW screen + NEW backend (§F)
- **Purpose:** which scanner creates good opportunities/trades/profit.
- **Layout:** `[KPI: Total/Active Scanners · Best · Worst · Highest Win% · Highest PF] [MAIN TABLE 🥇🥈🥉] [Signal funnel per scanner] [Rejection analysis + Top Reject Reason] [Ranking modes] [drilldown] [Scanner Mapping: Name·URL·Strategy]`.
- **Table cols:** Scanner·Strategy·Signals·Accepted·Rejected·Orders·Trades·Win%·Profit Factor·Net P&L (+ Avg Trade Duration · Quality Score 0-100).
- **Feeds:** NEW **`/api/scanner-attribution?period=`** → NEW `scanner_attribution` service.
- **New backend (no schema):** scanner→trade P&L join — `signals.scanner` (`schema.sql:45`) ← `trades.signal_id` (mutual FK `schema.sql:105-114`) → aggregate signals/orders/trades/win%/PF/net/avg-duration; funnel from `webhook_by_scanner` (exists) + storage funnel; Scanner URL from `scan_webhook_map`. Quality Score = acceptance×conversion×win×profit composite.
- **Export:** XLSX. **Parity:** mode-agnostic aggregation.

### 11. ⭐ Trade Explorer `spec 06` — NEW screen + NEW backend (§F) — "one trade = one story"
- **Purpose:** single most powerful investigation screen; full trade lifecycle in one place.
- **Layout:** `[Filters: date·strategy·scanner·trade-id·symbol·trade-type·direction·result·status] [MAIN TABLE grouped headers Entry/SL/TGT (System|Broker), Slippage (Allowed|Actual|%)] [row→ Trade Lifecycle timeline (Signal→Validated→Risk→Capital→Order→Fill→Position→SL/TGT→Closed, timestamps)]`.
- **Table cols:** common order + Trade ID·Symbol·Duration·Result·Status·ROI%·P&L·Charges·Net·Expected RR·Actual RR·RR Damage%·Exit Reason.
- **Feeds:** NEW **`/api/trades?filters`** (list) + **`/api/trade-story/<trade_id>`** (single assembly) → NEW `trade_story` service.
- **New backend (no schema):** per-trade assembly joining `trades`+`orders`+`signals`+`screener_results`+`trade_slippage_log`+`trade_excursions`+`reconciliation_log` (the GUI analog of `reports/daily_trade_review.py`). Shared with Signals/Orders/Positions/Trade-Logs lifecycles.
- **Two-state:** broker-filled prices where present; live/broker-only fields → pending. **Export:** XLSX.

### 12. ⭐/EXTEND P&L Analytics `spec 08` — NEW endpoint + folds in `/statistics`
- **Purpose:** profitability, attribution, trends, drawdown.
- **Layout:** `[KPI: Gross·Charges·Net·ROI%·Win%·Profit Factor] [Period: Today|Week|Month|Custom] [Filters] [MAIN TABLE by date/strategy/scanner/symbol/trade-type/direction] [Strategy/Scanner/Symbol rankings] [Equity Curve day/week/month, Gross|Net toggle] [Drawdown: Max/Current/Recovery/Duration] [Best/Worst] [Heatmaps: DoW×PnL, ToD×PnL] [P&L Attribution]`.
- **Feeds:** NEW **`/api/analytics/pnl?period&from&to`** → NEW `pnl_analytics` service (multi-day); PRESERVE `/api/pnl` (today widget) + `/api/statistics` (folded in, page composes).
- **New backend (no schema):** multi-period trades/fm_ledger readers; scanner P&L (scanner join); symbol P&L; drawdown + heatmap derivations. **Export:** XLSX. **Parity:** single-mode note carried from `/api/pnl`.

### 13. EXTEND Slippage Analytics `spec 09` — PRESERVE `/api/slippage` + additive
- **Purpose:** execution-quality vs tolerance + business impact.
- **Layout:** `[KPI: Total·Within·Near·Exceeded·Avg·Worst] [Filters incl. price-range·slippage-status] [CORE TABLE] [Price-bucket analysis 0-100..1000+] [Strategy/Scanner/Symbol slippage rankings] [RR Damage] [Trend through day]`.
- **Feeds:** `/api/slippage` (extend additively): `?period` param (default today = current behavior), + `per_scanner`, `per_symbol`, `price_buckets`, `trend` fields. Existing `rows/worst/per_strategy/model` unchanged.
- **New backend:** scanner ranking (join), bucket/trend derivations over `trade_slippage_log.price_band`. **Export:** XLSX.

### 14. EXTEND Execution Analytics `spec 10` — PRESERVE `/api/execution` + additive + TWO-STATE
- **Purpose:** execution speed across lifecycle.
- **Layout:** `[KPI: Signals·Orders·Avg Delay·Fastest·Slowest·Avg Fill] [Timing breakdown table] [Delay analysis] [Strategy/Scanner/Symbol delay rankings] [Delay distribution buckets] [Throughput/min] [Execution Warnings ⟵ Alerts merged] [Trend]`.
- **AVAILABLE:** signal→order, order→fill, total latency (`latency_rows_today`), `order_execution_log` incl. `exchange_timestamp` (Order-Create→Exchange-Accept→Fill).
- **UNAVAILABLE (G-3, L3):** per-stage **Validation/Risk/Capital** times → explicit "Not instrumented (G-3)" — not persisted; would need trading-side instrumentation (schema-pause P-1). Never fabricate.
- **Feeds:** `/api/execution` (extend: `?period`, `exchange_timing`, `throughput`, `per_scanner`/`per_symbol` rankings, `warnings`). **Merges (L10):** Execution Warnings. **Export:** XLSX.

### 15. ⭐ Live Activity `spec 17` — NEW screen + NEW merged feed (§F)
- **Purpose:** Mission-control wall; everything happening, near-real-time.
- **Layout:** `[KPI strip: Time·Signals·Orders·Open·(MTM ⟶ UNAVAILABLE)·Trading Status] [REAL-TIME FEED categorized Signal/Order/Position/Trade/Risk/Capital/System/Alert, traffic-light] [Live pipeline 6-step] [Strategy activity panel] [Recent Winners/Losers] [System Events ⟵ merged] [Alerts banner ⟵ merged] [Market Pulse: signals/orders/trades per min] [Feed filters] [Live Mode ON/OFF]`.
- **Feeds:** NEW **`/api/activity?since=`** → NEW `activity_feed` service (merged time-ordered stream) + reuse `/api/pipeline`, `/api/strategies`, `/api/alerts`, unwired `metrics_client.get_trader_metrics` (`:8080/metrics`) for pulse.
- **New backend (no schema):** merge `signals`+`orders`+`trades`+`system_events`+`telegram_alerts` into one time-ordered feed; per-minute pulse from timestamps. **Two-state:** MTM → pending (G-1). **Merges (L10):** Recent Events + Alerts BOTH land here. **Export:** XLSX (feed). **Live Mode:** frontend poll toggle (5s market / 60s off, `freshness`).

### 16. EXTEND System Health `spec 11` — merge `/api/services`+`/api/vm` into one page
- **Purpose:** infra health + trading readiness.
- **Layout:** `[KPI: Overall·Healthy·Warning·Failed·Uptime·Last Check] [SERVICE TABLE: Service·Status·Started·Last Heartbeat·Uptime·Response] [VM Health] [DB Health] [Broker Health] [External Deps] [Service Events ⟵ merged] [Health Trends] [Auto-Recovery] [TRADING READINESS 🟢/🔴] [Alerts ⟵ merged]`.
- **Feeds:** page composes `/api/services` + `/api/vm` (both preserved; `/api/dashboard.service_health` stays 6-pinned — enrichment goes on `/api/services`).
- **AVAILABLE (additive):** `all_units`+started-at/uptime (`systemctl show`, host_reader extend), DB size + last-backup (fs read), token status (token-file read), Chartink recency (`webhook_audit`), Tailscale (host check), Trading Readiness (reuse `preflight_runs`), auto-recovery (`reconciliation_log`).
- **UNAVAILABLE (G-6, L3):** CPU%/RAM% (psutil sentinel −1.0, existing honest note `system.py:62`), DB conn-count/query-time, broker login-status (no session) → explicit not-collected. **Merges (L10):** Service Events + Alerts. **Export:** XLSX.

### 17. EXTEND Capital & Risk `spec 07` — NEW consolidated page under **Operations** (L5)
- **Purpose:** one control-tower view: Capital · Risk · Exposure · Limits.
- **Layout (4 zones):**
```
[CAPITAL: Total·Intraday·Delivery·Used·Available·Util%]  [RISK: Daily Loss Limit·Current Loss·Remaining·Util%]
[EXPOSURE: Long·Short·Net·Open·Exposure%]                [LIMITS MONITOR: Configured/Used/Remaining/Status + color]
[Strategy Capital Allocation table] [Recent Risk Events ⟵ merged] [Capital History timeline] [Top Capital Consumers]
```
- **Feeds:** page **composes** `/api/risk` + `/api/capital` + `/api/exposure` + `/api/capacity` (`groups`/`rows`) — **all preserved (L5), none deleted**.
- **Additive:** `/api/exposure` gains `by_direction` (Long/Short/Net split — new GROUP BY `trades.direction`); capital-history timeline from `fm_ledger` + equity curve (realized); Limits color 0-60/60-80/80-100 (pct exists).
- **Merges (L10):** Recent Risk Events (risk/capital rejections from `audit_feed`/`signals`). **Two-state:** live MTM in exposure → system value only (G-1 note). **Export:** XLSX.

### 18. Controls `spec 16` — DESIGN (A) NOW, (B) G3 PLACEHOLDER (L4)
- **Purpose:** safe operational awareness (95% view / 5% control).
- **(A) Read-Only Controls Summary (DESIGN NOW — all already readable):**
```
[Operating Mode: PAPER/LIVE (display)] [Trading Permissions: trade_type INTRADAY/DELIVERY/BOTH, force_intraday_only]
[Active Strategy Toggles: 15 strategies enabled/disabled (config)] [Active Scanners (scan_webhook_map)]
[Active Runtime Limits: max_trades/positions/concentration/daily-loss (config)] [Telegram Alerts on/off (config)]
[Kill-Switch State: INACTIVE/SOFT/HARD + reason/since] [Readiness 🟢/🔴 (preflight)]
[Control History (mini-audit ⟵ audit_feed)]
```
  - **Feeds:** compose `/api/config` (config_view), `/api/risk` (kill-switch), `/api/services`+preflight (readiness), `/api/strategies` (enabled flags), `/api/audit` (history). Read-only, DB-backed. **Zero writes.**
- **(B) G3 Future Controls (PLACEHOLDER, L4):** the toggle/edit/pause/apply-revert UI is rendered **disabled** with a labeled "Future — G3 control-plane (requires design; not implemented)" banner. **NO write path, NO isolation exception, NO backend module.** Rationale cited: `test_isolation.py` read-only DB + G3.0 (kill-switch in-memory, config needs restart, no hot reload — `G3_0_CONTROL_PLANE_INVESTIGATION.md`).
- **Export:** no. **Parity:** simulation-mode visibility (Live/Paper/Disabled) = display-only.

### 19. EXTEND Audit `spec 12` — PRESERVE `/api/audit` + additive + honest gaps
- **Purpose:** accountability/traceability.
- **Layout:** `[KPI: Total·Today's Changes·Config Changes·Control Actions·Failed·Last] [Filters: date·category·action·status·module·user] [MAIN TABLE: Date·Time·Category·Module·Action·User·Status·Source·Ref ID] [row→Old vs New] [Timeline] [Critical Changes pinned] [Retention: oldest/newest/total]`.
- **Feeds:** `/api/audit` (6-source merge preserved) + additive `categories`, `old_new` (config diff from `config_snapshots` via `config_view`), retention counts.
- **UNAVAILABLE (G-5, L3):** **User / who-changed / auth login-logout / control-action timeline** = "Not captured (G-5)" — the system has no per-change author, no DB-logged auth, no control writes. Config *drift* IS shown (old/new); *authorship* is not faked. **Export:** XLSX.

### 20. ⭐ Trade Logs `spec 13` — NEW screen (reuses `log_reader` + `trade_story`)
- **Purpose:** forensic trade investigation console; reconstruct any trade.
- **Layout:** `[KPI: Total Events·Successful·Failed·Warnings·Errors·Last] [Filters: date·trade-id·order-id·symbol·strategy·scanner·trade-type·direction·event-type·status] [MAIN LOG TABLE: Date·Time·Trade ID·Order ID·Symbol·Strategy·Scanner·Event Type·Status·Message] [Trade Timeline] [Component tracking] [Request/Decision/Output] [▶ Trade Replay]`.
- **Feeds:** reuse `/api/logs?id=<trade_id>` (`log_reader.parse_structured` ref_id filter, existing) + NEW `/api/trade-story/<trade_id>` (shared with Trade Explorer) + `system_events`.
- **New:** trade-event assembly + replay (frontend reconstruction from `trade_story`). No new schema. **Two-state:** Resolution Status where absent → "—". **Export:** XLSX.

### 21. EXTEND System Logs `spec 14` — PRESERVE `/api/logs` + additive
- **Purpose:** technical troubleshooting console.
- **Layout:** `[KPI: Total·Info·Warn·Error·Critical·Last] [Filters: date·service·module·severity·event-type·status] [MAIN LOG TABLE] [Component timeline] [Dependency tracking] [Error investigation] [Recovery tracking] [▶ System Replay] [Trading Impact] [Retention]`.
- **Feeds:** `/api/logs` (file tail preserved) + additive severity counts, service/module/event-type filters (parse enrich), `system_events` merge, recovery from `reconciliation_log`.
- **UNAVAILABLE (G-5):** Resolution Status/Time not tracked → "—". **Export:** XLSX (tail).

### 22. Login — see #1 (listed first for menu clarity).

**Non-menu screens (endpoints PRESERVED, L1/L6):** Statistics→folds into P&L Analytics + Strategy screens · Exposure/Capacity/Risk/Capital→Capital & Risk zones · VM/Services→System Health · Alerts→merged (§3.3) · **Reports→endpoint UNTOUCHED (L6)**, no menu entry; per-screen export replaces it. **No endpoint deleted.**

---

## SECTION B — COMPONENT ARCHITECTURE

Alpine-over-JSON (G2 precedent, `base.html:186` `pageBase(endpoint)`); root polls `/api/dashboard`, broadcasts `ops-refresh`; each page fetches its own endpoint(s). All components self-hosted (no CDN — I7 gate). Building blocks:

| Component | Role |
|---|---|
| **NavShell** | 5-group sidebar (Dashboard/Trading/Analytics/Operations/Investigation), active-state, mobile icon-collapse, AlgoCore label-rebrand. Evolves `base.html:40-68`. |
| **KpiCard / KpiRow** | labeled metric tile, semantic color (green/red/orange/blue), clickable→route, optional sparkline. Generalizes G2b-3 hero row. |
| **DataTable** | sortable ▲▼ all cols, rows-per-page selector, common-column-order, grouped headers (Excel merge style for planned-vs-actual), sticky header, dense ≥13px. **The global-standard workhorse.** |
| **PeriodSelector** | Today/Week/Month/Custom range → emits `?period&from&to`. Analytics screens. |
| **FunnelPipelineCard** | horizontal stage cards (count + last-event + color). Reuses `pipeline_state` colors; Dashboard 13-stage + Live/Scanner funnels. |
| **ScoreChip** | System Score badge (L8 single score) + scorecard badge (GREEN/YELLOW/RED/GRAY, reuse `strategy_score`) + Health/Quality/Composite 0-100 gauges. |
| **StatusChip** | 🟢🟡🟠🔴⚫ health/silence/recon states; traffic-light for Live feed. |
| **EventsAlertsPanel** | merged Recent-Events + Alerts panel (L10); severity chips; composes `/api/alerts`+events. Drop-in on Dashboard/Live/System-Health/Capital&Risk/Strategy-Health/Execution. |
| **TwoStatePanel** | AVAILABLE renders data; UNAVAILABLE renders explicit "Pending Broker Source (G4/P1)" / "Not instrumented (G-3)" / "Not captured (G-5)" (L3/L9). Positions/Holdings/Orders-raw/Execution-timings/Audit-user. |
| **ExportButton** | XLSX/CSV of current filtered rows; **flag-gated** (`table_export_enabled`, default OFF, L7); disabled tooltip "Export disabled (Q3 pending)". |
| **LeaderboardMedals** | 🥇🥈🥉 + TrendArrow (Improving/Stable/Declining). Ranking/Scanner screens. |
| **LifecycleTimeline** | vertical stage timeline w/ timestamps (Signal→…→Closed). Signals/Orders/Positions/Trade-Explorer/Trade-Logs — all fed by `trade_story`. |
| **FilterBar** | date-range/strategy/scanner/symbol/trade-type/direction/status/reset; consistent across all data screens. |

**Design invariant:** every component is data-shape-driven (no mode branch); UNAVAILABLE is a first-class render state, never a blank or a fake number.

---

## SECTION C — SHARED COMPONENT INVENTORY (build-once, reuse-many)

| Component | New/Existing | Used by (screens) | Data contract |
|---|---|---|---|
| NavShell | Extend `base.html` | ALL | static menu |
| DataTable | **NEW** | Strategies, Signals, Orders, Positions, Holdings, Trade Explorer, all Analytics, Audit, Trade/System Logs (≈17) | array of row objects + column spec |
| KpiRow | Extend (G2b-3 hero) | Dashboard, Positions, all Analytics, System Health, Capital&Risk, Audit, Logs (≈15) | `{label,value,color,href?}` |
| FilterBar | **NEW** | all data screens (≈17) | filter model → query params |
| PeriodSelector | **NEW** | Ranking, Health, Scanner, P&L, Slippage, Execution (7) | `{period,from,to}` |
| EventsAlertsPanel | **NEW** (composes existing) | Dashboard, Live Activity, System Health, Capital&Risk, Strategy Health, Execution (6) | `/api/alerts` + events |
| TwoStatePanel | **NEW** | Positions, Holdings, Orders, Execution, Audit, System Health (6) | `{available:bool, reason, data?}` |
| ExportButton | **NEW** (flag) | all data screens (≈17) | current filtered rows + flag |
| ScoreChip | Extend `strategy_score` | Strategies, Signals, Orders, Ranking, Health, Scanner, Trade Explorer (7) | score int / badge |
| LifecycleTimeline | **NEW** (feeds from `trade_story`) | Signals, Orders, Positions, Trade Explorer, Trade Logs (5) | `trade_story` object |
| FunnelPipelineCard | Extend `pipeline_state` | Dashboard, Live Activity, Scanner Attribution (3) | stage array |
| LeaderboardMedals+Trend | **NEW** | Ranking, Scanner Attribution (2) | ranked list |
| StatusChip | Extend | Strategies, Health, Scanner, Live, System Health (5) | state enum |

**Headline: 13 shared components — 9 NEW, 4 existing-extended.** The NEW ones are generic (table/filter/kpi/export/two-state/period/timeline) → built once in G5a, consumed by ~17 screens = the low-risk bulk (§3.2).

---

## SECTION D — NAVIGATION ARCHITECTURE

**5-group icon sidebar** (evolves `base.html:40-68`; inline-SVG icons, no CDN): Dashboard (landing, `/`) · **Trading** · **Analytics** · **Operations** · **Investigation**. Active-state = accent border (existing `nv-item` active). Mobile ≤900px = icon-only collapse (existing). Landing ALWAYS Dashboard; never Signals/Orders/Live (`spec plan §LANDING`).

**OLD route → menu home (no endpoint deleted):**

| Old route/screen | New home | Endpoint disposition |
|---|---|---|
| `/` dashboard | Dashboard | preserve+extend |
| `/strategies,/signals,/orders,/positions,/holdings` | Trading | preserve+extend |
| `/slippage,/execution` | Analytics (Slippage/Execution Analytics) | preserve+extend |
| `/statistics` | folds into **P&L Analytics** + Strategy screens | endpoint preserved, page reuses |
| `/risk,/capital,/exposure,/capacity` | **Capital & Risk** (Operations, L5) | 4 endpoints preserved, page composes |
| `/services,/vm` | **System Health** (Operations) | 2 endpoints preserved, page composes |
| `/alerts` | **merged** (Dashboard/Live/System-Health/Capital&Risk/Strategy-Health/Execution) | endpoint preserved, panels compose |
| `/config` | Configuration (Operations) | preserve+extend |
| `/controls` | Controls (Operations) | stub→read-only summary (A) |
| `/audit,/logs` | Investigation (Audit / System Logs) | preserve+extend |
| `/reports` + `/reports/download` | **no menu** (per-screen export) | **endpoint UNTOUCHED (L6)** |
| — (new) | Analytics: **Strategy Ranking, Strategy Health, Scanner Attribution, Trade Explorer, P&L Analytics**; Operations: **Live Activity**; Investigation: **Trade Logs** | new routes/pages |

New page routes register in `app.py:_PAGES` (additive to the existing dict, `app.py:127-138`) — no existing route changed.

---

## SECTION E — API IMPACT MATRIX (additive-only; existing fields never change)

| Endpoint | Verdict | Additive fields (source) / New shape |
|---|---|---|
| `/api/dashboard` | EXTEND | `summary.counters += {signals_rejected, orders_created, orders_filled, closed_trades, wins, losses, win_rate, profit_factor}` (existing db_reader fns). `service_health` stays **6** (pinned). |
| `/api/pipeline` | **PRESERVE** | 13 stages, `last_event` already present. No change. |
| `/api/capacity` | PRESERVE | `pct` already present; color = frontend. `rows`==8/`groups`==6 unchanged. |
| `/api/strategies`(+`/<name>`) | EXTEND | per-row `performance.profit_factor`, `trading.sl_hits/tgt_hits`, `health_score`, `composite_score`, `trend`; top-level **`rankings_extra`** {roi, profit_factor, trade_count}. **`rankings` (4 keys) UNCHANGED** (equality-pinned `:44`). 7-group shape unchanged. |
| `/api/signals` | EXTEND | rows += `system_score` (screener join, L8), `trade_type,direction,trade_result,trade_duration` (trade join), `required_score`. |
| `/api/orders` | EXTEND | rows += `system_score, trade_type, direction, planned {entry,sl,tgt}, slippage_allowed, slippage_actual, exchange_timestamp`. |
| `/api/positions` | EXTEND | += `position_age, capital_pct, position_weight, expected_rr`; stable `unavailable:{ltp,mtm,unrealized,current_rr,reason:"G4"}`. |
| `/api/holdings` | EXTEND | += `system_snapshot` (full gtt_state), `broker:{available:false,reason,source:"P1 pending"}`. |
| `/api/risk,/capital,/exposure` | PRESERVE (+`/exposure` additive `by_direction`) | Capital&Risk composes; `by_direction` = long/short/net split. |
| `/api/pnl` | PRESERVE (+`profit_factor`) | today widget; analytics → new endpoint. |
| `/api/slippage` | EXTEND | `?period` (default today) + `per_scanner, per_symbol, price_buckets, trend`. Existing fields unchanged. |
| `/api/execution` | EXTEND | `?period` + `exchange_timing, throughput, per_scanner, per_symbol, warnings`; `unavailable:{stage_timings:"G-3"}`. |
| `/api/services` | EXTEND | units += `started_at, uptime, response_ms`; += `db_health, broker_health, external_deps, readiness`. (`/api/dashboard.service_health` untouched → stays 6.) |
| `/api/vm` | PRESERVE | CPU/RAM sentinel honest. |
| `/api/logs` | EXTEND | += severity counts, service/module/event-type filters, retention. File-tail contract unchanged. |
| `/api/audit` | EXTEND | += `categories, old_new (config diff), retention`; `unavailable:{user,control,auth:"G-5"}`. |
| `/api/config` | EXTEND | += scoring-weights, broker-costs, position-sizing, trading-hours categories, param-diff, history, search; `unavailable:{changed_by:"G-5"}`. |
| `/api/reports`(+`/download`) | **PRESERVE UNTOUCHED (L6)** | no change in Phase B. |
| **NEW** `/api/scanner-attribution?period` | NEW | `{scanners:[{scanner,strategy,signals,accepted,rejected,orders,trades,win_pct,profit_factor,net,avg_duration,quality_score,funnel,top_reject}], rankings, kpi}` |
| **NEW** `/api/trades?filters` | NEW | per-trade story list (grouped price/slippage/RR/exit) |
| **NEW** `/api/trade-story/<trade_id>` | NEW | single-trade full assembly + lifecycle (shared: Trade Explorer + Trade Logs + row-detail timelines) |
| **NEW** `/api/analytics/pnl?period&from&to` | NEW | multi-period P&L: `{kpi, table, strategy_rank, scanner_rank, symbol_rank, equity_curve, drawdown, heatmaps}` |
| **NEW** `/api/activity?since` | NEW | merged feed `{events:[{ts,category,severity,summary,ref}], pulse:{signals_pm,orders_pm,trades_pm}}` |
| **NEW** `/api/strategy-ranking?period`, `/api/strategy-health?period` | NEW (thin) | multi-period wrappers over strategy readers (Today served by existing `/api/strategies`) |

**Headline: 5 PRESERVE-as-is · 12 EXTEND-additive · 6–7 NEW endpoints. Zero existing field renamed/removed; all count-pinned lengths preserved.**

---

## SECTION F — READER / SERVICE IMPACT MATRIX (all read-only `mode=ro`, NO new schema)

| Reader/Service | Verdict | Detail (tables — all existing) |
|---|---|---|
| `db_reader` core (~55 fns) | reuse + EXTEND | extend `list_signals` (+score/outcome joins), `list_orders` (+planned/exchange_ts/score), `open_positions_list` (+age/weights), `exposure_breakdown` (+by_direction); NEW helpers `screener_scores(signal_ids)` (`screener_results`), `strategy_sltgt_hits` (`trades.exit_reason`), date-range variants (`*_range(from,to)`) of today-scoped fns. |
| `strategy_tower` | EXTEND | + `profit_factor, health_score, composite_score, trend`, `rankings_extra`. 7-group + 4-ranking shape preserved. |
| `strategy_score` | reuse | scorecard/silence powers Strategy Health. |
| `pipeline_state` | reuse | Dashboard 13-stage + Live/Scanner funnels. |
| `capacity` | reuse | Capital&Risk Limits zone (8 rows + 6 groups). |
| `summary_bar` | EXTEND | + profit_factor/win/loss counters (Dashboard KPI). |
| `freshness` | reuse | global market-clock/poll/staleness. |
| `config_view` | EXTEND | + scoring/broker-costs categories, param-diff, history, search. |
| `config_reader` | reuse | strategies/scan_webhook_map/cron/system-config. |
| `log_reader` | reuse | ref_id filter → Trade Logs; tail → System Logs. |
| `host_reader` | EXTEND | + `unit_detail` (systemctl show: started/uptime), db size/backup (fs), token status (token file), external-dep checks. |
| `metrics_client` | reuse | wire the existing-but-unused `get_trader_metrics` (`:8080/metrics`) → Live Activity/System Health pulse. |
| **NEW** `scanner_attribution` | NEW | scanner→trade P&L join (`signals.scanner`←`trades.signal_id`; +`screener_results`,`trade_slippage_log`). |
| **NEW** `trade_story` | NEW | per-trade assembly (`trades`+`orders`+`signals`+`screener_results`+`trade_slippage_log`+`trade_excursions`+`reconciliation_log`). Feeds Trade Explorer + Trade Logs + all lifecycle timelines. |
| **NEW** `pnl_analytics` | NEW | multi-period P&L, rankings, equity, drawdown, heatmaps (`trades`,`fm_ledger`). |
| **NEW** `activity_feed` | NEW | merged signals/orders/trades/system_events/telegram_alerts stream + per-min pulse. |
| **NEW** `broker_reconcile_reader` | NEW (graceful) | reads `eod_broker_reconciliation` **if present** (empty→"pending", pattern of `control_tower_open_findings` try/except `db_reader.py:930`). Holdings broker side lights up only when P1 ships. |

**Every new reader maps to existing tables (Phase A §3.1-A). NO schema change proposed. All stay pure (isolation I7 AST gate auto-covers new backend files, `test_isolation.py:32`).**

---

## SECTION G — FRONTEND IMPLEMENTATION SEQUENCE

1. **NavShell** (5-group) + AlgoCore label-rebrand + font tokens (Inter/13px) — touches every template's nav block; land first.
2. **Shared components**: DataTable (sort/rows-per-page/grouped-headers/common-order), FilterBar, KpiRow, PeriodSelector, ScoreChip, StatusChip, **EventsAlertsPanel**, **TwoStatePanel**, **ExportButton (flag OFF)**, LifecycleTimeline, LeaderboardMedals, FunnelPipelineCard.
3. **Reuse screens** (compose existing endpoints + additive fields): Dashboard, Strategies, Capital&Risk (consolidation), Configuration, Slippage, System Logs, Audit.
4. **NEW screens**: Strategy Ranking, Strategy Health, Scanner Attribution, Trade Explorer, P&L Analytics, Live Activity, Trade Logs.
5. **Gap-flagged / two-state**: Positions, Holdings, Execution (timing-gap), System Health (enrich), Controls (read-only summary A + G3 placeholder B).
Each step green against the CDN grep gate (`test_isolation.py:76`) + page-route tests.

## SECTION H — BACKEND IMPLEMENTATION SEQUENCE

1. **Additive endpoint fields** unblocking reuse screens: `summary_bar`/`/api/dashboard` counters + PF; `strategy_tower` `profit_factor`/`rankings_extra`/scores; `exposure` `by_direction`; `config_view` categories; `list_signals`/`list_orders` score+join fields. (Each additive → tests stay green.)
2. **Shared new readers**: `screener_scores` (score join), `trade_story` (assembly), date-range helpers.
3. **NEW screen services**: `scanner_attribution`, `pnl_analytics`, `activity_feed`, `broker_reconcile_reader`; `host_reader` enrichment.
4. **Multi-period layer**: `?period` params + `*_range` readers across Slippage/Execution/P&L/Ranking/Health.
Every step: additive-only, `mode=ro`, no schema; run full suite + new contract tests before proceeding.

## SECTION I — TEST IMPACT MATRIX

- **Existing 238 cases: NONE broken (additive-only by construction).** The equality-pinned assertions (`rankings`==4, `service_health`==6, `stages`==13, capacity `rows`==8/`groups`==6) are honored because new data lands in NEW fields (`rankings_extra`, new `/api/services` fields, new endpoints) — never mutating those keys/lengths. `d["count"]==5` (strategies fixture) unaffected.
- **New tests required:**
  - **Contract test per new endpoint** (`/api/scanner-attribution`, `/api/trades`, `/api/trade-story/<id>`, `/api/analytics/pnl`, `/api/activity`, `/api/strategy-ranking`, `/api/strategy-health`) — subset-shape + auth-401 + 200-authed.
  - **Additive-field assertions** on extended endpoints (new keys present; old keys untouched).
  - **Isolation I7** auto-applies to every NEW backend file (AST no-prod-import walk, `test_isolation.py:32`) + **read-only write-raises** for new readers + **CDN grep gate** for new templates (`:76`). New files must pass as-is.
  - **Two-state tests**: UNAVAILABLE renders reason, never a number (Positions/Holdings/Orders-raw/Execution-timings/Audit-user).
  - **Export-flag test**: `table_export_enabled=false` → button disabled / endpoint 403 (mirror `/api/reports/download` `analytics.py:168`).
- **Fixtures extended**: golden-day fixture gains multi-date rows (Week/Month) + scanner-attributed signals→trades + screener_results scores. Keep v41+v42 dual-run.

## SECTION J — ROLLOUT PLAN (Phase-C sub-phasing + branch strategy)

Each sub-phase = own branch off the **deployed GUI tag** (`main`@`7b1c92d`/tip`33e9223`), merged only after full suite + new tests green, independently testable, so the **PAUSED soak (S1-S8) can resume on the known-good tag at any moment**. No schema, no trading-code → cannot affect the trading system; worst-case rollback = revert GUI unit to deployed tag.

| Sub-phase | Scope | Rough size |
|---|---|---|
| **G5a** — Shared foundation | NavShell (5-group) + rebrand + font tokens + 9 new shared components + global table upgrade + ExportButton (flag OFF) + EventsAlertsPanel + TwoStatePanel. Frontend-only; backend untouched. | L (foundational, touches all templates) |
| **G5b** — Reuse screens | Dashboard, Strategies, Capital&Risk (consolidation, L5), Configuration, Slippage, System Logs, Audit + their additive endpoint fields (PF, score joins, by_direction, config categories). | M |
| **G5c** — NEW analytics screens | Strategy Ranking, Strategy Health, Scanner Attribution, Trade Explorer, P&L Analytics + new readers/services (`scanner_attribution`, `trade_story`, `pnl_analytics`) + new endpoints + multi-period layer. | L (most new backend) |
| **G5d** — Gap/two-state + Live/Logs + Controls-A | Positions, Holdings (two-state), Execution (timing gap), System Health (enrich), Live Activity, Trade Logs, Controls read-only summary (A) + G3 placeholder (B). | M |
| **G5e** — Deploy + resume soak | Merge to `main`, VM `gui-dashboard` restart, Tailscale unchanged, resume soak S1-S8; contract+isolation gate before merge. | S |

Branch names: `gui-g5a-*` … `gui-g5e-*` off the deployed tag; each rebased-clean (diff = `ops_dashboard/**` + docs) as G2c did.

## SECTION K — PHASE-C IMPLEMENTATION BACKLOG (ordered; one line per buildable unit)

**G5a (foundation):**
1. NavShell 5-group + landing=Dashboard + mobile-collapse (extend `base.html`).
2. AlgoCore label-rebrand (login + shell) — labels only, internal names untouched (L2).
3. Font tokens Inter/Segoe/IBM-Plex ≥13px + contrast.
4. DataTable (sort ▲▼ / rows-per-page / grouped headers / common-column-order).
5. FilterBar · KpiRow · PeriodSelector · ScoreChip · StatusChip · FunnelPipelineCard.
6. EventsAlertsPanel (composes `/api/alerts`+events) · TwoStatePanel · LifecycleTimeline · LeaderboardMedals+Trend.
7. ExportButton + **new flag `table_export_enabled` (DEFAULT OFF)** — ⚠ **Rama gate: Q3/XLSX policy (L7)**.

**G5b (reuse screens + additive backend):**
8. `summary_bar`/`/api/dashboard` counters + Profit Factor; Dashboard rebuild (KPI row, clickable, capacity color, compact strategy summary; keep 13-stage pipeline).
9. Strategies: table+detail tabs+hierarchy; `strategy_tower` += profit_factor, sl/tgt hits; Logs tab (log_reader ref_id).
10. Capital & Risk consolidated page (compose /risk+/capital+/exposure+/capacity); `/api/exposure` += `by_direction` (L5).
11. Configuration: `config_view` += scoring/broker-costs/position-sizing/trading-hours categories, param-diff, history, search.
12. Slippage: `/api/slippage` += per_scanner/per_symbol/price_buckets/trend/?period.
13. System Logs: `/api/logs` += severity counts/filters/retention + system_events merge.
14. Audit: `/api/audit` += categories/old-new/retention; user/auth = UNAVAILABLE (G-5).

**G5c (NEW analytics + new backend):**
15. `screener_scores` reader + wire `system_score` into Signals/Orders (L8, single score).
16. `trade_story` service + `/api/trade-story/<id>` + `/api/trades`; Trade Explorer screen.
17. `scanner_attribution` service + `/api/scanner-attribution`; Scanner Attribution screen.
18. `pnl_analytics` service + `/api/analytics/pnl`; P&L Analytics screen (folds Statistics).
19. Strategy Ranking (rankings_extra + composite + trend + medals) + Strategy Health (health_score + timeline + `/api/strategy-{ranking,health}?period`).
20. Multi-period `*_range` readers + `?period` params.

**G5d (two-state + live/logs + controls-A):**
21. Signals/Orders lifecycle timelines + detail endpoints; Orders raw-broker = UNAVAILABLE (G-4).
22. Positions two-state (system full; MTM/LTP/current-RR = G-1 UNAVAILABLE).
23. Holdings two-state (system gtt_state full; broker/delta = G-2 UNAVAILABLE via `broker_reconcile_reader`).
24. Execution: exchange_timing + throughput + rankings; per-stage timings = G-3 UNAVAILABLE.
25. System Health: merge services+vm page; host_reader enrich (uptime/db/token/deps/readiness); CPU/RAM = G-6 honest.
26. Live Activity: `activity_feed` + `/api/activity` + market pulse + merged Events/Alerts.
27. Trade Logs: reuse log_reader ref_id + trade_story + replay.
28. Controls: read-only summary (A) composing config/risk/preflight/audit; **G3 placeholder (B) — disabled, no writes (L4)**.

**G5e:** 29. contract+isolation gate → merge `main` → VM restart → resume soak S1-S8.

**⚠ Rama-decision gates inside the backlog (do NOT auto-build):**
- **Q3 / XLSX policy (L7)** — item 7: ship `table_export_enabled` DEFAULT OFF; enabling both flags is Rama's call (copy-protection/Q3).
- **Schema-pause PROPOSALS P-1..P-4 (NOT scheduled as builds — need Rama's explicit trigger):** P-1 persist per-stage exec timings (unblocks Execution G-3) · P-2 persist per-change author + control-action/auth audit rows (unblocks Audit G-5) · P-3 ship P1 `eod_broker_reconciliation` (unblocks Holdings EOD-delta G-2) · P-4 GUI-readable unrealized-MTM snapshot table (unblocks Positions live-MTM G-1 without a GUI broker session). **These remain honest UNAVAILABLE states until Rama triggers.**

---

## REPORT-BACK TO RAMA (evidence only)

1. **Design doc:** `ops_dashboard/docs/G5_REDESIGN_PHASE_B.md` — sections **A–K all present** (A: 22 screens; B: components; C: inventory; D: nav; E: API matrix; F: reader/service matrix; G/H: sequences; I: tests; J: rollout; K: backlog).
2. **Component inventory:** **13 shared components — 9 NEW / 4 existing-extended** (DataTable/FilterBar/KpiRow/PeriodSelector/EventsAlertsPanel/TwoStatePanel/ExportButton/ScoreChip/LifecycleTimeline/LeaderboardMedals new; NavShell/FunnelPipeline/StatusChip extended).
3. **API matrix:** **5 PRESERVE-as-is · 12 EXTEND-additive · 6–7 NEW** endpoints. Zero existing field renamed/removed; all count-pinned lengths (6 units / 13 stages / 8 rows / 6 groups / 4 rankings) preserved — new data in NEW fields (`rankings_extra`, etc.).
4. **Phase-C sub-phases:** **G5a** shared foundation+nav+rebrand+global-table (L) · **G5b** 7 reuse screens+additive backend (M) · **G5c** 5 NEW analytics screens+new services (L) · **G5d** two-state Positions/Holdings + Live/Logs + Controls-A (M) · **G5e** deploy+resume soak (S).
5. **Rama-decision gates:** Q3/XLSX = ship `table_export_enabled` DEFAULT OFF (enable = your call); schema-pause **P-1..P-4 remain PROPOSALS** (honest UNAVAILABLE until you trigger) — none scheduled as builds.
6. **Preservation confirmed:** **zero existing endpoints renamed/deleted** (Reports UNTOUCHED per L6); **238 tests design-preserved (additive-only)** — new data in new fields, equality-pinned assertions untouched, new files covered by I7/CDN gates.
7. **Memory:** `gui_g5_phaseB_04jul` written + MEMORY.md pointer; SYSTEM_MAP GUI section + PATHS.md updated with the doc path + Phase-B state.
8. **Deviations / NOT RUN:** Design only — no code/schema/route/config. Controls (B) is a labeled placeholder, no write path (L4). Git **HELD** (no branch/commit/push); ready for `gui-g5-phaseB-04jul`. `mode` stays a displayed attribute everywhere (parity; no mode branch introduced).

*Phase B is design only. Web Claude reviews, then issues Phase C sub-phase instruction files one at a time (G5a first). No coding until Rama approves Phase B.*
