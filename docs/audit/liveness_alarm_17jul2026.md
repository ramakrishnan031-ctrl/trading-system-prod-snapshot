# Liveness alarm — trading-system can no longer die silently

**Built + deployed 17-Jul-2026.** `scripts/liveness_probe.py` + `tests/unit/test_liveness_probe.py`
+ a `cron_registry.yaml` entry. No schema change. Monitoring/ops only — it **reads** service
state and alarms; it touches no capital/kill/order/signal runtime.

**Why:** today the S4 boot fix halted the service at 08:16:09 with a **clean exit 0**. It took
**0 trades on a live trading day** and nothing noticed for the whole session
(`docs/audit/s4_boot_outage_17jul2026.md`). A headless system that can die silently is the gap.

---

## 1. Investigation

### Q1 — the existing canary, and ⭐ a correction to the S4 report

`scripts/monitoring_canary.py`, cron `20 8 * * *` (**08:20, daily**, `market_day_only: false`).
Five probes — email (SMTP login), telegram (getMe), sentinel ingestion, dashboard, respawn —
each `(bool, str)`, pure and dependency-injected; Telegram WARNING on failure with a CRITICAL
sentinel fallback.

**⭐ The S4 report's own claim was imprecise, and this investigation disproved it.** That report
said the canary *"watches for a restart loop, not liveness, so a cleanly-dead service scores
the good value"* — which implies the canary **looked at** trading-system and misjudged it. It
never looked at all:

| Canary probe | Unit it actually watches | file:line |
|---|---|---|
| `check_service_respawn` | **`alert-watcher.service`** | `monitoring_canary.py:220` |
| `check_dashboard` | **`gui-dashboard`** | `monitoring_canary.py:124` |
| `run_canary` | mentions `trading-system` **nowhere** | `:258-275` |

So `{"nrestarts": 0}` in `canary_service_state.json` is **alert-watcher's** count. The truth is
simpler and worse: **no monitor has ever watched trading-system's liveness.** Corrected in the
S4 report and pinned by `test_old_canary_would_not_have_caught_it`.

And restart-counting could never have caught it anyway: `Restart=on-failure` + a clean **exit 0**
⇒ systemd never restarts, `NRestarts` stays **0** — the *healthy* value.

**Extend the canary, or a sibling probe? → SIBLING.** The canary's probes are expensive and
external (an SMTP login and a Telegram `getMe` **every run**); at a 5-minute cadence that would
hammer both services ~84×/day and risk rate-limits. Liveness needs high frequency; the canary is
a daily plumbing self-test. Different cadence, different job. **The infra is reused** (alert
delivery contract, cron registry, the authoritative calendar, and the canary's own
`_parse_systemctl_show` / `_load_service_state` / `_save_service_state`) — **no parallel stack.**

### Q2 — the expected service window

| Fact | Where |
|---|---|
| `SERVICE_WINDOW_START = 08:00`, `SERVICE_WINDOW_END = 16:00` (IST) | `main.py:1521-1522` |
| the service **never self-exits before 16:00** — `_eod_self_exit_due` returns `(False, -1)` *without querying* before `window_end` | `main.py:983-990` |
| it boots ~**08:15** (token cron `15 8 * * 1-5` → token-watcher) → ~08:30 premarket | `main.py:1517-1519` |
| it stays up **past** 16:00 if positions are still open | `_eod_self_exit_due` (flat-only) |

⇒ **Liveness window = [09:00, 16:00).**

- **Upper bound 16:00 is exact, not approximate.** At 15:59 the service *must* still be up; at
  16:00 a clean exit is legitimate. It equals `SERVICE_WINDOW_END` — drift-guarded by a test.
- **Lower bound is deliberately NOT 08:00.** 08:00 is when the service *may* start, not when it
  *must* be up — 08:00–08:30 is a legitimate not-yet-up gap, so alarming there would be a
  **guaranteed daily false alarm**. 09:00 clears every legitimate start path and is still 15 min
  before the 09:15 open (and an hour before the 10:00 entry window).
- **Today's 08:16:09 death would have been caught at 09:00.**

### Q3 — intentional-down vs unexpected death (the crux)

