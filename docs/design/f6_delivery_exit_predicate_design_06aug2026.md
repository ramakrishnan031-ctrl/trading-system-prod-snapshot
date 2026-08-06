# F6 / Delivery-Exit Predicate — DESIGN

**06-Aug-2026 · Opus 5 · ⛔ DESIGN ONLY — NO CODE WRITTEN**

> ## Gate status
> ✅ **Gate (1) — evidence CONFIRMED.** Predicate cited, md5-verified against the deployed
> file, mechanism traced end to end, both symptoms explained by one line, negative control
> (05-Aug) and positive control (06-Aug 10:47:46) bracket the defect from both sides.
> ⛔ **Gate (2) — Rama's approval to BUILD — HAS NOT ARRIVED.** He is away until ~17:00.
> **This document is the design step only. Nothing here is implemented.**

---

## 1 · The defect, as measured

**File identity — established by md5, because the VM is not a git checkout:**

| source | md5 | lines |
|---|---|---|
| VM, deployed | `025498f4ae05776915da2c242fdcc86e` | 735 |
| `0197923` | `025498f4ae05776915da2c242fdcc86e` | 735 |
| `HEAD` (`ea9dd28`) | `025498f4ae05776915da2c242fdcc86e` | 735 |

Identical at all three, so the line numbers below hold at the deployed file (M3).

```python
# orders/cnc_gtt_monitor.py:457-464   — the same-day CNC positions leg
for p in positions:
    if str(product).upper() != "CNC": continue
    ...
    held[sym] = held.get(sym, 0) + abs(int(qty))     # :464  THE DEFECT

# orders/cnc_gtt_monitor.py:486-489   — the branch point
if triggered:
    if held == 0:
        return self._finalize_gtt_exit(r, reason="GTT_EXIT")   # closes trade + releases capital
    return self._reprotect(r, held, why="F6: GTT triggered but holding still > 0")
```

Docstring `:9` states the intent: *"the held qty (**holdings + same-day CNC positions**)"*.

**Measured on 06-Aug:** `get_holdings → 0 holdings` at **09:31:55.600**; ATULAUTO CNC
`positions()` row at **−1**; `abs(−1) = +1` ⇒ `held = 1`, which F6 printed **810 ms later**
at 09:31:56.410.

### 1.1 · Two symptoms, one line

`held == 0` is the **only** door to `_finalize_gtt_exit`. Force it false and both follow:

1. **The trade never closes.** ATULAUTO `trd_e66ee17b…` remains `status=OPEN` with
   `margin_reserved=587.4228`, hours after the position genuinely closed at 09:23:45.
2. **A new GTT spawns every cycle.** Three placed on 06-Aug (`330638484`, `330648138`,
   `330657774`) on a ~15-minute cadence.

### 1.2 · Two branches, one poisoned value

⭐ **The re-protect is not the only consumer.** GTT #4 (`330657774`, 10:02:13) came from the
**auto-recreate** branch — `why: "GTT missing; holding intact"` — not from F6. Both read the
same `held`. ⇒ **A fix that corrects F6 and leaves auto-recreate reading the same value
fixes half a loop.**

### 1.3 · Reachability — why this is the first sighting, not the first noticed

| case | `holdings()` | CNC `positions()` | true `held` | current `abs()` | naive `abs()` removal |
|---|---|---|---|---|---|
| carried, unsold | 1 | — | **1** | 1 ✅ | 1 ✅ |
| **carried, sold today (T+1)** | **0** | **−1** | **0** | **1** 🔴 | **−1** 🔴 |
| same-day buy, unsettled | 0 | +1 | **1** | 1 ✅ | 1 ✅ |
| same-day round trip | 0 | 0 | **0** | 0 ✅ | 0 ✅ |

Only the **T+1 exit** row fails. A same-day round trip nets `+1 −1 = 0` ⇒ `abs(0) = 0` ⇒ the
clean door. That is why ASKAUTOLTD exited cleanly on 05-Aug (negative control, `grep -c F6`
= **0** across the whole 05-Aug log) and again on 06-Aug (positive control, §4).

