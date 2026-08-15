"""
Shared pytest fixtures: synthetic v41 + v42 fixture DBs built from minimal-but-
faithful DDL (column names/types match core/schema.sql for every column the
readers touch), seeded to cover:

  * Rama's V4 funnel (100 received / 85 validated / 10 dup / 5 rej / 70 created
    / 60 filled) — split across scanners for attribution
  * a SILENT enabled strategy (first_pullback_long — configured, zero rows)
  * an ALL-LOSING strategy (vwap_bounce_long — 0W/2L + a FAILED order)
  * an N:1 scanner case (gap_fade_long_alt → gap_fade_long, attribution exact)
  * an UNMAPPED scanner (momentum_combo — must surface as "scanner-level (shared)")
  * an EXPIRED signal (REJECTED_EXPIRED), a CANCELLED order, a superseded chain
  * innings (inning# for one open trade) + an empty gtt_state (holdings)

Isolation: standalone SQLite files + a tmp config dir; no production imports.
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

from datetime import timedelta  # noqa: E402

TODAY = freshness.ist_today_iso()
YDAY = (freshness.ist_now() - timedelta(days=1)).strftime("%Y-%m-%d")
TENDAYS = (freshness.ist_now() - timedelta(days=10)).strftime("%Y-%m-%d")   # G5c: month-only


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
        rejection_reason TEXT, trade_id TEXT, trigger_price REAL, fingerprint TEXT,
        webhook_payload TEXT)""",
    # Screen-06: `price` and `trigger_price` are what the SL/TGT legs carry at the
    # BROKER (SL uses trigger_price, TGT uses price). They exist in
    # core/schema.sql and were simply absent here — the fixture's own contract is
    # to match schema.sql for every column a reader touches.
    """CREATE TABLE orders (order_id TEXT PRIMARY KEY, trade_id TEXT, leg TEXT, transaction_type TEXT,
        order_type TEXT, product TEXT, variety TEXT, qty_requested INTEGER, price REAL,
        trigger_price REAL, status TEXT,
        qty_filled INTEGER DEFAULT 0, avg_fill_price REAL, placed_at TEXT, filled_at TEXT,
        updated_at TEXT, rejection_reason TEXT, superseded_by TEXT)""",
    """CREATE TABLE trades (trade_id TEXT PRIMARY KEY, signal_id TEXT, symbol TEXT, direction TEXT,
        strategy TEXT, sector TEXT, qty_planned INTEGER, qty_filled INTEGER,
        entry_target_price REAL, entry_actual_price REAL, sl_initial REAL, tgt_initial REAL,
        margin_reserved REAL, risk_amount REAL, created_at TEXT, entry_time TEXT, exit_time TEXT,
        exit_reason TEXT, exit_price REAL, charges REAL, gross_pnl REAL, net_pnl REAL, status TEXT,
        actual_position_value_rs REAL, signal_to_order_ms INTEGER, order_to_fill_ms INTEGER,
        total_latency_ms INTEGER,
        -- W8 closure axes (v45). Screen-06 surfaces them verbatim in the detail
        -- card and NEVER infers one from the other.
        closure_source TEXT, exit_mechanism TEXT,
        -- Screen-07: all three exist in core/schema.sql and were simply absent
        -- here; the fixture's own contract is to match schema.sql for every
        -- column a reader touches. `tgt_risk_reward_applied` is the PLANNED R:R
        -- "frozen at placement" (schema.sql:230) — which is why the Explorer
        -- reads it rather than today's strategy YAML.
        tgt_risk_reward_applied REAL, binding_constraint TEXT, mode TEXT,
        -- Screen-10: both exist in core/schema.sql:207-208 and were simply
        -- absent here. They are the fraction the ORDER PATH actually resolved
        -- for this trade (symbol > strategy > band > global) and which rule
        -- won. Screen 10 reads them, so the fixture's contract requires them.
        tolerance_fraction_used REAL, tolerance_source TEXT)""",
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
    """CREATE TABLE innings (id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT,
        inning_number INTEGER, is_real INTEGER, exit_reason TEXT,
        UNIQUE(trade_id, inning_number))""",
    """CREATE TABLE gtt_state (gtt_id INTEGER PRIMARY KEY, trade_id TEXT, status TEXT,
        exit_side TEXT, qty INTEGER, sl_trigger REAL, sl_limit REAL, tgt_trigger REAL,
        tgt_limit REAL, needs_review INTEGER DEFAULT 0)""",
    """CREATE TABLE cron_heartbeat (id INTEGER PRIMARY KEY AUTOINCREMENT, job_name TEXT,
        executed_at TEXT, status TEXT DEFAULT 'SUCCESS', duration_sec REAL, message TEXT)""",
    """CREATE TABLE reconciliation_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,
        check_name TEXT, tier TEXT, symbol TEXT, trade_id TEXT, description TEXT,
        action_taken TEXT, success INTEGER)""",
    """CREATE TABLE eod_verification (date TEXT PRIMARY KEY, open_trades INTEGER DEFAULT 0,
        pending_orders INTEGER DEFAULT 0, pnl_variance REAL DEFAULT 0.0,
        status TEXT DEFAULT 'VERIFIED', verified_at TEXT)""",
    """CREATE TABLE preflight_runs (run_id TEXT PRIMARY KEY, run_date TEXT, phase TEXT,
        started_at TEXT, completed_at TEXT, total_checks INTEGER DEFAULT 0,
        passed INTEGER DEFAULT 0, failed_critical INTEGER DEFAULT 0, warnings INTEGER DEFAULT 0,
        autofixes_attempted INTEGER DEFAULT 0, autofixes_succeeded INTEGER DEFAULT 0,
        overall_status TEXT, alert_id TEXT)""",
    """CREATE TABLE preflight_check_results (run_id TEXT, run_date TEXT, check_name TEXT,
        check_group TEXT, criticality TEXT, status TEXT, duration_ms INTEGER,
        details_json TEXT, fix_attempted INTEGER DEFAULT 0, fix_result TEXT)""",
    # Screen-12: the auto-recovery audit trail. Exists in core/schema.sql:1201
    # and was simply absent here; `result` is SUCCESS | FAILED, and a row with
    # NEITHER is an attempt that has not resolved.
    """CREATE TABLE preflight_autofix_log (log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT, check_name TEXT, attempted_at TEXT, fix_action TEXT,
        before_state TEXT, after_state TEXT, result TEXT, error_msg TEXT)""",
    """CREATE TABLE control_tower_findings (id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_time TEXT, category TEXT, severity TEXT, resource_type TEXT, resource_name TEXT,
        location TEXT, reason TEXT, recommended_action TEXT, status TEXT DEFAULT 'OPEN',
        first_seen TEXT, last_seen TEXT, acked_at TEXT, resolved_at TEXT, remarks TEXT)""",
    """CREATE TABLE telegram_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, sent_at TEXT,
        severity TEXT, title TEXT, body TEXT, status TEXT DEFAULT 'PENDING',
        attempts INTEGER DEFAULT 0, source_module TEXT)""",
    """CREATE TABLE trade_slippage_log (id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT,
        trade_date DATE, symbol TEXT, strategy_name TEXT, side TEXT, qty INTEGER,
        price_band TEXT, entry_signal_price REAL, entry_fill_price REAL,
        entry_slippage_rs REAL, entry_slippage_pct REAL, sl_trigger_price REAL,
        sl_fill_price REAL, sl_slippage_rs REAL, sl_slippage_pct REAL, tgt_price REAL,
        tgt_fill_price REAL, tgt_slippage_rs REAL, tgt_slippage_pct REAL,
        planned_sl_distance REAL, planned_rr REAL, actual_rr REAL, rr_damage_pct REAL,
        trade_result TEXT, exit_reason TEXT, created_at TEXT)""",
    """CREATE TABLE order_execution_log (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT,
        parent_trade_id TEXT, signal_id TEXT, symbol TEXT, strategy_name TEXT, leg TEXT,
        order_type TEXT, side TEXT, intended_price REAL, actual_price REAL, slippage_rs REAL,
        slippage_pct REAL, qty INTEGER, filled_qty INTEGER, is_partial INTEGER DEFAULT 0,
        retry_count INTEGER DEFAULT 0, status TEXT, order_timestamp TEXT, fill_timestamp TEXT,
        exchange_timestamp TEXT, tolerance_fraction_used REAL, tolerance_source TEXT,
        created_at TEXT)""",
    """CREATE TABLE trade_excursions (trade_id TEXT PRIMARY KEY, mfe_price REAL, mfe_pct REAL,
        mae_price REAL, mae_pct REAL, entry_candle_open REAL, entry_candle_high REAL,
        entry_candle_low REAL, entry_candle_close REAL, updated_at TEXT)""",
    # G5c: screener score = System Score (L8). Absent in the pre-G5c fixture.
    # `eligible_score` is the v14 THRESHOLD column and it exists in
    # core/schema.sql; it was absent here, which is what made the pre-13-Aug
    # mapping test vacuous. The fixture's contract is to match schema.sql for
    # every column a reader touches, and signal_scores() touches this one.
    # Screen-11: `ts` and `latencies` are the REAL column names in
    # core/schema.sql:705-708 (`created_at` below is a fixture-ism that predates
    # this and is kept so the existing seed insert is unchanged). `latencies` is
    # the ONLY per-stage timing this system records — screening/step_executor.py
    # writes it as {step_name: elapsed_ms} over the ten screening steps — so the
    # fixture must carry it for the Execution-Analytics reader to be testable.
    """CREATE TABLE screener_results (id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id TEXT, score INTEGER, eligible_score INTEGER, created_at TEXT,
        tier TEXT, status TEXT, step_results TEXT, latencies TEXT,
        market_data_snapshot TEXT, ts TEXT)""",
]

