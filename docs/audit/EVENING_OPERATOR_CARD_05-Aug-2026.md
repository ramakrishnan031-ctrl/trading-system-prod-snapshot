# EVENING OPERATOR CARD — WEDNESDAY 05-Aug-2026

> ## ⛔ PREPARED, NOT RUN. Every command below is for **you** to run this evening.
> Nothing here was executed when it was written. No VM or broker was contacted.

---

## 🔄 REVISION — 05-Aug ~12:5x IST. **The first version of this card had four faults that would have failed at the console. All measured against source; all fixed.**

| # | what was wrong | now |
|---|---|---|
| 1 | Every command said `<vm>` — nothing was copy-pasteable | **`trading-vm`**, taken from `~/.ssh/config` (`Host trading-vm`), cross-checked against `PATHS.md:219` — **the two agree** |
| 2 | 🔴 The census grep anchored on `'CENSUS BEGIN'` — **that string is not emitted anywhere.** It would have returned nothing on the one artifact that cannot be re-run | Anchor is **`effect_census`**, taken verbatim from `core/effect_telemetry.py:242`. And the read is now **capture-to-a-file first, filter second**, so a wrong anchor costs a retry, not the evidence |
| 3 | 🔴🔴 Every query used `trades.product` — **there is no such column.** All three would have errored with `no such column: product` | `product` lives on **`orders`** (`core/schema.sql:326`, table declared `:313`) and reaches the code by a **`LEFT JOIN orders ON leg='ENTRY'`**. Every query rewritten with that join |
| 4 | 🔴 §3 selected `sizing_breakdown` — no such column | The three candidates are **their own columns** on `trades` (`:218-221`) — easier, no JSON to read |
| 5 | ⛔ The card named `scripts/list_gtts.py` (**does not exist**) and offered a fallback that cannot produce a GTT id | **No read-only GTT lister exists in this repo.** The only GTT script **places real orders** and is now explicitly forbidden below. CHECK (1) restructured so the gate does not depend on it |
| 6 | A zero-row result could be read as "no position held" | **§2 now begins with an UNFILTERED count.** Zero rows is not a pass |

*(History is here, on purpose. Nothing below is struck through — a struck-out command is a command you might still run.)*

---

**Tonight is the first evening this system has ever ended with real delivery positions held
overnight.** Two things below **cannot be re-created if missed** — §1 and §2.

**Order matters. Do §1 first, then §2. §3 and §4 can wait until after dinner.**

---

## §0 — PRE-FLIGHT: THE ONE CHECK THAT COMES BEFORE EVERY OTHER READ

```bash
ssh trading-vm 'cat /home/ubuntu/systems/trading-system/data_store/session/zerodha_token.json'
```

**What you should see:** a line of JSON containing `"date": "2026-08-05"` — today's date.
**If it errors or the date is wrong:** that is the answer to *"why did nothing happen today"*. Stop
and note it.

**Why this is first:** nothing in cron starts the trading service. The 08:15 cron writes **only**
this file; a watcher polls every 30 seconds and starts the service when it sees a fresh one.
🔴 **A failed token refresh is completely silent — no boot, no error, no alert.** So *"no order
today"* and *"the service never started"* look identical, and **no later evidence separates them.**
⛔ **`cron-auto-token.log` is not a substitute** — it only writes when something is *wrong*, so
silence there means success. **The FILE is the gate.**

*(This morning was fine: token at 08:15:01.9, service active at 08:15:05.)*

---

## §1 — ⛔ MOST URGENT: THE ~17:35 SHUTDOWN CENSUS

**This is written to the log ONCE, when the service shuts down around 17:35. If the service is
restarted or disturbed, IT IS GONE AND CANNOT BE RE-CREATED.**

### 1a. FIRST — capture the whole window to a file. Do this before anything else.

```bash
ssh trading-vm 'journalctl -u trading-system.service --since "2026-08-05 16:30" --until "2026-08-05 18:30" --no-pager > ~/census_2026-08-05.txt; wc -l ~/census_2026-08-05.txt'
```

**What you should see:** a line count, e.g. `1247 /home/ubuntu/census_2026-08-05.txt`.
**If the count is 0 or very small:** the service may not have shut down yet — wait until 17:40 and
run it again.
⭐ **Why capture first:** if a search term below is wrong, you can just search the file again. If
you searched the live log and got nothing, you could not tell whether the census was missing or your
search was wrong — **and by then it is too late to find out.**

**Copy it to your PC as a backup** *(run this in Git Bash on the PC, not on the VM)*:
```bash
scp trading-vm:~/census_2026-08-05.txt .
```

### 1b. Now read the census out of that file

```bash
ssh trading-vm 'grep effect_census ~/census_2026-08-05.txt'
```

