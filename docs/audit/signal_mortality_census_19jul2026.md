# The Signal Mortality Census — 19-Jul-2026

**Read-only.** No code, config, schema or flag was changed. No test was written. No production
process was restarted. The only artefact is this file.

**State at time of work:** PC == VM == `7319d06`; tag `deploy-19jul-consecutive-losses` →
`d271525`; schema v44; system stopped since Fri 17-Jul; market closed; book flat; kill switch
inactive.

**Question asked:** Q9 proved the safety gates fire correctly *when consulted*. This asks how
often they are consulted at all — of every signal that arrives, what fraction reaches the risk
engine, and where do the rest die?

---

## THE HEADLINE, IN FOUR LINES

1. **19.00%** of admitted signals reach the risk engine (5,845 of 30,769 over 6 complete days).
   On raw arrivals the figure is 3.54%, but that denominator is 81% intentional de-duplication
   and is **not** the meaningful number — see §A2.
2. **Zero signals are unaccounted for.** Every status in the table maps to a known stage; there
   are no unmapped statuses, no NULL statuses, and no signals stuck in a non-terminal state.
3. The **silent-drop population is 134,342** (complete era) — but "silent" here means *no
   per-signal reason*, not *unknown*. The count is reported to Rama daily, tagged `[W9]
   PENDING_CAPTURE`. It is a **known, tracked capture gap**, not a blind spot.
4. **All three inherited claims were wrong in some part.** One count was right but its
   interpretation was backwards; one described a defect that was never silent and was fixed on
   14-Jul; one had two components right and its direction component inverted.

**The census found no scandal.** Most signals die for good reasons, and Rama can see the
aggregate of the ones that don't. The substantive finding is not a hidden failure — it is a
**measurement boundary** (§A0) and a **population bias** (§B1) that affect how earlier results
should be read.

---

## A0. SCOPE, AND THE RETENTION BOUNDARY THAT CONSTRAINS EVERYTHING

`signals` holds **32,928** rows spanning 2026-06-12 → 2026-07-16.
`webhook_audit` holds **89,794** POSTs over the same span: 146,305 accepted, 643,402 rejected,
25,960 blocked with 403, 18 with 503.

**The `signals` table cannot answer "every signal ever recorded."** `scripts/eod_cleanup.py:234`
prunes with this predicate:

```
(status IN ('EXPIRED','DUPLICATE') OR status GLOB 'REJECTED*')
AND fingerprint_date < ?
AND NOT EXISTS (SELECT 1 FROM trades t WHERE t.signal_id = signals.signal_id)
```

This is **status-selective**: it destroys `REJECTED_*` and keeps `SKIPPED_*` and the qualified
family. The observed cutoff is **2026-07-09**.

> **⚠️ NOTE 19-Jul-2026 (§A4 correction, throttle/record-correction batch).** The 09-Jul cutoff is a **fixed one-off** — the 16-Jul manual Phase-B prune (which cleared 113,377 old signals) — **not** a sliding window. Steady-state `signal_retention_days` is **90** and the earliest row is **12-Jun**, so the *daily* prune's `fingerprint_date < ?` matches **0 rows** every day until **~2026-09-10**. The data is not eroding day-to-day. See the corrected item under "What cannot be measured" below, and `docs/audit/throttle_selection_and_record_correction_19jul2026.md`.

| date | POSTs | accepted | webhook-rejected | 403 | signal rows | rows/accepted |
|---|---:|---:|---:|---:|---:|---:|
| 2026-06-12 | 3675 | 7667 | 33712 | 162 | 96 | 1.3% |
| 2026-06-15 | 4037 | 2087 | 8598 | 3146 | 11 | 0.5% |
| 2026-06-16 | 4138 | 32 | 23 | 4127 | 3 | 9.4% |
| 2026-06-17 | 4146 | 4694 | 20808 | 1674 | 85 | 1.8% |
| 2026-06-18 | 3526 | 2870 | 11904 | 2251 | 73 | 2.5% |
| 2026-06-19 | 1931 | 5501 | 24139 | 495 | 57 | 1.0% |
| 2026-06-22 | 3963 | 15073 | 68741 | 468 | 463 | 3.1% |
| 2026-06-23 | 3785 | 5718 | 25516 | 1924 | 128 | 2.2% |
| 2026-06-24 | 4082 | 7072 | 30809 | 466 | 51 | 0.7% |
| 2026-06-25 | 4479 | 7857 | 33782 | 535 | 22 | 0.3% |
| 2026-06-29 | 4556 | 8880 | 39669 | 575 | 75 | 0.8% |
| 2026-06-30 | 4074 | 8477 | 37860 | 752 | 311 | 3.7% |
| 2026-07-01 | 3664 | 1867 | 7985 | 2356 | 26 | 1.4% |
| 2026-07-02 | 3859 | 7970 | 35045 | 721 | 190 | 2.4% |
| 2026-07-03 | 4592 | 9666 | 42916 | 809 | 175 | 1.8% |
| 2026-07-06 | 4282 | 8299 | 36349 | 698 | 171 | 2.1% |
| 2026-07-07 | 4141 | 6594 | 28620 | 742 | 144 | 2.2% |
| 2026-07-08 | 3907 | 5212 | 22584 | 694 | 78 | 1.5% |
| **2026-07-09** | 3856 | 7764 | 34028 | 594 | **7764** | **100.0%** |
| **2026-07-10** | 3957 | 7814 | 34897 | 736 | **7814** | **100.0%** |
| **2026-07-13** | 3290 | 6973 | 30579 | 589 | **6973** | **100.0%** |
| **2026-07-14** | 3496 | 3596 | 15391 | 576 | **3596** | **100.0%** |
| **2026-07-15** | 3814 | 4317 | 18283 | 664 | **4317** | **100.0%** |
| **2026-07-16** | 544 | 305 | 1164 | 206 | **305** | **100.0%** |

