# M-K3 — FOREIGN-KEY ENFORCEMENT ON THE RAW-SCRIPT PATH: INVESTIGATION

> **LATENT VIOLATIONS IN PRODUCTION RIGHT NOW: NO — COUNT = 0.**
> Zero violating rows across all **14** FK-carrying tables (**92,318** rows) in
> `trading_system.db`; `analytics.db` declares **no** FK constraints at all.
> `PRAGMA integrity_check` = `ok` on both files.

**Date:** 25-Jul-2026 (IST) · **Scope:** INVESTIGATE ONLY · **Nothing was enabled, changed or deployed.**
**Item:** M-K3 — `core/db_connect.py:93-111` — `connect()` does not enable
`PRAGMA foreign_keys` or `synchronous = FULL` on the main DB
(`docs/audit/full_system_audit_04july2026.md:146`).
**Method:** read-only, against a **byte-identical copy** of the live VM databases. The
live files were **proven untouched** (sha256 before == after, no `-wal`/`-shm` sidecar
created — the `sqlite_ro_wal_sidecar_gotcha` memory).

---

## ARTIFACT BASELINE (within-batch before == after)

| file | size | mtime (IST) | sha256 before | sha256 after |
|---|---|---|---|---|
| `data_store/trading_system.db` | 123,101,184 | 2026-07-25 09:20:01 | `0266001489077d64…8e117` | **identical** |
| `data_store/analytics.db` | 52,809,728 | 2026-07-24 18:16:06 | `2ff1a46b602c7054…3eb03c68` | **identical** |

No `trading_system.db-wal` / `-shm` existed before **or** after (the DB is cleanly
checkpointed; the trading service is down). Work was done on
`/tmp/mk3_fkcheck/{main.db,analytics.db}`, verified byte-identical to live by sha256
immediately after the copy, and **the scratch directory was removed**.

⚠️ Per the `artifact-baseline-reconciliation-19jul` rule this is a *within-batch*
equality, not a claim that the file matches any earlier session's hash.

---

## A1 — WHICH CONNECTIONS LACK THE PRAGMA

Two connection tiers exist, and only one sets FK enforcement.

**Tier 1 — `StateStore` (`core/state_store.py:107-113`): FK ON.**
`_CONNECTION_PRAGMAS` = `journal_mode=WAL`, `synchronous=FULL`, **`foreign_keys=ON`**,
`temp_store=MEMORY`, `busy_timeout=30000`. Applied to every connection, every thread.
This is the whole application (`main.py` and everything it owns).

**Tier 2 — `core.db_connect.connect()` (`db_connect.py:107-111`): FK OFF.**
Sets `busy_timeout=30000` and ATTACHes analytics (with `analytics.journal_mode=WAL` +
`analytics.synchronous=FULL`); it sets **no** `foreign_keys` and **no** `main.synchronous`.

**Tier 3 — raw `sqlite3.connect()` outside `db_connect` entirely: FK OFF.** (P4-4's
"19 raw sqlite sites"; ~26 call sites today.)

### Every non-`StateStore` connection, classified by whether it writes

