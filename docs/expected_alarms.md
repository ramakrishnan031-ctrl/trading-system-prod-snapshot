# EXPECTED ALARMS — what fires on a HEALTHY day, and what would make it real

**Audience: the operator, at the moment an alert arrives.** Closes IA-XDOCS-05.

⛔⛔ **THE ONE RULE FOR THIS DOCUMENT: it lowers the ALARM, never the CHECK.**
Every entry below carries a **"THIS IS REAL IF…"** line. If the observed alarm does not
match the expected shape *exactly*, it is **not** the entry below — treat it as an
incident and use `05_incident_response.md`. ⛔ **Never reason "it's probably the known
one".** The known one has a signature; check the signature.

**Why this doc exists:** the edge behaviour below was documented only in the audit /
register layer, which made the register the de-facto operator reference — a role it was
never designed for. Measured cost: on **31-Jul**, a healthy and profitable day with **zero
real incidents, the system emitted 49 alerts (18 at WARNING)**. A daily CRITICAL nobody
can explain is how alert fatigue becomes policy.

---

## 1. 🔴 The 15:15 daily circuit-breaker — a **4-line CRITICAL chorus** every day

**You will see, in the log, every trading afternoon:**
`ACTIVE-AT-STARTUP` · `force_close` · `KillSwitchActivated` · `SOFT_KILL ACTIVATED`

All four describe **one designed event**: the scheduled 15:15 circuit-breaker kill.
⚠️ The **same** event's Telegram message is only **WARNING** and writes **no sentinel**
(SK-B) — so one routine event speaks in **three different severity vocabularies**. That
mismatch is the documented defect (IA-P9-01), **not** a second incident.

- **Do:** nothing. The SOFT_KILL persists overnight **by design** and **auto-clears at the
  next 08:15 boot** (`clear_stale_state`).
- ⛔ **Do NOT** run `resume.sh` for this one — only a **same-day EMERGENCY** kill needs it.
- **THIS IS REAL IF:** the kill's `triggered_at` **date is TODAY and the time is not
  ~15:15**, or the trigger reason is anything other than the scheduled breaker. Then it is
  an emergency kill → `05_incident_response.md`.

### 1a. ⛔ The 18:45 EOD report used to TELL YOU to run `resume.sh` — ledger #8c

**You will see, in the 18:45 `system_manager` EOD report under 🔮 TOMORROW READINESS:**
`Kill switch: SOFT_KILL (circuit_breaker_force_close_15:15) from <date> — prior-day at the
next open, so the <date> 08:15 boot auto-clears it (HEADLESS GUARANTEE). No action needed.`

**Why this section exists at all.** Until 03-Aug-2026 that same line read *"needs
`deploy/resume.sh` before market open"* — for **any** non-INACTIVE state. It was **wrong
every single trading day**, and it is the surface an operator reads at night, so it
contradicted §1 above with an instruction. §1 stated the principle but **never named
`system_manager`**, so nothing connected the prohibition to the instruction. Fixed in the
code (`tomorrow_readiness_check`); this entry closes the doc half.

- **Do:** nothing. It is now an **INFO** line, not a warning.
- ⭐ **The discriminator is the DATE, not the reason.** `clear_stale_state`
  (`kill_switch.py:284-297`, called `main.py:1902`) clears **ANY** kill dated strictly
  before the boot date — **SOFT or HARD, scheduled or emergency**. The next trading day is
  always after the report's day, so a kill visible here always auto-clears.
  ⛔ Do **not** reach for `SCHEDULED_KILL_REASONS` here — that governs the **same-day
  restart** path (`auto_clear_scheduled_kill`), a different question.
- **THIS IS REAL IF:** the line is a **⚠️ WARNING** rather than INFO — i.e. it says the kill
  is **not** dated before the next trading day (clock skew / a future-dated row), or that
  `triggered_at` was **unreadable** so auto-clear could not be confirmed. Both mean the kill
  will survive the boot → `05_incident_response.md`, and `resume.sh` **is** then correct.

### 1b. ⏱️ The 08:15 boot emits the **CRITICAL first and the auto-clear 4 ms later**

**You will see, in the log at every 08:15 boot that follows a 15:15 breaker day — in this
order:**

