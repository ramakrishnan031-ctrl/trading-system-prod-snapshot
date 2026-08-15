"""
ops_dashboard/backend/services/execution_analytics.py

SCREEN 11 — EXECUTION ANALYTICS.  Pure aggregation over the read-only
`db_reader.execution_rows_range` spine; NO schema, NO mode branch (paper/live
aggregate identically), NO write path anywhere.

═══════════════════════════════════════════════════════════════════════════════
THE INSTRUMENTATION GAP — READ THIS BEFORE ADDING ANY DELAY
═══════════════════════════════════════════════════════════════════════════════
The reference design (`gui/11. Execution_Analytics.txt` + PNG) asks for EIGHT
lifecycle instants and SEVEN delays. This system records FOUR instants and
THREE delays. That is a REAL GAP in the trading system's instrumentation, ⛔ not
a shortfall of this screen, and the screen REPORTS it rather than hiding it:

  MEASURED                          SOURCE
  ✅ Signal Time                    `signals.received_at`
  ✅ Screening time (per step)      `screener_results.latencies` (10 steps)
  ✅ Order Submit Time              ENTRY `orders.placed_at`
  ✅ Fill Time                      ENTRY `orders.filled_at`
  ✅ Signal→Order Delay             `trades.signal_to_order_ms`
  ✅ Fill Delay                     `trades.order_to_fill_ms`
  ✅ Total Delay                    `trades.total_latency_ms`

  NOT INSTRUMENTED — ⛔ no timestamp exists, so NO value is shown
  ⛔ Risk Delay              no risk-evaluation timestamp anywhere in the repo
  ⛔ Capital Delay           no capital-evaluation timestamp anywhere
  ⛔ Order Create Time       `orders` has ONE instant; no create/submit pair
  ⛔ Exchange Accept / Delay `order_execution_log.exchange_timestamp` is a real
                             column that is NEVER WRITTEN — the sole caller of
                             `insert_order_execution_log`
                             (orders/slippage_recorder.py:149) omits the key
  ⛔ Broker Delay            no broker-side instant is recorded at all

⛔⛔ THE ONE THING THIS MODULE MUST NEVER DO is present a missing stage as a
number. A `0.00 sec` risk delay would read as "measured, and it was instant" —
the precise opposite of the truth, on a screen whose entire job is to say WHERE
the time goes. Every unmeasured stage is emitted with `measured: false` and a
`reason`, and the UI renders it as an explicit gap.

⚠️ `signal_to_order_ms` is a COMPOSITE: it spans validation, risk, capital,
order-create AND submit. It is labelled "Signal → Order" for that reason and
⛔ must never be relabelled as any single one of those stages.

DOCUMENTED FORMULAS — every one carries its BASE:
  delay_sec        = ms / 1000, from the persisted column ⛔ never re-derived
  total identity   total_latency_ms ?= signal_to_order_ms + order_to_fill_ms
                   — a PROPERTY CHECKED per row, ⛔ not assumed (each is clamped
                   to ≥0 independently for NTP skew, so they can disagree)
  status           BASE = TOTAL delay seconds, the approved bands:
                     FAST 0–2s · MODERATE 2–5s · SLOW >5s
                     ⛔ a row with no total is UNMEASURED, never FAST
  bucket           BASE = TOTAL delay seconds: 0-1 · 1-2 · 2-5 · 5-10 · 10+
  throughput       counts PER MINUTE from three independent clocks:
                     signals   `signals.received_at`
                     created   ENTRY `orders.placed_at`
                     filled    ENTRY `orders.filled_at`
                   ⛔ never one series divided into three.
"""
from __future__ import annotations

from typing import Optional

from ..readers import db_reader
from . import freshness
from .analytics_period import _TOD_BUCKETS, _tod_bucket, _trade_type

# ─────────────────────────────────────────────────────────────────────────────
# Approved constants
# ─────────────────────────────────────────────────────────────────────────────

# Delay-distribution buckets, in SECONDS of TOTAL delay. [lo, hi) so a row lands
# in exactly one — the five are a partition.
_DELAY_BUCKETS = (("0-1 sec", 0.0, 1.0), ("1-2 sec", 1.0, 2.0),
                  ("2-5 sec", 2.0, 5.0), ("5-10 sec", 5.0, 10.0),
                  ("10+ sec", 10.0, None))

# Status bands, in SECONDS of TOTAL delay. The approved three.
_FAST_MAX, _MODERATE_MAX = 2.0, 5.0
_FAST, _MODERATE, _SLOW, _UNMEASURED = "FAST", "MODERATE", "SLOW", "UNMEASURED"
_STATUSES = (_FAST, _MODERATE, _SLOW, _UNMEASURED)

# Warning thresholds, in SECONDS. Only the two the data can actually support:
# the reference's "Exchange Delay > 3s" and "Broker Delay > 3s" are ⛔ NOT
# emittable — neither quantity is recorded (see the module header).
_WARN_TOTAL_SEC, _WARN_FILL_SEC = 5.0, 3.0