From 09-Jul the match is **exact** — every accepted signal still has its row. Before it, only
1.5% survive on average.

**What the pruned-era residue actually is** (2,159 rows, 12-Jun → 08-Jul):

| status | count | share |
|---|---:|---:|
| `SKIPPED_QUOTE_UNAVAILABLE` | 1876 | 86.89% |
| `PROCESSED` | 207 | 9.59% |
| `PLACEMENT_FAILED` | 74 | 3.43% |
| `RESERVED` | 2 | 0.09% |

**Zero `REJECTED_*` rows survive.** This is the single most important methodological fact in the
census, and it cuts both ways:

- ✅ **`SKIPPED_*` counts are COMPLETE for the whole window** — the prune never touches them. So
  every quote-unavailable figure in this report, including for 08-Jul, is trustworthy across all
  24 days.
- ❌ **Any share-of-total computed over all 32,928 rows is meaningless** — it blends 6 complete
  days with 18 days of status-selected survivors. §A1a is shown only to demonstrate this.

**Everything quantitative below is computed on the complete era (2026-07-09 → 2026-07-16, 6
trading days, 30,769 rows) unless explicitly stated.**

---

## A1. TERMINAL-STATUS CENSUS

Vocabulary is `reports/signal_status.py`; no second classifier was invented.

### Stage attribution (derived from code, not inferred from names)

| stage | assigns | source |
|---|---|---|
| **0 webhook admission** | `INVALID_SYMBOL`, `INVALID_PRICE`, `EXPIRED`, `IN_PROCESS`, `DUPLICATE`, `QUEUE_FULL`, `STORE_ERROR`, `REJECTED_EXCLUDED_SYMBOL` | `webhook_receiver.py:807-973` — **all return before the INSERT; no row is ever created** |
| **1 pre-gates** | `REJECTED_STRATEGY_CONTROL`, `_SHADOW_INNING_ACTIVE`, `_STRATEGY_CIRCUIT_BREAKER`, `_STRATEGY_POSITION_LIMIT`, `_OUTSIDE_ENTRY_WINDOW`, `_EXPIRED`, `_UNKNOWN_STRATEGY`, `_TRADE_TYPE` | `signal_processor.py:638-768` |
| **2 screening** | `SKIPPED_QUOTE_UNAVAILABLE`, `REJECTED_SCORE_*`, `_CIRCUIT_PROXIMITY`, `_NOT_MIS_TRADABLE` | `secondary_screener.py:175/187/215`, `signal_processor.py:776` |
| **3 sizing** | `REJECTED_SIZING_*` (except `_VALID`) | `signal_processor.py:901` |
| **4 risk engine** | `REJECTED_CAPITAL`, `_OPEN_POSITIONS`, `_DAILY_TRADES`, `_CONSECUTIVE_LOSSES`, `_DAILY_LOSS`, `_SECTOR_EXPOSURE`, `_CONTRARY_POSITION`, `_DUPLICATE_SYMBOL`, `_SIZING_VALID` | `risk_engine.py:403-658` (10 checks), via `signal_processor.py:1085` |
| **5 order path** | `REJECTED_ENTRY_THROTTLED`, `_RESERVE_*`, `_KILL_SWITCH_LATE`, `_SHUTDOWN`, `PROCESSED`, `PLACEMENT_FAILED`, `RESERVED`, `TRADED` | `signal_processor.py:1156-1176`, order placer |

