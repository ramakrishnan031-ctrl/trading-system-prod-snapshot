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
