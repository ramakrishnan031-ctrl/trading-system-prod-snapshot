# STOP PROCEDURE — 06-Aug-2026 EVENING

> # ⛔ **WRITTEN BEFORE THE ACT. NOTHING HERE HAS BEEN RUN.**
> **The stop is Rama's to authorise and Rama's to run.** This document records the **decision basis
> as it stood at 19:0x**, per **§G6(c)** — *an override recorded afterwards is a justification;
> recorded before, it is a decision.* The same ordering rule applies to a gate that **passes**:
> the basis is written first, and the outcome is appended afterwards.
>
> ## 🔴 **HEADLINE 1 — THE GATE DOES NOT REFUSE. THERE IS NOTHING TO OVERRIDE.**
> Re-measured fresh at **19:08:44 IST**: **all five pre-flight checks PASS** under the corrected
> rule-based gate. ⇒ **No override is required, and none is being taken.** See §2.
>
> ## 🔴🔴 **HEADLINE 2 — NOT STOPPING COSTS A TRADING DAY. MEASURED, NOT ARGUED.**
> **Today's 15:15 breaker FIRED. The kill is ACTIVE right now. Both clearers are BOOT-ONLY.**
> ⇒ **no stop ⇒ no boot ⇒ Friday opens in `SOFT_KILL` ⇒ NO NEW ENTRIES ALL DAY FRIDAY.** See §4b.
> ⭐ **This is the residue of §4 arriving as a consequence — and it is the decisive argument.**

**Clocks (`Get-Date` / VM `date`):** PC **2026-08-06 19:03:25 IST Thursday** · VM **19:08:44 IST**.
**Ahead-count:** `git rev-list --count origin/main..main` → **91**, run 19:03:25 IST.

---

## §1 — THE MEASUREMENT (a), RE-RUN FRESH AND READ-ONLY

⭐ Run with the **corrected** gate: `$(date +%F)` for the log path, and the **rule-based** PASS block.
⛔ Read-only by construction (`is-active`, `grep`, `sqlite3 file:…?mode=ro`). **(P)** throughout.

| # | check | measured 19:08:44 | rule | |
|---|---|---|---|---|
| 1 | service active | `active` | `active` | ✅ |
| 2 | position count vs check-5 rows | `2 positions` (stamped **19:08:38** — 6 s before the read, i.e. **LIVE, not stale**) vs **2 rows** | equal **or** explained | ✅ **equal — the escape clause is not even needed** |
| 3 | every ACTIVE `gtt_state` row's `trade_id` matches a check-5 row | `330657774│trd_e66ee17b…│ATULAUTO` · `330658430│trd_010f8e21…│DIFFNKG` — **both matched exactly** | every row matches; a **NULL or unmatched** `trade_id` is the real failure | ✅ **no NULL, no mismatch** |
| 4 | non-terminal orders | `0` | `0` | ✅ |
| 5 | every non-terminal trade nameable | `trd_e66ee17b…│ATULAUTO│OPEN` · `trd_010f8e21…│DIFFNKG│OPEN` | each one named and accounted for | ✅ **ATULAUTO = the F6 phantom, diagnosed; DIFFNKG = the real T+1 carry** |

⚠️ **`get_holdings` = `0 holdings`, last stamped 15:23:40** — consistent with both legs sitting in
`positions()` rather than holdings, and **it is the contrast §4.1 of the Friday card measures.**

---

## §2 — THE OVERRIDE, SCORED AGAINST ALL THREE CONDITIONS (§G6)

