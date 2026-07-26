# CHECK1's mid-fill deferral (§D) — what was built, and the two limits that keep it OFF

**Date:** 2026-07-26 · **Branch:** `hold-check1-w8-26jul` @ `81f9372` · ⛔ **UNPUSHED**
**Knob:** `order_reconciler.check1_mid_fill_defer_sec` · **default `0.0` = OFF**
Companions: `check1_w8_design_26jul2026.md` (the design) · `check1_external_close_26jul2026.md`
(the defect) · `check1_w8_build_order_26jul2026.md` (why §B/§C/§D are held).

---

## Read this first — the two limits

They lead because they *are* the reason the default is `0.0` and not some small positive
number. Both are measured, neither is inferred.

### Limit 1 — ⚠️ PAPER CANNOT EXERCISE THIS AT ALL

`ZerodhaAdapter.cancel_order`'s paper branch (`broker/zerodha_adapter.py:909-922`) returns
`CancelResult(success=True, reason="")` **unconditionally**. It marks the fill record
`CANCELLED` and returns success whether or not the order exists, whether or not it is
filling, whether or not the broker would have refused.

The deferral fires on exactly one input: the broker refusing our orphan-leg cancel with its
own *"being processed"* reason. Paper cannot produce that string, because paper cannot
produce a refusal. So:

> **`_cancel_reason_being_processed` can never return True in paper mode. The deferral is
> structurally unreachable there — not "rarely hit", not "hard to trigger". Unreachable.**

Raising the knob above `0.0` therefore ships an **unrehearsed path straight into LIVE**, on
the capital-release path, in the reconciler that owns the last-mile close. That is the same
shape as the live-only rung 1 in §C's classifier, and it is why the knob ships OFF.

**What raising it above 0.0 requires FIRST — one of these two, not neither:**

1. **A paper `cancel_order` that can refuse.** A seam that lets a test (or a paper session)
   make the paper branch return `success=False, reason="<order> is being processed"`, so the
   deferral, the expiry, and the release-ordering can all be driven end-to-end in paper.
   Cheapest and reversible; it is a test seam on a stub, not a change to live behaviour.
2. **A live canary.** Enable a small bound on a live day with a single position and watch for
   `CHECK1 DEFERRED` / `CHECK1 DEFERRAL EXPIRED UNRESOLVED`. Real evidence, real capital.

⛔ **Flipping it with neither in place is the thing this note exists to prevent.** See
`paper_fidelity_gaps_26jul2026.md` — this is one of a class, not a one-off.

### Limit 2 — it is NARROW, and buys less than it looks like it buys

Only the broker's own *"being processed"* refusal defers. The **common** shape — one of our
legs already `COMPLETE` in our own records — still finalizes inside CHECK1 at every bound, by
design (ladder rows 1-2: *finalize, attribute*). That shape is **35 of 41 measured cases**.

So even fully on, the deferral changes the outcome for the minority tail. It is not a
rewrite of when CHECK1 acts; it is a bounded pause on one specific, positively-evidenced
signal. Anyone reading "deferral" as "CHECK1 now waits" has it wrong.

---

## 1. What it does

CHECK1 polls broker positions and, on finding one gone, claims the close and releases the
capital. It wins the race against our own fill callback by roughly **0.9 s**. In the window
between the broker filling our exit and `order_placer._handle_exit_fill` arriving, CHECK1
sees a vanished position and attributes it to *someone else* — then `:2374`'s double-close
guard makes that attribution permanent, and the INFO "TARGET HIT" our own path would have
sent is never sent (see `rms_manual_close_is_mislabel_26jul`).

§D adds one bounded pause. When the orphan-leg cancel is refused with *"being processed"*,
that refusal **is positive evidence that the leg is ours and filling right now**. For up to
`check1_mid_fill_defer_sec` seconds CHECK1 finalizes nothing and lets the fill callback that
owns the close do the closing — and the capital release.

**The gate runs BEFORE the claim, and that is the whole mechanism.** A deferral decided after
`mark_trade_manually_closed` fires would defer nothing, because `:2374` has already settled
ownership. Which means: **at bound > 0 the orphan-leg cancel is hoisted above the claim.**
That is safe in both directions — cancelling a resting SL/TGT for a symbol the broker no
longer holds is correct either way, and the mid-fill branch cancels nothing. If the position
turns out not to be gone (a stale snapshot), G5b re-places a recovery SL next cycle: strictly
less harm than the pre-§D path, which closes the trade and releases capital on that same
stale premise.

**Seconds, not cycles.** A cycle count is a proxy for elapsed time whose meaning changes
silently the day `poll_interval_sec` is retuned. The bound is a duration, so it is measured
as one — and off `time.monotonic`, not wall time, because a duration measured on a clock NTP
can step is not a bound.

---

## 2. `0.0` is a true no-op — proven STRUCTURALLY, not behaviourally

The weak version of this proof is "we ran it at 0.0 and got the same numbers." That proves
similarity on the cases tried. It does not prove the new code is not running.

The proof taken instead asserts **unreachability**:

- `_check1_deferred_since` is replaced with a `dict` subclass whose `__getitem__`,
  `__setitem__`, `__delitem__`, `__contains__`, `get`, `pop`, `setdefault` and `update` all
  **raise**. If the gate is entered at all, in any branch, the test dies.
- `_monotonic` is replaced with a callable that **raises**. If the clock is consulted once,
  the test dies.
- ⭐ **And the case is set up with the mid-fill evidence PRESENT** — `cancel_ok=False,
  reason="Order cannot be cancelled as it is being processed"`, the one and only input that
  makes a bound > 0 defer. So the test is not "0.0 happened not to hit the gate on an easy
  case"; it is "0.0 does not hit the gate *on the case built to hit it*."

