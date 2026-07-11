# V3 SHARED-ENGINE — PHASE 0: INVESTIGATION & GAP ANALYSIS (READ-ONLY)

**Date (IST):** 11-Jul-2026 · **Baseline:** `main == origin/main == bare HEAD == 8116b74`, **schema v42 owed at boot** (live still v41 until Mon 13-Jul 08:15) · **Mode:** READ-ONLY (no code, no commits, no VM change) · **Method:** every premise verified against actual source (file:line cited); unconfirmable items marked **UNKNOWN**.

> Purpose: establish the factual baseline of the ACTUAL live system and map each existing component to the new V3 Shared-Engine modules (Common Utilities + 03.01–03.09), tagging each **ADAPT** (modify existing — anti-duplication R2) or **NEW** (net-new, no similar file exists). This report DRIVES every subsequent build step. Self-contained for relay to Web Claude + ChatGPT.

---

## 0. LEDGER / STATE NOTE (read first)

- Tree is **clean, nothing unpushed** except the standalone **T2 delivery-proof branch `854112b`** (same-day CNC canary PASSED 10-Jul; DDPI overnight leg still unproven — deferred, not part of main).
- **`8116b74` (trade_type Option A) + `61ae9cc` (min_pass_score 60 / max_consecutive_losses 4) both first LOAD at MON 13-Jul 08:15 auto-boot.** So the live *running* process (pre-Monday) is on the prior config; the *source* (this investigation's basis) already reflects both. Behavioral, not a code action.
- Numerous default-OFF feature branches exist unpushed (S&R V2 Phase A/B, MIS-filter, B-1 unrealized-MTM, etc.). None are in `main`. This report maps **`main`@`8116b74`** only.

---

## A. FINDINGS PER INVESTIGATION AREA (A–H)

### A. CONFIG STRUCTURE — single-source, Pydantic-validated, a few hardcoded thresholds
- **Loader:** `core/config_loader.py::load_all(config_dir) -> AppConfig` (L1634). No singleton, no hot-reload; every model is `extra="forbid"` (typos rejected at boot). Loads 8 YAMLs, each SHA-256'd. `SystemConfig` (L1216) holds all `system_config.yaml` sections.
- **`config/` inventory:** `system_config.yaml` (master, 30 KB), `scoring_weights.yaml`, `broker_costs.yaml`, `broker_limits.yaml`, `slippage_model.yaml`, `security.yaml`, `accounts.csv` (primary LFL836), `chartink_scanners.yaml` + `scan_webhook_map.yaml` (scanner→strategy routing, 15 scanners = 12 intraday + 3 `positional_*`), `nse_holidays_2026.yaml`, `instruments.csv`, `symbol_aliases.yaml`, `reference_data/`, and `config/strategies/*.yaml` (15 strategy files, loaded separately by `strategies/loader.py`).
- **`system_config.yaml` top-level sections:** `broker, trading_hours, special_sessions, signal_queue, excluded_symbols, force_intraday_only, trade_type, delivery_enabled, product_map, mis_filter, clock, order_monitor, capital, position_sizing, risk, webhook, signal_processor, kill_switch, eod_squareoff, alerts, logging, order_reconciler, eod_reconcile, tgt_retry, shadow_tracker, sr_detector, structure_exit, smart_tgt, entry_gate, slippage_bands, paper, drift_handler, strategy_circuit_breaker, circuit_breaker, live_feed, fno_ban`.
- **Key verified values (main@8116b74):** `force_intraday_only: true` (L70) · `trade_type: INTRADAY` (L77) · `delivery_enabled: false` (L83) · capital split `intraday_bucket_pct 0.70 / positional_bucket_pct 0.30` (L23-24), `conditional_allocation_enabled false` (L25) · `position_sizing.enabled: true`, `tier_multipliers HIGH 1.0 / MEDIUM 0.70 / LOW 0.50` (L57-60), `max_concentration_pct 0.10` (L140), `max_position_value_pct 0.40` (L150) · `risk`: `max_open_positions 5` (L169), `max_daily_trades 10` (L170), `max_open_delivery_positions 3` / `max_daily_delivery_trades 5` (L171-172, inert), `max_sector_exposure_pct 0.40` (L173), `max_consecutive_losses 4` (L174), `daily_loss_limit_pct 0.03` (L175) · `signal_queue.expiry_sec 600` (L55) · `webhook.dedup_window_seconds 300` (L189).
- **Scoring config:** `config/scoring_weights.yaml` — 10 step weights sum to 100 (`volume_surge 15, vwap_position 10, atr_filter 10, rsi_range 10, price_action 15, sector_strength 10, time_of_day 5, spread_check 5, circuit_check 10, signal_age 10`), `min_pass_score 60`, `high_score_threshold 80`, `medium_score_threshold 65`. Single source; no weights hardcoded in `.py`.
- **Contradiction auditor:** `core/config_auditor.py::audit_system_config()` (L567) — group A BLOCKs `force_intraday_only=true + trade_type=DELIVERY` (A1, L196) and invalid `trade_type` (A2). Wired into `SystemConfig` model_validator → **fail-fast at boot**.
- **Single-source verdict:** YES for weights/tiers/caps/thresholds via YAML. **Hardcoded exceptions in `.py`** (candidates to externalize in V3): RSI bands `LONG 40–80 / SHORT 20–60` (`screening/step_executor.py:249,251`); time-of-day scoring buckets (`step_executor.py:301,303`); signal-age score tiers `<=30s→1.0 / <=60s→0.5` (`step_executor.py:344,356`); `price_math.py` fallback constants `DEFAULT_TICK 0.05`, `EMERGENCY_EXIT_BUFFER_PCT 0.01`, `DEFAULT_SL_LIMIT_OFFSET_PCT 0.005`, `DEFAULT_CIRCUIT_MARGIN_PCT 0.02` (documented mirrors of config keys, used only when config unwired).

### B. SECONDARY SCREENER / SCORING — score steps AND upstream hard-rejects both exist
- **Orchestrator:** `screening/secondary_screener.py::SecondaryScreener.screen()` (L89). Pipeline order (SS4): MIS-blocklist pre-drop (L114, flag-gated dormant) → fetch quote/build market_data (L134) → **pre-fill circuit-proximity HARD reject** (`_circuit_proximity_reason`, L159/L355) → 10-step executor (L199) → step-error reject → **score** (L236) → per-strategy `min_score` override vs `min_pass_score` (L252) → **signal_age defense-in-depth HARD reject** if `signal_age` step scored 0.0 (L275) → PASSED (L292). Persists a `screener_results` row every path (L461).
- **10 steps:** `screening/step_executor.py::run_all` (L76) — all 10 always run (SE7), per-step timeout 5s→neutral 0.5 (FIX-091), exceptions captured never raised. Each step returns 0.0–1.0.
- **Scorer:** `screening/quality_scorer.py::score()` (L59) — **proportional** (FIX-042): `total = (Σ achieved / Σ weights_present) × 100`, missing steps excluded from denominator. Tier by `high_score_threshold 80 / medium_score_threshold 65` → HIGH/MEDIUM/LOW. `passed = total >= min_pass_score(60)`.
- **CRITICAL for V3 (03.03/03.04 split premise CONFIRMED):** `circuit_check` and `signal_age` are BOTH (a) scored steps (`step_executor` steps 9 & 10, weight 10 each) AND (b) enforced as **binary hard rejects** — circuit via the pre-pipeline `_circuit_proximity_reason` gate + the `circuit_check` step's 0.0; signal-age via the step-7 defense-in-depth reject (secondary_screener L275) on a 0.0 age score. So a "hard gate" battery already exists in spirit but is **entangled inside the scorer/screener**, not a separate pre-scoring gate module. Signal freshness is *also* hard-rejected far upstream at intake (see D).
- **Tier → sizing:** tier string flows `screen_result.tier` → `signal_processor._process_one` passes it as `score_tier` to `position_sizer.calculate()` (`signal_processor.py:887`); sizer applies `tier_multipliers` (see E). Assignment lives in the scorer; consumption in the sizer.

### C. EXECUTION PATHS — software OCO, two intraday protocols, delivery-exempt squareoff
- **Placement spine:** `orders/order_placer.py::OrderPlacer.place` (L769) creates the trade row (PENDING_FILL) → `full_entry_engine.execute` (ENTRY only) → exits deferred to fill (`place_deferred_exits`, `full_entry_engine.py:121`). Router picks protocol by `order_protocol`.
- **Protocols:** `orders/order_protocol_limit.py` **LIMIT_TRIPLE** = ENTRY LIMIT (or MARKET for SNR-V2 retest) + SL stop-limit (`order_type="SL"`, SL-M banned) placed FIRST at actual filled qty + TGT LIMIT (`place_exits` L263). `orders/order_protocol_co.py` **CO_PLUS_TGT** = CO bracket (`variety="co"`, broker-managed inner SL) + separate TGT LIMIT (L162). **DELIVERY** = one broker-side **OCO-GTT** (`orders/cnc_gtt.py`, wired via `full_entry_engine.py:151`).
- **OCO = software** (intraday): SL and TGT are separate broker orders both registered in `OrderPlacer._fill_map` + `order_monitor.track()`; on either fill, `_handle_exit_fill` (L2169) closes the trade (double-close-guarded) then `_cancel_oco_siblings` (L4346) cancels the surviving leg. TGT-retry daemon `orders/tgt_retry_manager.py` re-places a failed TGT without ever making a 2nd TGT.
- **MIS handling:** intent→broker-code via `broker/product_resolver.py::resolve` (INTRADAY→MIS, DELIVERY→CNC, COVER_ORDER→CO); enforced by **two independent locks inside `broker/zerodha_adapter.py::place_order`** — `force_intraday_only` coercion (~L544) + `delivery_lock` (~L558). Relocated here in Option A (`8116b74`).
- **RAMCOIND 4-layer duplicate-exit fix — CONFIRMED:** L1 G5b settling-window + broker-book SL check (`order_reconciler._g5b_crash_recovery_sl`); L2 keystone one-live-SL/one-live-TGT invariant (`_check_duplicate_exits` L2777 / `_dedupe_exit_leg`); L3 CHECK2 SYSTEM_OVERSELL (`_detect_system_oversell` L1557 / `_flatten_system_oversell`); L4 placement after-check flags `DUPLICATE_SL/TGT` (`order_placer._verify_exits_placed`).
- **15:15 vs 15:17:** **15:15 = EOD ENTRY CUTOFF (blocks NEW entries only)** — `core/market_windows.py:31`, enforced `order_placer.place` L970-991. **15:17 = EOD position squareoff** — `market_windows.py:27`, `orders/eod_squareoff.py`. (The string `circuit_breaker_force_close_15:15` in `kill_switch.py:90` is only a scheduled-kill reason label; **no separate 15:15 position force-flat exists** — treat as UNKNOWN/absent.)
- **Kill switch:** `capital/kill_switch.py` — INACTIVE / SOFT_KILL (block entries, allow exits) / HARD_KILL (block all + flatten). Scheduled reasons auto-clear (`auto_clear_scheduled_kill`), HARD never auto-clears; `clear_stale_state` clears prior-day kills at headless boot; manual `resume()`. HARD flatten cancels resting SL/TGT first (`_cancel_trade_resting_exits`, FIX-190 Bug E) then marketable-LIMIT/MARKET flatten (`_exit_all_trades_indestructible`, infinite retry).

### D. CHARTINK INGESTION — receiver with 2-layer dedup + intake expiry
- `signals/webhook_receiver.py::_process_request` (L399): shutdown 503 → per-IP token-bucket rate-limit → HMAC/token auth → scanner-known check → kill-switch 403 → backpressure 503 → entry-window 403 → JSON parse → numeric cast → required fields → parse `triggered_at` (localized IST) → per-symbol `_process_signal`.
- **Dedup (2 layers):** in-memory `TTLCache` keyed `(symbol, scanner_name)`, TTL = `dedup_window_seconds` (**300s**, floored 60s); DB fingerprint `sha256("{scanner}|{symbol}|{floor(epoch/window)}")` with UNIQUE constraint → `DUPLICATE`. Webhook dupes are dropped BEFORE any signal row INSERT.
- **Intake freshness (HARD reject, upstream of scoring):** `_process_signal` computes `age = now - triggered_at`, rejects `EXPIRED` if `age > expiry_sec` (**600s**, `signal_queue.expiry_sec`) before queueing. Two more age gates downstream (defense-in-depth): `signal_processor._process_one` re-checks `>_signal_expiry_sec` (default 60s) → EXPIRED; the screening `signal_age` step uses `<=30/<=60s` tiers. **Note: the task's "90s" freshness figure is not the code value — intake expiry is 600s; screening age hard-zero is >60s.**
- **Routing:** `config/scan_webhook_map.yaml` maps 15 scanners → strategies (12 intraday + 3 `positional_*`). `chartink_scanners.yaml` holds URLs for preflight only.

### E. CAPS / PORTFOLIO — per-trade concentration, FCFS admission, no ranking
- **`max_concentration_pct` is PER-TRADE, NOT shared/aggregate** — only in `capital/position_sizer.py`: `qty_by_concentration = floor(total_capital × 0.10 / entry_price)` (L375). It caps ONE order's notional vs total capital; it does NOT sum existing positions and is not shared across buckets.
- **Only shared/aggregate cap today = sector:** `max_sector_exposure_pct 0.40` via `risk_engine` SECTOR_EXPOSURE (`store.sector_exposure`, counts open + in-flight).
- **Position/count caps:** global `max_open_positions 5` (TOCTOU-hardened via `count_live_reservations`), delivery-scoped `max_open_delivery 3` (inert), daily `max_daily_trades 10`, per-strategy `max_concurrent_positions` (default 2, `signal_processor._enforce_strategy_position_cap`).
- **Per-bucket capital:** `capital/fund_manager.py` — intraday 70% / positional 30% absolute split; `reserve()` consults ONLY the intent's bucket; **no cross-bucket borrowing** (FM3). `resolve_bucket_allocation()` is the deterministic split function.
- **Admission is FIRST-COME-FIRST-SERVED / concurrent** — `signal_processor` dispatcher drains the queue to a 5-worker `ThreadPoolExecutor`, each signal runs the whole pipeline independently. **No candidate set is ever assembled or sorted.** The Telegram "Priority rank: #1 of 1 candidates" text is a hardcoded placeholder (`signal_processor.py:439`). **No ranked/deterministic portfolio allocator exists anywhere** (repo-wide grep negative).
- **One-position-per-symbol:** enforced by `risk_engine` DUPLICATE_SYMBOL + CONTRARY_POSITION, webhook in-flight claim, dedup, and `entry_throttle` per-symbol cooldown.

### F. DELIVERY / SLICE 2.5 STATUS — doubly-locked OFF, GTT protection is the only carve-out
- `force_intraday_only: true` + `delivery_enabled: false` + `trade_type: INTRADAY`. `broker/product_resolver.py` DELIVERY→CNC path EXISTS but unreachable live: `zerodha_adapter.place_order` has two independent locks (force-coercion → MIS, delivery_lock refuses CNC when disabled). Under Option A (`8116b74`) the 3 `positional_*` DELIVERY strategies are **dormant at the control gate** (`strategies/control.strategy_will_trade`) — default now **12 WILL / 3 WON'T** (deliberate).
- **Has any CNC order executed live in the main system? No evidence, and doubly blocked.** (The standalone T2 proof branch ran a same-day CNC canary in an isolated store — not the main system.)
- **CNC lifecycle exemptions:** ONLY the deliberate GTT-protection carve-out — `place_gtt/modify_gtt/delete_gtt/get_gtt(s)/get_holdings` are NOT gated on `delivery_enabled` (R2 guard split) so overnight protection survives disablement; EOD squareoff filters `product in ("MIS","CO")` (CNC exempt); reconciler skips GTT/DELIVERY trades for G5b/CHECK9/duplicate. Since new CNC entries are blocked, GTT ops only ever touch a pre-existing holding. **No other squareoff/reconcile/stale-cleanup CNC exemption exists.** Delivery stays OFF until Slice 2.5.

### G. WATCHLIST / NEXT-MORNING ENTRY — none (overnight stage is NEW)
- `screening/entry_gate.py::EntryGate` (`WatchEntry` L51) is an **intraday same-day price-pullback** watchlist: in-memory, polls LTP, releases on price re-entry `lower<=ltp<=upper` (EG5), hard per-entry timeout (default 180s, EG6), or 3 quote failures (EG9). Persisted to `gate_state` only for **same-session** mid-day restart rehydration; `clear_all()` wipes it at EOD to prevent next-morning stale rehydration (FIX-046).
- **No "analyse-today-trade-tomorrow" / overnight-carry watchlist / next-morning 5-min entry stage exists.** All "overnight/carry" grep hits are CNC delivery *position* carry (GTT), not an analysis watchlist. → **NEW.**

### H. PAPER/LIVE PARITY — shared logic; adapter owns fill simulation
- Paper vs live is isolated to the broker boundary (`zerodha_adapter` synth-fills / `_paper_*` stores) + a few explicit branches: `position_sizer` uses live margin when wired else static `leverage_map` fallback; `order_placer` liquidity check is LIVE-only (L1187); `sl_breach_monitor` places emergency MARKET live / logs only in paper. Screening, scoring, sizing math, risk gates, price_math, sr_detector, kill_switch, control gate, eod_squareoff product filter — all **mode-agnostic** (parity). `mode` is a label/column, not a logic branch, in the pure modules. **No pre-existing paper/live divergence found that would block a V3 shared engine** — the codebase is already built parity-first (a standing PERMANENT rule).

---

## B. GAP-ANALYSIS TABLE (one row per V3 module)

| V3 module | Existing component(s) + EXACT path | ADAPT / NEW | Notes / risks |
|---|---|---|---|
| **Common Utilities** | `orders/price_math.py` (tick/SL/TGT/clamp, pure) · `sr_detector/pivots.py::find_swing_pivots` (pure swing) · `sr_detector/models.py::Candle` · `data/candle_store.py::CandleStore` (stateful live 1m builder) · `core/time_authority.py` (now_ist) · `core/market_windows.py` (sessions/holidays) · `core/logger.py` · `core/config_loader.py::load_all` | **ADAPT** (helpers exist) + **NEW** (ATR + timeframe-resample) | **GAP:** no reusable **ATR/true-range** helper (ATR only ever consumed pre-computed from a market-data dict; ATR-based SL/TGT is explicitly NOT IMPLEMENTED, falls back to FIXED_PCT). No **timeframe-resampling** util (each TF is a separate broker fetch). Build these two NEW as pure helpers. |
| **03.01 S&R Detection** | `sr_detector/` package — `detector.py`, `fetch.py` (TFs day/60m/30m), `pivots.py`, `zones.py`, `confluence.py` (→ HIGH/MED/LOW + evidence + PDH/PDL/PDC anchors + round levels), `flags.py`, `models.py` | **ADAPT** | Already produces per-TF swings + zones + confidence. Currently **enabled but SHADOW/observer-only** (never gates). "Anchors" today = prior-day PDH/PDL/PDC + round numbers (no generic named-anchor object). V3 consumes/extends; do NOT rebuild. |
| **03.02 Market Regime** | *(none — repo-wide grep negative)* | **NEW** | No index-level regime, confidence, or extreme/volatility flag anywhere in source. Build NEW on Common-Utils substrate (candles, `find_swing_pivots`, ATR-NEW, `MarketWindows`). |
| **03.03 Hard Gate** | Logic exists ENTANGLED in `screening/secondary_screener.py` (pre-fill circuit-proximity reject `_circuit_proximity_reason`; signal-age defense-in-depth L275) + `screening/step_executor.py` (`circuit_check` step 9, `signal_age` step 10) + intake expiry `webhook_receiver._process_signal` + R:R / freshness scattered | **ADAPT (extract)** | No standalone pre-scoring binary gate battery. V3 wants gates to run BEFORE scoring; today circuit/age are BOTH scored AND rejected inside/after the scorer. **ADAPT by extracting** circuit + age + R:R + freshness + liquidity into a dedicated gate module, then removing `circuit_check`/`signal_age` from the score weights (they become gates). Risk: don't duplicate — modify the existing screener path, don't add a parallel one. |
| **03.04 Scoring** | `screening/quality_scorer.py` + `config/scoring_weights.yaml` + `screening/step_executor.py` (8 remaining steps) | **ADAPT** | 10-step weighted→100, proportional, min_pass 60, tiers 80/65. V3: re-scale the surviving weights into an **Execution SEED** layer; `circuit_check` + `signal_age` MOVE OUT to gates (03.03). This is a re-scale + step-removal on the existing scorer, not a rewrite. |
| **03.05 Portfolio Allocator** | Primitives only: `capital/fund_manager.py` (absolute bucket split, no-borrow) · `capital/risk_engine.py` (DUPLICATE_SYMBOL, sector cap) · `capital/position_sizer.py` (per-trade conc) | **NEW (core) + ADAPT (primitives)** | **The ranking core does not exist** — admission is FCFS/concurrent, no candidate set is assembled or sorted. Build NEW: deterministic ranked admission + **shared/aggregate** concentration cap (today's conc is per-trade only) + per-pipeline capital + one-per-symbol at the allocator layer. Sit it ON TOP of the existing bucket/risk primitives. **Highest-effort NEW module.** |
| **03.06 Risk & Sizing** | `capital/position_sizer.py` (risk-based `min(risk,capital,conc)`, tier ON/OFF `enabled`, tier mult 1.0/0.70/0.50) · `capital/risk_engine.py` (10 gates: capital, open/daily/delivery counts, consecutive losses, daily-loss 3%, sector, contrary, duplicate) · `capital/fund_manager.py` (per-bucket caps) | **ADAPT** | Nearly complete. Mostly fail-CLOSED (sizing-invalid/capital/counts reject) with two deliberate fail-OPEN (unknown sector → admit + WARN; kill_switch=None → skip + WARN). V3 feeds ranked candidates (03.05) INTO these. |
| **03.07 Entry** | `orders/order_placer.py::place` · `orders/full_entry_engine.py` · `orders/order_protocol_{limit,co}.py` · `orders/price_math.py` · `broker/product_resolver.py` + `zerodha_adapter` MIS locks | **ADAPT** | Entry-price + slippage tolerance + protective bracket (ONE SL + ONE TGT, software OCO) + MIS coercion all mature. Idempotency is behavioral (webhook dedup + one-reservation-one-place + trade-row-first), **not** DB-UNIQUE-enforced — a V3 hardening candidate. |
| **03.08 Trade Management** | `orders/smart_tgt_manager.py` (CO step-trail, only-advance) · `orders/breakeven_manager.py` (LIMIT_TRIPLE 60%/80% milestones) · `orders/structure_exit_manager.py` (structure trail, ONLY-TIGHTEN + WRONG-SIDE guards) · `orders/tgt_retry_manager.py` · partial-fill in `order_placer._on_order_partially_terminated` | **ADAPT** | Monotonic/only-tighten trailing implemented three ways. OCO-invariant modify = modify-in-place (never cancel-replace) + reconciler invariant. Reuse the only-tighten guard pattern; ensure a single SL owner (structure-exit vs breakeven mutually exclusive — already noted). |
| **03.09 Exit** | `orders/order_placer._handle_exit_fill` + `_cancel_oco_siblings` · `orders/eod_squareoff.py` (15:17, delivery-exempt) · `capital/kill_switch.py` · reconciler CHECK2 SYSTEM_OVERSELL | **ADAPT** | Terminal exits + OCO sibling cancel + 15:17 MIS squareoff WITH delivery exemption + SYSTEM_OVERSELL + kills all present. If V3 wants a hard **15:15 position flatten** distinct from 15:17, that is **NEW** (only entry-cutoff exists at 15:15). |

---

## C. ADAPT vs NEW CLASSIFICATION SUMMARY

- **Pure ADAPT (reuse/extend existing, modify in place):** 03.01 S&R Detection, 03.04 Scoring (re-scale), 03.06 Risk & Sizing, 03.07 Entry, 03.08 Trade Management, 03.09 Exit.
- **ADAPT-by-extraction (logic exists but entangled — refactor into a dedicated module, do NOT duplicate):** 03.03 Hard Gate.
- **Hybrid (NEW core on existing primitives):** Common Utilities (ADAPT helpers + NEW ATR + NEW timeframe-resample); 03.05 Portfolio Allocator (NEW ranking/shared-conc core on existing bucket/risk primitives).
- **Pure NEW (nothing similar exists):** 03.02 Market Regime; overnight/next-morning watchlist (Area G); (optional) a hard 15:15 flatten.

**Anti-duplication (R2) call-outs — build by MODIFYING these, never adding a parallel file:**
- 03.03 → extract from `screening/secondary_screener.py` + `step_executor.py`, don't add a second gate path.
- 03.04 → edit `quality_scorer.py` + `scoring_weights.yaml`, don't fork the scorer.
- 03.05 → sit on `fund_manager.resolve_bucket_allocation` + `risk_engine`, don't reimplement bucket/risk logic.
- 03.06 → extend `position_sizer` / `risk_engine`, don't add a second sizer.

---

## D. UNKNOWNS (need Rama to relay a question or confirm at runtime)

1. **15:15 semantics:** V3 module list says "15:15/15:17 MIS squareoff". Today 15:15 = entry-cutoff ONLY; the position flatten is 15:17. **Does V3 intend a NEW hard 15:15 position flatten, or is 15:15 shorthand for the entry cutoff?** (Q for ChatGPT/Rama.)
2. **"Anchors" definition (03.01):** the S&R detector's closest thing to "anchors" is PDH/PDL/PDC + round numbers. **Does V3 "anchors" mean these, or a new anchor primitive?**
3. **Signal freshness figure:** task says ≤90s; code uses 600s intake expiry + >60s screening hard-zero. **Which freshness value is authoritative for the V3 Hard Gate?**
4. **Ranked admission trigger (03.05):** ranking requires a candidate SET, but ingestion is per-signal concurrent (FCFS). **Does V3 want a batching window (collect candidates over N seconds, then rank-admit), or rank-on-arrival against currently-open slots?** This is an architecture decision that shapes the allocator.
5. **Runtime-wired values:** `signal_processor.signal_expiry_sec` and `position_sizer.enabled` confirmed only at constructor defaults (60s / True); their live-wired values are set in `main.py`/config — verify against the running config at Monday boot.

---

## E. RISKS / SURPRISES (vs memory + fragile areas)

1. **03.05 is the big lift.** No ranking, no allocator, no shared concentration cap exist — this is the largest NEW build and the one most likely to touch the hot signal path (`signal_processor` dispatcher). It changes admission from concurrent-FCFS to ranked; must preserve the TOCTOU-hardened capital/count gates and paper/live parity.
2. **03.03 extraction is delicate.** Moving `circuit_check`/`signal_age` out of the score changes the denominator (proportional scoring) and every historical `screener_results.score` comparison. Re-scaling `scoring_weights.yaml` must be done together with the gate extraction or scores shift silently. Config auditor + report Config sheet read these weights — keep them consistent.
3. **ATR gap.** ATR-based methods are declared in strategy schema but NOT implemented (fall back to FIXED_PCT with WARN). If V3 leans on ATR (regime, sizing, SL), a correct ATR-from-candles helper is a prerequisite NEW build, not an assumption.
4. **Idempotency is behavioral, not schema-enforced** (no DB UNIQUE on trade/signal at order_manager). Fine today (dedup upstream) but a V3 ranked allocator that defers/re-admits candidates could reintroduce a duplicate-entry window — harden with a claim/idempotency key.
5. **Delivery must stay OFF.** Any V3 work near `product_resolver`/`zerodha_adapter`/squareoff must preserve the two locks + GTT carve-out. The 3 dormant `positional_*` strategies are dormant BY DESIGN (Option A) — do not "fix" the 12/3 split.
6. **Many default-OFF branches unpushed** (S&R V2 A/B, MIS-filter, B-1). V3 builds on `main`@`8116b74` only — do not assume those exist. Reconcile intent with ChatGPT before adopting any.

---

## F. DELIVERY CONFIRMATION (must stay OFF until Slice 2.5)

- **`force_intraday_only: true`** (`system_config.yaml:70`) — coerces every intent → INTRADAY/MIS at the adapter chokepoint.
- **`delivery_enabled: false`** (`system_config.yaml:83`) — `delivery_lock` refuses real CNC at `zerodha_adapter.place_order`.
- **`trade_type: INTRADAY`** (`system_config.yaml:77`) — control gate segregates on declared intent; 3 `positional_*` DELIVERY strategies DORMANT (12 WILL / 3 WON'T).
- **NO CNC lifecycle exemptions** beyond the intentional GTT-protection carve-out (place/modify/delete/get GTT + get_holdings ungated so pre-existing overnight protection survives; EOD squareoff & reconciler exempt CNC/GTT trades). No CNC entry can execute in the main system today; no evidence any ever has. **Delivery remains OFF; V3 must not activate it.**

---

## ACCEPTANCE

Investigation complete; every premise source-verified against `main`@`8116b74` (file:line cited); the Common-Utilities + 03.01–03.09 module mapping is finalized with ADAPT/NEW tags and anti-duplication targets; unknowns and risks documented. **Ready for Web Claude + ChatGPT review before Step 1 (Common Utilities).**

*Method note: source read directly (config, screening, quality_scorer, step_executor, entry_gate, PATHS.md, SYSTEM_MAP header via memory) plus three READ-ONLY sequential investigation agents (execution/exit; ingestion/risk/sizing; config/delivery/S&R/regime/utils) — no parallel fan-out, per standing rule.*
