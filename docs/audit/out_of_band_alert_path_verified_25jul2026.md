# The out-of-band alert path — verified end to end

**25-Jul-2026. READ-ONLY. ⭐ NO TEST SENTINEL WAS WRITTEN — none was needed.**
The path was exercised end-to-end in production tonight, and the evidence is stronger than a
test would have been.

---

## The three headline answers

1. **It works. PROVEN, in production, tonight** — not by a contrived sentinel but by a real
   CRITICAL raised at **21:15:28** and delivered by SMTP at **21:15:58**, 30 seconds later.
2. **⚠️ THAT CRITICAL WAS CAUSED BY ME.** `security_monitor` detected my credential rotation
   as *"Sensitive file changed: dotenv"*. **Rama received a CRITICAL email at ~21:15 tonight
   about a `.env` change that was authorised work — it is not an intrusion.**
3. **⚠️⚠️ AND THE REAL FINDING: 37 of 82 delivered CRITICALs (45%) are the SAME recurring
   false positive** — an "UNEXPECTED SSH KEY present" alert firing **every 6 hours,
   indefinitely**. The one working out-of-band path is carrying a permanent alert-fatigue
   load.

---

## B1 — Two email paths, and only one is live. The premise was correct.

They are genuinely different mechanisms with **different env vars** — conflating them was
the reason this stayed unresolved.

| | **Path 1 — notifier `_send_email_fallback`** | **Path 2 — `alert_watcher`** |
|---|---|---|
| config block | `alerts.email_fallback` | `alerts.smtp` |
| shipped state | **`enabled: true`** | host/port/from/to all set in yaml |
| credential env var | `ALERT_EMAIL_USER` / `_PASSWORD` / `_TO` | **`ALERT_SMTP_PASSWORD`** |
| on the VM | ⛔ **ALL THREE ABSENT** (not empty — absent) | ✅ **SET** |
| verdict | **INERT — and it *looks* enabled**, which is why it was believed to be the fallback | ✅ **LIVE and delivering** |

⭐ **The trap: the inert one is the one that reads as enabled.** `email_fallback.enabled:
true` is committed in the yaml, so any reader concludes the notifier has an email fallback.
It does not — `_send_email_fallback` returns before any socket operation because the
credentials it names do not exist.

## B3 — Is `alert_watcher` actually running? YES — and unlike the watchman, it always was.

```
systemctl is-active alert-watcher      →  active
systemctl is-enabled alert-watcher     →  enabled
NRestarts=0   ExecMainStartTimestamp=Thu 2026-07-16 18:47:03 IST   (9 days, no restarts)
ExecStart=… scripts/alert_watcher.py --loop        ← NO --dry-run
crontab / cron_registry                →  absent, CORRECTLY (it is a service, not a job)
its log, written 23:12 tonight         →  "No pending sentinels found." on a ~10 s poll
```

**The watchman lesson was applied and did not repeat**: I checked rather than assumed, and
this one is genuinely scheduled.

## B2 — The chain, traced

```
CRITICAL raised
  → alerts/critical.py::write_critical_sentinel()   .tmp → fsync → rename to .flag  (atomic)
  → data_store/critical_alert_<ts>_<id>.flag
  → alert_watcher (--loop, ~10 s poll) picks up pending .flag
  → _send_email() via smtplib + STARTTLS, gmail:587, ALERT_SMTP_PASSWORD
  → future.result()            ← RAISES on SMTP failure
  → mark_delivered()           ← rename to .delivered happens ONLY after a successful send
  → log "Delivered <name> -> .delivered"
  (a TELEGRAM fallback also exists inside the watcher, :406-410)
```

⭐ **Anti-vacuity on the log line:** `.delivered` is not a "we tried" marker. `future.result()`
raises on failure and the rename is downstream of it, so **the word "Delivered" is earned.**
And `ExecStart` carries no `--dry-run`, so the sends are real.

