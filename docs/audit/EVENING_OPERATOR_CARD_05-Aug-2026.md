# EVENING OPERATOR CARD — WEDNESDAY 05-Aug-2026

> ## ⛔ PREPARED, NOT RUN. Every command below is for **you** to run this evening.
> Nothing here was executed when it was written. No VM or broker was contacted.

---

## ⚡ §EXEC — DO THIS. **Commands only, in order. Everything below this sheet is the reasoning.**

> ⛔ **This sheet is NAVIGATION, not a summary and not a second card.** Every command is **byte-identical** to the one in its section, and every line points at the section that explains it. **When anything is not what you expect — go to that section.**

### ⏰ ANY TIME

**Token file** — is it dated today?  → *§0*
```bash
ssh trading-vm 'cat /home/ubuntu/systems/trading-system/data_store/session/zerodha_token.json'
```
*You should see:* `"date": "2026-08-05"`


### ⏰ FROM 16:00 — **START HERE.** Nothing in this section can change after 15:30

**STEP 0 — unfiltered count.** ⛔ Do not skip.  → *§2 STEP 0*
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 data_store/trading_system.db "SELECT o.product, t.status, COUNT(*) FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' GROUP BY o.product, t.status ORDER BY o.product, t.status;"'
```
*You should see:* a small (product, status) table. 🔴 **A BLANK product = a finding.**

**STEP 0b — duplicate check.**  → *§2 STEP 0b*
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT t.trade_id, COUNT(*) AS entry_rows FROM trades t JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' GROUP BY t.trade_id HAVING COUNT(*) > 1;"'
```
*You should see:* **nothing.** Any row ⇒ the totals below are unreliable.

**CHECK (1)** — open Kite in the browser and count GTT triggers.  → *§2 CHECK (1)*
*You should see:* a count. ⛔ **No script — the only GTT script places real orders.**

**CHECK (2)** — ACTIVE rows in `gtt_state`.  → *§2 CHECK (2)*
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT gtt_id, trade_id, symbol, status, created_at FROM gtt_state ORDER BY created_at;"'
```
*You should see:* one row per protective GTT, `status` = `ACTIVE`.

**CHECK (3)** — does the row's `trade_id` match the trade's?  → *§2 CHECK (3)*
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT t.trade_id, o.product, t.status, t.qty_filled, t.entry_actual_price, g.gtt_id, g.status AS gtt_status FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' LEFT JOIN gtt_state g ON g.trade_id = t.trade_id WHERE o.product = '\''CNC'\'' AND t.status NOT IN ('\''CLOSED'\'','\''CLOSED_MANUAL'\'','\''CANCELLED'\'','\''FAILED'\'');"'
```
*You should see:* every row with a non-empty `gtt_id` and `gtt_status` = `ACTIVE`.

**(A) Money reconcile** — count + total, then compare with Kite.  → *§2 (A)*
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT COUNT(*) AS positions, ROUND(SUM(v), 2) AS total_cnc_value FROM (SELECT DISTINCT t.trade_id AS tid, t.qty_filled * t.entry_actual_price AS v FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' WHERE o.product = '\''CNC'\'' AND t.status NOT IN ('\''CLOSED'\'','\''CLOSED_MANUAL'\'','\''CANCELLED'\'','\''FAILED'\''));"'
```
*You should see:* a position count and one total. **Check the count against Kite first.**

> ## 🛑 **STOP HERE AND READ §2 BEFORE CONCLUDING ANYTHING.**
> The **zero-row rule**, the **branch table**, and — if you land on the hazard branch — **§2b's three options and their `systemctl` commands** are all DECISIONS.
> ⛔ **A decision must never be taken from a checklist without its reasoning.** They are deliberately not on this sheet.


### ⏰ ⛔ NOT BEFORE 17:40 — the census. Irrecoverable, so **scheduled**, not deprioritised

**Capture 1 — the real source.**  → *§1a*
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && cp logs/system_2026-08-05.log ~/census_system_2026-08-05.log && wc -l ~/census_system_2026-08-05.log'
```
*You should see:* a line count.

