# §A — `get_fm_ledger_for_date` now returns rows in chronological order (25-Jul-2026)

**Authorised** (VS Code handoff §A, 25-Jul). Off-market, service down, book flat. Closes the finding queued by the 25-Jul `opening_capital` fix (`opening_capital_double_count_25jul2026.md` §7). **This is the 16:05 report path** — scoped and gated accordingly.

---

## 1. The defect

`core/state_store.py:2440` ran

```sql
SELECT * FROM fm_ledger WHERE date = ?
```

with **no `ORDER BY`**. Its sole production caller, `reports/daily_report.py:185-189`, does:

```python
init_rows = [r for r in fm_ledger if r.get("entry_type") == "INIT"]
if init_rows:
    opening_capital = init_rows[0].get("balance_after", 0.0)
```

— it depends on the **first** INIT row being the day's 08:15 seed. SQL guarantees no order without `ORDER BY`; this worked only because SQLite happens to scan in `rowid` order, which is insertion order, which is usually chronological. **A query-plan accident, not a contract.** An index scan on the `date` column could legitimately return them otherwise.

It matters for the same reason the `db_reader` fix mattered: **INIT is not unique per day.** `FundManager.initialize()` writes one INIT row per **process start** (`capital/fund_manager.py:408-448`), so a mid-day restart adds a second INIT row for the same date — 10 of 30 production dates carry more than one. On such a day an arbitrary order yields an arbitrary opening capital, and every derived figure in the report (closing capital, utilisation %, max-drawdown %) is computed against it.

## 2. A2 — accessor, not call site. Why.

**Measured before deciding.** `get_fm_ledger_for_date` has, in the entire repo:

| reference | kind |
|---|---|
| `core/state_store.py:2432` | the definition |
| `reports/daily_report.py:125` | **the one and only production caller** |
| `tests/unit/test_daily_report.py:881` | a mock |

No caller wants an unspecified order, and none exists that would be disturbed by a defined one. So the instruction's "if the accessor is shared by callers that need a different order, fix the call site" does not apply.

**Fixed at the accessor**, because that is the root cause: an accessor that returns rows in an unspecified order while its consumer depends on order. Fixing the call site would leave the next caller to inherit the same trap. The returned list is also stored on `ReportData.fm_ledger` and consumed nowhere else (verified: `.fm_ledger` appears only at the dataclass field, the assignment, and `:187`), so ordering has no other downstream effect.

```sql
SELECT * FROM fm_ledger WHERE date = ? ORDER BY ts ASC, ledger_id ASC
```

`ledger_id` is the tiebreaker, so the order is **total** — deterministic even when two rows share a timestamp — rather than merely "sorted by ts".

## 3. A3 — same semantics as the db_reader fix, already proven

"First INIT is the day's true open" is the rule `state_store.get_day_opening_capital()` and `db_reader.opening_capital()` both use. Its safety was established on production data during the earlier fix and is not re-argued here: `fm_ledger.date` is `substr(ts,1,10)` (a string prefix — no `DATE()`, so no UTC shift); all 58 production INIT timestamps share one offset (`+05:30`) and one width; and a string `ORDER BY ts` matched a `datetime` sort on **every** date, 0 differences. An 11:57 re-seed therefore always sorts after an 08:15 seed.

## 4. A4 — RED first, and not vacuously

The trap here is that a naive test **passes on the broken code**, because the unordered query usually returns rowid order and rowid order is usually chronological. A test that merely inserts two INIT rows in time order proves nothing.

All three new tests in `tests/unit/test_state_store.py` therefore insert the **later-timestamped row first**, so `rowid` order and `ts` order disagree:

| test | on the unordered query |
|---|---|
| `test_get_fm_ledger_for_date_returns_rows_chronologically` | **RED** — `['…T11:57:41…', '…T08:15:26…']` |
| `test_first_init_row_is_the_opening_seed_not_the_restart_reseed` | **RED** — `assert 9858.73 == 9857.3` |
| `test_fm_ledger_order_is_total_not_merely_by_timestamp` | green both sides (the determinism pin) |

The second is the important one: it applies `daily_report.py:187`'s exact expression to real rows and shows the report **would have taken the 11:57 restart re-seed, 9,858.73, as the day's opening capital** instead of 9,857.30.

> **Why the test lives at the accessor, not in `test_daily_report.py`:** that suite drives the report through a `MagicMock` store (`:881` sets `get_fm_ledger_for_date.return_value`). A report-level test would assert against a list the test itself ordered — it would pass no matter what the accessor does. Testing the mock's ordering would be vacuous; the accessor is where the defect and the fix both live.

## 5. A5 — regression, with the report generator called out

Same window, same shell, same interpreter. BASE taken **before any edit**; no remembered baseline.

| | failed | passed | skipped | wall |
|---|---:|---:|---:|---:|
| BASE | 13 | 5,121 | 5 | 829.85s |
| MERGE | 13 | **5,124** | 5 | 828.06s |

- **MERGE-only: EMPTY.** **BASE-only: EMPTY.** Failure sets **byte-identical**.
- **+3 passed = exactly the three new tests.**
- **Report generator specifically:** `tests/unit/test_daily_report.py` + `tests/unit/test_state_store.py` → **150 passed**, including the `generate_daily_report` end-to-end tests.

**Parity:** one SQL clause on a shared table; no `mode`/`paper`/`live` branch in the accessor or its caller. Identical in both modes by construction — paper and live write `fm_ledger` the same way.

## 6. E2 — the A7 gate

- `scripts/forward_shadow_record.py` references `get_fm_ledger_for_date` **0 times** — nothing shipped here is reachable from the 18:15 recorder.
- No reference from `main.py`, `core/` (beyond the definition), `capital/`, `orders/`, `signals/` or `v3_chain/` — **no trading, sizing, capital or kill path calls it.**
- **The report-path change is intended and isolated:** the 16:05 report is the only consumer, and the change is confined to the `ORDER BY` clause of a single read. No report content, layout or sheet logic was touched.

## 7. What this closes

The queued finding from `opening_capital_double_count_25jul2026.md` §7. With this, all four independent derivations of "the day's opening capital" agree and are restart-safe:

| site | rule |
|---|---|
| `core/state_store.py:2539` `get_day_opening_capital` | `ORDER BY ts ASC LIMIT 1` |
| `reports/daily_trade_review.py:1031`, `:2157` | `ORDER BY ts LIMIT 1` |
| `ops_dashboard/.../db_reader.py:317` | `ORDER BY ts ASC LIMIT 1` (fixed 25-Jul) |
| `reports/daily_report.py:187` via this accessor | first row of a **now-ordered** list ✅ |
