# CHECK1's "external close" label — investigation

**Date:** 2026-07-26 (Sunday, market closed, trading service down)
**Deployed HEAD:** `e808595`
**Status:** ⛔ **INVESTIGATION ONLY — nothing built, nothing designed.** Order path ⇒ CAREFUL-LOOP.

---

## The three headline answers

1. **What CHECK1 reads:** one thing only — `bp = broker_pos.get(symbol)` from `get_positions()`.
   `if bp is None:` ⇒ MANUAL_CLOSE (`order_reconciler.py:823-825`). It never consults our own
   order legs before deciding.
2. **Missing check vs race vs ordering:** ⭐ **not a missing check — it is BOTH an ordering defect
   and a real race, over a signal that IS computed and then discarded.** CHECK1 already classifies
   the broker's cancel refusal (`_cancel_reason_being_processed`, `:1449`) and already resolves the
   true fill price from the broker — then throws both away because the helper returns only an `int`.
3. **Does anything act on the label:** ⭐ **YES.** It is *not* cosmetic. CHECK1 wins a race against
   our own exit path, which then aborts at `order_placer.py:2374` — **suppressing the INFO
   "TARGET HIT" alert and replacing it with a false CRITICAL "closed externally"**, and writing
   `exit_reason='MANUAL'` instead of `TGT_HIT`. The **money is unaffected** (double-close guard).

---

## A1 — the exact decision, quoted

`orders/order_reconciler.py:817-825`:

```python
for trade in local_trades:
    if trade["trade_id"] in delivery_trade_ids:
        continue                      # SLICE2.5-P2: delivery trade -> CncGttMonitor owns it
    symbol = trade["symbol"]
    bp = broker_pos.get(symbol)

    if bp is None:
        # CHECK 1: MANUAL_CLOSE (RC5a)
        actions.append(self._check1_manual_close(trade))
```

**That is the whole decision.** The premise is *"local trade is OPEN/PARTIAL and the broker reports no
position for this symbol."* The conclusion asserted is *"Position closed externally"*
(`:1195`, verbatim in the alert body).

The gap between premise and conclusion is the defect. **"The position is gone" and "somebody else
closed it" are different claims** — and the system's own exit legs are the single most likely
explanation for the first, because a filled TGT *is* a position going to zero.

Nothing in the branch, and nothing in `_check1_manual_close` before the label is written, looks at
`orders` to ask whether one of our own legs accounts for the disappearance.

---

## A2 — what it would have had to check (and could have)

### It already checks. The result is discarded.

`_cancel_orphaned_orders_for_trade` (`:1385-1460`) classifies the broker's response to a cancel into
four documented cases. The third is exactly our case:

```python
elif _cancel_reason_being_processed(result.reason):
    # Mid-fill: may COMPLETE. Do NOT mark — let order_monitor
    # observe the real terminal state on its next poll.
    log.warning("check1: orphan %s order %s is being processed at broker "
                "(may fill); leaving local status for order_monitor: %s", ...)
```

⭐ **"Being processed" is positive evidence that OUR order is filling.** The broker refused the
cancel *because our own exit was mid-fill*. The code names this correctly, comments it correctly,
logs it — and then the function returns `cancelled` (an `int`). The mid-fill branch does not
increment it, does not set a flag, and returns nothing distinguishable. **A caller cannot tell
"there were no orphan legs" from "an orphan leg is filling right now."**

### The measured 23-Jul WAAREERTL sequence (from the JSONL, not from memory)

```
13:10:45.747  INFO      order_reconciler   Orphan order …388 (SL) cancelled at broker AND marked CANCELLED locally
13:10:45.796  WARNING   order_reconciler   check1: orphan TGT order …391 is being processed at broker (may fill)
13:10:45.835  INFO      order_reconciler   check1: exit_price resolved from broker trades: 940.00
13:10:46.695  WARNING   order_placer       order_placer.terminal_status_exit_leg_skipped
13:10:46.716  INFO      order_placer       order_placer.exit_fill_received
13:10:46.731  WARNING   order_manager      close_trade.cas_no_op
13:10:46.732  WARNING   order_placer       order_placer.exit_fill_already_closed
13:10:47.144  CRITICAL  order_reconciler   CHECK1 MANUAL_CLOSE … broker=no_position exit_price=940.00 exit_source=broker_trades
```

