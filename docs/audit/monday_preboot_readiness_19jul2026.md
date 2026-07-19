# Monday pre-boot readiness — 20-Jul-2026 08:15, the first real boot since S4 (VERIFY-ONLY)

## VERDICT: **READY — with one named caveat, loudly backstopped.**
Nothing was changed, started, restarted or fixed. The code that boots Monday is `d271525` (every delta since is markdown-only); the S4 fix is in it and verified; the crontab is clean after ~8 reinstalls; the S4 *class* is now closed by an independent liveness alarm. The one caveat is the **08:15 token refresh** — the single point that gates whether the system trades at all — and its failure mode is **loud** (a liveness CRITICAL at 09:00, an hour before entries), not silent like S4 was.

**Deploy state:** PC == origin == VM bare == `63a6f0d`; code tag `deploy-19jul-consecutive-losses` → `d271525` (markdown-only delta). Schema v44; system DOWN; book flat; kill-switch INACTIVE. Read-only throughout — no boot, no restart, no `scripts/*.py` run. All three artifacts proven untouched (end).

> **Provenance — this batch was interrupted and resumed.** The analysis (§A–§D) completed in one session; the machine then powered down mid-way through a MEMORY.md size-trim, before the commit, the push, and the post-restart fingerprints. On resume: the working-tree state was re-established from disk (not assumed), the interrupted trim was verified item-by-item (§E), all three artifact fingerprints were re-taken **on the VM over SSH** post-restart (the recorded values were the VM's, not PC copies) and matched, and only then were the report committed and pushed. Nothing was re-investigated; nothing was fixed. `origin` **is** the VM bare repo (`trading-vm:~/trading-system.git`), so the push target and the deploy target are one — a docs-only push still fires a full crontab reinstall (§A / deploy proof).

---

## A. The crontab, after ~8 idempotent reinstalls — clean

- **0 exact-line duplicates** (`crontab -l | sort | uniq -d` = empty). The ~8 post-receive reinstalls did **not** accumulate duplicates.
- **All five boot-critical entries present exactly once, correct:** 08:15 `auto_refresh_token.py` (Mon-Fri), 05:00 `zerodha_token.json` cleanup (daily), 18:15 `forward_shadow_record.py`, 15:50 `eod_cleanup.py`, 16:22 `strategy_registry_officer.py`.
- The three scripts that appear twice — `db_retention.py` (Mon-Sat vs Sun), `cron_officer.py` (09:20 vs 18:50), `capture_metrics_baseline.py` (intraday */5 vs 16:16 EOD) — are **legitimately dual-scheduled**, not reinstall artifacts. (Four `tools/claude/*` lines are separate operator tooling, off the trading path.)
- **A3 — idempotency:** the reinstall replaces a managed block (the post-receive re-writes the crontab from `config/cron_registry.yaml`), not append-and-dedup; the empty exact-dup set across 8 reinstalls is the evidence.
- **A4 — the 4 Sunday `cron_heartbeat` rows I wrote:** they are `eod_cleanup` heartbeats (`SKIPPED`). No **boot** job reads heartbeat freshness; the only reader is `eod_cleanup`'s own Monday 15:50 freshness check, which sees a Sunday `SKIPPED` instead of Friday's and self-corrects at that run. Benign — as previously assessed.
- **⚠️ Re-check after this batch's own push:** this commit fires reinstall #9. Given #1–#8 produced 0 duplicates and the reinstall is a full block-replace, #9 is expected identical; the post-push re-check is recorded in the deploy proof at the end.

---

## B. ⭐ Is the S4 *class* closed, or only the instance?

**The instance is fixed and the fix is in the code that boots Monday.** `utils/startup_checks.py:807-808` — `reachable = status_code is not None and (200 <= status_code < 300 or status_code == 401)` — verified present in the deployed tree (`63a6f0d`), with the docstring documenting the S4 reasoning. A `/health` 401 (the response that took prod down) now reads as *reachable*; `5xx/404/429/refused` still fail.

### B1/B3 — the boot sequence and its failure behaviour

| # | boot step | on failure | exit / loudness |
|---|---|---|---|
| 1 | 08:15 `auto_refresh_token` TOTP (cron) | token not produced | app never starts → **liveness alarm 09:00 (LOUD)** |
| 2 | `token-watcher.service` starts the app on the fresh token | no token → no start | as above |
| 3 | `clear_stale_state` (auto-clear prior-day kill) | (kill already INACTIVE) | n/a Monday |
| 4 | token validity (`is_token_valid`, `main.py:1541`) | missing/expired | **`return 6`** — non-zero, LOUD |
| 5 | preflight (`check_strategy_configs` …) | `blocking_failures` | **`return 3`** — non-zero, LOUD, `RestartPreventExitStatus` (no flap) |
| 6 | `StateStore` open / schema | v-mismatch (n/a — v44==v44) | non-zero refuse |
| 7 | Flask webhook thread + **post-start `/health` self-check** (`main.py:3325`) | `5xx/404/refused` → `_shutdown_event.set()` | **clean exit 0 — the S4 signature.** 401 now SAFE; genuine down still fatal → **backstopped by liveness (B4)** |
| 8 | daemon-liveness on `/health` (`main.py:3333`) | dead poll thread → `/health` unhealthy | non-gating at boot; caught by monitors |
| 9 | main loop | — | trades |

### B2 — every self-check that infers "down" from a response
**Completeness (§E4), verified repo-wide:** a grep for the range-reachability shape `200 <= status_code < 300` across every `*.py` returns **exactly two** hits, both in `utils/startup_checks.py` — `:703` and `:808`. The other `status_code == 200` matches (`alerts/telegram_notifier.py:589`, `scripts/cron_officer.py:575`, `scripts/auto_refresh_token.py:196`) are **exact-success** checks, not reachability inferences, and **none is coupled to a boot `_shutdown_event`**; the rest are test assertions. So the two sites below are the complete population of the S4 misread-pattern, not a sample.
- **`check_webhook` (`:807`) — FIXED.** 2xx **or 401** ⇒ reachable; failure ⇒ shutdown (`main.py:3327`). Distinguishes "up but answering 401" (reachable) from "not answering / 5xx" (down).
- **`check_scanner` (`:703`) — LATENT sibling, benign.** Still `2xx only`, so a Chartink scanner returning 401/403 would be *misread* as unreachable — **but its failure is a WARNING** (`main.py:1596-1597 → if not scanner_result.all_reachable: warnings.append("scanner_unreachable")`), **not a shutdown**, and the scanner URLs are public (a 401/403 is not the expected response). Same misread-pattern, no S4-class consequence. **What would make it LIVE:** only if `scanner_unreachable` were escalated from a warning into a boot-halt — i.e. wired into `blocking_failures` or a `_shutdown_event.set()`. That is precisely the kind of small, plausible-looking boot-path change that *created* S4, so it is queued for the careful loop **after** Monday and **left untouched tonight** (`main.py`/`startup_checks.py` are the boot path). **Reported, not fixed.**

### B3 — how many boot steps can exit 0 on failure?
**Exactly one: the post-start webhook self-check (`main.py:3327`).** Every other failure exits non-zero (token→6, preflight→3, others→5/7/8) or warns-and-continues. That one site is now (a) 401-safe and (b) only fatal on a genuine Flask-down — appropriately.

### B4 — what alarms on a failed boot vs a boot into a non-trading state
**This is the durable S4 fix.** `scripts/liveness_probe.py` (deployed 17-Jul, in the crontab `*/5 9-15 * * 1-5`) is **independent** (does not import `main.py`, so it survives when main is broken) and alarms when `trading-system.service` is **inactive during [09:00, 16:00) on a trading day and not operator-parked** — raising ONE CRITICAL (Telegram + sentinel). Its own false-alarm matrix treats an unmarked clean-exit-0 death (exactly S4) as an ALARM. So a boot that dies at 08:16 — or one that never starts because the token failed, or one that succeeds into a dead state — is caught at **09:00, an hour before 10:00 entries.** The gap that made S4 a *silent* full-day outage is closed; the boot self-check's clean-exit-0 is no longer indistinguishable from health.

**B5 — nothing here is a live S4-class defect to fix tonight.** The residual (should the webhook self-check shut the boot down at all? — S4 lesson 4) is an OPEN Rama decision for the careful loop **after** Monday.

---

## C. What could make Monday a zero-trade day — ranked by loudness (not probability)

| failure | how it fails | what Rama sees | loudness |
|---|---|---|---|
| **08:15 token refresh fails** (TOTP/credential/network — Rama's domain) | app never boots (token-watcher has nothing to start; or `return 6`) | **liveness CRITICAL at 09:00** | **LOUD** ✅ |
| S4-class boot death (any `_shutdown_event`/clean-exit-0) | boots, self-check fails, clean exit 0 | **liveness CRITICAL at 09:00** | **LOUD** ✅ |
| preflight/config/token-invalid abort | `return 3/6` non-zero | systemd failed unit + (09:00 liveness if still down) | **LOUD** ✅ |
| **`sim_R=None` on the forward shadow** (recorder's own Kite fetch fails at 18:15) | recorder appends records with `sim_R=None`, exits 0 ⇒ fail-LOUD never fires | nothing at the time; **detectable after** (null `sim_R` in the JSONL, §D4) | **QUIET** ⚠️ |
| broker session degraded but service up | quotes fail → `SKIPPED_QUOTE_UNAVAILABLE` → few/no trades | looks like a quiet day | **QUIET** (but token-gated: if 08:15 succeeded, the session works) |
| **"up but idle" / genuinely quiet market** | normal | a quiet afternoon | **SILENT — and this is NORMAL, see §D** |

- **C2 confirmed:** the `sim_R=None` path is real and quiet — but note it only occurs if the **recorder's** Kite fetch fails while the **app** did trade (screener_results exist); if the app didn't run at all, the recorder finds "nothing new" and appends nothing (not nulls). A null-`sim_R` day is distinguishable after the fact, which is why §D4 checks it.
- **C3:** DB idle state is clean (WAL **0 bytes**, checkpointed; no stale locks; 72 G free). `require_hmac: false` (unchanged) — the S4 fix covers the self-check's response either way. Instruments/Chartink reachability are pre-market cron steps that warn (not halt) on failure.
- **C4:** the 15:50 `eod_cleanup` prune deletes **0 rows** Monday (90-day retention, earliest data 12-Jun, nothing bites until ~10-Sep) — the signals history is unchanged. Confirmed still holds.

---

## D. What a normal Monday looks like — so an ordinary morning is not misread as an outage

| time | what happens | NORMAL appearance | ABNORMAL |
|---|---|---|---|
| 08:15 | TOTP token refresh (trading-day only) | token file appears; token-watcher starts the app | no token → liveness alarm at 09:00 |
| 08:15–09:00 | boot: kill auto-clear (already INACTIVE), preflight, Flask, `/health` self-check | service goes `active`; canary 08:20 green | service stays inactive → liveness alarm 09:00 |
| pre-10:00 | Chartink POSTs arrive before the entry window | **every webhook 403 — CORRECT, by design** (`entry_start:10:00`; ~25,960 such POSTs across the window). **NOT an outage.** | (a 403 storm here is the single most-misread "failure") |
| 10:00 | entries open | orders begin | — |
| 10:00–10:15 | the opening burst | one entry per ~20 s (the throttle); **87% of the day's orders land in hour 10** | — |
| 10:00–15:00 | liveness probe every 5 min | silent (service active) | CRITICAL if the service died |
| 15:15 / 15:17 | entry cutoff / EOD squareoff | open positions squared off | — |
| 15:50 | `eod_cleanup` | prunes **0 rows** | — |
| 16:22 | strategy-registry officer (first live run) | 0 new / N confirmed / 0 conflicts | DIRECTION_CONFLICT alert |
| 18:15 | forward-shadow recorder — **OOS day 4** | appends the day's signals with **real `sim_R`** | `sim_R=None` (recorder's Kite fetch failed) — check this (§D4) |

- **D3 — set expectations from the record:** ~**6 entered trades/day**, ~**3 winners/day**, 87% of orders in hour 10, one entry per 20 s through the burst. **A quiet afternoon is normal**, not a failure.
- **D4 — the one thing worth checking after the close:** did the forward shadow append **OOS day 4 with real `sim_R`**, or nulls? (The confirmation path for D3/D4/#10/D1 depends on it.)
- **D5 — what Monday proves and does NOT prove:** the book is flat, so rehydrate is a **no-op** (Phase 1 replays 0 trades, Phase 2 carries 0 P&L, M-C1 carryover 0). **Monday proves only that the boot does not crash and the S4 fix holds in production.** Still unproven: Phase 1 replay of a real open position, Phase 2 realized-P&L carryover, M-C1 with non-zero carryover, same-day kill survival across a restart, and the first live HARD_KILL (M-C8).

---

## E. Session provenance — interrupted mid-trim; the MEMORY.md trim verified (instruction §A/§B)

**Working-tree state, re-established from disk (not assumed).** In the git repo, the **only** uncommitted item is this report (`docs/audit/monday_preboot_readiness_19jul2026.md`, untracked); nothing else is modified, nothing is staged. PC `63a6f0d` == origin (`trading-vm:~/trading-system.git`) `63a6f0d` == VM bare `63a6f0d` — **nothing was partially pushed** by the interruption. The Claude **memory palace** (`~/.claude/.../memory/`) is a *separate, non-git-tracked* tree, which is why the mid-trim MEMORY.md never appears in `git status`.

**The trim outcome (§B).** MEMORY.md on disk is **20,032 bytes** (mtime 19-Jul 15:53) and is **coherent and complete** — well-formed markdown, not truncated mid-line or mid-section, every structural block intact (Core · Hygiene · Feedback/Rules · Operations · Careful-loop · RAMA-actions). Because the palace is not git-tracked and **no `.bak`/`.tmp`/snapshot of the pre-trim file exists**, a literal byte-level itemised removal diff (§B1 as worded) is **not possible**; I state that plainly rather than fabricate a removal list. The safety question §B actually asks — *did anything get lost?* — is answered instead by four positive checks:

1. **§B3 — every open board item is present:** E4/W10 · D1 · D2 · D3 · D4 · PerformanceAllocator · Regime · Freeze-min_pass · Prune-retention · Throttle-admission (all on the DECISIONS-BOARD line), plus Q10 Part B / the Kite token and the standing security actions (RAMA-ACTIONS line). None missing.
2. **Link integrity:** every markdown topic-file link in MEMORY.md resolves on disk — **0 missing** (checked programmatically).
3. **Archive untouched:** `MEMORY_ARCHIVE_2026H1.md` was **not modified today** (mtime 18-Jul 21:04). The trim therefore *relocated nothing out* — so nothing was stranded, and the "relocate, never delete" rule was not tested.
4. **Size:** the file sits at ~20 KB, **above** the ~17 KB target the prior (verified-lossless) compaction established — consistent with an interruption that hit *before* the trim removed material content.

**Conclusion:** nothing in category (iii) [content that existed *only* in MEMORY.md] was lost; **nothing needed restoring (§B4).** The file is over its size target, which **§B5 explicitly permits** — a slightly-over-budget index is safer than one trimmed under pressure at the tail of an interrupted session; a considered compaction is deferred to after Monday.

---

## PROOF OF READ-ONLY (all three artifacts)
- No boot/restart; no `scripts/forward_shadow_record.py`; no `scripts/*.py --db`. `systemctl is-enabled/is-active` and `mode=ro` queries only.
- **Live DB:** `6df0c09a…`, mtime `2026-07-19 11:14:29`, size `89,968,640`. **Forward-shadow JSONL:** 7,827 lines, mtime `2026-07-16 18:15`, size `4,526,565`. **analytics.db:** `bb229f44…`, size `24,133,632`, mtime `2026-07-19 02:30:04`.
- **Re-fingerprinted on the VM over SSH after the PC restart (§C1/§C2) — all three byte-identical to the values recorded pre-restart** (DB size + mtime-to-the-second + sha; analytics sha; JSONL line-count + mtime). The JSONL mtime is *still* 16-Jul: the 18:15 forward-shadow crons on 17/18/19-Jul **did not touch it** (non-trading days append nothing; VM clock was 16:05 at re-take, before today's 18:15), and no manual recorder run occurred. Nothing differed, so §C2's "investigate before committing" did not trigger. *(The post-push crontab re-check and a final re-fingerprint are recorded in the **Deploy proof** addendum, added by the follow-up commit after this one's push.)*

*Docs-only. Nothing was changed, started, restarted, or fixed. The token is Rama's to watch at 08:15; everything else has a loud backstop.*