The risk engine's 10 checks, in evaluation order (`risk_engine.py`, `checks_run.append`):
`KILL_SWITCH` → `SIZING_VALID` → `CAPITAL` → `OPEN_POSITIONS` → `DAILY_TRADES` →
`CONSECUTIVE_LOSSES` → `DAILY_LOSS` → `SECTOR_EXPOSURE` → `CONTRARY_POSITION` →
`DUPLICATE_SYMBOL`. This independently confirms the §0.1 correction: `CONSECUTIVE_LOSSES` is
check 6, `DAILY_LOSS` check 7 — the latter cannot mask the former.

> ⚠️ **One genuine attribution ambiguity.** `REJECTED_KILL_SWITCH` is emitted by *both* the
> pre-gate (`signal_processor.py:685`) and risk-engine check 1 (`risk_engine.py:405`). The
> status alone cannot say which stage killed the signal. **Latent, not live** — zero such rows
> exist in the window. Recorded, not fixed.

### A1b. The census (complete era, 30,769 rows)

Collapsed to reporting family per `signal_status.family()`:

| family | stage | kind | count | share |
|---|---|---|---:|---:|
| `REJECTED_SCORE` (29→59) | 2 screening | DECISION | 10,949 | 35.58% |
| `REJECTED_STRATEGY_CONTROL` | 1 pre-gates | DECISION | 5,685 | 18.48% |
| `REJECTED_DAILY_TRADES` | **4 risk engine** | DECISION | 5,146 | 16.72% |
| `REJECTED_SIZING_CONCENTRATION` | 3 sizing | DECISION | 3,098 | 10.07% |
| `REJECTED_SHADOW_INNING_ACTIVE` | 1 pre-gates | DECISION | 2,801 | 9.10% |
| `REJECTED_STRATEGY_CIRCUIT_BREAKER` | 1 pre-gates | DECISION | 898 | 2.92% |
| `SKIPPED_QUOTE_UNAVAILABLE` | 2 screening | **DATA FAILURE** | 725 | 2.36% |
| `REJECTED_CIRCUIT_PROXIMITY` | 2 screening | DECISION | 539 | 1.75% |
| `REJECTED_OPEN_POSITIONS` | **4 risk engine** | DECISION | 443 | 1.44% |
| `REJECTED_STRATEGY_POSITION_LIMIT` | 1 pre-gates | DECISION | 229 | 0.74% |
| `REJECTED_ENTRY_THROTTLED` | 5 order path | DECISION | 139 | 0.45% |
| `PROCESSED` | 5 order path | DECISION | 65 | 0.21% |
| `REJECTED_DUPLICATE_SYMBOL` | **4 risk engine** | DECISION | 39 | 0.13% |
| `PLACEMENT_FAILED` | 5 order path | **DATA FAILURE** | 13 | 0.04% |

**By stage:**

| stage | count | share of admitted |
|---|---:|---:|
| 1 pre-gates | 9,613 | 31.24% |
| 2 screening | 12,213 | 39.69% |
| 3 sizing | 3,098 | 10.07% |
| 4 risk engine | 5,628 | 18.29% |
| 5 order path | 217 | 0.71% |

**Integrity checks — all clean:** unattributed statuses **0**; NULL/empty statuses **0**; signals
stuck in `QUEUED`/`PROCESSING`/`PENDING` **0**. Per `signal_status.has_explicit_disposition()`,
the schema-drift alarm reads **zero**.

---

## A2. THE FUNNEL

Window 2026-07-09 → 2026-07-16, 6 trading days.

| stage | count | share of arrived | survival from previous |
|---|---:|---:|---:|
| arrived (webhook, 200-series) | 165,111 | 100.000% | — |
| admitted (signals row created) | 30,769 | 18.635% | 18.6% |
| survived pre-gates | 21,156 | 12.813% | 68.8% |
| priced (quote obtained) | 20,431 | 12.374% | 96.6% |
| survived screening | 8,943 | 5.416% | 43.8% |
| **reached the RISK ENGINE** | **5,845** | **3.540%** | **65.4%** |
| approved by risk engine | 217 | 0.131% | 3.7% |
| ordered | 78 | 0.047% | 35.9% |
| filled | 48 | 0.029% | 61.5% |

### ⭐ REACHED THE RISK ENGINE = 5,845 — **19.00% of admitted**, 3.54% of raw arrivals