**Capture 2 — insurance.**  → *§1a*
```bash
ssh trading-vm 'journalctl -u trading-system.service --since "2026-08-05 16:00" --no-pager > ~/census_journal_2026-08-05.txt; wc -l ~/census_journal_2026-08-05.txt'
```
*You should see:* a line count (small or empty is expected).

**Copy both to the PC** *(run in Git Bash on the PC, not the VM)*.  → *§1a*
```bash
mkdir -p ~/Documents/trading-evidence/2026-08-05
scp trading-vm:~/census_system_2026-08-05.log trading-vm:~/census_journal_2026-08-05.txt ~/Documents/trading-evidence/2026-08-05/
```
*You should see:* two files copied.

**Read the census.**  → *§1b*
```bash
ssh trading-vm 'grep effect_census ~/census_system_2026-08-05.log'
```
*You should see:* `effect_census | BEGIN …` … `END … mismatches=0`.

**🔴 **The reading that matters tonight.****  → *§1c*
```bash
ssh trading-vm 'grep -E "cnc_gtt_placer|cnc_gtt_monitor" ~/census_system_2026-08-05.log'
```
*You should see:* **`acted 0` on either unit IS A FINDING.**

**Census self-check.**  → *§1d*
```bash
ssh trading-vm 'grep -E "effect_census \| (BEGIN|END)|composition OK" ~/census_system_2026-08-05.log'
```
*You should see:* `mismatches=0`.


### ⏰ AFTER DINNER

**Is any CNC trade `EXITING`?**  → *§2c*
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT t.trade_id, o.product, t.status FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' WHERE o.product = '\''CNC'\'';"'
```
*You should see:* one row per CNC trade with its status.


**Score the sizing prediction.**  → *§3*
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT t.trade_id, t.qty_filled, t.qty_by_risk, t.qty_by_capital, t.qty_by_concentration, t.binding_constraint FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' WHERE o.product = '\''CNC'\'';"'
```
*You should see:* the three `qty_by_*` numbers — **strictly smallest wins.**


