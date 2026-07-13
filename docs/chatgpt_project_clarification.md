# ChatGPT Project Clarification — Trading System v2

> **Purpose:** Onboarding brief for ChatGPT. This is architecture, philosophy, and
> operating-model context — NOT an implementation spec. Written 2026-07-10, grounded in
> the live repo (`SYSTEM_MAP.md`, `PATHS.md`, `AGENTS.md`, `GEMINI.md`, `DEPLOYMENT.md`,
> the module tree, and the operator memory). Where a fact could go stale, the source file
> is cited so you can re-verify.

---

## 0. One-paragraph orientation

This is a **single-user, self-hosted intraday-first algorithmic trading system** for Indian
equities (NSE), operated by one human (Rama). Signals originate from **Chartink scanners**
(15 of them), arrive via **webhook**, are screened and scored, sized against a capital
budget, risk-checked, and placed with **Zerodha (KiteConnect)**. Positions are monitored by
a 15-second reconciler loop and squared off intraday (MIS) or protected overnight by durable
GTT orders (CNC delivery, currently dormant). It runs on an **Oracle Cloud Ubuntu VM** in
**IST**, with SQLite as the system of record, systemd services for the always-on processes,
and cron for the scheduled jobs. Trading mode is **paper today, migrating to live
micro-capital**. The engineering culture is **safety-over-profit, derive-don't-duplicate,
fail-fast-and-loud, everything-auditable**.

---

## 1. SYSTEM PURPOSE

### 1.1 Primary objective
Automate a disciplined, rules-based **intraday equity trading** workflow end-to-end: turn
Chartink scanner alerts into risk-bounded, fully-tracked trades without a human in the loop
during market hours — while never taking an action that can't be reconstructed after the
fact. Profit is the goal; **capital preservation and correctness are the constraints that
outrank it.** Delivery (overnight CNC) exists in the codebase but is deliberately parked
behind flags until an end-to-end live proof (T2) passes.

### 1.2 Design principles (in priority order)
1. **Safety over profit** — every code change is evaluated first for what it can do wrong.
   Multiple independent safety layers, not one.
2. **Reliability / recovery-first** — the system must survive restarts, crashes, and broker
   hiccups mid-session and *recover state before continuing*, never resume blind.
3. **Auditability** — every signal, screen, order, fill, capital move, and exit is persisted
   and reconstructable. "If it isn't logged, it didn't happen."
4. **Derive, don't duplicate** — one source of truth per fact (one resolver, one kill-switch
   authority, one tick-snap chokepoint, one config authority). Redundant copies drift.
5. **Boring, explicit code; fail fast and loud** — no clever silent fallbacks. A wrong config
   or schema mismatch should *refuse to start*, not degrade quietly.
6. **Paper/Live parity** — the same code path runs in both modes; mode is a data column, not
   a fork. Every change is validated in both.
7. **Additive, reversible changes** — new features land behind default-OFF flags and shadow
   modes; nothing is cut over without a soak period and a rollback anchor.

*Source: `AGENTS.md`, `memory/feedback_foundation_rules.md`, `memory/feedback_paper_live_parity.md`.*

---

## 2. SYSTEM BOUNDARIES

### 2.1 Inside the system (its responsibilities)
- Ingesting and de-duplicating Chartink webhook signals.
- Secondary screening + scoring (deciding *whether* a signal becomes a trade).
- Capital budgeting / position sizing / bucket allocation.
- Pre-trade risk checks (daily-loss, exposure, concentration, count caps, kill-switch).
- Order placement, fill tracking, SL/TGT protection, trailing, and exit.
- Position monitoring, reconciliation with the broker, and naked/orphan recovery.
- Overnight protection for delivery (GTT), when enabled.
- Persistence (system of record), reporting, alerting, and its own operational health
  monitoring (cron officer, control tower, watchdogs).

### 2.2 Intentionally OUTSIDE the system
- **Signal generation logic** — that lives in Chartink (the scanners are authored there).
- **Discretionary trade decisions** — no human override *during* the session; the operator
  configures rules, not individual trades.
- **The reverse proxy / TLS / network edge** — recommended to be handled by Nginx +
  Let's Encrypt, not baked into the Python app (`DEPLOYMENT.md` §8).
- **AI-driven trade actions** — the operations AIs (Gemini/Antigravity, the heartbeat Claude)
  *observe, audit, and brief only*; they never place, modify, or cancel orders, never write
  code, never restart services (`AGENTS.md`, `GEMINI.md`).
