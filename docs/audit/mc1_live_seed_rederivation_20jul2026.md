# Re-deriving the M-C1 live-seed safety argument under the E4/W10 contract

**Status: FOR CHATGPT REVIEW. Design + analysis only — no test edited, no merge, nothing deployed.**
**Verdict: §C3 → STILL SOUND, NEW REASON.** The cancellation is independent of the reader's contract
**structurally**, not numerically — and the original argument, as written, contained a false structural
claim that happened not to matter. Line numbers are on `80fbe86` (pre-merge).

> **The tempting move, refused.** `test_the_carryover_equals_exactly_what_phase_2_re_applies` still
> passes, so it would be easy to conclude "the mechanism works, therefore it's fine" and flip the test.
> That proves the *mechanism*, not the *independence*. They are different claims. This document makes
> the independence argument, and in doing so finds that §A2's stated version of it was wrong.

---

## A1. The original §A2, quoted in full

From `docs/audit/live_seed_mc1_wired_18jul2026.md`. The TL;DR form (lines 20–22):

> **§A2 — the cancellation is CONTRACT-INDEPENDENT, so it survives E4/W10.** Both sides sum
> `pnl_delta` from the same helper, so `net − Σ + Σ = net` holds whatever `pnl_delta` means. The
> daily-loss **reader** — the quantity E4/W10 changes — is *not involved at all*.

And the section itself (lines 65–80):

> ## 2. §A2 — CONTRACT-INDEPENDENCE (verdict: YES, it survives E4/W10)
>
> Because both sides sum the **same field from the same rows**, the cancellation is algebraic and
> independent of what `pnl_delta` means. Measured:
>
> ```
> reader  (get_daily_realized_net_pnl) = -18,189.08     = SUM(pnl_delta) - SUM(costs)
> carryover (the live-seed subtrahend) = -18,094.54     = SUM(pnl_delta)
> difference                           =     -94.54     = exactly SUM(costs)
> ```
>
> **The reader is not part of the cancellation.** E4/W10 changes the *reader's* contract; the seed
> and Phase 2 never consult it. ⇒ the fix can land without disturbing live seeding.
> `TestSharedHelper::test_the_reader_is_a_different_quantity_and_is_not_involved` pins this, and
> fails if the two quantities ever converge (which would mean the independence argument had
> quietly stopped holding).

---

## A2. What it PROVED versus what it INFERRED — the boundary

**PROVED, and still true (the load-bearing part):**

> Both sides draw the **same rows and the same field from one shared helper**
> (`_today_release_used_pnl_rows`, `capital/fund_manager.py:1757`), so
> `_total = (net − Σ) + Σ = net` is an **algebraic identity for any meaning of `pnl_delta`.**

This is genuinely contract-independent, and E4/W10 does not touch it. It is also the part the surviving
tests actually exercise (`test_the_carryover_equals_exactly_what_phase_2_re_applies`,
`test_both_sides_still_call_the_shared_helper`).

**INFERRED, and wrong — two separate errors:**

**Error 1 — a false structural claim.** *"the seed and Phase 2 never consult it"* / *"the reader is not
involved at all"* is **false as written**. `rehydrate_from_open_trades` — the function that *contains*
Phase 2 — calls the reader:

```python
capital/fund_manager.py:1735-1736
        # FIX-051: Read daily_pnl from SQL for logging
        today = now_ist().date().isoformat()
        daily_pnl = self._store.get_daily_realized_net_pnl(today)
```

