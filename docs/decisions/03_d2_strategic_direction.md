# Decision — D2: strategic direction / strategy mix

**Status:** OPEN — Rama's call. **Type:** strategic. **Blocked by:** a positive-control baseline + regime data (Q10 Part B, token-blocked).
*Summary of the record, not a recommendation. Lettering is a label, not a ranking.*

## The choice
- **Option A — keep the current 15-strategy mix** as-is.
- **Option B — retire the negative-edge strategies** (the drain is concentrated — see below).
- **Option C — rebalance** the long/short/positional weighting.

## What is known (current evidence, with citations)
- **The closed book (155 trades) is 91% LONG** — this **corrects** the earlier "~58% short" premise (`docs/audit/bk1_long_short_scanner_analysis_17jul2026.md`; memory `bk1-long-short-scanner-17jul`). The long skew starts at the **scanner** (28,027 LONG vs 2,742 SHORT admitted signals).
- **The loss is concentrated, and it is a negative-edge loss, not a cost loss:** intraday longs **−0.239R (n=108)**; positional longs and shorts net **positive**; the single biggest drain is **`vwap_bounce_long` −12.1R (n=35)**. Losers are negative on **gross**, i.e. not cost-killed.
- **Direction is structured, not name-derived** (`StrategyConfig.direction`, `strategies/schema.py:58`); declared == actual on all traded strategies; no strategy trades both sides (`docs/audit/strategy_direction_investigation_17jul2026.md`).
- **Where the throttle finding (§C) bears:** the entry throttle is **time-selected, not strategy-selected** (the 21–72% per-strategy ordered% spread is an arrival-density confound, not an effect). So the strategy-mix comparison above is **not systematically distorted** by the throttle. **Where it does bear:** the fill step's 17 unfilled-LIMIT timeouts mildly select *against* fast-moving entries, which could differentially touch momentum-style strategies — an unquantified caveat (their counterfactual P&L is unmeasurable; §C, `throttle_selection_and_record_correction_19jul2026.md` §B4-B5).

## What is unknown
- **Whether the negative-edge strategies are negative in all regimes or only in this window** — *[not knowable from the current record]*: the regime confound is unmeasurable historically (no index candles before 16-Jul; Q10 NOT DETERMINABLE at n=23, `regime-thesis-validation-18jul`). Q10 Part B (backfill) is blocked on the Kite token.
- **Per-strategy edge on a corrected scorer** — *[knowable only by running the system]*: today's per-strategy P&L is measured on the min_pass=60 anti-edge band (see D3) and at ~1/20th intended size (D1).

## What changes if the chosen direction is wrong
- **If B (retire) and the retired strategy is regime-dependent rather than broken:** forgoes edge that would have appeared in the regime where that strategy works — unquantifiable from the current record.
- **If A (keep) and a strategy is genuinely broken:** continued drain at the observed rate (e.g. `vwap_bounce_long` −12.1R over the window).

## What would settle it
- **Per-strategy expectancy on a positive-control baseline** (corrected scorer, D3) **plus regime attribution** (Q10 Part B: the proven backfill `fetch_daily_candles.py --backfill --from 2026-06-15 --to 2026-07-16`, then correlate). Cost: the token refresh + backfill (data-only), then N days of forward data — and note Q10's own verdict that even the backfill cannot deliver a determination at n=23; it starts the clock.
