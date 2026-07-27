# Did the `exit_reason = MANUAL` mislabel corrupt the exits study? — and should the 35 rows be backfilled?

**Date:** 27-Jul-2026 Monday evening · **READ-ONLY.** Nothing was written, migrated or executed.
**Method:** a consistent `.backup` snapshot of production taken 20:42 IST (service `inactive`),
read through `mode=ro` + `PRAGMA query_only`. `trades` = 430, matching the VM.

**Question (from the 27-Jul instruction, §B):** 35 historical trades carry `exit_reason = MANUAL`
when they were actually `TGT_HIT`, `SL_HIT` or EOD — roughly 8 % of the book, mislabelled, in a
column that **analysis reads**. Did that corrupt the 13-Jul exit-policy work or the 24-Jul
trailing-stop work? And should the rows be corrected?

---

## Answer in one line

**No conclusion moves — but not for the reason the question assumed, and the exposure is larger
than 8 %.** The 13-Jul study is immune because `exit_reason` **is not an input to it at all**; the
24-Jul study *does* read it, and **100 % of its `MANUAL` bucket was mislabelled** — yet every
conclusion it drew survives reassignment, most of them strengthened.

---

## 0. First, the premise — re-measured

| claim | measured tonight | verdict |
|---|---|---|
| 41 `CLOSED_MANUAL` rows | **41** (40 `MANUAL` + 1 `MANUAL_CLOSE_EOD`) | ✅ |
| 35 have an own `COMPLETE` leg | **35** — EOD **22** · SL **9** · TGT **4** | ✅ matches the design doc; ⚠️ **corrects the memory line "SL 8 · TGT 4 · EOD 23"** |
| first SL/TGT/EOD leg row in `orders` | **2026-06-17** (397 such rows) | ✅ SULA (15-Jun) and AGARIND (16-Jun) genuinely predate exit-leg recording |
| the 6 with no own leg | SULA, GICRE, AGARIND, EVEREADY, AEROENTER, RCF — with the exact prices the decision package quoted | ✅ |
| "≈ 8 % of the book" | 35 / 430 = **8.1 %** of *all* trades | ✅ of the book — ⚠️ **but see §2: it is 18.7 % of the population the study actually used** |

⚠️ **And the schema fact the instruction did not have.** Production is on **schema 44**.
`trades.closure_source` and `trades.exit_mechanism` **do not exist in the database yet** — the v45
migration that adds them (a *rebuild* of `trades`) runs at the **Tue 28-Jul 08:15 boot**. So the
column is not "NULL on all 430 rows"; it is absent. **No backfill can run before Tuesday morning.**

## 1. The 13-Jul exit-policy backtest — structurally immune

`docs/audit/exit_policy_backtest_13jul2026.md` walks **115 trades on true 1-min paths** and
**re-simulates** each exit policy. Its population is selected by *"entry filled + candle coverage"*,
and its P0 self-check reproduces the real gross (−0.007 R).

**`exit_reason` never appears in it.** The simulator replays the price path and derives its own
exit from the *rules* (entry → −1 R SL → +1.5 R TGT → 15:17 squareoff); how a trade actually ended
is not an input to any of the eight policy rows (−0.010 R … −0.144 R). Its own P0 self-check —
"CURRENT (static)" reproducing the real gross to −0.007 R — is the evidence that the simulation
recovers reality *without* consulting the labels.

⚠️ **Strength of this claim, stated honestly:** the harness itself
(`scratchpad/q28_exit_backtest.py` → `/tmp/q3/q28.py`) is **gone**, so this is read from the
report's description of its own method rather than confirmed line by line. The description is
unambiguous and the self-check corroborates it; if the script is ever recovered, this is the one
paragraph worth re-checking.

> ⇒ **Not affected — and not because the effect is small. Because the column is not read.**
> This is a stronger result than the instruction's expectation ("the effects measured were far
> larger than 8 %"), and it is worth stating precisely, because "the effect was too small to matter"
> would have to be re-argued every time the book grows. "It is not an input" does not.

## 2. The 24-Jul trailing-stop study — it *does* read `exit_reason`

`docs/audit/trailing_stop_never_fired_2026-07-24.md` §D groups by exit reason. Reproduced tonight
(the book has grown by 4 trades since 24-Jul, which accounts for the drift):

| group | 24-Jul report | tonight |
|---|---:|---:|
| SL_HIT | n = 64 | n = 66 |
| TGT_HIT | n = 45 | n = 47 |
| **MANUAL** | **n = 26** | **n = 26** (unchanged — no new `CLOSED_MANUAL` since) |
| ALL | 135 | 139 |

### ⭐ The sharp finding: the `MANUAL` row was not *mostly* wrong. It was *entirely* wrong.

Of those **26** covered `MANUAL` trades, **every single one** has an own `COMPLETE` leg:

| the MANUAL bucket really was | n | armed (MFE ≥ 0.5 %) |
|---|---:|---:|
| EOD square-off | 14 | 11 (78.6 %) |
| SL | 8 | 5 (62.5 %) |
| TGT | 4 | 4 (100 %) |
| **a genuine external close** | **0** | — |

The six candidate true positives all fall **outside** the study's window (candles begin 19-Jun;
four of the six predate it and the other two have no excursion row). So the study's `MANUAL` row
contained **zero** manual or external closes.

⚠️ **And the exposure is 26 / 139 = 18.7 % of the studied population, not 8.1 %.** The 8 % figure
is of the whole book, which includes 235 never-filled rows the study excluded. Stating the smaller
number would have understated the risk by more than half.

### Does any conclusion move? — recomputed, group by group

**§D.1 — "Explanation 3 (arm threshold never reached / decorative) is refuted."**

| group | before | after reassignment |
|---|---|---|
| SL_HIT | n=66, armed 27 (40.9 %) | n=66, armed 27 (40.9 %) |
| TGT_HIT | n=47, armed 45 (95.7 %) | n=47, armed 45 (95.7 %) |
| *(new)* EOD | — | n=14, armed 11 (78.6 %) |
| *(into)* SL_HIT | — | n=8, armed 5 (62.5 %) |
| *(into)* TGT_HIT | — | n=4, armed 4 (100 %) |

The **ALL** row is invariant: reassignment renames buckets, it moves no trade in or out. Every
group's arm rate is ≥ 50 %. ⇒ **conclusion unchanged, and it is now supported by a fourth group
(EOD) it never had.**

**§D.4 — "Explanation 4 (trades never went far enough) is refuted as a blanket claim."**

| | n | armed | rate | never green |
|---|---:|---:|---:|---:|
| as studied (SL_HIT) | 66 | 27 | 40.9 % | 9 |
| **+ the 8 mislabelled SL legs** | **74** | **32** | **43.2 %** | 10 |

⇒ **strengthened.** The counterfactual population (losers that reached the arm before reversing)
grows 27 → 32, so the *"median +1.84 R/trade, sum +46.7 R"* upper bound gets **larger**, not
smaller. The conclusion moves in the direction it already pointed.

**§D.3 — the winners' uncapped MFE. This is the number the exits thread was closed on, so it was
re-derived from `candles`, not argued about.**

Reconstruction validated against the report first — entry → 15:20 on the entry date, max favourable
in R:

| | report (24-Jul, n=45) | my reproduction (n=52) |
|---|---|---|
| uncapped median | +2.96 R | **+2.93 R** |
| mean | +3.66 R | +3.55 R |
| p75 | +4.47 R | **+4.47 R** |

Faithful. Now with the 4 mislabelled TGT exits added:

| | as studied | after relabel | Δ |
|---|---|---|---|
| whole-book winners | +2.93 R (n=52) | **+2.78 R (n=56)** | −0.15 R |
| boundary 13-Jul — in-sample | +3.06 R (n=36) | +3.01 R (n=38) | −0.05 R |
| boundary 13-Jul — **out-of-sample** | +2.31 R (n=16) | **+2.20 R (n=18)** | −0.11 R |
| boundary 14-Jul — **out-of-sample** | +2.41 R (n=15) | **+2.22 R (n=17)** | −0.19 R |

The four added winners are **all modest** — KEC +2.51 R, SUVEN +2.13 R, AARTIIND +2.11 R,
WAAREERTL +1.85 R (median +2.12 R) — so they pull the median **down** slightly.

The report's conclusion was: *"out-of-sample winners median ~2.2–2.4 R (n=14–17) — below in-sample
but clearly above the 1.5 R exit."* After relabelling: **2.20–2.22 R (n=17–18)**.
⇒ **still inside the band the report itself quoted, at its lower edge, and still clearly above the
1.5 R exit. The conclusion does not move — it tightens to the pessimistic end of its own range.**

**§A — "the trail is UNREACHABLE by construction, 423/423 LIMIT_TRIPLE."** Reads `order_protocol`.
Untouched.

### The whole book

| | n | mean R | sum R |
|---|---:|---:|---:|
| all entered trades | 183 | −0.1137 | −20.81 |

⭐ **Relabelling moves no trade in or out of this set.** Per-bucket R barely shifts either — over the
**full closed-trade population** (all rows with `risk_amount > 0`, *not* the 139 excursion-covered
subset used above): SL_HIT mean −1.149 (n=86) → −1.142 (n=95); TGT_HIT +1.361 (n=59) → +1.368
(n=63). The 22 EOD-in-MANUAL rows carry mean **+0.031 R** — near-scratch, which is exactly what a
15:17 square-off bucket should look like, and a further sign they are EOD rows rather than
anything dramatic.

