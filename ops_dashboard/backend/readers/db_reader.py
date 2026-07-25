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
    """Day-opening TOTAL capital: the day's FIRST INIT ledger row, by timestamp.

    ⚠️ 25-Jul-2026: this was SUM(balance_after) over today's INIT rows, which
    DOUBLED on any day the app restarted. INIT is not unique per day —
    FundManager.initialize() writes one INIT row per PROCESS START (its H-4
    double-init guard is an in-memory per-process flag, capital/fund_manager.py
    :408-448), each with bucket='both' and the FULL broker balance. MEASURED: 10
    of 30 production INIT dates carry more than one row; on 2026-07-21 (the
    forced 11:57 restart) this returned 19,716.03 against a true opening of
    9,857.30, so every percentage resolved against it read half its real value.

    ORDER BY ts is safe: fm_ledger.ts is a uniform ISO-8601 IST string (single
    +05:30 offset, fixed width), so the TEXT sort is chronological — verified
    across all 58 production INIT rows, with zero days where the string sort
    differed from a datetime sort. Same rule as
    state_store.get_day_opening_capital(), so there is ONE definition of the
    day's opening capital rather than two that disagree on restart days.

    Percentage limits (daily-loss, intraday-bucket) resolve against total capital.
    Fallback: capital_snapshot (cash_floor + margin_used + margin_reserved).
    Returns None if neither source is available (pre-open) → callers render '—'.
    """
    with _ro(cfg) as conn:
        val = _scalar(
            conn,
            "SELECT balance_after FROM fm_ledger "
            "WHERE date=? AND entry_type='INIT' ORDER BY ts ASC LIMIT 1",
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
    """gtt_state mirror rows (M5) enriched with trade context (G5d: symbol/strategy/
    date/avg_price via LEFT JOIN trades). This is the SYSTEM/expected side only;
    the BROKER side + delta stay UNAVAILABLE (G-1/P1) — no reader invents them."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT g.gtt_id, g.trade_id, g.status, g.exit_side, g.qty, g.sl_trigger, "
            "g.sl_limit, g.tgt_trigger, g.tgt_limit, g.needs_review, "
            "t.symbol AS symbol, t.strategy AS strategy, t.created_at AS created_at, "
            "t.entry_actual_price AS avg_price "
            "FROM gtt_state g LEFT JOIN trades t ON t.trade_id = g.trade_id "
            "ORDER BY g.gtt_id DESC LIMIT ?",
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


# ─────────────────────────────────────────────────────────────────────────────
# G2b-2 — Strategy tower enhancements
# ─────────────────────────────────────────────────────────────────────────────

def strategy_reject_split(cfg: dict, today: str) -> dict:
    """Per-strategy risk-rejected vs capital-rejected counts today (failure strip)."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT strategy, "
            "COALESCE(SUM(CASE WHEN status IN (" + _in_clause(_RISK_REJECT_STATUSES) + ") THEN 1 ELSE 0 END),0) AS risk_rej, "
            "COALESCE(SUM(CASE WHEN status IN (" + _in_clause(_CAPITAL_REJECT_STATUSES) + ") THEN 1 ELSE 0 END),0) AS capital_rej "
            "FROM signals WHERE received_at LIKE ? GROUP BY strategy",
            (*_RISK_REJECT_STATUSES, *_CAPITAL_REJECT_STATUSES, today + "%"),
        ).fetchall()
    return {r["strategy"]: {"risk_rej": int(r["risk_rej"]), "capital_rej": int(r["capital_rej"])}
            for r in rows}


def strategy_loss_streaks(cfg: dict) -> dict:
    """Per-strategy trailing losing-close streak (rolling, like the global one).

    NULL net_pnl rows are skipped; a win/breakeven ends the streak.
    """
    states = _CLOSED_STATES
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT strategy, net_pnl FROM trades "
            "WHERE status IN (" + _in_clause(states) + ") AND net_pnl IS NOT NULL "
            "AND exit_time IS NOT NULL ORDER BY exit_time DESC LIMIT 500",
            states,
        ).fetchall()
    streaks: dict = {}
    done: set = set()
    for r in rows:
        s = r["strategy"]
        if s in done:
            continue
        if float(r["net_pnl"]) < 0:
            streaks[s] = streaks.get(s, 0) + 1
        else:
            done.add(s)
            streaks.setdefault(s, 0)
    return streaks


