# CHECK1 + W8 — one design, for review

**Date:** 2026-07-26 · **HEAD:** `bf8adf9` · **Status:** ⛔ **DESIGN ONLY — no code written.**
Order path ⇒ CAREFUL-LOOP. This is the design step; build is a separate, later decision.

**Prior evidence:** `docs/audit/check1_external_close_26jul2026.md` (the investigation).
**Reviewer:** ChatGPT. Open questions for you are marked **❓Q1…Q6**.

---

## 0. First principle — the rule the whole design is subordinate to

> ⭐ **Suppress the CRITICAL only on POSITIVE evidence that one of our own legs accounts for the
> close. NEVER on absence of evidence.**

Broker unreachable, `orders` unreadable, cancel reason unrecognised, ambiguous match ⇒ **still
CRITICAL**. Every unknown resolves *towards* the alert, never away from it.

⚠️ This project has produced a silent success seven times. A design that reaches zero false
positives by failing quiet is that pattern again, and it would be *harder* to detect than today's
noise — today the failure is loud and wrong; a quiet failure is invisible and wrong.

**Corollary — the acceptance test for this design is not "no more false CRITICALs".** It is:
*a genuine external close still fires, on the first occurrence, with every evidence source
degraded.* If that cannot be demonstrated, the design fails regardless of how clean the happy path
looks.

---

## 1. What is actually wrong — three defects, not one

They are independent. Fixing any one alone leaves the behaviour broken.

| # | defect | site | what it causes alone |
|---|---|---|---|
| **D1** | the **discarded result** | `_cancel_orphaned_orders_for_trade` returns `int`; the mid-fill branch never increments it | the caller cannot distinguish *"no orphan legs"* from *"a leg is filling right now"* |
| **D2** | the **ordering** | label written `:1054`; evidence gathered `:1084` | even a perfect signal arrives **after** the verdict |
| **D3** | the **race** | CHECK1's position-poll beats the fill callback by ~0.9 s | our own exit path aborts at `order_placer.py:2374`, **deleting the INFO "TARGET HIT"** |

⭐ **And a third discarded signal, found while designing this** (`:1339 _resolve_exit_price`): it
calls `adapter.get_trades()`, whose rows carry **`order_id`** (confirmed — `zerodha_adapter.get_trades`
surfaces `trade_id, order_id, tradingsymbol, transaction_type, quantity, average_price,
fill_timestamp`), and keeps **only `average_price`**. The identity of the filling order — the single
most direct answer to *"was this ours?"* — is fetched and thrown away on every call.

⇒ **Three separate places in one function already hold the answer.** This is not an information
problem. It is a plumbing problem.

---

## 2. The vocabulary — defined ONCE, shared with W8

⭐ **This is the part that must not be duplicated.** If CHECK1 invents a private label, it becomes
exactly the second classifier W8 (`trades.closure_source`, P3-r10) exists to retire.

**`closure_source` — the proposed enum:**

| value | meaning | who writes it |
|---|---|---|
| `OWN_SL` | our SL leg filled | `order_placer` exit path |
| `OWN_TGT` | our TGT leg filled | `order_placer` exit path |
| `OWN_EOD` | our EOD square-off leg filled | `eod_squareoff` |
| `OWN_KILL` | kill-switch flatten / sweep | `kill_switch` |
| `OWN_GTT` | delivery OCO-GTT leg fired | `cnc_gtt_monitor` |
| `EXTERNAL_UNATTRIBUTED` | position gone, **nothing of ours accounts for it** | `order_reconciler` CHECK1 |

