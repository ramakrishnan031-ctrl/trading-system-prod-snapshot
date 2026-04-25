# Deep System Audit — Closure Verdict (24-Apr-2026)

**Source:** `deep_system_audit_2026-04-24.md`
**Closer:** VS Code Claude
**Date:** 2026-04-24
**State at close:** commit `9013845`, 1657 tests green, paper trial Week 1 Day 4/5, live D-Day target Mon 11-May-2026

**Closure policy:**
- **APPLY** — land the fix (pre-live-blocker or cheap/high-value)
- **DEFER** — acknowledged, schedule after paper stabilises or after live Day-1 soak
- **SKIP** — wrong premise, already mitigated, or cost > benefit under current constraints
- **INFO** — factually open but not a safety issue; document and move on

Constraints that shape verdicts:
- Zerodha 10 orders/sec rate limit (already penalise-and-back-off at adapter via BL-6)
- Kite Connect forbids reverse `MARKET` for CO exit (uses `cancel_order(variety="co")`)
- Paper trial deadline: live switch 11-May (~2.5 weeks) — only ship live-blockers now; rest after live Day-1 soak
- Test drift rule: every landing must report `pytest --co -q` count; no delta arithmetic

---

## Part A — Findings Verdicts (24 items)

Header legend: `id | status | verdict | effort | priority`

### Section 1 — Concurrency

#### 1.1 Ghost Entry race — `order_placer.py ↔ order_monitor.py`
- **Current state:** `track()` runs before `_fill_map` is populated at `order_placer.py:590–610`. Race window exists but is bounded by OP-EF2a (lines 579–712) which rolls back `_fill_map` + untracks + cancels broker orders on `track()` failure.
- **Real-world impact:** In paper mode, synthetic fills carry a 10× safety delay. In live, Zerodha's order-ack latency (~50–200ms) gives the code enough time to register. The audit's `_watched` worry is partially stale — `_fill_map` is what matters.
- **Verdict:** **APPLY** — swap order so `_fill_map` insert happens **before** `adapter.place_order()`, mirroring OP's own docstring intent. Small diff in `order_placer._place_entry_leg`. Low risk, high clarity. **Priority: P0 pre-live.**

#### 1.2 Double Spend TOCTOU — `signal_processor.py ↔ risk_engine.py`
- **Current state:** Per-bucket `FundManager` `RLock` at `capital/fund_manager.py:398–430` acts as a backstop — a second thread reserving from a drained bucket gets rejected. But `risk_engine.approve()` is lockless; the sector-exposure and max-positions checks race against concurrent reserves. Five signals for the same sector **can** all pass approval; capital reserve rejects only if bucket runs dry.
- **Real-world impact:** With `max_sector_exposure_pct` at 20% and `max_open_positions` at N, the drift risk is concrete — a sector can overshoot 20% if per-signal margin is small relative to bucket.
- **Verdict:** **APPLY** — wrap `risk_engine.approve + fund_manager.reserve` in a single portfolio-lock critical section in `signal_processor._process_one`. Reuse existing `FundManager._lock`. **Priority: P0 pre-live.**

#### 1.3 Split-brain paradox — `order_monitor.py ↔ order_reconciler.py`
- **Current state:** `order_placer._fill_map` uses atomic pop under `_fill_map_lock` (lines 760–771, 806–807). `_on_order_filled` and `_on_order_status_changed` both pop — whichever fires first wins, the second sees `None` and returns. Explicit design doc at 795–797. Reconciler's adoption path also pops (RC-series).
- **Verdict:** **SKIP** — already fixed. Audit description is based on an older threat model.

### Section 2 — Financial / Math

