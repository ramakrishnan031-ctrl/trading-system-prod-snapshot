# §A — opening-capital double-count, fixed (25-Jul-2026)

**Authorised** (VS Code handoff §A, 25-Jul; ChatGPT ruling 1 — separate commit, §A first). Off-market, service down, book flat. **Display-only** — proven by import graph (§6). Built off the read-only finding in `capital_snapshot_verify_2026-07-25.md` §C4.

---

## 1. The defect

`ops_dashboard/backend/readers/db_reader.py:317` computed the day's opening capital as

```sql
SELECT SUM(balance_after) FROM fm_ledger WHERE date=? AND entry_type='INIT'
```

**INIT is not unique per day.** `FundManager.initialize()` writes one INIT row per **process start** — its H-4 double-init guard is the in-memory `self._initialized` flag (`capital/fund_manager.py:408-448`), which a restart resets along with the object. So a mid-day restart appends a second INIT row for the same date and the SUM doubles.

**MEASURED on production** (`mode=ro&immutable=1`, zero-trace):

| | |
|---|---|
| INIT dates with more than one row | **10 of 30** |
| worst case still in the window | **2026-07-21** — the forced 11:57 restart |
| what the dashboard read that day | **19,716.03** |
| the true opening | **9,857.30** |

Every percentage that resolves against opening capital — daily-loss %, intraday-bucket allocation, `deployed_pct`, capacity headroom — read **half** its real value on that day.

> The handoff cites the true opening as ~₹9,857.60. **Measured: ₹9,857.30** (`2026-07-21T08:15:26.589351+05:30`). Corrected here; the ₹9,875.60 in the capital-vocabulary note is a different figure (ACTUAL capital), not this day's seed.

## 2. ⭐ The premise that fell — the test fixture modelled something production has never written

Standing rule 8 said to assume a third premise would fall. It did, and it is the reason this bug survived review.

`ops_dashboard/tests/conftest.py` seeded **two** INIT rows for the day, split by bucket:

```python
(_ts("08:15:01"), "INIT", 70000.0, "intraday",   0.0, 70000.0)
(_ts("08:15:02"), "INIT", 30000.0, "positional", 0.0, 30000.0)
```

Under that fixture `SUM(balance_after) == 100000.0` is **correct**, and four separate tests asserted exactly that. The SUM was not a typo — it was a faithful implementation of a data shape that **has never existed**.

**Production, measured — all 58 INIT rows:**

```
bucket=both   n=58        (no 'intraday', no 'positional', ever)
```

and every multi-INIT day is a restart duplicate carrying the **full** balance, not a split:

```
2026-07-21T08:15:26  bucket=both  9857.30      2026-07-01T08:15:21  bucket=both  9826.50
2026-07-21T11:57:41  bucket=both  9858.73      2026-07-01T13:59:36  bucket=both  9824.24
```

`FundManager.initialize()` writes exactly one row, `bucket="both"`, `balance_after=broker_balance` — the whole balance, once (`fund_manager.py:431-439`). The bucket split is applied **in memory** (`_intraday_avail = broker_balance * _intraday_pct`), never to the ledger.

**So both the fixture and the code were wrong, and each made the other look right.** The fixture was corrected to model production *before* the code was touched, and the existing suite stayed green on the **old** code (372 passed) — proving the fixture change is behaviour-neutral and that the RED below is the code's fault, not the fixture's.

One expectation moved with it: `test_g2b2_screens.py` asserted `len(ledger) == 7  # 2 INIT + 5 RELEASE_USED` → now `== 6  # 1 INIT + 5 RELEASE_USED`. Bucket allocations are computed as `pct × opening_capital` in `risk_capital.get_capital()` (`:74`), never from the ledger's `bucket` column, so `intraday_allocated == 70000.0` still holds.

## 3. A1 — is "first by timestamp" actually first? (measured, not assumed)

The instruction asked to verify that a mid-day re-seed cannot sort **before** the 08:15 seed. Checked three ways:

1. **`date` is not `DATE(ts)`.** `core/schema.sql` defines it as `substr(ts, 1, 10) STORED` — a pure string prefix, so there is **no UTC conversion** and no chance of a row landing on a neighbouring date. (The in-code comment calling it `== DATE(ts)` is loose but harmless here.)
2. **Format is uniform.** All 58 production INIT timestamps: one distinct offset (`+05:30`), one distinct length (32), `T` separator. IST has no DST, so the offset cannot vary.
3. **The string sort equals the real sort.** For every INIT date, `ORDER BY ts ASC` was compared against `sorted(..., key=datetime.fromisoformat)` — **0 days differed**.

⇒ a TEXT `ORDER BY ts` is chronological here, and the 11:57 re-seed sorts after the 08:15 seed. **"First" is correct.**

This is the same rule `state_store.get_day_opening_capital()` already used (`core/state_store.py:2539-2546`), so the system now has **one** definition of the day's opening capital instead of two that disagreed on restart days.

## 4. Tests — RED first

`ops_dashboard/tests/test_db_reader.py`, 4 new tests (×2 schema versions = 8):

| test | RED on the SUM |
|---|---|
| `test_opening_capital_ignores_a_restart_reseed` | returned 200,500.0 |
| `test_opening_capital_reproduces_the_measured_21jul_production_shape` | **returned 19,716.03** — the exact production number |
| `test_opening_capital_orders_by_timestamp_not_insertion_order` | returned 15,382.05 |
| `test_opening_capital_unchanged_on_a_normal_single_init_day` | (green both sides — the regression guard) |

The third inserts the **later** row first, so it wins on `rowid`; only a real `ORDER BY ts` can pick the 08:15 seed. That pins the ordering key rather than trusting insertion order.

