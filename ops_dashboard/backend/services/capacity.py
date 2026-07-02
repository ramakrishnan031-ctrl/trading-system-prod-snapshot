"""
ops_dashboard/backend/services/capacity.py

Implements the STEP-0 capacity inventory (rows #1–#8) — Limit vs Used vs
Remaining for every primary daily quota. Limits come from the effective config
(snapshot-first, YAML-fallback via config_reader); "used" comes from db_reader;
the signal-queue depth comes from the trader's /health (unavailable when down).

Percentage limits (daily-loss, intraday-capital) resolve to ₹ against the
day-opening capital; if that is unknown (pre-open), the row reports
status='AWAITING' rather than a fabricated number (inventory decision D4).
"""
from __future__ import annotations

from typing import Optional

from ..readers import config_reader, db_reader, metrics_client
from . import freshness

_WARN_FRAC = 0.80  # used ≥ 80% of limit → WARNING


def _status(used, limit) -> str:
    if limit is None:
        return "AWAITING"
    if limit <= 0:
        return "OK"
    if used >= limit:
        return "BREACH"
    if used >= _WARN_FRAC * limit:
        return "WARNING"
    return "OK"


def _row(key, label, scope, unit, used, limit, *, inert=False,
         status=None, note=None, extra=None) -> dict:
    if inert:
        status = "INERT"
    if status is None:
        status = _status(used if used is not None else 0, limit)
    remaining = None
    pct = None
    if limit is not None and used is not None:
        remaining = max(0, limit - used) if unit == "count" else max(0.0, limit - used)
        if limit > 0:
            pct = round(100.0 * used / limit, 1)
    row = {
        "key": key, "label": label, "scope": scope, "unit": unit,
        "used": used, "limit": limit, "remaining": remaining,
        "pct": pct, "status": status, "inert": inert, "note": note,
    }
    if extra:
        row.update(extra)
    return row


def build_capacity(cfg: dict, today: Optional[str] = None, now=None,
                   system_config: Optional[dict] = None,
                   trader_health: Optional[dict] = None) -> dict:
    now = now or freshness.ist_now()
    today = today or freshness.ist_today_iso(now)
    sc = system_config if system_config is not None else config_reader.get_system_config(cfg, today)

    risk = sc.get("risk", {}) if isinstance(sc, dict) else {}
    capital = sc.get("capital", {}) if isinstance(sc, dict) else {}
    sq = sc.get("signal_queue", {}) if isinstance(sc, dict) else {}
    force_intraday = bool(sc.get("force_intraday_only", True)) if isinstance(sc, dict) else True

    opening = db_reader.opening_capital(cfg, today)
    cap_usage = db_reader.capital_usage(cfg)

    rows = []

    # #1 max_daily_trades
    rows.append(_row(
        "max_daily_trades", "Daily Trades", "global/day", "count",
        db_reader.daily_trades_used(cfg, today),
        _int(risk.get("max_daily_trades")),
    ))

    # #2 max_open_positions
    rows.append(_row(
        "max_open_positions", "Open Positions", "global/concurrent", "count",
        db_reader.open_positions_count(cfg),
        _int(risk.get("max_open_positions")),
    ))

    # #3 daily_loss_limit (₹ = pct × opening capital)
    loss_pct = _float(risk.get("daily_loss_limit_pct"))
    loss_used = round(db_reader.realized_loss_today(cfg, today), 2)
    if opening is not None and loss_pct is not None:
        loss_limit = round(loss_pct * opening, 2)
        rows.append(_row(
            "daily_loss_limit", "Daily Loss (₹)", "global/day", "rs",
            loss_used, loss_limit,
            extra={"limit_pct": loss_pct, "opening_capital": round(opening, 2)},
        ))
    else:
        rows.append(_row(
            "daily_loss_limit", "Daily Loss (₹)", "global/day", "rs",
            loss_used, None, status="AWAITING",
            note="opening capital not yet recorded",
            extra={"limit_pct": loss_pct},
        ))

    # #4 intraday capital bucket (₹ = pct × opening capital)
    intraday_pct = _float(capital.get("intraday_bucket_pct"))
    used_margin = round(cap_usage["margin_used"], 2)
    pending_margin = round(cap_usage["margin_reserved"], 2)
    if opening is not None and intraday_pct is not None:
        cap_limit = round(intraday_pct * opening, 2)
        rows.append(_row(
            "intraday_capital", "Intraday Capital (₹)", "global/instant", "rs",
            used_margin, cap_limit,
            extra={"pending": pending_margin, "limit_pct": intraday_pct,
                   "opening_capital": round(opening, 2)},
        ))
    else:
        rows.append(_row(
            "intraday_capital", "Intraday Capital (₹)", "global/instant", "rs",
            used_margin, None, status="AWAITING",
            note="opening capital not yet recorded",
            extra={"pending": pending_margin, "limit_pct": intraday_pct},
        ))

    # #5 max_consecutive_losses
    rows.append(_row(
        "max_consecutive_losses", "Consecutive Losses", "global/rolling", "count",
        db_reader.consecutive_loss_streak(cfg),
        _int(risk.get("max_consecutive_losses")),
    ))

    # #6 signal queue depth (from trader /health; unavailable when down)
    th = trader_health if trader_health is not None else metrics_client.get_trader_health(cfg)
    q_limit = _int(sq.get("capacity"))
    bp = _float(sq.get("backpressure_pct"))
    if th.get("trader_alive") and isinstance(th.get("health"), dict) and th["health"].get("queue_size") is not None:
        q_used = _int(th["health"].get("queue_size")) or 0
        rows.append(_row(
            "signal_queue", "Signal Queue", "global/instant", "count",
            q_used, q_limit,
            extra={"backpressure_at": round(bp * q_limit) if (bp and q_limit) else None},
        ))
    else:
        rows.append(_row(
            "signal_queue", "Signal Queue", "global/instant", "count",
            None, q_limit, status="UNAVAILABLE",
            note="trader down — live queue depth unavailable",
            extra={"backpressure_at": round(bp * q_limit) if (bp and q_limit) else None},
        ))

    # #7 delivery open positions (INERT while force_intraday_only)
    rows.append(_row(
        "max_open_delivery_positions", "Delivery Open", "global/concurrent", "count",
        db_reader.delivery_open_count(cfg),
        _int(risk.get("max_open_delivery_positions")),
        inert=force_intraday,
        note="inert (force_intraday_only)" if force_intraday else None,
    ))

    # #8 delivery daily trades (INERT while force_intraday_only)
    rows.append(_row(
        "max_daily_delivery_trades", "Delivery Trades", "global/day", "count",
        db_reader.delivery_daily_used(cfg, today),
        _int(risk.get("max_daily_delivery_trades")),
        inert=force_intraday,
        note="inert (force_intraday_only)" if force_intraday else None,
    ))

    return {
        "today": today,
        "config_source": sc.get("_source", "unknown") if isinstance(sc, dict) else "unknown",
        "opening_capital": round(opening, 2) if opening is not None else None,
        "rows": rows,
    }


def _int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
