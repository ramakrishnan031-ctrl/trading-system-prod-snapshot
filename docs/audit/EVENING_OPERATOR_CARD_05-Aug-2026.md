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

## 🔄 REVISION 2 — 05-Aug ~12:2x IST. **Four more, and two of them would have produced a FALSE finding — which is worse than a missing one.**

| # | what was wrong | now |
|---|---|---|
| 7 | 🔴🔴 **THE CENSUS IS NOT IN `journalctl` AT ALL.** The service's stdout handler is **`WARNING`+** (`core/logger.py:419-420`) and the census is emitted at **INFO** (`effect_telemetry.py:302`, logger `effect_census`, `main.py:1319-1320`) ⇒ **`journalctl … \| grep effect_census` returns NOTHING.** Both earlier versions read the wrong source | **§1 now reads `logs/system_2026-08-05.log`** — the INFO+ catch-all file handler (`logger.py:400-402`, filter `:240-243`). **Journald is kept as a second capture** so a wrong guess still cannot lose the artifact |
| 8 | 🔴🔴 The join `LEFT JOIN orders … leg='ENTRY'` **is not guaranteed to return one row per trade** — and **there is NO `UNIQUE` constraint on `orders(trade_id, leg)`** (measured, §A below). A duplicate ENTRY row would make the money reconcile **double-count** and report a **false divergence** | **A DUPLICATE CHECK runs before the totals**, and §2(A) now sums **per-trade values in a subquery** so a duplicate cannot inflate it |
| 9 | §2c and §4 asked whole-day / "has it ever" questions against a **two-hour** capture window | Both now read the **full-day log**, and "ever" is stated as bounded by **30-day** log retention (`config/cron_registry.yaml:9,:19`) |
| 10 | ⛔ The hazard branch offered *"harden the skip, or close the position"* — **at 18:00 the market is shut and a capital-path code change is not doable.** Two impossible options | **Three real options with their costs**, plus the mitigation that actually exists (§2b), and the plain statement that **CHECK1 does not sell anything** |

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

### 1a. FIRST — capture. **TWO commands. Run BOTH. Do this before anything else.**

⭐ **Why two:** the census is written by the application's own logging to a **file**, not to the
systemd journal — its stdout only carries `WARNING` and above, and the census is `INFO`. **The file
is the real source.** The journal capture is kept as a cheap insurance copy. **Two captures cost
thirty seconds; a missed census cannot be recovered at any price.**

**Capture 1 — THE REAL SOURCE (the application log):**
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && cp logs/system_2026-08-05.log ~/census_system_2026-08-05.log && wc -l ~/census_system_2026-08-05.log'
```
**What you should see:** a line count, e.g. `18342 /home/ubuntu/census_system_2026-08-05.log`.
**If it says "No such file":** list what is there — `ssh trading-vm 'ls -la /home/ubuntu/systems/trading-system/logs/ | tail -20'` — and copy whichever `system_*.log` has today's date.

**Capture 2 — insurance (the systemd journal):**
```bash
ssh trading-vm 'journalctl -u trading-system.service --since "2026-08-05 16:00" --no-pager > ~/census_journal_2026-08-05.txt; wc -l ~/census_journal_2026-08-05.txt'
```
*(No end time — a late shutdown must not fall outside the window.)*
**If this one is small or empty, that is expected** — the journal only receives warnings and errors.

**Copy both to your PC** *(run in Git Bash on the PC, not on the VM — the destination is named so
you can find them tomorrow)*:
```bash
mkdir -p /d/Projects/trading-system/data_store/evening_2026-08-05
scp trading-vm:~/census_system_2026-08-05.log trading-vm:~/census_journal_2026-08-05.txt /d/Projects/trading-system/data_store/evening_2026-08-05/
```

⭐ **Why capture before searching at all:** if a search term below is wrong, you can just search the
file again. If you searched the live log and got nothing, you could not tell whether the census was
missing or your search was wrong — **and by then it is too late to find out.**

### 1b. Now read the census out of the captured file

```bash
ssh trading-vm 'grep effect_census ~/census_system_2026-08-05.log'
```

**What you should see** — the emitter is `core/effect_telemetry.py:242` and `:300`, so each line
contains exactly these strings:
```
effect_census | BEGIN day=2026-08-05 mode=live entries=70
effect_census | <unit name>: acted <n> | <tag>
effect_census | MISMATCH: NONE — every zero is an expected zero
effect_census | END day=2026-08-05 mismatches=0
```
⚠️ **The log file is JSON, one object per line**, so each of the above will be wrapped in `{...}`
with other fields around it. **That is normal — read the text between the quotes.**
**If this returns nothing but the file has thousands of lines:** the service had not shut down when
you captured. **Re-copy after 17:40 and try again. Nothing is lost** — the file is still on the VM.

### 1c. 🔴 THE READING THAT MATTERS TONIGHT — AND IT IS NEW

```bash
ssh trading-vm 'grep -E "cnc_gtt_placer|cnc_gtt_monitor" ~/census_system_2026-08-05.log'
```

⭐ **Delivery TRADED today.** These two units were deliberately reclassified from
*"expected-dormant"* to *"expected-event-driven"* for exactly this day.

> ### 🔴 **IF EITHER STILL READS `acted 0`, THAT IS A FINDING — NOT A NORMAL READING.**

On every previous day `acted 0` was correct. Today it is not. **If you see `acted 0`, keep the whole
file** — that is the evidence.

### 1d. Confirm the census agrees with itself

```bash
ssh trading-vm 'grep -E "effect_census \| (BEGIN|END)|composition OK" ~/census_system_2026-08-05.log'
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
  🔴 **A BLANK IN STEP 0's FIRST COLUMN MUST BE INVESTIGATED BEFORE YOU READ THE BRANCH TABLE.**
  It is not a cosmetic gap: a blank-product trade could be a held CNC position that every query
  below cannot see. **If you see one, that is a finding — write down its count and stop.**