---

## 2 · Root cause — three structural failures, not one sign error

⛔ **A design that only corrects the predicate MUST be rejected by this document.** The sign
is the *instance*; the three items below are the *cause*.

**RC-1 · Two overlapping sources are added as though disjoint.**
`holdings()` and `positions()` describe the same shares at different points of the settlement
lifecycle. A negative CNC `positions()` row means *"sold today from holdings"* — an event
`holdings()` has **already** reflected. Adding it **double-counts the sale**. `abs()` errs
`+1`; the signed sum errs `−1`. Both are wrong because the addition itself is wrong.

**RC-2 · A single release path, gated on a live quantity, with no memory.**
`held == 0` is the sole door to release. Any predicate error — of any cause, transient or
permanent — strands capital **permanently**, because nothing re-examines the decision and
nothing records that this exit was already handled.

**RC-3 · The release is untraceable to its reservation.**
Measured across the whole `fm_ledger`:

| entry_type | rows | with `reservation_id` | with `trade_id` |
|---|---|---|---|
| `RESERVE` | 1422 | **1422** | 0 |
| `RELEASE` | 1189 | **1189** | 0 |
| `COMMIT` | 223 | 223 | — |
| **`RELEASE_USED`** | **220** | **0** | **61** |

⇒ **The keys are perfectly disjoint.** A reservation-keyed query finds 100 % of `RELEASE`
rows and **0 %** of `RELEASE_USED` rows — and `RELEASE_USED` is what a clean delivery exit
writes. Reservation↔release cannot be reconciled by any single key.

> ⚠️ **Known and previously documented, and it still caught us.**
> `docs/audit/e4_investigation_17jul2026.md:227` · `integrity_audit_2026.md:2030,2130` ·
> `04_db_schema_reference.md:202` · `ledger3b_separability_step1_04aug2026.md:14`
> ("⛔ different keys"). The July E4 work added `trade_id` to `RELEASE_USED` (hence 61 of 220);
> **`reservation_id` was never added.**

---

## 3 · The unsampled window — ⚠️ an UNVERIFIED bound that strengthens Invariant B

`holdings()` was sampled at **09:15:07 → `1 holdings`** and **09:31:55 → `0 holdings`**, with
the sale at **09:23:45**. **The 8-minute interval between those samples was never sampled.**

⇒ 🔴 **We have NOT ruled out a transient window in which `holdings()` still reports 1 while
`positions()` already reports −1.** During such a window `held` is wrong under *any* purely
additive rule, including the corrected one.

⭐⭐ **This is the strongest argument in this document for why the sign fix is insufficient.**
A quantity-only predicate acts wrongly during any window in which the quantity is wrong. An
identity-keyed guard does not — it never re-asks a question it has already answered.
🏷️ **UNVERIFIED — stated as a bound, not a claim.** Closing it requires sampling `holdings()`
and `positions()` together at high frequency across a T+1 sale.

---

## 4 · The golden reference trace — ⛔ this must still work afterwards

**ASKAUTOLTD, 06-Aug 10:47:46 — four views, all inside 2 ms:**

| view | evidence |
|---|---|
| trade | `CLOSED` · `exit_reason=GTT_EXIT` · `qty_filled=1` · `margin_reserved=656.5842` |
| `gtt_state` | `330660310` → `CLEANED`, updated **10:47:46.145101** |
| `fm_ledger` | `10222 · 10:47:46.143253 · RELEASE_USED · −656.6 · positional · 1112.44 → 1786.92` |
| log | `10:47:46.145 cnc_gtt_monitor.gtt_exit` + Telegram 10:47:46.798 |

`1112.44 + 656.60 + 17.88 = 1786.92` — reservation **and** realised P&L return in one row.

⭐ **Adopted as the GOLDEN REFERENCE for every future delivery change** — a fixture, not a
one-off record. 📌 Recorded, not chased: `closure_source` and `exit_mechanism` are **empty**
on a `GTT_EXIT` close.

---

