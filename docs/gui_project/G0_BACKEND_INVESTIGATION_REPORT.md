# G0 — BACKEND INVESTIGATION REPORT (Frontend GUI Project)

**Phase:** G0 — Backend Investigation (INVESTIGATION ONLY; zero code changes).
**Date:** 2026-07-02 (Thu), IST.
**Working tree:** branch `fix-p1-eod-broker-reconcile-02jul` (repo `D:\Projects\trading-system`).
**Deployed/live baseline:** `origin/main == 9becf8c` (schema **v41**). **This checkout is ahead** — see the schema caveat below.
**Method:** static read of code/config/docs across five parallel read-only investigations + direct verification of the two CRITICAL sections (control surfaces §3, security §6). Every claim is cited `file:line`. Where a fact is a live runtime value that a Windows PC cannot read (systemd `is-active`, `ss -tlnp`, `fail2ban-client status`, `auditctl -l`, live RAM/CPU/disk), it is marked **[REQUIRES LIVE VM]**.

> **This report designs nothing.** It records what exists today so Web Claude (Phase G1) can design the GUI architecture. "DOES NOT EXIST" means the codebase/docs contain no such thing. GUI-vs-security trade-offs are explicitly deferred to Web Claude + Rama + the parked VM-Security-Hardening project.

### Schema-version caveat (read before §2)
The **live VM** runs schema **v41** (deployed `9becf8c`). This **working tree** (`fix-p1…`) carries schema **v42** (`core/state_store.py:101 EXPECTED_SCHEMA_VERSION = 42`; `core/schema.sql:1477` trailing INSERT `'42'`) because the unpushed P1 branch adds `eod_broker_reconciliation`. Tables/counts below are read from the **checkout (v42)**; the one table that does not yet exist on the live VM is `eod_broker_reconciliation`. Similarly, **B-1 unrealized-MTM is NOT on this branch** (it is on the unpushed `fix-b1-daily-loss-mtm-02jul`) — noted where relevant.

---

## SECTION 1 — EXISTING HTTP / NETWORK SURFACE

### 1.1 `signals/webhook_receiver.py`
- **Framework:** Flask, single file. `from flask import Flask, request, jsonify` (`signals/webhook_receiver.py:52`); app built `self.app = Flask(__name__)` (`:202`). Locked decision WR2 "Flask, single file" (`:12`).
- **Host/port:** NOT bound inside the module — it only builds the app. Config keys `config/system_config.yaml` `webhook.bind_host: "0.0.0.0"`, `webhook.bind_port: 5000`, `webhook.require_hmac: false`, `webhook.per_ip_rate_limit_enabled: true / per_ip_burst: 60 / per_ip_refill_per_sec: 5.0`. **Bind is `0.0.0.0` (all interfaces)** — intentional (comment relies on `require_hmac`+firewall/security-group).
  - **Doc contradiction (flag for G1):** `DEPLOYMENT.md:306-308` claims the process binds `127.0.0.1:5000` (localhost only). The live config binds `0.0.0.0`. The doc describes an intended-but-unimplemented reverse-proxy setup; the config is authoritative → `0.0.0.0`.
- **Routes:** registered in `_register_routes` (`:251-275`): `POST /webhook/<scanner_name>` (`:268-270`), `GET /health` (`:255-266`, returns `{status, kill_switch_active, queue_size, queue_capacity, queue_depth}`), plus an `errorhandler(500)` (`:272-275`). That is the entire route surface.
- **Auth:** in `_process_request` (`:398-432`) gated on a configured secret (`WEBHOOK_SECRET`): (a) HMAC header `X-Webhook-Signature: sha256=<hex>` verified `hmac.compare_digest` over the raw body (`:411-419`); (b) `?token=<secret>` query-param bearer, accepted **only when `require_hmac=False`** (`:428-430`); missing auth → 401 (`:432`). Construction guard raises if `require_hmac=True` with empty secret (`:134-140`). **Per-IP token-bucket rate limiter** class `_PerIpRateLimiter` (`:58-97`), enforced pre-auth → 429 on exceed (`:357-366`). Extra guards: 1 MB max body (`:204`), kill-switch→403 (`:440-441`), backpressure→503 (`:448-451`), outside-entry-window→403 (`:455-456`), shutdown→503 (`:347-352`). Response codes: 200/400/401/403/404/429/500/503.
- **How run:** **Waitress** (threaded WSGI), **inside the main.py process as a daemon thread** — NOT gunicorn/uvicorn, NOT bare `app.run`. `main.py:2811-2824` `threading.Thread(target=waitress.serve, args=(webhook_receiver.app,), kwargs={host, port, threads:8, connection_limit:100}, name="webhook-server", daemon=True)`. Post-start self-check hits `http://127.0.0.1:{port}/health` (`main.py:2828-2832`). (The `receiver.app.run(...)` in the class docstring `:107` is illustrative only.)
- **Threading:** 8 Waitress worker threads; each Flask request in its own thread (WR11). Receiver-internal: in-flight sweeper daemon (`_run_sweeper` `:823-848`), locks for in-flight/dedup/rate-limiter.

### 1.2 Other listening / network services
- **SECOND production HTTP server — `scripts/healthcheck_server.py`.** Flask (`Flask("healthcheck")` `:60`) served via Waitress in a daemon thread (`start_healthcheck_server` `:240-267`), **host `0.0.0.0`, port 8080, threads=1** (`:243-244`), started from `main.py:2835-2842`. Routes: `GET /health` (`:62-107` → `{status, checks{db,token,kill_switch,tgt_retry}, uptime_seconds, trades_today}`, 200/503), `GET /metrics` (`:110-204` → JSON incl. `daily_pnl, open_positions, capital_deployed_pct, kill_switch_state, signals_received/traded, last_signal_at`), `GET /metrics/prometheus` (`:206-235`). **UNAUTHENTICATED** (no secret/HMAC on this server) — flagged in `docs/audit/system_security_audit_02jul2026.md:92-95` as an existing open surface exposing P&L/capital/kill-switch.
- `utils/instance_lock.py:89-93` — loopback single-instance mutex `socket.bind(("127.0.0.1", lock_port))` (port **5001**; never `accept()`s). Not a service.
- `scripts/test_webhook.py:1-13` — standalone Flask test stub (`app.run(0.0.0.0:5000)`), `__main__`-guarded dev tool, NOT in production.
- No FastAPI / uvicorn / http.server / socketserver / HTTPServer anywhere (grep of repo ex-venv).
- **Summary:** TWO production HTTP listeners, both Flask+Waitress, both `0.0.0.0`, both daemon threads inside `main.py` (so **up only while the trading process runs**): webhook :5000 (authed) and healthcheck/metrics :8080 (unauthed).

