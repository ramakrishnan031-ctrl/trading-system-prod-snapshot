# W8 `closure_source` backfill — the run record (28-Jul-2026)

**Status: VERIFIED LIVE.** Executed against production `data_store/trading_system.db` on the VM at
**2026-07-28 09:09:15 IST**, after v45 was confirmed VERIFIED LIVE the same morning. Approved by
Rama 27-Jul (option **(a)** populate `closure_source`, **not** (b) rewrite `exit_reason`); the
execution slot and the cherry-pick were authorised 28-Jul 09:05.

Script: `scripts/backfill_closure_source_w8.py` (`90c119d`, cherry-picked from `ddcb678`).
Interpreter: `/home/ubuntu/systems/venv/bin/python` — the same one the service and every cron use.

---

## 1. Preconditions, checked not assumed

| precondition | evidence |
|---|---|
| v45 migration ran | `migration complete: v44 -> v45 (1 tables rebuilt)` @ 08:15:16.875 |
| `closure_source` exists | `pragma_table_info(trades)` = 59 cols; both new columns present |
| DB undamaged | `PRAGMA integrity_check` = `ok` |
| `trades` row count unchanged | 430 (was 430) |
| service not disturbed | `active`, `NRestarts=0`, `ActiveEnterTimestamp` still 08:15:15 |
| crontab unchanged by the push | md5 `66d438165605216fc94b7dc0c635f36e` — byte-identical |

⚠️ `fm_ledger` read **2538** against a card that said 2537. Resolved **row-level, not by count**:
2537 rows dated ≤ 2026-07-27 (unchanged) **+ 1** INIT row written by the 08:15 boot. `fm_ledger`
was never rebuilt by the migration, and one INIT row per process start is its defined behaviour.
Not a loss. (This is the [artifact-baseline] rule: within-batch equality is not cross-batch
equality — the live DB advances on its own.)

## 2. What the run did

```
CLOSED_MANUAL rows            : 41
  closure_source BEFORE       : {'NULL': 41}
  would WRITE (1 own leg)     : 35  {'OWN_EOD': 22, 'OWN_SL': 9, 'OWN_TGT': 4}
  LEFT NULL (no own leg)      : 6
  backup taken                : data_store/backups/pre_w8_backfill_20260728_090915.db (134,221,824 B)
  receipt (the exact undo set): data_store/w8_backfill_receipt_20260728_090915.json
  closure_source AFTER        : {'OWN_EOD': 22, 'OWN_SL': 9, 'OWN_TGT': 4, 'NULL': 6}
```

The dry-run was run and **read** first, under both the system interpreter and the canonical venv
interpreter — byte-identical output, and identical to the `--commit` run's pre-write block.

### The six left NULL — named, so the count is auditable rather than asserted

| date | symbol | trade_id | why untouched |
|---|---|---|---|
| 2026-06-15 | SULA | `trd_763801ae…` | predates exit-leg recording (first SL/TGT row is 17-Jun) |
| 2026-06-16 | GICRE | `trd_08278985…` | `qty_filled=0` — a cancelled entry; there was never a position |
| 2026-06-16 | AGARIND | `trd_352c25b3…` | predates exit-leg recording |
| 2026-06-17 | EVEREADY | `trd_a2f6fc36…` | genuine external-close candidate |
| 2026-06-19 | AEROENTER | `trd_86abe1a3…` | genuine external-close candidate |
| 2026-06-19 | RCF | `trd_bfd823f2…` | genuine external-close candidate |

⚠️ **It is SIX, not five.** Both operator cards said five while naming six — the three genuine
candidates plus the two era artifacts, omitting GICRE. Verifying against "5 untouched" would have
read a correct run as a failure. The measured count wins over the document.

They stay **NULL**, never `EXTERNAL_UNATTRIBUTED`: NULL is "we do not know", which is honest;
`EXTERNAL_UNATTRIBUTED` is a positive claim the record cannot support.

`exit_mechanism` is left NULL on all 430 rows, deliberately — no canonical
`order_type → exit_mechanism` mapping exists, and inventing one inside a backfill is the
second-classifier divergence W8 exists to retire.

## 3. Independent verification (not the script's self-report)

| check | result |
|---|---|
| distribution on `CLOSED_MANUAL` | `OWN_EOD 22 · OWN_SL 9 · OWN_TGT 4 · NULL 6` = 41 |
| nothing outside `CLOSED_MANUAL` touched | FAILED 190, CLOSED 145, REJECTED 45, CANCELLED 9 — all `closure_source IS NULL` |
| `exit_mechanism` non-null anywhere | 0 |
| `trades` row count | 430 |
| `PRAGMA integrity_check` | ok |
| service | `active`, NRestarts=0, same 08:15:15 boot |

