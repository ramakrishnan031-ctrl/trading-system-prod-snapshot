# MARGIN CALIBRATION — 07-Aug-2026 · the intraday divisor

> **Status: PREDICTION RECORDED (§1) · SYSTEM SIDE MEASURED (§2) · BROKER READING PENDING (§4).**
> Parent: `sizing_thread_conclusions_06aug2026.md` §1.1a · card `FRIDAY_MORNING_07-Aug-2026.md` §4.

---

## 1. ⭐⭐ THE PREDICTION — **recorded BEFORE the measurement**

**Written 2026-08-07 10:29:27 +05:30 IST** (`Get-Date`), committed `a6808dc` **before** any MIS
entry was inspected. ⛔ Nothing below §1 existed when this was written.

> ### **PREDICTION**
> **`used margin` after both exits rest will EQUAL `used margin` after the entry fills.**
> ⇒ **DIVISOR = 1** — an exit order placed against an *existing* position requires no additional
> margin.
>
> ### **THE ALTERNATIVE, stated so the prediction can fail**
> **`used margin` RISES by roughly the position's margin again ⇒ DIVISOR = 2.**

### 1.1 · 🔴 THE ASYMMETRY

| if the truth is | and we assume | consequence |
|---|---|---|
| divisor **2** | **1** | ⛔ positions **DOUBLE-SIZED** against the intended cap |
| divisor **1** | **2** | merely **HALF-sized** |

🔴 **The dangerous direction is the one currently assumed.**

---

## 2. ✅ THE SYSTEM SIDE — MEASURED 10:3x, live DB `data_store/trading_system.db`

**`LIMIT_TRIPLE` is in exactly the state the test is about: entry `COMPLETE`, SL and TGT BOTH
`OPEN` (resting).** Measured from `orders`, joined on `trades.status='OPEN'`:

| symbol | leg | product | status | placed_at |
|---|---|---|---|---|
| CARRARO | ENTRY | MIS | COMPLETE | 10:06:17.720 |
| CARRARO | **SL** | MIS | **OPEN** | **10:06:41.695** |
| CARRARO | **TGT** | MIS | **OPEN** | **10:06:41.695** |
| CROMPTON | ENTRY | MIS | COMPLETE | 10:09:14.880 |
| CROMPTON | **SL** | MIS | **OPEN** | **10:09:22.967** |
| CROMPTON | **TGT** | MIS | **OPEN** | **10:09:22.967** |
| DIFFNKG | ENTRY | CNC | COMPLETE | 06-Aug 10:02:17 |
| MANINFRA | ENTRY | CNC | COMPLETE | 10:05:23.088 |

**The four open positions and the margin the SYSTEM modelled for each:**

| symbol | product | qty | entry | `margin_reserved` | position value | ratio |
|---|---|---|---|---|---|---|
| DIFFNKG | CNC | 1 | 446.10 | **446.106** | 446.11 | 1.00 |
| MANINFRA | CNC | 4 | 115.23 | **460.916** | 460.92 | 1.00 |
| CARRARO | MIS | 1 | 510.80 | **102.162** | 510.81 | **0.20** |
| CROMPTON | MIS | 1 | 254.05 | **50.811** | 254.05 | **0.20** |
| | | | | **Σ 1,059.995** | Σ 1,671.89 | |

⭐ Both MIS trades are **SHORTS** (`sl_initial` > entry, `tgt_initial` < entry) — a broker condition
that belongs in §5, not a footnote.

### 2.1 · ⛔⛔ READINGS (1) AND (2) ARE **UNOBTAINABLE** — stated plainly, not worked around

The card's capture sheet asks for `used margin` **before the first MIS entry fills** and **after the
fill, before exits are placed.** Those moments were **10:06:17** and the ~24 s to **10:06:41**.
**The card arrived at 10:30** — ⇒ **both moments had passed by ~24 minutes.**

⛔ **They cannot be reconstructed.** Kite's Funds page reports a *current* figure; there is no
historical used-margin series. ⚠️ **And the system does not record broker margin at all** — it
records its own `margin_reserved` model, which is the thing under test and therefore cannot stand in
for it.

⇒ **The clean before/after delta is NOT available for CARRARO or CROMPTON.** What replaces it is
weaker and is labelled as such in §3.

---

## 3. THE SUBSTITUTE TEST — a STATIC comparison, ⛔ not the delta

**One `used margin` reading now discriminates, because the two hypotheses predict different totals:**

