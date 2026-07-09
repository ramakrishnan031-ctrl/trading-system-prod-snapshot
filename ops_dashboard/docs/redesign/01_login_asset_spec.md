# Screen 01 — Login · Asset Requirement Specification

**Role:** Asset Manager (spec only — no implementation)
**Source spec:** `gui/01. Login-Screen.txt`
**Target mockup:** `gui/01. Login-Screen.png` (high-fidelity concept, AI-generated)
**Asset library root:** `ops_dashboard/assets/`
**Current live screen:** `ops_dashboard/frontend/templates/login.html` (deployed, merge `9815786`)

---

## ⚠️ Two constraints that shape EVERY login asset — read before generating

### 1. Pre-auth delivery constraint (UNIQUE to the login screen)
The login page is the **only** screen served *before* authentication. `backend/app.py`
enforces `login_required` on **all** routes — including `/static/*` — with only
`_LOGIN_EXEMPT = {"auth.login_get", "auth.login_post"}` exempt (`app.py:6, 43, 126`).

**Consequence:** an unauthenticated browser **cannot fetch any file** under `/static/`
or a future `/assets/` route — the request 302-redirects to `/login` and the image
breaks. So a login asset **cannot be referenced as an external file URL** the way the
other 21 screens can. It must be delivered by either:
- **(a) inlined** — SVG pasted inline, or a raster embedded as a `data:` base64 URI,
  directly in `login.html` (current approach, zero extra request); **or**
- **(b) a narrow un-authed route** added just for login art (backend change; small
  pre-auth exposure surface).

**Impact on this spec → prefer SVG for login assets** (inlines cleanly, tiny, crisp at
any DPI). If a photorealistic raster is generated instead (like the `.png` mockup), it
**must** be WebP, optimized, and **base64-inlined** — so keep it **≤ ~150 KB** or it
bloats the login HTML on every page load.

> This caveat is **login-only**. Screens 02–22 are all behind auth, so ChatGPT's
> file-based asset-library model works perfectly for them. Do **not** generalize this
> constraint to the rest of the project.

### 2. Screen 01 is already built and deployed
The login screen already ships a **hand-authored inline-SVG** chip + heraldic eagle
(`login.html:96–181`) plus an inline footer shield (`:196–198`). PCB texture is done with
pure CSS gradients (`:38–41`); fonts are a system stack (Inter → IBM Plex Sans → Segoe UI,
no web-font files). So these assets are an **upgrade/replacement of working art**, not
net-new — **no screen is blocked** waiting on them. The `.png` mockup is the *target*;
the job of Asset 1 is to close the gap between today's simple linework and that fidelity.

---

## Asset summary

| # | Asset | Category | Type (rec.) | Priority | New or reuse |
|---|-------|----------|-------------|----------|--------------|
| 1 | Chip + heraldic-eagle hero | illustrations/login | **SVG** (WebP fallback) | **Mandatory** | New (upgrades existing inline SVG) |
| 2 | Hero PCB / blueprint texture | backgrounds/login | SVG or WebP | Optional | Reuse (CSS gradient already covers it) |
| 3 | Security "shield-check" icon | icons/status | **SVG** | Optional | Reuse (inline shield already exists) |
| 4 | Brand fonts (Inter, IBM Plex Sans) | — (woff2) | woff2 | Optional | Reuse (system-font stack in use) |

---

## Detailed specification (9 fields)

### Asset 1 — Chip + heraldic-eagle hero illustration
1. **Category:** illustrations
2. **Suggested filename:** `login-chip-eagle.svg` (or `login-chip-eagle.webp`)
3. **Target folder:** `ops_dashboard/assets/illustrations/login/`
4. **Asset type:** **SVG preferred** (vector, crisp, inlines pre-auth, tiny). WebP only if a
   photorealistic raster is chosen — then transparent bg, ~1200×1200, **≤ 150 KB**, base64-inlined.
5. **Purpose:** Primary left-pane hero (40–45% width). Communicates *"Secure Operations
   Control Tower"* identity — strength, protection, precision.
6. **Reusable?** **NO** — login-specific. (The eagle emblem *sub-element* could later be
   extracted as a reusable brand mark — see "Candidates" below.)
7. **Priority:** **Mandatory**
8. **Can existing asset be reused?** **Partially** — a functional hand-authored inline SVG
   already exists (`login.html:96–181`). This asset replaces it at mockup fidelity; current
   art keeps the screen working until then (not a blocker).
9. **Image-gen prompt:** *(see Prompts section, Prompt A)*

### Asset 2 — Hero PCB / blueprint background texture
1. **Category:** backgrounds
2. **Suggested filename:** `login-pcb-texture.svg` (or `login-pcb-texture.webp`)
3. **Target folder:** `ops_dashboard/assets/backgrounds/login/`
4. **Asset type:** SVG pattern (seamless) or seamless WebP
5. **Purpose:** Matte charcoal hero backdrop with a *very subtle, low-contrast* PCB/blueprint
   texture behind the chip.
6. **Reusable?** **YES** — could back other dark auth screens.
7. **Priority:** **Optional** — the spec's "very subtle, low contrast" texture is already met
   by a pure-CSS repeating-gradient grid (`login.html:38–41`). Generate only if a richer
   texture is explicitly wanted.
8. **Can existing asset be reused?** **YES** — CSS grid already satisfies the requirement.
9. **Image-gen prompt:** *(see Prompts section, Prompt B — only if pursued)*