Two facts this makes measurable that were previously only asserted:

- ⭐ **CHECK1 fetched the real fill price and reported it in the same alert that called the close
  external.** `exit_price=940.00 exit_source=broker_trades` — that 940.00 *is our TGT fill*. The
  alert carries its own disproof in its own body.
- ⭐ **At 13:10:46.7 our own exit path arrived and found the trade already terminal.**
  `exit_fill_already_closed` + `close_trade.cas_no_op` + `terminal_status_exit_leg_skipped`.

### Classification: ordering **and** race, not a missing check

| aspect | verdict | evidence |
|---|---|---|
| missing check | **NO** | `_cancel_reason_being_processed` exists and fired at `:45.796` |
| ordering | **YES** | the label is written at `:1054` (`mark_trade_manually_closed`) — **30 lines and ~0.1 s BEFORE** the evidence is gathered at `:1084`. Even a perfect signal would arrive after the verdict. |
| race | **YES** | CHECK1's position-poll beats our own fill notification by **~0.9 s**. Both observe the same event; CHECK1's channel is faster. |
| discarded signal | **YES** | the mid-fill branch returns nothing; `-> int` cannot carry it |

**Why the race is structural, not incidental:** a filled TGT and an external close are *identical* at
the position level — both make the position vanish from `get_positions()`. CHECK1 polls positions;
the exit path waits for a fill callback. The poll will routinely win. Any fix that only reorders the
two statements inside `_check1_manual_close` narrows the window but does not close it — **the
disambiguating evidence has to be consulted, not merely arrive earlier.**

---

## A3 — the label is acted upon (the priority question)

### Money: correct, and correct *by design*

`mark_trade_manually_closed` (`:1054`) returns `False` if the trade is already terminal, and CHECK1
then skips capital release entirely (`:1064-1081`). Symmetrically, `close_trade` raises on a
double-close and `order_placer` returns. **Exactly one of the two paths releases capital.** That is
why all four alerts reconciled to the rupee. Measured: 37 of 41 `CLOSED_MANUAL` rows carry a real
broker exit price; only **1** fell back to the entry proxy.

### What is NOT correct

When CHECK1 wins, `order_placer._handle_exit_fill` returns at `:2374` and **everything below is
skipped**:

| skipped at | what it does | actually lost? |
|---|---|---|
| `:2395` | **INFO Telegram "🎯 TARGET HIT" / "STOP LOSS HIT"** | ⭐ **YES — and replaced by a false CRITICAL** |
| `:2422` | `_cancel_oco_siblings` | No — CHECK1 cancelled the SL itself at `45.747` |
| `:2437` | `release_used` | No — CHECK1 released instead |
| `:2359` | `close_trade(..., cost_breakdown=…)` | Partly — CHECK1's `record_manual_close_financials` takes no `cost_breakdown`, so the per-component cost detail is not persisted |

⭐ **The severity is inverted.** The correct notification was an **INFO "TARGET HIT +16.18"**.
What Rama actually received was a **CRITICAL "RMS/MANUAL CLOSE — Position closed externally"**.
The system did not merely add noise; it *deleted the good signal and substituted a false alarm*.

### Attribution damage

- `trades.status` = `CLOSED_MANUAL`, not `CLOSED`.
- `trades.exit_reason` = `MANUAL`, not `TGT_HIT`/`SL_HIT`.

Measured on the live DB (read-only, `mode=ro&immutable=1`):

| population | count |
|---|---|
| `CLOSED_MANUAL` total | **41** |
| …with one of OUR OWN exit legs `COMPLETE` | **35** |
| …with no own completed leg | **6** |
| own leg breakdown | EOD **22** · SL **9** · TGT **4** |
| `exit_reason` | `MANUAL` **40** · `MANUAL_CLOSE_EOD` **1** |

⇒ Any exit-policy analysis keyed on `exit_reason` is missing 35 real SL/TGT/EOD outcomes that were
relabelled `MANUAL`. This is the already-recorded hazard *"never count `exit_reason='MANUAL'` as an
exit policy"* — this report is its mechanism.

