# Database Schema Reference

> ## ⛔⛔ VERIFIED SCOPE — READ BEFORE TRUSTING ANY SECTION BELOW
>
> **Only the `fm_ledger` section has been checked against the live DB (04-Aug-2026).
> Everything else in this document is UNVERIFIED.**
>
> ⚠️ **This warning exists because a PARTIALLY corrected document is more dangerous than a
> uniformly stale one.** A reader who checks `fm_ledger`, finds it precise, measured and
> dated, will reasonably extend that credibility to the rest — which has been verified
> against nothing. **The correction lends authority to the parts it did not touch**, so the
> scope has to travel with it (§M5: state the subject in the same sentence as the conclusion).
>
> **Measured against the live VM DB, 04-Aug-2026:**
>
> | | this document claims | **live** |
> |---|---|---|
> | schema version | ~~24 (FIX-150)~~ | **45** |
> | tables described | 29 in the summary, **11** in detail | **45** in `trading_system.db` |
> | databases | one | **two** — `trading_system.db` + `analytics.db` (3 tables, ATTACHed) |
>
> ⇒ **21 schema versions stale · ~16 live tables entirely undocumented · the second database
> unmentioned.** The version-history table below stops at **v24**.
>
> ⛔ **Do NOT treat the un-flagged sections as current.** ⛔ **And do not "fix" this by
> re-verifying all 45 tables in passing — that is a project, not a task.** Verify the table
> you need, against `core/schema.sql` or the live DB, and mark what you verified.

**Schema version:** ~~24 (FIX-150)~~ → **45 live** (measured 04-Aug-2026; this document
describes v24 except where a section says otherwise)
**Database:** SQLite 3 (`data_store/trading_system.db`) — ⚠️ **plus `data_store/analytics.db`,
ATTACHed; raw sqlite access must go through `core.db_connect.connect`**
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

⛔⛔ **CORRECTED 04-Aug-2026 AGAINST THE LIVE DB — the previous description was wrong in
three ways, and one of them actively caused a false finding.** Struck rather than deleted
(§G4), because *what* it said is the reason the rule below exists:

- ~~`id INTEGER PK`~~ → the PK is **`ledger_id`**; ~~`timestamp`~~ → the column is **`ts`**.
- ~~`entry_type … RESERVE/RELEASE/PNL/COST/ADJUSTMENT`~~ → **`PNL`, `COST` and `ADJUSTMENT`
  DO NOT EXIST.** Measured vocabulary, all-time: **`RESERVE` · `RELEASE` · `RELEASE_USED` ·
  `COMMIT` · `INIT` · `SYNC` · `RESET_PNL` · `TOP_UP`**.
- ⛔ ~~`reservation_id -- Links RESERVE ↔ RELEASE`~~ → **THIS IS THE TRAP.** See below.

```
ledger_id INTEGER PK AUTOINCREMENT
ts TEXT NOT NULL
entry_type TEXT NOT NULL   -- RESERVE | RELEASE | RELEASE_USED | COMMIT
                           -- INIT | SYNC | RESET_PNL | TOP_UP
amount REAL NOT NULL
bucket TEXT                -- intraday | positional
balance_before REAL
balance_after REAL
signal_id TEXT
reservation_id TEXT        -- ⛔ NOT a simple RESERVE↔RELEASE pair — read below
reason TEXT
session_id TEXT
direction TEXT
trade_id TEXT
margin_delta REAL
pnl_delta REAL             -- the daily-loss limit reads THIS, not trades.net_pnl
costs REAL
```

#### ⛔⛔ A RESERVATION HAS **TWO** TERMINATION SHAPES, NOT ONE

| the trade | its ledger chain | does the terminator carry `reservation_id`? |
|---|---|---|
| **cancelled / rejected** (never filled) | `RESERVE` → **`RELEASE`** | ✅ yes |
| **filled** | `RESERVE` → **`COMMIT`** → `RELEASE_USED` | ✅ COMMIT yes · ⛔ **`RELEASE_USED` NO — 0 of 211** |

⭐ **`release_used()`'s signature takes no `reservation_id` at all** (it keys on
`symbol` + `trade_id`). ⇒ **a query for "RESERVE with no RELEASE" marks EVERY FILLED TRADE as
un-released.** Measured 04-Aug-2026: that query returns **221**; the correct one — *RESERVE
with neither `RELEASE` nor `COMMIT`* — returns **10**, which reconciles exactly with the
independent arithmetic `1327 − 1106 − 211 = 10`. **Two methods agreeing is what makes the 10
trustworthy; the 221 was a false finding produced by the old comment above.**

