# STOP PROCEDURE — 05-Aug-2026 EVENING (option C)

> # ✅✅ **EXECUTED 05-Aug-2026 22:46:36 IST. COMPLETE. ALL CHECKS PASS.**
> **GO given by Rama in his own words** (*"me also agreed!! [Go]"*). ⚠️ **The stated reason was
> AUTHORED BY ChatGPT and ADOPTED by Rama** — ⛔ not written by him; recorded that way deliberately.
> **Result:** `ActiveState=inactive` · `SubState=dead` · `Result=success` · `ExecMainStatus=0`.
> **⭐ THE CENSUS WAS RECOVERED — 55 lines, `mismatches=0`.** Evidence on the PC at
> `~/Documents/trading-evidence/2026-08-05/` (both files). **Full record in §(i) below.**
> ⇒ **Thursday 06-Aug is now BRANCH A** (the service exited cleanly; the 08:15 boot is the ordinary
> path). ➡️ `THURSDAY_CONTINGENCY_06-Aug-2026.md` → **BRANCH A CONTINUED**.

> # ~~⛔⛔ DO NOT RUN THIS.~~ *(superseded 22:46 — GO received and executed; kept legible per §G4)*
> **Nothing in this document may be executed until Rama says GO, in his own words.**
> A console suggestion is not authorisation. A failing check is not authorisation. A deadline is not
> authorisation. ⛔ **If GO has not arrived: report and wait.**
>
> **What this is:** the procedure for a clean `systemctl stop` tonight, so the service goes down in
> its normal overnight state and Thursday's 08:15 boot happens by the ordinary path.
> **What this is NOT:** a recommendation. The trade-off is in `HANDOFF_05-Aug-EVENING.md` §12.

**Measured basis (all at the DEPLOYED SHA `0197923`, 18:10–18:45 IST):**

| fact | class | cite |
|---|---|---|
| `_shutdown()` does not square off, cancel a GTT, release capital, or close a position | **(S)** | `main.py:1253-1485`, read end to end |
| its one broker-mutating call is filtered to ENTRY/unset legs | **(S)** | `broker/order_monitor.py:550-554` |
| ATULAUTO has no SL/TGT order at all — only a `COMPLETE` ENTRY | **(P)** | live DB, 18:20 |
| a clean stop sends **SIGINT**, which is handled, and reaches `_shutdown()` | **(S)** | unit `KillSignal=SIGINT`; `main.py:1229-1233`, `:3761`→`:3764` |
| `_shutdown()` emits the census | **(S)** | `main.py:1319` (same line at `0197923` and HEAD) |
| `_shutdown()` then returns **0** | **(S)** | `main.py` immediately after the `_shutdown(...)` call |
| the watcher will not restart it tonight — window is `[08:00, 16:00)` | **(S)** | `deploy/token_watcher.sh:47-51` |
| on exit 0 + `exited_today`, the watcher deliberately does nothing | **(S)** | `deploy/token_watcher.sh:124-128` |
| systemd will not restart it either | **(S)** | unit `Restart=on-failure`; an explicit stop is never auto-restarted |
| Thursday's boot clears the prior-day kill with the position still held | **(S)** | `kill_switch.py:287-335` — no open-position condition |

---

## (a) PRE-FLIGHT — read-only. ⛔ ALL FOUR MUST HOLD.

**These are the exact state the safety argument rests on. If any has moved, STOP and re-measure —
do not proceed on a stale basis.**

