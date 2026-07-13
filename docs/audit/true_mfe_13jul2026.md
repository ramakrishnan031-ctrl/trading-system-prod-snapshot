# Q2.7 — TRUE MFE (fixing Q2.6's truncation flaw)

**Date:** 13-Jul-2026 (off-market) · **Read-only** · 115/134 closed trades walked on real 1-min candles
(105 LONG, 10 SHORT). Nothing changed. **Evidence, not a decision.**

## 1. Is the truncation flaw REAL? — YES (source-cited)

`StateStore.compute_trade_excursions` (`core/state_store.py:1891`) filters candles to
`entry_dt <= cdt <= exit_dt` — MFE/MAE are computed **only over the trade's open lifetime, truncated at our
exit.** Since 79% of winners exit at the +1.5R target, their recorded MFE is capped at ~1.5R by construction.
**Q2.6's "1:2 is harmful" and "we leave only 0.28R on the table" are ARTIFACTS of this truncation — RETRACTED.**
(The Q2.6 MAE/stop conclusion still stands — adverse excursion before resolution is fully observed.)

## 2. The TRUE MFE distribution (the number nobody had seen)

Walked forward from entry to session end (15:29), honouring the original stop (conservative: adverse extreme
before favourable within a candle):

- **WINNERS (n=44): median TRUE_MFE = 2.71R** (p75 4.28R, p90 6.45R, max 15.15R).
- ALL: median 0.84R, p90 4.37R, p95 6.09R. LOSERS: median 0.49R (they don't run far before failing).

## 3. THE DECISIVE ANSWER — of trades we exited at +1.5R (TGT_HIT), how far did price actually run?

- **36 TGT_HIT trades → median TRUE_MFE = 2.93R** (we exited at 1.5R).
- **75% (27/36) ran to ≥ +2.0R** after our exit · **61% to ≥ +2.5R** · **47% to ≥ +3.0R**.
- **LEFT ON THE TABLE: median +1.43R / mean +2.32R per winner.**

**The winners kept running. We are cutting them short by roughly a full target's worth.** Rama's intuition
that there is more to capture is **correct and now quantified.**

## 4. BUT — is a 1:2 (or wider) FIXED target the way to capture it? — NO (the twist)

The honest path-walk target sweep (stop held at −1R, real candle ordering resolves the stop-vs-target race):

| target T | hit rate | expectancy |
|---|---|---|
| 1.0 | 43% | −0.060 |
| **1.5 (current)** | 36% | **−0.007** |
| 1.75 | 30% | −0.037 |
| **2.0 (Rama)** | 28% | **−0.027** |
| 2.5 | 22% | −0.067 |
| 3.0 | 17% | −0.071 |
| 4.0 | 12% | −0.004 |

Expectancy is **FLAT across targets** — 1.5R is as good as anything, 2.0R is *slightly worse*. Why, when winners
run to 2.93R? Because the paths are **choppy**: a trade touches +1.5R, pulls back toward the stop, *then*
extends. A fixed +2R target gets **stopped out on the pullback** — converting a would-be 1.5R winner into a −1R
loser. The hit-rate falls faster (36%→28%) than the bigger wins compensate. **The money is real, but a fixed
wider target cannot harvest it.**

Joint (stop × target) walk: best cell is **(−0.6R, +2.0R) → +0.008R** (barely positive, a knife-edge on 115
trades — overfit, do not trust the exact value); current **(−1.0R, +1.5R) → −0.007R** sits right beside it. The
whole surface is [−0.12, +0.01]R — no fixed geometry creates a real edge.

## 5. Does this change the roadmap? — YES, it ADDS a lever (and refines Q2.5)

- **Q2.5 refined:** the exit *mechanics* are sound (targets/stops fire reliably), but the exit *policy* (a fixed
  1.5R target, no trailing) **leaves a median +1.43R per winner unrealized.** That is a real, newly-quantified
  cost — the exits are NOT "fine", they are *leaving money*.
- **The right lever is a TRAILING STOP / partial-profit-and-runner**, NOT a wider fixed target. Only a mechanism
  that (a) banks some profit near 1.5R and (b) trails a runner can survive the pullback *and* ride the extension
  to 2.5–3R. A fixed target is all-or-nothing at a level the pullback undercuts. **This must be designed and
  backtested on the true paths — it is not a config nudge.**
- **This lever is MORE CERTAIN than M-S4 for the winners we already capture** (the +1.43R is observed, not
  hoped-for). But **M-S4 is still needed** — only 36% of trades reach even +1.5R, so the *win rate* (entry
  quality) remains the other half. And the two interact: better entries pull back less, letting a trail capture
  more.
- **Recommended sequence:** M-S4 first (as planned; it also builds the candle cache this used), then design +
  backtest a trailing/partial exit on true paths. Do NOT ship a fixed 1:2 — the data says it won't help.

## Mandatory honesty
- **STRUCTURAL (trust):** winners routinely run well past 1.5R (median 2.93R; 75% reach 2R) — a broad
  distribution, not a knife-edge. The choppy-path pullback effect is also structural (it's why the fixed sweep
  is flat).
- **OVERFIT (do NOT act on the number):** the (−0.6, +2.0)→+0.008 "optimum" — barely positive on 115 trades.
- **LIMITATIONS:** entry is fixed (M-S4 would change *which* trades and their paths — re-derive after). The walk
  uses `sl_initial` and ignores the actual exit, so trades that actually closed via manual/squareoff/trail are
  re-simulated (a few losers show large TRUE_MFE where the real stop/fill differed — a data caveat, immaterial to
  the TGT_HIT finding). 115 trades, ~2 months, one regime. The 15:17 squareoff truncates the day (a target
  needing more time is unreachable — the walk handles this).
- SHORT n=10 = noise (apparent "best target 1.75, +0.48R" — do NOT conclude from it).
- **No live SL/TGT/trail change is recommended here** — this is evidence for a design task, not a decision.

---
*Read-only. Nothing changed. Script: `scratchpad/q27_true_mfe.py` → `/tmp/q3/q27.py`.*
