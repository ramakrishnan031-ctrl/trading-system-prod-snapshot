# Full-Repo Deep System Audit — 04 July 2026

**Scope:** Entire production codebase of the trading system (`core/`, `broker/`, `capital/`, `orders/`, `signals/`, `screening/`, `strategies/`, `sr_detector/`, `data/`, `alerts/`, `utils/`, `reports/`, `scripts/`, `ops/`, `ops_dashboard/`, `main.py`, `config/`, `deploy/`, git hygiene). `tests/` and vendored `.venv`/`node_modules` excluded.

**Method:** 11 parallel deep-read audit agents, one per module group, each reading its files in full and verifying every claim against source with `file:line` evidence. Findings below are de-duplicated across agents, separated into NEW vs KNOWN, and ranked by severity. Paper/live parity and "fail fast and loud" were permanent lenses throughout.

**Headline:** **No CRITICAL findings** (nothing is losing money or leaving a naked position *right now* under normal operation). But there is a **recurring and serious theme: four independent safety mechanisms are silently dead** — three from a copy-paste `broker_order_id` column that does not exist, one from a wrong file path, plus several designed-in "backstops" that are unwired. These are latent until their trigger condition (an emergency exit, a token revocation, a warm restart) arrives — and then they don't fire. Severity counts (NEW): **13 HIGH · 47 MEDIUM · ~55 LOW · plus INFO/hygiene**.

---

## ★ Cross-cutting theme: dead safety nets (read this first)

Several of the HIGH findings share one root cause and one shape — a protection that *looks* active, has tests that pass, and does nothing in production:

| Mechanism | Why it's dead | Finding |
|---|---|---|
| FIX-190 Bug E: cancel resting SL/TGT before emergency flatten | Queries non-existent column `orders.broker_order_id`; error swallowed | H-1 |
| FIX-132: BreakevenManager milestone SL advance | Same non-existent `broker_order_id`; also entirely unwired | H-6 |
| FIX-013: ShadowTracker TGT recalc-from-fill | Wrong column `strategy_name` (real: `strategy`) | H-8 |
| FIX-062: invalidate dead broker token on auth-fail | Wrong path (CWD vs `data_store/session/`) → restart-loop guard inert | H-11 |
| FIX-049: candle late-tick discard | `on_tick` never reads its `ts` param; also an off-by-one landmine | H-9 |
| FIX-134 Item 39: SlBreachMonitor tick-level SL backstop | Never wired into main.py | M-scripts/orders |

**Common failure mode:** a schema/path/wiring drift, masked by a unit test that mocks the very thing that's broken (a `_MockStore` faking `fetch_one`, a mode that never passes the real timestamp). **Recommended systemic fix:** add schema-backed integration tests for every raw-SQL money path (a test that runs the query against the real `schema.sql` would have caught all three column bugs immediately), and a "wired-in" assertion for each documented safety layer.

---

## HIGH severity (NEW)

### H-1 — FIX-190 Bug E is dead code: `_cancel_trade_resting_exits` queries a non-existent column
- **File:** `orders/order_placer.py:3613`
- **Evidence:** `SELECT broker_order_id FROM orders WHERE trade_id = ? AND leg IN ('SL','TGT') AND status NOT IN (...)`. The `orders` table PK is `order_id` (`core/schema.sql:271`); there is no `broker_order_id` column and no migration adds one.
- **Problem:** sqlite raises `OperationalError` on every call; the `except` at :3626 logs a WARNING and returns. So the pre-flatten cancellation of resting SL/TGT — added specifically so an emergency flatten doesn't leave orphan exit legs — never runs. When `_emergency_market_exit` fires for a trade with a live SL/TGT at the broker, those legs survive the flatten and can fill against a now-flat book → **naked reverse position**. Compounded by H-2 (the OCO-sibling cancel at fill time is also skipped).
- **Fix:** `SELECT order_id ...`; add a schema-backed unit test.

### H-2 — Emergency-exit fills can never cleanly close the trade (EXITING → "double close" early-return)
- **File:** `orders/order_placer.py:3732`, `:2211-2217`; `orders/order_manager.py:536-542`
- **Evidence:** `_emergency_market_exit` marks the trade `EXITING`, then registers the exit fill; when it arrives, `_handle_exit_fill → close_trade` raises for any status outside `(OPEN, PARTIAL)`, and the handler catches it as `exit_fill_already_closed` and returns.
- **Problem:** The trade is `EXITING`, not `CLOSED`, and capital was **not** released. The early return skips `_cancel_oco_siblings`, `release_used`, `PositionClosed`, and cost/PnL recording. The trade is finalized only by `_check_stuck_exiting` ~30 min later (costs=0.0). For up to half an hour: capital stays locked, the realized loss is invisible to **both** daily-loss mechanisms (it also drops out of the B-1 unrealized-MTM set), and resting siblings stay live (H-1). This is the designed-in outcome on **every** emergency exit that fills, not a race.
- **Fix:** allow `close_trade` to accept `EXITING`, or have `_handle_exit_fill` distinguish EXITING from CLOSED and run the full close+release.

