# ☀️ THURSDAY MORNING — 06-Aug-2026. **ONE SCREEN. START HERE.**

> **Last night ended well.** The service was stopped cleanly at **22:46:36** with the delivery
> position still held. It squared off nothing, cancelled nothing, released no capital, closed no
> position — and the census was recovered (`mismatches=0`). **Nothing is outstanding from Wednesday.**

---

## ▶️ **YOU ARE EXPECTED TO BE IN BRANCH A.**

The service exited cleanly, so the **08:15 boot happens by the ordinary path** — which is exactly
what last night's stop was for. Everything below lives in **`THURSDAY_CONTINGENCY_06-Aug-2026.md`**.
⛔ **Commands are not repeated here — run them from that file, so there is one copy.**

➡️ **Open it, run the ONE COMMAND at the top, then go to `BRANCH A CONTINUED`.**

---

## ⚠️ THE ONE THING TO KNOW BEFORE YOU START

**Steps 1 and 2 will both look fine.** That is the expected outcome, not a lucky one.

🔴🔴 **AND STEP 3 IS THE MEASUREMENT OF THE WEEK, SITTING DIRECTLY BEHIND THEM.**
⛔ **This is the easiest place in the whole sequence to stop reading** — a normal-looking morning
gives you every reason to close the laptop at step 2.

| step | what it is | how it will feel |
|---|---|---|
| **1** | confirm the boot — **the timestamp**, not `is-active` | fine |
| **2** | confirm the kill actually cleared | fine |
| **3** | 🔴 **THE T+1 CARRY** — does CHECK1's delivery skip hold once the position has left `positions()` for `holdings()`? | **this is the one** |
| **4** | did T+1 actually happen? | **tells you whether step 3 measured anything at all** |
| **5** | the boot-seed reading | routine |
| **6** | read Wednesday's census | routine |

---

## 🔴 WHY STEP 3 IS THE POINT

**It is the only question the whole of Wednesday was spent making answerable, and it can only be
asked on a T+1 morning.** Its falsifiable expectation and Wednesday's baseline were both written
**before** the fact, so it is scoreable either way:

> **Expected:** the trade stays `OPEN`, its capital stays reserved, nothing is cancelled.
> **Refuted would look like:** `CLOSED` / `CLOSED_MANUAL`, and a `RELEASE` / `RELEASE_USED` row.

⛔ **Whatever it shows, it is RECORDED, not acted on.** A refutation is a finding. ATULAUTO's
protection is the broker-side GTT and is unaffected either way.

## ⚠️ AND STEP 4 IS NOT OPTIONAL — IT VALIDATES STEP 3

If ATULAUTO is **still in `positions()`**, T+1 has not happened and **step 3 measured nothing.**
⛔ **Say exactly that. Do not record step 3 as a pass** — a skip that was never asked for is not a
skip that worked, and it is not a refutation either.

---

## 📌 IF IT IS *NOT* BRANCH A

The contingency file routes you: **B** = no boot · **C** = HALT, `ExecMainStatus=4` ·
**D** = anything else, **including `ExecMainStatus=3`**, which is now named there rather than left
as "nobody predicted this". ⛔ **Do not improvise, do not run `resume.sh`, do not touch `gtt_state`.**

---

⛔ **Nothing else is owed this morning.** The eight open delivery items are in the register
(`MASTER_PENDING_01-Aug-2026.md`, A6/A7 companion); **only the T+1 carry has a date, and it is
today.** Everything else can wait.
