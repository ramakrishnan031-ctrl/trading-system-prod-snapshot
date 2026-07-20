# Regime direction-preference — does the mechanism Rama asked for already exist? — 20-Jul-2026

**Rama's question (20-Jul ~10:00):** "when Nifty falls, should SHORT signals get **PRIORITY** over LONG?"
— explicitly **priority, not a block.** Read-only, from code. Nothing enabled, designed, or recommended.

## §C6 — VERDICT: **IT ALREADY EXISTS — as a SHADOW preference engine — and it is PREFERENCE, not permission. It is OFF and not yet computable, waiting on the same wiring/evidence as #07.**

## §C2 — preference or permission? **PREFERENCE (down-weight), proven from code — it never rejects a signal.**
- The regime engine emits a **`preference_multiplier`** — an ordinal-confidence scalar
  `{HIGH:1.0, MEDIUM:0.5, LOW:0.2, TRANSITION:0.0}` (`regime/models.py:42-47`), headline = the direction
  axis (`regime/engine.py:133`).
- **Fail-safe is the contract**: missing/insufficient data → `status=UNKNOWN, multiplier 0 (neutral)` and
  the code repeats "**NOT a halt / the book is NOT halted**" (`engine.py:9-13,112,137`; `models.py:78-82,113`).
  A neutral preference means the system **trades normally**.
- The runner "**gates NOTHING — no order, score, size, or kill-switch is touched**" (`regime/runner.py:6-7`).
- The **only** thing that is permission-shaped is `extreme_flag` (a *confirmed* exchange halt/circuit),
  and even that is "**emitted but NOT wired to stop trading in this step**" (`engine.py:283-286`).
- ⇒ A bearish regime **scales the Context score** (down-weights), it does **not** reject longs. Exactly the
  "preference, not block" Rama asked for.

## §C1 — direction axis: off NIFTY, but never successfully computed
- Direction is computed on the **index** (default token **256265 = NIFTY 50**, `engine.py:78-79`) from
  EMA-50/EMA-200 cross + slope + swing structure + ADX on **DAILY** closes (`engine.py:142-188`).
- **Was the index fetch ever VM-verified? NO.** The persisted `data_store/regime/regime_state.json` is from
  **2026-04-16**, `status=UNKNOWN`, note **`insufficient_index_daily_candles`** — the last time the engine
  ran it could not get enough daily index history. It has **never** produced a real classification.
- Source: the engine fetches via `sr_detector` `OhlcFetcher.fetch_by_token(256265,"day",400)` →
  a **Kite `historical_data` closure** (`fetch.py:12-13,125`), i.e. **live from Kite, not any DB table.**

## §C3 — per-signal or per-book? **Per-BOOK.**
One index-level regime is computed **once per cycle** (`runner.py:63-75`) and would scale the Context score of
each signal by the same `preference_multiplier`. It is **not** a per-signal contest that ranks a long against
a short arriving in the same window — it is a book-level lean applied to every score alike.

## §C4 — the throttle coupling (made visible, NOT resolved — #10 is Rama's)
Even a perfect direction preference **in the SCORE** may not change **which** signals get orders: admission is
the entry throttle — a global 20s `min_gap` **after** risk approval, **first-come-first-served, not best-first**
(no score gradient in the selected book: 60.090 vs 59.878). **Direction preference (#07) and throttle
admission (#10) are the same lever from two ends** — a preference the admission layer ignores is decorative.
Dependency flagged; not resolved.

## §C5 — today is an anecdote, n=3
Nifty ~−0.70% at 10:00; 2 longs + 1 short (BEPL/PNB long, KROSS short), all closed at a loss, day ~−Rs 10.61.
**n=3 is an anecdote, not evidence.** It says nothing about whether a direction preference improves outcomes.

## Which layer does the question land in?
The book is **91% LONG** (corrected figure). A direction preference in a structurally-91%-long book is a
statement about the **scanners** (what signals arrive) as much as the regime layer. Regime can only re-weight
what the scanners produce; it cannot manufacture shorts.

## What it is waiting on (same gate as #07, and the §A finding)
1. **Computability** — the engine needs ~**201 daily** NIFTY bars (Kite-available) + the runner enabled; it has
   never computed one. **The Q10 candle backfill does NOT unblock this** — see
   `q10_part_b_backfill_20jul2026.md` §A: regime reads daily bars live from Kite, not the 1-minute `candles`
   table the backfill writes.
2. **Consumer** — the live Context-score consumer is a later, unbuilt step; today the only consumer is the V3
   **shadow** chain (log-only, no order path).
3. **Evidence** — #07 needs ~2.2 months of forward within-cell data (Q10 = NOT DETERMINABLE at n=23).

**So: the mechanism you want is built, it is preference-by-design, it is OFF, its consumer isn't wired live, and
it can't even compute yet.** No enable, no design, no recommendation — #07 and #10 are yours.

*Read-only. Baseline: 20-Jul, `8345cc0`, schema v44. `regime.enabled` untouched (FALSE).*
