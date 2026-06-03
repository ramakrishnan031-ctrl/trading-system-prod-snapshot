# System Architecture

**Trading System v2** — Automated intraday/positional equity trading system.

## Overview

Single-process Python application running on a Linux VM. Receives webhook
signals from Chartink scanners, screens and sizes them, places orders via
Zerodha Kite API, monitors fills, manages capital, and generates daily reports.

## Startup Flow (main.py)

```
main.py [--mode paper|live] [--interactive] [--resume]
  │
  ├─ Phase 0a: Parse args, load config (system_config.yaml)
  ├─ Phase 0b: Holiday/weekend guard (exit 0 if non-trading day)
  ├─ Phase 0c: Interactive mode (--interactive):
  │    ├─ Account selection (config/accounts.csv)
  │    ├─ Token check / Zerodha login
  │    ├─ Mode selection (paper/live)
  │    └─ Confirmation prompt
  ├─ Phase 0d: Startup checks (13 checks, blocking + warnings)
  │    ├─ Config file presence
  │    ├─ Required secrets (.env)
  │    ├─ Startup scenario (COLD/WARM/RESUME/HALT)
  │    ├─ Clock skew (vs broker server time)
  │    ├─ Config hash diff
  │    ├─ Market holiday
  │    ├─ Scanner connectivity
  │    ├─ Instrument cache size
  │    ├─ Strategy YAML validation
  │    ├─ Holiday calendar
  │    ├─ SDK version pin
  │    ├─ NTP sync
  │    └─ TEMP config markers
  ├─ Phase 1: Wire components
  │    ├─ StateStore (SQLite)
  │    ├─ FundManager (capital tracking)
  │    ├─ KillSwitch
  │    ├─ ZerodhaAdapter (broker API)
  │    ├─ WebhookReceiver (Flask HTTP)
  │    ├─ SignalProcessor (worker pool)
  │    ├─ OrderPlacer → OrderMonitor → SmartTgtManager
  │    ├─ OrderReconciler
  │    ├─ EodSquareoff scheduler
  │    └─ CapitalDriftHandler
  ├─ Phase 2: Start runtime threads
  │    ├─ Webhook server (Waitress WSGI, port 5000)
  │    ├─ Signal processor drain loop
  │    ├─ Order monitor poll loop
  │    ├─ Order reconciler poll loop
  │    ├─ Clock skew probe
  │    ├─ Token monitor
  │    └─ EOD squareoff scheduler
  └─ Phase 3: Main loop (block until shutdown signal)
       └─ SIGTERM/SIGINT → graceful shutdown
```

## Component Responsibilities

### Signal Layer (`signals/`)
- **WebhookReceiver** — Flask HTTP gateway. Validates payloads, deduplicates
  (SHA-256 fingerprint at minute precision), enforces backpressure (503 at 80%
  queue capacity), enqueues validated signals.
- **SignalProcessor** — ThreadPoolExecutor (5 workers). Dequeues signals,
  runs screening pipeline (quality scorer → step executor → secondary screener
  → entry gate), sizes positions, places orders.

### Order Layer (`orders/`)
- **OrderPlacer** — Translates trade intent into broker API calls. Supports
  CO_PLUS_TGT and LIMIT_TRIPLE protocols. Handles entry + SL + TGT orders.
- **OrderMonitor** — Polls broker for fill updates every 2s. Cancels unfilled
  orders after 60s timeout. Drives order state machine transitions.
- **SmartTgtManager** — Trailing target logic. Arms after price moves 0.5%
  toward target, then trails in 0.3% steps.
- **OrderReconciler** — Every 15s, reconciles system state vs broker positions.
  Detects orphaned positions, missing orders, capital drift.
- **EodSquareoff** — Fires at 15:17 IST. LIMIT_THEN_MARKET exit protocol
  (aggressive limit, 120s grace, then market).

### Capital Layer (`capital/`)
- **FundManager** — Double-entry capital accounting. Tracks total, reserved,
  available, realized P&L. Per-bucket (intraday 70% / positional 30%).
