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

*Read-only. Baseline: 20-Jul, `4763041`, schema v44. No `analytics.db` write occurred.*