## 5 · Constraints (7) — what any candidate must satisfy

| # | constraint |
|---|---|
| 1 | a same-day CNC **BUY (+1)** must still count as held — the case `abs()` was written for |
| 2 | a same-day CNC **SELL (−1)** must **not** count as held |
| 3 | ⛔ **deleting `abs()` does not work** — the signed sum gives `held = −1`, still `!= 0`, still re-protects, and hands `_reprotect` a negative quantity |
| 4 | 🔴 **the auto-recreate branch is in scope** — a second, independent consumer of the same `held` (§1.2) |
| 5 | regression must include a test that goes **RED on current code**, reproducing a T+1 exit — ⛔ not one that merely asserts the new behaviour |
| 6 | ⭐ **the same-day round-trip path worked in production (§4) and must still work** — a regression here breaks the *common* case to fix the *rare* one |
| 7 | 🔴 **the release must be traceable to its reservation** (RC-3) — ⭐ promoted to an architecture rule: **`docs/foundation_engineering_rules.md` §1.11 Traceable Reservation Lifecycle**, so it binds every future ledger change, not just this fix |

## 6 · Invariants (3) — the structural level, which outranks the expression

- **A · One release path is not enough.** Every reservation must have **exactly one matching
  release path**, and a predicate error must not be able to strand capital permanently. A
  second, independent release path is a structural answer; a correct `held` is only a local one.
- **B · Exit identity, not exit quantity.** A GTT whose exit has been processed must never be
  re-evaluated from live quantities. ⭐ §3 shows why: quantity can be transiently wrong;
  identity cannot.
- **C · A release must be traceable.** Every reservation must produce exactly one
  `RELEASE_USED`, **regardless of exit timing** — same-day, T+1, restart, monitor replay —
  **and that release must be keyed to its reservation.** ⛔ An untraceable release is half a
  release: it frees the money and destroys the audit path.

---

## 7 · Design directions — shape only, ⛔ no implementation

**D-1 (addresses RC-1, constraints 1–3, 6).** Stop treating `holdings()` and `positions()` as
disjoint. The settled book is authoritative; a same-day CNC position is a *delta* against it,
and only a **long** delta represents shares not yet in the settled book. A short delta is an
event the settled book has already applied. ⛔ The expression is deliberately not written here.

**D-2 (addresses RC-2, invariants A and B).** Make a processed exit a **recorded, one-way
fact** rather than a state re-derived each cycle from live quantities. Both consumers (F6 and
auto-recreate) must consult it. ⭐ This is the part that survives §3's unsampled window.

**D-3 (addresses RC-3, invariant C, constraint 7).** Carry `reservation_id` onto
`RELEASE_USED`, and add a reconciliation path that can detect and release an `OPEN` trade whose
broker position **and** holding are both absent across N consecutive cycles — the second,
independent release path Invariant A requires.

> ⛔ **Rejection criterion, binding on the next revision:** a design consisting of D-1 alone
> **must be rejected**. Constraint 3 proves the obvious edit fails; Invariant B and §3 prove a
> correct `held` still leaves the structure intact.

---

## 8 · Regression cases (6) — all six required

1. same-day CNC **buy**
2. same-day CNC **sell**
3. **T+1 exit** ← goes RED on current code (constraint 5)
4. **repeated monitor execution after a successful exit** ← would have caught the loop
5. **service restart after a successful exit** ← would have caught the phantom
6. **operator manually cancels a system-created GTT while the monitor runs** — does it recreate
   immediately, next cycle, or never? ⭐ the only case sourced from an operator action rather
   than code reading, and 🏷️ **still unobserved** (the 06-Aug attempt never reached the broker)
7. 🔴 **STEADY STATE.** After a successful T+1 release, run **multiple consecutive monitor
   cycles** and verify: **no new GTT · trade stays CLOSED · reservation stays released ·
   available capital stable.** ⭐ Case 4 tests the *first* cycle after an exit; this tests the
   *tenth*. **Today's loop was a steady-state failure, so this case is shaped exactly like the
   bug.**

