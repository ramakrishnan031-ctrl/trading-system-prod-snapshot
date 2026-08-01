# EFFECT-VERIFICATION TELEMETRY — PHASE B BUILD RECORD (01-Aug-2026, Saturday)

**Ledger item:** §XE.3 **#1** (families β/γ). **Gate:** cleared — Phase A contract
approved, all 7 recommendations accepted (Rama relayed; ChatGPT conditions binding).
**Status of this record's subject:** `<BUILT — with ONE unit STOPPED for review>`.
⛔ **NOT DEPLOYED. NOT PUSHED.** Deploy-slot decision is Rama's (§7 below).

**Authorities:** `docs/audit/effect_verification_contract_01aug2026.md` (frozen A2) ·
`config/expected_managers.yaml` (frozen registry). Built on the tree whose code files
== deployed `297b587`.

---

## 1. ⛔ THE STOP-CONDITION FINDING — `order_placer`, returned for review (ChatGPT #4)

**The frozen row assumed `orders/order_placer.py:3914` is "THE single adapter
chokepoint" and expected-active. MEASURED during implementation: :3914 is inside
`_emergency_market_exit` (:3849) — the FIX-148/181 emergency path, 0 executions ever
(audit P5.2). The real `adapter.place_order()` sites disperse:** protocols
(`order_protocol_limit.py:204/:375/:459/:573`, `order_protocol_co.py:116/:193`),
`eod_squareoff.py:1280/:1529/:1642`, the reconciler's G5b direct call, and the placer's
emergency :3914. Instrumenting :3914 as frozen would create a **permanent false
MISMATCH(i)** (an always-0 "active" unit) — the exact IA-P9-02 disease.

**Per condition #4: order_placer is left UNINSTRUMENTED; no substitute improvised.**
Phase-A error class: I over-read the audit's "one placement pipeline, one broker
chokepoint" (which is about the ADAPTER being the chokepoint) into "one call line in
order_placer.py"; my Phase-A grep was file-scoped and the audit citation filled the gap
a repo-wide grep would have exposed.

**CONSEQUENCE UNTIL RESOLVED:** the registry expects `order_placer` constructed; nothing
registers it ⇒ **a paper/dev boot FAILS FAST at the B2 assertion naming exactly
`order_placer`** (the assertion doing its job on a genuinely unresolved unit; a live
boot would CRITICAL + continue). The real-boot census demo is therefore blocked until
the review answers.

**REVIEW QUESTION FOR RAMA (with recommendation):** re-point `order_placer`'s effect to
**`place()` — the single public placement entry** (an entry-placement request executed;
zero-trade-day caveat unchanged), keeping `_emergency_market_exit`/:3914 as a **separate
NEW dormant tripwire unit** (`placer.emergency_exit` — the never-fired emergency path is
exactly the kind of thing this census exists to watch). That is 2 registry edits + ~4
code lines once approved. ⛔ Not applied — awaiting the word.

## 2. WHAT WAS BUILT (all against frozen effect-points, verified in-body before edit)

- **`core/effect_telemetry.py`** — the registry's declared consumer: pre-allocated
  `EffectCounter` handles (`inc()` = single integer add on a slot attr — measured
  **27.8 ns/op** vs 14.8 ns empty-call baseline; cannot raise); `handle()` /
  `register_constructed()` = construction registration; `assert_composition()` (B2:
  paper fail-fast / live CRITICAL+continue, both directions incl. ghosts + unknowns);
  `emit_census()` (B4: registry-file order = deterministic; mismatch classes i-iv;
  wrapped so it can never break shutdown). Counters reset by process lifecycle (one
  process = one trading day; documented).
- **42 of 43 counter-bearing units instrumented** at their frozen points across 28
  modules (A2.1 ×17-of-18 + A2.2 ×3 + A2.3 ×21 + candle closure in main). The one
  exception is §1.
- **main.py:** `set_mode()` at the EF-4 recompute; infra registrations (15-name block +
  `instrument_cache` conditional + `clock_skew_probe` live-only + `strategy_governor`
  helper + `ohlc_fetchers` in all five fetcher blocks); **B2 assertion immediately
  before the runtime wait; census emit at `_shutdown()` entry after the flatten drain**
  (drain-time acts count), with the `webhook_audit` day-count derive fn.
- **Two same-behaviour wrappers** where the frozen "verdict return" had many lexical
  exits: `PositionSizer.calculate` (8 returns) and `RiskEngine.approve` (reject-closure
  + final) became pure pass-through counting wrappers over `_calculate`/`_approve` — a
  raise is not a verdict and is not counted. `order_monitor._safe_transition` counts at
  its two success exits (one semantic point, documented in-line).
