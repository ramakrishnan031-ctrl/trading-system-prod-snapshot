# GUI DECISION PACK — D1 / D2 / D3 / D5
**19-Aug-2026 ~16:20 IST. ⛔ DECISION PREPARATION ONLY — no source touched, nothing implemented.**
HEAD `aab9e700`, tree CLEAN, **48 ahead / 5 behind**, **PUSHED = NO · DEPLOYED = NO**.
Suite **1 failed / 2,040 passed** (⛔ never 1,795), sole failure the known environmental
`test_isolation::test_c_venv_has_no_kiteconnect`, untouched.

---

## 🔴 CORRECTION TO MY OWN D2 EVIDENCE — READ FIRST

`cf3d0d5` stated *"no shared CSS involved · no other numbered screen needs regression QA"* for
**both** S14 and S17. **That is right for S14 and for S17-a. It is WRONG for S17-b.**

I assessed the selectors surfaced by a **right-edge** scan (`.ctl-shell` / `.ctl-rail` /
`.ctl-row4`). An **internal-overflow** scan (`scrollWidth > clientWidth`) names a different owner:

```
S17 @1440   span.kpi-val.num   scrollWidth 271   clientWidth 156   over 115px
S17 @1920   span.kpi-val.num   scrollWidth 271   clientWidth 236   over  35px
```

⇒ 🔑 **the ramp is a KPI VALUE STRING that never shrinks** — as the cards narrow the overflow
grows, which is exactly the ramp shape. And **`.kpi-row` / `.kpi-card` / `.kpi-val` are GLOBAL**
(`style.css:414-425`), delivered by the shared `kpi_row` / `kpi_card` macros — ⛔ **not
`.ctl-page`-scoped.**

📌 **7 campaign screens import that macro: S08 · S09 · S10 · S11 · S12 · S16 · S17.**
⭐ **But an isolated form EXISTS and has precedent: `.pnl-page` and `.slp-page` ALREADY override
`.kpi-val` to `1.18rem`** (`style.css:2095`, `:2192`). So D2-C can stay S17-only by doing exactly
what S09 and S10 already do.

---

## 1 · D1 — S03 PLACEMENT

| | **A — accept current** | **B — move to S20** | **C — new detail surface** |
|---|---|---|---|
| code needed | ⭐ **NONE** — built, captured | remove 3 cols from S03 + add to S20 | build a surface + routing + tests |
| **target truly per-strategy?** | n/a | ✅ **VERIFIED** — S20 renders `x-for="r in tSorted(rows())" :key="r.strategy"`, a genuine per-strategy table | n/a |
| artwork support | ⚠️ TXT says DETAIL PAGE | ⚠️ TXT says **S03's** detail page, ⛔ not S20 | ⛔ **no artwork exists for it** |
| scope / risk | **lowest** | moderate | **highest** |
| screens re-opened | S03 only | **S03 + S20** | S03 + a new screen |
| visual impact | none beyond today | 3 columns move off S03 onto S20 | new surface, new nav path |

⭐ **A is lowest-risk and the only zero-code option.** ✅ **B's premise is VALID — S20 is genuinely
per-strategy**, so B is a real alternative, ⛔ not a straw man. ⛔ **C builds UI no artwork
specifies.** ⛔ **I am not deciding — A is presented as lowest-risk, ⛔ not as the answer.**

---

## 2 · D2 — THREE MICRO-FIXES, DECIDABLE SEPARATELY

| | **D2-A · S14** | **D2-B · S17-a** | **D2-C · S17-b** |
|---|---|---|---|
| **selector(s)** | `.tlg-page .tlg-work` + its `@media` | `.ctl-page .ctl-hrow` / `.ctl-hist` | 🔴 **global `.kpi-val`** — or a `.ctl-page` override |
| **root cause** | `@media (max-width:1400px)` against an effective **clientWidth 1416**; the fixed **330px** rail stays | non-wrapping flex row: content **303px** in a **266px** rail box (`over 37`) | KPI value string **271px** in a **156px** card at 1440 (`over 115`); never shrinks |
| **minimal change** | raise one breakpoint above 1416 | let the row wrap or truncate (`min-width:0` + ellipsis) | **C-1** `.ctl-page .kpi-val` override, as `.pnl-page`/`.slp-page` already do · **C-2** change global `.kpi-val` |
| **viewport effect** | fixes 1416; already clean ≤1400 | constant ~4px at **every** width incl. 2200 | ramp: +19@1800 · +52@1600 · +79@1440 · +89@1380 |
| **regression screens** | ⛔ **none** (`.tlg-page`, 1 template) | ⛔ **none** (`.ctl-page`, 1 template) | **C-1: none** · 🔴 **C-2: S08·S09·S10·S11·S12·S16·S17** |
| **rollback** | ⭐ trivial — one number | trivial — one rule | C-1 trivial · C-2 touches 7 screens |
| **hidden question** | ⚠️ **should the rail stack at 1440, or shrink?** — a design call | none | ⚠️ **does a smaller KPI value violate the artwork?** |
| **size** | 🟢 one number | 🟢 small | 🟡 small **if C-1** · 🔴 wide if C-2 |

