# EFFECT-VERIFICATION TELEMETRY — PHASE A FROZEN CONTRACT (01-Aug-2026)

**Ledger item:** debt ledger §XE.3 **#1** (`docs/audit/integrity_audit_2026.md`) — families
**β (structural starvation)** + **γ (algebraic unreachability)**. Family α (BK-8
schema↔ctor) is **OUT of this card** — separate next iteration.
**Status:** `<PENDING — Phase A contract FROZEN; GATE awaiting Rama's approval>`.
⛔ **NO CODE EXISTS YET. Phase B does not begin until Rama relays approval of this
contract.** (Implementation card, GATE after A5.)

**Measured on:** the PC tree at `b4e3dac` whose **code files are byte-identical to the
deployed `297b587`** (verified: `git diff --name-only 297b587..HEAD -- '*.py' '*.yaml'
'*.sh'` → empty, 01-Aug 15:40 IST). Every file:line below is therefore a line in the
audited, deployed code.

**Companion artifact (the C2/C6 single source of truth):**
`config/expected_managers.yaml` — the expected-manager/expected-state registry, frozen in
the same commit. ⛔ **Review rule from this commit forward: a new manager ctor in main.py
REQUIRES a registry entry in the same diff.**

---

## A1 — THE COMPOSITION ROOT, ENUMERATED (main.py ctor sites)

**Single construction path, both modes:** `main()` builds every manager identically in
paper and live; `is_paper` alters only (a) adapter internals (`paper_mode=` main.py:2008,
paper quote provider :2012, paper capital seeds :2205/:2419), (b) **BrokerClockSkewProbe
— the ONE mode-conditional ctor** (live-only, main.py:3384-3386, documented rationale in
code), (c) start-time no-ops (token_monitor no-op in paper, :3435 comment). ⇒ the B2
startup assertion and the census run on the SAME code path in both modes; the registry
carries exactly one mode-conditional entry.

### Constructed unconditionally (every boot, both modes)

