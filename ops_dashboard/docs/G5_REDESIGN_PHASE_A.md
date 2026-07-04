# G5 — OPS DASHBOARD REDESIGN · PHASE A: INVESTIGATION + MAPPING

**Date (IST):** 04-Jul-2026 (Sat, market CLOSED) · **Mode:** READ-ONLY investigation — zero code edits.
**Model:** Opus 4.8 (1M), max effort. **Author:** VS Code Claude for Rama.
**Deliverable:** this document (the only repo write besides PATHS.md + SYSTEM_MAP.md GUI-section; memory files updated separately).

**Sources read (evidence base):**
- **Specs (22 + plan), all read in full** from `D:\Projects\trading-system\gui\*.txt` (local PC copy — NOT the VM; no VM copy was needed). Cited as `spec NN §SECTION`.
- **Live code**, all read in full: `ops_dashboard/backend/app.py`, `auth.py`, `api/{dashboard,pipeline,capacity,strategies,trading,risk_capital,system,analytics}.py`, `readers/{db_reader,config_reader,log_reader,host_reader,metrics_client}.py`, `services/{strategy_tower,strategy_score,pipeline_state,capacity,summary_bar,freshness,config_view}.py`, `frontend/templates/base.html`, `tests/{test_isolation,test_api_contract}.py` + `def test_` census across all 12 test files. Schema spot-checks in `core/schema.sql`. Cited as `file.py:line`.

**Discipline / git posture:** Investigation only. Per Rama's standing no-push posture and the in-flight uncommitted changes already in the working tree (PATHS.md, SYSTEM_MAP.md, `docs/audit/*` untracked), I **HELD**: no branch, no commit, no push. Files were written to the working tree. Recommendation: when ready, commit the docs-only set (this report + PATHS.md + SYSTEM_MAP.md GUI edits) onto `gui-g5-phaseA-04jul` and leave unpushed for Rama. No schema change is proposed as done anywhere in this report; every new table/column appears only as a PROPOSAL.

---

## SECTION 0 — EXECUTIVE SUMMARY (headline counts + top risks)

**Menu:** current live GUI = **4 nav groups / 24 screens** (Trade · Risk & Capital · System · Analytics — `base.html:42-67`). Target = **5 groups / 21 menu screens** (Dashboard · Trading · Analytics · Operations · Investigation — `spec plan §FINAL MENU STRUCTURE`) **+ Capital & Risk** which has a full spec (07) but is **missing from the target menu tree** (placement conflict, §4.2).

**Mapping headline (of 22 target screens — 21 menu + Capital&Risk):**
- **~9 reuse-as-is** = menu move / UX redesign only; existing endpoint suffices with *additive* fields (cheap derivations like Profit Factor/ROI): Dashboard, Strategies, Slippage Analytics, Configuration, System Logs, Audit, Capital & Risk (consolidation), Strategy Ranking, Strategy Health.
- **~4 need new frontend** over existing data/services (thin new derivation): Live Activity, Trade Logs (+ Strategy Ranking/Health straddle this and the prior bucket).
- **~9 need new backend readers/joins or hit a hard data wall:** Signals (score + outcome joins), Orders (planned-vs-actual + exchange-ts + score joins), Scanner Attribution (scanner→trade P&L join), Trade Explorer (per-trade assembly), P&L Analytics (multi-period + scanner + drawdown + heatmaps), Execution Analytics (per-stage timings — partial gap), **Positions (live MTM — blocked)**, **Holdings (broker side — blocked)**, Controls (**architecture wall**).

**Crucially: no new database schema is required for any screen except the two hard-blocked ones, and even those need no *new* schema — they need a data *source* the read-only GUI cannot reach (a broker session).** The redesign is overwhelmingly (a) frontend/nav/UX + (b) additive, read-only SQL joins/aggregations over the ~23 tables already read.

**Top 5 risks (one-liners; detail in §4.1):**
1. **HIGH — Controls (spec 16) demands write-actions** (strategy/scanner toggles, limit edits, pause-trading) but the dashboard is read-only by architecture (`test_isolation.py`) and **no control-plane exists** (G3.0: kill-switch in-memory, config needs restart). This is a G3 design + isolation-exception question, **not** a Phase B/C GUI build.
2. **HIGH — Live broker data (LTP / MTM / broker holdings) is unreachable read-only (G4).** Positions and Holdings depend on it centrally; only honest partials are buildable.
3. **MED-HIGH — 238-case contract-test gate pins exact JSON shapes & counts** (6 units, 13 stages, 8 capacity rows, 6 groups, 4 rankings). The redesign MUST be additive; a "cleanup" rename/removal breaks the gate.
4. **MED — Multi-period analytics (week/month) is a systematic new build** — every current query is today-scoped.
5. **MED — Per-stage execution timings + user/change attribution are not persisted** — Execution Analytics and Audit will be honest partials, not full specs.

**Preserve-vs-rename:** **NONE forced.** Every existing route/endpoint/module can be preserved and extended additively; new screens add new routes. (One menu-placement ambiguity — Capital & Risk — and several current standalone screens become non-menu but keep their endpoints; §4.2.)

**Q3 / XLSX:** ~20 of the 22 screens request "Download XLSX — export filtered results" — a platform-wide ask that collides with `reports_download_enabled=FALSE` (Q3 copy-protection, `analytics.py:158-160`). Rama decides (§3.4).

---

## SECTION 1 — CURRENT-STATE INVENTORY

### 1.1 Routes / endpoints (the "preserve where practical" baseline)

Flask app factory `app.py:73`; 8 API blueprints registered `app.py:102-110`; global login guard `app.py:112-120` (API→401, page→302/login). All `/api/*` are `@login_required` GET.