### H-3 — `_retry_limit_triple_exits` has no status/existing-SL guard → fresh SL+TGT on a closed or already-protected trade
- **File:** `orders/order_placer.py:3321-3371`, `:3217-3225`
- **Evidence:** goes straight to `place_deferred_exits` from a snapshot; unlike `retry_tgt_for_trade` (which re-checks status + live-SL + idempotency) it re-reads nothing, and `_pending_exit_retry` entries never expire.
- **Problem:** After an LTP-validation failure, G5b places a recovery SL within ~15-30s. A later tick then fires the stale retry: if the trade is still open → a second SL (duplicate-SL/RAMCOIND class; a stop-hit inside the one-cycle dedupe window double-fills → oversell); if the trade has since closed → SL+TGT placed for a flat position → **naked reverse**.
- **Fix:** re-read the trade, bail unless OPEN/PARTIAL, skip the SL leg if a non-terminal SL exists; expire stale queue entries.

### H-4 — HARD_KILL retry loop can double-sell after an ambiguous first exit
- **File:** `capital/kill_switch.py:1218-1246` (vs first pass at `:1099-1101`)
- **Evidence:** the first flatten pass is broker-net-aware (`determine_close_direction`); the retry loop uses a binary `_is_position_flat` check then re-fires the **stale full qty** captured at first-pass time.
- **Problem:** if the first exit raises *after* transmission (BrokerTimeoutError — the A-2 ambiguity class, fixed in order_placer but not here) and partially fills, the position is non-flat, so the retry fires the full qty again → oversell → new naked reverse. The FIX-190 hardening that protects the first pass was never applied to the retry path.
- **Fix:** re-derive `(close_side, close_qty)` via `determine_close_direction` on every retry attempt.

### H-5 — HARD_KILL broker-position sweep hardcodes `intent="INTRADAY"` → sweeping a CNC position opens a naked MIS short
- **File:** `capital/kill_switch.py:1166-1174`
- **Evidence:** the FIX-181 sweep does `place_order(..., intent="INTRADAY", tag="ks_hard_kill_sweep")`.
- **Problem:** Kite nets positions per product. An orphan CNC position swept with an MIS exit does not offset it — the CNC position remains **and** a fresh MIS short is created. The first pass explicitly fixed this (Bug C, product-aware); the sweep reintroduced the hardcode. Narrow today (delivery mostly coerced off) but Slice 2.5 CNC is live-capable.
- **Fix:** read `product` from the broker position payload, map via `_PRODUCT_TO_INTENT`, fall back to INTRADAY only when absent.

### H-6 — CNC GTT exit-day is broken end-to-end (held-qty double-count → false F6 → row retired → trade never finalized)
- **File:** `orders/cnc_gtt_monitor.py:353-361`, `:383-386`, `:475-481`, `:491-510`
- **Evidence:** `held[sym] = held.get(sym,0) + abs(int(qty))` for CNC positions; a same-day CNC SELL (what a fired GTT produces) is a **negative** CNC position, so `abs()` *adds* it instead of netting to 0.
- **Problem:** on the day a GTT fires and fully exits, `held > 0` → the ladder takes `_reprotect` (false F6 CRITICAL) instead of `_finalize_gtt_exit`. `_recreate` flips the row to TRIGGERED **first**, then `place_for_fill` fails the C8 straddle (LTP is now beyond the fired trigger by definition), so the row lands in `needs_review` and `get_active_gtt_states()` never returns it again. Net: **trade never closed via GTT_EXIT, `release_used` never runs, delivery capital stays reserved**, and the operator gets a misleading CRITICAL. (Three chained findings.)
- **Fix:** net signed CNC day-positions (clamp ≥0); recompute F6 triggers around current LTP (or emergency-flatten the residual); make `_recreate` failure keep the row ACTIVE so the ladder retries.

### H-7 — Per-strategy position cap is TOCTOU-racy in all three pipeline paths
- **File:** `signals/signal_processor.py:843-853` (also `:1540-1551`, `:1854-1863`)
- **Evidence:** the per-strategy open-count check reads only OPEN/PARTIAL, runs **outside** `fm.portfolio_lock`, and has no in-flight compensation (the global cap gets `processor_in_flight_count`).
- **Problem:** a Chartink burst (one webhook → N symbols → up to 5 workers on the same strategy) lets every worker observe the same stale count and all pass — a configured cap of 2 becomes 5 concurrent positions. A live-money risk cap defeated in exactly its target scenario. The buggy block is triplicated.
- **Fix:** move the check inside `portfolio_lock` and count strategy-scoped in-flight signals (mirror the FIX-018 global pattern); de-duplicate the block.

