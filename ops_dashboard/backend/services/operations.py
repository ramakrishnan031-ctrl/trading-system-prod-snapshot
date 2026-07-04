"""
ops_dashboard/backend/services/operations.py

G5d — Operations/Investigation composers (all read-only, mode=ro, EXISTING tables,
NO schema):
  * build_controls_summary — the read-only Controls page (L4: ZERO write path).
  * build_trade_logs        — trade-centric forensic feed (trades ⋈ scanner ⋈ score
                              ⋈ reconciliation_log actions), period-scoped.
  * build_activity          — the single merged attention feed (L10) over signals /
                              orders / trades / system_events / fm_ledger / kill.
Broker-side data is NEVER assembled here — it stays honestly UNAVAILABLE (G-1/P1).
`mode` is a displayed attribute only (parity).
"""
from __future__ import annotations

from typing import Optional

from ..readers import config_reader, db_reader
from . import freshness


# ─────────────────────────────────────────────────────────────────────────────
# Controls — READ-ONLY summary (L4). Composes config + kill-switch + strategies.
# ─────────────────────────────────────────────────────────────────────────────
def build_controls_summary(cfg: dict, today: Optional[str] = None) -> dict:
    today = today or freshness.ist_today_iso()
    sc = config_reader.get_system_config(cfg, today)
    sc = sc if isinstance(sc, dict) else {}
    risk = sc.get("risk") or {}
    ps = sc.get("position_sizing") or {}
    alerts_cfg = sc.get("alerts") or {}

    strategies = config_reader.get_strategies(cfg)
    enabled = sorted(n for n, s in strategies.items() if s.get("enabled"))
    ks = db_reader.get_kill_switch(cfg)

    force_intraday = bool(sc.get("force_intraday_only", True))
    trade_type = sc.get("trade_type")
    if force_intraday:
        mode_label = "Intraday Only"
    else:
        mode_label = {"BOTH": "Both", "DELIVERY": "Delivery Only",
                      "INTRADAY": "Intraday Only"}.get(trade_type, "Intraday Only")

    return {
        "today": today,
        "read_only": True,
        "trade_type": {"trade_type": trade_type, "force_intraday_only": force_intraday,
                       "label": mode_label},
        "strategies": {"enabled_count": len(enabled), "total": len(strategies),
                       "enabled": enabled},
        "telegram_alerts": {
            "configured": alerts_cfg.get("telegram_enabled") if alerts_cfg else None,
            "note": "shown from config; the bot is NOT probed (the .env is the single "
                    "token source after the 03-Jul shadow fix)",
        },
        "limits": {
            "max_daily_trades": risk.get("max_daily_trades"),
            "max_open_positions": risk.get("max_open_positions"),
            "max_concentration_pct": ps.get("max_concentration_pct"),
            "daily_loss_limit_pct": risk.get("daily_loss_limit_pct"),
        },
        "kill_switch": {"state": ks.get("state"), "reason": ks.get("reason"),
                        "since": ks.get("triggered_at"), "by": ks.get("triggered_by")},
        "future_controls": {
            "available": False,
            "note": "G3 Future Controls (mode/strategy/scanner toggles, runtime limit "
                    "edits, pause/resume) are NOT implemented — this page is a read-only "
                    "summary. A control plane requires G3 design; no write path exists (L4).",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Trade Logs — trade-centric forensic feed (period-scoped, full attribution).
# ─────────────────────────────────────────────────────────────────────────────
def build_trade_logs(cfg: dict, period: str = "week", from_date=None, to_date=None,
                     strategy=None, scanner=None, exit_reason=None) -> dict:
    frm, to = freshness.resolve_period(period, from_date, to_date)
    trades = db_reader.trades_in_range(cfg, frm, to, strategy=strategy)
    tscan = db_reader.scanner_for_trades(cfg, [t["trade_id"] for t in trades])
    scores = db_reader.screener_scores(cfg, [t["signal_id"] for t in trades])
    recon = db_reader.recon_actions_for_trades(cfg, [t["trade_id"] for t in trades])
    rows = []
    for t in trades:
        sc = tscan.get(t["trade_id"])
        if scanner and sc != scanner:
            continue
        if exit_reason and (t.get("exit_reason") or "") != exit_reason:
            continue
        ct = t.get("created_at") or ""
        rows.append({
            "trade_id": t["trade_id"], "trade_date": ct[:10], "trade_time": ct[11:19],
            "strategy": t["strategy"], "scanner": sc or "—", "symbol": t["symbol"],
            "direction": t["direction"], "status": t["status"],
            "exit_reason": t.get("exit_reason"), "net": t.get("net_pnl"),
            "system_score": scores.get(t.get("signal_id")),
            "recon_actions": recon.get(t["trade_id"], []),
        })
    return {"period": period, "from": frm, "to": to, "count": len(rows), "rows": rows,
            "log_drill_note": "open a trade's raw log lines via /api/logs?id=<trade_id> (log_reader ref_id)"}


# ─────────────────────────────────────────────────────────────────────────────
# Live Activity — the single merged attention feed (L10). Reuses existing readers.
# ─────────────────────────────────────────────────────────────────────────────
def _fmt(v):
    return "" if v is None else v


def build_activity(cfg: dict, today: Optional[str] = None, limit: int = 100) -> dict:
    today = today or freshness.ist_today_iso()
    feed = []

    for s in db_reader.list_signals(cfg, today, limit=30):
        feed.append({"ts": s.get("received_at"), "type": "SIGNAL", "ref_type": "signal",
                     "ref_id": s.get("signal_id"), "link": "/signals",
                     "summary": f"signal {_fmt(s.get('symbol'))} · {_fmt(s.get('strategy'))} · {_fmt(s.get('status'))}"})
    for o in db_reader.list_orders(cfg, today, limit=30):
        feed.append({"ts": o.get("placed_at"), "type": "ORDER", "ref_type": "order",
                     "ref_id": o.get("order_id"), "link": "/orders",
                     "summary": f"{_fmt(o.get('leg'))} {_fmt(o.get('status'))} · {_fmt(o.get('symbol'))}"})
    for t in db_reader.closed_trades_today(cfg, today):
        feed.append({"ts": t.get("exit_time"), "type": "TRADE", "ref_type": "trade",
                     "ref_id": t.get("trade_id"), "link": "/pnl",
                     "summary": f"closed {_fmt(t.get('symbol'))} · {_fmt(t.get('exit_reason'))} · net {_fmt(t.get('net_pnl'))}"})
    for e in db_reader.recent_events(cfg, limit=15):
        feed.append({"ts": e.get("timestamp"), "type": "SYSTEM", "ref_type": "event",
                     "ref_id": None, "link": "/audit",
                     "summary": _fmt(e.get("event_type")) + (f" [{e.get('scenario')}]" if e.get("scenario") else "")})
    for lg in db_reader.ledger_entries(cfg, today, limit=25):
        if lg.get("entry_type") == "RELEASE_USED":
            feed.append({"ts": lg.get("ts"), "type": "CAPITAL", "ref_type": "ledger",
                         "ref_id": lg.get("trade_id"), "link": "/capital-risk",
                         "summary": f"P&L Δ {_fmt(lg.get('pnl_delta'))} · {_fmt(lg.get('bucket'))}"})
    ks = db_reader.get_kill_switch(cfg)
    if ks.get("state") and ks.get("state") != "INACTIVE":
        feed.append({"ts": ks.get("triggered_at"), "type": "RISK", "ref_type": "kill",
                     "ref_id": None, "link": "/capital-risk",
                     "summary": f"KILL {ks.get('state')} · {_fmt(ks.get('reason'))}"})

    feed = [f for f in feed if f.get("ts")]
    feed.sort(key=lambda f: f["ts"] or "", reverse=True)
    return {"today": today, "count": len(feed), "types": sorted({f["type"] for f in feed}),
            "rows": feed[:max(1, int(limit))]}