- **KillSwitch** — SOFT_KILL (stop new trades) / HARD_KILL (close everything).
  Triggered by consecutive API failures, daily loss limit, capital drift.
- **CapitalDriftHandler** — Monitors FundManager vs broker margins. Escalates:
  log → soft_kill → hard_kill based on Rs thresholds.

### Broker Layer (`broker/`)
- **ZerodhaAdapter** — Kite Connect API wrapper. Rate-limited (3 req/sec).
  Paper mode: synthetic fills with LTP gating.
- **OrderStateMachine** — Validates order state transitions.
- **RateLimiter** — Token bucket, per-second rate control.
- **CostCalculator** — Brokerage, STT, exchange fees, GST, stamp duty.

### Screening Layer (`screening/`)
- **QualityScorer** — Scores signals 0-100 based on weighted criteria.
- **StepExecutor** — 10-step screening pipeline (symbol validation, market
  hours, sector exposure, consecutive losses, position limits, etc.).
- **SecondaryScreener** — Post-sizing validation (lot size, minimum qty).
- **EntryGate** — Price-based pullback gate with ThreadPoolExecutor workers.

### Data Layer (`data/`)
- **LiveFeedManager** — Kite WebSocket for real-time LTP.
- **CandleStore** — 1-min OHLCV candle storage and retrieval.

### Alert Layer (`alerts/`)
- **TelegramNotifier** — Mode-prefixed rich alerts to Telegram channels.
- **CriticalAlertManager** — Sentinel files for persistent CRITICAL state.
- **AlertWatcher** — Background daemon that monitors sentinel files.

## Data Flow

```
Chartink Scanner → HTTP POST → WebhookReceiver → signal_queue
  → SignalProcessor:
      1. Lookup strategy (scan_webhook_map.yaml)
      2. Quality score (scoring_weights.yaml)
      3. Step executor (10-step gate)
      4. Position sizing (risk_per_trade_pct, leverage)
      5. Secondary screen (lot size, min qty)
      6. Entry gate (price check, liquidity)
      7. OrderPlacer.place_entry()
          → ZerodhaAdapter.place_order()
          → trades table (PENDING_FILL)
  → OrderMonitor:
      - Poll fills every 2s
      - Entry filled → status=OPEN
      - SL/TGT filled → close_trade() → status=CLOSED
  → SmartTgtManager:
      - Trail target if armed
  → EodSquareoff:
      - 15:17 IST → close all OPEN positions
  → DailyReport:
      - 16:05 IST → xlsx + md report
```

## Threading Model

| Thread | Purpose | Daemon |
|--------|---------|--------|
| MainThread | Startup, shutdown orchestration | No |
| waitress-* | HTTP request handling (Waitress pool) | Yes |
| signal_processor | Queue drain + worker pool | Yes |
| order_monitor | Broker fill polling | Yes |
| order_reconciler | Position reconciliation | Yes |
| clock_skew_probe | Periodic broker time check | Yes |
| token_monitor | Token expiry watch | Yes |
| eod_scheduler | EOD squareoff timer | Yes |
| in_flight_sweeper | Stuck signal cleanup | Yes |
| paper_fill_* | Synthetic fill threads (paper mode) | Yes |

## Configuration

- `config/system_config.yaml` — Master config (all numeric limits)
- `config/strategies/*.yaml` — Per-strategy parameters (15 strategies)
- `config/scan_webhook_map.yaml` — Scanner → strategy mapping
- `config/instruments.csv` — Instrument master (symbol, token, exchange)
- `config/accounts.csv` — Broker account details

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Clean shutdown or holiday |
| 1 | TradingSystemError |
| 2 | Unexpected exception |
| 3 | Startup check failure |
| 4 | HALT without --resume |
| 5 | Invalid args/config |
| 6 | Token missing (non-interactive) |
| 7 | Live mode cancelled |
| 8 | Account selection cancelled |
