"""
ops_dashboard/backend/services/slippage_analytics.py

SCREEN 10 — SLIPPAGE ANALYTICS.  Pure aggregation over the read-only
`db_reader.slippage_rows_range` spine; NO schema, NO mode branch (paper/live
aggregate identically), NO write path anywhere.

SCANNER IS DELIBERATELY ABSENT (Rama — carried forward from Screen 09, 14-Aug):
"Strategy and Scanner represent the same source identity in this system." The
reference design (`gui/10. Slippage_Analytics.txt` + PNG) has a Scanner filter, a
Scanner column and a whole SCANNER SLIPPAGE RANKING panel; all three are gone
here because they would say the same thing Strategy already says, three more
times. ⛔ Do NOT reintroduce a scanner filter, column, ranking or slice.
`build_scanner_attribution` (Scanner Attribution screen) is UNTOUCHED — that
screen asks a different question and keeps its own dimension.

DOCUMENTED FORMULAS — every one carries its BASE, because a percentage without
its base is not a number:

  allowed_slippage_rs   the tolerance the ORDER PATH actually applied, mirroring
                        orders/order_placer._compute_slippage_tolerance for the
                        deployed mode (`sl_fraction`):
                            min(planned_sl_distance × fraction, absolute_cap_rs)
                        falling back to absolute_cap_rs when the SL distance is
                        unknown, then capped by hard_max_slippage_rs.
                        `fraction` is the trade's OWN persisted
                        tolerance_fraction_used when present (it already encodes
                        the symbol > strategy > band > global override that won),
                        else the configured global max_slippage_fraction.

  slippage_pct          100 × entry_slippage_rs / entry_signal_price
                        BASE = the SYSTEM (signal) price of that order.

  status                ratio = actual_slippage_rs / allowed_slippage_rs
                        BASE = THE ALLOWED SLIPPAGE, ⛔ not the price.
                            ratio  > 1.00          → EXCEEDED
                            0.60 ≤ ratio ≤ 1.00    → NEAR_LIMIT
                            ratio  < 0.60          → WITHIN_LIMIT
                        The 0.60 floor is the approved design's own definition
                        ("Near Limit — slippage near tolerance (60%–100%)").
                        ⛔ A row with no measured slippage or no tolerance is
                        UNMEASURED — never silently counted as within limit.

  rr_damage_pct         the SYSTEM'S OWN persisted metric
                        (orders/slippage_recorder.calc_rr_damage_pct):
                            (entry_adverse + sl_adverse − tgt_favourable)
                            / planned_sl_distance × 100
                        BASE = the planned risk budget (SL distance).

  rr_degradation_pct    100 × (planned_rr − actual_rr) / planned_rr
                        BASE = the planned R:R. This is the reference design's
                        "1:2 → 1:1.6 = 20%" arithmetic, and it is a DIFFERENT
                        quantity from rr_damage_pct above. Both are reported,
                        each under its own name. ⛔ Neither is presented as the
                        other — one label, one meaning (the Screen-04/05/06
                        "System Score" precedent).

  pnl_lost_to_slippage  Σ (entry_slippage_rs × qty) over rows carrying both.
                        ENTRY LEG ONLY: it is the only leg with a configured
                        budget, which is the same judgement the existing
                        /api/slippage endpoint already makes.
  potential_pnl         actual_pnl + pnl_lost_to_slippage
  impact_pct            100 × pnl_lost_to_slippage / |potential_pnl|
                        BASE = the potential (no-slippage) P&L.
"""
from __future__ import annotations

from typing import Optional

from ..readers import config_reader, db_reader
from . import freshness
from .analytics_period import _TOD_BUCKETS, _tod_bucket, _trade_type

# ─────────────────────────────────────────────────────────────────────────────
# Approved constants
# ─────────────────────────────────────────────────────────────────────────────