### 1.3 Open ports & firewall
- **[REQUIRES LIVE VM]** actual `ss -tlnp`, iptables/nftables, Oracle Cloud security-list. From code/docs: listeners are :5000 (webhook), :8080 (healthcheck), loopback :5001 (instance lock).
- **ufw:** documented to open **port 5000** only — `docs/web_claude/05_deployment/step3_systemd_and_firewall.txt:53-59`. No repo iptables/nftables/ufw time-schedule rule exists (grepped). Oracle Cloud security-list config not present in repo → **[REQUIRES LIVE VM]** / Rama's console.

### 1.4 Reverse proxy / TLS
- **DOES NOT EXIST.** No nginx/caddy/apache config, no `.pem`/`.crt`, no certbot/letsencrypt automation, no proxy systemd unit in the repo. The only mention is a **recommendation** — `DEPLOYMENT.md:298-403` §8 "Nginx Reverse Proxy Recommendation (DOCUMENT-001)" (sample 443/SSL/Certbot→localhost:5000), explicitly framed as future work ("Add the Nginx layer before Week 2 of live trading", `:403`). Both HTTP servers currently serve **plain HTTP on all interfaces**. ("TLS" elsewhere in docs refers only to SMTP STARTTLS for email alerts.)

---

## SECTION 2 — DATABASE INVENTORY

Authoritative schema `core/schema.sql`; split/ATTACH wiring `core/db_connect.py`; version+PRAGMAs `core/state_store.py`. Two SQLite files (v28 split): **MAIN** `data_store/trading_system.db` + **ANALYTICS** `data_store/analytics.db` (ATTACHed as alias `analytics` on every connection, `core/db_connect.py:52-64`; `ANALYTICS_TABLES=("candles","system_metrics","system_metrics_daily")` `:26-30`).

### 2.1 Table list & counts
- **Schema version = 42** in this checkout (`core/state_store.py:101`; `core/schema.sql:1477`). (Live VM = v41; see caveat.) Note: the schema file's own header "Version 1" / footer "v24" are stale banners — the `schema_meta` INSERT is authoritative.
- **MAIN db = 43 tables** (`core/schema.sql`, 43× `CREATE TABLE IF NOT EXISTS`). **ANALYTICS db = 3 tables** (`core/analytics_schema.sql`: `candles` :24, `system_metrics` :51, `system_metrics_daily` :75). **Total = 46.** (`capital_ledger` was DROPped v10; table numbering has intentional gaps.)
- **MAIN tables** (name — schema.sql line — purpose): `schema_meta` (29), `signals` (42), `trades` (117), `orders` (270), `capital_snapshot` (346), `system_events` (391), `session` (414), `fm_ledger` (454), `kill_switch_state` (501), `webhook_audit` (517), `eod_squareoff_log` (554), `reconciliation_log` (585), `screener_results` (617), `smart_tgt_state` (649), `innings` (679), `gate_state` (716), `trade_excursions` (760), `pnl_reconciliation` (780), `telegram_alerts` (802), `trade_journal` (819), `position_reconciliation` (847), `strategy_metrics` (869), `shadow_trades` (893), `fno_ban` (927), `eod_verification` (941), `cron_heartbeat` (955), `order_execution_log` (986), `trade_slippage_log` (1019), `market_execution_context` (1059), `preflight_runs` (1085), `preflight_check_results` (1103), `preflight_autofix_log` (1119), `gtt_state` (1155), `sr_detector_results` (1194), `retest_state` (1249), `excursion_reconstruction_runs` (1297), `control_tower_findings` (1327), `control_tower_runs` (1354), `control_tower_trends` (1371), `control_tower_status` (1384), `control_tower_freshness` (1399), `config_snapshots` (1427), `eod_broker_reconciliation` (1455, v42/P1).
- **ANALYTICS tables:** `candles` (1-min OHLCV, ~thousands/day), `system_metrics` (5-min resource snapshots, ~75/day), `system_metrics_daily` (daily roll-up).
- **Row counts — [PC LOCAL DEV COPY, NOT live VM]:** the local `data_store/trading_system.db` (761 KB, 02-Jul) is near-empty: only `cron_heartbeat`=819, `fm_ledger`=45, `system_events`=27, `session`=1, `kill_switch_state`=1, `schema_meta`=1; **all trading tables (`signals`/`trades`/`orders`/`webhook_audit`/`trade_slippage_log`/…) = 0**. Local `analytics.db` all 0. **Live counts REQUIRE the VM DB.**