# The delay stages, IN LIFECYCLE ORDER, each declaring whether it is measurable.
# ⭐ The unmeasurable ones are listed HERE rather than omitted, so the screen can
# show the operator that the stage exists and is NOT instrumented.
_STAGES = (
    {"key": "screening_ms", "label": "Screening Delay", "measured": True,
     "source": "screener_results.latencies — sum of the 10 screening steps"},
    {"key": "signal_to_order_ms", "label": "Signal → Order Delay", "measured": True,
     "source": "trades.signal_to_order_ms — signals.received_at → ENTRY orders.placed_at",
     "note": "COMPOSITE: contains validation, risk, capital, order-create and submit"},
    {"key": "risk_ms", "label": "Risk Delay", "measured": False,
     "reason": "no risk-evaluation timestamp is recorded anywhere in the system"},
    {"key": "capital_ms", "label": "Capital Delay", "measured": False,
     "reason": "no capital-evaluation timestamp is recorded anywhere in the system"},
    {"key": "exchange_ms", "label": "Exchange Delay", "measured": False,
     "reason": "order_execution_log.exchange_timestamp exists as a column but is "
               "never written — the only caller omits the key, so it is always NULL"},
    {"key": "order_to_fill_ms", "label": "Fill Delay", "measured": True,
     "source": "trades.order_to_fill_ms — ENTRY orders.placed_at → filled_at",
     "note": "contains the exchange-accept segment, which cannot be split out"},
    {"key": "total_latency_ms", "label": "Total Delay", "measured": True,
     "source": "trades.total_latency_ms — signals.received_at → filled_at"},
)

_MEASURED_STAGES = tuple(s["key"] for s in _STAGES if s["measured"])

# Filterable dimensions. ⛔ Scanner is absent on purpose: strategy IS the scanner
# identity on this system (Rama, 14-Aug; measured on Screen 07 — signals.scanner
# EQUALS trades.strategy on all 603 trades and all 127,246 signals).
_EXEC_FILTERS = ("strategy", "symbol", "trade_type", "direction", "status")

# ── TRADE STATE, which is a DIFFERENT axis from execution status ─────────────
# `status` above grades the execution SPEED (fast/moderate/slow). This grades
# where the trade IS in its lifecycle. ⛔ They must not be merged: a CLOSED trade
# can be slow, and an OPEN trade has no speed grade at all.
_CLOSED, _OPEN, _PENDING, _REJECTED, _UNKNOWN_STATE = (
    "CLOSED", "OPEN", "PENDING", "REJECTED", "UNKNOWN")
_TRADE_STATES = (_CLOSED, _OPEN, _PENDING, _REJECTED, _UNKNOWN_STATE)

# Throughput: the session window the chart runs over, and the point budget that
# decides the bucket width. 09:15–15:30 are the approved session bounds.
_SESSION_OPEN, _SESSION_CLOSE = "09:15", "15:30"
_TP_MAX_POINTS = 130
_TP_BUCKET_LADDER = (1, 2, 5, 10, 15, 30, 60, 120)


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────
def _f(v) -> Optional[float]:
    """float(v) or None — ⛔ never 0.0 for a missing value."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sec(ms) -> Optional[float]:
    v = _f(ms)
    return round(v / 1000.0, 3) if v is not None else None


def _avg(vals: list) -> Optional[float]:
    """Mean, or None when there is nothing to average. ⛔ Never 0.0 — an empty
    set has no mean, and 0.00 would read as a measurement."""
    return round(sum(vals) / len(vals), 3) if vals else None


def classify(total_sec: Optional[float]) -> str:
    """Status band. BASE = TOTAL delay in seconds.

    ⛔ A row with no total delay is UNMEASURED, never FAST: a PENDING or REJECTED
    trade never filled, and calling that 'fast' would rank the failures first."""
    if total_sec is None:
        return _UNMEASURED
    if total_sec <= _FAST_MAX:
        return _FAST
    if total_sec <= _MODERATE_MAX:
        return _MODERATE
    return _SLOW


def trade_state(row: dict) -> str:
    """Where the trade IS in its lifecycle — ⛔ never inferred from the delay.

    Read in precedence order from the AUTHORITATIVE fields:
      CLOSED   `trades.exit_time` present ⇒ the position was fully closed
      REJECTED the ENTRY order was rejected/failed ⇒ there is no position
      OPEN     entry filled (`trades.entry_time`) but no exit yet
      PENDING  an ENTRY order exists but has not filled
    ⛔ A row is never defaulted to CLOSED, which is what would manufacture an
    exit time and a duration for a position that is still live."""
    st = (row.get("status") or "").upper()
    ost = (row.get("entry_status") or "").upper()
    if row.get("exit_time"):
        return _CLOSED
    if "REJECT" in ost or "FAIL" in ost or "REJECT" in st:
        return _REJECTED
    if row.get("entry_time"):
        return _OPEN
    if row.get("entry_placed_at"):
        return _PENDING
    return _UNKNOWN_STATE


def _duration_sec(entry_time, exit_time) -> Optional[float]:
    """Trade Duration in seconds = exit − ENTRY FILL.

    ⛔ NOT from signal time and ⛔ NOT from order-submit time: those measure the
    execution latency this screen already reports, and conflating them would
    inflate every duration by the entry delay.

    Both operands are IST ISO strings written by the system's own clock
    (core.time_authority), so this is a plain difference — ⛔ no tz conversion,
    and ⛔ no server locale is consulted. Returns None unless BOTH exist, so an
    open position can never acquire a duration."""
    from datetime import datetime

    if not entry_time or not exit_time:
        return None
    try:
        a = datetime.fromisoformat(str(entry_time))
        b = datetime.fromisoformat(str(exit_time))
        # ⚠️ The subtraction is INSIDE the try on purpose: a MIXED aware/naive
        # pair parses fine and then raises TypeError here. Leaving it outside
        # turned one malformed stamp into a 500 for the whole screen — found by
        # the test, ⛔ not by the happy path, which never mixes the two.
        d = (b - a).total_seconds()
    except (ValueError, TypeError):
        return None
    # ⛔ A negative span is not a duration. It means the two stamps disagree
    # (clock skew, or a back-dated close) and is reported as unmeasurable
    # rather than shown as a plausible small number.
    return round(d, 3) if d >= 0 else None


def fmt_duration(sec: Optional[float]) -> Optional[str]:
    """HH:MM:SS(.cc) — the project's duration shape. None stays None so the UI
    renders the established dash, ⛔ never 00:00:00."""
    if sec is None:
        return None
    total = int(sec)
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    frac = sec - total
    base = "%02d:%02d:%02d" % (h, m, s)
    return base + (".%02d" % round(frac * 100) if total < 60 and frac else "")


def delay_bucket(total_sec: Optional[float]) -> Optional[str]:
    if total_sec is None:
        return None
    for label, lo, hi in _DELAY_BUCKETS:
        if total_sec >= lo and (hi is None or total_sec < hi):
            return label
    return None


def _row_day(r: dict) -> Optional[str]:
    """The trade's own day — what the period filter selects on."""
    return str(r.get("created_at") or "")[:10] or None