**Which denominator is honest?** 19.00%. The 165,111 "arrived" figure counts the *same signal
re-pushed* many times: Chartink POSTs each scanner roughly **once per minute** (370 POSTs across
09:20–15:29 on 09-Jul), against a **300s dedup TTL** (`system_config.yaml:213`). A symbol
persistently in a scan is therefore admitted once and refused four times — **an expected 80%
duplicate share**. Observed webhook-rejection share across the full window is **81.5%**
(643,402 / 789,707). The agreement is close enough that de-duplication accounts for essentially
the whole gap. Quoting "3.54% of signals reach the risk engine" would be technically true and
substantively misleading.

**Independently verified.** The 5,845 figure was derived from DB statuses. Counting
`risk_engine.approve` lines in the application logs — a completely separate source — gives
2548 + 3146 + 43 + 42 + 50 + 16 = **5,845 exactly**. Two independent records agree to the unit.

**The gates are being consulted.** 5,845 approve() calls in 6 days is roughly 975/day. Q9's
programme is not testing a dead code path.

---

## A3. DECISION / DATA FAILURE / SILENT DROP

### Recorded deaths (complete era)

| kind | count | share of admitted |
|---|---:|---:|
| **DECISION** — evaluated, declined. Working as designed. | 30,031 | **97.60%** |
| **DATA FAILURE** — could not be evaluated | 738 | **2.40%** |
| **SILENT DROP** — vanished with no recorded reason | **0** | **0.00%** |

Of the 738 data failures: 725 `SKIPPED_QUOTE_UNAVAILABLE`, 13 `PLACEMENT_FAILED`.

**Among signals that get a row, the system is in excellent shape.** 97.6% of deaths are
deliberate decisions, and not one signal is unaccounted for.

### The population with no row at all — the real answer to "silent"

| population | complete era | full window | recorded as |
|---|---:|---:|---|
| webhook-layer rejections | **134,342** | **643,402** | aggregate integer only |
| 403-blocked POSTs | 3,365 | 25,960 | POST count only; **signal count never computed** |
| 503 POSTs | **0** | 18 | POST count |

**⭐ THE SILENT-DROP COUNT IS 134,342 — with a large and important qualification.**

These signals have **no row and no log line**. That is proven, not assumed: grepping ~500MB of
logs (30-day retention) for every admission-layer status returns

```
IN_PROCESS = 0   QUEUE_FULL = 0   STORE_ERROR = 0
INVALID_SYMBOL = 0   INVALID_PRICE = 0   EXCLUDED_SYMBOL = 0
DUPLICATE = 294  ← all 294 are the risk engine's DUPLICATE_SYMBOL check, not webhook dedup
```

The dedup path (`webhook_receiver.py:843-846`) logs nothing at all by design.

**But they are not invisible to Rama, and this gap is already known.**
`reports/daily_trade_review.py:699-710` documents the exact mechanism in a comment block, and
lines 897/910/1008/2307 render it into the daily xlsx:

- `Received (webhook POST-level)` — the full arrival count
- `Dropped pre-storage` — tagged **`[W9]`** and **`PENDING_CAPTURE`**, on a grey fill

So the honest statement is: **no signal is unaccounted for at the count level; 134,342 are
accounted for only as an undifferentiated total, and that limitation is disclosed on the face of
the report Rama reads.** This is a tracked capture gap (W9), not a hidden failure.

**What is genuinely at risk inside that number:** `QUEUE_FULL` (backpressure) and `STORE_ERROR`
(disk/DB failure) are real failures that would be indistinguishable from benign dedup. They can,
however, be bounded — both are in `_RETRYABLE_STATUSES` and force a **503**. In the complete era
there were **zero** 503s; across the whole window, **18** POSTs. So retryable failures are
provably near-zero. The classes that remain unbounded are `INVALID_SYMBOL`, `INVALID_PRICE`,
`EXPIRED` and `IN_PROCESS`.

---

## A4. TIME-OF-DAY PROFILE — and was 2026-07-08 exceptional?

### Deaths by hour (complete era)

| hour | DECISION | DATA FAILURE | total | data-failure rate |
|---|---:|---:|---:|---:|
| 10 | 3,568 | 72 | 3,640 | 1.98% |
| 11 | 4,560 | 92 | 4,652 | 1.98% |
| 12 | 5,795 | 154 | 5,949 | 2.59% |
| 13 | 7,205 | 200 | 7,405 | 2.70% |
| 14 | 8,903 | 220 | 9,123 | 2.41% |

**The data-failure rate is flat — 1.98% to 2.70% across the whole session.** There is no
clustering, no time-of-day signature, nothing that looks like "something stopping". Volume rises
through the day; the *failure rate* does not.

(Hours 09 and 15 are absent because the entry window 403s everything outside 10:00–15:00 — see
§A5 claim 1 and §B3.)