**If STEP 0 shows no CNC row at all:** stop here and record that. Do not proceed to the branch table.

---

### ⭐ STEP 0b — THE DUPLICATE CHECK. **Run this second. It decides whether the totals can be trusted.**

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT t.trade_id, COUNT(*) AS entry_rows FROM trades t JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' GROUP BY t.trade_id HAVING COUNT(*) > 1;"'
```

> ### **You should see NOTHING. Empty output is the good result here.**

**If you see any row:** that trade has more than one ENTRY order record — which happens legitimately
when an entry was rejected and re-placed, or when an order was superseded. ⚠️ **Then the queries
below will count that position more than once: CHECK (3) will show it twice, and the money total
will be too high.** ⇒ **Read the totals as UNRELIABLE and write down what you saw.** ⛔ **Do not
report a money divergence as a finding if this check returned rows** — the divergence would be the
duplicate, not a real disagreement with the broker.

*(Why this check exists: there is **no `UNIQUE` constraint** on `orders(trade_id, leg)` —
`orders.order_id` is the primary key, and the three indexes on that table are all non-unique
(`core/schema.sql:368-380`). The schema also carries `leg_index -- 0 for FULL entry; 0/1/2 for SCALE
legs` and a `superseded_by` replacement chain, so multiple ENTRY rows are permitted by design.
⛔ Whether any exist **today** could not be checked from the PC — the local database copy is two days
stale — so this check is on the card rather than assumed away.)*

---

### CHECK (1) — does a GTT exist at the broker?

⛔⛔ **DO NOT RUN `scripts/t2_cnc_gtt_realtest.py`. IT PLACES REAL ORDERS WITH REAL MONEY.**
Its own header says so, and it can buy, sell and delete GTTs. **It is not a lister.**
⚠️ **There is no read-only GTT-listing script in this repo** (checked: `git ls-files | grep -i gtt`
→ 10 files, one script, and that script is the one above).

**So do this instead — open Kite in your browser and look at the GTT / orders page.**
**Record:** how many GTT triggers exist.
⚠️ **Kite's web page does not show a GTT's id number.** That is fine — CHECK (3) does not need it.
**Status: UNVERIFIED from the PC** — I could not confirm a safe listing command without contacting
the broker, so none is given rather than one that might not work.

> ### 🔴 **HOW IMPORTANT IS CHECK (1)? IT DEPENDS ENTIRELY ON WHAT (2) AND (3) SAY.**
> - **If (2) and (3) PASS** — a matching `ACTIVE` row for every position — CHECK (1) is
>   **corroboration**. Nice to have, changes nothing.
> - 🔴 **If (2) or (3) FAILS, CHECK (1) BECOMES THE MOST IMPORTANT ANSWER ON THIS CARD.** Here is
>   why: if the local row is missing **but the GTT does exist at Zerodha**, then your shares are
>   still protected — **by the broker** — and the only harm CHECK1 can do tomorrow is **cancel that
>   protection**. But if **no GTT exists at Zerodha either**, the position is already unprotected and
>   tomorrow's behaviour changes nothing about that.
> ⇒ **CHECK (1) is what decides whether the mitigation in §2b is worth anything. Do not skip it on
> the bad branch.**

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
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT COUNT(*) AS positions, ROUND(SUM(v), 2) AS total_cnc_value FROM (SELECT DISTINCT t.trade_id AS tid, t.qty_filled * t.entry_actual_price AS v FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' WHERE o.product = '\''CNC'\'' AND t.status NOT IN ('\''CLOSED'\'','\''CLOSED_MANUAL'\'','\''CANCELLED'\'','\''FAILED'\''));"'
```
⭐ **This sums one value per DISTINCT trade**, so a duplicate ENTRY row (STEP 0b) cannot inflate it.
**It also prints the position COUNT beside the total — check that count against the number of CNC
positions you can see in Kite before you compare any money.** If the count disagrees, the total is
meaningless and the count is the finding.

