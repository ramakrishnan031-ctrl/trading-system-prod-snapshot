"""
Shared pytest fixtures: synthetic v41 + v42 fixture DBs built from minimal-but-
faithful DDL (column names/types match core/schema.sql for every column the
readers touch) and seeded to cover Rama's V4 funnel example plus capacity,
strategy, consecutive-loss and event data.

Isolation: this builds standalone SQLite files in a tmp dir + a tmp config dir;
nothing from the production tree is imported or written.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest
import yaml

# Make `backend` importable (ops_dashboard/ on sys.path).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.services import freshness  # noqa: E402
from backend import app as app_module    # noqa: E402

TODAY = freshness.ist_today_iso()


# Faithful minimal DDL (only columns the readers use; FK omitted so seed order
# is free and read-only connections don't enforce integrity).
DDL = [
    "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE session (id INTEGER PRIMARY KEY CHECK(id=1), session_date TEXT,
        account_id TEXT, broker TEXT, mode TEXT, trade_type TEXT,
        last_config_hash TEXT, session_start TEXT, last_updated TEXT)""",
    """CREATE TABLE kill_switch_state (id INTEGER PRIMARY KEY CHECK(id=1),
        state TEXT NOT NULL, reason TEXT, triggered_at TEXT, triggered_by TEXT)""",
    """CREATE TABLE webhook_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
        scanner_name TEXT, source_ip TEXT, payload_size_bytes INTEGER, response_code INTEGER,
        signals_accepted INTEGER DEFAULT 0, signals_rejected INTEGER DEFAULT 0, duration_ms INTEGER,
        date TEXT GENERATED ALWAYS AS (substr(ts,1,10)) STORED)""",
    """CREATE TABLE signals (signal_id TEXT PRIMARY KEY, symbol TEXT, scanner TEXT, strategy TEXT,
        triggered_at TEXT, received_at TEXT, expires_at TEXT, status TEXT NOT NULL,
        rejection_reason TEXT, trade_id TEXT, trigger_price REAL, webhook_payload TEXT)""",
    """CREATE TABLE orders (order_id TEXT PRIMARY KEY, trade_id TEXT, leg TEXT, transaction_type TEXT,
        order_type TEXT, product TEXT, variety TEXT, qty_requested INTEGER, status TEXT,
        qty_filled INTEGER DEFAULT 0, placed_at TEXT, filled_at TEXT, updated_at TEXT)""",
    """CREATE TABLE trades (trade_id TEXT PRIMARY KEY, signal_id TEXT, symbol TEXT, direction TEXT,
        strategy TEXT, sector TEXT, qty_planned INTEGER, qty_filled INTEGER,
        margin_reserved REAL, risk_amount REAL, created_at TEXT, entry_time TEXT, exit_time TEXT,
        exit_reason TEXT, gross_pnl REAL, net_pnl REAL, status TEXT, actual_position_value_rs REAL)""",
    """CREATE TABLE fm_ledger (ledger_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
        entry_type TEXT NOT NULL, amount REAL, bucket TEXT, balance_before REAL, balance_after REAL,
        signal_id TEXT, reservation_id TEXT, reason TEXT, session_id TEXT, direction TEXT,
        trade_id TEXT, margin_delta REAL DEFAULT 0, pnl_delta REAL DEFAULT 0, costs REAL DEFAULT 0,
        date TEXT GENERATED ALWAYS AS (substr(ts,1,10)) STORED)""",
    """CREATE TABLE capital_snapshot (id INTEGER PRIMARY KEY CHECK(id=1), cash_floor REAL,
        realized_pnl_today REAL, margin_used REAL, margin_reserved REAL, charges_today REAL,
        last_broker_sync TEXT, sync_source TEXT, updated_at TEXT)""",
    """CREATE TABLE strategy_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT, date TEXT,
        sharpe REAL, win_rate REAL, avg_pnl REAL, total_trades INTEGER, computed_at TEXT,
        UNIQUE(strategy, date))""",
    """CREATE TABLE config_snapshots (snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_date TEXT, snapshot_ts TEXT, account_id TEXT, mode TEXT, trade_type TEXT,
        config_hash TEXT, config_json TEXT)""",
    """CREATE TABLE system_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
        event_type TEXT, scenario TEXT, details TEXT)""",
]

DDL_V42_EXTRA = [
    """CREATE TABLE eod_broker_reconciliation (recon_id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, overall_status TEXT, self_consistency INTEGER DEFAULT 0,
        authoritative INTEGER DEFAULT 0, mismatch INTEGER)""",
]

