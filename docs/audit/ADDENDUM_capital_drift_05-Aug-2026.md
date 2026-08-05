# ADDENDUM — THE CAPITAL-DRIFT CRITICALs OF 05-Aug-2026

> ## 🧊 **THIS IS A SEPARATE SHEET. THE EVENING OPERATOR CARD IS FROZEN AT REVISION 4 AND IS NOT TOUCHED.**
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

**A screenshot is fine. The numbers typed out are better** — a screenshot cannot be searched next
month.

**Sanity check on each reading:** does **available + used = opening**?
- **Yes** → the broker's own books balance at that moment. ✅
- 🔴 **No** → **that is a finding.** Write down all three numbers and stop.

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

**What you should see:** one row.
- `state = INACTIVE` → ✅ **no kill has fired.**
- `state = SOFT_KILL` with a `triggered_at` of **today** → 🔴 stop and read the reason.
- `state = SOFT_KILL` with **yesterday's** date → ⚠️ **that is the routine 15:15 circuit-breaker
  kill, which persists overnight BY DESIGN and auto-clears at the next 08:15 boot.** Not an incident.

*(Belt and braces — the log side. `soft_kill` writes a CRITICAL after persisting, so this should be
empty if the row above says `INACTIVE`:)*
```bash
ssh trading-vm 'grep -iE "SOFT_KILL|HARD_KILL" ~/census_system_2026-08-05.log | head -20'
```
⚠️ **Expect some hits even on a clean day** — the words appear in ordinary startup and status lines.
**The database row above is the authority; this is only a cross-check.**

---

## §5 — WHAT TO WRITE DOWN

1. **§1 reading A** — time, available, used, opening. **And whether available + used = opening.**
2. **§1 reading B** (after 15:30) — the same four.
3. **§2** — how many alerts, at what times, with their four figures.
4. **§3(i)** — the two numbers and whether they match.
5. **§3(ii)** — the number, and whether it looks like today's P&L.
6. **§4** — the `kill_switch_state` row, verbatim.
7. **Anything that did not match what this sheet said to expect.**

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
