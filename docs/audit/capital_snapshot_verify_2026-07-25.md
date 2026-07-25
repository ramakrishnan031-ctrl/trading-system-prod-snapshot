# capital_snapshot reader-redirect — PLAN VERIFICATION (25-Jul-2026)

# **NO. The planned WARN does NOT fire on a normal boot.** Preflight Phase B runs at **09:14** by cron; the INIT row is written at **08:15:2x–08:15:36** by `fund_manager.initialize()` — measured on the last five trading days. The seed precedes preflight by ~59 minutes on the normal path, and by source order (`main.py:2375` before `main.py:3481`) on the restart path too.

**INVESTIGATE-ONLY pass — nothing was built.** 2026-07-25 (IST), deployed HEAD `570b3e8`. Code read only; production DB opened `mode=ro&immutable=1` (zero-trace confirmed: no `trading_system.db-wal`/`-shm` created). Service `inactive` throughout. Every claim below is **MEASURED** from quoted source or query output unless marked ASSUMED.

Verifies the §B plan recorded in `fixation_batch_24jul_handoff.md`.

---

## Verdict table

| item | question | answer |
|---|---|---|
| **C1** | are the three read sites complete? | ✅ **yes** — 3, grepped not assumed |
| **C2** | preflight BEFORE or AFTER the boot capital seed? | ✅ **AFTER**, on both paths ⇒ **no daily false WARN** |
| **C3** | does a CRITICAL from `FundManagerBalanceCheck` block the boot? | ✅ **NO — alert-only.** Adds no new way to lose a day |
| **C4** | is INIT unique per day? | 🔴 **NO** — and `db_reader` **already** doubles on 10 of 30 dates |
| **C5** | does `fm_ledger` hold every value? | ⚠️ **premise is partly false** — it holds the *ingredients*, not the three named columns |

---

## C1 — the three read sites are complete (grepped)

`grep -rn "capital_snapshot" --include=*.py --include=*.sql` over the repo, excluding `tests/`, `venv/`, `__pycache__`:

| site | line | what it reads |
|---|---|---|
| `scripts/preflight/checks/engine.py` | **:94** | `SELECT cash_floor, margin_used FROM capital_snapshot WHERE id = 1` |
| `scripts/healthcheck_server.py` | **:187** | `SELECT margin_used, cash_floor FROM capital_snapshot WHERE id = 1` |
| `ops_dashboard/backend/readers/db_reader.py` | **:325** | `cash_floor, margin_used, margin_reserved` (the `opening_capital` **fallback**) |
| `ops_dashboard/backend/readers/db_reader.py` | **:337** | `margin_used, margin_reserved, realized_pnl_today, cash_floor` (`capital_usage`) |

**No fourth reader.** Two near-misses, both checked and dismissed:

- `scripts/eod_broker_reconcile.py:441` — the function is *named* `_local_capital_snapshot` but reads **`fm_ledger`** (`SELECT balance_after FROM fm_ledger ORDER BY ledger_id DESC LIMIT 1`). A name, not a read site.
- `scripts/backup_restore_drill.py:48` — the string `"capital_snapshot"` in a table-name list for the restore drill. Not a value read.

`core/schema.sql:372-378` is the definition; `core/state_store.py:38` is a docstring mention.

## C2 ⭐ — the one that decides the build

**Two paths, and preflight is after the seed on both.**

**Path 1 — the normal morning (cron).** `config/cron_registry.yaml:203-218`: `preflight_phase_b` = `14 9 * * 1-5`, i.e. **09:14 Mon-Fri**, `market_day_only: true`. `FundManagerBalanceCheck` is in the Engine group, and `scripts/preflight/checks/engine.py:4` states it plainly: *"Phase B runs at 09:14, by which time token-watcher has started the app."* The app boots 08:15.

**Measured** — the first INIT row per day, from production `fm_ledger`:

```
2026-07-24 first INIT ts=2026-07-24T08:15:36.336319+05:30
2026-07-23 first INIT ts=2026-07-23T08:15:33.709794+05:30
2026-07-22 first INIT ts=2026-07-22T08:15:29.680553+05:30
2026-07-21 first INIT ts=2026-07-21T08:15:26.589351+05:30
2026-07-20 first INIT ts=2026-07-20T08:15:33.036311+05:30
```

⇒ the INIT row exists **~59 minutes** before Phase B reads anything. The planned `WHERE date=today AND entry_type='INIT'` finds a row on every normal morning.