**And the tripwire is proven live by planting.** Changing the gate's guard from `> 0.0` to
`>= 0.0` — a one-character defect that turns the knob on at its OFF value — produces
**2 RED for two INDEPENDENT reasons**:

| # | detector | what it catches |
|---|---|---|
| 1 | the tripwire | the deferral bookkeeping was touched at bound 0.0 |
| 2 | the capital assertion | 0 capital releases where the pre-§D path performs 1 |

Two independent detectors is what makes it a proof rather than a coincidence. One of them
could be vacuous; both being vacuous in the same way is not a thing that happens quietly.

⚠️ **A note on the first attempt at this, because the lesson generalises.** An earlier
version of a sibling hygiene fix replaced a fixed number with
`len(cfg.scanners) == len(raw["scanners"])`. It *reads* like a property. It is vacuous —
pydantic raises on an unknown scanner rather than dropping it, so the two lengths can never
differ and the assertion can never be red. **A replacement that cannot fail is worse than the
number it replaced, because it looks rigorous.** That is why the check above is stated as
"could this have gone red, and by what mechanism", and answered by planting.

---

## 3. Exactly one release at both bounds

The property that matters for capital is not "does the deferral work" but **"does the
capital get released exactly once, whichever path wins."** Both bounds are asserted:

- **bound `0.0`** — CHECK1 finalizes and releases. **1 release.** (Pre-§D behaviour, byte for
  byte: the gate is not entered.)
- **bound `> 0`, callback arrives** — CHECK1 defers; the fill callback finalizes and
  releases. **1 release.** CHECK1's later cycles find the trade terminal and do not re-enter.
- **bound `> 0`, callback never arrives** — the deferral expires, CHECK1 finalizes and
  releases. **1 release**, one bound later.

Never zero (stranded capital), never two (double release). The deferral moves *which* path
releases and *when*; it never changes *how many times*.

---

## 4. Restart safety — the question §1.5 raised, answered

**The clock is in memory. The trade is not.** That asymmetry is the whole answer.

`_check1_deferred_since` is a plain in-process dict, deliberately not persisted. Kill the
process mid-deferral and the trade is still `OPEN` in the DB with its capital still reserved.
Boot rehydrates it; the next reconcile cycle re-enters CHECK1 with an **empty** map and
starts a **new bounded window — never an unbounded one**.

So losing the clock costs at most one more window of seconds, and **it cannot strand
capital**. Persisting it would buy nothing and would cost a schema migration on the
capital-bearing table — a real risk taken for no gain.

⭐ **This was verified, not reasoned.** A second reconciler instance was constructed over the
same state store — the actual restart shape, not a mocked one — and the trade was confirmed
still open with capital reserved, then confirmed to close exactly once through the fresh
instance. Reasoning about restart safety and *measuring* it are different claims.

Map growth is bounded by trades per process lifetime: `trade_id` is unique and never reused,
and a trade that reached a terminal status cannot re-enter CHECK1, so a resolved entry is
left behind and never read again.

---

## 5. Expiry — a bound that can expire must never expire silently

A mid-fill claim is a claim about **now**. If the bound elapses with nothing terminal, the
claim is **stale**, and a stale claim is not evidence:

- **rung 4 falls SILENT** — including for the contradiction check. A stale claim must not
  manufacture a disagreement with a live source; a false CRITICAL invented by a timer is
  worse than the ambiguity it replaces.
- **the verdict falls to `EXTERNAL_UNATTRIBUTED` at CRITICAL** — the honest answer when the
  evidence expired.
- **rungs 1-3 are untouched.** Expiry retires a stale claim; it never destroys good evidence.
  Forcing CRITICAL on a leg that *did* reach COMPLETE would be a false alarm manufactured by
  a timer.

And it is logged at **WARNING whatever verdict follows**:

```
CHECK1 DEFERRAL EXPIRED UNRESOLVED: trade_id=… symbol=… waited %.1fs of a %.1fs bound
for our own filling leg's callback and it never arrived; finalizing now.
```

⭐ The log is unconditional **on purpose**. An expired deferral is the case where *this design
was wrong* — the premise was that `order_monitor` would observe the terminal state and
`order_placer` would close the trade, and it did not hold. The classifier may still land on
an own leg via some other rung and produce a perfectly good verdict; the design would still
have been wrong to wait. A wrong thing that is silent is this project's signature failure
mode, so the wrongness is reported even when the outcome is fine.

---

## 6. Config surface

```yaml
order_reconciler:
  check1_mid_fill_defer_sec: 0.0   # SECONDS. 0.0 = OFF = pre-§D path exactly.
```

- **Validator floor is `0.0`, not `1.0`** — `0.0` *is* the OFF value. A negative bound would
  make every deferral expire on the cycle it started: the deferral silently disabled while
  the config still claimed it was on.
- **Degrades to OFF** on a non-numeric or absent value (an older config; a test mock whose
  attribute is not a number). The failure direction is "no new behaviour", never "unbounded
  new behaviour".

---

## 7. Status

| item | state |
|---|---|
| §D code + tests | built, on `hold-check1-w8-26jul` @ `81f9372`, **unpushed** |
| default | `0.0` — OFF, and proven a structural no-op |
| paper rehearsal | ⛔ **impossible today** (Limit 1) |
| precondition to raise it | a refusable paper `cancel_order`, **or** a live canary |
| decision owner | Rama — and this document is the input to that decision |

⛔ **Nothing here changes behaviour on Monday 27-Jul.** The branch is unpushed; the running
service does not have this code. Even once deployed, the shipped default is OFF.
