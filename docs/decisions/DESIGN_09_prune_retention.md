# DESIGN — #09 Prune retention, Option B (keep `REJECTED_*`)

**DESIGN ONLY. No code, config, schema or cron changed. For ChatGPT review before implementation.**
Option B is **decided** (Rama, "better future analysis"). This document implements it and does **not**
re-argue it. Everything below is measured from the live DB read-only (`mode=ro`) or quoted from code.

> ### ⚠️ The finding that shapes the whole design
> After Option B, the prune's remaining predicate is `status IN ('EXPIRED', 'DUPLICATE')` — and
> **neither status is ever written to `signals`.** Both are HTTP-response / pipeline-check vocabulary,
> not persisted statuses (§A3). The live table holds **0** of them across 36,004 rows.
> **⇒ Implemented naively, `_cleanup_old_fingerprints` becomes a permanent no-op** that runs daily at
> 15:50 and can never delete anything. That is a job which cannot fail — the same class as the vacuous
> checklist A3. §D2 addresses it; it needs a decision, not a silent acceptance.

---

## A. What the prune does today

### A1. The predicate, quoted (`scripts/eod_cleanup.py:234-236`)
```python
where = ("(status IN ('EXPIRED', 'DUPLICATE') OR status GLOB 'REJECTED*') "
         "AND fingerprint_date < ? "
         "AND NOT EXISTS (SELECT 1 FROM trades t WHERE t.signal_id = signals.signal_id)")
```
| Element | Value | Site |
|---|---|---|
| **Deletes** | `EXPIRED`, `DUPLICATE`, and the whole `REJECTED*` family | `:234` |
| **Keeps** | everything else — the trade audit trail (`PROCESSED`, `PLACEMENT_FAILED`, `RESERVED`, …) | `:234` |
| **Window** | `fingerprint_date < today − signal_retention_days`, default **90** | `:216`, `config/system_config.yaml:307` |
| **Capital guard** | `NOT EXISTS (trades)` — never delete a signal linked to P&L history, regardless of status | `:236` |
| **Cascade** | children deleted first (FK-safe): `screener_results`, `gate_state`, `shadow_trades`, `sr_detector_results`, `retest_state` | `:198-204`, `:261-263` |
| **Batching** | 2,000 signal_ids per COMMITted transaction | `:208`, `:250` |
| **Cron** | `50 15 * * 1-5` (15:50 Mon–Fri) | `deploy/cron/*:94` |
| **Dry-run** | counts with the *same* predicate as the DELETE (preview == action) | `:238-246` |

### A2. Was the status-selectivity deliberate? — **Verified, and the inherited framing is half wrong**
The census framed this as "an accident of the predicate". The git history says something more precise:

| Commit | Date | What it did |
|---|---|---|
| *(pre-P10)* | — | `status IN ('EXPIRED', 'DUPLICATE', 'REJECTED')` |
| **`2e61fad`** | **14-Jul-2026** | **`fix(q5/P10): eod_cleanup prunes REJECTED_* fingerprints`** — replaced the bare `'REJECTED'` with `GLOB 'REJECTED*'` |
| `3907a5c` | later | added the `NOT EXISTS (trades)` capital guard + children-first cascade + config-driven 90d |

P10's own commit message states the original defect:

> *"`_cleanup_old_fingerprints` deleted `status IN ('EXPIRED','DUPLICATE','REJECTED')`, but the pipeline
> persists `REJECTED_<check>` … and NEVER a bare `'REJECTED'`. So every reject fingerprint leaked
> forever — unbounded growth in the signals table, which the dedup path reads."*

**⇒ The precise reading:** the *intent* to prune rejects was deliberate from the beginning (bare
`'REJECTED'` was in the original list). What was accidental was the **predicate not matching the actual
status vocabulary** — so rejects were *retained* by accident. P10 deliberately fixed that, **six days
ago**.

**Therefore Option B is a deliberate policy reversal of `2e61fad`, not the correction of an accident.**
That framing belongs in the record. It does not change the decision; it changes what the change *is*.

### A3. Status vocabulary — measured, and two clauses are dead
`REJECTED*` is a family of ~40 statuses, dominated by a **per-score** family (`REJECTED_SCORE_29` …
`REJECTED_SCORE_59`) plus gate names (`REJECTED_STRATEGY_CONTROL` 6,749, `REJECTED_DAILY_TRADES` 5,146,
`REJECTED_SHADOW_INNING_ACTIVE` 3,123, `REJECTED_SIZING_CONCENTRATION` 3,099, …).

