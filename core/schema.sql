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
    status              TEXT NOT NULL                -- QUEUED/PASSED/PROCESSED/REJECTED*/GATE_*/DROPPED_*/SKIPPED_*
                        -- O2 (v25): signal status is an OPEN set written by many
                        -- modules (webhook_receiver, signal_processor, secondary_
                        -- screener, entry_gate). Four open prefix families are
                        -- allowed via GLOB so new sub-statuses never break an INSERT;
                        -- stable non-prefixed values are enumerated. This is a
                        -- shape/typo guard (rejects lowercase, empty, wrong constants
                        -- like 'OPEN'/'FILLED'), NOT a closed enum — by design,
                        -- because a wrongly-rejected signal write would crash the
                        -- pipeline. Verified complete against the full test suite.
                        CHECK (status IN (
                            'QUEUED','ACCEPTED','PROCESSING','PROCESSED',
                            'PROCESSED_NO_PLACER','PLACEMENT_FAILED','RESERVED',
                            'QUEUE_FULL','PASSED','TRADED','DUPLICATE','EXPIRED',
                            'INVALID_SYMBOL','INVALID_PRICE','OUTSIDE_HOURS',
                            'IN_PROCESS','CANCELLED','FAILED','PENDING','TIMEOUT')
                            OR status GLOB 'REJECTED*'
                            OR status GLOB 'DROPPED_*'
                            OR status GLOB 'SKIPPED_*'
                            OR status GLOB 'GATE_*'
                            OR status GLOB 'RETEST_*'),  -- SNR-V2: WAIT_FOR_RETEST parking states
    rejection_reason    TEXT,                        -- nullable, free text or step name
    trade_id            TEXT,                        -- nullable FK; set if signal became a trade
    trigger_price       REAL,                        -- price from Chartink at trigger time
    fingerprint         TEXT NOT NULL,               -- hash(scanner+symbol+trigger_minute) for P6 dedup
    fingerprint_date    TEXT NOT NULL,               -- YYYY-MM-DD of received_at, for unique index
    webhook_payload     TEXT,                        -- v14: raw JSON from Chartink for forensics

    FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
);

-- P6 dedup: same scanner + same symbol + same minute on same day = duplicate
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_fingerprint_today
    ON signals(fingerprint, fingerprint_date);

CREATE INDEX IF NOT EXISTS idx_signals_status
    ON signals(status);

CREATE INDEX IF NOT EXISTS idx_signals_received_at
    ON signals(received_at);

-- O3 (v25): back-ref lookup trades -> signals via signals.trade_id
CREATE INDEX IF NOT EXISTS idx_signals_trade_id
    ON signals(trade_id);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 3: trades
-- One row per logical position lifecycle. Created at capital reservation.
-- A trade may have many orders (entry + SL + TGT + modifications) — see
-- the orders table for that 1:N relationship.
--
-- Decision refs: G2a (trade_id), P7a (capital), P8/P13 (order_protocol),
--                G10 (entry_mode), G5a (recovered_flag)
--
-- O9 — SOFT CIRCULAR REFERENCE (intentional design, no fix needed):
--   signals.trade_id  <->  trades.signal_id  form a mutual reference.
--   trades.signal_id is set at creation (NOT NULL, immutable) and carries
--   the enforced FK -> signals(signal_id).
--   signals.trade_id is set post-fill (nullable, updated after the trade
--   opens) and carries a FK -> trades(trade_id).
--   FK enforcement on BOTH directions is safe here because each side is
--   populated at a different lifecycle moment (trade created first with its
--   signal_id; signal back-ref filled in afterwards), so neither INSERT
--   violates the other's FK at write time. The 1:N direction of record is
--   signals(1) -> trades(N); signals.trade_id is a convenience back-ref.
--   See docs/audit/db_schema_review_15jun2026.md O9 for rationale.
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
    status              TEXT NOT NULL                -- PENDING_FILL/OPEN/PARTIAL/EXITING/CLOSED/CANCELLED/FAILED/REJECTED*
                        -- O2 (v25): REJECTED* (e.g. REJECTED, REJECTED_PRICE_DRIFT
                        -- from order_placer placement-failure paths) allowed via GLOB.
                        -- FIX-179 (v29): EXITING — transitional state set by
                        -- kill_switch hard_kill while a MARKET exit is in flight
                        -- (before the fill handler / reconciler closes the row).
                        CHECK (status IN (
                            'PENDING','PENDING_FILL','OPEN','PARTIAL','EXITING','CLOSED',
                            'CLOSED_MANUAL','CANCELLED','FAILED','UNKNOWN_IN_FLIGHT')
                            OR status GLOB 'REJECTED*'),
    
    -- Metadata
    recovered_flag      INTEGER NOT NULL DEFAULT 0,  -- 1 if reconstructed during crash recovery
    entry_mode          TEXT NOT NULL DEFAULT 'FULL',-- FULL | SCALE (v2.1)
    order_protocol      TEXT NOT NULL,               -- CO_PLUS_TGT | LIMIT_TRIPLE

    -- v11 (E.4 / EF-5): capital reservation that funds this trade. Nullable
    -- for historical / recovered trades that predate the column. Populated
    -- at create_trade() time from signal_processor's reservation.
    reservation_id      TEXT,                        -- FK to fm_ledger.reservation_id (not enforced)

    -- v14: cost breakdown (previously single 'charges' float; components now stored)
    cost_brokerage      REAL,
    cost_stt            REAL,
    cost_exchange_txn   REAL,
    cost_sebi           REAL,
    cost_gst            REAL,
    cost_stamp_duty     REAL,

    -- v14: per-trade mode + SL trail analytics
    mode                TEXT,                        -- PAPER | LIVE
    sl_trail_count      INTEGER DEFAULT 0,

    -- v17 (FIX-130 Item 5): signal-to-fill latency in milliseconds.
    -- signal_to_order_ms: signals.received_at → entry order.placed_at
    -- order_to_fill_ms:   entry order.placed_at → entry order.filled_at
    -- total_latency_ms:   signals.received_at → entry order.filled_at
    signal_to_order_ms  INTEGER,
    order_to_fill_ms    INTEGER,
    total_latency_ms    INTEGER,

    -- v30 (Task: TGT retry): when a LIMIT_TRIPLE TGT leg cannot be placed but
    -- the SL is standing (FIX-190 Bug C — e.g. circuit band / transient broker
    -- reject), the trade is flagged here so TGTRetryManager re-attempts the TGT
    -- on a backoff schedule WITHOUT disturbing the live SL. Cleared on success
    -- or give-up. Survives restart (read from DB each retry cycle).
    needs_tgt_retry     INTEGER NOT NULL DEFAULT 0,   -- 1 = TGT placement owed
    tgt_retry_count     INTEGER NOT NULL DEFAULT 0,   -- attempts made so far
    tgt_last_retry_at   TEXT,                          -- ISO IST of last attempt (NULL = none yet)

    -- Slippage tolerance override (Phase 3a, v32). Which entry-slippage rule
    -- supplied the sl_fraction at placement (Symbol > Strategy > Band > Global),
    -- recorded for transparency + Phase-3b effectiveness analysis (join to
    -- trade_slippage_log.rr_damage_pct on trade_id). NULL for non-sl_fraction
    -- modes / recovered trades.
    tolerance_fraction_used REAL,                      -- resolved fraction (e.g. 0.22 / 0.15)
    tolerance_source        TEXT,                      -- "symbol:IDEA" / "strategy:gap_fade" / "band:0-100" / "global"

    -- Sizing audit (Diary #4, v34). One row per trade = one sizing DECISION: how qty
    -- was sized — tier x perf (ON) vs flat Rs/order (OFF) — the candidate qtys, which
    -- constraint bound it, and the final rupee exposure. Populated at create_trade()
    -- from PositionSizer's breakdown. NULL on recovered / pre-v34 trades.
    tier_multiplier_mode      TEXT,     -- 'ON' | 'OFF_FLAT'
    tier_weight_applied       REAL,     -- tier multiplier applied (NULL when OFF)
    perf_weight_applied       REAL,     -- perf weight applied (NULL when OFF)
    flat_value_rs_used        REAL,     -- flat Rs/order target (NULL when ON)
    qty_by_risk               INTEGER,  -- candidate qty from risk_per_trade
    qty_by_capital            INTEGER,  -- candidate qty from available bucket capital
    qty_by_concentration      INTEGER,  -- candidate qty from max_concentration_pct
    qty_by_flat               INTEGER,  -- candidate qty from flat_value_rs (NULL when ON)
    binding_constraint        TEXT,     -- risk | capital | concentration | flat
    actual_position_value_rs  REAL,     -- final qty * entry_price

    -- R:R fix (Slice 1, v35). The originating strategy's tgt_risk_reward, FROZEN
    -- at placement so the fill-time TGT recalc honours THIS trade's configured
    -- R:R instead of order_placer's hardcoded default. Read at fill by
    -- _place_*_exits; NULL on recovered / pre-v35 trades -> fill falls back to
    -- order_placer._rr_ratio (2.0) + a WARNING.
    tgt_risk_reward_applied   REAL,     -- strategy R:R frozen at placement (NULL = use fallback)

    -- SL/TGT placement after-check (Slice 1, v35). Written at fill AFTER the
    -- deferred SL/TGT exits are placed: did they land at the intended price + qty?
    -- Unlike the write-only v34 sizing columns, these are READ-BACK (the check
    -- compares intended vs actual and records the verdict). NULL until exits placed.
    exits_verified            INTEGER,  -- 1 = SL+TGT verified ok, 0 = mismatch (see detail)
    exits_verify_detail       TEXT,     -- 'ok' or human-readable mismatch description

    updated_at          TEXT NOT NULL,

    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
);

