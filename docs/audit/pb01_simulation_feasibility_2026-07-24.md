# Can PB-01 be evaluated without trading it? — a capability assessment

**READ-ONLY feasibility probe.** 2026-07-24 (IST). Deployed HEAD: `bc75406`. DB `mode=ro&immutable=1`; VM JSONL/logs read-only. **No evidence producer run** (`forward_shadow_record.py`, the V3 chain runner, `pb01_runner.py` — none invoked; §1.1). **This establishes whether the analysis COULD be produced — it does not produce it** (no results, no performance numbers, no recommendation; §1.2, §3.5).

Companion to `pb01_and_v3_gate_scoping_2026-07-24.md`.

---

## VERDICT (the three lines)

> **1. PARTIALLY.** The two capabilities split. The **outcome SIMULATOR** (`simulate_true_path`, `v3_chain/forward_shadow.py:75-105`) is a pure function and its 1-minute input **is held**; the entry-**DETECTION** gates (`gate_confirm`/`gate_pullback`, `hard_gate.py:362-459`) are pure functions on plain values with no live-only state. So the *code* to evaluate a PB-01 candidate without trading it exists and is reusable. **But the entry-DISCOVERY input — the breakout candidate universe — cannot be reconstructed from data the system holds.**
> **2. The single binding blocker is the CANDIDATE UNIVERSE.** PB-01's defining event is a "20-session-high daily breakout," and neither source exists: (a) the external Chartink EOD scan that would supply it **has never fired** (`pb01_watchlist` = **0 rows**, `pb01_breakout_retest` signals = **0 ever**), and (b) the level **cannot be computed from held data** — the system stores **only 1-minute candles** (24 trading days, 19-Jun→24-Jul, no daily OHLC), and **0 of 482 symbols have the 20 prior sessions** the level needs. From held data alone the candidate count is effectively **zero**.
> **3. It is a DATA blocker, and the missing input is one only Rama can supply** (the Chartink scan) **or a broker fetch the system does not hold** (daily history across the universe). The held 1-minute universe is additionally **biased**: it exists only for the 482 symbols the current momentum scanners already fired on — exactly the entry style PB-01 exists to replace.

---

## §0 — recorded before starting (survives the session)

- **§0.3 — THE HEADLINE (three independent lines converge — the entries buy extension):** STATISTICAL (band inversion — a high score marks an already-extended move, 60–65 worst); GEOMETRIC (the V3 RR gate — median 0.33R, 46% with no target above the entry); ARITHMETIC (38–39% win vs a 43.5% breakeven, exits measured and unable to close it). Recorded in memory `entries-buy-extension-24jul`.
- **§0.4 — PROTECTIVE DECISION:** ⛔ **do NOT lower `rr_floor` and do NOT loosen the V3 gate to reduce the rejection rate.** The gate is not broken; it is the only independent read on the entries, and tuning it to agree with them destroys the read. Recorded.
- **§0.5** — the mismatch is between THIS gate and THIS entry style; **not** a claim that "the S&R approach is wrong." Recorded.

## §2.1 — What must exist for a PB-01 candidate (the chain, quoted)

The `pb01_watchlist` is populated by **`WatchlistCaptureWorker`** (`v3_chain/watchlist_capture.py:50`), fed **only** from the webhook EOD route (`main.py:3119` `webhook_receiver.set_eod_capture(pb01_capture_worker)` → `.submit(scanner_name, symbol, triggered_at)` `:101`). So the trigger is an **external EOD Chartink alert** delivering a symbol. The worker then computes the LEVEL **itself** from its own settled daily candles (`compute_breakout_level` `:33-47` — highest daily high of the 20 sessions strictly before the breakout, "exactly the level the Chartink `Max(20, Daily High)` clause used"), stamps `trading_date = next_trading_day`, and inserts one row. **The input is external and confirmed:** nothing internal can populate the watchlist — the only caller is the webhook route, and 0 alerts have arrived (0 rows). (MEASURED.)

## §2.2 — Are the selection criteria reproducible from local data?

**Split answer:**
- **Reproducible (in the repo):** the retest-stage thresholds are all config SEEDS, not hardcoded (`level_lookback_sessions`, `gap_guard_pct`, `pullback_proximity_pct`, `confirm_min_body_frac`, `confirm_volume_mult`, `hold_buffer_atr_mult`, `atr30_period`, `baseline_candles_per_session` — `config_loader.py`/`system_config.yaml`), and the breakout **level definition** is in code (`compute_breakout_level`). The "V3 DECISION CONTENT SPECIFICATION v1.0" is *referenced* by 7 files but the document itself is **not in the repo** (`docs/v3/` holds only the STEP plans); its thresholds live as config, so the detection content is reconstructable even without the prose spec.
- **NOT reproducible (external):** the **complete Chartink EOD scan** — the exact filter set that decides *which* symbols get alerted as breakouts — lives inside Chartink. The repo reproduces the *core* criterion (20-session-high breakout) but cannot know whether Chartink applies additional price/volume/liquidity filters. So the candidate universe can be *approximated* (all 20-session-high breakouts) but not *matched* to what the scan would have alerted. (MEASURED from code; the external filter set is ASSUMED unknowable from here — §1.4 forbids specifying it.)

## §2.3 — Can the detection gates run against historical candles?