```
08:15:10.594  CRITICAL  kill_switch  KILL SWITCH ACTIVE AT STARTUP: state=SOFT_KILL
              reason=circuit_breaker_force_close_15:15 triggered_by=order_monitor
              -- operator must call resume() to clear (Audit Issue #18 fix)
08:15:10.598  WARNING   kill_switch  Kill switch auto-cleared: prior SOFT_KILL from
              2026-08-03 ... -- new day 2026-08-04 starts clean (HEADLESS)
```

⛔⛔ **The CRITICAL is emitted BEFORE the auto-clear, and its text is wrong by the time
you read it.** The startup check reports the state it found; `clear_stale_state` then
clears it ~4 ms later. **`operator must call resume()` is stale the instant it is
written** — nothing calls `resume()` on this path and nothing needs to.

This is the *log* half of §1a. §1a fixed the 18:45 report, which is the surface an
operator reads **at night**; this is the surface read **in the morning**, and it still
carries the old instruction because it is a factual record of the pre-clear state.

- **Do:** read **both** lines. The pair is the event; the CRITICAL alone is misleading.
- ⛔ **Do NOT run `deploy/resume.sh`.** Measured 04-Aug-2026: the gap was **4 ms**.
- ⭐ **The discriminator is the same as §1a — the DATE.** The auto-clear line names the
  date it cleared *from*; if that date is **before** the boot date, this is the designed
  prior-day path.
- **THIS IS REAL IF:** the `Kill switch auto-cleared` line **does not appear in the same
  boot**, or the kill's date **is the boot date** (a same-day emergency kill). Then the
  kill survives, `resume.sh` **is** correct → `05_incident_response.md`.

## 2. 🗄️ The migration-window night — a storm of cron CRITICALs after a schema push

**You will see:** `Schema migration refused (non-boot process)` / `MIGRATION_REFUSED
schema vNN -> vNN+1 pending`, repeatedly, from many different cron jobs, through the night
following an evening push that moved the schema.

**Why:** only `main.py`'s **off-market boot** may migrate. Every other opener — the ~33
heartbeat-writing crons, monitors, reports — refuses and aborts **by construction**, until
that boot happens. **It self-heals at the next off-market 08:15 boot.**

⭐ **Measured directly on 03-Aug 10:30**, when a PC boot was attempted mid-session:
`MigrationNotPermitted: schema v44 -> v45 pending … (allow_migrate=True, market_open=True)`
— **even the sanctioned boot path refuses while the market is open** (guard AC2). The
refusal happens *before* any write; nothing is corrupted.

- **Do:** confirm a schema push actually happened that evening. Then wait for the boot.
- ⛔ **Do NOT** hand-migrate the DB. The error says *"No manual DB surgery"* and it means it.
- **THIS IS REAL IF:** the CRITICALs continue **after** a successful off-market boot, or
  no schema push happened. Then the version mismatch is unexplained.

## 3. 🧺 Delivery / T2-class artefact alerts

**You will see (once delivery holdings exist):**
- `Orphan GTT` **WARNING** the morning after an overnight CNC leg — the never-run FIX-183
  prepass sees T2's own GTT. **Expected, not a finding.**
- `MISSING_AT_BROKER` / `ORPHAN_AT_BROKER` **CRITICAL** from `reconcile_positions.py`
  (`:13-14`, `:265-268`).

**Why the reconciler ones:** it reads `positions()` **only** and is **blind to delivery at
T+1** — a healthy holding that has moved to `holdings` reads as *"in system, not at
broker"*. It will fire **daily on a healthy book** until the D-4(a) scope-to-intraday
redesign ships.

- **Status today:** `MISSING_AT_BROKER` is **latent — it has never fired** and cannot until
  a delivery trade is `OPEN` in the live DB. **T2's basket produces the opposite
  direction** (`ORPHAN_AT_BROKER`). ⇒ before the carry pilot, either string is
  **unexpected** and worth investigating.
- **THIS IS REAL IF:** it names a symbol you are **not** knowingly holding as delivery, or
  a quantity that does not match the holding.

### 3a. 💰 `⚠️ Capital Drift Detected` **CRITICAL** from `order_reconciler` — ⭐ **NEW 05-Aug, and it fired the day delivery went live**

**You will see:** a `CRITICAL — [LIVE] Capital Drift Detected` email, `Module: order_reconciler`,
carrying `expected` · `actual` · `delta` · `tolerance` · `base_tolerance` · `human_orders`.
**Repeat alerts are throttled to one per 30 minutes** (`capital_drift_alert_interval_sec: 1800`),
so several in a morning is **one episode, not several problems.**

