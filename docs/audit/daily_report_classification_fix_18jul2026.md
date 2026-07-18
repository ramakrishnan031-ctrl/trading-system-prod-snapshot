# DAILY REPORT — REJECTION CLASSIFICATION DEFECT: ROOT-CAUSE FIX

**Date:** 18-Jul-2026 (Saturday, IST, off-market; system DOWN since Fri 17-Jul, book flat)
**Basis:** `230dd32` (tag `deploy-18jul-q9-batch5` → `a9758b3`), schema v44
**Scope:** ⚠️ **PRODUCTION CODE CHANGE — the first in six batches.** Reporting layer only.
**Files:** `reports/signal_status.py` (new) · `reports/daily_report.py` · `reports/daily_trade_review.py` · `tests/unit/test_signal_status_classification.py` (new)

---

## 0. TL;DR — including a correction to the premise

The defect is real and is fixed at the root: **classification, counting, filtering and grouping
now run off the structured `status`, never off the free-text `rejection_reason`.**

> ⚠️ **BUT THE HEADLINE NUMBER NEEDS CORRECTING, AND I AM NOT GOING TO MANUFACTURE IT.**
> The brief framed this as *"3,098 rejections reported as their exact opposite"* and expected
> **3,098 → 0**. Measured: **the old count was MISLABELLED, not miscounted.**
>
> All **3,098** sizing rejections in the corpus are `REJECTED_SIZING_CONCENTRATION`, and **no
> non-sizing rejection reason contains "capital"** — so the old substring test happened to
> select exactly the right *rows* while calling them the wrong *thing*. The number was right by
> coincidence; the label was wrong, and the constraint that actually binds was named nowhere.
>
> The fix makes the number right **by construction**, corrects the label, and names the binding
> constraint. The old logic would have miscounted the moment any second sizing constraint
> occurred or any non-sizing reason mentioned capital.

What did move, on real data (2026-07-10, 7,814 signals):

| Line | Before | After |
|---|---|---|
| Rejection breakdown | **190 fragmented lines** | **9 named lines** |
| `Rejected (Capital)` | label wrong (1,189 concentration rejections called "capital") | `Rejected (Sizing/Capital)` **1,189** + `Binding Constraint: CONCENTRATION (1189)` |
| `Silent Dead` | **139** | **0** |
| `After Dedup/Excluded` | 7,802 (99.8%) | **7,814 (100.0%)** |

**Two more sites of the same class were found and fixed** beyond the two reported.

---

## 1. §A1 — REGRESSION MAP, AND THE PROGRAMMATIC-CONSUMER QUESTION

| Aspect | Finding |
|---|---|
| Entry point | cron, `python -m reports.daily_report`, 16:05 IST (`config/cron_registry.yaml:487-500`) |
| Reads | `signals`, `trades`, `screener_results`, `system_events`, `recon_log`; config YAMLs; an optional candle CSV |
| Writes | **`reports/output/daily_report_<date>.xlsx` and nothing else** (`wb.save`, `:1761`). No DB writes |
| Monitoring | `scripts/cron_officer.py` tracks the job; its heartbeat is deferred (`_PENDING_REDESIGN_JOBS`) |

**⭐ IS ANY CONSUMER PROGRAMMATIC? NO — verified, and this is what kept the change in scope.**

- `scripts/system_manager.py:403` checks the file **exists and exceeds 2000 bytes**. It does not
  parse content.
- `deploy/hooks/secret_scan.py:147` scans workbooks for secrets — generic, not these counts.
- The ops dashboard serves the file for **human download** (`/api/reports/download`).
- `rejected_capital` and `rejection_reasons` are **local variables** used only to render rows —
  referenced nowhere else in the codebase.
- Section E "Auto Tuning Signals" sounds programmatic but is not: `_generate_tune_suggestions`
  emits advisory **strings** ("consider widening SL by 0.5%") into the sheet, never touches
  `rejection_reason`, and nothing acts on them.

⇒ **Presentation-only change. Behaviour is untouched.**

---

## 2. §A2 — THE FULL SWEEP (not the two known lines)

