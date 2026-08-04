# THE FLIP-PUSH PLAN — authored 04-Aug-2026 ~13:0x, for execution TUE evening

**Status: `<PLAN — NO EDIT MADE, NOTHING COMMITTED BEYOND THIS DOCUMENT>`.**
Measured at **`45f904e`** (HEAD at authoring). Docs-only; **0 `.py`, 0 `.yaml` changed.**

## 0. Why this exists, and why the edit is deliberately NOT in it

Authoring the most irreversible change in this project at 18:15, under time pressure,
after a census that may itself demand attention, is the wrong way to do it. The
*authoring* happens now; the *execution* becomes minutes.

⛔ **AND THE EDIT MUST NOT SIT ON `main` EARLY.** This is **D1** turned on its most
dangerous target: `main` is linear, deploy is `push → checkout -f`, and **anything
committed before a push rides that push**. If the 17:35 census comes back bad there is
**no flip** — but flags already committed would ride the *next* push, whatever that push
was for. ⇒ **The one change that must never ship by accident is the one that must not be
staged early.** This document is the mitigation: it makes the edit cheap to perform
correctly at the right moment, without existing until that moment.

---

## 0a. ✅ RULED 04-Aug ~13:00 — `trade_type` IS IN THE FLIP, AND WHAT THAT MEANS FOR R2

**`trade_type: INTRADAY → BOTH` is accepted into the flip's edit list.** ⛔ `BOTH`, never
`DELIVERY`.

