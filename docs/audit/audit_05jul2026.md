# FULL SYSTEM AUDIT — Trading System (Repository + Production VM)

**Date:** 05-Jul-2026 (Sunday, market closed) · **Auditor:** Claude (VS Code), 14 read-only audit agents in 7 batches of 2 + orchestrator verification
**Repo:** `D:\Projects\trading-system` @ `b4aea6f` (main) · **VM:** 161.118.187.249 (`trading-system`, ubuntu, IST)
**Mandate:** Complete operational audit — external CTO + Principal Engineer + SRE + Auditor lens. Not a bug hunt; not security-only. Every phase reports: what exists · what works well · risks · gaps · technical debt · improvements · severity · recommendation · effort.
**Method:** Evidence over assumption — every material claim cites `file:line` or a command + observed output; uncertainty is stated explicitly. All inspection strictly read-only (repo untouched, VM non-mutating commands only, SQLite opened `mode=ro`).
**Severity legend:** CRITICAL (money/position at risk) · HIGH (operational failure likely) · MED (degradation/debt) · LOW (polish).
**Effort legend:** S (<½ day) · M (1–3 days) · L (>3 days).

---

## 0. BASELINE SNAPSHOT (collected 05-Jul-2026 ~01:25 IST)

### 0.1 Repository
| Metric | Value |
|---|---|
| HEAD | `b4aea6f` — "merge G5: ops_dashboard redesign G5a-G5d" |
| Total commits | 557 |
| Tracked files | 818 |
| Python files / LOC | 495 / 184,714 |
| Test LOC share | 99,979 (54.1%) in `tests/` + 7,570 `ops_dashboard/` (incl. its tests) |
| Test functions | 4,143 (`tests/`) + 179 (`ops_dashboard/tests/`) = **4,322** |
| TODO/FIXME/HACK/XXX markers | **2** (in 184.7k LOC) |
| LOC by package (top) | orders 16,903 · core 9,174 · ops_dashboard 7,570 · capital 5,808 · broker 5,348 · reports 4,430 · signals 3,034 · main.py 3,004 |
| Largest production files | `orders/order_placer.py` 4,473 · `orders/order_reconciler.py` 3,673 · `main.py` 3,004 · `core/state_store.py` 2,845 · `reports/daily_trade_review.py` 2,432 · `broker/zerodha_adapter.py` 2,279 · `capital/fund_manager.py` 2,223 |

### 0.2 Production VM
| Metric | Value |
|---|---|
| Uptime / load | 1d 3h · load 0.00 (idle weekend) |
| Mem / disk | 611Mi used of 11Gi · 15G/96G disk (16%) |
| Units | trading-system **inactive** (expected, market closed) · token-watcher active · gui-dashboard active · cron-watchdog.timer active · **alert-watcher `activating`** · **security-watcher `activating`** · trading-watchman inactive |
| Crontab | 45 command lines |
| Main DB | `trading_system.db` **257 MB**, 42 tables, WAL mode |
| Analytics DB | `analytics.db` 7.1 MB (candles 36,327 · system_metrics 919 · _daily 11) |
| Key row counts | signals **95,431** · screener_results **82,574** · trades 228 · orders 340 · fm_ledger 1,242 · reconciliation_log 7,764 · trade_slippage_log 59 · config_snapshots 2 · gtt_state 0 |
| Dir sizes | logs **834 MB** · backups **3.4 GB** · reports 28 MB |

> ⚠️ Orchestrator flag at baseline: `alert-watcher` and `security-watcher` showed `activating` (not `active`) in two independent snapshots hours apart → investigated in Phase 9.

---

## PHASE 1 — REPOSITORY & ARCHITECTURE

**Baseline:** HEAD `b4aea6f` (merge G5, 2026-07-05 00:54 IST), branch `main`, 557 commits. 818 git-tracked files: 495 `.py` (184,714 LOC), 86 `.md`, 60 `.yaml`. Working tree at audit time: 2 modified (PATHS.md, docs/SYSTEM_MAP.md) + 5 untracked (3 docs, `gui/`, 1 signoff md).

### 1.1 What exists (inventory)

**Top-level layout** (all paths under `D:\Projects\trading-system`):

| Package | .py | LOC | Role |
|---|---|---|---|
| tests/ | 255 | 99,979 | unit/ + integration/ + crash_test/ + core/ + fixtures/ |
| scripts/ | 73 | 19,445 | cron jobs + manual tools + preflight/ subpackage + services |
| orders/ | 19 | 16,903 | order lifecycle (largest prod package) |
| core/ | 19 | 9,174 | state_store, config_loader, time_authority, events, schema.sql, db_connect |
| ops_dashboard/ | 48 | 7,570 | isolated Flask GUI: backend/{api×10, services×10, readers, config} + frontend + own tests |
| capital/ | 10 | 5,808 | fund_manager, kill_switch |
| broker/ | 13 | 5,348 | zerodha_adapter, order_monitor, slippage_engine |
| reports/ | 4 | 4,430 | daily_report.py (1,880) + daily_trade_review.py (2,432) |
| signals/ | 4 | 3,034 | webhook_receiver, signal_processor, entry_throttle |
| root | 2 | 3,025 | main.py (3,004) + run_tests.py |
| utils/ | 5 | 2,087 | cron_heartbeat, holiday_guard, instance_lock, startup_checks |
| screening/ | 6 | 1,963 | sr_detector/ 12/1,674 · ops/ 13/1,301 (control_tower) · data/ 3/1,122 · alerts/ 4/1,039 · strategies/ 4/592 · deploy/ 1/220 |

- **Config-as-data:** `config/` = 11 snake_case YAMLs + `config/strategies/` = 15 per-strategy YAMLs (`gap_go_long.yaml`…) + `reference_data/`. The `strategies/` code package is only 592 LOC — strategy definitions live in config, not code.
- **Deploy tree complete:** `deploy/systemd/` 7 units (trading-system, token-watcher, alert-watcher, security-watcher, trading-watchman, cron-watchdog+timer), `deploy/hooks/` (post-receive, pre-receive, pre-commit, secret_scan.py), `deploy/cron/` (generated from `config/cron_registry.yaml`, 44 jobs, via `scripts/generate_crontab.py`), security/, logrotate/.
- **Docs:** 67 md under docs/ (28 root-level, design=5, audit=11, ops=1, gui_project); `docs/SYSTEM_MAP.md` 1,230 lines/269KB/15 `##` sections; `PATHS.md` 75KB; `docs/locked_decisions.yaml` 167KB; `v2_design_spec.md` 52KB; `report_data_contract.md` 42KB; DEPLOYMENT.md 14KB. **No README.md exists** (verified `ls README*` empty). AGENTS.md/GEMINI.md are AI-agent charters, not human onboarding.
- **AI/PC-only artifacts correctly ignored:** `memory/`, `mempalace.yaml`, `sats/`, `.claude/settings.local.json` all in `.gitignore`; `.env`, `*.db`, `logs/`, `credentials.xlsx` ignored and verified untracked (`git ls-files '*.db' '.env' '*.log'` → empty). `.gitignore` carries incident-annotated rules (e.g. `config/instruments.csv` block cites the 21-Jun staleness incident).

### 1.2 What works well

1. **Layering is real at module level.** Package import matrix (495 files scanned): `core` is imported 700+ times and imports upward only via 3 function-scoped lazy imports; `sr_detector` is self-contained (20 internal vs 1 core import); `data`, `alerts`, `strategies` are near-leaves. Zero import-time circular imports found.
2. **ops_dashboard isolation verified, not just claimed.** Zero `import core|broker|orders|capital|…` statements anywhere under `ops_dashboard/backend/` or its tests (grep across tree → empty); own `.venv`, own `backend/requirements.txt` with explicit "I4: kiteconnect MUST NOT appear here" comment; talks to the system only via read-only DB (`backend/readers/db_reader.py`).
3. **Test corpus is exceptional for a solo system:** 99,979 LOC of tests vs ~77k production trading LOC (≈1.2:1), mirrored naming (`tests/unit/test_order_placer.py` 4,954 LOC ↔ `orders/order_placer.py`), organized unit/integration/crash_test tiers.
4. **Dependency hygiene near-perfect:** root `requirements.txt` = 10 runtime deps, every one exact-pinned with a decision-ID comment (`kiteconnect==5.1.0 # Audit #11: pin; DO NOT upgrade without full regression test`); third-party surface tiny (kiteconnect, flask/waitress, openpyxl, pyyaml, pydantic, requests, pyotp, cachetools, dotenv); stdlib-dominant everywhere else.
5. **Marker debt is effectively zero:** 2 TODO/FIXME/HACK/XXX hits in tracked code (1 genuine: `broker/slippage_engine.py:74` FIX-109; 1 false positive `--symbol=XXX` in check_vm_state.py:57). Commented-out code: 1 hit repo-wide (`git grep -c '^\s*# (def |class )'`). Naming 100% consistent snake_case (only exceptions: framework-mandated `formatTime` at core/logger.py:203, unittest setUp/tearDown).
6. **Decision provenance is a standout:** pervasive spec-ID comments (MAIN1-18, SU7-13, CL5, FIX-062, WR2…) cross-referenced to `docs/locked_decisions.yaml` (167KB) — changes are traceable to decisions years later. The `live_test_mode` removal even left a tripwire: `core/config_auditor.py:273` flags `B3_live_test_mode_resurrected` if the key ever returns.
7. **Shared-helper culture where it matters:** `alerts.telegram_notifier` (37 importers), `utils.cron_heartbeat` (25 of ~30 cron scripts), `scripts/preflight/base` framework (30 imports), `core.time_authority` (164 imports — single clock authority).
8. **Cron is registry-driven:** `config/cron_registry.yaml` (44 jobs) → `generate_crontab.py` → `deploy/cron/trading-system.cron`, with drift checking (`check_cron_drift.py`) — no hand-edited crontab as source of truth.

### 1.3 Risks

- **[HIGH] `core/state_store.py` is a universal dependency and god-object:** 2,845 LOC, 125 defs, 4 classes, imported by 126 files. Failure scenario: any schema/API change here has the widest possible blast radius in a live-money system; a subtle behavioral change ripples into orders, capital, reports, and scripts simultaneously, and its 2,222-LOC test file cannot cover all 126 consumer contexts.
- **[MED] Layering erosion at 5 concrete points:** (a) module-level inverted import `broker/zerodha_adapter.py:121` `from orders.price_math import DEFAULT_TICK` (orders→broker is the sanctioned direction, 10 imports; file-level acyclic only because price_math is a stdlib-only leaf); (b,c,d) `core/config_auditor.py:222` (→strategies), `:395` (→orders), `core/cron_registry.py:182` (→utils) — lazy, but core now semantically knows upper layers; (e) `orders/structure_exit_manager.py:70` imports **private** `_strong_close` from `sr_detector.retest_confirm`. Failure scenario: each tolerated edge lowers the bar for the next; a future refactor of price_math/retest_confirm internals silently breaks broker/orders at import time.
- **[MED] 19 raw `sqlite3.connect` call sites outside the sanctioned `core/db_connect.py`** (preflight checks ×7, gemini_premarket_brief ×4, backup_restore_drill ×6 [snapshot files — arguably legitimate], fetch_daily_candles, check_vm_state). Post-v28 two-DB ATTACH split, any of these that grows an analytics-table query fails at runtime on the VM only. Structural gap: sanctioned accessor exists but nothing enforces it (no lint/CI rule).
- **[MED] Two full report generators run in parallel in production:** `reports/daily_report.py` (1,880 LOC, still cron-registered at cron_registry.yaml:408-423) + `reports/daily_trade_review.py` (2,432 LOC). Registry comment says "eventually daily_report" retires but no sunset date. Failure scenario: a schema change gets applied to one, silently diverging numbers between two "official" EOD reports.
- **[MED] main.py drifting from orchestrator toward god-file:** 3,004 LOC, 39 top-level functions, 0 classes; contains embedded business logic (EOD pre-alert at 14:45 FIX-130 §L558+, FIX-189 service-window guard L785/L1376, token invalidation FIX-062 L116) alongside wiring; imports from 12 packages including `scripts/` (L85, L99, L1274, L2850 — scripts doubling as a library). Failure scenario: startup/shutdown changes and trading-behavior changes collide in the same file under deploy pressure.
- **[MED] SYSTEM_MAP.md format won't scale:** 269KB/1,230 lines, avg 224 chars/line — header is a single ~2,000-char deploy-narrative paragraph. It is the mandated pre-work reading ("READ before any work — NON-NEGOTIABLE"). Failure scenario: during an incident, a load-bearing fact (e.g. "deploy ≠ restart") is buried mid-paragraph and missed; grep is already the only viable access mode.
- **[LOW] PATHS.md (75KB) has outgrown "quick reference"** — same accretion pattern, header still says "Last updated: 2026-07-03" while carrying uncommitted 04/05-Jul edits (update discipline is post-hoc to work, not atomic with it).
- **[LOW] `gui/` (PC-only spec .txts) is untracked AND unignored** — permanent `??` git-status noise trains the operator to ignore status output; a genuinely forgotten file hides in the noise.
- **[LOW] pytest version skew:** root dev `pytest>=9.0.3` (floating) vs ops_dashboard `pytest==8.3.4` — two suites, two framework versions, one repo.

### 1.4 Gaps

1. **No README.md / no human onboarding document.** Entry path for a new engineer is PATHS.md (75KB) → SYSTEM_MAP.md (269KB) — both operational changelogs, neither an orientation. AGENTS.md/GEMINI.md onboard AI agents better than humans are onboarded.
2. **No import-boundary enforcement** (no import-linter/CI contract), so the 5 erosion points in 1.3 were caught only by this audit.
3. **No architecture overview doc** — docs/design has 5 files (feature-level); nothing shows the package DAG or the intended layering that this audit had to reverse-engineer.
4. **Docstring coverage uneven:** core/state_store 72%, time_authority 63%, order_placer 44%, fund_manager 44%, config_loader 7% (mitigated: only 3 public defs, rest pydantic validators). Public-API docstrings on the two 44% files lag their criticality.
5. **`ops/` package lacks `__init__.py`** (works as namespace pkg; inconsistent with all other packages — `ops/control_tower/` has one).

### 1.5 Technical debt

1. **Overdue deletions (explicitly promised, not executed):** `scripts/premarket_healthcheck.py` (252 LOC) header: "DEPRECATED 2026-06-21 … delete after the Monday 22-Jun + Tuesday 23-Jun live proof" — 12 days overdue. `scripts/reconcile_pnl.py` (397 LOC) superseded by P1 EOD broker-reconcile (v42, unpushed) with stale references remaining at `core/schema.sql:777` and `core/state_store.py:2772`.
2. **Orphan scripts** (zero code/doc/test references): `scripts/check_vm_state.py`, `scripts/find_symbol_aliases.py`; test-only: `load_test_signals.py`; one-off real-money test kept: `t2_cnc_gtt_realtest.py`; `agy_runner.py` has no cron/systemd/code invocation (UNCERTAIN: may be invoked manually from the AGY workflow).
3. **gemini_* duplicate family:** 6 scripts, 1,877 LOC, no shared client module (each embeds its own API-call + DB-read boilerplate; `git grep 'def call_gemini'` → empty). The 4× `sqlite3.connect` inside gemini_premarket_brief alone shows the copy-paste.
4. **Stale docstring cross-references:** `reports/daily_trade_review.py:16` still cites deleted `reports/daily_review.py`.
5. **scripts/ is a 73-file grab-bag** mixing cron entrypoints, long-running services (healthcheck_server), manual admin tools (clear_kill_switch, approve_ssh_keys), one-offs, and library code imported by main.py — no internal taxonomy.
6. **Root clutter:** GEMINI.md (13KB), AGENTS.md, mempalace.yaml (ignored but present), `tasks/` containing a single lessons.md.

### 1.6 Improvement opportunities

1. Extract `DEFAULT_TICK` (and any shared market constants) to `core/constants.py` — kills the only module-level inverted import (broker→orders) for ~10 lines of change.
2. Promote `_strong_close` to a public function in sr_detector or inject it — removes the cross-package private import.
3. Add `import-linter` (or a 20-line AST test in tests/core/) encoding the layer contract: core imports nothing internal; broker imports only core; scripts import anything; nothing imports scripts except main via a sanctioned whitelist.
4. Introduce `scripts/gemini_common.py` (client + DB read helpers) — ~300 LOC reduction and single point for API/model changes.
5. Split SYSTEM_MAP.md: keep a ≤300-line current-state map; move deploy narratives to dated `docs/ops/deploys/` entries (the audit/ dir already shows the dated-file pattern works).
6. Route preflight/gemini scripts' DB access through `core.db_connect.connect` (or a read-only wrapper) to make the v28 ATTACH split un-forgettable.
7. A 60-line README.md: what this is, money-safety warning, layer diagram, "read PATHS.md → SYSTEM_MAP.md", how to run tests.

### 1.7 Phase verdict

The repository is in the top tier of what a solo-operated live trading system can look like: genuine layering, verified GUI isolation, a 100k-LOC test corpus, exact-pinned minimal dependencies, near-zero TODO/commented-code debt, and an unusually strong decision-provenance system (spec-IDs ↔ locked_decisions.yaml ↔ FIX register). The debt that exists is concentrated and legible: two god-modules (state_store, main.py), five identified layering erosion points with no automated boundary enforcement, a parallel-run report system awaiting sunset, and operational mega-docs (PATHS/SYSTEM_MAP) whose changelog-accretion format is approaching its scalability limit. Nothing found here threatens live operation today; the risks are change-amplification risks that compound with every future modification.

### 1.8 Recommendations (prioritized)

1. **[S]** Delete `scripts/premarket_healthcheck.py` + `scripts/reconcile_pnl.py` (after P1 lands) + orphans (`check_vm_state.py`, `find_symbol_aliases.py`, `t2_cnc_gtt_realtest.py`); fix stale refs at core/schema.sql:777, core/state_store.py:2772, daily_trade_review.py:16.
2. **[S]** Add `gui/` to `.gitignore` (or track it); restores a clean `git status` baseline.
3. **[S]** Move `DEFAULT_TICK` to core; re-point broker/zerodha_adapter.py:121 and orders/price_math.py.
4. **[M]** Add an import-boundary test (import-linter or AST-based) to the suite; encode the current intended DAG, whitelisting the 3 lazy core exceptions until refactored.
5. **[M]** Set and record a sunset date for `reports/daily_report.py` in cron_registry.yaml:423 (the parallel-run has been live since 01-Jul; two EOD truths is a standing divergence risk).
6. **[M]** Write README.md + a 1-page architecture map (package DAG, layer rules, entry points); link from PATHS.md header.
7. **[M]** `scripts/gemini_common.py` extraction (6 scripts → shared client).
8. **[L]** Split SYSTEM_MAP.md into current-state map + dated deploy log entries; enforce ≤300-line map.
9. **[L]** Plan state_store decomposition (repository-per-domain facade over the same DB layer) — not urgent, but every month adds importers (126 today).

### 1.9 Scorecard input

- **Architecture: 7/10** — real, verified layering with isolated GUI and self-contained subsystems, held back by two god-modules, five unenforced boundary erosions, and scripts/-as-library coupling into main.py.
- **Maintainability: 7.5/10** — exceptional test ratio, pinning, provenance, and zero marker-debt; dragged by 269KB/75KB accreting ops docs, missing onboarding path, and overdue-deletion follow-through.

### 1.10 Evidence log

