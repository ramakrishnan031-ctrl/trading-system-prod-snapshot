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
# 25-Jul-2026: the 3-balance split, per core/schema.sql's own column comments —
# margin_used is "over open positions", margin_reserved is "not yet filled".
_OPEN_POSITION_STATES = ("OPEN", "PARTIAL", "EXITING")
_PENDING_STATES = ("PENDING_FILL",)

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
    Returns None if there is no INIT row yet (pre-open) → callers render '—'.

    25-Jul-2026: the `capital_snapshot` fallback was removed. That table has 0 rows
    in production — nothing has written it since the 3-balance model was retired —
    so the fallback could only ever return None. It was dead code that made the
    reader look like it had two sources when it had one.
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
    return None


def capital_usage(cfg: dict, today: str) -> dict:
    """The four capital balances, sourced where each one actually lives.

    25-Jul-2026: was a single read of `capital_snapshot`, a table with 0 rows in
    production — so this returned all-zeros on every call and every consumer
    rendered 0 as if it were measured. Each value now comes from its real source,
    keeping schema.sql's own definitions of the 3-balance model:

      margin_used       margin on OPEN positions      ("over open positions")
      margin_reserved   margin on PENDING orders      ("not yet filled")
      realized_pnl_today  Σ fm_ledger.pnl_delta on RELEASE_USED — the E4/W10
                        contract: pnl_delta is ALREADY NET, costs are persisted
                        alongside for observability and must NEVER be subtracted
                        again (mirrors state_store.get_daily_realized_net_pnl,
                        which this process cannot call — opening a StateStore
                        would trigger migration-on-open, reserved for main.py).
      cash_floor        total capital − margin deployed (the free-cash residual)

    `today` is now required: three of the four values are date-scoped, which the
    snapshot row hid by carrying no date at all.
    """
    total = opening_capital(cfg, today)          # first INIT row — restart-safe
    with _ro(cfg) as conn:
        used = float(_scalar(
            conn,
            "SELECT COALESCE(SUM(margin_reserved),0.0) FROM trades "
            "WHERE status IN (" + _in_clause(_OPEN_POSITION_STATES) + ")",
            _OPEN_POSITION_STATES) or 0.0)
        pending = float(_scalar(
            conn,
            "SELECT COALESCE(SUM(margin_reserved),0.0) FROM trades "
            "WHERE status IN (" + _in_clause(_PENDING_STATES) + ")",
            _PENDING_STATES) or 0.0)
        realized = float(_scalar(
            conn,
            "SELECT COALESCE(SUM(pnl_delta),0.0) FROM fm_ledger "
            "WHERE date=? AND entry_type='RELEASE_USED'",
            (today,)) or 0.0)
    return {
        "margin_used": used,
        "margin_reserved": pending,
        "realized_pnl_today": realized,
        # None total → 0.0 rather than a negative floor invented from no capital.
        "cash_floor": (float(total) - used) if total is not None else 0.0,
    }


# ── Screen-08 (Capital & Risk) — segment split ────────────────────────────────
# Product lives on `orders` (leg='ENTRY'), NEVER on trades: there is no
# trades.product column. This mirrors capital/state_store.py's own
# _NOT_DELIVERY_SQL so the GUI partitions trades exactly as the engine does.
_DELIVERY_ENTRY_SQL = (
    "EXISTS (SELECT 1 FROM orders o WHERE o.trade_id = t.trade_id "
    "AND o.leg = 'ENTRY' AND o.product = 'CNC')"
)
_NOT_DELIVERY_ENTRY_SQL = (
    "NOT EXISTS (SELECT 1 FROM orders o WHERE o.trade_id = t.trade_id "
    "AND o.leg = 'ENTRY' AND o.product = 'CNC')"
)


def current_total_capital(cfg: dict, today: str) -> Optional[float]:
    """Engine-truth TOTAL REAL capital as it stands now — the LATEST INIT or SYNC
    row for bucket='both', not the day's first.

    ⚠️ Deliberately different from opening_capital(), which is the day's FIRST INIT
    and is the correct base for "Opening Cash". This one moves if the engine ever
    re-syncs mid-day; measured 14-Aug-2026 the engine writes exactly ONE SYNC per
    day (09:15), so on that day the two are equal and the screen says so.
    """
    with _ro(cfg) as conn:
        row = conn.execute(
            "SELECT balance_after FROM fm_ledger "
            "WHERE date(ts) = ? AND entry_type IN ('INIT','SYNC') AND bucket = 'both' "
            "ORDER BY ts DESC, ledger_id DESC LIMIT 1",
            (today,),
        ).fetchone()
    return float(row["balance_after"]) if row else None


def capital_by_segment(cfg: dict, today: str) -> dict:
    """Real capital committed, split INTRADAY (MIS) vs DELIVERY (CNC).

    Operand is trades.margin_reserved — IDENTICAL to capital_usage(). The engine
    writes it as qty * price / leverage (capital/fund_manager.py required_margin:
    "Compute required margin = qty * price / leverage. NOT notional"). VERIFIED
    14-Aug-2026 against a live row to 6 dp: CAMLINFINE qty 2 @ 103.48338 / 5x
    = 41.393352, stored 41.393352.

    ⇒ This adds ONLY the bucket split. It does NOT re-derive the number from
    notional, so no second reservation formula is introduced.

    NOTE the 5% SL-M buffer (capital/fund_manager.py:561-563,
    slm_margin_buffer_pct 0.05) is held in fm_ledger's BUCKET availability and
    released once the SL-M is accepted. It is NOT part of trades.margin_reserved,
    so the figures here are the settled, un-buffered real-capital commitment.
    """
    out = {}
    with _ro(cfg) as conn:
        for key, pred in (("intraday", _NOT_DELIVERY_ENTRY_SQL),
                          ("delivery", _DELIVERY_ENTRY_SQL)):
            used = float(_scalar(
                conn,
                "SELECT COALESCE(SUM(t.margin_reserved),0.0) FROM trades t "
                "WHERE t.status IN (" + _in_clause(_OPEN_POSITION_STATES) + ") "
                "AND " + pred,
                _OPEN_POSITION_STATES) or 0.0)
            pending = float(_scalar(
                conn,
                "SELECT COALESCE(SUM(t.margin_reserved),0.0) FROM trades t "
                "WHERE t.status IN (" + _in_clause(_PENDING_STATES) + ") "
                "AND " + pred,
                _PENDING_STATES) or 0.0)
            out[key] = {"real_used": round(used, 2),
                        "real_reserved": round(pending, 2),
                        "real_committed": round(used + pending, 2)}
    return out


def strategy_capital_by_segment(cfg: dict, today: str) -> list:
    """Per-strategy real capital committed, split by segment. Same operand as
    capital_by_segment(); ordered by total committed, descending.
    """
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT t.strategy AS strategy, "
            f"COALESCE(SUM(CASE WHEN {_NOT_DELIVERY_ENTRY_SQL} "
            "  THEN t.margin_reserved ELSE 0 END),0.0) AS mis_real, "
            f"COALESCE(SUM(CASE WHEN {_DELIVERY_ENTRY_SQL} "
            "  THEN t.margin_reserved ELSE 0 END),0.0) AS gtt_real "
            "FROM trades t "
            "WHERE t.status IN (" + _in_clause(
                _OPEN_POSITION_STATES + _PENDING_STATES) + ") "
            "GROUP BY t.strategy ORDER BY (mis_real + gtt_real) DESC",
            _OPEN_POSITION_STATES + _PENDING_STATES,
        ).fetchall()
    return [{"strategy": r["strategy"],
             "mis_real": round(float(r["mis_real"]), 2),
             "gtt_real": round(float(r["gtt_real"]), 2),
             "total_real": round(float(r["mis_real"]) + float(r["gtt_real"]), 2)}
            for r in rows]


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