| site | opens via | writes? | write target | target has FK? |
|---|---|---|---|---|
| `ops/control_tower/aggregator.py:199` (+`db.py`, `health.py`, `status.py`) | `db_connect.connect(attach=True)` | **YES** | `control_tower_findings` / `_freshness` / `_runs` / `_trends` / `_status` | **no** |
| `ops/control_tower/size_logger.py:83` | `db_connect.connect(attach=False)` | **YES** | `control_tower_trends` | **no** |
| `scripts/capture_metrics_baseline.py:153,177,226,259` | `db_connect.connect` | **YES** | `system_metrics`, `system_metrics_daily` (analytics.db) | **no** |
| `scripts/fetch_daily_candles.py:299` | `db_connect.connect` | **YES** | `candles` (analytics.db) | **no** |
| `scripts/check_vm_state.py:20` | raw `sqlite3.connect` | **YES** (operator-only flags) | `kill_switch_state`; `trades.status`; `orders.status` | parent/child tables, but **no FK column is written** |
| `ops/control_tower/cli.py:48` | `db_connect.connect(attach=False)` | no | — | — |
| `scripts/strategy_registry_officer.py:230` | `db_connect.connect` | no | — | — |
| `scripts/liveness_probe.py:145` | `db_connect.connect` | no | — | — |
| `scripts/gemini_data_integrity_check.py:84,98,114` | `db_connect.connect` | no | — | — |
| `scripts/gemini_trade_coach.py:72,95,114` | `db_connect.connect` | no | — | — |
| `scripts/fetch_daily_candles.py:117` | raw `sqlite3.connect` | no | — | — |
| `scripts/gemini_premarket_brief.py:93,107,125,140` | raw `sqlite3.connect` | no | — | — |
| `scripts/preflight/checks/{database,engine,state}.py` (7 sites) | raw `sqlite3.connect` | no | — | — |
| `scripts/generate_screened_stocks_csv.py:270` | `db_connect.connect_readonly` | **cannot** (`mode=ro` + `query_only=ON`) | — | — |
| `scripts/deploy_assert.py:40` | raw, `mode=ro` + `query_only=ON` | **cannot** | — | — |
| `ops_dashboard/backend/readers/db_reader.py:64` | raw, `mode=ro` | **cannot** | — | — |
| `scripts/v3_shadow_soak_report.py:66,158,288` · `v3_hardgate_parity_recompute.py:214` | raw, `mode=ro` | **cannot** | — | — |
| `scripts/backup_restore_drill.py` (6 sites) | raw `sqlite3.connect` | on a **test copy** only; sets `foreign_keys=ON` itself at `:159` | — | — |
| `core/db_connect.py:83` (`init_analytics_schema`) | raw, direct to `analytics.db` | DDL only | analytics tables | **no** |

**⭐ The load-bearing result of A1:** *no FK-off connection writes to any FK-carrying
table.* Every DELETE-capable prune job — the only class that can orphan a child row —
goes through **`StateStore`**, i.e. already has FK **ON**:

- `scripts/db_retention.py:49,203` → `StateStore`
- `scripts/eod_cleanup.py:31,280` → `StateStore` (and `:229` documents that FK is ON)
- `scripts/trade_journal.py:33,157` → `StateStore`

This replicates the **15-Jul finding** (`followup_investigation_15jul2026.md:74`) that
M-K3's "cron writes run FK-enforcement OFF" was **REFUTED for the `eod_cleanup` path**
— and extends it: it is refuted for *every* path that touches an FK-carrying table.

---

## A2 — ARE THERE LATENT VIOLATIONS RIGHT NOW? **NO. COUNT = 0.**

`PRAGMA foreign_key_check`, run globally and then per-table (so a `foreign key mismatch`
error on one table could not mask the rest), against the copy of the live DB:

```
--- PRAGMA main.foreign_key_check ---
  TOTAL VIOLATING ROWS: 0

  gate_state                   rows=0         violations=0
  gtt_state                    rows=0         violations=0
  innings                      rows=322       violations=0
  orders                       rows=713       violations=0
  reconciliation_log           rows=7790      violations=0
  retest_state                 rows=0         violations=0
  screener_results             rows=33171     violations=0
  shadow_trades                rows=0         violations=0
  signals                      rows=49410     violations=0
  smart_tgt_state              rows=0         violations=0
  sr_detector_results          rows=229       violations=0
  trade_excursions             rows=135       violations=0
  trade_journal                rows=125       violations=0
  trades                       rows=423       violations=0
--- PRAGMA analytics.foreign_key_check ---
  TOTAL VIOLATING ROWS: 0      (analytics declares 0 FK constraints)

PRAGMA main.integrity_check      -> ok
PRAGMA analytics.integrity_check -> ok
```

**The 16 FK constraints (all `ON DELETE NO ACTION` / `ON UPDATE NO ACTION`):**

