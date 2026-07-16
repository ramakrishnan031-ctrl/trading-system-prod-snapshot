# SWEEP — batch-2 deploy + purge, then the buildable ops/security LOOP items

**Date (IST):** 2026-07-17, 01:10–05:00, off-market gating **waived by Rama**.
**Batch-2 deployed:** `0a9a6a8` (PC == VM). **Sweep base:** `0a9a6a8`.
**Credential discipline:** no secret value appears here. Tokens are identified by SHA-256
prefix; log hits counted, never printed.

---

## 0. Outcome

Batch-2 is **deployed and the purge is verified clean (0 live-token hits)**. Of the seven
sweep items, **four shipped** and **three escalated to the careful loop** — every one of
them because a gate the instruction itself specified *fired*, and in two cases because the
"obvious" fix was demonstrably **worse than the status quo**. The trading/capital path was
not touched.

| Item | Verdict | SHA |
|---|---|---|
| **S2** fail-open 2FA → fail-closed (§1.3 HIGH) | ✅ shipped | `3ad047e` |
| **S7** post-receive header + md5 re-arm (§4.4) | ✅ shipped | `5f89ec5` |
| **S6** FIX-065 market-hours guard — **in pre-receive** (§4.2) | ✅ shipped (unarmed) | `e4c6d3e` |
| **S4** `/health` auth + rate limiter (§1.7 MED) | ✅ shipped | `84cee3e` |
| **S1** `market_day_only` central enforcement (§2.2 HIGH) | ⛔ **ESCALATED** | — |
| **S3** lockout DoS (§1.6 MED) | ⛔ **ESCALATED** | — |
| **S5** functional-criterion tail (§2.5 MED) | ⛔ **ESCALATED** | — |

---

## 1. PHASE 1 — batch-2 deploy + the purge ✅

- Fresh VM backup taken pre-deploy (`predeploy-batch2-*`, `integrity_check: ok` on both DBs).
- `git push origin main` + tag → **bare HEAD `0a9a6a8` == PC HEAD**. Tag pushed.
- Verified on the VM: logger fix present in the checked-out tree · stale dup absent ·
  **schema v44** · `integrity_check ok` · `foreign_key_check` empty · **live crontab ==
  canonical** · services as expected.
- The push printed **`post-receive: crontab AUTO-INSTALLED from canonical`** — runtime proof
  for §4.4 (only the armed Option-A hook prints that), which S7 then fixed.

### The purge — verified clean
Purged with `shred -u` (bytes overwritten, not merely unlinked) while the app was down, so
nothing was mid-write:

| | |
|---|---|
| Files purged | **19** debug logs + `logs/cron-candle-fetch.log` (§1.9 residue) |
| ↳ live token | 10 files, 984 lines (03-Jul → 16-Jul) |
| ↳ **revoked** token | 9 files, 678 lines (15-Jun → 02-Jul) — the audit never counted these |
| **LIVE token hits in `logs/` after** | **0** |
| **Any token-shaped path in `logs/` after** | **0** |
| **Live token anywhere outside `.env`** | **0** |
| `logs/` | 140 files / 1.1 GB → **120 files / 642 MB** |

**Timing worked out:** the purge ran at ~02:00, before the 08:15 boot, so `debug_2026-07-17.log`
never existed and never received the token.

---

## 2. What shipped

### S2 — 2FA now fails CLOSED · `3ad047e`
`verify_totp()` returned `True` on an empty secret: losing the secret silently downgraded
the dashboard to one factor, and the weaker state was the **default**. Now an empty secret
refuses unless `auth.totp_disabled: true` is set explicitly — the deliberate escape hatch,
which does not override a real secret.

**Verified it cannot lock Rama out** (the thing that mattered): `load_gui_config()` merges
`gui_config.local.yaml` over the base; parsed with YAML on the VM (shape only, values never
printed) the effective `totp_secret` is a real 32-char base32 → login path unchanged.
*An earlier `sed` reading of the same file reported `len=34` — it had captured the trailing
comment. Re-measured with a parser before relying on it.*

This also sharpens the audit's "LATENT, not live" call: the fail-open path is reachable
**exactly when the local overlay goes missing** (restore-from-backup, bad chmod, fresh VM) —
the moment you'd least want a silent downgrade. Old contract
(`verify_totp("", "anything") is True`) was inverted deliberately; tests rewritten.
**Dashboard suite 357/357 green in its own venv.**

