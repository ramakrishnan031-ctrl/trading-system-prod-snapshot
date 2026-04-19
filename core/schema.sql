-- ─────────────────────────────────────────────────────────────────────────────
-- Trading System v2 — State Store Schema
-- ─────────────────────────────────────────────────────────────────────────────
-- Version: 1
-- Last updated: 2026-04-14
--
-- This file defines all tables for the v2 trading system's persistent state.
-- It is the SOURCE OF TRUTH for the schema. Migrations to new versions add
-- new sections at the bottom and bump the schema_version row.
--
-- Conventions:
--   - All TEXT timestamps are ISO-8601 IST (e.g., "2026-04-14T09:30:00+05:30")
--   - All IDs are UUID4 strings unless otherwise noted (broker order_ids are
--     broker-assigned strings)
--   - All monetary amounts are REAL (Python float). Rupee precision is
--     2 decimal places; we tolerate float rounding within 1 paise.
--   - All FK relationships are explicit even though SQLite does not enforce
--     them by default — application code MUST set PRAGMA foreign_keys = ON
--
-- DO NOT modify this file by hand without bumping schema_version and adding
-- a corresponding migration in state_store.py.
-- ─────────────────────────────────────────────────────────────────────────────

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 1: schema_meta
-- Single source of truth for schema version and migration tracking.
-- Always exists. Single row per key.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 2: signals
-- Immutable log of EVERY incoming webhook signal. One row per (symbol,
-- scanner) pair extracted from a webhook. Status field tells you what
-- happened to it. P16 visibility: every signal has a final status.
--
-- Decision refs: G2a (signal_id), P6 (dedup), P16 (visibility), P18 (status)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS signals (
    signal_id           TEXT PRIMARY KEY,            -- UUID4
    symbol              TEXT NOT NULL,
    scanner             TEXT NOT NULL,
    strategy            TEXT NOT NULL,
    triggered_at        TEXT NOT NULL,               -- ISO IST from Chartink
    received_at         TEXT NOT NULL,               -- ISO IST when we received it
    expires_at          TEXT NOT NULL,               -- received_at + signal_expiry_sec
    status              TEXT NOT NULL,               -- TRADED/REJECTED_*/DROPPED_*
    rejection_reason    TEXT,                        -- nullable, free text or step name
    trade_id            TEXT,                        -- nullable FK; set if signal became a trade
    trigger_price       REAL,                        -- price from Chartink at trigger time
    fingerprint         TEXT NOT NULL,               -- hash(scanner+symbol+trigger_minute) for P6 dedup
    fingerprint_date    TEXT NOT NULL,               -- YYYY-MM-DD of received_at, for unique index

    FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
);

-- P6 dedup: same scanner + same symbol + same minute on same day = duplicate
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_fingerprint_today
    ON signals(fingerprint, fingerprint_date);

CREATE INDEX IF NOT EXISTS idx_signals_status
    ON signals(status);

CREATE INDEX IF NOT EXISTS idx_signals_received_at
    ON signals(received_at);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 3: trades