### 2.2 Module → table/column map
- **Orders** → `orders` (270-337): `order_id` PK, `trade_id` FK, `leg` CHECK ENTRY/SL/TGT/EOD/CO/CANCEL (275-277), `status`, `qty_requested/filled/avg_fill_price`, `placed_at/filled_at`, `rejection_reason`, `reconciliation_status`, `superseded_by`.
- **Signals + pre-storage drop** → `signals` (42-93): `signal_id` PK, `status` (open-set CHECK w/ GLOB families), `webhook_payload`, unique `idx_signals_fingerprint_today` (82-83). **The ~82% drop is PRE-INSERT:** `signals/webhook_receiver.py` in-memory TTL dedup (`_dedup_cache`, 300 s) returns `DUPLICATE` at `:656-659` with **no row**; INSERT happens only after dedup (`:678-694`). So the "received" denominator lives in **`webhook_audit`** (aggregate accepted/rejected, `:524-525`), the "stored" numerator in `signals`. (Live ratio cited historically ≈18.3% stored.)
- **Trades / Positions / Holdings** → `trades` (117-255) is the spine. **There is NO `positions` table** — open positions = `trades WHERE status IN ('OPEN','PARTIAL','PENDING_FILL','EXITING',…)` (158-161). **There is NO `holdings` table** — CNC/delivery overnight is modelled by `gtt_state` (1155-1180, local mirror of the broker GTT; broker is authority).
- **Daily P&L / Capital** → `fm_ledger` (454-490, append-only; `entry_type` incl. RELEASE_USED/RESET_PNL; `pnl_delta`, `costs`, generated `date`), `trades.net_pnl/gross_pnl/charges` (147-149,174-179), `capital_snapshot` (346-363, single row). Query `get_daily_realized_net_pnl` (`core/state_store.py:2346-2365` = `SUM(pnl_delta)-SUM(costs)`; **W10:** double-subtracts costs — safe direction, non-urgent). Day-opening capital = INIT ledger row (`:2367-2389`).
- **config_snapshots (W0)** → (1427-1438): full resolved AppConfig JSON per date, idempotent by (date, config_hash). Written by `core/config_snapshotter.snapshot_config`.
- **Slippage** → `trade_slippage_log` (1019-1055, per-trade roll-up, 3-leg entry/SL/TGT decomposition + planned/actual RR + `price_band`); raw `order_execution_log` (986-1016); `market_execution_context` (1059-1076).
- **Innings / shadow_tracker** → `innings` (679-705, `is_real`, ≤3/trade, UNIQUE(trade_id,inning_number)). Distinct from `shadow_trades` (893-921, shadow paper engine).
- **Audit / event** → `reconciliation_log` (585), `sr_detector_results` (1194), `system_events` (391), `webhook_audit` (517), `telegram_alerts` (802), `cron_heartbeat` (955), `eod_verification` (941) + `eod_broker_reconciliation` (1455) + `pnl_reconciliation` (780) + `position_reconciliation` (847), `excursion_reconstruction_runs` (1297), `preflight_*` (1085-1130), `control_tower_*` (1327-1407).
- **Strategy Status** → `strategy_metrics` (869-885) + `trades.strategy` + `screener_results` (617). **Risk** → no dedicated table; computed at runtime (`fm_ledger` for daily-loss, `recent_trade_pnls()` for consecutive losses, `kill_switch_state` for halt, `trades.risk_amount/margin_reserved`). **Exposure** → derived from open `trades` (`margin_reserved`, `actual_position_value_rs`) + `capital_snapshot.margin_used`. **Trade Statistics** → `trades` + `trade_excursions` (MFE/MAE) + `strategy_metrics` + `innings`. **Execution monitoring** → `orders` + `order_execution_log` + `trades.signal_to_order_ms/order_to_fill_ms/total_latency_ms` (189-191) + `reconciliation_log` + `smart_tgt_state`.

