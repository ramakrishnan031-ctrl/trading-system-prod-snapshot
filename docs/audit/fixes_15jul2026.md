# 15-Jul-2026 Fixes — eod_cleanup (FK/retention) · generate_screened_csv (M-SC2b) · fm_ledger

**Phase A (this doc): implemented + tested + backup-validated. NOT pushed, NOT deployed, NO live
prune run.** Three root-cause fixes, each its own commit with a test that fails on old code and
passes on new; no schema change; each on a single mode-agnostic path (paper==live). Off-market.
Branch `fixes-eodcleanup-screened-fmledger-15jul` off `main` `5c5e1fb`; commits **`3907a5c`**
(Fix 1) → **`d3499b9`** (Fix 2) → **`561d281`** (Fix 3). PC `main` unchanged (`5c5e1fb`, docs);
VM==bare `2dc69d5`. **Phase B (first live backlog prune) is a supervised post-push step — runbook at the end.**

---

## Fix 1 — eod_cleanup FK / retention (`3907a5c`)

**Root cause.** Step-4 `_cleanup_old_fingerprints` ran a bare `DELETE FROM signals WHERE (status IN
('EXPIRED','DUPLICATE') OR status GLOB 'REJECTED*') AND fingerprint_date < (today-7)`. P10 (`2e61fad`)
broadened bare `'REJECTED'` → `GLOB 'REJECTED*'`; those signals are FK-referenced by `screener_results`
(94,436 refs) and `StateStore` forces `PRAGMA foreign_keys=ON`, so the whole DELETE rolled back every
15:50 run. **Empirically reproduced**: the old DELETE raises `sqlite3.IntegrityError: FOREIGN KEY
constraint failed` when a matching signal has a `screener_results` child.

**What changed** (`scripts/eod_cleanup.py`, `core/config_loader.py`, `config/system_config.yaml`):
- **Capital-safety invariant (primary guard):** the prune set now excludes any signal with a `trades`
  child — `AND NOT EXISTS (SELECT 1 FROM trades t WHERE t.signal_id = signals.signal_id)` — so a hygiene
  job can never delete capital/P&L-linked history.
- **FK-safe children-first cascade:** for each batch, DELETE the 5 analytics children
  (`screener_results, gate_state, shadow_trades, sr_detector_results, retest_state`) then the parent
  `signals` (`trades` is never in the set → never touched); beyond-window = DROP (no archive).
- **Batched:** 2,000 signal_ids/transaction, COMMIT between chunks, loop until empty (`foreign_keys` ON
  throughout) — bounds transaction size for the 108k backlog and daily runs.
- **Config-driven retention:** new `system.eod_cleanup.signal_retention_days` (default **90**, `ge=1`);
  the prune reads it via `load_all()` (config-load failure → same model default, never a bare literal).
  CLI `--signal-retention-days` overrides (one-time backlog clear / tests); `--fingerprint-days` kept as
  a backward-compat alias. The hardcoded 7-day default is gone. EXPIRED/DUPLICATE handling is
  behaviourally intact (same status filter, now FK-safe).

**Tests** (`tests/unit/test_fix135_eod_cleanup.py`): new `TestFingerprintPruneFkSafe` —
`test_rejected_with_screener_child_pruned_fk_clean` (REJECTED_* + screener child pruned together, FK
check clean; ERRORS on pre-fix code = the FK rollback), `test_trade_linked_signal_never_pruned`
(capital-safety: a signal with a trades child is never deleted even with a REJECTED_* status),
`test_batched_prune_clears_backlog_over_one_batch` (>2,000 rows clear). Existing `TestFingerprintPrune`
updated to pass `signal_retention_days=7` (default is now 90). **Result: 14 eod_cleanup + 42
config_loader pass; broader sweep 118 pass.**

**Regression map.** Only caller of `run_eod_cleanup` is eod_cleanup's own `main()`; the cron runs the
script with no args (so it now reads config). No other code referenced the renamed param / CLI arg
(grep-clean besides the alias). Single path (parity — no mode branch).

**Backup validation (against a `.backup` COPY of the live DB; live untouched, copy deleted after):**

| Table | BEFORE | AFTER (retention=7) |
|---|---|---|
| signals | 146,000 | **37,757** (−108,243) |
| trades | **355** | **355 (UNCHANGED)** |
| screener_results | 122,453 | **28,017** (−94,436, children-first) |
| sr_detector_results | 174 | 174 |
| gate_state / shadow_trades / retest_state | 0 | 0 |
| signals with a trades child | 355 | **355 (UNCHANGED)** |