## 5. ⚠️ One measured behaviour change beyond the fix — reported, not hidden

**2026-07-06** is the one production date where first ≠ "the sensible number":

```
2026-07-06T08:15:23  bucket=both  balance_after=0.00      <- the day's first INIT
2026-07-06T11:20:13  bucket=both  balance_after=10000.00
```

The 08:15 seed was **0.00** (a broker balance that came back empty), and an 11:20 restart re-seeded 10,000. The old SUM returned 10,000 by coincidence (0 + 10,000). The new code returns the first row, 0.00, which fails the pre-existing `if val is not None and float(val) > 0` guard at `:321`, falls through to the empty `capital_snapshot` fallback, and returns `None` → the UI renders `—`.

**That is one historical date, display-only, and arguably more honest**: the day genuinely opened with a zero seed, and `—` says "unknown" rather than asserting a number the ledger does not support. The `> 0` guard is pre-existing and was **not** touched — changing it is not this fix. Recorded so nobody re-derives it from a dashboard backfill.

## 6. Regression map and the A7 gate

**Every consumer of `db_reader.opening_capital` (5, all display):**
`ops_dashboard/backend/api/risk_capital.py:31,70,96` · `services/capacity.py:128` · `services/strategy_tower.py:105` · `services/summary_bar.py:31`. None expects a sum — each treats it as "the day's opening capital", so the correction is what they all wanted.

**A7 import graph — display-only, proven not asserted:**

- `db_reader` is imported **only** from inside `ops_dashboard/` (`backend/api/dashboard.py`, `backend/readers/config_reader.py`, `backend/services/pipeline_state.py`, plus the four consumers and the GUI tests). **Zero importers outside that package.**
- `main.py`, `core/`, `capital/`, `orders/`, `signals/`, `strategies/`, `screening/`, `allocation/`, `v3_chain/`, `scripts/` — **no reference**.
- `reports/daily_report.py`, `reports/daily_trade_review.py`, `scripts/forward_shadow_record.py` — **0 references each**, so nothing shipped here is reachable from the 16:05 report or the forward-shadow recorder.
- The GUI runs as a separate Waitress process on `127.0.0.1:8500`; the trading service never loads it.

⇒ **no trading, sizing, capital or kill path reads any changed accessor.**

**Parity basis:** pure SQL against the shared DB, with no `mode`/`paper`/`live` branch anywhere in `opening_capital` or its callers — identical in both modes **by construction**, not by two test runs. (Paper and live write INIT the same way; `fund_manager.initialize` has no mode branch.)

## 7. §C — is the SUM-over-INIT pattern a family? **No — but the derivation is duplicated four ways**

Instructed to list, not fix. `SUM(balance_after)` appears **exactly once** in the repo, and it was this one. But "the day's opening capital" is independently re-derived in four places:

| site | rule | verdict |
|---|---|---|
| `core/state_store.py:2539` `get_day_opening_capital` | `ORDER BY ts ASC LIMIT 1` | ✅ correct (1 caller: `system_manager.py:155`) |
| `reports/daily_trade_review.py:1031` and `:2157` | `ORDER BY ts LIMIT 1` | ✅ correct |
| `ops_dashboard/.../db_reader.py:317` | `SUM(...)` | 🔴 **was wrong — fixed here** |
| `reports/daily_report.py:187-189` | `init_rows[0]` | ⚠️ **queued finding, below** |

🆕 **QUEUED (not fixed here): `reports/daily_report.py:187` picks `init_rows[0]` out of an UNORDERED result set.** Its source, `state_store.get_fm_ledger_for_date()` (`:2440`), is `SELECT * FROM fm_ledger WHERE date = ?` with **no ORDER BY**. It works today because SQLite happens to return rows in `rowid` order, which is insertion order, which is chronological — but that is a query-plan accident, not a guarantee: an index scan on the `date` column could legitimately return them otherwise. On a restart day it would silently pick the wrong INIT row. **This is the 16:05 report, so it is deliberately NOT touched in this window.** One fix per commit, and that one deserves its own gate.

## 8. Result

| | |
|---|---|
| main suite | BASE 13 failed / 5,112 passed → MERGE **13 / 5,112**, sets byte-identical (§9) |
| ops_dashboard suite | BASE **372 passed** → **380 passed** (+8 = exactly the 4 new tests × 2 schema versions) |
| files changed | `db_reader.py` (the fix) · `risk_capital.py` (a stale comment) · `conftest.py` + `test_g2b2_screens.py` (fixture truth) · `test_db_reader.py` (+4 tests) |

## 9. Regression detail

Same window, same shell, same interpreters. BASE taken **before any edit**; no remembered baseline used.

**Main suite** (`venv/Scripts/python.exe -m pytest tests/ -q --tb=no -p no:randomly`):

| | failed | passed | skipped | wall |
|---|---:|---:|---:|---:|
| BASE | 13 | 5,112 | 5 | 836.44s |
| MERGE | 13 | 5,112 | 5 | 850.53s |

`comm -13` (MERGE-only) **EMPTY** · `comm -23` (BASE-only) **EMPTY** · failure sets **byte-identical**. Expected: the main suite never imports `ops_dashboard`, so it should not move — and it did not.

**ops_dashboard suite** (`ops_dashboard/.venv/Scripts/python.exe -m pytest tests/`):

| | result |
|---|---|
| BASE | **372 passed** |
| after the fixture correction, **old code still** | **372 passed** ← proves the fixture change is behaviour-neutral |
| RED proof on old code | **6 failed** (the 3 new defect tests × 2 schema versions) |
| MERGE | **380 passed** (+8 = 4 new tests × v41/v42) |

The 13 main-suite failures are the known PC-env set (Mock-config, WSL-stub, instance-lock), unchanged on both sides.
