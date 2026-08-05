# THURSDAY CONTINGENCY — 06-Aug-2026, 08:20 IST

> **Read the ONE command below. It routes you in seconds. Most mornings you stop at Branch A.**

---

## ▶️ THE ONE COMMAND — run this first

```bash
ssh trading-vm 'systemctl show trading-system.service -p ActiveState,ExecMainStatus,ExecMainStartTimestamp --no-pager'
```

| what you see | branch | meaning |
|---|---|---|
| `ActiveState=active` **and** `ExecMainStartTimestamp` = **Thu 2026-08-06 ~08:15** | ✅ **A** | **Prediction held. Nothing to do.** |
| `ActiveState=active` **but** timestamp still **Wed 2026-08-05 08:15:05** | ⚠️ **B** | **No boot happened** — Wednesday's process is still running |
| `ActiveState=inactive` **and** `ExecMainStatus=4` | 🔴 **C** | **HALT** — the kill survived |
| anything else | 🔵 **D** | **a case nobody predicted — that is itself a finding** |

> ### ⛔ WHY `is-active` ALONE IS NOT ENOUGH — READ THIS ONCE
> **Branch A and Branch B BOTH print `active`.** The only thing that separates them is the
> **start timestamp**. ⭐ A stale timestamp means the service never restarted, so
> `clear_stale_state` never ran and **Wednesday's `SOFT_KILL` is still set** — a system that looks
> healthy and will take no entries. ⛔ **Never route this morning on `is-active` by itself.**

### 🤖 WHAT PRE-FLIGHT WILL AND WILL NOT DO BEFORE YOU LOOK — **measured, and it is the opposite of what was assumed**

Pre-flight runs **08:30 (Phase A) · 09:14 (Phase B) · 09:15 (Phase C)** (`config/cron_registry.yaml`),
and its `systemctl_start_service` auto-fix **is** armed and whitelisted (`scripts/preflight/autofix.py:35`).

⛔ **BUT IT WILL MAKE *ZERO* ATTEMPTS TO START `trading-system.service`. Not one, not three.**
**(S)** `scripts/preflight/checks/__init__.py:48` builds the Phase-B check as
`ServiceActiveCheck("trading-system.service", auto_fixable=False)` — commented *"ALERT-ONLY
(token-watcher owns lifecycle; never auto-start)"*. The auto-fix covers only the **six** support
daemons in `SUPPORT_SERVICES` (`checks/services.py:85-92`: alert-watcher, token-watcher,
security-watcher, cron, fail2ban, auditd) — `trading-system` is deliberately absent
(`services.py:5-8`).
**(P)** `RestartPreventExitStatus=3 4` confirmed on the **live** unit ⇒ systemd will not loop either.

⇒ 🔴 **If you DO see repeated start attempts, that is not pre-flight and it IS a finding.**

> ⭐⭐ **THE REAL PRE-FLIGHT HAZARD IS THE MIRROR IMAGE, AND IT LANDS EXACTLY ON BRANCH B:**
> Phase B's check is `systemctl is-active` (`services.py:31-39`) — **which cannot tell a fresh
> Thursday boot from Wednesday's process still running.** Under Branch B the service *is* `active`,
> so **pre-flight will report `svc_trading_system` PASS on the precise failure mode this page
> predicts.** ⛔ **A green pre-flight at 09:14 is NOT evidence a boot happened.** It is the same
> defect the timestamp rule above exists to defeat — arriving from a second, more authoritative-
> looking direction.

---

## ✅ BRANCH A — service active, timestamp is Thursday. **NOTHING TO DO.**

The 08:15 boot happened and cleared Wednesday's prior-day kill at `main.py:1914`.
➡️ **Go to the boot-seed reading** (`ADDENDUM_capital_drift_05-Aug-2026.md` §4b) and
➡️ **score the H5 prediction** (`HANDOFF_05-Aug-EVENING.md` §8.4: if an intraday signal arrives on
ATULAUTO while it is still held, the rejection code should switch to `DUPLICATE_SYMBOL`).

⭐ **Two independent sources predicted this branch**, which is why it is first:
- **(S)** `kill_switch.clear_stale_state` (`:287-335`) clears **any** prior-day kill and carries
  **no open-position condition** — verified at the deployed SHA;