**Yes, mechanically.** `gate_confirm`/`gate_pullback` (`hard_gate.py:362-459`) take **plain values** (candle O/H/L/C/volume, level, session_low, lowest_5m_close, atr30, baseline, config constants) — no ticks, no feed, no session object; the fetcher is injected (`pb01_entry.py:84`). So they are directly callable in a read-only script (§1.2). **The 5-minute question:** the live path fetches 5-min bars from the broker (`fetch_interval(symbol,"5minute",lookback_days=1)`), but the system **stores only 1-minute candles**. Deriving 5-min from stored 1-min **is faithful**: the 1-min timestamps are clean and boundary-aligned (`09:15:00, 09:16:00, …` contiguous per session, MEASURED), so `[09:15..09:19]→09:15` grouping matches the broker's 5-min boundaries. The **inputs**, however, need multi-timeframe data the system does not store: `atr30` (30-min), `baseline_5m_volume` (SMA20 daily volume), and the LEVEL (daily) — all Kite-on-demand (`sr_detector/fetch.py` uses `historical_data`), not held.

## §2.4 — Can the simulator then evaluate it?

**Yes, and simply.** `simulate_true_path(entry, direction, candles[(h,l,c)], sl_pct=0.01, tgt_r=1.5)` is **pure** (the whole module is I/O-free, docstring `:11-14`). PB-01's **entry** is known (the confirmation candle close, `Pb01Confirmation.entry_price`), and its **stop/target are FIXED multiples** (1% SL, 1.5R TGT — `pb01_breakout_retest.yaml:40-48` `FIXED_PCT`/`RISK_REWARD`; the sim's own defaults match) — **not** S&R-derived, so no retrospective zone computation is needed. (`pullback_low` is also carried if a pullback-based SL were ever wanted — available either way.) ⚠️ **Standing caveat:** the walker is hardwired **adverse-first** (`:79-81`, "the adverse extreme is assumed hit before the favourable within a candle"); a faithful study would need **both intra-candle orderings reported as a pair**, exactly as the exit work required — the function currently gives only one.

## §2.5 — Data coverage, and the universe bias (named explicitly)

- **Held candles (MEASURED):** `analytics.db candles` = **1-minute only** (`interval_sec=60`), 275,129 rows, **482 symbols, 2026-06-19 → 2026-07-24 = 24 trading days**. No daily/30-min/5-min stored; no daily OHLC table (`daily_symbol_stats` holds `prev_close/avg_volume_20d/atr14/rsi14` — stats, **not** daily highs).
- **The level is uncomputable from held data:** it needs 20 prior daily sessions; **0 of 482 symbols have ≥20 trading days of 1-min** (each symbol is tracked only intermittently). So even deriving daily bars from 1-min, no symbol reaches the 20-session lookback — the defining criterion cannot be evaluated for any held symbol.
- **⚠️ THE UNIVERSE BIAS (named, per §2.5):** the system stores 1-min candles **only for symbols it tracked, and it tracked them because the current momentum scanners fired on them.** A PB-01 backtest restricted to held data would therefore only ever see breakout-retests in stocks that already produced momentum signals — **pre-selected by exactly the entry style PB-01 exists to replace.** This is not necessarily fatal, but it means a held-data backtest cannot represent PB-01's true universe, and that limitation must travel with any such result.

## §2.6 — Power, and the forward-vs-backward comparison (rough, labelled)

- **Backward from HELD data alone: ~zero candidates** (MEASURED constraint) — no symbol can even compute the level (0/482 with the lookback), so there is nothing to detect.
- **Backward WITH an external daily fetch** (fetching daily history from Kite across the universe to define breakouts, then using the held 1-min for outcomes): the outcome side is bounded to the **482 momentum-tracked symbols over ≤24 days** — a biased, short window. 20-session-high breakouts are individually rare, a clean next-morning retest-confirm rarer still, and the tracked universe is biased away from that setup; a rough, clearly-**ASSUMED** estimate is **a handful (single-digit to low-double-digit) candidates** — too few and too biased to conclude anything, and it would still carry the §2.4 ordering caveat and the §2.5 universe bias.
- **Forward, if Rama created the scan:** candidates accrue at the scan's natural rate from creation, over an **unbiased** universe — but that needs the scan (Rama's, §1.4) plus **weeks-to-months** of waiting, and every service-down day is a lost day (`forward_shadow_scoping_2026-07-24.md`).
- **The practical comparison (stated, not recommended):** backward is cheap in *code* but blocked on *data* (a broker fetch the system doesn't hold) and yields only a handful over a biased window; forward is blocked on the *scan* and on *waiting* but yields clean, unbiased evidence. Neither is a quick win; the backward path's ceiling is low and biased. Which to pursue — or neither — is Rama's.

## Evidence appendix (reproduce; read-only)

- Code (`bc75406`): `v3_chain/watchlist_capture.py:33-47,50,101,131-173`; `v3_chain/pb01_entry.py:165-257` (detection), `:302-304` (5-min fetch); `screening/hard_gate.py:362-459` (pure gates); `v3_chain/forward_shadow.py:75-105` (pure simulator, fixed SL/TGT, adverse-first); `sr_detector/fetch.py:70-133` (Kite `historical_data`); `config/strategies/pb01_breakout_retest.yaml:40-48`.
- Data (`immutable=1`): `candles` interval_sec=60 only, 24 trading days, 482 syms, 0 with ≥20 days; `pb01_watchlist`=0; `pb01_breakout_retest` signals=0; `daily_symbol_stats` has no daily OHLC.
- No evidence producer run; no pure-function invoked against persisted state (purity established from signatures).