# ─────────────────────────────────────────────────────────────────────────────
# G2b-2 — M7 Risk / M8 Capital / M9 Exposure / M10 P&L
# ─────────────────────────────────────────────────────────────────────────────

def open_risk_amount_sum(cfg: dict) -> float:
    states = _OPEN_STATES
    with _ro(cfg) as conn:
        val = _scalar(conn,
                      "SELECT COALESCE(SUM(risk_amount),0.0) FROM trades "
                      "WHERE status IN (" + _in_clause(states) + ")", states)
    return round(float(val or 0.0), 2)


def ledger_entries(cfg: dict, today: str, limit: int = 500) -> list:
    """fm_ledger rows today, newest first (M8 append-only ledger view)."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT ledger_id, ts, entry_type, amount, bucket, balance_before, "
            "balance_after, reason, trade_id, margin_delta, pnl_delta, costs "
            "FROM fm_ledger WHERE date=? ORDER BY ledger_id DESC LIMIT ?",
            (today, max(1, min(int(limit), 500))),
        ).fetchall()
    return [dict(r) for r in rows]


def equity_curve_points(cfg: dict, today: str) -> list:
    """Cumulative realized P&L over today's RELEASE_USED rows, ts ASC (M10)."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT ts, pnl_delta FROM fm_ledger "
            "WHERE date=? AND entry_type='RELEASE_USED' ORDER BY ts ASC",
            (today,),
        ).fetchall()
    cum = 0.0
    out = []
    for r in rows:
        cum += float(r["pnl_delta"] or 0.0)
        out.append({"ts": r["ts"], "cum_pnl": round(cum, 2)})
    return out


def pnl_summary_today(cfg: dict, today: str) -> dict:
    """Realized totals from trades CLOSED today (net/gross/charges + splits)."""
    states = _CLOSED_STATES
    with _ro(cfg) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(net_pnl,0)),0.0) AS net, "
            "COALESCE(SUM(COALESCE(gross_pnl,0)),0.0) AS gross, "
            "COUNT(*) AS closed "
            "FROM trades WHERE status IN (" + _in_clause(states) + ") AND exit_time LIKE ?",
            (*states, today + "%"),
        ).fetchone()
        per_strat = conn.execute(
            "SELECT strategy, COALESCE(SUM(COALESCE(net_pnl,0)),0.0) AS net, "
            "COUNT(*) AS closed FROM trades "
            "WHERE status IN (" + _in_clause(states) + ") AND exit_time LIKE ? "
            "GROUP BY strategy ORDER BY net DESC",
            (*states, today + "%"),
        ).fetchall()
        per_dir = conn.execute(
            "SELECT direction, COALESCE(SUM(COALESCE(net_pnl,0)),0.0) AS net, "
            "COUNT(*) AS closed FROM trades "
            "WHERE status IN (" + _in_clause(states) + ") AND exit_time LIKE ? "
            "GROUP BY direction",
            (*states, today + "%"),
        ).fetchall()
    net, gross = float(row["net"]), float(row["gross"])
    return {
        "net": round(net, 2), "gross": round(gross, 2),
        "charges": round(gross - net, 2), "closed": int(row["closed"]),
        "per_strategy": [{"strategy": r["strategy"], "net": round(float(r["net"]), 2),
                          "closed": int(r["closed"])} for r in per_strat],
        "per_direction": [{"direction": r["direction"], "net": round(float(r["net"]), 2),
                           "closed": int(r["closed"])} for r in per_dir],
    }