### H-8 — ShadowTracker FIX-013 TGT recalc is dead (wrong column name)
- **File:** `orders/shadow_tracker.py:317`
- **Evidence:** `"strategy_name" in trade.keys()` — the column is `strategy` (`schema.sql:124`); the same function reads `trade["strategy"]` at `:404`/`:624`.
- **Problem:** always False → the RISK_REWARD TGT-recalc branch never executes → inning-1 `tgt_price` is persisted as the theoretical target, not the fill-recalc'd one, so inning analytics are computed against the wrong target whenever entry slippage occurred.
- **Fix:** `trade["strategy"]`.

### H-9 — FIX-049 candle late-tick protection is dead, with an off-by-one landmine
- **File:** `data/candle_store.py:174`, `:184-196`, `:286-295`; caller `main.py:2140-2142`
- **Evidence:** `on_tick`'s `ts` parameter is never referenced; the caller routes the exchange timestamp into that dead slot and leaves `exchange_timestamp=None`. `_close_candles` stamps `_last_closed_window` with the *new* window (10:01), not the closed one (10:00).
- **Problem:** (a) the entire late-tick discard machinery never runs — late ticks from a closed minute are folded into the wrong candle. (b) Landmine: if anyone wires `exchange_timestamp`, every current-minute tick satisfies `tick_window(10:01) <= last_closed(10:01)` and is discarded → the candle store goes 100% synthetic. A latent naive/aware TypeError (`_Accumulator.update`) detonates at the same moment.
- **Fix:** `_last_closed_window = window_being_closed - interval`; then either wire `exchange_timestamp` (tz-normalized) or delete the dead parameter and guard so the inert net isn't mistaken for active.

### H-10 — `place()` can dereference `result=None` after a 16388 retry on the final attempt → leaked reservation, stuck-PENDING trade
- **File:** `orders/order_placer.py:1201`, `:1219-1235`, `:1335`
- **Evidence:** the 16388 retry `continue` has no bound on `attempt`; if the final loop attempt raises the first 16388, the loop exits with `result is None`, and `:1335 if not result.success` raises `AttributeError` — not a `BrokerError`.
- **Problem:** no `_handle_placement_failure` runs → the trade stays PENDING and the fund-manager reservation is never released (capacity leaked for the session; the A-1/E-1 prepass only mitigates by accident).
- **Fix:** after the loop, `if result is None: _handle_placement_failure(...); raise BrokerError(...)`; or separate the 16388 retry budget from the 429 loop.

### H-11 — `_invalidate_token()` targets the wrong path → FIX-062 auth-restart-loop guard is inert
- **File:** `main.py:127-135` (callers `:144-148`, `:569-571`, `:1107-1108`)
- **Evidence:** `token_path = Path("zerodha_token.json")` (process CWD). The real token is `data_store/session/zerodha_token.json` everywhere else.
- **Problem:** on `BrokerAuthError`, `_shutdown` calls `_invalidate_token` to rename the dead token so the next boot fails fast and breaks the systemd restart loop. The wrong path means it logs "not found, skipping" and leaves the dead token in place — the service can loop on a revoked token exactly as FIX-062 was written to prevent.
- **Fix:** point both paths at `data_store/session/zerodha_token.json` (+`.invalid`).

### H-12 — Paper `get_positions` strips the sign off qty → FIX-190 reverse-aware flatten broken in paper mode
- **File:** `broker/zerodha_adapter.py:1077` (paper) vs `:1108` (live); consumer `broker/position_helpers.py:24-42`
- **Evidence:** paper returns `qty=abs(info["qty"])`; live returns signed qty. `broker_net_qty()` sums `p.qty` assuming signed values.
- **Problem:** a live short nets < 0 → `determine_close_direction` returns BUY (flatten). The same short in paper nets > 0 → returns SELL → **doubles the short**. This is the THELEELA-oversell scenario `position_helpers` exists to prevent, used by the two most safety-critical callers (kill-switch HARD_KILL flatten, order_placer emergency exit). Paper drills can therefore never validate FIX-190. Direct parity violation.
- **Fix:** return signed qty in the paper branch (`qty=info["qty"]`).

### H-13 — TokenMonitor expiry latch never resets → one transient blip permanently disarms token-expiry alerting
- **File:** `broker/token_monitor.py:130-141`, `:150-155`
- **Evidence:** `check_now()` treats any exception from `profile_fn()` as expiry; `_handle_expiry` no-ops when `_expiry_fired` is already True, and the flag is never cleared on a later success.
- **Problem:** a transient network timeout fires a false CRITICAL "TOKEN EXPIRED" + SOFT_KILL and latches. SOFT_KILL auto-clears headlessly; when the token *genuinely* dies later the same day, `_handle_expiry` no-ops — no alert, no soft-kill, and the system keeps attempting entries with a dead token.
- **Fix:** reset `_expiry_fired=False` on a successful check; classify the exception (only fire on `TokenException`-shaped errors, or require 2 consecutive failures) via the in-package `auth_recovery.classify_broker_auth_error`.

