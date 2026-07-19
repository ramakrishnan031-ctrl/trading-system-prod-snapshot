# Decision — Freeze `min_pass_score` while the regime measurement accumulates?

**Status:** OPEN — Rama's call. **Type:** measurement hygiene. **Blocked by:** nothing; but it is in direct tension with D3 (they cannot both be exercised at once).
*Summary of the record, not a recommendation. Lettering is a label, not a ranking.*

## The choice
- **Option A — freeze `min_pass_score`** at a single value for the duration of the regime / min-score measurement.
- **Option B — allow it to change** (e.g. to act on D3) during that window.

## What is known (current evidence, with citations)
- **The book has already run at two thresholds** — reconstructed per-day: 6 days @ 60, 4 days @ 55, 1 mixed, 12 undetermined (`docs/audit/regime_thesis_minscore_control_18jul2026.md`, memory `regime-minscore-control-18jul`). The properly-controlled cells already hold **~1–2 days each**.
- **The ~2.2-month power estimate assumes a single threshold throughout.** Every mid-window change **re-fragments the sample and resets the clock** for the affected group (the source's one actionable output: *"if the regime feature is to be measurable at all, the threshold must stop moving"*).
- **The reconstruction problem is historical only:** `config_snapshots` (from 02-Jul, `main.py:2099`) now records the resolved threshold at every boot, so going forward every day is self-documenting.
- **Direct tension with D3:** D3 asks whether to *change* `min_pass_score` (the band inversion). Freezing forbids that change during the measurement; acting on D3 resets the measurement. The two decisions cannot both be satisfied in the same window.

## What is unknown
- **Whether the regime feature is measurable at all** — *[knowable only by running the system]* for months, and Q10's verdict is already "NOT DETERMINABLE at n=23" (`regime-thesis-validation-18jul`). Freezing protects a measurement whose power is itself in question.
- **How long the freeze would need to hold** — *[knowable only by running the system]*: ~2.2 months for a LARGE effect, ~9 months for MODERATE.

## What changes if the chosen direction is wrong
- **If A (freeze) and regime turns out unmeasurable anyway:** the threshold was held constant to protect a comparison that could not conclude — the cost is forgoing any D3 change for that period.
- **If B (allow) and regime was measurable:** a mid-window threshold change resets the clock and loses the comparability accumulated so far.

## What would settle it
- This is a meta-decision that resolves once the regime/D3 priority is set: it is settled by deciding whether the regime measurement is worth protecting (routes to the Regime decision + Q10) versus whether the D3 band-inversion change is worth making now. No experiment settles the freeze itself; it is downstream of those two.