- **(P)** the 18:45 Wednesday `system_manager` run said so in its own words:
  *"SOFT_KILL … from 2026-08-05 — prior-day at the next open, so the 2026-08-06 08:15 boot
  auto-clears it (HEADLESS GUARANTEE). No action needed; ⛔ do NOT run deploy/resume.sh for this."*
  ⚠️ **That sentence is TRUE and its precondition is UNSTATED — it assumes a boot happens.**
  On the one night it might not, it still reads *"no action needed"*. See §3 of the evening report.

### ⛔ BUT "BRANCH A" DOES **NOT** BY ITSELF ESTABLISH THAT THE KILL IS CLEAR — **run one more command**

**A boot that reaches the trading loop can still have set a NEW kill on the way there.** ChatGPT's
Q3 asked exactly this, and the answer is **YES** — traced at the deployed SHA `0197923`:

| # | boot stage (all **after** `clear_stale_state`) | what it can set | can it fire on the boot cycle? |
|---|---|---|---|
| 1 | `main.py:2442` `fund_manager.rehydrate_from_open_trades()` → `capital/fund_manager.py:1769` → `:2351` | **`hard_kill`** + raises `CapitalStateInconsistent` ⇒ **main returns 3** | **YES — single sample** |
| 2 | `main.py:3403` `order_reconciler.reconcile_once()` → `orders/order_reconciler.py:976` `_check9_missing_exits` → `:2830` | **`soft_kill("MISSING_EXITS: naked position …")`** | **YES — single sample** |
| 3 | same reconcile → `:1056` `_finalise_auth_counter` → `:720` | `soft_kill` (3 consecutive auth-error **cycles**) | **NO** — counter starts at 0; one cycle reaches at most 1 |

Both live stages sit **before** the first trading action (`main.py:3497` `signal_processor.start()`,
guarded by the boot-order assert at `:3491`). ⭐ **The source says so at the call site itself**
(`main.py:1911-1913`): *"if the trigger was legitimate, startup reconciliation will re-trigger it
within seconds"* — and that is now traced to a real mechanism, not taken on the comment's word.

✅ **What CANNOT happen: the HALT *scenario* cannot be re-created.** It is decided **once** at
`main.py:1922` and consumed at `:1938-1944`; `scenario` is never recomputed. ⇒ **a kill set after
that gate does not produce exit 4** — stage 1 exits **3**, stage 2 leaves a *running* service
with entries blocked.

```bash
# Branch A only — confirm the kill actually is clear before calling the morning good
ssh trading-vm 'cd /home/ubuntu/systems/trading-system
grep -iE "auto-cleared|MISSING_EXITS|capital_invariant|soft_kill|hard_kill" logs/system_2026-08-06.log | tail -20'
```
- ✅ **Expect exactly one line:** `Kill switch auto-cleared: prior SOFT_KILL from 2026-08-05 …`
- 🔴 **`MISSING_EXITS` or `capital_invariant_violated`** ⇒ a **new** kill was set this morning.
  ⛔ **Do not clear it — it is same-day and it is not the breaker.** Record and report.

---

# ✅▶️ BRANCH A CONTINUED — **THE BOOT WAS NORMAL. NOW DO THIS.**

> ## ⚠️⚠️ READ THIS BEFORE THE LIST — **a normal-looking morning is the easiest place to stop reading.**
> **Steps 1 and 2 will both look fine, and then most people stop.** ⭐⭐ **STEP 3 IS THE MEASUREMENT
> OF THE WEEK** — it is the one thing the whole of Wednesday was spent making possible, it can only
> be taken on a T+1 morning, and **it is unrecoverable once the day moves on.** ⛔ **Do not stop at
> step 2 because nothing looked wrong. Nothing looking wrong is the expected case.**

> ### 🔗 PRECONDITION — this section assumes **Wednesday's stop procedure RAN**.
> If it did not, the service never exited, no boot happened, and **you are in Branch B, not here.**
> Step 1 is what tells you which. ⭐ Step 6's census exists **only** if the stop ran.

### 1️⃣ Confirm the boot — *the timestamp, not `is-active`*
Already run at the top of this page. ✅ **`ActiveState=active` · `ExecMainStatus=0` ·
`ExecMainStartTimestamp` = Thu 2026-08-06 ~08:15.** ⭐ **The timestamp is the whole check** — it is
the only thing separating a fresh boot from Wednesday's survivor.