---

## MEDIUM severity (NEW) — grouped by area

### Capital & risk accounting
- **M-C1** `capital/fund_manager.py:1589-1606` — rehydrate Phase 2 **double-counts today's realized PnL** on a LIVE mid-day warm restart (broker `net` already includes it), inflating reservable capital until the next `sync_from_broker` (which then fires a spurious drift alert). Paper is correct → undocumented parity divergence. Invisible at 08:15 cold boots.
- **M-C2** `capital/risk_engine.py:385-391,469-475` — **delivery caps lack the FIX-185/Bug-B reservation hardening** the intraday caps got; a concurrent delivery-signal burst can overshoot (the 8-vs-5 mechanism). Inert under `force_intraday_only`; delivery is a supported path.
- **M-C3** `capital/fund_manager.py:2077-2100` + `capital/invariant.py:154-178` — the capital invariant checks only **global aggregates**; a `release_used` under a different bucket than the commit drives one bucket negative and the other positive with the sums balanced → corrupted per-bucket state passes silently, drifting the no-borrow guarantee.
- **M-C4** `capital/kill_switch.py:649-663` — `record_api_failure` holds the KillSwitch lock **through** `soft_kill`'s event publish + Telegram send, so a blocked subscriber or slow send stalls the last-mile order gate (`is_active()`/`current_state()`).
- **M-C5** `capital/fund_manager.py:1026-1050` — `commit_adopted_entry` guard-then-act spans a lock release; a benign duplicate recovery call can raise unknown-reservation `ValueError` → BL-4 fires **hard_kill** (full halt) for a race.
- **M-C6** `capital/position_sizer.py:419-422` — the `max(1, ...)` floor **defeats a zero multiplier**: a zeroed tier/perf weight still trades 1 lot instead of being skipped. Latent (allocator clamps min_weight 0.5).
- **M-C7** `capital/fund_manager.py:1159` — `release_used` **recomputes released margin from the current `leverage_map`**, not the committed amount; a leverage-config edit across a restart leaves `used` permanently drifted.
- **M-C8** `capital/kill_switch.py:550,1189-1214` — `hard_kill` runs its retry loop synchronously on the calling thread (fill handler / reconciler), which can be **frozen up to 2 hours** during an emergency, starving other symbols' fills/exits.

### Order lifecycle & reconciler
- **M-O1** `orders/order_reconciler.py:1743-1744` — `_flatten_broker_position` **double-prefixes the quote key** (`NSE:NSE:SYM`), so `ltp` is always None and every kill-switch / inflight-orphan flatten goes out as a raw MARKET order — the FIX-181 marketable-limit slippage cap is silently inoperative in both modes.
- **M-O2** `orders/order_reconciler.py:1779-1816` — **CHECK4 partial external close never releases capital or records PnL** for the externally-closed portion; the margin stays locked all session and the realized PnL never enters the daily total. No backstop catches it.
- **M-O3** `orders/order_placer.py:3192-3259` — the exit-retry queue leaves a filled position **unprotected with no time-based escalation** while waiting for the first tick; if `live_feed` is None / subscribe fails, nothing retries or escalates (relies on the implicit G5b backstop, and feeds H-3).
- **M-O4** `orders/eod_squareoff.py:1530-1536` — the **EOD phase-2 promotion sweep has no product filter**; a CNC/NRML position on the same symbol as an intraday trade makes a filled LIMIT still read `remaining>0` → a spurious MIS MARKET order → naked reverse in the 15:19 window.
- **M-O5** `orders/eod_squareoff.py:232-254` — `fire_now` leaves the fired-flag set when `_fire` raises (unlike `check_and_fire`), so a partial manual/emergency fire **blocks the 15:17 scheduled squareoff** for the rest of the day (recovers only on restart).
- **M-O6** `orders/cnc_gtt_monitor.py:491-510` — `_recreate` flips the GTT row out of ACTIVE **before** placing; one transient failure permanently retires it from the 15-min ladder → overnight position unprotected on the strength of a single alert.
- **M-O7** `orders/cnc_gtt_monitor.py:436-456` — `_finalize_gtt_exit` **silently skips `release_used`/financials** when entry data is missing (adopted/reconstructed trades) — trade CLOSED, delivery capital stays reserved, no ERROR/alert.
- **M-O8** `orders/smart_tgt_manager.py:543-553` — the **missing-CO-row failure path never escalates** (increments the counter but never calls `_maybe_fire_critical`), so a trail-dead CO trade goes unnoticed.
- **M-O9** `orders/slippage_recorder.py:198-201` — trailed-SL exits produce **bogus slippage rows** (`sl_initial` used as the reference vs the trailed trigger), corrupting the Phase-2/3 tolerance calibration data.

