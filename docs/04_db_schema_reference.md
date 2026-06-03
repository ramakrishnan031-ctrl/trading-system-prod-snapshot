# Database Schema Reference

**Schema version:** 24 (FIX-150)
**Database:** SQLite 3 (`data_store/trading_system.db`)
**File:** `core/schema.sql`

## Table Summary

| # | Table | Purpose | Key Columns |
|---|-------|---------|-------------|
| 1 | schema_meta | Schema version tracking | key, value |
| 2 | signals | Incoming webhook signals | signal_id, symbol, status |
| 3 | trades | Trade lifecycle | trade_id, symbol, status, net_pnl |
| 4 | orders | Broker orders | order_id, trade_id, side, status |
| 5 | capital_snapshot | Periodic capital state | timestamp, total, available |
| 6 | system_events | Audit trail of system events | event_type, payload |
| 7 | session | Current session state | id=1 (singleton), mode, started_at |
| 8 | fm_ledger | Fund manager write-ahead log | entry_type, amount, balance_after |
| 9 | kill_switch_state | Kill switch state (singleton) | state, reason, triggered_at |
| 10 | webhook_audit | HTTP request audit trail | scanner_name, response_code |
| 11 | eod_squareoff_log | EOD squareoff history | date, trades_closed, status |
| 12 | reconciliation_log | Order reconciliation results | date, trade_id, discrepancy |
| 13 | screener_results | Screening pipeline outcomes | signal_id, step, result |
| 14 | smart_tgt_state | Smart target trailing state | trade_id, armed, current_tgt |
| 15 | innings | Multi-inning shadow tracking | trade_id, inning_number |
| 16 | gate_state | Entry gate rehydration | symbol, state, entered_at |
| 17 | candles | 1-min OHLCV candle data | symbol, ts, open, high, low, close |
| 18 | trade_excursions | Per-trade MFE/MAE | trade_id, mfe_price, mae_price |
| 19 | pnl_reconciliation | Daily P&L recon (broker vs system) | date, broker_pnl, system_pnl |
| 20 | telegram_alerts | Sent Telegram messages | severity, title, sent_at |
| 21 | trade_journal | Daily journal entries | date, entry_text |
| 22 | position_reconciliation | Broker vs system positions | date, symbol, status |
| 23 | strategy_metrics | Daily strategy performance | strategy, date, win_rate, sharpe |
| 24 | shadow_trades | Shadow/simulated trades | shadow_trade_id, simulated_pnl |
| 25 | fno_ban | F&O ban list | symbol, date |
| 26 | eod_verification | EOD position/order audit | date, open_positions, status |
| 27 | cron_heartbeat | Cron job execution tracking | job_name, executed_at |
| 28 | system_metrics | 5-min health snapshots | timestamp, cpu_pct, memory_mb |
| 29 | system_metrics_daily | Daily health summary | date, cpu_avg, cpu_max, cpu_p95 |

## Key Relationships

```
signals (signal_id) ──┐
                      ├──→ trades (signal_id FK)
                      │       ├──→ orders (trade_id FK)
                      │       ├──→ trade_excursions (trade_id FK)
                      │       ├──→ smart_tgt_state (trade_id FK)
                      │       ├──→ innings (trade_id FK)
                      │       └──→ shadow_trades (live_trade_id FK)
                      └──→ screener_results (signal_id FK)

fm_ledger ← standalone (reservation_id links to trades.reservation_id)
session ← singleton (id=1)
kill_switch_state ← singleton (single row)
```

## Core Tables Detail

### signals
Primary record of every incoming webhook signal.
```
signal_id TEXT PK          -- UUID4
symbol TEXT NOT NULL
scanner TEXT NOT NULL       -- Chartink scanner name
strategy TEXT NOT NULL      -- Mapped strategy name
triggered_at TEXT NOT NULL  -- When Chartink triggered
received_at TEXT NOT NULL   -- When we received it
expires_at TEXT NOT NULL    -- received_at + expiry_sec
status TEXT NOT NULL        -- TRADED/REJECTED_*/DROPPED_*
rejection_reason TEXT       -- Step name or free text
trade_id TEXT               -- FK to trades (if TRADED)
trigger_price REAL
fingerprint TEXT NOT NULL   -- Dedup hash
fingerprint_date TEXT       -- YYYY-MM-DD for unique index
webhook_payload TEXT        -- Raw JSON for forensics
```

