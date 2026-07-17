# Regime-Ranking Feature · Phase 0 — index-candle ingestion

**Date (IST):** 17-Jul-2026 · **Scope:** DATA ONLY — ingest + store NIFTY 50 + sector-index
candles. **No regime computed, no weighting, no trading-decision change.** Base `7b3ecad`.

---

## Q1 — Why was index data empty? (root cause, confirmed)

BK-1 found NIFTY (token 256265) = 0 rows in `analytics.db.candles`. **Root cause: index tokens
were never in EITHER candle-ingestion path — not a scaffolding bug, just out of scope until now.**

The system has two writers to the `candles` table, and neither ever saw an index:

1. **LIVE tick path** — `data/live_feed.py` (Kite websocket) → `CandleStore.on_tick`
   (`data/candle_store.py`) builds 1-min OHLC → `main.py:3162 _persist_candle` →
   `store.insert_candle`. The subscribed tokens come from the **stock** universe
   (`instrument_cache.token_map()`, `main.py:3180`) plus per-trade subscriptions
   (`order_placer.py:3358`). No index token is ever subscribed → no index ticks → no candles.
2. **BATCH path** — `scripts/fetch_daily_candles.py` (cron 15:40 IST) fetches 1-min candles via
   `kite.historical_data` for the day's **traded** symbols only
   (`_get_traded_symbols` → PROCESSED signals, `:45`) and inserts them
   (`_insert_into_candles_db`, `:220`). Indices are never traded, so never fetched.

The live `candles` table confirms it: 127,777 rows, **285 tokens, all stocks**, all
`interval_sec=60`, 19-Jun→16-Jul. Zero indices.

## Q2 — The existing mechanism (what was extended)

The **BATCH path** (`fetch_daily_candles.py`) is the correct extension point, and the one this
task extends:

- Its entire job is already "fetch 1-min OHLC via the historical API and store it in the
  `candles` table" — exactly what Phase 0 needs, for a non-traded instrument.
- It is **testable today** (market closed): `kite.historical_data` works for past dates
  (`--backfill`), so index rows can be seeded and proven now.
- It is **zero-touch to the live tick/scanner path** — the scanners consume `CandleStore`
  (in-memory ticks) and per-symbol DB reads, never a broad index scan. Extending the batch cron
  cannot disrupt stock ingestion or the boot.