The correct, narrower statement is: **the reader's value does not enter the cancellation arithmetic.**
It is read *after* Phase 2 (`:1703-1711`) and *after* the invariant check, and is used **only as a log
field** (`rehydrate_complete`'s `extra={... "daily_pnl": daily_pnl ...}`). It feeds neither `_total`,
nor any bucket, nor the returned dict. The claim was true in effect and false in statement — which is
precisely the kind of gap that survives until something forces it open.

**Error 2 — a numerical proxy standing in for a structural property.** The test pinned
`reader != carryover`. But numerical difference was a *consequence* of the old contract (the gap was
exactly `Σcosts`), never the *reason* for independence. The reason was always structural. Pinning the
consequence meant the test fired when the consequence changed, while the actual property was untouched.

**This is the general lesson worth keeping:** *a test that pins a symptom of a property, rather than the
property, fails on changes that do not threaten the property — and stays silent on changes that do.*
Both halves of that are bad. The proxy here was strictly weaker than the claim it stood for.

## A3. What else cites §A2

| Site | Inherits the void premise? |
|---|---|
| `tests/integration/test_q9_live_seed_mc1_wired.py::TestSharedHelper::test_the_reader_is_a_different_quantity_and_is_not_involved` | **Yes — this is the failing test.** Its docstring restates §A2 verbatim |
| `docs/audit/live_seed_mc1_wired_18jul2026.md` §0 TL;DR + §2 | **Yes** — both quoted above; needs a dated correction on the retry |
| `docs/audit/live_seed_mc1_wired_18jul2026.md` §1 ("what would break it") | **No** — it names the shared-helper break, which is the sound half. Plant B proved it bites |
| memory `q9-live-seed-mc1-wired-19jul` | **Check on retry** — if it repeats "the reader is not involved", it needs the same correction |
| `docs/decisions/RUNBOOK_e4_w10_deploy.md` | **No** — it never cites §A2 (which is *why* it predicted "failures stay at 14") |

Not the attribution-gloss shape in its worst form: §A2 was cited as authority in only one test and one
report, and the *sound* half is what the other references lean on.

---

## B. The mechanism, traced from code

```
main.py:2255-2257    LIVE seed = broker_adapter.get_margins().net
                                 - fund_manager.today_realized_pnl_carryover()      <-- side 1
main.py:2259         fund_manager.initialize(_startup_capital)
main.py:2286         fund_manager.rehydrate_from_open_trades()                       <-- side 2
```

| Element | Site | Quantity |
|---|---|---|
| Shared helper | `fund_manager.py:1757` | `SELECT pnl_delta, bucket FROM fm_ledger WHERE entry_type='RELEASE_USED' AND ts >= ? AND pnl_delta != 0` |
| Carryover (subtrahend) | `:1777` | `Σ pnl_delta` over exactly those rows |
| Phase 2 (re-addition) | `:1703-1711` | for each of exactly those rows: `bucket avail += pnl; _total += pnl` |
| The reader | `core/state_store.py:2432` | `SUM(pnl_delta)` for the date — **no `entry_type` filter** |

**⭐ The two quantities are not the same set, even after E4/W10.** The carryover filters
`entry_type='RELEASE_USED'`; the reader deliberately does not filter at all (its docstring: *"Only
RELEASE_USED … and RESET_PNL … ever carry a non-zero `pnl_delta` … so no entry_type filter is needed
here"*). So under the new contract:

- **during the session** (no `RESET_PNL` row yet): reader = `Σδ_RELEASE_USED` = carryover — **equal**;
- **after the EOD reset**: reader = `Σδ_RU + (−old_pnl)` = **0**, carryover = `Σδ_RU` — **different**.

The convergence the test detected is therefore *partial and transient*, not a merger of the two concepts.

### B2 — where the reader enters, exhaustively

Four production callers, all in `fund_manager.py`:

| Caller | Site | Load-bearing? | On the cancellation path? |
|---|---|---|---|
| `release_used` | `:1279` | **Yes** — drives the daily-loss breach check | No (runtime close path) |
| `get_snapshot` | `:1507` | Reporting only | No |
| `reset_daily_pnl` | `:1603` | **Yes** — writes `RESET_PNL.pnl_delta = −reader(today)` (`:1610`) | **See B3** |
| `rehydrate_from_open_trades` | `:1736` | **No — log field only** | **Yes, same function; but no causal role** |

### B3 — identical value vs shared code path vs causal dependency

The old argument conflated the first with the third. Separating them:

| Relation | Present? | Evidence |
|---|---|---|
| **Identical value** | Yes, during the session, post-E4/W10 | Both equal `Σδ_RELEASE_USED` |
| **Shared code path** | **Yes** — and §A2 denied this | The reader is called at `:1736`, inside the Phase-2 function |
| **Causal dependency** | **No** | `:1736`'s value flows only into a log `extra`; it is read after Phase 2 *and* after `_check_invariant`, and touches neither `_total`, nor a bucket, nor the return value |

**And the one genuine reader→ledger causal edge is severed by the filter.** `reset_daily_pnl` is the only
writer whose row value is derived from the reader — and it writes `entry_type='RESET_PNL'`, which
`_today_release_used_pnl_rows` **excludes**. So the reader's value cannot reach either side of the
cancellation, by construction, under any contract. *This is a stronger and more durable fact than
anything in the original §A2, and nobody had stated it.*

---

## C. The re-derivation

### C1. The new situation, stated precisely
After E4/W10 the reader computes `SUM(pnl_delta)`, which during the session is numerically the
carryover's quantity. **This is an identity of value, not a coupling.** Two quantities being equal
creates a defect only if some code path adds them together, or if one is derived from the other in a way
that feeds back. Neither holds here (B2, B3).

### C2. Failure modes, worked through

**(a) Could the same value be counted twice — once by the reader, once by the carryover?**
**No.** The carryover's value is consumed by exactly two sites: the seed subtraction (`main.py:2257`) and
the Phase-2 re-addition (`:1703-1711`). The reader's value is consumed by the loss check, the snapshot,
the reset writer, and a log line. **No consumer takes both.** `_total` is written only by the seed and
Phase 2; the reader never writes capital state.

**(b) Could the cancellation consume a value the reader has already adjusted, or vice versa?**
**No.** The reader is a pure `SELECT` and adjusts nothing. The single reader-derived *write* is the
`RESET_PNL` row, and the shared helper's `entry_type='RELEASE_USED'` filter makes that row invisible to
both sides of the cancellation. Conversely, the cancellation writes no ledger rows at all.

**(c) Ordering — enumerated, not assumed**

| # | Scenario | carryover | reader (new) | Cancellation |
|---|---|---|---|---|
| 1 | Cold boot, flat book (e.g. every 08:15) | 0 | 0 | Trivially exact; the subtraction is a no-op |
| 2 | Mid-day warm restart, before EOD reset | `Σδ_RU` | `Σδ_RU` (**equal**) | `(net−Σ)+Σ = net` — exact. Reader read only for the log |
| 3 | Restart *after* the EOD reset, same date | `Σδ_RU` | **0** (**differ**) | Still exact — the `RESET_PNL` row is filtered out of both sides |
| 4 | Reset fires *between* seed and rehydrate | `Σδ_RU` | changes underneath | **Still exact** — both sides re-read the same filtered rows, which the reset does not alter |
| 5 | Restart after the daily-loss limit already fired | `Σδ_RU` | smaller loss by `Σcosts` | Cancellation unaffected. The kill state lives in `kill_switch_state`, not re-derived from the reader — a fired kill is **not** un-fired |

Scenario 5 deserves its own sentence: E4/W10 *does* change when the limit fires (later, on true net) —
that is the **intended** risk-posture change Rama approved, not a cancellation issue. The already-fired
kill persists independently (and auto-clears next day by the prior-day rule).

**(d) Does the identity itself create a problem?** **No.** It is a transient numerical coincidence
between quantities consumed by disjoint code with no shared write and no feedback edge.

### ⚠️ (e) One real hazard found — pre-existing, contract-INDEPENDENT, NOT an E4/W10 blocker
The two sides derive their day-window **independently**: `main.py:2257` calls
`today_realized_pnl_carryover()` with **no argument** (so it computes its own `now_ist()` floor,
`:1777`), and `rehydrate_from_open_trades(start_of_today_iso=None)` computes its own. **If the seed and
the rehydrate straddle midnight, the two windows differ and the cancellation breaks** — the seed
subtracts yesterday's Σ while Phase 2 replays today's (0).

This is the exact sibling of the standing *"a regression run must not cross midnight"* rule, in
production code. It exists identically before and after E4/W10, so it **does not block this deploy**.
Reachability is low (boot is 08:15; the service self-exits 16:00), but it is not zero for a manual
restart. **Queued, not fixed here.** The clean fix is to compute the floor once and pass it to both.

### C3. VERDICT — **STILL SOUND, NEW REASON**

> The live-seed cancellation is independent of the daily-loss reader's contract **structurally, not
> numerically**, for three reasons:
>
> 1. **Algebraic identity from a shared row-set.** Both sides draw the same rows and the same field from
>    the single helper `_today_release_used_pnl_rows` (`fund_manager.py:1757`), so
>    `(net − Σ) + Σ = net` holds for *any* meaning of `pnl_delta`.
> 2. **The only reader-derived ledger row is filtered out of both sides.** `reset_daily_pnl`
>    (`:1603/:1610`) writes `RESET_PNL.pnl_delta = −reader(today)` — the sole causal edge from the
>    reader into the ledger — and the helper's `entry_type='RELEASE_USED'` filter excludes it. The
>    reader's value therefore cannot reach the cancellation under any contract.
> 3. **The one reader call on the path is observational.** `rehydrate_from_open_trades:1736` reads it
>    *after* Phase 2 and *after* the invariant check, and uses it solely as a log field.
>
> Whether the reader and the carryover are numerically equal is **irrelevant**. After E4/W10 they are
> equal during the session and differ after the EOD reset; neither fact touches the cancellation.

### C4. Falsification condition — what would make this stop holding

The original had none, which is part of why it survived until a merge broke it. This one has four,
each mechanically checkable:

1. **The two sides cease to share `_today_release_used_pnl_rows`** (§A1's original break; Plant B proved
   it bites with a silent −542.84 drift and zero runtime-guard signal).
2. **The helper's `entry_type='RELEASE_USED'` filter is dropped or widened to admit `RESET_PNL`.** This
   is the new one: it would let the reader-derived value into the cancellation and genuinely break
   independence. *Reason 2 above rests entirely on this filter.*
3. **`rehydrate_from_open_trades:1736`'s `daily_pnl` ceases to be log-only** — if it ever feeds `_total`,
   a bucket, or the return dict.
4. **Either side computes its own day-floor such that they can straddle midnight** (§C2(e); already true
   today, contract-independent).

---

## D. What the three tests should then assert — described, NOT written

**C3 — `test_the_reader_is_a_different_quantity_and_is_not_involved`.** Do **not** merely invert
`reader != carry` to `reader == carry`; that would swap one numerical proxy for another and pin nothing.
Retire the numerical assertion and pin the three structural facts instead — ideally renamed to
`test_the_reader_cannot_reach_the_cancellation`:
- **(i)** the shared-helper property — already covered by `test_both_sides_still_call_the_shared_helper`;
- **(ii)** *the `RESET_PNL` row is invisible to the cancellation* — drive a close, call
  `reset_daily_pnl()`, then assert the carryover is **unchanged** and `_total` still lands on
  `broker.net`. This pins falsifier 2, which nothing currently covers;
- **(iii)** *the rehydrate reader call is observational* — monkeypatch `get_daily_realized_net_pnl` to
  return an absurd value (e.g. `-1e9`) across the seed+rehydrate sequence and assert `_total` **still**
  equals `broker.net`. **This is the anti-vacuity test that would have caught the false premise in the
  first place**, and it is contract-agnostic: it passes before and after E4/W10.

**C1 — `test_reader_equals_independent_ground_truth`.** Remove the `strict=True` `xfail` marker; it
becomes a plain green assertion. Keep the `costs > 0` anti-vacuity guard.

**C2 — `test_the_discrepancy_is_exactly_the_double_counted_costs`.** Invert to the new contract:
`reader == truth` exactly, retaining `costs > 0` so it cannot pass trivially. Consider renaming to
`test_the_reader_equals_ground_truth_with_costs_present`.

### Side finding for the retry — the W10 workarounds become stale
Three modules deliberately avoid the reader *because of this bug*, each citing W10:
`ops_dashboard/backend/api/risk_capital.py:38` ("D2 (approved permanent) … NOT
get_daily_realized_net_pnl (W10 double-subtracts costs)"), `ops_dashboard/backend/services/capacity.py:16`,
and `reports/daily_trade_review.py:1053`. They read `RELEASE_USED.pnl_delta` directly — which is what
the **new** reader returns during the session, so post-E4/W10 they **agree** rather than conflict. Not a
defect, and not a blocker. But their stated rationale becomes false the day this ships, leaving comments
that assert a bug which no longer exists — the attribution-gloss shape. **Re-label them in the retry.**

---

## E. The retry sequence — mechanical once the reasoning clears

1. **This document reviewed by ChatGPT.** ← the gate; nothing below starts until it clears.
2. Rewrite **C3** to pin the new reason (D(i)–(iii) above); correct §A2 in
   `live_seed_mc1_wired_18jul2026.md` with a dated note, leaving the superseded claim legible.
3. Flip **C1** (remove the strict `xfail`).
4. Flip **C2** (invert to `reader == truth`).
5. Recreate the merge — `git merge --no-ff e4-w10-pnl-contract`, zero conflicts.
6. **Fresh same-window BASE run + MERGE run; `comm -23` merge-only must be EMPTY.** No fixed-number
   baseline (see below).
7. Deploy per `RUNBOOK_e4_w10_deploy.md`; confirm `AUTO-INSTALLED`; tag.
8. Flip the **pre-armed checklist A3** to `expected_reset_IF_E4_SHIPPED`.
9. Verify on the next EOD: `RESET_PNL.pnl_delta == −Σδ` (the 20-Jul-equivalent of **18.29**, not 19.61).
10. Re-label the three W10 workarounds.

### ⭐ Standing regression method — replaces the fixed-number baseline
**There is no "14-failure baseline".** That number was a property of one environment at one moment, not
of the code: the known-failure set is time-of-day and calendar dependent, and on this PC `bash` resolves
to the **WSL stub**, so ~20 bash-subprocess tests run and fail instead of skipping. **Method: take a
fresh BASE run in the same session/window as the candidate run and `comm -23` the two failure sets.
Neither absolute number is meaningful; the differential is exactly meaningful.**

### Runbook corrections (D4) — applied to `RUNBOOK_e4_w10_deploy.md`
1. `broker/cost_calculator.py` is **not new** — it pre-exists at 299 lines and the branch takes it to
   353. The `+54` is exact; the **function** `round_trip_costs_or_zero` is what is new.
2. `tests/unit/test_e4_w10_pnl_contract.py` has **20** tests, not 19 (confirmed by the collected delta
   5001 → 5021).
3. "**81 commits behind**" is now **94** — ordinary drift, not an error.

---

*No test edited. No merge. Nothing deployed. `PC == origin == VM bare`; branch `e4-w10-pnl-contract`
untouched at `ad34ee4`; the pre-deploy DB backup is retained and still valid for the retry.*
