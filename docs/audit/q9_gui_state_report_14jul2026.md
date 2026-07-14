# Q9 — GUI state report: ops_dashboard + AlgoPilot design repo (14-Jul-2026)

**Purpose:** work-order item #5 — report what is wired, what is missing, and a concrete
recommendation. **Rama decides.** Read-only survey; no code touched. VM-runtime claims marked
`[verify off-market]` (mid-market, no prod-touch).

---

## 1. The two things

| | What it is | Where |
|---|---|---|
| **ops_dashboard** | The BUILT, deployed Flask ops dashboard (the real app) | `ops_dashboard/` · deployed `gui-dashboard.service` → `/home/ubuntu/systems/trading-system/ops_dashboard` (a SEPARATE checkout from the trading bare-repo tree) |
| **AlgoPilot design repo** | 15 numbered screen mockups (PNG + TXT visual specs) + the comprehensive redesign plan | `gui/01..15.{png,txt}` · plan `ops_dashboard/docs/G5_REDESIGN_PHASE_B.md` (22-screen superset) · pixel asset specs `ops_dashboard/docs/redesign/` |

---

## 2. What is WIRED today (built + deployed)

**The app is functionally comprehensive, not a 3-screen stub.** Login-gated (PBKDF2 + TOTP +
lockout, `auth.py`), **read-only by construction** (isolation AST gate — no prod imports; DB opened
`mode=ro`; no write path anywhere).

- **12 API blueprints** (`app.py:104-114`): dashboard, pipeline, capacity, strategies, trading,
  risk_capital, system, analytics, **analytics2 (G5c)**, **operations (G5d)**.
- **~34 page routes** (`_PAGES`, `app.py:150-173`):
  - Original G2b screens (21): strategies, signals, orders, positions, holdings, capacity, risk,
    capital, exposure, pnl, services, vm, logs, audit, alerts, slippage, execution, statistics,
    reports, config, controls.
  - G5 additive screens (8): `capital-risk` (consolidated), `strategy-ranking`, `strategy-health`,
    `scanner-attribution`, `trades` (Trade Explorer), `pnl-analytics`, `live-activity`, `trade-logs`.
- **Read-only readers:** config_reader, db_reader, host_reader, log_reader, metrics_client.
- **Services:** strategy_tower, strategy_score, pipeline_state, capacity, summary_bar, freshness,
  config_view, operations, analytics_period.

So **the G5 comprehensive-redesign backend + routing largely LANDED** (the G5c/G5d blueprints and
their templates are present in `main` → carried into the deployed tree). `[verify off-market: that
the running gui-dashboard checkout is current with main's G5 code, and which new screens serve real
data vs a nav placeholder — app.py:164 calls them "nav placeholders → live", so depth varies.]`

---

## 3. What is MISSING / in-progress

**A. The pixel-perfect AlgoPilot visual redesign is only 3 of 15 screens deep.**
The screen-by-screen "read PNG+TXT → pixel-study → reuse the frozen design language → pixel-review →
deploy" workflow ([[feedback_gui_redesign_workflow]]) has FROZEN/APPROVED only:
- **01 Login** (deployed `e0b26b7`), **02 Dashboard** (frozen `b7d72c7`), **03 Strategies**
  (approved `065d32e`/`ab14650`).
- Redesign asset specs exist for **01/02/03 only** (`docs/redesign/`).
- **Next = 04 Signals** (`gui/04. Signals.{png,txt}` in hand). Screens **04–15 are built
  functionally but not yet pixel-matched** to the mockups (older/interim styling).

**B. Two standing Rama-decision gates (from the Phase-B backlog §K):**
- **Q3 / XLSX export policy (L7):** `table_export_enabled` ships DEFAULT OFF; turning per-screen
  XLSX/CSV export on is Rama's call (copy-protection).
- **Schema-pause PROPOSALS P-1..P-4 (not scheduled — need Rama's trigger):** each unblocks an
  honest "UNAVAILABLE" field, and each needs a new persisted source on the TRADING side:
  - P-1 per-stage exec timings → Execution "Validation/Risk/Capital" times (now "not instrumented").
  - P-2 per-change author + control/auth audit rows → Audit "who changed / login-logout" (now "not captured").
  - P-3 ship P1 `eod_broker_reconciliation` (already built, UNPUSHED) → Holdings broker-side delta.
  - P-4 GUI-readable unrealized-MTM snapshot → Positions live MTM/LTP/current-RR (now G-4 pending).

**C. Honest two-state gaps that are correct-by-design, not bugs:** Positions/Holdings/Orders-raw/
Execution-timings/Audit-user render explicit "Pending Broker Source / Not instrumented / Not
captured" — the dashboard has NO broker session and never fabricates. These stay UNAVAILABLE until
the P-1..P-4 sources ship.

---

## 4. Recommendation (Rama decides)

**Continue the screen-by-screen AlgoPilot pixel-redesign — 04 Signals next — via the proven
review-first workflow.** Rationale:
- 01/02/03 are approved and the workflow is proven; it deploys + gets reviewed incrementally.
- It is **frontend-only: no schema, no trading code, no route/endpoint deletion** → it cannot affect
  the trading system; worst-case rollback = revert the GUI unit to the deployed tag.
- The comprehensive backend (12 blueprints, readers, services) is already in place to feed the
  redesigned screens — the remaining work is largely visual conformance, not new plumbing.

**Hold** the schema-pause proposals (P-1..P-4) until Rama triggers — they are the ONLY items that
would touch the trading side, and each is an honest-UNAVAILABLE today, not a defect. **Keep XLSX
export OFF** unless Rama wants it on.

**Priority note:** the GUI is not on the deploy critical path and does not block the Q4/Q5/P11
hardening deploy or the D2 strategic decision. It is parallel, low-risk, Rama-paced work.

**One thing to confirm off-market** `[verify]`: that the deployed `gui-dashboard` checkout at
`~/systems/trading-system/ops_dashboard` is current with `main` (so the G5c/G5d screens users can
navigate to are actually serving, not 404/placeholder), and whether the P1 feeder's absence leaves
any Holdings/Trade path throwing rather than gracefully "pending".