**Gate:** `pytest tests/unit tests/integration` — ⛔ never `run_tests.py`.

> ⚠️ **The existing BL-3 test is VACUOUS and must be fixed FIRST, then re-run on OLD code.**
> `tests/unit/test_state_store.py:1944` asserts `sum_fm_ledger_margin_delta(rid_closed) == 0.0`
> — and its fixture **inserts a `RELEASE_USED` row WITH a `reservation_id`**, a shape production
> has never produced (0 of 220). The test's own comment describes the production failure mode
> and asserts it cannot happen. ⭐ This is the fixture class: *a fixture asserting what its own
> data cannot support makes a wrong reader look right.*

## 9 · Post-deploy convergence checkpoint

After a successful T+1 exit, all four must converge, per §4's trace:
**reservation ledger · open-trade state · `gtt_state` · available capital.**

⚠️ **The checkpoint's own instrument is compromised until D-3 lands.** Until `RELEASE_USED`
carries `reservation_id`, view 3 can only be matched by **timestamp + bucket + amount**.
⛔ Checking only the F6 branch is what let this ship.

## 10 · Push passengers — ⛔ decided here, not by momentum at 18:15

Two unpushed commits touch Python:

| commit | change | risk |
|---|---|---|
| `c5c1926` | `docs(capital)`: pin WHY rehydrate keys on trade status | **comment only** |
| `0087d3a` | `fix(alerts)`: signal alert's "Risk (1.5 %)" was the SL distance, not a fraction of capital (+ its test) | alert text; no capital or order path |

⭐⭐ **This is not a choice.** `main` is **78 commits ahead** of `origin/main`, and **D1 forbids
a partial deploy** — a push carries everything or nothing.

⇒ **DECISION: the passengers ride.** The consequence, stated so it is not discovered later:
**the regression gate must cover them too**, not just the F6 fix. Both are low-risk by
inspection, and neither touches the capital or order path — but "low risk by inspection" is not
the gate, the gate is the gate.

## 11 · The blind-key callers, traced — ⛔ found, not fixed

**Width:** whole repo, all `*.py`, `venv` excluded. **18 references to
`sum_fm_ledger_margin_delta`; 4 non-test; exactly ONE production call site.**

### 11.1 · `order_reconciler.py:3761` — `_check7_capital_accounting_drift` (BL-3)

It compares `_Reservation.margin` against `sum_fm_ledger_margin_delta(rid)`, and on breach
**logs `BL-3 CAPITAL_ACCOUNTING_DRIFT` and publishes `CapitalDriftDetected`**. ⇒ **it alarms,
so it is on the capital path.**

⭐⭐ **But it iterates `fund_manager.get_live_reservations()`** — and a released reservation is
no longer live. ⇒ **it never asks the function about a closed reservation**, which is the only
case where the blindness produces a wrong number.

🏷️ **Revised classification: (b) LATENT, not live.** My earlier "CONFIRMED LIVE" was about the
*function*, which is measurably wrong (689.41341 for a fully-released reservation); the *live
consumer* is not affected. ⛔ **Recording the downgrade rather than leaving the stronger claim
standing.**

**Two ways it becomes live, both named:**
- **False negative — the direction that matters.** If a trade is live in `fm._reservations`
  while the ledger *has* released it, `ledger_sum` omits the `RELEASE_USED` and the two sides
  agree spuriously ⇒ **`_check7` fails to detect the exact inconsistency it exists to detect.**
- **Phase E.** The docstring defers orphan detection — *"rids in ledger but not in
  `fm._reservations`"* — to Phase E. **That is precisely the change that would start querying
  closed reservations and arm the false positive.**

### 11.2 · `fund_manager.py:1900` — inside `_replay_open_trade` (`:1849`)