- `git rev-list --count HEAD` → 557; `git log -1` → b4aea6f 2026-07-05 00:54 +0530; `git ls-files` → 818 files (495 .py / 86 .md / 60 .yaml)
- LOC/imports/cycles: python heredoc over all tracked .py (regex `^import|^from`, package matrix, DFS cycle scan) — output in §1.1 table, matrix rows quoted in §1.2/1.3
- Wrong-direction imports: grep hits at broker/zerodha_adapter.py:121, core/config_auditor.py:222+395, core/cron_registry.py:182, orders/structure_exit_manager.py:70; leaf-verification `grep '^(from|import)' orders/price_math.py` → stdlib only
- TODO scan: `git grep -nE '\b(TODO|FIXME|HACK|XXX)\b' -- '*.py'` → 2 (filesystem 331-hit anomaly traced to git-ignored `ops_dashboard/.venv`, 157 files)
- Dead code: headers of scripts/premarket_healthcheck.py (DEPRECATED 2026-06-21), reports/daily_report.py (not deprecated); config/cron_registry.yaml:408-423 (daily_report still registered, 44 jobs); per-script reference cross-scan (code/docs/tests) for 25 non-cron scripts
- Isolation: `grep -rE '^(from|import) (core|broker|…)' ops_dashboard/backend ops_dashboard/tests` → empty; ops_dashboard/backend/requirements.txt (5 exact pins, I4 comment)
- Requirements: requirements.txt (10 exact pins + decision comments), requirements-dev.txt (pytest>=9.0.3), verified `git ls-files '*.db' '.env' '*.log'` → empty
- Docs: `wc -lc docs/SYSTEM_MAP.md` → 1,230/275,631; PATHS.md head (Last updated 2026-07-03 vs uncommitted tree edits); docs counts (67 md; design 5, audit 11); `ls README*` → none
- Docstrings: AST-regex density on core/state_store.py (72%), config_loader.py (7%, 3 public), time_authority.py (63%), orders/order_placer.py (44%), capital/fund_manager.py (44%)
- sqlite discipline: `git grep 'sqlite3\.connect'` excluding core/db_connect.py → 19 sites (listed §1.3); core/db_connect.py existence confirmed via `ls core/`
- main.py: 3,004 lines, `grep -c '^class '` → 0, `^def` → 39; section titles at L116-2993 (MAIN1-MAIN18, FIX-130/FIX-189 logic); scripts-imports at L85/L99/L1274/L2850
- Naming: camelCase def scan → only core/logger.py:203 (logging override) + unittest fixtures; config/strategies/ → 15 YAMLs
- Deploy: `ls deploy/systemd` → 7 units; deploy/hooks (secret_scan.py, pre-receive); gemini family `wc -l scripts/gemini_*.py` → 6 files/1,877 LOC, shared-helper grep → empty

## PHASE 2 — CONFIGURATION

