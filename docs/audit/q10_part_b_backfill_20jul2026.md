# Q10 Part B — the Kite historical backfill: feasibility + a blocking finding — 20-Jul-2026

**Read-only investigation, off-market (post-squareoff, book flat). NOTHING written to `analytics.db`; no
backup created (the backfill was NOT run — see §A2).** Deploy state after tonight's §B push:
PC == origin == VM bare == `4763041`, schema v44.

## §A1 — CAN IT RUN WITHOUT RAMA? **YES — feasible on tonight's token, no new credentials, no subscription blocker.**
- `data_store/session/zerodha_token.json` present, refreshed **08:15:02 today** (Kite tokens expire next
  morning → live tonight). `fetch_daily_candles.py:258-281` consumes **only** that token (`access_token` →
  `KiteConnect.set_access_token`). No separate credential.
- **The historical-data API already works for this account** — `analytics.candles` holds 131,527 rows
  (06-19→07-16), and `fetch_daily_candles` cron ran **SUCCESS** as recently as 07-17 (15:40 + 22:58). So
  `kite.historical_data()` (the subscription-gated call) is actively succeeding → the paid historical add-on
  is live. **Do NOT ask Rama for anything on the credential axis.**

## §A2 — ⛔ BLOCKING FINDING: the documented backfill is NOT index-only, and its two goals are mutually exclusive
Proved from the code, not the doc (per the 0.1 discipline):
- **`fetch_daily_candles.py` has no index-only mode.** `_fetch_single_day` (`:153-219`) calls `_fetch_indices`
  **then** fetches **stock** candles for every symbol with a PROCESSED signal that date (`_get_traded_symbols`,
  `:113-126`), writing both via `_insert_into_candles_db` → **`INSERT OR IGNORE`** (`:306`).
- `INSERT OR IGNORE` ⇒ an existing candle row can **never** be altered/overwritten. So **corruption of
  existing stock rows is structurally impossible.** The only possible stock effect is *adding* new rows.
- **06-15, 06-16, 06-17, 06-18 have PROCESSED signals (9 / 2 / 17 / 15) but ZERO current candles** (the
  candle store starts 06-19). So `--backfill --from 2026-06-15` **would add stock candles for those 4 days.**
- ⇒ **The record's "index-only, 0 stock rows changed" is inaccurate**, and the "23 index days" target is
  *unreachable without adding stock rows*: 23 days needs the 06-15 start, and 06-15 adds stock. The two
  documented acceptance criteria **contradict each other** given this script.

**Current state (before, read-only fingerprints):**
- candles: 06-19→07-16, **131,527 rows**. STOCK (volume>0): **122,389 rows** — fingerprint
  `Σ(o+h+l+c)=279,255,804.23`, `Σvol=3,986,009,903`. (Index history is sparse/incomplete — the true indices
  are the 10 `NIFTY */INDIA VIX` symbols; a `volume=0` split is contaminated because many illiquid stock
  minutes are also volume 0.)
- trading days 06-15→07-16 ≈ 23-24 weekdays; 06-19→07-16 ≈ 19-20.

## Why it was NOT run tonight
1. **The plan needs a decision** (below) — I will not write data against a refuted premise.
2. **The run re-fetches ALL stock candles** for ~19-24 days × ~100-150 symbols/day (`time.sleep(0.35)` each)
   ≈ **many minutes / thousands of API calls**. That should **not race the 16:30 power-down** — an SSH drop
   mid-write is exactly the kind of interruption a data backfill must not have (recoverable via the pre-flight
   backup, but not worth the risk). It belongs in a clean off-market window.

## The decision (no recommendation on strategy; this is backfill mechanics)
| Option | Effect | Trade-off |
|---|---|---|
| **(a) `--from 2026-06-19 --to 2026-07-16`** | index-fill over the stock-covered window; stock re-fetch = `INSERT OR IGNORE` ≈ 0 net (verify) | ~19-20 index days, not 23; honors "0 stock rows changed" as closely as the script allows |
| **(b) `--from 2026-06-15` (documented)** | adds index for all 23-24 days **and** adds stock candles for 06-15→18 | benign additive stock (never corrupting), but violates "index-only / 0 stock changed" |
| **(c) add an `--index-only` flag** | a true index-only backfill for any range | a code change → separate careful-loop item, not tonight |