⚠️ **Deliberately NOT in the vocabulary: `BROKER_RMS` and `OPERATOR_MANUAL` as separate values.**
The 05-Jul audit established they are indistinguishable in-data ("an unauthorized manual close is
indistinguishable in-data from a CO SL fire"). Inventing two values we cannot populate would
re-create the over-claim in a new place. **One honest bucket beats two confident guesses.**
If Kite ever exposes an RMS marker, it becomes a *refinement* of `EXTERNAL_UNATTRIBUTED`, not a
retro-fit of the other five.

**Separability:** W8's writers (the five `OWN_*` paths) and CHECK1's writer
(`EXTERNAL_UNATTRIBUTED`) can land in **separate commits**. They must not land with *different
vocabularies*. ⇒ **Agree this table before either is built.**

**❓Q1 — is `OWN_GTT` worth having distinct from `OWN_SL`/`OWN_TGT`?** A GTT leg *is* an SL or TGT,
just broker-managed. Argument for keeping it: the delivery path is the one we cannot yet observe, and
collapsing it hides which mechanism fired. Argument against: it is a *venue*, not a *reason*, and
mixing the two axes is how the current collision started.

---

## 3. The design

### 3.1 D1 — carry the evidence (change the return type)

`_cancel_orphaned_orders_for_trade` returns a small immutable result instead of `int`:

```
OrphanLegOutcome:
    cancelled:    int          # unchanged, existing callers keep working
    mid_fill:     list[str]    # broker refused: "being processed"  -> POSITIVE evidence
    ambiguous:    list[str]    # any other failure / unrecognised reason -> NOT evidence
```

- `mid_fill` non-empty ⇒ positive evidence that one of **our** legs is filling.
- `ambiguous` non-empty ⇒ explicitly **not** evidence; forces the CRITICAL branch (rule §0).

⚠️ The second caller at `:1982` must be updated to read `.cancelled`. Trivial, but it is the whole
reason the return type was an `int` in the first place — worth stating so it is not missed.

### 3.2 D2 — gather, then classify, then act (reorder)

`_check1_manual_close` becomes three explicit phases:

```
A. GATHER   (read-only + the orphan-leg cancel, which is correct regardless of label)
     a1. our exit legs for this trade, with status                      [orders table]
     a2. orphan-cancel outcome                                          [D1]
     a3. broker trades() for this symbol — keep order_id AND price      [one call, already made]
B. CLASSIFY (pure function of A; no I/O, no writes — unit-testable in isolation)
C. ACT      (claim the trade + write closure_source + release capital + alert)
```

The atomic claim (`mark_trade_manually_closed`, today the double-release guard) stays in **C**, so
the idempotency property is unchanged — it simply now carries a *correct* label when it fires.

**❓Q2 — is it safe to cancel orphan legs (A.a2) BEFORE claiming the trade?** Today the cancel runs
only after `actually_closed == True`. My reading: cancelling a resting SL/TGT for a symbol the broker
no longer holds is correct in both branches, and the mid-fill branch deliberately cancels nothing —
so hoisting it is safe. **This is the single riskiest structural change in the design and I want it
challenged.**

### 3.3 B — the classifier (the core)

Pure function. Inputs from A, output `(closure_source, severity, action)`.

| # | condition (checked in order) | closure_source | severity | action |
|---|---|---|---|---|
| 1 | an own leg is `COMPLETE` **and** its `order_id` appears in broker `trades()` for this symbol | `OWN_{leg}` | **INFO** | finalize, attribute |
| 2 | an own leg is `COMPLETE` locally (no broker corroboration available) | `OWN_{leg}` | **INFO** | finalize, attribute |
| 3 | `mid_fill` non-empty (broker refused the cancel: filling now) | — | — | ⭐ **DEFER — finalize nothing this cycle** (§3.4) |
| 4 | deferral budget exhausted (§3.4) | `EXTERNAL_UNATTRIBUTED` | **CRITICAL** | finalize, alert, note the timeout |
| 5 | `ambiguous` non-empty, or broker/orders read failed | `EXTERNAL_UNATTRIBUTED` | **CRITICAL** | finalize, alert |
| 6 | **else** — no own leg accounts for it | `EXTERNAL_UNATTRIBUTED` | **CRITICAL** | finalize, alert |

⭐ Rows **4, 5, 6 are the default**. Every path that is not a *proved* own-leg close ends at
CRITICAL. That is rule §0 expressed as a table.

**❓Q3 — row 2.** Is a locally-`COMPLETE` own leg sufficient without broker corroboration? Argument
for: our `orders` row only reaches COMPLETE when `order_monitor` observed a real broker fill, so it
is already broker-derived. Argument against: it trusts our own bookkeeping at the exact moment we are
reconciling *because* our bookkeeping may be stale. ⚠️ **Note `get_trades()` returns `[]` in paper**
(`zerodha_adapter.get_trades`), so row 1 can never fire in paper — if row 2 were dropped, paper would
classify every own-leg close as `EXTERNAL_UNATTRIBUTED`. That argues for keeping row 2, but it means
**row 1 is live-only and cannot be paper-proven.**

### 3.4 D3 — the race: defer, don't attribute

The race cannot be closed by ordering. A filled TGT and an external close are **identical at the
position level** — both make the symbol vanish from `get_positions()`. CHECK1 polls; the exit path
waits for a callback; the poll will routinely win.

⭐ **So on positive mid-fill evidence, CHECK1 does nothing at all this cycle.** It does not mark, does
not release, does not alert. It logs and returns a `DEFERRED` action. `order_monitor` owns the leg —
which is what the existing comment at `:1450` already says should happen ("let order_monitor observe
the real terminal state on its next poll"); the code just didn't let the *caller* honour it.

**Bounded, so it can never hang:** deferral is capped (proposal: **3 consecutive reconcile cycles**,
or a wall-clock bound). On exhaustion → row 4 → `EXTERNAL_UNATTRIBUTED` at **CRITICAL**, explicitly
noting the timeout. A leg that never completes therefore still surfaces, loudly.

**❓Q4 — is 3 cycles right, and should the bound be cycles or wall-clock?** Cycles are simpler;
wall-clock is robust to a stalled reconciler. I lean wall-clock (~90 s) *and* a cycle cap.

### 3.5 B4 — restoring the deleted alert (a requirement, not a side-effect)

Today: CHECK1 finalizes first ⇒ `close_trade` raises ⇒ `order_placer._handle_exit_fill` returns at
`:2374` ⇒ the INFO **"🎯 TARGET HIT" / "STOP LOSS HIT"** is never sent, and the false CRITICAL takes
its place.

⭐ **Deferral (§3.4) restores it for free, and nothing else does.** If CHECK1 does not finalize, the
fill arrives, `close_trade` succeeds, and `order_placer` runs its full path — including the exit
alert, the OCO sibling cancel, and `cost_breakdown` persistence.

⚠️ **This is why "just attribute it correctly" (label-only) is the wrong fix.** Attribution would fix
the *label* and leave the *alert* deleted. The requirement is that a normal TGT fill produces the
INFO alert — and only deferral achieves that.

**Acceptance test (must be RED on today's code):** drive a TGT fill that races a reconcile cycle;
assert exactly one INFO "TARGET HIT" is sent, zero CRITICALs, `status='CLOSED'`,
`exit_reason='TGT_HIT'`, `closure_source='OWN_TGT'`.

---

## 4. B6 — does this make FIX-183's prepass redundant?

`cnc_gtt_monitor.py:136-140` states the adoption prepass exists *"so an adopted row excludes the
carried CNC trade from CHECK1 BEFORE CHECK1 can mis-mark it CLOSED_MANUAL (the C2.1 gap: a carried
holding lives in holdings(), not positions(), so the reconciler's bp is None)."*

**Answer: NO — and it must not be removed.** The two address different premises:

- This design fixes *"the position vanished and one of our ORDER legs explains it."*
- C2.1 is *"the position never vanished — it moved from `positions()` to `holdings()` overnight."*
  There is **no fill and no leg** to find. Every evidence source in §3.3 returns nothing, so the
  classifier lands on row 6 — `EXTERNAL_UNATTRIBUTED`, **CRITICAL** — which is exactly the wrong
  answer, and is precisely what rule §0 guarantees when evidence is absent.

⭐ **Rule §0 makes this design *strictly dependent* on the prepass, not a replacement for it.** A
carried CNC needs a *positive* exclusion, and adoption provides it.

**❓Q5 — should the classifier also consult `holdings()` directly**, as a belt-and-braces row 0
("the symbol is in holdings ⇒ not closed at all")? It would make the delivery case self-evident
rather than dependent on prepass ordering. Cost: one more broker call per CHECK1 invocation.

---

## 5. B7 — the 6 candidate true positives

Of 41 `CLOSED_MANUAL` rows, **35** have an own `COMPLETE` leg (EOD 22 / SL 9 / TGT 4) and **6** do
not. Those 6 are the only rows that could be genuine external closes:

| symbol | entry | exit | pnl | shape |
|---|---|---|---|---|
| GICRE | — | — | — | `MANUAL_CLOSE_EOD`, `qty_filled=0` — a non-trade, not a close |
| SULA | 159.59 | — | — | no exit price recorded |
| AGARIND | 572.55 | — | — | no exit price recorded |
| EVEREADY | 361.00 | 367.00 | +6.00 | ⭐ real broker exit price, no own leg |
| AEROENTER | 132.13 | 131.99 | −0.42 | ⭐ real broker exit price, no own leg |
| RCF | 137.73 | 137.73 | 0.00 | entry-proxy fallback (exit price unknown) |

**What the design does with them:** under §3.3 all six land on rows 5/6 ⇒ `EXTERNAL_UNATTRIBUTED` at
CRITICAL. **That is the correct outcome** — and it is the design's own true-positive demonstration.

**How they would be verified:** they cannot be, retroactively. Kite's `trades()` is same-day only and
`orders` history resets daily, so the 20/23/24-Jul evidence is gone. ⚠️ **State this plainly rather
than back-fill a story:** *EVEREADY and AEROENTER are consistent with a genuine external close and
cannot now be proven either way.* The design's true-positive proof must therefore come from a
**planted** test (a position that vanishes with no own leg and no mid-fill), not from history.

⭐ **And the correction it forces:** "0 % true-positive rate" is a property of **the four emails
examined**, not of the 41-row population. Do not let the fix be justified by a number that was never
measured population-wide.

---

## 6. What this design deliberately does NOT do

- ⛔ Does not distinguish RMS from operator-manual (§2) — unsupported by data.
- ⛔ Does not remove or weaken FIX-183's prepass (§4).
- ⛔ Does not change the capital path. The double-close guard already makes exactly one path release;
  deferral changes *which* path, never *how many*.
- ⛔ Does not touch `mark_trade_manually_closed`'s idempotency contract.

**❓Q6 — the biggest risk I can see.** Deferral means that, for up to N cycles, a trade whose position
is genuinely gone stays `OPEN` in our DB. Capital stays reserved for that window. Today it is
released immediately (with a wrong label). **Is trading a wrong-but-prompt release for a
right-but-delayed one acceptable?** My view: yes, because the deferral is bounded, the window is
seconds, and the alternative corrupts both the alert stream and exit attribution — but this is a
capital-path trade-off and it is Rama's call, not mine.

---

## 7. Build order, if approved

1. **Agree §2's vocabulary** (blocks everything; a table, not code).
2. W8 writers on the five `OWN_*` paths — independent, low risk, immediately improves attribution.
3. D1 (return type) + D2 (reorder) + the pure classifier — one commit, RED-first.
4. D3 (deferral) + the restored INFO alert — the highest-risk commit, its own gate.

⚠️ **Do not ship 3 without 4.** Correcting the label while leaving the alert deleted removes the noise
that made this visible and keeps the actual damage.
