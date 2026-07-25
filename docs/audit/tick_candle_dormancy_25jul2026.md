# The dormant live tick→candle path — investigation

**25-Jul-2026. READ-ONLY: no code changed, no config changed, no service touched.**
Tree at `9ee8892`+2 (M-A2 `afefccb`, E1 `4a0c0b2`). Production evidence read from the VM
with `mode=ro&immutable=1` (zero-trace; sidecar check clean).

---

## The three headline answers

1. **Why nothing subscribes: it was NEVER WIRED.** Not removed, not gated off, not
   regressed. `LiveFeedManager.subscribe()` has existed since the initial foundation
   commit and no boot path has ever called it. **MEASURED: the WebSocket has connected
   32 times in production and has never had a single instrument subscribed to it.**
2. **What is broken by it: five consumers, and one of them writes wrong data rather
   than no data.** `ShadowTracker` has produced **141 simulated innings whose every
   closed row exits at its own entry price for exactly 0.00 P&L** — it looks alive and
   answers nothing. The trail, structure-exit, breakeven and candle-persist consumers
   are silent no-ops. Three separate watchdogs that exist to catch this are themselves
   disarmed by it.
3. **Same root as the trail never firing? NO — INDEPENDENT, and both are individually
   sufficient.** The 24-Jul cause (nothing is ever *registered* with `SmartTgtManager`)
   and this cause (no candle event ever *arrives*) live in different files with different
   provenance. Fixing either one alone still yields 0 trails. "Wire the trail" is
   therefore a strictly bigger job than the exit study assumed — and there is a **third**
   independent blocker besides.

---

## C1 — What should subscribe, and why nothing does

### The chain is fully wired except for one link

Everything downstream of the subscription exists and is correctly connected:

```
live_feed._on_ticks          ← ticks from KiteTicker
  → _tick_queue → _consume_ticks (immortal consumer thread)
    → _tick_dispatcher                       main.py:2540, REGISTERED at :2547 ✅
      → candle_store.on_tick(...)            main.py:2544 ✅
      → shadow_tracker.on_tick(t)            main.py:2545 ✅
        → candle_store._timer_loop (60 s)    started main.py:3373 ✅
          → _close_candles → _on_close_cbs
            → _persist_candle                REGISTERED main.py:3391 ✅
            → smart_tgt._on_candle_close     REGISTERED smart_tgt_manager.py:146 ✅
            → structure_exit.on_1m_close     REGISTERED structure_exit_manager.py:150 (flag-off)
```

`candle_store.start()` runs, `live_feed.connect()` runs (`main.py:3373`/`:3394`), the
token map is injected (`:3393`). **The single missing call is
`live_feed.subscribe([tokens])`.** With `_subscribed` empty, `_on_connect` hits
`if not tokens: return` (`live_feed.py:333`) and the socket sits connected and idle.

### Archaeology: absence, asked of git

```
git log -S "subscribe([" --all -- '*.py'     → 3 commits, ALL accounted for:
  bcf03b5  Initial v2 foundation             — DEFINES LiveFeedManager.subscribe()
  de22a04  BL-11                             — ws.subscribe() inside _on_connect (re-subscribe only)
  91ab56a  FIX-061                           — order_placer.py:3393, the exit-retry caller
git log -S "live_feed.subscribe" --all       → 91ab56a only
git log -S "subscribe" -- main.py            → 7 commits, every one an event_bus.subscribe
```

**There is no commit that ever added a boot-path subscription, therefore none that
removed one.** The verdict is *never wired*, and it is not a close call: the only caller
in the repo arrived ~3 months after the class, for an unrelated purpose (FIX-061's
exit-price retry), and subscribes exactly one token.

**Nor is it gated off.** `config_loader.py:1259` reads *"nothing subscribes to the candle
feed"* and looks at grep-level like a deliberate global switch — **it is not.** In context
it is scoped to `StructureExitConfig` and means only "when `structure_exit_enabled` is
false, StructureExitManager is not constructed and *it* does not subscribe." No config
key anywhere gates the live subscription. Premise checked and rejected.

### What *should* subscribe

No design document names an owner — this is the gap. By consumer need, the correct owner
is **whatever opens a position**: the tokens that require live ticks are exactly the open
positions (for trail / structure-exit / breakeven) plus the symbols of active simulated
innings (for ShadowTracker). `order_placer.py:3393` already demonstrates the pattern for
a single token. Nothing generalises it to "the position I just opened".