> ### 🔴🔴 HARD ORDERING CONSTRAINT — **`reservation_id` on `RELEASE_USED` MUST land BEFORE any change that queries CLOSED reservations** *(06-Aug-2026)*
> **Phase E's deferred orphan detection — *"rids in ledger but not in `fm._reservations`"*
> (`order_reconciler.py`, `_check7` docstring) — is exactly such a change.**
>
> **(P) MEASURED 06-Aug:** a **fully released** reservation reports its **entire margin as still
> held** — `sum_fm_ledger_margin_delta('54672bb96d30421d')` → **689.41341**, because the offsetting
> `RELEASE_USED` row carries no `reservation_id` *(0 of 220; `RELEASE` carries it 1189 of 1189 —
> **the keys are perfectly disjoint**)*.
> **(S)** `_check7` publishes with `source_module="fund_manager_self_check"`
> (`order_reconciler.py:3782`), which **IS** in `_ESCALATING_SOURCES` (`drift_handler.py:66-70`)
> ⇒ **DH1 does NOT bar it** ⇒ **single-sample SOFT/HARD escalation.**
>
> ⇒ ⛔ **Shipping Phase E before this fix would arm a spurious kill on every closed delivery
> reservation.** ⭐ Today `_check7` iterates live reservations only, so the false positive is
> unreachable — **that is the only thing holding it, and Phase E removes it.**
> ➡️ Full analysis: `docs/design/f6_delivery_exit_predicate_design_06aug2026.md` §§11, 13, 14.

#### 🔴 THE LEDGER IS **NOT SELF-CONSISTENT** — the rule for anyone reconstructing capital

> **Capital reconstructed from `fm_ledger` MUST be reconciled against trade status, or must
> exclude reservations belonging to terminal trades. The ledger alone is not
> self-consistent: 10 reservations (₹1,628.13, dated 15–18 Jun 2026) have NO terminating row
> and never will.**

- **Why they will never be terminated:** the cause is **permanently unknowable** — the
  15–18 Jun window predates all retained logs (`system_*.log` begins 06-Jul). ⛔ **They are
  deliberately NOT reconciled.** Writing terminating rows would assert a termination that
  cannot be proven, and *a wrong correction is indistinguishable from correct data
  afterwards* — the same ruling that kept the 141 fabricated `innings` rows **VOID and
  KEPT**, never deleted.
- ✅ **They do NOT affect live capital.** `fund_manager.rehydrate_from_open_trades` walks
  **OPEN/PARTIAL** trades only, so reservations on terminal trades are never replayed and
  the in-memory state has never held them.
- ⚠️ **Which is exactly why this is a trap and not a bug:** ₹1,628.13 on a ~₹9,871 book is
  **16.5% of capital**. A tool that silently inherited it **would not look obviously
  broken** — it would just be wrong by a sixth.
- ⛔⛔ **BUT READ THAT 16.5% WITH ITS DENOMINATOR, AND PREFER THE OTHER TWO (added 04-Aug-2026).**
  **16.5% is measured against TODAY'S CAPITAL, which is a TESTING value, not the design
  capital** — so that figure **shrinks as the account grows and will quietly stop sounding
  alarming** while the defect is entirely unchanged. **It is the least durable way to state
  this.** The scale-free measurements, each with its denominator named:

  | figure | value | denominator |
  |---|---|---|
  | by **count** | **10 / 1,327 = 0.75%** | every reservation ever opened |
  | by **value** | **₹1,628.13 / ₹133,274.95 = 1.22%** | every rupee ever reserved |
  | ~~by capital~~ | ~~16.5%~~ | ⚠️ *today's ₹9,882.30 — a test value; kept because it is what makes the TOOLING risk vivid, ⛔ not because it is the durable number* |

  ⭐ **The three do not disagree — they answer different questions, and that is the point.**
  **0.75% / 1.22% say how much of the LEDGER is affected** (small, and it does not grow).
  **16.5% says how badly ONE TOOL would be wrong TODAY if it reconstructed capital from the
  ledger alone** (a sixth). ⇒ **quote the count/value ratios when describing the DATA; quote
  the capital ratio only when describing the CONSEQUENCE, and say which capital.**
  ⛔ **Never write a bare "16.5%" without its denominator** — a reader who meets it later will
  reasonably assume it means 16.5% of the ledger, which is off by ~13×.
- ⭐ **And note what protects you: `rehydrate` keys on TRADE STATUS, not on ledger
  completeness.** Re-key any reconstruction on the ledger "for accuracy" and you inherit all
  ten immediately. Record: `docs/audit/reservation_nonatomicity_latent_or_live_04aug2026.md`.

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