- `run_eod_cleanup(..., signal_retention_days=7)` → `fingerprints_pruned = 108,243`.
- **`PRAGMA foreign_key_check` = [] (clean)** after pruning 108,243 signals + 94,436 screener children.
- **trades UNCHANGED (355→355)** and **no signal with a trades child removed (355→355)** — capital guard held.
- REJECTED_* old signals WITH a trades child = **0** on real data (the guard is defense-in-depth; the
  unit test proves it with an adversarial construction).

> **⚠️ RETENTION NUANCE (must read before Phase B).** The live data starts **2026-06-12** (~33 days).
> With the **default 90-day** retention, eligible-for-prune **= 0** right now (nothing is older than 90
> days) — the first automated 15:50 run clears **nothing**, and the table keeps growing (~4.3k/session)
> until the oldest data ages past 90 days (≈10-Sep). The 108k backlog only becomes eligible at a
> **shorter** window (@7d = 108,243; @30d would be less). So: 90d is the safe steady-state policy, but to
> **reclaim the 108k noise NOW** Rama must run the one-time clear with an explicit lower window
> (`--signal-retention-days 7` or `30`). This is a config/ops decision, not a code change (the window is tunable).

**Rollback:** revert `3907a5c` (schema-free); the prune returns to the pre-fix bare DELETE (rolls back
on FK). No data is lost by reverting (the prune only ever deleted rows). No restart; the cron picks up
the reverted script on the next 15:50 run.

---

## Fix 2 — generate_screened_csv / M-SC2b (`d3499b9`)

**Root cause.** `get_traded_symbols`/`get_non_traded_symbols` called `store.transaction(readonly=True)`,
but `StateStore.transaction()` has no `readonly` parameter → `TypeError` every 16:01 run → header-only
empty CSV. The bad kwarg was introduced by **FIX-039 (`1b7225c`, 14-May)** and is **separate from + older
than** the M-SC2 DB-path fix (`522da32`) — proven at runtime (today's run used the correct DB path and
still failed on the kwarg).

**What changed** (`core/db_connect.py`, `scripts/generate_screened_stocks_csv.py`): new dedicated
**`db_connect.connect_readonly`** — a URI `mode=ro` connection + `PRAGMA query_only=ON` that is
STRUCTURALLY unable to write/migrate and does NOT go through the migrating `StateStore` init. Both reads
route through it; the migrating `StateStore(db_path=...)` open is removed from the reporting path. Per
the design, the shared `transaction()` is NOT extended (keeps migration-capable vs immutable-read paths
separate). No schema change.

**Tests** (`tests/unit/test_generate_screened_csv_readonly.py`): `test_connect_readonly_rejects_writes`
(INSERT + DELETE both raise `OperationalError`), `test_screened_reads_work_via_readonly` (the two queries
return seeded data, no TypeError), `test_full_read_to_csv_is_populated` (read→`generate_csv` produces a
POPULATED CSV, not the empty header-only pre-fix output). **Result: 3 pass.**

**Regression map.** The two call sites only READ; the CSV columns/rows are unchanged; the other
`_get_traded_symbols` in fetch_daily_candles/gemini are unrelated functions. Single path (parity).

