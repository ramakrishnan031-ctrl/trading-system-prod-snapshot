"""
ops_dashboard/backend/services/strategy_tower.py

Strategy Control Tower (M6, replaces G2a strategy_panel). Per strategy, the
seven Rama-specified groups (BASIC / SIGNALS / PROCESSING / TRADING /
PERFORMANCE / RISK / HEALTH) + four ranking views (net P&L / win rate /
expectancy / success rate).

Attribution (docs/G2b1_strategy_attribution.md):
  R1 — webhook "received" = Σ webhook_audit over the strategy's scanner set
       (exact under the 1:1/N:1 map). Scanners ABSENT from the map surface as
       separate rows labeled "scanner-level (shared)" — never split/guessed.
  R4 — ROI% = net_pnl / Σ margin_reserved over trades created today.
  R6 — last_failure = max(latest FAILED order, latest losing close).
`mode` is a data attribute from `session` — never a code branch (parity).
"""
from __future__ import annotations

from typing import Optional

from ..readers import config_reader, db_reader
from . import freshness, strategy_score

RANK_KEYS = ("net_pnl", "win_rate", "expectancy", "success_rate")

# HEALTH staleness thresholds (seconds) — colored only when activity is
# expected now (freshness drives GRAY-vs-RED semantics like the pipeline).
_STALE_WARN_SEC = 30 * 60
_STALE_RED_SEC = 2 * 60 * 60


def _family_of(name: str, direction: str | None = None) -> str:
    """UI grouping key (G5b): base strategy name without its direction token
    (gap_fade_long → gap_fade). Uses the STRUCTURED direction (StrategyConfig.direction,
    surfaced as basic.direction) to decide the token to strip — NOT a blind `_long`/
    `_short` suffix parse (retired 17-Jul-2026). Behaviour-identical for the current
    naming (LONG↔`_long`, SHORT↔`_short`); a strategy whose direction is unknown simply
    groups under its own full name. Pure; no config dependency beyond the passed value."""
    d = str(direction or "").strip().upper()
    if d in ("LONG", "SHORT"):
        suf = "_" + d.lower()
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def _trade_type(intent) -> Optional[str]:
    """Map the strategy YAML `intent` to the Screen-03 Trade Type label
    (read-only display field; INTRADAY→Intraday, POSITIONAL/DELIVERY→Delivery)."""
    t = str(intent or "").strip().upper()
    if t in ("POSITIONAL", "DELIVERY", "CNC"):
        return "Delivery"
    if t == "INTRADAY":
        return "Intraday"
    return None


def _win_rate(wins: int, losses: int) -> Optional[float]:
    decided = wins + losses
    return round(100.0 * wins / decided, 1) if decided > 0 else None


def _expectancy(wins: int, losses: int, win_sum: float, loss_sum: float) -> Optional[float]:
    """((win% × avgWin) − (loss% × |avgLoss|)) over decided trades, ₹/trade."""
    decided = wins + losses
    if decided <= 0:
        return None
    win_pct = wins / decided
    loss_pct = losses / decided
    avg_win = (win_sum / wins) if wins > 0 else 0.0
    avg_loss = abs(loss_sum / losses) if losses > 0 else 0.0
    return round(win_pct * avg_win - loss_pct * avg_loss, 2)


def _staleness(ts: Optional[str], expected: bool, now) -> str:
    """OK / WARN / STALE / NONE for a HEALTH timestamp."""
    if ts is None:
        return "NONE"
    age = freshness.age_seconds(ts, now)
    if age is None:
        return "NONE"
    if not expected:
        return "OK"          # silence is normal off-hours
    if age > _STALE_RED_SEC:
        return "STALE"
    if age > _STALE_WARN_SEC:
        return "WARN"
    return "OK"