```
gate_state.signal_id          -> signals.signal_id
gtt_state.trade_id            -> trades.trade_id
innings.trade_id              -> trades.trade_id
orders.superseded_by          -> orders.order_id      (self-referential)
orders.trade_id               -> trades.trade_id
reconciliation_log.trade_id   -> trades.trade_id
retest_state.signal_id        -> signals.signal_id
screener_results.signal_id    -> signals.signal_id
shadow_trades.live_trade_id   -> trades.trade_id
shadow_trades.signal_id       -> signals.signal_id
signals.trade_id              -> trades.trade_id
smart_tgt_state.trade_id      -> trades.trade_id
sr_detector_results.signal_id -> signals.signal_id
trade_excursions.trade_id     -> trades.trade_id
trade_journal.trade_id        -> trades.trade_id
trades.signal_id              -> signals.signal_id
```

### ⭐ ANTI-VACUITY — the green could have been red

A `0` from a check that cannot report anything else is worthless
(`feedback_verify_rc_not_output`). Proven on a **throwaway third copy** (`probe.db`,
deleted immediately; the evidence copy untouched):

```
violations BEFORE plant  = 0
plant: INSERT INTO screener_results (signal_id,…) VALUES ('MK3-PROBE-NO-SUCH-SIGNAL', …)
violations AFTER plant   = 1  [('screener_results', 485326, 'signals', 0)]
with foreign_keys=ON     : INSERT BLOCKED -> FOREIGN KEY constraint failed
```

Two things are established, not assumed: the check **detects** a violation on this exact
database, and the pragma **has teeth** on this build (the same insert that succeeds with
FK off is rejected with FK on).

### Observation — the signals table is no longer unbounded

The 15-Jul report recorded `signals` = **146,000** rows (143,044 `REJECTED_*`,
**108,243** prune-eligible-but-stuck) and `screener_results` = **94,436** conflicting
children, because the step-4 prune rolled back atomically every night. Today those read
**49,410** and **33,171**, and the DB is **123 MB** (was 357 MB). The FIX-135 child-first
delete (`eod_cleanup.py:262` deletes the children, `:265` then the parents) is running and
succeeding. Noted as context; not a claim about any other job.

---

## A3 — WHICH JOBS WOULD ENABLING BREAK? **NONE — the premise does not arise (0 violations).**

A3 is conditional on violations existing. They do not. The equivalent question that *can*
be answered is *"which job writes to a table where FK enforcement would newly apply?"* —
and per A1 the answer is **none**. Enabling `foreign_keys = ON` inside
`db_connect.connect()` today would change the behaviour of:

- `control_tower` (cron, 5 write targets) — none FK-carrying → **no change**
- `size_logger` — `control_tower_trends`, not FK-carrying → **no change**
- `capture_metrics_baseline` (cron ×2) — analytics tables, no FKs → **no change**
- `fetch_daily_candles` (cron) — `candles`, no FKs → **no change**
- the seven read-only `db_connect` callers → **no change** (a read is unaffected)

`scripts/check_vm_state.py` is the one write-capable raw-sqlite site that touches
FK-carrying tables (`trades`, `orders`) — but it is (a) an **operator-run** script, not a
cron, (b) writes only `status` / `exit_reason` / `kill_switch_state`, never an FK column,
and (c) does **not** go through `db_connect`, so a `db_connect` change would not reach it
at all.

---

## A4 — WHAT ENABLING WOULD COST AND PROTECT

**⭐ It would be a behavioural no-op today.** That is a measured statement, not a
prediction: zero latent violations × zero FK-off writers against FK-carrying tables.

**What it would protect:** future drift only. Today the FK-carrying tables are written
exclusively through `StateStore`. If a future raw-sqlite or `db_connect` script ever
inserted a child row (`screener_results`, `orders`, `trade_journal`, …) or deleted a
parent (`signals`, `trades`) without the constraint, nothing would catch it. Enabling
closes that door before it is opened, and removes a two-tier surprise — a reader who
knows `StateStore` enforces FKs currently has no reason to expect `db_connect.connect()`
does not.

