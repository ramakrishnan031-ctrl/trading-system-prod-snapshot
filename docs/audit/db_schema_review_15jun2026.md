# DB Schema Review — Trading System v2 (schema v24)

**Reviewer:** Claude (Opus 4.6, read-only)
**Date:** 15 Jun 2026
**Scope:** `core/schema.sql` (single source of truth, 29 tables + sqlite_sequence), connection PRAGMAs in `core/state_store.py`
**Method:** Read-only. No code changes made.

---

## Overall Verdict

**Well-disciplined, mature schema.** Single source-of-truth file, explicit version + migration history, append-only audit ledgers, single-row CHECK guards, and a documented FK convention. Issues below are refinements, not defects — nothing blocks Monday live start.

---

## Table Domain Map (logical grouping)

```
┌─ CORE TRADE LIFECYCLE ──────────────────────────────────┐
│  signals ──1:N──> trades ──1:N──> orders                │
│     │                │              └─ superseded_by     │
│     └── trade_id (back-ref, set post-fill)              │
├─ CAPITAL ───────────────────────────────────────────────┤
│  capital_snapshot (1 row)   fm_ledger (append-only WAL) │
├─ CONTROL / SESSION STATE ───────────────────────────────┤
│  schema_meta  session(1)  kill_switch_state(1)          │
│  system_events                                          │
├─ SCREENING / GATE ──────────────────────────────────────┤
│  screener_results   gate_state                          │
├─ TRAILING / SHADOW ─────────────────────────────────────┤
│  smart_tgt_state   innings   shadow_trades              │
├─ MARKET DATA ───────────────────────────────────────────┤
│  candles   trade_excursions                             │
├─ RECONCILIATION ────────────────────────────────────────┤
│  reconciliation_log  pnl_reconciliation                 │
│  position_reconciliation  eod_squareoff_log             │
│  eod_verification                                       │
├─ OPS / ANALYTICS / AUDIT ───────────────────────────────┤
│  webhook_audit  telegram_alerts  cron_heartbeat         │
│  system_metrics  system_metrics_daily                   │
│  trade_journal  strategy_metrics  fno_ban               │
└─────────────────────────────────────────────────────────┘
```

Reference data (`instruments`, `nse_holidays`) lives **outside** the DB as CSV/YAML — not in schema.sql.

---

## Strengths (keep these)

- **Versioned single-source schema** with full migration changelog (v1→v24) in footer.
- **Robust PRAGMAs:** WAL + `synchronous=FULL` (crash-safe) + `foreign_keys=ON` + 30s `busy_timeout`.
- **Write-ahead capital ledger** (`fm_ledger`), append-only, with `entry_type` CHECK enum — bad writes caught at INSERT boundary.
- **Single-row guards** via `CHECK (id = 1)` on snapshots/state tables.
- **Order replacement chain** (`superseded_by` self-FK) preserves full trail history without data loss.
- **Forensic columns** retained (`webhook_payload`, `market_data_snapshot`, cost breakdown cols).
- Good index coverage on hot read paths (status, symbol, ts, trade_id).

---

## Observations and Suggestions

### O1 — FK declarations are inconsistent (defense-in-depth gap)

> **STATUS: FIXED (FIX-173).** Added FKs to `screener_results.signal_id`, `smart_tgt_state.trade_id`, `innings.trade_id`, `shadow_trades.signal_id`+`live_trade_id`, `reconciliation_log.trade_id`. Schema v25→v26 via the table-rebuild runner; `foreign_key_check` clean on the live DB. `reconciliation_log.trade_id` is nullable and its insert site is wrapped in try/except (audit-log semantics — a rare orphan rejection drops one row with an error log, never crashes the reconciler).

Only a handful of child tables declare actual FK constraints (`trades`, `orders`, `trade_excursions`, `trade_journal`, `gate_state`). Many cross-table references are plain TEXT without FK declarations:

| Table | Column | References |
|-------|--------|-----------|
| `screener_results` | `signal_id` | `signals.id` |
| `smart_tgt_state` | `trade_id` | `trades.id` |
| `shadow_trades` | `signal_id`, `live_trade_id` | `signals.id`, `trades.id` |
| `reconciliation_log` | `trade_id` | `trades.id` |
| `innings` | `trade_id` | `trades.id` |

Since `foreign_keys=ON`, protection is uneven — declared FKs are enforced, undeclared ones are not.

**Suggestion:** Declare FKs uniformly on all cross-table references, or document why a reference is intentionally soft (as already done for `reservation_id`).

---

### O2 — Status/enum columns are unconstrained TEXT

