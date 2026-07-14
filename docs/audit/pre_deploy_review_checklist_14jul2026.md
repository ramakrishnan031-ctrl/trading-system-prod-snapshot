# PRE-DEPLOY REVIEW CHECKLIST — Q4 + Q5 + P11 bundle (14-Jul-2026)

**W1 deliverable.** Reviewer-mode read of the COMPLETE net diff (`main..HEAD`), as the LAST GATE
before real behaviour changes reach real capital. Reviewed as a critic, not the author.

- **Branch:** `q5-audit-backlog-14jul` — **31 commits** vs `main` `bb9b1e7` (deployed VM/bare HEAD).
- **What deploys:** the 27 Q4/Q5/P11 commits + 4 added 14-Jul: `5094633` behaviour-delta prediction
  (docs), `23ddd29` M-R4 delete (report-path), `4fe9188` Q7 wired tests (test-only), `f559059` Q9 GUI
  report (docs). **Of the 4, only M-R4 touches product code, and it is report-path (daily_trade_review),
  not the trading hot path.**
- **Surface:** 48 files, +2453/−164. Product code: 15 files. Tests: 18 files. Docs: 8. Scripts: 5.

---

## FILES REVIEWED (net diff `main..HEAD`, by risk)

**Hot-path / money / order / reservation / kill / migration — DEEP review:**
`capital/risk_engine.py` (gate-8) · `core/state_store.py` (migration guard + M-K1 + sector_exposure) ·
`main.py` (migration wiring + Q4b + Q4c) · `utils/startup_checks.py` (Q4b) · `core/config_auditor.py`
(Q4c/A4) · `signals/signal_processor.py` (M-S5) · `signals/webhook_receiver.py` (M-S2) ·
`screening/retest_monitor.py` (M-S6) · `orders/breakeven_manager.py` + `orders/sl_breach_monitor.py`
(M-X2) · `core/time_authority.py` (M-K4) · `screening/secondary_screener.py` (quote-noise).

**Report / script / alert — reviewed:** `reports/daily_report.py` (M-R1/M-R2) ·
`reports/daily_trade_review.py` (M-R3 + M-R4) · `scripts/eod_cleanup.py` (M-SC3/P10) ·
`scripts/generate_screened_stocks_csv.py` (M-SC2) · `scripts/alert_watcher.py` (P5) ·
`alerts/telegram_notifier.py` (M-A1) · `core/config_loader.py` · `scripts/v3_shadow_soak_report.py`.

**Tests — reviewed for the "cannot-fail" anti-pattern:** the two most safety-critical in full
(`test_migration_guard.py`, `test_gate8_sector_toctou.py`) + `test_hardening_scenarios.py` (mine);
the rest confirmed green + RED/GREEN-built.