| Legitimate state | Detected by | Verdict |
|---|---|---|
| weekend / NSE holiday | `utils.holiday_guard.is_trading_day` (the S1 authority), fail-open | **SILENT** |
| operator planned pause | `kill_switch_state.state IN ('SOFT_KILL','HARD_KILL')` | **SILENT** |
| outside the window | `[09:00, 16:00)`, in code *and* in the cron expression | **SILENT** |
| unit is active | `ActiveState=active` | **SILENT** |
| **unexpected death** | inactive + trading day + in-window + no kill marker | **ALARM** |

`kill_switch_state` is the single-row KS3 table (`state`/`reason`/`triggered_at`/`triggered_by`).
The 16-Jul pause is exactly the shape to honour: `SOFT_KILL`, *"planned pause for pending
fix/review work — no trading issue"*, `by=operator`. An active kill makes `main()` halt by
design, so parked ⇒ inactive is expected.

**Today's row was `INACTIVE`** (auto-cleared 08:15:52 by `main.auto_clear_stale`) while the
service was dead ⇒ the alarm fires. That is the case that was missed.

**⭐ A deliberate `systemctl stop` is NOT distinguishable from a silent death — so it ALARMS.**
Today's outage was itself `Result=success` / `ExecMainStatus=0`: `_shutdown_event.set()` produces
a *graceful* shutdown, whose systemd signature is identical to an operator `systemctl stop`.
Nothing in service state separates them. Per the instruction's own rule — *a missed real death is
worse than a mutable false alarm* — an unmarked stop alarms, honestly. This is not merely a
concession: **the operator already has the documented way to say "intentional" — park it with a
SOFT_KILL**, which is what the 16-Jul pause did and what this probe honours. The limitation is
therefore bounded to "an operator stopped it *without* using the pause workflow".

**Fail-open direction — checked, not inherited.** The S1 guard degrades to a plain weekday check
on any calendar error. For the token job fail-open means *run* (never starve a trading day). For
this alarm it means *alarm on a weekday holiday if the calendar is unreadable* — one cheap false
alarm versus a silenced real death. **Same asymmetry, same direction.** Kept.

Two further "silence must not be buyable" decisions, both tested:
- an **unreadable `kill_switch_state`** returns `UNKNOWN`, which is deliberately **not** in the
  suppression set — a corrupt DB cannot mute a real death;
- an **unreadable `systemctl`** during the window **alarms** rather than going quiet — silence is
  the failure mode being fixed.

### Q4 — alerting + dedup

Reused verbatim from the canary's contract: `TelegramNotifier.from_env(...).send(severity=
"CRITICAL", ...)`, and on failure `write_critical_sentinel(...)` so it escalates through the F1
fallback.

**Dedup key = the unit's `InactiveEnterTimestamp`** — a natural incident id, and already the
"since" the operator needs. One `systemctl show` yields state, the timestamp, and the exit
signature. Persisted to `data_store/liveness_alarm_state.json` via the canary's atomic
tmp+`os.replace` helper.
- down all session (~84 probes) → **one** alarm;
- recovers, then dies again → the timestamp changes → **a new alarm** (dedup must not mute a
  second real outage);
- systemd gives no timestamp → the key degrades to `date:<today>` ⇒ at most **one alarm/day**,
  never one every 5 minutes.

## 2. The check

`*/5 9-15 * * 1-5` (the `capture_metrics` precedent — the only other 5-minute job), with
`market_day_only: true`, `cadence: intraday`, **`monitored: false`** (a 5-minute job cannot be
heartbeat-expected). **The schedule *is* the window**: first run 09:00, last run 15:55 — inside
[09:00, 16:00) by construction — and the window is re-checked in code so a hand-edited crontab or
a manual run cannot produce an out-of-window alarm.

The verdict lives in a **pure** `classify_liveness(...)` (no I/O); `probe_liveness(...)` is a thin
wrapper with every dependency injectable.

**⭐ The probe deliberately does NOT import `main.py`.** It must not depend on the health of the
thing it monitors: if `main.py` failed to import, the probe would die exactly when the system is
broken — reproducing the silent failure it exists to prevent. The window constants are therefore
duplicated, and **`test_window_end_matches_main` asserts they have not drifted from
`main.SERVICE_WINDOW_END`**. Drift is caught by the suite, not paid for at runtime.

The alarm names the smoking gun:

```
🔴 [LFL836] LIVENESS: trading-system.service is DOWN during the service window
since: Fri 2026-07-17 08:16:09 IST
now:   17-Jul 09:00 IST (window 09:00-16:00)
state: ActiveState=inactive SubState=dead Result=success ExecMainStatus=0
...
(Result=success + ExecMainStatus=0 means it shut itself down cleanly, not that it
 crashed — check the boot self-checks in the journal.)
