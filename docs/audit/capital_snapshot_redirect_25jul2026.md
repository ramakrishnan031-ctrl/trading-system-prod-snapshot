# §B — capital_snapshot readers redirected to live sources (25-Jul-2026)

**Authorised** (VS Code handoff §B, 25-Jul; ChatGPT rulings 2/3/4 adopted). Off-market, service down, book flat. **Display/observability only** — proven by import graph (§7). Follows the read-only verification in `capital_snapshot_verify_2026-07-25.md` and the §A fix in `opening_capital_double_count_25jul2026.md`.

---

## 1. The defect

`capital_snapshot` is a single-row table that **nothing writes**. Production holds **0 rows** (measured, `mode=ro&immutable=1`). Three readers pointed at it, so all three were reading nothing — each failing differently, and none loudly:

| reader | what it actually did |
|---|---|
| `scripts/preflight/checks/engine.py:94` | emitted `"no capital_snapshot row yet (app may still be initialising)"` — a **permanent WARN wearing a transient's wording**, on every run, forever |
| `scripts/healthcheck_server.py:187` | `fetch_one` returned `None`, so `capital_deployed_pct` was **never emitted at all** — and the metric dict's `0.0` default shipped instead, reporting "0% deployed" as if measured |
| `ops_dashboard/.../db_reader.py:325,337` | `capital_usage()` returned an **all-zero dict**, which the GUI rendered as real numbers |

Three readers, three different silent failures, one dead table.

## 2. Per-value sourcing (ruling 4 — the design was correct; this implements it)

Not a blind `fm_ledger` redirect. That premise was **already refuted** in the verification pass: `fm_ledger` holds *deltas*, not levels, and `margin_used` does not live there at all. Each value is now taken from where it actually lives, keeping `core/schema.sql`'s own definitions of the 3-balance model:

| value | source | note |
|---|---|---|
| `total_capital` | day's **first** INIT ledger row | the same broker-net figure the capital seed used; restart-safe per §A |
| `margin_used` | `Σ trades.margin_reserved` where status ∈ `OPEN/PARTIAL/EXITING` | schema: *"over open positions"* |
| `margin_reserved` | `Σ trades.margin_reserved` where status = `PENDING_FILL` | schema: *"pending orders not yet filled"* |
| `realized_pnl_today` | `Σ fm_ledger.pnl_delta` over `RELEASE_USED` | E4/W10 contract: `pnl_delta` is **already NET**; costs are persisted alongside for observability and must never be subtracted again |
| `cash_floor` | `total_capital − margin_used` | the free-cash residual — the meaning it always had |

**Reuse, not re-summation** — where the layer allows it:

- `healthcheck_server` runs **in-process** with the trading service and holds the real `StateStore`, so it calls **`get_day_opening_capital()`** directly.
- `preflight` and the GUI `db_reader` are **separate processes** and must NOT construct a `StateStore`: schema migrations run on **DB-OPEN** (P11, 14-Jul) and only `main.py`'s boot may migrate. Both therefore read with their own connections, mirroring the accessor's definitions. That constraint is architectural, not a shortcut, and is stated at each site.

## 3. ⭐ `capital_deployed_pct` — new definition, dated at the site (ruling 2)

```
was   margin_used / cash_floor        (healthcheck_server.py:193)
now   margin_used / total_capital
```

The old form is a ratio against **remaining** cash, which grows without bound as the book fills and is not a deployment percentage at all. **Safe to redefine because it never emitted a value** — `capital_snapshot` has 0 rows, so there is no series and no consumer holding the old meaning. Recorded at both emitting sites with the date and the reason.

**Div-0 guard states a reason, never a silent zero** (B3). When `total_capital` is absent, zero or NaN:
- `/metrics` sets `capital_deployed_pct: null` **plus** `capital_deployed_pct_unavailable: "<why>"`, and still reports `margin_used`.
- the preflight check WARNs with the reason and **omits** the metric rather than emitting `0.0`.

🔎 **Found while doing this:** `/metrics` pre-seeded `"capital_deployed_pct": 0.0` in the metrics dict. Because the old read set nothing, **that default was what shipped on every request** — "0% of capital deployed", indistinguishable from a real measurement. Changed to `None`. This is the same silent-zero class the instruction names, one layer further out than the guard it asked for.