def build_strategy_tower(cfg: dict, today: Optional[str] = None, now=None) -> dict:
    now = now or freshness.ist_now()
    today = today or freshness.ist_today_iso(now)

    strategies = config_reader.get_strategies(cfg)
    scan_map = config_reader.get_scan_webhook_map(cfg)          # scanner -> strategy
    session = db_reader.get_session_info(cfg)
    mode = session.get("mode")

    funnel = db_reader.strategy_signal_funnel(cfg, today)        # stored-signal buckets
    by_scanner = db_reader.webhook_by_scanner(cfg, today)        # webhook aggregates
    order_stats = db_reader.strategy_order_stats(cfg, today)
    perf = db_reader.strategy_perf_stats(cfg, today)
    open_cap = db_reader.strategy_open_capital(cfg)
    opening = db_reader.opening_capital(cfg, today)
    reject_split = db_reader.strategy_reject_split(cfg, today)   # failure strip
    loss_streaks = db_reader.strategy_loss_streaks(cfg)          # scorecard input
    sltgt = db_reader.strategy_sltgt_hits(cfg, today)            # G5b: SL/TGT exit counts
    score_th = cfg.get("scorecard") or {}
    silence_th = cfg.get("silence") or {}

    # Strategy -> its scanner set (N:1 supported); unmapped scanners -> shared rows.
    scanners_of: dict = {}
    for scanner, strat in scan_map.items():
        scanners_of.setdefault(strat, []).append(scanner)
    unmapped = sorted(set(by_scanner) - set(scan_map))

    sys_cfg = config_reader.get_system_config(cfg, today)
    global_max_open = None
    risk_cfg = sys_cfg.get("risk", {}) if isinstance(sys_cfg, dict) else {}
    try:
        global_max_open = int(risk_cfg.get("max_open_positions")) if risk_cfg.get("max_open_positions") is not None else None
    except (TypeError, ValueError):
        global_max_open = None

    names = set(strategies) | set(funnel) | set(perf) | set(order_stats) | set(open_cap)
    rows = []
    for name in sorted(names):
        conf = strategies.get(name, {})
        f = funnel.get(name, {"accepted": 0, "rejected": 0, "duplicated": 0,
                              "expired": 0, "stored": 0, "last_signal": None})
        o = order_stats.get(name, {"created": 0, "submitted": 0, "filled": 0,
                                   "rejected": 0, "cancelled": 0, "last_failed_ts": None})
        p = perf.get(name, {"trades": 0, "open_trades": 0, "closed_trades": 0,
                            "wins": 0, "losses": 0, "net_pnl": 0.0, "gross_pnl": 0.0,
                            "win_sum": 0.0, "loss_sum": 0.0, "best_trade": None,
                            "worst_trade": None, "capital_used_today": 0.0,
                            "last_trade_ts": None, "last_win_ts": None, "last_loss_ts": None})
        oc = open_cap.get(name, {"margin": 0.0, "open_count": 0})

        my_scanners = scanners_of.get(name, [])
        received = sum(by_scanner.get(s, {}).get("received", 0) for s in my_scanners)
        wh_accepted = sum(by_scanner.get(s, {}).get("accepted", 0) for s in my_scanners)

        wins, losses = int(p["wins"]), int(p["losses"])
        win_rate = _win_rate(wins, losses)
        expectancy = _expectancy(wins, losses, float(p["win_sum"]), float(p["loss_sum"]))
        loss_sum = float(p["loss_sum"])
        profit_factor = round(float(p["win_sum"]) / abs(loss_sum), 2) if loss_sum < 0 else None
        created, filled = int(o["created"]), int(o["filled"])
        success_rate = round(100.0 * filled / created, 1) if created > 0 else None

        cap_used_today = float(p["capital_used_today"])
        net = round(float(p["net_pnl"]), 2)
        gross = round(float(p["gross_pnl"]), 2)
        roi_pct = round(100.0 * net / cap_used_today, 2) if cap_used_today > 0 else None

        max_conc = int(conf.get("max_concurrent_positions", 2))
        open_count = int(oc["open_count"])
        # Capital remaining: no per-strategy capital cap is configured today →
        # vs the GLOBAL intraday bucket (documented; per-strategy cap slot ready).
        intraday_pct = None
        cap_cfg = sys_cfg.get("capital", {}) if isinstance(sys_cfg, dict) else {}
        try:
            intraday_pct = float(cap_cfg.get("intraday_bucket_pct")) if cap_cfg.get("intraday_bucket_pct") is not None else None
        except (TypeError, ValueError):
            intraday_pct = None
        bucket_limit = round(intraday_pct * opening, 2) if (intraday_pct and opening) else None
        capital_remaining = round(bucket_limit - float(oc["margin"]), 2) if bucket_limit is not None else None

        # HEALTH (R6): last failure = max(FAILED order, losing close).
        last_failure = max(
            (t for t in (o["last_failed_ts"], p["last_loss_ts"]) if t), default=None
        )
        expected_sig = freshness.expected_activity(cfg, "received", now)
        expected_trade = freshness.expected_activity(cfg, "orders_created", now)

        # ── G2b-2 §1.1/1.2: scorecard + silence (pure, threshold-driven) ──
        sig_age_sec = freshness.age_seconds(f["last_signal"], now)
        sig_age_min = round(sig_age_sec / 60.0, 1) if sig_age_sec is not None else None
        enabled_flag = bool(conf.get("enabled", True)) if name in strategies else None
        in_entry = freshness.in_entry_window(cfg, now)
        capacity_pct = (round(100.0 * open_count / max_conc, 1) if max_conc > 0 else None)
        badge = strategy_score.score({
            "enabled": enabled_flag,
            "expected_activity": expected_sig,
            "last_signal_age_min": sig_age_min,
            "failed_orders": int(o["rejected"]),
            "consec_losses": int(loss_streaks.get(name, 0)),
            "net_pnl": net,
            "win_rate": win_rate,
            "closed_decided": wins + losses,
            "capacity_used_pct": capacity_pct,
            "in_entry_window": in_entry,
        }, score_th, silence_th)
        silence = strategy_score.silence_tier(sig_age_min, expected_sig, silence_th)
        rej = reject_split.get(name, {"risk_rej": 0, "capital_rej": 0})

        rows.append({
            "scorecard": badge,                                  # §1.1 badge + reasons
            "silence": silence,                                  # §1.2 last-signal tier
            "funnel": {                                          # §1.3 mini funnel
                "received": received,
                "accepted": int(f["accepted"]),
                "orders_created": created,
                "filled": filled,
                "trades_closed": int(p["closed_trades"]),
            },
            "failures": {                                        # §1.4 failure strip
                "risk_rej": int(rej["risk_rej"]),
                "capital_rej": int(rej["capital_rej"]),
                "order_rej": int(o["rejected"]),
                "duplicate": int(f["duplicated"]),
                "expired": int(f["expired"]),
            },
            "capital_view": {                                    # §1.6 (honest basis)
                "allocation_configured": None,                   # no per-strategy ₹ cap exists
                "allocation_basis": "global bucket",
                "capital_used": round(float(oc["margin"]), 2),
                "capital_remaining": capital_remaining,
                "positions_configured": max_conc,
                "positions_used": open_count,
                "positions_remaining": max(0, max_conc - open_count),
            },
            "basic": {
                "name": name,
                "display_name": conf.get("display_name", name),
                "enabled": bool(conf.get("enabled", True)) if name in strategies else None,
                "direction": conf.get("direction"),
                "trade_type": _trade_type(conf.get("intent")),   # Intraday | Delivery (read-only)
                "scanners": my_scanners,
                "mode": mode,
                "configured": name in strategies,
            },
            "signals": {
                "received": received,                 # webhook level (incl. pre-insert dupes)
                "webhook_accepted": wh_accepted,
                "stored": int(f["stored"]),
                "accepted": int(f["accepted"]),
                "rejected": int(f["rejected"]),
                "duplicated": int(f["duplicated"]),
                "expired": int(f["expired"]),
                "attribution": "exact",               # scanner set resolved 1:1/N:1
            },
            "processing": {
                "created": created, "submitted": int(o["submitted"]),
                "filled": filled, "rejected": int(o["rejected"]),
                "cancelled": int(o["cancelled"]),
            },
            "trading": {
                "open": int(p["open_trades"]), "closed": int(p["closed_trades"]),
                "wins": wins, "losses": losses, "win_rate": win_rate,
                "avg_win": round(float(p["win_sum"]) / wins, 2) if wins > 0 else None,
                "avg_loss": round(float(p["loss_sum"]) / losses, 2) if losses > 0 else None,
            },
            "performance": {
                "net_pnl": net, "gross_pnl": gross,
                "charges": round(gross - net, 2),
                "roi_pct": roi_pct,                   # R4: net / Σ margin_reserved today
                "capital_used_today": round(cap_used_today, 2),
                "best_trade": p["best_trade"], "worst_trade": p["worst_trade"],
                "expectancy": expectancy,
                "profit_factor": profit_factor,       # G5b (additive)
                # ⭐ ADDITIVE (16-Aug, Screen 21): the two OPERANDS behind
                # profit_factor, published so a consumer can build a bounded
                # profit share without re-deriving them from rounded averages.
                # ⛔ Screen 21's Profitability component reads these; computing
                # it from `avg_win × wins` would inherit two 2-dp roundings.
                "win_sum": round(float(p["win_sum"]), 2),
                "loss_sum": round(float(p["loss_sum"]), 2),   # ≤ 0 by construction
            },
            "risk": {
                "capital_used": round(float(oc["margin"]), 2),   # open reservations now
                "capital_remaining": capital_remaining,
                "capital_cap_basis": "global_intraday_bucket",   # no per-strategy ₹ cap configured
                "active_positions": open_count,
                "max_concurrent": max_conc,
                "position_capacity_remaining": max(0, max_conc - open_count),
                "global_max_open_positions": global_max_open,
            },
            "health": {
                "last_signal": f["last_signal"],
                "last_signal_staleness": _staleness(f["last_signal"], expected_sig, now),
                "last_trade": p["last_trade_ts"],
                "last_trade_staleness": _staleness(p["last_trade_ts"], expected_trade, now),
                "last_successful_trade": p["last_win_ts"],
                "last_failure": last_failure,         # R6
                "last_failed_order": o["last_failed_ts"],
                "last_losing_close": p["last_loss_ts"],
            },
            "_rank_metrics": {
                "net_pnl": net,
                "win_rate": win_rate if win_rate is not None else -1.0,
                "expectancy": expectancy if expectancy is not None else float("-inf"),
                "success_rate": success_rate if success_rate is not None else -1.0,
            },
            "success_rate": success_rate,
            "family": _family_of(name, conf.get("direction")),  # G5b: UI grouping (canonical direction, not name-parse)
            "sl_tgt_hits": sltgt.get(name, {"sl_hits": 0, "tgt_hits": 0}),  # G5b (additive)
        })

    # Rankings: each key desc; ties (and metric-less rows, coerced to -1e18)
    # break to stable name order.
    rankings = {}
    for key in RANK_KEYS:
        ordered = sorted(
            rows,
            key=lambda r: (
                -(r["_rank_metrics"][key] if r["_rank_metrics"][key] != float("-inf") else -1e18),
                r["basic"]["name"],
            ),
        )
        rankings[key] = [r["basic"]["name"] for r in ordered]

    # Default rank (BASIC.rank + dashboard strip) = net P&L view.
    default_order = {name: i + 1 for i, name in enumerate(rankings["net_pnl"])}
    for r in rows:
        r["basic"]["rank"] = default_order[r["basic"]["name"]]
        del r["_rank_metrics"]
    rows.sort(key=lambda r: r["basic"]["rank"])

    # Unattributable webhook rows (R1): scanner not in the map → labeled bucket.
    scanner_level = [{
        "scanner": s,
        "label": "scanner-level (shared)",
        "received": by_scanner[s]["received"],
        "accepted": by_scanner[s]["accepted"],
        "rejected": by_scanner[s]["rejected"],
        "last_ts": by_scanner[s]["last_ts"],
    } for s in unmapped]

    return {
        "today": today,
        "mode": mode,
        "count": len(rows),
        "enabled_count": sum(1 for r in rows if r["basic"]["enabled"]),
        "rows": rows,
        "rankings": rankings,
        "scanner_level": scanner_level,
        # The artwork's two KPI sparklines (V1). Additive: no existing key moves.
        "sparks": build_strategy_sparks(cfg, today),
    }