- **Broker-side risk (RMS, margin)** — Zerodha's own risk system is treated as an external
  authority the system reconciles against, not something it controls.

---

## 3. COMPLETE TRADE LIFECYCLE

```
Chartink scanner fires
     │  (HTTP POST, ?token=WEBHOOK_SECRET)
     ▼
Webhook receiver  (signals/, port 5000, loopback; per-IP token-bucket rate limit, 429)
     │  dedup → duplicates dropped BEFORE any DB insert (status=DUPLICATE, no row)
     ▼
Signal ingestion / persistence  (signals table; every STORED signal gets a row)
     │
     ▼
Signal processor  (signals/signal_processor.py :: _process_one)
     │  gates in order: kill-switch → trading window → STRATEGY_CONTROL (trade_type/intent)
     │  → strategy governor (circuit breaker) → side resolution
     ▼
Secondary screening  (screening/secondary_screener.py)
     │  MIS-tradability filter, circuit-band proximity reject, liquidity/quality checks
     ▼
Scoring  (screening/; algo_score + screener score → min_pass_score gate, currently 60)
     │  below threshold → REJECTED
     ▼
Capital allocation / sizing  (capital/position_sizer.py + fund_manager.py)
     │  bucket (intraday vs positional) → size → reserve() holds capital (three-balance ledger)
     ▼
Risk checks  (capital/risk_engine.py :: approve)
     │  DAILY_LOSS (pct gate), OPEN_POSITIONS, DAILY_TRADES, concentration, sector, exposure
     │  reject → capital released
     ▼
Broker order  (orders/order_placer.py → full_entry_engine → protocol → broker/zerodha_adapter)
     │  product coerced to MIS (double-locked), tag truncated ≤16, price tick-snapped
     │  entry placed (LIMIT default, or MARKET/CO per config)
     ▼
Fill tracking + protection  (orders/; on fill → place SL + TGT, or CO bracket)
     │  slippage recorded; capital reservation committed
     ▼
Position monitoring  (orders/order_reconciler.py — 15s loop, CHECK1..CHECK9)
     │  recovery prepass (adopt orphans/in-flight) → dup-exit dedupe → naked/oversell guard
     │  → optional structure-aware trailing (SNR-V2, default OFF)
     ▼
Exit  (SL hit / TGT hit / EOD squareoff 15:17 / structure break / kill-switch flatten)
     │  EXITING-first state write → cancel resting legs → flatten → capital released to ledger
     ▼
Reporting  (reports/daily_trade_review.py @16:07 → xlsx; EOD reconcile; AI EOD reviews)
```

Key nuance: **the strategy-control gate only *rejects*; it never changes the placement path.**
And **capital is reserved before the order and released on reject/exit** — the three-balance
invariant (available / reserved / committed) must always hold.

*Source: `PATHS.md` "Strategy control", "Order-exit safety", "Naked-orphan"; `AGENTS.md` role 9.*

---

## 4. CORE DESIGN PRINCIPLES (engineering rules)

- **Never place a duplicate order / never double-exit.** Exactly one live SL and one live
  TGT per open trade (the "L2 keystone" invariant in `order_reconciler`). Duplicate detection
  cancels the *non-canonical* leg, never drops to zero. (RAMCOIND fix, 25-Jun.)
- **Never leave a naked position.** An entry that reached the broker but whose local row was
  lost (timeout/crash) is *recovered by broker evidence*, not blindly failed. Capital is only
  released on evidence of zero fill. (A-1/E-1 orphan recovery.)
- **Every trade is traceable.** signal → screener_results → trade → orders → fills → excursions
  → reconciliation, all persisted and joinable.
- **Recovery before continuation.** The 15s reconciler runs a recovery prepass at the *top* of
  every cycle (startup and in-session) before any other check.
- **One chokepoint per cross-cutting concern.** Tick-snapping happens *only* in the adapter;
  tag-truncation *only* in the adapter; MIS product coercion is double-locked; the resolver
  `strategy_will_trade` is the single source of truth for "will this strategy trade?".
- **Fail fast on bad config/schema.** Contradictory config → refuse to start
  (Config Sanity Auditor → `ValidationError`). Schema-version mismatch → fail-fast, no silent
  downgrade.
- **Default-OFF, shadow-first, soak-then-cutover** for every new behavior.
- **Paper == Live.** No `if mode == 'paper'` business-logic branches; the paper adapter mimics
  the broker (paper fills, paper GTTs with numeric IDs, paper holdings).