## 4. `FundManagerBalanceCheck` — meaning kept, source moved

⚠️ **B4 as written ("keeps its current reference") could not hold together with B7 ("prove the old WARN is gone") and B9 ("table stays empty").** With 0 rows, keeping the reference means emitting that WARN forever. Raised before building; **Rama chose: redirect the source, keep the meaning.**

So the check still asks exactly one question — *"is the fund manager's free cash sane?"* — with the same criticality (CRITICAL) and the same FAIL conditions (NaN / None / ≤ 0). Only `cash_floor`'s derivation moved.

**Ruling 3 honoured:** the deployment percentage is **not** hung off this check. It is a new, separate `CapitalDeploymentCheck` with `criticality = WARN`, so it can report an unusual deployment level and **can never escalate a preflight run to CRITICAL**. One check, one meaning.

### 🐛 A real regression the tests caught mid-build

**SQLite has no NaN.** A NaN written to `balance_after` comes back as `NULL`, so "no INIT row" and "INIT row with a broken balance" collapse into the same `None`. The first draft treated both as the transient WARN — which would have **silently retired the crash-test NaN guard that is the entire reason this check exists**. Fixed by carrying `has_init` separately from `opening`: absence → WARN, present-but-unusable → FAIL. Pinned by `test_fund_manager_balance` and by plant P5.

## 5. Tests — RED first, by planting (B6/B7)

`tests/unit/test_preflight_engine.py` (rewritten to the new source, +8) and `tests/unit/test_fix132_healthcheck.py` (+3).

The old `_db_capital()` helper seeded a `capital_snapshot` row — so every one of these checks had only ever been exercised against a **fixture-only shape**. Replaced with `_db_live_capital()`, which builds what the app actually writes: one INIT row (`bucket='both'`, full balance) plus trades carrying `margin_reserved`.

**Six plants, six RED, source restored md5-identical each time** (`cfdedae30c5184df0b3d4bc67f23e883`; script `scratchpad/red_proof_b.py`):

| plant | test that went RED |
|---|---|
| P1 pct divides by `cash_floor` again (the old wrong definition) | `test_capital_deployment_emits_a_real_nonzero_pct` |
| P2 div-0 guard returns a silent `0.0` | `test_capital_deployment_div0_states_a_reason_never_a_silent_zero` |
| P3 new check made CRITICAL (ruling 3 violated) | `test_capital_deployment_is_alert_only_never_critical` |
| P4 unreadable DB allowed to raise | `test_capital_deployment_degrades_and_never_raises` |
| P5 NaN guard softened to a WARN | `test_fund_manager_balance` |
| **P6 the old `capital_snapshot` reader restored** | **`test_the_old_permanent_capital_snapshot_warn_is_gone`** |

**P6 is B7's anti-vacuity proof.** Putting the old reader back makes the exact string `"no capital_snapshot row yet (app may still be initialising)"` come straight back, and the test catches it. The assertion could be red, and was.

**B5 — preflight degrades, never crashes.** Both checks wrap the read and return a WARN with a real reason; `test_*_degrades_*` builds a DB with the tables absent and asserts WARN, not an exception. A crash in a Phase-B check costs a trading day, so this is tested rather than reasoned about.

**B8 — real non-zero against known open positions.** The book is flat and the service is down, so a live position is unavailable; both tests use fixtures with explicit open trades: preflight 2×1,500 margin against a 10,000 opening = **30.0%** (plus a separate 500 PENDING_FILL, proving the used/pending split), `/metrics` 2,500 against 10,000 = **25.0%** — a number the old `margin_used/cash_floor` form would have made 33.33%.

## 6. The GUI fixture was fiction too — the same shape as §A

`conftest.py` seeded `capital_snapshot` with `margin_used=42000, margin_reserved=3000`, and four tests asserted those numbers. The fixture's **own trades** are 4 OPEN × 5,000 = **20,000**, with no `PENDING_FILL` rows at all. So the snapshot claimed 42,000 of margin that no trade in the fixture supported.

Redirecting to the real source made the truth visible, and the expectations were corrected to it (`used 20000`, `pending 0`, `remaining 50000`, `deployed_pct 20.0`, exposure `margin_used 20000`, capacity `pct 28.6`). **That is the second fixture in this session found asserting numbers its own data did not support** — §A's was the bucket-split INIT pair. Both had made a wrong reader look right.