- **tests/unit/test_effect_telemetry.py** — 12 module-mechanics tests (both assertion
  modes incl. notifier-failure survival, all four mismatch classes, determinism,
  never-raises, frozen-registry parse). ⛔ Deliberately no unit-construct counter
  assertions (IA-XTEST-01).

## 3. DEVIATIONS + TEST-SIDE ADAPTATIONS (each with its reason)

| what | why | class |
|---|---|---|
| `calculate`/`approve` wrappers (rename + pass-through) | the frozen semantic point ("verdict return") has 8 / 6 lexical exits; a wrapper is the ONE lexical point and excludes raises correctly | faithful implementation of the frozen semantics |
| `order_monitor` inc at 2 success exits | the auto-step success return sits inside the except; both exits ARE the one "transition committed" point | documented 2-site single-point |
| `cnc_gtt_monitor` deferred-early-return not counted | "deferred:broker_unavailable" enacts nothing; counting it would false-fire the dormant tripwire on a broker hiccup | freeze-faithful narrowing, in-code comment |
| `tests/unit/test_main.py` autouse fixture neutralizing `assert_composition` | that module MOCKS the manager classes ⇒ nothing registers ⇒ the B2 assertion correctly fail-fasts a mocked composition; composition-truth is not assertable under mocks (IA-XTEST-01) — neutralized, never satisfied-by-mock | test-side (F2 precedent) |
| `test_time_authority_sweep.py` re-pointed `approve`→`_approve` | getsource(approve) now reads the wrapper — the sweep would go vacuous; re-pointing preserves its power (it remains base-red for its own pre-existing reason) | test-side power-preservation |
| missing `_effect_handle` import in `tgt_retry_manager` (caught by the delta gate, fixed) | py_compile can't see a runtime NameError; the first AFTER run's 16-failure cluster was exactly this | in-session fix, recorded not hidden |
| registry `ohlc_fetchers` says "x4"; measured **five** fetcher ctor sites (the 5th in the flag-OFF retest block) | frozen-registry descriptive-string inaccuracy; all five sites register; the string fix rides the next approved registry edit | noted, not silently edited |

## 4. VALIDATION EVIDENCE (what ran tonight vs what needs a market day)

| check | result |
|---|---|
| Regression BASE (pre-edit, 16:47-17:02) | **10F / 5,452P** — the standing base set, in-window |
| First AFTER run | 48F → set-diff exposed the two real defects (§3 rows 5-6) + fixture need; **all fixed in-session** |
| Confirmatory AFTER run | see the stamped result at §4a below (run crossed 18:15 — any `SERVICE_START_CUTOFF` startup-test flip is the KNOWN clock-window flip, named per memory rule, not a regression) |
| 4 tests failing in file-isolation (`TestBl15…paper_mode`, `TestContinueFromGate` ×3) | **fail identically on the BASE tree in isolation** (proven by stash-run) — pre-existing order-dependence, masked by full-suite order on both trees; NOT this change's delta |
| Module tests | **12/12** |
| Census mechanism on the REAL frozen registry | 69 lines, all state classes render; dormant zeros silent; absent set flagged `NEVER-CONSTRUCTED [expected]`; covered-derivation flows; mismatch(i) fires only for un-acted actives (GATE-Q3 semantics visible) |
| Micro-latency | `inc()` **27.8 ns/op** (~13 ns marginal) — no measurable hot-path cost |
| Manual money-path diff read | every non-comment hunk = import / handle-resolve / bare inc / pass-through wrapper; **no control-flow change, nothing raisable added on the hot path** |
| B2 fail-fast on a REAL boot | **weekend-blocked, MEASURED:** `python main.py --mode paper` exits 0 at the is_trading_day gate ("MARKET IS CLOSED / Weekend") BEFORE any manager ctor, and `--dry-run` exits after startup checks, also pre-composition ⇒ **no sanctioned fully-composed boot exists on a non-trading day.** The drill's both-modes behaviour is proven by module tests (fail-fast raise; live CRITICAL+continue; notifier-failure survival); the REAL-boot form lands with the deploy-day evidence — where, until §1 resolves, it would fail-fast naming exactly `order_placer` |
| Census in a REAL boot · known-acting >0 in production · MISMATCH empty on a trading day | **DEFERRED**: blocked by §1 + needs a market day ⇒ the `<DEPLOYED>`→`<VERIFIED LIVE>` stage (first candidate evidence day after deploy) |

