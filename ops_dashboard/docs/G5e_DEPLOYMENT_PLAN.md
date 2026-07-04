# G5e — Ops Dashboard Redesign · DEPLOYMENT + VALIDATION PLAN

**Status:** PLANNING ARTIFACT ONLY. No deploy / merge / push / restart / production
change was performed to author this. This is the runbook **Rama executes later**,
off-market, with a GO ping — exactly as written below.

**Authored (IST):** 04-Jul-2026 (Sat, market CLOSED) · **Base commit:** `80be23a`
(`origin/main == HEAD`) · **Rollback baseline:** `7b1c92d` (see §B).

**Deployment philosophy (binds every section):**
1. The existing deployed VM app (`gui-dashboard` on `80be23a`, G2 build) remains
   authoritative until promotion.
2. The G5 redesign stays isolated (working tree → deploy branch) until Rama approves
   cutover.
3. Merge only after validation passes.
4. Resume the PAUSED soak from the known-good baseline, now against the G5 build.
5. Human verification (Rama's browser pass) is required before production promotion.

---

## 0. Ground truth captured at authoring time (read-only)

| Fact | Value (verified 04-Jul) |
|---|---|
| `origin/main == HEAD` | `80be23a` — carries the **G2** ops_dashboard (currently deployed) |
| Pre-G5 GUI baseline | `7b1c92d` (merge "ops_dashboard G2a–G2c"); parents `a9d44e0` + `33e9223` |
| ops_dashboard drift `7b1c92d → 80be23a` | **only** `deployment/INSTALL.md` (+40 lines, E3 Tailscale doc) — running GUI code is **functionally identical** |
| G5 build location | **Uncommitted working tree** on `main`. **No `gui-g5*` branch exists yet** (verified `git branch -a`) |
| G5 working-tree scope | **44 files, all `ops_dashboard/**`** — 22 modified + 22 untracked (list in §A) |
| Non-G5 working-tree noise (MUST be excluded) | `PATHS.md`, `docs/SYSTEM_MAP.md` (both mixed w/ telegram/chartink edits), `docs/audit/c2_chartink_ip_investigation_03jul2026.md`, `docs/audit/telegram_token_shadow_investigation_03jul2026.md`, `gui/` (PC source `.txt` specs) |
| Claimed test state | **354 green** (v41+v42) — to be **RE-VERIFIED** on the deploy branch in a clean env (PC has 32 known env-only failures — run on the VM venv or a clean venv) |
| VM | `161.118.187.249`, user `ubuntu`, IST; tree `/home/ubuntu/systems/trading-system/` |
| GUI unit | `gui-dashboard.service` → `127.0.0.1:8500` (loopback), enabled + ACTIVE |
| GUI access | Tailscale serve (tailnet-only, NOT funnel): **https://trading-system.tail1cdc6d.ts.net** (`100.74.84.44:443` via `tailscaled`) |
| Trading units (untouched by GUI deploy) | `trading-system` · `token-watcher` · `alert-watcher` · `trading-watchman` · `security-watcher` · `cron-watchdog.timer` (6) |
| Prod overlay | `ops_dashboard/backend/config/gui_config.local.yaml` (git-ignored, `chmod 600`, deep-merged, **survives `checkout -f`**) |
| Export gates | `reports_download_enabled: false` + `table_export_enabled: false` — **Q3/XLSX stays OFF** at cutover |

---

## A. DEPLOYMENT CHECKLIST

### A.1 — MERGE-GATE (critical — run FIRST, before anything touches the VM)

The G5 chain (G5a→b→c→d, built on `80be23a`) currently lives as **uncommitted
working-tree changes**, not as branches. Bring it to a clean deploy branch off
`origin/main` and enforce a **HARD name-only diff gate**.

**A.1.1 — Create the deploy branch and stage ONLY `ops_dashboard/**`:**
```bash
cd D:/Projects/trading-system
git fetch origin
git checkout -b gui-g5-deploy origin/main        # base == 80be23a
git add ops_dashboard/                            # stages the 44 G5 files + this plan
# DO NOT add: PATHS.md, docs/SYSTEM_MAP.md, docs/audit/*, gui/
git commit -m "feat(gui-g5): ops_dashboard redesign G5a-G5d (additive, read-only)"
```

**A.1.2 — HARD name-only diff gate (the STOP condition):**
```bash
git diff --name-only origin/main...gui-g5-deploy | grep -vE '^ops_dashboard/' || echo "GATE CLEAN: ops_dashboard/** only"
```
- **Expected:** the `grep -v` prints **nothing** → `GATE CLEAN`. Every changed path
  begins with `ops_dashboard/`.
- **STOP + report** if the gate lists **any** path under `core/ orders/ capital/
  strategies/ broker/ signals/ main.py config/ core/schema.sql` (or anything outside
  `ops_dashboard/`). Do **not** merge. Re-scope the branch.

**Expected 44-file inventory** (name-only diff must equal exactly this set + this
plan doc):

*Modified (22):* `backend/api/analytics.py` · `backend/api/risk_capital.py` ·
`backend/api/trading.py` · `backend/app.py` · `backend/readers/db_reader.py` ·
`backend/services/freshness.py` · `backend/services/strategy_tower.py` ·
`backend/services/summary_bar.py` · `frontend/static/style.css` ·
`frontend/templates/{audit,base,config,controls,dashboard,holdings,login,logs,positions,slippage,strategies}.html`
· `tests/conftest.py` · `tests/test_api_contract.py`

*Untracked → new (22 + this plan):* `backend/api/analytics2.py` ·
`backend/api/operations.py` · `backend/services/analytics_period.py` ·
`backend/services/operations.py` · `docs/COMPONENTS.md` ·
`docs/G5_REDESIGN_PHASE_A.md` · `docs/G5_REDESIGN_PHASE_B.md` ·
`docs/G5e_DEPLOYMENT_PLAN.md` (this file) · `frontend/static/components.js` ·
`frontend/templates/{capital_risk,components,live_activity,pnl_analytics,scanner_attribution,strategy_health,strategy_ranking,trade_explorer,trade_logs}.html`
· `tests/test_g5a_components.py` · `tests/test_g5b_reuse.py` ·
`tests/test_g5c_analytics.py` · `tests/test_g5d_operations.py` · `tests/test_nav.py`

**A.1.3 — Ordering vs any pending production batch (restated):** as in the prior
deploy, **GUI merges LAST**. If any trading-engine batch is pending
(e.g. B-1 enforce, P1 authoritative — see the off-market deploy plan), that batch
merges and boots FIRST; the GUI deploy branch is merged only after. The GUI touches
zero trading paths, so its ordering constraint is purely "don't interleave with an
engine push window."

### A.2 — Pre-deploy VM state capture (read-only, before touching anything)
```bash
ssh trading-vm '
  echo "== mem ==";   free -h
  echo "== disk ==";  df -h /
  echo "== listeners =="; ss -tlnp | grep -E ":8500|:443" || true
  echo "== units ==";
  for u in trading-system token-watcher alert-watcher trading-watchman security-watcher gui-dashboard; do
    printf "%-22s %s\n" "$u" "$(systemctl is-active $u.service)"; done
  systemctl is-active cron-watchdog.timer
  echo "== deployed HEAD =="; cd /home/ubuntu/systems/trading-system && git rev-parse --short HEAD 2>/dev/null || echo "(working tree, not a git repo — expected)"
'
```
- Baseline expectation: MemAvailable healthy (VM = 11Gi/2 vCPU), root free ample
  (was 83G), all 6 trading units + `gui-dashboard` `active`, GUI listener on
  `127.0.0.1:8500`, `:443` served by tailscaled. Record the numbers as the
  before-picture for the soak S4 RSS/CPU comparison.
- **Deployed-tag note:** the VM working tree is a post-receive checkout, **not** a
  git repo — `git rev-parse` there will fail; the authoritative "what's deployed"
  is `origin/main` on the PC (`80be23a` pre-G5) and the soak baseline `7b1c92d`.

### A.3 — VM app-refresh steps (this is a REFRESH of an already-installed unit — NOT a fresh install)
This is **not** the first-install path (`deployment/INSTALL.md` §1/§5/§6 — venv,
Tailscale, systemd unit — are already done and are **not** re-run). Refresh only:

1. **Window check (mandatory):** off-market only, **never 15:30–17:05 IST**, never
   mid-session. Anchor with `python scripts/deploy_preflight.py` (PC↔VM UTC ≤120s +
   VM-authoritative market-open gate — refuses a mid-session deploy).
2. **Merge to main + push** (off-market): merge `gui-g5-deploy` `--no-ff` into `main`,
   then `git push origin main`. The bare-repo **post-receive checks out**
   `ops_dashboard/` into the working tree. (The hook starts nothing.)
3. **Confirm VM HEAD / tree:** the pushed commit is now `origin/main`; confirm the
   post-receive log shows a clean checkout (no `.local.yaml` clobber — it's
   git-ignored).
4. **Restart the GUI unit only** (trading untouched):
   ```bash
   ssh trading-vm 'sudo systemctl restart gui-dashboard && systemctl is-active gui-dashboard'
   ```
5. **Confirm `session_cookie_secure` stays `true`** (overlay lock — §A.4).
6. **Smoke the loopback:**
   ```bash
   ssh trading-vm 'curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" http://127.0.0.1:8500/'   # → 302 /login
   ```

### A.4 — Overlay safety (verify AFTER the checkout)
`gui_config.local.yaml` is git-ignored and deep-merged over the committed base, so a
`checkout -f` cannot clobber it. Confirm it survived and is still secure:
```bash
ssh trading-vm 'cd /home/ubuntu/systems/trading-system/ops_dashboard && \
  ls -l backend/config/gui_config.local.yaml && \
  stat -c "%a" backend/config/gui_config.local.yaml && \
  grep -cE "(session_cookie_secure: true|main_db:|password_hash:|totp_secret:)" backend/config/gui_config.local.yaml'
# expect: mode 600, and the grep count ≥ 4 (key NAMES only — never cat the file into a shared terminal).
```
- If the overlay is **absent** after the checkout → STOP. Re-transfer per
  `INSTALL.md §2b` before restarting the unit (a missing overlay drops to PC-dev
  paths + `secure=false` → wrong DBs + insecure cookie).

### A.5 — Tailscale serve persistence (verify, no re-auth)
```bash
ssh trading-vm 'tailscale serve status'   # expect https://trading-system.tail1cdc6d.ts.net → 127.0.0.1:8500
```
- `tailscale serve` config persists across app restarts and reboots (proven E4).
  No re-auth, no re-run of the serve command for a GUI refresh.

---

## B. ROLLBACK CHECKLIST

**Rollback baseline = `7b1c92d`** — the pre-G5 GUI merge commit, the soak-validated
G2 build. Its `ops_dashboard/` tree is **functionally identical** to the currently
live tip `80be23a` (only `deployment/INSTALL.md` doc differs). So the safe point is
"origin/main **as it stood before** the G5 merge" = `80be23a`, whose GUI == `7b1c92d`.

**B.1 — Pre-merge prep (do this in cutover-prep, §E — makes rollback a named target):**
```bash
git tag -a gui-pre-g5-rollback 80be23a -m "GUI G2 build, soak-validated (== 7b1c92d ops_dashboard); pre-G5 rollback point"
git push origin gui-pre-g5-rollback
```

**B.2 — Single-action rollback (if any trigger in B.4 fires):**
```bash
# On PC (off-market): revert the G5 merge on main, push, checkout, restart GUI.
git revert -m 1 <g5-merge-sha>            # restores ops_dashboard/ to the gui-pre-g5-rollback tree
git push origin main                      # post-receive checks out the reverted tree
ssh trading-vm 'sudo systemctl restart gui-dashboard && \
  curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8500/'   # → 302, old G2 dashboard serves
```
- Verify the browser now shows the **G2** dashboard (pre-redesign nav) over the
  Tailscale URL.

**B.3 — GUI-only blast radius (explicit):** rollback touches **ONLY** the
`gui-dashboard` service and the `ops_dashboard/` tree. The trading stack, both DB
files, all 6 trading systemd units, cron, and the overlay are **untouched**. There
is no data migration to unwind (G5 added **zero schema**; DBs are opened read-only).

**B.4 — Decision triggers (ANY → roll back):**
- Smoke fail (§D) — a menu/endpoint 500s or auth breaks.
- Soak zero-impact breach — any `SQLITE_BUSY` / read-error attributable to the GUI
  (S3 > 0).
- DB busy errors that correlate with GUI polling.
- Auth broken over Tailscale (login/TOTP fails, or anon reaches a page).
- Any measurable trading-latency degradation with GUI-on vs GUI-off.

---

## C. SOAK-TEST PLAN (resume the PAUSED soak on the G5 build)

The soak was paused pre-G5. It now runs against the **G5 build** once deployed,
using the locked contract. Evidence goes into an extension of
`SOAK_EVIDENCE_TEMPLATE.md` (the day-block dates in that template are placeholders —
use the actual first three market days after deploy). **PASS bar = ≥3 clean market
days. No patches during soak** — any FAIL → stop, root-cause, report.

### C.1 — LOCKED soak contract (S1–S8, restated)
- **S1** spot-check dashboard values vs DB truth (2/day min).
- **S2** page-latency medians (dashboard / strategies / pnl / logs) vs prior week.
- **S3** `SQLITE_BUSY` / reader-error count — **must be 0** (journal + app log, EOD).
- **S4** `gui-dashboard` RSS + CPU **3×/day** (~09:40 / 12:30 / 15:10).
- **S5** freshness **3×/day**: dashboard Last Signal / Last Order / Last Trade vs
  `SELECT max(...)` — must match AND keep advancing.
- **S6** trade traces daily: (a) **one** successful full lifecycle
  signal→strategy→order→fill→exit **and** (b) **one** rejected/failed trace shown at
  the right pipeline stage + in the strategy failure strip — or the explicit
  "none occurred" line.
- **S7** capacity accuracy **2×/day**: Configured/Used/Remaining for Orders ·
  Positions · Capital · Risk vs DB/ledger (**ONE drift = FAIL**).
- **S8** strategy-badge truth daily: one silent + one active strategy cross-checked
  against the `signals` table.

### C.2 — Redesign-specific additions (validate the NEW screens against live data)
- **Trade Explorer** (`/trades`) + **Trade Logs** (`/trade-logs`) show real trades
  with correct strategy + scanner + time; a row's drill reconciles to the DB.
- **P&L Analytics** (`/pnl-analytics`) period numbers (today / trailing-7d /
  trailing-30d / custom) reconcile to DB aggregates.
- **Live Activity** (`/live-activity`) merged 7-source feed **advances** during
  market hours.
- **Two-state Positions/Holdings**: the SYSTEM side shows numbers; the BROKER side
  (LTP / MTM / RR / Broker-Qty / Delta / Recon) stays an honest **"Pending Broker
  Source (G4/P1)"** panel — **never a fabricated number**.
- **Controls** (`/controls`) read-only summary fields match `config` truth; **no**
  form, **no** button, zero write path.
- **Scanner Attribution / Strategy Ranking / Strategy Health** period views
  reconcile to the funnel + `trades` truth.
- Pinned invariants intact live: **6 units · 13 pipeline stages · 8 rows · 6 groups ·
  4 ranking views · HARD_KILL blink** (blink is HARD_KILL only).

### C.3 — The LOCKED verdict question
> "Did Rama ever need Telegram / logs / DB queries / Excel to understand something
> the redesigned dashboard could **not** show?"

- **YES** → record each instance: (a) exactly what was missing, (b) the screen that
  SHOULD have shown it, (c) proposed enhancement → **F-backlog** (F10+ numbering).
- **NO** → state explicitly: *"The redesigned dashboard became the primary
  operational interface."*

---

## D. SMOKE-TEST PLAN (immediately post-deploy, BEFORE soak)

Run over the Tailscale URL (real access path) unless noted. All must pass or → §B.

1. **Auth:** login + TOTP works over `https://trading-system.tail1cdc6d.ts.net`;
   anonymous request to any page → **302 → /login**; anonymous `/api/*` → **401**.
2. **All 21 menu screens render 200 authed** (5 groups):
   - Dashboard: `/`
   - **Trading (5):** `/strategies` `/signals` `/orders` `/positions` `/holdings`
   - **Analytics (7):** `/strategy-ranking` `/strategy-health` `/scanner-attribution`
     `/trades` `/pnl-analytics` `/slippage` `/execution`
   - **Operations (5):** `/live-activity` `/services` (System Health) `/capital-risk`
     `/config` `/controls`
   - **Investigation (3):** `/audit` `/trade-logs` `/logs`
3. **Off-menu routes still 200 by direct URL** (dropped from sidebar, routes
   preserved): `/risk` `/capital` `/exposure` `/capacity` `/pnl` `/vm` `/statistics`
   `/reports` `/alerts`.
4. **Non-menu API endpoints still 200 authed:** `/api/risk` `/api/capital`
   `/api/exposure` `/api/capacity` `/api/pnl` `/api/vm` `/api/statistics`
   `/api/reports` `/api/alerts`.
5. **Contract spot-checks:**
   - Pipeline shows **13 stages** (`/api/pipeline`).
   - **ExportButton disabled** everywhere (`table_export_enabled: false`);
     `/api/reports/download` gated OFF (`reports_download_enabled: false`).
   - **ScoreChip = single score** (screener_results.score).
   - **Two-state panels honest** — Positions/Holdings broker side = UNAVAILABLE
     panel, never a number.
   - **Controls has no form / no button** (read-only summary only).
6. **Trader-down banner** correct: if the trading process is **not** running at
   deploy time (off-market → expected), the banner shows the trader-down state
   honestly.

---

## E. CUTOVER READINESS CHECKLIST

- [ ] All G5a–G5d acceptance criteria green (they are — additive-only, pinned counts
      + HARD_KILL blink intact, two-state honesty verified, 5-group nav complete).
- [ ] **354 tests green re-run on the deploy branch** in a clean env (VM venv or a
      fresh venv — **not** the PC, which has 32 known env-only failures).
- [ ] **Merge-gate name-only diff CLEAN** (`ops_dashboard/**` only — §A.1.2).
- [ ] **Rama's interactive browser pass** on the deploy branch (PC `127.0.0.1:8500`
      or the Tailscale URL) **signed off** — the outstanding NOT-RUN across all G5
      sub-phases. *(This is the gate before production promotion — see §I sequence.)*
