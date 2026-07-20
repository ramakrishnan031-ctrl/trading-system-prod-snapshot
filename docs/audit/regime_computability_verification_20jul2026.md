# Regime runtime verification — 20-Jul-2026 · VERDICT: COMPUTABLE TODAY

**ChatGPT's open ask:** prove by *observation* that the regime engine reads live daily Kite history,
then answer the question that falls out of it — **would it compute today?**

**Answer: yes.** At 19:35 IST on the VM, with a valid token, the engine fetched **271 daily NIFTY
bars** (it needs 201) and returned **status OK** with a real three-axis verdict. The April
`insufficient_index_daily_candles` state file is **not** a record of a data shortage — it is what a
run with **no market-data handle** produces, and that was reproduced exactly under control.

**Nothing was enabled.** `regime.enabled` is still `false`; config md5 unchanged before and after
(`33c215971dbb1155ffdd92bd7384e49d`). No file was written, no service touched.

---

## 0. Method — and why it is observation, not trace

A static read already said "Kite-only". The standing `pragma_table_info` lesson is that *a listing
and a behaviour are different things*, so the claim was re-established by running the code:

- The engine and fetcher are the **real production classes**
  (`regime.engine.MarketRegimeEngine`, `sr_detector.fetch.OhlcFetcher`, both loaded from
  `/home/ubuntu/systems/trading-system/`).
- The two production closures — `_build_market_data_kite` (`main.py:388-413`) and `_make_sr_fetch_fn`
  (`main.py:416-433`) — were **lifted out of `main.py` by AST and exec'd**, *not* re-typed and *not*
  imported. Importing `main.py` was deliberately avoided: that path can trigger migration-on-open.
- The real `broker.rate_limiter.RateLimiter` was used. (A first pass stubbed it after a wrong import
  path; the run was repeated with the real one and produced identical results. Both runs are reported.)
- The fetch closure was **wrapped** to record every call's arguments and row count.
- A **`sqlite3.connect` tracer** was installed *before any project import*, attaching
  `set_trace_callback` to every connection opened during `compute()`. This is what makes "no candles
  read" an observation rather than an assertion.
- `engine.compute()` was called directly. **The engine never persists** — only
  `MarketRegimeShadowRunner._persist` (`runner.py:74/89`) writes `regime_state.json`, and the runner
  was never constructed.

Harness: `regime_observe.py` (scratchpad; transferred by base64, run from `/tmp`, removed after).

---

## 1. B1 — the fetch path, by observation

**Fetcher identity:** `sr_detector.fetch.OhlcFetcher`, file
`/home/ubuntu/systems/trading-system/sr_detector/fetch.py`.

**What the engine actually issued** (two calls, both through the same rate-limited closure):

| # | instrument_token | interval | from → to | span | rows returned |
|---|---|---|---|---|---|
| 1 | **256265** | **`day`** | 2025-06-15 → 2026-07-20 | **400 d** | **271** (2025-06-16 → **2026-07-20**) |
| 2 | 256265 | `5minute` | 2026-07-19 → 2026-07-20 | 1 d | 75 (09:15 → 15:25 today) |

This matches the specified arguments exactly: `token=256265`, `interval="day"`, `days=400`. The
second call is the day-type axis's intraday fetch (`intraday_interval`/`intraday_lookback_days`).

**Reads of `analytics.candles` on that path: NONE.**

| observation | run A (live) | run B (no handle) |
|---|---|---|
| `sqlite3.connect` calls during `compute()` | **0** | **0** |
| SQL statements executed during `compute()` | **0** | **0** |
| any statement mentioning `candles` | **false** | **false** |
| DB-ish attributes on the fetcher instance | **none** | **none** |

`OhlcFetcher.__init__` takes `(fetch_fn, instrument_cache, lookback_days, logger, now_fn,
cache_ttl_sec)` — **it is never given a database handle at all.** Its only source of candles is the
injected `fetch_fn`, and that closure's only source is `market_kite.historical_data(...)`.

