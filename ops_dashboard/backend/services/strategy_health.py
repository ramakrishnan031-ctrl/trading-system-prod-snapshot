"""SCREEN 20 — STRATEGY HEALTH.  Operational health of every strategy.

`gui/20. Strategy_Health.png` + `.txt` are BINDING for structure.

═══════════════════════════════════════════════════════════════════════════════
⭐ WHAT IS REAL HERE

  · THE WHOLE TABLE is the existing `strategy_tower` — the today-scoped
    per-strategy assembly Screen 03 already uses — so Screens 03 and 20 cannot
    report different signal, order, trade or rejection counts for one strategy.
  · STATUS reuses `analytics_period.health_state` (Disabled → Silent → Warning
    → Quiet → Healthy), itself driven by the tested `strategy_score` badge and
    silence tier. ⛔ No second state machine was written.
  · SILENT DETECTION IS THE REAL CONFIGURED RULE. The artwork says "Silent if no
    signal for more than 2 Hours"; `gui_config.silence.yellow_max_min` is 120
    minutes, and `strategy_score.silence_tier` turns RED beyond it. The screen
    PRINTS the configured number — ⛔ it does not hard-code "2 Hours".
  · HEALTH SCORE is the artwork's own weighted composite (30/25/20/15/10) over
    five MEASURED quantities.

⛔⛔ TWO PANELS HAVE NO SOURCE AND SAY SO — HEALTH TIMELINE and RECENT HEALTH
EVENTS. Nothing in this system stores a per-strategy STATE HISTORY: a state is
derived at read time from today's counters, so there is no record of what a
strategy's state was at 11:30. ⭐ The artwork itself labels the timeline
"(Example)". Both panels keep their approved geometry and render NOT
INSTRUMENTED — ⛔ the example values in the artwork are NOT presented as data.

⛔ THE HEALTH SCORE IS NOT THE OLD 5-VALUE LOOKUP. `analytics_period._HEALTH_
SCORE` maps a STATE to 90/70/40/20/None; that is a state label, not the
composite the design specifies, and it is left where it is for the existing
`/api/strategy-health` contract while this screen computes the real thing.
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from ..readers import db_reader
from . import (analytics_period, freshness, strategy_meta, strategy_score,
               strategy_tower)

# ── the approved vocabularies (design §HEALTH STATUS / §WARNINGS) ─────────────
STATES = ("Healthy", "Quiet", "Warning", "Silent", "Disabled")

#: The artwork's HEALTH STATUS GUIDE, verbatim.
STATE_GUIDE = (
    ("Healthy", "Operating normally. Generating signals and trades."),
    ("Quiet", "Low activity. Monitor if it remains in quiet state."),
    ("Warning", "Issues detected. Requires attention."),
    ("Silent", "No signals for configured duration."),
    ("Disabled", "Strategy is disabled."),
)

#: The artwork's HEALTH SCORE BREAKDOWN donut, verbatim — the design's weights,
#: ⛔ not chosen here, and they sum to 100.
SCORE_WEIGHTS = (("activity", "Activity", 30),
                 ("signal_quality", "Signal Quality", 25),
                 ("acceptance_rate", "Acceptance Rate", 20),
                 ("trade_activity", "Trade Activity", 15),
                 ("errors", "Errors", 10))

#: The artwork's WARNINGS SUMMARY, in the approved order and with the approved
#: labels. ⚠️ "Scanner Offline" is kept EXACTLY as the artwork draws it (Rama,
#: 16-Aug) — ⛔ it is NOT renamed to Strategy Offline, and its count is DERIVED
#: from webhook_audit, ⛔ never invented.
WARNINGS = ("High Rejections", "No Signals (Silent)", "No Trades",
            "Scanner Offline", "Strategy Errors")

TRENDS = ("Improving", "Stable", "Declining")

#: The artwork's STRATEGY DRILLDOWN QUICK ACCESS tiles, in order.
DRILLDOWN = (("Overview", "/strategies"), ("Signals", "/signals"),
             ("Orders", "/orders"), ("Trades", "/trades"),
             ("Health", "/strategy-health"), ("Warnings", "/logs"))

#: Rejection share at or above which a strategy is counted under High Rejections.
#: ⚠️ The CLASSIFIER's threshold, ⛔ not a measurement — stated so the boundary is
#: visible, and every row carries its own rejection % so the call can be checked.
HIGH_REJECTION_PCT = 50.0

#: Fractional band around the recent daily average that still reads as Stable.
TREND_BAND_PCT = 10.0

#: How many days of history the sparklines and the trend baseline use.
ACTIVITY_DAYS = 7


def _gap(reason: str, short: str = "") -> dict:
    """⭐ `reason` is the FULL statement of what is missing and why — it travels
    in the payload and is what a reader is entitled to. `short` is the line the
    panel PRINTS: the approved panels are narrow, and a paragraph inside one
    stretches the whole row into exactly the dead vertical band Rama rejected on
    Screen 18. ⛔ The short line never says less than the truth — the full reason
    is on the element's tooltip."""
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


