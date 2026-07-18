# Regime Phase 1 — INVESTIGATION (READ-ONLY, 18-Jul-2026)

**Nothing was changed, enabled, or pushed.** Read-only inspection of the existing `regime/`
package, its config gate, data source, consumers, and the DB's ability to test the core
assumption. All claims carry `file:line` evidence.

---

## ONE-LINE STEER

**(i) A usable regime module EXISTS and is production-grade** (3 axes, ordinal confidence,
fail-safe, 13 unit tests) — do NOT build a parallel one. **(ii) But the "now that Phase 0
stores index candles it can compute" premise is WRONG: the engine never reads the `candles`
table — it reads the LIVE Kite historical API** (`OhlcFetcher.fetch_by_token` →
`_sr_fetch_fn`), so Phase 0's stored candles do not feed it and never will without a change.
**(iii) Something IS WIRED to its output — flag this loudly:** the V3 chain (`v3_chain_mode:
"shadow"`, deployed/live) consumes regime via `gate_extreme()` **and** `regime_fraction()`
(8 of 40 Context points), as does the PB-01 would-be scorer — all LOG-ONLY, but flipping
`regime.enabled` **shifts the shadow baseline currently being soaked for the enforce
decision**. **(iv) Phase 1 is NEITHER a flag-flip NOR a ground-up build: the existing
`direction` axis answers a DIFFERENT QUESTION** — it is a multi-month *daily* EMA50/200 +
ADX + swing-structure trend classifier needing ≥201 daily candles, not a 09:15–09:59 opening
move. Phase 1 = **add one new ordinal opening-move axis inside the existing module + a window
config + an append-only daily log** (small, additive, shadow-only). **(v) The Q10 core-
assumption backtest IS feasible cheaply** — but index candles currently exist for **exactly
one day** (16-Jul); it needs a ~23-trading-day backfill via the already-proven
`--backfill --from --to` path, then a correlation against 155 filled trades over 23 days.

---

## Q1 — WHAT EXISTS

`regime/` package, from V3 03.02: `__init__.py`, `engine.py` (360 lines), `models.py`,
`runner.py`. Tests: `tests/unit/test_market_regime.py` (13 tests, `:133-263`).

**Three orthogonal axes** (`regime/engine.py:120-124`), each an `AxisResult`:

| Axis | Values | Computed from | Evidence |
|---|---|---|---|
| `direction` | `BULL` / `BEAR` / `SIDEWAYS` | **DAILY** candles: EMA50 vs EMA200 position, fast>slow, EMA slope, swing structure (HH/HL vs LH/LL), ADX | `engine.py:142-188` |
| `volatility` | `HIGH` / `NORMAL` / `LOW` | current ATR ÷ mean true-range baseline, banded by `vol_high_ratio`/`vol_low_ratio` | `engine.py:205-229` |
| `day_type` | `TREND_DAY` / `RANGE_DAY` / `UNDETERMINED` | intraday range÷ATR, directionality, position-in-range, intraday ADX, gated on session progress | `engine.py:233-279` |

**Ordinal confidence → preference multiplier** (`models.py:38-47`):
`HIGH→1.0, MEDIUM→0.5, LOW→0.2, TRANSITION→0.0`.

