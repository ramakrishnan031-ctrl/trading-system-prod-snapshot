# ADDENDUM — THE CAPITAL-DRIFT CRITICALs OF 05-Aug-2026

> ## 🧊 **THIS IS A SEPARATE SHEET. THE EVENING OPERATOR CARD IS FROZEN AND ITS COMMANDS ARE NOT TOUCHED.**
> *(Its change-log was corrected to REVISION 5 on 05-Aug — ⛔ change-log only; not one command altered.)*
> ⛔ **Nothing here duplicates a command from that card.** §2 below **reuses the capture that card's §1a
> already makes** — do not run a second whole-log capture.

---

## 🟢 READ THIS FIRST — **NO MONEY HAS LEFT THE ACCOUNT, AND THE ALERT CANNOT KILL ANYTHING**

Three `CRITICAL — Capital Drift Detected` emails arrived today (first ~10:01, latest **11:51:20**).
On flip day, with real delivery positions held, a CRITICAL reads as an emergency. **Two things are
measured and settled:**

**1. 🔴 THE ALERT CANNOT TRIGGER A KILL — VERIFIED IN SOURCE AT THE DEPLOYED SHA.**
The reconciler publishes this event tagged `source_module="order_reconciler"`
(`orders/order_reconciler.py:3667`). The escalation handler only ever escalates three sources —
`fund_manager`, `fund_manager_self_check`, `fund_manager_bucket_overflow`
(`capital/drift_handler.py:66-70`) — and **`order_reconciler` is not one of them.** On a
non-escalating source it writes one INFO line and **returns immediately**, before any tier
calculation, before any counter, before `soft_kill` or `hard_kill` (`:147-161`).
⇒ **This alert is informational by construction. It has no path to stopping trading.**

**2. THE BROKER'S OWN PAGE BALANCES.** On the 12:56 Funds screenshot,
**available `9,202.37` + used `681.33` = `9,883.70` = the opening balance**, and payin/payout are
`0.00`. **Nothing has left the account.**
⚠️ **BUT THAT IS TRUE OF A SCREENSHOT, AT ONE MOMENT.** It is **not** a substitute for §1 below, and
⛔ **it cannot be compared against the 11:51 alert** — those are **different times**, and the numbers
move during the day.

**What the alert is actually reporting** — measured, not assumed: it compares **your total capital**
against **the broker's free cash**. Money you have *deployed into a position* is missing from the
second number and present in the first, so the two legitimately differ **by roughly whatever is
currently blocked in positions.** That is a bookkeeping difference, not a loss. **§3 is how you
confirm it rather than take my word for it.**

---

## §1 — ⏰ **TIME-SENSITIVE. DO THIS FIRST, AND AGAIN AFTER 15:30.**

> ### ⭐⭐ **THIS IS THE NUMBER THE SYSTEM THROWS AWAY.**
> The broker's margin endpoint is called **~2,225 times a day and its RESPONSE VALUE IS PERSISTED
> ZERO TIMES** — only the method name and a duration. ⇒ **what the broker actually had blocked while
> today's positions were open is GONE tomorrow and cannot be reconstructed.** Today is the first day
> it would explain a live CRITICAL. **Writing it down by hand is the only way to keep it.**

**Open Kite → Funds → Equity. Write down these four numbers, with the clock time:**

| reading | time | available margin | used margin | opening balance |
|---|---|---|---|---|
| **A — now** | ____:____ | ____________ | ____________ | ____________ |
| **B — after 15:30** | ____:____ | ____________ | ____________ | ____________ |

⭐ **IF YOU ALREADY TOOK A FUNDS SCREENSHOT EARLIER TODAY, THAT *IS* READING A** — write its four
numbers and its clock time into the row above. ⛔ **Do not retake it.** **An earlier reading, taken
while the intraday book was still open, is MORE useful than a later one** — it is the only kind that
can be compared against a mid-session alert. A 19:00 reading cannot be.

**A screenshot is fine. The numbers typed out are better** — a screenshot cannot be searched next
month.