### S7 — the post-receive header lied · `5f89ec5`
It claimed "*NOT the currently-installed hook*". False, proven twice: md5 identity, **and**
the live push printing `crontab AUTO-INSTALLED from canonical`. Comment-only (non-comment
lines byte-identical). The catch, handled rather than left behind: byte-identity **was** the
proof of which hook is real, so the edit moves md5 `bd950b7…` → `e493dc5…`; the VM hook is
re-armed to match and SYSTEM_MAP's checksum updated in the same commit. Audit reports citing
the old md5 were deliberately left alone — rewriting a point-in-time record is how records
stop being evidence.

### S6 — FIX-065 implemented **in pre-receive**, not post-receive · `e4c6d3e`
**Deliberate deviation from the instruction, for a hard technical reason:** git ignores
post-receive's exit status (refs are already updated), so a guard there **cannot reject a
push**. It could only skip the checkout while the bare ref moved — leaving **bare ≠ tree, a
half-deploy**, silently breaking the "bare HEAD == deployed" invariant every verification in
this repo relies on. That is worse than no guard. `pre-receive` is the only hook that can
say no. (The original FIX-065 was doubly broken: wrong hook *and* never armed.)

The decision lives in `deploy/hooks/market_hours_guard.sh` as a pure function of
`(HHMM, DOW, COMMIT_MSG)`, so the clock is **injected, not mocked**. Fails **open** on an
unparseable clock or a missing file — a deploy guard is a convenience, not a security
control, and must never wedge deploys on its own error. `10#` forces base-10 because `0915`
parses as **octal** in bash and would crash the guard at exactly the minute it matters most;
there is a test for that.

**The 12 vacuous tests are gone.** 21 tests now run the shipped script. Proof they are not
decoration: **move the guard away → 20 of 21 turn red**; restore → 21 pass. They also no
longer skip blanket-on-Windows.

> **⚠️ NOT ARMED — Rama's call.** `pre-receive` is not installed, so this ships correct but
> dormant. Arming is **not free**: the same file carries the cron-integrity guard, which has
> never run live, whose own header prescribes a DRY-RUN rollout, and which defaults to
> **enforcing** (`CRON_GUARD_DRYRUN:-0`). Arming turns on two controls, one untested, and a
> buggy `pre-receive` blocks **every** deploy including an emergency fix.
> Arm: `cp deploy/hooks/pre-receive ~/trading-system.git/hooks/ && chmod +x …`
> Break-glass: `rm ~/trading-system.git/hooks/pre-receive`

### S4 — `/health` authenticated + rate-limited · `84cee3e`
`GET /health` on `0.0.0.0:5000` handed any anonymous caller `kill_switch_active` + queue
depth — a free oracle for "is the trading system halted right now, and how loaded is it" —
and bypassed the per-IP limiter `/webhook` sits behind. Now: same limiter (applied **before**
auth, so a flood stays cheap), same secret; the 401 body deliberately discloses nothing.

**Scope kept deliberately narrow** — only the `/health` route function. `_handle_webhook` and
`_process_request` are untouched: this file is the signal entry path and a health endpoint is
not worth risking it. No secret configured → old behaviour (symmetry with `/webhook`).
4 of 6 new tests are **red-proven** on the pre-change tree. Checked nothing breaks: UptimeRobot
monitors a **different** `/health` (`scripts/healthcheck_server.py` on **:8080**); nothing in
production polls `:5000/health`.

*Our own pre-commit secret scanner **blocked** the first attempt — the test's fake token was
credential-shaped. The guard was right; the value is now a recognised placeholder. Worth
recording that the control fired on a real commit, not just in its own tests.*

---

## 3. The three escalations — each gate fired for a reason

### S1 — `market_day_only` central enforcement → **careful loop**
The instruction's own condition was *"confirm none of the 20 is a trading decision."* **It fails.**

- **`auto_refresh_token` is `critical: true` and gates whether the system starts at all.**
  Verified from code, not memory: `token-watcher.service` is *"auto-start on fresh token"* and
  `deploy/token_watcher.sh` is the headless starter. Chain: 05:00 delete → **08:15 token
  refresh** → token-watcher sees a fresh token → starts `trading-system`. A central guard keyed
  on the holiday calendar means **one wrong calendar entry = no token = the system does not
  trade that day.** The calendar becomes a single point of failure for the boot chain.
