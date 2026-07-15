# 15-Jul-2026 Day Reconstruction — READ-ONLY Diagnostic

**Task:** reconstruct what happened on 15-Jul-2026 (the first SUPERVISED session after the
14-Jul PB-01 shadow deploy), given Rama's LOCAL POWER was out during market hours and returned
~17:30 IST. **Nothing was changed** — no code, config, schema, state; no push, no restart, no
key rotation; the 18:15 forward-shadow cron was NOT triggered manually. All reads via
`sqlite3 -readonly` / log inspection / `systemctl`. Prepared ~17:45 IST.

---

## ★ ONE-LINE VERDICT

**A — SESSION RAN CLEAN.** The VM rode through the local outage (Oracle Cloud, separate power),
auto-refreshed its own Kite token at 08:15 (browserless — **PC-independent**), booted clean with
**no migration**, ran the full 08:15→16:00 session, and **self-exited flat**. Book is **FLAT and
reconciled** (system + broker). Trade count **12 ≈ pre-deploy baseline** (14-Jul 11 / 13-Jul 14) —
the deploy prediction **held**; every one of 4,317 signal rejects maps to a §A/§B trigger or a
normal gate; **SECTOR_EXPOSURE=0, QUEUE_FULL=0, duplicate-trades=0** as predicted.

**Non-safety caveats to flag (none is a trading-behaviour anomaly, none needs action tonight):**
1. The pre-registered **M-S5 `SHADOW_INNING_ACTIVE` watch tripped numerically (323)** — but this is
   confirmed **IN-BASELINE** (prior sessions 122–840; today is on the *low* end) and **not a
   mis-fire** (all 323 on 3 symbols with genuine active innings). The "more than a couple → STOP"
   threshold was mis-calibrated vs the pre-existing `shadow_tracker` mechanism → recommend recalibrate.
2. **Two report/hygiene cron jobs FAILED:** `eod_cleanup` (FK-constraint, new today) and
   `generate_screened_csv` (pre-existing — fails identically on 13/14/15-Jul). Neither touches the
   trading path or the book.
3. **PB-01 EOD capture wrote no row** — because its Chartink alert **isn't wired yet** (no
   `pb01_breakout_retest` scanner has ever hit the webhook). Expected state, **not** an outage miss.
4. **18:15 forward-shadow cron: PENDING** (report prepared before 18:15) — Rama must confirm after 18:15.

> **Correction to the task's premise:** the instruction assumed "PC pushes the fresh token at 08:15;
> if the PC was down, no session." **False.** The token refresh is **VM-side TOTP (browserless)** —
> the token file was written **08:15:02 today** with the PC down, and auth succeeded. PC/Rama
> connectivity is **not** on the trading critical path. This matches memory (token workflow 21-Jun),
> not the task's mental model.

---

## §2a — SAFETY: BOOK FLAT & RECONCILED ✅  (checked first; nothing to touch)

**FLAT, confirmed by five independent sources. No open position, no orphan order, no GTT, kill-switch normal.**

| Check | Result | Evidence |
|---|---|---|
| System state — non-terminal trades (ANY date) | **EMPTY** (0 rows OPEN/PENDING/EXITING/PARTIAL/UNKNOWN) | `sqlite3 -readonly trades WHERE status NOT terminal` → 0 rows |
| GTT state | **EMPTY** (0 ACTIVE, table empty) | `SELECT status,COUNT(*) FROM gtt_state` → 0 rows |
| Broker positions (authoritative, 15:58 cron) | `2026-07-15: VERIFIED [SHADOW]` **positions=VERIFIED orders=VERIFIED** | `logs/cron-eod-broker-reconcile.log` |
| Broker positions (session, continuous) | `get_positions → "0 positions"` repeatedly 15:17:29 → 15:59:50 | `system_2026-07-15.log` |
| EOD verify (15:55 cron) | `EOD PENDING: 2026-07-15 — positions/orders clear` (P&L "not checked" = known pre-P1 gap) | `logs/cron-eod-verify.log` |
| Self-exit flat-check | 16:00:00 `eod_self_exit: past 16:00 IST and flat (0 active positions)` | `system_2026-07-15.log:83025` |
| Resting entry orders at shutdown | `order_monitor.shutdown_cancel_complete cancelled=0 total_entry_orders=0` | shutdown block |
| Kill switch | Normal EOD `SOFT_KILL` (circuit_breaker_force_close_15:15, by-design); prior-day SOFT_KILL **auto-cleared** at 08:15 new-day boot | `:81748`, `:3-4` |