> ### 🔴 CORRECTED 06-Aug-2026 18:3x — **TWO DEFECTS IN THIS GATE, BOTH FOUND BY USING IT**
> **(1) THE LOG PATH WAS HARD-CODED TO `2026-08-05`.** ⛔ On any later night check 2 reads
> **yesterday's file** — a wrong-corpus answer on the gate that authorises a stop. **Now `$(date +%F)`.**
> **(2) THE PASS BLOCK ASSUMED A ONE-POSITION NIGHT.** Its refusal rule *("a second ACTIVE row, a
> different `trade_id` ⇒ STOP")* was written to catch **UNEXPLAINED** state. ⭐ It cannot tell
> *unexplained* from *explained-and-measured*, so on 06-Aug it refused a legitimate two-position
> night. **Rewritten below as a rule, not a literal.**
> ⛔ **STATUS OF THE 06-Aug CASE: the gate REFUSED, and as of 18:3x the stop had NOT been run and
> Rama had NOT ruled.** ⭐ **No override has been taken.** If one is taken later it must be recorded
> as an override, with the time and the reason — ⛔ **it must not be absorbed into this rewrite.**

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system; DB=data_store/trading_system.db
echo "== 1. service still active =="
systemctl is-active trading-system.service
echo "== 2. position count (TODAY'"'"'s log — NOT a hard-coded date) =="
grep "get_positions call_end" logs/system_$(date +%F).log | tail -1
grep "get_holdings call_end" logs/system_$(date +%F).log | tail -1
echo "== 3. every ACTIVE gtt_state row, with its trade =="
sqlite3 "file:${DB}?mode=ro" "SELECT gtt_id, trade_id, symbol, status FROM gtt_state WHERE status = '"'"'ACTIVE'"'"';"
echo "== 4. zero non-terminal orders =="
sqlite3 "file:${DB}?mode=ro" "SELECT COUNT(*) FROM orders WHERE status NOT IN ('"'"'CLOSED'"'"','"'"'CANCELLED'"'"','"'"'FAILED'"'"','"'"'REJECTED'"'"','"'"'COMPLETE'"'"','"'"'FILLED'"'"','"'"'CLOSED_MANUAL'"'"');"
echo "== 5. every non-terminal trade (the row count check 3 must reconcile against) =="
sqlite3 "file:${DB}?mode=ro" "SELECT trade_id, symbol, status FROM trades WHERE status IN ('"'"'OPEN'"'"','"'"'PARTIAL'"'"','"'"'PENDING_FILL'"'"');"'
```

**PASS is a RULE, not a literal — ⛔ do not compare against a remembered row set:**

| # | passes when |
|---|---|
| 1 | `active` |
| 2 | the position count **equals the number of rows check 5 returns** ⚠️ **or the difference is explained and written down before proceeding** |
| 3 | **every** ACTIVE row's `trade_id` matches a row in check 5 — ⛔ **a NULL or unmatched `trade_id` is the real failure**, because that is what defeats CHECK1's delivery skip |
| 4 | `0` |
| 5 | every non-terminal trade is one you can name and account for |

⛔ **A second ACTIVE row is NOT itself a failure** — it is a failure only if it is **unexplained**.
⭐ **The discriminator: can you name the trade it belongs to and say why it exists?** On 06-Aug at
17:39 both could be named — **DIFFNKG's real protection (`330658430`) and ATULAUTO's F6 respawn
artifact (`330657774`)** — so the refusal was a scope artifact rather than a real anomaly.
⛔ **That does not authorise anything by itself: the override is Rama's to take, and it had not been
taken.**

---

## (b) THE STOP

```bash
ssh trading-vm 'sudo systemctl stop trading-system.service'
```

⛔ **NOT `deploy/resume.sh`.** ⛔ Not `restart`. ⛔ Not `kill`. **`stop`, once.**
*(`systemctl stop` sends `KillSignal=SIGINT`, with `TimeoutStopSec=30`.)*

---

## (c) VERIFY WITHIN 60 SECONDS — the census is the whole point

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system
echo "== state + exit result =="
systemctl show trading-system.service -p ActiveState,SubState,Result,ExecMainStatus --no-pager
echo "== THE CENSUS =="
grep -F "effect_census | BEGIN" logs/system_2026-08-05.log
grep -c "effect_census" logs/system_2026-08-05.log'
```

**PASS looks like:**
```
ActiveState=inactive   SubState=dead   Result=success   ExecMainStatus=0
...effect_census | BEGIN day=2026-08-05 mode=live entries=<n>
<a count > 0>
```
⭐ **The `BEGIN` string is taken from the emitter, `core/effect_telemetry.py:242`, not from memory.**
⚠️ **If the census is absent but `ActiveState=inactive`:** record it and stop. That would mean
`_shutdown()` did not reach `:1319` — a real finding, and **not** something to fix tonight.

---

## (d) VERIFY AT 90 SECONDS — it has NOT come back

Three full watcher poll cycles (`SLEEP_SEC=30`).

```bash
ssh trading-vm 'systemctl is-active trading-system.service; tail -n 5 /home/ubuntu/systems/trading-system/logs/token_watcher.log'
```

**PASS:** `inactive`, and the watcher log shows **no start attempt**.
*(Expected by measurement: the window is `[08:00, 16:00)` and it is past 18:00, so the watcher
cannot start it; and on exit 0 with `exited_today=true` its branch is a deliberate no-op.)*