Nine occurrences of substring-membership classification exist in the reporting layer; **all
nine are in `reports/daily_report.py`.** Classified by what they match and whether they are
wrong:

| # | Line | Predicate | Verdict |
|---|---|---|---|
| 1 | `:464` | `"CAPITAL" in rejection_reason` | 🔴 **FIXED** — free text; mislabels concentration as capital |
| 2 | `:549` | groups by `rejection_reason` | 🔴 **FIXED** — free text; 190 lines instead of 9 |
| 3 | `:465` | `"REJECTED" in status` for *silent dead* | 🔴 **FIXED** — misses `DROPPED_*`/`SKIPPED_*`/`QUEUE_FULL`/`PLACEMENT_FAILED`/`TIMEOUT`; **2,688 signals corpus-wide** mislabelled as silently dead |
| 4 | `:459` | `"DUPLICATE" not in status` | 🔴 **FIXED** — conflates the dedup drop with `REJECTED_DUPLICATE_SYMBOL` |
| 5 | `:548` | `"REJECTED" in status` | 🟡 correct today; **routed through the helper** |
| 6 | `:1541` | `"REJECTED" in status` | 🟡 correct today; **routed through the helper** |
| 7 | `:536` | `"ERROR" in event_type` | ⚪ **reported, not changed** — see below |
| 8 | `:537` | `"CRITICAL" in event_type or "KILL" in ...` | ⚪ **reported, not changed** |
| 9 | `:538` | `"ORPHAN" in check_name` | ⚪ **reported, not changed** |

**Checked and found CLEAN (no fix needed):**
- `ops_dashboard/backend/readers/db_reader.py:_bucket_case_sql()` — already buckets on
  **structured `status`** with explicit `IN`/`GLOB`, documented against a spec. The right
  pattern, and the precedent this fix aligns to.
- `scripts/generate_screened_stocks_csv.py:expand_rejection_reason` — maps from **structured
  status** via `STATUS_MAP`; uses the reason text only for display.
- `scripts/forward_shadow_record.py` — records the reason verbatim as data; no branching.
- `reports/daily_trade_review.py`, `daily_report.py:663/719` — **render** the reason text.
  Rendering is correct and expected; classifying by it is the defect.

### Why 7–9 were reported rather than changed

`system_events.event_type` has only four values in the corpus: `STARTUP` (52), `SHUTDOWN` (51),
`CONFIG_DIFF` (24), `KILL_AUTO_CLEARED` (19). So:

- `:536` "ERROR Count" matches **no** event type — structurally always 0.
- `:537` "CRITICAL Count" matches `KILL_AUTO_CLEARED` via `"KILL" in ...`, so it reports **19
  criticals for what is routine prior-day housekeeping**, and `:540` counts the *same* events
  again under "Kill Switch Events".

That is the same *pattern*, but unlike #1–#4 it is **ambiguous intent, not demonstrable
misclassification** — a kill arguably *is* critical, and showing it under both labels may be
deliberate. Changing correct-but-debatable code on the eve of a live boot is unnecessary risk.
**Recorded for Rama; unchanged.**

---

## 3. §A3 — THE STRUCTURED VOCABULARY

`signals.status` families (corpus-wide, 32,928 priced signals):

- `REJECTED_*` — **29,966**. Includes `REJECTED_SIZING_*`, `REJECTED_SCORE_<nn>`,
  `REJECTED_DAILY_TRADES`, `REJECTED_OPEN_POSITIONS`, `REJECTED_STRATEGY_*`,
  `REJECTED_SHADOW_INNING_ACTIVE`, `REJECTED_CIRCUIT_PROXIMITY`, `REJECTED_ENTRY_THROTTLED`,
  `REJECTED_DUPLICATE_SYMBOL`, …
- `DROPPED_*` / `SKIPPED_*` / `QUEUE_FULL` / `PLACEMENT_FAILED` / `TIMEOUT` — **2,688**.
  Terminal, but *not* `REJECTED_*`. This distinction is what defect #3 got wrong.
- Qualified: `PROCESSED`, `TRADED`, `RESERVED`, `PASSED`, …

