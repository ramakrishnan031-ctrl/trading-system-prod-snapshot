# Backup-retention abort + Telegram delivery gap — investigation (22-Jul-2026)

**Read-only. Deleted nothing, re-ran nothing, changed nothing.** Two items from the Wednesday
addendum. Filesystem `ls/du/df`, `mode=ro` sqlite (`cron_heartbeat`), log greps, and repo-code reads
only. No `--max-delete`, no job re-run, no notifier/config/token touched. Deployed `0fbfc84` / `f858778`.

---

## Section A — backup_retention nightly abort

**Headline:** the guard is working as designed and **the three live deploy anchors are structurally
safe** — the addendum's worry (a `--max-delete` taking the anchors) is refuted by the mechanism.
What's real is a **chronic** abort (5 consecutive nights) from legitimate `pre_*` accumulation. Disk
has 72 G free — no urgency.

**A1 — the 3 anchors are present and KEPT.** `pre_deploy_e4_w10_20260720.db`, `pre_softkill_21jul.db`,
`pre_deploy_b1_midnight_floor_21jul.db` — all three are in the newest **9** of the `pre_*` category
(each is a `.db`+`-shm`+`-wal` triple). `pre_*` keeps the newest 20 ⇒ they can never be delete
candidates (NEVER-DELETE-NEWEST, `backup_retention.py:17-18,103`).

**A2 — chronic, not a one-off.** `cron_heartbeat` (mode=ro): `SUCCESS` 15/16/17-Jul (deleted 1/1/5),
then **`FAILED` (ABORT) on 18, 19, 20, 21, 22-Jul — five consecutive nights.** The delete-count grew
each night `17 → 37 → 42 → 47 → 55` as the deploy-heavy week added `pre_*` triples + dailies. It will
abort **every** night until reviewed.

**A3 — what's in the directory.** 141 files, 14 G. `pre_*` 67 · `trading_system-*.db` 17 ·
`analytics-*.db` 19 · **38 unmatched** (the daily backups' `-shm`/`-wal` sidecars — refused, never
deleted, `build_plan` surfaces them as "unmatched"). The policy **is** category-aware.

**A4 — does the policy protect deploy backups? YES — definitively.** `scripts/backup_retention.py`
categories (`:43-47`): `pre_*` keep-20, `trading_system-*.db` keep-14, `analytics-*.db` keep-14.
Delete candidates are always `members[keep_n:]` (`:103`) — the **oldest** beyond keep-N. **`--max-delete N`
is only a sanity-cap ABORT threshold** (`abort = total_delete > max_delete`, `:110-111`), *not* a
delete count — it never selects *which* files. Raising it to ≥55 would delete the 55 oldest-beyond-keep
(old dailies + old `pre_*` rank 21-67), **never** the 3 recent anchors. So the addendum's *"a --max-delete
47 would plausibly take all three"* is mechanically incorrect — the tool is safer than feared.

- Current decomposition: `pre_*` 67−20 = 47 · `trading_system` 17−14 = 3 · `analytics` 19−14 = 5 = **55**
  (matches today's abort). All 55 are oldest-beyond-keep; the 3 anchors sit in the kept newest-20.

**A5 — disk.** `/dev/sda1` 96 G, 25 G used, **72 G free (26 %)**; `backups/` = 14 G. No space pressure.
The abort is **hygiene, not a space threat** — the cap (10) is now permanently below the legitimate
backlog (55). Whether to raise `--max-delete` once (the 55 candidates are all old-beyond-keep, anchors
provably safe) or re-think the cap is **Rama's review call** — read-only here.

---

## Section B — the Telegram delivery gap (the one that could matter)

**Headline:** reframed — there is **ONE configured channel** (`TELEGRAM_CHANNEL_PRIMARY=-1003977291784`);
`SECONDARY` is an unfilled placeholder (`FILL_CHANNEL_ID_2`). The failing chat_id **is** that primary.
It was **broken 16-22 Jun** (278 failures), is **working now** (Rama receives the 21-Jul watchman
alerts on it), so the gap is **historical**. And the answer to B3 is reassuring: **the genuine
capital-safety alerts were CRITICAL → email-sentinel fallback by design; the un-backstopped ERROR
failures were ~all the confabulating watchman.**

**B1 — the failure ledger.** `logs/failed_alerts.log` = 278 records, **all to `-1003977291784`** (0 to
any other id). Window: 16-Jun (43) · 17 (35) · 18 (181) · 19 (14) · 20 (1) · 22 (3) Jun **+ one 02-Jul
straggler; nothing after.** Severity: **224 ERROR + 54 CRITICAL.** Modules: `gemini_watchman` 223 ·
`order_reconciler` 43 · `main` 3 · `cron_officer` 2 · `script` 2 · `order_placer`/`security_monitor`/
`system_manager`/`token_monitor`/`cron:fetch_fno_ban` 1 each.

**B2 — "two channels" is a misread; it is ONE.** `from_env` (`telegram_notifier.py:262-263`) sends to
`TELEGRAM_CHANNEL_PRIMARY` only. `AlgoCore_Engine` = the **bot**; `-1003977291784` = the **channel** —
the same destination. `SECONDARY` unset ⇒ **no redundancy.** The channel was unreachable 16-22 Jun
(the bot was most likely not yet a member of a freshly-created channel), then became reachable.

**B3 — is any class delivered ONLY to the failing chat_id? The answer.** During 16-22 Jun **every**
class rode the single (then-failing) channel — but the tiers split cleanly:
- **The 43 capital-path alerts were ALL CRITICAL** — 36 `⚠️ Capital Drift Detected` + 7
  `RMS/MANUAL CLOSE — <symbol>`, all `order_reconciler`. **CRITICAL writes a sentinel FIRST (TG5),
  unconditionally, before the Telegram attempt** ⇒ they carried the `alert_watcher` **email fallback**.
  The real safety alerts never depended on Telegram.
- **The 224 ERROR failures had NO fallback** (ERROR → `failed_alerts.log` only) — but **223 of 224 are
  `gemini_watchman`** (the confabulating observer, established elsewhere as mostly noise). The lone
  safety-flavoured ERROR (16-Jun watchman `ORPHAN_ADOPTION`) echoes a condition `order_reconciler`
  **also** raised as CRITICAL (email-backed).
- ⇒ **No genuine safety-alert *class* was silently lost to the delivery layer.** The CRITICAL
  sentinel/email design held; the lost ERROR traffic was overwhelmingly watchman confabulation.

**Honest caveat.** The CRITICAL email fallback *delivering* depends on `alert_watcher`'s **June** state —
and `alert_watcher` was hardened **16-Jul**, *after* this window. The sentinel *write* is guaranteed by
TG5; whether the June sentinels were actually emailed is a month-old forensic question I did **not**
chase (read-only, historical). Flag, don't assume.

**B — the standing finding (not the outage — the architecture).** One channel, `SECONDARY` unset ⇒ if
`PRIMARY` breaks again, only CRITICAL survives (via email, itself dependent on `alert_watcher`).
Configuring `TELEGRAM_CHANNEL_SECONDARY` would give ERROR/WARN a second path. Pairs with Rama's token
rotation (#11) and the watchman decision (addendum §C). `telegram.enabled = true` (master switch on).

---

*Read-only throughout; no code, config, schema, backup, or state changed; no job re-run; the service
was not touched.*
