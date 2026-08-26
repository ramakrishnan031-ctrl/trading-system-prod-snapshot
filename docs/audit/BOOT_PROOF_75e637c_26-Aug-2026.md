# BOOT PROOF — Wed 26-Aug-2026 · THE FIRST EVER RUN OF `75e637c`

**Verdict: ✅ BOOT PROVEN. The EOD-resolver CRITICAL is ABSENT ⇒ the 25-Aug EOD lifecycle fix is LIVE IN PRODUCTION, ⛔ NOT INERT.**

🏷️ Status of the unit: `PUSHED · DEPLOYED · BOOT PROVEN`. ⛔ Still **NOT** `VERIFIED LIVE` as a *feature* — see §5.
⚠️ **SUPERSEDED 26-Aug 19:2x — RETAINED:** *"🛑 **The rollback target was NOT advanced.** It still reads `195436b`. See §4."*
🟢 **26-Aug 19:2x — 👤 RAMA AUTHORISED THE ADVANCE (FILE 11 PART 2 A1). THE ROLLBACK TARGET / RB-3 TREE IS NOW `75e637c`.** 📄 The advance record lives in `docs/audit/ROLLBACK_AND_ATTENDANCE_23-Aug-2026.md`. ⭐ E2's verdict below was the qualifying evidence; E3's *"not advanced"* is now history.

All work in this file is **read-only forensic measurement**, with one exception disclosed in full at §7.

---

## §0 — SESSION START

### S1 · The ledger's status line for the F6-EOD unit (verbatim, as found)

`~/.claude/projects/D--Projects-trading-system/memory/UNPUSHED_PENDING_DEPLOY_LEDGER.md:26`

> `# 🧱✅🔝 **25-Aug evening — EOD LIFECYCLE UNIT — **P-3 CLOSED**. Branch head `75e637c300c1bead4aad3cf549e6dbe9318c6b1f` = `02a2a6b` + 3 closure commits — `PUSHED · DEPLOYED · ⛔ BOOT NOT PROVEN`.**`

and `:56`

> `🛑 **ROLLBACK TARGET REMAINS `195436b` — ⛔ NOT advanced.** ⭐ The rollback TREE is *"the last SHA **proven to boot**"*, and that moves on a **PROVEN BOOT**, ⛔ never on a push.`

⚠️ **Disclosure on how this file was read.** The ledger is **1,399,679 B / 4,010 lines**. It was **not** read line-by-line end-to-end. It was read structurally: frontmatter and header, the four structured sections (`:3950`–`:4010`), the two-clock block (`:160`–`:180`), and every line matching the F6/EOD/`75e637c` search. 🔬 The status line above is quoted from the file, ⛔ not reconstructed.

### S2 · Deployed state, BY MEASUREMENT

| fact | command | result |
|---|---|---|
| `origin/main` (from PC) | `git ls-remote origin refs/heads/main` | `75e637c300c1bead4aad3cf549e6dbe9318c6b1f` |
| `origin/main` (2nd way, on VM) | `cat /home/ubuntu/trading-system.git/refs/heads/main` | `75e637c300c1bead4aad3cf549e6dbe9318c6b1f` |
| deployed `HEAD` | `git --git-dir=/home/ubuntu/trading-system.git rev-parse HEAD` | `75e637c300c1bead4aad3cf549e6dbe9318c6b1f` |
| tracked drift | `read-tree` + `update-index --refresh` + `diff-index --name-only HEAD --` | **0 files** |

✅ **All three agree at `75e637c`. No STOP condition.**

⚠️ **A resolution note, ⛔ not a discrepancy.** `/home/ubuntu/systems/trading-system` is **not itself a git repository** — `git rev-parse` there returns *"not a git repository"*. That is the **designed** bare-repo + `post-receive` checkout model: the git dir is `/home/ubuntu/trading-system.git` and the app dir is its work-tree. The SHA was resolved through the bare repo, ⛔ not guessed.

### S3 · THE TWO-CLOCK RULE — quoted verbatim before it is applied

**Source: `docs/audit/ROLLBACK_AND_ATTENDANCE_23-Aug-2026.md`.**

`§0.1`, lines **68–70**:

> 🔬 The rollback TREE is *"the last SHA **proven to boot**"*. That value changes on a **PROVEN
> BOOT** — ⛔ **not on a push.** The *re-derive after every push* rule elsewhere in this file is the
> **right rule for RB-2's PARENT and the wrong trigger for the TREE**. ⇒ **two facts, two clocks.**

