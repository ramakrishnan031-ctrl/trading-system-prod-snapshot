# GUI REVIEW — **WHAT GUARDS THE LAYOUT CLAIMS, AND WHAT CANNOT**
**19-Aug-2026 ~10:40 IST · REVIEW ONLY — ⛔ no code written, ⛔ nothing committed, ⛔ nothing pushed.**
Worktree `trading-system-gui09`, branch `feat/screen10-slippage-analytics` @ `160ebbc`
(**37 ahead / 5 behind** `origin/main` `08b462b`). ⛔ Working tree left clean apart from this
untracked note.

**Why this question:** the ledger already records the lesson — Screen 10's *"0 overflow"*
reading was **vacuous, taken while its table was empty**, and only surfaced once
`trade_slippage_log` was seeded. The same pass claims *"page-level horizontal overflow = 0
everywhere"* across **all 21** authenticated screens. ⭐ The fix was applied to S10. **The
method that produced the vacuous reading was not examined.** That is what this reviews.

---

## R-1 · ⛔ NO TEST IN THE SUITE RENDERS A PAGE OR MEASURES GEOMETRY
**Measured, with the search width stated:** across **46 test files** in
`ops_dashboard/tests/`, a grep for
`selenium|playwright|webdriver|headless|msedge|chromedriver|getBoundingClientRect|scrollWidth|clientWidth`
returns **exactly one file** — and that hit is itself a *text* assertion:

```
tests/test_screen13_audit.py:582
    assert "measure()" in body and "clientWidth" in body
```

It asserts the string `clientWidth` appears in the template. It measures nothing.
**No browser driver is a declared dependency** — `selenium` / `playwright` /
`pytest-playwright` appear in **no** `requirements*.txt`, `pyproject.toml` or `setup.py`.

⇒ **Every layout, typography and overflow guarantee in this campaign is asserted by the
presence of text in CSS or template source.** ⛔ That is not a criticism of any individual
test; it is the shape of the whole suite, and it decides which defects it can possibly catch.

## R-2 · ⭐ CREDIT WHERE IT IS DUE — FOR S10/S11 CHART LABELS THE TEXT ASSERTION IS **VALID**
⛔ I am **not** calling `test_s1011_chart_labels.py` weak. It is the opposite, and the
distinction matters:

The defect was that SVG `<text>` declares size in **user units**, so rendered px =
`declared × (container ÷ viewBox)` — 1:1 at exactly one viewport (13.00 @1920 → 9.80 @1440).
The fix **moved the labels out of SVG into an HTML overlay**. ⇒ the labels are now real CSS
px, so **declared px == rendered px**, and

```
assert float(m.group(1)) >= 13, "%s .cxl is %spx"
```

**genuinely pins the rendered outcome.** The file also pins the things that would silently
break it — `<text>` cannot return, the overlay's viewBox constants must match the `<svg>`
they divide by, markup and labels share one geometry function so they cannot drift, and the
global `.curve-wrap` is deliberately denied a `position`. ⭐ **That is a well-built test, and
the change it guards converted an unmeasurable property into a measurable one.**

## R-3 · 🔴 THE GAP IS **CONTENT-DRIVEN HORIZONTAL OVERFLOW**, AND ⛔ NO SOURCE ASSERTION CAN REACH IT
The S10 overflow did not depend on any token in the source. It depended on **how many rows
the table had and how wide their values were.** ⇒ ⛔ **no assertion over CSS or template text
can detect it, in principle** — not a gap in coverage, a gap in *kind*.

Confirming it is the live shape: table columns are **generated**, not literal.
`trade_explorer.html` carries only **3 static `<th>`** yet is documented as the *28-column*
Trade Explorer — the columns come from `x-for="c in cols"` (`:199`, `:220`). ⛔ **A static
column count is therefore not a width proxy, and I discarded my own first inventory on that
basis** rather than report it.

## R-4 · 🔴 THE MEASUREMENT THAT *COULD* CATCH IT IS NOT REPRODUCIBLE — ⛔ IT IS NOT IN THE REPO
The claim *"ALL 21 authenticated screens at 1920×1080 AND 1440×900 — page-level horizontal
overflow = 0 everywhere"* rests on a headless-browser harness. **Search width stated:** a
repo-wide grep across `*.py`, `*.md`, `*.txt` for
`serve_verify | virtual-time-budget | --screenshot | window-size` returns **0 hits.**
The harness exists only in **per-session scratchpad directories**.

⇒ three consequences, and the third is the one that bit:
1. ⛔ the measurement **cannot be re-run** by anyone, including a later session;
2. ⛔ it **cannot regress** — nothing fails if a future change reintroduces overflow;
3. 🔑 **its seeding is unrecorded**, so *"0 overflow"* and *"0 rows"* are indistinguishable
   after the fact. **That is exactly why S10's zero read as a pass.**

## R-5 · ⚠️ THE SWEEP WAS NOT DONE — AND THE REMAINING SET IS **UNKNOWABLE FROM THE REPO**
The ledger records seeding for **`trade_slippage_log`** (Screen 10). ⛔ It does **not** record
per-screen row counts for the other **20** screens at measurement time, and neither does the
repo. ⇒ **which of the other 20 zeros were also taken against empty tables is presently
UNKNOWN**, and cannot be established from the tree.

⛔ **I am not asserting the other 20 are wrong.** ⭐ I am asserting that **the evidence does
not distinguish them from S10 before it was seeded** — which is the same position the
campaign was in on S10 the day it was called clean. `V5`: a missing check leaves you
uncertain; this one left a *reading* that looked certain.

---

## DESIGN OPTIONS — ⛔ NOTHING BUILT, ⛔ RAMA'S CALL
Ordered by cost. ⛔ I have not started any of them.

| # | option | what it buys | cost |
|---|---|---|---|
| **A** | **Promote the harness into the repo** (`ops_dashboard/qa/`), with a **seeded fixture per screen** and a recorded row count per table | the claim becomes **re-runnable and attributable**; a future *"0 overflow"* carries its seeding | ~half a session; no production code touched |
| **B** | **A** + one assertion — `document.documentElement.scrollWidth <= clientWidth` per screen per viewport, run in CI | overflow can **regress red** for the first time | adds a browser dependency to the gate |
| **C** | **Record-only:** a `SEEDING.md` stating, per screen, which tables had rows when it was measured | closes the *attribution* hole cheaply; ⛔ does not make anything re-runnable | ~1 hour |
| **D** | Do nothing; accept that layout is source-asserted and re-measure by hand each pass | zero cost | ⛔ the S10 class recurs silently, and the next one may not be cosmetic |

⭐ **My recommendation: C now, A when there is a window.** C is cheap and immediately removes
the *"was this zero vacuous?"* ambiguity for all 21 screens going forward. **B is the only
option that makes the property regress-testable**, but it puts a browser in the gate — which
is a real cost on a gate that already runs 542 s, and that trade is Rama's, ⛔ not mine.

⛔ **NOT proposed:** shrinking type to buy width (the fix commit explicitly refused this, and
it would trade one spec violation for another), and any change to the 22 screens' markup.

## STATUS OF THIS NOTE
🏷️ `REVIEWED · DESIGN OPTIONS DRAFTED · ⛔ NOTHING BUILT · ⛔ NOTHING COMMITTED · ⛔ UNPUSHED`
⛔ No file in the GUI tree was modified. ⛔ No test was run. ⛔ The 37 unpushed commits were
not touched, rebased or amended.