### 2.3 Write-concurrency reality (read-only GUI reader safety)
- **WAL mode = YES.** MAIN `_CONNECTION_PRAGMAS` (`core/state_store.py:106-111`): `journal_mode=WAL`, `synchronous=FULL`, `foreign_keys=ON`, `temp_store=MEMORY`, **`busy_timeout=30000` (30 s)**. ANALYTICS WAL set on ATTACH (`core/db_connect.py:38-41,62-63`) and init (`:85-87`). Raw-script connect helper also sets busy_timeout 30 s + ATTACH (`:97,108`).
- **Checkpointing:** routine `checkpoint_wal()` = `PRAGMA wal_checkpoint(PASSIVE)` (`state_store.py:247-264`, readers proceed); `checkpoint()` = TRUNCATE at EOD after positions close (`:266-301`, skipped on ticker thread FIX-088). No `wal_autocheckpoint` override (SQLite default 1000 pages).
- **Writers:** main process writes the trading tables during market hours + startup/shutdown; cron/EOD scripts (via `core/db_connect.connect`, same WAL+30 s) write reconciliation/verification/metrics tables.
- **Conclusion (on code facts):** WAL + 30 s busy_timeout + PASSIVE routine checkpoints ⇒ a **read-only reader is safe concurrent with the writer** during market hours (WAL readers don't block writers and vice-versa; momentary contention waits ≤30 s). The reader should open via the same ATTACH sequence (`core/db_connect.connect`) and connect read-only.

---

## SECTION 3 — STATE & CONTROL SURFACES  *(CRITICAL — directly verified)*

Verified: kill switch `capital/kill_switch.py`; resolver `strategies/control.py::strategy_will_trade` (pure → **paper==live**); per-strategy `config/strategies/*.yaml enabled:` → `strategies/schema.py:61`; master gate `config/system_config.yaml trade_type`. **NO inbound Telegram** — `alerts/telegram_notifier.py` exposes only outbound `send*`/`send_alert`; no `getUpdates`/`CommandHandler`/`Updater`/polling in production (`:179` comment: personal_chat_id "reserved for v2.1 bot-command interactions"). **Telegram is outbound-only — no command channel exists.**

For each action: (a) how today, (b) entry point, (c) parity, (d) restart, + gap.

### 3.1 Pause / Resume a strategy
- **No runtime "pause" distinct from disable exists.** A paused strategy == a disabled one. `strategy_will_trade:79-83` returns `Verdict(False, "WON'T TRADE — switch disabled")` from `StrategyConfig.enabled`. No per-strategy `pause`/`resume` symbol. Parity: pure resolver. Restart: required (config loaded once).
- **GAP: NO programmatic runtime-pause entry point** — would need one built.

### 3.2 Enable / Disable a strategy or scanner
- **How:** edit `config/strategies/<name>.yaml enabled:` (LAYER 3), or `system_config.yaml trade_type` (LAYER 1), or `force_intraday_only` (LAYER 0 breaker). **Loaded ONCE at startup** — `strategies/loader.py::load_all_strategies:35-80`, docstring `:25-27` "Strategies are loaded ONCE; no hot reload." No config file-watcher (`core/config_loader.py` has none).
- **Scanner on/off:** no independent flag — scanners map to strategies via `scan_webhook_map` (`loader.py:93-137`); disable the mapped strategy is the only lever.
- Parity: pure resolver/loader. Restart: **RESTART-REQUIRED, not live-mutable.**
- **GAP: no live-mutable enable/disable API** — YAML edit + restart only.

### 3.3 Kill pending orders
- **How:** internal/automatic only. Single cancel `broker/zerodha_adapter.py::cancel_order:864-918` (live `kite.cancel_order`; paper flips `_paper_fills` status — parity). Bulk ENTRY cancel `broker/order_monitor.py::cancel_all_entry_orders:519-578` — **only at graceful shutdown**. Placer/kill-switch internal cancels (`order_placer._cancel_*`, `kill_switch._cancel_trade_resting_exits:971-1022`). (Note: `kill_switch.py:143` references a legacy `order_placer.cancel_all_open` that **does not exist**; production uses the adapter path via `set_adapter`.)
- **GAP: NO operator-facing cancel** (all / per-strategy / per-symbol). Today cancellation happens only by tripping HARD_KILL, EOD squareoff, or process shutdown. Would need one built.

### 3.4 Manual square-off / position exit
- **How:** no operator command to flatten one named position. Bulk flatten exists but only auto-triggered: `orders/eod_squareoff.py::fire_now:232-254` (called internally from daily-loss path `main.py ~:689` + poller `check_and_fire`), and `kill_switch.hard_kill → _exit_all_trades_indestructible:1024-1271`. Per-trade `order_reconciler._emergency_market_close:2234` fires only from CHECK9. Parity: `fire_now`/`hard_kill` both use the adapter (paper vs Zerodha), explicit paper branches.
- **GAP: NO entry point to exit ONE named position on demand; no CLI wrapper for bulk `fire_now`/`hard_kill`.** Would need built.

### 3.5 Emergency trading halt (SOFT_KILL / HARD_KILL)
- **States:** `KillState` enum (`kill_switch.py:104-108`) INACTIVE / SOFT_KILL (block entries, allow exits) / HARD_KILL (block all + cancel + flatten). `is_active(intent)` `:394-410`.
- **Triggered TODAY — all automatic; NO operator "trigger" CLI, no `--kill`/`--halt` flag.** Callers: API-failure streak (`record_api_failure → soft_kill:588-663`), token expiry (`token_monitor.py:45`), live-feed failures (`data/live_feed.py:303/474/648`), capital drift (`fund_manager.py:963/2091 hard_kill`; `drift_handler.py:231/236`), placer safety (`order_placer.py:1377/3581 hard_kill`), reconciler (`order_reconciler.py:647/2133`), CNC GTT monitor (`:583`), EOD cron (`system_manager.py::trigger_soft_kill:852-865`, `reconcile_pnl.py:276`). Forcing a halt on demand today = a **raw DB write** to `kill_switch_state` (unsupported/unscripted).
- **State store:** `kill_switch_state` single row id=1, `_persist_state:717-736` (INSERT OR REPLACE), loaded on boot `_load_state_from_store:738-781` (persist-first atomicity KS9). Live-mutable (no restart to set/clear).
- **Stale same-day SOFT_KILL + auto-clear:** `clear_stale_state(today):199-248` clears **prior-day** kills only (`:220 if triggered_date >= today: return False`); `auto_clear_scheduled_kill:250-313` clears **scheduled** reasons (`SCHEDULED_KILL_REASONS:89-92`) same-day iff no open positions; **HARD_KILL never auto-cleared** (`:264-270`); an **emergency same-day SOFT_KILL persists** until manual clear or the next 08:15 boot. Both called at boot (`main.py:1535/1540`).
- **`--resume`:** `main.py:1559-1567` only in the HALT startup scenario (`if not args.resume: return 4` else `kill_switch.resume(...)`). Caveat: standalone `main.py --resume` competes with the service for the instance lock (port 5001) — the 18-Jun collision.
- **Clear a halt (4 ways):** (1) **`scripts/clear_kill_switch.py`** (`--dry-run`/`--force` for HARD_KILL; systemd-friendly, no instance lock, `:41-86`) — the one true operator control CLI; (2) `main.py --resume` (lock caveat); (3) `deploy/resume.sh` (stop→clear→start); (4) next 08:15 boot (prior-day only). `resume()` = `kill_switch.py:553-586`.
- Parity: mode-agnostic state; HARD_KILL exit uses `self._adapter`.

### 3.6 Restart services
- **Units** (`PATHS.md:46-49`): `trading-system.service` (`main.py --mode live`), `token-watcher`, `alert-watcher`, `trading-watchman`, `security-watcher`, `cron-watchdog.timer`. **Restart:** `ssh trading-vm 'sudo systemctl restart trading-system.service'`. **Deploy ≠ restart** — `git push` updates the tree via the bare-repo hook but does not restart; old code stays in memory until explicit restart.
- **Survives restart (DB-persisted):** kill-switch state, `trades`, `orders`, capital/`fm_ledger`, `gtt_state`, `retest_state`, `eod_squareoff_log`, `session`, `sr_detector_results`, `config_snapshots`. Rehydrated on boot (`_load_state_from_store`, `clear_stale_state`/`auto_clear_scheduled_kill`, startup-scenario detection COLD/WARM/CRASH/HALT `main.py:1543`, reconciler startup sync, `cnc_gtt.hydrate_from_store`).
- **In-memory only (LOST):** `in_flight` dict, `order_monitor._watched`, `eod_squareoff._fired_for_date`/poller, `_api_failure_count` + throttle timestamps, structure-exit/retest debounce maps. Re-derived from broker+DB truth on boot.
- Parity: systemd runs `--mode live`; paper runs same `main.py --mode paper`.

### 3.7 Actions with NO existing programmatic entry point (must be built for a GUI)
| Control action | Today | Operator entry point |
|---|---|---|
| Pause strategy (runtime ≠ disable) | concept absent | **NONE — build** |
| Enable/Disable strategy | YAML `enabled:` + **restart** (no hot reload) | **NONE live — build** |
| Enable/Disable scanner | via mapped strategy + restart | **NONE independent** |
| Master gate `trade_type` | YAML + restart | **NONE live** |
| Cancel pending orders (all/strategy/symbol) | internal/auto only | **NONE operator — build** |
| Square-off ONE named position | internal (CHECK9) only | **NONE — build** |
| Square-off ALL (manual) | `fire_now`/`hard_kill` exist, no CLI | **NO CLI wrapper** |
| TRIGGER a halt on demand | all callers auto; raw DB write only | **NONE scripted — build** |
| CLEAR a halt | `clear_kill_switch.py` / `--resume` / `resume.sh` / 08:15 | **YES (CLI)** |
| Restart service | `systemctl restart` via SSH | **YES (systemd/SSH)** |

**Existing operator/admin scripts:** `scripts/clear_kill_switch.py` (only true control CLI), `scripts/strategy_status.py` (READ-ONLY status), `scripts/system_manager.py` (cron EOD auditor, auto `trigger_soft_kill`), `scripts/reconcile_pnl.py` (cron, can soft_kill), `scripts/revert_temp_config.py` (YAML helper). No `halt.py`/`squareoff.py`/`cancel_orders.py`/`strategy_toggle.py`.

**Overall:** the system is built for **autonomous, config-file-driven** operation. The only live operator surfaces today are **clear-kill-switch (CLI)** and **restart-service (systemd/SSH)**. Every other control Rama wants (pause / enable-disable / cancel orders / single square-off / trigger-halt) has **no programmatic operator entry point** and needs an API built. Every control path that exists is **paper/live parity**.

---

## SECTION 4 — REAL-TIME DATA AVAILABILITY

### 4.1 Event bus / pub-sub
- **In-process synchronous EventBus** — `core/events.py::EventBus:241-351` (`subscribe:268-298`, `publish:300-345`). Docstring EV1-EV5 (`:9-23`): "Synchronous in-process dispatch… no queue, no async, no background thread… do not make a global singleton." Optional per-subscription background thread (`async_dispatch=True`, `ThreadPoolExecutor(max_workers=1)` `:290-296`) is still in-process, fire-and-forget. Event types: `OrderFilled`, `PositionClosed`, `KillSwitchActivated`, `CapitalDriftDetected`, `EodSquareoffComplete`, `OrderStatusChanged`, `InstrumentsRefreshed`, `OrderPartiallyTerminated`.
- **Candle-close callbacks** — `data/candle_store.py::register_on_candle_close:221-225` (in-memory list, `_lock`).
- **Conclusion:** No bus/queue/pub-sub is exposed over ANY transport — both registries are **in-process only**. **A GUI CANNOT get push updates today.** Cross-process options: (a) poll SQLite; (b) poll the in-process HTTP `/metrics` (only while the trading process is alive). No broker, no websocket.

### 4.2 Tick / LTP availability
- LTP source = broker adapter, **live only**: `ZerodhaAdapter.get_quote:1537-1613` (`Quote.last_price`; live `kite.quote`; paper delegates to injected `_quote_provider`, else `NotImplementedError`). No distinct `get_ltp`. Live ticks accumulate in **CandleStore memory** (`_history` deque), not persisted per tick. **No `ticks`/LTP table or cache file.** The `candles` table is **daily** OHLC (written 15:40 by `fetch_daily_candles.py`), not live LTP.
- **B-1 `_unrealized_mtm` is NOT on this branch** (writers have zero prod callers even where present; the B-1 refresh loop + `daily_loss_include_unrealized` flag live on the unpushed B-1 branch).
- **Conclusion:** current LTP per open position **is not in any persisted store** — it lives only in the trading process's memory, or must be fetched live from the broker. An external reader (GUI) can get live LTP **only by opening its own broker session** and calling `get_quote` (the pattern `scripts/eod_broker_reconcile.py` already uses to run process-down).

### 4.3 Log files
- Setup `core/logger.py::setup_logging:313-413`. **Four daily-dated files** (one file/day, append; **plain `FileHandler`, NOT Rotating** — `:350-354`): `system_YYYY-MM-DD.log` (**JSON-lines**, INFO+), `trades_YYYY-MM-DD.log` (**JSON-lines**, records w/ signal_id/trade_id/order_id), `reconciler_YYYY-MM-DD.log` (**JSON-lines**, order_reconciler/order_monitor), `debug_YYYY-MM-DD.log` (**plain text**, DEBUG+). Stdout handler WARNING+ plain. Async `QueueHandler`/`QueueListener` (FIX-099, `:382-404`). **Retention:** cron `log_cleanup` deletes `logs/*.log` >30 days @00:00 (`config/cron_registry.yaml:8-19`).
- Cron per-job logs: unstructured `>> logs/cron-*.log 2>&1` (e.g. `logs/preflight.log`, `logs/cron-officer.log`, `logs/cron-eod-broker-reconcile.log`, `logs/daily_report.log`, …). `logs/failed_alerts.log` = Telegram-failure JSON-lines.
- **Doc contradiction (flag):** `deploy/logrotate/trading-system:6-7` wrongly claims `RotatingFileHandler (50MB,5 backups)` — the code uses `FileHandler`. SYSTEM_MAP is correct.
- **Useful for a Logs Viewer:** the 3 JSON-lines files (structured, directly parseable); `debug_*`/`cron-*` are plain text.

### 4.4 Report artifacts
- `reports/daily_trade_review.py::generate` → **`reports/output/daily_trade_review_report_<YYYY-MM-DD>.xlsx`** (`:2384-2385`; 7 sheets: Dashboard, Reconciliation, Orders, Signals, Strategies, Slippage, Config). Cron `daily_trade_review` **16:07 Mon-Fri** (`config/cron_registry.yaml:445-458`, monitored/heartbeat). Legacy `daily_report` still runs 16:05 (`reports/output/daily_report_<date>.xlsx`); old `daily_review` deleted 01-Jul.
- **Retention: NONE.** No pruning of `reports/output/` anywhere (only `logs/*.log` is pruned). `docs/architectural_audit_2026-06-01.md:334` flags "report files will grow indefinitely." → xlsx accumulate unbounded.

---

## SECTION 5 — SERVICES, CRON, HEALTH

### 5.1 Systemd units (`deploy/systemd/`; `is-active` = [REQUIRES LIVE VM])
| Unit | ExecStart | Notes |
|---|---|---|
| `trading-system.service` | `venv/bin/python main.py --mode live` | main app; `User=ubuntu`, `Restart=on-failure`, `RestartPreventExitStatus=3 4`, `KillSignal=SIGINT` |
| `token-watcher.service` | `bash deploy/token_watcher.sh` | **`User=root`**, `Restart=always`, 30 s; auto-starts app on fresh token |
| `trading-watchman.service` | `venv/bin/python scripts/gemini_watchman.py` | `BindsTo=trading-system.service`; AI log monitor (market hours) |
| `alert-watcher.service` | `venv/bin/python scripts/alert_watcher.py` | sentinel→email digest; `Restart=always` (~10 s periodic-oneshot) |
| `security-watcher.service` | `venv/bin/python scripts/security_monitor.py --watch` | `Restart=always`+`RestartSec=60` (~60 s) |
| `cron-watchdog.service`+`.timer` | `venv/bin/python scripts/cron_watchdog.py` | systemd timer `OnCalendar=19:30`, `Persistent=true`; Tier-2 watch-the-watcher |
Plus the healthcheck HTTP server as an in-process daemon thread (not a unit).

### 5.2 Crontab (source of truth `config/cron_registry.yaml` → `deploy/cron/trading-system.cron`; `crontab -l` = [REQUIRES LIVE VM])
**45 registry entries** (41 trading/ops + 4 `claude_heartbeat` personal-tooling; `db_retention`+`db_retention_vacuum` share one marker). Ops-relevant schedule (all Mon-Fri unless noted): `log_cleanup` 00:00 · `db_backup` 01:00 · `analytics_backup` 01:05 · `backup_retention` 02:00 · `sentinel_retention` 02:05 · `db_retention` 02:30 (Mon-Sat) / `db_retention_vacuum` 02:30 (Sun) · `token_cleanup` 05:00 · `auto_refresh_token` 08:15 · `preflight_phase_a` 08:30 · `fetch_fno_ban` 08:35 · `gemini_premarket_brief` 08:55 · `refresh_instruments` 09:00 · `preflight_phase_b` 09:14 · `preflight_phase_c` 09:15 · `cron_officer_briefing` 09:20 · `capture_metrics` */5 09-15 · `disk_monitor` hourly · `fetch_daily_candles` 15:40 · `reconcile_positions` 15:45 · `reconstruct_excursions` 15:50 · `eod_cleanup` 15:50 · `eod_verify` 15:55 · `sr_detector_backfill` 15:58 · `eod_broker_reconcile` 15:58 (P1 new) · `wal_checkpoint` 16:00 · `generate_screened_csv` 16:01 · `daily_report` 16:05 · `daily_trade_review` 16:07 · `trade_journal` 16:10 · `compute_strategy_metrics` 16:15 · `metrics_summary` 16:16 · `gemini_log_review` 16:20 · `gemini_trade_coach` 16:40 · `gemini_data_integrity_check` 17:00 · `control_tower` 17:05 · `check_cron_drift` 18:00 · `system_manager_eod` 18:45 · `cron_officer_eod` 18:50 · `gemini_weekly_patterns` 18:00 Sun · `backup_restore_drill` 03:00 monthly · `claude_heartbeat_*` 05:30/10:31/15:32/20:33. (Cite `config/cron_registry.yaml:7-693`.)

### 5.3 Health-check code reusable for Service/VM Health
- **`scripts/healthcheck_server.py`** — HTTP `/health` (db/token/kill_switch/tgt_retry, uptime, trades_today) + `/metrics` (daily_pnl, open_positions, capital_deployed_pct, kill_switch_state, signals, last_signal_at) + `/metrics/prometheus`. **Reads SQLite; lives inside the trading process (up only while app runs).**
- **`scripts/preflight/checks/vm_health.py`** — `VmRamCheck` (`/proc/meminfo MemAvailable`, min 1 GB), `VmDiskRootCheck` (≥5 GB & <90%), `VmDiskDataCheck` (≥2 GB), `VmNtpSyncCheck` (ntplib skew). Uses `shutil.disk_usage`+`/proc/meminfo` (no psutil).
- **`scripts/capture_metrics_baseline.py`** — 5-min snapshot → `system_metrics` (analytics.db): cpu_pct, memory_mb, db_size_mb, log_size_mb, open_fds, thread_count, disk_used_pct; `--summarize`→`system_metrics_daily`. **psutil-gated:** cpu/mem/open_fds `return -1.0/-1` when psutil absent; **only disk is real** (shutil, fixed 29-Jun).
- **Control Tower** (`ops/control_tower/`, 17:05) — freshness engine (per-stage OK/OVERDUE/MISSING/NA), size_logger (disk_used_pct via shutil, backup/log/db sizes), disk/aggregator/reporter, findings lifecycle.
- **`scripts/security_monitor.py`** (`--watch`, ~60 s) — SSH keys, sudo, login-rate, file-hashes, session count, copy-protection; alert-only.
- Others: `disk_monitor.py` (hourly), `cron_watchdog.py` (19:30), `check_cron_drift.py` (18:00), `utils/startup_checks.py::check_disk_space` (startup gate, min 2 GB).
- Stored: `system_metrics`/`system_metrics_daily` (analytics.db) + control-tower tables + live HTTP.

### 5.4 VM headroom  — [REQUIRES LIVE VM]
- **Documented minimum spec:** `docs/06_deployment_guide.md:7-9` "Ubuntu 22.04+, **2 vCPU, 2 GB RAM, 20 GB disk** minimum." VM `161.118.187.249`, user `ubuntu`, Oracle Cloud headless.
- Preflight thresholds: RAM ≥1 GB free, `/` ≥5 GB & <90%, data ≥2 GB, NTP FAIL >30 s (`vm_health.py:19-23`). Startup gate `logging.min_free_disk_gb: 2.0`.
- **psutil ABSENT on the VM venv** → historical **CPU/RAM headroom is NOT persisted** (`system_metrics` cpu/mem = -1.0 sentinel); only disk is trackable historically. **Any real RAM/CPU/disk headroom number must be measured live** (`ssh … free -h` / `nproc` / `df -h`). Whether the 2 GB / 2 vCPU floor can host a web app + reverse proxy alongside trading during market hours **cannot be answered from the PC — REQUIRES LIVE VM measurement.**

---

## SECTION 6 — SECURITY CONSTRAINTS  *(CRITICAL — directly verified; facts only)*

### 6.1 fail2ban
- Config `deploy/security/jail.local` (→ `/etc/fail2ban/jail.local`). **Exactly one jail: `[sshd]`** (`:25-33`, `logpath=/var/log/auth.log`, maxretry 5, bantime 3600) + `[DEFAULT]` (`:16-23`, `ignoreself`, `ignoreip=127.0.0.1/8 ::1`, no static allow-list — dynamic IPs). Bans SSH password/preauth failures only; key logins never matched (`:8-11`). **No HTTP/web jail** → a new web service and browser HTTPS are NOT filtered by fail2ban. Active jails/bans = **[REQUIRES LIVE VM]**.

### 6.2 auditd watches
- Rules `deploy/security/trading-security.rules` (→ `/etc/audit/rules.d/`, loaded via `augenrules --load`). **11 file-watches (`-w -p wa`)** (`:16-34`): `.env` (env_change), `config/` dir (config_change), 4× `/etc/systemd/system/*.service` (systemd_change), `~/.ssh/authorized_keys` (ssh_keys_change), `/etc/ssh/sshd_config` + `sshd_config.d/` (sshd_config_change), `/etc/sudoers` + `sudoers.d/` (sudoers_change). Plus **3 execve syscall rules** for scp/sftp/rsync (`copy_attempt`, `:45-47`).
- **Note:** docs say "10 watches" (`SYSTEM_MAP.md:939`, Phase-1 count); committed file now has 11. Loaded rules = **[REQUIRES LIVE VM]** (`auditctl -l`).
- **GUI-write impact (factual):** a GUI writing under **`config/`** trips `config_change` (dir watched); writing **`.env`** trips `env_change` (CRITICAL-flavoured). Writes to **`data_store/`, `logs/`, `reports/` are NOT watched** → no auditd event. auditd here is forensic **record-only**, not a blocker.

### 6.3 security_monitor.py (alert-ONLY, never blocks)
- `run_pass()` runs **9 checks** (`:783-793`; the docstring's "7" is Phase-1, +2 Phase-2): (1) `check_authorized_keys` CRITICAL (key hash/fingerprint/count change), (2) `check_new_login_ips` WARNING, (3) `check_failed_spike` WARNING (>500/hr), (4) `check_root_probe_spike` INFO, (5) `check_sudo_events` INFO, (6) `check_watched_files` CRITICAL/WARNING (SHA-256 of `watched_files` changed), (7) `check_active_sessions` WARNING (**ss filter `sport = :22` only** — `:277-278`), (8) `check_copy_protection_switch` CRITICAL (ON→OFF), (9) `check_copy_bypass` CRITICAL (auditd raw outbound copy w/o token).
- **What a NEW web service triggers (factual):** a new **listening port / process / inbound (browser) connection triggers NONE** of the 9 — check 7 counts only `:22` sessions, no check inspects arbitrary ports/processes. The only interactions: (a) writing a **watched config/secret file** → check 6 (e.g. `system_config.yaml` WARNING, `.env` CRITICAL); (b) a setup step adding an **SSH key** → check 1 CRITICAL. **It never blocks** (alert-only). Watched files: `config/security.yaml:62-71` (authorized_keys, sshd_config, sudoers, the .service units, `.env` = CRITICAL; `system_config.yaml`, `accounts.csv` = WARNING). No config key references web ports.

### 6.4 The 18:00–08:00 absolute time-lock
- **Mechanism = a pure Python code guard inside `scripts/copy_gate.py`**, evaluated per copy invocation — **NOT a systemd timer, NOT iptables, NOT a network/SSH lock.** `in_time_lock(now,18,8):105-115`; enforced Rule-1 top priority in `CopyGate.check_copy_allowed:209-215` (returns `CopyDecision(False,"TIME_LOCK")`, "overrides everything including the OFF switch and any valid token"). Config `copy_protection.time_lock_start:18 / time_lock_end:8` (`config/security.yaml:87-88`).
- **Scope — what it BLOCKS:** only **VM-INITIATED outbound `scp`/`sftp`/`rsync`** — the gate is consulted **exclusively** by the copy-guard wrapper (`deploy/security/bin/copy-guard:33-36`) symlinked as `/usr/local/bin/{scp,sftp,rsync}` (`install_copy_protection.sh:40-43`). **Does NOT block:** SSH/interactive login (not wrapped — `copy-guard:11-16`, `SYSTEM_MAP.md:317`), any network port / HTTP, or PC-initiated pulls (detect-only). There is **no iptables/ufw time-schedule and no systemd timer** enforcing any night lock (grepped).
- **Would it block BROWSER (HTTPS) access to a GUI at night? → NO.** The lock has no hook into any HTTP server, listening port, or sshd. A browser reaching a web service on the VM at night is never evaluated by this mechanism.
- **Parked foreign-IP SSH bypass:** reported as mechanism only (no fix). Factually **no code enforces an SSH source-IP/geo restriction** — SSH is key-only with `PermitRootLogin no` (`sshd_config.d/99-trading-security.conf:13`); fail2ban bans only password failures; no IP allow-list by design. No dedicated "foreign-IP bypass" doc/fix exists in the repo.

### 6.5 Copy-protection hard-block
- VM Security Phase 2 (`SYSTEM_MAP.md:301-321,835-846`; HARD-BLOCK active on VM 20-Jun): policy engine `scripts/copy_gate.py::check_copy_allowed:201-242`; wrapper `copy-guard` symlinked over `/usr/local/bin/{scp,sftp,rsync}` (PATH ahead of `/usr/bin`); token issuer `request_copy.py` (15-min token); auditd bypass → `check_copy_bypass` CRITICAL. Priority: TIME_LOCK → enabled-flag → session-cap(>2) → valid-token; fail-safe blocks on gate error (`copy-guard:17-18`).
- **NOT** a hardware-fingerprint / license-lock / machine-binding / anti-tamper mechanism (grepped — none exist). It is purely an **exfiltration control on the scp/sftp/rsync clients**.
- **Would serving files over HTTP conflict with it? → NO interaction.** copy-guard intercepts only `scp/sftp/rsync` execve via the PATH shadow; it does not hook `read()`/`sendfile`/HTTP. A web server reading files and sending them over HTTP does not invoke those binaries, so copy-protection **neither sees nor blocks HTTP file-serving** — HTTP is out of scope of this control (i.e. a GUI download of the xlsx report would bypass copy-protection entirely; stated as a fact, not a recommendation).

### 6.6 Note
Facts only. All GUI-vs-security trade-offs (browser exposure, port binding, night access, the unauthenticated :8080 surface, the parked foreign-IP SSH item) are decisions for Web Claude + Rama + the parked VM-Security-Hardening project — not this report. **[REQUIRES LIVE VM]** items (active fail2ban jails, loaded `auditctl -l`, running services) must be confirmed on the VM.

---

## SECTION 7 — RUNTIME ENVIRONMENT

### 7.1 Python + web packages
- **Python 3.12.3** on the VM shared venv (`SYSTEM_MAP.md:53`). No `.python-version`/`pyproject.toml`/`setup.cfg`; no version pin in requirements.
- **Installed/pinned (relevant):** `flask==3.1.3` (`requirements.txt:11`), `waitress==3.0.2` (`:12`, production WSGI, chosen over gunicorn which is UNIX-only). **DOES NOT EXIST** in requirements: fastapi, uvicorn, starlette, gunicorn (comment only), websockets, jinja2 (ships transitively via Flask but not separately pinned), aiohttp. Other deps: pydantic 2.13.0, kiteconnect 5.1.0, pyyaml, requests, cachetools, openpyxl, python-dotenv, pyotp.

### 7.2 Node.js / npm
- **No Node inside the project** — no `package.json`/`node_modules` in-repo (grep). Node exists on the VM only for **external tooling** outside the project tree: `~/tools/gemini/` (Gemini CLI, node) and `~/tools/claude/` (`PATHS.md:37`, `SYSTEM_MAP.md:61`). The trading system itself has **zero Node/npm dependency**.

### 7.3 Deployment hook (facts only)
- **`deploy/hooks/post-receive`** (note: banner says the currently-installed live hook is checkout-only; this Phase-3 variant auto-installs crontab): on push it acts **only for `refs/heads/main`** (`:14`), runs `git checkout -f main` into `/home/ubuntu/systems/trading-system` (`:16`), then regenerates+diffs the crontab and installs iff `generate==canonical` (`:18-23`). It runs **no `systemctl`** — no service restart/reload.
- **`deploy/hooks/pre-receive`** — NOT installed by default; cron-integrity gate only.
- **A `gui/` subdir** would be checked out automatically like any path — no hook change needed to land files. **A new GUI systemd service** would follow the existing manual pattern (add a unit under `deploy/systemd/`, then `systemctl enable/start` on the VM) — **the hook would not start it automatically.** The main service already hosts both HTTP servers (:5000, :8080) as daemon threads in one process; a GUI could be another thread or a separate unit — the hook imposes no constraint either way.

---

## SECTION 8 — GAP SUMMARY TABLE

Per v1 GUI module: **data source exists?** · **control path exists?** · **gap to build.** (View-only modules have no control path by nature — "N/A".)

| v1 Module | Data source exists? | Control path exists? | Gap to build |
|---|---|---|---|
| **Dashboard** | YES — aggregate of `trades`/`fm_ledger`/`kill_switch_state` + `/metrics` | N/A (view) | Read API/query layer; no push (poll only) |
| **Signals** | YES — `signals` (+ `webhook_audit` for received denominator) | N/A | Read API; pre-INSERT dupes only in `webhook_audit` |
| **Orders** | YES — `orders` | N/A (cancel = §control) | Read API; **cancel-order operator API (does not exist)** |
| **Positions** | YES — `trades WHERE status∈(OPEN,PARTIAL,…)` (no `positions` table) | N/A (exit = §control) | Read API; **manual square-off API (does not exist)** |
| **Holdings** | PARTIAL — **no `holdings` table**; CNC via `gtt_state` (broker-authoritative) | N/A | Read API; live holdings need broker session (not in DB) |
| **Strategy Status** | YES — `strategies/control.py` resolver + `config/strategies/*.yaml` + `strategy_metrics` | **NO live control** (enable/disable = YAML+restart) | Read API (reuse `strategy_status.py`); **live enable/disable/pause API + hot-reload (do not exist)** |
| **Risk Monitoring** | YES (derived) — `fm_ledger`, `kill_switch_state`, `recent_trade_pnls()`, `trades.risk_amount` | N/A | Read/compute API (no dedicated risk table) |
| **Capital Utilization** | YES — `fm_ledger`, `capital_snapshot`, `/metrics.capital_deployed_pct` | N/A | Read API (mind W10 cost-double-count in `get_daily_realized_net_pnl`) |
| **Exposure** | YES (derived) — open `trades` (`margin_reserved`, `actual_position_value_rs`) + `capital_snapshot.margin_used` | N/A | Aggregation API (no exposure table) |
| **Daily P&L** | YES — `trades.net_pnl`, `fm_ledger.RELEASE_USED.pnl_delta` | N/A | Read API |
| **Service Health** | YES — `healthcheck_server /health` + `cron_heartbeat` + Control Tower | Restart = §control (systemd) | Reuse :8080; **systemd `is-active` needs VM shell exec, not a listener** |
| **VM Health** | PARTIAL — disk historical (`system_metrics`); **CPU/RAM NOT persisted (psutil absent)** | N/A | Live RAM/CPU probe (needs psutil or shell); currently -1.0 |
| **Logs Viewer** | YES — 3 JSON-lines files (`system/trades/reconciler_*.log`) + plain `debug_*`/`cron-*` | N/A | File-tail/parse API; no structured store (files only), no rotation-index |
| **Audit Trail** | YES — `reconciliation_log`, `system_events`, `webhook_audit`, `telegram_alerts`, `control_tower_findings` | N/A | Read API across audit tables |
| **Alerts** | YES — `telegram_alerts` (delivery log) + sentinel flags `data_store/critical_alert_*.flag` | **Outbound only** (no inbound Telegram) | Read API; alerts are one-way (no ack channel except Control Tower CLI) |
| **Slippage Monitoring** | YES — `trade_slippage_log` (+ `order_execution_log`) | N/A | Read API (tier not persisted — W12) |
| **Execution Monitoring** | YES — `orders`, `order_execution_log`, `trades.*_ms`, `reconciliation_log` | N/A | Read API |
| **Trade Statistics** | YES — `trades`, `trade_excursions`, `strategy_metrics`, `innings` | N/A | Read/aggregate API |
| **Daily Reports** | YES — `reports/output/daily_trade_review_report_<date>.xlsx` @16:07 | N/A | File-serve/list API; **no retention (grows unbounded)** |
| **Config view** | YES — `config_snapshots.config_json` (DB-pure) + `config/*.yaml` | Edit = §control (YAML+restart) | Read API (reuse W0 snapshot); **live config-edit API (does not exist)** |

**Cross-cutting gaps for ANY GUI:**
1. **No push transport** — event bus is in-process only; the GUI must poll SQLite (WAL-safe read-only) or the in-process `/metrics` (up only while the trader runs). §4.1.
2. **No live LTP outside the trading process** — GUI needs its own broker session for live prices. §4.2.
3. **Control plane barely exists** — only *clear-kill-switch* (CLI) and *restart* (systemd) are operator surfaces; pause/enable-disable/cancel/square-off/trigger-halt all need **new APIs**, and config/strategy changes need **restart (no hot reload)**. §3.
4. **No TLS/reverse proxy, both HTTP servers plain-HTTP on `0.0.0.0`; :8080 is unauthenticated.** §1.4, §1.2.
5. **Health/VM metrics partial** — CPU/RAM not persisted (psutil absent); systemd/service liveness needs a VM shell, not a DB read. §5.3/5.4.
6. **Night browser access is NOT blocked** by the 18:00–08:00 time-lock (that guards scp/sftp/rsync only). §6.4.

---

*Investigation-only. The single substantive write is this file. Companion memory-file updates (mempalace, SYSTEM_MAP GUI stub, PATHS pointer) accompany it. No code changed, no push, no VM mutation.*