### 2.1 What exists (inventory)
- **Master runtime config** `config/system_config.yaml` (443 lines, ~35 toggles/limit groups; BUILD-1 tagging convention `[LAUNCH-PHASE]`/`[PERMANENT]` at :10-19). Loaded by `core/config_loader.py` (1680 lines) into typed Pydantic v2 models.
- **Strategy configs** `config/strategies/*.yaml` — exactly 15 files (expected 15), all `enabled: true`, validated by `strategies/schema.py` (333 lines, `extra="forbid"`, cross-field model validator).
- **Cron source of truth** `config/cron_registry.yaml` (674 lines, 44 jobs, 0 disabled) → generates `deploy/cron/trading-system.cron` (14,419 B) via `scripts/generate_crontab.py` (`--generate/--check/--selftest/--bootstrap`).
- **Security config** `config/security.yaml` (94 lines) — standalone `yaml.safe_load` by `scripts/security_monitor.py` (deliberately outside the strict loader, documented at :2-6); `copy_protection:` top-level sibling key.
- **Accounts** `config/accounts.csv` (5 accounts, 1 enabled=LFL836) — pure env-var **NAME** indirection (`api_key_env,...`), zero secret values.
- **Env template** `.env.example` — all 16 credential slots are `FILL_WHEN_READY`/`FILL_*` placeholders; unmodified vs HEAD.
- **Other config/**: broker_costs, broker_limits, chartink_scanners, scan_webhook_map, scoring_weights, slippage_model, nse_holidays_2026, symbol_aliases (all loaded by the 8-file atomic loader); `instruments.csv` present on disk but **untracked** (`git ls-files` empty — 21-Jun staleness-incident fix holds, `.gitignore:33`).
- **GUI config**: committed base `ops_dashboard/backend/config/gui_config.yaml` (80 lines, PC paths, empty auth fields) + git-ignored overlay `gui_config.local.yaml` (VM: 845 B, **600 ubuntu:ubuntu**, keys only: paths/auth/password_hash/totp_secret/session_cookie_secure/reports_download_enabled/server) deep-merged in `backend/app.py:46-71` (ignore rule `ops_dashboard/.gitignore:9`).
- **systemd**: 7 committed units in `deploy/systemd/` + `ops_dashboard/deployment/gui-dashboard.service`. VM `/etc/systemd/system/trading-system.service.d/` contains ONLY `watchman.conf` (38 B, structure = `[Unit]` + `Wants=` — cannot hold a credential). **The deleted 03-Jul `telegram.conf` drop-in has NOT returned** (full dir listing is the evidence; no `telegram.conf` anywhere under /etc/systemd/system). `EnvironmentFile=/home/ubuntu/systems/trading-system/.env` is the single env source (trading-system.service:13).
- **VM .env**: exists, **600 ubuntu:ubuntu**, 3489 B, 24 vars (names verified only): 14× `ZERODHA_API_KEY/SECRET/TOTP_*` (5 accounts), 4× `TELEGRAM_*`, `WEBHOOK_SECRET`, `ALERT_SMTP_PASSWORD`, `ZERODHA_USER_ID/PASSWORD`, `GEMINI_BIN`. mtime 03-Jul 23:03 (post token-shadow fix).
- **Operator overrides**: `data_store/security/ssh_key_baseline.json` (290 B, 28-Jun) present, matches security.yaml:53-56 rotation note.
- **Broker token**: `data_store/session/zerodha_token.json` absent (expected — 05:00 daily delete, weekend).
- **Governance**: `docs/locked_decisions.yaml` (3067 lines, 181 decisions, status "LOCKED").
- **Runtime audit trail**: `core/config_snapshotter.py` → `config_snapshots` table; 2 rows (2026-07-02, 2026-07-03, both LIVE/INTRADAY) — correct for a v41-era (01-Jul) feature.

### 2.2 What works well
- **Typed, atomic, fail-fast loading**: every model `extra="forbid"` (CL3), all-8-files-or-nothing (CL2), per-file SHA-256 (CL4), no env interpolation (CL5) — `core/config_loader.py:14-23`. Typos in key names are hard startup failures, not silent defaults.
- **Self-auditing config**: `core/config_auditor.py` — one rule engine, two callers (startup `ACG` via `SystemConfig._cross_field_sanity_checks` at config_loader.py:1268-1291 with `raise_if_blocked()`; preflight A-G via `scripts/preflight/checks/config_sanity.py`, memoised, 7 rows in the 09:20 email). A1 BLOCKs the force_intraday×DELIVERY dead-system combo; B-group guards against resurrection of BUILD-1-deleted keys; F-group catches constructor-default drift.
- **Zero live drift, verified**: VM working tree at `b4aea6f` == PC HEAD; all 4 key config md5s byte-identical (system_config bf37a706…, security ab3494c0…, accounts 7ab9f843…, cron_registry 979f1b99…). Live crontab == committed canonical **byte-equal** (md5 f4242b94… both sides).
- **Cron single-source discipline**: registry → generator → canonical, pre-commit auto-regen (`deploy/hooks/pre-commit:25-29`), `check_cron_drift` cron (registry:526) + `cron-watchdog.timer` (enabled/active) as tier-2.
- **Secrets architecture**: name-indirection end-to-end (accounts.csv env names; `bot_token_env`/`password_env` in YAML; snapshotter stores names not values — config_snapshotter.py:23-27). `.env` 600; GUI overlay 600, untracked, survives `checkout -f`. Pre-commit scanner (`deploy/hooks/secret_scan.py`) blocks real .env, bot-token shapes, credential-named assignments, non-placeholder .env.example, and **parses .xlsx cell-by-cell, fail-closed** (:132-169); findings never echo values.
- **Residue better than briefed**: the 3 scripts said to embed a live api_key are FIXED at HEAD — all read `os.environ.get("ZERODHA_API_KEY_LFL836", "")` (fetch_daily_candles.py:39, gemini_data_integrity_check.py:48, reconstruct_excursions.py:68).
- **Delivery triple-lock is coherent**: `force_intraday_only: true` (:70) + `trade_type: INTRADAY` (:77) + `delivery_enabled: false` (:83) — independent layers, contradiction-gated by auditor A1, delivery caps explicitly documented inert (:171-172).
- **Config archaeology**: config_snapshots idempotent per (date,hash), captures same-day config changes as extra rows (config_snapshotter.py:19-22).

### 2.3 Risks
- **[MED] Webhook posture: `bind_host: "0.0.0.0"` + `require_hmac: false`** (system_config.yaml:180-188). Auth is a `?token=` query secret (Chartink cannot sign). Scenario: WEBHOOK_SECRET appears in any intermediate/access log or referer → holder can inject signals from anywhere at up to 60-burst/5-per-sec (:192-194) until screeners/caps stop them. Mitigations live: token required both modes (C-2), per-IP token bucket, secret rotated 03-Jul, downstream sizing/risk caps. UNCERTAIN: Oracle security-group reachability of :5000 not verifiable read-only from here. C-2 Phase 3 (bind/TLS/HMAC) explicitly open with Rama.
- **[MED] Dead safety net: email_fallback enabled but unfed.** `alerts.email_fallback.enabled: true` (:243) requires `ALERT_EMAIL_USER/PASSWORD/TO` — none exist in VM .env (24-name census). Consumer degrades to a log line "email_fallback.missing_credentials" (alerts/telegram_notifier.py:639-645). Scenario: Telegram outage during a CRITICAL → in-process email fallback silently no-ops; only the alert-watcher sentinel path (ALERT_SMTP_PASSWORD, present) remains.
- **[MED] Auditor G5 window check is dead code.** `config_auditor.py:502-503` reads `getattr(s, "entry_start"/"entry_end")` but StrategyConfig fields are `entry_start_time/entry_end_time` (schema.py:113-114) → always None, check never fires. The condition it exists to flag is REAL today: 15/15 strategies set `entry_start_time: "09:25"` vs global `entry_start: "10:00"`. Runtime is safe (global envelope binds) but the promised preflight warning is silent — same "dead safety net" theme as the 04-Jul repo audit.
- **[MED] Drop-in directory unwatched.** security.yaml watched_files (:64-73) hashes the unit FILES but not `/etc/systemd/system/trading-system.service.d/` — the exact vector of the 03-Jul telegram.conf shadow could return without a file-integrity alert. (Dir is clean today.)
- **[LOW] Override-replaces-default traps.** `security.yaml sudo_whitelist_prefixes` fully OVERRIDES the code default (bit once; now documented in memory + inline comments :30-42, but the mechanism is unchanged). Same class: GUI `market_clock` duplicated BY VALUE from trading_hours (gui_config.yaml:61-69, isolation rule I1) — relaxing entry_start to 09:20 in system_config would silently desync GUI freshness logic; no cross-check exists.
- **[LOW] Cron pre-receive equality guard not armed** — bare repo hooks contain only post-receive (VM listing); `deploy/hooks/pre-receive:3` says "NOT INSTALLED by default". Hand-edited canonical is pushable; compensated by pre-commit regen + drift cron + watchdog (all verified green today).
- **[LOW] Single-SL-owner rule is comment-enforced only.** "do NOT co-enable strategy.trailing_sl_enabled" with structure_exit (system_config.yaml:324) has no auditor contradiction check. Today safe: structure_exit off, 0/15 strategies set trailing_sl_enabled.
- **[LOW] VM config perms 664** (system_config.yaml, accounts.csv, .env.example world-readable) — content non-secret, single-user box; .env correctly 600.

### 2.4 Gaps
- No schema validation for `security.yaml` (deliberate standalone, but a typo'd key = silently weaker monitoring), `cron_registry.yaml` (generator round-trip is the only structural check), or the GUI YAML pair (deep-merge accepts any shape; auth validated only at use).
- `eod_reconcile.authoritative` (P1/v42) does NOT exist at HEAD — P1 was pulled from the 03-Jul deploy ("P1/v42 OUT"); only a forward-looking GUI label references it (ops_dashboard/backend/services/config_view.py:45). Flag census excludes it; reported as "planned, not present".
- Git history purge NOT run (confirmed): commit 9f58848 secrets remain in history, scrubbed at HEAD by 7bc3367, all 6 rotated dead 02-Jul. Residual = dead-secret archaeology, not live exposure.
- `special_sessions:` is an empty key with commented Muhurat example (system_config.yaml:45-50) — the 2026 Muhurat session will need a manual, easily-forgotten edit; nothing reminds.
- No committed gui-dashboard unit under `deploy/systemd/` (it lives at `ops_dashboard/deployment/`) — two homes for unit files; install docs differ per unit.

### 2.5 Technical debt
- **Flag debt is real but curated**: ~10 dormant/shadow booleans of ~35 toggles — `broker.fallback_enabled`, `broker.multi_account_mode`, `delivery_enabled`, `mis_filter.enabled` (+ pre-staged `shadow: true`), `capital.conditional_allocation_enabled`, `risk.daily_loss_include_unrealized` (SHADOW — MTM populated, would_reject logged, enforces realized-only), `sr_detector.wait_for_retest_enabled`, `structure_exit.structure_exit_enabled`, `smart_tgt.volume_dependent_trails`, Telegram secondary channel; plus inert params (delivery caps :171-172, commented `flat_value_rs`, empty slippage-override maps with `overrides.enabled: true`, GUI `reports_download_enabled: false` pending Q3). Every dormant flag carries an inline rollout comment; A1/A3 auditor checks cover the dangerous product-gate interactions. Interaction risk concentrated in the delivery ladder is triple-locked and BLOCK-gated.
- `config_loader.py` at 1680 lines vs CL6's own "split at 600 lines" rule (config_loader.py:22-23) — self-violating locked decision.
- Config comments carry heavy history (FIX-numbers) — excellent audit trail, but system_config.yaml is now 40% commentary; onboarding cost.
- `alerts.smtp.username`/addresses hardcoded in committed YAML (operator's own gmail; cosmetic).

### 2.6 Improvement opportunities
- Auditor additions are cheap wins (the engine + preflight plumbing already exist): fix G5 attrs; add "structure_exit + trailing_sl co-enable" contradiction; add "email_fallback.enabled but env names unresolvable" runtime-env check to preflight; add GUI market_clock == trading_hours cross-check.
- Watch the drop-in dir: one more `watched_files` row (dir-level or per-file) closes the telegram.conf recurrence vector.
- Arm pre-receive in DRY-RUN mode (env `CRON_GUARD_DRYRUN=1`) — designed for exactly this staged rollout.
- The [LAUNCH-PHASE] registry (config_auditor.py:162-169) holds only 1 param; several YAML comments (e.g. the `entry_start` relax note) suggest more candidates — formalize so the 09:20 email nags about all of them.

### 2.7 Phase verdict
Configuration is a genuine strength of this system: strict typed atomic loading, a self-auditing rule engine wired into both startup (fail-fast BLOCK) and morning preflight, single-source cron with byte-equal live state, and disciplined secrets indirection verified down to VM file perms — with zero config drift between HEAD and production today. The telegram drop-in shadow is confirmed eradicated and the briefed api_key script residue is already fixed at HEAD. Residual exposure clusters in a small set of dead or comment-enforced safety nets (email fallback env vars, the G5 dead check, unwatched drop-in dir) and the accepted webhook posture awaiting C-2 Phase 3.

### 2.8 Recommendations (prioritized)
1. **[S]** Feed or flip email_fallback: add `ALERT_EMAIL_USER/PASSWORD/TO` to VM .env, or set `email_fallback.enabled: false` so config matches reality; add a preflight check that enabled ⇒ env names resolve.
2. **[S]** Fix config_auditor G5 to read `entry_start_time/entry_end_time`; then decide: align 15 strategy YAMLs to 10:00 or accept + document the global-envelope override.
3. **[S]** Add `/etc/systemd/system/trading-system.service.d/` (and its files) to security.yaml watched_files.
4. **[M]** Execute C-2 Phase 3: loopback/allowlist bind or SG restriction to the known Chartink static IP + TLS; keep query-token as second factor.
5. **[S]** Add auditor contradiction check for structure_exit × trailing_sl_enabled before either ships enabled.
6. **[S]** Arm `pre-receive` cron guard in DRY-RUN for 2 weeks, then enforce.
7. **[M]** Cross-check (or generate) GUI market_clock from system_config trading_hours to kill the duplicate-by-value drift trap.
8. **[M/L]** Schedule the git history purge (BFG/filter-repo) at a quiet weekend — urgency low (secrets rotated dead) but it is the last open C-1 item.

### 2.9 Scorecard input
**Config discipline 9/10** — typed fail-fast loading + self-auditing rule engine + verified zero drift and byte-equal cron; docked for dead G5 check, comment-enforced invariants, and the 1680-line loader breaking its own CL6 rule.
**Secrets handling 8.5/10** — 600-perm .env single source, name-indirection everywhere incl. DB snapshots, fail-closed xlsx-aware pre-commit scanner, drop-in shadow eradicated, briefed residue already fixed; docked for query-string webhook token, unpurged git history, and GUI auth material co-located with an app-writable overlay.

### 2.10 Evidence log
- PC reads: `config/system_config.yaml` (full), `config/security.yaml`, `config/accounts.csv`, `config/cron_registry.yaml` (:1-120, 526), `config/strategies/gap_go_long.yaml` + flag census across 15 YAMLs, `.env.example`, `.gitignore`, `ops_dashboard/.gitignore`, `ops_dashboard/backend/config/gui_config.yaml`, `core/config_loader.py` (:1-60, model grep, :1204-1291, :1584-1680), `core/config_auditor.py` (full), `strategies/schema.py` (full), `strategies/loader.py` (type check), `core/config_snapshotter.py` (full), `deploy/hooks/{pre-commit,pre-receive,secret_scan.py}`, `deploy/systemd/trading-system.service`, `cron-watchdog.service`, `ops_dashboard/deployment/gui-dashboard.service`, `docs/locked_decisions.yaml` (:1-50), `scripts/preflight/checks/config_sanity.py`, `ops_dashboard/backend/app.py:46-71`, `alerts/telegram_notifier.py:631-676` (grep).
- PC commands: `git rev-parse HEAD` (b4aea6f); `git ls-files` (instruments.csv untracked, no tracked .env/local.yaml/token/credentials); `git show HEAD:<4 configs> | md5sum`; grep API_KEY assignments in 3 scripts; strategy `entry_start_time` census (15× "09:25").
- VM (2 batched read-only SSH sessions): stat/grep-names-only on `.env` (600, 24 vars) and `gui_config.local.yaml` (600, key names); full listings of `/etc/systemd/system/*.service.d/` (only watchman.conf 38 B `[Unit] Wants=`; no telegram.conf); `systemctl is-enabled/is-active` × 7 units (trading-system inactive = expected Sunday); `crontab -l | md5sum` vs canonical (byte-equal); bare-repo `rev-parse HEAD` (b4aea6f) + hooks listing (post-receive only, no pre-receive); md5sum of 4 VM configs (== HEAD blobs); `ls data_store/security/` (ssh_key_baseline.json present); token file absent; `sqlite3 mode=ro` on `config_snapshots` (2 rows, 02→03-Jul, LIVE INTRADAY).
- Method notes: one self-inflicted false positive corrected in-line (`find … && echo FOUND` fires on exit-0-no-match; directory listings are the authoritative negative for telegram.conf); first .env var count (9) was a regex artifact (`[A-Z_]` excluded digits), corrected census = 24. No secret VALUES were displayed at any point; no mutating command was run on PC or VM.

## PHASE 3 — DATABASE

### 3.1 What exists (inventory)
- **Two-file split (v28/O6)**: `trading_system.db` 256,901,120 B (245 MiB; page_size 4096 × 62,720 pages, freelist 0) + `analytics.db` 7,102,464 B, ATTACHed as `analytics` on every production connection (`core/db_connect.py:52-64`). Both WAL; WAL files 0 B at capture (post-01:00-backup checkpoint).
- **Schema**: 42 tables live (verified count) = spec'd inventory in `core/schema.sql` (1,487 lines, versioned header). Domains: trading spine (signals 95,431 / trades 228 / orders 340), capital (fm_ledger 1,242; capital_snapshot 0), recon (reconciliation_log 7,764; eod_squareoff_log 14; eod_verification 11; gtt_state 0; position_reconciliation 0; pnl_reconciliation 0), screening (screener_results 82,574; webhook_audit 58,507), state-parking (gate_state/retest_state/smart_tgt_state — all 0, clean weekend), analytics/observability (control_tower_* 4/5/5/5/30, preflight_* 0/0/0, sr_detector_results 71, trade_slippage_log 59, order_execution_log 118, trade_excursions 38, innings 143, strategy_metrics 84, config_snapshots 2, cron_heartbeat 1,266, system_events 106). Analytics file: candles 36,327 (19-Jun..03-Jul), system_metrics 919, system_metrics_daily 11.
- **Version storage**: `schema_meta` key/value table (schema.sql:29-32), bumped by trailing `INSERT OR REPLACE ... '41'` (schema.sql:1442). `PRAGMA user_version` deliberately unused. **Live DB = v41 = HEAD's `EXPECTED_SCHEMA_VERSION = 41`** (`core/state_store.py:101`). Verified.
- **Migrations**: `core/migrations.py` — 12-step table-rebuild for constraint changes (`MIGRATION_TABLES`: v25/26/27/30/32/34/35), pure-add discipline v36→v41 as claimed; version read *before* executescript, failed migration leaves version untouched (state_store.py:329-356).
- **Connection discipline** (state_store.py:106-111, 213-228): WAL + `synchronous=FULL` + `foreign_keys=ON` + `temp_store=MEMORY` + `busy_timeout=30000`; `isolation_level=None` with explicit `BEGIN IMMEDIATE` (L452); `check_same_thread=True`, thread-local connections. GUI reads via `file:...?mode=ro` URI with analytics attached ro (`ops_dashboard/backend/readers/db_reader.py:48-70`).
- **Backup/retention machinery** (all verified in live crontab): 01:00 `.backup` main, 01:05 `.backup` analytics, 02:00 `backup_retention.py --apply` (keep pre_*=20, dailies=14+14), 02:30 `db_retention.py` Mon-Sat + Sunday `--vacuum`, monthly 03:00 restore drill; 16:00 `wal_checkpoint` cron + TRUNCATE checkpoint at EOD squareoff (state_store.py:247-301).

### 3.2 What works well
- **Integrity is clean**: `PRAGMA quick_check` = ok on both files; full `PRAGMA foreign_key_check` = **0 violations**; 0 dangling trades.signal_id, orders.trade_id, signals.trade_id, screener_results.signal_id, sr_detector_results.signal_id; 0 duplicate (fingerprint, date); 0 trades with >1 ENTRY/CO order; 0 stale rows in the three state-parking tables; trades.reservation_id ⊆ fm_ledger 100%.
- **Real constraint discipline, unusual for SQLite**: FKs declared *and* enforced per-connection; CHECK enums on signals/trades/orders/fm_ledger.entry_type/gtt_state/retest_state/kill_switch_state with thoughtfully open GLOB families (`REJECTED*`, `GATE_*`) so a new sub-status can't crash an INSERT (schema.sql:50-70 rationale is textbook).
- **Index coverage matches query patterns**: every index the audit brief suspected missing actually exists — `idx_signals_fingerprint_today` UNIQUE (dedup), signals(status/received_at/trade_id), orders(trade_id/status/leg + partial superseded_by), screener_results(signal_id/ts/status), fm_ledger(ts/date/reservation_id/session_id), candles(symbol,ts)+UNIQUE(token,ts,interval)+date. v27 STORED generated `date` columns give index-backed date-range queries instead of `DATE(ts)` wraps.
- **Backup story executes, not just exists**: live counts 14 main + 14 analytics + 9 pre_* exactly match keep-N policy; retention log shows sane plans with "never-delete-newest" + max-delete cap; **monthly restore drill actually ran 01-Jul-2026 03:00, heartbeat SUCCESS** (restores latest backup to a temp copy, checks version/26 tables/FK joins — `scripts/backup_restore_drill.py`); Sunday VACUUM demonstrably reclaimed 86 MiB (backup series 244,494,336 → 158,085,120 across 28-Jun).
- **Write-path centralization**: one writer class (StateStore, BEGIN IMMEDIATE serialization), GUI provably read-only at the SQLite layer (write raises; test-pinned), and the v28 ATTACH trap is defused — all 8 scripts touching candles/system_metrics route through `core.db_connect`; none of the raw-connect stragglers query analytics tables.
- **fm_ledger design** (append-only write-ahead, closed entry_type enum, generated date column) is genuinely good capital-audit engineering.

### 3.3 Risks
- **[HIGH] All backups on the same disk as the primary.** 37 backup files, both DBs, all under `/dev/sda1` (96 GB, single VM). No rclone config, no scp/rsync/S3 cron (verified crontab + `~/.config`). Failure scenario: provider disk loss / VM deletion / ransomware-grade compromise (SSH was a live concern in June) destroys primary *and* every backup simultaneously — total loss of the trade audit trail of a real-money system. The Kite console/contract notes would be the only surviving record.
- **[MED] `signals` has no retention policy** — the largest table (95,431 rows; webhook_payload alone 73.4 MB avg 769 B) is absent from `DEFAULT_RETENTION` (`scripts/db_retention.py:63-70` covers candles/system_metrics/webhook_audit/screener_results/reconciliation_log/fm_ledger only). At observed ~7.8k signals/trading-day, +~1.9M rows ≈ +1.5–2 GB/yr uncapped. Not fatal, but it steadily inflates the 14 daily full-copy backups and every `.backup`/VACUUM window.
- **[MED] Cron-initiated auto-migration under a live app.** Any StateStore-constructing cron (db_retention 02:30 daily, etc.) runs `_initialize_schema` → `run_migrations` + `executescript` on init (state_store.py:307-367). Scenario: code deployed without app restart → the 02:30 cron migrates the DB (worst case a table REBUILD) beneath the still-running v-old app. Mitigated today only by deploy discipline ("deploy requires restart").
- **[LOW] Growth outpaces intuition**: backup series shows ~15–20 MB/day file growth on trading days (158→257 MB over 5 sessions, Jun-29→Jul-4) — 55% of the file is two JSON blob columns (webhook_payload 73.4 MB + screener step/latency/market snapshot blobs 67.3 MB). 12-month projection: main DB ~2.5–3.5 GB (UNCERTAIN ±50%; screener/webhook_audit 90-d caps first bite ~10-Sep-2026), backups dir ~35–50 GB. Disk 16% used today — fine for 12 months, needs the offsite/retention answer before mid-2027.
- **[LOW] position_reconciliation ERROR path never persists**: broker-fetch failure returns the ERROR result *before* the insert loop (`scripts/reconcile_positions.py:244-254` vs insert at 296-298) — a failed reconcile day leaves no row, so "no rows" is indistinguishable from "no positions". Table currently has 0 rows ever (clean-day writes only fire when positions exist at 15:45).
- **[LOW] Legacy data-quality residue, bounded to 15–18 Jun**: 3 CLOSED_MANUAL trades with NULL exit_time+net_pnl (trd_763801ae/SULA, trd_08278985/GICRE, trd_352c25b3/AGARIND — the known W11 class; skews any AVG(net_pnl) that forgets NULL-handling) and 10 fm_ledger RESERVE rows without COMMIT/RELEASE terminal (₹1,628 nominal, 15–18 Jun crash-remediation era; none since). FundManager rehydrates from open trades, so no live capital leak — UNCERTAIN whether any ledger-replay tooling would double-count them.

### 3.4 Gaps
- **Six-plus dead/empty audit tables** ("dead safety nets" theme confirmed at DB layer): `capital_snapshot` and `telegram_alerts` have **no production writer at all** (grep: only tests); `preflight_runs/_check_results/_autofix_log` (v33) were never wired to the preflight system that runs daily (markers exist, 0 rows ever); `pnl_reconciliation` empty — reconcile_pnl absent from cron registry (its replacement, P1 EOD broker-reconcile v42, is built but unpushed/shadow); `shadow_trades` 0 (engine off). The daily broker-vs-system P&L check currently persists **nowhere** in the DB.
- **Restore drill covers main DB only** — `find_latest_backup` globs `trading_system-*.db`; analytics backups are taken but never drill-restored (acceptable severity, but untested backups are hope, not backups).
- **ANALYZE never run** — no `sqlite_stat1` in either file; planner runs on defaults. Immaterial at 95k rows; will matter at 1M+ signals.
- **No offsite copy** (see 3.3 risk) and 2 unmanaged pre-deploy copies (`*.pre-deploy-2026-07-03`, 264 MB) sit in `data_store/` root outside the retention-managed `backups/` dir.
- **system_metrics observability gap**: cpu_pct/memory_mb are `-1.0` in **919/919 rows** (100% placeholder; only db_size/log_size/disk real since the 29-Jun fix) — the resource-trend table cannot answer "is the process leaking memory".

### 3.5 Technical debt
- `orders.reconciliation_status` contains undocumented values `RECONCILED`, `RECONCILED_NO_FILL` (live distribution: NULL 258, OK 77, RECONCILED 1, RECONCILED_NO_FILL 3, SL_MISSING 1) vs the documented set NULL/OK/MISMATCH/SL_MISSING (schema.sql:313-316); column has no CHECK — comment/code drift.
- 15 raw `sqlite3.connect` sites outside core (non-test): benign but inconsistent — `check_vm_state.py:20`, `gemini_premarket_brief.py` ×4, `fetch_daily_candles.py:47` (main-DB read, no busy_timeout), preflight checks ×7 (one is a deliberate `timeout=5` lock probe, `preflight/checks/database.py:139`), drill ×6 (temp copy — fine). None are read-only URIs, so each is a *potential* accidental writer; none query analytics tables, so no ATTACH-trap today — the trap re-arms the day someone adds a candles query to one.
- schema.sql trailing comment block ("END OF SCHEMA v24") stops narrating at v27 while the file is at v41 — harmless but misleading to a new reader.
- `cron_heartbeat` (1,266 rows) and several small ops tables have no retention entry — trivial size today, unbounded shape.
- capital_snapshot is schema-documented as "the fast read of capital" yet has 0 rows since the epoch — either delete the table or the comment.

### 3.6 Improvement opportunities
- Offsite backup: nightly `rsync`/rclone of the two newest `.backup` files + `.env`-free config to any second location (PC pull over Tailscale is zero-new-infra); rsync binary already present.
- Add `signals` to DEFAULT_RETENTION (e.g. 180 d full row; or 90 d NULL-out `webhook_payload` only, preserving the signal row forever — halves the growth rate while keeping the P16 audit).
- Wire the P1 EOD broker-reconcile (v42) or restore reconcile_pnl so `pnl_reconciliation` lives again; fix the reconcile_positions ERROR early-return; write a daily "0 positions, OK" sentinel row so absence becomes signal.
- Decide the fate of the 6 dead tables: wire writers (preflight persistence is ~20 lines) or drop at the next rebuild-class migration.
- Add `PRAGMA optimize` on clean shutdown / weekly cron (cheap, self-tuning ANALYZE).
- Backfill the 3 NULL-exit CLOSED_MANUAL rows from broker records (one-time UPDATE via a reviewed script) or exclude-list them in analytics readers.
- Drill the analytics backup too (one glob change + table list).

### 3.7 Phase verdict
This is a disciplined, evidently-maintained SQLite layer that most solo-operated trading systems never reach: enforced FKs, CHECK enums, versioned rebuild-capable migrations, BEGIN IMMEDIATE single-writer discipline, read-only GUI isolation, and a backup/retention/VACUUM/restore-drill loop that verifiably executed this month. Integrity checks came back essentially perfect (zero orphans/dupes/FK violations; residue confined to the 15–18 Jun go-live week). The two material weaknesses are strategic, not mechanical: every byte of backup lives on the same disk as the primary, and the biggest table (signals) plus its JSON payloads has no retention story while the audit-table graveyard (capital_snapshot, telegram_alerts, preflight_*, pnl_reconciliation) quietly claims controls that don't actually record.

### 3.8 Recommendations (prioritized)
1. **[S] Offsite backup copy** — nightly rsync of newest main+analytics backups to PC or object storage; alert on staleness. Single highest-value DB action available.
2. **[S] Add signals to db_retention** (payload-NULL-out at 90 d or row-delete at 180 d) before the table crosses 1M rows.
3. **[S] Fix reconcile_positions ERROR-path insert + persist P&L reconcile again** (push P1 v42 or re-cron reconcile_pnl).
4. **[M] Dead-table disposition** — wire preflight persistence + telegram_alerts writer, or drop the 6 tables in one documented migration; update schema comments (reconciliation_status enum, v24 trailer).
5. **[S] Guard cron-side migrations** — a `--no-migrate` StateStore mode (fail fast on version mismatch) for cron entrypoints, so only the app/deploy path migrates.
6. **[S] Backfill/annotate the 3 NULL-exit trades and 10 dangling RESERVEs** so future analytics need no tribal knowledge.
7. **[M] Add `PRAGMA optimize` weekly + extend the restore drill to analytics.db.**

### 3.9 Scorecard input
- **Scalability 6.5/10** — comfortable 12-month runway at ~15–20 MB/day with retention capping most bulk tables, but unbounded signals growth, JSON-blob-heavy rows (55% of file), single-writer SQLite and same-disk full-copy backups set a real ceiling; nothing here survives a 10× signal-volume future without the retention and offsite work.
- **Data-layer reliability 8/10** — FULL-sync WAL, enforced FKs, CHECK enums, BEGIN IMMEDIATE, versioned migrations, clean quick_check/FK-check/orphan sweep, and an *exercised* backup-restore-retention loop; docked for same-disk-only backups, dead audit tables masquerading as controls, and the bounded go-live-week data residue.

### 3.10 Evidence log
- Schema/DDL: `core/schema.sql` (CHECKs :60-70/:158-161/:298-301; FK note :17-18; O9 circular-ref :104-115; version bump :1442); `core/analytics_schema.sql`; split rationale `core/db_connect.py:1-64`.
- Connection/txn: `core/state_store.py:101` (EXPECTED=41), :106-111 (PRAGMAs), :213-228 (thread-local, check_same_thread), :247-301 (PASSIVE/TRUNCATE checkpoints, FIX-088 ticker guard), :426-464 (BEGIN IMMEDIATE), :307-376 (migrate-then-executescript ordering); `core/migrations.py:95-114` (MIGRATION_TABLES), :275 (FK off during rebuild + foreign_key_check after :300).
- GUI isolation: `ops_dashboard/backend/readers/db_reader.py:48-70` (mode=ro URI, analytics ro ATTACH).
- VM live (all via `file:...?mode=ro` URI, Sun 05-Jul 01:40 IST): schema_version=41; quick_check ok ×2; page/freelist stats; 42-table rowcount sweep; index_list on 6 hot tables (match schema); sqlite_stat1 absent; signals/screener rows-per-day (12 d); blob sizes (73.4 MB / 67.3 MB); date ranges (epoch 12-Jun-2026); status distributions (trades FAILED 120/CLOSED 60/CLOSED_MANUAL 24; orders COMPLETE 161/CANCELLED 179); integrity suite: fk_check 0, all orphan classes 0, dup fingerprints 0, multi-ENTRY 0; 3 NULL-exit CLOSED_MANUAL (trd_763801ae/trd_08278985/trd_352c25b3, 15–16 Jun); 10 RESERVE-without-terminal (15–18 Jun); analytics: candles 36,327 (19-Jun+), system_metrics 919 with cpu=-1 ×919; eod_verification 11×VERIFIED; drill heartbeat `('backup_restore_drill','2026-07-01T03:00:03',SUCCESS)`; wal_checkpoint heartbeats 01–03 Jul SUCCESS.
- Files/backups (VM shell): DB 256,901,120 B + WAL 0 B; backups 3.4 G / logs 834 M; counts 9 pre_* / 14 / 14 = policy; backup size series 22-Jun 64,978,944 → 28-Jun 244,494,336 → 29-Jun 158,085,120 (Sunday VACUUM) → 04-Jul 256,901,120; crontab lines for db_backup/analytics_backup/backup_retention/db_retention(+Sun --vacuum)/backup_restore_drill; no rclone config/offsite job; marker files rc=0 (db_backup 05-Jul 01:00:05, db_retention 04-Jul 02:30:01).
- Retention policy: `scripts/db_retention.py:63-70` (signals absent); `scripts/backup_retention.py:43-47` (keep-N); cron_registry.yaml:22-111, :585-599 (drill), :380-393 (wal_checkpoint 16:00).
- Empty-table writers: reconcile_positions early return `scripts/reconcile_positions.py:244-254` vs insert :296-298; `preflight_runs` INSERT absent from all production code (grep: only tests/migrations/db_reader); `INSERT INTO telegram_alerts|capital_snapshot` absent outside tests; reconcile_pnl absent from cron_registry.
- Raw-SQL sites (non-test): check_vm_state.py:20; gemini_premarket_brief.py ×4; fetch_daily_candles.py:47 (main-DB read, no busy_timeout); preflight engine.py:91, state.py:36/47, database.py:25/36/114/139 (one deliberate `timeout=5` lock probe); backup_restore_drill.py ×6 (temp copy); none touch analytics tables — db_connect users: fetch_daily_candles, capture_metrics_baseline, control_tower ×3, gemini_data_integrity_check, gemini_trade_coach.

## PHASE 4 — TRADING FLOW

### 4.1 What exists (verified flow map)

**Corrections to the prior map:** (1) Signal expiry is **600s, not 60s** — `config/system_config.yaml:55` (`expiry_sec: 600`), wired to both receiver and processor (`main.py:2590`). (2) Sizing lives in `capital/position_sizer.py`, not `screening/` (risk_engine.py:60). (3) Webhook dedup is two-layer: in-memory TTLCache **plus** DB unique index — not fingerprint-only. (4) Auth accepts HMAC (`X-Webhook-Signature`) or `?token=`; token fallback disabled when `require_hmac=true` (webhook_receiver.py:410-432).

| # | Stage | Module:line | Can fail | Detection | Recovery/backstop | Residual |
|---|-------|-------------|----------|-----------|-------------------|----------|
| 1 | Ingress | webhook_receiver.py:339-719 | flood, bad payload, dupe, stale, queue full | per-IP bucket→429 (:354-366), auth→401 (:410-432), kill→403 (:440), backpressure→503 (:444-451), window→403 (:455), casts→400 (:281-333), price≤0 (:623-628), expiry 600s (:638-640) | TTL dedup 300s (:654-661) + DB UNIQUE(fingerprint,fingerprint_date) via IntegrityError (:695-698, schema.sql:82) — dupes never stored; QUEUE_FULL persisted (:703-715); webhook_audit every POST (:773-801) | non-IntegrityError DB fault leaks symbol lock 60s + dedup-blocks 300s (only sweeper :823-848 clears) |
| 2 | Symbol serialization | webhook_receiver.py:648,721-767 | stuck worker holds symbol | heartbeat-aware sweeper, 60s no-heartbeat evict (:830-848) | processor heartbeats at 5 checkpoints (signal_processor.py:620,766,822,887,953) | in-memory only; restart clears (safe direction) |
| 3 | Pipeline gates | signal_processor.py:617-733 | kill/window/expiry/shadow/control/governor bypass | kill (:627), window (:632), expiry (:636-642), shadow-inning fail-closed (:651-671), `strategy_will_trade` 3-layer (:702-715; strategies/control.py:67-116), per-strategy window (:720), governor (:728-733) | second kill check pre-placement FIX-070 (:960), placer last-mile kill (order_placer.py:931-940) | — |
| 4 | Screening | secondary_screener.py:89-309 | quote outage, step error, low score | SKIPPED_QUOTE_UNAVAILABLE (:141-150), REJECTED_STEP_ERROR (:212-232), min_score override (:251-272), circuit-proximity reject sharing clamp constant (:154-178, 355-415), MIS blocklist flag-gated (:108-131) | screener persists own status (P18, :461-495) | quote outage silently drops signal (correct fail-closed) |
| 5 | Price derivation | signal_processor.py:1188-1393 | entry≤0, sl_pct=0, degenerate TGT | REJECTED_INVALID_DERIVED_PRICE (:1226-1238), ZERO_SL (:1265-1275), sl bounds (:1292-1311), TGT_DISTANCE_TOO_SMALL (:1385-1391) | — | — |
| 6 | Sizing+Risk+Reserve | signal_processor.py:804-887; risk_engine.py:177-598 | TOCTOU cap overshoot, capital overdraw | approve+reserve atomic under `fm.portfolio_lock` (:855-886); 10 checks incl. dual-hardened OPEN_POSITIONS (risk_engine.py:385-439) and DAILY_TRADES (:464-488) using `count_live_reservations` (fund_manager.py:1374-1388); B-1 unrealized-MTM term freshness-gated (:509-545) | reservation write-ahead fm_ledger (fund_manager.py:529-545); RESERVED status inside lock (:885) | per-strategy cap read outside lock (signal_processor.py:843-853) — same-strategy burst can exceed strategy cap (global caps still bind); MTM enforce flag still false (system_config.yaml:176) |
| 7 | Throttle | signal_processor.py:981-989; entry_throttle.py:65-115 | entry burst | atomic check-and-record; 20s gap / 3-per-60s / 300s per-symbol (system_config.yaml:202-205) | releases reservation on throttle (:983-985) | — |
| 8 | Placement | order_placer.py:757-1595 | stale price, drift, illiquidity, broker reject/429/timeout | R:R gate (:819-840); trade row PENDING_FILL **before** broker (:875-897); PENDING pre-call FIX-071 (:947); EOD cutoff 15:15 (:958-979); slippage guard vs trigger (:985-1074); drift top-up FIX-075 (:1076-1171); liquidity (:1173-1187); 429 retry loop (:1198-1290); 16388 one retry (:1216-1254) | timeout→UNKNOWN_IN_FLIGHT + recovery queue, capital kept (:1291-1323); persist-fail→cancel+hard_kill (:1355-1393); fill_map insert **before** track OP-AR1 (:1414-1563) | slippage guard best-effort — LTP fetch fail skips it (:991) |
| 9 | Broker chokepoint | zerodha_adapter.py:470-627 | off-tick reject, tag reject, CNC leak, rate limit | validate (:519); tick-snap both modes (:521-526, 1236-1290, fail-safe DEFAULT_TICK); CNC lock (:535-544); rate limiter (:564); tag truncate ≤16 (:578-579; ids.py:104-124) | OSM FAILED on exception (:593-599); paper parity `_synth_fill` LTP-gated + live-mode guard (:1985-2031) | — |
| 10 | Fill tracking | order_monitor.py:583-692 | poll gap, auth/API failure, lost order | 2s poll (system_config.yaml:113); auth 3x→stop+critical (:637-643); API 3x→hard_kill (:666-677); empty-history 3x + second-source orphan check (:694-702); fill_timeout 60s | ghost-fill safe: unknown internal_id ignored (order_placer.py:1610-1612) | pending_rr cancel-fail = ERROR only, no alert; self-retries next poll (order_monitor.py:1190-1196) — **known-issue verified** |
| 11 | Entry fill | order_placer.py:1921-2072 | commit fail, DB fail | commit_to_used BL-4: any failure→hard_kill (fund_manager.py:843-986); negative excess handled (:893-896); record_entry_fill→OPEN (order_manager.py:378-406) | partial-then-terminated handler commits actual qty + protects (order_placer.py:1620-1647) | — |
| 12 | Exit placement | order_placer.py:2656-2957; order_protocol_limit.py:263-407 | naked position, wrong-side clamp | exits deferred to fill, sized to filled qty (:2668-2675); TGT recalc from fill w/ frozen R:R (:2701-2712); clamp gate: SL unplaceable→SLUnplaceableError raised pre-placement (order_protocol_limit.py:312-338); SL-first, TGT never before SL (:280-282, 374-401); TGT-unplaceable→SL-only + TGTRetryManager (:2792-2806) | LTP-validation→retry queue (:2741-2750); any other→emergency marketable-limit exit (:3636-3719, price_math.py:127) + hard_kill (:2752-2776); persist/track fail→cancel+hard_kill (:2855-2933); after-check verify (:2951-2957) | — |
| 13 | Exit fill→P&L | order_placer.py:2107-2357; fund_manager.py:1112-1242 | double-close, cost fail, release fail | close_trade guarded to OPEN/PARTIAL (order_manager.py:536-542); OCO race→warn-return (:2211-2217); sibling cancel after close, before release (:2259-2276); direction-aware PnL (:2194-2198); RELEASE_USED pnl_delta ledger + loss-breach callback (fund_manager.py:1177-1229) | release_used fail→CHECK7/G3 capital-drift backstop (:2290-2299) | cost-calc failure→charges=0.0 (:2186-2192) understates costs in net_pnl |
| 14 | Reconciler 15s | order_reconciler.py:558-966 | everything above | non-blocking lock — cycles never overlap (:565-576); prepasses: GTT adoption (:611-627), A-1/E-1 recovery (:3221-3304); CHECK1 manual-close w/ atomic guard (:1021-1184; state_store.py:1528-1556); CHECK2 orphan/human/inflight/oversell (:1443-1725); CHECK4/5/6; CHECK9 missing exits (:2020+); duplicate-exits keystone (:2662-2807); CHECK7 capital drift; CHECK8 CO-SL drift **alert-only** (:3133+); B-1 MTM refresh (:2811) | G5b recovery-SL behind 4 layers: local SL check (:2389-2400), settling window (:2414-2421), authoritative broker/fill_map check (:2427-2433), no-position skip (:2437-2445) | CHECK1 releases with costs=0.0 (:1102) — known, matches memory note |
| 15 | EOD 15:17 | eod_squareoff.py:196-354, 1656-1807 | double-fire, crash mid-fire, missed fire | per-date atomic claim, reset-on-failure (:215-229); write-ahead IN_PROGRESS (:314-334); soft_kill first (:336-348); two-pass cancel-then-exit (:350+) | restart: COMPLETE→skip, IN_PROGRESS→one recovery fire, past-close miss→CRITICAL alert + event (:1683-1797) | — |

**In-flight windows:** reserve→create_trade crash: dangling ledger RESERVE not replayed by BL-1 (only OPEN/PARTIAL chains, fund_manager.py:1517-1663) — no capital lock post-restart. create_trade→broker crash: PENDING row → crash feed (state_store.py:873-901) → tag correlation. Broker-accept→persist crash: same feed + `correlate_entry_by_tag` (order_reconciler.py:186-230). Timeout: UNKNOWN_IN_FLIGHT, reservation kept, reconciler sole owner (signal_processor.py:1009-1029; order_placer.py:1291-1323).

**State machines (schema.sql):** `signals.status` open-set CHECK (:60-70); production writers: QUEUED/QUEUE_FULL (receiver), PROCESSING/RESERVED/PROCESSED/PROCESSED_NO_PLACER/PLACEMENT_FAILED/TIMEOUT/REJECTED_* (processor), PASSED/REJECTED_*/SKIPPED_* (screener), GATE_WAITING/GATE_RELEASED_* (entry_gate.py:304,494-496), RETEST_*. Dead vocabulary (no writer): ACCEPTED, TRADED, DUPLICATE, EXPIRED, INVALID_*, OUTSIDE_HOURS, IN_PROCESS, CANCELLED, FAILED, PENDING — pre-insert rejects are HTTP-response-only, never persisted. `trades.status` (:158-161): PENDING_FILL(order_placer.py:878)→PENDING(:947)→[UNKNOWN_IN_FLIGHT(:1307)]→OPEN(order_manager.py:398)→CLOSED/CLOSED_MANUAL/EXITING; FAILED/REJECTED*/CANCELLED via _handle_placement_failure. **`trades.PARTIAL` has no production writer found** (readers everywhere include it; orders.PARTIAL is OSM-driven). CLOSED→anything: no production caller, and all three finalizers are guarded (order_manager.py:536; state_store.py:1552, 1384) — but `update_trade_status` (order_manager.py:631-637) and `record_entry_fill` (:394-406) are raw UPDATEs with no from-state guard, so the invariant is by convention, not enforcement.