**Sanity check on each reading:** does **available + used = opening**?
- **Yes** → the broker's own books balance at that moment. ✅
- 🔴 **No** → **that is a finding.** Write down all three numbers and stop.

> ### ⭐⭐ **READING B IS NOT "ANOTHER DATA POINT". IT IS THE DELIVERY-ONLY FIGURE — AND IT IS THE ONE CROSS-CHECK TODAY THAT CAN ACTUALLY GO RED.**
> **After the 15:15/15:17 squareoff the intraday book is flat.** Whatever `used margin` is still
> showing after that is **the CNC block alone** — and delivery runs at **1×**, so the blocked amount
> is **roughly the full purchase value of what you are holding.**
> ⇒ 🔴 **COMPARE IT WITH THE TOTAL CNC VALUE THE OPERATOR CARD ALREADY READS OUT OF THE DATABASE —
> see the operator card, §2 (A).** ⛔ **Do not run a new query; that card already computes it.**
>
> | | figure | where it comes from |
> |---|---|---|
> | **1** | `used margin` in **reading B** | the **broker**, after the squareoff |
> | **2** | total CNC value | the **database** — operator card §2 (A) |
> | **3** | the CNC positions total shown on Kite's own **Positions/Holdings** page | the **broker**, a second way |
>
> ⭐ **1 and 2 come from two entirely different systems with no shared term, so a disagreement is
> real information — unlike an identity that rearranges the same numbers.**
> - **1 ≈ 2** → ✅ the broker's block and your books agree on what is deployed. **Strong confirmation.**
> - 🔴 **1 and 2 differ materially** → **use 3 as the tie-breaker:** whichever of 1 or 2 it agrees
>   with is the sound one, and **the other is the finding.** Write down all three.
> ⚠️ **BOTH READINGS MUST BE TAKEN CLOSE IN TIME, AND BOTH AFTER THE SQUAREOFF.** Reading B at 15:35
> against a database figure read at 19:00 is a comparison that should not have been made — the same
> timing trap as §3 below.
>
> ### ⚠️ **THE SAME INFORMATION MAY APPEAR IN EITHER OF TWO PLACES — CHECK BOTH BEFORE CALLING IT A FINDING**
> A delivery purchase is a **cash debit**, not a margin block. Kite may therefore show it **either**
> way, and ⛔ **which one this account uses has NOT been verified — it cannot be settled from the PC,
> and reading B is the measurement that settles it.**
>
> | shape | what you will see after the squareoff | **the delivery figure is** |
> |---|---|---|
> | **1 — blocked as margin** | `used margin` stays **high**, `available` low | **`used margin`** |
> | **2 — taken as a debit** | `used margin` ≈ **0**, `available` ≈ `opening − CNC value` | **`opening − available`** |
>
> ⭐⭐ **IN BOTH SHAPES `available + used = opening` STILL HOLDS.** ⇒ **that identity is the invariant,
> and it breaking is what would actually be a finding** — not which of the two shapes you see.
> 🔴 **So if `used margin` is ~0, do NOT record a discrepancy.** Read the delivery figure as
> `opening − available` instead and carry on with the comparison below. ⛔ **The cross-check against
> the operator card's §2 (A) database total is UNCHANGED — only the field you read it from moves.**
> 📌 **Whichever shape it turns out to be, write it down once** — it is worth recording permanently
> and never re-deriving.

---

## §2 — COUNT TODAY'S DRIFT ALERTS

⛔ **Do NOT capture the log again.** The operator card's §1a already copies the whole day's
`system_2026-08-05.log`. **Use that file** *(available after 17:40, when that capture runs)*:

```bash
ssh trading-vm 'grep "G3 CAPITAL_DRIFT" ~/census_system_2026-08-05.log'
```

**What you should see:** one line per alert, each carrying `expected=`, `actual=`, `delta=`,
`tolerance=` and `base=`. *(The emitter is `orders/order_reconciler.py:3653-3659` — the grep string
is taken from that line, not from memory.)*