*Source: `PATHS.md` (RAMCOIND, A-1/E-1, tick-snap, config auditor sections); `memory/feedback_foundation_rules.md`.*

---

## 5. SAFETY LAYERS

| Layer | Where | Responsibility |
|---|---|---|
| **Kill switch** | `capital/kill_switch.py` | Single in-memory authority; SOFT (stop new entries) and HARD_KILL (flatten everything). DB row is boot-read-only. Stale-day auto-clear. |
| **Risk engine (pre-trade)** | `capital/risk_engine.py::approve` | Gates every entry: daily-loss %, open-position cap, daily-trade cap, concentration, sector exposure, delivery caps. |
| **Fund manager (post-close, absolute)** | `capital/fund_manager.py` | Absolute daily-loss backstop from `fm_ledger.pnl_delta`; three-balance ledger; crash-aware reservation reconstruction. **Distinct from the risk-engine pct gate** — two independent daily-loss mechanisms. |
| **Strategy governor / circuit breaker** | strategy governor | Disables a strategy after excessive loss (e.g. 2× avg loss before noon). |
| **Strategy control resolver** | `strategies/control.py` | `strategy_will_trade` — the only authority on whether a strategy trades, keyed on trade_type + declared intent. |
| **Config Sanity Auditor** | `core/config_auditor.py` | Blocks startup on contradictory/unsafe config (A–G check groups); fail-fast. |
| **Duplicate-exit invariant** | `order_reconciler._check_duplicate_exits` | One live SL + one live TGT per trade; cancels extras. |
| **Naked/oversell guard** | `order_reconciler` CHECK2/CHECK9 | Detects oversell (CRITICAL + auto-flatten) and naked positions (WARNING, never silent). |
| **Orphan/in-flight recovery** | `order_reconciler._recover_in_flight_entries` | Broker-tag correlation → adopt-or-fail; closes the naked-position window. |
| **Circuit-band placeability gate** | `orders/price_math.clamp_exit_into_band` | Ensures SL/TGT are placeable within exchange circuit limits; unplaceable SL → emergency close + hard_kill. |
| **Tick-snap chokepoint** | `broker/zerodha_adapter._snap_order_to_tick` | Every submission (place + modify) snapped to tick; fail-safe default 0.05. |
| **Tag-length guard** | `broker/zerodha_adapter.place_order` | Truncates tag ≤16 so the last-line-of-defense emergency exit can't be broker-rejected. |
| **Order/position reconciliation** | `order_reconciler` (15s) + EOD reconcile jobs | System vs broker positions/P&L; detect-only vs authoritative. |
| **Token-expiry detection** | `main._invalidate_token`, token-watcher | Dead token → rename + guarded shutdown; auto-refresh at 08:15. |
| **GTT overnight protection** | `orders/cnc_gtt*.py` | Durable OCO GTT for delivery; survives restarts; not gated by the master delivery lock so protection persists even when entries are disabled. |
| **Operational watchdogs** | cron-watchdog timer, alert-watcher, trading-watchman, security-watcher | Watch-the-watcher: assert crons/heartbeats fired; deliver CRITICAL alerts; watch auth.log/network. |

*Source: `PATHS.md` (kill-switch, RAMCOIND, dual-daily-loss, circuit-band, GTT sections); `memory/dual_daily_loss_mechanism.md`, `memory/co_bracket_operational_note.md`.*

---

## 6. FAILURE RECOVERY

| Failure | Behavior + recovery |
|---|---|
| **Internet drop** | Broker/API calls fail; reconciler defers (never blind-fails). On reconnect, recovery prepass reconciles against broker truth. Positions already have broker-side SL/TGT (or GTT) protecting them. |
| **Oracle VM down** | Trading stops (no process). On reboot, systemd restarts services; startup sync (`reconcile_once`) recovers open state from broker before the loop resumes. |
| **Broker API failure** | Rate-limited retries; broker-unreachable → DEFER (keep reservation), not release. BrokerAuthError → guarded shutdown + dead-token rename. |
| **Chartink outage** | No signals arrive (system idle); no false trades. No alerting on non-trading days. |
| **Zerodha (fills/positions) mismatch** | 15s reconciler + EOD reconcile detect drift; naked/oversell/orphan guards act; CRITICAL alert on ambiguous cases. |
| **Telegram down** | Alerts queue as sentinel `.flag` files; alert-watcher retries/delivers; email is the durable record. |
| **Database issue** | SQLite is the system of record; daily backups (01:00/01:05) + pre-deploy backups; category-aware retention. Schema mismatch → fail-fast (no silent downgrade). |
| **Power failure** | Same as VM-down: systemd auto-start + startup recovery. State is durable in SQLite + broker + GTT. |
| **Restart during market** | The hardest case, explicitly engineered: recovery prepass adopts in-flight/orphaned entries the *same cycle*, re-arms SL (G5b settling window), retries TGT. Capital reservations reconstructed from the durable `fm_ledger` RESERVE row (exactly-once commit/release). |

