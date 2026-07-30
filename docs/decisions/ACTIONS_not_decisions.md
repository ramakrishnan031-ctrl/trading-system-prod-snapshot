# Operator actions (NOT decisions)

These are things to *do* when ready, not choices between options. Listed here so they do not clutter the decision files. No recommendation is implied by inclusion or order.

## Q10 Part B — the regime backfill
- **Action:** refresh the Kite token, then run the proven data-only backfill `scripts/fetch_daily_candles.py --backfill --from 2026-06-15 --to 2026-07-16` (backup first; verify 23 days ingested / 0 stock rows changed / integrity clean).
- **State:** blocked — the VM token is absent (`data_store/session/zerodha_token.json` deleted by the 05:00 cron; the 08:15 TOTP refresh does not fire on a non-trading day). Credentials/TOTP are Rama's; `auto_refresh_token.py` was deliberately not run.
- **What it yields:** the tercile calibration bands and it *starts the clock* — but Q10's verdict is already "NOT DETERMINABLE at n=23", so it does not deliver a determination (see [07_regime_enable.md](07_regime_enable.md)).
- **⚠️ CORRECTED 20-Jul-2026 — this action does nothing for regime (#07).** Runtime verification showed the regime engine fetches its ~201+ daily NIFTY bars **live from Kite** and issues **zero SQL** on the compute path — it never reads `analytics.candles`, which is what this backfill writes. It was also demonstrated that regime **computes fine today** (271 bars, `status: OK`) with no backfill at all. So the "blocked on the token → blocked on the backfill → blocked on regime" chain is broken at the second link. Keep this action only for whatever the tercile bands are worth **elsewhere**; do not carry it as a regime blocker. Evidence: `docs/audit/regime_computability_verification_20jul2026.md`.

## Standing security actions (Rama-owed)
From the AB-910 audit (`ab910-ops-security-audit-16jul`) and later:
- **Rotate the Telegram bot token** (`@Trade_sysbot` — compromised-at-rest; rotation proven clean).
- **Prod 2FA seed → VM-only** + re-enrol (currently hash-identical in the PC dev tree ⇒ both factors on one box).
- ~~**An off-disk / offsite backup target**~~ ✅ **DONE 27-Jul-2026 — first offsite backup ever taken.** Kept here struck through rather than deleted so the gap's history stays visible.
- **Disable rpcbind** (port 111 serves nothing).
- ~~**SSH → Tailscale-only**~~ ⛔ **REFUSED 27-Jul-2026, deliberately — do not re-propose without re-checking the premise.** Rama's phone has been **off the tailnet for 23+ days**, so closing `:22` would leave no working out-of-band path and **void the emergency runbook**. The hardening is only correct once a second device is reliably on the tailnet; until then it trades a real recovery path for a theoretical exposure.
- **`require_hmac`** — ~~a flag flip, de-risked on `/health` by P1 (`p1-health-require-hmac-17jul`); listed here because it is Rama-gated, but it is a one-line config flip, not an evidence-weighted decision.~~ **CORRECTED 20-Jul-2026 — NOT a safe flip.** Chartink cannot sign HMAC payloads, and `require_hmac=true` disables the `?token=` fallback (`signals/webhook_receiver.py:166-169,300-305,483-485`) → every Chartink POST 401s → **zero signals** (and a boot `ValueError` if `WEBHOOK_SECRET` is unset, `:146-151`). P1 pre-armed only the **receiver**'s `/health` verification; it did **not** give Chartink the ability to sign — different sides of the integration boundary. **Stays `false`** unless a signing proxy / different sender is added (a *design item*, not a flip). See `docs/audit/pre_receive_hook_investigation_20jul2026.md` §D.
- **Arm `pre-receive`?** — caveat recorded in `sweep-done-17jul`. **20-Jul-2026: investigated — DO NOT arm as-is.** Guard 2 (cron-integrity) would false-reject *every* push on a trailing-newline bug in the hook's `$(…)`+`printf '%s'` capture (`deploy/hooks/pre-receive:53-60`); the cron content is provably consistent (post-receive-style match). Needs a one-line fix + a `CRON_GUARD_DRYRUN=1` soak first. Guard 1 (market-hours) is sound. See `docs/audit/pre_receive_hook_investigation_20jul2026.md` §A–C.

## Deploy-tree integrity (registered 28-Jul-2026)

Three related items found while sweeping stray `.pyc` files. **None is a decision** — each is a
thing to do, with its own gate. Full reasoning: `../DEPLOY_CALENDAR_28-JUL_TO_04-AUG.txt` §7.3–7.4.

- ⏰ **PRESERVE THE DEPLOY REFLOG — this one has a DEADLINE.**
  `~/trading-system.git/logs/HEAD` on the VM holds **801 entries back to 3-May-2026**, recording
  both the `push` and the post-receive `checkout -f` for every deploy, timestamped to the second.
  **It is the only record of which commit was deployed when, and nobody knew it existed.**
  `gc.reflogExpire` is unset ⇒ git's default **90 days** ⇒ the 3-May entries are ~86 days old and
  **start expiring within days**; any `gc --auto` on a push can prune them.
  - **Action (two one-liners, no hook change, no code):**
    `git -C ~/trading-system.git config gc.reflogExpire never` and
    `… config gc.reflogExpireUnreachable never`
  - ✅ **DONE 28-Jul-2026 ~15:3x — APPLIED BEFORE ANYTHING WAS PUSHED.** Both keys read back
    `never`; **801 entries intact back to 3-May**. ⭐ The deadline moved from Thu 30-Jul to Tuesday
    the moment Rama's 15:45 "PC == VM 100%" made Tuesday the next push — **and the push itself was
    the thing that could have pruned it via `gc --auto`.**
  - ⛔ **Corrects an earlier claim of mine on 28-Jul** that "the bare repo has no reflog at all".
    That check ran `git reflog show main` — which is genuinely empty, because the repo is bare and
    no *per-ref* log was ever created — and wrongly generalised to "no reflog". The record is on
    **HEAD**. Git appends to a reflog file that already exists regardless of
    `core.logAllRefUpdates`; the setting only governs whether one is *created*.

- **Create the per-ref log going forward** (belt-and-braces, after the above):
  `git -C ~/trading-system.git config core.logAllRefUpdates true` — makes `git reflog show main`
  work from that moment. Adds nothing retroactively; stops the whole record depending on one file.

- **Build a deployed-tree-vs-HEAD integrity check.** ⛔ **NOT YET — its gate is after Tue 4-Aug's
  flag flip.** The invariant *"the deployed tree equals HEAD"* — the one the 28-Jul cherry-pick
  decision was explicitly made to protect — is **verified by nothing**. The deployed tree has no
  `.git`, and `checkout -f` never removes untracked files. ⭐ Cheaper than it first looked: the
  reflog above already supplies the *"which known commit"* half, so only the comparison remains.
  ⛔ Do **not** widen the new stray-`.pyc` detector toward this; different scope, different gate.

## Kill-path findings (registered 28-Jul-2026) — ⭐ these SURVIVE decision #11's closure

Decision [11](11_absolute_kill_ladder.md) is **CLOSED** (values unchanged). These three were
discovered *underneath* it and are **not part of that decision**. ⛔ **A closed decision must not
bury the findings made underneath it.** All three are **REGISTERED ONLY — nothing built, nothing
changed, nothing investigated further.** Earliest gate for any of them: **after Tue 4-Aug.**

### K1 — ⚠️⚠️ EMERGENCY KILL + SAME-DAY RESTART ⇒ THE SERVICE DOES NOT COME BACK
A kill whose reason is not in `SCHEDULED_KILL_REASONS` is an **EMERGENCY** kill. If the service
restarts **the same day** while it is persisted, startup hits `StartupScenario.HALT` and **exits 4**
— no boot without `--resume`. **In that window there is no exit management and no 15:15 / EOD
squareoff.** Bounded to the same day by `clear_stale_state()`, which is correct and is Rama's own
20-Jun headless guarantee (every *prior-day* kill clears regardless of type).

- **(i) Does it generalise? ⭐ YES — IT IS A CLASS, NOT A DRIFT ITEM.** Measured: **26 non-test
  `soft_kill`/`hard_kill` call sites**, and `SCHEDULED_KILL_REASONS` contains exactly **two**
  literals (`circuit_breaker_force_close_15:15`, `EOD_SQUAREOFF`). ⇒ **every other kill path is an
  emergency kill** — drift, token expiry, live-feed queue-full / reconnect-exhausted / consumer-dead,
  API-failure auto-trip, fund-manager invariant, order-placer, reconciler, cnc_gtt_monitor,
  System Manager EOD. Drift is merely where it was noticed. ⛔ Frame any future work as the CLASS;
  fixing the drift path alone would leave it open.
- **(ii) Would Rama know? ⭐ YES, and faster than expected — this is MONITORED, not silent.**
  · **systemd does NOT retry and does NOT loop:** `RestartPreventExitStatus=3 4` (verified in BOTH
    `deploy/systemd/trading-system.service:35` **and** on the VM — parity). The unit exits once and
    stays **`failed`** (journal: `Failed with result 'exit-code'` — measured 21-Jul-2026 11:37:53;
    the unit declares no `SuccessExitStatus`, so exit 4 is a failure exit). That is deliberate and
    correct. ⭐ **`inactive (dead)` is the HEALTHY nightly state after a clean exit 0 — `failed` is
    the HALT.** (Corrected 28-Jul: an earlier draft of this entry said `inactive`.)
  · **`liveness_probe` is installed and running:** cron `*/5 9-15 * * 1-5` (verified in the live
    crontab). During **[09:00, 16:00) on a trading day**, a not-active unit that the operator did
    not park raises **ONE CRITICAL** via Telegram with the CRITICAL-sentinel email fallback.
  ⇒ **Detection latency ≤ ~5 minutes across the whole trading day, including the 15:15 squareoff.**
  ⚠️ Gap for completeness: the probe window ends at 15:59, so a 16:00–17:35 outage is unwatched —
  outside market hours, and already a known separate item.
- **(iii) Recovery — possible, but NOT documented where an operator would look.**
  `deploy/resume.sh` does the right thing (stop the unit → `reset-failed` → `clear_kill_switch.py`).
  ⛔ **It is named in NO incident document.** And the two places that touch this are both wrong:
  · `docs/RUNBOOK.md:101` — *"Restart loop (exit code 4) | Stale SOFT_KILL in DB"*. **The symptom
    cannot occur** (`RestartPreventExitStatus=3 4` prevents the loop; the operator sees a service
    that is `failed` and stationary, so they would not match this row at all) **and the cause was fixed**
    (a *stale*/prior-day kill auto-clears since 20-Jun — the real cause today is a SAME-DAY kill).
  · `docs/05_incident_response.md:40` — *"Manual resume: restart the service"*. **Restarting is
    exactly what fails with exit 4.** The doc directs the operator to the action that does not work.
  ⇒ ✅ **DONE 28-Jul-2026 — PULLED FORWARD, not left for 4-Aug.** The deciding reason:
    **Wednesday is the first day this system holds a position overnight**, so the window in
    which the wrong doc could be read is now. Both files corrected (`1b03a64`, committed
    locally, unpushed — rides Thursday): `05_incident_response.md` now points at
    `sudo bash deploy/resume.sh` and states what it does; `RUNBOOK.md:101`'s row now names the
    real symptom (**`failed`, not a loop**) and the real cause (**a SAME-DAY emergency kill of
    ANY kind**). Both warn off `systemctl restart` **and** off `main.py --resume` (the latter
    competes with the service for the instance lock — the 18-Jun collision), and both state
    that detection is already covered by `liveness_probe`.
    ⭐ **Verified before writing, per "do not correct a wrong doc with a second unverified
    claim":** `deploy/resume.sh` does **three** things (stop + `reset-failed` → clear the kill
    switch → **start under systemd**), and it refuses to start if the clear fails.
  ⇒ 🔓 **WHAT REMAINS OPEN in K1** is the behaviour itself, not the documentation: an
    emergency kill plus a same-day restart still halts the service. Gate unchanged — after 4-Aug.