| # | Route | Method | Handler | Service/reader called |
|---|---|---|---|---|
| — | `/login` | GET/POST | `auth.login_get/post` (`auth.py:149,156`) | `authenticate` (PBKDF2+TOTP+lockout) |
| — | `/logout` | GET/POST | `auth.logout` (`auth.py:173`) | session.clear |
| — | `/` | GET | `dashboard_page` (`app.py:122`) | renders `dashboard.html` |
| — | `/<page>` | GET | `module_page` (`app.py:140`) | 21 pages in `_PAGES` (`app.py:127-138`) |
| 1 | `/api/dashboard` | GET | `dashboard.py:17` | `summary_bar.build_summary`, `host_reader.all_units`, `freshness.freshness_state`, `db_reader.recent_events`, `metrics_client.get_trader_health` |
| 2 | `/api/pipeline` | GET | `pipeline.py:12` | `pipeline_state.build_pipeline` (13 stages + halt) |
| 3 | `/api/capacity` | GET | `capacity.py:12` | `capacity.build_capacity` (8 rows + 6 groups) |
| 4 | `/api/strategies` | GET | `strategies.py:17` | `strategy_tower.build_strategy_tower` |
| 5 | `/api/strategies/<name>` | GET | `strategies.py:24` | `strategy_tower.strategy_detail` (404 if unknown) |
| 6 | `/api/signals` | GET `?date&scanner&strategy&family` | `trading.py:30` | `db_reader.list_signals`, `webhook_funnel`, `signals_duplicate_count`, `signals_stored_count` |
| 7 | `/api/orders` | GET `?date&leg&status&strategy&symbol` | `trading.py:62` | `db_reader.list_orders` |
| 8 | `/api/positions` | GET | `trading.py:79` | `db_reader.open_positions_list`, `get_session_info`; `config_reader.get_system_config`. **unrealized = `—`, note "G4"** (`trading.py:99`) |
| 9 | `/api/holdings` | GET | `trading.py:104` | `db_reader.holdings_list` (**gtt_state mirror only**; banner "Broker is authority") |
| 10 | `/api/risk` | GET | `risk_capital.py:25` | `db_reader.realized_loss_today` (D2), `consecutive_loss_streak`, `get_kill_switch`, `open_positions_count`, `open_risk_amount_sum`, `opening_capital` |
| 11 | `/api/capital` | GET | `risk_capital.py:62` | `db_reader.capital_usage`, `ledger_entries`, `opening_capital` |
| 12 | `/api/exposure` | GET | `risk_capital.py:90` | `db_reader.exposure_breakdown` (per-strategy/per-symbol) |
| 13 | `/api/pnl` | GET | `risk_capital.py:107` | `db_reader.pnl_summary_today`, `equity_curve_points`, `closed_trades_today` |
| 14 | `/api/services` | GET | `system.py:28` | `config_reader.get_cron_jobs`, `db_reader.latest_heartbeats`, `host_reader.all_units`, `metrics_client.get_trader_health`, `db_reader.control_tower_open_findings` |
| 15 | `/api/vm` | GET | `system.py:55` | `host_reader.vm_stats`, `db_reader.system_metrics_disk_history` (CPU/RAM = not-collected sentinel) |
| 16 | `/api/logs` | GET `?file&n&level&q&id` | `system.py:70` | `log_reader.list_log_files / resolve_log_path / tail_lines / parse_structured` |
| 17 | `/api/audit` | GET `?date&table&severity&page&size` | `system.py:102` | `db_reader.audit_feed` (6 sources) |
| 18 | `/api/alerts` | GET | `system.py:125` | `db_reader.telegram_alerts_today`, `log_reader` (failed_alerts.log), `host_reader.list_sentinels` |
| 19 | `/api/slippage` | GET | `analytics.py:31` | `db_reader.slippage_rows_today` + tolerance/breach model |
| 20 | `/api/execution` | GET | `analytics.py:79` | `db_reader.latency_rows_today`, `execution_log_today`, `audit_feed(reconciliation_log)` |
| 21 | `/api/statistics` | GET | `analytics.py:106` | `db_reader.statistics_bundle` (win/expectancy + MFE/MAE + innings + LONG/SHORT) |
| 22 | `/api/reports` | GET | `analytics.py:140` | filesystem listing of `reports_dir/*.xlsx` |
| 23 | `/api/reports/download` | GET | `analytics.py:164` | **403 while `reports_download_enabled=false`** (Q3) |
| 24 | `/api/config` | GET | `analytics.py:176` | `config_view.build_config_view` (8 groups + drift banner) |

Also present but **UNWIRED**: `metrics_client.get_trader_metrics` (GET `:8080/metrics`, `metrics_client.py:41`) — a ready reader with no endpoint, a natural feed for Live Activity / System Health.

### 1.2 Templates / current menu group (the G2b sidebar — `base.html:40-68`)

24 templates in `frontend/templates/`. Current nav = **4 groups** (icon sidebar, inline SVG, self-hosted Alpine+htmx, no CDN):

| Current group | Screens (template) |
|---|---|
| **Trade** | Dashboard (`dashboard.html`, `/`), Strategies, Signals, Orders, Positions, Holdings |
| **Risk & Capital** | Risk, Capital, Exposure, P&L (`pnl.html`), Capacity |
| **System** | Services, VM, Logs, Audit, Alerts |
| **Analytics** | Slippage, Execution, Statistics, Reports, Config, **Controls (G3, dimmed — `nv-dim`, stub)** |

Frontend pattern (`base.html:126-206`): root `opsDashboard()` polls `/api/dashboard`, drives the pinned summary bar + freshness cadence (5s market / 60s off, `freshness.poll_interval_ms`), then broadcasts `ops-refresh`; each page's `pageBase(endpoint)` fetches ITS own `/api/*` (Alpine-over-JSON). Only POST surfaces = `/login`, `/logout` (`base.html:122`).

### 1.3 Reader queries + service functions (data layer to REUSE, not rebuild)