def _bucket_case_sql(prefix: str = "") -> str:
    """Signal-status → family bucket (docs/G2b1_strategy_attribution.md §0.3).

    prefix qualifies the column when the query joins another table that also has
    a `status` (e.g. "s." when signals is aliased alongside trades). Default ""
    keeps every existing single-table caller byte-identical.

    expired    = REJECTED_EXPIRED / legacy EXPIRED (processor age gate)
    duplicated = DUPLICATE (post-insert race only; bulk dedup is pre-insert)
    rejected   = REJECTED*/DROPPED_*/SKIPPED_*/QUEUE_FULL/PLACEMENT_FAILED/TIMEOUT
    accepted   = everything else (QUEUED/PROCESSING/RESERVED/PROCESSED*/PASSED/
                 TRADED/ACCEPTED/GATE_*/RETEST_*)
    """
    c = prefix + "status"
    return (
        "CASE "
        "WHEN " + c + " IN ('REJECTED_EXPIRED','EXPIRED') THEN 'expired' "
        "WHEN " + c + " = 'DUPLICATE' THEN 'duplicated' "
        "WHEN " + c + " GLOB 'REJECTED*' OR " + c + " GLOB 'DROPPED_*' "
        "  OR " + c + " GLOB 'SKIPPED_*' "
        "  OR " + c + " IN ('QUEUE_FULL','PLACEMENT_FAILED','TIMEOUT') THEN 'rejected' "
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


def list_signals_cap() -> int:
    """The row cap the list endpoints apply. Surfaced so a screen can tell the
    operator its table is truncated instead of quietly showing a partial day."""
    return _LIST_CAP


def list_signals(cfg: dict, today: str, scanner: Optional[str] = None,
                 strategy: Optional[str] = None, family: Optional[str] = None,
                 limit: int = _LIST_CAP) -> list:
    """Stored signals for a date, newest first, optional filters.

    family in {accepted, rejected, duplicated, expired} filters the derived
    bucket (attribution doc §0.3).
    """
    sql = (
        "SELECT s.signal_id, s.received_at, s.expires_at, s.scanner, s.strategy, "
        "s.symbol, s.status, s.rejection_reason, s.fingerprint, s.trade_id, "
        # Signal→trade join (schema.sql: trades.signal_id FK). LEFT: a signal
        # that never became a trade keeps every trade field NULL → renders '—'.
        "t.direction AS direction, t.status AS trade_status, "
        "t.exit_reason AS exit_reason, t.entry_time AS entry_time, "
        "t.exit_time AS exit_time, t.net_pnl AS net_pnl, "
        "CAST((julianday(t.exit_time) - julianday(t.entry_time)) * 86400 AS INTEGER) "
        "  AS trade_duration_sec, "
        # Trade Type comes from orders.product — there is NO trades.product
        # column. Correlated subquery (not a join) so a trade with several legs
        # cannot multiply the signal row. NULL when the ENTRY row is missing,
        # which renders '—' rather than silently reading as Intraday.
        "(SELECT o.product FROM orders o WHERE o.trade_id = t.trade_id "
        "   AND o.leg = 'ENTRY' ORDER BY o.placed_at LIMIT 1) AS product, "
        # Entry-order stamps feed the lifecycle timeline in the detail drawer,
        # so the drawer needs no second request per row.
        "(SELECT o.placed_at FROM orders o WHERE o.trade_id = t.trade_id "
        "   AND o.leg IN ('ENTRY','CO') ORDER BY o.placed_at LIMIT 1) AS order_placed_at, "
        "(SELECT o.filled_at FROM orders o WHERE o.trade_id = t.trade_id "
        "   AND o.leg IN ('ENTRY','CO') ORDER BY o.placed_at LIMIT 1) AS order_filled_at, "
        + _bucket_case_sql("s.") + " AS family "
        "FROM signals s LEFT JOIN trades t ON t.signal_id = s.signal_id "
        "WHERE s.received_at LIKE ?"
    )
    params: list = [today + "%"]
    if scanner:
        sql += " AND s.scanner = ?"
        params.append(scanner)
    if strategy:
        sql += " AND s.strategy = ?"
        params.append(strategy)
    if family:
        sql = "SELECT * FROM (" + sql + ") WHERE family = ?"
        params.append(family)
    sql += " ORDER BY received_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), _LIST_CAP)))
    with _ro(cfg) as conn:
        rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
    for r in rows:
        r["trade_type"] = _trade_type_of(r.get("product"))
        r["trade_result"] = _signal_result_of(r)
    return rows


# MIS and CO are both intraday products; CNC is delivery. Anything else (or a
# missing ENTRY order) stays None so the UI shows '—' instead of guessing.
_PRODUCT_TRADE_TYPE = {"MIS": "Intraday", "CO": "Intraday", "CNC": "Delivery"}


def _trade_type_of(product) -> Optional[str]:
    if not product:
        return None
    return _PRODUCT_TRADE_TYPE.get(str(product).strip().upper())


def _signal_result_of(row: dict) -> str:
    """Full lifecycle result for a signal row (spec: do NOT stop at Accepted/
    Rejected). Derived only from stored signal/trade fields — never inferred."""
    fam = row.get("family")
    if fam == "expired":
        return "Expired"
    if fam == "duplicated":
        return "Duplicate"
    if fam == "rejected":
        return "Rejected"
    if not row.get("trade_id"):
        # The spec's lifecycle starts at Received, one step before Accepted:
        # QUEUED is stored-but-not-yet-screened. Read from the status column,
        # not inferred.
        return "Received" if (row.get("status") or "").strip().upper() == "QUEUED" else "Accepted"
    reason = (row.get("exit_reason") or "").strip().upper()
    if reason == "SL_HIT":
        return "SL Hit"
    if reason == "TGT_HIT":
        return "TGT Hit"
    status = (row.get("trade_status") or "").strip().upper()
    if status.startswith("CLOSED"):
        return "Trade Closed"
    if row.get("entry_time"):
        return "Order Filled"
    return "Order Created"


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


def signal_scores(cfg: dict, signal_ids) -> dict:
    """{signal_id: {"system_score": int|None, "score_threshold": int|None}}.

    CANONICAL NAMING — Rama, 13-Aug-2026, and it is SYSTEM-WIDE, not a
    Screen-07 preference:
      * system_score    = `screener_results.score`         — the ACHIEVED score.
      * score_threshold = `screener_results.eligible_score` — the minimum the
        signal had to reach (v14: per-strategy min_score threshold).

    ⛔ "Signal Score" is RETIRED as a label and as a key. ⛔ A threshold is never
    presented as a score under any name — if a threshold is shown it is called
    "Score Threshold". His words: *"Do not fabricate or relabel a threshold as a
    score."*

    ⚠️ THIS REVERSES THE MAPPING THIS FUNCTION SHIPPED UNTIL 13-Aug. It used to
    return `system_score = eligible_score` (the THRESHOLD) and
    `signal_score = score`. That was the 11-Aug Screen-04 supersession of L8;
    tonight's ruling RESTORES L8 (`docs/G5_REDESIGN_PHASE_B.md:11`) and makes it
    apply everywhere. ⭐ It also removes a real split-brain: `screener_scores()`
    below has always returned the ACHIEVED score under the name "System Score"
    for analytics/operations/trade_explorer/trade_logs, so the codebase carried
    BOTH meanings of the same label at once.

    `eligible_score` is a v14 column: on an older DB (and in the test fixture)
    it does not exist, so the SELECT is retried without it and score_threshold
    comes back None — the caller then falls back to the configured
    `min_pass_score`. Nothing here is ever fabricated.
    """
    ids = tuple(dict.fromkeys(s for s in (signal_ids or []) if s))
    if not ids:
        return {}
    inc = _in_clause(ids)
    with _ro(cfg) as conn:
        try:
            rows = conn.execute(
                "SELECT signal_id, MAX(score) AS score, MAX(eligible_score) AS eligible "
                "FROM screener_results WHERE signal_id IN (" + inc + ") GROUP BY signal_id",
                ids,
            ).fetchall()
        except sqlite3.OperationalError:
            try:
                rows = conn.execute(
                    "SELECT signal_id, MAX(score) AS score, NULL AS eligible "
                    "FROM screener_results WHERE signal_id IN (" + inc + ") GROUP BY signal_id",
                    ids,
                ).fetchall()
            except sqlite3.OperationalError:
                return {}
    out: dict = {}
    for r in rows:
        out[r["signal_id"]] = {
            "system_score": int(r["score"]) if r["score"] is not None else None,
            "score_threshold": (int(r["eligible"])
                                if r["eligible"] is not None else None),
        }
    return out


def signal_kpi_counts(cfg: dict, today: str) -> dict:
    """Whole-day lifecycle counts for the Screen-04 KPI deck.

    Computed in SQL over EVERY stored signal for the date — deliberately NOT
    from the returned row list, which is capped at _LIST_CAP and would make the
    cards silently under-report on a heavy day.

    `total` counts STORED signals. It is NOT the webhook `received` count in the
    denominator block (that one includes pre-insert duplicates) — the two are
    different denominators and must never be conflated.
    """
    fam = _bucket_case_sql("s.")
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT " + fam + " AS family, COUNT(*) AS n FROM signals s "
            "WHERE s.received_at LIKE ? GROUP BY family",
            (today + "%",),
        ).fetchall()
        by_family = {r["family"]: int(r["n"]) for r in rows}
        trade_row = conn.execute(
            "SELECT "
            "  COUNT(t.trade_id) AS created, "
            "  SUM(CASE WHEN t.entry_time IS NOT NULL THEN 1 ELSE 0 END) AS filled, "
            "  SUM(CASE WHEN UPPER(COALESCE(t.exit_reason,'')) = 'SL_HIT' "
            "           THEN 1 ELSE 0 END) AS sl_hit, "
            "  SUM(CASE WHEN UPPER(COALESCE(t.exit_reason,'')) = 'TGT_HIT' "
            "           THEN 1 ELSE 0 END) AS tgt_hit, "
            "  SUM(CASE WHEN UPPER(COALESCE(t.status,'')) LIKE 'CLOSED%' "
            "           THEN 1 ELSE 0 END) AS closed "
            "FROM signals s JOIN trades t ON t.signal_id = s.signal_id "
            "WHERE s.received_at LIKE ?",
            (today + "%",),
        ).fetchone()
    t = dict(trade_row) if trade_row else {}
    total = sum(by_family.values())
    return {
        "total": total,
        "accepted": by_family.get("accepted", 0),
        "rejected": by_family.get("rejected", 0),
        "duplicate": by_family.get("duplicated", 0),
        "expired": by_family.get("expired", 0),
        "order_created": int(t.get("created") or 0),
        "order_filled": int(t.get("filled") or 0),
        "sl_hit": int(t.get("sl_hit") or 0),
        "tgt_hit": int(t.get("tgt_hit") or 0),
        "trade_closed": int(t.get("closed") or 0),
    }


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


