# HAND-OFF — 05-Aug-2026 EVENING

> **Written BEFORE it was needed (P3), and kept current as work proceeded.** The PC powers down
> ~16:30–17:00; this is what survives that.
> ⏰ **Last updated: 16:07 IST — 🔴 THE CHECK1 GATE IS MEASURED AND IT PASSES. See §0 below.**

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
