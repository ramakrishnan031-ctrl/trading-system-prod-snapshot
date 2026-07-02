"""
ops_dashboard/backend/readers/db_reader.py

THE ONLY SQLite touchpoint in the GUI (isolation rule I2). Every connection is
opened READ-ONLY (``file:<abs>?mode=ro``, uri=True) with a 30s busy_timeout and
the analytics DB ATTACHed the same read-only way (ATTACH order replicated from
core/db_connect BY VALUE, never imported). Connections are short-lived — one per
request path. Every query is parameterized. No ORM, one function per need.

A write through any connection from here raises sqlite3.OperationalError
("attempt to write a readonly database") — exercised by tests/test_isolation.py.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Optional

# trades that are "currently open" (not date-filtered). PUBLIC contract:
# the G0 §2.2 open-set — /api/positions echoes this list and a contract test
# pins it exactly.
OPEN_STATES = ("OPEN", "PARTIAL", "PENDING_FILL", "EXITING")
_OPEN_STATES = OPEN_STATES  # internal alias used by the query helpers
# trades that represent a completed position lifecycle
_CLOSED_STATES = ("CLOSED", "CLOSED_MANUAL")

# Reject-status families (signals.status == f"REJECTED_{check}"), from
# capital/risk_engine.py check codes + signals/signal_processor reject codes.
_RISK_REJECT_STATUSES = (
    "REJECTED_OPEN_POSITIONS",
    "REJECTED_DAILY_TRADES",
    "REJECTED_DAILY_LOSS",
    "REJECTED_CONSECUTIVE_LOSSES",
    "REJECTED_SECTOR_EXPOSURE",
    "REJECTED_CONTRARY_POSITION",
    "REJECTED_DUPLICATE_SYMBOL",
    "REJECTED_KILL_SWITCH",
    "REJECTED_STRATEGY_CIRCUIT_BREAKER",
    "REJECTED_TRADE_TYPE",
)
_CAPITAL_REJECT_STATUSES = (
    "REJECTED_CAPITAL",
    "REJECTED_RESERVE_FAILED",
    "REJECTED_SIZING_VALID",
)


def _uri(path: str) -> str:
    """Read-only SQLite URI. Forward slashes work on Windows Python."""
    return "file:" + path.replace("\\", "/") + "?mode=ro"


def open_readonly_connection(cfg: dict) -> sqlite3.Connection:
    """Open a read-only connection to the main DB with analytics ATTACHed ro.

    Public so tests/test_isolation.py can prove a write raises. The connection
    is read-only at the SQLite layer — not merely by convention.
    """
    main = cfg["paths"]["main_db"]
    conn = sqlite3.connect(_uri(main), uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    analytics = cfg["paths"].get("analytics_db")
    if analytics:
        try:
            conn.execute("ATTACH DATABASE ? AS analytics", (_uri(analytics),))
        except sqlite3.OperationalError:
            # Analytics DB missing/locked — M1 does not require it; proceed.
            pass
    return conn


@contextmanager
def _ro(cfg: dict) -> Iterator[sqlite3.Connection]:
    conn = open_readonly_connection(cfg)
    try:
        yield conn
    finally:
        conn.close()


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return row[0]


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    val = _scalar(conn, sql, params)
    return int(val) if val is not None else 0


def _in_clause(values: tuple) -> str:
    return ",".join("?" for _ in values)


# ─────────────────────────────────────────────────────────────────────────────
# Session / meta / kill-switch (header)
# ─────────────────────────────────────────────────────────────────────────────
def get_schema_version(cfg: dict) -> Optional[int]:
    with _ro(cfg) as conn:
        val = _scalar(conn, "SELECT value FROM schema_meta WHERE key='schema_version'")
        try:
            return int(val) if val is not None else None
        except (TypeError, ValueError):
            return None


def get_session_info(cfg: dict) -> dict:
    with _ro(cfg) as conn:
        row = conn.execute(
            "SELECT session_date, account_id, broker, mode, trade_type, "
            "session_start, last_updated FROM session WHERE id=1"
        ).fetchone()
    if row is None:
        return {}
    return dict(row)


def get_kill_switch(cfg: dict) -> dict:
    with _ro(cfg) as conn:
        row = conn.execute(
            "SELECT state, reason, triggered_at, triggered_by "
            "FROM kill_switch_state WHERE id=1"
        ).fetchone()
    if row is None:
        return {"state": "INACTIVE", "reason": None, "triggered_at": None, "triggered_by": None}
    return dict(row)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline stage counts (services/pipeline_state.py)
# ─────────────────────────────────────────────────────────────────────────────
def webhook_funnel(cfg: dict, today: str) -> dict:
    """Received / Validated / Rejected from webhook_audit aggregates (today)."""
    with _ro(cfg) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(signals_accepted),0) AS accepted, "
            "COALESCE(SUM(signals_rejected),0) AS rejected, "
            "COUNT(*) AS posts, "
            "COALESCE(SUM(CASE WHEN response_code=429 THEN 1 ELSE 0 END),0) AS rate_limited, "
            "MAX(ts) AS last_ts "
            "FROM webhook_audit WHERE date = ?",
            (today,),
        ).fetchone()
    accepted = int(row["accepted"])
    rejected = int(row["rejected"])
    return {
        "received": accepted + rejected,
        "validated": accepted,
        "rejected_total": rejected,
        "posts": int(row["posts"]),
        "rate_limited": int(row["rate_limited"]),
        "last_ts": row["last_ts"],
    }


def signals_duplicate_count(cfg: dict, today: str) -> int:
    """DB-recorded DUPLICATE signals today.

    NOTE (W9): on live this is typically ~0 — the webhook TTL dedup drops dupes
    PRE-INSERT and they are lumped into webhook_audit.signals_rejected. This is
    the only DB-recorded duplicate slice; the pipeline derives the residual
    reject bucket as (rejected_total - this).
    """
    with _ro(cfg) as conn:
        return _count(
            conn,
            "SELECT COUNT(*) FROM signals WHERE status='DUPLICATE' "
            "AND received_at LIKE ?",
            (today + "%",),
        )


def signals_status_family_count(cfg: dict, today: str, statuses: tuple) -> int:
    with _ro(cfg) as conn:
        sql = (
            "SELECT COUNT(*) FROM signals WHERE received_at LIKE ? "
            f"AND status IN ({_in_clause(statuses)})"
        )
        return _count(conn, sql, (today + "%", *statuses))


def signals_risk_rejected(cfg: dict, today: str) -> int:
    return signals_status_family_count(cfg, today, _RISK_REJECT_STATUSES)


def signals_capital_rejected(cfg: dict, today: str) -> int:
    return signals_status_family_count(cfg, today, _CAPITAL_REJECT_STATUSES)


def orders_entry_counts(cfg: dict, today: str) -> dict:
    """ENTRY-leg funnel today: created / placed / filled + last placed_at."""
    with _ro(cfg) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS created, "
            "COALESCE(SUM(CASE WHEN placed_at IS NOT NULL AND placed_at<>'' "
            "  THEN 1 ELSE 0 END),0) AS placed, "
            "COALESCE(SUM(CASE WHEN status='COMPLETE' THEN 1 ELSE 0 END),0) AS filled, "
            "MAX(placed_at) AS last_ts "
            "FROM orders WHERE leg='ENTRY' AND placed_at LIKE ?",
            (today + "%",),
        ).fetchone()
    return {
        "created": int(row["created"]),
        "placed": int(row["placed"]),
        "filled": int(row["filled"]),
        "last_ts": row["last_ts"],
    }


def orders_exit_leg_filled(cfg: dict, today: str, leg: str) -> dict:
    """COMPLETE exit-leg fills today (leg in {'SL','TGT'}), by filled_at date."""
    with _ro(cfg) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(filled_at) AS last_ts FROM orders "
            "WHERE leg=? AND status='COMPLETE' AND filled_at LIKE ?",
            (leg, today + "%"),
        ).fetchone()
    return {"count": int(row["n"]), "last_ts": row["last_ts"]}


def trades_closed_counts(cfg: dict, today: str) -> dict:
    """Trades closed today: total + 'other exit' (not SL_HIT/TGT_HIT)."""
    states = _CLOSED_STATES
    with _ro(cfg) as conn:
        total = _count(
            conn,
            f"SELECT COUNT(*) FROM trades WHERE status IN ({_in_clause(states)}) "
            "AND exit_time LIKE ?",
            (*states, today + "%"),
        )
        other = _count(
            conn,
            f"SELECT COUNT(*) FROM trades WHERE status IN ({_in_clause(states)}) "
            "AND exit_time LIKE ? AND (exit_reason IS NULL OR "
            "exit_reason NOT IN ('SL_HIT','TGT_HIT'))",
            (*states, today + "%"),
        )
        last_ts = _scalar(
            conn,
            f"SELECT MAX(exit_time) FROM trades WHERE status IN ({_in_clause(states)}) "
            "AND exit_time LIKE ?",
            (*states, today + "%"),
        )
    return {"closed": total, "other_exit": other, "last_ts": last_ts}


# ─────────────────────────────────────────────────────────────────────────────
# Capacity counters (services/capacity.py)
# ─────────────────────────────────────────────────────────────────────────────
def daily_trades_used(cfg: dict, today: str) -> int:
    with _ro(cfg) as conn:
        return _count(
            conn,
            "SELECT COUNT(*) FROM trades WHERE created_at LIKE ? "
            "AND status NOT GLOB 'REJECTED*'",
            (today + "%",),
        )


def open_positions_count(cfg: dict) -> int:
    states = _OPEN_STATES
    with _ro(cfg) as conn:
        return _count(
            conn,
            f"SELECT COUNT(*) FROM trades WHERE status IN ({_in_clause(states)})",
            states,
        )


def delivery_open_count(cfg: dict) -> int:
    states = _OPEN_STATES
    with _ro(cfg) as conn:
        return _count(
            conn,
            "SELECT COUNT(DISTINCT t.trade_id) FROM trades t "
            "JOIN orders o ON o.trade_id=t.trade_id AND o.leg='ENTRY' AND o.product='CNC' "
            f"WHERE t.status IN ({_in_clause(states)})",
            states,
        )


def delivery_daily_used(cfg: dict, today: str) -> int:
    with _ro(cfg) as conn:
        return _count(
            conn,
            "SELECT COUNT(DISTINCT trade_id) FROM orders "
            "WHERE leg='ENTRY' AND product='CNC' AND placed_at LIKE ?",
            (today + "%",),
        )


def realized_loss_today(cfg: dict, today: str) -> float:
    """Cumulative realized LOSS today (positive number) from RELEASE_USED rows."""
    with _ro(cfg) as conn:
        val = _scalar(
            conn,
            "SELECT COALESCE(SUM(pnl_delta),0.0) FROM fm_ledger "
            "WHERE date=? AND entry_type='RELEASE_USED' AND pnl_delta<0",
            (today,),
        )
    return abs(float(val or 0.0))


def opening_capital(cfg: dict, today: str) -> Optional[float]:
    """Day-opening TOTAL capital, summed across today's INIT ledger rows.

    Percentage limits (daily-loss, intraday-bucket) resolve against total capital.
    Fallback: capital_snapshot (cash_floor + margin_used + margin_reserved).
    Returns None if neither source is available (pre-open) → callers render '—'.
    """
    with _ro(cfg) as conn:
        val = _scalar(
            conn,
            "SELECT SUM(balance_after) FROM fm_ledger "
            "WHERE date=? AND entry_type='INIT'",
            (today,),
        )
        if val is not None and float(val) > 0:
            return float(val)
        row = conn.execute(
            "SELECT cash_floor, margin_used, margin_reserved "
            "FROM capital_snapshot WHERE id=1"
        ).fetchone()
    if row is None:
        return None
    total = float(row["cash_floor"]) + float(row["margin_used"]) + float(row["margin_reserved"])
    return total if total > 0 else None


def capital_usage(cfg: dict) -> dict:
    with _ro(cfg) as conn:
        row = conn.execute(
            "SELECT margin_used, margin_reserved, realized_pnl_today, cash_floor "
            "FROM capital_snapshot WHERE id=1"
        ).fetchone()
    if row is None:
        return {"margin_used": 0.0, "margin_reserved": 0.0,
                "realized_pnl_today": 0.0, "cash_floor": 0.0}
    return {k: float(row[k]) for k in
            ("margin_used", "margin_reserved", "realized_pnl_today", "cash_floor")}


def consecutive_loss_streak(cfg: dict) -> int:
    """Trailing run of net_pnl<0 over closed trades (exit_time DESC).

    Stops at the first net_pnl>=0; NULL net_pnl rows are skipped (unresolved).
    """
    states = _CLOSED_STATES
    with _ro(cfg) as conn:
        rows = conn.execute(
            f"SELECT net_pnl FROM trades WHERE status IN ({_in_clause(states)}) "
            "AND net_pnl IS NOT NULL AND exit_time IS NOT NULL "
            "ORDER BY exit_time DESC LIMIT 200",
            states,
        ).fetchall()
    streak = 0
    for r in rows:
        if float(r["net_pnl"]) < 0:
            streak += 1
        else:
            break
    return streak


# ─────────────────────────────────────────────────────────────────────────────
# Strategy panel (services/strategy_panel.py)
# ─────────────────────────────────────────────────────────────────────────────
def strategy_trade_stats(cfg: dict, today: str) -> dict:
    """Per-strategy today: trades, wins, losses, net_pnl, open_count.

    Keyed by strategy name. Wins/losses over closed trades with net_pnl set.
    """
    open_states = _OPEN_STATES
    with _ro(cfg) as conn:
        traded = conn.execute(
            "SELECT strategy, "
            "COUNT(*) AS trades, "
            "COALESCE(SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END),0) AS wins, "
            "COALESCE(SUM(CASE WHEN net_pnl<0 THEN 1 ELSE 0 END),0) AS losses, "
            "COALESCE(SUM(COALESCE(net_pnl,0)),0.0) AS net_pnl "
            "FROM trades WHERE created_at LIKE ? AND status NOT GLOB 'REJECTED*' "
            "GROUP BY strategy",
            (today + "%",),
        ).fetchall()
        open_rows = conn.execute(
            "SELECT strategy, COUNT(*) AS open_count FROM trades "
            f"WHERE status IN ({_in_clause(open_states)}) GROUP BY strategy",
            open_states,
        ).fetchall()
    out: dict = {}
    for r in traded:
        out[r["strategy"]] = {
            "trades": int(r["trades"]), "wins": int(r["wins"]),
            "losses": int(r["losses"]), "net_pnl": float(r["net_pnl"]),
            "open_count": 0,
        }
    for r in open_rows:
        out.setdefault(r["strategy"], {"trades": 0, "wins": 0, "losses": 0,
                                       "net_pnl": 0.0, "open_count": 0})
        out[r["strategy"]]["open_count"] = int(r["open_count"])
    return out


def strategy_signal_counts(cfg: dict, today: str) -> dict:
    """Per-strategy signal count today (signals that reached storage)."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT strategy, COUNT(*) AS n FROM signals "
            "WHERE received_at LIKE ? GROUP BY strategy",
            (today + "%",),
        ).fetchall()
    return {r["strategy"]: int(r["n"]) for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
# Config snapshot (readers/config_reader.py) + events feed (dashboard)
# ─────────────────────────────────────────────────────────────────────────────
def latest_config_snapshot(cfg: dict, today: str) -> Optional[dict]:
    with _ro(cfg) as conn:
        row = conn.execute(
            "SELECT snapshot_id, snapshot_date, snapshot_ts, account_id, mode, "
            "trade_type, config_hash, config_json FROM config_snapshots "
            "WHERE snapshot_date=? ORDER BY snapshot_ts DESC LIMIT 1",
            (today,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT snapshot_id, snapshot_date, snapshot_ts, account_id, mode, "
                "trade_type, config_hash, config_json FROM config_snapshots "
                "ORDER BY snapshot_ts DESC LIMIT 1"
            ).fetchone()
    return dict(row) if row is not None else None


def recent_events(cfg: dict, limit: int = 10) -> list:
    """Last N system_events for the dashboard feed (newest first)."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT timestamp, event_type, scenario, details FROM system_events "
            "ORDER BY timestamp DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# G2b-1 — Strategy Control Tower (services/strategy_tower.py)
# ─────────────────────────────────────────────────────────────────────────────

def _bucket_case_sql() -> str:
    """Signal-status → family bucket (docs/G2b1_strategy_attribution.md §0.3).

    expired    = REJECTED_EXPIRED / legacy EXPIRED (processor age gate)
    duplicated = DUPLICATE (post-insert race only; bulk dedup is pre-insert)
    rejected   = REJECTED*/DROPPED_*/SKIPPED_*/QUEUE_FULL/PLACEMENT_FAILED/TIMEOUT
    accepted   = everything else (QUEUED/PROCESSING/RESERVED/PROCESSED*/PASSED/
                 TRADED/ACCEPTED/GATE_*/RETEST_*)
    """
    return (
        "CASE "
        "WHEN status IN ('REJECTED_EXPIRED','EXPIRED') THEN 'expired' "
        "WHEN status = 'DUPLICATE' THEN 'duplicated' "
        "WHEN status GLOB 'REJECTED*' OR status GLOB 'DROPPED_*' "
        "  OR status GLOB 'SKIPPED_*' "
        "  OR status IN ('QUEUE_FULL','PLACEMENT_FAILED','TIMEOUT') THEN 'rejected' "
        "ELSE 'accepted' END"
    )


def strategy_signal_funnel(cfg: dict, today: str) -> dict:
    """Per-strategy stored-signal funnel today: {strategy: {accepted, rejected,
    duplicated, expired, stored, last_signal}}."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT strategy, " + _bucket_case_sql() + " AS bucket, COUNT(*) AS n, "
            "MAX(received_at) AS last_ts "
            "FROM signals WHERE received_at LIKE ? GROUP BY strategy, bucket",
            (today + "%",),
        ).fetchall()
    out: dict = {}
    for r in rows:
        s = out.setdefault(r["strategy"], {
            "accepted": 0, "rejected": 0, "duplicated": 0, "expired": 0,
            "stored": 0, "last_signal": None,
        })
        s[r["bucket"]] = int(r["n"])
        s["stored"] += int(r["n"])
        if s["last_signal"] is None or (r["last_ts"] and r["last_ts"] > s["last_signal"]):
            s["last_signal"] = r["last_ts"]
    return out


def webhook_by_scanner(cfg: dict, today: str) -> dict:
    """Per-scanner webhook_audit aggregates today (received = accepted+rejected)."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT scanner_name, "
            "COALESCE(SUM(signals_accepted),0) AS accepted, "
            "COALESCE(SUM(signals_rejected),0) AS rejected, "
            "COUNT(*) AS posts, MAX(ts) AS last_ts "
            "FROM webhook_audit WHERE date = ? GROUP BY scanner_name",
            (today,),
        ).fetchall()
    return {
        r["scanner_name"]: {
            "received": int(r["accepted"]) + int(r["rejected"]),
            "accepted": int(r["accepted"]), "rejected": int(r["rejected"]),
            "posts": int(r["posts"]), "last_ts": r["last_ts"],
        }
        for r in rows
    }


def strategy_order_stats(cfg: dict, today: str) -> dict:
    """Per-strategy order funnel today (ALL legs, joined via trades.strategy).

    Buckets (attribution doc R5): created=all rows; submitted=status<>'PENDING';
    filled=COMPLETE; rejected=FAILED; cancelled=CANCELLED.
    """
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT t.strategy AS strategy, "
            "COUNT(*) AS created, "
            "COALESCE(SUM(CASE WHEN o.status<>'PENDING' THEN 1 ELSE 0 END),0) AS submitted, "
            "COALESCE(SUM(CASE WHEN o.status='COMPLETE' THEN 1 ELSE 0 END),0) AS filled, "
            "COALESCE(SUM(CASE WHEN o.status='FAILED' THEN 1 ELSE 0 END),0) AS rejected, "
            "COALESCE(SUM(CASE WHEN o.status='CANCELLED' THEN 1 ELSE 0 END),0) AS cancelled, "
            "MAX(CASE WHEN o.status='FAILED' THEN o.placed_at END) AS last_failed_ts "
            "FROM orders o JOIN trades t ON t.trade_id = o.trade_id "
            "WHERE o.placed_at LIKE ? GROUP BY t.strategy",
            (today + "%",),
        ).fetchall()
    return {r["strategy"]: {k: (int(r[k]) if k != "last_failed_ts" else r[k])
                            for k in ("created", "submitted", "filled",
                                      "rejected", "cancelled", "last_failed_ts")}
            for r in rows}


def strategy_perf_stats(cfg: dict, today: str) -> dict:
    """Per-strategy trading+performance today (trades created today).

    ROI denominator (attribution doc R4): Σ margin_reserved over today's trades.
    """
    closed = _CLOSED_STATES
    opens = _OPEN_STATES
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT strategy, "
            "COUNT(*) AS trades, "
            "COALESCE(SUM(CASE WHEN status IN (" + _in_clause(opens) + ") THEN 1 ELSE 0 END),0) AS open_trades, "
            "COALESCE(SUM(CASE WHEN status IN (" + _in_clause(closed) + ") THEN 1 ELSE 0 END),0) AS closed_trades, "
            "COALESCE(SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END),0) AS wins, "
            "COALESCE(SUM(CASE WHEN net_pnl<0 THEN 1 ELSE 0 END),0) AS losses, "
            "COALESCE(SUM(COALESCE(net_pnl,0)),0.0) AS net_pnl, "
            "COALESCE(SUM(COALESCE(gross_pnl,0)),0.0) AS gross_pnl, "
            "COALESCE(SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END),0.0) AS win_sum, "
            "COALESCE(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END),0.0) AS loss_sum, "
            "MAX(net_pnl) AS best_trade, MIN(net_pnl) AS worst_trade, "
            "COALESCE(SUM(margin_reserved),0.0) AS capital_used_today, "
            "MAX(created_at) AS last_trade_ts, "
            "MAX(CASE WHEN net_pnl>0 THEN exit_time END) AS last_win_ts, "
            "MAX(CASE WHEN net_pnl<0 THEN exit_time END) AS last_loss_ts "
            "FROM trades WHERE created_at LIKE ? AND status NOT GLOB 'REJECTED*' "
            "GROUP BY strategy",
            (*opens, *closed, today + "%"),
        ).fetchall()
    out: dict = {}
    for r in rows:
        d = dict(r)
        for k in ("net_pnl", "gross_pnl", "win_sum", "loss_sum", "capital_used_today"):
            d[k] = float(d[k])
        out[r["strategy"]] = d
    return out


def strategy_open_capital(cfg: dict) -> dict:
    """Per-strategy margin_reserved over CURRENTLY-OPEN trades (not date-bound)."""
    states = _OPEN_STATES
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT strategy, COALESCE(SUM(margin_reserved),0.0) AS margin, "
            "COUNT(*) AS open_count FROM trades WHERE status IN (" + _in_clause(states) + ") "
            "GROUP BY strategy",
            states,
        ).fetchall()
    return {r["strategy"]: {"margin": float(r["margin"]), "open_count": int(r["open_count"])}
            for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
# G2b-1 — Trading modules M2-M5 (list queries; filtered, parameterized, capped)
# ─────────────────────────────────────────────────────────────────────────────

_LIST_CAP = 500


def list_signals(cfg: dict, today: str, scanner: Optional[str] = None,
                 strategy: Optional[str] = None, family: Optional[str] = None,
                 limit: int = _LIST_CAP) -> list:
    """Stored signals for a date, newest first, optional filters.

    family in {accepted, rejected, duplicated, expired} filters the derived
    bucket (attribution doc §0.3).
    """
    sql = (
        "SELECT signal_id, received_at, scanner, strategy, symbol, status, "
        "rejection_reason, fingerprint, " + _bucket_case_sql() + " AS family "
        "FROM signals WHERE received_at LIKE ?"
    )
    params: list = [today + "%"]
    if scanner:
        sql += " AND scanner = ?"
        params.append(scanner)
    if strategy:
        sql += " AND strategy = ?"
        params.append(strategy)
    if family:
        sql = "SELECT * FROM (" + sql + ") WHERE family = ?"
        params.append(family)
    sql += " ORDER BY received_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), _LIST_CAP)))
    with _ro(cfg) as conn:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def list_orders(cfg: dict, today: str, leg: Optional[str] = None,
                status: Optional[str] = None, strategy: Optional[str] = None,
                symbol: Optional[str] = None, limit: int = _LIST_CAP) -> list:
    """Orders for a date (placed_at), newest first, joined to trades for
    strategy/symbol; place→fill latency computed in SQL (ms)."""
    sql = (
        "SELECT o.order_id, o.trade_id, o.leg, o.status, o.qty_requested, "
        "o.qty_filled, o.avg_fill_price, o.placed_at, o.filled_at, "
        "o.rejection_reason, o.superseded_by, t.symbol AS symbol, "
        "t.strategy AS strategy, t.order_to_fill_ms AS entry_fill_latency_ms, "
        "CAST((julianday(o.filled_at) - julianday(o.placed_at)) * 86400000 AS INTEGER) "
        "  AS place_to_fill_ms "
        "FROM orders o LEFT JOIN trades t ON t.trade_id = o.trade_id "
        "WHERE o.placed_at LIKE ?"
    )
    params: list = [today + "%"]
    if leg:
        sql += " AND o.leg = ?"
        params.append(leg)
    if status:
        sql += " AND o.status = ?"
        params.append(status)
    if strategy:
        sql += " AND t.strategy = ?"
        params.append(strategy)
    if symbol:
        sql += " AND t.symbol = ?"
        params.append(symbol)
    sql += " ORDER BY o.placed_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), _LIST_CAP)))
    with _ro(cfg) as conn:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def open_positions_list(cfg: dict) -> list:
    """Open-set trades (M4). Open-set = _OPEN_STATES exactly (contract-tested).

    inning_no = MAX(innings.inning_number) for the trade (NULL → renders '—').
    """
    states = _OPEN_STATES
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT t.trade_id, t.symbol, t.strategy, t.direction, t.status, "
            "t.qty_filled, t.qty_planned, t.entry_actual_price, t.entry_target_price, "
            "t.sl_initial, t.tgt_initial, t.risk_amount, t.margin_reserved, "
            "t.created_at, t.entry_time, "
            "(SELECT MAX(i.inning_number) FROM innings i WHERE i.trade_id = t.trade_id) "
            "  AS inning_no "
            "FROM trades t WHERE t.status IN (" + _in_clause(states) + ") "
            "ORDER BY t.created_at DESC",
            states,
        ).fetchall()
    return [dict(r) for r in rows]


def holdings_list(cfg: dict) -> list:
    """gtt_state mirror rows (M5). Broker is authority; this is the local mirror."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT gtt_id, trade_id, status, exit_side, qty, sl_trigger, "
            "sl_limit, tgt_trigger, tgt_limit, needs_review "
            "FROM gtt_state ORDER BY gtt_id DESC LIMIT ?",
            (_LIST_CAP,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# G2b-1 — Capacity completion (DB-readable actuals for deferred guards)
# ─────────────────────────────────────────────────────────────────────────────

def exposure_extremes(cfg: dict) -> dict:
    """Worst per-symbol / per-sector open exposure ₹ + the largest single open
    position value (concentration / sector / position-value-cap rows)."""
    states = _OPEN_STATES
    value_expr = ("COALESCE(actual_position_value_rs, "
                  "qty_filled*COALESCE(entry_actual_price, entry_target_price))")
    with _ro(cfg) as conn:
        sym = conn.execute(
            "SELECT symbol, SUM(" + value_expr + ") AS v "
            "FROM trades WHERE status IN (" + _in_clause(states) + ") "
            "GROUP BY symbol ORDER BY v DESC LIMIT 1",
            states,
        ).fetchone()
        sec = conn.execute(
            "SELECT COALESCE(sector,'UNKNOWN') AS sector, SUM(" + value_expr + ") AS v "
            "FROM trades WHERE status IN (" + _in_clause(states) + ") "
            "GROUP BY sector ORDER BY v DESC LIMIT 1",
            states,
        ).fetchone()
        biggest = conn.execute(
            "SELECT MAX(" + value_expr + ") AS v "
            "FROM trades WHERE status IN (" + _in_clause(states) + ")",
            states,
        ).fetchone()
    return {
        "worst_symbol": (sym["symbol"], float(sym["v"] or 0.0)) if sym else (None, 0.0),
        "worst_sector": (sec["sector"], float(sec["v"] or 0.0)) if sec else (None, 0.0),
        "largest_position_value": float(biggest["v"]) if biggest and biggest["v"] else 0.0,
    }


def max_order_qty_today(cfg: dict, today: str) -> int:
    with _ro(cfg) as conn:
        val = _scalar(conn, "SELECT MAX(qty_requested) FROM orders WHERE placed_at LIKE ?",
                      (today + "%",))
    return int(val) if val is not None else 0


def entries_in_window(cfg: dict, since_iso: str) -> int:
    """ENTRY orders placed at/after an ISO timestamp (entry-burst actual)."""
    with _ro(cfg) as conn:
        return _count(conn,
                      "SELECT COUNT(*) FROM orders WHERE leg='ENTRY' AND placed_at >= ?",
                      (since_iso,))


def rate_limited_posts_today(cfg: dict, today: str) -> int:
    """webhook_audit 429 count today (per-IP limiter's DB-visible proxy; D7)."""
    with _ro(cfg) as conn:
        return _count(conn,
                      "SELECT COUNT(*) FROM webhook_audit WHERE date=? AND response_code=429",
                      (today,))


def signals_stored_count(cfg: dict, today: str) -> int:
    """Total signals rows stored today (the honest per-signal denominator)."""
    with _ro(cfg) as conn:
        return _count(conn, "SELECT COUNT(*) FROM signals WHERE received_at LIKE ?",
                      (today + "%",))