- EOD squareoff worked: `EOD summary: positions_squared=2/2 cancels=4/4 duration=122.85s` (`:82367`); `EOD Pass 2 complete: 2 open positions exited` (`:82366`).
- **7 real fills today, all closed flat/positive.** The 3 `CLOSED_MANUAL` (AARTIIND +6.05, BIRLAMONEY +3.45, GROWW +1.58) are broker-authoritative OCO-TGT/EOD closes correctly reconciled by CHECK1 (`source=broker_trades`), not naked exits.
- Nothing can have changed the book since 16:00 — the only actor (the trading process) self-exited and no order path exists while it is down. A fresh broker API poll was therefore **not** performed (would require running code with the token); the 15:58 VERIFIED + continuous 0-positions to 16:00 are conclusive and recent.

## §2b — VM STAYED UP ✅

- **Uptime 11 days 19h — boot `2026-07-03 22:20:54`. NO reboot today.** `uptime -s`, `last -x reboot`. The outage did not touch the VM (Oracle Cloud, separate power) — now proven, not assumed.
- `trading-system.service`: `inactive (dead) since 2026-07-15 16:00:04 IST`, **Duration 7h 45min**, **`code=exited, status=0/SUCCESS`**, "Deactivated successfully." → started ~**08:15:04**, ran the full session, clean self-exit. PID 1175085.
- `token-watcher.service` active since 03-Jul (the auto-start mechanism); `gui-dashboard.service` active. No log gap during market hours.
- VM clock correct: 17:38 IST, NTP drift 0.073s (`check_ntp_sync` at boot).

## §2c — FRESH TOKEN TODAY ✅  (VM-side, PC-independent)

- **`data_store/session/zerodha_token.json` mtime = `2026-07-15 08:15:02`** — freshly written this morning (2s before boot), **despite the PC being down**. Refresh is VM-side TOTP (browserless), per token_watcher → auto-start.
- **Auth succeeded:** boot `LiveFeedManager … token_set=True`; `get_margins … net=9878.5` at 08:15:14; `token_monitor.start mode=LIVE`. **Zero** auth-failure / invalid-token / BrokerAuthError lines all day. `token_watcher.log` shows the standard "Fresh token detected → start" pattern.

## §2d — 08:15 BOOT / MIGRATION ✅  (clean, no migration)

- Boot 08:15:04, `run_all_startup_checks: OK … warnings=[]` — config(8 files), instrument cache 2228, strategy configs, holiday calendar, SDK kiteconnect==5.1.0, NTP, disk 80.9GB, DB perms — all OK.
- **NO migration.** `schema_meta.schema_version = 44` == code `EXPECTED_SCHEMA_VERSION = 44`; **zero** migration / `MigrationNotPermitted` / `StartupCheckFailed` lines in the boot log. (v44==v44 → migration skipped, exactly as predicted.)
- `check_kill_switch_present: OK (kill_switch wired; mode=live)` (`:31`) — the Q4b fail-fast passed, no abort. No config-auditor return-3.
- **Deploy unchanged:** bare `HEAD = 2dc69d5` (= `277d63e` + docs). `config/system_config.yaml` mtime `2026-07-14 18:52` (untouched since deploy). Flags verified on disk: `v3_chain_mode: "shadow"` (`:366`), `watchlist.enabled: true` (`:412`), `min_pass_score: 60` (`:401`), `force_intraday_only: true` (`:70`), `delivery_enabled: false` (`:83`) — delivery triple-locked, `strategy_control.summary` = **12 WILL / 4 WON'T** (PB-01 disabled + 3 positional dormant).

## §2e — SESSION RAN AND TRADED ✅

- **Signals arrived: 4,317** today (`signals WHERE received_at LIKE 2026-07-15`). Webhook_audit: 13 scanners firing 11–374 POSTs each.
- **Trades: 12 rows** — 4 CLOSED, 3 CLOSED_MANUAL, 4 FAILED, 1 REJECTED (`trades WHERE created_at LIKE 2026-07-15`). Span 10:00:22 → 14:57:13 (first entry at the 10:00 entry-window open). 11 signals reached `PROCESSED`.
- Symbols: WANBURY/NUVOCO/LANDMARK/BANDHANBNK (CLOSED); BIRLAMONEY/AARTIIND/GROWW (CLOSED_MANUAL); PATANJALI/RALLIS×2/STALLION (FAILED = no fill); NUVOCO 10:00 (REJECTED). **ERROR-level lines in the log: 0.**
- Zero trades was **not** the outcome — a normal session ran (option i/ii/iii moot).

## §2f — FALSIFIABLE CHECK (prediction doc §7) ✅  prediction HELD