**`db_reader.py`** — the ONLY SQLite touchpoint (isolation I2), read-only URI `mode=ro` + analytics ATTACH (`db_reader.py:53-70`). ~55 query functions reading **~23 tables**:

- `schema_meta, session, kill_switch_state` (header) · `webhook_audit, signals` (funnel/lists) · `orders, trades` (funnel/lists/perf) · `fm_ledger, capital_snapshot` (capital) · `config_snapshots` (config) · `system_events` (events) · `gtt_state` (holdings) · `innings` (position/inning) · `cron_heartbeat` (services) · `control_tower_findings` (findings) · `reconciliation_log, eod_verification, preflight_runs` (audit) · `telegram_alerts` (alerts) · `trade_slippage_log` (slippage) · `order_execution_log` (execution) · `trade_excursions` (MFE/MAE) · `analytics.system_metrics` (disk history).

**Services (view-model builders):** `strategy_tower` (7 groups + 4 rankings + scanner_level rows — the richest, `strategy_tower.py:65`), `strategy_score` (deterministic scorecard + silence tiers, pure/threshold-driven), `pipeline_state` (13 stages + halt, pure `derive_color`), `capacity` (8 primary rows + 6 grouped, all 30 inventory keys), `summary_bar` (pinned bar), `freshness` (IST market clock, poll cadence, staleness — **global dependency**), `config_view` (8-group config + drift).
**Other readers:** `config_reader` (system_config snapshot-first/YAML-fallback, strategies, scan_webhook_map, cron_registry), `log_reader` (hardened tail + `parse_structured` with **ref_id filter on signal_id/trade_id/order_id** — powers Trade Logs), `host_reader` (systemctl is-active, vm_stats, sentinels), `metrics_client` (trader `/health` + unwired `/metrics`).

### 1.4 Test inventory (the "must not break" contract)

**116 `test_` functions across 12 files** → **~238 collected cases** (pytest parametrization; PATHS.md cites 238/240), v41+v42 fixtures:
`test_trading_modules(13)`, `test_g2b2_screens(23)`, `test_capacity(14)`, `test_strategy_tower(12)`, `test_api_contract(13)`, `test_strategy_score(9)`, `test_db_reader(9)`, `test_auth(8)`, `test_freshness(5)`, `test_pipeline_state(4)`, `test_isolation(4)`, `test_summary_bar(2)`.

**Contracts that constrain the redesign** (`test_api_contract.py`, subset-checks `<= set(d)` ⇒ *adding* keys is safe; renaming/removing/count-change breaks):
- Dashboard: `service_health` length **== 6** (`:11`); summary has mode/trader_alive/kill_switch/counters/phase.
- Pipeline: `stages` length **== 13** (`:20`); halt has state/halted.
- Capacity: `rows` **== 8** + `groups` **== 6** (`:29,31`).
- Strategies: `count == 5` (test fixture) (`:39`); every row has the **7 groups** basic/signals/processing/trading/performance/risk/health (`:41`); `rankings` == the 4 keys (`:44`); `scanner_level` is a list.
- Signals/orders/positions/holdings: fixed key sets (`:57-87`).
- Auth: all `/api/*` →401 anon (`:90`); pages →302/login (`:97`); render 200 authed (`:106`).
**Isolation (`test_isolation.py`) — mechanism-enforced, not convention:** (a) no backend module imports a production package (AST gate over `core/orders/capital/strategies/broker/signals/data/alerts/ops/scripts/main`, `:32`); (b) a write through `db_reader` connection raises read-only (`:52`); (c) GUI venv has no `kiteconnect` (`:65`); (d) **no CDN / external URL in frontend** except loopback (`:76`).

---

## SECTION 2 — SCREEN MAPPING MATRIX

Verdict legend: **PRESERVE** = keep route/template/endpoint, extend additively. **NEW** = new route+template (endpoint may be new or reuse existing). *Exists / Partial / NEW* = current-screen status.

