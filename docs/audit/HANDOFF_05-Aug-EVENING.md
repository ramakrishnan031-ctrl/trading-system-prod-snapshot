# HAND-OFF — 05-Aug-2026 EVENING

> **Written BEFORE it was needed (P3), and kept current as work proceeded.** The PC powers down
> ~16:30–17:00; this is what survives that.
> ⏰ **Last updated: 16:29 IST (clock read from console) — ✅ STAGE 1 AND THE FOUR POST-GATE
> MEASUREMENTS ARE ALL DONE. EVERY PART PASSES.**

---

## ▶️ THE STATE, IN ONE BLOCK — read this first if you are picking up cold

```
STAGE 1a  §A  the CHECK1 gate .............. RESULT = PASS      (16:03-16:06)
STAGE 1b  §B  was the trigger observed ..... RESULT = PASS      (16:10)
              why gtt_state says CLEANED ... RESULT = NOT DETERMINABLE -> Stage 2 answers it
STAGE 1b  §C.1 H5 coupling ................. RESULT = PASS      (CONFIRMED, 16:21)
STAGE 1b  §C.2 sizing prediction ........... RESULT = PASS      (prediction DISPROVED, 16:11)
          §0d fm_ledger capital path ....... RESULT = PASS      (reconciles exactly, 16:14)
          §0d the 4.36 day-P&L gap ......... RESULT = NOT DETERMINABLE (contract note)
          §2c is any CNC trade EXITING ..... RESULT = PASS      (NO -- from STEP 0)
--- post-gate card, 16:21-16:28 block ---
§1  qty_by_flat / verdict integrity ........ RESULT = PASS      (DISPROVED survives)
§2  the real H5 gate named ................. RESULT = PASS      (product-BLIND)
§2  in-repo correction sweep ............... RESULT = PASS      (0 to correct -- honest zero)
§3  does the cost model branch on product .. RESULT = PASS      (YES, at three points)
§3  broker_costs.yaml / hardcoded fallback . RESULT = PASS      (loaded; NO fallback exists)
§4  FIX-133 load-bearing + silent class ..... RESULT = PASS
§4  delivery sizing pinned by arithmetic ... RESULT = PASS      (item 5 reordered)
§5  this file's self-description ........... RESULT = PASS      (4 timestamps corrected)
--- outstanding ---
STAGE 2   the 17:35 census ................. RESULT = NOT RUN   (⛔ not before 17:40)
STAGE 3   the push decision ................ RESULT = NOT RUN   (⛔ not before 18:15)
CHECK (1) broker GTT on the Kite web page .. RESULT = NOT RUN   (⛔ OPERATOR ONLY)
the register card (§5 of the post-gate) .... RESULT = NOT RUN   (⛔ FILE NOT FOUND -- see below)
```

### ⛔ THE REGISTER CARD IS NOT IN THE REPO — SO IT WAS NOT STARTED
`VSCODE_INSTRUCTION_05-Aug-2026_REGISTER-THE-DESIGN-THREAD.txt` **does not exist.**
**Search width, stated:** exact-path `ls` · case-insensitive `find` over the whole tree excluding
`.git` · `git ls-files | grep -i VSCODE_INSTRUCTION` (**zero tracked files of that shape**) · a root
`*.txt` listing. ⇒ **it is an operator-side file, like the run sheet itself.**
⛔ **Not started, and ⛔ NOTHING WAS INVENTED ABOUT WHAT IT CONTAINS.** ⭐ The findings it would draw
on are all written and committed above, so it can be picked up whole whenever the file appears.

### ⭐ §2c IS ALREADY ANSWERED — BY STEP 0, WITH NO EXTRA COMMAND
STEP 0's unfiltered count lists **every** CNC status held today: `CANCELLED 2 · CLOSED 1 ·
FAILED 5 · OPEN 1`. ⇒ **NO CNC TRADE IS IN `EXITING`.** ⇒ 🟢 **the second, unguarded CHECK1 path
(`_check_stuck_exiting`) CANNOT reach a delivery trade today.** An unknown became a known, and
tomorrow's design discussion is simpler for it. ⛔ *Reachability only — it says nothing about
whether that path has ever fired, which remains CANNOT DETERMINE.*

### 🔴 THE NEXT EXACT STEP
**Wait for 17:40, then run the census — operator card §1a.** Capture **both** files **before**
filtering. ⭐ **It now carries a sharpened question:** does `cnc_gtt_monitor` read `acted > 0`?
**That is what separates "the monitor observed the trigger" from "a cleanup swept the row."**

### ⛔ WHAT IS OUTSTANDING
1. **CHECK (1)** — the Kite GTT page. ⛔ **Only Rama can do this.** *(The gate does not depend on
   it: (2) and (3) both passed, so (1) is corroboration.)*
2. **Kite's positions total** vs the measured **₹587.40**, and **Kite's day P&L** vs the ledger's
   **44.61**.
3. **Stage 2** — the census, 17:40+.
4. **Stage 3** — the push decision, 18:15+.

### ✅ AND THE HEADLINE, WITH ITS CEILING HELD
**The gate is CLEAN. No decision is owed tonight. §2b is not reached. Thursday's 08:15 boot may
run.** ⛔ **The Stage-3 implementation gate is NOT met — there is no confirmed defect**, so the push
is optional and routine, and **no implementation is authorised.**
🏷️ **`<DELIVERY ROUND TRIP VERIFIED LIVE 05-Aug; T+1 CARRY UNVERIFIED>`.** ⛔ Write nothing wider.

---

## ✅✅ §0. THE GATE — MEASURED 16:03–16:06 IST. **RESULT = PASS.**

⛔ **This section is MEASURED, on the VM's live DB. It supersedes every operator-reported figure.**

### The four commands and what they returned

**STEP 0 — unfiltered `(product, status)`:**
```
|FAILED|63          <- BLANK product
|REJECTED|59        <- BLANK product
CNC|CANCELLED|2
CNC|CLOSED|1
CNC|FAILED|5
CNC|OPEN|1
MIS|CANCELLED|7
MIS|CLOSED|171
MIS|CLOSED_MANUAL|47
MIS|FAILED|173
```
- ✅ **`CNC|OPEN|1`** — exactly one held delivery position. **Confirms the operator report.**
- ✅ **`CNC|CLOSED|1`** — the same-day round trip is in the DB as **CLOSED**. ⭐ **The system did
  not miss the close.** (Detail pending — §B.)
- 🔴 **THE BLANK PRODUCT IS REAL: 122 rows (63 `FAILED` + 59 `REJECTED`).** The card said a blank
  must be investigated before the branch table is read. **It has been, and here is the finding:**
  ⭐ **every blank-product row is in a TERMINAL, NEVER-FILLED status.** Not one is `OPEN`,
  `PARTIAL`, `EXITING`, `PENDING`, `PENDING_FILL` or `UNKNOWN_IN_FLIGHT`.
  ⇒ **NO HELD POSITION IS HIDDEN FROM THE PRODUCT FILTER**, so the gate below stands.
  ⚠️ **The blank is still a data-completeness gap worth a register sub-entry** — it is the
  `LEFT JOIN` NULL that `schema_product_is_on_orders_05aug` warned about, now **OBSERVED IN
  PRODUCTION at 122 rows** rather than reasoned about. ⛔ **Latent, not live** — it becomes live the
  day a *fillable* status appears with a blank product.

**STEP 0b — duplicate ENTRY rows:** **nothing returned.** ✅ The totals below are trustworthy.

**CHECK (2) — `gtt_state`, the first rows this table has ever held in production:**
```
gtt_id     trade_id                              symbol      status   created_at
330456580  trd_e66ee17b1844491db5d2e99afa6f104b  ATULAUTO    ACTIVE   2026-08-05T10:01:22+05:30
330462987  trd_6b23c2e6899b4449bec816fd276d1185  ASKAUTOLTD  CLEANED  2026-08-05T10:13:27+05:30
```

**CHECK (3) — does the row's `trade_id` match the trade's?**
```
trade_id                              product  status  qty_filled  entry_actual_price  gtt_id     gtt_status
trd_e66ee17b1844491db5d2e99afa6f104b  CNC      OPEN    1           587.4               330456580  ACTIVE
```
✅ **Non-empty `gtt_id`. `gtt_status` = `ACTIVE`. `trade_id` IDENTICAL on both sides.**

**(A) money reconcile:** `positions = 1`, `total_cnc_value = 587.40`.
✅ **The pre-registered prediction was ₹587.40. It landed exactly.** (Kite cross-check owed to Rama.)

### 🟢 THE BRANCH: *"CNC held + every position has a matching `ACTIVE` row"* ⇒ **PROTECTED.**

⭐ **And the thing worth recording beyond the pass: this protection has now actually RUN IN
PRODUCTION for the first time.** The `gtt_state` table was empty before today.

⇒ ⛔ **§2b IS NOT REACHED. No decision is owed tonight. Thursday's 08:15 boot may run.**
⇒ ⛔ **The Stage-3 implementation gate is NOT met — condition (1) requires a CONFIRMED DEFECT and
there is none.** The push, if any, is optional and routine.

```
STAGE 1a (§A the gate)   RESULT = PASS
```

---

## ⭐⭐ §0b. WAS THE TRIGGER *OBSERVED*? — MEASURED 16:10. **RESULT = PASS.**

The GTT fired at the broker. Whether the system saw it is a separate fact, and **it saw it.**

```
        trade_id = trd_6b23c2e6899b4449bec816fd276d1185   (ASKAUTOLTD)
          status = CLOSED
      qty_filled = 1      entry 578.80  ->  exit 596.35
       gross_pnl = 17.55        net_pnl = 16.02
     exit_reason = GTT_EXIT          <- ⭐⭐ STRUCTURED, SPECIFIC, AND CORRECT
  exits_verified = 1
       exit_time = 2026-08-05T10:45:51+05:30
  closure_source = (empty)           <- ⚠️ see below
  exit_mechanism = (empty)           <- ⚠️ see below