### 2️⃣ Confirm the kill actually cleared
Run the Branch-A command above (the `auto-cleared|MISSING_EXITS|capital_invariant` grep).
⛔ **"Active with a Thursday timestamp" does NOT establish this** — a boot can reach the trading loop
having set a *new* kill on the way (the Q3 table above). **This is a separate check, not a formality.**

### 3️⃣ 🔴🔴 **THE CARRY OBSERVATION — THIS IS THE ONE. TAKE IT BEFORE ANYTHING ELSE MOVES.**

**What is happening:** at **T+1** ATULAUTO leaves `positions()` for `holdings()`. **CHECK1 therefore
runs against a trade that is `OPEN` in the DB with no broker position** — the exact condition its
delivery skip exists for, meeting it for the first time.

> #### 📌 THE FALSIFIABLE EXPECTATION — **written before the fact, 05-Aug 22:2x, and scoreable either way**
> The trade's `trade_id` is in `delivery_trade_ids` (the **`ACTIVE` `gtt_state` row verified
> Wednesday**) ⇒ **CHECK1 SKIPS IT** ⇒ **the trade stays `OPEN`, its capital stays reserved, and
> nothing is cancelled.**

**Wednesday's baseline, measured 22:2x — this is what Thursday is scored against:**
```
trades:     status=OPEN   exit_reason=(empty)   closure_source=(empty)   exit_mechanism=(empty)
            qty_filled=1  margin_reserved=587.4228  reservation_id=ee9af41eae554c35
fm_ledger:  10021  10:00:21  RESERVE  616.79394  positional   (margin_delta +616.79394)
            10047  10:01:21  COMMIT   587.4      positional   (margin_delta 0.0)
            ⇒ RESERVE then COMMIT, and NO release row.
gtt_state:  330456580 | trd_e66ee17b1844491db5d2e99afa6f104b | ATULAUTO | ACTIVE
control:    trades.status='OPEN' count = 1  (this trade is the only OPEN row in the DB)
```

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system; DB=data_store/trading_system.db
T=trd_e66ee17b1844491db5d2e99afa6f104b; R=ee9af41eae554c35
echo "== A. the trade — did CHECK1 leave it alone? =="
sqlite3 -header "file:${DB}?mode=ro" "SELECT status, exit_reason, closure_source, exit_mechanism FROM trades WHERE trade_id='"'"'$T'"'"';"
echo "== B. capital — did anything release it? (keyed on reservation_id, NOT trade_id) =="
sqlite3 -header "file:${DB}?mode=ro" "SELECT ledger_id, ts, entry_type, amount, bucket FROM fm_ledger WHERE reservation_id='"'"'$R'"'"' ORDER BY ledger_id;"
echo "== C. the GTT row =="
sqlite3 -header "file:${DB}?mode=ro" "SELECT gtt_id, trade_id, status FROM gtt_state WHERE trade_id='"'"'$T'"'"';"
echo "== D. control — how many OPEN trades exist at all =="
sqlite3 "file:${DB}?mode=ro" "SELECT COUNT(*) FROM trades WHERE status='"'"'OPEN'"'"';"'
```

| | ✅ **SKIPPED — the expectation held** | 🔴 **NOT SKIPPED — the expectation is REFUTED** |
|---|---|---|
| **A** trade | `status=OPEN`, all three closure fields still empty | `status=CLOSED` or **`CLOSED_MANUAL`**, and `exit_reason` / `closure_source` / `exit_mechanism` populated |
| **B** capital | **exactly the two rows above** — `RESERVE` + `COMMIT`, nothing after | **a third row appears** with `entry_type` **`RELEASE`** or **`RELEASE_USED`** ⇒ capital was released |
| **C** GTT | still `ACTIVE` | `CANCELLED` / gone |
| **D** control | `1` | `0` |

> ⛔⛔ **KEY THE CAPITAL QUERY ON `reservation_id`, NOT `trade_id` — this is a real trap I hit.**
> **(P)** `fm_ledger` has **3,145 rows and only 59 carry a `trade_id` (1.9%)**; this trade's rows
> carry **none**. ⇒ a `WHERE trade_id=…` query returns **empty**, and **an operator would read empty
> as "capital released"** — a certain false alarm on the one morning it matters.
> ⭐ **Vocabularies above (`RELEASE`/`RELEASE_USED`, `CLOSED_MANUAL`) were read from the live DB, not
> from memory.**

⛔ **WHATEVER IT SHOWS, IT IS RECORDED — NOT ACTED ON.** ⛔ Do not re-open, do not re-place a GTT, do
not touch `gtt_state`. A refutation is a **finding**, and the position's protection is broker-side
and unaffected either way. ➡️ **Step 4 is what tells you whether this measured anything at all.**

### 4️⃣ Confirm the T+1 transition actually happened — *⚠️ this validates step 3*
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system
grep "get_holdings call_end"  logs/system_2026-08-06.log | tail -1
grep "get_positions call_end" logs/system_2026-08-06.log | tail -1'
```
- ✅ **`"1 holdings"` (or more) and `"0 positions"`** ⇒ T+1 happened; **step 3 measured something real.**
  *(Wednesday's baseline for contrast: `get_holdings` → **`"0 holdings"`**, `get_positions` → `"1 positions"`.)*
- 🔴 **STILL `"1 positions"` and `"0 holdings"`** ⇒ **T+1 HAS NOT HAPPENED AND STEP 3 MEASURED
  NOTHING.** ⛔⛔ **Say exactly that. Do NOT record step 3 as a pass** — a skip that was never asked
  for is not a skip that worked. **The observation is simply NOT YET AVAILABLE**, and it is not a
  refutation either.

### 5️⃣ The boot-seed reading
➡️ **`ADDENDUM_capital_drift_05-Aug-2026.md` §4b — and now also §4c.** ⛔ **Their commands are NOT
restated here** — run them from the addendum so there is one copy.
⭐ **Carry its honest bound with it: an inexact match is SETTLEMENT, not a finding.** The prediction
was `tomorrow ≈ today − CNC block ± settled P&L`, and "≈" is doing real work.
🔴 **§4c SHARPENED IT AT SOURCE the night before, so it is scoreable as a ratio, not a vibe:**
**(S)** the seed is `broker.net` minus a carryover term that is **zero at 08:15** ⇒ **seed =
`broker.net`.** ⇒ predicted **Thu/Wed seed ratio ≈ 0.940** (a **≈5.94 %** shrinkage = the carried
position's own share of the base), with **both buckets shrinking by the same fraction** (fixed 70/30).
⭐⭐ **The claim to score: the system starts today believing it holds ~₹587 LESS than it controls —
because that value is in stock, not cash — and NOTHING flags it**, since `initialize()` performs no
comparison. ⛔ **Record the two seeds and the ratio. Do not reconcile, and do not act.**

### 6️⃣ Read Wednesday's census — *an artifact recovered, not a question answered*
Captured by the stop procedure (§e). ⭐ **It is the artifact the whole Wednesday-evening decision was
taken to recover.**
> ⛔⛔ **CARRY THE CORRECTION OR YOU WILL MISREAD IT: `acted` COUNTS ROWS *EXAMINED*, NOT ACTIONS
> *TAKEN*.** `orders/cnc_gtt_monitor.py:145-153` appends a label on **every** path — `healthy:`,
> `needs_review:`, `noop:` — and counts them all. ⇒ **`cnc_gtt_monitor acted > 0` does NOT mean the
> GTT was observed working.** ⛔ Four earlier cards said it did. **Record the census; do not conclude
> from `acted`.**

---

## ⚠️ BRANCH B — active, but the timestamp is still Wednesday

**This is the "no boot" case.** It is the expected outcome **if the stop procedure was NOT run**.

- The service never exited, so the token-watcher's first branch (`active → nothing to do`) held all
  night, `clear_stale_state` never ran, and **Wednesday's `SOFT_KILL` is still active.**
- ⇒ **The system will take no new entries today.** ATULAUTO remains held and protected by its
  broker-side GTT.

⛔ **Do NOT restart to "fix" it.** A restart still meets a **same-day** kill only after midnight —
by Thursday the Wednesday kill is prior-day, so a restart *would* clear it, **but** a restart also
puts the T+1 `MISSING_AT_BROKER` path in play for the first time
(`reconcile_positions` reads `positions()` only; the holding has moved to `holdings()`).
➡️ **Record and report. This is a decision, not a fix.**

---

## 🔴 BRANCH C — inactive, `ExecMainStatus=4`. **HALT.**

### What it means
The boot reached `main.py:1938` with a kill still active and returned **4**. The kill is almost
certainly **Wednesday's routine 15:15 breaker** (`circuit_breaker_force_close_15:15`) — the same one
the system clears every ordinary morning.

### ⛔ FIRST — CONFIRM *WHICH* KILL. Do not skip this.

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system
grep -iE "kill|HALT" logs/system_2026-08-06.log | tail -20'
```

- **`reason=circuit_breaker_force_close_15:15` or `EOD_SQUAREOFF`** ⇒ routine. Continue below.
- 🔴 **ANY OTHER REASON** ⇒ **an emergency kill. STOP. Do not clear it. Report.** Clearing an
  emergency kill overrides a real protection — that is the opposite of this document's purpose.

### The remedy — ⚠️ **UNVERIFIED. READ THE WARNING BEFORE TYPING.**

> ### ⛔⛔ **MARKED UNVERIFIED, DELIBERATELY.**
> I could not test this tonight and did not try. What follows is read from source, not observed.
> ⭐ **If you are not confident, the correct action is to RECORD AND WAIT.**
> 🔴 **A held CNC position with a resting broker-side GTT is NOT in danger from a system that fails
> to start.** ATULAUTO has **no SL or TGT order row at all** — its protection lives at Zerodha and
> is unaffected by whether this process runs. **Waiting costs a trading day. Guessing costs more.**

**`--resume` and `deploy/resume.sh` are NOT the same lever** — measured:

| | what it is | what it does |
|---|---|---|
| **`deploy/resume.sh`** | a shell script | `systemctl stop` → `scripts/clear_kill_switch.py` → `systemctl start`. ⛔ **It never uses `--resume`.** Its own header says why: a standalone `main.py --resume` *"competes with trading-system.service for the instance lock (port 5001) — the 18-Jun collision"* |
| **`--resume`** | a `main.py` CLI flag | consumed at `main.py:1938`; calls `kill_switch.resume(...)` and lets the boot continue |

**(S) What `--resume` does BEYOND clearing the kill — traced, and the answer is reassuring:**
`kill_switch.resume()` (`:703-736`) validates `resumed_by`, persists `INACTIVE`, publishes a
`resume` event, and logs. ✅ **It does NOT touch positions, capital, orders, or `gtt_state`.**
⚠️ **But two properties you must know:**
1. **`--resume` clears `HARD_KILL` as well as `SOFT_KILL`** (its help text says so). That is why the
   "confirm which kill" step above is mandatory and not ceremony.
2. **`--resume` BYPASSES the service-window guard** (`main.py:1810-1814`, alongside
   `--status`/`--dry-run`/`--interactive`). Irrelevant at 08:20 — you are in-window — but it means
   the flag is not a no-op outside trading hours.

**⇒ For a routine breaker, `deploy/resume.sh` is the supported route** (it is the systemd-friendly
one, and it avoids the instance-lock collision by stopping the service first). The service is
already stopped in this branch, so its step 1 is a no-op.

```bash
# ONLY after confirming the kill is circuit_breaker_force_close_15:15 or EOD_SQUAREOFF
ssh trading-vm 'cd /home/ubuntu/systems/trading-system && sudo bash deploy/resume.sh'
```
⛔ **NOT `--force`.** ⛔ Not if the reason is anything else. ⛔ Not if you are unsure.

**Then verify:**
```bash
ssh trading-vm 'systemctl show trading-system.service -p ActiveState,ExecMainStartTimestamp --no-pager'
```

---

## 🔵 BRANCH D — anything else

### ⚠️ ONE CASE IN HERE IS NOW PREDICTED: **`inactive` with `ExecMainStatus=3`, NOT 4**

Branch C is written for **status 4** (the HALT gate). **Status 3 is a different failure and it has
its own first-ever exposure Thursday.** `main.py:2442` `rehydrate_from_open_trades()` replays every
OPEN trade's reserve/commit chain, then checks the capital invariant once
(`capital/fund_manager.py:1761`); on failure it fires **`hard_kill`** (`:2351`) and raises, and main
returns **3**. **Thursday is the first boot where that replay walks an open CNC delivery trade.**

⛔ **If you see `ExecMainStatus=3`: STOP. Do NOT run `resume.sh`.** A `hard_kill` from
`capital_invariant_violated` is **not** the routine breaker, and Branch C's remedy is written only
for `circuit_breaker_force_close_15:15` / `EOD_SQUAREOFF`. Capture and report:
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system
grep -iE "capital_invariant|rehydrate|CapitalStateInconsistent|fund_manager.initialize" logs/system_2026-08-06.log | tail -30'
```

> **(S) The seed itself is NOT the risk.** `fund_manager.initialize()`
> (`capital/fund_manager.py:417-457`) is a pure **INIT**: it writes `balance_before=0.0`,
> `balance_after=broker_balance` and sets the buckets. **No comparison, no threshold, no drift
> event, no kill.** ⇒ Thursday's lower `broker.net` simply *becomes* the day's total. ✅ Confirmed —
> the seed **cannot** trigger anything by being smaller. (`check_paper_capital_consistency` is a
> **no-op in live mode**, `utils/startup_checks.py:982`.)
>
> **(I) The stage AFTER it is where the arithmetic lands**, and the estimate says comfortable:
> the guard needs a **negative** bucket (`fund_manager.py:2297-2299`), the positional bucket is a
> **fixed 30%** (`conditional_allocation_enabled: false`, verified on the VM), so ~₹2,789 of a
> ~₹9,296 seed against a ~₹587 CNC block ≈ **4.7× headroom.** ⛔ **That is an ESTIMATE computed
> from Wednesday's figures — it is NOT a measurement of Thursday, and it is why the branch is
> written rather than dismissed.**

⭐ **Any OTHER case nobody predicted is a finding, not an emergency.** Capture the state and stop:

```bash
ssh trading-vm 'systemctl status trading-system.service --no-pager | head -20
tail -n 40 /home/ubuntu/systems/trading-system/logs/token_watcher.log'
```
⛔ Do not improvise. Record and report.

---

## ⚖️ SCOPE OF THE "NEVER RUN `resume.sh`" INSTRUCTION — **restated, not revoked**

⛔ **The prohibition was written for WEDNESDAY NIGHT's SAME-DAY kill, and it is correct there:**
on 05-Aug the 15:15 breaker is a *same-day* kill, both clear paths refuse it by design, and running
`resume.sh` would override a protection the system deliberately holds within the trading day.

✅ **Thursday's case is different in kind, not in degree:** the same kill is now **prior-day**, and
clearing it is *what the system does by itself every ordinary morning*. ⭐ **The remedy restores the
normal path rather than overriding a protection.**
⭐ **The system states this scope itself** — Wednesday's 18:45 `system_manager` output says
*"prior-day at the next open … No action needed; ⛔ do NOT run deploy/resume.sh for this"*: i.e.
don't run it **because the boot handles it**, not because the command is forbidden forever.

> ⭐⭐ **THE GENERAL POINT, WORTH KEEPING:** an unscoped rule meeting a case it was never written for
> is how a correct instruction becomes a wrong one. **This instruction now carries its scope:
> forbidden for Wednesday's same-day kill; Thursday's prior-day case is governed by Branch C.**

---

## 📌 WHAT TO RECORD, WHICHEVER BRANCH

1. **Which branch, and the exact `ExecMainStatus` / `ExecMainStartTimestamp`.**
2. **The boot seed** (addendum §4b) — the prediction was written in advance, so it is scoreable.
3. **The H5 prediction** (§8.4) — `DUPLICATE_SYMBOL` vs `SYMBOL_DIRECTION_DAILY_LIMIT`. ⛔ **If no
   intraday signal arrives on ATULAUTO, the result is NOT DETERMINABLE — not a refutation.**
4. ⛔ **THE "NO BOOT THURSDAY" PREDICTION:** if the Wednesday stop procedure **was** run, that
   prediction is **MOOT — neither confirmed nor refuted**, because it was prevented from running.
   ⭐ **A prediction that never got to run is not evidence in either direction. Record it as moot.**
   *(It is scoreable only under Branch B, where the stop was not taken.)*