**Output** `RegimeState` (`models.py:85-110`): `status` (`OK`/`UNKNOWN`), the three axes,
`extreme_flag` (positive-confirmation-only halt, `engine.py:283-317`),
`preference_multiplier` (**headline = the direction axis' multiplier**, `engine.py:133`),
`ts`, `note`.

**Public API** (`regime/__init__.py:22-29`): `build_market_regime(...)`,
`MarketRegimeShadowRunner`, `read_persisted_regime`, `RegimeState`, `AxisResult`.
`compute()` **never raises** (`engine.py:100-138`).

---

## Q2 — ENABLED OR OFF

**OFF.** Config key `regime.enabled` — `config/system_config.yaml:362` (`enabled: false`,
"★ master gate (paper+live). false = not constructed/run; zero pipeline change"), schema
default `enabled: bool = False` (`core/config_loader.py:895`). The gate is honoured in
`main.py:2810` (`_regime_on`) and `:2856` (`if _regime_on:`) — when off the runner is **never
constructed**, so nothing runs today.

**Was the gate "verify the NIFTY token on the VM first"? YES** —
`config/system_config.yaml:364`: `index_token: 256265 # NIFTY 50 Kite instrument_token
(VERIFY live historical access on the VM before flipping enabled)`.

**Does Phase 0 satisfy it? PARTIALLY — and this matters (see Q3).** Phase 0 proved
`kite.historical_data` serves token 256265 (375 one-minute rows for 16-Jul). But the regime
engine makes a **different call**: `interval="day"`, `lookback_days=400`, and it **refuses to
compute below 201 daily candles** (`engine.py:92`, `min_daily_candles = max(ema_slow+1,
2*adx_period+1) = 201`; enforced at `:109-115`). Daily-interval index history at that depth is
**strongly implied but NOT yet proven** on this account. **One command would close it** — a
`day`-interval fetch of token 256265 — and that check belongs in Phase 1 before any flip.

---

## Q3 — ITS DATA SOURCE ⭐ (the crux — the premise inverts here)

**The engine does NOT read the `candles` table that Phase 0 populates.** The chain:

```
engine._fetch()                      engine.py:321-326
  → fetcher.fetch_by_token(token, interval, lookback_days)   sr_detector/fetch.py:85-101
    → _fetch_one(...)                                        sr_detector/fetch.py:114-130
      → self._fetch_fn(token, from_dt, now, interval)        sr_detector/fetch.py:125
      → Candle.from_kite(r)                                  sr_detector/fetch.py:126
```

`_fetch_fn` is `_sr_fetch_fn = _make_sr_fetch_fn(_md_kite, rate_limiter)` (`main.py:2828`) —
the **rate-limited LIVE Kite historical closure**, wired into the regime fetcher at
`main.py:2859-2864`. `sr_detector/fetch.py:92-95` states it outright: the index "is NOT in the
instrument cache, so its token is supplied from config and fetched directly through the SAME
rate-limited fetch_fn (reuse, not a new data path)".

**Consequences (important):**
1. **Phase 0's stored index candles are irrelevant to the live engine.** They are an
   analytics/backtest asset (which is exactly what Q10 needs) — not the engine's feed.
2. **The engine was never blocked on Phase 0.** If the Kite API serves the index token, it
   could always have computed; the block was the *unverified API access*, which Phase 0
   effectively de-risked (modulo the `day`-interval check in Q2).
3. **A "make the regime read stored candles" change is NOT required for Phase 1** and should
   not be smuggled in — the live path stays on the API; the stored candles serve calibration.

The live artifact confirms the current state: `data_store/regime/regime_state.json` holds
`status:UNKNOWN`, `note:"insufficient_index_daily_candles"` (dated 2026-04-16, a test-era
artifact) — the exact `engine.py:109-115` branch.

---

## Q4 — THE WINDOW

**09:15–09:59 is NOT expressible today. There is no window config at all.** The only
time-shaped knobs are `intraday_interval` (validated to `5minute`/`15minute`/`30minute`,
`config_loader.py:913-921`) and `intraday_lookback_days` (`:900`).

* The **direction axis takes no intraday input whatsoever** — its parameter is `daily`
  (`engine.py:142`), fetched at `interval="day"` (`engine.py:108`).
* The intraday series feeds **only** `day_type` (`engine.py:117-124`), and it consumes the
  **whole session so far** (`highs`/`lows` over the entire list, `engine.py:242-250`), gated
  on `session_progress` ≥ 0.2 (`:237`) — i.e. it deliberately *waits out* the opening, the
  opposite of an opening-move measure.

**Where a window would live:** `RegimeConfig` (`core/config_loader.py:884-927`) + the
`regime:` block (`config/system_config.yaml:361-376`) — e.g. `opening_window_start: "09:15"`,
`opening_window_end: "09:59"`. Both are `extra="forbid"` (`:894`), so new keys must be added
to the model *and* the YAML together.

---

## Q5 — THRESHOLDS

**The existing direction axis is already ORDINAL — no % bands.** It is a **vote count**:
`bull`/`bear` each sum four boolean sub-signals (`engine.py:162-163`); `≥2` and a strict
majority decides `BULL`/`BEAR`, else `SIDEWAYS` (`:165-170`); confidence is then ordinal from
the agreement count + ADX (`:172-179`). `models.py:37` states the design intent explicitly:
*"Ordinal FIRST — a calibrated % is a later, data-driven step (do NOT fake precision)."*

**This matches Rama + ChatGPT's "start ordinal, calibrate % later" decision exactly** — the
existing module is the *same philosophy*, already implemented.

**⭐ The downstream ordinal preference map ALSO already exists** —
`core/config_loader.py:1086-1091`:
```
regime_pref_direction:  {"BULL": 1.0, "SIDEWAYS": 0.5, "BEAR": 0.0}
regime_pref_day_type:   {"TREND_DAY": 1.0, "UNDETERMINED": 0.5, "RANGE_DAY": 0.4}
regime_pref_volatility: {"HIGH": 0.85, "NORMAL": 1.0, "LOW": 1.0}
```
at weight `w_regime_preference: 8.0` of the 40-point Context layer (`:1058`,
`config/system_config.yaml:408`). **`{BULL:1.0, SIDEWAYS:0.5, BEAR:0.0}` IS a Bull/Flat/Bear
ordinal tilt** — Phase 2's weighting shape is already scaffolded (for V3/PB-01 scope).

**What would change for Phase 1:** nothing about thresholds. The *input* changes — a new
opening-move axis needs its own ordinal rule (e.g. sign of the 09:15→09:59 NIFTY move with a
flat band), with the band **left uncalibrated** and recorded for Phase 3.

---

## Q6 — FAIL-SAFE (confirmed, quoted)

The contract is stated at `engine.py:9-13` and implemented at three layers:

* **Insufficient data** — `engine.py:109-115`: `if not daily or len(daily) <
  self._min_daily_candles:` → logs *"insufficient index daily candles (%s < %s) — status
  UNKNOWN, neutral preference (NOT a halt)"* → `unknown_state(...)`.
* **Per-axis error** — `engine.py:344-351` `_safe_axis`: any exception → `neutral_axis(default)`
  ("most-uncertain value at TRANSITION confidence (never crashes the cycle)").
* **Any exception** — `engine.py:136-138`: `except Exception as exc:` → *"compute() failed
  (%s) — UNKNOWN, neutral (NOT a halt)"* → `unknown_state`.
* **The neutral state itself** — `models.py:113-125` `unknown_state`: *"status UNKNOWN, every
  axis neutral (multiplier 0) … The system would trade normally — this is NOT a halt."*
* **Fetch failure** — `engine.py:321-326` returns `None`, never propagates.
* **The runner** — `runner.py:83-84`: *"a shadow runner must never die/raise"*; persistence is
  best-effort (`:99-100`).
* **The consumer** — `screening/hard_gate.py:337-345` `gate_extreme`: *"regime None / status
  UNKNOWN → PASS — a broken or disabled regime NEVER halts the book"*, plus
  `except → pass (fail-open)`.

**Missing index candle / holiday / partial session:** all three degrade to the same safe
place. A missing candle or a short history → `UNKNOWN` + neutral (`:109-115`); a holiday means
the runner's loop only computes when `market_windows.is_market_open(now)` (`runner.py:81`);
a partial session → `day_type` returns `UNDETERMINED` at low confidence until
`session_progress ≥ 0.2` (`engine.py:237`). **Verified by tests** T6 missing-data
(`test_market_regime.py:189`), axis-error degradation (`:217`), and
`test_compute_never_raises_on_garbage_fetcher` (`:230`).

---

## Q7 — CONSUMERS ⭐⭐ **BEHAVIOUR-CHANGE RISK — READ THIS**

**The output is NOT dead scaffolding. Two live shadow consumers read it.**

1. **`v3_chain/runner.py`** — takes `regime_runner` (`:54`), snapshots `sig.regime_state =
   self._regime_runner.latest` at signal time (`:104-108`), then uses it **twice**:
   * `gate_extreme(sig.regime_state)` → the `GATE_EXTREME` verdict (`:211-216`);
   * `regime_fraction(cfg, sig.regime_state, as_of)` → the **Context score** contribution
     (`:225`, implementation `:300-330`), worth **8 of 40 Context points**.
2. **`v3_chain/pb01_runner.py`** — the PB-01 would-be scorer: `regime_state =
   self._regime_latest()` (`:153`), `gate_extreme(...)` (`:154`), `regime_fraction(...)`
   (`:198`), and it **records the regime into its output rows** (`:214`).

**Are they live?** Yes — `v3_chain_mode: "shadow"` (`config/system_config.yaml:388`) and the
V3 chain + PB-01 are deployed. **Is the effect on live trading? No** — the same config line
states shadow is *"fire-and-forget background enrichment; the V3 verdict is LOG-ONLY (never
rejects/delays/alters a live order)"*, and `gate_extreme` fails **open**.

