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

## 3 · GUARD — RATIFIED DESIGN (Rama + Web Claude + ChatGPT, 14-Jul) — ACCEPTANCE CRITERIA

**Decision:** *only the main trading process's boot path may EVER migrate; every other process refuses and fails loudly.* This makes reality match the mental model everyone already holds. (My earlier "refuse 09:00–15:30 for everyone" was the right instinct, wrong boundary — a blanket time-refusal would stop `main.py` from starting after a mid-session crash if a migration were pending, turning a recoverable crash into a **lost session**.)

- **AC1 — WHO MAY MIGRATE:** EXACTLY ONE — `main.py`'s boot path, via a single explicit auditable entry point: `StateStore(..., allow_migrate=True)` passed **only** at `main.py:1566`. All 23 other StateStore openers get `allow_migrate=False` **by default** → they refuse.
- **AC2 — SAFE WINDOW:** the boot path ADDITIONALLY refuses while the **market is open**. `main.py` passes `market_open=<computed>`; the guard migrates only when `allow_migrate AND NOT market_open`. A mid-session crash-restart with a pending migration therefore refuses — which is correct: it means a schema change was pushed *during market hours* (already a rule violation), and failing loudly beats silently migrating a live DB under a running market.
- **AC3 — WHEN BLOCKED: FAIL LOUDLY.** Drop a CRITICAL sentinel naming the exact pending migration (`"schema vN→vN+1 pending; this process may not migrate; it applies at the next off-market boot"`) **and** raise `MigrationNotPermitted`. **Never a silent skip. Never a degraded "run anyway" path.** (A research job silently running against an un-migrated schema is the "reports success while doing nothing" failure found three times already.) The sentinel write is best-effort (a sentinel-dir failure must never mask the raise); the raise is the primary loud signal.
- **AC4 — RECOVERY:** restart the app off-market (or wait for the 08:15 boot); the migration applies through the ONE sanctioned path. **No manual DB surgery, ever.**
- **AC5 — PRESERVE** the existing fail-fast on a NEWER-than-code DB exactly as it is (line 349; a different guard, correct as-is; it runs BEFORE this gate).

**Accepted consequence:** if a schema change is deployed and a cron (e.g. the forward-shadow recorder) opens the DB before the next boot, that job **fails loudly and skips a day**. Right trade — one loudly-missed research day beats a silent mid-session migration on the live DB.

**Implementation:** `core/state_store.py` — `StateStore.__init__(..., *, allow_migrate=False, market_open=False)`; the refuse path drops the sentinel via a **function-local** `alerts.critical` import (no import-time `core→alerts` inversion) and raises `MigrationNotPermitted`. `main.py:1566` passes `allow_migrate=True, market_open=<weekday ∧ 09:15–15:30 IST>`. **Proven:** all 24 openers refuse a pending migration; `main.py`'s boot path migrates off-market; a FORCED blocked migration drops the CRITICAL sentinel (the same discipline as the V2 forced-failure test — an untested alert path is not an alert path).

---

## 4 · COROLLARY — "read-only verification" is not read-only if the code is newer

A verification run against the **live** DB with newer code is **not** read-only — the very act of opening a `StateStore` migrates it. **Always use `--db <copy>` for verification** (which is exactly what V1–V4 did). The 13-Jul 23:41 run was a **real** run (it captured the day's 3,467 records into the append-only jsonl), so its live-DB open was legitimate — but it *is* why v43→v44 happened then. Any future "just checking the live DB" with newer code would silently migrate it.

---

*Verified against `core/state_store.py` @ `q5-audit-backlog-14jul`. RULE + INVENTORY are recorded fact; the GUARD is a PROPOSAL and is NOT implemented.*
