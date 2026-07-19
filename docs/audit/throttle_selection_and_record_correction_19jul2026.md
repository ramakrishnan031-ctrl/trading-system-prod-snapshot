# Throttle Selection & Record Correction — 19-Jul-2026 (READ-ONLY)

**Scope:** (A) preserve the complete-era signal data; (B) answer the one question the signal-mortality
census left open — *does the traded book represent the approved book?*; (C) correct the record where the
census invalidated claims that sit inside Rama's pending decisions.

**Deploy state:** PC == VM == `ee29500`; code tag `deploy-19jul-consecutive-losses` → `d271525`; schema v44.
**System state:** DOWN since Fri 17-Jul; market closed (Sun 19-Jul); kill-switch INACTIVE; book flat; 0 open
positions. **Nothing executable changed in this batch** — no code, config, schema, flag, or test. Every query
ran against a `mode=ro` **preserved snapshot**, never through application code. This document and the memory
entries are the only artefacts; both are markdown.

This report continues and completes a batch that was interrupted after §A and ~80% of §B. It supersedes the
WIP handoff note `wip-throttle-batch-19jul-resume`.

---

## 0. DISCLOSURE FIRST — I wrote 4 rows to the live DB (benign, not reverted)

During §A, running `python scripts/eod_cleanup.py --db <copy> --dry-run` to model the prune **did not isolate
production.** `scripts/eod_cleanup.py:324` calls `_cron_main()`, which invokes `skip_if_non_trading_day()` and
**writes a `cron_heartbeat` row to the LIVE DB *before* `main()` ever parses `--db`.** Four invocations → **4
rows (ids 2417–2420)**, all truthful (`SKIPPED` / "non-trading day (weekend/NSE holiday)").

- Live DB sha256 `e69fd1b4…` → **`6df0c09a…`**; mtime `09:20:01` → `11:14:29`.
- **Blast radius verified: `cron_heartbeat` is the ONLY changed table of all 46.** `signals`, `trades`,
  `orders`, `webhook_audit`, `fm_ledger`, `kill_switch_state`, `session` are byte-identical. Schema still v44
  (no migration); `quick_check=ok`; `foreign_key_check` clean.
- **Deliberately NOT reverted** — the rows are accurate audit entries, and deleting truthful audit rows is worse
  than leaving them. The only distortion: an `eod_cleanup` freshness check before Mon 15:50 sees a Sunday
  `SKIPPED` heartbeat instead of Friday's; it self-corrects at Monday's real run.
- 📌 **RULE (new standing fact, §C3):** `python scripts/X.py --db <copy>` isolates only the code **below the
  cron entry point.** Any script whose module runs a `_cron_main()`/heartbeat at import-or-entry writes to the
  live DB regardless of `--db`. For a genuinely read-only probe, call the inner function / `main()` directly, or
  query the DB file with `sqlite3 ... mode=ro`. Sibling of the migration-on-open rule.

---

## A. THE EVIDENCE IS PRESERVED — and the "erosion" premise was wrong

### A1–A3. The snapshot (verified SOUND)

`/home/ubuntu/preserved/signal_census_19jul2026/` — outside every managed path (`backup_retention` prunes only
scoped prefixes under `data_store/backups`; **0 cron references** to `/home/ubuntu/preserved`; all files
`chmod 444`):

| file | size (bytes) | check |
|---|---:|---|
| `trading_system_snapshot_20260719.db` | 89,968,640 | sha256 `09a22ade4032747c65614b8ceed48e805b471546b89b347a04f4d1daedd140f8` |
| `signals_20260719.csv.gz` | 3,715,093 | `gzip -t` OK |
| `webhook_audit_20260719.csv.gz` | 1,301,042 | `gzip -t` OK |