```

### 🟢 THE TRAP DID NOT FIRE — and this is the night's second-biggest result

The run sheet's §B named a specific hazard: *if the trigger went unobserved, the trade stays OPEN
with a stale `ACTIVE` `gtt_state` row, its `trade_id` lands in `delivery_trade_ids`, and **CHECK1
skips it permanently** — closed at the broker, open in the DB, capital reserved forever.*

**None of that happened.** The trade is `CLOSED`, the exit price and P&L are recorded, and the
`gtt_state` row moved off `ACTIVE`. ⇒ **The skip-that-protects never became the skip-that-blinds.**

### ⚠️ THE RUN SHEET OFFERED TWO ANSWERS AND THE DB GAVE A THIRD: **`CLEANED`, not `TRIGGERED`**

`CLEANED` is a permitted status (`ACTIVE`/`TRIGGERED`/`CANCELLED`/`EXPIRED`/`REJECTED`/`CLEANED`).
The row was **actively transitioned**, so the outcome is right. ⛔ But *why* the terminal status is
`CLEANED` rather than `TRIGGERED` — whether the monitor observed the trigger and then a cleanup
swept the row, or the cleanup alone closed it — **is NOT determined from the DB alone.**
⭐ **The census (§1c) is what separates them:** `cnc_gtt_monitor` reading `acted > 0` says the
monitor saw it. **This is now a sharper question than the run sheet posed, and Stage 2 answers it.**

```
§0b closure OBSERVED?              RESULT = PASS
§0b WHY the status is CLEANED      RESULT = NOT DETERMINABLE (from the DB) -- deferred to Stage 2
```

### ⚠️ A NEW INSTANCE OF A KNOWN GAP: `closure_source` IS EMPTY ON THE FIRST DELIVERY EXIT

The 28-Jul backfill wrote 35 rows and left **six** NULL. **This is a seventh** — and it is on the
*new* path, so the backfill could not have covered it.
⛔ **And it bites a standing rule:** the memory rule says *"group by `closure_source`, never
`exit_reason`."* On this trade `closure_source` is **empty** and `exit_reason` = `GTT_EXIT` is the
**only** field carrying the information. ⇒ **That rule was written for the MIS/`MANUAL` mislabel and
has no operand on the delivery path.** ⚠️ **Register sub-entry, not a fix, and not tonight.**

---

## ⭐⭐ §0c. THE SIZING PREDICTION — MEASURED 16:11. **RESULT = PASS (prediction REFUTED).**

```
symbol      status  filled  by_risk  by_capital  by_concentration  by_flat  binding_constraint  tier_w
ATULAUTO    OPEN    1       8        5           1                 (null)   concentration       0.5
ASKAUTOLTD  CLOSED  1       8        4           1                 (null)   concentration       0.5
```

**The prediction was:** *"affordability binds before the 40% position-value cap, because the cap is
40% of TOTAL while the delivery bucket is only 30% of TOTAL."*

### 🔴 **IT IS DISPROVED — and not narrowly. CONCENTRATION binds, on both trades.**

Scored the correct way (**strictly smallest of the candidates**, ⛔ *not* off `binding_constraint`,
which reports `capital` on a tie): `qty_by_concentration` = **1** against 4–5 and 8.
**Strictly smallest, no tie, both trades.** ⇒ classification **(c) assumption disproved.**
⭐ The reasoning behind the prediction was sound and still is — it simply was not the binding one.

### ⚠️ AND THE CARD'S OWN CAVEAT ALSO LANDED — BOTH ARE TRUE AT ONCE

The card warned: *at qty 1 expect the tier floor to decide; say which it was.* **Both hold, and they
do not conflict:**
- the **label** `concentration` **correctly** describes `raw_qty`'s `min()` — it is genuinely,
  strictly the smallest; and
- the **quantity ordered** was set by the **FIX-133 floor**: `1 × 0.5 = 0.5`, floors to **0**, and
  the floor lifts it back to **1**.

> ### ⭐⭐ **THEREFORE: WITHOUT FIX-133 THE SYSTEM WOULD HAVE ORDERED ZERO, AND NO DELIVERY TRADE
> WOULD HAVE HAPPENED TODAY AT ALL.** The entire flip day rests on that one floor. **Recorded
> because it was not predicted and it is the load-bearing fact under every other result on this
> page.**

⚠️ **A FOURTH `qty_by_*` COLUMN EXISTS THAT THE CARD DOES NOT LIST: `qty_by_flat`.** It is **NULL on
both** trades. ⛔ **See §1 below — this was chased to the source, the verdict SURVIVES, and my
framing of it here was WRONG.** *(Corrected in place rather than deleted: the wrong framing is the
useful half.)*

```
STAGE 1b (§C.2 sizing)   RESULT = PASS -- prediction DISPROVED, bucket (c)
```

---

## ⭐⭐ §0d. `fm_ledger` — MEASURED 16:14. **RESULT = PASS. THE CAPITAL PATH RECONCILES EXACTLY.**

The non-churn rows (`RESERVE`/`RELEASE` throttle noise excluded — there were dozens):

```
10019  08:15:15  INIT          9883.70  both        ->  9883.70
10020  09:15:00  SYNC              0.00  both       9883.70 -> 9883.70   (FM9's single sync)
10047  10:01:21  COMMIT          587.40  positional  fill qty=1 @587.40  excess_returned 29.39
10121  10:13:27  COMMIT          578.80  positional  fill qty=1 @578.80  excess_returned 28.93
10127  10:45:51  RELEASE_USED   -578.80  positional  ASKAUTOLTD exit @596.35 pnl=16.02 costs=1.53
10168  15:19:05  RESET_PNL         0.00  both        EOD reset: previous pnl=44.61
```
*(plus five intraday COMMIT/RELEASE_USED pairs, all closed.)*

### ✅ §B's third question answered: **the capital IS released and the ledger DOES carry the exit.**
`RELEASE_USED` at **10:45:51** — the *same second* as `trades.exit_time` — at **596.35** for
**+16.02**. ⭐ **`trades` and `fm_ledger` agree to the paisa on the first delivery exit ever taken.**

### ⭐⭐ READING B, DERIVED FROM THE LEDGER — NO BROKER NEEDED, AND IT IS EXACT

The `positional` bucket opens at **2965.11** = exactly **30%** of the 9883.70 INIT ✅, and:

```
2965.11  -  587.40  +  16.02  =  2393.73     <- the ledger's final positional balance, EXACTLY
opening     ATULAUTO   ASKAUTO
            committed  realised
```
⇒ 🔴 **THE DELIVERY BLOCK IS ₹587.40 — and that is now the THIRD independent arrival at the same
number:** the pre-registered prediction, the DB's `total_cnc_value`, and the ledger's bucket
arithmetic. ⛔ **Kite's positions total is the fourth and is Rama's to check** — but the two
*measurable* legs of the run sheet's three-way cross-check agree exactly, so a Kite divergence would
now point at the broker/UI reading, not at the system.

✅ **`RESET_PNL` DID fire (15:19:05)** ⇒ the day was **not** HALTED — consistent with the routine
15:15 breaker, which does not forfeit it.

### ⚠️ ONE DIVERGENCE, AND IT IS **NOT DETERMINABLE** FROM HERE

The ledger's day P&L is **44.61** *(18.71 + 7.19 − 2.37 − 4.85 + 16.02 + 10.07 − 0.16 = 44.61 ✅,
already net of the 4.75 total costs)*. The **operator-reported Kite figure was +40.25** — a gap of
**4.36**.
⛔ **I cannot establish from the PC what Kite's "day P&L" field includes** (whether the CNC leg is
counted there or under holdings, and how it treats charges). ⚠️ **So this is a prompt for Rama to
look, NOT a finding** — and ⛔ **it is expressly not attributed to the 4.75 cost total, which is
close to 4.36 but does not equal it.** A near-miss is not an explanation.

```
§0d capital path reconciles       RESULT = PASS
§0d the 4.36 day-P&L gap          RESULT = NOT DETERMINABLE (needs Kite; bucket (d))
```

---

## ⭐⭐ §0e. H5 — MEASURED 16:21. **RESULT = PASS. IT IS *CONFIRMED*, IN PRODUCTION.**

The run sheet asked whether intraday signals on the two CNC symbols were rejected after their fills,
and warned the check **CAN CONFIRM BUT CAN NEVER REFUTE**. ⭐ **The caveat is moot — it CONFIRMED.**

```
ATULAUTO    positional_momentum_long    PROCESSED                              10:00:21
ATULAUTO    positional_sector_rotation  REJECTED_ENTRY_THROTTLED               10:01:15
ATULAUTO    vwap_bounce_long            REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT  10:03:14  <- INTRADAY
ATULAUTO    positional_momentum_long    REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT  10:06:12
ATULAUTO    positional_sector_rotation  REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT  10:07:15
ATULAUTO    vwap_bounce_long            REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT  10:09:13  <- INTRADAY
ATULAUTO    positional_momentum_long    REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT  10:11:13
ATULAUTO    positional_sector_rotation  REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT  10:12:15
ASKAUTOLTD  positional_momentum_long    PROCESSED                              10:13:12
ASKAUTOLTD  positional_sector_rotation  REJECTED_ENTRY_THROTTLED               10:13:15
```
reason text: `ATULAUTO LONG already traded today (1 executed trade(s))`

⇒ 🔴 **THE COUPLING IS REAL AND NOW OBSERVED: a DELIVERY fill consumes the symbol+direction for the
whole trading day, and it blocks INTRADAY strategies too** — `vwap_bounce_long` was refused twice by
a gate that a **CNC** trade had armed. ⛔ Whether that is intended is a **design** question, not a
defect claim: classified **(b) confirmed design, pending confirmation**, ⛔ **no fix implied.**

> ### ⛔⛔ AND THE NEAR-MISS THAT MATTERS MORE THAN THE RESULT
> The run sheet named the codes to look for: **`DUPLICATE_SYMBOL` / `CONTRARY_POSITION`.**
> **I checked. Both returned ZERO rows for the entire day.** The real gate is a **third** code,
> **`SYMBOL_DIRECTION_DAILY_LIMIT`.**
> ⇒ **Had this been run as a grep for the two predicted strings, it would have returned a clean
> zero — and that zero would have been recorded as "the coupling is inert."** The exact false-clean-
> bill the run sheet warned about, avoided **only** by enumerating the actual `rejection_reason`
> values instead of searching for the expected ones.
> ⭐ **A fresh instance of `feedback_absence_needs_wide_check`, and of `V5`: the narrow check had no
> failing input available to it.**

```
STAGE 1b (§C.1 H5)   RESULT = PASS -- CONFIRMED, via a rejection code the card did not name
```

---

## 🔴 1. THE STATE OF THE DAY, IN FIVE LINES

- **Flip day. It worked.** Boot passed on `0197923` (token 08:15:01.9 → service 08:15:05,
  composition 62/62, `will_trade_count: 15` including all three positional strategies). **Delivery
  then traded — CNC held overnight for the first time.**
- **Label: `<DEPLOYED — ENTRY PATH VERIFIED LIVE 05-Aug; EXIT PATH UNVERIFIED>`.** A fill proves
  screening → sizing → affordability → placement → fill. ⛔ **The GTT trigger, T+1 and the carry are
  unproven.**
- **Three `CRITICAL — Capital Drift` emails fired (~10:01 → 11:51). ⛔ NOT a loss** — the check
  compares total capital against broker free cash, so the delta **is** the deployed capital, and it
  **cannot escalate** (its source is excluded from `_ESCALATING_SOURCES`). Accepted as **AR9**, for
  **one session only**, with four reopen conditions.
- **Nothing has been pushed.** `origin/main` is still `0197923`. **No code has been changed all
  day** — every commit is `.md`.
- **⛔ NO IMPLEMENTATION IS AUTHORISED.** The CHECK1 fix is not designed and must not be.

---

## ⏰ 2. WHAT HAPPENS TONIGHT — IN ORDER

| when | what | where |
|---|---|---|
| **16:00** | ⛔ **THE GATE OUTRANKS EVERYTHING.** Switch to the run sheet. | `FINAL_EVENING_RUN_SHEET_vFinal-2_05-Aug-2026.txt` *(operator-side, not in repo)* |
| **any time** | token file check | operator card **§0** |
| **from 16:00** | 🔴 **the CHECK1 gate** — STEP 0, STEP 0b, CHECK (1)(2)(3), the money reconcile | operator card **§2** |
| **after 15:30** | 🔴 **reading B** — the delivery-only margin figure | addendum **§1** |
| **not before 17:40** | ⛔ **the census** — the one artifact that cannot be re-created | operator card **§1** |
| **only if §2 hits the hazard branch** | the three options + their `systemctl` commands | operator card **§2b** |
| **after dinner** | `EXITING` check · sizing score · the drift worksheet | card **§2c/§3/§4** · addendum |
| **Thursday ~08:20** | the boot-seed reading, against a prediction written in advance | addendum **§4b** |

### 🧊 THE TWO OPERATOR DOCUMENTS — BOTH FINAL
- **`docs/audit/EVENING_OPERATOR_CARD_05-Aug-2026.md`** — **FROZEN.** Its commands are final; only
  its change-log was corrected (REVISION 5). It opens with a **`§EXEC` front sheet**: commands in
  order, byte-identical to their sections (verified 16/16), with the decision points deliberately
  held back behind a *STOP, read §2* marker.
- **`docs/audit/ADDENDUM_capital_drift_05-Aug-2026.md`** — the capital-drift worksheet. Separate
  sheet; reuses the card's log capture rather than adding one.

---

## ⚠️ 3. THE FOUR THINGS MOST LIKELY TO GO WRONG TONIGHT

1. **Running §1 too early.** The census is not written until ~17:35. **Before 17:40 it returns
   nothing, and nothing looks exactly like a lost census.** §2 first — it is stable from 15:30.
2. **Reading a same-day `SOFT_KILL` as an emergency.** The routine 15:15 breaker fires **today**, so
   at 19:00 the date proves nothing. **Match the `reason`:**
   `circuit_breaker_force_close_15:15` or `EOD_SQUAREOFF` ⇒ routine. ⛔ **Do not run `resume.sh`.**
3. **Reading `used margin ≈ 0` as a discrepancy.** A delivery purchase may show as a **cash debit**
   instead of a margin block. **`available + used = opening` is the invariant**; which field carries
   the delivery figure is not.
4. **Treating a zero row as a pass.** STEP 0's unfiltered count exists to explain any zero **before**
   the branch table is read.

---

## 🔴 4. THE ONE DECISION THAT MAY BE OWED TONIGHT

**If a held CNC position has no matching `ACTIVE` `gtt_state` row**, a decision is owed **before
Thursday 08:15**. Three options with their costs are at operator card **§2b**. ⛔ **The card does not
recommend one — it is Rama's ruling.**
⭐ **The fact that decides how calmly it is taken: CHECK1 cancels orders and releases capital. It does
NOT place a sell. The shares stay in the account either way.**

---

## 📌 5. WHERE TODAY'S WORK LIVES

| record | what it holds |
|---|---|
| `docs/audit/check1_product_skip_step1_05aug2026.md` | the CHECK1 measurement — operands, the four entry points, the dependency chain, and 8 open design questions **left unanswered** |
| `docs/audit/ADDENDUM_capital_drift_05-Aug-2026.md` | the drift worksheet + the boot-seed prediction + **4 questions recorded unanswered** |
| `docs/audit/item4_partA_inventory_survey_05aug2026.md` | ⭐⭐ **COMPLETE (§1 survey + §2 raw config list).** *"Part A" does not exist*; **TWO control inventories do** and overlap ⇒ **item 4 must RECONCILE before it extends.** Neither can express `ENFORCE + PLACEHOLDER`. Inventory #1's line cites have **rotted (3 of 4 spot-checks)**. 302 config keys enumerated, ⛔ **unclassified and with no value proposed** |
| `docs/campaign_practices.md` | **M7** (a digest without its method) · **M8** (read the record before measuring) · **V5** (a check with no failing input) · **AR9** (the drift acceptance + reopen conditions) · a first *positive* **G1** instance |
| `docs/expected_alarms.md` §3a | the drift CRITICAL, with a six-condition discriminator that can go red |
| `docs/SYSTEM_MAP.md` | Delivery section corrected — it read *"DORMANT / never exercised"* on the day delivery traded |
| memory `UNPUSHED_PENDING_DEPLOY_LEDGER.md` | the RESUME block, the D2 inventory, and the open push-gate question |

---

## ⛔ 6. THE STANDING GATES — CARRIED FORWARD UNCHANGED

- **NO IMPLEMENTATION** unless **both**: evidence **CONFIRMED** (measured, bucket (a)) **AND** Rama
  approves **in his own words**. ⛔ A failing check is not authorisation; a console suggestion is not
  authorisation; a deadline is not authorisation. **If (1) holds and (2) has not arrived: report and
  wait.**
- **D3 — no push before 18:15**, and tonight's push is a **separate operator decision** with an open
  question recorded in the ledger: **the "book flat" pre-push gate meets a book that is correctly
  NOT flat, for the first time.**
- **231 stands.** No register row was created today.
- **The delivery configuration surface is NAMED, NOT STARTED** — item 5, gated on item 4.
  ⭐ **Item 4's step 1 is now DONE, and it changed item 4:** its instruction *"extend the existing
  inventory"* named a subject (*"Part A"*) that **does not exist**, while **two real inventories do**.
  ⇒ **item 4 begins with a RECONCILIATION, not an annotation.** ⛔ Which inventory survives is a
  design decision and was NOT taken.

---

## ▶️ 7. IF SOMEONE PICKS THIS UP COLD

**Read in this order:** this file → the operator card's `§EXEC` front sheet → the addendum.
**Then re-measure the ahead-count with the command** (`git rev-list --count origin/main..main`) —
⛔ **never quote a number from prose; it has rotted four times today alone.**
⭐ **And read `docs/SYSTEM_MAP.md` first, before any measurement** — that is **M8**, written today,
after the campaign twice re-derived facts the map already held.

---

## 🔴 §1. `qty_by_flat` — CHASED TO THE SOURCE (16:21–16:28 block). **THE VERDICT SURVIVES.**

### 1.1 — the measurement, unambiguous

```
symbol      filled  by_risk  by_capital  by_concentration  by_flat  typeof(by_flat)  tier_mode  tier_w  perf_w
ATULAUTO    1       8        5           1                 (empty)  null             ON         0.5     1.0
ASKAUTOLTD  1       8        4           1                 (empty)  null             ON         0.5     1.0
```
**`typeof()` = `null` on both.** ⛔ Not zero, not empty string — **SQL NULL.**

### 1.2 — 🟢 THE VERDICT IS UNCHANGED: **DISPROVED STILL STANDS.**

⛔ **And not because "flat happened to be null" — that would be luck. It is STRUCTURAL:**

**`capital/position_sizer.py:428`** *(HEAD)*:
```python
raw_qty = min(qty_by_risk, qty_by_capital, qty_by_concentration)
```
⭐⭐ **`qty_by_flat` IS NOT A MEMBER OF THAT `min()`. The population is exactly three, by
construction.** `qty_by_flat` is computed **only** in the `else` branch (`:536-548`, mode
`OFF_FLAT`) and is explicitly set to `None` in the `ON` branch (`:495`, `:535`). `core/schema.sql:221`
states it outright: `qty_by_flat INTEGER, -- candidate qty from flat_value_rs (NULL when ON)`.
**Measured `tier_multiplier_mode = ON` on both trades** ⇒ **NULL by construction.**

⭐ **The two modes are MUTUALLY EXCLUSIVE and cannot both be live:**
| | `ON` (today) | `OFF_FLAT` |
|---|---|---|
| `qty_by_flat` | `None` | computed |
| where it acts | — | `tiered_qty = min(raw_qty, qty_by_flat)` **after** the min(), `:541` |
| tier × perf | applied | ⛔ not applied |
| **the floor at 1** | ✅ **applied (FIX-133, `:530`)** | ⛔ **none** — `:539`: *"NO floor-at-1: a flat below 1 lot → BELOW_MIN skip"* |

⇒ **Concentration was strictly the minimum of the COMPLETE `raw_qty` population.** Verdict:
**(c) ASSUMPTION DISPROVED**, now computed over a population verified against source.

### ⚠️ 1.4 — AND THE CORRECTION I OWE: **THE CARD WAS RIGHT AND MY FLAG WAS WRONG**

I wrote that the card *"compares three of four, and a scoring method that cannot see a candidate
would be silently wrong the day it is populated."* ⛔ **That framing is incorrect.** The four columns
are **not four peers**. The card's three-candidate list is the **correct and complete** population
for `raw_qty`. And the day `qty_by_flat` *is* populated, `:542-543` overwrites `constraint` to
`"FLAT"`, so the label follows too.
⭐ **So the card's §3 list is not an incomplete population — it is the right one, and it does not
say why.** *(That last part is the only residual weakness, and it is documentation, not method.)*

### 1.3 — the search width, stated

**Four sources, agreeing:** ① `PRAGMA table_info(trades)` — all **59** columns enumerated, the
`qty_by_*` family is **exactly 4**; ② `core/schema.sql:218-221` declares exactly those 4;
③ a repo-wide grep for `qty_by` (tests excluded) surfaces **no fifth name**; ④ `position_sizer.py`
read end-to-end across the sizing block (`:415-559`) — the only other quantity gates are
`max_single_order_qty` (`:386`, a **REJECT guard**, not a candidate), the lot-size rounding (`:554`)
and the lot-skew check (`:558`). ⛔ **Neither the card's list nor this card's list was taken as the
population — the schema and the `min()` call were.**

```
§1 qty_by_flat / verdict integrity   RESULT = PASS -- DISPROVED stands, population verified
```

---

## ⭐ §2. THE REAL H5 GATE, NAMED — MEASURED (16:21–16:28 block)

### 2.1 — the code, the cites, and the predicate

**Code: `SYMBOL_DIRECTION_DAILY_LIMIT`** → stored as `signals.status =
REJECTED_SYMBOL_DIRECTION_DAILY_LIMIT`.

| where | at HEAD | at **deployed `0197923`** |
|---|---|---|
| the `raise` | `signals/signal_processor.py:717` | **`signals/signal_processor.py:693`** |
| config switch | `config/system_config.yaml:207-215` | same |

⚠️ **The two differ by 24 lines** — `git diff 0197923 HEAD -- signals/signal_processor.py` is
**+29/−5**. ⭐ **Cite `:693` for anything about what ran today; `:717` only for HEAD.** *(A live
instance of `M3`: a card's line numbers hold only at their measured SHA.)*

**The predicate** (`:706-720`, HEAD):
```python
if not bool(getattr(self._risk, "_one_trade_per_symbol_direction", False)):
    return
direction = "LONG" if str(side).upper() == "BUY" else "SHORT"
n = self._store.count_executed_trades_today_for_symbol_direction(symbol, direction, today)
if n >= 1:
    raise _PipelineReject("SYMBOL_DIRECTION_DAILY_LIMIT", ...)
```

> ### ⭐⭐ THE FINDING IS SHARPER THAN "DELIVERY BLOCKS INTRADAY"
> **This gate is PRODUCT-BLIND.** It counts *executed trades* for a symbol+direction and **never
> looks at CNC vs MIS.** ⇒ ⛔ **H5 is not a delivery feature — it is a product-blind daily limit
> that a delivery fill reached for the first time today.** `vwap_bounce_long` was not refused
> *because* the holder was CNC; it was refused because **something** already traded ATULAUTO LONG.
> ⭐ **That is the accurate statement, and it is the one to register** — the delivery-specific
> phrasing would send the next reader looking for a product branch that does not exist.

⏰ **Config context:** the flag was turned **ON by Rama on 27-Jul evening**, first live **Mon 3-Aug
08:15** (`system_config.yaml:207-215`). ⇒ **today is only its third live trading day, and the first
on which a DELIVERY trade armed it.**

### 2.2 — the correction sweep: **IN-REPO COUNT = 0.** ⛔ And that is the honest answer.

`DUPLICATE_SYMBOL` and `CONTRARY_POSITION` are **real, live codes** — `capital/risk_engine.py:668`
and `:689`. A repo-wide grep returns **~30 hits, and every one is a legitimate reference to those
risk-engine gates.** ⛔ **Not one repo record attributes today's H5 rejection to them.**

⇒ **There is nothing to correct in the repo.** The wrong attribution existed **only in the operator
run sheet, which is not a tracked file.** ⭐ **Reported as zero rather than manufactured into a
sweep** — a correction count inflated to look diligent would be the same defect in the other
direction.

⚠️ **One real rot found while sweeping, and it is a different fault:**
`docs/DEPLOY_CALENDAR_28-JUL_TO_04-AUG.txt:674` cites **`signal_processor.py:686`** for this raise.
**Actual: `:693` deployed, `:717` HEAD.** ⛔ **A rotted line cite, not a wrong code name** — logged
here, ⛔ **not edited**: that file is a dated historical record and was true at its own SHA.

### 2.3 — the method instance *(⛔ an INSTANCE of the M-family, not a new rule)*

> **A grep built from a PREDICTED string measures the prediction, not the system. Build the pattern
> from what the code EMITS — or search the behaviour and let it name itself.**

**Today's instance:** the run sheet named two codes; both returned **zero rows for the whole day**;
the gate that actually fired was a **third**. ⇒ **the predicted-string grep would have returned a
clean zero and been recorded as "the coupling is inert."**
⭐⭐ **What makes this one worth registering above the four earlier same-day instances: it is the
first where the wrong grep would have INVERTED A LIVE CONCLUSION** — not left a gap, but produced a
confident, false, opposite answer. ⛔ **That is the `V5` shape** (a check with no failing input
manufactures confidence) **arriving on the signal path.**
✅ **What actually saved it:** enumerating the real `rejection_reason` values instead of searching
for the expected ones — i.e. letting the behaviour name itself.

```
§2 the real H5 gate named          RESULT = PASS
§2 in-repo correction sweep        RESULT = PASS -- 0 records to correct (1 rotted line cite logged)
```

---

## ⚠️ §3. THE COST MODEL AND THE ₹4.36 GAP — MEASURED (16:21–16:28 block). **THE HYPOTHESIS IS REFUTED.**

### 3.1 — **DOES THE COST MODEL BRANCH ON `product`? YES. COMPREHENSIVELY.**

`broker/cost_calculator.py` takes `product` as a **required parameter** (`:117`) and **validates it**
(`:147-148`, `ValueError` on anything but `MIS`/`CO`/`CNC`). It then branches at **three** places:

| component | `file:line` | MIS / CO | **CNC** |
|---|---|---|---|
| brokerage | `:169-174` | `min(flat, pct×turnover)` | **BUY → 0.00 (free)**; SELL → same as MIS |
| **STT** | `:190-198` | **SELL only**, `stt_sell_pct` | ⭐ **BOTH SIDES**, `stt_cnc_pct` |
| stamp duty | `:224-228` | BUY `stamp_duty_mis_buy_pct` | BUY `stamp_duty_cnc_buy_pct` |

and `config/broker_costs.yaml` carries **separate rates**, exactly along the hypothesis's axis:
`stt_sell_pct: 0.025` vs **`stt_cnc_pct: 0.1`** (`:16-17`) · `stamp_duty_mis_buy_pct: 0.003` vs
**`stamp_duty_cnc_buy_pct: 0.015`** (`:21-22`) — **5× the stamp duty and delivery STT on both legs.**

> ### 🟢 **⇒ THE HYPOTHESIS — "the cost model applies intraday rates to a CNC trade" — IS FALSE.**
> ⛔ **There is not one rate table. There are two, and the delivery one is present and correct in
> shape.** ⭐ The hypothesis was worth writing down before the evidence; it is now **scored and
> refuted**, which is the point of writing it down.

### 3.2 — `broker_costs.yaml`: **EXISTS · TRACKED · LOADED. ⛔ AND THERE IS NO FALLBACK.**

`config/broker_costs.yaml` is a tracked file, loaded by `core/config_loader.py:2160` into
`BrokerCostsConfig` (`:1848`), with required-rate validation at `:1858-1884`.
⭐⭐ **`config_loader.py:1852` states it outright: *"No hardcoded fallback values exist in
CostCalculator (CC9)."*** ⇒ 🔴 **the older pending register's "falls back to hardcoded rates" claim
is REFUTED.** *(Re-measured rather than quoted — which is exactly why the instruction said to.)*

### ⭐⭐ THE NUMERIC CONFIRMATION — the recorded cost MATCHES the CNC table, not the MIS one

ASKAUTOLTD, qty 1, buy 578.80 → sell 596.35. **Recorded costs: `1.53`** (gross 17.55 − net 16.02).
Computed by hand from the yaml rates, per-component rounding as `:230-231` (`CC10`) requires:

```
BUY  (turnover 578.80)              SELL (turnover 596.35)
  brokerage  CNC BUY free   0.00      brokerage  min(20, 0.03%)   0.18
  STT        0.1%  both     0.58      STT        0.1%  both       0.60
  exch txn   0.00297%       0.02      exch txn   0.00297%         0.02
  sebi       0.0001%        0.00      sebi       0.0001%          0.00
  GST        18%            0.00      GST        18%              0.04
  stamp      0.015% CNC     0.09      stamp      SELL -> 0        0.00
                          ------                                ------
                            0.69                                  0.84     TOTAL = 1.53  ✅
```
**The same trade priced on the MIS table would have cost ≈ 0.63.** The recorded figure is **1.53**.

⇒ ⭐⭐ **THE CNC BRANCH DEMONSTRABLY *RAN* — it is not merely present in code.** Before today it was
unexercisable with real money (`paper_cannot_exercise_class`). 🏷️ **The CNC cost path moves to
`VERIFIED LIVE 05-Aug`.**
⚠️ **Stated as a hand computation, not an execution:** I did not run the code. Twelve rounded
components agreeing to the paisa is strong evidence, ⛔ not proof.

### 3.3 — ⛔ THE ₹4.36 GAP IS **NOT** RECONCILED, AND STAYS **(d) CANNOT DETERMINE**

The cost model being *correct for delivery* removes the leading hypothesis and **explains nothing**
about the gap. ⛔ **Nothing has been adjusted, and nothing will be, to make two numbers agree.**

**What the contract note would have to show — written now so it can be scored later:**
| the contract note says | verdict |
|---|---|
| day charges **≈ 4.75** | ⇒ the system's costs are right, and **Kite's 40.25 is measuring something else** — most likely excluding the CNC leg or being a positions-page-only figure |
| day charges **≈ 9.11** *(= system gross 49.36 − Kite 40.25, **only if** both figures are net of charges and the gross agrees)* | ⇒ a genuine cost **understatement** — ⛔ but **not** from product-blindness, which §3.1 refutes; the cause would have to be found elsewhere |
| anything else | both readings are wrong and the gross figures disagree too |

### 3.4 — why this mattered beyond ₹4.36, and where it now lands
The audit's central finding is that **costs dominate** — empirical breakeven **43.5%** against
**38–39%** actual. A cost model wrong for delivery would make **every** delivery expectancy number
wrong, and today is the first day it could be scored at all. ⇒ 🟢 **It was scored, and it is right.**
⛔ **That closes the cost-model question, not the ₹4.36 question.**

```
§3.1 does the model branch on product   RESULT = PASS -- YES, at three points
§3.2 broker_costs.yaml / fallback       RESULT = PASS -- loaded, and NO fallback exists
§3.3 the 4.36 gap                       RESULT = NOT DETERMINABLE (contract note; bucket (d))
```

---

## ⭐⭐ §4. WHAT FIX-133 TURNED OUT TO BE — MEASURED (16:21–16:28 block). **THE DAY'S BIGGEST FINDING.**

### 4.1 — FIX-133's floor is **LOAD-BEARING FOR DELIVERY**, not a rounding nicety

`capital/position_sizer.py:527-530`:
```python
tiered_qty = int(math.floor(raw_qty * effective_mult))
# FIX-133 Item 21: cap at 2x base_qty, floor at 1 — for a POSITIVE multiplier only
tiered_qty = max(1, min(tiered_qty, raw_qty * 2))
```
**Measured today:** `raw_qty = 1` · `tier_weight_applied = 0.5` · `perf_weight_applied = 1.0`
⇒ `effective_mult = 0.5` ⇒ `floor(1 × 0.5) = 0` ⇒ **`max(1, 0) = 1`.** ✅ **The floor did the work.**

> ### 🔴 WITHOUT THAT ONE `max(1, …)`: `tiered_qty = 0` → `final_qty = 0` (`:554`) → **`BELOW_MIN`
> skip.** ⇒ **zero delivery orders, zero GTTs, zero evidence — and the entire flip day would have
> produced nothing.**

⚠️ **AND ITS FAILURE MODE IS SILENCE.** A later reader trimming it as "an unnecessary rounding
guard" **stops delivery trading outright**, and it presents as *"no signals qualified today"* —
⛔ **indistinguishable from a normal quiet day.** 🏷️ **Register it as exactly that class: a removal
whose failure mode is silence.** *(Sub-entry on an existing row. ⛔ 231 stands.)*

### 4.2 — 🔴🔴 THE CONSEQUENCE FOR ITEM 5 — **AND IT IS STRONGER THAN THE CARD SUPPOSED**

The card expected concentration **and the tier multiplier** to be the axes that matter first.
⛔ **Measured, the tier multiplier is inert too.**

**At `raw_qty = 1`, every legal multiplier yields qty 1:**
`floor(1 × m) = 0` for **any** `m < 1`, and the floor lifts it to **1**; `m = 1.0` gives 1 directly.
Production bounds `effective_mult ∈ [0.25, 1.0]` (`:486-489`: `performance_allocator` clamps
`min_weight = 0.5`, unknown strategies default to 1.0). ⇒ **no value in that range changes the
outcome.**

**And moving `raw_qty` barely helps** — at the measured tier weight 0.5:
```
raw_qty  1 -> floor(0.5) = 0 -> floored to 1
raw_qty  2 -> floor(1.0) = 1
raw_qty  3 -> floor(1.5) = 1
raw_qty  4 -> floor(2.0) = 2     <- the FIRST raw_qty that changes the ordered quantity
```
⇒ **`raw_qty` must reach 4 before a single extra share is ordered.**
⭐⭐ **But `raw_qty = min(risk 8, capital 5, concentration 1)` — so the moment concentration is
relaxed far enough to reach 4, CAPITAL binds at 4–5 and becomes the new ceiling.**

> ### ⇒ **AT TODAY'S CAPITAL THE DELIVERY QUANTITY IS PINNED AT 1 BY ARITHMETIC, NOT BY
> CONFIGURATION.**
> `delivery_risk_per_trade_pct` and `delivery_max_position_value_pct` **cannot** become binding —
> risk is at 8, capital at 4–5, and neither is anywhere near the floor. ⛔ **And concentration, the
> one axis that can move at all, runs into capital almost immediately.**
> ⭐ **This REORDERS ITEM 5: the config surface is largely INERT at ~₹9.9k total capital against
> ~₹580 share prices. The lever is CAPITAL or price selection — not a config value.**

⛔ **RECORDED, NOT ACTED ON. ⛔ NO VALUE IS PROPOSED FOR ANY DELIVERY CONFIG KEY**, and this finding
is expressly **not** an argument for changing one — it is the measured reason most of them would do
nothing today.

```
§4.1 FIX-133 load-bearing + silent-failure class   RESULT = PASS
§4.2 delivery sizing pinned by arithmetic          RESULT = PASS -- item 5 reordered
```

---

## 🧾 §5. A CORRECTION THIS FILE OWES ABOUT ITSELF

§1–§4 originally carried the headings *"MEASURED 16:30 / 16:36 / 16:45 / 16:52."* ⛔ **Those were my
own estimates, not clock readings, and they ran AHEAD of the real time.** The clock, read from the
console, was **16:27:57** when §4 was committed — so all four sections were measured inside a single
**16:21–16:28** block, not spread across 22 minutes.

**Corrected in place to the honest window.** ⭐ **Recorded rather than quietly fixed, for the same
reason the operator card's REVISION 5 exists: the freeze covers commands and decisions, it does not
license a document to describe itself untruthfully** — and a timestamp is a claim like any other.
⚠️ **The §0-series times (16:03–16:21) are from the first working block and stand.**

```
§5 self-description accuracy   RESULT = PASS (corrected)
```

---

## ⭐⭐ §6. THE ₹4.36 — SECOND HYPOTHESIS. **§1.2 IS ANSWERED: YES, REALISED-ONLY.**

### ⚠️ FIRST, A NEAR-MISS I OWE — I ALMOST RECORDED A FALSE ABSENCE

My first pass grepped `unrealis|mtm|last_price` across `*.py` with **`files_with_matches` and a
15-file limit**, and `capital/fund_manager.py` **was not in the truncated list**. I was one step from
writing *"the module that produces the day figure has no mark-to-market concept."*
⛔ **That would have been FALSE.** An explicit grep of that one file returns **19 hits**.
⭐ **A textbook `feedback_absence_needs_wide_check` failure — a truncated list read as an absence —
caught only by re-running the check narrowed to the file instead of trusting the wide one's
top-15.** ⚠️ **The zero I nearly recorded was a display limit, not a property.**

### 🟢 §1.2 — **THE DAY FIGURE IS REALISED-ONLY BY CONSTRUCTION. YES.**

⭐ **And the true answer is stronger than "there is no MTM", because there IS MTM — it is
architecturally separated.**

| | where | what it is |
|---|---|---|
| **the day figure** | `fund_manager.py:1820-1847` `today_realized_pnl_carryover()` | `Σ pnl_delta` over `_today_release_used_pnl_rows` (`:1809-1818`): `SELECT pnl_delta … WHERE entry_type = 'RELEASE_USED'` |
| when `RELEASE_USED` is written | `:1271` | **only on an EXIT.** An open position has a `COMMIT` with `pnl_delta = 0.0` and **no `RELEASE_USED` row at all** |
| `pnl_delta`'s definition | `:204` | `# realized PnL change (0 for plain release)` — and `:1203-1208`, the E4/W10 contract: `pnl_delta := gross_pnl − costs` (**NET**) |
| **unrealised MTM** | `:386-392`, `:1564-1595` | a **separate, in-memory `dict[str, float]`**, marked ⭐ **"ADVISORY ONLY"** — ⛔ **it never enters `fm_ledger`, and never enters the day figure** |

⇒ 🔴🔴 **THE TWO NUMBERS WERE NEVER COMPARABLE.** The ledger's **44.61** counts closed trades only;
Kite's day P&L may carry ATULAUTO's open mark. ⭐⭐ **The "gap" is a CATEGORY ERROR, not a
discrepancy — a finding about the COMPARISON, not about the money.** ⛔ **No money is missing, and
nothing needed reconciling.**

⚠️ **ONE DISTINCTION THAT MUST NOT BE COLLAPSED:** the *daily-loss GATE* is a different consumer and
**may** use the advisory MTM — `:390-391`: *"Freshness is stamped so the gate can fall back to
realized-only when the MTM is stale/unavailable (never silently)."* ⇒ ⛔ **"the day FIGURE is
realised-only" does NOT mean "the daily-loss LIMIT is realised-only."** Two quantities, one name.

### 1.1 — ⛔ STILL **NOT DETERMINABLE**. Rama's one glance decides it.
**ATULAUTO's close and unrealised P&L on Kite.** Close ≈ **583.04** with unrealised ≈ **−4.36** ⇒
hypothesis **holds**; materially different ⇒ **refuted, and say so.**
⚠️ **A consistency check, offered as consistency and NOT as evidence:** 583.04 sits inside the OCO
band 575.65 / 605.00 — which is merely **consistent** with the GTT correctly not having fired. ⛔ It
confirms nothing.

### 1.3 — BOTH HYPOTHESES ON THE RECORD, WITH THE FIRST ONE'S CAUSE OF DEATH

| # | hypothesis | status | what killed it / what would |
|---|---|---|---|
| **H-A** | the cost model applies intraday rates to CNC | ⛔ **REFUTED** | `cost_calculator.py` branches on `product` at three points; two rate tables; ASKAUTOLTD's 1.53 reproduces from the **CNC** table to the paisa (MIS would be ≈0.63) |
| **H-B** | the ledger is realised-only; Kite's figure includes the open mark | ⚠️ **OPEN — and its PC-side half is CONFIRMED** | realised-only is **proved** (`:1809-1847`). The remaining half needs **one glance at Kite** (1.1) |

⛔ **NEITHER IS ADOPTED. The ₹4.36 stays (d) CANNOT DETERMINE. Nothing has been adjusted.**

```
§1.2 is the day figure realised-only    RESULT = PASS -- YES, by construction
§1.1 does H-B explain the 4.36          RESULT = NOT DETERMINABLE (one Kite glance)
```

---

## 🔴 §7. THE REAL SHAPE OF THE SIZING FINDING — AND ITS INTRADAY TWIN

⭐ *First use of **M9**: every claim below carries its evidence class.*

### 7.1 — the delivery side, as measured today

**(P)** `qty_by_risk 8 · qty_by_capital 5 · qty_by_concentration 1` on both CNC trades;
`tier_weight_applied 0.5`, `perf_weight_applied 1.0`, `qty_filled 1`. **n = 2** — ⛔ **every delivery
trade that has ever existed, and still n = 2.**
**(S)** `position_sizer.py:428` `raw_qty = min(risk, capital, concentration)` · `:527-530`
`tiered_qty = max(1, min(floor(raw_qty × effective_mult), raw_qty × 2))` · `:486-489` production
bounds `effective_mult ∈ [0.25, 1.0]`.
**(I)** ⇒ at `raw_qty = 1`, `floor(1 × m) = 0` for **every** `m < 1` and the floor lifts it to 1;
`raw_qty` must reach **4** before one extra share is ordered. ⛔ **Never observed at any other
capital level — this is arithmetic, not an observation.**

> ### 🔴 **THE DELIVERY QUANTITY IS PINNED AT 1 BY ARITHMETIC, NOT BY CONFIGURATION.**
> **The tier multiplier is inert at this scale, and so is every risk or position-value knob** —
> none can become binding while concentration returns 1. **(I)**

### 7.2 — ⭐⭐ THE INTRADAY TWIN, AND IT IS THE SAME FINDING

The register already carries it, **algebraically, at n in the hundreds** — ⛔ far stronger evidence
than today's n = 2:

| cite | what it says |
|---|---|
| `docs/decisions/02_d1_concentration_sizing.md:12` | *"Concentration binds on 100% of sized trades — `binding_constraint = concentration` in **298/298**. `qty_by_risk` binds **0** times; the risk sizer is **dead by algebra** (concentration binds ⟺ `sl_distance < 10% of price`, always true for 1–3% intraday stops)."* |
| `…:13` | *"Actual risk is ~**1/20th** of intended: median **Rs 4.92** vs intended 1% = **Rs 98.76**."* |
| `docs/audit/q9_batch4_sizing_floors_caps_18jul2026.md:96-97` | ✅ VERIFIED — `qty_by_risk <= qty_by_concentration` in **0** rows |
| `docs/audit/integrity_audit_2026.md:1135` | ⭐ *"conc binds **415/415 + 67/67**; risk-binds **0 ever** (ratio floor **5.0×**); **upgraded from empirical to ALGEBRAIC (IA-P3-04)**"* |

⇒ **(P)+(S)** **THE SAME ALGEBRAIC DEADNESS, NOW SEEN ON THE DELIVERY SIDE TOO.** Today's measured
ratio `risk 8 : conc 1` sits **above** the intraday ratio floor of 5.0×, so delivery is not an
exception to the intraday algebra — it is another instance of it.

### 7.3 — 🔴🔴 THEREFORE: A SEPARATE DELIVERY CONFIG SURFACE **DOES NOT FIX THIS**

**(S)** Both pipelines call the **same** `position_sizer.calculate` — the same `min()` at `:428`, the
same floor at `:530`. A separate surface supplies **different VALUES to the SAME FORMULA.**
**(I)** ⇒ **the binding constraint is ARITHMETIC and it is SHARED.** ⭐⭐ **Two pipelines with two
config surfaces produce TWO INERT CONFIG SURFACES** — because what is inert is the *structure*
(concentration binds, then the floor pins the result to 1), not the *values*.
⇒ ⭐ **THE LEVER IS CAPITAL OR PRICE SELECTION, NOT SETTINGS.**

### ⛔ 7.4 — WHAT THIS IS **NOT**, STATED SO IT CANNOT BE MISREAD

- ⛔ **This is NOT "item 5 is pointless."** ⭐ **A knob that is inert at ₹10k is not inert at ₹100k,
  and ₹10,000 is a TESTING value.** The surface must still exist. **What changes is item 5's
  ORDER: concentration and the tier multiplier FIRST, risk and position value AFTER.**
- ⛔ **NO delivery config value is proposed. None.**
- ⛔ **DO NOT TOUCH THE CONCENTRATION CAP.** My own measurement says relaxing it far enough to matter
  hands the ceiling **straight to capital at 5** — ⭐ **that is a different decision entirely, and it
  is Rama's to take.**
- ⚠️ **The delivery half is n = 2.** The *intraday* half is algebraic at n in the hundreds. ⛔ **The
  strength of the joint claim comes from the intraday side, not from today.**

```
§7 sizing shape + intraday twin + two-inert-surfaces   RESULT = PASS (recorded, not acted on)
```

---

## 🔴🔴 §8. A CORRECTION TO §2 AND TO §0e — AND IT PRODUCES A DATED PREDICTION

### 8.1 — ⛔ **MY "IN-REPO SWEEP = 0" WAS WRONG. THERE IS ONE, AND IT IS THE REGISTER.**

`docs/MASTER_PENDING_01-Aug-2026.md:763` (row 7, H5) says:
> *"a delivery position held for N days **BLOCKS EVERY INTRADAY ENTRY ON THAT SYMBOL FOR ALL N DAYS —
> invisibly, because it rejects as `DUPLICATE_SYMBOL`**, which looks entirely normal in the record."*
> …⭐ *"**CHECKABLE TONIGHT:** was any intraday signal on a held CNC symbol rejected AFTER the CNC
> fill? If yes, H5 has its **FIRST PRODUCTION INSTANCE** — record it."*

⇒ **The register DOES name `DUPLICATE_SYMBOL` as tonight's expected code, and I reported zero records
to correct.**

⚠️ **HOW I MISSED IT, because the mechanism matters more than the miss:** my sweep grep returned
`docs\MASTER_PENDING_01-Aug-2026.md:763:` **`[Omitted long matching line]`** — and **I read an omitted
line as a non-match.** ⛔ It was a *match whose text was too long to display.*
🔴🔴 **THIS IS THE SECOND TIME TODAY A TRUNCATED GREP RESULT PRODUCED A FALSE CONCLUSION** — the
first was the 15-file limit that nearly had me deny MTM exists in `fund_manager.py` (§6). ⭐ **Once
is an error; twice in one session on the same shape is the M9 near-miss clause earning itself
immediately: a truncated or omitted result is not evidence of anything, and must be re-run, not
read.**

### 8.2 — ⭐⭐ BUT THE REGISTER IS **MOSTLY RIGHT**, AND THE CORRECTION IS NARROW

| the register's claim | verdict |
|---|---|
| the mechanism: `has_active_position` filters **symbol + status only, no product, no date** | ✅ **CORRECT (S)** — `state_store.py:839-853`, verified verbatim today |
| consumed at `risk_engine.py:690` as `DUPLICATE_SYMBOL` | ✅ **CORRECT (S)** |
| a delivery hold blocks intraday on that symbol **for all N days** | ✅ **CORRECT** |
| ⛔ **the code you will see is `DUPLICATE_SYMBOL`** | 🔴 **WRONG FOR THE FILL DAY** — and right from day 2 |

**Why:** a **second, earlier, also product-blind** gate shadows it.
`signal_processor.py:693` `SYMBOL_DIRECTION_DAILY_LIMIT` rejects **before** `risk_engine` is reached
⇒ **(P)** today: **6 × `SYMBOL_DIRECTION_DAILY_LIMIT`, 0 × `DUPLICATE_SYMBOL`.**

### 8.3 — 🔴 AND SO §0e's "H5 CONFIRMED" WAS TOO WIDE. THE PRECISE STATEMENT:

- **(P) CONFIRMED:** a **product-blind** gate blocked intraday signals on a symbol a CNC fill had
  claimed. Real, observed, six times. ⭐ **The coupling class is confirmed.**
- 🔴 **(S) NOT H5's MECHANISM:** `has_active_position` was **never reached today** — the pipeline
  rejected upstream. ⇒ ⛔ **H5 itself has NOT had its first production instance. It is still LATENT.**

### ⭐⭐⭐ 8.4 — THE DATED, FALSIFIABLE PREDICTION FOR **THURSDAY 06-Aug**

**(S)** the two gates have **different reset semantics**, both verified today:
| gate | scope | resets? |
|---|---|---|
| `SYMBOL_DIRECTION_DAILY_LIMIT` | `count_executed_trades_today_for_symbol_direction` — `SUBSTR(created_at,1,10)` = **today** (`state_store.py:707-725`) | ✅ **resets at midnight** |
| `DUPLICATE_SYMBOL` | `has_active_position` — `status IN ('PENDING_FILL','OPEN','PARTIAL')`, **no date** (`:839-853`) | ⛔ **does NOT reset while the position is OPEN** |

**(I)** ⇒ ATULAUTO's `created_at` is **05-Aug**, so on **06-Aug** the daily counter returns **0** and
that gate goes quiet — while the trade is still `OPEN`, so `has_active_position` still returns True.

> ## 🔴 **PREDICTION: IF ATULAUTO IS STILL HELD ON THURSDAY AND ANY INTRADAY SIGNAL ARRIVES ON IT,
> THE REJECTION CODE WILL SWITCH FROM `SYMBOL_DIRECTION_DAILY_LIMIT` TO `DUPLICATE_SYMBOL`.**
> ⭐ **That would be H5's genuine first production instance — the one the register has been waiting
> for.** ⛔ **And if no intraday signal arrives on ATULAUTO, the result is `NOT DETERMINABLE`, not a
> refutation** — the same can-confirm-never-refute shape the register already flagged.

⚠️ **THE RISK THE REGISTER CREATED, stated plainly:** an operator checking tonight for
`DUPLICATE_SYMBOL` gets **zero**, records *"no H5 instance"* — ⛔ **and a real product-blind block
that DID happen goes unrecorded.** ⭐ **That is the exact inversion this campaign exists to catch,
and the register itself was the source of the wrong search string.**

⛔ **The register line is NOT edited here** — this is a measurement record; amending row 7 is a
register action and belongs in §9's sub-entry.

```
§8.1 my "sweep = 0" claim              RESULT = FAIL -- corrected above
§8.2 the register's H5 mechanism       RESULT = PASS -- correct (S); only the code name is wrong for day 1
§8.3 H5's own first production instance RESULT = NOT RUN -- still LATENT, not confirmed
§8.4 the Thursday prediction           RESULT = NOT RUN -- scoreable 06-Aug
```

---

# ▶️▶️ §9. RESUME HERE — POWER-DOWN HAND-OFF, WRITTEN 16:55

## 🔴 THE ONE THING STILL OWED, AND IT IS TIME-SENSITIVE

**AMEND REGISTER ROW 7 (H5) — `docs/MASTER_PENDING_01-Aug-2026.md:832`.**
⛔ **NOT DONE. Interrupted mid-edit by the power-down. Nothing was written — the file is clean.**

**What to add** (a **sub-entry** on row 7 — ⛔ **no new row, 231 stands**), all of it already
measured and committed in **§8** above:
- row 7 tells a reader the H5 rejection will read **`DUPLICATE_SYMBOL`**. 🔴 **On the FILL DAY it
  does not** — `signal_processor.py:693` `SYMBOL_DIRECTION_DAILY_LIMIT` rejects **earlier**.
  **(P) today: 6 × symdir, 0 × `DUPLICATE_SYMBOL`.**
- row 7's **mechanism is correct** (`has_active_position` = symbol + status, **no product, no
  date**, `state_store.py:839-853`) and its **multi-day claim is correct.** ⛔ **Only the expected
  code name is wrong, and only for day 1.**