**Path 2 — a restart (the on-demand hook).** `main.py:3481-3482` calls `run_on_demand_if_missed()`, which re-launches a missed phase. It sits **after** `main.py:2375 fund_manager.initialize(_startup_capital)` — the call that writes INIT (`capital/fund_manager.py:431-439`). So even here the seed is written first, and `startup_hook.py` is a *detached subprocess* that (its own docstring) *"Never raises; never blocks startup."*

**Non-trading days:** `market_day_only: true` ⇒ preflight does not run at all. No holiday false WARN.

**The one case where the WARN would fire is a TRUE one:** the app never booted (or died before `initialize`) and 09:14 arrives with no INIT row for today. That is a real fault worth an alert — it is the S4 shape. **The planned WARN is correctly specified; the §B B1 trap does not apply.**

## C3 — a CRITICAL alerts; it does not block

**ALERT-ONLY, stated in three places and true at the exit code:**

- `scripts/preflight/__init__.py:11` — *"ALERT-ONLY on CRITICAL -- pre-flight is the eyes, Rama is the gate; trading …"*
- `scripts/preflight/orchestrator.py:234-236` — *"ALERT-ONLY: success exit even with CRITICAL findings (they go to the report/sentinel/alert, not the exit code)"* → `return 0`
- `config/cron_registry.yaml:207` — `critical: false` for `preflight_phase_b`

**Structurally it could not block anyway:** preflight is a separate cron process running at 09:14, an hour after the 08:15 boot. There is nothing left to gate.

**Checked for a back door and found none:**

- **No autofix.** `FundManagerBalanceCheck` never sets `auto_fixable`, which defaults to `False` (`scripts/preflight/base.py:98`), so `fix()` is never called and nothing can restart or mutate the service off this check.
- **The sentinel has two consumers, both reporting.** `scripts/cron_officer.py:480` (`_read_preflight_summary` → a briefing-banner dict) and `scripts/cron_report_render.py:67` (the banner). Neither gates anything.

⇒ **the `<=0 / NaN → CRITICAL` branch adds no new way to lose a trading day.** It is a louder alert, not a gate. No decision is owed here.

## C4 🔴 — INIT is NOT unique per day, and the exposure is already live

**From the writer.** `capital/fund_manager.py:408-418` — *"Writes INIT row to fm_ledger. Must be called exactly once…"* — and the H-4 guard is `if self._initialized:`, an **in-memory, per-process** flag. `core/state_store.py:2527` says it outright: *"FundManager.initialize writes one INIT row **per process start**"*. A mid-day restart constructs a new `FundManager` ⇒ a **second INIT row for the same date**. Nothing at the schema or SQL level prevents it — it is an unguarded convention, not a constraint.

**From production data** (`GROUP BY date HAVING COUNT(*)>1`, `mode=ro&immutable=1`):

```
2026-07-21  n=2  SUM=19716.03   (true opening ≈ 9857.30)
2026-07-06  n=2  SUM=10000.00
2026-07-01  n=2  SUM=19650.74
2026-06-23  n=3  SUM=30162.28
2026-06-19  n=2  SUM=20030.33
2026-06-18  n=8  SUM=79338.70
2026-06-17  n=2  SUM=19989.80
2026-06-16  n=3  SUM=29988.20
2026-06-15  n=7  SUM=39993.82
2026-06-12  n=7  SUM=2000000.00
→ 10 of 30 INIT dates have more than one row.
```

**2026-07-21 is the one that matters** — it is the forced 11:57 mid-session restart that proved the Phase-2 carryover. Two INIT rows, `SUM = 19,716.03` against a real opening capital of ~**Rs 9,857.30** (ACTUAL capital, per the capital-vocabulary rule). **A clean 2×.**

### 🔴 Reported separately, as instructed — NOT fixed here

**`db_reader.opening_capital` (`:317`) already uses `SUM(balance_after) … entry_type='INIT'`.** So on 21-Jul the ops dashboard's opening capital read **19,716.03 instead of 9,857.30**, and every percentage resolved against it (daily-loss %, intraday-bucket %, `capital_deployed_pct`) was **half** its true value. This is a **pre-existing defect in shipped code**, not something the §B build would introduce — but §B copying that SUM would propagate it into the **boot-gate** check.

**The correct accessor already exists and is restart-safe:** `state_store.get_opening_capital()` (`core/state_store.py:2539-2546`) takes `ORDER BY ts ASC LIMIT 1` — the **FIRST** INIT row — and returns `None` when there is none. It is documented as *"the single, parity-safe (same DB in paper + live) capital source"*.

