# Candle retention (D4) + PerformanceAllocator feasibility — 19-Jul-2026

**READ-ONLY. Docs-only. No recommendation.** Two small verifications of assumptions the decision
board rests on: (A) will the out-of-sample 1-min candles survive long enough for **D4** to ever be
answerable? (B) the last unverified **"COMPUTABLE NOW"** label — **PerformanceAllocator** (#06) — does
its data *and* its power actually support the reachability algebra?

Nothing executable changed. All queries ran against `mode=ro` / throwaway snapshots, never through
application code. System DOWN since Fri 17-Jul; market closed; book flat. Deploy state at start:
PC == origin == VM bare == `051e672`; code tag `deploy-19jul-consecutive-losses` → `d271525`; schema
v44.

---

## 0. Integrity of the three artifacts (proven, with a correction to the baseline)

All three were **byte-identical before and after this session** (read-only pass, 19:29–19:33 IST):

| Artifact | sha256 | size | mtime |
|---|---|---|---|
| `data_store/trading_system.db` | `a7a1de53…ef229b4` | 89,968,640 | 2026-07-19 18:00:17 |
| `data_store/v3/forward_shadow_fs-v1.jsonl` | `f7c964fd…37f358f` | 4,526,565 (**7,827 lines**) | 2026-07-16 18:15:21 |
| `data_store/analytics.db` | `bb229f44…57a7fa40` | 24,133,632 | 2026-07-19 02:30:04 |

**⚠️ Baseline correction — the live DB hash the resume file quoted is stale, and not because of me.**
The resume recorded `6df0c09a…`@`11:14:29`. The live DB is now `a7a1de53…`@`18:00:17` — it advanced
**at 18:00 today, before I connected at 19:29.** Root cause identified precisely (read-only):
`cron_heartbeat` **id=2421 `gemini_weekly_patterns` SUCCESS, executed_at 18:00:17.330** — the DB mtime
`18:00:17.332` matches to the millisecond. The `6df0c09a` snapshot was taken just after the weekend
`eod_cleanup` SKIP heartbeats (ids 2417–2420, 11:13–11:14, "non-trading day"); `gemini_weekly_patterns`
then wrote its heartbeat at 18:00. The system is **DOWN** (`trading-system.service` inactive/dead;
`ExecMainStart/Exit` = Fri 17-Jul 08:15:51→08:16:09; not run since). `token-watcher` active is normal.

- **Standing lesson:** the live DB hash/mtime **advances on any monitoring/gemini cron `cron_heartbeat`
  write even while the trading system is down.** "Prove the live DB untouched" therefore means
  **before == after within your own session**, not equality to a prior day's hash. The resume's own
  "re-fingerprint before trusting" note anticipated this. The current honest baseline is
  **`a7a1de53…`@`18:00:17`** (until the next heartbeat write). `analytics.db` mtime `02:30:04` = the
  Sunday `db_retention --vacuum` run (see §A). The forward-shadow JSONL is unchanged (last append
  16-Jul; the 17-Jul recorder run left the done-mark but appended nothing).

*(Incidental, out of scope, not acted on: `cron_heartbeat` shows `backup_retention` FAILED 18-Jul &
19-Jul — "a run would delete 37 files (> cap 10). Nothing deleted." That is the reaper's own safety
abort; it means nightly backups are **accumulating**, not being lost — so candle backup coverage is if
anything wider than intended. Flagged for Rama; no action taken.)*

---

## A. Can D4 ever be answered? — candle retention. **VERDICT: SURVIVES.**

D4's evidence path: accumulate ~17–34 trading days of entered trades (~50–100 winners), then reconstruct
post-entry price paths from the 1-min `candles`. That only works if the candles are still present when
the sample is big enough.

### A1. The retention that governs the candles table (from code, traced end-to-end)

- **Policy:** `scripts/db_retention.py:63-70` — `DEFAULT_RETENTION["candles"] = ("date", 90)`. The
  DELETE is `DELETE FROM {table} WHERE {date_col} < ?` with `cutoff = run_date − 90 days`
  (`db_retention.py:118-121`, `_cutoff()` `:73-77`). Keyed on the **stored `date` column** (the O4 v27
  generated+indexed column; `schema.sql:1618-1621`), i.e. **row age by trading date**, `< cutoff`
  strictly (never today's rows).
- **It runs every calendar day** (not market-day-gated): `config/cron_registry.yaml:82-96`
  `db_retention` `cron_expression: 30 2 * * 1-6` (Mon–Sat) **+** `:97-111` `db_retention_vacuum`
  `30 2 * * 0` (Sun, `--vacuum`); both `enabled: true`, `market_day_only: false`. (`monitored: false` —
  a failure is logged, not alerted.)
- **The DELETE genuinely reaches `analytics.candles`** (the candles table was relocated out of the main
  DB in v28/O6): `schema.sql:772-777` + `core/db_connect.py:26-30` (`ANALYTICS_TABLES` = candles,
  system_metrics, system_metrics_daily; they exist **only** in `analytics.db`). `db_retention.py` opens
  via `StateStore(db_path=…)` (`:203`), and **`StateStore.__init__` ATTACHes `analytics.db` on every
  connection** (`core/state_store.py:247` → `db_connect.attach_analytics`). So the unqualified
  `DELETE FROM candles` resolves to `analytics.candles`.

### A2. Anti-vacuity — it actually executed, and correctly deleted nothing yet

The G11 trap (a "deleted 0 rows" that proved nothing because the prune body never ran) does **not**
apply here, on two independent grounds:

1. **The cron ran today and completed cleanly.** `analytics.db` mtime = **02:30:04 today** and
   `cron_marks/db_retention.done` = **02:30:05 today**. The Sunday variant passes `--vacuum`, and
   `db_retention.py:139` only VACUUMs **if `failures == 0`**; the loop VACUUMs both `main` and
   **`analytics`** (`:143`). A VACUUM of `analytics` rewrote the file (mtime moved) ⇒ the run reached
   the vacuum with **zero table failures** ⇒ `DELETE FROM candles` executed under the attach without
   error. The retention path is live, not dead config.
2. **0 candles were deleted because nothing is ≥ 90 days old yet — correctly, not vacuously.** Earliest
   candle = **2026-06-19** (verified: `MIN(date)` over 131,527 rows / 19 days). Today − 90 = 2026-04-20;
   `2026-06-19 > 2026-04-20`, so there is genuinely nothing to prune. OOS days cross-check the read
   exactly: 07-13 **3,750** · 07-14 **23,165** · 07-15 **34,298** · 07-16 **11,962** (identical to the
   instruction's figures; +07-10 4,115).

### A2 (cont.). The dates, plainly

A candle dated `D` is deleted on the first run with `run_date ≥ D + 91` (needs `cutoff > D`):

| Candle date | First deleted on/after | Notes |
|---|---|---|
| earliest = **2026-06-19** | **~2026-09-18** | first candle prune ever occurs |
| OOS **2026-07-13** | **~2026-10-12** | |
| OOS **2026-07-14** | **~2026-10-13** | |
| OOS **2026-07-15** | **~2026-10-14** | |
| OOS **2026-07-16** | **~2026-10-15** | last of the seed OOS days |

D4 sample maturity from **Mon 20-Jul-2026** at the observed ~3 winners/day (candles for each new
trading day are written fresh and are the *youngest* rows, so never at risk):

| Milestone | Trading days | ≈ calendar date |
|---|---|---|
| ~50 winners | ~17 | **~mid-Aug 2026** |
| ~100 winners | ~34 | **~mid-Sep 2026** |

**Comparison / verdict — SURVIVES.** The sample matures (~mid-Aug to ~mid-Sep) **5–9 weeks before** the
seed OOS candles (13–16 Jul) expire (~12–15 Oct). More fundamentally, the **rolling 90-calendar-day
window ≈ 62 trading days** permanently exceeds D4's 17–34-trading-day need, so at any instant the last
34 trading days of candles are all present (each < 90 days old). The *only* real constraint is that the
analysis be **run within ~90 days of the earliest trade it examines** — a ~40-day slack after the
100-winner sample matures. **Candle retention does not make D4 unanswerable; power does** (19 entered /
9 winners today — the underpowered constraint established 19-Jul, unchanged).

### A3. Options (moot — SURVIVES — but priced, as asked, and not chosen)

Not required, since the candles survive. For completeness and to put a number on the cheap option:

- **Preserve a candle snapshot** — a gzipped CSV of just the 4 OOS days (73,175 rows) ≈ **~2–4 MB**;
  the whole `analytics.db` copy = **24.1 MB**. A production-change: none (a read + copy).
- **Change retention** — a production change and #09's neighbourhood; **Rama's call.** Not needed.
- **Accept D4 stays open** — on power, not on data loss.

### A4. The known preservation gap — immaterial given SURVIVES

`/home/ubuntu/preserved/signal_census_19jul2026/` snapshots **`trading_system.db` only** — the candles
live in the ATTACHed `analytics.db`, which that snapshot did not cover. This does **not** matter here:
(a) the candles persist live until ~12–15 Oct, well past D4 maturity; and (b) the nightly
**`analytics_backup` cron** (`cron_registry.yaml:47-51`, `.backup analytics-<date>.db`, ran
**01:05 today** per its done-mark) independently backs up `analytics.db` — candles included — every
night. There is no D4 exposure from the census-snapshot gap.

---

## B. PerformanceAllocator (#06) — the last "COMPUTABLE NOW" label. **VERDICT: FEASIBLE — and it HOLDS, unlike D3/D4.**

The question (verbatim): *could `perf_weight ≠ 1.0` ever change the final quantity, given the
concentration cap binds 100% of trades?*

### B1. Feasibility FIRST — the inputs and the binding constraint ARE persisted

Unlike D3 (per-signal basis never persisted) and D4 (data survives but underpowered), the sizer's **full
per-trade breakdown is persisted** (schema v34, `schema.sql:210-223`): `tier_multiplier_mode`,
`tier_weight_applied`, `perf_weight_applied`, `qty_by_risk`, `qty_by_capital`, `qty_by_concentration`,
`qty_by_flat`, `binding_constraint`, `actual_position_value_rs` — "from PositionSizer's breakdown."

- **Coverage / power: 298 of 361 trades (82.5%)** carry the non-NULL breakdown (the 63 without are the
  pre-v34 / recovered rows). 298 is ample. **Both data and power are adequate ⇒ the third "COMPUTABLE
  NOW" label is the first of the three to actually HOLD.** (D3 failed on data, D4 on power; this one
  fails on neither.)

### B2. The algebra — and it refutes decision file 06's central premise

The sizing chain (`position_sizer.py`, `enabled=ON`): `raw_qty = min(qty_by_risk, qty_by_capital,
qty_by_concentration)` (`:407`); `effective_mult = tier_mult × perf_weight` (`:446`);
`tiered_qty = floor(raw_qty × effective_mult)` (`:503`); **`tiered_qty = max(1, min(tiered_qty,
raw_qty × 2))`** (`:506`, the 2× ceiling + floor-at-1); `final_qty = (tiered_qty // lot_size) × lot_size`
(`:530`). **`perf_weight` is applied AFTER the concentration cap — it multiplies `raw_qty` up to a
`2×raw_qty` ceiling — so concentration setting `raw_qty` does NOT prevent `perf_weight` from moving the
final quantity.**

Persisted facts on the 298:
- `binding_constraint = 'concentration'` on **298 / 298** — empirically confirms D1's "concentration
  binds 100% of sized trades."
- `perf_weight_applied = 1.0` on all 298 (allocator unwired; `main.py:2335-2339` only *logs* the tier
  config — no `PerformanceAllocator(...)` instantiation; `signal_processor.py:893/1827/2142` pass
  `perf_weight = self._perf_weights.get(name, 1.0)` and `_perf_weights` is only ever the empty default).
- `tier_weight_applied = 0.5` on all 298 — every traded signal scored **LOW** tier (consistent with the
  scorer's absent ranking power, M-S4).

**Counterfactual reachability** (`final(pw) = max(1, min(floor(raw_qty × tier_mult × pw), 2×raw_qty))`,
lot_size = 1, over `perf_weight`'s configured clamp `min_multiplier=0.5 … max_multiplier=2.0`):

| Range of `perf_weight` | trades whose final_qty changes | share of 298 |
|---|---|---|
| full **[0.5, 2.0]** (config clamp) | **233** | **78%** |
| modest **[0.8, 1.25]** | **151** | **51%** |

**Answer to B2: `perf_weight ≠ 1.0` would change final quantity on 233 of 298 trades over its full
configured range, and on 151 of 298 even under modest dispersion. It is decorative on only 65 — the
raw_qty=1 trades, pinned at 1 lot by the integer floor, not "swallowed by concentration."** The premise
in `06_performance_allocator.md` ("its effect is likely masked by the concentration cap"; "a multiplier
applied *before* a cap … is masked by that cap") is **incorrect on both the mechanism and the data**:
the multiplier is applied *after* the cap, and it binds on the majority of the recorded book.

*Distinction from Q9 batch-4:* batch-4's "only 4 of 15 sizing **guards** can bind" concerns the
rejection guards, a different question; the decision file's transfer of that finding to `perf_weight`
(itself a multiplier, not one of the 15 guards) does not hold.

### B3. Anti-vacuity — the method reproduces reality and discriminates

- **Could-have-failed check:** the counterfactual formula at `pw = 1.0` **reproduces the persisted
  `qty_planned` on 298 / 298 trades (0 mismatch)** — validating the reconstruction *and* confirming
  lot_size = 1 (all equity) throughout. A wrong formula would have mismatched.
- **It discriminates, not vacuously "all change":**

| raw_qty | tier | final@0.5 | final@1.0 | final@2.0 | changes? | n |
|---|---|---|---|---|---|---|
| 1 | 0.5 | 1 | 1 | 1 | **no** | 65 |
| 2 | 0.5 | 1 | 1 | 2 | yes | 47 |
| 3 | 0.5 | 1 | 1 | 3 | yes | 35 |
| 4 | 0.5 | 1 | 2 | 4 | yes | 102 |
| 5 | 0.5 | 1 | 2 | 5 | yes | 19 |
| 6 | 0.5 | 1 | 3 | 6 | yes | 15 |
| 7–19 | 0.5 | 1–4 | 3–9 | 7–19 | yes | 14 |

The 65 non-binding trades are exactly raw_qty = 1; every raw_qty ≥ 2 changes. `233 = 298 − 65`.

### B4. Shelf life — conditional on today's unlevered sizing

This verdict is computed against the **current unlevered regime** (total capital ≈ Rs 9,875;
`max_concentration_pct = 0.10` ⇒ concentration base ≈ Rs 987 ⇒ raw_qty 1–19, mostly ≤ 6, with 65 trades
pinned at raw_qty = 1). Under **5× MIS leverage** (currently unmodelled; the system sizes unlevered), the
sizing bases would grow and **fewer** trades would sit at raw_qty = 1, so `perf_weight` would bind on
**even more** trades — but the exact counts must be re-run if leverage is ever modelled. **No leverage
design is done here.** (Secondary interaction, noted not counted: at `pw` near 2.0 a near-cap position
~Rs 987 doubles to ~Rs 1,974, still under the ~Rs 3,950 40%-of-capital `POSITION_VALUE_CAP`, so that cap
rarely re-binds; and the latent 2× ceiling at `position_sizer.py:506` is exactly what *permits* the
doubling.)

### B5. Neutral framing (no recommendation)

This establishes **reachability**, not merit. A live PerformanceAllocator *would* move final quantity on
most of the book — but the per-strategy win-rate signal it would use (`PA2`) has **no demonstrated
ranking power** (M-S4 ρ +0.003; D3's inversion did not replicate out-of-sample). Whether that is
worth wiring — and its coupling to D1 (concentration), the latent 2× ceiling, and the leverage picture —
is **Rama's call**. Decision file 06's "would bind on N/N or M" question is now answered: **M = 233 of
298 (full range), 151 (modest).**

---

## C. Decision-file updates (neutral)

- **05 (D4):** candle-retention question resolved — **SURVIVES** (seed candles expire ~12–15 Oct; sample
  matures ~mid-Aug…mid-Sep; rolling 90d ≈ 62 trading days > 34 needed). D4's binding constraint remains
  **power**, not data. Preservation gap immaterial (nightly analytics backup).
- **06 (PerformanceAllocator):** "COMPUTABLE NOW" label **HOLDS** (298-trade coverage). The "masked by
  the concentration cap" premise is **refuted** — `perf_weight` is a post-cap multiplier and would change
  final_qty on **233 / 298 (78%)** trades over [0.5, 2.0], **151 / 298 (51%)** over [0.8, 1.25].
  Conditional on unlevered sizing.
- **Triage table** (`decision_readiness_triage_19jul2026.md`): the "2 of 3 COMPUTABLE-NOW proved
  otherwise" tally becomes **2 of 3 wrong, 1 (PerfAllocator) confirmed & computed** — the label was
  reliable for exactly the one item whose data *and* power were both checked and both held.