| Slice | Rows | Share |
|---|---|---|
| `status GLOB 'REJECTED*'` | **32,968** | **91.6%** |
| everything else | 3,036 | 8.4% |
| **`status IN ('EXPIRED','DUPLICATE')`** | **0** | **0.0%** |

**Why zero, verified in code — not an artefact of the sample:**
- `signals/signal_processor.py:696` raises `_PipelineReject("EXPIRED", …)`, and rejects persist as
  `REJECTED_<check>` ⇒ an expired signal is stored as **`REJECTED_EXPIRED`**, never `EXPIRED`.
- `signals/webhook_receiver.py:827` and `:846`/`:916` `return {"status": "EXPIRED"/"DUPLICATE"}` — these
  are **HTTP response dicts returned *before* the INSERT** at `:864`. They never reach the table.

⇒ `signals.status` **cannot** be `'EXPIRED'` or `'DUPLICATE'`. The `IN (…)` clause is the *same class of
bug* as the pre-P10 bare `'REJECTED'` — still present, masked only because the `GLOB` clause made the job
appear to work. The config comment at `system_config.yaml:307` repeats the same false vocabulary.

⚠️ Also unpruned and unnoticed: **`SKIPPED_QUOTE_UNAVAILABLE` (2,667 rows, back to 12-Jun)** matches no
clause and is retained forever today. Not in scope; recorded.

### A4. When does the prune next delete anything? — **~10-Sep-2026. There is no deadline.**
Cutoff today = `2026-07-20 − 90d` = **2026-04-21**. Earliest `fingerprint_date` in the table =
**2026-06-12**, which is *after* the cutoff ⇒ **0 rows deleted today**, and every day until
`fingerprint_date 2026-06-12 < today − 90d`, i.e. **run date > 2026-09-10**.

**Consequence:** the live DB already reflects "no pruning at all". The two options are indistinguishable
in the data until ~10-Sep, so this change can be deployed, reviewed or deferred at leisure.

---

## B. The storage number — measured, not estimated

Measured with `dbstat` (real page usage), not row-length arithmetic.

### B1. Per-retained-signal footprint
| Component | Table bytes | REJECTED share | Bytes / REJECTED row |
|---|---|---|---|
| `signals` | 38,526,976 | 91.6% | **1,070 B** |
| `signals` indexes (5) | 8,450,048 | 91.6% | **235 B** |
| `screener_results` (cascade child) | 22,712,320 | 87.6% (21,536/24,572) | **603 B** |
| `gate_state`, `shadow_trades`, `retest_state` | empty | — | 0 |
| `sr_detector_results` | 999,424 | 0 linked | 0 |
| **Total** | | | **≈ 1,908 B ≈ 1.86 KB** |

⚠️ **The cascade is 32% of the cost.** A "rows × row-size" estimate on `signals` alone would understate
it by a third, because keeping a rejected signal also keeps its `screener_results` child.

### B2. Three ways
**Observed rate:** full trading days, `REJECTED*` only — 09-Jul 7,635 · 10-Jul 7,655 · 13-Jul 6,710 ·
14-Jul 3,492 · 15-Jul 4,175 · 20-Jul 3,002 ⇒ **mean ≈ 5,445 rejects/trading day**.

| | Status quo (90-day window) | **Option B (keep forever)** |
|---|---|---|
| **Rows** | bounded ≈ 62 trading days × 5,445 ≈ **338,000** | **unbounded**, +5,445/day |
| **MB** | bounded ≈ **644 MB** | **+9.9 MB per trading day** ≈ 208 MB/month ≈ **2.5 GB/year** |
| **Share of the live DB** | — | rejects **already** occupy ≈ **62.9 MB of 91.58 MB = 69%** |

**The single most judgeable sentence:** rejected signals are *already* 69% of the live database, and
Option B removes the ceiling on that share.

### B3. Trajectory — linear in signal volume, but with a super-linear tail risk
Growth is **linear**: bytes/day = (signals/day) × (reject rate ≈ 91.6%) × 1.86 KB. Doubling scanner
volume doubles the rate. Two caveats:
- The `REJECTED_SCORE_<n>` family creates **one status value per integer score**, so `idx_signals_status`
  cardinality grows with the score range, not just row count. Currently 1.16 MB; benign, worth watching.
- The cascade multiplier depends on features that are currently **off**. `gate_state`, `shadow_trades`
  and `retest_state` are empty today; if any is enabled, the per-signal footprint rises above 1.86 KB and
  the growth rate rises with it. **This is the super-linear risk: not more signals, but more children per
  signal.**

### B4. Disk context — **yes, materially, and the amplifier is the backups**
`/dev/sda1`: 96 GB, **72 GB free (26% used)**. `data_store/backups` = **14 GB across 63 files**, on the
**same disk** — Rama's open item #9.