def exposure_breakdown(cfg: dict, top_n: int = 10) -> dict:
    """Gross open exposure + per-strategy and per-symbol splits (M9)."""
    states = _OPEN_STATES
    value_expr = ("COALESCE(actual_position_value_rs, "
                  "qty_filled*COALESCE(entry_actual_price, entry_target_price))")
    with _ro(cfg) as conn:
        total = _scalar(conn,
                        "SELECT COALESCE(SUM(" + value_expr + "),0.0) FROM trades "
                        "WHERE status IN (" + _in_clause(states) + ")", states)
        per_strat = conn.execute(
            "SELECT strategy, SUM(" + value_expr + ") AS v, "
            "COALESCE(SUM(margin_reserved),0.0) AS margin, COUNT(*) AS n "
            "FROM trades WHERE status IN (" + _in_clause(states) + ") "
            "GROUP BY strategy ORDER BY v DESC", states,
        ).fetchall()
        per_symbol = conn.execute(
            "SELECT symbol, SUM(" + value_expr + ") AS v, COUNT(*) AS n "
            "FROM trades WHERE status IN (" + _in_clause(states) + ") "
            "GROUP BY symbol ORDER BY v DESC LIMIT ?",
            (*states, max(1, int(top_n))),
        ).fetchall()
    return {
        "gross_exposure": round(float(total or 0.0), 2),
        "per_strategy": [{"strategy": r["strategy"], "value": round(float(r["v"] or 0), 2),
                          "margin": round(float(r["margin"]), 2), "positions": int(r["n"])}
                         for r in per_strat],
        "per_symbol": [{"symbol": r["symbol"], "value": round(float(r["v"] or 0), 2),
                        "positions": int(r["n"])} for r in per_symbol],
    }


# ─────────────────────────────────────────────────────────────────────────────
# G2b-2 — M11 Services / M14 Audit / M15 Alerts
# ─────────────────────────────────────────────────────────────────────────────