def _row_instant(r: dict) -> str:
    """The single instant the row's Trading Date + Time both render.

    The signal instant when it falls on the trade's day; otherwise the trade's
    creation. ⛔ Never a date from one and a time from the other."""
    day = _row_day(r)
    sig = str(r.get("signal_received_at") or "")
    if day and sig[:10] == day:
        return sig
    return str(r.get("created_at") or "")


def _enrich(rows: list, scores: dict, screening: dict) -> list:
    """One pass giving every row the derived fields the whole screen shares, so
    no panel can compute a status differently from the table."""
    out = []
    for r in rows:
        e = dict(r)
        scr = screening.get(r.get("signal_id")) or {}
        s2o = _f(r.get("signal_to_order_ms"))
        o2f = _f(r.get("order_to_fill_ms"))
        tot = _f(r.get("total_latency_ms"))
        tot_sec = _sec(tot)

        # ⭐ THE IDENTITY IS CHECKED, ⛔ NOT ASSUMED. `total_latency_ms` is written
        # independently of the two parts and each is clamped to ≥0 separately, so
        # NTP skew can make them disagree. A row where they do is FLAGGED rather
        # than quietly reconciled.
        parts_ok = None
        if tot is not None and s2o is not None and o2f is not None:
            parts_ok = abs(tot - (s2o + o2f)) <= 1.0        # 1 ms tolerance

        sc = scores.get(r.get("signal_id")) or {}
        e.update({
            # ⚠️⚠️ `date` and `time` MUST come from THE SAME INSTANT. The first
            # version took the date from `created_at` and the time from the
            # signal, so a trade created today from a signal received yesterday
            # rendered as "2026-08-15  10:00:00" — a date and a time that never
            # coexisted. Found by the newest-first ordering test, which could not
            # be satisfied while the sort key was two different clocks.
            #
            # RESOLUTION: the trade day governs (it is what the period filter
            # selects on), and the time is the signal instant ONLY when the
            # signal lands on that same day. Otherwise both come from
            # `created_at` and the off-day signal is surfaced in the remarks and
            # exported in full under `Signal Time` — ⛔ nothing is hidden.
            "date": _row_day(r),
            "time": _row_instant(r)[11:19] or None,
            "strategy": r.get("strategy"),
            "symbol": (r.get("symbol") or "").upper() or None,
            "trade_type": _trade_type({"product": r.get("entry_product")}),
            "direction": (r.get("direction") or "").upper() or None,
            "system_score": sc.get("system_score"),
            "score_threshold": sc.get("score_threshold"),

            # instants — ⛔ only the four that exist
            "signal_time": str(r.get("signal_received_at") or "")[11:19] or None,
            "screening_time": str(scr.get("ts") or "")[11:19] or None,
            "order_submit_time": str(r.get("entry_placed_at") or "")[11:19] or None,
            "fill_time": str(r.get("entry_filled_at") or "")[11:19] or None,

            # delays, in SECONDS, from the persisted columns
            "screening_sec": _sec(scr.get("total_ms")),
            "signal_to_order_sec": _sec(s2o),
            "order_to_fill_sec": _sec(o2f),
            "total_sec": tot_sec,
            "parts_reconcile": parts_ok,

            # ── entry/exit lifecycle. A SUPPLEMENT to the execution columns,
            #    ⛔ not a replacement: duration answers "how long was I in the
            #    trade", the delays answer "how long did it take to get in".
            "entry_fill_time": str(r.get("entry_time") or "")[11:19] or None,
            "exit_time": str(r.get("exit_time") or "")[11:19] or None,
            "exit_date": str(r.get("exit_time") or "")[:10] or None,
            "exit_reason": r.get("exit_reason"),
            "trade_state": trade_state(r),
            "duration_sec": _duration_sec(r.get("entry_time"), r.get("exit_time")),
            "duration": fmt_duration(_duration_sec(r.get("entry_time"),
                                                   r.get("exit_time"))),

            "status": classify(tot_sec),
            "delay_bucket": delay_bucket(tot_sec),
            "tod_bucket": _tod_bucket(r.get("signal_received_at") or r.get("created_at")),
            "order_status": r.get("entry_status"),
            "remarks": _remarks(r, tot_sec, parts_ok),
        })
        out.append(e)
    return out