CREATE INDEX IF NOT EXISTS idx_trades_status
    ON trades(status);

CREATE INDEX IF NOT EXISTS idx_trades_symbol
    ON trades(symbol);

CREATE INDEX IF NOT EXISTS idx_trades_created_at
    ON trades(created_at);

-- O3 (v25): join to signals + back-ref lookup via trades.signal_id
CREATE INDEX IF NOT EXISTS idx_trades_signal_id
    ON trades(signal_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Terminal-state write guard (Wave-5, 2026-07-07) — Audit-B Phase-4 rec #5.
-- The trades.status CHECK above validates the value SET, not TRANSITIONS: a raw
-- UPDATE could reopen an absorbing/terminal row (e.g. CLOSED -> OPEN), which the
-- reconciler would then try to re-protect / re-exit after capital was already
-- released. This trigger is the data-layer keystone: it forbids CHANGING status
-- once the row is terminal, catching EVERY writer including any future raw SQL.
--
-- Scope is deliberately a TERMINAL-LOCK, not a full transition matrix — every
-- legal edge (OPEN/PARTIAL/EXITING -> CLOSED incl. H-2 EXITING->CLOSED, the
-- recovery flows, EXITING->OPEN revert) originates from a NON-terminal state and
-- is untouched. Two narrowing conditions keep it from over-firing:
--   * BEFORE UPDATE OF status  -> fires ONLY when an UPDATE writes the status
--     column, so the exit-financial backfills (record_manual_close_financials,
--     record_gtt_close_financials) that write price/pnl onto an already-CLOSED
--     row WITHOUT touching status pass through untouched.
--   * WHEN NEW.status <> OLD.status -> idempotent same-status writes pass.
-- RAISE(ABORT) surfaces in Python as sqlite3.IntegrityError and rolls back only
-- the offending statement. This is a BEHAVIOUR-ONLY pure add: CREATE TRIGGER IF
-- NOT EXISTS is re-applied idempotently by executescript on every boot (after any
-- migration rebuild), so it needs NO schema_version bump (stays v41).
CREATE TRIGGER IF NOT EXISTS trg_trades_terminal_status_guard
BEFORE UPDATE OF status ON trades
FOR EACH ROW
WHEN (OLD.status IN ('CLOSED', 'CLOSED_MANUAL', 'FAILED', 'CANCELLED')
      OR OLD.status GLOB 'REJECTED*')
BEGIN
    SELECT CASE WHEN NEW.status <> OLD.status
        THEN RAISE(ABORT, 'illegal terminal-state transition on trades.status')
    END;
END;

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
    leg                 TEXT NOT NULL                -- ENTRY/SL/TGT/EOD/CO/CANCEL
                        -- O2 (v25): CO = CO-bracket entry leg (order_protocol_co).
                        CHECK (leg IN ('ENTRY','SL','TGT','EOD','CO','CANCEL')),
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
    status              TEXT NOT NULL                -- OSM STATES (broker/order_state_machine.py)
                        -- O2 (v25): OrderStateMachine.STATES is the write vocabulary;
                        -- Kite "REJECTED" maps to FAILED in order_monitor and no raw
                        -- broker strings reach this column. TRIGGER_PENDING (both
                        -- underscore and Kite's space form) is also permitted because
                        -- three read-sites filter on it for resting SL orders and
                        -- wrongly rejecting an SL status write would mean a naked
                        -- position — the worst outcome. Cost of permitting it: nil.
                        CHECK (status IN (
                            'PENDING','SUBMITTED','OPEN','PARTIAL','COMPLETE',
                            'CANCELLED','FAILED','EXPIRED','UNKNOWN_IN_FLIGHT',
                            'TRIGGER_PENDING','TRIGGER PENDING')),
    qty_filled          INTEGER NOT NULL DEFAULT 0,
    avg_fill_price      REAL,
    
    -- Lifecycle
    placed_at           TEXT NOT NULL,
    filled_at           TEXT,                        -- v14: exact fill timestamp
    updated_at          TEXT NOT NULL,

    -- v14: broker rejection details
    rejection_reason    TEXT,

    -- v16 (FIX-129 Item 26): reconciliation result from order_reconciler.
    -- NULL=not yet checked; OK=verified at broker; MISMATCH=local≠broker;
    -- SL_MISSING=SL order not found at broker (naked position detected).
    reconciliation_status TEXT,

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

-- O3 (v25): SL trail replacement-chain walks via orders.superseded_by.
-- Partial index: only the (few) superseded rows carry a non-NULL value.
CREATE INDEX IF NOT EXISTS idx_orders_superseded_by
    ON orders(superseded_by) WHERE superseded_by IS NOT NULL;

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

    -- 2026-04-26 audit CFG-7 dropped session.kill_state / kill_reason /
    -- kill_time / kill_type. The kill_switch_state table is the single
    -- source of truth (KS3). Audit NSK-1 also dropped yesterday_pnl /
    -- yesterday_wins / yesterday_losses / consecutive_losses; the values
    -- were never written, and risk_engine recomputes consecutive_losses
    -- from recent_trade_pnls() each cycle.

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
                                'RELEASE_USED','SYNC','RESET_PNL','TOP_UP')),
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
    costs               REAL NOT NULL DEFAULT 0.0,   -- transaction costs (nonzero on RELEASE_USED only)
    -- O4 (v27): stored YYYY-MM-DD (== DATE(ts) for ISO-8601 ts) so the
    -- daily-loss query can use an index instead of wrapping ts in DATE().
    date                TEXT GENERATED ALWAYS AS (substr(ts, 1, 10)) STORED
);

CREATE INDEX IF NOT EXISTS idx_fm_ledger_ts
    ON fm_ledger(ts);

CREATE INDEX IF NOT EXISTS idx_fm_ledger_date
    ON fm_ledger(date);

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
    state        TEXT NOT NULL                         -- INACTIVE/SOFT_KILL/HARD_KILL
                 CHECK (state IN ('INACTIVE','SOFT_KILL','HARD_KILL')),
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
    duration_ms         INTEGER NOT NULL,
    -- O4 (v27): stored YYYY-MM-DD (== DATE(ts)) for indexed date queries.
    date                TEXT GENERATED ALWAYS AS (substr(ts, 1, 10)) STORED
);

CREATE INDEX IF NOT EXISTS idx_webhook_audit_ts
    ON webhook_audit(ts);