- `eod_cleanup` mutates order/trade status (`CANCELLED`) → order lifecycle.
- `eod_verify` / `eod_broker_reconcile` / `reconcile_positions` read capital/reconciliation state.
- **AB-910 itself tagged §2.2 `LOOP` — "with the central mechanism designed first."**

Not a partial fix either: per-script guards would add the "20 more copies of the same five
lines" the audit explicitly warned against, while leaving the risky jobs unguarded.

### S3 — lockout DoS → **careful loop** (the obvious fix is *harmful* here)
`record_failure(username)` is keyed on the **submitted** username, so anyone can lock Rama out
by guessing "rama" five times. The audit's and instruction's primary fix is per-IP keying.
**That would be strictly worse here, and only checking the deployment reveals it:**

`tailscaled` listens on `100.74.84.44:443` and proxies to Waitress on **`127.0.0.1:8500`**
(`tailscale serve status` confirms; Waitress binds loopback only). So `request.remote_addr`
is **`127.0.0.1` for every request** → per-IP keying collapses to **one bucket** → an attacker's
five failures lock out **everyone, including Rama** — the exact harm §1.6 describes, amplified.

Making it correct needs `X-Forwarded-For` + ProxyFix, whose presence I could not verify without
touching the production `tailscale serve` config or adding an echo endpoint — neither acceptable
mid-sweep. The alternative ("throttle, never lock") is a genuine **security trade-off**: bypassing
the lock for correct credentials gives an attacker unlimited guesses, because you must evaluate
every attempt to know it was correct. **`gui-dashboard.service` is live right now** — a wrong
change locks Rama out of the tool he watches the system with, during market hours.
**AB-910 tagged §1.6 `LOOP / Rama` (auth control).**

**For the loop:** confirm whether Tailscale Serve populates `X-Forwarded-For`; if yes, key on it
(safe to trust — Waitress is loopback-only, so tailscaled is the only ingress). Otherwise decide
throttle-vs-lock explicitly.

### S5 — functional-criterion tail → **careful loop** (its premise doesn't survive contact)
The F2 table implies ~20 jobs are one `timer.functional_status = …` line away. They are not:

1. **The blocking design question, found here:** the Officer flags **any** functional status
   outside `{OK, SUCCESS, DELIVERED}` as an issue (`cron_officer.py:189`). So marking
   `gemini_trade_coach` `SKIPPED` on a legitimate **no-trade day** raises a *functional issue
   every quiet day* — manufacturing precisely the alert noise the Foundation Rule exists to
   prevent, training the operator to ignore the channel. Whether "nothing to do" is `OK` or an
   issue is a **judgment about alert semantics**, not a mechanical mapping. *Batch-1's
   `daily_trade_review` escapes this only because the Officer self-guards and doesn't run on
   holidays — that safety does not transfer to a quiet **trading** day.*
2. Most F2-listed jobs (`control_tower`, `daily_report`, …) have **no `HeartbeatTimer`** → not
   one-liners.
3. Of the 10 scripts that do, 4 already set `functional_status`; **3 defer under S5's own gate**
   (`eod_cleanup` order lifecycle · `fetch_fno_ban` gates what may be traded ·
   `reconcile_positions` capital).
4. The rest (`trade_journal`, `compute_strategy_metrics`) must distinguish "legitimately empty"
   from "broken" → reads trade state; `fetch_daily_candles` needs the market-day notion → **S1**.
5. `refresh_instruments` already asserts its own artifact inside `main()` (`_check_fresh_write`,
   surfaced via rc) — it is not "execution-only" in the way F2 assumed.

**Corrections to the record made while checking (both were my own premature conclusions,
disproven before they reached this report):** the 4 Gemini jobs *do* record heartbeats — via
`record_heartbeat()` directly, not `HeartbeatTimer` — so they are **not** unmonitored; and their
unconditional `record_heartbeat()` is **not** an execution-status bug for `gemini_premarket_brief`
(AST shows `run_briefing` returns 0 or raises). `run_coaching`/`run_check` *can* return non-zero,
but those codes are **documented legitimate states** ("no trades to coach", "no traded symbols",
`DIVERGENCE_DETECTED` — which already sends its own alert), not silent failures.

**For the loop, in order:** settle the Officer's benign-set semantics **first**, then apply
criteria to the ~16 remaining jobs.

---

## 4. Regression

Full suite (`pytest tests/`) plus the dashboard suite **in its own venv** (the supported way —
`tests/` does not include `ops_dashboard/tests`).