### Quote-unavailable per day — trustworthy across ALL 24 days (this status survives the prune)

| date | quote-unavail | accepted | rate | | date | quote-unavail | accepted | rate |
|---|---:|---:|---:|---|---|---:|---:|---:|
| 06-12 | 96 | 7667 | 1.25% | | 07-01 | 18 | 1867 | 0.96% |
| 06-15 | 2 | 2087 | 0.10% | | 07-02 | 176 | 7970 | 2.21% |
| 06-17 | 65 | 4694 | 1.38% | | 07-03 | 142 | 9666 | 1.47% |
| 06-18 | 47 | 2870 | 1.64% | | 07-06 | 145 | 8299 | 1.75% |
| 06-19 | 52 | 5501 | 0.95% | | 07-07 | 130 | 6594 | 1.97% |
| 06-22 | 449 | 15073 | 2.98% | | **07-08** | **63** | **5212** | **1.21%** |
| 06-23 | 111 | 5718 | 1.94% | | 07-09 | 114 | 7764 | 1.47% |
| 06-24 | 28 | 7072 | 0.40% | | 07-10 | 139 | 7814 | 1.78% |
| 06-29 | 59 | 8880 | 0.66% | | **07-13** | 249 | 6973 | **3.57%** |
| 06-30 | 293 | 8477 | **3.46%** | | 07-14 | 93 | 3596 | 2.59% |
| | | | | | 07-15 | 130 | 4317 | 3.01% |

**Window mean 1.78% (volume-weighted; 1.75% unweighted). 2026-07-08 was 1.21% — comfortably
BELOW average**, the **6th-LOWEST of the 21 days** with any quote failures. The genuinely
elevated days were **07-13 (3.57%)**, **06-30 (3.46%)** and **07-15 (3.01%)** — none of which
anyone has ever flagged.

**2026-07-08 was not exceptional. It was a better-than-average day for quote availability.**

---

## A5. THE THREE INHERITED CLAIMS

### Claim 1 — "On 2026-07-08 all 63 signals arriving after 10:22:23 were `SKIPPED_QUOTE_UNAVAILABLE`; they died upstream so the gate was never consulted."

**Verdict: COUNT VERIFIED · INTERPRETATION INVERTED.**

The number 63 is exactly right. What it means is the opposite of what was concluded.

- On 07-08 the webhook accepted **5,212** signals. The 63 skips are **1.21%** of the day, not
  "everything after 10:22".
- **Acceptance did not stop — it accelerated.** Hourly accepted counts: 10h **464** → 11h **676**
  → 12h **1,179** → 13h **1,431** → 14h **1,462**. The busiest hour of the day was 14:00, four
  hours *after* the supposed failure. The system priced signals all afternoon.
- The appearance of a cliff is **survivorship bias from the retention prune**. 07-08 sits in the
  pruned era; every `REJECTED_*` row from that day was deleted, and `SKIPPED_*` was kept. What
  remains is 63 skips, 9 `PROCESSED`, 6 `PLACEMENT_FAILED` — so of course everything after the
  last qualified row is a skip.
- The boundary is not even 10:22:23: the last surviving non-skip row is **10:20:20** and the
  first surviving skip is **12:36:12**. There is a **2h16m hole with no surviving row of any
  kind** — precisely the interval in which the deleted rejections lived.

**The gates were consulted on 07-08.** They were consulted for the ~5,134 signals whose
rejection records the prune removed.

> This does not weaken Q9's conclusion that the consecutive-losses gate has never fired — that
> rests on the gate's own recomputation semantics, verified separately. It removes one *reason*
> offered for it.

### Claim 2 — "A silently-dropped `KeyError` at `screening/secondary_screener.py:167`, ~249/day."

**Verdict: INVERTED on both counts, and already fixed.**

- **It was never silent.** The old code was `raise KeyError(f"No quote returned for {symbol}")`,
  caught by the `except Exception:` handler **two lines below it in the same function**, which
  logged `ERROR` **with a full traceback**. Far from being invisible, it was the single noisiest
  thing in the log — the commit message for the fix calls it a flood that "masked real ERRORs".
- **It is not a separate killer.** Both the old raise-path and the current path terminate in
  `_make_skipped("SKIPPED_QUOTE_UNAVAILABLE", ...)` followed by `_persist(...)`. It has always
  been the *same* mortality as claim 1, wearing a different name. **Adding claims 1 and 2
  together would double-count the same deaths.**
- **Fixed 14-Jul-2026** in commit `c22a25c`, *"fix(q5): quiet the ~249/day quote-unavailable
  ERROR-traceback flood"*. The behaviour change was to logging only — one INFO line, no
  traceback, no raise. Mortality was unchanged, which is why the census still sees the class.