A **full daily copy** is taken by cron (`sqlite3 … ".backup … trading_system-$(date).db"`), pruned by
`scripts/backup_retention.py`; **15** `trading_system-*` dailies are currently retained.

⇒ **The live DB is amplified ≈ 15× on the same disk.** At Option B's ~2.5 GB/year, after one year the
live DB ≈ 2.6 GB and its retained dailies ≈ **39 GB**, against 72 GB free — before `analytics.db` and
the `pre_deploy_*` snapshots (one of which is already 357 MB). **The live-DB growth alone is not the
risk; the 15× backup multiplication on a single disk is.** This strengthens, and puts a number on,
open item #9.

---

## C. Regression map

### C1. Consumers of `signals` (production only, tests excluded)
| Site | Query shape | Date-scoped? |
|---|---|---|
| `core/state_store.py:782` | `COUNT(*) … WHERE SUBSTR(received_at,1,10) = ?` | ✅ |
| `core/state_store.py:2411` | `SELECT * … WHERE DATE(received_at) = ?` | ✅ |
| `core/state_store.py:1266`, `:2796`, `:2857` | `UPDATE … WHERE signal_id = ?` | ✅ (by id) |
| `ops/control_tower/freshness.py:88`, `:90` | `WHERE substr(received_at,1,10)=?` | ✅ |
| `ops_dashboard/.../db_reader.py:179, 411, 480, 611, 762, 777, 1326, 1345` | `WHERE received_at LIKE ?` (today) | ✅ |
| `ops_dashboard/.../db_reader.py:1387` | `WHERE signal_id = ?` | ✅ (by id) |
| `ops_dashboard/.../db_reader.py:170` | `WHERE status='DUPLICATE' AND received_at LIKE ?` | ✅ — **but see C2** |
| `core/state_store.py:161`, `:165` | ⚠️ **docstring usage example, not code** — verified | n/a |

### C2. Does anything assume `REJECTED_*` rows are absent after 90 days? — **Searched: NO.**
**Every** production consumer is scoped by `received_at` / `fingerprint_date` / `signal_id`. The only
unscoped-looking query (`SELECT * FROM signals WHERE status='TRADED'`) is a **docstring example** in
`state_store.py:161`, confirmed by reading its context. So the failure mode this section exists to hunt
— a query that currently works only because old rejects are gone — **does not appear to exist**.

**And the dedup path, which P10 named, is structurally immune.** The dedup key is a
**UNIQUE index on `signals(fingerprint, fingerprint_date)`**, and the `IntegrityError` recovery query is
`WHERE fingerprint = ? AND fingerprint_date = ?` (`webhook_receiver.py:864-890`). A retained row from
>90 days ago carries a different `fingerprint_date` and **cannot collide** with today's insert; the
fingerprint itself also embeds a 300-second `epoch_bucket` (`:853-855`), so the hash differs across days
too. **Two independent layers.** P10's concern was index *size*, not dedup *correctness* — an important
precision, since "the dedup path reads it" reads like a correctness risk and is not one.

> ⚠️ **Related defect found while mapping (NOT in scope, queued).**
> `ops_dashboard/backend/readers/db_reader.py:170` counts `WHERE status='DUPLICATE'` — a status that
> **never exists** (§A3). It therefore returns **0 always**, and its own docstring says the pipeline
> *"derives the residual reject bucket as (rejected_total − this)"* — a subtraction of a constant zero.
> This is the **third** instance of the dead-status class (pre-P10 bare `'REJECTED'`; the prune's
> `IN ('EXPIRED','DUPLICATE')`; this). Same shape as the daily-report misclassification. **Queued.**

### C3. Performance
- **Index depth:** `idx_signals_fingerprint_today` is 3.34 MB / 36,004 rows. B-tree fanout is high, so
  10× the rows adds roughly **one page level** to a lookup — negligible per-insert.
- **The real effect is cache residency, not depth.** At ~2.5 GB/year the fingerprint index leaves the
  SQLite page cache and OS buffer cache, turning dedup INSERT collisions from memory hits into disk
  reads. This is gradual and worth a periodic check, not a cliff.
- **Full-table scans:** none found unscoped (§C2), so no consumer degrades from table growth.
- **`VACUUM`:** the Sunday `db_retention --vacuum` rewrites the whole file; its runtime scales with DB
  size and would grow from seconds to minutes at GB scale.