⭐⭐ **STATE IT PLAINLY, BECAUSE THE SHAPE MATTERS MORE THAN THE FLAG: the flip's INTENT was
ruled and stands; its MECHANISM was incomplete. The flag list was short.** That is
`campaign_practices.md` **§G5, now its FOURTH instance** (#6 · #5 · D4 · here). ⛔ **R2 is
not overturned, questioned, or weakened** — a mechanism found insufficient is returned for
re-scoping, and the intent survives it. ⛔ **Never "the flip plan was wrong."**

⭐ **And the reason this one was nearly missed is the reusable lesson:** the evidence that
looked like *"LAYER 1 is satisfied"* — **zero** LAYER-1 rejections in a full trading day —
was in fact *"LAYER 1 is unreachable"*. **A zero from a guard that sits BELOW an
earlier-returning guard measures the guard above it, not itself.** Reading that zero the
other way is precisely the class of error this campaign keeps finding.

## 0b. ⛔ VERIFICATION CANNOT USE AN EXISTING POSITION — MEASURED 04-Aug 13:05

Read-only broker probe (three GETs — `holdings()`, `get_gtts()`, `positions()`; no writes,
no DB touch, no credential printed):

| | measured |
|---|---|
| Holdings (T+1+ delivery) | **0** |
| Active GTTs | **0** |
| Net positions | **0** |

⇒ **T2's 29-Jul basket is FULLY CLOSED** — all 5 CNC positions and all 5 GTTs are gone.
**There is no already-past-T+1 position to test the holdings-blindness of §1 against.**

✅ **The probe is not vacuous — it could have returned non-zero.** SCI was OPEN at 10:30 and
closed `SL_HIT`/`OWN_SL` (−2.78) before the probe; today's book is flat, 0 open trades. The
zeros are a reading, not a silent auth failure.

⛔ **DO NOT BUY A CNC TEST POSITION TODAY**, and the reason is not the missing position: a
manually-bought CNC row is **untracked to the reconciler**, so it fires the
`Naked untracked position` alert already known false 5/5 (IA-P5-06, GTT-blind), and it adds
a variable to the **one morning deliberately reserved for having none.** ⇒ **Buy after the
flip, and only if the flip's own delivery orders do not supply one naturally.**

## 1. ⛔⛔ THE FINDING THAT CHANGES THE FLIP: `trade_type` IS A SECOND, MASKED GATE

**The flag list this plan was commissioned with did not name `trade_type`. It must.**

`config/system_config.yaml:98-101` states the condition in a comment:

> *"A REAL CNC order or its OCO-GTT is placed ONLY when `delivery_enabled=true` AND
> `force_intraday_only=false` AND `trade_type in {DELIVERY,BOTH}`."*

⚠️ A comment is not evidence — reading one as data is what produced the 31-Jul holiday
error. **Verified in code instead:**

| condition | enforced at | what it does |
|---|---|---|
| `delivery_enabled` | `broker/zerodha_adapter.py:558` | `if broker_code == "CNC" and not self._delivery_enabled` → **blocks the order** |
| `force_intraday_only` | `broker/zerodha_adapter.py:544` | coerces any non-INTRADAY intent → INTRADAY, so no CNC `broker_code` is ever produced |
| `force_intraday_only` (again) | `strategies/control.py:90` | **LAYER 0** — a DELIVERY strategy returns `WON'T TRADE` before placement |
| `trade_type` | `strategies/control.py:100` | **LAYER 1** — `trade_type == "INTRADAY" and intent != "INTRADAY"` → `WON'T TRADE` |

⭐⭐ **LAYER 0 RETURNS BEFORE LAYER 1 IS EVER REACHED — WHICH IS WHY `trade_type` LOOKS
SATISFIED AND IS NOT.** Measured on the live VM, today (04-Aug):

- **723 of 723** logged `STRATEGY_CONTROL` rejections were
  *"WON'T TRADE — emergency breaker (`force_intraday_only`) forces intraday"*.
- **ZERO** were LAYER 1's *"master INTRADAY blocks this DELIVERY strategy"*.

That zero is **not** evidence that LAYER 1 is satisfied. It is evidence that LAYER 0
short-circuits above it. ⇒ **Flipping `delivery_enabled` + `force_intraday_only` while
leaving `trade_type: INTRADAY` produces a flip that looks complete and changes NOTHING:**
the same delivery strategies stay blocked, with a different message. **A silent no-op
flip** — the exact "declared thing with no effect" class this campaign exists to find.

⭐ **Independent corroboration from the system's own auditor:** `core/config_auditor.py:258-262`
says *"the naive rule would BLOCK a `trade_type=BOTH` book … **and BOTH is the likeliest
production configuration**."* The codebase already assumes BOTH is where this lands.

⛔ **`BOTH`, not `DELIVERY`.** `control.py:107` — `trade_type == "DELIVERY" and intent !=
"DELIVERY"` → blocks every INTRADAY strategy, i.e. it would **switch off the working
intraday book** that is currently the only thing trading. ⚠️ And `config_auditor.py:196`
makes `force_intraday_only=true` + `trade_type=DELIVERY` a **BLOCK at boot** (0 strategies).

### 1a. ⚠️ SIZE THIS BEFORE FLIPPING — it is not "a flag", it is a book turning on

**63 signals** persisted today with `status = REJECTED_STRATEGY_CONTROL` (the row count in
`signals`); the **723** above counts *evaluation events* in the log, which re-evaluate the
same symbols through the day. **Use 63 as the daily signal-row figure and 723 as the
evaluation figure — they answer different questions and are not interchangeable.**

⇒ Either way, a body of delivery strategies is **enabled, firing, and held dormant only by
LAYER 0**. The morning `force_intraday_only` goes false *and* `trade_type` becomes BOTH,
those signals stop being rejected and start becoming orders. ⛔ **That is the real content
of "the flip" and it should be stated to Rama in those terms, not as a config toggle.**

---

## 2. THE FLAGS — RE-MEASURED AT HEAD (§M3)

⛔ **Cited at `45f904e`. Per §M3 these line numbers are valid ONLY at that SHA —
RE-MEASURE at HEAD before editing.** They have drifted twice in this campaign.

| # | file:line | current | flip to | required? |
|---|---|---|---|---|
| 1 | `config/system_config.yaml:89` | `force_intraday_only: true` | `false` | ✅ **YES** — LAYER 0 + broker coercion |
| 2 | `config/system_config.yaml:96` | `trade_type: INTRADAY` | `BOTH` | ✅ **YES — §1, and it was missing from the brief** |
| 3 | `config/system_config.yaml:102` | `delivery_enabled: false` | `true` | ✅ **YES** — the broker-boundary CNC block |
| 4 | `config/system_config.yaml:146` | `conditional_allocation_enabled: false` | `true` | ⚠️ **NO — see §2a. R10's call, and a NO-OP under BOTH** |
| — | `config/system_config.yaml:463` | `allocator_mode: "shadow"` | *(unchanged)* | ⛔ **NOT IN SCOPE — §2b** |

### 2a. ⚠️ The "4th delivery flag" (R10) is NOT required for this flip, and the register's framing assumes a book we are not deploying

R10 reads *"without it ~70% of capital strands in the idle intraday bucket."* That is true
for a **DELIVERY-ONLY** book. Under **`trade_type: BOTH` with intraday strategies still
live**, `config_auditor.py:258-260` states it directly: *"With `conditional_enabled` TRUE
**and both intents active** `fund_manager` returns the SAME split as FALSE."*

⇒ **Under BOTH, flipping it is behaviourally a no-op.** It becomes load-bearing only if the
active book ever resolves to delivery-only — and the auditor already fails **fast and loud**
in exactly that case: `A5_delivery_without_conditional_allocation`, **Severity.BLOCK**
(`:284`), which fires *only* when `delivery_enabled=true` and **every** tradable strategy is
DELIVERY.

⇒ **Recommendation: leave flag 4 alone on flip night.** It adds a capital-path variable to
the single-variable push for no behavioural gain, and the condition that would make it
matter is guarded by a BLOCK, not by silence. ⛔ **Rama's call (R10), not mine** — but the
premise the register states is not the configuration being deployed, and that is worth
saying before it is decided.

### 2b. ⛔ `allocator_mode` is a different decision and must not ride this push

`system_config.yaml:463` — its own gate is *"≥5 shadow sessions + regret review + Rama + an
off-market cutover"*. It governs **order admission** (FCFS vs allocator), not delivery.
Including it would put two independent behavioural changes on flip morning, which is what
the single-variable sequencing exists to prevent.

---

## 3. ⭐ THE R4 REGISTRY EDITS — EXACT, AND THEY ARE MANDATORY

Ledger #1's census reads `config/expected_managers.yaml`. Its **rule 4** (`:14-15`) is
explicit: *"A registry edit rides the SAME deploy as the behaviour change it describes
(e.g. the 4-Aug flip carries the `cnc_gtt_*` flips below)."*

⛔ **Without these, Wednesday's census mismatches BY DESIGN on the day delivery goes live** —
two dormant-declared units start acting, which the census reports as **MISMATCH(ii)
`dormant&>0`**. The registry itself already carries the instruction as a `flip_note`.

| line | current | change to |
|---|---|---|
| `config/expected_managers.yaml:320` (`cnc_gtt_placer`) | `    state: expected-dormant` | `    state: expected-event-driven` |
| `config/expected_managers.yaml:328` (`cnc_gtt_monitor`) | `    state: expected-dormant` | `    state: expected-event-driven` |

✅ `expected-event-driven` is a valid state (`:16-17`) and **3 entries already use it**;
per `:20` event-driven units **never enter the mismatch block**.

✅ **M1 WIDTH CHECK — the search was wide enough to have found a third.** `delivery|CNC|cnc|
gtt|GTT|flip` across the **entire** registry returns **only** these two entries plus the
header comment at `:15`. **There is no third delivery-gated entry.** The edit is exactly
two lines.

⚠️ **Also update each entry's `reason:` (`:323`, `:331`)** — both currently read
`delivery_enabled=false`, which becomes false the moment the flip lands. Leaving them is
the §G4 failure of a record that quietly becomes wrong. **Decide the wording with the
edit**; the `flip_note:` lines (`:324`, `:332`) should be **struck or dated**, not silently
deleted, once executed.

---

## 4. THE GATE LIST — EXECUTION ORDER

Run in this order; each is a stop, not a checkbox.

1. ⛔ **The 17:35 census is READ and CLEAN** — the whole flip is conditional on it.
   **Absence of a census is NOT a pass** (a crash-stop emits none, B5).
2. **~18:00 cron-drift WARNING seen and matched to `expected_alarms` §5a** — expected, and
   it *clears* by this very push carrying `71f331b`.
3. **D3 — after 18:15 IST**, and **the book is FLAT.** ⛔ **Rama flattens MANUALLY; never
   build an auto-flatten.** (Today's open `SCI` LONG must be closed or exited.)
4. **Service `inactive`** — the 17:35 self-exit already does this; confirm, don't assume.
5. **Forward-shadow already banked for today** — ⛔ never run `forward_shadow_record.py`
   manually; its output cannot be regenerated.
6. **D2 SHA inventory** — every commit in `297b587..HEAD` named in the unpushed ledger.
   ⭐ It has caught its own trailing stamp commits twice; **the flag commit itself must be
   added to the ledger before the push, not after.**
7. **Tree clean** (`git status --short` empty).
8. **`git ls-remote`** — confirm origin has not moved since the last local fetch.
9. **Full gate green, sets not counts** — `pytest tests/unit tests/integration`, compared
   by `comm` against a fresh base. ⚠️ The flip edits `system_config.yaml`, which **tests
   load** — a config change can move the suite, so this is not a formality.
10. **Push.** Then verify at the **deployed tree**, not the bare HEAD: `md5` the changed
    files PC vs VM, and grep the new values live.

⚠️ **Zero schema change** is expected in this push ⇒ the evening-schema-push overnight
CRITICAL-storm hazard does not apply. **Confirm** `core/schema.sql` diff is 0 lines rather
than assuming it.

---

## 4a. ⭐⭐ THE WEDNESDAY-EVENING GATE — NEW, AND IT IS NOT PART OF TONIGHT

**Ruled 04-Aug ~13:45. ⛔ CHECK1 is NOT changed tonight, and the flip is not held for it.**

**Why the decision is not owed tonight — the calendar is the whole argument.**
**Wednesday's exposure is ZERO:** at **T+0** a CNC position **is** in `positions()`, so
CHECK1 sees it and does nothing wrong. The hazard is **Thursday**, and **Wednesday evening
sits between the two.** ⇒ deciding tonight is deciding **blind**; deciding Wednesday evening
is deciding on a **measurement**. Changing CHECK1 tonight would put a reconciler-behaviour
change on flip morning against **zero exposure that morning**.

### The gate: after Wednesday's close, before Thursday's 08:15 boot — ONE question

> ### 🔴 **Is there a held CNC position with NO `ACTIVE` `gtt_state` row?**

⛔ **Ask it exactly that way.** It covers **both** routes, and checking only for a failed
placement misses the second:
1. **Placement failed** — `cnc_gtt.place_for_fill` raises (missing LTP / C8 violation), so
   `insert_gtt_state` never runs and no row exists.
2. **No failure at all** — the GTT is placed fine, **triggers Wednesday**, leaves `ACTIVE`,
   and a partially-filled sell leaves a **residual holding** Thursday with no ACTIVE row.

| branch | what it means | action |
|---|---|---|
| **No held CNC at all** | the fuse is unlit | nothing to decide; record it |
| **Held CNC + ACTIVE row** | protected | record it — ⭐ and note the protection has now **actually run once in production** |
| 🔴 **Held CNC, NO ACTIVE row** | ⛔ **Thursday's 08:15 must not run undecided** | harden the skip (§4b) **or** Rama closes the position Wednesday. Either is a decision with a fact in hand |

⚠️ **Carry this sentence into Wednesday evening:** `gtt_state` holds **0 rows today**, so
Wednesday's first CNC trade writes the **first row that table will ever carry in
production** ⇒ **the protection is exactly as good as a write that has never executed.**

## 4b. ⭐ WHAT Q4 DOES **NOT** BLOCK — so Wednesday evening knows its options

The obvious remedy is to key CHECK1's delivery skip on **`product == 'CNC'` from the local
trade row** instead of on GTT liveness.

⇒ **That is PRODUCT-aware, not HOLDINGS-aware.** It calls no `holdings()`, so ⛔ **Q4's
ordering rule does NOT bar it**, and ledger **#2 (the prerequisite) already landed.**
**The option is genuinely available Wednesday night.** ⛔ Not authorised now and not designed
here — but recorded so a future reader does not assume Q4 blocks it and rule it out wrongly.

⭐ **Recorded separately, because it is the real defect and not the symptom: keying a
delivery skip on GTT liveness rather than on product is ONE GUARD DOING TWO JOBS.** The
guard answers *"does this trade have a live GTT?"* when the question is *"is this trade
delivery?"* — two different questions, one guard. **`campaign_practices.md` §G-one-guard-two-
jobs, FIFTH instance** (`cutoff_time` · #3b's release-vs-disown · `determine_close_direction`
· `trades.status` · here).

## 5. WHAT THE FLIP DOES **NOT** INCLUDE — stated so it cannot be assumed

- ⛔ **THE CARRY PILOT IS SEPARATE.** The flip enables delivery *trading*; it does not
  authorise carrying a position overnight as a pilot.
- ⛔ **#2e is a hard gate on the CARRY PILOT, not on the flip.** Do not let it block the
  flip, and do not let the flip imply it is cleared.
- ⛔ **The buy-day product filter's third-site ruling**, `Naked untracked position`
  (detector logic), **#3a** (order-completion path), **#6**, **BK-8's build**, and the
  `--cleanup-*` flags all remain deferred and all first execute on flip morning.
- ⚠️ **Riding this push already, from today:** `408e445` (docs), `7662ace` (**ledger #2d —
  kill-path code**), `45f904e` (#9 interim string). **#2d is safe on flip morning ONLY
  because CO is doubly dormant and cannot fire** — say that plainly rather than letting
  "it's fine" stand unexplained.

---

## 6. AT PUSH TIME — the whole edit, in one place

1. **RE-MEASURE** every line number in §2 and §3 at HEAD (§M3). Do not trust this file's.
2. Edit **3 lines** in `config/system_config.yaml` (`:89`, `:96`, `:102`) — ⛔ **and NOT
   `:146` or `:463`** unless Rama rules otherwise on R10.
3. Edit **2 lines** in `config/expected_managers.yaml` (`:320`, `:328`), plus the two
   `reason:` lines and the two `flip_note:` lines per §3.
4. Run the §4 gates in order.
5. Add the flag commit's SHA to the unpushed ledger **before** pushing (D2).
6. Push. Verify at the deployed tree.
7. Label **`<DEPLOYED>`** — ⛔ **never `<VERIFIED LIVE>` on the night.** Delivery is
   verified live only by a real CNC order and its GTT appearing at the broker.
