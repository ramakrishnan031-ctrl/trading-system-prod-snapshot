# Brief — Freeze `min_pass_score` during measurement — DECIDABLE NOW

**One-screen summary of [`08_freeze_min_pass_during_measurement.md`](08_freeze_min_pass_during_measurement.md); it does NOT supersede that file.**
Decidable now — this is a priority/hygiene judgement, not an evidence question. No recommendation. Both options carry equal weight.

## The question (one choice)
Hold `min_pass_score` at a single value for the duration of the regime / min-score measurement, or allow it to change (e.g. to act on D3) during that window?

## The two options — each with its consequence
- **Option A — freeze it**: protects the regime measurement's comparability (a moving threshold re-fragments the sample). Cost: it **forbids any D3 change** for as long as the measurement runs.
- **Option B — allow it to change**: keeps D3 actionable now. Cost: any mid-window change **resets the regime clock** and discards the comparability accumulated so far.

## The one number that matters
**~2.2 months (≈47 trading days)** — the power floor for detecting a *large* regime effect, and it assumes **one threshold throughout**. The properly-controlled cells today hold only **~1–2 days each**; the book has already run at two thresholds (6 days @ 60, 4 days @ 55).

## What Rama is NOT deciding here
- Not whether regime *has* edge (that's #07 Regime, gated on Q10 — token-blocked, ~2.2 months).
- Not the D3 direction itself (#04). This decides only whether the threshold **holds still while measuring**.

## If he does nothing
The threshold is **already frozen in practice** — nobody is proposing to change `min_pass_score`, and D3 is gated on forward-shadow days it does not yet have. So inaction *is* the freeze, without a formal decision. Making it explicit changes nothing operationally today; it only records the intent for when D3 later becomes actionable.

## The tension to hold in view
Freeze and D3 **cannot both be exercised in the same window** — freezing forbids the D3 change; acting on D3 resets the measurement. A subtlety cutting the other way: freezing protects a measurement that Q10 already calls **NOT DETERMINABLE at n=23**, so Option A may be holding the threshold still to protect a comparison that cannot conclude.
