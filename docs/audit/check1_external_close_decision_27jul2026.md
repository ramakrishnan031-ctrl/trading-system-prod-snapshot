# Decision package — CHECK1's "RMS/MANUAL CLOSE" label

**Written:** 27-Jul-2026 Monday ~20:00 IST · every number re-measured against the live database,
not quoted from the 26-Jul report.
**Answer requested:** one word — `CLASSIFY` · `DEFER` · `BOTH` · `NOTHING`

---

> ## ✅ ANSWERED AND SUPERSEDED — read this box before the body
>
> **The answer was `CLASSIFY`, and CLASSIFY was already built, pushed and deployed *before this
> package was written*.** The v45 push at 18:17 on the same evening carried the whole family:
>
> | commit | what |
> |---|---|
> | `bc19aab` | §A — the canonical closure vocabulary (`core/closure_source.py`) |
> | `77b8b04` | §B — W8: `trades.closure_source` + `exit_mechanism`, written at the one finalizer |
> | `10c02ae` | the pure classifier (`orders/closure_classifier.py`), component only |
> | `2f8fd87` | **§C — CLASSIFY: gather, classify, then act; and emit the alert the evidence supports** |
> | `81f9372` | §D — the mid-fill deferral, shipped INERT at `check1_mid_fill_defer_sec = 0.0` |
>
> Verified on the VM 27-Jul 20:5x: `orders/closure_classifier.py` and `core/closure_source.py` are
> present in the deployed tree, `order_reconciler.py` imports the classifier, the bare repo HEAD is
> `d3fa5b8`, and the deployed `EXPECTED_SCHEMA_VERSION` is 45.
>
> ⚠️ **The live DB is still on schema 44.** The v45 migration — which *rebuilds* `trades` to add
> the two columns — runs at the **Tue 28-Jul 08:15 boot**. Until then `trades.closure_source` does
> not exist in production. (The body's "v45 added `trades.closure_source`" is true of the *code*,
> not yet of the *database*.)
>
> ⇒ **The body below is retained verbatim in substance as the record of the reasoning.** Only the
> "should we build it" framing is stale. The remaining open item is §B's backfill question, answered
> separately in [`check1_manual_mislabel_impact_27jul2026.md`](check1_manual_mislabel_impact_27jul2026.md).

---

## What was being decided

When a position disappears at the broker, CHECK1 asks the broker *"is there a position?"*, gets
*"no"*, and concludes **a human closed it**. It never asks our own `orders` table whether one of
**our** legs had just filled.

Consequence, and it is not cosmetic:

- the INFO 🎯 *"TARGET HIT"* that should have arrived is **never sent**
- a CRITICAL *"RMS/MANUAL CLOSE — closed externally"* is sent **instead**
- `trades.exit_reason` is written `MANUAL` instead of `TGT_HIT` / `SL_HIT` / EOD

⭐ **The money was right every time** (the double-close guard held). This is an **attribution and
alerting** defect, not a capital one.

## The fact that anchors the decision

> **35 of 41 `CLOSED_MANUAL` trades have one of OUR OWN exit legs `COMPLETE`.**
> EOD 22 · SL 9 · TGT 4. (Re-measured: 41 total, unchanged.)

**And the system already knew.** From the HUHTAMAKI timeline:

```
13:10:45.835  INFO      exit_price resolved from broker trades: 940.00
13:10:46.716  INFO      order_placer.exit_fill_received   <-- OUR OWN LEG FILLED
13:10:47.144  CRITICAL  CHECK1 MANUAL_CLOSE ... "closed externally"
```

⭐ Our own fill was recorded **0.43 seconds before** the alert that said nothing of ours accounted
for the close. The disproof was in hand, in the same process, under half a second earlier.
**This is a plumbing problem, not an information problem** — no new data source is needed, only
that the existing evidence be consulted before the verdict is written.

### ⭐ The false-positive rate as a property of the population

The 26-Jul report was careful to say the *"0 % true positives"* claim covered only the **4 CRITICAL
emails** examined, **not** all 41 rows. Measured properly:

- the 35 with a completed own exit leg span **17-Jun → 24-Jul**
- all 6 candidates fall within **15-Jun → 19-Jun**

**Since 19-Jun — five weeks, 35 rows — not one has had the true-positive shape.**
⚠️ That is a **strong prior, not a licence**: a real broker RMS close can still happen, and the
constraint below is absolute.

## The constraint any answer must satisfy

> ⛔ **Suppress the CRITICAL only on POSITIVE evidence that one of our own legs accounts for the
> close. NEVER on the absence of evidence.**
> Broker unreachable · `orders` unreadable · ambiguous cancel reason ⇒ **still CRITICAL**. Fail loud.

⛔ Any approach that reaches zero false positives by **going quiet** is the silent-failure pattern
and is not an option. It is listed under **rejected**, deliberately, so it cannot be re-proposed as
an alternative later.

## The six rows with no completed own exit leg — and what each is

These are the candidate **true** positives. Measured: they are **not one group**.

| symbol | date | leg states | what it actually is |
|---|---|---|---|
| **GICRE** | (none) | `ENTRY=CANCELLED` | ⛔ **not a position.** No `entry_time`, entry never filled. A shell row; an "external close" alert here is meaningless. |
| **SULA** | 15-Jun | `ENTRY=COMPLETE` only | ⚠️ **era artifact** — see below |
| **AGARIND** | 16-Jun | `ENTRY=COMPLETE` only | ⚠️ **era artifact** — see below |
| **EVEREADY** | 17-Jun | ENTRY ok, SL+TGT `CANCELLED` | ✅ genuine candidate — exit 367.00 vs entry 361.00 |
| **AEROENTER** | 19-Jun | ENTRY ok, SL+TGT `CANCELLED` | ✅ genuine candidate — exit 131.99 vs entry 132.13 |
| **RCF** | 19-Jun | ENTRY ok, SL+TGT `CANCELLED` | ✅ genuine candidate, **but** exit == entry (137.73), pnl 0.0 = the **entry-proxy fallback**. That price is *fabricated*, not observed. |