### Signals, screening, webhook
- **M-S1** `signals/signal_processor.py:916-951` — the FIX-067 fresh-quote path places at the fresh entry but **anchors SL/TGT/sizing/reservation to the stale price** (recomputed SL is discarded), so SL distance can drift outside bounds, R:R skews, and reserved capital ≠ order value.
- **M-S2** `signals/webhook_receiver.py:655-661,700-715` — a QUEUE_FULL **poisons the dedup cache**: the entry is written before the queue push and not rolled back, so the 503 "retry later" retry is dropped as DUPLICATE within the 300s window — backpressure recovery is impossible.
- **M-S3** `screening/step_executor.py:62-70,106-123` — the per-step timeout is **broken by construction** with the shared single-worker pool; one hung step serializes all concurrent screening and never recovers, and `latencies_ms` measure queue-wait not step time.
- **M-S4** `screening/secondary_screener.py:332-353` — screening **steps 1 & 3 are structurally always 0.0** (ATR and 20-day volume never populated); 25 of 100 score points are permanently unreachable, so the "min_score 60" floor is really 60-of-75, and every signal shows those steps as REJECTED noise.
- **M-S5** `signals/signal_processor.py:651-671` vs `:1439-1495`/`:1776-1810` — the **shadow-tracker re-entry guard exists only in `_process_one`**; the gate and retest resume paths skip it, allowing overlapping real+simulated positions after a wait.
- **M-S6** `screening/retest_monitor.py:339-368` — **RetestDiverter double-entry**: an exception after a successful `register()` returns False, so `_process_one` also places the original order; on retest confirm a second entry fires for the same signal.
- **M-S7** `signals/signal_processor.py:360-387` — the rate-limiter pre-check **busy-spins on requeue** (no backoff, can spin past the 60s expiry) and consumes an "order" token per dequeued signal including the ~82% later rejected, roughly halving real order throughput under burst.
- **M-S8** `signals/webhook_receiver.py:391-396,773-801` — every unauthenticated/rejected request still performs a **synchronous sqlite INSERT** into the trading DB, contending the single-writer lock the order path uses (a distributed flood amplifies this on the 0.0.0.0 bind).

### Core, data, reports, alerts
- **M-K1** `core/state_store.py:2749-2756` — `get_today_closed_pnl` keys on `DATE(updated_at)` not exit date; a later touch of a closed row produces a spurious P&L-reconciliation variance for two days.
- **M-K2** `core/config_auditor.py:499-501` — the G5 per-strategy-window audit is **dead code** (`getattr(s,"entry_start")` vs the real `entry_start_time`) → always None → never fires.
- **M-K3** `core/db_connect.py:93-111` — `connect()` (the mandated raw-script path) does **not enable `PRAGMA foreign_keys` or `synchronous=FULL`** on the main DB, so every cron script writes with FK enforcement off and weaker durability than the app.
- **M-K4** `core/time_authority.py:138-150` — `configure()` **silently wipes previously-registered skew callbacks** (unconditional assignment, `None` defaults); a second call disarms the HALT-tier soft-kill.
- **M-K5** `core/config_snapshotter.py:70-71` — the full resolved config is persisted **unredacted**, so if the sanctioned dev SMTP-password fallback is ever used, the plaintext password lands in `config_snapshots.config_json` (durable, in backups).
- **M-K6** `core/migrations.py:127-150` — the DDL block extractor is **blind to comments/string literals**; a future stray `(`/`;` in a comment silently corrupts the DDL a migration executes on live tables.
- **M-D1** `data/candle_store.py:73` / `live_feed.py:290` — candle `volume` is semantically wrong (Kite `volume_traded` is cumulative, summed per tick) and is always 0 under MODE_LTP; corruption goes live if any token is switched to MODE_FULL.
- **M-R1** `reports/daily_report.py:103-105` — the **legacy holiday guard is broken** (dict-format YAML entries never match); the report generates and can Telegram on NSE holidays. Use `utils.holiday_guard`.
- **M-R2** `reports/daily_report.py:469` (+`:901,1240,1500`) — legacy P&L **excludes `CLOSED_MANUAL` everywhere** (`status=="CLOSED"` only), understating P&L on any EOD-close day and skewing the bake-in comparison.
- **M-R3** `reports/daily_trade_review.py:1048-1054` — Reconciliation **Block-4 false FAIL** on overnight CNC closes (ledger keyed by close date, trades by `created_at`) → red "capital corruption" banner on a benign date seam.
- **M-R4** `reports/daily_trade_review.py:1019-1027` — Reconciliation **Block-2 "ORDER" identity is a tautology** (RHS derived from LHS) → structurally cannot FAIL, presents as verified while verifying nothing.
- **M-A1** `alerts/telegram_notifier.py:732,745` — HTML truncation can **split an escaped entity or `<b>` tag** → Telegram 400 → treated as permanent → long CRITICAL alerts lost on all chats (no 4xx body logged).
- **M-A2** `alerts/telegram_notifier.py:91-102,534-592` — alert sends are **fully synchronous in caller threads** (busy-wait acquire + ~26s retry ladder), and `_on_reconnect` sends from the **kiteconnect ticker thread**, so an alert storm blocks reconnection and tick distribution.
- **M-U1** `utils/startup_checks.py:1525-1535` — the scanner-connectivity preflight is **skipped on COLD startups** — i.e. every normal trading morning — so it effectively never runs.

