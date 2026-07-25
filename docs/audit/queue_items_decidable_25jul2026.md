# §C — Two queue items, made decidable

**25-Jul-2026. READ-ONLY: nothing built, nothing changed.** Both have sat open for weeks
because they were phrased as preferences. Both turn out to have a fact that decides them.

---

## C1 — The watchman (`gemini_watchman`)

### ⭐ The fact that decides it: **it is not scheduled, and never has been.**

```
crontab -l | grep -i watchman        →  NOT IN CRONTAB
config/cron_registry.yaml            →  no entry
running process                      →  none
reports/watchman/watchman_*.md       →  11 files, all MANUAL runs:
                                        22-Jun, 23-Jun, 01-Jul, 06-Jul … 21-Jul (latest)
```

**It is an on-demand tool Rama runs occasionally, not a monitor that watches continuously.**
The queue entry ("observe-only, nil control risk, confabulates alarming specifics") is
accurate about *what it does* and misleading about *when* — it reads like something running
daily and quietly lying. It has run **11 times in five weeks**, each because someone chose
to run it.

⚠️ **One caveat that keeps it from being a pure no-op:** `_send_critical_alert` (`:236`)
*"If agy flags something critical, send Telegram alert."* So a confabulated CRITICAL **can**
reach Telegram — but only during a run Rama initiated, while he is looking at it.

### ⭐⭐ Why "tighten the prompt" is not the lever — it has already been tried

The prompt **already** contains the instruction the tightening would add:

```
_WATCHMAN_PROMPT   Rules:
                   1. Always quote exact timestamp from log
                   2. Always include symbol if mentioned
_FLOW_SUMMARY_PROMPT   "Be specific with symbols and timestamps."
```

**Two honour-system specificity rules are present and are being ignored.** Adding a third
("must quote the exact log line") is the same instrument at a higher volume.

**And the output format actively works against it.** The prompt mandates:

```
[HH:MM:SS] [SEVERITY] [SYMBOL] — what happened (one line)
```

A rigid template with three required fields is **confabulation pressure**: a model with no
timestamp to quote must still emit something in the `[HH:MM:SS]` slot to satisfy the format.
*The specificity the prompt demands is the specificity it gets invented.* That is the
mechanism behind the observed symptom (a future-dated timestamp, a capital figure appearing
in no log) — and no wording change removes a structural incentive.

### The options, now decidable

| | cost | what it actually buys |
|---|---|---|
| **1. Keep, read sceptically** | **£0** | ⭐ Given it is manual and occasional, the confabulation only ever reaches Rama *while he is reading the run he just started*. Sceptical reading is exactly the right posture for an on-demand assistant. |
| **2. Retire it** | one file, one doc line | Nothing depends on it; nothing is scheduled; no heartbeat, no consumer. Genuinely free to remove — and equally free to keep. |
| **3. Post-hoc verification** (~15 lines) | small, but real work | ⭐ The only option that changes the **epistemics**: after the model returns, grep each emitted `[HH:MM:SS]` against the source log and drop/flag any line whose timestamp does not appear. Converts an honour system into a **check the model cannot ignore.** |
| ~~4. Tighten the prompt~~ | small | ⛔ **Recommend against** — two such rules already exist and are ignored, and the format's required fields push the other way. |

**⭐ THE DECIDING QUESTION IS NOT "IS IT ACCURATE" BUT "WILL IT EVER BE SCHEDULED".**
- **If it stays manual:** option 1. Verification is not worth 15 lines for a tool used
  ~twice a month with a human reading every word.
- **If it is ever put on cron:** option 3 becomes mandatory *before* that happens — an
  unattended confabulator with a Telegram CRITICAL path is a false-alarm generator, and
  false alarms are what made the PB-01 loss invisible for eleven days.

**No recommendation between 1 and 2** — both are free. **A firm recommendation against 4**,
and a firm "3-before-cron" if scheduling is ever considered.

---

## C2 — `TELEGRAM_CHANNEL_SECONDARY`

### ⭐ Is enabling it now HARMFUL? **No — but the redundancy it was assumed to buy never existed, and the deadline removes what was left.** That inverts the "cheap redundancy" framing on two independent grounds.

**Ground 1 — the endpoint was always shared.** MEASURED from source:

```
alerts/telegram_notifier.py:50   _TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
alerts/telegram_notifier.py:620  url = _TELEGRAM_API.format(token=self._token)
```

**One host, one bot token; the channels differ only by `chat_id`.** So a Telegram outage, a
DNS failure, a revoked bot token, or a network partition takes **both channels together**.
⇒ the secondary never protected against *endpoint* failure — only against **chat-level**
problems (wrong chat id, bot removed from a group, a channel deleted). That is a much
narrower benefit than "cheap redundancy" implies, and it was true long before M-A2.

**Ground 2 — under the shared 8 s budget it also loses degraded-mode delivery.** MEASURED by
executing the shipped config:

```
per-chat ladder = 26s   |   deadline = 8s, SHARED across channels

HEALTHY endpoint (~0.2s/send):  PRIMARY delivered @0.2s   SECONDARY delivered @0.4s   ✅ both
HUNG endpoint:                  PRIMARY burned 8.0s, failed
                                SECONDARY  ← NEVER ATTEMPTED
```

**So it works exactly when it is not needed, and does nothing exactly when it is.** In the
healthy case both deliver with ~7.6 s to spare. In the degraded case — the only case a
backup channel exists for — the primary consumes the entire budget and the secondary is
never tried.

### The honest verdict

- ⛔ **NOT harmful.** Nothing breaks; healthy alerts still reach both channels; the primary
  is unaffected. Enabling it costs nothing operationally.
- ⚠️ **But it is not the safety net the register has been calling it.** It buys chat-level
  redundancy only, in the healthy case only.
- ⭐ **The thing that WOULD buy real redundancy is a different transport, not a second chat**
  — and one already exists: the CRITICAL **sentinel + alert_watcher/email** path, which is
  written to disk *before any HTTP* (TG5) and therefore survives every failure mode above.
  ⚠️⚠️ **VERIFIED TONIGHT, and it matters: the VM `.env` contains NO `ALERT_EMAIL_*` keys at
  all** (not empty — absent). So the CRITICAL email fallback is **INERT**, and the sentinel
  file plus `alert_watcher` is currently the *entire* out-of-band path.
  ⭐ **That makes the real redundancy question `ALERT_EMAIL_*`, not a second chat id** — one
  buys a genuinely independent transport, the other buys a second row in the same window of
  the same app behind the same token. Sizing it is out of scope here; naming it is not.

**Decision framing for Rama:** enable it if you want a second *chat* to read from (cheap,
harmless, mildly useful). Do **not** enable it believing it covers a Telegram outage — it
cannot, and never could. And if the deadline ever needs to accommodate two channels, note
that 8 s was chosen against a **26 s single-channel** ladder; two channels make it 52 s, and
the two settings must move together.