# The five buckets the approved Screen-10 direction locks, keyed on the SYSTEM
# price. Intervals are [lo, hi) so ₹100.00 lands in "100-200" exactly once and
# the five are a partition — no order can fall in two, or in none.
#
# ⚠️ THESE ARE NOT `system_config.slippage_bands`. That list has SIX bands
# (0-100 / 100-200 / 200-300 / 300-500 / 500-1000 / 1000+) and is what the
# persisted `trade_slippage_log.price_band` column holds. The approved screen
# asks for five, merging 200-300 and 300-500. Deriving the bucket from the
# system price rather than reading the stored label is what lets the panel show
# the five that were approved without misreporting a stored value as something
# it is not. `bucket_note` states this on the payload.
_PRICE_BUCKETS = (("0-100", 0.0, 100.0), ("100-200", 100.0, 200.0),
                  ("200-500", 200.0, 500.0), ("500-1000", 500.0, 1000.0),
                  ("1000+", 1000.0, None))

# Fraction OF THE ALLOWED SLIPPAGE at which a fill stops being comfortable.
_NEAR_LIMIT_FLOOR = 0.60

_WITHIN, _NEAR, _EXCEEDED, _UNMEASURED = ("WITHIN_LIMIT", "NEAR_LIMIT",
                                          "EXCEEDED", "UNMEASURED")
_STATUSES = (_WITHIN, _NEAR, _EXCEEDED, _UNMEASURED)