CREATE INDEX IF NOT EXISTS idx_webhook_audit_date
    ON webhook_audit(date);

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
    success         INTEGER NOT NULL,        -- 1 = action succeeded; 0 = action failed

    -- O1 (v26): trade_id is nullable (position-less checks log NULL, which is
    -- FK-exempt). The insert site (order_reconciler) wraps this in try/except,
    -- so a rare orphan-scenario FK rejection drops one audit row with an error
    -- log rather than crashing the reconciler.
    FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
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
    ts                      TEXT NOT NULL,
    eligible_score          INTEGER,             -- v14: per-strategy min_score threshold

    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)  -- O1 (v26)
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
    registered_at       TEXT NOT NULL,            -- ISO-8601 IST when trade was registered

    FOREIGN KEY (trade_id) REFERENCES trades(trade_id)  -- O1 (v26)
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

    UNIQUE(trade_id, inning_number),
    FOREIGN KEY (trade_id) REFERENCES trades(trade_id)  -- O1 (v26)
);

CREATE INDEX IF NOT EXISTS idx_innings_trade
    ON innings(trade_id);

CREATE INDEX IF NOT EXISTS idx_innings_date
    ON innings(substr(entry_ts, 1, 10));

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 17: gate_state  (Audit 4.4)
-- One row per signal currently held in the EntryGate. Persisted so that a
-- mid-session restart can rehydrate the in-memory _watchlist and resume
-- watching at the same prices/timeouts. Mirrors the FundManager
-- rehydrate_from_open_trades pattern.
--
-- Lifecycle: written on entry_gate.add(); deleted on entry_gate._release().
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS gate_state (
    signal_id        TEXT PRIMARY KEY,
    symbol           TEXT NOT NULL,
    direction        TEXT NOT NULL,         -- LONG | SHORT
    trigger_price    REAL NOT NULL,
    entry_price      REAL NOT NULL,
    sl_price         REAL NOT NULL,
    tgt_price        REAL NOT NULL,
    tolerance_pct    REAL NOT NULL,
    timeout_sec      INTEGER NOT NULL,
    strategy_name    TEXT NOT NULL,
    tier             TEXT NOT NULL,
    scanner_name     TEXT NOT NULL,
    intent           TEXT NOT NULL,
    added_at         TEXT NOT NULL,         -- naive IST ISO-8601
    extras_json      TEXT,                  -- nullable JSON blob

    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
);

CREATE INDEX IF NOT EXISTS idx_gate_state_added_at
    ON gate_state(added_at);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 18: candles  -- RELOCATED (O6, v28) to analytics.db
-- The candles table now lives in analytics.db (see core/analytics_schema.sql
-- and core/db_connect.py). It is ATTACHed as schema `analytics` on every
-- connection, so unqualified `FROM candles` still resolves. Kept out of the
-- trading DB so the nightly .backup stays small and fast.
-- ═════════════════════════════════════════════════════════════════════════════

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 19: trade_excursions  (v14)
-- Per-trade MFE/MAE and entry candle snapshot. Written POST-EOD by
-- scripts/reconstruct_excursions.py (MFE/MAE Option B, 2026-06-28) — NOT on the
-- hot exit path (intraday-exit candles don't exist until the 15:40 backfill).
-- Enables trade quality analysis (how much heat was taken, how much
-- profit was left on the table).
-- SIGN CONVENTION (SIGNED): mfe_pct = best FAVOURABLE move vs entry (MAY be
-- negative if the trade never traded favourable); mae_pct = worst ADVERSE move
-- vs entry (MAY be positive if it never went adverse). Direction-signed
-- (LONG/SHORT inverted), NOT floored at zero. 1-min-candle reconstruction, so a
-- sub-minute trade gets no row.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS trade_excursions (
    trade_id           TEXT PRIMARY KEY,
    mfe_price          REAL,                         -- most favorable price during trade
    mfe_pct            REAL,                         -- % from entry
    mae_price          REAL,                         -- most adverse price during trade
    mae_pct            REAL,                         -- % from entry
    entry_candle_open  REAL,
    entry_candle_high  REAL,
    entry_candle_low   REAL,
    entry_candle_close REAL,
    updated_at         TEXT NOT NULL,
    FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 20: pnl_reconciliation  (v15 / FIX-128 Fix B)
-- Daily EOD record comparing broker-reported P&L against system net_pnl.
-- Written by scripts/reconcile_pnl.py at 16:15 IST after market close.
-- Status: OK | VARIANCE_MINOR | VARIANCE_MAJOR | PAPER_SKIPPED | ERROR
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS pnl_reconciliation (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL UNIQUE,          -- YYYY-MM-DD
    broker_pnl      REAL,                          -- null when broker fetch skipped (paper)
    system_pnl      REAL NOT NULL,                 -- sum(trades.net_pnl) for closed trades today
    variance        REAL,                          -- abs(broker_pnl - system_pnl); null if paper
    status          TEXT NOT NULL,                 -- OK | VARIANCE_MINOR | VARIANCE_MAJOR | PAPER_SKIPPED | ERROR
    notes           TEXT,                          -- human-readable explanation; null if OK
    created_at      TEXT NOT NULL                  -- ISO-8601 IST
);

CREATE INDEX IF NOT EXISTS idx_pnl_reconciliation_date
    ON pnl_reconciliation(date);

-- ─────────────────────────────────────────────────────────────────────────────
-- SCHEMA VERSION BUMP: v16 -> v17
-- ─────────────────────────────────────────────────────────────────────────────
-- ─────────────────────────────────────────────────────────────────────────────
-- TABLE 21: telegram_alerts — FIX-131 Item 18: Telegram alert delivery log
-- ─────────────────────────────────────────────────────────────────────────────
-- Tracks every Telegram alert attempt for audit + startup retry of CRITICAL.
-- status: SENT | FAILED | PENDING
CREATE TABLE IF NOT EXISTS telegram_alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at       TEXT NOT NULL,           -- ISO-8601 IST
    severity      TEXT NOT NULL,           -- CRITICAL | ERROR | WARN | INFO
    title         TEXT NOT NULL,
    body          TEXT,
    status        TEXT NOT NULL DEFAULT 'PENDING', -- SENT | FAILED | PENDING
    attempts      INTEGER NOT NULL DEFAULT 0,
    source_module TEXT
);

CREATE INDEX IF NOT EXISTS idx_telegram_alerts_status_severity
    ON telegram_alerts(status, severity);

-- TABLE 22: trade_journal — FIX-133 Item 30: daily trade journal
-- ─────────────────────────────────────────────────────────────────────────────
-- Structured trade journal for pattern analysis. Populated after EOD.
CREATE TABLE IF NOT EXISTS trade_journal (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    date                TEXT NOT NULL,           -- YYYY-MM-DD
    trade_id            TEXT NOT NULL,
    strategy            TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    direction           TEXT NOT NULL,           -- LONG | SHORT
    entry_reason        TEXT,                    -- scanner + screener score
    exit_reason         TEXT,                    -- SL_HIT/TGT_HIT/EOD/etc
    slippage_assessment TEXT,                    -- HIGH | LOW
    mfe_captured_pct    REAL,                    -- (exit-entry)/(mfe-entry)*100
    entry_price         REAL,
    exit_price          REAL,
    net_pnl             REAL,
    notes               TEXT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
);

CREATE INDEX IF NOT EXISTS idx_trade_journal_date ON trade_journal(date);
CREATE INDEX IF NOT EXISTS idx_trade_journal_strategy ON trade_journal(strategy);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 23: position_reconciliation  (v20 / FIX-134 Item 31)
-- Per-symbol daily position comparison: broker vs system.
-- Written by scripts/reconcile_positions.py at 15:45 IST after market close.
-- Status: OK | ORPHAN_AT_BROKER | MISSING_AT_BROKER | QTY_MISMATCH | ERROR
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS position_reconciliation (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,                  -- YYYY-MM-DD
    symbol          TEXT NOT NULL,
    broker_qty      INTEGER,                       -- null on error
    system_qty      INTEGER,
    status          TEXT NOT NULL,                  -- OK | ORPHAN_AT_BROKER | MISSING_AT_BROKER | QTY_MISMATCH | ERROR
    resolved_at     TEXT,                           -- ISO-8601 IST; null until manually resolved
    created_at      TEXT NOT NULL                   -- ISO-8601 IST
);

CREATE INDEX IF NOT EXISTS idx_position_reconciliation_date
    ON position_reconciliation(date);

CREATE INDEX IF NOT EXISTS idx_position_reconciliation_status
    ON position_reconciliation(status);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 24: strategy_metrics  (v20 / FIX-134 Item 36)
-- Per-strategy daily performance metrics (Sharpe, win rate, etc).
-- Computed at EOD by scripts/compute_strategy_metrics.py.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS strategy_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy        TEXT NOT NULL,
    date            TEXT NOT NULL,                  -- YYYY-MM-DD
    sharpe          REAL,                           -- annualized Sharpe ratio
    win_rate        REAL,                           -- fraction of winning trades (0-1)
    avg_pnl         REAL,                           -- average net P&L per trade
    total_trades    INTEGER DEFAULT 0,
    computed_at     TEXT NOT NULL,                   -- ISO-8601 IST
    UNIQUE(strategy, date)
);