> **STATUS: FIXED (FIX-172).** Added CHECK constraints to `kill_switch_state.state`, `orders.status`, `orders.leg`, `trades.status`, `signals.status`. Value lists were derived from the live code (the draft lists in this doc were incomplete/wrong — e.g. omitted `orders.leg='CO'` which would have broken live CO orders, and treated `signals.status` as closed when it is an open set written by 4 modules). `signals.status` uses GLOB prefix families (`REJECTED*`/`DROPPED_*`/`SKIPPED_*`/`GATE_*`) + enumerated stable values — a shape/typo guard, not a closed enum. Applied to existing DBs via the new idempotent table-rebuild runner in `core/migrations.py` (schema v24→v25). Verified against the full unit suite + live DB (`integrity_check: ok`, `foreign_key_check: []`).

`trades.status`, `orders.status`, `kill_switch_state.state`, every `direction` column, reconciliation `status` columns — all free TEXT. Only `fm_ledger.entry_type` has a CHECK enum.

A bad write (typo, wrong constant) will silently insert invalid state; application logic catches it only at read time.

**Suggestion:** Add `CHECK (status IN (...))` to key status columns, mirroring the `fm_ledger.entry_type` pattern already in use.

---

### O3 — Missing indexes on frequently joined columns

> **STATUS: FIXED (FIX-171).** Added `idx_signals_trade_id`, `idx_trades_signal_id`, and partial `idx_orders_superseded_by` to `core/schema.sql` (idempotent, apply on every startup) and to the live VM DB directly.

These columns appear in joins/lookups but lack indexes:

| Table | Column | Used in |
|-------|--------|---------|
| `trades` | `signal_id` | Joins to signals, back-ref lookup |
| `signals` | `trade_id` | Back-ref from trades |
| `orders` | `superseded_by` | SL trail chain walks |

**Suggestion:** Add `CREATE INDEX IF NOT EXISTS idx_trades_signal_id ON trades(signal_id)` etc. These are cheap to add and help reconciler joins and daily report queries.

---

### O4 — "Today" queries use DATE() on timestamp column (can't use index)

