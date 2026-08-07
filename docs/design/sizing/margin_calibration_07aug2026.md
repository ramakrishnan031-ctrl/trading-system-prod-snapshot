# MARGIN CALIBRATION — 07-Aug-2026 · the intraday divisor

> **Status: PREDICTION RECORDED, MEASUREMENT PENDING.**
> Parent: `sizing_thread_conclusions_06aug2026.md` §1.1a · card `FRIDAY_MORNING_07-Aug-2026.md` §4.

---

## 1. ⭐⭐ THE PREDICTION — **recorded BEFORE the measurement**

**Written 2026-08-07 10:29:27 +05:30 IST** (`Get-Date`), with the entry window `[10:00, 15:00)`
**OPEN** and **no MIS entry inspected yet.** ⛔ Nothing below §1 existed when this was written.

> ### **PREDICTION**
> **`used margin` after both exits rest will EQUAL `used margin` after the entry fills.**
> ⇒ **DIVISOR = 1** — because an exit order placed against an *existing* position requires no
> additional margin.
>
> ### **THE ALTERNATIVE, stated so the prediction can fail**
> **`used margin` RISES by roughly the position's margin again ⇒ DIVISOR = 2** — the second resting
> opposite-side order is treated as fresh.

**Why the prediction is recorded at all:** it is the difference between an experiment and a note.
A number captured with no prior commitment can be explained *after the fact* by whichever story
fits — and the 06-Aug methodological result measured exactly that split: **every prediction written
before the fact HELD; every explanation constructed after it FAILED.**

### 1.1 · 🔴 THE ASYMMETRY — why the direction matters more than the number

| if the truth is | and we assume | consequence |
|---|---|---|
| divisor **2** | **1** | ⛔ positions **DOUBLE-SIZED** against the intended cap |
| divisor **1** | **2** | merely **HALF-sized** |

🔴 **The dangerous direction is the one currently assumed.** That is what makes a passive
measurement worth the discipline.

---

## 2. CAPTURE SHEET — **four readings, each with its clock**

⛔ **PASSIVE. NO TRADE IS PLACED FOR THIS.** ⭐ If no MIS entry fires by **15:00**, the question
**ROLLS** — a legitimate outcome under the sizing thread's exit criterion **(B)**, ⛔ not a miss,
**provided the roll is RECORDED as one** (§5).

⚠️ **`used margin` is a Kite Funds reading — it is RAMA'S, not a system measurement.** The system
records the fill and the deferred `place_exits`; it does not record broker margin. Provenance is
labelled per reading.

| # | reading | moment | clock (IST) | value | source |
|---|---|---|---|---|---|
| 1 | **used margin** | BEFORE the first MIS entry fills | | | Kite Funds (Rama) |
| 1b | **available funds** | same moment as (1) | | | Kite Funds (Rama) |
| 2 | **used margin** | AFTER the entry fills, BEFORE exits are placed | | | Kite Funds (Rama) |
| 3 | **used margin** | AFTER **BOTH** exits rest — deferred `place_exits` has run | | | Kite Funds (Rama) |
| 3b | **available funds** | same moment as (3) | | | Kite Funds (Rama) |
| 4 | **position value** | qty × fill price | | | system (`trades`) |

⛔ **(3) is "after both exits are RESTING", not "after the entry fills."** The deferred
`place_exits` is the whole subject of the test; reading too early measures nothing.

---

## 3. THE FOUR FIELDS TO DOCUMENT — permanent evidence, not a note

| field | value |
|---|---|
| **observed divisor** | *(1 or 2 — the arithmetic, not the impression)* |
| **confidence** | *(what this reading CANNOT settle, stated in the same breath as what it does)* |
| **broker conditions** | *(segment · product · any other position open · time of day — ⛔ a margin reading is a reading of the WHOLE account, not of one order)* |
| **repeat required?** | *(yes/no WITH the reason. ⭐ one observation, one symbol, one day = a CALIBRATION POINT, ⛔ not a broker rule)* |

⚠️ **The `broker conditions` row is load-bearing today: DIFFNKG is a live CNC carry and ATULAUTO's
₹587.40 phantom reservation replayed at boot.** Any `used margin` read today includes both.
⇒ **the DELTA between (2) and (3) is the measurement; the ABSOLUTE figures are not.**

---

## 4. RESULT

*(pending — filled only from clock-stamped readings)*

---

## 5. ROLL RECORD

*(if no MIS entry fires by 15:00, record here: the clock, the fact that the window closed with no
MIS fill, and that the question rolls under exit criterion (B). ⭐ An unrecorded roll is
indistinguishable from forgetting.)*
