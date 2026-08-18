"""
GET /api/dashboard — header/status view model:
trader_alive, mode, kill_switch, service_health[6 units], counters, freshness,
and TODAY's last-N trading events. One trader-health probe is shared with the
summary.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from ..auth import login_required
from ..readers import host_reader, metrics_client
from ..services import freshness, live_activity, summary_bar

dashboard_api = Blueprint("dashboard_api", __name__)


@dashboard_api.route("/api/dashboard", methods=["GET"])
@login_required
def get_dashboard():
    cfg = current_app.config["GUI_CONFIG"]
    now = freshness.ist_now()
    today = freshness.ist_today_iso(now)
    trader_health = metrics_client.get_trader_health(cfg)

    summary = summary_bar.build_summary(cfg, today, now, trader_health=trader_health)
    # ⛔ `events` STAYS A LIST: base.html assigns it straight to the root
    # component (`this.events = d.events || []`) and that shared shell is
    # explicitly out of scope for Screen 02. The counts ride alongside it.
    feed = live_activity.build_recent_events(
        cfg, limit=live_activity.DASH_FEED_LIMIT, today=today)
    # The approved artwork draws a sparkline on SIGNALS RECEIVED (and one on
    # Today's P&L, which the page already builds from the real equity curve).
    signals_spark = live_activity.build_signals_spark(cfg, today=today, now=now)
    return jsonify({
        "today": today,
        "summary": summary,
        # ⭐ `all_units_with_change` carries a REAL per-unit
        # `last_change_at`; the old `all_units` carried state alone and the
        # screen filled the gap with its own clock. ⛔ `/api/system` keeps
        # `all_units` — that reader is byte-unchanged.
        "service_health": host_reader.all_units_with_change(cfg),
        "freshness": freshness.freshness_state(cfg, now),
        # ⭐ TODAY's real trading events, from the SAME builders Screen 18
        # uses, each row carrying its STRUCTURED category. ⛔ NOT
        # `system_events`, which holds only STARTUP / SHUTDOWN /
        # KILL_AUTO_CLEARED / CONFIG_DIFF (measured on production) and could
        # never have shown the trading events this panel promises.
        "events": feed["records"],
        "events_meta": {"today": feed["today"], "count": feed["count"],
                        "total_today": feed["total_today"]},
        "signals_spark": signals_spark,
    })