# ── Screen-05 Orders (12-Aug-2026) ──────────────────────────────────────────
# ADDITIVE and READ-ONLY. Nothing above is modified; `list_orders` keeps its
# existing shape for its existing callers.
#
# ROW GRAIN = the ENTRY order. The approved mockup has no Leg column, shows one
# broker order id per row, and puts Entry/SL/TGT system prices side by side —
# that is only coherent at entry grain, and it is also the only grain at which
# "Order Value = entry only" sums without double-counting a trade's SL and TGT
# legs into the same total.
#
# PRICES ARE SYSTEM PRICES ONLY (approved revision, 12-Aug): entry_target_price,
# sl_initial, tgt_initial. Broker fill prices belong to the Positions screen.
# ⛔ orders.price is NULL on every ENTRY row in production (456/456 measured
# 12-Aug), so it is deliberately NOT the entry-price source.

_ORD_INTRADAY = ("MIS", "CO", "BO")


def _trade_type_of_product(product) -> str:
    p = (product or "").upper()
    if not p:
        return ""
    return "Intraday" if p in _ORD_INTRADAY else "Delivery"


def order_screen_rows(cfg: dict, today: str, limit: int = _LIST_CAP) -> list:
    """ENTRY orders for a date, shaped for Screen-05. Read-only."""
    sql = (
        "SELECT o.order_id, o.trade_id, o.status, o.order_type, o.product, "
        "o.qty_requested, o.qty_filled, o.placed_at, o.filled_at, "
        "o.rejection_reason, "
        "t.symbol AS symbol, t.strategy AS strategy, t.direction AS direction, "
        "t.entry_target_price, t.sl_initial, t.tgt_initial, t.charges, "
        "t.signal_id AS signal_id "
        "FROM orders o LEFT JOIN trades t ON t.trade_id = o.trade_id "
        "WHERE o.placed_at LIKE ? AND o.leg = 'ENTRY' "
        "ORDER BY o.placed_at DESC LIMIT ?"
    )
    with _ro(cfg) as conn:
        rows = [dict(r) for r in conn.execute(
            sql, (today + "%", max(1, min(int(limit), _LIST_CAP)))).fetchall()]

    for r in rows:
        placed = r.get("placed_at") or ""
        r["date"] = placed[:10]
        r["time"] = placed[11:19]
        r["trade_type"] = _trade_type_of_product(r.get("product"))
        req = r.get("qty_requested") or 0
        fld = r.get("qty_filled") or 0
        r["fill_pct"] = round(fld / req * 100.0, 2) if req else None
        # Order Value = ENTRY ORDER VALUE ONLY: system entry price x ordered qty.
        # ⛔ never entry+SL+TGT, never position value, never a fill-based notional.
        px = r.get("entry_target_price")
        r["order_value"] = round(float(px) * int(req), 2) if (px is not None and req) else None
        r["order_result"] = _order_result_of(r)
    return rows


_ORD_RESULT_FILLED = ("COMPLETE", "FILLED")
_ORD_RESULT_REJECT = ("REJECTED", "FAILED")
_ORD_RESULT_CANCEL = ("CANCELLED", "CANCELED")


def _order_result_of(r: dict) -> str:
    """Filled / Partial Fill / Rejected / Cancelled / Expired — from status+qty."""
    st = (r.get("status") or "").upper()
    req = r.get("qty_requested") or 0
    fld = r.get("qty_filled") or 0
    if st in _ORD_RESULT_REJECT:
        return "Rejected"
    if st in _ORD_RESULT_CANCEL:
        return "Cancelled"
    if st == "EXPIRED":
        return "Expired"
    if req and 0 < fld < req:
        return "Partial Fill"
    if st in _ORD_RESULT_FILLED or (req and fld >= req):
        return "Filled"
    return st.title() if st else "—"


def order_kpis(cfg: dict, today: str) -> dict:
    """The eight KPI cards. Order-value totals are ENTRY ONLY, by construction."""
    rows = order_screen_rows(cfg, today)
    all_orders = len(rows)

    def _n(res):
        return sum(1 for r in rows if r.get("order_result") == res)

    filled, partial = _n("Filled"), _n("Partial Fill")
    rejected, cancelled = _n("Rejected"), _n("Cancelled")

    # RULE (Rama, 12-Aug): the TOP KPI pair counts FILLED ORDERS ONLY.
    #   Total Orders      = count of orders whose result is Filled.
    #   Total Order Value = SUM(entry price x ordered qty) over FILLED orders.
    # ⛔ Cancelled / Rejected / Expired / unfilled contribute NOTHING to either.
    # ⛔ Still ENTRY ONLY — SL and TGT never enter order_value (see
    #    order_screen_rows). ⭐ Total Orders equalling Filled Orders is INTENDED
    #    and was explicitly accepted; neither card is renamed or removed.
    filled_rows = [r for r in rows if r.get("order_result") == "Filled"]
    values = [r["order_value"] for r in filled_rows if r.get("order_value") is not None]
    charges = [r["charges"] for r in filled_rows if r.get("charges") is not None]

    # Percentages keep ALL orders as their base — that is what makes
    # "Filled 86.19%" meaningful. ⛔ Never the filled-only count, which would
    # make every percentage read 100%.
    def _pct(n):
        return round(n / all_orders * 100.0, 2) if all_orders else None

    return {
        "all_orders": all_orders,
        "total_orders": filled,
        "filled": filled, "filled_pct": _pct(filled),
        "partial": partial, "partial_pct": _pct(partial),
        "rejected": rejected, "rejected_pct": _pct(rejected),
        "cancelled": cancelled, "cancelled_pct": _pct(cancelled),
        "total_order_value": round(sum(values), 2) if values else None,
        "avg_order_value": round(sum(values) / len(values), 2) if values else None,
        "total_charges": round(sum(charges), 2) if charges else None,
    }


