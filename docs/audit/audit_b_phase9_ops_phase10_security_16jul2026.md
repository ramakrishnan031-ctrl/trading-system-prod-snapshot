# Audit-B — Phase 9 (Operations) + Phase 10 (Security)

**Date:** 16/17-Jul-2026 (IST) · **READ-ONLY.** Nothing changed, nothing fixed, nothing
pushed. **AB-910 — the two Audit-B phases that were never produced** (`audit_05jul2026.md`
ends at "Batch 5 running…").

**Baseline:** PC `main` == VM bare == `6969599` (M-C cluster + batch-1 deployed).
Schema v44. Method: code/config read at HEAD, `sqlite3 -readonly`, live VM inspection.
Severity scale per Audit-B: **CRITICAL / HIGH / MED / LOW**. Every finding carries
evidence. **No secret value appears in this file** — credentials were compared by hash
and classified by shape only.

---

## 0. Top 5 across both phases

| # | Sev | Finding | New? |
|---|---|---|---|
| 1 | **HIGH** | **The live Telegram bot token is written to logs in cleartext — 984 lines, 10 files, ongoing today.** `urllib3` DEBUG logs the request line; Telegram puts the credential in the URL path. | **NEW** |
| 2 | **HIGH** | **The live PRODUCTION 2FA seed sits in the PC dev tree** — PC seed hash == VM seed hash. Both factors on one box ⇒ 2FA adds nothing against a PC compromise. | confirmed |
| 3 | **HIGH** | **Backups have no independent failure domain** — live DB + all 33 backups (5.5 GB) on `/dev/sda1`, no other mounts, nothing offsite. | confirmed (S2) |
| 4 | **HIGH** | **`market_day_only` is decorative on 20 of 25 scripts** — 30 jobs declare it, nothing enforces it, 5 self-guard. | **NEW (systemic)** |
| 5 | **HIGH** | **Fail-open 2FA** — `verify_totp` returns True on an empty secret. **LATENT, not live** (see §1.3 for the calibration). | confirmed |

**Counts** — Security: 3 HIGH · 5 MED · 1 LOW. Ops: 2 HIGH · 3 MED · 1 LOW.
**No CRITICAL.** Nothing found that is presently exploitable from the internet without a
prior compromise.

**The genuine surprise:** #1 and #4. Both are *systemic* and neither was on anyone's list.
#1 is a live credential leaking to disk on every alert, and our own code never prints it —
a third-party library does, because Telegram's API design puts the secret in the URL.
#4 turns batch-1's single-job holiday fix into what it really is: **80% of the jobs that
claim to be market-day-only are not.**

---

# PHASE 10 — SECURITY

## 1.1 [HIGH] The live Telegram bot token is logged in cleartext — ongoing · **NEW**

**Evidence**
```
logs/debug_2026-07-16.log:
  2026-07-16T08:15:13 DEBUG urllib3.connectionpool —
      https://api.telegram.org:443 "POST /bot<TOKEN>/sendMessage HTTP/1.1" 200 576
```
- **984 lines** across **10 files**, `debug_2026-07-03.log` → `debug_2026-07-16.log`
  (**including today — this is not historical**). All 984 are `api.telegram.org` URLs.
- Verified it is the **real** token: matches `.env`'s `TELEGRAM_BOT_TOKEN` literally;
  46 chars, canonical `digits:base64ish` Telegram shape.
- Emitter: **`urllib3.connectionpool`** at DEBUG — *not our code*.
- Root cause: `core/logger.py:373` ("debug log: DEBUG+, **all loggers**") + `:409`
  `root.setLevel(logging.DEBUG)`. urllib3 logs the request line; **Telegram embeds the
  credential in the URL path**, so any HTTP debug logging leaks it by construction.

**Exposure.** A live credential in cleartext at rest, continuously appended, on the same
disk as the DB and every backup, and within reach of anything that reads logs (the
copy_gate PC sync, a shipped log bundle, anyone on the box). The token grants full
control of the alerting bot: read the channel, and **send messages that look exactly like
the system's own alerts**. An attacker who can forge "✅ all clear" is more dangerous than
one who merely silences alerts.