**The rule (ChatGPT's, adopted):** fresh evidence alone does not justify an override. Require
**(a)** the safety objective still satisfied · **(b)** the evidence fully explains the apparent
violation · **(c)** logged, before the act.

> ### ⭐⭐ **BUT THE FIRST QUESTION IS PRIOR TO ALL THREE: *does the gate actually refuse?***
> **It does not.** The 18:3x refusal came from the gate's **literal** PASS block, which had been
> written assuming a **one-position** night. That block was rewritten **as a rule** the same evening
> (`STOP_PROCEDURE_05-Aug-2026.md` §(a), lines 69–84), and the rule says in terms:
> *"⛔ A second ACTIVE row is NOT itself a failure — it is a failure only if it is **unexplained**."*
> Under that rule, re-measured fresh, **all five checks pass.**

⇒ **The three conditions are scored below for the record, but they are scored on a gate that PASSES
— which means the correct instrument tonight is a WRITTEN BASIS, not an override.**

| | condition | scored |
|---|---|---|
| **(a)** | **is the original safety objective still served?** ⭐ *Answered, not assumed.* The objective is that **no stop is taken over UNEXPLAINED state that might mean something is wrong.** — **A stop needs no broker call** (it is `systemctl stop`; the 502s live on the adoption path). — **DIFFNKG's protection is confirmed with a matching `trade_id`** (check 3, exact). — **ATULAUTO's row is a known artifact of a diagnosed defect** (F6, `cnc_gtt_monitor.py:464`). ⇒ **nothing in the state is unexplained.** | ✅ **served** |
| **(b)** | **does the evidence fully explain the apparent violation?** Both rows named, both `trade_id`s matched **fresh at 19:08:44** (not the 17:39 read), both to a named trade. | ✅ **fully** |
| **(c)** | **logged before the act, with timestamp · clause · evidence · who ruled** | ✅ **this document, 19:0x, before anything runs.** ⛔ **Clause overridden: NONE — the gate passes.** **Who ruled: nobody has; the stop itself remains Rama's call.** |

⛔ **AND THE THING NOT TO DO, STATED PLAINLY:** logging a "belt-and-braces" override anyway would
**not** have been the cautious choice. It would record a bypass **that never happened**, and a
register with spurious overrides in it is how the next override comes to look ordinary. **Filed as
the corollary to §G6.**

---

## §3 — §2 OF THE BRIEF: BOTH HALVES, DISTINCTLY LABELLED

⛔ **Neither half may be dropped — the first is what tonight rests on, the second is why F6 outranks
its current queue position.**

- 🏷️ **(P) — UNREACHABLE TONIGHT, MEASURED.** ATULAUTO's `OPEN` row has **no path to close**:
  `held==0` (`cnc_gtt_monitor.py:487`) is the sole door to `_finalize_gtt_exit`, and `abs()` at
  `:464` makes `held=1` for a sale `holdings()` has already applied. ⇒ *"will exit once flat"* is
  unreachable **for the current measured state**.
- 🏷️ **(I) — AND STRUCTURALLY REACHABLE AGAIN WHENEVER A PHANTOM EXISTS.** The 17:35 self-exit
  guard's condition is *"no open rows"*, and **a phantom IS an open row by definition.** ⇒ this is
  **not a one-off state; it is a property of the coupling, and it recurs with every phantom.**

⭐ **The distinction matters because the weaker half is the quotable one.** (P) is a fact about
tonight and expires; (I) is a fact about the design and does not.

---

## §4 — 🔴 §0.2 ANSWERED AT SOURCE: **NO DAILY RESET IS BOOT-BOUND. THEY ARE CLOCK-BOUND.**

**The question:** if the service is *not* stopped, it trades Friday on a process booted Thursday
08:15. Do the daily counters, the daily-loss limit, the trade cap and the kill switch read
**Thursday's** window on Friday?

**(S) — measured by source read, every call site:**

| control | site | what it does | binding |
|---|---|---|---|
| **DAILY_TRADES cap** | `capital/risk_engine.py:241` | `today = now_ist().date().isoformat()` **inside `approve()`** — the RE16 comment scopes "read once" to a **single call**, ⛔ not to the process | ✅ **clock-bound, per call** |
| **CONSECUTIVE_LOSSES** | `capital/risk_engine.py:274` | `recent_trade_pnls(…, today=today)` off that same per-call `today` | ✅ **clock-bound** |
| **Daily-loss limit — BOTH halves** | `capital/fund_manager.py:1314` and `:1549` | each recomputes `today = now_ist().date().isoformat()` immediately before `get_daily_realized_net_pnl(today)` | ✅ **clock-bound** |
| **…and its reader** | `core/state_store.py:2542-2545` | `SELECT SUM(pnl_delta) FROM fm_ledger WHERE date = ?` — **date-scoped** | ✅ **date-scoped** |
| **the P&L state itself** | FIX-051 | ⭐⭐ **the in-memory `_daily_pnl` accumulator was REMOVED.** `tests/integration/test_q9_post_restart_capital_wired.py:48-49`: *"realized P&L **NOT RESTORED — NOT HELD IN MEMORY AT ALL** … recomputes it from SQL."* ⇒ **there is no process-lifetime P&L state that CAN go stale** | ✅ **no stale state exists** |
| **EOD P&L reset** | `orders/eod_squareoff.py:531` → `fund_manager.py:1645` | `reset_daily_pnl()` fires from the **15:17 EOD squareoff event**, and re-derives its own `today` | ✅ **event-bound, not boot-bound** |
| **strategy governor** | `capital/strategy_governor.py:92-93` | `today_ist()` **at call time** | ✅ **clock-bound** |

⇒ ⭐⭐ **§0's specific hazard — *"Friday's risk controls read Thursday's window"* — is REFUTED at
source for every control it named.** They re-read the clock per call and query by date.

⭐ **And it was already known:** `docs/decisions/DESIGN_midnight_day_floor.md` §B4 (21-Jul) records
the midnight crossing as an investigated question and cites `f1_daily_loss_after_halt_21jul2026.md`
for the date-scoping. ⇒ *"nobody knows"* was **too strong**; the counters were established.

### ⚠️ BUT THE QUESTION FOUND A REAL RESIDUE — IT IS JUST NOT THE COUNTERS

**(S) What IS boot-bound**, and therefore simply **never re-evaluated** by a process that runs on:

1. 🔴 **`main.py:1914` — `kill_switch.clear_stale_state(today_ist_date)`** (date from `:1909`).
   **Boot-only.** ⇒ **no boot ⇒ no auto-clear**: a prior-day kill would still be sitting there
   Friday morning with nothing to clear it. ⭐ **This is the one that actually bites** — and note
   its direction: **entries BLOCKED. Restrictive, i.e. fail-safe, not permissive.**
2. **`main.py:1754` — `today = date.today()`**, the SU6 holiday guard, captured **once** before
   logging is even set up. A process running through midnight **never re-asks whether the new day is
   a trading day.** ⚠️ **Inert for Friday** (Friday is a trading day) — it would bite on a
   Friday→Saturday or a pre-holiday crossing.
3. **`main.py:2404` — `_start_of_today_iso`**, the B5 shared day-floor, and **`initialize()`**
   (FM13, *"called once at startup"*). ⇒ no boot ⇒ Friday would run on **Thursday's capital seed**,
   never re-seeded from `broker.net`.

⇒ 🏷️ **THE CORRECTED CHARACTERISATION:** not *"the risk controls go wrong in an unmeasured
direction"* but **"the counters roll correctly; what is boot-bound simply never re-runs — a stale
kill that cannot self-clear, and a stale capital seed."** ⛔ **This does not make not-stopping safe
— it makes the cost NAMEABLE, which is what §0 asked for.**

---

## §4b — 🔴🔴 THE RESIDUE HAS A PRICE: **NOT STOPPING COSTS FRIDAY**

**The chain, every link measured. ⛔ No inference carries it.**

| # | link | evidence |
|---|---|---|
| 1 | **today's 15:15 breaker FIRED** | **(P)** `15:15:01.115` `order_monitor.force_close_triggered` → `KillSwitchActivated: INACTIVE -> SOFT_KILL reason=circuit_breaker_force_close_15:15` |
| 2 | **the kill is ACTIVE right now** | **(P)** `kill_switch_state` id=1 → `SOFT_KILL │ circuit_breaker_force_close_15:15 │ 2026-08-06T15:15:01.115826+05:30 │ order_monitor` |
| 3 | **both clearers are BOOT-ONLY** | **(S)** repo-wide, all file types, no filter: `clear_stale_state` → **one** production site `main.py:1914`; `auto_clear_scheduled_kill` → **one**, `main.py:1919`. ⭐ Every other hit is tests, docs, or a comment/log string in `deploy/token_watcher.sh` |
| 4 | **no stop ⇒ no boot** | **(P)** service `active` at **19:08:44**, long past the 17:35 self-exit — the carried delivery position defers it (A6, already measured) |
| 5 | ⇒ **Friday opens in `SOFT_KILL`** | **no new entries all day Friday** |

> ### ⭐⭐ **AND THE PROOF IS IN TODAY'S OWN LOG — the same event, one day earlier**
> ```
> 2026-08-06T08:15:02.876 WARNING kill_switch: "Kill switch auto-cleared: prior SOFT_KILL from
> 2026-08-05 (reason=circuit_breaker_force_close_15:15 by=order_monitor) -- new day 2026-08-06
> starts clean (HEADLESS)"
> ```
> ⇒ **Yesterday's 15:15 kill — byte-identical reason string — was cleared by TODAY'S 08:15 boot.**
> 🏷️ **This is not a mechanism I inferred from source; it is the mechanism OBSERVED IN PRODUCTION,
> on the same reason, one day before.**

⭐ **AND IT DISPOSES OF THE OBVIOUS OBJECTION — *"we hold open positions, so it wouldn't clear
anyway."*** **ATULAUTO was OPEN at 08:15:02.876 and the prior-day kill cleared regardless.**
**(S)** why: a **prior-day** kill is handled by `clear_stale_state`, which **ignores open
positions**; the no-open-positions condition belongs to `auto_clear_scheduled_kill`, the **same-day
restart** path. Friday's boot is a new day ⇒ `:1914` fires first. ⇒ ✅ **Friday's boot WOULD clear
it. There just isn't one without a stop.**

⇒ 🏷️ **VERDICT: the trading-day argument is LIVE, not speculative. Not stopping costs Friday's
entries.** ⛔ **The stop remains Rama's to authorise and Rama's to run** — this is a cost, not an
authorisation.

### 🔒 THE FOUR PRECONDITIONS — ⛔ **THIS CONCLUSION IS VOID IF ANY ONE CHANGES**

⭐ **Attached in the AR9 shape (a conclusion carrying its own reopen conditions), because the
alternative is folklore:** *"you have to stop it every night"* becoming a habit whose reason nobody
can name. **Habits outlive their reasons.**

| # | precondition | falsified by | check |
|---|---|---|---|
| 1 | **the 15:15 breaker fires daily** | disabling / rescheduling the `force_close` circuit breaker | today's log: `order_monitor.force_close_triggered` |
| 2 | **BOTH clearers remain boot-only — one production site each** | any scheduled job, loop, route or CLI gaining a call | repo-wide grep, **all file types, no filter**, for `clear_stale_state` **and** `auto_clear_scheduled_kill` — must return `main.py:1914` and `:1919` and nothing else outside tests/docs |
| 3 | **the self-exit stays blocked by an open row** | F6 landing, or `_eod_self_exit_due` gaining a product/phantom filter | `main.py:1073-1113` |
| 4 | **no other path clears a prior-day kill** | a new date-rollover handler, a resume route, an operator CLI | as #2, plus `SCHEDULED_KILL_REASONS` consumers |

⛔ **If any of the four changes, DO NOT re-quote this conclusion — re-derive it.**

> ### 📌 §2.1 — **THE PRODUCTION LOG LINE IS PINNED BESIDE THE SOURCE CITATION, DELIBERATELY**
> The `08:15:02.876` auto-clear line sits **next to** the `main.py:1914`/`:1919` citation above, and
> must stay there. ⭐⭐ **So a later reader cannot mistake this for source-only reasoning when it has
> been DEMONSTRATED IN PRODUCTION** — the same event, the byte-identical reason string, one day
> earlier. 🏷️ **The upgrade this record makes is not a new inference; it is an architectural
> inference becoming an OBSERVED OPERATIONAL PROPERTY.**

### 🔴 AND IT IS A RECURRING OBLIGATION, NOT A ONE-OFF

**If the stop is taken tonight, Friday boots, Friday's 15:15 breaker fires — and on Friday evening
the service refuses to self-exit AGAIN**, because DIFFNKG carries and the phantom persists.
⇒ ⭐⭐ **A MANUAL STOP IS REQUIRED EVERY TRADING NIGHT UNTIL F6 LANDS.**
⚠️ **Friday's stop matters for MONDAY** — the weekend is not a trading day, so a missed Friday stop
costs **Monday**, three days after the mistake, when the cause is hardest to see.
⛔ **No cheaper mitigation exists — cancelling the GTT does NOT work** *(verified at source; the
`abs()` phantom keeps `held == row_qty`, so `cnc_gtt_monitor.py:513` recreates it, or queues it
pre-open for the first in-hours cycle)*. **The only levers are the nightly stop, or F6.**
➡️ **Filed as F6's SIXTH cost and its PRIORITY argument** — `f6_delivery_exit_predicate_design_06aug2026.md` §15.1.

---

## §4c — THE EVIDENCE SNAPSHOT *(ChatGPT's #4, adopted)*

⭐ **Record the evidence WITH the decision, so a later review audits the snapshot rather than
reconstructing it.** Frozen at **19:08:44 IST, 06-Aug-2026**:

| view | value |
|---|---|
| service | `active` (`ExecMainStart` 08:15:02) |
| broker `positions()` | **2** — stamped 19:08:38 |
| broker `holdings()` | **0** — stamped 15:23:40 |
| non-terminal trades | **2** — `trd_e66ee17b…│ATULAUTO│OPEN`, `trd_010f8e21…│DIFFNKG│OPEN` |
| ACTIVE `gtt_state` | **2** — `330657774→ATULAUTO`, `330658430→DIFFNKG`, both `trade_id`-matched |
| non-terminal orders | **0** |
| kill switch | `SOFT_KILL │ circuit_breaker_force_close_15:15 │ 15:15:01.115826 │ order_monitor` |
| reservations, un-released | ATULAUTO **₹587.40** `ee9af41eae554c35` · DIFFNKG **₹446.10** `0184b66d4c214416` |
| ahead / `origin/main` | **92** / `0197923` unmoved |

> ### ⚠️⚠️ **THE CAVEAT THAT MUST TRAVEL WITH THIS SNAPSHOT — ⛔ DO NOT READ THE EQUALITY AS HEALTH**
> **`2 positions == 2 rows` is arithmetically clean, but it holds BECAUSE THE PHANTOM EXISTS ON BOTH
> SIDES** — a phantom broker position row *and* a phantom `OPEN` trade row. ⭐⭐ **An equality that
> holds for the wrong reason is exactly the shape a snapshot must capture**, or the next reader
> reads a matched count as a healthy book. 🏷️ **One of these two positions does not exist.**

---

## §5 — IF RAMA RULES **STOP**: THE SEQUENCE

⛔ **I do not run 5.2. Rama runs it himself.**

1. ✅ **The written basis — DONE: this document, before the act.** (§G6(c))
2. ✅ **Pre-flight (a) — DONE fresh at 19:08:44, all five PASS** (§1). ⚠️ **This measurement goes
   stale.** The position cannot change after 15:30, but if the stop is taken much later, re-run it.
3. ▶️ **Rama runs the stop — ONCE:**
   `! ssh trading-vm 'sudo systemctl stop trading-system.service'`
   ⚠️ **~30 s of no output. ⛔ DO NOT Ctrl-C.** A stop needs no broker call.
   ⛔ **Keep the command ALONE on the line** — on 05-Aug a bracketed aside inside the shell line was
   split by bash into 5 extra unit names, producing **5 loud `Failed to stop …` lines while the real
   unit stopped silently and correctly.**
4. **(c) verify the STATE separately, within 60 s** — ⛔ never read the command's own output as
   evidence of its effect (§G1 #7). **(d) at 90 s:** `inactive` + no start attempt in
   `token_watcher.log`.
5. **(e) the census** — capture-to-file **first**, filter second, **both** sources.
   ⭐ Expect `cnc_gtt_monitor` HIGH; ⛔ **`acted` counts rows EXAMINED, not actions TAKEN.**

## §6 — THE PUSH

⛔ **Cost presented, no recommendation — it is Rama's call.**
⭐ **My view is unchanged: not tonight** — the F6 design now in the tree **understates its subject by
five costs**, and D3 (no push before 18:15) is not the binding constraint here; the understatement is.
⚠️ **91 ahead.** ⛔ **An evening schema push buys a night of CRITICALs** — not applicable to a
docs-only push, but it is why the F6 build was ruled off.

---

*Read-only throughout. No code. Nothing pushed. The service was `active` at 19:08:44 and has not
been touched.*
