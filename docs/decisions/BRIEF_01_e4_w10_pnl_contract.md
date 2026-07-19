# Brief — E4/W10 (the `pnl_delta` contract) — DECIDABLE NOW

**One-screen summary of [`01_e4_w10_pnl_contract.md`](01_e4_w10_pnl_contract.md); it does NOT supersede that file.**
Decidable now — the evidence is complete; only Rama's judgement is missing. No recommendation. Both options carry equal weight.

## The question (one choice)
Deploy the built branch so the daily-loss control reads **true net** P&L, or keep the **current reader** that subtracts the day's costs a second time?

## The two options — each with its consequence
- **Option A — adopt the NET contract** (deploy `e4-w10-pnl-contract`@`ad34ee4`): the daily-loss limit fires on true net, so on a losing day it trips **slightly later** than today — later by exactly that day's accumulated costs. Correct accounting; a real (bounded) risk-posture *loosening*.
- **Option B — keep the current reader**: the limit keeps firing **early** by that same margin. A bounded *opportunity cost*, not a capital loss; leaves a known double-subtract in the capital path.

## The one number that matters
**N = 0 of 23 book days.** The double-count has changed **no** daily-loss outcome on the record. Worst approach: 07-Jul, **Rs 243.5** of margin between the worst intraday figure and the **~Rs 300/day** threshold (3% × ~Rs 10,000 broker capital). Same-day costs max out at **Rs 5.56**. N=0 is structural (worst day ≈ 19% of the limit), **not** a knife-edge — but it is **not robust to D1**: ~5× sizing brings the worst day to the threshold, at which point that day breaches under *both* readings anyway.

## What Rama is NOT deciding here
- Not whether A or B is the *philosophically correct* accounting (a separate posture/correctness question; N=0 does not resolve it).
- Not sizing (that's D1) and not building a flatten mechanism — **none exists; a manual flatten is the deploy precondition**.
- Not deploying tonight. This is the posture call; the deploy is [`RUNBOOK_e4_w10_deploy.md`](RUNBOOK_e4_w10_deploy.md), gated on observing Monday.

## If he does nothing
Stays on **Option B** (early-firing); the branch stays unpushed. Costs **nothing on the current book** (N=0) — but the known double-subtract remains in the live capital path, and the question re-surfaces the moment D1/leverage make the margin matter.

## Why this is decidable now (not more analysis)
The evidence question is **closed**: the fix is proven correct against an independent computation (delta 0.000000), and the exposure is computed (N=0, Rs 243 margin, threshold ~Rs 300). What remains is **a posture judgement plus a deploy precondition**, both Rama's — not more measurement.