### 4a. Confirmatory-run stamp (filled post-run)
**BASE 10F/5,452P (16:47-17:02) → CONFIRMATORY 8F/5,466P (17:44-17:59, both runs fully
pre-18:15 — the startup-window flip never entered either set). ⭐ NEW-FAILURE SET:
EMPTY.** Two GONE (base-red → green): the q9 no-paper-branch test and the
time-authority approve-sweep — **both PASS in isolation on the BASE tree (stash-run
proven)** ⇒ their base-run redness was pre-existing order/session dependence, the same
class as §4's four isolation-artifacts, flipped the other way by collection-order
shift. Not vacuous (the assertions hold against the original code), not this change's
semantics. +14 net passes = the 12 new module tests + the 2 flips.

## 5. PARITY MAP (per touched file)

**One code path, both modes.** Every instrumented module is constructed identically in
paper and live (contract A1); no counter sits behind a mode branch. The three
mode-shaped facts, all pre-existing and registry-annotated: `clock_skew_probe` (live-only
ctor — registered inside its existing `if not is_paper`), `token_monitor` (paper no-op by
design; infra, uncounted), paper-capital seeding (untouched). Side-effects added: none
beyond the counters + boot registration + shutdown census lines. No second telemetry
path: webhook stays on `webhook_audit`; notifier stays on `_audit_send` (infra, no new
counter); `sizer.live_margin` rides the existing `live_margin_used` branch.

## 6. ROLLBACK

One commit, additive-only, `git revert`-clean. Nothing downstream consumes the census
yet. Reverting also removes the B2 assertion (a paper boot then stops checking
composition — the pre-change state exactly).

## 6a. PHASE B-2 ADDENDUM (01-Aug ~18:5x IST) — THE STOP IS RESOLVED

**Approved amendment executed as ONE commit `4959111`:** contract §AMENDMENTS block
(superseded row quoted; the Phase-A error class + the repo-wide-not-file-wide rule
recorded) · registry 70 entries (`order_placer` → `place()`; NEW `placer.emergency_exit`
dormant tripwire @ `_emergency_market_exit` :3849; fetcher string ×4→×5) · 4 code lines
in `orders/order_placer.py` (import + 2 handles + inc at `place()` entry + inc at the
emergency body). **Registry consistency: every counter-bearing entry has one
effect-point; MISSING: NONE — no unregistered constructed unit remains. Module tests
12/12. Census demo: `order_placer: acted 1 | active` · `placer.emergency_exit: acted 0 |
dormant`, zero class-ii/iii/iv.** Money-path hunks: import/handle/inc only.

**Regression (vs the 8F confirmatory baseline), stated exactly:** 10F/5,464P. The q9
streak test left the set; **3 entered** (`test_consecutive_losses_at_limit`,
`test_bugb_restart_floor_via_pending_fill`, `test_daily_delivery_cap_rejects_6th`) —
**all 3 PASS in isolation on this tree**, all sit in the consecutive-losses/risk_engine
fixture family B-2 never touched, and that family's q9 streak member has now oscillated
red→red→green across three runs with no relevant tree change — **run-to-run
nondeterminism in a shared-state family, the same pre-existing class stash-proven in
§4a, not the B-2 delta.** ⚠️ Honestly flagged, not smoothed: a calm-machine confirm
rerun is OWED alongside Monday's composition check before the deploy-slot decision uses
this gate.

**MONDAY OWED (ChatGPT deploy precondition):** PC PAPER boot on a trading day —
composes fully; **B2 assertion must PASS clean** (no fail-fast, no unknown/ghost); a
mid-day manual stop on the PC copy may witness one real partial-day census. ⛔ PC only —
the VM observation run stays untouched on `297b587`.

## 7. ⛔ OPEN ITEMS CARRIED OUT OF THIS BUILD

1. ~~**§1 — the order_placer effect-point review**~~ ✅ **RESOLVED — Phase B-2
   `4959111` (§6a); paper boots no longer fail-fast on it.**
2. **The deploy slot** — ⛔ NOT pushed tonight ON PURPOSE: `main` is frozen unpushed so
   **Mon 3-Aug boots the regression-tested `297b587`** (observation day must stay
   single-variable). Options for Rama: ride the Mon-evening slot WITH the flip stack
   (Tue boots flip+telemetry — two variables on flip day), or Tue evening → Wed boot
   (keeps the flip single-variable too; census's first live day = Wed). **This file
   does not decide it.**
3. **GATE-Q7 flip-rider unchanged:** the 4-Aug deploy carries the `cnc_gtt_*`
   dormant→event-driven registry edits (also stamp the "x4"→"x5" fetcher string then).
4. **B5 census heartbeat** — deferred by the card, still deferred.
