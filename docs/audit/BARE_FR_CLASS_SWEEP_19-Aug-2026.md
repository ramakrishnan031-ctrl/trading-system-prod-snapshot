# THE BARE-`fr` CLASS — SWEPT, AND WHAT IT IS ACTUALLY WORTH
**19-Aug-2026 ~18:45 IST. ⛔ INVESTIGATION ONLY — no source changed, no fix applied.**
Follows the S14 fix (`8f78c67`), which proved a bare `fr` track can both overflow the page
**and** silently distort the declared column ratios.

## 1 · THE RAW COUNT — ⛔ AND WHY IT IS NOT THE RISK MEASURE
**105 `grid-template-columns` declarations carry an unwrapped `fr`** (62 explicit, 43
`repeat(n, 1fr)`), spread across the global block and 16 page scopes.

⛔ **A raw count would be a misleading thing to hand anyone.** A bare `fr` only bites when the
track's **content has a large intrinsic min-width** — a wide table, an unbreakable string. A
`repeat(6, 1fr)` KPI deck holding `4 / 5` is harmless and always will be. **`.tlg-row4` bit
because it held a table.**

## 2 · THE RISK PREDICATE, DERIVED FROM THE ACTUAL S14 MECHANISM
A grid is at risk **only** when all three hold:
`display: grid` · `overflow-x: visible` (it does not clip) · `scrollWidth > clientWidth`
(its content already exceeds its box).

⭐ **That is a one-line browser check, and it would have caught S14 instantly.**

## 3 · THE SWEEP — ALL 21 AUTHENTICATED SCREENS AT 1440×900

| result | count |
|---|---|
| screens with page-level overflow | **0 of 21** |
| **grids at or over their content floor** | **0** |

⇒ 🏷️ **THE CLASS IS LATENT, ⛔ NOT ACTIVE. 104 of the 105 declarations are currently
harmless; the one that was symptomatic (`.tlg-row4`) is fixed.**

⛔⛔ **FIXTURE-BOUND, AND SAID PLAINLY:** this is measured against the conftest fixture.
**S14's own defect was invisible until its data grew wide enough** — the same latency
`SEEDING.md` was written to record. ⇒ ⛔ *"no grid at its floor today"* is ⛔ **not** *"no grid
will ever be"*. ⭐ What makes it useful is that the **predicate is cheap and repeatable**.

## 4 · 🔴 A CORRECTION TO MY OWN EARLIER FRAMING
I recorded that **S08 · S13 · S15** *"share S14's missing `main.content` constraint and are clean
only because their content happens to fit"*. ⭐ **Directionally right, ⛔ but imprecise, and the
imprecision matters.**

**MEASURED at 1440 — `main.content` on all six screens tested, constrained and unconstrained
alike:** `width 1236px · rect 1236 · scrollWidth 1236 · parent clientWidth 1236`.

⇒ 🔑 **`main.content` sizes to its 1236px track NATURALLY whenever its content fits. The
`:has(> …)` rule is a SAFETY NET, ⛔ not the sizing mechanism.** Its absence is inert until
something pushes against it — and S14's 1580px came from **`.tlg-row4` forcing it wide**, which
is now fixed at source.
📌 **Which is why S14 needs no `:has()` guard today: the CAUSE was fixed, ⛔ rather than a guard
added on top of it.** ⭐ That is the better outcome and it is why the guard was reverted.
⛔ **My headroom probe measured nothing useful** — for a block element `scrollWidth ==
clientWidth` means *"content fits"*, ⛔ not *"0px from failure"*. ⭐ The informative measurement
is §3's grid-floor sweep, which is already the answer.

## 5 · WHAT IS ACTUALLY WORTH DOING — ⛔ NOTHING PROPOSED FOR TONIGHT
⛔ **`raise all 105` is NOT a recommendation and is not made here.** 104 are inert, most will
never hold wide content, and touching them would re-open screens for no measured gain.

⭐ **The durable value is the predicate, ⛔ not a patch list.** It converts *"we hope no grid
overflows"* into a measurable property, and it is exactly what **layout-assurance Option A/B**
would automate — the same option already tabled under **D3/Q4** and awaiting a decision.
📌 Recorded here so that when Option A is authorised, the check is already specified:
**`display:grid` + `overflow-x:visible` + `scrollWidth > clientWidth`, asserted zero.**

⛔ **No source touched. ⛔ Nothing proposed for implementation. ⛔ D3 and D5 remain frozen.**
