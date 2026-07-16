# BATCH-2 — AB-910 BATCH-SAFE ops/security items · DONE, NOT PUSHED

**Date (IST):** 2026-07-17, 01:10–02:00, off-market. **Base:** `57f28a1`. **Head:** `3d03ff0`.
**Tag:** `deploy-17jul-batch2` (rollback point). **Nothing pushed. Nothing deployed. No restart.**

> **Filename note.** The instruction named this `batch2_done_16jul2026.md`; the work is dated
> 17-Jul, and reports here are named by production date (`batch1_done_16jul2026.md` was 16-Jul
> work), so a `…16jul…` name would have falsely implied a same-day batch. Renamed deliberately.

**Credential discipline:** no secret value appears in this report or in any command output it
quotes. Tokens are identified by SHA-256 prefix; log hits are counted, never printed.

---

## 0. Outcome in one paragraph

Two of the three items were real and are fixed, each in its own commit. **ITEM 2 was not
implemented because its premise is false** — the retention job the audit says is missing has
existed all along, is installed, and demonstrably works; building a second one would have been
duplicate machinery justified by a misreading. Four findings the audit did not have came out of
verifying its claims, one of which materially revises §1.1: **the leak is 41% larger than
reported (1,662 lines / 19 files, not 984 / 10)** — but the extra 678 lines are a **revoked**
token, so the live exposure is exactly what the audit said it was. The full suite is green
(zero attributable failures). **The live token remains compromised-at-rest; rotation is owed.**

| # | Item | Verdict | SHA |
|---|---|---|---|
| 1 | §1.1 Telegram token in cleartext logs (HIGH) | ✅ **FIXED** (purge sequenced to deploy) | `6f69419` |
| 2 | §2.3 Log retention (MED) | ⛔ **NOT DONE — premise false, no change needed** | — |
| 3 | §2.4 Stale `post-receive` dup (MED) | ✅ **DELETED** | `3d03ff0` |

**Escalations to LOOP: none from the items themselves.** No item touched capital, kill-switch,
orders, schema, sizing, or regime. Two *new* findings are kicked to LOOP/Rama (§4).

---

## 1. ITEM 1 — the Telegram token leak · `6f69419`

### The fix
`core/logger.py` caps third-party HTTP client loggers (`urllib3`, `requests`,
`requests.packages.urllib3`, `httpx`, `httpcore`) at **INFO** inside `setup_logging()`,
recorded as a new locked decision **L11**.

**Root cause, stated exactly.** `setup_logging()` sets root to DEBUG to feed the DEBUG+/
all-loggers debug sink (L3). That opts us into the wire-level DEBUG of every third-party
library — output nobody asked for. urllib3 logs the request line of every call, path included;
Telegram embeds the bot token **in the URL path**. So the leak is structural: *any* HTTP debug
logging writes the credential to disk by construction. Our code never printed it.

**Why the emitter, not the message.** urllib3 leaks the same URL from **three** call sites
(`_make_request` request line, `urlopen` redirect, `urlopen` retry). Capping the emitter kills
all three; matching message formats would have chased them forever.

