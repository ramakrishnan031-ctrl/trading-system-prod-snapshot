# The alert stream, fixed — and the four "external closes" that were not

**26-Jul-2026 (Sunday, early). Market closed, book flat, trading service DOWN
(`inactive`, `NRestarts=0`, `ExecMainStartTimestamp` still Fri 24-Jul 08:15:27).**

Follow-on to `critical_alert_stream_audit_25jul2026.md`, which classified the stream
but applied nothing. This one applies §A, fixes the §B mechanism, corrects §C against
measurement, and answers §D.

⚠️ **One thing here goes live tonight, not Monday.** `security-watcher.service` is a
one-shot with `RestartSec=60`, so it re-reads `scripts/security_monitor.py` and
`config/security.yaml` within 60 seconds of the push. The §B change is therefore
**deployed, not queued** — unlike everything that waits for the 08:15 boot.

---

## The four headline answers

1. **§A was already applied — by Rama, at 00:24:00 tonight, not by me.** The override
   baseline names the current key. What was left stale was the *committed* fallback in
   `config/security.yaml`, which is now fixed. The alert has already stopped **at the
   source**: the check produces zero findings.
2. **§B is built.** The dedup ledger could not tell *"still true"* from *"true again"* —
   and it failed in **both** directions. The loud half cost 37 CRITICAL emails; the
   quiet half silently swallowed recurrences. Both are closed.
3. **⭐ §C's premise is largely REFUTED by measurement.** "24 scheduled reports sent as
   CRITICAL" is an overcount of the *channel*, not the *email*. `System Manager EOD —
   clean` already arrives as **`[LFL836] INFO — …`**. Re-rendering all 85 sentinels
   through the exact function that builds the subject proves it.
4. **⭐⭐ §D is much worse than "one alert was missed", and much better than "money was
   lost".** WAAREERTL was **not** an external close. It was the system's **own target
   order filling**. All four `RMS/MANUAL CLOSE` CRITICALs are false. And the reconciler
   **logged the evidence itself, 1.3 seconds before firing the alert.**

---

# §A — The SSH key

## A1 — It was already re-baselined, and not by this session

```
data_store/security/ssh_key_baseline.json
  approved_at   2026-07-26T00:24:00.880298+05:30
  approved_by   operator (scripts/approve_ssh_keys.py --apply)
  note          deliberate SSH re-baseline after a legitimate key rotation
  fingerprints  ["SHA256:9WRshnFMWRlLCWRPMp5VIdaTUlpIj5dILraFNvilGzg"]
```

Evidence it was an operator run, not mine and not automation:

| check | result |
|---|---|
| file mtime | `00:24:00.880` — **before** this session's first VM command (00:34:02) |
| SSH sessions at that moment | an interactive session from `157.50.3.244`, 00:17→00:24 (mine is `157.51.76.152`, from 00:30) |
| crontab entries for `approve_ssh_keys` | **0** |
| claude process / session files on the VM | **none** |
| repo files changed since 00:00 | only the two cron marks, `security_state.json`, `last_run.json` (60 s watcher) and this baseline |

The tool was used, as §A1 required — `scripts/approve_ssh_keys.py --apply`, not a manual
edit — and it left the audit trail: the same verbatim note as the 28-Jun precedent, dated,
with the fingerprint. **A1 and A3 were satisfied before I started.**

## A2 — The third baseline was still stale. Now fixed.

| | before | after |
|---|---|---|
| committed `config/security.yaml:56` | `SHA256:uDRN8BJT…` (retired **12-Jul**) | `SHA256:9WRshnFM…` |
| operator override JSON | `SHA256:9WRshnFM…` | unchanged |
| what the monitor actually reads | `SHA256:9WRshnFM…` | unchanged |

Proven **by execution** on the VM, not by reading the file:

```
committed  expected_key_fingerprint : 'SHA256:uDRN8BJTmfNGFfCLofbnXkIGtWF6qTtrREQrQJGKduk'
override loaded                     : True  2026-07-26T00:24:00.880298+05:30
EFFECTIVE expected_key_fingerprints : ['SHA256:9WRshnFMWRlLCWRPMp5VIdaTUlpIj5dILraFNvilGzg']
LIVE keys                           : ['SHA256:9WRshnFMWRlLCWRPMp5VIdaTUlpIj5dILraFNvilGzg']
UNEXPECTED (live not in allowed)    : []
```

⭐ **Why the committed value matters even though the override wins.** `data_store/` is
not git-tracked. Lose the override — a VM rebuild, a restore, an rm — and
`apply_operator_ssh_baseline` returns `None`, the committed value takes over, and the
6-hourly CRITICAL loop restarts from a key retired two rotations ago. **Fixing it costs
one line and closes that trap.**

⛔ **It was NOT blanked instead.** With no override and no committed fingerprint,
`allowed` is empty and `check_authorized_keys` skips the unexpected-key test entirely
(`if allowed:`). **Silently disabling the check is worse than a wrong value** — the same
reasoning that kept `email_fallback.enabled: true` yesterday.

## A4 — The alert has already stopped, and here is how to confirm it

**Do not read this off the file contents.** The stronger proof is that the finding is no
longer *produced*:

```
$ security_monitor.py --report     (no alerts, no state writes)
authorized_keys: 1 key(s) ['SHA256:9WRshnFM…']
No findings.