> **Recommendation for the §B build (not applied):** use `get_opening_capital()`, **not** `SUM(...)`. It is the canonical accessor, it is restart-correct, its `None` is exactly the signal the planned WARN wants, and reusing it satisfies the plan's own anti-dup rule. The `db_reader:317` SUM is a separate fix, one per commit.

## C5 ⚠️ — the anti-dup premise, corrected

The instruction states: *"`fm_ledger` already holds every value (`margin_used`, `realized_pnl_today`, `cash_floor`)."* **Verified against the schema — it does not.**

```
fm_ledger columns (PRAGMA table_info):
  ledger_id, ts, entry_type, amount, bucket, balance_before, balance_after,
  signal_id, reservation_id, reason, session_id, direction, trade_id,
  margin_delta, pnl_delta, costs

capital_snapshot columns:
  id, cash_floor, realized_pnl_today, margin_used, margin_reserved,
  charges_today, last_broker_sync, sync_source, updated_at
```

`margin_used`, `realized_pnl_today` and `cash_floor` are **`capital_snapshot` columns**. `fm_ledger` holds the **ingredients** (`margin_delta`, `pnl_delta`, `balance_after`, `costs`) — deltas, not levels. The correct statement is: *every value is **derivable** without `capital_snapshot`, and the §B plan already resolved each derivation* — but **not all from `fm_ledger`**:

| value | source the plan itself names | is it `fm_ledger`? |
|---|---|---|
| total capital / opening | `fm_ledger` INIT row | ✅ yes (**use FIRST, not SUM** — C4) |
| `realized_pnl_today` | `state_store.get_daily_realized_net_pnl(today)` — existing accessor | ✅ via fm_ledger |
| `margin_used` | `SUM(trades.margin_reserved)` over OPEN/PARTIAL/PENDING_FILL | ❌ **`trades`, not `fm_ledger`** |

**Reuse-existing-accessors: confirmed available and correct** — `get_opening_capital()` and `get_daily_realized_net_pnl()` both exist. `margin_used` has no fm_ledger accessor and must come from `trades`.

### ⚠️ Flagged, because the plan's wording hides it: `cash_floor` ≠ total capital

The plan maps *"total_capital/cash_floor → SUM(INIT balance_after)"* as if the two were one value. They are not — `db_reader:329` computes `total = cash_floor + margin_used + margin_reserved`, so `cash_floor` is the **free-cash residual**, not the total.

This matters because **`FundManagerBalanceCheck` asserts `cash_floor > 0`** (`engine.py:105-106`). Redirect it to opening capital and the check's *meaning* silently changes from *"is there free cash?"* to *"did the day seed a positive opening balance?"* — a far rarer condition. The check would stop being able to detect a fully-deployed book. **Not a blocker, but it is a semantics change that belongs in the §B commit message and at the site**, in the same class as the plan's own B4 note about `capital_deployed_pct`.

**Live values right now** (single row, `id=1`) confirm the table is stale rather than wrong — it is the reason the redirect exists; recorded here only as the pre-state for §B's anti-vacuity check.

---

## What this pass did NOT do

- **No build.** No file changed. No test written. No config, no service, no restart.
- **Did not fix `db_reader:317`.** Reported above, separately, per the instruction.
- **Did not verify B4/B5/B6** (the metric-meaning change, the ~7% cross-check, the dead-fallback removal) — outside C1–C5. They remain owed by the build.
- **ASSUMED nothing about a live 09:14 run:** the timing conclusion rests on cron schedule + source order + five days of measured INIT timestamps. A live Monday 09:14 would confirm it observationally; nothing here needs it.

## Evidence appendix (all read-only, reproducible)

`scripts/preflight/checks/engine.py:4,10,78-108` · `scripts/preflight/base.py:97-98` · `scripts/preflight/orchestrator.py:234-236` · `scripts/preflight/__init__.py:11` · `scripts/preflight/startup_hook.py:1-10,30-32` · `scripts/preflight/autofix.py` (no fix bound to `fund_manager_balance`) · `scripts/cron_officer.py:475-489` · `scripts/cron_report_render.py:67` · `config/cron_registry.yaml:203-218` · `main.py:2372-2375,3481-3482` · `capital/fund_manager.py:408-448` · `core/state_store.py:2525-2546` · `ops_dashboard/backend/readers/db_reader.py:307-343` · `scripts/healthcheck_server.py:187` · `scripts/eod_broker_reconcile.py:441-458` · `core/schema.sql:372-378`.

DB probe: `file:/home/ubuntu/systems/trading-system/data_store/trading_system.db?mode=ro&immutable=1` — service `inactive`, no `-wal`/`-shm` created (verified after the read).