### Production evidence (VM logs, all history)

| grep marker | count | meaning |
|---|---|---|
| `connected to KiteTicker` | **32** | the socket connects fine, 32 boots |
| `exit_retry_subscribed_to_ltp` | **0** | the only `subscribe()` caller never fired |
| `re-subscribing to N tokens after connect` | **0** | BL-11 path unreachable (empty set) |
| `watchdog started` | **32** | the tick-age watchdog thread starts every boot… |
| `no tick for …s` | **0** | …and has never fired |
| `consecutive interval(s) with ZERO real ticks` | **0** | H-9 starvation guard never fired |
| `candle persist failed` | **0** | consistent with never being invoked |

⇒ **Zero ticks have ever entered the system.** The feed is authenticated, connected, and
carrying nothing.

---

## C2 — What depends on it

### The five consumers

| # | Consumer | Site | State | Independently blocked too? |
|---|---|---|---|---|
| 1 | `_persist_candle` → `analytics.candles` | `main.py:3375` | **0 rows ever** | no — this one is purely dormant |
| 2 | `SmartTgtManager._on_candle_close` (SL trail) | `smart_tgt_manager.py:353` | never called | **yes** — `_tracked` is always empty (24-Jul) |
| 3 | `StructureExitManager.on_1m_close` | `structure_exit_manager.py:150` | never called | **yes** — `structure_exit_enabled: false`, deliberate |
| 4 | `BreakevenManager.on_candle_close` | `breakeven_manager.py:168` | never called | **yes** — never constructed in `main.py` at all (E3) |
| 5 | `ShadowTracker.on_tick` → `_check_hit` | `shadow_tracker.py:259` | never called | **NO — this one is blocked ONLY by the dormancy** |

Plus one read path: `smart_tgt._recompute_on_reconnect` → `candle_store.get_candles()`
(`:796`) can only ever return `[]`, because `_history` is populated exclusively by closed
tick candles.

### ⭐ ShadowTracker is the real damage — it fabricates, it does not merely omit

`shadow_tracker` is `enabled: true`, `max_innings: 3`. Its entire stated purpose is
"Fixes Concern 4: old system reported TGT when SL hit first then price recovered." It
answers that by watching live ticks (SH5/SH6). **Measured on the production DB:**

```
innings, by is_real:                       real=181   simulated=141
simulated exit_reason:                     EOD=130,  NULL=11,  SL=0,  TGT=0
real     exit_reason:                      SL=84, TGT=57, EOD=40
simulated, closed rows:  n=130   exit_price == entry_price: 130/130   avg pnl_pct: 0.0
real,      closed rows:  n=181   exit_price == entry_price:   1/181   non-zero pnl: 180
simulated inning_number spread:            {2: 141}      ← zero inning-3 rows, ever
```

Every number follows from the dormancy:

- Real innings show a healthy `SL/TGT/EOD` mix because inning 1 copies its outcome from
  the **trade record** (`:343-351`), not from ticks.
- For a *simulated* inning, `SL`/`TGT` can **only** be produced by `_check_hit` on the
  tick path (`:259`). **0 of 130** — with real innings hitting a boundary 78 % of the
  time, 0/130 is not a market outcome, it is a dead code path.
- All 130 closed via the EOD event handler (`:445`), which prices the exit as
  `self._last_price.get(symbol, ing.entry_price)`. `_last_price` is filled by `on_tick`,
  so **every close fell back to `entry_price`** ⇒ exit == entry ⇒ P&L exactly 0.00,
  130 times.
- The cascade to inning 3 requires `exit_reason in ("SL","TGT")` (`:398`, `:598`).
  EOD-closed innings never cascade ⇒ `max_innings: 3` has never produced an inning 3.

**This is worse than a silent no-op.** A silent consumer writes nothing and is obviously
absent. This one has written 141 rows of confident-looking zeros. Anyone reading the
`innings` table would conclude "simulated innings mostly ran to EOD" — the truth is the
simulation never observed a single price.

### The disarmed watchdogs — three protections that this defect switches off

1. **H-9 starvation guard** (`candle_store.py:294-308`) exists precisely to make a
   starved feed loud. Its logic is `if to_close: reset / elif synthetic_candidates: warn`.
   Under **total** dormancy both are empty, so **neither branch runs**. It detects
   *partial* starvation (ticks that stop) and is structurally blind to *never-started* —
   which is the actual state. 0 firings in 32 boots.
