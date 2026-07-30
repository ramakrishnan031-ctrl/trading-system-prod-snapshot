# Q4 — HARD_KILL vs DELIVERY. Rama's revised decision, registered and sized.

Written 30-Jul-2026 Thursday, IST. ⛔ **NOTHING BUILT.** Register + size only.
§8 answered 30-Jul evening (Q6/Q7/Q8/Q9). Converted `.txt` → `.md` per the
`docs/audit/` convention.

**Labels:** `MEASURED` (observed in production / read-only broker call) ·
`SOURCE` (read from code, file:line) · `INFERENCE` (reasoned) · `OPEN` (needs Rama).

---

## ⭐⭐ THE ORDERING CONSTRAINT — READ THIS FIRST

> Reconciliation step 1, FORCE_EXIT_ALL and the §4 GTT check **all make a live
> component holdings-aware.** The moment any of them lands, the accident described
> in §2B is gone, and the §2A buy-day product filter is **the only thing left
> preventing delivery liquidation. The filter must land FIRST.**

⛔ This is a hard sequencing rule across **three separate workstreams**, not a
caveat on one of them. It must survive into every file that touches any of them —
the reconciliation redesign, the FORCE_EXIT_ALL design, and this document.

---

## 1. The decided behaviour (Rama, 30-Jul, revising Q4)

> "HARD_KILL flattens ONLY MIS/intraday. Delivery (CNC) positions SURVIVE
> HARD_KILL — intentionally protected by their GTT and designed to be held across
> sessions. Force-closing delivery must be a SEPARATE explicit action
> (DELIVERY_HARD_KILL / FORCE_EXIT_ALL), not part of normal HARD_KILL. MIS must
> close same-day; CNC is held to SL, target or manual exit."

⭐ **B5 — the invariant is narrowed, and stated:**

**HARD_KILL's invariant becomes "leaves no live INTRADAY position."**

It was documented as "A HARD_KILL must leave NO live broker position"
(`capital/kill_switch.py:1567-1571`). That sentence is now wrong in two different
ways at once, and both must be corrected in the same edit:

- it claims MORE than intended (it should not cover delivery), and
- it is **ALREADY FALSE** for delivery from T+1 (the sweep cannot see holdings).

⛔ A narrowed invariant that is stated is fine. A quietly false one is not.

---

## 2. The inversion — both halves of the old answer flip

### 2A. Buy day: was correct, is now wrong ⇒ CODE CHANGE REQUIRED

`SOURCE` Two sites flatten a CNC position on its buy day, and both do it well:

- **The local-trade path** — the SELECT has NO product filter: it takes every
  trade in `('OPEN','PARTIAL','PENDING_FILL')` (`kill_switch.py:1472-1479`) and
  fetches `product` only to exit under the same product (`:1510`).
- **The FIX-181 LAYER A broker sweep** (`:1567-1611`) — filters ONLY on symbol,
  qty and already-handled. It maps CNC→DELIVERY on purpose (H-5, `:1582-1590`) so
  the exit actually offsets — i.e. it is **BUILT to succeed at selling CNC.**

⇒ On a delivery buy day, a HARD_KILL sells the position Rama has just said must
survive. **Under the revised decision this is a defect, not a design.**

### 2B. T+1 blindness: was a defect, is now the desired outcome — BY ACCIDENT

`MEASURED 30-Jul 10:45, read-only` `positions()` day+net held only a closed
SYNGENE MIS row (qty 0) ⇒ adapter `get_positions()` returns 0, while `holdings()`
returned all five T2 symbols at qty 3 CNC. The sweep reads `get_positions()`.

⇒ From T+1 a HARD_KILL cannot touch a delivery holding — which is now what we
want. ⛔⛔ **DO NOT CLOSE THIS AS "ALREADY CORRECT."** The kill does not CHOOSE to
spare delivery; it cannot SEE it. Behaviour that matches intent by accident breaks
the moment the accident is fixed.

