# 11 — The absolute rupee kill ladder (drift handler + reconciler tolerances)

**Type:** capital-posture · **Status: ✅ CLOSED 28-Jul-2026 — Option A, LEAVE AS-IS, NO code change.**
**Registered and closed the same day.** Closed by *choosing to change nothing* — the same shape as
decision [09](09_prune_retention.md).

> ⛔ **This file recommends nothing**, per this board's convention. It records what Rama decided,
> what the code actually does, and what was measured — not what anyone would prefer.
> ⛔ **NO VALUE WAS EVER CHANGED.** Not the yaml, not a default, not a test fixture. Proven, not
> asserted: `git diff HEAD -- config/ capital/ core/ orders/ signals/ main.py` was empty and the
> thresholds still read 250 / 1,000 / 2,500 with `consecutive_cycles_before_escalate: 3`.

> ⭐⭐ **THE CLOSURE IS ABOUT THE VALUES ONLY. §3, §3a, §3b, §4 and §7 below are the durable part
> of this file** — several of them CORRECT how the kill path was understood — and three findings
> made underneath this decision **survive it** and are registered separately in
> [`ACTIONS_not_decisions.md`](ACTIONS_not_decisions.md). ⛔ **A closed decision must not bury the
> findings made underneath it.**

---

## 1. ✅ CLOSED — posture AND values. (Rama, 28-Jul-2026 15:04)

> *"I'll stick with existing amounts — ₹1,000 (soft kill) and ₹2,500 (hard kill). Point 3 CLOSED."*

⇒ **OUTCOME: OPTION A — absolute, values UNCHANGED, no code change.**
Options C (capital-relative) and D (hybrid) are closed; a book-vs-broker *discrepancy* is not a risk
fraction, so absolute is the coherent semantics. **Independent review concurs**, with *"do not
reopen unless new production evidence requires it."*
⇒ **Nothing to design, nothing to red-team, nothing to implement, no boot slot needed.**

## 2. ✅ THE VALUES — RESOLVED

| key | value | status |
|---|---:|---|
| `hard_kill_threshold_rs` | **2,500** | ✅ unchanged (Rama, 28-Jul) |
| `soft_kill_threshold_rs` | **1,000** | ✅ **unchanged (Rama, 28-Jul).** ⛔ The ₹1,500 figure proposed earlier the same day is **WITHDRAWN 28-Jul-2026 — not deferred.** *Recorded, not deleted: a withdrawn figure that vanishes gets re-proposed.* |
| `log_only_threshold_rs` | **250** | ✅ unchanged — see §6 Q1 |
| `order_reconciler.human_order_margin_tolerance` | **5,000** | ✅ unchanged — see §6 Q2 |

## 2a. 🔓 REOPEN TRIGGER — and absent one of these, do not re-litigate