**Watch item only — nothing to do.**  → *§4*
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && grep -inE "gtt_state|database is locked|OperationalError|DatabaseError" logs/system_2026-08-05.log logs/reconciler_2026-08-05.log | head -30'
```
*You should see:* nothing, or ordinary `gtt_state` lines.

---

# 📚 THE REASONING, THE COMMANDS IN FULL, AND THE HISTORY

*(Everything from here down explains the sheet above. The revision tables are history — they are last-but-one on purpose.)*

## 🔄 REVISION 1 — 05-Aug **11:26 IST**. **The first version of this card had four faults that would have failed at the console. All measured against source; all fixed.**

| # | what was wrong | now |
|---|---|---|
| 1 | Every command said `<vm>` — nothing was copy-pasteable | **`trading-vm`**, taken from `~/.ssh/config` (`Host trading-vm`), cross-checked against `PATHS.md:219` — **the two agree** |
| 2 | 🔴 The census grep anchored on `'CENSUS BEGIN'` — **that string is not emitted anywhere.** It would have returned nothing on the one artifact that cannot be re-run | Anchor is **`effect_census`**, taken verbatim from `core/effect_telemetry.py:242`. And the read is now **capture-to-a-file first, filter second**, so a wrong anchor costs a retry, not the evidence |
| 3 | 🔴🔴 Every query used `trades.product` — **there is no such column.** All three would have errored with `no such column: product` | `product` lives on **`orders`** (`core/schema.sql:326`, table declared `:313`) and reaches the code by a **`LEFT JOIN orders ON leg='ENTRY'`**. Every query rewritten with that join |
| 4 | 🔴 §3 selected `sizing_breakdown` — no such column | The three candidates are **their own columns** on `trades` (`:218-221`) — easier, no JSON to read |
| 5 | ⛔ The card named `scripts/list_gtts.py` (**does not exist**) and offered a fallback that cannot produce a GTT id | **No read-only GTT lister exists in this repo.** The only GTT script **places real orders** and is now explicitly forbidden below. CHECK (1) restructured so the gate does not depend on it |
| 6 | A zero-row result could be read as "no position held" | **§2 now begins with an UNFILTERED count.** Zero rows is not a pass |

## 🔄 REVISION 2 — 05-Aug **11:45 IST**. **Four more, and two of them would have produced a FALSE finding — which is worse than a missing one.**

| # | what was wrong | now |
|---|---|---|
| 7 | 🔴🔴 **THE CENSUS IS NOT IN `journalctl` AT ALL.** The service's stdout handler is **`WARNING`+** (`core/logger.py:419-420`) and the census is emitted at **INFO** (`effect_telemetry.py:302`, logger `effect_census`, `main.py:1319-1320`) ⇒ **`journalctl … \| grep effect_census` returns NOTHING.** Both earlier versions read the wrong source | **§1 now reads `logs/system_2026-08-05.log`** — the INFO+ catch-all file handler (`logger.py:400-402`, filter `:240-243`). **Journald is kept as a second capture** so a wrong guess still cannot lose the artifact |
| 8 | 🔴🔴 The join `LEFT JOIN orders … leg='ENTRY'` **is not guaranteed to return one row per trade** — and **there is NO `UNIQUE` constraint on `orders(trade_id, leg)`** (measured, §A below). A duplicate ENTRY row would make the money reconcile **double-count** and report a **false divergence** | **A DUPLICATE CHECK runs before the totals**, and §2(A) now sums **per-trade values in a subquery** so a duplicate cannot inflate it |
| 9 | §2c and §4 asked whole-day / "has it ever" questions against a **two-hour** capture window | Both now read the **full-day log**, and "ever" is stated as bounded by **30-day** log retention (`config/cron_registry.yaml:9,:19`) |
| 10 | ⛔ The hazard branch offered *"harden the skip, or close the position"* — **at 18:00 the market is shut and a capital-path code change is not doable.** Two impossible options | **Three real options with their costs**, plus the mitigation that actually exists (§2b), and the plain statement that **CHECK1 does not sell anything** |

## 🔄 REVISION 3 — 05-Aug **12:28 IST**. **The order was backwards, two more greps could not answer their own questions, and one option had no command.**

| # | what was wrong | now |
|---|---|---|
| 11 | 🔴🔴 **THE ORDER WAS BACKWARDS.** The card said *"do §1 first"* — but **§1's census is not written until ~17:35** and the operator is back at **17:00** ⇒ he would have run it too early, got an empty result, and hit exactly the frightening ambiguity the last two revisions existed to remove | **Every section now carries a CLOCK**, and the order is **§0 → §2 → §1 → §2b/2c → §3 → §4.** ⭐ **§2 is stable from 15:30 and §1 is impossible before 17:40** — so §1 is **scheduled, not deprioritised** |
| 12 | 🔴 §2c's *"has that path ever fired?"* grep **could not answer its own question** — `-l` lists filenames, and `stuck_exiting` also matches the config key `stuck_exiting_timeout_minutes` and ordinary startup lines | **DELETED.** Measured: that path emits `check_name="MANUAL_CLOSE"` — **identical to CHECK1's own disposition** — and logs only on *failure*. **Nothing distinguishes it in the log.** ⭐ A check that cannot go red is not a check |
| 13 | 🔴 §4's grep hid its own failure with `2>/dev/null`, and its prose (a `gtt_state` read failure) named something different from its pattern (generic DB errors) | **`2>/dev/null` removed** — an error you can see beats a silence you cannot interpret. Log filenames verified against `core/logger.py:400-416`. Pattern and prose now match |
| 14 | 🔴🔴 Option (i) explained **why** not-booting works but **never said what to type** — an option the operator cannot execute | **The exact command, its verification, and its undo are now in the same block**, gated behind three conditions, with the one residual hole named as **UNVERIFIED** |
| 15 | Option (i)'s costs were incomplete | **Two real costs added** (a permanently lost forward-shadow day; an unobserved GTT trigger) — **and one cost that is NOT real is stated so it is not assumed** |
| 16 | ⛔ The `scp` destination was inside `data_store/` — **a guarded directory, and "scratch inside `data_store/`" is already a registered finding (F2)** | Destination moved **outside the repo tree** |

## 🔄 REVISION 4 — 05-Aug **12:49 IST**. **The last hardening pass. ⛔ The card is FROZEN after this — the next thing that improves it is running it.**

| # | what was wrong | now |
|---|---|---|
| 17 | 543 lines, read at 17:40 by a tired person: **excellent as a reference, slow as a checklist** | ⚡ **A `§EXEC` front sheet at the top** — commands in order, one "you should see" each, a pointer to every section. ⛔ **NAVIGATION, not a summary and not a second card** (splitting would be the multi-authority defect on the worst possible night). **Every command extracted programmatically and byte-identity VERIFIED, not asserted** |
| 18 | 🔴 The option (i) STOP used `ssh … 'sudo …'`, which **allocates no TTY** — it can fail with `no tty present` or appear to hang, on the one command whose failure costs most | **`-t` fallback added for BOTH the stop AND the undo**, with its symptom named. Precedent stated **as evidence, not proof**: the same pattern is a documented operational command in 4 tracked files. **Passwordless sudo marked UNVERIFIED** |
| 19 | ⚠️ §1a still said *"do this before anything else"* after REVISION 3 made **§2** first — **the card contradicted itself** | Reworded to *"before anything else **in this section**"* with a pointer to the order block. Swept: 4 candidate hits, 3 correct in local scope, **1 real contradiction** |
| 20 | §2 asserted the held positions **as fact**, when STEP 0 is precisely what would disprove it | Reworded to **REPORTED**, with *"a different count or a blank product is a FINDING, not your mistake"* — ⭐ one word turning a premise into a prediction the next command scores |
| 21 | CHECK (2) said nothing about **more `ACTIVE` rows than positions** | One line: note the count and move on — orphan GTT rows are expected noise on delivery days and **not** tonight's gate |
| 22 | ⛔ `SYSTEM_MAP.md` still read *"DORMANT / never exercised"* **on the day delivery traded** — and **M8 had just made reading it mandatory** | Corrected there (`3548cac`), ⛔ **not here** — with the compound label `<DEPLOYED — ENTRY PATH VERIFIED LIVE; EXIT PATH UNVERIFIED>` and no figures copied from conversation |

## 🧊 REVISION 5 — 05-Aug **13:3x IST. ⛔ CHANGE-LOG ONLY. NOT ONE COMMAND CHANGED.**

| # | what was wrong | now |
|---|---|---|
| 23 | ⛔ **The card made a FALSE CLAIM ABOUT ITSELF:** its revision table stopped at 3 while its content was at 4, and **§5 item 10 told the operator it had been corrected "three times"** | **REVISION 4 recorded above; "three" → "four"; the approximate heading times replaced with the real ones** (they had drifted out of order). ⭐ **The freeze covers commands and decisions — it does NOT license a document to describe itself untruthfully.** ✅ **Byte-identity re-verified after the edit** |

*(History is here, on purpose. Nothing below is struck through — a struck-out command is a command you might still run.)*

---

**Tonight is the first evening this system has ever ended with real delivery positions held
overnight.**

## ⏰ THE ORDER, AND THE CLOCK ON EACH PART

> **§0 — any time** · **§2 — from 16:00 (do this first)** · **§1 — ⛔ NOT BEFORE 17:40** ·
> **§2b — only if §2 lands on the hazard branch** · **§2c · §3 · §4 — after dinner**

⭐ **WHY §2 GOES FIRST EVEN THOUGH §1 IS THE URGENT ONE:** **nothing in §2 can change after 15:30.**
The market is shut, no fill can happen, no GTT can trigger, and the `gtt_state` table cannot gain a
row. **§2 is fully answerable the moment you sit down.**

⛔ **AND §1 IS STILL THE ONE THING THAT CANNOT BE RECOVERED — it also cannot be captured before
17:40, because the service does not write it until it shuts down at ~17:35. So it is SCHEDULED, not
deprioritised.** Running it at 17:00 would return nothing, and "nothing" would look exactly like a
lost census.

---

## §0 — ⏰ ANY TIME · PRE-FLIGHT: THE ONE CHECK THAT COMES BEFORE EVERY OTHER READ

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

## §1 — ⏰ ⛔ NOT BEFORE 17:40 · THE ~17:35 SHUTDOWN CENSUS (irrecoverable, so SCHEDULED — not deprioritised)

**This is written to the log ONCE, when the service shuts down around 17:35. If the service is
restarted or disturbed, IT IS GONE AND CANNOT BE RE-CREATED.**

### 1a. FIRST **WITHIN §1** — capture. **TWO commands. Run BOTH, before anything else in this section.**
⏰ **§2 comes before all of §1.** See the order block above.

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
mkdir -p ~/Documents/trading-evidence/2026-08-05
scp trading-vm:~/census_system_2026-08-05.log trading-vm:~/census_journal_2026-08-05.txt ~/Documents/trading-evidence/2026-08-05/
```
⛔ **Deliberately OUTSIDE the project folder.** `data_store/` inside the repo is a **guarded
directory** — writing scratch there is already a registered defect in this system (`F2`), and
repeating it here would re-commit it. **`~/Documents/trading-evidence/2026-08-05/` on your PC.**

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