**Sizing family, all-time:** `REJECTED_SIZING_CONCENTRATION` **3,098** — and nothing else. Per
batch 4's reachability table, the other sizing constraints (`CAPITAL`, `ZERO_MULTIPLIER`,
`BELOW_MIN`, `POSITION_VALUE_CAP`, `REJECTED_LOT_SKEW`, `INVALID_SL_DISTANCE`,
`QTY_EXPLOSION_GUARD`) are **unreachable under current production config**.

**Proposal, and why:** the breakdown lists only families that **actually occurred** — a report
enumerating ten categories of which nine can never fire is noise. Unreachable constraints
simply do not appear; if one ever fires it appears automatically, correctly named. The one
family deliberately collapsed is `REJECTED_SCORE_<nn>` (~40 distinct statuses, one per score),
which would otherwise fragment the breakdown on the *status* side just as the reason text did.

---

## 4. §A4 — THE WRONGNESS, QUANTIFIED (read-only, raw sqlite, `mode=ro`)

| Measure | Value |
|---|---|
| Old-logic count (`"CAPITAL" in reason`), all-time | **3,098** |
| `REJECTED_SIZING_*` (what the row now reports) | **3,098** |
| `REJECTED_SIZING_CAPITAL` (literal capital) | **0** |
| Non-sizing rows the old logic also swept in | **0** |
| Signals mislabelled "silently dead" | **2,688** |
| Breakdown lines, 2026-07-10 | **190** for 7,655 rejections |

⇒ On this corpus the old predicate selected the right rows for the wrong reason. It is a
**labelling** defect with a **fragility** defect behind it — not, today, a miscount.

---

## 5. §A5 — THE CORRECTED OUTPUT, AND WHAT WAS PRESERVED

**Nothing the operator had was discarded.** Sheet `1_Signals` renders **every** signal with its
full `rejection_reason` text (`daily_report.py:719`), so collapsing the Section D summary from
190 lines to 9 removes duplication, not information: the symbol and arm values remain, per
signal, where per-signal detail belongs.

Design decisions:
1. **Breakdown groups by status family** — 190 → 9 readable lines.
2. **`Rejected (Capital)` → `Rejected (Sizing/Capital)`**, computed from `REJECTED_SIZING_*`.
   A row meaning *strictly* `REJECTED_SIZING_CAPITAL` would read **0 every single day** — true
   but useless. The operator's question is "how many died at the sizing/capital stage", so the
   family is the honest metric.
3. **New `Binding Constraint` row** names the dominant sizing constraint and its count —
   the fact the old report never surfaced. *If Rama prefers the strict-capital reading, it is a
   one-line change; the helper already exposes `sizing_constraint()`.*
4. **`Silent Dead`** now means what it says: no *known* disposition. It can only be non-zero if
   a status appears that the vocabulary does not know — making it a genuine schema-drift alarm
   rather than a noise figure.

---

## 6. §B — THE FIX

**Root cause, not symptom.** All classification routes through **`reports/signal_status.py`** —
the single place statuses are interpreted: `bucket()`, `is_rejected()`,
`has_explicit_disposition()`, `is_dedup_duplicate()`, `is_sizing_rejection()`,
`sizing_constraint()`, `family()`.

