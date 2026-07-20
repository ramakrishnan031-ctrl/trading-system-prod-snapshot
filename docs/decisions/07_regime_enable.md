# Decision — Regime: the strategic choice (enable / build Phase 1 / leave)

**Status:** OPEN — Rama's call. **Type:** strategic. **Blocked by:** Q10 (thesis not determinable at n=23) + a standing "do not flip mid-soak" constraint. *(20-Jul: "token-blocked backfill" removed from the blocker list — the backfill is not on regime's path at all; see the update box below.)*
*Summary of the record, not a recommendation. Lettering is a label, not a ranking.*

## The choice
- **Option A — leave `regime.enabled = false`** (`system_config.yaml:362`); the V3 chain's regime component stays log-only.
- **Option B — enable the existing regime axis** (the multi-month daily-trend engine) so its 8/40 Context weight becomes live.
- **Option C — build the Phase-1 opening-move axis first** (a new configurable plug-in inside `regime/`; designed, not built), then decide B.

> ### ⚠️ UPDATE 20-Jul-2026 — the "cannot compute" premise is retired (runtime-verified)
> The engine was run read-only on the VM at 19:35 IST with `regime.enabled` left **false**. It fetched
> **271 daily NIFTY bars** (token 256265, `interval="day"`, 400-day window; requirement is 201) and
> returned **`status: OK`** — direction BULL/LOW, `preference_multiplier` 0.2. **Regime is COMPUTABLE
> TODAY.**
> - **The April `regime_state.json` (`insufficient_index_daily_candles`) is not evidence of a data
>   gap.** A control run with no market-data handle reproduced that note exactly; the file exists only
>   on the PC (the VM has no `data_store/regime/` at all) and the PC's token is a month stale.
> - **The candle-backfill route is CLOSED.** A `sqlite3` tracer recorded **0 connections and 0 SQL
>   statements** across the whole compute path, in both the success and the forced-failure case. The
>   engine reads Kite live and **never** reads `analytics.candles`, so no backfill can unblock this.
> - **This narrows the blocker; it does not resolve the decision.** The gate moves from *"cannot
>   compute"* to *"needs forward data"* — still the ~2.2-month within-cell requirement below, and the
>   live consumer is still unbuilt. The standing do-not-flip-mid-soak constraint is untouched.
> - Also settled: the standing **"VM-verify the NIFTY index token"** item — done, 20-Jul-2026.
>
> Evidence: `docs/audit/regime_computability_verification_20jul2026.md`. **No recommendation implied.**

## What is known (current evidence, with citations)
- **The existing `regime` axis is a multi-month DAILY trend** (EMA50/200 + ADX + swings, ≥201 daily candles; `regime/engine.py:142-188`), reading the **live Kite daily API**, **not** the 09:15–09:59 opening move. Phase 1 would be a **new ordinal opening-move axis** (memory `regime-phase1-investigation-18jul`).
- **The V3 chain is already wired to regime output** — `gate_extreme` + `regime_fraction` = **8/40 Context** (`v3_chain/runner.py:225`), plus PB-01 — but all **log-only / fail-open** today with `regime.enabled = false`.
- **The regime thesis is NOT determinable at current data** (`docs/audit/regime_thesis_validation_18jul2026.md`): days per control cell fall to **single digits**; power analysis (observed σ=13.54) says a **LARGE effect needs ≈47 trading days (~2.2 months)**, MODERATE ≈9 months. The **91%-long book is a beta confound** ("bull mornings did better" is the null).
- **⚠️ STANDING CONSTRAINT:** do **not** flip `regime.enabled` during the V3/F1 shadow soak — the chain scores 8/40 from regime, so flipping mid-soak makes pre/post shadow rows non-comparable and corrupts the enforce-decision baseline being accumulated.

## What is unknown
- **Whether regime has any predictive power over this book** — *[knowable only by running the system]* for months (Q10's ~2.2-month floor for a LARGE effect), and only after the beta confound is separated by a within-cell test that currently has ~1–2 days per cell.
- **The Phase-1 opening-move axis's value** — *[not knowable from the current record]*; it is unbuilt. The raw ingredients (index candles + trades) now accumulate automatically via Phase 0's 15:40 cron, so the clock runs with no new code.

## What changes if the chosen direction is wrong
- **If B (enable) and regime has no edge:** adds an 8/40 score component of unknown value to live admission **and** breaks the shadow baseline mid-soak (the constraint above).
- **If A (leave) / C (build first) and regime has edge:** forgoes it for the months the measurement takes — but the shadow data required to know either way is being accumulated regardless.

## What would settle it
- Q10 run to power: ~~the proven backfill (`fetch_daily_candles.py --backfill …`, token-blocked) starts the clock and builds the tercile bands, but~~ the source is explicit that **even the backfill cannot deliver a verdict at n=23** — the settling observation is ~2.2 months of forward within-cell data. Cost: months.
  - **⚠️ CORRECTED 20-Jul-2026:** the backfill does **not** feed the regime engine at all — it writes `analytics.candles`, which the engine provably never reads (0 SQL on the compute path, runtime-verified). Whatever the backfill is worth for tercile bands elsewhere, **it is not on regime's critical path**, and regime's computability never depended on it. The settling observation is unchanged: ~2.2 months of forward within-cell data.