- ⇒ **H5's OWN mechanism has NOT had its first production instance. Still LATENT.**
- ⏰ **THE THURSDAY PREDICTION:** the symdir counter is date-bounded (`SUBSTR(created_at,1,10)`,
  `:707-725`) and **resets at midnight**; `has_active_position` **does not**. ⇒ **if ATULAUTO is
  still held on 06-Aug and any intraday signal arrives on it, the code SWITCHES to
  `DUPLICATE_SYMBOL`** — H5's genuine first instance. ⛔ **No signal ⇒ NOT DETERMINABLE, not a
  refutation.**

⚠️ **Why it matters tonight rather than whenever:** an operator following row 7 as written greps
`DUPLICATE_SYMBOL`, gets **zero**, and records *"no H5 instance"* — **while a real product-blind
block DID happen.** ⭐ **The register itself is currently the source of a wrong search string.**

⚠️ **Editing note for whoever does it:** row 7 is **one enormous single-line table cell**. Appending
inside it is risky; **safer to add the sub-entry as a titled block immediately AFTER the table
ends**, cross-referenced to row 7. ⛔ **Do not break the table.**

## ⏰ THE CLOCK-BOUND ITEMS — UNCHANGED, AND STILL THE PRIORITY

| when | what | notes |
|---|---|---|
| 🔴 **17:40+** | **THE CENSUS — operator card §1a** | ⛔ **irrecoverable · outranks everything above** · the VM is remote and **unaffected by the PC power-down**; the log is written at ~17:35 regardless |
| **18:00** | the cron-drift WARNING should be **GONE** | if it still fires, `71f331b`'s registry half did not take |
| **18:15+** | the push decision (**D3**) | ⛔ **gate is CLEAN ⇒ optional and routine.** No confirmed defect ⇒ **implementation gate NOT met** |
| **THU 08:20** | the boot seed — addendum §4b | and ⭐ **score the §8.4 prediction** |