**⚠️ THE REAL RISK IS THE SOAK BASELINE, NOT THE ORDER PATH.** Today `regime_runner` is
`None`, so `regime_fraction` returns `(None, unavailable=True)` (`runner.py:305-306`) and the
Context score is computed *without* its regime component. **Flipping `regime.enabled` would
start feeding a real regime into the shadow scores and PB-01 would-be records mid-soak** —
changing the very distribution being accumulated for the enforce decision, and making
pre-flip and post-flip shadow rows non-comparable. **Therefore Phase 1 must NOT flip
`regime.enabled`;** it should compute + log the new opening-move axis on a path that does not
alter `RegimeState` as the V3 chain sees it. If the flip is ever wanted, it is a separate,
explicitly-decided step with a soak-baseline reset.

---

## Q8 — THE GAP (concrete deltas to compute + LOG ordinal Bull/Flat/Bear, zero trading change)

Given Q1–Q7, the missing pieces are small and additive:

1. **A new opening-move axis** in `regime/engine.py` (e.g. `_opening_move(...)`) — reuse the
   existing `self._fetcher.fetch_by_token(index_token, intraday_interval, 1)` path; take the
   first candle's open at/after the window start and the last close at/before the window end;
   emit `BULL`/`FLAT`/`BEAR` ordinally with an uncalibrated flat band. **New axis, not a
   change to the existing `direction` axis** (which is a legitimate, separately-useful
   multi-month trend read and is consumed by the V3 Context score).