**Register re-grade:** **M-SC2 CLOSED → PARTIAL** (its named DB-path defect was genuinely fixed by
522da32; the report's outcome was still broken by a distinct older bug). Residual **M-SC2b closed here**.

**Rollback:** revert `d3499b9` (schema-free; `connect_readonly` is additive with this script its only
caller). Script returns to the TypeError path (empty CSV), no data risk (read-only). No restart.

---

## Fix 3 — fm_ledger capital snapshot `id` → `ledger_id` (`561d281`)

**Root cause.** `_local_capital_snapshot` ran `SELECT balance_after FROM fm_ledger ORDER BY id DESC LIMIT
1`, but fm_ledger's PK is `ledger_id` (no `id` column) → `no such column: id` → caught → silent
`(True, 0.0)` (wrong capital total; logged daily as "capital snapshot failed: no such column: id").
**Empirically reproduced**: old query raises `OperationalError`; fixed query returns the real latest
balance (9876.5).

**What changed** (`scripts/eod_broker_reconcile.py`): `ORDER BY id` → `ORDER BY ledger_id` at the one call
site (`ledger_id` is the autoincrement PK → DESC LIMIT 1 = latest row). No existing "latest balance"
helper to reuse (StateStore's ledger reader is the opening/INIT-row query, different semantics). No schema change.

**Tests** (`tests/unit/test_eod_broker_reconcile.py::TestLocalCapitalSnapshot`):
`test_returns_latest_ledger_balance` (two rows → returns latest 9876.5, not 0.0; fails on old),
`test_empty_ledger_returns_zero` (empty → 0.0 unchanged). **Result: 17 reconcile tests pass.**

**Regression map.** `local.total` feeds ONLY the margin dimension (`compute_verdict` lines 174-179), which
is inactive in the current shadow run (margin=NOT_CHECKED). So correcting 0.0 → the real value changes
**no current verdict**; it fixes a latent input for when margin-checking becomes authoritative and stops
the daily WARNING. `invariant_ok` is unaffected. Single path (parity).

**Rollback:** revert `561d281` → the silent 0.0 + daily WARNING return; no verdict change (margin
NOT_CHECKED in shadow); no data risk. No restart.

---

## Forward-shadow idempotency (ChatGPT Q7) — CLEAN, flag CLOSED

The 18:15 cron re-processed 14-Jul (`wrote=1644`) alongside 15-Jul (`wrote=2535`). Read-only check of
`data_store/v3/forward_shadow_fs-v1.jsonl`: per-date rows 13-Jul **3467** / 14-Jul **1644** / 15-Jul
**2535**; **distinct `(date, signal_id)` = 7,646 = total rows → 0 duplicate pairs.** Duplication is
**impossible by design**: `scripts/forward_shadow_record.py:151-160` reads the existing JSONL, builds a
`seen` set of signal_ids already present for the date, and filters them out before append (docstring:
"Idempotent: a signal already present in the JSONL for the date is skipped"). The 15-Jul "wrote=1644 for
14-Jul" = 14-Jul signal_ids not yet present (the 14-Jul run wrote fewer; the 15-Jul run backfilled the
rest, deduped). **No D2/D3 pollution; flag closed — no separate defect.**

---

## PHASE B — FIRST LIVE PRUNE RUNBOOK (Rama, off-market, supervised, AFTER pushing this branch)

**Goal:** clear the ~108k signal backlog on the live DB once, supervised. Subsequent automated 15:50
runs then handle the small daily increment per the config window.

0. **Deploy** this branch (merge to main, push, off-market). The new `eod_cleanup.py` + config land; no
   restart needed for the cron (it re-reads the script each run). Schema is unchanged (v44).
1. **Fresh live backup immediately before:** `sqlite3 data_store/trading_system.db ".backup data_store/backups/pre_prune_15jul.db"` (or the standard `db_backup`).
2. **Decide the one-time-clear window.** With the config default **90 days**, the prune clears **0** now
   (the 108k are <90 days old — see the retention nuance above). To reclaim the backlog now, run with an
   explicit lower window, e.g.:
   `. .env && PYTHONPATH=. venv/bin/python scripts/eod_cleanup.py --signal-retention-days 7`
   (7 clears all 108,243; use `30` for a more conservative one-time clear — your call). Trigger it
   deliberately off-market; do NOT wait for the automated 15:50 run (which uses the 90d default → 0).
3. It runs **BATCHED** (2,000/tx). Capture before/after: `SELECT COUNT(*) FROM signals;`,
   `SELECT COUNT(*) FROM screener_results;`, `SELECT COUNT(*) FROM trades;` (backup-copy expectation:
   signals 146,000→37,757, screener_results 122,453→28,017, **trades 355→355 UNCHANGED**).
4. **`PRAGMA foreign_key_check;` afterward → must be empty (clean).**
5. **Confirm `trades` count UNCHANGED** (capital/P&L history untouched).
6. After this one supervised backlog-clear, leave `signal_retention_days: 90` as the steady-state policy
   (bounds growth once data ages past 90 days). Adjust the config value if you want a tighter steady state.

**Rollback of the whole batch:** revert the three fix commits (schema-free, no data loss — the prune only
ever deletes noise rows; a revert stops it). `signal_retention_days` in system_config.yaml is additive.

---

## Deliverable status

3 root-cause fixes committed (not pushed), each with a fail-on-old/pass-on-new test; Fix 1 validated on a
DB copy (108,243 cleared, FK clean, trades untouched, children-first, retention nuance flagged);
forward-shadow idempotency answered (clean, closed); Phase B runbook written. The destructive live prune
remains a supervised post-push step.