CREATE INDEX IF NOT EXISTS idx_strategy_metrics_strategy
    ON strategy_metrics(strategy);

CREATE INDEX IF NOT EXISTS idx_strategy_metrics_date
    ON strategy_metrics(date);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 25: shadow_trades  (v21 / FIX-135 Item 41)
-- Shadow paper engine: simulated trades run in parallel with live.
-- Entry at signal trigger price; exit at SL/TGT/EOD simulation.
-- Used for regret analysis in daily report.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS shadow_trades (
    shadow_trade_id     TEXT PRIMARY KEY,
    date                TEXT NOT NULL,                  -- YYYY-MM-DD
    signal_id           TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    strategy            TEXT NOT NULL,
    direction           TEXT NOT NULL,                  -- LONG | SHORT
    entry_price         REAL NOT NULL,
    qty                 INTEGER NOT NULL DEFAULT 0,
    sys_sl              REAL NOT NULL,
    sys_tgt             REAL NOT NULL,
    live_trade_id       TEXT,                           -- FK to trades.trade_id (nullable if live rejected)
    live_status         TEXT DEFAULT 'UNKNOWN',         -- TRADED | REJECTED | CANCELLED
    simulated_exit_price REAL,
    simulated_exit_reason TEXT,                         -- SL_HIT | TGT_HIT | EOD
    simulated_pnl       REAL,
    created_at          TEXT NOT NULL,

    -- O1 (v26): signal_id always set; live_trade_id nullable (NULL = live
    -- rejected/cancelled), FK-exempt when NULL.
    FOREIGN KEY (signal_id) REFERENCES signals(signal_id),
    FOREIGN KEY (live_trade_id) REFERENCES trades(trade_id)
);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_date
    ON shadow_trades(date);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_signal
    ON shadow_trades(signal_id);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 26: fno_ban  (v21 / FIX-135 Item 44)
-- Daily F&O ban list from NSE. Fetched at 08:30 IST.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS fno_ban (
    symbol          TEXT NOT NULL,
    ban_date        TEXT NOT NULL,                  -- YYYY-MM-DD
    fetched_at      TEXT NOT NULL,                  -- ISO-8601 IST
    PRIMARY KEY (symbol, ban_date)
);

CREATE INDEX IF NOT EXISTS idx_fno_ban_date
    ON fno_ban(ban_date);

-- ─────────────────────────────────────────────────────────────────────────────
-- TABLE 27: eod_verification — FIX-137 Item 59
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS eod_verification (
    date            TEXT NOT NULL PRIMARY KEY,       -- YYYY-MM-DD
    open_trades     INTEGER NOT NULL DEFAULT 0,
    pending_orders  INTEGER NOT NULL DEFAULT 0,
    pnl_variance    REAL NOT NULL DEFAULT 0.0,
    status          TEXT NOT NULL DEFAULT 'VERIFIED', -- VERIFIED | ISSUES_FOUND
    verified_at     TEXT NOT NULL                     -- ISO-8601 IST
);

-- ─────────────────────────────────────────────────────────────────────────────
-- TABLE 28: cron_heartbeat  (v23 / FIX-145)
-- Records successful cron job executions. Each cron job writes a row on success.
-- Used by scripts/check_cron_drift.py to detect missing heartbeats.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cron_heartbeat (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name        TEXT NOT NULL,                  -- e.g., "daily_report", "token_watcher"
    executed_at     TEXT NOT NULL,                  -- ISO-8601 IST when job ran
    status          TEXT NOT NULL DEFAULT 'SUCCESS', -- SUCCESS | PARTIAL | FAILED
    duration_sec    REAL,                           -- job duration in seconds
    message         TEXT                            -- optional diagnostic
);

CREATE INDEX IF NOT EXISTS idx_cron_heartbeat_job_name
    ON cron_heartbeat(job_name);