| Target screen (menu) | Spec | Current? | Current route + template | Current data source → reuse | What CHANGES | New data? (source) | Verdict |
|---|---|---|---|---|---|---|---|
| **Dashboard** | 02 Dashboard | Exists | `/` `dashboard.html` | `/api/dashboard,/pipeline,/capacity,/strategies` | KPI "Today's Summary" row; clickable KPIs; pipeline last-event-time (already in `pipeline_state`); capacity Usage% + 0-60/60-80/80-100 color (pct already computed `capacity.py:56`); move Strategy-Tower cards OUT → compact summary; keep Recent Events; merge-in Alerts banner | **Profit Factor** (derive Σwin/Σ\|loss\|); else all reuse | PRESERVE |
| **Strategies** | 02 Strategies | Exists | `/strategies` `strategies.html` | `/api/strategies` (`strategy_tower`) | Dense sortable table + filters; detail tabs Overview/Signals/Orders/Trades/Perf/Capital/Risk/**Logs**; strategy-family grouping (First Pullback → Long/Short); SL/TGT-hit counts; ROI% (exists `:127`); XLSX | SL/TGT-hit count per strategy (new small `trades.exit_reason` GROUP BY); family = derive from name; **Logs tab** = `log_reader` ref_id | PRESERVE (extend `strategy_tower`) |
| **Signals** | 03 Signals | Exists | `/signals` `trading.py:30` | `list_signals`, `webhook_funnel` | System/Signal Score cols; Trade Type/Direction; **Trade Result + Trade Duration** (join signal→trade); signal lifecycle timeline; Reject/Required score; age color; rows/page 200; XLSX | **System Score** = `screener_results.score` (join `signal_id`); signal→trade outcome via `trades.signal_id`; Required score = `min_pass_score` cfg; **"Signal Score" has no distinct column** (see §3.1 ambiguity); raw payload = `signals.webhook_payload` ✓ | PRESERVE (extend `list_signals`) |
| **Orders** | 04 Orders | Exists | `/orders` `trading.py:62` | `list_orders` (order+trade join) | Planned-vs-actual grouped headers (Entry/SL/TGT system vs filled); Allowed vs Actual slippage; execution timing; broker-response expandable; Order Type (product); scores; XLSX | Planned SL/TGT = `trades.sl_initial/tgt_initial` (extend join); slippage = `trade_slippage_log`/`order_execution_log`; **Exchange Accept Time** = `order_execution_log.exchange_timestamp` ✓ (partial); **broker raw response = GAP** (only `orders.rejection_reason`); "Order ID"=="Broker Order ID" (`orders.order_id` IS broker-assigned, `schema.sql:271`) | PRESERVE (extend `list_orders`) |
| **Positions** | 05 Positions | Exists | `/positions` `trading.py:79` | `open_positions_list` | KPI strip; **Unrealized P&L/%/MTM/Current LTP/Current RR/SL-TGT distance**; broker SL/TGT; position age; lifecycle; MFE/MAE; scores; XLSX | **Live LTP/MTM/current-RR = G4 BLOCKED** (no broker session read-only); open-position MFE/MAE not live (`trade_excursions` is post-EOD); broker SL/TGT partial via `gtt_state`; age/weights derivable | PRESERVE shell; flag LTP wall |
| **Holdings** | 21 Holdings | Exists | `/holdings` `trading.py:104` | `holdings_list` (**gtt_state mirror**) | Broker-first reconciliation: Broker Qty vs System Qty vs Delta; matched/mismatch; orphan detector; broker snapshot; current price/value | **Broker holdings (live) = BLOCKED** (no session); nearest future source `eod_broker_reconciliation` (P1) is **UNPUSHED/shadow, not in live schema** → EOD-granularity delta only, and only after P1 ships | PRESERVE shell; flag broker wall |
| **Strategy Ranking** | 18 | Partial | *(data in `/api/strategies.rankings`; no screen)* | `strategy_tower.rankings` (net_pnl/win_rate/expectancy/success_rate) | NEW leaderboard screen; ranking modes ROI/**Profit Factor**/Trade-Count; **composite 0-100 score**; **trend** (Improving/Stable/Declining); 🥇🥈🥉 | ROI exists; Profit Factor + composite score (reuse `daily_trade_review` T5 formula concept) + trend (trailing-session compare) = new derivations; multi-period = new | NEW screen; PRESERVE+extend `/api/strategies` |
| **Strategy Health** | 19 | Partial | *(data in `strategy_tower.health`+scorecard; no screen)* | `strategy_tower` health/scorecard/silence (`:243`, `strategy_score`) | NEW health screen; status counts; **Health Score 0-100** (currently GREEN/YELLOW/RED badge); health timeline; week aggregates; warnings section | Numeric 0-100 (new, from existing badge inputs); health timeline (new — status not time-seried); week counts = multi-day | NEW screen; reuse `strategy_tower` |
| **Scanner Attribution** | 20 | Partial | *(`scanner_level` rows + `webhook_by_scanner`; no per-scanner P&L)* | `strategy_tower.scanner_level`, `db_reader.webhook_by_scanner` | NEW screen; per-scanner signals/orders/**trades/win%/PF/net P&L**; funnel; avg trade duration; quality score; drilldown; 🥇🥈🥉 | **Scanner→trade P&L attribution** = NEW join (scanner on `signals`; `trades.signal_id`→`signals.scanner`, `schema.sql:105-107,241`) — data EXISTS, assembly new; avg duration = exit−entry; quality/PF = derived | NEW screen + NEW reader/service |
| **Trade Explorer** | 06 | **NEW** | *(none; nearest = `daily_trade_review.xlsx` offline)* | trades+orders+signals+`trade_slippage_log`+`trade_excursions` | NEW "one trade = one story" screen: unified per-trade row (entry/SL/TGT system-vs-broker, slippage, RR damage, ROI, exit) + lifecycle timeline | **NEW assembly reader** joining existing tables (no new schema); the GUI analog of `reports/daily_trade_review.py` Orders sheet | NEW screen + NEW reader/service |
| **P&L Analytics** | 08 | Partial | `/pnl` (today only) + `/statistics` | `pnl_summary_today`, `equity_curve_points`, `closed_trades_today`, `statistics_bundle` | Multi-period (Today/Week/Month/Custom); Profit Factor; strategy/**scanner**/symbol rankings; equity curve multi-day; drawdown; heatmaps (DoW×PnL, ToD×PnL); attribution | **Multi-day queries** (new date-range readers); **scanner P&L** (new join); drawdown/heatmaps (new derivations) — all over existing `trades`/`fm_ledger`, no schema; **current `/statistics` folds in here** | Mostly NEW backend; reuse today-slice |
| **Slippage Analytics** | 09 | Exists | `/slippage` `analytics.py:31` | `slippage_rows_today` + tolerance model | Scanner + symbol rankings; price-bucket analysis (0-100/100-200/…); trend-through-day; multi-day | Scanner ranking (join); buckets/trend (derive over `trade_slippage_log.price_band`); date-range = new | PRESERVE (extend) |
| **Execution Analytics** | 10 | Exists | `/execution` `analytics.py:79` | `latency_rows_today` (3 fields), `execution_log_today` | Finer timing (Validation/Risk/Capital/Order-Submit/**Exchange-Accept**/Fill); per-stage delays; strat/scanner/symbol delay rankings; throughput/min; warnings; trend | **Per-stage validation/risk/capital timings NOT persisted = GAP** (trades has only `signal_to_order_ms/order_to_fill_ms/total_latency_ms`); Exchange-Accept via `order_execution_log.exchange_timestamp` ✓ (partial) | PRESERVE (extend); flag timing gap |
| **Live Activity** | 17 | Partial | *(`recent_events` only; no wall)* | `recent_events` (`system_events`), `pipeline_state`, `strategy_tower` | NEW "Mission Control Wall": merged real-time feed (signals/orders/trades/system_events/alerts); market pulse /min; strategy-activity panel; winners/losers; MTM | Merged-feed assembly (new, from existing tables); **absorbs Recent Events + Alerts**; per-min pulse derivable; **MTM = LTP gap**; unwired `get_trader_metrics` usable | NEW screen + merged-feed reader |
| **System Health** | 11 | Exists | `/services` + `/vm` (2 screens) | `/api/services`, `/api/vm`, `all_units`, `vm_stats`, `latest_heartbeats`, `control_tower_open_findings` | Merge services+vm; overall status; **uptime/started-at/response-time**; DB health (size/backup); broker/token health; external deps (Chartink/Tailscale/Internet); trading readiness; auto-recovery; absorbs Events+Alerts | uptime/started-at (`systemctl show`, host enhance); DB size/last-backup (fs read); token status (token file read); **CPU/RAM still −1.0 sentinel GAP**; conn-count/query-time GAP; readiness = reuse `preflight_runs` | PRESERVE both endpoints; new combined page |
| **Configuration** | 15 | Exists | `/config` `analytics.py:176` | `config_view` (8 groups + drift) | KPI row; +categories (Scoring/Position-Sizing/Broker-Costs/Trading-Hours); scoring weights; slippage bands; broker costs; **param-level** compare; config history; global search; sub-menu | Scoring weights/broker costs = in `config_snapshots.config_json` (read if present); history = `config_snapshot_history` ✓; **"Changed By" = GAP** (no author captured) | PRESERVE (extend `config_view`) |
| **Controls** | 16 | Partial (STUB) | `/controls` `controls.html` (dimmed, no backend) | *(none)* | **Requested: strategy/scanner toggles, Telegram on/off, runtime limit edits, pause/allow-exits/full-stop, apply/revert, every action → Audit** | **BLOCKED — architecture wall.** Real controls need a write path (violates read-only I1/I2) + a control-plane that **does not exist** (G3.0: kill-switch in-memory sole authority, config needs restart). Read-only-safe subset: Active-Controls-Summary, Readiness (preflight), Simulation-mode visibility, Control-History (audit) | PRESERVE stub → **escalate to G3** |
| **Audit** | 12 | Exists | `/audit` `system.py:102` | `audit_feed` (6 sources) | Reframe as accountability; categories; **User** attribution; old/new values; control-action timeline; critical-changes pinned; retention counts | old/new = `config_snapshots` diff (`config_view` has group diff); **User / who-changed / auth login-logout / control-actions = GAP** (not persisted — single-user, no control writes, auth events not DB-logged) | PRESERVE (extend `audit_feed`) |
| **Trade Logs** | 13 | **NEW** | *(current `/logs` = file tail, not trade-indexed)* | `log_reader.parse_structured` (**ref_id filter** on signal/trade/order id), `system_events`, trades | NEW forensic trade console: trade-event table + timeline + component tracking + trade replay | Reuse `log_reader` ref_id + `system_events`; assembly new, **no new schema**; "Resolution Status" partial | NEW screen (reuse `log_reader`) |
| **System Logs** | 14 | Exists | `/logs` `system.py:70` | `log_reader` (tail/whitelist/parse) | KPI severity counts; service/module/severity filters; aggregation; recovery tracking; replay; trading-impact; retention | Reuse `log_reader` + `system_events`; counts/aggregation = derive; **Resolution Status/Time = GAP** | PRESERVE (extend) |
| **Capital & Risk** ⚠ | 07 | Exists (as 3 screens) | `/risk` + `/capital` + `/exposure` (+`/capacity`) | `/api/risk,/capital,/exposure`, `capacity.groups` | **Consolidate 4 current screens → 1** (Capital · Risk · Exposure · Limits zones); Long/Short/Net exposure; strategy capital allocation; risk events; capital-history timeline; top consumers | Long/Short exposure split (new small GROUP BY direction); capital-through-day timeline partial (only `fm_ledger` events + equity curve); rest = pure reuse; **NOT in target menu tree → needs a home (§4.2)** | PRESERVE all 3 endpoints; new combined page |