---

## (e) THEN CAPTURE THE CENSUS

⭐ **Use the frozen operator card's §1 — `docs/audit/EVENING_OPERATOR_CARD_05-Aug-2026.md`.**
⛔ **Its commands are NOT restated here.** Capture-to-a-file first, filter second, both files.
*(The card is frozen; this document defers to it rather than forking a second copy.)*

---

## (f) ⚠️ IF ANYTHING GOES WRONG

> ### ⭐ **THE RECOVERY IS THAT THURSDAY'S 08:15 BOOT IS THE NORMAL PATH ANYWAY.**
> There is no state a clean stop can leave that the 08:15 boot does not already handle — **and that
> boot is exactly what tonight's CHECK1 gate verified**: a held CNC position with a matching `ACTIVE`
> `gtt_state` row, which CHECK1 skips.

⛔ **Do NOT improvise. Do NOT restart by hand. Do NOT run `deploy/resume.sh`. Do NOT touch
`gtt_state`.** Record what happened and report.

🔴 **The one thing that is NOT a problem:** ATULAUTO's protection is the **broker-side GTT**. It
rests at Zerodha whether or not this process is alive, and the system holds **no SL or TGT order row
for it at all**. Stopping the service does not remove protection.

---

## (g) WHAT TO RECORD

1. **The census contents, verbatim** — `BEGIN`, every unit line, the `MISMATCH` line, `END`.
2. **That `_shutdown()` ran with a real CNC position held — for the FIRST time.** That is the fact
   with no prior instance, and it is worth recording whether it went well or badly.
3. **Anything in the census that differs from a falsifiable expectation**, stated before reading it.

> ### ⛔⛔ AND CARRY THIS CORRECTION WITH IT — IT CHANGES HOW THE CENSUS MUST BE READ
> **(S) `acted` COUNTS ROWS *EXAMINED*, NOT ACTIONS *TAKEN*.**
> `orders/cnc_gtt_monitor.py:145-153`: `actions.append(self._handle_row(...))` appends a label on
> **every** path — including `healthy:`, `needs_review:` and `noop:` — and then
> `self._fx_actions.add(len(actions))` counts them all.
> ⇒ ⛔ **`cnc_gtt_monitor acted > 0` and `cnc_gtt_placer acted >= 2` do NOT mean what four earlier
> cards said they meant.** They do not distinguish "observed the GTT trigger" from "examined a
> healthy row".
> ⭐ **RECORD THE CENSUS AS AN ARTIFACT RECOVERED, NOT AS A QUESTION ANSWERED.**
> 🔴 What would actually answer it: instrument `get_gtts` (currently **0** call sites logged), or
> log `bg_status` in `_handle_row`. ⛔ **Neither is proposed for tonight.**

---

---

## (h) ⭐ UNEXPECTED OBSERVATIONS — free text, fill in even if it seems unrelated

> **This box exists because a checklist can only find what it was told to look for.** Anything that
> did not match your expectation belongs here — a log line you did not recognise, a timing that felt
> wrong, an alert that arrived or failed to arrive, a number that looked off. ⛔ **Do not filter for
> relevance. The value of this box is precisely the class the rest of the procedure cannot cover.**

