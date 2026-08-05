# EVENING OPERATOR CARD — WEDNESDAY 05-Aug-2026

> ## ⛔ PREPARED, NOT RUN. Every command below is for **you** to run this evening.
> Nothing on this card was executed when it was written. No VM or broker was contacted.

**Tonight is the first evening this system has ever ended with real delivery positions held
overnight.** Two artifacts below **cannot be re-created if missed** — §1 and §2.

**Order matters. Do §1 first, then §2. §3 can wait until after dinner.**

---

## §0 — PRE-FLIGHT: THE ONE CHECK THAT COMES BEFORE EVERY OTHER READ

⭐ **Every trading morning, before anything else: is the token file present and dated today?**

```bash
ssh <vm> 'cat /home/ubuntu/systems/trading-system/data_store/session/zerodha_token.json'
```

**Expect:** a JSON object containing `"date": "2026-08-05"` (today's date, matching the day you
are checking).

**Why this is first, and why nothing else substitutes for it:** nothing in cron starts the
trading service. The 08:15 cron writes **only** the token file; a separate `token-watcher`
polls every 30 seconds and starts the service when it sees a fresh token.
🔴 **A FAILED TOKEN REFRESH IS COMPLETELY SILENT — no boot, no error, no alert.** That means
*"no order today"* and *"the service never started"* look **exactly the same**, and **no later
evidence can separate them.** Only this file can.
⛔ **`cron-auto-token.log` is NOT a substitute** — it writes only when something is *abnormal*, so
silence there means success. An unchanged log proves nothing. **The FILE is the gate.**

*(This morning it was fine: token present at 08:15:01.9, service active at 08:15:05.)*

---

## §1 — ⛔ MOST URGENT: THE ~17:35 SHUTDOWN CENSUS

**This is emitted ONCE, when the service shuts down at about 17:35. If the service is disturbed,
restarted, or the log rotated, IT IS GONE AND CANNOT BE RE-CREATED.** Do this before anything else.

### 1a. Pull the census

```bash
ssh <vm> "journalctl -u trading-system.service --since '2026-08-05 17:00' --no-pager | grep -A200 'CENSUS BEGIN'"
```

**Expect:** a block starting `BEGIN day=2026-08-05 mode=live entries=70`, a list of unit names with
an `acted` count beside each, a `MISMATCH` section, and a closing `END mismatches=0`.

### 1b. 🔴 THE READING THAT MATTERS TONIGHT — AND IT IS NEW

```bash
ssh <vm> "journalctl -u trading-system.service --since '2026-08-05 17:00' --no-pager | grep -E 'cnc_gtt_placer|cnc_gtt_monitor'"
```

⭐ **Delivery TRADED today.** These two units were deliberately reclassified from
*"expected-dormant"* to *"expected-event-driven"* for exactly this day.

> ### 🔴 **IF THEY STILL READ `acted 0`, THAT IS A FINDING, NOT A NORMAL READING.**

On every previous day `acted 0` was correct. Today it is not. If you see `acted 0` on either unit,
**copy the whole census block out and keep it** — that is the evidence.

### 1c. Confirm the census reconciles with itself

```bash
ssh <vm> "journalctl -u trading-system.service --since '2026-08-05 08:00' --no-pager | grep -E 'composition OK|CENSUS BEGIN|END mismatches'"
```

