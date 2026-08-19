# S14 / S17 OVERFLOW — DIAGNOSIS AND BLAST RADIUS FOR **D2**

**19-Aug-2026 ~15:50 IST. ⛔ DIAGNOSIS ONLY — no fix written, no source file touched.**
Produced because D2's own guidance says *"Prefer A only if the fix can be isolated to the
affected screens without re-opening already-approved screens. Otherwise present the exact blast
radius before coding."* ⭐ This is that blast radius, measured.

## 0 · THE ANSWER TO D2's CONDITION, FIRST

✅ **BOTH FIXES ARE FULLY ISOLATED. ⛔ NEITHER RE-OPENS ANY APPROVED SCREEN.** Measured:

| class | rules in `style.css` | page-scoped | unscoped rules | templates using it |
|---|---|---|---|---|
| `.tlg-work` | 2 | **2** | **0** | `trade_logs.html` only |
| `.ctl-hrow` · `.ctl-hist` | 1 · 1 | **1 · 1** | **0** | `controls.html` only |
| `.ctl-shell` · `.ctl-rail` · `.ctl-row4` | 2 · 3 · 2 | **2 · 3 · 2** | **0** | `controls.html` only |

⇒ every rule is under `.tlg-page` / `.ctl-page`, and each class appears in **exactly one**
template. **A change to either screen cannot reach another screen.**
⚠️ **But the two screens are NOT equal in size — see §3.**

## 1 · S14 TRADE LOGS — 🟢 **ONE NUMBER. A BREAKPOINT SET 16px TOO LOW.**

**MEASURED — the overflow appears and disappears across a 16px boundary:**

| window | **clientWidth** | scrollWidth | overflow |
|---|---|---|---|
| 1440 | **1416** | 1760 | 🔴 **344 px** |
| 1424 | **1400** | 1400 | ✅ none |
| 1416 | 1392 | 1392 | ✅ none |
| 1400 | 1376 | 1376 | ✅ none |
| 1380 | 1356 | 1356 | ✅ none |

**ROOT CAUSE, `style.css:3231-3233`:**
```
.tlg-page .tlg-work { display: grid; grid-template-columns: minmax(0, 1fr) 330px; }
@media (max-width: 1400px) { .tlg-page .tlg-work { grid-template-columns: minmax(0, 1fr); } }
```
🔑 **The stacking query is `max-width: 1400px`, but the 1440×900 viewport's CSS `clientWidth`
is `1416`** — 24px of scrollbar/chrome. So at the target viewport the query **does not fire**,
the fixed **330px** rail stays beside a `minmax(0,1fr)` column, and the row demands ~1548px in a
~1236px slot. At `clientWidth 1400` the query fires, the rail stacks, and the overflow is gone.

⭐⭐ **THE BREAKPOINT WAS WRITTEN AGAINST THE WINDOW SIZE, ⛔ NOT THE CSS VIEWPORT** — a 1440
window is **not** a 1440 clientWidth. 📌 The sibling deck on the same screen already uses
`max-width: 1500px` (`.tlg-kpis`, `:3240`), which **does** cover 1416 — so the two breakpoints on
one screen disagree about the same viewport.
📌 **Culprit chain confirms it is layout, ⛔ not a table:** the widest offender is
`main.content` itself at **1580.2px**, ⛔ not `table`.

🏷️ **SHAPE OF THE FIX: one number** — raise `1400px` so it covers `1416`. ⛔ Not written.
⚠️ **A value must be chosen deliberately, ⛔ not defaulted:** `1500px` would match the
already-present `.tlg-kpis` breakpoint on the same screen. ⛔ **Whether the rail SHOULD stack at
1440 is a design call, ⛔ not a bug fix** — the alternative is to keep two columns and let the
rail shrink, which changes the approved look.

## 2 · S17 CONTROLS — 🔴 **NOT ONE BUG. TWO, AND ONE IS PRESENT AT EVERY WIDTH.**

**MEASURED across a wide sweep:**

| window | clientWidth | scrollWidth | overflow |
|---|---|---|---|
| 2200 | 2176 | 2180 | 🔴 **4 px** |
| 2000 | 1976 | 1980 | 🔴 **4 px** |
| 1920 | 1896 | 1900 | 🔴 **4 px** |
| 1800 | 1776 | 1799 | 🔴 23 px |
| 1600 | 1576 | 1632 | 🔴 56 px |
| 1440 | 1416 | 1499 | 🔴 **83 px** |
| 1380 | 1356 | 1449 | 🔴 93 px |

### 🔴 **S17-a — a CONSTANT ~4px, at EVERY width tested including 2200**
Culprit is stable across all seven widths: `span < div.ctl-hrow < div.ctl-hist <
section.panel < aside.ctl-rail`, `over = 3.9px`. ⇒ ⛔ **not responsive at all** — a fixed
sizing defect in one rail item. ⭐ **This one IS small and isolated** (`.ctl-hrow`/`.ctl-hist`,
one template, one rule each).

### 🔴 **S17-b — a WIDTH-DEPENDENT floor that GROWS as the viewport narrows**
Beyond the constant 4px: **1800 → +19 · 1600 → +52 · 1440 → +79 · 1380 → +89.**
⇒ ⛔ **NOT a missed breakpoint** (a missed breakpoint is a step, ⛔ not a ramp). Content has a
minimum width that never compresses, and narrowing merely widens the gap. **Onset is between
`clientWidth` 1896 (clean) and 1776 (23px)**, and it persists down to the `max-width: 1180px`
stack (`:5610-5617`), below which the rows finally go single-column.
📌 Relevant rules: `.ctl-shell` stacks at **`max-width: 1560px`**; `.ctl-rail` becomes
`repeat(3, minmax(0,1fr))` there; `.ctl-row1..4` only stack at **`max-width: 1180px`** ⇒ **the
band ~1180–1900 is where S17-b lives.**

🏷️ **SHAPE OF THE FIX: ⛔ NOT one number.** S17-a is a small sizing correction; S17-b is a
responsive-layout change across a ~700px band. ⚠️ **Verification cost is the real cost: S17-b
must be re-checked across that whole band, ⛔ not merely at 1920 and 1440** — the two carded
viewports would have shown 4px and 83px and hidden the ramp between them.

## 3 · SUMMARY FOR THE D2 DECISION

| | **S14** | **S17-a** | **S17-b** |
|---|---|---|---|
| shape | breakpoint 16px too low | fixed ~4px sizing | responsive floor over ~700px |
| size | **one number** | **small** | ⚠️ **not small** |
| isolated? | ✅ `.tlg-page`, 1 template | ✅ `.ctl-page`, 1 template | ✅ `.ctl-page`, 1 template |
| re-opens an approved screen? | ⛔ **no** | ⛔ **no** | ⛔ **no** |
| verification needed | 1440 + a couple of widths | 1920 + 1440 | ⚠️ **a width sweep ~1180–1920** |
| carries a design call? | ⚠️ **yes** — should the rail stack at 1440? | no | ⚠️ **likely** |

⇒ ⭐ **D2 option A satisfies its own condition on isolation for all three** — nothing leaks to
another screen. ⛔ **But "isolated" is not "small": S17-b is a genuine responsive-layout change
with a sweep-sized verification bill, and S14 hides a design question inside a one-line fix.**
📌 They could be decided separately — **S14 and S17-a are cheap; S17-b is the one worth pausing
on.**

⛔ **NOTHING IMPLEMENTED. ⛔ No source file touched. ⛔ Awaiting D2.**