**Not damaged:** the reporting layer. `reports/daily_trade_review.py:34-46` already documents the
4-way collision and deliberately refuses to over-claim, labelling the bucket the honest
`SYSTEM_CLOSE` rather than "external". The report is already more careful than the alert.

---

## A4 — W8, and whether this is one fix or two

**W8 = a per-trade `trades.closure_source` column written at close time.** Scoped in
`docs/audit/audit_05jul2026.md` (§ tracked as **P3-r10**): *"EOD/CO-SL/RMS/human collapse to
SYSTEM_CLOSE (10 intraday cases live)"*, remedy *"add closure_source column"*, sized there as
*"a one-liner per close path … retires the classifier workaround."*

**Same root cause, two different layers — and they are separable:**

| | W8 | CHECK1's mislabel |
|---|---|---|
| question | *who* closed it? (EOD / operator / RMS / kill-flatten) | is the "externally" claim even true? |
| needs | a new column + a writer at every close path | **nothing new** — the evidence is already in `orders` and in the cancel refusal |
| blocked by | schema change | not blocked |

⭐ **They are NOT one fix, but they must share one vocabulary.** If CHECK1 stops asserting
"external", it needs something true to say instead — and that vocabulary is exactly what W8 defines.
Building CHECK1's fix with an ad-hoc private label would create a *second* classifier for the same
question, which is the thing W8 exists to retire.

**Recommended sequencing (not a design — a constraint on whoever designs it):**
1. W8's vocabulary first (even just the agreed enum), because it is the shared contract.
2. CHECK1 then *populates* it from evidence it already has, rather than inventing a parallel label.

⚠️ The instruction's warning holds: **a partial fix here is worse than none.** Specifically —
correcting the *alert text* while leaving `exit_reason='MANUAL'` would make the stream look healthy
while the attribution stays corrupted, and would remove the very noise that led to this finding.

---

## A5 — ⚠️ how a TRUE external close stays visible

**A real broker RMS close can happen and must still alert.** Any fix reaching 0 false positives by
never firing is the silent-failure pattern.

The disambiguating predicate already exists in data. At CHECK1 time, for the trade whose position
vanished, exactly one of these holds:

| evidence | meaning | correct outcome |
|---|---|---|
| an SL/TGT/EOD leg is `COMPLETE` | **ours** | INFO — and let the normal exit path own the alert |
| a leg refused cancel as *being processed* | **ours, mid-fill** | INFO — wait for `order_monitor`, do not pre-empt |
| a leg cancelled cleanly / already gone, none COMPLETE, none filling | **nothing of ours accounts for it** | ⭐ **CRITICAL — genuine external close** |

⭐ **The true-positive path is the `else` branch, and it must remain the default.** The rule to hold
whoever designs this to: *the CRITICAL is suppressed only on positive evidence that one of our own
legs accounts for the close — never on the absence of evidence.* Broker unreachable, orders table
unreadable, ambiguous cancel reason ⇒ **still CRITICAL**. Fail loud, exactly as
`_resolve_exit_price` already fails open loudly.

**The candidate true positives exist and are measurable:** the **6** rows with no completed own leg.
Three (GICRE, SULA, AGARIND) have no exit price at all; two (EVEREADY, AEROENTER) carry real broker
exit prices; one (RCF) hit the entry proxy. ⚠️ **The "0% true-positive rate" claim is about the four
CRITICAL emails examined — it is NOT a property of the 41-row population.** Do not let the fix be
justified by a 0% that was never measured population-wide.

**Pin required:** whatever is built, a test must plant a genuine external close (position gone, no
own leg, no mid-fill) and assert the CRITICAL still fires. That test is the fix's licence.

---

## Parity (Rule #5)

`OrderReconciler` is mode-agnostic — `self._mode` is used only to label the alert title
(`:1193`). Both paper and live run CHECK1, so any change lands in both and must be asserted in both.

---

## What was NOT done

⛔ No build. ⛔ No design. ⛔ No change to the order path.
Read-only throughout: the live DB was opened `mode=ro&immutable=1` (zero-trace, no `-shm`/`-wal`
created); logs were read, never written.