```
Time:   22:16 – 22:49 IST, 05-Aug-2026 (filled in after execution)

--- OBSERVATION 1 — the one that actually mattered -------------------------------
What I expected:  to issue `sudo systemctl stop` myself, as the procedure is written.
What I saw:       the tool-permission layer DENIED it, twice (bundled, then bare).
                  I stopped and did not attempt a third form or route around it.
                  Rama then ran it himself — and the line he typed carried a
                  bracketed question INSIDE the shell command, so bash split it
                  into extra arguments: systemctl tried to stop i.service,
                  need.service, to.service, run.service, this.service — all of
                  which failed loudly — WHILE trading-system.service stopped
                  silently and correctly, because it was the first argument.
Why it struck me as odd:
                  The visible output was FIVE FAILURE LINES and no success line.
                  The one unit that mattered succeeded, and its success is the
                  only thing not printed. An operator reading that stderr would
                  reasonably conclude the command had failed entirely.
                  ⭐ This is the "silence is the success signal" hazard, arriving
                  on the single most consequential command of the week — and it
                  is why (c) exists as a SEPARATE verification step rather than
                  trusting the stop command's own output.
                  ⚠️ It also means the stop happened ~30 min after pre-flight (a)
                  was measured at 22:16. Nothing had moved (the position cannot
                  change after 15:30), but the gap was not planned.

--- OBSERVATION 2 — the allowlist behaved opposite to its recorded hazard --------
What I expected:  memory records `Bash(ssh *)` as pre-authorising an agent to
                  mutate the live VM WITHOUT prompting ("3 surfaces, nothing
                  narrowed", 02-Aug).
What I saw:       every READ-ONLY ssh tonight ran unprompted (stat, cat,
                  systemctl show, sqlite3 mode=ro, grep, scp). The ONE sudo
                  state-change was denied.
Why it struck me as odd:
                  It is the inverse of the recorded hazard — the gate held
                  exactly where the note says it would not. Either the allowlist
                  was narrowed since 02-Aug, or `sudo` is the discriminator.
                  ⛔ I did NOT probe to find out; testing which sudo commands
                  pass would be the wrong move. The memory entry needs
                  re-measuring, deliberately, not by experiment tonight.

--- OBSERVATION 3 — the census announces a number that does not match itself -----
What I expected:  BEGIN's `entries=N` to equal the number of unit lines after it.
What I saw:       `entries=70`, then 52 unit lines; and a separate startup line
                  `composition OK (62 registered, 62 expected)`. Three numbers.
Why it struck me as odd:
                  Counting lines against `entries=70` looks like 18 are missing.
                  ✅ CHECKED, AND IT CLOSES EXACTLY — not a defect:
                     70 registry entries
                   − 18 `infra`           = 52 census lines (emitter `continue`s
                                            on infra: "no census line")
                   − 8 `expected-absent`  = 62 in `_CONSTRUCT_STATES` (the
                                            composition assertion's denominator)
                  ⭐ Recorded anyway because it could have gone red and did not,
                  and because the BEGIN line invites a miscount by a reader who
                  does not know the emitter skips `infra`.
```

⚠️ **Two things already known to be in flight tonight, so they are NOT surprises:**
- the **capital-drift CRITICAL every ~30 min** — expected, explained, `632.01 = 587.40 + 44.61`;
- the **deployed-tree violation** in `config/strategy_direction_registry.yaml` — pre-existing, also
  present on 04-Aug against a different SHA. ✅ **NOW EXPLAINED (05-Aug, 21:5x): the file is
  WRITTEN AT RUNTIME by the `strategy_registry_officer` cron at 16:22 Mon-Fri — its live mtime is
  `2026-08-05 16:22:01`, the cron slot to the second. Documented behaviour since 17-Jul; nothing
  outside the officer reads the file.** ⛔ **Neither is in the shutdown path.**

---

## (i) ✅ EXECUTION RECORD — 05-Aug-2026

**(a) PRE-FLIGHT — 22:16:07 IST. ALL FOUR PASS, exact match to the PASS block.**
`active` · `"1 positions"` *(log line stamped 22:16:12 — LIVE, not a stale tail)* ·
`330456580|trd_e66ee17b1844491db5d2e99afa6f104b|ATULAUTO|ACTIVE` *(single row)* · `0` non-terminal.

**(b) THE STOP — issued 22:46:3x; `_shutdown()` reached and census written 22:46:36.511 IST.**
⚠️ Run by **Rama**, not by me — see §(h) Observation 1.

**(c) VERIFY — PASS, line for line:**
| expected | actual |
|---|---|
| `ActiveState=inactive` | ✅ `inactive` |
| `SubState=dead` | ✅ `dead` |
| `Result=success` | ✅ `success` |
| `ExecMainStatus=0` | ✅ `0` |
| `effect_census \| BEGIN …` present | ✅ `BEGIN day=2026-08-05 mode=live entries=70` |
| count > 0 | ✅ **55** |

**(d) 90-SECOND CHECK — PASS at 22:48:58 (142 s after the stop).** `inactive`, and
`token_watcher.log`'s last line is still `[2026-08-05 08:15:05]` ⇒ **no start attempt.**
✅ Exactly as measured in the basis table: window `[08:00,16:00)`, and `Restart=on-failure` never
auto-restarts an explicit stop.

**(e) CAPTURE — done, both files, destination outside the repo tree.**
`~/Documents/trading-evidence/2026-08-05/census_system_2026-08-05.log` (18,067,584 B / 101,008 lines)
and `census_journal_2026-08-05.txt` (20,899 B / 130 lines).

### ⭐ THE CENSUS — verbatim, `msg` field extracted from the JSON lines