## §A5/A6 — what it will and won't deliver (once run)
- It backfills the **index candle history** the regime module needs to compute regime on past days →
  makes the **D2/D3 regime-confound split computable** over the window (BK-1's "regime confound unmeasurable —
  no index candles" gap closes for these dates).
- It does **NOT** determine #07. Q10's verdict stays **"NOT DETERMINABLE at n=23"**; the determination needs
  **~2.2 months of forward within-cell** data. The backfill produces the tercile bands and *starts the clock* —
  it is not an answer. D2/D3/#07 remain Rama's.

---

## §A/§B RESULTS (20-Jul, after (a) was chosen) — ⭐ THE BACKFILL IS MOOT; (a) WAS NOT RUN

**§A5 VERDICT: NOT COMPUTABLE via this backfill — and it never could be, at any range.** The whole
premise was wrong on the data path, proved from code (`regime/engine.py`, `sr_detector/fetch.py`):

- The regime engine's direction axis needs ~**201 DAILY** NIFTY candles (`engine.py:80,92,108` →
  `daily_lookback_days=400`, `min_daily_candles=max(ema_slow+1, 2·adx+1)=201`, `interval="day"`).
- It fetches them via `OhlcFetcher.fetch_by_token(256265,"day",400)` → a **Kite `historical_data`
  closure** (`fetch.py:12-13,125,133`) — **live from Kite, NOT the `candles` DB table.**
- The Q10 backfill (`fetch_daily_candles.py`) writes **1-minute** candles (`interval="minute"`) to
  the `candles` table. **Triple mismatch:** wrong granularity (1-min vs daily), wrong quantity
  (23 days vs 201+), **wrong source entirely** (a table regime never reads).
- Confirmed by the live state: `data_store/regime/regime_state.json` last computed **2026-04-16**,
  `status=UNKNOWN`, note **`insufficient_index_daily_candles`** — never a real classification.

⇒ **(a) was NOT run.** Adding 1-minute index candles to a table the regime engine never reads would
have reported "success" and left D2/D3 exactly as blocked — the paper-closure §A5 warned about.

**§A2 stock invariant:** N/A — no write occurred. (And per the earlier finding it was moot anyway:
`INSERT OR IGNORE` makes existing-row corruption structurally impossible.)

**§B — (a) vs (b) cost, answered for ChatGPT:**
- Trades in 06-15→06-18 (the days (a) excludes): **9 + 3 + 20 + 26 = 58 trades = 15.7%** of the
  369-trade book (16% of the 361 baseline). Not marginal *as trades*.
- **But the cost for the regime confound is ZERO** — neither range feeds regime (it reads Kite daily
  bars, not the candles table), so 19-20 vs 23-24 backfilled days changes **nothing** for Q10/#07.
- **D3** (band inversion) is **forward-shadow-gated, not candle-gated** — untouched by any of this.
- **D2** — the regime-confound split needs a *daily-regime-per-trade-date* computation, which this
  backfill does not provide at any range.
- ⇒ **(b) and (c) are not worth doing** for regime: the extra days/flag change no verdict, because the
  candles table is the wrong artifact. That **closes** ChatGPT's question rather than leaving it open.

**What would actually make the regime confound measurable:** run the *built* regime engine
**retrospectively per past trade-date** off the ~400-day **daily** NIFTY history (Kite-available;
`historical_data` works) + verify the index token is wired to the engine's fetcher + enable/persist.
That is a small **harness**, not a candle backfill. (No recommendation — a separate careful-loop item.)

*Read-only. Baseline: 20-Jul, `8345cc0`, schema v44. **No `analytics.db` write occurred** — (a) was proven moot and not run.*