def _health_score(row: dict, max_signals: int, max_trades: int) -> dict:
    """The artwork's weighted composite over five MEASURED quantities.

      activity        stored signals vs the busiest strategy today (relative)
      signal_quality  accepted / stored — how many stored signals survived
      acceptance_rate webhook accepted / webhook received — how much of what the
                      scanner posted the intake accepted
      trade_activity  trades today vs the busiest strategy today (relative)
      errors          the NON-error share of orders (1 − rejected/created)

    ⛔ A component whose denominator is 0 contributes 0 — the convention
    `analytics_period` already documents for `opportunity_quality`, reused so
    two screens cannot score the same absence differently.
    ⛔ A DISABLED strategy scores None, ⛔ not 0: it is not failing, it is off.
    """
    if row["basic"]["enabled"] is False:
        return {"score": None, "parts": {}, "reason": "strategy is disabled"}

    sig = row["signals"]
    proc = row["processing"]
    trades_today = int(row["trading"]["open"]) + int(row["trading"]["closed"])
    created = int(proc["created"])

    parts = {
        "activity": (round(100.0 * int(sig["stored"]) / max_signals, 2)
                     if max_signals else None),
        "signal_quality": _pct(sig["accepted"], sig["stored"]),
        "acceptance_rate": _pct(sig["webhook_accepted"], sig["received"]),
        "trade_activity": (round(100.0 * trades_today / max_trades, 2)
                           if max_trades else None),
        "errors": (round(100.0 * (1.0 - int(proc["rejected"]) / created), 2)
                   if created else None),
    }
    total = 0.0
    for key, _label, weight in SCORE_WEIGHTS:
        v = parts.get(key)
        total += (float(v) * weight / 100.0) if v is not None else 0.0
    return {"score": int(round(total)), "parts": parts, "reason": None}


def _trend(today_n: int, prior_daily: dict, days: int) -> Optional[str]:
    """OPERATIONAL trend: today's signal count against this strategy's OWN recent
    daily average. ⛔ None when there is no prior history to average — the row
    then renders the em-dash rather than being called Stable."""
    if not prior_daily:
        return None
    total = sum(prior_daily.values())
    if days <= 0:
        return None
    avg = total / float(days)
    if avg <= 0:
        # ⛔ No baseline to compare against. Activity where there was none before
        # is genuinely improving; no activity either side is not a measurement.
        return "Improving" if today_n > 0 else None
    lo = avg * (1.0 - TREND_BAND_PCT / 100.0)
    hi = avg * (1.0 + TREND_BAND_PCT / 100.0)
    if today_n > hi:
        return "Improving"
    if today_n < lo:
        return "Declining"
    return "Stable"


def _days_back(today: str, n: int) -> tuple:
    d = _dt.date(*(int(x) for x in today.split("-")))
    return (d - _dt.timedelta(days=n)).isoformat(), (d - _dt.timedelta(days=1)).isoformat()