⭐⭐ And that gives the hard ordering constraint stated at the top of this document.

---

## 3. Why option 2 (restrict) and not option 1 (teach it holdings) — on record

Agreed, and the reason is asymmetric risk, not preference:

**Making an emergency path do LESS is a restriction. Making it reach into a data
source it cannot currently read is an extension.** On the one path that runs when
the system's picture of reality is already broken, a restriction cannot introduce
a new failure mode; an extension can (a new broker call, a new timeout, a new
exception surface, inside the flatten).

⭐ **And the pattern already exists in this codebase** — this is not a new idea:
`orders/eod_squareoff.py` restricts to `product in ("MIS","CO")` at BOTH of its
sites (`:1064-1072` FIX-015, and `:1439` for the FIX-182 residual sweep), with
EOD6 stating "DELIVERY (CNC) positions NOT touched" (`:23-24`).

⇒ 2A is bringing the kill path into line with a rule the EOD path has enforced all
along. **Derive it from EOD6; do not invent a second vocabulary.**

---

## 4. B2 — the real technical risk: "protected by GTT" is an assumption

Agreed, and it is the sharpest point in the decision. A HARD_KILL fires precisely
when the system's picture of reality is broken. In that moment nothing has
established that the GTT is ACTIVE, unmodified, or was ever placed.

⇒ **THE DESIGNED FIX (design only, do NOT build):** HARD_KILL does not flatten
delivery, but it **MUST VERIFY that every delivery holding carries a live
protective GTT, and raise CRITICAL for any that does not.** Confirm protected,
never assume protected. It changes no positions and costs Rama nothing.

### ⚠️ The honest bound, written down rather than discovered

**A GTT stop is a TRIGGER plus a LIMIT. A hard gap through the limit leaves it
UNFILLED.** `SOURCE` the SL limit sits `gtt_sl_limit_offset_pct` (3%) below the
trigger; on the T2 band that is −13.0% of arm price, not −10%. So:

- "Protected" means "an order will be attempted at a known level."
- It does **NOT** mean "the position will be closed at that level."
- And a GTT cannot fire at all while the market is closed — overnight gap risk
  lands at the next open, unprotected by construction.

⇒ Delivery surviving HARD_KILL is a **deliberate acceptance of gap risk.** That is
a legitimate choice; it must be recorded as a choice, not as a safety property.

---

## 5. B4 — one root cause, named once. Do not fix it three times.

**A live component that cannot see delivery inventory.** Four known symptoms:

| # | Symptom |
|---|---------|
| (a) | the kill's `get_positions()` blindness (§2B) |
| (b) | `reconcile_positions` reading `positions()` only ⇒ a held delivery trade emits a daily false `MISSING_AT_BROKER` (reconciliation step 1) |
| (c) | FORCE_EXIT_ALL / DELIVERY_HARD_KILL, which must find delivery to act on it |
| (d) | the §4 GTT-coverage check, which needs held-qty AND live GTTs |

⇒ ONE reader serves all four, and it already exists and already runs in
production: **`CncGttMonitor._gather()` (`orders/cnc_gtt_monitor.py:422-455`)**
merges `get_holdings()` (settled + t1) with `get_positions()` FILTERED TO CNC
(`:447-454`), and on broker failure DEFERS with a WARNING rather than reading
no-data as no-positions (Y4). ⛔ **Do not write a third definition of "held".**

⚠️ Lifting it to a shared helper touches a LIVE protection path ⇒ careful loop,
behaviour-identical, existing tests green FIRST.

---

## 6. Blocker or non-blocker — in Rama's shape

**FIRST, THE BASE RATE, BECAUSE IT SIZES BOTH ANSWERS:**

**HARD_KILL HAS NEVER FIRED IN PRODUCTION.** `MEASURED 30-Jul` Zero
"HARD_KILL ACTIVATED" lines anywhere in `logs/`; `kill_switch_state` holds only
INACTIVE with the routine 15:15 SOFT_KILL auto-cleared at 08:15 today.