## 7. A7 gate — display/observability only

- **`ops_dashboard/.../db_reader.py`** — imported only inside `ops_dashboard/`; **zero** importers outside. Separate Waitress process on `127.0.0.1:8500`.
- **`scripts/preflight/checks/engine.py`** — reachable only from the preflight package's phase composition, run by cron as a **separate process** at 09:14. `main.py:3481` imports `startup_hook` only, which launches a **detached subprocess** and "never raises; never blocks startup".
- **`scripts/healthcheck_server.py`** — imported by `main.py:86` and started at `:3467`, so it is in-process. The changed code sits **inside the `/metrics` request handler**: it executes only on an HTTP GET to `127.0.0.1:8080/metrics`. Nothing in the trading loop calls it, and the only consumers of `capital_deployed_pct` are that handler and the preflight check. The new `get_day_opening_capital()` call is a **read** on an already-open `StateStore` — no migration-on-open risk.
- `reports/daily_report.py`, `reports/daily_trade_review.py`, `scripts/forward_shadow_record.py` — **0 references** to any changed accessor, so nothing shipped here is reachable from the 16:05 report or the forward-shadow recorder.

⇒ **no trading, sizing, capital or kill path reads any changed accessor.**

**Parity:** pure SQL and pure arithmetic; no `mode`/`paper`/`live` branch in any changed function or its callers. Identical in both modes **by construction**. Paper and live write INIT and `trades.margin_reserved` the same way.

## 8. B9 — the table is kept, with a dated note

`capital_snapshot` is **not dropped**: a schema change was not authorised, dropping needs a migration, and the definition documents what the 3-balance model meant. `core/schema.sql` now carries a dated block at the definition recording that it is unused since 25-Jul-2026, where each value moved to, and an instruction to delete the note if a writer is ever added.

## 9. Result

| | |
|---|---|
| files changed | `preflight/checks/engine.py` · `preflight/checks/__init__.py` (comment) · `healthcheck_server.py` · `db_reader.py` · `core/schema.sql` (comment) · 4 test files |
| `capital_snapshot` reads remaining in the three readers | **0** |
| new preflight check | `capital_deployment`, WARN-only, registered in Phase B |
| ops_dashboard suite | 380 passed |
| main suite | see §10 |

## 10. Regression — same window, same shell

BASE was taken **before any edit** in this session; no remembered baseline used.

| | failed | passed | skipped | wall |
|---|---:|---:|---:|---:|
| BASE | 13 | 5,112 | 5 | 836.44s |
| MERGE (§A+§B) | 12 | **5,122** | 5 | 857.57s |

- **MERGE-only failures: EMPTY** (`comm -13`) — **nothing new broke**. That is the binding criterion.
- **Test-count delta reconciles exactly:** 5,130 → 5,139 collected = **+9**, being +6 in `test_preflight_engine.py` (2 old capital_snapshot tests replaced by 8) and +3 in `test_fix132_healthcheck.py`. Passed went +10 = those 9 plus one flake flipping.
- **One BASE failure disappeared:** `test_instance_lock.py::TestSingleInstanceAcrossProcesses::test_p2_restart_after_crash_is_not_blocked`. **Proven flaky, not masked** — three consecutive runs of the *unchanged* tree gave `2 failed`, `2 failed`, `1 failed`. It is the known PC-env instance-lock flake already recorded as "fallout, not fixed", and it is untouched by anything here.

### The gate went RED once, and that is worth recording

The first §B gate run had **one MERGE-only failure**: `test_fix133_metrics.py::test_metrics_returns_all_fields`, asserting `capital_deployed_pct == 25.0`.

Its store double branched on `"capital_snapshot" in sql` and handed back `margin_used=25000, cash_floor=100000`. **That double was the only reason the metric ever had a value anywhere** — it was asserted at 25% in the test suite while production emitted nothing at all, for months. Fixed by teaching the double the two live queries with the same underlying numbers, so the 25% expectation still means what it always meant.

**That is the third fixture in this session found asserting what production never produced** — after §A's bucket-split INIT pair and the GUI's 42,000 margin. All three had made a wrong reader look right.

> Process note, recorded rather than hidden: two source files were edited while an earlier gate run was in flight, which contaminates it. That run was **killed and re-run from scratch** on the final tree rather than reported. The numbers above are from the clean run.
