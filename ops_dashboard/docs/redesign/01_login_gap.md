# Login Screen (01) — Gap Analysis

Branch: `gui-redesign-login-08jul` (off `main`@`271d24f`)
Spec: `gui/01. Login-Screen.txt` · Target mockup: `gui/01. Login-Screen.png`

## Correction to task premise

The instruction file assumed the deployed `/login` already implements most of
the spec ("chip+heraldic-eagle hero on the left... already implements most").
That is **not** the case for the code in this repo:

- `ops_dashboard/frontend/templates/login.html` is a single centered card,
  **no hero pane, no split layout, no chip/eagle illustration at all**.
- Full git history (`git log --all -- .../login.html`, `git grep --all eagle`)
  confirms this content never existed in any branch — `01. Login-Screen.png`
  is a **target mockup**, not a screenshot of the live app.

This is therefore closer to a from-scratch build of the hero pane within the
existing file, not a small polish pass. Confirmed presentation-only either
way — no backend/route/API/auth touch required (see §3-9). Proceeding per
the instruction's own contingency ("verify each spec point... change only
the deltas" — here the deltas are large but still 100% presentation).

## 1. Gap Analysis

| Spec requirement | Current state | Verdict | Required change |
|---|---|---|---|
| Title "AlgoCore Systems" | Present, plain single weight | PARTIAL | Two-tone: "AlgoCore" accent-blue bold + "Systems" light bold (per mockup) |
| Subtitle "Operations Control Tower · Secure Read-Only Access" | Present, exact text | MATCH | none |
| Desktop layout 40-45% hero / 55-60% card | Single centered card, no split | MISSING | flex/grid 2-pane layout |
| Mobile: hero hidden, card only | N/A (no hero exists) | MISSING | media query hiding hero pane, card centered full-width |
| Hero: rounded-square glass chip, PCB traces, neon-blue edge glow | Absent | MISSING | inline SVG chip (see §Hero asset) |
| Hero: heraldic eagle center emblem, metallic/engraved | Absent | MISSING | inline SVG eagle glyph inside chip |
| Hero: pins on all 4 sides, equal spacing, symmetrical | Absent | MISSING | inline SVG pins, straight rects, no wavy wire flourish |
| Hero: no animation/particles/charts | Absent (n/a) | MATCH (by default) | keep static, do not add animation |
| Hero: ~15% smaller than concept, generous negative space | Absent | MISSING | size chip to leave >40% padding in hero pane |
| Background: matte charcoal + subtle PCB texture, low contrast | Plain `#0d1117` | PARTIAL | keep dark base, add very low-opacity grid/trace texture |
| Card: dark glass | Solid `#161b22` flat panel | PARTIAL | add translucency/blur + border glow consistent with mockup |
| Fields: Username / Password / TOTP | Present; only TOTP has placeholder | PARTIAL | add placeholder text "Enter your username" / "Enter your password" |
| Primary button "Sign In" | Present, green, correct text | MATCH | keep colors, restyle to match card weight |
| Footer "Read Only · TOTP Protected" | Present, text-only | PARTIAL | add small shield icon (inline SVG) before text |
| Typography: Inter/Segoe UI/IBM Plex Sans, medium weight | `system-ui, -apple-system, Segoe UI, Roboto` — no Inter | PARTIAL | switch stack to match `style.css` G5a convention: `Inter, "IBM Plex Sans", system-ui, ...` (no CDN — Inter must resolve to system-installed font or be omitted gracefully; see risk) |
| No clutter/preview/charts/stats/animation | Compliant (minimal form) | MATCH | keep as-is |

## 2. Existing files affected

- `ops_dashboard/frontend/templates/login.html` (structure + all styling — file is intentionally self-contained since `/static/*` requires auth, see §10)
- No new files needed: hero art will be **inline SVG** inside `login.html`, not a separate asset (see §10 risk)

## 3. Backend impact
None. `backend/auth.py` and `backend/app.py` are not modified.

## 4. Route impact
None. `GET /login`, `POST /login`, `/logout` signatures and behavior unchanged.

## 5. API impact
None. No `/api/*` endpoints touched.

## 6. Frontend impact
`login.html` markup restructured into `.login-hero` + `.login-card` panes.

## 7. CSS impact
All new styles are inline `<style>` in `login.html` (scoped to this file only, as it already is — it does not `extend` `base.html` or import `style.css`). Zero changes to `frontend/static/style.css`, so zero risk to any other screen's tokens.

## 8. JS impact
None planned. Fields/submit behavior already correct (native form POST); no gap requires JS.

## 9. Template impact
Only `login.html`. No other template touched.

**Confirmed: no backend/route/API change of any kind.**

## 10. Risk assessment

- **Hero asset delivery**: `backend/app.py` enforces `login_required` on **all** routes including `/static/*` (`_LOGIN_EXEMPT = {"auth.login_get", "auth.login_post"}`), so a separate static image file would 404 for an unauthenticated visitor. Mitigation: hero is **inline SVG** directly in `login.html` (consistent with the file's existing self-contained design) — zero extra requests, trivially satisfies the "local, not CDN" requirement, and needs no guard change.
- **Font**: "Inter" is not vendored/installed; the CDN gate forbids fetching it externally. Mitigation: keep `Inter` first in the stack for users who have it installed system-wide, but rely on `"IBM Plex Sans", system-ui, -apple-system, "Segoe UI"` as the effective fallback (same pattern already used in `style.css` — no CDN link, so this is a no-risk, gate-compliant declaration, not an actual font fetch).
- **Shared CSS leak**: none possible — `login.html` styles are inline and scoped to this file only, `style.css` is untouched.
- **Mobile breakpoint regression**: none — this is a new, isolated template; no other screen shares it.
- **Scope creep**: kept to this one file; no shared component extracted (per instructions, not needed for a single screen).

## Pause-point disposition

No backend/auth change is required — the delta is larger than the instruction
assumed, but 100% of it stays in `login.html` presentation. Proceeding to
Phase 3-4 implementation.