# Filterable dimensions. ⛔ Scanner is absent on purpose (see module header).
_SLIP_FILTERS = ("strategy", "symbol", "trade_type", "direction",
                 "price_bucket", "status")


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────
def _f(v) -> Optional[float]:
    """float(v) or None — ⛔ never 0.0 for a missing value. A missing slippage
    and a zero slippage are different observations and must not merge."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def resolve_price_bucket(system_price) -> Optional[str]:
    """The approved bucket for a SYSTEM price. None when there is no price —
    such a row is counted as unbucketed rather than dropped into "0-100".

    Named `resolve_*` so the module-level helper is never shadowed by the
    `price_bucket` FILTER ARGUMENT that carries the same idea into the builder."""
    p = _f(system_price)
    if p is None:
        return None
    for label, lo, hi in _PRICE_BUCKETS:
        if p >= lo and (hi is None or p < hi):
            return label
    return None


def slippage_model(cfg: dict, today: str) -> dict:
    """The DEPLOYED entry-slippage budget, read from the config snapshot (or the
    YAML fallback) — ⛔ never hard-coded here. The defaults below are only the
    last resort when neither source carries the block, and `source` says which
    one answered so a number on screen can always be traced."""
    sc = config_reader.get_system_config(cfg, today)
    sc = sc if isinstance(sc, dict) else {}
    slc = ((sc.get("entry_gate") or {}).get("slippage_control")) or {}
    return {
        "mode": slc.get("mode") or "sl_fraction",
        "enabled": bool(slc.get("enabled", True)),
        "max_slippage_fraction": float(slc.get("max_slippage_fraction") or 0.22),
        "absolute_cap_rs": float(slc.get("absolute_cap_rs") or 5.0),
        "hard_max_slippage_rs": float(slc.get("hard_max_slippage_rs") or 10.0),
        "near_limit_floor_pct": round(_NEAR_LIMIT_FLOOR * 100.0, 2),
        "source": sc.get("_source") or "defaults",
    }


def allowed_slippage(row: dict, model: dict) -> tuple:
    """(allowed_rs, fraction_used, source) for one row.

    Mirrors the order path's own tolerance for the deployed `sl_fraction` mode.
    ⚠️ For any OTHER configured mode the per-trade SL distance is not the input
    the order path used, so this returns the absolute cap under the source
    label `mode:<mode>` and the screen states that the figure is the backstop
    rather than the tiered/pct tolerance — ⛔ better an honest coarse bound than
    a precise-looking number the system never applied."""
    hard = model["hard_max_slippage_rs"]
    if model.get("mode") != "sl_fraction":
        return round(min(model["absolute_cap_rs"], hard), 4), None, "mode:%s" % model.get("mode")

    frac = _f(row.get("tolerance_fraction_used"))
    src = row.get("tolerance_source") or None
    if frac is None:
        frac, src = model["max_slippage_fraction"], "config-global"

    dist = _f(row.get("planned_sl_distance"))
    tol = min(dist * frac, model["absolute_cap_rs"]) if dist else model["absolute_cap_rs"]
    return round(min(tol, hard), 4), round(frac, 4), src


def classify(actual_rs: Optional[float], allowed_rs: Optional[float]) -> tuple:
    """(status, ratio_pct). ratio BASE = the allowed slippage.

    ⛔ A row with no measured slippage is UNMEASURED, not WITHIN_LIMIT: counting
    an absent observation as a pass is exactly how a broken recorder would look
    like a clean day."""
    if actual_rs is None or not allowed_rs:
        return _UNMEASURED, None
    ratio = actual_rs / allowed_rs
    if ratio > 1.0:
        status = _EXCEEDED
    elif ratio >= _NEAR_LIMIT_FLOOR:
        status = _NEAR
    else:
        status = _WITHIN
    return status, round(ratio * 100.0, 2)


def _enrich(rows: list, model: dict, scores: dict) -> list:
    """One pass that gives every row the derived fields the whole screen shares,
    so no panel can compute a status differently from the table."""
    out = []
    for r in rows:
        e = dict(r)
        sys_px = _f(r.get("entry_signal_price"))
        actual = _f(r.get("entry_slippage_rs"))
        allowed, frac, src = allowed_slippage(r, model)
        status, ratio = classify(actual, allowed)

        pct = _f(r.get("entry_slippage_pct"))
        if pct is None and actual is not None and sys_px:
            pct = round(actual / sys_px * 100.0, 4)

        sc = scores.get(r.get("signal_id")) or {}
        e.update({
            "date": r.get("trade_date"),
            "time": (str(r.get("entry_time"))[11:19] or None) if r.get("entry_time") else None,
            "strategy": r.get("strategy_name"),
            "symbol": (r.get("symbol") or "").upper() or None,
            "trade_type": _trade_type(r),
            "direction": (r.get("side") or "").upper() or None,
            "system_score": sc.get("system_score"),
            "score_threshold": sc.get("score_threshold"),
            "entry_price_system": sys_px,
            "entry_price_filled": _f(r.get("entry_fill_price")),
            "allowed_slippage_rs": allowed,
            "actual_slippage_rs": actual,
            "slippage_pct": pct,
            "status": status,
            "status_ratio_pct": ratio,
            "tolerance_fraction_used": frac,
            "tolerance_source": src,
            "price_bucket": resolve_price_bucket(sys_px),
            "tod_bucket": _tod_bucket(r.get("entry_time")),
            "slippage_cost_rs": (round(actual * float(r["qty"]), 2)
                                 if (actual is not None and r.get("qty")) else None),
        })
        out.append(e)
    return out


def _apply_filters(rows: list, f: dict) -> list:
    """THE single filter gate. Every panel on Screen 10 is derived from the list
    this returns — KPIs, table, buckets, rankings, RR, trend, donut and the XLSX
    export — so they CANNOT describe different populations."""
    out = rows
    if f.get("strategy"):
        out = [r for r in out if (r.get("strategy") or "") == f["strategy"]]
    if f.get("symbol"):
        out = [r for r in out if (r.get("symbol") or "") == f["symbol"].upper()]
    if f.get("trade_type"):
        out = [r for r in out if r.get("trade_type") == f["trade_type"].upper()]
    if f.get("direction"):
        out = [r for r in out if (r.get("direction") or "") == f["direction"].upper()]
    if f.get("price_bucket"):
        out = [r for r in out if r.get("price_bucket") == f["price_bucket"]]
    if f.get("status"):
        out = [r for r in out if r.get("status") == f["status"].upper()]
    return out


def _measured(rows: list) -> list:
    return [r for r in rows if r.get("actual_slippage_rs") is not None]


def _avg(vals: list) -> Optional[float]:
    """Mean, or None when there is nothing to average. ⛔ Never 0.0 — an empty
    set has no mean, and 0.00 would read as a measurement."""
    return round(sum(vals) / len(vals), 4) if vals else None


def _rank(rows: list, key: str, label: str) -> list:
    """Ranking rows for one dimension. Averages are over MEASURED rows only, and
    each group reports how many of its orders were measurable."""
    groups: dict = {}
    for r in rows:
        k = r.get(key)
        if k is None:
            continue
        g = groups.setdefault(k, {label: k, "orders": 0, "measured": 0,
                                  "exceeded": 0, "near": 0, "within": 0,
                                  "_vals": [], "cost_rs": 0.0})
        g["orders"] += 1
        if r["status"] == _EXCEEDED:
            g["exceeded"] += 1
        elif r["status"] == _NEAR:
            g["near"] += 1
        elif r["status"] == _WITHIN:
            g["within"] += 1
        v = r.get("actual_slippage_rs")
        if v is not None:
            g["measured"] += 1
            g["_vals"].append(v)
        if r.get("slippage_cost_rs") is not None:
            g["cost_rs"] = round(g["cost_rs"] + r["slippage_cost_rs"], 2)
    out = []
    for g in groups.values():
        vals = g.pop("_vals")
        g["avg_slippage_rs"] = _avg(vals)
        g["worst_slippage_rs"] = round(max(vals), 4) if vals else None
        g["exceeded_pct"] = (round(100.0 * g["exceeded"] / g["orders"], 2)
                             if g["orders"] else None)
        out.append(g)
    # Worst average first; a group with nothing measured sinks to the bottom
    # rather than sorting as if its average were zero.
    return sorted(out, key=lambda g: (g["avg_slippage_rs"] is None,
                                      -(g["avg_slippage_rs"] or 0.0), g[label]))


def _buckets(rows: list) -> list:
    """The five approved price buckets, always ALL FIVE. A bucket with no orders
    reports observed=False and dashes on screen — ⛔ never a green ₹0.00, which
    would read as "measured, and it was perfect"."""
    by = {b: [] for b, _lo, _hi in _PRICE_BUCKETS}
    for r in rows:
        if r.get("price_bucket") in by:
            by[r["price_bucket"]].append(r)
    out = []
    for label, _lo, _hi in _PRICE_BUCKETS:
        rs = by[label]
        vals = [r["actual_slippage_rs"] for r in _measured(rs)]
        exceeded = sum(1 for r in rs if r["status"] == _EXCEEDED)
        out.append({
            "bucket": label, "orders": len(rs), "measured": len(vals),
            "observed": bool(rs),
            "avg_slippage_rs": _avg(vals),
            "worst_slippage_rs": round(max(vals), 4) if vals else None,
            "exceeded": exceeded,
            "exceeded_pct": round(100.0 * exceeded / len(rs), 2) if rs else None,
        })
    return out


def _trend(rows: list) -> list:
    """Average slippage through the session, bucketed by ENTRY time — the moment
    the slippage actually happened. ⛔ Not exit time.

    The buckets are `analytics_period._TOD_BUCKETS`, imported rather than
    re-declared, so Screen 09 and Screen 10 cannot drift into two different
    definitions of "11:00–12:00". Their edges are the approved timeline
    (09:15 · 10:00 · 11:00 · 12:00 · 13:00 · 14:00 · 15:30)."""
    by: dict = {b[0]: [] for b in _TOD_BUCKETS}
    outside = 0
    for r in rows:
        b = r.get("tod_bucket")
        if b in by:
            by[b].append(r)
        elif r.get("entry_time"):
            outside += 1
    out = []
    for label, lo, _hi in _TOD_BUCKETS:
        rs = by[label]
        m = _measured(rs)
        out.append({
            "bucket": label, "at": lo, "orders": len(rs), "measured": len(m),
            "observed": bool(m),
            "avg_slippage_rs": _avg([r["actual_slippage_rs"] for r in m]),
            "avg_slippage_pct": _avg([r["slippage_pct"] for r in m
                                      if r.get("slippage_pct") is not None]),
        })
    return out, outside


def _rr(rows: list) -> dict:
    """RR damage. Two DIFFERENT quantities, each under its own name (see header).
    Both are averaged over the rows that actually carry them, and each reports
    its own n so a mean over three trades cannot look like a mean over 300."""
    planned = [_f(r.get("planned_rr")) for r in rows]
    actual = [_f(r.get("actual_rr")) for r in rows]
    pairs = [(p, a) for p, a in zip(planned, actual)
             if p is not None and a is not None and p > 0]
    dmg = [_f(r.get("rr_damage_pct")) for r in rows]
    dmg = [d for d in dmg if d is not None]

    exp_avg = _avg([p for p in planned if p is not None])
    act_avg = _avg([a for a in actual if a is not None])
    degr = _avg([100.0 * (p - a) / p for p, a in pairs])

    impacted = [r for r in rows
                if (r.get("actual_slippage_rs") or 0.0) > 0.0]
    return {
        "expected_rr_avg": exp_avg, "expected_rr_n": sum(1 for p in planned if p is not None),
        "actual_rr_avg": act_avg, "actual_rr_n": sum(1 for a in actual if a is not None),
        "rr_damage_pct_avg": _avg(dmg), "rr_damage_n": len(dmg),
        "rr_damage_basis": "% of the planned risk budget (SL distance) destroyed by "
                           "execution — the system's own persisted metric",
        "rr_degradation_pct_avg": degr, "rr_degradation_n": len(pairs),
        "rr_degradation_basis": "% of the planned R:R lost (planned − actual) ÷ planned "
                                "— the reference design's 1:2 → 1:1.6 arithmetic",
        "trades_impacted": len(impacted),
        "total_trades": len(rows),
    }


def _impact(rows: list) -> dict:
    """Business impact of ENTRY slippage. Every figure is a sum over real rows;
    ⛔ nothing is modelled or extrapolated.

    ⚠️ `potential_pnl` is explicitly a COUNTERFACTUAL — "the same trades filled
    at their system price" — and it is labelled as one on screen. It is NOT a
    claim about what the market would have done."""
    costed = [r for r in rows if r.get("slippage_cost_rs") is not None]
    lost = round(sum(r["slippage_cost_rs"] for r in costed), 2)
    # Net P&L for the SAME rows, de-duplicated by trade so a trade cannot be
    # counted twice if it ever produced two roll-up rows.
    seen: dict = {}
    for r in rows:
        if r.get("trade_id") and r.get("trade_id") not in seen:
            seen[r["trade_id"]] = _f(r.get("net_pnl")) or 0.0
    actual_pnl = round(sum(seen.values()), 2)
    potential = round(actual_pnl + lost, 2)
    return {
        "actual_pnl": actual_pnl,
        "pnl_lost_to_slippage": lost,
        "potential_pnl": potential,
        "impact_pct": (round(100.0 * lost / abs(potential), 2) if potential else None),
        "impact_pct_basis": "share of the potential (no-slippage) P&L",
        "costed_rows": len(costed), "total_rows": len(rows),
        "note": "entry leg only — the only leg with a configured slippage budget",
    }


def _status_counts(rows: list) -> dict:
    return {s: sum(1 for r in rows if r["status"] == s) for s in _STATUSES}


# ─────────────────────────────────────────────────────────────────────────────
# The screen
# ─────────────────────────────────────────────────────────────────────────────
def build_slippage_analytics(cfg, period="today", from_date=None, to_date=None, *,
                             strategy=None, symbol=None, trade_type=None,
                             direction=None, price_bucket=None, status=None,
                             limit=None) -> dict:
    """Screen 10. One filtered population feeds every panel."""
    frm, to = freshness.resolve_period(period, from_date, to_date)
    model = slippage_model(cfg, to)

    raw = db_reader.slippage_rows_range(cfg, frm, to, limit=limit)
    scores = db_reader.signal_scores(cfg, [r.get("signal_id") for r in raw])
    all_rows = _enrich(raw, model, scores)

    # Option lists come from the UNFILTERED period, so choosing one value never
    # erases the others from the dropdown.
    options = {
        "strategy": sorted({r["strategy"] for r in all_rows if r.get("strategy")}),
        "symbol": sorted({r["symbol"] for r in all_rows if r.get("symbol")}),
        "trade_type": sorted({r["trade_type"] for r in all_rows}),
        "direction": sorted({r["direction"] for r in all_rows if r.get("direction")}),
        "price_bucket": [b for b, _lo, _hi in _PRICE_BUCKETS],
        "status": list(_STATUSES),
    }
    active = {"strategy": strategy, "symbol": symbol, "trade_type": trade_type,
              "direction": direction, "price_bucket": price_bucket, "status": status}
    rows = _apply_filters(all_rows, active)

    counts = _status_counts(rows)
    measured = _measured(rows)
    vals = [r["actual_slippage_rs"] for r in measured]
    trend, outside_session = _trend(rows)

    worst_row = max(measured, key=lambda r: r["actual_slippage_rs"], default=None)

    return {
        "period": period, "from": frm, "to": to,
        "model": model,
        "filters": {"active": {k: v for k, v in active.items() if v},
                    "options": options, "keys": list(_SLIP_FILTERS)},

        # ── KPI deck. The four counts PARTITION the population: within + near +
        #    exceeded + unmeasured == total_orders, always. `unmeasured` is
        #    published rather than folded into a pass so the deck can never
        #    quietly stop adding up.
        "totals": {
            "total_orders": len(rows),
            "within_limit": counts[_WITHIN],
            "near_limit": counts[_NEAR],
            "exceeded": counts[_EXCEEDED],
            "unmeasured": counts[_UNMEASURED],
            "measured": len(measured),
            "avg_slippage_rs": _avg(vals),
            "worst_slippage_rs": round(max(vals), 4) if vals else None,
            "worst_symbol": worst_row["symbol"] if worst_row else None,
            "within_pct": (round(100.0 * counts[_WITHIN] / len(rows), 2) if rows else None),
            "near_pct": (round(100.0 * counts[_NEAR] / len(rows), 2) if rows else None),
            "exceeded_pct": (round(100.0 * counts[_EXCEEDED] / len(rows), 2) if rows else None),
            "pct_basis": "share of the filtered order population",
        },

        "rows": rows,
        "price_buckets": _buckets(rows),
        "bucket_note": ("buckets are derived from the SYSTEM price. The persisted "
                        "price_band column uses the six configured slippage_bands "
                        "(0-100/100-200/200-300/300-500/500-1000/1000+); the five "
                        "approved buckets merge 200-300 and 300-500"),
        "per_strategy": _rank(rows, "strategy", "strategy"),
        "per_symbol": _rank(rows, "symbol", "symbol"),
        "rr": _rr(rows),
        "trend": trend,
        "trend_note": ("bucketed by ENTRY time (IST) — when the slippage happened"
                       + (f"; {outside_session} order(s) entered outside 09:15–15:30 "
                          "and are counted in the totals but sit in no bucket"
                          if outside_session else "")),
        "status_distribution": [
            {"status": _WITHIN, "label": "Within Limit", "count": counts[_WITHIN]},
            {"status": _NEAR, "label": "Near Limit", "count": counts[_NEAR]},
            {"status": _EXCEEDED, "label": "Exceeded", "count": counts[_EXCEEDED]},
            {"status": _UNMEASURED, "label": "Unmeasured", "count": counts[_UNMEASURED]},
        ],
        "status_guide": [
            {"status": _WITHIN, "label": "Within Limit",
             "rule": "slippage under %.0f%% of the allowed slippage" % (_NEAR_LIMIT_FLOOR * 100)},
            {"status": _NEAR, "label": "Near Limit",
             "rule": "slippage at %.0f%%–100%% of the allowed slippage" % (_NEAR_LIMIT_FLOOR * 100)},
            {"status": _EXCEEDED, "label": "Exceeded",
             "rule": "slippage over 100% of the allowed slippage"},
            {"status": _UNMEASURED, "label": "Unmeasured",
             "rule": "no signal price, no fill price or no tolerance — never counted as a pass"},
        ],
        "impact": _impact(rows),

        "score_note": ("System Score = the ACHIEVED screener score; Score Threshold = "
                       "the minimum it had to reach. \"Signal Score\" is retired "
                       "system-wide (Rama, 13-Aug) — a threshold is never shown as a score"),
        "slippage_pct_basis": "share of the SYSTEM (signal) price of the order",
        "row_cap": limit,
        "row_cap_applied": bool(limit) and len(raw) >= int(limit or 0),
        "order_count_unfiltered": len(all_rows),
        "order_count": len(rows),
    }


# ─────────────────────────────────────────────────────────────────────────────
# XLSX export.
#
# FOUR SHEETS, all derived from the ONE filtered population the screen shows.
# ⚠️ The first version exported only the detail rows, and the "Export XLSX"
# buttons on the Strategy and Symbol ranking panels handed you that same detail
# sheet — a button labelled for one thing producing another. Either the buttons
# had to go or the workbook had to cover what they name; covering it is the more
# useful of the two and keeps every label honest.
# ─────────────────────────────────────────────────────────────────────────────

# Column order mirrors the approved table, then the analytics fields. ⛔ No
# Scanner column — strategy IS the scanner identity on this system.
EXPORT_COLS = [
    ("Trading Date", "date"), ("Time", "time"),
    ("Strategy", "strategy"), ("Symbol", "symbol"),
    ("Trade Type", "trade_type"), ("Direction", "direction"),
    ("System Score", "system_score"), ("Score Threshold", "score_threshold"),
    ("Entry Price (System)", "entry_price_system"),
    ("Entry Price (Filled)", "entry_price_filled"),
    ("Allowed Slippage", "allowed_slippage_rs"),
    ("Actual Slippage", "actual_slippage_rs"),
    ("Slippage %", "slippage_pct"),
    ("Status", "status"),
    ("Slippage vs Allowed %", "status_ratio_pct"),
    ("Price Bucket", "price_bucket"),
    ("Tolerance Fraction Used", "tolerance_fraction_used"),
    ("Tolerance Source", "tolerance_source"),
    ("Planned SL Distance", "planned_sl_distance"),
    ("Planned R:R", "planned_rr"), ("Actual R:R", "actual_rr"),
    ("RR Damage %", "rr_damage_pct"),
    ("Slippage Cost (Rs)", "slippage_cost_rs"),
    ("Net P&L", "net_pnl"),
    ("Trade Result", "trade_result"), ("Exit Reason", "exit_reason"),
    ("Trade ID", "trade_id"),
]

_BUCKET_COLS = [("Price Range (System Price)", "bucket"), ("Orders", "orders"),
                ("Measured", "measured"), ("Avg Slippage", "avg_slippage_rs"),
                ("Worst Slippage", "worst_slippage_rs"),
                ("Exceeded", "exceeded"), ("Exceeded %", "exceeded_pct")]

_RANK_COLS = [("Avg Slippage", "avg_slippage_rs"), ("Worst Slippage", "worst_slippage_rs"),
              ("Exceeded", "exceeded"), ("Exceeded %", "exceeded_pct"),
              ("Orders", "orders"), ("Measured", "measured"),
              ("Within Limit", "within"), ("Near Limit", "near"),
              ("Slippage Cost (Rs)", "cost_rs")]


def export_sheets(payload: dict) -> list:
    """[(sheet_name, header_row, data_rows)] for the workbook.

    Pure: it reads ONLY the payload the screen was served, so a sheet cannot be
    computed from a different query than the table the operator is looking at."""
    f = payload.get("filters", {}).get("active") or {}
    t = payload.get("totals") or {}
    rr = payload.get("rr") or {}
    imp = payload.get("impact") or {}
    m = payload.get("model") or {}

    summary = [
        ("Period", payload.get("period")),
        ("From", payload.get("from")), ("To", payload.get("to")),
        ("Filters applied", ", ".join(f"{k}={v}" for k, v in f.items()) or "none"),
        ("Orders (filtered)", payload.get("order_count")),
        ("Orders (unfiltered, same period)", payload.get("order_count_unfiltered")),
        ("", ""),
        ("Total Orders", t.get("total_orders")),
        ("Orders Within Limit", t.get("within_limit")),
        ("Orders Near Limit", t.get("near_limit")),
        ("Orders Exceeded Limit", t.get("exceeded")),
        ("Orders Unmeasured", t.get("unmeasured")),
        ("Average Slippage (Rs)", t.get("avg_slippage_rs")),
        ("Worst Slippage (Rs)", t.get("worst_slippage_rs")),
        ("", ""),
        ("Expected RR (avg)", rr.get("expected_rr_avg")),
        ("Actual RR (avg)", rr.get("actual_rr_avg")),
        ("RR Damage % (avg)", rr.get("rr_damage_pct_avg")),
        ("RR Damage % basis", rr.get("rr_damage_basis")),
        ("RR Degradation % (avg)", rr.get("rr_degradation_pct_avg")),
        ("RR Degradation % basis", rr.get("rr_degradation_basis")),
        ("", ""),
        ("Potential P&L (if filled at system price)", imp.get("potential_pnl")),
        ("Actual P&L (realised)", imp.get("actual_pnl")),
        ("P&L Lost to Slippage", imp.get("pnl_lost_to_slippage")),
        ("% Impact on P&L", imp.get("impact_pct")),
        ("Impact note", imp.get("note")),
        ("", ""),
        ("Slippage budget mode", m.get("mode")),
        ("Global fraction of SL distance", m.get("max_slippage_fraction")),
        ("Absolute cap (Rs)", m.get("absolute_cap_rs")),
        ("Hard ceiling (Rs)", m.get("hard_max_slippage_rs")),
        ("Near-limit floor (% of allowed)", m.get("near_limit_floor_pct")),
        ("Budget source", m.get("source")),
        ("Bucket note", payload.get("bucket_note")),
    ]

    def _rank(rows, label_hdr, label_key):
        return ([label_hdr] + [h for h, _k in _RANK_COLS],
                [[r.get(label_key)] + [r.get(k) for _h, k in _RANK_COLS] for r in rows])

    strat_hdr, strat_rows = _rank(payload.get("per_strategy") or [], "Strategy", "strategy")
    sym_hdr, sym_rows = _rank(payload.get("per_symbol") or [], "Symbol", "symbol")
    return [
        ("Slippage Details", [h for h, _k in EXPORT_COLS],
         [[r.get(k) for _h, k in EXPORT_COLS] for r in payload.get("rows") or []]),
        ("Price Buckets", [h for h, _k in _BUCKET_COLS],
         [[b.get(k) for _h, k in _BUCKET_COLS] for b in payload.get("price_buckets") or []]),
        ("Strategy Ranking", strat_hdr, strat_rows),
        ("Symbol Ranking", sym_hdr, sym_rows),
        ("Summary", ["Measure", "Value"], [list(x) for x in summary]),
    ]