| hypothesis | arithmetic | predicted `used margin` |
|---|---|---|
| **DIVISOR 1** *(predicted)* | 446.11 + 460.92 + 102.16 + 50.81 | **≈ ₹1,060** |
| **DIVISOR 2** | the two MIS exits charge again: + (102.16 + 50.81) | **≈ ₹1,213** |

**A ₹153 gap on a ₹1,060 base.** ⭐ **Distinguishable by a single reading.**

### 3.1 · ⚠️ THE CONFOUND, stated in the same breath

This compares broker truth against **the system's own leverage model** (MIS at 0.20 = 5×). ⛔ If the
broker's real MIS margin for these symbols is not 5×, the absolute totals shift and the test
degrades. **It is not a clean experiment; it is a bounded one.**

⭐ **What limits the damage:** the **CNC portion is model-independent** — CNC is charged at full
value by definition, so **₹907.02 of the ₹1,060 carries no leverage assumption.** The entire
uncertainty lives in the **₹152.97** MIS portion, which is also exactly the quantity the divisor
doubles. ⇒ **the confound and the signal are the same size**, which is the honest way to say this
reading is *suggestive*, ⛔ **not decisive.**

### 3.2 · ⭐ THE CLEAN DELTA IS STILL AVAILABLE — the window runs to 15:00

⛔ **A third MIS entry may still fire.** If one does, the clean readings are takeable and they
**supersede §3 entirely.** The system's own entry activity today shows the pipeline is live and
attempting frequently (12 `entry_throttled` releases 10:00–10:11, 2 `slippage_exceeded`), so this is
**not a remote possibility.**

⛔ **STILL PASSIVE. NO TRADE IS PLACED FOR THIS.**

---

## 4. CAPTURE SHEET — **broker readings, PENDING**

⚠️ **`used margin` is a Kite Funds reading — RAMA'S, ⛔ not a system measurement.**

### 4a · the static reading (takeable NOW, while CARRARO + CROMPTON are both open)

| # | reading | clock (IST) | value |
|---|---|---|---|
| A | **used margin** (Funds) | | |
| B | **available funds** | | |
| C | per-position margin, if the positions page shows it | | |

⏰ **Time-critical: both MIS positions have SL and TGT resting. If either fires, the state changes
and this reading is gone.**

### 4b · the clean delta (only if a THIRD MIS entry fires before 15:00)

| # | reading | moment | clock | value |
|---|---|---|---|---|
| 1 | used margin | BEFORE the entry fills | | |
| 2 | used margin | AFTER the fill, BEFORE exits placed | | |
| 3 | used margin | AFTER **BOTH** exits rest | | |

⛔ **(3) is "after both exits are RESTING", not "after the entry fills."** Reading too early measures
nothing. ⭐ On today's evidence the gap between fill and exits-resting is **~8–24 seconds**
(10:06:17→10:06:41; 10:09:14→10:09:22) — **narrow, and it is why (2) was missed.**

---

## 5. THE FOUR FIELDS — to be completed from the readings

| field | value |
|---|---|
| **observed divisor** | *(pending)* |
| **confidence** | ⚠️ **already bounded, whatever the reading says:** §3.1's confound means a static reading is **suggestive, not decisive**. Only §4b's delta settles it. |
| **broker conditions** | **07-Aug-2026, NSE equity, mid-session.** ⛔ **NOT a clean single-position account:** 2 CNC positions open (DIFFNKG carried from 06-Aug; MANINFRA opened 10:06 today) **plus** 2 MIS positions, **both SHORT**. A margin reading today is a reading of **all four**. |
| **repeat required?** | ✅ **YES.** One observation, one day, on an account holding three unrelated positions, via a *static* comparison rather than a delta. ⭐ A **calibration point**, ⛔ not a broker rule. |

---

## 6. 🔴🔴 THE "CLOSED BY SOURCE" ARGUMENT IS **REFUTED** — ⛔ CRITERION (B) STAYS OPEN

**A 07-Aug 11:00 card proposed closing this question by source, on the premise:** *"`place_exits`
calls `_place_sl` and RETURNS. NO TGT ORDER IS EVER PLACED ON THE MIS PATH"* ⇒ no second resting
order ⇒ the divisor question is moot ⇒ divisor 1 everywhere.