**Current standalone screens that become NON-menu (data merged; endpoints PRESERVED, not deleted):** `Statistics` → into P&L Analytics + Strategy screens · `Exposure`/`Capacity`/`Risk`/`Capital` → into Capital & Risk · `VM`/`Services` → into System Health · `Alerts` → merged (§3.3) · `Reports` → replaced by per-screen XLSX export (no target home; §3.4).

---

## SECTION 3 — GAP ANALYSIS

### 3.1 Screens needing NEW backend data (with exact table/column)

**A. Reachable read-only — new READERS/JOINS over existing tables (no schema change):**
1. **System Score** on every "Common Column Standard" table (Signals/Orders/Positions/Trade Explorer/Slippage/Execution — `spec 03 §COMMON COLUMN`) → `screener_results.score` INTEGER, join by `signal_id` (`schema.sql:619-620,629`). ✔ available, not currently read.
2. **Signal → trade outcome** (Trade Result, Trade Duration, exit) on Signals → `trades` via `trades.signal_id`/`signals.trade_id` mutual FK (`schema.sql:105-114`). ✔ join.
3. **Scanner → trade P&L attribution** (Scanner Attribution, P&L-by-scanner, Slippage-by-scanner, Execution-by-scanner) → `signals.scanner` (`schema.sql:45`) ← `trades.signal_id`. ✔ join; no current reader assembles it.
4. **Planned-vs-actual order prices** (Orders, Trade Explorer) → `trades.sl_initial/tgt_initial/entry_target_price` (system) vs `orders.avg_fill_price` (broker). ✔ extend `list_orders` join.
5. **Exchange Accept Time** (Orders, Execution) → `order_execution_log.exchange_timestamp` (`schema.sql:1007`). ✔ partial (filled orders only).
6. **Multi-period aggregation** (P&L/Slippage/Execution/Ranking/Health week-month) → existing `trades`/`fm_ledger`/`trade_slippage_log` are dated; needs date-range-parameterized readers (today-only today). ✔ no schema.
7. **Profit Factor / ROI / composite 0-100 / trend** (Dashboard, Ranking, Health, Scanner, P&L) → derive from existing `net_pnl/gross_pnl/margin_reserved`; trend = trailing-session compare. ✔ derivation.
8. **Long/Short/Net exposure split** (Capital & Risk) → `trades` GROUP BY `direction` × value_expr (mirror `exposure_breakdown`). ✔.
9. **SL/TGT-hit counts per strategy** (Strategies) → `trades.exit_reason` GROUP BY strategy. ✔.
10. **Signal raw payload** (Signals lifecycle/forensics) → `signals.webhook_payload` (`schema.sql:76`). ✔ available, not read.