**What you should see** — the emitter is `core/effect_telemetry.py:242` and `:300`, so the lines
look exactly like this:
```
effect_census | BEGIN day=2026-08-05 mode=live entries=70
effect_census | <unit name>: acted <n> | <tag>
effect_census | MISMATCH: NONE — every zero is an expected zero
effect_census | END day=2026-08-05 mismatches=0
```
**If this returns nothing but the file has thousands of lines:** the service had not shut down when
you captured. Re-capture after 17:40. **The file is safe; nothing is lost.**

### 1c. 🔴 THE READING THAT MATTERS TONIGHT — AND IT IS NEW

```bash
ssh trading-vm 'grep -E "cnc_gtt_placer|cnc_gtt_monitor" ~/census_2026-08-05.txt'
```

⭐ **Delivery TRADED today.** These two units were deliberately reclassified from
*"expected-dormant"* to *"expected-event-driven"* for exactly this day.

> ### 🔴 **IF EITHER STILL READS `acted 0`, THAT IS A FINDING — NOT A NORMAL READING.**

On every previous day `acted 0` was correct. Today it is not. **If you see `acted 0`, keep the whole
file** — that is the evidence.

### 1d. Confirm the census agrees with itself

```bash
ssh trading-vm 'grep -E "effect_census \| (BEGIN|END)|composition OK" ~/census_2026-08-05.txt'
```

**What you should see:** `END day=2026-08-05 mismatches=0`.
🔴 **If `mismatches=` is anything other than `0`:** keep the file and stop. Note the mismatch lines —
they begin `MISMATCH(i)`, `MISMATCH(ii)` or `MISMATCH(iv)`.

---

## §2 — 🔴🔴 THE CHECK1 GATE — **THREE CHECKS, AND ONE COUNT THAT COMES BEFORE THEM**

### Why this matters, in one paragraph
Two CNC (delivery) positions are held. **Tomorrow they leave the broker's "positions" list and move
to "holdings".** A reconciler check called CHECK1 looks only at *positions*. When it sees a trade it
believes is open but finds no position, it concludes the position was closed by hand — and then
**cancels that trade's broker orders and releases its capital, on shares you still own.** There is a
protection that makes CHECK1 skip delivery trades. **Tonight we find out whether it is armed.**

> ### ⛔⛔ "THE GTT WAS ACCEPTED AT ZERODHA" IS **NOT** WHAT CHECK1 READS.
> CHECK1 skips a trade only when there is a row in the system's own `gtt_state` table, with status
> `ACTIVE`, **whose `trade_id` matches that trade's `trade_id`.** A GTT that Zerodha accepted, with
> no matching row, **will not protect the position.**

---

### ⭐ STEP 0 — THE UNFILTERED COUNT. **RUN THIS FIRST. DO NOT SKIP IT.**

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 data_store/trading_system.db "SELECT o.product, t.status, COUNT(*) FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' GROUP BY o.product, t.status ORDER BY o.product, t.status;"'
```

**What you should see:** a small table — one line per (product, status) pair, e.g.
```
CNC|OPEN|2
MIS|CLOSED|14
```
**This is the ground truth. Everything below narrows it.**

> ## ⛔ **ZERO ROWS IS NOT A PASS.**
> It means **EITHER** no position exists, **OR** the filter is wrong. **You must prove which before
> you read the branch table at the end of this section.** The proof is this unfiltered count.

**Two specific ways a narrowed query can lie to you, both real:**
- A held trade can sit in a status other than `OPEN`/`PARTIAL`/`EXITING`. The full permitted set is
  `PENDING`, `PENDING_FILL`, `OPEN`, `PARTIAL`, `EXITING`, `CLOSED`, `CLOSED_MANUAL`, `CANCELLED`,
  `FAILED`, `UNKNOWN_IN_FLIGHT`, or anything starting `REJECTED` (`core/schema.sql:158-161`).
  ⚠️ **`PENDING_FILL` and `UNKNOWN_IN_FLIGHT` both mean a real broker position may exist while the
  database has not caught up.**
- `product` comes from a **LEFT JOIN**, so a trade whose ENTRY order row is missing shows
  `product` as **blank** — and would be invisible to a `product = 'CNC'` filter.
  **In the STEP 0 output, a blank in the first column is exactly that case. If you see one, note it.**

**If STEP 0 shows no CNC row at all:** stop here and record that. Do not proceed to the branch table.

---

### CHECK (1) — does a GTT exist at the broker?

⛔⛔ **DO NOT RUN `scripts/t2_cnc_gtt_realtest.py`. IT PLACES REAL ORDERS WITH REAL MONEY.**
Its own header says so, and it can buy, sell and delete GTTs. **It is not a lister.**
⚠️ **There is no read-only GTT-listing script in this repo** (checked: `git ls-files | grep -i gtt`
→ 10 files, one script, and that script is the one above).

**So do this instead — open Kite in your browser and look at the GTT / orders page.**
**Record:** how many GTT triggers exist.
⚠️ **Kite's web page does not show a GTT's id number.** That is fine — **the gate below does not
need it.** CHECK (3) is answered entirely from the database. Treat CHECK (1) as corroboration.
**Status: UNVERIFIED from the PC** — I could not confirm a safe listing command without contacting
the broker, so none is given rather than giving one that might not work.

### CHECK (2) — does an `ACTIVE` row exist in `gtt_state`?

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT gtt_id, trade_id, symbol, status, created_at FROM gtt_state ORDER BY created_at;"'
```