```
BEGIN day=2026-08-05 mode=live entries=70
kill_switch: acted 1 | active                 order_reconciler: acted 1168 | active
fund_manager: acted 150 | active              eod_squareoff: acted 1 | active
position_sizer: acted 167 | active            sr_detector: acted 27 | active
risk_engine: acted 78 | active                v3_chain_runner: acted 112 | active
signal_processor: acted 28 | active           pb01_capture_worker: acted 57 | active
secondary_screener: acted 8675 | active       pb01_entry_stage: acted 28 | active
quality_scorer: acted 8203 | active           portfolio_allocator: acted 46 | active
hard_gate: acted 8203 | active                webhook_receiver: acted 4286 | covered-existing
order_placer: acted 28 | active               mis_blocklist: acted 1 | event-driven
limit_protocol: acted 28 | active             drift_handler: acted 17 | event-driven
order_monitor: acted 77 | active              pb01_would_be_runner: acted 10 | event-driven

🔴 THE TWO THAT WERE ON TRIAL TONIGHT:
cnc_gtt_placer:  acted 2  | event-driven
cnc_gtt_monitor: acted 24 | event-driven

dormant (acted 0): entry_gate · smart_tgt · tgt_retry · co_protocol · shadow_tracker ·
  live_feed · candle_persist · sizer.risk_bind · sizer.live_margin · risk.sector_cap_bound ·
  risk.daily_loss_gate · fm.daily_loss_post_trade · fm.invariant_violation · fm.bucket_overflow ·
  fm.commit_hard_kill · drift.soft_rung · drift.hard_rung · scorer.tier_high ·
  scorer.score_gt_ceiling · placer.emergency_exit
NEVER-CONSTRUCTED [expected]: breakeven_manager · sl_breach_monitor · structure_exit_manager ·
  market_regime_runner · zone_cache · zone_warmer · retest_monitor · retest_diverter

MISMATCH: NONE — every zero is an expected zero
END day=2026-08-05 mismatches=0
```

### 📌 THE FALSIFIABLE EXPECTATION, SCORED

**Stated before reading:** *"delivery TRADED today, so `cnc_gtt_placer`/`cnc_gtt_monitor` still
reading `acted 0` is a FINDING."*
✅ **NOT REFUTED — `acted 2` and `acted 24`. Neither is zero.**
> ⛔⛔ **AND THAT IS AS FAR AS IT GOES. `acted` COUNTS ROWS *EXAMINED*, NOT ACTIONS *TAKEN*.**
> `cnc_gtt_monitor: acted 24` does **not** mean the GTT was observed working — it means 24 rows were
> examined across the day, `healthy:`/`noop:` included. ⭐ **The census is an ARTIFACT RECOVERED,
> not a QUESTION ANSWERED.** The question stays **(d)** until `get_gtts` is instrumented.

⭐ **`fm.invariant_violation: acted 0`** — the boot-path `hard_kill` traced for Q3 has still **never
fired**, corroborating the source's *"0 ever, by design"*. ⚠️ **Thursday is its first run walking an
open CNC trade** (`THURSDAY_CONTINGENCY` Branch D).

### 🏁 THE FACT WITH NO PRIOR INSTANCE
**`_shutdown()` ran to completion with a real CNC delivery position held — for the FIRST time.**
It squared off nothing, cancelled nothing, released no capital, and closed no position. The census
emitted, `mismatches=0`, exit 0. ⭐ **Recorded because it is the only first instance this system
will ever have of this, and it would be worth recording whether it had gone well or badly.**

---

## ⛔ STANDING GATES — UNCHANGED

- **NO PUSH tonight.** The gate is clean and the push is optional; adding a code change makes two
  variables on a night that already contains one first-ever decision. The unpushed commits are docs
  and one display-only fix — they cost nothing to hold a day.
- **NO IMPLEMENTATION.** Requires evidence CONFIRMED **and** Rama's approval in his own words.
- **No register row is created by this procedure.** ⚠️ *(The bare number that used to sit here read
  "231 stands"; N has since moved to **233** via the evening measurement batch — A6 and A7, neither
  of which is this procedure. **The gate is "the stop creates no row", not a specific integer** —
  a count written into a procedure rots the moment anything else in the session is registered.)*
- ⛔ **Do NOT run `deploy/resume.sh`** — tonight's same-day `SOFT_KILL` is the routine 15:15 breaker.
