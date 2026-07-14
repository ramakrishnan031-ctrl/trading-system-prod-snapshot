# W2 — STAGED PB-01 SHADOW FLIP (config only) + API-load pre-check (14-Jul-2026)

**Status: STAGED, NOT APPLIED.** The config files are UNTOUCHED. This is the exact change to ship
WITH tonight's deploy (off-market, on Rama's GO) plus the mandatory broker-rate-limit pre-check.

## The staged config change (2 flips + 1 explicit no-change)

`config/system_config.yaml`:
```diff
-  v3_chain_mode: "off"        # 10a chain on the 15 live strategies (LOG-ONLY when shadow)
+  v3_chain_mode: "shadow"     # fire-and-forget background enrichment; verdict LOG-ONLY, never alters an order

 watchlist:
-  enabled: false              # capture worker + entry-stage monitor NOT constructed
+  enabled: true               # PB-01 EOD capture + next-morning entry stage (SHADOW, would-be records only)
```

`config/strategies/pb01_breakout_retest.yaml`:
```
 enabled: false               # ← STAYS false. FAIL-CLOSED. PB-01 must NEVER place an order.
```

**Why PB-01 `enabled` stays false:** `strategy_will_trade()` returns WON'T-TRADE under every
trade_type×force while `enabled:false`, so any firing is `_PipelineReject("STRATEGY_CONTROL")` before
sizing/placement (G-NO-ORDER, structural). The watchlist entry stage produces **would-be JSONL records
only** (`data_store/v3/pb01_would_be.jsonl`) — no position, no capital, no CNC/GTT. It tests the single
most promising hypothesis (enter the pullback, never the breakout candle — the opposite of the
band-inversion "buying already-extended moves") at ZERO order risk.

---

## MANDATORY PRE-CHECK — API load, quantified

**Mechanics (measured from the code, not guessed):**
- Entry stage = a **daemon poll thread**, off the hot path (`pb01_entry.py:_run`; the code comment is
  explicit: "never a fetch inside `_process_one`").
- Cadence: `poll_interval_sec = 20.0` → `poll_once()` every 20 s. Each `poll_once` loads **all PENDING**
  watchlist rows and does **1 historical fetch per pending symbol** (`_fetch_5m` →
  `fetcher.fetch_interval(symbol, "5minute", lookback_days=1)`).
- One-time per symbol (first evaluation, cached in `_static`): daily + 30-min fetch = **2 historical**.
- Window: `entry_start 09:20` → `entry_end 11:00` = 100 min. A symbol drops out of the poll the moment
  it reaches a terminal state (CONFIRMED / SKIPPED_GAP / INVALIDATED / EXPIRED_WINDOW), so N decays.

**The formula:** sustained load = **N / 20 historical fetches/sec** (N = PENDING symbols), + a one-time
2N drained at the bucket rate. Worst case N = full watchlist (no early terminations).

| Watchlist N | Sustained historical req/s (N/20) | vs `historical` bucket (2/s) |
|---|---|---|
| 5  | 0.25 | 12% |
| 10 | 0.50 | 25% |
| 15 | 0.75 | 38% |
| 20 | 1.00 | 50% |
| 40 | 2.00 | 100% (saturates historical alone) |

**v3_chain shadow adds** ~3 historical fetches per live signal (day/60m/30m, background worker) ≈
~36/day for ~12 signals — negligible sustained rate, spread across the session.

---

## Does it sit comfortably inside the broker limit ALONGSIDE live trading? → YES (trading is never at risk)

The broker limits are **per-endpoint token buckets with NO global cap** (`broker_limits.yaml`):
`order 8/s · quote 3/s · historical 2/s · margins 8/s`. Therefore:

1. **PB-01 (+ v3_chain shadow) load is ENTIRELY on the `historical` bucket (2/s).** Live trading uses
   `quote` (3/s, secondary_screener LTP) for screening and `order` (8/s) for placement — **different
   buckets, no global coupling.** PB-01 physically cannot consume the buckets live trading needs.
2. **The client RateLimiter caps historical at 2/s**, so the broker's historical limit is never
   breached (no 429). If PB-01 would exceed 2/s, the limiter *queues its own fetches* — it throttles
   PB-01, never live trading.
3. **All of it runs on background / daemon threads** (PB-01 poll thread + v3_chain background worker) —
   the live order path never blocks on a rate-limit wait.

**Conclusion: the PB-01 shadow flip CANNOT degrade live trading at ANY watchlist size.** It does not
need to be postponed. Trading comes first, and this change is on a different endpoint bucket than
trading, capped by the limiter, on background threads.

**One tail-risk, SHADOW-only (not a deploy blocker):** the watchlist has **no size cap** today. On a
high-breakout day (N ≳ 40) PB-01 alone would saturate the 2/s historical bucket; the RateLimiter would
throttle PB-01 and starve the *sr_detector's* shadow S&R fetches (also non-gating shadow) — degrading
**research quality, never live trading**. Recommended **fast-follow (not a blocker):** add
`watchlist.max_symbols` (~15–20, ranked by retest proximity / liquidity) so even a big day stays ≤ ~1/s
historical. Ship the flip now; add the cap when convenient.

---

## Recommendation

- **APPLY the two flips WITH tonight's atomic deploy** (on Rama's GO, off-market). PB-01 `enabled` stays
  false. No postponement — the load is trading-safe by endpoint separation + limiter + background threads.
- **Do NOT apply now** — config files remain untouched until the deploy window.
- **Fast-follow (optional):** `watchlist.max_symbols` cap for shadow-consumer health.

**NOT APPLIED. Config unchanged. Staged for the deploy.**