### C4. Reversibility — **trivially reversible, confirmed**
Option B **destroys nothing**; it only declines to delete. To reverse, restore the `GLOB 'REJECTED*'`
clause and let one scheduled run catch up — the batched loop (`:249-267`, 2,000/txn) is built for exactly
that backlog case ("a large backlog (~108k) clears in COMMITted chunks"). Nothing is lost by waiting, and
no migration is involved either way. **The decision is reversible at any time, at the cost of one run.**

---

## D. The change

### D1. The proposed diff — one predicate
```diff
--- a/scripts/eod_cleanup.py
+++ b/scripts/eod_cleanup.py
@@ -234,3 +234,3 @@
-    where = ("(status IN ('EXPIRED', 'DUPLICATE') OR status GLOB 'REJECTED*') "
+    where = ("status IN ('EXPIRED', 'DUPLICATE') "
              "AND fingerprint_date < ? "
              "AND NOT EXISTS (SELECT 1 FROM trades t WHERE t.signal_id = signals.signal_id)")
```
Plus a corrected comment block at `:220-233` (which currently explains *why* rejects are pruned) and the
config comment at `system_config.yaml:307` (which lists `REJECTED_*` in the keep-window description).

### D2. ⭐ The anti-vacuity problem — this needs a decision, not a default
The diff above leaves a predicate matching **statuses that are never persisted** (§A3), so the job
becomes a permanent no-op. This breaks the instruction's own test requirement in an interesting way:

> *"a test that only asserts 'REJECTED_* survived' would pass on a prune that deleted nothing at all."*

**After Option B, the prune genuinely deletes nothing at all** — so that test would pass *and* the job
would be inert. A synthetic `EXPIRED` row would make a test go green while proving nothing about
production. Two implementation options, presented without recommendation:

- **B-inert** — accept the no-op. Keep the predicate as-is and make the inertness **explicit**: rename
  the log line, and have the job log `WARNING: prune predicate matched 0 rows and no persisted status
  can match it` rather than a cheerful `pruned: 0`. Honest, zero risk, and leaves the dead clause as a
  documented placeholder.
- **B-clean** — implement Option B *and* retire the dead clause, leaving `_cleanup_old_fingerprints`
  either removed or explicitly disabled with a comment pointing at this document. Smaller surface, no
  misleading vocabulary, but a larger diff than Rama approved.

**Required test, either way — the one that would have caught the pre-P10 bug and this one:**
a **reachability test** asserting that the set of statuses the pipeline can persist **intersects** the
prune predicate. Build the persisted-status set from the code (the `_PipelineReject` check names and the
literal statuses written by `state_store`), evaluate the predicate against it, and assert the
intersection is non-empty *or* that the job is explicitly marked inert. That test fails on a dead clause
instead of passing quietly, which is precisely the class of failure this predicate has produced twice.

**Behavioural tests:**
| Test | Must fail when |
|---|---|
| `REJECTED_*` older than the window **survives** | the GLOB clause returns |
| the trade audit trail survives | the status filter breaks |
| a signal with a `trades` child survives regardless of status | the capital guard is dropped |
| **dry-run count == actual delete count** | preview and action diverge again (P10's second bug) |
| **the job reports 0 deletions LOUDLY** | it silently reports success while inert |

### D3. Deploy window
`eod_cleanup` runs **15:50 Mon–Fri** — a cron-behaviour change, so it goes **off-market through the
careful loop**, and **after E4/W10 is confirmed** (tomorrow's EOD reset). The two must never be in
flight together: E4/W10 is deployed but unverified, and a second change on top would make tomorrow's
observation unable to isolate cause. **There is no deadline** — the prune deletes nothing until
~10-Sep-2026 (§A4).

**Parity (Rule #5) — verified, not assumed:** `scripts/eod_cleanup.py` contains **no** `paper` / `live` /
`is_paper` / mode reference (the sole grep hit is a comment about config defaults). It takes no mode
argument and operates on the single DB. **Mode-agnostic by construction.**

### D4. What could go wrong
| Risk | Assessment |
|---|---|
| A consumer starts seeing old rejects | **None found** (§C2) — every consumer is date- or id-scoped |
| Dedup breaks | **Structurally impossible** — the unique key includes `fingerprint_date` (§C2) |
| Unbounded growth | **Real and quantified**: ~9.9 MB/trading day, amplified ~15× by backup retention on one disk (§B4) |
| The job silently becomes inert | **The main risk of this change** — §D2 exists for it |
| Reversal is costly | **No** — one predicate restore + one catch-up run (§C4) |
| Deploy collides with E4/W10 | Avoided by §D3 sequencing |

---

*No code, config, schema, cron or systemd change. `scripts/eod_cleanup.py` was never executed, in any
form. All figures read-only via `mode=ro` or from `git`/source.*