def order_exec_context(cfg: dict, order_ids) -> dict:
    """Lifecycle timestamps + slippage per order, from order_execution_log.

    ⚠️ Two-state by design: an order with no execution-log row returns nothing
    and the UI renders 'not captured'. ⛔ Nothing is inferred or back-filled.
    """
    ids = [str(o) for o in (order_ids or []) if o]
    if not ids:
        return {}
    out = {}
    with _ro(cfg) as conn:
        for chunk in (ids[i:i + 400] for i in range(0, len(ids), 400)):
            q = ("SELECT order_id, intended_price, actual_price, slippage_rs, "
                 "slippage_pct, tolerance_fraction_used, order_timestamp, "
                 "fill_timestamp, exchange_timestamp, retry_count, status "
                 "FROM order_execution_log WHERE order_id IN (" +
                 ",".join("?" * len(chunk)) + ")")
            for r in conn.execute(q, tuple(chunk)).fetchall():
                out[str(r["order_id"])] = dict(r)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Screen-06 Positions (12-Aug-2026)
#
# ROW GRAIN = the TRADE (a position), not the order. Screen-05's grain is the
# ENTRY order; these are different questions and the two readers stay separate.
#
# ⛔ SCANNER IS ABSENT BY DECISION (Strategy carries the same relationship) —
#    no column, no filter, no detail row, exactly as Screen-05.
#
# ⭐ SYSTEM vs BROKER, and the words are load-bearing (Rama, 12-Aug):
#      Entry (System) = trades.entry_target_price   — the LIMIT we wanted
#      Entry (Filled) = trades.entry_actual_price   — a REAL fill price
#      SL  (System)   = trades.sl_initial
#      SL  (Broker)   = orders.trigger_price WHERE leg='SL'   — the trigger
#                       actually STANDING at the broker
#      TGT (System)   = trades.tgt_initial
#      TGT (Broker)   = orders.price        WHERE leg='TGT'
# ⛔⛔ THERE IS NO FILLED SL/TGT EXECUTION PRICE AND NONE IS INVENTED.
#    MEASURED 12-Aug: orders.avg_fill_price is NULL on ALL 927 orders ever
#    placed (ENTRY 457, SL 241, TGT 229 — zero populated in every leg), and a
#    leg that executes still records nothing there (today's trd_05c93... SL is
#    status=COMPLETE, qty_filled=0, avg_fill_price=NULL). The executed price of
#    an exit lands on trades.exit_price and is surfaced SEPARATELY as Exit
#    Price — ⛔ it never overwrites the standing Broker SL/TGT columns.
# ─────────────────────────────────────────────────────────────────────────────

# exit_reason values MEASURED in production (12-Aug): SL_HIT 111, TGT_HIT 74,
# MANUAL 47, GTT_EXIT 8, 'pre-FIX-189 cleanup' 6, STALE_PENDING_CLEANUP 3,
# MANUAL_CLOSE_EOD 1. ⛔ These are exit_reason strings, NOT the closure-source
# vocabulary (core/closure_source.py) — that one is imported, never re-typed.
_POS_SL = ("SL_HIT",)
_POS_TGT = ("TGT_HIT",)
_POS_MANUAL = ("MANUAL", "MANUAL_CLOSE_EOD")
_POS_EXPIRED = ("EOD", "TIMEOUT", "CIRCUIT_BREAKER")

POSITION_STATUSES = ("Open", "Closed", "SL Hit", "TGT Hit",
                     "Manual Exit", "Partial Exit", "Expired")


def _position_status_of(r: dict) -> str:
    """Derived position status. ⛔ Nothing is guessed: an exit_reason we do not
    recognise resolves to the neutral 'Closed', never to SL Hit / TGT Hit.

    ⚠️ GTT_EXIT is a MECHANISM, not a reason (the broker's GTT fired) — it does
    NOT say whether the SL or the TGT leg went, so it maps to 'Closed' and the
    verbatim reason is shown in the detail card instead of being inferred.
    """
    st = (r.get("status") or "").upper()
    if st in ("OPEN", "EXITING", "PENDING_FILL"):
        return "Open"
    if st == "PARTIAL":
        return "Partial Exit"
    reason = (r.get("exit_reason") or "").upper()
    if st == "CLOSED_MANUAL" or reason in _POS_MANUAL:
        return "Manual Exit"
    if reason in _POS_SL:
        return "SL Hit"
    if reason in _POS_TGT:
        return "TGT Hit"
    if reason in _POS_EXPIRED:
        return "Expired"
    return "Closed"


def _expected_rr(entry, sl, tgt, direction) -> Optional[float]:
    """(reward / risk) from the SYSTEM prices. None when any leg is missing or
    risk is non-positive — ⛔ never a fabricated 1:1 default."""
    try:
        e, s, t = float(entry), float(sl), float(tgt)
    except (TypeError, ValueError):
        return None
    is_long = str(direction or "").upper() == "LONG"
    risk = (e - s) if is_long else (s - e)
    reward = (t - e) if is_long else (e - t)
    if risk <= 0:
        return None
    return round(reward / risk, 2)


def _level_points(entry, level, direction, is_sl: bool) -> Optional[float]:
    """SL/TGT distance in RUPEES PER SHARE from the SYSTEM entry (spreadsheet
    columns "SL POINTS (₹)" / "TGT POINTS (₹)").

    📌 PER SHARE, NOT PER POSITION — spreadsheet note 2/3: "₹ denotes in all
    places per qty basis (except: unrealised)". ⛔ Never multiplied by qty here.

    📌 FROM THE SYSTEM LEVELS, NOT THE BROKER ONES (section E): these columns
    describe what the system intended. A broker-derived distance would be a
    different quantity and would need its own, differently-labelled column.

    Direction-aware, so a correctly-placed level yields a POSITIVE distance:
        LONG   sl = entry - sl_level    tgt = tgt_level - entry
        SHORT  sl = sl_level - entry    tgt = entry - tgt_level
    ⛔ Returns None (not 0.0) when an operand is missing — a zero would read as
    "the stop sits exactly at entry", which is a measurement, not an absence.
    """
    try:
        e, v = float(entry), float(level)
    except (TypeError, ValueError):
        return None
    is_long = str(direction or "").upper() == "LONG"
    d = (e - v) if (is_sl == is_long) else (v - e)
    return round(abs(d), 2)


def position_unrealised(entry_filled, ltp, qty, direction) -> Optional[float]:
    """Unrealised P&L for the WHOLE position (the one total-value column).

    📌 THE EXCEPTION TO THE PER-SHARE RULE — spreadsheet note 6:
        LONG   (ltp - entry_filled) * qty
        SHORT  (entry_filled - ltp) * qty
    ⭐ Against the FILLED entry, ⛔ never the system entry — unrealised measures
    what the position is actually worth against what was actually paid.

    ⛔⛔ THIS FUNCTION IS CORRECT AND CURRENTLY UNREACHABLE WITH A REAL `ltp`.
    ops_dashboard has ZERO live-price call sites, so every caller passes
    ltp=None and gets None back. It exists, and is tested, so that the day a
    live-price source is wired in, the arithmetic is already pinned — ⛔ it must
    NEVER be fed a stale or system price to make the column look populated.
    """
    if ltp is None or entry_filled is None or not qty:
        return None
    try:
        e, p, q = float(entry_filled), float(ltp), int(qty)
    except (TypeError, ValueError):
        return None
    per_share = (p - e) if str(direction or "").upper() == "LONG" else (e - p)
    return round(per_share * q, 2)


