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

# trades that are "currently open" (not date-filtered)
_OPEN_STATES = ("OPEN", "PARTIAL", "PENDING_FILL", "EXITING")
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
