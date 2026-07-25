# The CRITICAL alert stream — the SSH key, and the other 45

**25-Jul-2026. READ-ONLY. ⛔ NOTHING APPLIED.** No re-baseline, no change to
`authorized_keys`, no change to the monitor. Security conclusions are Rama's; this reports
evidence.

---

## The three headline answers

1. **The SSH key in `authorized_keys` is the one every current session authenticates with,
   including mine.** *Evidence, not a verdict:* it is the **only** key present, and it is the
   key that accepted my login minutes ago.
2. **The baseline was never updated after a legitimate change — twice.** And this exact
   situation was diagnosed and fixed once before, on 28-Jun, with a script that still exists.
   ⭐ **So re-baselining only resets the clock: the re-alert loop will recur on the next
   rotation. That is a defect independent of this key.**
3. **⚠️⚠️ 80% of the CRITICAL stream is noise, and something real HAS already been missed:**
   a genuine mid-session external position close (**WAAREERTL, 23-Jul 13:10, +16.18**) was
   delivered and appears **nowhere** in any report or memory. **That is the answer to
   "would he notice at 3am" — empirically, no.**

---

# §A — The SSH-key alert

## A1 — Whose key is it? (evidence)

```
authorized_keys                 →  EXACTLY ONE key:
                                   SHA256:9WRshnFMWRlLCWRPMp5VIdaTUlpIj5dILraFNvilGzg
                                   comment "trading-vm", ED25519
my session, minutes ago         →  Accepted publickey for ubuntu from 157.51.69.124
                                   ED25519 SHA256:9WRshnFM…      ← the same key
accepted logins, last 30 days   →  267 with this fingerprint
```

**What the evidence SHOWS:** the flagged fingerprint is the sole authorised key, and it is
the key in active daily use — every SSH session to this box, including the ones running this
audit, authenticates with it. **What it does not show:** whether Rama generated it. *That is
his to confirm; approving an unrecognised key is not a judgement an assistant should make.*

⭐ **A second fact that reframes the alert:** **five distinct keys have been accepted in the
last 30 days, and only one remains authorised.**

| fingerprint | first accepted | last accepted | logins |
|---|---|---|---|
| `uDRN8BJT…` | 28-Jun 09:39 | **12-Jul 21:13** | 900 |
| `BRi6UmV8…` | **13-Jul 09:16** | **23-Jul 21:34** | 1088 |
| `9WRshnFM…` | **23-Jul 21:45** | 25-Jul 23:36 (now) | 267 |
| `XrYwY+iv…` / `wPqEm8hq…` | — | — | 65 / 5 |

`authorized_keys` mtime: **23-Jul 21:47:26**, **1 line**.

⇒ The 23-Jul evening sequence reads unambiguously as a **key rotation**: old key last used
21:34, new key first used 21:45:57, file rewritten 21:47:26 down to a single key. **The
change the monitor is complaining about is a reduction in authorised keys, not an addition.**

## A2 — Why is the baseline stale? **It was never updated after a legitimate change — twice.**

There are **three** baselines in play, and both stored ones predate the current key:

| | value | dated |
|---|---|---|
| committed `config/security.yaml:56` `expected_key_fingerprint` | `SHA256:uDRN8BJT…` | the key retired **12-Jul** |
| operator override `data_store/security/ssh_key_baseline.json` | `SHA256:uDRN8BJT…` | **28-Jun 14:04** |
| live `authorized_keys` | `SHA256:9WRshnFM…` | since 23-Jul |

⭐ **The override file contains its own precedent, in its own words:**

```json
"approved_by": "operator (scripts/approve_ssh_keys.py --apply)",
"note": "deliberate SSH re-baseline after a legitimate key rotation"
```

**So this happened on 28-Jun, was correctly diagnosed as a legitimate rotation, and was
fixed with a purpose-built script.** Two rotations later (13-Jul and 23-Jul) nobody re-ran
it. The alert history matches exactly — **39 alert bodies, naming two different keys:**

```
30 alerts name BRi6UmV8…   (the 13-Jul → 23-Jul key)
 9 alerts name 9WRshnFM…   (the current key)
first SSH-key CRITICAL: 17-Jul 03:25     last: 25-Jul 21:46
```

⇒ **This has been firing since 17-Jul across TWO key generations.** It is not a 23-Jul
problem; it is a 13-Jul problem that a second rotation renamed.

## A3 — The two fixes, sized

**(a) Re-baseline — trivial, and the tooling already exists and has been used before.**
`scripts/approve_ssh_keys.py --apply` writes the override file; `security_monitor.py:67-70`
documents it as existing specifically to prevent *"repeated false-positive CRITICALs"*.
**One command, one file, no code change.** ⛔ **Not run — it approves a key, which is Rama's.**

**(b) ⭐ The re-alert loop — and this is the defect that outlives the key.**

The 6-hourly cadence is **by design**: `security_monitor.py:29-30` describes *"an alert-dedup
ledger so a persistent condition is not re-alerted"*, and `:516` names the window —
**"event-id dedup ledger (6h) prevents re-alerting"**.

**So it is not repeating by accident. It is designed to re-alert every 6 h for as long as a
condition persists — with no escalation, no decay, and no expiry.**

⚠️ **Answering the question directly: yes, it would do the same on the next legitimate
change.** Any benign-but-unresolved condition becomes a CRITICAL every six hours forever.
**Re-baselining stops *this* instance and leaves the mechanism intact — the next key
rotation restarts the loop, exactly as 13-Jul did after 28-Jun.**