### Asset 3 — Security "shield-check" icon
1. **Category:** icons (status)
2. **Suggested filename:** `shield-check.svg`
3. **Target folder:** `ops_dashboard/assets/icons/status/`
4. **Asset type:** **SVG**, single-color, stroke-based
5. **Purpose:** Footer indicator beside *"Read Only · TOTP Protected"*; reinforces protection.
6. **Reusable?** **YES** — a shield/secure icon recurs across dashboard / operations / system screens.
7. **Priority:** **Optional** — a working inline shield already exists (`login.html:196–198`).
   Promoting it to a shared asset file mainly benefits screens 02–22 (which *can* load `/static`).
8. **Can existing asset be reused?** **YES** — reuse/extract the existing inline shield.
9. **Image-gen prompt:** *AI image-gen is a poor fit for a tiny monochrome line icon (it yields
   raster, not crisp SVG). Recommend hand-authoring or pulling from an icon set (e.g. Lucide
   `shield-check`). If generated anyway, see Prompt C.*

### Asset 4 — Brand fonts (Inter, IBM Plex Sans)
1. **Category:** — (font files, not under `assets/` image tree; e.g. `frontend/static/fonts/`)
2. **Suggested filename:** `Inter-Medium.woff2`, `Inter-SemiBold.woff2`, `IBMPlexSans-Medium.woff2`
3. **Target folder:** `ops_dashboard/frontend/static/fonts/` (**note:** same pre-auth caveat —
   a `@font-face` file URL won't load pre-auth on login; base64-inline the woff2 in the login
   CSS, or accept the system-font fallback there)
4. **Asset type:** woff2
5. **Purpose:** Guarantee the spec's preferred typography (Inter / IBM Plex Sans) instead of
   relying on the client having them installed.
6. **Reusable?** **YES** — all 22 screens.
7. **Priority:** **Optional** — `login.html` already uses a system stack ending in **Segoe UI**,
   which is native on the target Windows clients, so brand type renders acceptably today.
8. **Can existing asset be reused?** **YES** — current system-font stack. Bundle woff2 only if
   pixel-consistent brand type across machines is required.
9. **Image-gen prompt:** N/A (source from Google Fonts / self-host, SIL OFL licensed).

---

## Image-generation prompts (for NEW assets only)

### Prompt A — Chip + heraldic-eagle hero (Asset 1) — MANDATORY
> A premium, enterprise-grade hero illustration for a secure trading operations platform.
> A futuristic **rounded-square microchip** rendered as **transparent smoked glass**, with
> faint **internal PCB traces** visible through the body and a **soft neon-blue edge glow**
> outlining the chip. **Metallic connector pins on all four sides** (top, bottom, left, right),
> **equally spaced and symmetrical** — remove all external circuit wires. Centered inside the
> chip, a **detailed heraldic eagle emblem**, **metallic / engraved** appearance, wings spread
> with a shield at the chest, integrated into the glass — conveying strength, protection and
> precision. Background: **matte charcoal-black** with a **very subtle, low-contrast**
> blueprint / PCB texture. The chip occupies a **modest portion of the frame with generous
> negative space** around it (~15% smaller than a full-bleed concept). Style: cinematic,
> minimal, high-end, corporate, dark theme. Neon-blue accents (**#3ba7ff / #58a6ff**) on a
> near-black (**#05070c**) background. **No text, no charts, no marketing graphics, no
> particles, no animation.** Front-facing, centered composition. High detail, crisp edges.
>
> **Delivery:** SVG vector if possible; otherwise a **transparent-background WebP/PNG**,
> ~1200×1200, **optimized to ≤ 150 KB** (it will be base64-inlined into the login page).

### Prompt B — Hero PCB texture (Asset 2) — OPTIONAL
> A **seamless, tileable** dark PCB blueprint texture. Thin blue circuit lines and node dots on
> a **matte charcoal-black** background, **very subtle and low-contrast**, no focal point, no
> text, minimal. Suitable as a repeating background behind a centered emblem.
>
> **Delivery:** seamless WebP (~512×512 tile) or an SVG `<pattern>`. Only if a richer texture
> than the current CSS grid is wanted.

### Prompt C — Shield-check icon (Asset 3) — OPTIONAL, prefer hand-authored SVG
> A minimal **line-art security shield** with a subtle check/lock, single-color stroke, flat,
> enterprise, no fill, no gradient — icon style. *(Prefer Lucide `shield-check` or hand-authored
> SVG for crispness; AI raster output is discouraged for icons.)*

---

## Candidates for later / global — NOT required by the login spec (do not invent now)
- **AlgoCore eagle brand mark** (`branding/shields/` or `branding/logos/`): the spec renders
  "AlgoCore Systems" as *text*, so no logo image is required here. If a global brand mark is
  wanted, extract it from Asset 1 once that exists — defer until a screen actually needs it.
- **Favicon / app icon**: every screen needs one eventually, but it's out of this screen's spec.
- **Wordmark image**: not needed — two-tone styled text already satisfies the branding block.

---

## Net requirement
- **1 Mandatory asset** to generate now: **Asset 1** (chip + eagle hero) — SVG preferred, else
  WebP ≤ 150 KB, both destined to be **inlined** into `login.html` (pre-auth constraint).
- **0 blockers**: the screen is live on functional inline art; assets 2–4 are optional upgrades
  already covered by CSS / system fonts / an existing inline icon.