def _series(daily: dict, days: list) -> list:
    return [int(daily.get(d, 0)) for d in days]


def build_strategy_health_screen(cfg: dict, state: Optional[str] = None,
                                 trade_type: Optional[str] = None,
                                 silent_min=None, now=None) -> dict:
    """The whole wall from ONE tower build, so every panel describes the same
    strategies at the same instant.

    ⭐ `silent_min` is the SILENT DETECTION SETTINGS selection. It is a READ-TIME
    VIEW ONLY: the chosen threshold is handed to this build's own copy of the
    config so the tower re-evaluates the silence tier at it. ⛔ NOTHING IS
    WRITTEN — the deployed `gui_config.silence.yellow_max_min` is untouched, and
    the panel always states which value is the configured one so a what-if can
    never be read as the live rule.
    """
    now = now or freshness.ist_now()
    today = freshness.ist_today_iso(now)
    # ⚠️ ORDER MATTERS: the options and the CONFIGURED value are read from the
    # DEPLOYED config, before the selection is applied — reading them afterwards
    # would make whatever was selected look like the configured rule.
    sil_options = silence_options(cfg)
    cfg = _cfg_for_silence(cfg, silent_min)
    tower = strategy_tower.build_strategy_tower(cfg, today, now)
    meta = strategy_meta.strategy_meta(cfg)

    # ── history for the sparklines and the trend baseline ────────────────────
    hist_from, hist_to = _days_back(today, ACTIVITY_DAYS)
    daily = db_reader.health_daily_counts(cfg, hist_from, today)
    per_strategy_daily = db_reader.health_strategy_daily_signals(cfg, hist_from, hist_to)

    all_rows = tower["rows"]
    max_signals = max([int(r["signals"]["stored"]) for r in all_rows], default=0)
    max_trades = max([int(r["trading"]["open"]) + int(r["trading"]["closed"])
                      for r in all_rows], default=0)

    # ⭐ Activity is only EXPECTED inside the market window; outside it, silence
    # is normal. This is the same gate `strategy_score.silence_tier` applies, so
    # a quiet Sunday is not reported as a fleet of offline scanners.
    expected_now = freshness.expected_activity(cfg, "received", now)

    rows = []
    for r in all_rows:
        name = r["basic"]["name"]
        info = meta.get(name) or {}
        badge = (r.get("scorecard") or {}).get("badge")
        sil = (r.get("silence") or {}).get("color")
        st = analytics_period.health_state(r["basic"]["enabled"], badge, sil)
        hs = _health_score(r, max_signals, max_trades)
        sig = r["signals"]
        trades_today = int(r["trading"]["open"]) + int(r["trading"]["closed"])
        # ⛔⛔ REJECTIONS TODAY IS THE SIGNAL-LEVEL COUNT, ⛔ NOT the sum of the
        # failure strip. The strip mixes SIGNAL outcomes (duplicate, expired)
        # with ORDER outcomes (risk_rej, capital_rej, order_rej): summing it and
        # dividing by STORED SIGNALS puts two different denominators in one
        # ratio, which is how the donut came to read 36% accepted + 68% rejected
        # = 104%. `signals.rejected` shares STORED's own base, so the column,
        # the donut and the High-Rejections warning all measure one thing.
        rej_total = int(sig["rejected"])
        rej_pct = _pct(rej_total, sig["stored"])
        rows.append({
            "strategy": name,
            "display_name": r["basic"]["display_name"],
            # ⭐ from the SAME shared path Screen 19 uses (YAML `intent`)
            "trade_type": info.get("trade_type") or r["basic"].get("trade_type"),
            "state": st,
            "enabled": r["basic"]["enabled"],
            "last_signal": r["health"]["last_signal"],
            "last_trade": r["health"]["last_trade"],
            "signals_today": int(sig["stored"]),
            "orders_today": int(r["processing"]["created"]),
            "trades_today": trades_today,
            "rejections_today": rej_total,
            "rejections_pct": rej_pct,
            # the signal funnel's own outcomes — these DO share STORED's base
            "signal_outcomes": {"accepted": int(sig["accepted"]),
                                "rejected": int(sig["rejected"]),
                                "duplicated": int(sig["duplicated"]),
                                "expired": int(sig["expired"]),
                                "stored": int(sig["stored"])},
            # ⚠️ the order-level failure strip, kept SEPARATE and labelled as
            # such so it is never mistaken for a share of stored signals
            "failure_strip": dict(r["failures"]),
            "received": int(sig["received"]),
            "accepted": int(sig["accepted"]),
            "webhook_accepted": int(sig["webhook_accepted"]),
            "order_rejected": int(r["processing"]["rejected"]),
            "scanners": r["basic"]["scanners"],
            "health_score": hs["score"],
            "score_parts": hs["parts"],
            "score_reason": hs["reason"],
            "reasons": (r.get("scorecard") or {}).get("reasons", []),
            "silence_age_min": (r.get("silence") or {}).get("age_min"),
            "trend": _trend(int(sig["stored"]),
                            per_strategy_daily.get(name, {}), ACTIVITY_DAYS),
        })

    shown = _filter(rows, state, trade_type)
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "today": today,
        "rows": shown, "count": len(shown), "total": len(rows),
        "active": {"state": state or None, "trade_type": trade_type or None},
        "states": list(STATES),
        "state_guide": [{"state": s, "text": t} for s, t in STATE_GUIDE],
        "counts": counts,
        "kpi": _kpi(rows, counts),
        "silent_detection": _silent_detection(cfg, sil_options),
        "rejection_monitoring": _rejections(rows),
        "signal_activity": _signal_activity(cfg, rows, daily, today, now),
        "trade_activity": _trade_activity(cfg, rows, daily, today),
        "warnings": _warnings(rows, expected_now),
        "trend_analysis": _trend_analysis(rows),
        "score_weights": [{"key": k, "label": lbl, "pct": w}
                          for k, lbl, w in SCORE_WEIGHTS],
        "drilldown": [{"label": lbl, "href": href} for lbl, href in DRILLDOWN],
        "filters": {"state": list(STATES),
                    "trade_type": strategy_meta.trade_type_options(meta)},
        "expected_activity_now": expected_now,
        "poll_interval_ms": freshness.poll_interval_ms(cfg, now),
        # ⛔⛔ THE TWO PANELS WITH NO SOURCE
        "health_timeline": {"available": False, "rows": []},
        "health_events": {"available": False, "rows": []},
        "gaps": {
            # ⭐ Kept SHORT on purpose: these strings are rendered inside two
            # approved panels, and a long paragraph stretches the whole row and
            # opens exactly the dead vertical band Rama rejected on Screen 18.
            "health_timeline": _gap(
                "no per-strategy state history is stored. A state is derived at "
                "read time from today's counters, so past states were never "
                "recorded. The artwork marks this panel Example; those values "
                "are not shown as data.",
                "No state history is stored, so past states cannot be shown."),
            "health_events": _gap(
                "state transitions are not recorded. There is no strategy health "
                "event log to read, and deriving events from today's counters "
                "would invent a history that was never observed.",
                "State transitions are not recorded, so there are no events to "
                "list."),
            "scanner_offline": _gap(
                "counted only while signal activity is EXPECTED (inside the "
                "market window), the same gate the silence tier applies — "
                "outside it a scanner that has posted nothing is not offline, "
                "it is closed"),
        },
        "note": ("All times are in IST (Asia/Kolkata). Status, silence and the "
                 "health score describe TODAY; the sparklines and the trend "
                 "baseline use the preceding %d days." % ACTIVITY_DAYS),
    }