⭐ **THE CENSUS CARRIES A SHARPENED QUESTION:** does **`cnc_gtt_monitor`** read **`acted > 0`**?
**It is the ONLY source that separates *"the monitor observed the trigger"* from *"a cleanup swept
the row"*** — `gtt_state` reads `CLEANED` and answers neither.

## ✅ WHAT IS DONE — 15 COMMITS, ALL LOCAL

**Post-gate card:** §1 `qty_by_flat` (verdict survives) · §2 the real H5 gate (product-blind) ·
§3 cost model (branches on product; hypothesis refuted) · §4 FIX-133 + sizing pinned by arithmetic.
**Second card:** §1 the ₹4.36 (day figure **is** realised-only ⇒ **category error**) · §2 **M9**
promoted · §3 sizing shape + intraday twin.
**Register card:** §1 item-4 sub-entry (`RECOMMENDED — NOT RULED`) · §2 **M10** promoted, two
corollaries added, two candidates held with their trigger.
**Plus:** §8's correction of two of my own claims, and §5's timestamp correction.

## ⛔ STANDING — RE-CONFIRMED AT POWER-DOWN
**Nothing pushed** · **no code changed** · **no VM writes** (every VM call was `SELECT`/`PRAGMA`) ·
**no design decided** · **no delivery config value proposed** · **231 stands** ·
**no implementation begun.**
🏷️ **`<DELIVERY ROUND TRIP VERIFIED LIVE 05-Aug; T+1 CARRY UNVERIFIED>`.** ⛔ Nothing wider.
⛔ **Do NOT run `deploy/resume.sh`** — tonight's same-day `SOFT_KILL` is the routine 15:15 breaker.