Note in passing: the index path uses `fetch_by_token()`, which skips `_resolve_token()` entirely, so
it does not touch the instrument cache either — consistent with the index not being in that cache.

---

## 2. B2 — is there a fallback? **No.**

The failure path was exercised, not reasoned about. Run B built the identical engine over
`_make_sr_fetch_fn(None, rate_limiter)` — the "no market-data kite handle" condition:

- fetch call issued: `256265 / day / 400 d` → **0 rows**
- engine log: `regime: insufficient index daily candles (0 < 201) — status UNKNOWN, neutral preference (NOT a halt)`
- state: `UNKNOWN`, all axes TRANSITION, `preference_multiplier` 0.0, note `insufficient_index_daily_candles`
- **`sqlite3` connects: 0. SQL statements: 0.**

So when the Kite fetch yields nothing, the engine **does not drop to the candles table** — it returns
the fail-safe UNKNOWN. There is no second data path to fall back to.

The chain that swallows the failure, for the record:

```
main.py:424-425   if market_kite is None: return []        # returns [], does NOT raise
fetch.py:126-129  candles = [...]; return candles or None  # [] -> None
engine.py:321-326 _fetch(...) -> None                       # no exception, so no warning here
engine.py:109-115 not daily -> unknown_state("insufficient_index_daily_candles")
```

---

## 3. B3 — the candle-backfill hypothesis is **CLOSED**