def latest_heartbeats(cfg: dict, today: str) -> dict:
    """Latest cron_heartbeat per job today: {job: {executed_at, status}}."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT job_name, MAX(executed_at) AS executed_at, status "
            "FROM cron_heartbeat WHERE executed_at LIKE ? GROUP BY job_name",
            (today + "%",),
        ).fetchall()
    return {r["job_name"]: {"executed_at": r["executed_at"], "status": r["status"]}
            for r in rows}


def control_tower_open_findings(cfg: dict, limit: int = 100) -> list:
    with _ro(cfg) as conn:
        try:
            rows = conn.execute(
                "SELECT scan_time, category, severity, resource_name, reason, status "
                "FROM control_tower_findings WHERE status='OPEN' "
                "ORDER BY scan_time DESC LIMIT ?", (int(limit),),
            ).fetchall()
        except sqlite3.OperationalError:
            return []   # table absent on older DBs — honest empty
    return [dict(r) for r in rows]


def audit_feed(cfg: dict, today: str, table: Optional[str] = None,
               severity: Optional[str] = None, limit: int = 200) -> list:
    """Unified audit feed (M14): normalized {ts, source, severity, summary}
    over 6 sources, merged newest-first in Python (single pass per table)."""
    limit = max(1, min(int(limit), 500))
    rows: list = []

    def _safe(fn):
        try:
            return fn()
        except sqlite3.OperationalError:
            return []

    with _ro(cfg) as conn:
        if table in (None, "system_events"):
            rows += [{"ts": r["timestamp"], "source": "system_events",
                      "severity": "INFO",
                      "summary": r["event_type"] + (f" [{r['scenario']}]" if r["scenario"] else "")}
                     for r in _safe(lambda: conn.execute(
                         "SELECT timestamp, event_type, scenario FROM system_events "
                         "WHERE timestamp LIKE ? ORDER BY timestamp DESC LIMIT ?",
                         (today + "%", limit)).fetchall())]
        if table in (None, "reconciliation_log"):
            sev_map = {"COSMETIC": "INFO", "RECOVERABLE": "WARN", "UNRECOVERABLE": "CRITICAL"}
            rows += [{"ts": r["ts"], "source": "reconciliation_log",
                      "severity": sev_map.get(r["tier"], "WARN"),
                      "summary": f"{r['check_name']} {r['symbol']}: {r['action_taken']}"
                                 + ("" if r["success"] else " (FAILED)")}
                     for r in _safe(lambda: conn.execute(
                         "SELECT ts, check_name, tier, symbol, action_taken, success "
                         "FROM reconciliation_log WHERE ts LIKE ? "
                         "ORDER BY ts DESC LIMIT ?", (today + "%", limit)).fetchall())]
        if table in (None, "webhook_audit"):
            rows += [{"ts": r["ts"], "source": "webhook_audit",
                      "severity": "WARN" if int(r["response_code"]) >= 400 else "INFO",
                      "summary": f"POST /webhook/{r['scanner_name']} -> {r['response_code']} "
                                 f"(+{r['signals_accepted']}/-{r['signals_rejected']})"}
                     for r in _safe(lambda: conn.execute(
                         "SELECT ts, scanner_name, response_code, signals_accepted, "
                         "signals_rejected FROM webhook_audit WHERE date=? "
                         "ORDER BY ts DESC LIMIT ?", (today, limit)).fetchall())]
        if table in (None, "eod_verification"):
            rows += [{"ts": r["verified_at"], "source": "eod_verification",
                      "severity": "INFO" if r["status"] == "VERIFIED" else "CRITICAL",
                      "summary": f"EOD {r['status']} (open={r['open_trades']}, "
                                 f"pending={r['pending_orders']})"}
                     for r in _safe(lambda: conn.execute(
                         "SELECT verified_at, status, open_trades, pending_orders "
                         "FROM eod_verification WHERE date=? LIMIT ?",
                         (today, limit)).fetchall())]
        if table in (None, "preflight"):
            rows += [{"ts": r["completed_at"] or r["started_at"], "source": "preflight",
                      "severity": "INFO" if r["overall_status"] == "READY" else
                                  ("WARN" if r["overall_status"] == "READY_WITH_WARNINGS" else "CRITICAL"),
                      "summary": f"Preflight {r['phase']}: {r['overall_status']} "
                                 f"({r['passed']}/{r['total_checks']} passed)"}
                     for r in _safe(lambda: conn.execute(
                         "SELECT phase, started_at, completed_at, total_checks, passed, "
                         "overall_status FROM preflight_runs WHERE run_date=? "
                         "ORDER BY started_at DESC LIMIT ?", (today, limit)).fetchall())]
        if table in (None, "control_tower"):
            rows += [{"ts": r["scan_time"], "source": "control_tower",
                      "severity": r["severity"],
                      "summary": f"[{r['category']}] {r['resource_name'] or ''}: {r['reason']}"}
                     for r in _safe(lambda: conn.execute(
                         "SELECT scan_time, category, severity, resource_name, reason "
                         "FROM control_tower_findings WHERE scan_time LIKE ? "
                         "ORDER BY scan_time DESC LIMIT ?", (today + "%", limit)).fetchall())]

    if severity:
        rows = [r for r in rows if (r["severity"] or "").upper() == severity.upper()]
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    return rows[:limit]


def telegram_alerts_today(cfg: dict, today: str, limit: int = 200) -> list:
    with _ro(cfg) as conn:
        try:
            rows = conn.execute(
                "SELECT sent_at, severity, title, status, attempts, source_module "
                "FROM telegram_alerts WHERE sent_at LIKE ? "
                "ORDER BY sent_at DESC LIMIT ?", (today + "%", int(limit)),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# G2b-2 — M16 Slippage / M17 Execution / M18 Statistics / M20 Config history
# ─────────────────────────────────────────────────────────────────────────────

def slippage_rows_today(cfg: dict, today: str, limit: int = 500) -> list:
    with _ro(cfg) as conn:
        try:
            rows = conn.execute(
                "SELECT trade_id, symbol, strategy_name, side, qty, price_band, "
                "entry_signal_price, entry_fill_price, entry_slippage_rs, "
                "sl_slippage_rs, tgt_slippage_rs, planned_sl_distance, "
                "planned_rr, actual_rr, rr_damage_pct, trade_result, exit_reason "
                "FROM trade_slippage_log WHERE trade_date=? "
                "ORDER BY id DESC LIMIT ?", (today, int(limit)),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


def execution_log_today(cfg: dict, today: str, limit: int = 500) -> list:
    with _ro(cfg) as conn:
        try:
            rows = conn.execute(
                "SELECT order_id, parent_trade_id, symbol, strategy_name, leg, side, "
                "intended_price, actual_price, slippage_rs, qty, filled_qty, "
                "is_partial, retry_count, status, order_timestamp, fill_timestamp "
                "FROM order_execution_log WHERE order_timestamp LIKE ? "
                "ORDER BY id DESC LIMIT ?", (today + "%", int(limit)),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


def latency_rows_today(cfg: dict, today: str) -> list:
    """Per-trade latency triple for bucket histograms (M17)."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT signal_to_order_ms, order_to_fill_ms, total_latency_ms "
            "FROM trades WHERE created_at LIKE ? AND status NOT GLOB 'REJECTED*'",
            (today + "%",),
        ).fetchall()
    return [dict(r) for r in rows]