---

# 🔴🔴 §10. THE CENSUS — RUN 17:49. **RESULT = THERE IS NO CENSUS, AND THAT IS THE FINDING.**

⭐ *M9 applied throughout: every claim carries its evidence class.*

## 10.1 — the zero, with its width stated **before** it is interpreted

```
log file  logs/system_2026-08-05.log   16,899,403 B   93,833 lines   mtime 17:48
grep -c  effect_census .............. 0
grep -ci census ..................... 0     <- case-insensitive, whole file
grep -c  effect ..................... 8,204 <- the file is NOT empty of telemetry
```
**(P)** ⛔ **A zero on the one artifact that cannot be re-created — so it was widened before it was
believed.** The `effect` control returning **8,204** is what makes the census zero interpretable:
the logger is alive, the census specifically is absent. *(A check that could have gone red — `V5`.)*

## 10.2 — 🔴 **WHY: THE SERVICE NEVER SHUT DOWN, AND IT DID SO DELIBERATELY**

```
(P) 17:35:00.002 WARNING main  "eod_self_exit: past 17:35 IST but 1 active position(s)
                                remain — staying up to manage them; will exit once flat."
(P) 17:35:00.652 telegram      "[LIVE] EOD shutdown deferred"        -> DELIVERED
(P) 17:49       systemctl      ActiveState=active SubState=running
                               ExecMainStartTimestamp=08:15:05  (never exited)
```
**(S)** `emit_census()` is called **at `_shutdown()` entry** — `main.py:1319`, documented at
`core/effect_telemetry.py:33`. **(S)** `_eod_self_exit_due` (`main.py:1073-1113`) returns due
**only** when active positions (`OPEN`/`PARTIAL`/`PENDING_FILL`) is **zero**.