**Write down, for each:** the time, `expected`, `actual`, `delta`, `tolerance`.

⚠️ **HOW MANY TO EXPECT, AND WHY SILENCE IS AMBIGUOUS.** Repeat alerts are **throttled to at most one
per 30 minutes** (`capital_drift_alert_interval_sec: 1800`, `system_config.yaml:371`; logic at
`_should_alert_capital_drift`). ⛔ **Three alerts between 10:01 and 11:51 is consistent with that
throttle — it is NOT evidence of three separate problems.**
🔴 **AND SILENCE AFTER 11:51 HAS THREE POSSIBLE CAUSES, NOT ONE. Do not conflate them:**
1. still inside the 30-minute window;
2. **the drift came back within tolerance** — which *resets* the throttle (`:3636-3643`), so the next
   genuine drift would alert immediately;
3. ⛔ **the check stopped running** (the service died).
**§4 is what separates (3) from the other two.**

---

## §3 — THE CHECK THAT CAN ACTUALLY FAIL

> ### ⛔⛔ FIRST, A CHECK THAT **CANNOT** FAIL — AND IT IS NOT ON THIS SHEET, DELIBERATELY
> It was proposed that you verify:
> `delta == (opening − actual) + (expected − opening)`.
> **That identity is ALWAYS true.** The `opening` term cancels algebraically — it reduces to
> `delta == expected − actual`, which is simply the definition of `delta`. **It would "close to the
> paisa" for ANY value of `opening`**, including a wrong one. ⇒ **it proves nothing, and a check that
> cannot go red is not a check.** *(Same rule that removed a query from the operator card earlier
> today.)*

### ✅ THE CHECK THAT CAN GO RED — compare against something INDEPENDENT

**Take your §1 reading A** *(or B — but use ONE reading and the alert closest to it in time)*, and
compare it with the alert line closest to that time from §2:

**(i) Does `opening − actual` ≈ the broker's `used margin` at the same moment?**
```
   opening balance  −  alert's `actual`     =   ____________
   the broker's `used margin` (§1)          =   ____________
```
- **≈ equal** → ✅ the gap is deployed capital. **Explanation holds.**
- 🔴 **materially different** → **THAT is the finding.** Write both numbers down.

**(ii) Does `expected − opening` ≈ today's realised profit/loss?**
```
   alert's `expected`  −  opening balance   =   ____________
```
- A **small** number in the tens of rupees, matching the day's booked P&L → ✅ expected.
- 🔴 A **large** number, or one that does not match the day's P&L → **that is the finding.**

⛔⛔ **TIMING WARNING — this is the easiest mistake to make here:** the alert values are from
**11:51**; a Funds reading taken at **12:56** is a **different moment** and the used margin moves as
positions open and close. ⇒ **Compare readings that are close in time, and write the clock time
beside every number.** A mismatch between two different times is **not** a finding — it is a
comparison that should not have been made.

---

## §4 — 🔴 CONFIRM NO KILL FIRED TODAY