- [ ] **Rollback tag confirmed** (`gui-pre-g5-rollback` → `80be23a`, GUI == `7b1c92d`)
      + rollback steps understood (§B).
- [ ] **Q3/XLSX stays OFF** — no export-gate change at cutover.
- [ ] Deploy window **off-market** (never 15:30–17:05 IST; `deploy_preflight.py`
      market-open gate passes).

---

## F. RISK ASSESSMENT

| # | Risk | Rating | Mitigation |
|---|---|---|---|
| F1 | Merge entanglement with a pending engine batch, or with non-G5 working-tree edits (PATHS.md/SYSTEM_MAP.md/docs/audit/gui) | **Med** | Merge-gate name-only diff (`ops_dashboard/**` only, §A.1.2) + GUI-merges-last ordering. The gate is the STOP. |
| F2 | GUI restart during market hours | **Med** | Off-market deploy only; `deploy_preflight.py` VM-authoritative market-open gate refuses a mid-session push. |
| F3 | New-screen load on the VM during market (extra polling) | **Low** | Poll cadence unchanged from G2; soak S4 RSS/CPU watch 3×/day; DBs read-only (`mode=ro`); S3 busy-error count must stay 0. |
| F4 | Carried-not-redesigned screens (Signals / Orders / Execution / System-Health / VM) look cosmetically inconsistent with the redesign | **Low** | Cosmetic only — no functional risk; flag for a later G-pass. Not a deploy blocker. |
| F5 | The six honest G-gaps (G-1 MTM, trade-story timings, author/user, etc.) show UNAVAILABLE | **None** | By design (two-state honesty). Not a deploy risk; NOT to be "fixed" by faking data. |
| F6 | Overlay lost on checkout → wrong DBs / insecure cookie | **Low** | git-ignored + deep-merged (survives `checkout -f`); §A.4 verifies presence + `600` + `secure:true` before declaring smoke pass. |
| F7 | Rollback needed under time pressure | **Low** | Single revert + push + GUI-only restart (§B); zero schema to unwind; blast radius = one service. |

