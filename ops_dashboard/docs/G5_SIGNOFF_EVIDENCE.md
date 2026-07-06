# G5 — Pre-Cutover Local Validation + Browser Sign-Off Evidence

**Date (IST):** 04-Jul-2026 (Sat, market CLOSED) · **Surface:** LOCAL (Rama's PC),
working-tree G5 build. **No deploy / merge / push / VM restart / production change
was performed.** Output = objective evidence + a GO/NO-GO recommendation; Rama's
interactive browser pass (STEP 5) is the binding human gate.

---

## Build under test

| Item | Value |
|---|---|
| Local branch | `gui-g5-deploy` (off `origin/main` == `80be23a`; **local only, NOT pushed**) |
| Local commit | **`91cc207`** — "G5 redesign (G5a-G5d) + deploy plan — pre-cutover validation build" |
| Merge-gate (committed diff `origin/main..HEAD`) | **CLEAN — 45 files, all `ops_dashboard/**`** (0 core/orders/capital/strategies/broker/signals/main.py/config/schema) |
| Excluded from branch (left unstaged, ride a later docs commit) | `PATHS.md`, `docs/SYSTEM_MAP.md`, `docs/audit/{c2_chartink,telegram_token_shadow}...md`, `gui/` |

---

## STEP 2 — Clean-environment validation (the go/no-go gate)

Fresh isolated venv `ops_dashboard/.venv_validate` (throwaway) — pinned GUI deps
only, zero PC-global contamination.

| Check | Result |
|---|---|
| Installed | Flask 3.1.3 · Waitress 3.0.2 · pyotp 2.9.0 · PyYAML 6.0.3 · pytest 8.3.4 |
| **I4 gate** — `pip show kiteconnect` | `Package(s) not found` (exit 1) → **kiteconnect ABSENT = I4 PASS** |
| **Full suite** — `pytest -q` | **354 passed / 0 failed** in 87.74s (`pytest exit=0`) |
| Env-only failures | **0** — the ~32 known PC env-only failures belong to the MAIN repo suite, NOT this isolated GUI suite. Nothing to separate. |

### Pinned-contract / isolation gate tests — explicitly confirmed PASS (28/28)
| Gate | Test(s) | Result |
|---|---|---|
| Pinned counts 6/13/8/6/4 unmoved | `test_pinned_contract_counts_unmoved` [v41,v42] · `test_pinned_shapes_frozen_after_g5c` [v41,v42] · `test_pinned_shapes_frozen_after_g5d` [v41,v42] | PASS |
| Pipeline == 13 stages | `test_pipeline_endpoint_still_13_stages` [v41,v42] | PASS |
| HARD_KILL blink == 1 (only state that blinks) | `test_hard_kill_blink_still_single` · `test_hard_kill_state_reflected_and_blink_binding` [v41,v42] | PASS |
| Two-state honesty (broker never fabricated) | `test_two_state_panel_is_honest_never_fabricates` [v41,v42] · `test_positions_two_state` [v41,v42] · `test_holdings_template_broker_first_two_state` | PASS |
| Single System Score (no "Signal Score") | `test_score_chip_is_system_score_only` [v41,v42] · `test_export_off_and_no_signal_score` | PASS |
| ExportButton disabled (Q3 OFF) | `test_export_button_disabled_by_default` [v41,v42] · `test_export_button_disabled_on_g5b_screens` [v41,v42] | PASS |
| Isolation I1–I7 | `test_a_no_production_imports` · `test_b_db_connection_is_readonly` [v41,v42] · `test_c_venv_has_no_kiteconnect` | PASS |
| **CDN / no-network gate** | `test_d_no_cdn_or_network_refs_in_frontend` | PASS |

---

## STEP 6 — Automated route sweep (objective backstop)

**(a) Live unauthenticated sweep** — real Waitress server, real overlay, on
`127.0.0.1:8500`, raw status (no redirect-following):

| Route class | Count | Result | Meaning |
|---|---|---|---|
| Page routes (`/` + 29 `_PAGES`) | 30 | **all 302 → /login** | route registered (no 404) + auth enforced |
| API routes (`/api/*` static) | 30 | **all 401** | endpoint registered + auth enforced |
| `/login` | 1 | **200** | login page renders |
| **404 (missing route)** | — | **0** | no dead nav links at routing level |
| **500 (framework crash)** | — | **0** | app imports + dispatches cleanly |

**Verdict: CLEAN** — 61/61 routes as expected; 0 404, 0 500.

**(b) Authed sweep** — via the test client (the authed mechanism the suite uses):
**120 passed** = 60 static routes (30 page + 30 API) × 2 fixtures (v41, v42), **every
route 200 authed**.

*Dynamic routes not in the live static sweep (covered by unit tests):*
`/api/strategies/<name>`, `/api/trade-story/<trade_id>`, `/api/reports/download`
(gated OFF by design).

---

## STEP 5 — Browser sign-off checklist (Rama clicks; binding human gate)

**Status: PENDING — Rama's live browser pass.** This checklist cannot be self-signed;
it is Rama's binding gate before promotion. For each item, the **objective backing**
column notes what an automated test/sweep already confirms, so the visual pass is
faster. Mark ✅/❌ during the browser pass; ❌ rows get a one-line note + screen.

| Group | Item | Objective backing (already confirmed) | Rama |
|---|---|---|---|
| AUTH | login+TOTP works | login page 200; auth enforced on all routes | ☐ |
| AUTH | anon `/api/dashboard` → 401 | **CONFIRMED** — live sweep: 401 | ☐ |
| AUTH | logout → /login | route registered | ☐ |
| NAV | 5 groups present, every item opens, no dead link, no leftover "soon" | **CONFIRMED** — all 30 page routes 302→authed-200; `test_nav` PASS | ☐ |
| DASHBOARD | 13 stage cards · KPI row · Best/Worst · Recent Events+Alerts · capacity | **13 stages CONFIRMED** by test; content visual | ☐ |
| TRADING | Strategies (sort+tabs+ScoreChip) · Signals/Orders carried 200 | ScoreChip single-score CONFIRMED; routes 200 | ☐ |
| TRADING | Positions two-state (broker MTM/RR/LTP = "Pending Broker Source (G4)") | **two-state honesty CONFIRMED** by test | ☐ |
| TRADING | Holdings broker-first two-state (Qty/Delta/Recon = "Pending (G4/P1)", no fake numbers) | **CONFIRMED** by test | ☐ |
| ANALYTICS | Ranking medals+5 modes · Health (no Signal Score) · Scanner funnel · Trade Explorer story ("not captured" honest) · P&L period switch (realized+MTM two-state) · Slippage · Execution carried | no-Signal-Score + two-state CONFIRMED; period/content visual | ☐ |
| OPERATIONS | Live Activity feed advances · System Health carried 200 · Capital&Risk 4-in-1+long/short+MTM two-state · Config drift+"author not captured" · **Controls READ-ONLY 8 fields, ZERO buttons/forms, G3 disabled** | **Controls zero-write CONFIRMED** (grep-proven + test); feed-advance visual | ☐ |
| INVESTIGATION | Audit ("user not captured") · Trade Logs (strategy+scanner+time+exit+ScoreChip) · System Logs (severity+tail) | routes 200; content visual | ☐ |
| GLOBAL | ExportButton disabled everywhere · tables sort+paginate · no "Signal Score" text · mobile sidebar collapses | **Export-off + no-Signal-Score CONFIRMED** by test; sort/mobile visual | ☐ |
| PARITY | mode badge PAPER/LIVE = data only, no behavioural branch | no screen branches on mode (display-only attribute) | ☐ |

---

## STEP 8 — GO / NO-GO recommendation

**Objective gates (all machine-verifiable) — GREEN:**
- ✅ Clean-env suite: **354 passed / 0 failed** (0 env-only in this suite).
- ✅ Pinned counts 6/13/8/6/4 + HARD_KILL blink + isolation I1–I7 + CDN gate: **PASS**.
- ✅ Every route: no 500, no 404 (live 61/61 as expected; authed 120/120 = 200).
- ✅ Merge-gate diff = `ops_dashboard/**` only.

**Human gate (STEP 5 browser content): PENDING** — Rama's binding pass not yet run
(cannot be self-signed; the production overlay stores only a password hash, so no
autonomous login).

### Verdict: **CONDITIONAL GO**
Recommend **GO** — every objectively-verifiable gate is green, and the STEP-5 items
that map to automated assertions (auth-401, export-off, two-state honesty, single
System Score, Controls zero-write, 13 stages, pinned counts, no-404/500) already
**PASS**. The remaining STEP-5 items are visual/content confirmations.

**Contingent on:** Rama's interactive browser pass confirming the visual/content
items. If any STEP-5 item fails (a fabricated broker number, a write control, a
missing/broken screen, wrong period numbers) → **NO-GO**, stop, report.

This is a **recommendation only.** Rama's browser sign-off is the binding human gate;
the actual merge / push / VM deploy / soak resume happens later, off-market, on
Rama's explicit GO — none of it in this validation pass.

---
*No secrets/tokens recorded. Local branch + local commit + local run only.*