journalctl -u security-watcher     8 consecutive passes: "0 finding(s), 0 new alert(s)"
data_store/security/last_run.json  {"clean": true, "findings_count": 0, "max_severity": "INFO"}
```

**Zero findings in ⇒ zero alerts out, at any time.** `_dedup` can only remove findings,
never create one, so the cooldown is irrelevant when the check itself is silent.

**The next cycle, named.** Last SSH-key CRITICAL: `2026-07-25T21:46:45.560902+05:30`
(37th). Cooldown 21600 s ⇒ the next eligible re-fire is **2026-07-26 03:46:45 IST**.

> **Confirmation:** after 03:47, the count of sentinels in `data_store/` whose title
> contains `UNEXPECTED SSH KEY` must **still be 37**, and no `critical_alert_*` file
> may carry a `ts` at/after `2026-07-26T03:46`. If a 38th appears, the override was not
> read — check `apply_operator_ssh_baseline` and the file's permissions.

## E3 — the lockout risk in the instruction does not exist

§E3 warned that §A "changes `authorized_keys` state". **It does not.**
`approve_ssh_keys.py` writes only `data_store/security/ssh_key_baseline.json` and re-seeds
the monitor state; it never touches `~/.ssh/authorized_keys`. Verified: the file's mtime
is still **23-Jul 21:47:26**, 1 line, and every SSH command in this session authenticated
with `SHA256:9WRshnFM…`. **There was no way to lock ourselves out here.**

---

# §B — The re-alert loop (BUILT)

## B1/B2 — What was actually wrong: one number, two opposite failures

`_dedup` stored **one float per finding key** — the last time it alerted — and compared it
to a fixed cooldown. A single number cannot express the difference between a condition
that *never went away* and one that *went away and came back*. So it got both wrong:

| | what happened | cost |
|---|---|---|
| **"still true"** | a persistent benign condition re-fired at **full CRITICAL every 6 h, forever**, with no decay, escalation or expiry | 37 CRITICAL emails from one stale baseline — **45% of the entire delivered stream**, across 9 days and two key generations |
| **"true again"** | a condition that **cleared and recurred inside the cooldown was silently dropped** | unknown, by construction — it leaves no trace |

⭐ **The second one is the dangerous one, and it was invisible because the first one was
so loud.** Fixing only the noise would have left it in place.

## B2 — The shape built

The ledger now records **presence**, not just alerts:
`{first_seen, last_seen, last_alerted, repeats}`.

* **Absent for more than the presence gap (default 300 s), then seen again ⇒ RECURRENCE.**
  New episode, alerted **immediately at full severity**. This closes the silent half.
* **Seen continuously ⇒ PERSISTENCE.** The interval widens along a ladder —
  **6 h → 24 h → 7 d, capped** — and every repeat after the first is **downgraded out of
  CRITICAL** and labelled `(STILL PRESENT) … [REPEAT #n] UNCHANGED for 3d 4h`.
* A **one-pass flicker is persistence, not recurrence** — otherwise a flapping condition
  re-earns CRITICAL every few minutes and we are back where we started.

**Measured against the real cadence** (60 s passes, condition present on every one, for
the 9 days the SSH baseline actually persisted): **1 CRITICAL + 3 decaying WARNINGs,
instead of 37 CRITICALs.**

### ⭐ Why downgrading a repeat is safe — the structural argument

Every `Finding.key` encodes the **identity** of the condition, not just its type:

```
authkeys:unexpected:<fingerprints>     authkeys:hash:<sha12>
file:<label>:<sha12>                   copybypass:<audit_event_id>
```

**Any change to what is wrong produces a different key**, which is a different condition,
which alerts at full severity immediately. Only a byte-for-byte unchanged condition is
ever downgraded. This is pinned by a test that reads the source and fails if a key ever
loses its identity component — because that is the one change that would make the
downgrade unsafe.

## B3 — "Still broken" does NOT go quiet

Three channels, in ascending order of reliability:

1. **The repeat still fires** — as WARNING, on the decaying ladder, saying how long the
   condition has held and that it is a repeat. ⚠️ *Honest limitation:* a WARNING Telegram
   is dropped silently on delivery failure (TG routing), so this is a courtesy, not a
   guarantee.
2. **`data_store/security/last_run.json` — the guaranteed channel.** It is written from
   the **pre-dedup** findings on **every ~60 s pass**, so it reflects the CONDITION, not
   the alert. It now also **names** the persistent keys (`persistent[]`,
   `persistent_count`) instead of only counting them.
3. **The Control Tower reads it, daily.** `ops/control_tower/aggregator.read_security`
   raises a finding whenever `clean` is false, at the monitor's native max severity. This
   is not new — and it means the stale SSH baseline **necessarily** produced a Control
   Tower CRITICAL every day it was present, which is consistent with the six
   `Control Tower — N issue(s)` sentinels at 17:05 on consecutive days.

**Not made (one line, deliberately left):** `read_security` could name the persistent keys
in its `reason` instead of only the count. That widens the change into the Control Tower;
the data is now there for it whenever Rama wants it.

## B4 — Built, not held. The one judgement call, named.

The shape was contained (one function, additive state, no new channel) and the safety
argument is structural rather than empirical, so this was **built**. The judgement call is
**the ladder itself** — 6 h → 24 h → 7 d, and CRITICAL→WARNING on repeat. Both are now
**config, not code**: `realert_backoff_multipliers` and `realert_presence_gap_sec` in
`config/security.yaml`. Rama can retune without a deploy; a test pins that the yaml value
actually reaches the behaviour, so the knob cannot become decorative.

## B5 — RED-first, parity

Every behavioural test was run against the pre-fix `_dedup` and failed. **PARITY (Rule 5)
is argued, not skipped:** `security_monitor` is standalone VM infrastructure with no
trading mode — it never reads one and never branches on one, so PAPER and LIVE are
identical by construction. That is pinned by a test that fails if the module ever gains a
mode dependence.

---

# §C — The scheduled reports (premise corrected by measurement)

## C1 — Every scheduled emitter on the CRITICAL path

| emitter | schedule | sent when | severity source | can it ever mean a problem? |
|---|---|---|---|---|
| `system_manager` EOD | 18:45 Mon-Fri | **always** | `context.severity`, CRITICAL iff violations/soft-kill else INFO | **yes** — it can trip SOFT_KILL |
| `cron_officer` EOD | 18:50 Mon-Fri | **always** (`is_eod`) | verbatim subject + context | yes |
| `cron_officer` briefing | 09:20 daily | only if ban-active **or** CRITICAL | verbatim subject + context | yes |
| `preflight` Phase C | 09:19 daily | **always** (phase C) | verbatim subject + context | yes |
| `preflight` Phase A/B | 09:14 | only if CRITICAL | verbatim subject | yes |
| `control_tower` | 17:05 daily | only on a CRITICAL / new-HIGH finding | notifier tier (writes no context) | yes |
| `security_monitor` | ~60 s | only on a finding | none (sentinel is CRITICAL-only) | yes |
| `kill_switch` | on trigger | only on a kill | none | yes |

## C2 — ⭐ The measured split: the reports already declare honest severity

`alert_watcher._build_email` is the single place an email subject is decided:

```python
severity = data.get("context", {}).get("severity", "CRITICAL")
subject  = data.get("subject") or f"[{_ACCOUNT_TAG}] {severity} — {title}"
```

**All 85 delivered sentinels were re-rendered through that exact function.** Result:

```
subjects containing "CRITICAL" :  69 / 85
  security_monitor 45 · control_tower 6 · cron_officer 5 · kill_switch 5 · preflight 4 · order_reconciler 4

subjects NOT containing "CRITICAL" : 16 / 85
  system_manager 6   →  [LFL836] INFO — System Manager EOD — clean
  cron_officer   6   →  [LFL836] 📋 Cron Morning Briefing — 24-Jul  /  ✅ Cron Daily Report — 24-Jul
  preflight      4   →  [LFL836] ⚠️ Pre-flight READY (1 warnings) — 24-Jul
```

⇒ **The audit's starkest example was wrong about the email.** `System Manager EOD — clean`
is **already an INFO email**. Its code already does exactly what C2 asks for:
`severity = "CRITICAL" if (violations or reasons) else "INFO"`. **There is nothing to
downgrade there, and downgrading it would have been a change made against the evidence.**

**What C2's rule correctly identifies is a real but LATENT trap, not a live defect:** the
transport **infers CRITICAL when it is not told**. Every emitter that omits
`context={"severity": …}` today only writes a sentinel for a genuine CRITICAL, so it is
currently harmless — but it is exactly how a future scheduled report would silently join
the CRITICAL stream. Per the LIVE-vs-LATENT rule: **documented and pinned with tests**,
not "fixed" by changing a default that is currently correct.

## C3 — Nothing was downgraded, so nothing became silent — and the proof exists anyway

Had `EOD — clean` been downgraded or removed, three independent proofs that the 18:45 job
ran would remain, **measured on the VM 26-Jul**:

| evidence | measured |
|---|---|
| `cron_heartbeat` row `system_manager_eod` | **34 rows**, all `SUCCESS`, latest 24-Jul 18:45:03 |
| saved report `reports/system_manager/<date>.txt` | **26 files**, latest `2026-07-24.txt` |
| `cron_registry.yaml` → `monitored: true` | the **18:50 Cron Officer EOD** flags it `MISSED` if it does not run — an *active alarm on absence*, not a passive record |

**The third is the one that matters:** removing the email could not create a silent
success, because a different job actively complains when this one does not run.

## C4 — What was built, and what was refused

* **Built:** a guard test file pinning (a) the clean EOD declares INFO and emails as INFO,
  (b) a violation day still emails CRITICAL, (c) the `CRITICAL`-by-default trap, and
  (d) the three C3 proofs — so that a future refactor dropping `context` turns the routine
  report into a CRITICAL email and goes **red** instead of quiet.
* **Refused, with evidence:** the downgrades themselves. They would be changes made
  against measurement.
* **Reported, not built — needs a decision:** the **five routine 15:15 circuit-breaker
  SOFT_KILLs**. These are the remaining genuinely-mislabelled recurring CRITICALs. They
  are *not* a safe sweep: `soft_kill` serves both the designed daily circuit breaker and
  real emergencies, so separating them means branching on the kill reason
  (`SCHEDULED_KILL_REASONS` is the existing hook) — a capital/kill-path change, which the
  CAREFUL-LOOP rule says is never swept. 🔴 **Rama's call.**

## Corrected noise ratio

```
before :  45 security_monitor + 6 control_tower(propagated) + 5 routine kills + 4 false RMS
       =  60 / 85  =  71% noise
after §A+§B  :  the 45 collapse to ~1 per condition
residual     :  5 routine 15:15 kills  +  4 false RMS labels   (both now named, neither swept)
```

⇒ **The bigger half after the SSH fix is NOT the scheduled reports.** It is §D.

---

# §D — WAAREERTL: not an external close. None of them were.

## D1 — What actually happened

The alert is `order_reconciler._check1_manual_close` (CHECK1 / RC5a, FIX-148 GAP 5). It
fires when **the local trade is OPEN/PARTIAL and the broker reports no position** — a
*state comparison*, not a causal determination. It then asserts, as fact:
*"Position closed externally."*

For WAAREERTL that assertion is **false**. The trade closed on **its own target order**:

```
trd_9aa52a585f…  WAAREERTL  SHORT  gap_go_short   entry 957.20   TGT limit 940.0369
  order 260723170289391  leg=TGT  BUY LIMIT @940.0368884  ->  status COMPLETE
  trades.exit_price = 940.00        (a BUY limit filling at/below its limit)
```

## ⭐ D1 — The reconciler logged the evidence itself, 1.3 seconds before the alert

Verbatim from `logs/reconciler_2026-07-23.log`:

```
13:10:45.796  WARNING  check1: orphan TGT order 260723170289391 is being processed at
                       broker (may fill); leaving local status for order_monitor:
                       "Order cannot be cancelled as it is being processed. Try later."
13:10:45.835  INFO     check1: exit_price resolved from broker trades: 940.00
13:10:46.732  INFO     order_monitor.complete  avg_fill_price=940.0  slippage_pct=-0.0039
13:10:47.144  CRITICAL CHECK1 MANUAL_CLOSE: … WAAREERTL local=OPEN/PARTIAL
                       broker=no_position exit_price=940.00 exit_source=broker_trades
```

**The broker refused the cancel because the order was mid-fill, and the reconciler wrote
that down — then called the result an external close anyway.** This is not a race it could
not see. The information needed to suppress the false alert was in the same function, one
second earlier, already in its own log line.

**The same line appears for two of the other three:**

| when | symbol | the tell | true exit |
|---|---|---|---|
| 20-Jul 13:52 | HUHTAMAKI | `orphan SL order … is being processed at broker (may fill)` @13:52:21.258 → `order_monitor.complete 243.2` @13:52:22.454 → CRITICAL @13:52:22.592 | **SL_HIT** |
| 23-Jul 13:10 | WAAREERTL | as above | **TGT_HIT** |
| 24-Jul 11:16 | GODIGIT | `orphan SL order … is being processed at broker (may fill)` @11:16:40.602 → `order_monitor.complete 262.5` @11:16:41.582 → CRITICAL @11:16:41.976 | **SL_HIT** |
| 24-Jul 15:17 | BLSE | no cancel-refusal; the **EOD leg** filled — `order_monitor.complete 282.9` @15:17:07.599, CRITICAL @15:17:10.805 | **EOD square-off** |

⇒ **The audit's BLSE hypothesis is CONFIRMED, with its mechanism — and it generalises to
all four. 4 of 4 `RMS/MANUAL CLOSE` CRITICALs are false positives.**

## D2 — Did the system handle WAAREERTL correctly? **Yes — completely.**

The label is wrong; **the money is right**, checked end to end rather than by eyeballing
the number:

| | expected | recorded |
|---|---|---|
| gross (SHORT, qty 1) | 957.20 − 940.00 = **17.20** | `gross_pnl = 17.20` ✓ |
| net | 17.20 − 1.02 | `net_pnl = 16.18` ✓ |
| capital released | `margin_reserved = 191.43124` | `fm_ledger RELEASE_USED −191.44` ✓ |
| balance move | 191.44 + 16.18 = **+207.62** | `6705.87 → 6913.49` ✓ exactly |
| `trades.net_pnl` vs `fm_ledger.pnl_delta` | equal (FIX-180) | 16.18 == 16.18 ✓ |
| exit legs | none orphaned | SL `CANCELLED`, TGT `COMPLETE` ✓ |
| `exits_verified` | 1 | `1 ('ok')` ✓ |

**Nothing was lost, nothing is unreconciled, and no capital was stranded.** The cost of
this defect is entirely in the alert stream and in the exit-attribution data.

## D3 — The population: the mislabel is 85%, not 4 cases

| | count |
|---|---|
| `CLOSED_MANUAL` trades, total | **41** |
| …with one of the **system's own** exit legs `COMPLETE` (SL 8 / TGT 4 / EOD 23) | **35** |
| …with **no** own leg complete (candidate genuine external close) | 6 — and 3 of those have no exit legs at all (`qty_filled=0`); the real candidates are EVEREADY 17-Jun, RCF 19-Jun, AEROENTER 19-Jun |

So `exit_reason='MANUAL'` is wrong for **35 of 41** trades. This contaminates exit
attribution: those closes are really 8 SL_HIT, 4 TGT_HIT and 23 EOD square-offs sitting
outside the `CLOSED/SL_HIT/TGT_HIT` census (84 SL_HIT / 57 TGT_HIT).

⭐ **This is already known in the report layer and nowhere else.**
`reports/daily_trade_review.py:35` documents it verbatim as *"a 4-way collision (daily-EOD,
operator-manual, RMS, kill-flatten)"* and names the permanent fix as **W8 — a per-trade
`closure_source` written at close time.** **The report layer knows the label is ambiguous.
The ALERT layer does not — it states "Position closed externally" as fact, at CRITICAL.**