| Suite | Result |
|---|---|
| `tests/` (full, 13m20s) | **4814 passed · 11 failed · 4 skipped** |
| `ops_dashboard/tests` (in `ops_dashboard/.venv`) | **357 passed · 0 failed** |

**The 11 failures are the known PC-env set — ZERO attributable.** They are the *identical*
node IDs seen in the batch-2 baseline, which were re-run against the **TRUE pre-change tree**
(`git checkout 57f28a1 -- …` + grep-confirm absent — **never `git stash`**, rc checked) and
failed identically there. Memory records this set as time-gated (10 in-window / **11 outside**);
both runs were outside the window → 11 expected, 11 observed, same IDs.

**The deltas reconcile exactly, which is the real proof nothing silently changed:**

| | batch-2 run | sweep run | why |
|---|---|---|---|
| passed | 4787 | **4814** | +21 `test_fix065` · +6 `/health` |
| skipped | 16 | **4** | −12: the fix065 tests no longer skip blanket-on-Windows |
| failed | 11 | **11** | same IDs |

`4787 + 21 + 6 = 4814` ✓ and `16 − 12 = 4` ✓ — every unit of movement is accounted for by a
test this sweep added or un-skipped. Nothing appeared or vanished unexplained.

**Dashboard note:** run with the *system* python, `test_c_venv_has_no_kiteconnect` fails — an
interpreter artifact, not a defect. It asserts the GUI venv carries no broker packages, and its
own message says "Run tests inside ops_dashboard/.venv". Run the supported way → **357/357**.
`pytest tests/` does **not** cover `ops_dashboard/tests`; both were run.

---

## 5. Still owed — nothing lost

### 5.1 CAREFUL LOOP (not swept — capital/signal path or a real design decision)
| Item | Why it is here |
|---|---|
| **E4 — CHECK1/RMS `costs=0.0` ⇒ daily-loss limit fed GROSS, not net** | capital (excluded by Rama) |
| **P3-s14 — webhook dedup-claim rollback** | entry-path control flow (excluded) |
| **B2/M-S4 — `secondary_screener.py:407-410` `atr/rsi/prev_close: None`** ⇒ 25/100 of the live selection score is constant 0.0 | signal path (excluded) |
| **S1 — `market_day_only` central enforcement** | gates the boot chain via `auto_refresh_token` |
| **S3 — lockout DoS** | per-IP is *harmful* behind the Tailscale proxy; needs XFF or a throttle decision |
| **S5 — functional-criterion tail** | settle the Officer's benign-set semantics first |
| §2.5 monitoring tail, §1.6/§1.7 residue | as above |

### 5.2 RAMA-ACTIONS owed
1. **🔴 ROTATE the Telegram token** — `@Trade_sysbot`, id `8648177777`, **compromised-at-rest**.
   The logs are purged, but purging is not un-disclosing. **Rotation is proven clean here:** the
   02-Jul rotation left the old token verifiably **401-revoked**.
2. **Move the prod 2FA seed VM-only + re-enrol** (§1.2 — both factors currently on one box).
3. **Provision an off-disk/offsite backup target** (§2.1 — backups + live DB on one disk).
4. **Disable `rpcbind`** (§1.4). 5. **SSH → Tailscale-only** (§1.5, C1 re-baseline first).
6. **`require_hmac` decision** (§1.8 — accepted risk, unchanged).
7. **Arm `pre-receive`?** (S6 — read the caveat in §2 first.)
8. **D1–D4** still pending on the decision sheet.

### 5.3 ⏰ Operational note Rama should see
The **16-Jul SOFT_KILL is a PRIOR-DAY kill** (`triggered_at 2026-07-16T10:54:28`, reason
"planned pause for pending fix/review work"). Per `deploy/token_watcher.sh` and
[[killswitch-autoclear-prior-day]], a prior-day kill **auto-clears at the next boot** — so the
system **resumes trading at 08:15 today** with the swept code, unless re-paused. The book is
**flat** (7 CLOSED + 3 CLOSED_MANUAL since 15-Jul; zero OPEN). Not an incident — the `failed`
unit state is just exit code 4 from the intended HALT.

---

## 6. Method note

No secret value was printed at any point. The VM purge used `shred -u`. The one outbound call
made in the whole run was a read-only `getMe` to Telegram — the issuer of the credential in
question, hence not a disclosure — to settle whether the second token was live. It is not.
Scripts were transferred to the VM base64-encoded (the `\\`-halving rule) and removed after use.