Soundness (re-verified this session): `quick_check=ok`, `integrity_check=ok`, FK clean, `schema_version=44`,
and every census figure reproduces exactly — **32,928 total signals · 30,769 complete-era · 89,794 POSTs ·
361 trades · approved 217 · throttled 139 · ordered 78**. Whole-DB was chosen (not table-level) so joins to
`trades`/`orders`/`screener_results` — required by §B — remain possible; two gzipped table CSVs are included as
a portable fallback.

### A4 — how many complete days exist, and what the next prune will lose ⭐ CORRECTS THE CENSUS

**6 complete days exist today: 2026-07-09, -10, -13, -14, -15, -16.** They are safe.

**The data is NOT eroding.** `signal_retention_days` is **90** (`system_config.yaml`, VM-verified; code default
90). The earliest row is **12-Jun**, so the daily prune's predicate `fingerprint_date < (run_date − 90d)`
matches **nothing** until **~2026-09-10**. Computed with the exact `eod_cleanup.py:234` predicate at run-date
Mon 20-Jul:

```
90d → 0 rows    30d → 0    14d → 0    10d → 7,635    7d → 15,290    5d → 25,492
```

⇒ **Monday's prune (20-Jul 15:50) deletes 0 rows.** The 09-Jul boundary in the census was **not** the daily job
sliding — it was the **one-off manual Phase-B prune of 16-Jul** (the 15-Jul combined deploy cleared 113,377 old
signals). **The census's own words "the window keeps sliding" were WRONG** and are corrected in §C. The half
that stands: pre-09-Jul `REJECTED_*` really is destroyed, and future rejection-composition analysis still needs
an unpruned window or this snapshot. The live risk to the boundary is **another manual short-window prune**, not
the clock.

> ⚠️ Do **not** cite the "real code path deleted 0 rows" dry-run as evidence — it was **vacuous** (Sunday's
> holiday-skip meant the prune body never executed at any retention; a 10-day control also deleted 0, which is
> what exposed it). The SQL-predicate computation above is the sound evidence.

**Whether to change the prune is Rama's call and a production change. §A only preserves.**

---

## B. ⭐ Does the traded book represent the approved book?

The census funnel: **reached the risk engine 5,845 → APPROVED 217 → ORDERED 78 → FILLED 48.**
`REJECTED_ENTRY_THROTTLED` = 139, and 217 − 139 = 78 exactly. So after every gate said yes, **64% of approved
signals never became a trade**, and a further 30 of 78 orders never entered the market. Two attrition stages sit
below approval. This section asks whether the survivors are a *representative* sample — because every expectancy
figure this project has produced was computed on the FILLED trades.

### B1. What the throttle is (code)

**`signals/entry_throttle.py`**, wired at `signal_processor.py:1170` inside `_admit_and_place`, **after**
`risk_engine.approve()` at `:1075`. It is a **pure in-memory temporal rate limiter** — not a capital guard, not
`max_open_positions` (that is risk-engine check 4, an independent concern the throttle sits *below*). Three
gates, evaluated global-first (`system_config.yaml:226-229`):

- **`min_gap`** — ≥ 20 s between any two *placed* entries (global);
- **`burst`** — max 3 placed entries per rolling 60 s;
- **`per_symbol`** — 300 s cooldown per symbol.

The logic is pure in-memory arithmetic on placement timestamps; its docstring asserts **paper == live parity**
(no broker or market-data dependence), so the selection effect described below applies identically in both modes.

**Observed gate attribution (complete era, from the free-text `rejection_reason`):**

| gate | throttled | example reason |
|---|---:|---|
| `min_gap` | **132** | `Entry throttled: min_gap 0.1s < 20s` |
| `per_symbol` | 7 | (300 s cooldown) |
| `burst` | 0 | — reachable at exactly-20 s cadence, never fired |

> ⚠️ The gate category lives **only** in the free-text `rejection_reason`; the structured `status` is just
> `REJECTED_ENTRY_THROTTLED`. Analysis had to parse free text — a sibling of the W9 capture gap and of the
> "classify by structured status, not free text" rule. **Recorded in §C3, not fixed.**