def position_screen_rows(cfg: dict, today: str, limit: int = _LIST_CAP) -> list:
    """Positions for a trading date, shaped for Screen-06. Read-only.

    Dated by entry_time, falling back to created_at for a trade that reserved
    capital but never filled — so a row never silently vanishes from its day.
    """
    sql = (
        "SELECT t.trade_id, t.signal_id, t.symbol, t.strategy, t.direction, "
        "t.status, t.sector, t.qty_planned, t.qty_filled, "
        "t.entry_target_price, t.entry_actual_price, t.sl_initial, t.tgt_initial, "
        "t.risk_amount, t.margin_reserved, t.actual_position_value_rs, "
        "t.created_at, t.entry_time, t.exit_time, "
        "t.exit_price, t.exit_reason, t.gross_pnl, t.charges, t.net_pnl, "
        "t.closure_source, t.exit_mechanism, "
        # ENTRY-leg order stamps feed the lifecycle rail (Signal -> Order ->
        # Position Open -> Exit). ⛔ A trade whose ENTRY order row is missing
        # keeps NULL here and the rail renders that step 'not captured'.
        "o.product AS product, o.placed_at AS order_placed_at, "
        "o.filled_at AS order_filled_at, o.order_id AS entry_order_id "
        "FROM trades t "
        "LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = 'ENTRY' "
        "WHERE COALESCE(t.entry_time, t.created_at) LIKE ? "
        "ORDER BY COALESCE(t.entry_time, t.created_at) DESC LIMIT ?"
    )
    with _ro(cfg) as conn:
        rows = [dict(r) for r in conn.execute(
            sql, (today + "%", max(1, min(int(limit), _LIST_CAP)))).fetchall()]

    for r in rows:
        stamp = r.get("entry_time") or r.get("created_at") or ""
        r["date"] = stamp[:10]
        r["time"] = stamp[11:19]
        r["trade_type"] = _trade_type_of_product(r.get("product"))
        r["position_status"] = _position_status_of(r)

        # QUANTITY — the BROKER-FILLED quantity IS the position (section F).
        # qty_planned is what the system asked for and is kept BESIDE it, never
        # in place of it. ⛔ The two are never collapsed into one "Qty".
        r["qty_system"] = r.get("qty_planned")
        r["qty_position"] = r.get("qty_filled")

        entry_sys = r.get("entry_target_price")
        direction = r.get("direction")
        # Per-share rupee distances from the SYSTEM levels (spreadsheet §E).
        r["sl_points"] = _level_points(entry_sys, r.get("sl_initial"), direction, True)
        r["tgt_points"] = _level_points(entry_sys, r.get("tgt_initial"), direction, False)
        # Derived R:R is kept for the detail card as a CROSS-CHECK against the
        # strategy-configured ratio the table shows. ⛔ They are two different
        # quantities and the table never silently substitutes one for the other.
        r["expected_rr"] = _expected_rr(
            entry_sys, r.get("sl_initial"), r.get("tgt_initial"), direction)

        # ⛔⛔ LTP AND UNREALISED HAVE NO SOURCE IN THIS BUILD and are set to
        # None here rather than omitted, so the shape of a row never changes
        # when a live-price source is finally wired in. The arithmetic lives in
        # position_unrealised() and is already tested; only `ltp` is missing.
        r["ltp"] = None
        r["ltp_at"] = None
        r["unrealised"] = position_unrealised(
            r.get("entry_actual_price"), r["ltp"], r.get("qty_filled"), direction)

        # ⭐ THE SAME strict definition the KPI uses — filled qty x FILLED entry,
        # None when either is missing. The row, the detail card and the KPI now
        # answer capital with one function, so they cannot drift apart.
        # ⛔ SL/TGT play no part; ⛔ no fallback to the system entry price.
        r["capital_used"] = _position_value_of(r)
    return rows


def position_broker_exits(cfg: dict, trade_ids) -> dict:
    """{trade_id: {sl_broker, sl_broker_status, tgt_broker, tgt_broker_status}}.

    The SL/TGT values STANDING AT THE BROKER, from the real exit-leg orders:
    SL uses trigger_price (the SL-M trigger), TGT uses price (the LIMIT).
    ⚠️ Two-state by design — a trade whose exit legs were never placed returns
    nothing and the UI renders an em-dash. ⛔ Nothing is inferred from the
    system value, which is the whole reason the two columns exist side by side.
    """
    ids = [str(t) for t in (trade_ids or []) if t]
    if not ids:
        return {}
    out: dict = {}
    with _ro(cfg) as conn:
        for chunk in (ids[i:i + 400] for i in range(0, len(ids), 400)):
            # ⛔ SUPERSEDED LEGS ARE EXCLUDED, and this is not a detail: a trade
            # can carry an OLD cancelled SL alongside the live one (the
            # superseded chain). Taking the last row seen would let a dead leg
            # overwrite the standing one and the screen would report a stale
            # trigger — or a blank — as the broker's current position.
            # ORDER BY placed_at makes the LATEST row win deterministically.
            q = ("SELECT trade_id, leg, price, trigger_price, status "
                 "FROM orders WHERE leg IN ('SL','TGT') "
                 "AND superseded_by IS NULL AND trade_id IN (" +
                 ",".join("?" * len(chunk)) + ") ORDER BY placed_at ASC")
            for r in conn.execute(q, tuple(chunk)).fetchall():
                tid = str(r["trade_id"])
                slot = out.setdefault(tid, {})
                if (r["leg"] or "").upper() == "SL":
                    val, key, skey = r["trigger_price"], "sl_broker", "sl_broker_status"
                else:
                    val, key, skey = r["price"], "tgt_broker", "tgt_broker_status"
                # ⛔ Never let a row WITHOUT a price blank out one that has it.
                if val is None and slot.get(key) is not None:
                    continue
                slot[key] = val
                slot[skey] = r["status"]
    return out


def position_excursions(cfg: dict, trade_ids) -> dict:
    """{trade_id: {mfe_pct, mae_pct}} — Highest Profit % / Highest Drawdown %.

    REAL, from trade_excursions (populated by the reconstruct_excursions cron).
    ⚠️ MEASURED 12-Aug: 192 of 589 trades have a row. A trade without one
    returns nothing and renders an em-dash. ⛔ Never zero, which would read as
    "no drawdown" when the truth is "not reconstructed yet".
    """
    ids = [str(t) for t in (trade_ids or []) if t]
    if not ids:
        return {}
    out: dict = {}
    with _ro(cfg) as conn:
        for chunk in (ids[i:i + 400] for i in range(0, len(ids), 400)):
            q = ("SELECT trade_id, mfe_pct, mae_pct FROM trade_excursions "
                 "WHERE trade_id IN (" + ",".join("?" * len(chunk)) + ")")
            for r in conn.execute(q, tuple(chunk)).fetchall():
                out[str(r["trade_id"])] = {"mfe_pct": r["mfe_pct"],
                                           "mae_pct": r["mae_pct"]}
    return out


def position_open_set(cfg: dict) -> list:
    """The CURRENT open positions, all dates — the base for the live KPIs.

    📌 A DIFFERENT BASE from the dated table, deliberately: a delivery position
    carried from an earlier day is still open exposure today and must not fall
    out of "Open Positions" merely because its entry date is not the one being
    viewed. Every KPI built on this carries its base in its own footer text.
    """
    states = _OPEN_STATES
    with _ro(cfg) as conn:
        rows = conn.execute(
            "SELECT t.trade_id, t.symbol, t.direction, t.status, t.qty_filled, "
            "t.entry_target_price, t.entry_actual_price, t.margin_reserved, "
            "t.actual_position_value_rs, t.entry_time "
            "FROM trades t WHERE t.status IN (" + _in_clause(states) + ")",
            states,
        ).fetchall()
    return [dict(r) for r in rows]


def _position_value_of(r: dict) -> Optional[float]:
    """Capital tied up in ONE open position.

    📌 THE DEFINITION, and it is deliberately narrow (Rama, 12-Aug):
            capital = BROKER-FILLED qty  x  FILLED entry price
    ⛔ NOT the system/planned qty — a partial fill ties up only what filled.
    ⛔ NOT the system entry price — capital is what was actually paid.
    ⛔ SL AND TGT CONTRIBUTE NOTHING. They are exit levels, not money spent;
       a notional built from them would be a different quantity entirely.
    ⛔ NOT `actual_position_value_rs` either: the sizer writes that as
       `final qty * entry_price` and which entry price it used is not
       guaranteed to be the FILL. Computing it here keeps the operands visible.

    Returns None — ⛔ never 0.0 — when the fill price or the filled quantity is
    missing. A zero would silently shrink the KPI and read as "this position
    ties up nothing"; the caller counts these separately and says so.
    """
    px = r.get("entry_actual_price")
    q = r.get("qty_filled") or 0
    if px is None or not q:
        return None
    try:
        return round(float(px) * int(q), 2)
    except (TypeError, ValueError):
        return None


def position_capital_used(open_rows: list) -> dict:
    """{'total', 'valued', 'unpriced'} over the open set.

    ⭐ `unpriced` is reported, not swallowed: it is the number of open positions
    that have no filled entry price, and therefore the number the total does
    NOT account for. ⛔ A KPI that quietly omits rows is worse than one that
    admits it.
    """
    vals = [(_position_value_of(r)) for r in open_rows]
    priced = [v for v in vals if v is not None]
    return {
        "total": round(sum(priced), 2),
        "valued": len(priced),
        "unpriced": len(vals) - len(priced),
    }