**What you should see:** one row per protective GTT, `status` = `ACTIVE`.
*(Columns verified: `core/schema.sql:1218-1232`. Permitted statuses are `ACTIVE`, `TRIGGERED`,
`CANCELLED`, `EXPIRED`, `REJECTED`, `CLEANED` — `:1228-1229`.)*
⭐ **This table held ZERO rows before today, so whatever you see is the first row it has ever
carried in production.**
**If it returns nothing:** that is a real result, not an error — and it is the hazard. Go to the
branch table.

### CHECK (3) — 🔴 does that row's `trade_id` MATCH the held trade's?

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT t.trade_id, o.product, t.status, t.qty_filled, t.entry_actual_price, g.gtt_id, g.status AS gtt_status FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' LEFT JOIN gtt_state g ON g.trade_id = t.trade_id WHERE o.product = '\''CNC'\'' AND t.status NOT IN ('\''CLOSED'\'','\''CLOSED_MANUAL'\'','\''CANCELLED'\'','\''FAILED'\'');"'
```

**What you should see:** one row per held CNC position, each with a **non-empty `gtt_id`** and
**`gtt_status` = `ACTIVE`**.
🔴 **Any row where `gtt_id` is blank, or `gtt_status` is not `ACTIVE`, is the hazard.**
**If this errors:** copy the error text and stop — do not improvise a different query.
*(This deliberately excludes only the four finished statuses rather than listing three live ones, so
a position in `PENDING_FILL` or `UNKNOWN_IN_FLIGHT` still appears.)*

---

### 📏 TWO RECONCILIATIONS. ⛔ THEY ANSWER DIFFERENT QUESTIONS — DO NOT MERGE THEM.

**(A) THE MONEY RECONCILE — today's TOTAL CNC VALUE, one number, computed twice.**
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 data_store/trading_system.db "SELECT ROUND(SUM(t.qty_filled * t.entry_actual_price), 2) FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' WHERE o.product = '\''CNC'\'' AND t.status NOT IN ('\''CLOSED'\'','\''CLOSED_MANUAL'\'','\''CANCELLED'\'','\''FAILED'\'');"'
```
Then add up the same total in Kite (quantity × average price, across the CNC positions) and
**compare the two numbers.**
⛔ **Record the TOTAL, not symbol names** — a total survives a further fill or a partial exit, and it
keeps position identifiers out of a document that gets read and quoted.
🔴 **A DIVERGENCE BETWEEN THE TWO TOTALS IS ITSELF THE FINDING.**
**If it returns blank:** that means zero matching rows — go back to STEP 0.

**(B) THE IDENTITY RECONCILE — per position, by necessity.** That is CHECK (3): for **each** held
position, does its trade row carry an `ACTIVE` `gtt_state` row **with the same `trade_id`**?
⛔ **Report `trade_id` values only — no symbol names.**

> ⭐ **(A) tells you the books agree. (B) tells you the protection will actually fire.**
> **(A) passing does NOT imply (B), and (B) is the one Thursday depends on.**

---

### THE BRANCH TABLE — ⛔ only readable once STEP 0 has explained any zero

| what STEP 0 + the three checks show | meaning | what to do |
|---|---|---|
| **STEP 0 shows no CNC row at all** | no delivery position exists | Record it. Fuse unlit, nothing else owed. |
| **CNC held + every position has a matching `ACTIVE` row** | ✅ **Protected** | Record it — **and record that this protection has now actually run for the first time in production.** |
| 🔴 **CNC held, but a position has NO matching row** *(blank `gtt_id`, or a non-`ACTIVE` status, or a row whose `trade_id` differs)* | **the exact hazard this gate exists for** | **A decision is owed TONIGHT: harden the skip, or close the position.** ⛔ **Thursday's 08:15 boot must not run undecided.** |
| ⚠️ **Any query returned zero and STEP 0 was not run or not understood** | **no evidence, not a pass** | Run STEP 0. Do not conclude anything until it is explained. |

