# Brief — Prune retention & status-selectivity — DECIDABLE NOW

**One-screen summary of [`09_prune_retention.md`](09_prune_retention.md); it does NOT supersede that file.**
Decidable now — this turns on Rama's intent, not on evidence. No recommendation. Both directions carry equal weight.

## The question (one choice)
Is **rejection-composition** (the `REJECTED_*` signal history the daily prune destroys) a **recurring analytical need** — and if so, should it be preserved?

## The options — each with its consequence
- **Option A — leave the prune as-is**: 90-day retention, status-selective (keeps `SKIPPED_*`, destroys `REJECTED_*`), with a 6-day snapshot as the only rejection insurance. Cost: rejection history beyond the snapshot is **unrecoverable** if later needed.
- **Option B — retain `REJECTED_*` too**: rejection composition survives every prune. Cost: the `signals` table grows by **~7,800 rows/day** with no *proven* analytical use.
- **Option C — change the 90-day window** (longer = more storage; shorter = faster loss of accepted + skip history too).

## The one number that matters
**The daily prune deletes 0 rows until ~2026-09-10** (90-day retention; earliest row 12-Jun). There is **no time pressure**. A verified read-only snapshot of 6 complete days already exists (`/home/ubuntu/preserved/signal_census_19jul2026/`, chmod 444).

## What Rama is NOT deciding here
- Not making the production change tonight — altering the prune predicate or retention window is a **production change, out of scope** for this batch.
- If the answer is "yes, preserve it," the **cheap settlement is a scheduled snapshot cron** of the pre-prune state — *not* touching the live prune. That is a separate later action.

## If he does nothing
Rejection history keeps being pruned forward (past the 6-day snapshot). Costs **nothing** unless rejection-composition is later needed — in which case it is gone beyond those 6 days.

## The two sides to weigh
Retaining is arguably wasteful (large rejection volume, no *demonstrated* recurring need). Yet rejection composition is **exactly what the census and throttle analyses consumed** — losing it forecloses future audits of the same kind, cheaply preventable by a snapshot. Also unresolved from the record: whether the status-selectivity (keep `SKIPPED_*`, drop `REJECTED_*`) was **intentional or incidental** — the predicate is explicit in `eod_cleanup.py:234`, but its rationale lives in the prune's history.