**Is the risk a first-write-after-enable surprise?** **No, on today's code.** The
first-write-after-enable failure mode requires either (i) an existing violation that a
subsequent write to the same table would surface, or (ii) a writer that depends on FK-off
semantics (e.g. a bulk loader inserting children before parents). Neither exists. The
residual risk is the ordinary one: any *future* raw-path bulk-load pattern would have to
be written parent-first.

**⚠️ One caveat that keeps this an investigation, not a rubber stamp.** The
`foreign_keys` pragma is **connection-wide, not per-schema** — enabling it in
`db_connect.connect()` would also cover the ATTACHed `analytics` schema. Analytics
declares no FK constraints, so this is inert today, but it is the mechanism by which a
future analytics FK would silently start enforcing on the raw path. Also,
`core/migrations.py:279-315` deliberately toggles FK **OFF** around a table rebuild and
restores it after COMMIT (and runs its own `foreign_key_check` before committing, at
`:301`) — it reads the prior value first, so it composes correctly with either setting.
Migrations only run through `StateStore` from `main.py` boot (the `MIGRATION-ON-OPEN`
guard), so `db_connect` never reaches that path.

### ⭐ MEASURED CORRECTION — the `synchronous = FULL` half of M-K3 does not reproduce

M-K3 asserts the raw path writes with *"weaker durability than the app."* Measured:

| | `foreign_keys` | `synchronous` | `journal_mode` | `busy_timeout` |
|---|---|---|---|---|
| `StateStore` (explicit) | **1** | 2 (FULL) | wal | 30000 |
| plain `sqlite3.connect` on the live DB, **VM** (py 3.12.3 / sqlite 3.45.1) | **0** | **2 (FULL)** | wal (persistent on the file) | 5000 |
| plain `sqlite3.connect`, **PC** (py 3.11.9 / sqlite 3.45.1) | **0** | **2 (FULL)** | — | 5000 |

`synchronous` is already **FULL** on both platforms without anyone setting it, and
`journal_mode=WAL` is persistent on the file, so the raw path is **not** less durable.
Only `foreign_keys` genuinely differs (`0` vs `1`); `temp_store` differs (`0` default vs
`MEMORY`) and is a performance knob, and `db_connect` already raises `busy_timeout` to
30 s itself.

The real — and much smaller — durability finding is that the raw path **inherits**
`synchronous=FULL` from SQLite's compile-time `SQLITE_DEFAULT_SYNCHRONOUS` rather than
**asserting** it. That is a platform-contingent guarantee: a future Python/SQLite build
compiled with `SQLITE_DEFAULT_SYNCHRONOUS=1` would silently downgrade every raw-path
write with no code change and no signal. So the one-line `synchronous=FULL` in
`db_connect.connect()` is worth having as *insurance against an environment change*, not
as a fix for a current gap. **This is not a recommendation** — it is stated so the next
reader does not re-derive it.

---

## A5 — NOTHING WAS ENABLED

⛔ No pragma added. No code changed. No config changed. No cron changed. No service
restarted. The live databases are byte-identical before and after (table above), and the
VM scratch directory was removed.

**What Rama is being asked, if anything:** nothing is blocking. M-K3 is, on today's
evidence, **hygiene with no live exposure** — the deciding question ("are there latent
violations?") is answered **no/0**, and the one job class that could create them already
runs with the pragma on.

---

## SUMMARY

| question | answer |
|---|---|
| Latent FK violations in production right now | **NO — 0 rows, 14 FK-carrying tables, 92,318 rows checked** |
| Could the check have gone red | **Yes — proven by planting a violation on a throwaway copy** |
| Connections lacking `foreign_keys=ON` | `db_connect.connect()` + ~26 raw `sqlite3.connect` sites (A1 table) |
| Of those, any that write to an FK-carrying table | **None** (the 3 prune jobs that delete all use `StateStore`) |
| Jobs that enabling would break | **None** — it is a behavioural no-op today |
| `synchronous=FULL` half of M-K3 | **Does not reproduce** — default is already FULL on VM and PC (measured) |
| Live DB touched | **No** — sha256 before == after; no `-wal`/`-shm` created |