### 4.2 What works well (verified defenses)
- **Approve+reserve atomicity** under portfolio_lock with RESERVED written inside the lock (signal_processor.py:855-886) — kills the sector/position double-approve race.
- **Triple-hardened caps**: DB atomic count (Bug E, state_store.py:560-577) + processor in-flight counter (FIX-018) + live-reservation count (FIX-185/Bug B, risk_engine.py:406-439, 476-488). Restart-burst and pre-insert TOCTOU both closed; `max()` composition can only harden.
- **Capital exactly-once**: write-ahead fm_ledger on every mutation (fund_manager.py:529-545, 607-618, 898-917, 1177-1192); commit failure after broker fill = hard_kill (BL-4, :935-986); release idempotent (:600-602); invariant check on every mutator with deferred hard_kill (C.1 pattern).
- **A-2 timeout discipline**: BrokerTimeoutError never retried, reservation single-owner handoff to recovery, in all three pipeline variants (signal_processor.py:1009-1029, 1663-1676, 1936-1948).
- **A-1/E-1 recovery**: one orderbook read per cycle, defer-on-unreachable (never release blind, order_reconciler.py:3254-3268), absence poll budget before FAILED (:3359-3401), atomic `fail_recovery_trade` (state_store.py:1372-1388), partial-fill-then-cancel adopted not disowned (:3344-3350), AMBIGUOUS tag collision → manual, never blind-adopt (:186-230).
- **Naked-position lattice**: SL-first placement (order_protocol_limit.py:280-282), wrong-side clamp gate (NOCIL, price_math.py:284-321), emergency marketable-limit exit + hard_kill on SL failure (order_placer.py:2752-2776), CHECK9, G5b 4-layer, always-on duplicate-exit dedupe with canonical-keep + naked re-verify + instant SL re-place (order_reconciler.py:2699-2777) — the RAMCOIND class is defended at four independent layers.
- **Reverse-aware emergency exit** (THELEELA fix): broker-confirmed direction before flatten, EXITING marked to prevent double-sell (order_placer.py:3653-3667); EXITING excluded from CHECK1/G5b, finalized by _check_stuck_exiting→CHECK1 (order_reconciler.py:1186-1237; state_store.py:1528-1556 accepts EXITING).
- **Fill-event idempotency**: pop-once _fill_map, ghost fills ignored, OCO double-close warn-return, insert-before-track (OP-AR1) closes the instant-fill ghost window (order_placer.py:1414-1420).
- **EOD**: atomic per-date claim + write-ahead + crash recovery + late-miss alerting (eod_squareoff.py:196-229, 314-334, 1656-1807).
- **Paper/live parity**: tick-snap runs in both modes (zerodha_adapter.py:521-526); synth fill LTP-gated with live-mode guard (:2023-2031).

### 4.3 Risks
- **[MED] FIX-067 fresh-quote skew**: momentum path re-derives entry from live LTP but discards the re-derived SL (signal_processor.py:927 `fresh_entry_price, _`); TGT (:907) and sizing/reservation (:805, :871) stay on the stale anchor. Entry moves up to the slippage tolerance while SL stays fixed → realized risk-per-trade exceeds sized risk by up to the allowed slippage fraction. Partially mitigated by the sl_fraction slippage abort (order_placer.py:999-1003) and fill-time TGT recalc (:2701-2712) — the SL is the one leg never re-anchored.
- **[MED] Slippage guard is best-effort**: LTP fetch failure skips the guard entirely (order_placer.py:991) — during a quote outage a badly stale LIMIT can be placed. Same fail-open on drift top-up (:1159-1171).
- **[MED] CO bracket single-backstop**: CHECK8 is alert-only (order_reconciler.py:3133+); broker-managed CO SL with CHECK1 the only external-close finalizer — verified as documented. Currently LIMIT_TRIPLE is the default protocol (main.py has no CO default override; order_placer.py:843), so exposure is dormant.
- **[LOW] Per-strategy position cap TOCTOU** (signal_processor.py:843-853 runs outside portfolio_lock): concurrent same-strategy signals can both pass; bounded by global caps + throttle min-gap 20s.
- **[LOW] DUPLICATE_SYMBOL blind spot**: `has_active_position` counts only PENDING_FILL/OPEN/PARTIAL (state_store.py:727-741) — a symbol in PENDING/UNKNOWN_IN_FLIGHT/EXITING is invisible to it; covered in practice by the 300s per-symbol throttle (recorded at admit, before the timeout) and 300s webhook dedup.
- **[LOW] CHECK1/RMS closes book costs=0.0** (order_reconciler.py:1102) — net_pnl slightly optimistic for external closes; feeds the daily-loss gate via fm_ledger. Known and accepted (memory note); magnitude is per-trade charges (~Rs 3-5).
- **[LOW] Webhook insert failure path** (non-IntegrityError, e.g. DB locked): 500 returned, but TTL cache already marked (webhook_receiver.py:661) and symbol stays in_flight until sweeper — that scanner+symbol is dark for up to 300s with no alert.

### 4.4 Gaps
- No LTP-vs-trigger sanity check at ingress: `price>0` is the only price validation (webhook_receiver.py:627); a fat-fingered Chartink price flows to screening, where circuit/score checks catch most but not all distortions (derived entry/SL stay trigger-anchored for pullback strategies).
- No symbol-existence check against instrument cache at ingress — unknown symbols burn a pipeline pass to die as SKIPPED_QUOTE_UNAVAILABLE.
- No DB-level transition guard on `trades.status` (raw UPDATEs in order_manager.py:631-637, 394-406); CLOSED-reopen is prevented only by caller discipline.
- QUEUED signals stranded by a crash (queue is in-memory) are never rescanned — acceptable (expiry would kill them) but they sit as non-terminal rows.
- QUEUE_FULL + TTL-dedup interplay: after a 503, Chartink's retry within the same 300s bucket returns DUPLICATE — the signal is lost for that bucket by design.
- pending_rr_cancel failure has no notifier/escalation (order_monitor.py:1190-1196) — verified; self-retry via poll is the only mechanism.

### 4.5 Technical debt
- Three near-duplicate pipeline bodies (`_process_one`, `continue_from_gate`, `continue_from_retest`, signal_processor.py:589-2003) — every gate fix must be applied thrice (FIX-165e/F06 history shows drift risk).
- Dead status vocabulary in signals CHECK (schema.sql:60-70) and `trades.PARTIAL` with no writer — harmless, but state-machine docs and code disagree.
- `_PROTOCOL_TO_PRODUCT` fallback "MIS safe default" on unknown protocol (order_placer.py:2160-2170) papers over a data bug rather than failing loud.
- order_placer.py at 4,473 lines and order_reconciler.py at 3,673 are past the maintainability knee; place() alone spans ~840 lines.

### 4.6 Improvement opportunities
- Re-derive SL (and re-size or shrink qty) from the fresh LTP in the FIX-067 path — keeps `_derive_prices` output pair coherent.
- Add a notifier escalation after N consecutive pending_rr cancel failures.
- Move the per-strategy cap query inside the portfolio_lock block.
- SQLite trigger (or guarded UPDATE) forbidding writes to trades rows in terminal states.
- Startup sweep marking stale QUEUED signals EXPIRED for hygiene.

### 4.7 Phase verdict
This is a mature, defense-in-depth trading flow: every hop from webhook to P&L has a named failure mode, a detection path, and (almost always) an automated backstop, with the reconciler as a genuinely comprehensive second truth-source. The 190+ fix history is visible as layered, well-commented guards rather than patch spaghetti — capital accounting in particular (write-ahead ledger, invariant checks, hard_kill on commit failure, evidence-based recovery release) is exemplary for a system this size. Remaining risks are second-order: a stale-SL sizing skew on the momentum fresh-quote path, fail-open best-effort guards during quote outages, and convention-enforced (not schema-enforced) state transitions.

### 4.8 Recommendations
1. **[S] Fix FIX-067 SL anchor**: use both outputs of `_derive_prices(live_ltp,…)` (signal_processor.py:927) or abort when fresh-vs-stale delta exceeds the sl_fraction budget — closes the only place sized risk and placed risk diverge silently.
2. **[S] Escalate pending_rr_cancel failures** (order_monitor.py:1196): WARN→notifier after 3 consecutive failures on the same order.
3. **[S] Move per-strategy cap check inside portfolio_lock** (signal_processor.py:843-853, and gate/retest twins).
4. **[M] Extract a shared pipeline core** for the three `continue_*` bodies so gates cannot drift.
5. **[M] Terminal-state write guard**: add `AND status NOT IN ('CLOSED','CLOSED_MANUAL','FAILED','CANCELLED')` to `update_trade_status`/`record_entry_fill` or a DB trigger.
6. **[M] Alert on webhook insert-failure path** (the 300s dark-window case) — one ERROR→notifier line in `_process_signal`.
7. **[L] Flip `daily_loss_include_unrealized` to enforce** after the shadow soak validates (system_config.yaml:176) — the machinery (B-1) is already live.

### 4.9 Scorecard input
**Reliability(flow): 9/10** — every stage idempotent or atomically guarded, reconciler re-entrancy-safe, restart paths proven in code; deductions for triplicated pipeline bodies and convention-only transition enforcement.
**Trading Safety: 8.5/10** — four-layer naked-position defense, evidence-based capital release, hard_kill on any accounting inconsistency; deductions for the FIX-067 risk-sizing skew, fail-open slippage/drift guards under quote outage, and unrealized-MTM gate still in shadow.

### 4.10 Evidence log
Key citations (all verified at HEAD b4aea6f): ingress auth/dedup/limits `signals/webhook_receiver.py:354-366,410-432,638-668,678-719,823-848`; pipeline gates/reserve/timeout `signals/signal_processor.py:617-733,855-887,960-1029,1153-1178`; throttle `signals/entry_throttle.py:65-115`; control resolver `strategies/control.py:67-116`; screener `screening/secondary_screener.py:108-309,355-415`; risk checks `capital/risk_engine.py:342-598`; capital lifecycle `capital/fund_manager.py:472-636,843-986,1112-1242,1374-1388,1449-1482,1517+`; placement `orders/order_placer.py:757-1595,1921-2357,2656-2957,3595-3719,4404+`; protocol SL-first/clamp `orders/order_protocol_limit.py:263-407`; clamp math `orders/price_math.py:100-124,256-321`; adapter chokepoint `broker/zerodha_adapter.py:470-627,1236-1290,1985-2031`; monitor `broker/order_monitor.py:167-244,583-702,1117-1196`; reconciler `orders/order_reconciler.py:186-230,558-627,1021-1237,2020+,2349-2445,2662-2807,3133+,3221-3457`; EOD `orders/eod_squareoff.py:196-354,1656-1807`; schema/state `core/schema.sql:42-331`; store guards `core/state_store.py:539-577,727-741,873-901,1372-1388,1528-1556`; ids/tag `core/ids.py:104-124`; kill EXITING `capital/kill_switch.py:956-967,1253`; wiring `main.py:2170-2308,2551-2590,2781-2787`; config `config/system_config.yaml:36-55,113-114,169-205,271,345-350`. UNCERTAIN: whether nginx fronts :8080 in production (receiver comment L151 implies possible; per-IP limiter would then bucket by proxy IP) — repo-only phase cannot confirm; `_validate_place_order` internals (qty>0) verified by ZA13 docstring only; `trades.PARTIAL` writer absence based on repo-wide grep, not exhaustive dynamic trace.

## PHASE 5 — STRATEGIES

### 5.1 What exists

**Model.** Strategies are pure config-as-data: 15 YAMLs in `config/strategies/` validated by a Pydantic schema with `extra="forbid"` (strategies/schema.py:43), loaded once at boot by `StrategyLoader` — "Strategies are loaded ONCE; no hot reload" (strategies/loader.py:27). The code package is 592 LOC total (loader 137, control 116, schema 333, `__init__` 6). No per-strategy code, threads, or classes — a strategy is a row of parameters consumed by the shared pipeline.

**Schema fields (strategies/schema.py:35-115)** — 38 fields:

| Group | Fields (defaults) | Validation |
|---|---|---|
| Identity | name, display_name, description (required) | — |
| Product | direction, intent, order_protocol (required enums) | :119-144 |
| Control | enabled=True (LAYER 3, :61) | — |
| Entry | entry_method (req), entry_offset_pct=0.0 | 0≤v<1 :202-207 |
| Stop loss | sl_method (req), sl_pct=0.0, sl_atr_multiplier=1.5, sl_min_pct=0.003, sl_max_pct=0.05, sl_gap_buffer_pct=0.0 | sl_pct>0 iff FIXED_PCT and within min/max :261-277 |
| Target | tgt_method (req), tgt_pct=0.0, tgt_risk_reward=2.0, tgt_atr_multiplier=2.5 | :164-192, :280-284 |
| Smart TGT / trail | smart_tgt_enabled (req), trigger=0.005, step=0.003; trailing_sl_enabled=False + 3 thresholds (:88-91) | :194-200 |
| Pullback wait | pullback_wait_enabled (req), tolerance=0.005, timeout=180s | :230-235 |
| Screening | min_score=0 (0=global), min_volume_surge=1.3, min_adr_pct=0.005, max_spread_pct=0.005 | :209-228 |
| Risk | lot_size=1, max_concurrent_positions=2 (per-strategy max_risk_pct deleted as dead, :105-108) | :237-242 |
| Time | entry_start_time="10:00", entry_end_time="15:00", active_days=Mon-Fri | window format + start<end :286-301 |