**Anti-duplication (Rule #4), honoured rather than asserted:** `daily_trade_review._signal_bucket`
**already had the right pattern** — structured prefixes plus an explicit `unmapped` catch-all
documented as a schema-drift alarm. Rather than write a second classifier, it was **promoted**
into the shared module; `daily_trade_review.py` now delegates to it
(`_signal_bucket = sig_status.bucket`), asserted by a test. **Exactly one implementation.**

### §B6 — Parity

The report has **no paper/live divergence**: `daily_report.py` takes no mode argument and
contains no `paper`/`live` branch; it reads the same tables in both modes and is invoked by one
cron entry. Nothing to fix on the other side.

---

## 7. §B5 — PROVEN TO BITE

| Plant | Break | Result |
|---|---|---|
| **A** | free-text classification restored inside `sizing_constraint()` | `AssertionError: assert 'CAPITAL' == 'CONCENTRATION'` + *"the new logic reproduces the defect"* |
| **B** | `REJECTED_SCORE_<nn>` collapse removed | `AssertionError: REJECTED_SCORE_29` |
| **C** | `has_explicit_disposition` reverted to REJECTED-only | *"DROPPED_DEDUP carries an explicit outcome and must not count as silently dead"* (and for `SKIPPED_QUOTE_UNAVAILABLE`, `QUEUE_FULL`, …) |
| **D** | the original `:464` line reintroduced **as code** | the source guard fires, naming file, line and the offending text |

Restored afterwards: **17 passed**, `reports/` clean.

**The tests found two defects in themselves before they found any in the code** — the pattern
that has now held for three consecutive batches:
- `has_explicit_disposition` initially excluded `QUEUE_FULL`/`TIMEOUT`, which the parametrised
  test caught immediately; the semantics were sharpened to "any *known* outcome".
- The reintroduction guard fired on **my own explanatory comment** quoting the retired
  predicate. Scoped to skip comment lines — documenting the old defect must stay legal;
  branching on free text must not.

Both guards carry anti-vacuity assertions: one proves the real reason string still contains
`capital_qty=` (or the whole class proves nothing), the other proves the regex **would** have
matched the historical defect line.

---

## 8. §C — BEFORE / AFTER ON REAL DATA, EVERY DIFF ACCOUNTED FOR

Run on the VM against **`/tmp/b6/scratch.db`, a copy** — never the live DB — for **2026-07-10**
(7,814 signals, the busiest day in the corpus).

> ⚠️ **A FALSE PASS I CAUGHT AND HAD TO REDO.** The first comparison reported **0 differing
> cells**. That was not a clean result — with `python -m`, the *current directory* precedes
> `PYTHONPATH` on `sys.path`, so the "after" run had silently loaded the **old** module.
> Verified by printing `dr.__file__` and `hasattr(dr, 'sig_status')` → `False`. Both runs were
> re-done with the overlay genuinely first on `sys.path`, confirmed the same way before use.
> *A green check is evidence only if it could have been red.*

A second artefact then appeared — 26 diffs in `6_Strategy_Analysis` (`INTRADAY` → `—`). **Cause:
my harness, not the change.** `build_taxonomy_map()` (`daily_report.py:1493`) is called with no
argument, so it resolves `config/` **relative to cwd** and ignores `--config-dir`; the overlay
run had a different cwd. Re-running *both* sides from an identical cwd removed it entirely.
*(Minor pre-existing finding recorded below — not fixed here.)*

**Isolated result — same cwd, same config, only the fix differing:**

```
populated cells: before=174504  after=174144
DIFFERING CELLS: 429      sheets touched: {'0_EOD_Dashboard': 429}
```

**Every one of the other six sheets is byte-identical.** By label:

| Change | Detail |
|---|---|
| **191 labels removed** | `Rejected (Capital)` (renamed) + **190 fragmented breakdown lines** |
| **11 labels added** | `Rejected (Sizing/Capital) 1189` · `Binding Constraint CONCENTRATION (1189)` · **9 breakdown families** |
| **2 values changed** | `After Dedup/Excluded` 7802 (99.8%) → **7814 (100.0%)** · `Silent Dead` **139 → 0** |
| **41 labels unchanged** | P&L, costs, win rate, best/worst trade, capital utilisation, drawdown, ERROR/CRITICAL/kill/orphan counts — all identical |

The remaining cell-level diffs are the **one-row downward shift** caused by inserting the
`Binding Constraint` row. Nothing outside classification moved. The corrected breakdown:

```
REJECTED_DAILY_TRADES             2891
REJECTED_SCORE                    2218
REJECTED_SIZING_CONCENTRATION     1189   <- named for the first time
REJECTED_SHADOW_INNING_ACTIVE      840
REJECTED_CIRCUIT_PROXIMITY         194
REJECTED_OPEN_POSITIONS            173
REJECTED_STRATEGY_POSITION_LIMIT    88
REJECTED_ENTRY_THROTTLED            50
REJECTED_DUPLICATE_SYMBOL           12
```

---

## 9. ROLLBACK PLAN

**One commit, one step.** The change is four files in `reports/` plus one test file, with no
schema, config, flag or trading-path component.

```
git revert b3a960d && git push origin main
```

Effect: the report reverts to its previous output. Nothing else in the system is affected —
no state is written by this code path, so there is nothing to unwind. If the 16:05 cron has
already run, the next day's report simply renders the old way; the xlsx is regenerable for any
past date with `--date`.

---

## 10. REGRESSION + DEPLOY

### 10.1 Regression — same window, correctly-seeded base

Base taken with the standing sharpened rule: `git checkout <base> -- <files>` in the **MAIN
TREE** (preserving git-ignored runtime data), the two new files moved aside, `__pycache__`
cleared so "absent" is a fact rather than a half-truth, `git stash` unused (stash list verified
empty). Both runs 21:43–22:20 IST.

| Run | Failed | Passed | Skipped | xfail | **Collected** |
|---|---|---|---|---|---|
| **BASE** — `deploy-18jul-q9-batch5` files restored, new files absent | 12 | 4982 | 5 | 1 | **5000** |
| **MINE** — `b3a960d` | 12 | 4999 | 5 | 1 | **5017** |

- **Both `comm` directions EMPTY ⇒ the failure sets are IDENTICAL, ZERO ATTRIBUTABLE.**
- **Totals reconcile exactly: 5000 + 17 = 5017**, matching the stated 5000 baseline.
- **`xfailed = 1`, `XPASS = 0`** ⇒ batch 3's E4/W10 contract xfail still genuinely xfailing.
- The 12 are the documented PC-env / calendar-gated set (`test_main.py` ×4,
  `test_order_placer_fix061` ×4, Saturday `test_daily_trade_review`, `test_interactive_startup`,
  `test_fix181`, `test_phase17_batch2`).

### 10.2 Deploy verification

| Step | Result |
|---|---|
| Tag `deploy-18jul-report-classification` | → `b3a960d` |
| Fresh VM backup `pre_deploy_report_classification_20260718.db` | **SOUND**: `quick_check=ok` · schema **44** · **361** trades · **0** FK |
| Push `main` + tag | `230dd32..b3a960d`, post-receive checkout OK |
| Bare HEAD == re-derived local HEAD | `b3a960d` == `b3a960d` ✓ **PC == VM** |
| Deployed module carries the fix | `hasattr(dr, 'sig_status')` → **True** |
| **New tests ON THE VM** | **17 passed** |
| **⭐ REPORT GENERATES ON THE VM** | ✅ 1,167,898-byte xlsx produced from the deployed code |
| Deployed output verified | `Rejected (Sizing/Capital) 1189` · `Binding Constraint CONCENTRATION (1189)` · `Silent Dead 0` · **9-line breakdown** |
| Schema / integrity / FK | **v44 unchanged** · `quick_check=ok` · **0** violations · 361 trades |
| Kill-switch state | **INACTIVE** (unchanged) |
| Services | `trading-system` inactive (expected) · `alert-watcher` active · `gui-dashboard` active |
| **Live DB untouched** | mtime still `Jul 18 09:20` (pre-dating this work); every §C run used `/tmp/b6/scratch.db`, a copy |

---

## 11. FINDINGS RECORDED, NOT FIXED

| Finding | Why not now |
|---|---|
| `:537` "CRITICAL Count" counts `KILL_AUTO_CLEARED` (routine housekeeping), double-counted with `:540` "Kill Switch Events"; `:536` "ERROR Count" matches no event type | Ambiguous intent, not demonstrable misclassification. Rama's call |
| `build_taxonomy_map()` (`:1493`) ignores `--config-dir` and resolves `config/` relative to cwd | Pre-existing; harmless under cron (which runs from the tree root); fail-safe already shows `—` |
| The report is slated for retirement with `fetch_daily_candles` (X3), blocked on W1 | Unchanged by this fix |

---

*Reporting layer only. No trading-path file, config, schema or flag was changed. The sweep found
no instance of this pattern being used to make a decision — only to render.*