**Shape of a real fix (described, not designed):** a persistent *unchanged* condition should
alert once, then decay to a daily/weekly digest or a status field — reserving CRITICAL
re-alerts for conditions that *change*. **The current design cannot distinguish "still true"
from "true again".**

---

# §B — The other 45

## B1 — All 82 delivered CRITICALs, classified

| class | count | |
|---|---|---|
| **SSH-key loop** | **37** | the recurring false positive above |
| **⭐ Scheduled status reports sent as CRITICAL** | **24** | `System Manager EOD — clean` ×6 · `Cron EOD <date>` ×6 · `Cron Briefing <date>` ×5 · `Pre-flight Phase B/C <date>` ×7 |
| **Routine 15:15 circuit breaker** | **5** | `SOFT KILL ACTIVATED — circuit_breaker_force_close_15:15`, five consecutive trading days, same second |
| Legitimate one-off security events | 6 | `NEW SSH KEY DETECTED` ×4 (the rotations themselves) · `SSH key COUNT exceeds baseline` ×1 · `Sensitive file changed: dotenv` ×1 (**tonight, caused by the authorised credential rotation**) |
| Control Tower findings | 6 | `1 issue(s)` ×4, `2 issue(s)`, `5 issue(s)` |
| **⭐ Genuine trading incidents** | **4** | `RMS/MANUAL CLOSE` — positions closed externally |

## B3 — Which are "a status line with the wrong severity"? **29 of 82.**

- **The 24 scheduled reports.** The starkest is `System Manager EOD — **clean**` — a CRITICAL
  alert whose entire content is that nothing is wrong. Six of those.
- **The 5 routine 15:15 SOFT_KILLs.** Fire at the same second every trading day, for the
  designed daily circuit breaker. Already documented elsewhere as routine and benign.

⇒ **These are daily reports wearing an incident severity.** They are not defects in
themselves — they deliver real information — but they occupy the channel reserved for
"something is wrong right now".

## B4 — The noise ratio, and what re-baselining actually buys

```
recurring / scheduled noise :  37 + 24 + 5  =  66 / 82  =  80%
incident-grade              :  4 + 6 + 6    =  16 / 82  =  20%

after the SSH loop is resolved:  29 noise / 45 remaining  =  64%
```

⭐ **Re-baselining takes the stream from 80% noise to 64% noise.** It is worth doing and it
is not the main problem — **the scheduled-report class is larger than the SSH loop once the
SSH loop is fixed.**

## B2 — ⚠️⚠️ Was a real alert delivered and never acted on? **YES.**

The four `RMS/MANUAL CLOSE` alerts are the genuine trading incidents — a position closed by
the broker's RMS or by hand, i.e. *not* by this system:

| when | symbol | entry → exit | qty | P&L | followed up? |
|---|---|---|---|---|---|
| 20-Jul 13:52 | HUHTAMAKI | 246.22 → 243.20 | 2 | −6.04 | ✅ yes — 4 docs, 6 memories |
| **23-Jul 13:10** | **WAAREERTL** | 957.20 → 940.00 | 1 | **+16.18** | ⛔ **NO — zero mentions anywhere** |
| 24-Jul 11:16 | GODIGIT | 259.40 → 262.50 | 1 | −3.40 | ⚠️ incidental only |
| 24-Jul 15:17 | BLSE | 283.59 → 282.90 | 1 | −1.00 | ⛔ no — but see below |

Searched by **symbol and by trade_id**, across `docs/` and the memory palace.

- **WAAREERTL (23-Jul 13:10:46) is the real miss.** A mid-session external close, on a
  winning position (+16.18), with **no mention in any report, audit or memory**. It was
  delivered to the one out-of-band channel and nothing happened.
- **BLSE is very likely a labelling artifact, not a miss:** it fired at **15:17:10**, and
  `eod_squareoff_time` is **15:17** — ten seconds after the system's own square-off trigger.
  The reconciler saw the fill in `broker_trades` and labelled it external. *Stated as a
  hypothesis with its evidence, not as a finding.*
- **GODIGIT** appears once, as an incidental control in the SPANDANA net-short investigation
  (*"GODIGIT, DBL, SURYODAY all closed to 0 today"*) — observed in passing, never examined as
  an external close.

⚠️ **A mid-session RMS close is worth understanding on its own merits**: RMS closes typically
follow a margin shortfall, which would be a capital finding rather than an execution one.
**Not investigated here** — §B is an audit of the stream, and this is now a named item.

## B5 — **If something genuinely bad happened at 3am, would Rama notice it in this stream?**

**On the evidence: no — and it is not hypothetical, because it already happened.**

WAAREERTL was a genuine, unexplained, mid-session external close. It was delivered to the
only out-of-band path and was never followed up, in a week when four separate investigations
were running. **It was not missed through carelessness; it arrived in a stream where four out
of five messages mean nothing.**

⭐ **That is the whole case for treating alert hygiene as a safety issue rather than a tidiness
one.** The path works — proven end-to-end last night. **A working path that cries wolf 80% of
the time is not a working alarm.**

---

## ⛔ What was NOT done

Nothing was applied. `authorized_keys` untouched, no re-baseline, `security_monitor` and
`security.yaml` unmodified, no alert suppressed. **Three things now need Rama, and only he
can supply them:** confirm the key is his · decide whether to re-baseline now or fix the
re-alert loop first · decide whether WAAREERTL warrants a look.
