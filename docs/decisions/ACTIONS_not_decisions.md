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
  - ⭐ **Do it before the next push** (i.e. before Thu 30-Jul) or ~3 months of deployment history is
    lost to a default nobody chose.
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

## Note
These actions gate several of the decisions (Q10 gates D2/D3/Regime evidence; the security items are independent). They are tracked in `MEMORY.md` under RAMA-ACTIONS and are restated here only so the decision index is complete.
