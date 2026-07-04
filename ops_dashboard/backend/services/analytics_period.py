"""
ops_dashboard/backend/services/analytics_period.py

G5c — the multi-period analytics engine shared by Strategy Ranking, Strategy
Health, Scanner Attribution, Trade Explorer and P&L Analytics. Pure aggregation
over the read-only period-scoped readers (db_reader.*_range); NO schema, NO mode
branch (parity — paper/live aggregate identically).

DOCUMENTED FORMULAS (reviewed by Web Claude):
  win_rate     = 100 × wins / (wins + losses)                       [None if 0 decided]
  avg_win      = win_sum / wins ;  avg_loss = |loss_sum| / losses
  expectancy   = (win% × avg_win) − (loss% × avg_loss)   ₹/decided-trade
  profit_factor= Σ winning_net / Σ |losing_net|                     [None if no losses]
  roi_pct      = 100 × net / Σ margin_reserved                      [None if no margin]
  opportunity_quality (scanner, 0-100) =
                 100 × accept_rate × trade_conversion × win_rate
                 accept_rate      = accepted / received  (webhook)
                 trade_conversion = min(1, trades / accepted)
                 win_rate         = wins / (wins+losses)
                 — any factor whose denominator is 0 contributes 0 (no evidence),
                   NEVER fabricated.
  health_state (precedence): Disabled(enabled False) → Silent(silence RED) →
                 Warning(badge RED, non-silence) → Quiet(badge YELLOW | silence
                 YELLOW) → Healthy(badge GREEN).
  health_score : Healthy 90 / Quiet 70 / Warning 40 / Silent 20 / Disabled None.
"""
from __future__ import annotations

from typing import Optional

from ..readers import config_reader, db_reader
from . import freshness, strategy_tower


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation primitives (pure)
# ─────────────────────────────────────────────────────────────────────────────
def _blank_agg(label_key: str, key) -> dict:
    return {label_key: key, "trades": 0, "wins": 0, "losses": 0, "net": 0.0,
            "gross": 0.0, "charges": 0.0, "win_sum": 0.0, "loss_sum": 0.0,
            "margin": 0.0, "best": None, "worst": None}


def _add(agg: dict, net: float, gross: float, charges: float, margin: float) -> None:
    agg["trades"] += 1
    agg["net"] = round(agg["net"] + net, 2)
    agg["gross"] = round(agg["gross"] + gross, 2)
    agg["charges"] = round(agg["charges"] + charges, 2)
    agg["margin"] = round(agg["margin"] + margin, 2)
    if net > 0:
        agg["wins"] += 1
        agg["win_sum"] = round(agg["win_sum"] + net, 2)
    elif net < 0:
        agg["losses"] += 1
        agg["loss_sum"] = round(agg["loss_sum"] + net, 2)
    agg["best"] = net if agg["best"] is None else max(agg["best"], net)
    agg["worst"] = net if agg["worst"] is None else min(agg["worst"], net)


