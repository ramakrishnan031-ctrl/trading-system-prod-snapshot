"""
ops_dashboard/backend/services/summary_bar.py

Assembles the pinned top summary bar (G1 §5): mode, trader liveness, kill-switch
state, market phase, and the day's headline counters. Every number is read from
the same sources the detail views use, so the bar cannot disagree with them.
`mode` is a data attribute (from `session` / snapshot) — never a code branch (parity).
"""
from __future__ import annotations

from typing import Optional

from ..readers import config_reader, db_reader, metrics_client
from . import freshness


def build_summary(cfg: dict, today: Optional[str] = None, now=None,
                  trader_health: Optional[dict] = None) -> dict:
    now = now or freshness.ist_now()
    today = today or freshness.ist_today_iso(now)

    session = db_reader.get_session_info(cfg)
    # Header identity: resolve Broker ID + Client Name from the account roster
    # (config/accounts.csv, read-only) for the session's active account.
    account = config_reader.active_account(cfg, session.get("account_id"))
    ks = db_reader.get_kill_switch(cfg)
    th = trader_health if trader_health is not None else metrics_client.get_trader_health(cfg)
    funnel = db_reader.webhook_funnel(cfg, today)
    cap = db_reader.capital_usage(cfg)

    opening = db_reader.opening_capital(cfg, today)

    return {
        "mode": session.get("mode"),               # PAPER | LIVE (data attribute)
        "account_id": session.get("account_id") or account.get("account_id"),   # Broker ID
        "client_name": account.get("label"),       # from config/accounts.csv (roster label)
        "broker": session.get("broker") or account.get("broker"),
        "trade_type": session.get("trade_type"),
        "trader_alive": bool(th.get("trader_alive")),
        "kill_switch": {
            "state": ks.get("state", "INACTIVE"),
            "reason": ks.get("reason"),
            "triggered_at": ks.get("triggered_at"),
            "halted": ks.get("state", "INACTIVE") in ("SOFT_KILL", "HARD_KILL"),
        },
        "phase": freshness.phase(cfg, now),
        "counters": {
            "received": funnel["received"],
            "validated": funnel["validated"],
            "trades_today": db_reader.daily_trades_used(cfg, today),
            "open_positions": db_reader.open_positions_count(cfg),
            "net_pnl_today": round(cap["realized_pnl_today"], 2),
            "realized_loss_today": round(db_reader.realized_loss_today(cfg, today), 2),
            "opening_capital": round(opening, 2) if opening is not None else None,
            "profit_factor": db_reader.profit_factor_today(cfg, today),   # G5b (additive)
        },
        "ist_now": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