2. **Tick-age watchdog** (`live_feed.py:695-699`) pre-arms with
   `while ...: if self._last_tick_at is not None: break`. No first tick ⇒ **it never
   leaves the pre-arm loop**. Started 32 times, armed 0 times.
3. **FIX-029 consumer-thread health check** (`_check_consumer_health`) is called *inside
   the watchdog's main loop* (`:707`) — i.e. behind (2). **The consumer-thread death
   detector has therefore never run in production either.** This is a second-order
   casualty worth its own line: a protection disabled by a defect in an unrelated module.

⚠️ `docs/audit/system_security_audit_02jul2026.md:187` asserts the resilience story
*"Websocket drop → auto-reconnect + re-subscribe + tick-age watchdog"*. All three legs
are vacuous in ordinary operation. That claim should be corrected.

---

## C3 — Does this explain the trailing stop never firing?

**No. It is a second, independent, individually-sufficient cause.**

| | Cause A (24-Jul) | Cause B (this report) |
|---|---|---|
| Statement | no trade is ever *registered* with `SmartTgtManager` | no candle event ever *arrives* |
| Mechanism | `order_placer.py:949` sets `order_protocol = self._default_protocol` unconditionally ("OP9: choose protocol" — with no choosing), discarding the 13/16 strategies declaring `CO_PLUS_TGT`; registration at `:2192` is gated on `== "CO_PLUS_TGT"` | `live_feed.subscribe()` never called ⇒ no ticks ⇒ no accumulators ⇒ `_close_candles()` returns at `:349` before firing callbacks |
| Measured | `order_protocol` = LIMIT_TRIPLE on 423/423 trades; `smart_tgt_state` = **0 rows** (re-measured today) | 0 ticks ever; `analytics.candles` rows from `_persist_candle` = **0 / 275,129** |
| Kill test | with `_tracked` empty, `_on_candle_close` computes `matching_ids = []` and returns | with no candle, `_on_candle_close` is never invoked at all |

Neither causes the other; they sit in different modules with different histories (A is a
router that ignores strategy config; B is a call site never written). **Fix A alone → still
0 trails. Fix B alone → still 0 trails.** They share an organisational *pattern*
(built-and-never-wired), not a technical root.

And there is a **third** independent blocker on the same feature: `BreakevenManager`, the
other SL-moving engine, is never constructed in `main.py` (`git log -S breakeven_manager
-- main.py` is empty for all history), so `OrderPlacer` receives `None` — plus its
`_get_sl_broker_order_id` selects a `broker_order_id` column that does not exist.

⇒ **"Wire the trail" is a three-fix job, not one.** This does not reopen the exits thread
(closed 24-Jul; reopen trigger is post-M-S4 only) — it corrects the *cost estimate*
attached to that decision, which is the kind of fact Rama's 5A call depends on.

---

## C4 — Is the historical API an adequate substitute?

**Split answer: YES for analysis, NO for the live path.**

`scripts/fetch_daily_candles.py` runs from cron at **15:40 Mon–Fri** — after the 15:30
close — and pulls `interval="minute"` OHLCV for (a) traded symbols from that day's
PROCESSED signals and (b) the configured index universe. Production holds **275,129 rows,
482 symbols, 19-Jun → 24-Jul**, all `interval_sec=60`, all `is_synthetic=0`.

**Adequate — genuinely, and better than the tick path would be:**

- Post-hoc analytics, the daily report, excursion reconstruction, the 24-Jul MFE study.
- **Volume is TRUE DELTA** here (measured 25-Jul: 40/40 `(symbol,date)` groups
  non-monotonic). The tick path under MODE_LTP carries **no** `volume_traded` at all, so
  `main.py:2543` records 0. The historical API is strictly the better source, and PB-01's
  `confirm_volume_mult` correctly reads it — **Monday's expectations do not change.**
- ⇒ Consumer #1 (`_persist_candle`) is **fully redundant**. Its dormancy is *harmless*,
  and re-enabling it would only add a second, worse-quality writer to the same table.

**Not adequate, and cannot be made so:**

- It runs **once, after the close**. Every live consumer needs the data *during* the
  session, for a position it is holding *right now*. A 15:40 fetch cannot trail a stop at
  11:20, cannot close a simulated inning at 13:45, cannot confirm a structure break.
- It only covers symbols that **already traded**. A position's symbol qualifies only
  after the fact.