Recovery philosophy: **reconstruct state from durable evidence (broker orders by tag, the
ledger, GTT state) before acting — never resume on the stale local table alone.**

*Source: `PATHS.md` "Naked-orphan ROOT-CAUSE fix (A-1/E-1)"; `memory/order_lifecycle_operational_note.md`.*

---

## 7. MODULE RESPONSIBILITIES

- **`broker/`** (13 files) — All broker integration. `zerodha_adapter.py` is the single I/O
  chokepoint to KiteConnect (place/modify/cancel, GTT, holdings, quotes) and the enforcement
  point for tick-snap, tag-truncation, and MIS product coercion. Also polling (`order_monitor`),
  rate limiting, slippage engine, instrument cache. The paper adapter lives here too (parity).

- **`capital/`** (10 files) — Money and risk-of-ruin. `fund_manager.py` (three-balance ledger,
  reservations, absolute daily-loss backstop), `risk_engine.py` (pre-trade gates),
  `position_sizer.py` (sizing + bucket allocation), `kill_switch.py` (SOFT/HARD authority).

- **`screening/`** (6 files) — Deciding whether a signal becomes a trade. `secondary_screener.py`
  (MIS-tradability, circuit-proximity reject, quality), scoring against `min_pass_score`, plus
  the retest monitor (SNR-V2 entry side).

- **`signals/`** (4 files) — Ingestion and the pipeline entry. Webhook receiver (dedup, rate
  limit), `signal_processor.py` (the gate chain: kill-switch → window → strategy-control →
  governor → side → screen → size → place; plus `continue_from_gate`/`continue_from_retest`
  resumption paths).

- **`orders/`** (19 files) — Order lifecycle and the safety heart. `order_placer.py`,
  `full_entry_engine.py`, order protocols (LIMIT_TRIPLE / CO+TGT), `order_reconciler.py`
  (the 15s CHECK1..9 loop, recovery prepass, dup-exit invariant, naked/oversell guards),
  `eod_squareoff.py` (15:17 flatten), `price_math.py` (clamp/tick/GTT-limit math),
  `cnc_gtt*.py` (delivery GTT), `structure_exit_manager.py` (SNR-V2 exit, default OFF).

- **`reports/`** (4 files) — Analytics output. `daily_trade_review.py` (the 7-sheet forensic
  xlsx: Dashboard/Reconciliation/Orders/Signals/Strategies/Slippage/Config) — DB-pure, reads
  `config_snapshots` not YAML, and its Dashboard is engineered to *agree with* the detail sheets.

- **`ops/`** (13 files) — VM operations aggregation. `ops/control_tower/` (daily 17:05 runner:
  health scoring, findings lifecycle, freshness, disk, noise-controlled Telegram deltas + a
  pull report). This is the system's *self-monitoring* layer.

- **`ops_dashboard/`** (isolated sub-project, own venv) — Read-only Flask+Waitress web GUI on
  loopback 127.0.0.1:8500, exposed via Tailscale TLS. No kiteconnect, read-only DB (mode=ro),
  login+TOTP auth. Displays pipeline/capital/risk/strategies/trades/logs/audit. Deliberately
  *display-only* (no control plane writes). Being redesigned screen-by-screen (Screen-03 done,
  Screen-04 Signals next).

- **`core/`** (19 files) — Infrastructure. `state_store.py` (the DAO / system of record),
  `schema.sql` + `migrations.py` (schema v42), `config_loader.py` (typed config + cross-field
  sanity), `config_auditor.py` (the config authority), `db_connect.py` (v28 two-DB ATTACH),
  `cron_registry.py`, `time_authority.py` (`now_ist()`), `mis_blocklist.py`.

- **`scripts/`** (74 files) — Ops + jobs + AI-ops. Cron entry points (EOD reconcile/cleanup/
  verify, backfills, backups, control tower, preflight, strategy status), the Gemini/Antigravity
  AI-ops scripts, deploy/preflight tooling, token/login helpers.

- **`alerts/`** (4 files) — Telegram notification layer (`TelegramNotifier`), sentinel-flag
  based delivery so alerts survive a Telegram outage.