⇒ ⭐ **D2-A and D2-B are cheap, isolated and independently revertible. D2-C is cheap ONLY in its
C-1 (page-scoped) form** — and that form has direct precedent on two already-approved screens.

---

## 3 · D3 — THE 63, IN FIVE TIERS
⛔ **No option recommended. ⛔ "Raise all" is not a default.** `px` is the current computed size;
`kind` marks decorative glyphs — the class the closure pass explicitly exempted.

**① BASE**

| class | px | reach | kind |
|---|---|---|---|
| `.nv-soon-tag` | 8.0 | ALL 21 | text |
| `.nav-status` | 11.52 | ALL 21 | text |
| `.nav-status` | 12.0 | ALL 21 | text |
| `.btn-logout` | 12.48 | ALL 21 | text |

**② MACRO**

| class | px | reach | kind |
|---|---|---|---|
| `.sc-k` | 9.6 | via macro | text |
| `.pcard-foot` | 10.24 | via macro | text |
| `.sev-chip` | 10.56 | S02 | text |
| `.kpi-label` | 10.56 | S16 | text |
| `.kpi-unit` | 10.88 | via macro | text |
| `.ts-note` | 10.88 | via macro | text |
| `.status-chip` | 10.88 | via macro | text |
| `.dt-arrow` | 11.2 | via macro | glyph |
| `.lc-ts` | 11.2 | S04 | text |
| `.dt-pginfo` | 11.52 | via macro | text |
| `.dt-size` | 11.52 | via macro | text |
| `.score-chip` | 11.52 | via macro | text |
| `.pcard-name` | 11.84 | via macro | text |
| `.rt-btn` | 11.84 | S09,S10,S11,S12,S13,S14,S15,S18,S19 | text |
| `.dt-pg` | 11.84 | via macro | text |
| `.ps-btn` | 11.84 | S09,S10,S11 | text |
| `.mono` | 12.16 | S05,S06,S07,S11,S12,S13,S14,S15,S16,S17,S18,S19,S20, | text |
| `.btn-export` | 12.16 | S05,S06,S07,S10,S11,S12 | text |
| `.events` | 12.8 | S02,S03,S08,S12,S13,S14,S15,S18 | text |
| `.flt` | 12.8 | S09,S10,S11,S12,S16,S17 | text |

**③ MULTI**

| class | px | reach | kind |
|---|---|---|---|
| `.cap-table` | 10.88 | S08,S09,S10,S11,S12,S13,S14,S15,S18,S19,S20,S21,S22 | text |
| `.dt-th` | 10.88 | S16,S17 | text |
| `.cap-table` | 11.52 | S08,S09,S10,S11,S12,S13,S14,S15,S18,S19,S20,S21,S22 | text |
| `.panel-sub` | 11.84 | S08,S11,S12,S13,S14,S15,S16,S17,S18,S19,S20,S21,S22 | text |
| `.panel-link` | 11.84 | S08,S18 | text |
| `.info-banner` | 12.8 | S02,S03 | text |

**④ SINGLE**

| class | px | reach | kind |
|---|---|---|---|
| `.bn-label` | 10.56 | S08 | text |
| `.curve-meta` | 10.56 | S08 | text |
| `.cap-status` | 10.88 | S08 | text |
| `.silence` | 10.88 | S20 | text |
| `.cfg-key` | 12.16 | S17 | text |

**⑤ NONE**

| class | px | reach | kind |
|---|---|---|---|
| `.mf-step` | 8.96 | none found | text |
| `.cap-inert` | 9.28 | legacy: capacity | text |
| `.bn-note` | 9.92 | legacy: risk,statistics | text |
| `.score-badge` | 9.92 | none found | text |
| `.twg` | 10.24 | none found | text |
| `.dn` | 10.24 | none found | text |
| `.score-reasons` | 10.24 | none found | text |
| `.tw-mode` | 10.56 | none found | text |
| `.tw-scanners` | 10.56 | none found | text |
| `.svc-state` | 10.88 | none found | text |
| `.tw-dir` | 10.88 | none found | text |
| `.fam-pill` | 10.88 | legacy: alerts,capital | text |
| `.failstrip` | 10.88 | none found | text |
| `.rt-label` | 11.2 | none found | text |
| `.cap-note` | 11.2 | legacy: capacity | text |
| `.drift-key` | 11.2 | none found | text |
| `.cfg-val` | 11.2 | none found | text |
| `.bn-sub` | 11.52 | legacy: capital,exposure,risk,statistics,vm | text |
| `.logpre` | 11.52 | legacy: alerts | text |
| `.wrapcell` | 11.84 | legacy: alerts,capital | text |
| `.hbar-label` | 11.84 | legacy: exposure | text |
| `.hbar-val` | 11.84 | legacy: exposure | text |
| `.log-table` | 11.84 | none found | text |
| `.svc-chip` | 12.16 | none found | text |
| `.twg-row` | 12.16 | none found | text |
| `.cap-group-title` | 12.48 | legacy: capacity | text |
| `.drift-banner` | 12.8 | none found | text |
| `.g3-list` | 12.8 | none found | text |