### trades
Full trade lifecycle from capital reservation through close.
```
trade_id TEXT PK
signal_id TEXT NOT NULL FK
symbol TEXT NOT NULL
direction TEXT NOT NULL      -- LONG | SHORT
strategy TEXT NOT NULL
qty_planned INTEGER NOT NULL
qty_filled INTEGER DEFAULT 0
entry_target_price REAL NOT NULL
entry_actual_price REAL
sl_initial REAL NOT NULL
tgt_initial REAL NOT NULL
margin_reserved REAL NOT NULL
risk_amount REAL NOT NULL
status TEXT NOT NULL         -- PENDING_FILL/OPEN/CLOSED/CANCELLED/FAILED
exit_price REAL
exit_reason TEXT             -- SL_HIT/TGT_HIT/EOD/MANUAL
gross_pnl REAL
charges REAL
net_pnl REAL
mode TEXT                    -- PAPER | LIVE
order_protocol TEXT NOT NULL -- CO_PLUS_TGT | LIMIT_TRIPLE
reservation_id TEXT          -- FK to fm_ledger
cost_brokerage REAL          -- v14: itemized costs
cost_stt REAL
cost_exchange_txn REAL
cost_sebi REAL
cost_gst REAL
cost_stamp_duty REAL
```

### orders
Individual broker orders (entry, SL, TGT per trade).
```
order_id TEXT PK
trade_id TEXT NOT NULL FK
broker_order_id TEXT         -- Zerodha order ID
symbol TEXT NOT NULL
side TEXT NOT NULL            -- BUY | SELL
order_type TEXT NOT NULL      -- LIMIT | SL | MARKET
product TEXT NOT NULL         -- MIS | CNC | CO
qty INTEGER NOT NULL
price REAL
trigger_price REAL
status TEXT NOT NULL          -- PENDING/OPEN/COMPLETE/CANCELLED/REJECTED
filled_qty INTEGER DEFAULT 0
average_price REAL
placed_at TEXT
filled_at TEXT
reconciliation_status TEXT
rejection_reason TEXT
```

### fm_ledger
Append-only double-entry capital accounting log.
```
id INTEGER PK AUTOINCREMENT
entry_type TEXT NOT NULL     -- RESERVE/RELEASE/PNL/COST/ADJUSTMENT
amount REAL NOT NULL
balance_after REAL NOT NULL
reservation_id TEXT          -- Links RESERVE ↔ RELEASE
trade_id TEXT
timestamp TEXT NOT NULL
```

## Common Queries

### Today's trades
```sql
SELECT symbol, direction, status, net_pnl, strategy
FROM trades
WHERE date(created_at) = date('now')
ORDER BY created_at;
```

### Open positions
```sql
SELECT symbol, direction, qty_filled, entry_actual_price, sl_initial
FROM trades WHERE status = 'OPEN';
```

### Daily P&L
```sql
SELECT date(created_at) as day,
       COUNT(*) as trades,
       SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
       ROUND(SUM(net_pnl), 2) as total_pnl
FROM trades
WHERE status = 'CLOSED'
GROUP BY day ORDER BY day DESC LIMIT 10;
```

### Strategy performance
```sql
SELECT strategy,
       COUNT(*) as trades,
       ROUND(AVG(CASE WHEN net_pnl > 0 THEN 1.0 ELSE 0.0 END) * 100, 1) as win_pct,
       ROUND(SUM(net_pnl), 2) as total_pnl
FROM trades WHERE status = 'CLOSED'
GROUP BY strategy ORDER BY total_pnl DESC;
```

### Capital state
```sql
SELECT entry_type, amount, balance_after, timestamp
FROM fm_ledger ORDER BY id DESC LIMIT 20;
```

### Kill switch history
```sql
SELECT state, reason, triggered_at
FROM kill_switch_state ORDER BY rowid DESC LIMIT 5;
```

### Cron job health
```sql
SELECT job_name, executed_at
FROM cron_heartbeat
ORDER BY executed_at DESC LIMIT 20;
```

## Schema Version History

| Version | Change | Commit |
|---------|--------|--------|
| v1 | Initial 8 tables | Module builds |
| v2 | +fm_ledger | Capital module |
| v3 | +kill_switch_state | KillSwitch |
| v4 | +webhook_audit | Audit trail |
| v5 | +eod_squareoff_log | EOD module |
| v6 | +reconciliation_log | Reconciler |
| v7 | +screener_results | Screening |
| v8 | +smart_tgt_state | Smart target |
| v9 | +innings | Shadow tracker |
| v10 | fm_ledger redesign | Capital overhaul |
| v11 | +eod status/completed_at, +reservation_id | Phase E |
| v12 | +gate_state | Entry gate rehydration |
| v13 | session cleanup | Audit 2026-04-26 |
| v14 | +candles, +trade_excursions, cost breakdown | FIX-124 |
| v15 | +pnl_reconciliation | FIX-128 |
| v16 | +orders.reconciliation_status | FIX-129 |
| v17 | +latency tracking cols | FIX-130 |
| v18 | +telegram_alerts | FIX-131 |
| v19 | +trade_journal | FIX-133 |
| v20 | +position_reconciliation, +strategy_metrics | FIX-134 |
| v21 | +shadow_trades | FIX-135 |
| v22 | +eod_verification | FIX-137 |
| v23 | +cron_heartbeat | FIX-145 |
| v24 | +system_metrics, +system_metrics_daily | FIX-150 |
