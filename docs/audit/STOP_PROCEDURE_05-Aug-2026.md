# STOP PROCEDURE — 05-Aug-2026 EVENING (option C)

> # ⛔⛔ DO NOT RUN THIS.
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

```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system; DB=data_store/trading_system.db
echo "== 1. service still active =="
systemctl is-active trading-system.service
echo "== 2. position count still 1 =="
grep "get_positions call_end" logs/system_2026-08-05.log | tail -1
echo "== 3. gtt_state: one ACTIVE row for ATULAUTO with matching trade_id =="
sqlite3 "file:${DB}?mode=ro" "SELECT gtt_id, trade_id, symbol, status FROM gtt_state WHERE status = '"'"'ACTIVE'"'"';"
echo "== 4. zero non-terminal orders =="
sqlite3 "file:${DB}?mode=ro" "SELECT COUNT(*) FROM orders WHERE status NOT IN ('"'"'CLOSED'"'"','"'"'CANCELLED'"'"','"'"'FAILED'"'"','"'"'REJECTED'"'"','"'"'COMPLETE'"'"','"'"'FILLED'"'"','"'"'CLOSED_MANUAL'"'"');"'
```

**PASS looks like:**
```
1.  active
2.  ..."result_summary":"1 positions"
3.  330456580|trd_e66ee17b1844491db5d2e99afa6f104b|ATULAUTO|ACTIVE
4.  0
```
⛔ **Anything else — especially a second ACTIVE row, a different `trade_id`, or a non-zero count —
means STOP. Do not continue. Re-measure and re-decide.**

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
Time:
What I expected:
What I saw:
Why it struck me as odd (even if I think it is nothing):


```

⚠️ **Two things already known to be in flight tonight, so they are NOT surprises:**
- the **capital-drift CRITICAL every ~30 min** — expected, explained, `632.01 = 587.40 + 44.61`;
- the **deployed-tree violation** in `config/strategy_direction_registry.yaml` — pre-existing, also
  present on 04-Aug against a different SHA. ⛔ **Neither is in the shutdown path.**

---

## ⛔ STANDING GATES — UNCHANGED

- **NO PUSH tonight.** The gate is clean and the push is optional; adding a code change makes two
  variables on a night that already contains one first-ever decision. The unpushed commits are docs
  and one display-only fix — they cost nothing to hold a day.
- **NO IMPLEMENTATION.** Requires evidence CONFIRMED **and** Rama's approval in his own words.
- **231 stands.** No register row is created by this.
- ⛔ **Do NOT run `deploy/resume.sh`** — tonight's same-day `SOFT_KILL` is the routine 15:15 breaker.