```

`Result=success` + `ExecMainStatus=0` is the detail that points straight at a self-inflicted
shutdown rather than a crash — the thing that would have found S4 in minutes.

## 3. The false-alarm matrix — results

| Scenario | Expected | Test | Result |
|---|---|---|---|
| **17-Jul reconstructed**: inactive, trading day, in-window, no marker | **ONE alarm** | `test_fires_on_the_17jul_outage` | ✅ |
| the old canary cannot see this unit | pinned | `test_old_canary_would_not_have_caught_it` | ✅ |
| NSE holiday, inactive, in-window | silent | `test_silent_on_nse_holiday` | ✅ |
| operator `SOFT_KILL` / `HARD_KILL` | silent | `test_silent_when_operator_parked_it` ×2 | ✅ |
| 08:16 / 08:59 / 16:00 / 18:30, inactive | silent | `test_silent_outside_the_window` ×4 | ✅ |
| unit active | silent | `test_silent_when_alive` | ✅ |
| down across 12 probes | **exactly 1** alarm | `test_dedup_one_alarm_across_many_probes` | ✅ |
| down → up → **different** death | alarms **again** | `test_a_second_distinct_death_alarms_again` | ✅ |
| no `InactiveEnterTimestamp` | ≤1 alarm/day | `test_missing_timestamp_degrades_to_one_alarm_per_day` | ✅ |
| systemctl unreadable in-window | **alarms** (never quiet) | `test_unreadable_systemctl_still_alarms` | ✅ |
| kill switch unreadable | **alarms** (silence unbuyable) | `test_unreadable_kill_switch_does_not_buy_silence` | ✅ |
| calendar unreadable | fails open to weekday | `test_calendar_fails_open_to_weekday` | ✅ |
| window end vs `main` | no drift | `test_window_end_matches_main` | ✅ |

**21 passed, rc=0.**

### The S4 lesson, applied

The matrix tests inject a runner for determinism — which is *exactly* the fixture pattern that
hid S4 (`secret_token=None` in every wired fixture meant the auth branch never executed, so the
401 could not occur in any test). So the probe's **production default is pinned separately**:
`test_default_runner_shells_out_to_systemctl` monkeypatches the real boundary
(`subprocess.run`), asserts the argv is `systemctl show … trading-system.service … ActiveState`,
and asserts the output is genuinely **parsed** (`props["ActiveState"] == "inactive"`), not
defaulted. Without it, a refactor that quietly turned the default into an assume-healthy stub
would leave every other test green while the probe is blind.

### Proof the tests could have been red

The module is new, so the whole file is fail-on-old (ImportError) — the canary's own precedent.
Stronger, a **mutation check**: replacing the alarm verdict with `return False, "ALIVE", ...`
(a probe that can never alarm — the exact defect this exists to catch) turned **7 tests red**,
including `test_fires_on_the_17jul_outage`; restoring returned **21 passed**
([[feedback-verify-rc-not-output]] — a green check is evidence only if it could have been red).

## 4. Regression

**Full suite (not scoped): `11 failed, 4875 passed, 4 skipped`, rc=1, in 793.67 s (13:13).**

The **11** are the known **out-of-window** PC-env set, matched **by name** (the run was at
~17:00 IST — [[pc-test-env-hygiene]]'s time-gate applies, so 11 is the correct baseline, not
10): `test_main` ×4 · `test_order_placer_fix061` ×4 · `test_fix181` ×1 · `test_phase17_batch2`
×1 · `test_interactive_startup::test_holiday_guard_missing_yaml_proceeds` ×1.

**Count reconciles exactly:** the pre-liveness deploy run was `11F / 4854P`; **4854 + 21 new
liveness tests = 4875**, and the failure count is unchanged at 11.

**0 attributable — proven two ways, not name-matched:**
1. the failure set is **byte-identical** to the pre-liveness run (`diff` of the sorted `FAILED`
   lines = **empty**), at the same out-of-window time of day;
2. this change's only non-new-file edits are `config/cron_registry.yaml` and the generated
   `deploy/cron/trading-system.cron` — and **none of the 5 failing suites references
   `cron_registry`, the crontab, or `liveness`** (grep = 0 each), so the one globally-read file
   I touched cannot reach them.

Cron framework intact: `generate_crontab.py --selftest` → **48/48 lines round-trip
byte-for-byte**, rc=0; `test_cron_registry.py` + `test_cron_alerts.py` → **42 passed**, rc=0.

## 5. Deploy — verified

Tag **`deploy-17jul-liveness`** → **`6a4c092`** (the code identity; delta tag..HEAD = **0
non-markdown files**). Backup `pre_deploy_liveness_20260717_170239.db`, verified **sound**
(`quick_check=ok`, v44) rather than merely present. Schema **v44, no migration**.

| Check | Result |
|---|---|
| PC HEAD == VM bare HEAD | ✅ `164b72c` |
| **cron entry registered** | ✅ `*/5 9-15 * * 1-5 … scripts/liveness_probe.py` present in the live crontab |
| non-comment cron lines | ✅ **48 → 49** (exactly one job added) |
| live crontab == deployed canonical | ✅ identical |
| `generate(registry)` == canonical **on the VM** | ✅ registry / canonical / live **all three agree** |
| schema / integrity / FK | ✅ v44 · `quick_check=ok` · FK clean |
| `e4-w10-pnl-contract` still unpushed | ✅ (sign-off gated) |

**Unlike the previous two deploys, this one DOES change the crontab** — so the hook's install
condition was verified up-front (`generate(registry)` byte-identical to the canonical via the
LF writer) and confirmed after (`post-receive: crontab AUTO-INSTALLED from canonical`). The
generator's `--selftest` passes 48/48 round-trip.

### ⭐ End-to-end proof on the REAL box — it would have caught today's outage

The system is **still down**, so the deployed probe was pointed at it with **real systemctl,
real NSE calendar, real `kill_switch_state`** — only `now` moved to an in-window time, and the
*decision* function called rather than `main()`, so no alarm was actually sent:

```
REAL inputs:  kill_switch_state = INACTIVE   trading day today = True