def _remarks(r: dict, tot_sec, parts_ok) -> Optional[str]:
    """The honest reason a row has no timing — ⛔ never left blank in a way that
    reads as 'nothing to say'."""
    bits = []
    st = (r.get("entry_status") or "").upper()
    if tot_sec is None:
        # ⛔ The four causes are DISTINCT and must not be collapsed. Saying "not
        # filled yet" about a CLOSED trade that simply has no ENTRY order row
        # would be a false statement about the trade's state on a screen whose
        # whole job is to say where the time went.
        if not r.get("entry_placed_at"):
            bits.append("no ENTRY order row — submit and fill instants do not "
                        "exist for this trade")
        elif "REJECT" in st or "FAIL" in st:
            bits.append("entry order %s — never filled, so no fill time exists"
                        % (st.lower() or "rejected"))
        elif not r.get("entry_filled_at"):
            bits.append("entry order still open — timing is written only on fill")
        else:
            bits.append("filled, but no latency was recorded for this trade")
    if parts_ok is False:
        bits.append("parts do not sum to total (clock skew)")
    sig = str(r.get("signal_received_at") or "")
    day = _row_day(r)
    if sig and day and sig[:10] != day:
        bits.append("signal received %s, a different day from the trade" % sig[:10])
    rq, qf = _f(r.get("entry_qty_requested")), _f(r.get("entry_qty_filled"))
    if rq and qf is not None and 0 < qf < rq:
        bits.append("partial fill %g/%g" % (qf, rq))
    if r.get("entry_rejection_reason"):
        bits.append(str(r["entry_rejection_reason"])[:60])
    return " · ".join(bits) or None


def _apply_filters(rows: list, f: dict) -> list:
    """THE single filter gate. Every panel on Screen 11 is derived from the list
    this returns — KPIs, timing table, delay summary, rankings, distribution,
    throughput, trend, warnings AND the export — so they CANNOT describe
    different populations."""
    out = rows
    if f.get("strategy"):
        out = [r for r in out if (r.get("strategy") or "") == f["strategy"]]
    if f.get("symbol"):
        out = [r for r in out if (r.get("symbol") or "") == f["symbol"].upper()]
    if f.get("trade_type"):
        out = [r for r in out if r.get("trade_type") == f["trade_type"].upper()]
    if f.get("direction"):
        out = [r for r in out if (r.get("direction") or "") == f["direction"].upper()]
    if f.get("status"):
        out = [r for r in out if r.get("status") == f["status"].upper()]
    return out


def _measured(rows: list) -> list:
    return [r for r in rows if r.get("total_sec") is not None]


def _rank(rows: list, key: str, label: str) -> list:
    """Ranking for one dimension. Averages are over MEASURED rows only, and each
    group reports how many of its executions were measurable."""
    groups: dict = {}
    for r in rows:
        k = r.get(key)
        if k is None:
            continue
        g = groups.setdefault(k, {label: k, "executions": 0, "measured": 0,
                                  "slow": 0, "_vals": []})
        g["executions"] += 1
        if r["status"] == _SLOW:
            g["slow"] += 1
        v = r.get("total_sec")
        if v is not None:
            g["measured"] += 1
            g["_vals"].append(v)
    out = []
    for g in groups.values():
        vals = g.pop("_vals")
        g["avg_delay_sec"] = _avg(vals)
        g["worst_delay_sec"] = round(max(vals), 3) if vals else None
        g["fastest_sec"] = round(min(vals), 3) if vals else None
        out.append(g)
    # Slowest average first; a group with nothing measured sinks to the bottom
    # rather than sorting as if its average were zero.
    return sorted(out, key=lambda g: (g["avg_delay_sec"] is None,
                                      -(g["avg_delay_sec"] or 0.0), g[label]))


def _distribution(rows: list) -> list:
    """All five buckets, always. A bucket with no orders reports observed=False
    — ⛔ never a 0 that reads as a measured absence of slow orders."""
    m = _measured(rows)
    total = len(m)
    out = []
    for label, _lo, _hi in _DELAY_BUCKETS:
        n = sum(1 for r in m if r["delay_bucket"] == label)
        out.append({"bucket": label, "orders": n, "observed": bool(m),
                    "pct": (round(100.0 * n / total, 2) if total else None)})
    return out


def _delay_summary(rows: list) -> list:
    """The reference's seven-row summary, IN LIFECYCLE ORDER, with the three
    un-instrumented stages present and explicitly marked.

    ⭐ Listing them rather than dropping them is the point: an operator asking
    'where does the time go?' must be able to see that risk, capital and
    exchange are NOT MEASURED — otherwise the screen implies the remaining
    stages account for everything."""
    out = []
    for st in _STAGES:
        row = {"stage": st["label"], "measured": st["measured"],
               "key": st["key"]}
        if st["measured"]:
            # ⛔ BLOCK, ⛔ do not degrade. Marking a stage `measured` requires a
            # DELIBERATE EDIT to _STAGES, so a missing mapping is a developer
            # error, not an environment condition — and the failure mode it
            # would otherwise take is the worst one available here: falling back
            # to an empty list would publish `avg: null` under `measured: true`,
            # i.e. a stage claiming to be instrumented while showing nothing.
            # (The fail-fast-vs-degrade discriminator: a deliberate act ⇒ block.)
            key = {"screening_ms": "screening_sec",
                   "signal_to_order_ms": "signal_to_order_sec",
                   "order_to_fill_ms": "order_to_fill_sec",
                   "total_latency_ms": "total_sec"}.get(st["key"])
            if key is None:
                raise KeyError(
                    "execution_analytics._STAGES: stage %r is marked measured "
                    "but has no row-field mapping in _delay_summary. Add the "
                    "mapping, or leave the stage measured=False with a reason."
                    % st["key"])
            vals = [r[key] for r in rows if r.get(key) is not None]
            row.update({"avg_sec": _avg(vals), "n": len(vals),
                        "worst_sec": round(max(vals), 3) if vals else None,
                        "source": st["source"], "note": st.get("note")})
        else:
            row.update({"avg_sec": None, "n": 0, "worst_sec": None,
                        "reason": st["reason"]})
        out.append(row)
    return out


