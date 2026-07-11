# V3 STEP 6 — 03.05 PORTFOLIO ALLOCATOR: DESIGN PLAN (PLAN ONLY, NO CODE)

**Date (IST):** 12-Jul-2026 · **Type:** NEW ranked-admission core on the HOT signal path — *design only*. · **For:** Web Claude + ChatGPT + Rama architectural sign-off. · **Status:** NOTHING BUILT (no code/config/commit; ledger unchanged).

**Headline.** Admission today is **FCFS/concurrent**: the dispatcher drains the queue to a 5-worker pool and each signal runs the *whole* pipeline independently — no candidate set is ever assembled or ranked (`signals/signal_processor.py:319-341`, the "Priority rank #1 of 1" text at `:439` is a hardcoded placeholder). 03.05 replaces that with **collect-over-a-short-candle-window → rank by score → greedily admit within portfolio rules → atomically reserve via the EXISTING primitives**. The plan below inserts this at **one seam** (the boundary between *candidate preparation* and *atomic admission* inside `_process_one`), keeps today's FCFS **byte-identical behind a default-OFF flag**, and defines a **shadow regret-compare** that runs alongside live FCFS before the allocator ever governs. Step-6b implements the approved plan.

The allocator **sits on** the proven primitives and reimplements none of them: `fund_manager.reserve` (`capital/fund_manager.py:473`), `fund_manager.portfolio_lock` (RLock, `:382`), `count_live_reservations[_for_strategy]` (`:1424` / `:1440`), `risk_engine.approve`'s 10 gates (`capital/risk_engine.py:177`), and `signal_processor._enforce_strategy_position_cap` (`:589`).

---

## Source-verified baseline (what exists today)