- **`sr_detector/`** (12 files) — Support/Resistance detection (pure package, core+stdlib only):
  pivots, zone clustering, volume profile, multi-TF confluence → HIGH/MED/LOW zones. Feeds the
  SNR-V2 retest-entry and structure-exit features. Shadow/default-OFF today.

*(Also: `strategies/` — the 15 strategy configs + resolver + loader + schema; `data/` — market
data / candle store; `config/` — YAML source of truth; `deploy/` — systemd/cron/hooks; `sats/` —
PC-only static analysis, git-ignored.)*

---

## 8. DATABASE PHILOSOPHY

Two SQLite files, ATTACHed as one logical DB (raw reads MUST go through `core.db_connect.connect`):
- **`trading_system.db`** (schema **v42**) — the operational system of record.
- **`analytics.db`** — `system_metrics` / `_daily` and analytics splits.

**Permanently stored (system of record):** every signal (`signals`), every trade (`trades`),
every broker order/leg (`orders`), score per signal (`screener_results`), capital moves
(`fm_ledger`), reconciliation results, MFE/MAE excursions (`trade_excursions`), GTT state
(`gtt_state`), config-as-it-ran per day (`config_snapshots`), cron heartbeats, control-tower
findings, S&R detector results, EOD broker reconciliation verdicts.

**Temporary / in-memory (NOT durable):** the kill-switch live state (DB row is boot-read-only),
`in_flight` order tracking (memory only — a restart clears it), the structure-exit debounce map,
unrealized-MTM cache, hot GTT cache (rehydrated from `gtt_state` on boot).

**Reconstructed (derived, not primary):** open-position state on startup (rebuilt from broker +
ledger), capital reservations after a crash (from the durable `fm_ledger` RESERVE row), MFE/MAE
(post-EOD reconstruction from candles), the daily report (entirely from DB, never from YAML).

Rules: **fail-fast on schema mismatch** (no silent downgrade), all migrations are **pure-additive**
where possible, back up **both** DB files before any schema bump.

*Source: `memory/db_schema_v28_split.md`, `PATHS.md` (config_snapshots, gtt_state, schema-failfast).*

---

## 9. CONFIGURATION PHILOSOPHY

- **YAML drives behavior extensively.** `config/system_config.yaml` (master: trade_type,
  trading hours, risk knobs, webhook, flags), `config/strategies/*.yaml` (15 strategies:
  enabled, intent, params), `config/cron_registry.yaml` (the *executable* source of truth for
  cron), `config/accounts.csv`, `config/broker_limits.yaml`, `config/security.yaml`.
  New features are almost always **flag-gated in YAML, default OFF**.

- **What requires code changes:** anything structural — new gates, new order protocols, new
  schema, new pipeline stages. Config toggles a behavior that already exists in code; it can't
  invent one.

- **What must NEVER be manually edited:**
  - The **live crontab** — it's generated from `cron_registry.yaml`; edit the registry and
    regenerate (post-receive auto-installs). Manual edits drift and get overwritten.
  - Anything the **Config Sanity Auditor** would flag as contradictory (it will refuse to start).
  - The **overlay secrets file** on the VM (`gui_config.local.yaml`, `.env`) — managed out of
    band, chmod 600, never in git.
  - Config in a way that violates the single-source rule (e.g. duplicating a value the code
    derives).

Config-as-it-ran is snapshotted daily to `config_snapshots` so reports read the exact config a
given day used, DB-purely.

*Source: `PATHS.md` (Master config, Self-maintaining cron, Config snapshots, Config Sanity Auditor).*

---

## 10. REPORTING PHILOSOPHY

**Reports generated:**
- **Daily Trade Review** (`reports/daily_trade_review.py`, @16:07) — the primary forensic
  workbook: Dashboard · Reconciliation · Orders · Signals · Strategies · Slippage · Config
  (7 sheets, DB-pure). The Dashboard is engineered so headlines *cannot disagree* with detail.
- **EOD reconciliation** (local `eod_verify` today; broker-authoritative `eod_broker_reconcile`
  in shadow, cutover ~20-Jul).
- **Cron Officer briefings** (09:20 strategy status, EOD summary), **Control Tower** daily
  health report, **preflight** readiness sentinels.
- **AI operations reports** (Gemini/Antigravity): premarket brief, live watchman notes, EOD
  log review, trade coaching, data-integrity checks, weekly patterns.
- **Flow trace** — a per-day "flight recorder" (`reports/flow_trace/`) of the full pipeline.