- **The ~249/day figure is plausible but was a log-line count, not a signal count.** Measured
  signal deaths are ~124/day (2,601 over 21 days). The two are not the same quantity.

This claim has been carried as an open defect. **It is closed, and it was never what it said it
was.**

### Claim 3 — "Stocks above ~Rs 990 excluded by the concentration cap, ~23.3% of the universe, hitting LONGs ~4.5x harder."

**Verdict: threshold VERIFIED (and sharper than claimed) · share VERIFIED · direction INVERTED.**

**The threshold is real and startlingly clean:**

| price band | rows | `REJECTED_SIZING_CONCENTRATION` | rate |
|---|---:|---:|---:|
| < 250 | 7,845 | 0 | **0.00%** |
| 250–500 | 7,844 | 0 | **0.00%** |
| 500–990 | 7,689 | 0 | **0.00%** |
| 990–2000 | 5,399 | 2,281 | **42.25%** |
| > 2000 | 1,992 | 817 | **41.01%** |

**Not one signal below Rs 990 was ever rejected for concentration; above it, ~42% are.** All
3,098 concentration rejections are above 990. The "~Rs 990" figure is exact.

**The share is verified:** 7,390 of 30,769 signals carry `trigger_price > 990` = **24.02%**
(claimed 23.3%).

**The direction component is inverted:**

| direction | rows | concentration rejects | rate | share > Rs990 |
|---|---:|---:|---:|---:|
| LONG | 28,027 | 2,804 | **10.00%** | 24.43% |
| SHORT | 2,742 | 294 | **10.72%** | 19.84% |

**SHORTs are hit marginally harder, not LONGs 4.5x harder.** The rates are within 0.7pp.

The likely origin of "4.5x" is a confusion of rate with count: LONGs absorb 2,804 of 3,098
rejections (90.5%), which is **9.5x** the short count — but that is driven entirely by the
**10.2x volume skew** (28,027 LONG vs 2,742 SHORT rows), not by the cap treating longs
differently. Directions are taken from `config/strategy_direction_registry.yaml`, never from the
`_long`/`_short` name suffix.

---

## A6. WOULD RAMA KNOW?

| killer | count (6d) | daily xlsx | Telegram | ops dashboard | verdict |
|---|---:|---|---|---|---|
| `REJECTED_SCORE` (all) | 10,949 | ✅ family breakdown | — | ✅ | **VISIBLE** |
| `REJECTED_STRATEGY_CONTROL` | 5,685 | ✅ | — | ✅ | **VISIBLE** |
| `REJECTED_DAILY_TRADES` | 5,146 | ✅ | — | ✅ | **VISIBLE** |
| `REJECTED_SIZING_CONCENTRATION` | 3,098 | ✅ (correctly named since 18-Jul) | — | ✅ | **VISIBLE** |
| `REJECTED_SHADOW_INNING_ACTIVE` | 2,801 | ✅ | — | ✅ | **VISIBLE** |
| `SKIPPED_QUOTE_UNAVAILABLE` | 725 | ✅ skipped bucket | ❌ | ✅ | **VISIBLE (aggregate)** |
| webhook-layer drops | 134,342 | ⚠️ **aggregate only, tagged `[W9] PENDING_CAPTURE`** | ❌ | ✅ count | **PARTIALLY VISIBLE — disclosed** |
| **403-blocked POSTs** | 3,365 POSTs | ❌ | ❌ | ⚠️ POST count | **⭐ NOT VISIBLE as signals** |

**The reporting layer is in good order.** Since the 18-Jul classification fix, every killer that
produces a signals row is named correctly and grouped by structured status. The webhook drop is
reported with an explicit "we have not captured the detail" tag — which is the honest way to
present a known gap.

**⭐ The one genuine blind spot is the 403 population.** 25,960 POSTs across the window were
refused before their payload was parsed, so `signals_accepted` and `signals_rejected` are both
**0** and the signals inside were never counted anywhere. Nothing tells Rama how many signals
those POSTs carried.

These are *correctly* refused — they arrive outside the entry window (this is the known
`entry_start:10:00` behaviour, which 403s all pre-10:00 POSTs and makes an ordinary morning look
like an outage). The issue is not the refusal; it is that the volume is unmeasured. It is
estimable: parsed POSTs run **39.32 bytes/signal**, and 403 POSTs average **512.7 bytes** →
roughly **13 signals each ≈ 338,000 signals** over the window, comparable in size to the entire
counted population. **Reported, not fixed.**

---

## A7. PAPER vs LIVE (Rule #5)