**Why not a redaction filter** (the audit's alternative): a filter on the debug handler mutates
a `LogRecord` that `QueueListener` passes to *all four* file handlers in sequence, so redaction
would have leaked or not depending on handler order. Security that depends on list order is not
security. Rejected deliberately.

**Why INFO, not WARNING:** keeps urllib3's genuinely useful retry/connection records.
**Why our own DEBUG survives:** our loggers are module-named (`core.*`, `alerts.*`), never
matching these names — and a dedicated test guards it, because silencing our own debug output
is the obvious wrong fix.

### Proof it works — and that it could have failed
Per the "a green check is evidence only if it could have been red" rule, the fix was reverted to
the true pre-change tree (`git checkout`, **never** `git stash`) and the absence grep-confirmed:

| Test | pre-change | post-fix |
|---|---|---|
| `test_l11_http_client_debug_does_not_leak_url_credentials` | 🔴 **FAILS — token written to debug log** | ✅ passes |
| `test_l11_cap_is_idempotent_and_survives_relogging` | 🔴 FAILS | ✅ passes |
| `test_l11_cap_does_not_suppress_application_debug` | ✅ passes (guard — correctly green both sides) | ✅ passes |

The leak test drives a **real HTTP request through `requests`** to a throwaway localhost server,
with a Telegram-shaped fake token in the URL path — i.e. it reproduces the live defect rather
than asserting on a mock. Full logger suite **39/39** green in pytest *and* standalone mode.

### "Nothing else leaks" — verified, not assumed
Every value in the VM's `.env` (24 vars), plus the GUI `totp_secret` and `password_hash`, was
counted against all 132 VM log files:

| Secret | Hits |
|---|---|
| `TELEGRAM_BOT_TOKEN` | **984 in 10 files** |
| `ZERODHA_API_SECRET_*` (×5), `ZERODHA_TOTP_*` (×5), `ZERODHA_PASSWORD`, `WEBHOOK_SECRET`, `ALERT_SMTP_PASSWORD`, `TELEGRAM_PERSONAL_CHAT_ID`, GUI `totp_secret`, GUI `password_hash` | **0** |

Non-secrets that do appear (expected, not findings): `TELEGRAM_CHANNEL_PRIMARY` (a destination
ID, useless without the token) and `ZERODHA_USER_ID` (a username).

**Side benefit:** `urllib3.connectionpool` was the single largest DEBUG emitter on the box —
**42,972 of ~77k debug lines on 15-Jul**, more than all our own loggers combined. The cap
removes roughly half of all debug volume.

### The purge — deliberately NOT done in this run
**The token-bearing files do not exist on the PC.** PC `logs/` holds 2 test artifacts; the token
appears in exactly one PC file — `.env`, where it belongs. All 10 files are on the **VM**.
So *"token literal absent from `logs/`" is already true on the PC (verified)*.

The VM purge is **correctly sequenced into the deploy**, not done now, because:
1. The fix is not live yet. The **08:15 boot re-leaks** into `debug_2026-07-17.log` until it is.
   Purging now would be undone within hours.
2. §6 of the instruction already schedules the VM purge as part of the deploy.

> **⏰ Timing opportunity for Rama:** as of 01:5x there is **no `debug_2026-07-17.log` yet**
> (the app boots at 08:15). **Deploy before the 08:15 boot and today's log never gets the
> token at all** — the purge then covers 10 files, not 11.

### Token status — confirmed against Telegram itself
The audit inferred the token was live from a literal `.env` match. Confirmed directly via a
read-only `getMe` (sent only to Telegram, the credential's own issuer — no new party sees it):

- **LIVE:** bot id `8648177777`, `@Trade_sysbot`, sha256[:12] `79b64abe7c82`.
- **⚠️ Treat as COMPROMISED-AT-REST. Rotation is Rama's call and is still owed** — the fix stops
  future writes; it does not un-disclose what is already on disk.

---

## 2. ITEM 2 — NOT DONE: the premise is false

The audit's §2.3 says *"no pruning job"* and asks for an age-based prune. **A `log_cleanup`
cron job has existed all along, is installed live, and works.**

```
# log_cleanup  [00:00 daily]      — deploy/cron/trading-system.cron:15-16, AND in the live crontab
0 0 * * * find /home/ubuntu/systems/trading-system/logs -name '*.log' -mtime +30 -delete; ...
```

Evidence it is not merely present but **effective**:

| Check | Result |
|---|---|
| In the **live** crontab? | ✅ yes (byte-identical to canonical) |
| Last run | ✅ `data_store/cron_marks/log_cleanup.done` → `0  2026-07-17T00:00:02+05:30` (78 min before this batch) |
| `*.log` files older than 30 days | **0** |
| System live since | **11-May-2026** |
| Oldest surviving log | **15-Jun-2026** |

That last pair is decisive: **~5 weeks of logs (11-May → 14-Jun) are gone — pruned.** The audit
read *"oldest file ≈ 30 days"* as evidence of no retention, when it is the exact signature of a
**working 30-day retention**. And 1.1 GB is not unbounded growth; it is the correct **steady
state** of 30 days × ~35 MB/day.

**Decision: no code.** Adding a second prune would duplicate a working mechanism, contradict
*"derive don't duplicate; boring code"*, and put a new Python job on the deploy path for zero
behaviour change. A one-line `find -mtime +30 -delete` is already the most boring implementation
available. The window lives in the cron registry, which is a config mechanism.

**Consequence for ITEM 1:** the audit's hope that retention "permanently caps the token residue"
is *already true* — the 10 token-bearing files self-expire by ~15-Aug. That is 30 days of
exposure, so the explicit purge still matters; it is not a substitute for rotation.

---

## 3. ITEM 3 — the stale `post-receive` dup · `3d03ff0`

Precondition re-confirmed independently rather than trusted from the audit:

```
LIVE VM hook  ~/trading-system.git/hooks/post-receive   md5 bd950b7…  1336 B
deploy/hooks/post-receive                               md5 bd950b7…  1336 B   ← the real one
deploy/post-receive                                     md5 604d2b3…  3629 B   ← DEAD, deleted
```

- **No caller anywhere.** Only docs/audits referenced it. Its own header claimed it was installed
  "via `deploy/install_vm_services.sh`" — **that script never mentions post-receive**. The dead
  file lied about its own installation.
- **Its paths are stale beyond repair:** checkout `/home/ubuntu/trading-system` — a directory that
  **does not exist** (real tree: `/home/ubuntu/systems/trading-system`); venv likewise wrong.
  Arming it would have *broken* a deploy, not changed one.
- **Nothing is lost:** `git show 57f28a1:deploy/post-receive` recovers it.
- `docs/SYSTEM_MAP.md` updated (its "record-don't-fix footgun" entry would otherwise be stale);
  the `mempalace.yaml` history entry marked superseded — its note still told operators to keep
  the deleted file in sync (mempalace is git-ignored by design, so that edit stays local).

---

## 4. NEW FINDINGS — none of these were on any list

### 4.1 🔴 §1.1 UNDERCOUNTED — there is a **second** token in the logs (it is **revoked**)
Scanning by *shape* rather than by the known value found **two distinct bot tokens**:

| Token | sha256[:12] | Hits | Files | Dates | Status |
|---|---|---|---|---|---|
| A — current `.env` | `79b64abe7c82` | 984 | 10 | 03-Jul → 16-Jul | 🔴 **LIVE** (`@Trade_sysbot`) |
| B — old systemd drop-in | `a581ca0b6831` | **678** | **9** | 15-Jun → 02-Jul | ✅ **REVOKED** (401) |

**Total residue: 1,662 lines across 19 files**, not 984 across 10. Token B's hash matches the
drop-in recorded in `telegram_token_shadow_investigation_03jul2026.md` exactly, which also
explains the leak's start date: the leak did **not** begin on 03-Jul — that is simply when the
main process stopped using the drop-in token and picked up `.env`'s. The audit searched only for
the `.env` value, so the nine older files were invisible to it.

**Why it doesn't raise severity:** token B returns **401 Unauthorized** — the 02-Jul rotation
revoked it properly. Those 678 lines are a dead credential. **Live exposure is unchanged.**

**Two things this is worth:**
- **Rotation is a proven, clean operation here** — Rama has already done exactly this once, and
  the old credential is verifiably dead. That de-risks the rotation decision §5 asks for.
- The purge should cover **19 files**, not 10 (9 are merely hygiene).

### 4.2 🔴 FIX-065's market-hours push guard has **never been live** → LOOP/Rama
Deleting the dead file exposed that it held **the only copy** of FIX-065's market-hours push
guard. `deploy/hooks/post-receive` has no such guard; the **live VM hook has no such guard**
(grep = 0). **"No push during market hours (09:15–15:30)" is manual discipline, not enforcement**
— including the constraint this very batch was run under.

`tests/unit/test_fix065_market_hours_guard.py`'s **12 tests do not catch this**: they build a
mock hook as an inline bash string and assert against *that*, never against any real hook. They
pass no matter what the system does, and are skipped on Windows besides. A textbook vacuous test.

**Decide:** implement the guard in `deploy/hooks/post-receive` (a deploy-path behaviour change —
out of scope for a batch) **or** retire FIX-065 and delete its 12 tests. Not pre-empted here.

### 4.3 🟡 The prune cannot reach the append-only `cron-*.log` files
`cron-*.log` are appended to a **fixed** filename, so their mtime is always fresh and
`-mtime +30` **never matches**: **0 of 31** are prunable. Disk impact is negligible — **0.1 MB
across 31 files** vs 1010 MB of date-stamped logs — so **no code is warranted**. It matters for
one reason only: **§1.9's residue (the 8-char access-token prefix in `cron-candle-fetch.log`)
can never self-purge**, so that file must be purged explicitly. Low risk regardless — Zerodha
access tokens are deleted daily at 05:00 and refreshed at 08:15, so an 8-char prefix of a
long-expired daily token is not a live credential.

### 4.4 🟡 `deploy/hooks/post-receive`'s own header contradicts reality
Its line 3 says *"**NOT** the currently-installed hook (the live one is checkout-only)"* — but
md5 proves it **IS** byte-identical to the live hook, crontab auto-install included. A reader
would conclude the crontab is not auto-installed on push, when it is. **Deliberately not fixed:**
editing it would break the byte-identity that is currently the *proof* of which hook is real.
Re-arming must accompany any edit. → LOOP.

---

## 5. Regression — FULL suite, zero attributable

```
4787 passed · 11 failed · 16 skipped · 12m41s   (python -m pytest tests/ -q)
```

**11 failed = the known PC-env set, baseline-proven.** Memory records this set as time-gated
(10 in-window / **11 outside**); this run was 01:2x IST → 11 expected, 11 observed.

Attribution was done properly (§4's rule): the same 11 node IDs were re-run against the **true
pre-change tree** (`git checkout 57f28a1 -- core/logger.py tests/unit/test_logger.py`, absence
grep-confirmed) → **11 failed, identical set**. **New failures: 0.**

This mattered more than usual: ITEM 1 touches system-wide logging, and `test_main.py` — three of
the 11 — has prior history of a logger leak silently vacuating tests. It is pre-existing.

Working tree is clean against HEAD; the fix is restored (grep-verified); no stray artifacts
(the NR-4 lesson — the new test uses `TemporaryDirectory` + an ephemeral-port server).

---

## 6. Restated queues — nothing lost

### 6.1 LOOP queue (do NOT batch these)
| Rank | Item | Source |
|---|---|---|
| 1 | **§2.2 `market_day_only` decorative on 20 of 25 scripts** — one central mechanism | AB-910 |
| 2 | **§1.3 Fail-open 2FA** — invert default to fail-closed (LATENT, not live) | AB-910 |
| 3 | §1.6 Username-keyed lockout = operator DoS | AB-910 |
| 4 | §1.7 `/health` unauthenticated, discloses system state | AB-910 |
| 5 | §2.5 Monitoring functional-criterion tail (~20 jobs EXECUTION-only) | AB-910 |
| 6 | **NEW — FIX-065 guard never live + 12 vacuous tests** (§4.2) | this batch |
| 7 | **NEW — `deploy/hooks/post-receive` header contradicts live reality** (§4.4) | this batch |
| — | *Carried from batch-1:* **E4 capital finding — CHECK1/RMS `costs=0.0` ⇒ daily-loss limit fed GROSS not net**; **P3-s14** webhook dedup-claim rollback | batch-1 |
| — | *Top open engineering item:* **B2/M-S4** — `secondary_screener.py:407-410` `atr/rsi/prev_close: None` ⇒ 25/100 of the live selection score is constant 0.0 | census |

### 6.2 Rama-action queue (owed — none actionable by me)
| # | Action | Note |
|---|---|---|
| 1 | **🔴 ROTATE the Telegram bot token** (`@Trade_sysbot`, id `8648177777`) | Compromised-at-rest. **Proven clean:** the 02-Jul rotation left the old token verifiably revoked (§4.1). |
| 2 | **§1.2 Prod 2FA seed in the PC dev tree** — regenerate VM-only, re-enrol | Both factors currently on one box (hash-identical) |
| 3 | **§2.1 Backups + live DB on ONE disk** — provision an independent target | Blocked on a target |
| 4 | §1.4 Disable `rpcbind` (internet-exposed, serves nothing) | |
| 5 | §1.5 SSH → Tailscale-only (C1 re-baseline first) | |
| 6 | §1.8 `require_hmac: false` + `bind_host: 0.0.0.0` | Accepted/Rama-owned; unchanged |
| 7 | **Deploy go for this batch** (+ the VM log purge, §7) | |

---

## 7. Deploy runbook (on Rama's go — off-market only)

1. **Prefer before the 08:15 boot** (§1) — then `debug_2026-07-17.log` never contains the token.
2. Fresh VM backup → `git push origin main` + `git push origin deploy-17jul-batch2`.
3. Verify: bare HEAD == `3d03ff0`; code-identity delta vs the tag empty/markdown-only;
   **schema v44, no migration** (this batch changes no schema); integrity/FK; services.
4. **Purge the token-bearing logs ON THE VM** — **19 files**, not 10 (§4.1):
   10 hold the LIVE token (03-Jul → 16-Jul), 9 hold the revoked one (15-Jun → 02-Jul).
   Also `logs/cron-candle-fetch.log` (§1.9 residue — it can never self-purge, §4.3).
   Then re-verify: **0 hits** for the live token across `logs/`.
5. **No restart owed for correctness** — but note the fix only takes effect for a process that
   *calls `setup_logging()` after the deploy*; the running app keeps its old config until the
   next boot. The 08:15 boot applies it naturally.
6. Memory only after verified.

**Rollback:** per-item revert (`6f69419` logger, `3d03ff0` dup) or reset to base `57f28a1`.
Tag `deploy-17jul-batch2` is the rollback point.

---

## 8. Method note

VM access was read-only throughout except nothing — **no file on the VM was created, modified,
or deleted by this batch**; scripts were transferred base64 (the `\\`-halving rule) and removed
after each run. No credential value was printed at any point: tokens were compared and
identified by SHA-256 prefix, log hits counted. The one outbound call made was a read-only
`getMe` to Telegram — the issuer of the credential in question, and therefore not a disclosure —
to settle whether the second token was live. It is not.