**Named gap:** intraday 1-minute OHLC, for currently-held symbols, available in-session.
Nothing in the system provides it today. (`broker_adapter.get_quote` provides a *point*
LTP on demand — used by `EntryGate` and `smart_tgt._startup_ltp_check` — but not bars,
and no consumer polls it for this purpose.)

---

## C5 — What would subscribing cost? (sized, NOT changed)

**The scope is far smaller than "the universe" — this is the main sizing point.**
`instrument_cache.token_map()` holds **2,228 NSE instruments**, and that is what
`candle_store.set_token_map()` receives. But no dormant consumer wants 2,228 tokens. They
want:

- open positions — capped at **`max_open_positions: 5`** portfolio-wide, and
- symbols of active simulated innings — bounded by that day's closed trades.

**Realistic steady-state subscription: ~5–20 tokens.** Against Zerodha's documented
WebSocket limits (3,000 instruments per connection; 3 connections per API key — *external
figures, verify against current Kite docs before acting*) this is ~0.5 % of one
connection's capacity. **Bandwidth and rate limits are not the constraint.** Subscription
is already batched at 50/call (FIX-059).

**MODE_LTP vs MODE_FULL.** `subscribe()` hardcodes `MODE_LTP` (`:182`).

- **MODE_LTP is sufficient** for every dormant consumer's actual need: OHLC bars are built
  from LTP by construction (`Audit 3.4`: candle OHLC is built from LTP only, never from
  `tick["ohlc"]`), and `_check_hit` compares LTP to SL/TGT.
- **MODE_FULL buys two things and costs correctness**: it would populate `bid`/`ask` (so
  `shadow_tracker` could stop being LTP-at-ask optimistic, per Audit #20) and
  `volume_traded`. But `volume_traded` is **cumulative**, and `_Accumulator.update()` sums
  it per tick (`candle_store.py:63`) — so flipping to MODE_FULL **activates M-D1's
  corruption**, which is latent today only because MODE_LTP delivers nothing. M-D1 must be
  fixed *before* any MODE_FULL flip, not after.
- ⇒ **If subscribing: use MODE_LTP.** MODE_FULL is a separate, later decision with a
  prerequisite.

**Two hazards a naive fix would hit** (recorded so the eventual fix does not):

1. **Synthetic candles are sticky.** Once a token receives one real tick it acquires
   `_history`, and from then on `_close_candles()` emits a *synthetic* carry-forward
   candle for it **every 60 s for the rest of the process**, even after unsubscribe —
   there is no eviction but `gc_sweep` (on `InstrumentsRefreshed`) and `MAX_HISTORY`. Each
   synthetic fans out to every registered callback, including a `_persist_candle` DB
   insert. Subscribe-then-forget therefore leaks a permanent 1/min write per token
   touched. (The per-minute scan of all 2,228 tokens is itself trivial — dict lookups —
   and emission is correctly bounded by what was subscribed, not by the map size.)
2. **The H-9 guard flips from silent to loud the moment the first tick lands** — expect a
   burst of "ZERO real ticks" warnings for every subscribed-but-quiet illiquid token.
   That is the guard working as designed; it should be anticipated, not treated as a
   regression.

---

## Recorded, not acted

Per §C6 this is docs-only. Nothing below was built.

- **P1 — ShadowTracker's 141 rows of zeros.** The most defensible immediate action is not
  to wire ticks but to decide whether `shadow_tracker.enabled` should be `false` until it
  can work. It is currently `true` and producing data that is worse than absent.
- **P2 — the three disarmed watchdogs** (H-9 blind to never-started; tick-age watchdog
  never arms; FIX-029 consumer health-check dead behind it). The third is the one to care
  about: it protects a thread unrelated to this defect.
- **P3 — `system_security_audit_02jul2026.md:187`** overstates websocket resilience.
- **P4 — the trail cost estimate** is three fixes, not one (C3).
- **P5 — no owner is defined for subscription.** Whatever fixes this should subscribe on
  position-open and unsubscribe on position-close, with hazard (1) above in mind.

**Cross-refs:** `docs/audit/ma2_ms3_md1_25jul2026.md` (M-D1, where the dormancy was first
noticed) · `docs/audit/trailing_stop_never_fired_2026-07-24.md` (Cause A) ·
`docs/audit/limit_triple_modify_path_2026-07-24.md` (BreakevenManager never constructed).
