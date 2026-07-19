# Decision — E4/W10: the `pnl_delta` contract (daily-loss input)

**Status:** OPEN — Rama's call. Deliberately held (*"won't merge a risk-posture change on 'clear the list'"*, 17-Jul).
**Type:** capital-posture. **Blocked by:** nothing technical — the fix is built, tested, and unpushed; the block is a posture sign-off. Deploy also has an operational precondition (below).
*This file is a summary of the record, not a recommendation. The lettering below is a label, not a ranking.*

## The choice
- **Option A — adopt the NET contract** (deploy branch `e4-w10-pnl-contract`@`ad34ee4`): `fm_ledger.pnl_delta` is NET, `costs` is observability-only and never re-subtracted.
- **Option B — keep the current reader**, in which the daily-loss control input subtracts costs a second time.

## What is known (current evidence, with citations)
- **The fix is proven correct against an independent computation.** Batch 3 re-derived the true net independently; the delta was **exactly 0.000000**, re-verified in a seeded worktree. RED-on-old: the current reader returns **−140.0** for a NET row of −100 / costs 40 (i.e. it double-subtracts). Report `docs/audit/e4_w10_done_17jul2026.md`; memory `e4-w10-done-17jul`.
- **Behavioural delta:** under Option B the daily-loss control's input is more negative than true net by the day's accumulated costs, so the limit **fires earlier** than the true-net figure. Under Option A it fires on true net (**later**, by that same margin). This is the risk-posture change.
- **Magnitude, from the live ledger** (`mode=ro`): only `RELEASE_USED` (155 rows, Σpnl −96.45, **Σcosts 60.24**) and `RESET_PNL` (22 rows) carry `pnl_delta`. The control **resets nightly** (`RESET_PNL`), so only same-day rows matter. Per-trade cost on this book is **~Rs 0.40** (expectancy autopsy: costs Rs 53.51, cost/trade Rs 0.40). So same-day Σcosts is order **single-digit-to-low-tens of rupees**.
- **The thresholds this shifts against:** the pre-trade pct-gate = 5% ≈ **Rs 25,000**, and the post-close `FundManager` absolute = **Rs 10,000** (memory `dual-daily-loss-mechanism`). These are two different mechanisms with different keys.
- **Scope of the change:** schema v44 unchanged; branch never pushed; rollback = revert one commit (schema-free). CHECK1/GTT writers were extended in the same commit so `Σ pnl_delta == Σ trades.net_pnl` is preserved (`PATHS.md:447`).
- **Operational precondition for deploy:** no flatten mechanism was built into this change; the topic note records that deploy = Rama flattens manually first, then push + tag.
- **Historical artifact (bounded):** 36 pre-fix `costs=0` rows were written gross; not backfillable (would be fabrication), but the control is per-day and zeroed nightly, so only forward rows are affected. Deploying after a day's EOD reset makes the new reader read that day as Σcosts (small positive ⇒ no breach).

## What is unknown
- **Whether the double-count has ever actually changed a gate outcome on the record** — *[knowable from existing data]*: the ledger + the daily-loss thresholds are both stored; one could compute, per day, whether `Σpnl` vs `Σpnl − Σcosts` crossed Rs 10,000 / Rs 25,000. On the magnitudes above (tens of rupees vs Rs 10,000+) a crossing is unlikely, but it has not been computed and is not asserted here.
- **The forward per-day Σcosts distribution** if positions grow (e.g. if D1 raises sizing) — *[knowable only by running the system]* at the larger size.

## What changes if the chosen direction is wrong
- **If A (adopt) and it is the wrong call:** the daily-loss control tolerates up to the day's Σcosts *more* realised loss before firing — a loosening bounded by same-day Σcosts (order tens of rupees) against thresholds of Rs 10,000 / Rs 25,000.
- **If B (keep) and it is the wrong call:** the control fires *early* by the same margin, ending the session sooner than a true-net reading would — an opportunity cost, not a capital loss, of the same bounded magnitude.

## What would settle it
- A per-day ledger computation (existing data, `mode=ro`, minutes of work) of whether `Σpnl` and `Σpnl − Σcosts` ever fall on opposite sides of Rs 10,000 or Rs 25,000 on any book day — turning "the posture shifts by Σcosts" into "the posture shift changed the outcome on N of 23 days" (N may be 0). That quantifies the exposure without deploying anything.