## §2 — ⏰ FROM 16:00, STABLE · 🔴🔴 THE CHECK1 GATE — **THREE CHECKS, AND ONE COUNT THAT COMES BEFORE THEM**

⭐ **Do this section FIRST.** Nothing in it can change after 15:30 — the market is shut, no fill can happen, no GTT can trigger, and `gtt_state` cannot gain a row.

### Why this matters, in one paragraph
Two CNC (delivery) positions are **REPORTED** held — ⭐ **STEP 0 below is what confirms it, and it is the first thing you run.** ⚠️ **Nobody has measured this yet** (the PC's database copy is two days stale). **If STEP 0 shows a different number, or a blank product, that is a FINDING — not your mistake.** **Tomorrow they leave the broker's "positions" list and move
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
⚠️ **If you see MORE `ACTIVE` rows than you have positions:** note the count and move on — an orphan
GTT row is expected noise on delivery days and is **not** tonight's gate.

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

## §2b — ⏰ ONLY IF §2 HIT THE HAZARD BRANCH · 🔴 THE THREE REAL OPTIONS

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
`token_is_fresh && within_service_window` (`:185-186`). ⇒ **stop the watcher and nothing calls
`start_service` ⇒ CHECK1 never runs ⇒ nothing is cancelled.**
⭐ **The boot chain has been recorded as a hazard. Tonight it is also a control.**

> ### ⛔⛔ **THE COMMAND — ONLY UNDER ALL THREE CONDITIONS. READ THE UNDO BEFORE YOU RUN THE STOP.**
> **1.** §2 landed on the **hazard branch**, **AND**
> **2.** **Rama has chosen option (i)** — ⛔ this card does not choose it, **AND**
> **3.** you have read the undo below and know you must run it.
>
> ⏰ **It must be in place BEFORE 08:15 Thursday — but decide TONIGHT, because 08:15 is early.**
>
> **STOP (run this):**
> ```bash
> ssh trading-vm 'sudo systemctl stop token-watcher.service && systemctl is-active token-watcher.service; systemctl is-active trading-system.service'
> ```
> **What you should see:** `inactive` for the watcher, and `inactive` for `trading-system` (it
> self-exited at 17:35). **If either says `active`, stop and do not assume it worked.**
>
> ⚠️ **IF IT SAYS `sudo: no tty present and no askpass program specified`, OR IT SEEMS TO HANG:**
> press **Ctrl-C** and use this instead — it will ask for your password:
> ```bash
> ssh -t trading-vm 'sudo systemctl stop token-watcher.service'
> ```
> *(Then re-run the plain command above to see the two `inactive` lines.)*
>
> **UNDO (run this when the decision is made and you want Friday to start — or Thursday, if the
> position is resolved during the day):**
> ```bash
> ssh trading-vm 'sudo systemctl start token-watcher.service && systemctl is-active token-watcher.service'
> ```
> **What you should see:** `active`.
> ⚠️ **Same `no tty` fallback applies to the UNDO** — and it matters more here, because an undo you
> cannot run is the state you cannot leave:
> ```bash
> ssh -t trading-vm 'sudo systemctl start token-watcher.service'
> ```
> ⚠️ **If you undo it inside 08:00–16:00 on a day with a fresh token, the service will start within
> 30 seconds.** That is the intended behaviour — just know it is immediate.
>
> 📌 **ON `sudo` OVER `ssh` — EVIDENCE, STATED AS EVIDENCE AND NOT AS PROOF:** this exact pattern is
> already written down as an operational command in **four tracked files** — `PATHS.md:223`
> (`ssh trading-vm 'sudo systemctl restart trading-system.service'`), `DEPLOYMENT.md:135` and `:191`,
> and `deploy/setup_uptimerobot.md:39`. **`deploy/resume.sh:25,38` also runs `sudo systemctl`
> stop/start as the `ubuntu` user** — though it runs *on* the VM, so it evidences the sudo right, not
> the non-interactive-ssh path. ⚠️ **UNVERIFIED from the PC: I could not confirm passwordless sudo
> without contacting the VM.** ⇒ **the `-t` fallback above is on the card precisely because the
> primary form is evidenced but not proven.**
>
> ⭐ **WHY THIS MECHANISM AND NOT THE OTHERS** *(the HOW is an engineering answer; the WHETHER is
> Rama's)*: stopping the unit is **deterministic, edits no file, and undoes with one command.**
> ⛔ **Commenting out the 08:15 cron line** edits the crontab and is easy to forget — and the crontab
> is generated from `config/cron_registry.yaml`, so a hand edit can be silently reverted.
> ⛔⛔ **DELETING THE TOKEN FILE IS NOT OFFERED AND MUST NOT BE USED — it races the 30-second poll**
> (the cron could rewrite it at 08:15 and the watcher could read it before you act again).
>
> ⚠️ **UNVERIFIED — ONE RESIDUAL HOLE, NAMED RATHER THAN GLOSSED:** `trading-system.service` is itself
> `WantedBy=multi-user.target`, so **if the VM reboots between 08:00 and 16:00 Thursday it could start
> directly, bypassing the stopped watcher.** No reboot is expected, and I could not check from here
> whether the unit is `enabled`. **What would settle it:** `ssh trading-vm 'systemctl is-enabled
> trading-system.service'` — if it prints `enabled`, add `sudo systemctl stop trading-system.service`
> to the stop step and remember it in the undo.

**Its costs, and they are real — all five:**
1. **No intraday trading Thursday at all.**
2. It is a **manual intervention outside every normal procedure**, and it must be **deliberately
   undone**, or Friday does not start either.
3. ⛔ **A PERMANENTLY LOST FORWARD-SHADOW DAY.** `forward_shadow_record.py` is the only out-of-sample
   evidence producer, its output **cannot be regenerated**, and it must **never** be run by hand — a
   gap is a loss, a manufactured day is a corruption. **Small, but it is the one cost that can never
   be recovered.**
4. ⚠️ **AN UNOBSERVED GTT TRIGGER.** If the broker GTT fires on Thursday while the system is down, the
   exit happens at Zerodha and **the system learns nothing about it** — Friday's boot then meets a
   position that closed itself, which is a **reconciliation event, not a clean start.** ⛔ Stated as a
   consequence to expect, **not** as a reason against.
5. ⭐ **AND ONE COST THAT IS NOT REAL — said plainly so it is not assumed: there is NO unattended
   intraday risk.** No service means no entries, so **there is nothing open to manage.** The 15:17
   squareoff not running is irrelevant on a day with no intraday positions — and it was never the
   CNC position's subject anyway.

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

## §2c — ⏰ AFTER DINNER · ⭐ ONE CHEAP QUERY THAT SETTLES AN OPEN QUESTION

There is a second, unguarded way for CHECK1 to fire that we found this morning. **Whether it can
reach a delivery trade at all turns on one thing: is any CNC trade in `EXITING` status?** This read
settles it, and you are already at the console.

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sqlite3 -header -column data_store/trading_system.db "SELECT t.trade_id, o.product, t.status FROM trades t LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = '\''ENTRY'\'' WHERE o.product = '\''CNC'\'';"'
```
**What you should see:** one row per CNC trade, with its status.
⭐ **If no CNC trade shows `EXITING`, that second path cannot fire today** — which turns an unknown
into a known and makes tomorrow's design decision much easier. **One line either way is enough.**

> ### ⛔ A SECOND QUERY WAS HERE AND HAS BEEN **DELETED**, DELIBERATELY — recorded so nobody re-adds it
> It asked *"has that path ever actually fired?"* by grepping the logs for `stuck_exiting`.
> **It cannot answer that question, so it was removed rather than left to mislead:**
> when that path resolves a trade it calls the *same* function CHECK1 does and returns
> **`check_name="MANUAL_CLOSE"` — byte-identical to CHECK1's own disposition** — and it writes its own
> log lines **only on failure**. **Nothing in the log distinguishes the two.** The word
> `stuck_exiting` also appears in the config key `stuck_exiting_timeout_minutes`, so a hit would have
> proved only that the *string* exists — and a list of filenames reads to a tired operator as
> *"it fired, a lot."*
> ⭐ **A check that cannot go red is not a check. Deleting it is the fix.**
> *(The question is still open and is recorded in the audit record as CANNOT DETERMINE. The query
> above answers the half that matters tonight — reachability — which is the useful half.)*

---

## §3 — ⏰ AFTER DINNER · SCORE THE SIZING PREDICTION

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

## §4 — ⏰ AFTER DINNER · ⚠️ WATCH ITEM ONLY. **NOTHING TO DO.**

There is a deliberate design choice worth recognising if you ever see it: if the system's read of the
`gtt_state` table fails for any reason (a database lock, for example), it treats the result as
*"no delivery trades to protect"* for that cycle. **That is intentional** — it stops a database
hiccup from breaking the whole reconciler — but on a day with real delivery positions it means a
transient error could briefly expose them.

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && grep -inE "gtt_state|database is locked|OperationalError|DatabaseError" logs/system_2026-08-05.log logs/reconciler_2026-08-05.log | head -30'
```
**What you should see:** nothing, or ordinary informational `gtt_state` lines.
**If you see `database is locked` / `OperationalError` / `DatabaseError` on the same lines as
`gtt_state`:** note them — that is the case described above. ⛔ **Do not act on it. Recognition
only** — a database-lock morning is not exotic in this system.

⚠️ **Two deliberate choices here, so the check cannot lie to you:**
- **The pattern now includes `gtt_state`**, because the paragraph above is about a `gtt_state` read
  failing — the earlier version searched only for generic database errors, so its description and its
  command named different things.
- ⛔ **There is no `2>/dev/null`.** If a log file is missing you will see an error and know it. The
  earlier version hid that, so a missing file would have produced clean output and read as
  *"no locks."* **An error you can see beats a silence you cannot interpret.**
*(File names verified against `core/logger.py:400-412`: this application writes
`system_<date>.log`, `trades_<date>.log`, `reconciler_<date>.log` and `debug_<date>.log`. The
reconciler's own file is included because that is where these lines would land.)*
*(Whole-day logs, not the evening window — a lock at 10:00 matters as much as one at 17:30.)*

---

## §5 — WHAT TO WRITE DOWN BEFORE BED

*(In the order you did them.)*

1. **§2 STEP 0:** the unfiltered (product, status) counts. 🔴 **Especially any blank product.**
2. **§2 STEP 0b:** did the duplicate check return anything? **(If yes, the totals are unreliable.)**
3. **§2:** the three checks, **reported separately**, and which branch you landed in.
4. **§2(A):** the position count, the two total-CNC-value figures, and whether they agree.
5. **§2b** *(only if the hazard branch fired)*: which option Rama chose, and — if (i) — that the
   stop command ran and **that you know the undo.**
6. **§1c:** did `cnc_gtt_placer` / `cnc_gtt_monitor` read `acted 0`? **(yes = a finding)**
7. **§1d:** `mismatches=` — was it `0`?
8. **§2c:** is any CNC trade in `EXITING`? (one line either way)
9. **§3:** which candidate was strictly smallest — or that the tier floor decided it.
10. **Anything that did not match what this card said to expect.** ⭐ **A card that turns out to be
    wrong is a useful result — write it down rather than working around it.** This card has been
    corrected **four times** for exactly that reason, and each correction came from someone
    checking a claim rather than trusting it.

---

**⛔ REMINDERS:** no push before 18:15. Tonight's push is a **separate decision that is yours** —
there is an open question recorded in the deploy ledger, because the usual *"book flat"* pre-push
check meets a book that is **correctly not flat** for the first time.