## B4 — Delivery TESTED, not assumed — by production, not by me

⛔ **I did not write a test sentinel, and deliberately so.** The batch authorised one, but
production had already run the experiment tonight, twice, with better evidence than a
synthetic flag would give:

```
2026-07-25 21:15:28   sentinel written : "🔒 Sensitive file changed: dotenv"
2026-07-25 21:15:58   alert_watcher    : Delivered critical_alert_20260725_211528_ee1e09f4.flag -> .delivered
2026-07-25 21:46:45   sentinel written : "🔒 UNEXPECTED SSH KEY present"
2026-07-25 21:47:02   alert_watcher    : Delivered critical_alert_20260725_214645_4b85a708.flag -> .delivered
```

**82 CRITICALs delivered in total; 0 still pending as `.flag`.**

⚠️ **The honest limit of this claim:** what is proven is that **SMTP accepted the message** —
the send did not raise, and the rename followed. **Arrival in Rama's inbox is the one hop I
cannot observe from here.** Given `to_addresses: ramakrishnan031@gmail.com` and 82 deliveries
over weeks, the inference is strong, but it is an inference. *He can confirm in one glance:
a "Sensitive file changed: dotenv" email at ~21:15 tonight.*

**Not writing a test sentinel also honoured §E3:** the hard deadline was that no sentinel may
be left before Monday 08:15. **The safest way to satisfy that was not to create one.**

## B6 — Sentinel state is clean. VERIFIED, not assumed.

```
critical_alert_*.flag        →  0     ← nothing pending; Monday's preflight will find zero
critical_alert_*.delivered   →  82    ← inert history, not a backlog
```

**Nothing was added by this work and nothing needs clearing.**

---

## ⚠️⚠️ THE FINDING THAT MATTERS MOST — 45% of all CRITICALs are one false positive

`security_monitor` raises **"🔒 UNEXPECTED SSH KEY present — authorized_keys has key(s) not
matching the expected baseline: SHA256:9WRshnFMWRlLCWRPMp5VIdaTUlpIj5dILraFNvilGzg"**
on a **6-hourly cadence** (03:46, 09:46, 15:46, 21:46 today, and so on).

**37 of the 82 delivered CRITICALs are this one alert.**

⭐ **This is the mechanism that hides real incidents.** The out-of-band path exists for the
case where Telegram is down and something is genuinely wrong. It is currently training its
only reader to expect a CRITICAL email every six hours that means nothing. **This is the same
failure shape that let the PB-01 loss go unnoticed for eleven days** — not a missing alert,
but a routine one.

**It is almost certainly not a security problem but a stale baseline.** The pending
`SSH → Tailscale-only` action carries the note *"do the C1 SSH re-baseline first"* — i.e. the
baseline is known to be out of date, and this alert is the consequence, firing four times a
day since.

**⛔ NOT FIXED — it needs Rama to confirm the key is his** (it is: he reaches this box over
SSH daily). **Two candidate actions, both his:** re-baseline `authorized_keys` so the alert
stops, or suppress/downgrade the repeat. **Sizing is trivial; the confirmation is not mine to
give — approving an unrecognised SSH key is exactly the decision that must not be
auto-resolved by an assistant.**

## B7 — Should `ALERT_EMAIL_*` be configured? Sized, not done.

**Recommendation: probably not, and for a better reason than cost.** Path 2 already delivers,
so configuring Path 1 would add a *second* email route over the *same* transport to the
*same* address — while a genuinely independent transport (SMS/push) would be the thing that
adds cover. And it needs a credential, which is Rama's.

⭐ **The higher-value action is the opposite of adding a channel: reduce the noise on the one
that works.** A path carrying 45% false positives is degraded whether or not a second one
exists.

**If it is wanted anyway:** three env vars in the VM `.env` (a Gmail app password), no code
change — `email_fallback.enabled` is already `true`. ~5 minutes, and it would then need the
same delivery proof this report gives Path 2.