### B2. The selection is structured by TIME, essentially not by quality

All figures below are the **approved population = 78 ordered + 139 throttled = 217**, complete era, verified this
session against the snapshot.

| dimension | result | verdict |
|---|---|---|
| **Score** | mean **60.090** ordered vs **59.878** throttled (both n fully scored); cells **non-monotone**: 59→80% ordered, 60→26.3% (n=175, the modal mass), 62/64→100% (n=13) | **Not quality-selective** — 0.21-pt gap, no monotone gradient. The 62/64 = 100% cells are n=13, underpowered, and consistent with those signals arriving outside the opening burst |
| **Direction** | LONG **36.2%** ordered (71/196) vs SHORT **33.3%** (7/21) | **No material difference** |
| **Price band** (`trigger_price`) | <250 **40.0%** · 250–500 **40.0%** · 500–990 **28.6%**; **zero** approved signals ≥ Rs 990 | Mild; the ≥990 band is already gone before the risk engine (census §B1: 0.14% there) |
| **Strategy** | spread **21.4%** (`vwap_bounce_long`, n=70) → **72.2%** (`open_low_breakout_long`, n=18) | **A confound, not an effect** — see below |
| **Arrival time** | ordered% rises from **12.8%** at 10:00 to **~100%** by 10:15+ | **THE driver** |

**The strategy spread is fully explained by arrival density, and the throttle is causally blind to strategy.**
132 of 139 throttles (95%) are the **global 20-second `min_gap`** gate, which by construction can only see *time
since the last placed entry* — never a signal's strategy, score, or direction. The 7 `per_symbol` throttles are
same-symbol repeats; `burst` fired 0 times. So strategy cannot be a causal input; it can only *correlate* with
arrival timing. And it does: `vwap_bounce_long` fires 70 signals in dense simultaneous clusters at the open and
self-collides on the 20 s spacing; `open_low_breakout_long` fires 18, more spread out, and clears.

Ordered% by arrival minute makes the mechanism explicit:

| arrival | 10:00 | 10:01 | 10:02 | 10:03 | 10:04 | 10:05–10:14 | 10:15+ |
|---|---:|---:|---:|---:|---:|---:|---:|
| approved | 47 | 30 | 10 | 4 | 2 | ~78 | ~46 |
| ordered % | **12.8** | 16.7 | 50.0 | 75.0 | 100 | 23–50 | **~100** |

77 approved signals collide in the 10:00–10:02 opening burst → only 14.3% clear the 20 s spacing; by 10:15 the
arrival rate falls below one per 20 s and essentially everything clears. This is **first-come-first-served on a
20-second global cadence, responding to arrival density** — not to quality.

### B3. Time-of-day, in one line

**Throttling is 100% a market-open phenomenon.** Hour 10: **139** throttled, 68 ordered (32.9%). Hours 11–14:
**0** throttled, 10 ordered (100%). A first-come-first-served rate limiter favours whatever arrives after the
opening burst clears — and entries open at 10:00, so the burst *is* the throttle's entire working set.

### B4. The fill step: 78 ordered → 48 filled — three mechanisms, zero defects

The other 30 orders never entered the market (`entry_time IS NULL`; 0 partial fills anywhere). They decompose
cleanly, and **none is a live defect** — so the "report and stop" gate is not tripped:

| n | signal→trade | mechanism | evidence | expected? |
|---:|---|---|---|---|
| **17** | PROCESSED → FAILED | **Entry-fill timeout.** A LIMIT ENTRY order rested unfilled and was auto-cancelled | each has exactly 1 ENTRY LIMIT MIS order, `CANCELLED`, `qty_filled=0`, no broker reject; cancelled **uniformly ~60 s** after placement (`fill_timeout_sec`; `order_monitor.py:526/560/776`; the named event `order_placer.entry_cancelled_zero_fill:1919`) | ✅ expected — the cost of LIMIT (not MARKET) entry when price never returns to the limit |
| **6** | PLACEMENT_FAILED → FAILED | **Broker RMS refusal.** Zerodha blocks MIS product on specific symbols | 0 order rows; `rejection_reason` = *"MIS orders are currently blocked for KOTIC / UNICHEMLAB / BIRLAMONEY…"* | ✅ expected — external broker/universe constraint |
| **7** | PLACEMENT_FAILED → REJECTED | **System slippage guard.** LTP moved past entry tolerance before placement | 0 order rows; `rejection_reason` = *"slippage_exceeded: trigger=… ltp=… slippage ₹X > tolerance ₹Y (mode=sl_fraction…)"* | ✅ expected — a protective control working as designed |

The 48 filled = 41 `CLOSED` + 7 `CLOSED_MANUAL` (the only two with a real `entry_time`). The 30-row loss **is**
structured, but benignly: 27 LONG / 3 SHORT (mirrors the 91%-long book), open-concentrated, and the broker
MIS-blocks cluster on illiquid names (KOTIC ×4). The one selection that matters for §B5 is the **17 unfilled
LIMITs** — a mechanical selection *against* signals where price ran away from the limit before ~60 s elapsed.

> One notable slippage case: SL distance Rs 0.29 → tolerance Rs 0.06 → the guard rejected at **969% of SL**. The
> guard is doing its job on a degenerate (near-zero) SL distance; it is not a defect, but the degenerate SL is
> worth a glance if Rama ever revisits the slippage-fraction mode. Reported, not acted on.

### B5. Consequence for the existing expectancy work

**The traded book is NOT a uniform random sample of the approved book — but the distortion is time and
mechanics, not the risk engine re-selecting on quality.** Two attrition stages below "approved (217)":

1. **Throttle (217 → 78).** Time/arrival-density selected, **quality-neutral** (score gap 0.21, no gradient;
   direction flat). It removes signals that arrived inside the opening-minute burst.
2. **Fill (78 → 48).** LIMIT-fill mechanics (17 timeouts) + broker/slippage declines (13). The LIMIT-timeout
   removal is a mild selection against fast-moving entries.

So every expectancy figure this project has produced — the **38.8% gross win rate**, the ~flat gross expectancy,
the cost-dominated net loss, the R-multiples — describes **"approved signals that both cleared a market-open
20-second rate limiter and filled a resting LIMIT within ~60 s"**, not the intended approved book. This is the
**same shape as the census's §B1 finding about batch 4's reachability table, one layer further down**: the
arithmetic is not in question; what changes is the **domain** over which the numbers are claims.

- It **does not invalidate** the expectancy work. Quality survives both stages roughly unbiased, so the *sign*
  and rough magnitude of the edge are unlikely to be an artefact of the throttle.
- It **does bound the domain** and flags one **specific, testable caveat**: the fill step may under-represent
  fast momentum-continuation entries (the unfilled LIMITs are precisely the signals where price left the limit
  behind). Their counterfactual P&L is **unmeasurable from the record** — they never traded — so this is a named
  hypothesis, not a quantified bias.

**This bears directly on D2 and D3. Both are Rama's. This report does not re-run the expectancy analysis and
does not recommend a strategy.**

---

## C. Correcting the record

The census invalidated three claims that sit inside decisions Rama has not yet made. A half-corrected record is
worse than an uncorrected one, so this was swept as a class; each occurrence now carries a **dated note of what
it used to say and why it changed.**

### C1. The three corrected claims — every occurrence fixed