def _filter(rows: list, state: Optional[str], trade_type: Optional[str]) -> list:
    out = rows
    if state:
        want = state.strip().title()
        out = [r for r in out if r["state"] == want]
    if trade_type:
        wt = trade_type.strip().title()
        out = [r for r in out if r["trade_type"] == wt]
    return out


def _kpi(rows: list, counts: dict) -> dict:
    """The six approved cards, each with its share of the fleet. ⭐ Every
    percentage carries its base — the total strategy count."""
    total = len(rows)
    warn = sum(1 for r in rows if r["state"] == "Warning" or r["reasons"])
    err = sum(1 for r in rows if r["order_rejected"] > 0)
    out = {
        "active": counts.get("Healthy", 0),
        "quiet": counts.get("Quiet", 0),
        "silent": counts.get("Silent", 0),
        "disabled": counts.get("Disabled", 0),
        "with_warnings": warn,
        "with_errors": err,
        "total": total,
    }
    for k in ("active", "quiet", "silent", "disabled", "with_warnings", "with_errors"):
        out[k + "_pct"] = _pct(out[k], total)
    return out


def _silence_cfg(cfg: dict) -> dict:
    """The deployed silence contract, defaults filled in."""
    sil = dict(strategy_score.DEFAULT_SILENCE)
    sil.update({k: v for k, v in (cfg.get("silence") or {}).items() if v is not None})
    return sil