**Loader pipeline.** `load_all_strategies` globs `*.yaml` sorted; ANY invalid file raises `ConfigSchemaError` immediately — "no partial loads" (loader.py:44, 63-65) = one bad YAML aborts the whole process at startup. LAYER 0 `force_intraday_only=true` rewrites every non-INTRADAY intent to INTRADAY at load — the single chokepoint guaranteeing MIS-only (loader.py:47-53, 66-72; main.py:2332). S10 map cross-validation is an optional parameter (loader.py:76-77).

**Control layers** (strategies/control.py:9-27): LAYER 0 force_intraday_only breaker (currently **true**, config/system_config.yaml:70); LAYER 1 master `trade_type` (**INTRADAY**, :77); LAYER 2 strategy intent; LAYER 3 `enabled`. Single pure resolver `strategy_will_trade` (control.py:67-116) used by BOTH the entry gate (signals/signal_processor.py:702-715) and status surfaces, with machine-readable causes (control.py:41-44). Boot logs a WILL/WON'T roster (main.py:2335-2358). Because the loader pre-rewrites intent, the 3 DELIVERY strategies pass LAYER 1 and **trade live as forced-intraday**.

**Param matrix (all 15 YAMLs parsed).** Identical across all 15: enabled=true, entry_method=LIMIT, sl_method=FIXED_PCT, tgt_method=RISK_REWARD, **tgt_risk_reward=1.5** (R:R standardization confirmed), tgt_pct=0.0, smart_tgt trigger/step, pullback tolerance/timeout, **min_score=0** (global 60 binds), min_adr=0.005, max_spread=0.005, lot_size=1, **entry 09:25-15:00**, active_days Mon-Fri — ~19 fields duplicated 15×. Varies: direction (9 LONG/6 SHORT), intent (12 INTRADAY/3 DELIVERY), protocol (12 CO_PLUS_TGT/3 LIMIT_TRIPLE), sl_pct (0.008 vwap-pair, 0.01 ×6, 0.012 gap_go-pair, 0.015 first_pullback-pair, 0.02 positional×3), entry_offset/sl_atr/sl_min/sl_max/tgt_atr (positional trio differs as a block), smart_tgt off + pullback off on positional, min_volume_surge (1.2-2.0), max_concurrent_positions (2 ×11 / 3 ×4), sl_gap_buffer_pct=0.3 only on the 4 gap strategies. The 6 long/short twins are perfectly symmetric (no copy-paste outliers found); the 3 positional YAMLs differ from each other only in min_volume_surge.

**Signal path.** Chartink POSTs `/webhook/<scanner_name>?token=` (webhook_receiver.py:268-270, single shared secret :410-432) → WR4 scanner must be in scan_webhook_map else 404 (:434-437) → queue → pipeline maps scanner→strategy→StrategyConfig (signal_processor.py:676-696) → control gate (:702-715) → per-strategy window (:717-725) → governor (:728-731) → screener (per-strategy min_score override, screening/secondary_screener.py:251-255; global min_pass_score=60, config/scoring_weights.yaml) → price derivation from sl_pct/offset/R:R (signal_processor.py:1188-1335, 1341+) → sizing (global risk %, tier × perf_weight, capital/position_sizer.py:419).

### 5.2 What works well

- **Schema rigor.** `extra="forbid"` + enum, range, and cross-field validators (sl within min/max bounds, window format/order) make a typo'd or out-of-band YAML a hard startup failure, not silent drift (schema.py:43, 258-303). Fail-fast matches the Foundation Rules.
- **Single-authority control resolver.** One pure function answers "will it trade" for gate, boot log, and status table — gate and display cannot disagree (control.py:2-8; signal_processor.py:698-701; main.py:2335-2354). Cause codes avoid string-matching (control.py:35-44).
- **LAYER 0 chokepoint.** force_intraday_only rewriting intent at load is a genuinely single point that makes CNC impossible downstream (loader.py:47-53), with a defensive resolver branch locked under test (control.py:15-17, 86-96).
- **Multiple independent validation layers.** Boot startup check (utils/startup_checks.py:1205-1232), preflight CRITICAL check (scripts/preflight/checks/config_integrity.py:146-158), and the loader itself all validate YAMLs; scanner reachability is preflighted (utils/startup_checks.py:652-745).
- **Per-strategy runtime brakes exist.** Governor pauses a strategy for the day when daily loss < −2× its 10-day avg losing day, pre-noon, with WARNING Telegram (capital/strategy_governor.py:49-87, 131-162; config/system_config.yaml:422-426); per-strategy max_concurrent_positions enforced in the pipeline (signal_processor.py:843).
- **Honest reject taxonomy.** Every pipeline reject persists as `REJECTED_<check>` on the signal row (signal_processor.py:1116), so YAML-side desync (`UNKNOWN_STRATEGY`) is at least visible in the Signals truth layer of the daily report.
- **Slippage override typo-catch.** `by_strategy` override keys that match no known strategy are WARNed at startup (core/config_loader.py:953-975) — the kind of cross-check S10 should have had.

### 5.3 Risks

- **[HIGH] Chartink-side rename/desync is silent.** A scanner renamed on Chartink (or a map key edit) makes the webhook return 404 at WR4 with **no log line, no metric, no persistence** (webhook_receiver.py:434-437 — compare the malformed-JSON branch which logs, :462-465). `signals_received` never increments, and the only detector is the aggregate preflight `SignalsArrivedCheck`, which WARNs only if the *entire* day is silent (scripts/preflight/checks/signals.py:36-54). Failure scenario: one of 15 scanners renamed → that strategy silently stops trading for days; nothing internal notices. This is the sharpest edge of the claimed "de-synced rename fails at RUNTIME".
- **[HIGH] Auto-demotion is a dead safety net that claims otherwise.** `compute_strategy_metrics` docstring promises "auto-sets strategy to PAPER_ONLY" (scripts/compute_strategy_metrics.py:8); `_check_demotion` only logs CRITICAL and formats an alert saying "Strategy set to PAPER_ONLY" (:218-227) — **no state is ever written** (grep: PAPER_ONLY appears nowhere else in prod code), the return value is discarded (:289), and the cron path passes `notifier=None` (:318-326) so the Telegram alert can never send. The CRITICAL lands in `logs/cron-strategy-metrics.log` (config/cron_registry.yaml:455-468); alert_watcher only ships `.flag` sentinels, not logs (scripts/alert_watcher.py:5-9). UNCERTAIN whether the nightly Gemini log review would surface it. Scenario: a strategy bleeds Sharpe < −0.3 for weeks; the designed brake never engages and the operator believes it exists.
- **[MED] S10 startup cross-check confirmed dead in production.** Every production caller omits `scan_webhook_map_path`: main.py:2330-2333, utils/startup_checks.py:1221-1222, scripts/preflight/checks/config_integrity.py:155 & 173, config_sanity.py:37, ops/control_tower/aggregator.py:158; only tests pass it (tests/unit/test_strategies.py:355, 382). A map entry referencing a renamed/deleted strategy therefore surfaces only per-signal as `REJECTED_UNKNOWN_STRATEGY` (signal_processor.py:691-696) instead of aborting boot. Note S10 is also one-directional even when wired (map→YAML only, loader.py:98).
- **[MED] No hot per-strategy kill.** `enabled` is read once at load (loader.py:27); `trade_type`/`force_intraday_only` are constructor-bound (main.py:2597-2599). Disabling one misbehaving strategy mid-session requires a full process restart (in_flight state is memory-only per ops notes) or the global kill switch — a blunt instrument during live hours. GUI controls are deliberately read-only (G0 report, docs/gui_project/G0_BACKEND_INVESTIGATION_REPORT.md:84).
- **[MED] 3 positional strategies run live with delivery-calibrated params.** force_intraday_only converts positional_momentum/sector_rotation/swing to intraday (loader.py:66-72) with sl_pct=0.02, sl_max=0.08, smart-TGT off, pullback off, and a 1.5R target on a 2% SL — a 3% intraday move squared off at 15:17. Intentional pending Slice 2.5, but these params were designed for multi-day holds; their intraday expectancy is untested by design.
- **[MED] Cross-strategy contention on shared budgets.** Global `max_daily_trades: 10` (system_config.yaml:170) vs 15 strategies × concurrent caps summing to 34; the shared intraday capital bucket is 70% (system_config.yaml:117-118). One prolific scanner morning can exhaust the day's 10-entry budget and starve later strategies (entry throttle mitigates bursts, not budget consumption). No per-strategy daily-entry cap exists.
- **[LOW] Governor cold-start and restart blind spots.** No losing history → `avg_loss >= 0` → never pauses (strategy_governor.py:74-75): a brand-new bad strategy is ungoverned its first days. Pause set is in-memory; a midday restart un-pauses (:9, :42). Fails open on DB error (:70-72). Uses gross_pnl (:95), so charges are invisible to the brake.
- **[LOW] Shared webhook token across all 15 scanner URLs.** Single `self._secret` (webhook_receiver.py:410-432); rotation = manually editing 15 Chartink URLs (03-Jul rotation memory). Leak of any one URL exposes all scanners; at 50 strategies rotation churn scales linearly.

### 5.4 Gaps

- **No per-scanner/per-strategy silence detection.** Nothing computes "scanner X: last signal N days ago" — preflight is aggregate-only (preflight/checks/signals.py:36-54); cron officer's NO_SIGNAL tracks cron jobs, not scanners. Distinguishing "scanner silent" from "strategy disabled" is manual.
- **No automated performance feedback into sizing.** The loop is built but open: `perf_weights` parameter exists (signal_processor.py:145, 181) and the sizer applies it (position_sizer.py:419), but the production constructor never passes it (main.py:2551-2600) → every strategy sizes at perf_weight 1.0 (:813). strategy_metrics (core/schema.sql:865-882, EOD 16:15 cron) feeds only human surfaces: Gemini pre-market brief (gemini_premarket_brief.py:113-114), daily report T5 trailing-20-session ranking (reports/daily_trade_review.py:1368-1371, 1390, 1420 — T5 deliberately recomputes from trades, not strategy_metrics), GUI period ranking (ops_dashboard/backend/services/analytics_period.py:100-101). All tuning is manual.
- **No cross-check between chartink_scanners.yaml and scan_webhook_map.yaml.** The reachability check iterates the map and uses chartink_scanners only as URL fallback (startup_checks.py:663-665); orphan/divergent entries in chartink_scanners.yaml are never flagged. chartink_scanners.yaml:12-13 still says "URLs are placeholders" — UNCERTAIN (repo-only) whether the committed URLs are the real live ones.
- **No per-strategy absolute loss cap (Rs).** The governor is history-relative only; risk_engine/fund_manager daily-loss gates are account-level.
- **ATR SL/TGT methods are schema-valid but unimplemented** — runtime falls back to FIXED_PCT with a warning, or HALTs per `atr_fallback_mode` (signal_processor.py:1242-1251). No YAML uses ATR today; a future YAML author could reasonably assume it works.

### 5.5 Technical debt

- **~19 fields duplicated across 15 YAMLs** (see 5.1 matrix): min_score, adr/spread, smart-TGT/pullback constants, windows, active_days restate schema defaults or global values 15×. Any global change (e.g., R:R 1.5→1.6) is a 15-file edit (the 24-Jun R:R standardization was exactly that).
- **Dead/dormant knobs riding along:** `sl_gap_buffer_pct=0.3` on 4 gap YAMLs can never fire — buffer applies only 09:15-09:30 (signal_processor.py:1185-1186, 1318-1322) while the global entry window opens 10:00 (system_config.yaml:28; enforced webhook_receiver.py:453-456 and market_windows.py:166 before the per-strategy check). `trailing_sl_*` (schema.py:88-91) absent from all 15 YAMLs → False everywhere. Per-strategy `min_score` override (secondary_screener.py:251-255) unused (all 0). Per-strategy windows are a no-op (all 09:25-15:00; global 10:00 binds — Phase 2 finding confirmed).
- **Unit inconsistency:** `sl_gap_buffer_pct` is percent (÷100 at signal_processor.py:1323) while every other `*_pct` strategy field is a fraction — a 0.3 vs 0.003 trap for future authors.
- **Stale comments:** "gap_fade_long cuts off at 11:30" (signal_processor.py:717-719) — actual YAML says 15:00; "URLs are placeholders" (chartink_scanners.yaml:12-13); alert text claiming an action that doesn't happen (compute_strategy_metrics.py:224).
- **Triplicated pipeline enforcement:** max_concurrent/governor/perf_weight checks appear at three parallel sites (signal_processor.py:843/1541/1854, :729/:1491/:1808, :813/:1518/:1838) — a per-strategy rule change must be applied 3×.
- **Market opinion hardcoded in preflight:** `LongStrategiesEnabledCheck` permanently WARNs that 9 LONG strategies exist (config_integrity.py:161-182) — manual-feedback vestige that will nag forever until code-edited.

### 5.6 Improvement opportunities

1. Wire S10 into main.py + extend it bidirectionally (YAML without scanner = WARN; map/chartink key-set diff = FAIL) — one argument plus ~20 lines.
2. Per-scanner last-signal-age table (webhook already knows scanner_name at :434) surfaced in cron officer / daily report; alert at N trading days silent per scanner.
3. Log + count the WR4 404 path; add `unknown_scanner_rejects` to /metrics.
4. Make demotion honest: either persist a `demoted` flag consumed by `strategy_will_trade` (fits LAYER 3 cleanly) or delete the claim and wire the notifier.
5. Populate `perf_weights` at boot from strategy_metrics (clamped, e.g. 0.5-1.5) — the entire plumbing already exists end-to-end.
6. Hot per-strategy disable: a small DB/state flag checked in `strategy_will_trade` would give the GUI a safe write path later (G3 wall acknowledged).
7. Defaults overlay: let YAMLs declare only deltas from a `_defaults.yaml`; schema already has defaults for most fields.
8. Per-scanner tokens or the planned HMAC (Ph3) to cut shared-secret blast radius and rotation churn.

### 5.7 Phase verdict

The config-as-data core is genuinely strong: a 592-LOC package with a rigorously validated schema, a single-chokepoint product breaker, and a provably consistent 3-layer control resolver — hard to misconfigure silently on the YAML side. Its weaknesses are at the edges: the scanner↔strategy seam is protected by a check that never runs (S10) and fails silent on the Chartink side, the advertised auto-demotion brake is dead code in three independent ways, and the performance feedback loop is fully plumbed but never connected — consistent with the repo-wide "dead safety nets" theme. Scaling to ~50 strategies is architecturally fine (O(1) lookups, trivial load) but operationally gated by manual Chartink URL management, a global 10-trade/day budget, restart-bound controls, and copy-paste YAML growth.

### 5.8 Recommendations (prioritized)

1. **[S] Pass `scan_webhook_map_path` in main.py:2330** (and preflight) so S10 runs; add reverse + chartink_scanners key-set checks.
2. **[S] Log/metric the unknown-scanner 404** (webhook_receiver.py:436) — precondition for any silence detection.
3. **[M] Per-scanner silence monitor** (last-signal timestamp per scanner; alert after N trading days).
4. **[S] Fix the demotion lie:** wire notifier (or alerts/critical.py flag) and reword until enforcement exists; **[M]** then enforce via a persisted LAYER-3 flag.
5. **[M] Close the sizing loop:** build perf_weights from strategy_metrics at boot with clamps; keep tier system as-is.
6. **[M] Hot per-strategy disable** consumed by `strategy_will_trade` per-signal.
7. **[S] Sweep dead knobs:** remove sl_gap_buffer values (or move entry_start earlier deliberately), delete trailing_sl block or implement, document ATR as unimplemented in schema comments.
8. **[M] YAML defaults overlay** to stop 15-file global edits before strategy #16 lands.
9. **[L] Revisit positional trio** params for their actual (forced-intraday) regime or disable until Slice 2.5.

### 5.9 Scorecard input

- **Strategy architecture 8/10** — disciplined schema/loader/resolver trio with real fail-fast and a single product chokepoint; docked for the dead S10/demotion nets and restart-bound controls.
- **Strategy scalability 5/10** — O(1) hot path and trivial config cost, but manual 15-URL Chartink surface with one shared token, global 10-trade budget, no silence detection, restart-required strategy control, and 15-way param duplication make 50 strategies an ops hazard today.

### 5.10 Evidence log

- strategies/schema.py:35-115 (fields/defaults), :43 (forbid), :119-254 (field validators), :258-303 (cross-field), :308-333 (validate_strategy)
- strategies/loader.py:27 (no hot reload), :44+63-65 (any bad YAML aborts), :47-53+66-72 (LAYER 0 rewrite), :76-77+93-137 (S10, one-directional)
- strategies/control.py:9-27 (layers), :41-44 (causes), :67-116 (resolver)
- main.py:2330-2333 (loader call, **no map path**), :2335-2358 (boot roster), :2551-2600 (SignalProcessor ctor — **no perf_weights**), :2597-2599 (trade_type ctor-bound)
- signals/webhook_receiver.py:268-270 (route), :410-432 (single shared secret), :434-437 (WR4 404, unlogged), :453-456 (global window 403)
- signals/signal_processor.py:676-696 (mapping miss → UNKNOWN_STRATEGY), :702-715 (control gate), :717-725 (per-strategy window + stale comment), :728-731 (governor), :843/1541/1854 (per-strategy cap ×3), :813/1518/1838 (perf_weight default 1.0), :1116 (reject persisted), :1184-1335 (price derivation; :1323 ÷100 unit quirk), :1242-1251 (ATR fallback)
- capital/strategy_governor.py:9+42 (in-memory), :49-87 (check; :64 noon cutoff; :70-72 fail-open; :74-75 cold-start), :91-127 (gross_pnl SQL), :131-162 (WARNING alert)
- capital/position_sizer.py:174, 419, 425-443 (perf_weight application)
- scripts/compute_strategy_metrics.py:8 (claim), :176-231 (_check_demotion — log+alert only), :224 (false alert text), :273-289 (insert; demotion result discarded), :318-326 (notifier=None)
- core/schema.sql:865-882 (strategy_metrics DDL — no status column)
- config/system_config.yaml:28+36 (entry 10:00-15:00), :70 (force_intraday_only true), :77 (trade_type INTRADAY), :83 (delivery_enabled false), :117-118 (70/30 buckets), :151-153 (tier multipliers), :166-172 (max_daily_trades 10 + delivery caps), :422-426 (circuit breaker), :53-55+197 (queue 300 / workers 5)
- config/scoring_weights.yaml (10 steps, min_pass_score 60, tiers 80/65); screening/secondary_screener.py:251-255 (min_score override); screening/quality_scorer.py:119
- config/chartink_scanners.yaml:12-13 (placeholder note), :15-29; config/scan_webhook_map.yaml:16-75 (15 1:1 entries)
- 15 YAMLs parsed (matrix in 5.1); e.g. config/strategies/vwap_bounce_long.yaml:43 (09:25), gap_fade_long.yaml:22 (sl_gap_buffer 0.3)
- utils/startup_checks.py:652-745 (sequential reachability, chartink fallback :663-665), :1205-1232 (boot YAML check, no map path)
- scripts/preflight/checks/config_integrity.py:146-182; scripts/preflight/checks/signals.py:36-54 (aggregate-only silence WARN)
- core/config_loader.py:953-975 (slippage by_strategy override + typo-catch); core/market_windows.py:143-169 (global-then-strategy window)
- reports/daily_trade_review.py:1368-1371, 1390, 1420-1422 (T5 trailing ranking from trades); scripts/gemini_premarket_brief.py:113-114; ops_dashboard/backend/services/analytics_period.py:100-101; config/cron_registry.yaml:455-468 (16:15 cron, log target)
- tests/unit/test_strategies.py:355+382 (only S10 callers); scripts/alert_watcher.py:5-9 (sentinel-only, no log tailing)
- UNCERTAIN: live Chartink URL reality (placeholder comment vs 15/15-day C-2 traffic); Gemini nightly log review catching the demotion CRITICAL; werkzeug access-log capture of WR4 404s.

## PHASE 6 — RISK & CAPITAL

### 6.1 What exists (control inventory)