- **Trade-count:** 12 today vs **14-Jul 11 / 13-Jul 14 / 10-Jul 20 / 09-Jul 15** → in-baseline, **MATCH**.
- **Every reject reason maps** (signals by status): `STRATEGY_CONTROL 1459` (PB-01 fail-closed + 3 dormant positional — §B/normal) · `SCORE_* ~2300` (min_pass 60 — normal) · `SHADOW_INNING_ACTIVE 323` (§A2 — see below) · `QUOTE_UNAVAILABLE 130` (known illiquid-quote noise) · `CIRCUIT_PROXIMITY 21` · `SIZING_CONCENTRATION 21` · `OPEN_POSITIONS 20` · `ENTRY_THROTTLED 11` · `DUPLICATE_SYMBOL 7` · `STRATEGY_POSITION_LIMIT 4` · `PLACEMENT_FAILED 1`. **No unmapped/UNEXPECTED reason.**
- **gate-8 SECTOR_EXPOSURE = 0** (as predicted; Finding-1 resting-book term is structurally 0). **QUEUE_FULL = 0** (M-S2, queue never filled). **Duplicate real trades per signal = 0** (M-S6 dormant, cross-checked empty).
- **M-S5 `SHADOW_INNING_ACTIVE` = 323 — the pre-registered watch item.** Investigated fully:
  - **3 symbols only:** NUVOCO 247, LANDMARK 74, WANBURY 2. Each had a **real trade CLOSE today**, spawning a simulated inning (e.g. NUVOCO `innings` row 258 `is_real=1` TGT +5.60 @10:22 → row 259 `is_real=0` sim, ran to EOD). **Every reject is on a symbol with a GENUINE active inning — zero mis-fires** (the FALSIFY sub-condition "reject on a symbol with no active inning" is NOT met).
  - **Volume is baseline, not new:** by session — 15-Jul **323**, 14-Jul 303, 13-Jul 823, 10-Jul 840, 09-Jul 505, 08-Jul 480, 07-Jul 828, 06-Jul 122. Today is on the **low end**. The pre-existing `shadow_tracker` (enabled well before this deploy) rejects every Chartink re-fire on a symbol holding an active inning; scanners re-fire 300–374×/day, so hundreds of these rejects are normal.
  - **Conclusion:** the guard behaved as designed; the literal ">a couple" STOP threshold in the prediction doc was mis-calibrated against this mechanism. **Substantively no STOP trigger fired.** Recalibration is a Web-Claude/Rama call (recorded, not acted on).

## §2g — PB-01 SHADOW CAPTURE ⚠️ no row (expected — alert not wired)

- Capture worker + entry stage **started clean**: `pb01 watchlist capture worker started` / `pb01 entry stage started (window 09:20-11:00, poll 20s)` / `V3 Step 10b PB-01 watchlist: ENABLED — SHADOW / ANALYSIS-ONLY, NO order path` (`:47-49`).
- **`pb01_watchlist` table is EMPTY (0 rows, all dates).** Root cause is **not** a miss: **no `pb01_breakout_retest` scanner has ever appeared in `webhook_audit`** → Rama has not yet saved/activated the PB-01 Chartink alert (URL was handed over 12-Jul; activation is his action). So there was nothing to capture.
- Design note for when Rama wires it: the capture worker lives inside `main`, which is up **08:15–16:00** only — the PB-01 EOD alert must fire **before 16:00** to be captured (after that the receiver is down). Record only; not today's issue.

## §2h — 18:15 FORWARD-SHADOW CRON ⏳ PENDING (check after 18:15)

- Last mark `data_store/cron_marks/forward_shadow_record.done` = **`2026-07-14 18:15:59`** (yesterday). Today's 18:15 run has **NOT fired** (report prepared ~17:45). **Rama must confirm after 18:15** that it appended today's rows (rc0, clean log, JSONL skips prior + appends 15-Jul). It was **not** triggered manually.

## §2i — EOD CRON JOBS (15:30–17:05) — mostly clean; TWO failures (non-safety)

**Ran clean today** (fresh `.done` marks / logs): db_backup, analytics_backup, backup_retention, db_retention, sentinel_retention, log_cleanup, token_cleanup, preflight a/b/c, fetch_daily_candles, capture_metrics, reconstruct_excursions, sr_detector_backfill, metrics_summary, strategy_metrics, daily_report, daily_trade_review, data_integrity, disk_monitor, control_tower.