| # | ctor site (main.py) | class | role class |
|---|---|---|---|
| 1 | :1851 | `core.state_store.StateStore` | infra |
| 2 | :1856 | `core.events.EventBus` | infra |
| 3 | :1861 | `capital.kill_switch.KillSwitch` | **unit** |
| 4 | :1938 | `core.market_windows.MarketWindows` | infra |
| 5 | :1964 | `broker.order_state_machine.OrderStateMachine` | infra |
| 6 | :1966 | `broker.rate_limiter.RateLimiter` | infra |
| 7 | :1967 | `broker.product_resolver.ProductResolver` | infra |
| 8 | :1968 | `broker.cost_calculator.CostCalculator` | infra |
| 9 | :2001 | `broker.zerodha_adapter.ZerodhaAdapter` | infra (chokepoint counted at order_placer) |
| 10 | :2059 | `core.instrument_cache.InstrumentCache` (`.load`) | infra |
| 11 | :2243 | `alerts.telegram_notifier.TelegramNotifier` | infra — existing effect-trail = `_audit_send` (:398, G5 DEPLOYED 30-Jul) |
| 12 | :2333 | `capital.fund_manager.FundManager` | **unit** (+4 alarm sub-units) |
| 13 | :2428 | `capital.position_sizer.PositionSizer` | **unit** (+2 sub-units) |
| 14 | :2481 | `capital.risk_engine.RiskEngine` | **unit** (+2 sub-units) |
| 15 | :2505 | `data.live_feed.LiveFeedManager` | **unit** (dormancy tripwire) |
| 16 | :2518 | `data.candle_store.CandleStore` | infra — its persist CONSUMER is the unit (#17) |
| 17 | :3399/:3415 | `main._persist_candle` (closure, registered `register_on_candle_close`) | **unit** (dormancy tripwire) |
| 18 | :2524 | `orders.shadow_tracker.ShadowTracker` | **unit** (dormant) |
| 19 | :2551 | `orders.smart_tgt_manager.SmartTgtManager` | **unit** (dormant) |
| 20 | :2576 | `broker.order_monitor.OrderMonitor` | **unit** |
| 21 | :2592 | `orders.order_protocol_co.CoPlusTgtProtocol` | **unit** (dormant) |
| 22 | :2596 | `orders.order_protocol_limit.LimitTripleProtocol` | **unit** |
| 23 | :2605 | `orders.cnc_gtt.CncGttPlacer` | **unit** (dormant → flips 4-Aug) |
| 24 | :2618 | `orders.full_entry_engine.FullEntryEngine` | infra (pass-through router; its effect IS the protocol executes) |
| 25 | :2624 | `orders.order_manager.OrderManager` | infra (thin DB layer, own docstring) |
| 26 | :2633 | `core.mis_blocklist.MisLearnedBlocklist` | **unit** (event-driven) |
| 27 | :2639 | `orders.order_placer.OrderPlacer` | **unit** |
| 28 | :2687 | `orders.cnc_gtt_monitor.CncGttMonitor` | **unit** (dormant → flips 4-Aug) |
| 29 | :2702 | `orders.order_reconciler.OrderReconciler` | **unit** |
| 30 | :2722 | `orders.tgt_retry_manager.TGTRetryManager` | **unit** (dormant) |
| 31 | :2736 | `strategies.loader.StrategyLoader` | infra (boot-loud by design) |
| 32 | :835 (helper) | `capital.strategy_governor.StrategyGovernor` | infra-policy — verdicts already persisted as `signals.status` (covered-existing) |
| 33 | :2808 | `screening.quality_scorer.QualityScorer` | **unit** (+2 sub-units) |
| 34 | :2812 | `screening.step_executor.StepExecutor` | infra |
| 35 | :2820 | `screening.hard_gate.HardGate` | **unit** |
| 36 | :2826 | `screening.secondary_screener.SecondaryScreener` | **unit** |
| 37 | :2844 | `orders.eod_squareoff.EodSquareoff` | **unit** |
| 38 | :2873 | `broker.token_monitor.TokenMonitor` | infra (paper no-op by design) |
| 39 | :2892 | `signals.webhook_receiver.WebhookReceiver` | **unit** (covered-existing: `webhook_audit`) |
| 40 | :3058 | `signals.signal_processor.SignalProcessor` | **unit** |
| 41 | :3318 | `screening.entry_gate.EntryGate` | **unit** (dormant — IA-P2-01) |
| 42 | :3332 | `capital.drift_handler.CapitalDriftHandler` | **unit** (event-driven, +2 rung sub-units) |

### Constructed conditionally — flag ON at `297b587` (in production today)

| ctor site | class | gate (verified value) | role |
|---|---|---|---|
| :2979 | `sr_detector.build_sr_detector(...)` | `sr_detector.enabled: true` (yaml :407) | **unit** |
| :3118-3126 | `allocation.PortfolioAllocator` | `allocator_mode: "shadow"` (yaml :463) | **unit** |
| :3165 | `v3_chain.runner.V3ChainRunner` | `v3_chain_mode: "shadow"` (yaml :472) | **unit** |
| :3214 | `v3_chain.watchlist_capture.WatchlistCaptureWorker` | `watchlist.enabled: true` (yaml :518) | **unit** |
| :3218 | `v3_chain.pb01_runner.Pb01WouldBeRunner` | same | **unit** (event-driven) |
| :3223 | `v3_chain.pb01_entry.Pb01EntryStage` | same | **unit** |
| :3004/:3043/:3160/:3209/:3254 | `OhlcFetcher` ×4 (regime/structure/v3/watchlist) | per-feature | infra |
| :3384-3386 | `broker.clock_skew_probe.BrokerClockSkewProbe` | **`not is_paper` — LIVE-ONLY ctor** | infra (mode-conditional registry entry) |

### Constructed conditionally — flag OFF at `297b587` (expected-ABSENT today)

| ctor site | class | gate (verified value) |
|---|---|---|
| :3015 | `MarketRegimeShadowRunner` | `regime.enabled: false` (yaml :446; refusal D) |
| :3041 | `ZoneCache` | `wait_for_retest_enabled: false` (yaml :427) |
| :3047 | `ZoneWarmer` | same |
| :3263 | `RetestMonitor` | same |
| :3268 | `RetestDiverter` | same |
| :3296 | `StructureExitManager` | `structure_exit_enabled: false` (yaml :532) |

### Expected but NEVER constructed anywhere (the C6 set)

| module | why absent | register ID |
|---|---|---|
| `orders/breakeven_manager.py` — `BreakevenManager` | 0 references in main.py (measured: `grep -c Breakeven main.py` = 0); exits thread CLOSED | X6, IA-P4-01 family |
| `orders/sl_breach_monitor.py` | **zero importers repo-wide** (measured: the only `sl_breach` hit outside tests is the module itself) | IA-P4-01(b) |

---

## A2 — THE FROZEN "ACTED" CONTRACT — one effect-point per unit

**The C1 rule as applied:** one registry entry = one effect-point = one counter. An
entry is either a **constructed manager** (β detection: constructed-but-starved) or an
**audit-named branch inside one** (γ detection: reachable-in-code, algebraically never —
γ is INVISIBLE to manager-level counters, so the card's own γ anchors force exactly these
sub-units, and no others were invented). "Acted" always means the CONSEQUENTIAL effect
(placed / modified / persisted / fired / bound / rejected), never invocation.

### A2.1 — Manager-level units (β)

| unit | ctor | effect-point (file:line) | "acted" means | expected |
|---|---|---|---|---|
| `kill_switch` | main:1861 | `capital/kill_switch.py:859` `_persist_state` — **counting ONLY `SOFT_KILL`/`HARD_KILL` states** (activation sites :552/:652 route through it; INACTIVE clears :311/:378/:703 deliberately NOT counted — one point, one documented predicate) | a kill ACTIVATION persisted (persist-first design) | **ACTIVE** — the 15:15 breaker guarantees ≥1/trading day |
| `fund_manager` | main:2333 | `capital/fund_manager.py:2354` `_write_ledger` (the single writer; 10 internal callers) | an fm_ledger row written | **ACTIVE** — daily INIT guarantees ≥1 |
| `position_sizer` | main:2428 | `capital/position_sizer.py:176` `calculate` verdict return | a sizing verdict produced (incl. rejections) | **ACTIVE** (~20-49/day) |
| `risk_engine` | main:2481 | `capital/risk_engine.py:189` `approve` verdict return | an approve/reject verdict issued | **ACTIVE** (~20-49/day) |
| `signal_processor` | main:3058 | `signals/signal_processor.py:1230` — the `_process_one` dispatch `self._placer.place(` | an approved entry DISPATCHED to placement | **ACTIVE** (zero-trade-day caveat §A2.4) — ⚠️ see GATE-Q4: resume-path dispatch sites :1995/:2272 EXCLUDED (production-unreachable — IA-P2-01, retest off; their awakening surfaces at `entry_gate`/retest units first; same shape as IA-P2-06) |
| `secondary_screener` | main:2826 | `screening/secondary_screener.py:592` `_persist` (P18 trail — all verdict paths route through it, :157-:279) | a screening verdict persisted | **ACTIVE** (thousands/day) |
| `quality_scorer` | main:2808 | `screening/quality_scorer.py:121` `return ScoreResult(` | a composite score produced | **ACTIVE** (thousands/day) |
| `hard_gate` | main:2820 | `screening/hard_gate.py:135` `evaluate` verdict | a V3 hardgate verdict produced (shadow) | **ACTIVE** — if census reads 0, IA-P2-05's shadow leg broke further: investigate, do NOT reclassify |
| `order_placer` | main:2639 | `orders/order_placer.py:3914` — THE single adapter chokepoint `self._adapter.place_order(` (audit P4: one placement pipeline; only call site in the module) | an order dispatched to the broker | **ACTIVE** (zero-trade-day caveat) |
| `limit_protocol` | main:2596 | `orders/order_protocol_limit.py:167` `execute` | a LIMIT_TRIPLE entry sequence executed | **ACTIVE** (423/423 historical; zero-trade-day caveat) |
| `order_monitor` | main:2576 | `broker/order_monitor.py:1207` `_safe_transition` commit | an order state transition committed | **ACTIVE** (zero-order-day caveat) |
| `order_reconciler` | main:2702 | `orders/order_reconciler.py:615` `reconcile_once` return — count += len(actions) (RC20: "the list of ReconciliationActions TAKEN") | a reconciliation action enacted (CHECK1 finalizations dominate) | **ACTIVE** (zero-trade-day caveat: clean cycles return `[]`) |
| `eod_squareoff` | main:2844 | `orders/eod_squareoff.py:320` `_fire` completion (`EodFireResult` produced) | an EOD square-off sequence executed | **ACTIVE** — ≥1/trading day |
| `sr_detector` | main:2979 | `sr_detector/detector.py:254` `_write` | an sr_detector_results row written | **ACTIVE** (~11/day; observes sized candidates) |
| `v3_chain_runner` | main:3165 | `v3_chain/runner.py:272` `fh.write(...)` | a V3 would-be observation appended | **ACTIVE** (shadow accrues daily — IA-P2-05 measured 2k+/mode) |
| `pb01_capture_worker` | main:3214 | `v3_chain/watchlist_capture.py:131` `_capture` — the ROW write | a pb01_watchlist ROW WRITTEN — ⛔ **rows, never `queued=`** (memory `silent_failure_gaps_25jul`: the heartbeat records queued, the row count is the only proof) | **ACTIVE** (12-18/night) |
| `pb01_entry_stage` | main:3223 | `v3_chain/pb01_entry.py:318` `_terminate` (→ `update_pb01_watchlist_status` :322) | a watchlist row driven to a TERMINAL status | **ACTIVE** (statuses reconcile 17==17 daily) |
| `portfolio_allocator` | main:3118 | `allocation/portfolio_allocator.py:144` `_process_window` completion | a shadow allocation window adjudicated | **ACTIVE** (0 only on no-candidate days) |
| `webhook_receiver` | main:2892 | **COVERED-EXISTING** — no new counter; census line derived from `webhook_audit` day-count (WR13 `finally`, `webhook_receiver.py:498-503` — every POST outcome path writes it; IA-P1-04's pre-try hole noted, not scanner-selective) | a POST audited | **ACTIVE** (thousands/day) |

### A2.2 — Event-driven units (0 and >0 both normal — GATE-Q1)

| unit | effect-point | "acted" means | note |
|---|---|---|---|
| `mis_blocklist` | `core/mis_blocklist.py:81` `record_block` (sole path to `_persist_locked` :141 — observation-card-verified) | a broker MIS-block learned + persisted | F5 class; acted 27-Jul, 30-Jul |
| `drift_handler` | `capital/drift_handler.py:135` `_handle` — an enacted outcome (any tier) | a drift ladder action enacted | CapitalDriftDetected is rare by construction (IA-P6-01) |
| `pb01_would_be_runner` | `v3_chain/pb01_runner.py:246` `fh.write(...)` | a PB-01 would-be verdict appended (on confirm) | confirms are rare by design |

### A2.3 — Dormant units: the β/γ tripwires (acted > 0 ⇒ MISMATCH — the finding class going live or a dormancy decision being violated)

| unit | effect-point | why dormant (register ID) |
|---|---|---|
| `entry_gate` | `screening/entry_gate.py:283` `add` | **IA-P2-01** — constructed, started, EOD-cleared; nothing has EVER put an entry into it (divert unwired, EG14/EG15 deferred). `add()` firing = someone wired it |
| `smart_tgt` | `orders/smart_tgt_manager.py:588` `self._adapter.modify_order(` | starved: protocol never assigned (`order_placer.py:949` unconditional default — X6); `modify_order` NEVER executed in production; exits thread CLOSED |
| `tgt_retry` | `orders/tgt_retry_manager.py:306` `retry_tgt_for_trade` outcome | constructed-idle: 26 boot lines, 0 acts (P4.2 census; NOCIL gate: `sl_unplaceable` 0 ever) |
| `co_protocol` | `orders/order_protocol_co.py:87` `execute` | CO never used: 805/805 orders `variety=regular`; 12/15 YAMLs declare CO_PLUS_TGT and :949 discards it (X6, 5-Jul "loaded gun") |
| `shadow_tracker` | `orders/shadow_tracker.py:461` `_start_simulated_inning` (the inning row write) | SH13 `shadow.enabled: false` (yaml :402) — DISABLED 25-Jul (`d31759f`, X4 fabrication; VOID set stays) |
| `live_feed` | `data/live_feed.py:167` `subscribe` | **DORMANT BY DECISION** (`e754c7e`): 32 connects, 0 tokens ever. ⭐ This counter ENFORCES **IA-P1-06** — the dormancy is otherwise un-enforced (`order_placer.py:3393` calls subscribe on the exit-retry path; acted>0 = the decision silently ended) |
| `candle_persist` | `main.py:3399` `_persist_candle` (registered :3415) | X8: must stay dark EVEN IF the feed wires — the 15:40 fetch covers the table with TRUE delta volume; this writer would be strictly worse |
| `sizer.risk_bind` (γ) | `capital/position_sizer.py:407` — the `min()` resolving to `qty_by_risk` | **IA-P3-04**: risk-per-trade CANNOT bind for any legal config (ratio ≥2 structurally); acted>0 = the algebra changed |
| `sizer.live_margin` (γ/α) | `capital/position_sizer.py:298-307` — the live-margin branch (rides the existing `position_sizer.live_margin_used` log event :307 — extend, not duplicate) | **IA-P3-02** / FIX-072: `broker_adapter` never passed by main.py — branch unreachable until wired |
| `risk.sector_cap_bound` (γ) | `capital/risk_engine.py:618` — `projected > self._max_sector_pct * snap.total` branch (observe-log :628-637 / enforce :622 — the BRANCH is the one point, both modes counted) | **IA-P3-05**: the cap cannot be approached at current sizing — the soak is structurally eventless; acted>0 = the soak finally has evidence |
| `risk.daily_loss_gate` (γ) | `capital/risk_engine.py:605-607` — the enforced-reject branch (`return reject(` on breach) | **IA-P6-06**: 0 ever (E4: worst day −₹107 vs ≈₹296 limit) |
| `fm.daily_loss_post_trade` (γ) | `capital/fund_manager.py:1314` `self._on_loss_breach()` | FM7's post-trade half of the DUAL mechanism (memory `dual_daily_loss_mechanism` — same limit, two enforcement points, both registered separately BY NAME) |
| `fm.invariant_violation` | `capital/fund_manager.py:2310` `_handle_invariant_violation` | IA-P6-06: 0 ever |
| `fm.bucket_overflow` | `capital/fund_manager.py:1438` — the H-1 overflow branch | IA-P6-06: 0 ever |
| `fm.commit_hard_kill` | `capital/fund_manager.py:982` — BL-4 `kill_switch.hard_kill(` from `commit_to_used` | IA-P6-06: 0 ever |
| `drift.soft_rung` | `capital/drift_handler.py:236` `soft_kill(` enactment | K-ladder: NEVER fired (28-Jul); single-sample rung |
| `drift.hard_rung` | `capital/drift_handler.py:231` `hard_kill(` enactment | same — HARD has never fired anywhere (Q4 record) |
| `scorer.tier_high` (γ) | `screening/quality_scorer.py:113` `tier = "HIGH"` branch | **IA-P2-03**: HIGH unreachable (threshold 80 > achievable ceiling 65) |
| `scorer.score_gt_ceiling` (γ) | `screening/quality_scorer.py:121` — observation `score > 65` at result return | **G2 tripwire**: >65 is algebraically impossible while 25 pts are dead-at-0 + 20 pinned-at-half; acted>0 = the dead inputs came alive (G2 moved) — the census notices the fix before anyone reports it |
| `cnc_gtt_placer` | `orders/cnc_gtt.py:94` `place_for_fill` | delivery OFF (`delivery_enabled: false`); acted>0 BEFORE the 4-Aug flip = an alarm. ⭐ **FLIP-RIDER: the 4-Aug deploy carries the registry edit → event-driven** (GATE-Q7) |
| `cnc_gtt_monitor` | `orders/cnc_gtt_monitor.py:101` `reconcile` — count += len(actions) | same flip-rider |

### A2.4 — The zero-trade-day rule (stated once, applies to the money-path ACTIVE units)

On a genuinely tradeless day, `signal_processor` / `order_placer` / `limit_protocol` /
`order_monitor` / `order_reconciler` legitimately read acted-0 and WILL appear in
MISMATCH(i). **That line is truthful — "nothing acted today" is exactly G9's question
being answered daily — and it is NOT reclassified away.** (GATE-Q3 offers Rama the
alternative; recommendation is to keep it.)

---

## A3 — THE REGISTRY (C2/C6)

Frozen as **`config/expected_managers.yaml`** in this commit. Design decisions embedded
in it (each a GATE question below):

- **Six states**, not two: `expected-active` · `expected-dormant` (reason mandatory) ·
  `expected-event-driven` · `expected-absent` (reason mandatory) · `infra` ·
  `covered-existing`. The card named two; γ anchors, rare-event components and pure
  plumbing do not fit two states without manufacturing daily false mismatches — the
  exact IA-P9-02 disease this item exists to fight.
- **Mismatch classes the census emits:** (i) expected-active & acted==0 · (ii)
  expected-dormant & acted>0 · (iii) expected-absent but CONSTRUCTED · (iv) constructed
  but UNREGISTERED (B2's echo). `infra`/`covered-existing`/`event-driven` never enter
  the mismatch block.
- **expected-dormant carries the reason AND the register ID** — "dormant-by-decision"
  and "dormant-by-registered-finding" are both encoded in `reason:`; what matters
  operationally is identical (acted>0 must surface).
- **Mode-conditionality is IN the registry** (`modes:` field): `clock_skew_probe` is
  live-only BY DESIGN — not a parity break, a registry fact.
- **Flip-riders:** `cnc_gtt_placer`/`cnc_gtt_monitor` flip dormant→event-driven **in the
  same deploy as the 4-Aug flag flip** — the registry edit rides the behaviour change it
  describes (same-carrier rule).

## A4 — THE EOD EMIT POINT (and why it cannot be the 16:05 report)

**Measured:** `daily_report` is a **cron, 16:05, out-of-process**
(`deploy/cron/trading-system.cron:109`) — it can never see in-process counters. The
census therefore emits **in-process, at `_shutdown()` entry — `main.py:1236`**, invoked
from the single convergence point `main.py:~3681` (`_shutdown_event.wait()` →
`_shutdown(...)`). Every clean stop converges there: the FIX-189 17:35 self-exit
(`_start_eod_self_exit_thread` :1097, event set :1210), SIGTERM/Ctrl-C (:1266), manual
`systemctl stop`. ⇒ census emitted **before** manager teardown (counters warm), on
**every clean stop** (a mid-day manual stop emits a partial-day census labeled by its
emit timestamp), **identically in paper and live**. Emission = stable-ordered structured
log lines + the MISMATCH block via the existing logger (grep-able, ships with the
existing log pipeline; exact artifact form is a Phase B decision inside this constraint).
**A crash day produces no census — by design here; the census heartbeat is B5, explicitly
deferred.**

---

## THE GATE — questions for Rama (approve/adjust; Phase B starts only after this)

| # | question | recommendation |
|---|---|---|
| **Q1** | Registry states beyond the card's two: `event-driven` + `infra` + `covered-existing` added | **Approve** — two states manufacture daily false mismatches |
| **Q2** | γ sub-units at branch level (12 named above, ALL from the card's own anchor list, none invented) | **Approve** — γ is invisible at manager level by definition |
| **Q3** | Zero-trade-day: money-path ACTIVE units mismatch truthfully on tradeless days | **Keep ACTIVE** (the mismatch is information); alternative: event-driven |
| **Q4** | `signal_processor` counts the `:1230` main path only; resume-path sites `:1995`/`:2272` excluded-and-documented (unreachable today; awakening caught by `entry_gate`/retest units) | **Approve** — one effect-point honestly beats three sites two of which are dead |
| **Q5** | Census hook = `_shutdown()` entry (16:05 report is out-of-process — measured, not assumed) | **Approve** |
| **Q6** | `webhook_receiver` covered-existing via `webhook_audit`; `telegram_notifier` = infra (its `_audit_send` trail exists; alert-truth is ledger #9's scope, not #1's) | **Approve** |
| **Q7** | Flip-rider rule: the 4-Aug flip deploy carries the `cnc_gtt_*` registry edits | **Approve** — else Wednesday's census mismatches by design |

**Phase B constraints restated (binding, from the card):** counter = pre-allocated handle,
single integer add, no dict-miss, no allocation, no logging, cannot raise (C4) · B2
assertion fail-fast in dev/paper, **CRITICAL-and-continue in live** · stable census order
(C5) · counters reset per trading day · NO new DB table · no behaviour change · one
commit, revert-clean · validation is composition-truth in BOTH modes, never
unit-construct (IA-XTEST-01) · ⛔ no push before 18:15 · deploy only with the book flat
(Rama flattens manually).