CREATE INDEX IF NOT EXISTS idx_cron_heartbeat_executed_at
    ON cron_heartbeat(executed_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- TABLE 29: system_metrics        -- RELOCATED (O6, v28) to analytics.db
-- TABLE 30: system_metrics_daily  -- RELOCATED (O6, v28) to analytics.db
-- Both now live in analytics.db (see core/analytics_schema.sql,
-- core/db_connect.py), ATTACHed as schema `analytics` on every connection so
-- unqualified `FROM system_metrics[_daily]` still resolves. Kept out of the
-- trading DB so the nightly .backup stays small and fast.
-- ═════════════════════════════════════════════════════════════════════════════
-- TABLES 31-33: Slippage / execution-intelligence RAW data layer (v31)
-- Append-only raw FACTS only — all analytics (band/strategy stats, tolerance
-- recommendations) are computed ON-DEMAND in reports (no aggregate tables, no
-- stale derived data). Recording is best-effort + decoupled (async event
-- subscribers) so it can NEVER block or fail trade execution.
-- ═════════════════════════════════════════════════════════════════════════════

-- TABLE 31: order_execution_log — one row per filled broker order/leg.
CREATE TABLE IF NOT EXISTS order_execution_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id         TEXT,
    parent_trade_id  TEXT,
    signal_id        TEXT,
    symbol           TEXT NOT NULL,
    strategy_name    TEXT,
    leg              TEXT NOT NULL,        -- ENTRY / SL / TGT
    order_type       TEXT,                 -- LIMIT / SL / CO ...
    side             TEXT,                 -- BUY / SELL
    intended_price   REAL,                 -- expected/planned price
    actual_price     REAL,                 -- avg fill price
    slippage_rs      REAL,                 -- adverse = positive (paid worse)
    slippage_pct     REAL,                 -- adverse = positive (OM9 convention)
    qty              INTEGER,
    filled_qty       INTEGER,
    is_partial       INTEGER DEFAULT 0,
    retry_count      INTEGER DEFAULT 0,
    status           TEXT,
    order_timestamp  TEXT,
    fill_timestamp   TEXT,
    exchange_timestamp TEXT,
    -- Slippage tolerance override (Phase 3a, v32) — copied from the parent trade
    -- onto the execution row so Phase-2/3b reports can see WHICH rule applied.
    tolerance_fraction_used REAL,    -- resolved sl_fraction at entry (NULL for non-sl_fraction)
    tolerance_source        TEXT,    -- "symbol:IDEA" / "strategy:gap_fade" / "band:0-100" / "global"
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_oel_trade  ON order_execution_log(parent_trade_id);
CREATE INDEX IF NOT EXISTS idx_oel_date   ON order_execution_log(created_at);
CREATE INDEX IF NOT EXISTS idx_oel_symbol ON order_execution_log(symbol);

-- TABLE 32: trade_slippage_log — one row per completed trade (roll-up).
CREATE TABLE IF NOT EXISTS trade_slippage_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id            TEXT,
    trade_date          DATE NOT NULL,
    symbol              TEXT NOT NULL,
    strategy_name       TEXT NOT NULL,
    side                TEXT NOT NULL,     -- LONG / SHORT
    qty                 INTEGER,
    price_band          TEXT,              -- e.g. "200-300" (from slippage_bands)
    -- entry
    entry_signal_price  REAL,
    entry_fill_price    REAL,
    entry_slippage_rs   REAL,              -- adverse = positive
    entry_slippage_pct  REAL,
    -- stop loss
    sl_trigger_price    REAL,
    sl_fill_price       REAL,
    sl_slippage_rs      REAL,              -- adverse = positive
    sl_slippage_pct     REAL,
    -- target
    tgt_price           REAL,
    tgt_fill_price      REAL,
    tgt_slippage_rs     REAL,              -- favourable = positive (better fill)
    tgt_slippage_pct    REAL,
    -- R:R analysis
    planned_sl_distance REAL,              -- |entry - sl|
    planned_rr          REAL,              -- strategy's designed R:R
    actual_rr           REAL,              -- realised R:R after slippage
    rr_damage_pct       REAL,              -- THE KEY METRIC: % of risk budget eaten
    trade_result        TEXT,              -- WIN / LOSS / BREAKEVEN
    exit_reason         TEXT,              -- SL_HIT / TGT_HIT / EOD / MANUAL ...
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tsl_date     ON trade_slippage_log(trade_date);
CREATE INDEX IF NOT EXISTS idx_tsl_strategy ON trade_slippage_log(strategy_name);
CREATE INDEX IF NOT EXISTS idx_tsl_band     ON trade_slippage_log(price_band);
CREATE INDEX IF NOT EXISTS idx_tsl_symbol   ON trade_slippage_log(symbol);

-- TABLE 33: market_execution_context — Priority-2, ALL nullable, best-effort.
-- Bid/ask/spread at execution; absence NEVER blocks recording or execution.
CREATE TABLE IF NOT EXISTS market_execution_context (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id         TEXT,
    order_id         TEXT,
    symbol           TEXT NOT NULL,
    leg              TEXT,                 -- ENTRY / SL / TGT
    captured_at      TEXT,
    ltp              REAL,
    bid_price        REAL,
    ask_price        REAL,
    spread_rs        REAL,
    spread_pct       REAL,
    bid_qty          INTEGER,
    ask_qty          INTEGER,
    volume_traded    INTEGER,
    recent_range_pct REAL,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mec_trade  ON market_execution_context(trade_id);
CREATE INDEX IF NOT EXISTS idx_mec_symbol ON market_execution_context(symbol);

-- v32 -> v33 : Pre-flight check audit trail (3 NEW append-only tables). PURE
-- additions: no MIGRATION_TABLES entry (nothing rebuilt); executescript creates
-- them (CREATE IF NOT EXISTS) and the trailing INSERT bumps the version.

-- TABLE 34: preflight_runs — one row per pre-flight phase run (A/B/C/FINAL).
CREATE TABLE IF NOT EXISTS preflight_runs (
    run_id              TEXT PRIMARY KEY,
    run_date            TEXT NOT NULL,
    phase               TEXT NOT NULL,             -- A | B | C
    started_at          TEXT,
    completed_at        TEXT,
    total_checks        INTEGER NOT NULL DEFAULT 0,
    passed              INTEGER NOT NULL DEFAULT 0,
    failed_critical     INTEGER NOT NULL DEFAULT 0,
    warnings            INTEGER NOT NULL DEFAULT 0,
    autofixes_attempted INTEGER NOT NULL DEFAULT 0,
    autofixes_succeeded INTEGER NOT NULL DEFAULT 0,
    overall_status      TEXT,                      -- READY | READY_WITH_WARNINGS | CRITICAL_FAILURE
    alert_id            TEXT
);
CREATE INDEX IF NOT EXISTS idx_pfr_date ON preflight_runs(run_date);

-- TABLE 35: preflight_check_results — one row per check per run.
CREATE TABLE IF NOT EXISTS preflight_check_results (
    run_id          TEXT NOT NULL,
    run_date        TEXT,
    check_name      TEXT NOT NULL,
    check_group     TEXT,
    criticality     TEXT,                          -- CRITICAL | WARN | INFO
    status          TEXT,                          -- PASS | FAIL | WARN | AUTOFIXED | SKIPPED
    duration_ms     INTEGER,
    details_json    TEXT,
    fix_attempted   INTEGER NOT NULL DEFAULT 0,
    fix_result      TEXT                           -- SUCCESS | FAILED | (empty)
);
CREATE INDEX IF NOT EXISTS idx_pfcr_run  ON preflight_check_results(run_id);
CREATE INDEX IF NOT EXISTS idx_pfcr_name ON preflight_check_results(check_name, run_date);

-- TABLE 36: preflight_autofix_log — one row per auto-fix attempt (audit trail).
CREATE TABLE IF NOT EXISTS preflight_autofix_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT,
    check_name      TEXT,
    attempted_at    TEXT,
    fix_action      TEXT,
    before_state    TEXT,
    after_state     TEXT,
    result          TEXT,                          -- SUCCESS | FAILED
    error_msg       TEXT
);
CREATE INDEX IF NOT EXISTS idx_pfal_run ON preflight_autofix_log(run_id);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 37: gtt_state  (v36 / SLICE2.5-P2)
-- Durable source of truth for the ONE OCO-GTT (Good-Till-Triggered) that protects
-- a CNC (delivery) position overnight. Phase 1 (SLICE2.5-P1) held this in an
-- in-memory map (orders/cnc_gtt.py CncGttPlacer._trade_gtts) that did NOT survive
-- a restart; Phase 2 persists it here so the startup + 15-min reconcile can
-- re-verify each GTT against the broker, recreate a missing one, and finalise a
-- GTT-fired exit. The BROKER GTT is the authority; this table is the local mirror.
--
-- Y6 (active-row invariant): a trade may accumulate MULTIPLE rows over its life —
-- each recreate is a NEW broker gtt_id = a new row = history. The one-GTT-per-trade
-- invariant (M2) is therefore on status='ACTIVE' rows: at most ONE ACTIVE row per
-- open trade at any time (not one row ever).
--
-- status lifecycle: ACTIVE -> TRIGGERED (broker fired the OCO) -> CLEANED (exit
-- finalised / orphan swept); or ACTIVE -> CANCELLED|EXPIRED|REJECTED (broker-side
-- end states). needs_review (Y2) latches a qty-mismatch / ownership anomaly so its
-- CRITICAL alert fires ONCE per state rather than every reconcile cycle.
--
-- PURE ADDITION: created by CREATE TABLE IF NOT EXISTS on the schema re-apply; no
-- MIGRATION_TABLES entry, nothing rebuilt (same pattern as v31 slippage / v33
-- preflight tables). The trailing INSERT bumps schema_version to 36.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS gtt_state (
    gtt_id           INTEGER PRIMARY KEY,        -- broker trigger id
    trade_id         TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    exit_side        TEXT NOT NULL,              -- SELL (both OCO legs exit a long)
    qty              INTEGER NOT NULL,
    sl_trigger       REAL NOT NULL,
    sl_limit         REAL NOT NULL,
    tgt_trigger      REAL NOT NULL,
    tgt_limit        REAL NOT NULL,
    status           TEXT NOT NULL               -- ACTIVE|TRIGGERED|CANCELLED|EXPIRED|REJECTED|CLEANED
                     CHECK (status IN ('ACTIVE','TRIGGERED','CANCELLED',
                                       'EXPIRED','REJECTED','CLEANED')),
    needs_review     INTEGER NOT NULL DEFAULT 0, -- Y2 de-dup latch for anomaly alerts
    last_verified_at TEXT,                        -- ISO-8601 IST of last broker re-verify
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,

    FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
);

CREATE INDEX IF NOT EXISTS idx_gtt_state_trade_id
    ON gtt_state(trade_id);

CREATE INDEX IF NOT EXISTS idx_gtt_state_status
    ON gtt_state(status);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 38: sr_detector_results   (SNR-DETECTOR-V1, schema v37)
-- One row per PLACED candidate observed by the async, non-gating S&R detector.
-- SHADOW analytics only — detect + confluence + flags + retest PROPOSAL; nothing
-- here gates or modifies an order. actual_*/win_loss/pnl/hypothetical_retest_result
-- are backfilled by the EOD outcome step (scripts/sr_detector_backfill.py) and are
-- NULL at insert time.
--
-- PURE ADDITION: created by CREATE TABLE IF NOT EXISTS on the schema re-apply; no
-- MIGRATION_TABLES entry, nothing rebuilt (same pattern as v31 slippage / v33
-- preflight / v36 gtt_state). The trailing INSERT bumps schema_version to 37.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS sr_detector_results (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id                   TEXT NOT NULL,
    symbol                      TEXT NOT NULL,
    ts                          TEXT NOT NULL,          -- candidate ts (ISO-8601 IST)
    mode                        TEXT NOT NULL,          -- 'paper' | 'live'
    strategy                    TEXT,
    direction                   TEXT,
    score                       INTEGER,                -- nullable (gate-release path has none)
    intended_entry              REAL,
    actual_fill                 REAL,                   -- backfilled (EOD)
    nearest_resistance_zone     TEXT,                   -- JSON zone snapshot
    nearest_support_zone        TEXT,                   -- JSON zone snapshot
    dist_to_resistance_pct      REAL,
    dist_to_support_pct         REAL,
    resistance_confidence       TEXT,                   -- HIGH|MEDIUM|LOW|NONE
    support_confidence          TEXT,                   -- HIGH|MEDIUM|LOW|NONE
    confluence_evidence         TEXT,                   -- JSON: methods/TFs/touches per zone
    breakout_volume             REAL,
    flags                       TEXT,                   -- JSON array of flag strings
    would_wait_for_retest       INTEGER NOT NULL DEFAULT 0,
    proposed_retest_entry       REAL,
    proposed_retest_sl          REAL,
    structure_status            TEXT,                   -- OK|NO_CLEAR_STRUCTURE|FETCH_FAILED
    detector_version            TEXT NOT NULL,
    -- backfilled by the EOD outcome step (nullable now):
    actual_result               TEXT,
    win_loss                    TEXT,
    pnl                         REAL,
    hypothetical_retest_result  TEXT,
    created_at                  TEXT NOT NULL,

    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)   -- O1 (mirrors screener_results)
);