def position_kpis(cfg: dict, today: str) -> dict:
    """The six Screen-06 KPI cards.

    ⛔⛔ CURRENT MTM IS NOT COMPUTED AND NOT GUESSED. It needs a live price and
    ops_dashboard has ZERO live-price call sites (measured 12-Aug, whole
    backend) — /api/positions already declares ltp/mtm/unrealized/current_rr
    UNAVAILABLE, 'Pending Broker Source' (G4). It is returned as None so the UI
    can say so out loud. ⭐ Today's REALIZED P&L is real (trades.net_pnl) and is
    a DIFFERENT quantity — the two are never substituted for each other.
    """
    open_rows = position_open_set(cfg)

    def _dir(d):
        return sum(1 for r in open_rows
                   if str(r.get("direction") or "").upper() == d)

    cap = position_capital_used(open_rows)

    with _ro(cfg) as conn:
        # Dated by exit_time ONLY. ⛔ Not COALESCE(exit_time, updated_at): a row
        # touched today for an unrelated reason would pull an older day's P&L
        # into today's realized figure, and updated_at moves for any write.
        realized = conn.execute(
            "SELECT SUM(net_pnl) AS p FROM trades "
            "WHERE net_pnl IS NOT NULL AND exit_time LIKE ?",
            (today + "%",),
        ).fetchone()
    rp = realized["p"] if realized else None

    return {
        "open_positions": len(open_rows),
        "long_positions": _dir("LONG"),
        "short_positions": _dir("SHORT"),
        # filled qty x FILLED entry, over open positions only. ⛔ SL/TGT excluded.
        "total_capital_used": cap["total"],
        "capital_valued": cap["valued"],
        "capital_unpriced": cap["unpriced"],
        # ⛔ unavailable BY ARCHITECTURE, not by omission — see the docstring.
        "current_mtm": None,
        "current_mtm_reason": "Pending Broker Source (G4) — the GUI has no live price",
        "realized_pnl_today": round(float(rp), 2) if rp is not None else None,
        "opening_capital": opening_capital(cfg, today),
    }