DDL_V42_EXTRA = [
    """CREATE TABLE eod_broker_reconciliation (recon_id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, overall_status TEXT, self_consistency INTEGER DEFAULT 0,
        authoritative INTEGER DEFAULT 0, mismatch INTEGER)""",
]

# System config embedded in the config_snapshot (drives capacity + tower limits).
_SNAPSHOT_SYSTEM = {
    "force_intraday_only": True,
    "trade_type": "INTRADAY",
    "risk": {
        "max_daily_trades": 10, "max_open_positions": 5,
        "max_open_delivery_positions": 3, "max_daily_delivery_trades": 5,
        "max_consecutive_losses": 5, "daily_loss_limit_pct": 0.03,
        "max_sector_exposure_pct": 0.40,
    },
    "capital": {"intraday_bucket_pct": 0.70, "positional_bucket_pct": 0.30},
    "signal_queue": {"capacity": 300, "backpressure_pct": 0.80},
    "position_sizing": {
        "max_concentration_pct": 0.10, "max_position_value_pct": 0.40,
        "max_single_order_qty": 10000,
        "tier_multipliers": {"HIGH": 1.0, "MEDIUM": 0.70, "LOW": 0.50},
        "dynamic_by_winrate": True, "min_multiplier": 0.5, "max_multiplier": 2.0,
    },
    "signal_processor": {
        "entry_burst_max": 3, "entry_burst_window_sec": 60,
        "min_gap_between_entries_sec": 20, "per_symbol_cooldown_sec": 300,
    },
    "webhook": {
        "per_ip_rate_limit_enabled": True, "per_ip_burst": 60,
        "per_ip_refill_per_sec": 5.0, "dedup_window_seconds": 300,
    },
    "kill_switch": {"api_failure_threshold": 3, "enable_auto_trip": True},
    "drift_handler": {"log_only_threshold_rs": 250.0, "soft_kill_threshold_rs": 1000.0,
                      "hard_kill_threshold_rs": 2500.0, "consecutive_cycles_before_escalate": 3},
    "strategy_circuit_breaker": {"enabled": True, "loss_multiplier": 2.0,
                                 "cutoff_time": "12:00", "lookback_days": 10},
    "circuit_breaker": {"partial_fill_timeout_minutes": 5, "force_close_time": "15:15",
                        "max_api_failures": 3},
    "order_reconciler": {"capital_drift_tolerance": 50.0, "capital_drift_tolerance_pct": 0.10,
                         "human_order_margin_tolerance": 5000.0},
    "clock": {"warn_skew_sec": 2.0, "alert_skew_sec": 5.0, "halt_skew_sec": 30.0},
    "live_feed": {"max_reconnect_attempts": 10},
    "entry_gate": {
        "max_entry_slippage_pct": 1.0,
        "slippage_control": {"mode": "sl_fraction", "max_slippage_fraction": 0.22,
                             "absolute_cap_rs": 5.0, "hard_max_slippage_rs": 10.0},
    },
    "smart_tgt": {"enabled": True, "trigger_pct": 0.005, "step_pct": 0.003,
                  "max_modify_failures": 3},
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

    # ── Webhook funnel: totals 100 received / 85 accepted / 15 rejected, split
    #    across scanners: gap_fade_long 50/8 · gap_fade_long_alt 10/2 (N:1) ·
    #    vwap_bounce_long 20/3 · momentum_combo 5/2 (UNMAPPED → shared label) ──
    for scanner, acc, rej in (("gap_fade_long", 50, 8), ("gap_fade_long_alt", 10, 2),
                              ("vwap_bounce_long", 20, 3), ("momentum_combo", 5, 2)):
        c.execute(
            "INSERT INTO webhook_audit(ts,scanner_name,source_ip,payload_size_bytes,response_code,"
            "signals_accepted,signals_rejected,duration_ms) VALUES(?,?,?,?,?,?,?,?)",
            (_ts("10:31:00"), scanner, "1.2.3.4", 900, 200, acc, rej, 12),
        )

    # ── Signals: 10 DUPLICATE + 3 risk-rej + 1 expired (gap_fade_long),
    #    2 capital-rej (vwap), 8 QUEUED (gap_fade_long) ──
    def sig(sid, strat, status, hh="10:32:00", scanner=None):
        c.execute("INSERT INTO signals(signal_id,symbol,scanner,strategy,received_at,status) "
                  "VALUES(?,?,?,?,?,?)",
                  (sid, "AAA", scanner or strat, strat, _ts(hh), status))
    for i in range(10):
        sig(f"sig_dup_{i}", "gap_fade_long", "DUPLICATE")
    for i in range(3):
        sig(f"sig_risk_{i}", "gap_fade_long", "REJECTED_DAILY_LOSS", "10:33:00")
    sig("sig_exp_0", "gap_fade_long", "REJECTED_EXPIRED", "10:33:30")
    for i in range(2):
        sig(f"sig_cap_{i}", "vwap_bounce_long", "REJECTED_CAPITAL", "10:34:00")
    for i in range(8):
        sig(f"sig_q_{i}", "gap_fade_long", "QUEUED", "10:35:00")

    # ── Orders: 70 ENTRY placed (60 COMPLETE, 10 CANCELLED). The first 8 ENTRY
    #    orders belong to the 8 REAL trades (per-strategy PROCESSING join). ──
    real_trades = ["trd_c1", "trd_c2", "trd_c3", "trd_c4",
                   "trd_o1", "trd_o2", "trd_o3", "trd_o4"]
    for i in range(70):
        st = "COMPLETE" if i < 60 else "CANCELLED"
        filled = _ts("10:40:00") if st == "COMPLETE" else None
        trade_id = real_trades[i] if i < 8 else f"trd_e_{i}"
        c.execute(
            "INSERT INTO orders(order_id,trade_id,leg,transaction_type,order_type,product,variety,"
            "qty_requested,status,placed_at,filled_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"ord_e_{i}", trade_id, "ENTRY", "BUY", "LIMIT", "MIS", "regular", 10, st,
             _ts("10:39:00"), filled, _ts("10:40:00")),
        )
    # Exit fills: 8 SL + 12 TGT COMPLETE. ord_sl_0 → trd_c1 (gap SL_HIT close);
    # ord_tgt_0 → trd_c4 (gap TGT_HIT close). Others on synthetic trades.
    # Screen-06 broker-standing values. ⭐ DELIBERATELY NON-VACUOUS IN BOTH
    # DIRECTIONS: ord_sl_0's trigger (989.5) DIFFERS from trd_c1's sl_initial
    # (990.0) so a system-vs-broker mismatch is detectable, while ord_tgt_0's
    # limit (1015.0) MATCHES trd_c4's tgt_initial so the equal case is covered
    # too. A fixture where they always agree could not fail.
    for i in range(8):
        tid = "trd_c1" if i == 0 else f"trd_e_{i+10}"
        c.execute(
            "INSERT INTO orders(order_id,trade_id,leg,transaction_type,order_type,product,variety,"
            "qty_requested,price,trigger_price,status,placed_at,filled_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"ord_sl_{i}", tid, "SL", "SELL", "SL", "MIS", "regular", 10, 989.0, 989.5,
             "COMPLETE", _ts("11:00:00"), _ts("13:00:00"), _ts("13:00:00")),
        )
    for i in range(12):
        tid = "trd_c4" if i == 0 else f"trd_e_{i+30}"
        c.execute(
            "INSERT INTO orders(order_id,trade_id,leg,transaction_type,order_type,product,variety,"
            "qty_requested,price,trigger_price,status,placed_at,filled_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"ord_tgt_{i}", tid, "TGT", "SELL", "LIMIT", "MIS", "regular", 10, 1015.0, None,
             "COMPLETE", _ts("11:00:00"), _ts("13:30:00"), _ts("13:30:00")),
        )
    # Superseded chain: an old CANCELLED SL on trd_c1, replaced by ord_sl_0.
    c.execute(
        "INSERT INTO orders(order_id,trade_id,leg,transaction_type,order_type,product,variety,"
        "qty_requested,status,placed_at,filled_at,updated_at,superseded_by) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ord_sl_old", "trd_c1", "SL", "SELL", "SL", "MIS", "regular", 10, "CANCELLED",
         _ts("10:45:00"), None, _ts("11:00:00"), "ord_sl_0"),
    )
    # A FAILED order (rejection) on the all-losing strategy's trade (trd_c2, vwap).
    c.execute(
        "INSERT INTO orders(order_id,trade_id,leg,transaction_type,order_type,product,variety,"
        "qty_requested,status,placed_at,filled_at,updated_at,rejection_reason) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ord_fail_1", "trd_c2", "SL", "SELL", "SL", "MIS", "regular", 10, "FAILED",
         _ts("12:00:00"), None, _ts("12:00:00"), "Invalid tags: max allowed tag length is 20"),
    )

    # ── Trades: 8 created today — 4 CLOSED + 4 OPEN.
    #    Streak by exit_time DESC: c1(-100), c2(-50), c3(-75) → 3 losses, then c4 win. ──
    closed = [
        ("trd_c1", "gap_fade_long",    "15:10:00", "SL_HIT", -100.0),
        ("trd_c2", "vwap_bounce_long", "15:05:00", "EOD",     -50.0),
        ("trd_c3", "vwap_bounce_long", "15:00:00", "MANUAL",  -75.0),
        ("trd_c4", "gap_fade_long",    "14:50:00", "TGT_HIT", 200.0),
    ]
    for tid, strat, et, reason, pnl in closed:
        c.execute(
            "INSERT INTO trades(trade_id,signal_id,symbol,direction,strategy,sector,qty_planned,"
            "qty_filled,entry_target_price,entry_actual_price,sl_initial,tgt_initial,"
            "margin_reserved,risk_amount,created_at,entry_time,exit_time,exit_reason,"
            "exit_price,charges,gross_pnl,net_pnl,status,actual_position_value_rs,"
            "signal_to_order_ms,order_to_fill_ms,total_latency_ms) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, f"sig_{tid}", "AAA", "LONG", strat, "IT", 10, 10, 1000.0, 1001.0,
             990.0, 1015.0, 5000.0, 100.0, _ts("10:05:00"), _ts("10:06:00"), _ts(et),
             reason, 1001.0 + pnl / 10.0, 5.0, pnl + 5, pnl, "CLOSED", 10000.0,
             120, 850, 970),
        )
    opens = [("trd_o1", "gap_fade_long"), ("trd_o2", "gap_fade_long"),
             ("trd_o3", "vwap_bounce_long"), ("trd_o4", "range_breakout_long")]
    for tid, strat in opens:
        c.execute(
            "INSERT INTO trades(trade_id,signal_id,symbol,direction,strategy,sector,qty_planned,"
            "qty_filled,entry_target_price,entry_actual_price,sl_initial,tgt_initial,"
            "margin_reserved,risk_amount,created_at,entry_time,exit_time,exit_reason,"
            "gross_pnl,net_pnl,status,actual_position_value_rs,signal_to_order_ms,"
            "order_to_fill_ms,total_latency_ms) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, f"sig_{tid}", "AAA", "LONG", strat, "IT", 10, 10, 1000.0, 1001.0,
             990.0, 1015.0, 5000.0, 100.0, _ts("11:00:00"), _ts("11:01:00"), None,
             None, None, None, "OPEN", 10000.0, 150, 900, 1050),
        )

    # ── Innings: trd_o1 is on inning 2 (re-entry); others have no innings row. ──
    c.execute("INSERT INTO innings(trade_id,inning_number,is_real,exit_reason) VALUES(?,?,?,?)",
              ("trd_o1", 1, 1, "SL"))
    c.execute("INSERT INTO innings(trade_id,inning_number,is_real,exit_reason) VALUES(?,?,?,?)",
              ("trd_o1", 2, 0, "OPEN"))

    # G5b scanner-attribution fixtures: YESTERDAY-dated linking signals for the two
    # slippage trades so trades.signal_id → signals.scanner resolves. Yesterday-dated
    # ⇒ invisible to every today-scoped signal count (all signal reads filter
    # received_at LIKE today), so existing per-strategy signal assertions are unmoved.
    for tid, scn in (("trd_c1", "gap_fade_long"), ("trd_c4", "gap_fade_long")):
        c.execute("INSERT INTO signals(signal_id,symbol,scanner,strategy,received_at,status) "
                  "VALUES(?,?,?,?,?,?)",
                  (f"sig_{tid}", "AAA", scn, "gap_fade_long", f"{YDAY}T10:00:00+05:30", "TRADED"))

    # G5c: screener scores for the trade-linked signals. ⭐ The ACHIEVED score
    # (72) and the THRESHOLD it had to reach (65) are seeded as DIFFERENT
    # numbers on purpose: with one value for both, a reader that returned the
    # threshold under the "System Score" label — which is exactly the defect
    # corrected on 13-Aug — would pass every assertion.
    # Screen-11: `latencies` carries the per-step screening times. The two rows
    # are seeded with DIFFERENT step sets and DIFFERENT totals on purpose (sum
    # 41.5 ms vs 12.0 ms), so a reader that returned a constant, or summed the
    # wrong signal's blob, would fail rather than coincidentally pass.
    _lat = {
        "sig_trd_c1": {"volume_surge": 12.5, "vwap_position": 8.0, "atr_filter": 6.0,
                       "rsi_range": 5.0, "price_action": 10.0},          # = 41.5 ms
        "sig_trd_c4": {"volume_surge": 7.0, "vwap_position": 5.0},        # = 12.0 ms
    }
    for sid in ("sig_trd_c1", "sig_trd_c4"):
        c.execute("INSERT INTO screener_results(signal_id,score,eligible_score,created_at,"
                  "tier,status,step_results,latencies,market_data_snapshot,ts) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (sid, 72, 65, _ts("10:30:00"), "A", "PASS", "{}",
                   json.dumps(_lat[sid]), "{}", _ts("10:30:00")))

    # G5c multi-day trades for the period layer — gap_fade_long closed on YDAY
    # (within trailing-7) + TENDAYS (trailing-30 only). created_at+exit_time dated
    # that day ⇒ invisible to today-scoped counts; ALL older than today's 14:50 win
    # ⇒ loss-streak assertions (global + per-strategy) are unmoved.
    def _mkclosed(tid, day, reason, net, hh="14:00:00", sym="AAA", lat=(120, 850, 970),
                  exit_hh=None):
        # `lat` = (signal_to_order_ms, order_to_fill_ms, total_latency_ms). The
        # default reproduces the original seed EXACTLY, so every pre-existing
        # caller and latency assertion is byte-unchanged; Screen-11 passes varied
        # values so the FAST/MODERATE/SLOW bands and the warning thresholds are
        # each exercised by a real row rather than assumed reachable.
        ts = f"{day}T{hh}+05:30"
        # Screen-11: exit_time defaults to ts (byte-identical to the original
        # seed) but can be moved LATER so Trade Duration is a real span. ⭐ With
        # entry == exit the duration is 0, and 0 is exactly the value a broken
        # duration would produce — the fixture could not tell the two apart.
        # ⚠️ Any override must stay on the SAME DAY: Screen 09 buckets by
        # exit_time, so moving it across midnight would silently re-bucket it.
        ex = f"{day}T{exit_hh}+05:30" if exit_hh else ts
        c.execute(
            "INSERT INTO trades(trade_id,signal_id,symbol,direction,strategy,sector,qty_planned,"
            "qty_filled,entry_target_price,entry_actual_price,sl_initial,tgt_initial,margin_reserved,"
            "risk_amount,created_at,entry_time,exit_time,exit_reason,exit_price,charges,gross_pnl,"
            "net_pnl,status,actual_position_value_rs,signal_to_order_ms,order_to_fill_ms,total_latency_ms) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, f"sig_{tid}", sym, "LONG", "gap_fade_long", "IT", 10, 10, 1000.0, 1001.0,
             990.0, 1015.0, 5000.0, 100.0, ts, ts, ex, reason, 1001.0 + net / 10.0, 5.0,
             net + 5, net, "CLOSED", 10000.0, *lat))
        c.execute("INSERT INTO signals(signal_id,symbol,scanner,strategy,received_at,status) "
                  "VALUES(?,?,?,?,?,?)",
                  (f"sig_{tid}", sym, "gap_fade_long", "gap_fade_long", ts, "TRADED"))
    # Screen-10 needs several SYMBOLS to rank and several PRICE BUCKETS to fill,
    # so these three carry distinct symbols. `sym` defaults to "AAA", so every
    # pre-existing caller and assertion is byte-unchanged.
    # Screen-11 latency spread, and every value is chosen to land in a DIFFERENT
    # band so no band is merely assumed reachable:
    #   trd_w1  6.20 s total → SLOW      · fill 3.40 s → BOTH warnings fire
    #   trd_w2  3.50 s total → MODERATE  · fill 2.60 s → ⛔ under the 3 s fill
    #                                       threshold, so ONLY the total warning
    #                                       is eligible — and 3.50 s is under the
    #                                       5 s total threshold too, so NEITHER
    #                                       fires. That is the control: it proves
    #                                       the warning list is not just "every
    #                                       non-fast row".
    #   trd_m1  latency NULL → UNMEASURED (a closed trade whose timing was never
    #                                       recorded; ⛔ must not read as fast)
    #   Exits are moved LATER THE SAME DAY so Trade Duration is a real span
    #   (1h05m / 0h35m / 1h30m) rather than 0 — see the note in _mkclosed.
    _mkclosed("trd_w1", YDAY, "SL_HIT", -30.0, sym="BBB", lat=(2800, 3400, 6200),
              exit_hh="15:05:00")
    _mkclosed("trd_w2", YDAY, "TGT_HIT", 80.0, sym="CCC", lat=(900, 2600, 3500),
              exit_hh="14:35:00")
    _mkclosed("trd_m1", TENDAYS, "TGT_HIT", 50.0, sym="DDD", lat=(None, None, None),
              exit_hh="15:30:00")

    # ── fm_ledger: INIT total=100000; realized losses 450 + one win 200 ──
    # 25-Jul-2026: this used to seed TWO INIT rows split by bucket (intraday
    # 70000 + positional 30000) so that SUM(balance_after) == 100000. Production
    # has never looked like that: FundManager.initialize() writes exactly ONE
    # INIT row per process start, with bucket='both' and balance_after = the FULL
    # broker balance (capital/fund_manager.py:431-439). Verified against all 58
    # production INIT rows -- bucket='both' on every one, and multi-INIT days are
    # restart duplicates, never bucket splits.
    #
    # The bucket-split fixture is what let db_reader.opening_capital's
    # SUM(balance_after) look correct while it silently doubled on any real
    # restart day. Bucket allocations are derived as pct x opening_capital in
    # risk_capital.get_capital(), never from this column, so modelling
    # production here changes no other expectation.
    c.execute("INSERT INTO fm_ledger(ts,entry_type,amount,bucket,balance_before,balance_after) "
              "VALUES(?,?,?,?,?,?)", (_ts("08:15:01"), "INIT", 100000.0, "both", 0.0, 100000.0))
    for i, loss in enumerate((-100.0, -50.0, -75.0, -225.0)):  # sum = -450
        c.execute("INSERT INTO fm_ledger(ts,entry_type,amount,bucket,balance_before,balance_after,"
                  "pnl_delta,costs) VALUES(?,?,?,?,?,?,?,?)",
                  (_ts(f"15:1{i}:00"), "RELEASE_USED", 0.0, "intraday", 0.0, 0.0, loss, 2.0))
    c.execute("INSERT INTO fm_ledger(ts,entry_type,amount,bucket,balance_before,balance_after,"
              "pnl_delta,costs) VALUES(?,?,?,?,?,?,?,?)",
              (_ts("14:50:00"), "RELEASE_USED", 0.0, "intraday", 0.0, 0.0, 200.0, 2.0))

    # ── capital_snapshot: used 42000 / pending 3000 ──
    # ⚠️ 25-Jul-2026: NO READER USES THIS ROW ANY MORE. db_reader.capital_usage()
    # now derives the balances from trades + fm_ledger, because capital_snapshot
    # has 0 rows in production and these numbers were fiction -- the fixture's own
    # trades carry 4 OPEN x 5000 = 20000 of margin and no PENDING_FILL at all.
    # Kept only so the table stays exercised; do not add expectations against it.
    c.execute("INSERT INTO capital_snapshot(id,cash_floor,realized_pnl_today,margin_used,"
              "margin_reserved,charges_today,updated_at) VALUES(1,?,?,?,?,?,?)",
              (55000.0, -25.0, 42000.0, 3000.0, 10.0, _ts("15:15:00")))

    # ── config snapshots: yesterday (different hash — risk.max_daily_trades=9)
    #    + today. Drives the M20 drift banner + last-change-date (V4). ──
    y_system = json.loads(json.dumps(_SNAPSHOT_SYSTEM))
    y_system["risk"]["max_daily_trades"] = 9
    c.execute("INSERT INTO config_snapshots(snapshot_date,snapshot_ts,account_id,mode,trade_type,"
              "config_hash,config_json) VALUES(?,?,?,?,?,?,?)",
              (YDAY, f"{YDAY}T08:15:30+05:30", "LFL836", "PAPER", "INTRADAY",
               "cafebabe" * 8, json.dumps({"system": y_system})))
    config_json = json.dumps({"system": _SNAPSHOT_SYSTEM, "scoring": {}, "slippage": {}})
    c.execute("INSERT INTO config_snapshots(snapshot_date,snapshot_ts,account_id,mode,trade_type,"
              "config_hash,config_json) VALUES(?,?,?,?,?,?,?)",
              (TODAY, _ts("08:15:30"), "LFL836", "PAPER", "INTRADAY", "deadbeef" * 8, config_json))

    # ── G2b-2 seeds ──
    for job, tm in (("eod_verify", "15:55:10"), ("daily_trade_review", "16:07:20")):
        c.execute("INSERT INTO cron_heartbeat(job_name,executed_at,status,duration_sec) "
                  "VALUES(?,?,?,?)", (job, _ts(tm), "SUCCESS", 4.2))
    c.execute("INSERT INTO reconciliation_log(ts,check_name,tier,symbol,trade_id,description,"
              "action_taken,success) VALUES(?,?,?,?,?,?,?,?)",
              (_ts("12:30:00"), "MANUAL_CLOSE", "RECOVERABLE", "AAA", "trd_c3",
               "external close detected", "marked CLOSED_MANUAL", 1))
    c.execute("INSERT INTO eod_verification(date,open_trades,pending_orders,pnl_variance,"
              "status,verified_at) VALUES(?,?,?,?,?,?)",
              (TODAY, 0, 0, 0.0, "VERIFIED", _ts("15:55:30")))
    c.execute("INSERT INTO preflight_runs(run_id,run_date,phase,started_at,completed_at,"
              "total_checks,passed,overall_status) VALUES(?,?,?,?,?,?,?,?)",
              ("pf_a_1", TODAY, "A", _ts("08:30:00"), _ts("08:31:00"), 12, 12, "READY"))
    # Screen-12: checks spanning the FIVE readiness pillars. ⭐ `capital_deployment`
    # is seeded WARN on purpose so the Capital pillar is WARNING while every other
    # pillar is HEALTHY — with all five identical, a rollup that ignored one
    # pillar would still look right. ⭐ And Capital is assembled from check NAMES
    # (it has no group of its own), so this also proves that path.
    for _cn, _cg, _crit, _st in (
            ("vm_ram", "VM Health", "CRITICAL", "PASS"),
            ("kite_token_file_exists", "Broker", "CRITICAL", "PASS"),
            ("kite_token_fresh_today", "Broker", "CRITICAL", "PASS"),
            ("kite_profile_call_ok", "Broker", "CRITICAL", "PASS"),
            ("kite_orders_endpoint", "Broker", "WARN", "PASS"),
            ("kite_funds_available", "Broker", "WARN", "PASS"),
            ("db_file_exists", "Database", "CRITICAL", "PASS"),
            ("db_writable", "Database", "CRITICAL", "PASS"),
            ("app_health", "Engine", "CRITICAL", "PASS"),
            ("fund_manager_balance", "Engine", "CRITICAL", "PASS"),
            ("capital_deployment", "Engine", "WARN", "WARN"),
            ("kill_switch_state", "State", "CRITICAL", "PASS"),
            ("open_positions_at_start", "State", "WARN", "PASS")):
        c.execute("INSERT INTO preflight_check_results(run_id,run_date,check_name,"
                  "check_group,criticality,status) VALUES(?,?,?,?,?,?)",
                  ("pf_a_1", TODAY, _cn, _cg, _crit, _st))
    # Recovery lifecycle — one of EACH state, so TRIGGERED can never be mistaken
    # for SUCCESS by a counter that only looks at row presence.
    for _cn, _act, _res, _err in (
            ("alert_backlog", "flush_alert_backlog", "SUCCESS", None),
            ("today_log_writable", "chmod_log_dir", "FAILED", "permission denied"),
            ("cron_marks_dir_writable", "mkdir_marks", None, None)):
        c.execute("INSERT INTO preflight_autofix_log(run_id,check_name,attempted_at,"
                  "fix_action,before_state,after_state,result,error_msg) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  ("pf_a_1", _cn, _ts("08:30:30"), _act, "bad", "good", _res, _err))
    c.execute("INSERT INTO control_tower_findings(scan_time,category,severity,resource_type,"
              "resource_name,reason,status,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?)",
              (_ts("17:05:00"), "disk", "HIGH", "mount", "/dev/sda1",
               "disk 87% used", "OPEN", _ts("17:05:00"), _ts("17:05:00")))
    c.execute("INSERT INTO telegram_alerts(sent_at,severity,title,status,attempts,source_module) "
              "VALUES(?,?,?,?,?,?)",
              (_ts("15:10:05"), "INFO", "SL_HIT AAA gap_fade_long", "SENT", 1, "order_placer"))
    c.execute("INSERT INTO telegram_alerts(sent_at,severity,title,status,attempts,source_module) "
              "VALUES(?,?,?,?,?,?)",
              (_ts("12:00:05"), "CRITICAL", "order FAILED: invalid tag", "SENT", 1, "order_reconciler"))
    # slippage: row 1 BREACHES the sl_fraction budget (tol = min(0.22×10, 5) = 2.2 < 3.0);
    # row 2 does not (0.5 < 2.2).
    c.execute("INSERT INTO trade_slippage_log(trade_id,trade_date,symbol,strategy_name,side,qty,"
              "price_band,entry_signal_price,entry_fill_price,entry_slippage_rs,"
              "planned_sl_distance,planned_rr,actual_rr,rr_damage_pct,trade_result,exit_reason) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("trd_c1", TODAY, "AAA", "gap_fade_long", "LONG", 10, "200-300",
               1000.0, 1003.0, 3.0, 10.0, 1.5, 1.1, 27.0, "LOSS", "SL_HIT"))
    c.execute("INSERT INTO trade_slippage_log(trade_id,trade_date,symbol,strategy_name,side,qty,"
              "price_band,entry_signal_price,entry_fill_price,entry_slippage_rs,"
              "planned_sl_distance,planned_rr,actual_rr,rr_damage_pct,trade_result,exit_reason) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              ("trd_c4", TODAY, "AAA", "gap_fade_long", "LONG", 10, "200-300",
               1000.0, 1000.5, 0.5, 10.0, 1.5, 1.45, 4.0, "WIN", "TGT_HIT"))
    # ── SCREEN 10 (15-Aug-2026) ────────────────────────────────────────────────
    # (a) trd_c1 carries the fraction the ORDER PATH actually resolved (0.15 via a
    #     by_symbol override) while trd_c4 carries NONE. The two are seeded
    #     DIFFERENTLY ON PURPOSE and neither equals the global 0.22, so a reader
    #     that ignored the persisted value and always used the config global would
    #     produce allowed=2.2 for both and FAIL — the fixture can tell the two
    #     code paths apart. ⛔ A fixture where both paths give the same number
    #     proves nothing.
    #     trd_c1: min(10 × 0.15, 5) = 1.50  ⇒ actual 3.0 = 200% ⇒ EXCEEDED
    #     trd_c4: min(10 × 0.22, 5) = 2.20  ⇒ actual 0.5 =  23% ⇒ WITHIN_LIMIT
    #     The today-scoped /api/slippage endpoint does NOT read these columns, so
    #     its tolerance_rs == 2.2 assertions are untouched.
    c.execute("UPDATE trades SET tolerance_fraction_used=?, tolerance_source=? "
              "WHERE trade_id=?", (0.15, "symbol:AAA", "trd_c1"))
    # (b) Multi-day slippage rows so the RANGE layer, the five price buckets and
    #     all four statuses are exercised. All are dated BEFORE today, so every
    #     today-scoped assertion on /api/slippage (count == 2) is unmoved.
    #       BBB @ 85    → bucket 0-100    · 0.30 vs min(5×0.22,5)=1.10 = 27% → WITHIN
    #       CCC @ 450   → bucket 200-500  · 1.55 vs min(10×0.22,5)=2.20 = 70% → NEAR
    #       DDD @ 150   → bucket 100-200  · slippage NULL            → UNMEASURED
    #     ⭐ Bucket 500-1000 is deliberately left EMPTY: the panel must render all
    #     five and show the empty one as unobserved, ⛔ never as a measured ₹0.00.
    for tid, sym, band, px, fill, slip, dist, prr, arr, dmg, res, reason in (
            ("trd_w1", "BBB", "0-100", 85.0, 85.3, 0.30, 5.0, 2.0, 1.7, 6.0, "LOSS", "SL_HIT"),
            ("trd_w2", "CCC", "300-500", 450.0, 451.55, 1.55, 10.0, 2.0, 1.5, 15.5, "WIN", "TGT_HIT"),
            ("trd_m1", "DDD", "100-200", 150.0, None, None, None, None, None, None, "WIN", "TGT_HIT")):
        c.execute("INSERT INTO trade_slippage_log(trade_id,trade_date,symbol,strategy_name,side,qty,"
                  "price_band,entry_signal_price,entry_fill_price,entry_slippage_rs,"
                  "planned_sl_distance,planned_rr,actual_rr,rr_damage_pct,trade_result,exit_reason) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (tid, (YDAY if tid != "trd_m1" else TENDAYS), sym, "gap_fade_long",
                   "LONG", 10, band, px, fill, slip, dist, prr, arr, dmg, res, reason))
    for oid, leg, slip in (("ord_e_0", "ENTRY", 1.0), ("ord_sl_0", "SL", 0.2)):
        c.execute("INSERT INTO order_execution_log(order_id,parent_trade_id,symbol,strategy_name,"
                  "leg,side,intended_price,actual_price,slippage_rs,qty,filled_qty,status,"
                  "order_timestamp,fill_timestamp) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (oid, "trd_c1", "AAA", "gap_fade_long", leg, "BUY", 1000.0,
                   1000.0 + slip, slip, 10, 10, "COMPLETE", _ts("10:39:30"), _ts("10:40:00")))
    c.execute("INSERT INTO trade_excursions(trade_id,mfe_pct,mae_pct,updated_at) VALUES(?,?,?,?)",
              ("trd_c1", 1.2, -0.8, _ts("15:50:00")))

    # ── Screen-07: the PLANNED R:R frozen at placement, plus the sizing
    # constraint and the mode. ⭐ THREE DIFFERENT VALUES AND ONE DELIBERATE NULL:
    # every production strategy currently configures 1.5, so a fixture seeded
    # 1.5-everywhere could not tell a per-trade read from a constant, and a test
    # that only asserted "None when unset" would pass on a completely broken one.
    for tid, rr, bc in (("trd_c1", 1.5, "concentration"), ("trd_c2", 2.0, "capital"),
                        ("trd_c3", 3.0, "risk"), ("trd_c4", None, "flat")):
        c.execute("UPDATE trades SET tgt_risk_reward_applied=?, binding_constraint=?, mode=? "
                  "WHERE trade_id=?", (rr, bc, "LIVE", tid))

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
    """analytics.db — MIRRORS `core/analytics_schema.sql:51`.

    ⚠️⚠️ THIS FIXTURE USED TO INVENT `id INTEGER PRIMARY KEY` + `ts`, NEITHER OF
    WHICH EXISTS IN PRODUCTION (the real columns are `timestamp … disk_used_pct`,
    with no `id`). A reader written against the fixture therefore passed its
    tests while raising `no such column: ts` against the real database — the
    Health Trends disk series was structurally empty in production and displayed
    as "NOT INSTRUMENTED". ⛔ A FIXTURE MUST MATCH PRODUCTION SHAPE, or it makes a
    wrong reader look right.

    Rows are REAL disk percentages with the -1.0 psutil sentinels left in place
    for cpu/memory, because a caller that charts a sentinel must fail a test.
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE system_metrics (timestamp TEXT NOT NULL, cpu_pct REAL, "
            "memory_mb REAL, db_size_mb REAL, log_size_mb REAL, open_fds INTEGER, "
            "thread_count INTEGER, disk_used_pct REAL)"
        )
        conn.executemany(
            "INSERT INTO system_metrics (timestamp, cpu_pct, memory_mb, "
            "disk_used_pct) VALUES (?,?,?,?)",
            [("2026-08-15 09:%02d:00" % m, -1.0, -1.0, 41.0 + m / 60.0)
             for m in (15, 20, 25, 30, 35, 40)],
        )
        conn.commit()
    finally:
        conn.close()


def _write_strategies(config_dir: str) -> None:
    """5 configured strategies: gap_fade_long (3-cap), vwap_bounce_long (all-losing),
    range_breakout_long, first_pullback_long (SILENT enabled), gap_fade_short (disabled)."""
    sdir = os.path.join(config_dir, "strategies")
    os.makedirs(sdir, exist_ok=True)
    strategies = [
        ("gap_fade_long", "LONG", True, 3),
        ("vwap_bounce_long", "LONG", True, 2),
        ("range_breakout_long", "LONG", True, 2),
        ("first_pullback_long", "LONG", True, 2),   # silent — configured, zero rows
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
    # scan_webhook_map: 1:1 for each strategy + N:1 (gap_fade_long_alt → gap_fade_long).
    # momentum_combo is deliberately ABSENT (unmapped → "scanner-level (shared)").
    scan_map = {"scanners": {}}
    for name, _d, _e, _c in strategies:
        scan_map["scanners"][name] = {"strategy": name, "chartink_url": f"https://x/{name}"}
    scan_map["scanners"]["gap_fade_long_alt"] = {"strategy": "gap_fade_long",
                                                 "chartink_url": "https://x/alt"}
    with open(os.path.join(config_dir, "scan_webhook_map.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump(scan_map, fh, sort_keys=False)
    # minimal system_config.yaml for the YAML-fallback path
    with open(os.path.join(config_dir, "system_config.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump(_SNAPSHOT_SYSTEM, fh, sort_keys=False)
    # cron_registry.yaml (M11 expected-vs-actual join)
    registry = {"jobs": {
        "eod_verify": {"monitored": True, "enabled": True, "critical": True,
                       "cron_expression": "55 15 * * 1-5", "marker_name": "eod_verify"},
        "daily_trade_review": {"monitored": True, "enabled": True, "critical": False,
                               "cron_expression": "7 16 * * 1-5",
                               "marker_name": "daily_trade_review"},
        "silent_job": {"monitored": True, "enabled": True, "critical": False,
                       "cron_expression": "0 12 * * 1-5", "marker_name": "silent_job"},
        "retired_job": {"monitored": True, "enabled": False, "critical": False,
                        "cron_expression": "0 1 * * *", "marker_name": "retired_job"},
    }, "officer": {"briefing": "09:20"}}
    with open(os.path.join(config_dir, "cron_registry.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump(registry, fh, sort_keys=False)


def _write_runtime_dirs(tmp_path) -> dict:
    """Logs / reports / data_store fixture trees (M13/M15/M19)."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(exist_ok=True)
    # structured JSON-lines system log (10 lines, one ERROR, one with trade_id)
    with open(logs_dir / f"system_{TODAY}.log", "w", encoding="utf-8") as fh:
        for i in range(8):
            fh.write(json.dumps({"ts": f"{TODAY}T10:0{i}:00", "level": "INFO",
                                 "logger": "core.system", "msg": f"heartbeat {i}"}) + "\n")
        fh.write(json.dumps({"ts": f"{TODAY}T12:00:00", "level": "ERROR",
                             "logger": "order_placer", "msg": "placement failed",
                             "trade_id": "trd_c2", "order_id": "ord_fail_1"}) + "\n")
        fh.write(json.dumps({"ts": f"{TODAY}T13:00:00", "level": "INFO",
                             "logger": "order_placer", "msg": "SL filled",
                             "trade_id": "trd_c1"}) + "\n")
    with open(logs_dir / f"debug_{TODAY}.log", "w", encoding="utf-8") as fh:
        fh.write("plain debug line 1\nplain debug line 2\n")
    with open(logs_dir / "failed_alerts.log", "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": f"{TODAY}T12:00:06", "title": "telegram send failed"}) + "\n")
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(exist_ok=True)
    for name in (f"daily_trade_review_report_{TODAY}.xlsx", f"daily_report_{TODAY}.xlsx"):
        with open(reports_dir / name, "wb") as fh:
            fh.write(b"PK\x03\x04" + b"x" * 2048)   # xlsx-magic dummy
    data_store = tmp_path / "data_store"
    data_store.mkdir(exist_ok=True)
    with open(data_store / "critical_alert_20260703_120006.flag", "w", encoding="utf-8") as fh:
        fh.write("order FAILED: invalid tag")
    return {"logs_dir": str(logs_dir), "reports_dir": str(reports_dir),
            "data_store": str(data_store)}


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
    runtime = _write_runtime_dirs(tmp_path)
    return {
        "paths": {
            "main_db": main_db, "analytics_db": analytics_db,
            "logs_dir": runtime["logs_dir"], "reports_dir": runtime["reports_dir"],
            "config_dir": config_dir, "data_store": runtime["data_store"],
        },
        "scorecard": {},           # library defaults (strategy_score.DEFAULT_SCORECARD)
        "silence": {},
        "reports_download_enabled": False,
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
            "username": "tester", "password_hash": "", "totp_secret": "",
            "max_failures": 5, "lockout_minutes": 15, "session_lifetime_minutes": 60,
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


# ── L8 and its ONE authorised exception ──────────────────────────────────────
# L8 (G5_REDESIGN_PHASE_B.md) locked a single "System Score" and dropped the
# "Signal Score" label everywhere. Rama SUPERSEDED that FOR SCREEN-04 ONLY on
# 11-Aug-2026: the approved Signals redesign shows two genuinely different
# quantities, and both are read from real columns —
#     System Score = the minimum score required for eligibility
#                    (screener_results.eligible_score → config min_pass_score)
#     Signal Score = this signal's own score (screener_results.score)
# The sweep below is therefore NOT deleted: it still fails if the label appears
# on any other screen, so the drop cannot be undone by accident where it was
# deliberate. Widening this set needs the same kind of explicit decision.
# WIDENED ONCE, 12-Aug-2026, by explicit decision (Rama): Screen-05 Orders
# shows System Score + Signal Score between Direction and Broker Order ID,
# reusing Screen-04's reader and terminology. The guard is NARROWED, ⛔ not
# deleted — it still fails on every other screen, so the drop cannot be
# undone by accident where it was deliberate.
# WIDENED A SECOND TIME, 12-Aug-2026, by explicit instruction: Screen-06
# Positions carries the SAME common column head as Screens 04/05 — System Score
# and Signal Score sit between Direction and the position data, read from the
# SAME db_reader.signal_scores reader, and both must participate in the movable
# heading mechanism. ⛔ The guard is NARROWED again, ⛔ never deleted: it still
# fails on every OTHER screen, so L8's drop cannot be undone by accident where
# it was deliberate. A third widening needs the same kind of explicit decision.
# ── L8 RESTORED IN FULL, 13-Aug-2026 (Rama) — the exceptions above are CLOSED ─
# The three widenings recorded above are HISTORY, kept because they were real
# decisions; they are no longer in force. Rama, 13-Aug-2026, ruled the naming
# system-wide and reverted his own 11-Aug supersession:
#     System Score    = the ACHIEVED score  (screener_results.score)
#     Score Threshold = the minimum required (eligible_score → min_pass_score)
#     "Signal Score"  = RETIRED as a label AND as a payload key.
# His words: *"If a screen has no genuine second score, omit 'Signal Score'
# rather than fabricate/relabel a threshold."*
#
# ⭐ WHY THIS IS A RESTORATION AND NOT A NEW RULE: the codebase already carried
# BOTH meanings of "System Score" at once — db_reader.screener_scores() has
# always returned the ACHIEVED score under that label for analytics, operations,
# trade_explorer and trade_logs, while db_reader.signal_scores() returned the
# THRESHOLD under it for Screens 04/05/06. One label, two quantities, same app.
#
# ⇒ THE ALLOW-LIST IS NOW EMPTY, so the guard can go red on ANY file. It is kept
# as a set rather than deleted so a future widening is again an explicit act.
SIGNAL_SCORE_ALLOWED_FILES: set = set()


def assert_signal_score_label_is_retired(frontend_root):
    """L8 restored: the label 'Signal Score' must appear NOWHERE in the frontend.

    ⛔ Not 'confined to Screen-04' — that exception is closed. The old name of
    this function said 'confined_to_screen04' and would have been a lie the
    moment the allow-list emptied.
    """
    offenders = []
    for dirpath, _dirs, files in os.walk(frontend_root):
        for name in files:
            if name.endswith(".min.js") or name in SIGNAL_SCORE_ALLOWED_FILES:
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8", errors="ignore") as fh:
                if "signal score" in fh.read().lower():
                    offenders.append(path)
    assert not offenders, (
        "'Signal Score' is retired (L8 restored 13-Aug-2026) but still appears "
        "in: " + ", ".join(offenders))