> ## ⇒ §B1 answered: **no conclusion of either study moves.**
> The 13-Jul work never read the column. The 24-Jul work read it and was 100 % wrong in that one
> bucket, and **every** conclusion survives — three of them strengthened, one (D.3's OOS median)
> nudged to the bottom of the range it already published.

---

## 3. §B2 — the backfill recommendation

⭐ **There are two different acts here and they must not be conflated.**

| | act | risk |
|---|---|---|
| **(a)** | **populate `closure_source`** for the 35 rows from the `orders` evidence | **low** — fills a *new, empty* column; destroys nothing; reversible by setting it back to NULL |
| **(b)** | **rewrite `exit_reason`** `MANUAL` → `SL_HIT` / `TGT_HIT` / `EOD` | **higher** — overwrites a value reports and studies have already read |

### ⭐ Recommendation: do **(a)**. Do **not** do (b).

**Why (a):** the evidence exists in `orders` right now, W8's vocabulary was designed for exactly
this, and every future analysis then inherits the right answer through the column *built* to carry
it. The standing memory rule *"never count `exit_reason='MANUAL'` as an exit policy"* stops being a
thing a human has to remember and becomes something code can check.

**Why not (b) — three reasons, in order of weight:**

1. **It would create a new inconsistency.** `status` would still read `CLOSED_MANUAL` while
   `exit_reason` read `TGT_HIT`. Readers keyed on `status` (the 24-Jul study's own scope line is
   *"141 CLOSED + 40 CLOSED_MANUAL"*) would then disagree with readers keyed on `exit_reason`.
   Fixing one column and not the other trades a known error for an unknown one.
2. **It destroys the audit trail.** `exit_reason = MANUAL` on those rows is a true record of *what
   the system believed at the time*, and that belief is the subject of this whole investigation.
   Once (a) is done, the pair `(exit_reason=MANUAL, closure_source=OWN_TGT)` is strictly more
   informative than `exit_reason=TGT_HIT` alone.
3. **It is unnecessary.** (a) already delivers the benefit (b) was for.

### If (a) is approved — the shape of the change (§B3)

- ⛔ **Its own commit.** Never in the same commit as code.
- ⏰ **Not before the Tue 28-Jul 08:15 boot** — the column does not exist until v45 migrates, and
  that migration *rebuilds* `trades`. A backfill run before it would be destroyed by it.
- **Reversible:** `UPDATE trades SET closure_source = NULL WHERE trade_id IN (...)`, with the 35
  ids recorded in the commit message.
- **Before/after counts recorded**, and the write done through the existing COALESCE-guarded
  `state_store.set_trade_closure_axes` so a value written later by the live path is never clobbered.
- **Expected result:** 35 rows written — `OWN_EOD` 22 · `OWN_SL` 9 · `OWN_TGT` 4; the other 6 left
  **NULL**, not `EXTERNAL_UNATTRIBUTED`. ⭐ *Silence must never suppress, and it must not manufacture
  a verdict either* — we cannot prove those six were external closes, and NULL is the honest value
  for "we do not know" (`docs/closure_source_contract.md`).

### If (a) is declined — the register line (§B4)

> `trades.exit_reason` is **unreliable for `CLOSED_MANUAL` rows**: 35 of 41 say `MANUAL` when the
> real cause was EOD (22), SL (9) or TGT (4). Rows dated **15-Jun-2026 → 24-Jul-2026**. Trades
> closed from the **28-Jul-2026 08:15 boot** onward carry the correct `closure_source`; earlier rows
> do not. **Never group or filter by `exit_reason` across that boundary.**

⛔ **Either way this is Rama's call.** It is a data change to rows that reports have already read,
and it is stated here rather than done quietly.

---

## Method notes / limits

- Read-only throughout: `mode=ro` URI + `PRAGMA query_only = ON`; the VM's `/tmp` snapshots were
  deleted after transfer. The live DB was never opened for write and no `scripts/*.py --db` was used.
- The `.backup` was taken while `trading-system.service` was `inactive` (17:35 self-exit), and
  returned a `trading_system.db` byte-identical to the 19:48 operator backup — so the snapshot and
  that backup are the same state.
- ⚠️ **One reconstruction caveat, stated rather than buried:** `trades.entry_time` is ISO-with-offset
  (`...T10:00:28+05:30`) while `candles.ts` is `YYYY-MM-DD HH:MM:SS`. A raw string comparison silently
  matches **nothing** (`'T'` > `' '`), which is a *quiet* zero, not an error. The separator is
  normalised before comparing; the first run of this analysis returned n=0 and was caught only
  because n=0 was implausible.
- 7 of the 59 TGT_HIT winners have no candle coverage (they predate 19-Jun) and are excluded from
  the uncapped reconstruction — the same coverage caveat the 24-Jul report states.
