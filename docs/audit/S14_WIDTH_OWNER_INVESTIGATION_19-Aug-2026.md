# S14 STRUCTURAL INVESTIGATION — THE WIDTH OWNER, MEASURED
**19-Aug-2026 ~16:45 IST. ⛔ INVESTIGATION ONLY — no source changed, no fix applied, no commit
of any fix.** HEAD `b14dea3`, tree CLEAN, **PUSHED = NO · DEPLOYED = NO**.

## 1 · OWNERSHIP CHAIN AT 1440×900 (clientWidth 1416)
⛔ Measured by **internal `scrollWidth`/`clientWidth` and rect-vs-parent-clientWidth**, ⛔ never
from a right-edge position.

| level | scrollW | clientW | rect | parent clientW | **exceeds parent** | overflow-x |
|---|---|---|---|---|---|---|
| `html` | 1760 | 1416 | 1416 | — | — | visible |
| `body` | 1760 | 1416 | 1416 | 1416 | — | visible |
| `.shell` | 1760 | 1416 | 1416 | 1416 | — | visible |
| `.main-col` | **1580** | **1236** | 1236 | 1416 | — | visible |
| 🔴 **`main.content`** | 1580 | 1580 | **1580.2** | **1236** | 🔴 **+344.2** | visible |
| `.tlg-page` | 1548 | 1548 | 1548.2 | 1580 | — | visible |
| `.tlg-head` | 1548 | 1548 | 1548.2 | 1548 | — | visible |
| `.tlg-work` | 1548 | 1548 | 1548.2 | 1548 | — | tracks **`1202.17px 330px`** |
| `.tlg-left` | 1202 | 1202 | 1202.2 | 1548 | — | visible |
| `.tlg-rail` | 330 | 330 | 330 | 1548 | — | visible |
| `.tlg-tbl-wrap` | 1168 | 1168 | 1168.2 | 1200 | — | ✅ **auto** |

⇒ 🔑 **`main.content` is the FIRST and ONLY element that exceeds its containing block.**
Everything below it fits *within main.content's own inflated width*; ⛔ nothing below it exceeds
its own parent. The page overflow (**1760 vs 1416 = 344px**) is `main.content` (1580.2) sitting
in a 1236px track, plus the shell's own padding.

## 2 · HYPOTHESIS VERDICTS — MEASURED AND RANKED

| | hypothesis | verdict | evidence |
|---|---|---|---|
| **H1** | missing `min-width:0` / width constraint on the main column | ✅ **CONFIRMED — this is the owner**, though the mechanism is a **missing width rule**, ⛔ not a missing `min-width` | `main.content` rect 1580.2 vs parent clientWidth 1236 |
| **H2** | three rail tables with `overflow-x: visible` | ⛔ **REFUTED** | with the rail **beside** (tracks `1202.17px 330px`) `.tlg-rail` is **330px** and fits; ⛔ no table exceeds its parent |
| **H3** | a table's intrinsic min-content expands the parent | ⛔ **REFUTED** | the wide table sits in `.tlg-tbl-wrap`, `overflow-x: **auto**`, **1168.2 inside 1200** — ✅ already contained |
| **H4** | main + rail + gap exceed the available width | ⚠️ **TRUE BUT DOWNSTREAM, ⛔ not the owner** | `1202.17 + 16 + 330 = 1548.2` — but that total is the *consequence* of `main.content` already being 1580, ⛔ not its cause. Constrain `main.content` and the `minmax(0,1fr)` track absorbs the difference |

⭐ **RANKING: H1 is the owner. H4 is its symptom. H2 and H3 are refuted by measurement.**

## 3 · EXACT ROOT OWNER
**`style.css:516`**
```css
main.content:has(> .dash-page) { width: 100%; max-width: 2100px; margin: 0; padding: …; }
```
**`trade_logs.html:32`**
```html
<div class="tlg-page" x-data="tradeLogsPage()" …>
```
🔑 **S14's page root is `class="tlg-page"` — it does NOT carry `dash-page`, and there is no
`main.content:has(> .tlg-page)` rule.** So **no width constraint ever applies to `main.content`
on this screen**, and it sizes to its content's intrinsic width (1580) instead of its 1236px
track.