> ### ⇒ **NO SHUTDOWN ⇒ NO CENSUS. THE EOD SELF-EXIT IS UNREACHABLE WHILE DELIVERY IS HELD.**
> ⭐⭐ **This is not a fault — every component behaved exactly as designed.** The self-exit was
> written for a system that is flat by EOD **by construction**. The flip to delivery removed that
> premise, and the census was silently coupled to it. **A dependency nobody declared.**

⛔ **AND THE SHARPENED QUESTION IS THEREFORE UNANSWERABLE TONIGHT:** *does `cnc_gtt_monitor` read
`acted > 0`?* — **`NOT DETERMINABLE`, and not for tonight only.** It stays unanswerable for **every
day a delivery position is held**, which is the design going forward. ⭐ **The census was the only
source that separates *"the monitor observed the trigger"* from *"a cleanup swept the row"*; that
discriminator is now structurally unavailable on exactly the days it is needed.**

## 10.3 — 🔴🔴 THE THURSDAY CONSEQUENCE — **AND THE REFUTATION ATTEMPT FAILED**

**(S)** `deploy/token_watcher.sh`, decision block, read verbatim:
```bash
if [ "$active_state" = "active" ] || [ "$active_state" = "activating" ]; then
    clear_alert_flags
    # running -- nothing to do
```
⇒ **the watcher's FIRST branch is "active ⇒ do nothing."** Every restart path it owns keys on the
**last exit code**; a service that never exits has none. **(S)** `within_service_window()` is
`[08,16)` — at 08:15 Thursday the watcher is willing, but it never reaches the willing branch.