-- One row per logical position lifecycle. Created at capital reservation.
-- A trade may have many orders (entry + SL + TGT + modifications) — see
-- the orders table for that 1:N relationship.
--
-- Decision refs: G2a (trade_id), P7a (capital), P8/P13 (order_protocol),
--                G10 (entry_mode), G5a (recovered_flag)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS trades (
    trade_id            TEXT PRIMARY KEY,            -- UUID4
    signal_id           TEXT NOT NULL,               -- FK to signals
    
    -- Identity
    symbol              TEXT NOT NULL,
    direction           TEXT NOT NULL,               -- LONG | SHORT
    strategy            TEXT NOT NULL,
    sector              TEXT,
    
    -- Entry parameters (from screening)
    qty_planned         INTEGER NOT NULL,
    qty_filled          INTEGER NOT NULL DEFAULT 0,
    entry_target_price  REAL NOT NULL,               -- the LIMIT price we want
    entry_actual_price  REAL,                        -- actual fill price (may differ)
    sl_initial          REAL NOT NULL,
    tgt_initial         REAL NOT NULL,
    
    -- Capital snapshot AT trade_id creation
    margin_reserved     REAL NOT NULL,               -- qty * entry * 0.20
    risk_amount         REAL NOT NULL,               -- qty * (entry - sl) for LONG
    
    -- Lifecycle timestamps
    created_at          TEXT NOT NULL,               -- when capital was reserved
    entry_time          TEXT,                        -- when entry was filled
    exit_time           TEXT,                        -- when position was fully closed
    
    -- Exit
    exit_price          REAL,
    exit_reason         TEXT,                        -- SL_HIT/TGT_HIT/EOD/MANUAL/TIMEOUT/CIRCUIT_BREAKER
    gross_pnl           REAL,
    charges             REAL,
    net_pnl             REAL,
    
    -- State machine
    status              TEXT NOT NULL,               -- PENDING_FILL/OPEN/PARTIAL/CLOSED/CANCELLED/FAILED
    
    -- Metadata
    recovered_flag      INTEGER NOT NULL DEFAULT 0,  -- 1 if reconstructed during crash recovery
    entry_mode          TEXT NOT NULL DEFAULT 'FULL',-- FULL | SCALE (v2.1)
    order_protocol      TEXT NOT NULL,               -- CO_PLUS_TGT | LIMIT_TRIPLE

    -- v11 (E.4 / EF-5): capital reservation that funds this trade. Nullable
    -- for historical / recovered trades that predate the column. Populated
    -- at create_trade() time from signal_processor's reservation.
    reservation_id      TEXT,                        -- FK to fm_ledger.reservation_id (not enforced)

    updated_at          TEXT NOT NULL,

    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
);

CREATE INDEX IF NOT EXISTS idx_trades_status
    ON trades(status);

CREATE INDEX IF NOT EXISTS idx_trades_symbol
    ON trades(symbol);

CREATE INDEX IF NOT EXISTS idx_trades_created_at
    ON trades(created_at);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 4: orders
-- One row per broker order. A single trade has multiple orders over its
-- lifetime: ENTRY, SL, TGT, possibly multiple SL versions (trail updates),
-- possibly an EOD market exit.
--
-- The superseded_by column tracks the chain of replacements: when a trail
-- SL update creates a new SL order at the broker, the old SL row stays
-- (status=CANCELLED) but its superseded_by points to the new SL's order_id.
-- This preserves the full history without losing any record.
--
-- Decision refs: G2a (order_id), G10 (leg_index), P8/P13 (CO support)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS orders (
    order_id            TEXT PRIMARY KEY,            -- broker-assigned ID
    trade_id            TEXT NOT NULL,               -- FK to trades
    
    -- Logical role within the trade
    leg                 TEXT NOT NULL,               -- ENTRY/SL/TGT/EOD/CANCEL
    leg_index           INTEGER NOT NULL DEFAULT 0,  -- 0 for FULL entry; 0/1/2 for SCALE legs
    
    -- Order parameters as sent to broker
    transaction_type    TEXT NOT NULL,               -- BUY | SELL
    order_type          TEXT NOT NULL,               -- LIMIT/MARKET/SL-M/SL
    product             TEXT NOT NULL,               -- MIS/CNC/CO
    variety             TEXT NOT NULL,               -- regular | co
    qty_requested       INTEGER NOT NULL,
    price               REAL,                        -- LIMIT price; null for MARKET
    trigger_price       REAL,                        -- SL/SL-M trigger; null for LIMIT/MARKET
    
    -- Fill state (updated from broker polls)
    status              TEXT NOT NULL,               -- PENDING/OPEN/COMPLETE/CANCELLED/REJECTED/TRIGGER_PENDING
    qty_filled          INTEGER NOT NULL DEFAULT 0,
    avg_fill_price      REAL,
    
    -- Lifecycle
    placed_at           TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    
    -- Replacement chain (for trail SL updates that cancel-and-replace)
    superseded_by       TEXT,                        -- nullable FK → orders.order_id
    
    FOREIGN KEY (trade_id) REFERENCES trades(trade_id),
    FOREIGN KEY (superseded_by) REFERENCES orders(order_id)
);

CREATE INDEX IF NOT EXISTS idx_orders_trade_id
    ON orders(trade_id);

