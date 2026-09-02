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

from datetime import date, timedelta
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
# P&L Analytics (Screen 09)
#
# SCANNER IS DELIBERATELY ABSENT (Rama, 14-Aug): "Strategy and Scanner represent
# the same source identity in this system." Showing both duplicated one fact in
# four places (column, filter, ranking panel, best/worst). ⛔ Do NOT reintroduce a
# scanner column, filter, ranking or attribution slice here. `build_scanner_
# attribution` (Scanner Attribution screen) is UNTOUCHED and keeps its own use.
#
# TIME BUCKETS are IST because exit_time/entry_time are written by the system's
# own IST clock (core.time_authority) — ⛔ never the server locale.
# ─────────────────────────────────────────────────────────────────────────────

# Trading-session buckets. The last one runs to 15:30 (close), so it is 90 min
# wide by design — it is a SESSION boundary, ⛔ not an even hourly split.
_TOD_BUCKETS = (("09:15–10:00", "09:15", "10:00"), ("10:00–11:00", "10:00", "11:00"),
                ("11:00–12:00", "11:00", "12:00"), ("12:00–13:00", "12:00", "13:00"),
                ("13:00–14:00", "13:00", "14:00"), ("14:00–15:30", "14:00", "15:30"))
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Filterable dimensions. Scanner is absent on purpose (see header).
_PNL_FILTERS = ("strategy", "symbol", "trade_type", "direction")


def _trade_type(r: dict) -> str:
    """Product from the ENTRY order, or the explicit UNKNOWN bucket.

    ⛔ NEVER defaults to MIS/CNC. A trade with no ENTRY order row has NO product,
    and inventing one would put fabricated data in a filter. UNKNOWN keeps such a
    trade VISIBLE and countable rather than silently dropping it."""
    return (r.get("product") or "UNKNOWN").upper()