**Why it is expected on a delivery day — and this is the whole point:** the check compares
**`expected` = your TOTAL capital** (`snapshot.total`; reservations reduce the *available* buckets,
not the total) against **`actual` = the broker's FREE CASH** (Kite's `equity.net`, which is **net of
blocked margin** — `order_reconciler.py:3590-3591`, `zerodha_adapter.py:1452`). ⇒ **the two
legitimately differ by whatever is currently deployed in positions.**
The in-session tolerance is **`max(₹50, 10% of expected)`** (`:3629-3631`,
`system_config.yaml:368-369`), and **FIX-190 (Bug I) added that 10% band to silence exactly this
noise — for an INTRADAY book.** ⛔⛔ **Intraday runs ~5× levered, so blocked margin is a fraction of
position value and stays inside 10%. DELIVERY RUNS AT 1× AND BLOCKS THE FULL PURCHASE VALUE** ⇒ **a
delivery book deploying more than ~10% of capital breaches a 10% band BY CONSTRUCTION**, and the
positional bucket is **30% of total.** ⛔ **There is no delivery-specific tolerance** (width:
`capital_drift_tolerance` across all tracked `.py`/`.yaml` — only a global rupee floor and a global
percentage; zero variant hits).

- **⛔ IT CANNOT KILL ANYTHING, AND THAT IS STRUCTURAL — VERIFIED IN SOURCE:** the event is published
  with `source_module="order_reconciler"` (`:3667`), and `drift_handler._ESCALATING_SOURCES`
  (`:66-70`) contains **only** `fund_manager`, `fund_manager_self_check` and
  `fund_manager_bucket_overflow`. A non-escalating source logs one INFO line and **returns**
  (`:147-161`) — before any tier, counter, `soft_kill` or `hard_kill`. ⇒ **informational by
  construction.**
- **THIS IS REAL IF** *(any one of these — ⛔ the arithmetic in the alert itself proves nothing, see
  below)*:
  **(i)** on the Kite Funds page, **available margin + used margin ≠ opening balance** — the broker's
  own books do not balance; **or**
  **(ii)** `opening balance − actual` does **not** match the broker's **used margin** read at
  **the same time** — the gap is not deployed capital; **or**
  **(iii)** `expected − opening balance` is **not** a small figure matching the day's booked P&L; **or**
  **(iv)** `human_orders` is **non-empty** — someone traded manually on the account; **or**
  **(v)** it fires **outside market hours** with `actual` **not** `0.0` (the overnight `net=0.0` case
  is separately suppressed at `:3605-3616`); **or**
  **(vi)** `kill_switch_state.state` is anything other than `INACTIVE` with **today's**
  `triggered_at`.
- ⛔⛔ **DO NOT VERIFY IT WITH `delta == (opening − actual) + (expected − opening)`.** That identity
  is **always true** — `opening` cancels, leaving `delta == expected − actual`, which is the
  definition of `delta`. **It closes to the paisa for any value of `opening`, including a wrong one.**
  ⭐ **A check that cannot go red is not a check** — use (i)–(iii), which compare against something
  independent.
- ⏰ **AND CAPTURE THE BROKER MARGIN WHILE IT EXISTS:** `get_margins` is called **~2,225×/day and its
  response value is persisted ZERO times** ⇒ what was actually blocked is **gone tomorrow**. Worksheet:
  `docs/audit/ADDENDUM_capital_drift_05-Aug-2026.md`.
- 📌 Registered: `MASTER_PENDING` **§B#7** (fourth member of the two-pipeline coupling family — and
  the first that did **not** wait to be predicted) and **§B#5** (DH1's third instance, first with real
  delivery positions). ⛔ **No fix and no tolerance change is authorised.**

## 4. 🚪 "The service didn't start" — the exit-code matrix, and where it is SILENT

| exit | meaning | how loud |
|---|---|---|
| **3** | startup check failed | watcher sends **WARNING**-tier Telegram only |
| **4** | HALT — a kill is persisted | watcher sends **WARNING**-tier Telegram only; needs `resume.sh` |

⛔⛔ **The gap that matters: exit-3/4 alerts are WARNING-tier (TG4 — no sentinel, no email,
drop-on-failure), and `RestartPreventExitStatus=3 4` means systemd will NOT bring the
service back.** So a failed start is **quiet by design**, and the process stays down.

