# SCHEMA MIGRATION-ON-OPEN — architectural rule, DB-opener inventory, guard PROPOSAL

**Date (IST):** 14-Jul-2026 · **Author:** VS Code Claude (post-power-outage resume) · **Status:** RULE + INVENTORY recorded; **GUARD = PROPOSAL ONLY — NOT implemented, awaits Web Claude + ChatGPT + Rama review.** · **Trigger:** the 13-Jul migrations did NOT fire "at boot" — they fired when a cron / research process opened the live DB.

---

## 1 · THE RULE (verified against source)

**Schema migrations execute on the FIRST process that opens the live DB with NEWER code — NOT at boot.**

`core/state_store.py::StateStore.__init__` (line 180) calls `_initialize_schema()`, which:
1. reads the on-disk `schema_version` (`_read_existing_version`);
2. **fail-fast** if the DB is *newer* than `EXPECTED_SCHEMA_VERSION` (line 349 — refuses, no downgrade);
3. if `old_version < EXPECTED_SCHEMA_VERSION` → `migrations.run_migrations(...)` (rebuilds affected tables) + `relocate_analytics_tables`;
4. then `conn.executescript(schema.sql)` (creates missing tables, re-stamps the version).

This runs **every time a `StateStore` is constructed against the live DB with newer code**. Therefore:

- **Boot is NOT the migration trigger** — the 08:15 `main.py` boot is merely the *first opener on a normal trading day*.
- **Any cron, monitor, report, or research process that constructs a `StateStore` CAN become the migration trigger** — whichever opens the DB first after a schema-changing deploy.
- The already-present guard only protects against a *newer* DB (downgrade). It does **NOT** guard *when* the migration runs.

### Empirical proof (13-Jul-2026)
| migration | when | trigger | window |
|---|---|---|---|
| v42 → v43 (`+pb01_watchlist`) | 13-Jul **19:30** | `cron_watchdog` (the 19:30 watch-the-watcher) | off-market ✓ |
| v43 → v44 (`+daily_symbol_stats`) | 13-Jul **23:41** | `forward_shadow_record` (the V4 idempotency run) | off-market ✓ |

**Neither fired at 08:15.** Both were additive + off-market, so harmless — but that was luck of timing, not design.

### THE RISK
We have been telling ourselves *"deploy the schema change off-market and it migrates at the next 08:15 boot."* **That is not what happens.** If a schema change is pushed and a **during-market** StateStore-opener runs first (see §2), the migration runs **09:00–15:30, on the live trading DB, with `main.py` running on it** — an in-place rebuild of a table the live process is reading/writing. Today's migrations were additive; a future CHECK/FK/generated-column migration (which `run_migrations` handles by **rebuilding the whole table**) would not be.

---

## 2 · INVENTORY — what opens `data_store/trading_system.db`

### A. MIGRATION TRIGGERS — read-WRITE via `StateStore._initialize_schema` (these CAN migrate)
The live process + 23 scripts construct `StateStore`:

**Live:** `main.py` (08:15 Mon-Fri boot → runs to 16:00).

**Off-market crons (safe timing):** `cron_watchdog` (19:30) · `db_retention` (02:30, +`--vacuum` 02:30) · `eod_broker_reconcile` (15:58) · `eod_cleanup` · `eod_verify` · `reconcile_pnl` · `reconcile_positions` (15:45) · `reconstruct_excursions` (15:50) · `sr_detector_backfill` (15:58) · `compute_strategy_metrics` · `fetch_fno_ban` (08:35) · `forward_shadow_record` (18:15) · `system_manager` · `wal_checkpoint` · `db_retention` · `generate_screened_stocks_csv` · `trade_journal` · `replay_signals` (ad-hoc) · `check_cron_drift` · `clear_kill_switch` (ad-hoc/operator) · `premarket_healthcheck` (08:xx).

**⚠️ DURING-MARKET StateStore openers (the realistic mid-session trigger):**
- **`cron_officer.py`** — runs **09:20** (5 min after open) **and hourly** → opens the live DB read-write during the session. **This is the most likely mid-session migration trigger.**
- **reports** (`daily_report.py`, `daily_trade_review.py`) — normally EOD, but a manual/ad-hoc mid-day report would open the live DB and migrate.