CREATE INDEX IF NOT EXISTS idx_sr_detector_results_signal_id
    ON sr_detector_results(signal_id);

CREATE INDEX IF NOT EXISTS idx_sr_detector_results_ts
    ON sr_detector_results(ts);

CREATE INDEX IF NOT EXISTS idx_sr_detector_results_mode
    ON sr_detector_results(mode);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 39: retest_state   (SNR-V2 Phase A, schema v38)
-- One row per candidate DIVERTED into the WAIT_FOR_RETEST monitor (a LONG entry
-- detected inside a HIGH resistance zone, BEFORE any capital reservation). The
-- restart-safe parking store (mirrors gate_state): RetestMonitor rehydrates from
-- it on boot and deletes the row atomically on release (confirm / reject / EOD).
-- NO capital is held while parked — reservation happens only at confirm-resume.
--
-- PURE ADDITION (same pattern as v36 gtt_state / v37 sr_detector_results); the
-- trailing INSERT bumps schema_version to 38.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS retest_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,           -- LONG (Phase A is long-only)
    zone_band_low   REAL NOT NULL,
    zone_band_high  REAL NOT NULL,
    entry_price     REAL NOT NULL,           -- original derived entry (pre-divert)
    sl_price        REAL NOT NULL,           -- original derived SL (pre-divert)
    strategy        TEXT NOT NULL,
    intent          TEXT NOT NULL,
    tier            TEXT,
    state           TEXT NOT NULL            -- WAIT_BREAKOUT | WAIT_RETEST | WAIT_CONFIRM
                    CHECK (state IN ('WAIT_BREAKOUT', 'WAIT_RETEST', 'WAIT_CONFIRM')),
    trigger_price   REAL,
    sizing_inputs   TEXT,                    -- JSON snapshot of inputs to re-size at confirm
    timeout_at      TEXT,                    -- ISO-8601 IST deadline
    added_at        TEXT NOT NULL,           -- ISO-8601 IST at divert (the retest clock start)
    created_at      TEXT NOT NULL,

    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
);

CREATE INDEX IF NOT EXISTS idx_retest_state_signal_id
    ON retest_state(signal_id);

CREATE INDEX IF NOT EXISTS idx_retest_state_symbol
    ON retest_state(symbol);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 40: excursion_reconstruction_runs   (MFE/MAE Option B, schema v39)
-- One row per scripts/reconstruct_excursions.py run (daily EOD or historical
-- backfill). Audit trail for the post-EOD MFE/MAE reconstruction: how many
-- closed trades were examined and how each resolved. Every examined trade lands
-- in EXACTLY one of three buckets:
--   trades_written                 — trade_excursions row written
--   trades_skipped_unreconstructable — PERMANENT non-writable (NULL/invalid
--                                    window, or 0-candle window despite candles
--                                    present). Logged + non-alarming.
--   trades_failed                  — transient/unexpected (compute/insert threw,
--                                    or candles unavailable e.g. no token).
--                                    Alarming + retryable.
-- HARD GUARD (asserted by the script): written + skipped + failed == examined
-- (a broken identity means a trade vanished silently → the run FAILS loudly).
--
-- PURE ADDITION (same pattern as v37 sr_detector_results / v38 retest_state); no
-- MIGRATION_TABLES entry, nothing rebuilt. The trailing INSERT bumps to v39.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS excursion_reconstruction_runs (
    run_id                          TEXT PRIMARY KEY,       -- uuid4 hex
    mode                            TEXT NOT NULL,          -- 'daily' | 'backfill'
    started_at                      TEXT NOT NULL,          -- ISO-8601 IST
    completed_at                    TEXT NOT NULL,          -- ISO-8601 IST
    trades_examined                 INTEGER NOT NULL,
    trades_written                  INTEGER NOT NULL,
    trades_skipped_unreconstructable INTEGER NOT NULL,
    trades_failed                   INTEGER NOT NULL,
    status                          TEXT NOT NULL,          -- OK | OK_WITH_NOTES | FAILED
    notes                           TEXT                    -- human-readable summary
);

CREATE INDEX IF NOT EXISTS idx_excursion_recon_runs_started
    ON excursion_reconstruction_runs(started_at);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLES 41-45: control_tower_* — VM Operations Control Tower (Phase 1a, v40)
-- An AGGREGATOR subsystem (ops/control_tower/) that READS existing monitors and
-- adds data-freshness + disk/backup checks. Phase 1a creates the FOUNDATION
-- tables only (the size-logger seeds control_tower_trends; the aggregator/report
-- that populate findings/runs/status/freshness arrive in 1b/1c).
--
-- PURE ADDITION (same pattern as v37 sr_detector_results / v39
-- excursion_reconstruction_runs): no MIGRATION_TABLES entry, nothing rebuilt;
-- CREATE TABLE IF NOT EXISTS on the schema re-apply. The trailing INSERT bumps
-- schema_version to 40.
-- ═════════════════════════════════════════════════════════════════════════════

-- TABLE 41: control_tower_findings — one row per distinct issue (deduped).
CREATE TABLE IF NOT EXISTS control_tower_findings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_time           TEXT NOT NULL,          -- ISO IST of the scan that (re)detected it
    category            TEXT NOT NULL,          -- security | cron | config | freshness | disk | backup
    severity            TEXT NOT NULL,          -- CRITICAL | HIGH | MEDIUM | LOW | INFO
    resource_type       TEXT,                   -- table | file | service | mount | job | dir
    resource_name       TEXT,                   -- e.g. 'candles', 'security-watcher', '/dev/sda1'
    location            TEXT,                   -- path / table / host detail
    reason              TEXT NOT NULL,          -- why this is a finding
    recommended_action  TEXT,
    status              TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN | ACKNOWLEDGED | RESOLVED
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL,
    acked_at            TEXT,                           -- 1c: set when an operator acks
    resolved_at         TEXT,                           -- 1c: set on auto-resolve (no longer detected)
    remarks             TEXT
);
-- DEDUP IDENTITY = (category, resource_name, reason): the aggregator UPSERTs
-- last_seen/scan_time/status on re-detection rather than inserting a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ct_findings_dedup
    ON control_tower_findings(category, resource_name, reason);