⚠️ **Two silent windows, both known:**
- an **emergency HALT after 16:00** gets no CRITICAL-grade notification until **09:00 the
  next day**;
- the **liveness probe stops at 16:00 while the service runs to 17:35** — ~95 minutes
  unwatched every trading day. **NO ALERT ≠ HEALTHY in that window.** The manual ~17:10
  check is the compensating control.
- a **HUNG-but-active** process has **no external detector at all** (the probe reads unit
  state only).

- **THIS IS REAL IF:** you see no morning start at all, or 0 trades with a LIVENESS-DOWN.
  ⚠️ A night `inactive (dead)` / exit-0 **while flat** is the designed self-exit, not a fault.

## 5. 🆕 New from the 03-Aug push — expect these from Tuesday's boot

| what you will see | why it is expected | THIS IS REAL IF |
|---|---|---|
| `REJECTED_NOT_MIS_TRADABLE (shadow)` WARNING | `mis_filter` runs in **shadow**: log-only. **Nothing was dropped.** Live since Mon 3-Aug. | it appears **without** `(shadow)` — that would mean it is enforcing |
| `SPARED delivery position … product=CNC` **CRITICAL** | ledger #2: a HARD_KILL now **spares** delivery (Q4). The CRITICAL is deliberate loudness, not a fault | a HARD_KILL fired at all — **it never has**. That is the real event, not the spare |
| `UNKNOWN PRODUCT … flattening LOUDLY` **CRITICAL** | ledger #2: a NULL/unrecognised product is flattened **and** announced, replacing a silent fallback | always worth reading — it means a product outside `{MIS, CO, CNC}` reached the book |
| `INFLIGHT_ORPHAN_SPARED_DELIVERY` / `INFLIGHT_ORPHAN_REFUSED_CO` | #2b / #2c-R. **Bounded to ~3 CRITICALs** by CHECK6's 3-cycle FIX-B, not an endless stream | the stream does **not** stop after ~3 cycles |
| `🌳 DEPLOYED TREE vs HEAD` in the EOD report | ledger #10 check 12 — **new, and normally a one-line ✅** | it reports drift or an untracked `.py`: the running code is not the audited code |

⛔ **The `(shadow)` suffix is load-bearing.** If someone reads the `mis_filter` line as
enforcing, they will "fix" a working system.

### 5a. ⏳ `CRON INTEGRITY WARNING` naming the 4 claude heartbeats — ⛔ **EXPIRES AT THE NEXT PUSH**

⏳ **THIS ENTRY HAS A CLEAR-CONDITION. Delete it once met — an "expected" entry with no
expiry becomes permanent noise.**

**You will see, from `check_cron_drift` (`0 18 * * 1-5`, next run 18:00):**
`[LFL836] CRON INTEGRITY WARNING` listing `claude_heartbeat_0530 / _1031 / _1532 / _2033`
as enabled in the registry but absent from the live crontab.

**Why.** The 4 dead claude heartbeat jobs were removed from the **live crontab** on
04-Aug ~02:12 (160→148 lines, 0 claude refs). The commit that removes them from the
**registry** — `71f331b` — is **not yet pushed**, so the VM's
`config/cron_registry.yaml` at `4149263` still declares all four (49 jobs). Live and
registry therefore genuinely disagree, and the check is **correct to say so**.

⭐ **It arrives as WARNING and not CRITICAL for exactly one reason: those four carry
`personal_tooling: true`.** `check_cron_drift.py:123-127` routes an absent-from-live job
to `absent_warn` when that flag is set and to `absent_critical` when it is not. **That
flag is load-bearing** — it is what makes it safe to retire a personal-tooling job from
live ahead of the registry push. ⚠️ They are the **only** four jobs carrying it.

- **Do:** nothing.
- ✅ **CLEAR-CONDITION — delete this entry when:** `71f331b` has ridden a push and the VM
  registry reads **45 jobs, 0 claude**. The WARNING stops by itself; no action clears it.
- **THIS IS REAL IF:** the list names **any job that is not one of those four**, or it
  arrives as **CRITICAL** rather than WARNING. Either means a non-personal-tooling job is
  missing from live — a real scheduling gap → `05_incident_response.md`.

## 6. ⚠️ The daily WARNING stream — six that fire, and are not incidents