**The durable record is the database, not the log** — the kill state is persisted to a single-row
table (`kill_switch_state`, `core/schema.sql:563-570`):

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT state, reason, triggered_at, triggered_by FROM kill_switch_state;"'
```

> ## ⛔⛔ **READ THIS BEFORE THE TABLE — OTHERWISE YOU WILL READ THE NORMAL RESULT AS AN EMERGENCY.**
> **You are running this in the EVENING. The routine 15:15 circuit-breaker fires EVERY TRADING DAY,
> so at 19:00 the row will almost certainly say `SOFT_KILL` WITH TODAY'S DATE.** ⭐ **THAT IS THE
> EXPECTED RESULT, NOT A PROBLEM.**
> ⇒ **THE DATE DOES NOT TELL YOU ANYTHING TONIGHT. THE `reason` AND THE `triggered_at` TIME DO.**
> *(An earlier draft of this sheet used the date as the discriminator — it would have told you to
> stop, on the ordinary daily breaker, in the middle of a real-money gate. It is corrected here.)*

**What you should see:** one row. **Match the `reason` text, do not judge intent:**

| row | meaning | what to do |
|---|---|---|
| `SOFT_KILL` · `triggered_at` ≈ **15:15 today** · `reason` = **`circuit_breaker_force_close_15:15`** | ✅ **ROUTINE. Every single trading day.** | ⛔ **Nothing.** Record it and move on. |
| `SOFT_KILL` · today · **any other time**, or **any other `reason`** | 🔴 **THAT is the finding** | Read the reason, write it down verbatim, stop. |
| `SOFT_KILL` · `reason` = `EOD_SQUAREOFF` | ✅ also a scheduled reason | Record it. Not an incident. |
| `INACTIVE` | ⚠️ **see the note below — this is NOT automatically good at 19:00** | Read the note. |
| **`HARD_KILL`** · any time · any reason | 🔴🔴 **STOP.** | **It has never fired in this system's life. It would be the first.** Record everything and stop. |

*(The two routine reason strings are `circuit_breaker_force_close_15:15` and `EOD_SQUAREOFF` —
quoted verbatim from `capital/kill_switch.py:120-123`, where they are defined as the kills that are
"part of normal daily operations". The first is written at `main.py:695`.)*

> ### ⚠️ **AND `INACTIVE` AT 19:00 IS NOT OBVIOUSLY GOOD — BOTH ANSWERS HAVE TO BE INTERPRETABLE**
> The breaker fires at 15:15 **every** trading day. So an `INACTIVE` row at 19:00 means **either**:
> **(a)** the breaker did not fire today — 🔴 **which is itself worth recording**, because something
> that runs daily did not; **or**
> **(b)** something cleared it after 15:15 — 🔴 **also worth recording**, because nothing routine
> does that in the evening *(the automatic clear happens at the NEXT morning's boot, not tonight)*.
> ⇒ **Write down which one you think it is, and the `reason` text, either way.** A check whose PASS
> you cannot interpret is only half a check.

> ### ✅ **AND THE THING NOT TO WORRY ABOUT TONIGHT, MEASURED SO YOU DO NOT HAVE TO WONDER**
> **Tomorrow's 08:15 boot WILL clear tonight's breaker automatically, even though CNC positions are
> held.** The auto-clear (`kill_switch.py:287-335`) turns on **ONE thing: was the kill triggered on a
> PREVIOUS calendar day.** ⛔ **It does not look at open positions, and it does not look at the
> reason** — its own words: *"a new trading day ALWAYS starts with a clean slate — EVERY prior-day
> kill is cleared regardless of type… The system never blocks the next-day startup."*
> ⛔⛔ **SO DO NOT RUN `deploy/resume.sh` TONIGHT.** It begins with `systemctl stop`, and nothing here
> calls for it.

*(Belt and braces — the log side. `soft_kill` writes a CRITICAL after persisting, so this should be
empty if the row above says `INACTIVE`:)*
```bash
ssh trading-vm 'grep -iE "SOFT_KILL|HARD_KILL" ~/census_system_2026-08-05.log | head -20'
```
⚠️ **Expect some hits even on a clean day** — the words appear in ordinary startup and status lines.
**The database row above is the authority; this is only a cross-check.**

---

## §4b — ⏰ **TOMORROW MORNING: RECORD THE BOOT SEED. THIS ONE EXPIRES AT 08:15.**

> ### ⭐ WHY — and why it is worth two minutes
> Every morning the system **seeds its capital from the broker** at the 08:15 boot. That seed is
> written as an `INIT` row — ⛔ **it is not a drift event, it has no comparison of any kind, and
> NO CHECK ANYWHERE COMPARES ONE DAY'S SEED WITH THE PREVIOUS DAY'S.**
> That is not a guess: it is the register's own measurement, and it is how a **three-session capital
> movement in July went unalerted** — it was absorbed by the seed. *(The figure and its dates are on
> record in `MASTER_PENDING` §B#5; ⛔ cited, not retyped here.)*
> ⇒ 🔴 **Tomorrow's seed will come in LOWER than today's, by roughly whatever is blocked in the CNC
> positions — and nothing will flag it.**
> ⭐⭐ **What makes today different from July: for the first time we can say that BEFORE it happens.**
> A prediction written in advance can be scored. *(Registered as `IA-P6-02`'s first live instance —
> `MASTER_PENDING` §B#5.)*

**THE PREDICTION, written before the fact:**
> **`tomorrow's seed ≈ today's seed − the CNC block ± settled P&L`**

**Today's seed is already on record** — it is the `broker.net` figure in the register's currency
block for the 05-Aug 08:15 boot (`docs/MASTER_PENDING_01-Aug-2026.md`). ⛔ **Cite it from there; do
not copy a number out of an email or a chat message.**

**THURSDAY MORNING, after 08:20, write down the new seed:**

| | date | boot seed (`broker.net` at 08:15) |
|---|---|---|
| today | 2026-08-05 | *(on record — see the register currency block)* |
| tomorrow | 2026-08-06 | ____________ |

**Then check the prediction:**
- **The drop ≈ the CNC block** (from §1 reading B) → ✅ **the prediction held.** Record it and move on.
- ⚠️ **The drop is close but not exact** → **expected, and NOT a finding.** T+1 settlement means the
  broker credits and debits on **its own schedule**, so small differences are normal.
- 🔴 **A finding is a mismatch you cannot explain by settlement** — as a rule of thumb, a gap
  **larger than the day's total realised P&L**, or one in the **wrong direction** (the seed going
  *up* while positions are held). **Write the two seeds and the block down; do not act.**

> ⛔⛔ **THIS IS AN OBSERVATION WITH A DATE. IT IS NOT A RECONCILIATION.**
> **Nothing is fixed. Nothing is reconciled. Nobody edits a ledger.** The value of this is entirely
> in having written the prediction down *before* the number arrived.

⚠️ **ONE COROBBORATION, worth a single line:** if the CNC block turns out to be a **small fraction**
of the delivery bucket, that is **consistent with the ~7% buying-power utilisation the register has
already measured** (the concentration cap combined with the permanently-low tier multiplier).
⛔ **Record the observation only — do not re-derive that figure, and do not open the sizing thread.**

---

## §4c — 🔴🔴 **THE PREDICTION, SHARPENED AT SOURCE 05-Aug 23:2x — ⛔ WRITTEN BEFORE THE BOOT**

**(S) THE SEED FORMULA, exact** — `main.py:1728-1732`:
```python
def compute_live_seed(broker_adapter, fund_manager, start_of_today_iso=None) -> float:
    return broker_adapter.get_margins().net - fund_manager.today_realized_pnl_carryover(start_of_today_iso)