def _duration_label(minutes: int) -> str:
    hours = minutes / 60.0
    if hours == int(hours) and hours >= 1:
        return "%d Hour%s" % (int(hours), "" if hours == 1 else "s")
    return "%d Minutes" % minutes


def silence_options(cfg: dict) -> list:
    """⭐ THE CHOICES COME FROM THE CONFIGURATION CONTRACT, ⛔ NOT INVENTED.

    `gui_config.silence` defines exactly TWO last-signal-age boundaries, and they
    are the only two values that change what "Silent" means — `silence_tier`
    returns RED beyond `yellow_max_min` and GREEN at or under `green_max_min`.
    ⛔ No arbitrary ladder (15m / 30m / 1h / 4h …) is offered: every option here
    is a value this system is actually configured with, and each carries the
    config key it came from.
    """
    sil = _silence_cfg(cfg)
    seen, out = set(), []
    for key in ("green_max_min", "yellow_max_min"):
        minutes = int(sil[key])
        if minutes in seen:
            continue
        seen.add(minutes)
        out.append({"minutes": minutes, "label": _duration_label(minutes),
                    "source": "gui_config.silence.%s" % key,
                    "configured": key == "yellow_max_min"})
    return sorted(out, key=lambda o: o["minutes"])


def _cfg_for_silence(cfg: dict, silent_min) -> dict:
    """A COPY of the config with the selected threshold applied.

    ⛔ READ-TIME ONLY — this dict lives for one request and is never persisted;
    the deployed `gui_config.yaml` is untouched (L4: no write path). ⛔ A value
    that is not one of the configured options is IGNORED, so a hand-typed query
    string cannot invent a threshold this system was never configured with.
    """
    if silent_min is None:
        return cfg
    try:
        want = int(silent_min)
    except (TypeError, ValueError):
        return cfg
    if want not in {o["minutes"] for o in silence_options(cfg)}:
        return cfg
    out = dict(cfg)
    out["silence"] = dict(_silence_cfg(cfg))
    out["silence"]["yellow_max_min"] = want
    return out


def _silent_detection(cfg: dict, options: list) -> dict:
    """⭐ The REAL configured rule, read from config — ⛔ the artwork's "2 Hours"
    is not hard-coded. `silence.yellow_max_min` is the age beyond which
    `strategy_score.silence_tier` returns RED, which `health_state` maps to
    Silent.

    ⚠️ `cfg` here is ALREADY the per-request copy, so `minutes` is what the
    screen is CURRENTLY evaluating at. `configured_minutes` is what the deployed
    config says — the two are reported separately so a selection can never be
    mistaken for the live rule.
    """
    sil = _silence_cfg(cfg)
    minutes = int(sil["yellow_max_min"])
    configured = next((o["minutes"] for o in options if o["configured"]), minutes)
    return {"minutes": minutes, "label": _duration_label(minutes),
            "status": "Silent",
            "source": "gui_config.silence.yellow_max_min",
            "options": options,
            "selected": minutes,
            "configured_minutes": configured,
            "configured_label": _duration_label(configured),
            "is_configured": minutes == configured,
            "green_max_min": int(sil["green_max_min"])}


