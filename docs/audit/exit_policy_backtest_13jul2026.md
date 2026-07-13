# Q2.8 — NAKED-TRADE FINDING + EXIT-POLICY BACKTEST

**Date:** 13-Jul-2026 (off-market) · **Read-only** · 115 trades walked on true 1-min paths. Nothing changed.
Harness **self-check PASSED** (P0 gross −0.007R = Q2.7 baseline). Cost = real 0.105% of position/round-trip.

## PART 1 — Are trades running naked? **YES (confirmed, source-cited).**

- **`order_protocol` is DEAD CONFIG.** 12 intraday strategies declare `CO_PLUS_TGT`, but `order_placer.py:855`
  uses `self._default_protocol` = `LIMIT_TRIPLE` (line 518/535). **100% of trades placed LIMIT_TRIPLE** — the
  declared protocol is ignored (confirms Audit-B Phase-7).
- **No SL was ever managed:** `sl_trail_count = 0` in **134/134**; `smart_tgt_state` = **0 rows**; orders
  `superseded_by` = **0**; `entry_mode` = **100% FULL**.
- **No exit-management engine is active on any live trade.** SmartTgt needs CO_PLUS_TGT (never placed);
  Breakeven needs `trailing_sl_enabled` (false); StructureExit needs `structure_exit_enabled` (false).
  **Every trade is naked: entry → fixed −1R SL → fixed +1.5R TGT → 15:17 squareoff.**
- **This corrects the Step-8 gap-map** ("SmartTgtManager is the sole live SL owner") — it assumed CO_PLUS_TGT;
  the real trades are LIMIT_TRIPLE, so SmartTgt registers nothing. *(Being corrected in memory + SYSTEM_MAP.)*

## PART 2 — Exit-policy backtest (net of cost, ranked)

| policy | exp (R) | win% | avgWin | avgLoss | scratch | maxDD |
|---|---|---|---|---|---|---|
| **P1a BE-after-0.5R** (TGT 1.5) | **−0.010** | 27% | +1.34 | −0.51 | 1% | −12.4 |
| P1a BE-after-0.75R | −0.072 | 30% | +1.35 | −0.69 | 1% | −16.0 |
| P3d give-back 25% (runner) | −0.076 | 44% | +1.07 | −0.99 | 1% | −16.7 |
| P3d give-back 33% | −0.093 | 44% | +1.03 | −0.99 | 1% | −17.6 |
| P2 partial-33%@1.5 + BE + runoff | −0.098 | 38% | +1.43 | −1.04 | 0% | −25.3 |
| **P0 CURRENT (static)** | **−0.100** | 38% | +1.35 | −1.00 | 1% | −20.6 |
| P1b **BreakevenMgr 60/80 [CONFIG-ONLY]** | **−0.117** | 35% | +1.20 | −0.82 | 1% | −19.8 |
| P4 partial-50% + BE + give-back 33% | −0.144 | 44% | +0.97 | −1.03 | 0% | −21.3 |

## The answers (blunt — this refutes the optimistic prior)

**1. Is any exit engine active?** No — trades are naked (Part 1).

**2. Best policy & margin:** **BE-after-0.5R → −0.010R**, a **+0.090R** improvement over the current −0.100R.
But: (a) it reaches only **~breakeven, it does NOT flip the book positive**; (b) it works by **cutting losers**
(avgLoss −1.00 → −0.51) at the cost of win rate (38% → 27%), **not by riding winners**; (c) it is a **SPIKE, not a
plateau** (0.5R best, 0.75R→1.0R monotonically worse) — the exact 0.5R trigger sits at the sweep edge and is
**overfit-suspect**. The honest structural takeaway is only directional: *an early breakeven move cuts the loser
tail and roughly halves the average loss, bringing the book to ~breakeven and cutting drawdown (−20.6 → −12.4R).*

**3. Config-only vs new code — no free win.** The **config-only** path (enable `trailing_sl_enabled` → the
as-built BreakevenMgr 60/80 rule) is **−0.117R — WORSE than doing nothing.** It scratches winners that reach
0.9R then pull back, and locks trades at +0.6R that would have reached 1.5R anyway. **There is no config flag
that flips the book.** The modest BE-after-0.5R win would need new code.

**4. Does exit-management outrank M-S4? NO — Web Claude's "~+0.10R flip" prior is NOT supported.**
The winner-riding policies (**give-back trail, partial+runner**) that were supposed to harvest Q2.7's +1.43R
**do not work**: the choppy paths (touch 1.5R → deep pullback → THEN extend) **whipsaw** any protective
mechanism — the trail/BE fires on the pullback and exits before the extension. Partial+runner books 1.5R but
the BE-protected runner is stopped at 0R on the pullback (and pays a 2nd exit cost). **Q2.7's +1.43R is real
but largely UNCAPTURABLE by simple mechanical exits.** The one thing that helps (early BE) just trims losers to
reach breakeven — it is not the book-flipping lever, and it is overfit-suspect.

## Reconciliation & roadmap
- **M-S4 remains the primary lever.** Only 36% of trades reach even +1.5R — the win rate (entry quality) is the
  binding problem, and no exit policy fixes a flat gross book. Neither lever alone flips it.
- **Exit-management is a secondary, modest lever** — an early breakeven move to cut the loser tail (~+0.05–0.09R,
  overfit-suspect) and reduce drawdown. **NOT** a trailing/partial runner (those whipsaw here).
- **The +1.43R "on the table" is a mirage for mechanical capture** — it requires surviving a deep pullback that
  any stop triggers on. Only better ENTRIES (less pullback) would make it harvestable — which loops back to M-S4.

## Mandatory honesty
- **Self-check PASSED** (P0 gross −0.007R). Harness trustworthy.
- **OVERFITTING:** the BE-after-0.5R "win" is a spike, not a plateau — do not trust the exact trigger. 115
  trades, one regime, ~2 months. Report is directional.
- **UNTESTED:** ATR-based and structure-based (swing-low) trails were not simulated (the adaptive give-back trail
  was, and it whipsaws — the mechanism strongly suggests ATR/structure would too, but this is not proven; a
  follow-up could test them).
- **COSTS** modelled at the real 0.105% round-trip; partial policies charged 1.5× (2nd exit fill).
- **ENTRIES FIXED** — M-S4 changes which trades exist and their paths; **re-derive all of this post-M-S4.**
- **No live exit change recommended.** Evidence for a design decision, not a decision.

---
*Read-only. Nothing changed. Script: `scratchpad/q28_exit_backtest.py` → `/tmp/q3/q28.py`.*