**Docs:** SYSTEM_MAP, PATHS, docs/audit/* — narrative only, no runtime effect.

---

## THE SPECIFIC CHECKS THE WORK ORDER DEMANDED

1. **Anything that LOOSENS a limit → NONE.** Every behaviour change hardens (stricter / more correct)
   or is report/log-only. Verified case by case below.
2. **gate-8 genuinely `max()`-floored IN THE FINAL CODE (not just the design note) → ✓ CONFIRMED.**
   `RiskEngine._effective_sector_margin` returns `max(existing_sector_margin, open_partial + reserved)`
   (`risk_engine.py`). It can ONLY harden. Both degradation paths (fund_manager lacks
   `get_live_reservations`, or the read raises) return `existing_sector_margin` — the OLD DB-truth
   value, i.e. never looser than before — and log LOUD (never silently). The reporting `sector_pct`
   snapshot deliberately keeps DB truth; only the gate consumes the hardened value.
3. **Migration guard — all non-boot openers default `allow_migrate=False`; nothing passes True except
   main.py boot → ✓ CONFIRMED.** `StateStore.__init__(*, allow_migrate=False, market_open=False)` —
   keyword-only, default False. `grep allow_migrate` over the repo: the ONLY `=True` is `main.py:1580`
   (the boot path), gated `if not (allow_migrate and not market_open): _refuse_migration()`. All 23
   other StateStore call-sites (reports/scripts/utils) construct positionally → False → refuse a
   pending migration + drop a CRITICAL sentinel. On THIS deploy nothing refuses (v44==v44, no pending
   migration). The refusal touches nothing in the DB (called before `run_migrations`); the sentinel
   write is best-effort inside try/except with the `raise` OUTSIDE it, so a sentinel failure can never
   mask the refusal.
4. **ORDER / RESERVATION / KILL changes not covered by tests → NONE uncovered.** gate-8 (reservation
   fold-in) has `test_gate8_sector_toctou` (real FundManager/StateStore/RiskEngine, RED-on-old bracket)
   + a wired integration test. M-S6 (retest double-order) has `test_sr_v2_divert` incl. a 24-thread
   concurrency proof. M-X2 (emergency-notifier) leaves the CLOSE path untouched (separate try-block) —
   verified the notifier failure cannot block the emergency close. Q4b (kill_switch) has
   `test_startup_checks`.
5. **ANY TEST THAT CANNOT FAIL → NONE found (incl. my own).**
   - `test_migration_guard` — stamps an older version, asserts `MigrationNotPermitted` raised AND the
     raw sqlite version stays un-migrated; the AC3 test FORCES the block and verifies a real sentinel
     file with the version strings. RED on old code (which silently migrates). Model test.
   - `test_gate8_sector_toctou::test_full_approve_rejects_when_reservation_pushes_over_cap` — sets the
     limit STRICTLY BETWEEN the old and new projections, asserts the old gate would pass, then asserts
     the new code rejects. Explicitly "RED against pre-fix code." Real collaborators (a mock could get
     `.symbol`/`.margin` field names wrong and still pass — the docstring calls this out).
   - My Q7 tests — each is falsifiable by construction: gate-8 (asserts reject WITH reservation / pass
     WITHOUT), M-S5 (asserts `REJECTED_SHADOW_INNING_ACTIVE` + nothing placed), M-S2 (asserts retry
     ACCEPTED not DUPLICATE). All RED on pre-fix code.
   - M-R4 (my delete) — replaced the tautology assertion with a guard that NO block's identity contains
     `placement_failed` (survives a renumber; catches re-introduction by intent).
6. **Leftover debug / scratch / hardcoded paths / stray TODOs → NONE.** The one hardcoded-path CHANGE
   is M-SC2 fixing a path that never existed (`data/state.db` → `data_store/trading_system.db`); it is
   the fix, not a leftover. No `print(`-debug, no commented-out code, no TODO that should not ship.

---

## PER-FIX VERDICT (product code)

| Fix | File | Verdict | Note |
|---|---|---|---|
| gate-8 sector TOCTOU | risk_engine, state_store | ✅ SAFE | max()-floored; only hardens; fail-safe degrade to DB-truth |
| Migration guard (P11) | state_store, main | ✅ SAFE | default-False; only boot-off-market migrates; loud refuse + sentinel |
| Q4b kill_switch fail-fast | main, startup_checks | ✅ SAFE | live+None raises → boot abort (return 3, store.close()); runtime warn+skip unchanged |
| Q4c structure∩trailing guard | main, config_auditor | ✅ SAFE | `is True` gated (MagicMock-proof); A4 code matches filter; off-by-default (structure_exit off) |
| M-K1 closed-pnl date key | state_store | ✅ SAFE | IST substr key; +CLOSED_MANUAL; report/recon only; landmine comment |
| M-S5 shadow-inning hoist | signal_processor | ✅ SAFE | guard is no-op when tracker None; gate path enforced; retest path dormant |
| M-S6 divert double-order | retest_monitor | ✅ SAFE | dormant in prod (wait_for_retest=false); register()-authoritative; 24-thread test |
| M-S2 QUEUE_FULL dedup | webhook_receiver | ✅ SAFE | backpressure-only; recovers a dropped signal, never removes one |
| M-K4 skew-callback preserve | time_authority | ✅ SAFE | `_UNSET` sentinel; 1 caller; latent fix; delta 0 |
| M-X2 emergency-notifier log | breakeven/sl_breach | ✅ SAFE | logs loud, still swallows; CLOSE path untouched (separate try) |
| quote-noise | secondary_screener | ✅ SAFE | outcome-preserving (no-quote still skips); INFO not ERROR-traceback |
| M-R1 holiday guard | daily_report | ✅ SAFE | delegates to holiday_guard (parses dict+string); report skips holidays correctly |
| M-R2 CLOSED_MANUAL P&L | daily_report | ✅ SAFE | single `_CLOSED_STATUSES`; report-only |
| M-R3 recon close-date key | daily_trade_review | ✅ SAFE | report/recon; dormant intraday-only |
| M-R4 tautology delete | daily_trade_review | ✅ SAFE | removes a false-PASS; verdict math unchanged |
| M-SC2 CSV path | generate_screened_stocks_csv | ✅ SAFE | real DB path; returns 1 on real failure; composes w/ migration guard |
| M-SC3/P10 eod reaper | eod_cleanup | ✅ SAFE | PROCESSING (real status); prior-day stale only; EOD cron |
| M-A1 telegram truncation | telegram_notifier | ✅ SAFE | HTML-boundary-safe cut; 4xx body logged; alert-path only |
| P5 alert_watcher --loop | alert_watcher, config_loader | ✅ SAFE | opt-in (default --once); heartbeat off by default; persistent-fault stops loop |

---

## ZERO-NEW-FAILURES EVIDENCE

- **Touched suites (every suite whose source changed) on HEAD: 723 passed, 0 failed.** Zero failures →
  trivially zero NEW failures in the changed areas.
- **Whole-suite `--collect-only`: 4694 tests import cleanly, exit 0** — no import-level break from any
  of the 31 changes.
- **`test_main.py` (highest-risk untouched suite — main.py changed heavily): 4 failed on HEAD.** The 3
  `TestContinueFromGate` + 1 `TestBl15WebhookSecretRequired` are **PRE-EXISTING** — a fresh `git
  worktree` of `main` (`bb9b1e7`) fails the SAME 4 identically. Root cause is a mock-harness gap (place
  not reached), NOT this branch: `SignalProcessor` defaults `shadow_tracker=None`, so M-S5's hoisted
  guard is a no-op in these tests (the one line my change adds to `continue_from_gate` returns
  immediately). These 4 are the documented PC-env failures (green on the VM).
- **Full unit-suite HEAD-vs-main diff (definitive):** pre-fix HEAD = **11 failed / 4614 passed / 15
  skipped**. Of the 11: **10 are PRE-EXISTING** (4 `test_main` + 4 `test_order_placer_fix061` + 1
  `test_phase17_batch2::test_fix077_flask` + 1 `test_fix181::...inflight_orphan`), each confirmed to
  fail IDENTICALLY on a clean `git checkout main` (same environment, runtime files present) — the
  documented Windows PC-env baseline, green on the VM. **1 was NEW** (`test_v34_to_v35_columns_added`)
  and is now FIXED (see Concerns). **Post-fix: 10 failed / 0 new.**

---

## CONCERNS FOUND

**CONCERN #1 — FOUND + FIXED (the review's real catch):** the migration guard (P11) broke
`test_slice1_rr_aftercheck::test_v34_to_v35_columns_added`, which reopens a version-rewound DB with a
plain `StateStore(db)` expecting the migration to run. The guard now (correctly) refuses a non-boot
migration → `MigrationNotPermitted`. The guard commit updated `test_migrations` +
`test_schema_version_failfast` but **missed this migration-exercising test** — it passed on `main`,
failed on the branch = a genuine new failure. **This is a test-maintenance gap, NOT a production bug**
(prod is v44==v44, so the guard never refuses; a precise grep confirms this was the only stamp-old-
then-reopen-plain test not already updated). **FIX (`ee70f40`):** the test declares migration intent
(`allow_migrate=True, market_open=False`) exactly like `main.py`'s boot and the other migration tests;
now 24/24 green. Post-fix full-suite: 0 new failures.

**CONCERN #2 — INVESTIGATED + CLEARED (chased, not assumed):** the 3 `TestContinueFromGate` failures
sit on the exact M-S5 hot path and initially read as a possible regression from hoisting the
shadow-inning guard. Chased to ground: `shadow_tracker` defaults to None in the test → the guard is a
no-op → the failures reproduce identically on `main`. **Not a regression.** No code change warranted.

**No remaining blocking concerns.** No loosening, no uncovered order/reservation/kill change, no
cannot-fail test, no leftover debug/scratch/hardcoded/TODO. The one real defect the review found (the
migration-guard test gap) is fixed. This is exactly what a pre-deploy review is for — done NOW, not at
18:30.

---

## FINAL RECOMMENDATION

# ✅ SAFE TO DEPLOY

Deploy as ONE atomic off-market unit on Rama's explicit GO. Every change hardens or is report/log/test/
docs only; the trading hot path is either hardened (gate-8, M-S5, M-S2) or untouched. The behaviour
deltas are quantified in `deploy_behaviour_delta_prediction_14jul2026.md` — session-1 should test that
prediction. The empirical count-confirmation queries (prediction §D) and the 18:15 forward-shadow gate
(W4) remain off-market pre-deploy steps.