**Fix direction (not implemented):** raise the third-party HTTP loggers above DEBUG
(`logging.getLogger("urllib3").setLevel(logging.INFO)`), or add a redaction filter on the
debug handler. Then purge/rotate the 10 affected files. Consider whether the rest of the
`root.setLevel(DEBUG)` + all-loggers debug sink leaks anything else.
**Tag: BATCH-safe** (logging config; no trading path) — but do it deliberately, and treat
the token as **compromised-at-rest** (rotation is Rama's call).

## 1.2 [HIGH] The live production 2FA seed is in the PC dev tree — confirmed

**Evidence.** `ops_dashboard/backend/config/gui_config.local.yaml` on the **PC** holds a
real 32-char base32 `totp_secret` + a 118-char `password_hash`. Compared by SHA-256
(values never printed):
```
PC seed sha256[:16] : 831323f7889ca9f1
VM seed sha256[:16] : 831323f7889ca9f1   ==> IDENTICAL
```
Gitignored (`ops_dashboard/.gitignore:9`) and 600 on the VM — so it is not *committed*.

**Exposure.** The second factor is not a second factor if it lives on the same machine as
the first. The PC holds **both** the password hash (offline-crackable) and the seed that
generates valid TOTP codes. A stolen laptop, a PC backup, or any PC compromise yields
dashboard access — 2FA contributes nothing to that scenario, which is the main scenario.

**Fix direction:** regenerate the seed on the VM (`auth.py:231` already has the
generator), keep it VM-only, and re-enrol Rama's authenticator. Remove the PC copy.
**Tag: Rama-owned** (requires re-enrolment).

## 1.3 [HIGH] Fail-open 2FA — **LATENT, not live** (calibrated)

**Evidence.** `ops_dashboard/backend/auth.py:62-64`
```python
def verify_totp(secret: str, code: str) -> bool:
    if not secret:
        return True   # TOTP disabled (dev only; empty secret)
```
and `:120` `verify_totp(auth_cfg.get("totp_secret", ""), totp_code)` — **the default is
`""`**, so a *missing* key silently disables 2FA.

**Why it is NOT live today** (checked rather than assumed):
- the VM's `gui_config.local.yaml` carries a **real** seed ⇒ `not secret` is False;
- the **tracked** `gui_config.yaml` carries **prose placeholders** for both `totp_secret`
  and `password_hash` (they contain spaces; not base32/hex). A non-base32 secret makes
  `pyotp` raise → caught → `return False` ⇒ the shipped default **fails CLOSED**.

So the bypass needs a config slip: the key emptied, or the auth block missing, or a
malformed overlay "fixed" by blanking the field. It is one mistake away, not currently
open.

**Exposure if it fires:** password-only access to the dashboard, silently — the login
still *looks* like it did 2FA.

**Fix direction:** invert the default — an absent/empty secret should **refuse** unless an
explicit `totp_disabled: true` dev flag is set. Fail-closed is the only correct default
for an auth control.
**Tag: LOOP / Rama** — it is a live auth control; a careless fix locks the operator out
(that is why this was defaulted to LOOP in the batch classification, not because it is
low-value).

## 1.4 [MED] `rpcbind` is exposed to the internet and serves nothing

**Evidence.** `ss -tlnp`: `0.0.0.0:111` and `[::]:111` LISTEN. `systemctl is-enabled
rpcbind` → **enabled**, `is-active` → **active**. `mount | grep -c nfs` → **0**.

**Exposure.** Pure attack surface with zero function on this host, plus rpcbind's history
as a UDP reflection/amplification vector (your box becomes someone else's DDoS tool).

**Fix direction:** `systemctl disable --now rpcbind rpcbind.socket`. Nothing here uses it.
**Tag: Rama-owned** (VM state change; = the deferred control C5).

## 1.5 [MED] SSH is world-exposed while Tailscale already works

**Evidence.** `0.0.0.0:22` LISTEN; **no `ufw`** (`command not found`); Tailscale **is
installed and up** (`100.74.84.44 trading-system`, `100.88.112.125 desktop-029ushu`), and
the GUI already serves only on the Tailscale IP (`100.74.84.44:443`).

**Mitigation that makes this MED, not HIGH** — `sshd -T` effective config:
```
passwordauthentication no · permitrootlogin no · pubkeyauthentication yes
kbdinteractiveauthentication no · permitemptypasswords no
```
Key-only. That is why the July brute-force bot achieved **0 successes** — the forensics
conclusion holds, and it holds *because of this configuration*, not by luck.

**Exposure.** Bounded to key-compromise and sshd 0-days, but the port is continuously
probed and the remedy is already installed and proven by the GUI.

**Fix direction:** C2 — bind sshd to the Tailscale interface and close `0.0.0.0:22`.
**Do the C1 key re-baseline FIRST** (`authorized_keys` = 1 key today) so the tripwire is
meaningful before the door narrows. **Tag: Rama-owned.**

## 1.6 [MED] Username-keyed lockout = operator DoS

**Evidence.** `auth.py:98-102` + `record_failure(username)`: failures and
`_locked_until` are keyed by **username**, in-memory, single process.

**Exposure.** Anyone who can reach the login page and knows/guesses the username can lock
the real operator out for `lockout_minutes` (15) by failing 5 times — **during market
hours, when the dashboard is how Rama sees the system.** Availability, not confidentiality.
Reachability is limited to the tailnet (the GUI is Tailscale-only), which caps it at MED.

**Fix direction:** key the lockout on source IP (or IP+username), and never lock the sole
operator out entirely — throttle instead. **Tag: LOOP / Rama** (auth control).

## 1.7 [MED] `/health` is unauthenticated and discloses system state

**Evidence.** `signals/webhook_receiver.py:262-274` — `GET /health`, no auth, no rate
limiter, returns `kill_switch_active`, `queue_size`, `queue_capacity`, `queue_depth`.
Bound `0.0.0.0:5000` (`system_config.yaml:204,211`).
**Currently dormant** — no listener on :5000 because `trading-system` is halted on the
planned SOFT_KILL. **It returns at the 08:15 boot.**

**Exposure.** An unauthenticated internet endpoint that reports whether the trading system
is halted and how loaded its queue is — reconnaissance, and a free oracle for timing
(e.g. "is it kill-switched right now?").

**Fix direction:** require the same token as `/webhook`, or bind health to localhost/
Tailscale, or reduce it to a bare `{"status":"ok"}`.
**Tag: LOOP** (webhook = the signal entry path).

## 1.8 [MED] `require_hmac: false` + `bind_host: 0.0.0.0`

**Evidence.** `config/system_config.yaml:212 require_hmac: false` — "Chartink cannot sign
payloads; use `?token=` auth". `:204 bind_host: "0.0.0.0"`.

**Exposure.** Signal injection is gated by a **shared token in a query string** — logged
by intermediaries, shoulder-surfable, and replayable. Auth itself is timing-safe
(`hmac.compare_digest`, before parse/queue — verified correct), so this is a design
acceptance, not a defect. Known and accepted pending Phase-3 (C-2 / NR-1).

**Fix direction:** reverse-proxy with HMAC or IP-allowlist Chartink's egress.
**Tag: Rama-owned** (accepted risk; unchanged).

## 1.9 [LOW] Residue of the just-fixed token leak

**Evidence.** The 8-char access-token prefix (batch-1 `7b492fb` stopped the *future*
writes) still appears in **`logs/cron-candle-fetch.log`**. Full token: **0 hits anywhere**.

**Fix direction:** purge/rotate that file. **Tag: BATCH-safe.**

## 1.10 What is CLEAN (stated, because "we checked" is a finding too)

- **No injection.** No `eval`/`exec` in production (`signal_processor.py:1456-57`'s
  `__import__("datetime")` is a static idiom, not dynamic). Raw f-string SQL exists **only**
  in `core/migrations.py` (PRAGMA/table names from the schema — no user input reaches it).
  The webhook's inserts are parameterised.
- **`WEBHOOK_SECRET`: 0 hits in logs. `ALERT_SMTP_PASSWORD`: 0 hits. Full access token: 0 hits.**
- **No real credential is tracked in git** — the repo's `gui_config.yaml` carries prose
  placeholders; `credentials.xlsx` (NR-2) is **absent from the PC**; a pre-commit scanner
  (`deploy/hooks/secret_scan.py`) guards the class.
- **M-K5 is fixed and deployed** (batch-1 `dbeba4a`): populated secrets no longer reach
  `config_snapshots`. Verified byte-identical on the real config.
- **SSH is properly configured** (key-only, no root, no empty passwords) — the single
  strongest control on the box.
- **The GUI is Tailscale-only** (`100.74.84.44:443`), not internet-bound.

---

# PHASE 9 — OPERATIONS

## 2.1 [HIGH] Backups have no independent failure domain (S2)

**Evidence.**
```
live DB : /dev/sda1 (/)
backups : /dev/sda1 (/)        ==> SAME DEVICE
33 backups · 5.5 GB · daily 01:00 cron writes to the same disk
mount: no other filesystems — nothing off-disk, nothing offsite
```

**Exposure.** These protect against *logical* loss (corruption, a bad prune, a wrong
DELETE) and against **nothing else**. One `/dev/sda1` failure, VM loss, or ransomware
destroys the system **and all 33 backups together**. For a system holding the audit trail
of real money, "backup" currently means "a second copy in the same place".

**Fix direction:** any independent target — object storage, a second volume, or a pull
from the PC. Even a weekly offsite copy changes the failure domain. **This needs Rama to
provision the target first; it cannot be built without one.**
**Tag: Rama-owned (blocked on provisioning).**

## 2.2 [HIGH] `market_day_only` is decorative on 20 of 25 scripts · **NEW (systemic)**

**Evidence.** `config/cron_registry.yaml`: **30 jobs declare `market_day_only: true`**.
`core/cron_registry.py:75` defines the field; `scripts/generate_crontab.py` copies it into
`_META_FIELDS`. **Nothing enforces it** — the generated crontab has no holiday wrapper, and
the schedules (`* * 1-5`) exclude weekends only. Each script must self-guard. Audit of the
25 distinct scripts:

| | scripts |
|---|---|
| **GUARDED (5)** | `daily_trade_review` *(batch-1 `2bb9194`)* · `cron_officer` · `fetch_daily_candles` · `reconcile_positions` · `system_manager` |
| **UNGUARDED (20)** | `eod_verify` · `eod_broker_reconcile` · `eod_cleanup` · `trade_journal` · `generate_screened_stocks_csv` · `refresh_instruments` · `wal_checkpoint` · `forward_shadow_record` · `reconstruct_excursions` · `sr_detector_backfill` · `compute_strategy_metrics` · `capture_metrics_baseline` · `check_cron_drift` · `fetch_fno_ban` · `auto_refresh_token` · `control_tower/runner` · `gemini_premarket_brief` · `gemini_log_review` · `gemini_trade_coach` · `gemini_data_integrity_check` |

**Exposure.** Every mid-week NSE holiday, **20 jobs that declare they only run on market
days run anyway** — reconciling a day with no trades, generating empty reports, and
making **paid Gemini API calls** on 4 of them. It manufactures exactly the noise the
Foundation Rule ("no real alerts on non-trading days") exists to prevent, and it trains
the operator to ignore the channel. Batch-1 fixed **one**; the systemic gap is that the
field looks like a control and is a comment.

**Fix direction:** enforce **centrally, once** — either the crontab generator wraps
market-day jobs in a guard, or `cron_heartbeat`/a decorator checks the registry flag —
rather than 20 more copies of the same five lines. Note the correct skip is
`status=SUCCESS` + `functional_status=SKIPPED` (batch-1's pattern): going silent on a
monitored job trades a spurious report for a spurious "no heartbeat" alarm.
**Tag: LOOP** (it changes *when* jobs run, and some read capital state) — with the central
mechanism designed first.

## 2.3 [MED] Log growth is unbounded; no retention

**Evidence.** `logs/` = **1.1 GB**, **132 files**, oldest `reconciler_2026-06-15.log`
(a month). Rotation is by date-embedded filename (F4), not size/age; no pruning job.
Same disk as the DB and the 5.5 GB of backups (→ §2.1).

**Exposure.** Slow-burn: 96 GB disk, 80 GB free, so not urgent — but it is the same
single disk, and §1.1 means those logs currently **contain a live credential**, which
makes "we keep every log forever" a security property, not just a housekeeping one.

**Fix direction:** an age-based prune (mirroring `eod_cleanup`'s retention pattern), and
purge the token-bearing files as part of §1.1. **Tag: BATCH-safe.**

## 2.4 [MED] A stale `post-receive` duplicate sits on the deploy path

**Evidence.**
```
LIVE hook (VM)           md5 bd950b7b1718001f56f5c8f27f2e00d6  (1336 B)
deploy/hooks/post-receive md5 bd950b7b1718001f56f5c8f27f2e00d6  ← this is the real one
deploy/post-receive       md5 604d2b378bac2a07b60d73adbcf3664c  (3629 B, May-16) ← DEAD
```
The stale copy is referenced only by audit documents — nothing executes it.

**Exposure.** Someone hardening or fixing the deploy edits `deploy/post-receive`, tests
nothing (it never runs), and believes the deploy changed. A dead file that looks
authoritative on the **deploy path** is a trap, not clutter.

**Fix direction:** delete it (dead-proof is above), or leave a one-line pointer to
`deploy/hooks/post-receive`. **Tag: BATCH-safe** (it was X4/P4-9's LOOP tag only because
the dup sits on the deploy path; the identity is now proven by md5).

## 2.5 [MED] Monitoring: the functional-criterion tail (Q10)

**Evidence.** `monitoring_hardening_15jul2026.md` §F2 enumerates per-job functional
criteria; ~20 remain EXECUTION-only (heartbeat says "ran", not "did its job"). The two
that bit us are done (`generate_screened_csv`, and `daily_trade_review` now reports
SKIPPED).

**Exposure.** The "green while broken" class — a job that runs cleanly and produces
nothing still reads healthy. This is the exact family as M-SC2 (a CSV that generated
empty for three days) and F2 (heartbeat==SUCCESS while delivery was dead).

**Fix direction:** convert the tail incrementally; each is one
`timer.functional_status = …` line **but each needs a per-job criterion decision**, and
several read reconciliation/capital state. **Tag: LOOP-ish** (own focused pass).

## 2.6 [LOW] Ops posture that is genuinely strong (stated for balance)

- **Deploy/rollback is the best-run part of this system.** Bare repo + post-receive
  checkout; annotated tags as the **code identity** (`git diff --name-only <tag>..HEAD`
  = markdown-only) — a practice that caught a stale-SHA instruction twice today; the
  migration-on-open guard (`ed1c4b9`, only `main.py` boot may migrate, off-market);
  three rollback levels, all schema-free; a pre-deploy backup every time.
- **Runbooks exist where they matter**: the mandatory 8-step broker-truth flatten runbook
  (never flatten from DB state), the headless guarantee (prior-day kills auto-clear —
  verified: clean daily `KILL_AUTO_CLEARED` cadence), browserless token auto-refresh.
- **Monitoring has real teeth**: the 5-path canary (incl. the respawn probe that would
  have caught the 104k alert-watcher restarts), `deploy_assert`, cron-drift markers,
  EXECUTION-vs-FUNCTIONAL status, a Telegram fallback for CRITICAL sentinels.
- **Known architectural limitation (not a defect):** no live mid-session kill/flatten API —
  KillSwitch loads at boot; an intra-session stop is stop → set-kill → start → HALT
  exit-4. Recorded as a future decision.

---

## 3. Ranked queue this feeds (fix directions only — nothing implemented)

| Rank | Finding | Tag |
|---|---|---|
| 1 | §1.1 Telegram token in logs — stop the write, then purge; treat as compromised-at-rest | BATCH (+ Rama: rotate?) |
| 2 | §1.2 Prod 2FA seed in the dev tree — regenerate VM-only, re-enrol | Rama |
| 3 | §2.1 Backups on one disk — provision an independent target | Rama (blocked) |
| 4 | §2.2 `market_day_only` unenforced on 20 jobs — one central mechanism | LOOP |
| 5 | §1.3 Fail-open 2FA — invert the default to fail-closed | LOOP / Rama |
| 6 | §1.4 rpcbind — disable | Rama |
| 7 | §1.5 SSH → Tailscale-only (C1 re-baseline first) | Rama |
| 8 | §1.6 lockout DoS · §1.7 `/health` auth | LOOP |
| 9 | §2.3 log retention · §2.4 stale post-receive · §1.9 token residue | BATCH |
| 10 | §2.5 functional-criterion tail | LOOP |

**Not in scope / unchanged:** §1.8 `require_hmac:false` (accepted, Rama-owned).

---

## 4. Method note

Credentials were **never printed**. The PC/VM 2FA seeds were compared by SHA-256 prefix;
config values were classified by *shape* (base32 / hex / contains-spaces ⇒ prose
placeholder); log hits were counted, and the one sample line quoted has its token
redacted by this audit.

A naive "long alphanumeric string" grep over `logs/` returned **433,305 hits** — that is
order IDs, hashes and tracebacks, i.e. **noise, not evidence**, and is deliberately not
reported as a finding. §1.1 stands on a literal match against the actual `.env` value.
</content>