# System config embedded in the config_snapshot (limits capacity reads).
_SNAPSHOT_SYSTEM = {
    "force_intraday_only": True,
    "trade_type": "INTRADAY",
    "risk": {
        "max_daily_trades": 10, "max_open_positions": 5,
        "max_open_delivery_positions": 3, "max_daily_delivery_trades": 5,
        "max_consecutive_losses": 5, "daily_loss_limit_pct": 0.03,
    },
    "capital": {"intraday_bucket_pct": 0.70, "positional_bucket_pct": 0.30},
    "signal_queue": {"capacity": 300, "backpressure_pct": 0.80},
}


def _ts(hhmmss: str) -> str:
    return f"{TODAY}T{hhmmss}+05:30"


def _seed(conn: sqlite3.Connection, schema_version: int) -> None:
    c = conn.cursor()
    c.execute("INSERT INTO schema_meta(key,value) VALUES('schema_version',?)", (str(schema_version),))
    c.execute(
        "INSERT INTO session(id,session_date,account_id,broker,mode,trade_type,session_start,last_updated) "
        "VALUES(1,?,?,?,?,?,?,?)",
        (TODAY, "LFL836", "zerodha", "PAPER", "INTRADAY", _ts("08:15:00"), _ts("15:20:00")),
    )
    c.execute(
        "INSERT INTO kill_switch_state(id,state,reason,triggered_at,triggered_by) "
        "VALUES(1,'INACTIVE','',?, 'system')",
        (_ts("08:15:00"),),
    )

    # ── Webhook funnel: received=100, validated=85, rejected_total=15 ──
    c.execute(
        "INSERT INTO webhook_audit(ts,scanner_name,source_ip,payload_size_bytes,response_code,"
        "signals_accepted,signals_rejected,duration_ms) VALUES(?,?,?,?,?,?,?,?)",
        (_ts("10:31:00"), "gap_fade_long", "1.2.3.4", 900, 200, 85, 15, 12),
    )

    # ── Signals: 10 DUPLICATE + 3 risk-reject + 2 capital-reject + per-strategy QUEUED ──
    sid = 0
    for _ in range(10):
        sid += 1
        c.execute("INSERT INTO signals(signal_id,symbol,scanner,strategy,received_at,status) "
                  "VALUES(?,?,?,?,?,?)",
                  (f"sig_dup_{sid}", "AAA", "gap_fade_long", "gap_fade_long", _ts("10:32:00"), "DUPLICATE"))
    for i in range(3):
        sid += 1
        c.execute("INSERT INTO signals(signal_id,symbol,scanner,strategy,received_at,status) "
                  "VALUES(?,?,?,?,?,?)",
                  (f"sig_risk_{i}", "BBB", "gap_fade_long", "gap_fade_long", _ts("10:33:00"),
                   "REJECTED_DAILY_LOSS"))
    for i in range(2):
        sid += 1
        c.execute("INSERT INTO signals(signal_id,symbol,scanner,strategy,received_at,status) "
                  "VALUES(?,?,?,?,?,?)",
                  (f"sig_cap_{i}", "CCC", "vwap_bounce_long", "vwap_bounce_long", _ts("10:34:00"),
                   "REJECTED_CAPITAL"))
    for i in range(8):
        sid += 1
        c.execute("INSERT INTO signals(signal_id,symbol,scanner,strategy,received_at,status) "
                  "VALUES(?,?,?,?,?,?)",
                  (f"sig_q_{i}", "DDD", "gap_fade_long", "gap_fade_long", _ts("10:35:00"), "QUEUED"))

    # ── Orders: 70 ENTRY placed (60 COMPLETE, 10 CANCELLED) + 8 SL + 12 TGT fills ──
    for i in range(70):
        st = "COMPLETE" if i < 60 else "CANCELLED"
        filled = _ts("10:40:00") if st == "COMPLETE" else None
        c.execute(
            "INSERT INTO orders(order_id,trade_id,leg,transaction_type,order_type,product,variety,"
            "qty_requested,status,placed_at,filled_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"ord_e_{i}", f"trd_e_{i}", "ENTRY", "BUY", "LIMIT", "MIS", "regular", 10, st,
             _ts("10:39:00"), filled, _ts("10:40:00")),
        )
    for i in range(8):
        c.execute(
            "INSERT INTO orders(order_id,trade_id,leg,transaction_type,order_type,product,variety,"
            "qty_requested,status,placed_at,filled_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"ord_sl_{i}", f"trd_e_{i}", "SL", "SELL", "SL", "MIS", "regular", 10, "COMPLETE",
             _ts("11:00:00"), _ts("13:00:00"), _ts("13:00:00")),
        )
    for i in range(12):
        c.execute(
            "INSERT INTO orders(order_id,trade_id,leg,transaction_type,order_type,product,variety,"
            "qty_requested,status,placed_at,filled_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"ord_tgt_{i}", f"trd_e_{i}", "TGT", "SELL", "LIMIT", "MIS", "regular", 10, "COMPLETE",
             _ts("11:00:00"), _ts("13:30:00"), _ts("13:30:00")),
        )

    # ── Trades: 8 created today (4 CLOSED today + 4 OPEN) ──
    # Closed: consecutive-loss streak=3 (latest 3 losses, then a win by exit_time).
    closed = [
        # trade_id, strategy, exit_time, exit_reason, net_pnl
        ("trd_c1", "gap_fade_long",     "15:10:00", "SL_HIT", -100.0),
        ("trd_c2", "vwap_bounce_long",  "15:05:00", "EOD",     -50.0),
        ("trd_c3", "vwap_bounce_long",  "15:00:00", "MANUAL",  -75.0),
        ("trd_c4", "gap_fade_long",     "14:50:00", "TGT_HIT", 200.0),
    ]
    for tid, strat, et, reason, pnl in closed:
        c.execute(
            "INSERT INTO trades(trade_id,signal_id,symbol,direction,strategy,sector,qty_planned,"
            "qty_filled,margin_reserved,risk_amount,created_at,entry_time,exit_time,exit_reason,"
            "gross_pnl,net_pnl,status,actual_position_value_rs) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, f"sig_{tid}", "AAA", "LONG", strat, "IT", 10, 10, 5000.0, 100.0,
             _ts("10:05:00"), _ts("10:06:00"), _ts(et), reason, pnl + 5, pnl, "CLOSED", 10000.0),
        )
    opens = [("trd_o1", "gap_fade_long"), ("trd_o2", "gap_fade_long"),
             ("trd_o3", "vwap_bounce_long"), ("trd_o4", "range_breakout_long")]
    for tid, strat in opens:
        c.execute(
            "INSERT INTO trades(trade_id,signal_id,symbol,direction,strategy,sector,qty_planned,"
            "qty_filled,margin_reserved,risk_amount,created_at,entry_time,exit_time,exit_reason,"
            "gross_pnl,net_pnl,status,actual_position_value_rs) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, f"sig_{tid}", "AAA", "LONG", strat, "IT", 10, 10, 5000.0, 100.0,
             _ts("11:00:00"), _ts("11:01:00"), None, None, None, None, "OPEN", 10000.0),
        )

    # ── fm_ledger: INIT total=100000 (70k intraday + 30k positional); losses=450 ──
    c.execute("INSERT INTO fm_ledger(ts,entry_type,amount,bucket,balance_before,balance_after) "
              "VALUES(?,?,?,?,?,?)", (_ts("08:15:01"), "INIT", 70000.0, "intraday", 0.0, 70000.0))
    c.execute("INSERT INTO fm_ledger(ts,entry_type,amount,bucket,balance_before,balance_after) "
              "VALUES(?,?,?,?,?,?)", (_ts("08:15:02"), "INIT", 30000.0, "positional", 0.0, 30000.0))
    for i, loss in enumerate((-100.0, -50.0, -75.0, -225.0)):  # sum = -450
        c.execute("INSERT INTO fm_ledger(ts,entry_type,amount,bucket,balance_before,balance_after,"
                  "pnl_delta,costs) VALUES(?,?,?,?,?,?,?,?)",
                  (_ts(f"15:1{i}:00"), "RELEASE_USED", 0.0, "intraday", 0.0, 0.0, loss, 2.0))
    c.execute("INSERT INTO fm_ledger(ts,entry_type,amount,bucket,balance_before,balance_after,"
              "pnl_delta,costs) VALUES(?,?,?,?,?,?,?,?)",
              (_ts("14:50:00"), "RELEASE_USED", 0.0, "intraday", 0.0, 0.0, 200.0, 2.0))  # a win

    # ── capital_snapshot: used 42000 / pending 3000 ──
    c.execute("INSERT INTO capital_snapshot(id,cash_floor,realized_pnl_today,margin_used,"
              "margin_reserved,charges_today,updated_at) VALUES(1,?,?,?,?,?,?)",
              (55000.0, -25.0, 42000.0, 3000.0, 10.0, _ts("15:15:00")))

    # ── config snapshot (limits source) ──
    config_json = json.dumps({"system": _SNAPSHOT_SYSTEM, "scoring": {}, "slippage": {}})
    c.execute("INSERT INTO config_snapshots(snapshot_date,snapshot_ts,account_id,mode,trade_type,"
              "config_hash,config_json) VALUES(?,?,?,?,?,?,?)",
              (TODAY, _ts("08:15:30"), "LFL836", "PAPER", "INTRADAY", "deadbeef" * 8, config_json))

    # ── events ──
    for et, scn, tm in (("STARTUP", "COLD", "08:15:00"), ("RECOVERY", None, "08:16:00"),
                        ("CONFIG_DIFF", None, "08:17:00")):
        c.execute("INSERT INTO system_events(timestamp,event_type,scenario,details) VALUES(?,?,?,?)",
                  (_ts(tm), et, scn, "{}"))

    if schema_version >= 42:
        c.execute("INSERT INTO eod_broker_reconciliation(date,overall_status) VALUES(?, 'VERIFIED')",
                  (TODAY,))
    conn.commit()