2. **Window config** — `opening_window_start`/`opening_window_end` (default `09:15`/`09:59`)
   added to `RegimeConfig` (`config_loader.py:884`) **and** `config/system_config.yaml:361`
   together (`extra="forbid"`), plus the flat-band knob.
3. **A separate shadow entry point** that computes and logs this once per day **without**
   flipping `regime.enabled` (protecting the Q7 soak baseline) — see Q9.
4. **An append-only daily sink** (today's `regime_state.json` is a single overwritten latest —
   `runner.py:89-98` `os.replace` — **not a history**, so it cannot serve Phase 3).
5. **The `day`-interval verification** from Q2 — only needed if/when the existing 3-axis engine
   is ever enabled; the opening-move axis itself needs only intraday candles.
6. **Tests** mirroring the existing style: ordinal boundaries, missing/short window → neutral,
   holiday/partial session, never-raises.

**Explicitly NOT in the gap:** a regime engine (exists), a fail-safe contract (exists), an
ordinal confidence model (exists), a preference map (exists, Q5), a data feed (exists, Q3),
reading Phase 0's candles in the live path (not needed, Q3).

---

## Q9 — SHADOW LOGGING (recommendation)

**Do not reuse `data_store/regime/regime_state.json`** — it is overwritten every cycle
(`runner.py:89-98`), so it holds only the latest state and no history.

**Recommended: an append-only JSONL, one row per trading day**, matching the pattern already
used twice in this codebase — `data_store/v3/forward_shadow_<method>.jsonl`
(`scripts/forward_shadow_record.py:40`), `data_store/v3/pb01_would_be.jsonl`
(`config/system_config.yaml:445`), and `data_store/allocator/regret.jsonl` (`:385`).
Suggested `data_store/regime/regime_daily.jsonl` with
`{date, window_start, window_end, nifty_open, nifty_close, move_pct, regime, band_used, method_version}`.

**Why JSONL over a DB table:** no schema migration (the last several deploys have been
migration-free), it matches the established research-sink convention, and Phase 3 calibration
is an offline read. **Written by a daily cron after close** — the natural sibling of the
existing 15:40 `fetch_daily_candles` job that already ingests the index (`_fetch_indices`,
`scripts/fetch_daily_candles.py:65`), so the candles it needs are already on disk. This also
keeps it entirely off the live trading process, satisfying Q7.

