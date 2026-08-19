# LIVE VISUAL-APPROVAL LEDGER — S01 … S22
**Opened 19-Aug-2026 22:36 IST · one screen at a time · ⛔ no batching**

⛔ **A screen is VISUALLY APPROVED only when Rama explicitly approves that screen.**
⛔ Approval is **never** inferred from: tests passing · implementation complete · a previous
approval · artwork matching · a generated PNG · my own assessment.
⭐ **Approval basis is BROWSER MODE** — the live app rendered from its real route, ⛔ not a static
PNG, ⛔ not source HTML, ⛔ not a description.
📌 Only the CURRENT screen may move `PENDING → SHOWN → DISCUSSION → APPROVED`.
⛔ **No future screen is pre-approved.**

## STATE

| # | screen | browser shown | discussion | corrections | explicit approval | status |
|---|---|---|---|---|---|---|
| **S01** | Login | ✅ 22:0x IST · live `/login` @1920 + 1440 | ✅ | ✅ **1** — hero/card gap, then a 3.0% left nudge of the chip | ✅ **"Approved"** — 19-Aug-2026 **22:36 IST** | 🟢 **VISUALLY APPROVED** |
| **S02** | Dashboard | ⏳ | — | — | — | ⏳ PENDING |
| **S03** | Strategies | ⏳ | — | — | — | ⏳ PENDING |
| **S04** | Signals | ⏳ | — | — | — | ⏳ PENDING |
| **S05** | Orders | ⏳ | — | — | — | ⏳ PENDING |
| **S06** | Positions | ⏳ | — | — | — | ⏳ PENDING |
| **S07** | Trade Explorer | ⏳ | — | — | — | ⏳ PENDING |
| **S08** | Capital & Risk | ⏳ | — | — | — | ⏳ PENDING |
| **S09** | P&L Analytics | ⏳ | — | — | — | ⏳ PENDING |
| **S10** | Slippage Analytics | ⏳ | — | — | — | ⏳ PENDING |
| **S11** | Execution Analytics | ⏳ | — | — | — | ⏳ PENDING |
| **S12** | System Health | ⏳ | — | — | — | ⏳ PENDING |
| **S13** | Audit | ⏳ | — | — | — | ⏳ PENDING |
| **S14** | Trade Logs | ⏳ | — | — | — | ⏳ PENDING |
| **S15** | System Logs | ⏳ | — | — | — | ⏳ PENDING |
| **S16** | Configuration | ⏳ | — | — | — | ⏳ PENDING |
| **S17** | Controls | ⏳ | — | — | — | ⏳ PENDING |
| **S18** | Live Activity | ⏳ | — | — | — | ⏳ PENDING |
| **S19** | Strategy Ranking | ⏳ | — | — | — | ⏳ PENDING |
| **S20** | Strategy Health | ⏳ | — | — | — | ⏳ PENDING |
| **S21** | Scanner Attribution | ⏳ | — | — | — | ⏳ PENDING |
| **S22** | Holdings | ⏳ | — | — | — | ⏳ PENDING |

**APPROVED: 1 of 22.**

---

## S01 — LOGIN · APPROVED 19-Aug-2026 22:36 IST

**Approval basis:** browser mode — the live application at `/login`, rendered and measured at
**1920×1080** and re-measured at **1440×900**, **1200×800** and **780×900**.

**Rama's words:** *"Approved"* (after *"screen is okay for me, but if you slighly move left side
eagle panel to left side slightly say 2-4%"*).

### CORRECTION APPLIED BEFORE APPROVAL — ⛔ nothing else touched
| | |
|---|---|
| **① reported** | *"Reduce the unnecessarily large horizontal empty space between the left hero image/chip and the right login card."* Boxed in red on `Downloads/login_screen.png` |
| **cause** | both panes used `justify-content: center`, so the hero's **188px** slack and the card's **336px** slack met at the split line — plus 24px padding = **548px** at 1920. ⛔ Nothing was oversized; it was **two centrings back to back** |
| **fix** | `justify-content` → `flex-end` (hero) / `flex-start` (card pane), each with a clamped gutter toward the split line; mobile re-centred because there is no split line when the hero is hidden |
| **② reported** | *"slighly move left side eagle panel to left side slightly say 2-4%"* |
| **fix** | the offset became `margin-right: clamp(32px, 8vw, 160px)` **on the image** |