Six production trigger sites, five modules `SOURCE`: `main.py:719` ·
`capital/drift_handler.py:231` (the ladder's HARD tier — fires on ONE sample) ·
`capital/fund_manager.py:982`, `:2329` (BL9 invariant / bucket overflow) ·
`orders/order_placer.py:1534`, `:3792`.

### (i) The buy-day product filter

**VERDICT: NOT a blocker for the FLAG FLIP. IS a blocker for the CARRY PILOT —
and the calendar puts both on Tue 4-Aug. They must be separated.**

REASON:

- The four-flag flip alone puts no delivery position on the book. A HARD_KILL on a
  flip-only day has nothing new to destroy ⇒ the flip is safe. **NON-BLOCKER.**
- The **CARRY PILOT** (D1, same day) deliberately opens a real CNC position and
  holds it. That position sits in `positions()` all day on its buy day ⇒
  **reachable by both flatten sites** ⇒ a HARD_KILL that day liquidates exactly
  the thing Rama has just ruled must survive.
- ⚠️ **AND 4-AUG IS THE WORST DAY FOR THE MOST REACHABLE TRIGGER.** The flip turns
  `conditional_allocation_enabled` TRUE for the first time, which is what makes
  the capital bucket split depend on the active trade types. The FundManager
  invariant/bucket-overflow sites (`:982`, `:2329`) are precisely the class that
  new capital arithmetic perturbs. Low probability, but its highest probability is
  on the one day a delivery position is also open. `INFERENCE` — the trace is now
  **required before 4-Aug**, see Q9.
- **COST IF IT FIRES:** not money — ~₹650 of stock sold at market. **The cost is
  the SEQUENCE:** the carry pilot's evidence is an UNBROKEN carry, so a
  liquidation forces a full restart (2+ days), pushing completion into mid-August.

**REQUIRED ACTION — ✅ DECIDED, see Q7: option (a).**

- **(a) Ship the filter before 4-Aug.** Small: two sites, precedent already in
  `eod_squareoff.py`, no schema, no config. ⭐ **CHOSEN.**
- ~~(b) Flip on 4-Aug, DEFER the carry pilot until the filter lands.~~
- ~~(c) Accept the risk explicitly, in writing, given the zero base rate.~~

⚠️ **ONE DESIGN QUESTION INSIDE (i)** — the product comes from a correlated
subquery on `orders` and CAN BE NULL; `:1510` currently falls back to INTRADAY,
commented "safest: MIS exits are always allowed." Under the revised decision that
fallback now means **an unknown-product trade gets FLATTENED.** ⇒ see Q6.

### (ii) The GTT-verification-on-kill check

**VERDICT: NON-BLOCKER for 4-Aug.** Should ship with or shortly after (i).

REASON: it is DETECT-AND-ALERT only — it changes no position and cannot make an
emergency worse. Its absence does not create the exposure; it leaves the exposure
unobserved. And on 4-Aug the operator is present and watching, so the compensating
control (Rama's own eyes) is at its strongest that day and weakest afterwards.

⇒ The day it really starts earning is the first **UNATTENDED** delivery day, not
the flip day.

REQUIRED ACTION: design it now (done, §4 + Q8), build it after (i), on the shared
reader from §5. ⛔ Not tonight.

---

## 7. What is explicitly NOT changing

- No code, config or schema touched tonight. This is a register-and-size document.
- The 15:15 breaker and the 15:17 EOD squareoff stay as they are — their CNC
  exclusion is already correct at all three sites (EOD6 / FIX-015 / FIX-182).
- FORCE_EXIT_ALL / DELIVERY_HARD_KILL is NOT designed here beyond naming it as a
  consumer of the §5 shared reader.
- The T+1 blindness is **NOT** "fixed" as part of (i) — see the ordering
  constraint. Removing the blindness before the filter exists would make things
  worse, not better.

---

## 8. The four questions — ANSWERED 30-Jul evening

### Q7 — ✅ CLOSED. Option (a): ship the filter before 4-Aug.

Rama and independent review both chose (a). ⇒ The buy-day product filter is now a
**dated pre-4-Aug commitment, not a recommendation.** It ships in its own slot
before Tue 4-Aug, and — per the ordering constraint — **before** reconciliation
step 1, FORCE_EXIT_ALL, or the §4 GTT check.

### Q6 — the NULL-product fallback. ⚠️ STILL OPEN — Rama's call.

**RECOMMENDATION: keep flattening the unknown, AND alert on it.**

The reasoning, so it can be argued with:

- The two failure modes are **NOT symmetric.** Wrongly flattening a delivery
  position costs ~₹650 and a sequence restart — **bounded, and recoverable.**
  Sparing a genuinely naked MIS position in an emergency is **unbounded** — that
  is the exposure HARD_KILL exists to remove.
- ⛔ Fail-safe should favour **bounded loss over unbounded exposure**, and that
  direction does not change just because the decision narrowed.
- ⚠️ **BUT A NULL PRODUCT IS ITSELF A DEFECT SIGNAL** — the correlated subquery
  found no order rows for a trade that is OPEN/PARTIAL/PENDING_FILL. ⇒ **Raise
  CRITICAL naming the trade whenever the fallback is taken.** Today it is silent,
  which is how a NULL becomes normal.
- ⛔ Do NOT resolve the NULL by adding a broker lookup inside the flatten — that
  is exactly the extension-vs-restriction argument from §3, and it applies to this
  line too.

⇒ **PUT TO RAMA AS, one line each:**
> **Bounded loss** — flatten the unknown: worst case ~₹650 + a pilot restart.
> **Unbounded exposure** — spare the unknown: worst case a live naked MIS position
> left open through an emergency.

### Q8 — ✅ ANSWERED: YES, verify quantity, not just existence.

**Partial coverage is the silent case.** A GTT for 1 share against a holding of 3
reads as "protected" on every existence check and leaves two thirds bare.

⚠️ **And the sibling case, to be designed at the same time: a GTT whose quantity
EXCEEDS the holding** — after a partial manual exit — which would try to sell
stock that is not there. **Both are the same check**, and it must compare
held-qty against live-GTT-qty in both directions.

⛔ Design only; it ships with or after the filter.

### Q9 — ✅ ANSWERED: YES, trace it. REQUIRED before 4-Aug, not optional.

It is the **ONLY `INFERENCE`** holding up §6(i)'s argument, and that argument is
what puts a code change on the calendar before 4-Aug. **An unmeasured link under a
dated commitment is exactly the shape this project keeps catching.**

- **The question:** does flipping `conditional_allocation_enabled` to TRUE change
  the reachability of the BL9 invariant / bucket-overflow HARD_KILL sites
  (`fund_manager.py:982`, `:2329`)?
- ⛔ **Read-only.** ⛔ Not tonight — it belongs with the filter's slot, before
  4-Aug. Registered against that gate.
- ⚠️ **If the trace shows those sites become MORE reachable after the flip, that
  strengthens (a) from "recommended" to "required"** — and it is worth knowing
  before the pilot is scheduled, not after.

---

## 9. Status of this document

| Item | Status |
|---|---|
| Q4 revised decision | **RECORDED** (§1) |
| Narrowed invariant stated | **RECORDED** (§1, B5) |
| Buy-day product filter | **DECIDED — dated pre-4-Aug commitment** (Q7 = a) |
| GTT-verification-on-kill | **DESIGNED, incl. quantity both directions** (§4, Q8) |
| NULL-product fallback | ⚠️ **OPEN — awaiting Rama** (Q6) |
| BL9 reachability trace | **REGISTERED, required before 4-Aug** (Q9) |
| Code / config / schema | ⛔ **NOTHING BUILT** |