**Why this section was added (03-Aug, ledger #9 Step 1).** §1–§5 covered the *structural* alarms
but none of the **WARNING traffic that actually arrives**. Measured across the two days for which
a send-side audit trail exists: **31-Jul = 49 alerts (18 WARNING) · 03-Aug = 84 alerts (8 WARNING
+ 1 `WARN`)** — and **six of the nine non-INFO alerts on 03-Aug were not listed anywhere in this
document.** Combined with the closing rule below (*"an unlisted alarm is an incident until proven
otherwise"*), this doc was instructing you to treat most of a normal day's WARNINGs as incidents.
Record: `docs/audit/ledger_3a_9_5_step1_03aug2026.md`.

⛔⛔ **READ FIRST — WARNING TIER IS NOT DURABLE.** Measured on both audited days: **zero of the
non-INFO alerts wrote a sentinel.** Everything at WARNING is Telegram-only, no email, dropped
silently on send failure (TG4). ⇒ **the absence of a WARNING proves nothing**, and you cannot go
looking for one after the fact. Only CRITICAL leaves a durable trace.

### 6a. 🩳 `Naked untracked position — <SYM>` — ⛔ **the one that has been FALSE every time it fired**

**You will see:** *"Untracked/operator position SYM qty=N avg=X — no local trade and NO protective
stop at the broker. … If unexpected, check for a manual order or a system anomaly."*

⛔ **The detector is GTT-blind (IA-P5-06).** All five that fired on 31-Jul were **T2's own CNC
basket, deliberately held, each with a verified GTT at the broker.** The line *"NO protective stop
at the broker"* was false 5/5, and its instruction sends you hunting a manual order that does not
exist.

- **Do:** check the symbol against your known delivery holdings **first**. If it is one of them,
  a GTT is its stop and this alert is blind to it.
- ⛔ **Do NOT** start an incident on the words "no protective stop" alone — this detector cannot
  see GTTs.
- **THIS IS REAL IF:** the symbol is **not** a delivery holding you know about, **or** the
  quantity does not match the holding. Then it is a genuine untracked position →
  `05_incident_response.md`.

### 6b. 📉 `SLIPPAGE GUARD — <SYM>` — the most frequent WARNING, and it is **correct**

**You will see:** *"Order aborted: BUY | … Trigger: ₹X | LTP: ₹Y | Slip: ₹Z"* — 3–4 on a typical
day.

This is the guard **working**: the price moved past tolerance between signal and placement, so the
order was refused before it reached the broker. The trade appears as `REJECTED`. No money moved.

- **Do:** nothing. It is a protective refusal, not a failure.
- **THIS IS REAL IF:** the count is far outside the normal handful — a sustained run suggests a
  stale feed or a signal source firing on old prices, not slippage.

### 6c. 🏷️ `Sector data-quality: high UNKNOWN rate` — daily, correct, and **you cannot act on it**

**You will see, once per session:** *"N/M (96%) trades this session resolved to UNKNOWN sector …
the sector concentration cap is operating on incomplete data; check instruments.csv…"*

The statement is true and will stay true: `instruments.csv` carries sector for **142 of 2,228**
symbols, so the sector cap runs on mostly-UNKNOWN input (which fails **closed** — pooled, never
waved through). Improving it is a reference-data project, not a runtime action.

- **Do:** nothing at the time. It is one-shot per session and cannot be silenced by anything you
  do during the day.
- **THIS IS REAL IF:** it **stops** appearing on a normal trading day — that would mean the
  sector source changed, which nobody planned.

### 6d. ⏸️ `STRATEGY PAUSED -- <name>` — real, protective, and ⚠️ **a restart does not simply clear it**

**You will see:** *"Daily loss N exceeded 2x avg daily loss (M). Strategy paused for the rest of
today — UNLESS the service restarts…"* followed by the two restart branches.

The pause is genuine and other strategies keep trading.

⚠️⚠️ **THIS ENTRY WAS ITSELF WRONG UNTIL 04-Aug-2026 AND THE CORRECTION IS THE POINT.** It used to
say flatly *"Restart the service and the strategy resumes."* That is **false before the cutoff** —
it replaced one wrong duration claim with a wrong claim in the **opposite direction**. Measured:
`check()`'s cutoff guard (`strategy_governor.py:63-65`) returns **before any P&L computation**, and
the pause set is in-memory (`:42`). So a restart has **two** outcomes, not one:

| restart time | what actually happens |
|---|---|
| **before `cutoff_time`** (default **12:00**) | the loss is **re-derived** and the strategy **RE-PAUSES** — it self-heals |
| **at/after `cutoff_time`** | the breaker **cannot fire at all**, so the strategy **RESUMES** for the remainder of the session **however large the loss** |

⇒ With the entry window `[10:00, 15:00)`, the resuming branch covers **3 of the 5 entry hours**.

⏳ **The alert text is an INTERIM (D3).** D3 ruled the **BEHAVIOUR** is what is wrong — the pause
should persist, and `cutoff_time` should stop doing two jobs (*"may I CREATE a pause now?"* vs
*"is this strategy CURRENTLY paused?"*). ⛔ **Do not read the reworded alert as #9 being closed.**

- **Do:** nothing on the alert itself. It is the per-strategy circuit breaker doing its job.
- ⛔ **Do NOT** restart the service to "clear" something else **at/after the cutoff** without
  knowing it un-pauses every strategy paused today, permanently for that session.
- **THIS IS REAL IF:** several strategies pause in quick succession — that is a market-wide or
  system-wide loss pattern, not a single-strategy breaker.

### 6e. 🧾 `⚠️ Config Changed Since Last Session` — expected after **every** push

**You will see, at the 08:15 boot following an evening deploy:** *"Files: …"*

⚠️ Note its severity is spelled **`WARN`**, not `WARNING` — the only alert in the system that does.
Both route identically today; the spelling is a known inconsistency, not a different tier.

- **Do:** confirm the listed files match what was deployed the previous evening.
- **THIS IS REAL IF:** it names files **no push touched** — the deployed tree then differs from
  HEAD, which is ledger #10's check 12 territory.

### 6f. ⏰ `EOD SQUAREOFF IN ~30 MIN` — only when you still hold something

**You will see at 14:45**, listing each open position, **only if positions are open.** Silence
here means flat, not broken.

- **Do:** nothing, unless you want to exit manually before 15:17.
- **THIS IS REAL IF:** it lists a position you did not expect to still be open.

## 7. ✅ The 15:15 pair now NAMES the delivery carve-out — corrected 03-Aug

**From Wednesday's boot you will see, in both alerts:**

> `CIRCUIT BREAKER — Force Close` —
> *"15:15 circuit breaker fired: pending entry orders cancelled.
> EOD squareoff closes **INTRADAY (MIS/CO)** positions at 15:17.
> Delivery (CNC) is carried by design (EOD6) — not squared off."*

> `SOFT KILL — scheduled (…)` —
> *"Reason: …
> New signals: BLOCKED | **Intraday** positions: managed to SL/TGT/EOD
> Delivery (CNC) is carried by design (EOD6) — not squared off."*

**Why they changed.** Until 03-Aug they read ~~*"EOD squareoff will close **all** positions at
15:17"*~~ and ~~*"Open positions: managed to SL/TGT/**EOD**"*~~. Both were correct only while
delivery was impossible. Verified at source — `eod_squareoff.py:23 / :34 / :1073`: **EOD6 —
DELIVERY (CNC) positions are NOT touched.** A CNC leg is *deliberately carried overnight*, so from
the flip onward both statements would have been **false every single afternoon**, in the two
alerts an operator reads at exactly the moment positions are being closed. Corrected by Rama's
ruling on the flip push; pinned by `tests/unit/test_kill_alerts_delivery_carveout.py`, which
asserts the *claim* (no unqualified "all positions"; the managed set scoped to intraday; the
carve-out named) at **both** sites rather than the wording at one.

- **Do:** nothing. **A delivery holding surviving 15:15, 15:17 and the residual sweep is the
  designed behaviour** — ledger #2 and the Q4 ruling behind it.
- ⭐ **The two now agree, deliberately.** They land seconds apart; if you ever see them give
  *different* answers about delivery, one has been edited without the other.
- **THIS IS REAL IF:** a **delivery** position is actually gone after 15:17, or an **intraday**
  position survives it. Either is the opposite of the designed behaviour →
  `05_incident_response.md`.

---

## What this document does NOT cover

- Anything not listed above. **An unlisted alarm is an incident until proven otherwise.**
- The **absence** of an expected alarm. A *missing* `forward_shadow_record` heartbeat on a
  trading day means **DEAD**, not "a quiet day".
- Any alarm during the 16:00–17:35 unwatched window, where silence proves nothing.

**Related:** `05_incident_response.md` (what to do when it *is* real) ·
`03_daily_operations_runbook.md` · `RUNBOOK.md` · `disaster_recovery.md`.