- ⚠️ **WEIGHTING, stated honestly.** The drift path has never fired in 22 trading days, so
  probability is low — but the consequence is the worst shape this system has: **service down,
  market hours, positions open, no squareoff.** ⭐ **Low probability × worst consequence is exactly
  what a register is for**; "never fired" must not argue it away. The liveness probe genuinely
  reduces this from *unmonitored* to *detected within ~5 minutes*, which is the difference between
  a bounded hazard and an open-ended one.

### K2 — the 06-Jul ₹10,000 drift events are UNRESOLVED
Two events, `source=order_reconciler`, tier=HARD, **delta ₹10,000** (4× the hard threshold, 200× the
₹50 reconciler tolerance), with **`expected=0.00`** — the *"~zero expected, possible publisher bug"*
shape the drift handler itself warns about. Logged at INFO and ignored by the ladder **by design**
(`order_reconciler` is not an escalating source).
**The narrow question: was that a real ₹10,000 book-vs-broker discrepancy, or a seeding/startup
artefact?** ⭐ **Both answers matter** — a real one means a non-escalating source hid something
serious; an artefact means a publisher emits garbage that a **future** escalating source could
inherit. ⛔ Marked **not-to-be-cited as evidence of real drift** until resolved. Gate: after 4-Aug.

### K3 — observability gap: drift below ₹250 is INVISIBLE
`TIER_NOISE` logs at **DEBUG** and production runs at INFO ⇒ **no realised-drift distribution is
obtainable from the logs at all** (min/median/p95 cannot be computed). `kill_switch_state` is a
single-row **current-state** table, not a history, so it cannot answer "has soft ever tripped"
either — the log grep is the only authority, and only ~22 trading days deep.
⛔ **Honest correction attached:** decision #11's original *"cheap and unblocked"* framing was mine
and it was **true of the TRIP question and false of the DISTRIBUTION question.**
⇒ ⭐ **THE CONSEQUENCE, which is the useful part: if the ladder ever DOES need re-tuning, it cannot
be re-tuned on evidence as things stand.** That is decision #11's reopen-trigger (c) pointing back
at this item. Closing it needs a code change (raise the noise tier, or persist drift samples) **plus
time** — a candidate for after 4-Aug, **not a commitment**.

