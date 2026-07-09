# Screen 02 — Dashboard · Asset Requirement Specification (v2 — supersedes v1)

> **v1 of this document (same filename, prior revision) is superseded.** It proposed a NEW
> ChatGPT-generated brand mark (Asset 1) and a 12-glyph icon set (Asset 2). Rama's follow-up
> implementation instructions explicitly override the logo proposal — **"DO NOT use the logo
> shown in 02. Dashboard.png. Use the SAME Eagle logo used on the Login screen... Use the
> production optimized asset already available."** — and do not request icon polish this round.
> This revision reflects that narrower, final scope.

**Role:** Asset Manager (spec only)
**Source spec:** `gui/02. Dashboard.txt` + Rama's Screen-02 implementation instructions (logo rule,
header fields, layout rules)
**Asset library root:** `ops_dashboard/assets/`

---

## Genuinely missing assets: **ZERO**

| Need | Resolution |
|---|---|
| Sidebar/header brand mark | **Reuse** `assets/illustrations/login/login-chip-eagle.web.webp` (Screen 01's production asset) — explicitly directed, not a new asset. It needs a small **derivative** (the 800×800 art re-encoded at sidebar size, ~64px) so it stays crisp and lightweight at ~28px display size — this is a resize of an *already-approved* file, the same self-service operation already used to go from Rama's 1.77 MB master → the 106 KB production webp. **Not new art, not ChatGPT-generated, no wait required.** |
| Broker ID | **No asset** — data field. `summary.account_id` is already returned by `/api/dashboard` (`summary_bar.py:32`) — bind directly, zero backend change. |
| Client Name | **No asset** — data field. Verified absent from every existing read path (`db_reader.get_session_info`'s `session` table row = `session_date, account_id, broker, mode, trade_type, session_start, last_updated` — no name field anywhere). Render `"--"` per your own fallback instruction. |
| KPI/pipeline/capacity/service-health/events icons | **Not requested this round** — your instructions don't ask for icon polish (unlike the mockup image); skipping per "report only genuinely missing," not inventing a requirement. |

**Since nothing needs generation, there is nothing to stop and wait for — proceeding directly to
implementation**, per your own step 5 ("Continue implementation" once assets are ready — they
already are).

## Production asset policy (reconfirmed, applies here too)
Only the optimized derivative is committed: `assets/branding/logos/algocore-mark.web.webp` (new,
small, ≤~10 KB). No new master is generated (it's a re-encode of an existing approved file, and
per policy raw masters stay local-only / git-ignored regardless).