def _dow(ts) -> Optional[str]:
    """IST weekday label from a 'YYYY-MM-DD...' timestamp, by Zeller — no tz math
    is needed because the stored timestamp is ALREADY IST."""
    s = str(ts or "")[:10]
    if len(s) != 10:
        return None
    try:
        y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
    except ValueError:
        return None
    if m < 3:
        m += 12
        y -= 1
    k, j = y % 100, y // 100
    h = (d + (13 * (m + 1)) // 5 + k + k // 4 + j // 4 + 5 * j) % 7   # 0=Sat
    return _DOW[(h + 5) % 7]


def _tod_bucket(ts) -> Optional[str]:
    """Session bucket for an IST timestamp. Outside 09:15–15:30 → None (counted
    separately rather than forced into a bucket that did not happen)."""
    s = str(ts or "")
    hhmm = s[11:16] if len(s) >= 16 else ""
    if len(hhmm) != 5 or hhmm[2] != ":":
        return None
    for label, lo, hi in _TOD_BUCKETS:
        if lo <= hhmm < hi:
            return label
    return None


def _dow_buckets(rows: list) -> tuple:
    """Weekday buckets for the day-of-week heatmap.

    ⛔ FIXED 15-Aug — the first version rendered `_DOW[:5]` (Mon–Fri) UNCONDITIONALLY,
    which SILENTLY DROPPED any trade whose exit fell on a Saturday or Sunday: the
    panel showed all dashes while `totals.trades` showed 4, i.e. one panel
    describing a different population with nothing on screen saying so — exactly
    the failure this screen is built to prevent.

    ⭐ Mon–Fri is kept as the base because the market trades Mon–Fri and the
    approved design shows five columns; a weekend bucket is appended ONLY when it
    actually holds trades. So a normal week renders exactly as designed, and a
    weekend close becomes VISIBLE rather than vanishing.

    ⚠️ Found by rendering the page after midnight rolled the clock to a Saturday —
    ⛔ not by the suite, which had been green all evening on a Friday. The four
    tests that now guard it are CALENDAR-GATED and would have passed forever on a
    weekday run."""
    base = list(_DOW[:5])
    seen = {_dow(r.get("exit_time")) for r in rows}
    for wd in _DOW[5:]:                      # Sat, Sun — appended only if observed
        if wd in seen:
            base.append(wd)
    return tuple(base)


def _apply_pnl_filters(rows: list, f: dict) -> list:
    """THE single filter gate. Every panel on Screen 09 is derived from the list
    this returns, so KPIs / table / rankings / curve / drawdown / heatmaps /
    attribution CANNOT describe different populations (§11)."""
    out = rows
    if f.get("strategy"):
        out = [r for r in out if (r.get("strategy") or "") == f["strategy"]]
    if f.get("symbol"):
        out = [r for r in out if (r.get("symbol") or "").upper() == f["symbol"].upper()]
    if f.get("direction"):
        out = [r for r in out if (r.get("direction") or "").upper() == f["direction"].upper()]
    if f.get("trade_type"):
        out = [r for r in out if _trade_type(r) == f["trade_type"].upper()]
    return out


def _curve(rows: list, key: str) -> list:
    """Cumulative realized series, chronological by exit_time. Each trade appears
    EXACTLY once (we iterate the filtered list, never a re-query), so gross and
    net curves are the same trades under two measures."""
    cum, out = 0.0, []
    for r in sorted(rows, key=lambda x: str(x.get("exit_time") or "")):
        cum = round(cum + float(r.get(key) or 0.0), 2)
        out.append({"ts": r.get("exit_time"), "cum": cum})
    return out


def _drawdown(series: list) -> dict:
    """Peak-to-trough on the SAME sequence the curve draws.

    ⛔ Returns None (not 0.0) when a figure has no meaning: with no losing streak
    there IS no drawdown, and reporting 0.00 would read as 'measured, and it was
    zero' rather than 'never went underwater'. `available` carries that."""
    if not series:
        return {"available": False, "reason": "no closed trades in the selected period",
                "max_drawdown": None, "current_drawdown": None,
                "recovery_pct": None, "duration": None, "from": None, "to": None}
    peak = series[0]["cum"]
    peak_ts = series[0]["ts"]
    max_dd, max_from, max_to = 0.0, None, None
    for p in series:
        if p["cum"] > peak:
            peak, peak_ts = p["cum"], p["ts"]
        dd = round(p["cum"] - peak, 2)
        if dd < max_dd:
            max_dd, max_from, max_to = dd, peak_ts, p["ts"]
    cur_dd = round(series[-1]["cum"] - max(x["cum"] for x in series), 2)
    if max_dd == 0.0:
        return {"available": False, "reason": "never went below its running peak in this period",
                "max_drawdown": None, "current_drawdown": None,
                "recovery_pct": None, "duration": None, "from": None, "to": None}
    recovery = round(100.0 * (1.0 - abs(cur_dd) / abs(max_dd)), 2)
    return {"available": True, "reason": None,
            "max_drawdown": max_dd, "current_drawdown": cur_dd,
            "recovery_pct": recovery, "duration": _span(max_from, max_to),
            "from": max_from, "to": max_to}


def _span(a, b) -> Optional[str]:
    """'2h 15m' between two IST timestamps; None if either is unusable."""
    def _mins(ts):
        s = str(ts or "")
        if len(s) < 16 or s[13] != ":":
            return None
        try:
            return int(s[11:13]) * 60 + int(s[14:16]) + int(s[8:10]) * 1440
        except ValueError:
            return None
    ma, mb = _mins(a), _mins(b)
    if ma is None or mb is None or mb < ma:
        return None
    d = mb - ma
    return f"{d // 60}h {d % 60}m" if d >= 60 else f"{d}m"


def _heatmap(rows: list, key_fn, order: tuple) -> list:
    """Realized net per bucket. Buckets with no trades report count 0 and net 0.0
    with `observed: False` — the chart may render them, but nothing downstream may
    read them as an observation (§9)."""
    acc: dict = {}
    for r in rows:
        k = key_fn(r)
        if k is None:
            continue
        a = acc.setdefault(k, {"net": 0.0, "trades": 0})
        a["net"] = round(a["net"] + float(r.get("net_pnl") or 0.0), 2)
        a["trades"] += 1
    return [{"bucket": b, "net": acc.get(b, {}).get("net", 0.0),
             "trades": acc.get(b, {}).get("trades", 0), "observed": b in acc} for b in order]


def _attribution(rows: list, dim: str) -> dict:
    """Breakdown WITHIN one dimension — which is why it cannot double-count.

    ⛔ The reference PNG's legend lists Strategy/Scanner/Symbol/Type/Direction as
    slices of ONE pie summing to net. That is not a decomposition: every trade has
    a strategy AND a symbol AND a direction, so those shares overlap completely
    and the total is meaningless. The approved instruction is explicit —
    'Avoid double-counting P&L across attribution dimensions' — so the pie shows
    ONE dimension at a time and the dimension is selectable. Same visual, honest
    arithmetic."""
    key = {"strategy": lambda r: r.get("strategy"), "symbol": lambda r: r.get("symbol"),
           "trade_type": _trade_type, "direction": lambda r: r.get("direction")}.get(dim)
    if key is None:
        dim, key = "strategy", (lambda r: r.get("strategy"))
    acc: dict = {}
    for r in rows:
        k = key(r) or "—"
        acc[k] = round(acc.get(k, 0.0) + float(r.get("net_pnl") or 0.0), 2)
    net_total = round(sum(acc.values()), 2)
    base = sum(abs(v) for v in acc.values())          # share of ABSOLUTE contribution
    slices = [{"label": k, "net": v,
               "pct": (round(100.0 * abs(v) / base, 2) if base else None)}
              for k, v in acc.items()]
    return {"dimension": dim, "net_total": net_total,
            "slices": sorted(slices, key=lambda s: -abs(s["net"])),
            "pct_basis": "share of absolute contribution (signed nets cannot sum to 100%)"}


def _summary_rows(rows: list) -> list:
    """Main table. Grouped by (date, strategy, symbol, trade type, direction) —
    the PNG's grain minus Scanner.

    ⚠️ TIME is the group's FIRST ENTRY time, and that is an interpretation worth
    naming: the instruction adds Time as its own column after Trading Date, but
    every row here aggregates many trades, so no single instant belongs to the
    row. The earliest entry is a real, measured value (⛔ not invented) and gives
    the row a temporal anchor; `time_note` carries the definition to the UI."""
    groups: dict = {}
    for r in rows:
        k = (str(r.get("exit_time") or "")[:10], r.get("strategy") or "—",
             (r.get("symbol") or "—").upper(), _trade_type(r),
             (r.get("direction") or "—").upper())
        g = groups.get(k)
        if g is None:
            g = groups[k] = _blank_agg("group", k)
            g.update({"date": k[0], "strategy": k[1], "symbol": k[2],
                      "trade_type": k[3], "direction": k[4], "first_entry": None})
        et = r.get("entry_time")
        if et and (g["first_entry"] is None or str(et) < str(g["first_entry"])):
            g["first_entry"] = et
        _add(g, float(r.get("net_pnl") or 0.0), float(r.get("gross_pnl") or 0.0),
             float(r.get("charges") or 0.0), float(r.get("margin_reserved") or 0.0))
    out = []
    for g in groups.values():
        m = _metrics(g)
        m["time"] = (str(g["first_entry"])[11:19] or None) if g["first_entry"] else None
        # `group` is the internal tuple key and `first_entry` the raw timestamp;
        # neither belongs in a public payload — the row already carries date,
        # strategy, symbol, trade_type, direction and the formatted time.
        m.pop("group", None)
        m.pop("first_entry", None)
        out.append(m)
    return sorted(out, key=lambda m: (m["date"], m["strategy"], m["symbol"]), reverse=True)


def _shift_range(frm: str, to: str) -> tuple:
    """The immediately-preceding window of EQUAL length, for Compare With.

    ⭐ This is derived from the SAME authoritative aggregator over a real date
    range — ⛔ nothing is modelled, extrapolated or invented (§9)."""
    def _d(s):
        return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    try:
        a, b = _d(frm), _d(to)
    except (ValueError, TypeError):
        return None, None
    span = (b - a).days + 1
    return (a - timedelta(days=span)).isoformat(), (a - timedelta(days=1)).isoformat()


def build_pnl_analytics(cfg, period="today", from_date=None, to_date=None, *,
                        strategy=None, symbol=None, trade_type=None, direction=None,
                        attribution_dim="strategy", compare=None, limit=None) -> dict:
    """Screen 09. One filtered population feeds every panel (§11)."""
    frm, to = _period(cfg, period, from_date, to_date)
    all_rows = db_reader.closed_trades_range(cfg, frm, to, limit=limit)

    # Filter option lists come from the UNFILTERED period so choosing one value
    # never erases the others from the dropdown.
    options = {
        "strategy": sorted({r["strategy"] for r in all_rows if r.get("strategy")}),
        "symbol": sorted({(r["symbol"] or "").upper() for r in all_rows if r.get("symbol")}),
        "trade_type": sorted({_trade_type(r) for r in all_rows}),
        "direction": sorted({(r["direction"] or "").upper() for r in all_rows if r.get("direction")}),
    }
    active = {"strategy": strategy, "symbol": symbol,
              "trade_type": trade_type, "direction": direction}
    rows = _apply_pnl_filters(all_rows, active)

    total = _blank_agg("scope", "total")
    for r in rows:
        _add(total, float(r.get("net_pnl") or 0.0), float(r.get("gross_pnl") or 0.0),
             float(r.get("charges") or 0.0), float(r.get("margin_reserved") or 0.0))
    totals = _metrics(total)

    net_curve = _curve(rows, "net_pnl")
    gross_curve = _curve(rows, "gross_pnl")

    # Best / worst — the TRADE identity, not just the number.
    best_t = max(rows, key=lambda r: float(r.get("net_pnl") or 0.0), default=None)
    worst_t = min(rows, key=lambda r: float(r.get("net_pnl") or 0.0), default=None)

    def _t(r):
        if r is None:
            return None
        return {"trade_id": r.get("trade_id"), "symbol": r.get("symbol"),
                "strategy": r.get("strategy"), "net": round(float(r.get("net_pnl") or 0.0), 2),
                "exit_reason": r.get("exit_reason"), "exit_time": r.get("exit_time")}

    per_strategy = sorted(_aggregate(rows, lambda r: r.get("strategy"), "strategy"),
                          key=lambda m: -m["net"])
    per_symbol = sorted(_aggregate(rows, lambda r: (r.get("symbol") or "").upper() or None, "symbol"),
                        key=lambda m: -m["net"])

    derived = sum(1 for r in rows if r.get("charges_derived"))

    out = {
        "period": period, "from": frm, "to": to,
        "filters": {"active": {k: v for k, v in active.items() if v}, "options": options,
                    "keys": list(_PNL_FILTERS)},
        "totals": totals,
        "rows": _summary_rows(rows),
        "time_note": "Time = first ENTRY time in the group (IST); rows aggregate many trades",
        "per_strategy": per_strategy,
        "per_symbol": per_symbol,
        "per_direction": sorted(_aggregate(rows, lambda r: r.get("direction"), "direction"),
                                key=lambda m: m["direction"]),
        "per_trade_type": sorted(_aggregate(rows, _trade_type, "trade_type"),
                                 key=lambda m: -m["net"]),
        "equity_curve": {"net": net_curve, "gross": gross_curve},
        "curve_note": "realized only (closed trades over the period) — intraday unrealized MTM not included (G4)",
        "drawdown": _drawdown(net_curve),
        "drawdown_gross": _drawdown(gross_curve),
        "best_worst": {
            "best_trade": _t(best_t), "worst_trade": _t(worst_t),
            "best_strategy": per_strategy[0]["strategy"] if per_strategy else None,
            "worst_strategy": per_strategy[-1]["strategy"] if per_strategy else None,
        },
        "heatmap_dow": _heatmap(rows, lambda r: _dow(r.get("exit_time")), _dow_buckets(rows)),
        "heatmap_tod": _heatmap(rows, lambda r: _tod_bucket(r.get("exit_time")),
                                tuple(b[0] for b in _TOD_BUCKETS)),
        "attribution": _attribution(rows, attribution_dim),
        "charges_note": (f"{derived} of {len(rows)} rows have NO persisted charges column; "
                         f"for those, charges = gross − net (derived, not broker-reported)"
                         if derived else "all charges read from the persisted column"),
        "charges_derived_rows": derived,
        "row_cap": limit,
        "row_cap_applied": bool(limit) and len(all_rows) >= int(limit or 0),
        "trade_count_unfiltered": len(all_rows),
        "trade_count": len(rows),
    }

    # ── Compare With (§9) — real data or an explicit unavailability, never a guess.
    if compare == "previous":
        pfrm, pto = _shift_range(frm, to)
        if pfrm is None:
            out["compare"] = {"mode": "previous", "available": False,
                              "reason": "period bounds are not parseable dates"}
        else:
            prev_rows = _apply_pnl_filters(
                db_reader.closed_trades_range(cfg, pfrm, pto, limit=limit), active)
            pt = _blank_agg("scope", "prev")
            for r in prev_rows:
                _add(pt, float(r.get("net_pnl") or 0.0), float(r.get("gross_pnl") or 0.0),
                     float(r.get("charges") or 0.0), float(r.get("margin_reserved") or 0.0))
            pm = _metrics(pt)
            out["compare"] = {
                "mode": "previous", "available": True, "from": pfrm, "to": pto,
                "totals": pm, "trade_count": len(prev_rows),
                "delta": {k: (None if (totals.get(k) is None or pm.get(k) is None)
                              else round(totals[k] - pm[k], 2))
                          for k in ("gross", "charges", "net", "roi_pct",
                                    "win_rate", "profit_factor")},
            }
    elif compare:
        out["compare"] = {"mode": compare, "available": False,
                          "reason": f"no authoritative source for comparison mode '{compare}'"}
    return out


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