#: The two sparklines the approved Screen-03 artwork draws, on TOTAL P&L and
#: WIN RATE. ⛔ NOTHING IS SYNTHESISED: both are the SAME sequence — today's
#: CLOSED trades in the order they actually closed — read through the existing
#: `activity_trade_exits`, which Screen 18 already uses. The P&L line is the
#: running cumulative `net_pnl`; the win-rate line is the running win % after
#: each close. Neither is smoothed, back-filled or interpolated.
#:
#: ⛔ NO PER-STRATEGY ALLOCATION OR CAPITAL FIGURE IS INVOLVED — D1/D2 remain
#: pending and this function deliberately touches neither.
DASH_SPARK_MIN_POINTS = 2


def build_strategy_sparks(cfg: dict, today: Optional[str] = None) -> dict:
    """`{pnl, winrate}` series for the Screen-03 KPI deck.

    ⛔ `available` IS FALSE BELOW TWO POINTS and the card then draws NO line. A
    single point is not a shape, and a flat line along the axis is still a drawn
    chart — a reader takes a drawn chart as a measurement of trend. The number
    above it already states the value.
    """
    today = today or freshness.ist_today_iso()
    exits = db_reader.activity_trade_exits(cfg, today) or []
    # `activity_trade_exits` returns newest-first; the series runs forwards.
    ordered = sorted(
        (e for e in exits if e.get("exit_time")),
        key=lambda e: (e["exit_time"], str(e.get("trade_id") or "")),
    )

    pnl_points, wr_points = [], []
    cum, wins, closed = 0.0, 0, 0
    for e in ordered:
        net = e.get("net_pnl")
        if net is not None:
            cum += float(net)
            if float(net) > 0:
                wins += 1
        closed += 1
        pnl_points.append(round(cum, 2))
        wr_points.append(round(100.0 * wins / closed, 2))

    enough = len(ordered) >= DASH_SPARK_MIN_POINTS
    return {
        "pnl": {"points": pnl_points, "available": enough,
                "closed_trades": len(ordered), "basis": "cumulative net P&L per close, today"},
        "winrate": {"points": wr_points, "available": enough,
                    "closed_trades": len(ordered), "basis": "running win % per close, today"},
    }


def strategy_detail(cfg: dict, name: str, today: Optional[str] = None, now=None) -> Optional[dict]:
    """Single strategy row (same shape as a tower row) or None if unknown."""
    tower = build_strategy_tower(cfg, today, now)
    for r in tower["rows"]:
        if r["basic"]["name"] == name:
            return {"today": tower["today"], "strategy": r,
                    "rankings": {k: v.index(name) + 1 for k, v in tower["rankings"].items() if name in v}}
    return None
