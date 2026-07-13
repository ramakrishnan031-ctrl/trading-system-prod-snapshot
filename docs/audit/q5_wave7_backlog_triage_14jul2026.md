# Q5 — Wave-7 Audit Backlog: Triage + Fixes (14-Jul-2026)

Branch `q5-audit-backlog-14jul` (stacked on the Q4 tip `c1eea66`, off `main` `bb9b1e7`). Every
item was first checked **"still live at HEAD?"** — the source audit is `full_system_audit_04july2026.md`
(10 days old; several items have since been reworked). **UNPUSHED; awaiting Rama review + a deliberate
off-market deploy.**

## ✅ FIXED this session (7 items, 7 commits, all tested, zero new failures)

| Item | Commit | What | Test |
|------|--------|------|------|
| soak-report crash | `9550dfd` | `v3_shadow_soak_report` used `v.old_total`/`v.gate`; RowVerdict has `old_score`/`gate_reason` → the `--scorer` detail print crashed | verified vs `dataclasses.fields(RowVerdict)` |
| quote-noise (Rama flag) | `c22a25c` | `secondary_screener` raised+caught KeyError on a missing quote → ~249 ERROR tracebacks/day masking real errors. Now INFO one-liner for the benign no-quote; real exceptions keep ERROR+traceback | 2 tests (benign=no-ERROR, raise=ERROR-kept) |
| **M-SC2** | `522da32` | screened-stocks CSV (crond 16:01) pointed at non-existent `data/state.db` → wrote empty CSV + returned 0 (SUCCESS) every day. Fixed path + fail-loud (return 1) | 12 tests |
| **M-X2** | `a8d8164` | `sl_breach_monitor` + `breakeven_manager` swallowed emergency-notifier failures silently → a revoked token hid an emergency close. Now log at ERROR (still swallow so the action proceeds). order_placer's site was already fixed at HEAD | 35 tests |
| **M-SC3** | `501d14f` | eod_cleanup reaper filtered `status='IN_PROCESS'` (never persisted; real is `PROCESSING`) → dead. Now reaps `PROCESSING`; test encoded the bug → updated | 9 tests |
| **M-R1** | `aea9bb6` | daily report ran/Telegrammed on NSE holidays (`date_iso in holidays` vs dict-format YAML never matched; the test used string-format so it passed). Delegated to `holiday_guard` (parses both) | +dict-format test (fails pre-fix) |
| **M-R2** | `9a303ee` | daily report filtered `status=="CLOSED"` at 6 sites → dropped CLOSED_MANUAL → understated P&L on close days. Single-source `_CLOSED_STATUSES`; +CLOSED_MANUAL to the processed funnel | +Net-P&L-includes-manual test |

## ⏳ REMAINING — triaged, NOT fixed (need care / current-state verification / Rama's judgment)

Deliberately not changed unsupervised overnight — each touches a money, alert, pipeline, or
data-integrity path, or was reworked since the audit.

| Item | Still live at HEAD? | Risk | Recommended approach |
|------|--------------------|------|----------------------|
| **M-K1** `get_today_closed_pnl` keys on `DATE(updated_at)` not exit date | **LIVE** (confirmed) | Money/recon | Key on `exit_time` — BUT verify `exit_time` is always set on CLOSED/CLOSED_MANUAL first (else it under-counts). **Entangled with the failing `test_state_store::test_fix156` (got 1000 vs 1226.50)** — investigate together. Supervised. |
| **M-R3 / M-R4** daily_trade_review recon Block-4 false-FAIL / Block-2 tautology | **REWORKED** — file now has MANUAL_CLOSE / RECON_CLOSE / STUCK_EXITING closure-source logic + a reconciliation_log join | Needs a fresh read of the current reconciler to confirm whether R3/R4 still apply; likely superseded. Verify, don't assume. |
| **M-D1** candle `volume` semantics (cumulative `volume_traded` summed per tick; 0 under MODE_LTP) | **PARTIALLY** — FIX-135 Item 46 now accumulates from ticks; the cumulative-vs-delta semantic is the open question | Data-integrity; only bites if a token moves to MODE_FULL. Verify whether the tick's `volume` is a delta (sum is correct) or cumulative (sum is wrong) before touching. Risky. |
| **M-A1** Telegram HTML truncation can split an entity/`<b>` tag → 400 → alert lost | Likely LIVE (`_MSG_MAX=4096`, simple `[:n]` truncation) | Alert-reliability | Truncate at a tag/entity-safe boundary + log the 4xx body. Contained but alert-path — test with a mid-entity boundary. |
| **M-S2** webhook QUEUE_FULL poisons the dedup cache (entry written before push, not rolled back) | Needs verification of current push/dedup ordering | Pipeline/backpressure | Write the dedup entry only AFTER a successful queue push (or roll back on QUEUE_FULL). Concurrency test. |
| **M-S5** shadow re-entry guard only in `_process_one` (gate/retest paths skip it) | Needs verification | Pipeline | Hoist the guard to a shared point all three entry paths call. Careful. |
| **M-S6** RetestDiverter double-entry (exception after `register()` returns False → original also places) | Needs verification | Pipeline/double-order | Make register()'s success authoritative; a post-register exception must not fall through to placement. Concurrency test. |
| **M-K4** `time_authority.configure()` overwrites skew callbacks with None on a 2nd call | LIVE but **latent** (only 1 caller: main.py:293) | Safety (latent) | Preserve-on-None — BUT the unit tests call `configure()` with single callbacks and rely on overwrite-to-None for isolation; needs a `_reset_for_test()` + updating ~6 call sites. Design. |
| alert-watcher `--loop` + liveness heartbeat (Rama promoted from the V2 false-alarm) | N/A (enhancement) | Observability | `--once`+`Restart=always` works (it delivered the V2 sentinel) but churns restarts. Add a `--loop` mode + dead-man's-switch heartbeat; preserve fail-loud; re-run the V2 forced-failure test after. |

## Notes
- `eod_cleanup._cleanup_old_fingerprints` prunes `status IN ('EXPIRED','DUPLICATE','REJECTED')` but the
  pipeline persists `REJECTED_<check>` (not bare `REJECTED`) → REJECTED_* fingerprints may not prune.
  Bonus observation, out of the audit's M-SC3 scope — worth a follow-up.
- All 7 fixes are report/script/logging/notifier only — **no trading hot-path behaviour changed**.