Then add up the same total in Kite (quantity × average price, across the CNC positions) and
**compare the two numbers.**
⛔ **Record the TOTAL, not symbol names** — a total survives a further fill or a partial exit, and it
keeps position identifiers out of a document that gets read and quoted.
🔴 **A DIVERGENCE BETWEEN THE TWO TOTALS IS ITSELF THE FINDING** — ⛔ **unless STEP 0b returned rows,
in which case the divergence is the duplicate and not a real disagreement.**
**If `positions` is 0 or the total is blank:** that means zero matching rows — go back to STEP 0.

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
| 🔴 **CNC held, but a position has NO matching row** *(blank `gtt_id`, or a non-`ACTIVE` status, or a row whose `trade_id` differs)* | **the exact hazard this gate exists for** | **A decision is owed TONIGHT — see §2b for the three real options.** ⛔ **Thursday's 08:15 boot must not run undecided.** |
| ⚠️ **Any query returned zero and STEP 0 was not run or not understood** | **no evidence, not a pass** | Run STEP 0. Do not conclude anything until it is explained. |

⚠️ **So you do not act on the wrong thing:** ⛔ **a CNC position still open after 15:17 is CORRECT.**
The end-of-day squareoff deliberately never touches CNC. **Do not intervene.** Today is the first day
that carve-out actually mattered.

---

## §2b — 🔴 IF YOU LANDED ON THE HAZARD BRANCH: THE THREE REAL OPTIONS

> ## ⭐ **FIRST, THE THING THAT DECIDES HOW CALMLY YOU CHOOSE: YOUR SHARES ARE NOT AT RISK OF BEING SOLD.**
> CHECK1 **cancels orders and releases capital in the accounting. It does not place a sell order.**
> Whatever happens tomorrow morning, **the shares stay in your account.** The damage would be a
> cancelled protective GTT plus wrong capital figures — **both repairable by hand.**

**A decision is owed tonight, and these are the options that actually exist at this hour.**
⛔ *Not on this list, and deliberately: "harden the skip" is a capital-path code change needing
design, review, build, regression and a push — not doable in two hours on flip day. "Close the
position" is impossible — the market is shut.*

**(i) DO NOT LET THURSDAY BOOT.**
**How it works** — verified from source: nothing in cron starts the trading service. The 08:15 cron
writes only the token file, and `deploy/token_watcher.sh` starts the service **only** when
`token_is_fresh && within_service_window` (`:185-186`). `token_is_fresh()` (`:53-71`) requires the
token file to exist, its `date` field to equal today, **and** a non-empty `access_token` — any one
failing means the watcher never calls `start_service`. ⇒ **no fresh token ⇒ no boot ⇒ CHECK1 never
runs ⇒ nothing is cancelled.**
⭐ **The boot chain has been recorded as a hazard. Tonight it is also a control.**
**Its costs, and they are real:** **no intraday trading Thursday at all** · it is a **manual
intervention outside every normal procedure** · and it must be **deliberately undone afterwards**,
or Friday does not start either.
⚠️ **Worth only as much as CHECK (1) says:** if the GTT exists at Zerodha, not booting **preserves**
that protection. If no GTT exists anywhere, not booting protects nothing.