def _rejections(rows: list) -> dict:
    """REJECTION MONITORING (TODAY) — the donut and its figures, over the SAME
    rows the table shows and over ONE base: signals STORED today.

    ⭐ Accepted and Rejected are two outcomes of the stored-signal funnel, so
    their shares are comparable and can be drawn as one donut. ⛔ A stored signal
    can also be a DUPLICATE or EXPIRED; that remainder is reported rather than
    folded into either side, so the three shares total exactly 100%.
    """
    total = sum(r["signals_today"] for r in rows)
    accepted = sum(r["accepted"] for r in rows)
    rejected = sum(r["rejections_today"] for r in rows)
    other = max(0, total - accepted - rejected)
    return {
        "total_signals": total, "accepted": accepted, "rejected": rejected,
        "other": other,
        "accepted_pct": _pct(accepted, total), "rejected_pct": _pct(rejected, total),
        "other_pct": _pct(other, total),
        "available": total > 0,
        "base": "signals stored today, across the strategies shown",
    }


def _signal_activity(cfg: dict, rows: list, daily: dict, today: str, now) -> dict:
    """SIGNAL ACTIVITY — Last Hour / Today / This Week, each with its own real
    series. ⭐ Last Hour is counted from the per-minute map, so it is a genuine
    trailing hour, ⛔ not 'today so far'."""
    pulse = db_reader.activity_pulse(cfg, today)
    minutes = pulse.get("signals") or {}
    base = now.replace(second=0, microsecond=0)
    keys = [(base - _dt.timedelta(minutes=i)).strftime("%H:%M") for i in range(59, -1, -1)]
    last_hour = sum(minutes.get(k, 0) for k in keys)
    days = _day_keys(today, ACTIVITY_DAYS)
    week = sum(int((daily.get("signals") or {}).get(d, 0)) for d in days)
    return {
        "last_hour": last_hour, "today": sum(r["signals_today"] for r in rows),
        "week": week, "week_days": ACTIVITY_DAYS,
        "spark_hour": [minutes.get(k, 0) for k in keys[-30:]],
        # ⭐ the artwork draws a sparkline under EACH of the three figures, so
        # each one gets its OWN real series — ⛔ never the same series reused
        # under a different heading.
        "spark_today": _hourly(minutes),
        "spark_days": _series(daily.get("signals") or {}, days),
        "days": days, "hours": HOURS,
    }


def _trade_activity(cfg: dict, rows: list, daily: dict, today: str) -> dict:
    days = _day_keys(today, ACTIVITY_DAYS)
    last_trades = [r["last_trade"] for r in rows if r.get("last_trade")]
    minutes = (db_reader.activity_pulse(cfg, today).get("trades")) or {}
    return {
        "today": sum(r["trades_today"] for r in rows),
        "week": sum(int((daily.get("trades") or {}).get(d, 0)) for d in days),
        "week_days": ACTIVITY_DAYS,
        "last_trade": max(last_trades) if last_trades else None,
        "today_date": today,
        "spark_today": _hourly(minutes),
        "spark_days": _series(daily.get("trades") or {}, days),
        "days": days, "hours": HOURS,
    }


#: Hour buckets for the "today" sparklines — the whole day, so nothing that
#: happened outside the market window is silently dropped from the picture.
HOURS = tuple("%02d" % h for h in range(24))


def _hourly(minutes: dict) -> list:
    """Roll a per-minute HH:MM map up into 24 hour buckets. ⛔ Nothing is
    interpolated: an hour with no event is a real zero."""
    out = [0] * 24
    for k, n in (minutes or {}).items():
        try:
            out[int(str(k)[:2])] += int(n)
        except (ValueError, IndexError):
            continue
    return out