**B. NOT reachable read-only — hard data gaps (honest):**
- **G-1 Live LTP / MTM / unrealized / current-RR** (Positions, Holdings, Live Activity, Capital&Risk MTM) — needs a broker quote session; the GUI is broker-free (isolation I4, `test_isolation.py:65`). `/api/positions` already renders `—` note "G4" (`trading.py:99`). **No DB source exists** (B-1's reconciler `_unrealized_mtm` lives in the *trading* process memory, not a GUI-readable table).
- **G-2 Broker holdings (real)** (Holdings broker-first) — needs broker `get_holdings`. Nearest future DB source `eod_broker_reconciliation` (P1, v42) is **UNPUSHED/shadow and absent from live `core/schema.sql`** — grep confirms no such table on `main`. Even shipped, it is EOD-granularity, not live.
- **G-3 Per-stage execution timings** (Execution Analytics validation/risk/capital delays) — only 3 latency fields persisted (`trades.signal_to_order_ms/order_to_fill_ms/total_latency_ms`); validation/risk/capital sub-stage times are not captured anywhere → would require **trading-system instrumentation (out of GUI scope)**.
- **G-4 Broker/exchange RAW response payload** (Orders broker-response, Trade/System Logs) — only `orders.rejection_reason` persisted; no raw broker JSON column.
- **G-5 User / change / auth attribution** (Audit "who changed what", Configuration "Changed By", control-action timeline) — the system captures no per-change author, no DB-logged login/logout, and no control actions (there are none). Config *drift* is available; *authorship* is not.
- **G-6 Service uptime / started-at / response-time / DB conn-count / CPU-RAM** (System Health) — `all_units` returns only `{unit,state}` (`host_reader.py:51`); CPU/RAM are −1.0 sentinels (psutil absent). Partial: uptime via `systemctl show`, DB size/backup via fs, token status via token file.

**C. Proposed (schema-pause items — NOT to be migrated in this investigation; Rama's call):** none are *required* for a first pass, but if Rama later wants full fidelity: (P-1) persist per-stage timings (G-3), (P-2) persist per-change author/audit-write for controls (G-5), (P-3) ship P1 `eod_broker_reconciliation` for the EOD Holdings delta (G-2), (P-4) a GUI-readable unrealized-MTM snapshot table if live MTM is wanted without a GUI broker session (G-1).

### 3.2 Pure frontend / menu-reorg (the low-risk bulk)

No backend change beyond additive derivations: **Dashboard** (KPI row, colors, compact strategy summary), **Configuration** (categories, search — reuse `config_view`), **System Logs** (filters/counts — reuse `log_reader`), **Slippage** (buckets/trend — reuse), **Capital & Risk** (consolidate existing 3-4 endpoints into one page), **Strategy Ranking / Strategy Health** (reuse `strategy_tower` + thin derivation), the **global table upgrades** (sort ▲▼ on all columns, rows-per-page, common column order, Inter/13px fonts, AlgoCore rebrand). This is the majority of the pixel work and it is low-risk.

### 3.3 Events / Alerts merge (which screens absorb; existing source)

Per `spec plan §REMOVED/MERGED` — **both merges are a FRONTEND redistribution of two existing endpoints' data (`/api/alerts` + `/api/audit`/`recent_events`); no new backend.**

| Merge | Absorbing screens | Existing data source |
|---|---|---|
| **Recent Events** | Live Activity, System Health | `db_reader.recent_events` (`system_events`), `audit_feed`, `reconciliation_log` |
| **Alerts** | Dashboard, Live Activity, System Health, Capital & Risk, Strategy Health, Execution Analytics | `telegram_alerts_today`, `failed_alerts.log` tail (`log_reader`), `list_sentinels` (`host_reader`), `control_tower_findings` — all already served by `/api/alerts` (`system.py:125`) |

### 3.4 XLSX-download vs Q3 conflict

**~20 of 22 screens** request "Download XLSX — export filtered results" (`spec 03,04,05,06,07,08,09,10,12,13,14,15,17,18,19,20,21 §EXPORT`; Strategies §EXPORT; System Health §EXPORT). Two distinct mechanisms are implied and BOTH hit the same policy wall:
- Existing **report-file download** `/api/reports/download` — hard-gated 403 by `reports_download_enabled=false` (`analytics.py:158-169`), pending **Q3** (copy-protection-bypass acceptance) + the 18:00-08:00 copy-gate posture.
- NEW **client-side export of the on-screen filtered table** (the specs' actual intent) — same copy-protection question, plus it re-exposes read-only data as a file.

**Do NOT resolve — Rama decides.** Recommendation framing for Rama: this is one policy toggle (`reports_download_enabled` + a new `table_export_enabled`), not 20 technical builds; the export helper is a shared component (§4.3) built once and flag-gated OFF until Q3 is answered.

### 3.5 Broker-first Holdings — honest reachability

`spec 21 §GOAL` wants Broker = truth, System = expected, **Delta** = difference, with live Broker Qty / current price / orphan detection. **Constraint (stated plainly):** the read-only GUI has **no broker session** (I4) and there is **no DB table holding live broker holdings**. Today `/api/holdings` reads only `gtt_state` (the *system/expected* side, `trading.py:104`). Therefore:
- The **System/expected** half + **orphan-vs-gtt** signals ARE buildable now (gtt_state, `trades`).
- The **Broker** half + **live delta** are NOT — pending either (a) P1 `eod_broker_reconciliation` shipping (EOD-granularity delta; currently unpushed/absent from live schema) or (b) a G4 decision to give the GUI a read-only broker quote/holdings path. Phase B should design Holdings as a **two-state shell** that renders the System side fully and shows the Broker side as "pending broker source (G4/P1)" rather than faking it.

---

## SECTION 4 — RISK & DEPENDENCY ANALYSIS

### 4.1 Migration risks

| # | Risk | Rating | Why / mitigation |
|---|---|---|---|
| R1 | **Controls write-actions vs read-only architecture** | **HIGH** | Toggles/limit-edits/pause (`spec 16`) need a write path → breaks `test_isolation.py:52` (read-only DB) + I1 (no prod imports) AND a control-plane that doesn't exist (G3.0). Mitigation: Phase B/C build only the **display-only** subset; route real control to a separate **G3** design (in-memory kill-switch semantics, restart-based config, an explicit isolation exception if ever approved). Do NOT smuggle writes into the read-only GUI. |
| R2 | **Live-broker data wall (LTP/MTM/broker holdings)** | **HIGH** | Positions/Holdings/Live-Activity MTM centrally depend on data the GUI cannot reach (G4). Mitigation: build honest two-state shells; render `—`/"pending G4" (precedent already in `trading.py:99`); never fabricate. |
| R3 | **238-case contract gate breakage** | **MED-HIGH** | Tests pin exact shapes + counts (6 units, 13 stages, 8 rows, 6 groups, 4 rankings, strategies count; `test_api_contract.py`). A redesign that renames/removes keys or drops the Dashboard pipeline to the spec's 12 stages breaks the gate. Mitigation: **additive-only** evolution (subset-checks allow new keys); keep 13 stages (spec's 12 is a visual subset); update contract tests deliberately when counts must grow. |
| R4 | **Multi-period + new-join readers touch the hot data path breadth** | **MED** | Every analytics screen wants week/month + new joins (scanner P&L, score, outcome). Broad but low-severity: all are read-only additive `db_reader` functions with the `mode=ro` guarantee; risk is scope/effort, not safety. Mitigation: build the date-range + scanner-attribution + score-join readers ONCE as shared primitives (§4.3). |
| R5 | **Honest partials read as "done"** | **MED** | Execution timings (G-3), Audit authorship (G-5), System-Health uptime/CPU (G-6) can't fully meet spec. Mitigation: explicit "not captured" labels (existing precedent: `system.py:62-66` CPU/RAM honesty) — never chart sentinels. |
| R6 | **Tailscale-served deployed app disruption** | **LOW** | Redesign is frontend + additive read-only backend, binds loopback:8500 behind `tailscale serve` unchanged; no unit/port/bind change. Isolation I6 (loopback-only, `app.py:163`) preserved. |
| R7 | **XLSX export re-exposes data / copy-gate** | **LOW-MED** | Policy (Q3), not technical. Ship gated OFF. |

### 4.2 Route-preservation conflicts

**None forced — default PRESERVE holds everywhere.** All 24 existing endpoints stay and extend additively; the 6 new screens (Strategy Ranking, Strategy Health, Scanner Attribution, Trade Explorer, Live Activity, Trade Logs) add new page routes and (where needed) new `/api/*` endpoints. Merges reuse both underlying endpoints (System Health calls `/api/services` + `/api/vm`; Capital & Risk calls `/api/risk`+`/capital`+`/exposure`+`/capacity`).

Two **non-route** placement issues to surface (report, don't act):
- **Capital & Risk (spec 07) is absent from the target menu tree** (`spec plan §FINAL MENU STRUCTURE` lists Dashboard/Trading/Analytics/Operations/Investigation — no Capital & Risk), yet it is a **Phase-1 "must have"** (`§PHASE-1`), an **Alerts merge target** (`§REMOVED/MERGED`), and has a full spec. It needs a menu home — most naturally under **Operations** (alongside System Health) or as a top-level item beside Dashboard. **Rama's call.**
- **Reports** (current `/reports`) has **no target home** — the specs replace it with per-screen filtered XLSX export (§3.4). Confirm whether the daily-report-file listing is dropped entirely or retained under Investigation.

No compelling technical reason to rename any file/module/route was found. The target's hyphenated multi-word screen names (e.g. "Strategy Ranking") map to internal single-word routes freely.

### 4.3 Dependency order (build shared components before screens)

1. **Nav shell** — the 5-group menu (Dashboard/Trading/Analytics/Operations/Investigation) in `base.html` + per-template nav blocks + AlgoCore rebrand + Inter/13px font tokens. (Touches every template's nav; do first.)
2. **Shared frontend components** — sortable-table (global ▲▼), rows-per-page selector, filter-bar, common-column-order helper, **merged Events/Alerts panel**, **XLSX/CSV export helper (flag-gated OFF)**, KPI-tile + color-band (0-60/60-80/80-100) primitives.
3. **Shared backend read-only primitives** — (a) **score join** (`screener_results`), (b) **signal→trade outcome join**, (c) **scanner→trade P&L attribution** (feeds Scanner Attribution + P&L-by-scanner + Slippage/Execution-by-scanner), (d) **date-range multi-period** readers, (e) Profit-Factor/ROI/composite/trend derivations. Build once, reuse across screens.
4. **Screens, low-risk first:** Dashboard, Configuration, Slippage, System Logs, Audit, Strategies, Capital & Risk → then new-frontend (Strategy Ranking, Strategy Health, Live Activity, Trade Logs) → then new-backend (Scanner Attribution, Trade Explorer, P&L Analytics, Execution Analytics) → then honest-partial/blocked (Positions, Holdings, System Health with gaps) → **Controls last, as a separate G3 track.**

### 4.4 Soak interaction

The PAUSED soak (S1-S8) is tied to the **deployed** GUI (`main` @ `7b1c92d`, tip `33e9223`; VM unit `gui-dashboard` on Tailscale). The redesign MUST live on an **isolated branch** (`gui-g5-*`) so the soak can resume on that known-good tag at any moment, independent of the redesign. Because the redesign is **frontend + additive read-only backend with NO trading-code and NO schema migration**, it cannot affect the trading system; worst-case rollback is reverting the GUI unit to the deployed tag. Gate before any merge to `main`: full 238-case suite + isolation tests green on the branch. **Confirmed: branch isolation preserves the soak's known-good resume point; no schema change proposed.**

---

## SECTION 5 — REUSE INVENTORY (enhancement vs new-build sizing)

**The data layer is ~90% reused. The redesign is predominantly frontend + additive joins. Only 2 screens hit a hard data wall and 1 hits the architecture wall.**

**Readers reused as-is:** `db_reader` (all ~55 query fns, 23 tables, `mode=ro` isolation) · `config_reader` (system-config snapshot-first, strategies, scan_webhook_map, cron_registry) · `log_reader` (hardened tail + **`ref_id` filter** → directly powers Trade Logs) · `host_reader` (systemctl/vm/sentinels) · `metrics_client` (`/health` + the **unwired `/metrics`** → Live Activity/System Health).

**Services reused as-is (the redesign's backbone):**
- `strategy_tower` → **Strategies + Strategy Ranking + Strategy Health + Scanner-level rows** (7 groups + 4 rankings already built, `strategy_tower.py:65-300`).
- `strategy_score` → Strategy Health badges/silence.
- `pipeline_state` → Dashboard pipeline + Live Activity pipeline (13 stages + halt).
- `capacity` → Dashboard capacity monitor + Capital & Risk "Limits" zone (8 rows + 6 groups, all 30 keys).
- `summary_bar` → the pinned top bar (every screen).
- `freshness` → **global**: market clock, poll cadence, staleness coloring, IST — reused by all.
- `config_view` → Configuration screen (8 groups + drift + history).

**Infra reused as-is:** app factory + blueprint pattern (`app.py`), before_request login guard, Alpine-over-JSON + htmx + `base.html` shell, auth (PBKDF2 + TOTP + lockout — Login is **rebrand-only, zero backend change**), `gui_config.yaml` + `.local.yaml` overlay, Waitress loopback bind, Tailscale serve, the **238-case suite + isolation harness** as the regression gate.

**Genuinely NEW build (from existing tables, no schema):** Scanner-attribution reader, Trade-Explorer assembly reader, multi-period/date-range readers, score/outcome joins, merged Events/Alerts feed, 4 new frontend screens (Ranking, Health, Live Activity, Trade Logs) + 6 new page routes. **NEW build that hits a wall (needs a data source, not code):** Positions live-MTM (G4), Holdings broker-side (G4/P1). **NEW that hits the architecture (needs G3):** Controls write-actions.

---

## REPORT-BACK TO RAMA (evidence only)

1. **Report:** `ops_dashboard/docs/G5_REDESIGN_PHASE_A.md` — all 5 sections present (+ Section 0 exec summary).
2. **Headline counts (of 22 target screens = 21 menu + Capital&Risk):** **~9 reuse-as-is** (menu/UX only, additive) · **~4 new frontend** over existing data · **~9 new-backend/blocked** — of which **2 are hard-blocked by the no-broker-session wall** (Positions live-MTM, Holdings broker-side) and **1 by the read-only architecture** (Controls writes). **No new DB schema is required** for any buildable screen.
3. **Top risks:** (HIGH) Controls needs a non-existent control-plane + breaks read-only isolation → G3, not B/C. (HIGH) Live broker LTP/MTM/holdings unreachable read-only (G4) → Positions/Holdings honest partials. (MED-HIGH) 238-case contract gate pins shapes/counts → redesign must be additive. (MED) Multi-period analytics is a broad new read-only build. (MED) Execution per-stage timings + Audit authorship aren't persisted → honest partials.
4. **Preserve-vs-rename:** **none — all preserved.** Two *placement* flags (not renames): **Capital & Risk (spec 07) is missing from the target menu tree** despite being Phase-1 + an Alerts-merge target → needs a home (suggest Operations); **Reports** has no target home (replaced by per-screen export).
5. **Q3 / XLSX:** ~20 screens request "Download XLSX (filtered)". Collides with `reports_download_enabled=FALSE` + copy-gate. Framed as ONE flag-gated shared export component + a policy toggle — **your decision**, not resolved here.
6. **Memory updates:** memory file `gui_g5_phaseA_04jul` + MEMORY.md pointer written; SYSTEM_MAP.md GUI section + PATHS.md GUI line updated with the report path + Phase-A state.
7. **Deviations / NOT RUN:** Specs were read from the **PC** copy (`D:\...\gui\`), not the VM — no VM copy needed (temp `gui/` folder can be deleted on your word once G5 lands). Git: **HELD** (no branch/commit/push) per no-push posture + in-flight working-tree changes; docs written to the tree, ready for `gui-g5-phaseA-04jul` when you choose. No code, schema, route, or config was changed.

*Phase A is investigation + mapping only. Web Claude reviews this, then issues Phase B (design). No coding until you approve the mapping.*