Fetches the reservation's full ledger chain at boot to replay it; **blind to `RELEASE_USED`**.
For a correctly-`OPEN` trade no `RELEASE_USED` exists, so it is harmless today.
🔴 **It bites if a trade is `OPEN` while its reservation has been released** — the replay would
re-reserve capital that was already freed, a **double-reserve**.
🏷️ **(b) LATENT.** ⛔ An earlier note said *"rehydrate keys on trade status, so it does not
drive the replay."* **That established it does not drive the selection; it did not establish
the query is harmless.** Corrected here.

### 11.3 · The three qualified sites are safe

`fund_manager.py:2144` (`entry_type='RESERVE'`, 1422/1422) · `fund_manager.py:2113`
(`entry_type='COMMIT'`, 223/223) · `state_store.py:2575` (`entry_type='COMMIT'`).
**Hit count: 2 blind of 5 production sites; 0 live; 2 latent.**

---

## 12 · 🔴 THE TENSION IN TONIGHT'S PLAN — named now, not at 18:15

**The bundle is the problem.** D-3 mixes the root-cause lifecycle fix with ledger auditability:

- **D-3** (carry `reservation_id` onto `RELEASE_USED`) is a **schema + write-path change on the
  capital ledger**, plus a backfill question for 220 existing rows. ⛔ **Not a same-evening
  change under any honest reading of the careful loop.**
- **D-2** (exit identity) must **survive a restart** (regression case 5) ⇒ **persistence** ⇒
  **schema again.**
- **D-1 alone** is forbidden by this document's own binding rejection criterion.

### 12.1 · The proposed split — ⛔ PROPOSED, NOT CHOSEN

- **(a) LIFECYCLE** — D-1 + D-2 — the fix, with its own gate and regression set.
- **(b) AUDITABILITY** — D-3's `reservation_id` + the second release path + §11's two latent
  blind callers. **Independently testable, no deadline, and it now has two named instances.**

### 12.2 · Does (a) alone satisfy the rejection criterion? — **YES**

Answered from the criterion, not the clock. The criterion rejects **D-1 alone**, for two stated
reasons: constraint 3 (the obvious edit fails) and Invariant B + §3 (a correct `held` leaves the
structure intact). **(a) = D-1 + D-2, and D-2 *is* Invariant B.** Both reasons are addressed.
⇒ **(a) is not excluded by the criterion.**

### 12.3 · ⛔ But the criterion is not what binds tonight — schema is

**D-2 needs persistence to survive a restart.** An evening schema push has a measured,
documented consequence: **every heartbeat-writing cron trips `_refuse_migration` until the next
08:15 boot migrates** — a full night of CRITICALs.

⇒ 🔴 **Tonight is only possible if D-2 can be satisfied WITHOUT a schema change.** ⭐ **Open
question for the next revision, not answered here:** `gtt_state.status` already carries terminal
states and already transitioned `ACTIVE → CLEANED` on today's clean exit — **can exit identity
be expressed on the existing column?** ⛔ If not, tonight is off, and that is the answer.

### 12.4 · Pricing the miss, so the choice is made on numbers

Cost is incurred **only if DIFFNKG's GTT triggers tomorrow**. Measured at 11:2x today:
`last_price 446.60` · `sl_trigger 437.20` (**−2.10 %**) · `tgt_trigger 459.45` (**+2.88 %**).
Neither is near. ⛔ I am not converting that to a probability.

- **If it triggers:** one phantom, **~16 % of the delivery bucket**, cumulative stranding ~37 %.
- ⭐ **And it is RECOVERABLE by hand.** A bad deploy to the only path that releases capital is not.

⇒ **expected cost ≈ P(trigger) × one recoverable phantom**, against **a rushed change to the
capital-release path.** ⭐⭐ **A deadline that can only be met by breaking a rule is a deadline
that should be missed.**

---

## 13 · What this design does NOT settle

- The **expression** for D-1 (deliberately — see the rejection criterion).
- Whether §3's transient window exists. 🏷️ **UNVERIFIED.**
- The `(d)` capital-comparison question (equity vs free cash) — a **separate** finding, filed
  against the existing drift row, not in scope here.
- Whether the `reservation_id`-blind-query class is a **fourth** M1 group or an instance of an
  existing one. ⛔ Filed, not folded.