def statistics_bundle(cfg: dict, today: str) -> dict:
    """M18: closed-trade stats + MFE/MAE join + LONG/SHORT split (today)."""
    states = _CLOSED_STATES
    with _ro(cfg) as conn:
        per_dir = conn.execute(
            "SELECT direction, COUNT(*) AS closed, "
            "COALESCE(SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END),0) AS wins, "
            "COALESCE(SUM(CASE WHEN net_pnl<0 THEN 1 ELSE 0 END),0) AS losses, "
            "COALESCE(SUM(COALESCE(net_pnl,0)),0.0) AS net, "
            "COALESCE(SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END),0.0) AS win_sum, "
            "COALESCE(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END),0.0) AS loss_sum, "
            "COALESCE(AVG(CASE WHEN risk_amount>0 THEN net_pnl/risk_amount END),0.0) AS avg_r "
            "FROM trades WHERE status IN (" + _in_clause(states) + ") AND exit_time LIKE ? "
            "GROUP BY direction",
            (*states, today + "%"),
        ).fetchall()
        exc = conn.execute(
            "SELECT t.trade_id, t.symbol, t.strategy, t.direction, t.net_pnl, "
            "e.mfe_pct, e.mae_pct "
            "FROM trades t LEFT JOIN trade_excursions e ON e.trade_id=t.trade_id "
            "WHERE t.status IN (" + _in_clause(states) + ") AND t.exit_time LIKE ? "
            "ORDER BY t.exit_time DESC LIMIT 200",
            (*states, today + "%"),
        ).fetchall()
        inn = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(is_real),0) AS real_n FROM innings"
        ).fetchone()
    return {
        "per_direction": [dict(r) for r in per_dir],
        "trades_with_excursions": [dict(r) for r in exc],
        "innings": {"total": int(inn["n"]), "real": int(inn["real_n"])},
    }


