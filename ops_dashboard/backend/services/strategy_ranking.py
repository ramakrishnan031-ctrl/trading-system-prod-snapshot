"""SCREEN 19 — STRATEGY RANKING.  The strategy leaderboard.

`gui/19. Strategy_Ranking.png` + `.txt` are BINDING for structure.

═══════════════════════════════════════════════════════════════════════════════
⭐ WHAT IS REAL HERE, AND WHERE EACH NUMBER COMES FROM

  · THE WHOLE TABLE is aggregated from CLOSED TRADES in the selected window
    (`db_reader.closed_trades_range`, dated by `exit_time`), through the SAME
    `analytics_period` primitives Screen 09 uses — ⛔ no second P&L engine.
    That is why the footer says "completed trades only": an open position has no
    realised outcome to rank.
  · TRADE TYPE is the STRATEGY's own configured `intent` from
    `config/strategies/<name>.yaml`, resolved through the ONE shared path
    (`strategy_meta`). ⛔⛔ It is NOT the trade's order product (MIS/CNC) — a
    DIFFERENT quantity that `analytics_period._trade_type` owns. The Trade Type
    FILTER on this screen binds to the SAME strategy-level value as the column,
    so the two can never describe different things on one screen.
  · SCORE is a weighted composite of FOUR measured quantities, in the weights
    the artwork's donut states (35/25/20/20). Every component is bounded 0-100
    by construction — ⛔ no magic scaling constant, ⛔ no hand-tuned curve.
  · TREND is a REAL period-over-period comparison: the same score recomputed
    over the IMMEDIATELY PRECEDING window of EQUAL LENGTH. ⛔ Not a guess, and
    ⛔ not a state invented when there is nothing to compare against — a
    strategy with no trades in the previous window has trend None and renders
    the em-dash.

⛔ A COMPONENT WITH NO EVIDENCE CONTRIBUTES 0, ⛔ NEVER a flattering default.
That is the convention `analytics_period` already documents for
`opportunity_quality`, reused here so two screens cannot score the same absence
differently.

⛔ A STRATEGY WITH NO CLOSED TRADES IN THE WINDOW SCORES None, ⛔ NOT 0. Zero is
a measurement ("it traded and earned nothing"); None is the absence of one. Such
a strategy is still LISTED — the screen is a leaderboard of ALL strategies — and
sorts last under every mode.
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from ..readers import db_reader
from . import analytics_period, freshness, strategy_meta

# ── the approved ranking modes, in the approved order (design §RANKING MODES) ─
MODES = (("net_pnl", "By Net P&L"), ("roi", "By ROI"), ("win_rate", "By Win %"),
         ("profit_factor", "By Profit Factor"), ("trade_count", "By Trade Count"))
MODE_KEYS = tuple(k for k, _ in MODES)
_MODE_METRIC = {"net_pnl": "net", "roi": "roi_pct", "win_rate": "win_rate",
                "profit_factor": "profit_factor", "trade_count": "trades"}

#: The artwork's STRATEGY SCORE BREAKDOWN donut, verbatim. ⭐ These weights are
#: the design's, ⛔ not chosen here, and they sum to 100.
SCORE_WEIGHTS = (("profitability", "Profitability", 35),
                 ("consistency", "Consistency", 25),
                 ("risk_adjusted", "Risk Adjusted", 20),
                 ("activity", "Activity", 20))

TRENDS = ("Improving", "Stable", "Declining")

#: Score points either side of flat that still count as Stable. ⚠️ This is the
#: CLASSIFIER's band, ⛔ not a measurement — it is stated here so the boundary is
#: visible rather than buried, and both scores travel in the payload so a reader
#: can check the call.
TREND_BAND = 5.0

#: The approved DATE RANGE choices (design §FILTERS).
DATE_RANGES = (("today", "Today"), ("week", "This Week"), ("month", "This Month"),
               ("custom", "Custom Range"))

DIRECTIONS = ("LONG", "SHORT")


def _gap(reason: str) -> dict:
    return {"measured": False, "value": None, "reason": reason}


def _pct_share(a: float, b: float) -> Optional[float]:
    """100 × a / (a + b) — the bounded 0-100 form of a ratio.

    ⭐ Used so a ratio with no ceiling (profit factor, payoff) becomes a score
    component without inventing a scale: PF 1.0 → 50, PF 3.0 → 75, PF → ∞ → 100.
    ⛔ Returns None when there is NOTHING to divide (a + b == 0) — no evidence.
    """
    total = a + b
    if total <= 0:
        return None
    return round(100.0 * a / total, 2)


def _score(m: dict, max_trades: int) -> dict:
    """The composite, in the artwork's own weights.

    profitability   share of gross profit in total gross movement (PF-bounded)
    consistency     the win rate itself
    risk_adjusted   share of the average win in (average win + average loss)
    activity        this strategy's trades against the busiest in the SAME
                    filtered set — a RELATIVE measure, and the payload says so

    ⛔ A component with no evidence contributes 0 (the documented convention),
    ⛔ and a strategy with no trades at all scores None rather than 0.
    """
    if not m.get("trades"):
        return {"score": None, "parts": {}, "reason": "no completed trades in this window"}

    win_sum = float(m.get("win_sum") or 0.0)
    loss_abs = abs(float(m.get("loss_sum") or 0.0))
    avg_win = float(m.get("avg_win") or 0.0)
    avg_loss = float(m.get("avg_loss") or 0.0)

    parts = {
        "profitability": _pct_share(win_sum, loss_abs),
        "consistency": m.get("win_rate"),
        "risk_adjusted": _pct_share(avg_win, avg_loss),
        "activity": (round(100.0 * float(m["trades"]) / max_trades, 2)
                     if max_trades else None),
    }
    total = 0.0
    for key, _label, weight in SCORE_WEIGHTS:
        v = parts.get(key)
        total += (float(v) * weight / 100.0) if v is not None else 0.0
    return {"score": int(round(total)), "parts": parts, "reason": None}


def _prev_window(frm: str, to: str) -> tuple:
    """The IMMEDIATELY PRECEDING window of EQUAL length. ⭐ Equal length is what
    makes the comparison a comparison — a 7-day window judged against a 1-day one
    would call every strategy 'declining'."""
    try:
        a = _dt.date(*(int(x) for x in frm.split("-")))
        b = _dt.date(*(int(x) for x in to.split("-")))
    except (ValueError, TypeError):
        return None, None
    span = (b - a).days + 1
    prev_to = a - _dt.timedelta(days=1)
    prev_frm = prev_to - _dt.timedelta(days=span - 1)
    return prev_frm.isoformat(), prev_to.isoformat()


def _classify_trend(now_score, prev_score) -> Optional[str]:
    """⛔ None when there is nothing to compare — the UI renders an em-dash
    rather than calling an unmeasured strategy 'Stable'."""
    if now_score is None or prev_score is None:
        return None
    delta = float(now_score) - float(prev_score)
    if delta > TREND_BAND:
        return "Improving"
    if delta < -TREND_BAND:
        return "Declining"
    return "Stable"


def _filtered(rows: list, meta: dict, trade_type: Optional[str],
              direction: Optional[str]) -> list:
    """⭐ TRADE TYPE FILTERS ON THE STRATEGY'S OWN INTENT — the same value the
    column shows. ⛔ Not the order product: filtering by one and displaying the
    other is exactly how a screen starts contradicting itself."""
    out = rows
    if trade_type:
        want = trade_type.strip().title()
        out = [r for r in out
               if (meta.get(r.get("strategy")) or {}).get("trade_type") == want]
    if direction:
        want_d = direction.strip().upper()
        out = [r for r in out if str(r.get("direction") or "").upper() == want_d]
    return out


def _metrics_for(cfg, frm, to, meta, trade_type, direction) -> tuple:
    raw = db_reader.closed_trades_range(cfg, frm, to)
    kept = _filtered(raw, meta, trade_type, direction)
    metrics = analytics_period._aggregate(kept, lambda r: r.get("strategy"), "strategy")
    return metrics, len(raw), len(kept)


def build_strategy_ranking_screen(cfg: dict, period: str = "today",
                                  from_date: Optional[str] = None,
                                  to_date: Optional[str] = None,
                                  trade_type: Optional[str] = None,
                                  direction: Optional[str] = None,
                                  mode: str = "net_pnl") -> dict:
    """The whole leaderboard from ONE filtered population, so the KPI strip, the
    table, the trend summary, the winners/losers and the insights cannot describe
    different sets."""
    now = freshness.ist_now()
    frm, to = freshness.resolve_period(period, from_date, to_date, now)
    mode = mode if mode in MODE_KEYS else "net_pnl"
    meta = strategy_meta.strategy_meta(cfg)

    metrics, total_rows, kept_rows = _metrics_for(cfg, frm, to, meta,
                                                  trade_type, direction)
    by_name = {m["strategy"]: m for m in metrics}

    # ⭐ EVERY configured strategy is listed, not only those that traded: the
    # screen is a leaderboard of ALL strategies, and a strategy that produced
    # nothing in the window is itself a finding. ⛔ Its metrics stay None.
    #
    # ⚠️⚠️ THE TWO FILTERS ACT ON DIFFERENT THINGS, AND IT IS NOT A DETAIL:
    #   TRADE TYPE is a property of the STRATEGY (its YAML intent), so it
    #     filters WHICH STRATEGIES ARE LISTED. Leaving a strategy of the other
    #     type on screen with zeros would say it traded nothing as Delivery,
    #     when the truth is it is not a Delivery strategy at all.
    #   DIRECTION is a property of a TRADE, so it filters the TRADES only and
    #     every strategy stays listed — a strategy with no LONG trades in the
    #     window genuinely did zero of them, which is a real answer.
    names = set(by_name) | set(meta)
    if trade_type:
        want = trade_type.strip().title()
        names = {n for n in names
                 if (meta.get(n) or {}).get("trade_type") == want}
    names = sorted(names)
    max_trades = max([m["trades"] for m in metrics], default=0)

    # ── the previous equal-length window, for the REAL trend ────────────────
    p_frm, p_to = _prev_window(frm, to)
    prev_by_name: dict = {}
    prev_max = 0
    if p_frm:
        prev_metrics, _t, _k = _metrics_for(cfg, p_frm, p_to, meta,
                                            trade_type, direction)
        prev_by_name = {m["strategy"]: m for m in prev_metrics}
        prev_max = max([m["trades"] for m in prev_metrics], default=0)

    rows = []
    for name in names:
        m = by_name.get(name)
        info = meta.get(name) or {}
        sc = _score(m, max_trades) if m else {"score": None, "parts": {},
                                              "reason": "no completed trades in this window"}
        pm = prev_by_name.get(name)
        prev_sc = _score(pm, prev_max)["score"] if pm else None
        rows.append({
            "strategy": name,
            "display_name": info.get("display_name") or name,
            # ⭐ THE NEW COLUMN — the strategy's own YAML intent, one shared path
            "trade_type": info.get("trade_type"),
            "configured": name in meta,
            "trades": (m or {}).get("trades", 0),
            "wins": (m or {}).get("wins", 0),
            "losses": (m or {}).get("losses", 0),
            "win_rate": (m or {}).get("win_rate"),
            "roi_pct": (m or {}).get("roi_pct"),
            "profit_factor": (m or {}).get("profit_factor"),
            "gross": (m or {}).get("gross"),
            "net": (m or {}).get("net"),
            "avg_trade": (m or {}).get("avg_trade"),
            "best": (m or {}).get("best"),
            "worst": (m or {}).get("worst"),
            "score": sc["score"],
            "score_parts": sc["parts"],
            "score_reason": sc["reason"],
            "prev_score": prev_sc,
            "trend": _classify_trend(sc["score"], prev_sc),
        })

    ranked = _rank(rows, mode)
    traded = [r for r in ranked if r["trades"]]

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "period": period, "from": frm, "to": to,
        "prev_from": p_frm, "prev_to": p_to,
        "mode": mode, "modes": [{"key": k, "label": lbl} for k, lbl in MODES],
        "date_ranges": [{"key": k, "label": lbl} for k, lbl in DATE_RANGES],
        "active": {"trade_type": trade_type or None, "direction": direction or None},
        "rows": ranked, "count": len(ranked), "traded_count": len(traded),
        "trades_in_window": total_rows, "trades_after_filters": kept_rows,
        "kpi": _kpi(traded),
        "score_weights": [{"key": k, "label": lbl, "pct": w} for k, lbl, w in SCORE_WEIGHTS],
        "trend_summary": _trend_summary(ranked),
        "winners_losers": _winners_losers(traded),
        "insights": _insights(traded),
        "filters": {
            "trade_type": strategy_meta.trade_type_options(meta),
            "direction": list(DIRECTIONS),
        },
        "gaps": {
            "trade_type": _gap(
                "a strategy whose YAML carries no readable `intent` has no trade "
                "type; the enum the production validator permits is INTRADAY or "
                "DELIVERY, and anything else is shown as unavailable rather than "
                "guessed"),
            "trend": _gap(
                "trend compares this window's score with the immediately "
                "preceding window of equal length; a strategy that did not trade "
                "in that earlier window has nothing to compare against and is "
                "shown as unavailable rather than called stable"),
            "score": _gap(
                "score needs completed trades; a strategy with none in the window "
                "scores unavailable rather than 0, because 0 would read as 'it "
                "traded and earned nothing'"),
        },
        "note": ("All performance metrics are based on completed trades only. "
                 "Activity is scored against the busiest strategy in the same "
                 "filtered set, so it is a relative measure."),
    }


def _rank(rows: list, mode: str) -> list:
    """Sort by the chosen metric, then stamp Rank. ⛔ Rows with no value for the
    metric sink to the bottom rather than sorting as zero; ties break by name so
    a refresh cannot reshuffle equal rows."""
    key = _MODE_METRIC[mode]
    def sort_key(r):
        v = r.get(key)
        return (0 if v is not None else 1, -(v if v is not None else 0), r["strategy"])
    ordered = sorted(rows, key=sort_key)
    for i, r in enumerate(ordered, start=1):
        r["rank"] = i
    return ordered


def _best(rows: list, key: str, reverse: bool = False) -> Optional[dict]:
    vals = [r for r in rows if r.get(key) is not None]
    if not vals:
        return None
    pick = (min if reverse else max)(vals, key=lambda r: r[key])
    return {"strategy": pick["strategy"], "display_name": pick["display_name"],
            "trade_type": pick["trade_type"], "value": pick[key]}


def _kpi(traded: list) -> dict:
    """The six approved cards. ⛔ Each is None when nothing in the window
    qualifies — the card then says so instead of naming an arbitrary strategy."""
    return {
        "best": _best(traded, "net"),
        "worst": _best(traded, "net", reverse=True),
        "highest_roi": _best(traded, "roi_pct"),
        "highest_win_rate": _best(traded, "win_rate"),
        "highest_profit_factor": _best(traded, "profit_factor"),
        "most_trades": _best(traded, "trades"),
    }


def _trend_summary(rows: list) -> dict:
    out = {t: 0 for t in TRENDS}
    for r in rows:
        if r.get("trend") in out:
            out[r["trend"]] += 1
    out["unavailable"] = sum(1 for r in rows if r.get("trend") is None)
    return out


def _winners_losers(traded: list, top: int = 3) -> dict:
    """TOP WINNERS / TOP LOSERS by net P&L over the SAME filtered window.
    ⛔ A strategy is never listed on both sides: winners are net > 0 only and
    losers net < 0 only, so a flat strategy appears on neither."""
    wins = sorted([r for r in traded if (r["net"] or 0) > 0],
                  key=lambda r: -r["net"])[:top]
    losses = sorted([r for r in traded if (r["net"] or 0) < 0],
                    key=lambda r: r["net"])[:top]
    fmt = lambda r: {"strategy": r["strategy"], "display_name": r["display_name"],  # noqa: E731
                     "net": r["net"], "trade_type": r["trade_type"]}
    return {"winners": [fmt(r) for r in wins], "losers": [fmt(r) for r in losses],
            "base": "net P&L over the filtered window"}


def _n(count: int, one: str, many: str) -> str:
    """A count and its noun agreeing in number. ⛔ "1 strategies" reads as a
    formatting bug and puts the number itself in doubt."""
    return "%d %s" % (count, one if count == 1 else many)


def _insights(traded: list) -> list:
    """RANKING INSIGHTS — every line is a COUNT or an AVERAGE over the same
    filtered set. ⛔ No advice is generated, and ⛔ no line appears unless the
    quantity behind it exists."""
    out = []
    scored = [r for r in traded if r["score"] is not None]
    if scored:
        avg = sum(r["score"] for r in scored) / len(scored)
        above = sum(1 for r in scored if r["score"] > avg)
        out.append({"tone": "pos",
                    "text": "%s %s performing above system average"
                            % (_n(above, "strategy", "strategies"),
                               "is" if above == 1 else "are")})
    declining = sum(1 for r in traded if r.get("trend") == "Declining")
    if declining:
        out.append({"tone": "neg",
                    "text": "%s need%s immediate review"
                            % (_n(declining, "strategy", "strategies"),
                               "s" if declining == 1 else "")})
    neg_roi = [r for r in traded if r.get("roi_pct") is not None and r["roi_pct"] < 0]
    if neg_roi:
        out.append({"tone": "warn",
                    "text": "%s %s negative ROI"
                            % (_n(len(neg_roi), "strategy", "strategies"),
                               "has" if len(neg_roi) == 1 else "have")})
    wr = [r["win_rate"] for r in traded if r.get("win_rate") is not None]
    if wr:
        out.append({"tone": "info",
                    "text": "Average Win Rate: %.2f%%" % (sum(wr) / len(wr))})
    pf = [r["profit_factor"] for r in traded if r.get("profit_factor") is not None]
    if pf:
        out.append({"tone": "info",
                    "text": "Average Profit Factor: %.2f" % (sum(pf) / len(pf))})
    return out


# ── XLSX ─────────────────────────────────────────────────────────────────────
#: ⭐ The approved column order, with TRADE TYPE inserted immediately after
#: Strategy exactly as instructed. ⛔ No other column moved.
EXPORT_HEADER = ("Rank", "Strategy", "Trade Type", "Trades", "Wins", "Losses",
                 "Win %", "ROI %", "Profit Factor", "Gross P&L", "Net P&L",
                 "Avg Trade", "Best Trade", "Worst Trade", "Score", "Trend")

#: ⛔ Never a blank cell for an unmeasured value — a reader would read blank as
#: zero. The project's marker travels into the spreadsheet.
NA = "NOT INSTRUMENTED"


def export_rows(payload: dict) -> list:
    """Exactly the rows the table is showing — the SAME filtered, SAME ranked
    list, in the SAME order."""
    def cell(v):
        # ⛔ A BLANK CELL READS AS ZERO in a spreadsheet. Every unmeasured value
        # carries the project's marker instead — money columns included, which
        # are None (⛔ not 0.0) for a strategy that traded nothing in the window.
        return NA if v is None or v == "" else v

    out = [list(EXPORT_HEADER)]
    for r in payload.get("rows") or []:
        out.append([
            cell(r.get("rank")), r.get("display_name") or r.get("strategy"),
            cell(r.get("trade_type")),
            cell(r.get("trades")), cell(r.get("wins")), cell(r.get("losses")),
            cell(r.get("win_rate")), cell(r.get("roi_pct")),
            cell(r.get("profit_factor")),
            cell(r.get("gross")), cell(r.get("net")), cell(r.get("avg_trade")),
            cell(r.get("best")), cell(r.get("worst")),
            cell(r.get("score")), cell(r.get("trend")),
        ])
    return out
