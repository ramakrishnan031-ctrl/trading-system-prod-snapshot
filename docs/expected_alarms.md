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

---

## What this document does NOT cover

- Anything not listed above. **An unlisted alarm is an incident until proven otherwise.**
- The **absence** of an expected alarm. A *missing* `forward_shadow_record` heartbeat on a
  trading day means **DEAD**, not "a quiet day".
- Any alarm during the 16:00–17:35 unwatched window, where silence proves nothing.

**Related:** `05_incident_response.md` (what to do when it *is* real) ·
`03_daily_operations_runbook.md` · `RUNBOOK.md` · `disaster_recovery.md`.