def config_snapshot_history(cfg: dict, limit: int = 30) -> list:
    """Recent snapshots (date, ts, hash) newest-first for drift/last-change (M20)."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT snapshot_date, snapshot_ts, config_hash FROM config_snapshots "
            "ORDER BY snapshot_ts DESC LIMIT ?", (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def config_snapshot_for_date(cfg: dict, date_iso: str) -> Optional[dict]:
    with _ro(cfg) as conn:
        row = conn.execute(
            "SELECT snapshot_date, snapshot_ts, config_hash, config_json "
            "FROM config_snapshots WHERE snapshot_date=? "
            "ORDER BY snapshot_ts DESC LIMIT 1", (date_iso,),
        ).fetchone()
    return dict(row) if row else None


def system_metrics_disk_history(cfg: dict, limit: int = 60) -> list:
    """Historical disk_used_pct from analytics.system_metrics (M12).

    cpu/mem columns are -1.0 sentinels on the VM (psutil absent) — only disk is
    real; callers state that gap, never chart the sentinels.
    """
    with _ro(cfg) as conn:
        try:
            rows = conn.execute(
                "SELECT ts, disk_used_pct FROM system_metrics "
                "ORDER BY id DESC LIMIT ?", (int(limit),),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in reversed(rows)]


def closed_trades_today(cfg: dict, today: str, limit: int = 200) -> list:
    """Closed trades today for the P&L screen (B8/A9) — display columns only."""
    states = _CLOSED_STATES
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT trade_id, symbol, strategy, direction, qty_filled, "
            "entry_actual_price, exit_price, exit_time, exit_reason, "
            "COALESCE(charges, COALESCE(gross_pnl,0)-COALESCE(net_pnl,0)) AS charges, "
            "net_pnl FROM trades "
            "WHERE status IN (" + _in_clause(states) + ") AND exit_time LIKE ? "
            "ORDER BY exit_time DESC LIMIT ?",
            (*states, today + "%", int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# G5b — additive read-only helpers. All map to EXISTING tables (NO schema change):
# scanner→trade join · per-strategy SL/TGT hits · long/short exposure split ·
# profit factor · slippage through-day trend. Read-only (mode=ro) like every
# reader above; short-lived connection; parameterized.
# ─────────────────────────────────────────────────────────────────────────────

def scanner_for_trades(cfg: dict, trade_ids) -> dict:
    """{trade_id: scanner} via ``trades.signal_id → signals.scanner`` — the exact
    attribution path (Phase A §3.1; mutual FK schema.sql:105-114). Trades whose
    signal row is absent are omitted (honest — no fabricated scanner). SHARED with
    the G5c Scanner-Attribution screen."""
    ids = tuple(dict.fromkeys(t for t in (trade_ids or []) if t))
    if not ids:
        return {}
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT t.trade_id AS trade_id, s.scanner AS scanner "
            "FROM trades t JOIN signals s ON s.signal_id = t.signal_id "
            "WHERE t.trade_id IN (" + _in_clause(ids) + ")",
            ids,
        ).fetchall()
    return {r["trade_id"]: r["scanner"] for r in rows}


def strategy_sltgt_hits(cfg: dict, today: str) -> dict:
    """Per-strategy SL_HIT / TGT_HIT exit counts over trades closed today."""
    states = _CLOSED_STATES
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT strategy, "
            "COALESCE(SUM(CASE WHEN exit_reason='SL_HIT' THEN 1 ELSE 0 END),0) AS sl_hits, "
            "COALESCE(SUM(CASE WHEN exit_reason='TGT_HIT' THEN 1 ELSE 0 END),0) AS tgt_hits "
            "FROM trades WHERE status IN (" + _in_clause(states) + ") AND exit_time LIKE ? "
            "GROUP BY strategy",
            (*states, today + "%"),
        ).fetchall()
    return {r["strategy"]: {"sl_hits": int(r["sl_hits"]), "tgt_hits": int(r["tgt_hits"])}
            for r in rows}


def exposure_by_direction(cfg: dict) -> dict:
    """Long / Short / Net open exposure (₹) + position counts (Capital & Risk)."""
    states = _OPEN_STATES
    value_expr = ("COALESCE(actual_position_value_rs, "
                  "qty_filled*COALESCE(entry_actual_price, entry_target_price))")
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT direction, COALESCE(SUM(" + value_expr + "),0.0) AS v, COUNT(*) AS n "
            "FROM trades WHERE status IN (" + _in_clause(states) + ") GROUP BY direction",
            states,
        ).fetchall()
    by = {r["direction"]: (float(r["v"] or 0.0), int(r["n"])) for r in rows}
    long_v, long_n = by.get("LONG", (0.0, 0))
    short_v, short_n = by.get("SHORT", (0.0, 0))
    return {
        "long_value": round(long_v, 2), "short_value": round(short_v, 2),
        "net_value": round(long_v - short_v, 2),
        "long_positions": long_n, "short_positions": short_n,
    }


def profit_factor_today(cfg: dict, today: str) -> Optional[float]:
    """Σ winning net_pnl / Σ|losing net_pnl| over trades closed today. None when
    there are no losses (undefined) → callers render '—'."""
    states = _CLOSED_STATES
    with _ro(cfg) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END),0.0) AS wins, "
            "COALESCE(SUM(CASE WHEN net_pnl<0 THEN -net_pnl ELSE 0 END),0.0) AS losses "
            "FROM trades WHERE status IN (" + _in_clause(states) + ") AND exit_time LIKE ?",
            (*states, today + "%"),
        ).fetchone()
    wins, losses = float(row["wins"]), float(row["losses"])
    return round(wins / losses, 2) if losses > 0 else None


def slippage_trend_today(cfg: dict, today: str) -> list:
    """Through-day avg entry slippage bucketed by entry hour (join to trades for
    entry_time). Honest empty when the join yields no timestamps."""
    with _ro(cfg) as conn:
        try:
            rows = conn.execute(
                "SELECT substr(t.entry_time,12,2) AS hh, "
                "AVG(sl.entry_slippage_rs) AS avg_slip, COUNT(*) AS n "
                "FROM trade_slippage_log sl JOIN trades t ON t.trade_id = sl.trade_id "
                "WHERE sl.trade_date=? AND t.entry_time IS NOT NULL "
                "GROUP BY hh ORDER BY hh",
                (today,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [{"hour": r["hh"], "avg_slippage": round(float(r["avg_slip"] or 0.0), 4),
             "n": int(r["n"])} for r in rows if r["hh"]]


# ─────────────────────────────────────────────────────────────────────────────
# G5c — multi-period readers (date-range scoped) + trade-story + System Score.
# All read-only; map to EXISTING tables; NO schema change. `from_date`/`to_date`
# are YYYY-MM-DD (freshness.resolve_period); the range is INCLUSIVE on the day.
# The existing today-scoped readers are UNTOUCHED — these are new functions.
# ─────────────────────────────────────────────────────────────────────────────

def closed_trades_range(cfg: dict, from_date: str, to_date: str, limit: int = 2000) -> list:
    """Closed trades whose EXIT DAY falls in [from_date, to_date] — the spine for
    Strategy Ranking + P&L Analytics. Scanner is joined in the service via
    scanner_for_trades (signal→scanner)."""
    states = _CLOSED_STATES
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT trade_id, signal_id, strategy, direction, symbol, "
            "qty_filled, entry_actual_price, entry_target_price, exit_price, "
            "entry_time, exit_time, created_at, exit_reason, "
            "COALESCE(gross_pnl,0) AS gross_pnl, COALESCE(net_pnl,0) AS net_pnl, "
            "COALESCE(charges, COALESCE(gross_pnl,0)-COALESCE(net_pnl,0)) AS charges, "
            "COALESCE(margin_reserved,0) AS margin_reserved, COALESCE(risk_amount,0) AS risk_amount "
            "FROM trades WHERE status IN (" + _in_clause(states) + ") "
            "AND substr(exit_time,1,10) BETWEEN ? AND ? "
            "ORDER BY exit_time DESC LIMIT ?",
            (*states, from_date, to_date, int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def trades_in_range(cfg: dict, from_date: str, to_date: str, strategy: Optional[str] = None,
                    direction: Optional[str] = None, symbol: Optional[str] = None,
                    limit: int = 500) -> list:
    """All trades CREATED in [from_date, to_date] (open + closed) for the Trade
    Explorer table. Scanner + System Score joined in the service."""
    sql = (
        "SELECT trade_id, signal_id, strategy, direction, symbol, status, "
        "qty_planned, qty_filled, entry_actual_price, entry_target_price, "
        "sl_initial, tgt_initial, exit_price, entry_time, exit_time, created_at, "
        "exit_reason, gross_pnl, net_pnl, "
        "COALESCE(charges, COALESCE(gross_pnl,0)-COALESCE(net_pnl,0)) AS charges, "
        "risk_amount, margin_reserved "
        "FROM trades WHERE substr(created_at,1,10) BETWEEN ? AND ?"
    )
    params: list = [from_date, to_date]
    if strategy:
        sql += " AND strategy = ?"; params.append(strategy)
    if direction:
        sql += " AND direction = ?"; params.append(direction)
    if symbol:
        sql += " AND symbol = ?"; params.append(symbol)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), _LIST_CAP)))
    with _ro(cfg) as conn:
        return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def signals_scanner_funnel_range(cfg: dict, from_date: str, to_date: str) -> dict:
    """Per-scanner stored-signal funnel over [from_date, to_date] (received day):
    {scanner: {accepted, rejected, duplicated, expired, stored, last_signal}}."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT scanner, " + _bucket_case_sql() + " AS bucket, COUNT(*) AS n, "
            "MAX(received_at) AS last_ts FROM signals "
            "WHERE substr(received_at,1,10) BETWEEN ? AND ? GROUP BY scanner, bucket",
            (from_date, to_date),
        ).fetchall()
    out: dict = {}
    for r in rows:
        s = out.setdefault(r["scanner"], {"accepted": 0, "rejected": 0, "duplicated": 0,
                                          "expired": 0, "stored": 0, "last_signal": None})
        s[r["bucket"]] = int(r["n"])
        s["stored"] += int(r["n"])
        if s["last_signal"] is None or (r["last_ts"] and r["last_ts"] > s["last_signal"]):
            s["last_signal"] = r["last_ts"]
    return out