**Empirically there is nothing to compare: all 361 trades in the window are `mode=LIVE`.** Zero
paper trades. The census is a pure live measurement.

**Structurally, the quote path — the source of the system's only significant data-failure class —
differs by mode**, which matters for any future paper-derived statistic:

- **LIVE:** `zerodha_adapter.get_quote` (`:1594-1619`) calls Kite directly and builds a dict from
  whatever returns. **A missing symbol is simply absent** — there is no partial-response check.
- **PAPER:** the same method short-circuits at `:1579` to an injected `_quote_provider`.
  `main._make_paper_quote_provider` (`:468`) **also fetches real Kite quotes**, so the underlying
  data source is the same — but it applies **its own alias map** (`:533`) and **its own 0.35s
  rate limit** (`:520`).

Two consequences worth recording:

1. **Rate:** because both modes ultimately query Kite, quote-unavailability rates should be
   broadly comparable — but they are **not guaranteed identical**, because the paper path
   translates symbols through a separate alias map. An alias divergence would show up as a
   different miss rate. Untested; no paper data exists to test it against.
2. **⭐ Observability is inverted from what you'd expect — paper is better instrumented than
   live.** The paper provider detects a partial response and logs a **WARNING naming the missing
   symbols** (`main.py:546-549`). The live path has **no equivalent detection at all**; the only
   trace is one INFO line per signal from the screener, with no aggregate. The mode that cannot
   lose money is the mode that tells you when it lost a quote.

---

## B. WHAT THIS MEANS

### B1. ⭐ Does upstream mortality distort batch 4's reachability table? — **YES, materially.**

Batch 4 classified which sizing guards can bind, computed over signals that survived to the
sizer. That population is **not representative**, and the bias is price-structured:

| stage | n | share > Rs 990 | mean trigger price |
|---|---:|---:|---:|
| admitted | 30,769 | 24.02% | Rs 732 |
| survived pre-gates | 21,156 | 26.68% | Rs 757 |
| **reached the SIZER** | 8,943 | **34.73%** | **Rs 875** |
| **reached the RISK ENGINE** | 5,845 | **0.14%** | **Rs 441** |

Two distinct distortions:

1. **The sizer sees a population enriched in expensive stocks** — 34.73% above Rs 990 versus
   24.02% at admission, a **1.45x enrichment**. Screening preferentially passes higher-priced
   names. So the concentration guard's apparent bindability is measured on a sample that
   over-represents exactly the band it binds on.
2. **Below the sizer, the price distribution collapses.** Only **0.14%** of signals reaching the
   risk engine are above Rs 990 — mean price falls from Rs 875 to Rs 441. The concentration cap
   removes essentially the entire upper price band *before the risk engine ever sees it*.

**What survives and what does not:** batch 4's verdicts remain correct **as statements about the
population that reaches the sizer** — the arithmetic was never in question, and the finding that
only 4 of 15 sizing guards can bind still holds for that population. What must be qualified is
any reading of them as statements about *signals in general*. In particular, a risk-engine guard
whose binding depends on high notional per share is unreachable **not because of its own
threshold** but because an upstream cap deletes the whole price band first. That is a different
diagnosis, and it is invisible if you only look below the sizer.

**This does not invalidate batch 4. It bounds the domain over which its conclusions are claims.**

### B2. Bearing on D2 (strategic direction) — numbers only, no recommendation

D2 asks whether the scanner/scorer stack has a viable edge. These numbers bear on the diagnosis
without answering it:

- **The decision layers ARE being reached.** 5,845 risk-engine consultations in 6 days (~975/day).
  This is not a system whose gates sit idle.
- **97.60% of deaths are deliberate decisions**; 2.40% are data failures. Signals are not
  leaking away through failure — they are being declined.
- **The largest single killer is the score gate**: 10,949 of 30,769 (35.58%), spread across
  `REJECTED_SCORE_29` … `_59`. That is the scorer doing its job, at volume.
- **But 3,098 signals (10.07%) die at a price ceiling, not at a judgement.** The scanner emits
  24.02% of its signals above Rs 990, and the sizing configuration makes ~42% of that band
  untradeable. That is a **universe/configuration mismatch**, structurally distinct from "the
  scoring has no edge" — the system never forms an opinion on those names.
- **Direction skew is extreme upstream, not just in the closed book:** 28,027 LONG vs 2,742 SHORT
  admitted signals (**91.1% long**), which matches the 91%-long closed book reported by BK-1.
  The skew originates at the scanner, not at execution.

**No recommendation is offered. D2 is Rama's.**

### B3. What cannot be measured from the record that exists

