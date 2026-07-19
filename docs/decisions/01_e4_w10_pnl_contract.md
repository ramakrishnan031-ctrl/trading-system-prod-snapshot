# Decision — E4/W10: the `pnl_delta` contract (daily-loss input)

**Status:** OPEN — Rama's call. Deliberately held (*"won't merge a risk-posture change on 'clear the list'"*, 17-Jul).
**Type:** capital-posture. **Blocked by:** nothing technical — the fix is built, tested, and unpushed; the block is a posture sign-off. Deploy also has an operational precondition (below).
*This file is a summary of the record, not a recommendation. The lettering below is a label, not a ranking.*

## ⚠️ UPDATE 19-Jul-2026 — thresholds corrected (they were fixture values), and the exposure is now computed: N = 0
Established from config + code (not memory):
- **ONE source, `daily_loss_limit_pct = 0.03`** (`config/system_config.yaml:193`, *"SOLE daily-loss authority (3% of capital), PERMANENT"*). The old absolute `capital.daily_loss_limit` (was ₹300) was **DELETED 24-Jun** (`:131-133`). Both enforcement points read the **same** key: the pre-trade gate `RiskEngine` (`main.py:2369` → `risk_engine.py:569`) and the post-close breach `FundManager` (`main.py:2231` → `fund_manager.py:1280`), each = **3% × current capital**, realized-only (`daily_loss_include_unrealized: false`). **There is no absolute mechanism and no second key.**
- **Capital base = broker margin ≈ Rs 10,000** (`fund_manager.py:440` `self._total = broker_balance`; ledger INIT rows show Rs 9,995–10,040; the Rs 1,000,000 on 12-Jun is the pre-sync fixture seed). ⇒ **threshold ≈ Rs 300/day** — not the Rs 25,000 / Rs 10,000 stated in the (now-corrected) bullets below.
- **The deployed reader IS Option B**, proven from the data: every `RESET_PNL` row equals −(Σpnl_delta − Σcosts) on all 22 reset days.
- **Exposure computed** (`docs/audit/e4_w10_outcome_impact_19jul2026.md`): on the daily-loss check, **N = 0 of 23 book days** — the double-count changed no outcome. Closest approach: **07-07, worst intraday cumulative −56.47 (Option B) vs the −Rs 300 threshold = Rs 243.5 of margin.** N=0 is **structural** (the worst day is ~19% of the limit), *not* a knife-edge — but **not robust to D1**: raising sizing ~5× brings the worst day to the threshold, at which point that day breaches under *both* readings (the double-count is then moot, not decisive). The double-count can only flip an outcome if one day's true-net loss lands within Σcosts (a few rupees) of the threshold — a coincidence the book has never approached.

## The choice
- **Option A — adopt the NET contract** (deploy branch `e4-w10-pnl-contract`@`ad34ee4`): `fm_ledger.pnl_delta` is NET, `costs` is observability-only and never re-subtracted.
- **Option B — keep the current reader**, in which the daily-loss control input subtracts costs a second time.

## What is known (current evidence, with citations)
- **The fix is proven correct against an independent computation.** Batch 3 re-derived the true net independently; the delta was **exactly 0.000000**, re-verified in a seeded worktree. RED-on-old: the current reader returns **−140.0** for a NET row of −100 / costs 40 (i.e. it double-subtracts). Report `docs/audit/e4_w10_done_17jul2026.md`; memory `e4-w10-done-17jul`.
- **Behavioural delta:** under Option B the daily-loss control's input is more negative than true net by the day's accumulated costs, so the limit **fires earlier** than the true-net figure. Under Option A it fires on true net (**later**, by that same margin). This is the risk-posture change.
- **Magnitude, from the live ledger** (`mode=ro`): only `RELEASE_USED` (155 rows, Σpnl −96.45, **Σcosts 60.24**) and `RESET_PNL` (22 rows) carry `pnl_delta`. The control **resets nightly** (`RESET_PNL`), so only same-day rows matter. Per-trade cost on this book is **~Rs 0.40** (expectancy autopsy: costs Rs 53.51, cost/trade Rs 0.40). So same-day Σcosts is order **single-digit-to-low-tens of rupees**.
- **The threshold this shifts against (CORRECTED — see the 19-Jul update above):** a **single** limit, `daily_loss_limit_pct = 0.03` = **3% × current capital ≈ Rs 300/day** (capital ≈ Rs 10,000 broker margin), enforced at two points (pre-trade gate + post-close breach), realized-only. ~~5% ≈ Rs 25,000 / absolute Rs 10,000~~ were fixture constructor literals mis-carried from the `dual-daily-loss-mechanism` memory, which is itself stale (it still lists the deleted absolute key).
- **Scope of the change:** schema v44 unchanged; branch never pushed; rollback = revert one commit (schema-free). CHECK1/GTT writers were extended in the same commit so `Σ pnl_delta == Σ trades.net_pnl` is preserved (`PATHS.md:447`).
- **Operational precondition for deploy:** no flatten mechanism was built into this change; the topic note records that deploy = Rama flattens manually first, then push + tag.
- **Historical artifact (bounded):** 36 pre-fix `costs=0` rows were written gross; not backfillable (would be fabrication), but the control is per-day and zeroed nightly, so only forward rows are affected. Deploying after a day's EOD reset makes the new reader read that day as Σcosts (small positive ⇒ no breach).

## What is unknown
- ~~Whether the double-count has ever changed a gate outcome~~ — **now COMPUTED: N = 0** (see the 19-Jul update; `e4_w10_outcome_impact_19jul2026.md`).
- **The forward per-day Σcosts and loss distribution if positions grow** (e.g. if D1 raises sizing) — *[knowable only by running the system]* at the larger size. §B4 bounds it: a ~5× sizing increase brings the worst day to the threshold, but at that point the day breaches on its own merits under both readings.
- **Whether Option A or Option B is the *correct* accounting** (a separate question from N) — depends on whether `fm_ledger.pnl_delta` is stored net or gross per row; batch 1 found RMS/CHECK1 closes wrote it gross (`costs=0` passed), which the E4/W10 branch also addresses. This is a posture/correctness judgement, not resolved by N.

## What changes if the chosen direction is wrong
- **If A (adopt) and it is the wrong call:** the daily-loss control tolerates up to the day's Σcosts *more* realised loss before firing — a loosening bounded by same-day Σcosts (max Rs 5.56 on the record) against the **Rs 300** threshold. On the current book that changed **0** outcomes (N=0, Rs 243 margin).
- **If B (keep) and it is the wrong call:** the control fires *early* by the same margin — an opportunity cost, not a capital loss, of the same bounded magnitude; also 0 outcomes on the record.

## What would settle it
- **DONE (19-Jul):** the per-day ledger computation was run against the *corrected* Rs 300 threshold — **N = 0 of 23 days, closest approach Rs 243** (`docs/audit/e4_w10_outcome_impact_19jul2026.md`). What remains is not evidence but the posture call (adopt vs keep) and, if adopting, the manual-flatten deploy precondition — both Rama's.
