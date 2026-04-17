# Trading System v2 — Consolidated Design Specification

> **Status:** LOCKED — produced after a full audit of the existing system, gap analysis against two prior v2 design docs, and 31 deliberate design decisions made in dialogue with the system owner.
>
> **Authority:** This document is the single source of truth for the v2 rebuild. Where this document and any earlier v2 doc disagree, this document wins. Where this document and the audit doc disagree, this document wins (the audit's purpose was diagnostic, not prescriptive).
>
> **Companion file:** [`locked_decisions.yaml`](./locked_decisions.yaml) is the machine-readable index of all 31 design decisions with rationale and alternatives. Every section in this document references decisions by their ID (G1, G2a, P7a, etc.).
>
> **Document version:** 1.0 — 2026-04-14
> **Total locked decisions referenced:** 31 (10 G + 3 Q + 18 P)
> **Project owner:** Rama (single developer + trader)

---

# Table of Contents

1. [Foreword — How to Read This Document](#1-foreword)
2. [Folder & File Tree](#2-folder-and-file-tree)
3. [Inter-Module Dependency Map](#3-inter-module-dependency-map)
4. [Startup Sequence (4 Scenarios)](#4-startup-sequence)
5. [Per-File Specifications](#5-per-file-specifications) *(Turn 2)*
6. [End-to-End Trade Flow](#6-end-to-end-trade-flow) *(Turn 2)*
7. [Database Schema](#7-database-schema) *(Turn 2)*
8. [Configuration Files](#8-configuration-files) *(Turn 3)*
9. [Build Phase Order](#9-build-phase-order) *(Turn 3)*
10. [Validation Checklist](#10-validation-checklist) *(Turn 3)*

---

# 1. Foreword

## 1.1 Why This Rebuild Exists

The previous trading system has been rebuilt three times. Each rebuild fixed surface bugs from the previous version without addressing structural gaps. The pattern is now identifiable: complexity was added faster than correctness was validated, every iteration carried forward unstated assumptions, and the existing audit document caught the symptoms but not the architectural causes.

This document exists because the fourth rebuild needs to be the last one. Not because v2 will be perfect — it will not — but because the design decisions made here are deliberate, documented, and auditable. When something needs to change in v2.1, the change will be traceable back to a specific decision that no longer fits, rather than to a vague feeling that "the system needs to be cleaner."

## 1.2 Three Things to Internalize Before Reading Further

Three principles shape every other decision in this document. If you internalize these three, the rest of the design follows naturally. If you fight any of them, the system will drift back into the patterns that caused the existing system's problems.

**1. The broker is the source of truth.**
Local state is a cache. Every meaningful operation (capital, position, order) is verified against the broker via reconciliation. When local state and broker state disagree, **the broker wins by default.** This is the inverse of the existing system, where local state was treated as truth and the broker was a fallback verification source. The four-scenario startup model (G5) and the hybrid reconciler (G1) are both expressions of this principle.

**2. Identity flows through the system as `signal_id → trade_id → order_id`.**
Three different IDs for three different needs (audit trail, dedup, broker reference). Every log line that involves a trade activity carries the relevant IDs. Every database row that represents a trade activity is linked to these IDs. Every report row can be traced back to the originating webhook. This is the foundation that makes debugging a losing day possible. Without it, you are guessing.

**3. The bin card invariant must hold at all times.**
Capital math is enforced inside every transaction that touches capital. The invariant — `margin_used + margin_reserved + margin_available = cash_floor + min(0, realized_intraday_pnl)` — is checked anchored to the day's starting cash, not relative to the previous row. Violations are detected immediately and the transaction is rolled back. This single property eliminates an entire class of bugs that the existing system carried for a year.

## 1.3 What This Document Is, and Is Not

**This document IS:**
- A complete design specification for v2, written so that someone else (or future-you) could build it from scratch
- The single authoritative source for every architectural decision
- A bug-fix bridge from the existing system, with footnotes connecting v2 modules to the audit findings they address
- A build phase plan with deliverables and exit criteria per phase
- A validation checklist that defines "ready for paper trading" and "ready for live trading"

**This document IS NOT:**
- An implementation guide. It does not contain code beyond illustrative snippets. Implementation is the next stage.
- A user manual. It does not document how to operate the system day-to-day.
- A backtest specification. Backtesting is deferred to v2.1 (the rationale is documented in the locked decisions).
- A trading-strategy document. Strategy logic lives in the strategy YAMLs and the screening pipeline; this document defines the system that runs strategies, not the strategies themselves.

## 1.4 Cross-Cutting Principles (Apply Everywhere)

Six principles apply to every file, every module, every decision in this document. They are not mentioned in every section because that would be repetitive, but they are always in force.

| Principle | Statement | Source |
|---|---|---|
| **No encryption** | Plain SQLite, plain text secrets in `.env` (gitignored). No SQLCipher, no Fernet, no encryption of files in transit beyond SSH transport. | Q2 |
| **Config Honor** | Every limit in config has a runtime check, an alert when reached, and a daily report row. No "namesake" config values. | P2 |
| **Broker is truth** | Local state is a cache of broker state, verified continuously, broker wins on disagreement. | G1, P7a |
| **Fail fast, no silent** | Every exception either logs full traceback and raises, OR is explicitly handled with documented recovery. No bare except, no try/except/log/continue patterns. | Foundation Rule 1.6 + Rule 4 |
| **Pre-check before change** | Before adding/modifying any code, search the repo for the concept name. Report what exists. Then propose the change. Phase 0 of every code task. | P3 |
| **Identity traceability** | Every log line, database row, and Telegram alert involving trade activity carries `signal_id`, `trade_id`, and `order_id` when applicable. | G2a |

These principles are repeated as a permanent block at the top of `CLAUDE.md` so they cannot be skipped during development.

## 1.5 What the 31 Locked Decisions Cover

The 31 decisions are grouped into three sets:

- **G1–G10** — Ten v2 design gaps identified during review of the prior v2 design docs. These are the architectural decisions that the prior docs left unspecified and that, if not pinned down, would have produced v2 bugs identical to v1 bugs.

- **Q1–Q3** — Three product-level confirmations: max_open_positions default (10), no encryption anywhere (Q2), and AI/LLM review deferred to v2.1 (Q3).

- **P1–P18** — Eighteen architecture review points raised by the system owner from direct experience with the existing system. These cover trading hours, capital model, order types, reporting, and a dozen other concrete improvements.

Every section in this document references decisions by ID. The full table with rationale lives in `locked_decisions.yaml`.

## 1.6 What Is Deferred to v2.1 (Explicitly)

These are intentional out-of-scope items for v2. They are not forgotten — they are deferred with reasoning:

- **Scaling entry (40/30/30 multi-leg).** Schema and `EntryEngine` abstraction land in v2; the actual `ScalingEntryEngine` class is v2.1. Reasoning: G10. Migration to scaling later is additive (new class, config flag), not destructive.
- **AI/LLM daily trade review.** Trade log structure in v2 designed to make later LLM integration a 50-line script. Reasoning: Q3.
- **Backtesting harness.** Build only after v2 is live and stable. A backtest harness that doesn't exactly mirror live execution is worse than no backtest (false confidence). Reasoning: earlier discussion.
- **Multi-broker routing.** Broker abstraction is in place; AngelOne adapter is not built. Reasoning: focus v2 on getting Zerodha right.
- **SQLCipher / database encryption.** Permanently deferred per Q2.

## 1.7 What "Done" Looks Like

v2 is "done" when:
1. All 12 build phases (Section 9) are complete with their exit criteria met.
2. The pre-live validation checklist (Section 10) has every box checked.
3. v2 has run 5 full market days in paper mode without crash or capital invariant violation.
4. Paper P&L matches manual contract-note calculations to within rounding.
5. The owner is confident enough to start live at ₹50,000 risk capital.

This document does not say "v2 is done when it is bug-free." That is impossible. v2 is done when it is *correct in the structural ways the existing system was not* and *transparent enough to debug when it fails.*

---

# 2. Folder and File Tree

This is the complete v2 folder structure. Every file mentioned anywhere else in this document is listed here. Files marked `[NEW]` did not exist in the existing system. Files marked `[REBUILD]` existed but are being rewritten from scratch. Files marked `[PORT]` are being copied with cleanup but their core logic is sound.

```
trading-system-v2/
│
├── core/                                # Foundation layer — every other layer depends on this
│   ├── __init__.py
│   ├── state_store.py             [NEW] # SQLite WAL — central state, transactional, atomic
│   ├── events.py                  [NEW] # Tiny EventBus for 4 fan-out events ONLY (G9)
│   ├── time_authority.py          [NEW] # Single now() source + skew detection (G4)
│   ├── market_windows.py          [NEW] # Single source of truth for ALL market times (P1)
│   ├── logger.py               [REBUILD]# Structured logging with signal_id/trade_id/order_id
│   ├── config_loader.py           [NEW] # Pydantic-validated YAML loading + hash diff (G4 prereq)
│   ├── exceptions.py              [NEW] # Custom exception hierarchy (CapitalInvariantViolation, etc.)
│   └── ids.py                     [NEW] # UUID generation helpers + ID format/parse
│
├── broker/                              # Broker abstraction layer
│   ├── __init__.py
│   ├── base_broker.py             [NEW] # Abstract interface (BrokerBase)
│   ├── zerodha_adapter.py      [REBUILD]# Zerodha implementation with rate limiter wrapping
│   ├── rate_limiter.py            [NEW] # Token bucket per endpoint category (G7)
│   ├── product_resolver.py        [NEW] # Generic product → broker code (eliminates hardcoding)
│   └── cost_calculator.py      [REBUILD]# Brokerage + STT + GST + slippage (P12)
│
├── auth/                                # Authentication
│   ├── __init__.py
│   ├── zerodha_login.py           [PORT]# Token fetch (PC-side script)
│   ├── token_manager.py        [REBUILD]# Token validity + auto-refresh + token-account binding
│   └── account_selector.py        [PORT]# Interactive account selection
│
├── capital/                             # Capital & risk management
│   ├── __init__.py
│   ├── fund_manager.py         [REBUILD]# 3-balance model with T+1 settlement (P7a)
│   ├── position_sizer.py       [REBUILD]# 4-layer sizing with binding-layer logging (P10)
│   ├── risk_engine.py             [NEW] # Portfolio-level risk + sector concentration + max positions
│   ├── kill_switch.py          [REBUILD]# RLock + last-mile + 4 halt types
│   └── invariant.py               [NEW] # Bin card invariant (G3) — assert_capital_invariant()
│
├── data/                                # Market data
│   ├── __init__.py
│   ├── candle_store.py         [REBUILD]# Incremental fetch + cache; lock released during HTTP
│   ├── live_feed.py            [REBUILD]# LTP-only candle building + clock thread + reconnect (G6)
│   ├── instrument_cache.py        [PORT]# NSE instrument list with daily refresh
│   ├── vwap_calculator.py         [PORT]# Market-hours filtered (with bug fixes)
│   ├── atr_calculator.py          [PORT]# ATR with explicit failure logging
│   ├── candle_calculator.py       [PORT]# OHLC structure + EMA + RSI + swing detection
│   ├── symbol_validator.py        [PORT]# F&O check + sector lookup + rename resolution
│   └── market_hours_guard.py   [REBUILD]# Reads from market_windows.py (P1)
│
├── screening/                           # Signal quality assessment
│   ├── __init__.py
│   ├── secondary_screener.py   [REBUILD]# 10 steps, fixed bugs, weights from config (P9a, P9b)
│   ├── step_executor.py           [NEW] # Step-by-step runner with failure semantics (P18)
│   ├── quality_scorer.py       [REBUILD]# Thin wrapper, all weights config-driven (P9b)
│   └── entry_gate.py           [REBUILD]# Price-based pullback in worker thread (P11a)
│
├── signals/                             # Signal ingestion & processing
│   ├── __init__.py
│   ├── webhook_receiver.py     [REBUILD]# HMAC + dedup + expiry + 503 backpressure (G2a, P6, P15, P16)
│   ├── signal_validator.py        [NEW] # signal_id assignment + dedup fingerprint + expiry check
│   ├── signal_processor.py     [REBUILD]# Async dispatch via ThreadPoolExecutor; calls position_sizer
│   └── signal_queue.py         [REBUILD]# 300-cap queue with backpressure threshold
│
├── orders/                              # Order lifecycle
│   ├── __init__.py
│   ├── entry_engine.py            [NEW] # Abstract base — entry placement strategy (G10)
│   ├── full_entry_engine.py       [NEW] # Single-shot entry implementation (G10 default)
│   │                                    # NOTE: scaling_entry_engine.py reserved for v2.1
│   ├── order_placer.py         [REBUILD]# Calls entry_engine; protocol-aware (CO_PLUS_TGT vs LIMIT_TRIPLE)
│   ├── order_protocol_co.py       [NEW] # CO bracket + separate LIMIT TGT (P8/P13 default)
│   ├── order_protocol_limit.py    [NEW] # 3-LIMIT fallback (P8/P13 fallback)
│   ├── order_manager.py           [NEW] # State machine: PENDING→PLACED→FILLED→EXIT
│   ├── order_monitor.py        [REBUILD]# 15s poll, race-free with smart_tgt (P14)
│   ├── order_reconciler.py     [REBUILD]# Hybrid cadence (G1) + 6 checks + 3-tier action
│   ├── order_timeout.py        [REBUILD]# Cancel + verify + force-exit partials (P11b)
│   ├── smart_tgt_manager.py    [REBUILD]# State updated AFTER broker confirmation (Ghost SL fix)
│   └── eod_squareoff.py        [REBUILD]# 15:17 IST staggered MARKET orders (P1)
│
├── strategies/                          # Strategy configurations + loader
│   ├── __init__.py
│   ├── schema.py                  [NEW] # Pydantic schema for strategy YAMLs
│   ├── loader.py                  [NEW] # Validates all 15 YAMLs at startup; refuses to start on invalid
│   ├── intraday/                        # 12 intraday strategies (regenerated from template)
│   │   ├── open_low_breakout_long.yaml
│   │   ├── first_pullback_long.yaml
│   │   ├── vwap_bounce_long.yaml
│   │   ├── gap_go_long.yaml
│   │   ├── gap_fade_long.yaml
│   │   ├── range_breakout_long.yaml
│   │   ├── open_high_breakdown_short.yaml
│   │   ├── first_pullback_short.yaml
│   │   ├── vwap_rejection_short.yaml
│   │   ├── gap_go_short.yaml
│   │   ├── gap_fade_short.yaml
│   │   └── range_breakout_short.yaml
│   └── positional/                      # 3 positional strategies
│       ├── positional_momentum_long.yaml
│       ├── positional_sector_rotation.yaml
│       └── positional_swing_long.yaml
│
├── alerts/                              # Notifications
│   ├── __init__.py
│   ├── telegram_notifier.py    [REBUILD]# Send only, with 4-tier severity (G8)
│   └── critical.py                [NEW] # CRITICAL alert sentinel file pattern (G8)
│
├── reports/                             # Post-market analysis
│   ├── __init__.py
│   ├── daily_review.py         [REBUILD]# Multi-sheet Excel orchestrator
│   ├── report_builder.py       [REBUILD]# Per-sheet builders
│   ├── trade_logger.py         [REBUILD]# Per-trade JSON + CSV with all IDs
│   ├── ml_feature_logger.py       [PORT]# ML training row per trade
│   └── signal_funnel.py           [NEW] # Signal handling visibility report (P16)
│
├── utils/                               # Shared utilities
│   ├── __init__.py
│   ├── lock_manager.py            [PORT]# Process-level lock (prevents double run)
│   ├── path_config.py             [PORT]# All paths via pathlib.Path
│   ├── startup_checks.py       [REBUILD]# 4-scenario detection + invariants (G5a)
│   ├── data_retention.py          [PORT]# Log/data retention with backup-before-delete
│   └── file_validator.py          [PORT]# Foundation Rule 3.4 file validation
│
├── scripts/                             # Operational scripts
│   ├── __init__.py
│   ├── morning_auth.py            [PORT]# Daily token fetch (PC-side)
│   ├── daily_review_runner.py  [REBUILD]# Triggered by cron at 16:05 IST
│   ├── alert_watcher.py           [NEW] # Sentinel file → email watcher (G8)
│   ├── preflight_scanner_check.py [NEW] # P17 lightweight pre-flight check
│   └── recovery_helper.py         [NEW] # Manual recovery utility for crash scenarios
│
├── config/
│   ├── system_config.yaml      [REBUILD]# Master config — every limit honored (P2)
│   ├── broker_costs.yaml       [REBUILD]# Brokerage rates per broker
│   ├── broker_limits.yaml         [NEW] # Token bucket rates (G7)
│   ├── slippage_model.yaml        [NEW] # Per-tier slippage in bps (P12)
│   ├── scoring_weights.yaml       [NEW] # All screener/scorer weights (P9b)
│   ├── scan_webhook_map.yaml   [REBUILD]# Chartink scanner → strategy mapping
│   ├── chartink_scanners.yaml     [NEW] # Canonical scanner URLs for P17
│   ├── nse_holidays_2026.yaml     [PORT]# Holiday calendar
│   └── daily_capital.csv          [PORT]# Capital fallback (CSV)
│
├── data_store/                          # Runtime data (gitignored)
│   ├── trading_system.db                # SQLite WAL database (the source of truth)
│   ├── trading_system.db-wal            # WAL file
│   ├── trading_system.db-shm            # Shared memory
│   ├── critical_alert_*.flag            # Sentinel files (G8) — pre-delivery
│   ├── critical_alert_*.delivered       # Sentinel files — post-delivery
│   └── failed_alerts.log                # ERROR-tier alert failures
│
├── logs/                                # Append-only daily log files (gitignored)
│   ├── system_YYYY-MM-DD.log
│   ├── trades_YYYY-MM-DD.log
│   ├── reconciler_YYYY-MM-DD.log
│   └── debug_YYYY-MM-DD.log
│
├── reports/daily_review/                # Generated Excel reports (gitignored)
│   └── YYYY-MM-DD_review.xlsx
│
├── tests/                               # Unit + integration tests
│   ├── __init__.py
│   ├── unit/
│   │   ├── test_capital_invariant.py
│   │   ├── test_position_sizer.py
│   │   ├── test_screener_steps.py
│   │   ├── test_time_authority.py
│   │   └── test_state_store.py
│   ├── integration/
│   │   ├── test_signal_to_order.py
│   │   ├── test_recovery_scenarios.py
│   │   └── test_paper_live_parity.py
│   └── chaos/
│       ├── test_api_failure.py
│       ├── test_websocket_drop.py
│       └── test_kill_mid_position.py
│
├── main.py                     [REBUILD]# Entry point — wires all phases, 4-scenario startup
├── requirements.txt            [REBUILD]# Pinned exact versions
├── .env                                 # Secrets (gitignored, plain text per Q2)
├── .gitignore                     [PORT]# Standard Python ignore + secrets + db + logs
├── CLAUDE.md                   [REBUILD]# Project context with cross-cutting principles
└── README.md                      [NEW] # Setup + run instructions

# ─────────────────────────────────────────────────────────────────────────────
# REMOVED FROM EXISTING SYSTEM (do not carry forward to v2):
#
#   app/                              # 38 dead stub files — ABANDONED EARLIER ATTEMPT
#   archive/                          # Old code archive — not needed
#   morning/morning_config_checker.py # Replaced by core/config_loader.py + startup_checks
#   integration_test.py               # Will be replaced by tests/integration/
#   encrypt_secrets.py                # No encryption per Q2
#   trade_analyser.session            # Old session file
#   telegram_exporter.py              # Standalone tool — not part of trading system
# ─────────────────────────────────────────────────────────────────────────────
```

**File count summary:**
- Core: 9 files (8 NEW, 1 REBUILD)
- Broker: 6 files (5 NEW, 1 REBUILD)
- Auth: 4 files (2 PORT, 1 REBUILD, 1 NEW boilerplate)
- Capital: 6 files (3 REBUILD, 2 NEW, 1 NEW boilerplate)
- Data: 9 files (5 PORT, 3 REBUILD, 1 NEW boilerplate)
- Screening: 5 files (2 REBUILD, 2 NEW, 1 NEW boilerplate)
- Signals: 5 files (3 REBUILD, 1 NEW, 1 NEW boilerplate)
- Orders: 12 files (4 REBUILD, 7 NEW, 1 NEW boilerplate)
- Strategies: 18 files (15 YAMLs regenerated + 2 NEW Python + 1 NEW boilerplate)
- Alerts: 3 files (1 REBUILD, 1 NEW, 1 NEW boilerplate)
- Reports: 6 files (3 REBUILD, 2 PORT, 1 NEW)
- Utils: 6 files (4 PORT, 1 REBUILD, 1 NEW boilerplate)
- Scripts: 6 files (1 PORT, 1 REBUILD, 3 NEW, 1 NEW boilerplate)
- Config: 9 files (2 PORT, 4 REBUILD, 3 NEW)
- Top-level: 6 files (3 REBUILD, 2 PORT, 1 NEW)

**Total v2 Python files:** ~80 (vs existing system's 112, with the ~38 dead `app/` files removed)

**Dead code being deleted from existing system:** 38 `app/` files + ~10 morning/integration test files = 48 fewer files than the current count.

---

# 3. Inter-Module Dependency Map

## 3.1 The Layer Rule

v2 has a strict layered architecture. Imports flow **downward only**. A module in a higher layer may import from any layer below it. A module in a lower layer **never** imports from a higher layer. This eliminates the circular import problems that plague the existing system and makes module testing tractable.

The layers, top to bottom:

```
┌─────────────────────────────────────────────────────────────┐
│  LAYER 6 — ENTRY POINTS                                      │
│  main.py · scripts/* · webhook_receiver.py                   │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 5 — APPLICATION                                       │
│  signals/* · screening/* · orders/*                          │
│  reports/* · alerts/*                                        │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 4 — CAPITAL & RISK                                    │
│  capital/* (fund_manager, position_sizer, risk_engine,       │
│             kill_switch, invariant)                          │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 3 — BROKER & DATA                                     │
│  broker/* · data/* · auth/*                                  │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 2 — STRATEGIES                                        │
│  strategies/schema.py · strategies/loader.py                 │
│  (YAML files are pure data, not in import graph)             │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 1 — CORE                                              │
│  core/* — state_store · events · time_authority ·            │
│  market_windows · logger · config_loader · exceptions · ids  │
│                                                              │
│  utils/* — pure helpers, no dependencies on anything above   │
└─────────────────────────────────────────────────────────────┘
```

**The rule:** if you find yourself wanting to import a higher-layer module from a lower-layer module, the design is wrong. The fix is either to move the function to a lower layer, or to inject the dependency via a function parameter at runtime.

## 3.2 Specific Inter-Module Dependencies

This subsection lists every important dependency. It is not exhaustive (every module imports from `core/logger` and `core/exceptions`), but it covers every dependency that matters for understanding the design.

### 3.2.1 Layer 1 (Core) — depends on nothing in this codebase

```
core/state_store.py    →  (sqlite3, threading) [stdlib only]
core/events.py         →  (threading) [stdlib only]
core/time_authority.py →  (datetime, zoneinfo) [stdlib only]
core/market_windows.py →  core/time_authority
core/logger.py         →  (logging) [stdlib only]
core/config_loader.py  →  (yaml, pydantic, hashlib)
core/exceptions.py     →  (no imports)
core/ids.py            →  (uuid) [stdlib only]
```

### 3.2.2 Layer 2 (Strategies)

```
strategies/schema.py  →  (pydantic)
strategies/loader.py  →  strategies/schema, core/config_loader, core/exceptions
```

### 3.2.3 Layer 3 (Broker & Data)

```
broker/base_broker.py      →  core/exceptions
broker/rate_limiter.py     →  core/time_authority
broker/zerodha_adapter.py  →  broker/base_broker, broker/rate_limiter,
                              core/logger, core/exceptions
broker/cost_calculator.py  →  core/config_loader (loads broker_costs.yaml)
broker/product_resolver.py →  core/config_loader

auth/token_manager.py      →  broker/zerodha_adapter, core/state_store
auth/account_selector.py   →  auth/token_manager, core/state_store
auth/zerodha_login.py      →  (kiteconnect) [external]

data/instrument_cache.py   →  broker/zerodha_adapter, core/state_store
data/candle_store.py       →  broker/zerodha_adapter, core/state_store,
                              core/time_authority
data/live_feed.py          →  broker/zerodha_adapter, core/events,
                              core/time_authority, data/instrument_cache
data/symbol_validator.py   →  data/instrument_cache
data/atr_calculator.py     →  (pandas)
data/vwap_calculator.py    →  (pandas), core/market_windows
data/candle_calculator.py  →  (pandas)
data/market_hours_guard.py →  core/market_windows, core/time_authority
```

### 3.2.4 Layer 4 (Capital & Risk)

```
capital/invariant.py      →  core/state_store, core/exceptions
capital/fund_manager.py   →  capital/invariant, core/state_store,
                             broker/zerodha_adapter, core/events,
                             core/time_authority
capital/position_sizer.py →  capital/fund_manager, core/config_loader
capital/risk_engine.py    →  capital/fund_manager, core/state_store,
                             data/symbol_validator
capital/kill_switch.py    →  capital/fund_manager, core/state_store,
                             core/events, alerts/critical
```

### 3.2.5 Layer 5 (Application)

```
screening/secondary_screener.py →  data/*, screening/quality_scorer,
                                   screening/step_executor,
                                   strategies/loader, core/config_loader
screening/quality_scorer.py     →  core/config_loader (scoring_weights.yaml)
screening/step_executor.py      →  core/exceptions, core/logger
screening/entry_gate.py         →  data/live_feed, core/time_authority,
                                   strategies/loader

signals/signal_queue.py         →  core/config_loader
signals/signal_validator.py     →  core/state_store, core/time_authority,
                                   core/ids
signals/webhook_receiver.py     →  signals/signal_queue, signals/signal_validator,
                                   capital/kill_switch, data/market_hours_guard,
                                   strategies/loader, core/config_loader
signals/signal_processor.py     →  signals/signal_queue, screening/secondary_screener,
                                   capital/position_sizer, capital/risk_engine,
                                   capital/fund_manager, capital/kill_switch,
                                   orders/order_placer, core/state_store,
                                   core/events

orders/entry_engine.py          →  (abstract base — no imports)
orders/full_entry_engine.py     →  orders/entry_engine, orders/order_protocol_co,
                                   orders/order_protocol_limit
orders/order_protocol_co.py     →  broker/zerodha_adapter, broker/product_resolver
orders/order_protocol_limit.py  →  broker/zerodha_adapter, broker/product_resolver
orders/order_manager.py         →  core/state_store, core/events
orders/order_placer.py          →  orders/full_entry_engine, orders/order_manager,
                                   capital/fund_manager, capital/kill_switch,
                                   broker/cost_calculator
orders/order_monitor.py         →  broker/zerodha_adapter, core/state_store,
                                   capital/fund_manager, core/events
orders/order_reconciler.py      →  broker/zerodha_adapter, core/state_store,
                                   capital/fund_manager, capital/invariant,
                                   alerts/critical
orders/order_timeout.py         →  broker/zerodha_adapter, capital/fund_manager
orders/smart_tgt_manager.py     →  broker/zerodha_adapter, core/state_store,
                                   data/live_feed, core/events
orders/eod_squareoff.py         →  broker/zerodha_adapter, core/state_store,
                                   capital/fund_manager, core/market_windows

alerts/telegram_notifier.py     →  core/config_loader (rate-limited)
alerts/critical.py              →  alerts/telegram_notifier, core/state_store,
                                   core/time_authority

reports/trade_logger.py         →  core/state_store, core/time_authority
reports/signal_funnel.py        →  core/state_store
reports/report_builder.py       →  core/state_store, reports/trade_logger
reports/daily_review.py         →  reports/report_builder, reports/signal_funnel,
                                   alerts/telegram_notifier
```

### 3.2.6 Layer 6 (Entry Points)

```
main.py                          →  (almost everything — it wires the system)
                                    Specifically initializes in this order:
                                      core/* → broker/* → auth/* → capital/* →
                                      data/* → screening/* → signals/* →
                                      orders/* → alerts/* → main loop
                                    See Section 4 for exact order.

scripts/morning_auth.py          →  auth/zerodha_login, broker/zerodha_adapter
scripts/daily_review_runner.py   →  reports/daily_review
scripts/alert_watcher.py         →  (independent of trading system —
                                     reads sentinel files, sends email,
                                     does NOT import trading modules)
scripts/preflight_scanner_check.py →  core/config_loader, broker/zerodha_adapter
scripts/recovery_helper.py       →  orders/order_reconciler, capital/fund_manager
```

## 3.3 Cycle Detection Verification

The above dependency graph has been mentally walked for cycles. Two paths that look like cycles but are not:

**Apparent cycle 1:** `fund_manager → state_store → ???`
- Resolution: `state_store` depends on nothing above Layer 1. It is a leaf node from Layer 1. No cycle.

**Apparent cycle 2:** `signal_processor → fund_manager → events → ???`
- Resolution: `events` (the EventBus) is in Layer 1 and publishes/subscribes via callbacks registered at startup. The bus does not import from its subscribers. Subscribers register themselves with the bus (function pointer registration), so the dependency direction is `subscriber → bus`, not `bus → subscriber`. No cycle.

**Apparent cycle 3:** `order_monitor → fund_manager` and `fund_manager → events` and `events → ???`
- Same resolution as above. The bus is a passive registry; cycles only exist if A imports B and B imports A. The bus does not import its subscribers.

**Verification rule for new modules:** any new module added to v2 must have its dependency direction validated against this layer rule before merge. Section 9 (Build Phase Order) builds layers bottom-up, which makes accidental upward imports impossible during construction.

## 3.4 What This Eliminates from the Existing System

The existing system has documented circular imports:
- `kill_switch ↔ fund_manager` (each calls into the other)
- `paper_engine ↔ signal_processor` (signal_processor imports paper_engine for position checks)
- `live_feed_manager ↔ session_state ↔ fund_manager` (chain of mutual imports)

v2 eliminates all three by:
1. Moving shared state into `core/state_store.py` (Layer 1) which everyone reads from
2. Using the EventBus (`core/events.py`) for genuinely cross-cutting notifications
3. Making capital state queryable (read-only) from any layer without import dependencies

---

# 4. Startup Sequence

This section defines the exact order of operations when `python main.py` runs. The four scenarios from G5a (Cold / Warm / Crash / Halt) determine which path is taken.

## 4.1 Scenario Detection (Always Runs First)

Before any subsystem initializes, `main.py` runs scenario detection. This MUST happen first because it determines which subsequent steps to run.

```
Phase 0a: Acquire process lock (utils/lock_manager)
          └─→ if another instance running: print error, exit(1)

Phase 0b: Load configuration files
          ├─→ core/config_loader.load_all()
          ├─→ Validate every YAML against Pydantic schemas
          ├─→ Compute SHA256 hash of all config files
          ├─→ Compare to last_config_hash in state_store
          └─→ if mismatch: log diff, queue Telegram alert (sent later)

Phase 0c: Initialize core/state_store (SQLite WAL connection)
          └─→ Run schema migration if version mismatch

Phase 0d: Initialize core/time_authority
          ├─→ Read VM time, set as runtime clock
          ├─→ This is the LAST step that doesn't need broker access
          └─→ NO broker call yet — that comes in Phase 1

Phase 0e: Run startup_checks.detect_scenario()
          ├─→ Read system_events table for last SHUTDOWN today
          ├─→ Read kill_switch state from state_store
          ├─→ Read session_date from session table
          └─→ Returns: COLD | WARM | CRASH | HALT

Phase 0f: Branch on scenario:
          ├─→ HALT     → Phase H (halt-recovery path)
          ├─→ COLD     → Phase C (new-day path)
          ├─→ WARM     → Phase W (warm-restart path)
          └─→ CRASH    → Phase R (crash-recovery path)
```

The detection logic is deterministic:

```python
def detect_scenario(state_store) -> Scenario:
    kill_state = state_store.get_kill_switch_state()
    session_date = state_store.get_session_date()
    today = time_authority.now_ist().date().isoformat()
    last_shutdown = state_store.get_last_shutdown_event_today()
    
    # HALT takes precedence — never auto-start from a hard kill
    if kill_state == "HARD_KILLED":
        return Scenario.HALT
    
    # New trading day = COLD start (most common)
    if session_date != today:
        return Scenario.COLD
    
    # Same day, clean shutdown marker exists = WARM
    if last_shutdown is not None:
        return Scenario.WARM
    
    # Same day, no clean shutdown = CRASH
    return Scenario.CRASH
```

## 4.2 Phase H — HALT Restart

The system was killed and is being restarted. Rules from G5c:

```
H.1: Read halt type from state_store
     ├─→ HARD_KILL: REQUIRE --resume CLI flag
     │              └─→ if missing: print kill reason, exit(1)
     │
     └─→ SOFT_KILL: Auto-resume IF triggering condition has cleared
                    ├─→ API failure → check API now reachable
                    ├─→ Daily loss limit → check date is new day
                    ├─→ Consecutive losses → reset counter on new day
                    └─→ Capital invariant → require manual --resume

H.2: If conditions allow auto-resume:
     ├─→ Clear kill state in state_store
     ├─→ Send Telegram: "System auto-resumed after soft_kill cleared"
     └─→ Continue to Phase C/W/R based on session_date

H.3: If --resume flag provided manually:
     ├─→ Clear kill state
     ├─→ Send Telegram: "System manually resumed after halt"
     └─→ Continue to Phase R (always treat manual resume as crash recovery)
```

## 4.3 Phase C — COLD Start (New Trading Day)

This is the most common startup path. Most market days, this is what runs.

```
C.1: Pre-market validation (NO broker calls except margins)
     ├─→ Holiday check — is today a trading day?
     │   └─→ if no: log + Telegram alert + exit(0)
     │
     ├─→ Strategy YAML validation
     │   └─→ strategies/loader.load_all() — Pydantic validates 15 YAMLs
     │       └─→ if any invalid: Telegram alert + exit(1)
     │
     └─→ Pre-flight scanner check (P17)
         ├─→ scripts/preflight_scanner_check.py
         ├─→ HTTP HEAD all 15 Chartink scanner URLs
         ├─→ Verify scan_webhook_map has no duplicates
         ├─→ Verify every map entry points to a valid strategy YAML
         ├─→ Verify own webhook /health endpoint reachable
         └─→ if any failure: Telegram CRITICAL + exit(1)

C.2: Authentication
     ├─→ auth/token_manager.load_token()
     ├─→ if expired: print instruction to run morning_auth.py + exit(1)
     ├─→ Verify token-account binding matches selected account
     └─→ Initialize broker/zerodha_adapter with token

C.3: Clock validation (G4 startup NTP check)
     ├─→ broker/zerodha_adapter.margins() — first broker call
     ├─→ Extract response timestamp, compute skew vs VM clock
     ├─→ if skew > 30s: print error, send critical alert, exit(1)
     ├─→ if skew 5-30s: warn, retry once after 5s
     └─→ if skew < 5s: proceed, record skew baseline

C.4: Reset daily counters (this is what makes it a COLD start)
     ├─→ state_store.reset_daily_state()
     │   ├─→ daily_pnl = 0
     │   ├─→ realized_intraday_pnl = 0
     │   ├─→ daily_loss_amount = 0
     │   ├─→ consecutive_losses = 0 (carry from yesterday IF positive count)
     │   ├─→ traded_today = {}
     │   ├─→ in_flight = {}
     │   └─→ session_date = today.isoformat()
     │
     ├─→ Carry forward yesterday's stats:
     │   ├─→ yesterday_pnl = previous session's daily_pnl
     │   ├─→ yesterday_wins = previous count
     │   ├─→ yesterday_losses = previous count
     │   └─→ smart_tgt_stats = preserved
     │
     └─→ Apply T+1 settlement (P7a):
         ├─→ if yesterday_pnl > 0: cash_floor += yesterday_pnl
         └─→ (yesterday_pnl < 0 was already deducted yesterday)

C.5: Initialize Layer 4 (Capital)
     ├─→ capital/fund_manager.init(broker, system_config)
     │   ├─→ Fetch broker margins → set cash_floor
     │   ├─→ Verify cash_floor matches broker.equity.net
     │   ├─→ Reset margin_used = 0, margin_reserved = 0
     │   ├─→ Run capital invariant check (G3 Level 1)
     │   └─→ if invariant fails: HARD_KILL + exit(1)
     │
     ├─→ capital/risk_engine.init(system_config)
     ├─→ capital/position_sizer.init(system_config, scoring_weights)
     └─→ capital/kill_switch.init(state_store)
         └─→ Load kill state from disk (should be ACTIVE for COLD start)

C.6: Initialize Layer 3 (Data)
     ├─→ data/instrument_cache.refresh_if_stale()
     │   └─→ Daily refresh from NSE bhavcopy if cache > 24h old
     │
     ├─→ data/candle_store.init(broker)
     │   └─→ NO data load yet — lazy on first signal
     │
     └─→ data/market_hours_guard.init(market_windows)

C.7: Initialize Layer 5 (Application — Application thread + Background threads)
     ├─→ alerts/telegram_notifier.init(config) — set up rate limiter
     ├─→ alerts/critical.init(state_store) — set up sentinel directory
     │
     ├─→ orders/order_manager.init(state_store)
     ├─→ orders/smart_tgt_manager.init(broker, state_store, live_feed)
     ├─→ orders/order_monitor.init(broker, state_store) — START background thread
     ├─→ orders/order_reconciler.init(broker, state_store) — START background thread
     │   └─→ For COLD start, run one-shot reconcile to catch yesterday's leftovers
     │
     ├─→ orders/eod_squareoff.init(market_windows) — START clock watcher thread
     │
     ├─→ data/live_feed.init(broker)
     │   └─→ Subscribe to instruments referenced in open_positions (should be empty for COLD)
     │
     ├─→ signals/signal_queue.init(config) — capacity 300
     ├─→ signals/signal_validator.init(state_store)
     ├─→ signals/signal_processor.init(...) — START worker thread pool (G9)
     └─→ signals/webhook_receiver.init(signal_queue) — START Flask thread

C.8: Final readiness check
     ├─→ Verify all background threads are alive
     ├─→ Verify Flask is bound to port (P17 catch)
     ├─→ Send Telegram "SYSTEM ACTIVE" message with mode + capital
     └─→ Enter main loop (sleeps until kill_switch fires or Ctrl+C)
```

## 4.4 Phase W — WARM Restart (Same-Day, Clean Shutdown)

You stopped the system 5 minutes ago and are restarting. The DB is in a known-good state. The broker may have done things since.

```
W.1: Pre-market validation — same as C.1 but skip holiday check
     (you wouldn't have shut down on a non-trading day)

W.2: Authentication — same as C.2

W.3: Clock validation — same as C.3

W.4: Load existing daily state from state_store
     ├─→ DO NOT reset daily counters (this is mid-day)
     ├─→ daily_pnl, realized_intraday_pnl preserved
     ├─→ traded_today preserved
     └─→ open_positions preserved

W.5: Initialize Layer 4 (Capital)
     ├─→ capital/fund_manager.init(broker, system_config)
     │   ├─→ Fetch broker margins → compare to local cash_floor
     │   │   ├─→ if drift < 0.1%: accept broker value (cosmetic drift)
     │   │   ├─→ if drift 0.1%-0.5%: log warning, accept broker value
     │   │   └─→ if drift > 0.5%: HARD_KILL + critical alert (G3 Level 3 / G1 tier 3)
     │   │
     │   ├─→ Recompute margin_used from open_positions (sum qty × entry × 0.20)
     │   ├─→ Run invariant check
     │   └─→ if fails: HARD_KILL + exit(1)
     │
     ├─→ Other Layer 4 modules — same as C.5

W.6: Reconcile with broker (one-shot before resuming)
     ├─→ orders/order_reconciler.run_one_shot_reconcile()
     │   ├─→ Run all 6 G1 checks
     │   ├─→ For each open local position: verify broker still has it
     │   ├─→ For each broker position: verify we know about it (orphan adoption)
     │   └─→ Apply 3-tier action policy on any mismatches

W.7: Initialize remaining layers — same as C.6 + C.7
     ├─→ Live feed subscribes to ALL open positions (not empty for WARM)
     └─→ Smart TGT manager registers all open positions from state_store

W.8: Final readiness — same as C.8 with WARM flavor in startup message
```

## 4.5 Phase R — CRASH Recovery

The system was killed uncleanly. The DB is consistent up to the last committed transaction, but the broker may have done things we never recorded.

```
R.1: Pre-market validation — same as C.1 (skip holiday check)

R.2: Authentication — same as C.2

R.3: Clock validation — same as C.3

R.4: Load partial daily state from state_store
     ├─→ Read whatever was committed before crash
     ├─→ Mark all read state as "potentially-stale, broker will overwrite"
     └─→ Set recovery_mode = True

R.5: CRASH RECOVERY PROCEDURE (the heart of Phase R)
     
     Phase R.5.1: Diagnosis (no state changes)
     ├─→ Fetch broker positions (zerodha.positions().net)
     ├─→ Fetch broker open orders (filtered to OPEN/TRIGGER_PENDING)
     ├─→ Fetch broker today's trades (zerodha.trades())
     ├─→ Read all OPEN trades from local DB
     └─→ Build comparison table
     
     Phase R.5.2: Classification (decide action per position)
     For each open trade in DB:
       ├─→ Match to broker positions by symbol
       │   ├─→ Found, qty matches → HEALTHY
       │   ├─→ Found, qty differs → DRIFT (G1 case 4: partial close)
       │   └─→ Not found → CLOSED EXTERNALLY (G1 case 1: find exit price, record close)
       │
     For each broker position not in DB:
       └─→ ORPHAN ADOPTION (G1 case 2: generate new trade_id, recovered=true)
     
     Phase R.5.3: Capital rebuild
     ├─→ Read broker margins
     ├─→ Recompute margin_used from sum of (broker positions × 0.20 × entry)
     │   └─→ Use broker fill price, not last cached price
     ├─→ Reset local capital state to match broker
     ├─→ Run capital invariant check (G3 Level 1)
     └─→ if fails: HARD_KILL, alert, manual intervention required
     
     Phase R.5.4: Order alignment
     For each adopted/healthy position:
       ├─→ Check broker has SL order
       ├─→ if missing SL:
       │   ├─→ Get last known SL from DB (G5b)
       │   ├─→ Get current LTP
       │   ├─→ if LTP is on favorable side of last SL:
       │   │   └─→ Place fresh SL at last known level
       │   └─→ else:
       │       └─→ Place immediate MARKET exit (G5b safety condition)
       │
       ├─→ Check broker has TGT order
       └─→ if missing TGT: place fresh TGT from DB (no safety condition needed)

R.6: Initialize remaining layers (same as W.7) but with recovery flavor
     ├─→ Smart TGT manager: register recovered positions
     │   └─→ For each: recompute trail SL once from current candle history
     └─→ Live feed: subscribe + recompute candle history

R.7: Send recovery summary alert
     ├─→ Telegram: "RECOVERY COMPLETE — N healthy, M drift, K adopted, J external close"
     ├─→ Set recovery_mode = False
     └─→ Continue to main loop
```

## 4.6 Main Loop and Shutdown

After any of phases C/W/R completes, `main.py` enters the main loop:

```
Main loop (runs until Ctrl+C or kill_switch fires):
  every 5 seconds:
    ├─→ Check kill_switch state
    │   ├─→ if HARD_KILLED → break loop
    │   └─→ if SOFT_KILLED → continue (existing positions still managed)
    │
    ├─→ Heartbeat log entry
    └─→ sleep(5)

On Ctrl+C or kill exit:
  Phase X.1: Stop accepting new signals
             └─→ webhook_receiver.shutdown() — return 503 to new requests
  
  Phase X.2: Drain in-flight signals
             └─→ signal_processor.drain(timeout=10s)
  
  Phase X.3: Stop background threads in reverse init order
             ├─→ eod_squareoff.stop()
             ├─→ order_reconciler.stop()
             ├─→ order_monitor.stop()
             ├─→ live_feed.stop()
             └─→ fund_manager.shutdown()
  
  Phase X.4: Write SHUTDOWN event to system_events table (clean exit marker)
             └─→ This is what makes the next start a WARM restart
  
  Phase X.5: Release process lock + close state_store + exit(0)
```

The SHUTDOWN event in Phase X.4 is critical. Without it, the next restart will be detected as CRASH instead of WARM. The event must be written **inside the same transaction** as the final state mutations.

---

*[Sections 5-10 continue in Turn 2 and Turn 3]*

---

# Document Status

- **Section 1:** Foreword — COMPLETE
- **Section 2:** Folder & File Tree — COMPLETE
- **Section 3:** Inter-Module Dependency Map — COMPLETE
- **Section 4:** Startup Sequence — COMPLETE
- **Section 5:** Per-File Specifications — *Turn 2*
- **Section 6:** End-to-End Trade Flow — *Turn 2*
- **Section 7:** Database Schema — *Turn 2*
- **Section 8:** Configuration Files — *Turn 3*
- **Section 9:** Build Phase Order — *Turn 3*
- **Section 10:** Validation Checklist — *Turn 3*