### Premise checked, not assumed: the `charges=0.0` question

`CLOSED_MANUAL` books `charges=0` in **35/41** cases against `CLOSED` at **0/141**. That
looks like an open capital defect. It is not: the **last** zero-cost manual close is
HUHTAMAKI on **20-Jul 13:52**, and all three since (WAAREERTL 1.02, GODIGIT 0.30, BLSE
0.31) book real costs. **E4 (deployed 20-Jul `a266432`) already closed it.** The 35 rows
are historical. No new item.

## D4 — Reported, not fixed

Something **is** genuinely broken, so per D4 this is reported first and built nothing.
🔴 **Rama's call.** The fix shape, for when it is taken:

> CHECK1 already holds the disproof. When `_cancel_orphaned_orders_for_trade` reports an
> exit leg that could not be cancelled **because it was filling** — or when an SL/TGT/EOD
> leg for the trade reaches `COMPLETE` within the same cycle — the close is the system's
> own exit. Label it `SL_HIT`/`TGT_HIT`/`EOD_SQUAREOFF` and do **not** raise a CRITICAL.
> Reserve `MANUAL_CLOSE` + CRITICAL for the case where no own leg accounts for the close —
> which, on this data, is **rare and therefore worth waking up for**.

⛔ **Not built here.** This is the order/capital path — the CAREFUL-LOOP rule says it is
never swept — and it overlaps the already-scoped **W8**. Building it inside an alert-hygiene
batch would be exactly the kind of drive-by change that rule exists to prevent.

---

## What §D means for the original question

The 25-Jul audit asked whether a real alert had been missed, and answered *"yes —
WAAREERTL."* The truthful answer is sharper and, in one way, worse:

**Nothing real was missed, because none of the four was real.** The one category the audit
had classified as *"genuine trading incidents"* — the only 4 of 82 emails that were
supposed to matter — is **the category with a 0% true-positive rate.**

⇒ The stream was not 80% noise with a real signal buried in it. Over these three weeks it
was **noise plus four false alarms dressed as the real thing** — which is why not following
up on WAAREERTL cost nothing, and why that is not reassuring.