CREATE INDEX IF NOT EXISTS idx_orders_status
    ON orders(status);

CREATE INDEX IF NOT EXISTS idx_orders_leg
    ON orders(leg);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 5: capital_snapshot
-- Current capital state. Single-row table (CHECK enforces id = 1).
-- This is the "fast read" of capital. The capital_ledger is the audit trail.
--
-- Decision refs: P7a (3-balance model), G3 (invariant)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS capital_snapshot (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    
    -- The 3 balances (P7a)
    cash_floor          REAL NOT NULL,               -- broker-settled cash, withdrawable
    realized_pnl_today  REAL NOT NULL DEFAULT 0,     -- can be negative (losses) or positive
    margin_used         REAL NOT NULL DEFAULT 0,     -- sum of (qty * entry * 0.20) over open positions
    margin_reserved     REAL NOT NULL DEFAULT 0,     -- pending orders not yet filled
    
    -- Charges accumulated today (deducted from realized_pnl already)
    charges_today       REAL NOT NULL DEFAULT 0,
    
    -- Sync metadata
    last_broker_sync    TEXT,
    sync_source         TEXT,                        -- 'broker' | 'csv' | 'startup' | 'event'
    
    updated_at          TEXT NOT NULL
);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 6: (removed v10)
-- The legacy capital_ledger table was never written to; FundManager audits
-- capital mutations via fm_ledger (Table 9). BL-5 (v10) retires
-- capital_ledger entirely and extends fm_ledger with write-ahead semantics.
-- No CREATE statement here on purpose. Drop handled below for upgrade path.
-- ═════════════════════════════════════════════════════════════════════════════
DROP TABLE IF EXISTS capital_ledger;
DROP INDEX IF EXISTS idx_ledger_trade_id;
DROP INDEX IF EXISTS idx_ledger_timestamp;

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 7: system_events
-- Lifecycle marker log. Used by G5a startup detection to determine
-- COLD vs WARM vs CRASH vs HALT scenarios.
--
-- The critical event types:
--   STARTUP        — written at end of every successful startup (with scenario)
--   SHUTDOWN       — written at end of every CLEAN shutdown (its presence = WARM)
--   CRASH_DETECTED — written when startup detects no SHUTDOWN row for today
--   KILL_SWITCH    — written when kill_switch fires (soft or hard)
--   RECOVERY       — written when reconciler completes recovery procedure
--   CONFIG_DIFF    — written when config hash differs from last run
--
-- Decision refs: G5a (scenario detection), G4 (config hash diff)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS system_events (
    event_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    scenario            TEXT,                        -- COLD/WARM/CRASH/HALT (set on STARTUP)
    details             TEXT                         -- JSON blob with event-specific payload
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp
    ON system_events(timestamp);

CREATE INDEX IF NOT EXISTS idx_events_type
    ON system_events(event_type);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 8: session
-- Single-row table holding current session metadata + kill switch state +
-- daily counters. Lives in the same DB transaction context as everything
-- else, so kill_switch state changes are atomic with capital changes.
--
-- Decision refs: G5a (scenario detection), G5c (halt-type dependent restart),
--                Q1 (max_open_positions), all daily counters
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS session (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    
    -- Identity
    session_date        TEXT NOT NULL,               -- YYYY-MM-DD IST
    account_id          TEXT NOT NULL,
    broker              TEXT NOT NULL,
    mode                TEXT NOT NULL,               -- PAPER | LIVE
    trade_type          TEXT NOT NULL,               -- INTRADAY | POSITIONAL
    
    -- Kill switch state (here for atomicity with capital)
    kill_state          TEXT NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE/PAUSED/HARD_KILLED
    kill_reason         TEXT,
    kill_time           TEXT,
    kill_type           TEXT,                        -- soft | hard
    
    -- Carry-forward daily stats
    yesterday_pnl       REAL DEFAULT 0,
    yesterday_wins      INTEGER DEFAULT 0,
    yesterday_losses    INTEGER DEFAULT 0,
    
    -- Today's running counters
    consecutive_losses  INTEGER DEFAULT 0,
    
    -- Config tracking (G4 startup hash diff)
    last_config_hash    TEXT,
    
    -- Lifecycle
    session_start       TEXT NOT NULL,
    last_updated        TEXT NOT NULL
);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 9: fm_ledger  (BL-5: write-ahead capital ledger — v10)
-- Append-only write-ahead log for FundManager capital mutations (FM10).
-- One row per atomic capital operation. NEVER UPDATE OR DELETE a row.
-- Covers both intraday and positional buckets independently.
--
-- BL-5 contract (v10):
--   * The row is written BEFORE the in-memory state mutation (write-ahead).
--   * If the app crashes between INSERT and mutation, rehydrate (B.2 / BL-1)
--     replays fm_ledger rows to rebuild in-memory state.
--   * entry_type is a closed enum (CHECK) — catches typos at INSERT time.
--   * session_id correlates rows to a FundManager instance across restarts.
--
-- Decision refs: FM1-FM16 (capital/ layer), G3 (invariant auditing), BL-5 (WAL)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS fm_ledger (
    ledger_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  TEXT NOT NULL,               -- ISO-8601 IST
    entry_type          TEXT NOT NULL                -- BL-5: enum, was mutation_type
                        CHECK (entry_type IN
                               ('INIT','RESERVE','RELEASE','COMMIT',
                                'RELEASE_USED','SYNC','RESET_PNL')),
    amount              REAL NOT NULL,               -- positive = into reserved/used; negative = release
    bucket              TEXT NOT NULL,               -- 'intraday' | 'positional' | 'both'
    balance_before      REAL NOT NULL,               -- available before mutation (bucket-scoped)
    balance_after       REAL NOT NULL,               -- available after mutation (bucket-scoped)
    signal_id           TEXT,                        -- nullable; set on RESERVE from a signal
    reservation_id      TEXT,                        -- nullable; set on RESERVE/RELEASE/COMMIT
    reason              TEXT,                        -- free text; required on RELEASE (cancelled/rejected)
    -- BL-5 additions (v10):
    session_id          TEXT,                        -- correlates rows to a FundManager instance
    direction           TEXT,                        -- 'LONG' | 'SHORT' | NULL (set on RELEASE_USED; EF-3)
    trade_id            TEXT,                        -- nullable; set on COMMIT/RELEASE_USED when known
    margin_delta        REAL NOT NULL DEFAULT 0.0,   -- signed margin movement (+reserve, -release/release_used)
    pnl_delta           REAL NOT NULL DEFAULT 0.0,   -- realized PnL change (nonzero on RELEASE_USED only)
    costs               REAL NOT NULL DEFAULT 0.0    -- transaction costs (nonzero on RELEASE_USED only)
);

CREATE INDEX IF NOT EXISTS idx_fm_ledger_ts
    ON fm_ledger(ts);

CREATE INDEX IF NOT EXISTS idx_fm_ledger_reservation_id
    ON fm_ledger(reservation_id);

CREATE INDEX IF NOT EXISTS idx_fm_ledger_session_id
    ON fm_ledger(session_id);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 10: kill_switch_state
-- Single-row table holding the persistent kill switch state.
-- Used by KillSwitch.__init__ to recover state on restart (KS3 audit fix).
-- Without this table the system would overwrite operator manual halts on
-- restart (Audit Issue #18).
--
-- Decision refs: KS2 (state model), KS3 (startup recovery), KS9 (schema)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS kill_switch_state (
    id           INTEGER PRIMARY KEY CHECK (id = 1),  -- single row
    state        TEXT NOT NULL,                        -- INACTIVE/SOFT_KILL/HARD_KILL
    reason       TEXT NOT NULL,
    triggered_at TEXT NOT NULL,                        -- ISO-8601 IST
    triggered_by TEXT NOT NULL
);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 11: webhook_audit
-- Append-only audit row per POST to /webhook/<scanner_name>.
-- Written regardless of outcome (WR13). Used for forensics + rate analysis.
--
-- Decision refs: WR13 (audit log)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS webhook_audit (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  TEXT NOT NULL,               -- ISO-8601 IST when request arrived
    scanner_name        TEXT NOT NULL,
    source_ip           TEXT NOT NULL,
    payload_size_bytes  INTEGER NOT NULL,
    response_code       INTEGER NOT NULL,
    signals_accepted    INTEGER NOT NULL DEFAULT 0,
    signals_rejected    INTEGER NOT NULL DEFAULT 0,
    duration_ms         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webhook_audit_ts
    ON webhook_audit(ts);

CREATE INDEX IF NOT EXISTS idx_webhook_audit_scanner
    ON webhook_audit(scanner_name);

-- ═════════════════════════════════════════════════════════════════════════════
-- INITIALIZATION
-- ═════════════════════════════════════════════════════════════════════════════
-- Schema version marker. Application code reads this on startup and
-- runs migrations if it differs from the code's expected version.
-- OR REPLACE so that re-running this script on an existing DB bumps the version.
-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 12: eod_squareoff_log
-- One row per EOD square-off fire. UNIQUE on fired_date — only one EOD run
-- per trading day is recorded. Used by EOD9 restart recovery check and by
-- the daily report.
--
-- Decision refs: EOD8 (schema spec), EOD9 (restart recovery queries this)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS eod_squareoff_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    fired_date              TEXT NOT NULL,           -- YYYY-MM-DD IST (unique per day)
    fired_at                TEXT NOT NULL,           -- ISO-8601 IST timestamp of fire
    positions_attempted     INTEGER NOT NULL DEFAULT 0,
    positions_succeeded     INTEGER NOT NULL DEFAULT 0,
    positions_failed        INTEGER NOT NULL DEFAULT 0,
    cancels_attempted       INTEGER NOT NULL DEFAULT 0,
    cancels_succeeded       INTEGER NOT NULL DEFAULT 0,
    cancels_failed          INTEGER NOT NULL DEFAULT 0,
    duration_sec            REAL NOT NULL DEFAULT 0.0,
    -- v11 (E.4 / M-3): write-ahead status. IN_PROGRESS row is written at
    -- fire start; UPDATE to COMPLETE after squareoff finishes. Restart
    -- recovery distinguishes: IN_PROGRESS -> crash mid-fire, re-run
    -- recovery; COMPLETE -> already done today, skip. Recovery failure
    -- leaves row IN_PROGRESS so the operator is alerted (no auto-retry).
    status                  TEXT NOT NULL DEFAULT 'COMPLETE',  -- IN_PROGRESS | COMPLETE
    completed_at            TEXT                     -- ISO-8601 IST; NULL until COMPLETE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_eod_squareoff_log_date
    ON eod_squareoff_log(fired_date);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 13: reconciliation_log
-- One row per reconciliation action per cycle. Written by order_reconciler
-- after each check fires an action. Append-only audit of all reconciler
-- decisions — cosmetic (no-ops not written), recoverable, and unrecoverable.
--
-- Decision refs: RC10 (schema spec), G1 (6 checks + 3-tier policy)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS reconciliation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,           -- ISO-8601 IST
    check_name      TEXT NOT NULL,           -- e.g. MANUAL_CLOSE, ORPHAN_ADOPTION
    tier            TEXT NOT NULL,           -- COSMETIC | RECOVERABLE | UNRECOVERABLE
    symbol          TEXT NOT NULL,
    trade_id        TEXT,                    -- nullable (some checks are position-less)
    description     TEXT NOT NULL,           -- human-readable drift description
    action_taken    TEXT NOT NULL,           -- what reconciler did
    success         INTEGER NOT NULL         -- 1 = action succeeded; 0 = action failed
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_log_ts
    ON reconciliation_log(ts);

CREATE INDEX IF NOT EXISTS idx_reconciliation_log_trade_id
    ON reconciliation_log(trade_id);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 14: screener_results
-- One row per secondary_screener.screen() call. Append-only audit of all
-- screening decisions (PASSED / REJECTED_* / SKIPPED_*). Used by daily
-- report for screening analytics (SS6).
--
-- Decision refs: SS5 (P18 persistence), SS6 (table spec)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS screener_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id               TEXT NOT NULL,
    score                   INTEGER NOT NULL,
    tier                    TEXT NOT NULL,
    status                  TEXT NOT NULL,
    step_results            TEXT NOT NULL,
    latencies               TEXT NOT NULL,
    market_data_snapshot    TEXT NOT NULL,
    ts                      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_screener_results_signal_id
    ON screener_results(signal_id);

CREATE INDEX IF NOT EXISTS idx_screener_results_ts
    ON screener_results(ts);

CREATE INDEX IF NOT EXISTS idx_screener_results_status
    ON screener_results(status);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 15: smart_tgt_state
-- Per-trade trailing SL state for SmartTgtManager crash recovery (ST15).
-- One row per active tracked trade. Deleted when trade is unregistered.
-- Repopulated on restart to resume trailing from last known SL level.
--
-- Decision refs: ST15 (schema spec), ST8 (startup recovery), G6 (reconnect)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS smart_tgt_state (
    trade_id            TEXT PRIMARY KEY,
    symbol              TEXT NOT NULL,
    instrument_token    INTEGER NOT NULL,
    direction           TEXT NOT NULL,            -- LONG | SHORT
    entry_price         REAL NOT NULL,
    initial_sl          REAL NOT NULL,
    current_sl          REAL NOT NULL,            -- updated after each confirmed trail
    qty                 INTEGER NOT NULL,
    trigger_pct         REAL NOT NULL,            -- from strategy.smart_tgt_trail_trigger_pct
    step_pct            REAL NOT NULL,            -- from strategy.smart_tgt_trail_step_pct
    best_price          REAL,                     -- most favorable price seen; nullable (none yet)
    trail_count         INTEGER NOT NULL DEFAULT 0,
    last_trail_ts       TEXT,                     -- ISO-8601 IST of last trail; nullable
    registered_at       TEXT NOT NULL             -- ISO-8601 IST when trade was registered
);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 16: innings
-- One row per simulated inning tracked by shadow_tracker. Inning 1 is always
-- real (is_real=1) and mirrors the original trade's entry/exit. Innings 2 and 3
-- are simulated (is_real=0): no real broker orders, pure price watching.
--
-- UNIQUE(trade_id, inning_number): each trade can have at most 3 innings.
-- Indexes support fast lookup by trade and by date for daily report.
--
-- Decision refs: SH1 (Inning dataclass), SH9 (schema spec), SH10 (helpers)
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS innings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        TEXT NOT NULL,
    inning_number   INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,       -- LONG | SHORT
    entry_price     REAL NOT NULL,
    entry_ts        TEXT NOT NULL,       -- ISO-8601 IST
    sl_price        REAL NOT NULL,
    tgt_price       REAL NOT NULL,
    exit_price      REAL,                -- nullable until closed
    exit_ts         TEXT,                -- nullable until closed
    exit_reason     TEXT,                -- SL | TGT | EOD | OPEN; null until closed
    duration_sec    INTEGER,             -- nullable until closed
    pnl_pct         REAL,                -- nullable until closed
    pnl_per_share   REAL,                -- nullable until closed
    is_real         INTEGER NOT NULL,    -- 1 = real (inning 1); 0 = simulated

    UNIQUE(trade_id, inning_number)
);

CREATE INDEX IF NOT EXISTS idx_innings_trade
    ON innings(trade_id);

CREATE INDEX IF NOT EXISTS idx_innings_date
    ON innings(substr(entry_ts, 1, 10));

-- ─────────────────────────────────────────────────────────────────────────────
-- SCHEMA VERSION BUMP: v10 -> v11
-- ─────────────────────────────────────────────────────────────────────────────
INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '11');

-- ─────────────────────────────────────────────────────────────────────────────
-- END OF SCHEMA v11 (v1: tables 1-8; v2: +fm_ledger; v3: +kill_switch_state;
--                    v4: +webhook_audit, signals.trigger_price;
--                    v5: +eod_squareoff_log; v6: +reconciliation_log;
--                    v7: +screener_results; v8: +smart_tgt_state;
--                    v9: +innings;
--                    v10: -capital_ledger (dead); fm_ledger becomes write-ahead
--                          + entry_type CHECK + session_id/direction/trade_id/
--                          margin_delta/pnl_delta/costs columns;
--                    v11: +eod_squareoff_log.status/completed_at (M-3 write-
--                          ahead); +trades.reservation_id (EF-5 capital flow))
-- ─────────────────────────────────────────────────────────────────────────────