**Consumers:** Rama (primary). The AI layer consumes prior reports to produce the next
(briefings read yesterday's EOD; weekly reads five days of notes). Reports are also the intended
future training corpus for a personalized model.

**How used to improve trading:** the Strategies sheet ranks strategies over a trailing window
with a confidence-weighted composite (so a low-sample strategy can't top the board); slippage
decomposition (entry+SL−TGT) localizes execution cost; MFE/MAE excursions grade entry/exit
timing; the Coach surfaces recurring loss patterns.

*Source: `PATHS.md` (Daily Trade Review), `GEMINI.md` (roles 4 & 8).*

---

## 11. LOGGING PHILOSOPHY

**Always logged:** every signal received, every screen PASS/REJECT with reason, every capital
reserve/release with balances, every order placed/filled with slippage, every SL/TGT placement
and trailing move, every close with exit reason + P&L + time-in-trade, every daily running total,
every kill-switch/naked/oversell/CRITICAL event, every cron heartbeat, every config snapshot.

**What constitutes the audit trail:** the *joinable persisted chain* signal → screener_results →
trade → orders → fills → excursions → reconciliation → config_snapshots, plus the structured
one-line-per-event flow trace and the daily log files (`logs/system_YYYY-MM-DD.log`,
`reconciler_*.log`, `trades_*.log`, `cron-*.log`). Alerts leave durable sentinel flags; email is
the record of record for critical alerts.

Logging rules: **one line per event during live tracing, timestamped, sourced, severity-tagged**
(CRITICAL/WARN/INFO); never silently swallow an error (a past bug was a silent DEBUG swallow —
removed); log rotation is retention-only (`FileHandler`, not `RotatingFileHandler`, per an
operational fix); **no false alerts on non-trading days.**

*Source: `AGENTS.md` (role 9 event vocabulary), `memory/feedback_log_rotation_fix.md`.*

---

## 12. DEPLOYMENT PHILOSOPHY

**Two machines, clear split:**
- **PC (`D:\Projects\trading-system`)** — the **developer machine and source of truth for code**.
  All editing, testing, committing happen here. Static analysis (SATS) is PC-only.
- **Oracle VM (`161.118.187.249`, user `ubuntu`, IST)** — the **runtime**. It runs the services,
  holds the live DB, tokens, secrets, and reports. Its working tree is **not a git repo** — it's
  a checkout produced by a post-receive hook.

**Source of truth for CODE = the PC / `origin` (the VM bare repo `~/trading-system.git`).**
**Source of truth for RUNTIME STATE = the VM** (DB, tokens, `.env`, GTT state).

**How deployments work:**
1. Commit on PC → `git push origin main` (origin is the VM's bare repo over SSH, no public forge).
2. The **post-receive hook** checks out the tree into the running dir and auto-installs the
   canonical crontab *iff* `generate(registry) == canonical`.
3. **Deploy ≠ restart.** Code on disk doesn't run until `sudo systemctl restart trading-system.service`.
   Restarts are **market-gated** — never 09:15–15:30; deferred to the next 08:15 auto-boot or an
   off-market window. `scripts/deploy_preflight.py` refuses a mid-session deploy.
4. Overlay secrets survive `checkout -f` (git-ignored, chmod 600, deep-merged).
5. Every risky deploy has a **rollback anchor/tag** and a backup of both DB files first.

Schema bumps apply at the next boot; new behaviors ship default-OFF and shadow before cutover.

*Source: `PATHS.md` (VM/PC tables, Activate deployed code, Self-maintaining cron), `DEPLOYMENT.md`,
`memory/project_vm_architecture_locked.md`, `memory/token_workflow_confirmed_21jun.md`.*

---

## 13. AI WORKFLOW

| Actor | Role | When to use |
|---|---|---|
| **Web Claude** | Architecture & design discussions; long-form design docs; the "design authority" for how a change should be built. | Design decisions, trade-offs, reviewing screenshots/output.txt/architecture. |
| **VS Code / Claude Code (PC)** | **Code-modification authority.** Writes, edits, tests, commits, and (when Rama authorizes) pushes/deploys. Runs the audits and builds the features. | Any actual code change, build, test, deploy, investigation in the repo. |
| **ChatGPT** | Second-opinion reviewer / reasoning partner; consumes screenshots, output.txt, Claude discussions, architecture docs and gives structured review. (This document onboards it.) | Cross-checking a design or an output; not a code-committer. |
| **Gemini / Antigravity (`agy`, on VM)** | **Senior Operations AI** — CEO/Ops-Manager/Auditor/Watchman/Coach/Security/Co-pilot. **Observe, audit, brief only.** Read anything; write only to `reports/` + `docs/` (audit); **never** code, git, systemctl, or orders. | Live monitoring, EOD reviews, data-integrity, answering operational Q&A. Backup analyst when Claude is unavailable. |
| **User (Rama)** | The **only authorized human**; final decision-maker; the one who pushes/deploys/restarts and rotates credentials. | All authorization, all go/no-go gates, all human sign-offs (e.g. GUI browser review). |

Hard boundary shared by the operations AIs: **read-anything, write-only-reports/docs, never
touch code/config/db-writes/services/orders/secrets.**

*Source: `AGENTS.md`, `GEMINI.md`.*

---

## 14. DECISION RULES (conflict resolution)

- **Rama has final authority — always.** No AI overrides him; no AI acts without his gate on
  anything irreversible (deploy, restart, credential rotation, live cutover).
- **When AI tools disagree:** surface the disagreement with evidence (cite files/logs/DB), let
  Rama decide. The operations AIs explicitly escalate design conflicts to Web Claude and
  code needs to VS Code Claude rather than resolving them unilaterally.
- **Escalation ladder (Gemini/Antigravity):** info question → answer from observation;
  config question → report + Rama decides; code change → "needs VS Code Claude"; architecture
  → "needs Web Claude"; security incident → CRITICAL Telegram immediately.
- **On safety, the conservative path wins by default** — defer/hold rather than act, keep the
  reservation rather than release, alert rather than silently proceed.
- **Single-source-of-truth resolves data conflicts:** if two components disagree on a fact, the
  designated authority (the resolver, the kill-switch, the ledger, the broker) is correct and
  the other is the bug.

*Source: `AGENTS.md` (Interaction Rules), `GEMINI.md` (Escalation Path).*

---

## 15. FUTURE ROADMAP (6–12 months, no implementation detail)

- **Live micro-capital cutover** — migrate from paper to real money at small size, then scale.
- **Delivery (CNC) activation** — pass the T2 end-to-end live GTT proof, then enable overnight
  positional strategies (3 of the 15 are delivery-intent, currently dormant).
- **Broker-authoritative EOD reconciliation** — cut `eod_broker_reconcile` from shadow to
  authoritative and retire the local-only `eod_verify`.
- **Unrealized-MTM daily-loss enforcement** — flip the B-1 shadow to enforcing.
- **S&R V2 features** — activate retest-entry and structure-aware exit (both built, default-OFF)
  after calibration (V1 calibration checkpoint ~mid-July).
- **GUI redesign completion** — finish the screen-by-screen Mission-Control redesign, and
  eventually a (read-only-first) control plane.
- **Network hardening** — Nginx/TLS reverse proxy, webhook IP allowlist + HMAC, port lockdown.
- **Personalized model** — long-term, train a small LLM on the accumulated reviews/notes/outcomes.

*Source: `PATHS.md` (Deferred board, B-1/P1/SNR sections), `memory/MEMORY.md` (Deferred board).*

---

## 16. THINGS CHATGPT SHOULD ALWAYS REMEMBER

1. **One human (Rama) is the only authority.** Nothing irreversible happens without his gate.
2. **Safety outranks profit.** Correctness and capital preservation are constraints, not
   trade-offs.
3. **Paper == Live** — same code path; mode is a data column. Every change checks both.
4. **PC is source-of-truth for code; VM is source-of-truth for runtime state.**
5. **Deploy ≠ restart**, and restarts are **market-gated** (never 09:15–15:30).
6. **SQLite is the system of record** (two files, ATTACHed; schema v42; fail-fast on mismatch).
7. **New behavior ships default-OFF → shadow → soak → cutover**, with a rollback anchor.
8. **Single source of truth per fact** — resolver, kill-switch, ledger, adapter chokepoints.
9. **Signals come from Chartink; the broker is Zerodha; timezone is IST.**
10. **The operations AIs observe/audit/brief only** — never code, orders, config, or services.
11. **Read `SYSTEM_MAP.md` + `PATHS.md` before reasoning about the system** — they're the
    living map and they change often.

## 17. THINGS CHATGPT SHOULD NEVER ASSUME

- **Don't assume it's live-trading real money** — it's paper today, migrating carefully.
- **Don't assume delivery/CNC is active** — it's built but flag-gated OFF (dormant).
- **Don't assume a deploy is running** — code on disk ≠ running; a restart is required and is
  market-gated.
- **Don't assume the VM working tree is a git repo** — it's a post-receive checkout; git ops
  happen on the PC / bare repo.
- **Don't assume you can edit the crontab directly** — it's generated from `cron_registry.yaml`.
- **Don't assume one DB file** — there are two, ATTACHed; raw reads must use `core.db_connect`.
- **Don't assume the schema version** from an old doc (`AGENTS.md`/`GEMINI.md` say v24 — that's
  stale; it's **v42**). Always check `schema_meta` / `EXPECTED_SCHEMA_VERSION`.
- **Don't assume any config value is safe to hand-edit** — the Config Sanity Auditor may refuse
  startup; single-source rules may be violated.
- **Don't assume an AI can act** — Gemini/Antigravity/heartbeat-Claude cannot touch code, orders,
  services, or write the DB.
- **Don't propose destructive/one-shot fixes** — the culture is additive, reversible, shadowed.
- **Don't invent numbers** — if you don't have the value, say so and point to the source file.

## 18. RESPONSE EXPECTATIONS (review format)

When Rama uploads screenshots, `output.txt`, a Claude discussion, or an architecture doc, respond in this shape:

1. **One-line verdict** — is it correct / safe / ready? (GO / NO-GO / NEEDS-INFO.)
2. **What I checked** — cite exactly what you read (file, line, log timestamp, DB table).
3. **Findings** — one per line, severity-tagged `[CRITICAL] / [WARN] / [INFO]`, most severe
   first, specific numbers/symbols/timestamps. No padding.
4. **Safety/parity impact** — does it affect capital, naked-position risk, paper/live parity,
   or a single-source-of-truth invariant?
5. **What's missing / assumptions** — flag anything you couldn't verify; never fill a gap with a
   guess.
6. **Recommendation** — concrete next step (and *which actor* should do it: VS Code Claude for
   code, Web Claude for design, Rama for authorization).

Honesty rules: if tests failed, say so with the output; if a step was skipped, say that; "All
clear" when nothing is found — don't manufacture issues.

*Source: `AGENTS.md`/`GEMINI.md` output standards; `memory/feedback_*` (honesty, brevity).*

## 19. SUCCESS CRITERIA (what makes a good ChatGPT recommendation)

- **Grounded** — cites the actual file/log/table, not a memory or a guess.
- **Safety-first** — explicitly reasons about capital, naked positions, and parity before profit.
- **Respects single-source-of-truth** — doesn't propose a duplicate/parallel mechanism.
- **Additive & reversible** — default-OFF, shadow, rollback-anchored; no big-bang cutovers.
- **Correctly routed** — names the right actor (Rama authorizes, Claude codes, ops-AI observes).
- **Honest about uncertainty** — flags what it couldn't verify; distinguishes fact from inference.
- **Specific and actionable** — numbers, symbols, timestamps, a concrete next step.
- **Aware of state** — knows it's paper, delivery-dormant, schema v42, market-gated deploys.

## 20. ADDITIONAL NOTES

- **Timezone traps:** always reason in IST. Never trust `TZ='Asia/Kolkata'` on the MSYS2 dev
  PC (it silently returns UTC); the app itself uses a fixed +5:30 `now_ist()`.
- **The 15 strategies:** 12 intraday (gap_go/gap_fade/first_pullback/range_breakout/vwap/
  open_high-low ± long/short) + 3 positional/delivery-intent (`positional_momentum_long`,
  `positional_swing_long`, `positional_sector_rotation`). Under `trade_type=INTRADAY` the 3
  delivery-intent strategies are **dormant** (declared intent preserved but gated) — currently
  12 WILL / 3 WON'T by design.
- **Docs age fast.** `AGENTS.md`/`GEMINI.md` were written when the system was schema v24 and
  pre-migration; treat their *roles/boundaries* as current but their *numbers* (schema, mode,
  temp config) as historical. `SYSTEM_MAP.md` + `PATHS.md` are the living truth.
- **The webhook token is shared across all 15 scanners**, loopback-bound today, with per-IP
  rate limiting; network hardening (bind, HMAC, allowlist, TLS) is a pending decision, not yet
  applied.
- **Test suite:** ~254 test modules; changes are expected to keep it green (a handful of
  Windows-PC-only env failures are a known baseline, green on the VM).
- **When in doubt, ask Rama** — he is the single point of authorization and the only human in
  the loop.

---

*End of clarification. Re-verify anything time-sensitive against `docs/SYSTEM_MAP.md` and
`PATHS.md`, which are updated continuously.*