### B. NOT migration triggers (they do NOT run `_initialize_schema`)
- **GUI `ops_dashboard`** — opens `file:<db>?mode=ro` (read-only, `uri=True`). A read-only connection **cannot** `executescript` → it can never migrate (it would read the old schema or fail a query, never rebuild). ✓
- **`capture_metrics_baseline.py`** (`*/5 09-15 Mon-Fri`, every 5 min during market) — opens via raw `core.db_connect`, **not** `StateStore` → no `_initialize_schema` → does **not** migrate. ✓ (It writes metrics to whatever schema is present; it never rebuilds.)
- **`db_backup`** (01:00) — `sqlite3 … ".backup …"` CLI → no migration. ✓

> The frequent during-market openers (GUI, capture_metrics) happen to be migration-safe **by mechanism, not by intent**. The one that is NOT safe is `cron_officer` (StateStore, 09:20/hourly).

### "First opener after a push" reasoning
- **Push in the evening (19:30–02:30):** next StateStore opener is typically `db_retention` (02:30) or the 08:15 boot → **off-market, safe.**
- **Push overnight (02:30–08:15):** first opener is the **08:15 boot** → safe.
- **Push DURING market hours (violates the off-market-push rule):** `cron_officer` (hourly) or a report could open+migrate mid-session while `main.py` runs on the DB → **UNSAFE.** The off-market-push guardrail is the *only* thing preventing this today — it is a convention, not an enforced invariant.

---

## 3 · PROPOSAL (do NOT implement — review required, touches every DB-opening process)

**Goal:** make "never migrate the live DB mid-session" an *enforced* invariant, not a convention.

### Option 1 (recommended) — market-hours refusal in `_initialize_schema`
Before running `run_migrations`, if a migration is needed **and** `now_ist()` is within the trading window (e.g. 09:00–15:30 on a market day) **and** this is the live DB (not a `--db` copy): **refuse and fail LOUDLY** (raise + CRITICAL sentinel → the alert chain), rather than migrate.
- **Pro:** a single choke-point (every StateStore goes through `_initialize_schema`); protects even if the off-market-push rule is violated.
- **Con / trade-off to decide:** a mid-session `main.py` crash-restart with a pending migration would be **blocked** → trading stays down until an off-market migration. That is arguably *correct* (a silent mid-session rebuild of the live trades table is worse), but it is a policy call. Mitigation: the 08:15 boot is before 09:00, so a normal day is unaffected; only a *pending schema change* + a *mid-session restart* hits this.

### Option 2 — explicit opt-in for non-boot processes
Require `--allow-migrate` (or an env flag) for any process other than `main.py` to run a migration; without it, a non-boot process that finds `old_version < EXPECTED` **refuses + alerts** instead of migrating. Boot migrates; crons/reports/research never silently do.
- **Pro:** the trigger becomes explicit and auditable.
- **Con:** touches ~24 call sites; a forgotten flag on a legitimately-first off-market cron blocks a valid migration.

### Option 3 (belt) — a deploy-time preflight
`deploy_preflight` already refuses a mid-session *deploy*. Extend it to detect a **schema bump** in the push and refuse unless the window is off-market AND to name which process will open first. Does not protect against a push that bypasses preflight, so pairs with Option 1/2, not a replacement.

**Recommendation:** Option 1 as the enforced backstop (single choke-point, fail-loud) + Option 3 as the deploy-time nudge. Option 2 is the most invasive and most easily mis-configured. **All three are PROPOSALS — PAUSED pending review.**

---

## 4 · COROLLARY — "read-only verification" is not read-only if the code is newer

A verification run against the **live** DB with newer code is **not** read-only — the very act of opening a `StateStore` migrates it. **Always use `--db <copy>` for verification** (which is exactly what V1–V4 did). The 13-Jul 23:41 run was a **real** run (it captured the day's 3,467 records into the append-only jsonl), so its live-DB open was legitimate — but it *is* why v43→v44 happened then. Any future "just checking the live DB" with newer code would silently migrate it.

---

*Verified against `core/state_store.py` @ `q5-audit-backlog-14jul`. RULE + INVENTORY are recorded fact; the GUARD is a PROPOSAL and is NOT implemented.*
