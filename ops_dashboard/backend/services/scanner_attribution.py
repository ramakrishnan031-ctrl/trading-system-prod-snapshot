"""SCREEN 21 — SCANNER ATTRIBUTION.  Which source produces the good trades.

`gui/21. Scanner_Attribution.png` + `.txt` are BINDING for structure.

═══════════════════════════════════════════════════════════════════════════════
⚖️ SCANNER *AND* STRATEGY BOTH NAME THE ROW — ONE IDENTITY, ⛔ NOT TWO DATASETS

MEASURED, ⛔ not assumed: `config/scan_webhook_map.yaml` maps 16 scanners onto
16 DISTINCT strategies, and every scanner name IS its strategy name. Scanner and
Strategy are therefore 1:1 in this system.

⚖️ 01-Sep-2026 — 👤 RAMA SUPERSEDED HIS OWN 16-Aug RULING. That ruling dropped
the artwork's `Scanner` column because printing both would put one identity in
two columns. The new contract answers that directly: "Keep the word Scanner
wherever it is meaningful in this screen; do not rename or remove the Scanner
concept merely because it maps 1:1 to Strategy." The approved order is now
  # | Scanner | Strategy (Primary) | Trade Type | Health | …
⚠️ THE 1:1 MEASUREMENT ABOVE STILL HOLDS — it was never wrong, only its
conclusion was overturned. ⛔ So the Scanner cell reads the SAME row's own
`scanners` list: one identity, shown twice by request, ⛔ NOT two datasets.

⭐ The scanner identity now appears (a) in the TABLE's own Scanner column,
(b) in the payload, (c) in the SCANNER MAPPING panel — which keeps its job of
carrying each scanner's screener URL — and (d) in the export. ⛔ All four read
the same `scanners` value; ⛔ none of them is a separate source.

═══════════════════════════════════════════════════════════════════════════════
⭐ ONE POPULATION, ONE BASE — WHERE EVERY NUMBER COMES FROM

  · THE WHOLE SCREEN is ONE `strategy_tower` build, the same today-scoped
    per-strategy assembly Screens 03 and 20 already use. ⛔ No second counter is
    written, so Screens 20 and 21 cannot report different signal, order, trade
    or rejection counts for one strategy, and HEALTH here is the SAME
    `analytics_period.health_state` Screen 20 shows.

  · SIGNALS / ACCEPTED / REJECTED ALL SHARE THE STORED-SIGNAL BASE:
        Signals  = `signals.stored`     every signal the system took in today
        Accepted = the `accepted` bucket
        Rejected = the `rejected` bucket
    ⚠️ Signals ≥ Accepted + Rejected, because DUPLICATE and EXPIRED are two
    further outcomes of the SAME base. All four travel in the payload and the
    screen states it. ⛔ The alternative — Accepted at the intake (webhook)
    level — would put a 33%-accepted figure beside a rejection donut whose
    reasons (Low Score, Risk Limit, Capital Limit) are all gates that run AFTER
    storage: two populations under one heading. `received` (the webhook intake
    count) is carried separately and labelled as such.

  · THE REJECTION DONUT SPLITS EXACTLY THAT `rejected` SET, so its four buckets
    sum to the Rejected column and to 100% — the discipline Screen 20's donut
    was corrected to on 16-Aug (it had read 36% + 68% = 104%).

  · CLASSIFICATION IS BY THE STRUCTURED `signals.status`, ⛔ NEVER by
    `signals.rejection_reason`. That free-text field embeds the symbol and the
    sizing arm values, and branching on it is the defect
    `reports/signal_status.py` exists to make impossible — on 2026-07-10 it
    reported 1,189 capital rejections when the true count was 0.

  · QUALITY SCORE is the artwork's own four components at 25% each, ⛔ not the
    three-factor PRODUCT the legacy `/api/scanner-attribution` endpoint uses
    (which is left untouched for its own callers). A component with no evidence
    contributes 0 — the convention `analytics_period` already documents — and a
    strategy with no signals at all scores None, ⛔ not 0.

⛔⛔ ONE PANEL HAS NO SOURCE AND SAYS SO — HEALTH TIMELINE. Nothing stores a
per-strategy STATE HISTORY: a state is derived at read time from today's
counters, so what a source's state was at 11:30 was never recorded. ⭐ The
artwork itself labels that panel "(Example: …)". It keeps its approved footprint
and reports the gap; ⛔ the artwork's example values are NOT rendered as data.
This is the same finding Screen 20 recorded, from the same absence.
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

from ..readers import config_reader, db_reader
from . import (analytics_period, freshness, strategy_health, strategy_meta,
               strategy_tower)

# ── the approved vocabularies ────────────────────────────────────────────────
#: The artwork's SCANNER HEALTH legend, in the approved order. Identical to
#: Screen 20's STATES because it is the SAME state machine — ⛔ not a second one.
HEALTH_STATES = ("Healthy", "Quiet", "Warning", "Silent", "Disabled")

#: The artwork's SCANNER RANKING tabs, in the approved order.
MODES = (("net_pnl", "By P&L"), ("roi", "By ROI"), ("win_rate", "By Win %"),
         ("profit_factor", "By Profit Factor"), ("trade_count", "By Trade Count"))
MODE_KEYS = tuple(k for k, _ in MODES)
_MODE_METRIC = {"net_pnl": "net", "roi": "roi_pct", "win_rate": "win_rate",
                "profit_factor": "profit_factor", "trade_count": "trades"}

#: The artwork's OPPORTUNITY QUALITY SCORE scoring components, verbatim — four
#: at 25% each, summing to 100. ⭐ These weights are the design's, ⛔ not chosen
#: here.
SCORE_WEIGHTS = (("acceptance_rate", "Acceptance Rate", 25),
                 ("trade_conversion", "Trade Conversion", 25),
                 ("win_rate", "Win Rate", 25),
                 ("profitability", "Profitability", 25))

TRENDS = ("Improving", "Stable", "Declining")

#: The artwork's SCANNER DRILLDOWN QUICK ACCESS tiles, in the order it draws
#: them, each with the TONE the artwork paints its icon. ⚠️ The artwork's six are
#: Overview · Signals · Orders · Trades · Performance · Health — ⛔ the brief's
#: list says "Warnings" where the artwork draws "Orders"; the ARTWORK is the
#: binding visual target.
#:
#: ⭐ THE TONES ARE MEASURED OFF THE ARTWORK, then mapped onto the EXISTING theme
#: tokens by nearest hue — ⛔ no new colour value is introduced (global rule 2):
#:      Overview    rgb(20,178,255)  h200  → blue    (--blue   h214)
#:      Signals     rgb(136,63,196)  h280  → purple  (--purple h266)
#:      Orders      rgb(255,167,1)   h39   → amber   (--yellow h40)
#:      Trades      rgb(19,180,67)   h137  → green   (--pos    h132)
#:      Performance rgb(19,186,250)  h200  → blue    (--blue)
#:      Health      rgb(251,65,39)   h7    → red     (--neg    h3)
DRILLDOWN = (("Overview", "/strategies", "blue"),
             ("Signals", "/signals", "purple"),
             ("Orders", "/orders", "amber"),
             ("Trades", "/trades", "green"),
             ("Performance", "/strategy-ranking", "blue"),
             ("Health", "/strategy-health", "red"))

#: How many days ACTIVITY METRICS' "Signals This Week" spans, and the window the
#: trend baseline averages over. ⭐ The SAME constant Screen 20 uses, imported
#: rather than restated so one screen cannot mean 7 days while the other means 5.
ACTIVITY_DAYS = strategy_health.ACTIVITY_DAYS

#: The score gate emits ONE status PER SCORE (REJECTED_SCORE_29 … _59). Mirrored
#: BY VALUE from `reports/signal_status.py:53` (isolation rule I1 — the GUI never
#: imports a production package). ⛔ Without collapsing the family a real day's
#: breakdown fragments into ~40 lines, none big enough to notice.
#:
#: ⚠️⚠️ THE COLLAPSED NAME MATCHES TOO — `_\d+` is OPTIONAL. `_reject_bucket` is
#: called with a raw status in one place and with the ALREADY-COLLAPSED family
#: `REJECTED_SCORE` in another (the export's breakdown sheet and `_top_reason`).
#: Measured in the shipped workbook before this was fixed: the Rejection
#: Breakdown row read `Low Score | Other Reasons | 9` — the family label and the
#: bucket beside it contradicting each other on one line.
#: ⛔ Still anchored at both ends, so `REJECTED_SCORER_X` is NOT swallowed.
_SCORE_STATUS_RE = re.compile(r"^REJECTED_SCORE(_\d+)?$")

#: The approved REJECTION ANALYSIS buckets, in the artwork's own order. Each is
#: a set of STRUCTURED statuses; the risk and capital families are the tuples
#: `db_reader` already owns, reused so Screen 03's failure strip and this donut
#: cannot disagree about what "a risk rejection" is.
#: ⛔ `Other Reasons` is a real remainder, not a dumping ground: every status in
#: it is named in the payload with its own count, so nothing hides inside it.
REJECT_BUCKETS = ("Low Score", "Risk Limit", "Capital Limit", "Other Reasons")


def _reject_bucket(status: Optional[str]) -> str:
    """Classify ONE structured signal status into an approved bucket.

    ⛔ Takes `signals.status`, ⛔ never `signals.rejection_reason`.
    """
    s = (status or "").strip().upper()
    if _SCORE_STATUS_RE.match(s):
        return "Low Score"
    if s in db_reader._RISK_REJECT_STATUSES:
        return "Risk Limit"
    if s in db_reader._CAPITAL_REJECT_STATUSES:
        return "Capital Limit"
    return "Other Reasons"


def _family(status: Optional[str]) -> str:
    """The status collapsed to its reporting family — what a breakdown groups
    by. Identity for everything except the per-score rejections. Mirrors
    `reports/signal_status.py::family` BY VALUE."""
    s = (status or "").strip().upper() or "UNKNOWN"
    return "REJECTED_SCORE" if _SCORE_STATUS_RE.match(s) else s


def _humanise(family: str) -> str:
    """`REJECTED_DAILY_LOSS` → `Daily Loss`. ⭐ A LABEL for a value that was
    already classified structurally — ⛔ nothing branches on this string.

    ⚠️ The score family is named for its APPROVED BUCKET rather than stripped
    mechanically: `REJECTED_SCORE` → "Score" would put a word on screen that
    appears nowhere in the design, beside a donut segment labelled Low Score.
    """
    s = (family or "").upper()
    if s == "REJECTED_SCORE":
        return "Low Score"
    for prefix in ("REJECTED_", "DROPPED_", "SKIPPED_"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.replace("_", " ").title() if s else "Unknown"


def _gap(reason: str, short: str = "") -> dict:
    """⭐ `reason` is the FULL statement of what is missing and why, and travels
    in the payload; `short` is the line a narrow panel PRINTS. ⛔ The short line
    never says less than the truth — the full reason is on the tooltip. Same
    contract as `strategy_health._gap`."""
    return {"measured": False, "value": None, "reason": reason,
            "short": short or reason}


def _pct(n, d) -> Optional[float]:
    """⛔ None — never 0.0 — when the denominator is absent: a percentage of
    nothing is not a number."""
    try:
        d = float(d)
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    return round(100.0 * float(n) / d, 2)


def _pct_share(a: float, b: float) -> Optional[float]:
    """100 × a / (a + b) — the bounded 0-100 form of an unbounded ratio.

    ⭐ THE SAME helper Screen 19 uses for its profitability component, so the two
    screens score the same evidence identically. ⛔ None when there is nothing to
    divide.
    """
    total = a + b
    if total <= 0:
        return None
    return round(100.0 * a / total, 2)


def _quality(row: dict) -> dict:
    """The artwork's OPPORTUNITY QUALITY SCORE — four components, 25% each.

      acceptance_rate   accepted / stored        how much of what arrived survived
      trade_conversion  trades / accepted        how much of that became a trade
      win_rate          wins / decided           how much of that won
      profitability     gross-profit share       and by how much, bounded 0-100

    ⛔ A component whose denominator is 0 contributes 0 — the convention
    `analytics_period` documents for `opportunity_quality`, reused here so two
    screens cannot score the same absence differently.
    ⛔ A source with NO SIGNALS AT ALL scores None, ⛔ not 0: zero is a
    measurement ("it produced signals and none were any good"); None is the
    absence of one.
    ⭐ `trade_conversion` is capped at 100: a trade re-entered on the same
    accepted signal would otherwise push one component past its own ceiling and
    silently inflate the composite.
    """
    stored = int(row["signals"]["stored"])
    if not stored:
        return {"score": None, "parts": {}, "reason": "no signals today"}

    accepted = int(row["signals"]["accepted"])
    trades = int(row["trading"]["open"]) + int(row["trading"]["closed"])
    wins, losses = int(row["trading"]["wins"]), int(row["trading"]["losses"])
    win_sum = float(row["performance"].get("win_sum") or 0.0)
    loss_abs = abs(float(row["performance"].get("loss_sum") or 0.0))

    parts = {
        "acceptance_rate": _pct(accepted, stored),
        "trade_conversion": (min(100.0, round(100.0 * trades / accepted, 2))
                             if accepted else None),
        "win_rate": _pct(wins, wins + losses),
        "profitability": _pct_share(win_sum, loss_abs),
    }
    total = 0.0
    for key, _label, weight in SCORE_WEIGHTS:
        v = parts.get(key)
        total += (float(v) * weight / 100.0) if v is not None else 0.0
    return {"score": int(round(total)), "parts": parts, "reason": None}


def _days_back(today: str, n: int) -> tuple:
    d = _dt.date(*(int(x) for x in today.split("-")))
    return (d - _dt.timedelta(days=n)).isoformat(), (d - _dt.timedelta(days=1)).isoformat()


def build_scanner_attribution_screen(cfg: dict, health: Optional[str] = None,
                                     trade_type: Optional[str] = None,
                                     now=None) -> dict:
    """The whole screen from ONE tower build, so the KPI strip, the table, the
    funnel, the rejection donut, the rankings and the export all describe the
    same sources at the same instant.

    ⚠️ NOT period-scoped. The artwork labels the funnel, the rejection donut and
    the profitability panel "(TODAY)" and draws no date filter; ACTIVITY METRICS
    carries its own explicit second window ("Signals This Week"). Accepting a
    `period` here would invite a reader to believe an arbitrary window is
    retrievable for panels that are today-only.
    """
    now = now or freshness.ist_now()
    today = freshness.ist_today_iso(now)

    tower = strategy_tower.build_strategy_tower(cfg, today, now)
    meta = strategy_meta.strategy_meta(cfg)
    registry = config_reader.get_scanner_registry(cfg)

    # scanner identity, kept out of the table but never lost
    scanners_of: dict = {}
    for entry in registry:
        if entry["strategy"]:
            scanners_of.setdefault(entry["strategy"], []).append(entry["scanner"])

    hist_from, hist_to = _days_back(today, ACTIVITY_DAYS)
    per_strategy_daily = db_reader.health_strategy_daily_signals(cfg, hist_from, hist_to)
    week_daily = db_reader.health_strategy_daily_signals(cfg, hist_from, today)
    rejected_rows = db_reader.signals_rejected_by_status(cfg, today)

    # {strategy: {status: n}} — the structured split, per source
    rej_by_strategy: dict = {}
    for r in rejected_rows:
        rej_by_strategy.setdefault(r["strategy"], {})[r["status"]] = r["n"]

    rows = []
    for r in tower["rows"]:
        name = r["basic"]["name"]
        info = meta.get(name) or {}
        badge = (r.get("scorecard") or {}).get("badge")
        sil = (r.get("silence") or {}).get("color")
        sig = r["signals"]
        perf = r["performance"]
        trd = r["trading"]
        trades = int(trd["open"]) + int(trd["closed"])
        q = _quality(r)
        by_status = rej_by_strategy.get(name, {})
        rej_total = int(sig["rejected"])
        rows.append({
            "strategy": name,
            "display_name": r["basic"]["display_name"],
            # ⭐ the scanner identity — in the PAYLOAD, ⛔ not a table column
            "scanners": scanners_of.get(name) or list(r["basic"]["scanners"] or []),
            "trade_type": info.get("trade_type") or r["basic"].get("trade_type"),
            "health": analytics_period.health_state(r["basic"]["enabled"], badge, sil),
            "enabled": r["basic"]["enabled"],
            # ── the funnel, all four outcomes of ONE base ──────────────────
            "signals": int(sig["stored"]),
            "accepted": int(sig["accepted"]),
            "rejected": rej_total,
            "duplicated": int(sig["duplicated"]),
            "expired": int(sig["expired"]),
            "received": int(sig["received"]),          # webhook intake, labelled
            "orders": int(r["processing"]["created"]),
            "trades": trades,
            "wins": int(trd["wins"]),
            "losses": int(trd["losses"]),
            "win_rate": trd["win_rate"],
            "profit_factor": perf["profit_factor"],
            "gross": perf["gross_pnl"],
            "net": perf["net_pnl"],
            "roi_pct": perf["roi_pct"],
            # the OPERANDS behind ROI and Profit Factor, carried so the fleet
            # panel can recompute both from sums instead of averaging ratios
            "capital_used_today": perf["capital_used_today"],
            "win_sum": perf["win_sum"],
            "loss_sum": perf["loss_sum"],
            "quality_score": q["score"],
            "quality_parts": q["parts"],
            "quality_reason": q["reason"],
            "trend": strategy_health._trend(int(sig["stored"]),
                                            per_strategy_daily.get(name, {}),
                                            ACTIVITY_DAYS),
            # ── ACTIVITY METRICS ──────────────────────────────────────────
            "signals_today": int(sig["stored"]),
            "signals_week": sum((week_daily.get(name) or {}).values()),
            "last_signal": r["health"]["last_signal"],
            "last_trade": r["health"]["last_trade"],
            # ── REJECTION detail, per source ──────────────────────────────
            "rejection_pct": _pct(rej_total, sig["stored"]),
            "reject_buckets": _bucket_counts(by_status),
            "top_reject_reason": _top_reason(by_status),
        })

    # ⭐ THE MAIN TABLE IS RANKED BY NET P&L, ALWAYS — that is the order the
    # artwork draws, and it is deliberately NOT tied to the SCANNER RANKING
    # tabs. Those five tabs switch a SEPARATE top-three panel; reordering the
    # big table under the operator when they click one would be a surprise the
    # design does not ask for. All five lists are precomputed in `ranking`, so
    # switching a tab costs no request.
    shown = _filter(rows, health, trade_type)
    ranked = _rank(shown, "net_pnl")
    counts = {s: sum(1 for r in rows if r["health"] == s) for s in HEALTH_STATES}

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "today": today,
        "rows": ranked, "count": len(ranked), "total": len(rows),
        "active": {"health": health or None, "trade_type": trade_type or None},
        "modes": [{"key": k, "label": lbl} for k, lbl in MODES],
        "table_order": "net_pnl",
        "health_states": list(HEALTH_STATES),
        "health_counts": counts,
        "kpi": _kpi(rows, registry, counts),
        "funnel": _funnel(rows),
        "rejection_analysis": _rejection_analysis(rows, rejected_rows),
        "profitability": _profitability(rows),
        "ranking": _ranking(rows),
        "trend_summary": _trend_summary(rows),
        "quality": _quality_overview(rows),
        "mapping": _mapping(registry, meta),
        # ⭐ `active` marks the FIRST tile, which the artwork paints with the
        # blue active treatment. It travels in the payload so the tile order
        # and the active one cannot drift apart in the template.
        "drilldown": [{"label": lbl, "href": href, "tone": tone,
                       "active": i == 0}
                      for i, (lbl, href, tone) in enumerate(DRILLDOWN)],
        "score_weights": [{"key": k, "label": lbl, "pct": w}
                          for k, lbl, w in SCORE_WEIGHTS],
        "filters": {"health": list(HEALTH_STATES),
                    "trade_type": strategy_meta.trade_type_options(meta)},
        "activity_days": ACTIVITY_DAYS,
        "poll_interval_ms": freshness.poll_interval_ms(cfg, now),
        # ⛔⛔ THE PANEL WITH NO SOURCE
        "health_timeline": {"available": False, "rows": []},
        "gaps": {
            "health_timeline": _gap(
                "no per-source state history is stored. A health state is "
                "derived at read time from today's counters, so past states "
                "were never recorded. The artwork marks this panel Example; "
                "those values are not shown as data.",
                "No state history is stored, so past states cannot be shown."),
        },
        "note": ("Signals, Accepted and Rejected all share the stored-signal "
                 "base; DUPLICATE and EXPIRED are two further outcomes of that "
                 "same base, so Accepted + Rejected can be less than Signals. "
                 "All times are IST (Asia/Kolkata)."),
    }


# ── the panels ───────────────────────────────────────────────────────────────
def _filter(rows: list, health: Optional[str], trade_type: Optional[str]) -> list:
    out = rows
    if health:
        want = health.strip().title()
        out = [r for r in out if r["health"] == want]
    if trade_type:
        wt = trade_type.strip().title()
        out = [r for r in out if r["trade_type"] == wt]
    return out


def _rank(rows: list, mode: str) -> list:
    """Sort by the chosen metric, then stamp Rank. ⛔ Rows with no value sink to
    the bottom rather than sorting as zero; ties break by name so a refresh
    cannot reshuffle equal rows."""
    key = _MODE_METRIC[mode]
    def sort_key(r):
        v = r.get(key)
        return (0 if v is not None else 1, -(v if v is not None else 0), r["strategy"])
    ordered = sorted(rows, key=sort_key)
    for i, r in enumerate(ordered, start=1):
        r["rank"] = i
    return ordered


def _bucket_counts(by_status: dict) -> dict:
    """{bucket: n} over the approved four. ⭐ Every bucket is present even at
    zero, so the donut always draws the same four segments."""
    out = {b: 0 for b in REJECT_BUCKETS}
    for status, n in (by_status or {}).items():
        out[_reject_bucket(status)] += int(n)
    return out


def _top_reason(by_status: dict) -> Optional[dict]:
    """The single largest rejection FAMILY, named. ⛔ None when nothing was
    rejected — a source with no rejections has no top reason, and printing one
    would be an invention."""
    fam: dict = {}
    for status, n in (by_status or {}).items():
        fam[_family(status)] = fam.get(_family(status), 0) + int(n)
    if not fam:
        return None
    name, n = max(sorted(fam.items()), key=lambda kv: kv[1])
    total = sum(fam.values())
    return {"family": name, "label": _humanise(name), "count": n,
            "pct": _pct(n, total), "bucket": _reject_bucket(name)}


def _kpi(rows: list, registry: list, counts: dict) -> dict:
    """The six approved cards.

    ⭐ TOTAL SCANNERS is the CONFIGURED scanner registry — the artwork's own
    caption says "All configured scanners" — so it counts `scan_webhook_map`
    entries, ⛔ not the strategies that happened to fire today.
    ⭐ ACTIVE SCANNERS counts SCANNERS whose linked strategy is not Disabled,
    which is what the artwork shows (9 configured, 1 disabled row, 8 active).
    ⛔⛔ IT IS NOT `total − disabled_strategies`. The map permits N:1, so a
    subtraction would take a STRATEGY count off a SCANNER count and, the moment
    two scanners feed one strategy, print a number that is neither. Both sides
    of this figure are resolved at scanner grain.
    ⛔ Each of the four "best/worst" cards is None when nothing qualifies; the
    card then says so instead of naming an arbitrary source.
    """
    total = len(registry)
    health_of = {r["strategy"]: r["health"] for r in rows}
    disabled = sum(1 for e in registry
                   if health_of.get(e["strategy"]) == "Disabled")
    active = total - disabled
    traded = [r for r in rows if r["trades"]]
    return {
        "total_scanners": total,
        "active_scanners": active,
        "active_pct": _pct(active, total),
        "disabled_scanners": disabled,
        "disabled_strategies": counts.get("Disabled", 0),
        "best": _best(traded, "net"),
        "worst": _best(traded, "net", reverse=True),
        "highest_win_rate": _best(traded, "win_rate"),
        "highest_profit_factor": _best(traded, "profit_factor"),
    }


def _best(rows: list, key: str, reverse: bool = False) -> Optional[dict]:
    vals = [r for r in rows if r.get(key) is not None]
    if not vals:
        return None
    pick = (min if reverse else max)(vals, key=lambda r: r[key])
    return {"strategy": pick["strategy"], "display_name": pick["display_name"],
            "value": pick[key], "net": pick["net"], "trades": pick["trades"]}


def _funnel(rows: list) -> dict:
    """SIGNALS → ACCEPTED → ORDERS → TRADES, every percentage over SIGNALS.

    ⭐ ONE BASE for all four stages, which is what makes the shape a funnel: a
    stage measured against its own predecessor would draw the same picture for a
    healthy pipeline and a collapsing one.
    """
    signals = sum(r["signals"] for r in rows)
    stages = [("Signals", signals), ("Accepted", sum(r["accepted"] for r in rows)),
              ("Orders", sum(r["orders"] for r in rows)),
              ("Trades", sum(r["trades"] for r in rows))]
    counts = [n for _l, n in stages]
    monotonic = all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1))
    return {
        "base": "stored signals today",
        "stages": [{"label": lbl, "n": n, "pct": _pct(n, signals)}
                   for lbl, n in stages],
        # ⛔⛔ A LATER STAGE CAN LEGITIMATELY EXCEED AN EARLIER ONE, AND THE
        # SCREEN MUST SAY SO RATHER THAN DRAW A TIDY FUNNEL. Signals are counted
        # by `received_at` today; orders and trades are counted over trades
        # CREATED today (the base Screens 03 and 20 use). A trade opened this
        # morning from a signal that arrived yesterday afternoon therefore
        # appears in Orders and Trades with no signal above it.
        # ⭐ The alternative — counting orders through `signals JOIN trades`, one
        # cohort, monotonic by construction — was rejected because it would put
        # a DIFFERENT Orders number in the funnel from the one in the table's
        # own Orders column, and one screen must not carry two.
        "monotonic": monotonic,
        "carryover_note": (
            None if monotonic else
            "a stage exceeds the one above it: signals are counted by arrival "
            "today, while orders and trades are counted over trades created "
            "today — a trade opened today from an earlier day's signal has no "
            "signal above it in this window"),
    }


def _rejection_analysis(rows: list, rejected_rows: list) -> dict:
    """The approved donut: the four buckets over the SAME rejected set the table
    counts, so they sum to the Rejected total and to 100%.

    ⭐ `families` names every distinct status inside those buckets with its own
    count, so nothing disappears into "Other Reasons".
    """
    by_status: dict = {}
    for r in rejected_rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + int(r["n"])
    buckets = _bucket_counts(by_status)
    total = sum(buckets.values())
    fam: dict = {}
    for status, n in by_status.items():
        fam[_family(status)] = fam.get(_family(status), 0) + int(n)
    return {
        "total": total,
        "base": "rejected stored signals today",
        "buckets": [{"label": b, "n": buckets[b], "pct": _pct(buckets[b], total)}
                    for b in REJECT_BUCKETS],
        "families": [{"family": f, "label": _humanise(f), "n": n,
                      "bucket": _reject_bucket(f)}
                     for f, n in sorted(fam.items(), key=lambda kv: (-kv[1], kv[0]))],
        "top": _top_reason(by_status),
        "signals": sum(r["signals"] for r in rows),
        "accepted": sum(r["accepted"] for r in rows),
    }


def _profitability(rows: list) -> dict:
    """PROFITABILITY OVERVIEW (TODAY) — fleet totals.

    ⛔ ROI and PROFIT FACTOR are RECOMPUTED FROM THE FLEET'S OWN OPERANDS, ⛔ not
    averaged across sources: the mean of per-source ratios is not the ratio of
    the sums, and printing one under the other's name is how a screen starts
    disagreeing with its own table.
    """
    gross = round(sum(float(r["gross"] or 0.0) for r in rows), 2)
    net = round(sum(float(r["net"] or 0.0) for r in rows), 2)
    # ⭐ THE SAME OPERANDS THE ROWS USE, summed — `capital_used_today` is the
    # ROI denominator the attribution doc fixes (R4: Σ margin_reserved over
    # today's trades), and win_sum / loss_sum are profit_factor's own pair.
    deployed = sum(float(r["capital_used_today"] or 0.0) for r in rows)
    win_sum = sum(float(r["win_sum"] or 0.0) for r in rows)
    loss_abs = abs(sum(float(r["loss_sum"] or 0.0) for r in rows))
    return {
        "gross": gross, "net": net,
        "roi_pct": (round(100.0 * net / deployed, 2) if deployed > 0 else None),
        "roi_base": "net P&L over capital deployed today (Σ margin_reserved)",
        "profit_factor": (round(win_sum / loss_abs, 2) if loss_abs > 0 else None),
        "profit_factor_base": "Σ winning net P&L over Σ losing net P&L",
        "deployed": round(deployed, 2),
    }


def _ranking(rows: list) -> dict:
    """SCANNER RANKING — the top three under EVERY approved mode, precomputed so
    switching tab does not re-query."""
    out = {}
    for key, _lbl in MODES:
        metric = _MODE_METRIC[key]
        vals = [r for r in rows if r.get(metric) is not None and r["trades"]]
        ordered = sorted(vals, key=lambda r: (-r[metric], r["strategy"]))[:3]
        out[key] = [{"rank": i, "strategy": r["strategy"],
                     "display_name": r["display_name"], "net": r["net"],
                     "roi_pct": r["roi_pct"], "win_rate": r["win_rate"],
                     "profit_factor": r["profit_factor"], "trades": r["trades"]}
                    for i, r in enumerate(ordered, start=1)]
    return out


def _trend_summary(rows: list) -> dict:
    out = {t: 0 for t in TRENDS}
    for r in rows:
        if r.get("trend") in out:
            out[r["trend"]] += 1
    out["unavailable"] = sum(1 for r in rows if r.get("trend") is None)
    return out


def _quality_overview(rows: list) -> dict:
    """The gauge: the mean quality score over the sources that HAVE one.

    ⛔ A source scoring None is excluded from the mean rather than counted as 0,
    and the count of excluded sources travels so the base is visible.
    """
    scored = [r for r in rows if r["quality_score"] is not None]
    return {
        "score": (int(round(sum(r["quality_score"] for r in scored) / len(scored)))
                  if scored else None),
        "scored": len(scored), "unscored": len(rows) - len(scored),
        "base": "mean quality score over sources with signals today",
        "best": _best(scored, "quality_score"),
    }


#: The artwork draws FIVE mapping rows and then a "View All Mappings →" link.
#: ⭐ The cap is the panel's APPROVED FOOTPRINT, ⛔ not a data limit: production
#: carries 16 scanners and an uncapped list grows the right rail by ~216px and
#: pushes EXPORT down. Every row still travels in the payload and on the export
#: sheet, so nothing is withheld — only the default view is bounded.
MAPPING_PREVIEW = 5


def _mapping(registry: list, meta: dict) -> dict:
    """SCANNER MAPPING — scanner name · scanner URL · linked strategy, straight
    from `scan_webhook_map.yaml`.

    ⛔ NO URL IS CONSTRUCTED. An entry without a `chartink_url` comes back None
    and the panel prints the unavailable marker rather than a plausible-looking
    address the operator might click.
    ⭐ `strategy_configured` says whether the linked strategy actually has a YAML
    — a mapping pointing at a strategy that does not exist is a real finding.
    """
    rows = []
    for e in registry:
        strat = e["strategy"]
        info = meta.get(strat) or {}
        rows.append({"scanner": e["scanner"], "url": e["chartink_url"],
                     "strategy": strat,
                     "display_name": info.get("display_name") or strat,
                     "strategy_configured": strat in meta})
    return {"rows": rows, "count": len(rows),
            "preview": MAPPING_PREVIEW,
            "hidden": max(0, len(rows) - MAPPING_PREVIEW),
            "with_url": sum(1 for r in rows if r["url"]),
            "source": "config/scan_webhook_map.yaml"}


# ── XLSX ─────────────────────────────────────────────────────────────────────
#: ⭐ THE TABLE'S OWN COLUMNS, in the table's order — a test binds the two
#: together, so the spreadsheet can never describe a different table.
#: ⚖️ 01-Sep-2026: Scanner and Trade Type joined the table on 👤 Rama's contract,
#: superseding the 16-Aug removal, so they join the export with it. ⛔ The
#: export is NOT a second opinion about the columns.
EXPORT_HEADER = ("#", "Scanner", "Strategy (Primary)", "Trade Type", "Health",
                 "Signals", "Accepted", "Rejected", "Orders", "Trades", "Win %",
                 "Profit Factor", "Net P&L", "Quality Score", "Trend")

#: ⛔ Never a blank cell for an unmeasured value — a reader would read blank as
#: zero. The project's marker travels into the spreadsheet.
NA = "NOT INSTRUMENTED"


def _cell(v):
    return NA if v is None or v == "" else v


def export_sheets(payload: dict) -> list:
    """[(title, header, rows)] — the FILTERED, RANKED view only.

    ⭐ Sheet 2 carries the SCANNER MAPPING, which is where the scanner identity
    belongs, and Sheet 3 the rejection breakdown BY NAMED FAMILY — a spreadsheet
    that exported only the four buckets would let a reader believe "Other
    Reasons" was a single cause.
    """
    table = [list(EXPORT_HEADER)]
    #: ⭐ `#` is the SERIAL of the exported order, exactly as the screen renders
    #: it — ⛔ not the payload's `rank`, which is a different quantity and stays
    #: untouched. The rows arrive already ranked, so position IS the serial.
    for i, r in enumerate(payload.get("rows") or [], start=1):
        table.append([
            i,
            #: ⛔ the SAME row's own scanner list — ⛔ not a second dataset
            _cell(" · ".join(r.get("scanners") or [])),
            r.get("display_name") or r.get("strategy"),
            #: ⛔ derived from the strategy's YAML intent, ⛔ never from a name
            _cell(r.get("trade_type")),
            _cell(r.get("health")), _cell(r.get("signals")), _cell(r.get("accepted")),
            _cell(r.get("rejected")), _cell(r.get("orders")), _cell(r.get("trades")),
            _cell(r.get("win_rate")), _cell(r.get("profit_factor")),
            _cell(r.get("net")), _cell(r.get("quality_score")), _cell(r.get("trend")),
        ])

    mapping = [["Scanner Name", "Scanner URL", "Linked Strategy", "Strategy Configured"]]
    for m in (payload.get("mapping") or {}).get("rows") or []:
        mapping.append([m["scanner"], _cell(m["url"]), _cell(m["strategy"]),
                        "YES" if m["strategy_configured"] else "NO"])

    rej = [["Reason Family", "Bucket", "Signals Rejected"]]
    for f in (payload.get("rejection_analysis") or {}).get("families") or []:
        rej.append([f["label"], f["bucket"], f["n"]])

    return [("Scanner Attribution", table[0], table[1:]),
            ("Scanner Mapping", mapping[0], mapping[1:]),
            ("Rejection Breakdown", rej[0], rej[1:])]


def export_rows(payload: dict) -> list:
    """The main sheet alone, header first — kept for callers that want the table
    and nothing else."""
    sheets = export_sheets(payload)
    return [list(sheets[0][1])] + [list(r) for r in sheets[0][2]]
