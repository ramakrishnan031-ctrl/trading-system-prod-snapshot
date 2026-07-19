# Decision — Entry-throttle admission (arrival-order vs ranked)

**Status:** OPEN — Rama's call. **New (19-Jul, from the throttle-selection analysis).** **Type:** strategic / mechanism. **Blocked by:** its value is coupled to whether the scorer can rank (D3) — see the counter-case.
*Summary of the record, not a recommendation. Lettering is a label, not a ranking. The counter-case below is given equal weight by design.*

## The choice
- **Option A — leave arrival-order admission as-is:** a global 20-second `min_gap` between placed entries, first-come-first-served, evaluated after risk approval.
- **Option B — change the throttle parameters** (e.g. the 20s `min_gap`, the 3-per-60s burst, the 300s per-symbol cooldown).
- **Option C — build the ranked-admission batching window** the V3 design already specifies: accumulate a short window, dedupe by symbol, rank, admit greedily.

## What is known (the finding)
- The entry throttle (`signals/entry_throttle.py` @ `signal_processor.py:1170`) enforces a **global 20s `min_gap`**, evaluated **after** `risk_engine.approve()`. Of 139 throttles, **132 are that one gate** (7 per-symbol, 0 burst).
- **77 of 217 approved signals arrive in the 10:00–10:02 opening burst; 14.3% of them clear.** Throttling is **100% a market-open phenomenon** (hour 10: 139 throttled; hours 11–14: 0). **87% of orders (68/78) occur in hour 10.**
- **Selection among the burst is first-come-first-served, not best-first:** score **60.090 ordered vs 59.878 throttled**, non-monotone, no gradient (`docs/audit/throttle_selection_and_record_correction_19jul2026.md` §B).

## The counter-case — equal weight
**Ranked admission (Option C) is only worth anything if the ranking has predictive power, and this project's own evidence says it currently does not.**
- M-S4 full-range: NEW-score Spearman **rho = +0.003** (permutation p≈0.91) — **OUTCOME C, no ranking power at any level** (`docs/audit/ms4_fullrange_study_13jul2026.md`).
- Band inversion: the score **inverts** above ~35, and the **60–65 band — the only band that clears `min_pass=60`, i.e. every signal the throttle sees — is the worst-performing** (31% win, −0.27R), below the mid-bands at 4–5σ (`docs/audit/band_inversion_investigation_13jul2026.md`).
- Within the throttled set the score barely varies (0.21pt), and it is the inverted top band. **So if the scorer cannot rank, first-come-first-served is not throwing anything away** — ranked admission that ranks by the current composed score would rank by a signal with no demonstrated edge (and one that inverts at the top). This is not a minor caveat; it is the load-bearing objection to Option C as currently scorable.

## What it means regardless of ranking power (the visibility point)
Independently of whether the scorer can rank: **~23 approved signals per day (139 over 6 complete days) are discarded by the throttle**, 87% of orders land in the first hour, and **the throttle's gate category is recorded only in the free-text `rejection_reason`** — the structured `status` is the undifferentiated `REJECTED_ENTRY_THROTTLED`. So the discard is effectively **invisible in every report**. This is the fourth instance of the free-text-classification pattern (see `docs/audit/decision_packages_19jul2026.md` §C4). This observation stands whether or not ranking is worth adding.

## What is unknown
- **Whether a future, fixed scorer would give ranked admission value** — *[knowable only after D3 / the scorer work]*: Option C's worth is coupled to D3.
- **Whether the discarded burst signals would have been profitable** — *[not knowable from the record]*: they were never traded (the same unmeasurable-counterfactual shape as the unfilled-LIMIT fill step, §B4).
- **The purpose the 20s `min_gap` was set to serve** — *[knowable from existing data]*: it is a temporal rate limiter; its original protective intent is in the code/history, not re-derived here (changing it, Option B, without that is uninformed).

## What changes if the chosen direction is wrong
- **If C (build ranked admission) and the scorer still cannot rank:** added complexity that ranks the opening burst by noise — and, given the inversion, could *systematically prefer* the worst (already-moved) signals.
- **If A (leave) and the scorer is later fixed:** first-come-first-served then discards by arrival time what a working ranker could triage — the burst's best signals are lost to whoever arrived in the first 20 seconds.
- **If B (change params, e.g. shorten `min_gap`) and the gap was protecting against near-simultaneous order flooding:** admits more of the burst into whatever that protection guarded — unquantified, because the gap's intent is not established here.

## What would settle it
- **The ranking question is settled by D3 / the scorer:** does the composed score rank outcomes? If a scorer with ranking power exists, Option C triages the burst; if not, it ranks noise. **The visibility question is settled cheaply and independently:** persist the gate category to a structured column (a reporting change — described, not made here). Cost of Option C: the V3 batching-window design work, which is already specified.