#### 2.1 LIMIT_TRIPLE naked short — `order_protocol_limit.py`
- **Current state (verified directly):** `order_protocol_limit.py:95–210` places ENTRY LIMIT → SL SL-M → TGT LIMIT **all at full qty** in sequence, without waiting for ENTRY fill. If ENTRY partial-fills (100/1000) and price spikes through TGT, TGT executes at qty=1000 → naked short of 900. Broker RMS does not link the legs. `RiskEngine` is blind.
- **Real-world impact:** CATASTROPHIC. This is exactly why Zerodha built CO — bracketed legs linked at the broker. `LIMIT_TRIPLE` is the DELIVERY / CNC fallback, which is where this vuln bites hardest (overnight hold).
- **Verdict:** **APPLY** — restructure protocol: place ENTRY only; subscribe to `OrderFilled(ENTRY)`; on fill (including partial via audit-#7 path), place SL for `qty_filled`, then TGT for `qty_filled`. This is the Phase-1 Fix #2 in the priority queue. **Priority: P0 pre-live BLOCKER. Largest line-count fix in the audit. Requires new state machine (`ENTRY_PLACED → ENTRY_FILLED → LEGS_PLACED`).**

#### 2.2 IEEE-754 float drift — `cost_calculator.py → fund_manager.py → invariant.py`
- **Current state:** `broker/cost_calculator.py:220` returns `CostBreakdown` with `float` fields. `capital/fund_manager.py:646` takes `costs: float` and does `pnl = gross_pnl - costs`. `capital/invariant.py:40` tolerance `_DEFAULT_TOLERANCE = 0.01`. Float arithmetic accumulates ≥ 1e-14 per op; over ~500 trades crossing tolerance is plausible, especially once PnL is mixed with commissions/STT.
- **Real-world impact:** At Rs 10L capital × ~5 trades/day, 500 trades ≈ 100 days. We'll live-trade ~20 days before crossing the risk window. But a single outlier op can push it earlier.
- **Verdict:** **APPLY but scope carefully** — two paths:
  - **Option A (robust, big diff):** Convert ledger to `decimal.Decimal` end-to-end. Touches every capital/ledger site. ~300 LOC diff.
  - **Option B (cheap, correct):** Leave ledger as float; in `invariant._compare`, round both sides to 2 dp (paise) **before** comparing to tolerance; keep tolerance at 0.01. Adds one `round(x, 2)` per side. Eliminates drift without converting types.
  - **Recommendation:** Option B for pre-live. Option A backlog for v3. **Priority: P0 pre-live (Option B).**

#### 2.3 Reserved capital rehydration amnesia — `fund_manager.py ↔ main.py`
- **Current state:** `FundManager.rehydrate_from_open_trades` at `capital/fund_manager.py:1022–1201` replays RESERVE + COMMIT rows for all non-closed trades (including `PENDING_FILL`) and rebuilds `_reservations` dict at 1224–1233. Memory record BL-1 (`project_bl1_rehydrate.md`) documents this closure.
- **Verdict:** **SKIP** — already fixed. Audit description assumes pre-BL-1 state.

#### 2.4 Partial Fill Black Hole — `order_monitor.py`
- **Current state:** `order_placer._on_order_status_changed` at lines 773–867 handles CANCELLED/REJECTED/FAILED with `qty_filled > 0` by calling `FundManager.commit_to_used(actual_qty=qty_filled)`. The docstring explicitly calls this "Audit #7" closure. Partial fills are committed; the unfilled portion is auto-released via `commit_to_used`. `record_entry_fill` is called so DB reflects partial OPEN state.
- **Verdict:** **SKIP** — already fixed.

### Section 3 — Broker API / Market Physics

#### 3.1 CO square-off trap — `eod_squareoff.py`
- **Current state:** `orders/eod_squareoff.py:762–780` places exit orders with `intent="INTRADAY"`, `order_type="MARKET"` for all positions — including CO. Zerodha forbids this for CO; CO must be cancelled via `cancel_order(variety="co")`.
- **Real-world impact:** Guaranteed broker auto-square at 15:20 with ₹50+GST penalty per CO position. At 10 CO positions/day this is ₹500+/day bleeding.
- **Verdict:** **APPLY** — branch on protocol: if `order_protocol == "CO"` call `adapter.cancel_order(broker_order_id, variety="co")`; else reverse-market. Needs `broker_order_id` of the SL leg (already tracked). **Priority: P0 pre-live BLOCKER.**

#### 3.2 Tick-size rejection — `order_placer.py ↔ smart_tgt_manager.py`
- **Current state:** `order_placer.py:364–368, 1196–1212` — `_round_to_tick()` wraps all prices. `smart_tgt_manager.py:520–539` rounds before `modify_order`. IC8 landed.
- **Verdict:** **SKIP** — already fixed.

#### 3.3 15:17 liquidity vacuum — `eod_squareoff.py ↔ slippage_model.yaml`
- **Current state:** `eod_squareoff.py:777` places MARKET orders (`price=0.0`, `order_type="MARKET"`) at EOD. No LTP-relative limit logic.
- **Real-world impact:** Small-caps do slip 3–5% on the 15:17 drain. Combined with `slippage_model.yaml` assuming 0.3% max, backtests systematically underestimate EOD slippage.
- **Verdict:** **APPLY** — replace MARKET with aggressive LIMIT: fetch LTP, set `price = LTP × (1 - 0.01)` for SELL / `LTP × (1 + 0.01)` for BUY, `order_type="LIMIT"`. If unfilled by 15:19, fall back to MARKET (1-minute window). **Priority: P1 — reduces live P&L bleed; not a correctness bug, so land after P0 items.**

#### 3.4 Delivery protocol mismatch — `positional_momentum_long.yaml` vs `order_protocol_limit.py`
- **Current state:** `config/strategies/positional_momentum_long.yaml:10–11` declares `intent: DELIVERY` + `order_protocol: LIMIT_TRIPLE`. `order_protocol_limit.py:131` places SL leg as `SL-M`. Zerodha rejects overnight `SL-M` for CNC products — they are GTT-only or need `SL` (not `SL-M`).
- **Real-world impact:** First DELIVERY signal → SL leg rejects → naked overnight position. Compounds 2.1.
- **Verdict:** **APPLY** — in `LimitTripleProtocol`, branch on intent: for `DELIVERY`, use `SL` (not `SL-M`) with explicit `price = sl_price`; for `INTRADAY`, keep `SL-M`. Verify with Zerodha docs that SL is valid for CNC (it is). **Priority: P0 pre-live BLOCKER** — any DELIVERY strategy triggered live will break.

### Section 4 — Resources / Memory

#### 4.1 Shadow Tracker zombie inning — `shadow_tracker.py ↔ live_feed.py`
- **Current state:** `orders/shadow_tracker.py:192` subscribes to `EodSquareoffComplete`; `_on_eod_complete` (line 346) closes all active innings and sets `_eod_fired=True`. BL-13 (lines 164–189) persists `_eod_fired` via `eod_squareoff_log` so 13-minute post-EOD restart is guarded. Memory record `project_bl13_shadow_tracker.md`.
- **Verdict:** **SKIP** — already fixed.

#### 4.2 SQLite WAL checkpoint starvation — `state_store.py`
- **Current state:** `core/state_store.py:79–85` sets `journal_mode=WAL` + `synchronous=NORMAL`. No scheduled checkpoint; relies on SQLite auto-checkpoint which requires quiet-lock windows that may never arrive under continuous writer pressure.
- **Real-world impact:** `-wal` file grows unbounded. At our write volume (~100–500 rows/day) this is slow bleed, not acute — months before disk issues.
- **Verdict:** **APPLY (cheap)** — add `PRAGMA wal_checkpoint(TRUNCATE)` in a cron task at 16:00 IST after EOD. Already have cron infra from F.1. 10-line change to `scripts/cron_maintenance.py` (new file) + systemd timer. **Priority: P2 — nice-to-have; soak-window observability issue.**

#### 4.3 SQLite connection memory leak — `state_store.py ↔ signal_processor.py`
- **Current state:** `core/state_store.py:140–162` uses `threading.local()`. Python does not auto-close `threading.local` values on thread exit. ThreadPoolExecutor workers (`signals/signal_processor.py`) recycle threads within the pool lifetime, so leak is bounded by pool size (default 4).
- **Real-world impact:** For a 4-worker pool, this is 4 connections max — not a leak, a bounded pool. The audit's "create/destroy threads" premise assumes a non-pooled executor.
- **Verdict:** **SKIP** — misdiagnosed. With bounded pool, connection count is bounded. Noted as INFO.

#### 4.4 Entry Gate amnesia on restart — `entry_gate.py ↔ main.py`
- **Current state:** `screening/entry_gate.py:126` `_watchlist` is in-memory dict. No rehydrate; `main.py:1267` calls `entry_gate.start()` without populating from DB `GATE_WAITING` rows.
- **Real-world impact:** A restart mid-session orphans all watching signals. Since restarts are rare, low frequency, but high per-incident cost (missing high-quality setups).
- **Verdict:** **APPLY** — on `entry_gate.start()`, query signals with `status='GATE_WAITING'` and re-add to `_watchlist`. Mirror `FundManager.rehydrate_from_open_trades` pattern. **Priority: P1 pre-live.**

### Section 5 — Process Flow

#### 5.1 Inning transition gap — `shadow_tracker.py`
- **Current state:** `shadow_tracker.py:200 (on_tick)` plus `_check_hit (line 669)` have no guard against a **new real-signal entry arriving while a simulated inning is active**. Signal processor does not query `shadow_tracker.is_tracking(symbol)` before approving new entry.
- **Real-world impact:** Rare — requires a new signal for the same symbol within minutes of a fill on that symbol. v2 uses `symbol_locks` (signal_processor) which blocks re-entry while a trade is OPEN, but shadow tracking is a second inning on a **closed** trade, so symbol_lock is released. Collision possible.
- **Verdict:** **APPLY** — in `signal_processor._check_symbol_locked()`, also check `shadow_tracker.is_tracking(symbol)` and skip new signal if present. 5-line change. **Priority: P1 pre-live.**

#### 5.2 15:17 partial fill phantom share trap — `eod_squareoff.py ↔ order_monitor.py`
- **Current state:** `eod_squareoff.py:766` reads `qty_filled` from DB. If OrderFilled(PARTIAL) is delayed, DB says 0 → only cancel emitted. However, the audit-#7 path commits partial fills on CANCELLED status — so the DB catches up *after* cancel. The race is: `eod_squareoff` queries (qty=0) → cancels → OrderFilled(PARTIAL) arrives → audit-#7 commits. Still a gap because `eod_squareoff` never emits a reverse order for the partial fill.
- **Real-world impact:** Partial fills at EOD are rare (limit orders on liquid stocks typically fill-or-cancel). Tail risk.
- **Verdict:** **APPLY** — in `eod_squareoff._build_exits()`, query broker via `adapter.get_positions()` (authoritative) not DB. Use broker qty. **Priority: P1 pre-live — pairs with 3.3 EOD rewrite.**

#### 5.3 Smart target blind minute — `smart_tgt_manager.py ↔ candle_store.py`
- **Current state:** `orders/smart_tgt_manager.py:309–346` — trails only on `_on_candle_close`, using `candle.high/low` as `favorable`. A 3% intra-minute spike that reverses is not captured — `best_price` only moves when the candle closes with a new high/low.
- **Real-world impact:** Real. Intraday volatility can absolutely produce wick-then-reverse patterns where we leave 1–2% gains on the table. For a ₹10L book, this is ₹2k–5k/trade missed protection.
- **Verdict:** **APPLY but carefully** — subscribe to ticks in addition to candles; update `best_price` per tick (no modify on every tick — too many API calls). Compute `target_sl` per tick, but only call `modify_co_sl` if the new SL exceeds the last-sent SL by ≥ 0.5 × ATR or ≥ 0.3% (debounce). This respects Zerodha modify rate (~10/sec). **Priority: P1 post-live** — current candle-close is correct but conservative; not a safety bug. Defer to Week-2 live if paper results look good.

#### 5.4 Event-bus thread-block cascade — `live_feed.py → candle_store.py → smart_tgt_manager.py`
- **Current state:** `data/live_feed.py:312–336` single consumer thread iterates callbacks synchronously. `smart_tgt_manager._on_candle_close` → `_modify_co_sl` → `adapter.modify_order` (sync HTTP, ~100ms). With N tracked trades, N × 100ms serial.
- **Real-world impact:** At 10 trades tracked, candle close → 1 second block. Tick queue can backfill during the block. Not a WS disconnect (WS consumer is upstream in pykiteconnect), but can lose ticks in `_tick_queue` if bounded.
- **Verdict:** **APPLY** — move `modify_order` to a dedicated `ThreadPoolExecutor(max_workers=2)` inside smart_tgt; enqueue modifies with newest-wins semantics (coalesce same-trade). Keeps main consumer responsive. **Priority: P1 pre-live — scales with adoption, small-book risk is manageable.**

### Section 6 — Edge cases / Runtime

#### 6.1 Worker-death capital leak — `webhook_receiver.py ↔ fund_manager.py`
- **Current state:** `signals/signal_processor.py:557–593` — explicit except+finally around the reserve→place pipeline. `FundManager.release` is called on any exception after reserve; `finally` releases in-flight symbol lock. Memory record shows this was hardened in E.2/E.3.
- **Verdict:** **SKIP** — already fixed. Audit's premise "crashes mid-flight leave capital reserved" is not current.

#### 6.2 Paper mode front-running — `zerodha_adapter.py` (paper_synth)
- **Current state:** `broker/zerodha_adapter._synth_fill:1155` fills at the `price` parameter after `auto_fill_delay_sec`, no LTP gating.
- **Real-world impact:** Paper P&L is inflated because LIMIT orders always fill, including unrealistic prices. `daily_review.py` results overstate live edge.
- **Verdict:** **APPLY (high value, small diff)** — in `_synth_fill`, query the latest LTP from an injected `LiveFeedManager` (or `candle_store.latest(symbol)`); fill only if LTP has crossed the limit (LTP ≤ limit for BUY, LTP ≥ limit for SELL). Otherwise keep pending. For MARKET, fill at LTP + slippage_model. **Priority: P0 pre-live BLOCKER** — we are *on* paper right now; false P&L = false confidence.

#### 6.3 Webhook time-boundary bypass — `webhook_receiver.py`
- **Current state:** `signals/webhook_receiver.py:355` `minute_str = triggered_at.strftime("%Y-%m-%d %H:%M")`. Chartink jitter of ±1 sec across a minute boundary produces different hashes.
- **Real-world impact:** Chartink jitter is usually ≤ 500ms; crossing a minute boundary requires ~1s jitter, which does happen. The downstream per-symbol lock in `signal_processor` prevents duplicate reservations, so the worst outcome is a rejected duplicate — not double-entry. Still wastes a scan cycle + logs noise.
- **Verdict:** **APPLY (cheap)** — use 5-minute-bucketed dedup key (`triggered_at.replace(minute=(triggered_at.minute // 5) * 5)`), or hash the signal payload only (symbol + strategy + side), not time. One-line change. **Priority: P2 — nice-to-have.**

#### 6.4 NTP clock stepping — `time_authority.py ↔ candle_store.py`
- **Current state:** `core/time_authority.py:94–99` uses `datetime.now(tz=_IST)`. No monotonic fallback. `candle_store.py:181` uses `now_ist()` for close_ts without monotonicity enforcement.
- **Real-world impact:** On a well-tuned server (VM, systemd-timesyncd in slew mode) NTP steps are < 128ms and slewed, not stepped. On the PC running paper, step risk is real but still rare (chronyd default is slew for drift < 1s).
- **Verdict:** **DEFER to INFO** — cheap mitigation exists (systemd `tsdiff` threshold; use `time.monotonic()` for intervals, `datetime.now()` only for timestamps). Not a live-blocker. Document in DEPLOYMENT.md: "ntpd must be in slew mode". **Priority: P3 — document and move on.**

#### 6.5 Werkzeug dev server — `webhook_receiver.py`
- **Current state:** `main.py:1272–1282` → `webhook_receiver.app.run(threaded=True)`. Flask/Werkzeug dev server.
- **Real-world impact:** Chartink burst of N signals ≤ ~20 is fine; > 50 in a sub-second window can drop connections. Paper trial has not hit this yet (4–10 signals/cycle typical).
- **Verdict:** **APPLY (pre-live)** — swap to `waitress.serve(app, host=..., port=..., threads=8, connection_limit=100)`. 5-line change, no code refactor. Waitress is pure-Python, no C deps, Windows-friendly. **Priority: P1 pre-live.**

---

## Part B — Priority Queue Verdicts (14 items)

| # | Fix | Addresses | Verdict | Priority | Notes |
|---|-----|-----------|---------|----------|-------|
| 1 | Atomic Registration (`order_placer.py`) | 1.1 | **APPLY** | P0 | Swap insert order; cheap. |
| 2 | Naked Short Fix (`order_protocol_limit.py`) | 2.1 | **APPLY** | P0 | Biggest diff in the audit. New state machine. |
| 3 | Float-Drift Fix (`fund_manager.py`) | 2.2 | **APPLY (Opt B)** | P0 | Round-to-paise in invariant comparison; defer full Decimal rewrite. |
| 4 | Tick-Safe Quantization (`instrument_cache.py`) | 3.2 | **SKIP** | — | Already done (IC8). |
| 5 | CO Square-off (`eod_squareoff.py`) | 3.1 | **APPLY** | P0 | `cancel_order(variety="co")` path. |
| 6 | EOD Limit Protocol (`eod_squareoff.py`) | 3.3 | **APPLY** | P1 | LTP±1% limit with MARKET fallback at 15:19. Pairs with 5.2. |
| 7 | Partial Fill Adoption (`order_monitor.py`) | 2.4 | **SKIP** | — | Audit-#7 closure already covers this via `_on_order_status_changed`. |
| 8 | Portfolio Lock (`risk_engine` → `fund_manager`) | 1.2 | **APPLY** | P0 | Reuse `FundManager._lock` around approve+reserve. |
| 9 | Rehydration Fix (`main.py` → `fund_manager.py`) | 2.3 | **SKIP** | — | BL-1 closed this. |
| 10 | Shadow Tracker Cleanup (`shadow_tracker.py`) | 4.1 | **SKIP** | — | BL-13 already subscribes to `EodSquareoffComplete`. |
| 11 | Production WSGI (`webhook_receiver.py`) | 6.5 | **APPLY** | P1 | Waitress; 5-line change. |
| 12 | Watchdog Timer (`live_feed.py`) | (not in findings) | **APPLY** | P1 | Add tick-age watchdog + WS reconnect. LiveFeedManager has `on_reconnect` hook; add the timer. |
| 13 | WAL Maintenance (`state_store.py`) | 4.2 | **APPLY** | P2 | Cron PRAGMA. |
| 14 | Entry Gate Rehydration (`entry_gate.py`) | 4.4 | **APPLY** | P1 | Query GATE_WAITING rows on startup. |

---

## Part C — Recommended Action Plan

Ordered by priority, time-boxed against the 2.5-week pre-live window.

### Phase A (P0 BLOCKERS — must land before 11-May)
**Target: complete by end of this week (Fri 01-May).**

1. **2.1 Naked Short fix** — `order_protocol_limit.py` rewrite + new tests. ~1 day.
2. **3.4 DELIVERY protocol mismatch** — `SL` vs `SL-M` branch. 2 hours. Bundle with 2.1.
3. **3.1 CO square-off trap** — branch on `order_protocol == "CO"` in `eod_squareoff`. ~3 hours.
4. **1.2 Portfolio Lock** — wrap approve+reserve under `FundManager._lock`. ~2 hours.
5. **1.1 Atomic Registration** — swap `_fill_map` insert order. ~1 hour.
6. **2.2 Float drift (Option B)** — `round(x, 2)` in invariant comparator. ~1 hour.
7. **6.2 Paper front-running fix** — LTP-gated synth fill. ~3 hours.

**Phase A total: ~2 engineering days. Phase A test count target: +20 to +40 tests.**

### Phase B (P1 pre-live — land by 08-May)
8. **3.3 + 5.2 EOD LIMIT protocol + broker-authoritative qty** — ~1 day.
9. **4.4 Entry Gate rehydration** — mirror FundManager pattern. ~3 hours.
10. **6.5 Waitress WSGI** — swap server. ~1 hour.
11. **5.4 Modify-order worker pool** — `ThreadPoolExecutor(max_workers=2)` with coalesce. ~4 hours.
12. **5.1 Shadow-tracker re-entry guard** — check in signal_processor. ~1 hour.
13. **12 Watchdog Timer** — `LiveFeedManager` tick-age watchdog. ~2 hours.

### Phase C (P2 post-live / backlog)
14. **4.2 WAL checkpoint cron.**
15. **6.3 5-min dedup bucket.**
16. **5.3 Smart target intra-minute** — evaluate against paper Week 2 data first. If intra-minute whipsaws lost us < 0.5% P&L, skip.

### Phase D (INFO / Document only)
17. **6.4 NTP clock stepping** — add DEPLOYMENT.md note requiring slew mode.
18. **4.3 Connection leak** — misdiagnosis; no action.

---

## Part D — Deviations from Audit Phasing

The audit groups fixes into Phases 1–4 by topic. My phasing is by **priority against live deadline**, so Phase 1 of the audit is split across my Phase A (P0) + Phase B (P1). Rationale:

- Audit Phase 1 Fix #4 (tick quantization) is already closed → skip.
- Audit Phase 2 Fix #7 (partial-fill adoption) is closed by Audit-#7 closure → skip.
- Audit Phase 3 Fix #9 (rehydration) is closed by BL-1 → skip.
- Audit Phase 3 Fix #10 (shadow cleanup) is closed by BL-13 → skip.
- Audit Phase 4 Fix #12 (watchdog) was not itemised in findings 1-22 but is named separately in the queue — kept as P1.

**Net new work:** 13 items (from 14 priority-queue items: 4 skipped + 9 applied; + 4 additional findings not in queue: 4.4 in queue but also separate, 5.1, 5.3 deferred, 5.4, 6.2, 6.3, 6.4, 6.5). Corrected count below.

### Actually applying:
- Phase A: 7 items
- Phase B: 6 items
- Phase C: 3 items (backlog)
- Phase D: 2 items (INFO only)
- **SKIPPED as already fixed: 7 items** (1.3, 2.3, 2.4, 3.2, 4.1, 4.3, 6.1 from findings; #4, #7, #9, #10 from priority queue — overlapping set)

---

## Part E — Open Questions for Rama

Before I touch code, please confirm:

1. **Paper trial schedule:** Is the Mon 11-May live-Day-1 date firm? This sets whether Phase A must finish by 01-May or 04-May.
2. **Float-drift Option B approval:** OK to go with `round(x, 2)` in invariant (cheap, correct-enough), or do you want the full Decimal migration (3× the effort, v3-scale refactor)?
3. **Naked-short fix scope:** The rewrite of `LimitTripleProtocol` changes the shape of `EntryResult` (SL + TGT orders are placed async after ENTRY fill). This ripples into `order_placer`, `order_monitor`, and the `full_entry_engine`. Confirm you want this rewrite vs. a lighter mitigation (e.g., set `max_position_size_pct` very low for LIMIT_TRIPLE strategies and live with the risk). My recommendation is the rewrite — any less is a live hazard — but it's a ~2-day diff with blast radius across the orders/ layer.
4. **Smart target (5.3) verdict:** OK to defer to Week-2 live and evaluate with paper P&L data, vs. shipping pre-live?
5. **WSGI swap (6.5):** Waitress is my pick (pure-Python, Windows-friendly). Any objection to adding it as a dependency?

**Nothing committed. Awaiting your go/no-go per phase.**
