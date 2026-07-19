# Decision — D4: exit policy

**Status:** OPEN — Rama's call. **Type:** strategic (edge). **Blocked by:** nothing; but see the coupling to M-S4/D3.
*Summary of the record, not a recommendation. Lettering is a label, not a ranking.*

## The choice
- **Option A — keep the current static exits:** entry → fixed −1R SL → +1.5R TGT → 15:17 squareoff.
- **Option B — enable the as-built breakeven rule via config** (`trailing_sl_enabled` → BreakevenMgr 60/80).
- **Option C — build a new BE-after-0.5R rule** (requires new code).

## What is known (current evidence, with citations)
- **Every live trade runs naked** (`docs/audit/exit_policy_backtest_13jul2026.md`, memory not separately held): `order_protocol` is dead config — 12 strategies declare `CO_PLUS_TGT` but `order_placer.py:855` uses `LIMIT_TRIPLE`; **`sl_trail_count = 0` in 134/134**, `smart_tgt_state` 0 rows, `entry_mode` 100% FULL. No exit-management engine is active on any live trade.
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
- An **out-of-sample** exit backtest (fresh 1-min data) to test the 0.5R trigger's stability — cost: data + re-run, no deploy. Note the record's framing that even at its best this reaches ~breakeven, so it is a drawdown/loss-tail question, not an edge question; the edge question routes back to M-S4/D3.