---

## G. GO / NO-GO CRITERIA (binary)

**GO only if ALL of:**
1. 354 tests green on the deploy branch (clean env).
2. Merge-gate name-only diff CLEAN (`ops_dashboard/**` only).
3. Smoke pass (§D — all 21 menu screens + off-menu routes + contract checks + auth).
4. **Rama's browser sign-off** obtained.
5. Rollback tag confirmed (`gui-pre-g5-rollback`).
6. Deploy window off-market.

**NO-GO if ANY of the above fails → do NOT merge.** Fix the failing gate, re-run,
re-evaluate. No partial promotion.

---

## H. PRODUCTION VALIDATION MATRIX (during-soak evidence template)

Extends `SOAK_EVIDENCE_TEMPLATE.md`. For each screen/endpoint: what to verify live,
the source-of-truth to reconcile against, and the pass criterion.

| Screen / endpoint | Verify live | Source of truth | Pass criterion |
|---|---|---|---|
| Dashboard `/` `/api/dashboard` | headline KPIs, profit_factor, sparkline | `trades` (today) + DB counters | headline == DB aggregate |
| Strategy Ranking `/strategy-ranking` | 4 ranking views, medals | `screener_results` + `trades` composite | ranking order == recompute; 4 views pinned |
| Strategy Health `/strategy-health` `/api/strategy-health` | health_state per strategy | `signals`/`trades` per-strategy | Disabled→Silent→Warning→Quiet→Healthy matches state |
| Scanner Attribution `/scanner-attribution` `/api/scanner-attribution` | scanner→trade join, funnel | `webhook`+`signals`+`trades` | attribution counts reconcile to funnel |
| Trade Explorer `/trades` `/api/trades` + `/api/trade-story/<id>` | rows: strategy+scanner+time; drill story | `trades` + join | every field == DB row; story parts consistent |
| P&L Analytics `/pnl-analytics` `/api/analytics/pnl` | today / 7d / 30d / custom totals | `trades.net_pnl` range aggregates | period totals == DB SUM per range |
| Slippage `/slippage` `/api/slippage` | per-scanner/symbol/band/trend | `trade_slippage_log` | totals == independent recompute |
| Execution `/execution` `/api/execution` | (carried) execution metrics | `orders`/`trades` | 200 + values match DB |
| Live Activity `/live-activity` `/api/activity` | 7-source merged feed advances | 7 source tables | feed advances intra-day; entries traceable |
| System Health `/services` `/api/services` + `/vm` `/api/vm` | 6 units status; VM metrics | `systemctl` / metrics | **6 units** pinned; status truthful |
| Capital & Risk `/capital-risk` (`/api/risk`+`capital`+`exposure`+`capacity`) | 4 zones; MTM two-state | ledger + `fm_ledger` + config | numbers == ledger; MTM = UNAVAILABLE panel |
| Configuration `/config` `/api/config` | resolved config drift view | `config_snapshots` / YAML | values == day's resolved config |
| Controls `/controls` `/api/controls-summary` | read-only summary fields | config | fields match config; **zero write path** |
| Positions `/positions` `/api/positions` | SYSTEM side full; BROKER side honest | `trades`/`orders` (system); broker=UNAVAILABLE | broker fields never a number |
| Holdings `/holdings` `/api/holdings` | SYSTEM side + trade-context join | `holdings`+`trades` join; broker=UNAVAILABLE | broker side UNAVAILABLE; join correct |
| Trade Logs `/trade-logs` `/api/trade-logs` | trades+scanner+score+recon actions | `trades`+`reconciliation_log` | rows reconcile; raw drill via `/api/logs?id=` |
| Signals `/signals` · Orders `/orders` | (carried) render + values | `signals`/`orders` | 200 + values match DB |
| Pipeline `/api/pipeline` | **13 stages** | pipeline funnel | 13 stages pinned |
| Reports `/reports` `/api/reports` | list; download **gated** | `reports/output` | list 200; `/api/reports/download` OFF |