⚠️ **So you do not act on the wrong thing:** ⛔ **a CNC position still open after 15:17 is CORRECT.**
The end-of-day squareoff deliberately never touches CNC. **Do not intervene.** Today is the first day
that carve-out actually mattered.

---

## §2b — ⭐ TWO CHEAP QUERIES THAT SETTLE AN OPEN QUESTION

There is a second, unguarded way for CHECK1 to fire that we found this morning. **Whether it can
reach a delivery trade at all depends on one thing: is any CNC trade in `EXITING` status?** These two
reads settle it, and you are already at the console.

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT t.trade_id, o.product, t.status FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' WHERE o.product = '\''CNC'\'';"'
```
⭐ **If no CNC trade shows `EXITING`, that second path cannot fire today** — which turns an unknown
into a known and makes tomorrow's design decision much easier. **One line either way is enough.**

```bash
ssh trading-vm 'grep -iE "stuck_exiting|MANUAL_CLOSE" ~/census_2026-08-05.txt | head -20'
```
⭐ **Has that path ever actually fired?** A path that has never run in production is a different
problem from one that runs weekly. **Nothing to do — just record what you see, including nothing.**

---

## §3 — SCORE THE SIZING PREDICTION

A prediction was recorded **before** today's fills so it could be scored honestly:

> **Affordability binds before the 40% position-value cap** — because the cap is **40% of TOTAL
> capital** while the whole delivery bucket is only **30% of TOTAL**.

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT t.trade_id, t.qty_filled, t.qty_by_risk, t.qty_by_capital, t.qty_by_concentration, t.binding_constraint FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' WHERE o.product = '\''CNC'\'';"'
```
*(Columns verified: `core/schema.sql:218-222`. There is no `sizing_breakdown` column — the three
candidates are their own columns, so nothing needs decoding.)*

### ⛔⛔ DO NOT SCORE THIS OFF `binding_constraint`.
That column reports `capital` on a **tie** as well as on a real capital bind — the source comment
says *"CAPITAL wins on tie"*. Reading it would make a tie look like a confirmation.

### ⭐ Score it on which of the three numbers is **STRICTLY** the smallest
Compare `qty_by_risk`, `qty_by_capital`, `qty_by_concentration`. **The strictly smallest one is the
answer.** If two are tied for smallest, **say "tied" — that is a real result, not a failure.**

### ⚠️ CHECK THIS FIRST — at quantity 1 the tier floor is the likely decider
The smallest of the three is then multiplied by a tier weight (permanently **0.5**) and rounded
down; **a value of 1 rounds to 0 and is lifted back to 1** by a safety floor. ⇒ **If the smallest of
the three is 1 or 2, then the tier and its floor decided the quantity — not any cap** — and
`binding_constraint` is describing something other than what was actually ordered. **If that is what
you see, write that down instead of scoring the prediction.**

⛔ **Express limits as ratios, never rupees.** The account's current size is a *testing* value; a
rupee figure computed on it expires tonight.

---

## §4 — ⚠️ WATCH ITEM ONLY. **NOTHING TO DO.**

There is a deliberate design choice worth recognising if you ever see it: if the system's read of the
`gtt_state` table fails for any reason (a database lock, for example), it treats the result as
*"no delivery trades to protect"* for that cycle. **That is intentional** — it stops a database
hiccup from breaking the whole reconciler — but on a day with real delivery positions it means a
transient error could briefly expose them.

```bash
ssh trading-vm 'grep -iE "gtt_state|database is locked|OperationalError" ~/census_2026-08-05.txt | head -20'
```
**What you should see:** nothing, or ordinary informational lines.
**If you see database-lock or error lines near the shutdown time:** note them. ⛔ **Do not act on
this. It is for recognition only** — a database-lock morning is not exotic in this system.

---

## §5 — WHAT TO WRITE DOWN BEFORE BED

1. **§1c:** did `cnc_gtt_placer` / `cnc_gtt_monitor` read `acted 0`? **(yes = a finding)**
2. **§1d:** `mismatches=` — was it `0`?
3. **§2 STEP 0:** the unfiltered (product, status) counts. **Especially any blank product.**
4. **§2:** the three checks, **reported separately**, and which branch you landed in.
5. **§2(A):** the two total-CNC-value figures, and whether they agree.
6. **§2b:** is any CNC trade in `EXITING`? (one line either way)
7. **§3:** which candidate was strictly smallest — or that the tier floor decided it.
8. **Anything that did not match what this card said to expect.** ⭐ **A card that turns out to be
   wrong is a useful result — write it down rather than working around it.** This card has already
   been corrected once for exactly that reason.

---

**⛔ REMINDERS:** no push before 18:15. Tonight's push is a **separate decision that is yours** —
there is an open question recorded in the deploy ledger, because the usual *"book flat"* pre-push
check meets a book that is **correctly not flat** for the first time.
