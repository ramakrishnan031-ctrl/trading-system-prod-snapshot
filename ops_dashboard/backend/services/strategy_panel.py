"""
ops_dashboard/backend/services/strategy_panel.py

Per-strategy view for today: enabled/disabled, signals, trades, wins/losses,
net P&L, win-rate, open vs per-strategy concurrent cap, ranked by net P&L.
Merges config (config_reader) + today's trades/signals (db_reader).
`mode` is never branched on — the same query serves paper and live.
"""
from __future__ import annotations

from typing import Optional

from ..readers import config_reader, db_reader
from . import freshness


def _win_rate(wins: int, losses: int) -> Optional[float]:
    decided = wins + losses
    if decided <= 0:
        return None
    return round(100.0 * wins / decided, 1)


def build_strategy_panel(cfg: dict, today: Optional[str] = None, now=None) -> dict:
    now = now or freshness.ist_now()
    today = today or freshness.ist_today_iso(now)

    strategies = config_reader.get_strategies(cfg)
    trade_stats = db_reader.strategy_trade_stats(cfg, today)
    signal_counts = db_reader.strategy_signal_counts(cfg, today)

    rows = []
    # Union of configured strategies and any strategy that traded/signalled today.
    names = set(strategies) | set(trade_stats) | set(signal_counts)
    for name in names:
        conf = strategies.get(name, {})
        ts = trade_stats.get(name, {"trades": 0, "wins": 0, "losses": 0,
                                    "net_pnl": 0.0, "open_count": 0})
        cap = int(conf.get("max_concurrent_positions", 2))
        rows.append({
            "name": name,
            "display_name": conf.get("display_name", name),
            "enabled": bool(conf.get("enabled", True)) if name in strategies else None,
            "direction": conf.get("direction"),
            "order_protocol": conf.get("order_protocol"),
            "signals": int(signal_counts.get(name, 0)),
            "trades": int(ts["trades"]),
            "wins": int(ts["wins"]),
            "losses": int(ts["losses"]),
            "win_rate": _win_rate(int(ts["wins"]), int(ts["losses"])),
            "net_pnl": round(float(ts["net_pnl"]), 2),
            "open_count": int(ts["open_count"]),
            "max_concurrent": cap,
            "concurrent_remaining": max(0, cap - int(ts["open_count"])),
            "configured": name in strategies,
        })

    # Rank by net P&L desc; enabled strategies with activity first for a stable read.
    rows.sort(key=lambda r: (r["net_pnl"], r["trades"]), reverse=True)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i

    return {
        "today": today,
        "count": len(rows),
        "enabled_count": sum(1 for r in rows if r["enabled"]),
        "rows": rows,
    }