### 🔴 A REGRESSION I INTRODUCED AND THEN FIXED — recorded, ⛔ not buried
The first fix used **`padding-right` on the pane**. Padding shrinks the pane's **content box**, and
the chip's width is a **percentage of that box**, so it silently shrank the eagle:
**1440 · 380.6 → 335.0px (−12%)** and **1200 · 316.1 → 278.0px (−12%)**.
⚠️ **1920 hid it entirely** — the `420px` cap was binding there, and 1920 was the only viewport on
screen at the time. ⭐ Moving the offset to a **margin on the image** leaves the measured box
untouched, so `min(64%, 420px)` resolves as it always did. **All three widths verified back at
420.0 / 380.6 / 316.1.**

### MEASURED, BEFORE → AFTER
| viewport | chip↔card gap | chip left | shift | split | overflow | chip inside its `overflow:hidden` pane |
|---|---|---|---|---|---|---|
| **1920** (vp 1896) | **548 → 246** | 281.6 → **224.6** | **57px = 3.0%** | 42.0 / 58.0 ✓ | no | ✓ |
| **1440** (vp 1416) | **327 → 184** | 143.3 → **100.8** | **43px = 3.0%** | 42.0 / 58.0 ✓ | no | ✓ |
| **1200** (vp 1176) | — → 153 | → 83.7 | 3.0% | 42.0 / 58.0 ✓ | no | ✓ |
| **780** (vp 756) | hero hidden | — | — | — | no | card centred **188 / 188** |

### ACCEPTED DEVIATIONS — exact wording, carried forward
1. **Hero artwork differs from `01. Login-Screen.png`.** The artwork PNG shows a **neon-blue
   wireframe chip with a blue eagle**; the live screen shows a **smoked-glass chip with a metallic
   engraved eagle**. The **written TXT governs** and describes the live one — *"Transparent smoked
   glass appearance"*, *"Internal PCB traces visible"*, *"Detailed heraldic eagle — Metallic /
   engraved appearance"*. `01_login_IMPLEMENTED.png` matches the live screen. ✅ **Accepted.**
2. **Two sub-13px items remain by decision:** `<label>` **12.48px**, `.foot` **11.52px**. S01 is the
   only screen whose TXT states **no px minimum**, so B1 excluded it. ✅ **Accepted, unchanged.**
3. **The composition sits left of centre** (margins 224 / 649 at 1920). Structural: with equal
   gutters the composition's centre is fixed near the split line, and a tight gap **plus** centred
   margins is impossible while the hero pane is 42% — centring 800px of content at 1920 would push
   the chip past the pane boundary, where `overflow: hidden` would clip it. ✅ **Accepted.**

### ⛔ CONFIRMED UNCHANGED
hero artwork · eagle/chip artwork · colours · fonts · wording · fields · field order · Sign In ·
footer · card styling · **hero proportions (chip size verified identical at 3 widths)** ·
authentication behaviour · login/dashboard routing · S01 numbering · dashboard design ·
**shared CSS** · every other screen. Diff = **`login.html` only**.

### ROUTE FINDING CARRIED FORWARD
**S01 and S02 are separate surfaces**, established from route/template evidence, ⛔ not assumption:
`GET /login` renders `login.html` and redirects an authenticated session to `dashboard_page`;
`POST /login` on success redirects to `dashboard_page`; `/` renders `dashboard.html`;
`login.html` extends nothing and loads no `style.css`; and S01's spec forbids a dashboard preview.
⛔ **Numbering unchanged; no duplicate screen invented.**