def strategy_signal_counts_range(cfg: dict, from_date: str, to_date: str) -> dict:
    """Per-strategy stored-signal count over a range (Strategy Health trend)."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT strategy, COUNT(*) AS n FROM signals "
            "WHERE substr(received_at,1,10) BETWEEN ? AND ? GROUP BY strategy",
            (from_date, to_date),
        ).fetchall()
    return {r["strategy"]: int(r["n"]) for r in rows}


def screener_scores(cfg: dict, signal_ids) -> dict:
    """{signal_id: System Score} from screener_results.score (L8 single score).
    Graceful empty when the table/rows are absent (honest — no fabricated score)."""
    ids = tuple(dict.fromkeys(s for s in (signal_ids or []) if s))
    if not ids:
        return {}
    with _ro(cfg) as conn:
        try:
            rows = conn.execute(
                "SELECT signal_id, MAX(score) AS score FROM screener_results "
                "WHERE signal_id IN (" + _in_clause(ids) + ") GROUP BY signal_id",
                ids,
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
    return {r["signal_id"]: (int(r["score"]) if r["score"] is not None else None) for r in rows}


def trade_story_parts(cfg: dict, trade_id: str) -> dict:
    """Single-trade assembly for the Trade Explorer / Trade Logs lifecycle:
    the trade + its signal (scanner/score/payload) + orders + execution rows.
    Per-stage validation/risk/capital timings are NOT persisted (G-2) — the
    caller renders those stages as an honest 'not captured', never invented."""
    with _ro(cfg) as conn:
        trow = conn.execute(
            "SELECT trade_id, signal_id, strategy, direction, symbol, status, "
            "qty_planned, qty_filled, entry_target_price, entry_actual_price, "
            "sl_initial, tgt_initial, exit_price, entry_time, exit_time, created_at, "
            "exit_reason, gross_pnl, net_pnl, risk_amount, margin_reserved "
            "FROM trades WHERE trade_id = ?", (trade_id,)).fetchone()
        if trow is None:
            return {}
        trade = dict(trow)
        srow = conn.execute(
            "SELECT signal_id, scanner, strategy, symbol, received_at, triggered_at, "
            "status, rejection_reason, webhook_payload FROM signals WHERE signal_id = ?",
            (trade["signal_id"],)).fetchone()
        orders = conn.execute(
            "SELECT order_id, leg, status, transaction_type, order_type, qty_requested, "
            "qty_filled, avg_fill_price, placed_at, filled_at, rejection_reason "
            "FROM orders WHERE trade_id = ? ORDER BY placed_at", (trade_id,)).fetchall()
        try:
            execs = conn.execute(
                "SELECT order_id, leg, side, intended_price, actual_price, slippage_rs, "
                "order_timestamp, fill_timestamp, exchange_timestamp "
                "FROM order_execution_log WHERE parent_trade_id = ? ORDER BY id", (trade_id,)).fetchall()
        except sqlite3.OperationalError:
            execs = []
    return {
        "trade": trade,
        "signal": dict(srow) if srow else None,
        "orders": [dict(o) for o in orders],
        "execution": [dict(e) for e in execs],
    }


def webhook_by_scanner_range(cfg: dict, from_date: str, to_date: str) -> dict:
    """Per-scanner webhook_audit aggregates over [from_date, to_date] (received =
    accepted+rejected). Range variant of webhook_by_scanner (Scanner Attribution)."""
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT scanner_name, COALESCE(SUM(signals_accepted),0) AS accepted, "
            "COALESCE(SUM(signals_rejected),0) AS rejected, COUNT(*) AS posts, "
            "MAX(ts) AS last_ts FROM webhook_audit WHERE date BETWEEN ? AND ? "
            "GROUP BY scanner_name",
            (from_date, to_date),
        ).fetchall()
    return {r["scanner_name"]: {"received": int(r["accepted"]) + int(r["rejected"]),
                                "accepted": int(r["accepted"]), "rejected": int(r["rejected"]),
                                "posts": int(r["posts"]), "last_ts": r["last_ts"]}
            for r in rows}


def recon_actions_for_trades(cfg: dict, trade_ids) -> dict:
    """{trade_id: [{ts, check, action, success}]} from reconciliation_log (G5d
    Trade Logs — what the system did to each trade). Read-only, existing table."""
    ids = tuple(dict.fromkeys(t for t in (trade_ids or []) if t))
    if not ids:
        return {}
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT trade_id, ts, check_name, action_taken, success FROM reconciliation_log "
            "WHERE trade_id IN (" + _in_clause(ids) + ") ORDER BY ts",
            ids,
        ).fetchall()
    out: dict = {}
    for r in rows:
        out.setdefault(r["trade_id"], []).append(
            {"ts": r["ts"], "check": r["check_name"], "action": r["action_taken"],
             "success": bool(r["success"])})
    return out