### D3 NOTES THAT BOUND THE TABLE
- **① BASE (4)** — exactly Q2's radius; could ride the same re-approval. **`.btn-logout` 12.48px
  is readable text on every screen** and is the most visible survivor. `.nv-soon-tag` at **8.0px**
  is the smallest text in the application.
- **② MACRO (20)** 🔴 **highest risk** — reaches screens through `components.html`, so a change
  lands wherever the macro is imported. `.mono`, `.rt-btn`, `.events`, `.btn-export` and `.flt`
  drive controls and monospace data across many screens.
- **③ MULTI (6)** 🔴 — **`.cap-table` and `.panel-sub` each reach 13 screens**; `.cap-table`
  governs table density and is the single widest-reaching class in the set.
- **④ SINGLE (5)** 🟢 — cheapest and narrowest; one screen each.
- **⑤ NONE (28)** 🟢 — no campaign template references them by token.

⛔⛔ **SEARCH WIDTH ON ⑤, STATED: a token grep of the 21 campaign templates found nothing. That
is ⛔ NOT proof the class is dead** — a dynamically composed `:class` binding would be missed.
✅ **Checked beyond the grep on 8 of 28:** `.logpre`→`alerts` · `.hbar-label`→`exposure` ·
`.fam-pill`→`alerts`,`capital` · `.cap-inert`→`capacity` are **legacy-route only**; `.failstrip` ·
`.svc-state` · `.twg` · `.drift-banner` are referenced by **no template at all**.
⛔ **The remaining 20 were NOT individually checked, and the split is NOT extrapolated to them.**

⚠️ **ARTWORK REQUIREMENT — the honest position:** every screen's `.txt` states a **13px minimum**
in its READABILITY section, so the *written* requirement covers all readable text. ⛔ But the
closure pass exempted **decorative glyphs** (drag handles, sort arrows) and **that exemption was
never ruled on by Rama** — ⭐ which is precisely what a D3 option-B answer would settle.

---

## 4 · D5 — APPROVAL LIST

**FIRST APPROVAL (never approved):** **S01** ⭐ *(pre-auth · standalone `login.html` · never loads
`style.css` · ⛔ outside Q2 and outside every shared-CSS radius)* · S04 · S05 · S06 · S07 · S08

**RE-APPROVAL (changed since approval):** **S03** *(Q3 — plus D1 pending)* · **S09** *(axis
labels)* · **S12** *(collision fix)* · S10 · S11 *(typography)* · S02 · S13 · S15 · S16 · S18 ·
S19 · S20 · S21 · S22 *(Q2 chrome strip)*

🔴 **BLOCKED — ⛔ no approval possible until D2:** **S14** *(344px @1440)* · **S17** *(4px @1920,
83px @1440)*

📌 **Q2 re-QA is TARGETED:** all five raised classes live only in `base.html` — the sidebar +
top-status strip, identical on all 21 ⇒ re-check **that strip plus a page-overflow check**,
⛔ not a full re-audit of 21 screens.

## 5 · S01–S22 — ONE NEXT ACTION EACH

| screen | next action |
|---|---|
| **S01** | first visual approval — ⛔ independent of Q2 |
| **S02** | re-approve chrome strip |
| **S03** | 🔴 **rule D1**, then re-approve |
| **S04 – S08** | first visual approval (+ chrome strip) |
| **S09** | re-approve (axis labels + chrome) |
| **S10 · S11** | re-approve (typography + chrome) |
| **S12** | re-approve (collision fix + chrome) |
| **S13 · S15 · S16 · S18 – S22** | approve (+ chrome strip) |
| **S14** | 🔴 **rule D2-A** — blocked |
| **S17** | 🔴 **rule D2-B + D2-C** — blocked |

## 6 · DECISIONS REQUIRED
1. **D1** — **A** (zero code, lowest risk) · **B** (valid; re-opens S03 + S20) · **C** (no artwork).
2. **D2-A** S14 breakpoint · **D2-B** S17 rail row · **D2-C** S17 KPI value — ⭐ **and if C, then
   C-1 page-scoped (S17 only, precedented) or C-2 global (7 screens).**
3. **D3** — per tier ①–⑤, ⛔ not one blanket answer; **plus a ruling on whether decorative glyphs
   are exempt from the 13px floor.**
4. **D5** — 22 approvals; ⛔ S14/S17 cannot be approved until D2.

⛔ **Parked, untouched:** S07 RR Damage % · rejection taxonomy · S03 D1/D2 · S03 export · Gate E ·
layout-assurance Option A.