CREATE INDEX IF NOT EXISTS idx_ct_findings_status
    ON control_tower_findings(status, severity);
CREATE INDEX IF NOT EXISTS idx_ct_findings_last_seen   -- 1b query path (auto-resolve sweep)
    ON control_tower_findings(last_seen);

-- TABLE 42: control_tower_runs — one row per tower run (the 17:00 + Sun-18:00 jobs).
CREATE TABLE IF NOT EXISTS control_tower_runs (
    run_id          TEXT PRIMARY KEY,           -- uuid4 hex
    started_at      TEXT NOT NULL,              -- ISO IST
    completed_at    TEXT,                       -- ISO IST (NULL while running / on crash)
    duration_s      REAL,
    checks_run      INTEGER NOT NULL DEFAULT 0,
    findings_total  INTEGER NOT NULL DEFAULT 0,
    critical_count  INTEGER NOT NULL DEFAULT 0,
    high_count      INTEGER NOT NULL DEFAULT 0,
    medium_count    INTEGER NOT NULL DEFAULT 0,
    low_count       INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'RUNNING'  -- RUNNING | OK | OK_WITH_FINDINGS | FAILED
);
CREATE INDEX IF NOT EXISTS idx_ct_runs_started ON control_tower_runs(started_at);

-- TABLE 43: control_tower_trends — one row per DATE (PK). The Phase-1a size-logger
-- UPSERTs the four size fields; the aggregator (1b/1c) fills health_score + counts.
CREATE TABLE IF NOT EXISTS control_tower_trends (
    date            TEXT PRIMARY KEY,           -- YYYY-MM-DD IST
    health_score    INTEGER,                    -- 0-100 (aggregator, 1b/1c)
    critical_count  INTEGER,
    high_count      INTEGER,
    medium_count    INTEGER,
    disk_used_pct   REAL,                       -- FRESH (shutil.disk_usage), NOT system_metrics
    backup_size_mb  REAL,
    log_size_mb     REAL,
    db_size_mb      REAL
);

-- TABLE 44: control_tower_status — ChatGPT B: one current-status row per run_date.
CREATE TABLE IF NOT EXISTS control_tower_status (
    run_date            TEXT PRIMARY KEY,       -- YYYY-MM-DD IST (last run of the day wins)
    security_status     TEXT,                   -- OK | WARN | CRITICAL | UNKNOWN
    cron_status         TEXT,
    config_status       TEXT,
    freshness_status    TEXT,
    disk_pct            REAL,
    backup_size_mb      REAL,
    critical_count      INTEGER,
    high_count          INTEGER,
    overall_status      TEXT,                   -- ChatGPT D top-line (1c)
    last_successful_run TEXT                    -- ISO IST of the last OK run
);

-- TABLE 45: control_tower_freshness — ChatGPT C: per-stage data-pipeline freshness.
CREATE TABLE IF NOT EXISTS control_tower_freshness (
    run_date        TEXT NOT NULL,              -- YYYY-MM-DD IST
    stage           TEXT NOT NULL,              -- candles|signals|orders|executions|reconstruction|eod
    expected_by     TEXT,                       -- ISO IST deadline
    actual_at       TEXT,                       -- ISO IST when it happened (NULL = missing)
    delay_minutes   INTEGER,                    -- actual - expected (NULL if missing / NA)
    status          TEXT NOT NULL               -- OK | LATE | MISSING | NA
);
CREATE INDEX IF NOT EXISTS idx_ct_freshness_run ON control_tower_freshness(run_date, stage);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 46: config_snapshots   (W0 — daily-report redesign foundation, schema v41)
-- One row per distinct resolved-config seen on a date. The SYSTEM writes the FULL
-- effective config (core/config_snapshotter.snapshot_config, at startup after
-- config is resolved) so the daily report's Config sheet — and any historically-
-- correct report re-run — reads the config AS IT WAS on that date, DB-purely
-- (the report reads ONLY this table, never YAML).
--
-- Idempotent by (snapshot_date, config_hash): a same-config restart is a no-op; a
-- config change on a same-day restart writes a NEW row (timestamps distinguish
-- them). config_hash = sha256 of the stable/sorted config_json (also seeds a
-- future config-drift alert — NOT built here).
--
-- PURE ADDITION (same pattern as v37 sr_detector_results / v39
-- excursion_reconstruction_runs / v40 control_tower_*): no MIGRATION_TABLES entry,
-- nothing rebuilt; CREATE TABLE IF NOT EXISTS on the schema re-apply. The trailing
-- INSERT bumps schema_version to 41.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS config_snapshots (
    snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT NOT NULL,      -- YYYY-MM-DD IST (the run/trading date)
    snapshot_ts     TEXT NOT NULL,      -- ISO-8601 IST — when the row was written
    account_id      TEXT,               -- primary/selected account (e.g. 'LFL836')
    mode            TEXT,               -- 'PAPER' | 'LIVE'
    trade_type      TEXT,               -- 'INTRADAY' | 'DELIVERY' | 'BOTH'
    config_hash     TEXT NOT NULL,      -- sha256 of config_json (stable/sorted)
    config_json     TEXT NOT NULL       -- FULL resolved AppConfig (model_dump json)
);
CREATE INDEX IF NOT EXISTS idx_config_snapshots_date
    ON config_snapshots(snapshot_date);

