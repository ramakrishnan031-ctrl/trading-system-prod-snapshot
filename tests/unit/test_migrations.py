"""
tests/unit/test_migrations.py — schema migration runner + O2 CHECK constraints.

Covers:
  * Fresh build lands at EXPECTED_SCHEMA_VERSION with constraints active.
  * v24 -> v25 migration preserves existing data and adds the CHECK.
  * Migration is idempotent (re-open is a no-op, version stable).
  * Migration is fail-safe: legacy data that violates a new constraint aborts
    the migration and leaves the version untouched (no silent data loss).
  * Convergence: a freshly built table and a migrated table have identical DDL.
  * The O2 CHECK constraints accept every real status the code emits (incl.
    leg=CO, orders.status TRIGGER_PENDING, the four signal prefix families) and
    reject typos / wrong-case / empty.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.state_store import StateStore, EXPECTED_SCHEMA_VERSION
from core import migrations


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — build a minimal pre-O2 ("v24") database by hand
# ─────────────────────────────────────────────────────────────────────────────

_V24_SIGNALS = """
CREATE TABLE signals (
    signal_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, scanner TEXT NOT NULL,
    strategy TEXT NOT NULL, triggered_at TEXT NOT NULL, received_at TEXT NOT NULL,
    expires_at TEXT NOT NULL, status TEXT NOT NULL, rejection_reason TEXT,
    trade_id TEXT, trigger_price REAL, fingerprint TEXT NOT NULL,
    fingerprint_date TEXT NOT NULL, webhook_payload TEXT
);
"""

_V24_KILL = """
CREATE TABLE kill_switch_state (
    id INTEGER PRIMARY KEY CHECK (id = 1), state TEXT NOT NULL, reason TEXT NOT NULL,
    triggered_at TEXT NOT NULL, triggered_by TEXT NOT NULL
);
"""


def _build_v24(path: Path, signal_status: str = "REJECTED_SCORE_42") -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        + _V24_SIGNALS
        + _V24_KILL
    )
    conn.execute("INSERT INTO schema_meta VALUES ('schema_version','24')")
    conn.execute("INSERT INTO kill_switch_state VALUES (1,'INACTIVE','boot','t','sys')")
    conn.execute(
        "INSERT INTO signals(signal_id,symbol,scanner,strategy,triggered_at,"
        "received_at,expires_at,status,fingerprint,fingerprint_date) "
        "VALUES('L1','Y','sc','st','t','t','t',?,'fp1','2026-06-14')",
        (signal_status,),
    )
    conn.commit()
    conn.close()


def _signal_insert(store: StateStore, sid: str, status: str) -> None:
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals(signal_id,symbol,scanner,strategy,triggered_at,"
            "received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES(?,?,'sc','st','t','t','t',?,?,?)",
            (sid, "X", status, sid, sid[:8]),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fresh build
# ─────────────────────────────────────────────────────────────────────────────

def test_fresh_build_is_latest_version(tmp_path):
    store = StateStore(tmp_path / "fresh.db")
    assert store.get_schema_version() == EXPECTED_SCHEMA_VERSION
    store.close()


def test_fresh_build_kill_switch_check_active(tmp_path):
    store = StateStore(tmp_path / "fresh.db")
    with store.transaction() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO kill_switch_state VALUES(1,'SOFT_KILL','r','t','x')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO kill_switch_state VALUES(1,'BOGUS','r','t','x')"
            )
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# Migration: v24 -> v25
# ─────────────────────────────────────────────────────────────────────────────

def test_migration_preserves_data_and_adds_check(tmp_path):
    db = tmp_path / "legacy.db"
    _build_v24(db, signal_status="REJECTED_SCORE_42")

    store = StateStore(db)  # triggers migration
    assert store.get_schema_version() == EXPECTED_SCHEMA_VERSION

    rows = store.fetch_all("SELECT signal_id, status FROM signals")
    assert [(r["signal_id"], r["status"]) for r in rows] == [("L1", "REJECTED_SCORE_42")]

    # CHECK now active — a bogus signal status is rejected
    with pytest.raises(sqlite3.IntegrityError):
        _signal_insert(store, "bad1", "totally_bogus")
    store.close()


def test_migration_idempotent(tmp_path):
    db = tmp_path / "legacy.db"
    _build_v24(db)
    StateStore(db).close()
    # Re-open: already migrated, must be a no-op and stay at latest version.
    store = StateStore(db)
    assert store.get_schema_version() == EXPECTED_SCHEMA_VERSION
    assert store.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    store.close()


def test_migration_failsafe_on_violating_legacy_data(tmp_path):
    db = tmp_path / "dirty.db"
    # 'weird_legacy' violates the new signals CHECK
    _build_v24(db, signal_status="weird_legacy")

    with pytest.raises(migrations.MigrationError):
        StateStore(db)

    # Rolled back: version stays at 24, original row intact, no CHECK applied.
    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()[0] == "24"
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1
    assert "CHECK" not in (
        conn.execute("SELECT sql FROM sqlite_master WHERE name='signals'").fetchone()[0]
    )
    conn.close()


def test_o1_fk_added_to_existing_table(tmp_path):
    """A pre-O1 screener_results (no FK) is rebuilt with the FK on migration."""
    db = tmp_path / "v25.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        + _V24_SIGNALS
        + _V24_KILL
        + """
        CREATE TABLE screener_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id TEXT NOT NULL,
            score INTEGER NOT NULL, tier TEXT NOT NULL, status TEXT NOT NULL,
            step_results TEXT NOT NULL, latencies TEXT NOT NULL,
            market_data_snapshot TEXT NOT NULL, ts TEXT NOT NULL,
            eligible_score INTEGER
        );
        """
    )
    # Pretend this DB already has the v25 CHECK constraints so only O1 runs.
    conn.execute("INSERT INTO schema_meta VALUES ('schema_version','25')")
    conn.execute(
        "INSERT INTO signals(signal_id,symbol,scanner,strategy,triggered_at,"
        "received_at,expires_at,status,fingerprint,fingerprint_date) "
        "VALUES('s1','Y','sc','st','t','t','t','TRADED','fp','2026-06-14')"
    )
    conn.execute(
        "INSERT INTO screener_results(signal_id,score,tier,status,step_results,"
        "latencies,market_data_snapshot,ts) VALUES('s1',1,'A','PASSED','{}','{}','{}','t')"
    )
    conn.commit()
    conn.close()

    store = StateStore(db)  # migrate v25 -> latest
    assert store.get_schema_version() == EXPECTED_SCHEMA_VERSION
    # data preserved
    assert store.fetch_one("SELECT COUNT(*) AS n FROM screener_results")["n"] == 1
    # FK now enforced
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as cur:
            cur.execute(
                "INSERT INTO screener_results(signal_id,score,tier,status,step_results,"
                "latencies,market_data_snapshot,ts) "
                "VALUES('nope',1,'A','PASSED','{}','{}','{}','t')"
            )
    store.close()


def test_o4_date_column_added_and_indexed(tmp_path):
    """A pre-O4 fm_ledger (no date col) gains the stored date column + index."""
    db = tmp_path / "v26.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        + _V24_SIGNALS
        + _V24_KILL
        + """
        CREATE TABLE fm_ledger (
            ledger_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            entry_type TEXT NOT NULL CHECK (entry_type IN
              ('INIT','RESERVE','RELEASE','COMMIT','RELEASE_USED','SYNC','RESET_PNL','TOP_UP')),
            amount REAL NOT NULL, bucket TEXT NOT NULL, balance_before REAL NOT NULL,
            balance_after REAL NOT NULL, signal_id TEXT, reservation_id TEXT, reason TEXT,
            session_id TEXT, direction TEXT, trade_id TEXT,
            margin_delta REAL NOT NULL DEFAULT 0.0, pnl_delta REAL NOT NULL DEFAULT 0.0,
            costs REAL NOT NULL DEFAULT 0.0
        );
        """
    )
    conn.execute("INSERT INTO schema_meta VALUES ('schema_version','26')")
    conn.execute(
        "INSERT INTO fm_ledger(ts,entry_type,amount,bucket,balance_before,balance_after,"
        "pnl_delta,costs) VALUES('2026-06-13T10:00:00+05:30','RELEASE_USED',0,'intraday',"
        "1,1,500.0,50.0)"
    )
    conn.commit()
    conn.close()

    store = StateStore(db)  # migrate v26 -> latest
    assert store.get_schema_version() == EXPECTED_SCHEMA_VERSION
    # generated column computed from existing data
    assert store.fetch_one("SELECT date FROM fm_ledger")["date"] == "2026-06-13"
    # the daily-loss query returns the preserved value
    assert abs(store.get_daily_realized_net_pnl("2026-06-13") - 450.0) < 0.001
    # the new query uses the date index
    plan = store.fetch_all(
        "EXPLAIN QUERY PLAN SELECT * FROM fm_ledger WHERE date = '2026-06-13'"
    )
    assert any("idx_fm_ledger_date" in r[3] for r in plan), plan
    store.close()


def test_o6_analytics_tables_relocated(tmp_path):
    """A v27 DB with candles/system_metrics in MAIN has them moved to analytics.db."""
    from core import db_connect

    db = tmp_path / "v27.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        + """
        CREATE TABLE candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
            instrument_token INTEGER NOT NULL, ts TEXT NOT NULL,
            interval_sec INTEGER NOT NULL DEFAULT 60, open REAL NOT NULL,
            high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
            volume INTEGER NOT NULL DEFAULT 0, is_synthetic INTEGER NOT NULL DEFAULT 0,
            date TEXT GENERATED ALWAYS AS (substr(ts,1,10)) STORED,
            UNIQUE(instrument_token, ts, interval_sec)
        );
        CREATE TABLE system_metrics (
            timestamp TEXT NOT NULL, cpu_pct REAL, memory_mb REAL, db_size_mb REAL,
            log_size_mb REAL, open_fds INTEGER, thread_count INTEGER, disk_used_pct REAL,
            date TEXT GENERATED ALWAYS AS (substr(timestamp,1,10)) STORED
        );
        CREATE TABLE system_metrics_daily (
            date TEXT NOT NULL PRIMARY KEY, snapshot_count INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute("INSERT INTO schema_meta VALUES ('schema_version','27')")
    conn.execute(
        "INSERT INTO candles(symbol,instrument_token,ts,interval_sec,open,high,low,"
        "close,volume) VALUES('ABC',101,'2026-06-13T09:30:00+05:30',60,10,11,9,10.5,500)"
    )
    conn.execute(
        "INSERT INTO system_metrics(timestamp,cpu_pct,memory_mb) "
        "VALUES('2026-06-13T09:35:00+05:30',12.5,256.0)"
    )
    conn.execute("INSERT INTO system_metrics_daily(date,snapshot_count) VALUES('2026-06-13',75)")
    conn.commit()
    conn.close()

    store = StateStore(db)  # migrate v27 -> v28 (relocation)
    assert store.get_schema_version() == EXPECTED_SCHEMA_VERSION

    # The analytics file now exists and the main DB no longer holds these tables.
    apath = db_connect.analytics_path_for(db)
    assert apath.exists()
    main_tables = {r["name"] for r in store.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in db_connect.ANALYTICS_TABLES:
        assert t not in main_tables, f"{t} should have been relocated out of main"

    # Rows are intact and queryable via the unqualified (ATTACH-resolved) name,
    # and the relocated candle's STORED date column recomputed in analytics.db.
    crow = store.fetch_one("SELECT symbol, instrument_token, date FROM candles")
    assert (crow["symbol"], crow["instrument_token"], crow["date"]) == ("ABC", 101, "2026-06-13")
    assert store.fetch_one("SELECT cpu_pct FROM system_metrics")["cpu_pct"] == 12.5
    assert store.fetch_one("SELECT snapshot_count FROM system_metrics_daily")["snapshot_count"] == 75

    # Idempotent: re-open does not error and stays at v28 with rows intact.
    store.close()
    store2 = StateStore(db)
    assert store2.get_schema_version() == EXPECTED_SCHEMA_VERSION
    assert store2.fetch_one("SELECT COUNT(*) AS n FROM candles")["n"] == 1
    store2.close()


def test_o6_fresh_build_has_analytics_in_separate_file(tmp_path):
    """Fresh build: analytics tables live in analytics.db, not the trading DB."""
    from core import db_connect

    db = tmp_path / "fresh.db"
    store = StateStore(db)
    main_tables = {r["name"] for r in store.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "candles" not in main_tables
    assert "system_metrics" not in main_tables
    # but writable/readable transparently via ATTACH
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO candles(symbol,instrument_token,ts,interval_sec,open,high,"
            "low,close,volume) VALUES('Z',9,'2026-06-14T09:30:00+05:30',60,1,1,1,1,1)"
        )
    assert store.fetch_one("SELECT COUNT(*) AS n FROM candles")["n"] == 1
    assert db_connect.analytics_path_for(db).exists()
    store.close()


def test_fresh_and_migrated_table_ddl_converge(tmp_path):
    """A table built fresh from schema.sql and one migrated from v24 must match."""
    fresh = StateStore(tmp_path / "fresh.db")
    fresh_sql = {
        t: fresh.fetch_one(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
        )["sql"]
        for t in ("signals", "kill_switch_state")
    }
    fresh.close()

    db = tmp_path / "legacy.db"
    _build_v24(db)
    migrated = StateStore(db)
    for t, fsql in fresh_sql.items():
        msql = migrated.fetch_one(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
        )["sql"]
        assert migrations._normalize_ddl(msql) == migrations._normalize_ddl(fsql), t
    migrated.close()


# ─────────────────────────────────────────────────────────────────────────────
# O2 CHECK behaviour — accept real values, reject fiction
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "o2.db")
    yield s
    s.close()


@pytest.mark.parametrize("status", [
    "QUEUED", "IN_PROCESS", "PROCESSING", "PASSED", "PROCESSED",
    "PROCESSED_NO_PLACER", "PLACEMENT_FAILED", "RESERVED", "TRADED",
    "REJECTED", "REJECTED_SCORE_45", "REJECTED_NO_ATR_DATA",
    "DROPPED_BACKPRESSURE", "SKIPPED_QUOTE_UNAVAILABLE",
    "GATE_WAITING", "GATE_RELEASED_PRICE_HIT", "TIMEOUT", "EXPIRED",
])
def test_signals_status_accepts_real_values(store, status):
    _signal_insert(store, "ok_" + status, status)


@pytest.mark.parametrize("bad", ["queued", "", "OPEN", "FILLED", "rejected_x"])
def test_signals_status_rejects_fiction(store, bad):
    with pytest.raises(sqlite3.IntegrityError):
        _signal_insert(store, "bad_" + (bad or "empty"), bad)


def test_orders_leg_accepts_co_and_rejects_fiction(store):
    # Need a parent signal + trade for the orders FK.
    _signal_insert(store, "sig1", "TRADED")
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO trades(trade_id,signal_id,symbol,direction,strategy,"
            "qty_planned,entry_target_price,sl_initial,tgt_initial,margin_reserved,"
            "risk_amount,created_at,updated_at,status,order_protocol) "
            "VALUES('t1','sig1','X','LONG','st',1,100,95,110,20,5,'t','t',"
            "'PENDING_FILL','CO_PLUS_TGT')"
        )

    def ins_order(oid, leg, status="PENDING"):
        with store.transaction() as cur:
            cur.execute(
                "INSERT INTO orders(order_id,trade_id,leg,transaction_type,order_type,"
                "product,variety,qty_requested,status,placed_at,updated_at) "
                "VALUES(?, 't1', ?, 'BUY','LIMIT','MIS','co',1,?, 't','t')",
                (oid, leg, status),
            )

    for leg in ("ENTRY", "SL", "TGT", "EOD", "CO"):
        ins_order("o_" + leg, leg)
    # resting SL stored as TRIGGER_PENDING must be allowed (naked-position guard)
    ins_order("o_tp", "SL", "TRIGGER_PENDING")
    with pytest.raises(sqlite3.IntegrityError):
        ins_order("o_bad", "BOGUS_LEG")
    with pytest.raises(sqlite3.IntegrityError):
        ins_order("o_badstatus", "SL", "FILLED")