AT 09:00 (in-window), REAL dead service:
  -> DOWN | alarm = True
  detail: trading-system.service=inactive since Fri 2026-07-17 08:16:09 IST
  state:  ActiveState=inactive SubState=dead Result=success ExecMainStatus=0

AT 09:05 (same incident) -> ALREADY_ALARMED | alarm = False
AT the real time now     -> OUTSIDE_WINDOW  | alarm = False

VERDICT: WOULD HAVE CAUGHT TODAY'S OUTAGE, once, and is silent out-of-window   (rc=0)
```

It fires on the **real** 08:16:09 death timestamp, dedups against the **real** incident id, and
stays silent outside the window — on production state, not a fixture.

## 6. Parity

Monitoring is mode-agnostic — confirmed: `liveness_probe.py` has **no** paper/live reference. It
reads systemd state, the NSE calendar and `kill_switch_state`, none of which differ by mode.

## 7. Limitations (stated, not hidden)

1. **An operator `systemctl stop` without a kill marker will alarm** — indistinguishable from a
   death (both are a clean exit 0). Deliberate; the pause workflow (SOFT_KILL) is the documented
   way to signal intent, and it is honoured.
2. **The 08:00–09:00 gap is unwatched.** A death between the 08:15 boot and 09:00 is caught at
   09:00, not instantly — the price of never false-alarming on the boot chain. Still an hour
   before the 10:00 entry window.
3. **After 16:00 is unwatched** — a clean exit is legitimate there, and a service holding open
   for live positions past 16:00 is a *different* signal, not this one.
4. **It proves the unit is `active`, not that it is trading correctly.** Liveness only. A wedged-
   but-running process is out of scope (that is the canary's / Cron Officer's territory).

## 8. Out of scope (per the instruction)

The `secret_token=None` **fixture blindness** (the next item) · the **Monday 08:15 boot watch**
(observe) · the **`_shutdown_event.set()`-halts-the-day design question** (Rama's call: should a
failed self-check take down a whole session, or degrade?) · any capital/signal-path or schema
change. **Nothing here touched capital, kill, order or signal runtime** — the probe only reads.

Related: [[s4-boot-outage-17jul]] [[p3s14-done-17jul]] [[feedback-verify-rc-not-output]]
[[feedback-verify-the-finding-premise]] [[killswitch-autoclear-prior-day]] [[pc-test-env-hygiene]]