**(S)** ⛔ **I tried to refute this and could not:**

| what I looked for | result |
|---|---|
| `clear_stale_state` call sites (non-test) | **exactly one** — `main.py:1914`, **boot path** |
| `auto_clear_scheduled_kill` call sites (non-test) | **exactly one** — `main.py:1919`, **boot path** |
| any date-rollover / new-trading-day handler in `main.py`·`core/`·`capital/`·`orders/` | **none** |

⚠️ **Width stated honestly:** the last row is a **name-based grep** (`date_rollover`,
`new_trading_day`, `day_changed`, `rollover`, `_current_day`, `reset_for_new_day`). A
differently-named mechanism would evade it. ⛔ **So the conclusion below is (I), not (P) — it has
never been observed, because this state has never existed before.**

**(P)** the kill is real and it is today's: `15:15:00.969 CRITICAL KillSwitchActivated: INACTIVE ->
SOFT_KILL reason=circuit_breaker_force_close_15:15`. ✅ The routine breaker — **matched on `reason`,
not on date**, exactly as the standing rule requires.

> ## 🔴 **(I) PREDICTION FOR THU 06-Aug — DATED AND FALSIFIABLE**
> **No boot occurs at 08:15** (the service is already `active`). ⇒ **`clear_stale_state` never
> runs.** ⇒ **Wednesday's `SOFT_KILL` is still ACTIVE on Thursday morning, with no path in the
> system that clears it.** ⇒ **the system enters Thursday in a state it has never been in, holding
> a kill it cannot clear by itself.**
> ⛔ **Scored Thursday 08:15–09:20. If a boot line appears, this is REFUTED — say so.**

### ⚠️ 10.3b — THE OBVIOUS REMEDY IS **NOT** SAFE, AND THAT IS THE POINT OF WRITING IT DOWN

A manual `systemctl restart` Thursday morning **would** clear it — `clear_stale_state(06-Aug)`
treats a 05-Aug kill as prior-day, and the watcher's own contract says a prior-day exit-4 gets *"one
clean start attempt."* ⛔ **But a restart on Thursday is exactly when a LATENT path becomes
reachable for the first time:** `reconcile_positions` reads `positions()` only and is **blind to
delivery T+1** — and on T+1 ATULAUTO moves from *positions* to *holdings*. The registered note says
`MISSING_AT_BROKER` *"is REAL but LATENT — never fired, CAN'T until a delivery trade is OPEN in the
LIVE db."* 🔴 **A delivery trade is now OPEN in the live DB.**
⛔ **THEREFORE NO REMEDY IS RECOMMENDED HERE. Both branches carry a first-ever path. This is
Rama's ruling, and it wants the trade-off in front of it, not a default.**

## 10.4 — 🔴 THE DRIFT CRITICAL IS NOW IN A 30-MINUTE OVERNIGHT LOOP **(P, MEASURED)**

```
(P) 10:03:16  expected 9883.70  actual 8764.50  delta 1119.20  tolerance 988.37   <- in-session
    10:13:25           9902.41         8470.74        1431.67            990.24   <- in-session
    11:51:20           9918.40         8828.34        1090.06            991.84   <- in-session
    15:45:10           9928.31         9296.30         632.01             50.00   <- OUT of session
    16:15:29           9928.31         9296.30         632.01             50.00
    16:45:46           9928.31         9296.30         632.01             50.00
    17:16:03           9928.31         9296.30         632.01             50.00
    17:46:24           9928.31         9296.30         632.01             50.00