## Test-coverage findings (registered 28-Jul-2026)

### T1 ✅ FIX-061 — RESOLVED 28-Jul. Coverage restored; **no production defect.**
The four `test_order_placer_fix061` tests died **inside production code** at
`order_placer.py:3513` (`TypeError: 'Mock' object is not subscriptable`) before reaching a single
assertion, and had covered **nothing** on the LTP-retry → emergency-exit → HARD_KILL path for 22+
days. **Root cause: a stale 15-Jun fixture, not a stale test** — H-3 (`a254a82`, 05-Jul) added a
store re-read whose row the fixture supplied as a bare `Mock`.
Repaired **test-side only**; **9 passed**. ⇒ *the production path was fine and had merely gone
unwatched.* ⭐ Not vacuous: returning an SL row instead of `None` makes three of them fail on
"hard_kill not called", so the assertions can still go red.

---

## ⭐⭐ THE 13-FAILURE SWEEP (28-Jul-2026) — "known PC-env failures" is a LABEL, not a diagnosis

**The headline, and it is stronger than expected: NOT ONE of the 13 is a genuine environment
limitation.** No network call, no missing binary, no Windows-path problem — **all 13 run in 2.54
seconds** and fail on mock wiring, a stale fixture, a self-inflicted orphan process, or a real
contract violation.