**Stated plainly, as ChatGPT's condition requires: the regime engine never reads the `candles` table.
No backfill of `analytics.candles`, at any range, can unblock regime (#07).** The Q10 Part B backfill
is moot for this purpose — not because of a retention argument, but because the consumer does not
exist. This is now established at runtime in both the success and the failure case.

---

## 4. B4 — does it fetch ~400 daily NIFTY bars right now?

**Yes — 271 bars, which is what a 400-calendar-day window contains** (≈400 × 5/7 trading days, less
holidays). The requirement is `max(ema_slow + 1, 2 × adx_period + 1)` = `max(201, 29)` = **201**.

**271 ≥ 201 ⇒ the engine computed.** Actual output, today:

| field | value |
|---|---|
| `status` | **OK** (note: `null`) |
| `direction` | **BULL / LOW**, multiplier **0.2** |
| `volatility` | NORMAL / MEDIUM, multiplier 0.5 |
| `day_type` | UNDETERMINED / MEDIUM, multiplier 0.5 |
| `extreme_flag` | false |
| `preference_multiplier` (headline = direction) | **0.2** |

Direction evidence: price 24,239.5 · EMA50 23,984.44 · EMA200 24,545.93 · ADX 10.19 (not trending) ·
bull_votes 2 (slope_up, struct_up) vs bear_votes 1. Price is above the fast EMA but below the slow
one — a genuinely mixed picture, which is why the axis lands at **LOW** confidence. Volatility ratio
0.955 (ATR 246.17 vs baseline TR 257.67). Day-type is UNDETERMINED at session_progress 1.0 because
the range was only 0.53× ATR with directionality 0.38.

One CRITICAL is logged by design and is **not** an error: `exchange-status feed MISSING — extreme_flag
left FALSE (absence of data is NOT a confirmed halt)`. `exchange_status_fn=None` is what `main.py:2869`
passes in production too.

---

## 5. ⭐ What the April state file actually recorded

`data_store/regime/regime_state.json` (PC) reads:

```json
{"date": "2026-04-16", "regime": {"status": "UNKNOWN", ..., "note": "insufficient_index_daily_candles"}}
```

**Run B reproduced that note exactly, with no token.** Three further facts point the same way:

1. **The VM has no `data_store/regime/` directory at all** — not an empty file, no directory. The
   regime engine has **never run on the VM**. The April artifact is PC-side.
2. The PC's `zerodha_token.json` is dated **21-Jun-2026** — stale by a month, and Kite access tokens
   expire ~05:00 daily. A PC run in April would have had no usable handle unless one was refreshed
   that day.
3. `_build_market_data_kite` returns `None` when the token file is absent *or* unparseable, and
   `_make_sr_fetch_fn` then returns `[]` **silently**.

**Conclusion: the April file is best explained as a missing-handle run, not a data shortage.** It was
being carried as evidence that regime "cannot compute". It is not that evidence.

### ⚠️ Latent observability defect (recorded, NOT fixed)
`insufficient_index_daily_candles` is emitted for **two distinguishable conditions** — "Kite served
fewer than 201 bars" and "there was no Kite handle at all" — and the persisted state cannot tell them
apart. Only the log line carries the count that discriminates (`0 < 201` vs e.g. `150 < 201`). This is
**LATENT**: regime is off, so nothing live depends on it. Queued, not fixed tonight, per scope.

---

## 6. B5 — the verdict

> ### **COMPUTABLE TODAY.**
> It works. It has simply not been run since April — and the April record is a missing-handle
> artifact, not a verdict on the data.

Not *blocked on wiring*: the fetch path, the closure, the token, and the engine all functioned
end-to-end without a single change. Not *blocked on data/permission*: Kite served 271 daily bars and
75 intraday bars for token 256265 on this account, on demand.

**This also settles the standing "the NIFTY index token must be VM-verified" item**, which had never
actually been done. `config/system_config.yaml` carries the comment *"index_token: 256265 — NIFTY 50
Kite instrument_token (VERIFY live historical access on the VM before flipping enabled)"*. **Verified
on the VM, 20-Jul-2026: 256265 serves both `day` and `5minute` history for this account.**

---

## 7. B6 — what this does and does not unblock

**Does:** it moves #07's gate from **"cannot compute"** to **"needs forward data"** — a different and
much smaller problem.

**Does not:**
- A computable regime **does not determine #07.** That still needs roughly **2.2 months of forward,
  within-cell** observations. Today's run is one point-in-time snapshot, not a sample.
- **The live consumer is unbuilt.** Regime is emitted into the V3 **shadow** chain and gates nothing —
  no order, score, size or kill-switch reads it. `preference_multiplier` is a *preference*, not a
  permission, and it is fail-safe by construction.
- **The #10 coupling stands.** Admission is first-come-first-served on a 20 s `min_gap` throttle that
  is blind to quality, so a score preference the throttle ignores is **decorative**. Regime cannot
  change what gets admitted until that is addressed.

**No recommendation is made on #07, D2, D3 or #10** — out of scope by instruction.

### The retrospective harness (DESCRIBED ONLY — deliberately NOT built)
Now that the fetch is known to work, a per-trade-date regime could be reconstructed retrospectively:
for each historical trade date *D*, fetch `token=256265, interval="day"` for the 400 days ending at
*D*, truncate to bars `< D`, and run `MarketRegimeEngine.compute()` with `now_fn` pinned to *D*. The
daily axes (direction, volatility) are reconstructible this way because Kite serves the history; the
**day-type axis is not**, since it needs that day's intraday bars, which are only retained for the
recent window. Caveats before anyone builds it: it must never write `regime_state.json`; it is
~1 historical call per trade date and must ride the rate limiter; and a retrospective label is **not**
a forward observation — it cannot substitute for the 2.2 months #07 needs, only characterise the
existing book. **Not built. Not authorised.**

---

## Appendix — safety ledger for this batch

| control | before | after |
|---|---|---|
| `regime.enabled` | `false` | **`false`** (untouched) |
| `config/system_config.yaml` md5 | `33c215971dbb1155ffdd92bd7384e49d` | **identical** |
| VM `data_store/regime/` | does not exist | **still does not exist** |
| `trading-system.service` | `inactive` (designed 16:00 `eod_self_exit`) | **`inactive`** |
| PC `data_store/regime/regime_state.json` | sha256 `27AF3813…5F0E72C` | backed up to scratchpad, **unmodified** |
| harness on VM | — | `/tmp` only, **removed**; deployed tree never written to |

Read-only throughout. No code, config, schema, flag, cron or systemd change.