---

## I. REPORT-BACK FORMAT (for when Rama executes G5e — define here, use then)

G5e execution reports evidence-only, in this order:

1. **Merge-gate diff list** — the name-only diff output (must be `ops_dashboard/**`
   only) + explicit "GATE CLEAN" / "STOP (path X)".
2. **Push / deploy commits** — the deploy-branch SHA, the merge SHA, the pushed
   `origin/main` SHA; confirmation post-receive checked out cleanly.
3. **Smoke results** — §D checklist, pass/fail per item (21 menu + off-menu +
   contract checks + auth + trader-down banner).
4. **Daily soak tables** — S1–S8 day-blocks + the §C.2 new-screen validation rows,
   one block per market day (≥3).
5. **Go/No-Go verdict** — §G, binary, with the six gates enumerated.
6. **Rollback readiness** — rollback tag confirmed; whether rollback was exercised.
7. **Memory updates** — the memory / SYSTEM_MAP / PATHS lines written.
8. **Deviations / NOT-RUN** — anything skipped or that could not run, with why
   (never fabricate a soak entry — "NOT RUN (why)").

**Cutover sequence (where the human gate sits):**
merge-gate CLEAN → tests green (clean env) → deploy branch built → **Rama browser
sign-off (HUMAN GATE)** → off-market merge+push → GUI restart → smoke → **resume
soak (≥3 clean days)** → final verdict → promote / rollback. Production promotion
does **not** proceed past the browser-sign-off gate without Rama's explicit GO.

---

*Planning artifact — no execution performed. Rama executes off-market with a GO ping,
exactly as written.*