```
⭐ **On Thursday at 08:15 the carryover term is ZERO** — it sums *Thursday's* `RELEASE_USED` rows and
no trade has closed yet. ⇒ **the seed is `broker.net`, full stop.**

> ### 📌 THE PREDICTION — scoreable because it is written first
> **The system will start Thursday believing its total capital is ~₹587 LOWER than what it actually
> controls** — because that value is sitting in **stock, not cash** — **and nothing will flag it**,
> since `initialize()` (`capital/fund_manager.py:417-457`) performs **no comparison of any kind**
> *(verified: it writes `balance_before=0.0` and sets the buckets; no threshold, no drift event)*.
> ⇒ **BOTH buckets are sized off that shrunken total.**

**Expressed as ratios — ⛔ `₹10,000` is a TESTING value and no rupee figure here is a target:**

| quantity | Wednesday 05-Aug | Thursday 06-Aug (predicted) |
|---|---|---|
| seed (`broker.net`) | **9,883.70** | **≈ 9,296** *(± overnight settlement of the 44.61)* |
| **seed ratio Thu/Wed** | — | **≈ 0.940** |
| **shrinkage** | — | **≈ 5.94 %** = **587.40 / 9,883.70** = *the carried position's own share of the base* |
| intraday bucket (70 %) | ≈ 6,918.59 | ≈ 6,507 — **same 0.940 ratio** |
| positional bucket (30 %) | ≈ 2,965.11 | ≈ 2,789 — **same 0.940 ratio** |

⚠️ **The split is a FIXED 70/30** (`conditional_allocation_enabled: false`, verified on the VM) ⇒
**both buckets shrink by exactly the same fraction. Neither is protected.**

### ⭐ §3.2 — THE STRUCTURAL CONSEQUENCE, STATED ONCE
> **Every carried position shrinks the next day's capital base by its own raw value, and the
> delivery bucket by its share of that.**

🏷️ **CLASSIFICATION: `(d)` — UNDETERMINED.** ⛔ **I am not classifying this (a) or (b), and the
source does not settle it.** The two readings are both coherent:
- **correct-by-design** — the cash genuinely is not deployable while it sits in the share, so sizing
  off `broker.net` is the *conservative* and arguably right thing;
- **a defect** — the system then **under-counts what it owns**, and a book with several carried
  positions would compound the shrinkage every day it holds them.
⛔ **That is a DESIGN QUESTION and it is not tonight's.** ⭐ What tonight establishes is only that the
behaviour is **real, predicted in advance, and silent.**

### ⭐ §3.3 — THE FOLLOW-ON NOBODY HAS ASKED — **recorded, ⛔ not chased**
**When the carried position eventually SELLS, the proceeds arrive as capital the seed never
counted.** The same-day exit path worked on 05-Aug; **across a boot boundary it is UNTESTED.**
⇒ 🔴 **That is the carry's SECOND unverified half, and it does NOT resolve on Thursday** — Thursday
scores the *hold*; only a *sale* scores this. ⛔ **Named so it is not rediscovered. Nothing started.**

---

## §6 — ✅ **RESOLVED 05-Aug-2026 23:2x — THE MECHANISM IS RAMA'S, AND EVERY TERM NOW HAS ITS OWN MEASUREMENT**

### 📌 CREDIT, STATED FIRST
⭐⭐ **The mechanism is RAMA'S, in his words:** *"the broker deducts the CNC purchase value from free
cash; the system does not. In MIS this never shows, because everything is squared off by 15:30 and
the two reconverge."*
⭐ **The second half is the part that turns this from a bug report into a classification** — it is
what makes the drift **structurally a DELIVERY-ONLY phenomenon** rather than something new.
⚠️ **One refinement, and it is MINE, not his:** he wrote *purchase value = scrip price + brokerage &
taxes*. **The blocked figure is the RAW scrip value — costs are not in it** (measured below).

### (P) THE ALERT, VERBATIM FROM PRODUCTION — `order_reconciler`, 22:19:13.993
```
G3 CAPITAL_DRIFT: expected=9928.31 actual=9296.30 delta=632.01 tolerance=50.00 (base=50.00 human_orders=none)
```

### (S) WHAT THE CODE ACTUALLY COMPARES — `orders/order_reconciler.py:3590-3592`
```python
expected = snapshot.total     # the system's TOTAL capital
actual   = margins.net        # the broker's FREE CASH
delta    = abs(actual - expected)
```
⇒ ✅ **RAMA'S MECHANISM CONFIRMED AT SOURCE.** The system keeps the purchase inside its *total*
(moved avail → used); the broker removes it from *net*. **In MIS the position closes by 15:30, `used`
returns to zero, and the two reconverge — which is exactly why five months of intraday trading never
showed this.**

### ✅ EVERY TERM, MEASURED INDEPENDENTLY — ⛔ not taken from the delta

| term | value | how it was measured — **(P)** |
|---|---|---|
| **opening** | **9,883.70** | `fm_ledger` `INIT` row, ledger_id 10019, ts `08:15:15.181` |
| **realised P&L** | **44.61** | **TWO independent paths:** 7 `RELEASE_USED` rows sum `+44.61`; **and** 7 trades `CLOSED` today sum `net_pnl = 44.61` — **same 7 trades** |
| **CNC purchase** | **587.40** | `trades`: `qty_filled 1 × entry_actual_price 587.40` = **raw scrip value** |
| `expected` | 9,928.31 | = 9,883.70 + 44.61 ✅ closes |
| `actual` | 9,296.30 | = 9,883.70 − 587.40 ✅ closes |
| `delta` | 632.01 | = 587.40 + 44.61 ✅ closes |

> ### ⛔⛔ **AND THE THING THAT MUST BE SAID, BECAUSE THIS CAMPAIGN ALREADY DELETED A CLAIM OF THIS EXACT SHAPE (V5):**
> Written as `delta = (opening − actual) + (expected − opening)`, this identity is **VACUOUS** —
> `opening` cancels and it closes for any values. **That version was refuted earlier today and
> deleted.**
> ⭐⭐ **THIS VERSION IS NOT THAT.** Neither `587.40` nor `44.61` was obtained by subtraction from the
> alert. **Both came from independent subsystems** — the trade record and the P&L ledger — and were
> then found to reconstruct the delta. ⭐ **It could have gone red:** had the trade value been 590, or
> the realised P&L 40, the sum would not be 632.01. **That is the difference between a check and an
> identity, and it is the whole reason the terms were measured separately.**

### ⚠️ WHAT I COULD **NOT** VERIFY — stated, not glossed
🔴 **`actual = opening − CNC purchase` is arithmetic against a BROKER figure I cannot audit.** I
confirmed `actual = 9,296.30` **(P)** from the alert, and that `587.40` matches the trade record
**(P)**. **But the claim that Kite's own `available + used` decomposes to `9,296.30 + 587.40`
comes from a screenshot, not from a measurement I took.** ⛔ It is consistent; it is not independently
established here.
⭐ **The cost question is settled in ONE direction only:** the 7 closed trades' `gross 49.36 −
charges 4.75 = net 44.61` ⇒ **their** costs are already inside the 44.61. ⛔ **ATULAUTO's own purchase
costs are NOT** — it is unsold, so nothing is booked. **The contract note remains the only thing that
settles that, exactly as the card said.**

### ⚠️ A FALSE ZERO I PRODUCED AND CAUGHT — recorded because it nearly refuted a correct finding
My **first** query for realised P&L summed `pnl_delta` over *all* non-zero rows and returned
**`0.0`**. Had I stopped there I would have reported *"realised P&L is 0 — the decomposition fails."*
**The 8th row is a `RESET_PNL` of `−44.61`** which cancels the seven `RELEASE_USED` rows **by
design**. ⇒ **the filter was wider than the subject.** ⭐ **Second false zero of the night from the
same cause; both were caught only because a control was run beside the zero.**

---

## §7 — 🔴 **AR9 — THE OVERNIGHT SCOPE GAP, IN PRODUCTION, EXACTLY AS REGISTERED**

**(P)** `tolerance=50.00 (base=50.00)` — the **flat overnight band**, not the in-session percentage
band. **Delta 632.01 ÷ 50.00 = 12.64×.**
**(S)** `order_reconciler.py:3626-3631`: the percentage widening is applied **during market hours
only** (`max(Rs, expected × pct)`); **outside hours the flat ₹ tolerance stands.**

⭐⭐ **THIS IS AR9's REGISTERED SCOPE GAP ARRIVING EXACTLY WHERE IT WAS PREDICTED: the acceptance
scored the IN-SESSION regime, and overnight is the ONLY regime in which a delivery position can
exist.** ⇒ the one band that was never scored is the only one delivery ever meets.

⛔ **Recorded as an INSTANCE against AR9. ⛔ The acceptance is NOT widened. ⛔ No tolerance is
proposed, and no config value appears anywhere in this section.**

---

## §8 — ⚠️ **THE ₹4.36 — REMAINS `(d)`. THE TEST COULD NOT BE RUN.**

**The system holds no closing price for ATULAUTO.** *(Width, with its control: **46 tables** in the
DB, **none** matching `%candle%`/`%price%`/`%tick%`/`%quote%`; the only price-bearing log line for
the symbol all day is the **10:01:22 GTT placement**, `last_price: 586.5` — a placement snapshot
**5½ hours before the close**, not a close.)*
**(P)** `583.04` appears **0 times** in the day's log — ⭐ **against a working control: the same
price-pattern grep matches 32 times across other symbols**, so the zero is real and not a broken
pattern.
⇒ ⛔ **The realised-vs-marked hypothesis is NEITHER confirmed NOR refuted.** The single price on
record (586.50) would imply an unrealised of **−0.90**, not −4.36 — **but it is not a close, so it
proves nothing.** ⭐ **Kite is the only source that can settle it.**
⛔ **Scored and stopped. Not reconciled.**

---

## §5 — WHAT TO WRITE DOWN

1. **§1 reading A** — time, available, used, opening. **And whether available + used = opening.**
2. **§1 reading B** (after 15:30) — the same four. 🔴 **Plus the delivery-only cross-check: does
   reading B's `used margin` match the total CNC value from the operator card §2 (A)? If not, what
   does Kite's own positions total say?**
3. **§2** — how many alerts, at what times, with their four figures.
4. **§3(i)** — the two numbers and whether they match.
5. **§3(ii)** — the number, and whether it looks like today's P&L.
6. **§4** — the `kill_switch_state` row, verbatim.
7. **Anything that did not match what this sheet said to expect.**

---

## ❓ THE FOUR QUESTIONS OWED AFTER TONIGHT — ⛔ **WRITTEN NOW, DELIBERATELY UNANSWERED**

> ⭐⭐ **A question written BEFORE the evidence is a PREDICTION. The same question written AFTER it is
> a RATIONALISATION.** These are recorded now so they survive the session boundary — and so that
> whoever answers them cannot quietly reshape the question to fit what arrived.

1. **Did every runtime observation match the operator card — or were undocumented behaviours found?**
2. **Did the three figures converge:** reading B's delivery figure, the database CNC total
   (operator card §2 (A)), and Kite's own positions total?
3. **Did Thursday's boot seed match the written prediction** (§4b) **within settlement tolerance?**
4. **If all three hold, is the delivery evidence sufficient to close the capital-drift thread
   formally?**

⛔ **None of these is answered here, and none may be answered from reasoning — only from what tonight
and tomorrow morning actually produce.**

---

## 📌 FOR THE RECORD — what is already settled, so tonight is not spent re-deriving it

- **The two operands are different quantities.** `expected = snapshot.total` (total capital;
  reservations reduce the *available* buckets, not the total) vs `actual = margins.net`
  (`order_reconciler.py:3590-3591`; the adapter parses Kite's `equity.net` at
  `zerodha_adapter.py:1452`, and parses `available.cash` **separately** at `:1453`). **One is net of
  blocked margin; the other is not.**
- **The tolerance is `max(₹50, 10% of expected)` during market hours** (`:3629-3631`;
  `system_config.yaml:368-369`). For `expected = 9,918.40` that is **991.84** — exactly the figure in
  the email.
- ⭐ **The 10% band was added by FIX-190 (Bug I) to silence exactly this noise — for an INTRADAY
  book.** Its own comment says *"in-session, broker margin legitimately drops by the deployed capital,
  so the tight Rs tolerance fires constantly"*. **Intraday runs at ~5× leverage, so the blocked
  margin is a fraction of position value and stays inside 10%. Delivery runs at 1× — the full
  purchase value is blocked.** ⇒ **a delivery book deploying more than ~10% of capital breaches a 10%
  band by construction**, and the delivery bucket is 30% of total.
- ⛔ **There is no delivery-specific tolerance.** Width: `capital_drift_tolerance` across all tracked
  `.py`/`.yaml` — the only knobs are the **global** rupee floor and the **global** percentage; **zero**
  hits for any delivery or positional variant.
- ⛔ **NOT A FIX, NOT A RECOMMENDATION.** This is a money-path governor. It is registered
  (`MASTER_PENDING` §B#7 and §B#5) and goes through the full careful loop. **Nothing is changed here.**