**FAILED (both report/hygiene — no trading-path or book impact):**
- **`eod_cleanup` (15:50) — FAILED, new today.** `sqlite3.IntegrityError: FOREIGN KEY constraint failed` in `_cleanup_old_fingerprints` → `DELETE FROM signals` (`scripts/eod_cleanup.py:201`). Old dedup fingerprints were not pruned (a signal selected by the cutoff is still referenced by `trades.signal_id`). No `.done` mark; `control_tower` flagged it HIGH. Consequence = fingerprint/table bloat over time, **not** a stuck safety state (kill-switch auto-clears on boot; `in_flight` is in-memory). Recurrence could not be established from the single-run log — **flag for review** (possibly interacts with the deploy's M-S2/P10 fingerprint changes, or pre-existing).
- **`generate_screened_csv` — FAILED, PRE-EXISTING.** `ERROR: … StateStore.transaction() got an unexpected keyword argument 'readonly'` — the log shows the **identical** failure for **2026-07-13, -14, AND -15** (writes an empty header-only CSV each day). A broken report script, **not** deploy-caused. `control_tower` flagged it HIGH.

**Known pre-existing (non-safety), noted for completeness:** `eod_broker_reconcile` core result is VERIFIED, but every run also logs `capital snapshot failed: no such column: id` + `⚠️ shadow mismatch vs eod_verify` (SHADOW-mode, authoritative=0, pre-P1 — same on 10/13/14/15-Jul).

**No stale-signal/stuck-state left that matters for tomorrow's boot** beyond the un-pruned fingerprints above.

## §2j — LEDGER / GIT STATE ✅ unchanged

- **PC `main` = `321e15b`** (2 unpushed **docs-only** commits ahead of VM/bare: `d017142` Q8 forensics + `321e15b` 14-Jul reconciliation). Working tree clean at session start.
- **VM == bare `HEAD` = `2dc69d5`.** Deploy intact.
- T2 branch `854112b` still standalone/unpushed (deferred). **Nothing new outstanding.** Matches the UNPUSHED_PENDING_DEPLOY_LEDGER.

---

## Sentinels fired today (all explained, none a breach)

`data_store/critical_alert_20260715_*.flag` ×6, plus system-log CRITICAL=7 (ERROR=0):
- 10:42 AARTIIND · 15:17:12 BIRLAMONEY · 15:17:13 GROWW — **RMS/MANUAL CLOSE** reconciliation of the 3 broker-authoritative profitable closes (CHECK1). By-design.
- 15:15:00 **SOFT KILL ACTIVATED** (circuit_breaker_force_close_15:15) — the normal EOD breaker that blocks new entries and hands open positions to squareoff. By-design.
- 15:21:56 **"UNEXPECTED SSH KEY present … SHA256:BRi6…"** — **Rama's OWN Jul-13 key rotation**, already investigated in Q8 (14-Jul) as **NO breach**; recurs because the baseline wasn't re-stamped (`scripts/approve_ssh_keys.py --apply`). Known-benign.
- 17:05:01 **Control Tower "ATTENTION — 3 issues"** = the BRi6 SSH-key finding (Q8, benign) + the two cron failures above. No new content.

---

## §7 OBSERVED — filled into `deploy_behaviour_delta_prediction_14jul2026.md` (per task §2f)

| Field | Observed (15-Jul) |
|---|---|
| Session date | 2026-07-15 |
| Boot | **CLEAN** — 08:15:04, checks OK/warnings=[]; **no migration** (schema_meta 44==EXPECTED 44); kill_switch wired; no config-auditor abort |
| PREDICTED vs OBSERVED trade count | ≈ pre-deploy **HELD** — 12 rows (7 fills all closed flat, 4 FAILED, 1 REJECTED) vs 14-Jul 11 / 13-Jul 14 |
| Signals rejected (by reason) | 4,317 signals; all mapped (STRATEGY_CONTROL 1459 · SCORE ~2300 · SHADOW_INNING 323 · QUOTE_UNAVAIL 130 · CONCENTRATION 21 · OPEN_POS 20 · …) |
| Trades prevented (gate-8 / M-S5) | gate-8 SECTOR=**0** (predicted); M-S5 SHADOW_INNING=**323** on 3 symbols w/ genuine innings — IN-BASELINE (122–840), no mis-fire, not deploy-new |
| Duplicates prevented (M-S6) | **0** — dup-signal-trades empty; path dormant |
| Sector rejections | **0** (predicted) |
| Signals recovered (M-S2 re-queue) | **0** — QUEUE_FULL=0, queue never filled |
| UNEXPECTED REJECTS | **NONE** — every reason maps to §A/§B or a normal gate |
| Rollback decision + reasoning | **Not this agent's call** (read-only). No STOP trigger substantively met; the literal SHADOW_INNING>‘a couple’ threshold tripped (323) but is baseline/genuine/no-mis-fire → **recommend Web Claude + Rama RECALIBRATE the threshold.** Decision to Rama/Web Claude. |

---

## What "done" means

We can state, with evidence, exactly what happened on 15-Jul: **a valid supervised session ran
unattended, the book is flat and reconciled, and the outage broke nothing on the trading path.**
The only genuinely new imperfection is one report-hygiene cron failure (`eod_cleanup`); everything
else is expected, pre-existing, or pending (18:15 cron). No fix applied — recorded for Rama.