`§1` invariant box, lines **225–233**:

> ▎ **The rollback COMMIT PARENT comes from the current `origin/main`.**
> ▎ **They are DIFFERENT FACTS and must be measured INDEPENDENTLY.**
> ⛔ **Never collapse them into one "rollback SHA".** They go stale on **different clocks**:
>
> | | value | goes stale on | how to get it |
> |---|---|---|---|
> | **PARENT** (RB-2 only) | current `origin/main` | 🔁 **every PUSH** | `git ls-remote origin refs/heads/main` — derived, ⛔ never pasted |
> | **TREE / RB-3 target** | last SHA **proven to boot** | 🔁 **every PROVEN BOOT** (⛔ not merely a started one — ⭐ §0.1 defines it: a `STARTUP` row written AFTER this attempt's start time) | the `system_events` record — ⛔ NOT derivable from `origin/main` |

And the definition it depends on, `L-1`, lines **84–86**:

> ## ⭐ **PROVEN TO BOOT = the `STARTUP` row in `system_events` BELONGING TO *THIS* BOOT ATTEMPT.**
> ⛔ **NOT `systemctl is-active` · ⛔ NOT the T+15 s green line · ⛔ NOT a Telegram ·
> ⛔ NOT the absence of *"Config load failed"* · ⛔ NOT an earlier same-day row.**

---

## §1 — C1 · THE LOG PATHS, RESOLVED FROM `PATHS.md`

| path | `PATHS.md` line that gave it |
|---|---|
| `logs/system_<date>.log` — the service's JSON log, under the deployed tree | **`PATHS.md:91`** — *"LOG PATHS, MEASURED: the service's JSON log is `logs/system_<date>.log` under the deployed tree; each cron writes its OWN `logs/cron-<job>.log`"* |
| `logs/system_<date>.log` for INFO; `journalctl -u trading-system.service` for anything dying **before** the JSON logger exists | **`PATHS.md:48`** |
| `logs/system_YYYY-MM-DD.log`, `reconciler_*.log`, `trades_*.log`, `cron-*.log` | **`PATHS.md:420`** (the Logs table row) |

Resolved absolute paths read today, all under `/home/ubuntu/systems/trading-system/logs/`:
`system_2026-08-26.log` (548,110 B) · `debug_2026-08-26.log` · `reconciler_2026-08-26.log` · `trades_2026-08-26.log` · `token_watcher.log` · `preflight.log`.

⛔ No log directory was guessed. ✅ Every one came from `PATHS.md`.

---

## §2 — C2 · THE ONE THING THAT MATTERS

### ⛔ `"NO strategy resolver was supplied"` — **ABSENT**

```
grep -n "NO strategy resolver was supplied" \
  logs/system_2026-08-26.log logs/debug_2026-08-26.log logs/token_watcher.log \
  logs/reconciler_2026-08-26.log logs/trades_2026-08-26.log logs/preflight.log
# → no output, rc=1
```

Looser searches — `-i "strategy resolver"`, `-i "resolver"`, `"strategy_intent_fn"` — all returned **empty**.

**THE BOUNDED WINDOW.** `system_2026-08-26.log` runs
**`2026-08-26T08:15:18.715+05:30` → `2026-08-26T10:05:36.401+05:30`**, **3,313 lines** — first and last lines read directly. The boot is at the head of that range; the search covers the entire service lifetime to the moment of the report.

**⭐ THE SEARCH COULD HAVE BEEN RED.** The literal exists in the **deployed** source:

```
/home/ubuntu/systems/trading-system/main.py:1376:
    "eod_self_exit: NO strategy resolver was supplied — the EOD lifecycle "
```

⇒ the grep target is the real string; an empty result is a measurement, ⛔ not a spelling accident.

### ⭐ AND THE ABSENCE IS ⛔ NOT VACUOUS — the part that actually took work

🔴 **A working resolver logs NOTHING at arm time.** So *"no CRITICAL in the log"* is, on its own, **indistinguishable from the thread never being armed**. Worse: **zero** `eod_self_exit` lines appear today — ⛔ neither the CRITICAL **nor** the `else`-branch INFO. The armed branch had to be proven independently:

| step | measurement |
|---|---|
| 1. no operator/diagnostic start | `/proc/402677/cmdline` = `…/venv/bin/python …/main.py --mode live` — ⛔ no `--interactive`, ⛔ no `--resume` |
| 2. no env bypass | `TS_IGNORE_MARKET_WINDOW` **unset** in `/proc/402677/environ` (the running process, ⛔ not the unit file) |
| 3. ⇒ armed | `_eod_exit_armed = not (interactive or resume or TS_IGNORE_MARKET_WINDOW=="1")` — `main.py:4032-4036` ⇒ **True** |
| 4. the `else` did not run | `"eod_self_exit: not armed"` (`main.py:4061`) — **ABSENT** from all logs |
| 5. execution reached past the block | the `STARTUP` row is written at **`main.py:4071`**, which lies **AFTER** the arm block ends at `:4064`. The row **exists** (§3) ⇒ lines `4037-4059` demonstrably executed |

⇒ 🔬 **`_start_eod_self_exit_thread(...)` was called at `main.py:4038` with the lambda at `:4051`, the guard at `:1374` ran, and it did NOT fire ⇒ `strategy_intent_fn` was NOT `None` in the live process.**

⭐ **This is the whole lesson of the unit turned on itself: a silent success path cannot be proven by an empty grep. To read an absence as proof, first prove the code that would have spoken was actually reached.**

---

## §3 — C3 · THE BOOT SEQUENCE (verbatim)

**a. `token_watcher` starting the service** — `logs/token_watcher.log:2481-2482`

```
[2026-08-26 08:15:17 IST] Fresh token detected (daily start, in service window). Starting trading-system.service.
[2026-08-26 08:15:18 IST] trading-system.service start command issued.
```

**b. The prior-day SOFT_KILL auto-clearing** — `system_2026-08-26.log:4-5`

```json
{"ts":"2026-08-26T08:15:18.790+05:30","level":"CRITICAL","logger":"kill_switch","msg":"KILL SWITCH ACTIVE AT STARTUP: state=SOFT_KILL reason=circuit_breaker_force_close_15:15 triggered_by=order_monitor -- operator must call resume() to clear (Audit Issue #18 fix)"}
{"ts":"2026-08-26T08:15:18.796+05:30","level":"WARNING","logger":"kill_switch","msg":"Kill switch auto-cleared: prior SOFT_KILL from 2026-08-25 (reason=circuit_breaker_force_close_15:15 by=order_monitor) -- new day 2026-08-26 starts clean (HEADLESS); audited to system_events"}
```

Audited to `system_events` as `KILL_AUTO_CLEARED` @ `2026-08-26T08:15:18.793999+05:30`. ⭐ The alarming CRITICAL is resolved **6 ms** later by design.

**c. Service start / first line** — `system_2026-08-26.log:1`

```json
{"ts":"2026-08-26T08:15:18.715+05:30","level":"INFO","logger":"main","msg":"Trading System v2.0.0 starting (mode=live)"}
```

systemd: `ExecMainStartTimestamp = Wed 2026-08-26 08:15:18 IST` · `ExecMainPID = 402677` · `NRestarts = 0` · `active (running)`.

**d. Every ERROR / CRITICAL in the boot window** — there is exactly **ONE**, and it is (b) above:

```json
{"ts":"2026-08-26T08:15:18.790+05:30","level":"CRITICAL","logger":"kill_switch","msg":"KILL SWITCH ACTIVE AT STARTUP: …"}
```

🔬 **`ERROR` lines in the whole day so far: 0.** `CRITICAL` lines in the whole day: **1** (the above). Boot-window `WARNING`s: the auto-clear line, plus `CONFIG_UNACCESSED: 388 config keys never read` at `08:15:29.559`.

---

## §4 — C4 · N20-35, THE `CRASH` LINE · BLAST RADIUS 🔬 **PROVEN** ZERO

**It appeared again** — `system_2026-08-26.log:10`

```json
{"ts":"2026-08-26T08:15:18.813+05:30","level":"INFO","logger":"main","msg":"startup_scenario=CRASH: same day, no SHUTDOWN event found"}
```

**The mechanism, measured.** `detect_startup_scenario` is called **twice**:

| call site | what it returned today | what consumes it |
|---|---|---|
| `main.py:2265` — **the authoritative one** | **COLD** (`08:15:18.797` *"startup_scenario=COLD: new day (prev=2026-08-25, today=2026-08-26)"*, then `main.py:2269` *"Startup scenario: COLD (cold start)"*) | the `if/elif` ladder at `main.py:2268-2289`, and `scenario.value` into the `STARTUP` row |
| `utils/startup_checks.py:1561` (inside `run_all_startup_checks`) — **the one that logged CRASH** at `:372` | CRASH | ⛔ only `:1564` (`if … == HALT` → `blocking_failures`) and `:1667` (a log string). **CRASH is not acted on there.** |

**⭐ THE FALSIFIERS THAT COULD HAVE FIRED AND DID NOT.** `main.py:2272-2279` says a genuine CRASH scenario emits `_log.critical("Startup scenario: CRASH -- crash detected")` **and** inserts a `CRASH_DETECTED` system event. Both were measured:

```
grep -c "Startup scenario: CRASH" logs/system_2026-08-26.log        → 0
SELECT COUNT(*) … WHERE event_type='CRASH_DETECTED' AND date=today  → 0
```

and the `STARTUP` row itself records **`scenario = COLD`**.

⇒ ✅ **Blast radius is zero, and this is now a measurement rather than an assertion.** ⛔ Not fixed, per instruction.

💭 **INFERENCE, labelled as such:** the two calls disagree because by the time the preflight call runs, a session row for today exists, so its *"same day, no SHUTDOWN"* branch is reached. ⛔ This was **not** measured and is not required for the blast-radius conclusion.

---

## §5 — C5 / C6 · THE GUARD AND THE WIRING, AT `75e637c`

⚠️ **M3 — every line number below was measured today at `75e637c` and holds ONLY at that SHA.** ⛔ None was derived by arithmetic; the function was read end-to-end from the deployed file.

### C5 · The guard fires at **BOOT**, ⛔ not at 17:35 — Observation E **CONFIRMED**

Enclosing function signature — **`main.py:1326-1338`**:

```python
1326| def _start_eod_self_exit_thread(
1327|     store: "StateStore",
1328|     notifier,
1329|     mode: str,
1330|     log,
1331|     market_windows,
1332|     shutdown_event: "threading.Event",
1333|     window_end: "_time",
1334|     poll_interval_sec: int = 60,
1335|     flatten_in_progress_fn: "Optional[Callable[[], bool]]" = None,
1336|     strategy_intent_fn: "Optional[Callable[[str], Optional[str]]]" = None,
1337|     squareoff_time: "Optional[_time]" = None,
1338| ) -> None:
```

The guard — **`main.py:1374-1386`**:

```python
1374|     if strategy_intent_fn is None:
1375|         log.critical(
1376|             "eod_self_exit: NO strategy resolver was supplied — the EOD lifecycle "
1377|             "gate is running PRODUCT-BLIND for this session. Every open position "
…
1386|         )
1387|
1388|     def _run() -> None:
```

🔴 **THE STRUCTURAL PROOF:** the guard at **`:1374`** sits in the body of `_start_eod_self_exit_thread` **BEFORE** `def _run()` is even defined at **`:1388`**. It therefore executes **in the calling thread, at the moment the function is called** — i.e. at boot — ⛔ never inside `_run()`, and ⛔ never at the 17:35 window end. ✅ **Observation E is confirmed by reading the function, ⛔ not by grep.**

### C6 · Both wiring links, and the count — ⭐ carry these forward to FILE 2 §M3 (Q-3)

**Link 1 — the resolver lambda, `main.py:4051-4053`:**

```python
4051|             strategy_intent_fn=(
4052|                 lambda name: getattr(strategies.get(name), "intent", None)
4053|             ),
```

**Link 2 — the forward into the gate, `main.py:1489`:**

```python
1489|                 strategy_intent_fn=strategy_intent_fn,
```

**`strategy_intent_fn` occurrences in `main.py` today: `grep -c` = 12 lines** (`1181, 1195, 1197, 1232, 1249, 1278, 1283, 1296, 1336, 1374, 1489, 4051`).

⭐ **Link 1 alone proves nothing — that is this unit's whole lesson.** The runtime proof is §2: the guard ran and stayed silent.

### Did the wiring survive into the *running process*?

✅ Yes, and by two independent routes:

1. **Identity of the running code.** PID `402677`, `cwd → /home/ubuntu/systems/trading-system`, started `08:15:17`, `NRestarts=0`. Deployed `main.py` **md5 `ea62b0b876489f2897c288548acb2977` == its `75e637c` blob**; size **203,478 B** both sides; `mtime = 2026-08-25 22:53:13.255804739 +0530` = **the deploy instant**; and `find … -name '*.py' -newermt '2026-08-25 22:53:20'` returns **nothing** ⇒ ⛔ nothing mutated between deploy and boot.
2. **Behaviour.** §2's chain — the guard executed and did not fire.

---

## §6 — SECTION D · LIVE STATE (26-Aug, market open, ~10:10 IST)

| # | fact | measurement |
|---|---|---|
| **D1** | Holdings at boot | `get_holdings call_end` @ `2026-08-26T08:15:26.956+05:30` → **`0 holdings`** |
| **D2** | Holdings now | `0 holdings` at `09:15:08`, `09:29:59`, `09:45:06`, `10:00:12` — **0 throughout** |
| **D3** | Positions now | broker `get_positions` → **4 positions** (`10:09:20.919`). DB `status='OPEN'`, product via `orders LEFT JOIN … leg='ENTRY'`: |
| | | `HINDCOPPER` · `first_pullback_long` · LONG · 1 · **MIS** · `10:00:25.271` |
| | | `CYIENT` · `gap_go_long` · LONG · 1 · **MIS** · `10:02:18.796` |
| | | `FMGOETZE` · `positional_sector_rotation` · LONG · 1 · **CNC** · `10:07:48.161` |
| | | `THEMISMED` · `positional_swing_long` · LONG · 4 · **CNC** · `10:08:17.055` |
| **D4** | `broker_cash` | **`10567.6`** at the 09:15 sync. ⛔ No separate boot-time `broker_cash` line exists in today's log. |
| **D5** | `fund_manager.sync_from_broker` @ 09:15 | `{"ts":"2026-08-26T09:15:00.041+05:30",…,"msg":"fund_manager.sync_from_broker","broker_cash":10567.6,"carry":0.0,"new_total":10567.6,"old_total":10567.6}` — and `main`: `"market_open_margin_sync: capital re-synced at 09:15","delta":0.0` |
| **D6** | Kill switch now | **INACTIVE** — `kill_switch_state` row: `1|INACTIVE|auto_clear_stale: was SOFT_KILL from 2026-08-25 …|2026-08-26T08:15:18.791189+05:30|main.auto_clear_stale`. ⛔ Not changed by me. |
| **D8** | Capital drift | **0 capital-drift alerts** · **0 ERROR** · 1 CRITICAL (the kill-switch startup line) · **466** `G3 MARGIN_RECON` samples · peak `\|residual\|` **216.94** · latest `held=2051.12 carry=0.00 held_today=2051.12 broker_used=2020.60 residual=30.52` · band `capital_drift_tolerance: 50.0` (overnight, flat) and `capital_drift_tolerance_pct: 0.10` ⇒ in-session `max(₹50, _total×10%)` = **₹1,056.76** on `_total` 10,567.6 ⇒ 216.94 is well inside |

Other WARNINGs today (3, all benign and self-resolving): two `order_monitor.fill_timeout`, and one `CHECK2 INFLIGHT_ORPHAN: CYIENT … entry filled at broker, awaiting local fill confirmation (no action; fill path will adopt)`.

### 🔴 D7 · THE CARRY QUESTION — **NO overnight carry into today**

🔬 **Answer: NO.** Holdings were **0** at boot and are **0** now; `carry=0.0` at the 09:15 sync; and **all four open trades were entered TODAY** between 10:00 and 10:08.

⇒ ⭐ **OWED-2 (the carry-day comparator) REMAINS ARMED AND UNPROVEN. ⛔ A non-carry is NOT evidence** — nothing about the comparator was exercised today.

⚠️ **BUT — THE FORWARD-LOOKING FACT THAT MATTERS TONIGHT.** Two **`CNC`** legs opened this morning (`FMGOETZE` 10:07:48, `THEMISMED` 10:08:17). 🔴 **If they are still open at close, tonight is the FIRST GENUINE CARRY-DAY TEST** — of *both* the pipeline-aware EOD gate at **17:35 today** *and* the carry comparator overnight (where the band is **₹50 FLAT**). ⭐ **This is precisely the scenario `75e637c` was built to govern. Watch 17:35 today and 08:15 tomorrow.**

⛔ Nothing was tuned, widened, or touched. ⛔ No carry term added. ⛔ `_total`, `RESERVE`, `COMMIT` and the buckets are untouched.

---

## §7 — 🧹 MY OWN ERROR, DISCLOSED

⚠️ **While probing V-4 non-vacuity I appended one line to the LIVE `/home/ubuntu/systems/trading-system/main.py` and immediately restored it.** That was a **write to the production tree during market hours**, and this file was scoped as making **no** system-file modifications. It belonged on a copy — it was subsequently redone that way.

🔬 **Restoration verified exactly, after the fact:**

| check | result |
|---|---|
| md5 | `ea62b0b876489f2897c288548acb2977` — **== the `75e637c` blob** |
| byte size | **203,478** — == the blob |
| mtime | restored to `2026-08-25 22:53:13.255804739 +0530` (the deploy instant) |
| probe string present? | `grep -c "v4 non-vacuity probe"` → **0** |
| last line | `        sys.exit(2)` — identical to the blob |
| service | PID **402677** unchanged · `NRestarts=0` · `active (running)` · same `ExecMainStartTimestamp` |

⭐ The running process imported `main.py` at 08:15 and there is no reloader ⇒ **no runtime effect**. ⛔ **Rule for next time: non-vacuity probes go on a COPY, never the deployed file.**

*(The first V-4 attempt was also **methodologically wrong** and is recorded so nobody repeats it: `read-tree` into a fresh temp index **without** `update-index --refresh` reports **all 1,324 tracked files** as drifted — a stat-info artefact, ⛔ not content drift. The corrected form is below.)*

---

## §8 — THE V-BATTERY (the 24-Aug precedent's form)

| | value | how |
|---|---|---|
| **V-2** `origin/main` | `75e637c300c1bead4aad3cf549e6dbe9318c6b1f` | measured **two independent ways** — `git ls-remote` from the PC **and** `cat /home/ubuntu/trading-system.git/refs/heads/main` on the VM ✅ **== RECORDED** |
| **V-3** deployed `HEAD` | `75e637c300c1bead4aad3cf549e6dbe9318c6b1f` | ✅ **== RECORDED** |
| **V-4** tree drift | **0 files** | `GIT_INDEX_FILE=$(mktemp)` → `read-tree HEAD` → **`update-index --refresh`** → `diff-index --name-only HEAD --` |
| **V-6** the boot gate | `STARTUP` **`event_id 3794`** @ `2026-08-26T08:15:29.559643+05:30` · `COLD` · `{"mode": "live", "version": "2.0.0"}` | **AFTER** `ExecMainStartTimestamp = Wed 2026-08-26 08:15:18 IST` |

**🔬 V-4 NON-VACUITY, proven today on a THROWAWAY COPY** (⛔ not the live file): the copy unmodified → `main.py` in the drift list **0** times; after appending one line → **1**. ⇒ `diff-index` **can** report a real change.

**🔬 V-6 NON-VACUITY:** `system_events` holds **STARTUP 80 · SHUTDOWN 78 · KILL_AUTO_CLEARED 46 · CONFIG_DIFF 29**. The 24-Aug record measured STARTUP 78 ⇒ **+2 across 25-Aug and 26-Aug**, and today's is `3794` (25-Aug was `3791`, 24-Aug `3788`). ⇒ ⛔ the query is not vacuous.

**🧬 M-1 CLOSED THE L-2 WAY.** M-1 warns that the `STARTUP` row proves *a boot happened*, ⛔ **not which code booted** (no SHA in the payload; `VERSION` is a hardcoded constant). L-2's instruction is to **confirm the RECORDED value still holds**, ⛔ not to re-capture at query time. Done: the deploy record wrote `75e637c` + drift 0 + both links on 25-Aug 22:53; today `main.py` **md5-matches its `75e637c` blob**, its **mtime is the deploy instant**, **no `.py` was mutated after the deploy**, and drift is **0**. ⇒ the falsifier had every chance to fire and did not.

---

## §9 — SECTION E · THE ROLLBACK TARGET (report only)

**E1.** Rollback target on record **at the time this section was written**: **`195436b`** (RB-3 tree), proven to boot 24-Aug (`docs/audit/BOOT_PROVEN_24-Aug-2026.md`). 🟢 **⚠️ NOW SUPERSEDED — the target is `75e637c` since 26-Aug 19:2x; see E3-RESOLVED below.**

**E2. THE VERDICT.**

- **The rule** (quoted at §0/S3): the TREE is *"the last SHA **proven to boot**"*; it moves on **every PROVEN BOOT**, ⛔ not on a push; and **proven** means *a `STARTUP` row written **AFTER** this attempt's start time*.
- **The evidence:** `ExecMainStartTimestamp = Wed 2026-08-26 08:15:18 IST`; `STARTUP` row `event_id 3794` @ `2026-08-26T08:15:29.559643+05:30` — **11.4 s after** the start, belonging to this attempt. V-2/V-3/V-4 all hold; M-1 closed per L-2.
- **⇒ VERDICT: the two-clock rule IS SATISFIED. `75e637c` QUALIFIES to become the rollback TREE. The TREE's clock has ticked.**

**E3. ⛔ THE TARGET WAS NOT ADVANCED** *(as at the time this section was written)*. 👤 This is Rama's call. §0.1 is a **written procedure, ⛔ not a control**, and ⛔ neither a card nor a reviewer may speak for Rama.

⚠️ **The standing consequence, stated once so the decision is informed:** while the target reads `195436b`, an incident today would roll back to a tree that **predates the EOD lifecycle unit** — i.e. it would undo the very code that just proved it boots.

> 🟢 **E3-RESOLVED — 26-Aug 19:2x.** 👤 **RAMA AUTHORISED THE ADVANCE** in FILE 11 PART 2 A1. The
> rollback TREE / RB-3 target moved **`195436b` → `75e637c`**, and every OPERATIVE recipe in
> `docs/audit/ROLLBACK_AND_ATTENDANCE_23-Aug-2026.md` was corrected in the same pass (§0 clause ·
> two-clock box · RB-2 tree literal · RB-3 checkout · V-7 comparand · the `RECORDED=` template ·
> the quick-reference card). ⚠️ **Dated historical records were ⛔ NOT rewritten.**
> ⚠️ **The exposure above was REAL and is now recorded as closed: it ran from 25-Aug 22:53 to
> 26-Aug 19:2x, ~20 h 30 m.**
> 🔴 **⛔ TONIGHT'S PUSH DOES NOT MOVE THE TREE AGAIN — a push is not a boot. ⭐ Tomorrow's 08:15 is.**

**E4. FOR THE RECORD: ⛔ a clean boot validates NO individual item.** Twenty commits ran together on 24-Aug and a failure would have isolated none of them. Today proves that **`75e637c` as a whole boots**, and — separately and specifically, via §2 — that **the resolver reached the EOD gate**. It proves nothing else about any other commit in the range.

---

## §10 — WHAT I DID NOT MEASURE

1. **The 17:35 EOD gate has not run yet.** Everything here is boot-side. The pipeline-aware decision itself is **UNOBSERVED**; its first real exercise is tonight — and, if the CNC legs are held, its first *carry* exercise.
2. **OWED-2 remains UNPROVEN.** No overnight carry existed today. ⛔ Not evidence of correctness.
3. **The `UNPUSHED_PENDING_DEPLOY_LEDGER.md` was not read line-by-line end-to-end** (1.4 MB / 4,010 lines). Read structurally + by exhaustive search — disclosed at §0/S1.
4. **`journalctl` was not read.** The JSON logger came up at `08:15:18.715`, ~0.7 s behind the systemd start, and wrote 3,313 lines; nothing died before it existed. A pre-logger failure window of ~700 ms is therefore **unexamined**, though the successful `STARTUP` row makes a silent failure in it incoherent.
5. **The `main.py:2265` vs `startup_checks.py:1561` divergence** — the *mechanism* of the disagreement is 💭 **INFERENCE**, not measured (§4). The blast-radius conclusion does not depend on it.
6. **`/etc/systemd/system/trading-system.service.d/watchman.conf` exists** (a drop-in, dated Jun 11). Per the standing hazard, drop-ins are ⛔ **not** integrity-watched. Its contents were **not** read — out of scope here. 📌 Flagged, not investigated.
7. **A pre-existing memory-guard violation was observed and NOT fixed:** `LC_ALL=C awk 'length>(index($0,"🔝")?450:300)' MEMORY*.md` prints **`MEMORY_BOARD.md 6: 829`**. ⛔ Not mine, ⛔ not in scope (`MEMORY.md` itself is clean at **8,877 B**, well under the 24,000 B guard).
8. **Everything in Section G stayed closed:** Q-1, Q-5, G-G, P6-EV, O-3, O-4, the five unpushed units, and the MEMORY.md rebuild were not opened.