**(ii) LET THURSDAY BOOT, AND REPAIR AFTERWARDS.**
The shares are not sold. CHECK1 would cancel the position's broker orders and release its capital in
the accounting; both are **recoverable by hand**. **The cost:** the position sits **unprotected**
from that moment until someone re-places a protective order — an unattended, unhedged holding.

**(iii) SOMETHING ELSE, decided with the facts in hand.**

⛔⛔ **THIS CARD DOES NOT RECOMMEND ONE. It is Rama's ruling, and the facts he needs are: (a) does a
GTT exist at the broker (CHECK 1), (b) how many positions and what total value (STEP 0 + §2(A)),
(c) which of (2)/(3) failed and for how many positions.** Gather those three, then decide.

---

## §2c — ⭐ TWO CHEAP QUERIES THAT SETTLE AN OPEN QUESTION

There is a second, unguarded way for CHECK1 to fire that we found this morning. **Whether it can
reach a delivery trade at all depends on one thing: is any CNC trade in `EXITING` status?** These two
reads settle it, and you are already at the console.

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT t.trade_id, o.product, t.status FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' WHERE o.product = '\''CNC'\'';"'
```
⭐ **If no CNC trade shows `EXITING`, that second path cannot fire today** — which turns an unknown
into a known and makes tomorrow's design decision much easier. **One line either way is enough.**

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && grep -ilE "stuck_exiting" logs/*.log | head -20'
```
⭐ **Has that path ever actually fired?** A path that has never run in production is a very different
problem from one that runs weekly.
**What you should see:** a list of log filenames, or nothing.
⛔⛔ **AND THE BOUND MUST BE WRITTEN DOWN BESIDE THE ANSWER, OR A ZERO WILL BE READ AS "NEVER" WHEN
IT ONLY MEANS "NOT IN WHAT I LOOKED AT": the logs are deleted after 30 days**
(`find logs -name '*.log' -mtime +30 -delete`, `config/cron_registry.yaml:9`). ⇒ **the honest way to
record an empty result is *"not in the last 30 days of logs"* — never *"never".***
**Nothing to do — just record what you see, including nothing.**

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
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && grep -iE "database is locked|OperationalError" logs/system_2026-08-05.log logs/debug_2026-08-05.log 2>/dev/null | head -20'
```
**What you should see:** nothing, or ordinary informational lines.
**If you see database-lock or error lines near the shutdown time:** note them. ⛔ **Do not act on
this. It is for recognition only** — a database-lock morning is not exotic in this system.
*(This reads the whole day's logs, not the evening window — a lock at 10:00 matters as much as one
at 17:30.)*

---

## §5 — WHAT TO WRITE DOWN BEFORE BED

1. **§1c:** did `cnc_gtt_placer` / `cnc_gtt_monitor` read `acted 0`? **(yes = a finding)**
2. **§1d:** `mismatches=` — was it `0`?
3. **§2 STEP 0:** the unfiltered (product, status) counts. **Especially any blank product.**
4. **§2:** the three checks, **reported separately**, and which branch you landed in.
5. **§2(A):** the two total-CNC-value figures, and whether they agree.
6. **§2c:** is any CNC trade in `EXITING`? (one line either way)
7. **§3:** which candidate was strictly smallest — or that the tier floor decided it.
8. **Anything that did not match what this card said to expect.** ⭐ **A card that turns out to be
   wrong is a useful result — write it down rather than working around it.** This card has already
   been corrected once for exactly that reason.

---

**⛔ REMINDERS:** no push before 18:15. Tonight's push is a **separate decision that is yours** —
there is an open question recorded in the deploy ledger, because the usual *"book flat"* pre-push
check meets a book that is **correctly not flat** for the first time.
