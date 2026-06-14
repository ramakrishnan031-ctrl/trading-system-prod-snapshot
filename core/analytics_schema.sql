-- ═════════════════════════════════════════════════════════════════════════════
-- analytics_schema.sql -- Trading System v2  (O6: analytics DB split)
--
-- These high-volume, non-critical tables live in analytics.db, a SEPARATE
-- SQLite file from trading_system.db (see core/db_connect.py). Keeping them out
-- of the trading DB keeps that file small: the nightly .backup is near-instant
-- and WAL checkpoints on the hot trading DB stay short.
--
-- Applied to analytics.db directly via db_connect.init_analytics_schema(). All
-- DDL is unqualified and IF NOT EXISTS (idempotent). Working connections ATTACH
-- this file as schema `analytics`; since these tables exist ONLY here, SQLite
-- resolves unqualified `FROM candles` / `FROM system_metrics` to them.
--
-- NOTE: none of these tables declare a FOREIGN KEY into the trading DB (SQLite
-- does not support cross-database FKs), and nothing in trading_system.db
-- references them — that independence is what makes the split safe. Do not add
-- a table here that needs an FK to trades/signals/etc.
-- ═════════════════════════════════════════════════════════════════════════════

-- ─────────────────────────────────────────────────────────────────────────────
-- candles  (relocated from schema.sql in O6; was TABLE 18)
-- 1-min OHLCV history for traded symbols. Thousands of rows/day.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS candles (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol           TEXT NOT NULL,
    instrument_token INTEGER NOT NULL,
    ts               TEXT NOT NULL,                   -- ISO-8601 IST candle close
    interval_sec     INTEGER NOT NULL DEFAULT 60,     -- 60 for 1-min
    open             REAL NOT NULL,
    high             REAL NOT NULL,
    low              REAL NOT NULL,
    close            REAL NOT NULL,
    volume           INTEGER NOT NULL DEFAULT 0,
    is_synthetic     INTEGER NOT NULL DEFAULT 0,
    -- O4 (v27): stored YYYY-MM-DD (== DATE(ts)) for indexed date queries.
    date             TEXT GENERATED ALWAYS AS (substr(ts, 1, 10)) STORED,
    UNIQUE(instrument_token, ts, interval_sec)
);

CREATE INDEX IF NOT EXISTS idx_candles_symbol_ts
    ON candles(symbol, ts);

CREATE INDEX IF NOT EXISTS idx_candles_date
    ON candles(date);

-- ─────────────────────────────────────────────────────────────────────────────
-- system_metrics  (relocated from schema.sql in O6; was TABLE 29 / FIX-150)
-- 5-min resource snapshots during market hours (~75 rows/day).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS system_metrics (
    timestamp       TEXT NOT NULL,
    cpu_pct         REAL,
    memory_mb       REAL,
    db_size_mb      REAL,
    log_size_mb     REAL,
    open_fds        INTEGER,
    thread_count    INTEGER,
    disk_used_pct   REAL,
    -- O4 (v27): stored YYYY-MM-DD (== DATE(timestamp)) for indexed date queries.
    date            TEXT GENERATED ALWAYS AS (substr(timestamp, 1, 10)) STORED
);

CREATE INDEX IF NOT EXISTS idx_system_metrics_ts
    ON system_metrics(timestamp);

CREATE INDEX IF NOT EXISTS idx_system_metrics_date
    ON system_metrics(date);

-- ─────────────────────────────────────────────────────────────────────────────
-- system_metrics_daily  (relocated from schema.sql in O6; was TABLE 30 / FIX-150)
-- Daily summary of system metrics: avg/max/p95 per metric.
-- Computed by capture_metrics_baseline.py --summarize at EOD.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS system_metrics_daily (
    date                TEXT NOT NULL PRIMARY KEY,   -- YYYY-MM-DD
    snapshot_count      INTEGER NOT NULL DEFAULT 0,
    cpu_avg             REAL,
    cpu_max             REAL,
    cpu_p95             REAL,
    memory_avg_mb       REAL,
    memory_max_mb       REAL,
    memory_p95_mb       REAL,
    db_size_mb          REAL,
    log_size_mb         REAL,
    thread_avg          REAL,
    thread_max          INTEGER,
    disk_used_avg_pct   REAL,
    disk_used_max_pct   REAL
);