*(The live tick path was deliberately NOT chosen: subscribing index tokens there would add index
ticks to the hot scanner/candle path for no Phase-0 benefit — the regime's live 10:00 decision in
Phase 1 uses `OhlcFetcher.fetch_by_token` on-demand, and calibration [Phase 3] needs stored
history, which the batch path provides. Phase 0's job is precisely to store that history.)*

## Q3 — Index tokens (confirmed live against Kite, 17-Jul)

A read-only `kite.instruments("NSE")` call (segment `INDICES`, 136 instruments) resolved every
token, and `historical_data(256265, …, "minute")` returned **46 clean 1-min NIFTY candles** for
09:15–10:00 on 16-Jul (O=24142→C=24124, **volume=0**), computing a 09:15→10:00 move of −0.075% —
exactly the Phase-1 regime input.

| Index | token | Index | token |
|---|---|---|---|
| NIFTY 50 | 256265 | NIFTY METAL | 263689 |
| NIFTY BANK | 260105 | NIFTY ENERGY | 261641 |
| NIFTY IT | 259849 | NIFTY FIN SERVICE | 257801 |
| NIFTY AUTO | 263433 | INDIA VIX | 264969 |
| NIFTY PHARMA | 262409 | | |
| NIFTY FMCG | 261897 | | |

**Volume-less handling confirmed:** indices return `volume=0`; the `candles.volume` column is
`INTEGER NOT NULL DEFAULT 0`, so index rows store cleanly with no schema change.

## Q4 — Timeframe + retention

- **Timeframe: 1-min** (`interval_sec=60`), matching the existing stock cadence. The regime's
  09:15–10:00 window is trivially derived from 1-min bars; no separate cadence needed.
- **Retention:** the `candles` table has **no prune today** (a pre-existing, out-of-scope item —
  `db_schema_review_15jun2026` suggested one, never built). Index rows add ~46 rows × 10 indices ×
  ~20 sessions ≈ **9k rows/month** (vs ~127k stock rows/month) — negligible; they follow whatever
  retention is eventually added to the shared table.

---

## The build (extend, not duplicate)

**Store decision: reuse the existing `candles` table** (no new table, no migration) — its schema
already holds volume-less rows.

- **`config/index_universe.yaml`** (NEW) — the index tradingsymbols to ingest. Data-driven:
  adding a sector index is a config edit, resolved to a token at fetch time. No hardcoded tokens
  in code.
- **`scripts/fetch_daily_candles.py`** (EXTENDED, additive):
  - `_load_index_universe()` — reads the config; missing/unreadable → `[]` (fail-safe).
  - `_fetch_indices(trade_date, kite, inst_map)` — fetches each index's 1-min candles via the
    **same** `kite.historical_data` call and stores them via the **same** `_insert_into_candles_db`
    path. Indices are **not** written to the traded-stock CSV (they'd be dead keys in the daily
    report, which keys candles by trade symbol). **Fail-safe by construction:** a missing token or
    a failed fetch logs and continues; the function never raises.
  - `_fetch_single_day` calls `_fetch_indices` **first**, before the traded-stock logic — so
    indices ingest every session (including no-trade days), and the stock path is reached and
    behaves exactly as before.

**Fail-safe:** if an index fetch fails, or the config is missing, or a token is unknown → logged
and skipped; the stock ingestion and the cron continue untouched. A simulated failure is tested.

## Stock-ingestion-unchanged proof

`git diff scripts/fetch_daily_candles.py`: the **only deletion in the whole file** is one log
string (`"skipping."` → `"skipping stock candles."`, a clarification now that indices also
ingest). The stock fetch loop, the CSV write, and `_insert_into_candles_db` are **untouched** —
same symbols, same `historical_data` calls, same rows written. Behaviourally byte-identical for
stocks. Unit test `test_fetch_single_day_runs_indices_then_stock_path` asserts the stock path is
still reached after the index fetch.

**Reader-safety** (index rows must not pollute stock consumers): every `candles`-table reader was
checked — all are `symbol`/`token`-filtered except `get_candles_for_date` (daily report), which
builds a `(symbol, hhmm)` lookup map only ever queried by *trade* symbol (always a stock), so
index rows are harmless dead keys. No reader breaks.

**Parity (Rule #5):** `fetch_daily_candles` is a mode-agnostic historical-data cron (no
paper/live branch); index data is identical in both modes. Parity-free.

## Tests

`tests/unit/test_fetch_daily_candles_indices.py` — **7 passed**:
config loads (+ missing-file fail-safe) · index rows stored with `volume=0`/`interval_sec=60` ·
**one-index-failure is fail-safe** (others still stored, no raise) · unknown symbol skipped ·
empty universe no-op · **stock path still reached** after the index fetch.

---

## Regression + deploy

**Full suite (not scoped), ~22:3x IST:** **42 failed / 4850 passed / 3 skipped** (12m51s).
**Zero attributable** — all 42 failures are in the 8 documented PC-env baseline files
(`test_main` ×26, `test_fix135_auto_token` ×6, `test_order_placer_fix061` ×4, `test_instance_lock`
×2, `test_fix129_ntp_check` ×1, `test_fix181` ×1, `test_interactive_startup` ×1,
`test_phase17_batch2` ×1 — [[pc-test-env-hygiene]]); the 42-vs-43 delta from the P1 run is one
fewer of the flaky NTP test (fewer failures). No failure in any file this task touched, and
`fetch_daily_candles.py` is imported by no failing test, so it structurally cannot affect them. My
7 new tests passed (part of the +8 pass delta).

**Deploy (17-Jul ~22:5x IST, off-market, system DOWN — no flatten needed):**
- Commit `4e2df39`, tag `deploy-17jul-regime-phase0`. **No schema migration** — the `candles`
  table already exists in `analytics.db` with `volume DEFAULT 0`; only INSERTs. `trading_system.db`
  schema **v44 unchanged**. (So the "test migration on a backup + pause for Rama" clause did not
  apply.)
- Fresh backups: `pre_deploy_regime_p0_20260717_225742.db` (trading) +
  `pre_deploy_regime_p0_analytics_20260717_225742.db` (analytics, `quick_check=ok`). NIFTY rows
  before = **0** (the baseline).
- Push → post-receive checkout. **PC == origin == VM bare == `4e2df39`**; code-identity delta vs
  tag empty; `_fetch_indices` + `config/index_universe.yaml` present in the deployed tree.
- **End-to-end proof (deployed code, live Kite backfill of 16-Jul):** all **10 indices fetched 375
  candles each → 3,750 index rows stored (10/10)**. Verified in the live DB: **NIFTY 50 (256265)
  now 375 rows** (was 0), 09:15→15:29, low 24050 / high 24186.5, `volume=0`, `interval_sec=60`,
  `is_synthetic=0`, OHLC matching the read-only investigation exactly. **Stock ingestion unchanged:
  tokens 285→295 (+10 indices), rows +3,750 = exactly the index rows — 0 stock rows changed** (the
  backfill's stock re-fetch was all idempotent `INSERT OR IGNORE` dupes).
- **Going forward:** the existing 15:40 cron now fetches indices every trading day automatically —
  no boot dependency, no further wiring. A wider historical seed is available on demand via
  `fetch_daily_candles.py --backfill --from <d> --to <d>` if Phase 3 calibration wants more history.

**Rollback:** revert `4e2df39` (schema-free); the index rows are a targeted
`DELETE FROM candles WHERE instrument_token IN (…)` if ever needed (non-destructive to stocks).

## Not built (Phase 1+ — signal-path, careful loop)

No regime computed, no weighting, no scanner-selection/scoring/order-flow change, no strategy
disabled. Phase 1 (ordinal regime in the V3 module + fail-safe) → Phase 2 (regime→weight scaling)
→ Phase 3 (shadow + daily logging + calibration) → Phase 4 (secondary factors) are **signal-path
careful-loop items**, each design→ChatGPT→implement, not this task.