**Expect:** the census `entries=` total should equal (block entries + inline `infra` entries), and
that total **minus the units marked `[expected]` absent** should equal the boot's `composition OK
N/N` figure. This morning the boot read **62/62**. **If `END mismatches=` is anything other than
`0`, keep the block and stop.**

---

## §2 — 🔴🔴 THE CHECK1 GATE — **THIS IS THREE SEPARATE CHECKS, NOT ONE**

### Why this matters, in one paragraph
Two CNC (delivery) positions are held. **Tomorrow at T+1 they leave the broker's "positions" list
and move to "holdings".** A reconciler check called CHECK1 looks only at *positions*. When it sees a
trade it thinks is open but finds no position, it concludes the position was closed manually — and
it then **cancels that trade's broker orders and releases its capital, on shares you still own.**
There is a protection that makes CHECK1 skip delivery trades. **Tonight we find out whether that
protection is actually armed.**

> ### ⛔⛔ "THE GTT WAS ACCEPTED AT ZERODHA" IS **NOT** THE THING CHECK1 READS.
> CHECK1 skips a trade only when there is a row in the system's own `gtt_state` table, with status
> `ACTIVE`, **whose `trade_id` matches that trade's `trade_id`.** A GTT that Zerodha accepted, with
> no matching row, is a **different fact** and **will not protect the position.**

### Run all three. Report them SEPARATELY. Do not merge the answers.

**CHECK (1) — does the GTT exist at the broker?**
```bash
ssh <vm> 'cd /home/ubuntu/systems/trading-system && venv/bin/python scripts/list_gtts.py'
```
*(If that script name is not present, list GTTs from the Kite web UI instead.)*
⚠️ **Kite's web UI never shows a GTT id** — for the id you need the API listing.
**Record:** how many GTTs exist, and their trigger ids.

**CHECK (2) — does an `ACTIVE` row exist in `gtt_state`?**
```bash
ssh <vm> "cd /home/ubuntu/systems/trading-system && sqlite3 data_store/trading_system.db \
  \"SELECT gtt_id, trade_id, symbol, status, created_at FROM gtt_state ORDER BY created_at;\""
```
**Record:** the number of rows, and the `status` of each. ⭐ **This table held ZERO rows before
today, so whatever you see is the first row it has ever carried in production.**

**CHECK (3) — 🔴 does that row's `trade_id` MATCH the held trade's `trade_id`?**
```bash
ssh <vm> "cd /home/ubuntu/systems/trading-system && sqlite3 data_store/trading_system.db \
  \"SELECT t.trade_id, t.product, t.status, t.qty_filled, t.entry_actual_price, \
           g.gtt_id, g.status AS gtt_status \
    FROM trades t LEFT JOIN gtt_state g ON g.trade_id = t.trade_id \
    WHERE t.product = 'CNC' AND t.status IN ('OPEN','PARTIAL','EXITING');\""
```
**Expect:** one row per held CNC position, each with a **non-empty `gtt_id`** and
**`gtt_status = ACTIVE`**.
🔴 **Any row where `gtt_id` is empty/NULL, or `gtt_status` is not `ACTIVE`, is the hazard.**

### 📏 TWO RECONCILIATIONS ARE OWED. ⛔ THEY ANSWER DIFFERENT QUESTIONS — DO NOT COLLAPSE THEM.

**(A) THE MONEY RECONCILE — today's TOTAL CNC VALUE, one number, computed twice.**
```bash
ssh <vm> "cd /home/ubuntu/systems/trading-system && sqlite3 data_store/trading_system.db \
  \"SELECT ROUND(SUM(qty_filled * entry_actual_price), 2) AS total_cnc_value_from_trades \
    FROM trades WHERE product = 'CNC' AND status IN ('OPEN','PARTIAL','EXITING');\""
