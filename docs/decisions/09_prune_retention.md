# Decision — Prune retention & status-selectivity

**Status:** ✅ **CLOSED 25-Jul-2026 — Option A (leave the prune as-is). NO CODE CHANGE.** **Type:** data-retention. **Decided by:** Rama.
*Summary of the record, not a recommendation. Lettering is a label, not a ranking.*

## The choice
- **Option A — leave the prune as-is:** 90-day retention, status-selective (`eod_cleanup.py:234` destroys `REJECTED_*` / `EXPIRED` / `DUPLICATE`, keeps `SKIPPED_*`), plus a preserved snapshot as insurance.
- **Option B — stop the status-selectivity** (retain `REJECTED_*` too), so rejection composition survives.
- **Option C — change the retention window** (longer or shorter than 90 days).

## What is known (current evidence, with citations)
- **The prune predicate is status-selective:** `(status IN ('EXPIRED','DUPLICATE') OR status GLOB 'REJECTED*') AND fingerprint_date < ? AND NOT EXISTS(trades)` (`scripts/eod_cleanup.py:234`). `SKIPPED_*` and qualified families are never touched.
- **The data is NOT eroding day-to-day** (corrects the census's own "keeps sliding"): retention is **90 days**, earliest row **12-Jun**, so the daily prune deletes **0 rows** until **~2026-09-10**. The 09-Jul boundary was a **one-off manual prune on 16-Jul** (which cleared 113,377 old signals), not the daily job. Source: `docs/audit/throttle_selection_and_record_correction_19jul2026.md` §A, `docs/audit/signal_mortality_census_19jul2026.md` §A4.
- **Consequence for analysis:** pre-09-Jul rejection history is already gone; only 2026-07-09→16-Jul is complete; any share computed over all 32,928 rows is meaningless. `SKIPPED_*` counts are complete for the whole 24-day window.
- **Insurance already taken:** a verified read-only snapshot exists at `/home/ubuntu/preserved/signal_census_19jul2026/` (89.97MB DB + 2 CSVs, chmod 444) — 6 complete days preserved regardless of any future prune.
- **Volume context:** the `signals` table is ~32,928 rows / ~90MB with rejections *already pruned* pre-09-Jul; from 09-Jul the accepted rate is ~7,800/day and every accepted signal has a row (100% match). Retaining `REJECTED_*` at ~7,800/day would grow the table materially.

## What is unknown
- **Whether rejection-composition analysis is a recurring need** — *[knowable by Rama's intent]*: if it is, Option A loses it (mitigated forward by the snapshot or a snapshot cron); if it is not, the selectivity is harmless.
- **Whether the status-selectivity was intentional or incidental** — *[knowable from existing data]*: the predicate is explicit in `eod_cleanup.py:234`; its design rationale (why keep `SKIPPED_*` but not `REJECTED_*`) is in the prune's history, not re-derived here.

## What changes if the chosen direction is wrong
- **If A (leave) and rejection history is later needed:** it is unrecoverable beyond the preserved snapshot's 6 days (future windows would need a fresh snapshot before each manual prune).
- **If B (retain rejections) and it is not needed:** the `signals` table grows by ~the rejection volume (order thousands of rows/day) with no analytical use.
- **If C (shorten retention) :** accelerates loss of both accepted and skip history; (lengthen) grows storage.

## What would settle it
- A decision on whether rejection-composition is a recurring analytical need. If yes, the cheap settlement is a **scheduled snapshot** (cron) of the pre-prune state rather than changing the live prune — cost: a snapshot job, no change to the trading path. Changing the prune predicate or retention is a production change and is out of scope here.


---

## ✅ DECISION — 25-Jul-2026: **Option A. Leave the prune as-is. Nothing built.**

**Rama's call, recorded verbatim in intent:** *2,667 rows against ~9.9 MB/day of growth is noise, not value.*

**What that means concretely:**

- The status-selective predicate at `scripts/eod_cleanup.py:234` **stays exactly as it is** — `REJECTED_*` / `EXPIRED` / `DUPLICATE` continue to be pruned at 90 days; `SKIPPED_*` and qualified families continue to be kept.
- **Option B is declined.** Retaining rejections would trade ~9.9 MB/trading day of unbounded growth (×15 through the backup chain) for ~2,667 rows of rejection-composition history whose analytical need was the open question. The answer is that it is not a recurring need.
- **Option C is declined.** The 90-day window is unchanged.

**Therefore this decision ships NO code, NO config and NO cron change.** The design work in `DESIGN_09_prune_retention.md` and the brief in `BRIEF_09_prune_retention.md` are retained as the record of what was considered and why it was refused — not as pending work.

⚠️ **The one thing that survives from the design:** the naive Option-B diff would have made the 15:50 job a **permanent no-op**, because `EXPIRED` and `DUPLICATE` are never actually persisted. That is a real trap and is why Option B was never a one-line change. Recorded here so nobody re-derives it if the question reopens.

**Insurance already in place, unaffected:** the read-only snapshot at `/home/ubuntu/preserved/signal_census_19jul2026/` (89.97 MB DB + 2 CSVs, chmod 444) preserves 6 complete days of rejection composition regardless of the prune. If rejection-composition analysis ever does become recurring, the cheap settlement remains a **scheduled pre-prune snapshot**, not a change to the live prune.