If a DB table is later preferred, `shadow_trades` (`core/schema.sql:925`) and
`daily_symbol_stats` (`:1561`) are the precedents — but that is a migration, and unnecessary
for calibration.

---

## Q10 — THE CORE ASSUMPTION TEST ⭐ (feasible — with one prerequisite)

**Verdict: FEASIBLE, cheaply, using only existing proven tooling — but the index data is not
there yet.**

**What exists (VM, read-only):**
* **Trades side — ready.** `trades`: **155 filled trades across 23 trading days,
  2026-06-15 → 2026-07-16**, with per-day net P&L already aggregatable
  (`SELECT date(entry_time), COUNT(*), SUM(net_pnl) … WHERE qty_filled>0`). Sample days:
  17-Jun `+45.3`, 18-Jun `−10.7`, 22-Jun `+47.9`, 14-Jul `−14.1`, 16-Jul `−2.6`.
* **Index side — ONE DAY ONLY.** `analytics.db candles` holds 131,527 rows over
  2026-06-19 → 2026-07-16 across 295 tokens, but **NIFTY 256265 has exactly 1 day: 2026-07-16
  (375 rows)** — the Phase 0 proof. All 10 index tokens show the same single date.

**The prerequisite:** backfill the index universe across the book's window. **The capability
already exists and is proven** — `scripts/fetch_daily_candles.py --backfill --from 2026-06-15
--to 2026-07-16` (`:9`, `:132-135`), and `_fetch_indices` runs **first and independently** in
`_fetch_single_day` (`:65`, `:153-158`), fail-safe by construction. That is a **data-only**
operation (`candles` insert; `volume DEFAULT 0` already handles volume-less index rows — no
migration), i.e. a Phase 0 re-run over a date range, not new code.

**Then the correlation** is a single query/notebook: per trading day, derive the NIFTY
09:15→09:59 move from the 1-minute index candles, bucket it ordinally (Bull/Flat/Bear), and
join to that day's trade outcomes (net P&L, win rate, and — using the strategy-direction
registry — long-vs-short performance, which is the actually interesting cut given BK-1 found
the book is 91% long).

**Honest caveats to record with any result:**
* **n = 23 trading days** — a directional read, not statistical proof. Three ordinal buckets
  over 23 days leaves single-digit counts per bucket.
* The stock-candle history starts **19-Jun** while trades start **15-Jun**; the index backfill
  can cover the full window, but any stock-candle-dependent cut is short by 2 trading days.
* Outcomes are confounded by scanner mix — BK-1 already showed the losses concentrate in
  intraday longs, so a naive "morning move vs P&L" correlation may be reading scanner quality,
  not regime. Control for strategy/direction.
* This tests *association on the existing book*, not the counterfactual "would tilting weights
  have helped" — that needs the Phase 2 shadow.

---

## RECOMMENDED PHASE 1 SCOPE (recommendation only — nothing built)

**Do Q10 FIRST, before building anything.** It is a data-only backfill + a query, it uses
already-proven tooling, and it tests the thesis that Phases 1–3 rest on. If the morning move
shows no relationship to outcomes even directionally, the whole feature deserves a rethink
before code is written.

**Then Phase 1 proper (small, additive, shadow-only):**
1. Add an **opening-move ordinal axis** to the existing `regime/` module (never a parallel
   module) + the window/band config keys.
2. Log it **daily, append-only**, via a post-close cron sibling of `fetch_daily_candles` —
   **without flipping `regime.enabled`**, so the V3/PB-01 shadow soak baseline (Q7) stays
   intact.
3. Tests in the existing style; fail-safe parity with the current contract.

**Deliberately deferred:** flipping `regime.enabled` (a soak-baseline decision, Q7); the
`day`-interval API verification (only matters for the existing 3-axis engine, Q2); calibrating
the flat band (Phase 3, from the logged data); any weighting or strategy tilt (Phase 2 — note
its scaffolding already exists per Q5, currently V3/PB-01-scoped only).

---

## What was NOT done (per §1/§5)

No code, config, schema, or DB change. Nothing enabled. Nothing pushed. All DB access was
`mode=ro`. The Q10 backfill described above was **not** run.