> **STATUS: FIXED (FIX-174).** Added a STORED generated `date` column (`substr(<ts>,1,10)`, identical to `DATE(ts)` for ISO-8601 timestamps) plus an index to `fm_ledger`, `candles`, `system_metrics`, and `webhook_audit`. Switched the hot date-range queries (`get_daily_realized_net_pnl`, `get_ledger_for_date`, `get_candles_for_date`, and the `gemini_trade_coach` candle summary) from `WHERE DATE(ts)=?` to `WHERE date=?` so they hit the index. `EXPLAIN QUERY PLAN` confirms the daily-loss query now does `SEARCH fm_ledger USING INDEX idx_fm_ledger_date`. Applied to existing DBs via the table-rebuild runner (STORED generated columns can't be added via `ALTER`; the rebuild excludes generated columns from the data copy and lets SQLite recompute them). Schema v26→v27; full unit suite 2933 passed.

`get_daily_realized_net_pnl()` runs `WHERE DATE(ts) = ?` on `fm_ledger.ts` — correct result, but `DATE(ts)` wraps the column in a function, preventing index use.

High-traffic time-series tables without a plain `date` column: `fm_ledger`, `candles`, `system_metrics`, `webhook_audit`.

**Suggestion:** Add a stored `YYYY-MM-DD` `date` column + index, matching the pattern already used in `pnl_reconciliation`, `strategy_metrics`, `trade_journal`. (Or use a functional index on `substr(ts,1,10)` as done in `innings`.)

---

### O5 — No DB-level retention / row cleanup (unbounded growth)

> **STATUS: FIXED (FIX-175).** Added `scripts/db_retention.py` — the DB analogue of the log-file cleanup cron. Deletes rows older than per-table windows: bulky analytics 90d (`candles`, `system_metrics`, `webhook_audit`, `screener_results`), `reconciliation_log` 180d, `fm_ledger` 365d (capital audit kept long). Index-backed cutoffs use the O4 `date` column where present, else `substr(ts,1,10)`; only deletes rows strictly older than the cutoff. Per-table transactions isolate failures; `--dry-run`, `--vacuum` (off by default; VACUUMs main + analytics), and `--set TABLE=DAYS` override. Cron at 02:30 IST (Sun adds VACUUM), after the 01:00 backup so a pre-prune snapshot exists. 8 unit tests.

Append-only tables with no row expiry:

| Table | Growth rate |
|-------|-------------|
| `candles` | Thousands/day |
| `system_metrics` | ~75 rows/day (5-min snapshots) |
| `webhook_audit` | Per-signal |
| `screener_results` | Per-signal |
| `fm_ledger` | Per-capital-event |
| `reconciliation_log` | Per-reconcile cycle |

Log *files* have a 30-day cron cleanup; DB rows have none. Over a year this bloats the SQLite file and slows the nightly `.backup`.

**Suggestion:** Add a retention cron (e.g., `DELETE FROM candles WHERE date < DATE('now', '-90 days')`) or move old rows to an archive DB file.

---

### O6 — Monolithic DB mixes hot transactional + high-volume analytics tables

> **STATUS: FIXED (FIX-176).** `candles`, `system_metrics` and `system_metrics_daily` relocated out of `trading_system.db` into a separate `analytics.db`, ATTACHed at runtime as schema `analytics` (`core/db_connect.py` is the single source of truth for the path, the table set, and the connect+attach sequence). Transparent to query code — since these tables exist only in `analytics.db`, SQLite resolves unqualified `FROM candles` to them, so existing SQL is unchanged. Safe because none of the three has an FK to/from the trading DB and nothing joins them to `trades`/`signals`. Existing DBs migrate automatically on next start (v27→v28): `relocate_analytics_tables()` moves the rows then drops them from main (idempotent, per-table txn, target cleared first so a crash-retry can't duplicate). Raw-sqlite scripts switched to the attach-aware `db_connect.connect`; nightly cron now `.backup`s both files. The trading DB stays small → near-instant backup + short WAL checkpoints. 2 migration tests + full suite green.

`trades`/`orders`/`fm_ledger` (critical path, tiny rows) share one SQLite file and WAL with `candles`/`system_metrics` (bulky, non-critical). WAL absorbs most contention today, but as candle history grows:
- WAL checkpoint takes longer, potentially stalling writers
- `.backup` grows proportionally

**Suggestion (medium-term):** Isolate analytics into a separate `analytics.db`, attached at runtime with `ATTACH DATABASE`. Keeps trading DB small (~MB), backup near-instant, eliminates any chance of a candle write stalling an order write.

---

### O7 — Comment numbering drift in schema.sql (cosmetic)

> **STATUS: FIXED (FIX-171).** Renumbered table comments sequentially 1..30 (counting the removed `capital_ledger` as slot 6). Duplicate "TABLE 19/20/24" labels removed.

The table comment block has duplicate labels:
- Two "TABLE 19": `trade_excursions` and `telegram_alerts`
- Two "TABLE 20": (check sequential)
- Two "TABLE 24": `fno_ban` and `eod_verification`

No runtime impact, but confusing when grepping the comment block.

**Suggestion:** Renumber sequentially next time the file is touched.

---

### O8 — `instruments` table may not exist in DB (verify)

`scripts/gemini_data_integrity_check.py:112` runs `SELECT instrument_token FROM instruments ...`, but `instruments` is not in `schema.sql`. The instrument master is `config/instruments.csv` loaded via `InstrumentCache`. If no `instruments` table exists in the live DB, that integrity check script fails silently or errors.

**Suggestion:** Verify with `SELECT name FROM sqlite_master WHERE name='instruments'` against the live DB. See verification result below.

---

### O9 — Soft circular reference between signals and trades

> **STATUS: DOCUMENTED (FIX-171).** Intentional design, no code fix needed. Rationale block added to the `trades` table comment in `core/schema.sql`.

`signals.trade_id` ↔ `trades.signal_id` is a mutual reference — works because `signals.trade_id` is nullable (set post-fill), but makes the 1:N direction slightly ambiguous in the schema.

This is documented and correct; noting for completeness.

---

## What NOT to change

- **Money as REAL:** Tolerate 1-paise float rounding; switching to integer-paise is a large migration for marginal benefit at Rs 10k scale.
- **Single-row snapshot pattern:** `capital_snapshot`, `kill_switch_state`, `session` — good pattern, keep it.
- **Append-only ledger discipline:** `fm_ledger` — do not add UPDATE paths.
- **Migration changelog in footer:** High value for understanding schema evolution.

---

## Observation #8 Verification Result

*See SSH check run against live DB — result recorded below.*

```
SELECT name FROM sqlite_master WHERE name='instruments';
```

Result documented in session notes.

---

## Priority Order (all post-Monday, non-urgent)

| Priority | Observation | Impact | Status |
|----------|-------------|--------|--------|
| High | O5 — DB retention | Long-term operational stability | FIXED (FIX-175) |
| High | O6 — DB split | WAL health, backup speed | FIXED (FIX-176) |
| Medium | O3 — Missing indexes | Reconciler + report query speed | FIXED (FIX-171) |
| Medium | O4 — Date query index | fm_ledger daily-loss query | FIXED (FIX-174) |
| Low | O1 — FK declarations | Defense-in-depth | FIXED (FIX-173) |
| Low | O2 — Enum CHECKs | Defense-in-depth | FIXED (FIX-172) |
| Cosmetic | O7 — Comment numbering | Readability | FIXED (FIX-171) |
| Verify | O8 — instruments table | Data integrity check correctness | FIXED (warn added) |
| Doc | O9 — Soft circular ref | Schema clarity | DOCUMENTED (FIX-171) |

**All nine observations closed.** O1–O9 implemented in FIX-171→FIX-176 (schema v24→v28). Nothing outstanding from this review.

---

*This review was read-only at schema v24. All nine observations were subsequently implemented in follow-up commits FIX-171→FIX-176, advancing the schema to v28: O1–O4/O7/O9 (defense-in-depth, indexing, docs), O5 (DB row retention cron), and O6 (analytics DB split into analytics.db). Nothing from this review remains outstanding. Schema is production-ready for live trading from 16-Jun-2026.*