-- ─────────────────────────────────────────────────────────────────────────────

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 47: eod_broker_reconciliation   (P1 — broker-authoritative EOD verdict, v42)
-- One row per date: the broker-authoritative EOD reconcile verdict that REPLACES
-- eod_verify's local-only false-VERIFY. Written by scripts/eod_broker_reconcile.py
-- @15:58 (standalone, own creds → runs even if the trading process was DOWN at EOD).
-- Per-dimension statuses + an overall verdict VERIFIED | ISSUES | UNVERIFIED. The
-- REQUIRED dims (positions, orders, broker day-realized P&L) unavailable ⇒ overall
-- UNVERIFIED (never a false VERIFIED). MARGIN is SUPPLEMENTAL, reliability-gated →
-- NOT_CHECKED post-15:45 (never blocks VERIFIED). LEDGER = the fm_ledger 3-balance
-- invariant (local, always available). Paper = SELF_CONSISTENCY (labeled), never
-- broker-authoritative. Shadow columns record the same-day eod_verify verdict +
-- mismatch for cutover readiness. PURE ADDITION (no rebuild). Trailing INSERT → v42.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS eod_broker_reconciliation (
    date               TEXT NOT NULL PRIMARY KEY,   -- YYYY-MM-DD IST
    mode               TEXT NOT NULL,               -- LIVE | PAPER
    self_consistency   INTEGER NOT NULL DEFAULT 0,  -- 1 in paper (no independent broker)
    authoritative      INTEGER NOT NULL DEFAULT 0,  -- 0=shadow, 1=authoritative
    broker_reachable   INTEGER NOT NULL DEFAULT 0,
    positions_status   TEXT,   -- VERIFIED | ISSUES | UNVERIFIED   (REQUIRED)
    orders_status      TEXT,   -- VERIFIED | ISSUES | UNVERIFIED   (REQUIRED)
    pnl_status         TEXT,   -- VERIFIED | ISSUES | UNVERIFIED   (REQUIRED; broker day-realized vs local)
    ledger_status      TEXT,   -- VERIFIED | ISSUES               (local invariant)
    margin_status      TEXT,   -- VERIFIED | ISSUES | NOT_CHECKED (SUPPLEMENTAL, reliability-gated)
    overall_status     TEXT NOT NULL,   -- VERIFIED | ISSUES | UNVERIFIED
    eod_verify_status  TEXT,   -- same-day eod_verify verdict (shadow comparison)
    mismatch           INTEGER,-- 1 if P1 disagrees with eod_verify (shadow readiness)
    detail             TEXT,   -- human-readable per-dimension notes (NEVER a secret)
    verified_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eod_broker_reconciliation_date
    ON eod_broker_reconciliation(date);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 49: pb01_watchlist   (V3 Step 10b — PB-01 overnight watchlist, schema v43)
-- The STATEFUL OVERNIGHT WATCHLIST for the PB-01 "Breakout + Retest" playbook. An
-- EOD Chartink breakout is captured (day D) with the LEVEL it cleared and a single
-- valid trading_date = D+1; the next-morning entry stage (09:20-11:00) loads today's
-- rows and evaluates the 5-min retest. ANALYSIS ONLY — never a position, never
-- capital; PB-01 is SHADOW (would-be records only), enabled:false (fail-closed).
--
-- Two load-bearing invariants (approved by Rama + Web Claude + ChatGPT):
--   * UNIQUE (symbol, trading_date) — DB-level dedupe: premature/repeated Chartink
--     firings CANNOT create duplicate or early rows (belt-and-suspenders with the
--     receiver dedup).
--   * trading_date — the ANTI-REHYDRATION key (FIX-046 class): on load, DISCARD any
--     row whose trading_date != today, so a stale candidate can NEVER fire on a
--     later day. Enforced in code + a test (G-NO-REHYDRATION).
--
-- PURE ADDITION (same pattern as v37 sr_detector_results / v38 retest_state / v39
-- excursion_reconstruction_runs); no MIGRATION_TABLES entry, nothing rebuilt. The
-- trailing INSERT bumps to v43.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS pb01_watchlist (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    trading_date  TEXT NOT NULL,           -- YYYY-MM-DD IST; the SINGLE valid session (D+1).
                                           -- ANTI-REHYDRATION key: load discards trading_date != today.
    level         REAL NOT NULL,           -- retest LEVEL = highest daily HIGH of the 20 sessions
                                           -- BEFORE the breakout day (WE compute it; never the payload).
    breakout_date TEXT NOT NULL,           -- YYYY-MM-DD IST; the session whose daily close cleared LEVEL.
    source        TEXT NOT NULL,           -- scanner name (pb01_breakout_retest).
    sr_zone_json  TEXT,                    -- nearest 30m/1h sr_detector zone to LEVEL (S&R validation programme).
    status        TEXT NOT NULL DEFAULT 'PENDING'
                  CHECK (status IN ('PENDING','CONSUMED','EXPIRED_WINDOW','INVALIDATED','SKIPPED_GAP')),
    outcome_json  TEXT,                    -- entry-stage outcome detail (confirmation candle / gap / expiry ts).
    captured_at   TEXT NOT NULL,           -- ISO-8601 IST at capture (EOD).
    created_at    TEXT NOT NULL,           -- ISO-8601 IST row-insert.
    consumed_at   TEXT,                    -- ISO-8601 IST when CONSUMED/EXPIRED/INVALIDATED/SKIPPED.
    UNIQUE (symbol, trading_date)
);

CREATE INDEX IF NOT EXISTS idx_pb01_watchlist_trading_date
    ON pb01_watchlist(trading_date);

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLE 50: daily_symbol_stats   (M-S4 — pre-market daily-stats cache, schema v44)
-- Per-(symbol, trading_date) daily statistics computed PRE-MARKET (~08:30 cron, AFTER
-- the token refresh) from daily candles that CLOSED BEFORE trading_date, via the shared
-- rate-limited OhlcFetcher. The screener's _build_market_data does an O(1) READ of this
-- table to wire the previously-dead scorer inputs (volume_surge / atr_filter / rsi_range)
-- WITHOUT any hot-path broker fetch. A cache MISS -> the screener keeps today's fail-safe
-- (0.0 / 0.5); this table NEVER blocks, fetches on the hot path, or fabricates a value.
-- All behind a default-OFF wiring flag until the M-S4 threshold re-fit ships (GATE 1).
-- PURE ADDITION (same pattern as v43 pb01_watchlist / v38 retest_state); no
-- MIGRATION_TABLES entry, nothing rebuilt. The trailing INSERT bumps to v44.
-- ═════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS daily_symbol_stats (
    symbol          TEXT NOT NULL,
    trading_date    TEXT NOT NULL,          -- YYYY-MM-DD IST; the session these stats are FOR
                                            -- (computed ONLY from candles closed BEFORE it — NO lookahead).
    prev_close      REAL,                   -- previous session's daily close.
    avg_volume_20d  REAL,                   -- SMA of daily volume over the 20 sessions before trading_date.
    atr14           REAL,                   -- Wilder ATR(14) on daily candles (the atr_filter ADR% input).
    rsi14           REAL,                   -- Wilder RSI(14) on daily closes (the rsi_range input).
    computed_at     TEXT NOT NULL,          -- ISO-8601 IST when the row was computed (staleness / audit).
    PRIMARY KEY (symbol, trading_date)      -- natural dedupe + O(1) point lookup by the screener.
);

CREATE INDEX IF NOT EXISTS idx_daily_symbol_stats_date
    ON daily_symbol_stats(trading_date);

-- ─────────────────────────────────────────────────────────────────────────────

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '44');  -- M-S4: +daily_symbol_stats (pre-market scorer-input cache). Pure addition — no rebuild.

-- ─────────────────────────────────────────────────────────────────────────────
-- END OF SCHEMA v24 (v1: tables 1-8; v2: +fm_ledger; v3: +kill_switch_state;
--                    v4: +webhook_audit, signals.trigger_price;
--                    v5: +eod_squareoff_log; v6: +reconciliation_log;
--                    v7: +screener_results; v8: +smart_tgt_state;
--                    v9: +innings;
--                    v10: -capital_ledger (dead); fm_ledger becomes write-ahead
--                          + entry_type CHECK + session_id/direction/trade_id/
--                          margin_delta/pnl_delta/costs columns;
--                    v11: +eod_squareoff_log.status/completed_at (M-3 write-
--                          ahead); +trades.reservation_id (EF-5 capital flow);
--                    v12: +gate_state (Audit 4.4 — entry-gate rehydration);
--                    v13: -session.kill_state/kill_reason/kill_time/kill_type
--                          (CFG-7); -session.yesterday_pnl/wins/losses
--                          /consecutive_losses (NSK-1) — 2026-04-26 audit;
--                    v14: +trades cost breakdown (6 cols) + mode + sl_trail_count;
--                          +orders.rejection_reason/filled_at;
--                          +signals.webhook_payload;
--                          +screener_results.eligible_score;
--                          +candles table; +trade_excursions table;
--                    v15: +pnl_reconciliation table (FIX-128 Fix B);
--                    v16: +orders.reconciliation_status (FIX-129 Item 26);
--                    v17: +trades.signal_to_order_ms/order_to_fill_ms/total_latency_ms (FIX-130 Item 5);
--                    v18: +telegram_alerts table (FIX-131 Item 18);
--                    v19: +trade_journal table (FIX-133 Item 30);
--                    v20: +position_reconciliation, +strategy_metrics (FIX-134 Items 31+36);
--                    v21: +shadow_trades (FIX-135 Item 41);
--                    v22: +eod_verification (FIX-137 Item 59);
--                    v23: +cron_heartbeat (FIX-145);
--                    v24: +system_metrics, +system_metrics_daily (FIX-150);
--                    v25: O2 status/enum CHECK constraints on signals.status,
--                          trades.status, orders.status, orders.leg,
--                          kill_switch_state.state (FIX-172). Applied to existing
--                          DBs via core/migrations.py table rebuild;
--                    v26: O1 uniform FK declarations — screener_results.signal_id,
--                          smart_tgt_state.trade_id, innings.trade_id,
--                          reconciliation_log.trade_id, shadow_trades.signal_id +
--                          live_trade_id (FIX-173). Same table-rebuild migration;
--                    v27: O4 stored `date` generated column + index on fm_ledger,
--                          candles, system_metrics, webhook_audit (FIX-174) so
--                          daily date-range queries use an index instead of
--                          wrapping the ts column in DATE().)
-- ─────────────────────────────────────────────────────────────────────────────