⭐ **The era artifact, measured:** the first SL/TGT leg row in the entire `orders` table is dated
**2026-06-17**. On 15-Jun and 16-Jun there are **zero** SL/TGT rows for any trade. So SULA and
AGARIND **predate exit-leg recording** — their "no completed exit leg" is a **gap in the record**,
not evidence of an external close. They cannot be classified either way and must not be counted as
true positives.

> ⇒ **The honest candidate set is 3, not 6** — EVEREADY, AEROENTER, RCF, all 17–19 Jun, all with
> the same shape (entry filled, both exit legs cancelled cleanly, position gone). Every option below
> is judged on whether **those three still produce a CRITICAL**.

---

## The options

### `CLASSIFY` — gather evidence, classify, then act ⭐ RECOMMENDED

Before choosing the alert, CHECK1 reads `orders` for that trade:

| evidence | verdict |
|---|---|
| a completed SL/TGT/EOD leg | **OURS** → INFO, and let the normal exit path send "TARGET HIT"; write the real `exit_reason`, not `MANUAL` |
| a leg refused cancel as *"being processed"* | **OURS, mid-fill** → INFO, wait |
| anything else / unreadable / unreachable | **CRITICAL, unchanged** |

- **Fixes:** all 35 false positives. Restores the deleted INFO. Stops writing `MANUAL` over real
  SL/TGT/EOD outcomes.
- **The 3:** all three still CRITICAL — no completed leg, none filling, so they fall to the else
  branch, which stays the default.
- **SULA / AGARIND:** still CRITICAL (no positive evidence — correct under the rule; the record is
  silent, and **silence must never suppress**).
- **GICRE:** still CRITICAL. ⚠️ Honest limitation: this option does not fix GICRE, because a
  cancelled entry is a different defect. It is 1 row and it is not what is being decided here.
- **Cost:** order-path code (`order_reconciler`), so it is capital-adjacent and needs its own deploy
  slot and a paper+live parity pass. Requires **one mandatory test as its licence**: plant a genuine
  external close (position gone, no own leg, nothing filling) and assert the CRITICAL still fires.
- **Risk:** the classifier reads state already in hand in the same process. It adds no broker call
  and no new failure mode.

### `DEFER` — wait for our own fill callback before classifying

Already built and shipped **inert**: `check1_mid_fill_defer_sec`, default `0.0` = OFF, and 0 is
**proven a true no-op** (`81f9372`, on main via v45).

- **Fixes:** the mid-fill **race** only — the HUHTAMAKI 0.43-second shape.
- ⚠️ **Narrow:** 35 of 41 still finalize inside CHECK1 regardless. Deferral alone leaves most of the
  defect in place.
- ⛔ **And it cannot be rehearsed:** paper's `cancel_order` **always succeeds**, so the mid-fill
  branch never fires in paper. Turning this on means a path that has never executed anywhere goes
  straight into LIVE.
- **Cost:** a config flip, but an unrehearsed one. Not recommended alone.

### `BOTH` — CLASSIFY now; DEFER later, once CLASSIFY is proven live

CLASSIFY removes the 35. DEFER then closes the residual sub-second race for the handful where our
fill lands between the broker poll and the verdict.
⭐ **Order matters:** CLASSIFY first makes DEFER's effect *observable*, because the stream is no
longer full of false CRITICALs to hide it.
**Cost:** two slots instead of one. Nothing is lost by sequencing them.

### `NOTHING` — leave it; keep reading "RMS/MANUAL CLOSE" as "probably ours"

Defensible **only** because the money is right. But the cost compounds: every `CLOSED_MANUAL` row
keeps corrupting `exit_reason`, so any exit-policy analysis keyed on it silently omits 35 real
SL/TGT/EOD outcomes — and 22 of those are EOD, the largest exit bucket there is.

### ⛔ Rejected — not offered as alternatives

- ⛔ **"Soften the alert text."** Correcting the wording while leaving `exit_reason` as `MANUAL`
  would make the stream *look* healthy while attribution stays corrupted — and would remove the very
  noise that surfaced this finding. **Worse than nothing.**
- ⛔ **"Suppress CHECK1's CRITICAL when a position vanishes and we cannot prove otherwise."** This
  reaches zero false positives by **failing quiet**. It violates the positive-evidence rule and
  would have silenced all 3 genuine candidates.

---

## Recommendation

**`CLASSIFY`.** The information needed to be right was already in the process 0.43 seconds before
the wrong alert was sent. The fix consults evidence we already hold; it invents nothing, calls
nothing, and the CRITICAL path remains the default for every case that is not positively explained.

⏰ **Slot (as written on 27-Jul):** not Tue (v45 alone), not Wed (T2 arm), not Thu (T2 close). This
is order-path code and should not ride with the boot-path pair. Earliest sensible is the week of
3-Aug, after Mon 3-Aug has been observed clean.
⇒ **Overtaken by events** — the code shipped in v45 on 27-Jul at 18:17 and goes live at the Tue
28-Jul 08:15 boot. See the box at the top.

⭐ **Follow-on, not part of this decision:** the 35 mislabelled rows are **recoverable** — `orders`
records which leg completed on each, and v45 adds `trades.closure_source` + `exit_mechanism`, the
exact vocabulary needed. That question is answered in
[`check1_manual_mislabel_impact_27jul2026.md`](check1_manual_mislabel_impact_27jul2026.md).