| # | Control | Key (value) | Enforcement point | Stage |
|---|---------|-------------|-------------------|-------|
| 1 | Kill-switch gate | `kill_switch` injected | `risk_engine._run_checks` check 1 (risk_engine.py:342-346); re-checked in signal_processor (signal_processor.py:627 et al.) + order_placer last-mile (order_placer.py:932) | pre-trade |
| 2 | Sizing validity | — | check 2 (risk_engine.py:349-354) | pre-trade |
| 3 | Bucket capital | `capital.intraday/positional_bucket_pct` (0.70/0.30, system_config.yaml:117-118) | check 3 vs `margin_required` (risk_engine.py:356-368); re-enforced with +5% SLM buffer in `reserve()` (fund_manager.py:508-527) | pre-trade |
| 4 | Max open positions | `risk.max_open_positions` (5, yaml:169) | check 4, `> max` after +1 candidate (risk_engine.py:370-439) | pre-trade |
| 5 | Max daily trades | `risk.max_daily_trades` (10, yaml:170) | check 5, `>= max` (risk_engine.py:464-488) | pre-trade |
| 6 | Delivery caps (dormant) | `risk.max_open/daily_delivery_*` (3/5, yaml:171-172) | positional branch of checks 4/5 (risk_engine.py:385-391, 469-475); inert while `force_intraday_only: true` (yaml:70) | pre-trade |
| 7 | Consecutive losses | `risk.max_consecutive_losses` (5, yaml:174) | check 6 (risk_engine.py:490-499); day-scoped FIX-183 (risk_engine.py:242-245; state_store.py:765-803) | pre-trade |
| 8 | Daily loss — pre-trade gate | `risk.daily_loss_limit_pct` (0.03, yaml:175); limit = pct × `snap.total` | check 7 (risk_engine.py:501-545); pnl source = `fm_ledger` via `get_daily_realized_net_pnl` (fund_manager.py:1390-1410; state_store.py:2346-2365) | pre-trade |
| 9 | Daily loss — post-close breach | SAME key, wired main.py:1976; fires at `daily_pnl <= -pct*total` | `release_used` after every close (fund_manager.py:1209-1229) → callback: alert → `EodSquareoff.fire_now` flatten → SOFT_KILL (main.py:648-709) | post-close |
| 10 | B-1 unrealized MTM (SHADOW) | `risk.daily_loss_include_unrealized` (false, yaml:176) | gate term risk_engine.py:512-537; refresh `order_reconciler._refresh_unrealized_mtm` each 15s cycle (order_reconciler.py:974-980, 2811-2881; cadence yaml:271); freshness 45s (fund_manager.py:1446-1469) | pre-trade (advisory) |
| 11 | Sector exposure | `risk.max_sector_exposure_pct` (0.40, yaml:173) | check 8, projected margin vs pct × total (risk_engine.py:547-558); sector from instruments.csv via `instrument_cache.sector` (instrument_cache.py:257-268, main.py:2092); exposure = SUM(`trades.margin_reserved`) active statuses (state_store.py:709-725) | pre-trade |
| 12 | Contrary position / wash | — | check 9 (risk_engine.py:560-581) | pre-trade |
| 13 | Duplicate symbol | — | check 10 (risk_engine.py:583-589; state_store.py:727-742) | pre-trade |
| 14 | Risk per trade | `position_sizing.risk_per_trade_pct` (0.01, yaml:139) | `qty_by_risk = floor(total*pct / sl_distance)` (position_sizer.py:333-334) | sizing |
| 15 | Concentration | `position_sizing.max_concentration_pct` (0.10, yaml:140) | `qty_by_concentration = floor(total*pct / entry_price)` — NOTIONAL, total-relative (position_sizer.py:375-377) | sizing |
| 16 | Position value cap | `max_position_value_pct` (0.40, yaml:150) | REJECT if `qty*price > pct*total` (position_sizer.py:476-506) | sizing |
| 17 | Leverage | `capital.leverage_map` (INTRADAY 5.0, yaml:127-131) | margin = qty*price/leverage (fund_manager.py:241-261); sizing margin_per_share (position_sizer.py:270-300, 369-372; FIX-072 live-margin override w/ static fallback) | sizing+reserve |
| 18 | Qty guards | `min_tick_size` 0.05, `max_single_order_qty` 10000, lot-skew 0.25 (yaml:142-144) | position_sizer.py:306-364, 450-467 | sizing |
| 19 | 70/30 buckets, no-borrow | `conditional_allocation_enabled` false (yaml:119) | `resolve_bucket_allocation` (fund_manager.py:111-145); `reserve()` consults only intent's bucket (fund_manager.py:507-527) | reserve |
| 20 | Per-strategy circuit breaker | `strategy_circuit_breaker` (2.0×, cutoff 12:00, lookback 10; yaml:422-426) | `StrategyGovernor.check` in signal_processor gates (strategy_governor.py:50-87; signal_processor.py:728-729, 1490-1491); fail-open | pre-trade |
| 21 | Entry throttle | `min_gap_between_entries_sec` 20 / burst 3-per-60s / symbol cooldown 300 (yaml:202-205) | signal_processor (FIX-190 Bug G) | pre-trade |
| 22 | Three-balance invariant | tolerance Rs 1.0 (fund_manager.py:148-156) | every mutation `_check_invariant` (fund_manager.py:2048-2117; invariant.py:113-205); violation → hard_kill outside lock (fund_manager.py:2119-2159) | every mutation |
| 23 | Capital drift escalation | `drift_handler` 250/1000/2500 Rs, 3 cycles (yaml:416-420) | `CapitalDriftHandler` tiers NOISE/LOG/SOFT/HARD; escalating sources only (drift_handler.py:65-69, 126-245) | post-hoc |
| 24 | Broker-vs-local drift (G3) | `capital_drift_tolerance` 50 / in-session 10% / human +5000 / re-alert 1800s (yaml:272-275) | `_g3_capital_drift` per cycle: alert-only, source `order_reconciler` = non-escalating (order_reconciler.py:2883-3015) | post-hoc |
| 25 | FM-vs-ledger drift (CHECK7) | same tolerance | `_check7_capital_accounting_drift` → source `fund_manager_self_check` = ESCALATING (order_reconciler.py:3046-3129) | post-hoc |
| 26 | API-failure auto-trip | `kill_switch.api_failure_threshold` 3 (yaml:208) | SOFT_KILL from INACTIVE only; whitelist Timeout/RateLimit (kill_switch.py:588-663) | post-hoc |
| 27 | EOD squareoff | `eod_squareoff_time` 15:17 (yaml:38) | `check_and_fire` once/day + `fire_now` (eod_squareoff.py:196-260); LIMIT_THEN_MARKET, 1%, 120s grace (yaml:215-217) | backstop |

### 6.2 What works well
- **Single daily-loss authority, verified**: BUILD 1 deleted the absolute `capital.daily_loss_limit`; BOTH mechanisms now derive Rs from `risk.daily_loss_limit_pct` × capital (yaml:120-122; risk_engine wiring main.py:2088; fund_manager wiring main.py:1973-1976) and BOTH read the same `fm_ledger`-based `get_daily_realized_net_pnl` (fund_manager.py:1218, 1398). Semantics differ only by role: gate rejects new entries at `abs(pnl) >= limit` when negative (risk_engine.py:539); breach fires flatten+SOFT_KILL at `pnl <= -limit` (fund_manager.py:1220) — identical boundary. *(Note: the operational memory claim "different config keys" is STALE; keys were unified 24-Jun.)*
- **Breach sequence correctly ordered**: CRITICAL alert → `fire_now` (cancel pending + flatten) → SOFT_KILL, and the SOFT_KILL lands before the capital lock releases, so no approve() can slip through the post-fire resume window (main.py:666-707; eod_squareoff.py:493-499; fund_manager.py:1154).
- **B-1 freshness discipline**: MTM used only when fresh (≤45s = 3× cadence); quote outage marks unavailable immediately; degraded mode is realized-only + WARN, never block-all, never fabricated; set-based prune kills stale entries closed by any path (fund_manager.py:1446-1482; order_reconciler.py:2811-2881; risk_engine.py:516-537). Shadow logging (`would_reject_with_unrealized`) gives evidence before the flag flip (risk_engine.py:528-537).
- **Kill-switch mechanics**: persist-first atomicity (KS9, kill_switch.py:474, 524), no HARD→SOFT downgrade (462-468), idempotent hard_kill re-runs cancellation (536-540), auto-trip only from INACTIVE with the FIX-191 connectivity whitelist that killed the false-SOFT_KILL class (613-647), and the IP-403 actionable alert instead of a wrong halt (670-711).
- **HARD_KILL flatten is genuinely defensive**: cancel resting SL/TGT first (971-1022), reverse-aware close from BROKER truth so a flat position is never re-fired (1099-1108), marketable LIMIT LTP±1% with MARKET fallback (906-923), orphan broker-position sweep (1147-1183), bounded 2h retry with CRITICAL escalation (1185-1207), EXITING-first marking so CHECK1/G5b treat it as ours mid-exit and never re-arm (955-969; order_reconciler.py:849-855), with the 30-min stuck-EXITING resolver as cleanup (order_reconciler.py:1188-1291).
- **Emergency close paths are oversell-proof**: CHECK9 FACET-1 confirms genuinely-naked against broker truth before acting (order_reconciler.py:2100-2107) and FACET-2 sells only `min(tracked, live_held)`, skipping when flat/unconfirmable (2277-2300); system-oversell detection requires the full unambiguous signature before auto-flatten (1549-1594).
- **Capital integrity engineering**: write-ahead fm_ledger before every in-memory mutation (FM10/BL-5, fund_manager.py:2163-2214), per-bucket + global invariant on every mutation with deferred hard_kill outside the lock (C.1, 2048-2159), BL-4 commit hard_kill, crash-aware A-1/E-1 reserve reconstruction with exactly-once COMMIT guard (998-1110, 1909-1990), and HARD_KILL+fill → EXITING+flatten in recovery (order_reconciler.py:3538-3575).

### 6.3 Risks
- **[HIGH] Unrealized losses do not gate anything today.** B-1 is SHADOW (`daily_loss_include_unrealized: false`, yaml:176) and B-2 (periodic unrealized soft-kill) is design-only. Scenario: 5 open positions gap adversely; realized pnl = 0, so check 7 passes and the breach never fires until closes land; only per-position SLs and the 15:17 squareoff bound the loss. With SL-fraction risk 1%/trade this is bounded in the normal case, but a gap through SLs is unbounded by any daily-loss control until realization. Known and instrumented (shadow logs) — the flag flip is the control.
- **[MED] The daily-loss breach callback executes the entire flatten while holding `FundManager._lock`**: `_on_loss_breach()` is invoked inside `release_used`'s lock (fund_manager.py:1154, 1228-1229) and runs `fire_now` → broker I/O + `time.sleep(2)` two-pass (eod_squareoff.py:380), 500ms inter-order sleeps (1191-1192, 1300-1301) and up to `limit_grace_sec` 120s (1303-1305, yaml:217). All capital ops serialize behind a multi-minute critical section at the worst moment; squareoff fills queued behind the lock post their RELEASE_USED rows AFTER the step-7 RESET_PNL, leaving the day's ledger sum non-zero post-reset. Liveness/accounting-hygiene risk, not corruption (RLock, single writer).
- **[MED] Overnight auto-clear includes HARD_KILL**: `clear_stale_state` wipes ANY prior-day kill at boot, explicitly including emergency/loss-limit/HARD (kill_switch.py:199-248; called main.py:1535). A capital-corruption HARD_KILL at 15:25 self-clears at the next 08:15 boot. Backstops exist — KILL_AUTO_CLEARED audit row (327-362), rehydrate raising `CapitalStateInconsistent` on replay corruption (fund_manager.py:1612-1627), and "startup reconciliation re-triggers within seconds" (main.py:1532-1534) — but only for re-detectable conditions. Deliberate operator decision (20-Jun); CRO flag, not a defect.
- **[MED] W10 confirmed unfixed at HEAD**: `get_daily_realized_net_pnl` = `SUM(pnl_delta) - SUM(costs)` (state_store.py:2361) while the writer stores `pnl_delta` already NET (`pnl = gross - costs`, fund_manager.py:1167, 1190-1191) → costs subtracted twice; the docstring (state_store.py:2350) wrongly claims pnl_delta is gross. Callers: pre-trade gate via snapshot (fund_manager.py:1398 → risk_engine.py:511), post-close breach (fund_manager.py:1218), RESET_PNL (1494). Direction SAFE-conservative (loss overstated by ~Rs/day of charges → earlier trips), but magnitude scales with volume and it corrupts the RESET_PNL row value. CHECK1/RMS closes pass `costs=0.0` (order_reconciler.py:1102, 1111-1113) so those rows are exempt but slightly overstate net.
- **[LOW] Intraday broker-truth is alert-only**: `sync_from_broker` runs ONLY at 09:15 (main.py:898-910; startup uses `initialize` at main.py:1987-1991 — live = `get_margins().net`). Between syncs, G3 compares broker net vs FM total every cycle but publishes source `order_reconciler` = non-escalating (drift_handler.py:65-69), so it alerts (30-min throttle, yaml:275) without killing; only CHECK7 fm-vs-ledger drift escalates. A sustained genuine broker divergence relies on the operator reading the CRITICAL alert.
- **[LOW] Post-breach budget reset**: breach-triggered `fire_now` runs `reset_daily_pnl` mid-day (eod_squareoff.py:487), zeroing the day's realized loss. The surviving SOFT_KILL (`daily_loss_limit_breached`, non-scheduled → not auto-cleared same-day, kill_switch.py:89-97, 273-279) is the ONLY thing blocking re-entry; an operator `clear_kill_switch.py` + restart grants a fresh 3% budget the same day.
- **[LOW] Basis inconsistency**: sector cap is MARGIN-based (SUM margin_reserved vs 40% of total, state_store.py:718; risk_engine.py:550-551) while concentration/position-value caps are NOTIONAL (position_sizer.py:375-377, 476-477). At 5× leverage, 40% margin = up to 200% of capital in sector NOTIONAL. Unknown sectors pool into one "UNKNOWN" 40% bucket (conservative, but if instruments.csv sector coverage is thin the cap degenerates into a pseudo-portfolio cap).
- **[LOW] Policy numbers for operator judgment** (no prescription): capital ~Rs 10k → daily loss limit ~Rs 300 (3%); risk/trade Rs 100 (1%) → 3 clean SL hits ≈ limit; loss-streak cap 5 can only bind before the loss cap on partial losses; streak resets overnight BY DESIGN (FIX-183 deadlock rationale, risk_engine.py:242-245); 5 positions × 10% notional = 50% max deployed notional; 10 trades/day × Rs 100 = Rs 1000 theoretical risk vs Rs 300 gate (gate closes entries after −300 realized only).

### 6.4 Gaps
- No unrealized-loss enforcement anywhere (B-1 shadow; B-2 designed 03-Jul, unbuilt); the post-close breach is realized-only by construction (fund_manager.py:1218).
- `eod_squareoff.auto_resume_kill_switch` (yaml:214; config_loader.py:403) is a **DEAD KNOB**: never passed to `EodSquareoff` (ctor main.py:2424-2443); resume-if-we-set-it is unconditional (eod_squareoff.py:493-499). Matches the "dead safety nets" theme.
- **No remote/API kill trigger**: the GUI is display-only; in-process state is the sole authority (DB row read only at construction, kill_switch.py:181-197, 738-781), so `clear_kill_switch.py` and `system_manager.trigger_soft_kill` (scripts/system_manager.py:904-918) write a DB row the running process never sees — effective only across a restart; the system_manager EOD soft-kill is then auto-cleared by next-boot `clear_stale_state` anyway (advisory only).
- `scripts/reconcile_pnl.py:276` soft_kill on VARIANCE_MAJOR still the live EOD pnl-variance path (P1 v42 replacement built but not deployed).
- **Trigger-site inventory (complete, per grep)**: SOFT — auto-trip (kill_switch.py:657), drift SOFT/ESCALATED (drift_handler.py:236), daily-loss breach (main.py:703), critical-failure cb (main.py:573), clock skew (main.py:270), force-close 15:15 (main.py:602), token expiry (main.py:2455), live_feed ×3 (live_feed.py:303,474,648), reconciler auth ×3-cycles (order_reconciler.py:651), CHECK9 naked (order_reconciler.py:2149), EOD (eod_squareoff.py:339), GTT ambiguity (cnc_gtt_monitor.py:583), system_manager EOD (scripts/system_manager.py:914), reconcile_pnl (scripts/reconcile_pnl.py:276). HARD — invariant violation (fund_manager.py:2138), BL-4 commit failure (fund_manager.py:971), drift HARD (drift_handler.py:231), API-failure circuit breaker (main.py:628), persist-after-broker-success (order_placer.py:1377), exits-failed-unprotected incl. SLUnplaceable routing (order_placer.py:3565-3593, 3168-3173; order_protocol_limit.py:334). Clear — manual `resume` only within-day (kill_switch.py:553-586; script scripts/clear_kill_switch.py, HARD needs `--force`), scheduled same-day auto-clear (250-313), everything at day rollover boot (199-248).

### 6.5 Technical debt
- W10 (above) + contradictory docstring; RESET_PNL persists the double-counted value (fund_manager.py:1494-1508).
- **Emergency-exit buffer defined twice**: config `capital.emergency_exit_buffer_pct` wired to kill_switch/order_placer (main.py:1525, 2258) vs hardcoded `EMERGENCY_EXIT_BUFFER_PCT = 0.01` used by reconciler flattens (orders/price_math.py:124; order_reconciler.py:84, 1751-1752) — a config change silently desyncs paths (both 0.01 today).
- `SCHEDULED_KILL_REASONS` is exact-string matching including a time literal ("circuit_breaker_force_close_15:15", kill_switch.py:89-92) — brittle if `force_close_time` (yaml:430) changes.
- Legacy FIX-035 MTM methods (`get_total_unrealized_mtm`, per-close `remove_unrealized_mtm`) coexist with the B-1 set-based path (fund_manager.py:1412-1443) — two APIs, one consumer.
- `StrategyGovernor` measures gross_pnl via `updated_at LIKE` date prefix (strategy_governor.py:91-119) — approximate keys; fail-open is documented but silent beyond an error log.

### 6.6 Improvement opportunities
- Flip B-1 to enforce after shadow validation, and build B-2 with the W10 fix bundled (already the plan of record).
- Move the breach callback out of the capital lock (capture-and-defer exactly like the C.1 invariant pattern already used three lines away).
- Consider excluding HARD_KILL from `clear_stale_state` (require `--force` next morning) or gate the clear on a clean rehydrate + zero broker positions; the audit row alone is detective, not preventive.
- Wire or delete `auto_resume_kill_switch`; single-source the emergency buffer constant.
- Escalate G3 broker-drift after N consecutive breaching cycles (mirror DH4) instead of alert-forever.
- Document (or align) the margin-vs-notional basis split between SECTOR_EXPOSURE and concentration caps.

### 6.7 Phase verdict
The pre-trade policy layer is mathematically sound, snapshot-consistent, and unusually well-hardened (TOCTOU-proof caps, freshness-gated MTM, evidence-based recovery capital); the kill/emergency layer is defense-in-depth done properly, with broker-truth checks before every destructive action. The two material weaknesses are policy-level, both known: no unrealized-loss enforcement (B-1 shadow/B-2 unbuilt) and the headless overnight auto-clear of even HARD_KILL, which trades safety-conservatism for autonomy. W10 remains a live correctness bug in the exact number both loss controls read — currently fail-safe in direction, but it distorts thresholds and reporting and grows with volume.

### 6.8 Recommendations (prioritized)
1. **[S] Fix W10**: change state_store.py:2361 to `SUM(pnl_delta)` (writer is already NET) + correct the docstring; regression-test against `trades.net_pnl` per day.
2. **[M] Flip `daily_loss_include_unrealized`** after the shadow soak; build B-2 periodic unrealized soft-kill.
3. **[S] Defer `_on_loss_breach` invocation to after lock release** in `release_used` (existing C.1 pattern).
4. **[S] Wire-or-delete `auto_resume_kill_switch`**; unify emergency-buffer constant to the config value.
5. **[M] Add a HARD_KILL exception** (or positions+rehydrate precondition) to `clear_stale_state`; keep SOFT auto-clear as-is.
6. **[S] Make G3 broker-drift escalate on persistence** (N cycles) rather than alert-only.
7. **[M] Operator review of documented policy numbers** (6.3 last bullet): 3-SL-hits==daily-limit coupling and the margin-basis sector cap.

### 6.9 Scorecard input
**Risk-control coverage 8/10** — every realized-loss/count/exposure vector has a math-correct, TOCTOU-hardened pre-trade gate plus post-hoc backstops; unrealized-loss blindness (shadow B-1, unbuilt B-2) is the one open flank.
**Capital integrity 8/10** — write-ahead ledger, per-mutation invariant with deferred hard-kill, crash-aware exactly-once recovery capital are exemplary; docked for W10 feeding both loss controls, single-daily broker sync with alert-only drift, and the in-lock breach flatten.