### Cross-cutting & scripts
- **M-X1** duplication: two parallel Decimal **tick-rounding implementations** (`orders/price_math.py:30` vs `broker/slippage_engine.py:147/161/175`); SL-trigger snapping (a money path, via `smart_tgt_manager.py:747`) uses the copy. Outputs agree today; drift risk on any tick-policy change.
- **M-X2** silent-swallow on money paths: `orders/sl_breach_monitor.py:196`, `orders/order_placer.py:3788`, `orders/breakeven_manager.py:379` all `except Exception: pass` around the **emergency-action notifier send** — a revoked token makes an emergency close invisible to the operator.
- **M-SC1** `scripts/eod_verify.py:126-151` — on **ISSUES_FOUND (open positions overnight)** the critical job exits 0, writes a SUCCESS heartbeat, and only alerts at INFO; the Cron Officer sees a healthy job while a real EOD breach is downgraded.
- **M-SC2** `scripts/generate_screened_stocks_csv.py:246` — points at a **non-existent DB** (`data/state.db` vs `data_store/trading_system.db`), writing an empty headers-only CSV every trading day since the FIX-039 rewrite while reporting SUCCESS.
- **M-SC3** `scripts/eod_cleanup.py:87,96` — the stale-signal reaper filters on `status='IN_PROCESS'`, which is **never persisted** (real status is `PROCESSING`) → dead code, stuck signals never cleaned.
- **M-DP1** `deploy/post-receive:23-25` — this tracked hook references **stale pre-`/systems/` VM paths**; if it is (or is re-installed as) the live hook, a push deploys into a directory neither cron nor systemd runs from — a silent no-op "successful" deploy. Reconcile against `deploy/hooks/post-receive`.

---

## LOW severity (NEW) — condensed

**Correctness / latent:**
- `core/state_store.py:348-376` — schema-version guard cannot detect a **newer** DB; booting v41 code against a v42 DB silently stamps the version *down* to 41 instead of failing fast (relevant with P1/v42 unpushed). *(borderline HIGH; listed here as the trigger requires a rollback/branch mixup)*
- `broker/order_monitor.py:1096-1115` — fill-timeout cancel-failure marks FAILED+orphan without re-checking whether the order just filled (backstop catches it).
- `broker/order_monitor.py:899-929` — immediate-cancel of a PARTIAL entry publishes stale `filled_qty`; shares filled in the race window are briefly unprotected.
- `broker/zerodha_adapter.py:911-917,984-990` — 429 backoff bypassed on `cancel_order`/`modify_order` (blanket except returns success=False without penalize/reset).
- `broker/order_monitor.py:652-678` — client-side `BrokerRateLimitError` counts toward the HARD_KILL circuit breaker (self-inflicted pacing can escalate to flatten-all).
- `broker/zerodha_adapter.py:886-891,2111-2136` — paper cancel overwrites a COMPLETE order to CANCELLED / can fill after a "successful" cancel; `_synth_fill` corrupts avg_price on scale-in and books zero PnL on a reversal (paper capital drift).
- `capital/kill_switch.py:315-325` — `auto_clear_scheduled_kill` open-position gate ignores EXITING trades; `:222-224,773-775` — an unparseable `triggered_at` makes a prior-day kill immortal (silent no-trade day).
- `orders/order_placer.py:1606-1618` — `_on_order_filled` claims the fill via `get()` not `pop()` (non-atomic; unreachable today with one poll thread).
- `data/live_feed.py:202-223` — the weakref callback registry makes `_tick_dispatcher` a refactor landmine (survives only because `_main_locked` never returns).
- `utils/instance_lock.py:90` — SO_REUSEADDR defeats the socket lock on Windows (prod Linux safe); stale recycled-PID gives a false "another instance running".
- `scripts/fetch_daily_candles.py:239` — `sqlite3.IntegrityError` referenced without a module-level import (latent NameError, masked by INSERT OR IGNORE).

**Fail-quiet / escalation gaps:**
- `alerts/cron_alerts.py:33-41` — a registry read error silently downgrades a critical job's FAILED alert to ERROR (no email escalation).
- `utils/cron_heartbeat.py:157-162` — a holiday-calendar error degrades to a weekday-only check → jobs/alerts can run on a mid-week NSE holiday.
- `utils/startup_checks.py:1408-1411` — FIX-151 "block TEMP config when capital>25K" is documented but not implemented (TEMP values ride into live with only a warning).