| Fact | Source |
|---|---|
| Single dispatcher thread; `queue.get` → `executor.submit(_process_one_safe, …)`; **no batching/ranking** | `signal_processor.py:319-341` |
| 5-worker `ThreadPoolExecutor` (`worker_count` default 5) | `signal_processor.py:126, 252` |
| "Priority rank #1 of 1 candidates" = **placeholder** (no real ranking) | `signal_processor.py:439` |
| Per-signal pipeline: pre-flight → strategy → screen → price → **size** → **[portfolio_lock: cap → approve → reserve → RESERVED]** → place | `signal_processor.py:626-960` |
| Atomic admission critical section (the seam's downstream half) | `signal_processor.py:919-960` |
| FIX-018 TOCTOU: `_in_flight_count` bumped **before** `approve()` | `signal_processor.py:919-922` |
| Three admission paths, each reserving independently | `_process_one:626` (reserve `:943`) · `continue_from_gate:~1460` (reserve `:1602`) · `continue_from_retest:1794` (reserve `:1904`) |
| `reserve()` atomic, per-bucket, no cross-bucket borrow, returns `ReservationResult` | `fund_manager.py:473-587` |
| 70/30 intraday/positional split; `reserve()` consults ONLY the intent's bucket | `fund_manager.py:111-145, 509` |
| The 10 risk gates (KILL_SWITCH, SIZING_VALID, CAPITAL, OPEN_POSITIONS, DAILY_TRADES, CONSECUTIVE_LOSSES, DAILY_LOSS, SECTOR_EXPOSURE, CONTRARY_POSITION, DUPLICATE_SYMBOL) | `risk_engine.py:342-589` |
| **Only aggregate cap = SECTOR 0.40**; concentration (10%) is **per-trade** in the sizer, not aggregate | `risk_engine.py:547-558` · `position_sizer` `qty_by_concentration` |
| `sector_exposure` counts **DB** `margin_reserved` (PENDING_FILL/OPEN/PARTIAL) — NOT `fund_manager` live reservations | `state_store.py:732-748` |
| Count gates ARE reservation-aware via `count_live_reservations` (FIX-185 / Bug B) | `risk_engine.py:426-430, 477-481` |
| Chartink POSTs a **batch** per scan (`stocks[]`/`trigger_prices[]`); dedup = SHA-256 fingerprint (epoch-bucket) + `idx_signals_fingerprint_today` UNIQUE; atomic `_claim_in_flight(symbol)` held until pipeline completion | `webhook_receiver.py:6-8, 695-714` · `schema.sql:82` |
| `signals.signal_id` PRIMARY KEY; `trades.signal_id` FK **NOT UNIQUE** → one-trade-per-signal is **behavioral**, not DB-enforced | `schema.sql:43, 119, 241, 254` |
| Deferred-admission precedent exists (gate/retest park a signal_id, reserve at resume; `gate_state`/retest keyed on signal_id, "re-add … does not raise") | `state_store.py:2659-2705, 2737` |
| Reusable candle-close callback infra | `data/candle_store.py::CandleStore.register_on_candle_close` (used by structure_exit/smart_tgt/breakeven) |
| Fresh-LTP re-anchor at placement (entry not stale) | `signal_processor.py:858-882` (FIX-067) |

---

## P1 — DISPATCHER SEAM

**Where FCFS admission happens.** The dispatcher (`_dispatcher_loop`, `:319`) does *only* dequeue + submit; it never decides. The **decision** is fused inside `_process_one`: sizing ends at `:904` (`sizing.success`), then the **atomic admission** runs in the `with self._fm.portfolio_lock:` block (`:924-960`) — `_enforce_strategy_position_cap` → `risk.approve` → `fm.reserve` → status `RESERVED` → (later) `place()`.

**The single seam = the boundary between two phases that are today fused per-signal:**
- **PREPARE** (`:653-904`): pre-flight, strategy lookup, screen+score, price derivation, **sizing**. Produces a fully-scored candidate. **No capital touched, no reservation.** Runs concurrently per-signal (5 workers) — unchanged.
- **ADMIT** (`:919-1052`): `_in_flight_count`++, portfolio_lock, per-strategy cap, `approve`, `reserve`, `place`.

**Insertion (one seam, flag-selected).** Interpose a **WindowBuffer + Ranker** at the PREPARE→ADMIT boundary:
- **OFF (default):** PREPARE flows straight into ADMIT in the same worker — **the current fused path, byte-identical**. The buffer/ranker are never entered.
- **SHADOW:** PREPARE additionally emits a **copy** of the scored candidate to the buffer (non-blocking, guarded), then flows straight into ADMIT exactly as OFF. Live FCFS still governs and places. The buffer is drained by an async observer that computes the counterfactual (P6).
- **ENFORCE:** PREPARE emits the candidate to the buffer and **returns** (does not self-admit). A single **admission worker** drains the buffer at window close, ranks, and runs ADMIT greedily for the whole batch under one `portfolio_lock`.

This is a *refactor-in-place* (extract the ADMIT block into a callable that both the fused OFF path and the batch worker invoke), **not** a parallel allocator and **not** a second reservation path — the same `approve`/`reserve`/`_enforce_strategy_position_cap` calls run, just driven by the batch worker under enforce. The three admission paths keep their own ADMIT; **v1 wraps only `_process_one`** (see P8 for the gate/retest paths).

---

## P2 — BATCHING WINDOW

**Arrival pattern (verified).** Chartink fires on candle close and POSTs a *batch* of symbols per scan in one request; several scanners can fire on the same candle → several POSTs within ~1-2s (the open-bell burst is ~40 signals from one IP, `webhook_receiver.py:64`). Each symbol becomes one queue tuple after dedup + symbol-claim (`:695-714`). So the natural unit of "candidates that should compete" is **one candle's worth of signals across all scanners**.

**Window definition.** Candle-aligned + short:
- Anchor to the candle boundary (reuse `CandleStore.register_on_candle_close`, already driving structure_exit/smart_tgt/breakeven; or a monotonic timer aligned to the wall-clock candle boundary if the feed isn't subscribed for the relevant tokens).
- Collect from candle-close until a short **drain tail** (a few seconds) that absorbs the multi-scanner burst + queue drain, then close the window and rank.
- Far shorter than the 60s signal expiry (`:128, :675`) so nothing expires in the buffer.

**Migration scope (recommendation, with rationale).**
- **SHADOW observes ALL candidates** (existing 15 scanners + V3) → portfolio-wide regret measurement.
- **ENFORCE v1 governs only the V3-playbook path**; the existing 15 scanners keep FCFS. Rationale: smallest blast radius on the proven money path; V3 entries are new and already flag-gated; the allocator reads FCFS-admitted positions as **prior exposure** via the existing count/exposure/reservation primitives, so caps still compose. **Open question (P10):** a portfolio-wide shared cap spanning two admission regimes during migration — full-portfolio enforce is v2 once shadow proves out.

---

## P3 — RANKED-ADMISSION ALGORITHM

Within a closed window, under **one** `fund_manager.portfolio_lock` acquisition (reuse — same lock FCFS holds today, so no new lock ordering):

1. **Sort** survivors by a **deterministic** key: `(score DESC, tier_rank DESC, triggered_at ASC, signal_id ASC)`. No clock/random — fully replayable.
2. **Greedy admit** each candidate in order; maintain a running in-memory batch tally:
   - **one-per-symbol:** skip if the symbol was already admitted this batch, or `has_active_position` (reuse `state_store.py:750`) — mirrors DUPLICATE_SYMBOL.
   - **long/short balance** (NEW, optional knob; default inert) — skip if it would breach a configured directional skew.
   - **shared concentration cap** (NEW, P4).
   - **per-strategy cap:** `_enforce_strategy_position_cap` (reuse, `:589`).
   - **risk gates:** `risk.approve(…, processor_in_flight_count=<batch in-flight>)` (reuse — all 10 gates, `:177`).
   - **capital:** `fm.reserve(…)` (reuse, `:473`) — atomic, per-bucket.
   - on success → `RESERVED` → drive `place()` (reuse the existing placement block `:963-1052`, incl. FIX-067 fresh-LTP re-anchor, entry throttle, FIX-070 late kill-switch recheck).
   - on **any** gate/reserve reject → skip this candidate (release its in-flight increment + mark its signal status exactly as the current `_PipelineReject` path does), continue to the next.

**Why the running tally is mostly automatic.** `reserve()` mutates the bucket balances and `count_live_reservations[_for_strategy]` immediately, and `approve()` reads them — so **counts and capital are already reservation-aware within the batch** (each admitted reserve is visible to the next candidate). The *only* NEW running state the allocator must track itself is the **shared concentration measure** (P4) and the **batch's own** one-per-symbol / long-short sets.

---

## P4 — SHARED CONCENTRATION CAP (NEW)

**Gap (verified).** Today's 10% concentration is **per-trade** (`position_sizer.qty_by_concentration = floor(total_capital*0.10/entry_price)`), and the only *aggregate* cap is **SECTOR 0.40** (`risk_engine.py:547-558`, from `state_store.sector_exposure`). There is **no portfolio-wide concentration ceiling** across both pipelines + holdings.

**Definition (proposed).** A portfolio-wide ceiling on total deployed margin (candidate for the primary form; alternatives in P10):
```
deployed = total − (intraday_avail + positional_avail)          # from fm snapshot, reservation-aware
admit candidate iff  deployed + candidate.margin_required  ≤  max_portfolio_deployment_pct × total
```
Computed at admission from the `fund_manager` snapshot (`get_snapshot`, `:1453`), which `reserve()` keeps live — so it is **reservation-aware within the batch** by construction.

**Relationship to the sector 0.40 cap (no duplication/conflict).**
- Sector cap = **per-sector** ceiling (any one sector ≤ 40%).
- Shared concentration = **portfolio-wide** ceiling (the whole book ≤ X%).
- They **compose** (both must pass); neither subsumes the other. Constrain the config so the shared cap ≥ the single-sector cap (else sector 0.40 becomes dead) and < 1.0 (else it never binds).
- **Default INERT** (`max_portfolio_deployment_pct: null`/`1.0` = no-op) → OFF *and* enforce-v1 are byte-identical until Rama sets a real value.

**Do NOT silently rewrite gate 8.** The allocator ADDS a reservation-aware aggregate check in the greedy loop; it does not modify `risk_engine`'s existing DB-based SECTOR_EXPOSURE gate. (As a *side benefit* the batch's single-lock running tally also closes the residual sector-cap TOCTOU noted below — but any change to gate 8 itself is a separate, flagged decision, P10.)

---

## P5 — IDEMPOTENCY / CLAIM KEY

**Existing guards (verified) that the window already inherits.** A buffered candidate is *already dequeued* and still holds the receiver's atomic **`_claim_in_flight(symbol)`** (`:695`), which is released only when the pipeline completes (`release_in_flight`), plus a unique `signal_id` (PK) and the today-unique fingerprint index. Downstream, `DUPLICATE_SYMBOL`/`has_active_position` (`risk_engine.py:583`) block a second position per symbol. So a **short in-window buffer opens no new duplicate window** — provided the allocator releases the in-flight claim + marks signal status on rejection exactly as `_process_one`'s `finally` does today.

**The one NEW risk = cross-window carry.** If the allocator *defers* a candidate to a *later* window, the 60s in-flight sweeper could evict its symbol claim and a re-sent Chartink signal (past the 300s dedup TTL or in a new epoch bucket) could open a parallel entry — because `trades.signal_id` is **not** DB-unique (behavioral idempotency only).

**Design decision (recommend).** **v1 does NOT carry candidates across windows** — every buffered candidate is admitted or rejected *within its own window*; rejection releases the claim + sets the signal status, identical to an FCFS reject. This sidesteps reopening any duplicate window entirely.
- **Claim key = `signal_id`** (already the PK) **+ the existing symbol in-flight claim** — no new mechanism needed for v1.
- IF cross-window carry is ever required (v2), follow the **`gate_state` precedent** (`state_store.py:2659-2705` — signal_id-keyed, "re-add … does not raise") for a persisted claim, and heartbeat the in-flight symbol claim so the sweeper can't evict a still-pending candidate.

---

## P6 — FLAG + SHADOW/ENFORCE BOUNDARY

**Config:** a new `PortfolioAllocatorConfig` (Pydantic `extra="forbid"`, default-off — mirroring `RegimeConfig`/`SRDetectorConfig`): `allocator_mode: off | shadow | enforce` (default `off`), window/candle knobs, `max_portfolio_deployment_pct` (default inert), optional long/short skew, and the enforce **scope** (`v3_only` | `all`, default `v3_only`).

- **OFF:** the fused per-signal path (P1); the buffer/observer/worker are never constructed or never entered → **BYTE-IDENTICAL** to today.
- **SHADOW:** live FCFS governs and places, **timing and orders unchanged**. Each candidate, upon reaching sizing-success (`:904`), emits a **copy** (`ScoredCandidate`: symbol, score, tier, side, intent, `sizing.margin_required`, sector, strategy, signal_id, triggered_at) to the buffer via a **fire-and-forget, fully-guarded** call (never raises, never blocks — the live worker proceeds into its own reserve/place with **zero added latency**). At window close an async observer computes the allocator's **would-be** ranked-admission set against a **start-of-window snapshot** of capital/counts and logs the **REGRET** delta vs what FCFS actually admitted. It calls **neither reserve nor place**. Persist regret rows (reuse a `screener_results`-style table or a JSONL under `data_store/allocator/`).
- **ENFORCE:** PREPARE emits + returns; the admission worker ranks + admits the batch (P3), driving reserve/place. FCFS self-admission is bypassed for the wrapped scope.

**How shadow decides without holding live signals.** The observer only *reads copies* of already-computed facts and runs the counterfactual **after** the live path has moved on — it is off the hot path entirely.

**Honest caveat (→ P10).** The shadow counterfactual is **approximate**: FCFS mutates capital/counts as it runs, so a start-of-window snapshot replay cannot perfectly reproduce the live interleaving. REGRET is a **directional** signal (does ranked crowd out low-score FCFS admits? does FCFS ordering starve high-score candidates?), not an exact predictor. Accumulate several sessions before judging enforce.

---

## P7 — NO-LATE-ENTRY GUARANTEE

- The window is **short** (a few seconds, ≤ the entry candle) and candle-aligned, adding at most ~window-length latency before admission — of the same order as the per-signal screening I/O FCFS already incurs.
- **Placement uses a fresh LTP re-anchor** (FIX-067, `:858-882`): entry/SL/TGT/sizing are re-derived from the live quote at placement, so a few seconds in the buffer does **not** produce a stale entry — the fresh-quote path already protects price integrity.
- Window ≪ the 60s signal expiry, so no candidate ages out in the buffer.
- **Window-length rationale:** long enough to absorb the open-bell multi-scanner burst + queue drain, short enough to preserve momentum. Exact length = a shadow-tuned open question (P10). Under enforce, a candidate admitted at window close is placed immediately (no deferral).

---

## P8 — REGRESSION MAPPING

**Dispatcher callers/side-effects:** `_dispatcher_loop` → `_process_one_safe` → `_process_one` (`:626`). Independent resume paths that also reserve: `continue_from_gate` (`:~1460`, from EntryGate PRICE_HIT) and `continue_from_retest` (`:1794`, from RetestMonitor). **v1 does NOT batch the resume paths** — they are already-deferred single entries; treat their reservations as prior exposure the allocator reads via the primitives (flagged, P10).

**Reservation/gate primitives touched (all reused, none reimplemented):** `fm.reserve` (`:473`), `fm.release` (`:589`), `fm.count_live_reservations[_for_strategy]` (`:1424`/`:1440`), `fm.portfolio_lock` (`:382`, RLock — batch admits under a single acquisition), `fm.get_snapshot` (`:1453`), `risk.approve` (`:177`), `_enforce_strategy_position_cap` (`:589`), `state_store.sector_exposure` (`:732`) / `count_active_positions` (`:583`) / `has_active_position` (`:750`). Secondary consumer: the `/metrics` broker-quota gauges (`:568-582`) — unaffected (same reservation semantics).

**Hot-path concurrency:** the batch admit serializes on the **same** portfolio_lock FCFS uses today → no new lock-ordering, no new global lock. PREPARE stays 5-worker-concurrent. The **WindowBuffer** needs its own small lock (N-writer append / 1-reader drain). FIX-018 `_in_flight_count` semantics preserved (increment before each `approve` — the batch worker does it per-candidate inside the loop). The single admission worker only serializes the ADMIT phase, which already serialized on portfolio_lock → **no worse than today** (confirm open-bell throughput, P10).

**OFF byte-identity proof (the gate):** (a) code inspection that OFF == the current fused path (the buffer is never entered); (b) a unit test asserting OFF admission == current admission for a fixed candidate set; (c) full-suite **clean-vs-dirty stash-diff = zero new failures** (the Step 1-5 method). **Parity:** paper + live both flow through the same signal_processor/reserve seam; the allocator decides admission *above* the paper/live placer split, so shadow/enforce behave identically in both modes.

---

## P9 — ENFORCE CUTOVER + FCFS REMOVAL

**Recommendation: first enforce = an off-market flip (R6 halt-cutover), not a live flag-flip.** The prepare/admit split + a single admission worker is a **structural change to the dispatch model**; flipping while down / pre-open guarantees the first live window of the day is uniformly ranked, with no mid-session FCFS↔ranked mix and no half-migrated window. After several clean enforce sessions (stable regret, zero admission incidents, parity clean), subsequent flag changes can be live. (A live flip *can* be made safe — let in-flight FCFS candidates finish on the old path, apply ranked only to new windows — but the off-market first-cut is cleaner for a hot-path structural change.)

**FCFS removal (R5 — no permanent dual path).** Once enforce is proven over N sessions, DELETE the fused FCFS admit branch: PREPARE→buffer→ranked-admit becomes the sole path, `allocator_mode` collapses (drop `off`/`shadow`), and the observer/regret scaffolding is removed. Until then, `off`+`shadow` exist **only** as migration scaffolding.

---

## P10 — RISKS + OPEN QUESTIONS (for Web Claude / ChatGPT / Rama)

1. **Window length** — the core tuning knob; derive from shadow burst-timing data. Too long = momentum decay; too short = misses the burst → degenerates toward FCFS.
2. **Enforce scope** — V3-only (v1, recommended) vs full-portfolio; and **how a portfolio-wide shared cap spans two admission regimes** during migration.
3. **Shared-concentration definition** — deployed-margin % (proposed) vs max-position-count vs gross notional; and its default value (start inert).
4. **Resume paths (gate/retest)** — confirm they stay OUT of the batch (treated as prior exposure) in v1.
5. **Shadow REGRET metric** — exact definition (crowd-out count, starvation count, score-weighted regret) and acceptance of the counterfactual approximation (P6 caveat).
6. **Residual SECTOR_EXPOSURE TOCTOU** — gate 8 reads DB `margin_reserved` only (not `fund_manager` live reservations), so concurrent same-sector FCFS admits can race a narrow window today. Does enforce's reservation-aware aggregate check supersede it, or do we *also* harden gate 8 to be reservation-aware? (Flag — do **not** change gate 8 silently.)
7. **Long/short balance** — is a directional-skew rule wanted, and defined how? (Default inert if unspecified.)
8. **Admission-worker throughput** — the single admit worker serializes only the ADMIT phase (already portfolio_lock-serialized), but confirm it clears the open-bell burst within the window.
9. **Tiebreak determinism** — equal-score handling `(triggered_at, signal_id)` — confirm acceptable (favors the earlier-triggered candidate).
10. **Candle-source for alignment** — reuse `CandleStore.register_on_candle_close` (needs the relevant token subscribed) vs a wall-clock-aligned monotonic timer. Recommend the timer for scope-independence; confirm.

---

## Acceptance for Step-6b (build)

- G-OFF: `allocator_mode=off` byte-identical (inspection + test + stash-diff).
- G-SHADOW: regret compare runs alongside live FCFS with zero live timing/order change; N sessions of regret data.
- G-ENFORCE: ranked admission governs (V3-only first) on all existing primitives; parity proven; then FCFS removed.

**Nothing built this step.** PLAN only, for Web Claude + ChatGPT + Rama sign-off. After approval, a SEPARATE Step-6b authorizes: default-OFF flag → shadow regret-compare → (off-market) enforce → remove FCFS.