⛔⛔ **THE PREMISE IS FALSE, AND IT WAS ATTRIBUTED TO ME. I DID NOT FIND IT — I MEASURED THE
OPPOSITE, 30 MINUTES EARLIER** *(§2's leg table: both TGT legs `OPEN`)*.

### 6.1 · **(S)** THE SOURCE — the early return is a **CONDITIONAL BRANCH**, not the path

`orders/order_protocol_limit.py::place_exits` *(read at HEAD `ad303e4`)*:

| step | line | behaviour |
|---|---|---|
| Step 1 — SL | `:381` | placed **always** |
| **the early return** | **`:452-463`** | ⛔ **guarded by `if tgt_unplaceable:`** — the NOCIL circuit-band gate. Returns `_partial_sl_only()` and hands the TGT to `TGTRetryManager` |
| **Step 2 — TGT LIMIT** | **`:465-474`** | **`self._adapter.place_order(..., order_type="LIMIT")` — the ORDINARY path** |

⇒ **One branch, generalised to the whole path.**

### 6.2 · **(P)** PRODUCTION — refuted 220 times over

| measure | value |
|---|---|
| **MIS `TGT` legs ever placed** | **220** — ⭐ **all 220 carry a broker `order_id`** |
| MIS `SL` legs | 232 |
| CNC `SL` / `TGT` legs | **0 / 0** — delivery exits via GTT, as designed |
| **MIS trades with an SL and NO TGT** | **1** — `THELEELA`, 19-Jun-2026 |
| today's resting TGTs | CARRARO `260807170241188` · CROMPTON `260807170247310`, both `OPEN` |

⇒ **the `tgt_unplaceable` branch has fired ONCE in the campaign.** The card describes a real path
that fires ~0.5 % of the time and states it as 100 %.

### 6.3 · ⇒ THE DOWNSTREAM CLAIMS DO NOT FOLLOW

| claim | verdict |
|---|---|
| *"divisor = 1 for both pipelines, closed by source"* | ⛔ **NOT CLOSED.** Two exit orders **do** rest on the MIS path ⇒ the original question is **LIVE** |
| *"`LIMIT_TRIPLE` is a misnomer, there is no third leg"* | ⛔ **FALSE.** ENTRY + SL + TGT, all three with broker order IDs. **The name is honoured** |
| *"close exit criterion (B)"* | ⛔⛔ **MUST NOT.** Closing on a refuted premise is `V5` exactly — **manufactured confidence**, and the worse half of it: a missing check leaves you uncertain, a tautological one leaves you **wrongly certain** |
| *"`tgt_retry.*` inert on both paths?"* | ⚠️ **NOT inert — DORMANT.** See §6.4 |

### 6.4 · §2.3 ANSWERED — `tgt_retry.*` is **DORMANT, ⛔ not inert**, and that is a different thing

**(S)** wired and started — `main.py:2788` constructs `TGTRetryManager`, `:3527` starts it; it is the
declared handler for **both** the `:452` band branch **and** FIX-190 Bug C (broker-side TGT
rejection). ⇒ ⛔ **it is REACHABLE on the intraday path, so "inert" is the wrong word.**
**(P)** across **555 trades: `needs_tgt_retry` = 0 · `tgt_retry_count` = 0 · max 0.**
⇒ 🏷️ **BUILT AND NEVER RUN** — the campaign's named class. ⭐ **And the one trade that needed it
(`THELEELA`, 19-Jun) records no retry**, which is the gap worth carding: **the branch fired and the
handler shows nothing.** ⛔ **Disclosed, NOT chased (`G3`)** — it is outside this doc's subject.

### 6.5 · ⭐ WHAT THE REFUTATION COSTS, STATED HONESTLY

**The static reading in §3/§4a is now the ONLY live read on the divisor** — the "closed by source"
route is gone, and §2.1's readings remain unobtainable. ⇒ **the prediction in §1 stands UNRESOLVED**,
and 🔴 **the dangerous direction is still the one currently assumed.**

⚠️ 🏷️ **AND THE SHAPE IS WORTH RECORDING: the card diagnoses this exact error in `§1.4` — *"a
conclusion from three samples, stated as general"* — and commits it in `§2` in the same breath.**
⭐ **Not a criticism; a demonstration that naming a failure mode does not immunise against it**, which
is the argument for checks that run rather than rules that are written *(`G7.1`)*.

### 6.6 · ⛔ THE 11:20 CARD DOUBLED DOWN — **"No TGT resting order exists on either path"** — refuted in the PRESENT TENSE

**(P) measured 11:2x, live DB:** **CROMPTON's TGT `260807170247310` is `OPEN` — resting at the
broker at this minute.** CARRARO's TGT `260807170241188` is `CANCELLED` **because the trade CLOSED**
— `SL_HIT` at 11:15:14, exit 519.55, net −9.31, `closure_source OWN_SL`; the bracket peer is
cancelled on an SL fill, which is the design, ⛔ not an absence. **220 all-time MIS TGT legs stand.**
⇒ **Criterion (B) remains OPEN.** ⚠️ The static discriminator WEAKENED with CARRARO's close: one MIS
position left, so the divisor-2 increment is only **CROMPTON's ₹50.81** — and whether a **carried**
CNC holding (DIFFNKG) appears inside Kite's `used margin` at all is a presentation unknown this
reading cannot settle. **The clean §4b delta on a third MIS entry remains the decisive instrument.**

### 6.7 · ✅ `LIMIT_TRIPLE` — the name at source; the retraction was right, its reason is not

The card withdrew its "misnomer" claim on the ground that the name means *"the ENTRY order type
(LIMIT, 3 retry attempts), nothing to do with three legs."* **(S)** the module docstring,
`order_protocol_limit.py:5-8`: *"LIMIT_TRIPLE order protocol. Two-phase placement: Phase 1 (execute):
ENTRY LIMIT … Phase 2 (place_exits): SL + TGT"*; and the pre-fix history `:13-17`: *"the protocol
placed **ENTRY + SL + TGT** in sequence."* ⇒ **TRIPLE = the three legs.** ⛔ **"3 retry attempts"
appears nowhere in the file** — every `attempt` hit (7, whole file) is "no TGT attempted" /
retry-manager scheduling. Corroborated: `order_placer.py:1651` — *"TGT leg — both LIMIT_TRIPLE and
CO_PLUS_TGT place a separate TGT order."*

### 6.8 · ⛔ `tgt_retry.*` "CLASSIFIED BACKWARDS" — **refused with the consumer inventory; the four rows STAND**

The card: *"Four keys · three consumers · ALL on the CNC/GTT path · ZERO intraday consumers"* ⇒ the
`INTENTIONALLY UNAVAILABLE` classification is inverted. **(S) measured, repo-wide over `*.py`,
tests excluded — the claim is itself the inversion:**

| role | site | path |
|---|---|---|
| sets `needs_tgt_retry` | `order_placer.py:2619` *(FIX-190 Bug C)* | **LIMIT_TRIPLE / intraday** |
| sets (recovery) | `order_reconciler.py:4194` | **LIMIT_TRIPLE / intraday** |
| consumes + clears | `tgt_retry_manager.py:330/:348/:358` | **LIMIT_TRIPLE / intraday** |
| scheduling view | `state_store.py:1556-1586` *(`:1503`: "A LIMIT_TRIPLE trade whose SL is live but whose TGT could not be placed")* | **LIMIT_TRIPLE / intraday** |
| passive readers | `daily_trade_review.py:480` · `preflight/checks/state.py:140-152` | reporting |
| **any CNC/GTT file** | **0 hits** | — |

⭐ **And the classification was made BY MECHANISM, not by name** — `delivery_config_surface:330`'s own
recorded reason: *"(P) the delivery order set is ONE row, `leg=ENTRY`, `product=CNC` — zero SL rows,
zero TGT rows; protection is a broker-side GTT."* ⇒ **no inversion occurred ⇒ the proposed sweep of
the 42-key bucket loses its premise and is NOT run** *(the bottleneck is governance; a sweep on a
refuted exemplar is review work manufacturing itself)*. **Bucket counts: UNCHANGED; no percentage
quoted.**

## 7. RESULT

*(pending — filled only from clock-stamped readings)*

---

## 7. ROLL RECORD

⛔ **The question has NOT rolled.** Two MIS entries fired (10:06:41, 10:09:22) — exit criterion (B)'s
"observation completed" branch is **live**, not its "explicitly ROLLED" branch. ⚠️ **But what fired
was the OPPORTUNITY, not the OBSERVATION:** the readings that matter were missed by ~24 minutes.

⇒ 🏷️ **A THIRD STATE the criterion does not name: *the event occurred and the measurement was
missed.*** ⛔ That is neither "completed" nor "rolled", and recording it as either would be false.
**Recorded here as its own outcome.**