**Security / hygiene (LOW):**
- `ops_dashboard/backend/config/gui_config.local.yaml` — the **production TOTP seed + password hash sit in the dev working tree** (git-ignored, but a real prod-credential copy; regenerate the seed and keep it VM-only). *(→ see recommendations)*
- `ops_dashboard/backend/app.py:86` — ephemeral Flask `secret_key` logs the operator out on every restart.
- `ops_dashboard/backend/auth.py:98-102` — username-keyed lockout enables a trivial account-lockout DoS on the tailnet; `:62-64` — `verify_totp` returns True on an empty secret (fail-open 2FA).
- `signals/webhook_receiver.py:466,538,255-266` — post-auth malformed input → 500 + error-echo + CRITICAL log spam; symbols are unvalidated strings (log-line forgery via `%s`); `/health` has no auth and bypasses the rate limiter.
- `signals/webhook_receiver.py:79-97` — `_PerIpRateLimiter` grows past `max_ips` unbounded with O(n) eviction under the request lock (degrades under the flood it targets).
- `scripts/fetch_daily_candles.py:189` — prints the first 8 chars of the live access token to the cron log.
- `main.py:2752-2784` — SIGINT window before signal handlers are installed; `:753-755,2837-2844` — `_gate_release_pool` and the healthcheck server are never shut down.
- `reports/output/daily_report_b64.txt` — a 1.44 MB base64-XLSX committed to bypass the `*.xlsx` ignore (embeds trade data); `audit-1.jpg` — stray untracked binary in the repo root.

**Consistency / duplication (LOW):**
- Terminal-status sets diverge across cancel paths (`order_placer.py:4277` vs `order_reconciler.py:1367`); marketable-limit flatten logic duplicated ×3 (eod / structure-exit / reconciler); exit-leg persist+track machinery duplicated ×4 in order_placer.
- IST offset re-implemented in ~8 production sites instead of `time_authority`; naive `datetime.now()` derives the trade date in `fetch_daily_candles.py:173` and `sr_detector_backfill.py:112` (wrong day on a non-IST host).
- Strategy `entry_start_time: 09:25` vs global `entry_start: 10:00` (cosmetic; global floor binds).
- Reconciliation-sheet win-rate denominator (`daily_trade_review.py:2025`) contradicts the "decided" denominator the notes claim it matches; `daily_trade_review` has no non-trading-day guard.
- `strategies/control.py` `strategy_will_trade` fails **open** on an unrecognized `trade_type` (mitigated by the config_loader validator).

---

## KNOWN issues re-confirmed (already tracked, not re-litigated)

- **D-1** close_trade double-release race — `order_manager.py:533-542` (guard) vs `:587-616` (unguarded UPDATE); CHECK1 vs `_handle_exit_fill` on the same broker close. Design ready.
- **W10** `get_daily_realized_net_pnl` double-subtracts costs (bundled into B-2). Reports avoid it deliberately.
- **RMS/manual/GTT closes pass `costs=0.0`** into `release_used` (`order_reconciler.py:1102`, `cnc_gtt_monitor` finalize, `state_store` manual-close financials).
- **C-2** webhook network posture (`bind_host: 0.0.0.0` + `require_hmac: false`) — accepted pending Phase-3. Webhook **auth itself is timing-safe** (`hmac.compare_digest`, runs before parse/queue) — verified correct.
- **eod_verify.py:60-68** P&L-variance check queries wrong columns (`system_net_pnl`/`broker_net_pnl` vs real `system_pnl`/`broker_pnl`) → branch silently dead.
- pending_rr_cancel logs ERROR without escalation; a rejected last-line emergency exit only SOFT_KILLs; CO bracket SL is broker-managed (CHECK1 sole backstop); in-memory-only state (in_flight / _fill_map / _pending_exit_retry); S10 strategy cross-check dead; `reconcile_pnl.py` dead/unscheduled; `t2_cnc_gtt_realtest.py` 3 drifts; W1/W2/W3/W5/W6/W9 report placeholders (deliberate honesty markers).

---

## Verified-clean areas (no findings)

- **Layering** — `core/` imports nothing upward; `sr_detector/` is self-contained (only `sr_detector.*` + stdlib); `ops/` imports only `core.*`/`ops.*` (read-only aggregator role intact).
- **ops_dashboard web-app security** — every `/api/*` route behind `@login_required` **plus** a defense-in-depth `before_request` guard; cookies HttpOnly+SameSite=Strict; DB genuinely read-only (`mode=ro`); log-reader path-traversal defenses hold (basename + realpath `commonpath` recheck); reports download gated off; XSS-safe (Alpine `x-text`, no `x-html`/`|safe`); loopback bind enforced in code; no `debug=True`.
- **Control Tower** — writes only its own bookkeeping tables; never touches trades/orders/capital; auto-resolve guard, severity maps, non-trading-day skip, and HTML-escaped report all sound.
- **Git hygiene** — `credentials.xlsx` confirmed absent; no real secrets tracked; canonical crontab == `generate(registry)` byte-for-byte; EOD job ordering intact; all 15 strategy YAMLs consistent (names, R:R 1.5, min_score, entry_end); trading-hours values agree across files; 2026 holiday list complete through year-end.
- **v28 two-DB ATTACH discipline** — every raw-sqlite site touching analytics tables routes through `core.db_connect.connect`; raw survivors query main-DB tables only.
- **P&L/charges math** — single Decimal authority (`broker/cost_calculator.py`); the reports fallback uses the same rate keys; no conflicting third implementation.
- **Cron heartbeat / exit-code discipline** — sound across the entrypoints; `backup_retention.py` exemplary on deletion safety.