def _build_db(path: str, schema_version: int) -> None:
    conn = sqlite3.connect(path)
    try:
        for stmt in DDL:
            conn.execute(stmt)
        if schema_version >= 42:
            for stmt in DDL_V42_EXTRA:
                conn.execute(stmt)
        _seed(conn, schema_version)
    finally:
        conn.close()


def _build_analytics(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE system_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
            "cpu_pct REAL, memory_mb REAL, disk_used_pct REAL)"
        )
        conn.commit()
    finally:
        conn.close()


def _write_strategies(config_dir: str) -> None:
    sdir = os.path.join(config_dir, "strategies")
    os.makedirs(sdir, exist_ok=True)
    strategies = [
        ("gap_fade_long", "LONG", True, 3),
        ("vwap_bounce_long", "LONG", True, 2),
        ("range_breakout_long", "LONG", True, 2),
        ("gap_fade_short", "SHORT", False, 2),
    ]
    for name, direction, enabled, cap in strategies:
        with open(os.path.join(sdir, f"{name}.yaml"), "w", encoding="utf-8") as fh:
            yaml.safe_dump({
                "name": name, "display_name": name.replace("_", " ").title(),
                "direction": direction, "enabled": enabled,
                "order_protocol": "CO_PLUS_TGT", "max_concurrent_positions": cap,
                "entry_start_time": "09:25", "entry_end_time": "15:00",
            }, fh, sort_keys=False)
    # a minimal system_config.yaml for the YAML-fallback path
    with open(os.path.join(config_dir, "system_config.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump(_SNAPSHOT_SYSTEM, fh, sort_keys=False)


@pytest.fixture(params=[41, 42], ids=["v41", "v42"])
def schema_version(request):
    return request.param


@pytest.fixture
def today():
    return TODAY


@pytest.fixture
def gui_config(tmp_path, schema_version):
    main_db = str(tmp_path / "trading_system.db")
    analytics_db = str(tmp_path / "analytics.db")
    config_dir = str(tmp_path / "config")
    os.makedirs(config_dir, exist_ok=True)
    _build_db(main_db, schema_version)
    _build_analytics(analytics_db)
    _write_strategies(config_dir)
    return {
        "paths": {
            "main_db": main_db, "analytics_db": analytics_db,
            "logs_dir": str(tmp_path / "logs"), "reports_dir": str(tmp_path / "reports"),
            "config_dir": config_dir,
        },
        "server": {"bind_host": "127.0.0.1", "bind_port": 8500, "session_cookie_secure": False},
        "trader_metrics": {
            "health_url": "http://127.0.0.1:8080/health",
            "metrics_url": "http://127.0.0.1:8080/metrics", "timeout_sec": 1,
        },
        "poll": {"market_ms": 5000, "off_ms": 60000},
        "units": ["trading-system.service", "token-watcher.service", "alert-watcher.service",
                  "trading-watchman.service", "security-watcher.service", "cron-watchdog.timer"],
        "market_clock": {
            "market_open": "09:15", "entry_start": "10:00", "entry_end": "15:00",
            "eod_squareoff": "15:17", "market_close": "15:30", "active_weekdays": [0, 1, 2, 3, 4],
        },
        "auth": {
            "username": "tester",
            # pbkdf2 hash of "secret123" (generated via backend.auth.hash_password)
            "password_hash": "",
            "totp_secret": "", "max_failures": 5, "lockout_minutes": 15,
            "session_lifetime_minutes": 60,
        },
    }


@pytest.fixture
def app(gui_config):
    application = app_module.create_app(gui_config=gui_config)
    application.testing = True
    return application


@pytest.fixture
def client(app):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user"] = "tester"   # authenticated session for API contract tests
    return c
