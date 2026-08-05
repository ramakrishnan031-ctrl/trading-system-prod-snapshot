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

⭐ **A case nobody predicted is a finding, not an emergency.** Capture the state and stop:

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