## 4. ⭐ The 24-Jul figures, re-run against the corrected data

The 24-Jul trailing-stop study groups by `exit_reason`. The backfill deliberately did **not** touch
`exit_reason`, so the re-run groups by the corrected label instead:
`closure_source` where present (`OWN_SL→SL_HIT`, `OWN_TGT→TGT_HIT`, `OWN_EOD→EOD`), else `exit_reason`.

The predictions were computed on 27-Jul by *reassigning in the analysis*. They are now checked
against data that says it itself.

**Method validated first.** Every BEFORE figure reproduces the 27-Jul impact report exactly — so a
divergence in the AFTER column would be the data's, not the method's.

### §D.1 — arm rates (capped MFE ≥ 0.5 %, from `trade_excursions`)

| group | BEFORE | AFTER |
|---|---|---|
| SL_HIT | n=66, armed 27 (40.9 %) | **n=74, armed 32 (43.2 %)** |
| TGT_HIT | n=47, armed 45 (95.7 %) | n=51, armed 49 (96.1 %) |
| MANUAL | n=26, armed 20 (76.9 %) | **gone — 0 rows** |
| EOD *(new group)* | — | **n=14, armed 11 (78.6 %)** |
| **ALL** | **139, armed 92 (66.2 %)** | **139, armed 92 (66.2 %)** |

⭐ **The ALL row is invariant, as predicted** — reassignment renames buckets, it moves no trade in
or out. And the `MANUAL` bucket vanished entirely, confirming it held **zero** genuine external
closes.

### §D.4 — the counterfactual population

| | n | armed | rate | never green |
|---|---:|---:|---:|---:|
| as studied (SL_HIT) | 66 | 27 | 40.9 % | 9 |
| **after correction** | **74** | **32** | **43.2 %** | **10** |

**Predicted 74 / 32 / 43.2 % / 10. Measured 74 / 32 / 43.2 % / 10.** The rescued-loser population
grows 27 → 32, so the *"median +1.84 R/trade, sum +46.7 R"* upper bound gets **larger**.

### §D.3 — the winners' uncapped MFE (`analytics.candles`, entry → 15:20, direction-aware R)

| slice | BEFORE | predicted AFTER | **measured AFTER** |
|---|---|---|---|
| whole-book | +2.9329 R (n=52) | +2.78 R (n=56) | **+2.7797 R (n=56)** ✓ |
| in-sample ≤13-Jul | +3.0639 R (n=36) | +3.01 R (n=38) | **+3.0067 R (n=38)** ✓ |
| OOS >13-Jul | +2.3111 R (n=16) | +2.20 R (n=18) | **+2.1977 R (n=18)** ✓ |
| OOS >14-Jul | +2.4056 R (n=15) | +2.22 R (n=17) | **+2.2165 R (n=17)** ✓ |

**Eight of eight, to two decimals** — four BEFORE (method validation) and four AFTER (the
prediction). No divergence, so the reassignment logic and the backfill logic agree.

### Does any conclusion move? No.

The sentence the exits thread was closed on — *"out-of-sample winners median ~2.2–2.4 R, below
in-sample but clearly above the 1.5 R exit"* — now reads **2.20–2.22 R (n=17–18)** off the data
itself. Attenuated at the third decimal, unchanged in substance. **The exits thread stays CLOSED**;
nothing here is grounds to reopen it.

## 5. Reversibility — structural, not procedural

The script took its own backup (SQLite **online backup API**, not a file copy — a `cp` of a live
WAL database can copy a torn page), verified it with `integrity_check` and a non-zero row count
*before* writing, and wrote the receipt **immediately after the write and before verification** so
an undo path exists from the instant anything changes.

```
python scripts/backfill_closure_source_w8.py \
  --revert-file data_store/w8_backfill_receipt_20260728_090915.json
```

The revert touches only rows whose recorded prior value was NULL, so a value the live path has
written since cannot be clobbered by an undo.

## 6. Recorded, not acted on

- ⚠️ **`docs/audit/check1_manual_mislabel_impact_27jul2026.md` — the report these predictions come
  from — is NOT on `main`.** It is stranded on `check1-classify-27jul`. Its numbers are quoted
  inline above so this record stands alone, but the antecedent document is missing from the
  deployed history.
- 🧊 **A stale `.pyc` outranked its source.** `scripts/__pycache__/backfill_closure_source_w8.cpython-311.pyc`
  existed on `main` while the `.py` did not — a compiled artifact present where its source is
  absent can make a module look deployed when it is not. Worth remembering as a search hazard:
  `find`/`ls` hits on `__pycache__` are not evidence the module is there.

Related: `docs/audit/trailing_stop_never_fired_2026-07-24.md` · `docs/closure_source_contract.md` ·
`core/closure_source.py`