Reopen **only** on: **(a)** a material change in capital · **(b)** the ladder **actually firing in
production** (it never has — §4) · **(c)** a realised-drift **distribution becoming obtainable**
(today it is not — §4's limit, registered as its own item).
⚠️ Note (c) points back at the observability gap: **if this ever does need re-tuning, it cannot be
re-tuned on evidence as things stand.**

---

## 3. ⭐ WHAT THE CODE ACTUALLY DOES — read from source, not from the config comments

*Because the code outranks everyone's description of it, including Rama's and mine.*

| rung | fires when | what it DOES | what it does **NOT** do |
|---|---|---|---|
| **NOISE** `< 250` | any drift | `logger.debug` only | **not observable in production** — see §5 limit |
| **LOG_ONLY** `≥ 250` | escalating source | `logger.CRITICAL` + increments a counter | no kill, no alert to Telegram |
| **SOFT** `≥ 1,000` | escalating source | `kill_switch.soft_kill()` — **blocks new ENTRIES, ALLOWS EXITS**; persists to DB (persist-first); CRITICAL log; Telegram **CRITICAL** | does **not** touch exits, pending exit orders, or the EOD squareoff |
| **SOFT_ESCALATED** | 3 consecutive LOG_ONLY cycles | identical to SOFT | — |
| **HARD** `≥ 2,500` | escalating source | `kill_switch.hard_kill()` — blocks **ALL** orders **and dispatches an async FLATTEN** that cancels resting orders and exits `OPEN`/`PARTIAL`/`PENDING_FILL` trades, retrying up to **2 h**, alerting per trade on failure | does **not** leave positions unmanaged — it actively closes them |

**Enforcement is real, not just docstring** — `kill_switch.is_active(intent)`:
`entry`/`any` → blocked on SOFT **or** HARD; `exit` → blocked **only** on HARD. SOFT genuinely
allows exits. On HARD the exit block exists because the kill switch's **own** indestructible
flatten owns the close; it is not an absence of exit management.

⇒ **RAMA'S DESCRIPTION OF BOTH RUNGS IS ACCURATE, in-session.** SOFT = "stop taking new trades /
raise warnings" ✅ (its alert body literally reads *"New signals: BLOCKED | Open positions: managed
to SL/TGT/EOD"*). HARD = "emergency stop" ✅ **and it squares off.**

### 3a. ⚠️ THE ONE DIVERGENCE — bounded, but it must be said before a number moves

A drift kill's reason is `"capital drift SOFT: delta=Rs…"`, which is **not** in
`SCHEDULED_KILL_REASONS` (`{"circuit_breaker_force_close_15:15", "EOD_SQUAREOFF"}`) ⇒ it is an
**EMERGENCY** kill. If the service **RESTARTS ON THE SAME DAY** while that kill is persisted, the
next start hits `StartupScenario.HALT` and **exits 4 — the service does not come back up** without
`--resume`. In that window there is no exit management and **no 15:15 / EOD squareoff**.

⭐ **BOUNDED, and the bound is Rama's own earlier decision.** `clear_stale_state()` is a
**HEADLESS GUARANTEE** (20-Jun-2026): *every* prior-day kill clears at the next boot regardless of
type. Same-day kills deliberately persist within the day. ⇒ **the hazard is a SAME-DAY restart
only — never overnight.** It is not "SOFT_KILL leaves positions unmanaged"; it is "SOFT_KILL + a
same-day restart does".

### 3b. ⛔ `consecutive_cycles_before_escalate: 3` IS NOT A GATE IN FRONT OF THE LADDER

The desk brief asked to "confirm escalation is not single-sample". **It is single-sample.**
Source: the counter increments **only** on `TIER_LOG_ONLY`; `TIER_SOFT` and `TIER_HARD` **reset** it
and dispatch immediately. The 3 cycles govern **only** the LOG_ONLY → SOFT_ESCALATED promotion of a
*sustained sub-soft* drift. **A single ≥₹1,000 sample soft-kills; a single ≥₹2,500 sample hard-kills.**

**Wall-clock of "3 cycles" — and it depends on which source drifts:**
- via `fund_manager_self_check` (reconciler CHECK7, runs every cycle at `poll_interval_sec: 15`) ⇒ **≈ 45 seconds**.
- via `fund_manager` (FM9) ⇒ **never within one process.** `sync_from_broker` has exactly **one**
  production caller — `main.py:995`, a **one-shot at 09:15** — so that source fires **once per day**,
  and the counter is in-memory on an object that dies at the 17:35 self-exit. Three cycles from that
  source alone is unreachable. *(The counter is shared across escalating sources, by design.)*

---

## 4. 📉 THE MEASUREMENT decision #11 said was owed — DONE 28-Jul, read-only

**Search width, stated up front** *(an absence is only established by a check wide enough to have
found the thing)*: **22 files**, `logs/system_2026-06-29.log` → `logs/system_2026-07-28.log`,
**1,539,561 lines**. No `.gz`, no `logs/archive` ⇒ that is the whole on-disk history (~22 trading days).

| question | answer |
|---|---|
| total `drift_handler` lines in 22 days | **2** |
| events from an **escalating** source (`fund_manager*`) | **ZERO** |
| `log_only` (₹250) ever tripped? | **No** |
| `soft` (₹1,000) ever tripped? | **No** |
| `hard` (₹2,500) ever tripped? | **No** |
| any kill `triggered_by=drift_handler`? | **No** — the 19 `SOFT_KILL ACTIVATED` lines are all the scheduled 15:15 / EOD kills |
| `HARD_KILL ACTIVATED` | **0** |

⇒ **THE LADDER HAS NEVER FIRED.**

⭐⭐ **AND THE PART THAT MATTERS MOST.** The only drift ever observed at hard-kill magnitude came
from a source the ladder **deliberately ignores**. Both events, 06-Jul:
```
INFO drift_handler "drift event from non-escalating source"
     source=order_reconciler tier=HARD delta=10000.0 expected=0.0 actual=10000.0
```
₹10,000 = **4× the hard threshold, 200× the ₹50 reconciler tolerance** — logged at **INFO**, no
escalation, by design (`order_reconciler` is not in `_ESCALATING_SOURCES`; it has its own alert
path, and this pair also shows as `G3 CAPITAL_DRIFT … tolerance=50.00`).
⚠️ `expected=0.00` is the tell: that is the *"~zero expected, possible publisher bug"* shape the
handler warns about for escalating sources. It looks like a seeding/startup artefact rather than a
genuine ₹10,000 discrepancy — **unresolved, and worth its own look before it is used as evidence.**

### ⛔ WHAT THIS MEASUREMENT CANNOT TELL YOU — stated, not glossed
1. **There is no realised-drift *distribution*.** `TIER_NOISE` logs at **DEBUG** and production runs
   at INFO ⇒ **every drift below ₹250 is invisible.** Min/median/p95 of normal drift **cannot be
   computed from these logs at all.** Getting it needs a code change (raise the noise tier to INFO,
   or persist drift samples) and then time. **The "cheap and unblocked" framing in the original
   version of this file was wrong** — the *trip* question was cheap; the *distribution* question is not.
2. **`kill_switch_state` is a single-row CURRENT-STATE table**, not a history (1 row; one distinct
   `triggered_by`). It cannot answer "has soft ever tripped" over time. The log grep above is the
   authority, and it only reaches back 22 trading days.

⇒ **Positioning the rungs "against observed noise" is NOT possible today.** Any value chosen now —
₹1,000 or ₹1,500 — is chosen against an unmeasured background, on a path that has never fired.
That is not an argument against Rama's figure; it is the honest statement of what backs it.

---

## 5. ~~THE SLOT~~ — ⛔ STRUCK 28-Jul-2026. THERE IS NO SLOT, BECAUSE THERE IS NO CHANGE.

~~This is boot-path config needing its own single-variable boot … earliest slot is after the 4-Aug
flag flip.~~
⛔ **Struck deliberately rather than left in place.** With the values unchanged there is nothing to
deploy and no boot to schedule. **A live "earliest slot is after 4-Aug" line inside a CLOSED file is
exactly the superseded-instruction hazard** — someone would eventually schedule a deploy for a
change that was withdrawn.

## 6. ✅ BOTH QUESTIONS RESOLVED BY THE CLOSURE — nothing is owed by Rama

**Q1 — `log_only_threshold_rs`: ✅ RESOLVED, stays ₹250.** The question only existed because SOFT
was proposed to move to ₹1,500, which would have widened the quiet band from 250–1,000 to
250–1,500. **SOFT stays 1,000 ⇒ the band is unchanged at 250–1,000 ⇒ there is nothing to answer.**
*(For the record, the band is the quiet zone: drift logs at CRITICAL severity but sends no Telegram
and trips no kill; it becomes a halt only by crossing the soft line or persisting 3 consecutive
cycles ≈45 s via CHECK7 — see §3b.)*

**Q2 — `human_order_margin_tolerance`: ✅ CONFIRMED, stays ₹5,000.** It was never part of this
ladder — it is **alert-only and non-escalating** (`orders/order_reconciler.py:3397` adds it to an
*alert* tolerance, not to the drift ladder) — and Rama's "existing amounts" covers it.
⛔ **Before re-raising the "it widens an escalation path" idea: it is REFUTED on code evidence** —
[`../audit/capital_figure_sweep_28jul2026.md` §3](../audit/capital_figure_sweep_28jul2026.md).
⭐ **The pointer stays even though the decision is closed — that is the entire reason it exists.**
This has now come up three times.

## 7. Constraints any change must respect

- ⛔ **Ordering is enforced at config load:** `log_only < soft_kill < hard_kill`
  (`core/config_loader.py:1599-1620`). A violating re-tune **fails startup** — on this system that
  is a boot that does not start.
- `consecutive_cycles_before_escalate: 3` — see §3b; it is **not** a gate in front of SOFT/HARD.
- **PARITY:** these load identically in paper and live; a change lands in both at once.
- ⚠️ **Only three sources can escalate at all:** `fund_manager`, `fund_manager_self_check`,
  `fund_manager_bucket_overflow`. Everything else (incl. `order_reconciler`) is INFO-only.

**Related:** memory `capital-vocabulary` · `dual-daily-loss-mechanism` · `persisted-kill-is-halt-21jul`
· `killswitch-autoclear-prior-day` · decision [01](01_e4_w10_pnl_contract.md).