### 6.10 Evidence log
- risk_engine.py:342-589 (10 checks), 212-267 (snapshot reads), 501-545 (DAILY_LOSS + B-1), 242-245/600-612 (day-scoped streak), 547-558 (sector), 614-634 (UNKNOWN sector)
- fund_manager.py:287-337 (ctor/keys), 508-527 (reserve+SLM), 1112-1242 (release_used, pnl math 1163-1167, breach 1209-1229), 1244-1353 (sync_from_broker + H-1), 1390-1410 (snapshot), 1446-1482 (B-1 freshness/prune), 1484-1513 (RESET_PNL), 2048-2159 (invariant + deferred kill), 935-986 (BL-4), 998-1110/1920-1990 (A-1/E-1 capital)
- state_store.py:2346-2365 (W10 query), 709-803 (sector_exposure/has_active/recent_trade_pnls)
- kill_switch.py:104-108 (states), 394-410 (is_active semantics), 199-248 (clear_stale incl. HARD), 250-313 (scheduled clear), 447-586 (soft/hard/resume), 588-663 (auto-trip whitelist), 738-781 (boot-only DB read), 906-923/1024-1271 (flatten machinery)
- main.py:648-709 (breach cb), 560-644 (critical/force-close/api-failure cbs), 898-910 (09:15 sync), 1525-1540 (buffer wiring + auto-clears), 1967-1991 (FM ctor + init capital), 2076-2098 (RiskEngine ctor), 2424-2443 (EOD ctor — no auto_resume), 2455 (token expiry)
- order_reconciler.py:747-790 (prepasses/EXITING), 1093-1113 (CHECK1 costs=0.0), 1549-1633 (oversell), 2020-2168 (CHECK9 FACET-1), 2250-2320 (FACET-2), 2811-2881 (B-1 refresh), 2883-3015 (G3), 3046-3129 (CHECK7), 3538-3575 (kill-flatten recovery)
- position_sizer.py:164-563 (formula/caps); invariant.py:49-205; drift_handler.py:65-245; eod_squareoff.py:196-260/337-343/380/487-499/1191-1305; strategy_governor.py:50-119; scripts/clear_kill_switch.py; scripts/system_manager.py:904-918; scripts/reconcile_pnl.py:272-282; orders/price_math.py:124; config: system_config.yaml:116-177/207-217/270-276/416-431
- **STALE-MEMORY note**: `dual_daily_loss_mechanism` ("different config keys") predates BUILD 1 #1 — both mechanisms now share `risk.daily_loss_limit_pct` and the same fm_ledger read. UNCERTAIN: whether a second same-day breach can re-fire `fire_now` after the RESET_PNL wipe (depends on `_fired_for_date` marking in `fire_now`, eod_squareoff.py:232-260 — marked fired, so scheduled 15:17 fire is consumed on breach days; re-breach re-fire not fully traced).

## PHASE 7 — EXECUTION

### 7.1 What exists (protocol adjudication FIRST, then inventory)

**ADJUDICATION — SETTLED: every live entry executes LIMIT_TRIPLE; the YAML `order_protocol` field is dead config.** (Resolves the Phase-4 vs Phase-5 contradiction — both saw real facts; the YAML matrix implied the wrong conclusion.) The chain, each link line-cited:

1. 12/15 strategy YAMLs declare `order_protocol: "CO_PLUS_TGT"`; the 3 positional declare LIMIT_TRIPLE (config/strategies/*.yaml:11-12).
2. The field is parsed and validated into `StrategyConfig.order_protocol` (strategies/schema.py:53, validator :137-144) — so it *looks* honored.
3. signal_processor calls `place(..., intent=strategy_obj.intent, ...)` and **never passes order_protocol** (signals/signal_processor.py:992-998, 1645-1651, 1924-1929). Zero references to `order_protocol` in signals/.
4. `OrderPlacer.place()` has **no order_protocol parameter** (orders/order_placer.py:757-775).
5. Inside place(): `order_protocol = self._default_protocol` — unconditional (order_placer.py:842-843). OP9 admits it: "Future: per-strategy config via scanner_configs" — never built (:31-33).
6. main.py constructs OrderPlacer **without** `default_order_protocol` (main.py:2235-2260) → constructor default `"LIMIT_TRIPLE"` (order_placer.py:523, stored :562).
7. That value is passed explicitly to the engine (order_placer.py:1203-1212) → LimitTripleProtocol (orders/full_entry_engine.py:93-98).
8. The 3 positional strategies (intent DELIVERY) are additionally walled off: CNC orders refused while `delivery_enabled: false` (broker/zerodha_adapter.py:535-544).

**Consequences:** CoPlusTgtProtocol is never invoked live, so broker-managed-SL semantics, CHECK8 CO_SL_DRIFT (alert-only), the dedupe `variety='co'` SL-exemption, and the EOD CO cancel path are all **inactive backstops, not the live risk surface**. Moreover the CO path is **not activation-safe**: `modify_order` hardcodes `variety="regular"` (zerodha_adapter.py:974-975) so a CO SL could never be trailed, and the CO entry is submitted as `order_type="SL"` (order_protocol_co.py:121) — never live-validated (system live since 11-May on LIMIT_TRIPLE; current Kite CO availability UNCERTAIN).

**Inventory:**
- **State machine**: 9 states, one-way table, terminal absorbing, thread-safe, in-memory only (broker/order_state_machine.py:64-110, OSM11 :40); `EARLIER_STATES` chronological-inversion guard for jittered polls (:88-93, consumed at broker/order_monitor.py:1227).
- **Orders table lifecycle**: statuses = OSM vocabulary + TRIGGER_PENDING variants (core/schema.sql:290-301); `superseded_by` replacement chain with partial index (schema.sql:263-266, 319, 336-337); `reconciliation_status` written by CHECK9 (order_reconciler.py:2061, 2112); `variety` column persisted (schema.sql:284).
- **Protocols**: LIMIT_TRIPLE two-phase — ENTRY LIMIT only at execute (order_protocol_limit.py:167-241), SL+TGT deferred to fill at ACTUAL qty (naked-short fix, :13-19); SL-first discipline (OPL3 :32-35); every SL is stop-limit `"SL"` with offset limit (P0 15-Jun — Zerodha rejects SL-M via API; :362-372); circuit-band placeability gate with wrong-side detection (NOCIL, :297-360); TGT-fail → SL-only partial result, never book-wide escalation (FIX-190 Bug C, :421-495). CO_PLUS_TGT: **no clamp gate at all** on its TGT (order_protocol_co.py:162-248) and TGT failure raises → hard-kill path (:202-213).
- **Retries**: BL-19 429-only loop, max 3, no sleep — pacing via bucket freeze (order_placer.py:1189-1290); adapter exponential 0.2s→5.0s cap with ±0.05s jitter (broker_limits.yaml:35-40, zerodha_adapter.py:1817-1835); 16388 exactly-one retry after margin-cache invalidation (order_placer.py:1216-1254); A-2/FIX-068 BrokerTimeoutError → **no retry**, UNKNOWN_IN_FLIGHT + reconciler queue (order_placer.py:1291-1323); TGTRetryManager: DB-durable flag/count, backoff 30·2^(N-1), 5 attempts, kill-switch+market-hours guards, crash-loop self-alert (24-Jun post-mortem lesson) (orders/tgt_retry_manager.py:23-32, 80-90), wired live (main.py:2314-2320, 2803).
- **RateLimiter**: token buckets order 8/8s, quote 3/3s, historical 2/2s, margins 8/8s (broker_limits.yaml:9-23); monotonic clock, per-bucket lock, penalize/freeze (broker/rate_limiter.py:52-121).
- **Partial fills**: entry PARTIAL → immediate cancel on first partial (FIX-130 Option A, order_monitor.py:899-929) → commit actual qty + place exits at filled qty (order_placer.py:1620-1728); zero-fill-cancel via OrderStatusChanged (:1746); exit legs exempt from fill/partial timeouts (order_monitor.py:1074-1075, 932-933).
- **Rejection taxonomy**: kiteconnect exceptions → typed errors (zerodha_adapter.py:274-323); pre-broker rejects: EOD cutoff, slippage guard, liquidity, RR gate, CNC lock; `_handle_placement_failure`: Telegram WARNING + cancel live legs + release + MIS-blocklist record (order_placer.py:4158-4233).
- **Cancel flows**: OCO sibling cancel AFTER close_trade, BEFORE release_used (order_placer.py:2259-2276); cancel failures → CRITICAL `CANCEL_FAILED_MANUAL_INTERVENTION_REQUIRED`, never abort remaining (:4343-4391); EOD two-pass cancel→2s→exit (eod_squareoff.py:350-393); kill/shutdown cancel-all preserves SL/TGT/EOD legs (order_monitor.py:519-579).
- **Broker sync**: 2s per-order `get_order_history` poll (system_config.yaml:112-114); 3-empty-history → orphan with `get_open_orders` second-source verification (H-15, :694-747); auth×3 stop + API-fail×3 hard-kill (:629-677); 15:15 force-close (:783-856); `get_all_orders` = day-orderbook oracle with tag for A-1/E-1 (zerodha_adapter.py:1732-1794); matching: order_id authoritative, tag only for recovery correlation.
- **Execution-quality instrumentation**: SlippageRecorder (async, best-effort) writes order_execution_log / market_execution_context / trade_slippage_log with rr_damage_pct (orders/slippage_recorder.py:1-68; wired main.py:2266-2274). Latency columns computed on entry fill (orders/order_manager.py:389-491). Entry-slippage abort with observation log (order_placer.py:857-873, 981-1074). broker/slippage_engine.py is **paper-mode** adverse-fill synthesis — not live instrumentation.

### 7.2 What works well
- **Naked-short elimination is systemic**: two-phase placement, exits sized to `event.filled_qty` on every path — full fill, partial-then-cancel, zero-fill (order_placer.py:1984-2003, 1712-1728).
- **SL-first with graduated failure**: SL fail → CRITICAL raise → emergency close/hard-kill; TGT fail → SL-only + durable retry. The 19-Jun "HARD_KILL the book on TGT reject" failure mode is designed out.
- **Retry discipline is narrow and typed**: 429-only loop paced by bucket freeze (no sleeps, no thundering herd), 16388 exactly-once, timeout NEVER retried (duplicate-order-safe by construction).
- **Placeability gate beats broker rejection**: wrong-side clamp detection distinguishes "SL unplaceable → escalate" from "TGT unplaceable → SL-only + retry when band relaxes" (order_protocol_limit.py:297-338, 545-570).
- **Duplicate-exit net is race-hardened**: canonical-earliest kept, terminal-guarded local marks, post-cancel re-verify, immediate SL re-place + CRITICAL if the canonical raced to fill (order_reconciler.py:2662-2807) — direct RAMCOIND closure.
- **EOD is broker-authoritative**: Pass-2 qty overridden by `get_positions()`, stale rows filtered on ALL fires, LIMIT_THEN_MARKET caps vacuum slippage (eod_squareoff.py:985-1047).
- **Boundary guards at the single chokepoint**: tick-snap and tag-truncation inside the adapter — no caller can bypass (zerodha_adapter.py:521-526, 570-579).
- **Fill-tracking race closures**: insert-fill-map-before-track (OP-AR1), PENDING before broker call (FIX-071), chronological-inversion guard, H-15 second-source orphan verification with fail-safe-fire default.

### 7.3 Risks
- **[HIGH] Config-vs-execution divergence on the protocol axis.** 12/15 YAMLs declare CO_PLUS_TGT (validated by schema, displayed by dashboards) while the machine runs LIMIT_TRIPLE unconditionally (order_placer.py:843). Failure scenario: an operator or future engineer "completes" OP9's declared intent or passes `default_order_protocol="CO_PLUS_TGT"` — 12 strategies instantly switch to a **never-live-validated** protocol whose TGT has no circuit clamp, whose TGT-fail path hard-kills, whose SL cannot be modified (`modify_order` variety="regular"), and whose entry order-type is unverified against current Kite CO support. **A loaded config gun, not a dormant curiosity.**
- **[HIGH] No SL trailing of any kind executes live, while config and alerts claim otherwise.** SmartTgt registers only `order_protocol == "CO_PLUS_TGT"` trades (order_placer.py:2036-2040) — never fires. BreakevenManager (FIX-132, the LIMIT_TRIPLE trail) is never constructed: zero references in main.py, so `self._breakeven_manager is None` (order_placer.py:2006-2011); additionally `fill_entry.strategy_obj` is never set (:1420-1439) — a second independent disabler. StructureExitManager is master-off. Yet 12 YAMLs say `smart_tgt_enabled: true` and **every** ORDER PLACED Telegram says "Smart TGT monitoring: ACTIVE (FIXED mode)" because `smart_on` keys on global config only (order_placer.py:1578-1586). Failure scenario: operator believes winners are trailed; risk decisions made on a false premise. (`sl_trail_count` copy also only runs for CO — :2320-2337.)
- **[MED] No per-minute/per-day order budget.** Buckets are per-second only. Kite's published caps include ~200 orders/min, 3000/day, 25 modifications/order (exact current values UNCERTAIN). A cancel/re-place storm at 8/s sustained = 480/min breaches the per-minute cap; defense degrades to reactive 429 penalize. The 25-modify cap is untracked — dormant today only because trailing is dead.
- **[MED] Per-order polling scales API load linearly with the book.** N watched legs → N `get_order_history` calls every 2s (order_monitor.py:607-628); a 10-trade book ≈ 20-30 legs approaches quota pressure and slows fill detection. A single `kite.orders()` snapshot per cycle would be O(1) (primitive exists: zerodha_adapter.py:1732).
- **[MED] Unknown/transient Kite statuses bypass the fill-timeout clock.** Only OPEN/TRIGGER PENDING/SUBMITTED reach `_check_fill_timeout` (order_monitor.py:759-760, 866-870); Kite transients (VALIDATION PENDING, PUT ORDER REQ RECEIVED, MODIFY/CANCEL PENDING) fall to the warn-only branch (:774-779) — an order stuck at OMS in a transient status ages invisibly. Same branch is the only shield against Kite status-string drift — warn-spam, no state corruption, but no escalation counter.
- **[MED] Exit-leg partial-then-terminated is explicitly skipped.** `_on_order_partially_terminated` returns for non-ENTRY legs (order_placer.py:1650-1661): a partially-filled-then-cancelled SL/TGT leaves DB qty > broker qty with the trade OPEN. Mitigations: reconciler CHECK4 PARTIAL_CLOSE (order_reconciler.py:1779-1836) + EOD broker-authoritative qty — but between reconcile ticks the remainder is unprotected.
- **[LOW] pending_rr cancel failure logs ERROR without escalation** (order_monitor.py:1189-1196) — cross-referenced; mitigated structurally (entry stays watched; RR check re-fires every 2s).
- **[LOW] CO-bracket properties as live risk — RE-RATED DOWN.** Per the adjudication, broker-managed-SL opacity, CHECK8 alert-only, CHECK1-as-sole-finalizer, and the dedupe CO exemption are dormant-path properties. Residual exposure only via the HIGH config-gun above. (The operational "CO bracket note" should be re-labeled dormant.)

### 7.4 Gaps
- **W7 exchange_order_id capture: confirmed NOT implemented.** Report renders literal "— pending W7" (reports/daily_trade_review.py:83, 176, 535); PlacedOrder and `get_all_orders` dicts carry no exchange_order_id (zerodha_adapter.py:607-620, 1780-1793).
- **No live execution-quality feedback loop**: recorder writes raw facts ("NO analytics here"); Phase-3b effectiveness analysis referenced but not present (order_placer.py:861-862); tolerance tuning manual.
- **No modification-count tracking per broker order** (Kite 25-modify cap).
- **`entry_order_type: MARKET` (SNR-V2 retest) path exists but master-off** — dormant, consistent with flags.
- Docstring drift: OP-NS4 still claims "INTRADAY uses SL-M" (order_placer.py:118-120), contradicting OPL7/P0 reality (order_protocol_limit.py:21-26) — a trap for future readers.

### 7.5 Technical debt
- **Dead-but-validated config fields**: `order_protocol`, `smart_tgt_enabled`, `trailing_sl_*` in YAMLs have no execution effect — the schema's `extra="forbid"` rigor creates false confidence that present fields are consumed.
- **Two independent, unwired trailing subsystems** (SmartTgtManager built+started for a protocol that never runs; BreakevenManager imported nowhere) plus a third master-off (StructureExitManager) — three SL-owners in code, zero in production.
- `get_all_orders`/`get_open_orders` piggyback the *margins* rate bucket (zerodha_adapter.py:1765, 1769) — category mislabel makes quota reasoning misleading.
- eod_squareoff maintains full CO squareoff machinery (:1088-1168) for a protocol that produces zero rows — untested-in-anger code in the hottest daily path.
- In-memory OSM + `_fill_map` rebuilt by convention on restart (rehydrate keys broker_order_id as synthetic internal id, order_placer.py:659-725) — works, but the id-space duality is tribal knowledge.

### 7.6 Improvement opportunities
- Replace per-order history polling with one `kite.orders()` snapshot per 2s cycle, per-order history only for detail on state change.
- Add Kite transient statuses to the timeout clock + an unknown-status escalation counter.
- Add per-minute/daily order budgets to RateLimiter; per-order modify counter with alert at ~20.
- Make the ORDER PLACED alert honest: derive `smart_on` from actual registration (protocol + manager + strategy flag).
- Either delete `order_protocol` from YAMLs (single source: code) or wire it through place() behind a preflight check — the half-state is the risk.
- Capture `exchange_order_id`/`exchange_timestamp` into orders rows (W7) — data already returned by Kite.
- Extend `_on_order_partially_terminated` to exit legs (record partial exit qty, re-arm remainder SL).

### 7.7 Phase verdict
Execution mechanics are mature, incident-hardened, and consistently fail-safe: two-phase entries, SL-first discipline, typed narrow retries, race-verified dedupe, and broker-authoritative EOD form a coherent defense-in-depth. The protocol contradiction resolves decisively — LIMIT_TRIPLE executes for 100% of live entries; the CO surface is dormant. The dominant finding is a *truth divergence*, not a mechanics flaw: strategy YAMLs, dashboards, and Telegram alerts describe a CO+trailing system that does not exist at runtime, and the dormant CO code is not safe to activate as-is. Broker-sync is robust but architecturally chatty (per-order polling) and lacks per-minute/lifetime budget awareness.

### 7.8 Recommendations (prioritized)
1. **[S] Kill the config gun**: remove `order_protocol`/`smart_tgt_enabled`/`trailing_sl_*` from YAMLs (or mark schema-deprecated with a loader WARN), and add a preflight check asserting the live protocol set == {LIMIT_TRIPLE}. Re-label the CO-bracket operational note "dormant".
2. **[S] Fix the ORDER PLACED alert** `smart_on` derivation (order_placer.py:1578-1586) — operator-facing honesty, one conditional.
3. **[S] Decide trailing intent**: if wanted, wire BreakevenManager (construct in main.py + set `strategy_obj`); if not, delete it and the SmartTgt wiring — one SL owner, matching structure_exit's own doctrine.
4. **[M] Snapshot-based order polling** via `kite.orders()` with per-order fallback; add transient statuses + timeout coverage.
5. **[M] Rate budget completeness**: per-minute/daily order buckets + per-order modify counter.
6. **[M] W7 exchange_order_id capture** into orders rows from order history.
7. **[M] Exit-leg partial-termination handling** (record partial exit, protect remainder) rather than skip-and-defer to reconciler.
8. **[L] If CO is ever to be real**: full CO enablement project — modify_order variety support, CO order-type validation against current Kite API, clamp gate parity, live canary — treat as new protocol certification, not a config flip.

### 7.9 Scorecard input
- **Execution reliability: 8/10** — two-phase/SL-first/dedupe/EOD machinery is incident-proven and fail-safe on every traced path; docked for the dead-trailing + misleading-config truth gap and the skipped exit-partial path.
- **Broker-integration robustness: 7/10** — typed exception taxonomy, disciplined retries, boundary guards, and dual-source orphan verification are strong; docked for per-order polling load, absent per-minute/day/modify budgets, transient-status blind spot, and unverifiable-CO dead code at the adapter seam.

### 7.10 Evidence log
Protocol adjudication: config/strategies/*.yaml:11-12 · strategies/schema.py:26,53,137-144 · signals/signal_processor.py:992-998,1645-1651,1924-1929 · orders/order_placer.py:31-33,523,562,757-775,843,1203-1212 · main.py:2235-2260 · orders/full_entry_engine.py:93-98 · zerodha_adapter.py:535-544 · system_config.yaml:83.
Protocols: orders/order_protocol_limit.py:13-26,32-35,167-241,263-518,520-615 · orders/order_protocol_co.py:25-38,87-158,162-248 · orders/full_entry_engine.py:121-236.
State machine/schema: broker/order_state_machine.py:64-110,140-232 · core/schema.sql:263-337.
Retries/limits: order_placer.py:84-100,1189-1333 · broker_limits.yaml:9-40 · rate_limiter.py:52-121 · zerodha_adapter.py:1798-1885,1475-1494 · tgt_retry_manager.py:23-90 · main.py:2314-2320,2803.
Monitor/sync: order_monitor.py:56-93,583-779,783-856,860-1196,1200-1231,519-579 · system_config.yaml:112-114,429-431 · zerodha_adapter.py:274-323,470-627,864-998,1732-1794.
Partials/fills/OCO: order_placer.py:628-637,1355-1508,1599-1744,1921-2072,2107-2357.
Rejections/cancels: order_placer.py:940-1074,4158-4233,4235-4266,4343-4391 · eod_squareoff.py:350-393,959-1168.
Dedupe/reconcile: order_reconciler.py:140,808-966,1019-1186,2662-2807,3257.
Instrumentation: slippage_recorder.py:1-80 · state_store.py:2250-2299 · order_manager.py:389-491 · slippage_engine.py:1-40 · daily_trade_review.py:83,176,535 · order_placer.py:857-873,981-1031,1575-1594.

## PHASE 8 — DATA QUALITY

### 8.1 What exists (recon stack inventory + live-execution evidence)

| Layer | Code | Schedule | Persists to | Live evidence (VM SQL) |
|---|---|---|---|---|
| In-process reconciler CHECK1-9 + G3 drift + B-1 MTM | `orders/order_reconciler.py` (persist: :989-1008 → `state_store.insert_reconciliation_log` :1639-1646; non-COSMETIC only) | 15s loop in-app | `reconciliation_log` | 7,764 rows, 15-Jun→03-Jul 15:17 |
| Position recon (detect-only) | `scripts/reconcile_positions.py` | cron 15:45 M-F (`deploy/cron/trading-system.cron:81-82`) | `position_reconciliation` | heartbeat: 14 runs, last 03-Jul 15:45, 2 FAILED — but **0 rows ever inserted** |
| EOD cleanup | `scripts/eod_cleanup.py` (4 actions: expire signals, cancel stale orders, orphan smart_tgt, prune fingerprints :58-76) | cron 15:50 (`:84-85`) | trades/orders/signals updates | heartbeat: 11 runs, 1 FAILED |
| EOD verify | `scripts/eod_verify.py` | cron 15:55 (`:87-88`) | `eod_verification` | 11 rows 19-Jun→03-Jul, **all VERIFIED, all pnl_variance=0.0** |
| P&L recon | `scripts/reconcile_pnl.py` (correct columns via `upsert_pnl_reconciliation`) | **NOT in cron** — no entry anywhere in `trading-system.cron`; schema.sql:777 "runs 16:15" is stale | `pnl_reconciliation` | **0 rows ever** |
| P1 broker reconcile | `scripts/eod_broker_reconcile.py` — **ABSENT at HEAD b4aea6f** (design-only `docs/design/p1_eod_broker_reconcile_build_design_02jul2026.md`; built on an unpushed branch) | — | — | — |

`reconciliation_log` taxonomy (SQL E): CAPITAL_DRIFT/UNRECOVERABLE/ok 4,181 · ORPHAN_ADOPTION 2,635(+2) · CRASH_RECOVERY_SL **failed** 904 / ok 4 · MANUAL_CLOSE 23 · POSITION_GREW 14 · MISSING_EXITS 1. Rows/day: 15-Jun 351, 16-Jun 4,672, 17-Jun 1,398, 18-Jun 1,175, 19-Jun 149, then **≤3/day from 22-Jun on** — 99.75% of all rows are the 15-19 Jun crash storm; the system has settled.

**Broker truth surfaces pulled today:** positions (`kite.positions()` — reconciler CHECK1 15s + 15:45 script), orders (`get_all_orders` recovery `order_reconciler.py:3257` + order_monitor polling), margins (`get_margins()` at init/banner + G3 drift `order_reconciler.py:2887-2960`, alert-only), holdings (`orders/cnc_gtt_monitor.py:333`), LTP quotes (B-1 MTM `:2811-2881`, advisory). **NOT pulled/compared:** broker realized P&L (nothing persists it), charges/fees actuals vs computed cost model, exchange order ids (W7), per-fill broker `trades()` vs local avg except inside CHECK1 manual-close adoption. `scripts/system_manager.py` EOD email makes **no broker call** — :361-367 merely labels internal gross-vs-net for the human comparing against the Kite page (the ₹3.55 = charges episode).

**Charges model:** `broker/cost_calculator.py` + `config/broker_costs.yaml`. `docs/locked_decisions.yaml:335` requires "daily report rows match contract notes within rounding"; `broker_costs.yaml:5` says "Verify against Zerodha contract notes before each live season"; `docs/web_claude/03_audit_responses/full_system_audit_2026-04-26.md:293` admits it was never verified. No later in-repo evidence of a contract-note check. UNCERTAIN whether done manually off-repo.

### 8.2 What works well
- **Signal capture identity is exact:** SUM(`webhook_audit.signals_accepted`) = 95,431 == COUNT(`signals`) — per-POST audit (58,507 rows, incl. 20,461×403 auth-rejects + 18×503) reconciles to per-symbol storage with Δ=0.
- **Zero duplicate trades per signal** (no `signal_id` with >1 trade). Same-day symbol+strategy repeats (up to ×6, RSYSTEMS 03-Jul) are re-signals, not double-fills.
- **Latency + screener linkage 100%:** 83/83 filled trades have all three latency columns; 228/228 trades join `screener_results`.
- **Slippage coverage healed:** 59/84 closed (70.2%) overall, but missing rows are confined to 15-19 Jun (SQL I) — 100% since the 22-Jun v31 deploy.
- **Reconciler intervention collapsed** from 4,672/day (16-Jun) to ≤3/day; CLOSED_MANUAL weekly rate declining 36.0% → 28.6% → 23.7% (SQL G3). MANUAL_CLOSE recon markers (23) exactly match marker-bearing CLOSED_MANUAL trades.
- **The 16:07 report's Reconciliation sheet** (live since 01-Jul, 3 heartbeats) has a FAIL-able CAPITAL block (ledger==trades ≤₹1) with a proven FAIL path, and honestly renders BROKER as PENDING_CAPTURE rather than fake-PASS (`docs/report_data_contract.md` Block 4/5).
- No stuck `needs_tgt_retry` backlog (0 rows); eod_squareoff 10/10 positions succeeded across 8 firing days.

### 8.3 Risks
- **[HIGH] FALSE-VERIFY — confirmed at HEAD.** `scripts/eod_verify.py:61` queries `system_net_pnl - broker_net_pnl`; real columns are `broker_pnl/system_pnl/variance` (`core/schema.sql:783-785`). The query sits in `try/except Exception: pass` (:59-68) → `sqlite3.OperationalError` swallowed → `pnl_variance=0.0` → the `>100` check (:77) can never fire. It is dead **twice over**: `pnl_reconciliation` has 0 rows (feeder never scheduled), so even a column-fixed query returns None → 0.0. Operational meaning: 11/11 days the operator received "EOD VERIFIED: all clear" (:129) — a claim whose docstring includes "capital reconciled" (:5-6) — while the P&L leg has **never once evaluated**. VERIFIED attests only "no OPEN/PARTIAL trades created today, no PENDING orders placed today". Failure scenario: a mis-adopted external-close price books wrong `net_pnl`; fm_ledger, daily-loss gates and all reports inherit it; eod_verify still prints VERIFIED; nothing automated ever compares to the broker.
- **[HIGH] No broker-vs-system P&L comparison persists anywhere today.** `reconcile_pnl.py` un-cronned AND would fail live anyway (uses generic `ZERODHA_API_KEY/ACCESS_TOKEN` env vars, :99-100, which "are never set in this deployment" per `reconcile_positions.py:87-89`); P1 `eod_broker_reconcile.py` unpushed. The only daily broker-P&L check is the operator's eyeball against the Kite app. Scenario: slow per-trade drift (e.g. the RMS-close `costs=0.0` quirk) accumulates invisibly for weeks.
- **[MED-HIGH] The 15:45 position backstop leaves no evidence and fails silent.** `position_reconciliation` = 0 rows across 14 runs: empty-vs-empty days insert nothing (loop over `all_symbols`, :256-298), and the broker-fetch ERROR path (:244-254) returns **before** insert with **no Telegram** (notifier fires only on `has_mismatch`, :300) — only a cron-heartbeat FAILED remains (2 of 14 runs; dates UNCERTAIN). Scenario: credential breakage at 15:45 for a week = zero position reconciliation, one easily-missed heartbeat alert per day, no DB trace.
- **[MED] Charges are computed, never captured/validated.** No contract-note parity check ever recorded (8.1); `trades.cost_*` and `charges` are model output. Scenario: a Zerodha rate change (STT/stamp revision) silently skews every `net_pnl` and the daily-loss ledger until a human notices.
- **[MED] Raw execution log unlinkable.** All 118 `order_execution_log` rows have `parent_trade_id` NULL (SQL K: COUNT DISTINCT = 0) — writer passes `ev.trade_id or None` (`orders/slippage_recorder.py:123`), so `OrderFilled` events carry no trade_id; rows are ENTRY/COMPLETE only, from 22-Jun. `idx_oel_trade` is dead; per-trade forensics must fall back to `trade_slippage_log`.
- **[MED] W8 blindness:** 10 of 24 CLOSED_MANUAL closed intraday (10:00-14:11, SQL G2) — CO broker-managed SL fires, RMS closes and human closes are indistinguishable (bare `MANUAL_CLOSE` = detection, not initiation; classifier's honest SYSTEM_CLOSE bucket, `reports/daily_trade_review.py:258` + contract note). Scenario: an unauthorized manual close is indistinguishable in-data from a CO SL fire.
- **[LOW] Drift history fragmented:** zero CAPITAL_DRIFT rows in `system_events` (only STARTUP 41 / SHUTDOWN 40 / CONFIG_DIFF 16 / KILL_AUTO_CLEARED 9) — drift evidence lives solely in `reconciliation_log` (4,181 rows, all June noise-era) and Telegram.
- **[LOW] 904 failed CRASH_RECOVERY_SL rows** (15-18 Jun) show the reconciler retry-spamming a failing action once per 15s cycle — log-volume amplification during incidents.

### 8.4 Gaps (W-ledger status at HEAD — all verified still pending)
| W | Gap | State at HEAD b4aea6f | Operational cost |
|---|---|---|---|
| W2 | `trades.broker_margin_blocked` | No such column in schema.sql | Report "Broker Margin Blocked" = pending; margin truth only transient (G3) |
| W3 | Broker P&L/positions recon wiring | BROKER block permanently PENDING_CAPTURE (contract Block 5); feeder tables 0 rows | The report can never FAIL on broker divergence |
| W5 | Exit-trigger timestamp | Absent (`filled_at`≈`exit_time`; resting legs placed at entry) | Exit latency unmeasurable |
| W6 | `trade_excursions` provenance | No provenance column (schema.sql:760-772); only aggregate `excursion_reconstruction_runs` | RECONSTRUCTED-vs-BACKFILLED split impossible; 38/84 = 45.2% coverage, missing 46 (only 3 NULL-exit + 3 sub-minute explained; rest = pre-cron history, cron live only since ~29-Jun) |
| W7 | `orders.exchange_order_id` | Absent (only `order_execution_log.exchange_timestamp`) | No exchange-level audit trail for disputes |
| W8 | `trades.closure_source` | Absent | EOD/CO-SL/RMS/human collapse to SYSTEM_CLOSE (10 intraday cases live) |
| W9 | Per-signal drop log | Absent; `webhook_audit` aggregates only (421,507 rejected, no per-signal reason) | 81.5% of received symbols have no row-level disposition |
| W11 | NULL-exit CLOSED_MANUAL | 3 rows confirmed (15/16-Jun, SQL H) — two-step close: step 2 (`record_manual_close_financials`) never reached (`docs/audit/system_security_audit_02jul2026.md:169`) | 3 trades permanently outside P&L sums; loss invisible to daily-loss gate at the time |
| W12 | Persisted slippage tier | `trade_slippage_log` has `price_band` only | True per-tier vs-expected analysis impossible |
| W10 | `get_daily_realized_net_pnl` double-cost | Still present; report avoids it (contract CAPITAL note); fix bundled into B-2 | Any new caller silently double-subtracts costs |

### 8.5 Technical debt
- `eod_verify.py` — wrong columns + blanket `except: pass` + a schema that defaults `status='VERIFIED'` (schema.sql:946): verification-shaped code, not verification.
- Stale docs-as-spec: schema.sql:777 claims reconcile_pnl runs 16:15; the cron says otherwise — doc/deploy drift inside the schema file itself.
- Dead/legacy credential path: `reconcile_pnl.py` still on generic env vars while `reconcile_positions.py` was upgraded to the account/token-file pattern (RI8) — divergent twins.
- `position_reconciliation` and `pnl_reconciliation` tables exist since v15/v20 but hold 0 rows — schema surface without data.
- `recovered_flag` (G5a, schema.sql:164) brand-new and unpopulated — fine, but adoption events (2,637) predate it, so history is unlabeled.
- `order_execution_log.leg` defaults to "ENTRY" when enrichment fails (`slippage_recorder.py:127`) — can't distinguish "is ENTRY" from "couldn't resolve".

### 8.6 Improvement opportunities
- Deploy P1 `eod_broker_reconcile.py` (already built, v42) — it closes the two HIGHs at once (persists broker P&L daily + gives eod_verify a real feeder) and subsumes `reconcile_pnl.py`.
- Make `reconcile_positions.py` insert an "OK-EMPTY" attestation row and its ERROR row, and alert on ERROR — positive evidence instead of silent green.
- One-time contract-note parity check: pick 3 live trading days, compare `trades.charges` components vs Zerodha contract-note lines; record result in-repo; repeat per season per `broker_costs.yaml:5`.
- Populate `OrderFilled.trade_id` at publish (order_monitor knows it) — one field fixes OEL linkage.
- W8 is a one-liner per close path (`closure_source`) and retires the classifier workaround; W12 is one column at record time.

### 8.7 Phase verdict
Internal self-consistency is genuinely strong — signal-capture identity exact, no duplicate fills, 100% latency/screener linkage, capital ledger cross-checked daily by a FAIL-able report block, and reconciler intervention has collapsed to near-zero as fixes landed. But the broker-truth axis is a facade at EOD: the one artifact that claims daily verification ("EOD VERIFIED") has never evaluated its P&L leg (wrong columns, swallowed exception, empty feeder table), the position backstop has never persisted a single row, and no broker-vs-system P&L or charges comparison exists anywhere in the running system. The system's picture of itself is well-audited; its picture against the broker rests on 15-second position polling plus a human looking at the Kite app.

### 8.8 Recommendations (prioritized)
1. **[S] Fix eod_verify.py:61** to the real columns AND treat a missing `pnl_reconciliation` row as `PENDING` (not silent 0.0); log instead of `except: pass`. Until a feeder exists, have it say honestly "P&L: NOT CHECKED".
2. **[M] Push + cron P1 `eod_broker_reconcile.py`** (built, shadow-tested); retire `reconcile_pnl.py` or fix its credential path — one persisted broker-vs-system P&L row per day, alert on variance.
3. **[S] reconcile_positions.py:** persist the ERROR row + an OK/empty attestation row; add Telegram on ERROR path.
4. **[S] W8 `closure_source`** at each close path; **[S] W12 tier column**; both single-line writers.
5. **[M] Contract-note parity check** (manual, 3 days) + record in `docs/audit/`; recurring seasonal task.
6. **[S] Set `OrderFilled.trade_id`** so `order_execution_log.parent_trade_id` populates; backfill not required.
7. **[M] W2 broker margin capture** at entry (`order_margins()` already called at `zerodha_adapter.py:1344`); **[L] W9** raw receiver log (highest volume, lowest urgency).
8. **[S] Backfill/annotate the 3 W11 NULL-net trades** (one-time manual SQL with broker statement, recorded as a migration note).

### 8.9 Scorecard input
- **Data-truth alignment 5/10** — positions/orders/margins are continuously broker-aligned and internally the ledgers reconcile to the paisa, but broker P&L and charges are never compared and the daily "VERIFIED" is partly fictitious.
- **Reconciliation completeness 4/10** — in-process reconciliation is comprehensive and demonstrably settling, but two of four EOD recon artifacts have empty-forever tables, one lies, and the built replacement (P1) sits unpushed.

### 8.10 Evidence log
- **Code (HEAD b4aea6f):** `scripts/eod_verify.py:44-89` (checks, swallow, INSERT) · `core/schema.sql:780-792` (pnl_reconciliation cols), `:938-948` (eod_verification, DEFAULT 'VERIFIED'), `:585-601` (reconciliation_log), `:986-1016` (order_execution_log), `:164,189-191` (recovered_flag/latency) · `scripts/reconcile_positions.py:244-254` (ERROR early-return, no insert/alert), `:296-298` (insert), `:87-89` (generic env vars "never set") · `scripts/reconcile_pnl.py:99-100` (generic env creds), correct upsert `:287-296` · `deploy/cron/trading-system.cron:81-88` (15:45/15:50/15:55; no reconcile_pnl/eod_broker_reconcile entries) · `orders/order_reconciler.py:989-1008` (RC10 persist), `:2811-2881` (B-1 MTM), `:2883-2960` (G3 alert-only), `:3257` (get_all_orders) · `orders/cnc_gtt_monitor.py:333` (holdings) · `orders/slippage_recorder.py:112-145` (OEL writer, `ev.trade_id or None`) · `scripts/system_manager.py:361-367` (no broker call) · `reports/daily_trade_review.py:258` (classify_closure) · `docs/report_data_contract.md` (W-ledger, closure-source note, BROKER block) · `docs/locked_decisions.yaml:335`, `config/broker_costs.yaml:5`, `docs/web_claude/03_audit_responses/full_system_audit_2026-04-26.md:293` (contract-note never verified) · `docs/audit/system_security_audit_02jul2026.md:153,169` (W11).
- **VM SQL (read-only `mode=ro` URIs, single session):** eod_verification 11 rows all VERIFIED/0.0 · pnl_reconciliation COUNT=0 · position_reconciliation COUNT=0 · reconciliation_log 7,764 taxonomy + per-day · trades status distribution 228 total 15-Jun→03-Jul · CLOSED_MANUAL per-trade exit times/markers, weekly rate 36.0/28.6/23.7 · W11 3 NULL rows · slippage 59/84, missing confined 15-19 Jun · excursions 38/84, missing 46 · OEL 118 ENTRY/COMPLETE, DISTINCT parent_trade_id=0 · latency 83/83 · screener 228/228 · dup signal_id none · webhook_audit 58,507 rows / accepted 95,431 == signals / rejected 421,507; codes 200×38,028 · 403×20,461 · 503×18 · system_events no DRIFT type · control_tower_findings 4 all RESOLVED · cron_heartbeat: reconcile_positions 14 (2 fail), eod_cleanup 11 (1 fail), eod_verify 11 (0 fail) · eod_squareoff_log 10/10 across 8 firing days · needs_tgt_retry / recovered_flag 0 rows.
- **UNCERTAIN:** off-repo manual contract-note checks; dates/causes of the 2 reconcile_positions FAILED heartbeats; whether `order_execution_log.signal_id` is also NULL.

-----

Also Check below point too:

Batch 5 running (Agent-I: Operations — with the stuck-activating alert-watcher/security-watcher units as priority investigation; Agent-J: Security posture). Phases 1–8 are consolidated in the report. Cross-agent corrections logged so far for the final synthesis: two stale operational memories identified (daily-loss config keys were unified 24-Jun; CO-bracket note should say dormant), and the P4↔P5 protocol contradiction resolved by P7's line-cited adjudication.