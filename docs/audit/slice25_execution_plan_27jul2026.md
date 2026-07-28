# Slice 2.5 — execution plan

**Written 27-Jul-2026 (Monday), market open, from source only.**
⛔ **A PLAN. Nothing enabled, nothing flipped, no flag touched, no script run.**
Branch `hold-check1-w8-26jul` @ `d3fa5b8`, 24 commits unpushed.

> ⛔ **Uncommitted today, deliberately** — the Monday card asserts 24 commits. Commit with the §C
> audit doc after tonight's push.

---

## 0. Three things that changed since the 26-Jul decomposition

**0.1 — One of the two "NOT STARTED" pieces is done.** #15 (paper CNC product fidelity) was fixed
26-Jul `f7eedd3`. **One not-started piece remains — #16 — and it is really two separable items**
(§7).

**0.2 ⭐ — T2 needs no push, no flag, and no deploy. Verified by hash today:**

| copy | sha256 | what it is |
|---|---|---|
| `/home/ubuntu/t2_proof_run/t2_cnc_gtt_realtest.py` | `a5779420…` | ⭐ **the repaired script — what the canary runs** |
| branch `fix-t2-repair-07jul` | `a5779420…` | identical, unpushed by design |
| deployed tree `scripts/t2_cnc_gtt_realtest.py` | `23aca731…` | ❌ **the OLD bit-rotted one** |

