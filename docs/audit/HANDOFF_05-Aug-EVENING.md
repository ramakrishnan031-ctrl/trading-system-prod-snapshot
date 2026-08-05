# HAND-OFF — 05-Aug-2026 EVENING

> **Written BEFORE it was needed (P3), and kept current as work proceeded.** The PC powers down
> ~16:30–17:00; this is what survives that.
> ⏰ **Last updated: 14:2x IST.**

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
| `docs/audit/item4_partA_inventory_survey_05aug2026.md` | ⭐ **"Part A" does not exist and TWO control inventories do** — item 4 must reconcile before it extends |
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
- **The delivery configuration surface is NAMED, NOT STARTED** — item 5, gated on item 4, and item 4
  now has a survey saying what it must reconcile first.

---

## ▶️ 7. IF SOMEONE PICKS THIS UP COLD

**Read in this order:** this file → the operator card's `§EXEC` front sheet → the addendum.
**Then re-measure the ahead-count with the command** (`git rev-list --count origin/main..main`) —
⛔ **never quote a number from prose; it has rotted four times today alone.**
⭐ **And read `docs/SYSTEM_MAP.md` first, before any measurement** — that is **M8**, written today,
after the campaign twice re-derived facts the map already held.