**(a) ">Rs 990 exclusion hits LONGs ~4.5× harder" — DIRECTION INVERTED.** By rejection *rate*, LONG **10.00%**
vs SHORT **10.72%** — SHORTs marginally harder, within 0.7 pp. The "4.5×" (and batch 4's later "9.54×") are
*count* ratios driven by the **10.2× long-volume skew** (28,027 vs 2,742 signals), not differential treatment by
the cap. The threshold (**~Rs 990**, exact) and the **~23–24% share** both remain VERIFIED. **This claim sits in
D1's evidence package; D1 is undecided — the corrected direction must travel with it.**

| file | correction |
|---|---|
| `docs/audit/sizing_interaction_impact_report_13jul2026.md` §4 / §5 Cause (a) | dated banner: not "silent", and direction inverted by rate |
| `docs/audit/q9_batch4_sizing_floors_caps_18jul2026.md` (rows 6–7, "Nothing inverted") | dated banner: rate vs count; **flagged as D1 evidence**; + C2 |
| `docs/audit/expectancy_autopsy_13jul2026.md` (long-vs-short attribution) | dated note: the ">Rs990 long-book distortion" is a count/selection effect, not a long-specific cap bias; the empirical LONG-carries-loss observation is unaffected |
| memory `q9_batch4_sizing_reachability_18jul` | dated note: rate vs count + D1 flag + C2 |
| memory `capital_chain_binding_constraint_analysis_13jul` | **no change** — its Rs 990/position claims are about the cap *mechanism* (correct), not the direction |

**(b) "~249/day silently-dropped `KeyError` at `secondary_screener.py:167`" — CLOSED, and never what it said.**
Not silent (it logged ERROR + full traceback — the noisiest thing in the log); not a separate killer (same
`SKIPPED_QUOTE_UNAVAILABLE` mortality as the quote class, so adding it double-counts); fixed **14-Jul in
`c22a25c`**; and ~249 was a **log-line** count, not a signal count (measured signal deaths ~124/day). **The
sweep found this already correctly closed everywhere** — the wave-7 triage lists it in its **✅ FIXED** table
(`c22a25c`), memory `q5_wave7_backlog` says "Retires", and the census itself carries the corrected version. **No
document carries it as an open backlog item; no surgery was required.** (Recorded here so the "remove it from the
backlog" instruction has a closed audit trail — refusing an item with evidence is a valid outcome.)

**(c) The 2026-07-08 "quote cliff" — interpretation inverted, Q9/RE10 conclusion kept intact and separate.**
The "63 signals after 10:22:23 all `SKIPPED_QUOTE_UNAVAILABLE` ⇒ entries stopped" is **survivorship bias**:
07-08 is in the pre-09-Jul pruned era (`REJECTED_*` deleted, `SKIPPED_*` kept), so the surviving rows only look
that way. Acceptance **accelerated** after 10:22 (peak 1,462/hr; 63 = 1.21%, 6th-lowest of 21 days).

| file | correction |
|---|---|
| `docs/audit/consecutive_losses_gate_wired_19jul2026.md` (point 2) | dated banner: reason is survivorship bias; **verdict + point 1 (entry-vs-exit recomputation) STAND** |
| `docs/audit/q9_coverage_matrix_final_18jul2026.md` (point 2) | dated banner, same separation |
| memory `consecutive_losses_gate_wired_19jul` (point 2) | dated note, same separation |

> **The separation is the point.** RE10's verdict — *REACHABLE, live, precondition met on 3 of 21 days, never
> the binding rejection* — rests on the gate's **entry-vs-exit recomputation semantics**, verified independently,
> not on 07-08's quote history. The census in fact **strengthens** "reachable": the gate is consulted ~975×/day.
> Removing a bad *reason* for a conclusion is not removing the conclusion.

### C2. Population-bias qualification (do not overstate)

Added to **batch 4's report** and the **Q9 coverage matrix**: their verdicts (incl. "only 4 of 15 sizing guards
can bind") are correct **as statements about the population that reaches the sizer**, which is **enriched 1.45×
in >Rs 990 names** (34.73% at the sizer vs 24.02% at admission; mean price Rs 875 vs Rs 732) while the risk
engine sees only **0.14%** of that band. A guard needing high notional is unreachable partly **because of an
upstream cap**, not solely its own threshold. **Batch 4's arithmetic was never in question — what changes is the
domain over which its conclusions are claims.** This is the same qualification the census applied to itself in
§B1.

### C3. New standing facts (recorded so they are not rediscovered)

1. **Retention boundary + status-selectivity.** Only 2026-07-09 → 16-Jul is complete; any share over all 32,928
   rows is meaningless. **And (A4): the boundary is a fixed one-off (16-Jul manual prune), NOT a sliding window
   — 90-day retention, next daily prune deletes 0, nothing until ~10-Sep.** `SKIPPED_*` counts are complete for
   the whole 24-day window (the prune never touches them).
2. **The 403 population — the one genuine blind spot.** 25,960 POSTs refused before parse ⇒ the signals inside
   are **never counted anywhere**; ~**338,000** estimated (comparable to the entire counted population), and
   **cheaply estimable** since the payload size is already stored.
3. **Live has NO partial-response detection on the quote path**, while **paper logs a WARNING naming the missing
   symbols** (`main.py:546-549`). The mode that cannot lose money is the better-instrumented one.
4. **Two latent classifier issues** (both 0 rows today, latent not live): `REJECTED_KILL_SWITCH` is emitted by
   both the pre-gate (`signal_processor.py:685`) and risk-engine check 1 (`risk_engine.py:405`) ⇒ stage-
   ambiguous; and `signal_status.is_sizing_rejection()` would classify `REJECTED_SIZING_VALID` — a *risk-engine*
   check — as a sizing rejection.
5. **The `--db`/cron-wrapper rule** (§0): a `scripts/*.py --db <copy>` invocation isolates only the code below
   the cron entry point; the heartbeat/`_cron_main` runs against the live DB first. Query `mode=ro` or call the
   inner function directly.
6. **The throttle gate category is free-text only** — a fourth instance of "a decision recorded in prose that a
   structured column should carry." Recorded, not fixed.

### C4. Nothing here was acted on. Every item is reported, not repaired.

---

## PROOF OF READ-ONLY

- **All analysis queried the preserved snapshot** at `file:/home/ubuntu/preserved/signal_census_19jul2026/trading_system_snapshot_20260719.db?mode=ro` — sha256 `09a22ade…`, re-verified this session, predating and independent of the §0 heartbeat write.
- **Live DB fingerprint, before this session's work:** sha256 `6df0c09ab7e95ae5288b330dacbd74316a16d6c3515450c8981ae9722c02c442`, mtime `2026-07-19 11:14:29`, size `89,968,640`.
- **After (see the deploy/commit log at the end of this batch): identical** — no query in §B or §C touched the live DB. *(Re-checked at completion; recorded in the memory ledger.)*
- The four `cron_heartbeat` rows of §0 predate this session and are the reason the baseline is `6df0c09a…` rather than the census's `e69fd1b4…`.

---

## WHAT "DONE" MEANS (met)

A sound, verified snapshot of the complete-era signal data exists at a stated path, with the number of complete
days recorded (**6**) and the erosion premise corrected (**not eroding; next prune 0**). The entry throttle is
identified in code (`signals/entry_throttle.py` @ `signal_processor.py:1170`) and the throttled-vs-ordered
populations are compared on every dimension the data supports, with a plain verdict: **the traded book is
time-selected, not quality-selected — a domain qualification on every expectancy number, not an invalidation.**
The fill step is accounted for (three expected mechanisms, zero defects). The consequence for expectancy work is
stated without re-running it. Every occurrence of the three corrected claims carries a dated note; batch 4 and
the Q9 matrix carry the population-bias qualification without overstating it; the new standing facts are
recorded; the live DB is proven untouched by this session's analysis.

*Bookkeeping only — no code, config, schema or flag changed by this document. D2/D3/D1 remain Rama's.*