```
Then compute the same total at the broker (sum of qty × average price across the CNC positions in
Kite) and **compare the two numbers.**
⛔ **Record the TOTAL, not symbol names** — a total survives a further fill or a partial exit, and it
keeps position identifiers out of a document that gets read, quoted and copied.
🔴 **A DIVERGENCE BETWEEN THE TWO TOTALS IS ITSELF THE FINDING.**

**(B) THE IDENTITY RECONCILE — per position, by necessity.** This is CHECK (3) above: for **each**
held CNC position, does the broker holding correspond to a trade row whose `trade_id` matches the
`trade_id` on the `ACTIVE` `gtt_state` row?
⛔ **Report internal `trade_id` values only — do not list symbol names.**

> ⭐ **(A) tells you the books agree. (B) tells you the protection will actually fire.**
> **(A) passing does NOT imply (B), and (B) is the one Thursday depends on.**

### THE THREE BRANCHES — what each result means

| what you find | what it means | what to do |
|---|---|---|
| **No held CNC position** | fuse unlit | Record it. Nothing else owed. |
| **Held + a matching `ACTIVE` row for every position** | ✅ **Protected** | Record it — **and record that this protection has now actually EXECUTED for the first time in production.** |
| 🔴 **Held, but NO matching row** *(or the row exists with a different/NULL `trade_id`)* | **This is the exact hazard the gate exists for** | **A decision is owed TONIGHT: either harden the skip, or close the position.** ⛔ **Thursday's 08:15 boot must not run undecided.** |

⚠️ **A note so you do not act on the wrong thing:** ⛔ **a CNC position still open after 15:17 is
CORRECT.** The end-of-day squareoff deliberately never touches CNC. **Do not intervene on that.**
Today is the first day that carve-out actually mattered.

---

## §3 — SCORE THE SIZING PREDICTION

A prediction was recorded **before** today's fills, so it can be scored honestly. It was:

> **Affordability binds before the 40% position-value cap** — because the cap is **40% of TOTAL
> capital** while the whole positional (delivery) bucket is only **30% of TOTAL**.

### ⛔⛔ DO NOT SCORE THIS OFF THE `binding_constraint` LABEL.
That label reports `CAPITAL` on a **three-way tie** as well as on a genuine capital bind — the
source comment says *"CAPITAL wins on tie"*. Reading the label would make a tie look like a
confirmation.

### ⭐ Score it on which candidate was **STRICTLY** the minimum
```bash
ssh <vm> "cd /home/ubuntu/systems/trading-system && sqlite3 data_store/trading_system.db \
  \"SELECT trade_id, product, qty_filled, binding_constraint, sizing_breakdown \
    FROM trades WHERE product = 'CNC' ORDER BY created_at;\""
```
From `sizing_breakdown`, read the three candidates — **`qty_by_risk`**, **`qty_by_capital`**,
**`qty_by_concentration`** — and record **which one was strictly smallest**. That, not the label, is
the answer.

### ⚠️ CHECK THE TIER FLOOR FIRST — at quantity 1 it is the likely decider
`raw_qty` is multiplied by the tier weight (measured permanently **0.5**) and then floored. **A
`raw_qty` of 1 floors to 0 and is lifted back to 1** by a safety floor. ⇒ **If `raw_qty` was 1 or 2,
then the TIER AND ITS FLOOR decided the quantity — not any cap** — and the `binding_constraint`
label is describing something other than the quantity actually ordered. **If that is what you see,
say that instead of scoring the prediction.**

⛔ **Express every limit as a RATIO, never as rupees.** The account's current size is a **testing**
value, not the design capital — a rupee figure computed on it expires tonight, and a bare percentage
without its denominator is how "16.5%" got misread by ~13×.

---

## §4 — WHAT TO WRITE DOWN BEFORE BED

1. §1b: did `cnc_gtt_placer` / `cnc_gtt_monitor` read `acted 0`? **(yes = a finding)**
2. §2: the three checks, **reported separately**, and which of the three branches you landed in.
3. §2(A): the two total-CNC-value figures and whether they agree.
4. §3: which candidate was strictly the minimum — or that the tier floor decided it.
5. Anything that did not match what this card told you to expect. **A card that turns out to be
   wrong is a useful result — write it down rather than working around it.**

---

**⛔ REMINDERS:** no push before 18:15 (standing rule), and tonight's push is a **separate decision**
that is yours to make — there is an open question about it recorded in the deploy ledger, because
the usual *"book flat"* pre-push gate meets a book that is **correctly not flat** for the first time.
