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

## Note
These actions gate several of the decisions (Q10 gates D2/D3/Regime evidence; the security items are independent). They are tracked in `MEMORY.md` under RAMA-ACTIONS and are restated here only so the decision index is complete.
