# Decision — D4: exit policy

**Status:** OPEN — Rama's call. **Type:** strategic (edge). **Blocked by:** power (see the update). **Triage (updated 19-Jul): COMPUTABLE NOW → PARTIALLY COMPUTABLE / UNDERPOWERED (NEEDS THE SYSTEM RUNNING).**
*Summary of the record, not a recommendation. Lettering is a label, not a ranking.*

## ⚠️ UPDATE 19-Jul-2026 — feasibility established; the binding constraint is power (`docs/audit/forward_shadow_capacity_and_d4_feasibility_19jul2026.md`)
- **The facts replicate (verified from persisted data):** no exit engine has ever fired — `sl_trail_count > 0` on **0 of all 361 trades** (not "0/134"); `order_protocol` = LIMIT_TRIPLE on 361/361; `entry_mode` = FULL on 361/361.
- **D4 IS partially testable out-of-sample — more than D3** (its 1-min candles survived in the live `analytics.db`; `trade_excursions` holds MFE/MAE for 109 trades). **But the binding constraint is POWER, not data:** only **19 entered trades exist out-of-sample (9 winners)** — far too few to confirm or refute any exit-policy effect.
- **The "~2.93R winners' MFE" claim does NOT reproduce out-of-sample** — the OOS median (uncapped full-path, from candles) is **1.44R** (n=9), near the 1.5R TGT, and unstable (one added in-window day swings the mean 1.75R→3.63R). **"Cannot distinguish," not a refutation.** The `trade_excursions` MFE (winners 1.56R) is *capped by the current exit* and so cannot answer "money left on the table."
- **Not re-verifiable on its own data:** the 13-Jul exit study's per-trade sims were never persisted, and there are no 1-min candles before 13-Jul — the same standing constraint that made D3's discovery study irreproducible.
- **The Option-B/C backtest numbers below are inherited from the 13-Jul study** and cannot be re-checked at adequate power from what exists; read them as *what was found in one window*, unconfirmed out-of-sample.

## The choice
- **Option A — keep the current static exits:** entry → fixed −1R SL → +1.5R TGT → 15:17 squareoff.
- **Option B — enable the as-built breakeven rule via config** (`trailing_sl_enabled` → BreakevenMgr 60/80).
- **Option C — build a new BE-after-0.5R rule** (requires new code).

## What is known (current evidence, with citations)
- **Every live trade runs naked** (`docs/audit/exit_policy_backtest_13jul2026.md`, memory not separately held): `order_protocol` is dead config — 12 strategies declare `CO_PLUS_TGT` but `order_placer.py:855` uses `LIMIT_TRIPLE`; **`sl_trail_count = 0` on all 361 trades** (verified 19-Jul; the record's "134" was a subset), `smart_tgt_state` 0 rows, `entry_mode` 100% FULL. No exit-management engine is active on any live trade.
- **Exit-policy backtest (net of real cost, 115 trades on true 1-min paths):**
  - **P0 current (static): −0.100R**, win 38%, maxDD −20.6R.
  - **Option C, BE-after-0.5R: −0.010R** (+0.090R over current) — but (a) reaches only **~breakeven**, does **not** flip the book positive; (b) works by **cutting losers** (avgLoss −1.00 → −0.51) at the cost of win rate (38% → 27%), not by riding winners; (c) is a **spike at 0.5R** (0.75R and 1.0R are monotonically worse), i.e. the trigger sits at the sweep edge and is **overfit-suspect**.
  - **Option B, config-only BreakevenMgr 60/80: −0.117R — worse than doing nothing.** It scratches winners that reach 0.9R then pull back and locks trades at +0.6R that would have reached 1.5R.
- **"There is no config flag that flips the book."** The modest BE win needs new code, and the source states exit-management does **not** outrank M-S4 (Web Claude's "~+0.10R flip" prior is not supported).

## What is unknown
- **Whether BE-after-0.5R's +0.090R is real or overfit** — *[knowable from existing data]*: an out-of-sample run on fresh 1-min paths would test whether the 0.5R spike survives.
- **Whether it holds live** — *[knowable only by running the system]* (the backtest assumes the modelled fill/cost behaviour).

## What changes if the chosen direction is wrong
- **If C (build) and the 0.5R spike is overfit:** new exit code for no durable gain (the backtest's own maxDD improvement −20.6 → −12.4R would also not transfer).
- **If B (enable config) :** the record already shows this is **−0.117R**, worse than A.
- **If A (leave) and BE-after-0.5R is real:** forgoes a ~+0.090R improvement that would bring the book to ~breakeven (still not positive).

## What would settle it
- **The out-of-sample test is feasible (the candles survived) but underpowered today** — 19 entered / 9 winners out-of-sample. At the observed ~3 winners/day it takes **~17 trading days for ~50 winners, ~34 for ~100** to test an exit-policy effect at the traded book — i.e. it **needs the system running**, not just a re-analysis. A faithful re-run of the exit-sim harness on the surviving OOS candles could raise n via the signal-population counterfactual, but that answers a counterfactual-entry question, not the traded-book decision.
- Even fully powered, the record's own framing stands: the best backtested policy reaches only **~breakeven**, so this is a drawdown/loss-tail question, not an edge question; the edge question routes back to M-S4/D3 (itself unresolved out-of-sample).