def _trend(rows: list) -> tuple:
    """Average TOTAL delay through the session, bucketed by SIGNAL time — when
    the execution began. The buckets are `analytics_period._TOD_BUCKETS`,
    imported rather than re-declared so Screens 09/10/11 cannot drift into three
    definitions of '11:00–12:00'. Their edges are the approved timeline."""
    by: dict = {b[0]: [] for b in _TOD_BUCKETS}
    outside = 0
    for r in rows:
        b = r.get("tod_bucket")
        if b in by:
            by[b].append(r)
        elif r.get("signal_received_at") or r.get("created_at"):
            outside += 1
    out = []
    for label, lo, _hi in _TOD_BUCKETS:
        rs = by[label]
        m = _measured(rs)
        out.append({"bucket": label, "at": lo, "executions": len(rs),
                    "measured": len(m), "observed": bool(m),
                    "avg_delay_sec": _avg([r["total_sec"] for r in m])})
    return out, outside


def _hhmm_to_min(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _throughput(rows: list, signal_minutes: list, frm: str, to: str) -> tuple:
    """THROUGHPUT AS A RATE, over a CONTINUOUS, GAP-FILLED session axis.

    ⚠️⚠️ REBUILT after the first version drew a misleading chart, and the two
    defects are worth naming because they are easy to reintroduce:

      ① FALSE CONTINUITY. It plotted one point per minute that HAPPENED to have
         activity and joined them, so 09:22 on Monday sat next to 10:34 on
         Thursday as if they were adjacent minutes. ⛔ That is not a time axis.
      ② 0/1 SAWTOOTH. Because only active minutes existed, every point was 1 and
         the gaps were invisible, so the y-axis read as on/off noise instead of
         a rate — the exact opposite of "identify system stress periods".

    THE FIX, and each part is load-bearing:
      * the axis covers EVERY bucket of the session window on EVERY day in range,
        so a quiet minute is a REAL ZERO and a busy one visibly spikes;
      * the line BREAKS between days — ⛔ no segment ever spans a date boundary;
      * the bucket width is chosen from the range so the chart keeps a readable
        number of points, and it is REPORTED (`bucket_min`) rather than implied;
      * the plotted value is EVENTS PER MINUTE (count ÷ bucket width), so the
        unit stays "per minute" whatever the bucket is. ⛔ Not a raw count, which
        would silently change meaning as the window grows.

    THREE INDEPENDENT CLOCKS, ⛔ never one series divided three ways: signals
    arrive whether or not an order follows, and an order is created whether or
    not it fills. Each is counted from its own timestamp, so
    Signals ≥ Created ≥ Filled emerges from the data instead of being imposed.

    ⚠️ The SIGNAL series is whole-population (SQL over `signals.received_at`)
    while the order series come from the FILTERED rows — stated on the panel,
    because a signal that produced no trade has no strategy to filter on.
    """
    from datetime import date, timedelta

    def _stamp_minutes(key):
        acc: dict = {}
        for r in rows:
            ts = str(r.get(key) or "")
            if len(ts) >= 16:
                acc[ts[:16]] = acc.get(ts[:16], 0) + 1
        return acc

    created = _stamp_minutes("entry_placed_at")
    filled = _stamp_minutes("entry_filled_at")
    sigs = {m["minute"]: m["n"] for m in (signal_minutes or [])}

    totals = {"signals": sum(sigs.values()), "created": sum(created.values()),
              "filled": sum(filled.values()),
              "peak_signals_per_min": max(sigs.values()) if sigs else 0,
              "peak_created_per_min": max(created.values()) if created else 0,
              "peak_filled_per_min": max(filled.values()) if filled else 0}

    try:
        d0 = date.fromisoformat(frm)
        d1 = date.fromisoformat(to)
    except (ValueError, TypeError):
        return [], dict(totals, bucket_min=1, days=0)
    days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    if not days:
        return [], dict(totals, bucket_min=1, days=0)

    open_m, close_m = _hhmm_to_min(_SESSION_OPEN), _hhmm_to_min(_SESSION_CLOSE)
    span = max(1, close_m - open_m)

    # BUCKET WIDTH — the NARROWEST rung that keeps the axis readable.
    #
    # ⚠️ AN OCCUPANCY RULE WAS TRIED AND REJECTED BY MEASUREMENT. The idea was to
    # widen until a decent share of buckets were occupied, so a quiet day would
    # not render as one-event spikes. It does not work, because real activity is
    # CLUSTERED, not thin: with every event inside a five-minute burst, widening
    # never raises occupancy — it just collapses the whole day into 3 buckets
    # (measured: today went from 75 points to 4). ⛔ Worse than the problem.
    #
    # ⭐ AND THE SPIKES ARE NOT A DEFECT WHEN THE DATA IS GENUINELY SPARSE: a day
    # with 17 signals in one burst SHOULD show one burst. Smoothing it would be
    # manufacturing a shape the data does not have. What was actually wrong with
    # the first version is fixed elsewhere — real zeros, a per-minute rate axis,
    # and a line that breaks between days.
    bucket = _TP_BUCKET_LADDER[-1]
    for b in _TP_BUCKET_LADDER:
        if (span / b) * len(days) <= _TP_MAX_POINTS:
            bucket = b
            break

    series = []
    for di, day in enumerate(days):
        iso = day.isoformat()
        for start in range(open_m, close_m, bucket):
            s = c = f = 0
            for m in range(start, min(start + bucket, close_m)):
                key = "%sT%02d:%02d" % (iso, m // 60, m % 60)
                s += sigs.get(key, 0)
                c += created.get(key, 0)
                f += filled.get(key, 0)
            series.append({
                "day": iso, "day_index": di,
                "at": "%02d:%02d" % (start // 60, start % 60),
                "minute": "%sT%02d:%02d" % (iso, start // 60, start % 60),
                # per-MINUTE rate, so the unit is invariant to the bucket width
                "signals": round(s / bucket, 4), "created": round(c / bucket, 4),
                "filled": round(f / bucket, 4),
                "signals_n": s, "created_n": c, "filled_n": f,
            })

    # Events that fell OUTSIDE the session window are counted in the totals but
    # sit in no bucket — reported rather than silently dropped off the chart.
    in_window = sum(p["signals_n"] + p["created_n"] + p["filled_n"] for p in series)
    all_events = totals["signals"] + totals["created"] + totals["filled"]
    totals.update({"bucket_min": bucket, "days": len(days),
                   "outside_session": max(0, all_events - in_window)})
    return series, totals


def _warnings(rows: list) -> list:
    """Recent execution warnings, newest first.

    ⛔ Only the two the data supports. The reference also asks for 'Exchange
    Delay > 3 sec' and 'Broker Delay > 3 sec'; NEITHER quantity is recorded
    anywhere (see the module header), so ⛔ no such warning is emitted — a
    warning that can never fire is worse than none, because its silence reads as
    an all-clear."""
    out = []
    for r in rows:
        if r.get("total_sec") is not None and r["total_sec"] > _WARN_TOTAL_SEC:
            out.append({"kind": "TOTAL_DELAY", "severity": "SLOW",
                        "label": "Execution delay > %.0f sec" % _WARN_TOTAL_SEC,
                        "value_sec": r["total_sec"], "symbol": r.get("symbol"),
                        "strategy": r.get("strategy"), "time": r.get("time"),
                        "date": r.get("date"), "trade_id": r.get("trade_id")})
        if r.get("order_to_fill_sec") is not None and r["order_to_fill_sec"] > _WARN_FILL_SEC:
            out.append({"kind": "FILL_DELAY", "severity": "MODERATE",
                        "label": "Fill delay > %.0f sec" % _WARN_FILL_SEC,
                        "value_sec": r["order_to_fill_sec"], "symbol": r.get("symbol"),
                        "strategy": r.get("strategy"), "time": r.get("time"),
                        "date": r.get("date"), "trade_id": r.get("trade_id")})
    return sorted(out, key=lambda w: ((w.get("date") or ""), (w.get("time") or "")),
                  reverse=True)


def _status_counts(rows: list) -> dict:
    return {s: sum(1 for r in rows if r["status"] == s) for s in _STATUSES}


# ─────────────────────────────────────────────────────────────────────────────
# The screen
# ─────────────────────────────────────────────────────────────────────────────
def build_execution_analytics(cfg, period="today", from_date=None, to_date=None, *,
                              strategy=None, symbol=None, trade_type=None,
                              direction=None, status=None, limit=None) -> dict:
    """Screen 11. One filtered population feeds every panel."""
    frm, to = freshness.resolve_period(period, from_date, to_date)

    raw = db_reader.execution_rows_range(cfg, frm, to, limit=limit)
    sig_ids = [r.get("signal_id") for r in raw]
    all_rows = _enrich(raw, db_reader.signal_scores(cfg, sig_ids),
                       db_reader.screening_latencies(cfg, sig_ids))

    options = {
        "strategy": sorted({r["strategy"] for r in all_rows if r.get("strategy")}),
        "symbol": sorted({r["symbol"] for r in all_rows if r.get("symbol")}),
        "trade_type": sorted({r["trade_type"] for r in all_rows}),
        "direction": sorted({r["direction"] for r in all_rows if r.get("direction")}),
        "status": list(_STATUSES),
    }
    active = {"strategy": strategy, "symbol": symbol, "trade_type": trade_type,
              "direction": direction, "status": status}
    rows = _apply_filters(all_rows, active)

    counts = _status_counts(rows)
    measured = _measured(rows)
    vals = [r["total_sec"] for r in measured]
    fill_vals = [r["order_to_fill_sec"] for r in rows
                 if r.get("order_to_fill_sec") is not None]
    trend, outside_session = _trend(rows)
    series, tp_totals = _throughput(
        rows, db_reader.signal_throughput_range(cfg, frm, to), frm, to)
    states = {s: sum(1 for r in rows if r["trade_state"] == s) for s in _TRADE_STATES}
    closed = [r for r in rows if r.get("duration_sec") is not None]

    fastest = min(measured, key=lambda r: r["total_sec"], default=None)
    slowest = max(measured, key=lambda r: r["total_sec"], default=None)
    unreconciled = sum(1 for r in rows if r.get("parts_reconcile") is False)

    return {
        "period": period, "from": frm, "to": to,
        "filters": {"active": {k: v for k, v in active.items() if v},
                    "options": options, "keys": list(_EXEC_FILTERS)},

        # ── KPI deck. `total_signals` is the WHOLE-POPULATION stored-signal count
        #    for the window (SQL over `signals`), NOT the filtered trade count —
        #    they are different denominators and `signals_basis` says so.
        "totals": {
            "total_signals": tp_totals["signals"],
            "total_orders": len(rows),
            "measured": len(measured),
            "unmeasured": counts[_UNMEASURED],
            "avg_delay_sec": _avg(vals),
            "fastest_sec": round(min(vals), 3) if vals else None,
            "fastest_symbol": fastest["symbol"] if fastest else None,
            "slowest_sec": round(max(vals), 3) if vals else None,
            "slowest_symbol": slowest["symbol"] if slowest else None,
            "avg_fill_sec": _avg(fill_vals),
            "fast": counts[_FAST], "moderate": counts[_MODERATE], "slow": counts[_SLOW],
            "pct_basis": "share of the filtered execution population",
            "signals_basis": "every stored signal in the window (SQL over signals."
                             "received_at) — ⛔ NOT the filtered trade count; a signal "
                             "that produced no trade has no strategy to filter on",
        },

        "rows": rows,
        "delay_summary": _delay_summary(rows),
        "stages": [{"label": s["label"], "measured": s["measured"],
                    "reason": s.get("reason"), "note": s.get("note")} for s in _STAGES],
        "instrumentation_note": (
            "3 of the reference design's 7 delays are NOT INSTRUMENTED in this "
            "system: risk and capital evaluation record no timestamp at all, and "
            "order_execution_log.exchange_timestamp is never written. They are "
            "listed as gaps rather than shown as 0.00 — a zero would read as "
            "'measured, and it was instant'."),
        "composite_note": (
            "Signal → Order is a COMPOSITE spanning validation, risk, capital, "
            "order-create and submit; it cannot be split with the timestamps that "
            "exist"),

        "per_strategy": _rank(rows, "strategy", "strategy"),
        "per_symbol": _rank(rows, "symbol", "symbol"),
        "distribution": _distribution(rows),
        "throughput": {"series": series, "totals": tp_totals},
        "throughput_note": (
            "events per MINUTE on a gap-filled %s-minute grid across the %d "
            "session day(s) 09:15–15:30 — a quiet minute is a real zero, and the "
            "line BREAKS between days so no segment spans a date boundary. Three "
            "independent clocks (signals.received_at · ENTRY orders.placed_at · "
            "ENTRY orders.filled_at), ⛔ not one series divided three ways. The "
            "signal series is whole-population; the order series follow the filters"
            % (tp_totals.get("bucket_min", 1), tp_totals.get("days", 0))
            + (" · %d event(s) fell outside the session window and sit in no bucket"
               % tp_totals["outside_session"] if tp_totals.get("outside_session") else "")),

        # ── ENTRY/EXIT LIFECYCLE — a supplement to the execution columns.
        "trade_states": [
            {"state": s, "count": states[s]} for s in _TRADE_STATES if states[s]],
        "duration": {
            "closed": len(closed), "total": len(rows),
            "avg_sec": _avg([r["duration_sec"] for r in closed]),
            "avg": fmt_duration(_avg([r["duration_sec"] for r in closed])),
            "longest_sec": max((r["duration_sec"] for r in closed), default=None),
            "shortest_sec": min((r["duration_sec"] for r in closed), default=None),
            "basis": ("Trade Duration = trades.exit_time − trades.entry_time, i.e. "
                      "full close − ENTRY FILL (core/schema.sql:141-142). ⛔ NOT from "
                      "signal or order-submit time — those are the execution delay "
                      "this screen already reports. Only a CLOSED trade has one; an "
                      "OPEN, PENDING or REJECTED row shows a dash, ⛔ never 0"),
        },
        "trend": trend,
        "trend_note": ("bucketed by SIGNAL time (IST) — when the execution began"
                       + (f"; {outside_session} execution(s) began outside "
                          "09:15–15:30 and sit in no bucket"
                          if outside_session else "")),
        "warnings": _warnings(rows),
        "warnings_note": ("⛔ No exchange-delay or broker-delay warning is emitted: "
                          "neither quantity is recorded, and a warning that can "
                          "never fire reads as an all-clear"),
        "status_distribution": [
            {"status": _FAST, "label": "Fast", "count": counts[_FAST]},
            {"status": _MODERATE, "label": "Moderate", "count": counts[_MODERATE]},
            {"status": _SLOW, "label": "Slow", "count": counts[_SLOW]},
            {"status": _UNMEASURED, "label": "Unmeasured", "count": counts[_UNMEASURED]},
        ],
        "status_guide": [
            {"status": _FAST, "label": "Fast", "rule": "0–%.0f sec total delay" % _FAST_MAX},
            {"status": _MODERATE, "label": "Moderate",
             "rule": "%.0f–%.0f sec total delay" % (_FAST_MAX, _MODERATE_MAX)},
            {"status": _SLOW, "label": "Slow", "rule": "> %.0f sec total delay" % _MODERATE_MAX},
            {"status": _UNMEASURED, "label": "Unmeasured",
             "rule": "never filled, so no total delay exists — ⛔ never counted as fast"},
        ],

        "reconcile": {
            "unreconciled_rows": unreconciled,
            "note": ("total_latency_ms is written independently of the two parts and "
                     "each is clamped to ≥0, so `total == parts` is CHECKED per row, "
                     "⛔ not assumed"),
        },
        "score_note": ("System Score = the ACHIEVED screener score; Score Threshold = "
                       "the minimum it had to reach. \"Signal Score\" is retired "
                       "system-wide (Rama, 13-Aug) — a threshold is never shown as a score"),
        "row_cap": limit,
        "row_cap_applied": bool(limit) and len(raw) >= int(limit or 0),
        "execution_count_unfiltered": len(all_rows),
        "execution_count": len(rows),
    }


# Column order mirrors the approved common standard, then the lifecycle. ⛔ No
# Scanner column — strategy IS the scanner identity on this system.
EXPORT_COLS = [
    ("Trading Date", "date"), ("Time", "time"),
    ("Strategy", "strategy"), ("Symbol", "symbol"),
    ("Trade Type", "trade_type"), ("Direction", "direction"),
    ("System Score", "system_score"), ("Score Threshold", "score_threshold"),
    ("Signal Time", "signal_time"), ("Screening Done", "screening_time"),
    ("Order Submit Time", "order_submit_time"), ("Fill Time", "fill_time"),
    ("Exit Time", "exit_time"), ("Exit Date", "exit_date"),
    ("Exit Reason", "exit_reason"),
    ("Trade State", "trade_state"),
    ("Trade Duration", "duration"), ("Trade Duration (sec)", "duration_sec"),
    ("Screening Delay (sec)", "screening_sec"),
    ("Signal to Order Delay (sec)", "signal_to_order_sec"),
    ("Fill Delay (sec)", "order_to_fill_sec"),
    ("Total Delay (sec)", "total_sec"),
    ("Status", "status"), ("Delay Bucket", "delay_bucket"),
    ("Order Status", "order_status"),
    ("Parts Reconcile", "parts_reconcile"),
    ("Remarks", "remarks"),
    ("Trade ID", "trade_id"),
]

_SUMMARY_COLS = [("Stage", "stage"), ("Measured", "measured"),
                 ("Avg (sec)", "avg_sec"), ("Worst (sec)", "worst_sec"),
                 ("n", "n"), ("Source / why not measured", None)]

_RANK_COLS = [("Avg Delay (sec)", "avg_delay_sec"),
              ("Worst Delay (sec)", "worst_delay_sec"),
              ("Fastest (sec)", "fastest_sec"), ("Executions", "executions"),
              ("Measured", "measured"), ("Slow", "slow")]


def export_sheets(payload: dict) -> list:
    """[(sheet_name, header, rows)] — every sheet from the ONE filtered payload,
    so no sheet can disagree with the table or with another sheet."""
    t = payload.get("totals") or {}
    f = payload.get("filters", {}).get("active") or {}

    summary_rows = [[r.get("stage"), "yes" if r.get("measured") else "NOT INSTRUMENTED",
                     r.get("avg_sec"), r.get("worst_sec"), r.get("n"),
                     r.get("source") or r.get("reason")]
                    for r in payload.get("delay_summary") or []]

    def _rank(rows, hdr, key):
        return ([hdr] + [h for h, _k in _RANK_COLS],
                [[r.get(key)] + [r.get(k) for _h, k in _RANK_COLS] for r in rows])

    strat_h, strat_r = _rank(payload.get("per_strategy") or [], "Strategy", "strategy")
    sym_h, sym_r = _rank(payload.get("per_symbol") or [], "Symbol", "symbol")

    overview = [
        ("Period", payload.get("period")), ("From", payload.get("from")),
        ("To", payload.get("to")),
        ("Filters applied", ", ".join(f"{k}={v}" for k, v in f.items()) or "none"),
        ("Executions (filtered)", payload.get("execution_count")),
        ("Executions (unfiltered, same period)", payload.get("execution_count_unfiltered")),
        ("", ""),
        ("Total Signals", t.get("total_signals")), ("Total Orders", t.get("total_orders")),
        ("Average Execution Delay (sec)", t.get("avg_delay_sec")),
        ("Fastest Execution (sec)", t.get("fastest_sec")),
        ("Slowest Execution (sec)", t.get("slowest_sec")),
        ("Average Fill Time (sec)", t.get("avg_fill_sec")),
        ("Fast / Moderate / Slow / Unmeasured",
         f"{t.get('fast')} / {t.get('moderate')} / {t.get('slow')} / {t.get('unmeasured')}"),
        ("", ""),
        ("INSTRUMENTATION GAP", payload.get("instrumentation_note")),
        ("Signal to Order is a composite", payload.get("composite_note")),
        ("Signals basis", t.get("signals_basis")),
        ("Throughput basis", payload.get("throughput_note")),
        ("Warnings basis", payload.get("warnings_note")),
        ("Total-vs-parts check", (payload.get("reconcile") or {}).get("note")),
        ("Rows failing that check", (payload.get("reconcile") or {}).get("unreconciled_rows")),
    ]

    return [
        ("Executions", [h for h, _k in EXPORT_COLS],
         [[r.get(k) for _h, k in EXPORT_COLS] for r in payload.get("rows") or []]),
        ("Delay Summary", [h for h, _k in _SUMMARY_COLS], summary_rows),
        ("Strategy Ranking", strat_h, strat_r),
        ("Symbol Ranking", sym_h, sym_r),
        ("Delay Distribution", ["Bucket", "Orders", "% of measured"],
         [[b.get("bucket"), b.get("orders"), b.get("pct")]
          for b in payload.get("distribution") or []]),
        ("Overview", ["Measure", "Value"], [list(x) for x in overview]),
    ]