1. **⭐ The composition of the webhook drop.** 643,402 signals with no row and no log line. We
   can bound the retryable classes (`QUEUE_FULL`, `STORE_ERROR`) at near-zero via the 503 count
   (18 POSTs total, 0 in the complete era), and we can attribute the bulk to dedup by the
   cadence-vs-TTL argument (~80% expected, 81.5% observed). We **cannot** measure
   `INVALID_SYMBOL`, `INVALID_PRICE`, `EXPIRED` or `IN_PROCESS` at all. This is W9.
2. **⭐ The signal count inside 403-blocked POSTs.** ~338,000 estimated, never counted. Note this
   is **cheaply fixable in principle** — the payload size is already stored, and the parse
   happens after the auth check.
3. **All `REJECTED_*` history before 2026-07-09.** Destroyed. Only 6 complete days exist. **Any
   future analysis of rejection composition must be run against a window that has not yet been
   pruned, or against a preserved snapshot.**
   > **⚠️ CORRECTED 19-Jul-2026 (throttle/record-correction batch, §A4).** "The window will keep sliding" was **WRONG** — I inferred an ongoing rolling erosion from a single event. Steady-state `signal_retention_days` is **90** (`system_config.yaml`), the earliest row is **12-Jun**, so the daily prune predicate matches **0 rows** every day until **~2026-09-10** (verified at run-date 20-Jul: 90d→0, 30d→0, 14d→0; only ≤10-day windows delete anything). The 09-Jul boundary was carved by the **one-off manual Phase-B prune of 16-Jul**, not the daily job. The data is **not eroding day-to-day**; the real risk is **another manual short-window prune**, not the clock. Everything else in item 3 stands. A preserved snapshot now exists at `/home/ubuntu/preserved/signal_census_19jul2026/`.
4. **Per-check attribution inside the risk engine.** `approve()` records the *first* failing
   check. A signal rejected at `DAILY_TRADES` (check 5) tells us nothing about whether it would
   also have failed `DAILY_LOSS` (check 7). Q9's reachability work already relies on this and it
   is a genuine ceiling on what "which gate binds" can mean.
5. **Whether paper and live quote-mortality actually differ.** Structurally they can (§A7); no
   paper data exists in the window to test it.
6. **`REJECTED_KILL_SWITCH` stage attribution** — ambiguous between pre-gate and risk-engine
   check 1 (§A1). Latent; zero rows today.

---

## PROOF OF READ-ONLY

| | value |
|---|---|
| live DB | `/home/ubuntu/systems/trading-system/data_store/trading_system.db` |
| size before / after | 89,968,640 / **89,968,640** |
| mtime before / after | `2026-07-19 09:20:01.635524365` / **identical** |
| sha256 before | `e69fd1b476109b85efac71e09485ecbbd654692240913aa9e6a7e3dd7eabe9c7` |
| sha256 after | **`e69fd1b476109b85efac71e09485ecbbd654692240913aa9e6a7e3dd7eabe9c7`** |

Method: a snapshot was taken with `sqlite3 "file:<live>?mode=ro" ".backup /tmp/census19jul/snap.db"`
and **every query in this report ran against that snapshot**, itself opened with `mode=ro`. No
application code touched the live DB, so migration-on-open was never a risk.

`-shm` (32KB) and `-wal` (**0 bytes**) exist alongside the live file. These are created by
WAL-mode **reads** — a read-only connection still maps the shared-memory index. The WAL is empty
and the main file's hash and mtime are byte-identical, so no write occurred. The 09:20 mtime
predates this work and belongs to the daily `cron_officer --briefing` job.

---

## APPENDIX — corrections to note

- **My own script artefact:** in the raw census output the collapsed family `REJECTED_SCORE`
  reported stage `?_unattributed`, because the stage function matched only `REJECTED_SCORE_<n>`
  and not the collapsed label. Cosmetic, in the analysis script only, corrected in the tables
  above (correct stage: **2 screening**). No production code is involved.
- **A classifier collision worth recording (latent):** `signal_status.is_sizing_rejection()`
  treats any `REJECTED_SIZING_*` as a sizing rejection — which would include
  `REJECTED_SIZING_VALID`, a **risk-engine** check (check 2), not a sizer outcome. Zero such rows
  exist today. **Reported, not fixed.**

---

## WHAT WAS NOT DONE

Nothing was fixed, and nothing outside the census was touched. Specifically not done: the
live-seed extraction from `main()` (boot path, post-Monday, careful loop); the three deferred
`event_type` sites; the `Rejected (Sizing/Capital)` label; `build_taxonomy_map()`'s
`--config-dir`; the destructive CTs; any restart of the production system; the full test suite
(nothing executable changed, so there is nothing to regress).