⭐⭐ **AND THE GENUINELY ENVIRONMENTAL CASES ARE THE *SKIPS*, NOT THE FAILURES.** The 4 skips each
name their environmental fact and skip cleanly: `test_zerodha_login` ×2 (`skipif` win),
`test_backup_retention` (symlinks not permitted), `test_gui_secret_key` ("Windows does not honour
POSIX file modes"). ⇒ **the "PC-env failures" label borrows the skips' legitimacy for a population
that has none.** That is precisely how `test_instance_lock` and then FIX-061 stayed unexamined.

| rank | test(s) | why it actually fails | verdict | production path left uncovered |
|---|---|---|---|---|
| **1** | `test_order_placer_fix061` **×4** | died at `order_placer.py:3513`, H-3 (05-Jul) vs 15-Jun fixture | **MISLABELLED** → ✅ **FIXED 28-Jul** | **LTP-retry → emergency-exit → HARD_KILL** — same machinery as K1 |
| **2** | `test_fix181::test_inflight_orphan_flattened_when_kill_active` | asserts `LIMIT`, production emits **`MARKET`** on the CHECK2 inflight-orphan flatten under HARD_KILL | **⚠️ UNKNOWN — needs root-cause** | **the HARD_KILL flatten's order type.** Either the test is stale after a deliberate move to marketable exits, or it is a real divergence. ⛔ Not guessed. |
| **3** | `test_main::TestContinueFromGate` ×3 | placer never invoked; `release` never called; `0 == 0+1` | **MISLABELLED** (mock harness) | entry gate → order placement (money path) |
| **4** | `test_closure_source_contract::test_no_module_restates_the_vocabulary_literals` | the scan found `scripts/backfill_closure_source_w8.py:92` restating `OWN_SL`/`OWN_TGT`/`OWN_EOD` | ⭐ **NOT A FAILURE — A REAL FINDING. The test is working.** | none — it is *reporting* a live defect. `core/closure_source.py:43-45` is the authority and that script does **not** import it. **Unfixed on every branch checked.** ⚠️ That script wrote 35 rows on 28-Jul. |
| **5** | `test_phase17_batch2::test_fix077_flask_max_content_length` | `int(Mock)` at `webhook_receiver.py:212` | **MISLABELLED** (mock harness) | the webhook max-content-length guard (FIX-077) |
| **6** | `test_instance_lock` ×2 | a stale 120 s orphan `Popen` holds the machine-global lock; **the holder PID differs every run** | **MISLABELLED** (diagnosed 27-Jul) — ⭐ fix already **queued on `fix-tests-27jul`** (Thursday) | the single-instance guard |
| **7** | `test_main::test_paper_mode_does_not_require_webhook_secret` | `WEBHOOK_SECRET` is present in the required-key list in paper mode | **⚠️ UNKNOWN** | paper-mode boot requirements |

**⇒ Standing at 28-Jul evening: 1 fixed · 2 UNKNOWN and owed a root-cause (ranks 2 and 7) · 1 real
defect to fix in the script, not the test (rank 4) · 5 mock-harness repairs (ranks 3, 5) · 2 already
queued (rank 6).** ⛔ None of the remaining work is done here; this section is the sweep.

### ⚠️ A3 — THE POPULATION WIDTH, because a count is only as wide as its search
- The "13" comes from **`pytest tests/unit tests/integration`** — 308 + 14 = **322 test files**.
- ⛔ **`tests/crash_test/` (8 test files) and `tests/core/` (1) are NOT in that command.** **9 test
  files never run in the regression gate at all.** ⭐ *A skipped test and a failing test hide the
  same thing — but an **uncollected** test hides more, because it appears in no count whatsoever.*
  Widening the gate is its own item; ⛔ not done tonight (it would change the baseline mid-sequence).
- 4 skipped (all genuine ENV, above) · 3 `skipif` decorators · 7 runtime `pytest.skip()` calls,
  most of them inside the uncollected `tests/crash_test/`. **0 collection errors.**

### T2 — ⭐⭐ **9 TEST FILES ARE NEVER COLLECTED BY THE REGRESSION GATE AT ALL**
`tests/crash_test/` (**8 files**) and `tests/core/` (**1**) are not in `pytest tests/unit
tests/integration` — the command every deploy is verified against, **including today's**.
⭐ *A skipped test and a failing test hide the same thing; an **uncollected** test hides more,
because it appears in **no count whatsoever** — not as a failure, not as a skip.*
⇒ **THE HONEST CONSEQUENCE: the regression gate's own coverage has never been measured.** Every
"13 failed / 5,362 passed" figure this project has ever quoted describes 322 files, and nobody
established that those are all of them.
⛔ **DO NOT fix it by adding the directories tonight.** Running 9 never-run test files for the
first time, hours before the T2 arm, is the wrong night for whatever they turn out to say — and it
would move the regression baseline mid-sequence. **Gate: after Tue 4-Aug**, and expect the first
run to be noisy.

### T3 — ⚠️ `test_fix181` asserts `LIMIT`; production emits `MARKET` under HARD_KILL — **UNKNOWN**
`test_inflight_orphan_flattened_when_kill_active` fails on `assert 'MARKET' == 'LIMIT'` for the
CHECK2 inflight-orphan flatten. **Either the test is stale after a deliberate move to marketable
exits, or it is a real divergence on the kill path.** ⛔ **Not guessed, and not resolved tonight.**
⭐ It is the **same machinery as K1 and FIX-061** — the emergency-exit / HARD_KILL path — which is
the third finding in a day pointing at that area. **Gate: after Tue 4-Aug.**
*(A `MARKET` exit under an emergency flatten is plausible **by design** — `kill_switch` has
`_marketable_exit_params` — which is exactly why this needs reading, not assuming.)*

### T4 — `backfill_closure_source_w8.py:92` restates the closure-source vocabulary
`core/closure_source.py:43-45` is the authority (`OWN_SL` / `OWN_TGT` / `OWN_EOD`, `Final`). The
script does **not import it** — it restates the literals in a local `_LEG_TO_SOURCE` map. The
contract test `test_no_module_restates_the_vocabulary_literals` **is working**: it reports this.
**Unfixed on every branch checked.**

⭐⭐ **DID THIS AFFECT THE 35 ROWS THAT SCRIPT WROTE ON 28-Jul? NO — and the reason matters, so it
is stated rather than assumed.** Both sides were read and compared today:
`authority {OWN_SL:"OWN_SL", OWN_TGT:"OWN_TGT", OWN_EOD:"OWN_EOD"}` vs
`restated {"SL":"OWN_SL","TGT":"OWN_TGT","EOD":"OWN_EOD"}` ⇒ **the values are IDENTICAL today.**
⛔ **THAT — not the row verification — is what makes the 35 rows safe.** They are two different
reassurances and must not be blurred:
1. **The values currently match.** This is what guarantees the written rows are correct. It is a
   property of *today's state*, **not** a guarantee — nothing enforces it but the failing test.
2. **The 35 rows were independently verified** the same morning against **8 pre-registered
   predictions, 8/8 met**. That confirms the *outcome* — it would **not** have caught a literal
   drift that happened to be self-consistent.
⇒ **The duplication is harmless TODAY and unsafe GOING FORWARD:** the moment `core/closure_source.py`
changes, the script silently writes stale values, and this test is the only thing that would notice.
**Fix = import from the authority** (a one-line change in a non-trading script). **Gate: after
4-Aug**, with the rest of the sweep.

## Schema-migration window (registered 28-Jul-2026) — ⛔ register only, gate after 4-Aug

### M1 — ⭐⭐ THE EVENING SCHEMA PUSH OPENS A REFUSAL WINDOW. **IT IS A PROPERTY, NOT A ONE-OFF.**
**Measured window (boundaries from the deploy reflog, not from memory): OPEN `d3fa5b8` push +
`checkout -f` at *27-Jul 18:17:37*, CLOSE `migration complete: v44 -> v45` at *28-Jul 08:15:16.875*.**
Inside it, every **non-boot** process that opens `state_store` raises `MigrationNotPermitted`
(`state_store.py:355`) and **aborts whole**. ⇒ ***v46 will do exactly this again.***

**CASUALTY LIST, with the distinction that matters — REGENERABLE or GONE:**

| job | in-window | outcome | REGENERABLE? |
|---|---|---|---|
| `forward_shadow_record` 18:15 | **NO — ran 18:16, ~71 s BEFORE the 18:17:37 deploy** | ✅ **wrote normally: 3,695 records for 27-Jul, the HIGHEST of any day** | n/a — **nothing lost.** ⭐ Safe **by luck, not design**: a 3-minute-earlier push would have destroyed the only out-of-sample evidence producer. That margin is the real finding here. |
| `system_manager` 18:45 | yes | **ABORTED** (`MigrationNotPermitted` traceback in `logs/system-manager.log`) | ✅ **REGENERABLE** — `--date 2026-07-27`, and safely: `--dry-run` / `--no-soft-kill` exist. ⛔ not regenerated tonight |
| `cron_officer --eod-summary` 18:50 | yes | **ABORTED** (same traceback, `logs/cron-officer.log`) | ✅ regenerable (derived from the DB) |
| `backup_retention` 02:00 | yes | refused | ✅ harmless — it PRUNES old backups; a deletion that did not happen. ⭐ **The BACKUPS THEMSELVES were taken** (`analytics-2026-07-28.db` @ 01:05) because those cron jobs use **raw `sqlite3`, not `state_store`**, so they never hit the guard |
| `db_retention` 02:30 | yes | refused | ✅ harmless — same shape, a prune that did not run |
| `auto_refresh_token` 08:15:03 | yes | logged `MIGRATION_REFUSED` | ✅ **THE TOKEN STILL REFRESHED** — `data_store/session/zerodha_token.json` exists, dated **28-Jul 08:15**. The refusal hit the heartbeat write, **not** the token work. ⭐ This is the one that could have cost the whole trading day, and it did not |

⇒ ***NOTHING IS OWED FROM THAT WINDOW. It is CLOSED.*** Two derived reports are re-runnable and
nothing append-only was lost.

**TWO DIRECTIONS, deliberately not chosen here:** (a) push migration code **immediately before an
off-market boot** rather than the evening before, shrinking the window to ~minutes; (b) let non-boot
processes **DEGRADE** — skip the schema-dependent work, keep the rest — instead of aborting whole.
⛔ Not decided, not built. **Gate: after 4-Aug.**

⚠️ **ADJACENCY, CHECKED RATHER THAN ASSUMED: the S4 change queued for Thursday does NOT cover this
path.** S4 turns a *webhook self-check* failure at **boot** from a halt into a degrade-plus-alarm.
This is a **non-boot cron process** hitting a **schema** guard. Same *shape* (halt → degrade), but a
different trigger, a different code path and a different process class. ⛔ **Do not assume Thursday
fixes it.**

### M2 — ⚠️ THE DAY'S ERROR CENSUS CANNOT SEE THIS CLASS OF CRITICAL
**Measured:** `grep -icE "refus|migrat|schema" logs/system_2026-07-27.log` = **1**, and the three
28-Jul matches are all unrelated (two INFO migration-success lines and a `delivery_lock` WARNING).
The refusals live in **`logs/cron-officer.log`** and **`logs/system-manager.log`** — per-cron files.
⇒ ***The census (`grep -cE '"level":"(ERROR|CRITICAL)"' logs/system_<date>.log`) is NOT a complete
count of CRITICALs. A whole class reaches Rama's email and bypasses it entirely.***

**Consequence for tonight, in one line:** it does **not** confound the 17:40 gate — these cannot
land in that log — **but "no new CRITICAL in the census" must not be read as "no new CRITICAL".**

⚠️ **AND THE AUDIT TRAIL QUEUED FOR THURSDAY (`214a878`) WOULD NOT CLOSE THIS.** It records the
**send outcome** in whatever log the *sending process* writes to — for a cron process that is
`logs/cron-*.log`, **not** `logs/system_<date>.log`. So it improves auditability of the alert stream
(its actual purpose) but **does not put this class into the census.** ⛔ Stated so nobody assumes it
does. **Gate: after 4-Aug.**

## Read-only traces completed 30-Jul-2026 night — F1 and Q9. ⛔ Nothing built.

### F1 — `reconcile_positions` vs a delivery book. **REAL, but LATENT — it has never been reachable.**

**Mechanism, read from source.** `scripts/reconcile_positions.py`:
- **Broker side** `:158-168` — `kite.positions()`, takes `["net"]`, keeps `qty != 0`.
  ⛔ **`holdings()` is NEVER called.** Confirmed by grep across the file.
- **System side** `:183-187` — `SELECT symbol, qty_filled, direction … WHERE status IN
  ('OPEN','PARTIAL') AND qty_filled > 0` from the **live** DB.
- **Comparison** `:255-274` — over `set(broker) | set(system)`:
  `broker≠0 & system==0 → ORPHAN_AT_BROKER` · `broker==0 & system≠0 → MISSING_AT_BROKER` ·
  `broker≠system → QTY_MISMATCH` · else `OK`. **`OK` rows ARE written**, so *zero rows*
  means *zero symbols on either side*, not "no discrepancies".

**⭐ THE TWO CASES ARE OPPOSITE AND MUST NOT BE BLURRED:**

| | system side | broker side | result |
|---|---|---|---|
| **TEST-ARTEFACT (T2)** — isolated DB, **no live trade row** | 0 | position present on any day the basket trades | **ORPHAN_AT_BROKER** |
| **REAL DELIVERY (future)** — a delivery trade OPEN in the **live** DB, held past T+1 | ≠0 | 0 (it left `positions()` for `holdings()`) | **MISSING_AT_BROKER** ← *this* is F1 |

**MEASURED, from `position_reconciliation` — the table's entire history is one date:**
- **29-Jul (buy day):** 5 rows, `ORPHAN_AT_BROKER`, `broker_qty=3`, `system_qty=0`.
- **30-Jul (T+1, nothing traded):** **zero rows**, job SUCCESS — the stock had left
  `positions()` for `holdings()`, so it was invisible on **both** sides.

**⭐⭐ AND THE CONCLUSION THAT SIZES IT: F1's misfire has NEVER FIRED, and could not
have.** It requires a delivery trade `OPEN` in the **live** `trades` table. MEASURED: the
only CNC orders ever written to the live DB are **3** — AVL **FAILED**, SETL **CANCELLED**,
HARIOMPIPE **CANCELLED**. **None ever reached `OPEN/PARTIAL` with `qty_filled > 0`**, which
is exactly what the job selects on. ⇒ **F1 is LATENT; its gate is the first live delivery
trade, i.e. the 4-Aug flip + carry pilot.** ⛔ Do not report it as a live daily false alarm.

**⇒ IT FOLDS INTO RECONCILIATION STEP 1 — NOT A SEPARATE WORKSTREAM.** D-8 step 1
(`D-4(a)`: scope `reconcile_positions` to intraday) **is** the fix. Per the ordering
constraint, step 1 is a **RESTRICTION**, so it is unaffected by the buy-day-filter
sequencing and its "ship before or with the flip" slot stands.

**⚠️ FRIDAY 31-JUL, PREDICTED:** the closes are a **SELL**, and a sell is a fresh day
position ⇒ the five should reappear in `positions()` at **qty −3** with no system row ⇒
**ORPHAN_AT_BROKER ×5 and a CRITICAL ~15:46 — which would be evidence the sells HAPPENED.**
⚠️ Buy-day direction MEASURED; sell-day direction **inferred by symmetry, not measured**.
⛔ **Either way the 15:45 job is NOT the witness** — the five `EXIT=0` codes and the Kite
holdings/GTT check are. Briefing line added to the Friday card (it previously predicted
SUCCESS; corrected 30-Jul).

### Q9 — does `conditional_allocation_enabled=TRUE` make the BL9 HARD_KILL sites more reachable? **NO — UNCHANGED.**

**The two sites, read from source:**
- **`fund_manager.py:982` (BL-4)** — fires when `commit_to_used` **raises**; reason
  `commit_to_used failed for reservation_id=…`. An **exception** path (ledger/apply
  failure), not an arithmetic threshold.
- **`fund_manager.py:2329` (BL-9)** — `_handle_invariant_violation`, fired by
  `_check_invariant` (`:2228`) raising `CapitalInvariantViolation`.

**⭐ THE BL-9 PREDICATE IS A NON-NEGATIVITY GUARD, NOT A SPLIT CHECK.** It calls
`assert_capital_invariant` **only when a partition is already negative**:
`if (self._intraday_avail < -TOL or self._intraday_used < -TOL or
self._intraday_reserved < -TOL)` (and the positional mirror). The code's own comment is
explicit: *"a legitimate PnL-shifted per-bucket split (avail+reserved+used != total\*pct)
is never reached, so this cannot false-fire."*

**⭐ THE FLAG NEVER REACHES FundManager.** `grep conditional_allocation_enabled
capital/fund_manager.py` = **0**. Its single production consumer is `main.py:2318`, which
resolves it through `resolve_bucket_allocation()` into two floats — and states
*"FundManager is UNCHANGED — it only receives the final pcts."*

**⭐ AND AN OVER-RESERVATION CANNOT DRIVE A BUCKET NEGATIVE.**
`resolve_bucket_allocation`'s docstring (`fund_manager.py:111-136`): *"delivery 0% => every
delivery reserve() rejects 'Insufficient positional capital' and never borrows intraday
(**the no-borrow guarantee is already in reserve(): it consults ONLY the intent's
bucket**)."* `reserve()` *"does NOT raise on insufficient capital — returns failure
gracefully"* (`:494`, `:761`).

⇒ The flip changes only the **VALUE** of `intraday_pct`/`positional_pct` (used at `:441`,
`:1395` to seed bucket balances, and as `cash_floor` at `:2271`/`:2285` — which is only
evaluated *after* a partition is already negative). It changes **neither the predicate,
nor the frequency of evaluation, nor the reachability**.
⭐ And in the **BOTH-active** case the resolver returns **the config split unchanged**
(`elif delivery_active and intraday_active: delivery = positional_pct`) — i.e. **value-neutral**.

**⇒ VERDICT: UNCHANGED. The §6(i) `[INFERENCE]` — "4-Aug is the worst day for the most
reachable trigger" — is NOT SUPPORTED and should be struck.**
⭐ **Effect on the plan, in one sentence: it does NOT weaken Q7 — the buy-day filter still
ships before 4-Aug — but its urgency rests on the CARRY PILOT ALONE, not on any interaction
with the flag flip.**
⚠️ **Sized against the base rate:** HARD_KILL has **never fired** in production (measured).
This trace **removes** a stated reason for alarm rather than adding one.
⚠️ Non-kill consequence worth naming separately: in the **only-delivery** configuration the
resolver returns **0/100**, so the intraday bucket is seeded at **zero** and every intraday
`reserve()` would reject — correct and intended (that is R10's entire purpose), but it is a
real behavioural change and not a kill.

## Note
These actions gate several of the decisions (Q10 gates D2/D3/Regime evidence; the security items are independent). They are tracked in `MEMORY.md` under RAMA-ACTIONS and are restated here only so the decision index is complete.