```
**(P) ALL FIVE OUT-OF-SESSION ALARMS WERE *DELIVERED* AS `[LIVE] ⚠️ Capital Drift Detected`,
severity CRITICAL** — verified in the `alert_send` outcomes, not assumed from the ERROR line.

**(S)** `config/system_config.yaml:368-369`:
`capital_drift_tolerance: 50.0` — *"Production threshold (**out-of-session / overnight**)"* ·
`capital_drift_tolerance_pct: 0.10` — *"**in-session** tolerance = max(Rs, expected×10%)"*.

> ### ⇒ 🔴 **THE TOLERANCE COLLAPSES FROM ~₹993 TO ₹50 THE MOMENT THE SESSION ENDS — WHILE ₹587.40
> OF DELIVERY CAPITAL IS LEGITIMATELY DEPLOYED OVERNIGHT BY DESIGN.**
> **(I)** at `capital_drift_alert_interval_sec: 1800` that is **~2 CRITICALs/hour, ~29 more before
> Thursday 08:15**, and ⛔ **it does not stop there — it continues for every hour the position is
> held.** The service staying up (§10.2) is what keeps the emitter alive to do it.

⚠️ **THIS CORRECTS A STANDING NOTE OF MINE.** I recorded the 10% band as *"an intraday-leverage
calibration."* ⭐ **Right in spirit, and it omitted the fact that decides tonight: the 10% applies
IN-SESSION ONLY.** ⛔ **Overnight — the only time a delivery position can be held — the band is ₹50
flat.** The three in-session alarms breached ~₹990; the five since 15:45 breached **₹50**. *Same
alarm, two different regimes, and only the second one is structural.*
⚠️ **AR9 accepted this CRITICAL for ONE SESSION with four reopen conditions. A regime it was not
scored against has now appeared. ⛔ Not reopened here — flagged for Rama.**

## 10.5 — ⭐⭐ AND THE DRIFT DECOMPOSES **EXACTLY**. ZERO MONEY IS MISSING.

```
expected  9928.31  =  9883.70 (fm_ledger INIT)  +  44.61 (day realised P&L)     ✅ to the paisa
actual    9296.30  =  9883.70                   -  587.40 (ATULAUTO CNC block)  ✅ to the paisa
delta      632.01  =   587.40 (deployed)        +  44.61 (unsettled realised)   ✅ to the paisa
```
⭐ **`V5` satisfied — this check could have gone red.** All three right-hand operands were measured
at **16:14** from `fm_ledger`; both left-hand figures were emitted at **17:46** by a *different*
subsystem (`order_reconciler`, reading the broker API). **Two independent systems, five numbers, no
residual.**

> ### ⇒ **THE DRIFT IS FULLY EXPLAINED: DEPLOYED CAPITAL + UNSETTLED REALISED P&L. NOTHING IS
> MISSING, AND NOTHING NEEDED RECONCILING.**
> ⭐ **This is also the FOURTH independent arrival at ₹587.40** — the pre-registered prediction, the
> DB's `total_cnc_value`, the ledger's bucket arithmetic, and now the broker's own free-cash figure.
> ⛔ **It upgrades the standing note from a qualitative *"operand mismatch"* to a closed identity.**

```
§10.1 the census .................. RESULT = ABSENT (0 of 93,833; width stated)
§10.2 why ......................... RESULT = PASS -- self-exit unreachable while delivery held (P+S)
§10.2 the sharpened question ...... RESULT = NOT DETERMINABLE -- structurally, not just tonight
§10.3 no Thursday boot ............ RESULT = (I) PREDICTION -- refutation attempted and failed
§10.3b the remedy ................. RESULT = NOT RECOMMENDED -- both branches are first-ever
§10.4 drift loop overnight ........ RESULT = (P) MEASURED -- 5 delivered, ~29 more by 08:15
§10.5 drift decomposition ......... RESULT = PASS -- exact, zero residual, 4th arrival at 587.40
```

⛔ **STANDING, RE-CONFIRMED:** nothing pushed · no code changed · **no VM writes — every VM call was
`grep`/`cat`/`sed`/`systemctl show`, and the card's `cp`-capture was deliberately NOT run** ·
no design decided · no delivery config value proposed · **231 stands** · no implementation.