def _day_keys(today: str, n: int) -> list:
    d = _dt.date(*(int(x) for x in today.split("-")))
    return [(d - _dt.timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _warnings(rows: list, expected_now: bool) -> dict:
    """WARNINGS SUMMARY — the five approved rows, each a COUNT of strategies.

    ⚠️ "Scanner Offline" keeps the artwork's label EXACTLY (Rama, 16-Aug) and is
    DERIVED, ⛔ never invented: a strategy that HAS a mapped scanner and whose
    scanners posted NOTHING to `webhook_audit` today. ⛔ It is counted only while
    activity is expected — outside the market window a scanner that has posted
    nothing is closed, not offline — the same gate the silence tier applies.
    """
    counted = {
        "High Rejections": sum(
            1 for r in rows
            if r["rejections_pct"] is not None and r["rejections_pct"] >= HIGH_REJECTION_PCT),
        "No Signals (Silent)": sum(1 for r in rows if r["state"] == "Silent"),
        "No Trades": sum(1 for r in rows
                         if r["enabled"] is not False and r["trades_today"] == 0),
        "Scanner Offline": (sum(1 for r in rows
                                if r["enabled"] is not False and r["scanners"]
                                and r["received"] == 0) if expected_now else 0),
        "Strategy Errors": sum(1 for r in rows if r["order_rejected"] > 0),
    }
    return {
        "rows": [{"label": w, "count": counted[w]} for w in WARNINGS],
        "scanner_offline_measured": expected_now,
        "high_rejection_pct": HIGH_REJECTION_PCT,
    }


def _trend_analysis(rows: list) -> dict:
    """TREND ANALYSIS — the three approved states with their counts and the
    artwork's own descriptions."""
    text = {"Improving": "Strategy performance is improving",
            "Stable": "Strategy performance is stable",
            "Declining": "Strategy performance is declining"}
    return {
        "rows": [{"trend": t, "count": sum(1 for r in rows if r["trend"] == t),
                  "text": text[t]} for t in TRENDS],
        "unavailable": sum(1 for r in rows if r["trend"] is None),
        "base": "today's signals with each strategy's own %d-day daily average"
                % ACTIVITY_DAYS,
    }


# ── XLSX ─────────────────────────────────────────────────────────────────────
#: ⭐ The approved table columns IN THE TABLE'S OWN ORDER, Trade Type second —
#: the Screen-14 rule that a spreadsheet opens in the order the screen shows.
#: ⛔ No Scanner column.
EXPORT_HEADER = ("Strategy", "Trade Type", "Status", "Last Signal", "Last Trade",
                 "Signals Today", "Orders Today", "Trades Today",
                 "Rejections Today", "Health Score", "Trend")

NA = "NOT INSTRUMENTED"


def _hhmm(ts) -> str:
    """⚠️ TWO MARKERS, TWO MEANINGS, and they are not interchangeable:
    `NOT INSTRUMENTED` = this system cannot measure the value at all;
    `—` = it CAN, and the measured answer is that there was none today.
    A strategy that has not signalled yet is the second, ⛔ never the first."""
    s = str(ts or "").replace("T", " ")
    return s[11:19] if len(s) >= 19 else (s or "—")


def export_rows(payload: dict) -> list:
    """Exactly the rows the table is showing — the SAME filtered list."""
    out = [list(EXPORT_HEADER)]
    for r in payload.get("rows") or []:
        out.append([
            r.get("display_name") or r.get("strategy"),
            r.get("trade_type") or NA, r.get("state"),
            _hhmm(r.get("last_signal")), _hhmm(r.get("last_trade")),
            r.get("signals_today"), r.get("orders_today"), r.get("trades_today"),
            r.get("rejections_today"),
            r.get("health_score") if r.get("health_score") is not None else NA,
            r.get("trend") or NA,
        ])
    return out