### ⭐ THE SAME GAP EXISTS ON THREE MORE SCREENS — LATENT, ⛔ NOT SYMPTOMATIC
Every `main.content:has(> …)` rule, mapped against every campaign page root:

| constrained | page roots |
|---|---|
| `:has(> .dash-page)` `:516` | S02 · S03 · S04 · S05 · S06 · S07 · S09 · S10 · S11 · S12 |
| `:has(> .lav-page)` `:3668` · `.sr-page` `:4140` · `.sh-page` `:4349` · `.sca-page` `:4652` · `.hld-page` `:5105` · `.ctl-page` `:5463` · `.cfg16-page` `:5685` | S18 · S19 · S20 · S21 · S22 · S17 · S16 |
| 🔴 **NOT CONSTRAINED** | **`.cap-page` (S08) · `.aud-page` (S13) · `.tlg-page` (S14) · `.slg-page` (S15)** |

⇒ ⚠️ **S08, S13 and S15 share S14's missing constraint and are clean only because their content
happens to fit inside 1236px today.** ⛔ Not a defect to fix now — ⭐ but it means S14 is **an
instance of a class**, and those three would overflow the moment their content grows.
📌 **This also explains S08's pre-existing 9px internal KPI overflow surviving with a clean
page** — different symptom, same missing guard.

## 4 · PROPOSED MINIMAL FIX — ⛔ NOT APPLIED, ⛔ NOT MEASURED
**One new rule, mirroring the pattern seven screens already use:**
```css
main.content:has(> .tlg-page) { width: 100%; max-width: none; margin: 0; padding: …; }
```
- ✅ **one selector, no template change, no global CSS, no `overflow:hidden`, no breakpoint
  change, no table-width change**
- ✅ **preserves the artwork's side-by-side rail** — `.tlg-work` stays `minmax(0,1fr) 330px`, and
  once `main.content` is 1236 the `1fr` track absorbs the difference
- ✅ **the wide table already scrolls inside `.tlg-tbl-wrap` (`overflow-x: auto`)** ⇒ ⛔ nothing is
  hidden or truncated
- ⛔ **the alternative — adding `dash-page` to `trade_logs.html:32` — is REJECTED**: it would pull
  in every `.dash-page` style, a far wider change than the one property needed

⛔⛔ **EVIDENCE LEVEL, STATED HONESTLY: this is MECHANISM-DERIVED, ⛔ NOT YET MEASURED.** The card
forbids S14 code in this pass, so I have ⛔ not applied it and ⛔ cannot claim it works. ⭐ One
apply-measure-revert cycle would settle it, exactly as the S17 attribution was settled — **on
authorization.**
⚠️ **AND THE RESIDUAL RISK IS NAMED:** if `main.content` is constrained to 1236 and the rail keeps
its fixed **330px**, the `1fr` track becomes ~**890px** at 1440 — the main table then scrolls
horizontally *inside its own region* rather than the page scrolling. ⭐ That is the card's stated
target, ⛔ but it is a visible change to how S14 reads at 1440 and belongs in the D5 review.

## 5 · CROSS-SCREEN BLAST RADIUS — VERIFIED, ⛔ NOT ASSUMED
`main.content:has(> .tlg-page)` matches only a `main.content` whose **direct child** carries
`.tlg-page`, and **`.tlg-page` appears in exactly one template — `trade_logs.html`.**
⇒ 🏷️ **S14-ONLY.** ⛔ Not shared with any other numbered screen. ⛔ Not shared with any of the 9
legacy routes. ⛔ No shared/base CSS touched.

## 6 · IS IT SAFE TO AUTHORIZE?
⭐ **Yes, with one caveat.** The selector is provably S14-only, the pattern is already used by
seven screens, and it is a single property set. ⚠️ **But it is unmeasured**, and the 1440 reading
of the screen changes (table scrolls in-region instead of the page scrolling). ⇒ 📌 **Recommend
authorizing an apply-measure-revert verification FIRST**, then a decision on the measured result
— ⛔ rather than authorizing a blind commit.