`_build_live_adapter` passes **`delivery_enabled=True` on its own adapter instance** (`:144`, "the
gated exception"), writes to a throwaway DB, and never reads the live delivery flags.
⇒ **the T2 pair is independent of tonight's push and of every config flag.**

> ⚠️ **HAZARD — the flag is not the fingerprint.** The old script *also* has `--arm-overnight`
> (it predates the repair). Seeing the flag in `--help` proves nothing. **Check the sha256, and run
> the `/home/ubuntu/t2_proof_run/` copy — never `scripts/` from the deployed tree.**

**0.3 ⭐⭐ — the overnight leg is NOT isolated from the live service, and that was not on the list.**
T2's DB is isolated; **the broker account is not.** `CncGttMonitor` is constructed
**unconditionally** (`main.py:2686`, not gated on `delivery_enabled`) and wired into the reconciler
(`:2713`), and `adopt_orphan_gtts()` runs as a prepass on **every** reconcile cycle
(`order_reconciler.py:668-684`). It calls the real `get_gtts()` — and GTT ops are deliberately
**not** gated on `delivery_enabled` (`zerodha_adapter.py:683,734,766`, *"protection must survive
disablement"*).

⇒ **On the morning after arming, the live service sees T2's GTT.** Traced through
`cnc_gtt_monitor.adopt_orphan_gtts`: ACTIVE GTT → no `gtt_state` row → `_delivery_open_trades_by_symbol()`
returns 0 candidates → `_warn_adopt_once` → **one `WARNING` alert, "Orphan GTT — no open delivery
trade (IDEA)", deduped once per gtt_id per process.**

**This is predicted, benign, and must not be read as a failure.** Three source facts make it safe:
- severity is **WARNING**, not CRITICAL, and the code comment names this exact case ("benign human GTTs that match the 0-trades case");
- adoption **only ever INSERTs a row or WARNs — it never deletes a live GTT**;
- `_orphan_sweep_and_cap` **never touches a GTT with no `gtt_state` row** (FIX-182 discipline) ⇒ **the live service cannot delete T2's protection.**

⭐ **Bonus worth banking: this is the first real execution of piece #8 (FIX-183 adoption), free, on a
1-share position.** The decomposition scheduled #8 for the carry pilot. It gets exercised two stages
early.

---

## 1. The calendar and the gating chain (A2)

**No weekday NSE holiday between now and 14-Sep-2026.** (`config/nse_holidays_2026.yaml`: nothing
between 26-Jun and 14-Sep; 15-Aug-2026 falls on a Saturday.) So the only non-trading days in this
window are weekends — the calendar is unusually clean, which is worth using.

### The chain, stated so no one assumes a free day

```
Mon 27-Jul  observation day (today) ─── clean? ──► push tonight
                                          │ not clean → push waits, everything below slips 1 day
Tue 28-Jul  v45 migration boot ─── clean? ──► sequence may start
                                          │ migration fails → service does NOT start; all of Slice 2.5 blocked
Wed 29-Jul  EARLIEST T2 arm day
Thu 30-Jul  T2 close  →  DDPI PROVEN
Fri 31-Jul  desk work only  (⛔ never an arm day — see below)
Mon  3-Aug  ⛔ BLOCKED — first boot after the 2FA seed move
Tue  4-Aug  flag flip + carry pilot D1     ◄── the irreversible step (§6)
Wed  5-Aug  carry pilot D2 (the boot-rehydration proof)
Thu  6-Aug  carry pilot D3 / close  →  EARLIEST COMPLETION, zero redos
```

**Three constraints behind that shape:**

1. ⛔ **Friday is never an arm day.** The pair must not straddle a weekend — a Fri→Mon carry holds a
   real position through three unsupervised calendar days for no extra evidence.
2. ⛔ **Mon 3-Aug is blocked, and this collision is not currently on anyone's board.** The 2FA seed
   move is scheduled *"Fri eve or Sat only, never the night before a trading day"* → **Fri 31-Jul /
   Sat 1-Aug**, and the re-enrol is **one-way with no rollback**. Monday 3-Aug is therefore the
   first boot on a new enrolment. ⭐ **This morning is the precedent**: the rotated TOTP was
   "verified offline" and its broker acceptance stayed unproven until the 08:15 boot answered. A
   one-way credential change earns its own single-variable day.
3. **Tue 28-Jul cannot carry a second variable.** v45 rebuilds the live capital-bearing `trades`
   table, and a failed migration raises out of `StateStore` init — **the service does not start.**

⇒ **Earliest completion Thu 6-Aug with zero redos.** §3 explains why zero redos is not the number to
plan around.

---

## 2. Day by day (A1)

### Stage 1 — T2 `--arm-overnight` · earliest **Wed 29-Jul**

> ## ⛔ STEP ZERO — RUN THIS FIRST, BEFORE ANYTHING ELSE ON THE T2 DAY
>
> ```bash
> sha256sum /home/ubuntu/t2_proof_run/t2_cnc_gtt_realtest.py
> # MUST print exactly:
> # a5779420d652c60678cecab400c64cc2e36680865f0052d92c78e1d76f006202
> ```
>
> **Any other hash — including `23aca731…` — STOP. Do not run it. That is the bit-rotted copy
> and it will burn the market day.**
>
> ⚠️ **`--help` is NOT a check.** The bit-rotted script also has `--arm-overnight`. The flag is
> not the fingerprint; **the hash is.**

| | |
|---|---|
| **enabled** | nothing. No flag, no restart, no deploy. |
| **Rama does** | runs `/home/ubuntu/t2_proof_run/t2_cnc_gtt_realtest.py --arm-overnight --i-understand-this-places-a-real-cnc-order`, supervised, market hours (script self-guards `09:15–15:30`). Verify sha256 `a5779420…` first. |
| **observed** | real CNC BUY 1 share fills · real OCO GTT placed · `get_gtt` shows two SELL legs, product CNC, qty 1 · ACTIVE `gtt_state` row in the throwaway DB · **position deliberately held** |
| **PASS** | exit 0 **and** the broker shows 1 share held **and** the GTT ACTIVE at close |
| **capital** | ~₹10 (1 IDEA share) + ~₹0.2 costs |

> ⚠️⚠️ **THE TRAP IN THIS STAGE — read the source, not the exit code.** `run_single_session` sets
> `intentional_hold = True` **before** evaluating the result:
> `if arm_overnight: intentional_hold = True; … return 0 if ok else 1`.
> The `finally` block then skips ensure-flat *and* skips GTT deletion because `intentional_hold` is
> set. ⇒ **exit 1 from `--arm-overnight` still means you are holding stock overnight.** It does not
> mean "nothing happened". **Always confirm the broker position and the GTT by hand before walking
> away**, whatever the exit code says.

### Stage 2 — T2 `--close-overnight <gtt_id>` · next trading day, **Thu 30-Jul**

| | |
|---|---|
| **first** | ⭐ at ~08:20 expect **one WARNING**: *"Orphan GTT — no open delivery trade (IDEA)"*. Predicted (§0.3). **Not a finding.** Its absence is the more interesting result — it would mean the adoption prepass did not run. |
| **Rama does** | `--close-overnight <gtt_id>`, supervised |
| **observed** | CNC SELL from **holdings** (a genuine demat debit) polls `COMPLETE` **with zero manual TPIN intervention** → GTT deleted only after the sell completes |
| **PASS** | `COMPLETE` + no TPIN prompt ⇒ **DDPI PROVEN — piece #5 closed** |
| **FAIL** | sell REJECTED ⇒ DDPI/TPIN unauthorised. The script **keeps the GTT** and flags manual handling — by design. See §3. |

### Stage 3 — flag flip + live-carry pilot · earliest **Tue 4-Aug**, ≥2 consecutive days

| | |
|---|---|
| **enabled** | ⭐ **the four flags** — `delivery_enabled=true`, `force_intraday_only=false`, `trade_type=DELIVERY\|BOTH`, **and `conditional_allocation_enabled=true`** (§6) |
| **D1 observed** | the *service's own* path places a real CNC entry and its OCO GTT · a live `gtt_state` row appears for the first time ever · `CncGttMonitor` 15-min reconcile runs against a non-empty set · delivery caps bind (3 / 5) |
| **D2 observed** | ⭐ the point of the pilot: **08:15 boot rehydrates the carried GTT** (`hydrate_from_store()`, `main.py:2616` — has returned 0 at every boot in the system's history) · the reconciler's delivery exclusion keeps the carried trade away from CHECK1 · the 15:17 EOD squareoff leaves the CNC alone |
| **PASS** | an unbroken carry across a real boot, with capital accounted correctly at both ends and no false SOFT_KILL |

⚠️ **The top first-run risk sits in stage 3 D1, not D2:** conditional capital allocation re-splits
capital for **every** position, intraday included — it is the only never-run piece that touches the
part of the system currently working and earning, and it has **no test coverage and no runtime
guard**. Second is `CncGttMonitor`, which **can SOFT_KILL** (M2: >1 ACTIVE GTT for one trade) — a
first-run false positive stops the trading day.

---

## 3. If a day fails: restart or resume? (A3) — the question that sets the real cost

**Answered per stage. The short version: only desk work resumes. Every live stage restarts.**

| stage | failure | restart or resume | calendar cost |
|---|---|---|---|
| 1 | CNC buy REJECTED — nothing held, script aborts before cleanup is needed | **RESUME** | 1 day |
| 1 | buy fills, GTT placement fails — `finally` ensure-flat squares it, ends flat | **RESUME** | 1 day |
| 1 | buy fills, GTT places, **verification fails but position is held** (the trap above) | **RESUME** — but you are carrying stock; close it next day before re-arming | 1 day |
| 2 | ⭐ **sell REJECTED — DDPI/TPIN unauthorised** | ⛔ **RESTART stage 1** — a same-day square proves nothing about demat | **2 days + broker turnaround for DDPI authorisation** |
| 2 | broker/token outage on the close day | **RESUME** — GTT still protects; close the next trading day. The proof is not forfeited | 1 day |
| 3 | any pilot day fails (false SOFT_KILL, capital mis-split, rehydration returns 0) | ⛔ **RESTART the pilot** — the evidence is an *unbroken* carry across a real boot; a patched-together two days is not that | **2+ days per attempt** |

⭐ **So the honest expectation is not four days.** Four is the zero-redo floor. With **seven
never-run components**, two restart-on-failure stages, one blocked Monday and no Friday arms, a
single redo in stage 3 pushes completion to **mid-August**. Rama should plan around
*"three supervised windows, each restartable, each gated on the previous"* — and know before
starting that a Wednesday failure in stage 3 does send him back to the start of that stage.

---

## 4. Real evidence vs merely green (A4)

| day / activity | evidence |
|---|---|
| desk work, paper runs, unit tests | 🟡 **GREEN ONLY** |
| stage 1 arm | 🟢 **REAL** — real order, real GTT, real money |
| stage 2 close | 🟢 **REAL** — a genuine demat debit; the only thing that can prove DDPI |
| stage 3 pilot | 🟢 **REAL** — the service's own path, real capital |

⛔ **The specific thing paper cannot prove, and why a paper run is actively misleading here.**
`_paper_holdings` has **exactly one writer — `seed_paper_holding`, a test helper.** Nothing promotes
a filled paper CNC position into a holding, and `_paper_positions` is an in-memory dict with no
day-boundary carry. So paper can inject the **end state** of a carry but never the **transition**
into it — and the transition *is* the lifecycle.

⭐⭐ **Worse than "cannot prove": it prints a PASS.** A paper Mon→Tue shows a clean `GTT_EXIT` while
protection was in fact torn down. A green paper carry is **not weak evidence — it is wrong
evidence.** That is why the live pair is irreducible rather than conventional, and why no amount of
desk work compresses stages 1–3.

---

## 5. What aborts a day, and what it costs (A5)

| event | forfeits the day? | cost |
|---|---|---|
| **market holiday** | n/a in this window — none until 14-Sep | — |
| **broker outage / token failure, arm day** | yes | 1 day, resume |
| **broker outage, close day** | **no** — GTT still protects; close next trading day | 1 day, proof intact |
| **boot failure (v45 migration, Tue 28-Jul)** | yes, **and blocks everything downstream** | ≥1 day + fix time |
| **unexpected CRITICAL during a pilot day** | yes | restart the pilot, 2+ days |
| ⭐ **the orphan-GTT WARNING the morning after arming** | **NO — predicted and benign** | 0 |
| **a 15:15 circuit-breaker SOFT_KILL on an arm day** | no — routine, and CNC is exempt from the 15:17 squareoff (`eod_squareoff.py:1064-1073` filters `("MIS","CO")`) | 0 |

---

## 6. ⭐⭐ The first irreversible step (A6)

**It is not the T2 pair.** Stage 1 buys one share (~₹10) through a standalone operator script with a
human watching both ends, and stage 2 sells it. Small and reversible.

> ### The line is the moment all four flags are true together, on Tue 4-Aug.
>
> ```
> delivery_enabled: true
> force_intraday_only: false
> trade_type: DELIVERY | BOTH
> conditional_allocation_enabled: true      ← the one that gets forgotten
> ```

The first three are **AND**-ed at the broker boundary (`zerodha_adapter.py:558`), so **any one of
them left false keeps the lock fully closed** — the flips can be staged, and there is no partial
exposure. Past that line the live service places real CNC orders on real signals, sized by the real
position sizer, **with no human in the loop per order.** Everything before it has a person watching
each individual order.

**What must be true before crossing:**
1. **DDPI proven** (stage 2 PASS) — otherwise a carried position cannot be sold without manual TPIN.
2. **v45 stable ≥2 trading days.**
3. ⚠️ **#16a built or consciously waived** — `delivery_enabled=true` *without*
   `conditional_allocation_enabled=true` **strands 70 % of capital in the idle intraday bucket**
   (~₹6,913 of ₹9,875.60 **as measured 19-Jul** — the *70 %* is the claim; the rupee figures are a
   snapshot and actual capital moves daily, 9,872.30 on 28-Jul). Not dangerous; it silently halves
   capacity and reads like a sizing bug.
4. **The conditional-allocation decision made deliberately** — it is the #1 first-run risk *and* the
   only never-run piece that touches intraday capital, which is the part currently earning.
5. `max_open_delivery_positions: 3` / `max_daily_delivery_trades: 5` reviewed against actual capital
   **re-read on the day of the review** — ⛔ not against the ₹9,875.60 written here on 27-Jul. This is a
   review *basis*, so a stale base makes the review stale: read `fm_ledger`'s latest INIT first.

**⭐ The rollback property, which makes this less frightening than it sounds — and its one limit.**
Setting `delivery_enabled=false` refuses **new** CNC orders, while **GTT operations stay ungated by
design** (`zerodha_adapter.py:683,734,766` — *"protection must survive disablement"*). So disarming
never strands an already-protected position. **What cannot be undone is a CNC position already
settled into demat**: that one has to be sold, and selling it needs DDPI — which is precisely why
stage 2 gates stage 3.

---

## 7. §B — the not-started pieces: named, sized, and their effect on day 1

**#15 is done** (`f7eedd3`, 26-Jul). One piece remains, and it is two separable items:

### #16a — the config foot-gun · **SMALL, ~2–3 h · effectively blocks the flag flip, not stage 1**

A config-validation rule refusing (or loudly flagging) `delivery_enabled=true` with
`conditional_allocation_enabled=false`. **Precedent exists in-tree** — `config_auditor.py:196` +
`raise_if_blocked()` is the same shape, so this is a rule addition, not new machinery.

- Does **not** block stages 1–2 (T2 ignores every live flag).
- ⭐ **Does gate stage 3** — the flip is exactly when the foot-gun fires. Build it **this week**,
  off-market, after Tuesday is observed. Not today.
- 🔴 **Decision for Rama:** *hard refuse-to-boot, or loud warning?* This is the same question shape
  as the open S4 follow-up (*should a non-2xx `_shutdown_event.set()` block the boot or degrade?*).
  Worth answering both the same way, once.

### #16b — the GTT-linkage-loss edge · **4–6 h, LOW confidence · does not block day 1**

A carried CNC is mis-closed if **both** the `gtt_state` row and the broker GTT vanish. Needs both
halves to disappear, so it is not on the day-1 path — but it must be resolved before the pilot's
later days, when a real carry is exposed to it.

- 🔴 **Decision for Rama — it is a design question, not a patch:** when both vanish, should the
  system (a) treat the trade as closed (today's behaviour, and wrong), (b) alert and halt, or
  (c) re-derive the truth from `holdings()`?
- ⭐ **Re-scope it AFTER v45 lands, not before.** #16b is the same false premise as CHECK1's
  ("no position ⇒ closed externally"), and §A/§B/§C of CHECK1+W8 — the canonical `closure_source` /
  `exit_mechanism` vocabulary — are in the 24 commits going out tonight. **The vocabulary changes
  the available answer**, so designing #16b against the pre-v45 tree would be wasted work.
  Same reason the decomposition warns that piece #6 is load-bearing *only because CHECK1 is wrong*.

**Neither piece blocks stage 1 or stage 2.** ⇒ **the T2 pair can start Wed 29-Jul with no code
written at all.** Only stage 3 needs #16a first.

---

## 8. Decisions owed by Rama, in the order they hit the critical path

| # | decision | needed by |
|---|---|---|
| 1 | Confirm Monday clean → push tonight | tonight |
| 2 | **Arm day: Wed 29-Jul?** (⛔ not Friday, ⛔ not Mon 3-Aug) | Tue evening |
| 3 | **#16a: refuse-to-boot or warn?** | before the flip |
| 4 | **`conditional_allocation_enabled` — flip with the others, or stage it?** | before the flip |
| 5 | **#16b: closed / halt / re-derive from `holdings()`?** | before pilot D2 |
| 6 | ⚠️ **Does the 2FA seed move still go Fri 31-Jul / Sat 1-Aug?** It blocks Mon 3-Aug. Moving it to a later weekend buys back a market day | this week |

---

## 9. ⭐ The #16a decision — answered, together with the S4 follow-up (27-Jul)

**One rule, two opposite answers. The shape is the same; the asymmetry does not transfer.**

> **#16a → BLOCK (fail-fast).  S4 → DEGRADE + ALARM (do not block the boot).**
>
> **The discriminator: fail-fast is right when the failing condition can only arise from a
> DELIBERATE ACT. Degrade-and-alarm is right when it can arise from the ENVIRONMENT.**

### The precedent is stronger than "in the tree" — it is in the same function, on the same flags

`config_auditor._group_a_contradictions` already **BLOCKs** a delivery-flag contradiction: **A1**
refuses `force_intraday_only=true` + `trade_type=DELIVERY` ("0 strategies would trade → silent dead
system"). `Severity` already has `WARN` and `BLOCK`, `raise_if_blocked()` is already the startup
gate, and the docstring already states the rule: *"The hard ones BLOCK (fail-fast); the 'valid but
pointless' ones WARN."* ⇒ **#16a is not a new policy question. It is asking which side of an
existing line one more finding falls on.**

### A2 — does the asymmetry transfer from the service window? Partly, and the honest answer is "not the way you'd expect"

**On the accepted-bad-value side, #16a is MILDER than the service window, not worse.** Stranding
70 % of capital places no wrong order, risks no capital, is fully reversible by a flag edit, and is
visible as halved capacity. The service-window bound protects the forward-shadow recorder, whose
output the code says *"cannot be regenerated"* — that is irreversible. On a strict reading of the
window's own argument, **#16a is the weaker case for fail-fast.**

⭐ **But the window's argument is not the binding one here. This one is:** the flip happens on a
planned, supervised day (Tue 4-Aug) as the opening act of the carry pilot. A wrong split does not
merely waste capacity — **it corrupts the pilot's capital evidence**, and the pilot is a
restart-on-failure stage (§3) costing 2+ days per attempt. So the irreversibility that justified
fail-fast for the window reappears here in a different place: not in the capital, **in the
evidence**.

### A3 — is the boot-blocking false-positive risk higher with four interacting flags? YES, and here is the exact false positive

Measured from `fund_manager.py:139-146`, not assumed:

```
conditional_enabled FALSE                      -> the fixed config split (70/30)
conditional_enabled TRUE + delivery only       -> (0.0, 1.0)
conditional_enabled TRUE + BOTH active         -> the config split (70/30)   ← IDENTICAL
```

⇒ **with `trade_type=BOTH` and both intents active, the flag makes literally no difference** — both
branches return the same split. A naive rule *"`delivery_enabled=true` requires
`conditional_allocation_enabled=true`"* would therefore **block a configuration that is not merely
acceptable but behaviourally identical** — and `BOTH` is the likeliest production configuration.
**A3's worry is justified, and it is a defect in the naive rule, not in fail-fast.**

**The rule must be conditioned on the ACTIVE INTENT SET, not on the flag pair:**

> BLOCK **iff** `delivery_enabled=true` **and** `conditional_allocation_enabled=false` **and** the
> active strategy set resolves to **delivery-only** (either `trade_type=DELIVERY`, or `BOTH` with
> every intraday strategy disabled).

⭐ The machinery already exists: group A **already takes `strategies`** and A4 already branches on
`if strategies:` for exactly this kind of "depends on what is actually enabled" test.

### Why BLOCK cannot cost an unattended trading day here

`delivery_enabled` has been `false` since the 15-Jun incident that created the lock. **A rule whose
first precondition is `delivery_enabled=true` cannot fire on any ordinary morning** — it can only
fire on a day when someone deliberately turned delivery on. That block lands in front of the person
who made the change, minutes after they made it: **cost ≈ 60 seconds, not a trading day.**

### And why the same answer is WRONG for S4 — the asymmetry genuinely does not transfer

S4's precondition is **environmental**: one local endpoint answering non-2xx on an unattended 08:15
boot. On 17-Jul it produced **0 trades on a trading day, behind an exit 0 that looked like a normal
stop.** No deliberate act preceded it.

⭐⭐ **And the decisive point is a safety argument, not a convenience one:** `_shutdown_event.set()`
does not merely stop signal intake — it takes down **exit management, the reconciler, and the 15:17
EOD squareoff** with it. The check exists only *"to confirm Flask is listening"*, and no Flask means
no new signals anyway. So blocking the boot does not prevent the bad outcome; **it converts "no new
entries, everything protective still running" into "nothing running at all."** On 17-Jul the book
was flat. That was luck, not design.

⇒ **S4: degrade, log CRITICAL, keep booting.** ⭐ The prerequisite for that answer **did not exist
when the question was first asked and does now**: `liveness_probe` runs `*/5 09-15 Mon-Fri`
(verified in the live crontab today), so a degraded boot is observable rather than silent — which
was lesson 3's whole complaint (*"a clean exit 0 + 0 restarts is indistinguishable from a healthy
system"*). **Degrading was the wrong answer in July because nothing would have noticed. It is the
right answer now because something does.**

⛔ Neither is built today. Both are off-market work for later this week.

---

## 10. ⭐ §B — document the trap, or remove it? REMOVE it.

**Repair the deployed `scripts/` copy by merging `fix-t2-repair-07jul`. Do not leave a bit-rotted
script at the obvious path and defend it with a note.**

**Why the original reason to hold the branch back has expired.** It was held *"live-validation-
gated"* — do not merge until the live proof passes. **The same-day leg passed live on 10-Jul** with
real orders on four symbols. The branch is no longer unvalidated; the only unproven part is
`--arm-overnight`, which is **a flag on that same script**, not a different script. Meanwhile the
cost of holding it has become concrete: **holding the branch back is now what creates the trap it
was meant to avoid.**

⏰ **When: Tue 28-Jul evening, off-market — after the v45 boot is observed clean, before the Wed
29-Jul arm day.** Not tonight: tonight's push is 24 commits whose single variable is v45, and the
T2 script must not share that day. It is operator-only with no app importer, so it *cannot* touch
the trading service — but tomorrow is not the day to be leaning on a "cannot".

⚠️ **The hash check stays step zero even after the merge.** After merging there will be two correct
copies; the runbook should still prove which one it ran. And until the merge lands, the hash check
is the only defence there is.

---

## 11. What was NOT done

⛔ No build, no flag change, no script run, no VM write, no suite. Read-only throughout; the only
VM commands were `sha256sum`, `ls`, and log greps. `delivery_enabled: false`,
`force_intraday_only: true`, `trade_type: INTRADAY`, `conditional_allocation_enabled: false` — all
unchanged and unread by anything today.