def _metrics(agg: dict) -> dict:
    wins, losses = agg["wins"], agg["losses"]
    decided = wins + losses
    win_rate = round(100.0 * wins / decided, 1) if decided else None
    avg_win = (agg["win_sum"] / wins) if wins else 0.0
    avg_loss = abs(agg["loss_sum"] / losses) if losses else 0.0
    expectancy = (round((wins / decided) * avg_win - (losses / decided) * avg_loss, 2)
                  if decided else None)
    profit_factor = (round(agg["win_sum"] / abs(agg["loss_sum"]), 2)
                     if agg["loss_sum"] < 0 else None)
    roi_pct = round(100.0 * agg["net"] / agg["margin"], 2) if agg["margin"] > 0 else None
    avg_trade = round(agg["net"] / agg["trades"], 2) if agg["trades"] else 0.0
    out = dict(agg)
    out.update({"win_rate": win_rate, "expectancy": expectancy,
                "profit_factor": profit_factor, "roi_pct": roi_pct,
                "avg_trade": avg_trade, "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2)})
    return out


def _aggregate(rows: list, key_fn, label_key: str, scanners: Optional[dict] = None) -> list:
    groups: dict = {}
    for r in rows:
        k = key_fn(r)
        if k is None:
            continue
        g = groups.setdefault(k, _blank_agg(label_key, k))
        _add(g, float(r.get("net_pnl") or 0.0), float(r.get("gross_pnl") or 0.0),
             float(r.get("charges") or 0.0), float(r.get("margin_reserved") or 0.0))
    return [_metrics(g) for g in groups.values()]


def _period(cfg, period, from_date, to_date):
    frm, to = freshness.resolve_period(period, from_date, to_date)
    return frm, to


# ─────────────────────────────────────────────────────────────────────────────
# Strategy Ranking
# ─────────────────────────────────────────────────────────────────────────────
_RANK_MODES = ("net_pnl", "roi", "win_rate", "profit_factor", "trade_count")
_MODE_KEY = {"net_pnl": "net", "roi": "roi_pct", "win_rate": "win_rate",
             "profit_factor": "profit_factor", "trade_count": "trades"}


def build_strategy_ranking(cfg, period="today", from_date=None, to_date=None) -> dict:
    frm, to = _period(cfg, period, from_date, to_date)
    rows = db_reader.closed_trades_range(cfg, frm, to)
    metrics = _aggregate(rows, lambda r: r.get("strategy"), "strategy")
    by_name = {m["strategy"]: m for m in metrics}

    rankings = {}
    for mode in _RANK_MODES:
        key = _MODE_KEY[mode]
        # metric-less rows sink to the bottom; ties break stable by name.
        ordered = sorted(metrics, key=lambda m: (
            -(m[key] if m[key] is not None else float("-inf")) if m[key] is not None else float("inf"),
            m["strategy"]))
        rankings[mode] = [m["strategy"] for m in ordered]

    return {"period": period, "from": frm, "to": to, "modes": list(_RANK_MODES),
            "count": len(metrics), "rows": metrics, "rankings": rankings,
            "by_name": by_name}


# ─────────────────────────────────────────────────────────────────────────────
# P&L Analytics (per strategy / scanner / direction + equity curve)
# ─────────────────────────────────────────────────────────────────────────────
def build_pnl_analytics(cfg, period="today", from_date=None, to_date=None) -> dict:
    frm, to = _period(cfg, period, from_date, to_date)
    rows = db_reader.closed_trades_range(cfg, frm, to)
    scanners = db_reader.scanner_for_trades(cfg, [r["trade_id"] for r in rows])

    total = _blank_agg("scope", "total")
    for r in rows:
        _add(total, float(r.get("net_pnl") or 0.0), float(r.get("gross_pnl") or 0.0),
             float(r.get("charges") or 0.0), float(r.get("margin_reserved") or 0.0))

    per_strategy = _aggregate(rows, lambda r: r.get("strategy"), "strategy")
    per_direction = _aggregate(rows, lambda r: r.get("direction"), "direction")
    per_scanner = _aggregate(rows, lambda r: scanners.get(r.get("trade_id")) or "unattributed", "scanner")

    # realized equity curve over the range (cumulative net by exit_time asc)
    curve, cum = [], 0.0
    for r in sorted(rows, key=lambda r: r.get("exit_time") or ""):
        cum = round(cum + float(r.get("net_pnl") or 0.0), 2)
        curve.append({"ts": r.get("exit_time"), "cum_pnl": cum})

    return {
        "period": period, "from": frm, "to": to,
        "totals": _metrics(total),
        "per_strategy": sorted(per_strategy, key=lambda m: -m["net"]),
        "per_scanner": sorted(per_scanner, key=lambda m: -m["net"]),
        "per_direction": sorted(per_direction, key=lambda m: m["direction"]),
        "equity_curve": curve,
        "curve_note": "realized only (trades net over the period) — intraday unrealized MTM not included (G4)",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scanner Attribution (funnel + opportunity quality)
# ─────────────────────────────────────────────────────────────────────────────
def build_scanner_attribution(cfg, period="today", from_date=None, to_date=None) -> dict:
    frm, to = _period(cfg, period, from_date, to_date)
    webhook = db_reader.webhook_by_scanner_range(cfg, frm, to)
    sig_funnel = db_reader.signals_scanner_funnel_range(cfg, frm, to)
    scan_map = config_reader.get_scan_webhook_map(cfg)          # scanner -> strategy

    trades = db_reader.trades_in_range(cfg, frm, to)
    tscan = db_reader.scanner_for_trades(cfg, [t["trade_id"] for t in trades])
    trade_agg: dict = {}
    for t in trades:
        sc = tscan.get(t["trade_id"])
        if not sc:
            continue
        g = trade_agg.setdefault(sc, _blank_agg("scanner", sc))
        if t.get("net_pnl") is not None:          # closed → contributes to W/L/net
            _add(g, float(t["net_pnl"]), float(t.get("gross_pnl") or 0.0),
                 float(t.get("charges") or 0.0), float(t.get("margin_reserved") or 0.0))
        else:
            g["trades"] += 1                      # open trade counts toward conversion

    universe = set(webhook) | set(sig_funnel) | set(trade_agg)
    out = []
    for sc in sorted(universe):
        wh = webhook.get(sc, {"received": 0, "accepted": 0, "rejected": 0})
        sf = sig_funnel.get(sc, {"stored": 0, "accepted": 0})
        ta = trade_agg.get(sc, _blank_agg("scanner", sc))
        m = _metrics(ta)
        received, accepted = wh["received"], wh["accepted"]
        decided = ta["wins"] + ta["losses"]
        accept_rate = (accepted / received) if received else 0.0
        trade_conv = min(1.0, ta["trades"] / accepted) if accepted else 0.0
        win_rate = (ta["wins"] / decided) if decided else 0.0
        quality = round(100.0 * accept_rate * trade_conv * win_rate, 1)
        out.append({
            "scanner": sc,
            "linked_strategy": scan_map.get(sc) or "scanner-level (shared)",
            "received": received, "accepted": accepted, "rejected": wh["rejected"],
            "stored": sf["stored"], "orders": ta["trades"], "trades": ta["trades"],
            "wins": ta["wins"], "losses": ta["losses"], "net": ta["net"],
            "win_rate": m["win_rate"], "profit_factor": m["profit_factor"],
            "quality_score": quality,
            "funnel": {"signals": received, "accepted": accepted,
                       "orders": ta["trades"], "trades": ta["trades"]},
        })
    out.sort(key=lambda r: -r["net"])
    # medal rank by net (stable)
    for i, r in enumerate(out):
        r["rank"] = i + 1
    return {"period": period, "from": frm, "to": to, "count": len(out), "rows": out,
            "orders_note": "orders ≈ trades (one entry order per trade; per-scanner order rows not separately attributed)"}


# ─────────────────────────────────────────────────────────────────────────────
# Strategy Health (state mapping over the today tower + period signal counts)
# ─────────────────────────────────────────────────────────────────────────────
def health_state(enabled, badge: str, silence_color) -> str:
    if enabled is False:
        return "Disabled"
    if silence_color == "RED":
        return "Silent"
    if badge == "RED":
        return "Warning"
    if badge == "YELLOW" or silence_color == "YELLOW":
        return "Quiet"
    return "Healthy"


_HEALTH_SCORE = {"Healthy": 90, "Quiet": 70, "Warning": 40, "Silent": 20, "Disabled": None}


def build_strategy_health(cfg, period="week", from_date=None, to_date=None, now=None) -> dict:
    now = now or freshness.ist_now()
    today = freshness.ist_today_iso(now)
    tower = strategy_tower.build_strategy_tower(cfg, today, now)
    frm, to = _period(cfg, period, from_date, to_date)
    period_signals = db_reader.strategy_signal_counts_range(cfg, frm, to)

    rows = []
    counts = {"Healthy": 0, "Quiet": 0, "Warning": 0, "Silent": 0, "Disabled": 0}
    for r in tower["rows"]:
        badge = (r.get("scorecard") or {}).get("badge")
        sil = (r.get("silence") or {}).get("color")
        enabled = r["basic"]["enabled"]
        state = health_state(enabled, badge, sil)
        counts[state] = counts.get(state, 0) + 1
        rows.append({
            "strategy": r["basic"]["name"], "display_name": r["basic"]["display_name"],
            "state": state, "health_score": _HEALTH_SCORE.get(state),
            "badge": badge, "silence": sil,
            "reasons": (r.get("scorecard") or {}).get("reasons", []),
            "last_signal": r["health"]["last_signal"], "last_trade": r["health"]["last_trade"],
            "signals_today": r["signals"]["stored"], "orders_today": r["processing"]["created"],
            "trades_today": r["trading"]["open"] + r["trading"]["closed"],
            "rejections": {"risk": r["failures"]["risk_rej"], "capital": r["failures"]["capital_rej"],
                           "order": r["failures"]["order_rej"], "duplicate": r["failures"]["duplicate"],
                           "expired": r["failures"]["expired"]},
            "signals_period": int(period_signals.get(r["basic"]["name"], 0)),
        })
    return {"today": today, "period": period, "from": frm, "to": to,
            "counts": counts, "count": len(rows), "rows": rows}


# ─────────────────────────────────────────────────────────────────────────────
# Trade story (single-trade lifecycle assembly; honest "not captured" stages)
# ─────────────────────────────────────────────────────────────────────────────
def build_trade_story(cfg, trade_id: str) -> Optional[dict]:
    parts = db_reader.trade_story_parts(cfg, trade_id)
    if not parts:
        return None
    trade, signal = parts["trade"], parts["signal"]
    orders = parts["orders"]
    score = None
    if signal:
        score = db_reader.screener_scores(cfg, [signal["signal_id"]]).get(signal["signal_id"])

    entry = next((o for o in orders if o["leg"] in ("ENTRY", "CO")), None)
    exit_o = next((o for o in orders if o["leg"] in ("SL", "TGT", "EOD")
                   and o["status"] == "COMPLETE"), None)

    NC = "not captured (G-2)"      # per-stage validation/risk/capital timings unpersisted
    steps = [
        {"label": "Signal Received", "ts": signal["received_at"] if signal else None, "state": "done" if signal else "pending"},
        {"label": "Validation", "ts": None, "state": "unknown", "note": NC},
        {"label": "Risk", "ts": None, "state": "unknown", "note": NC},
        {"label": "Capital", "ts": None, "state": "unknown", "note": NC},
        {"label": "Order Created", "ts": entry["placed_at"] if entry else trade.get("created_at"), "state": "done" if entry else "pending"},
        {"label": "Fill", "ts": entry["filled_at"] if entry else trade.get("entry_time"),
         "state": "done" if (entry and entry.get("filled_at")) else "pending"},
        {"label": "Exit", "ts": trade.get("exit_time"),
         "state": "done" if trade.get("exit_time") else "pending",
         "note": trade.get("exit_reason") or ""},
    ]
    return {
        "trade_id": trade_id,
        "system_score": score,
        "scanner": signal["scanner"] if signal else None,
        "raw_payload": signal["webhook_payload"] if signal else None,
        "trade": trade, "signal": signal, "orders": orders, "execution": parts["execution"],
        "timeline": steps,
        "timeline_note": "Validation/Risk/Capital stage timings are not persisted (G-2) — shown honestly, never invented.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Trade Explorer table (per-trade forensic rows)
# ─────────────────────────────────────────────────────────────────────────────
def build_trade_explorer(cfg, period="week", from_date=None, to_date=None,
                         strategy=None, direction=None, symbol=None) -> dict:
    frm, to = _period(cfg, period, from_date, to_date)
    trades = db_reader.trades_in_range(cfg, frm, to, strategy=strategy,
                                       direction=direction, symbol=symbol)
    scanners = db_reader.scanner_for_trades(cfg, [t["trade_id"] for t in trades])
    scores = db_reader.screener_scores(cfg, [t["signal_id"] for t in trades])
    rows = []
    for t in trades:
        ct = (t.get("created_at") or "")
        rows.append({
            "trade_id": t["trade_id"], "trade_date": ct[:10], "trade_time": ct[11:19],
            "strategy": t["strategy"], "scanner": scanners.get(t["trade_id"]) or "—",
            "symbol": t["symbol"], "direction": t["direction"], "status": t["status"],
            "system_score": scores.get(t.get("signal_id")),
            "qty": t.get("qty_filled"), "entry": t.get("entry_actual_price"),
            "exit": t.get("exit_price"), "net": t.get("net_pnl"),
            "exit_reason": t.get("exit_reason"),
        })
    return {"period": period, "from": frm, "to": to, "count": len(rows), "rows": rows}