---

## Recommended remediation order

1. **Emergency-exit chain (H-1, H-2, H-3, M-O1)** — the highest-leverage cluster: an emergency flatten today can leave live exit legs, fail to release capital for ~30 min, place fresh exits on a closed trade, and always fall back to raw MARKET. Fix the dead column, the EXITING close path, the retry guards, and the quote double-prefix together, with a schema-backed integration test.
2. **HARD_KILL correctness (H-4, H-5)** — the flatten-all path can double-sell / open a naked MIS short. Fix before any scenario that could trigger a hard kill on a short or CNC position.
3. **CNC GTT exit-day (H-6)** — delivery is live-capable; the exit-day path never finalizes. Fix before enabling delivery beyond shadow.
4. **Dead safety nets (H-8, H-9, H-11, H-13, M-SC1, M-SC2)** — each is a protection that reports healthy while doing nothing; cheap fixes, high assurance value. Add the systemic "wired-in + schema-backed" test recommended above.
5. **Per-strategy cap race (H-7)** and **paper parity (H-12)** — a defeated risk cap and a parity gap that blinds paper testing of the flatten path.
6. **Reporting integrity (M-R1..R4)** and **capital-accounting MEDIUMs (M-C1..C8)** — no immediate loss, but they distort the numbers Rama trades on and the safety-net behavior around restarts/emergencies.
7. **LOW hygiene / security** — dashboard TOTP-seed relocation, the deploy-hook path reconciliation, the token-in-log print, and the duplication consolidations.

*Generated by an 11-agent full-repo audit, 04 July 2026. Every finding cites verifiable `file:line` evidence; claims were verified against source, not inferred.*


-----
-----

VS Code Claude Final reply after audit is over:

Now updating memory to mark the audit complete.

The full-repo audit is complete. All 11 module groups were read in full and the consolidated report is written to docs/audit/full_system_audit_04july2026.md.

What happened

The first attempt (last session) launched all 11 agents in parallel and burned the API session limit twice with zero reports returned. This time I ran them strictly one at a time — launch one, collect its full report, launch the next — resuming each interrupted agent from its preserved context. That worked cleanly through all 11, and I've saved a permanent memory rule to never parallel-fan-out again.

The result

No CRITICAL findings — nothing is losing money or going naked right now under normal operation. But 13 HIGH, 47 MEDIUM, ~55 LOW, and one theme dominates the HIGHs:

Four safety mechanisms are silently dead — they look active, their unit tests pass, and they do nothing in production:
- Two dead-column bugs (orders.broker_order_id doesn't exist; the real PK is order_id) — kill FIX-190's pre-flatten exit cancel and FIX-132's breakeven SL advance. This is the same class as the tag bug you found on 01-Jul.
- A wrong-path bug — _invalidate_token() looks in the process CWD, not data_store/session/, so FIX-062's auth-restart-loop guard never fires; the service can loop on a revoked token.
- A wrong-column bug — ShadowTracker's TGT recalc keys on strategy_name instead of strategy.
- Plus FIX-049's candle late-tick discard (dead, with an off-by-one landmine) and two backstops (SlBreachMonitor, BreakevenManager) never wired into main.py.

The other HIGH clusters: the emergency-exit path can leave live exit legs after a flatten, lock capital for ~30 min while hiding the loss from both daily-loss gates, and always falls back to raw MARKET (the FIX-181 cap is defeated by a NSE:NSE: double-prefix); the HARD_KILL path can double-sell or open a naked MIS short when sweeping a CNC position; the CNC GTT exit-day never finalizes; the per-strategy position cap is race-defeated by a Chartink burst; and paper mode can't validate the reverse-flatten because it strips the qty sign.

The common root cause is schema/path/wiring drift masked by tests that mock the very thing that's broken. My top systemic recommendation, in the report, is schema-backed integration tests for every raw-SQL money path plus a "wired-in" assertion per documented safety layer.

Verified clean: layering, the whole ops_dashboard web-app security surface, Control Tower's read-only guarantee, git hygiene (credentials.xlsx confirmed gone, no tracked secrets, crontab matches the registry), the v28 DB discipline, and the webhook's timing-safe auth.

The report ends with a 7-step recommended remediation order. The natural next step is a batched fix-plan — say the word and I'll draft one (starting with the emergency-exit chain), one agent at a time.