def position_summary(cfg: dict, today: str) -> dict:
    """The bottom summary panels.

    ⭐ THREE OF THE FOUR ARE REAL: capital utilization, long/short distribution
    and the status breakdown all come from the DB.
    ⛔ THE FOURTH — MTM PERFORMANCE — IS NOT. It is an intraday MTM curve and
    needs a live price the GUI cannot reach, so it returns available=False with
    its reason and the panel says so instead of drawing an invented line.
    """
    open_rows = position_open_set(cfg)
    # ⭐ THE SAME function the KPI uses — the donut and the card cannot disagree.
    cap = position_capital_used(open_rows)
    used = cap["total"]
    opening = opening_capital(cfg, today)
    available = (float(opening) - used) if opening is not None else None

    rows = position_screen_rows(cfg, today)
    breakdown = {s: 0 for s in POSITION_STATUSES}
    for r in rows:
        breakdown[r["position_status"]] = breakdown.get(r["position_status"], 0) + 1

    longs = sum(1 for r in open_rows if str(r.get("direction") or "").upper() == "LONG")
    shorts = sum(1 for r in open_rows if str(r.get("direction") or "").upper() == "SHORT")

    return {
        "capital": {
            "used": round(used, 2),
            "available": round(available, 2) if available is not None else None,
            "total": round(float(opening), 2) if opening is not None else None,
            "used_pct": round(used / float(opening) * 100.0, 2)
                        if (opening and float(opening)) else None,
            "unpriced": cap["unpriced"],   # open positions the total cannot value
        },
        "distribution": {"long": longs, "short": shorts, "total": len(open_rows)},
        "mtm": {
            "available": False,
            "reason": "Pending Broker Source (G4) — the GUI has no live price",
        },
        "status_breakdown": breakdown,
        "status_total": len(rows),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Screen-07 Trade Explorer (14-Aug-2026)
#
# ROW GRAIN = the TRADE, over a DATE RANGE. Screen-06's grain is also the trade
# but its question is "what is open right now"; this one is "what happened, and
# why". They stay separate readers.
#
# ⭐ WHAT IS NEW HERE, AND WHY IT COULD NOT LIVE ON SCREEN-06:
#   ROI          net_pnl / margin_reserved            — realised return on the
#                capital actually COMMITTED. ⛔ NOT on the leveraged notional
#                (qty x price), which would flatter every intraday row by the
#                leverage factor. margin_reserved is populated and > 0 on all
#                603 trades (measured 13-Aug), so the denominator is real.
#   R-multiple   net_pnl / risk_amount                — the realised outcome in
#                units of the risk taken. ⛔ A DIFFERENT QUANTITY FROM R:R and
#                never a substitute for it: R:R is what was PLANNED, R-multiple
#                is what was ACHIEVED. Both are shown, side by side.
#   SL/TGT       Filled = the price at which that leg ACTUALLY EXECUTED, and it
#                exists here only because a CLOSED trade records it.
#
# ⛔⛔ THE FILLED COLUMNS ARE NOT FABRICATED, AND THE RULE IS NARROW:
#    orders.avg_fill_price is STILL NULL on all 977 orders ever placed
#    (re-measured 13-Aug: ENTRY 469 / SL 247 / TGT 235 / EOD 26, zero populated
#    in every leg) — Screen-06's finding reproduces on a larger corpus. The
#    executed price lives on trades.exit_price, and trades.exit_reason NAMES the
#    leg that fired. So, and ONLY so:
#        exit_reason = SL_HIT   ⇒ SL (Filled)  = exit_price
#        exit_reason = TGT_HIT  ⇒ TGT (Filled) = exit_price
#    Every other reason leaves BOTH empty. ⚠️ GTT_EXIT is a MECHANISM, not a
#    leg — it does not say whether the stop or the target went, so it fills
#    NEITHER column (the same treatment _position_status_of gives it).
#    (P) 114/114 SL_HIT and 76/76 TGT_HIT rows carry an exit_price.
# ─────────────────────────────────────────────────────────────────────────────

# The Explorer is historical, so its cap is the range cap the other range
# readers use (closed_trades_range), NOT the 500-row today cap.
_EXPLORER_CAP = 2000

TRADE_RESULTS = ("TGT Hit", "SL Hit", "Manual Exit", "Expired", "Closed",
                 "Partial Exit", "Open", "Failed", "Rejected", "Cancelled")


def trade_explorer_cap() -> int:
    return _EXPLORER_CAP


def _trade_result_of(r: dict) -> str:
    """Derived outcome. ⭐ Reuses Screen-06's exit-reason vocabulary constants
    (_POS_SL/_POS_TGT/_POS_MANUAL/_POS_EXPIRED) rather than re-typing them, so
    the two screens can never disagree about what SL_HIT means.

    ⛔ Nothing is guessed: an unrecognised exit_reason resolves to the neutral
    'Closed', never to SL Hit / TGT Hit.

    ⚠️ The Explorer sees the states Screen-06 never shows, because two-thirds of
    all trade rows never opened exposure: FAILED 278 / REJECTED 69 /
    CANCELLED 9 of 603 (measured 13-Aug). They are their own results — ⛔ NOT
    folded into 'Closed', which would read as a completed round trip.
    """
    st = (r.get("status") or "").upper()
    if st == "FAILED":
        return "Failed"
    if st == "REJECTED":
        return "Rejected"
    if st in ("CANCELLED", "CANCELED"):
        return "Cancelled"
    if st in ("OPEN", "EXITING", "PENDING_FILL"):
        return "Open"
    if st == "PARTIAL":
        return "Partial Exit"
    reason = (r.get("exit_reason") or "").upper()
    if st == "CLOSED_MANUAL" or reason in _POS_MANUAL:
        return "Manual Exit"
    if reason in _POS_SL:
        return "SL Hit"
    if reason in _POS_TGT:
        return "TGT Hit"
    if reason in _POS_EXPIRED:
        return "Expired"
    return "Closed"


def roi_pct(net_pnl, margin_reserved) -> Optional[float]:
    """ROI = 100 x net_pnl / margin_reserved.

    📌 THE BASE IS THE COMMITTED (RESERVED) CAPITAL — the money the system
    actually set aside for this trade. ⛔ NOT the position notional
    (qty x price): with intraday leverage the notional is several times the
    margin, so dividing by it would report a materially smaller return and
    would answer a question nobody asked.

    ⛔ Returns None — never 0.0 — when the P&L is unknown or the margin is
    missing/zero. A zero would read as "this trade returned nothing".
    """
    if net_pnl is None or margin_reserved in (None, 0):
        return None
    try:
        m = float(margin_reserved)
        if m <= 0:
            return None
        return round(float(net_pnl) / m * 100.0, 2)
    except (TypeError, ValueError):
        return None


def r_multiple(net_pnl, risk_amount) -> Optional[float]:
    """R-multiple = net_pnl / risk_amount — the realised result expressed in
    units of the risk that was taken.

    ⛔⛔ THIS IS NOT R:R AND MUST NEVER BE PRINTED IN AN R:R COLUMN. R:R is the
    ratio the strategy PLANNED (reward per unit of risk, decided before entry);
    R-multiple is what the trade ACHIEVED. A trade with R:R 1.5 that stops out
    scores about -1R; the two numbers answer different questions and both are
    shown.
    """
    if net_pnl is None or risk_amount in (None, 0):
        return None
    try:
        risk = float(risk_amount)
        if risk <= 0:
            return None
        return round(float(net_pnl) / risk, 2)
    except (TypeError, ValueError):
        return None


def _duration_sec(entry_time, exit_time) -> Optional[int]:
    """Holding period in seconds. None unless BOTH stamps exist — ⛔ an open
    trade's duration is not measured against 'now' here, because the row would
    then change every time the page is refreshed."""
    if not entry_time or not exit_time:
        return None
    try:
        from datetime import datetime
        a = datetime.fromisoformat(str(entry_time))
        b = datetime.fromisoformat(str(exit_time))
        return max(0, int((b - a).total_seconds()))
    except (TypeError, ValueError):
        return None


def entry_slippage(entry_system, entry_filled, direction):
    """(rupees_per_share, percent) of ENTRY slippage. POSITIVE = ADVERSE.

        LONG   filled - system      (bought higher than intended = worse)
        SHORT  system - filled      (sold lower than intended = worse)

    ⭐⭐ THIS FORMULA IS NOT INVENTED — IT IS THE PRODUCTION ONE, RECOVERED AND
    VERIFIED. order_execution_log records slippage_rs/slippage_pct, but its
    `parent_trade_id` was only back-filled from July (measured: 0/72 rows in
    June, 100/290 in July, 74/74 in August), so only 91 of 246 filled trades can
    be joined to a recorded row.

    Rather than leave 63% of the column empty, the recorded rows were used as
    the CONTROL: on all 91 overlapping trades this formula reproduces
    `slippage_rs` to <=0.005 and `slippage_pct` to <=0.01, and the log's own
    `intended_price`/`actual_price` are equal to trades.entry_target_price /
    trades.entry_actual_price on 91/91. ⇒ the derived value is the SAME function
    of the SAME operands, ⛔ not a second definition.

    The row still says which source it used (`slippage_source`), so a reader can
    tell a recorded measurement from a reproduced one.
    """
    if entry_system is None or entry_filled is None:
        return None, None
    try:
        sysp, fill = float(entry_system), float(entry_filled)
    except (TypeError, ValueError):
        return None, None
    d = (fill - sysp) if str(direction or "").upper() == "LONG" else (sysp - fill)
    pct = (d / sysp * 100.0) if sysp else None
    return round(d, 4), (round(pct, 4) if pct is not None else None)


def trade_recorded_slippage(cfg: dict, trade_ids) -> dict:
    """{trade_id: {slippage_rs, slippage_pct, intended_price, actual_price}} for
    the ENTRY leg, from order_execution_log.

    ⚠️⚠️ THE JOIN KEY IS `parent_trade_id`, AND THAT IS A MEASURED CHOICE, NOT A
    PREFERENCE: `order_execution_log.order_id` holds an INTERNAL id
    (`ord_<hex>`) while `orders.order_id` holds the BROKER id (`260813170888908`).
    They are DIFFERENT ID SPACES — joining them returns 0 rows on production
    data for every trade ever placed. ⛔ Do not "simplify" this to an order_id
    join.
    """
    ids = [str(t) for t in (trade_ids or []) if t]
    if not ids:
        return {}
    out: dict = {}
    with _ro(cfg) as conn:
        for chunk in (ids[i:i + 400] for i in range(0, len(ids), 400)):
            q = ("SELECT parent_trade_id, intended_price, actual_price, "
                 "slippage_rs, slippage_pct FROM order_execution_log "
                 "WHERE leg = 'ENTRY' AND parent_trade_id IN (" +
                 ",".join("?" * len(chunk)) + ") ORDER BY id ASC")
            for r in conn.execute(q, tuple(chunk)).fetchall():
                # ORDER BY id ASC ⇒ the LATEST row wins deterministically when a
                # retry produced more than one (measured: 1 trade in 603).
                out[str(r["parent_trade_id"])] = dict(r)
    return out


def trade_explorer_rows(cfg: dict, from_date: str, to_date: str,
                        limit: int = _EXPLORER_CAP) -> list:
    """Trades whose TRADING DAY falls in [from_date, to_date], shaped for
    Screen-07. Read-only.

    📌 DATED BY `COALESCE(entry_time, created_at)` — the day the trade HAPPENED,
    the same rule Screen-06 uses. ⛔ NOT by exit_time: a trade that never filled
    has no exit and would vanish from its own day, and two-thirds of all rows
    are exactly that. The exit day is carried separately as `exit_date`.
    """
    sql = (
        "SELECT t.trade_id, t.signal_id, t.symbol, t.strategy, t.direction, "
        "t.status, t.sector, t.qty_planned, t.qty_filled, "
        "t.entry_target_price, t.entry_actual_price, t.sl_initial, t.tgt_initial, "
        "t.risk_amount, t.margin_reserved, t.actual_position_value_rs, "
        "t.tgt_risk_reward_applied, t.binding_constraint, "
        "t.created_at, t.entry_time, t.exit_time, "
        "t.exit_price, t.exit_reason, t.gross_pnl, t.net_pnl, "
        # ⛔ THE gross-minus-net FALLBACK IS GATED ON A ROUND TRIP HAVING
        # HAPPENED. The ungated form (used by trades_in_range) yields 0.00 for
        # every trade that never opened exposure — and two-thirds of all rows
        # are exactly that — so a Charges column would print a measured-looking
        # zero for 161 of 271 rows. NULL is the truth there: nothing was traded,
        # so nothing was charged and nothing was recorded.
        "CASE WHEN t.charges IS NOT NULL THEN t.charges "
        "     WHEN t.gross_pnl IS NOT NULL OR t.net_pnl IS NOT NULL "
        "       THEN COALESCE(t.gross_pnl,0) - COALESCE(t.net_pnl,0) "
        "     ELSE NULL END AS charges, "
        "t.closure_source, t.exit_mechanism, t.mode, "
        # ⛔ `s.scanner` IS NOT PROJECTED. (P) 14-Aug: it equals
        # trades.strategy on all 603 trades AND on all 127,246 signals ever
        # received (13 distinct values each side, ZERO differing pairs), so
        # carrying it would ship one value under two names. The JOIN stays
        # for `received_at`, which is the lifecycle rail's first stamp and
        # lives nowhere else. Scanner-level analysis: /scanner-attribution.
        "s.received_at AS signal_received_at, "
        "o.product AS product, o.placed_at AS order_placed_at, "
        "o.filled_at AS order_filled_at, o.order_id AS entry_order_id "
        "FROM trades t "
        "LEFT JOIN signals s ON s.signal_id = t.signal_id "
        "LEFT JOIN orders o ON o.trade_id = t.trade_id AND o.leg = 'ENTRY' "
        "WHERE substr(COALESCE(t.entry_time, t.created_at), 1, 10) BETWEEN ? AND ? "
        "ORDER BY COALESCE(t.entry_time, t.created_at) DESC LIMIT ?"
    )
    with _ro(cfg) as conn:
        rows = [dict(r) for r in conn.execute(
            sql, (from_date, to_date,
                  max(1, min(int(limit), _EXPLORER_CAP)))).fetchall()]

    recorded = trade_recorded_slippage(cfg, [r["trade_id"] for r in rows])

    for r in rows:
        stamp = r.get("entry_time") or r.get("created_at") or ""
        r["date"] = stamp[:10]
        r["time"] = stamp[11:19]
        r["exit_date"] = (r.get("exit_time") or "")[:10] or None
        r["trade_type"] = _trade_type_of_product(r.get("product"))
        r["result"] = _trade_result_of(r)

        # Qty: the system-ordered quantity and the broker-filled one are kept
        # side by side, never collapsed — Screen-06's rule, unchanged.
        r["qty_system"] = r.get("qty_planned")
        r["qty_filled"] = r.get("qty_filled")

        direction = r.get("direction")
        entry_sys = r.get("entry_target_price")

        # ── SL / TGT: System · Broker · Filled ────────────────────────────────
        # System and Filled are computed here; Broker is joined by the caller
        # from position_broker_exits (the SAME reader Screen-06 uses, so the
        # standing trigger cannot be reported two different ways).
        reason = (r.get("exit_reason") or "").upper()
        exit_px = r.get("exit_price")
        r["sl_filled"] = exit_px if reason in _POS_SL else None
        r["tgt_filled"] = exit_px if reason in _POS_TGT else None

        r["sl_points"] = _level_points(entry_sys, r.get("sl_initial"), direction, True)
        r["tgt_points"] = _level_points(entry_sys, r.get("tgt_initial"), direction, False)

        # ── R:R (planned) vs R-multiple (achieved) ────────────────────────────
        # R:R comes from the trade's OWN recorded ratio, ⛔ not from today's
        # strategy YAML: this screen shows history, and a YAML edited since would
        # silently rewrite what a past trade was planned against. A trade with no
        # recorded ratio renders '—'; ⛔ no fallback, ⛔ no global default.
        rr = r.get("tgt_risk_reward_applied")
        try:
            r["rr_applied"] = float(rr) if rr is not None else None
        except (TypeError, ValueError):
            r["rr_applied"] = None
        r["expected_rr"] = _expected_rr(
            entry_sys, r.get("sl_initial"), r.get("tgt_initial"), direction)
        r["r_multiple"] = r_multiple(r.get("net_pnl"), r.get("risk_amount"))

        # ── ROI on COMMITTED capital ─────────────────────────────────────────
        r["roi_pct"] = roi_pct(r.get("net_pnl"), r.get("margin_reserved"))
        r["capital_committed"] = (round(float(r["margin_reserved"]), 2)
                                  if r.get("margin_reserved") is not None else None)

        # ── Entry slippage ───────────────────────────────────────────────────
        rec = recorded.get(str(r["trade_id"])) or {}
        if rec.get("slippage_rs") is not None:
            r["slippage_rs"] = round(float(rec["slippage_rs"]), 4)
            r["slippage_pct"] = (round(float(rec["slippage_pct"]), 4)
                                 if rec.get("slippage_pct") is not None else None)
            r["slippage_source"] = "recorded"
        else:
            srs, spct = entry_slippage(entry_sys, r.get("entry_actual_price"), direction)
            r["slippage_rs"] = srs
            r["slippage_pct"] = spct
            r["slippage_source"] = "derived" if srs is not None else None

        r["duration_sec"] = _duration_sec(r.get("entry_time"), r.get("exit_time"))
        # win / loss / flat / undecided — one classifier, used by the row, the
        # KPI deck and the summary panels alike.
        net = r.get("net_pnl")
        r["outcome"] = (None if net is None
                        else "win" if float(net) > 0
                        else "loss" if float(net) < 0 else "flat")
    return rows


def trade_explorer_kpis(rows: list) -> dict:
    """The six KPI cards, computed over the SAME rows the table shows — so the
    deck and the table can never describe different populations.

    ⛔ Every average is taken over the rows that HAVE the quantity, and the count
    of those rows is returned beside it. An average silently taken over a
    denominator that includes rows with no value is a different number wearing
    the same label.
    """
    total = len(rows)
    closed = [r for r in rows if r.get("net_pnl") is not None]
    wins = [r for r in closed if r["outcome"] == "win"]
    losses = [r for r in closed if r["outcome"] == "loss"]
    decided = len(wins) + len(losses)

    nets = [float(r["net_pnl"]) for r in closed]
    gross = [float(r["gross_pnl"]) for r in rows if r.get("gross_pnl") is not None]
    charges = [float(r["charges"]) for r in rows if r.get("charges") is not None]
    rois = [r["roi_pct"] for r in rows if r.get("roi_pct") is not None]
    rms = [r["r_multiple"] for r in rows if r.get("r_multiple") is not None]

    win_sum = sum(float(r["net_pnl"]) for r in wins)
    loss_sum = abs(sum(float(r["net_pnl"]) for r in losses))

    return {
        "total_trades": total,
        "closed_trades": len(closed),
        "open_trades": sum(1 for r in rows if r["result"] == "Open"),
        # ⛔ Trades that never opened exposure are counted and NAMED, not hidden:
        # they are the majority of rows and a screen that omits them would
        # overstate how much the system actually traded.
        "no_exposure_trades": sum(1 for r in rows
                                  if r["result"] in ("Failed", "Rejected", "Cancelled")),
        "wins": len(wins), "losses": len(losses), "decided": decided,
        "win_rate": round(len(wins) / decided * 100.0, 2) if decided else None,
        "net_pnl": round(sum(nets), 2) if nets else None,
        "gross_pnl": round(sum(gross), 2) if gross else None,
        "total_charges": round(sum(charges), 2) if charges else None,
        "avg_roi_pct": round(sum(rois) / len(rois), 2) if rois else None,
        "roi_basis": len(rois),
        "avg_r_multiple": round(sum(rms) / len(rms), 2) if rms else None,
        "r_basis": len(rms),
        # Σ winning net ÷ Σ |losing net|. None when there are no losses — ⛔ not
        # infinity and ⛔ not a large sentinel.
        "profit_factor": round(win_sum / loss_sum, 2) if loss_sum else None,
        "best_trade": round(max(nets), 2) if nets else None,
        "worst_trade": round(min(nets), 2) if nets else None,
    }


def trade_explorer_summary(rows: list) -> dict:
    """The four bottom panels, over the same rows. All four are REAL — this
    screen is historical, so nothing here needs a live price."""
    outcome_mix = {k: 0 for k in TRADE_RESULTS}
    for r in rows:
        outcome_mix[r["result"]] = outcome_mix.get(r["result"], 0) + 1

    def _side(side):
        sub = [r for r in rows if str(r.get("direction") or "").upper() == side]
        closed = [r for r in sub if r.get("net_pnl") is not None]
        w = sum(1 for r in closed if r["outcome"] == "win")
        d = sum(1 for r in closed if r["outcome"] in ("win", "loss"))
        return {
            "trades": len(sub), "closed": len(closed),
            "net": round(sum(float(r["net_pnl"]) for r in closed), 2) if closed else None,
            "win_rate": round(w / d * 100.0, 2) if d else None,
        }

    by_strategy: dict = {}
    for r in rows:
        name = r.get("strategy") or "—"
        agg = by_strategy.setdefault(name, {"strategy": name, "trades": 0,
                                            "closed": 0, "wins": 0, "losses": 0,
                                            "net": 0.0})
        agg["trades"] += 1
        if r.get("net_pnl") is not None:
            agg["closed"] += 1
            agg["net"] = round(agg["net"] + float(r["net_pnl"]), 2)
            if r["outcome"] == "win":
                agg["wins"] += 1
            elif r["outcome"] == "loss":
                agg["losses"] += 1
    strat_rows = sorted(by_strategy.values(), key=lambda a: -abs(a["net"]))

    wins = sum(1 for r in rows if r.get("outcome") == "win")
    losses = sum(1 for r in rows if r.get("outcome") == "loss")
    flats = sum(1 for r in rows if r.get("outcome") == "flat")

    return {
        "outcome_mix": outcome_mix,
        "outcome_total": len(rows),
        "win_loss": {"wins": wins, "losses": losses, "flat": flats,
                     "decided": wins + losses},
        "by_direction": {"long": _side("LONG"), "short": _side("SHORT")},
        "by_strategy": strat_rows,
        # Coverage of the two enrichment sources, surfaced rather than implied by
        # a column full of em-dashes.
        "coverage": {
            "slippage_recorded": sum(1 for r in rows
                                     if r.get("slippage_source") == "recorded"),
            "slippage_derived": sum(1 for r in rows
                                    if r.get("slippage_source") == "derived"),
            "roi_valued": sum(1 for r in rows if r.get("roi_pct") is not None),
            "rr_recorded": sum(1 for r in rows if r.get("rr_applied") is not None),
        },
    }
